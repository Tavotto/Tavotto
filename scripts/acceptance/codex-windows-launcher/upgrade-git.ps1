# #266 close condition 2: after `codex plugin marketplace upgrade`, are the tools still there?
#
# Builds a throwaway git marketplace under $Root with portable MinGit (unzipped under $Root,
# only this process's PATH sees it): commit "old" = -OldTar, then installs, probes, moves main to
# commit "new" = -NewTar, runs `codex plugin marketplace upgrade tavotto`, probes again.
#   upgrade-git.ps1 -OldTar <tar> -NewTar <tar> [-Phase all|install|upgrade]
param(
    [string]$Root = (Join-Path $env:USERPROFILE 'w266-tmp'),
    [Parameter(Mandatory = $true)][string]$OldTar,
    [Parameter(Mandatory = $true)][string]$NewTar,
    [string]$ExtraPath = ''
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$mingit = Join-Path $Root 'mingit'
if (-not (Test-Path "$mingit\cmd\git.exe")) { Expand-Archive -Path (Join-Path $Root 'mingit.zip') -DestinationPath $mingit }
$env:PATH = "$mingit\cmd;$env:PATH"
if ($ExtraPath) { $env:PATH = "$ExtraPath;$env:PATH" }
$codex = (Get-ChildItem "$env:LOCALAPPDATA\OpenAI\Codex\bin\*\codex.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
# git identity for the throwaway repo only (environment, not config)
$env:GIT_AUTHOR_NAME = 'w266'; $env:GIT_AUTHOR_EMAIL = 'w266@invalid'
$env:GIT_COMMITTER_NAME = 'w266'; $env:GIT_COMMITTER_EMAIL = 'w266@invalid'
$env:GIT_CONFIG_NOSYSTEM = '1'; $env:GIT_CONFIG_GLOBAL = (Join-Path $Root 'gitconfig-empty')
Set-Content -Path $env:GIT_CONFIG_GLOBAL -Value '' -Encoding ascii

$repo = Join-Path $Root 'gitmkt'
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $repo
New-Item -ItemType Directory -Force -Path $repo | Out-Null
# Codex takes owner/repo, http(s) or ssh git URLs only (file:// is refused), so the repo is served
# read-only over git's dumb HTTP protocol on 127.0.0.1 by a python http.server in $Root.
$port = 18767
$url = "http://127.0.0.1:$port/gitmkt/.git"
$web = Start-Process -FilePath python -ArgumentList @('-m', 'http.server', $port, '--bind', '127.0.0.1', '--directory', "`"$Root`"") -PassThru -WindowStyle Hidden

function Set-Tree([string]$Tar) {
    Get-ChildItem -Force $repo | Where-Object { $_.Name -ne '.git' } | Remove-Item -Recurse -Force
    $x = Join-Path $Root 'untar'
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $x
    New-Item -ItemType Directory -Force -Path $x | Out-Null
    & tar -x -f $Tar -C $x
    Copy-Item -Recurse (Join-Path $x 'w266-mkt\codex-plugin') (Join-Path $repo 'codex-plugin')
    New-Item -ItemType Directory -Force -Path (Join-Path $repo '.agents\plugins') | Out-Null
    $m = '{"name":"tavotto","interface":{"displayName":"Tavotto (#266 upgrade test)"},"plugins":[{"name":"tavotto","source":{"source":"git-subdir","url":"' + $url + '","path":"./codex-plugin","ref":"main"},"policy":{"installation":"AVAILABLE","authentication":"ON_INSTALL"},"category":"Productivity"}]}'
    [System.IO.File]::WriteAllText((Join-Path $repo '.agents\plugins\marketplace.json'), $m)
}
function Invoke-Git { $ErrorActionPreference = 'Continue'; & git -C $repo @args 2>&1 | ForEach-Object { "  git: $_" } }
function Invoke-Cx([string[]]$A) { $ErrorActionPreference = 'Continue'; & $codex @A 2>&1 | ForEach-Object { "  codex: $_" }; "  codex exit=$LASTEXITCODE" }

Invoke-Git init -q -b main
Set-Tree $OldTar
Invoke-Git add -A
Invoke-Git update-index --chmod=+x -- codex-plugin/mcp/launch.cmd
if (Test-Path (Join-Path $repo 'codex-plugin\mcp\launch')) { Invoke-Git update-index --chmod=+x -- codex-plugin/mcp/launch }
Invoke-Git commit -q -m old
Invoke-Git update-server-info
$h = Join-Path $Root 'home-upgrade'
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $h
New-Item -ItemType Directory -Force -Path $h | Out-Null
$env:CODEX_HOME = $h
'== install old'
Invoke-Cx @('plugin', 'marketplace', 'add', $url, '--sparse', '.agents/plugins')
Invoke-Cx @('plugin', 'add', 'tavotto@tavotto')
& (Join-Path $here 'probe-tools.ps1') -Codex $codex -CodexHome $h -Out (Join-Path $Root 'run-upgrade-old')

'== move main to new + marketplace upgrade'
Set-Tree $NewTar
Invoke-Git add -A
if (Test-Path (Join-Path $repo 'codex-plugin\mcp\launch')) { Invoke-Git update-index --chmod=+x -- codex-plugin/mcp/launch }
Invoke-Git commit -q -m new
Invoke-Git update-server-info
Invoke-Git log --oneline
Invoke-Cx @('plugin', 'marketplace', 'upgrade', 'tavotto')
$cache = Join-Path $h 'plugins\cache\tavotto\tavotto'
Get-ChildItem -Recurse -File $cache | Where-Object { $_.FullName -match '\\mcp\\launch' -or $_.Name -eq '.mcp.json' } | ForEach-Object {
    $t = [System.IO.File]::ReadAllText($_.FullName)
    "  cache: $($_.FullName.Substring($cache.Length)) first line: " + $t.Split("`n")[0]
}
& (Join-Path $here 'probe-tools.ps1') -Codex $codex -CodexHome $h -Out (Join-Path $Root 'run-upgrade-new')
Stop-Process -Id $web.Id -Force -ErrorAction SilentlyContinue
