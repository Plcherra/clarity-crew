# PLAN a project (read-only) — turns a plain-English request or a preset plus the
# project's docs + code into reports/launch_plan.md: ranked BUILD/FIX items, each
# with a plain-English summary and an approval-ready builder prompt. Never edits files.
#
# Presets: launch-review (default) · find-bugs · create-feature
#
# Usage:
#   .\plan.ps1                                        # launch review of the repo (cwd)
#   .\plan.ps1 src                                    # focus the scan on one folder/area
#   .\plan.ps1 --repo C:\path\to\project              # review another project
#   .\plan.ps1 --preset find-bugs                     # hunt for bugs (review engine)
#   .\plan.ps1 --goal "let users reset their password"  # create-feature (asks if vague)
#   .\plan.ps1 --preset create-feature --goal "..." --repo C:\path\to\project
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
& "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\clarity_planner.py" @args
