# FIX — analyze AND apply in one shot (one pass). Edits files.
#
# Usage:
#   .\fix.ps1 services/rex-api/app/services
#   .\fix.ps1 services/rex-api/app/services --rounds 3   # loop up to 3 passes
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
# The applier needs a strong model to iterate on test failures. Override in .env
# or by setting $env:APPLIER_MODEL before calling.
if (-not $env:APPLIER_MODEL) { $env:APPLIER_MODEL = "gpt-4o" }
& "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\clarity_crew.py" @args --fix
