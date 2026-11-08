import { test, expect } from "@playwright/test";

test("search, empty results, review filters and candidate navigation", async ({ page, request }) => {
  const runs = await (await request.get("/api/runs")).json();
  const id = runs.find((run) => run.model === "mock/browser-primary").id;
  const run = await (await request.get(`/api/run?id=${encodeURIComponent(id)}`)).json();
  for (const promo of run.variants.main.promos) {
    await request.post(`/api/review?id=${encodeURIComponent(id)}`, {
      headers: { Origin: new URL(test.info().project.use.baseURL).origin },
      data: { promo_id: promo.id, verdict: "clear", note: "" },
    });
  }
  await page.goto(`/#run=${encodeURIComponent(id)}`);
  const items = page.getByRole("navigation", { name: "Ranked promos" }).getByRole("button");
  const search = page.getByRole("searchbox", { name: "Search promos" });
  await expect(items).toHaveCount(run.variants.main.promos.length);
  await search.fill("zzzz-no-dialogue-match");
  await expect(items).toHaveCount(0);
  await expect(page.getByText("No matching promos.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(items).toHaveCount(run.variants.main.promos.length);
  await expect(page.getByRole("button", { name: "Previous candidate", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Next candidate", exact: true }).click();
  await expect(items.nth(1)).toHaveAttribute("aria-current", "true");
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("combobox", { name: "Filter by review status" }).selectOption("approve");
  await expect(items).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Next candidate", exact: true })).toBeDisabled();
  await page.getByRole("combobox", { name: "Filter by review status" }).selectOption("unreviewed");
  await expect(items).toHaveCount(run.variants.main.promos.length - 1);
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toHaveAttribute("aria-pressed", "false");
  await page.getByRole("combobox", { name: "Filter by review status" }).selectOption("all");
  await search.fill("episode 3");
  await expect.poll(() => items.count()).toBeGreaterThan(0);
  await page.locator("h1").click();
  const matchCount = await items.count();
  for (let i = 0; i < matchCount; i += 1) await page.keyboard.press("k");
  await expect(items.first()).toHaveAttribute("aria-current", "true");
  await page.keyboard.press("j");
  await expect(items.nth(Math.min(1, matchCount - 1))).toHaveAttribute("aria-current", "true");
});
