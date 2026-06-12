# Engineering Session Report

## 1. Session Objective

This session evolved from a narrow frontend usability issue into a broader redesign and hardening effort across the `job_tracker` stack.

The initial question was whether application drafts created through the conversational UI could be manually edited and saved. Investigation showed that manual editing had not actually been implemented. From there, the work expanded into four major areas:

1. Adding a Manual Editing MVP for both drafts and saved applications.
2. Correcting the relational model so that companies are reusable entities and each application represents exactly one role.
3. Adding lifecycle completeness: archive, restore, permanent delete and pending-changes previews for chat-based updates.
4. Hardening the local LLM semantic layer after real runtime testing exposed repeated failures in field extraction, tool selection, fallback routing and malformed tool-call arguments.

The session ended with an architectural realization: the current semantic interpreter contract has become too complex. Before adding more patches, the next step should be a read-only audit comparing the current dual-output LLM pipeline against a simpler structured intent contract.

---

## 2. Starting Context

At the beginning of the session, the project already had a local-first job tracker with:

```
FastAPI backendPostgreSQL persistenceNext.js frontendchat / voice transcript inputdraft creationsaved application rowsarchive / restore backend operations
```

A public backend adapter had also recently been added so that frontend code consumed clean DTOs instead of internal `_json` database fields.

The assumed current frontend contract exposed:

```
roles: string[]employment_types: string[]current_stages: string[]
```

Draft creation through chat worked, but the user observed that clicking the pencil icon on a draft did nothing. This led to a deeper inspection.

The initial assumptions carried forward were:

```
draft editing probably exists but is visually hiddenDetailPanel may exist but be affected by Tailwind layoutpencil icon likely opens an edit stateroles array is acceptable because multi-value preservation was recently hardened
```

Several of these assumptions turned out to be wrong.

---

## 3. User Goal Behind the Work

The broader product goal is a local-first conversational job-tracking assistant where natural speech or typed commands can capture application progress without sacrificing user control.

The desired experience is:

```
speak or type a command→ assistant prepares a safe structured change→ user reviews or edits it→ explicit save / apply action persists it
```

The system must remain:

```
local-firstmanual-review friendlynon-destructivedeterministic after interpretationresistant to accidental duplicate rowsusable through both conversational and manual UI paths
```

This session mattered because the tracker was moving beyond a transcript parser into a real application-management product. Once editing, reapplication, deletion and conversational updates were introduced, the data model and semantic contract had to become more precise.

---

## 4. Obstacles Encountered

### 4.1 Draft pencil icon looked interactive but was not wired

**Symptom**

Clicking the pencil icon on a draft application did nothing.

**Initially suspected**

A Tailwind visibility issue, collapsed panel, `overflow-hidden`, missing provider or row-selection bug.

**Actual root cause**

Manual editing had not been implemented.

The pencil icon on draft rows was decorative only. Draft rows also short-circuited click handling. `DetailPanel` was read-only and no structured REST endpoint existed for patching a draft.

**Why non-obvious**

The UI visually suggested editing existed. `DetailPanel` was mounted and selection plumbing existed for saved rows, making the issue look like a rendering bug rather than a missing feature.

**Boundary**

Frontend UX and backend API surface.

**Resolution**

Implemented a Manual Editing MVP:

```
PATCH /drafts/{id}POST /drafts/{id}/savePOST /drafts/{id}/discardPATCH /applications/{id}
```

Added selectable draft rows, shared form component and four DetailPanel modes.

---

### 4.2 Role cardinality did not match product semantics

**Symptom**

One application row could contain multiple roles:

```
{  "company": "Rockwell",  "roles": [    "AI Engineer Intern",    "Graduate Engineer Trainee"  ]}
```

**Initially suspected**

The correct fix might simply be converting `roles[]` into one scalar role per row.

**Actual root cause**

The deeper issue was relational modeling. Company was stored directly on application rows rather than as a reusable entity.

**Why non-obvious**

A simple scalar-role conversion would still repeat company strings across rows and fail to model company-level reuse cleanly.

**Boundary**

Database schema and product model.

**Resolution**

Locked the relational model:

```
Company = reusable entityApplication = one company-linked, role-specific mutable recordRole = scalar open-ended string
```

Added:

```
companiesjob_applications.company_idjob_applications.role
```

Removed:

```
job_applications.company textroles_jsonroles[]
```

---

### 4.3 Earlier migration direction risked cloning ambiguous data

**Symptom**

A generated migration attempted to split legacy multi-role rows by cloning application records.

**Initially suspected**

Automatic splitting might be the correct way to preserve data.

**Actual concern**

Fields such as:

```
statusjob_linkprioritycommentsnotestimeline events
```

may be role-specific. Blind cloning would assign ambiguous history to multiple applications.

**Why non-obvious**

At the schema level, duplicating rows looks lossless. At the product level, it can fabricate history.

**Boundary**

Database migration and product semantics.

**Resolution**

Rejected blind automatic splitting as a default migration strategy.

The final audit found:

