@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.10+ was not found. Please install Python and add it to PATH.
  pause
  exit /b 1
)

%PY% -m pp.api --db data\yungu_pp.db --port 8000 --seed
pause
