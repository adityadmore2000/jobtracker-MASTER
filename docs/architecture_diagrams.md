# Architecture Diagrams — `job_tracker`

Mermaid-only. Each diagram reflects **real** dependencies and behavior in the repo, not a generic "frontend → backend → db" sketch. Companion prose: `runtime_flows.md`, `system_design_deep_dive.md`.

---

## 1. Cognitive Architecture (LLM reasoning layer)

How interpretation is isolated from mutation. The model produces understanding; everything downstream is deterministic backend authority.

```mermaid
flowchart TD
    T["Transcript (typed or voice)"] --> CTX["Context block builder<br/>_format_context_block<br/>(selected app as explicit fact, NO ids, NO history)"]
    CTX --> CALL["ONE Ollama /api/chat call<br/>format=json · temperature=0<br/>_SYSTEM_PROMPT contract"]
    CALL --> RAW["Raw JSON string"]
    RAW --> REPAIR["_lift_misplaced_note<br/>(single safe repair: changes.note → top-level note)"]
    REPAIR --> VAL["Pydantic validate<br/>SemanticCommand (extra=forbid)"]
    VAL -->|invalid / non-JSON / timeout| ERR["SemanticExtractorError<br/>→ unsupported_command_response (safe, no mutation)"]
    VAL -->|valid| ENV["SemanticCommand envelope<br/>intent · target(identity only) · changes(mutable only) · note · suggested_phrasings"]

    subgraph COG["COGNITIVE LAYER — interpretation only (ZERO mutation authority)"]
        CTX
        CALL
        RAW
        REPAIR
        VAL
        ENV
    end

    ENV ==>|handed to Execution Layer| PIPE["resolve_semantic_command<br/>(deterministic — owns mutations)"]

    classDef cog fill:#1f2d3d,stroke:#4aa3df,color:#e6f0fa;
    classDef danger fill:#3d1f1f,stroke:#df4a4a,color:#fae6e6;
    class CTX,CALL,RAW,REPAIR,VAL,ENV cog;
    class ERR danger;
```

---

## 2. Runtime Sequence Diagram (text flow)

The short-circuiting ladder of `POST /transcript/parse`, including the cheap rungs that never touch the model.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant FE as Next.js (ChatPanel / AppShell)
    participant API as FastAPI /transcript/parse
    participant CONT as resume_pending_command
    participant FP as try_parse_v2 (fast path)
    participant LLM as Ollama extractor
    participant PIPE as resolve_semantic_command
    participant DSP as dispatch (only DB writer)
    participant DB as Database

    U->>FE: type command
    FE->>API: POST {transcript, context{active_application_id, draft_id, pending_command}}
    API->>CONT: rung 0 — pending continuation
    alt pending resolves
        CONT-->>API: outcome (no LLM)
        API-->>FE: response
    else no pending
        API->>FP: rung 1 — deterministic fast path
        alt MutationPayload
            FP-->>API: payload
            API->>DSP: dispatch (no LLM)
            DSP->>DB: write
            DB-->>DSP: row
            DSP-->>API: MutationResult
            API-->>FE: response
        else ClarificationNeeded
            FP-->>API: question + pending_command
            API-->>FE: clarification
        else ParseMiss
            FP-->>API: ParseMiss
            API->>LLM: rung 2 — ONE call (flag gated)
            alt extractor error
                LLM-->>API: SemanticExtractorError
                API-->>FE: unsupported (safe)
            else SemanticCommand
                LLM-->>API: envelope
                API->>PIPE: rung 3 — validate + DB-verified target
                alt DispatchOutcome
                    PIPE-->>API: MutationPayload
                    API->>DSP: dispatch
                    DSP->>DB: create_draft / create_application_update_draft / append_note / archive
                    DB-->>DSP: row
                    DSP-->>API: MutationResult
                    API-->>FE: draft / pending-change preview
                else Clarification / Suggestion / MixedIntent
                    PIPE-->>API: no-mutation outcome
                    API-->>FE: question or suggestions
                end
            end
        end
    end
