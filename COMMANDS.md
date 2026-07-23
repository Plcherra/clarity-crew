# Clarity Crew — Command Cheat Sheet

Short scripts so you don't type the long `& ".venv\Scripts\python.exe" ...` line.
Run them from the `crew/` folder in PowerShell.

> First time only: `.\.venv\Scripts\Activate.ps1` is **not** needed — the scripts
> call the venv Python directly. If PowerShell blocks scripts, run once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## The everyday commands

| Command | What it does | Edits files? | Cost |
| --- | --- | --- | --- |
| `.\plan.ps1 [path]` | Read the project's docs + code → write a ranked **launch plan** | No | Med |
| `.\review.ps1 <path>` | Read → find bugs → write review report | No | Low |
| `.\apply.ps1 <path>` | Apply the fixes left in the report | **Yes** | Low |
| `.\fix.ps1 <path>` | Review **and** apply in one shot | **Yes** | High |

`plan.ps1` is step 1 of the Director Loop (Phase 1): it produces
`reports/launch_plan.md` — ranked BUILD/FIX items, each with a plain-English
summary and an approval-ready builder prompt. It never edits files.

Tell it what you want with a **preset** or a **plain-English goal**:

```powershell
.\plan.ps1                                            # launch-review (default) of the repo
.\plan.ps1 app\services                               # focus the scan on one area
.\plan.ps1 --preset find-bugs                         # hunt for bugs (via review engine)
.\plan.ps1 --goal "let users reset their password"    # create-feature (asks if vague)
.\plan.ps1 --repo C:\path\to\other-project            # plan a different project
```

Presets: `launch-review` (default) · `find-bugs` · `create-feature`. A `--goal`
implies `create-feature`; if the request is vague, the Planner asks clarifying
questions interactively before planning.

`<path>` is **optional** and is just *where to focus* (to save tokens). Leave it
off to scan the whole repo — the agents find the files themselves.

```powershell
.\review.ps1                                   # review the whole repo
.\review.ps1 src/app     # review one folder
```

## Recommended daily flow (review → prune → apply)

```powershell
# 1. Review the area you're about to work on (cheap, no edits)
.\review.ps1 src/app

# 2. Open crew\reports\review_latest.md — delete any fixes you DON'T want

# 3. Apply only the fixes you kept (cheap — no re-analysis)
.\apply.ps1 src/app

# 4. Review the real changes like a PR, keep or drop
git diff
git checkout -- <file you don't want>
```

## One-shot (analyze + apply together)

```powershell
.\fix.ps1 src/app              # one pass
.\fix.ps1 src/app --rounds 3   # loop up to 3 passes
```

## Preview without spending anything

Add `--dry-run` to any command to print what *would* run and exit (no tokens):

```powershell
.\review.ps1 src/app --dry-run
.\fix.ps1 src/app --dry-run
```

## All flags (passed straight through to clarity_crew.py)

| Flag | Meaning | Default |
| --- | --- | --- |
| *(none)* | REVIEW only — writes the report, no edits | on |
| `--fix` / `--apply` | analyze **and** apply (FixApplier edits files) | off |
| `--apply-only` | apply from the existing report, skip analysis | off |
| `--rounds N` | max fix passes (only with `--fix`) | 1 |
| `--dry-run` | print the plan and exit, spend nothing | off |

Raw form (what the scripts wrap), if you ever need it:

```powershell
& ".venv\Scripts\python.exe" clarity_crew.py <path> [--fix | --apply-only] [--rounds N] [--dry-run]
```

## Outputs

| File | When | Contents |
| --- | --- | --- |
| `crew\reports\launch_plan.md` | every `plan.ps1` run | ranked BUILD/FIX items, each with a plain-English summary + approval-ready builder prompt |
| `crew\reports\review_latest.md` | every run | the bugs + concrete fixes (this is the value); prune this before apply-only |
| `crew\reports\fix_latest.md` | fix / apply-only | FIXED / APPLIED-UNVERIFIED / REVERTED / SKIPPED per issue, plus a **Ground truth** section (real edit count + `git diff --stat`) |
| `crew\reports\*_<timestamp>.md` | every run | archived copies so you keep a history |

## Fix outcomes (how the applier decides)

- **SKIPPED** — suggestion was a no-op/cosmetic; never edited.
- **FIXED** — real change, tests pass (it iterates on failures to reach green).
- **APPLIED-UNVERIFIED** — real change, no test covers it; kept, flagged for you.
- **REVERTED** — it couldn't make tests pass after a few tries; reverted byte-exact.

## Models (set in `crew\.env`)

```
MODEL=gpt-4o-mini      # analysis agents — cheap
APPLIER_MODEL=gpt-4o   # applier — strong enough to iterate on test failures
```

`fix.ps1` / `apply.ps1` default `APPLIER_MODEL=gpt-4o` if you haven't set one.

## Safety

- Always run on a **clean git branch**; treat the diff as a PR to review.
- The applier can only edit inside the repo, won't write invalid Python, and
  reverts byte-exact. But **you** are the final approver via `git diff`.
