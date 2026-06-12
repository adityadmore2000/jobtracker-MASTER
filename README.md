# job_tracker

A local-first job application tracker that lets you add and manage applications using natural language — typed or spoken. You describe what you want ("Add Neilsoft for AI Engineer, full time, onsite") and the system interprets it, shows you an editable preview, and saves only after your explicit confirmation. Nothing is written to the database by the LLM directly.

This repository (`jobtracker-MASTER`) is the umbrella entry point for the complete project. The four service repositories are linked as Git submodules. Supporting folders (`docker/`, `docs/`, `evaluation/`, `data/`, `job_tracker-extension/`) are tracked directly here.

---

## Why This Project Exists

Tracking a serious job search across dozens of applications — each with its own status, stage, notes, links, and next actions — becomes unwieldy in a spreadsheet. Context switches are expensive: what stage is this one at? Did I follow up? Where is the job listing? The standard answer is a heavyweight ATS, most of which are designed for recruiters, not candidates.

This project is the other approach: a lightweight local tracker that stays out of the way, captures what you actually care about, and lets you add and update entries by describing them in plain language rather than filling out forms.

---

## Core Capabilities

| Capability | Details |
|---|---|
| Application tracking | Add, view, edit, and archive job applications |
| Draft and pending-change workflow | All LLM-generated changes are previews; nothing is persisted without an explicit save |
| Conversational commands | Natural-language typed commands: "Add Neilsoft for AI Engineer, full time, onsite" |
| Multi-role creates | One command can propose multiple roles for the same company |
| Company matching | Canonical company names and aliases are stored; new companies trigger a confirmation modal |
| Context follow-ups | Follow-up commands apply against the active unsaved draft |
| Voice input | LiveKit tap-to-toggle voice; transcript appears in panel, copied to command area on explicit click |
| ASR hotword adaptation | Backend exposes `/asr/hotwords` for Whisper and the agent to improve transcription accuracy |
| Browser-context capture | Chrome extension captures the active tab URL and title; one click fills the JOB LINK field |
| Local-first execution | Everything runs on your machine; no external API calls required |
| Evaluation tooling | Separate evaluation harness for benchmarking Whisper model accuracy against ground truth |

---

## Repository Map

| Path | Type | Responsibility |
|---|---|---|
| `jobtracker-BE/` | Git submodule | FastAPI backend — CRUD API, semantic command interpreter, company table, Alembic migrations, ASR hotwords |
| `jobtracker-FE/` | Git submodule | Next.js frontend — tracker table, add/edit forms, draft panel, voice panel, company confirmation modal |
| `livekit-agent/` | Git submodule | Python LiveKit RTC participant — subscribes to browser mic audio, calls Whisper, publishes transcript |
| `whisper-service/` | Git submodule | CUDA Faster-Whisper microservice — accepts audio uploads, returns transcripts |
| `job_tracker-extension/` | Supporting folder | Chrome Manifest V3 extension — captures active tab URL/title, posts to backend |
| `docker/` | Supporting folder | Dockerfiles for services (e.g. `faster-whisper.Dockerfile`) |
| `evaluation/` | Supporting folder | Whisper accuracy evaluation harness — ground truth CSV, run scripts, reports |
| `data/` | Supporting folder | Raw audio files used for evaluation runs |
| `docs/` | Documentation | Architecture notes and implementation documents |
| `AGENTS.md` | Documentation | Project constraints and field definitions for AI assistants |

---

## Architecture

### Typed command flow

```mermaid
graph TD
    A[User — Browser UI] -->|natural-language command| B[Next.js Frontend :3000]
    B -->|POST /api/chat| C[FastAPI Backend :8000]
    C -->|structured extraction prompt| D[Ollama llama3.2:3b :11434]
    D -->|extracted fields + tool call| C
    C -->|editable preview response| B
    B -->|explicit Save click| C
    C -->|SQL INSERT / UPDATE| E[PostgreSQL :5432]
```

### Voice input flow

