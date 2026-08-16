import { expect, test } from "@playwright/test";

import { AUDIO_FILES, installApiMocks, setLocalAuthState } from "./fixtures/api-mocks";

test.beforeEach(async ({ page }) => {
    await setLocalAuthState(page);
    await installApiMocks(page);
});

test("opens a recordings list and switches transcripts without returning to the dashboard", async ({ page }) => {
    await page.goto("/audio/1");

    const toggle = page.getByRole("button", { name: "Show recordings list" });
    await expect(toggle).toBeVisible();
    await toggle.click();

    const sidebar = page.getByRole("complementary", { name: "Recordings list" });
    await expect(sidebar).toBeVisible();
    await expect(sidebar.getByText(AUDIO_FILES[0].title)).toBeVisible();
    await expect(sidebar.getByRole("button", { name: /Team Standup March 10/ })).toHaveAttribute("aria-current", "page");

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(sidebar).toBeVisible();

    await sidebar.getByRole("button", { name: /Product Review/ }).click();
    await expect(page).toHaveURL(/\/audio\/2$/);
    await expect(sidebar.getByRole("button", { name: /Product Review/ })).toHaveAttribute("aria-current", "page");
});

test("remembers the recordings list preference", async ({ page }) => {
    await page.goto("/audio/1");
    await page.getByRole("button", { name: "Show recordings list" }).click();
    await page.reload();

    await expect(page.getByRole("complementary", { name: "Recordings list" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Hide recordings list" }).first()).toBeVisible();
});
