"""导出里的「修改后的 .py」（ADR 0171）。

判据的主语分三段：

* 生成器（纯字节）：块外一个字节不动，编码 / 换行 / BOM / shebang 都不坏；
* 导出链路（真 worker + 真 `/api/export`）：原脚本逐字节不变，项目里不冒新文件，验证过不了就不交付，
  图片格式不受牵连；
* **用户拿到的那个文件**：不带 Tavotto 的纯 matplotlib 子进程跑它，画出来的 PDF 与导出的 PDF 逐像素一致。
"""

from __future__ import annotations

import ast
import codecs
import hashlib
import io
import json
import os
import subprocess
from pathlib import Path

import pymupdf
import pytest

from support.invariant_figures import ENTRY, LIBRARY, SCRIPT_NAME, STEMS
from support.overridesample import _sample_value
from tavotto.engine import (
    exportjob,
    exportreq,
    exportroute,
    figcapture,
    pool,
    project_watch,
    scriptadjust,
)

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

real_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

MAN = {
    "elements": [
        {"gid": "axes_0.title", "role": "title", "label": "标题", "editable": []},
        {"gid": "axes_0", "role": "axes", "label": "子图 1", "editable": []},
    ]
}
COLOR = {"gid": "axes_0.title", "prop": "color", "value": "#123456"}


def _build(src: bytes, patches=(COLOR,)) -> scriptadjust.Adjusted:
    return scriptadjust.build(src, "Fig1", list(patches), MAN, version="9.9")


def _without_block(data: bytes, encoding: str, newline: str) -> bytes:
    text = data.decode(encoding)
    start = text.index(scriptadjust.BEGIN)
    tail = scriptadjust.END + newline
    end = text.index(tail) + len(tail)
    two = newline * 2
    if text[start - len(two) : start] == two:
        start -= len(two)
    if text[end : end + len(two)] == two:
        end += len(two)
    return (text[:start] + text[end:]).encode(encoding)


# ---------------------------------------------------------------------- 字节
@pytest.mark.parametrize(
    "src, encoding, newline",
    [
        (b"import os\nx = 1\n", "utf-8", "\n"),
        (b"import os\r\nx = 1\r\n", "utf-8", "\r\n"),
        (codecs.BOM_UTF8 + b"import os\r\nx = 1\r\n", "utf-8-sig", "\r\n"),
        ("# -*- coding: gbk -*-\nimport os\n# 中文\nx = 1\n".encode("gbk"), "gbk", "\n"),
        (b"import os\nif 1:\n\tx = 1\n", "utf-8", "\n"),
        (b'"""doc"""\nfrom __future__ import annotations\nimport os\nx = 1\n', "utf-8", "\n"),
    ],
    ids=["lf", "crlf", "bom-crlf", "gbk", "tabs", "future"],
)
def test_nothing_outside_the_block_changes(src, encoding, newline):
    out = _build(src)
    assert out.written == 1
    ast.parse(out.data.decode(encoding))
    assert _without_block(out.data, encoding, newline) == src


def test_mixed_line_endings_survive_byte_for_byte():
    src = b"import os\r\nimport sys\nx = 1\r\ny = 2\n"
    assert _without_block(_build(src).data, "utf-8", "\r\n") == src  # 主导换行 = CRLF


def test_last_line_without_a_newline():
    src = b"import os"
    out = _build(src)
    ast.parse(out.data.decode())
    assert out.data.startswith(src + b"\n")


@pytest.mark.parametrize(
    "head",
    [b"#!/usr/bin/env python\n", b"# -*- coding: utf-8 -*-\n", b"#!/bin/py\n# coding: utf-8\n"],
)
def test_block_stays_below_shebang_and_coding_line(head):
    """它们只在头两行才算数：块插到文件头，声明就失效了（GBK 脚本当场读不出来）。"""
    out = _build(head + b"x = 1\n")
    assert out.data.startswith(head)
    assert out.data[len(head) :].lstrip(b"\n").startswith(scriptadjust.BEGIN.encode())


def test_block_goes_after_the_leading_imports():
    src = b"import os\nimport sys\n\nx = 1\n"
    data = _build(src).data
    assert (
        data.index(b"import sys") < data.index(scriptadjust.BEGIN.encode()) < data.index(b"x = 1")
    )


