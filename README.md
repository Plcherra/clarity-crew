# Clarity Crew

Build software by describing what you want in plain English. Clarity Crew reads your
project, turns your request into a concrete plan, explains that plan back in plain
language — and, once you approve, drives an AI coding agent to build and verify each
piece, then explains what it did. You direct in plain English; it plans, explains,
builds, and checks.

**Start here (north-star docs):**

- [`docs/VISION.md`](docs/VISION.md) — what it is, who it's for, MVP, non-goals.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the Director Loop, pluggable builder, BYOK.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — phased delivery (the **Planner is built**; the **Explainer** plain-English layer is next).

The rest of this file documents what runs today: the **Planner** (Phase 1) and
the underlying **review engine** (Phase 0).

---

## Planner (Phase 1)

The Planner is step 1 of the Director Loop. You tell it what you want — a
**plain-English request** or a **preset** — and it uses the project's **own docs +
code** to write a ranked, approval-ready plan to `reports/launch_plan.md`. Planning
is **read-only — it never edits your files.**

**Presets (what you want it to do):**

- **`launch-review`** (default) — what does this project need to reach launch?
- **`find-bugs`** — hunt for real bugs (delegates to the review engine below).
- **`create-feature`** — deliver a plain-English request; if the request is vague it
  **asks you clarifying questions first** (interactive), then plans it.

Three planner agents run in sequence (read-only tools only):

1. **SpecReader** — auto-detects the project's spec (`README`, `docs/`, `plans/`,
   requirement-like `*.md` via `find_spec_docs`) to understand the app / place your
   request.
2. **CodeScanner** — scans the code for **BUILD** items (missing / stubbed / half-wired)
   and **FIX** items (present-but-broken on real paths), each with `file:line` evidence.
3. **PlanWriter** — writes the ranked plan: each item gets a plain-English summary
   **and** a ready-to-run builder task (goal + acceptance criteria + constraints +
   files/area + how to verify).

```powershell
.\plan.ps1                                            # launch review of the repo (cwd)
.\plan.ps1 src                                        # focus the scan on one folder/area
.\plan.ps1 --preset find-bugs                         # hunt for bugs (review engine)
.\plan.ps1 --goal "let users reset their password"    # create-feature (asks if vague)
.\plan.ps1 --preset create-feature --goal "..." --repo C:\path\to\project
```

| Flag | Meaning | Default |
| --- | --- | --- |
| *(scope path)* | optional folder to focus the code scan on | whole repo |
| `--goal "<text>"` | a plain-English request (implies `create-feature`) | — |
| `--preset <name>` | `launch-review` \| `find-bugs` \| `create-feature` | `launch-review` (or `create-feature` if `--goal` given) |
| `--repo <path>` | target another project (defaults to cwd) | cwd |
| `--dry-run` | print the config and exit, spend nothing | off |

Output: `reports/launch_plan.md` (plus a timestamped archive). Set `PLANNER_MODEL`
in `.env` to give the Planner a stronger reasoner than the reviewer's `MODEL`.

> The plan describes **what to do next** — nothing in it has been built or verified.
> That's the trust anchor: you review/edit/approve items, then a builder (Phase 3)
> runs an approved item and returns a diff.
>
> The Planner already takes a plain-English **`--goal`** or a **preset**
> (`launch-review` / `find-bugs` / `create-feature`) and asks **clarifying questions**
> when a request is vague. Coming next per the [roadmap](docs/ROADMAP.md): an
> **Explainer** layer (Phase 2) that renders the plan in plain, concrete language so
> you approve *that* — not raw prompts.

---

## Review engine (current)

A focused CrewAI team that **reviews** a codebase and can **optionally fix** it.
Point it at any project with `--repo` (defaults to the current folder). Two modes.

**REVIEW mode (default — cheap, no file edits):**

1. **ProjectReader** — maps the code in scope: key files, responsibilities, how they connect.
2. **BugFinder** — hunts for real bugs, risks, and Clarity-rule violations (cites file:line).
3. **FixSuggester** — writes a concrete fix report to `reports/review_latest.md`.

**FIX mode (opt-in with `--fix` — edits files, one pass by default):**

4. **FixApplier** — applies each suggested fix, runs the closest tests, and marks each
   issue FIXED / REVERTED (reverts exactly if its tests fail).

