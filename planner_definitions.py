"""Agent and task definitions for the Clarity Planner (Phase 1).

The Planner turns a project's OWN docs + code into a ranked, approval-ready
launch plan. It is READ-ONLY — it never edits files. Three agents run in
sequence:

  1. SpecReader  -> auto-detects and reads the project's spec (README, docs/,
                    plans/, requirement-like *.md) and distills the intended
                    product + the project's own launch/acceptance criteria.
  2. CodeScanner -> scans the code in scope, maps what actually exists, and
                    compares it to the spec: what's MISSING/incomplete (BUILD)
                    and what's present-but-broken (FIX), each with file:line
                    evidence it actually read.
  3. PlanWriter  -> writes reports/launch_plan.md: ranked BUILD/FIX items on the
                    critical path, each with a plain-English summary AND a
                    ready-to-run builder prompt (goal + acceptance + constraints
                    + files/area + how to verify).

Truth principle (non-negotiable): the plan describes what NEEDS doing. It never
claims anything is already built, fixed, or verified. Where the scanner is not
certain, it says "suspected" and cites what it saw. Nothing is invented.
"""

from __future__ import annotations

from crewai import Agent, Task

REPORTS_DIR = "reports"
LAUNCH_PLAN_FILE = f"{REPORTS_DIR}/launch_plan.md"


def build_planner_agents(llm, tools) -> tuple[Agent, Agent, Agent]:
    """Create the three read-only Planner agents sharing the same LLM + tools."""
    spec_reader = Agent(
        role="Spec Reader",
        goal=(
            "Discover and read the project's OWN specification so the plan is "
            "grounded in the project's real intent — never in assumptions. Produce "
            "a faithful summary of what the project is meant to be and the "
            "launch/acceptance criteria the docs themselves imply."
        ),
        backstory=(
            "You are a careful product engineer dropped into an unfamiliar codebase. "
            "You never guess what a project is 'probably' for. You call find_spec_docs "
            "to locate the README, docs/, plans/, and requirement-like files, then "
            "read the most telling ones (shortest/root-level first) with read_file, "
            "and use search_code to confirm details. You distinguish clearly between "
            "what the docs actually say and what is merely implied. If the docs are "
            "thin, you say so honestly rather than inventing a spec."
        ),
        tools=tools,
        llm=llm,
        allow_delegation=False,
        verbose=True,
        max_iter=25,
    )

    code_scanner = Agent(
        role="Code Scanner",
        goal=(
            "Compare the real code against the spec and find, with concrete "
            "file:line evidence, (a) what the project still needs to BUILD to reach "
            "launch and (b) what is present but BROKEN and must be FIXed. Focus on "
            "the critical path to launch, not cosmetics."
        ),
        backstory=(
            "You are a skeptical senior engineer who only reports what you can point "
            "to in code you actually read. You use list_directory, read_file, and "
            "search_code to map what exists. A BUILD item is a spec capability that "
            "is missing, stubbed, or half-wired (empty function bodies, "
            "NotImplementedError, TODO/FIXME, a route with no handler, a feature the "
            "spec requires with no implementation). A FIX item is real code on a live "
            "path that is wrong (logic error, bad None/empty handling that actually "
            "occurs, broken async/await, data-scoping/security issues, silent failures "
            "or fake-success patterns). You never invent problems; when you are not "
            "certain a path is exercised, you label it 'suspected' and cite the exact "
            "evidence. You ignore style/naming/formatting."
        ),
        tools=tools,
        llm=llm,
        allow_delegation=False,
        verbose=True,
        max_iter=30,
    )

    plan_writer = Agent(
        role="Plan Writer",
        goal=(
            "Turn the spec summary and the scan findings into a ranked, "
            "critical-path launch plan a non-coder can act on — each item a plain "
            "English summary plus a self-contained, approval-ready builder prompt."
        ),
        backstory=(
            "You are a technical lead who writes tasks so clearly that a builder (a "
            "person or an AI coding agent) can execute them without asking questions, "
            "and a non-coder can approve them with confidence. You rank strictly by "
            "what actually blocks launch: the fewest items that unblock the most. "
            "Every item you write includes a ready-to-run builder prompt (goal, "
            "acceptance criteria, constraints, exact files/area) and an honest "
            "verification note. You never claim work is done or verified — this is a "
            "plan of what to do next, phrased in plain English first."
        ),
        tools=tools,
        llm=llm,
        allow_delegation=False,
        verbose=True,
        max_iter=20,
    )

    return spec_reader, code_scanner, plan_writer