```
14 application rows12 single-role rows2 zero-role drafts0 multi-role rows
```

Therefore no remediation was needed. The schema migration proceeded safely.

---

### 4.4 Existing Alembic migration file had been lost

**Symptom**

The database reported revision `20260610_0007`, but the corresponding migration file no longer existed.

**Initially suspected**

A new migration could simply be added.

**Actual root cause**

Migration history and local schema state had diverged.

**Why non-obvious**

The database itself still contained the migrated schema, so runtime could appear healthy while fresh database setup would fail.

**Boundary**

Infrastructure and schema versioning.

**Resolution**

Recreated `0007` as a coherent historical stub and added a new `0008` companies migration.

Verified:

```
downgrade -1 → upgrade headfresh empty database → upgrade head
```

---

### 4.5 Duplicate company-role rows remained possible

**Symptom**

The UI allowed rows such as:

```
Neilsoft | AI EngineerNeilsoft | AI EngineerNeilsoft | AI Engineer
```

**Initially suspected**

Duplicate rows might be acceptable to represent reapplications.

**Actual decision**

For this product version, one row represents the current mutable state of a company-role pair. Reapply should update the existing row rather than add another row.

**Why non-obvious**

Both models are legitimate:

```
row = current state
```

versus:

```
row = historical application attempt
```

The product needed to choose one explicitly.

**Boundary**

Product semantics and database constraints.

**Resolution**

Added:

```
normalized_roleUNIQUE(company_id, normalized_role)
```

Role normalization:

```
trimcollapse whitespacecasefold
```

Reapply now reuses the existing row.

---

### 4.6 Draft edit collision returned HTTP 500

**Symptom**

Editing a draft into an already-existing company-role pair could hit a DB uniqueness violation and return HTTP 500.

**Initially suspected**

The DB constraint was enough because it prevented duplicates.

**Actual issue**

The DB constraint protected data integrity but did not provide a clean user-facing response.

**Why non-obvious**

The data stayed safe, but UX degraded into a server error.

**Boundary**

Backend route validation and frontend error handling.

**Resolution**

Added pre-flight uniqueness validation to:

```
PATCH /drafts/{id}
```

Now collisions return:

```
HTTP 409 ConflictAn application for X — Y already exists.
```

---

### 4.7 Permanent delete failed because an unrelated Phase 2 table was missing

**Symptom**

Deleting an archived application permanently returned HTTP 500.

**Observed backend error**

```
relation "application_change_drafts" does not exist
```

**Initially suspected**

Permanent delete cascade logic was broken.

**Actual root cause**

The ORM relationship for `application_change_drafts` existed, but the database table had not been created or applied in the running schema.

On `db.delete(app)`, SQLAlchemy lazy-loaded related change drafts before deletion and queried a missing table.

**Why non-obvious**

The failure surfaced in permanent deletion, but the missing table belonged to pending-changes functionality.

**Boundary**

ORM model, Alembic migration and runtime database state.

**Resolution**

Identified ORM/schema drift. The required corrective action was to apply or add the missing migration, or revert prematurely introduced relationships if Phase 2 was incomplete.

---

### 4.8 Chat-based updates needed staging rather than immediate mutation

**Symptom**

Existing saved-row updates through chat required a safe preview workflow.

**Initially suspected**

Existing direct patch operations could be reused.

**Actual product requirement**

Manual form edits and conversational edits have different trust levels.

Manual edits are explicit structured actions. Chat / voice interpretation can be uncertain and should stage changes first.

**Boundary**

Backend workflow, database schema and frontend UX.

**Resolution**

Implemented Phase 2 Pending Changes:

```
chat update→ application_change_drafts row→ delta preview→ Apply Changesor→ Discard Changes
```

Saved row remains unchanged until Apply.

---

### 4.9 Local LLM rejected optional blank fields

**Symptom**

Real `llama3.2:3b` extraction failed with:

```
comments must contain a non-blank string
```

because the model emitted:

```
{  "comments": ""}
```

**Initially suspected**

The LLM was failing to understand the command.

**Actual root cause**

The extraction boundary treated optional blank values as invalid instead of absent.

**Why non-obvious**

Strict validation is desirable before DB mutation, but too-strict validation at the raw extraction boundary rejected otherwise usable proposals.

**Boundary**

LLM output schema and Pydantic validation.

**Resolution**

Added extraction sanitization:

```
blank optional scalar strings → None / droppedblank list entries → filteredempty lists → droppednon-blank values → strictly validated
```

---

### 4.10 Controlled values were misclassified or inconsistently normalized

**Symptoms**

```
update status ... to in-touch→ no changeGoogle AI engineer is onsite location→ Unsupported employment type valueGoogle AI engineer has location onsite→ no change
```

**Initially suspected**

Independent alias gaps.

**Actual root causes**

Multiple semantic-boundary problems:

```
"in-touch" did not normalize to "in_touch""onsite" was extracted as employment type instead of locationcanonical location representation was inconsistent
```

**Why non-obvious**

