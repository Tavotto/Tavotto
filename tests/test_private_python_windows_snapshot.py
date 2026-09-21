"""`scripts/ci/private_python_windows_snapshot.py` 的比对判据（纯函数，任何平台都能量）。

目标腿（`private-python-targets.yml` 的 Windows 腿）靠它说「注册表 / PATH / USERPROFILE 前后一致」：
判据本身要钉住——顶层条目**两个方向**都比，放行清单只对新增有效（Codex #467 P2）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import private_python_windows_snapshot as snap  # noqa: E402


def _snapshot(top: list[str], **overrides) -> dict:
    out = {
        "registry": {"HKCU\\Environment": {"rc": 0, "text": "Path REG_SZ C:\\x"}},
        "registry_path_values": {"HKCU\\Environment::Path": {"rc": 0, "text": "C:\\x"}},
        "env": {"PATH": "C:\\x", "USERPROFILE": "C:\\Users\\r"},
        "userprofile_top": sorted(top),
    }
    out.update(overrides)
    return out


BASE = ["AppData", "Desktop", "Documents", ".gitconfig"]


def test_identical_snapshots_are_clean():
    assert snap.diff(_snapshot(BASE), _snapshot(BASE)) == []


def test_allowed_additions_pass_and_others_are_reported():
    allowed = sorted(snap.ALLOWED_NEW_TOP_LEVEL)
    assert allowed, "放行清单不能为空——它是判据的一部分"
    assert snap.diff(_snapshot(BASE), _snapshot(BASE + allowed)) == []
    problems = snap.diff(_snapshot(BASE), _snapshot(BASE + [".python-version"]))
    assert problems == ["USERPROFILE 顶层多出了 ['.python-version']"]


def test_removed_top_level_entries_are_reported():
    """删了用户目录里原有的东西也是改动：`after - before` 为空不等于没动。"""
    problems = snap.diff(_snapshot(BASE), _snapshot(["AppData", "Desktop", "Documents"]))
    assert problems == ["USERPROFILE 顶层少了 ['.gitconfig']"]


def test_a_rename_to_an_allowed_name_is_still_reported():
    """改名成放行清单里的名字：新增被放行，但少掉的那个照样报——放行只对新增有效。"""
    allowed = sorted(snap.ALLOWED_NEW_TOP_LEVEL)[0]
    problems = snap.diff(_snapshot(BASE), _snapshot(["AppData", "Desktop", "Documents", allowed]))
    assert problems == ["USERPROFILE 顶层少了 ['.gitconfig']"]


@pytest.mark.parametrize(
    "section,key",
    [("registry", "HKCU\\Environment"), ("registry_path_values", "HKCU\\Environment::Path")],
)
def test_registry_changes_are_reported(section, key):
    after = _snapshot(BASE)
    after[section][key] = {"rc": 0, "text": "changed"}
    assert snap.diff(_snapshot(BASE), after) == [f"{section}: {key} 前后不同"]


def test_path_change_is_reported():
    after = _snapshot(BASE, env={"PATH": "C:\\x;C:\\y", "USERPROFILE": "C:\\Users\\r"})
    assert snap.diff(_snapshot(BASE), after) == ["env: PATH 前后不同"]
