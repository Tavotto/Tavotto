# Windows Lab one-time administrator bootstrap (docs/ci/windows-lab.md, "Bootstrap").
#
# Run as administrator over SSH: `python3 scripts/winlab/winlab.py bootstrap` on the Mac
# copies this file and runs it with `powershell -File`. Idempotent: pinned versions
# already present are skipped. Daily tests run as the non-admin tavotto-lab user; this
# script is the only step that needs elevation. Keep this file ASCII-only: Windows
# PowerShell 5.1 reads BOM-less files in the ANSI code page (GB2312 on this box).
#
# Large installers come from a domestic mirror (npmmirror), never through the DMIT
# proxy (docs/rules/ci/windows-lab.md). The mirror is not trusted, so every exe / msi
# must carry a valid Authenticode signature from the expected publisher.
param(
  [switch]$WithBuildTools  # VS Build Tools + rustup (~6 GB): only for local workerd / Tauri builds
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Root = 'C:\TavottoLab'
$LabUser = 'tavotto-lab'
$Dl = Join-Path $Root 'cache\installers'

$Pins = @{
  Python  = '3.13.17'   # same minor as the CI windows legs (docs/support-matrix.json)
  Node    = '22.23.4'   # ci.yml node-version: 22
  Pnpm    = '11.27.1'   # ci.yml pnpm/action-setup version
  Git     = '2.56.0.3'
  Pwsh    = '7.6.8'
  Ts      = '1.104.2'
  SingBox = '1.14.3'
}
# sha256 of the official sing-box-1.14.3-windows-amd64.zip (also pinned in winlab.py)
$SingBoxSha256 = 'D8B86EF58936DBA9230EC6A57FFA4243933F3C14EF4809828B6C553440ED1518'

function Step($msg) { Write-Output "== $msg" }

function Get-Installer([string]$Url, [string]$Name) {
  New-Item -ItemType Directory -Force $Dl | Out-Null
  $out = Join-Path $Dl $Name
  if (-not (Test-Path $out)) {
    Invoke-WebRequest $Url -OutFile "$out.part" -UseBasicParsing -TimeoutSec 900
    Move-Item "$out.part" $out -Force
  }
  return $out
}

function Assert-Signed([string]$Path, [string]$SubjectLike) {
  $sig = Get-AuthenticodeSignature $Path
  if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notlike $SubjectLike) {
    Remove-Item $Path -Force
    throw "signature check failed for $(Split-Path $Path -Leaf): $($sig.Status) $($sig.SignerCertificate.Subject)"
  }
}

