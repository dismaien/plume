# Plume Archive Quality Retranscription Handoff

Date: 2026-09-05

## Mission

Rehabilitate the complete Plume voice-memo archive, not just a small canary batch.
Prioritize transcripts with catastrophic text hallucination or collapsed speaker
diarization, retranscribe them with the trusted WhisperX pipeline, preserve all
human metadata and previous transcript artifacts, and only accept replacements
that measurably improve quality.

This is private personal data. Work locally. Do not upload audio, transcripts,
or audit data to third-party services.

## Current State

- Repository: `/Users/leona/Documents/Playground/plume`
- Working branch: `codex/voice-memo-archive`
- Latest handoff-relevant commit: `bc9dcc8` (`Add interactive archive quality audit`)
- Remote: `https://github.com/dismaien/plume.git`
- Plume database: `/Users/leona/Library/Application Support/plume-desktop/data/quill.db`
- Raw preservation archive:
  `/Users/leona/Documents/Voice Notes Archive/10-raw-apple-voice-memos-sync-20260615`
- Processing data:
  `/Users/leona/Documents/Voice Notes Archive/20-quill-processing`
- Retranscription runs:
  `/Users/leona/Documents/Voice Notes Archive/20-quill-processing/retranscription-runs`
- Interactive audit:
  `/Users/leona/Documents/Voice Notes Archive/20-quill-processing/archive-quality-audit-20260904.html`
- Audit CSV:
  `/Users/leona/Documents/Voice Notes Archive/20-quill-processing/archive-quality-audit-20260904.csv`

At handoff time, Plume and its backend were running, no overnight repair
controller was active, and `df -h /` reported about 83 GiB available. Treat all
three as transient facts and recheck them before starting work.

At the last full scan:

| Measure | Count |
| --- | ---: |
| Parent recordings | 1,748 |
| Completed readable transcripts | 1,633 |
| Critical | 522 |
| High | 367 |
| Review | 325 |
| No automated flag | 419 |
| Unavailable | 115 |
| Text-signal flags | 1,147 |
| Speaker-signal flags | 180 |
| Total audio | 908.9 hours |
| Total transcript words | 5,164,529 |

The classifications are conservative triage signals, not ground-truth error
labels. Categories overlap. `No automated flag` only means the current checks
did not find a known failure signature.

## Why Only Nine Recordings Were Repaired

The September 4 work was deliberately a bounded canary, not an estimate of the
affected archive. Six repair runs processed nine recordings:

- Eight passed the existing repetition gate.
- One improved but still requires review.
- Seven were diarization-focused: six were reported as recovered and one as
  improved-but-review.

That result validates the repair direction. It does not justify stopping after
nine. The full audit subsequently found 522 critical recordings.

## Non-Negotiable Safety Rules

1. Never modify or delete raw archive audio.
2. Never import giant source recordings into Plume a second time merely to
   retranscribe them.
3. Before every retranscription, back up the current JSON, Markdown, and speaker
   mappings into that run's directory.
4. Stop queuing work if free disk space falls below 12 GiB.
5. Run one CPU-heavy transcription at a time. Multiple queued records are fine
   only if Plume still executes them sequentially.
6. Do not overwrite or discard manually corrected titles, dream markers,
   locations, dates, speaker names, or accepted speaker mappings.
7. Do not call a job successful merely because its status is `completed`.
   Compare before and after and retain a reviewable manifest.
8. If a new transcript is worse or materially shorter without a clear audio
   reason, stop that batch and preserve the backup for restoration.
9. Do not use MLX or quantized `large-v3-turbo-q4` for archive repair. Use the
   verified WhisperX/faster-whisper `large-v3` path with Silero VAD.

## Trusted Repair Configuration

The existing queue scripts contain the current known-good settings:

```text
model_family: whisper
model: large-v3
device: cpu
compute_type: int8
vad_method: silero
vad_preprocess: false
condition_on_previous_text: false
temperature: 0
beam_size: 5
best_of: 5
diarize_model: nvidia_sortformer
max_speakers: evidence-dependent, never blindly unlimited
```

Keep these stable during the first archive-wide phase. Change one parameter at
a time only when a documented canary demonstrates an improvement.

## Important Limitation In The Existing Controller

Do not run `scripts/start-overnight-quality-reruns.sh` as the first action.

`scripts/queue-whisperx-quality-rerun.py` still uses the older narrow score:
`thank_you * 2 + big_big * 3 + repeated_word_runs * 10`. By default it also
excludes recordings already marked as Whisper `large-v3`, and the overnight
controller skips recordings with trusted human speaker mappings. The new audit
detects broader n-gram loops, physically implausible isolated segments,
speaker collapse, over-segmentation, metadata errors, missing files, and
Sherpa-collapse signatures. Therefore the current controller cannot represent
the whole 522-recording critical queue.

The first engineering task is to make the repair queue consume the canonical
audit output, or share the scanner's classification functions directly.

## Recommended Order Of Work

### 1. Pin And Protect The Starting State

Before changing code or data:

```bash
cd /Users/leona/Documents/Playground/plume
git status --short --branch
git log -1 --oneline
df -h /
```

Confirm Plume is running, port `59152` is listening, and no other overnight
controller is active. Create a timestamped SQLite backup using SQLite's backup
command, not a live filesystem copy.

### 2. Regenerate The Canonical Audit

```bash
python3 scripts/build-archive-quality-audit.py \
  --output "$HOME/Documents/Voice Notes Archive/20-quill-processing/archive-quality-audit-latest.html"
```

The adjacent `archive-quality-audit-latest.csv` is the machine-readable source
for queue construction. Verify its row count equals the live count of
non-deleted parent jobs and that transcript parse errors remain zero.

### 3. Upgrade Queue Selection Before Scaling

