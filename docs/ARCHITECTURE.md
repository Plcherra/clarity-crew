# Clarity Crew — Architecture

**North-star doc.** How the system is built. Product intent in
[`VISION.md`](VISION.md); delivery order in [`ROADMAP.md`](ROADMAP.md).

## The Director Loop

The loop starts from what the **user says in plain English** — a free-text command
("I want the assistant to save memory while I talk") or a preset button — not from a
code scan. The **Explainer** is not a single step at the end: it is a layer that
translates *every* user-facing output (the plan, the build, the test) into plain,
concrete language, so the human always understands what they're approving and what
happened.

```text
 USER INTENT   free text ("save my memory while I talk") OR a preset button
        │        (launch-ready review · find bugs · create feature)
        ▼
   too vague? ──yes──▶ [PLANNER asks clarifying questions] ──▶ back to intent
        │no
        ▼
 [1] PLANNER      intent + project docs (README, docs/, plans/, *.md) + code
        │           → a concrete code plan: ranked Build/Fix items on the critical
        │             path, each an approval-ready builder task (goal + acceptance
        │             + constraints + files/area)
        ▼
 [*] EXPLAINER    renders the plan in plain, concrete, structural language
        ▼           (this is what the human reads)
 [2] HUMAN GATE   you approve / edit / reject the PLAIN-ENGLISH plan     ← trust anchor
        ▼
 [3] BUILDER      runs an approved task → writes code → returns a diff
        │           (pluggable: built-in edit-loop OR the user's Cursor via cursor-sdk)
        ▼
 [4] TESTER       verify what's verifiable (pytest today); loop back on failure;
        │           mark item done when green, or APPLIED-UNVERIFIED when no test exists
        ▼
 [*] EXPLAINER    "here's what your app's X looks like, what I changed and why, and
        └──────────  whether it's verified" — tied back to what you originally asked
```

`[*] EXPLAINER` marks where the cross-cutting plain-English layer runs: once to make
the plan approvable, and again to report the result. It is used by the Planner,
Builder, and Tester alike — it is a function, not a phase.

**Presets map to capabilities:** *find bugs* → the existing review engine;
*launch-ready review* → the Planner's spec + code scan; *create feature* → the
plain-English intent → code-plan path. All three produce plans the Explainer renders.

## Components

| Component | Role | Status |
| --- | --- | --- |
| **Engine** | Python + CrewAI orchestrator; sandboxed file/search/edit/test tools | exists (extracted from clarity-rex) |
| **Planner** | Turns a plain-English command or preset (launch-ready review / find bugs / create feature) — asking clarifying questions when vague — plus the project's docs + code into a concrete plan of approvable tasks | Phase 1 (built) |
| **Explainer** | Cross-cutting plain-English *layer*: renders every user-facing output (plan, build, test) in concrete, structural language; the plan is approved in this form | Phase 2 |
| **Builder (interface)** | Swappable executor that applies one approved task and returns a diff | Phase 3 |
| ↳ built-in edit-loop | LLM + `edit_file`/`run_tests` (already in the engine); BYOK GPT/Grok — the default | Phase 3 |
| ↳ Cursor adapter | Drives the user's Cursor agent via `cursor-sdk` (`Agent.prompt`, local `cwd`); optional | Phase 3 |
| **Tester** | Runs pytest for Python; honest "unverified" for other languages | Phase 4 |
| **App shell** | Local server (FastAPI) + web UI (idle chat + presets, approve, watch, read explanations); later a Tauri desktop wrapper | Phase 5 |
| **Free extension** | clarity-diff as the read-only insight/explain funnel to the paid app | Phase 6 |

## The Explainer principle (plain, concrete, structural)

The Explainer's job is to give a **non-coder a real mental model of their own app** —
in plain English, but **concrete and structural**. It is never vague, and it never
just names files or functions without explaining what they are and how they connect.

- **Target style:** "Your app's memory system is structured like this: these files
  take what you say, these decide what's worth keeping, and these save it — together
  they're the 'memory' path. To make the assistant save memory while you talk, I added
  a file that catches your request mid-conversation and hands it to the save path, and
  now memory saves as you talk."
- **Anti-example A — too vague:** "We added the part that catches your request and
  saves it. It now works." (says nothing real; the user learns nothing about their app)
- **Anti-example B — raw jargon, unexplained:** "`auto_suggestions_gate.py` only let
  Off-mode thread actions through when explicitly marked; split thread logic into
  `open_thread_matching.py` (match by `thread_id`)." (names files/functions without
  ever explaining what they are or how they fit — the exact failure that left a
  non-coder asking "matching what with what?" and "what are all these .py files doing?")

**Rule:** name the real pieces **and** explain what each one does and how they connect;
give the user a mental model of their app; always tie the explanation back to what the
user asked for. This principle applies wherever the Explainer runs — plan, build, test.

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
