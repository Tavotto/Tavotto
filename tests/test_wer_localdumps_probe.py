"""WER LocalDumps 对照探针：HKCU 下的 LocalDumps 子键到底生不生效（PR D 风险点 1）。

`installer.nsi` 只写 `HKCU\\...\\Windows Error Reporting\\LocalDumps\\<exe>`（currentUser 安装，不要管理员）。
微软文档只写了 HKLM；HKCU 是否被 WER 读取没有权威说法，所以这里**实测**：让一个会崩的 python.exe
各崩一次，A 轮只开 HKCU、B 轮只开 HKLM，看哪一轮在指定目录里落下了 `python.exe.*.dmp`。

只在 Windows 且 `CI=true` 时运行（要写 HKLM，需要管理员；本机开发机不该被改注册表）。
· HKCU 轮有转储 → 通过；
· HKCU 没有、HKLM 有 → 失败，「HKCU LocalDumps 不生效」——安装器方案要改（见 PR D 的方案 B）；
· 两轮都没有 → skip（runner 上 WER 不产转储，探针无效，**不是通过**）。
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("CI", "").lower() != "true",
    reason="只在 Windows CI 上运行：要写注册表（含 HKLM），本机开发机不动",
)

LOCALDUMPS = r"Software\Microsoft\Windows\Windows Error Reporting\LocalDumps"
# 被探测的 exe 名（WER 只认文件名）。installer.nsi 写的是 Tavotto 自己的三个名字；
# 探针借 python.exe 是因为它是 CI 上现成、能受控崩溃的进程。
PROBE_EXE = "python.exe"
CRASH_CODE = "import ctypes; ctypes.string_at(0)"
WAIT_S = 30


def _write_key(hive, subkey_name: str, folder: Path) -> None:
    import winreg

    with winreg.CreateKeyEx(hive, rf"{LOCALDUMPS}\{subkey_name}", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "DumpFolder", 0, winreg.REG_EXPAND_SZ, str(folder))
        winreg.SetValueEx(k, "DumpType", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "DumpCount", 0, winreg.REG_DWORD, 5)


def _key_exists(hive, subkey_name: str) -> bool:
    import winreg

    try:
        winreg.CloseKey(winreg.OpenKey(hive, rf"{LOCALDUMPS}\{subkey_name}"))
        return True
    except FileNotFoundError:
        return False


def _delete_key(hive, subkey_name: str) -> None:
    import winreg

    try:
        winreg.DeleteKey(hive, rf"{LOCALDUMPS}\{subkey_name}")
    except FileNotFoundError:
        pass


def _crash_and_wait(folder: Path) -> list[Path]:
    subprocess.run(
        [sys.executable, "-c", CRASH_CODE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        dumps = sorted(folder.glob(f"{PROBE_EXE}.*.dmp"))
        if dumps:
            return dumps
        time.sleep(1)
    return []


def _round(hive, subkey_name: str, folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    _write_key(hive, subkey_name, folder)
    try:
        return _crash_and_wait(folder)
    finally:
        _delete_key(hive, subkey_name)


def test_hkcu_localdumps_produces_a_minidump(tmp_path_factory):
    import winreg

    # runner 上本来就有这个键就不动它（探针不覆盖别人的配置）
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        if _key_exists(hive, PROBE_EXE):
            pytest.skip(f"{PROBE_EXE} 的 LocalDumps 子键已存在，探针不覆盖它")

    # 变异点：HKCU 轮用的子键名。改成别的名字，WER 不会为 python.exe 开转储，这条必须红。
    hkcu_name = PROBE_EXE
    dir_a = tmp_path_factory.mktemp("wer_hkcu")
    dir_b = tmp_path_factory.mktemp("wer_hklm")

    hkcu_dumps = _round(winreg.HKEY_CURRENT_USER, hkcu_name, dir_a)
    if hkcu_dumps:
        return
    # HKCU 轮没有：再用 HKLM 轮判定是「HKCU 不生效」还是「这台 runner 根本不产转储」
    hklm_dumps = _round(winreg.HKEY_LOCAL_MACHINE, PROBE_EXE, dir_b)
    if hklm_dumps:
        pytest.fail(
            "HKCU LocalDumps 不生效：同一次崩溃，HKLM 轮在指定目录落了转储、HKCU 轮没有"
            f"（{hklm_dumps[0].name}）。installer.nsi 的 HKCU 方案要换（PR D 方案 B：壳内自写 minidump）"
        )
    pytest.skip("runner 上 WER 不产转储（HKCU / HKLM 两轮都没有 .dmp），探针无效，不当作通过")