function Install-Msi([string]$Path, [string[]]$Props) {
  $p = Start-Process msiexec.exe -ArgumentList (@('/i', "`"$Path`"", '/qn', '/norestart') + $Props) -Wait -PassThru
  if ($p.ExitCode -notin 0, 3010) { throw "msiexec $(Split-Path $Path -Leaf) exit $($p.ExitCode)" }
}

# ---------------------------------------------------------------- directories and ACLs
Step 'directories'
$dirs = 'repo', 'runs', 'cache', 'reports', 'profiles', 'tools', 'logs', 'secrets', 'state'
foreach ($d in $dirs) { New-Item -ItemType Directory -Force (Join-Path $Root $d) | Out-Null }
icacls $Root /inheritance:r /grant 'Administrators:(OI)(CI)F' /grant 'SYSTEM:(OI)(CI)F' /grant "${LabUser}:(RX)" | Out-Null
foreach ($d in 'repo', 'runs', 'cache', 'reports', 'profiles', 'state') {
  icacls (Join-Path $Root $d) /grant "${LabUser}:(OI)(CI)M" | Out-Null
}
icacls (Join-Path $Root 'tools') /grant "${LabUser}:(OI)(CI)RX" | Out-Null
icacls (Join-Path $Root 'logs') /grant "${LabUser}:(OI)(CI)RX" | Out-Null
# secrets: proxy credentials. Not readable by the test account (no inherited RX).
icacls (Join-Path $Root 'secrets') /inheritance:r /grant 'Administrators:(OI)(CI)F' /grant 'SYSTEM:(OI)(CI)F' | Out-Null

# ---------------------------------------------------------------- toolchain
$py = "C:\Program Files\Python313\python.exe"
if (-not ((Test-Path $py) -and ((& $py -c 'import platform;print(platform.python_version())') -eq $Pins.Python))) {
  Step "python $($Pins.Python)"
  $f = Get-Installer "https://registry.npmmirror.com/-/binary/python/$($Pins.Python)/python-$($Pins.Python)-amd64.exe" "python-$($Pins.Python)-amd64.exe"
  Assert-Signed $f '*Python Software Foundation*'
  $p = Start-Process $f -ArgumentList '/quiet', 'InstallAllUsers=1', 'PrependPath=0', 'Include_launcher=1', 'Include_test=0', 'Shortcuts=0' -Wait -PassThru
  if ($p.ExitCode -ne 0) { throw "python installer exit $($p.ExitCode)" }
}

$node = 'C:\Program Files\nodejs\node.exe'
if (-not ((Test-Path $node) -and ((& $node --version) -eq "v$($Pins.Node)"))) {
  Step "node $($Pins.Node)"
  $f = Get-Installer "https://registry.npmmirror.com/-/binary/node/v$($Pins.Node)/node-v$($Pins.Node)-x64.msi" "node-v$($Pins.Node)-x64.msi"
  Assert-Signed $f '*OpenJS Foundation*'
  Install-Msi $f @()
}
$pnpmVer = ''
try { $pnpmVer = & 'C:\Program Files\nodejs\pnpm.cmd' --version 2>$null } catch {}
if ($pnpmVer -ne $Pins.Pnpm) {
  Step "pnpm $($Pins.Pnpm)"
  # explicit prefix: the default global prefix is the admin's own %APPDATA%\npm, invisible to tavotto-lab
  & 'C:\Program Files\nodejs\npm.cmd' install -g --prefix 'C:\Program Files\nodejs' "pnpm@$($Pins.Pnpm)" --registry=https://registry.npmmirror.com --no-fund --no-audit
  if ($LASTEXITCODE -ne 0) { throw "npm install pnpm exit $LASTEXITCODE" }
}

$git = 'C:\Program Files\Git\cmd\git.exe'
if (-not ((Test-Path $git) -and ((& $git --version) -like "*$(($Pins.Git -split '\.')[0..2] -join '.').windows.$(($Pins.Git -split '\.')[3])*"))) {
  Step "git $($Pins.Git)"
  $v = ($Pins.Git -split '\.')
  $tag = "v$($v[0]).$($v[1]).$($v[2]).windows.$($v[3])"
  $f = Get-Installer "https://registry.npmmirror.com/-/binary/git-for-windows/$tag/Git-$($Pins.Git)-64-bit.exe" "Git-$($Pins.Git)-64-bit.exe"
  Assert-Signed $f '*Johannes Schindelin*'
  $p = Start-Process $f -ArgumentList '/VERYSILENT', '/NORESTART', '/NOCANCEL', '/SP-', '/SUPPRESSMSGBOXES', '/COMPONENTS=gitlfs', '/o:PathOption=Cmd' -Wait -PassThru
  if ($p.ExitCode -ne 0) { throw "git installer exit $($p.ExitCode)" }
}

$pwsh = 'C:\Program Files\PowerShell\7\pwsh.exe'
if (-not ((Test-Path $pwsh) -and ((& $pwsh -NoProfile -c '$PSVersionTable.PSVersion.ToString()') -eq $Pins.Pwsh))) {
  Step "pwsh $($Pins.Pwsh)"
  # GitHub release assets are unreachable from this region; TUNA mirrors them per tag.
  $f = Get-Installer "https://mirrors.tuna.tsinghua.edu.cn/github-release/PowerShell/PowerShell/v$($Pins.Pwsh)%20Release%20of%20PowerShell/PowerShell-$($Pins.Pwsh)-win-x64.msi" "PowerShell-$($Pins.Pwsh)-win-x64.msi"
  Assert-Signed $f '*Microsoft Corporation*'
  Install-Msi $f @('ADD_PATH=1', 'ENABLE_PSREMOTING=0', 'REGISTER_MANIFEST=1', 'USE_MU=0', 'ENABLE_MU=0')
}

# Tailscale in unattended mode: stays up after reboot without a desktop logon
# (the first login link still has to be opened by a human once).
$ts = 'C:\Program Files\Tailscale\tailscale.exe'
if (-not ((Test-Path $ts) -and ((& $ts version | Select-Object -First 1) -eq $Pins.Ts))) {
  Step "tailscale $($Pins.Ts)"
  $f = Get-Installer "https://pkgs.tailscale.com/stable/tailscale-setup-$($Pins.Ts)-amd64.msi" "tailscale-setup-$($Pins.Ts)-amd64.msi"
  Assert-Signed $f '*Tailscale Inc*'
  Install-Msi $f @('TS_UNATTENDEDMODE=always', 'TS_NOLAUNCH=1')
}

# sing-box: official release zip, not Authenticode-signed and not mirrored. winlab.py
# downloads it on the Mac from the GitHub release and stages it into cache\installers;
# here it must match the pinned sha256 or nothing is installed.
$sb = Join-Path $Root "tools\sing-box\sing-box.exe"
if (-not ((Test-Path $sb) -and ((& $sb version | Select-Object -First 1) -like "*$($Pins.SingBox)*"))) {
  Step "sing-box $($Pins.SingBox)"
  $f = Join-Path $Dl "sing-box-$($Pins.SingBox)-windows-amd64.zip"
  if (-not (Test-Path $f)) { throw "sing-box zip not staged (run winlab.py admin bootstrap from the Mac)" }
  if ((Get-FileHash $f -Algorithm SHA256).Hash -ne $SingBoxSha256) { Remove-Item $f -Force; throw 'sing-box zip sha256 mismatch' }
  $tmp = Join-Path $Dl 'sing-box-unzip'
  if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
  Expand-Archive $f $tmp
  New-Item -ItemType Directory -Force (Split-Path $sb) | Out-Null
  Copy-Item (Join-Path $tmp "sing-box-$($Pins.SingBox)-windows-amd64\sing-box.exe") $sb -Force
  Remove-Item $tmp -Recurse -Force
}

if ($WithBuildTools) {
  Step 'vs build tools (C++ workload)'
  $f = Get-Installer 'https://aka.ms/vs/17/release/vs_BuildTools.exe' 'vs_BuildTools.exe'
  Assert-Signed $f '*Microsoft Corporation*'
  $p = Start-Process $f -ArgumentList '--quiet', '--wait', '--norestart', '--nocache', '--add', 'Microsoft.VisualStudio.Workload.VCTools', '--includeRecommended' -Wait -PassThru
  if ($p.ExitCode -notin 0, 3010) { throw "vs_BuildTools exit $($p.ExitCode)" }
  Step 'rustup (stable, per-user for tavotto-lab is done by winlab prepare)'
  Get-Installer 'https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe' 'rustup-init.exe' | ForEach-Object { Assert-Signed $_ '*Rust*' }
  icacls (Join-Path $Dl 'rustup-init.exe') /grant "${LabUser}:RX" | Out-Null
}

# ---------------------------------------------------------------- resident proxy task
# sing-box runs as SYSTEM (the only principal besides Administrators that can read
# secrets\), starts at boot and restarts on failure. Loopback-only listeners; the
# config is rendered on the Mac by `winlab.py proxy-install` into secrets\sing-box.json.
$cfg = Join-Path $Root 'secrets\sing-box.json'
$taskName = 'TavottoLab-SingBox'
$action = New-ScheduledTaskAction -Execute $sb -Argument "run -c `"$cfg`" -D `"$Root\state\sing-box`"" -WorkingDirectory (Split-Path $sb)
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
New-Item -ItemType Directory -Force "$Root\state\sing-box" | Out-Null
icacls "$Root\state\sing-box" /inheritance:r /grant 'Administrators:(OI)(CI)F' /grant 'SYSTEM:(OI)(CI)F' | Out-Null
if (Test-Path $cfg) {
  & $sb check -c $cfg
  if ($LASTEXITCODE -ne 0) { throw "sing-box check failed" }
  Stop-ScheduledTask -TaskName $taskName -EA SilentlyContinue
  Start-ScheduledTask -TaskName $taskName
  Step 'sing-box task (re)started'
} else {
  Step 'sing-box config not installed yet (run winlab.py proxy-install)'
}

Step 'versions'
& $py --version; & $node --version; & 'C:\Program Files\nodejs\pnpm.cmd' --version; & $git --version
& $pwsh -NoProfile -c '$PSVersionTable.PSVersion.ToString()'; & $ts version | Select-Object -First 1; & $sb version | Select-Object -First 1
Step 'done'
exit 0
