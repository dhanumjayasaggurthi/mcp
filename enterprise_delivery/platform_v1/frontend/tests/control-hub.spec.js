import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.edpAuth = { getAccessToken: async () => "ui-test-only" };
  });
});
test("all screens load from real API; no fabricated chrome", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  for (const route of [
    "overview",
    "sources",
    "datasets",
    "collections",
    "policies",
    "guardrails",
    "clients",
    "agents",
    "indexes",
    "playground",
    "jobs",
    "audit",
    "monitoring",
  ]) {
    await page.goto("/" + route);
    await expect(page.locator("main h1")).toBeVisible();
    await expect(page.getByRole("status", { name: "Loading" })).toHaveCount(0);
    await expect(page.getByText("All Systems Operational")).toHaveCount(0);
  }
  expect(errors).toEqual([]);
});
test("create consumer, review, save, reload and inspect revision", async ({
  page,
}) => {
  await page.goto("/clients/new");
  await page.getByLabel("id *", { exact: true }).fill("browser-client");
  await page.getByLabel("display name *").fill("Browser consumer");
  await page.getByLabel("owner *").fill("Platform team");
  await page.getByRole("button", { name: "Review changes" }).click();
  await expect(page.getByRole("dialog")).toContainText("browser-client");
  await page.getByRole("button", { name: "Save configuration" }).click();
  await expect(page).toHaveURL(/clients\/browser-client/);
  await page.reload();
  await expect(page.locator("main h1")).toHaveText("Browser consumer");
  await page.getByRole("button", { name: "history", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "1", exact: true }),
  ).toBeVisible();
});
test("onboarding discovers a real SQL object and saves a draft", async ({
  page,
}) => {
  await page.goto("/datasets/new");
  await page.getByRole("button", { name: "source", exact: true }).click();
  await page.getByRole("button", { name: "facts", exact: true }).click();
  await page.getByLabel("Dataset ID", { exact: true }).fill("browser.facts");
  await page.getByLabel("Display name", { exact: true }).fill("Browser facts");
  await page.getByRole("button", { name: "Inspect & draft contract" }).click();
  await expect(
    page.getByRole("button", { name: "fields", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Review changes" }).click();
  await page.getByRole("button", { name: "Save configuration" }).click();
  await expect(page).toHaveURL(/datasets\/browser.facts/);
  await page.getByRole("button", { name: "fields", exact: true }).click();
  await expect(page.getByLabel("id selectable")).toBeChecked();
});
test("query executes policy-filtered SQL with cursor pagination", async ({
  page,
}) => {
  await page.goto("/playground");
  await page.getByLabel("Operation", { exact: true }).selectOption("query");
  await page.getByLabel("Dataset ID", { exact: true }).fill("facts");
  await page.getByLabel("Page size").fill("1");
  await page.getByRole("button", { name: "Run request" }).click();
  await expect(
    page.getByRole("columnheader", { name: "amount", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "10", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "20", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "999", exact: true }),
  ).toHaveCount(0);
});
test("overview is accessible and mobile layout fits", async ({ page }) => {
  await page.goto("/overview");
  await expect(page.locator("main h1")).toBeVisible();
  const scan = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(scan.violations).toEqual([]);
  await page.screenshot({
    path: "test-results/overview-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("main h1")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Toggle navigation" }).click();
  await expect(
    page.getByRole("link", { name: "Data products", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/overview-mobile.png",
    fullPage: true,
  });
});
