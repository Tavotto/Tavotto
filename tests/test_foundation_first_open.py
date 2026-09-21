"""统一实施包 U03：已有环境与数据上下文的首开场景（FirstOpenBench，经真实公共入口）。

每条场景：把合成夹具复制成「用户本来就有」的项目 → `python -m tavotto`（会话认证默认开、
用户配置空、**没有** `TAVOTTO_WORKER_PYTHON`）→ 准备接口 → 渲染 → 用夹具 `truth.json` 的
已知数值核对图内值（自动缩放 5% 边距由真值推出）→ 写一条结果记录（`foundation_harness`
的 schema）。**harness 不替产品选环境、不装包、不预设 cwd**（05 §4）；引导型场景（FO02 /
FO07）里用户的那一次选择走公开的 `PATCH /api/engine/workdir`，与界面上的确认框同一条路。

| case | 场景 | 预期产品结果 |
|---|---|---|
| FO01 | 脚本同目录 CSV，干净设置 | automatic |
| FO02 | scripts/ 与 data/ 分离、cwd 歧义（`data/points.csv`） | guided：先问运行目录，选项目根后成功 |
| FO03 | `__file__` 定位数据 + 本地包 `labhelpers` | automatic |
| FO07 | 脚本目录与项目根各有一份同名 `data.csv`、内容不同 | guided：选择前不发布图；值等于选的那份 |
| FO15 | 显式指定的解释器（环境变量）没有 matplotlib | safe_stop：报原因，不静默换环境，一行脚本不跑 |
| FO19 | 用户的 `manifest.py` / `overrides.py` 与引擎重名 + 本地包 `lab_utils` | automatic（issue #447） |

safe_stop 的通过**不进**自动兼容成功的分子（校验器按台账预期的结果分开计数）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from support import foundation_app as fa, foundation_harness as fh

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "foundation"

try:
    from tavotto.engine import pool as _pool

    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001 — 没有科学栈就 skip，而 skip 在 CI 校验步里是红
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


# ---------------------------------------------------------------- 小工具


def _truth(name: str) -> dict:
    return json.loads((FIXTURES / name / "truth.json").read_text(encoding="utf-8"))


def _case(case_id: str) -> tuple[dict, dict]:
    ledger = fh.load_ledger()
    case = next(c for c in ledger["cases"] if c["case_id"] == case_id)
    binding = fh.binding_from_environment(
        ledger=ledger, entry=case["entry"], fixture=case["fixture"]
    )
    return case, binding


def _record(
    case_id: str, binding: dict, outcome: str, observed: dict, evidence: list[str], out_dir
):
    record = fh.ResultRecord(
        case_id=case_id,
        binding=binding,
        product_outcome=outcome,
        test_verdict="pass",
        observed=observed,
        evidence=tuple(evidence),
    )
    path = fh.write_result(record, fh.results_dir() or out_dir)
    assert path.is_file()


def _axes_ylim(render: dict) -> list[float]:
    axes = next(e for e in render["manifest"]["elements"] if e["role"] == "axes")
    return next(f["value"] for f in axes["editable"] if f["prop"] == "ylim")


def _title_text(render: dict) -> str:
    title = next(e for e in render["manifest"]["elements"] if e["role"] == "title")
    return next(f["value"] for f in title["editable"] if f["prop"] == "text")


def _expected_ylim(ys: list[float]) -> list[float]:
    margin = 0.05 * (max(ys) - min(ys))
    return [min(ys) - margin, max(ys) + margin]


def _panel(app: fa.RunningApp, file_name: str) -> dict:
    _, panels = app.call("/api/panels", timeout=30)
    return next(p for p in panels["panels"] if p["id"] == file_name)


def _native_reference(project: Path, script: str, tmp: Path) -> None:
    """夹具的原生参考：用户在终端里跑过一次脚本，磁盘上有原件。**不是**产品路径。

    环境只留装载器与系统必需的几个变量（setup-python 在 Linux 上的解释器靠
    ``LD_LIBRARY_PATH`` 找 libpython；Windows 上没有 ``SYSTEMROOT`` 起不来），
    渲染相关的一律不继承——这一步模拟的是用户自己的终端，不是产品。"""
    env = {"PATH": "/usr/bin:/bin", "MPLCONFIGDIR": str(tmp / "mpl"), "MPLBACKEND": "Agg"}
    for name in ("LD_LIBRARY_PATH", "SYSTEMROOT", "TEMP", "TMP"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    proc = subprocess.run(
        [WORKER_PY, script],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]


# ================================================================ FO01：脚本同目录 CSV


@needs_worker
def test_fo01_same_directory_csv_opens_automatically(tmp_path):
    """干净应用设置（空配置、没有环境变量）打开同目录 CSV 的脚本：产品自己选环境、沙盒默认
    就能读到数据（只读回退到脚本目录）、一次确认都不问、图内值 = 真值。"""
    case, binding = _case("FO01")
    truth = _truth("single_file_csv")
    proj = tmp_path / "proj"
    shutil.copytree(FIXTURES / "single_file_csv", proj)
    _native_reference(proj, "figure.py", tmp_path)
    evidence: list[str] = []
    with fa.running_app(proj, tmp_path / "work") as app:
        panel = _panel(app, truth["expected_output"])
        assert panel["capability"]["status"] == "editable"
        state = app.prepare(panel["id"])
        plan, result = state["plan"], state["result"]
        assert result["status"] == "ready", result
        assert plan["required_input"] is None and result["required_input"] is None
        assert plan["workdir_decision"]["needs_confirmation"] is False
        assert plan["workdir_decision"]["evidence"]["verdict"] == "default_ok"
        assert plan["launch_context"]["cwd_origin"] == "sandbox"
        assert plan["grant"]["cwd_write"]["granted"] is False
        # 环境是产品自己选的：计划里有证据（首开发现在这个夹具里找不到 venv——如实记）
        assert plan["environment"]["python"] and plan["environment"]["source"]
        assert plan["environment"]["error"] is None
        assert plan["environment"]["discovery"]["ok"] is False
        assert plan["environment"]["discovery"]["code"] == "project_env_not_found"
        receipt = result["receipt"]
        assert receipt["completeness"] == "complete"
        assert receipt["launch_context"]["cwd_origin"] == "sandbox"
        render = app.render(panel["id"])
        ylim = _axes_ylim(render)
        assert ylim == pytest.approx(_expected_ylim(truth["y"]), abs=1e-6)
        evidence.append(f"prepare ready without input; ylim {ylim} == truth ± 5%")
        # 环境状态与回执自报的解释器版本对得上（独立探针）
        _, envst = app.call("/api/engine/environment", timeout=30)
        chosen = envst["project"]["python"]
        chosen_path = chosen if Path(chosen).is_absolute() else str(proj / chosen)
        ident = fa.independent_identity(chosen_path)
        assert receipt["runtime"]["python_version"] == ident["version"]
        observed = {
            "backend": case.get("backend"),
            "receipt": receipt,
            "plan_environment": plan["environment"],
            "workdir_decision": plan["workdir_decision"],
            "plotted_ylim": ylim,
            "chosen_python": {"source": envst["project"]["source"], "prefix": ident["prefix"]},
        }
    _record("FO01", binding, "automatic", observed, evidence, tmp_path / "results")


# ================================================================ FO03：__file__ + 本地包


_ENTRY_FILE = '''"""U03 FO03 变体：按 `__file__` 定位 `../data/points.csv` + 本地包 `labhelpers`（存图名是常量，
静态扫描能直接把它登记上——夹具原版的 `--out` 参数让输出名只有运行期才知道，那是 needs_probe
那一档，不是本场景要测的东西）。"""
import csv
import os

import labhelpers
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
ts, vs = [], []
with open(os.path.join(PROJECT, "data", "points.csv"), encoding="utf-8", newline="") as fh:
    for row in csv.DictReader(fh):
        ts.append(float(row["t"]))
        vs.append(float(row["v"]))
fig, ax = plt.subplots(figsize=(3.2, 2.4))
ax.plot(ts, vs, "o")
ax.plot(ts, [labhelpers.predict(t) for t in ts], "-")
fig.savefig("entry_file.pdf")
'''


@needs_worker
def test_fo03_file_relative_data_and_local_package_open_automatically(tmp_path):
    """按 `__file__` 找 `../data/points.csv`、import 本地包 `labhelpers`：沙盒默认成立，
    不问；本地包不被当成缺失依赖（不联网、不装）；值 = 真值。"""
    case, binding = _case("FO03")
    truth = _truth("split_scripts_data")
    proj = tmp_path / "proj"
    shutil.copytree(FIXTURES / "split_scripts_data", proj)
    (proj / "scripts" / "entry_file.py").write_text(_ENTRY_FILE, encoding="utf-8")
    # 用户在终端里站在项目根跑过 `python scripts/entry_file.py`（按 __file__ 定位，任何 cwd 都对；
    # 原件落在项目根——`scripts/` 里的 PDF 不算素材，那是既有的素材扫描规则）
    _native_reference(proj, "scripts/entry_file.py", tmp_path)
    evidence: list[str] = []
    with fa.running_app(proj, tmp_path / "work") as app:
        panel = _panel(app, "entry_file.pdf")
        assert panel["script"] == "scripts/entry_file.py"
        state = app.prepare(panel["id"])
        plan, result = state["plan"], state["result"]
        assert result["status"] == "ready", result
        assert plan["required_input"] is None
        # 静态证据：`points.csv` / `data` 是拼出来的，不是单个相对路径字面量——说不出话就
        # 不问（unknown），按默认走；真跑成功了。
        assert plan["workdir_decision"]["needs_confirmation"] is False
        assert plan["launch_context"]["cwd_origin"] == "sandbox"
        receipt = result["receipt"]
        assert receipt["completeness"] == "complete"
        assert receipt["descriptors"][0]["stem"] == "entry_file"
        render = app.render(panel["id"])
        ylim = _axes_ylim(render)
        assert ylim == pytest.approx(_expected_ylim(truth["v"]), abs=1e-6)
        evidence.append(f"__file__ data + labhelpers: ylim {ylim} == truth ± 5%; no input asked")
        # 依赖意图：夹具没有声明文件 → 空列表是事实；`labhelpers` 没被当成包名去装
        assert plan["dependency_intents"] == []
        assert "labhelpers" not in json.dumps(plan["environment"])
        observed = {
            "backend": case.get("backend"),
            "receipt": receipt,
            "plotted_ylim": ylim,
            "workdir_decision": plan["workdir_decision"],
        }
    _record("FO03", binding, "automatic", observed, evidence, tmp_path / "results")


# ================================================================ FO02：scripts/ 与 data/ 分离（歧义）


_ENTRY_CWD = '''"""U03 FO02 变体：读相对路径 `data/points.csv`——只有 cwd = 项目根才成立。"""
import csv

import labhelpers
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ts, vs = [], []
with open("data/points.csv", encoding="utf-8", newline="") as fh:
    for row in csv.DictReader(fh):
        ts.append(float(row["t"]))
        vs.append(float(row["v"]))
fig, ax = plt.subplots(figsize=(3.2, 2.4))
ax.plot(ts, vs, "o")
ax.plot(ts, [labhelpers.predict(t) for t in ts], "-")
fig.savefig("entry_cwd.pdf")
'''


@needs_worker
def test_fo02_scripts_and_data_split_asks_once_then_runs_at_the_project_root(tmp_path):
    """cwd 歧义：数据只有在项目根下才找得到。产品**先问一次**（推荐项目根、不猜、不就近
    替换），选择前不发布图；用户经公开流程选项目根 → 成功，值 = 真值，脚本不变。"""
    case, binding = _case("FO02")
    truth = _truth("split_scripts_data")
    proj = tmp_path / "proj"
    shutil.copytree(FIXTURES / "split_scripts_data", proj)
    (proj / "scripts" / "entry_cwd.py").write_text(_ENTRY_CWD, encoding="utf-8")
    # 用户在终端里站在项目根跑过：`python scripts/entry_cwd.py`
    _native_reference(proj, "scripts/entry_cwd.py", tmp_path)
    script_bytes = (proj / "scripts" / "entry_cwd.py").read_bytes()
    evidence: list[str] = []
    with fa.running_app(proj, tmp_path / "work") as app:
        panel = _panel(app, "entry_cwd.pdf")
        assert panel["script"] == "scripts/entry_cwd.py"
        # 准备：需要输入，一行脚本没跑
        state = app.prepare(panel["id"])
        plan, result = state["plan"], state["result"]
        assert result["status"] == "needs_input", result
        need = result["required_input"]
        assert need["code"] == "workdir_confirmation_required"
        assert need["reason"] == "project_root_evidence"
        assert need["recommended"] == "project_root"
        by_mode = {o["mode"]: o for o in need["options"]}
        assert by_mode["project_root"]["found"] == ["data/points.csv"]
        assert by_mode["project"]["found"] == [] and by_mode["sandbox"]["found"] == []
        assert result["receipt"] is None and result["started_at"] is None
        assert plan["grant"]["cwd_write"]["granted"] is False
        # 渲染入口同一道门：选择前不发布图
        with pytest.raises(fa.HttpError) as blocked:
            app.render(panel["id"])
        assert blocked.value.body["code"] == "workdir_confirmation_required"
        assert blocked.value.body["confirmation"]["recommended"] == "project_root"
        evidence.append(
            "prepare → needs_input(project_root_evidence); render blocked before any run"
        )
        # 用户的那一次选择（公开流程，与界面确认框同一条路）
        _, patched = app.call("/api/engine/workdir", {"mode": "project_root"}, method="PATCH")
        assert patched["workdir"]["mode"] == "project_root"
        assert patched["workdir"]["grant"]["cwd_write"]["granted"] is True
        # 过期的旧计划不能再执行：它记的授权与此刻不同 → 重新准备
        state2 = app.prepare(panel["id"])
        plan2, result2 = state2["plan"], state2["result"]
        assert result2["status"] == "ready", result2
        assert plan2["required_input"] is None
        assert plan2["launch_context"]["cwd_origin"] == "project.root"
        assert plan2["grant"]["cwd_write"]["granted"] is True
        receipt = result2["receipt"]
        assert receipt["launch_context"]["cwd_origin"] == "project.root"
        assert receipt["launch_context"]["write_mode"] == "project_dir"
        render = app.render(panel["id"])
        ylim = _axes_ylim(render)
        assert ylim == pytest.approx(_expected_ylim(truth["v"]), abs=1e-6)
        evidence.append(
            f"after PATCH workdir=project_root: ready, cwd_origin project.root, ylim {ylim}"
        )
        # 二开不再问：决定记住了
        state3 = app.prepare(panel["id"])
        assert state3["result"]["status"] == "ready"
        assert state3["result"]["existing_runtime"] is not None
        observed = {
            "backend": case.get("backend"),
            "required_input": need,
            "receipt": receipt,
            "plotted_ylim": ylim,
            "grant_after": patched["workdir"]["grant"],
        }
    assert (proj / "scripts" / "entry_cwd.py").read_bytes() == script_bytes, "脚本不许被改"
    _record("FO02", binding, "guided", observed, evidence, tmp_path / "results")


# ================================================================ FO07：同名干扰数据（歧义）


@needs_worker
def test_fo07_same_name_data_in_two_places_asks_and_honours_the_choice(tmp_path):
    """脚本目录与项目根各有一份 `data.csv`、内容不同、都画得出图——只有数值能分。
    产品必须先问（没有推荐、机器不裁决），选择前不发布图；之后值 = 选的那份：
    选脚本目录 → [7, 13, 25]；选项目根 → 干扰那份 [601, 1201, 2401]。"""
    case, binding = _case("FO07")
    truth = _truth("same_name_data")
    proj = tmp_path / "proj"
    proj.mkdir()
    shutil.copytree(FIXTURES / "same_name_data", proj / "sub")
    shutil.copy(FIXTURES / "same_name_data" / "decoy" / "data.csv", proj / "data.csv")
    shutil.rmtree(proj / "sub" / "decoy")
    _native_reference(proj / "sub", "plot.py", tmp_path)
    evidence: list[str] = []
    with fa.running_app(proj, tmp_path / "work") as app:
        panel = _panel(app, "sub/plot.pdf")
        assert panel["script"] == "sub/plot.py"
        state = app.prepare(panel["id"])
        result = state["result"]
        assert result["status"] == "needs_input", result
        need = result["required_input"]
        assert need["reason"] == "ambiguous_data"
        assert need["recommended"] is None, "歧义时不预选——机器不裁决"
        assert need["conflicts"] == ["data.csv"]
        assert not any(o["recommended"] for o in need["options"])
        with pytest.raises(fa.HttpError) as blocked:
            app.render(panel["id"])
        assert blocked.value.body["code"] == "workdir_confirmation_required"
        evidence.append("ambiguous_data: no recommendation; render blocked before choice")
        # 选脚本目录 → 正确那份
        app.call("/api/engine/workdir", {"mode": "project"}, method="PATCH")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        assert state["plan"]["launch_context"]["cwd_origin"] == "script.parent"
        ylim_correct = _axes_ylim(app.render(panel["id"]))
        assert ylim_correct == pytest.approx(_expected_ylim(truth["correct"]["y"]), abs=1e-6)
        # 换成项目根 → 干扰那份（值必须等于选的，不许就近替换成「看起来对」的）
        app.call("/api/engine/workdir", {"mode": "project_root"}, method="PATCH")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        assert state["plan"]["launch_context"]["cwd_origin"] == "project.root"
        ylim_decoy = _axes_ylim(app.render(panel["id"]))
        assert ylim_decoy == pytest.approx(_expected_ylim(truth["decoy"]["y"]), abs=1e-6)
        assert ylim_correct != ylim_decoy
        evidence.append(
            f"choice honoured: script dir → {ylim_correct} (truth), project root → {ylim_decoy} (decoy)"
        )
        observed = {
            "backend": case.get("backend"),
            "required_input": need,
            "ylim_by_choice": {"project": ylim_correct, "project_root": ylim_decoy},
            "truth": {"correct": truth["correct"]["y"], "decoy": truth["decoy"]["y"]},
        }
    _record("FO07", binding, "guided", observed, evidence, tmp_path / "results")


# ================================================================ FO19：重名引擎模块 + 本地包


@needs_worker
def test_fo19_user_modules_shadow_engine_names_and_still_win(tmp_path):
    """用户的 `manifest.py` / `overrides.py` 与引擎重名，`lab_utils` 是本地包：脚本 import 到的
    是用户那份（标题三个 sentinel 齐全），值 = 真值；没有任何东西被当成包名去装。
    修法：safe worker 经 bridgeboot 把引擎模块装进私有包（issue #447）。"""
    case, binding = _case("FO19")
    truth = _truth("shadowed_engine_modules")
    proj = tmp_path / "proj"
    shutil.copytree(FIXTURES / "shadowed_engine_modules", proj)
    _native_reference(proj, "figure.py", tmp_path)
    evidence: list[str] = []
    with fa.running_app(proj, tmp_path / "work") as app:
        panel = _panel(app, truth["expected_output"])
        state = app.prepare(panel["id"])
        plan, result = state["plan"], state["result"]
        assert result["status"] == "ready", result
        assert plan["required_input"] is None
        render = app.render(panel["id"])
        assert _title_text(render) == truth["sentinel_title"]
        ylim = _axes_ylim(render)
        assert ylim == pytest.approx(_expected_ylim(truth["y"]), abs=1e-6)
        evidence.append(f"title == {truth['sentinel_title']!r}; ylim {ylim} == truth ± 5%")
        # 引擎那份 manifest 仍在私有包里工作（manifest 生成成功、元素完整）——不是「两份都是用户的」
        assert len(render["manifest"]["elements"]) >= 3
        observed = {
            "backend": case.get("backend"),
            "receipt": result["receipt"],
            "title": _title_text(render),
            "plotted_ylim": ylim,
        }
    _record("FO19", binding, "automatic", observed, evidence, tmp_path / "results")


