# System Design Deep Dive — `job_tracker`

This document breaks down the system's execution core: the LLM reasoning pipeline, the intent→action transformation, schema validation, the DB mutation strategy, the failure-handling system, and scalability constraints. Everything is grounded in `jobtracker-BE/app/*`. Tags: **[observed in code]** vs **[inferred improvement]**.

---

## 1. The Transcript Endpoint Is a Ladder, Not a Function

`POST /transcript/parse` (`app/main.py:610`) is the single entry point for both typed and voice commands. It executes a strict, **short-circuiting ladder** — each rung either resolves the request or falls through to the next. *[observed]*

```
context (selection + pending_command) + transcript
   │
 0 │ resume_pending_command(...)         ── clarification continuation (no LLM)
   │      └─ resolves? → response
 1 │ try_parse_v2(transcript, context)   ── deterministic fast path (no LLM)
   │      ├─ MutationPayload      → dispatch() → response
   │      └─ ClarificationNeeded  → ask, carry pending_command
 2 │ ParseMiss + flag on:
   │   extract_semantic_command_once(...)── ONE Ollama call (interpretation only)
   │      └─ SemanticExtractorError → unsupported_command_response()  (safe)
 3 │ resolve_semantic_command(cmd, ctx, db) ── deterministic pipeline
   │      → DispatchOutcome | Clarification | Suggestion | MixedIntent
 4 │ dispatch(payload, db)               ── the ONLY DB writer
```

Design rationale: cheap/safe rungs run first. Continuation and the fast path never touch the model, so the common high-confidence commands are deterministic and low-latency. The model is the *last resort for understanding*, never the decider of action. *[observed; rationale in CONV 21 §6 Decisions]*

---

## 2. LLM Reasoning Pipeline (Cognitive Layer)

### 2.1 One call, structured output, zero temperature
`extract_semantic_command_once` (`app/semantic_command_extractor.py`) makes exactly one request to local Ollama: *[observed]*

```python
{
  "model": settings.model, "stream": False, "keep_alive": settings.keep_alive,
  "format": "json", "options": {"temperature": 0},
  "messages": [{"role": "system", "content": _SYSTEM_PROMPT},
               {"role": "user",   "content": context_block + "\nCommand: " + transcript}],
}
```

- **`format=json`** forces the model into JSON mode.
- **`temperature=0`** makes interpretation as deterministic as the model allows.
- **No tool-calling.** The earlier dual-output design (extract fields *and* select tool/args) was abandoned because it created two independent failure surfaces and malformed envelopes (CONV 21 §5.2). This is structured JSON only.

### 2.2 The prompt is a contract, not a hint
`_SYSTEM_PROMPT` is long and prescriptive because the target is a small model. It pins down, among others *[observed]*:
- The exact output schema (every key present; `null` when N/A).
- **Five intent literals only** — and "intent is NEVER a status/priority value" (the model used to emit `intent: "applied"`).
- **Identity vs. mutation separation**: company/role live in `target`; mutable fields in `changes`; the two never cross.
- **Notes are sacred**: free-form note prose goes only in `note`, never in `role`/`comments`/`next_action`.
- **Questions are not commands**: "how many applications do I have?" → `unsupported`.
- **Context resolution rules**: a company named in the command always overrides the selected application; only a command naming no company at all inherits the selected application's company.

### 2.3 Context injection without history
There is **no chat history**. The selected record is rendered as an explicit fact block by `_format_context_block` *[observed]*:

```
Context:
- Selected application: Aiden AI — AI Engineer
- Draft active: yes
```

IDs are **never** exposed to the model; the noisy full `known_applications` list is deliberately omitted (it caused short names to pattern-match the wrong row — CONV 21 §4.8). The backend re-resolves and verifies every target.

### 2.4 Strict structural validation with self-repair
- Output is `json.loads`-parsed; non-JSON or non-object → `SemanticExtractorInvalidResponseError`. *[observed]*
- `_lift_misplaced_note` performs **one** safe repair: if the model puts `note` inside `changes` (an illegal key under `extra="forbid"`), it lifts it to the top-level `note` rather than rejecting the whole command — but only when top-level `note` is empty, never overwriting. *[observed]*
- `SemanticCommand.model_validate(raw)` enforces `extra="forbid"` on every nested model — an unknown key is a hard rejection (safe: no mutation). `target`/`changes` `null` is coerced to empty models via `field_validator`. *[observed]*

