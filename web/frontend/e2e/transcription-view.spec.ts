import { test, expect } from '@playwright/test';
import { installApiMocks, setLocalAuthState, TRANSCRIPT_SEGMENTS, SPEAKER_MAPPINGS } from './fixtures/api-mocks';

test.beforeEach(async ({ page }) => {
  await setLocalAuthState(page);
  await installApiMocks(page);
});

test.describe('View Completed Transcription', () => {
  test('displays transcript segments with text', async ({ page }) => {
    await page.goto('/audio/1');
    await page.waitForLoadState('networkidle');

    // Transcript text should appear on the page (check first few segments)
    for (const segment of TRANSCRIPT_SEGMENTS.slice(0, 3)) {
      const snippet = segment.text.substring(0, 20);
      await expect(page.locator(`text=${snippet}`).first()).toBeVisible({ timeout: 10000 });
    }
  });

  test('shows speaker labels from mappings', async ({ page }) => {
    await page.goto('/audio/1');
    await page.waitForLoadState('networkidle');

    // Speaker names from the mappings should appear
    for (const mapping of SPEAKER_MAPPINGS) {
      await expect(page.locator(`text=${mapping.custom_name}`).first()).toBeVisible({ timeout: 10000 });
    }
  });

  test('displays the transcription title', async ({ page }) => {
    await page.goto('/audio/1');
    await page.waitForLoadState('networkidle');

    await expect(page.locator('text=Team Standup March 10').first()).toBeVisible();
  });

  test('audio player area is present', async ({ page }) => {
    await page.goto('/audio/1');
    await page.waitForLoadState('networkidle');

    // The page should have some kind of audio-related element (player, waveform, canvas, or error message)
    const audioArea = page
      .locator('audio, video, canvas, [class*="player"], [class*="Player"], [class*="waveform"], [class*="Waveform"], [class*="audio"]')
      .or(page.locator('text=Unable to load audio'));
    const count = await audioArea.count();
    expect(count).toBeGreaterThan(0);
  });

  test('space toggles playback without hijacking title editing', async ({ page }) => {
    await page.addInitScript(() => {
      let paused = true;
      Object.defineProperty(HTMLMediaElement.prototype, 'paused', {
        configurable: true,
        get: () => paused,
      });
      HTMLMediaElement.prototype.play = async function () {
        paused = false;
        this.dispatchEvent(new Event('play'));
      };
      HTMLMediaElement.prototype.pause = function () {
        paused = true;
        this.dispatchEvent(new Event('pause'));
      };
    });

    await page.goto('/audio/1');
    await page.waitForLoadState('networkidle');

    await expect(page.getByRole('button', { name: 'Play recording' })).toBeVisible();
    await page.keyboard.press('Space');
    await expect(page.getByRole('button', { name: 'Pause recording' })).toBeVisible();
    await page.keyboard.press('Space');
    await expect(page.getByRole('button', { name: 'Play recording' })).toBeVisible();

    await page.locator('h1.recording-title').click();
    const titleInput = page.locator('input.recording-title');
    await expect(titleInput).toBeVisible();
    await titleInput.press('Space');

    await expect(page.getByRole('button', { name: 'Play recording' })).toBeVisible();
    await expect(titleInput).toHaveValue(/ $/);
  });
});
