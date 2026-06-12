from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RUNS_DIR = ROOT_DIR / "evaluation" / "runs"
DEFAULT_REPORTS_DIR = ROOT_DIR / "evaluation" / "reports"
COMPARISON_FIELDNAMES = [
    "run_label",
    "model",
    "backend",
    "device",
    "compute_type",
    "beam_size",
    "vad_enabled",
    "initial_prompt_enabled",
    "prompt_file",
    "total_samples",
    "model_load_seconds",
    "mean_wer",
    "mean_cer",
    "mean_transcription_seconds_including_first_request",
    "mean_file_real_time_factor_including_first_request",
    "overall_real_time_factor",
]


@dataclass
class RunSummary:
    run_label: str
    model: str
    backend: str
    device: str
    compute_type: str
    beam_size: int
    vad_enabled: bool
    initial_prompt_enabled: bool
    prompt_file: Optional[str]
    total_samples: int
    model_load_seconds: float
    mean_wer: Optional[float]
    mean_cer: Optional[float]
    mean_transcription_seconds_including_first_request: float
    mean_file_real_time_factor_including_first_request: float
    overall_real_time_factor: float
    summary_path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize evaluation/runs/*/summary.json into comparison reports."
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help="Directory containing evaluation run folders.",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=DEFAULT_REPORTS_DIR,
        help="Directory for comparison summary reports.",
    )
    return parser.parse_args()


def read_run_summaries(runs_dir: Path) -> List[RunSummary]:
    if not runs_dir.exists():
        raise FileNotFoundError(f"Missing runs directory: {runs_dir}")

    summary_paths = sorted(runs_dir.glob("*/summary.json"))
    if not summary_paths:
        summary_paths = sorted(runs_dir.glob("*/*/summary.json"))
    if not summary_paths:
        raise FileNotFoundError(f"No summary.json files found under: {runs_dir}")

    summaries: List[RunSummary] = []
    for path in summary_paths:
        data: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        summaries.append(
            RunSummary(
                run_label=data["run_label"],
                model=data["model"],
                backend=data["backend"],
                device=data["device"],
                compute_type=data["compute_type"],
                beam_size=int(data["beam_size"]),
                vad_enabled=bool(data["vad_enabled"]),
                initial_prompt_enabled=bool(data["initial_prompt_enabled"]),
                prompt_file=data.get("prompt_file"),
                total_samples=int(data["total_samples"]),
                model_load_seconds=float(data["model_load_seconds"]),
                mean_wer=(
                    None
                    if data.get("mean_wer") is None
                    else float(data["mean_wer"])
                ),
                mean_cer=(
                    None
                    if data.get("mean_cer") is None
                    else float(data["mean_cer"])
                ),
                mean_transcription_seconds_including_first_request=float(
                    data["mean_transcription_seconds_including_first_request"]
                ),
                mean_file_real_time_factor_including_first_request=float(
                    data["mean_file_real_time_factor_including_first_request"]
                ),
                overall_real_time_factor=float(data["overall_real_time_factor"]),
                summary_path=path,
            )
        )

    return sorted(summaries, key=lambda summary: summary.run_label)


def format_metric(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"


def csv_row(summary: RunSummary) -> Dict[str, str]:
    return {
        "run_label": summary.run_label,
        "model": summary.model,
        "backend": summary.backend,
        "device": summary.device,
        "compute_type": summary.compute_type,
        "beam_size": str(summary.beam_size),
        "vad_enabled": "true" if summary.vad_enabled else "false",
        "initial_prompt_enabled": (
            "true" if summary.initial_prompt_enabled else "false"
        ),
        "prompt_file": summary.prompt_file or "",
        "total_samples": str(summary.total_samples),
        "model_load_seconds": format_metric(summary.model_load_seconds),
        "mean_wer": format_metric(summary.mean_wer),
        "mean_cer": format_metric(summary.mean_cer),
        "mean_transcription_seconds_including_first_request": format_metric(
            summary.mean_transcription_seconds_including_first_request
        ),
        "mean_file_real_time_factor_including_first_request": format_metric(
            summary.mean_file_real_time_factor_including_first_request
        ),
        "overall_real_time_factor": format_metric(summary.overall_real_time_factor),
    }


def write_comparison_csv(path: Path, summaries: List[RunSummary]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COMPARISON_FIELDNAMES)
        writer.writeheader()
        for summary in summaries:
            writer.writerow(csv_row(summary))


def markdown_lines(summaries: List[RunSummary]) -> List[str]:
    lines = [
        "# Comparison Summary",
        "",
        "| Run Label | Model | Backend | Device | Compute Type | Beam Size | VAD | Prompt | Prompt File | Samples | Model Load (s) | Mean WER | Mean CER | Mean Tx (s) | Mean File RTF | Overall RTF |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for summary in summaries:
        lines.append(
            "| "
            + " | ".join(
                [
                    summary.run_label,
                    summary.model,
                    summary.backend,
                    summary.device,
                    summary.compute_type,
                    str(summary.beam_size),
                    "true" if summary.vad_enabled else "false",
                    "true" if summary.initial_prompt_enabled else "false",
                    summary.prompt_file or "",
                    str(summary.total_samples),
                    format_metric(summary.model_load_seconds),
                    format_metric(summary.mean_wer),
                    format_metric(summary.mean_cer),
                    format_metric(
                        summary.mean_transcription_seconds_including_first_request
                    ),
                    format_metric(
                        summary.mean_file_real_time_factor_including_first_request
                    ),
                    format_metric(summary.overall_real_time_factor),
                ]
            )
            + " |"
        )
    lines.extend(
        ["", "Run summaries were loaded from `evaluation/runs/<model>/<run-folder>/summary.json`.", ""]
    )
    return lines


def write_comparison_markdown(path: Path, summaries: List[RunSummary]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(markdown_lines(summaries)), encoding="utf-8")


def print_summary(summary: RunSummary) -> None:
    print(f"run label: {summary.run_label}")
    print(f"  total samples: {summary.total_samples}")
    print(f"  mean WER: {format_metric(summary.mean_wer) or 'blank'}")
    print(f"  mean CER: {format_metric(summary.mean_cer) or 'blank'}")
    print(
        "  mean transcription seconds including first request: "
        f"{format_metric(summary.mean_transcription_seconds_including_first_request)}"
    )
    print(
        "  mean file RTF including first request: "
        f"{format_metric(summary.mean_file_real_time_factor_including_first_request)}"
    )
    print(f"  overall RTF: {format_metric(summary.overall_real_time_factor)}")


def main() -> None:
    args = parse_args()
    summaries = read_run_summaries(args.runs_dir)
    csv_path = args.reports_dir / "comparison_summary.csv"
    md_path = args.reports_dir / "comparison_summary.md"

    write_comparison_csv(csv_path, summaries)
    write_comparison_markdown(md_path, summaries)

    for summary in summaries:
        print_summary(summary)
        print()

    print(f"Comparison CSV: {csv_path}")
    print(f"Comparison Markdown: {md_path}")


if __name__ == "__main__":
    main()
