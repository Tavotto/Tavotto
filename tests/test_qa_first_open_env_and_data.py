"""QA 第一批强验收②的短真流程（QA 2026-09-24 flagship 节 / ACCEPT-2）：E1 双环境 × D1 同名干扰数据，
经真实 HTTP 入口（会话认证开、干净用户配置、**没有** `TAVOTTO_WORKER_PYTHON`）一次走完。

既有用例把两条轴分开量：`test_first_open_environment.py` 只有环境（数据无歧义），
`test_foundation_first_open.py` 的 FO02 / FO07 只有数据（没有项目 venv、PATH 上也没有竞争环境）。
这里两者**同时**成立——这正是用户的真实形状：

* 项目 `.venv`（A）与 PATH 最前面的另一个 venv（B）都装着同名测试包 `qa_signal`，返回不同数值；
  内置 / 系统环境里没有它；
* `data/points.csv`（真值 y = [0, 9, 2, 10]）与脚本目录下的同名干扰 `scripts/data/points.csv`
  （y = [0, 2, 9, 10]，极值、均值都一样）；
* 脚本还 import 脚本目录里的本地模块、读一份项目外的绝对路径数据。

判据的主语：**worker 实际执行时**用了哪个解释器 / 包 / 数据文件——不是界面提示、不是
`VIRTUAL_ENV`。证据取三处互相独立的出处：回执里 worker 自报的 import 来源与输入文件 sha256、
图内那段由脚本在运行时写下的「数据 sha 前缀 + 环境标识」文字、SVG 里折线四个顶点的纵坐标
（按真值做仿射拟合，干扰序列拟合不上）。极值 / 均值 / ylim 对两份数据完全相同，**不能**当判据。

harness 只准备「用户本来就有」的东西（两个 venv、数据、原件），不替产品选环境、不装包、不预设 cwd；
用户的那一次选择走公开的 `PATCH /api/engine/workdir`（与界面确认框同一条路）。
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

from support import foundation_app as fa, venvfixture

try:
    from tavotto.engine import pool as _pool

    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001 — 没有科学栈就 skip，而 skip 在 CI 校验步里是红
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

Y_TRUE = [0, 9, 2, 10]
Y_DECOY = [0, 2, 9, 10]
SIGNAL_A = [1.0, 3.0, 2.0, 4.0]
SIGNAL_B = [3.0, 1.0, 4.0, 2.0]  # 与 A 不成仿射关系：拟合分得开

ENTRY = """import csv
import hashlib

import labmod
import matplotlib
import qa_signal

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

EXTERNAL = {external!r}

with open("data/points.csv", "rb") as fh:
    RAW = fh.read()
ts, ys = [], []
for row in csv.DictReader(RAW.decode("utf-8").splitlines()):
    ts.append(float(row["t"]))
    ys.append(float(row["y"]))
offs = []
with open(EXTERNAL, encoding="utf-8", newline="") as fh:
    for row in csv.DictReader(fh):
        offs.append(float(row["offset"]))
