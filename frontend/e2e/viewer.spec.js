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

test("run selection, variants and keyboard navigation", async ({ page }) => {
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
  const secondary = await page.getByRole("combobox", { name: "Run", exact: true }).locator("option").filter({ hasText: "browser-secondary" }).getAttribute("value");
  await page.getByRole("combobox", { name: "Run", exact: true }).selectOption(secondary);
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