The LLM often understood the phrase conceptually, but assigned the value to the wrong structured field.

**Boundary**

LLM prompt, normalization and semantic reconciliation.

**Resolution**

Added:

```
separator-tolerant alias normalization"onsite" / "on site" / "on-site" → "on-site""in-touch" / "in_touch" / "in touch" → "in_touch"
```

Added generic safe reconciliation:

```
employment_types=["onsite"]location unset→ move to location="on-site"
```

only when the repair is unambiguous.

---

### 4.11 Open-ended roles allowed controlled values to leak into `role`

**Symptom**

Input:

```
update status of google application to in-touch
```

could produce a draft with:

```
role = "in-touch"
```

**Initially suspected**

Role validation was too permissive.

**Actual tension**

Roles intentionally remain open-ended. Rejecting unknown roles would break valid titles such as:

```
Founding EngineerLLM Inference Optimization Engineer
```

**Why non-obvious**

Open-ended fields create ambiguity: any non-empty string is structurally valid.

**Boundary**

LLM extraction and semantic reconciliation.

**Resolution**

Added explicit field-cue reconciliation:

```
status cue + role="in-touch"→ status="in_touch"→ role cleared
```

Similarly:

```
priority cue + role="low"→ priority="LOW"location cue + role="onsite"→ location="on-site"
```

---

### 4.12 Draft discard commands were mistaken for patches

**Symptoms**

```
discard draft of Aiden AI for founding engineer role→ Draft updated.discard draft fro Aiden AI→ Extracted fields conflicted with selected tool arguments.
```

**Initially suspected**

The discard backend operation was broken.

**Actual root cause**

`discard_draft` was not exposed properly as a semantic tool. The LLM saw company and role and selected `patch_active_draft`.

**Why non-obvious**

Company and role can be either patch values or target selectors depending on intent.

**Boundary**

LLM tool selection and semantic argument shaping.

**Resolution**

Added `discard_draft` semantic tool and treated company / role as target hints for lifecycle operations.

---

### 4.13 Short contextual draft updates no-op’d

**Symptoms**

```
change status to in-touchrole is AI Engineer, change employment type to fulltime
```

did nothing when an active draft already existed.

**Initially suspected**

Alias normalization remained incomplete.

**Actual root cause**

The semantic pipeline returned early when the LLM selected `ask_clarification` or another non-mutation tool. It did not use active draft context to absorb valid extracted field changes.

**Why non-obvious**

The extracted fields could be correct while tool selection was poor.

**Boundary**

Semantic routing and active-draft context.

**Resolution**

Added deterministic active-draft contextual fallback:

```
active draft exists+actionable extracted changes+no lifecycle intent+no explicit saved-row target→ patch_active_draft
```

Also added compact aliases:

```
fulltime → Full Timeparttime → Part Timeintouch → in_touch
```

---

### 4.14 No-tool-call LLM outputs discarded usable fields

**Symptom**

Input:

```
ai engineer role neilsoft company
```

returned:

```
Local language interpreter returned no tool call.No tracker changes were saved.
```

**Initially suspected**

The noun phrase was too incomplete for the model.

**Actual root cause**

The LLM could extract:

```
{  "company": "Neilsoft",  "role": "AI Engineer"}
```

but failed to emit a tool call. The backend discarded usable fields.

**Why non-obvious**

The field extractor and tool selector were separate passes with different failure modes.

**Boundary**

Dual-output LLM contract and fallback routing.

**Resolution**

Added no-tool-call deterministic fallback:

```
company + role, no active draft→ create draftactive draft + actionable fields→ patch draftcompany only→ clarify rolerole only→ clarify company
```

Also added structured few-shot categories to prompts.

---

### 4.15 Explicit create commands leaked into saved-row update path

**Symptoms**

```
add application for ai engineer role at neilsoft→ Application for company "Neilsoft" was not found.add application for ai engineer at neilsoft→ invalid tool arguments
```

**Initially suspected**

The few-shot examples were insufficient.

**Actual root cause**

Routing precedence was incomplete. Explicit create intent did not reliably override saved-row update resolution or malformed tool output.

**Why non-obvious**

The same words can appear in both create and update commands:

```
applicationcompanyrole
```

**Boundary**

Intent classification and routing precedence.

**Resolution**

Locked routing precedence:

```
1. lifecycle intent2. explicit create intent3. explicit saved-row update intent4. active-draft contextual patch5. terse company-role fallback6. clarification7. unsupported / no_change
```

---

### 4.16 Internal fallback diagnostics leaked into the UI

**Symptom**

Input:

```
ai engineer at neilsoft
```

returned:

```
Context note: Resolved via no-tool-call deterministic fallback.
```

**Initially suspected**

Fallback response generation was incorrect.

**Actual root cause**

Internal `context_notes` were appended to public warnings and rendered in the chat panel.

**Boundary**

Backend semantic adapter and UX.

**Resolution**

Stopped surfacing fallback implementation metadata in public warnings. Internal notes remain in logs only.

---

