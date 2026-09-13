"""保留式规范化（ADR 0051）的**真链路**验收：真 matplotlib、真 worker、真产物。

`tests/test_normalize.py` 在合成 manifest 上盯逻辑；这里盯的是整条闭环在真实
渲染上成不成立：

    原图基准 → 修改约定 → 尺寸/字体最小编辑 → 真实渲染检测 → 有界局部修复
    → 最终产物验收 → 提交或回退

既要有**做成的**案例（只改字体 / 缩到 120 mm），也要有**正确退出的**案例
（字体没装 / 80 mm 装不下 / 40 mm 更装不下）——退出时会话必须回到 B0、磁盘上
不能多出一个看起来成功的文件、用户的脚本与原件一个字节不动。

字体用 matplotlib 自带的 DejaVu Serif 当「装了的」那个，商业字体一个都不依赖。
缺 matplotlib 就跳过（.venv 里没有科学栈是常态）。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto import pdfbackend  # noqa: E402
from tavotto.engine import interference, normalize, pool as engine_pool  # noqa: E402
from tavotto_mcp import bridge, server  # noqa: E402

HEAD = """\
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent
"""

#: 典型的用户现有图：两个子图、误差棒、图例、对数轴，150 mm 宽，脚本里一次 tight_layout。
SCRIPT_TWO = (
    HEAD
    + """

def main():
    rng = np.random.default_rng(7)
    x = np.linspace(0, 10, 15)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(150 / 25.4, 60 / 25.4))
    for k, c in ((3, "tab:blue"), (6, "tab:orange")):
        y = 1 - np.exp(-x / k)
        ax1.errorbar(x, y, yerr=rng.uniform(0.01, 0.04, x.size), marker="o", ms=3,
                     lw=1.0, capsize=2, color=c, label=f"k = {k} min")
    ax1.set_xlabel("Time (min)")
    ax1.set_ylabel("Conversion (-)")
    ax1.set_title("Kinetics")
    ax1.legend(loc="lower right", frameon=False)
    ax1.tick_params(direction="in")
    t = np.linspace(300, 800, 40)
    ax2.plot(t, 1e5 * np.exp(-t / 200), lw=1.0, color="tab:green", label="Signal")
    ax2.set_yscale("log")
    ax2.set_xlabel("Temperature (K)")
    ax2.set_ylabel("Intensity (a.u.)")
    ax2.set_title("Thermal decay")
    ax2.legend(frameon=False)
    ax2.tick_params(direction="in")
    fig.tight_layout(pad=0.4)
    fig.savefig(OUT / "Two.pdf")


if __name__ == "__main__":
    main()
"""
)

#: 持久 tight 布局引擎 + 8 pt 字：布局归引擎管，改尺寸不该出现任何 position 调整。
SCRIPT_TIGHT_ENGINE = (
    HEAD
    + """

def main():
    x = np.linspace(0, 6, 40)
    fig, ax = plt.subplots(figsize=(80 / 25.4, 60 / 25.4), layout="tight")
    ax.plot(x, np.sin(x), lw=1.0, label="sin")
    ax.plot(x, np.cos(x), lw=1.0, ls="--", label="cos")
    ax.set_xlabel("Time (s)", fontsize=8)
    ax.set_ylabel("Amplitude (V)", fontsize=8)
    ax.tick_params(labelsize=8, direction="in")
    ax.legend(loc="upper right", frameon=False, fontsize=8)
    fig.savefig(OUT / "Tight.pdf")


if __name__ == "__main__":
    main()
"""
)

SCRIPT_CONSTRAINED = SCRIPT_TIGHT_ENGINE.replace('layout="tight"', 'layout="constrained"').replace(
    '"Tight.pdf"', '"Constrained.pdf"'
)

#: 手工布局：subplots_adjust 定死的边距，100 mm 宽。
SCRIPT_MANUAL = (
    HEAD
    + """

def main():
    x = np.linspace(0, 6, 40)
    fig, ax = plt.subplots(figsize=(100 / 25.4, 70 / 25.4))
    fig.subplots_adjust(left=0.16, right=0.96, bottom=0.17, top=0.9)
    ax.plot(x, np.exp(-x / 3), lw=1.0, label="decay")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Signal (a.u.)")
    ax.set_title("Manual layout")
    ax.legend(loc="upper right", frameon=False)
    fig.savefig(OUT / "Manual.pdf")


if __name__ == "__main__":
    main()
"""
)

#: 有意的重叠：曲线交叉、插图、箭头连到目标、图例在子图内的空白角落。
SCRIPT_INTENT = (
    HEAD
    + """

