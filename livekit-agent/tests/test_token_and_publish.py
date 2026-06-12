import json

import pytest
from livekit.api import TokenVerifier

from agent import build_final_transcript_payload, build_transcription_error_payload, create_agent_access_token, publish_final_transcript, publish_transcription_error
from config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        livekit_url="ws://127.0.0.1:7880",
        livekit_api_key="devkey",
        livekit_api_secret="secret",
        livekit_room_name="job-tracker-local",
        backend_url="http://127.0.0.1:8000",
        whisper_service_url="http://127.0.0.1:8100",
    )


def test_agent_token_generation(settings: Settings):
    token = create_agent_access_token(settings)
    claims = TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret).verify(token)

    assert token
    assert claims.identity == "job-tracker-local-agent"
    assert claims.video is not None
    assert claims.video.room == settings.livekit_room_name
    assert claims.video.room_join is True
    assert claims.video.can_subscribe is True
    assert claims.video.can_publish_data is True
    assert claims.video.can_publish is False


class FakeLocalParticipant:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, bool]] = []

    async def publish_data(self, payload: bytes, *, reliable: bool = True, destination_identities=None, topic: str = "") -> None:
        self.calls.append((payload, reliable))


@pytest.mark.anyio
async def test_successful_transcript_publishes_reliable_json():
    participant = FakeLocalParticipant()
    await publish_final_transcript(participant, "utt-1", "hello world")

    assert len(participant.calls) == 1
    payload, reliable = participant.calls[0]
    assert reliable is True
    assert json.loads(payload.decode("utf-8")) == {"type": "final_transcript", "utterance_id": "utt-1", "text": "hello world"}


@pytest.mark.anyio
async def test_failure_publishes_reliable_json():
    participant = FakeLocalParticipant()
    await publish_transcription_error(participant, "utt-2", "Whisper service unavailable.")

    assert len(participant.calls) == 1
    payload, reliable = participant.calls[0]
    assert reliable is True
    assert json.loads(payload.decode("utf-8")) == {"type": "transcription_error", "utterance_id": "utt-2", "message": "Whisper service unavailable."}


def test_payload_builder_helpers_preserve_utterance_id():
    assert json.loads(build_final_transcript_payload("u1", "ok").decode("utf-8"))["utterance_id"] == "u1"
    assert json.loads(build_transcription_error_payload("u2", "bad").decode("utf-8"))["utterance_id"] == "u2"