### 4.17 Double-wrapped LLM tool arguments caused HTTP 500

**Symptom**

Input:

```
add application for ai engineer at neilsoft
```

eventually produced a 500.

Observed payload:

```
{  "function": "patch_active_draft",  "args": {    "fields": {      "company": "Neilsoft",      "role": "AI Engineer"    }  },  "fields": {    "company": "Neilsoft",    "role": "AI Engineer"  }}
```

**Initially suspected**

Intent precedence was still wrong.

**Actual root cause**

Tool-call envelope normalization only handled the inner `fields` object. It did not strip outer `function` and `args` keys before strict Pydantic validation.

Retry logic then attempted:

```
dict(arguments.get("fields") or {})
```

on malformed data and raised `ValueError`, causing HTTP 500.

**Why non-obvious**

The LLM output contained correct information, but the wrapper shape was inconsistent.

**Boundary**

Tool-calling contract, canonicalization and retry error handling.

**Resolution**

Added canonical tool-argument normalization supporting:

```
canonical fields shapeargs envelopearguments envelopeduplicate envelope + canonical fieldsconflict rejectionnon-dict rejectionmalformed fields rejection
```

All malformed responses now route to controlled errors or fallbacks rather than HTTP 500.

---

## 5. Approaches Considered

### 5.1 Treat the pencil issue as a CSS bug

**Why it seemed reasonable**

The panel existed and row selection logic partly existed.

**Advantages**

Smallest possible fix if correct.

**Drawbacks**

Would not solve missing editing state, missing form or missing draft REST API.

**Decision**

Rejected after inspection showed the edit flow did not exist.

---

### 5.2 Route manual form edits through natural-language transcript parsing

**Why it seemed reasonable**

Could reuse existing semantic command flow.

**Advantages**

Less API surface.

**Drawbacks**

Brittle and unnecessary:

```
structured form values→ generate sentence→ re-parse sentence
```

**Decision**

Rejected.

**Stable principle**

Manual structured input should call structured APIs directly.

---

### 5.3 Keep `roles[]` on application rows

**Why it seemed reasonable**

The backend had recently fixed multi-value truncation and arrays preserved data.

**Advantages**

Flexible storage.

**Drawbacks**

Ambiguous application-level status, links, stages and notes.

**Decision**

Rejected.

---

### 5.4 Convert `roles[]` to scalar but leave company text denormalized

**Why it seemed reasonable**

Minimal schema change.

**Advantages**

Simpler migration.

**Drawbacks**

Repeats company values, permits spelling drift and complicates company-centric workflows.

**Decision**

Rejected in favor of `companies` table.

---

### 5.5 Auto-split multi-role legacy rows

**Why it seemed reasonable**

Could preserve all roles automatically.

**Advantages**

No manual cleanup.

**Drawbacks**

Could fabricate role-specific history by cloning ambiguous metadata.

**Decision**

Rejected as default. Deferred unless explicitly approved after audit.

---

### 5.6 Allow duplicate company-role rows for reapplication history

**Why it seemed reasonable**

Reapplications can be legitimate historical events.

**Advantages**

Attempt history preserved.

**Drawbacks**

Current UI would fill with accidental duplicates. Product semantics become unclear.

**Decision**

Rejected for the current version.

**Stable principle**

```
one company + one normalized role→ one mutable current-state row
```

Historical attempts may be modeled later as separate events or entities.

---

### 5.7 Add strict DB uniqueness only

**Why it seemed reasonable**

Database prevents duplicate rows reliably.

**Advantages**

Strong integrity.

**Drawbacks**

User-facing collisions become HTTP 500 without pre-flight checks.

**Decision**

Modified.

**Final approach**

DB uniqueness as final safety net plus clean pre-flight `409` responses.

---

### 5.8 Let the LLM mutate the DB directly

**Why it seemed reasonable**

LLM can interpret flexible language better than regex rules.

**Advantages**

Natural language flexibility.

**Drawbacks**

Unsafe. Small-model outputs are inconsistent and malformed.

**Decision**

Rejected.

**Stable principle**

```
LLM proposesvalidator checksdispatcher mutates
```

---

### 5.9 Fix each semantic failure with phrase-specific regexes

**Why it seemed reasonable**

Fastest response to individual runtime bugs.

**Advantages**

Quick local fixes.

**Drawbacks**

Creates brittle patchwork and does not scale.

**Decision**

Rejected repeatedly.

**Adopted alternative**

Generic normalization, reconciliation, routing precedence and fallback rules.

---

### 5.10 Keep extending the dual-output LLM interpreter indefinitely

**Why it initially seemed reasonable**

Existing infrastructure and test coverage were already substantial.

**Advantages**

Incremental fixes preserve working behavior.

**Drawbacks**

Complexity kept growing around:

```
field extractiontool selectionargument mergingretry handlingfallback routing
```

**Decision**

Not yet rejected, but now under serious review.

---

### 5.11 Move toward a simpler semantic contract

Three future options were identified.

