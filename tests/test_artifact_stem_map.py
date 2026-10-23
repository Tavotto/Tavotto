"""#1251：磁盘素材文件 → 图的解析不再只看文件名 stem。

同一脚本里 `panel.png` 由甲写、`panel.pdf` 由乙写（乙以 `panel-2` 登记，#1250）。注册表只记「脚本 → stems」时，
打开磁盘上的 `panel.pdf` 按 `for_stem("panel")` 落到甲。现在注册表按脚本多记一份 `artifacts`
（`{项目相对路径: stem}`），`Registry.for_artifact` 先查它、查不到再退回按 stem，app 层所有「主语是磁盘文件」
的解析都走 `_disk_panel` / `_disk_panel_rel`。

钉住的合同：
1. 新版本读没有 `artifacts` 的老注册表：行为与按 stem 完全一致；旧 reader 对未知字段宽容（本文件的最后一条
   用例把 reader 对未知键的宽容钉成合同，旧 reader 就是靠这个读新文件）；
2. `discover.register` 的 `artifacts`：没有执行证据（手工裁决 / AI）不抹已知映射；并入 / 替换 / 被别的脚本认领；
   没有映射时文件字节与此前逐字节一致；
3. 打开 `panel.pdf` 得到乙：渲染的 stem、素材清单的 script、写回只写 `panel.pdf` 不碰 `panel.png`，
   基线记在 `panel-2` 名下；`panel.png` 仍是甲。
"""

from __future__ import annotations

import json

import pytest

from tavotto.engine import discover, probe, registry

SCRIPT = "panels.py"


def _cfg(artifacts=None, stems=("panel", "panel-2")):
    entry = {"entry": "main", "cost": "light", "notes": "", "stems": list(stems)}
    if artifacts is not None:
        entry["artifacts"] = artifacts
    return {"version": 1, "scripts": {SCRIPT: entry}}


def _reg(data) -> registry.Registry:
    reg = registry.Registry()
    reg.load_data(data)
    return reg


# --------------------------------------------------------------------------- 注册表
def test_for_artifact_prefers_the_recorded_file_owner():
    reg = _reg(_cfg({"panel.png": "panel", "panel.pdf": "panel-2"}))
    assert reg.for_artifact("panel.png")["stem"] == "panel"
    pdf = reg.for_artifact("panel.pdf")
    assert pdf["stem"] == "panel-2" and pdf["script"] == SCRIPT
    # 登记表里没有的文件仍按文件名 stem 回落
    assert reg.for_artifact("panel.svg")["stem"] == "panel"
    assert reg.for_artifact("other.pdf") is None
    assert reg.for_artifact("panel.pdf".replace("/", "\\"))["stem"] == "panel-2"


def test_old_registry_without_artifacts_behaves_like_for_stem():
    reg = _reg(_cfg(None))
    assert reg.for_artifact("panel.pdf")["stem"] == "panel"
    assert reg.for_artifact("panel-2.pdf")["stem"] == "panel-2"
    assert reg.artifacts_of(SCRIPT) == {}


def test_bad_artifact_entries_are_dropped_not_fatal():
    bad = {
        "ok.pdf": "panel-2",
        "ghost.pdf": "not-a-stem-of-this-script",
        "num.pdf": 3,
        "": "panel",
    }
    reg = _reg(_cfg(bad))
    assert reg.artifacts_of(SCRIPT) == {"ok.pdf": "panel-2"}
    # 整个字段形状不对也只是当没有
    assert _reg(_cfg(["panel.pdf"])).for_artifact("panel.pdf")["stem"] == "panel"


def test_a_path_claimed_by_two_scripts_is_ambiguous_whatever_the_json_order():
    """手写 / 合并出来的注册表里同一路径挂在两个脚本下：谁都不赢（退回按文件名 stem），与脚本键的顺序无关；
    第三个认领者也不能把它加回来。"""

    def scripts(order):
        table = {
            "a.py": {"stems": ["x"], "artifacts": {"x.pdf": "x", "only_a.pdf": "x"}},
            "b.py": {"stems": ["y"], "artifacts": {"x.pdf": "y"}},
            "c.py": {"stems": ["z"], "artifacts": {"x.pdf": "z"}},
        }
        return {"scripts": {k: table[k] for k in order}}

    import itertools

    for order in itertools.permutations(["a.py", "b.py", "c.py"]):
        reg = _reg(scripts(order))
        # 退回按文件名 stem：`x.pdf` 的 stem 是 `x`，登记在 a.py
        assert reg.for_artifact("x.pdf")["stem"] == "x", order
        assert reg.artifacts_of("b.py") == {} and reg.artifacts_of("c.py") == {}, order
        assert reg.artifacts_of("a.py") == {"only_a.pdf": "x"}, order  # 没有争用的条目照常保留
        assert "artifacts" not in reg.entries()["b.py"]
        assert reg.entries()["a.py"]["artifacts"] == {"only_a.pdf": "x"}
    # 两个脚本的 stem 就是争用的文件名 stem 时区别明显：落在谁手上只看 stem，不看顺序
    swapped = {
        "scripts": {
            "b.py": {"stems": ["y"], "artifacts": {"y.pdf": "y"}},
            "a.py": {"stems": ["x"], "artifacts": {"y.pdf": "x"}},
        }
    }
    assert _reg(swapped).for_artifact("y.pdf")["script"] == "b.py"  # 按 stem `y` 回落，不是按顺序


