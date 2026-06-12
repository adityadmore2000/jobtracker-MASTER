import json

import httpx
import pytest

from config import Settings
from whisper_adapter import WhisperAdapter, WhisperAdapterError


@pytest.fixture
def settings() -> Settings:
    return Settings(
        livekit_url="ws://127.0.0.1:7880",
        livekit_api_key="devkey",
        livekit_api_secret="secret",
        livekit_room_name="job-tracker-local",
        backend_url="http://127.0.0.1:8000",
        whisper_service_url="http://127.0.0.1:8100",
        whisper_request_timeout_seconds=120.0,
    )


@pytest.mark.anyio
async def test_hotwords_fetched_before_every_transcription_and_contract_matches(settings: Settings):
    calls: list[tuple[str, str, dict[str, str], bytes, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            calls.append(("GET", str(request.url), {}, b"", {}))
            return httpx.Response(200, json={"hotwords": ["Neilsoft", "Analytics Vidhya"], "limit": 100})
        body = request.read()
        calls.append(("POST", str(request.url), dict(request.headers), body, request.extensions))
        return httpx.Response(200, json={"transcript": "hello world"})

    adapter = WhisperAdapter(settings, transport=httpx.MockTransport(handler))

    transcript = await adapter.transcribe_utterance(b"RIFF....fakewav")

    assert transcript == "hello world"
    assert calls[0][0] == "GET"
    assert calls[0][1] == "http://127.0.0.1:8000/asr/hotwords"
    assert calls[1][0] == "POST"
    assert calls[1][1] == "http://127.0.0.1:8100/transcribe"
    assert "application/json" not in calls[1][2].get("content-type", "")
    assert b'name="file"; filename="utterance.wav"' in calls[1][3]
    assert b'Content-Type: audio/wav' in calls[1][3]
    assert b'name="hotwords"' in calls[1][3]
    assert b"Neilsoft" in calls[1][3]
    assert b"Analytics Vidhya" in calls[1][3]
    assert calls[1][3].count(b'name="hotwords"') == 2
    assert calls[1][4]["timeout"]["read"] == 120.0


@pytest.mark.anyio
async def test_initial_prompt_is_appended_only_when_present(settings: Settings):
    calls: list[tuple[str, dict[str, str], bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"hotwords": ["Neilsoft"], "limit": 100})
        body = request.read()
        calls.append((dict(request.headers), body))
        return httpx.Response(200, json={"transcript": "hello world"})

    adapter = WhisperAdapter(settings, transport=httpx.MockTransport(handler))

    transcript = await adapter.transcribe_utterance(b"RIFF....fakewav", initial_prompt="tracker vocabulary")

    assert transcript == "hello world"
    assert "multipart/form-data" in calls[0][0]["content-type"]
    assert b'name="initial_prompt"' in calls[0][1]
    assert b"tracker vocabulary" in calls[0][1]


@pytest.mark.anyio
async def test_initial_prompt_is_omitted_when_absent(settings: Settings):
    calls: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"hotwords": [], "limit": 100})
        body = request.read()
        calls.append(body)
        return httpx.Response(200, json={"transcript": "hello world"})

    adapter = WhisperAdapter(settings, transport=httpx.MockTransport(handler))

    transcript = await adapter.transcribe_utterance(b"RIFF....fakewav")

    assert transcript == "hello world"
    assert b'name="initial_prompt"' not in calls[0]


@pytest.mark.anyio
async def test_hotword_fetch_failure_falls_back_to_transcription_without_hotwords(settings: Settings):
    calls: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            calls.append(("GET", str(request.url), b""))
            return httpx.Response(503, json={"detail": "down"})
        body = request.read()
        calls.append(("POST", str(request.url), body))
        return httpx.Response(200, json={"transcript": "fallback transcript"})

    adapter = WhisperAdapter(settings, transport=httpx.MockTransport(handler))

    transcript = await adapter.transcribe_utterance(b"RIFF....fakewav")

    assert transcript == "fallback transcript"
    assert calls[0][0] == "GET"
    assert calls[1][0] == "POST"
    assert b'name="hotwords"' not in calls[1][2]


@pytest.mark.anyio
async def test_whisper_unavailability_produces_safe_error(settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"hotwords": [], "limit": 100})
        return httpx.Response(503, json={"detail": "gpu down"})

    adapter = WhisperAdapter(settings, transport=httpx.MockTransport(handler))

    with pytest.raises(WhisperAdapterError) as caught:
        await adapter.transcribe_utterance(b"RIFF....fakewav")

    assert str(caught.value) == "Whisper service unavailable."


@pytest.mark.anyio
async def test_whisper_422_is_mapped_to_safe_validation_error(settings: Settings, caplog: pytest.LogCaptureFixture):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"hotwords": [], "limit": 100})
        return httpx.Response(422, json={"detail": [{"loc": ["body", "file"], "msg": "Field required"}]})

    adapter = WhisperAdapter(settings, transport=httpx.MockTransport(handler))

    with caplog.at_level("WARNING", logger="livekit_agent.whisper_adapter"):
        with pytest.raises(WhisperAdapterError) as caught:
            await adapter.transcribe_utterance(b"RIFF....fakewav")

    assert str(caught.value) == "Whisper service rejected the transcription request."
    assert "status_code=422" in caplog.text
    assert '"Field required"' in caplog.text


@pytest.mark.anyio
async def test_whisper_timeout_produces_safe_error(settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"hotwords": [], "limit": 100})
        raise httpx.TimeoutException("timeout")

    adapter = WhisperAdapter(settings, transport=httpx.MockTransport(handler))

    with pytest.raises(WhisperAdapterError) as caught:
        await adapter.transcribe_utterance(b"RIFF....fakewav")

    assert str(caught.value) == "Whisper service request timed out."


@pytest.mark.anyio
async def test_invalid_whisper_response_produces_safe_error(settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"hotwords": [], "limit": 100})
        return httpx.Response(200, json={"wrong": "shape"})

    adapter = WhisperAdapter(settings, transport=httpx.MockTransport(handler))

    with pytest.raises(WhisperAdapterError) as caught:
        await adapter.transcribe_utterance(b"RIFF....fakewav")

    assert str(caught.value) == "Whisper service returned an invalid transcript response."
