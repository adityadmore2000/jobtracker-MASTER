from __future__ import annotations

import argparse
import csv
import json
import os
import re
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import create_engine, inspect, text


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT_DIR / "evaluation" / "exports" / "latest"
BACKEND_DIR = ROOT_DIR / "jobtracker-BE"
if str(BACKEND_DIR) not in sys.path:
    sys.path.append(str(BACKEND_DIR))

from app.database_config import get_required_database_url, validate_postgresql_database_url  # noqa: E402


METADATA_FIELDNAMES = [
    "audio_reference",
    "raw_transcript",
    "corrected_transcript",
    "original_extracted_company_name",
    "confirmed_company_name",
    "created_at",
    "include_for_training",
    "notes",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export ASR company correction events into review and training-ready datasets."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL database URL. Defaults to DATABASE_URL from the OS environment or jobtracker-BE/.env.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where export files should be written.",
    )
    return parser.parse_args()


def resolve_database_url(explicit_database_url: str | None) -> str:
    if explicit_database_url:
        return validate_postgresql_database_url(explicit_database_url, env_var="--database-url")
    return get_required_database_url("DATABASE_URL")


def best_effort_corrected_transcript(
    raw_transcript: str,
    original_extracted_company_name: str,
    confirmed_company_name: str,
) -> str:
    raw = raw_transcript.strip()
    original = original_extracted_company_name.strip()
    confirmed = confirmed_company_name.strip()
    if not raw or not original or not confirmed:
        return ""
    if original.casefold() == confirmed.casefold():
        return raw

    pattern = re.compile(re.escape(original), flags=re.IGNORECASE)
    corrected, replacements = pattern.subn(confirmed, raw, count=1)
    return corrected if replacements else ""


def fetch_correction_rows(database_url: str) -> list[dict[str, Any]]:
    engine = create_engine(database_url, pool_pre_ping=True)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "asr_company_correction_events" not in table_names:
        engine.dispose()
        raise RuntimeError(
            "Missing required table 'asr_company_correction_events'. Run `alembic upgrade head` against the PostgreSQL database first."
        )

    query = text(
        """
        SELECT
            audio_reference,
            raw_transcript,
            original_extracted_company_name,
            confirmed_company_name,
            created_at
        FROM asr_company_correction_events
        ORDER BY created_at ASC, id ASC
        """
    )

    with engine.connect() as connection:
        rows = connection.execute(query).mappings().all()
    engine.dispose()

    exports: list[dict[str, Any]] = []
    for row in rows:
        corrected_transcript = best_effort_corrected_transcript(
            row["raw_transcript"] or "",
            row["original_extracted_company_name"] or "",
            row["confirmed_company_name"] or "",
        )
        include_for_training = bool(
            (row["audio_reference"] or "").strip()
            and (row["raw_transcript"] or "").strip()
            and corrected_transcript
        )
        exports.append(
            {
                "audio_reference": row["audio_reference"] or "",
                "raw_transcript": row["raw_transcript"] or "",
                "corrected_transcript": corrected_transcript,
                "original_extracted_company_name": row["original_extracted_company_name"] or "",
                "confirmed_company_name": row["confirmed_company_name"] or "",
                "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                "include_for_training": include_for_training,
                "notes": "",
            }
        )
    return exports


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=METADATA_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def export_corrections(
    database_url: str,
    output_dir: Path,
    *,
    database_url_provided: bool = True,
) -> dict[str, Any]:
    rows = fetch_correction_rows(database_url)
    training_rows = [row for row in rows if row["include_for_training"]]
    metadata_only_rows = [row for row in rows if not row["include_for_training"]]

    review_jsonl_path = output_dir / "corrections_review.jsonl"
    review_csv_path = output_dir / "corrections_review.csv"
    training_jsonl_path = output_dir / "training_eligible.jsonl"
    summary_path = output_dir / "export_summary.json"

    write_jsonl(review_jsonl_path, rows)
    write_csv(review_csv_path, rows)
    write_jsonl(training_jsonl_path, training_rows)

    summary = {
        "database_url_env": "DATABASE_URL",
        "database_url_provided": database_url_provided,
        "total_corrections": len(rows),
        "training_eligible": len(training_rows),
        "metadata_only": len(metadata_only_rows),
        "review_jsonl": str(review_jsonl_path),
        "review_csv": str(review_csv_path),
        "training_jsonl": str(training_jsonl_path),
        "notes": (
            "Training-ready export excludes rows without audio_reference or without a deterministic corrected_transcript. "
            "Periodic fine-tuning is not triggered automatically."
        ),
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    args = parse_args()
    database_url = resolve_database_url(args.database_url)
    summary = export_corrections(
        database_url,
        args.output_dir,
        database_url_provided=bool(args.database_url),
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
