"""override 的目标身份（ADR 0083，QA 2026-09-24 SCI-03-B1）。

gid 是位置式的（`axes_i.lines_j`）。脚本重排 / 插入 / 删除曲线或子图之后，同一个
gid 指向了另一个对象——以前旧 override 会**静默**落到那个对象上、warnings 为空，
写回的一次性重放按同一个 gid 重放，也放行。

判据的主语：**一次性 worker（写回 verify 用的同一条路）按「写编辑时那一版 manifest
抄下来的 identity」重放到「结构改过的脚本」上**，按 label（独立真值，不信 gid）看
洋红色落在谁身上、有没有 warning。合格 = 洋红只在 alpha 上，或者谁都没有且有一条
warning 点名那条编辑；不合格 = 洋红落在别的曲线上。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pikepdf
import pytest

from tavotto.engine import pool, project_watch

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

HEADER = (
    "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n\n\ndef main():\n"
)

BASE = """    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot([0, 1, 2, 3], [0, 9, 2, 10], color="#1f77b4", label="alpha")
    ax.plot([0, 1, 2, 3], [0, 2, 9, 10], color="#2ca02c", label="beta")
    ax.plot([0, 1, 2, 3], [4, 4, 4, 4], color="#7f7f7f")
    ax.legend()
    fig.savefig("Fig1.pdf")
"""

#: QA 复现脚本 `sci03_structure_change.py` 的四个变体（第三条无 label 的灰线是本文件加的：
#: 它钉住「没有名字的对象不带身份」这一侧）。
VARIANTS = {
    "reorder": """    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot([0, 1, 2, 3], [0, 2, 9, 10], color="#2ca02c", label="beta")
    ax.plot([0, 1, 2, 3], [0, 9, 2, 10], color="#1f77b4", label="alpha")
    ax.legend()
    fig.savefig("Fig1.pdf")
""",
    "insert_before": """    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot([0, 1, 2, 3], [5, 5, 5, 5], color="#d62728", label="gamma")
    ax.plot([0, 1, 2, 3], [0, 9, 2, 10], color="#1f77b4", label="alpha")
    ax.plot([0, 1, 2, 3], [0, 2, 9, 10], color="#2ca02c", label="beta")
    ax.legend()
    fig.savefig("Fig1.pdf")
""",
    "delete_a": """    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot([0, 1, 2, 3], [0, 2, 9, 10], color="#2ca02c", label="beta")
    ax.legend()
    fig.savefig("Fig1.pdf")
""",
    "insert_axes": """    fig, (ax0, ax) = plt.subplots(1, 2, figsize=(5.0, 2.4))
    ax0.plot([0, 1], [1, 0], color="#9467bd", label="delta")
    ax.plot([0, 1, 2, 3], [0, 9, 2, 10], color="#1f77b4", label="alpha")
    ax.plot([0, 1, 2, 3], [0, 2, 9, 10], color="#2ca02c", label="beta")
    ax.legend()
    fig.savefig("Fig1.pdf")
""",
}

#: 同一个结构、换了一批数据（点数也变了）：编辑必须照旧跟着 alpha 走。
#: 这一条钉住「身份不含数据」的取舍——按数据核的话它会把正确的编辑也拒掉。
NEW_DATA = """    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot([0, 1, 2, 3, 4, 5], [1, 8, 3, 7, 2, 6], color="#1f77b4", label="alpha")
    ax.plot([0, 1, 2, 3, 4, 5], [0, 1, 2, 3, 4, 5], color="#2ca02c", label="beta")
    ax.plot([0, 1, 2, 3], [4, 4, 4, 4], color="#7f7f7f")
    ax.legend()
    fig.savefig("Fig1.pdf")