```mermaid
graph TD
    A[User — Browser UI] -->|Connect + Start Recording| F[LiveKit Server :7880]
    F -->|WebRTC audio track| G[livekit-agent]
    G -->|POST /transcribe| H[Whisper Service :8100]
    H -->|transcript text| G
    G -->|final_transcript packet| F
    F -->|WebRTC data channel| B[Next.js Frontend :3000]
    B -->|Use transcript button| A
```

### Browser-context capture flow

```
Chrome Extension → POST /browser-context → FastAPI Backend
                                                ↓
                                    Stored as latest captured URL
                                                ↓
                              Frontend "Use captured URL" button → JOB LINK field
```

**End-to-end sequence:**

1. User types a command ("Add Neilsoft for AI Engineer") or uses the voice flow to get a transcript.
2. Frontend sends the command to `POST /api/chat` along with the active unsaved draft and any selected persisted row.
3. Backend calls Ollama with a structured extraction prompt. Pydantic validates the output. Deterministic rules resolve company matching and enum normalization.
4. Backend returns an **editable preview** — the LLM never writes to the database.
5. If the company is new, the frontend shows a confirmation modal. If it matches an existing company, the save is low-friction.
6. User clicks **Save Application** or **Save Update**. The change is persisted through the standard CRUD endpoints.

---

## Tech Stack

| Layer | Technologies |
|---|---|
| Frontend | Next.js 16, React 19, TypeScript 5.7, Tailwind CSS 4, Radix UI, livekit-client 2 |
| Backend | Python 3.11+, FastAPI 0.115, SQLAlchemy 2, Alembic, Pydantic 2, Uvicorn, psycopg 3 |
| Database | PostgreSQL |
| Local LLM | Ollama (`llama3.2:3b` or any compatible 7B+ model) |
| Voice transport | LiveKit server (local dev mode) |
| Speech-to-text | Faster-Whisper 1.1.1 on CUDA 12.3, ctranslate2 4.5.0, exposed as a FastAPI microservice |
| Browser integration | Chrome Manifest V3 extension (unpacked) |
| Evaluation tooling | Python scripts, pytest, ground truth CSV, per-run JSON reports |

---

## Prerequisites

**Required for the core tracker (frontend + backend):**

- Git with submodule support
- Node.js 20+ with npm
- Python 3.11+ with venv
- PostgreSQL accessible on port 5432
- Ollama running on port 11434 with `llama3.2:3b` pulled

**Required for voice input:**

- LiveKit server CLI (`livekit-server --dev`)
- Docker with NVIDIA Container Toolkit and a CUDA-capable GPU

**Optional:**

- Google Chrome (for the browser extension)

---

## Clone the Complete Project

```bash
git clone --recurse-submodules git@github-adi:adityadmore2000/jobtracker-MASTER.git
cd jobtracker-MASTER
```

If you already cloned without `--recurse-submodules`:

```bash
git submodule update --init --recursive
```

---

## Local Setup

### 1. Start infrastructure dependencies

```bash
# PostgreSQL
docker start resume_tailor

# Ollama
docker start ollama
# ollama pull llama3.2:3b   # if not already pulled

# LiveKit (required only for voice)
livekit-server --dev
```

### 2. Backend (`jobtracker-BE`)

```bash
cd jobtracker-BE
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `jobtracker-BE/.env`:

```env
DATABASE_URL=postgresql+psycopg://<user>:<password>@localhost:5432/job_tracker
TEST_DATABASE_URL=postgresql+psycopg://<user>:<password>@localhost:5432/job_tracker_test
FRONTEND_ORIGIN=http://localhost:3000,http://127.0.0.1:3000
AUTO_MIGRATE=false
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2:3b
OLLAMA_TIMEOUT_SECONDS=20
OLLAMA_KEEP_ALIVE=10m
OLLAMA_MAX_TOOL_TURNS=2
LIVEKIT_URL=ws://127.0.0.1:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=secret
```

Bootstrap databases and run migrations:

```bash
python scripts/bootstrap_postgres.py
alembic upgrade head
```

Start:

```bash
uvicorn app.main:app --reload
```

Health check: `curl http://127.0.0.1:8000/semantic-interpreter/health`

