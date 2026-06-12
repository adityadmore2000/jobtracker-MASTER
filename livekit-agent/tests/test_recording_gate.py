from __future__ import annotations

import json

import pytest
from livekit import rtc

from agent import LiveKitTranscriptionParticipant
from config import Settings


def make_frame(samples_per_channel: int, *, start_value: int = 0, sample_rate: int = 48000, num_channels: int = 1) -> rtc.AudioFrame:
    values = []
    current = start_value
    for _ in range(samples_per_channel * num_channels):
        values.append(int(current).to_bytes(2, byteorder="little", signed=True))
        current += 1
    return rtc.AudioFrame(data=b"".join(values), sample_rate=sample_rate, num_channels=num_channels, samples_per_channel=samples_per_channel)


class FakeLocalParticipant:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, bool]] = []

    async def publish_data(self, payload: bytes, *, reliable: bool = True, destination_identities=None, topic: str = "") -> None:
        self.calls.append((payload, reliable))


class FakeRoom:
    def __init__(self) -> None:
        self.local_participant = FakeLocalParticipant()

    def on(self, _event_name: str):
        def decorator(callback):
            return callback

        return decorator

    def isconnected(self) -> bool:
        return False

    async def disconnect(self) -> None:
        return None


class FakeWhisperAdapter:
    def __init__(self) -> None:
        self.calls: list[bytes] = []

    async def transcribe_utterance(self, wav_bytes: bytes, *, initial_prompt: str | None = None) -> str:
        self.calls.append(wav_bytes)
        return f"transcript-{len(self.calls)}"


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


@pytest.mark.anyio
async def test_frames_are_ignored_before_and_after_active_utterance(settings: Settings):
    participant = LiveKitTranscriptionParticipant(settings, room=FakeRoom(), whisper_adapter=FakeWhisperAdapter())

    await participant._handle_audio_frame(make_frame(4800, start_value=1))
    assert participant._buffer.snapshot_and_reset() is None

    started, warning = await participant._begin_utterance("utt-1")
    assert started is True
    assert warning is None

    await participant._handle_audio_frame(make_frame(4800, start_value=10))
    snapshot, end_warning = await participant._end_utterance("utt-1")
    assert end_warning is None
    assert snapshot is not None
    assert snapshot.total_samples_per_channel == 4800

    await participant._handle_audio_frame(make_frame(4800, start_value=99))
    assert participant._buffer.snapshot_and_reset() is None


@pytest.mark.anyio
async def test_second_utterance_does_not_contain_idle_frames_from_previous_session(settings: Settings):
    whisper_adapter = FakeWhisperAdapter()
    room = FakeRoom()
    participant = LiveKitTranscriptionParticipant(settings, room=room, whisper_adapter=whisper_adapter)

    started, _ = await participant._begin_utterance("utt-1")
    assert started is True
    await participant._handle_audio_frame(make_frame(4800, start_value=1))
    first_snapshot, _ = await participant._end_utterance("utt-1")
    await participant._process_utterance_end("utt-1", first_snapshot)

    await participant._handle_audio_frame(make_frame(9600, start_value=300))

    started, _ = await participant._begin_utterance("utt-2")
    assert started is True
    await participant._handle_audio_frame(make_frame(2400, start_value=500))
    second_snapshot, _ = await participant._end_utterance("utt-2")
    assert second_snapshot is not None
    assert second_snapshot.total_samples_per_channel == 2400

    await participant._process_utterance_end("utt-2", second_snapshot)

    assert len(whisper_adapter.calls) == 2
    assert len(room.local_participant.calls) == 2
    first_payload = json.loads(room.local_participant.calls[0][0].decode("utf-8"))
    second_payload = json.loads(room.local_participant.calls[1][0].decode("utf-8"))
    assert first_payload["utterance_id"] == "utt-1"
    assert second_payload["utterance_id"] == "utt-2"


@pytest.mark.anyio
async def test_stale_end_packet_is_ignored_safely(settings: Settings):
    participant = LiveKitTranscriptionParticipant(settings, room=FakeRoom(), whisper_adapter=FakeWhisperAdapter())

    started, _ = await participant._begin_utterance("utt-1")
    assert started is True
    await participant._handle_audio_frame(make_frame(4800, start_value=1))

    snapshot, warning = await participant._end_utterance("utt-2")
    assert snapshot is None
    assert warning == "stale_utterance_end"

    next_snapshot, next_warning = await participant._end_utterance("utt-1")
    assert next_warning is None
    assert next_snapshot is not None


@pytest.mark.anyio
async def test_duplicate_start_packet_is_ignored_safely(settings: Settings):
    participant = LiveKitTranscriptionParticipant(settings, room=FakeRoom(), whisper_adapter=FakeWhisperAdapter())

    started, warning = await participant._begin_utterance("utt-1")
    assert started is True
    assert warning is None

    duplicate_started, duplicate_warning = await participant._begin_utterance("utt-2")
    assert duplicate_started is False
    assert duplicate_warning == "duplicate_utterance_start"
