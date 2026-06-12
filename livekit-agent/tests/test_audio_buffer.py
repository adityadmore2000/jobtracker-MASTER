import wave
from io import BytesIO

from livekit import rtc

from audio_buffer import AudioBuffer


def make_frame(samples_per_channel: int, *, start_value: int = 0, sample_rate: int = 48000, num_channels: int = 1) -> rtc.AudioFrame:
    values = []
    current = start_value
    for _ in range(samples_per_channel * num_channels):
        values.append(int(current).to_bytes(2, byteorder="little", signed=True))
        current += 1
    return rtc.AudioFrame(data=b"".join(values), sample_rate=sample_rate, num_channels=num_channels, samples_per_channel=samples_per_channel)


def test_pcm_frames_append_snapshot_and_reset():
    buffer = AudioBuffer()
    first = make_frame(4800, start_value=1)
    second = make_frame(4800, start_value=100)

    buffer.append_frame(first)
    buffer.append_frame(second)
    snapshot = buffer.snapshot_and_reset()

    assert snapshot is not None
    assert snapshot.sample_rate == 48000
    assert snapshot.num_channels == 1
    assert snapshot.sample_width_bytes == 2
    assert snapshot.total_samples_per_channel == 9600
    assert round(snapshot.duration_seconds, 3) == 0.2

    detached_pcm = snapshot.pcm_bytes
    buffer.append_frame(make_frame(4800, start_value=200))
    second_snapshot = buffer.snapshot_and_reset()

    assert second_snapshot is not None
    assert second_snapshot.pcm_bytes != detached_pcm
    assert snapshot.pcm_bytes == detached_pcm


def test_empty_buffer_snapshot_is_safe():
    buffer = AudioBuffer()
    assert buffer.snapshot_and_reset() is None


def test_wav_output_has_valid_header_and_format():
    buffer = AudioBuffer()
    buffer.append_frame(make_frame(4800))

    snapshot = buffer.snapshot_and_reset()
    assert snapshot is not None
    wav_bytes = snapshot.to_wav_bytes()

    with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 48000
        assert wav_file.getnframes() == 4800
