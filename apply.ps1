# APPLY-ONLY — apply the fixes left in reports/review_latest.md (no re-analysis).
# Review first, prune the report to what you want, then run this.
#
# Usage:
#   .\apply.ps1 src/app
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
# The applier needs a strong model to iterate on test failures. Override in .env
# or by setting $env:APPLIER_MODEL before calling.
if (-not $env:APPLIER_MODEL) { $env:APPLIER_MODEL = "gpt-4o" }
& "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\clarity_crew.py" @args --apply-only
