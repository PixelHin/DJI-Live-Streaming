@echo off
cd /d "%~dp0"
python -u "%~dp0start_server.py"
set ERR=%ERRORLEVEL%
echo %CMDCMDLINE% | find /I "/c" >nul
if %ERRORLEVEL%==0 pause
exit /b %ERR%
