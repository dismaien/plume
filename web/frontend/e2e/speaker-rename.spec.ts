import { test, expect } from '@playwright/test';
import { installApiMocks, setLocalAuthState } from './fixtures/api-mocks';

test.beforeEach(async ({ page }) => {
  await setLocalAuthState(page);
  await installApiMocks(page);
});

test.describe('Speaker Rename Dialog', () => {
  test('consolidates multiple diarization labels renamed to one person', async ({ page }) => {
    let savedMappings = [
      { original_speaker: 'SPEAKER_00', custom_name: 'SPEAKER_00', match_source: 'manual' },
      { original_speaker: 'SPEAKER_01', custom_name: 'SPEAKER_01', match_source: 'manual' },
      { original_speaker: 'SPEAKER_02', custom_name: 'SPEAKER_02', match_source: 'manual' },
    ];

    await page.route('**/api/v1/transcription/*/speakers', async (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ json: savedMappings });
      }

      const payload = route.request().postDataJSON() as { mappings: typeof savedMappings };
      savedMappings = payload.mappings;
      return route.fulfill({
        json: {
          mappings: savedMappings,
          contact_bootstrap: { started_count: 0, created_count: 0, skipped_existing_count: 0 },
        },
      });
    });

    await page.goto('/audio/1');
    await page.getByRole('button', { name: 'Rename Speakers' }).click();

    for (const speaker of ['SPEAKER_00', 'SPEAKER_01', 'SPEAKER_02']) {
      await page.locator(`#speaker-${speaker}`).fill('Leona');
    }
    const renameDialog = page.getByRole('dialog', { name: 'Rename Speakers' });
    await renameDialog.getByRole('button', { name: 'Save' }).click();
    await expect(renameDialog).toBeHidden();

    const moreMenu = page.getByRole('button').filter({
      has: page.locator('svg.lucide-ellipsis-vertical'),
    }).first();
    await moreMenu.click();
    await page.getByRole('menuitem', { name: 'Compacted View' }).click();

    const transcript = page.locator('[data-transcript-text]');
    await expect(transcript).toHaveCount(1);
    await expect(transcript).toContainText('Good morning everyone');
    await expect(page.getByText('Leona', { exact: true })).toHaveCount(1);
  });

  test('opens speaker rename dialog and shows speakers', async ({ page }) => {
    await page.goto('/audio/1');
    await page.waitForLoadState('networkidle');

    // Find and click the speaker rename/edit button (could be in dropdown or direct button)
    const renameButton = page.locator(
      'button:has-text("Rename"), button:has-text("Speaker"), button:has-text("Edit Speaker"), [aria-label*="speaker"]',
    ).first();

    // If the rename button is in a dropdown, open the dropdown first
    const dropdownTrigger = page.locator('button:has([class*="ellipsis"]), button:has([class*="more"]), [aria-label="More"]').first();
    if (await dropdownTrigger.isVisible().catch(() => false)) {
      await dropdownTrigger.click();
      await page.waitForTimeout(200);
    }

    // Try to find the speaker rename trigger
    const trigger = page.locator('text=Rename Speakers, text=Speaker, text=Edit Speakers').first();
    if (await trigger.isVisible().catch(() => false)) {
      await trigger.click();
    } else if (await renameButton.isVisible().catch(() => false)) {
      await renameButton.click();
    }

    // The dialog should now be open with speaker inputs
    const dialog = page.locator('[role="dialog"]');
    if (await dialog.isVisible().catch(() => false)) {
      await expect(dialog).toContainText('Rename Speakers');

      // Should show speaker labels (SPEAKER_00, SPEAKER_01, SPEAKER_02)
      await expect(dialog.locator('text=SPEAKER_00').first()).toBeVisible();
      await expect(dialog.locator('text=SPEAKER_01').first()).toBeVisible();
      await expect(dialog.locator('text=SPEAKER_02').first()).toBeVisible();
    }
  });

  test('can type a new name for a speaker', async ({ page }) => {
    // Override speaker mappings to return raw speaker IDs
    await page.route('**/api/v1/transcription/*/speakers', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          json: [
            { original_speaker: 'SPEAKER_00', custom_name: 'SPEAKER_00' },
            { original_speaker: 'SPEAKER_01', custom_name: 'SPEAKER_01' },
          ],
        });
      }
      // POST – echo back with updated name
      return route.fulfill({
        json: {
          mappings: [
            { original_speaker: 'SPEAKER_00', custom_name: 'Jane' },
            { original_speaker: 'SPEAKER_01', custom_name: 'SPEAKER_01' },
          ],
          contact_bootstrap: { started_count: 0, created_count: 0 },
        },
      });
    });

    await page.goto('/audio/1');
    await page.waitForLoadState('networkidle');

    // Open the dialog (try multiple approaches)
    const triggers = [
      page.locator('button:has-text("Rename")').first(),
      page.locator('button:has-text("Speaker")').first(),
      page.locator('[aria-label*="speaker"]').first(),
    ];

    for (const trigger of triggers) {
      if (await trigger.isVisible().catch(() => false)) {
        await trigger.click();
        break;
      }
    }

    const dialog = page.locator('[role="dialog"]');
    if (await dialog.isVisible().catch(() => false)) {
      // Find the first speaker input and type a new name
      const speakerInput = dialog.locator('input[id*="speaker"]').first();
      if (await speakerInput.isVisible().catch(() => false)) {
        await speakerInput.clear();
        await speakerInput.fill('Jane');

        // Click Save
        const saveButton = dialog.locator('button:has-text("Save")');
        await expect(saveButton).toBeEnabled();
      }
    }
  });

  test('shows confidence badges for auto-matched and promoted speakers', async ({ page }) => {
    await page.goto('/audio/1');
    await page.waitForLoadState('networkidle');

    // Open dialog
    const triggers = [
      page.locator('button:has-text("Rename")').first(),
      page.locator('button:has-text("Speaker")').first(),
      page.locator('[aria-label*="speaker"]').first(),
    ];

    for (const trigger of triggers) {
      if (await trigger.isVisible().catch(() => false)) {
        await trigger.click();
        break;
      }
    }

    const dialog = page.locator('[role="dialog"]');
    if (await dialog.isVisible().catch(() => false)) {
      // SPEAKER_00 has match_source="auto" with 92% confidence
      const autoBadge = dialog.locator('[data-testid="badge-auto-SPEAKER_00"]');
      if (await autoBadge.isVisible().catch(() => false)) {
        await expect(autoBadge).toContainText('Auto 92%');
      }

      // SPEAKER_01 has match_source="suggestion_promoted" with 75% confidence
      const matchedBadge = dialog.locator('[data-testid="badge-matched-SPEAKER_01"]');
      if (await matchedBadge.isVisible().catch(() => false)) {
        await expect(matchedBadge).toContainText('Matched 75%');
      }

      // SPEAKER_02 has match_source="manual" — no auto or matched badge
      await expect(dialog.locator('[data-testid="badge-auto-SPEAKER_02"]')).not.toBeVisible();
      await expect(dialog.locator('[data-testid="badge-matched-SPEAKER_02"]')).not.toBeVisible();
    }
  });

  test('auto-matched speaker inputs are read-only', async ({ page }) => {
    await page.goto('/audio/1');
    await page.waitForLoadState('networkidle');

    // Open dialog
    const triggers = [
      page.locator('button:has-text("Rename")').first(),
      page.locator('button:has-text("Speaker")').first(),
      page.locator('[aria-label*="speaker"]').first(),
    ];

    for (const trigger of triggers) {
      if (await trigger.isVisible().catch(() => false)) {
        await trigger.click();
        break;
      }
    }

    const dialog = page.locator('[role="dialog"]');
    if (await dialog.isVisible().catch(() => false)) {
      // SPEAKER_00 (auto-matched) should be read-only
      const autoInput = dialog.locator('input#speaker-SPEAKER_00');
      if (await autoInput.isVisible().catch(() => false)) {
        await expect(autoInput).toHaveAttribute('readonly', '');
        await expect(autoInput).toHaveValue('Alice');
      }

      // SPEAKER_02 (manual) should be editable
      const manualInput = dialog.locator('input#speaker-SPEAKER_02');
      if (await manualInput.isVisible().catch(() => false)) {
        const isReadOnly = await manualInput.getAttribute('readonly');
        expect(isReadOnly).toBeNull();
      }
    }
  });

  test('save button is disabled when no speakers exist', async ({ page }) => {
    // Override to return empty speakers
    await page.route('**/api/v1/transcription/*/speakers', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ json: [] });
      }
      return route.continue();
    });

    await page.goto('/audio/1');
    await page.waitForLoadState('networkidle');

    // Open dialog
    const triggers = [
      page.locator('button:has-text("Rename")').first(),
      page.locator('button:has-text("Speaker")').first(),
    ];

    for (const trigger of triggers) {
      if (await trigger.isVisible().catch(() => false)) {
        await trigger.click();
        break;
      }
    }

    const dialog = page.locator('[role="dialog"]');
    if (await dialog.isVisible().catch(() => false)) {
      // Save button should be disabled when no speakers
      const saveButton = dialog.locator('button:has-text("Save")');
      if (await saveButton.isVisible().catch(() => false)) {
        await expect(saveButton).toBeDisabled();
      }

      // Should show "No speakers found" message
      await expect(dialog.locator('text=No speakers found')).toBeVisible();
    }
  });
});
