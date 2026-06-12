# LiveKit Local Participant

This service is a lightweight direct Python LiveKit RTC participant for `job_tracker`.

It is intentionally not a LiveKit Agents SDK worker. It does not use worker registration, dispatch, subprocess jobs, VAD, turn detection, or plugin lifecycles. Its only job in this phase is:

```text
join local LiveKit room
    -> subscribe to browser microphone audio
    -> buffer PCM frames only during an active utterance window
    -> wait for utterance_start and utterance_end control packets
    -> fetch current canonical hotwords from the backend
    -> call whisper-service /transcribe
    -> publish final_transcript or transcription_error packets
```

## Required Local Services

- `livekit-server --dev`
- `jobtracker-BE`
- `whisper-service`

Frontend microphone publishing is now handled by the PR 3 browser voice controls.

## Environment

Copy `.env.example` to `.env`:

```text
LIVEKIT_URL=ws://127.0.0.1:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=secret
LIVEKIT_ROOM_NAME=job-tracker-local
JOBTRACKER_BACKEND_URL=http://127.0.0.1:8000
WHISPER_SERVICE_URL=http://127.0.0.1:8100
WHISPER_REQUEST_TIMEOUT_SECONDS=120
```

OS environment variables override values from `livekit-agent/.env`.

`WHISPER_REQUEST_TIMEOUT_SECONDS` defaults to `120` seconds, must be a positive number, and is used for the Whisper `/transcribe` HTTP request only.

## Setup

```bash
cd /home/aditya/dev-work/job_tracker_assistant/livekit-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
cd /home/aditya/dev-work/job_tracker_assistant/livekit-agent
source .venv/bin/activate
python agent.py
```

The participant connects with a fixed local identity:

```text
job-tracker-local-agent
```

## Structured Logs

Logs are written to stdout and include:

- startup and configuration validation
- room connect start / success / failure
- participant identity and room name
- track subscription events
- utterance ids
- recording gate open / close events
- buffer reset and audio duration
- hotword fetch start / success / failure
- Whisper request start / success / failure with configured timeout and request duration
- final transcript publish success
- clean shutdown

Secrets and JWTs are never logged.

## Current Limitations

- No VAD or silence detection
- No partial transcripts
- No automatic submission to the typed transcript flow
- No LiveKit Agents SDK worker lifecycle
