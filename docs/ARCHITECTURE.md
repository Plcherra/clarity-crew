# Clarity Crew — Architecture

**North-star doc.** How the system is built. Product intent in
[`VISION.md`](VISION.md); delivery order in [`ROADMAP.md`](ROADMAP.md).

## The Director Loop

```text
 project docs (README, docs/, plans/, *.md) + source code
        │
        ▼
 [1] PLANNER      reads docs + scans code (any language, LLM-read)
        │           → ranked launch plan: "Build X,Y,Z · Fix A,B" on the critical path
        │           → each item = an approval-ready builder prompt (goal + acceptance + constraints)
        ▼
 [2] HUMAN GATE   you review / edit / approve which prompts run          ← trust anchor
        ▼
 [3] BUILDER      runs an approved prompt → writes code → returns a diff
        │           (pluggable: built-in edit-loop OR the user's Cursor via cursor-sdk)
        ▼
 [4] EXPLAINER    "you asked X · the AI did Y · result Z · what could break" (plain English)
        ▼
 [5] TESTER       verify what's verifiable (pytest today); loop back on failure;
        └──────────  mark item done when green, or APPLIED-UNVERIFIED when no test exists
```

## Components

| Component | Role | Status |
| --- | --- | --- |
| **Engine** | Python + CrewAI orchestrator; sandboxed file/search/edit/test tools | exists (extracted from clarity-rex) |
| **Planner** | Doc-aware, project-scanning agent → launch plan + approvable prompts | Phase 1 (next) |
| **Builder (interface)** | Swappable executor that applies one approved task and returns a diff | Phase 2 |
| ↳ built-in edit-loop | LLM + `edit_file`/`run_tests` (already in the engine); BYOK GPT/Grok | Phase 2 |
| ↳ Cursor adapter | Drives the user's Cursor agent via `cursor-sdk` (`Agent.prompt`, local `cwd`) | Phase 2 |
| **Explainer** | Diff → plain-English "asked/did/result/risk" (reuses clarity-diff's approach) | Phase 3 |
| **Tester** | Runs pytest for Python; honest "unverified" for other languages | Phase 3 |
| **App shell** | Local server (FastAPI) + web UI; later a Tauri desktop wrapper | Phase 4–5 |
| **Free extension** | clarity-diff as the read-only insight/explain funnel to the paid app | Phase 5 |

## Model access — BYOK, and what can actually be driven

The engine's `llm_client.py` is provider-agnostic (model chosen by string). Users
supply their own keys. What each provider allows:

| Provider | Driveable with user's key? | Credential |
| --- | --- | --- |
| OpenAI API | Yes | API key (pay-as-you-go — **not** a ChatGPT/Codex subscription) |
| xAI Grok | Yes | API key |
| Cursor | Yes (supported, intended per Cursor) | User API key; **bills the user's own plan**, usage-metered |
| ChatGPT / Codex / Claude Pro subscriptions | No | consumer subs are not API-accessible |

Guardrails from Cursor's terms: don't resell Cursor access under one account, don't
train a competing model on outputs, use the official SDK/CLI (never a proxy). BYOK
(each user's own key) is the compliant path. Confirm commercial specifics with
Cursor sales before selling.

## Form factor (why, and in what order)

The loop must run on the user's machine (files, git, spawning builders/tests), so a
cloud-hosted website can't do it. Order:

1. **Engine (Python, headless)** — the reusable brain. Host-agnostic. *(built)*
2. **Local app** — FastAPI server + web UI on the user's machine (easy UI, full local access).
3. **Desktop app** — Tauri wrapper of the same, for one-click non-coder distribution.
4. **Free extension** — clarity-diff as the insight wedge that funnels to the paid app.

The engine is identical behind any shell, so the shell choice never forces a rewrite.

## Safety guardrails

- **Human approval per build task** (at least until proven), so a mis-scoped plan can't run wild.
- **Verification honesty** — only "verified" when a test actually passed; else "unverified."
- **Bounded builder retries** — fix-forward a few times, then stop; never weaken/delete tests.
- **Sandboxed tools** — every file/edit/test op is confined to the target project root (`--repo`).

## Tech stack

- **Engine:** Python 3.12, CrewAI, LiteLLM (multi-provider), git + pytest tools.
- **Builders:** built-in edit-loop (in-repo); `cursor-sdk` (Python) adapter for Cursor.
- **App shell (later):** FastAPI + a small web UI; Tauri for desktop packaging.
