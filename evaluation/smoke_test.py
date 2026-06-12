from pathlib import Path
from time import perf_counter

from faster_whisper import WhisperModel


AUDIO_FILE = Path("data/raw/short/1.wav")


def main() -> None:
    if not AUDIO_FILE.exists():
        raise FileNotFoundError(f"Missing audio file: {AUDIO_FILE}")

    load_started = perf_counter()

    model = WhisperModel(
        "small",
        device="cuda",
        compute_type="float16",
    )

    model_load_seconds = perf_counter() - load_started

    transcription_started = perf_counter()

    segments, info = model.transcribe(
        str(AUDIO_FILE),
        language="en",
        task="transcribe",
        beam_size=5,
        vad_filter=Falseo,
    )

    segments = list(segments)

    transcription_seconds = perf_counter() - transcription_started

    text = " ".join(
        segment.text.strip()
        for segment in segments
        if segment.text.strip()
    ).strip()

    print(f"Model load seconds: {model_load_seconds:.4f}")
    print(f"Transcription seconds: {transcription_seconds:.4f}")
    print(f"Detected language: {info.language}")
    print(f"Segments: {len(segments)}")
    print(f"Transcription: {text}")


if __name__ == "__main__":
    main()
