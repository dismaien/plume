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