```

---

## 3. Voice Pipeline Deep Flow

Browser mic to transcript, with the utterance gate, buffering, serialized Whisper call, and convergence back onto the text endpoint.

```mermaid
flowchart TD
    MIC["Browser mic"] -->|WebRTC Opus track| SFU["LiveKit SFU / room"]
    FE["VoiceButton (FE)"] -->|POST /livekit/token| BE1["Backend mints JWT"]
    FE -->|reliable data: utterance_start{uuid}| SFU
    FE -->|reliable data: utterance_end{uuid}| SFU
    SFU -->|track_subscribed (audio+mic only)| AG["livekit-agent (separate process)"]
    SFU -->|data_received| AG

    subgraph AG_INTERNAL["Agent — utterance gating & buffering"]
        GATE{"recording_gate_open?<br/>active_utterance_id match?"}
        BUF["AudioBuffer<br/>append 16-bit PCM frames<br/>(drops frames when gate closed)"]
        SNAP["snapshot_and_reset → BufferedUtterance.to_wav_bytes()"]
        LOCK["_transcription_lock<br/>(serialize: one utterance at a time)"]
    end

    AG --> GATE
    GATE -->|start: open gate, reset| BUF
    GATE -->|frame & open| BUF
    GATE -->|end & id match: close gate| SNAP
    GATE -->|duplicate start / stale end / no audio| WARN["structured warning<br/>(or transcription_error if no audio)"]
    SNAP --> LOCK

    LOCK -->|GET /asr/hotwords (best-effort)| BE2["Backend hotwords"]
    LOCK -->|POST /transcribe (WAV + hotwords)| WH["whisper-service<br/>faster-whisper CUDA<br/>(lazy locked model load)"]
    WH -->|transcript| OK["publish final_transcript{uuid,text}"]
    WH -->|timeout / 422 / down| ERRP["publish transcription_error{message}"]
    BE2 -. failure → proceed without hotwords .-> WH

    OK -->|reliable data → browser| FE2["FE copies text to command area"]
    FE2 ==>|explicit submit → SAME endpoint| TXT["POST /transcript/parse (see Diagram 2)"]

    classDef proc fill:#1f3d2d,stroke:#4adf8f,color:#e6faf0;
    classDef danger fill:#3d1f1f,stroke:#df4a4a,color:#fae6e6;
    class GATE,BUF,SNAP,LOCK proc;
    class WARN,ERRP danger;
```

---

## 4. State Machine (job application lifecycle)

Every state below is a real persisted condition (`is_draft`, `archived_at`, `ApplicationChangeDraft` existence) with a navigable UI surface. Transitions are labeled with the operation that performs them.

```mermaid
stateDiagram-v2
    [*] --> Draft: create_draft (is_draft=true)
    Draft --> Draft: patch_draft (edit fields/notes)
    Draft --> Active: save_draft (is_draft=false, emit application_saved)
    Draft --> [*]: discard_draft / DELETE /drafts/{id}\n(notes cascade)

    Active --> PendingChange: create_application_update_draft\n(transcript edit → staged delta)
    PendingChange --> PendingChange: patch_application_update_draft
    PendingChange --> Active: apply_application_update_draft\n(delta applied, emit field/status events)
    PendingChange --> Active: discard_application_update_draft\n(saved row unchanged)

    Active --> Active: patch_application (manual form edit — direct)
    Active --> Active: append_note (application_notes)
    Active --> Archived: archive_application (archived_at set)
    Archived --> Active: restore_application (archived_at cleared)
    Archived --> [*]: delete_application_permanently

    note right of PendingChange
        Transcript edits to a SAVED row never
        mutate it directly — they stage a
        reviewable ApplicationChangeDraft.
    end note
    note right of Active
        Direct patch_application is reserved
        for manual form editing only.
    end note
```

---

## 5. Component Dependency Graph (real dependencies)

Actual runtime/network dependencies between the four submodules, the extension, and external local services.

```mermaid
flowchart LR
    subgraph BROWSER["Browser"]
        FE["jobtracker-FE<br/>Next.js :3000<br/>AppShell · Chat/Detail/Applications panels<br/>URL-canonical SelectionContext"]
        EXT["job_tracker-extension<br/>(Chrome MV3)"]
    end

    subgraph LOCAL["Local machine services"]
        BE["jobtracker-BE<br/>FastAPI :8000<br/>extractor · pipeline · dispatcher · CRUD"]
        OLL["Ollama :11434<br/>(local LLM)"]
        AGENT["livekit-agent<br/>(RTC participant process)"]
        WH["whisper-service<br/>faster-whisper CUDA"]
        SFU["LiveKit server / SFU"]
        DB[("Database<br/>Postgres / SQLite")]
    end

    FE -->|POST /transcript/parse, CRUD, /drafts, /livekit/token, /asr/hotwords| BE
    FE -->|publish mic + utterance control| SFU
    FE -->|receive final_transcript / error| SFU
    EXT -->|POST /browser-context| BE

    BE -->|ONE /api/chat per transcript| OLL
    BE -->|SQLAlchemy| DB

    AGENT -->|subscribe audio + data| SFU
    AGENT -->|publish transcript / error| SFU
    AGENT -->|GET /asr/hotwords| BE
    AGENT -->|POST /transcribe| WH

    classDef be fill:#1f2d3d,stroke:#4aa3df,color:#e6f0fa;
    classDef ext fill:#2d2d3d,stroke:#9a8adf,color:#efeafA;
    class BE,OLL,DB be;
    class AGENT,WH,SFU,FE,EXT ext;
```

> **Note on coupling:** the agent depends on the backend *only* for hotwords (best-effort, non-fatal) and on the SFU + Whisper for its core job. The FE talks to the backend for all semantics and to the SFU for media. There is no direct FE↔agent or FE↔Whisper link — voice transcripts arrive via the SFU data channel. *[observed]*