#### Option A — Keep current dual-output pipeline

```
field extraction+tool selection+merge
```

**Pros**

Already implemented and heavily tested.

**Cons**

High complexity and fragile merge boundary.

#### Option B — Single intent contract

```
{  "intent": "create_application",  "target": {    "company": "Neilsoft",    "role": "AI Engineer"  },  "changes": {}}
```

**Pros**

Simpler validation and routing. Eliminates tool-call merge complexity.

**Cons**

Requires migration and test churn.

#### Option C — Hybrid

```
simple commands→ deterministic parsercomplex language→ single LLM intent + target + changes contractall paths→ same validator→ same dispatcher
```

**Pros**

Likely best reliability for local small models.

**Cons**

Needs a careful boundary between deterministic and LLM paths.

**Decision**

Deferred to a read-only architecture audit. Option C emerged as the leading candidate.

---

## 6. Decisions Made

### 6.1 Manual form input must bypass transcript parsing

**Decision**

Structured forms call structured REST endpoints directly.

**Reasoning**

Form values are already structured. Re-generating language and re-parsing it adds failure modes.

**Rejected alternative**

Form values → generated sentence → transcript parser.

**Status**

Stable architectural principle.

---

### 6.2 Company is a reusable entity

**Decision**

Add `companies` table and use `company_id` foreign key.

**Reasoning**

Company-centric workflows, grouping, referrals and future metadata require normalization.

**Rejected alternative**

Store company text independently on each application row.

**Status**

Stable principle.

---

### 6.3 One application row represents one company-role pair

**Decision**

Use scalar open-ended `role`.

**Reasoning**

Status, stage, next action and notes belong to one application for one role.

**Rejected alternative**

`roles[]` on one row.

**Status**

Stable principle.

---

### 6.4 One company-role pair maps to one mutable current-state record

**Decision**

Enforce:

```
UNIQUE(company_id, normalized_role)
```

**Reasoning**

Reapplication should update the existing row in the current product version.

**Rejected alternative**

Duplicate rows for historical attempts.

**Status**

Stable for the current version. Historical attempt modeling deferred.

---

### 6.5 Chat updates to saved rows must be staged

**Decision**

Use Pending Changes workflow.

**Reasoning**

Conversational interpretation is less explicit than manual form editing.

**Rejected alternative**

Immediate mutation from chat.

**Status**

Stable principle.

---

### 6.6 Manual saved-row edits remain immediate structured patches

**Decision**

Keep:

```
PATCH /applications/{id}
```

for manual form edits.

**Reasoning**

The user explicitly reviews and submits structured values.

**Rejected alternative**

Route all updates through Pending Changes.

**Status**

Intentional product distinction.

---

### 6.7 Permanent delete remains an archived-view action

**Decision**

Active rows must be archived first. Permanent delete is explicit and irreversible.

**Reasoning**

Separates soft delete from hard delete safely.

**Rejected alternative**

Direct hard delete of active rows or chat-triggered irreversible deletion.

**Status**

Stable principle.

---

### 6.8 LLM output must always be normalized and validated before mutation

**Decision**

Keep deterministic validation boundary.

**Reasoning**

Small local LLM outputs can be blank, misclassified, missing, wrapped inconsistently or structurally malformed.

**Rejected alternative**

Trust LLM tool output directly.

**Status**

Stable principle.

---

### 6.9 Stop adding semantic patches indefinitely

**Decision**

After blocker fixes, run a Semantic Contract Simplification Audit.

**Reasoning**

The current dual-output design has accumulated too many reconciliation layers.

**Rejected alternative**

Keep extending wrappers, examples and fallbacks without architectural review.

**Status**

Required next step.

---

## 7. Architecture Evolution

### 7.1 Data model before normalization

```
erDiagram    JOB_APPLICATIONS {        bigint id        text company        json roles_json        json employment_types_json        json current_stages_json        text status        text priority    }
```

Problems:

```
company repeated on every rowmultiple roles inside one applicationambiguous status and workflow fieldsno company-level entity
```

### 7.2 Data model after normalization

```
erDiagram    COMPANIES ||--o{ JOB_APPLICATIONS : has    JOB_APPLICATIONS ||--o{ APPLICATION_NOTES : contains    JOB_APPLICATIONS ||--o{ APPLICATION_EVENTS : records    JOB_APPLICATIONS ||--o| APPLICATION_CHANGE_DRAFTS : stages    COMPANIES {        bigint id PK        text name        text normalized_name UK    }    JOB_APPLICATIONS {        bigint id PK        bigint company_id FK        text role        text normalized_role        text status        text priority        boolean is_draft        timestamp archived_at    }    APPLICATION_CHANGE_DRAFTS {        bigint id PK        bigint target_application_id FK        json changes_json    }
```

Key invariant:

```
UNIQUE(company_id, normalized_role)
```

---

### 7.3 Draft and saved-row UX before Manual Editing MVP

```
flowchart TD    A[Chat creates draft] --> B[Amber row]    B --> C[Pencil icon visible]    C --> D[No action]
```

