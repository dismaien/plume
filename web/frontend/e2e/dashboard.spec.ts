import { test, expect } from '@playwright/test';
import { installApiMocks, setLocalAuthState, AUDIO_FILES } from './fixtures/api-mocks';

test.beforeEach(async ({ page }) => {
  await setLocalAuthState(page);
  await installApiMocks(page);
});

test.describe('Dashboard – Audio File List', () => {
  test('displays all audio files on load', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Should show all 4 audio files by title
    for (const file of AUDIO_FILES) {
      await expect(page.locator(`text=${file.title}`).first()).toBeVisible({ timeout: 10000 });
    }
  });

  test('shows status indicators for each file', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Completed file should be visible
    await expect(page.locator('text=Team Standup March 10').first()).toBeVisible({ timeout: 10000 });

    // Processing file should show processing state
    await expect(page.locator('text=Interview with Candidate').first()).toBeVisible({ timeout: 10000 });
  });

  test('shows the recording-local time beside the date', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await expect(page.getByText('Mar 10, 2026 · 10:15').first()).toBeVisible();
  });

  test('clicking an audio file navigates to detail view', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Wait for the file list to render
    await expect(page.locator('text=Team Standup March 10').first()).toBeVisible({ timeout: 10000 });

    await page.locator('text=Team Standup March 10').first().click();
    await expect(page).toHaveURL(/\/audio\/1/);
  });
});

for (const width of [390, 820, 1440]) {
  test(`shows the full recording title at ${width}px`, async ({ page }) => {
    const title = '260101 12:48 Planning a family gathering and the long walk through Yosemite Valley @'
      + 'UnbrokenPlaceName'.repeat(8) + ' {03:40}';
    const record = { ...AUDIO_FILES[0], title };
    await page.route('**/api/v1/transcription/list*', route => route.fulfill({
      json: { jobs: [record], pagination: { page: 1, pages: 1, total: 1, limit: 50 } },
    }));
    await page.route('**/api/v1/transcription/1', route => route.fulfill({ json: record }));
    await page.setViewportSize({ width, height: 950 });

    const heading = page.getByRole('heading', { name: title, exact: true });
    await page.goto('/');
    await expect(heading).toBeVisible();
    const wrapsInsideBox = (element: Element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      const box = element.getBoundingClientRect();
      const lines = Array.from(range.getClientRects());
      return lines.length > 1 && element.scrollHeight <= element.clientHeight + 1
        && lines.every(line =>
        line.left >= box.left - 1 && line.right <= box.right + 1);
    };
    expect(await heading.evaluate(wrapsInsideBox)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);

    await page.goto('/audio/1');
    await expect(heading).toBeVisible();
    expect(await heading.evaluate(wrapsInsideBox)).toBe(true);
    const sidebar = page.getByRole('complementary', { name: 'Recordings list' });
    const showList = page.getByRole('button', { name: 'Show recordings list' });
    if (await showList.isVisible()) await showList.click();
    const sidebarTitle = sidebar.getByText(title, { exact: true });
    await expect(sidebarTitle).toBeVisible();
    expect(await sidebarTitle.evaluate(wrapsInsideBox)).toBe(true);
  });
}
