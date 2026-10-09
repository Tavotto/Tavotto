"""Tavotto wrapper 下与标准库同名的本地文件（Codex 第一轮 P1-a + 定向复核 r4222101043）。

主语：**执行脚本的那个进程是 Tavotto 的 wrapper**（safe：`engine/worker.py`；native：
`engine/bridge_runner.py`），不是裸的 `python script.py`。wrapper 在把用户目录插进 `sys.path` 之前已 import
一批标准库，这些模块又传递带进别的标准库（`re` 带进 `enum` ……），精确闭包随解释器版本变、静态量不出。
维护者裁决：不追闭包，用保守规则——wrapper profile（safe / native）下**任何**与标准库同名的本地文件 / 包
一律 `resolution_status=ambiguous`、不跟进它的 import（里面的第三方依赖不进 needed）、`shadowing="stdlib"`、
给 `local_shadows_stdlib_in_wrapper` 警告；只有裸 `python script.py`（`PROFILE_BARE`）才按遮蔽判本地并跟进。
裸解释器的对拍留在 `test_import_origin_static.py::TestRealPythonParity`。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from tavotto.engine import figcapture, importscan

WRAPPERS = [figcapture.PROFILE_SAFE, figcapture.PROFILE_NATIVE]


def _scan(root, profile, **kw):
    res = importscan.scan(root, "s.py", entry=importscan.Entry(profile=profile), **kw)
    return res, {c.module: c for c in res.classes}


@pytest.mark.parametrize("profile", WRAPPERS)
class TestWrapperConservativeRule:
    def test_enum_py_with_requests_is_ambiguous_and_not_followed(self, tmp_path, profile):
        (tmp_path / "s.py").write_text("import enum\nimport numpy\n", encoding="utf-8")
        (tmp_path / "enum.py").write_text("import requests\n", encoding="utf-8")
        res, got = _scan(tmp_path, profile, declared={"numpy": ""})
        c = got["enum"]
        assert (c.bucket, c.resolution_status, c.shadowing) == ("local", "ambiguous", "stdlib")
        assert c.local_path == "enum.py"
        assert "local_shadows_stdlib_in_wrapper" in c.warnings
        assert "requests" not in got
        assert not any(x.needed for x in got.values() if x.module != "numpy")
        assert res.files == ("s.py",)

    def test_a_json_py_and_a_package_dir_are_the_same(self, tmp_path, profile):
        (tmp_path / "s.py").write_text("import json\nimport colorsys\n", encoding="utf-8")
        (tmp_path / "json.py").write_text("import requests\n", encoding="utf-8")
        (tmp_path / "colorsys").mkdir()
        (tmp_path / "colorsys" / "__init__.py").write_text("import scipy\n", encoding="utf-8")
        res, got = _scan(tmp_path, profile)
        for name in ("json", "colorsys"):
            assert got[name].resolution_status == "ambiguous"
            assert got[name].shadowing == "stdlib"
        assert "requests" not in got and "scipy" not in got
        assert res.files == ("s.py",)

    def test_a_non_stdlib_local_module_is_still_followed(self, tmp_path, profile):
        (tmp_path / "s.py").write_text("import helper\n", encoding="utf-8")
        (tmp_path / "helper.py").write_text("import requests\n", encoding="utf-8")
        _, got = _scan(tmp_path, profile)
        assert got["helper"].resolution_status == "resolved" and got["requests"].needed

    def test_a_stdlib_name_without_a_local_file_stays_plain_stdlib(self, tmp_path, profile):
        (tmp_path / "s.py").write_text("import enum\n", encoding="utf-8")
        _, got = _scan(tmp_path, profile)
        assert (got["enum"].bucket, got["enum"].resolution_status) == ("stdlib", "resolved")


class TestBareScriptKeepsTheRealShadowing:
    def test_enum_py_is_local_and_followed(self, tmp_path):
        (tmp_path / "s.py").write_text("import enum\n", encoding="utf-8")
        (tmp_path / "enum.py").write_text("import requests\n", encoding="utf-8")
        _, got = _scan(tmp_path, importscan.PROFILE_BARE)
        assert (got["enum"].bucket, got["enum"].resolution_status) == ("local", "resolved")
        assert got["enum"].shadowing == "stdlib"
        assert got["requests"].needed and got["requests"].via == ("enum.py",)

    def test_a_real_bare_interpreter_agrees(self, tmp_path):
        """对拍：裸 `python s.py` 真的执行了本地 colorsys.py（遮蔽标准库）。"""
        (tmp_path / "s.py").write_text("import colorsys\n", encoding="utf-8")
        (tmp_path / "colorsys.py").write_text("print('LOCAL-COLORSYS')\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-S", "s.py"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "LOCAL-COLORSYS" in proc.stdout
        _, got = _scan(tmp_path, importscan.PROFILE_BARE)
        assert got["colorsys"].bucket == "local"


def test_the_removed_preload_table_is_gone():
    assert not hasattr(importscan, "WRAPPER_PRELOADED")
