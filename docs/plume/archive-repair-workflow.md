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
