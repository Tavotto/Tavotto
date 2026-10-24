# Windows Lab installer acceptance (docs/rules/ci/windows-lab.md, task "installer").
#
# Mirrors the checks of .github/workflows/nightly.yml job "windows-install" (that
# workflow is the authority; keep this in step with it): install location from the
# registry, sidecar + bundled runtime + workerd + LICENSE present, shell -> sidecar ->
# follow-exit, smoke_app.py with the bundled runtime, reinstall smoke. Adds what the
# nightly cannot do on a throwaway runner: upgrade from an older package keeping user
# data, and a clean uninstall. Runs as the non-admin tavotto-lab user (installMode is
# currentUser). ASCII only. Emits one JSON object per step on stdout; the agent judges.
param(
  [Parameter(Mandatory = $true)][string]$Setup,
  [string]$From = '',
  [Parameter(Mandatory = $true)][string]$Repo,
  [Parameter(Mandatory = $true)][string]$Python,
  [Parameter(Mandatory = $true)][string]$Evidence,
  [switch]$KeepInstalled
)
$ErrorActionPreference = 'Stop'
$Uninst = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'

# Write straight to the console stream: functions below also return values, and anything
# put on the pipeline inside them would be swallowed into those return values.
function Emit($step, $ok, $detail) {
  [Console]::Out.WriteLine(([pscustomobject]@{ step = $step; ok = [bool]$ok; detail = "$detail" } | ConvertTo-Json -Compress))
}

# Child processes without WMI: Win32_Process (CIM) is denied to a non-admin SSH session;
# PowerShell 7's Get-Process exposes Parent and CommandLine directly.
function Get-Children([int]$parentId, [string]$exePath) {
  # By parent + executable path only: Process.CommandLine also goes through WMI in PS 7.
  @(Get-Process -EA SilentlyContinue | Where-Object {
      $_.Parent -and $_.Parent.Id -eq $parentId -and $_.Path -and ($_.Path -ieq $exePath) })
}

function Get-Installs {
  if (-not (Test-Path $Uninst)) { return @() }
  @(Get-ChildItem $Uninst | ForEach-Object { Get-ItemProperty $_.PSPath } |
    Where-Object { $_.DisplayName -like 'Tavotto*' })
}

function Find-Install([string]$exeName) {
  $hits = Get-Installs
  foreach ($h in $hits) {
    $loc = ($h.InstallLocation -replace '^"|"$', '')
    if ($loc -and (Test-Path (Join-Path $loc $exeName))) { return @{ loc = $loc; reg = $h } }
  }
  return $null
}

function Install-Package([string]$path, [string]$label) {
  $p = Start-Process -Wait -PassThru $path -ArgumentList '/S'
  Emit "install:$label" ($p.ExitCode -eq 0) "exit=$($p.ExitCode) $(Split-Path $path -Leaf)"
  $found = $null
  foreach ($h in Get-Installs) {
    $loc = ($h.InstallLocation -replace '^"|"$', '')
    if ($loc -and (Get-ChildItem $loc -Filter *.exe -EA SilentlyContinue | Where-Object { $_.Name -like 'Tavotto*.exe' })) { $found = @{ loc = $loc; reg = $h } }
  }
  if (-not $found) { Emit "registry:$label" $false 'no Tavotto* entry with an InstallLocation in HKCU Uninstall'; throw 'install not found' }
  Emit "registry:$label" $true "DisplayName=$($found.reg.DisplayName) DisplayVersion=$($found.reg.DisplayVersion)"
  return $found
}

function Check-Layout($inst, [string]$label) {
  $shell = Get-ChildItem $inst.loc -Filter *.exe | Where-Object { $_.Name -like 'Tavotto*.exe' } | Select-Object -First 1
  $side = Get-ChildItem $inst.loc -Recurse -Filter $shell.Name -EA SilentlyContinue |
          Where-Object { $_.FullName -ne $shell.FullName } | Select-Object -First 1
  if (-not $side) { $side = Get-ChildItem $inst.loc -Recurse -Filter 'Tavotto.exe' -EA SilentlyContinue | Where-Object { $_.FullName -ne $shell.FullName } | Select-Object -First 1 }
  Emit "sidecar:$label" ([bool]$side) "$(if ($side) { $side.FullName.Replace($inst.loc, '<install>') } else { 'missing' })"
  if (-not $side) { throw 'no sidecar' }
  $internal = Join-Path $side.Directory.FullName '_internal'
  foreach ($rel in 'runtime\python.exe', 'runtime\runtime-manifest.json', 'tavotto-workerd.exe') {
    Emit "bundled:$rel" (Test-Path (Join-Path $internal $rel)) $label
  }
  $lic = Join-Path $inst.loc 'LICENSE'
  Emit "license:$label" ((Test-Path $lic) -and (Select-String -Path $lic -Pattern 'GNU AFFERO' -Quiet)) 'AGPL full text in install root'
  return @{ shell = $shell.FullName; side = $side.FullName }
}