### 3. Frontend (`jobtracker-FE`)

```bash
cd jobtracker-FE
npm install
```

Create `jobtracker-FE/.env.local`:

```env
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

Start:

```bash
npm run dev
```

App available at `http://localhost:3000`.

### 4. Whisper transcription service (`whisper-service`) — optional, requires GPU

```bash
docker build -f whisper-service/Dockerfile -t job-tracker-whisper-cuda:latest whisper-service

mkdir -p "$HOME/.cache/huggingface"
docker run --rm --gpus all \
  -p 8100:8100 \
  --env-file whisper-service/.env \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  job-tracker-whisper-cuda:latest
```

Health check: `curl http://127.0.0.1:8100/health`

### 5. LiveKit agent (`livekit-agent`) — optional, requires Whisper service

```bash
cd livekit-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python agent.py
```

### 6. Chrome extension (`job_tracker-extension`) — optional

1. Open `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked** and select `job_tracker-extension/`

### 7. End-to-end verification

Open `http://localhost:3000`. Type:

```
Add Neilsoft for AI Engineer
```

An editable preview should appear. Click **Save Application** to persist.

---

## Environment Variables

### `jobtracker-BE`

| Variable | Required | Purpose | Safe Example |
|---|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string | `postgresql+psycopg://user:pass@localhost:5432/job_tracker` |
| `TEST_DATABASE_URL` | Yes (tests) | Separate test database | `postgresql+psycopg://user:pass@localhost:5432/job_tracker_test` |
| `FRONTEND_ORIGIN` | Yes | CORS allowed origins | `http://localhost:3000,http://127.0.0.1:3000` |
| `AUTO_MIGRATE` | No | Run Alembic on startup | `false` |
| `OLLAMA_BASE_URL` | Yes | Ollama endpoint | `http://127.0.0.1:11434` |
| `OLLAMA_MODEL` | Yes | Model name | `llama3.2:3b` |
| `OLLAMA_TIMEOUT_SECONDS` | No | Per-request timeout | `20` |
| `OLLAMA_KEEP_ALIVE` | No | Model keep-alive duration | `10m` |
| `OLLAMA_MAX_TOOL_TURNS` | No | Max LLM tool turns | `2` |
| `LIVEKIT_URL` | No | LiveKit server URL | `ws://127.0.0.1:7880` |
| `LIVEKIT_API_KEY` | No | LiveKit API key | `devkey` |
| `LIVEKIT_API_SECRET` | No | LiveKit API secret | `secret` |

### `jobtracker-FE`

| Variable | Required | Purpose | Safe Example |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Yes | Backend base URL | `http://127.0.0.1:8000` |

### `livekit-agent`

| Variable | Required | Purpose | Safe Example |
|---|---|---|---|
| `LIVEKIT_URL` | Yes | LiveKit server WebSocket URL | `ws://127.0.0.1:7880` |
| `LIVEKIT_API_KEY` | Yes | LiveKit API key | `devkey` |
| `LIVEKIT_API_SECRET` | Yes | LiveKit API secret | `secret` |
| `LIVEKIT_ROOM_NAME` | Yes | Room to join | `job-tracker-local` |
| `JOBTRACKER_BACKEND_URL` | Yes | Backend URL for hotwords | `http://127.0.0.1:8000` |
| `WHISPER_SERVICE_URL` | Yes | Whisper microservice URL | `http://127.0.0.1:8100` |
| `WHISPER_REQUEST_TIMEOUT_SECONDS` | No | Whisper HTTP timeout | `120` |

### `whisper-service`

| Variable | Required | Purpose | Safe Example |
|---|---|---|---|
| `WHISPER_MODEL_SIZE` | No | Faster-Whisper model size | `small` |
| `WHISPER_DEVICE` | No | Inference device | `cuda` |
| `WHISPER_COMPUTE_TYPE` | No | CTranslate2 compute type | `float16` |
| `WHISPER_LANGUAGE` | No | Language hint | `en` |
| `MAX_AUDIO_UPLOAD_MB` | No | Max upload size | `20` |

