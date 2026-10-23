"""scripts/dev/refresh_autoinstall_list.py（自动安装名单生成器）与它生成的真实名单。全部离线，fixture 在 tmp_path。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tavotto.engine import convertdeps

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dev" / "refresh_autoinstall_list.py"
_spec = importlib.util.spec_from_file_location("refresh_autoinstall_list", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _run(tmp_path, rows, top=4):
    src = tmp_path / "in.tsv"
    src.write_text("".join(f"{n}\t{c}\n" for n, c in rows), encoding="utf-8")
    out = tmp_path / "out.txt"
    rc = mod.main(["--input", str(src), "--top", str(top), "--days", "30", "--out", str(out)])
    return rc, out


def _names(out):
    return [
        ln for ln in out.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")
    ]


def test_excluded_tools_never_listed(tmp_path):
    rows = [
        ("pip", 900),
        ("Setuptools", 800),
        ("wheel", 700),
        ("requests", 600),
        ("poetry_core", 500),
    ]
    rc, out = _run(tmp_path, rows, top=5)
    names = _names(out)
    assert rc == 0 and "requests" in names
    assert not set(names) & mod.EXCLUDE


def test_seed_merged_and_dedup_normalized(tmp_path):
    rows = [("Foo_Bar", 9), ("foo.bar", 8), ("foo-bar", 7), ("requests", 6)]
    _, out = _run(tmp_path, rows, top=4)
    names = _names(out)
    assert [n for n in names if n.lower().startswith("foo")] == ["foo-bar"]
    assert set(mod.SEED) <= set(names)
    assert names == sorted(names)


def test_too_few_rows_refuses_and_writes_nothing(tmp_path):
    with pytest.raises(SystemExit):
        _run(tmp_path, [("requests", 5)], top=10)
    assert not (tmp_path / "out.txt").exists()


def test_malformed_tsv_refuses(tmp_path):
    with pytest.raises(SystemExit):
        _run(tmp_path, [("requests", "abc")], top=1)
    assert not (tmp_path / "out.txt").exists()


def test_header_fields_present(tmp_path):
    _, out = _run(tmp_path, [("a", 4), ("b", 3), ("c", 2), ("d", 1)], top=4)
    head = "\n".join(
        ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.startswith("#")
    )
    for field in ("来源：", "窗口天数：30", "快照日期：", "top N：4", "条数：", "别手改", "排除"):
        assert field in head
    assert f"条数：{len(_names(out))}" in head


def test_real_generated_allowlist():
    convertdeps.allowlist.cache_clear()
    names = convertdeps.allowlist()
    assert {"numpy", "requests"} <= names
    assert not {"pip", "setuptools", "wheel"} & names
    assert len(names) >= 900
