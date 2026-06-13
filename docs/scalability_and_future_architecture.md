# Scalability & Future Architecture — `job_tracker`

Where the current system is constrained, *why* (grounded in code), and the forward path. The project is **single-user and local-first by design**, so "scalability" here means primarily *latency, throughput per machine, and architectural headroom* — not multi-tenant horizontal scale. Tags: **[observed in code]** / **[inferred improvement]**.

---

## 1. Current Bottlenecks (observed)

### 1.1 LLM call is synchronous and on the request path
`POST /transcript/parse` makes a **blocking** `httpx.post` to Ollama for any command that misses the fast path. *[observed: `extract_semantic_command_once` → `_post_chat`]*
- One call per transcript; `stream=False`; the FastAPI request is held until the model returns.
- **Impact:** non-fast-path latency = model inference time (hundreds of ms to seconds for a 7B model on CPU/consumer GPU). This is the dominant user-perceived latency for conversational commands.
- **Mitigation already in place:** the deterministic fast path (`try_parse_v2`) keeps high-frequency controlled commands (`save it`, `set priority as medium`) entirely off the LLM path. *[observed]*

### 1.2 Whisper is GPU-bound and serialized
- `whisper-service` runs CUDA Faster-Whisper; the model is lazily loaded under a lock on first transcription. *[observed: `transcription.py` `_load_model`]*
- The agent serializes transcription with `_transcription_lock` — **one utterance transcribed at a time per agent**. A second utterance logs `transcription_queue_wait`. *[observed: `agent._process_utterance_end`]*
- **Impact:** voice throughput is bounded by single-stream GPU inference. Fine for one user; a hard ceiling for concurrency.

### 1.3 Voice path is single-room / half-duplex
- The agent tracks exactly one `_active_utterance_id` and one recording gate. *[observed: `agent.py`]*
- **Impact:** the design assumes one speaker, one room. Multi-user concurrent voice is not modeled.

### 1.4 Single-instance database
- SQLAlchemy against a single Postgres/SQLite instance; no read replicas, no sharding, no connection-pool tuning surfaced. *[observed: `database_config.py`, `database.py`]*
- **Impact:** adequate for single-user volumes; the uniqueness constraint and per-target change-draft constraint assume a single authoritative writer.

### 1.5 Model quality is the real ceiling
- The architecture is *safe* with a weak model, but *useful* only with a capable one. `llama3.2:3b` is structurally safe but semantically weaker; `qwen2.5:7b-instruct` performed better in verification; a **7B+ instruct model is recommended**. *[observed: memory `project_phase_single_semantic_extractor`; CONV 21 §11]*
- **Impact:** there is a direct tension — bigger model = better intent/field accuracy but higher latency on the synchronous path.

---

## 2. LLM Latency Constraints

| Lever | Current | Headroom |
|---|---|---|
| Calls per command | exactly 1 | already minimal; no multi-pass |
| Streaming | `stream=False` | **[inferred]** stream tokens to show progress / early-cancel |
| Model residency | `keep_alive` setting passed to Ollama | keeps model warm between calls *[observed]* |
| Determinism | `temperature=0` | maximizes cache-friendliness and repeatability *[observed]* |
| Bypass | deterministic fast path | extend fast-path coverage to shrink LLM share *[inferred]* |

**[inferred improvement]** A small *semantic cache* keyed on (normalized transcript + context fingerprint) → `SemanticCommand` would eliminate repeat-phrasing latency entirely for common commands, at near-zero risk given `temperature=0`.

---

## 3. Whisper / GPU Constraints

- **Cold-start:** first-utterance model load is a latency spike; warming the model at service start would remove it. **[inferred]**
- **Single-stream:** to support concurrent voice, the GPU work would need a batching/queue service or per-user worker pool. **[inferred]**
- **Accuracy vs. model size:** larger Whisper models improve company-name recognition (the chronic ASR pain point) at higher VRAM/latency cost; hotwords already partially mitigate this. *[observed: hotword integration]*
- **[inferred]** A confirm-company / ASR-correction loop (planned, CONV 21 §11) plus persisting corrections to `company_aliases` would create a feedback loop that improves recognition over time without a bigger model.

---

## 4. LiveKit Streaming Limits

