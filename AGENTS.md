# Job Tracker Project Constraints

This repository implements `job_tracker`, a local-first, voice-driven job application tracker. Follow these constraints for every phase.

## Core Product Rules

- Preserve the user's existing spreadsheet-style workflow.
- The tracker table is the source of truth.
- Do not add timelines, event sourcing, derived workflow stages, or automatic workflow transitions.
- `Current Stage` is a manually stored multi-select field.
- Never infer `Current Stage` from comments, status, or any other field.
- `Comments` and `Next Action` are explicit user-provided text fields.
- AI may clean dictated text only when explicitly requested, but it must not invent comments, actions, assumptions, or facts.
- Never generate or infer `Next Action` automatically.
- Never change `Engaged (# OF DAYS)` automatically.
- Do not add auto-apply functionality.
- Do not automate LinkedIn actions, messages, connections, or job-form submission.
- Do not hardcode job portals.
- Require preview and explicit user save before creating or updating tracker rows.
- Keep the overview assistant read-only.
- Do not add future-phase features unless explicitly requested in the current prompt.

## Visible Tracker Columns

| Column | Type |
|---|---|
| Company | string |
| Role | multi-select of free-form strings |
| Type | multi-select |
| JOB LINK | URL string |
| LOCATION | single-select |
| STATUS | editable string |
| Current Stage | multi-select |
| PRIORITY | single-select |
| ENGAGED (# OF DAYS) | editable integer |
| NEXT ACTION | free text |
| COMMENTS | free text |

## Allowed Values

### Role

Roles are **free-form strings**. There is no fixed role enum and no backend
validation against a closed list — any non-blank string is a valid role.

The values below are **suggestions / UI and ASR-hotword hints only** and are not
enforced. The frontend may surface them as quick-pick options, but users can enter
any role title:

- AI Engineer
- Generative AI Engineer
- GenAI Engineer
- LLM Engineer
- RAG Engineer
- AI Systems Engineer
- ML Engineer
- Computer Vision Engineer
- Agentic AI Engineer
- Data Science
- Prompt Engineer
- Platform Engineer
- GET
- AI Product Engineer

### Type

- Internship
- Full Time
- Part Time

### LOCATION

- remote
- hybrid
- onsite

### Current Stage

- Tailored
- Applied
- Networked
- Engaged
- COLD_MAIL
- Followed up

### PRIORITY

- LOW
- MEDIUM
- HIGH