def test_value_the_file_encoding_cannot_hold_is_escaped():
    src = "# -*- coding: latin-1 -*-\nimport os\n".encode("latin-1")
    out = _build(src, [{"gid": "axes_0.title", "prop": "text", "value": "温度"}])
    assert b"\\u6e29\\u5ea6" in out.data
    ast.parse(out.data.decode("latin-1"))


def test_readable_value_stays_readable():
    out = _build(b"import os\n", [{"gid": "axes_0.title", "prop": "text", "value": "温度"}])
    assert "温度" in out.data.decode()


def test_second_export_keeps_the_first_block():
    """用户把导出的脚本换回去继续改：之前的调整不能丢，新的一段排在它后面、编号不撞。"""
    first = _build(b"import os\nx = 1\n").data
    second = _build(first, [{"gid": "axes_0", "prop": "facecolor", "value": "#eeeeee"}]).data
    text = second.decode()
    assert text.count(scriptadjust.BEGIN) == 2
    assert text.index("axes_0.title.color") < text.index("axes_0.facecolor")
    assert "}, 0, {})" in text and "}, 1, {})" in text


def test_frame_comes_from_the_manifest_and_only_when_active():
    frame = {"active": True, "savefig_in": [0.1, 0.2, 3.1, 2.2]}
    assert scriptadjust._frame_inches({"frame": frame}) == (0.1, 0.2, 3.1, 2.2)
    assert scriptadjust._frame_inches({}) is None
    assert scriptadjust._frame_inches({"frame": {**frame, "active": False}}) is None
    assert scriptadjust._frame_inches({"frame": {"active": True}}) is None


def _font_line(value, gid="axes_0.title", tail=("DejaVu Sans", "Noto Sans CJK SC")) -> str:
    man = {**MAN, "font_fallback_tail": list(tail)}
    man["elements"] = [
        *MAN["elements"],
        {"gid": "axes_0.xticks", "role": "ticks", "label": "x 刻度", "editable": []},
    ]
    p = {"gid": gid, "prop": "fontfamily", "value": value}
    out = scriptadjust.build(b"import os\n", "Fig1", [p], man, version="9.9")
    assert out.written == 1
    return out.data.decode()


def test_font_chain_carries_the_machine_fallback_tail():
    """只设一个名字时缺的字形画成方框：族链要和引擎一样带上本机的回退尾巴。"""
    text = _font_line("Times New Roman")
    assert "['Times New Roman', 'DejaVu Sans', 'Noto Sans CJK SC'], 'Times New Roman')" in text


def test_font_already_in_the_tail_is_not_repeated():
    assert "['DejaVu Sans', 'Noto Sans CJK SC'], 'DejaVu Sans')" in _font_line("DejaVu Sans")


def test_font_list_value_uses_its_first_family():
    assert "'Arial')" in _font_line(["Arial", "Helvetica"])


def test_font_selection_keeps_primary_and_fallback():
    text = _font_line({"primary": "Arial", "fallback": "SimSun"})
    assert "_font_pick(_ax(fig, 0).title, 'Arial', 'SimSun')" in text


def test_tick_fonts_go_through_tick_params():
    text = _font_line("Arial", gid="axes_0.xticks")
    assert "_tick_font(_TickSet(_ax(fig, 0), 'x'), ['Arial'" in text
    assert "labelfontfamily=" in text


def test_block_only_carries_the_helpers_it_uses():
    out = _build(b"import os\n").data.decode()
    assert "_legend_refont" not in out and "_title_to" not in out
    assert "def _install" in out


def test_block_never_imports_tavotto():
    tree = ast.parse(_build(b"import os\n").data.decode())
    roots = {
        (n.names[0].name if isinstance(n, ast.Import) else (n.module or "")).split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
    }
    assert "tavotto" not in roots


def test_block_runtime_is_ascii():
    """任何 PEP 263 编码都放得下；非 ASCII 只可能来自用户自己的值。"""
    assert scriptadjust._RUNTIME.isascii()


