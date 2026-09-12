@echo off
REM Run this once on a new machine. It is a thin wrapper: everything real happens in
REM install.py, so the double-click path and the "an agent installed it for me" path
REM cannot drift apart.
setlocal
cd /d "%~dp0"
echo === Minutes Desk setup ===
echo.

set PY=
for %%P in (py python) do (
  if not defined PY (
    %%P -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set PY=%%P
  )
)
if not defined PY (
  echo Python 3.10 or newer was not found on this machine.
  echo Install it from https://www.python.org/downloads/windows/
  echo Tick "Add python.exe to PATH" in the installer, then run this file again.
  pause
  exit /b 1
)

%PY% install.py %*
if errorlevel 1 (
  echo.
  echo Setup failed above. Fix the error and run this file again.
  pause
  exit /b 1
)
echo.
echo Opening the app.
start "" "%~dp0Minutes Desk.bat"
exit /b 0
