# Working with jobtracker-MASTER — Submodule Guide

This document explains how the umbrella repository works and what commands to run in your day-to-day development workflow.

---

## The mental model

`jobtracker-MASTER` does **not** contain the source code of the four services. It contains:
- A **pointer** to a specific commit in each service repo
- The shared supporting folders (`docs/`, `evaluation/`, `docker/`, etc.)

Think of a submodule pointer like a sticky note that says:

> "When you check out jobtracker-MASTER, grab commit `13715c17` from jobtracker-BE."

When you do new work inside `jobtracker-BE` and merge it to `main`, the umbrella's sticky note still points to the old commit — until you explicitly update it.

```
jobtracker-MASTER (umbrella)
├── jobtracker-BE  ──► points to commit 13715c17 on jobtracker-BE repo
├── jobtracker-FE  ──► points to commit 289d6bc3 on jobtracker-FE repo
├── livekit-agent  ──► points to commit e4d38897 on livekit-agent repo
├── whisper-service ──► points to commit 47747bfe on whisper-service repo
├── docs/
├── evaluation/
└── ...
```

---

## Day-to-day: working inside a service (most common)

You spend most of your time inside `jobtracker-BE/` or `jobtracker-FE/`. This works exactly like before — the submodule directories are normal git repos.

### 1. Make your changes

```bash
cd jobtracker-BE          # or jobtracker-FE, livekit-agent, whisper-service
# ... edit files ...
git add app/main.py
git commit -m "fix: improve semantic prompt"
git push
```

At this point `jobtracker-MASTER` still points to the old commit. That is fine while you are actively developing.

### 2. Merge your feature branch to main in the service repo

When your feature is ready, merge it to `main` inside the service repo (via GitHub PR or locally):

```bash
# inside jobtracker-BE
git checkout main
git merge fix/semantic-extractor-prompt
git push
```

### 3. Update the umbrella pointer

Go back to the umbrella root and tell it to point at the new `main`:

```bash
cd /home/aditya/dev-work/job_tracker_assistant   # umbrella root

# pull the latest main inside the submodule
git -C jobtracker-BE checkout main
git -C jobtracker-BE pull origin main

# stage the updated pointer in the umbrella
git add jobtracker-BE
git commit -m "chore: update jobtracker-BE to latest main"
git push
```

That's the full loop. The umbrella now points to the new commit.

---

## Cloning fresh on a new machine

```bash
git clone --recurse-submodules git@github-adi:adityadmore2000/jobtracker-MASTER.git
cd jobtracker-MASTER
```

This clones the umbrella **and** checks out every submodule at the pinned commit automatically.

If you already cloned without `--recurse-submodules`:

```bash
git submodule update --init --recursive
```

---

## Starting a new work session (pulling latest everything)

```bash
cd /home/aditya/dev-work/job_tracker_assistant

# Pull umbrella changes (updated pointers, docs, evaluation, etc.)
git pull

# Update all submodules to whatever commit the umbrella now points to
git submodule update --recursive
```

If you also want each submodule to be on the latest `main` of its own remote
(not just the pinned commit), run:

```bash
git submodule update --remote --recursive
# then stage and commit the updated pointers if any changed
git add jobtracker-BE jobtracker-FE livekit-agent whisper-service
git diff --cached --stat    # check what changed
git commit -m "chore: update all submodules to latest main"
git push
```

> **Note:** `--remote` advances to the latest remote `main`. Without `--remote`,
> submodules stay at whatever commit the umbrella currently pins.

---

## Checking what the umbrella is currently pointing to

```bash
git submodule status
```

Output format:
```
 13715c17... jobtracker-BE  (heads/main)
 289d6bc3... jobtracker-FE  (heads/main)
 e4d38897... livekit-agent  (heads/main)
 47747bfe... whisper-service (heads/main)
```

- No prefix → submodule is at the pinned commit, clean.
- `+` prefix → submodule has local commits not yet reflected in the umbrella.
- `-` prefix → submodule has not been initialized yet (run `git submodule update --init`).

---

## Scenario: you made commits in a service but forgot to update the umbrella

```bash
cd /home/aditya/dev-work/job_tracker_assistant
git submodule status
# you see:  +4d0c5a5... jobtracker-BE  (fix/semantic-extractor-prompt)
# the + means the submodule is ahead of what the umbrella pins
```

Fix:

```bash
# make sure the service branch is merged to main and pushed first
git -C jobtracker-BE checkout main
git -C jobtracker-BE pull origin main

# then update the umbrella pointer
git add jobtracker-BE
git commit -m "chore: update jobtracker-BE to latest main"
git push
```

---

## Scenario: working on a feature branch in a service

You never need to tell the umbrella about feature branches. Only update the umbrella pointer when a feature is merged to `main`.

```bash
# inside the service, work normally on your feature branch
cd jobtracker-BE
git checkout -b feature/new-endpoint
# ... work ...
git push origin feature/new-endpoint
# open PR, get it merged to main
# then do the umbrella pointer update described above
```

---

## What NOT to do

| Don't | Why |
|---|---|
| `git add .` from the umbrella root while inside a service | You'll stage service source files directly into the umbrella — the source code belongs in the submodule repo, not here |
| Commit inside a submodule without pushing | The umbrella pointer will reference a commit nobody else can fetch |
| Edit files inside a submodule from the umbrella's git context | Always `cd` into the submodule first so you're using that repo's git |
| Delete and re-clone a submodule to "reset" it | Use `git submodule update --init` instead |

---

## Quick reference

| Task | Command |
|---|---|
| Start fresh session, pull everything | `git pull && git submodule update --recursive` |
| Check what each submodule points to | `git submodule status` |
| Update umbrella to latest main of all services | `git submodule update --remote --recursive` then commit |
| Update umbrella pointer for one service | `git add <service-dir> && git commit -m "chore: update <service>"` |
| Clone with submodules on new machine | `git clone --recurse-submodules git@github-adi:adityadmore2000/jobtracker-MASTER.git` |
| Initialize submodules after plain clone | `git submodule update --init --recursive` |
