import { expect, test } from '@playwright/test';

import { installApiMocks, setLocalAuthState } from './fixtures/api-mocks';

test.beforeEach(async ({ page }) => {
  await setLocalAuthState(page);
  await installApiMocks(page);
});

function longAlternatingTranscript() {
  const segments = Array.from({ length: 80 }, (_, index) => ({
    start: index,
    end: index + 0.8,
    text: index === 69 ? 'Zürich umsteigt.' : `Passage${String(index + 1).padStart(2, '0')}`,
    speaker: index % 2 === 0 ? 'SPEAKER_00' : 'SPEAKER_01',
  }));

  return {
    text: segments.map(segment => segment.text).join(' '),
    segments,
    word_segments: segments.flatMap(segment => segment.text.split(' ').map((word, wordIndex) => ({
      start: segment.start + wordIndex * 0.2,
      end: segment.start + wordIndex * 0.2 + 0.15,
      word,
      score: 1,
      speaker: segment.speaker,
    }))),
  };
}

test('reveals the first and singleton transcript search result', async ({ page }) => {
  const transcript = longAlternatingTranscript();
  await page.addInitScript(() => {
    const original = HTMLElement.prototype.scrollIntoView;
    const state = window as typeof window & { __searchScrolls?: string[] };
    state.__searchScrolls = [];
    HTMLElement.prototype.scrollIntoView = function (...args) {
      state.__searchScrolls?.push(this.textContent ?? '');
      return original.apply(this, args);
    };
  });
  await page.route('**/api/v1/transcription/1/transcript', route => route.fulfill({
    json: { available: true, transcript },
  }));

  await page.goto('/audio/1');
  await page.getByRole('button', { name: 'Search' }).click();
  await page.getByPlaceholder('Search transcript...').fill('Zürich');
  await expect(page.getByText('1 of 1')).toBeVisible();
  await expect.poll(() => page.evaluate(() => {
    const state = window as typeof window & { __searchScrolls?: string[] };
    return state.__searchScrolls?.filter(text => text.includes('Zürich')).length ?? 0;
  })).toBe(1);

  await page.getByRole('button', { name: 'Next match' }).click();
  await expect.poll(() => page.evaluate(() => {
    const state = window as typeof window & { __searchScrolls?: string[] };
    return state.__searchScrolls?.filter(text => text.includes('Zürich')).length ?? 0;
  })).toBe(2);
});

test('shows transcript actions for a late selection in compacted view', async ({ page }) => {
  const transcript = longAlternatingTranscript();
  await page.route('**/api/v1/transcription/1/transcript', route => route.fulfill({
    json: { available: true, transcript },
  }));
  await page.goto('/audio/1');

  const passage = page.locator('[data-transcript-text]').filter({ hasText: 'Zürich umsteigt.' });
  await passage.evaluate(element => {
    const node = element.firstChild;
    if (!node) throw new Error('Transcript text node is missing');
    const text = node.textContent ?? '';
    const start = text.indexOf('umsteigt.');
    const range = document.createRange();
    range.setStart(node, start);
    range.setEnd(node, start + 'umsteigt.'.length);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    document.dispatchEvent(new Event('selectionchange'));
  });

  await expect(page.getByRole('button', { name: 'Edit', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Listen', exact: true })).toBeVisible();
});
