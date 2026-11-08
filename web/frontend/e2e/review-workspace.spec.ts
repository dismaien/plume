import { test, expect } from '@playwright/test';
import { installApiMocks, setLocalAuthState } from './fixtures/api-mocks';

test.beforeEach(async ({ page }) => {
  await setLocalAuthState(page);
  await installApiMocks(page);
});

test('Review is reachable after integration and generates only on request', async ({ page }) => {
  let requests = 0;
  await page.route('**/api/v1/review/generate', async route => {
    requests++;
    expect(route.request().method()).toBe('POST');
    expect(route.request().postDataJSON().max_recordings).toBe(18);
    await route.fulfill({ json: {
      generated_at: '2026-11-07T20:00:00Z', since: '2026-11-01T00:00:00Z', until: '2026-11-07T20:00:00Z',
      recording_count: 1, total_matching: 1, model: 'local-test-model',
      sections: [{ id: 'open_loops', title: 'Open loops', items: [{ text: 'An evidence-backed follow-up.', evidence: [{ source_id: 'S1', start_seconds: 12 }] }] }],
      sources: [{ id: 'S1', job_id: '1', title: 'Source conversation', recorded_at: '2026-11-07T12:00:00Z', duration_ms: 60000, speakers: ['Alice'], has_unclear_speakers: true }],
    } });
  });
  await page.goto('/audio/1');
  await page.getByTitle('Review themes and open loops across conversations').click();
  await expect(page).toHaveURL(/\/review$/);
  await expect(page.getByRole('heading', { name: 'Review recent conversations' })).toBeVisible();
  expect(requests).toBe(0);
  await page.getByRole('button', { name: 'Generate review' }).click();
  await expect(page.getByText('An evidence-backed follow-up.')).toBeVisible();
  expect(requests).toBe(1);
  await page.getByTitle('Open Source conversation at 0:12').click();
  await expect(page).toHaveURL(/\/audio\/1\?t=12$/);
});

test('Review reports local-model failure without claiming a generated result', async ({ page }) => {
  await page.route('**/api/v1/review/generate', route => route.fulfill({ status: 503, json: { error: 'Local model unavailable' } }));
  await page.goto('/review');
  await page.getByRole('button', { name: 'Generate review' }).click();
  await expect(page.getByRole('alert')).toHaveText('Local model unavailable');
  await expect(page.getByText(/recordings? reviewed/)).toHaveCount(0);
});