### 7.4 Draft and saved-row UX after Manual Editing MVP

```
flowchart TD    A[Chat creates draft] --> B[Amber draft row]    B --> C[Select draft]    C --> D[DetailPanel draft-edit mode]    D --> E[Save Draft Changes]    D --> F[Save Application]    D --> G[Discard Draft]    H[Saved row] --> I[Read-only DetailPanel]    I --> J[Edit]    J --> K[Save Changes]    I --> L[Archive]
```

---

### 7.5 Saved-row chat update workflow after Phase 2

```
flowchart TD    A[Chat update command] --> B[Semantic interpretation]    B --> C[application_change_drafts row]    C --> D[Pending badge]    D --> E[DetailPanel delta preview]    E --> F[Apply Changes]    E --> G[Discard Changes]    F --> H[Saved application updated]    G --> I[Saved application unchanged]
```

---

### 7.6 Semantic pipeline before hardening

```
flowchart TD    A[Transcript] --> B[LLM field extraction]    A --> C[LLM tool selection]    B --> D[Merge]    C --> D    D --> E[Validate]    E --> F[Dispatcher]
```

Problem:

```
two independently fallible LLM outputs→ fragile merge boundary
```

### 7.7 Semantic pipeline after accumulated hardening

```
flowchart TD    A[Transcript] --> B[LLM extraction]    A --> C[LLM tool selection]    B --> D[Blank sanitizer]    C --> E[Envelope canonicalization]    D --> F[Controlled-field reconciliation]    F --> G[Explicit field-cue reconciliation]    E --> H[Argument merge]    G --> H    H --> I[Canonical normalization]    I --> J[Strict validation]    J --> K[Intent precedence]    K --> L[No-tool-call fallback]    L --> M[Invalid-args fallback]    M --> N[Active-draft contextual fallback]    N --> O[Target resolution]    O --> P[Dispatcher]
```

This pipeline is safer but increasingly difficult to reason about.

---

### 7.8 Candidate future simplified architecture

```
flowchart TD    A[Transcript] --> B{Simple known command?}    B -->|Yes| C[Deterministic parser]    B -->|No| D[Single LLM JSON intent contract]    C --> E[Canonical validator]    D --> E    E --> F[Deterministic router]    F --> G[Dispatcher]
```

Candidate LLM output:

```
{  "intent": "update_saved_application",  "target": {    "company": "Google",    "role": "AI Engineer"  },  "changes": {    "status": "rejected"  }}
```

---

## 8. Implementation Progress

## Completed Implementation

### Manual Editing MVP

Added:

```
PATCH /drafts/{id}POST /drafts/{id}/savePOST /drafts/{id}/discardPATCH /applications/{id}
```

Frontend changes included:

```
SelectionContext draft supportclickable draft rowsreal draft pencil actionApplicationForm shared across draft and saved editsDetailPanel modes A–D
```

Reported test state:

```
Backend:  328 passedFrontend: 239 passedBuild:    passing
```

---

### Schema normalization

Added:

```
companies tablecompany_id FKrole scalarnormalized_roleUNIQUE(company_id, normalized_role)company_resolution.pyrole_resolution.py
```

Updated public DTO:

```
type Application = {  company: string;  role: string;  employment_types: string[];  current_stages: string[];  ...}
```

Removed public exposure of:

```
rolesroles_jsoncompany_idnormalized_role
```

Reported test state after normalization:

```
Backend:  358 passedFrontend: 235 passedBuild:    passingMigration round-trip: clean
```

---

### Duplicate prevention and reapply semantics

Added:

```
normalize_role_name()find_application_by_company_role()UNIQUE(company_id, normalized_role)
```

Reapply behavior:

|Existing state|Result|
|---|---|
|No row|Create draft|
|Draft|Reuse draft|
|Applied|Truthful no-op|
|Rejected / `in_touch` / blank|Set `applied`|
|Archived|Restore and set `applied`|
|Accepted|Clarification|

Reported test state:

```
Backend:  385 passedFrontend: 241 passed
```

---

### Permanent delete and draft-collision handling

Added:

```
draft PATCH pre-flight uniqueness checkDELETE /applications/{id}Delete Permanently UI confirmation
```

Delete behavior:

|State|Action|
|---|---|
|Active saved row|Archive only|
|Archived saved row|Restore or Delete Permanently|
|Draft|Discard Draft|

Related cleanup:

```
ApplicationNote → cascade deleteApplicationEvent → cascade deleteAsrCompanyCorrectionEvent.application_id → SET NULLCompany row → preserved
```

Reported test state:

```
Backend:  403 passedFrontend: 252 passedBuild:    passing
```

---

### Pending Changes workflow

Backend:

```
application_change_drafts tablepending changes createpending changes patchapplydiscard
```

Frontend:

```
ApplicationChangeDraft typepending transcript statusespending-change selection statePending badgeDetailPanel Mode EApply ChangesDiscard Changes
```

Reported test state:

```
Backend:  431 passedFrontend: 262 passed
```