---

## 3. Intent → Action Transformation (Execution Layer)

`resolve_semantic_command` in `app/semantic_command_pipeline.py` turns a validated `SemanticCommand` into one of four outcomes. **This module owns mutation authority.** *[observed]*

### 3.1 The transformation steps
```
SemanticCommand
  → _reconcile_location_employment_mixup   (repair model placing "full-time" into location_mode)
  → _normalize_and_validate_changes        (alias-normalize + enum-validate every field)
  → intent handler (_handle_create / _handle_update / note / archive)
  → _resolve_*_target                      (DB-verified identity resolution)
  → MutationPayload | Clarification | Suggestion | MixedIntent
```

### 3.2 Atomic, all-or-nothing field validation
`_normalize_and_validate_changes` collects **every** invalid field into an `invalid` list. If anything is invalid, the handler downgrades the whole command to a `SuggestionOutcome` — **a command is never partially applied.** *[observed: `if norm.invalid: return SuggestionOutcome(...)`]* Valid multi-field updates flow through atomically because `ApplicationChanges` carries all optional fields and the dispatcher applies them in one transaction.

### 3.3 Intent-specific safety boundaries (schema-valid ≠ semantic-valid)
- **Mixed intent rejection:** if `intent ∈ {create, update}` *and* `cmd.note` is present, the handler returns `MixedIntentOutcome` — "update the fields and add the note in separate messages." Notes persist immediately; field changes go through Pending-Changes review, so mixing them is unsafe. *[observed: `if cmd.note: return MixedIntentOutcome()`]*
- **Never guess a field:** `update_application` with no concrete field change → `SuggestionOutcome` (offers templated rephrasings), never a no-op mutation. *[observed: `if not _changes_has_any(...)`]*
- **Create requires a company:** no company → suggestion with an example. *[observed]*

### 3.4 DB-verified target resolution (the trust boundary)
`_resolve_update_target` precedence *[observed]*:

```
explicit company (+ optional role) in transcript
   → match against ACTIVE applications (not draft, not archived)
   → exactly 1 match  → create_application_update_draft  (Pending Change, NOT a direct write)
   → 0 matches        → clarification "No active application found for …"
   → >1 match         → clarification "Which role at <Company>? (…)"  + missing_field=role
else active draft in context  → patch_draft
else active application in context → create_application_update_draft (re-verified via db.get + not draft + not archived)
else → clarification "Which application should I update?"  + missing_field=company
```

The pivotal invariant — fixed during CONV 21 §4.2 — is that a transcript edit to a **saved** row produces `create_application_update_draft` (a staged delta), **not** `patch_application` (a direct commit). Direct `patch_application` is reserved for manual form editing. *[observed]*

### 3.5 Clarification continuation
When resolution is ambiguous, the outcome carries a `pending_command` describing the partial intent and the `missing_field`. The frontend echoes it back on the next turn, and `resume_pending_command` (rung 0) fills *only* the missing identity and resumes — without re-invoking the model. This made clarifications genuinely stateful (they were previously "decorative" — CONV 21 §4.6). *[observed]*

---

## 4. Schema Validation Pipeline (layered defense)

Validation happens at **four** distinct depths, each catching a different failure class *[observed]*:

| Depth | Mechanism | Catches |
|---|---|---|
| 1. Structural | Pydantic `extra="forbid"` on `SemanticCommand`/`Target`/`Changes` | unknown keys, wrong shapes, company/role smuggled into `changes` |
| 2. Lexical | `_clean_str` / `_clean_list` | blank/whitespace-only values |
| 3. Enum/alias | `normalize_status/priority/location/employment/stage_value` (`constants.py`) | out-of-vocabulary values; canonicalizes aliases (`wfh`→`remote`, `fulltime`→`Full Time`) |
| 4. Semantic/contextual | intent handlers + `_resolve_*_target` | mixed intent, empty changes, unresolvable/ambiguous targets, note-in-field |

