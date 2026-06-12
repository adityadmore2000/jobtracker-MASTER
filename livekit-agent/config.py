from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_LIVEKIT_ROOM_NAME = "job-tracker-local"
DEFAULT_AGENT_IDENTITY = "job-tracker-local-agent"
AGENT_TOKEN_TTL = timedelta(hours=1)
DEFAULT_AUDIO_SAMPLE_RATE = 48000
DEFAULT_AUDIO_CHANNELS = 1
DEFAULT_HTTP_TIMEOUT_SECONDS = 20.0
DEFAULT_WHISPER_REQUEST_TIMEOUT_SECONDS = 120.0


class AgentConfigurationError(RuntimeError):
    """Raised when required agent settings are missing."""


@dataclass(frozen=True)
class Settings:
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str
    livekit_room_name: str
    backend_url: str
    whisper_service_url: str
    agent_identity: str = DEFAULT_AGENT_IDENTITY
    agent_token_ttl: timedelta = AGENT_TOKEN_TTL
    audio_sample_rate: int = DEFAULT_AUDIO_SAMPLE_RATE
    audio_channels: int = DEFAULT_AUDIO_CHANNELS
    http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS
    whisper_request_timeout_seconds: float = DEFAULT_WHISPER_REQUEST_TIMEOUT_SECONDS


def get_agent_root() -> Path:
    return Path(__file__).resolve().parent


def get_agent_env_path() -> Path:
    return get_agent_root() / ".env"


def _load_environment_cached(env_path_str: str) -> str | None:
    env_path = Path(env_path_str)
    if not env_path.exists():
        return None
    load_dotenv(dotenv_path=env_path, override=False)
    return str(env_path)


_load_environment_cached = lru_cache(maxsize=None)(_load_environment_cached)


def load_agent_environment(env_path: Path | None = None) -> Path | None:
    target = env_path or get_agent_env_path()
    loaded = _load_environment_cached(str(target))
    return Path(loaded) if loaded else None


def reset_agent_environment_cache() -> None:
    _load_environment_cached.cache_clear()


def _get_env_value(name: str, *, env_path: Path | None = None) -> str:
    load_agent_environment(env_path)
    return os.getenv(name, "").strip()


def _get_positive_float_env_value(name: str, default: float, *, env_path: Path | None = None) -> float:
    raw_value = _get_env_value(name, env_path=env_path)
    if not raw_value:
        return default

    try:
        parsed = float(raw_value)
    except ValueError as exc:
        raise AgentConfigurationError(f"LiveKit agent configuration for {name} must be a positive number.") from exc

    if parsed <= 0:
        raise AgentConfigurationError(f"LiveKit agent configuration for {name} must be a positive number.")

    return parsed


def get_settings(*, env_path: Path | None = None) -> Settings:
    values = {
        "LIVEKIT_URL": _get_env_value("LIVEKIT_URL", env_path=env_path),
        "LIVEKIT_API_KEY": _get_env_value("LIVEKIT_API_KEY", env_path=env_path),
        "LIVEKIT_API_SECRET": _get_env_value("LIVEKIT_API_SECRET", env_path=env_path),
        "LIVEKIT_ROOM_NAME": _get_env_value("LIVEKIT_ROOM_NAME", env_path=env_path) or DEFAULT_LIVEKIT_ROOM_NAME,
        "JOBTRACKER_BACKEND_URL": _get_env_value("JOBTRACKER_BACKEND_URL", env_path=env_path),
        "WHISPER_SERVICE_URL": _get_env_value("WHISPER_SERVICE_URL", env_path=env_path),
        "WHISPER_REQUEST_TIMEOUT_SECONDS": _get_positive_float_env_value(
            "WHISPER_REQUEST_TIMEOUT_SECONDS",
            DEFAULT_WHISPER_REQUEST_TIMEOUT_SECONDS,
            env_path=env_path,
        ),
    }
    missing = [name for name in ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "JOBTRACKER_BACKEND_URL", "WHISPER_SERVICE_URL"] if not values[name]]
    if missing:
        raise AgentConfigurationError(
            "LiveKit agent configuration is incomplete. Set "
            + ", ".join(missing)
            + " in the OS environment or livekit-agent/.env."
        )

    return Settings(
        livekit_url=values["LIVEKIT_URL"],
        livekit_api_key=values["LIVEKIT_API_KEY"],
        livekit_api_secret=values["LIVEKIT_API_SECRET"],
        livekit_room_name=values["LIVEKIT_ROOM_NAME"],
        backend_url=values["JOBTRACKER_BACKEND_URL"].rstrip("/"),
        whisper_service_url=values["WHISPER_SERVICE_URL"].rstrip("/"),
        whisper_request_timeout_seconds=values["WHISPER_REQUEST_TIMEOUT_SECONDS"],
    )
