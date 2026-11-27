@echo off
if "%~1"=="" (
  start "" pythonw "%USERPROFILE%\.gemini\config\plugins\codex-orchestrator\skills\codex-orchestrator\scripts\orchestrate.py" ui --open
) else (
  python "%USERPROFILE%\.gemini\config\plugins\codex-orchestrator\skills\codex-orchestrator\scripts\orchestrate.py" %*
)
