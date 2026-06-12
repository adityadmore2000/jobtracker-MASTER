from pathlib import Path

import pytest

from config import (
    AgentConfigurationError,
    DEFAULT_LIVEKIT_ROOM_NAME,
    DEFAULT_WHISPER_REQUEST_TIMEOUT_SECONDS,
    get_settings,
    reset_agent_environment_cache,
)


@pytest.fixture(autouse=True)
def reset_env(monkeypatch: pytest.MonkeyPatch):
    for name in [
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "LIVEKIT_ROOM_NAME",
        "JOBTRACKER_BACKEND_URL",
        "WHISPER_SERVICE_URL",
        "WHISPER_REQUEST_TIMEOUT_SECONDS",
    ]:
        monkeypatch.delenv(name, raising=False)
    reset_agent_environment_cache()
    yield
    reset_agent_environment_cache()


def write_env(path: Path, content: str) -> None:
    path.write_text(content)


def test_required_settings_are_loaded(tmp_path: Path):
    env_path = tmp_path / ".env"
    write_env(
        env_path,
        "\n".join(
            [
                "LIVEKIT_URL=ws://127.0.0.1:7880",
                "LIVEKIT_API_KEY=devkey",
                "LIVEKIT_API_SECRET=secret",
                "LIVEKIT_ROOM_NAME=job-tracker-local",
                "JOBTRACKER_BACKEND_URL=http://127.0.0.1:8000",
                "WHISPER_SERVICE_URL=http://127.0.0.1:8100",
            ]
        ),
    )

    settings = get_settings(env_path=env_path)

    assert settings.livekit_url == "ws://127.0.0.1:7880"
    assert settings.livekit_room_name == "job-tracker-local"
    assert settings.backend_url == "http://127.0.0.1:8000"
    assert settings.whisper_service_url == "http://127.0.0.1:8100"
    assert settings.whisper_request_timeout_seconds == DEFAULT_WHISPER_REQUEST_TIMEOUT_SECONDS


def test_os_environment_overrides_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    env_path = tmp_path / ".env"
    write_env(
        env_path,
        "\n".join(
            [
                "LIVEKIT_URL=ws://127.0.0.1:7880",
                "LIVEKIT_API_KEY=dotenv-key",
                "LIVEKIT_API_SECRET=dotenv-secret",
                "JOBTRACKER_BACKEND_URL=http://127.0.0.1:8000",
                "WHISPER_SERVICE_URL=http://127.0.0.1:8100",
                "WHISPER_REQUEST_TIMEOUT_SECONDS=120",
            ]
        ),
    )
    monkeypatch.setenv("LIVEKIT_API_KEY", "os-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "os-secret")
    monkeypatch.setenv("WHISPER_REQUEST_TIMEOUT_SECONDS", "45")

    settings = get_settings(env_path=env_path)

    assert settings.livekit_api_key == "os-key"
    assert settings.livekit_api_secret == "os-secret"
    assert settings.livekit_room_name == DEFAULT_LIVEKIT_ROOM_NAME
    assert settings.whisper_request_timeout_seconds == 45.0


def test_whisper_timeout_value_is_loaded_from_dotenv(tmp_path: Path):
    env_path = tmp_path / ".env"
    write_env(
        env_path,
        "\n".join(
            [
                "LIVEKIT_URL=ws://127.0.0.1:7880",
                "LIVEKIT_API_KEY=devkey",
                "LIVEKIT_API_SECRET=secret",
                "JOBTRACKER_BACKEND_URL=http://127.0.0.1:8000",
                "WHISPER_SERVICE_URL=http://127.0.0.1:8100",
                "WHISPER_REQUEST_TIMEOUT_SECONDS=150",
            ]
        ),
    )

    settings = get_settings(env_path=env_path)

    assert settings.whisper_request_timeout_seconds == 150.0


@pytest.mark.parametrize("raw_value", ["0", "-1", "abc"])
def test_invalid_whisper_timeout_rejection(tmp_path: Path, raw_value: str):
    env_path = tmp_path / ".env"
    write_env(
        env_path,
        "\n".join(
            [
                "LIVEKIT_URL=ws://127.0.0.1:7880",
                "LIVEKIT_API_KEY=devkey",
                "LIVEKIT_API_SECRET=secret",
                "JOBTRACKER_BACKEND_URL=http://127.0.0.1:8000",
                "WHISPER_SERVICE_URL=http://127.0.0.1:8100",
                f"WHISPER_REQUEST_TIMEOUT_SECONDS={raw_value}",
            ]
        ),
    )

    with pytest.raises(AgentConfigurationError) as caught:
        get_settings(env_path=env_path)

    assert "WHISPER_REQUEST_TIMEOUT_SECONDS" in str(caught.value)


@pytest.mark.parametrize("missing_name", ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "JOBTRACKER_BACKEND_URL", "WHISPER_SERVICE_URL"])
def test_missing_or_blank_required_values_fail_safely(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing_name: str):
    env_path = tmp_path / ".env"
    values = {
        "LIVEKIT_URL": "ws://127.0.0.1:7880",
        "LIVEKIT_API_KEY": "devkey",
        "LIVEKIT_API_SECRET": "secret",
        "JOBTRACKER_BACKEND_URL": "http://127.0.0.1:8000",
        "WHISPER_SERVICE_URL": "http://127.0.0.1:8100",
    }
    values[missing_name] = "   "
    write_env(env_path, "\n".join(f"{key}={value}" for key, value in values.items()))

    with pytest.raises(AgentConfigurationError) as caught:
        get_settings(env_path=env_path)

    message = str(caught.value)
    assert missing_name in message
    assert "secret" not in message.replace("LIVEKIT_API_SECRET", "")
