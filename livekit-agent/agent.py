from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import signal
import sys
import time
from typing import Any

from livekit import rtc
from livekit.api import AccessToken, VideoGrants

from audio_buffer import AudioBuffer
from config import DEFAULT_AGENT_IDENTITY, Settings, get_settings
from whisper_adapter import WhisperAdapter, WhisperAdapterError


logger = logging.getLogger("livekit_agent")
UTTERANCE_START_TYPE = "utterance_start"
UTTERANCE_END_TYPE = "utterance_end"
FINAL_TRANSCRIPT_TYPE = "final_transcript"
TRANSCRIPTION_ERROR_TYPE = "transcription_error"


@dataclass(frozen=True)
class UtteranceControlEvent:
    packet_type: str
    utterance_id: str


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_agent_access_token(settings: Settings) -> str:
    return (
        AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(settings.agent_identity)
        .with_ttl(settings.agent_token_ttl)
        .with_grants(
            VideoGrants(
                room_join=True,
                room=settings.livekit_room_name,
                can_subscribe=True,
                can_publish=False,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )


def parse_utterance_control_packet(payload: bytes) -> tuple[UtteranceControlEvent | None, str | None]:
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None, "invalid_utf8"

    try:
        parsed = json.loads(decoded)
    except json.JSONDecodeError:
        return None, "invalid_json"

    if not isinstance(parsed, dict):
        return None, "invalid_shape"
    packet_type = parsed.get("type")
    if packet_type not in {UTTERANCE_START_TYPE, UTTERANCE_END_TYPE}:
        return None, "unknown_type"

    utterance_id = parsed.get("utterance_id")
    if not isinstance(utterance_id, str):
        return None, "missing_utterance_id"
    utterance_id = utterance_id.strip()
    if not utterance_id:
        return None, "empty_utterance_id"

    return UtteranceControlEvent(packet_type=packet_type, utterance_id=utterance_id), None


def build_final_transcript_payload(utterance_id: str, text: str) -> bytes:
    return json.dumps({"type": FINAL_TRANSCRIPT_TYPE, "utterance_id": utterance_id, "text": text}, ensure_ascii=True).encode("utf-8")


def build_transcription_error_payload(utterance_id: str, message: str) -> bytes:
    return json.dumps({"type": TRANSCRIPTION_ERROR_TYPE, "utterance_id": utterance_id, "message": message}, ensure_ascii=True).encode("utf-8")


async def publish_final_transcript(local_participant: rtc.LocalParticipant, utterance_id: str, text: str) -> None:
    await local_participant.publish_data(build_final_transcript_payload(utterance_id, text), reliable=True)


async def publish_transcription_error(local_participant: rtc.LocalParticipant, utterance_id: str, message: str) -> None:
    await local_participant.publish_data(build_transcription_error_payload(utterance_id, message), reliable=True)


class LiveKitTranscriptionParticipant:
    def __init__(self, settings: Settings, *, room: rtc.Room | None = None, whisper_adapter: WhisperAdapter | None = None) -> None:
        self._settings = settings
        self._room = room or rtc.Room()
        self._whisper_adapter = whisper_adapter or WhisperAdapter(settings)
        self._buffer = AudioBuffer()
        self._buffer_lock = asyncio.Lock()
        self._transcription_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._stream_tasks: dict[str, asyncio.Task[None]] = {}
        self._streams: dict[str, rtc.AudioStream] = {}
        self._active_utterance_id: str | None = None
        self._recording_gate_open = False
        self._bind_room_events()

    @property
    def room(self) -> rtc.Room:
        return self._room

    async def run(self) -> None:
        logger.info(
            "agent_startup status=starting room_name=%s participant_identity=%s",
            self._settings.livekit_room_name,
            self._settings.agent_identity,
        )
        token = create_agent_access_token(self._settings)
        logger.info("room_connect_start livekit_url=%s room_name=%s", self._settings.livekit_url, self._settings.livekit_room_name)
        await self._room.connect(self._settings.livekit_url, token)
        logger.info(
            "room_connect_success livekit_url=%s room_name=%s participant_identity=%s",
            self._settings.livekit_url,
            self._settings.livekit_room_name,
            self._settings.agent_identity,
        )
        try:
            await self._stop_event.wait()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        logger.info("agent_shutdown status=starting room_name=%s", self._settings.livekit_room_name)
        self._stop_event.set()
        for task in list(self._stream_tasks.values()):
            task.cancel()
        await asyncio.gather(*self._stream_tasks.values(), return_exceptions=True)
        self._stream_tasks.clear()

        for stream in list(self._streams.values()):
            try:
                await stream.aclose()
            except Exception:
                logger.warning("audio_stream_close_failure", exc_info=True)
        self._streams.clear()

        if self._room.isconnected():
            await self._room.disconnect()
        logger.info("agent_shutdown status=complete room_name=%s", self._settings.livekit_room_name)

    def request_stop(self) -> None:
        self._stop_event.set()

    def _bind_room_events(self) -> None:
        @self._room.on("connected")
        def _on_connected() -> None:
            logger.info("room_event type=connected room_name=%s", self._settings.livekit_room_name)

        @self._room.on("disconnected")
        def _on_disconnected(reason: Any | None = None) -> None:
            logger.info("room_event type=disconnected reason=%s", reason)
            self._stop_event.set()

        @self._room.on("connection_state_changed")
        def _on_connection_state_changed(state: Any) -> None:
            logger.info("room_event type=connection_state_changed state=%s", state)

        @self._room.on("track_subscribed")
        def _on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant) -> None:
            asyncio.create_task(self._handle_track_subscribed(track, publication, participant))

        @self._room.on("track_unsubscribed")
        def _on_track_unsubscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant) -> None:
            asyncio.create_task(self._handle_track_unsubscribed(publication.sid, participant.identity))

        @self._room.on("track_subscription_failed")
        def _on_track_subscription_failed(sid: str, participant: rtc.RemoteParticipant, error: str) -> None:
            logger.warning("track_subscription_failed track_sid=%s remote_participant=%s error=%s", sid, participant.identity, error)

        @self._room.on("data_received")
        def _on_data_received(packet: rtc.DataPacket) -> None:
            asyncio.create_task(self._handle_data_packet(packet))

    async def _handle_track_subscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        logger.info(
            "track_subscribed track_sid=%s remote_participant=%s track_kind=%s track_source=%s",
            publication.sid,
            participant.identity,
            publication.kind,
            publication.source,
        )
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            logger.info("track_ignored reason=non_audio track_sid=%s remote_participant=%s", publication.sid, participant.identity)
            return
        if publication.source != rtc.TrackSource.SOURCE_MICROPHONE:
            logger.info("track_ignored reason=non_microphone track_sid=%s remote_participant=%s", publication.sid, participant.identity)
            return

        existing_task = self._stream_tasks.get(publication.sid)
        if existing_task is not None and not existing_task.done():
            logger.warning("track_subscription_duplicate track_sid=%s remote_participant=%s", publication.sid, participant.identity)
            return

        stream = rtc.AudioStream.from_track(
            track=track,
            sample_rate=self._settings.audio_sample_rate,
            num_channels=self._settings.audio_channels,
        )
        self._streams[publication.sid] = stream
        task = asyncio.create_task(self._consume_audio_stream(publication.sid, participant.identity, stream))
        self._stream_tasks[publication.sid] = task

    async def _handle_track_unsubscribed(self, publication_sid: str, participant_identity: str) -> None:
        logger.info("track_unsubscribed track_sid=%s remote_participant=%s", publication_sid, participant_identity)
        task = self._stream_tasks.pop(publication_sid, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        stream = self._streams.pop(publication_sid, None)
        if stream is not None:
            try:
                await stream.aclose()
            except Exception:
                logger.warning("audio_stream_close_failure track_sid=%s", publication_sid, exc_info=True)

    async def _consume_audio_stream(self, publication_sid: str, participant_identity: str, stream: rtc.AudioStream) -> None:
        logger.info("audio_stream_start track_sid=%s remote_participant=%s", publication_sid, participant_identity)
        try:
            async for event in stream:
                await self._handle_audio_frame(event.frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("audio_stream_failure track_sid=%s remote_participant=%s", publication_sid, participant_identity)
        finally:
            logger.info("audio_stream_stop track_sid=%s remote_participant=%s", publication_sid, participant_identity)

    async def _handle_audio_frame(self, frame: rtc.AudioFrame) -> None:
        async with self._buffer_lock:
            if not self._recording_gate_open:
                return
            self._buffer.append_frame(frame)

    async def _begin_utterance(self, utterance_id: str) -> tuple[bool, str | None]:
        async with self._buffer_lock:
            if self._recording_gate_open:
                return False, "duplicate_utterance_start"

            self._buffer.reset()
            self._active_utterance_id = utterance_id
            self._recording_gate_open = True
            return True, None

    async def _end_utterance(self, utterance_id: str) -> tuple[Any | None, str | None]:
        async with self._buffer_lock:
            if not self._recording_gate_open or self._active_utterance_id is None:
                return None, "utterance_end_without_active_utterance"
            if self._active_utterance_id != utterance_id:
                return None, "stale_utterance_end"

            self._recording_gate_open = False
            self._active_utterance_id = None
            snapshot = self._buffer.snapshot_and_reset()
            return snapshot, None

    async def _handle_data_packet(self, packet: rtc.DataPacket) -> None:
        sender = packet.participant.identity if packet.participant else "unknown"
        if packet.participant is None:
            logger.warning("data_packet_ignored sender=%s reason=unexpected_sender", sender)
            return

        event, warning = parse_utterance_control_packet(packet.data)
        if event is None:
            logger.warning("data_packet_ignored sender=%s reason=%s", sender, warning)
            return

        if event.packet_type == UTTERANCE_START_TYPE:
            started, start_warning = await self._begin_utterance(event.utterance_id)
            if not started:
                logger.warning("data_packet_ignored sender=%s reason=%s utterance_id=%s", sender, start_warning, event.utterance_id)
                return
            logger.info("utterance_start_received sender=%s utterance_id=%s", sender, event.utterance_id)
            return

        snapshot, end_warning = await self._end_utterance(event.utterance_id)
        if end_warning:
            logger.warning("data_packet_ignored sender=%s reason=%s utterance_id=%s", sender, end_warning, event.utterance_id)
            return

        logger.info("utterance_end_received sender=%s utterance_id=%s", sender, event.utterance_id)
        asyncio.create_task(self._process_utterance_end(event.utterance_id, snapshot))

    async def _process_utterance_end(self, utterance_id: str, snapshot) -> None:
        if snapshot is None:
            logger.warning("utterance_end_ignored reason=no_buffered_audio utterance_id=%s", utterance_id)
            await publish_transcription_error(self._room.local_participant, utterance_id, "No buffered audio was available for transcription.")
            return

        logger.info(
            "audio_buffer_reset utterance_id=%s audio_duration_seconds=%.3f utterance_end_timestamp=%s",
            utterance_id,
            snapshot.duration_seconds,
            datetime.now(timezone.utc).isoformat(),
        )

        if self._transcription_lock.locked():
            logger.info("transcription_queue_wait utterance_id=%s", utterance_id)

        async with self._transcription_lock:
            started = time.perf_counter()
            try:
                transcript = await self._whisper_adapter.transcribe_utterance(snapshot.to_wav_bytes())
                await publish_final_transcript(self._room.local_participant, utterance_id, transcript)
                latency_seconds = time.perf_counter() - started
                logger.info(
                    "final_transcript_published utterance_id=%s latency_seconds=%.3f transcript_text=%s",
                    utterance_id,
                    latency_seconds,
                    transcript,
                )
            except WhisperAdapterError as exc:
                latency_seconds = time.perf_counter() - started
                message = str(exc)
                logger.warning(
                    "transcription_error utterance_id=%s latency_seconds=%.3f message=%s",
                    utterance_id,
                    latency_seconds,
                    message,
                )
                await publish_transcription_error(self._room.local_participant, utterance_id, message)
            except Exception:
                logger.exception("unexpected_transcription_failure utterance_id=%s", utterance_id)
                await publish_transcription_error(
                    self._room.local_participant,
                    utterance_id,
                    "Unexpected transcription failure.",
                )


async def _async_main() -> int:
    settings = get_settings()
    logger.info("configuration_validation status=success room_name=%s backend_url=%s whisper_service_url=%s", settings.livekit_room_name, settings.backend_url, settings.whisper_service_url)
    participant = LiveKitTranscriptionParticipant(settings)

    loop = asyncio.get_running_loop()
    stop_requested = asyncio.Event()

    def _request_stop() -> None:
        if not stop_requested.is_set():
            logger.info("signal_received signal=SIGINT")
            stop_requested.set()
            participant.request_stop()

    try:
        loop.add_signal_handler(signal.SIGINT, _request_stop)
        loop.add_signal_handler(signal.SIGTERM, _request_stop)
    except NotImplementedError:
        signal.signal(signal.SIGINT, lambda _sig, _frame: _request_stop())
        signal.signal(signal.SIGTERM, lambda _sig, _frame: _request_stop())

    await participant.run()
    return 0


def main() -> int:
    configure_logging()
    try:
        return asyncio.run(_async_main())
    except KeyboardInterrupt:
        logger.info("agent_shutdown status=interrupted")
        return 0
    except Exception as exc:
        logger.error("agent_startup_failed error=%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
