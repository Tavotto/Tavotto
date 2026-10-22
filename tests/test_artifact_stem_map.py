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


def test_one_file_belongs_to_one_script():
    data = {
        "scripts": {
            "a.py": {"stems": ["x"], "artifacts": {"x.pdf": "x"}},
            "b.py": {"stems": ["y"], "artifacts": {"x.pdf": "y"}},
        }
    }
    assert _reg(data).for_artifact("x.pdf")["script"] == "a.py"


def test_reader_ignores_unknown_script_keys():
    """旧 reader 能读带 `artifacts` 的新文件，靠的是对未知键的宽容：未知键不报错、也不进 entries()。"""
    data = _cfg({"panel.pdf": "panel-2"})
    data["scripts"][SCRIPT]["some_future_field"] = {"x": 1}
    reg = _reg(data)
    assert set(reg.entries()[SCRIPT]) == {"entry", "cost", "notes", "stems"}


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
