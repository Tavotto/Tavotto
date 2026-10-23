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


def test_big_packages_are_always_listed(tmp_path):
    rc, out = _run(tmp_path, [("requests", 9), ("idna", 8)], top=2)
    names = _names(out)
    assert rc == 0 and {"torch", "tensorflow", "sympy", "xgboost"} <= set(names)


class _FakeSource:
    """按 SQL 回假数据；记下每条 SQL。`numpy_exact` 模拟单独按主键查到的真值。"""

    def __init__(self, shard_rows, numpy_exact):
        self.sqls, self.shard_rows, self.numpy_exact = [], list(shard_rows), numpy_exact

    def __call__(self, sql):
        self.sqls.append(sql)
        if "max(date)" in sql:
            return "2026-10-22\n"
        if "project = 'numpy'" in sql:
            return f"{self.numpy_exact}\n"
        rows = self.shard_rows.pop(0)
        return "".join(f"{n}\t{c}\n" for n, c in rows)


def test_every_shard_throws_on_truncation_and_together_cover_the_key_space(monkeypatch):
    fake = _FakeSource([[("numpy", 100)], [("pandas", 50)], [("requests", 40)], [("six", 30)]], 100)
    monkeypatch.setattr(mod, "_query", fake)
    tsv, snap = mod.fetch_tsv(top=10, days=30)
    shard_sqls = [s for s in fake.sqls if "GROUP BY project" in s]
    assert len(shard_sqls) == len(mod.SHARDS) and snap == "2026-10-22"
    assert all("read_overflow_mode='throw'" in s for s in shard_sqls)
    # 切片首尾相接、覆盖整个主键空间：第一片从空串起，最后一片无上界，中间的上界 = 下一片的下界
    assert mod.SHARDS[0][0] == "" and mod.SHARDS[-1][1] is None
    assert all(a[1] == b[0] for a, b in zip(mod.SHARDS, mod.SHARDS[1:]))
    assert {"numpy", "pandas", "requests", "six"} == {ln.split("\t")[0] for ln in tsv.splitlines()}


def test_a_silently_truncated_count_is_caught_by_the_cross_check(tmp_path, monkeypatch):
    # 切片里 numpy 只有 40（被截断），单独按主键查是 100 → 拒绝、不写文件
    fake = _FakeSource([[("numpy", 40)], [("pandas", 50)], [("requests", 40)], [("six", 30)]], 100)
    monkeypatch.setattr(mod, "_query", fake)
    out = tmp_path / "out.txt"
    with pytest.raises(SystemExit, match="交叉核对失败"):
        mod.main(["--top", "4", "--out", str(out)])
    assert not out.exists()


def test_the_real_list_has_the_big_packages():
    allow = convertdeps.allowlist()
    assert {"torch", "tensorflow", "sympy", "xgboost", "jax", "transformers"} <= allow


@pytest.mark.parametrize(
    "name",
    ["uv-build", "pip-audit", "setuptools-scm", "hatch-vcs", "installer", "maturin", "cmake"],
)
def test_packaging_tool_families_are_excluded_by_prefix_or_name(tmp_path, name):
    rc, out = _run(tmp_path, [(name, 900), ("requests", 800)], top=2)
    assert rc == 0 and name not in _names(out) and "requests" in _names(out)


@pytest.mark.parametrize("name", ["uvicorn", "uvloop", "packaging", "pipe"])
def test_lookalike_names_are_not_excluded(name):
    assert not mod.excluded(name)


def test_the_window_is_anchored_to_the_snapshot_date_not_today(monkeypatch):
    fake = _FakeSource([[("numpy", 100)], [], [], []], 100)
    monkeypatch.setattr(mod, "_query", fake)
    mod.fetch_tsv(top=10, days=30)
    windowed = [s for s in fake.sqls if "GROUP BY project" in s or "project = 'numpy'" in s]
    assert windowed and all("today()" not in s for s in windowed)
    want = "date > toDate('2026-10-22') - 30 AND date <= toDate('2026-10-22')"
    assert all(want in s for s in windowed)


def test_the_real_list_has_no_packaging_tools():
    allow = convertdeps.allowlist()
    assert not {"uv-build", "installer", "pip-audit", "setuptools-scm", "cmake", "maturin"} & allow
