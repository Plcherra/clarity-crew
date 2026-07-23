# Clarity Crew — Roadmap

**North-star doc.** Delivery order and "done" criteria. Intent in
[`VISION.md`](VISION.md); design in [`ARCHITECTURE.md`](ARCHITECTURE.md).

Build order is strict-ish: prove the engine and the loop before dressing it in a UI.
Each phase must be usable and **self-tested on a sample repo** (clarity-crew itself
is a fine target) before the next starts. Real-product **dogfooding — pointing the
finished tool at clarity-rex — happens only at the end** (Phase 7), once the whole
loop is built. We build the tool first, then use it to launch real apps.

## Phase 0 — Engine extracted ✅ (done)

- Standalone repo, own git history, on GitHub.
- Provider-neutral LLM client (BYOK), REVIEW / FIX / APPLY-ONLY modes.
- Targets any project via `--repo` (defaults to cwd); tools sandboxed at runtime.
- **Done when:** `--dry-run` against an external project prints correct repo/scope. ✅

## Phase 1 — Planner ✅ (built)

Turn a **plain-English request** — or a preset — plus the project's own docs + code
into a ranked, approval-ready plan.

- Accept a plain-English command (`--goal "save my memory while I talk"`) or a preset
  (`launch-review` / `find-bugs` / `create-feature`); a `--goal` implies `create-feature`.
- **Ask clarifying questions when the request is too vague** before planning (interactive;
  fails open to the request as-is when run non-interactively).
- Auto-detect the project's spec (`README`, `docs/`, `plans/`, requirement-like `*.md`)
  and scan the code for what's missing vs. intent/spec and what's broken.
- Output a structured, ranked plan of **Build / Fix** tasks on the critical path, each
  with a goal + acceptance criteria + constraints + files/area (`reports/launch_plan.md`).
- **`find-bugs`** reuses the existing REVIEW engine (no duplication); **`create-feature`**
  feeds the request into the crew so the plan targets that specific feature.
- **Done when:** given a plain-English request (or preset) on a sample repo (e.g.
  clarity-crew itself) — asking clarifying questions if vague — it produces a valid,
  ranked plan grounded in that repo's real docs + code, with no hardcoding to any one
  app. ✅ (verified on clarity-crew itself: launch-review, find-bugs, and a
  create-feature request all produce grounded plans.)

## Phase 2 — Explainer (plain-English layer)

The cross-cutting translation layer that makes every output understandable to a
non-coder. It comes before the Builder because the plan must be **approved in plain
English**.

- A reusable Explainer applied to every user-facing output (the plan first; later the
  build and test results too).
- Plain, **concrete, structural** language — it names the real pieces AND explains what
  each does and how they connect; never vague, never raw unexplained jargon (see the
  Explainer principle in [`ARCHITECTURE.md`](ARCHITECTURE.md)). Reuse clarity-diff's
  prompt approach.
- **Done when:** any output (starting with the Planner's plan) is rendered in plain,
  concrete, structural language a non-coder can act on — and approval happens on that
  plain-English version.

## Phase 3 — Builder (pluggable)

Apply one approved plan task and return a diff.

- Define a `Builder` interface: `build(task) -> diff + result`.
- Implement **built-in edit-loop** first (reuse the engine's `edit_file`/`run_tests`),
  BYOK GPT/Grok — the default.
- Implement **Cursor adapter** via `cursor-sdk` (`Agent.prompt`, local `cwd`), BYOK
  Cursor key — optional.
- **Done when:** an approved task from a Planner-produced plan (on a sample repo) is
  built by each builder and the diff is captured.

## Phase 4 — Tester (close the loop)

- Run the closest pytest for Python changes; mark non-Python / untested changes
  **APPLIED-UNVERIFIED**; loop back to the Builder on failure (bounded retries).
- The Explainer then renders the result — what changed, why, and whether it's verified
  — tied back to the original request.
- **Done when:** intent → (clarify) → plan → explain → approve → build → test → explain
  runs end-to-end on one item (on a sample repo).

## Phase 5 — Local app shell

- FastAPI server on the user's machine + a simple web UI: an idle chat box + preset
  buttons, run the Planner, review/**approve the plain-English plan**, watch builds,
  read the plain-English explanations.
- BYOK key management (stored locally, never logged).
- **Done when:** the whole loop is usable from the UI with no terminal.

## Phase 6 — Package + funnel

- Wrap the local app as a **Tauri desktop app** (one-click, for non-coders).
- Release **clarity-diff** as the free read-only insight/explain extension that
  funnels users to the paid app.
- Licensing / subscription; confirm Cursor commercial terms in writing before selling.
- **Done when:** a new user can install, connect a key, and get a launch plan.

## Phase 7 — Launch clarity-rex (first real use)

Only now — with the full loop built and self-tested — do we point the finished tool
at a real product. This is the first time clarity-rex is involved; nothing before
this phase depends on it.

- Run the Planner on **clarity-rex**'s real launch blockers: assistant behavior,
  memory/goals saving, voice latency, data retrieval. (Finance/Plaid already works.)
- Drive the approve → build → explain → test loop to actually close those blockers.
- **Done when:** clarity-rex reaches launch using the tool, with a visible,
  verified trail of what changed.

### Dogfood targets (the proof), in order

1. **clarity-rex** — the first real launch.
2. Waiting app #2.
3. Waiting app #3.

Launching these *with* the finished tool is both the validation and the reliability
work that must happen before selling it — but it comes after the tool exists.
