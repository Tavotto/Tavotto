# #266 acceptance probe: which Tavotto MCP tools does a real Codex hand to the model?
#
# Runs one `codex exec` turn against fake-responses.ps1 under an isolated CODEX_HOME (the
# user's own ~/.codex, login and plugins are never read or written). Evidence it leaves in
# $Out:
#   requests/*.json  every /responses body Codex sent (the tool list the model would see)
#   codex.stderr     codex exec stderr with RUST_LOG (rmcp handshake / spawn errors)
#   summary.txt      tool names containing "tavotto", plus the verdict line
# Verdict: TOOLS_PRESENT when the request carries tavotto_open_figure (the full server),
# HEALTH_ONLY when only tavotto_health is there (the degraded server spoke),
# NO_TAVOTTO_TOOLS when none (the #266 shape), NO_REQUEST when the ruler itself saw nothing.
param(
    [Parameter(Mandatory = $true)][string]$CodexHome,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Codex = 'codex',
    [int]$Port = 18766
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$req = Join-Path $Out 'requests'
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $req
$work = Join-Path $Out 'workspace'
New-Item -ItemType Directory -Force -Path $work | Out-Null

$shell = if ($IsWindows -or $env:OS -eq 'Windows_NT') { 'powershell.exe' } else { 'pwsh' }
$server = Start-Process -FilePath $shell -PassThru -WindowStyle Hidden -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $here 'fake-responses.ps1'),
    '-Port', $Port, '-OutDir', $req, '-Seconds', 200
) -ErrorAction Stop
$t0 = Get-Date
while (-not (Test-Path (Join-Path $req 'ready'))) {
    if (((Get-Date) - $t0).TotalSeconds -gt 30) { throw 'fake responses server did not start' }
    Start-Sleep -Milliseconds 200
}

# TOML literal strings (single quotes) and no spaces: Windows PowerShell 5.1 strips embedded
# double quotes when it builds a native command line.
$provider = "model_providers.w266={name='w266',base_url='http://127.0.0.1:$Port/v1',wire_api='responses',env_key='W266_FAKE_KEY',request_max_retries=0,stream_max_retries=0}"
$env:CODEX_HOME = $CodexHome
$env:W266_FAKE_KEY = 'not-a-real-key'
$env:RUST_LOG = 'info,rmcp=debug,codex_rmcp_client=debug,codex_core::mcp_connection_manager=debug'
$stderr = Join-Path $Out 'codex.stderr'
$stdout = Join-Path $Out 'codex.stdout'
# One pre-quoted argument string + Start-Process: the redirected stderr stays raw bytes (a
# PowerShell `2>` would re-wrap every line at console width and re-encode it).
$argline = "exec --skip-git-repo-check -C `"$work`" -c `"model_provider='w266'`" -c `"model='gpt-5'`" -c `"$provider`" `"reply with ok`""
# stdin from an empty file: over SSH an inherited open stdin makes `codex exec` wait for more input
$stdin = Join-Path $Out 'empty.stdin'
[System.IO.File]::WriteAllText($stdin, '')
$p = Start-Process -FilePath $Codex -ArgumentList $argline -PassThru -NoNewWindow -Wait `
    -RedirectStandardError $stderr -RedirectStandardOutput $stdout -RedirectStandardInput $stdin
$exit = $p.ExitCode
Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue

$bodies = @(Get-ChildItem -Path $req -Filter '*.json' -ErrorAction SilentlyContinue)
$names = @()
foreach ($b in $bodies) {
    $names += [regex]::Matches((Get-Content -Raw $b.FullName), '"name"\s*:\s*"([^"]*tavotto[^"]*)"') |
        ForEach-Object { $_.Groups[1].Value }
}
$names = @($names | Sort-Object -Unique)
$verdict = if ($bodies.Count -eq 0) { 'NO_REQUEST' }
elseif ($names -match 'open_figure') { 'TOOLS_PRESENT' }
elseif ($names -match 'health') { 'HEALTH_ONLY' }
else { 'NO_TAVOTTO_TOOLS' }
$lines = @("codex exit: $exit", "requests: $($bodies.Count)", "tavotto tools: $($names -join ', ')", "VERDICT: $verdict")
$lines | Set-Content -Path (Join-Path $Out 'summary.txt') -Encoding utf8
$lines
