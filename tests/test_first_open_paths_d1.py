"""QA 2026-09-24 §4（PATH-01…09）：工作目录、数据来源与科研数值——D1 夹具经**真实公共入口**。

与 `tests/test_foundation_first_open.py`（FO01/02/03/07）同一套装置（`python -m tavotto` + 会话认证、
空用户配置、服务进程的环境里**没有** `TAVOTTO_WORKER_PYTHON`、harness 不 chdir、不预设 cwd 模式），
补上那几条用例量不到的维度：

* **全点序列**而不是 ylim：D1 的正确数据 `y=[0,9,2,10]` 与同名干扰 `y=[0,2,9,10]` 极值、均值、ylim
  完全相同（用例开头先把这个前提断言一遍）。图内序列由 manifest 里那条线的 `geometry`（图幅分数、
  原点左上）+ axes 框 + ylim **在测试侧**换算回数据坐标（不调产品的坐标换算）；输入身份看回执
  `inputs.files` 的 sha256；导出物用纯标准库读 PDF 内容流里那条 4 点折线，比仿射不变的归一化序列。
* 重放（`/api/engine/invalidate` 后新一代 worker）、重启服务后的「编辑重开」、导出。
* 默认沙盒的盲区（`exists` / `glob` / `listdir`）如实记成引导型安全停止，不计入自动成功。

夹具由 `docs/qa/2026-09-24/path/repro/make_d1.py` 同形状现造（这里内联一份，测试不依赖 docs/）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from support import foundation_app as fa, pdfread

try:
    from tavotto.engine import pool as _pool

    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001 — 没有科学栈就 skip，而 skip 在 CI 校验步里是红
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

X = [0, 1, 2, 3]
TRUE_Y = [0, 9, 2, 10]
DECOY_Y = [0, 2, 9, 10]
EXTERNAL_Y = [1, 8, 3, 7]

# ---------------------------------------------------------------- 夹具

ENTRY = '''"""D1 入口：站在项目根跑 `python scripts/entry.py` 才读得到 data/points.csv。"""
import csv

import labmod
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

xs, ys = [], []
with open("data/points.csv", encoding="utf-8", newline="") as fh:
    for row in csv.DictReader(fh):
        xs.append(float(row["x"]))
        ys.append(labmod.passthrough(float(row["y"])))
fig, ax = plt.subplots(figsize=(3.2, 2.4))
ax.plot(xs, ys, "o-")
ax.set_title(labmod.TITLE)
fig.savefig("entry.pdf")
'''

LABMOD = '''"""D1 本地模块：不是 PyPI 包。"""
TITLE = "D1 labmod"


def passthrough(v):
    return v + 0.0
'''


def _csv(ys: list[int]) -> bytes:
    return ("x,y\n" + "".join(f"{x},{y}\n" for x, y in zip(X, ys))).encode("utf-8")


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _plot_script(read_expr: str, out: str, *, pre: str = "", title: str = "'D1'") -> str:
    """一份同形状的小脚本：`read_expr` 求出 (xs, ys)，画 4 点折线，存 `out`。"""
    return (
        "import csv, glob, os, sys\n"
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "import numpy as np\n"
        f"{pre}"
        "def _read_csv(path):\n"
        "    xs, ys = [], []\n"
        "    with open(path, encoding='utf-8', newline='') as fh:\n"
        "        for row in csv.DictReader(fh):\n"
        "            xs.append(float(row['x'])); ys.append(float(row['y']))\n"
        "    return xs, ys\n"
        f"xs, ys = {read_expr}\n"
        "fig, ax = plt.subplots(figsize=(3.2, 2.4))\n"
        "ax.plot(xs, ys, 'o-')\n"
        f"ax.set_title({title})\n"
        f"fig.savefig({out!r})\n"
    )


def _native(project: Path, script: str, tmp: Path, *, cwd: Path | None = None) -> None:
    """用户在终端里跑过一次（磁盘上有原件，面板列表扫的是磁盘产物）。不是产品路径。"""
    env = {"PATH": "/usr/bin:/bin", "MPLCONFIGDIR": str(tmp / "mpl"), "MPLBACKEND": "Agg"}
    for name in ("LD_LIBRARY_PATH", "SYSTEMROOT", "TEMP", "TMP"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    proc = subprocess.run(
        [WORKER_PY, script],
        cwd=cwd or project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    for cache in list((cwd or project).rglob("__pycache__")):
        shutil.rmtree(cache)


def _d1(tmp: Path, *, decoy: bool = False, name: str = "paper") -> tuple[Path, dict]:
    paper = tmp / name
    (paper / "scripts").mkdir(parents=True)
    (paper / "data").mkdir()
    (paper / "scripts" / "entry.py").write_text(ENTRY, encoding="utf-8", newline="\n")
    (paper / "scripts" / "labmod.py").write_text(LABMOD, encoding="utf-8", newline="\n")
    (paper / "data" / "points.csv").write_bytes(_csv(TRUE_Y))
    truth = {"true_sha": _sha256(_csv(TRUE_Y)), "decoy_sha": _sha256(_csv(DECOY_Y))}
    if decoy:
        (paper / "scripts" / "data").mkdir()
        (paper / "scripts" / "data" / "points.csv").write_bytes(_csv(DECOY_Y))
    return paper, truth


# ---------------------------------------------------------------- 独立的尺子


def test_the_decoy_is_indistinguishable_by_extrema_mean_and_ylim():
    """D1 的前提：只有全序列 / hash 能分。极值、均值、自动 ylim 全部相同。"""
    assert TRUE_Y != DECOY_Y
    assert (min(TRUE_Y), max(TRUE_Y)) == (min(DECOY_Y), max(DECOY_Y))
    assert sum(TRUE_Y) == sum(DECOY_Y)
    assert sorted(TRUE_Y) == sorted(DECOY_Y)
    assert _normalized(TRUE_Y) != _normalized(DECOY_Y)


def _normalized(ys: list[float]) -> list[float]:
    """仿射不变的形状：(y_i − y_0) / (y_last − y_0)。PDF 点坐标与数据坐标之间只差一个仿射。"""
    span = ys[-1] - ys[0]
    return [(y - ys[0]) / span for y in ys]


def _manifest_line(render: dict) -> dict:
    lines = [e for e in render["manifest"]["elements"] if e["role"] == "line"]
    assert len(lines) == 1, [e["gid"] for e in lines]
    return lines[0]


def _plotted_y(render: dict) -> list[float]:
    """图里那条线的全点序列（数据坐标），测试侧独立换算：

    `geometry.paths[0].points` 是图幅分数、原点**左上**；axes 的 `bbox` 是 [x0, y0_top, w, h] 同一坐标系；
    y 轴线性、ylim 是 manifest 的可编辑值。 data_y = ylim0 + (axes_bottom − py) / h · (ylim1 − ylim0)。
    """
    axes = next(e for e in render["manifest"]["elements"] if e["role"] == "axes")
    ylim = next(f["value"] for f in axes["editable"] if f["prop"] == "ylim")
    _x0, y0, _w, h = axes["bbox"]
    geom = _manifest_line(render)["geometry"]
    assert geom["kind"] == "polyline" and len(geom["paths"]) == 1, geom
    pts = geom["paths"][0]["points"]
    bottom = y0 + h
    return [ylim[0] + (bottom - py) / h * (ylim[1] - ylim[0]) for _px, py in pts]


def _title_text(render: dict) -> str:
    title = next(e for e in render["manifest"]["elements"] if e["role"] == "title")
    return next(f["value"] for f in title["editable"] if f["prop"] == "text")


_NUM = rb"-?\d+(?:\.\d+)?"
_TOKEN = re.compile(rb"(" + _NUM + rb")|([A-Za-z*'\"]+)")


def _pdf_open_polylines(pdf: bytes) -> list[list[tuple[float, float]]]:
    """纯标准库：所有内容流里「m + 若干 l、不闭合、描边」的子路径（不 import 产品代码）。"""
    out: list[list[tuple[float, float]]] = []
    for _head, data in pdfread.objects(pdf).values():
        if not data:
            continue
        stack: list[float] = []
        cur: list[tuple[float, float]] = []
        closed = False
        for m in _TOKEN.finditer(data):
            if m.group(1) is not None:
                stack.append(float(m.group(1)))
                continue
            op = m.group(2)
            if op == b"m" and len(stack) >= 2:
                cur, closed = [(stack[-2], stack[-1])], False
            elif op == b"l" and len(stack) >= 2 and cur:
                cur.append((stack[-2], stack[-1]))
            elif op == b"h":
                closed = True
            elif op in (b"S", b"s") and cur:
                if not closed:
                    out.append(cur)
                cur = []
            elif op in (b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"n"):
                cur = []
            stack = []
    return out


def _exported_series(pdf: bytes) -> list[float]:
    """导出 PDF 里那条数据折线的归一化 y 序列：4 个点、x 等距严格递增（X = 0..3）的开放描边子路径。"""
    cands = []
    for poly in _pdf_open_polylines(pdf):
        if len(poly) != len(X):
            continue
        xs = [p[0] for p in poly]
        steps = [b - a for a, b in zip(xs, xs[1:])]
        if all(s > 0 for s in steps) and max(steps) - min(steps) < 1e-3 * max(steps):
            cands.append(_normalized([p[1] for p in poly]))
    assert len(cands) == 1, f"导出物里应恰有一条 4 点等距折线，实得 {len(cands)}"
    return cands[0]


def _export_pdf(app: fa.RunningApp, panel_id: str, name: str, overrides=None) -> tuple[Path, str]:
    _, body = app.call(
        "/api/export",
        {
            "scope": "original",
            "filename": name,
            "formats": ["pdf"],
            "overwrite": "replace",
            "original": {
                "figure_id": panel_id,
                "overrides": overrides or [],
                "source_kind": "figure",
                "w_mm": 81.28,
                "h_mm": 60.96,
            },
        },
        timeout=300,
    )
    assert body["status"] == "done", body
    (out,) = body["outputs"]
    path = Path(body["export_dir"]) / out["name"]
    return path, _sha256(path.read_bytes())


def _panel(app: fa.RunningApp, file_name: str) -> dict:
    _, panels = app.call("/api/panels", timeout=30)
    wanted = file_name.replace("\\", "/")
    found = [p for p in panels["panels"] if p["id"].replace("\\", "/") == wanted]
    assert found, [p["id"] for p in panels["panels"]]
    return found[0]


def _input_files(result: dict) -> list[tuple[str, str]]:
    return [(f["path"], f["sha256"]) for f in result["receipt"]["inputs"]["files"]]


def _approx_seq(ys: list[float]):
    return pytest.approx(ys, abs=0.02)


def _blocked_code(app: fa.RunningApp, panel_id: str) -> str:
    with pytest.raises(fa.HttpError) as blocked:
        app.render(panel_id)
    return blocked.value.body.get("code")


# ================================================================ PATH-01 同目录、异 cwd


@needs_worker
def test_path01_same_directory_csv_from_a_foreign_cwd_first_open_replay_and_export(tmp_path):
    """脚本与 CSV 同在 `analysis/`，服务进程的 cwd 是另一处（harness 不 chdir）：首开不问、沙盒默认
    经只读回退读到**这一份**；重放（新一代 worker）与导出的全序列都 = 真值。"""
    paper, truth = _d1(tmp_path)
    (paper / "analysis").mkdir()
    (paper / "analysis" / "points.csv").write_bytes(_csv(TRUE_Y))
    (paper / "analysis" / "same.py").write_text(
        _plot_script("_read_csv('points.csv')", "same.pdf"), encoding="utf-8"
    )
    _native(paper, "same.py", tmp_path, cwd=paper / "analysis")
    work = tmp_path / "work"
    with fa.running_app(paper, work) as app:
        panel = _panel(app, "analysis/same.pdf")
        state = app.prepare(panel["id"])
        plan, result = state["plan"], state["result"]
        assert result["status"] == "ready", result
        assert plan["required_input"] is None
        assert plan["workdir_decision"]["evidence"]["verdict"] == "default_ok"
        assert plan["launch_context"]["cwd_origin"] == "sandbox"
        assert _input_files(result) == [("analysis/points.csv", truth["true_sha"])]
        assert _plotted_y(app.render(panel["id"])) == _approx_seq(TRUE_Y)
        gen1 = result["receipt"]["generation"]
        # 重放：用户明确重建 → 新一代 worker 再跑一次
        _, inv = app.call("/api/engine/invalidate", {"id": panel["id"]})
        assert inv["invalidated"] is True
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        assert state["result"]["receipt"]["generation"] > gen1
        assert _input_files(state["result"]) == [("analysis/points.csv", truth["true_sha"])]
        assert _plotted_y(app.render(panel["id"])) == _approx_seq(TRUE_Y)
        pdf, _ = _export_pdf(app, panel["id"], "path01")
        assert _exported_series(pdf.read_bytes()) == _approx_seq(_normalized(TRUE_Y))


# ================================================================ PATH-02 分离目录


@needs_worker
def test_path02_split_directories_ask_once_remember_project_root_and_survive_a_restart(
    tmp_path,
):
    """`scripts/` 与 `data/` 分离：先问一次（推荐项目根）、选择前不出图；选项目根后 cwd 是**项目根**
    （不是脚本目录）、全序列 = 真值；关掉服务再开（编辑重开）不再问，带一次编辑导出仍是真值。"""
    paper, truth = _d1(tmp_path)
    _native(paper, "scripts/entry.py", tmp_path)
    script_bytes = (paper / "scripts" / "entry.py").read_bytes()
    work = tmp_path / "work"
    with fa.running_app(paper, work) as app:
        panel = _panel(app, "entry.pdf")
        state = app.prepare(panel["id"])
        result = state["result"]
        assert result["status"] == "needs_input", result
        need = result["required_input"]
        assert need["code"] == "workdir_confirmation_required"
        assert need["reason"] == "project_root_evidence"
        assert need["recommended"] == "project_root"
        by_mode = {o["mode"]: o for o in need["options"]}
        assert by_mode["project_root"]["found"] == ["data/points.csv"]
        assert by_mode["project"]["found"] == []
        assert result["receipt"] is None
        assert _blocked_code(app, panel["id"]) == "workdir_confirmation_required"
        _, patched = app.call("/api/engine/workdir", {"mode": "project_root"}, method="PATCH")
        assert patched["workdir"]["grant"]["cwd_write"]["granted"] is True
        state = app.prepare(panel["id"])
        result = state["result"]
        assert result["status"] == "ready", result
        assert state["plan"]["launch_context"]["cwd_origin"] == "project.root"
        assert result["receipt"]["launch_context"]["cwd_origin"] == "project.root"
        assert _input_files(result) == [("data/points.csv", truth["true_sha"])]
        assert [m["name"] for m in result["receipt"]["inputs"]["local_modules"]] == ["labmod"]
        render = app.render(panel["id"])
        assert _plotted_y(render) == _approx_seq(TRUE_Y)
        assert _title_text(render) == "D1 labmod"
    # 编辑重开：同一份数据目录 / 配置目录，新服务进程
    with fa.running_app(paper, work) as app:
        panel = _panel(app, "entry.pdf")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        decision = state["plan"]["workdir_decision"]
        assert decision["decided"] is True and decision["mode"] == "project_root"
        assert state["plan"]["launch_context"]["cwd_origin"] == "project.root"
        assert _input_files(state["result"]) == [("data/points.csv", truth["true_sha"])]
        render = app.render(panel["id"])
        title = next(e for e in render["manifest"]["elements"] if e["role"] == "title")
        patch = {"gid": title["gid"], "prop": "text", "value": "PATH-02 reopened"}
        edited = app.render(panel["id"], [patch])
        assert _title_text(edited) == "PATH-02 reopened"
        assert _plotted_y(edited) == _approx_seq(TRUE_Y)
        pdf, _ = _export_pdf(app, panel["id"], "path02", [patch])
        assert _exported_series(pdf.read_bytes()) == _approx_seq(_normalized(TRUE_Y))
    assert (paper / "scripts" / "entry.py").read_bytes() == script_bytes, "脚本不许被改"


# ================================================================ PATH-03 __file__ / 绝对路径


@needs_worker
def test_path03_file_relative_and_absolute_outside_are_kept_and_a_stale_absolute_path_fails(
    tmp_path,
):
    """`__file__` 仍指真实脚本；项目外的有效绝对路径原样读取（不拷进项目 / 数据目录）；旧机器上的硬编码
    绝对路径（basename 与项目里的 `data/points.csv` 相同）**明确失败**，不按同名映射到项目里那份。"""
    paper, truth = _d1(tmp_path)
    ext = tmp_path / "external store"
    ext.mkdir()
    (ext / "abs_points.csv").write_bytes(_csv(EXTERNAL_Y))
    ext_sha = _sha256(_csv(EXTERNAL_Y))
    s = paper / "scripts"
    (s / "file_rel.py").write_text(
        _plot_script(
            "_read_csv(os.path.join(HERE, '..', 'data', 'points.csv'))",
            "file_rel.pdf",
            pre="HERE = os.path.dirname(os.path.abspath(__file__))\n",
            title="os.path.basename(HERE) + '/' + os.path.basename(__file__)",
        ),
        encoding="utf-8",
    )
    (s / "abs_out.py").write_text(
        _plot_script(f"_read_csv({str(ext / 'abs_points.csv')!r})", "abs_out.pdf"),
        encoding="utf-8",
    )
    stale = "/nonexistent-old-machine/home/alice/paper/data/points.csv"
    (s / "stale_abs.py").write_text(
        _plot_script(f"_read_csv({stale!r})", "stale_abs.pdf"), encoding="utf-8"
    )
    _native(paper, "scripts/file_rel.py", tmp_path)
    _native(paper, "scripts/abs_out.py", tmp_path)
    # 旧机器上跑出来的原件（这台机器上脚本已经跑不通了）
    shutil.copy(paper / "file_rel.pdf", paper / "stale_abs.pdf")
    work = tmp_path / "work"
    with fa.running_app(paper, work) as app:
        # __file__
        panel = _panel(app, "file_rel.pdf")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        assert state["plan"]["required_input"] is None
        render = app.render(panel["id"])
        assert _title_text(render) == "scripts/file_rel.py"
        assert _plotted_y(render) == _approx_seq(TRUE_Y)
        assert _input_files(state["result"]) == [("data/points.csv", truth["true_sha"])]
        # 项目外的有效绝对路径：原样读取
        panel = _panel(app, "abs_out.pdf")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        assert _plotted_y(app.render(panel["id"])) == _approx_seq(EXTERNAL_Y)
        # 回执只记项目内的输入（观察有界、如实 partial）：项目外那份不在 files 里
        assert state["result"]["receipt"]["inputs"]["observation"] == "partial"
        assert _input_files(state["result"]) == []
        # 旧机器的绝对路径：明确失败，一张图都不发布；项目里同名那份不许被拿来顶替
        panel = _panel(app, "stale_abs.pdf")
        state = app.prepare(panel["id"])
        result = state["result"]
        assert result["status"] == "error", result
        assert result["receipt"] is None
        blob = json.dumps(result["error"], ensure_ascii=False)
        assert "FileNotFoundError" in blob or "No such file" in blob, blob[:800]
        with pytest.raises(fa.HttpError):
            app.render(panel["id"])
    # 项目外的数据没被拷进项目或数据目录（不搬实验目录）
    copies = [p for root in (paper, work) for p in root.rglob("abs_points.csv")]
    assert copies == []
    assert _sha256((ext / "abs_points.csv").read_bytes()) == ext_sha


# ================================================================ PATH-04 同名干扰


@needs_worker
def test_path04_same_name_decoy_asks_without_preselection_and_reads_only_the_chosen_file(
    tmp_path,
):
    """脚本目录（`scripts/data/points.csv`，干扰）与项目根（`data/points.csv`，正确）各一份同名数据，
    极值 / 均值 / ylim 全同。确认前不发布图、不预选；选项目根 → 全序列、输入 hash、导出折线都 = 正确那份；
    改选脚本目录 → 全部 = 干扰那份（值必须等于**选的**那份）。"""
    paper, truth = _d1(tmp_path, decoy=True)
    _native(paper, "scripts/entry.py", tmp_path)
    work = tmp_path / "work"
    with fa.running_app(paper, work) as app:
        panel = _panel(app, "entry.pdf")
        state = app.prepare(panel["id"])
        result = state["result"]
        assert result["status"] == "needs_input", result
        need = result["required_input"]
        assert need["reason"] == "ambiguous_data"
        assert need["recommended"] is None, "歧义时不预选"
        assert not any(o.get("recommended") for o in need["options"])
        assert need["conflicts"] == ["data/points.csv"]
        assert result["receipt"] is None and result["started_at"] is None
        assert _blocked_code(app, panel["id"]) == "workdir_confirmation_required"
        # 选项目根 → 正确那份
        app.call("/api/engine/workdir", {"mode": "project_root"}, method="PATCH")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        # 三把互相独立的尺子，先量图里的全序列（ylim 在这里量不出差别），再量输入 hash、导出折线
        render = app.render(panel["id"])
        assert _plotted_y(render) == _approx_seq(TRUE_Y)
        assert _input_files(state["result"]) == [("data/points.csv", truth["true_sha"])]
        pdf, _ = _export_pdf(app, panel["id"], "path04")
        assert _exported_series(pdf.read_bytes()) == _approx_seq(_normalized(TRUE_Y))
        # 改选脚本目录 → 干扰那份（机器不裁决，值跟着选择走）
        app.call("/api/engine/workdir", {"mode": "project"}, method="PATCH")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        assert state["plan"]["launch_context"]["cwd_origin"] == "script.parent"
        assert _input_files(state["result"]) == [("scripts/data/points.csv", truth["decoy_sha"])]
        assert _plotted_y(app.render(panel["id"])) == _approx_seq(DECOY_Y)


# ================================================================ PATH-05 特殊路径 / 参数 / 环境


def _windows_long_paths_enabled() -> bool:
    """这台 Windows 开没开长路径（`LongPathsEnabled`）。没开的机器上，用户自己（资源管理器、Python、
    matplotlib）就建不出超过 MAX_PATH 的路径——「> 260」这一维在那里不存在。"""
    import winreg  # noqa: PLC0415 — 只在 Windows 上有

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def _pad_dirs(base: Path, tail: tuple[str, ...], target: int) -> Path:
    """在 `base` 与 `tail` 之间插入「长路径段」目录，让 `base/…/tail` 的总长到 `target`（差不过 1 个字符）。

    长度按**这台机器上的真实前缀**（`tmp_path`）现算：Linux 的 `/tmp/pytest-of-runner/…` 比 macOS 的
    `/private/var/folders/…` 短六十来个字符，写死段数的话「> 260」只在写用例的那台机器上成立（CI 实测 223）。
    单段 ≤ 68 字符 / 100 UTF-8 字节，远低于各文件系统 255 的分量上限；段尾不是空格或点（Windows 不许）。"""
    unit = "长路径段_long_segment_" * 4
    deep = base
    while (room := target - len(str(deep.joinpath(*tail))) - 1) >= 1:
        deep = deep / unit[: min(room, len(unit))]
    return deep


@needs_worker
def test_path05_cjk_space_quotes_long_path_and_no_env_secret_in_public_responses(tmp_path):
    """项目路径含中文、空格、引号且总长 > 260；数据文件名含中文与空格。首开自动成功、全序列 = 真值；
    服务环境里的一条「密钥」不出现在准备 / 渲染 / 导出的任何公开响应里。safe 档没有给脚本传参的入口：
    要参数的脚本明确报 `script_needs_arguments`（安全停止，不是兼容成功）。

    路径按平台的规则造（用户在那个平台上真建得出来的才算数）：

    * 双引号：POSIX 上是合法文件名字符，照测；Windows 的文件名不许有 `"`（`< > : " / \\ | ? *`
      都不许），用户在 Windows 上根本建不出这样的目录——那里只测单引号。
    * > 260：POSIX 没有 MAX_PATH，目录本身就拉到 260 以上。Windows 上只有开了 `LongPathsEnabled`
      才成立；而**当前目录**另有 MAX_PATH − 12 的限制（脚本以它为 cwd 运行），所以那里目录停在 230
      以内、由数据文件名把全路径推过 260。没开长路径的 Windows 上这一维不适用：路径留在 260 以内，
      中文 / 空格 / 单引号照测。
    """
    tail = ("paper project", "分析 a")
    data_name = "点 数据.csv"
    long_path = True
    if sys.platform == "win32":
        top = tmp_path / "数据 目录 'single'"
        long_path = _windows_long_paths_enabled()
        if long_path:
            analysis = _pad_dirs(top, tail, 230).joinpath(*tail)
            stem = "点 数据 "
            while len(str(analysis / f"{stem}.csv")) <= 270:
                stem += "长文件名_long_name_"
            data_name = f"{stem.rstrip()}.csv"
        else:
            analysis = _pad_dirs(top, tail, 200).joinpath(*tail)
    else:
        top = tmp_path / "数据 目录 'single' \"double\""
        analysis = _pad_dirs(top, tail, 280).joinpath(*tail)
    paper = analysis.parent
    analysis.mkdir(parents=True)
    (analysis / data_name).write_bytes(_csv(TRUE_Y))
    (analysis / "图 一.py").write_text(
        _plot_script(f"_read_csv({data_name!r})", "图 一.pdf"), encoding="utf-8"
    )
    (paper / "分析 a" / "needs_args.py").write_text(
        "import argparse\nap = argparse.ArgumentParser()\nap.add_argument('--data', required=True)\n"
        "ap.parse_args()\nimport matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots()\nax.plot([0, 1])\nfig.savefig('needs_args.pdf')\n",
        encoding="utf-8",
    )
    if long_path:
        assert len(str(analysis / data_name)) > 260
    else:  # 没开长路径的 Windows：这一维不适用（见 docstring），其余维度照测
        assert len(str(analysis / data_name)) < 260
    _native(paper, "图 一.py", tmp_path, cwd=paper / "分析 a")
    shutil.copy(paper / "分析 a" / "图 一.pdf", paper / "分析 a" / "needs_args.pdf")
    secret = "qa-secret-5f0c2d9e-not-a-real-key"
    responses: list[str] = []
    with fa.running_app(
        paper, tmp_path / "work", env_overrides={"QA_PATH05_API_KEY": secret}
    ) as app:
        panel = _panel(app, "分析 a/图 一.pdf")
        state = app.prepare(panel["id"])
        responses.append(json.dumps(state, ensure_ascii=False))
        assert state["result"]["status"] == "ready", state["result"]
        assert _input_files(state["result"]) == [(f"分析 a/{data_name}", _sha256(_csv(TRUE_Y)))]
        render = app.render(panel["id"])
        responses.append(json.dumps(render, ensure_ascii=False))
        assert _plotted_y(render) == _approx_seq(TRUE_Y)
        pdf, _ = _export_pdf(app, panel["id"], "path05 导出")
        assert _exported_series(pdf.read_bytes()) == _approx_seq(_normalized(TRUE_Y))
        _, envst = app.call("/api/engine/environment", timeout=30)
        responses.append(json.dumps(envst, ensure_ascii=False))
        # 要参数的脚本：safe 档 argv 恒为空 → 明确的安全停止
        panel = _panel(app, "分析 a/needs_args.pdf")
        state = app.prepare(panel["id"])
        responses.append(json.dumps(state, ensure_ascii=False))
        assert state["result"]["status"] == "error", state["result"]
        assert state["result"]["error"]["code"] == "script_needs_arguments", state["result"]
    for text in responses:
        assert secret not in text


# ================================================================ PATH-06 先探测后读取


PROBE_THEN_READ = (
    "import csv, glob, os, sys\n"
    "import matplotlib\n"
    "matplotlib.use('Agg')\n"
    "import matplotlib.pyplot as plt\n"
    "import numpy as np\n"
    "exists = os.path.exists('points.csv')\n"
    "found = glob.glob('*.csv')\n"
    "listed = 'points.csv' in os.listdir('.')\n"
    "print(f'probe exists={exists} glob={len(found)} listdir={listed}', flush=True)\n"
    "if not exists:\n"
    "    print('[ERROR] 数据文件未找到，跳过作图', flush=True)\n"
    "    sys.exit(0)\n"
    "arr = np.loadtxt('points.csv', delimiter=',', skiprows=1)\n"
    "fig, ax = plt.subplots(figsize=(3.2, 2.4))\n"
    "ax.plot(arr[:, 0], arr[:, 1], 'o-')\n"
    "ax.set_title(f'exists={exists} glob={len(found)} listdir={listed}')\n"
    "fig.savefig('probe_read.pdf')\n"
)


@needs_worker
def test_path06_probe_then_read_is_a_guided_stop_in_the_sandbox_and_correct_in_project_mode(
    tmp_path,
):
    """默认沙盒：`np.loadtxt` 直读（经 numpy DataSource 的只读回退）自动成功、全序列 = 真值；而先
    `exists/glob/listdir` 再读的脚本在沙盒里看到的是空目录——**不**算自动成功：产品报
    `no_figures_captured`（带脚本自己的输出），不发布图；用户改「在脚本目录里运行」后三个探测
    与读取一致、全序列 = 真值。"""
    paper, truth = _d1(tmp_path)
    a = paper / "analysis"
    a.mkdir()
    (a / "points.csv").write_bytes(_csv(TRUE_Y))
    (a / "np_only.py").write_text(
        _plot_script("np.loadtxt('points.csv', delimiter=',', skiprows=1).T", "np_only.pdf"),
        encoding="utf-8",
    )
    (a / "probe_read.py").write_text(PROBE_THEN_READ, encoding="utf-8")
    _native(paper, "np_only.py", tmp_path, cwd=a)
    _native(paper, "probe_read.py", tmp_path, cwd=a)
    with fa.running_app(paper, tmp_path / "work") as app:
        panel = _panel(app, "analysis/np_only.pdf")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        assert state["plan"]["launch_context"]["cwd_origin"] == "sandbox"
        assert _input_files(state["result"]) == [("analysis/points.csv", truth["true_sha"])]
        assert _plotted_y(app.render(panel["id"])) == _approx_seq(TRUE_Y)
        # 盲区：exists / glob / listdir 在沙盒里是沙盒的真话
        panel = _panel(app, "analysis/probe_read.pdf")
        state = app.prepare(panel["id"])
        # 静态证据说「脚本目录找得到」（default_ok）→ 不问；准备接口此刻的终局见 QA 台账
        # （repro/repro_path06_prepare_ready_without_figure.py）——这里只钉用户看得到的那条路：渲染。
        assert state["plan"]["workdir_decision"]["needs_confirmation"] is False
        assert state["plan"]["workdir_decision"]["evidence"]["verdict"] == "default_ok"
        with pytest.raises(fa.HttpError) as blocked:
            app.render(panel["id"])
        assert blocked.value.body["code"] == "no_figures_captured", blocked.value.body
        assert "probe exists=False glob=0 listdir=False" in blocked.value.body["traceback"]
        # 用户显式选「在脚本目录里运行」
        app.call("/api/engine/workdir", {"mode": "project"}, method="PATCH")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        render = app.render(panel["id"])
        assert _title_text(render) == "exists=True glob=1 listdir=True"
        assert _plotted_y(render) == _approx_seq(TRUE_Y)


# ================================================================ PATH-07 符号链接


@needs_worker
@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows 的 symlink 要开发者模式；另腿记 not_run"
)
def test_path07_symlinks_inside_are_read_and_escaping_ones_are_not_followed_by_the_fallback(
    tmp_path,
):
    """沙盒默认：项目内指向项目内的软链接经只读回退读到目标（全序列 = 真值）；指向项目外的软链接**不**被
    回退跟出去——脚本读不到、不发布图（安全停止）。之后项目根上出现一份同名文件：静态证据**不**把逃出项目
    的那条算进候选（ADR 0057「不碰项目根之外」），于是问一次、推荐项目根；用户显式选「脚本目录」后行为
    与终端里站在脚本目录一致（读到外部那份），项目根那份同名文件一个字节都没被读。"""
    paper, truth = _d1(tmp_path)
    ext = tmp_path / "outside"
    ext.mkdir()
    (ext / "abs_points.csv").write_bytes(_csv(EXTERNAL_Y))
    a = paper / "analysis"
    a.mkdir()
    os.symlink(paper / "data" / "points.csv", a / "link_in.csv")
    os.symlink(ext / "abs_points.csv", a / "link_out.csv")
    (a / "link_in.py").write_text(
        _plot_script("_read_csv('link_in.csv')", "link_in.pdf"), encoding="utf-8"
    )
    (a / "link_out.py").write_text(
        _plot_script("_read_csv('link_out.csv')", "link_out.pdf"), encoding="utf-8"
    )
    _native(paper, "link_in.py", tmp_path, cwd=a)
    _native(paper, "link_out.py", tmp_path, cwd=a)
    with fa.running_app(paper, tmp_path / "work") as app:
        panel = _panel(app, "analysis/link_in.pdf")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        assert _plotted_y(app.render(panel["id"])) == _approx_seq(TRUE_Y)
        assert _input_files(state["result"]) == [("data/points.csv", truth["true_sha"])]
        # 逃出项目的软链接：静态证据记为 outside、不参与判决；运行时回退不跟出去
        panel = _panel(app, "analysis/link_out.pdf")
        state = app.prepare(panel["id"])
        evidence = state["plan"]["workdir_decision"]["evidence"]
        assert evidence["candidates"]["script.parent"]["outside"] == ["link_out.csv"]
        assert evidence["verdict"] == "unknown"
        result = state["result"]
        assert result["status"] == "error", result
        assert result["error"]["code"] == "script_error", result["error"]
        assert result["receipt"] is None
        with pytest.raises(fa.HttpError):
            app.render(panel["id"])
        # 项目根出现同名文件 → 问一次（不静默改读它）
        (paper / "link_out.csv").write_bytes(_csv(DECOY_Y))
        state = app.prepare(panel["id"])
        need = state["result"]["required_input"]
        assert state["result"]["status"] == "needs_input", state["result"]
        assert need["reason"] == "project_root_evidence"
        # 失败 build 留下的旧会话要先作废，渲染入口才回到同一道门
        # （不作废时的行为见 repro/repro_path07_gate_vs_live_worker.py）
        app.call("/api/engine/invalidate", {"id": panel["id"]})
        assert _blocked_code(app, panel["id"]) == "workdir_confirmation_required"
        # 用户选脚本目录：与终端一致，读外部那份；根上的同名文件没被读
        app.call("/api/engine/workdir", {"mode": "project"}, method="PATCH")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        assert _plotted_y(app.render(panel["id"])) == _approx_seq(EXTERNAL_Y)
        assert _input_files(state["result"]) == []  # 项目外的输入不进观察（如实 partial）


# ================================================================ PATH-08 数据丢失 / 撤权限


@needs_worker
def test_path08_deleted_or_unreadable_data_fails_explicitly_and_never_falls_back_to_the_decoy(
    tmp_path,
):
    """项目根模式下读 `data/points.csv`（脚本目录里另有同名干扰）。删掉正确那份后重建：明确失败、不发布，
    **不**改读干扰那份；带编辑的导出失败（要当次重跑脚本）；不带编辑的导出按既有合同交出磁盘原件
    （FO-065：静态导出不强迫执行脚本）——它的折线仍是原件那份真值，不是干扰；撤读权限同样明确失败；
    恢复后回到真值。"""
    paper, truth = _d1(tmp_path, decoy=True)
    _native(paper, "scripts/entry.py", tmp_path)
    data = paper / "data" / "points.csv"
    with fa.running_app(paper, tmp_path / "work") as app:
        panel = _panel(app, "entry.pdf")
        app.call("/api/engine/workdir", {"mode": "project_root"}, method="PATCH")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        assert _plotted_y(app.render(panel["id"])) == _approx_seq(TRUE_Y)
        # 删掉数据 → 用户重建
        data.unlink()
        app.call("/api/engine/invalidate", {"id": panel["id"]})
        state = app.prepare(panel["id"])
        result = state["result"]
        assert result["status"] == "error", result
        assert result["receipt"] is None
        assert result["error"]["code"] == "script_error", result["error"]
        assert "data/points.csv" in result["error"]["message"]
        with pytest.raises(fa.HttpError):
            app.render(panel["id"])
        title = {"gid": "axes_0.title", "prop": "text", "value": "PATH-08 edit"}
        with pytest.raises(fa.HttpError) as failed:
            _export_pdf(app, panel["id"], "path08-edited", [title])
        assert failed.value.body["status"] == "failed" and failed.value.body["outputs"] == []
        pdf, _ = _export_pdf(app, panel["id"], "path08-static")
        assert _exported_series(pdf.read_bytes()) == _approx_seq(_normalized(TRUE_Y))
        # 撤读权限
        data.write_bytes(_csv(TRUE_Y))
        if os.name != "nt" and os.geteuid() != 0:
            data.chmod(0)
            try:
                app.call("/api/engine/invalidate", {"id": panel["id"]})
                state = app.prepare(panel["id"])
                assert state["result"]["status"] == "error", state["result"]
                blob = json.dumps(state["result"]["error"], ensure_ascii=False)
                assert "PermissionError" in blob or "Permission denied" in blob, blob[:800]
            finally:
                data.chmod(0o644)
        # 恢复 → 真值
        app.call("/api/engine/invalidate", {"id": panel["id"]})
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        assert _input_files(state["result"]) == [("data/points.csv", truth["true_sha"])]
        assert _plotted_y(app.render(panel["id"])) == _approx_seq(TRUE_Y)


# ================================================================ PATH-09 读写隔离


RW = _plot_script(
    "_read_csv(os.path.join(HERE, '..', 'data', 'points.csv'))",
    "rw.pdf",
    pre=(
        "HERE = os.path.dirname(os.path.abspath(__file__))\n"
        "with open('interm_out.txt', 'w', encoding='utf-8') as _fh:\n"
        "    _fh.write('intermediate')\n"
    ),
)


@needs_worker
def test_path09_relative_writes_land_only_where_the_current_grant_allows(tmp_path):
    """同一脚本读数据（按 `__file__`，三种模式都读得到）又写相对路径的中间结果：沙盒 → 只落沙盒、项目里
    一个字节都没有；脚本目录 → `scripts/`；项目根 → 项目根。撤销授权（切回沙盒）后旧会话不再被复用，
    下一次写回到沙盒。"""
    paper, truth = _d1(tmp_path)
    (paper / "scripts" / "rw.py").write_text(RW, encoding="utf-8")
    _native(paper, "scripts/rw.py", tmp_path)
    (paper / "interm_out.txt").unlink()  # 原生参考写的，不是本用例的观测
    work = tmp_path / "work"

    def interm_in_project() -> list[str]:
        return sorted(p.relative_to(paper).as_posix() for p in paper.rglob("interm_out.txt"))

    with fa.running_app(paper, work) as app:
        panel = _panel(app, "rw.pdf")
        expected = {"sandbox": [], "project": ["scripts/interm_out.txt"]}
        expected["project_root"] = ["interm_out.txt"]
        for mode in ("sandbox", "project", "project_root", "sandbox"):
            _, patched = app.call("/api/engine/workdir", {"mode": mode}, method="PATCH")
            assert patched["workdir"]["grant"]["cwd_write"]["granted"] is (mode != "sandbox")
            state = app.prepare(panel["id"])
            result = state["result"]
            assert result["status"] == "ready", (mode, result)
            assert result["created_runtime"] is True, f"{mode}: 换了授权却复用了旧会话"
            assert _input_files(result) == [("data/points.csv", truth["true_sha"])]
            assert _plotted_y(app.render(panel["id"])) == _approx_seq(TRUE_Y)
            assert interm_in_project() == expected[mode], mode
            for p in paper.rglob("interm_out.txt"):
                p.unlink()
        # 沙盒模式下的相对写落进了沙盒（数据目录里），不是凭空消失
        assert list((work / "data").rglob("interm_out.txt")), "沙盒里应有那份中间结果"
