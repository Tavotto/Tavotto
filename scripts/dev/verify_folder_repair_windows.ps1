# 手动验证（只在 Windows 真机上跑，CI 不跑）：「修复权限」（ADR 0153 修订 2026-10-30）的提权遍历与 icacls 序列。
#
# 用法（普通 PowerShell，不要以管理员运行——脚本里会弹一次 UAC，和用户点「修复权限」一模一样）：
#   .\scripts\dev\verify_folder_repair_windows.ps1 -Python .\.venv\Scripts\python.exe
#
# 三个场景（每个在 %TEMP%\tavotto-repair-verify 下建一棵 tzdata\__init__.py 的依赖夹）：
#   1. 受限：DACL 只有 SYSTEM、属主 SYSTEM，不含 Administrators（拿 SYSTEM 代替 CodexSandboxOffline）
#      预期：UAC 后退出码 0；之后当前账户读得了 tzdata\__init__.py；属主是 Administrators
#   2. 受限 + 里面有指向目录外的联接点
#      预期：退出码 201（repair_has_links），目录外的目标 ACL / 属主一点没变，依赖夹仍读不了
#   3. 普通 mkdtemp 形状（含 Administrators）
#      预期：退出码 0
# 建受限目录需要管理员权限，所以脚本开头会自己再提权一次建场景（第 0 次 UAC，只为造数据）。
param([Parameter(Mandatory)][string]$Python)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:TEMP 'tavotto-repair-verify'
if (Test-Path $root) { Write-Host "先手动删除 $root（里面有受限目录，需要管理员）后重试"; exit 2 }
New-Item -ItemType Directory $root | Out-Null

$setup = @"
`$root = '$root'
function Mk(`$name, [bool]`$restricted, [string]`$junctionTo = '') {
  `$d = Join-Path `$root `$name
  New-Item -ItemType Directory (Join-Path `$d 'tzdata') -Force | Out-Null
  Set-Content (Join-Path `$d 'tzdata\__init__.py') 'X = 1'
  # 联接点必须在收紧 DACL 之前建：收紧后连管理员也写不进去（10-30 真机实测，旧顺序建不出联接点、场景 2 什么都没测到）
  if (`$junctionTo) { New-Item -ItemType Junction -Path (Join-Path `$d 'tzdata\escape') -Target `$junctionTo -ErrorAction Stop | Out-Null }
  if (`$restricted) {
    & icacls `$d /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' /T /C /Q | Out-Null
    & icacls `$d /setowner '*S-1-5-18' /T /C /Q | Out-Null
  }
  return `$d
}
New-Item -ItemType Directory (Join-Path `$root 'precious') | Out-Null
Mk 'restricted' `$true | Out-Null
Mk 'restricted_link' `$true (Join-Path `$root 'precious') | Out-Null
Mk 'plain' `$false | Out-Null
"@
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($setup))
Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile', '-EncodedCommand', $enc

$check = @'
import sys, os
from tavotto.engine import folderrepair as f
root = sys.argv[1]
def readable(p):
    try:
        open(p, "rb").close(); return True
    except OSError:
        return False
import subprocess
def acl(p):
    return subprocess.run(["icacls", p], capture_output=True, text=True).stdout.strip()
precious = os.path.join(root, "precious")
precious_before = acl(precious)
escape = os.path.join(root, "restricted_link", "tzdata", "escape")
print(f"junction present before repair: {os.path.lexists(escape)}")
cases = [("restricted", 0), ("restricted_link", f.EXIT_LINKS), ("plain", 0)]
bad = 0
for name, want in cases:
    d = os.path.join(root, name)
    out = f.shell_execute_runner(f.build_commands(d))
    ok = out.status == "done" and out.exit_code == want
    print(f"{name:16s} status={out.status} exit={out.exit_code} (want {want}) "
          f"readable_after={readable(os.path.join(d, 'tzdata', '__init__.py'))} -> {'OK' if ok else 'FAIL'}")
    bad += 0 if ok else 1
same = acl(precious) == precious_before
print(f"precious ACL unchanged: {same} -> {'OK' if same else 'FAIL'}")
bad += 0 if same else 1
sys.exit(1 if bad else 0)
'@
$env:PYTHONPATH = (Join-Path (Split-Path $PSScriptRoot -Parent | Split-Path -Parent) 'src')
$pyfile = Join-Path $root '_check.py'
Set-Content -Path $pyfile -Value $check -Encoding UTF8
& $Python $pyfile $root
$code = $LASTEXITCODE
Write-Host "`n场景 2 之后请确认：precious 目录的属主/ACL 没变：  icacls $root\precious ; (Get-Acl $root\precious).Owner"
Write-Host "场景 1/3 之后确认属主：(Get-Acl $root\restricted).Owner  应为 BUILTIN\Administrators"
exit $code
