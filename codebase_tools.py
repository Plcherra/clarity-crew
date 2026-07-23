"""Safe, read-only codebase tools for the Clarity review crew.

Every tool is sandboxed to REPO_ROOT so an agent can never read or list files
outside the project. Paths are always resolved and checked against the root.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from crewai.tools import tool

# Repo root = the project being scanned. Defaults to the current working
# directory (so you can `cd` into any project), or CLARITY_REPO_ROOT if set.
# The --repo CLI flag calls configure_repo_root() to point at any project.
REPO_ROOT = Path(
    os.environ.get("CLARITY_REPO_ROOT", Path.cwd())
).resolve()


def configure_repo_root(path: str | Path) -> Path:
    """Re-point every tool at a different project root (used by the --repo flag).

    Tools read REPO_ROOT as a module global at call time, so reassigning it here
    re-sandboxes them to the new project without re-importing anything.
    """
    global REPO_ROOT
    REPO_ROOT = Path(path).expanduser().resolve()
    return REPO_ROOT

# Files/dirs we never want an agent to waste tokens on. Includes vendored /
# generated trees (e.g. Flutter's ephemeral plugin symlinks, CocoaPods) so
# spec-detection and search never drown in third-party README/CHANGELOG noise.
IGNORED_DIRS = {
    ".git",
    ".dart_tool",
    "node_modules",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    ".idea",
    ".wrangler",
    "dist",
    ".pytest_cache",
    "ephemeral",
    ".plugin_symlinks",
    ".symlinks",
    "Pods",
    "DerivedData",
    ".gradle",
    "vendor",
    ".next",
    "coverage",
    "target",
}
# Per-read cap. Big reads pile up context fast and can make some models (Grok)
# return empty responses, so keep this modest. Override with READ_MAX_CHARS.
MAX_READ_CHARS = int(os.environ.get("READ_MAX_CHARS", "16000"))

# --- Edit accounting (used by the auto-fix loop control) ---------------------
# edit_file bumps this on every successful write so the outer loop can tell how
# much changed in a pass and stop when a pass makes no more edits.
_EDIT_COUNT = 0

# First-seen (pre-edit) content of every file we touch this run, so restore_file
# can deterministically put a file back exactly as it started — unlike asking the
# LLM to re-edit its way back, which is unreliable.
_ORIGINAL_SNAPSHOTS: dict[str, str] = {}


def get_edit_count() -> int:
    """Total successful file edits applied so far this process."""
    return _EDIT_COUNT


def reset_edit_count() -> None:
    global _EDIT_COUNT
    _EDIT_COUNT = 0
    _ORIGINAL_SNAPSHOTS.clear()


def _syntax_error(path: Path, text: str) -> str | None:
    """Return a message if `text` is not valid Python for a .py file, else None."""
    if path.suffix not in {".py", ".pyi"}:
        return None
    import ast

    try:
        ast.parse(text)
    except SyntaxError as exc:
        return f"line {exc.lineno}: {exc.msg}"
    return None


def _read_preserving(path: Path) -> tuple[str, str]:
    """Read text as LF-normalized (so LLM \n matches) but remember the real EOL.

    Editing must not silently convert a Windows CRLF file to LF, which would flag
    the whole file as changed in git. We normalize for matching, then write back
    with the file's original line-ending style.
    """
    raw = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), newline


def _write_preserving(path: Path, text: str, newline: str) -> None:
    out = text.replace("\n", "\r\n") if newline == "\r\n" else text
    path.write_bytes(out.encode("utf-8"))


def _safe_resolve(relative_path: str) -> Path:
    """Resolve a user/agent supplied path and ensure it stays inside REPO_ROOT."""
    candidate = (REPO_ROOT / relative_path.strip().lstrip("/\\")).resolve()
    if REPO_ROOT not in candidate.parents and candidate != REPO_ROOT:
        raise ValueError(
            f"Path '{relative_path}' escapes the repository root and is not allowed."
        )
    return candidate


@tool("list_directory")
def list_directory(relative_path: str = ".") -> str:
    """List files and subfolders inside a directory of the Clarity repo.

    Input: a path relative to the repo root (use "." for the root, or e.g.
    "src/app" or "app/services"). Returns folders (with a trailing /) and
    files. Noise directories like node_modules and .git are skipped.
    """
    try:
        target = _safe_resolve(relative_path)
    except ValueError as exc:
        return str(exc)
    if not target.exists():
        return f"Path does not exist: {relative_path}"
    if not target.is_dir():
        return f"Not a directory: {relative_path}"

    entries: list[str] = []
    for child in sorted(target.iterdir()):
        if child.name in IGNORED_DIRS:
            continue
        rel = child.relative_to(REPO_ROOT).as_posix()
        entries.append(f"{rel}/" if child.is_dir() else rel)
    if not entries:
        return f"(empty) {relative_path}"
    return "\n".join(entries)


@tool("read_file")
def read_file(relative_path: str) -> str:
    """Read the contents of a single text file inside the Clarity repo.

    Input: a file path relative to the repo root, e.g.
    "src/app/main.py". Output is truncated if the file is very large so you get
    the important top portion.
    """
    try:
        target = _safe_resolve(relative_path)
    except ValueError as exc:
        return str(exc)
    if not target.exists() or not target.is_file():
        return f"File not found: {relative_path}"
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - surface any read error to the agent
        return f"Could not read {relative_path}: {exc}"

    numbered = [f"{i:>5} | {line}" for i, line in enumerate(text.splitlines(), 1)]
    body = "\n".join(numbered)
    if len(body) > MAX_READ_CHARS:
        body = body[:MAX_READ_CHARS] + "\n... [truncated: file is large]"
    return body


@tool("search_code")
def search_code(query: str, file_glob: str = "") -> str:
    """Search the Clarity repo for a text/regex pattern and show matching lines.

    Input: `query` is the text or regex to find. Optional `file_glob` narrows
    the search (e.g. "*.py" or "*.dart"). Returns file:line: matched text.
    Great for finding where a function, symbol, or string is used.
    """
    query = query.strip()
    if not query:
        return "Provide a non-empty search query."

    # Prefer ripgrep when available (fast, respects ignores), else fall back.
    rg = _which("rg")
    try:
        if rg:
            cmd = [rg, "--line-number", "--no-heading", "--color", "never", query]
            for ignored in IGNORED_DIRS:
                cmd += ["--glob", f"!{ignored}/**"]
            if file_glob:
                cmd += ["--glob", file_glob]
            cmd.append(".")
        else:
            cmd = ["grep", "-rn", query, "."]
        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Search failed: {exc}"

    out = result.stdout.strip()
    if not out:
        return f"No matches for '{query}'."
    lines = out.splitlines()
    if len(lines) > 200:
        lines = lines[:200] + [f"... [{len(lines) - 200} more matches truncated]"]
    return "\n".join(lines)


# Directory names that usually hold a project's own spec/planning docs.
SPEC_DIR_HINTS = {
    "docs",
    "doc",
    "plans",
    "plan",
    "specs",
    "spec",
    "requirements",
    "design",
}
# Filename stems (case-insensitive substring) that mark a doc as spec-like.
SPEC_NAME_HINTS = (
    "readme",
    "requirement",
    "spec",
    "roadmap",
    "vision",
    "architecture",
    "design",
    "prd",
    "plan",
    "tasks",
    "todo",
    "milestone",
    "launch",
    "backlog",
    "feature",
    "scope",
    "goal",
)
# Extensions we treat as readable spec/prose files.
SPEC_EXTS = {".md", ".markdown", ".rst", ".txt", ".adoc"}
# Cap so a huge docs tree can't flood the agent's context.
MAX_SPEC_FILES = int(os.environ.get("MAX_SPEC_FILES", "60"))


@tool("find_spec_docs")
def find_spec_docs(subpath: str = ".") -> str:
    """Auto-detect the project's OWN spec/planning docs to ground the plan.

    Walks the repo (or a `subpath` within it) and returns the paths of files that
    describe what the project is meant to be: any README, everything inside
    docs/plans/specs-style folders, and requirement-like markdown/text files
    (roadmap, vision, architecture, prd, tasks, todo, etc.). Noise dirs
    (node_modules, .git, build, ...) are skipped. Read these with read_file to
    learn the intended product and its launch criteria before scanning code.

    Output groups files as [README], [DOCS/PLANS folder], and [REQUIREMENT-LIKE],
    each with its line count so you can prioritize what to read.
    """
    try:
        base = _safe_resolve(subpath)
    except ValueError as exc:
        return str(exc)
    if not base.exists():
        return f"Path does not exist: {subpath}"

    readmes: list[tuple[str, int]] = []
    in_spec_dir: list[tuple[str, int]] = []
    named: list[tuple[str, int]] = []
    seen: set[str] = set()

    def _line_count(path: Path) -> int:
        try:
            return sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))
        except Exception:  # noqa: BLE001
            return 0

    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        root_path = Path(root)
        # Is any path component a spec-ish directory (docs/, plans/, ...)?
        try:
            parts = {p.lower() for p in root_path.relative_to(REPO_ROOT).parts}
        except ValueError:
            parts = set()
        under_spec_dir = bool(parts & SPEC_DIR_HINTS)

        for name in sorted(files):
            fpath = root_path / name
            if fpath.suffix.lower() not in SPEC_EXTS:
                continue
            rel = fpath.relative_to(REPO_ROOT).as_posix()
            if rel in seen:
                continue
            stem = name.lower()
            lines = _line_count(fpath)
            if stem.startswith("readme"):
                readmes.append((rel, lines))
                seen.add(rel)
            elif under_spec_dir:
                in_spec_dir.append((rel, lines))
                seen.add(rel)
            elif any(hint in stem for hint in SPEC_NAME_HINTS):
                named.append((rel, lines))
                seen.add(rel)

    total = len(readmes) + len(in_spec_dir) + len(named)
    if total == 0:
        return (
            f"No obvious spec/planning docs found under '{subpath}'. This project "
            "may document itself only in code/comments. Fall back to reading the "
            "top-level README (if any), list_directory, and search_code."
        )

    def _fmt(items: list[tuple[str, int]]) -> str:
        # README/root docs first, then shallowest paths, then by name.
        items = sorted(items, key=lambda it: (it[0].count("/"), it[0].lower()))
        return "\n".join(f"  {rel}  ({lines} lines)" for rel, lines in items)

    out: list[str] = [f"Found {total} candidate spec/planning file(s):"]
    if readmes:
        out.append("\n[README]\n" + _fmt(readmes))
    if in_spec_dir:
        out.append("\n[DOCS/PLANS folder]\n" + _fmt(in_spec_dir))
    if named:
        out.append("\n[REQUIREMENT-LIKE files]\n" + _fmt(named))
    if total > MAX_SPEC_FILES:
        out.append(
            f"\n(Showing all {total}; if that's a lot, read the shortest/root-level "
            "ones first — they usually state the product intent most directly.)"
        )
    return "\n".join(out)


@tool("edit_file")
def edit_file(relative_path: str, old_string: str, new_string: str) -> str:
    """Apply a minimal, exact edit to a file in the Clarity repo.

    Replaces ONE occurrence of `old_string` with `new_string` in the file at
    `relative_path`. `old_string` must appear exactly once (include enough
    surrounding context to make it unique) — otherwise the edit is rejected and
    nothing is changed. Use this to apply a small, focused fix, not to rewrite a
    whole file.
    """
    try:
        target = _safe_resolve(relative_path)
    except ValueError as exc:
        return str(exc)
    if not target.exists() or not target.is_file():
        return f"File not found: {relative_path}"
    if old_string == new_string:
        return "old_string and new_string are identical; nothing to do."

    try:
        content, newline = _read_preserving(target)
    except Exception as exc:  # noqa: BLE001
        return f"Could not read {relative_path}: {exc}"

    count = content.count(old_string)
    if count == 0:
        return (
            "old_string was not found in the file. Re-read the file and copy the "
            "exact text (including whitespace) you want to replace."
        )
    if count > 1:
        return (
            f"old_string appears {count} times; it must be unique. Add more "
            "surrounding context so it matches exactly one location."
        )

    updated = content.replace(old_string, new_string, 1)

    # Reject edits that would break Python syntax — never write invalid code.
    syntax_problem = _syntax_error(target, updated)
    if syntax_problem is not None:
        return (
            f"Edit REJECTED: it would break Python syntax ({syntax_problem}). "
            "The file was NOT changed. Fix your new_string (check indentation and "
            "block structure) and try again."
        )

    # Snapshot the original the first time we touch this file, for restore_file.
    key = target.as_posix()
    if key not in _ORIGINAL_SNAPSHOTS:
        _ORIGINAL_SNAPSHOTS[key] = (content, newline)

    try:
        _write_preserving(target, updated, newline)
    except Exception as exc:  # noqa: BLE001
        return f"Could not write {relative_path}: {exc}"
    global _EDIT_COUNT
    _EDIT_COUNT += 1
    return f"Applied edit to {relative_path} (1 replacement). [total edits: {_EDIT_COUNT}]"


@tool("restore_file")
def restore_file(relative_path: str) -> str:
    """Restore a file to exactly its original contents (before any edits this run).

    Use this to cleanly revert a fix when its tests fail — it puts the file back
    exactly as it started, unlike trying to edit your way back. Input: the file
    path relative to the repo root.
    """
    try:
        target = _safe_resolve(relative_path)
    except ValueError as exc:
        return str(exc)
    key = target.as_posix()
    if key not in _ORIGINAL_SNAPSHOTS:
        return f"No original snapshot for {relative_path}; it was not edited this run."
    original, newline = _ORIGINAL_SNAPSHOTS[key]
    try:
        _write_preserving(target, original, newline)
    except Exception as exc:  # noqa: BLE001
        return f"Could not restore {relative_path}: {exc}"
    return f"Restored {relative_path} to its original contents."


def _resolve_test_cwd() -> Path:
    """Directory the tests run from. Defaults to the repo root.

    Override with TEST_CWD for monorepos where imports/conftest need a specific
    working dir (e.g. TEST_CWD=services/api). An out-of-repo override is ignored.
    """
    override = os.environ.get("TEST_CWD", "").strip()
    if override:
        candidate = Path(override)
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        candidate = candidate.resolve()
        if candidate == REPO_ROOT or REPO_ROOT in candidate.parents:
            return candidate
    return REPO_ROOT


def _test_python(cwd: Path) -> str:
    """Interpreter for the default pytest runner. Override with TEST_PYTHON.

    Prefers a project `.venv` (in the test cwd, then the repo root) so tests run
    with the target project's own dependencies; otherwise falls back to the
    interpreter running this tool.
    """
    override = os.environ.get("TEST_PYTHON", "").strip()
    if override:
        return override
    for base in (cwd, REPO_ROOT):
        for candidate in (
            base / ".venv" / "Scripts" / "python.exe",  # Windows
            base / ".venv" / "bin" / "python",           # posix
        ):
            if candidate.exists():
                return str(candidate)
    return sys.executable or "python"


@tool("run_tests")
def run_tests(test_target: str = "") -> str:
    """Run the project's tests (pytest by default) and report pass/fail output.

    `test_target` is an optional path (relative to the repo root) to the tests to
    run — a file or directory (e.g. "tests" or "tests/test_api.py"). With no
    target, the whole suite runs.

    Project-agnostic and configurable via env vars (no assumptions about layout):
      * TEST_COMMAND — a full custom test command (e.g. "npm test" or
        "poetry run pytest"); the resolved target is appended. Use for non-pytest
        projects.
      * TEST_CWD — directory to run tests from, for monorepos where imports need a
        specific working dir. Defaults to the repo root.
      * TEST_PYTHON — interpreter for the default pytest runner.
    Returns the runner's summary output so you can tell if the change works.
    """
    target = test_target.strip()
    cwd = _resolve_test_cwd()

    # Resolve + sandbox the target against the repo root, then express it relative
    # to the run cwd where possible.
    resolved_arg = ""
    if target:
        abs_target = (REPO_ROOT / target).resolve()
        if REPO_ROOT not in abs_target.parents and abs_target != REPO_ROOT:
            return f"Test target '{target}' escapes the repository root."
        try:
            resolved_arg = abs_target.relative_to(cwd).as_posix()
        except ValueError:
            resolved_arg = str(abs_target)

    custom = os.environ.get("TEST_COMMAND", "").strip()
    if custom:
        cmd = shlex.split(custom)
        if resolved_arg:
            cmd.append(resolved_arg)
        runner = "tests"
    else:
        cmd = [_test_python(cwd), "-m", "pytest", "-q", resolved_arg or "."]
        runner = "pytest"

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Failed to run tests: {exc}"

    output = (result.stdout or "") + (result.stderr or "")
    output = output.strip() or "(no output)"
    if len(output) > MAX_READ_CHARS:
        output = output[-MAX_READ_CHARS:]  # keep the tail (summary lives there)
    status = "PASSED" if result.returncode == 0 else "FAILED"
    return (
        f"[{runner} {status}] (exit {result.returncode})\n"
        f"cwd={cwd}\ncmd={' '.join(cmd)}\n\n{output}"
    )


def _which(program: str) -> str | None:
    from shutil import which

    return which(program)
