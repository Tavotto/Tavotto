"""安装文件清单（TVD-MHQJ）：构建侧写的格式与壳侧读的格式同一份（`tests/golden/desktop_install_manifest.json`）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "desktop_install_manifest.json").read_text("utf-8")
)


def test_name_and_header_are_the_golden_pair():
    import build_desktop as bd

    assert bd.INSTALL_MANIFEST_NAME == GOLDEN["file_name"]
    assert bd.INSTALL_MANIFEST_HEADER == GOLDEN["header"]


def test_the_manifest_lists_every_file_once_sorted_relative_and_not_itself(tmp_path):
    import build_desktop as bd

    side = tmp_path / "Tavotto"
    (side / "_internal" / "pkg").mkdir(parents=True)
    for rel in ("Tavotto.exe", "_internal/b.dll", "_internal/pkg/_a.pyd", "_internal/a.dll"):
        (side / rel).write_bytes(b"x")
    # 旧的清单（重跑构建）不把自己记进去
    (side / GOLDEN["file_name"]).write_text("stale\n", encoding="utf-8")
    assert bd.write_install_manifest(side) == 4
    raw = (side / GOLDEN["file_name"]).read_bytes()
    assert b"\r\n" not in raw, "换行固定 LF：壳在任何平台上按同一种格式读"
    lines = raw.decode("utf-8").splitlines()
    assert lines[0] == GOLDEN["header"]
    assert lines[1:] == [
        "Tavotto.exe",
        "_internal/a.dll",
        "_internal/b.dll",
        "_internal/pkg/_a.pyd",
    ]


def test_an_empty_sidecar_dir_stops_the_build(tmp_path):
    import build_desktop as bd

    (tmp_path / "Tavotto").mkdir()
    with pytest.raises(SystemExit):
        bd.write_install_manifest(tmp_path / "Tavotto")


def test_the_manifest_is_written_after_the_last_sidecar_self_check_and_before_tauri():
    """清单记的必须是进安装包的那一份：写在所有成品自检之后、`--skip-tauri` 分叉（发行链在那里停下交给 Tauri）之前。"""
    src = (Path(__file__).resolve().parents[1] / "scripts" / "build_desktop.py").read_text("utf-8")
    body = src[src.index("def main()") :]
    write = body.index("write_install_manifest(sidecar.parent)")
    assert body.index('"run", "python", "x.py"') < write < body.index("if args.skip_tauri:")
