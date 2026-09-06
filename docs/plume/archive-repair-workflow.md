# Audit-driven archive repair

`queue-archive-quality-rerun.py` replaces the legacy queue/controller apply paths.
Those paths now refuse live dispatch because their repetition-only gate could
accept empty transcripts and the controller ignored failed comparisons.

All generated audits, plans, ledgers, SQLite backups, transcript snapshots, and
comparison reports belong outside the repository. Keep private archive contents
local. Never upload audio or private reports. The API client accepts loopback
HTTP only and disables proxies and redirects.

## Preview and selection

Regenerate the audit before selection. It retains every non-deleted parent ID,
including reference-only Granola documents, which are explicitly distinguished
from recordings without readable audio transcripts. Empty transcripts and invalid
timestamps are review signals; legitimate silence/music must not be blindly
retranscribed based on an empty word count.

```sh
python3 scripts/build-archive-quality-audit.py --output /private/audit-latest.html
python3 scripts/queue-archive-quality-rerun.py \
  --audit /private/audit-latest.csv --category text --human exclude \
  --limit 20 --max-hours 6 --output /private/text-preview.json
python3 scripts/queue-archive-quality-rerun.py \
  --audit /private/audit-latest.csv --category speakers \
  --severity Critical High Review --max-hours 48 \
  --limit 20 --output /private/speaker-preview.json
```

Selection supports severity, category, model, diarizer, individual duration,
aggregate duration, trusted mapping presence, previous run, and ledger state.
Candidates include current large-v3 transcripts. Preview includes source audio
length measured by ffprobe, evidence, names and their provenance, historical
attempts, and explicit blockers. A speaker preview is a candidate list, not a
claim that its inherited names are verified. `--verified-speakers-only` excludes
unverified title-only counts; it may return fewer than the requested limit.

Speaker dispatch requires a local evidence JSON file supplied during selection:

```json
{
  "job-id": {
    "expected_speakers": 2,
    "reason": "User confirmed two audible participants, or documented local audio review"
  }
}
```

Do not invent this evidence from bracketed titles. Recordings with trusted manual
mappings have `blocked_metadata_reconciliation` state and remain visible in the
program. In-place reruns are blocked until identity reconciliation is available;
speaker label equality across independent diarizations does not prove identity.

## Sequential execution

```sh
python3 scripts/queue-archive-quality-rerun.py \
  --apply-plan /private/canary.json --api-base http://127.0.0.1:PORT
```

Discover the current backend listener; its port is not guaranteed to be 59152.
An applied plan cannot exceed six source-audio hours. One job is dispatched at a
time with a 12 GiB free-space floor, an advisory controller lock, a check for the
legacy controller, and a live queue-idle check. It checks that completion hooks
use local Ollama before dispatch. Avoid submitting unrelated UI jobs during a run.

Each dispatch revalidates the frozen transcript hash and metadata version. It
backs up JSON, Markdown, mappings, job metadata, notes/summaries, and available
sidecars in a unique run directory. Source audio is read and hashed, never copied
into Plume or modified. Dispatch intent and backups are persisted before POST.
An interrupted or ambiguous POST remains `queued`; it is never blindly retried.
Investigate execution history and the saved manifest before any resumed work.

Completion is followed by an artifact stability window and full comparison:

- Canonical n-gram, isolated filler timing, repetition, and speaker metrics.
- Uncapped repetition burden, since the audit display score saturates at 100.
- Word count, transcript endpoint, union of speech intervals, and unlabeled speech.
- Speaker count, dominance, and over-segmentation against documented evidence.
- Human metadata, mappings, notes, summaries, and source-audio checksum.
- JSON/Markdown availability, a new completed execution, recorded settings, and
  Plume transcript/open and search APIs.

A 30% word-count/coverage reduction or 15% endpoint reduction requires audio
review even if repetition disappears. Automated acceptance does not establish
semantic fidelity or prove that quiet/distant speech was recovered. Any rejected,
failed, or unresolved result stops the plan before another recording is queued.
Rejected replacements and original artifacts are retained; the runner does not
silently restore a possibly worse transcript or rewrite historical evidence.

## Ledger and recovery

The separate SQLite ledger has one row for every parent job and one attempt row
per run/job. States include `not_attempted`, `queued`, `completed_unreviewed`,
`accepted`, `improved_review`, `rejected`, `failed`, `blocked_missing_audio`,
`blocked_reference_document`, and `blocked_metadata_reconciliation`.

Historical manifests are imported idempotently as unreviewed, never accepted from
model names or narrow scores. Accepted, queued, rejected, and review-needed jobs
are excluded from automatic reselection. The HTML/CSV audit displays repair state,
attempt count, and reason when the ledger exists.

`compare-retranscription-quality.py` retains the historical narrow-score columns
and adds full audit metrics and verdicts. Its exit code is 2 whenever full
acceptance has not been established. Historical runs without immutable baselines
or complete metadata cannot be certified retroactively. Save revised reports in
a new private review directory to retain earlier comparison artifacts.

The current runner checks that execution metadata accurately records the intended
settings. Older desktop binaries incorrectly apply GORM defaults to explicit
false/zero values when creating execution rows (including `vad_preprocess=false`).
The repository fix preserves all observed parameters atomically. Single-job and
batch start paths also preserve existing summaries, which may contain human edits. Rebuild/restart
the desktop backend to use it; do not rewrite historical execution rows merely to
make a gate pass. Runtime logs can support an explicit review note instead.

