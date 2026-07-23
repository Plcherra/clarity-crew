"""Agent and task definitions for the Clarity Review Crew.

Kept separate from the orchestrator (clarity_crew.py) so each file stays small
and focused. Agents/tasks here are deliberately biased toward HIGH-VALUE, real
fixes: the crew should only spend tokens on changes worth keeping, never on
speculative churn that gets reverted.
"""

from __future__ import annotations

from crewai import Agent, Task

# All generated reports live under crew/reports/. Each run overwrites the
# *_latest.md working copy (what you prune / what --apply-only reads) and the
# orchestrator also archives a timestamped copy alongside it.
REPORTS_DIR = "reports"
REVIEW_REPORT_FILE = f"{REPORTS_DIR}/review_latest.md"
FIX_RESULTS_FILE = f"{REPORTS_DIR}/fix_latest.md"

# Shared policy the FixApplier follows in both full-fix and apply-only modes.
# The guiding rule: only revert a change that a test proves is a REGRESSION.
# Never waste a run reverting good work.
_APPLIER_POLICY = (
    "For EACH suggested fix:\n"
    "1. Re-read the target file to confirm the exact current code.\n"
    "2. FIRST decide if the fix actually changes runtime behavior. If it is a "
    "no-op, cosmetic, or duplicates logic that already exists, SKIP it (mark "
    "SKIPPED) — do not edit. We do not want pointless changes.\n"
    "3. Apply a real fix with edit_file (focused, exact, no whole-file rewrites). "
    "If edit_file REJECTS it for breaking syntax, do NOT resubmit the same text — "
    "re-read, copy the exact indentation, and change your new_string first.\n"
    "4. Verify. NOTE: run_tests runs PYTHON/pytest only. If the file you changed is "
    "Python, run ONLY the closest relevant test file (not the whole suite; unrelated "
    "pre-existing failures must not block you). If the file is NOT Python (e.g. Dart, "
    "JS), you CANNOT verify here — do NOT run pytest against unrelated tests; keep the "
    "change and mark it APPLIED-UNVERIFIED.\n"
    "5. Decide the outcome:\n"
    "   - Tests PASS  -> keep the change, mark FIXED (verified).\n"
    "   - Tests FAIL -> the fix is NOT finished. Do NOT revert yet. Read the "
    "failure, diagnose the real cause, and IMPROVE the code (adjust your change or "
    "add the necessary companion change), then re-run the closest test. Try up to "
    "3 attempts to get it green. NEVER weaken, skip, or delete a test to force a "
    "pass — fix the real code. If it goes green -> mark FIXED. Only if it still "
    "fails after your attempts -> restore_file to revert everything you changed for "
    "this issue, mark REVERTED, and move on (do not get stuck).\n"
    "   - No test exercises this code -> KEEP the change, mark "
    "APPLIED-UNVERIFIED (needs human review). Do NOT revert just because there is "
    "no test.\n"
    "Never claim a fix is verified unless run_tests actually passed."
)

_APPLIER_OUTPUT = (
    "A Markdown section titled '# Fix Application Results'. For each issue: the "
    "title, file(s) changed, status (FIXED / APPLIED-UNVERIFIED / REVERTED / "
    "SKIPPED), the test target and its pass/fail result (or why none applied), and "
    "a one-line note. End with a summary count per status."
)


def build_agents(llm, applier_llm, tools, applier_tools) -> tuple[Agent, Agent, Agent, Agent]:
    project_reader = Agent(
        role="Project Reader",
        goal=(
            "Understand the Clarity codebase in the requested scope: its structure, "
            "the main modules, how they connect, and what each part is responsible for."
        ),
        backstory=(
            "You are a meticulous senior engineer who joins a project that is already "
            "80-90% built. You never guess — you use the tools to list directories, read "
            "files, and search code until you genuinely understand how things fit together. "
            "You care about the assistant pipeline (Grok brain + backend body), memory, "
            "voice, and finance wiring."
        ),
        tools=tools,
        llm=llm,
        allow_delegation=False,
        verbose=True,
        max_iter=25,
    )

    bug_finder = Agent(
        role="Bug Finder",
        goal=(
            "Find REAL, high-impact defects with concrete evidence in code you actually "
            "read. Quality over quantity — a few certain bugs beat a long speculative list."
        ),
        backstory=(
            "You are a sharp, skeptical code auditor. You only report a defect when you can "
            "point to the exact code path that triggers it and describe the real user-facing "
            "impact. You do NOT report speculative 'might/could/potential' issues, defensive "
            "nice-to-haves, or style/naming/formatting. You look for logic errors, wrong "
            "None/empty handling that actually occurs, broken async/await, unhandled errors "
            "on real paths, data-scoping/security problems, and genuine Clarity-rule "
            "violations (fake success/memory, invented balances, silent saves, files over "
            "500 lines). You always read the code before claiming a bug and cite file:line."
        ),
        tools=tools,
        llm=llm,
        allow_delegation=False,
        verbose=True,
        max_iter=25,
    )

    fix_suggester = Agent(
        role="Fix Suggester",
        goal=(
            "Turn each CONFIRMED, behavior-affecting issue into a concrete, minimal fix. "
            "Drop anything whose fix would be a no-op."
        ),
        backstory=(
            "You are a decisive senior engineer. For every issue you keep, you state what is "
            "wrong and exactly how to fix it, with a short code sketch, and you name the test "
            "that would verify it. You only keep a fix if it changes behavior for the better; "
            "if the change would be cosmetic or duplicate existing logic, you drop the issue "
            "entirely. You prefer minimal, root-cause fixes that respect the architecture."
        ),
        tools=tools,
        llm=llm,
        allow_delegation=False,
        verbose=True,
        max_iter=20,
    )

    fix_applier = Agent(
        role="Fix Applier",
        goal=(
            "Apply the real, worth-keeping fixes and drive each one to GREEN tests. When a "
            "test fails, fix the code until it passes — revert only as a last resort."
        ),
        backstory=(
            "You are a careful, decisive engineer who finishes what you start. You skip no-op "
            "suggestions, apply genuine fixes with edit_file, and verify with run_tests. When "
            "a test fails you don't give up — you diagnose the real cause and iterate on the "
            "code (never weakening the tests) until it passes, and only revert if you truly "
            "cannot get it green after a few tries. If no test covers the code, you keep the "
            "fix and flag it for human review. You never claim a fix works unless run_tests "
            "actually passed."
        ),
        tools=applier_tools,
        llm=applier_llm,
        allow_delegation=False,
        verbose=True,
        max_iter=60,
    )

    return project_reader, bug_finder, fix_suggester, fix_applier