def test_normalised_duplicate_keys_in_one_script_are_ambiguous_too():
    reg = _reg(_cfg({"d/panel.pdf": "panel", "d\\panel.pdf": "panel-2", "ok.pdf": "panel-2"}))
    assert reg.artifacts_of(SCRIPT) == {"ok.pdf": "panel-2"}


def test_reader_ignores_unknown_script_keys():
    """旧 reader 能读带 `artifacts` 的新文件，靠的是对未知键的宽容：未知键不报错、也不进 entries()。
    `artifacts` 是已知键：有才进 entries()，与磁盘条目同形（`project_refresh` 比相等判执行是否仍有效）。"""
    data = _cfg({"panel.pdf": "panel-2"})
    data["scripts"][SCRIPT]["some_future_field"] = {"x": 1}
    reg = _reg(data)
    assert set(reg.entries()[SCRIPT]) == {"entry", "cost", "notes", "stems", "artifacts"}
    assert set(_reg(_cfg(None)).entries()[SCRIPT]) == {"entry", "cost", "notes", "stems"}


# --------------------------------------------------------------------------- discover.register
def _read(tmp_path):
    return json.loads((tmp_path / registry.REGISTRY_NAME).read_text(encoding="utf-8"))


def test_register_records_artifacts_and_stays_byte_identical_without_them(tmp_path):
    discover.register(tmp_path, SCRIPT, ["panel"], entry="main", cost="light")
    plain = (tmp_path / registry.REGISTRY_NAME).read_bytes()
    assert b"artifacts" not in plain
    discover.register(
        tmp_path,
        SCRIPT,
        ["panel", "panel-2"],
        entry="main",
        cost="light",
        artifacts={"panel.png": "panel", "panel.pdf": "panel-2"},
    )
    assert _read(tmp_path)["scripts"][SCRIPT]["artifacts"] == {
        "panel.pdf": "panel-2",
        "panel.png": "panel",
    }


def test_register_without_evidence_keeps_known_map_and_prunes_dead_stems(tmp_path):
    discover.register(
        tmp_path,
        SCRIPT,
        ["panel", "panel-2"],
        artifacts={"panel.png": "panel", "panel.pdf": "panel-2"},
    )
    # 手工裁决 / AI：没给 artifacts → 沿用
    discover.register(tmp_path, SCRIPT, ["panel", "panel-2"], cost="heavy")
    assert _read(tmp_path)["scripts"][SCRIPT]["artifacts"]["panel.pdf"] == "panel-2"
    # stems 缩了：指向已不属于它的图的条目随之摘掉
    discover.register(tmp_path, SCRIPT, ["panel"])
    assert _read(tmp_path)["scripts"][SCRIPT]["artifacts"] == {"panel.png": "panel"}


def test_register_replace_vs_append_with_evidence(tmp_path):
    discover.register(tmp_path, SCRIPT, ["panel", "panel-2"], artifacts={"panel.pdf": "panel-2"})
    # 无参数整条替换：以这次执行为准（甲现在也有原件，乙换成 svg）
    discover.register(
        tmp_path,
        SCRIPT,
        ["panel", "panel-2"],
        artifacts={"panel.png": "panel", "panel.svg": "panel-2"},
    )
    assert _read(tmp_path)["scripts"][SCRIPT]["artifacts"] == {
        "panel.png": "panel",
        "panel.svg": "panel-2",
    }
    # 带运行配置的执行并进去：同一张图换了文件时新的让位旧的，其余保留
    discover.register(
        tmp_path, SCRIPT, ["panel-2"], append=True, artifacts={"panel.pdf": "panel-2"}
    )
    assert _read(tmp_path)["scripts"][SCRIPT]["artifacts"] == {
        "panel.png": "panel",
        "panel.pdf": "panel-2",
    }
    # 执行了、但这次一个原件都没有（pyplot）：空映射替换掉旧的，而不是沿用陈旧文件
    discover.register(tmp_path, SCRIPT, ["panel", "panel-2"], artifacts={})
    assert "artifacts" not in _read(tmp_path)["scripts"][SCRIPT]