"""

MAGENTA = "#ff00ff"
REFUSED = re.compile(r"编辑的对象已找不到")


def _library(root: Path, body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "fig1.py").write_text(HEADER + body, encoding="utf-8")
    return root


def _replay(figs: Path, patches: list) -> tuple[dict, list[str]]:
    """一次性 worker 从零跑脚本、按 patches 全量重放（写回 verify 的同一条路）。"""
    w = pool.one_shot("fig1.py", str(figs), "main")
    try:
        resp = w.override("Fig1", patches)
        manifest = json.loads((Path(w.out_dir) / "Fig1.json").read_text(encoding="utf-8"))
        return manifest, list(resp.get("warnings") or [])
    finally:
        pool.discard(w)


def _lines(manifest: dict) -> dict[str, dict]:
    """{label: 元素}——按 label（独立真值）找曲线，不信 gid。"""
    out = {}
    for el in manifest["elements"]:
        if el.get("role") != "line":
            continue
        ed = {f["prop"]: f.get("value") for f in el.get("editable", [])}
        # 没有 label 的（manifest 把 matplotlib 自动起的 `_child…` 报成空串）按 gid 记
        out[str(ed.get("label") or el["gid"])] = {
            "gid": el["gid"],
            "identity": el.get("identity"),
            "color": str(ed.get("color", "")).lower(),
        }
    return out


def _stamped_patches(base_manifest: dict) -> tuple[list[dict], str]:
    """前端写 override 时做的事：抄下**写编辑那一刻**这个 gid 的 identity。"""
    alpha = _lines(base_manifest)["alpha"]
    assert alpha["identity"], "alpha 有显式 label，manifest 必须给出它的目标身份"
    patches = [
        {"gid": alpha["gid"], "prop": "color", "value": MAGENTA, "identity": alpha["identity"]},
        {"gid": alpha["gid"], "prop": "linewidth", "value": 4.5, "identity": alpha["identity"]},
    ]
    return patches, alpha["gid"]


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    figs = _library(tmp_path_factory.mktemp("identity-base"), BASE)
    manifest, warnings = _replay(figs, [])
    assert warnings == []
    return manifest


def test_manifest_gives_identity_to_user_labelled_elements_only(base):
    lines = _lines(base)
    assert set(lines) >= {"alpha", "beta"}
    ids = {k: v["identity"] for k, v in lines.items()}
    assert re.fullmatch(r"l1:[0-9a-f]{16}", ids["alpha"])
    assert re.fullmatch(r"l1:[0-9a-f]{16}", ids["beta"])
    assert ids["alpha"] != ids["beta"]
    # 没有 label 的灰线：matplotlib 自动起的 `_child2` 本质是位置，不算身份
    unlabeled = [v for k, v in lines.items() if k.startswith("axes_")]
    assert len(unlabeled) == 1 and unlabeled[0]["identity"] is None
    # 存的是摘要：label 原文不进 identity
    assert all("alpha" not in (el.get("identity") or "") for el in base["elements"])


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_edit_never_lands_on_another_object_after_structure_change(base, tmp_path, variant):
    """SCI-03：旧 override 只匹配原逻辑对象；匹配不上明确报告，绝不「成功」套给另一条曲线。"""
    patches, gid = _stamped_patches(base)
    figs = _library(tmp_path / variant, VARIANTS[variant])
    manifest, warnings = _replay(figs, patches)
    lines = _lines(manifest)
    magenta = sorted(k for k, v in lines.items() if v["color"] == MAGENTA)
    assert magenta in (["alpha"], []), f"编辑落到了别的对象上: {magenta}（{variant}）"
    if not magenta:
        refused = [w for w in warnings if REFUSED.search(w)]
        assert {f"{gid}.color", f"{gid}.linewidth"} <= {w.rsplit(": ", 1)[-1] for w in refused}, (
            warnings
        )


def test_same_structure_still_applies_without_warnings(base, tmp_path):
    """反方向那条边：没改结构时一个都不许拒（否则每次渲染都在报失效）。"""
    patches, _ = _stamped_patches(base)
    manifest, warnings = _replay(_library(tmp_path / "same", BASE), patches)
    assert warnings == []
    assert _lines(manifest)["alpha"]["color"] == MAGENTA


def test_new_data_for_the_same_labelled_curve_still_applies(base, tmp_path):
    """换了一批数据（点数也变了）、结构没变：编辑照旧跟着 alpha。"""
    patches, _ = _stamped_patches(base)
    manifest, warnings = _replay(_library(tmp_path / "data", NEW_DATA), patches)
    assert warnings == []
    lines = _lines(manifest)
    assert lines["alpha"]["color"] == MAGENTA
    assert lines["beta"]["color"] != MAGENTA


def test_patches_without_identity_keep_positional_matching(base, tmp_path):
    """向后兼容：旧文档里的 patch 没有 identity，行为与引入前一致（按 gid 位置）。

    这不是说这种行为对——旧文档写下时没有记下对象身份，事后无从得知它原来指谁；
    ADR 0083 把这一侧写明为已知边界。钉住它是为了让「不带 identity = 不核对」
    这条兼容承诺有人看着，而不是哪天悄悄把旧文档的编辑全部判失效。
    """
    patches, _ = _stamped_patches(base)
    legacy = [{k: v for k, v in p.items() if k != "identity"} for p in patches]
    manifest, warnings = _replay(_library(tmp_path / "legacy", VARIANTS["reorder"]), legacy)
    assert warnings == []
    assert _lines(manifest)["beta"]["color"] == MAGENTA


def test_refused_edit_is_undone_in_a_live_session(tmp_path):
    """同一个热会话里：先按对的身份应用，再发一份身份对不上的——上一次的必须还原。

    被拒的 key 等于不在列表里（全量列表语义）：热态停在洋红的话，热态 ≠ 全量重放。
    """
    figs = _library(tmp_path / "live", BASE)
    w = pool.one_shot("fig1.py", str(figs), "main")
    try:
        w.override("Fig1", [])
        first = json.loads((Path(w.out_dir) / "Fig1.json").read_text(encoding="utf-8"))
        patches, gid = _stamped_patches(first)
        w.override("Fig1", patches)
        hot = _lines(json.loads((Path(w.out_dir) / "Fig1.json").read_text(encoding="utf-8")))
        assert hot["alpha"]["color"] == MAGENTA
        wrong = [dict(p, identity=_lines(first)["beta"]["identity"]) for p in patches]
        resp = w.override("Fig1", wrong)
        after = _lines(json.loads((Path(w.out_dir) / "Fig1.json").read_text(encoding="utf-8")))
        assert after["alpha"]["color"] == "#1f77b4"
        assert any(REFUSED.search(x) and gid in x for x in resp.get("warnings") or [])
    finally:
        pool.discard(w)


def test_malformed_identity_is_refused_not_matched_by_position(base, tmp_path):
    """带了 identity 却不是非空字符串 = 脏数据：不许退回按位置匹配（那正是要堵的路）。"""
    patches = [
        {"gid": _lines(base)["alpha"]["gid"], "prop": "color", "value": MAGENTA, "identity": ""}
    ]
    manifest, warnings = _replay(_library(tmp_path / "bad", VARIANTS["reorder"]), patches)
    assert all(v["color"] != MAGENTA for v in _lines(manifest).values())
    assert any(REFUSED.search(w) for w in warnings)


def test_renaming_the_curve_does_not_invalidate_its_own_edits(tmp_path):
    """label 本身是可编辑的 prop：身份必须是 baseline 那一刻采的，不跟着编辑走。

    按实况现算的话，改名那一刻起这条曲线的身份就换了——同一份 patches 第二次渲染
    （每一次拖动定稿都是）会把用户自己的编辑全部判成「对象已找不到」。
    """
    figs = _library(tmp_path / "rename", BASE)
    w = pool.one_shot("fig1.py", str(figs), "main")
    try:
        w.override("Fig1", [])
        first = json.loads((Path(w.out_dir) / "Fig1.json").read_text(encoding="utf-8"))
        patches, gid = _stamped_patches(first)
        ident = patches[0]["identity"]
        patches.append({"gid": gid, "prop": "label", "value": "renamed", "identity": ident})
        for _ in range(2):
            resp = w.override("Fig1", patches)
            assert list(resp.get("warnings") or []) == []
        man = json.loads((Path(w.out_dir) / "Fig1.json").read_text(encoding="utf-8"))
        assert _lines(man)["renamed"]["color"] == MAGENTA
        assert _lines(man)["renamed"]["identity"] == ident
    finally:
        pool.discard(w)


# ---------------- 写回：同一道核对在一次性重放里拦下 ----------------

REGISTRY = {
    "version": 1,
    "scripts": {"fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]}},
}


def test_write_back_refuses_an_edit_whose_object_moved(tmp_path, monkeypatch):
    """写回的 verify 按同一组 patches 从零重放——对不上的那条是 warning，一条即阻断，原件零改动。"""
    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    figs = _library(tmp_path / "figures", BASE)
    (figs / "tavotto_registry.json").write_text(json.dumps(REGISTRY), encoding="utf-8")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 100))
    pdf.save(figs / "Fig1.pdf")
    try:
        m.open_project(str(figs))
        client = m.app.test_client()
        r = client.post("/api/engine/render", json={"id": "Fig1.pdf", "patches": []})
        assert r.status_code == 200, r.get_json()
        patches, gid = _stamped_patches(r.get_json()["manifest"])
        r = client.post("/api/engine/render", json={"id": "Fig1.pdf", "patches": patches})
        assert _lines(r.get_json()["manifest"])["alpha"]["color"] == MAGENTA

        # 改脚本结构 → 等 watcher（2 s 轮询）让热会话换成新脚本，免得撞上 script_changed
        time.sleep(1.1)
        (figs / "fig1.py").write_text(HEADER + VARIANTS["reorder"], encoding="utf-8")
        deadline = time.monotonic() + 15
        while True:
            r = client.post("/api/engine/render", json={"id": "Fig1.pdf", "patches": patches})
            body = r.get_json() or {}
            if (
                r.status_code == 200
                and "beta" in _lines(body["manifest"])
                and (_lines(body["manifest"])["beta"]["gid"] == gid)
            ):
                break
            assert time.monotonic() < deadline, "watcher 没有换上改过的脚本"
            time.sleep(0.5)
        # 热态同样拒绝：洋红不在 beta 上，warning 点名那条编辑
        assert _lines(body["manifest"])["beta"]["color"] != MAGENTA
        assert any(REFUSED.search(w) for w in body.get("warnings") or [])

        before = (figs / "Fig1.pdf").read_bytes()
        resp = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": patches})
        assert resp.status_code == 409, resp.get_json()
        payload = resp.get_json()
        assert payload["code"] == "write_back_warnings"
        assert any(REFUSED.search(w) for w in payload["warnings"])
        assert (figs / "Fig1.pdf").read_bytes() == before
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()


SIBLINGS = """    for stem in ("Fig1", "Fig1a"):
        fig, ax = plt.subplots(figsize=(3.2, 2.4))
        ax.plot([0, 1, 2, 3], [0, 9, 2, 10], label="alpha" if stem == "Fig1" else "other")
        fig.savefig(stem + ".pdf")
