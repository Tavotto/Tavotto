#!/bin/sh
:<<'::TAVOTTO_WINDOWS'
@echo off
rem Tavotto MCP launcher (issue #266). One file: a sh script on POSIX, a batch file on Windows.
rem Codex runs `.mcp.json`'s single `command` string with no shell and no per-platform field;
rem `python3` on Windows is often the Microsoft Store alias (exit 9009, no output), so the
rem plugin loaded with zero tools. This file only finds a Python that really runs and hands
rem it every argument unchanged; locating the engine stays with mcp/server.py.
rem Rules (see codex-plugin/AGENTS.md, "launch.cmd"): keep the shebang on line 1, keep the
rem file LF + ASCII in this batch part, no goto / call :label, probe every candidate by running it (and checking its version).
setlocal EnableExtensions DisableDelayedExpansion
set "TAVOTTO_LAUNCH_PY="
set "TAVOTTO_LAUNCH_PYARG="
rem The probe must reject a Python too old to parse mcp/server.py (Python 2 passes `import sys`).
set "TAVOTTO_LAUNCH_PROBE=import sys; sys.exit(sys.version_info[:2] < (3, 8))"
if defined TAVOTTO_MCP_PYTHON if exist "%TAVOTTO_MCP_PYTHON%" "%TAVOTTO_MCP_PYTHON%" -c "%TAVOTTO_LAUNCH_PROBE%" >nul 2>nul && set "TAVOTTO_LAUNCH_PY=%TAVOTTO_MCP_PYTHON%"
set "TAVOTTO_LAUNCH_CFG=%TAVOTTO_CONFIG_DIR%"
if not defined TAVOTTO_LAUNCH_CFG set "TAVOTTO_LAUNCH_CFG=%APPDATA%\Tavotto"
if not defined TAVOTTO_LAUNCH_PY if exist "%TAVOTTO_LAUNCH_CFG%\mcp-runtime\venv\Scripts\python.exe" "%TAVOTTO_LAUNCH_CFG%\mcp-runtime\venv\Scripts\python.exe" -c "%TAVOTTO_LAUNCH_PROBE%" >nul 2>nul && set "TAVOTTO_LAUNCH_PY=%TAVOTTO_LAUNCH_CFG%\mcp-runtime\venv\Scripts\python.exe"
if not defined TAVOTTO_LAUNCH_PY py -3 -c "%TAVOTTO_LAUNCH_PROBE%" >nul 2>nul && set "TAVOTTO_LAUNCH_PY=py" && set "TAVOTTO_LAUNCH_PYARG=-3"
for %%N in (python python3) do for /f "delims=" %%P in ('where %%N 2^>nul') do if not defined TAVOTTO_LAUNCH_PY "%%P" -c "%TAVOTTO_LAUNCH_PROBE%" >nul 2>nul && set "TAVOTTO_LAUNCH_PY=%%P"
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*" "%ProgramFiles%\Python3*") do if not defined TAVOTTO_LAUNCH_PY if exist "%%~D\python.exe" "%%~D\python.exe" -c "%TAVOTTO_LAUNCH_PROBE%" >nul 2>nul && set "TAVOTTO_LAUNCH_PY=%%~D\python.exe"
if not defined TAVOTTO_LAUNCH_PY >&2 echo tavotto-mcp: no runnable Python found (tried TAVOTTO_MCP_PYTHON, the plugin-managed env, py -3, python/python3 on PATH, %%LOCALAPPDATA%%\Programs\Python). Install Python 3.10+ and restart Codex, or run: tavotto codex install
if not defined TAVOTTO_LAUNCH_PY exit /b 9009
"%TAVOTTO_LAUNCH_PY%" %TAVOTTO_LAUNCH_PYARG% %*
exit /b %ERRORLEVEL%
::TAVOTTO_WINDOWS
# ---- POSIX (sh): exactly what `command: python3` did before ----
exec python3 "$@"
