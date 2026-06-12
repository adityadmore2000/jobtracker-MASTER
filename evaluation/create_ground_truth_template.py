from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
OUTPUT_FILE = ROOT_DIR / "evaluation" / "ground_truth.csv"
FIELDNAMES = ["test_id", "audio_file", "category", "expected_text", "notes"]


def natural_sort_key(path: Path) -> List[object]:
    parts: List[object] = []
    token = ""

    for char in path.stem:
        if char.isdigit():
            if token and not token[-1].isdigit():
                parts.append(token.lower())
                token = ""
            token += char
        else:
            if token and token[-1].isdigit():
                parts.append(int(token))
                token = ""
            token += char

    if token:
        parts.append(int(token) if token.isdigit() else token.lower())

    parts.append(path.suffix.lower())
    return parts


def discover_wavs() -> List[Path]:
    wavs = [path for path in RAW_DIR.rglob("*.wav") if path.is_file()]
    return sorted(
        wavs,
        key=lambda path: (
            path.parent.relative_to(RAW_DIR).as_posix(),
            natural_sort_key(path),
        ),
    )


def load_existing_rows() -> Dict[str, Dict[str, str]]:
    if not OUTPUT_FILE.exists():
        return {}

    with OUTPUT_FILE.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return {
            row["audio_file"]: {
                "expected_text": row.get("expected_text", ""),
                "notes": row.get("notes", ""),
            }
            for row in reader
            if row.get("audio_file")
        }


def build_test_id(category: str, filename: str) -> str:
    category_id = category.replace("-", "_").replace("/", "_")
    stem = Path(filename).stem
    try:
        index = int(stem)
        suffix = f"{index:02d}"
    except ValueError:
        suffix = stem.replace("-", "_")
    return f"{category_id}_{suffix}"


def main() -> None:
    wav_files = discover_wavs()
    existing_rows = load_existing_rows()
    discovered_audio_files = {
        path.relative_to(DATA_DIR).as_posix() for path in wav_files
    }

    rows = []
    added_rows = 0
    preserved_rows = 0

    for wav_path in wav_files:
        category = wav_path.parent.relative_to(RAW_DIR).as_posix()
        audio_file = wav_path.relative_to(DATA_DIR).as_posix()
        preserved = existing_rows.get(audio_file, {})

        if preserved:
            preserved_rows += 1
        else:
            added_rows += 1

        rows.append(
            {
                "test_id": build_test_id(category, wav_path.name),
                "audio_file": audio_file,
                "category": category,
                "expected_text": preserved.get("expected_text", ""),
                "notes": preserved.get("notes", ""),
            }
        )

    missing_previously_tracked = sorted(
        set(existing_rows) - discovered_audio_files
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Discovered WAV files: {len(wav_files)}")
    print(f"Added rows: {added_rows}")
    print(f"Preserved rows: {preserved_rows}")
    print(f"Missing previously tracked files: {len(missing_previously_tracked)}")
    print("Output: evaluation/ground_truth.csv")


if __name__ == "__main__":
    main()