fig, (ax1, ax3) = plt.subplots(1, 2, figsize=(7.0, 3.2))
ax1.plot(ts, ys, "o-", label="points")
ax2 = ax1.twinx()
ax2.plot(ts, qa_signal.values(len(ts)), "s--", label="sig " + qa_signal.IDENT)
ax3.bar(ts, labmod.scale(offs))
fig.text(0.01, 0.02, "src " + hashlib.sha256(RAW).hexdigest()[:12] + " env " + qa_signal.IDENT)
fig.savefig("Fig_qa.pdf")
"""

QA_SIGNAL = "IDENT = {ident!r}\n_V = {values!r}\n\n\ndef values(n):\n    return list(_V[:n])\n"


def _csv(ys: list[int]) -> str:
    return "t,y\n" + "".join(f"{t},{y}\n" for t, y in enumerate(ys))


def _install_signal(venv: Path, ident: str, values: list[float]) -> Path:
    """把测试包写进这个 venv 的 site-packages（harness 准备「用户本来就装着」的包）。"""
    pkg = venvfixture.site_packages(venv) / "qa_signal"
    pkg.mkdir(parents=True, exist_ok=True)
    init = pkg / "__init__.py"
    init.write_text(QA_SIGNAL.format(ident=ident, values=values), encoding="utf-8")
    return init


def _affine_residual(svg_ys: list[float], data: list[int]) -> tuple[float, float]:
    """最小二乘 svg_y = a + b·data：回 (最大残差 pt, 斜率 b)。SVG 的 y 朝下，正确的一份 b < 0。"""
    n = len(data)
    mx, my = sum(data) / n, sum(svg_ys) / n
    sxx = sum((d - mx) ** 2 for d in data)
    b = sum((d - mx) * (s - my) for d, s in zip(data, svg_ys, strict=True)) / sxx
    a = my - b * mx
    return max(abs(a + b * d - s) for d, s in zip(data, svg_ys, strict=True)), b


def _line_ys(svg: str, gid: str) -> list[float]:
    m = re.search(r'<g id="' + re.escape(gid) + r'"[\s\S]*?<path d="([^"]+)"', svg)
    assert m, f"SVG 里没有 {gid} 的路径"
    return [float(y) for _x, y in re.findall(r"[ML]\s*(-?[\d.]+)\s+(-?[\d.]+)", m.group(1))]


def _site_listing(venv: Path) -> list[str]:
    return sorted(p.name for p in venvfixture.site_packages(venv).iterdir())


def _text(manifest: dict, gid: str) -> str:
    el = next(e for e in manifest["elements"] if e["gid"] == gid)
    return next(f["value"] for f in el["editable"] if f["prop"] == "text")


@needs_worker
def test_project_venv_and_same_name_decoy_resolve_to_the_true_interpreter_and_file(tmp_path):
    proj = tmp_path / "paper"
    (proj / "scripts" / "data").mkdir(parents=True)
    (proj / "data").mkdir()
    external = tmp_path / "external" / "offsets.csv"
    external.parent.mkdir()
    external.write_text("t,offset\n0,0.5\n1,1.5\n2,2.5\n3,3.5\n", encoding="utf-8")
    (proj / "data" / "points.csv").write_text(_csv(Y_TRUE), encoding="utf-8")
    (proj / "scripts" / "data" / "points.csv").write_text(_csv(Y_DECOY), encoding="utf-8")
    (proj / "scripts" / "entry.py").write_text(
        ENTRY.format(external=str(external)), encoding="utf-8"
    )
    (proj / "scripts" / "labmod.py").write_text(
        "def scale(values):\n    return [v * 2.0 for v in values]\n", encoding="utf-8"
    )
    true_sha = hashlib.sha256((proj / "data" / "points.csv").read_bytes()).hexdigest()
    decoy_sha = hashlib.sha256((proj / "scripts" / "data" / "points.csv").read_bytes()).hexdigest()

    venv_a = venvfixture.make_project_venv(proj, ".venv", python=WORKER_PY)
    _install_signal(venv_a, "A", SIGNAL_A)
    venv_b = venvfixture.make_project_venv(tmp_path / "elsewhere", "envB", python=WORKER_PY)
    _install_signal(venv_b, "B", SIGNAL_B)
    python_a = venvfixture.interpreter_of(venv_a)
    # 用户在终端里站在项目根、用项目环境跑过一次：原件落在项目根（素材卡片）
    subprocess.run(
        [python_a, "scripts/entry.py"],
        cwd=proj,
        check=True,
        capture_output=True,
        timeout=300,
        env={
            **{k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "TEMP", "TMP")},
            "PATH": os.environ.get("PATH", ""),
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": str(tmp_path / "mpl"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    script_bytes = (proj / "scripts" / "entry.py").read_bytes()
    site_before_a, site_before_b = _site_listing(venv_a), _site_listing(venv_b)
    b_bin = Path(venvfixture.interpreter_of(venv_b)).parent
    path_with_b_first = str(b_bin) + os.pathsep + os.environ.get("PATH", "")

    with fa.running_app(proj, tmp_path / "work", env_overrides={"PATH": path_with_b_first}) as app:
        _, panels = app.call("/api/panels", timeout=30)
        panel = next(p for p in panels["panels"] if p["id"].replace("\\", "/") == "Fig_qa.pdf")
        assert panel["script"] == "scripts/entry.py"

        # ① 首开：同名数据两处各一份、内容不同 → 先问、不预选；选择前一张图都不发布
        state = app.prepare(panel["id"])
        result = state["result"]
        assert result["status"] == "needs_input", result
        need = result["required_input"]
        assert need["reason"] == "ambiguous_data"
        assert need["recommended"] is None
        assert need["conflicts"] == ["data/points.csv"]
        assert result["receipt"] is None
        with pytest.raises(fa.HttpError) as blocked:
            app.render(panel["id"])
        assert blocked.value.body["code"] == "workdir_confirmation_required"

        # ② 用户选项目根（公开流程）
        _, patched = app.call("/api/engine/workdir", {"mode": "project_root"}, method="PATCH")
        assert patched["workdir"]["mode"] == "project_root"

        # ③ 准备：产品自己选的是项目 venv（A），不是 PATH 上更靠前的 B，也不是内置环境
        state = app.prepare(panel["id"])
        plan, result = state["plan"], state["result"]
        assert result["status"] == "ready", result
        assert plan["environment"]["source"] == "project_venv"
        assert plan["dependency_intents"] == []  # 什么都不装
        receipt = result["receipt"]
        assert receipt["python_source"] == "project_venv"
        assert receipt["launch_context"]["cwd_origin"] == "project.root"
        inputs = receipt["runtime"]["inputs"]
        mods = {m["name"]: m for m in inputs["local_modules"]}
        # worker 自报的本地模块：labmod 是脚本目录的本地模块；qa_signal 装在项目 `.venv`（A）的
        # site-packages 里，是解释器自己的目录，不是用户的本地模块（PATH-B2，#599）。
        # 「用的是 A 那份」的证据在 ④（图内写下的 env A + 折线顶点）与 ⑤（环境状态指 A）
        assert mods["labmod"]["path"] == "scripts/labmod.py"
        assert set(mods) == {"labmod"}, mods
        files = {f["path"]: f["sha256"] for f in inputs["files"]}
        assert files.get("data/points.csv") == true_sha != decoy_sha

        # ④ 图内数值：脚本运行时写下的出处文字 + 折线顶点（全序列，而不是极值 / 均值）
        # inline_svg：SVG 与 manifest 必须出自同一次响应（与前端 engineRender 同一个请求形状）
        _, render = app.call(
            "/api/engine/render",
            {"id": panel["id"], "patches": [], "inline_svg": True},
            timeout=300,
        )
        assert _text(render["manifest"], "fig.texts_0") == f"src {true_sha[:12]} env A"
        ys = _line_ys(render["svg"], "axes_0.lines_0")
        res_true, slope = _affine_residual(ys, Y_TRUE)
        res_decoy, slope_decoy = _affine_residual(ys, Y_DECOY)
        assert res_true < 0.01 and slope < 0, (ys, res_true, slope)
        assert res_decoy > 1 or slope_decoy >= 0, (ys, res_decoy)
        sig = _line_ys(render["svg"], "axes_2.lines_0")
        res_a, slope_a = _affine_residual(sig, SIGNAL_A)
        res_b, slope_b = _affine_residual(sig, SIGNAL_B)
        assert res_a < 0.01 and slope_a < 0, (sig, res_a)
        assert res_b > 1 or slope_b >= 0, (sig, res_b)

        # ⑤ 重开不再问（决定记住了），环境状态仍指 A
        again = app.prepare(panel["id"])
        assert again["result"]["status"] == "ready"
        _, envst = app.call("/api/engine/environment", timeout=30)
        chosen = envst["project"]["python"]
        chosen_path = Path(chosen) if Path(chosen).is_absolute() else proj / chosen
        assert os.path.normcase(str(chosen_path)) == os.path.normcase(python_a)

    # 没有副作用：脚本字节不变；两个 venv 的 site-packages 与开应用之前逐项相同（没装、没升级、没删）
    assert (proj / "scripts" / "entry.py").read_bytes() == script_bytes
    assert _site_listing(venv_a) == site_before_a
    assert _site_listing(venv_b) == site_before_b