def test_unreadable_script_is_refused_not_guessed():
    with pytest.raises(scriptadjust.ScriptAdjustError) as e:
        _build(b"# coding: utf-8\nx = '\xff'\n")
    assert e.value.code == "script_export_unreadable"
    with pytest.raises(scriptadjust.ScriptAdjustError):
        _build(b"def (:\n", [])


def test_duplicate_patch_keeps_the_last_value():
    """与 `effectiveOverride` 同一口径：重复 (gid, prop) 取最后一条。"""
    out = _build(b"import os\n", [COLOR, {**COLOR, "value": "#abcdef"}])
    assert out.written == 1
    text = out.data.decode()
    assert "#abcdef" in text and "#123456" not in text


def test_unwritable_override_is_reported_not_guessed():
    p = {"gid": "axes_0", "prop": "aspect", "value": "equal"}
    out = _build(b"import os\n", [COLOR, p])
    assert out.written == 1
    assert out.residual == [p]
    assert out.skipped == [{"gid": "axes_0", "prop": "aspect", "label": "子图 1"}]


def test_nothing_writable_returns_the_original_bytes():
    src = b"import os\n"
    p = {"gid": "axes_0", "prop": "aspect", "value": "equal"}
    assert _build(src, [p]).data == src


def test_narrow_entry_stays_with_its_residual_broadcast():
    """留作 override 的广播条目重放时排在窄条目前面，窄条目写进脚本就被它盖掉（ADR 0094 §五.3）。"""
    man = {
        "elements": [
            {"gid": "axes_0.legend", "role": "legend", "editable": []},
            {"gid": "axes_0.legend.texts_0", "role": "legend_text", "editable": []},
        ]
    }
    broad = {"gid": "axes_0.legend", "prop": "fontsize", "value": 7}  # 缺整份图例参数 → 写不了
    narrow = {"gid": "axes_0.legend.texts_0", "prop": "fontsize", "value": 9}
    man["elements"][0]["editable"] = None  # 让图例字号这一条走进 residual
    out = scriptadjust.build(b"import os\n", "Fig1", [broad, narrow], man, version="9.9")
    assert out.written == 0
    assert {p["gid"] for p in out.residual} == {broad["gid"], narrow["gid"]}


def test_block_stem_matches_the_engine():
    """严格同源对：块里的 `_stem` ↔ `figcapture.savefig_stem`。两边不一致，调整就落不到那张图上。"""
    tree = ast.parse(scriptadjust._RUNTIME)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_stem")
    ns: dict = {"_os": os}
    exec(compile(ast.Module([node], []), "<block>", "exec"), ns)  # noqa: S102
    for fname in (
        "Fig1.pdf",
        "a/b/Fig 1.png",
        "a.tar.gz",
        "noext",
        Path("out") / "f.pdf",
        b"x.pdf",
    ):
        assert ns["_stem"](fname) == figcapture.savefig_stem(fname), fname
    assert ns["_stem"](io.BytesIO()) == figcapture.savefig_stem(io.BytesIO()) == ""


# ---------------------------------------------------------------------- 表态
def _handler_keys() -> set[tuple[str, str]]:
    code = "import json\nimport overrides as o\nprint(json.dumps(sorted(o.HANDLERS)))\n"
    # overrides.py 只在 worker 进程里按平铺模块 import（engine/ 目录进 sys.path）
    env = {**os.environ, "PYTHONPATH": str(Path(exportjob.__file__).parent)}
    out = subprocess.run(
        [WORKER_PY, "-c", code],
        capture_output=True,
        encoding="utf-8",
        check=True,
        env=env,
    )
    return {tuple(k) for k in json.loads(out.stdout.strip().splitlines()[-1])}


@real_worker
def test_every_override_key_has_taken_a_side():
    """`HANDLERS` 新增键忘了表态就红：要么写得进脚本，要么明说不写。"""
    keys = _handler_keys()
    refused = {(c, p) for c, props in scriptadjust.NOT_WRITABLE.items() for p in props.split()}
    assert not (keys - set(scriptadjust.WRITES) - refused)
    assert not (set(scriptadjust.WRITES) & refused)
    assert not (set(scriptadjust.WRITES) - keys), "表里有引擎不认识的键"
    assert not (refused - keys), "NOT_WRITABLE 里有引擎不认识的键"


