import { test, expect } from "@playwright/test";

let runId;
let promos;
const failures = [];
const origin = `http://127.0.0.1:${process.env.PF_TEST_PORT || 8877}`;

async function post(request, endpoint, body) {
  const response = await request.post(`${endpoint}?id=${encodeURIComponent(runId)}`, {
    data: body, headers: { Origin: origin },
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function open(page, extra = "") {
  await page.goto(`/#run=${encodeURIComponent(runId)}${extra}`);
  await expect(page.getByRole("navigation", { name: "Ranked promos" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeEnabled();
}

async function feedback(request) {
  return (await request.get(`/api/feedback?id=${encodeURIComponent(runId)}`)).json();
}

test.beforeEach(async ({ page, request }) => {
  const runs = await (await request.get("/api/runs")).json();
  runId = runs.find((run) => run.model === "mock/browser-primary").id;
  const run = await (await request.get(`/api/run?id=${encodeURIComponent(runId)}`)).json();
  promos = run.variants.main.promos;
  for (const promo of promos) {
    await post(request, "/api/nudge", { promo_id: promo.id, reset: true });
    await post(request, "/api/review", { promo_id: promo.id, verdict: "clear", note: "" });
  }
  failures.length = 0;
  page.on("pageerror", (error) => failures.push(error.message));
});

test.afterEach(() => expect(failures).toEqual([]));

test("run selection by link, variants and keyboard navigation", async ({ page }) => {
  await open(page);
  const items = page.getByRole("navigation", { name: "Ranked promos" }).getByRole("button");
  await expect(items).toHaveCount(promos.length);
  await page.locator("h1").click();
  await page.keyboard.press("j");
  await expect(items.nth(1)).toHaveAttribute("aria-current", "true");
  await page.keyboard.press("k");
  await expect(items.first()).toHaveAttribute("aria-current", "true");
  await page.getByRole("combobox", { name: "Ranking", exact: true }).selectOption("ablation_episode_end_prior");
  await expect(page.getByText("You are viewing an ablation", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toHaveCount(0);
  const runs = await (await page.request.get("/api/runs")).json();
  const secondary = runs.find((r) => r.model === "mock/browser-secondary").id;
  await page.evaluate((id) => { location.hash = `#run=${encodeURIComponent(id)}`; }, secondary);
  await expect.poll(() => hashOf(page)).toContain(encodeURIComponent(secondary));
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeVisible();
});

test("approve, would post, reject and clear persist across reload", async ({ page, request }) => {
  await open(page);
  for (const [label, value] of [["Approve", "approve"], ["Would post", "would_post"], ["Reject", "reject"]]) {
    const button = page.getByRole("button", { name: label, exact: true });
    await button.click();
    await expect(button).toHaveAttribute("aria-pressed", "true");
    await page.reload();
    await expect(button).toHaveAttribute("aria-pressed", "true");
    expect((await feedback(request)).entries[promos[0].id].verdict).toBe(value);
  }
  await page.getByRole("button", { name: "Reject", exact: true }).click();
  await expect(page.getByRole("button", { name: "Reject", exact: true })).toHaveAttribute("aria-pressed", "false");
});

test("typing a note then immediately choosing a verdict saves both", async ({ page, request }) => {
  await open(page);
  // Delay the blur-save to reproduce an actual network round trip while clicking Reject.
  await page.route("**/api/review?*", async (route) => {
    if (route.request().postDataJSON().note !== undefined) {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    await route.continue();
  });
  await page.getByRole("textbox").fill("The ending gives away the resolution.");
  await page.getByRole("button", { name: "Reject", exact: true }).click();
  await expect.poll(async () => (await feedback(request)).entries[promos[0].id]?.verdict).toBe("reject");
  expect((await feedback(request)).entries[promos[0].id].note).toBe("The ending gives away the resolution.");
});

test("notes are not lost when switching promos immediately", async ({ page, request }) => {
  await open(page);
  await page.getByRole("textbox").fill("Check this opening again.");
  await page.getByRole("navigation", { name: "Ranked promos" }).getByRole("button").nth(1).click();
  await expect.poll(async () => (await feedback(request)).entries[promos[0].id]?.note).toBe("Check this opening again.");
  await expect(page.getByRole("textbox")).toHaveValue("");
});

test("invalid cut shows suggestions; suggested cut and reset persist", async ({ page, request }) => {
  await open(page);
  await page.getByRole("button", { name: "end of segment 1, 0.5 seconds later", exact: true }).click();
  const suggestion = page.getByRole("button", { name: /^Use / }).first();
  await expect(suggestion).toBeVisible();
  await suggestion.click();
  await expect(page.getByRole("button", { name: "Reset cut", exact: true })).toBeVisible();
  expect((await feedback(request)).entries[promos[0].id].nudged).toBe(true);
  await page.reload();
  await expect(page.getByRole("button", { name: "Reset cut", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Reset cut", exact: true }).click();
  await expect(page.getByRole("button", { name: "Reset cut", exact: true })).toHaveCount(0);
});

test("timestamp clipboard, CSV, ranking and quality report", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await open(page);
  await page.getByRole("button", { name: "Copy timestamps", exact: true }).click();
  await expect(page.getByRole("button", { name: "Copied", exact: true })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("Ep ");
  for (const label of ["Download CSV", "Download ranking"]) {
    const download = page.waitForEvent("download");
    await page.getByRole("link", { name: label, exact: true }).click();
    expect((await download).suggestedFilename()).toMatch(/promos.csv|ranking.md/);
  }
  await page.getByRole("tab", { name: "Quality report", exact: true }).click();
  await expect(page.locator(".md")).toContainText("Where the tool picks badly");
});

test("direct links preserve the selected variant and promo after reload", async ({ page, request }) => {
  const run = await (await request.get(`/api/run?id=${encodeURIComponent(runId)}`)).json();
  const variant = "ablation_episode_end_prior";
  const promo = run.variants[variant].promos.at(-1);
  await page.goto(`/#run=${encodeURIComponent(runId)}&v=${variant}&p=${promo.id}`);
  const selected = page.getByRole("navigation", { name: "Ranked promos" }).locator('[aria-current="true"]');
  await expect(selected).toContainText(`#${promo.rank} `);
  await page.reload();
  await expect(selected).toContainText(`#${promo.rank} `);
});

test("mobile layout and no external network dependency", async ({ page }) => {
  const external = [];
  page.on("request", (request) => {
    if (!request.url().startsWith(origin)) external.push(request.url());
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(external).toEqual([]);
});

test("empty installation explains how to generate results", async ({ page }) => {
  await page.route("**/api/runs", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  await expect(page.getByText("No results yet.", { exact: true })).toBeVisible();
});

test("failed run list can be retried", async ({ page }) => {
  let attempts = 0;
  await page.route("**/api/runs", async (route) => {
    if (attempts++ === 0) await route.fulfill({ status: 503, json: { error: "Temporarily unavailable" } });
    else await route.continue();
  });
  await page.goto("/");
  await expect(page.getByText("Couldn't load runs.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "Ranked promos" })).toBeVisible();
});

test("editing can recover after a refused write", async ({ page, request }) => {
  await open(page);
  let first = true;
  await page.route("**/api/review?*", async (route) => {
    if (first) {
      first = false;
      await route.fulfill({ status: 409, json: { error: "Temporary conflict" } });
    } else await route.continue();
  });
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByText("Temporary conflict", { exact: true })).toBeVisible();
  expect((await feedback(request)).entries[promos[0].id]).toBeUndefined();
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toHaveAttribute("aria-pressed", "true");
});

// --- the URL is the single source of truth (hashchange) ---------------------------------

const hashOf = (page) => page.evaluate(() => location.hash);
const selectedItem = (page) =>
  page.getByRole("navigation", { name: "Ranked promos" }).locator('[aria-current="true"]');

test("editing the URL hash switches the promo, and back/forward follow it", async ({ page }) => {
  await open(page);
  const [first, , third] = promos;
  await expect(selectedItem(page)).toContainText(`#${first.rank} `);
  await page.evaluate(([r, id]) => { location.hash = `#run=${encodeURIComponent(r)}&p=${id}`; }, [runId, third.id]);
  await expect(selectedItem(page)).toContainText(`#${third.rank} `);
  expect(await hashOf(page)).toContain(`p=${third.id}`); // URL and screen agree
  await page.goBack();
  await expect(selectedItem(page)).toContainText(`#${first.rank} `);
  await page.goForward();
  await expect(selectedItem(page)).toContainText(`#${third.rank} `);
});

test("hash changes switch the tab and the ranking variant", async ({ page }) => {
  await open(page);
  const run = await (await page.request.get(`/api/run?id=${encodeURIComponent(runId)}`)).json();
  await page.evaluate((r) => { location.hash = `#run=${encodeURIComponent(r)}&tab=report`; }, runId);
  await expect(page.locator(".md")).toContainText("Where the tool picks badly");
  await page.goBack();
  await expect(page.locator(".md")).toHaveCount(0);
  const variant = "ablation_episode_end_prior";
  const last = run.variants[variant].promos.at(-1);
  await page.evaluate(([r, v, p]) => { location.hash = `#run=${encodeURIComponent(r)}&v=${v}&p=${p}`; }, [runId, variant, last.id]);
  await expect(page.getByLabel("Ranking")).toHaveValue(variant);
  await expect(selectedItem(page)).toContainText(`#${last.rank} `);
});

test("a pasted link to another run loads that run", async ({ page, request }) => {
  await open(page);
  const runs = await (await request.get("/api/runs")).json();
  const other = runs.find((r) => r.model === "mock/browser-secondary");
  await page.evaluate((id) => { location.hash = `#run=${encodeURIComponent(id)}`; }, other.id);
  await expect.poll(() => hashOf(page)).toContain(encodeURIComponent(other.id));
  await page.goBack();
  await expect.poll(() => hashOf(page)).toContain(encodeURIComponent(runId));
});

test("a hash naming an unknown run is corrected instead of leaving URL and screen apart", async ({ page }) => {
  await open(page);
  await page.evaluate(() => { location.hash = "#run=does-not-exist%2Fnope"; });
  await expect.poll(() => hashOf(page)).toContain(encodeURIComponent(runId));
});

test("phone layout keeps the review stats in one compact row above the shortlist", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page);
  const bar = page.locator(".reviewbar");
  await expect(bar).toBeVisible();
  const tops = await bar.locator("> *").evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().top)));
  expect(tops).toHaveLength(5);
  expect(new Set(tops).size).toBe(1); // one row
  const barBox = await bar.boundingBox();
  expect(barBox.height).toBeLessThanOrEqual(76);
  const list = await page.getByRole("navigation", { name: "Ranked promos" }).boundingBox();
  const scrollY = await page.evaluate(() => scrollY);
  expect(list.y + scrollY).toBeLessThan(900); // was ~1000px down with four tall tiles
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.setViewportSize({ width: 1280, height: 800 }); // desktop keeps the full tiles
  expect((await bar.boundingBox()).height).toBeGreaterThan(90);
});

// --- upload and analyse your own screenplay ---------------------------------------------

function ts(t) { return `${String(Math.floor(t / 60)).padStart(2, "0")}:${(t % 60).toFixed(2).padStart(5, "0")}`; }
function screenplay(n, dur = 110) {
  const L = [`EPISODE NUMBER: Episode ${n}`, `EPISODE LOGLINE: Invented episode ${n}.`, "CONTINUITY NOTES: Continues.", "", "--- SCRIPT START ---", ""];
  let t = 0, i = 0;
  for (const sc of [1, 2]) {
    L.push(`SCENE 0${sc}`, `Scene heading: INT. HALL ${sc} – DAY`, `Scene start: ${ts((sc - 1) * dur / 2)}`, `Scene end: ${ts(sc * dur / 2)}`, "", "Actions and dialogue:", "");
    while (t < sc * dur / 2 - 4) {
      const who = ["ANA (Furious)", "BEN (Cold)", "CAL (Pleading)"][i % 3];
      L.push("**CAMERA:**", `Start: (${ts(t)}) | End: (${ts(t + 3.5)})`, "Close-up on the speaker.", "");
      L.push(`**${who}:**`, `Start: (${ts(t + 0.2)}) | End: (${ts(t + 2.7)})`, `"Line ${i} of episode ${n} about the vault key${i % 4 === 0 ? " You lied to me!" : ""}"`, "");
      if (i % 5 === 4) L.push("**ACTION:**", `Start: (${ts(t + 2.8)}) | End: (${ts(t + 3.4)})`, "He slaps her across the face.", "");
      t += 3.5; i += 1;
    }
  }
  L.push("**CAL (Shocked):**", `Start: (${ts(dur - 6)}) | End: (${ts(dur - 3.5)})`, '"Who are you?"', "",
    "**CAMERA:**", `Start: (${ts(dur - 3.4)}) | End: (${ts(dur)})`, "Hold, then an abrupt cut to black.", "", "--- SCRIPT END ---");
  return L.join("\n");
}
const upload = (names = [1, 2, 3]) => names.map((n) => ({ name: `episode-00${n}.txt`, mimeType: "text/plain", buffer: Buffer.from(screenplay(n)) }));

test("a visitor can upload episode files and get a ranked, reviewable result", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Analyze your screenplay" }).click();
  await expect(page.getByRole("heading", { name: "Analyze your own screenplay" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Find promos" })).toBeDisabled(); // nothing chosen yet
  await page.getByLabel("Episode files").setInputFiles(upload());
  await page.getByLabel("Name").fill("My invented pilot");
  await page.getByRole("button", { name: "Find promos" }).click();
  await expect(page.getByRole("heading", { name: "My invented pilot" })).toBeVisible({ timeout: 60000 });
  await expect(page.getByText("Rules-only ranking")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Ranked promos" }).locator('[aria-current="true"]')).toBeVisible();
  expect(await hashOf(page)).toContain("run=upload-");
  await page.getByRole("button", { name: "Approve", exact: true }).click(); // the editor works on it
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.reload(); // the unlisted link survives a reload
  await expect(page.getByRole("heading", { name: "My invented pilot" })).toBeVisible();
});

test("there is no API key field; the AI option explains itself when the server has no model", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Analyze your screenplay" }).click();
  await expect(page.getByLabel("API key")).toHaveCount(0);
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await expect(page.getByLabel("Provider")).toHaveCount(0);
  const model = page.getByRole("radio", { name: /With an AI model/ });
  await expect(model).toBeDisabled(); // the test server has no model configured
  await expect(page.getByRole("note").filter({ hasText: "No model is configured on this server" })).toBeVisible();
  await expect(page.getByRole("radio", { name: /Rules only/ })).toBeChecked();
});

test("the analyse feature is hidden when the server does not offer it", async ({ page }) => {
  await page.route("**/api/capabilities", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ analyze: false }) }));
  await open(page);
  await expect(page.getByRole("button", { name: "Analyze your screenplay" })).toHaveCount(0);
});

test("an older server without the endpoint gives a clear message, not a silent failure", async ({ page }) => {
  await open(page);
  await page.route("**/api/analyze", (route) => route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "not found" }) }));
  await page.getByRole("button", { name: "Analyze your screenplay" }).click();
  await page.getByLabel("Episode files").setInputFiles(upload([1]));
  await page.getByRole("button", { name: "Find promos" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "does not support uploads" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Find promos" })).toBeEnabled();
});

test("a file that is not a screenplay shows a readable error and the form stays usable", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Analyze your screenplay" }).click();
  await page.getByLabel("Episode files").setInputFiles([{ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("just prose, no timestamps") }]);
  await page.getByRole("button", { name: "Find promos" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "no parseable episodes" })).toBeVisible({ timeout: 30000 });
  await expect(page.getByRole("button", { name: "Find promos" })).toBeEnabled();
});

test("there is no Run dropdown; the page shows the run it was opened with", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("combobox", { name: "Run", exact: true })).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Ranking", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Ranked promos" })).toBeVisible();
});
