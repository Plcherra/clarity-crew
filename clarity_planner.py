"""Clarity Planner — Phase 1 of the Director Loop.

Turns a plain-English request (or a preset) plus a project's own docs + code into
a ranked, approval-ready plan in reports/launch_plan.md. READ-ONLY for planning:
it never edits your files.

Presets (what you want it to do):
  * launch-review  — what does this project need to reach launch? (spec + code scan)
  * find-bugs      — hunt for real bugs (delegates to the review engine)
  * create-feature — deliver a plain-English request ("save my memory while I talk"),
                     asking clarifying questions first when the request is vague

Three planner agents run in sequence (see planner_definitions.py):
  1. SpecReader   -> reads the project's docs to understand it / place the request.
  2. CodeScanner  -> scans code for BUILD (missing) and FIX (broken) items.
  3. PlanWriter   -> writes the ranked plan; each item has a ready-to-run builder task.

Target ANY project with --repo (defaults to the current working directory):
    python clarity_planner.py --repo C:\\path\\to\\project              # launch review
    python clarity_planner.py --preset find-bugs --repo C:\\path\\proj  # find bugs
    python clarity_planner.py --goal "let users reset their password"   # create feature
    python clarity_planner.py <path> --dry-run                          # print config only

Configure via .env (see .env.example). PLANNER_MODEL overrides MODEL for the plan.
"""

from __future__ import annotations

import json
import logging
import os
import re
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

PRESETS = {"launch-review", "find-bugs", "create-feature"}
# Friendly aliases the user might type.
_PRESET_ALIASES = {
    "launch": "launch-review",
    "review": "launch-review",
    "launch_review": "launch-review",
    "bugs": "find-bugs",
    "find_bugs": "find-bugs",
    "findbugs": "find-bugs",
    "feature": "create-feature",
    "create_feature": "create-feature",
    "build": "create-feature",
}


class RunConfig:
    """Parsed CLI options for one Planner invocation."""

    def __init__(
        self,
        scope: str,
        dry_run: bool,
        repo: str | None = None,
        goal: str | None = None,
        preset: str | None = None,
    ) -> None:
        self.scope = scope
        self.dry_run = dry_run
        self.repo = repo
        self.goal = goal
        self.preset = preset


def _parse_args(argv: list[str]) -> RunConfig:
    scope: str | None = None
    dry_run = False
    repo: str | None = None
    goal: str | None = None
    preset: str | None = None
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
        elif arg in ("--goal", "--ask"):
            i += 1
            if i < len(argv):
                goal = argv[i]
        elif arg.startswith("--goal="):
            goal = arg.split("=", 1)[1]
        elif arg.startswith("--ask="):
            goal = arg.split("=", 1)[1]
        elif arg == "--preset":
            i += 1
            if i < len(argv):
                preset = argv[i]
        elif arg.startswith("--preset="):
            preset = arg.split("=", 1)[1]
        elif not arg.startswith("-"):
            scope = arg
        i += 1
    if preset:
        preset = _PRESET_ALIASES.get(preset.strip().lower(), preset.strip().lower())
    return RunConfig(scope or ".", dry_run, repo, goal, preset)


def _resolve_preset(cfg: RunConfig) -> str:
    """Pick the preset: explicit flag wins; a --goal implies create-feature."""
    if cfg.preset:
        return cfg.preset
    return "create-feature" if cfg.goal else "launch-review"


def _ensure_reports_dir() -> None:
    Path(REPORTS_DIR).mkdir(parents=True, exist_ok=True)


