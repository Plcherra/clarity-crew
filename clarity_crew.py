"""Clarity Review Crew — a CrewAI team that reviews code, and can optionally fix it.

Two modes:

  REVIEW (default, cheap — one pass, no file edits):
    1. ProjectReader  -> maps the codebase and explains how it is put together.
    2. BugFinder      -> hunts for real bugs, risks, and rule violations.
    3. FixSuggester   -> writes a concrete fix report (reports/review_latest.md).

  FIX (opt-in with --fix — edits files, one pass by default):
    4. FixApplier     -> applies each suggested fix, runs the closest tests, and
                         reverts anything whose tests fail.

Cost note: FIX mode is much more expensive (it re-reads code and runs the model
in a tool loop). It also tends to propose marginal/no-op changes on already-clean
code, so REVIEW is the default. Only add --fix when you expect real bugs.

Target ANY project with --repo (defaults to the current working directory):
    python clarity_crew.py --repo C:\\path\\to\\project        # REVIEW whole project
    python clarity_crew.py src --repo C:\\path\\to\\project    # REVIEW one folder in it

Run:
    python clarity_crew.py .                                # REVIEW cwd
    python clarity_crew.py <path>                           # REVIEW one folder
    python clarity_crew.py <path> --fix                     # also apply fixes
    python clarity_crew.py <path> --fix --rounds 3          # loop fix up to 3x

Configure via .env (see .env.example). Set APPLIER_MODEL=gpt-4o for reliable edits.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

from crewai import Crew, Process
from dotenv import load_dotenv

from codebase_tools import (
    REPO_ROOT,
    configure_repo_root,
    edit_file,
    get_edit_count,
    list_directory,
    read_file,
    reset_edit_count,
    restore_file,
    run_tests,
    search_code,
)
from crew_definitions import (
    FIX_RESULTS_FILE,
    REPORTS_DIR,
    REVIEW_REPORT_FILE,
    build_agents,
    build_apply_only_task,
    build_tasks,
)
from llm_client import build_llm

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("clarity_crew")

# --- Model: resilient LLM (OpenAI GPT by default; retries empty responses) ---
# Configure model/temperature/tokens/retries via env (see .env.example).
MODEL_NAME = os.environ.get("MODEL", "gpt-4o-mini")
# The Fix Applier needs precise, exact edits. Optionally give it a stronger model
# (e.g. APPLIER_MODEL=gpt-4o) while the other agents use the cheaper MODEL.
APPLIER_MODEL = os.environ.get("APPLIER_MODEL", "").strip()

llm = build_llm()
applier_llm = build_llm(APPLIER_MODEL) if APPLIER_MODEL else llm

TOOLS = [list_directory, read_file, search_code]
# FixApplier also gets write + test tools so it can change code and verify it.
APPLIER_TOOLS = [
    list_directory,
    read_file,
    search_code,
    edit_file,
    run_tests,
    restore_file,
]

# Fix-mode caps. Default to a SINGLE pass so we never silently re-run the whole
# (expensive) pipeline. Use --rounds N (or MAX_ROUNDS) to opt into looping.
MAX_FIXES = int(os.environ.get("MAX_FIXES", "40"))
MAX_ROUNDS = int(os.environ.get("MAX_ROUNDS", "1"))


def _build_crew(scope: str, apply: bool) -> Crew:
    reader, finder, suggester, applier = build_agents(
        llm, applier_llm, TOOLS, APPLIER_TOOLS
    )
    tasks = build_tasks(scope, reader, finder, suggester, applier)
    if not apply:
        # REVIEW mode: drop the file-editing FixApplier and its task entirely.
        tasks = tasks[:3]
        agents = [reader, finder, suggester]
    else:
        agents = [reader, finder, suggester, applier]
    return Crew(
        agents=agents,
        tasks=tasks,
        process=Process.sequential,
        verbose=True,
    )


def _build_apply_only_crew(scope: str) -> Crew:
    """Crew with only the FixApplier, applying fixes from the saved report."""
    _, _, _, applier = build_agents(llm, applier_llm, TOOLS, APPLIER_TOOLS)
    return Crew(
        agents=[applier],
        tasks=[build_apply_only_task(scope, applier)],
        process=Process.sequential,
        verbose=True,
    )


class RunConfig:
    """Parsed CLI options for one invocation."""

    def __init__(
        self,
        scope: str,
        apply: bool,
        rounds: int,
        apply_only: bool,
        dry_run: bool,
        repo: str | None = None,
    ) -> None:
        self.scope = scope
        self.apply = apply
        self.rounds = rounds
        self.apply_only = apply_only
        self.dry_run = dry_run
        self.repo = repo


def _parse_args(argv: list[str]) -> RunConfig:
    """Parse the scope and flags.

    Modes:
      (default)      REVIEW — 3 agents, no edits, cheapest.
      --fix          analyze + apply (one pass; --rounds N to loop).
      --apply-only   apply fixes from the EXISTING report (skip analysis, cheapest
                     way to actually land curated fixes).
    """
    scope: str | None = None
    apply = False
    apply_only = False
    dry_run = False
    repo: str | None = None
    rounds = MAX_ROUNDS
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--fix", "--apply"):
            apply = True
        elif arg in ("--apply-only", "--apply-report"):
            apply_only = True
        elif arg in ("--dry-run", "--dry"):
            dry_run = True
        elif arg == "--repo":
            i += 1
            if i < len(argv):
                repo = argv[i]
        elif arg.startswith("--repo="):
            repo = arg.split("=", 1)[1]
        elif arg == "--rounds":
            i += 1
            if i < len(argv):
                try:
                    rounds = max(1, int(argv[i]))
                except ValueError:
                    rounds = MAX_ROUNDS
        elif arg.startswith("--rounds="):
            try:
                rounds = max(1, int(arg.split("=", 1)[1]))
            except ValueError:
                rounds = MAX_ROUNDS
        elif not arg.startswith("-"):
            scope = arg
        i += 1
    return RunConfig(scope or ".", apply, rounds, apply_only, dry_run, repo)


def _ensure_reports_dir() -> None:
    Path(REPORTS_DIR).mkdir(parents=True, exist_ok=True)


def _archive(latest_file: str, prefix: str) -> None:
    """Copy a *_latest.md report to a timestamped archive next to it."""
    src = Path(latest_file)
    if not src.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = Path(REPORTS_DIR) / f"{prefix}_{stamp}.md"
    try:
        shutil.copyfile(src, dest)
        print(f"Archived: {dest.resolve()}")
    except Exception:  # noqa: BLE001
        pass


def _write_error_report(scope: str, exc: Exception) -> None:
    """Record a run failure to the fix-results file so it isn't just a traceback."""
    _ensure_reports_dir()
    fixes_path = Path(FIX_RESULTS_FILE).resolve()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    note = (
        f"\n\n---\n\n## Run error ({stamp})\n\n"
        f"- **Scope:** `{scope}`\n"
        f"- **Edits applied before failure:** {get_edit_count()}\n"
        f"- **Error:** {detail}\n\n"
        "The crew stopped this pass because the model could not return a usable "
        "response even after retries/fallback. Things to try: narrow the scope, "
        "switch models (e.g. MODEL=gpt-4o-mini with OPENAI_API_KEY), or re-run — "
        "earlier applied edits are preserved.\n"
    )
    try:
        with fixes_path.open("a", encoding="utf-8") as handle:
            handle.write(note)
    except Exception:  # noqa: BLE001 - never mask the original error
        pass