#: `visible` 关掉之后别的字段量不到；翻转坐标轴 + 改图像 + 改色条标签 va 三者同时才会偏（真出现时是
#: `script_export_diverged`，不交付），翻转单独一条用例（`test_inverted_axes_replay_identically`）。
#: 对数轴 + 改刻度字体同理：`tick_params` 没有公式字体的键，`10^4` 这种标签的字体只盖得住已有的那几条。
_NOT_IN_THE_SWEEP = {"visible", "invert_x", "invert_y", "xscale", "yscale"}
#: 3D 图逐字段单独都等价，所有字段一次全改才超像素阈值（max 22 / 255），同样由验证拦下。
_FLAT_STEMS = [s for s in STEMS if not s.startswith("Inv3D")]


@real_worker
@pytest.mark.parametrize("stem", _FLAT_STEMS)
def test_every_writable_field_replays_identically(tmp_path, stem):
    """夹具里每张图上所有写得进脚本的字段**一起**改，新脚本重放必须与热态几何 + 像素一致。"""
    from tavotto import app

    (tmp_path / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    worker = pool.one_shot(SCRIPT_NAME, str(tmp_path), ENTRY)
    try:
        worker.ensure_built()
        man = worker.override(stem, [])["manifest"]
        patches = []
        for el in man["elements"]:
            cls = scriptadjust._ROLE_CLS.get(el.get("role"))
            for field in el.get("editable") or []:
                if (cls, field["prop"]) not in scriptadjust.WRITES:
                    continue
                value = _sample_value(field)
                if value is not None and field["prop"] not in _NOT_IN_THE_SWEEP:
                    patches.append({"gid": el["gid"], "prop": field["prop"], "value": value})
        if not patches:
            pytest.skip("这张图上没有写得进脚本的字段")
        assert not (worker.override(stem, patches).get("warnings") or [])
        hot = app._hot_manifest(worker, stem, patches)
        src = (tmp_path / SCRIPT_NAME).read_bytes()
        adjusted = scriptadjust.build(src, stem, patches, hot, version="9.9")
        assert adjusted.written
        stage = tmp_path / "_stage"
        stage.mkdir()
        target = stage / "edited.py"
        target.write_bytes(adjusted.data)
        assert app._verify_edited_script(worker, stem, target, adjusted, hot, stage) == []
    finally:
        pool.discard(worker)


# ---------------------------------------------------------------------- 请求
def _spec(**over) -> dict:
    return {
        "scope": "original",
        "filename": "Fig1",
        "formats": ["pdf"],
        "original": {"figure_id": "Fig1.pdf", "source_kind": "vector", "overrides": []},
        **over,
    }


def test_request_may_ask_only_for_the_script():
    req = exportreq.normalize(_spec(formats=[], include_edited_script=True))
    assert req.include_script and req.formats == ()


def test_request_without_formats_or_script_is_still_refused():
    with pytest.raises(exportreq.ExportRequestError) as e:
        exportreq.normalize(_spec(formats=[]))
    assert e.value.code == "no_format"


def test_canvas_export_cannot_carry_a_script():
    with pytest.raises(exportreq.ExportRequestError) as e:
        exportreq.normalize(
            {
                "scope": "canvas",
                "filename": "x",
                "formats": ["pdf"],
                "include_edited_script": True,
                "canvas": {"page_w_mm": 10, "page_h_mm": 10, "objects": []},
            }
        )
    assert e.value.code == "script_export_not_for_canvas"


def test_legacy_requests_do_not_get_a_script():
    req = exportreq.normalize(
        {
            "stem": "x",
            "formats": ["pdf"],
            "page_w_mm": 1,
            "page_h_mm": 1,
            "include_edited_script": 1,
        }
    )
    assert not req.include_script


@pytest.mark.parametrize(
    "scope, has_script, how",
    [
        ("original", True, exportroute.HOW_REDRAW),
        ("original", False, exportroute.HOW_UNAVAILABLE),
        ("canvas", True, exportroute.HOW_UNAVAILABLE),
    ],
)
def test_route_offers_the_script_regardless_of_formats(scope, has_script, how):
    route = exportroute.panel_route(
        scope=scope, formats=["pdf"], file_kind="pdf", overrides=[], has_script=has_script
    )
    assert route["offers"]["script"]["how"] == how


# ---------------------------------------------------------------------- 真链路
SCRIPT = """\
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    with open("points.csv", encoding="utf-8") as fh:
        ys = [float(v) for v in fh.read().split(",")]
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot(range(len(ys)), ys, label="signal")
    ax.set_xlabel("Time (s)")
    ax.set_title("Raw")
    ax.legend(loc="upper left")
    fig.subplots_adjust(left=0.2, bottom=0.22)
    fig.savefig("Fig1.pdf")


if __name__ == "__main__":
    main()
"""
REGISTRY = json.dumps(
    {
        "version": 1,
        "scripts": {"fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]}},
    }
)
EDITS = [
    {"gid": "axes_0.lines_0", "prop": "color", "value": "#d6278f"},
    {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 3.0},
    {"gid": "axes_0.title", "prop": "fontsize", "value": 15},
    {"gid": "axes_0.legend", "prop": "fontsize", "value": 7},
]


@pytest.fixture
def project(tmp_path, monkeypatch):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig1.py").write_text(SCRIPT, encoding="utf-8")
    (figs / "points.csv").write_text("0,9,2,10,4,7.5", encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(REGISTRY, encoding="utf-8")
    subprocess.run([WORKER_PY, "fig1.py"], cwd=figs, check=True)
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    try:
        yield m.app.test_client(), figs
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()


def _export(client, patches, **over):
    spec = {
        "scope": "original",
        "filename": "Fig1",
        "formats": ["pdf"],
        "include_edited_script": True,
        "overwrite": "replace",
        "original": {"figure_id": "Fig1.pdf", "source_kind": "vector", "overrides": patches},
        **over,
    }
    return client.post("/api/export", json=spec).get_json()


def _script_output(body) -> dict:
    return next(o for o in body["outputs"] if o["format"] == exportjob.SCRIPT_KEY)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(root: Path) -> set[str]:
    return {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and "tavottofile" not in p.parts and "__pycache__" not in p.parts
    }


def _largest_channel_diff(a: Path, b: Path) -> int:
    """两份 PDF 渲染后逐字节比，最大的一处差。"""
    with pymupdf.open(a) as da, pymupdf.open(b) as db:
        pa, pb = da[0].get_pixmap(dpi=150), db[0].get_pixmap(dpi=150)
    assert (pa.w, pa.h) == (pb.w, pb.h)
    return max(abs(x - y) for x, y in zip(pa.samples, pb.samples))


#: 导出按 600 dpi 重画、脚本的 savefig 用默认 dpi：矢量里坐标框的直线吸附差一点点，
#: 抗锯齿边缘差到 20 / 255 上下（没有修改的图导出是原件拷贝，逐字节相同）。这条线上下是两回事：
#: 换一种颜色、挪一段文字都远超它。
_SNAP_NOISE = 32


@real_worker
def test_plain_matplotlib_reproduces_the_exported_figure(project, tmp_path):
    """用户拿到 `Fig1_edited.py`，放到原脚本旁边，纯 matplotlib 跑：画出来就是导出的那张。"""
    client, figs = project
    before_tree, before_sha = _tree(figs), _sha(figs / "fig1.py")
    body = _export(client, EDITS)
    assert body["status"] == "done", body
    out = _script_output(body)
    assert out["name"] == "Fig1_edited.py"
    report = out["script_report"]
    assert report["written"] == len(EDITS) and report["skipped"] == []
    # 原脚本与项目目录零改动
    assert _sha(figs / "fig1.py") == before_sha and _tree(figs) == before_tree

    exported = Path(body["export_dir"])
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "points.csv").write_text("0,9,2,10,4,7.5", encoding="utf-8")
    (run_dir / "edited.py").write_bytes((exported / "Fig1_edited.py").read_bytes())
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    subprocess.run([WORKER_PY, "edited.py"], cwd=run_dir, check=True, env=env)
    assert _largest_channel_diff(run_dir / "Fig1.pdf", exported / "Fig1.pdf") <= _SNAP_NOISE
    # 判据是活的：这份导出与没改过的原图差得远，不是两张白纸在比
    assert _largest_channel_diff(figs / "Fig1.pdf", exported / "Fig1.pdf") > _SNAP_NOISE


@real_worker
def test_tight_crop_keeps_the_page_the_editor_shows(project, tmp_path):
    """`bbox_inches="tight"` 量的是脚本画出来的样子：字号变大不能让页面跟着重裁。

    Tavotto 的图幅在脚本跑完时定下，块要是先改再裁，改字号这种最常见的编辑就 `script_export_diverged`。
    """
    client, figs = project
    (figs / "fig1.py").write_text(
        SCRIPT.replace('fig.savefig("Fig1.pdf")', 'fig.savefig("Fig1.pdf", bbox_inches="tight")'),
        encoding="utf-8",
    )
    subprocess.run([WORKER_PY, "fig1.py"], cwd=figs, check=True)
    pool.shutdown_all(figures_dir=str(figs), wait=True)
    edit = [{"gid": "axes_0.xlabel", "prop": "fontsize", "value": 16}]
    body = _export(client, edit)
    assert body["status"] == "done", body
    exported = Path(body["export_dir"])
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "points.csv").write_text("0,9,2,10,4,7.5", encoding="utf-8")
    (run_dir / "edited.py").write_bytes((exported / "Fig1_edited.py").read_bytes())
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    subprocess.run([WORKER_PY, "edited.py"], cwd=run_dir, check=True, env=env)
    with pymupdf.open(run_dir / "Fig1.pdf") as a, pymupdf.open(exported / "Fig1.pdf") as b:
        assert abs(a[0].rect.height - b[0].rect.height) < 0.1
        assert abs(a[0].rect.width - b[0].rect.width) < 0.1


@real_worker
@pytest.mark.parametrize(
    "patch",
    [
        {"gid": "axes_0.xlabel", "prop": "pos_frac", "value": [0.4, 0.9]},
        {"gid": "axes_0.title", "prop": "pos_frac", "value": [0.35, 0.08]},
        {"gid": "axes_0.legend", "prop": "loc_frac", "value": [0.5, 0.3]},
    ],
    ids=["axis-label", "title", "legend"],
)
def test_moves_on_a_tight_cropped_figure_land_where_the_editor_shows(project, tmp_path, patch):
    """`bbox_inches="tight"` 的图，界面上的位置是**裁出来那一页**里的比例（ADR 0098）：
    块按整张 figsize 换算就差出裁掉的那一圈，挪个文字就 `script_export_diverged`（一份用户反馈）。"""
    client, figs = project
    (figs / "fig1.py").write_text(
        SCRIPT.replace('fig.savefig("Fig1.pdf")', 'fig.savefig("Fig1.pdf", bbox_inches="tight")'),
        encoding="utf-8",
    )
    subprocess.run([WORKER_PY, "fig1.py"], cwd=figs, check=True)
    pool.shutdown_all(figures_dir=str(figs), wait=True)
    body = _export(client, [patch])
    assert body["status"] == "done", body
    assert _script_output(body)["script_report"]["written"] == 1
    exported = Path(body["export_dir"])
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "points.csv").write_text("0,9,2,10,4,7.5", encoding="utf-8")
    (run_dir / "edited.py").write_bytes((exported / "Fig1_edited.py").read_bytes())
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    subprocess.run([WORKER_PY, "edited.py"], cwd=run_dir, check=True, env=env)
    assert _largest_channel_diff(run_dir / "Fig1.pdf", exported / "Fig1.pdf") <= _SNAP_NOISE