def _archive_plan() -> None:
    """Save a timestamped copy of the plan next to the working copy."""
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
    """Strip any model preamble so the file starts at its top-level '# ' heading.

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
        (i for i, ln in enumerate(lines) if ln.lstrip().startswith("# ")),
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
        f"# Plan — run error ({stamp})\n\n"
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


# --- Clarifying questions (create-feature) -----------------------------------

def _parse_json_obj(raw: object) -> dict:
    """Best-effort parse of a JSON object from an LLM completion. Fail open."""
    if not isinstance(raw, str):
        return {"clear": True}
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"clear": True}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {"clear": True}
    except Exception:  # noqa: BLE001
        return {"clear": True}


def _assess_goal(llm, goal: str) -> dict:
    """Ask the model whether a plain-English request is specific enough to build."""
    prompt = (
        "You help a NON-CODER turn a plain-English software request into a buildable "
        f'task.\nTheir request: "{goal}"\n\n'
        "Is this specific enough to plan a concrete code change that delivers what they "
        "actually want? If yes, return clear=true. If it is too vague to build the "
        "RIGHT thing, return clear=false with 1-3 short clarifying questions a non-coder "
        "can answer, each with 2-4 suggested options when helpful.\n"
        'Return ONLY JSON: {"clear": true|false, "questions": '
        '[{"question": "...", "options": ["...", "..."]}]}'
    )
    try:
        raw = llm.call([{"role": "user", "content": prompt}])
    except Exception as exc:  # noqa: BLE001 - never block the user on this
        log.warning("Goal assessment failed (%s); treating request as clear.", exc)
        return {"clear": True}
    return _parse_json_obj(raw)


def _clarify_goal(goal: str, llm) -> str:
    """Interactively refine a vague request. Blocks for typed answers.

    Non-interactive (no stdin) runs fail open: they proceed with the request as-is.
    """
    try:
        rounds = 0
        while rounds < 2:
            rounds += 1
            assessment = _assess_goal(llm, goal)
            if assessment.get("clear", True):
                return goal
            questions = assessment.get("questions") or []
            if not questions:
                return goal
            print(
                "\nA few quick questions so I build the right thing "
                "(press Enter to skip any):\n"
            )
            answers: list[str] = []
            for question in questions:
                qtext = str(question.get("question") or "").strip()
                if not qtext:
                    continue
                options = [str(o) for o in (question.get("options") or [])]
                print(qtext)
                for idx, opt in enumerate(options, 1):
                    print(f"  {idx}. {opt}")
                try:
                    ans = input("> ").strip()
                except EOFError:
                    print("(no input available; proceeding with what I have)")
                    return goal
                if not ans:
                    continue
                if options and ans.isdigit() and 1 <= int(ans) <= len(options):
                    ans = options[int(ans) - 1]
                answers.append(f"{qtext} -> {ans}")
            if not answers:
                return goal  # user skipped everything; plan with the original request
            goal = goal + "\n\nClarifications:\n" + "\n".join(f"- {a}" for a in answers)
        return goal
    except Exception as exc:  # noqa: BLE001 - clarification is best-effort
        log.warning("Clarify step skipped: %s", exc)
        return goal


# --- find-bugs delegation to the review engine -------------------------------

def _run_find_bugs(scope: str, repo: str | None) -> int:
    """Delegate the 'find bugs' preset to the existing review engine (REVIEW mode)."""
    engine = Path(__file__).with_name("clarity_crew.py")
    cmd = [sys.executable, str(engine), scope]
    if repo:
        cmd += ["--repo", repo]
    print(f"\n{'#' * 70}\n# Find bugs — delegating to the review engine\n{'#' * 70}")
    print(f"Running: {' '.join(cmd)}\n")
    try:
        return subprocess.run(cmd).returncode
    except Exception as exc:  # noqa: BLE001
        log.error("find-bugs delegation failed: %s", exc)
        return 1


# --- planner crew ------------------------------------------------------------

def _build_planner_crew(scope: str, goal: str | None, mode: str, llm) -> Crew:
    spec_reader, code_scanner, plan_writer = build_planner_agents(llm, PLANNER_TOOLS)
    tasks = build_planner_tasks(
        scope, spec_reader, code_scanner, plan_writer, goal=goal, mode=mode
    )
    return Crew(
        agents=[spec_reader, code_scanner, plan_writer],
        tasks=tasks,
        process=Process.sequential,
        verbose=True,
    )


def _run_plan(scope: str, goal: str | None, mode: str, llm):
    banner = "Building a feature plan" if mode == "create-feature" else "Planning pass"
    print(f"\n{'#' * 70}\n# {banner} (read-only — no files will be edited)\n{'#' * 70}")
    try:
        result = _build_planner_crew(scope, goal, mode, llm).kickoff()
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
    if cfg.preset and cfg.preset not in PRESETS:
        print(f"Unknown preset '{cfg.preset}'. Choose one of: {', '.join(sorted(PRESETS))}.")
        return
    if cfg.repo:
        REPO_ROOT = configure_repo_root(cfg.repo)
    preset = _resolve_preset(cfg)

    mode_desc = {
        "launch-review": "PLAN launch review (read-only)",
        "find-bugs": "FIND BUGS (delegates to the review engine)",
        "create-feature": "CREATE FEATURE plan (read-only)",
    }[preset]

    print(f"Repo root : {REPO_ROOT}")
    print(f"Scope     : {cfg.scope}")
    print(f"Model     : {MODEL_NAME}")
    print(f"Preset    : {preset}")
    if cfg.goal:
        print(f"Goal      : {cfg.goal}")
    print(f"Mode      : {mode_desc}")
    print()

    if cfg.dry_run:
        print("[dry-run] Nothing was executed and no tokens were spent. "
              "Remove --dry-run to actually run.")
        return

    if preset == "find-bugs":
        _run_find_bugs(cfg.scope, cfg.repo)
        return

    _ensure_reports_dir()
    llm = build_llm(MODEL_NAME)

    goal = cfg.goal
    if preset == "create-feature":
        if not goal:
            try:
                goal = input(
                    "What do you want to build or change? Describe it in plain "
                    "English:\n> "
                ).strip()
            except EOFError:
                goal = ""
        if not goal:
            print('No request given — nothing to plan. Provide --goal "..." next time.')
            return
        goal = _clarify_goal(goal, llm)

    result = _run_plan(cfg.scope, goal, preset, llm)

    plan_path = Path(LAUNCH_PLAN_FILE).resolve()
    print("\n" + "=" * 70)
    print("Clarity Planner finished.")
    if plan_path.exists():
        print(f"Plan saved to: {plan_path}")
        print("Next: open it, review/edit/approve the items you want. "
              "(Explainer = Phase 2, Builder = Phase 3.)")
    print("=" * 70)
    print(result)


if __name__ == "__main__":
    main()