function Probe-Shell($paths, [string]$label) {
  $out = Join-Path $Evidence "shell-$label.stdout.log"; $err = Join-Path $Evidence "shell-$label.stderr.log"
  $p = Start-Process -FilePath $paths.shell -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
  $child = $null
  foreach ($i in 1..60) {
    Start-Sleep -Milliseconds 500
    $child = Get-Children $p.Id $paths.side | Select-Object -First 1
    if ($child) { break }
  }
  $alive = $null -ne (Get-Process -Id $p.Id -EA SilentlyContinue)
  Emit "shell-starts-sidecar:$label" ([bool]$child) "shell_pid=$($p.Id) alive=$alive sidecar_pid=$(if ($child) { $child.Id } else { 'none' })"
  $killed = $true
  try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch { $killed = $false; Emit "kill-shell:$label" $false $_.Exception.Message }
  if ($killed) {
    $shellGone = $false
    foreach ($i in 1..20) { Start-Sleep -Milliseconds 250; if (-not (Get-Process -Id $p.Id -EA SilentlyContinue)) { $shellGone = $true; break } }
    Emit "kill-shell:$label" $shellGone "TerminateProcess on pid $($p.Id)"
  }
  if ($child) {
    $gone = $false
    foreach ($i in 1..30) { Start-Sleep -Milliseconds 500; if (-not (Get-Process -Id $child.Id -EA SilentlyContinue)) { $gone = $true; break } }
    Emit "sidecar-follows-shell-exit:$label" $gone "no orphan within 15 s after the shell is killed"
    if (-not $gone) { Stop-Process -Id $child.Id -Force -EA SilentlyContinue }
  }
}

function Smoke($paths, [string]$label, [string[]]$extra) {
  $log = Join-Path $Evidence "smoke-$label.log"
  Push-Location $Repo
  try {
    $env:TAVOTTO_WORKER_PYTHON = $null
    & $Python scripts\smoke_app.py --exe $paths.side --figures examples\runtime_check --expect-source bundled --expect-control-plane workerd @extra *> $log
    $code = $LASTEXITCODE
  } finally { Pop-Location }
  Emit "smoke:$label" ($code -eq 0) "exit=$code"
}

function Uninstall-All([string]$label) {
  foreach ($h in Get-Installs) {
    $loc = ($h.InstallLocation -replace '^"|"$', '')
    $u = Join-Path $loc 'uninstall.exe'
    if (Test-Path $u) {
      $p = Start-Process -Wait -PassThru $u -ArgumentList '/S'
      # the NSIS uninstaller re-spawns itself from %TEMP%; wait for the directory to go
      foreach ($i in 1..60) { if (-not (Test-Path (Join-Path $loc 'uninstall.exe'))) { break }; Start-Sleep -Milliseconds 500 }
      Emit "uninstall:$label" ((-not (Test-Path (Join-Path $loc 'uninstall.exe'))) -and $p.ExitCode -eq 0) "exit=$($p.ExitCode) $($h.DisplayName)"
    }
  }
  Emit "registry-clean:$label" ((Get-Installs).Count -eq 0) 'no Tavotto* entries left in HKCU Uninstall'
}

New-Item -ItemType Directory -Force $Evidence | Out-Null
try {
  Uninstall-All 'before'
  $pkgs = 'numpy,pandas,scipy,seaborn,PIL,matplotlib'
  if ($From) {
    $old = Install-Package $From 'from'
    $oldPaths = Check-Layout $old 'from'
    Smoke $oldPaths 'from' @('--expect-packages', $pkgs)
    $dataDirs = @(Get-ChildItem $env:LOCALAPPDATA, $env:APPDATA -Directory -EA SilentlyContinue | Where-Object { $_.Name -like 'com.tavotto*' -or $_.Name -like 'Tavotto*' })
    $marker = $null
    if ($dataDirs) { $marker = Join-Path $dataDirs[0].FullName 'winlab-upgrade-marker.txt'; Set-Content $marker 'kept-across-upgrade' -Encoding ascii }
    Emit 'user-data-before-upgrade' ([bool]$dataDirs) "$($dataDirs.Count) app data dir(s)"
  }
  $new = Install-Package $Setup 'new'
  $paths = Check-Layout $new 'new'
  if ($From) {
    Emit 'version-changed' ($new.reg.DisplayVersion -ne $old.reg.DisplayVersion) "$($old.reg.DisplayVersion) -> $($new.reg.DisplayVersion)"
    if ($marker) { Emit 'user-data-kept' ((Test-Path $marker) -and ((Get-Content $marker) -eq 'kept-across-upgrade')) 'marker in app data survived the upgrade' }
  }
  Probe-Shell $paths 'new'
  Smoke $paths 'new' @('--expect-packages', $pkgs)
  $p = Start-Process -Wait -PassThru $Setup -ArgumentList '/S'
  Emit 'reinstall' ($p.ExitCode -eq 0) "exit=$($p.ExitCode)"
  $paths = Check-Layout (Find-Install (Split-Path $paths.shell -Leaf)) 'reinstall'
  Smoke $paths 'reinstall' @()
  if (-not $KeepInstalled) { Uninstall-All 'after' }
} catch {
  Emit 'aborted' $false $_.Exception.Message
}
exit 0