def main():
    x = np.linspace(0, 10, 60)
    fig, ax = plt.subplots(figsize=(90 / 25.4, 65 / 25.4))
    fig.subplots_adjust(left=0.15, right=0.95, bottom=0.15, top=0.92)
    ax.plot(x, x / 10, lw=1.0, label="rise")
    ax.plot(x, 1 - x / 10, lw=1.0, label="fall")
    ax.set_xlabel("x (-)")
    ax.set_ylabel("y (-)")
    ax.legend(loc="upper center", frameon=False)
    ax.annotate("crossing", xy=(5, 0.5), xytext=(7.5, 0.2),
                arrowprops=dict(arrowstyle="->", lw=0.8), fontsize=8)
    ins = ax.inset_axes([0.12, 0.1, 0.3, 0.3])
    ins.plot(x, np.sin(x), lw=0.8)
    ins.tick_params(labelsize=6)
    fig.savefig(OUT / "Intent.pdf")


if __name__ == "__main__":
    main()
"""
)

#: 原件是 bbox_inches="tight" 存的：磁盘页面 ≠ figsize。
SCRIPT_TIGHTBBOX = SCRIPT_MANUAL.replace(
    'fig.savefig(OUT / "Manual.pdf")', 'fig.savefig(OUT / "Cropped.pdf", bbox_inches="tight")'
)

#: 原图本来就有一条裁切（tight_layout pad 太小，x 轴标题贴着下边探出去）。
SCRIPT_PREEXISTING = (
    HEAD
    + """

def main():
    x = np.linspace(0, 6, 40)
    fig, ax = plt.subplots(figsize=(80 / 25.4, 50 / 25.4))
    fig.subplots_adjust(left=0.2, right=0.96, bottom=0.09, top=0.9)
    ax.plot(x, np.sin(x), lw=1.0, label="sin")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Amplitude (V)")
    ax.legend(loc="upper right", frameon=False)
    fig.savefig(OUT / "Pre.pdf")


if __name__ == "__main__":
    main()
