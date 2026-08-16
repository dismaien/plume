import { expect, test } from "@playwright/test";

import { installApiMocks, setLocalAuthState } from "./fixtures/api-mocks";

test("a rejected voice guess never becomes an identified speaker", async ({ page }) => {
    await setLocalAuthState(page);
    await installApiMocks(page);

    let dismissed = false;
    let dismissCalls = 0;
    let savedMappings: Array<{ original_speaker: string; custom_name: string }> = [];
    const pendingMama = {
        id: 501,
        original_speaker: "SPEAKER_00",
        custom_name: "Mama",
        contact_id: 8,
        confidence_score: 0.48,
        match_source: "auto",
        match_tier: "suggest",
        review_status: "pending",
    };
    const confirmedLeona = {
        id: 502,
        original_speaker: "SPEAKER_01",
        custom_name: "Leona",
        contact_id: 9,
        confidence_score: 0.58,
        match_source: "auto",
        match_tier: "auto",
        review_status: "",
    };

    await page.route("**/api/v1/transcription/1/speakers/dismiss", async (route) => {
        dismissCalls += 1;
        dismissed = true;
        await route.fulfill({ json: { status: "dismissed" } });
    });
    await page.route("**/api/v1/transcription/1/speakers/suggestions", async (route) => {
        await route.fulfill({ json: dismissed ? [] : [pendingMama] });
    });
    await page.route("**/api/v1/transcription/1/speakers", async (route) => {
        if (route.request().method() === "POST") {
            const body = route.request().postDataJSON() as { mappings: typeof savedMappings };
            savedMappings = body.mappings;
            await route.fulfill({
                json: {
                    mappings: [],
                    contact_bootstrap: { started_count: 0, created_count: 0, skipped_existing_count: 0 },
                },
            });
            return;
        }
        await route.fulfill({
            json: [
                { ...pendingMama, review_status: dismissed ? "dismissed" : "pending" },
                confirmedLeona,
            ],
        });
    });

    await page.goto("/audio/1");
    await page.getByRole("button", { name: "Rename Speakers" }).click();

    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("Is this")).toBeVisible();
    await expect(dialog.getByText("Mama", { exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: "No" }).click();

    await expect.poll(() => dismissCalls).toBe(1);
    await expect(dialog.getByText("Mama", { exact: true })).toHaveCount(0);
    await expect(dialog.getByLabel("Speaker A")).toHaveValue("");
    await expect(dialog.getByText("IDENTIFIED (1)")).toBeVisible();
    await expect(dialog.getByLabel("Speaker B")).toHaveValue("Leona");

    await dialog.getByRole("button", { name: "Save" }).click();
    await expect.poll(() => savedMappings).toContainEqual({
        original_speaker: "SPEAKER_00",
        custom_name: "Mama",
    });
});
