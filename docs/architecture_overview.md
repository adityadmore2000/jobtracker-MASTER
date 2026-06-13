# Architecture Overview — `job_tracker`

> A local-first, conversational job-application tracker. You describe what you want — typed or spoken — and the system interprets it, shows you an editable preview, and persists only after explicit confirmation. **Nothing is ever written to the database directly by the LLM.**

This document explains the system **behavior-first**: what the system *does* and *guarantees*, then which components realize those guarantees. It is grounded in the actual repository, not a generic template. Statements are tagged **[observed in code]** or **[inferred improvement]** where it matters.

---

## 1. The One Idea That Shapes Everything

Every architectural decision in this repo descends from a single invariant:

> **The LLM understands language. The backend controls mutations.**
> *(`app/semantic_command_pipeline.py` module docstring — [observed in code])*

The language model is permitted to *interpret* a natural-language utterance into a strict structured envelope. It is **never** permitted to choose a database operation, construct tool arguments, resolve which record to mutate, or commit a write. All of that is deterministic backend code. This is the lesson the project learned the hard way (see `docs/AI_brainstormed_implementation/conversations/CONV 21.md`): an earlier design let a small local model select tools and build payloads, and it hallucinated note prose into the `role` field, picked the wrong operation, and emitted malformed tool envelopes. The architecture below is the corrective.

---

## 2. System Goals & Constraints

### Goals
- **Natural language in any order.** "I applied for AI Engineer at Aiden AI, it's onsite, full-time, high priority" should work as well as a rigid command. *[observed: single-call extractor + multi-field `changes`]*
- **Safety over recall.** A command that cannot be safely resolved becomes a *clarification* or *suggestion*, never a guessed mutation. *[observed: `SuggestionOutcome` / `ClarificationOutcome`]*
- **Explicit confirmation for persisted records.** Transcript-driven edits to a saved row produce a *Pending Change* preview, not a direct write. *[observed: `create_application_update_draft`]*
- **Voice as a first-class input,** equivalent to typing, by converging both paths onto the same transcript endpoint. *[observed: LiveKit agent publishes `final_transcript`; FE feeds it to `POST /transcript/parse`]*
- **Local-first.** Ollama, Whisper, Postgres/SQLite all run on the user's machine; no external API calls are required. *[observed: README + `httpx.post(f"{settings.base_url}/api/chat")` to local Ollama]*

### Constraints
- **Small local models are unreliable interpreters.** The design assumes the model can produce schema-valid-but-semantically-wrong output and defends against it. *[observed: `extra="forbid"`, intent-specific validation, DB-verified target resolution]*
- **No conversation history at the model.** Each extraction is single-shot; context is injected as an explicit fact block, not a chat transcript. *[observed: `_build_user_message` / `_format_context_block`]*
- **Whisper needs a GPU** for acceptable latency (CUDA Faster-Whisper). *[observed: `whisper-service`, `docker/faster-whisper.Dockerfile`]*
- **Voice is half-duplex and tap-gated** — recording only flows while an utterance gate is open. *[observed: `_recording_gate_open` in `livekit-agent/agent.py`]*

---

## 3. The Four Layers (behavior-first)

The system is best understood as four behavioral layers, not as four microservices. Components cross layer boundaries; that is intentional.