def test_claiming_a_stem_moves_its_artifacts_off_the_other_script(tmp_path):
    discover.register(tmp_path, "a.py", ["panel-2"], artifacts={"panel.pdf": "panel-2"})
    discover.register(tmp_path, "b.py", ["panel-2"], artifacts={"panel.pdf": "panel-2"})
    scripts = _read(tmp_path)["scripts"]
    assert "a.py" not in scripts or "artifacts" not in scripts["a.py"]
    assert scripts["b.py"]["artifacts"] == {"panel.pdf": "panel-2"}
    assert _reg_from_disk(tmp_path).for_artifact("panel.pdf")["script"] == "b.py"


def _reg_from_disk(tmp_path):
    return registry.open_registry(tmp_path)


def test_descriptor_artifacts_only_takes_figures_with_an_original():
    descs = [
        {"stem": "panel", "original_artifact": "panel.png"},
        {"stem": "panel-2", "original_artifact": "panel.pdf"},
        {"stem": "pyplot-fig", "original_artifact": None},
    ]
    assert probe.descriptor_artifacts(descs) == {"panel.png": "panel", "panel.pdf": "panel-2"}
    assert probe.descriptor_artifacts([]) == {}
    assert probe.descriptor_artifacts(None) is None  # 没有执行证据


def test_descriptor_artifacts_takes_every_file_a_figure_wrote():
    """P1：乙存 pdf + svg 时两个文件都记到乙；只有 `original_artifact` 的旧描述符照旧。"""
    descs = [
        {"stem": "panel", "original_artifact": "panel.png", "artifacts": ["panel.png"]},
        {
            "stem": "panel-2",
            "original_artifact": "panel.pdf",
            "artifacts": ["panel.pdf", "panel.svg"],
        },
        {"stem": "old", "original_artifact": "old.png"},
    ]
    assert probe.descriptor_artifacts(descs) == {
        "panel.png": "panel",
        "panel.pdf": "panel-2",
        "panel.svg": "panel-2",
        "old.png": "old",
    }


def test_descriptor_artifacts_drops_a_path_two_figures_both_claim():
    """同一路径两张图都声称：这里没有「谁最后写」的证据，不记（退回按文件名 stem），而不是猜一个。"""
    descs = [
        {"stem": "panel", "artifacts": ["panel.png", "panel.pdf"]},
        {"stem": "panel-2", "artifacts": ["panel.pdf", "panel.svg"]},
    ]
    assert probe.descriptor_artifacts(descs) == {"panel.png": "panel", "panel.svg": "panel-2"}


def test_descriptor_factory_carries_artifacts_and_stays_byte_identical_without_them():
    from tavotto.engine import figcapture

    kw = dict(
        script="a.py",
        entry="main",
        stem="panel-2",
        capture_source=figcapture.SOURCE_SAVEFIG,
        execution_profile=figcapture.PROFILE_SAFE,
        size_mm=(10, 10),
        source_fingerprint="sha256:x",
    )
    plain = figcapture.build_descriptor(**kw).to_payload()
    assert "artifacts" not in plain  # 旧描述符一个字节不变
    d = figcapture.build_descriptor(
        original_artifact="panel.pdf",
        artifacts=["panel.pdf", "panel.svg"],
        **kw,
    )
    assert d.to_payload()["artifacts"] == ["panel.pdf", "panel.svg"]
    assert figcapture.descriptor_from_payload(d.to_payload()) == d
    with pytest.raises(ValueError):
        figcapture.build_descriptor(
            **{**kw, "capture_source": figcapture.SOURCE_PYPLOT}, artifacts=["x.pdf"]
        )


def test_find_artifacts_all_own_formats_but_one_when_formats_unknown(tmp_path):
    from tavotto.engine import figcapture

    for n in ("panel.png", "panel.pdf", "panel.svg"):
        (tmp_path / n).write_bytes(b"x")
    root = str(tmp_path)
    assert figcapture.find_artifacts(root, "panel", formats=["pdf", "svg"]) == [
        "panel.pdf",
        "panel.svg",
    ]
    assert figcapture.find_artifacts(root, "panel", formats=["png"]) == ["panel.png"]
    assert len(figcapture.find_artifacts(root, "panel")) == 1  # 不知道格式：旧判据，只取首个
    assert figcapture.find_artifacts(root, "../panel", formats=["pdf"]) == []


# --------------------------------------------------------------------------- app：打开 panel.pdf 得到乙
@pytest.fixture
def client(tmp_path, monkeypatch):
    from tavotto import app as m
    from tavotto.engine import project_watch

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    yield m.app.test_client()
    m.reset_projects()
    project_watch.stop()