def build_tasks(
    scope: str,
    project_reader: Agent,
    bug_finder: Agent,
    fix_suggester: Agent,
    fix_applier: Agent,
) -> list[Task]:
    read_task = Task(
        description=(
            f"Explore the Clarity repository, focusing on this scope: '{scope}'.\n"
            "Use list_directory to see what exists, read_file to read the important files, "
            "and search_code to trace how pieces connect.\n"
            "Produce a clear map: the key files in scope, what each is responsible for, "
            "how data/control flows between them, and any areas that look fragile or "
            "overly complex. Note file sizes that look large (rule: keep under 500 lines)."
        ),
        expected_output=(
            "A structured overview of the scope: bullet list of key files with a one-line "
            "purpose each, a short description of how they interact, and a list of "
            "'areas worth a closer look' for the bug hunt."
        ),
        agent=project_reader,
    )

    bug_task = Task(
        description=(
            "Using the Project Reader's map, hunt for REAL, high-impact defects in scope: "
            f"'{scope}'.\n"
            "Read the actual code before flagging anything. Only report an issue if you can "
            "point to the exact code path that triggers it AND describe the concrete "
            "user-facing impact. Do NOT report speculative 'might/could/potential' issues, "
            "defensive nice-to-haves, or style/naming/formatting. Do NOT propose guards that "
            "the language already guarantees (e.g. null-checking a non-nullable Dart field, "
            "asserting a `required` constructor arg is non-null) — those are not bugs.\n"
            "Focus on: logic errors, None/empty handling that actually occurs, wrong "
            "async/await, unhandled exceptions on real paths, data-scoping/security "
            "problems, and genuine Clarity-rule violations."
        ),
        expected_output=(
            "A numbered list of at most 8 concrete, high-confidence issues, ordered by "
            "severity (High first). Each: short title, file:line, severity, and 1-3 "
            "sentences describing the problem and the exact code evidence. If there are no "
            "real defects, say so plainly instead of inventing issues."
        ),
        agent=bug_finder,
        context=[read_task],
    )

    fix_task = Task(
        description=(
            "Take the Bug Finder's issues and, for each one that is real, write a concrete "
            "fix. Re-read the relevant code to make the fix accurate. Prefer minimal, "
            "root-cause fixes that respect the architecture; no whole-file rewrites.\n"
            "IMPORTANT: if a proposed change would be a no-op (does not change behavior) or "
            "just duplicates existing logic, DROP that issue — do not include it. It is fine "
            "to return few or zero fixes."
        ),
        expected_output=(
            "A Markdown report titled '# Clarity Review Report'. For each kept issue give "
            "the Fix Applier everything it needs to act without guessing:\n"
            "- **Issue** (title + severity)\n"
            "- **File** (FULL path relative to the repo root, e.g. "
            "`src/app/module.py`) and line(s)\n"
            "- **What's wrong** (brief, with the exact code evidence)\n"
            "- **Fix** (precise steps: the exact old code to find and the new code to put "
            "in its place — a copy-pasteable sketch, not a whole-file rewrite)\n"
            "- **Verify with**: only for PYTHON fixes, the exact pytest path if a real test "
            "exists (the applier can only run pytest). For non-Python code (Dart/Flutter/JS/"
            "etc.) or when no test exists, write 'manual verification — no automated test for "
            "this file' instead of inventing a pytest path. NEVER point a non-Python fix at a "
            "pytest path.\n"
        ),
        agent=fix_suggester,
        context=[read_task, bug_task],
        output_file=REVIEW_REPORT_FILE,
    )

    apply_task = Task(
        description=(
            f"Apply the Fix Suggester's fixes to the real code in scope: '{scope}'. Work "
            "through them without asking permission.\n" + _APPLIER_POLICY
        ),
        expected_output=_APPLIER_OUTPUT,
        agent=fix_applier,
        context=[fix_task],
        output_file=FIX_RESULTS_FILE,
    )

    return [read_task, bug_task, fix_task, apply_task]


def build_apply_only_task(scope: str, fix_applier: Agent) -> Task:
    """A single task that applies fixes from an EXISTING review report.

    This skips the three analysis agents entirely (much cheaper): it reads the
    saved report and only applies what is in it — so you can review and prune the
    report first, then pay only to apply the fixes you actually want.
    """
    return Task(
        description=(
            "Read the existing review report — use read_file on "
            f"'crew/{REVIEW_REPORT_FILE}'. It contains a list of proposed fixes for code "
            f"in scope: '{scope}'.\n"
            "Apply the fixes it lists, without re-analyzing the whole project.\n"
            + _APPLIER_POLICY
        ),
        expected_output=_APPLIER_OUTPUT,
        agent=fix_applier,
        output_file=FIX_RESULTS_FILE,
    )
