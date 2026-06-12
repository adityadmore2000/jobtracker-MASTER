from __future__ import annotations

from dataclasses import dataclass
import threading
import wave
from io import BytesIO

from livekit import rtc


PCM_SAMPLE_WIDTH_BYTES = 2


@dataclass(frozen=True)
class BufferedUtterance:
    pcm_bytes: bytes
    sample_rate: int
    num_channels: int
    sample_width_bytes: int
    total_samples_per_channel: int

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return self.total_samples_per_channel / self.sample_rate

    def to_wav_bytes(self) -> bytes:
        output = BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(self.num_channels)
            wav_file.setsampwidth(self.sample_width_bytes)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(self.pcm_bytes)
        return output.getvalue()


class AudioBuffer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pcm_bytes = bytearray()
        self._sample_rate: int | None = None
        self._num_channels: int | None = None
        self._sample_width_bytes = PCM_SAMPLE_WIDTH_BYTES
        self._total_samples_per_channel = 0

    def append_frame(self, frame: rtc.AudioFrame) -> None:
        frame_bytes = bytes(frame.data)
        expected_width = len(frame_bytes) // (frame.samples_per_channel * frame.num_channels)
        if expected_width != PCM_SAMPLE_WIDTH_BYTES:
            raise ValueError(f"Unsupported PCM sample width: {expected_width} bytes.")

        with self._lock:
            if self._sample_rate is None:
                self._sample_rate = frame.sample_rate
                self._num_channels = frame.num_channels
            elif self._sample_rate != frame.sample_rate or self._num_channels != frame.num_channels:
                raise ValueError(
                    "Received inconsistent audio frame format. "
                    f"Expected {self._sample_rate}Hz/{self._num_channels}ch but got {frame.sample_rate}Hz/{frame.num_channels}ch."
                )

            self._pcm_bytes.extend(frame_bytes)
            self._total_samples_per_channel += frame.samples_per_channel

    def snapshot_and_reset(self) -> BufferedUtterance | None:
        with self._lock:
            if not self._pcm_bytes or self._sample_rate is None or self._num_channels is None:
                self._reset_unlocked()
                return None

            snapshot = BufferedUtterance(
                pcm_bytes=bytes(self._pcm_bytes),
                sample_rate=self._sample_rate,
                num_channels=self._num_channels,
                sample_width_bytes=self._sample_width_bytes,
                total_samples_per_channel=self._total_samples_per_channel,
            )
            self._reset_unlocked()
            return snapshot

    def reset(self) -> None:
        with self._lock:
            self._reset_unlocked()

    def _reset_unlocked(self) -> None:
        self._pcm_bytes = bytearray()
        self._sample_rate = None
        self._num_channels = None
        self._total_samples_per_channel = 0
