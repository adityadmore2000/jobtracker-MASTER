# Evaluation

This directory contains the local evaluation workflow for recorded WAV files under `data/raw/`.

The single transcript source of truth is:

- `evaluation/ground_truth.csv`

Run outputs are stored independently:

```text
evaluation/
├── ground_truth.csv
├── prompts/
│   └── job_tracker_vocab_v1.txt
├── runs/
│   └── <model>/
│       └── <run-folder>/
│           ├── config.json
│           ├── inference.csv
│           └── summary.json
├── reports/
│   ├── comparison_summary.csv
│   └── comparison_summary.md
└── archive/
    └── legacy_inference_runs.csv
```

## Docker Build

```bash
docker build \
  -f docker/faster-whisper.Dockerfile \
  -t job-tracker-faster-whisper:cuda .
```

## GPU Verification

```bash
docker run --rm \
  --gpus all \
  nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04 \
  nvidia-smi
```

## Interactive Container Startup

```bash
cd ~/dev-work/job_tracker_assistant

docker run --rm -it \
  --gpus all \
  --ipc=host \
  --shm-size=2g \
  -v "$PWD:/workspace" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  -w /workspace \
  job-tracker-faster-whisper:cuda \
  bash
```

## Foreground Docker Evaluation Workflow

The helper script avoids repeatedly typing the full `docker run` command while still keeping inference in the foreground.

Run experiments with:

```bash
./evaluation/fw_container.sh eval \
  --model medium \
  --device cuda \
  --compute-type float16 \
  --vad off \
  --beam-size 5 \
  --run-label fw_medium_fp16_vad_off_prompt_off
```

- Each command starts a temporary Docker container.
- Inference runs in the foreground.
- Logs and errors appear in the current terminal.
- Ctrl+C interrupts the running process.
- The temporary container is removed automatically after completion.
- Repository files, evaluation results, and cached models persist through host bind mounts.

Generate summaries:

```bash
./evaluation/fw_container.sh summary
```

Open a shell:

```bash
./evaluation/fw_container.sh shell
```

## Ground-Truth Generation

```bash
python3 evaluation/create_ground_truth_template.py
```

## ASR Correction Export

Confirmed company-name correction events can be exported for later review and curation:

```bash
source jobtracker-BE/.venv/bin/activate

python evaluation/export_asr_corrections.py \
  --output-dir evaluation/exports/latest
```

When `--database-url` is omitted, the exporter automatically reads `DATABASE_URL` from the OS environment or `jobtracker-BE/.env`.

An explicit override still wins:

```bash
python evaluation/export_asr_corrections.py \
  --database-url 'postgresql+psycopg://<user>:<password>@localhost:5432/job_tracker' \
  --output-dir evaluation/exports/latest
```

This writes:

```text
evaluation/exports/latest/
├── corrections_review.jsonl
├── corrections_review.csv
├── training_eligible.jsonl
└── export_summary.json
```

Behavior:

- `corrections_review.*` contains all stored correction metadata for manual review.
- `training_eligible.jsonl` contains only rows that include an `audio_reference` and a deterministic `corrected_transcript`.
- Records without audio references are kept in the review export and excluded from the training-ready export.
- The export is deterministic by `created_at`, then insertion order.
- The exporter is PostgreSQL-only and requires the migrated backend schema.
- Source correction records are never mutated or deleted.
- Periodic fine-tuning is not automatically triggered by this export.

## Manual Expected-Text Entry

After generating the template, manually fill:

```text
evaluation/ground_truth.csv
```

Only enter known expected transcripts. Leave unknown values blank.

## Smoke Test

```bash
python3 evaluation/evaluate.py \
  --model small \
  --device cuda \
  --compute-type float16 \
  --vad on \
  --beam-size 5 \
  --category short \
  --limit 1 \
  --run-label fw_small_fp16_vad_on_prompt_off_smoke
```

This creates:

```text
evaluation/runs/small/fw_fp16_vad_on_prompt_off_smoke/
```

## Baseline Evaluation

Baseline: VAD ON, prompt OFF

```bash
python3 evaluation/evaluate.py \
  --model small \
  --device cuda \
  --compute-type float16 \
  --vad on \
  --beam-size 5 \
  --run-label fw_small_fp16_vad_on_prompt_off
```

## Prompt Comparison

Prompt comparison: VAD ON, prompt ON

```bash
python3 evaluation/evaluate.py \
  --model small \
  --device cuda \
  --compute-type float16 \
  --vad on \
  --beam-size 5 \
  --prompt-file evaluation/prompts/job_tracker_vocab_v1.txt \
  --run-label fw_small_fp16_vad_on_prompt_on
```

## VAD Comparison

VAD comparison: VAD OFF, prompt OFF

```bash
python3 evaluation/evaluate.py \
  --model small \
  --device cuda \
  --compute-type float16 \
  --vad off \
  --beam-size 5 \
  --run-label fw_small_fp16_vad_off_prompt_off
```

## Optional int8_float16 Comparison

Optional reduced-precision run

```bash
python3 evaluation/evaluate.py \
  --model small \
  --device cuda \
  --compute-type int8_float16 \
  --vad on \
  --beam-size 5 \
  --run-label fw_small_int8float16_vad_on_prompt_off
```

## Summary Generation

```bash
python3 evaluation/summarize_results.py
```

This scans:

```text
evaluation/runs/*/summary.json
```

and writes:

```text
evaluation/reports/comparison_summary.csv
evaluation/reports/comparison_summary.md
```

## Manual Review Workflow

Review rows in each run-specific `evaluation/runs/<model>/<run-folder>/inference.csv`.
Use blank values for not reviewed.
Use `false` only when a reviewed item failed.

Manual review labels for `observed_errors` should be semicolon-separated from this list:

```text
company_name_misspelled
company_name_split
company_name_substitution
role_name_wrong
missing_final_sentence
speech_after_pause_missing
truncated_output
hallucination
repeated_text
word_substitution
action_wrong
status_wrong
priority_wrong
next_action_wrong
```
