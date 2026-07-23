# PLAN a project (read-only) — auto-detects its spec + scans code, then writes
# reports/launch_plan.md: ranked BUILD/FIX items, each with a plain-English
# summary and an approval-ready builder prompt. Never edits your files.
#
# Usage:
#   .\plan.ps1                                  # plan the whole repo (cwd)
#   .\plan.ps1 src                              # focus one folder/area
#   .\plan.ps1 --repo C:\path\to\project        # plan another project
#   .\plan.ps1 app\services --repo C:\path\to\project
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
& "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\clarity_planner.py" @args
