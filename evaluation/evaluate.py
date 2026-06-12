from __future__ import annotations

import argparse
import csv
import json
import platform
import re
import shutil
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
EVALUATION_DIR = ROOT_DIR / "evaluation"
DEFAULT_GROUND_TRUTH = EVALUATION_DIR / "ground_truth.csv"
DEFAULT_RUNS_DIR = EVALUATION_DIR / "runs"
GROUND_TRUTH_FIELDNAMES = [
    "test_id",
    "audio_file",
    "category",
    "expected_text",
    "notes",
]
INFERENCE_FIELDNAMES = [
    "run_id",
    "test_id",
    "audio_file",
    "category",
    "model",
    "backend",
    "device",
    "compute_type",
    "vad_enabled",
    "initial_prompt_enabled",
    "is_first_request",
    "audio_duration_seconds",
    "transcription_seconds",
    "real_time_factor",
    "transcribed_text",
    "wer",
    "cer",
    "full_audio_transcribed",
    "final_sentence_present",
    "company_names_correct",
    "role_names_correct",
    "important_fields_correct",
    "hallucination_detected",
    "speech_after_pauses_captured",
    "observed_errors",
    "notes",
]


@dataclass
class GroundTruthRow:
    test_id: str
    audio_file: str
    category: str
    expected_text: str
    notes: str


@dataclass
class EvaluationArgs:
    ground_truth: Path
    runs_dir: Path
    model: str
    device: str
    compute_type: str
    vad_enabled: bool
    beam_size: int
    run_label: str
    prompt_file: Optional[Path]
    category: Optional[str]
    limit: Optional[int]
    allow_cpu_fallback: bool
    overwrite_existing_run: bool


def parse_args() -> EvaluationArgs:
    parser = argparse.ArgumentParser(
        description="Run faster-whisper evaluation and store outputs in a per-run folder."
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=DEFAULT_GROUND_TRUTH,
        help="Path to evaluation/ground_truth.csv",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help="Directory containing evaluation run folders.",
    )
    parser.add_argument("--model", default="small")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument(
        "--vad",
        choices=("on", "off"),
        default="on",
        help="Enable or disable VAD during transcription.",
    )
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--category")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--allow-cpu-fallback", action="store_true")
    parser.add_argument("--overwrite-existing-run", action="store_true")

    parsed = parser.parse_args()
    if parsed.limit is not None and parsed.limit <= 0:
        parser.error("--limit must be greater than 0")
    if parsed.beam_size <= 0:
        parser.error("--beam-size must be greater than 0")

    return EvaluationArgs(
        ground_truth=parsed.ground_truth,
        runs_dir=parsed.runs_dir,
        model=parsed.model,
        device=parsed.device,
        compute_type=parsed.compute_type,
        vad_enabled=parsed.vad == "on",
        beam_size=parsed.beam_size,
        run_label=parsed.run_label,
        prompt_file=parsed.prompt_file,
        category=parsed.category,
        limit=parsed.limit,
        allow_cpu_fallback=parsed.allow_cpu_fallback,
        overwrite_existing_run=parsed.overwrite_existing_run,
    )


def read_ground_truth_rows(path: Path) -> List[GroundTruthRow]:
    if not path.exists():
        raise FileNotFoundError(f"Missing ground-truth CSV: {path}")

    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if (reader.fieldnames or []) != GROUND_TRUTH_FIELDNAMES:
            raise ValueError(
                "Ground-truth CSV must use exactly these columns: "
                "test_id,audio_file,category,expected_text,notes"
            )

        return [
            GroundTruthRow(
                test_id=row["test_id"],
                audio_file=row["audio_file"],
                category=row["category"],
                expected_text=row.get("expected_text", ""),
                notes=row.get("notes", ""),
            )
            for row in reader
        ]


def filter_rows(
    rows: Sequence[GroundTruthRow],
    category: Optional[str],
    limit: Optional[int],
) -> List[GroundTruthRow]:
    filtered = [row for row in rows if category is None or row.category == category]
    if limit is not None:
        filtered = filtered[:limit]
    return filtered


