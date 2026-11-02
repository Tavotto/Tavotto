"""同名不同脚本的图分开登记（ADR 0159，TVD-TZQ9）：注册表别名表、捕获层改名、执行描述、池的复用判定、显示名。

真跑两份脚本的端到端用例在 `test_script_probe.py::TestRegistryEffects`
（`test_copied_script_saving_same_names_elsewhere_gets_its_own_figures`）；这里是各层的纯函数与契约。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.engine import discover, execspec, figcapture, pool, registry, runtimeasset


def _registry(figs: Path, scripts: dict) -> None:
    (figs / registry.REGISTRY_NAME).write_text(
        json.dumps({"version": 1, "scripts": scripts}), encoding="utf-8"
    )


def _entry(stems, **extra) -> dict:
    return {"entry": "main", "cost": "medium", "notes": "", "stems": list(stems), **extra}


# ---------------------------------------------------------------- 注册表
def test_alias_table_is_cleaned_to_one_to_one_pairs_into_this_scripts_stems():
    stems = ["x@b", "y@b"]
    raw = {"x": "x@b", "y": "y@b", "z": "elsewhere", "": "x@b", "w": "w", "dup": "y@b", 3: "x@b"}
    # 两个原图名指向同一个登记名（y / dup）：都不要；登记名不在本脚本里、原名为空、自己指自己：丢掉
    assert registry.clean_stem_aliases(raw, stems) == {"x": "x@b"}
    assert registry.clean_stem_aliases(None, stems) == {}


def test_entries_carry_aliases_only_when_present_and_display_name_is_the_raw_name():
    reg = registry.Registry()
    reg.load_data(
        {
            "scripts": {
                "a.py": _entry(["x"]),
                "b.py": _entry(["x@b"], stem_aliases={"x": "x@b"}),
            }
        }
    )
    assert "stem_aliases" not in reg.entries()["a.py"]  # 没有别名的条目与旧文件逐键相同
    assert reg.entries()["b.py"]["stem_aliases"] == {"x": "x@b"}
    assert reg.for_stem("x")["script"] == "a.py"
    assert reg.for_stem("x@b")["script"] == "b.py"
    assert reg.display_name("x@b") == "x"
    assert reg.display_name("x") == "x"


def test_stem_aliases_for_reads_the_project_registry_and_fails_open(tmp_path):
    assert registry.stem_aliases_for(tmp_path, "b.py") == ()  # 没有注册表
    _registry(tmp_path, {"b.py": _entry(["x@b", "y@b"], stem_aliases={"y": "y@b", "x": "x@b"})})
    assert registry.stem_aliases_for(tmp_path, "b.py") == (("x", "x@b"), ("y", "y@b"))
    assert registry.stem_aliases_for(tmp_path, "a.py") == ()
    (tmp_path / registry.REGISTRY_NAME).write_text("{not json", encoding="utf-8")
    assert registry.stem_aliases_for(tmp_path, "b.py") == ()


def test_register_keeps_existing_aliases_and_prunes_stolen_ones(tmp_path):
    """别名粘住：下一次登记（探测结果里已经只有登记图名）不给别名也保留；登记名被别的脚本认领走就跟着摘掉。"""
    _registry(tmp_path, {"b.py": _entry(["x@b"], stem_aliases={"x": "x@b"})})
    discover.register(tmp_path, "b.py", ["x@b"])
    scripts = json.loads((tmp_path / registry.REGISTRY_NAME).read_text("utf-8"))["scripts"]
    assert scripts["b.py"]["stem_aliases"] == {"x": "x@b"}
    discover.register(tmp_path, "b.py", ["x@b", "y@b"], stem_aliases={"y": "y@b"})
    scripts = json.loads((tmp_path / registry.REGISTRY_NAME).read_text("utf-8"))["scripts"]
    assert scripts["b.py"]["stem_aliases"] == {"x": "x@b", "y": "y@b"}
    discover.register(tmp_path, "c.py", ["y@b"])  # 手工指认把 y@b 归给 c.py
    scripts = json.loads((tmp_path / registry.REGISTRY_NAME).read_text("utf-8"))["scripts"]
    assert scripts["b.py"]["stem_aliases"] == {"x": "x@b"}
    assert "stem_aliases" not in scripts["c.py"]


# ---------------------------------------------------------------- 捕获层
def test_aliased_stem_uses_the_script_file_name_and_numbers_collisions():
    assert figcapture.aliased_stem("fig", "sub/plot_new.py", set()) == "fig@plot_new"
    assert figcapture.aliased_stem("fig", "sub\\plot_new.py", set()) == "fig@plot_new"
    taken = {"fig@plot_new", "fig@plot_new-2"}
    assert figcapture.aliased_stem("fig", "other/plot_new.py", taken) == "fig@plot_new-3"


def test_apply_stem_aliases_renames_every_stem_keyed_table_and_keeps_order():
    a, b, c = object(), object(), object()
    capture = {"x": a, "keep": b, "y": c}
    source = {"x": "savefig", "keep": "pyplot", "y": "savefig"}
    calls = {"x": [{"format": "png"}], "y": None}
    extras = {"x": [[]]}
    file_stems = {"y": "y0"}
    out = figcapture.apply_stem_aliases(
        (("x", "x@b"), ("y", "y@b")), capture, source, calls, extras, file_stems
    )
    assert out == ["x@b", "y@b"]
    assert list(capture) == ["x@b", "keep", "y@b"]
    assert capture["x@b"] is a and capture["y@b"] is c
    assert source == {"x@b": "savefig", "keep": "pyplot", "y@b": "savefig"}
    assert calls == {"x@b": [{"format": "png"}], "y@b": None}
    assert extras == {"x@b": [[]]}
    # 找原件按它写文件时的名字：原图名本身已是补认领名时沿着链取
    assert file_stems == {"x@b": "x", "y@b": "y0"}


def test_apply_stem_aliases_is_a_noop_without_aliases_and_never_overwrites_another_figure():
    a, b = object(), object()
    capture = {"x": a}
    assert figcapture.apply_stem_aliases((), capture, {}, {}, {}) == []
    assert capture == {"x": a}
    # native 第二个屏障：登记图名已被同一张图占着 → 只摘原名
    capture = {"x@b": a, "x": a}
    figcapture.apply_stem_aliases({"x": "x@b"}, capture, {}, {}, {})
    assert capture == {"x@b": a}
    # 被另一张图占着 → 另起名字，不覆盖
    capture = {"x@b": a, "x": b}
    figcapture.apply_stem_aliases({"x": "x@b"}, capture, {}, {}, {})
    assert capture == {"x@b": a, "x@b-2": b}


@pytest.mark.parametrize(
    "raw",
    [
        '{"x": "y"}',
        '[["x"]]',
        '[["x", ""]]',
        '[["x", "a"], ["x", "b"]]',
        '[["x", "a"], ["y", "a"]]',
    ],
)
def test_parse_stem_aliases_rejects_bad_shapes(raw):
    with pytest.raises(ValueError):
        figcapture.parse_stem_aliases(raw)


def test_parse_stem_aliases_round_trips_the_spec_encoding():
    pairs = (("x", "x@b"), ("图", "图@新样本"))
    assert figcapture.parse_stem_aliases(execspec._aliases_json(pairs)) == dict(pairs)
    assert figcapture.parse_stem_aliases(None) == {}


# ---------------------------------------------------------------- 执行描述
def _safe(figs, **kw):
    return execspec.safe_spec("b.py", str(figs), "main", interpreter="/py", sandbox="/sb", **kw)


def test_no_aliases_keeps_argv_and_stable_identity_byte_for_byte(tmp_path):
    spec = _safe(tmp_path)
    assert spec.stem_aliases == ()
    argv = execspec.worker_argv(spec, worker_py="/w.py", out_dir="/o")
    assert "--stem-aliases" not in argv
    assert "stem_aliases" not in spec.stable_payload()
    native = execspec.native_spec(
        str(tmp_path / "b.py"), interpreter="/py", cwd=str(tmp_path), project_root=str(tmp_path)
    )
    assert "--stem-aliases" not in execspec.bridge_argv(native, runner_py="/r.py", out_dir="/o")


def test_safe_and_native_specs_take_the_registry_aliases(tmp_path):
    _registry(tmp_path, {"b.py": _entry(["x@b"], stem_aliases={"x": "x@b"})})
    spec = _safe(tmp_path)
    assert spec.stem_aliases == (("x", "x@b"),)
    argv = execspec.worker_argv(spec, worker_py="/w.py", out_dir="/o")
    i = argv.index("--stem-aliases")
    assert json.loads(argv[i + 1]) == [["x", "x@b"]]
    assert spec.stable_payload()["stem_aliases"] == [["x", "x@b"]]
    assert execspec.spec_from_payload(spec.to_payload()).stem_aliases == spec.stem_aliases
    native = execspec.desktop_native_spec(
        str(tmp_path / "b.py"), interpreter="/py", cwd=str(tmp_path), project_root=str(tmp_path)
    )
    bargv = execspec.bridge_argv(native, runner_py="/r.py", out_dir="/o")
    assert json.loads(bargv[bargv.index("--stem-aliases") + 1]) == [["x", "x@b"]]
    assert bargv.index("--stem-aliases") < bargv.index("--")
    # 显式给（含空）就用给的：重开会话不许换成另一份表
    assert _safe(tmp_path, stem_aliases=()).stem_aliases == ()


def test_spec_rejects_aliases_that_are_not_one_to_one(tmp_path):
    with pytest.raises(ValueError):
        _safe(tmp_path, stem_aliases=(("x", "a"), ("y", "a")))
    with pytest.raises(ValueError):
        _safe(tmp_path, stem_aliases=(("x", "x"),))


# ---------------------------------------------------------------- 池 / 清单
class _FakeWorker:
    def __init__(self, spec):
        self.spec = spec


def test_pool_retires_a_session_started_with_an_older_alias_table(tmp_path):
    old = _FakeWorker(_safe(tmp_path))  # 起会话时还没有别名
    assert pool._aliases_current(old, str(tmp_path), "b.py")
    _registry(tmp_path, {"b.py": _entry(["x@b"], stem_aliases={"x": "x@b"})})
    assert not pool._aliases_current(old, str(tmp_path), "b.py")
    assert pool._aliases_current(_FakeWorker(_safe(tmp_path)), str(tmp_path), "b.py")
    assert pool._aliases_current(object(), str(tmp_path), "b.py")  # 没有 spec 的替身不拦


def test_runtime_asset_list_shows_the_raw_name_for_split_figures(tmp_path):
    reg = registry.Registry()
    reg.load_data(
        {"scripts": {"a.py": _entry(["x"]), "b.py": _entry(["x@b"], stem_aliases={"x": "x@b"})}}
    )
    rows = {r["stem"]: r for r in runtimeasset.list_assets(tmp_path, reg, worker_python=False)}
    assert rows["x@b"]["display_name"] == "x"
    assert "display_name" not in rows["x"]
