import { test, expect } from '@playwright/test';
import { installApiMocks, setLocalAuthState } from './fixtures/api-mocks';

test.beforeEach(async ({ page }) => {
  await setLocalAuthState(page);
  await installApiMocks(page);
});

const initial = {
  text: 'The original mistake. Another section.',
  segments: [
    { start: 0, end: 4, text: 'The original mistake.', speaker: 'SPEAKER_00' },
    { start: 4, end: 8, text: 'Another section.', speaker: 'SPEAKER_01' },
  ],
};

test('save, reload, compare and restore retains every correction', async ({ page }) => {
  let transcript = structuredClone(initial);
  let version = 0;
  const versions: { id: number; kind: string; created_at: string; transcript: typeof initial }[] = [];
  await page.route('**/api/v1/transcription/1/transcript', route => route.fulfill({ json: { available: true, transcript } }));
  await page.route('**/api/v1/transcription/1/transcript/editor', async route => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      expect(body.expected_version).toBe(`v${version}`);
      if (!versions.length) versions.push({ id: 1, kind: 'original', created_at: '2026-09-08T01:00:00Z', transcript: structuredClone(transcript) });
      if (body.restore_id) transcript = structuredClone(versions.find(r => r.id === body.restore_id)!.transcript);
      else {
        expect(body.segment_texts).toHaveLength(2);
        transcript = { ...transcript, text: body.segment_texts.join(' '), segments: transcript.segments.map((s, i) => ({ ...s, text: body.segment_texts[i] })) };
      }
      version++;
      versions.push({ id: versions.length + 1, kind: body.restore_id ? 'restore' : 'edit', created_at: `2026-09-08T01:0${version}:00Z`, transcript: structuredClone(transcript) });
    }
    await route.fulfill({ json: { version: `v${version}`, transcript } });
  });
  await page.route('**/api/v1/transcription/1/transcript/revisions', route => route.fulfill({ json: { revisions: [...versions].reverse(), next_before: null } }));
  await page.route(/\/transcript\/revisions\/\d+$/, route => {
    const revision = versions.find(r => r.id === Number(route.request().url().split('/').pop()))!;
    return route.fulfill({ json: { revision, transcript: revision.transcript } });
  });

  await page.goto('/audio/1');
  await page.getByRole('button', { name: 'Edit text', exact: true }).click();
  let dialog = page.getByRole('dialog', { name: 'Edit transcript', exact: true });
  await dialog.getByRole('textbox', { name: 'Section 1 text' }).fill('The corrected sentence.');
  await dialog.getByRole('button', { name: 'Save correction', exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator('[data-transcript-text]').first()).toContainText('The corrected sentence.');
  await page.reload();
  await expect(page.locator('[data-transcript-text]').first()).toContainText('The corrected sentence.');
  const menu = page.getByRole('button').filter({ has: page.locator('svg.lucide-ellipsis-vertical') }).first();
  await menu.click();
  await page.getByRole('menuitem', { name: 'Timeline View', exact: true }).click();
  await expect(page.locator('[data-transcript-text]').first()).toContainText('The corrected sentence.');
  await expect(page.locator('[data-transcript-text]')).toHaveCount(2);
  await expect(menu).toHaveAttribute('data-state', 'closed');
  await menu.focus();
  await menu.press('ArrowDown');
  await page.getByRole('menuitem', { name: 'Plain Text', exact: true }).click();
  await expect(page.locator('[data-transcript-text]')).toHaveCount(1);
  await expect(page.locator('[data-transcript-text]')).toContainText('The corrected sentence. Another section.');
  await page.getByRole('button', { name: 'Edit history', exact: true }).click();
  dialog = page.getByRole('dialog', { name: 'Transcript edit history' });
  await dialog.getByRole('button', { name: /Original transcript/ }).click();
  await expect(dialog.getByTestId('revision-text')).toContainText('The original mistake.');
  await expect(dialog).toContainText('The corrected sentence.');
  await dialog.getByRole('button', { name: 'Restore this version' }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator('[data-transcript-text]').first()).toContainText('The original mistake.');
  expect(versions.map(r => r.kind)).toEqual(['original', 'edit', 'restore']);
  await page.getByRole('button', { name: 'Edit history', exact: true }).click();
  await page.getByRole('button', { name: /Text correction/ }).click();
  await expect(page.getByTestId('revision-text')).toContainText('The corrected sentence.');
});

