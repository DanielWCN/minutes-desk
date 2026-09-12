@echo off
REM Double-click this. It starts a local server and opens the app in your browser.
REM Nothing leaves this machine: the app never calls out except pip and the model
REM download, and both of those only happen when you click a button.
setlocal
set PORT=8760
set PY=%~dp0.venv\Scripts\python.exe
REM MINUTESDESK_PY lets you point at an environment you keep somewhere else.
if not exist "%PY%" if defined MINUTESDESK_PY set PY=%MINUTESDESK_PY%
if not exist "%PY%" (
  echo Python environment not found. Run  python install.py  once first
  echo ^(or double-click Setup.bat^).
  pause
  exit /b 1
)
title Minutes Desk - close this window to stop the app

REM Any earlier copy of this tool is stopped first. This matters more than it looks:
REM the page is re-read from disk every refresh but the Python is not, so a window left
REM running from an older start answers new HTML with old code, and every symptom after
REM that looks like a data bug. Command line, not port, is what identifies it - two
REM copies can end up bound to the same port at once.
powershell -NoProfile -Command "$t = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*app.py --port %PORT%*' }); foreach($x in $t){ Write-Host ('stopping an older Minutes Desk: pid ' + $x.ProcessId + ', started ' + $x.CreationDate) ; Stop-Process -Id $x.ProcessId -Force -ErrorAction SilentlyContinue }; if($t.Count -gt 0){ Start-Sleep -Milliseconds 900 }; $o = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; foreach($id in ($o.OwningProcess | Select-Object -Unique)){ $pr = Get-Process -Id $id -ErrorAction SilentlyContinue; if($pr){ Write-Host ('NOTE: port %PORT% is still held by ' + $pr.ProcessName + ' (pid ' + $id + ') - if the app does not start, close that program') } }"

cd /d "%~dp0mmt"
"%PY%" -u app.py --port %PORT%
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
  echo app stopped with an error ^(exit code %RC%^). The last lines above say why.
  echo   - "10048" / "already running": something else is on port %PORT%.
  echo   - "ModuleNotFoundError": run Setup.bat once.
) else (
  echo app stopped.
)
pause
