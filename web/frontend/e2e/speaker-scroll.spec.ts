import { test, expect } from '@playwright/test';
import { installApiMocks, setLocalAuthState } from './fixtures/api-mocks';

test.beforeEach(async ({ page }) => {
  await setLocalAuthState(page);
  await installApiMocks(page);
  await page.route('**/api/v1/transcription/*/speakers', route => route.fulfill({ json:
    Array.from({ length: 12 }, (_, i) => ({ original_speaker: `SPEAKER_${String(i).padStart(2, '0')}`, custom_name: '', match_source: 'manual' }))
  }));
  await page.route(/\/api\/v1\/contacts(\?.*)?$/, route => route.fulfill({ json: {
    contacts: Array.from({ length: 8 }, (_, i) => ({ id: i + 1, name: `Contact ${i + 1}` })), vault_id: 1,
  } }));
});

test('wheel scroll reaches the last contact and selecting it preserves input behavior', async ({ page }) => {
  await page.goto('/audio/1');
  await page.getByRole('button', { name: 'Rename Speakers' }).click();
  const input = page.locator('#speaker-SPEAKER_00');
  await input.click();
  const list = page.locator('[data-slot="popover-content"] > div').first();
  await expect(list.getByRole('option')).toHaveCount(8);
  await list.hover();
  await page.mouse.wheel(0, 450);
  await expect.poll(() => list.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
  await list.getByRole('option', { name: 'Contact 8', exact: true }).click();
  await expect(input).toHaveValue('Contact 8');
  await input.fill('Contact 2');
  await input.press('Enter');
  await expect(input).toHaveValue('Contact 2');
});

test('short viewport can scroll to the dialog save controls', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 500 });
  await page.goto('/audio/1');
  await page.getByRole('button', { name: 'Rename Speakers' }).click();
  const dialog = page.getByRole('dialog', { name: 'Rename Speakers' });
  await dialog.getByRole('heading', { name: 'Rename Speakers' }).click();
  await dialog.hover({ position: { x: 15, y: 100 } });
  await page.mouse.wheel(0, 900);
  await expect(dialog.getByRole('button', { name: 'Save', exact: true })).toBeInViewport();
});

test('playback updates keep the contact list and unsaved speaker name stable', async ({ page }) => {
  let mappingReads = 0;
  await page.route('**/api/v1/transcription/*/speakers', route => {
    mappingReads++;
    return route.fulfill({ json: Array.from({ length: 3 }, (_, i) => ({ original_speaker: `SPEAKER_${String(i).padStart(2, '0')}`, custom_name: '', match_source: 'manual' })) });
  });
  await page.goto('/audio/1');
  await page.getByRole('button', { name: 'Rename Speakers' }).click();
  const input = page.locator('#speaker-SPEAKER_02');
  await input.fill('Contact');
  const list = page.locator('[data-slot="popover-content"]');
  await expect(list).toBeVisible();
  const readsBeforePlayback = mappingReads;
  for (const time of [1, 2, 3, 4, 5]) {
    await page.locator('audio').first().evaluate((node: HTMLAudioElement, seconds) => {
      Object.defineProperty(node, 'currentTime', { value: seconds, configurable: true });
      node.dispatchEvent(new Event('timeupdate', { bubbles: true }));
    }, time);
    // Allow the timeupdate render and any unintended fetch/reset to settle.
    await page.waitForTimeout(150);
    await expect(input).toHaveValue('Contact');
    await expect(input).toBeFocused();
    await expect(list).toBeVisible();
  }
  expect(mappingReads).toBe(readsBeforePlayback);
  await list.getByRole('option', { name: 'Contact 2', exact: true }).click();
  await expect(input).toHaveValue('Contact 2');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Rename Speakers' }).click();
  await expect(page.locator('#speaker-SPEAKER_02')).toHaveValue('');
  expect(mappingReads).toBeGreaterThan(readsBeforePlayback);
});

test('speaker C remains C when it speaks before B and retains its saved name', async ({ page }) => {
  const segments = [
    { start: 0, end: 1, speaker: 'speaker_0', text: 'First speaker words.' },
    { start: 1, end: 2, speaker: 'speaker_2', text: 'Second speaker words.' },
    { start: 2, end: 3, speaker: 'speaker_1', text: 'Third speaker words.' },
  ];
  let mappings = ['speaker_0', 'speaker_1', 'speaker_2'].map(original_speaker => ({ original_speaker, custom_name: '', match_source: 'manual' }));
  await page.route('**/api/v1/transcription/*/transcript', route => route.fulfill({ json: { transcript: { segments, text: segments.map(s => s.text).join(' ') } } }));
  await page.route('**/api/v1/transcription/*/speakers', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: mappings });
    mappings = route.request().postDataJSON().mappings;
    return route.fulfill({ json: { mappings, contact_bootstrap: { started_count: 0, created_count: 0, skipped_existing_count: 0 } } });
  });
  await page.goto('/audio/1');
  await page.getByRole('button', { name: 'Rename Speakers' }).click();
  const dialog = page.getByRole('dialog', { name: 'Rename Speakers' });
  // Label association checks identity, not just whether the letter exists.
  await expect(dialog.getByLabel('Speaker C', { exact: true })).toHaveAttribute('id', 'speaker-speaker_2');
  await dialog.getByLabel('Speaker C', { exact: true }).fill('Leona');
  await dialog.getByRole('heading').click();
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(dialog).toBeHidden();
  expect(mappings.find(m => m.original_speaker === 'speaker_2')?.custom_name).toBe('Leona');
  expect(mappings.find(m => m.original_speaker === 'speaker_1')?.custom_name).not.toBe('Leona');
  await expect(page.getByText('Leona', { exact: true }).first()).toBeVisible();
  segments.reverse(); // Moving turns must never rename the speaker identities.
  await page.reload();
  await expect(page.getByText('Leona', { exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Rename Speakers' }).click();
  await expect(dialog.getByLabel('Speaker C', { exact: true })).toHaveValue('Leona');
});

test('sparse speaker IDs keep the same letters in transcript and rename dialog', async ({ page }) => {
  const segments = [
    { start: 0, end: 1, speaker: 'speaker_13', text: 'A later numbered speaker.' },
    { start: 1, end: 2, speaker: 'speaker_2', text: 'Speaker C speaks second.' },
  ];
  await page.route('**/api/v1/transcription/*/transcript', route => route.fulfill({ json: { transcript: { segments, text: segments.map(s => s.text).join(' ') } } }));
  await page.route('**/api/v1/transcription/*/speakers', route => route.fulfill({ json: [] }));
  await page.goto('/audio/1');
  await expect(page.getByText('Speaker N', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Speaker C', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Speaker A', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Rename Speakers' }).click();
  const dialog = page.getByRole('dialog', { name: 'Rename Speakers' });
  await expect(dialog.getByLabel('Speaker N', { exact: true })).toHaveAttribute('id', 'speaker-speaker_13');
  await expect(dialog.getByLabel('Speaker C', { exact: true })).toHaveAttribute('id', 'speaker-speaker_2');
});
