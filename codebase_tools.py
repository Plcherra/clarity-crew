"""Safe, read-only codebase tools for the Clarity review crew.

Every tool is sandboxed to REPO_ROOT so an agent can never read or list files
outside the project. Paths are always resolved and checked against the root.
"""

from __future__ import annotations

import os
import subprocess
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

# Files/dirs we never want an agent to waste tokens on.
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
    "services/rex-api/app/services"). Returns folders (with a trailing /) and
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
    "services/rex-api/app/services/tiny_system_prompt.py". Output is truncated
    if the file is very large so you get the important top portion.
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


BACKEND_DIR = (REPO_ROOT / "services" / "rex-api").resolve()


def _backend_python() -> str:
    """Path to the backend's own venv Python (which has pytest + app deps).

    The crew's venv does not have the backend dependencies, so tests must run
    with the backend interpreter. Override with TEST_PYTHON.
    """
    override = os.environ.get("TEST_PYTHON")
    if override:
        return override
    candidates = [
        BACKEND_DIR / ".venv" / "Scripts" / "python.exe",  # Windows
        BACKEND_DIR / ".venv" / "bin" / "python",           # posix
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "python"  # last resort


@tool("run_tests")
def run_tests(test_target: str = "") -> str:
    """Run pytest to verify a fix, then report pass/fail output.

    `test_target` is an optional path relative to the repo root pointing at the
    tests to run (e.g. "services/rex-api/tests/test_open_thread_suggestion_flow.py"
    or "services/rex-api/tests"). Backend tests run inside services/rex-api using
    the backend's own Python environment. Returns pytest's summary output so you
    can tell if the fix works.
    """
    target = test_target.strip()
    # Resolve the target against the REPO ROOT (not the crew cwd) so paths like
    # "services/rex-api/tests" map correctly.
    if not target:
        cwd = BACKEND_DIR
        pytest_arg = "tests"
    else:
        abs_target = (REPO_ROOT / target).resolve()
        if REPO_ROOT not in abs_target.parents and abs_target != REPO_ROOT:
            return f"Test target '{target}' escapes the repository root."
        try:
            # Backend tests run from services/rex-api for imports/conftest to work.
            pytest_arg = abs_target.relative_to(BACKEND_DIR).as_posix()
            cwd = BACKEND_DIR
        except ValueError:
            pytest_arg = abs_target.relative_to(REPO_ROOT).as_posix()
            cwd = REPO_ROOT

    cmd = [_backend_python(), "-m", "pytest", "-q", pytest_arg]

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
        f"[pytest {status}] (exit {result.returncode})\n"
        f"cwd={cwd}\ntarget={pytest_arg}\n\n{output}"
    )


def _which(program: str) -> str | None:
    from shutil import which

    return which(program)
