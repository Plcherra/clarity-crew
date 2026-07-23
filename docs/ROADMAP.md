# Clarity Crew — Roadmap

**North-star doc.** Delivery order and "done" criteria. Intent in
[`VISION.md`](VISION.md); design in [`ARCHITECTURE.md`](ARCHITECTURE.md).

Build order is strict-ish: prove the engine and the loop before dressing it in a UI.
Each phase must be usable (and dogfooded on a real project) before the next starts.

## Phase 0 — Engine extracted ✅ (done)

- Standalone repo, own git history, on GitHub.
- Provider-neutral LLM client (BYOK), REVIEW / FIX / APPLY-ONLY modes.
- Targets any project via `--repo` (defaults to cwd); tools sandboxed at runtime.
- **Done when:** `--dry-run` against an external project prints correct repo/scope. ✅

## Phase 1 — Planner (next)

Turn a project's own docs + code into a ranked, approval-ready plan.

- Auto-detect the project's spec (`README`, `docs/`, `plans/`, requirement-like `*.md`).
- Scan the code for what's missing vs. that spec and what's broken.
- Output `reports/launch_plan.md`: ranked **Build / Fix** items on the critical path,
  each with a plain-English summary **and** a ready-to-run builder prompt (goal +
  acceptance criteria + constraints + files/area).
- **Done when:** running it on clarity-rex's assistant/memory area yields a plan a
  non-coder could act on without writing prompts by hand.

## Phase 2 — Builder (pluggable)

Apply one approved plan item and return a diff.

- Define a `Builder` interface: `build(task) -> diff + result`.
- Implement **built-in edit-loop** first (reuse the engine's `edit_file`/`run_tests`), BYOK GPT/Grok.
- Implement **Cursor adapter** via `cursor-sdk` (`Agent.prompt`, local `cwd`), BYOK Cursor key.
- **Done when:** an approved item from the Phase 1 plan is built by each builder and
  the diff is captured.

## Phase 3 — Explainer + Tester (close the loop)

- **Explainer:** diff → "you asked X · the AI did Y · result Z · what could break."
  Reuse clarity-diff's prompt approach.
- **Tester:** run the closest pytest for Python changes; mark non-Python / untested
  changes **APPLIED-UNVERIFIED**; loop back to Builder on failure (bounded retries).
- **Done when:** plan → approve → build → explain → test runs end-to-end on one item.

## Phase 4 — Local app shell

- FastAPI server on the user's machine + a simple web UI: pick a project, run the
  Planner, review/approve tasks, watch builds, read the plain-English explanations.
- BYOK key management (stored locally, never logged).
- **Done when:** the whole loop is usable from the UI with no terminal.

## Phase 5 — Package + funnel

- Wrap the local app as a **Tauri desktop app** (one-click, for non-coders).
- Release **clarity-diff** as the free read-only insight/explain extension that
  funnels users to the paid app.
- Licensing / subscription; confirm Cursor commercial terms in writing before selling.
- **Done when:** a new user can install, connect a key, and get a launch plan.

## Dogfood targets (the proof)

1. **clarity-rex** — real launch blockers: assistant behavior, memory/goals saving,
   voice latency, data retrieval. (Finance/Plaid is already working.)
2. Waiting app #2.
3. Waiting app #3.

Launching these with the tool is both the validation and the reliability work that
must happen before selling it.