test('conflicting save keeps the draft and cancel requires discarding it', async ({ page }) => {
  await page.route('**/api/v1/transcription/1/transcript/editor', route => route.request().method() === 'PUT'
    ? route.fulfill({ status: 409, json: { error: 'This transcript changed since you opened it. Reload the latest text before saving.' } })
    : route.fulfill({ json: { version: 'v0', transcript: initial } }));
  await page.goto('/audio/1');
  await page.getByRole('button', { name: 'Edit text', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Edit transcript', exact: true });
  const text = dialog.getByRole('textbox', { name: 'Section 1 text' });
  await text.fill('Keep my unsaved correction');
  await dialog.getByRole('button', { name: 'Save correction' }).click();
  await expect(dialog.getByRole('alert')).toContainText('changed since you opened');
  await expect(text).toHaveValue('Keep my unsaved correction');
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await dialog.getByRole('button', { name: 'Keep editing' }).click();
  await expect(text).toHaveValue('Keep my unsaved correction');
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await dialog.getByRole('button', { name: 'Discard changes' }).click();
  await expect(dialog).toBeHidden();
});

test('empty plain transcript remains editable and history stays reachable', async ({ page }) => {
  await page.route('**/api/v1/transcription/1/transcript', route => route.fulfill({ json: { available: true, transcript: '' } }));
  await page.route('**/api/v1/transcription/1/transcript/editor', route => route.fulfill({ json: { version: 'v0', transcript: '' } }));
  await page.goto('/audio/1');
  await page.getByRole('button', { name: 'Edit text', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Transcript text', exact: true })).toHaveValue('');
});

test('contextual Edit opens the selected passage instead of the start of a long transcript', async ({ page }) => {
  const segments = Array.from({ length: 35 }, (_, index) => ({
    start: index,
    end: index + 0.9,
    text: `Passage${String(index + 1).padStart(2, '0')}`,
    speaker: 'SPEAKER_00',
  }));
  const transcript = {
    text: segments.map(segment => segment.text).join(' '),
    segments,
    word_segments: segments.map(segment => ({
      start: segment.start,
      end: segment.end,
      word: segment.text,
      score: 1,
      speaker: segment.speaker,
    })),
  };

  await page.route('**/api/v1/transcription/1/transcript', route => route.fulfill({ json: { available: true, transcript } }));
  await page.route('**/api/v1/transcription/1/transcript/editor', route => route.fulfill({ json: { version: 'v0', transcript } }));
  await page.goto('/audio/1');

  await page.locator('[data-transcript-text]').evaluate((element, selectedText) => {
    const node = element.firstChild;
    if (!node) throw new Error('Transcript text node is missing');
    const start = node.textContent?.indexOf(selectedText) ?? -1;
    if (start < 0) throw new Error('Selected passage is missing');
    const range = document.createRange();
    range.setStart(node, start);
    range.setEnd(node, start + selectedText.length);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    document.dispatchEvent(new Event('selectionchange'));
  }, 'Passage32');

  const contextualEdit = page.getByRole('button', { name: 'Edit', exact: true });
  await expect(contextualEdit).toBeVisible();
  await contextualEdit.click();

  const dialog = page.getByRole('dialog', { name: 'Edit transcript', exact: true });
  const target = dialog.getByRole('textbox', { name: 'Section 32 text', exact: true });
  await expect(target).toBeVisible();
  await expect(target).toBeFocused();
  await expect(target).toHaveJSProperty('selectionStart', 0);
  await expect(target).toHaveJSProperty('selectionEnd', 'Passage32'.length);
  await expect(dialog.getByText('31–35 of 35')).toBeVisible();
});

test('contextual actions reopen after a saved edit removes stale word timings', async ({ page }) => {
  let version = 0;
  let transcript: typeof initial & { word_segments?: Array<{ start: number; end: number; word: string; score: number; speaker: string }> } = {
    ...structuredClone(initial),
    word_segments: [
      { start: 0, end: 1, word: 'The', score: 1, speaker: 'SPEAKER_00' },
      { start: 1, end: 2, word: 'original', score: 1, speaker: 'SPEAKER_00' },
      { start: 2, end: 4, word: 'mistake.', score: 1, speaker: 'SPEAKER_00' },
      { start: 4, end: 5, word: 'Another', score: 1, speaker: 'SPEAKER_01' },
      { start: 5, end: 8, word: 'section.', score: 1, speaker: 'SPEAKER_01' },
    ],
  };

  await page.route('**/api/v1/transcription/1/transcript', route => route.fulfill({ json: { available: true, transcript } }));
  await page.route('**/api/v1/transcription/1/transcript/editor', async route => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      transcript = {
        text: body.segment_texts.join(' '),
        segments: transcript.segments.map((segment, index) => ({ ...segment, text: body.segment_texts[index] })),
      };
      version++;
    }
    await route.fulfill({ json: { version: `v${version}`, transcript } });
  });

  await page.goto('/audio/1');
  await page.locator('[data-transcript-text]').filter({ hasText: 'original' }).dblclick();
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Edit transcript', exact: true });
  await dialog.getByRole('textbox', { name: 'Section 1 text', exact: true }).fill('The corrected mistake.');
  await dialog.getByRole('button', { name: 'Save correction', exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator('[data-transcript-text]').first()).toContainText('The corrected mistake.');

  await page.locator('[data-transcript-text]').filter({ hasText: 'Another' }).dblclick();
  await expect(page.getByRole('button', { name: 'Edit', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Listen', exact: true })).toBeVisible();
});

test('assign a selected phrase to another speaker without changing its words', async ({ page }) => {
  const source = { text: "Other words. It's like my safety in the world is limited.", segments: [
    { start: 0, end: 10, text: "Other words. It's like my safety in the world is ", speaker: 'SPEAKER_00' },
    { start: 10, end: 12, text: 'limited.', speaker: 'SPEAKER_01' },
  ] };
  let saved = false;
  await page.route('**/api/v1/transcription/1/transcript/editor', async route => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      expect(body.expected_version).toBe('v0');
      expect(body.segment_parts).toEqual([
        [{ text: 'Other words. ', speaker: 'SPEAKER_00' }, { text: "It's like my safety in the world is ", speaker: 'SPEAKER_01' }],
        [{ text: 'limited.', speaker: 'SPEAKER_01' }],
      ]);
      saved = true;
    }
    await route.fulfill({ json: { version: 'v0', transcript: source } });
  });
  await page.goto('/audio/1');
  await page.getByRole('button', { name: 'Edit text', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Edit transcript', exact: true });
  const field = dialog.getByRole('textbox', { name: 'Section 1 text', exact: true });
  await field.evaluate((node: HTMLTextAreaElement) => {
    node.focus(); node.setSelectionRange('Other words. '.length, node.value.length);
    node.dispatchEvent(new Event('select', { bubbles: true }));
  });
  await field.press('Shift+ArrowLeft');
  await field.press('Shift+ArrowRight');
  await dialog.getByRole('button', { name: 'Separate selected phrase' }).first().click();
  await expect(dialog.getByRole('textbox', { name: 'Section 1 part 1 text' })).toHaveValue('Other words. ');
  await expect(dialog.getByRole('textbox', { name: 'Section 1 part 2 text' })).toHaveValue("It's like my safety in the world is ");
  await dialog.getByRole('combobox', { name: 'Section 1 part 2 speaker' }).selectOption('SPEAKER_01');
  await dialog.getByRole('button', { name: 'Save correction' }).click();
  await expect(dialog).toBeHidden();
  expect(saved).toBeTruthy();
});

test('uncertain speaker turns remain visible after API normalization', async ({ page }) => {
  const transcript = {
    text: 'First uncertain phrase. Second uncertain phrase.',
    metadata: { diarization_status: 'uncertain', text_status: 'ready' },
    segments: [
      { start: 0, end: 2, text: 'First uncertain phrase.' },
      { start: 3, end: 5, text: 'Second uncertain phrase.' },
    ],
  };
  await page.route('**/api/v1/transcription/1/transcript', route => route.fulfill({ json: { available: true, transcript } }));
  await page.goto('/audio/1');
  await expect(page.getByText('Text ready · speakers need review.', { exact: false })).toBeVisible();
  await expect(page.getByText('Speaker unclear', { exact: true })).toHaveCount(2);
  await expect(page.locator('[data-transcript-text]')).toHaveCount(2);
});

test('partial attribution shows its size and a review navigation action', async ({ page }) => {
  await page.route('**/api/v1/transcription/1/transcript', route => route.fulfill({ json: {
    available: true,
    transcript: {
      text: 'Known quiet reply',
      metadata: { text_status: 'ready', diarization_status: 'uncertain' },
      segments: [{ start: 0, end: 3, text: 'Known quiet reply', speaker: 'SPEAKER_00' }],
      word_segments: [
        { start: 0, end: 1, word: 'Known', speaker: 'SPEAKER_00' },
        { start: 1, end: 2, word: 'quiet' }, { start: 2, end: 3, word: 'reply' },
      ],
    },
  } }));
  await page.goto('/audio/1');
  await expect(page.getByRole('status').filter({ hasText: 'speakers need review' })).toContainText('2 of 3 words have no speaker label');
  await expect(page.getByRole('button', { name: 'Review next unclear passage' })).toBeVisible();
});

test('an overlap-only voice remains reviewable when every word already has a label', async ({ page }) => {
  await page.route('**/api/v1/transcription/1/transcript', route => route.fulfill({ json: {
    available: true, transcript: {
      text: 'One labelled passage.',
      metadata: { text_status: 'ready', diarization_status: 'uncertain',
        speaker_review_reason: 'detected_voice_has_no_attributed_words', speaker_review_start: '4.2' },
      segments: [{ start: 0, end: 10, text: 'One labelled passage.', speaker: 'SPEAKER_00' }],
    },
  } }));
  await page.goto('/audio/1');
  await expect(page.getByText('A detected voice has no attributed words.', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Review next unclear passage' })).toBeVisible();
});

test('refined speaker words explain the change and expose a review jump', async ({ page }) => {
  await page.route('**/api/v1/transcription/1/transcript', route => route.fulfill({ json: {
    available: true, transcript: {
      text: 'A brief reply.',
      metadata: { text_status: 'ready', diarization_status: 'uncertain',
        word_refinement_status: 'applied_needs_review', word_refinement_applied_words: '2',
        speaker_review_reason: 'refined_speaker_words', speaker_review_start: '1.2' },
      segments: [{ start: 0, end: 3, text: 'A brief reply.', speaker: 'speaker_01' }],
    },
  } }));
  await page.goto('/audio/1');
  await expect(page.getByRole('status').filter({ hasText: 'speakers need review' })).toContainText('2 word speaker labels were refined');
  await expect(page.getByRole('button', { name: 'Review refined speaker labels' })).toBeVisible();
});

test('rerun candidate is compared anonymously before explicit acceptance', async ({ page }) => {
  const candidate = { text: 'New words.', segments: [{ start: 0, end: 2, text: 'New words.', speaker: 'SPEAKER_00' }] };
  await page.route('**/api/v1/transcription/1/transcript/editor', route => route.fulfill({ json: { version: 'v1', transcript: initial } }));
  await page.route('**/api/v1/transcription/1/transcript/revisions', route => route.fulfill({ json: {
    revisions: [{ id: 42, kind: 'candidate', created_at: '2026-10-29T01:00:00Z' }], next_before: null,
  } }));
  await page.route('**/api/v1/transcription/1/transcript/revisions/42', route => route.fulfill({ json: {
    revision: { id: 42, kind: 'candidate', created_at: '2026-10-29T01:00:00Z' }, transcript: candidate,
  } }));
  await page.goto('/audio/1');
  await page.getByRole('button', { name: 'Edit history', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Transcript edit history' });
  await dialog.getByRole('button', { name: /Rerun candidate/ }).click();
  await expect(dialog.getByTestId('revision-text')).toContainText('SPEAKER_00: New words.');
  await expect(dialog.getByTestId('revision-text')).not.toContainText('Alice');
  await expect(dialog.getByRole('button', { name: 'Accept this candidate' })).toBeEnabled();
});