@real_worker
@pytest.mark.parametrize(
    "patch",
    [
        {"gid": "axes_0.title", "prop": "fontfamily", "value": "serif"},
        {"gid": "axes_0.xlabel", "prop": "fontfamily", "value": "DejaVu Serif"},
        {"gid": "axes_0.xticks", "prop": "fontfamily", "value": "serif"},
        {"gid": "axes_0.legend.texts_0", "prop": "fontfamily", "value": "serif"},
        {
            "gid": "axes_0.title",
            "prop": "fontfamily",
            "value": {"primary": "DejaVu Serif", "fallback": "DejaVu Sans"},
        },
    ],
    ids=["title", "axis-label", "ticks", "legend-entry", "selection"],
)
def test_font_edits_replay_identically(project, patch):
    client, _figs = project
    body = _export(client, [patch], formats=[])
    assert body["status"] == "done", body
    assert _script_output(body)["script_report"]["written"] == 1


@real_worker
def test_inverted_axes_replay_identically(project):
    client, _figs = project
    body = _export(client, [{"gid": "axes_0", "prop": "invert_y", "value": True}], formats=[])
    assert body["status"] == "done", body


@real_worker
def test_script_only_export(project):
    client, _figs = project
    body = _export(client, EDITS[:1], formats=[])
    assert body["status"] == "done", body
    assert [o["format"] for o in body["outputs"]] == [exportjob.SCRIPT_KEY]


