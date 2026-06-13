# Runtime Flows — `job_tracker`

Two end-to-end traces with decision points, async boundaries, transformation steps, and edge cases. Grounded in the actual code paths. Tags: **[observed in code]** / **[inferred]**.

For visual sequence/pipeline diagrams of the same flows, see `architecture_diagrams.md`.

---

## A. Text Flow — `User → UI → API → LLM → pipeline → DB → response`

Example utterance: **"I applied for AI Engineer at Aiden AI, it's onsite, full-time, high priority."**

### A.1 Step-by-step trace

```
[1] User types in ChatInput (jobtracker-FE/components/chat/ChatInput.tsx)
      └─ submit → ChatPanel builds the request context:
           { transcript,
             context: { active_application_id?, draft_id?, pending_command? } }
         context is derived from SelectionContext, which is derived from the URL route
         (AppShell: routeApplicationId / routeDraftId is the canonical identity).   [observed]

[2] POST /transcript/parse  (jobtracker-BE/app/main.py:610)
      └─ _build_applications_list(db) injected into parser_context for company resolution.

[3] DECISION — rung 0: pending continuation
      resume_pending_command(pending_command, transcript, ctx, db)
        ├─ pending present & resolves → return response   ⟵ exits here on a clarification reply
        └─ None → fall through                               [observed]

[4] DECISION — rung 1: deterministic fast path
      try_parse_v2(transcript, ctx)
        ├─ MutationPayload      → dispatch() → mutation_result_to_public_response()  ⟵ exits (no LLM)
        ├─ ClarificationNeeded  → clarification_needed_response(...)                  ⟵ exits (no LLM)
        └─ ParseMiss            → fall through
      (Our example is conversational/multi-field → ParseMiss.)                        [observed]

[5] DECISION — rung 2: feature flag
      _use_single_extractor()  (USE_SINGLE_SEMANTIC_EXTRACTOR)
        └─ off → unsupported_command_response()  ⟵ safe exit
        └─ on  → continue                                                              [observed]

══════════ ASYNC / NETWORK BOUNDARY #1 ══════════
[6] extract_semantic_command_once(transcript, extractor_context)   ── COGNITIVE LAYER
      └─ ONE httpx.post → local Ollama /api/chat (format=json, temperature=0)
         system prompt + "Context:\n- Selected application: …\nCommand: <transcript>"
      Model returns (interpretation only):
        {
          "intent": "create_application",
          "target": {"company": "Aiden AI", "role": "AI Engineer", "application_id": null},
          "changes": {"status": "applied", "priority": "HIGH",
                      "location_mode": "on-site", "employment_types": ["Full Time"], ...},
          "note": null, "clarification": null, "suggested_phrasings": null
        }
      └─ _lift_misplaced_note (safe repair) → SemanticCommand.model_validate (extra=forbid) [observed]
      Errors here (timeout / non-JSON / schema-invalid) → SemanticExtractorError
         → caught in main.py → unsupported_command_response()  (NO 500, NO mutation)   [observed]
══════════════════════════════════════════════════

[7] resolve_semantic_command(cmd, ctx, db)   ── EXECUTION LAYER (deterministic)
      ├─ _reconcile_location_employment_mixup  (would fold "full-time" out of location if mislaid)
      ├─ _normalize_and_validate_changes
      │     status "applied"→applied · priority "HIGH"→HIGH · location "on-site"→on-site
      │     employment ["Full Time"]→["Full Time"]      (any invalid field ⇒ whole cmd → SuggestionOutcome)
      ├─ _handle_create: company present, note absent → build ApplicationChanges
      │     changes.company="Aiden AI"; changes.role="AI Engineer"
      └─ DispatchOutcome(MutationPayload(operation="create_draft", target=∅, changes=…))  [observed]

[8] dispatch(payload, db)   ── DATA LAYER (the ONLY writer)
      ├─ get_or_create_company("Aiden AI")
      ├─ uniqueness check on (company_id, normalized_role)
      │     ├─ collision → MutationResult(conflict, CollisionInfo{kind, ids})  ⟵ recovery path
      │     └─ clear → INSERT JobApplication(is_draft=True, …)                  [observed]
      └─ MutationResult(success, draft={…})

[9] _outcome_to_response(...) → PublicTranscriptResponse
      └─ FE renders the editable draft preview; user clicks Save → POST /drafts/{id}/save
         (save_draft flips is_draft=False, emits application_saved event)        [observed]
```

### A.2 Key decision points
- **Fast path vs. model** (rung 1): obvious controlled commands never pay LLM latency.
- **Flag gate** (rung 2): with the extractor disabled, `ParseMiss` is *always* a safe `unsupported`, never the legacy dual-output pipeline (which remains in-tree but unreachable from this endpoint). *[observed]*
- **create vs. update**: decided by the model's `intent`, but the *operation* and *target* are decided by the backend in step 7–8.
- **Saved-row edit → Pending Change**: had the example targeted an existing saved application, step 7 would emit `create_application_update_draft`, not a direct write. *[observed]*

### A.3 Edge cases (text)
| Input | Outcome | Path |
|---|---|---|
| "set priority to high" (a row is selected) | `update_application`, inherits selected company, → `create_application_update_draft` | pipeline |
| "set priority to high" (nothing selected) | `ClarificationOutcome` "Which application?" + `pending_command{missing_field: company}` | pipeline |
| "neilsoft" (reply to "Which company?") | continuation fills company, resumes original op | rung 0 |
| "set priority to medium and add a note saying recruiter replied" | `MixedIntentOutcome` — asks to split | pipeline |
| "how many applications do I have?" | model returns `unsupported` → suggestion/none | pipeline |
| "set priority to ultrahigh" | `priority` invalid → `SuggestionOutcome` with rephrasings | pipeline |
| Ollama offline | `unsupported_command_response()` | rung 2 catch |