Desktop updates must use Plume's [local ad-hoc signing workflow](../../desktop/electron/README.md#local-signing).
Never choose an Apple/keychain identity from an old Plume signature or reuse a
certificate belonging to another project. Stage, sign, verify, then replace the
app while retaining a rollback copy and database backup.

## Validation

```sh
python3 -m unittest discover -s scripts/tests -v
go test ./internal/repository -count=1
```

The execution parameter regression test reproduces the prior mutation before the
fix and checks both in-memory and persisted values through completion.

The API package currently has an unrelated compile failure in the obsolete
`hf_token_handlers_test.go` (removed handler symbols). The summary-preservation
regression was reproduced on both start paths and verified using a temporary Go
overlay excluding only that obsolete test file; the repository file was not changed.

Full-audio verification includes the Python decoder contract tests, the WhisperX
argument tests, the transcription service/repository/model tests, and a production
frontend build. The broader adapter suite also exposes pre-existing Sherpa tests
that expect a 0.5 default while the implementation uses 0.8; this change does not
alter Sherpa's threshold or claim that suite passes.

## Local audio cross-check findings

The continuation uses offline, diagnostic outputs outside Plume to compare
standard faster-whisper timestamp decoding with WhisperX batched decoding and
overlapping crops. Such agreement is supporting evidence, not a human listening
check, and disagreement must block automatic acceptance. A smaller chunk size
is not automatically better; retain the baseline until a canary demonstrates an
improvement without new filler or speech loss.

The service-to-adapter conversion and WhisperX CLI builder now forward chunk size,
previous-text conditioning, and all three configured fallback thresholds. Tests
cover both boundaries. This fixes silently dropped settings; it does not claim
that every upstream batched decoder applies all fallback options effectively.

The canonical filler timing gate now covers German `Vielen Dank`/`Danke` alongside
English `thank you`, retaining the original English-only metric for historical
comparisons. A nine-second German closing phrase no longer escapes triage merely
because it is not English. Plausibly timed thanks remain unflagged.

Speaker diagnostics use a four-slot Sortformer pass to avoid forcing a title's
assumed count into the evidence. Model-derived labels do not establish identities;
a contradictory count or discarded short speaker streams requires review.

## Preserve quiet speech

`whisper_full_audio=true` selects a bundled subprocess entry point that retains
the complete input timeline, uses faster-whisper's timestamp decoder, and keeps
WhisperX alignment plus the existing independent diarization step. It does not
modify the installed WhisperX source. It is incompatible with `vad_preprocess=true`.
The Silero setting remains available for the legacy mode; full-audio decoding
explicitly bypasses VAD cuts and records that fact in `metadata.asr_diagnostics`.

The local canary isolated two causes of speech loss: text-only decoding omitted
a short exchange on identical audio, and Silero excluded later sentences from
another recording. Full-timeline decoding recovered those sentences. The tested
quality profile uses large-v3, CPU/int8, temperature zero without sampling fallback,
no previous-text conditioning, logprob threshold -0.5 and no-speech threshold 0.5.
These are measured canary settings, not a promise of error-free recognition.

Use `--full-audio` during queue preview to freeze this choice into a plan. Legacy
plans retain their original mode. Execution gates verify the new mode and its
thresholds. Word-count/coverage gates remain unchanged: removing a long repeated
passage still requires a documented review. Candidate trials, full decoding
evidence, source hashes and before snapshots stay in the private processing folder.

## Continue offline work while listening review is pending

`run-archive-candidates.py --plan /private/plan.json --output /private/candidates`
runs a frozen eligible plan with the local WhisperX environment's Python. It
generates full-audio and Silero comparison JSON files separately from Plume.
It does not apply the plan through the live API, change originals, or promote
repair-ledger states. Use a separate candidate manifest to track this work.

There is no small-batch completion limit. Restarting with the same plan/output
resumes an interrupted recording and skips terminal candidate entries. Completed
decoder outputs are reused only for the same source hash. Per-recording failures
remain visible and do not prevent unrelated candidate generation; they are not
automatically retried. A `STOP` file in the output directory, low disk space, or
an active Plume transcription pauses the worker. Both offline-worker and repair
locks prevent competing controllers. Removing a STOP file requires user intent.

This worker currently uses the existing unprotected, completed-parent backup
path. Protected metadata, unreadable/empty baselines and longer source recordings
remain separate work; do not infer that exhausting one eligible queue completes
the archive. Existing live acceptance gates remain unchanged.

## Detected-language metadata

WhisperX 3.8.0's CLI assigns its initial alignment language to the final output.
With automatic language detection, this can label German ASR text as English even
after German alignment succeeds. The full-audio adapter now takes the detected
language from its decoder evidence rather than that overwritten CLI field;
translation output remains labeled English. Missing/malformed language evidence
fails validation. This does not correct historical artifacts or certify that
Whisper's language guess was right, and the legacy CLI path is unchanged.

An unavailable alignment model is a separate failure: an ASR language guess may
select a language without a default aligner. Investigate those recordings without
forcing a language from a title or silently discarding the ASR result. The
separate candidate worker does not require alignment to preserve its output.
