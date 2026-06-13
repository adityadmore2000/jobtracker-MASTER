# Failure Modes & Reliability — `job_tracker`

The system's reliability philosophy, in one line: **fail closed, surface clearly, never corrupt.** A command the system cannot safely resolve becomes a clarification, a suggestion, or an `unsupported` response — it never becomes a guessed mutation, and it never returns HTTP 500 from the transcript path. Tags: **[observed in code]** / **[inferred improvement]**.

---

## 1. LLM Hallucination Handling

Hallucination is the **expected** failure mode for a small local model, so the defenses are layered, not incidental.

| Hallucination | Defense | Result | Source |
|---|---|---|---|
| Invents an unknown JSON key | `extra="forbid"` on `SemanticCommand`/`Target`/`Changes` | whole command rejected → `unsupported` | `semantic_command_schemas.py`, extractor |
| Puts `note` inside `changes` | `_lift_misplaced_note` (single safe repair, only if top-level note empty) | note preserved, command survives | `semantic_command_extractor.py` |
| Emits `intent: "applied"` (a status as intent) | `intent` is a `Literal` of 5 values; prompt explicitly forbids it | validation reject → `unsupported` | schema + `_SYSTEM_PROMPT` |
| Note prose written into `role`/`comments` | identity/mutable separation + mixed-intent rejection | `MixedIntentOutcome` or schema reject | pipeline `_handle_*` |
| Out-of-vocabulary value (`priority: "ultrahigh"`) | enum/alias normalization returns `None` → field marked invalid | `SuggestionOutcome` with rephrasings | `_normalize_and_validate_changes` |
| Fabricated/guessed `application_id` | **never trusted** — every target re-resolved against live DB | clarification if unresolvable | `_resolve_*_target` |
| Copies selected-context company into a new identity | only command-named company → `target`; ids never sent to model | wrong-row mutation avoided | `_format_context_block` |
| Treats a question as a command | prompt rule "questions are not commands" | `unsupported` | `_SYSTEM_PROMPT` |

**Net guarantee:** schema validity is *not* taken as semantic validity. Intent-specific validation and DB-verified targeting catch the class of "type-correct but wrong" outputs that plagued the earlier design (CONV 21 §10.3). *[observed]*

---

## 2. Invalid Intent / Parsing Failures

The transcript ladder degrades gracefully at every rung:

- **Continuation mismatch** (reply doesn't fit the pending question): falls through to normal parsing rather than misapplying. *[observed: `resume_pending_command` returns `None`]*
- **Fast-path `ParseMiss`**: hands off to the extractor; with the flag off, becomes a safe `unsupported`. The legacy dual-output pipeline is never reached from this endpoint. *[observed: `main.py:648`]*
- **Extractor errors** (`SemanticExtractorUnavailableError`, `SemanticExtractorInvalidResponseError` — covering timeout, unreachable Ollama, non-JSON, schema violation): caught in `main.py` → `unsupported_command_response()`. No 500. *[observed]*
- **Ambiguous target** (multiple roles at a company): `ClarificationOutcome` + `pending_command{missing_field}`. *[observed]*
- **Empty/no concrete change**: `SuggestionOutcome` — the system never guesses a field. *[observed: `if not _changes_has_any(...)`]*
- **Mixed field+note**: `MixedIntentOutcome` — asks the user to split. *[observed]*

---

## 3. DB Failure Handling & the Mutation Contract

- **Single writer:** only `dispatch()` mutates application data, over a closed `ALLOWED_OPERATIONS` set — there is one place to reason about transactional safety. *[observed: `mutation_dispatcher.py`]*
- **No partial apply:** a single invalid field aborts the entire command before any write. *[observed: pipeline]*
- **Uniqueness collisions** are not exceptions — `create_draft` returns structured `CollisionInfo` so the UI offers Open / Discard / Restore instead of failing opaquely. *[observed: `mutation_dispatcher.py:216–290`]*
- **Two-stage persistence** for saved rows (`create_application_update_draft` → review → `apply_*`) means a bad transcript edit is reviewable and discardable before it touches the live row. *[observed]*
- **Cascade integrity:** notes and events cascade-delete with their application; ASR correction events `SET NULL` on application delete (audit survives). *[observed: `models.py` relationships, migration `0002`]*
- **[inferred improvement]** The transcript path's defense against *write-time* DB errors (constraint violation outside uniqueness, connection loss mid-transaction) is not explicitly documented here; surfacing those as a clean `error` status (rather than a 500) would be a worthwhile hardening if not already covered by the dispatcher's result mapping.

---

## 4. Voice Packet Loss & Audio Failures

The voice layer is built to never fabricate a transcript:

| Failure | Behavior | Source |
|---|---|---|
| `utterance_end` with no buffered audio | `transcription_error` "No buffered audio"; nothing transcribed | `agent._process_utterance_end` |
| Duplicate `utterance_start` | ignored — `duplicate_utterance_start` warning | `_begin_utterance` |
| `utterance_end` without active utterance / wrong id | ignored — `utterance_end_without_active_utterance` / `stale_utterance_end` | `_end_utterance` |
| Frames arriving while gate closed | dropped (no ambient leak) | `_handle_audio_frame` |
| Inconsistent frame format mid-utterance | `ValueError` → stream failure logged, utterance not corrupted | `AudioBuffer.append_frame` |
| Malformed control data packet | parsed defensively (`invalid_utf8`/`invalid_json`/`unknown_type`/…), ignored | `parse_utterance_control_packet` |
| Reliable data channel | LiveKit `publish_data(..., reliable=True)` for control + transcript | `agent.py` |

**Packet loss specifically:** media uses WebRTC (Opus) over the SFU; control and transcript packets use the **reliable** data channel, so the start/end gating and the final transcript are not lost to transient drops. Lost *media* frames degrade transcription quality but cannot desync the gate, because gating is driven by the reliable control packets, not by audio continuity. *[observed]*

---

## 5. Whisper Service Failures

`WhisperAdapter` maps every failure to a typed `WhisperAdapterError` and a `transcription_error` packet — never a silent or fabricated transcript: *[observed]*

- **Timeout** → "Whisper service request timed out."
- **HTTP 422** → "Whisper service rejected the transcription request."
- **Other HTTP / connection** → "Whisper service unavailable."
- **Invalid/empty transcript body** → "invalid transcript response."
- **Hotword fetch failure** is *non-fatal*: transcription proceeds without hotwords (graceful degradation of accuracy, not an outage). *[observed: `fetch_hotwords` caught, `fallback=without_hotwords`]*
- **CUDA/model init failure** → `TranscriptionError` with an explicit GPU/cuDNN diagnostic message. *[observed: `whisper-service/app/transcription.py`]*

---

## 6. Retry Strategy

**Current state: deliberately retry-free on the critical path.** *[observed]*

- The extractor makes **one** Ollama call and does **not** retry; a malformed response is rejected safely. This was a conscious choice — retries on a non-deterministic small model add latency without guaranteeing correctness, and the safe `unsupported` fallback is cheap. *[observed: `extract_semantic_command_once`; CONV 21 §11 "One-shot semantic repair retry" listed as optional future work]*
- The agent does **not** retry a failed Whisper call within an utterance; it reports the error and lets the user retry by tapping again. *[observed]*
- HTTP clients use explicit per-call timeouts (`settings.timeout_seconds`, `whisper_request_timeout_seconds`) rather than indefinite waits. *[observed]*

**[inferred improvement]** A single bounded "semantic repair" retry (re-prompt with the validation error) could be added behind a flag if malformed-JSON rates prove material in practice — explicitly flagged as optional, not present today.

---

## 7. Fallback Behavior (the safety ladder, bottom-up)

```
Best:    Deterministic fast path resolves → exact mutation, no model.
         ↓ ParseMiss
Good:    Extractor + pipeline resolve → safe mutation (draft / pending-change / note).
         ↓ ambiguous target
Safe:    ClarificationOutcome + pending_command → resumes next turn.
         ↓ invalid / empty / out-of-vocab field
Safe:    SuggestionOutcome → templated rephrasings (validated before display).
         ↓ mixed field + note
Safe:    MixedIntentOutcome → asks user to split.
         ↓ extractor unavailable / invalid / flag off
Floor:   unsupported_command_response() → no mutation, no 500.
```

Every downward step trades capability for safety, and the floor is always "do nothing, explain." Suggestion chips are themselves dry-run-validated before being shown, so a suggestion can never propose an unparseable command. *[observed: `_validate_suggestions` in `main.py`]*

---

## 8. Reliability Summary

| Property | Guarantee | Mechanism |
|---|---|---|
| No corrupt writes | A field error aborts the whole command | atomic `_normalize_and_validate_changes` |
| No LLM-driven writes | Model never picks operation/target/commit | pipeline owns mutation; DB-verified targets |
| No 500 on bad input | Transcript path always returns a structured status | extractor error catch + outcome mapping |
| No fabricated transcripts | Errors surfaced as `transcription_error` | `WhisperAdapter` typed errors |
| Reviewable saved-row edits | Two-stage Pending-Change persistence | `*_application_update_draft` ops |
| Recoverable blocked states | Drafts/archived are navigable; collisions carry actions | `GET /drafts`, `CollisionInfo` |