@real_worker
def test_unedited_figure_exports_a_plain_copy(project):
    client, figs = project
    body = _export(client, [])
    out = _script_output(body)
    assert out["script_report"]["written"] == 0
    assert (Path(body["export_dir"]) / out["name"]).read_bytes() == (figs / "fig1.py").read_bytes()


@real_worker
def test_unwritable_edit_is_listed_and_the_rest_still_ships(project):
    client, _figs = project
    aspect = {"gid": "axes_0", "prop": "aspect", "value": "equal"}
    body = _export(client, [*EDITS[:1], aspect])
    assert body["status"] == "done", body
    report = _script_output(body)["script_report"]
    assert report["written"] == 1
    assert [(s["gid"], s["prop"]) for s in report["skipped"]] == [("axes_0", "aspect")]


@real_worker
def test_a_wrong_translation_never_ships(project, monkeypatch):
    """反证验证是活的：把翻译改错，脚本不交付，PDF 照常交付。"""
    client, _figs = project
    monkeypatch.setitem(scriptadjust.WRITES, ("line", "color"), "{o}.set_color('#00ff00')")
    body = _export(client, EDITS[:1])
    assert body["status"] == "partial", body
    out = _script_output(body)
    assert out["status"] == "failed" and out["error"]["code"] == "script_export_diverged"
    assert not (Path(body["export_dir"]) / "Fig1_edited.py").exists()
    assert next(o for o in body["outputs"] if o["format"] == "pdf")["status"] == "done"