def load_prompt_text(prompt_file: Optional[Path]) -> Optional[str]:
    if prompt_file is None:
        return None
    if not prompt_file.exists():
        raise FileNotFoundError(f"Missing prompt file: {prompt_file}")
    prompt_text = prompt_file.read_text(encoding="utf-8").strip()
    return prompt_text or None


def import_transcription_dependencies() -> Tuple[Any, Any]:
    try:
        import ctranslate2
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper dependencies are unavailable. "
            "Run this command inside the faster-whisper environment."
        ) from exc
    return ctranslate2, WhisperModel


def resolve_device(
    requested_device: str,
    allow_cpu_fallback: bool,
    ctranslate2_module: Any,
) -> str:
    if requested_device != "cuda":
        return requested_device

    cuda_count = ctranslate2_module.get_cuda_device_count()
    if cuda_count >= 1:
        return "cuda"

    if allow_cpu_fallback:
        print(
            "CUDA unavailable; falling back to CPU because --allow-cpu-fallback was passed."
        )
        return "cpu"

    raise RuntimeError(
        "CUDA is unavailable. Re-run with a visible GPU or pass --allow-cpu-fallback."
    )


def load_model(
    whisper_model_class: Any,
    model_name: str,
    device: str,
    compute_type: str,
) -> Tuple[Any, float]:
    load_started = perf_counter()
    model = whisper_model_class(
        model_name,
        device=device,
        compute_type=compute_type,
    )
    return model, perf_counter() - load_started


def audio_duration_seconds(audio_path: Path) -> float:
    with wave.open(str(audio_path), "rb") as wav_file:
        frame_count = wav_file.getnframes()
        sample_rate = wav_file.getframerate()
        return frame_count / sample_rate


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def levenshtein_distance(seq_a: Sequence[str], seq_b: Sequence[str]) -> int:
    if not seq_a:
        return len(seq_b)
    if not seq_b:
        return len(seq_a)

    previous = list(range(len(seq_b) + 1))
    for index_a, item_a in enumerate(seq_a, start=1):
        current = [index_a]
        for index_b, item_b in enumerate(seq_b, start=1):
            insertion = current[index_b - 1] + 1
            deletion = previous[index_b] + 1
            substitution = previous[index_b - 1] + (0 if item_a == item_b else 1)
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]


def compute_wer(expected_text: str, transcribed_text: str) -> Optional[float]:
    normalized_expected = normalize_text(expected_text)
    if not normalized_expected:
        return None

    expected_tokens = normalized_expected.split()
    transcribed_tokens = normalize_text(transcribed_text).split()
    distance = levenshtein_distance(expected_tokens, transcribed_tokens)
    return distance / len(expected_tokens)


def compute_cer(expected_text: str, transcribed_text: str) -> Optional[float]:
    normalized_expected = normalize_text(expected_text)
    if not normalized_expected:
        return None

    expected_chars = list(normalized_expected)
    transcribed_chars = list(normalize_text(transcribed_text))
    distance = levenshtein_distance(expected_chars, transcribed_chars)
    return distance / len(expected_chars)


