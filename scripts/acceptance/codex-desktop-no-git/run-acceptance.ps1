# #722 acceptance on a real Windows machine that has only Codex Desktop: `codex` is not on PATH
# and there is no git. ASCII only (Windows PowerShell 5.1 reads BOM-less scripts in the ANSI
# code page).
#
# What it proves: `tavotto codex install` (this branch's engine, run from source) finds the CLI
# bundled with Codex Desktop, falls back to the plugin-stable zip when Codex cannot run git,
# verifies it, registers it as a local marketplace, installs + pins the plugin, and a real
# `codex exec` then hands Tavotto tools to the model. Also replays the README's manual
# PowerShell lines literally.
#
# Isolation: everything lives under $Root (default %USERPROFILE%\w722-tmp): CODEX_HOMEs,
# TAVOTTO_DATA_DIR, probe output. The user's own %USERPROFILE%\.codex is never read or written;
# `inventory` records its fingerprint and `userhome` compares it at the end. No global
# keystrokes, no app config changes. `cleanup` removes $Root and nothing else.
#
# Preparation (on the dev machine, from the branch checkout):
#   sh scripts/acceptance/codex-desktop-no-git/make-src-tar.sh HEAD w722-src.tar
# copy w722-src.tar + this directory + ..\codex-windows-launcher\ (probe-tools.ps1,
# fake-responses.ps1) to the Windows machine, keeping the two directories side by side.
#
# Order:
#   run-acceptance.ps1 -Step inventory -Tar w722-src.tar   # facts + unpack engine source + ~/.codex fingerprint
#   run-acceptance.ps1 -Step install                      # PASS: marketplace via archive, channel stable-archive, tools present
#   run-acceptance.ps1 -Step doctor                       # PASS: ok=true, nothing changed
#   run-acceptance.ps1 -Step upgrade                      # PASS: marketplace "already latest", no reinstall
#   run-acceptance.ps1 -Step manual                       # README manual lines, literally; tools present
#   run-acceptance.ps1 -Step userhome                     # PASS: user ~/.codex fingerprint unchanged
#   run-acceptance.ps1 -Step cleanup
#
# Pass criteria (report each verbatim):
#   install : JSON ok=true; steps.codex_cli.detail is ...\OpenAI\Codex\bin\<hash>\codex.exe;
#             steps.marketplace.detail mentions the zip; summary.channel.channel == stable-archive;
#             summary.archive.commit == `git rev-parse origin/plugin-stable` at test time;
#             probe verdict TOOLS_PRESENT or HEALTH_ONLY (HEALTH_ONLY = no engine for the MCP server,
#             still counts: the plugin loaded and spoke). NO_TAVOTTO_TOOLS = fail.
#   upgrade : ok=true; marketplace step skipped with "latest" wording; no `plugin add` in the output.
#   manual  : both codex commands exit 0; probe verdict as above.
param(
    [Parameter(Mandatory = $true)][string]$Step,
    [string]$Root = (Join-Path $env:USERPROFILE 'w722-tmp'),
    [string]$Tar = '',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$probe = Join-Path (Split-Path -Parent $here) 'codex-windows-launcher\probe-tools.ps1'
New-Item -ItemType Directory -Force -Path $Root | Out-Null

function Get-DesktopCodex {
    (Get-ChildItem "$env:LOCALAPPDATA\OpenAI\Codex\bin\*\codex.exe" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}

function Set-NoGitNoCodexPath {
    # PATH keeps only dirs without git.exe / codex.exe / codex.cmd: the #722 machine shape even
    # if someone installed git since. Plain string concat + File.Exists (PATH entries can be odd).
    $keep = $env:PATH -split ';' | Where-Object {
        $d = $_.Trim('"', "'")
        $d -and -not (@('git.exe', 'codex.exe', 'codex.cmd') | Where-Object { [System.IO.File]::Exists("$d\$_") })
    }
    $env:PATH = (@($keep) | Select-Object -Unique) -join ';'
}

# A failing `tavotto codex <step>` must fail the step (non-zero script exit), not just print its code:
# the env restore and later probes would otherwise leave $LASTEXITCODE at 0. `doctor` may legitimately
# exit non-zero (it reports), so it passes -AllowFailure.
function Invoke-Tavotto([string]$CodexHome, [string[]]$CliArgs, [switch]$AllowFailure) {
    $saved = @{ PYTHONPATH = $env:PYTHONPATH; CODEX_HOME = $env:CODEX_HOME; TAVOTTO_DATA_DIR = $env:TAVOTTO_DATA_DIR; PATH = $env:PATH }
    $rc = $null
    try {
        New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null
        $env:PYTHONPATH = Join-Path $Root 'src'
        $env:CODEX_HOME = $CodexHome
        $env:TAVOTTO_DATA_DIR = Join-Path $Root 'data'
        Set-NoGitNoCodexPath
        "git on PATH: " + [bool](Get-Command git -ErrorAction SilentlyContinue)
        "codex on PATH: " + [bool](Get-Command codex -ErrorAction SilentlyContinue)
        $ErrorActionPreference = 'Continue'
        & $Python -m tavotto.cli_entry codex @CliArgs 2>&1 | ForEach-Object { "$_" }
        $rc = $LASTEXITCODE
        "exit=$rc"
    }
    finally { foreach ($k in $saved.Keys) { Set-Item "env:$k" $saved[$k] } }
    if ($rc -ne 0 -and -not $AllowFailure) {
        throw "tavotto codex $($CliArgs -join ' ') exited $rc"
    }
}

function Invoke-Probe([string]$CodexHome, [string]$Name) {
    # the MCP server must not see our PYTHONPATH: Codex spawns it with this process's env
    $savedPy = $env:PYTHONPATH
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    try {
        & $probe -Codex (Get-DesktopCodex) -CodexHome $CodexHome -Out (Join-Path $Root "run-$Name")
        Get-Content (Join-Path $Root "run-$Name\summary.txt")
    }
    finally { $env:PYTHONPATH = $savedPy }
}

# Paths under ~/.codex that a running Codex Desktop rewrites on its own, unrelated to this
# acceptance -- excluded from the fingerprint, one entry per reason (relative, backslashes):
$UserCodexIgnore = @(
    @{ Pattern = '(^|\\)(log|logs|sessions|tmp|\.tmp)\\'; Why = 'Codex logs, session transcripts and scratch dirs' },
    @{ Pattern = '^models_cache\.json$'; Why = 'Codex Desktop refreshes its model list about every 271 s while running' }
)

function Get-UserCodexFingerprint {
    $d = Join-Path $env:USERPROFILE '.codex'
    if (-not (Test-Path $d)) { return 'absent' }
    (Get-ChildItem -Recurse -File $d -ErrorAction SilentlyContinue |
        Where-Object {
            $rel = $_.FullName.Substring($d.Length).TrimStart('\')
            -not ($UserCodexIgnore | Where-Object { $rel -match $_.Pattern })
        } |
        Sort-Object FullName |
        ForEach-Object { "$($_.FullName)|$($_.Length)|$($_.LastWriteTimeUtc.Ticks)" }) -join "`n"
}

switch ($Step) {
    'inventory' {
        "os: " + (Get-CimInstance Win32_OperatingSystem | ForEach-Object { "$($_.Caption) $($_.Version)" })
        foreach ($n in 'git', 'codex', 'python', 'python3', 'py', 'pipx', 'tavotto') {
            $c = @(Get-Command $n -All -ErrorAction SilentlyContinue)
            if (-not $c) { "${n}: (none)" } else { $c | ForEach-Object { "${n}: $($_.Source)" } }
        }
        $codex = Get-DesktopCodex
        "desktop codex: $codex"
        $ErrorActionPreference = 'Continue'
        "desktop codex --version: " + ((& $codex --version 2>&1) -join ' ')
        "python: " + ((& $Python -c "import sys; print(sys.version, sys.executable)" 2>&1) -join ' ')
        if ($Tar) {
            $src = Join-Path $Root 'src'
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $src
            New-Item -ItemType Directory -Force -Path $Root | Out-Null
            & tar -x -f $Tar -C $Root
            "engine source: " + (Test-Path (Join-Path $src 'tavotto\engine\codexinstall.py'))
        }
        Set-Content -Path (Join-Path $Root 'user-codex.before') -Value (Get-UserCodexFingerprint) -Encoding ascii
        "user .codex fingerprint recorded"
    }
    'install' {
        $h = Join-Path $Root 'home-install'
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $h, (Join-Path $Root 'data')
        Invoke-Tavotto $h @('install', '--json')
        'cached plugin: ' + ((Get-ChildItem -Directory (Join-Path $h 'plugins\cache\tavotto\tavotto') -ErrorAction SilentlyContinue | ForEach-Object Name) -join ', ')
        Invoke-Probe $h 'install'
    }
    'doctor' { Invoke-Tavotto (Join-Path $Root 'home-install') @('doctor', '--json') -AllowFailure }
    'upgrade' {
        $h = Join-Path $Root 'home-install'
        Invoke-Tavotto $h @('upgrade')
        Invoke-Probe $h 'upgrade'
    }
    'manual' {
        # README lines, literally, except $dir and CODEX_HOME point under $Root
        $h = Join-Path $Root 'home-manual'
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $h
        New-Item -ItemType Directory -Force -Path $h | Out-Null
        $savedHome = $env:CODEX_HOME
        $env:CODEX_HOME = $h
        try {
            Set-NoGitNoCodexPath
            $codex = (Get-ChildItem "$env:LOCALAPPDATA\OpenAI\Codex\bin\*\codex.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
            $dir = Join-Path $Root 'manual-mkt'
            Invoke-WebRequest https://github.com/Tavotto/Tavotto/archive/refs/heads/plugin-stable.zip -OutFile "$env:TEMP\tavotto-plugin-stable.zip"
            Expand-Archive "$env:TEMP\tavotto-plugin-stable.zip" $dir -Force
            $ErrorActionPreference = 'Continue'
            & $codex plugin marketplace add "$dir\Tavotto-plugin-stable" 2>&1 | ForEach-Object { "$_" }; "exit=$LASTEXITCODE"
            & $codex plugin add tavotto@tavotto 2>&1 | ForEach-Object { "$_" }; "exit=$LASTEXITCODE"
        }
        finally { $env:CODEX_HOME = $savedHome }
        Invoke-Probe $h 'manual'
    }
    'userhome' {
        $before = Get-Content -Raw (Join-Path $Root 'user-codex.before')
        $now = Get-UserCodexFingerprint
        if ($before.Trim() -eq $now.Trim()) { 'PASS: user .codex unchanged' } else { 'FAIL: user .codex changed'; Compare-Object ($before -split "`n") ($now -split "`n") }
    }
    'cleanup' {
        Set-Location $env:USERPROFILE
        Remove-Item -Recurse -Force $Root
        Remove-Item -Force -ErrorAction SilentlyContinue "$env:TEMP\tavotto-plugin-stable.zip"
        "removed $Root"
    }
    default { throw "unknown step $Step" }
}