SHOW_ONLY = """\
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

for k in range(2):
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([0, 1, 2], [k, 2, 1], label="signal")
    ax.set_title("Raw")
plt.show()
"""


def _run_exported(path: Path, tmp_path: Path, trailer: str) -> str:
    run_dir = tmp_path / "run-show"
    run_dir.mkdir()
    (run_dir / "edited.py").write_text(path.read_text(encoding="utf-8") + trailer, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    out = subprocess.run(
        [WORKER_PY, "edited.py"],
        cwd=run_dir,
        check=True,
        env=env,
        capture_output=True,
        encoding="utf-8",
    )
    return out.stdout


@real_worker
@pytest.mark.parametrize("which", [0, 1], ids=["first", "second"])
def test_show_only_figure_is_edited_at_plt_show(tmp_path, which):
    """没有 savefig 的图：块挂在 `plt.show` 上，按引擎给 stem 的顺序认图，改的是对的那一张。"""
    from tavotto import app

    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "only.py").write_text(SHOW_ONLY, encoding="utf-8")
    worker = pool.one_shot("only.py", str(figs), "__main__")
    stem = "only" if which == 0 else "only-2"
    patches = [{"gid": "axes_0.lines_0", "prop": "color", "value": "#d6278f"}]
    try:
        worker.override(stem, patches)
        assert app._shown_index(worker, stem) == which
        hot = app._hot_manifest(worker, stem, patches)
        src = (figs / "only.py").read_bytes()
        adjusted = scriptadjust.build(src, stem, patches, hot, version="9.9", shown_index=which)
        assert adjusted.written == 1
        stage = tmp_path / "stage"
        stage.mkdir()
        target = stage / "edited.py"
        target.write_bytes(adjusted.data)
        assert app._verify_edited_script(worker, stem, target, adjusted, hot, stage) == []
    finally:
        pool.discard(worker)
    out = _run_exported(
        target,
        tmp_path,
        "\nfor n in plt.get_fignums():\n"
        "    print(matplotlib.colors.to_hex(plt.figure(n).axes[0].lines[0].get_color()))\n",
    )
    colors = out.split()
    assert colors[which] == "#d6278f"
    assert colors[1 - which] != "#d6278f", "另一张图不该被改"