---

### Semantic hardening passes

#### Phase 2A

Added:

```
open-ended role prompt guidancestatus alias normalizationlocation canonicalizationcontrolled-field reconciliationarchive tooldelete-policy guidance
```

Reported:

```
Backend: 467 passedFrontend: 262 passedBuild:    success
```

#### Blank-field sanitizer

Added:

```
_sanitize_extracted_fields_dict()optional blank string → absentblank list entries → removed
```

Added:

```
28 regression tests
```

#### Phase 2A.1

Added:

```
discard_draft semantic toolfield-cue reconciliationlifecycle target hints
```

Reported:

```
Focused: 42 passedBackend: 537 passed
```

#### Phase 2A.2

Added:

```
active-draft contextual fallbackcompact aliasesmulti-field patch preservationdraft no-op detection
```

Reported:

```
Focused: 51 passedBackend: 589 passed
```

#### Phase 2A.3

Added:

```
no-tool-call recoverystructured few-shot categoriesdeterministic fallback
```

Reported:

```
Focused: 31 passedBackend: 620 passed
```

#### Phase 2A.4

Added:

```
explicit create-intent precedenceinvalid-argument fallbackwrong-tool interceptionpublic diagnostic suppression
```

Reported:

```
Focused: 38 passedBackend: 660 passed
```

#### Phase 2A.5

Added:

```
canonicalize_tool_arguments()safe envelope unwrappingstrict conflict rejectiondefensive merge shape checksshared first-attempt / retry / fallback canonicalizationHTTP 500 elimination for malformed semantic arguments
```

Reported:

```
Focused: 29 passedBackend: 689 passed
```

---

## Planned but Not Completed

```
real llama3.2:3b runtime verification after backend restartexact live payload trace if blocker persistsSemantic Contract Simplification Auditdecision on dual-output vs single-contract vs hybrid architecture
```

---

## 9. Validation and Evidence

### Automated validation progression

|Milestone|Backend tests|Frontend tests|Build|
|---|---|---|---|
|Manual Editing MVP|328|239|Passing|
|Schema normalization|358|235|Passing|
|Duplicate prevention|385|241|Passing|
|Permanent delete|403|252|Passing|
|Pending Changes|431|262|Passing|
|Phase 2A|467|262|Passing|
|Phase 2A.1|537|Not touched|Not touched|
|Phase 2A.2|589|Not touched|Not touched|
|Phase 2A.3|620|Not touched|Not touched|
|Phase 2A.4|660|Not touched|Not touched|
|Phase 2A.5|689|Not touched|Not touched|

### Manual utterances that exposed failures

```
add aiden ai application for founding engineerupdate status of ai engineer application at google to in-touchdelete google application for AI Engineer rolearchieve Google application having AI Engineer roleset the priority of google application to lowGoogle AI engineer is onsite locationGoogle AI engineer has location onsitediscard draft of Aiden AI for founding engineer rolediscard draft fro Aiden AIchange status to in-touchrole is AI Engineer, change employment type to fulltimeai engineer role neilsoft companyadd application for ai engineer role at neilsoftadd application for ai engineer at neilsoftai engineer at neilsoft
```

### Important observed backend failures

Blank optional field rejection:

```
comments must contain a non-blank string
```

Missing ORM table during permanent deletion:

```
relation "application_change_drafts" does not exist
```

Malformed tool-call arguments:

```
function  Extra inputs are not permittedargs  Extra inputs are not permitted
```

Unsafe retry merge:

```
ValueError: dictionary update sequence element #0 has length 1; 2 is required
```

### Runtime verification limitation

Many later reports explicitly stated:

```
Not yet run against live llama3.2:3b
```

Automated tests injected mocked or fake interpreter outputs. Therefore, test coverage proves downstream behavior for reproduced failure shapes, but not full live-model reliability.

This distinction is central to the next step.

---

## 10. Lessons Learned

### 10.1 Visual affordances can hide missing product flows

A pencil icon suggested editing existed, but it was decorative. UI review must distinguish:

```
visible affordance
```

from:

```
wired interaction contract
```

---

### 10.2 Manual structured input should remain structured

Generating text from form values and re-parsing it would have created unnecessary fragility.

Reusable principle:

```
manual form→ structured APInatural language→ semantic interpretation→ same dispatcher
```

---

### 10.3 Data modeling decisions should reflect product semantics, not parser convenience

Keeping `roles[]` was technically flexible but conceptually wrong. The correct model emerged only after asking:

```
What does one tracker row represent?
```

The answer:

```
one current application for one company-role pair
```

---

### 10.4 Migration logic must not invent history

Automatic row cloning can preserve values while corrupting meaning. Legacy migration should be conservative when domain semantics are ambiguous.

---

### 10.5 Database constraints and UX validation serve different purposes

```
DB uniqueness→ data integritypre-flight validation→ user-facing clarity
```

Both are required.

---

### 10.6 Strict validation must be placed at the correct boundary

