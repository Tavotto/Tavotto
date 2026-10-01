@echo off
rem Tavotto MCP launcher, Windows half (issue #266). `codex.mcp.json` says `command: ./mcp/launch`;
rem Codex's program resolver on Windows appends PATHEXT and runs this file (measured on a real
rem Windows 11 + Codex Desktop, codex-cli 0.158). mcp/launch is the POSIX half.
rem `python3` on Windows is often the Microsoft Store alias (exit 9009, no output), so this file
rem finds a Python that really runs and hands it every argument unchanged; locating the engine
rem stays with mcp/server.py.
rem Rules (codex-plugin/AGENTS.md, docs/rules/plugin/mcp-launcher-and-provision.md):
rem `@echo off` is the FIRST line: anything cmd echoes lands on stdout before the first JSON-RPC
rem frame, and Codex (rmcp) drops the server on it ("expected value at line 1 column 1", #266).
rem Keep the file ASCII, no goto / call :label, probe every candidate by running it (and checking
rem its version).
rem Probes go through `call`: a candidate may itself be a batch file (pyenv-win shims are python.bat), and a
rem batch file run without `call` never returns -- this launcher would stop at the first probe.
setlocal EnableExtensions DisableDelayedExpansion
set "TAVOTTO_LAUNCH_PY="
set "TAVOTTO_LAUNCH_PYARG="
rem The probe must reject a Python too old to parse mcp/server.py (Python 2 passes `import sys`).
set "TAVOTTO_LAUNCH_PROBE=import sys; sys.exit(sys.version_info[:2] < (3, 8))"
rem The managed venv is preferred only inside the engine's range (PYTHON_MIN..PYTHON_MAX_EXCLUSIVE in
rem mcp/server.py): outside it --provision must rebuild that venv, which Windows cannot do while this
rem server runs from its python.exe. Then it is only the last resort, after every external Python.
set "TAVOTTO_LAUNCH_PROBE_MANAGED=import sys; sys.exit(not ((3, 10) <= sys.version_info[:2] < (3, 15)))"
if defined TAVOTTO_MCP_PYTHON if exist "%TAVOTTO_MCP_PYTHON%" call "%TAVOTTO_MCP_PYTHON%" -c "%TAVOTTO_LAUNCH_PROBE%" >nul 2>nul && set "TAVOTTO_LAUNCH_PY=%TAVOTTO_MCP_PYTHON%"
set "TAVOTTO_LAUNCH_CFG=%TAVOTTO_CONFIG_DIR%"
if not defined TAVOTTO_LAUNCH_CFG set "TAVOTTO_LAUNCH_CFG=%APPDATA%\Tavotto"
if not defined TAVOTTO_LAUNCH_PY if exist "%TAVOTTO_LAUNCH_CFG%\mcp-runtime\venv\Scripts\python.exe" call "%TAVOTTO_LAUNCH_CFG%\mcp-runtime\venv\Scripts\python.exe" -c "%TAVOTTO_LAUNCH_PROBE_MANAGED%" >nul 2>nul && set "TAVOTTO_LAUNCH_PY=%TAVOTTO_LAUNCH_CFG%\mcp-runtime\venv\Scripts\python.exe"
if not defined TAVOTTO_LAUNCH_PY call py -3 -c "%TAVOTTO_LAUNCH_PROBE%" >nul 2>nul && set "TAVOTTO_LAUNCH_PY=py" && set "TAVOTTO_LAUNCH_PYARG=-3"
for %%N in (python python3) do for /f "delims=" %%P in ('where %%N 2^>nul') do if not defined TAVOTTO_LAUNCH_PY call "%%P" -c "%TAVOTTO_LAUNCH_PROBE%" >nul 2>nul && set "TAVOTTO_LAUNCH_PY=%%P"
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*" "%ProgramFiles%\Python3*") do if not defined TAVOTTO_LAUNCH_PY if exist "%%~D\python.exe" call "%%~D\python.exe" -c "%TAVOTTO_LAUNCH_PROBE%" >nul 2>nul && set "TAVOTTO_LAUNCH_PY=%%~D\python.exe"
if not defined TAVOTTO_LAUNCH_PY if exist "%TAVOTTO_LAUNCH_CFG%\mcp-runtime\venv\Scripts\python.exe" call "%TAVOTTO_LAUNCH_CFG%\mcp-runtime\venv\Scripts\python.exe" -c "%TAVOTTO_LAUNCH_PROBE%" >nul 2>nul && set "TAVOTTO_LAUNCH_PY=%TAVOTTO_LAUNCH_CFG%\mcp-runtime\venv\Scripts\python.exe"
if not defined TAVOTTO_LAUNCH_PY >&2 echo tavotto-mcp: no runnable Python found (tried TAVOTTO_MCP_PYTHON, the plugin-managed env, py -3, python/python3 on PATH, %%LOCALAPPDATA%%\Programs\Python). Install Python 3.10+ and restart Codex, or run: tavotto codex install
if not defined TAVOTTO_LAUNCH_PY exit /b 9009
"%TAVOTTO_LAUNCH_PY%" %TAVOTTO_LAUNCH_PYARG% %*
exit /b %ERRORLEVEL%