The first three agents get **read-only** tools (`list_directory`, `read_file`,
`search_code`), sandboxed to the repo root. **FixApplier** additionally gets
`edit_file` (exact, unique-match, syntax-checked), `run_tests` (pytest via the
backend's own venv), and `restore_file` (byte-exact revert).

> 💸 **Cost warning:** FIX mode is much more expensive than REVIEW — it re-reads
> code and runs the model in a tool loop, and on already-clean code it often
> proposes marginal/no-op changes that then get reverted. **Start with REVIEW.**
> Only add `--fix` when you actually expect bugs, and keep the scope narrow.

> ⚠️ FIX mode edits your source files. **Run it on a clean git branch** and review
> the full diff (`git diff`). It will not run multiple passes unless you pass
> `--rounds N`.

## Setup

```powershell
cd crew
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env       # then edit .env and add your API key
```

By default it uses **OpenAI `gpt-4o-mini`** for analysis (add your `OPENAI_API_KEY`
to `.env`). Set `APPLIER_MODEL=gpt-4o` so the FixApplier is strong enough to
iterate on test failures.

The provider is **auto-detected from the `MODEL` string**, so you can switch
freely — just set `MODEL` and the matching key in `.env`:

| `MODEL` | Provider | Key needed | Notes |
| --- | --- | --- | --- |
| `gpt-4o-mini` | OpenAI | `OPENAI_API_KEY` | cheap/fast, **most reliable in CrewAI's tool loop** (default) |
| `gpt-4o` | OpenAI | `OPENAI_API_KEY` | strongest; best for `APPLIER_MODEL` |
| `xai/grok-3` | xAI | `XAI_API_KEY` | non-reasoning, usable |
| `xai/grok-3-mini` | xAI | `XAI_API_KEY` | reasoning model; can return empties (not recommended) |

## Run

Recommended workflow — **review → prune → apply-only** — so you never pay to
apply fixes you don't want:

```powershell
# 1) REVIEW (default): read + find + suggest, NO edits. Cheap. Start here.
python clarity_crew.py src/app

# 2) Open crew/reports/review_latest.md and DELETE any fixes you don't want.

# 3) APPLY-ONLY: apply just the fixes left in the report (no re-analysis = cheap).
python clarity_crew.py src/app --apply-only
```

Or do it all in one shot (analyze + apply, one pass):

```powershell
python clarity_crew.py <path> --fix
python clarity_crew.py <path> --fix --rounds 3   # loop up to N passes (rarely needed)
```

| Flag | Meaning | Default |
| --- | --- | --- |
| *(none)* | REVIEW only — writes the report, edits nothing | on |
| `--fix` / `--apply` | analyze **and** apply (FixApplier edits files) | off |
| `--apply-only` | apply fixes from the existing report, skip analysis | off |
| `--rounds N` | max fix passes (only with `--fix`) | 1 |

### How the FixApplier decides (so tokens aren't wasted on reverts)

- **No-op / cosmetic / duplicate** suggestion → **SKIPPED** (never edited).
- Real fix, **tests pass** → kept, **FIXED**.
- Real fix, **no test covers it** → kept, **APPLIED-UNVERIFIED** (flagged for your
  review) — it is *not* thrown away just because there's no test.
- Real fix, **a test now fails** → the applier **iterates on the code to make it
  pass** (up to a few attempts; it never weakens/deletes tests). Green → **FIXED**.
  Only if it still can't → reverted byte-exact, **REVERTED** (last resort).

Reports live under `crew/reports/`. Each run overwrites `reports/review_latest.md`
(the working copy you prune and that `--apply-only` reads) and also saves a
timestamped archive (`reports/review_<timestamp>.md`). When applying, FixApplier's
results plus a **Ground truth** section (real edit count + `git diff --stat`) are
saved to `reports/fix_latest.md` (+ timestamped archive). For reliable edits set
`APPLIER_MODEL=gpt-4o`.

## Reliability (empty-response handling)

Some models occasionally return an empty completion — CrewAI then raises
`Invalid response from LLM call - None or empty`. The crew is hardened against
this via `llm_client.py`:

- **Retries up to 3 times** when the model returns None/empty or the API errors
  out, with a short backoff **and escalating temperature** (0.1 → 0.5 → 0.9) so a
  stuck empty takes a different path.
- **Automatic fallback model**: if it still comes back empty, that single call is
  handed to `FALLBACK_MODEL`. If unset, it uses `gpt-4o-mini` when `OPENAI_API_KEY`
  is present, otherwise `xai/grok-3`. Set `FALLBACK_MODEL=none` to disable.
- **Smaller context**: file reads are capped (`READ_MAX_CHARS`, default 16k) so a
  few large files don't blow up the context and trigger empty responses.
- **Lower `TEMPERATURE` (0.1)** for steadier output and a **`MAX_TOKENS` cap (4000)**
  to reduce rate-limit pressure.
- If a run *still* fails after retries and fallback, the crew **doesn't crash with
  a raw traceback** — it logs a clear message and appends a "Run error" note to
  `reports/fix_latest.md`, keeping any edits already applied.

The default `gpt-4o-mini` is the most reliable choice inside CrewAI's tool loop.

## Notes

- **REVIEW is the default** and by far the cheapest, most useful mode — the report
  is the main value. Only reach for `--fix` when you expect real, fixable bugs.
- Scoping to one folder keeps token usage (and cost) low and each pass fast.
- FIX mode **modifies your code**. Use a clean git branch and review with `git diff`.
- FIX mode runs a **single pass** unless you pass `--rounds N` — it will not silently
  loop and burn tokens re-analyzing clean code.
