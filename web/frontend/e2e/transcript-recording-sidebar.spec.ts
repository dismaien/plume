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

test("keeps the sidebar scroll position when selecting a visible recording", async ({ page }) => {
    await page.addInitScript(() => {
        const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
        (window as typeof window & { __scrollIntoViewCalls?: number }).__scrollIntoViewCalls = 0;
        HTMLElement.prototype.scrollIntoView = function (...args) {
            const state = window as typeof window & { __scrollIntoViewCalls?: number };
            state.__scrollIntoViewCalls = (state.__scrollIntoViewCalls ?? 0) + 1;
            return originalScrollIntoView.apply(this, args);
        };
    });

    await page.goto("/audio/1");
    const sidebar = page.getByRole("complementary", { name: "Recordings list" });
    await expect(sidebar.locator('button[aria-current="page"]')).toContainText("Team Standup March 10");

    const callsBeforeSelection = await page.evaluate(
        () => (window as typeof window & { __scrollIntoViewCalls?: number }).__scrollIntoViewCalls ?? 0,
    );
    await sidebar.getByRole("button", { name: /Product Review/ }).first().click();
    await expect(sidebar.locator('button[aria-current="page"]')).toContainText("Product Review");

    const callsAfterSelection = await page.evaluate(
        () => (window as typeof window & { __scrollIntoViewCalls?: number }).__scrollIntoViewCalls ?? 0,
    );
    expect(callsAfterSelection).toBe(callsBeforeSelection);
});

test("remembers when the recordings list is closed", async ({ page }) => {
    await page.goto("/audio/1");
    await page.getByRole("button", { name: "Hide recordings list" }).first().click();
    await page.reload();

    await expect(page.getByRole("complementary", { name: "Recordings list" })).toBeHidden();
    await expect(page.getByRole("button", { name: "Show recordings list" })).toBeVisible();
});