@real_worker
def test_second_export_of_a_show_only_script_applies_both_blocks(tmp_path):
    """Codex #1790 P2：两段块登记同一个 stem，`plt.show` 时第二段不能被第一段的防重入挡掉。"""
    src = SHOW_ONLY.replace("range(2)", "range(1)").encode()
    man = {
        "elements": [
            {"gid": "axes_0.title", "role": "title", "label": "标题", "editable": []},
        ]
    }
    color = {"gid": "axes_0.title", "prop": "color", "value": "#123456"}
    size = {"gid": "axes_0.title", "prop": "fontsize", "value": 21}
    first = scriptadjust.build(src, "only", [color], man, version="9.9", shown_index=0).data
    second = scriptadjust.build(first, "only", [size], man, version="9.9", shown_index=0).data
    path = tmp_path / "edited.py"
    path.write_bytes(second)
    out = _run_exported(
        path,
        tmp_path,
        "\nt = plt.gcf().axes[0].title\n"
        "print(matplotlib.colors.to_hex(t.get_color()), t.get_fontsize())\n",
    )
    assert out.split() == ["#123456", "21.0"]


def test_show_hook_is_only_in_blocks_that_need_it():
    assert "_watch_show" not in _build(b"import os\n").data.decode()
    assert "matplotlib.pyplot" not in _build(b"import os\n").data.decode()
    data = scriptadjust.build(b"import os\n", "Fig1", [COLOR], MAN, version="9.9", shown_index=1)
    assert "_watch_show(0, {1: 'Fig1'})" in data.data.decode()


@real_worker
def test_figure_neither_saved_nor_shown_says_why(project):
    client, figs = project
    (figs / "fig1.py").write_text(
        SCRIPT.replace('fig.savefig("Fig1.pdf")', "pass"), encoding="utf-8"
    )
    pool.shutdown_all(figures_dir=str(figs), wait=True)
    body = _export(client, EDITS[:1], formats=[])
    assert _script_output(body)["error"]["code"] in (
        "script_export_no_savefig",
        "script_export_run_failed",
    )


@real_worker
def test_export_never_overwrites_the_source_script(project, monkeypatch):
    """Codex #1790 P1：脚本本身就叫 `Fig1_edited.py`、导出目录就是脚本目录时，覆盖不许落到它头上。"""
    from tavotto import app as m

    client, figs = project
    source = figs / "fig1.py"
    monkeypatch.setattr(m, "project_export_dir", lambda ctx=None: figs)
    alias = figs / "Fig1_edited.py"
    before = source.read_bytes()
    os.link(source, alias)  # 硬链接：同一个文件换了名字，字符串比路径比不出来
    try:
        body = _export(client, EDITS[:1], formats=[])
    finally:
        keep = alias.read_bytes()
        alias.unlink()
    assert _script_output(body)["error"]["code"] == "script_export_same_file"
    assert keep == before and source.read_bytes() == before


@real_worker
def test_lost_pixel_baseline_is_not_a_pass(project, monkeypatch):
    """Codex #1790 P1：热会话中途重开时像素没有比过，「没比」不算过。"""
    from tavotto import app as m

    client, _figs = project
    monkeypatch.setattr(m, "_replay_pixel_diff", lambda *a: (None, "hot_rebuilt"))
    body = _export(client, EDITS[:1], formats=[])
    assert _script_output(body)["error"]["code"] == "script_export_diverged"


@real_worker
def test_existing_file_goes_through_the_overwrite_policy(project):
    client, _figs = project
    first = _export(client, EDITS[:1], formats=[])
    export_dir = Path(first["export_dir"])
    body = _export(client, EDITS[:1], formats=[], overwrite="ask")
    assert body["status"] == "conflict" and body["conflicts"] == ["Fig1_edited.py"]
    body = _export(client, EDITS[:1], formats=[], overwrite="rename")
    assert _script_output(body)["name"] == "Fig1 (2)_edited.py"
    assert (export_dir / "Fig1 (2)_edited.py").is_file()