"""
)


def _worker_python():
    try:
        return engine_pool.find_worker_python()
    except engine_pool.WorkerError:
        return None


WORKER_PY = _worker_python()
pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="没有带科学栈的解释器，跳过真链路用例")

AVAILABLE_FONT = "DejaVu Serif"  # matplotlib 自带，任何机器都有
MISSING_FONT = "Tavotto Nonexistent Serif 9x"


def _project(tmp_path: Path, name: str, script: str, stem: str) -> Path:
    figures = tmp_path / name
    figures.mkdir()
    (figures / f"{name}.py").write_text(script, encoding="utf-8")
    (figures / "tavotto_registry.json").write_text(
        json.dumps(
            {"scripts": {f"{name}.py": {"entry": "main", "cost": "light", "stems": [stem]}}}
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [WORKER_PY, str(figures / f"{name}.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(figures),
    )
    assert proc.returncode == 0, proc.stderr
    return figures


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    bridge.reset_root_authority()
    bridge.sessions().clear()
    yield
    bridge.sessions().clear()
    bridge.reset_root_authority()


@pytest.fixture(scope="module", autouse=True)
def _shutdown_workers():
    yield
    bridge.shutdown_all()


def _open(project: Path, stem: str) -> str:
    return bridge.open_figure(str(project), stem=stem)["session_id"]


def _faces(manifest: dict) -> dict[str, str]:
    return {e["gid"]: e["face"] for e in manifest["elements"] if "face" in e}


def _visible_inside(manifest: dict) -> bool:
    """所有可见元素画出来的部分都在图幅内（按 clip_bbox 折过）。"""
    for el in manifest["elements"]:
        r = interference.visible_rect(el)
        if r is None or el["role"] in ("figure", "ticks"):
            continue
        w, h = manifest["size_mm"]
        if r[0] * w < -0.3 or r[1] * h < -0.3 or (r[2] - 1) * w > 0.3 or (r[3] - 1) * h > 0.3:
            return False
    return True


def _files_in(out_dir: Path) -> list[str]:
    return sorted(p.name for p in out_dir.glob("*")) if out_dir.exists() else []


# ------------------------------ 零修改往返 ------------------------------------
@pytest.mark.parametrize(
    "name, script, stem",
    [
        ("two", SCRIPT_TWO, "Two"),
        ("manual", SCRIPT_MANUAL, "Manual"),
        ("tight", SCRIPT_TIGHT_ENGINE, "Tight"),
    ],
)
def test_zero_edit_roundtrip_keeps_page_size_and_pixels(tmp_path, name, script, stem):
    """导入 → 不编辑 → 导出：页面尺寸逐位相同，像素只差字体嵌入方式（Type 3 vs 42）。"""
    project = _project(tmp_path, name, script, stem)
    sid = _open(project, stem)
    out_dir = tmp_path / "out"
    res = bridge.export(sid, formats=["pdf"], out_dir=str(out_dir), explicit_confirm=True)
    exported = Path(res["files"][0]["path"])
    orig = project / f"{stem}.pdf"
    a, b = pdfbackend.probe_asset(orig, "pdf"), pdfbackend.probe_asset(exported, "pdf")
    assert abs(a["w_pt"] - b["w_pt"]) < 0.01 and abs(a["h_pt"] - b["h_pt"]) < 0.01
    pa, pb = tmp_path / "a.png", tmp_path / "b.png"
    pdfbackend.render_preview_png(orig, 1200, pa)
    pdfbackend.render_preview_png(exported, 1200, pb)
    diff = pdfbackend.compare_png(pa, pb)
    # 字形栅格化的抖动在 1% 像素以内；布局移位会到几个百分点
    assert diff["changed_pixel_ratio"] < 0.02, diff
    assert diff["mean_abs_diff"] < 0.5, diff
    assert bridge.get_session(sid).patches == []


# ------------------------------ 只改字体 --------------------------------------
def test_font_only_change_reaches_ticks_and_touches_nothing_else(tmp_path):
    project = _project(tmp_path, "two", SCRIPT_TWO, "Two")
    sid = _open(project, "Two")
    b0 = bridge.get_session(sid).manifest
    out = bridge.normalize_figure(
        sid, targets={"font_family": AVAILABLE_FONT}, formats=["pdf"], out_dir=str(tmp_path / "out")
    )
    assert out["exit"] == normalize.EXIT_DONE, json.dumps(out.get("verdict"), ensure_ascii=False)[
        :2000
    ]
    v = out["verdict"]
    assert (
        v["protected_changes"] == []
        and v["structure"]["missing"] == []
        and v["structure"]["extra"] == []
    )
    assert v["targets_met"]["font_family"] is True
    # 刻度组也换了，而且是真正画字的那张脸换了
    faces = _faces(out["manifest"])
    assert faces["axes_0.xticks"] == AVAILABLE_FONT and faces["axes_1.yticks"] == AVAILABLE_FONT
    assert all(f == AVAILABLE_FONT for f in faces.values()), faces
    # 对数轴 10^4 的 mathtext 也跟着换了
    yt = next(e for e in out["manifest"]["elements"] if e["gid"] == "axes_1.yticks")
    assert yt.get("math_face") == AVAILABLE_FONT
    # 只有 fontfamily 这一种 patch：没有 position、没有颜色、没有尺寸
    assert {p["prop"] for p in out["patches"]} == {"fontfamily"}
    assert out["adjustments"] == []
    assert out["manifest"]["size_mm"] == b0["size_mm"]
    # 最终文件里只有这一种字体
    pdf = next(f for f in out["files"] if f["format"] == "pdf")
    assert pdfbackend.pdf_fonts(Path(pdf["path"])) == ["DejaVuSerif"]
    assert out["acceptance"]["pdf"]["font_ok"] is True


def test_min_font_size_only_lifts_text_below_the_floor(tmp_path):
    # 轴标题 / 刻度 6 pt，图例仍是 8 pt：只有前两类该被补齐
    script = (
        SCRIPT_TIGHT_ENGINE.replace('"Time (s)", fontsize=8', '"Time (s)", fontsize=6')
        .replace('"Amplitude (V)", fontsize=8', '"Amplitude (V)", fontsize=6')
        .replace("labelsize=8", "labelsize=6")
    )
    assert "frameon=False, fontsize=8" in script
    project = _project(tmp_path, "small", script, "Tight")
    sid = _open(project, "Tight")
    out = bridge.normalize_figure(
        sid, targets={"min_font_pt": 8}, formats=["pdf"], out_dir=str(tmp_path / "out")
    )
    assert out["exit"] == normalize.EXIT_DONE
    raised = {(p["gid"], p["value"]) for p in out["patches"] if p["prop"] == "fontsize"}
    # 6 pt 的轴标题 / 刻度补到 8；图例本来就是 8 → 不动；标题不存在
    assert ("axes_0.xlabel", 8.0) in raised and ("axes_0.xticks", 8.0) in raised
    assert not any(g == "axes_0.legend" for g, _ in raised)
    assert out["verdict"]["targets_met"]["font_floor"] is True


# ------------------------------ 缩小宽度 --------------------------------------
def test_shrinking_width_keeps_structure_and_adapts_margins_within_budget(tmp_path):
    project = _project(tmp_path, "two", SCRIPT_TWO, "Two")
    sid = _open(project, "Two")
    out = bridge.normalize_figure(
        sid,
        targets={"width_mm": 120},
        formats=["pdf", "png"],
        dpi=300,
        out_dir=str(tmp_path / "out"),
    )
    assert out["exit"] == normalize.EXIT_DONE, json.dumps(out, ensure_ascii=False, default=str)[
        :3000
    ]
    assert out["size_mm"] == [120.0, 48.0]  # 高度按原长宽比
    v = out["verdict"]
    assert v["protected_changes"] == [] and v["structure"]["missing"] == []
    assert v["budget"]["over"] == [] and 0 < v["budget"]["max_edge_shift_mm"] <= 0.15 * 120
    assert {a["gid"] for a in out["adjustments"]} == {"axes_0", "axes_1"}
    assert all(a["prop"] == "position" for a in out["adjustments"])
    assert _visible_inside(out["manifest"])
    # 两个子图仍并排、各自的图例仍在自己子图里
    man = out["manifest"]
    ax0 = next(e for e in man["elements"] if e["gid"] == "axes_0")["bbox"]
    ax1 = next(e for e in man["elements"] if e["gid"] == "axes_1")["bbox"]
    assert ax0[0] + ax0[2] <= ax1[0]
    # 最终文件：PDF 页面 120×48，PNG 像素 = 120/25.4×300
    assert out["acceptance"]["pdf"]["size_mm"] == [120.0, 48.0]
    assert (
        out["acceptance"]["png"]["size_ok"] is True and out["acceptance"]["png"]["dpi_ok"] is True
    )
    # 数据 / 颜色 / 范围一个都没动
    assert {p["prop"] for p in out["patches"]} == {"size_mm", "position"}


@pytest.mark.parametrize(
    "name, script, stem",
    [("tight", SCRIPT_TIGHT_ENGINE, "Tight"), ("constr", SCRIPT_CONSTRAINED, "Constrained")],
)
def test_persistent_layout_engines_stay_in_charge(tmp_path, name, script, stem):
    """脚本用了 layout="tight" / "constrained"：改尺寸后引擎自己重排，我们不加、不关、不叠加。"""
    project = _project(tmp_path, name, script, stem)
    sid = _open(project, stem)
    out = bridge.normalize_figure(
        sid, targets={"width_mm": 70}, formats=["pdf"], out_dir=str(tmp_path / "out")
    )
    assert out["exit"] == normalize.EXIT_DONE, json.dumps(out.get("verdict"), ensure_ascii=False)[
        :2000
    ]
    assert out["adjustments"] == []  # 没有一条 position 调整
    assert {p["prop"] for p in out["patches"]} == {"size_mm"}
    assert _visible_inside(out["manifest"])
    # 预览 == 导出：manifest 的尺寸就是文件的尺寸
    assert out["acceptance"]["pdf"]["size_mm"] == out["manifest"]["size_mm"]


def test_manual_layout_only_gets_bounded_margin_adjustments(tmp_path):
    project = _project(tmp_path, "manual", SCRIPT_MANUAL, "Manual")
    sid = _open(project, "Manual")
    out = bridge.normalize_figure(
        sid, targets={"width_mm": 70}, formats=["pdf"], out_dir=str(tmp_path / "out")
    )
    assert out["exit"] == normalize.EXIT_DONE, json.dumps(out.get("verdict"), ensure_ascii=False)[
        :2000
    ]
    assert out["verdict"]["budget"]["over"] == []
    assert all(a["prop"] in ("position", "loc") for a in out["adjustments"])
    assert _visible_inside(out["manifest"])
    # 导出不再二次重排：全量重放与热态一致
    replay = bridge.verify_replay(sid)
    assert replay["ok"], replay["divergence"][:5]


# ------------------------------ 检测与正确退出 --------------------------------
def test_new_collisions_are_detected_and_an_unsolvable_width_exits_cleanly(tmp_path):
    """150 → 80 mm、字号不许缩：装饰物装得下，但图例在自己子图的八个预设位置都压数据。
    退出，会话回到 B0，磁盘上什么都没多出来。"""
    project = _project(tmp_path, "two", SCRIPT_TWO, "Two")
    sid = _open(project, "Two")
    b0_hash = bridge.get_session(sid).patch_hash()
    b0_manifest_hash = normalize.manifest_hash(bridge.get_session(sid).manifest)
    out_dir = tmp_path / "out"
    out = bridge.normalize_figure(
        sid,
        targets={"width_mm": 80, "font_family": AVAILABLE_FONT, "min_font_pt": 8},
        formats=["pdf"],
        out_dir=str(out_dir),
    )
    assert out["exit"] == normalize.EXIT_CONSTRAINT_CONFLICT
    first = out["rounds"][0]["blocking"]
    assert {b["id"] for b in first} >= {"element-outside-figure", "text-over-axes"}
    # 原图那条 0.8 mm 的 ylabel 裁切在 80 mm 下**加重**了：与新增的一起挡，且分得开
    assert {b["bucket"] for b in first} == {"new", "worsened"}
    # 边距那一轮修掉了裁切；剩下的是图例压数据，且图例候选都试过了
    assert any(r.get("step") == "margins" for r in out["rounds"])
    assert any(str(r.get("step", "")).startswith("legend") for r in out["rounds"])
    # 剩下的全是图例装不进自己子图的那一族（压数据 / 压到自己的刻度与轴标题）
    left = out["verdict"]["blocking"]
    assert left and {b["id"] for b in left} <= {"legend-over-data", "text-overlap"}
    assert all(any(".legend" in g for g in b["gids"]) for b in left)
    # 没有一个预设位置让图例干干净净：一条图例调整都不许记成「已做的局部调整」
    # （换到一个还在压别的东西的位置不叫修好，只是把问题换了个地方）
    assert not any(a["prop"] == "loc" for a in out["adjustments"])
    assert out["rolled_back"] is True
    s = bridge.get_session(sid)
    assert s.patches == [] and s.patch_hash() == b0_hash
    assert normalize.manifest_hash(s.manifest) == b0_manifest_hash
    assert _files_in(out_dir) == []
    assert s.contract is None and s.normalized is None


def test_missing_font_is_refused_not_silently_substituted(tmp_path):
    project = _project(tmp_path, "two", SCRIPT_TWO, "Two")
    sid = _open(project, "Two")
    out_dir = tmp_path / "out"
    out = bridge.normalize_figure(
        sid, targets={"font_family": MISSING_FONT}, formats=["pdf"], out_dir=str(out_dir)
    )
    assert out["exit"] == normalize.EXIT_FONT_UNAVAILABLE
    assert out["verdict"]["targets_met"]["font_family"] is False
    assert out["verdict"]["font_unresolved"][0]["face"] == "DejaVu Sans"
    assert bridge.get_session(sid).patches == [] and _files_in(out_dir) == []
    text = "\n".join(server.normalize_report_lines(out))
    assert "没装它" in text and "不自动替代" in text


def test_hopeless_width_exits_without_touching_the_originals(tmp_path):
    project = _project(tmp_path, "two", SCRIPT_TWO, "Two")
    script_sha = _sha(project / "two.py")
    pdf_sha = _sha(project / "Two.pdf")
    sid = _open(project, "Two")
    out_dir = tmp_path / "out"
    out = bridge.normalize_figure(
        sid, targets={"width_mm": 40}, formats=["pdf"], out_dir=str(out_dir)
    )
    assert out["exit"] in (normalize.EXIT_CONSTRAINT_CONFLICT, normalize.EXIT_BUDGET_EXCEEDED)
    assert out["rolled_back"] is True and bridge.get_session(sid).patches == []
    assert _files_in(out_dir) == []
    assert _sha(project / "two.py") == script_sha and _sha(project / "Two.pdf") == pdf_sha
    text = "\n".join(server.normalize_report_lines(out))
    assert "没有擅自放宽任何约束" in text


def test_intentional_overlaps_are_not_interference(tmp_path):
    project = _project(tmp_path, "intent", SCRIPT_INTENT, "Intent")
    sid = _open(project, "Intent")
    man = bridge.get_session(sid).manifest
    certain = [i for i in interference.detect(man) if i["detail"].get("certain")]
    assert certain == [], certain
    # 缩一点也不该因为「曲线交叉 / 插图 / 箭头」被重排
    out = bridge.normalize_figure(
        sid, targets={"width_mm": 85}, formats=["pdf"], out_dir=str(tmp_path / "out")
    )
    assert out["exit"] == normalize.EXIT_DONE, json.dumps(out.get("verdict"), ensure_ascii=False)[
        :2000
    ]
    assert not any(a["prop"] == "loc" for a in out["adjustments"])


def test_pre_existing_clipping_is_kept_and_reported_not_worsened(tmp_path):
    project = _project(tmp_path, "pre", SCRIPT_PREEXISTING, "Pre")
    sid = _open(project, "Pre")
    b0 = bridge.run_preflight(sid)
    assert b0["blocking"] and any(i["id"] == "element-outside-figure" for i in b0["errors"])
    out = bridge.normalize_figure(
        sid, targets={"font_family": AVAILABLE_FONT}, formats=["pdf"], out_dir=str(tmp_path / "out")
    )
    assert out["exit"] == normalize.EXIT_DONE, json.dumps(out.get("verdict"), ensure_ascii=False)[
        :2000
    ]
    geo = out["verdict"]["issues"]["geometry_issues"]
    kept = [i["id"] for i in geo["unchanged"] + geo["improved"]]
    assert "element-outside-figure" in kept and geo["new"] == [] and geo["worsened"] == []
    assert "element-outside-figure" in out["kept_pre_existing"]
    assert out["adjustments"] == []  # 原有问题不顺手修
    text = "\n".join(server.normalize_report_lines(out))
    assert "原图已有" in text or "顺带改善" in text


# ------------------------------ 越权与事务 ------------------------------------
def test_edits_beyond_the_contract_require_the_user_and_invalidate_the_report(tmp_path):
    project = _project(tmp_path, "two", SCRIPT_TWO, "Two")
    sid = _open(project, "Two")
    out = bridge.normalize_figure(
        sid, targets={"width_mm": 120}, formats=["pdf"], out_dir=str(tmp_path / "out")
    )
    assert out["exit"] == normalize.EXIT_DONE
    committed = list(bridge.get_session(sid).patches)
    line = next(e["gid"] for e in out["manifest"]["elements"] if e["role"] == "line")
    sneaky = committed + [{"gid": line, "prop": "color", "value": "#ff0000"}]
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.apply_overrides(sid, sneaky)
    assert exc.value.code == normalize.EXIT_REQUIRES_AUTHORIZATION
    assert exc.value.extra["violations"] == [
        {"gid": line, "prop": "color", "change": "added", "value": "#ff0000"}
    ]
    assert bridge.get_session(sid).patches == committed  # 一个字节没动
    # 画布账本不带规范化 patch 的那种「静默还原」也被挡住
    with pytest.raises(bridge.BridgeError):
        bridge.apply_overrides(sid, [])
    # 原样重发放行；导出仍是通过验收的那一版
    bridge.apply_overrides(sid, committed)
    res = bridge.export(sid, formats=["pdf"], out_dir=str(tmp_path / "out2"), explicit_confirm=True)
    assert res["normalized"]["verified"] is True and res["acceptance"]["pdf"]["ok"] is True
    # 用户明确要求 → 合同解除、验收作废，导出如实说明
    ok = bridge.apply_overrides(sid, sneaky, user_authorized=True)
    assert ok["contract_released"] is True and bridge.get_session(sid).contract is None
    res = bridge.export(sid, formats=["pdf"], out_dir=str(tmp_path / "out3"), explicit_confirm=True)
    assert res["normalized"] is None or res["normalized"]["verified"] is False


def test_other_sessions_and_source_files_are_isolated(tmp_path):
    p1 = _project(tmp_path, "two", SCRIPT_TWO, "Two")
    p2 = _project(tmp_path, "manual", SCRIPT_MANUAL, "Manual")
    s1, s2 = _open(p1, "Two"), _open(p2, "Manual")
    other_hash = normalize.manifest_hash(bridge.get_session(s2).manifest)
    sha_py, sha_pdf = _sha(p1 / "two.py"), _sha(p1 / "Two.pdf")
    out = bridge.normalize_figure(
        s1, targets={"width_mm": 120}, formats=["pdf"], out_dir=str(tmp_path / "out")
    )
    assert out["exit"] == normalize.EXIT_DONE
    assert bridge.get_session(s2).patches == []
    assert normalize.manifest_hash(bridge.get_session(s2).manifest) == other_hash
    assert _sha(p1 / "two.py") == sha_py and _sha(p1 / "Two.pdf") == sha_pdf
    assert bridge.get_session(s2).contract is None


def test_repeating_the_same_request_does_not_drift(tmp_path):
    project = _project(tmp_path, "two", SCRIPT_TWO, "Two")
    sid = _open(project, "Two")
    first = bridge.normalize_figure(
        sid, targets={"width_mm": 120}, formats=["pdf"], out_dir=str(tmp_path / "out")
    )
    assert first["exit"] == normalize.EXIT_DONE
    patches = list(bridge.get_session(sid).patches)
    mhash = normalize.manifest_hash(bridge.get_session(sid).manifest)
    second = bridge.normalize_figure(
        sid, targets={"width_mm": 120}, formats=["pdf"], out_dir=str(tmp_path / "out")
    )
    assert second["exit"] in (normalize.EXIT_DONE, normalize.EXIT_NOTHING_TO_DO)
    assert bridge.get_session(sid).patches == patches
    assert normalize.manifest_hash(bridge.get_session(sid).manifest) == mhash
    assert len({(p["gid"], p["prop"]) for p in patches}) == len(patches)


def test_final_formats_are_verified_on_disk(tmp_path):
    project = _project(tmp_path, "tight", SCRIPT_TIGHT_ENGINE, "Tight")
    sid = _open(project, "Tight")
    out = bridge.normalize_figure(
        sid,
        targets={"width_mm": 75, "font_family": AVAILABLE_FONT},
        formats=["pdf", "svg", "png", "eps", "tiff"],
        dpi=300,
        out_dir=str(tmp_path / "out"),
    )
    assert out["exit"] == normalize.EXIT_DONE, json.dumps(
        out.get("verdict") or out.get("failed_files"), ensure_ascii=False
    )[:2000]
    acc = out["acceptance"]
    assert acc["pdf"]["size_ok"] is True and acc["pdf"]["font_ok"] is True
    assert (
        acc["svg"]["size_ok"] is True
        and acc["svg"]["viewbox_ok"] is True
        and acc["svg"]["font_ok"] is None
    )
    assert (
        acc["png"]["size_ok"] is True
        and acc["png"]["dpi_ok"] is True
        and acc["png"]["font_ok"] is None
    )
    assert acc["eps"]["size_ok"] is True
    assert acc["tiff"]["size_ok"] is True and acc["tiff"]["dpi_ok"] is None
    assert {f["format"] for f in out["files"] if f["status"] == "done"} == {
        "pdf",
        "svg",
        "png",
        "eps",
        "tiff",
    }


def test_original_artifact_mismatch_is_reported_not_replaced(tmp_path):
    project = _project(tmp_path, "cropped", SCRIPT_TIGHTBBOX, "Cropped")
    orig_sha = _sha(project / "Cropped.pdf")
    sid = _open(project, "Cropped")
    out = bridge.normalize_figure(
        sid, targets={"font_family": AVAILABLE_FONT}, formats=["pdf"], out_dir=str(tmp_path / "out")
    )
    facts = out["baseline"]["original_artifact"]
    assert facts["path"] == "Cropped.pdf" and facts["mismatch"] is True
    assert _sha(project / "Cropped.pdf") == orig_sha
    text = "\n".join(server.normalize_report_lines(out))
    assert "bbox_inches" in text and "未被替换" in text


# ------------------------------ 插件链路 --------------------------------------
class _Client:
    def __init__(self, roots: str, data_dir: str):
        env = {
            **os.environ,
            "TAVOTTO_MCP_ROOTS": roots,
            "TAVOTTO_DATA_DIR": data_dir,
            "PYTHONPATH": str(ROOT / "src"),
            "TAVOTTO_NO_TELEMETRY": "1",
        }
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "codex-plugin" / "mcp" / "server.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(ROOT),
        )
        self.n = 0

    def call(self, method, params=None):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError(
                "server 挂了:\n" + self.proc.stderr.read().decode("utf-8", "replace")[-4000:]
            )
        return json.loads(line.decode("utf-8"))

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.wait(timeout=120)


def test_the_tool_is_wired_through_the_real_stdio_server(tmp_path):
    """工具级集成：真 server 子进程、真 tools/call。**这不是经 Codex 宿主的端到端**——
    宿主那一段没有在这里跑，见 docs/acceptance。"""
    project = _project(tmp_path, "two", SCRIPT_TWO, "Two")
    c = _Client(str(tmp_path), str(tmp_path / "data"))
    try:
        c.call(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        )
        tools = {t["name"]: t for t in c.call("tools/list")["result"]["tools"]}
        assert "tavotto_normalize_figure" in tools
        schema = tools["tavotto_normalize_figure"]["inputSchema"]["properties"]
        assert set(normalize.TARGET_KEYS) <= set(schema)
        assert "user_authorized" in tools["tavotto_apply_overrides"]["inputSchema"]["properties"]
        opened = c.call(
            "tools/call",
            {"name": "tavotto_open_figure", "arguments": {"project_path": str(project)}},
        )["result"]
        sid = opened["structuredContent"]["session_id"]
        res = c.call(
            "tools/call",
            {
                "name": "tavotto_normalize_figure",
                "arguments": {
                    "session_id": sid,
                    "width_mm": 120,
                    "formats": ["pdf"],
                    "out_dir": str(tmp_path / "out"),
                },
            },
        )["result"]
        assert not res.get("isError"), res
        sc = res["structuredContent"]
        assert sc["exit"] == normalize.EXIT_DONE and sc["files"][0]["status"] == "done"
        assert "规范化完成" in res["content"][0]["text"]
        # 越权：结构化错误，code 稳定，不改任何东西
        line = next(e["gid"] for e in sc["manifest"]["elements"] if e["role"] == "line")
        bad = c.call(
            "tools/call",
            {
                "name": "tavotto_apply_overrides",
                "arguments": {
                    "session_id": sid,
                    "patches": sc["patches"] + [{"gid": line, "prop": "color", "value": "#ff0000"}],
                },
            },
        )["result"]
        assert (
            bad["isError"]
            and bad["structuredContent"]["code"] == normalize.EXIT_REQUIRES_AUTHORIZATION
        )
        c.call("tools/call", {"name": "tavotto_close_session", "arguments": {"session_id": sid}})
    finally:
        c.close()


def test_a_file_that_fails_acceptance_is_not_published_and_the_transaction_rolls_back(
    tmp_path, monkeypatch
):
    """最终产物验收不过：那一格以 acceptance_failed 进 partial、**不发布**；事务回到
    B0。用假的核验结果模拟「文件尺寸不对」——真产物尺寸永远对，只有这样才能证明
    这道门在控制流里，而不是摆设。"""
    from tavotto.engine import artifactcheck

    real = artifactcheck.check_file

    def fake(path, fmt, **kw):
        out = real(path, fmt, **kw)
        if fmt == "png":
            out.update({"ok": False, "size_ok": False, "failed": ["size_ok"]})
        return out

    monkeypatch.setattr(bridge.engine_artifactcheck, "check_file", fake)
    project = _project(tmp_path, "two", SCRIPT_TWO, "Two")
    sid = _open(project, "Two")
    out_dir = tmp_path / "out"
    out = bridge.normalize_figure(
        sid, targets={"width_mm": 120}, formats=["pdf", "png"], out_dir=str(out_dir)
    )
    assert out["exit"] == normalize.EXIT_ACCEPTANCE_FAILED and out["rolled_back"] is True
    png = next(f for f in out["failed_files"] if f["format"] == "png")
    assert png["status"] != "done" and png["error"]["code"] == "acceptance_failed"
    assert not list(out_dir.glob("*.png"))  # 没通过的那个文件没有落到最终目录
    # 同一次作业里已经发布的 PDF 与留档也撤回：它们描述的是一个已回退的状态
    assert _files_in(out_dir) == [] and out["files"] == [] and out["withdrawn_files"]
    assert bridge.get_session(sid).patches == [] and bridge.get_session(sid).normalized is None


def test_evidence_snapshots_locate_the_first_deviation(tmp_path):
    """§4.1 的证据：B0 / 只应用目标 / 每轮局部修复各一张位图，落在运行时数据目录——
    不进项目目录、不进导出目录，README 里写明它们是候选图不是交付物。"""
    project = _project(tmp_path, "two", SCRIPT_TWO, "Two")
    sid = _open(project, "Two")
    out = bridge.normalize_figure(
        sid,
        targets={"width_mm": 120},
        formats=["pdf"],
        out_dir=str(tmp_path / "out"),
        evidence=True,
    )
    assert out["exit"] == normalize.EXIT_DONE
    ev = Path(out["evidence_dir"])
    assert ev.is_relative_to(tmp_path / "data")
    names = out["evidence_files"]
    assert names[0] == "00-b0.png" and names[1] == "01-targets.png"
    assert any("margins" in n for n in names[2:])
    assert (
        (ev / "README.txt").read_text(encoding="utf-8").startswith("Tavotto 保留式规范化的阶段证据")
    )
    b0 = pdfbackend.probe_asset(ev / names[0], "raster")
    tg = pdfbackend.probe_asset(ev / names[1], "raster")
    assert abs(b0["px_h"] / b0["px_w"] - 60 / 150) < 0.01  # B0 仍是 150×60
    assert abs(tg["px_h"] / tg["px_w"] - 48 / 120) < 0.01  # 只应用目标之后 120×48
    assert not list(project.glob("*.png")) and not list((tmp_path / "out").glob("*.png"))


def test_a_figure_without_a_script_cannot_enter_the_transaction(tmp_path):
    """位图 / 没有脚本的产物进不了图内编辑，也就谈不上改字号字体：入口就说清（稳定 code），
    不从截图猜数据重画，也不把缩放位图说成「已改字号」。"""
    import pymupdf

    figures = tmp_path / "raw"
    figures.mkdir()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), False)
    pix.clear_with(255)
    pix.save(str(figures / "Photo.png"))
    (figures / "tavotto_registry.json").write_text('{"scripts": {}}', encoding="utf-8")
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.open_figure(str(figures), stem="Photo")
    assert exc.value.code in ("stem_not_parameterizable", "no_figure")
