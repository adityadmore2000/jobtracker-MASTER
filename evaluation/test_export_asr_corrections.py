from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text

PROJECT_DIR = Path(__file__).resolve().parent.parent / "jobtracker-BE"
if str(PROJECT_DIR) not in sys.path:
    sys.path.append(str(PROJECT_DIR))

from export_asr_corrections import best_effort_corrected_transcript, export_corrections, resolve_database_url
from app import database_config  # noqa: E402

TEST_DATABASE_URL = database_config.get_required_database_url("TEST_DATABASE_URL")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


def alembic_config() -> Config:
    config = Config(str(PROJECT_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_DIR / "alembic"))
    return config


def reset_database() -> None:
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    with engine.begin() as connection:
        table_names = connection.execute(
            text(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY tablename ASC
                """
            )
        ).scalars().all()
        if table_names:
            joined = ", ".join(f'"{table_name}"' for table_name in table_names)
            connection.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
    engine.dispose()


def reset_schema_and_upgrade() -> None:
    engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with engine.connect() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(text("GRANT ALL ON SCHEMA public TO CURRENT_USER"))
        connection.execute(text("GRANT ALL ON SCHEMA public TO public"))
    engine.dispose()
    command.upgrade(alembic_config(), "head")


def seed_correction_events() -> None:
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    with engine.begin() as connection:
        canonical_company_id = connection.execute(
            text(
                """
                INSERT INTO canonical_companies (canonical_name, created_at, updated_at)
                VALUES (:canonical_name, NOW(), NOW())
                RETURNING id
                """
            ),
            {"canonical_name": "Krutrim Labs"},
        ).scalar_one()

        connection.execute(
            text(
                """
                INSERT INTO asr_company_correction_events (
                    raw_transcript,
                    original_extracted_company_name,
                    confirmed_company_name,
                    canonical_company_id,
                    application_id,
                    alias_created,
                    audio_reference,
                    created_at
                ) VALUES
                    (
                        :raw_transcript_1,
                        :original_extracted_company_name_1,
                        :confirmed_company_name_1,
                        :canonical_company_id,
                        NULL,
                        true,
                        :audio_reference_1,
                        TIMESTAMPTZ '2026-06-07 00:00:00+00:00'
                    ),
                    (
                        :raw_transcript_2,
                        :original_extracted_company_name_2,
                        :confirmed_company_name_2,
                        :canonical_company_id,
                        NULL,
                        false,
                        NULL,
                        TIMESTAMPTZ '2026-06-07 01:00:00+00:00'
                    )
                """
            ),
            {
                "canonical_company_id": canonical_company_id,
                "raw_transcript_1": "Add Crew Trim Labs for an AI Engineer role.",
                "original_extracted_company_name_1": "Crew Trim Labs",
                "confirmed_company_name_1": "Krutrim Labs",
                "audio_reference_1": "recordings/session-1.webm",
                "raw_transcript_2": "Add Another Company for an AI Engineer role.",
                "original_extracted_company_name_2": "Another Company",
                "confirmed_company_name_2": "Another Company",
            },
        )
    engine.dispose()


@pytest.fixture(autouse=True)
def reset_backend_env_loader() -> None:
    database_config.reset_backend_environment_cache()
    yield
    database_config.reset_backend_environment_cache()


def test_best_effort_corrected_transcript_replaces_original_company_name() -> None:
    assert (
        best_effort_corrected_transcript(
            "Add Crew Trim Labs for an AI Engineer role.",
            "Crew Trim Labs",
            "Krutrim Labs",
        )
        == "Add Krutrim Labs for an AI Engineer role."
    )


def test_export_corrections_reads_postgresql_rows_and_separates_training_records(tmp_path: Path) -> None:
    reset_schema_and_upgrade()
    reset_database()
    seed_correction_events()

    summary = export_corrections(TEST_DATABASE_URL, tmp_path)

    assert summary["total_corrections"] == 2
    assert summary["training_eligible"] == 1
    assert summary["metadata_only"] == 1

    review_rows = [
        json.loads(line)
        for line in (tmp_path / "corrections_review.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    training_rows = [
        json.loads(line)
        for line in (tmp_path / "training_eligible.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert review_rows[0]["confirmed_company_name"] == "Krutrim Labs"
    assert review_rows[0]["corrected_transcript"] == "Add Krutrim Labs for an AI Engineer role."
    assert review_rows[0]["include_for_training"] is True
    assert review_rows[1]["include_for_training"] is False
    assert review_rows[1]["audio_reference"] == ""
    assert len(training_rows) == 1
    assert training_rows[0]["audio_reference"] == "recordings/session-1.webm"


def test_export_corrections_fails_clearly_when_required_table_is_missing(tmp_path: Path) -> None:
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS asr_company_correction_events CASCADE"))
    engine.dispose()

    try:
        export_corrections(TEST_DATABASE_URL, tmp_path)
    except RuntimeError as exc:
        assert "Run `alembic upgrade head`" in str(exc)
    else:  # pragma: no cover - explicit failure branch for clarity.
        raise AssertionError("Expected a clear failure when the correction table is missing.")
    finally:
        reset_schema_and_upgrade()


def test_exporter_uses_backend_dotenv_when_database_url_is_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    expected_url = "postgresql+psycopg://dotenv:dotenv@localhost:5432/job_tracker"
    env_file.write_text(f"DATABASE_URL={expected_url}\n", encoding="utf-8")

    monkeypatch.setattr(database_config, "get_backend_env_path", lambda: env_file)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert resolve_database_url(None) == expected_url


def test_explicit_exporter_database_url_overrides_backend_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+psycopg://dotenv:dotenv@localhost:5432/job_tracker\n",
        encoding="utf-8",
    )
    explicit_url = "postgresql+psycopg://override:override@localhost:5432/job_tracker"

    monkeypatch.setattr(database_config, "get_backend_env_path", lambda: env_file)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert resolve_database_url(explicit_url) == explicit_url
