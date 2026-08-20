import { expect, test } from "@playwright/test";

import { AUDIO_FILES, installApiMocks, setLocalAuthState } from "./fixtures/api-mocks";

test.beforeEach(async ({ page }) => {
    await setLocalAuthState(page);
    await installApiMocks(page);
});

test("opens the recordings list by default and switches transcripts without returning to the dashboard", async ({ page }) => {
    await page.goto("/audio/1");

    const sidebar = page.getByRole("complementary", { name: "Recordings list" });
    await expect(sidebar).toBeVisible();
    await expect(page.getByRole("button", { name: "Hide recordings list" }).first()).toBeVisible();
    await expect(sidebar.getByText(AUDIO_FILES[0].title)).toBeVisible();
    await expect(sidebar).toContainText('Mar 10 · 10:15');
    await expect(sidebar.getByRole("button", { name: /Team Standup March 10/ })).toHaveAttribute("aria-current", "page");

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(sidebar).toBeVisible();

    await sidebar.getByRole("button", { name: /Product Review/ }).click();
    await expect(page).toHaveURL(/\/audio\/2$/);
    await expect(sidebar.getByRole("button", { name: /Product Review/ })).toHaveAttribute("aria-current", "page");
});

test("remembers when the recordings list is closed", async ({ page }) => {
    await page.goto("/audio/1");
    await page.getByRole("button", { name: "Hide recordings list" }).first().click();
    await page.reload();

    await expect(page.getByRole("complementary", { name: "Recordings list" })).toBeHidden();
    await expect(page.getByRole("button", { name: "Show recordings list" })).toBeVisible();
});
