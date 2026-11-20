import { test, expect } from '@playwright/test';
import { AUDIO_FILES, installApiMocks, setLocalAuthState, TRANSCRIPT_SEGMENTS, SPEAKER_MAPPINGS } from './fixtures/api-mocks';

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
    await expect(page.getByTestId('audio-waveform')).toBeVisible();
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

  test('keeps the title caret where the user is typing', async ({ page }) => {
    await page.goto('/audio/1');
    await page.waitForLoadState('networkidle');

    await page.locator('h1.recording-title').click();
    const titleInput = page.locator('input.recording-title');
    await expect(titleInput).toHaveValue(AUDIO_FILES[0].title);

    const insertion = 'Edited ';
    const insertionPoint = AUDIO_FILES[0].title.indexOf('Team');
    await titleInput.evaluate((input, position) => {
      const element = input as HTMLInputElement;
      element.focus();
      element.setSelectionRange(position, position);
    }, insertionPoint);
    await page.keyboard.type(insertion);

    const expected =
      AUDIO_FILES[0].title.slice(0, insertionPoint)
      + insertion
      + AUDIO_FILES[0].title.slice(insertionPoint);
    await expect(titleInput).toHaveValue(expected);
    await expect.poll(() => titleInput.evaluate(input => (input as HTMLInputElement).selectionStart))
      .toBe(insertionPoint + insertion.length);
  });

  test('recovers from a playback error when another recording is opened', async ({ page }) => {
    await page.goto('/audio/1');
    await page.waitForLoadState('networkidle');

    await page.locator('audio').dispatchEvent('error');
    await expect(page.getByText('Unable to load audio stream.')).toBeVisible();

    await page.getByText('Product Review', { exact: true }).click();
    await expect(page).toHaveURL(/\/audio\/2$/);
    await expect(page.getByRole('button', { name: 'Play recording' })).toBeVisible();
    await expect(page.getByText('Unable to load audio stream.')).toHaveCount(0);
  });

  test('labels a sub-second recording as too short instead of showing a broken player', async ({ page }) => {
    await page.route('**/api/v1/transcription/1', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...AUDIO_FILES[0],
          has_audio: true,
          duration: 64,
        }),
      });
    });

    await page.goto('/audio/1');
    await page.waitForLoadState('networkidle');

    await expect(page.getByText('Recording is too short to play.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Play recording' })).toHaveCount(0);
  });
});