# Shared builder-prompt instructions used by the Plan Writer in either mode.
_BUILDER_PROMPT_RULES = (
    "For EACH item, write a self-contained, approval-ready builder prompt so the user "
    "never has to phrase a prompt by hand. The prompt must give a builder (human or AI "
    "agent) everything needed: a clear goal, concrete acceptance criteria, constraints "
    "(respect the architecture, minimal changes, no weakening tests), the exact "
    "files/area, and how the result will be verified.\n"
    "TRUTH: this is a plan of what to do. Never state anything is already built, fixed, "
    "or verified. For verification, only name a real pytest path when a Python test "
    "plausibly covers it; otherwise write 'unverified — no automated test (needs manual "
    "check)'."
)

# The per-item shape + closing note are identical across modes.
_PLAN_ITEM_SHAPE = (
    "A numbered list. For EACH item use exactly this shape:\n\n"
    "### N. [BUILD|FIX] <short title> — Impact: <Critical|High|Medium>\n"
    "**In plain English:** <what this is and why it matters, no jargon>\n"
    "**Where:** <relative file path(s)/area>\n"
    "**Evidence:** <file:line facts the scan found; 'suspected' if unsure>\n\n"
    "**Builder prompt (approval-ready):**\n"
    "```\n"
    "Goal: <one clear sentence of what to build/fix>\n"
    "Acceptance criteria:\n"
    "- <observable, checkable outcome>\n"
    "- <...>\n"
    "Constraints: <respect existing architecture; minimal, focused change; "
    "do not weaken or delete tests; ask if scope grows>\n"
    "Files / area: <relative paths to touch>\n"
    "Verify: <exact pytest path if one plausibly applies, else 'unverified — "
    "no automated test (needs manual check)'>\n"
    "```\n\n"
    "End with a one-line note: 'Nothing here has been built or verified — "
    "approve an item to run it through a builder.'"
)


