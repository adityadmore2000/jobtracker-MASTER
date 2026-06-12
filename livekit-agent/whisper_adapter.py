from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from config import Settings


class WhisperAdapterError(RuntimeError):
    """Raised for recoverable backend or Whisper failures."""


class WhisperAdapter:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport
        self._logger = logging.getLogger("livekit_agent.whisper_adapter")

    async def fetch_hotwords(self) -> list[str]:
        self._logger.info("hotword_fetch_start backend_url=%s", self._settings.backend_url)
        try:
            async with httpx.AsyncClient(timeout=self._settings.http_timeout_seconds, transport=self._transport) as client:
                response = await client.get(f"{self._settings.backend_url}/asr/hotwords")
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise WhisperAdapterError("Job Tracker backend hotword request timed out.") from exc
        except httpx.HTTPError as exc:
            raise WhisperAdapterError("Job Tracker backend hotword request failed.") from exc

        payload = response.json()
        hotwords = payload.get("hotwords")
        if not isinstance(hotwords, list) or any(not isinstance(item, str) for item in hotwords):
            raise WhisperAdapterError("Job Tracker backend returned an invalid hotword response.")

        self._logger.info("hotword_fetch_success hotword_count=%s", len(hotwords))
        return hotwords

    async def transcribe_utterance(self, wav_bytes: bytes, *, initial_prompt: str | None = None) -> str:
        hotwords: list[str] = []
        try:
            hotwords = await self.fetch_hotwords()
        except WhisperAdapterError:
            self._logger.warning("hotword_fetch_failure fallback=without_hotwords", exc_info=True)

        files = {
            "file": (
                "utterance.wav",
                wav_bytes,
                "audio/wav",
            )
        }
        data: dict[str, Any] = {}
        if hotwords:
            data["hotwords"] = hotwords
        if initial_prompt:
            data["initial_prompt"] = initial_prompt

        self._logger.info(
            "whisper_request_start whisper_url=%s hotword_count=%s timeout_seconds=%s",
            self._settings.whisper_service_url,
            len(hotwords),
            self._settings.whisper_request_timeout_seconds,
        )
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._settings.whisper_request_timeout_seconds, transport=self._transport) as client:
                response = await client.post(f"{self._settings.whisper_service_url}/transcribe", files=files, data=data)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            duration_seconds = time.perf_counter() - started
            self._logger.warning(
                "whisper_request_failure timeout_seconds=%s duration_seconds=%.3f error=%r",
                self._settings.whisper_request_timeout_seconds,
                duration_seconds,
                exc,
                exc_info=True,
            )
            raise WhisperAdapterError("Whisper service request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            duration_seconds = time.perf_counter() - started
            response_text = exc.response.text
            self._logger.warning(
                "whisper_request_failed timeout_seconds=%s duration_seconds=%.3f status_code=%s response_body=%s",
                self._settings.whisper_request_timeout_seconds,
                duration_seconds,
                exc.response.status_code,
                response_text[:1000],
            )
            if exc.response.status_code == 422:
                raise WhisperAdapterError("Whisper service rejected the transcription request.") from exc
            raise WhisperAdapterError("Whisper service unavailable.") from exc
        except httpx.HTTPError as exc:
            duration_seconds = time.perf_counter() - started
            self._logger.warning(
                "whisper_request_failure timeout_seconds=%s duration_seconds=%.3f error=%r",
                self._settings.whisper_request_timeout_seconds,
                duration_seconds,
                exc,
                exc_info=True,
            )
            raise WhisperAdapterError("Whisper service unavailable.") from exc

        payload = response.json()
        transcript = payload.get("transcript")
        if not isinstance(transcript, str) or not transcript.strip():
            raise WhisperAdapterError("Whisper service returned an invalid transcript response.")

        duration_seconds = time.perf_counter() - started
        self._logger.info(
            "whisper_request_success timeout_seconds=%s duration_seconds=%.3f transcript_chars=%s",
            self._settings.whisper_request_timeout_seconds,
            duration_seconds,
            len(transcript),
        )
        return transcript.strip()