Extend the queue tooling so it can select from the audit by:

- classification: `Critical`, `High`, `Review`
- category: `text`, `speakers`, `metadata`, `files`, `availability`
- model and diarizer
- maximum recording duration and maximum aggregate batch hours
- whether trusted human speaker mappings exist
- prior repair run and latest before/after verdict

Every job needs a stable ledger state such as `not_attempted`, `queued`,
`completed_unreviewed`, `accepted`, `improved_review`, `rejected`, `failed`, or
`blocked_missing_audio`. Do not infer completion solely from the current model
name because some existing `large-v3` transcripts are still critically bad.

Make the selector dry-run by default. Its preview must include job ID, title,
duration, current model, critical evidence, trusted names, previous attempts,
and total selected audio hours.

### 4. Process Separate Queues

Do not combine every failure type into one opaque batch.

**Queue A: catastrophic text failures**

Start with `Critical` text cases showing long repeated spans, high repetition
scores, implausible isolated `thank you` segments, or the Sherpa-collapse
signature. Prefer shorter recordings within the same risk tier to restore more
files while the pipeline is still being validated.

**Queue B: collapsed diarization with strong speaker evidence**

Use recordings with two to four trusted people from manual mappings or
carefully reviewed title suffixes, but fewer detected speakers or at least 90%
dominance. Pass an evidence-based `max_speakers`. Do not assume every bracketed
title is correct.

**Queue C: protected human metadata**

Handle recordings with manual/accepted speaker mappings separately. Back up
the mappings, retranscribe, reconcile new diarization labels to trusted names,
then run title-speaker synchronization. Never silently drop those recordings
from the overall repair program.

**Queue D: failed, uploaded, missing, and borderline records**

Only after the critical text and speaker queues are stable, investigate the 99
failed and 16 otherwise unavailable recordings, then move through `High` and
`Review` cases. Missing audio is an archive-reconciliation issue, not a reason
to create a duplicate copy blindly.

### 5. Use Small, Sequential Batches

For the next stage, queue one recording at a time and cap each run at roughly
three to six audio hours. Let the job finish, produce its comparison, and only
then queue the next. Increase throughput only after at least 20 consecutive
accepted repairs without a regression or metadata loss.

Use unique run directories. Never reuse or overwrite a previous run name.

### 6. Gate Every Result

Run the existing comparison immediately after each completed batch:

```bash
python3 scripts/compare-retranscription-quality.py \
  --run-dir "/absolute/path/to/the/run" \
  --db "$HOME/Library/Application Support/plume-desktop/data/quill.db"
```

Then regenerate the full audit. A replacement is eligible for acceptance only
when all applicable conditions hold:

- no catastrophic repeated span remains
- repetition/hallucination score materially decreases
- `thank you` and similar filler loops disappear or are supported by plausible
  timing and surrounding speech
- word count and speech coverage do not collapse unexpectedly
- a multi-person recording no longer collapses to one speaker
- diarization does not become implausibly over-segmented
- trusted speaker names and title metadata survive or are correctly reconciled
- the transcript opens and searches correctly in Plume

For important or ambiguous recordings, listen to samples near the beginning,
middle, and end, including at least one prior failure region. Automated metrics
cannot prove that quiet or distant speech was recovered.

Extend `compare-retranscription-quality.py` to use the same full metrics as the
canonical audit. Its current repetition score is useful but too narrow to be
the final acceptance gate.

### 7. Keep A Durable Progress Ledger

After each accepted batch, record:

- run directory and timestamp
- job ID and title
- before/after model and settings
- before/after audit classification and evidence
- text verdict
- diarization verdict
- metadata reconciliation result
- manual spot-check result, when performed
- final disposition and reason

The interactive audit should expose attempted/accepted/rejected state so Leona
can see real progress without reconstructing it from folders.

### 8. Commit Code, Not Private Archive Data

Commit and push reusable scripts, tests, and documentation to
`codex/voice-memo-archive`. Do not commit database backups, transcripts, audio,
run manifests containing personal text, or generated archive audit files.

## First Concrete Deliverable

Before launching a large overnight run, produce:

1. An audit-driven dry-run queue of the next 20 critical text recordings.
2. A separate dry-run queue of the next 20 high-confidence diarization
   collapses.
3. An updated comparison tool using the full audit metrics.
4. A ledger that prevents already accepted repairs from being selected again.
5. A five-recording sequential canary across both failure types, with explicit
   before/after results.

If all five pass, continue sequentially through the remaining critical queue,
respecting the 12 GiB free-space floor. If any fail or lose metadata, stop and
fix the categorical cause before continuing.

## Definition Of Done

The archive-wide repair effort is complete only when:

- every one of the 1,748 parent recordings has a durable audit/ledger state
- every available critical item has been attempted or explicitly blocked with
  a reason
- accepted replacements pass text, speaker, metadata, and file-integrity gates
- rejected replacements remain recoverable from their backups
- failed/unavailable items have explicit reconciliation outcomes
- the final full audit and CSV are regenerated
- remaining `High` and `Review` items are presented as a prioritized human
  review queue, not silently declared good
- raw preservation audio remains byte-for-byte untouched

## Suggested Astra Prompt

Read this handoff and inspect the repository and live Plume database before
acting. Begin by regenerating the full audit and verifying the stated counts.
Then upgrade the repair selector and comparison ledger so they use the full
audit classifications rather than the legacy repetition score. Show the two
20-item dry-run queues and the proposed five-item canary before applying the
canary. Preserve raw audio and human metadata, keep a 12 GiB free-space floor,
run transcriptions sequentially, compare every result, and continue through the
critical backlog only while the acceptance gates keep passing. Commit and push
reusable code to `codex/voice-memo-archive`; keep private archive data out of Git.
