# Plume Voice Memo Archive

Plume is a private fork of Quill for turning a large Apple Voice Memos library into a durable, searchable knowledge archive.

## Goals

- Keep raw Apple exports as preservation masters.
- Avoid storing audio twice unless explicitly requested.
- Preserve Apple original titles, especially location-derived titles.
- Display recording times in the place-local time of the recording, not the current Mac timezone.
- Generate readable summary titles and searchable speaker suffixes.
- Let Quill/Plume do the first speaker pass, then surface low-confidence speaker labels for review.

## Naming

Human display title:

```text
YYMMDD HH:MM - Summary Title [Speaker, Speaker] @Specific Place
```

Filesystem-safe folder name:

```text
YYYY-MM-DD_HHMM - Summary Title -- Speaker Speaker -- Specific Place
```

`@Specific Place` should be the most specific trustworthy label available:

- Apple location title, when present.
- Manually corrected place label.
- Travel ledger place label.
- Broader fallback such as `@Bali` only when no more specific label exists.

## Storage Modes

Plume adds `AUDIO_STORAGE_MODE` / `PLUME_AUDIO_STORAGE_MODE`:

- `copy`: existing Quill behavior; bundle contains its own `audio.*`.
- `reference`: bundle stores transcripts and metadata while `audio_path` points to the raw archive file.

For the voice memo archive, use `reference` only after a canary import proves every audio-serving, speaker-snippet, contact-signature, and restore flow works against raw archive paths. Otherwise use `copy` and accept the storage cost until the reference path is proven.

## Target Products

```text
Voice Notes Archive/
  00-index/
    recordings.sqlite
    recordings.csv
    checksums.sha256
    timezone-ledger.csv
  10-raw-apple-exports/
    original .m4a / .qta files
  30-plume-vault/
    transcript.md
    transcript.json
    metadata.json
```

Raw audio should sync to cloud once. Plume vault sync should exclude duplicate `audio.*` files when `reference` or raw-archive-first mode is used.

## Next Implementation Slices

- Apple Voice Memos importer with `.m4a` / `.qta` handling.
- Location/timezone ledger and title formatter.
- Speaker review queue and suffix regeneration.
- Archive index writer for CSV, SQLite, and checksums.