Rejecting blank optional fields at extraction time made valid commands fail. The correct pattern is:

```
raw model output→ sanitize harmless empties→ normalize→ strictly validate meaningful values
```

---

### 10.7 Open-ended fields require contextual reconciliation

Role titles must remain flexible. That means the schema alone cannot catch mistakes such as:

```
role = "in-touch"
```

when the user explicitly said:

```
status
```

Context-aware reconciliation is necessary.

---

### 10.8 Small local LLMs need prompt guidance and deterministic fallback

Few-shot examples improve behavior, but they are not safety guarantees.

Reliable local AI systems need:

```
prompt guidance+strict schemas+generic normalizers+deterministic fallback+controlled dispatcher
```

---

### 10.9 Dual-output LLM contracts create merge complexity

The biggest architectural lesson was that asking the LLM to separately provide:

```
fieldstool + arguments
```

created many classes of inconsistencies.

Most semantic bugs came from reconciling two imperfect outputs.

---

### 10.10 Automated tests cannot replace live-model smoke testing

Mocks reproduced known shapes, but the real model emitted new structures and routing failures. Every semantic release should include a compact live utterance suite.

---

## 11. Open Questions and Deferred Work

## Required Next Steps

### 11.1 Restart backend and verify live Phase 2A.5 behavior

Run:

```
cd ~/dev-work/job_tracker_assistant/jobtracker-BEpkill -f "uvicorn".venv/bin/uvicorn app.main:app --reload
```

Test:

```
add application for ai engineer at neilsoft
```

Expected:

```
Draft created. Review it and save when ready.
```

### 11.2 Capture exact live trace if create still fails

Log:

```
raw Ollama responseparsed proposalraw argumentscanonicalized argumentsextracted fieldsmerged proposalfallback pathfinal handlerpublic response
```

### 11.3 Run a Semantic Contract Simplification Audit

Compare:

```
A. current dual-output architectureB. single intent + target + changes contractC. hybrid deterministic parser + single LLM contract
```

The audit should be read-only first.

---

## Optional Enhancements

```
structured clarification choices in frontendcompany cleanup UI for unreferenced company rowshistorical application-attempt modelingbroader local-LLM evaluation corpusbetter base local model experimentstructured semantic metrics and tracing dashboard
```

---

## Deferred Ideas

```
chat-based permanent deletebulk multi-role creationfuzzy company matchingautomatic company deletionhistorical attempt rows
```

---

## Explicitly Rejected for Now

```
phrase-specific regex patchworkloosening Pydantic schemas with extra="allow"blind auto-splitting of ambiguous multi-role rowsdirect LLM-to-DB mutationmanual form edits routed through transcript parsing
```

---

## Questions Requiring Investigation

1. Does Phase 2A.5 fully fix the live create flow after backend restart?
2. Is the current dual-output interpreter still maintainable?
3. Can the local model reliably emit one strict `intent + target + changes` JSON object?
4. Which common commands should bypass LLM entirely through deterministic parsing?
5. Should `discarded` receive a dedicated stable public transcript status rather than mapping through `no_change`?
6. How much of the existing 689-test suite can be reused if the interpreter contract changes?

---

## 12. Significance in the Overall Project Journey

This session was primarily:

```
foundational data-model redesign+UX completion milestone+semantic-layer debugging breakthrough+architectural warning signal
```

It moved the project from a transcript-driven prototype toward a safer application-management system with:

```
manual reviewnormalized relational datastable lifecycle operationsduplicate preventionstaged conversational updatesdefensive local-LLM handling
```

It also revealed a key limitation of the current semantic architecture. The system now has extensive defensive logic, but the dual-output LLM contract is becoming costly to maintain.

The most important outcome is not merely that many bugs were fixed. It is that the project reached the point where the next improvement should be simplification, not more patches.

---

## 13. Compact Timeline Entry

**Milestone:**  
Manual Editing MVP, normalized company-role schema, permanent-delete lifecycle, Pending Changes workflow and local-LLM semantic hardening.

**Problem:**  
The tracker lacked real manual editing, modeled multiple roles ambiguously, allowed duplicate company-role rows and produced unreliable conversational mutations with `llama3.2:3b`.

**Key obstacle:**  
The semantic layer asked the LLM to independently extract fields and select tools, then merged both outputs. This caused wrong-field placement, no-tool-call failures, malformed wrappers, retry crashes and create-versus-update routing leakage.

**Decision:**  
Normalize the relational model, stage conversational saved-row updates, enforce deterministic validation and fallbacks, eliminate semantic HTTP 500s, then stop adding patches and audit a simpler semantic contract.

**Outcome:**  
Backend suite reached `689 passed`, frontend editing and lifecycle workflows were implemented, DB constraints became explicit and known malformed LLM outputs no longer escape as HTTP 500.

**Next step:**  
Verify the core live command `add application for ai engineer at neilsoft` after backend restart, capture an exact runtime trace if it still fails, then perform a read-only Semantic Contract Simplification Audit comparing dual-output, single-contract and hybrid designs.