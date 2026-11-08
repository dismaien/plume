import { test, expect, type Page } from '@playwright/test';
import { AUDIO_FILES, installApiMocks, setLocalAuthState } from './fixtures/api-mocks';

test.beforeEach(async ({ page }) => {
  await setLocalAuthState(page);
  await page.addInitScript(() => localStorage.setItem('plume-transcript-recordings-sidebar-v2', 'true'));
  await installApiMocks(page);
  await page.route('**/api/v1/transcription/*/kill', route => route.fulfill({ json: { success: true } }));
  await page.route('**/api/v1/transcription/1', route => route.request().method() === 'DELETE' ? route.fulfill({ json: { success: true } }) : route.fallback());
  await page.route('**/api/v1/transcription/*/title/auto?force=true', route => route.fulfill({ json: { title: 'New title' } }));
  await page.route('**/api/v1/obsidian/sync/*', route => route.fulfill({ json: { success: true } }));
});
async function openActions(page: Page, title: string) {
  const trigger = page.getByRole('complementary', { name: 'Recordings list' }).getByRole('button', { name: `Actions for ${title}`, exact: true });
  await expect(trigger).toBeEnabled();
  await trigger.focus();
  await trigger.press('ArrowDown');
}

test('completed and active rows expose matching actions without navigating', async ({ page }) => {
  await page.goto('/audio/1');
  await openActions(page, 'Product Review');
  for (const name of ['Transcribe', 'Move to Folder', 'Publish to Obsidian', 'Regenerate Title', 'Delete Recording']) {
    await expect(page.getByRole('menuitem', { name, exact: true })).toBeVisible();
  }
  await expect(page).toHaveURL(/\/audio\/1$/);
  await page.keyboard.press('Escape');
  await openActions(page, 'Interview with Candidate');
  await expect(page.getByRole('menuitem', { name: 'Stop Transcription', exact: true })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: 'Delete Recording', exact: true })).toHaveCount(0);
  await expect(page.getByRole('menuitem', { name: 'Transcribe', exact: true })).toHaveCount(0);
  await page.getByRole('menuitem', { name: 'Stop Transcription', exact: true }).click();
  const dialog = page.getByRole('alertdialog');
  await expect(dialog).toContainText('Interview with Candidate');
  const stopped = page.waitForRequest(r => r.url().endsWith('/transcription/3/kill') && r.method() === 'POST');
  await dialog.getByRole('button', { name: 'Stop Transcription', exact: true }).click();
  await stopped;
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(/\/audio\/1$/);
});

test('delete cancel, error/retry and deleting another recording preserve current transcript', async ({ page }) => {
  let deleted = false;
  let requests = 0;
  await page.route('**/api/v1/transcription/list?*', route => route.fulfill({ json: {
    jobs: AUDIO_FILES.filter(f => !deleted || f.id !== '2'), pagination: { page: 1, pages: 1, total: deleted ? 3 : 4, limit: 30 },
  } }));
  await page.route('**/api/v1/transcription/2', async route => {
    if (route.request().method() !== 'DELETE') return route.fallback();
    requests++;
    if (requests === 1) return route.fulfill({ status: 500, json: { error: 'Could not delete recording' } });
    deleted = true;
    await route.fulfill({ json: { success: true } });
  });
  await page.goto('/audio/1');
  await openActions(page, 'Product Review');
  await page.getByRole('menuitem', { name: 'Delete Recording' }).click();
  const dialog = page.getByRole('alertdialog');
  await expect(dialog).toContainText('Product Review');
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  expect(requests).toBe(0);
  await openActions(page, 'Product Review');
  await page.getByRole('menuitem', { name: 'Delete Recording' }).click();
  await dialog.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(dialog.getByRole('alert')).toHaveText('Could not delete recording');
  await expect(page).toHaveURL(/\/audio\/1$/);
  await dialog.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('button', { name: 'Actions for Product Review', exact: true })).toHaveCount(0);
  await expect(page).toHaveURL(/\/audio\/1$/);
  expect(requests).toBe(2);
});

test('deleting the open recording returns to the list', async ({ page }) => {
  await page.goto('/audio/1');
  await openActions(page, AUDIO_FILES[0].title);
  await page.getByRole('menuitem', { name: 'Delete Recording' }).click();
  const request = page.waitForRequest(r => r.url().endsWith('/transcription/1') && r.method() === 'DELETE');
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete', exact: true }).click();
  await request;
  await expect(page).toHaveURL(/\/$/);
});

test('move, title and publishing act on the selected sidebar recording', async ({ page }) => {
  await page.goto('/audio/1');
  await openActions(page, 'Product Review');
  await page.getByRole('menuitem', { name: 'Move to Folder' }).hover();
  const move = page.waitForRequest(r => r.url().endsWith('/transcription/2/folder') && r.method() === 'PUT');
  await page.getByRole('menuitem', { name: 'Clients', exact: true }).click();
  expect((await move).postDataJSON()).toEqual({ folder: 'Clients' });
  await openActions(page, 'Product Review');
  const title = page.waitForRequest(r => r.url().endsWith('/transcription/2/title/auto?force=true') && r.method() === 'POST');
  await page.getByRole('menuitem', { name: 'Regenerate Title' }).click();
  await title;
  await openActions(page, 'Product Review');
  const publish = page.waitForRequest(r => r.url().endsWith('/obsidian/sync/2') && r.method() === 'POST');
  await page.getByRole('menuitem', { name: 'Publish to Obsidian' }).click();
  await publish;
  await expect(page).toHaveURL(/\/audio\/1$/);
});


test('transcribe uses the existing profile dialog and the selected recording', async ({ page }) => {
  const profile = { id: 'local', name: 'Local quality', is_default: true, parameters: { model: 'large-v3', is_multi_track_enabled: false } };
  await page.route('**/api/v1/profiles', route => route.fulfill({ json: [profile] }));
  await page.route('**/api/v1/user/default-profile', route => route.fulfill({ json: profile }));
  await page.route('**/api/v1/transcription/2/start', route => route.fulfill({ json: { success: true } }));
  await page.goto('/audio/1');
  await openActions(page, 'Product Review');
  await page.getByRole('menuitem', { name: 'Transcribe', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('button', { name: 'Choose profile' })).toHaveText('Local quality (Default)');
  const started = page.waitForRequest(r => r.url().endsWith('/transcription/2/start') && r.method() === 'POST');
  await dialog.getByRole('button', { name: 'Start Transcription', exact: true }).click();
  expect((await started).postDataJSON()).toEqual(profile.parameters);
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(/\/audio\/1$/);
});