"""


def test_sync_to_a_sibling_figure_drops_the_source_identity(tmp_path, monkeypatch):
    """跨图同步是**有意**按位置映射到另一张图的另一个对象：源图的身份不许跟过去。

    跟过去的话，目标图上那条曲线 label 不同，编辑会被判成「对象已找不到」——
    同步这个功能就整个失效了。
    """
    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    figs = _library(tmp_path / "figures", SIBLINGS)
    registry = json.loads(json.dumps(REGISTRY))
    registry["scripts"]["fig1.py"]["stems"] = ["Fig1", "Fig1a"]
    (figs / "tavotto_registry.json").write_text(json.dumps(registry), encoding="utf-8")
    for stem in ("Fig1", "Fig1a"):
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(200, 100))
        pdf.save(figs / f"{stem}.pdf")
    try:
        m.open_project(str(figs))
        client = m.app.test_client()
        r = client.post("/api/engine/render", json={"id": "Fig1.pdf", "patches": []})
        assert r.status_code == 200, r.get_json()
        patches, gid = _stamped_patches(r.get_json()["manifest"])
        r = client.post(
            "/api/engine/sync_overrides",
            json={"from_id": "Fig1.pdf", "to_id": "Fig1a.pdf", "patches": patches},
        )
        assert r.status_code == 200, r.get_json()
        mapped = r.get_json()["mapped"]
        assert [(p["gid"], p["prop"]) for p in mapped] == [(gid, "color"), (gid, "linewidth")]
        assert all("identity" not in p for p in mapped)
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()