def format_metric(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"


def format_seconds(value: float) -> str:
    return f"{value:.6f}"


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def package_versions() -> Dict[str, str]:
    versions: Dict[str, str] = {}
    for package_name, display_name in (
        ("faster-whisper", "faster-whisper"),
        ("ctranslate2", "ctranslate2"),
        ("av", "av"),
    ):
        try:
            versions[display_name] = metadata.version(package_name)
        except metadata.PackageNotFoundError:
            versions[display_name] = "unavailable"
    versions["Python"] = platform.python_version()
    return versions


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def summary_value(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return mean(values)


def transcription_text(segments: Iterable[Any]) -> str:
    return " ".join(
        segment.text.strip()
        for segment in segments
        if segment.text.strip()
    ).strip()


def run_directory(args: EvaluationArgs) -> Path:
    return args.runs_dir / args.model / run_folder_name(args.run_label, args.model)


def run_folder_name(run_label: str, model: str) -> str:
    prefix = f"fw_{model}_"
    if run_label.startswith(prefix):
        return f"fw_{run_label[len(prefix):]}"
    return run_label


def relative_to_repo(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return str(path)


def prepare_run_directory(path: Path, overwrite: bool) -> None:
    if path.exists():
        if not overwrite:
            raise FileExistsError(
                f"Run folder already exists: {path}. Pass --overwrite-existing-run to replace it."
            )
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=False)


def write_run_inference_csv(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=INFERENCE_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def write_config_json(
    path: Path,
    *,
    args: EvaluationArgs,
    created_at: str,
    prompt_text: Optional[str],
    total_samples: int,
) -> None:
    config = {
        "run_label": args.run_label,
        "created_at": created_at,
        "model": args.model,
        "backend": "faster-whisper",
        "device": args.device,
        "compute_type": args.compute_type,
        "beam_size": args.beam_size,
        "vad_enabled": args.vad_enabled,
        "initial_prompt_enabled": prompt_text is not None,
        "prompt_file": (
            relative_to_repo(args.prompt_file) if args.prompt_file is not None else None
        ),
        "ground_truth_file": "evaluation/ground_truth.csv",
        "ground_truth_version": "v1",
        "total_samples": total_samples,
    }
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def write_summary_json(
    path: Path,
    *,
    args: EvaluationArgs,
    device_used: str,
    prompt_text: Optional[str],
    model_load_seconds: float,
    started_at: str,
    completed_at: str,
    result_rows: Sequence[Dict[str, str]],
) -> None:
    total_audio = sum(float(row["audio_duration_seconds"]) for row in result_rows)
    total_transcription = sum(float(row["transcription_seconds"]) for row in result_rows)
    file_rtfs = [float(row["real_time_factor"]) for row in result_rows]
    file_times = [float(row["transcription_seconds"]) for row in result_rows]
    excluding_first = [
        row for row in result_rows if row["is_first_request"] == "false"
    ]
    excluding_first_rtfs = [float(row["real_time_factor"]) for row in excluding_first]
    excluding_first_times = [
        float(row["transcription_seconds"]) for row in excluding_first
    ]
    wers = [float(row["wer"]) for row in result_rows if row["wer"]]
    cers = [float(row["cer"]) for row in result_rows if row["cer"]]

    summary = {
        "run_label": args.run_label,
        "model": args.model,
        "backend": "faster-whisper",
        "device": device_used,
        "compute_type": args.compute_type,
        "beam_size": args.beam_size,
        "vad_enabled": args.vad_enabled,
        "initial_prompt_enabled": prompt_text is not None,
        "prompt_file": (
            relative_to_repo(args.prompt_file) if args.prompt_file is not None else None
        ),
        "model_load_seconds": model_load_seconds,
        "total_samples": len(result_rows),
        "total_audio_duration_seconds": total_audio,
        "total_transcription_seconds": total_transcription,
        "overall_real_time_factor": (
            total_transcription / total_audio if total_audio else 0.0
        ),
        "mean_file_real_time_factor_including_first_request": (
            summary_value(file_rtfs) or 0.0
        ),
        "mean_file_real_time_factor_excluding_first_request": summary_value(
            excluding_first_rtfs
        ),
        "mean_transcription_seconds_including_first_request": (
            summary_value(file_times) or 0.0
        ),
        "mean_transcription_seconds_excluding_first_request": summary_value(
            excluding_first_times
        ),
        "mean_wer": summary_value(wers),
        "mean_cer": summary_value(cers),
        "package_versions": package_versions(),
        "started_at": started_at,
        "completed_at": completed_at,
    }
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def evaluate_rows(args: EvaluationArgs) -> Tuple[List[Dict[str, str]], Path]:
    rows = read_ground_truth_rows(args.ground_truth)
    selected_rows = filter_rows(rows, args.category, args.limit)
    if not selected_rows:
        raise ValueError("No matching rows found in ground-truth CSV.")

    prompt_text = load_prompt_text(args.prompt_file)
    target_run_dir = run_directory(args)
    prepare_run_directory(target_run_dir, args.overwrite_existing_run)

    ctranslate2_module, whisper_model_class = import_transcription_dependencies()
    device_used = resolve_device(args.device, args.allow_cpu_fallback, ctranslate2_module)
    model, model_load_seconds = load_model(
        whisper_model_class, args.model, device_used, args.compute_type
    )

    started_at = iso_now()
    result_rows: List[Dict[str, str]] = []

    for index, row in enumerate(selected_rows, start=1):
        audio_path = DATA_DIR / row.audio_file
        if not audio_path.exists():
            raise FileNotFoundError(
                f"Missing audio file referenced by ground truth: {audio_path}"
            )

        duration_seconds = audio_duration_seconds(audio_path)
        transcription_started = perf_counter()
        segments, _info = model.transcribe(
            str(audio_path),
            language="en",
            task="transcribe",
            beam_size=args.beam_size,
            vad_filter=args.vad_enabled,
            initial_prompt=prompt_text,
        )
        segments = list(segments)
        transcription_seconds = perf_counter() - transcription_started
        rtf = transcription_seconds / duration_seconds if duration_seconds else 0.0
        text = transcription_text(segments)
        wer = compute_wer(row.expected_text, text)
        cer = compute_cer(row.expected_text, text)
        is_first_request = index == 1
        run_id = f"{args.run_label}__{row.test_id}"

        result_rows.append(
            {
                "run_id": run_id,
                "test_id": row.test_id,
                "audio_file": row.audio_file,
                "category": row.category,
                "model": args.model,
                "backend": "faster-whisper",
                "device": device_used,
                "compute_type": args.compute_type,
                "vad_enabled": bool_text(args.vad_enabled),
                "initial_prompt_enabled": bool_text(prompt_text is not None),
                "is_first_request": bool_text(is_first_request),
                "audio_duration_seconds": format_seconds(duration_seconds),
                "transcription_seconds": format_seconds(transcription_seconds),
                "real_time_factor": format_seconds(rtf),
                "transcribed_text": text,
                "wer": format_metric(wer),
                "cer": format_metric(cer),
                "full_audio_transcribed": "",
                "final_sentence_present": "",
                "company_names_correct": "",
                "role_names_correct": "",
                "important_fields_correct": "",
                "hallucination_detected": "",
                "speech_after_pauses_captured": "",
                "observed_errors": "",
                "notes": "",
            }
        )

        print(
            f"[{index}/{len(selected_rows)}] {row.test_id} | "
            f"audio={duration_seconds:.3f}s | "
            f"inference={transcription_seconds:.3f}s | "
            f"RTF={rtf:.4f}"
        )

    inference_path = target_run_dir / "inference.csv"
    summary_path = target_run_dir / "summary.json"
    config_path = target_run_dir / "config.json"

    write_run_inference_csv(inference_path, result_rows)
    completed_at = iso_now()
    write_summary_json(
        summary_path,
        args=args,
        device_used=device_used,
        prompt_text=prompt_text,
        model_load_seconds=model_load_seconds,
        started_at=started_at,
        completed_at=completed_at,
        result_rows=result_rows,
    )
    write_config_json(
        config_path,
        args=args,
        created_at=started_at,
        prompt_text=prompt_text,
        total_samples=len(result_rows),
    )
    return result_rows, target_run_dir


def main() -> None:
    args = parse_args()
    _, run_dir = evaluate_rows(args)
    print(f"Run directory: {run_dir}")
    print(f"Inference CSV: {run_dir / 'inference.csv'}")
    print(f"Summary JSON: {run_dir / 'summary.json'}")
    print(f"Config JSON: {run_dir / 'config.json'}")


if __name__ == "__main__":
    main()