- The current model is **one browser publisher + one agent subscriber per room**, gated by reliable control packets. *[observed]*
- Scaling voice to multiple simultaneous users would require: per-room agents (the agent is already a standalone process, which helps), and a Whisper backend that can absorb the resulting concurrent transcription load (see §3).
- The reliable data channel for control/transcript is robust to media packet loss (gating is control-driven, not audio-continuity-driven), so the streaming design is sound for its intended single-user scope. *[observed]*

---

## 5. Database Scaling

For the intended single-user product, the DB is not a near-term bottleneck. If the product ever broadened:
- **Multi-tenancy** would require a `user`/`tenant` model (absent today) and scoping every query/uniqueness constraint by tenant. *[inferred — no user table exists]*
- **The per-target `ApplicationChangeDraft` unique constraint** (one pending change per application) is a single-writer assumption; concurrent editors would need optimistic-concurrency/versioning. *[observed constraint; inferred concern]*
- Read-heavy growth (large application lists) would benefit from pagination on `GET /applications` and indexing review. *[inferred]*

---

## 6. Future Architecture Upgrades

### 6.1 Agentic planner (evolution of the cognitive layer)
**Today:** one stateless interpretation call → one primary intent. **Future [inferred]:** a planner that can decompose a compound utterance ("archive the Google one and bump Acme to high") into an ordered list of *individually validated* `SemanticCommand`s, each still passing through the deterministic pipeline. Critically, the planner must **inherit the same invariant** — it plans, the backend still owns every mutation. The current mixed-intent rejection is the conservative placeholder this would replace.

### 6.2 Event-driven architecture
**Today:** `application_events` already records a timeline (saved / field_changed / note_added / status_changed / archived / restored) as a side effect of dispatch. *[observed: `EVENT_TYPES`, `ApplicationEvent`]* **Future [inferred]:** promote these to a real event stream/outbox so that downstream concerns (reminders, "you haven't followed up in 7 days," analytics) react to events instead of polling. The schema is already event-friendly — this is mostly a transport upgrade.

### 6.3 Memory layer (multi-turn understanding)
**Today:** the only cross-turn state is the single `pending_command`; there is intentionally no conversation history at the model. *[observed]* **Future [inferred]:** a bounded, *structured* memory (recent entities, last-referenced application) injected into the context block — never raw chat history, to avoid the context-contamination failures that motivated the current "explicit fact block" design (CONV 21 §4.8). Memory should be *facts the backend verified*, not model recollection.

### 6.4 Caching strategy
**[inferred]**, layered:
1. **Semantic extraction cache** — (transcript + context fingerprint) → `SemanticCommand`; safe under `temperature=0`.
2. **Whisper warm model + result cache** for repeated identical audio (evaluation runs especially).
3. **Hotword list cache** in the agent (currently fetched per utterance, best-effort) to cut a round-trip. *[observed: per-utterance fetch — a cheap caching win]*
4. **Application-list cache** for the context builder (`_build_applications_list` runs every transcript).

### 6.5 MCP as an integration adapter — not a safety layer
MCP was explicitly evaluated and **deferred** (CONV 21 §5.5): it standardizes tool schemas but does **not** solve semantic misclassification (a model can still emit schema-valid-but-wrong tool calls). The forward position: MCP may later expose tracker capabilities to *external* clients as an adapter, layered *on top of* the deterministic pipeline — never as a replacement for backend mutation authority. *[observed decision]*

---

## 7. Scaling Posture Summary

| Dimension | Current ceiling | Primary lever |
|---|---|---|
| Conversational latency | synchronous single LLM call | fast-path coverage + semantic cache + streaming |
| Voice throughput | one serialized GPU stream per agent | per-room agents + batched Whisper |
| Semantic accuracy | small-model quality | 7B+ instruct model; ASR-correction feedback loop |
| Concurrency / multi-user | single-user by design (no user model) | tenant model + per-tenant scoping (large undertaking) |
| Reactivity | side-effect event rows | promote `application_events` to an event stream |

The honest summary: this is an **architecturally clean single-user system** whose deliberate constraint — *the LLM never owns mutations* — is exactly what makes every future upgrade (planner, memory, events, MCP) safe to layer on without re-introducing the instability the project already fought through and eliminated.