`constants.py` adds **import-time assertions** that every alias maps to a canonical value — a guard against the alias tables silently drifting out of the allowed enums. *[observed: `assert _STATUS_ALIAS_TARGETS.issubset(...)`]* Roles are deliberately **free-form** (never enum-validated); `ALLOWED_ROLES` is only an ASR/UI hint. *[observed: `constants.py` comment]*

---

## 5. DB Mutation Strategy

### 5.1 One writer, closed operation set
`mutation_dispatcher.dispatch` (`app/mutation_dispatcher.py:1025`) is the only path that mutates application data, and it accepts only `ALLOWED_OPERATIONS` (`mutation_schemas.py`): *[observed]*

```
create_draft, patch_draft, save_draft, discard_draft,
patch_application, append_note,
archive_application, restore_application, delete_application_permanently,
create_application_update_draft, patch_application_update_draft,
apply_application_update_draft, discard_application_update_draft,
ask_clarification, set_active_application
```

### 5.2 Two-stage persistence for saved rows
Transcript-originated edits never hit the live row directly:

```
transcript edit to saved row
  → create_application_update_draft   → ApplicationChangeDraft (changes_json delta; one per target via uq constraint)
  → user reviews "Pending Changes"
  → apply_application_update_draft    → applies delta to JobApplication, emits ApplicationEvent
     OR discard_application_update_draft
```

Drafts of *new* applications are real `JobApplication` rows with `is_draft=true`, materialized only on explicit `save_draft`. *[observed in `models.py` + dispatcher]*

### 5.3 Uniqueness & collisions
`uq_job_applications_company_role(company_id, normalized_role)` enforces one application per (company, role). On collision, `create_draft` returns structured `CollisionInfo` (`kind ∈ {draft, active_application, archived_application}`, ids, archived flag) so the UI can offer the right recovery — Open / Discard / Restore — instead of a dead "already exists" string. *[observed: `mutation_dispatcher.py:216–290`, `CollisionInfo`]*

### 5.4 Notes are structurally isolated
`append_note` writes to `application_notes` (cascade-delete with the parent, including discarded drafts). Note text can never reach a mutable application field because the schema forbids it and the pipeline rejects mixed intents. *[observed]*

---

## 6. Failure-Handling System (summary; full detail in `failure_modes_and_reliability.md`)

The system's failure philosophy is **fail closed, surface clearly, never corrupt**:

- **Ollama down / timeout / invalid JSON / schema violation** → `SemanticExtractorError` → `unsupported_command_response()`. No mutation, no HTTP 500. *[observed: `try/except SemanticExtractorError` in `main.py`]*
- **Out-of-vocabulary field** → that field invalidates → `SuggestionOutcome` with templated rephrasings. *[observed]*
- **Ambiguous/unresolvable target** → `ClarificationOutcome` with a `pending_command` so the next turn resumes. *[observed]*
- **Whisper unavailable / 422 / timeout** → `WhisperAdapterError` → the agent publishes a `transcription_error` data packet; no transcript is fabricated. *[observed: `whisper_adapter.py`, `agent._process_utterance_end`]*
- **No audio buffered at utterance end** → `transcription_error`, never an empty/guessed transcript. *[observed]*
- **Mutation contract** is "no partial apply": a single invalid field aborts the whole command. *[observed]*

---

## 7. Scalability Constraints (summary; full detail in `scalability_and_future_architecture.md`)

- **LLM latency dominates** non-fast-path commands: one synchronous `httpx.post` to Ollama per transcript, blocking the request. The deterministic fast path exists partly to keep common commands off this path. *[observed]*
- **Whisper is GPU-bound** and serialized by `_transcription_lock` in the agent — one utterance transcribed at a time per agent. *[observed: `async with self._transcription_lock`]*
- **Voice is single-room, half-duplex.** One utterance gate, one active utterance id; concurrent multi-user voice is not modeled. *[observed: single `_active_utterance_id`]*
- **DB is single-instance,** SQLite/Postgres via SQLAlchemy; no sharding or read replicas. *[observed: `database_config.py`]*
- **Model quality is the ceiling.** `llama3.2:3b` is structurally safe but semantically weaker; `qwen2.5:7b-instruct` performed better in verification — a 7B+ model is recommended. *[observed: memory `project_phase_single_semantic_extractor`, CONV 21 §11]*
