"""#1051：冻结产物里顶层的 libcrypto / libssl 必须与 `_ssl.pyd` 同源（构建期闸门 + 构建后自检的接线）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import frozen_ssl_gate as gate  # noqa: E402

HOST = "C:/hostedtoolcache/windows/Python/3.13.16/x64/DLLs"
RUNTIME = "D:/a/Tavotto/Tavotto/runtime/python-embed"


def toc(*, crypto_src: str, ssl_src: str | None = None, extra=()):
    ssl_src = ssl_src or crypto_src
    return [
        ("_ssl.pyd", f"{HOST}/_ssl.pyd", "EXTENSION"),
        ("libcrypto-3.dll", f"{crypto_src}/libcrypto-3.dll", "BINARY"),
        ("libssl-3.dll", f"{ssl_src}/libssl-3.dll", "BINARY"),
        # runtime 以 DATA 放在 runtime/ 下面：不是顶层，不参与比较
        ("runtime/libcrypto-3.dll", f"{RUNTIME}/libcrypto-3.dll", "DATA"),
        *extra,
    ]


def test_dlls_from_the_same_directory_as__ssl_pass():
    assert gate.ssl_dll_problems(toc(crypto_src=HOST)) == []
    gate.assert_ssl_dlls_consistent(toc(crypto_src=HOST))


def test_the_runtimes_libcrypto_winning_the_top_level_slot_is_caught():
    """#1051 的形状：runtime 的依赖先到，顶层留下的是 runtime 里（旧版）的那份。"""
    problems = gate.ssl_dll_problems(toc(crypto_src=RUNTIME))
    assert len(problems) == 2 and all("不在同一个目录" in p for p in problems)
    with pytest.raises(SystemExit, match="#1051"):
        gate.assert_ssl_dlls_consistent(toc(crypto_src=RUNTIME))


def test_only_one_of_the_two_dlls_mismatching_is_still_caught():
    problems = gate.ssl_dll_problems(toc(crypto_src=HOST, ssl_src=RUNTIME))
    assert len(problems) == 1 and "libssl-3.dll" in problems[0]


def test_a_missing_ssl_extension_or_libcrypto_is_a_problem():
    assert gate.ssl_dll_problems([("libcrypto-3.dll", f"{HOST}/libcrypto-3.dll", "BINARY")])
    no_crypto = [("_ssl.pyd", f"{HOST}/_ssl.pyd", "EXTENSION")]
    assert any("libcrypto" in p for p in gate.ssl_dll_problems(no_crypto))


def test_nested_copies_and_other_dlls_are_ignored():
    extra = [
        (
            "pikepdf.libs/libcrypto-3-x64.dll",
            "C:/site-packages/pikepdf.libs/libcrypto-3-x64.dll",
            "BINARY",
        ),
        ("vcruntime140.dll", "C:/Windows/System32/vcruntime140.dll", "BINARY"),
    ]
    assert gate.ssl_dll_problems(toc(crypto_src=HOST, extra=extra)) == []


def test_the_spec_adds_the_runtime_after_analysis_on_windows_and_runs_the_gate():
    spec = (REPO / "packaging" / "tavotto.spec").read_text(encoding="utf-8")
    assert "_RUNTIME_AFTER_ANALYSIS" in spec
    after = spec[spec.index("a = Analysis(") :]
    assert 'Tree(str(_RUNTIME_AFTER_ANALYSIS), prefix="runtime")' in after
    assert "assert_ssl_dlls_consistent" in after
    # Windows 分支不再把 runtime 放进 datas（会被重新归类为二进制并做依赖分析）
    win_branch = spec[
        spec.index('if sys.platform == "win32":') : spec.index("else:\n        datas.append")
    ]
    assert "datas.append" not in win_branch
    # 其它平台仍走 datas（macOS 的符号链接 / 签名行为不变）
    assert 'datas.append((str(RUNTIME), "runtime"))' in spec


def test_the_built_artifact_is_import_checked_before_it_ships():
    entry = (REPO / "packaging" / "entry.py").read_text(encoding="utf-8")
    assert '"--ssl-selftest"' in entry and "import ssl" in entry
    assert entry.index('"--ssl-selftest"') < entry.index("    _redirect_streams()\n")
    desktop = (REPO / "scripts" / "build_desktop.py").read_text(encoding="utf-8")
    assert '"--ssl-selftest"' in desktop
    smoke = (REPO / "scripts" / "smoke_app.py").read_text(encoding="utf-8")
    assert '"--ssl-selftest"' in smoke