---

## B. Voice Flow — `Mic → WebRTC → LiveKit → utterance gating → Whisper → transcript → (then Text Flow)`

The voice layer's entire job is to produce a **transcript string**. Once it has one, it re-enters the Text Flow above. There is no separate semantic path for voice.

### B.1 Step-by-step trace

```
[V1] User taps VoiceButton (jobtracker-FE/components/chat/VoiceButton.tsx)
       └─ FE requests a LiveKit token: POST /livekit/token (main.py:468)
          backend mints a JWT (can_publish mic, can_publish_data) → FE joins the room.   [observed]

[V2] FE publishes:
       ├─ microphone track (WebRTC/Opus over LiveKit RTC)
       └─ data packet {"type":"utterance_start","utterance_id": <uuid>}   (reliable)     [observed]

══════════ ASYNC BOUNDARY: separate process — livekit-agent ══════════
[V3] Agent (livekit-agent/agent.py) is already connected (can_subscribe, can_publish_data).
       on track_subscribed (audio + microphone source only):
         rtc.AudioStream.from_track(sample_rate, channels) → _consume_audio_stream task   [observed]

[V4] on data_received → _handle_data_packet → parse_utterance_control_packet
       ├─ utterance_start → _begin_utterance: reset buffer, OPEN recording gate          [observed]
       │     DECISION: gate already open → "duplicate_utterance_start" ignored
       └─ each audio frame → _handle_audio_frame:
             if NOT _recording_gate_open: DROP frame   (audio only buffered while gate open)
             else AudioBuffer.append_frame (validates 16-bit PCM, consistent rate/channels)[observed]

[V5] User taps again → FE sends {"type":"utterance_end","utterance_id": <same uuid>}
       _end_utterance:
         ├─ no active utterance      → "utterance_end_without_active_utterance" (ignored)
         ├─ id mismatch              → "stale_utterance_end" (ignored)
         └─ match → CLOSE gate, snapshot_and_reset() → BufferedUtterance | None          [observed]

[V6] _process_utterance_end (async task):
       ├─ snapshot is None → publish transcription_error "No buffered audio"  ⟵ edge case [observed]
       └─ async with _transcription_lock:   ← SERIALIZES transcription (one at a time)

══════════ ASYNC / NETWORK BOUNDARY: agent → whisper-service ══════════
[V7] WhisperAdapter.transcribe_utterance(snapshot.to_wav_bytes())
       ├─ fetch_hotwords(): GET backend /asr/hotwords  (failure → continue WITHOUT hotwords)[observed]
       └─ POST whisper-service /transcribe  (multipart WAV + hotwords + optional initial_prompt)
            TranscriptionService.transcribe_file: faster-whisper model.transcribe(...)
              lazy/locked model load (CUDA); hotwords joined as comma string             [observed]
            Errors: timeout → "timed out"; 422 → "rejected"; other → "unavailable"
                    → WhisperAdapterError                                                [observed]
═══════════════════════════════════════════════════════════════════════

[V8] Success → publish_final_transcript:
       data packet {"type":"final_transcript","utterance_id","text"} (reliable) back to FE [observed]
     Failure → publish_transcription_error {"type":"transcription_error","message"}        [observed]

[V9] FE receives final_transcript → copies text into the command area.
     (Per product rule, transcript is surfaced and submitted on explicit user action,
      then proceeds exactly as Text Flow step [2] onward.)                                 [observed: README]
```

### B.2 Async boundaries (voice)
1. **Browser ↔ LiveKit SFU** — WebRTC media + reliable data channel.
2. **LiveKit ↔ agent process** — the agent is a *separate* Python process/submodule; track and data callbacks are scheduled with `asyncio.create_task`.
3. **Agent ↔ whisper-service** — HTTP multipart upload; GPU inference.
4. **Agent ↔ backend** — HTTP for `/asr/hotwords`.
5. **Agent → browser** — reliable data packet with the transcript.

### B.3 Concurrency & ordering guarantees
- **One utterance at a time per agent:** `_active_utterance_id` + `_buffer_lock` gate recording; `_transcription_lock` serializes Whisper calls. A second utterance queues (logs `transcription_queue_wait`). *[observed]*
- **Gate-scoped buffering:** frames outside an open utterance are silently dropped — no ambient audio leaks into a transcript. *[observed]*
- **Idempotent control packets:** duplicate `utterance_start`, stale or unmatched `utterance_end` are ignored with structured warnings, not crashes. *[observed]*

### B.4 Edge cases (voice)
| Situation | Behavior | Source |
|---|---|---|
| Tap-end with no audio captured | `transcription_error` "No buffered audio"; nothing fabricated | `_process_utterance_end` |
| Backend hotword fetch fails | transcription proceeds without hotwords (degraded accuracy, not failure) | `whisper_adapter.fetch_hotwords` |
| Whisper 422 (bad request) | `WhisperAdapterError "rejected"` → `transcription_error` | `whisper_adapter` |
| Whisper/GPU down or slow | timeout → `transcription_error "timed out"` | `whisper_adapter` |
| Inconsistent frame format mid-utterance | `AudioBuffer.append_frame` raises `ValueError` → stream failure logged | `audio_buffer.py` |
| Company misrecognized by ASR | (planned) confirm-company correction flow; events recorded in `asr_company_correction_events` | CONV 21 §11 [inferred/planned] |

### B.5 Where voice and text converge
After **[V9]**, voice is indistinguishable from typing: the same `POST /transcript/parse`, the same ladder, the same deterministic mutation control. This convergence is the reason the system has only one semantic safety surface to reason about. *[observed]*