def _open_same_stem_project(tmp_path, monkeypatch, *, with_map: bool):
    """Fig1.png 由 Fig1 写、Fig1.pdf 由 Fig1-2 写；假 worker 的全量重放给出这两张图的描述符。"""
    import test_write_back as wb
    from tavotto import app as m

    figs = wb._figs(tmp_path)
    art = {"Fig1.png": "Fig1", "Fig1.pdf": "Fig1-2"}
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "fig1.py": {
                        "entry": "main",
                        "cost": "light",
                        "notes": "",
                        "stems": ["Fig1", "Fig1-2"],
                        **({"artifacts": art} if with_map else {}),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    m.reset_projects()
    m.open_project(str(figs))
    manifest = wb._manifest()
    manifest["stem"] = "Fig1-2"
    hot = wb._FakeWorker(figs, tmp_path / "_hot", manifest=manifest)
    fresh = wb._FakeWorker(figs, tmp_path / "_fresh", manifest=manifest)
    fresh.last_build_descriptors = [
        {"stem": "Fig1", "original_artifact": "Fig1.png", "savefig_calls": [{"format": "png"}]},
        {"stem": "Fig1-2", "original_artifact": "Fig1.pdf", "savefig_calls": [{"format": "pdf"}]},
    ]
    wb._use(monkeypatch, hot, fresh)
    seen: list[str] = []
    real = m.engine_enginesession.resolve

    def spy(**kw):
        seen.append(kw["stem"])
        return real(**kw)

    monkeypatch.setattr(m.engine_enginesession, "resolve", spy)
    return figs, fresh, seen, m


def test_opening_the_pdf_gives_the_figure_that_wrote_it(client, tmp_path, monkeypatch):
    figs, fresh, seen, m = _open_same_stem_project(tmp_path, monkeypatch, with_map=True)
    before_png = (figs / "Fig1.png").read_bytes()
    resp = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 200, resp.get_json()
    assert seen == ["Fig1-2"], "渲染/写回解析到乙，而不是按文件名 stem 落到甲"
    assert (figs / "Fig1.pdf").read_bytes() == b"NEW-pdf"
    assert (figs / "Fig1.png").read_bytes() == before_png, "甲的 PNG 一个字节都不许动"
    assert fresh.calls.count("png") == 0 and "pdf" in fresh.calls
    assert "Fig1-2" in m.load_baked(), "基线记在乙名下"
    assert "Fig1" not in m.load_baked()


def test_opening_the_png_still_gives_the_first_figure(client, tmp_path, monkeypatch):
    figs, fresh, seen, m = _open_same_stem_project(tmp_path, monkeypatch, with_map=True)
    manifest_stem = "Fig1"
    fresh.manifest["stem"] = manifest_stem
    fresh._write_manifest()
    before_pdf = (figs / "Fig1.pdf").read_bytes()
    resp = client.post("/api/engine/update_source", json={"id": "Fig1.png", "patches": []})
    assert resp.status_code == 200, resp.get_json()
    assert seen == ["Fig1"]
    assert (figs / "Fig1.png").read_bytes() == b"NEW-png"
    assert (figs / "Fig1.pdf").read_bytes() == before_pdf
    assert "pdf" not in fresh.calls


def test_without_the_map_the_pdf_still_resolves_by_stem(client, tmp_path, monkeypatch):
    """老注册表（没有 artifacts）：行为与此前一致——Fig1.pdf 按文件名 stem 落到 Fig1；写回守卫仍拒绝。"""
    figs, fresh, seen, m = _open_same_stem_project(tmp_path, monkeypatch, with_map=False)
    before = {p: (figs / p).read_bytes() for p in ("Fig1.pdf", "Fig1.png")}
    resp = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "write_back_foreign_file"
    assert seen == ["Fig1"]
    assert {p: (figs / p).read_bytes() for p in before} == before


def test_disk_panel_helper_is_the_single_resolution_point(client, tmp_path, monkeypatch):
    figs, _, _, m = _open_same_stem_project(tmp_path, monkeypatch, with_map=True)
    assert m._disk_panel_rel("Fig1.pdf")[0] == "Fig1-2"
    assert m._disk_panel_rel("Fig1.png")[0] == "Fig1"
    assert m._disk_panel_rel("nothing.pdf") == ("nothing", None)
    assert m._has_runnable_script("Fig1.pdf")
    with m.app.test_request_context():
        assert m._disk_panel(figs / "Fig1.pdf")[1]["script"] == "fig1.py"


# --------------------------------------------------------------------------- 真 worker：执行 → 登记 → 解析
def test_real_run_registers_the_map_and_resolves_the_pdf_to_the_second_figure(tmp_path):
    """同 stem 夹具（`test_same_stem_other_figure.SCRIPT`）真跑一次：描述符的原件按各自存过的格式绑定，
    `register_probed` 把它们记进注册表，重新加载后 `panel.pdf` 解析到 `panel-2`。"""
    from tavotto.engine import pool
    from test_same_stem_other_figure import SCRIPT as SAME_STEM, WORKER_PY

    if WORKER_PY is None:
        pytest.skip("not_run：找不到装有 matplotlib 的解释器")
    (tmp_path / "panels.py").write_text(SAME_STEM, encoding="utf-8")
    (tmp_path / "panel.png").write_bytes(b"\x89PNG")
    (tmp_path / "panel.pdf").write_bytes(b"%PDF")
    w = pool.one_shot("panels.py", str(tmp_path), "main")
    try:
        w.ensure_built()
        descriptors = list(w.last_build_descriptors)
    finally:
        pool.discard(w)
    stems = [d["stem"] for d in descriptors]
    assert stems == ["panel", "panel-2"]
    out = probe.register_probed(
        tmp_path,
        "panels.py",
        {"stems": stems, "entry": "main", "descriptors": descriptors, "remap_generation": None},
    )
    assert out["registered"], out
    assert _read(tmp_path)["scripts"]["panels.py"]["artifacts"] == {
        "panel.pdf": "panel-2",
        "panel.png": "panel",
    }
    reg = registry.open_registry(tmp_path)
    assert reg.for_artifact("panel.pdf")["stem"] == "panel-2"
    assert reg.for_artifact("panel.png")["stem"] == "panel"


TWO_FORMATS_SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def first():
    fig, ax = plt.subplots(figsize=(2.0, 1.6))
    ax.plot([0, 1], [0, 1])
    fig.savefig("panel.png", dpi=100)


def second():
    fig, ax = plt.subplots(figsize=(2.4, 1.6))
    ax.plot([0, 1], [1, 0])
    fig.savefig("panel.pdf")
    fig.savefig("panel.svg")
    plt.close(fig)


def main():
    first()
    second()


if __name__ == "__main__":
    main()
"""


def test_real_run_second_figure_saving_two_formats_owns_both_files(tmp_path):
    """P1 真 worker：甲存 panel.png，乙存 panel.pdf + panel.svg。乙登记为 `panel-2`，打开 svg / pdf 都得到乙，
    不是默默落到甲。"""
    from tavotto.engine import pool
    from test_same_stem_other_figure import WORKER_PY

    if WORKER_PY is None:
        pytest.skip("not_run：找不到装有 matplotlib 的解释器")
    (tmp_path / "panels.py").write_text(TWO_FORMATS_SCRIPT, encoding="utf-8")
    for name in ("panel.png", "panel.pdf", "panel.svg"):
        (tmp_path / name).write_bytes(b"x")
    w = pool.one_shot("panels.py", str(tmp_path), "main")
    try:
        w.ensure_built()
        descriptors = list(w.last_build_descriptors)
    finally:
        pool.discard(w)
    assert [d["stem"] for d in descriptors] == ["panel", "panel-2"]
    by_stem = {d["stem"]: d for d in descriptors}
    assert sorted(by_stem["panel-2"]["artifacts"]) == ["panel.pdf", "panel.svg"]
    assert by_stem["panel"]["artifacts"] == ["panel.png"]
    out = probe.register_probed(
        tmp_path,
        "panels.py",
        {
            "stems": [d["stem"] for d in descriptors],
            "entry": "main",
            "descriptors": descriptors,
            "remap_generation": None,
        },
    )
    assert out["registered"], out
    reg = registry.open_registry(tmp_path)
    assert reg.for_artifact("panel.png")["stem"] == "panel"
    assert reg.for_artifact("panel.pdf")["stem"] == "panel-2"
    assert reg.for_artifact("panel.svg")["stem"] == "panel-2"


# --------------------------------------------------------------------------- P2：CLI 等的键 == app 记回执的键
def test_cli_target_stem_equals_the_stem_the_app_records_receipts_under(
    client, tmp_path, monkeypatch
):
    """同 stem 情形下 `tavotto open panel.pdf`：CLI 轮询的 stem 必须等于 app 记交接回执的 stem（两边是同一个
    `Registry.artifact_stem`）。老注册表（没有 artifacts）两边都按文件名 stem。"""
    from tavotto.engine import handoff

    for with_map, pdf_stem in ((True, "Fig1-2"), (False, "Fig1")):
        figs, _, _, m = _open_same_stem_project(tmp_path, monkeypatch, with_map=with_map)
        (figs / "Fig1.pdf").write_bytes(b"%PDF")
        cli = handoff.resolve_target(str(figs / "Fig1.pdf"))
        assert cli.stem == pdf_stem == m._handoff_target_stem("Fig1.pdf")
        png = handoff.resolve_target(str(figs / "Fig1.png"))
        assert png.stem == "Fig1" == m._handoff_target_stem("Fig1.png")
    # 注册表读不了 / 不存在：文件名 stem，行为与此前一致
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "x.pdf").write_bytes(b"%PDF")
    assert handoff.resolve_target(str(bare / "x.pdf")).stem == "x"


# --------------------------------------------------------------------------- 素材盘点：不同的图不合并
def _real_png(path):
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 6), False)
    pix.set_rect(pix.irect, (200, 30, 30))
    pix.save(str(path))


def _touch_pair(root, art):
    import test_write_back as wb

    figs = wb._figs(root)
    _real_png(figs / "Fig1.png")
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "fig1.py": {
                        "entry": "main",
                        "cost": "light",
                        "notes": "",
                        "stems": ["Fig1", "Fig1-2"],
                        **({"artifacts": art} if art else {}),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return figs


def test_iter_assets_keeps_two_files_of_different_figures_apart(tmp_path):
    from tavotto.engine import project_refresh

    figs = _touch_pair(tmp_path, {"Fig1.png": "Fig1", "Fig1.pdf": "Fig1-2"})
    ids = sorted(p.name for p, _k in project_refresh.iter_assets(figs))
    assert ids == ["Fig1.pdf", "Fig1.png"]  # 甲（png）与乙（pdf）各是一份素材


def test_iter_assets_still_folds_one_figure_saved_in_two_formats(tmp_path):
    from tavotto.engine import project_refresh

    # 没有映射（老项目）/ 映射说两个文件是同一张图：矢量版压掉位图，同旧
    for art in (None, {"Fig1.png": "Fig1", "Fig1.pdf": "Fig1"}):
        figs = _touch_pair(tmp_path, art)
        assert sorted(p.name for p, _k in project_refresh.iter_assets(figs)) == ["Fig1.pdf"], art


def test_panels_api_lists_both_disk_assets_with_their_own_stems(client, tmp_path, monkeypatch):
    from tavotto import app as m

    figs = _touch_pair(tmp_path, {"Fig1.png": "Fig1", "Fig1.pdf": "Fig1-2"})
    m.reset_projects()
    m.open_project(str(figs))
    panels = {p["id"]: p for p in client.get("/api/panels").get_json()["panels"]}
    assert set(panels) == {"Fig1.png", "Fig1.pdf"}
    assert "stem" not in panels["Fig1.png"]  # 与文件名 stem 相同：不发
    assert panels["Fig1.pdf"]["stem"] == "Fig1-2"
    # 各自解析到正确的图
    assert m._disk_panel_rel("Fig1.png")[0] == "Fig1"
    assert m._disk_panel_rel("Fig1.pdf")[0] == "Fig1-2"


def test_panels_api_without_a_map_is_unchanged(client, tmp_path):
    from tavotto import app as m

    figs = _touch_pair(tmp_path, None)
    m.reset_projects()
    m.open_project(str(figs))
    panels = client.get("/api/panels").get_json()["panels"]
    assert [p["id"] for p in panels] == ["Fig1.pdf"]
    assert all("stem" not in p for p in panels)


def test_runtime_list_does_not_double_list_a_figure_whose_original_is_mapped(tmp_path):
    """乙（Fig1-2）的原件是 Fig1.pdf：磁盘素材的地盘，不再同时出现 runtime 条目；没有映射条目的图仍按文件名找。"""
    from tavotto.engine import runtimeasset

    figs = _touch_pair(tmp_path, {"Fig1.png": "Fig1", "Fig1.pdf": "Fig1-2"})
    info = registry.open_registry(figs).entries()["fig1.py"]
    assert runtimeasset.has_disk_original(figs, info, "Fig1-2")
    assert runtimeasset.has_disk_original(figs, info, "Fig1")
    (figs / "Fig1.pdf").unlink()
    assert not runtimeasset.has_disk_original(figs, info, "Fig1-2")  # 乙的原件没了
    assert runtimeasset.has_disk_original(figs, info, "Fig1")
    old = {"stems": ["Fig1"]}
    assert runtimeasset.has_disk_original(figs, old, "Fig1")  # 老条目：按文件名


# --------------------------------------------------------------------------- panel.file_changed 直接带磁盘文件 id
def _file_changed_payload(m, monkeypatch, scripts):
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(m, "sse_publish", lambda kind, data: sent.append((kind, data)))
    m._script_change_handler(m.current_ctx())(scripts)
    assert [k for k, _ in sent] == ["panel.file_changed"]
    return sent[0][1]


def test_file_changed_event_carries_the_files_of_the_figures_that_changed(
    client, tmp_path, monkeypatch
):
    from tavotto import app as m

    figs = _touch_pair(tmp_path, {"Fig1.png": "Fig1", "Fig1.pdf": "Fig1-2"})
    m.reset_projects()
    m.open_project(str(figs))
    # 乙（Fig1-2）变了：files 是 Fig1.pdf，不含甲的 Fig1.png
    with m.app.test_request_context():
        assert m._panel_files_of_stems(m.current_ctx(), ["Fig1-2"]) == ["Fig1.pdf"]
        assert m._panel_files_of_stems(m.current_ctx(), ["Fig1"]) == ["Fig1.png"]
        assert m._panel_files_of_stems(m.current_ctx(), ["nobody"]) == []
    payload = _file_changed_payload(m, monkeypatch, ["fig1.py"])
    assert sorted(payload["stems"]) == ["Fig1", "Fig1-2"]
    assert payload["files"] == ["Fig1.pdf", "Fig1.png"]


def test_file_changed_files_include_the_bitmap_a_pdf_twin_hides(client, tmp_path):
    """同一张图有 PNG 和 PDF（素材盘点里 PNG 被矢量版让位）：排版里留着的 PNG 面板也要收到事件，files 两个都含。"""
    from tavotto import app as m

    # 无映射（老项目）与映射说两个文件是同一张图：都一样
    for art in (None, {"Fig1.png": "Fig1", "Fig1.pdf": "Fig1"}):
        figs = _touch_pair(tmp_path, art)
        m.reset_projects()
        m.open_project(str(figs))
        with m.app.test_request_context():
            assert m._panel_files_of_stems(m.current_ctx(), ["Fig1"]) == ["Fig1.pdf", "Fig1.png"], (
                art
            )
            assert m._panel_files_of_stems(m.current_ctx(), ["Fig1-2"]) == [], art
        # 对照：素材盘点里 PNG 确实被让位隐藏
        from tavotto.engine import project_refresh

        assert [p.name for p, _k in project_refresh.iter_assets(figs)] == ["Fig1.pdf"]


def test_file_changed_event_omits_files_when_they_cannot_be_computed(client, tmp_path, monkeypatch):
    from tavotto import app as m

    figs = _touch_pair(tmp_path, None)
    m.reset_projects()
    m.open_project(str(figs))
    monkeypatch.setattr(m.engine_refresh, "iter_assets", lambda *a, **k: 1 / 0)
    payload = _file_changed_payload(m, monkeypatch, ["fig1.py"])
    assert "files" not in payload  # 前端退回按文件名 stem（老行为）


# --------------------------------------------------------------------------- 真实文件名大小写 / 只发完整的 files
def test_find_artifacts_keeps_the_real_file_name_case(tmp_path):
    from tavotto.engine import figcapture

    (tmp_path / "panel.PDF").write_bytes(b"%PDF")
    (tmp_path / "panel.png").write_bytes(b"x")
    (tmp_path / "other.pdf").write_bytes(b"%PDF")
    root = str(tmp_path)
    assert figcapture.find_artifacts(root, "panel", formats=["pdf"]) == ["panel.PDF"]
    assert figcapture.find_artifacts(root, "panel", formats=["pdf", "png"]) == [
        "panel.PDF",
        "panel.png",
    ]
    assert figcapture.find_original_artifact(root, "panel", formats=["pdf"]) == "panel.PDF"
    assert (
        figcapture.find_original_artifact(root, "panel") == "panel.PDF"
    )  # 不知道格式：旧顺序里的首个
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "plot.SVG").write_bytes(b"<svg/>")
    assert figcapture.find_artifacts(root, "sub/plot", formats=["svg"]) == ["sub/plot.SVG"]


def test_real_run_upper_case_extension_resolves_by_its_real_id(tmp_path):
    """乙存 `panel.PDF`、甲存 `panel.png`：登记进注册表的键是真实文件名，素材盘点的真实 id 查得到，解析到 `panel-2`。"""
    from tavotto.engine import pool, project_refresh
    from test_same_stem_other_figure import WORKER_PY

    if WORKER_PY is None:
        pytest.skip("not_run：找不到装有 matplotlib 的解释器")
    (tmp_path / "panels.py").write_text(
        TWO_FORMATS_SCRIPT.replace('fig.savefig("panel.pdf")', 'fig.savefig("panel.PDF")').replace(
            '    fig.savefig("panel.svg")\n', ""
        ),
        encoding="utf-8",
    )
    for name in ("panel.png", "panel.PDF"):
        (tmp_path / name).write_bytes(b"x")
    w = pool.one_shot("panels.py", str(tmp_path), "main")
    try:
        w.ensure_built()
        descriptors = list(w.last_build_descriptors)
    finally:
        pool.discard(w)
    stems = [d["stem"] for d in descriptors]
    assert stems == ["panel", "panel-2"]
    out = probe.register_probed(
        tmp_path,
        "panels.py",
        {"stems": stems, "entry": "main", "descriptors": descriptors, "remap_generation": None},
    )
    assert out["registered"], out
    assert _read(tmp_path)["scripts"]["panels.py"]["artifacts"] == {
        "panel.PDF": "panel-2",
        "panel.png": "panel",
    }
    reg = registry.open_registry(tmp_path)
    ids = sorted(p.name for p, _k in project_refresh.iter_assets(tmp_path, reg=reg))
    assert ids == ["panel.PDF", "panel.png"]  # 素材盘点的真实 id
    assert reg.for_artifact("panel.PDF")["stem"] == "panel-2"
    assert reg.for_artifact("panel.png")["stem"] == "panel"


def test_file_changed_event_never_carries_a_partial_files_list(client, tmp_path, monkeypatch):
    """某个子目录暂时读不了：iter_assets 非 strict 会悄悄跳过那个子树——这里用 strict，算不出就不带 `files`
    （前端退回按文件名 stem），不「部分带」、也不发空数组。正常情况仍带。"""
    import os

    from tavotto import app as m

    figs = _touch_pair(tmp_path, {"Fig1.png": "Fig1", "Fig1.pdf": "Fig1-2"})
    (figs / "sub").mkdir()
    (figs / "sub" / "Fig1.png").write_bytes(b"x")
    m.reset_projects()
    m.open_project(str(figs))
    assert _file_changed_payload(m, monkeypatch, ["fig1.py"])["files"] == [
        "Fig1.pdf",
        "Fig1.png",
        "sub/Fig1.png".replace("/", os.sep),
    ]

    real = os.scandir

    def flaky(path="."):
        if os.path.basename(os.fspath(path)) == "sub":
            raise PermissionError(13, "denied", os.fspath(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", flaky)
    payload = _file_changed_payload(m, monkeypatch, ["fig1.py"])
    assert "files" not in payload
    assert sorted(payload["stems"]) == ["Fig1", "Fig1-2"]


def test_find_artifacts_listing_is_not_recorded_as_a_user_script_probe(tmp_path, monkeypatch):
    """worker 里 `os.scandir` 被换成记账版（脚本读了哪些路径的诊断）。Tavotto 自己找原件的列目录不是用户脚本的探测：
    相对根 + 不存在的子目录这种会被记账版记下来的调用，走捕获的真 scandir 就不留痕。"""
    from tavotto.engine import figcapture

    monkeypatch.chdir(tmp_path)
    misses = figcapture.ProbeMisses()
    undo = figcapture.install_probe_observer(misses, str(tmp_path))
    try:
        assert figcapture.find_artifacts(".", "nosuchdir/plot", formats=["pdf"]) == []
        # 对照：用户脚本自己列同一个不存在的目录，记账版照常记
        import os

        with pytest.raises(FileNotFoundError):
            os.scandir("nosuchdir")
    finally:
        undo()
    assert [m["target"] for m in misses.items] == ["nosuchdir"]


def test_cli_normalises_a_hand_typed_path_to_the_real_file_name(tmp_path):
    """`panel.PDF` 是乙的真实文件名、注册表键也是它；用户在大小写不敏感的盘上敲 `panel.pdf`：先规范成真实名再查映射。"""
    from tavotto.engine import handoff

    (tmp_path / "panel.PDF").write_bytes(b"%PDF")
    (tmp_path / "panel.png").write_bytes(b"x")
    (tmp_path / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "p.py": {
                        "entry": "main",
                        "stems": ["panel", "panel-2"],
                        "artifacts": {"panel.PDF": "panel-2", "panel.png": "panel"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    root = str(tmp_path)
    assert handoff._real_rel(root, "panel.pdf") == "panel.PDF"
    assert handoff._artifact_stem(root, "panel.pdf", "panel") == "panel-2"
    assert handoff._artifact_stem(root, "panel.PDF", "panel") == "panel-2"
    assert handoff._artifact_stem(root, "panel.png", "panel") == "panel"
    assert handoff._real_rel(root, "nothing.pdf") == "nothing.pdf"  # 找不到的原样


def test_panel_stem_endpoint_answers_even_for_files_the_inventory_does_not_list(client, tmp_path):
    """「重建为可编辑图」按它取权威 stem：素材清单不列的文件（被同一张图的 PDF 让位隐藏的 PNG）也答得出。"""
    from tavotto import app as m

    # 同一张图的 PNG 被 PDF 让位：/api/panels 只有 pdf，但 panel-stem 对 png 也答 Fig1
    figs = _touch_pair(tmp_path, None)
    m.reset_projects()
    m.open_project(str(figs))
    assert [p["id"] for p in client.get("/api/panels").get_json()["panels"]] == ["Fig1.pdf"]
    got = client.get("/api/panel-stem", query_string={"id": "Fig1.png"})
    assert got.status_code == 200 and got.get_json() == {"id": "Fig1.png", "stem": "Fig1"}
    # 两张不同的图：各自的 stem
    figs = _touch_pair(tmp_path, {"Fig1.png": "Fig1", "Fig1.pdf": "Fig1-2"})
    m.reset_projects()
    m.open_project(str(figs))
    assert (
        client.get("/api/panel-stem", query_string={"id": "Fig1.pdf"}).get_json()["stem"]
        == "Fig1-2"
    )
    assert (
        client.get("/api/panel-stem", query_string={"id": "Fig1.png"}).get_json()["stem"] == "Fig1"
    )
    # 不是项目里的文件：老规矩 4xx
    assert client.get("/api/panel-stem", query_string={"id": "nothing.png"}).status_code == 404
    assert client.get("/api/panel-stem", query_string={"id": "../x.png"}).status_code in (403, 404)