```
┌──────────────────────────────────────────────────────────────────────┐
│  COGNITIVE LAYER  — "understand the language"                          │
│  Single-call Ollama extractor → strict SemanticCommand envelope        │
│  (interpretation ONLY; zero mutation authority)                        │
├──────────────────────────────────────────────────────────────────────┤
│  EXECUTION LAYER  — "decide and apply safely"                          │
│  Pending-continuation → deterministic fast path → pipeline             │
│  (sanitize → normalize → validate → DB-verified target → dispatch)     │
├──────────────────────────────────────────────────────────────────────┤
│  VOICE LAYER  — "turn speech into a transcript"                        │
│  Browser mic → LiveKit RTC → utterance gate → Whisper → transcript     │
│  (converges onto the SAME text input as typing)                        │
├──────────────────────────────────────────────────────────────────────┤
│  DATA LAYER  — "remember and stage"                                    │
│  Companies · JobApplications · Drafts · Change-Drafts · Notes · Events │
│  (drafts and pending-changes are first-class, navigable lifecycle)     │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.1 Cognitive Layer — interpretation only
- **Component:** `app/semantic_command_extractor.py` + `app/semantic_command_schemas.py`.
- **Behavior:** exactly **one** Ollama `/api/chat` call per transcript, `format=json`, `temperature=0`, with a JSON-schema-shaped system prompt. Output is parsed and validated against `SemanticCommand` with `extra="forbid"`. *[observed]*
- **Authority:** none. It emits an `intent` (one of five literals: `create_application`, `update_application`, `append_note`, `archive_application`, `unsupported`), an identity-only `target`, a mutable-fields-only `changes`, an optional `note`, and optional `suggested_phrasings`. It cannot name an operation or a record id it controls.
- **Why it is shaped this way:** the prompt is exhaustively explicit (intent-from-meaning, questions-are-not-commands, status-is-never-an-intent, note-text-never-in-fields) precisely because the target model is small. Schema strictness converts hallucination into a safe rejection rather than a corrupt write.

### 3.2 Execution Layer — the safety machine
The transcript endpoint `POST /transcript/parse` (`app/main.py:610`) runs a strict, ordered ladder *[observed]*:

1. **Pending continuation** (`resume_pending_command`) — if the previous turn asked "which company?", this turn's reply only fills the missing identity and resumes. Never reaches the LLM.
2. **Deterministic fast path** (`fast_path_parser.try_parse_v2`) — high-confidence controlled commands (`save it`, `discard draft`, `set priority as medium`) bypass the model entirely.
3. **Single-call extractor** — only on `ParseMiss`, and only when `USE_SINGLE_SEMANTIC_EXTRACTOR` is enabled.
4. **Pipeline** (`resolve_semantic_command`) — sanitize blanks → normalize aliases → validate enums → intent-specific validation → **DB-verified** target resolution → emit a `MutationPayload`, a `ClarificationOutcome`, a `SuggestionOutcome`, or a `MixedIntentOutcome`.
5. **Dispatcher** (`mutation_dispatcher.dispatch`) — the *only* code that writes to the DB, over a closed set of `ALLOWED_OPERATIONS`.

The crucial property: **no LLM-provided `application_id` is ever trusted.** Every target is re-resolved against live DB rows (`_resolve_update_target`, `_match_by_company_role`). *[observed]*

### 3.3 Voice Layer — speech converges onto text
- **Components:** `jobtracker-FE` VoiceButton → `livekit-agent/agent.py` → `whisper-service`.
- **Behavior:** the browser publishes mic audio over LiveKit RTC plus `utterance_start` / `utterance_end` control packets. The agent buffers PCM **only while the gate is open**, snapshots the utterance to WAV on `utterance_end`, calls the Whisper microservice (with backend-provided hotwords), and publishes a `final_transcript` data packet back to the browser. *[observed in `agent.py`, `audio_buffer.py`, `whisper_adapter.py`]*
- **Convergence:** the transcript is copied into the command area and submitted to the *same* `POST /transcript/parse` endpoint as typed input. Voice adds no parallel semantic path — it only produces text. *[observed: README typed/voice flow + agent publishing transcript]*

### 3.4 Data Layer — every persisted state is navigable
- **Component:** `app/models.py`, Alembic migrations `0001`–`0010`.
- **Behavior:** the schema makes the workflow's states real rows: `JobApplication` (with `is_draft`, `archived_at`), `ApplicationChangeDraft` (a staged delta for a saved row, one per target), `ApplicationNote`, `ApplicationEvent` (timeline), `Company` (canonical, FK from applications). *[observed]*
- **Why:** a hard lesson from CONV 21 — a persisted lifecycle state that can *block* an action (a hidden draft failing a uniqueness check) **must** have a UI surface and a recovery action. Hence `GET /drafts` and the Active / Drafts / Archived tabs.

---

## 4. Component → Layer Map

| Component (repo) | Type | Primary layer | Responsibility |
|---|---|---|---|
| `jobtracker-FE/` | Next.js submodule | Execution (UI) + Voice (capture) | AppShell, Chat/Detail/Applications panels, URL-canonical selection, voice capture |
| `jobtracker-BE/` | FastAPI submodule | Cognitive + Execution + Data | Extractor, pipeline, dispatcher, CRUD, company resolution, ASR hotwords |
| `livekit-agent/` | Python submodule | Voice | RTC participant: utterance gating, buffering, Whisper call, transcript publish |
| `whisper-service/` | Python submodule | Voice | CUDA Faster-Whisper microservice; `POST /transcribe` |
| `job_tracker-extension/` | Chrome MV3 | Data (capture) | Active-tab URL/title → `POST /browser-context` → fills JOB LINK |
| `evaluation/` | Harness | (off-path) | Whisper accuracy benchmarking against ground truth |

---

## 5. What "Local-First" Buys and Costs

**Buys:** privacy (job-search data never leaves the machine), zero per-request cost, offline operation, full control of the model. **Costs:** the interpreter is a small local model whose semantic accuracy is the system's dominant quality risk — which is exactly why mutation authority was removed from it. The architecture trades model trust for deterministic backend control. *[observed throughout; rationale in CONV 21]*

---

## 6. Where to Read Next
- **`system_design_deep_dive.md`** — the reasoning pipeline, validation, and mutation strategy in depth.
- **`runtime_flows.md`** — step-by-step text and voice traces with decision points and async boundaries.
- **`architecture_diagrams.md`** — Mermaid: cognitive architecture, sequences, voice pipeline, lifecycle state machine, dependency graph.
- **`data_model.md`** — schema, transitions, validation rules, example JSON.
- **`failure_modes_and_reliability.md`** — hallucination, invalid intent, DB/voice failures, retries, fallbacks.
- **`scalability_and_future_architecture.md`** — bottlenecks and the forward path.