# ================================================================ FO15：显式环境失效 → safe_stop


def _bare_venv(tmp: Path) -> str:
    """一个**真实**的、没有 matplotlib 的解释器：`python -m venv`（不装任何包）。"""
    venv = tmp / "bare-venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=300)
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    probe = subprocess.run(
        [str(python), "-c", "import matplotlib"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert probe.returncode != 0, "前提：这个 venv 里不该有 matplotlib"
    return str(python)


@needs_worker
def test_fo15_explicit_interpreter_without_matplotlib_stops_with_a_reason(tmp_path):
    """用户显式指定 A（`TAVOTTO_WORKER_PYTHON`），A 不满足约束（没有 matplotlib）：产品
    **停下来说原因**，不静默换一个能 import 的 Python、不装包、不执行脚本；静态原件照常可见。"""
    case, binding = _case("FO15")
    truth = _truth("single_file_csv")
    proj = tmp_path / "proj"
    shutil.copytree(FIXTURES / "single_file_csv", proj)
    _native_reference(proj, "figure.py", tmp_path)
    bare = _bare_venv(tmp_path)
    evidence: list[str] = []
    with fa.running_app(
        proj, tmp_path / "work", env_overrides={"TAVOTTO_WORKER_PYTHON": bare}
    ) as app:
        panel = _panel(app, truth["expected_output"])
        assert panel["capability"]["status"] == "editable"  # 静态原件与脚本关系仍在
        _, envst = app.call("/api/engine/environment", timeout=30)
        err = envst["project"]["resolution_error"]
        assert err and err["code"] == "explicit_python_unusable"
        assert err["explicit"]["source"] == "env_override"
        assert err["explicit"]["reason"] == "no_matplotlib"
        state = app.prepare(panel["id"])
        plan, result = state["plan"], state["result"]
        assert plan["environment"]["error"]["code"] == "explicit_python_unusable"
        assert plan["environment"]["error"]["explicit"]["source"] == "env_override"
        assert plan["environment"]["python"] == ""  # 没有擅自选别的
        assert result["status"] == "error", result
        assert result["error"]["code"] == "explicit_python_unusable"
        assert result["receipt"] is None and result["created_runtime"] is False
        with pytest.raises(fa.HttpError) as blocked:
            app.render(panel["id"])
        assert blocked.value.body["code"] == "explicit_python_unusable"
        assert blocked.value.body["explicit"]["source"] == "env_override"
        evidence.append(
            "explicit env_override without matplotlib → explicit_python_unusable; no run"
        )
        observed = {
            "backend": case.get("backend"),
            "resolution_error": err,
            "plan_environment": plan["environment"],
            "result": {"status": result["status"], "error": result["error"]},
        }
    # 没有副作用：夹具里除了原生参考的产物什么都没多；bare venv 里没被装任何东西
    assert not any(p.suffix == ".pyc" for p in proj.rglob("*") if p.is_file())
    site = subprocess.run(
        [
            bare,
            "-c",
            "import importlib.metadata as m; print(sorted(d.metadata['Name'] for d in m.distributions()))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    ).stdout
    assert "matplotlib" not in site.lower()
    _record("FO15", binding, "safe_stop", observed, evidence, tmp_path / "results")