---

## How to Use the Application

1. Open `http://localhost:3000`. The tracker table is empty on first run.
2. Type a command in the **Transcript Command** area: `Add Stripe for AI Engineer, full time, remote`
3. An editable draft appears. Adjust any field, then click **Save Application**.
4. To update an existing row: click the row to select it, then type: `Update status to interviewing`
5. To capture a job listing URL: click the Chrome extension on the job page, then click **Use captured URL** in the form.
6. For voice: click **Connect**, then **Start Recording**. Speak your command. Click **Stop Recording**, then **Use transcript** to copy it to the command area.

---

## API Overview

| Method | Path | Description |
|---|---|---|
| `GET` | `/applications` | List all applications |
| `POST` | `/applications` | Create a new application |
| `PATCH` | `/applications/{id}` | Update an application |
| `DELETE` | `/applications/{id}` | Permanently delete an application |
| `POST` | `/api/chat` | Process a natural-language command; returns an editable preview |
| `GET` | `/asr/hotwords` | Return deduplicated hotword list for Whisper/agent callers |
| `POST` | `/browser-context` | Store a captured browser URL from the Chrome extension |
| `GET` | `/browser-context/latest` | Retrieve the most recently captured URL |
| `GET` | `/semantic-interpreter/health` | Ollama connectivity health check |

---

## Testing

| Repository | Command | Notes |
|---|---|---|
| `jobtracker-BE` | `cd jobtracker-BE && source .venv/bin/activate && pytest` | Requires `TEST_DATABASE_URL` pointing to a running PostgreSQL instance |
| `jobtracker-FE` | `cd jobtracker-FE && npm run test` | Vitest + Testing Library; no server required |
| `livekit-agent` | `cd livekit-agent && source .venv/bin/activate && pytest` | Mocks LiveKit and Whisper; no services required |
| `whisper-service` | `cd whisper-service && PYTHONPATH=. pytest` | Mocks Whisper model; no GPU or Docker required |
| Evaluation tooling | `cd evaluation && python evaluate.py` | Requires Whisper service running and audio files in `data/raw/` |

---

## Development Workflow

Changes must be committed inside the relevant child repository first. Then update the umbrella's submodule pointer.

```bash
# 1. Make changes inside the child repository
cd jobtracker-BE
git add app/main.py
git commit -m "fix: improve semantic prompt"
git push

# 2. Update the umbrella pointer
cd ..
git add jobtracker-BE
git commit -m "chore: update jobtracker-BE revision"
git push
```

The child-repository commit and the umbrella pointer update are always separate commits. The umbrella never contains child source code directly — only a pointer to a specific commit in each child repo.

---

## Current Limitations

- Voice transcripts always require an explicit **Use transcript** click before they reach the command area — there is no automatic submission.
- No VAD, silence detection, or partial transcripts in the voice pipeline.
- No CSV import/export.
- No reminders, scheduled follow-ups, or notifications.
- No analytics or reporting dashboards.
- The Chrome extension captures URL and title only; it does not scrape page content or infer metadata.
- The Whisper service requires a CUDA-capable GPU for production use; CPU fallback is for debugging only.
- No automatic fine-tuning or model deployment pipeline.

---

## Documentation Map

| Path | Contents |
|---|---|
| `AGENTS.md` | Project constraints, allowed field values, and rules for AI assistants working in this repo |
| `docs/` | Architecture notes and implementation documents |
| `evaluation/README.md` | Whisper evaluation harness — how to run, directory layout, Docker build |

---

## Related Repositories

| Repository | URL |
|---|---|
| `jobtracker-BE` | git@github-adi:adityadmore2000/jobtracker-BE.git |
| `jobtracker-FE` | git@github-adi:adityadmore2000/jobtracker-FE.git |
| `livekit-agent` | git@github-adi:adityadmore2000/livekit-agent.git |
| `whisper-service` | git@github-adi:adityadmore2000/whisper-service.git |

---

## License

License information has not yet been added.
