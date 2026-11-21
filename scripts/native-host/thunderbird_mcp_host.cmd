@echo off
rem Native Messaging host launcher. Thunderbird starts this file; it must not print anything
rem to standard output except Native Messaging frames, so every diagnostic goes to stderr.
setlocal
for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" goto nopython
"%PYTHON%" -u -m thunderbird_mcp.native_host
exit /b %ERRORLEVEL%

:nopython
echo thunderbird-mcp: the virtual environment was not found under %ROOT%. Run "uv sync" there first. 1>&2
exit /b 1
