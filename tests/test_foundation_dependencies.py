"""统一实施包 U04：联合依赖准备的首开场景（FirstOpenBench，经真实公共入口）。

每条场景：把合成夹具 ⑧ 复制成「用户本来就有」的项目（**项目自带一个真 venv**，matplotlib 来自
宿主 site-packages——`support.venvfixture`，一个字节都不下载；脚本要的三个包只存在于用例现造的
离线 wheelhouse 里，**harness 不预装它们**）→ `python -m tavotto`（会话认证默认开、用户配置空、
没有 `TAVOTTO_WORKER_PYTHON`）→ 准备接口 → 跑前的门以 `dependency_preparation_required` 回来
（这是「需要输入」）→ 用户经公开的 `POST /api/engine/dependencies/plan` + `/prepare` 授权**一次**
→ 装完再准备 → ready → 渲染 → 图内值 = 真值 → 一次真实编辑。

| case | 场景 | 预期产品结果 |
|---|---|---|
| FO20 | markers / 未选的组：只准备所选且适用的三条（extra 拉进第四个包），marker 为假与未选组的不装 | guided |
| FO31 | 首开 / 二开 / 会话重启：装一次；二开 `existing_runtime`；重启应用后不重复准备 | automatic（首开那一次授权之后） |
| FO21 | 声明矛盾：计划 blocked、不建代不装、旧环境原样；渲染以 `missing_dependency` + 诊断收场 | safe_stop |
| FO22 | 装上了但 import 不了（wheel 的模块 import 一个不存在的原生库）：验证挡住、有界退出 | safe_stop |
| FO27 | 安装期间取消：明确终态、环境没变、能再来 | safe_stop |
| FO18（observing，联网） | 项目没有 venv：受管环境**新的一代**（真装 matplotlib + 三个包）一次成功 | guided |
| FO05（observing，联网） | 真实 h5py：原生库真正写 / 读 HDF5 并出已知数值 | automatic |

目标是**项目自己的 venv**（它就是首开选中的解释器）——FO20 / 21 / 22 / 27 / 31 在离线 CI 里能真跑；
受管环境那条路要联网装 matplotlib，本文件只在 `TAVOTTO_FOUNDATION_ONLINE=1` 时真跑（FO18 / FO05，
nightly 的 `foundation-observing` job），本机 / PR 上 skip 并说明——skip 不是绿。受管环境事务的
机制面由 `tests/test_dependency_transaction.py` 离线真跑。safe_stop 的通过**不进**自动兼容成功的分子。
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from support import foundation_app as fa, foundation_harness as fh
from support.dependency_repair import build_wheel, needs_worker, real_venv

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "foundation"
ONLINE_ENV = "TAVOTTO_FOUNDATION_ONLINE"

pytest_plugins = ("support.dependency_repair",)

ALPHA = ("tavotto-test-alpha", "tavotto_test_alpha")
BETA = ("tavotto-test-beta", "tavotto_test_beta")
GAMMA = ("tavotto-test-gamma", "tavotto_test_gamma")
WIDE = ("tavotto-test-wide", "tavotto_test_wide")
TRAIN = ("tavotto-test-train", "tavotto_test_train")
NEVER = ("tavotto-test-never", "tavotto_test_never")
BROKEN = ("tavotto-test-broken", "tavotto_test_broken")


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


def _importable(python: str, name: str) -> bool:
    proc = subprocess.run(
        [python, "-c", f"import {name}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return proc.returncode == 0


def _freeze(python: str) -> str:
    return subprocess.run(
        [python, "-m", "pip", "freeze", "--disable-pip-version-check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    ).stdout


def _wheelhouse(dest: Path) -> Path:
    """夹具 ⑧ 的 wheel（与 `test_dependency_transaction.py` 同一组名字 / 版本）+ 一个 import 就炸的。"""
    build_wheel(dest, name=WIDE[0], import_name=WIDE[1], version="1.0")
    build_wheel(
        dest,
        name=ALPHA[0],
        import_name=ALPHA[1],
        version="1.0",
        provides_extras=("wide",),
        requires=(f'{WIDE[0]}; extra == "wide"',),
    )
    build_wheel(dest, name=BETA[0], import_name=BETA[1], version="1.0")
    build_wheel(dest, name=BETA[0], import_name=BETA[1], version="2.0")
    build_wheel(dest, name=GAMMA[0], import_name=GAMMA[1], version="1.0")
    build_wheel(dest, name=TRAIN[0], import_name=TRAIN[1], version="1.0")
    build_wheel(dest, name=NEVER[0], import_name=NEVER[1], version="1.0")
    # FO22：装得上、import 不了——模块体 import 一个不存在的原生库（真实 ImportError）
    build_wheel(
        dest,
        name=BROKEN[0],
        import_name=BROKEN[1],
        version="1.0",
        body="import _tavotto_test_missing_native_lib  # noqa: F401\n",
    )
    return dest


@pytest.fixture
def house(tmp_path, monkeypatch) -> Path:
    """离线 wheelhouse；`PIP_FIND_LINKS` / `PIP_NO_INDEX` 经环境传给服务子进程——安装命令不为测试改。"""
    dest = _wheelhouse(tmp_path / "house")
    monkeypatch.setenv("PIP_FIND_LINKS", str(dest))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return dest


def _project(tmp_path, *, with_venv: bool = True) -> Path:
    """夹具 ⑧ 复制成用户项目；`with_venv` 时带一个真 venv（matplotlib 来自宿主，包一个不装）。"""
    proj = tmp_path / "proj"
    shutil.copytree(FIXTURES / "joint_dependencies", proj)
    if with_venv:
        real_venv(proj)
    return proj


def _native_reference(proj: Path, tmp: Path, script: str = "figure.py", *, modules=None) -> None:
    """夹具的原生参考：用户在别处跑过一次脚本，磁盘上有原件（素材库据此列出面板）。**不是**产品路径，
    也不装任何东西：脚本要的那几个模块以 `.py` 文件放在一个临时目录里经 `PYTHONPATH` 提供
    （与 wheel 里的模块体逐字相同），目标 venv 一个字节不碰。"""
    mods = tmp / "native-mods"
    mods.mkdir(parents=True, exist_ok=True)
    for name in modules or (ALPHA[1], BETA[1], GAMMA[1]):
        (mods / f"{name}.py").write_text(f'VALUE = 42\nNAME = "{name}"\n', encoding="utf-8")
    from support.dependency_repair import WORKER_PY

    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(mods),
        "MPLCONFIGDIR": str(tmp / "mpl"),
        "MPLBACKEND": "Agg",
    }
    proc = subprocess.run(
        [WORKER_PY, script],
        cwd=proj,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    shutil.rmtree(proj / "__pycache__", ignore_errors=True)


def _venv_python(proj: Path) -> str:
    return str(proj / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"))


def _authorize(app: fa.RunningApp, script: str, target: str, *, timeout: float = 600.0) -> dict:
    """用户的那一次授权（公开流程，与授权框同一条路）：绑定计划 → 只发 plan_id → 轮询到终态。"""
    _, planned = app.call(
        "/api/engine/dependencies/plan", {"script": script, "target": target}, timeout=60
    )
    plan = planned["plan"]
    _, started = app.call(
        "/api/engine/dependencies/prepare", {"plan_id": plan["plan_id"]}, timeout=30
    )
    assert started["started"] is True
    deadline = time.time() + timeout
    while True:
        _, state = app.call(f"/api/engine/dependency/state?plan_id={plan['plan_id']}", timeout=30)
        if state["state"] in ("done", "failed", "cancelled"):
            return {"plan": plan, "final": state}
        assert time.time() < deadline, state
        time.sleep(0.3)


def _edit_title(app: fa.RunningApp, panel_id: str, render: dict) -> str:
    """一次真实编辑（05 §3：可见 patch 且数据不变）：改 ylabel 的文字 → 重放 → 读回。"""

    def _ylabel(elements):
        return next(
            e
            for e in elements
            if any(f["prop"] == "text" and f.get("value") == "y" for f in e.get("editable", []))
        )

    ylabel = _ylabel(render["manifest"]["elements"])
    patch = {"gid": ylabel["gid"], "prop": "text", "value": "U04 edited"}
    after = app.render(panel_id, [patch])
    edited = next(e for e in after["manifest"]["elements"] if e["gid"] == ylabel["gid"])
    return next(f["value"] for f in edited["editable"] if f["prop"] == "text")


# ================================================================ FO20：markers / extras / 所选组


@needs_worker
def test_fo20_markers_extras_and_selected_groups_prepare_only_what_applies(tmp_path, house):
    """跑前的门以整份计划回来：要装的 = 三条选中且适用的声明（extra 拉进第四个包）；marker 为假
    与未选组的**不装**（wheelhouse 里有也不装）；一次授权 → ready → 值 = 真值 → 一次编辑。"""
    case, binding = _case("FO20")
    truth = _truth("joint_dependencies")
    proj = _project(tmp_path)
    _native_reference(proj, tmp_path)
    python = _venv_python(proj)
    for name in ("alpha", "beta", "gamma", "wide", "never", "train"):
        assert not _importable(python, f"tavotto_test_{name}"), "harness 不许预装"
    before = _freeze(python)
    evidence: list[str] = []
    with fa.running_app(proj, tmp_path / "work") as app:
        panel = _panel(app, truth["expected_output"])
        assert panel["capability"]["status"] == "editable"
        state = app.prepare(panel["id"])
        plan, result = state["plan"], state["result"]
        assert result["status"] == "needs_input", result
        need = result["required_input"]
        assert need["code"] == "dependency_preparation_required"
        joint = need["plan"]
        assert joint["status"] == "ready"
        assert joint["requirements"] == [f"{ALPHA[0]}[wide]==1.0", f"{BETA[0]}<2", GAMMA[0]]
        assert [m["distribution"] for m in joint["missing"]] == [ALPHA[0], BETA[0], GAMMA[0]]
        assert joint["selection"]["selected_groups"] == ["requirements.txt"]
        assert "requirements-train.txt" in joint["selection"]["unselected_groups"]
        assert [s["marker"] for s in joint["selection"]["skipped_marker"]] == [
            'sys_platform == "never_os"'
        ]
        assert need["target_kind"] == "project_venv"
        assert result["receipt"] is None and result["started_at"] is None  # 一行脚本没跑
        assert plan["environment"]["source"] == "project_venv"
        # 渲染入口同一道门
        with pytest.raises(fa.HttpError) as blocked:
            app.render(panel["id"])
        assert blocked.value.body["code"] == "dependency_preparation_required"
        assert (
            blocked.value.body["dependency_preparation"]["plan"]["requirements"]
            == joint["requirements"]
        )
        evidence.append(
            "prepare → needs_input(dependency_preparation_required); render blocked before any run"
        )
        # 用户的那一次授权
        done = _authorize(app, "figure.py", "project_venv")
        assert done["final"]["state"] == "done", done
        assert done["plan"]["modifies_user_environment"] is True
        assert done["plan"]["adapter"] == []  # 用户 venv 不并入 adapter
        assert done["final"]["committed"] is True
        # 装的与不装的
        for dist, mod in (ALPHA, BETA, GAMMA, WIDE):
            assert _importable(python, mod), dist
        for dist, mod in (NEVER, TRAIN):
            assert not _importable(python, mod), dist
        assert f"{BETA[0]}==1.0" in _freeze(python)
        # 再准备：ready，脚本跑了一次；值 = 真值
        state2 = app.prepare(panel["id"])
        assert state2["result"]["status"] == "ready", state2["result"]
        assert state2["plan"]["dependency_preparation"]["plan"]["status"] == "nothing_needed"
        receipt = state2["result"]["receipt"]
        assert receipt["completeness"] == "complete"
        render = app.render(panel["id"])
        ylim = _axes_ylim(render)
        assert ylim == pytest.approx(_expected_ylim(truth["y"]), abs=1e-6)
        edited = _edit_title(app, panel["id"], render)
        assert edited == "U04 edited"
        evidence.append(
            f"one authorization installed {ALPHA[0]}, {BETA[0]}==1.0, {GAMMA[0]} (+{WIDE[0]} via extra); "
            f"{NEVER[0]} / {TRAIN[0]} not installed; ylim {ylim} == truth ± 5%; title edited"
        )
        observed = {
            "backend": case.get("backend"),
            "required_input": {k: need[k] for k in ("code", "target_kind", "rounds_remaining")},
            "joint_plan": {
                k: joint[k] for k in ("status", "requirements", "constraints", "selection")
            },
            "prepare_result": done["final"].get("result"),
            "receipt": receipt,
            "plotted_ylim": ylim,
            "freeze_delta": sorted(set(_freeze(python).splitlines()) - set(before.splitlines())),
        }
    _record("FO20", binding, "guided", observed, evidence, tmp_path / "results")


# ================================================================ FO31：首开 / 二开 / 重启不重复准备


@needs_worker
def test_fo31_first_second_open_and_restart_do_not_prepare_again(tmp_path, house):
    """首开授权一次；二开 `existing_runtime`、不再问；关掉应用再开（同一数据目录、同一项目）：
    计划 `nothing_needed`、不再问、不再装（freeze 一字不变）、脚本会话 generation 1 起步。"""
    case, binding = _case("FO31")
    truth = _truth("joint_dependencies")
    proj = _project(tmp_path)
    _native_reference(proj, tmp_path)
    python = _venv_python(proj)
    evidence: list[str] = []
    installs = 0
    with fa.running_app(proj, tmp_path / "work") as app:
        panel = _panel(app, truth["expected_output"])
        assert app.prepare(panel["id"])["result"]["status"] == "needs_input"
        done = _authorize(app, "figure.py", "project_venv")
        assert done["final"]["state"] == "done", done
        installs += 1
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready"
        gen1 = state["result"]["receipt"]["generation"]
        ylim = _axes_ylim(app.render(panel["id"]))
        assert ylim == pytest.approx(_expected_ylim(truth["y"]), abs=1e-6)
        # 二开：现有 runtime，一个字节不发
        state2 = app.prepare(panel["id"])
        assert state2["result"]["status"] == "ready"
        assert state2["result"]["existing_runtime"] is not None
        assert state2["result"]["existing_runtime"]["generation"] == gen1
        assert state2["result"]["created_runtime"] is False
        evidence.append(
            f"first open: 1 authorization; second open: existing_runtime generation {gen1}"
        )
    frozen = _freeze(python)
    # 会话重启：新进程、同一数据目录与项目
    with fa.running_app(proj, tmp_path / "work") as app:
        panel = _panel(app, truth["expected_output"])
        _, offer = app.call("/api/engine/dependencies?script=figure.py", timeout=60)
        assert offer["offer"]["plan"]["status"] == "nothing_needed"
        state3 = app.prepare(panel["id"])
        assert state3["result"]["status"] == "ready", state3["result"]
        assert state3["plan"]["required_input"] is None
        assert state3["plan"]["environment"]["source"] == "project_venv"
        assert state3["result"]["existing_runtime"] is None  # 新进程：池是空的，会话是新起的
        assert state3["result"]["created_runtime"] is True
        assert state3["result"]["receipt"]["generation"] == 1
        ylim2 = _axes_ylim(app.render(panel["id"]))
        assert ylim2 == pytest.approx(_expected_ylim(truth["y"]), abs=1e-6)
    assert _freeze(python) == frozen, "重启之后不许再装任何东西"
    evidence.append("restart: plan nothing_needed, no input asked, freeze unchanged, generation 1")
    observed = {
        "backend": case.get("backend"),
        "installs": installs,
        "plotted_ylim": ylim2,
        "restart_receipt": state3["result"]["receipt"],
    }
    _record("FO31", binding, "automatic", observed, evidence, tmp_path / "results")


# ================================================================ FO21：声明矛盾 → safe_stop


@needs_worker
def test_fo21_conflicting_declarations_stop_and_keep_the_environment(tmp_path, house):
    """`requirements.txt` 的 `beta<2` 与 `constraints.txt` 的 `beta>=2` 确定矛盾：计划 blocked、
    绑定被拒（409 + 计划）、门不拦、脚本照跑并以 `missing_dependency` + 同一份诊断收场；
    环境一个字节没改、声明文件没改。"""
    case, binding = _case("FO21")
    proj = _project(tmp_path)
    _native_reference(proj, tmp_path)
    (proj / "constraints.txt").write_text(f"{BETA[0]}>=2\n", encoding="utf-8")
    python = _venv_python(proj)
    before = _freeze(python)
    decl_bytes = {p.name: p.read_bytes() for p in proj.glob("*.txt")}
    evidence: list[str] = []
    with fa.running_app(proj, tmp_path / "work") as app:
        panel = _panel(app, "figure.pdf")
        state = app.prepare(panel["id"])
        plan, result = state["plan"], state["result"]
        dependency = plan["dependency_preparation"]
        assert dependency["plan"]["status"] == "blocked"
        assert [b["code"] for b in dependency["plan"]["blocked"]] == ["dependency_conflict"]
        assert dependency["plan"]["blocked"][0]["conflicts"][0]["name"] == BETA[0]
        assert plan["required_input"] is None  # blocked 不问：门放行
        assert result["status"] == "error", result
        assert result["error"]["code"] == "missing_dependency"
        with pytest.raises(fa.HttpError) as refused:
            app.call(
                "/api/engine/dependencies/plan",
                {"script": "figure.py", "target": "project_venv"},
                timeout=60,
            )
        assert refused.value.status == 409
        assert refused.value.body["code"] == "dependency_plan_blocked"
        assert refused.value.body["joint"]["status"] == "blocked"
        evidence.append(
            "blocked: dependency_conflict visible; plan binding refused (409); no install"
        )
        observed = {
            "backend": case.get("backend"),
            "blocked": dependency["plan"]["blocked"],
            "result_error": result["error"]["code"],
        }
    assert _freeze(python) == before
    assert {p.name: p.read_bytes() for p in proj.glob("*.txt")} == decl_bytes
    for dist, mod in (ALPHA, BETA, GAMMA):
        assert not _importable(python, mod), dist
    _record("FO21", binding, "safe_stop", observed, evidence, tmp_path / "results")


# ================================================================ FO22：装上了但 import 不了 → safe_stop


@needs_worker
def test_fo22_installed_but_unimportable_is_caught_by_verification(tmp_path, house):
    """包装得上（pip 退出 0）、但模块 import 一个不存在的原生库：关键 import 验证挡住 →
    `dependency_import_still_failed`，明确终态；门只问一次、轮次可见；不无限装 / 重跑。"""
    case, binding = _case("FO22")
    proj = _project(tmp_path)
    (proj / "requirements.txt").write_text(f"{BROKEN[0]}\n", encoding="utf-8")
    (proj / "figure.py").write_text(
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        f"import {BROKEN[1]}\nfig, ax = plt.subplots()\nax.plot([1, 2], [1, 2])\nfig.savefig('figure.pdf')\n",
        encoding="utf-8",
    )
    _native_reference(proj, tmp_path, modules=(BROKEN[1],))  # 参考用一个能 import 的替身出原件
    python = _venv_python(proj)
    evidence: list[str] = []
    with fa.running_app(proj, tmp_path / "work") as app:
        panel = _panel(app, "figure.pdf")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "needs_input"
        rounds_before = state["result"]["required_input"]["rounds_remaining"]
        done = _authorize(app, "figure.py", "project_venv")
        assert done["final"]["state"] == "failed", done
        assert done["final"]["code"] == "dependency_import_still_failed"
        # 真实 ImportError 出现在诊断里；用户 venv 上的安装只进不退（如实：包文件在，import 不了）
        assert "_tavotto_test_missing_native_lib" in (done["final"].get("error") or "")
        # 有界：失败不消耗轮次；门一直问到有答案——用户明确「不准备，直接跑」之后再准备 = 真跑一次
        # → missing_dependency（运行后那条路，同一份 offer）
        state_again = app.prepare(panel["id"])
        assert state_again["result"]["status"] == "needs_input"
        _, skipped = app.call("/api/engine/dependencies/skip", {"script": "figure.py"}, timeout=30)
        assert skipped["skipped"] is True
        state2 = app.prepare(panel["id"])
        assert state2["result"]["status"] == "error"
        assert state2["result"]["error"]["code"] == "missing_dependency"
        _, offer = app.call("/api/engine/dependencies?script=figure.py", timeout=60)
        assert offer["rounds_remaining"] == rounds_before
        assert offer["offer"]["skipped"] is True
        evidence.append(
            "pip exit 0 but key import fails → dependency_import_still_failed; door asks until answered; after explicit skip one run ends in missing_dependency; rounds unchanged"
        )
        observed = {
            "backend": case.get("backend"),
            "final": {k: done["final"].get(k) for k in ("state", "code")},
            "rounds_remaining": offer["rounds_remaining"],
        }
    _record("FO22", binding, "safe_stop", observed, evidence, tmp_path / "results")


# ================================================================ FO27：安装期间取消 → safe_stop


class _SlowWheelHandler(http.server.SimpleHTTPRequestHandler):
    """把 wheelhouse 当 find-links 目录页提供；`.whl` 的下载先睡一会儿（给取消留窗口）。"""

    delay = 6.0

    def do_GET(self):  # noqa: N802
        if self.path.endswith(".whl"):
            time.sleep(self.delay)
        return super().do_GET()

    def log_message(self, *args):  # 安静
        return


@pytest.fixture
def slow_house(tmp_path, monkeypatch):
    dest = _wheelhouse(tmp_path / "house")
    handler = functools.partial(_SlowWheelHandler, directory=str(dest))
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    monkeypatch.setenv("PIP_FIND_LINKS", url)
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    try:
        yield url
    finally:
        httpd.shutdown()
        httpd.server_close()


@needs_worker
def test_fo27_cancel_during_install_is_a_clean_terminal_state(tmp_path, slow_house):
    """下载 / 安装期间取消：终态 `cancelled`、后续用户脚本没跑、环境没变（pip 被杀在装之前）、
    能再来一次（第二次不取消 → done）。"""
    case, binding = _case("FO27")
    truth = _truth("joint_dependencies")
    proj = _project(tmp_path)
    _native_reference(proj, tmp_path)
    python = _venv_python(proj)
    before = _freeze(python)
    evidence: list[str] = []
    with fa.running_app(proj, tmp_path / "work") as app:
        panel = _panel(app, truth["expected_output"])
        assert app.prepare(panel["id"])["result"]["status"] == "needs_input"
        _, planned = app.call(
            "/api/engine/dependencies/plan",
            {"script": "figure.py", "target": "project_venv"},
            timeout=60,
        )
        plan_id = planned["plan"]["plan_id"]
        app.call("/api/engine/dependencies/prepare", {"plan_id": plan_id}, timeout=30)
        deadline = time.time() + 60
        while True:
            _, st = app.call(f"/api/engine/dependency/state?plan_id={plan_id}", timeout=30)
            if st["state"] == "installing":
                break
            assert st["state"] in ("preparing", "idle"), st
            assert time.time() < deadline
            time.sleep(0.2)
        time.sleep(0.5)  # pip 已经起来、正在慢慢下载
        _, cancel = app.call("/api/engine/dependencies/cancel", {"plan_id": plan_id}, timeout=30)
        assert cancel["accepted"] is True and cancel["reason"] == ""
        deadline = time.time() + 60
        while True:
            _, st = app.call(f"/api/engine/dependency/state?plan_id={plan_id}", timeout=30)
            if st["state"] in ("cancelled", "done", "failed"):
                break
            assert time.time() < deadline, st
            time.sleep(0.2)
        assert st["state"] == "cancelled" and st["code"] == "dependency_install_cancelled", st
        assert st.get("committed") is not True
        assert _freeze(python) == before, "取消后环境一字未变"
        for dist, mod in (ALPHA, BETA, GAMMA):
            assert not _importable(python, mod), dist
        # 一行用户脚本没跑：再准备仍是「需要输入」（门一直问到有答案）；计划一次性
        assert app.prepare(panel["id"])["result"]["status"] == "needs_input"
        _, cancelled_again = app.call(
            "/api/engine/dependencies/cancel", {"plan_id": plan_id}, timeout=30
        )
        assert cancelled_again["accepted"] is False and cancelled_again["reason"] == "not_found"
        evidence.append(
            "cancel accepted during installing → cancelled; freeze unchanged; plan consumed; door still asks"
        )
        observed = {
            "backend": case.get("backend"),
            "final": {k: st.get(k) for k in ("state", "code")},
            "freeze_unchanged": True,
        }
    _record("FO27", binding, "safe_stop", observed, evidence, tmp_path / "results")


# ================================================================ FO18（observing）：受管环境新的一代


needs_online = pytest.mark.skipif(
    os.environ.get(ONLINE_ENV) != "1",
    reason=f"要联网装 matplotlib 进受管环境：{ONLINE_ENV}=1 才跑（nightly foundation-observing）",
)


def _download_scientific_stack(house: Path) -> None:
    """把 adapter 需要的 matplotlib / numpy 的 wheel 下到 wheelhouse（联网；只下载不安装）。"""
    from tavotto.engine import depplan

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "-d",
            str(house),
            *depplan.ADAPTER_REQUIREMENTS,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        env={k: v for k, v in os.environ.items() if k not in ("PIP_NO_INDEX", "PIP_FIND_LINKS")},
    )


@needs_worker
@needs_online
def test_fo18_three_packages_prepare_into_a_managed_generation(tmp_path, house):
    """项目**没有** venv：跑前的门 → 目标是受管环境 → 一次授权建**新的一代**（真装 matplotlib +
    numpy + 三个包）→ 验证通过切 active → 再准备 ready → 值 = 真值 → 一次编辑。"""
    case, binding = _case("FO18")
    truth = _truth("joint_dependencies")
    _download_scientific_stack(house)
    proj = _project(tmp_path, with_venv=False)
    _native_reference(proj, tmp_path)
    evidence: list[str] = []
    with fa.running_app(proj, tmp_path / "work") as app:
        panel = _panel(app, truth["expected_output"])
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "needs_input", state["result"]
        need = state["result"]["required_input"]
        assert need["target_kind"] == "tavotto_managed"
        assert need["plan"]["adapter"] != []
        done = _authorize(app, "figure.py", "tavotto_managed", timeout=1500)
        assert done["final"]["state"] == "done", done
        assert done["final"]["result"]["activated"] is True
        generation = done["final"]["result"]["generation"]
        state2 = app.prepare(panel["id"])
        assert state2["result"]["status"] == "ready", state2["result"]
        assert state2["plan"]["environment"]["source"] == "managed_project_env"
        render = app.render(panel["id"])
        ylim = _axes_ylim(render)
        assert ylim == pytest.approx(_expected_ylim(truth["y"]), abs=1e-6)
        assert _edit_title(app, panel["id"], render) == "U04 edited"
        _, envst = app.call("/api/engine/environment", timeout=30)
        assert envst["project"]["managed"]["active_generation"] == generation
        assert envst["project"]["source"] == "managed_project_env"
        evidence.append(
            f"managed generation {generation} built from base python, activated after verification; ylim {ylim}"
        )
        observed = {
            "backend": case.get("backend"),
            "generation": generation,
            "installed": done["final"]["result"].get("installed"),
            "receipt": state2["result"]["receipt"],
            "plotted_ylim": ylim,
        }
    _record("FO18", binding, "guided", observed, evidence, tmp_path / "results")


# ================================================================ FO05（observing）：真实 h5py


_H5PY_SCRIPT = '''"""FO05：真实 h5py——原生库真正写 / 读 HDF5 并出已知数值（不是 Python 的 open 补丁能替代的）。"""
import h5py
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

with h5py.File("values.h5", "w") as f:
    f.create_dataset("y", data=np.array([1.0, 2.0, 3.0]) * 3.5)
with h5py.File("values.h5", "r") as f:
    ys = [float(v) for v in f["y"][...]]
assert ys == [3.5, 7.0, 10.5], ys
fig, ax = plt.subplots(figsize=(3.2, 2.4))
ax.plot([1, 2, 3], ys, marker="o")
fig.savefig("h5figure.pdf")
'''


@needs_worker
@needs_online
def test_fo05_real_h5py_reads_hdf5_in_the_prepared_environment(tmp_path, house):
    """项目声明 `h5py`（真实二进制 wheel，联网取）；一次授权装进受管环境的新一代；脚本用原生
    HDF5 写 / 读并出图；值 = 真值。**纯 Python 替身不算**：这条要真的 h5py。"""
    case, binding = _case("FO05")
    _download_scientific_stack(house)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "-d",
            str(house),
            "h5py",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        env={k: v for k, v in os.environ.items() if k not in ("PIP_NO_INDEX", "PIP_FIND_LINKS")},
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "requirements.txt").write_text("h5py\n", encoding="utf-8")
    (proj / "h5figure.py").write_text(_H5PY_SCRIPT, encoding="utf-8")
    evidence: list[str] = []
    with fa.running_app(proj, tmp_path / "work") as app:
        _, panels = app.call("/api/panels", timeout=30)
        _, probe = app.call("/api/registry/probe", {"script": "h5figure.py"}, timeout=120)
        assert probe.get("error", {}).get("code") == "dependency_preparation_required", probe
        done = _authorize(app, "h5figure.py", "tavotto_managed", timeout=1500)
        assert done["final"]["state"] == "done", done
        _, probe2 = app.call("/api/registry/probe", {"script": "h5figure.py"}, timeout=600)
        assert probe2.get("ok") or probe2.get("error") is None, probe2
        # 原生参考：用产品准备好的那一代跑一次脚本，磁盘上有原件后素材库才列出面板（h5py 在别处
        # 都没有，参考只能用它；参考本身是真 h5py 写 / 读 HDF5）
        _, envst = app.call("/api/engine/environment", timeout=30)
        managed_python = envst["project"]["python"]
        assert Path(managed_python).is_absolute(), envst["project"]
        subprocess.run(
            [managed_python, "h5figure.py"],
            cwd=proj,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            env={
                "PATH": os.environ.get("PATH", ""),
                "MPLCONFIGDIR": str(tmp_path / "mpl"),
                "MPLBACKEND": "Agg",
            },
        )
        deadline = time.time() + 60
        while True:
            _, panels = app.call("/api/panels", timeout=30)
            if any(p["id"] == "h5figure.pdf" for p in panels["panels"]):
                break
            assert time.time() < deadline, panels
            time.sleep(0.5)
        panel = _panel(app, "h5figure.pdf")
        render = app.render(panel["id"])
        ylim = _axes_ylim(render)
        assert ylim == pytest.approx(_expected_ylim([3.5, 7.0, 10.5]), abs=1e-6)
        evidence.append(f"real h5py wrote and read values.h5; ylim {ylim} == truth ± 5%")
        observed = {
            "backend": case.get("backend"),
            "plotted_ylim": ylim,
            "installed": done["final"]["result"].get("installed"),
        }
    _record("FO05", binding, "automatic", observed, evidence, tmp_path / "results")
