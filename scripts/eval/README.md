The canonical accuracy path is `score_reference.py`, with `compare_local.py`
and `go run ./cmd/transcription-eval` for production word assignment. Label
coverage, model agreement and a correct speaker count are not accuracy scores.

Run the regression checks from a clean checkout:

```sh
uv venv .diagnostics/eval-env --python 3.11
uv pip install --python .diagnostics/eval-env/bin/python -r scripts/eval/requirements.txt
PLUME_EVAL_PYTHON="$PWD/.diagnostics/eval-env/bin/python" bash scripts/check-diarization.sh
```

Build the embedded desktop backend with `bash scripts/build-desktop-backend.sh`.
It uses locked frontend dependencies and stamps the source commit, dirty state,
version and commit date. The desktop package still needs its tools and signing
steps. A backend-only build is not a substitute for the complete installed app.

`public_benchmark.py` runs four fixed excerpts from AMI IS1009a against published
RTTM derived from manual annotations. Supply separate cached Community-1 and
NeMo Python runtimes and an HF cache; inference is offline. `--fetch-source`
downloads hash-pinned public source files. The audio stays outside Git.

```sh
.diagnostics/eval-env/bin/python scripts/eval/public_benchmark.py \
  --fetch-source --source .diagnostics/ami-source --out .diagnostics/ami-run-1 \
  --community-python /path/to/pyannote/.venv/bin/python \
  --nemo-python /path/to/parakeet/.venv/bin/python \
  --cache /path/to/huggingface --device auto
```

Read each stage's exit code, source/configuration provenance, DER components,
speaker-count discrepancy, per-speaker recall and brief-turn misses. Raw
Sortformer heads are a diagnostic, not the production identity result. The
two-speaker excerpt is contained in the opening excerpt: do not double-count
it in aggregate metrics. These are tuning smoke checks from one public meeting,
not held-out proof of quality on personal recordings or unseen model test data.

The scorer also reports overlap/non-overlap speaker-time precision and recall,
worst-person recall and completely missed people. All regions reuse one global
identity permutation; re-mapping each region could hide speaker swaps. A brief
turn is an original RTTM turn under one second, measured before UEM clipping.
An excellent overall DER can still mean zero recall for a person who only says
a few words. Publishing every person everywhere increases false speaker-time,
so recall must be read alongside precision and DER. Literal unknown labels earn
no identity credit in either the timeline or fixed-word checker.

`score_oracle_words.py` additionally compares assignment policies using the
hash-pinned AMI manual word/timing archive. It removes all reference speaker
labels before running each assembler. This isolates speaker attribution: its
zero text WER is by construction and is **not** an ASR result. The optional
`transcription-eval -overlap-review` policy trades label coverage for precision;
it remains experimental because the initial benchmark worsened cpWER on two
excerpts. The production default retains the exclusive assignment timeline and
explicitly flags detected voices that have no attributed words.

When the UEM covers the complete WAV, the scorer also reports `full_text_wer`
from all decoded text, including words the aligner could not time. Prefer it
for text accuracy: scoring only timed words can hide unaligned hallucinations.
Full text is deliberately not scored against a partial UEM.

For personal audio, `prepare_reference.py` creates an unreviewed annotation
pack. Only mark it human-reviewed after a person has checked every scored
interval. Published manual annotations retain their original annotator/source
provenance; Codex does not claim to have listened and annotated them.

`harness.py`, `stage_recluster.py` and `stage_assemble_score.py` preserve older
diagnostic experiments. Their heuristic coverage and speaker statistics are not
the canonical production assembler or human-reference accuracy scores.

`stage_diar_nemotron3.py` uses an independently cached native Transformers model
as a local challenger. `run_idle_comparison.py --plan /private/plan.json` runs
source-hashed cases with bounded immutable attempts and offline inference.
Set `requires_verified_install` in the plan to the installation receipt, source
commit and backend hash when experiments must follow a verified desktop update.
New foreground work preempts only the comparison's own process group. An
explicit MPS failure is retained; a CPU attempt must be separately named.
These experimental runners never promote models or accepted transcripts.
Nemotron runs retain compressed native frame logits for
`rethreshold_activity.py --candidate result.json --threshold 0.4 --out derived.json`.
The decoder matches the official processor, retains brief overlapping activity,
and verifies immutable artifact hashes. Choose any setting on tuning clips and
freeze it before independent validation; do not search held-out thresholds.

Idle-only execution remains the default. An explicit expiring `cpu_asr_permit`
may permit a one-CPU-thread MPS Nemotron experiment beside the final CPU ASR job
after its GPU diarization completed. It pins that job's completed artifact and
live CPU process. New queued work, a running GPU diarizer or an expired/changed
permit revokes the slot and stops the owned benchmark. This does not authorize
an app restart while a recording is processing; runtime under concurrent load
is not a controlled speed comparison.
`stage_idle_community.py` supplies the automatic-count production Community-1
baseline through the same guarded runner; its `--model` value is the existing
Hugging Face cache root. It records the production MPS per-operation fallback
policy explicitly. Use a separate plan directory for independent validation
cases so their receipts remain separate from tuning results.

The application now shares the inference core and frozen
`primary_speakers_review_unmatched_v2` proposal policy with evaluation. A separately prepared
local runtime can refine existing machine labels where Community-1's regular
tracks are ambiguous and Nemotron has a strong activity winner. Explicit unknown
words stay unknown. A strongly disputed word on an unmatched channel keeps a reviewable boundary. Only identities supported by the primary timelines can be assigned. Go checks the complete proposal batch against the source,
preserves exact text/timing and rebuilds displayed speaker boundaries. Changed
labels require review; automatic identity naming/publication remains gated.

Prepare an already downloaded pinned model with:

```sh
python3 scripts/prepare-native-word-refinement.py \
  --root /path/to/whisperx-env/nemotron3 \
  --model-source /path/to/verified/nemotron-model --enable
```

The feature is active only with that enabled, revision-matched local profile.
Disable it by setting `enabled` to false in its `ready.json`. Runtime/model
failures preserve the primary result and explicitly request speaker review.
The full application stage is testable without a database using
`transcription-eval -native-runtime /path/to/nemotron3 -audio full-clock.wav`.
The production policy was selected on IS1009a; ES2004a became development data
when an unknown-filling variant failed. Frozen validation on TS3003a abstained,
so independent accuracy gains remain unproven. Do not call the manual-word
attribution results new ASR accuracy or claim a global engine winner.

### Compare conflicting speaker labels on a saved machine candidate

Use the Parakeet Python environment and a local TitaNet model. Supply the same
mono 16 kHz WAV used to generate the native activity. For a production job this
is the waveform produced by the job's FFmpeg conversion. The mapping comes from
the corresponding word-refinement proposals file.

```sh
"$PARAKEET_PYTHON" scripts/eval/compare_assigned_voices.py \
  --transcript assembled.json --audio input.wav --native native-result.json \
  --mapping proposals.json --model "$TITANET_MODEL" --out voice-evidence.json
go run ./cmd/transcription-eval -transcript assembled.json \
  -voice-comparison voice-evidence.json -out comparison.json
```

Both commands create a new file. The second verifies the source transcript hash
and uses the app's word and paragraph assembly code. This comparison runs offline
on machine candidates. A newly introduced speaker boundary needs independent
activity on the adjacent word, and an isolated acknowledgement can veto a merge.
