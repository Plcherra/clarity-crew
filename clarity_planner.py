"""Clarity Planner — Phase 1 of the Director Loop.

Reads a project's OWN docs + code and writes a ranked, approval-ready launch
plan to reports/launch_plan.md. READ-ONLY: it never edits your files.

Three agents run in sequence (see planner_definitions.py):
  1. SpecReader   -> auto-detects the spec (README, docs/, plans/, *.md) and
                     distills the intended product + launch criteria.
  2. CodeScanner  -> scans the code and finds BUILD (missing/incomplete) and
                     FIX (present-but-broken) items, with file:line evidence.
  3. PlanWriter   -> writes the ranked plan: each item a plain-English summary
                     plus a ready-to-run builder prompt.

Target ANY project with --repo (defaults to the current working directory):
    python clarity_planner.py --repo C:\\path\\to\\project          # plan whole project
    python clarity_planner.py src --repo C:\\path\\to\\project       # focus a folder

Run:
    python clarity_planner.py                       # plan the cwd
    python clarity_planner.py <path>                # focus one folder/area
    python clarity_planner.py <path> --dry-run      # print the config, spend nothing

Configure via .env (see .env.example). PLANNER_MODEL overrides MODEL for the plan.
"""

from __future__ import annotations

import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from crewai import Crew, Process
from dotenv import load_dotenv

from codebase_tools import (
    REPO_ROOT,
    configure_repo_root,
    find_spec_docs,
    list_directory,
    read_file,
    search_code,
)
from llm_client import build_llm
from planner_definitions import (
    LAUNCH_PLAN_FILE,
    REPORTS_DIR,
    build_planner_agents,
    build_planner_tasks,
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("clarity_planner")

# The Planner may use a different (often stronger) model than the reviewer.
MODEL_NAME = os.environ.get("PLANNER_MODEL", "").strip() or os.environ.get(
    "MODEL", "gpt-4o-mini"
)

# Read-only tool set — the Planner never edits or runs tests.
PLANNER_TOOLS = [find_spec_docs, list_directory, read_file, search_code]


class RunConfig:
    """Parsed CLI options for one Planner invocation."""

    def __init__(self, scope: str, dry_run: bool, repo: str | None = None) -> None:
        self.scope = scope
        self.dry_run = dry_run
        self.repo = repo


def _parse_args(argv: list[str]) -> RunConfig:
    scope: str | None = None
    dry_run = False
    repo: str | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--dry-run", "--dry"):
            dry_run = True
        elif arg == "--repo":
            i += 1
            if i < len(argv):
                repo = argv[i]
        elif arg.startswith("--repo="):
            repo = arg.split("=", 1)[1]
        elif not arg.startswith("-"):
            scope = arg
        i += 1
    return RunConfig(scope or ".", dry_run, repo)


def _ensure_reports_dir() -> None:
    Path(REPORTS_DIR).mkdir(parents=True, exist_ok=True)


def _archive_plan() -> None:
    """Save a timestamped copy of the launch plan next to the working copy."""
    src = Path(LAUNCH_PLAN_FILE)
    if not src.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = Path(REPORTS_DIR) / f"launch_plan_{stamp}.md"
    try:
        dest.write_bytes(src.read_bytes())
        print(f"Archived: {dest.resolve()}")
    except Exception:  # noqa: BLE001
        pass


def _clean_plan_output() -> None:
    """Strip any model preamble so the file starts at the '# Launch Plan' heading.

    CrewAI writes the agent's raw completion to output_file; some models wrap it
    in a ``` fence or prefix a 'Thought:' line. We keep the real document (which
    itself contains ``` builder-prompt blocks) and drop only the wrapper before
    the heading, plus a dangling wrapper fence at the very end if unbalanced.
    """
    path = Path(LAUNCH_PLAN_FILE)
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return
    lines = text.splitlines()
    start = next(
        (i for i, ln in enumerate(lines) if ln.lstrip().startswith("# Launch Plan")),
        None,
    )
    if start is None:
        return  # nothing recognizable to clean; leave as-is
    body = lines[start:]
    # Drop a trailing lone ``` that was the wrapper's close (odd fence count).
    while body and body[-1].strip() == "":
        body.pop()
    if body and body[-1].strip() == "```":
        fences = sum(1 for ln in body if ln.strip().startswith("```"))
        if fences % 2 == 1:
            body.pop()
    cleaned = "\n".join(body).rstrip() + "\n"
    if cleaned != text:
        try:
            path.write_text(cleaned, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def _write_error_report(scope: str, exc: Exception) -> None:
    """Record a run failure to the plan file so it isn't just a traceback."""
    _ensure_reports_dir()
    plan_path = Path(LAUNCH_PLAN_FILE).resolve()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    note = (
        f"# Launch Plan — run error ({stamp})\n\n"
        f"- **Scope:** `{scope}`\n"
        f"- **Error:** {detail}\n\n"
        "The Planner could not finish this pass (the model may have returned an "
        "unusable response even after retries/fallback). Things to try: narrow the "
        "scope, switch models (e.g. MODEL=gpt-4o-mini with OPENAI_API_KEY), or "
        "re-run.\n"
    )
    try:
        plan_path.write_text(note, encoding="utf-8")
    except Exception:  # noqa: BLE001 - never mask the original error
        pass


def _build_planner_crew(scope: str) -> Crew:
    llm = build_llm(MODEL_NAME)
    spec_reader, code_scanner, plan_writer = build_planner_agents(llm, PLANNER_TOOLS)
    tasks = build_planner_tasks(scope, spec_reader, code_scanner, plan_writer)
    return Crew(
        agents=[spec_reader, code_scanner, plan_writer],
        tasks=tasks,
        process=Process.sequential,
        verbose=True,
    )


def _run_plan(scope: str):
    print(f"\n{'#' * 70}\n# Planning pass (read-only — no files will be edited)\n{'#' * 70}")
    try:
        result = _build_planner_crew(scope).kickoff()
    except Exception as exc:  # noqa: BLE001
        log.error("Planner failed: %s", exc)
        _write_error_report(scope, exc)
        return f"Planner failed: {exc}"
    _clean_plan_output()
    _archive_plan()
    return result


def main() -> None:
    global REPO_ROOT
    cfg = _parse_args(sys.argv[1:])
    if cfg.repo:
        REPO_ROOT = configure_repo_root(cfg.repo)

    print(f"Repo root : {REPO_ROOT}")
    print(f"Scope     : {cfg.scope}")
    print(f"Model     : {MODEL_NAME}")
    print("Mode      : PLAN (read-only — writes reports/launch_plan.md)")
    print()

    if cfg.dry_run:
        print("[dry-run] Nothing was executed and no tokens were spent. "
              "Remove --dry-run to actually run.")
        return

    _ensure_reports_dir()
    result = _run_plan(cfg.scope)

    plan_path = Path(LAUNCH_PLAN_FILE).resolve()
    print("\n" + "=" * 70)
    print("Clarity Planner finished.")
    if plan_path.exists():
        print(f"Launch plan saved to: {plan_path}")
        print("Next: open it, edit/approve the items you want, then run a builder "
              "(Phase 2).")
    print("=" * 70)
    print(result)


if __name__ == "__main__":
    main()