def build_planner_tasks(
    scope: str,
    spec_reader: Agent,
    code_scanner: Agent,
    plan_writer: Agent,
    goal: str | None = None,
    mode: str = "launch-review",
) -> list[Task]:
    """Build the sequential Planner tasks.

    Two modes:
    - launch-review (default, no goal): plan what the project needs to reach launch.
    - create-feature (a plain-English `goal` is given): plan what to build/fix to
      deliver that specific request.
    """
    goal = (goal or "").strip()
    feature_mode = mode == "create-feature" and bool(goal)
    title = "# Build Plan" if feature_mode else "# Launch Plan"

    if feature_mode:
        spec_description = (
            f'The user asked for this, in plain English:\n"{goal}"\n\n'
            "Your job: understand THIS project well enough to deliver exactly that "
            f"request. Scope hint for the code area: '{scope}'.\n"
            "1. Call find_spec_docs to locate README, docs/, plans/, requirement-like "
            "files.\n"
            "2. Read the most relevant ones with read_file; use search_code to find the "
            "part of the app the request touches.\n"
            "3. Distill: what the app is, and specifically what the user's request means "
            "inside this app — the behavior/feature they want and where it belongs.\n"
            "Ground everything in the docs + code. Do not invent requirements the user "
            "did not ask for."
        )
        spec_output = (
            "A concise brief with:\n"
            "- **Product**: 2-4 plain-English sentences on what the app is.\n"
            "- **The request, understood**: what the user wants in this app's own terms, "
            "and the area/feature it belongs to.\n"
            "- **Spec sources**: the doc paths you relied on.\n"
            "- **Open assumptions**: anything you had to assume to interpret the request."
        )
    else:
        spec_description = (
            "Discover and read THIS project's own specification to learn what it is "
            f"meant to be. Scope hint for later code work: '{scope}' (but read the "
            "spec across the whole project).\n"
            "1. Call find_spec_docs to locate the README, docs/, plans/, and "
            "requirement-like files.\n"
            "2. Read the most informative ones with read_file (start with the "
            "shortest/root-level docs — they usually state intent most directly). "
            "Use search_code to confirm specifics.\n"
            "3. Distill: what is this product, who is it for, and — most important — "
            "what does IT consider necessary to launch? Capture any explicit "
            "acceptance/'done' criteria, MVP scope, roadmap items, and known "
            "blockers the docs mention.\n"
            "Ground everything in the docs. If the docs are thin or silent on "
            "launch, say so plainly — do not invent requirements."
        )
        spec_output = (
            "A concise brief with:\n"
            "- **Product**: 2-4 plain-English sentences on what the project is and who "
            "it's for.\n"
            "- **Spec sources**: the doc paths you actually relied on.\n"
            "- **Launch criteria**: a bullet list of what the project's own docs say "
            "it needs to reach launch (features, acceptance criteria, roadmap, known "
            "blockers). Mark anything you had to infer as (inferred).\n"
            "- **Gaps in the docs**: what the docs do NOT specify, if relevant."
        )

    spec_task = Task(
        description=spec_description,
        expected_output=spec_output,
        agent=spec_reader,
    )

    if feature_mode:
        scan_description = (
            f'The user\'s request:\n"{goal}"\n\n'
            "Using the Spec Reader's brief, scan the REAL code to find exactly what must "
            f"change to deliver this request. Focus on the area it touches (scope hint: "
            f"'{scope}').\n"
            "Read actual code with list_directory, read_file, and search_code before "
            "asserting anything. Classify each finding as:\n"
            "- **BUILD** — something new to add to deliver the request (a missing "
            "capability, new file/function, or new wiring).\n"
            "- **FIX** — existing code on the request's path that is broken and would "
            "stop it working.\n"
            "For every finding, cite file:line evidence you actually read. Prioritize "
            "what's needed for THIS request; mention other issues only if they directly "
            "block it. Do NOT report style/naming/formatting or speculative "
            "'might/could' issues."
        )
        scan_output = (
            "Two lists, BUILD items and FIX items needed to deliver the request. Each "
            "finding: a short title, BUILD/FIX, an estimated impact on delivering the "
            "request (Critical / High / Medium), the file(s) and line(s) (relative "
            "paths), 1-3 sentences of the exact code evidence, and — for BUILD — what "
            "'done' looks like. Note honestly if part of the request is already built."
        )
    else:
        scan_description = (
            "Using the Spec Reader's brief, scan the real code to find what stands "
            f"between this project and launch. Concentrate on this scope: '{scope}' "
            "(read beyond it only to understand connections).\n"
            "Read the actual code with list_directory, read_file, and search_code "
            "before asserting anything. Classify each finding as:\n"
            "- **BUILD** — a launch-critical capability from the spec that is "
            "missing, stubbed, or only half-wired (empty/`pass` bodies, "
            "NotImplementedError, TODO/FIXME, unhandled route, no implementation).\n"
            "- **FIX** — existing code on a real path that is broken (logic error, "
            "None/empty mishandling that actually occurs, broken async/await, "
            "data-scoping/security bug, silent failure or fake-success).\n"
            "For every finding, cite file:line evidence you actually read. If you are "
            "unsure a path is exercised, label it 'suspected' and say why. Do NOT "
            "report style/naming/formatting or speculative 'might/could' issues. It is "
            "fine to report few items — quality and launch-relevance over quantity."
        )
        scan_output = (
            "Two lists, BUILD items and FIX items. Each finding: a short title, "
            "BUILD/FIX, an estimated launch impact (Critical / High / Medium), the "
            "file(s) and line(s) (relative paths), 1-3 sentences of the exact code "
            "evidence, and — for BUILD — what 'done' should look like. If the code "
            "already satisfies part of the spec, note that honestly too."
        )

    scan_task = Task(
        description=scan_description,
        expected_output=scan_output,
        agent=code_scanner,
        context=[spec_task],
    )

    if feature_mode:
        plan_description = (
            "Write a build plan a non-coder can act on to deliver their request:\n"
            f'"{goal}"\n'
            f"Combine the spec brief and the scan findings (scope hint '{scope}').\n"
            "Rank the items so that doing them in order delivers the request; put "
            "anything that blocks the rest first. Keep it to the fewest items that "
            "deliver the request. Merge duplicates.\n" + _BUILDER_PROMPT_RULES
        )
        plan_sections = (
            "## What you asked for\n"
            "The user's request restated plainly (1-2 sentences).\n\n"
            "## How it fits your app\n"
            "2-4 plain-English sentences on the relevant part of the app, plus the spec "
            "sources.\n\n"
            "## Plan (in order)\n"
        )
    else:
        plan_description = (
            "Write the launch plan a non-coder can act on, combining the spec brief "
            f"and the scan findings for scope '{scope}'.\n"
            "Rank items by what truly blocks launch first (a Critical FIX that breaks "
            "a core flow outranks a nice-to-have BUILD). Keep the list focused on the "
            "critical path — the fewest items that unblock launch. Merge duplicates.\n"
            + _BUILDER_PROMPT_RULES
        )
        plan_sections = (
            "## What this project is\n"
            "2-4 plain-English sentences grounded in the docs, plus the spec sources.\n\n"
            "## Launch readiness snapshot\n"
            "- What already appears present/working (honest, from the scan)\n"
            "- What's on the critical path to launch (one line each)\n\n"
            "## Ranked plan (critical path first)\n"
        )

    plan_task = Task(
        description=plan_description,
        expected_output=(
            f"Output ONLY the Markdown document itself, beginning exactly with the "
            f"line '{title}'. Do NOT wrap the whole document in a code fence and do NOT "
            f"write a 'Thought:' preface — just the document.\n\n"
            f"A Markdown document titled '{title}' with these sections:\n\n"
            + plan_sections
            + _PLAN_ITEM_SHAPE
        ),
        agent=plan_writer,
        context=[spec_task, scan_task],
        output_file=LAUNCH_PLAN_FILE,
    )

    return [spec_task, scan_task, plan_task]
