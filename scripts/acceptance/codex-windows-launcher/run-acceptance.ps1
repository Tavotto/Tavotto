# #266 acceptance on a real Windows machine. ASCII only (Windows PowerShell 5.1 reads BOM-less
# scripts in the ANSI code page).
#
# Everything lives under $Root (default %USERPROFILE%\w266-tmp): isolated CODEX_HOMEs, the local
# marketplace, probe output. The user's own %USERPROFILE%\.codex (login, plugins, config) is never
# read or written. `cleanup` removes $Root and nothing else.
#
#   run-acceptance.ps1 -Step inventory
#   run-acceptance.ps1 -Step stable          # README main path from GitHub plugin-stable, then probe
#   run-acceptance.ps1 -Step counterfactual  # same install, command forced back to "python3" (the #266 shape)
#   run-acceptance.ps1 -Step nopython        # launcher with every Python hidden: what does the user get?
#   run-acceptance.ps1 -Step upgrade         # `codex plugin marketplace upgrade tavotto`, then probe again
#   run-acceptance.ps1 -Step branch -Tar x   # install a branch packed by make-local-marketplace.sh, probe
#   run-acceptance.ps1 -Step cleanup
param(
    [Parameter(Mandatory = $true)][string]$Step,
    [string]$Root = (Join-Path $env:USERPROFILE 'w266-tmp'),
    [string]$Tar = '',
    [string]$Tag = '',
    # Codex Desktop ships its CLI under %LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\codex.exe (not on PATH).
    [string]$Codex = '',
    # prepended to PATH for the probe only (e.g. the pipx bin dir from -Step engine)
    [string]$ExtraPath = '',
    # -Step engine: index for this process only (the user's pip config may point at a lagging mirror)
    [string]$Index = '',
    # put the portable MinGit unpacked by upgrade-git.ps1 ($Root\mingit) on this process's PATH
    [switch]$WithGit
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($WithGit) { $env:PATH = "$(Join-Path $Root 'mingit\cmd');$env:PATH" }
if (-not $Codex) {
    $c = Get-Command codex -ErrorAction SilentlyContinue
    if ($c) { $Codex = $c.Source }
    else {
        $Codex = (Get-ChildItem "$env:LOCALAPPDATA\OpenAI\Codex\bin\*\codex.exe" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
    }
}
New-Item -ItemType Directory -Force -Path $Root | Out-Null

function Invoke-Codex([string]$CodexHome, [string[]]$CodexArgs) {
    $old = $env:CODEX_HOME
    New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null
    $env:CODEX_HOME = $CodexHome
    $ErrorActionPreference = 'Continue'
    try { & $Codex @CodexArgs 2>&1 | ForEach-Object { "$_" } ; "exit=$LASTEXITCODE" }
    finally { $env:CODEX_HOME = $old }
}

function Get-CachedPlugin([string]$CodexHome) {
    Get-ChildItem -Directory (Join-Path $CodexHome 'plugins\cache\tavotto\tavotto') | Select-Object -First 1
}

function Show-Launcher([string]$CodexHome) {
    $p = Get-CachedPlugin $CodexHome
    "cached plugin: $($p.FullName)"
    $mcp = [System.IO.File]::ReadAllText((Join-Path $p.FullName '.mcp.json'))
    "command: " + ([regex]::Match($mcp, '"command"\s*:\s*"([^"]*)"').Groups[1].Value)
    $raw = [System.IO.File]::ReadAllBytes((Join-Path $p.FullName 'mcp\launch.cmd'))
    $cr = @($raw | Where-Object { $_ -eq 13 }).Count
    "launch.cmd bytes=$($raw.Length) CR=$cr first16=" + (($raw[0..15] | ForEach-Object { '{0:x2}' -f $_ }) -join ' ')
}

function Invoke-Probe([string]$CodexHome, [string]$Name) {
    & (Join-Path $here 'probe-tools.ps1') -Codex $Codex -CodexHome $CodexHome -Out (Join-Path $Root "run-$Name")
    $err = Join-Path $Root "run-$Name\codex.stderr"
    'stderr lines about the tavotto server:'
    Select-String -Path $err -Pattern 'MCP server stderr|MCP server startup failed|tavotto-mcp|failed to initialize MCP' |
        ForEach-Object { '  ' + ($_.Line -replace '^.*?(codex_\S+: )', '$1') } | Select-Object -First 12
}

switch ($Step) {
    'inventory' {
        "os: " + (Get-CimInstance Win32_OperatingSystem | ForEach-Object { "$($_.Caption) $($_.Version) $($_.OSArchitecture)" })
        "psversion: $($PSVersionTable.PSVersion)"
        "CODEX_HOME env: '$env:CODEX_HOME'"
        foreach ($n in 'python', 'python3', 'py', 'pipx', 'tavotto', 'codex', 'git', 'node') {
            $c = @(Get-Command $n -All -ErrorAction SilentlyContinue)
            if (-not $c) { "${n}: (none)" } else { $c | ForEach-Object { "${n}: $($_.Source)" } }
        }
        foreach ($n in 'python3', 'python') {
            $ErrorActionPreference = 'Continue'
            $o = & cmd /c "$n -c ""import sys; print(sys.version)"" 2>&1"
            "run $n -> exit $LASTEXITCODE : $o"
        }
        $ErrorActionPreference = 'Continue'
        "py -0p: " + ((& cmd /c 'py -0p 2>&1') -join ' | ')
        "codex exe: $Codex"
        "codex --version: " + ((& $Codex --version 2>&1) -join ' ')
        "codex desktop packages: " + ((Get-AppxPackage -Name '*Codex*' -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Name) $($_.Version)" }) -join '; ')
        $dotcodex = Join-Path $env:USERPROFILE '.codex'
        "user .codex exists: $(Test-Path $dotcodex)"
        if (Test-Path "$dotcodex\plugins\cache") { "user plugin cache: " + ((Get-ChildItem "$dotcodex\plugins\cache" -Directory | ForEach-Object Name) -join ', ') }
        foreach ($d in "$env:APPDATA\Tavotto", "$env:LOCALAPPDATA\Tavotto", "$env:LOCALAPPDATA\Programs\Python", "$env:USERPROFILE\.local\bin", "$env:USERPROFILE\pipx") { "exists ${d}: $(Test-Path $d)" }
    }
    'stable' {
        $h = Join-Path $Root 'home-stable'
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $h
        if ($ExtraPath) { $env:PATH = "$ExtraPath;$env:PATH" }
        Invoke-Codex $h @('plugin', 'marketplace', 'add', 'Tavotto/Tavotto', '--sparse', '.agents/plugins')
        Invoke-Codex $h @('plugin', 'add', 'tavotto@tavotto')
        Show-Launcher $h
        Invoke-Probe $h 'stable'
    }
    'counterfactual' {
        $src = Join-Path $Root 'home-stable'
        $h = Join-Path $Root 'home-python3'
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $h
        Copy-Item -Recurse $src $h
        $mcp = Join-Path (Get-CachedPlugin $h).FullName '.mcp.json'
        # .NET file APIs: Windows PowerShell 5.1's Get-Content reads BOM-less UTF-8 as ANSI
        # the command is ./mcp/launch.cmd up to 0.17.0 and ./mcp/launch after #266: match both, and
        # refuse to probe an unchanged copy (it would report the working install, not the python3 shape)
        $before = [System.IO.File]::ReadAllText($mcp)
        $after = $before -replace '"\./mcp/launch(\.cmd)?"', '"python3"'
        if ($after -eq $before) { throw "counterfactual: no ./mcp/launch command to replace in $mcp" }
        [System.IO.File]::WriteAllText($mcp, $after)
        Show-Launcher $h
        Invoke-Probe $h 'python3'
    }
    'nopython' {
        # Hide every interpreter the launcher could reach: PATH keeps only dirs without
        # python*.exe / py.exe, LOCALAPPDATA / ProgramFiles / APPDATA point at empty dirs.
        $name = if ($Tag) { $Tag } else { 'stable' }
        $h = Join-Path $Root "home-$name"
        $empty = Join-Path $Root 'empty'
        New-Item -ItemType Directory -Force -Path $empty | Out-Null
        $saved = @{ PATH = $env:PATH; LOCALAPPDATA = $env:LOCALAPPDATA; ProgramFiles = $env:ProgramFiles; APPDATA = $env:APPDATA }
        # PATH entries can be malformed (quoted, dangling): plain string concat + File.Exists, no Join-Path
        $keep = $env:PATH -split ';' | Where-Object {
            $d = $_.Trim('"', "'")
            $d -and -not (@('python.exe', 'python3.exe', 'py.exe', 'tavotto.exe') | Where-Object { [System.IO.File]::Exists("$d\$_") })
        }
        $codexDir = Split-Path -Parent $Codex
        $env:PATH = (@($keep) + $codexDir | Select-Object -Unique) -join ';'
        "PATH now: $env:PATH"
        $env:LOCALAPPDATA = $empty; $env:ProgramFiles = $empty; $env:APPDATA = $empty
        try { Invoke-Probe $h "nopython-$name" }
        finally { foreach ($k in $saved.Keys) { Set-Item "env:$k" $saved[$k] } }
    }
    'upgrade' {
        $h = Join-Path $Root 'home-stable'
        Invoke-Codex $h @('plugin', 'marketplace', 'upgrade', 'tavotto')
        Show-Launcher $h
        Invoke-Probe $h 'upgrade'
    }
    'branch' {
        if (-not $Tar) { throw '-Tar <path to tar from make-local-marketplace.sh>' }
        $name = if ($Tag) { $Tag } else { 'branch' }
        $m = Join-Path $Root "mkt-$name"
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $m
        New-Item -ItemType Directory -Force -Path $m | Out-Null
        & tar -x -f $Tar -C $m
        $h = Join-Path $Root "home-$name"
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $h
        Invoke-Codex $h @('plugin', 'marketplace', 'add', (Join-Path $m 'w266-mkt'))
        Invoke-Codex $h @('plugin', 'add', 'tavotto@tavotto')
        Show-Launcher $h
        Invoke-Probe $h $name
    }
    'engine' {
        # README's third command, `pipx install "tavotto[worker]"`, without touching the machine:
        # pipx itself goes into a venv under $Root, PIPX_HOME / PIPX_BIN_DIR point under $Root.
        $ErrorActionPreference = 'Continue'
        $venv = Join-Path $Root 'pipx-tool'
        & python -m venv $venv 2>&1 | ForEach-Object { "$_" }
        & "$venv\Scripts\python.exe" -m pip install -q pipx 2>&1 | Select-Object -Last 3 | ForEach-Object { "$_" }
        $env:PIPX_HOME = Join-Path $Root 'pipx-home'
        $env:PIPX_BIN_DIR = Join-Path $Root 'pipx-bin'
        $env:PIPX_MAN_DIR = Join-Path $Root 'pipx-man'
        if ($Index) { $env:PIP_INDEX_URL = $Index }
        & "$venv\Scripts\python.exe" -m pipx install --force 'tavotto[worker]' 2>&1 | Select-Object -Last 5 | ForEach-Object { "$_" }
        "pipx exit=$LASTEXITCODE"
        Get-ChildItem $env:PIPX_BIN_DIR | ForEach-Object { "bin: $($_.Name)" }
        & (Join-Path $env:PIPX_BIN_DIR 'tavotto.exe') --version 2>&1 | ForEach-Object { "tavotto --version: $_" }
    }
    'probe' {
        $name = if ($Tag) { $Tag } else { 'stable' }
        $h = Join-Path $Root "home-$name"
        $saved = $env:PATH
        if ($ExtraPath) { $env:PATH = "$ExtraPath;$env:PATH" }
        try { Invoke-Probe $h "$name-probe" } finally { $env:PATH = $saved }
    }
    'cleanup' {
        Set-Location $env:USERPROFILE
        Remove-Item -Recurse -Force $Root
        "removed $Root"
    }
    default { throw "unknown step $Step" }
}