def _append_ground_truth(scope: str) -> None:
    """Append the REAL change set to the results file, independent of the LLM.

    The agent's self-reported summary can be wrong (it may claim fixes it never
    applied). This records the actual edit count and git diff so the user sees
    ground truth.
    """
    fixes_path = Path(FIX_RESULTS_FILE).resolve()
    diff_stat = ""
    try:
        proc = subprocess.run(
            ["git", "diff", "--stat", "--", scope if scope != "." else "."],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        diff_stat = (proc.stdout or "").strip() or "(no files changed)"
    except Exception as exc:  # noqa: BLE001
        diff_stat = f"(could not run git diff: {exc})"

    section = (
        f"\n\n---\n\n## Ground truth (verified, not self-reported)\n\n"
        f"- **Successful file edits applied this run:** {get_edit_count()}\n"
        f"- **git diff --stat:**\n\n```\n{diff_stat}\n```\n\n"
        "If the summary above claims fixes but this shows no changes, the agent's "
        "report was inaccurate — trust this section.\n"
    )
    try:
        with fixes_path.open("a", encoding="utf-8") as handle:
            handle.write(section)
    except Exception:  # noqa: BLE001
        pass


def _run_once(scope: str, apply: bool):
    crew = _build_crew(scope, apply)
    return crew.kickoff()


def _run_review(scope: str):
    """Single, cheap review pass — three agents, no file edits."""
    print(f"\n{'#' * 70}\n# Review pass (no files will be edited)\n{'#' * 70}")
    try:
        result = _run_once(scope, apply=False)
    except Exception as exc:  # noqa: BLE001
        log.error("Review failed: %s", exc)
        _write_error_report(scope, exc)
        return f"Review failed: {exc}"
    _archive(REVIEW_REPORT_FILE, "review")
    return result


def _run_fix(scope: str, rounds: int):
    """Fix passes (edits files). Loops only up to `rounds` (default 1)."""
    reset_edit_count()
    last_result = None
    round_num = 0
    while round_num < rounds and get_edit_count() < MAX_FIXES:
        round_num += 1
        edits_before = get_edit_count()
        print(f"\n{'#' * 70}\n# Fix round {round_num}/{rounds} "
              f"(edits so far: {edits_before})\n{'#' * 70}")
        try:
            last_result = _run_once(scope, apply=True)
        except Exception as exc:  # noqa: BLE001 - keep prior edits, stop cleanly
            log.error("Round %d failed: %s", round_num, exc)
            _write_error_report(scope, exc)
            last_result = f"Round {round_num} failed: {exc}"
            print("Stopping after error; earlier edits are kept.")
            break
        edits_this_round = get_edit_count() - edits_before
        print(f"\n[round {round_num}] edits applied this round: {edits_this_round} "
              f"(total: {get_edit_count()})")
        if edits_this_round == 0:
            print("No edits applied this round — no more fixable issues. Stopping.")
            break
    _append_ground_truth(scope)
    _archive(REVIEW_REPORT_FILE, "review")
    _archive(FIX_RESULTS_FILE, "fix")
    return last_result


def _run_apply_only(scope: str):
    """Apply fixes from the EXISTING report — no analysis agents. Cheapest apply."""
    report = Path(REVIEW_REPORT_FILE)
    if not report.exists():
        msg = (
            f"No {REVIEW_REPORT_FILE} found. Run a review first "
            "(.\\review.ps1 <scope>), optionally prune it, then --apply-only."
        )
        print(msg)
        return msg
    reset_edit_count()
    print(f"\n{'#' * 70}\n# Apply-only (from saved report; no re-analysis)\n{'#' * 70}")
    try:
        result = _build_apply_only_crew(scope).kickoff()
    except Exception as exc:  # noqa: BLE001
        log.error("Apply-only failed: %s", exc)
        _write_error_report(scope, exc)
        result = f"Apply-only failed: {exc}"
    _append_ground_truth(scope)
    _archive(FIX_RESULTS_FILE, "fix")
    return result


def main() -> None:
    global REPO_ROOT
    cfg = _parse_args(sys.argv[1:])
    if cfg.repo:
        # Re-point the sandboxed tools (and this module) at the target project.
        REPO_ROOT = configure_repo_root(cfg.repo)
    if cfg.apply_only:
        mode = "APPLY-ONLY (edits files from saved report)"
    elif cfg.apply:
        mode = "FIX (edits files)"
    else:
        mode = "REVIEW (no edits)"
    edits_mode = cfg.apply or cfg.apply_only

    print(f"Repo root : {REPO_ROOT}")
    print(f"Scope     : {cfg.scope}")
    print(f"Model     : {MODEL_NAME}")
    if edits_mode:
        print(f"Applier   : {APPLIER_MODEL or MODEL_NAME}")
    print(f"Mode      : {mode}" + (f"  (max {cfg.rounds} round(s))" if cfg.apply else ""))
    if not edits_mode:
        print("Tip       : review first, prune the report, then --apply-only (cheapest fix).")
    print()

    if cfg.dry_run:
        print("[dry-run] Nothing was executed and no tokens were spent. "
              "Remove --dry-run to actually run.")
        return

    _ensure_reports_dir()

    if cfg.apply_only:
        last_result = _run_apply_only(cfg.scope)
    elif cfg.apply:
        last_result = _run_fix(cfg.scope, cfg.rounds)
    else:
        last_result = _run_review(cfg.scope)

    report_path = Path(REVIEW_REPORT_FILE).resolve()
    fixes_path = Path(FIX_RESULTS_FILE).resolve()
    print("\n" + "=" * 70)
    print("Clarity Review Crew finished.")
    if edits_mode:
        print(f"Total edits applied: {get_edit_count()}")
        print("Review the real changes with:  git diff")
    if report_path.exists():
        print(f"Review report saved to: {report_path}")
    if edits_mode and fixes_path.exists():
        print(f"Fix results saved to:   {fixes_path}")
    print("=" * 70)
    print(last_result)


if __name__ == "__main__":
    main()
