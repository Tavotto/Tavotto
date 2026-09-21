"""首开的工作目录决定（U03，ADR 0057）：`workdir.decision_for / resolve_mode`、`project_root`
模式与 `pool` 里那道门。

判据的主语：**这个项目 × 这个脚本**在起第一个 worker 之前的决定——决定过就用记住的；
没决定过按静态证据要不要问；问就抛结构化的「需要输入」，不猜、不就近、不自动切真实 cwd。
真 worker 的用例只跑 `project_root` 这一档新东西（`project` 档在 `test_workdir_mode.py`）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tavotto.engine import (
    config as engine_config,
    execspec,
    pool as engine_pool,
    workdir,
)

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

ROOT_ONLY = "import matplotlib.pyplot as plt\nopen('data/x.csv').read()\nfig, ax = plt.subplots()\nax.plot([1])\nfig.savefig('fig.pdf')\n"
SAME_DIR = "import matplotlib.pyplot as plt\nopen('x.csv').read()\nfig, ax = plt.subplots()\nax.plot([1])\nfig.savefig('fig.pdf')\n"
NO_READS = "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot([1])\nfig.savefig('fig.pdf')\n"


def _project(tmp_path: Path, script: str, files: dict[str, str], *, name: str = "proj") -> Path:
    root = tmp_path / name
    (root / "s").mkdir(parents=True)
    (root / "s" / "fig.py").write_text(script, encoding="utf-8")
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content, encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _clean_pool():
    engine_pool.reset_worker_python()
    yield
    engine_pool.shutdown_all(wait=True)
    engine_pool.reset_worker_python()


# ---------------------------------------------------------------- 决定与门
def test_undecided_root_only_evidence_asks_with_a_recommendation(tmp_path):
    root = _project(tmp_path, ROOT_ONLY, {"data/x.csv": "x\n"})
    d = workdir.decision_for(root, "s/fig.py")
    assert d["decided"] is False and d["needs_confirmation"] is True
    assert d["mode"] == workdir.MODE_SANDBOX  # 没问到之前的默认，不是决定
    c = d["confirmation"]
    assert c["code"] == workdir.ERROR_CONFIRMATION_REQUIRED
    assert c["reason"] == workdir.REASON_PROJECT_ROOT_EVIDENCE
    assert c["recommended"] == workdir.MODE_PROJECT_ROOT
    assert [o["mode"] for o in c["options"]] == ["project_root", "project", "sandbox"]
    assert c["options"][0]["found"] == ["data/x.csv"] and c["options"][0]["recommended"]
    assert c["options"][0]["cwd_origin"] == execspec.CWD_ORIGIN_PROJECT_ROOT
    assert c["options"][2]["write_mode"] == execspec.WRITE_MODE_SANDBOXED
    # 载荷里一条机器路径都没有
    assert str(root) not in json.dumps(c)
    with pytest.raises(workdir.ConfirmationRequired) as exc:
        workdir.resolve_mode(root, "s/fig.py")
    assert exc.value.payload == c


def test_undecided_ambiguous_evidence_asks_without_a_recommendation(tmp_path):
    root = _project(tmp_path, SAME_DIR, {"s/x.csv": "x\n1\n", "x.csv": "x\n2\n"})
    c = workdir.decision_for(root, "s/fig.py")["confirmation"]
    assert c["reason"] == workdir.REASON_AMBIGUOUS_DATA
    assert c["recommended"] is None and not any(o["recommended"] for o in c["options"])
    assert c["conflicts"] == ["x.csv"]


@pytest.mark.parametrize(
    "script,files",
    [
        (SAME_DIR, {"s/x.csv": "x\n"}),  # default_ok：脚本目录就有
        (NO_READS, {}),  # none：没有相对路径字面量
        (ROOT_ONLY, {}),  # unknown：一处都找不到——说不出话就不问
    ],
)
def test_undecided_but_default_suffices_or_evidence_is_silent_does_not_ask(tmp_path, script, files):
    root = _project(tmp_path, script, files)
    d = workdir.decision_for(root, "s/fig.py")
    assert d["needs_confirmation"] is False and d["confirmation"] is None
    assert workdir.resolve_mode(root, "s/fig.py") == workdir.MODE_SANDBOX


@pytest.mark.parametrize("mode", workdir.MODES)
def test_a_decided_project_is_never_asked_again_whatever_the_evidence(tmp_path, mode):
    root = _project(tmp_path, ROOT_ONLY, {"data/x.csv": "x\n"})
    workdir.set_mode(root, mode)
    d = workdir.decision_for(root, "s/fig.py")
    assert d == {
        "mode": mode,
        "decided": True,
        "needs_confirmation": False,
        "evidence": None,
        "confirmation": None,
    }
    assert workdir.resolve_mode(root, "s/fig.py") == mode


def test_forgetting_the_decision_reopens_the_question(tmp_path):
    """项目搬家 / 用户要求重新问：`forget()` 回到没决定过，证据要问就再问。"""
    root = _project(tmp_path, ROOT_ONLY, {"data/x.csv": "x\n"})
    workdir.set_mode(root, workdir.MODE_PROJECT_ROOT)
    assert workdir.resolve_mode(root, "s/fig.py") == workdir.MODE_PROJECT_ROOT
    workdir.forget(root)
    with pytest.raises(workdir.ConfirmationRequired):
        workdir.resolve_mode(root, "s/fig.py")


def test_the_decision_is_project_scoped(tmp_path):
    a = _project(tmp_path, ROOT_ONLY, {"data/x.csv": "x\n"}, name="a")
    b = _project(tmp_path, ROOT_ONLY, {"data/x.csv": "x\n"}, name="b")
    workdir.set_mode(a, workdir.MODE_PROJECT_ROOT)
    assert workdir.resolve_mode(a, "s/fig.py") == workdir.MODE_PROJECT_ROOT
    with pytest.raises(workdir.ConfirmationRequired):
        workdir.resolve_mode(b, "s/fig.py")


# ---------------------------------------------------------------- pool 里那道门
def test_pool_raises_the_structured_confirmation_before_spawning_anything(tmp_path, monkeypatch):
    """四类入口都从 `pool.get()` 起会话：门在这里，一个进程都不起。"""
    root = _project(tmp_path, ROOT_ONLY, {"data/x.csv": "x\n"})
    monkeypatch.setattr(
        engine_pool, "resolve_worker_python", lambda *a, **k: ("/nonexistent/python", "system")
    )
    spawned = []
    monkeypatch.setattr(engine_pool.subprocess, "Popen", lambda *a, **k: spawned.append(a) or 0)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.get("s/fig.py", str(root), "__main__")
    assert err.value.code == workdir.ERROR_CONFIRMATION_REQUIRED
    assert err.value.confirmation["recommended"] == workdir.MODE_PROJECT_ROOT
    assert err.value.script_name == "s/fig.py"
    assert spawned == []
    assert not engine_pool._workers


# ---------------------------------------------------------------- execspec：project_root 档
def test_project_root_mode_is_a_third_cwd_mode_and_project_keeps_its_meaning(tmp_path):
    root = tmp_path / "p"
    (root / "s").mkdir(parents=True)
    spec_parent = execspec.safe_spec(
        "s/fig.py", root, "main", interpreter="/py", sandbox="/box", cwd_mode=execspec.CWD_PROJECT
    )
    spec_root = execspec.safe_spec(
        "s/fig.py",
        root,
        "main",
        interpreter="/py",
        sandbox="/box",
        cwd_mode=execspec.CWD_PROJECT_ROOT,
    )
    assert spec_parent.cwd == str(root / "s")  # ADR 0047：project 仍是脚本目录
    assert spec_root.cwd == str(root)
    assert execspec.launch_context(spec_parent)["cwd_origin"] == execspec.CWD_ORIGIN_SCRIPT_PARENT
    lc = execspec.launch_context(spec_root)
    assert lc["cwd_origin"] == execspec.CWD_ORIGIN_PROJECT_ROOT
    assert lc["write_mode"] == execspec.WRITE_MODE_PROJECT_DIR
    argv = execspec.worker_argv(spec_root, worker_py="/w.py", out_dir="/o")
    assert argv[-2:] == ["--cwd", str(root)]
    assert spec_root.stable_payload()["cwd_mode"] == "project_root"
    assert execspec.spec_from_payload(spec_root.to_payload()) == spec_root
    # native 没有 cwd_mode 这个维度：第三档也一样拒绝
    with pytest.raises(ValueError):
        execspec.ExecutionSpec(
            profile=execspec.PROFILE_NATIVE,
            interpreter="/py",
            target_kind="script",
            target="f.py",
            entry=None,
            argv=(),
            cwd="/c",
            env=None,
            project_root=str(root),
            passthrough_savefig=True,
            raw_target="f.py",
            cwd_mode=execspec.CWD_PROJECT_ROOT,
        )


def test_the_workdir_endpoint_accepts_the_third_mode(tmp_path, monkeypatch):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    root = _project(tmp_path, NO_READS, {})
    m.open_project(str(root))
    try:
        client = m.app.test_client()
        body = client.patch("/api/engine/workdir", json={"mode": "project_root"}).get_json()
        assert body["ok"] is True and body["workdir"]["mode"] == "project_root"
        assert body["workdir"]["modes"] == ["sandbox", "project", "project_root"]
        assert body["workdir"]["grant"]["cwd_write"]["mode"] == "project_root"
        bad = client.patch("/api/engine/workdir", json={"mode": "native"})
        assert bad.status_code == 400 and bad.get_json()["code"] == workdir.ERROR_MODE_INVALID
    finally:
        for pid in [p for p, ctx in list(m.PROJECTS.items()) if str(ctx.path) == str(root)]:
            m.close_project(pid, wait=True)


# ---------------------------------------------------------------- 真 worker：project_root
@needs_worker
def test_project_root_mode_really_runs_at_the_project_root_with_cjk_and_spaces(tmp_path):
    """FO02 + FO09 的机制面：cwd 真的是项目根（worker 自报），相对路径 `data/x.csv` 读得到，
    路径里的中文与空格按平台语义原样成立；守卫仍在（相对写落进项目根，删除被拦）。"""
    root = tmp_path / "论文 项目 v2"
    (root / "scripts").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "data" / "x.csv").write_text("x\n2\n4\n8\n", encoding="utf-8")
    (root / "scripts" / "fig.py").write_text(
        "import csv, os\nimport matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "assert os.path.exists('data/x.csv'), os.getcwd()\n"
        "xs = [float(r['x']) for r in csv.DictReader(open('data/x.csv', encoding='utf-8'))]\n"
        "open('cache.txt', 'w').write('written')\n"
        "os.remove('data/x.csv')\n"  # 守卫：真实图库里的删除被拦下
        "fig, ax = plt.subplots()\nax.plot(xs, [3 * x + 1 for x in xs])\nfig.savefig('fig.pdf')\n",
        encoding="utf-8",
    )
    workdir.set_mode(root, workdir.MODE_PROJECT_ROOT)
    engine_config.set_project_settings(str(root), {})  # 触碰一次设置文件（中文路径当键）
    worker, resp = engine_pool.build("scripts/fig.py", str(root), "__main__")
    assert sorted(resp["stems"]) == ["fig"]
    assert Path(resp["runtime"]["cwd"]).resolve() == root.resolve()
    assert worker.spec.cwd_mode == execspec.CWD_PROJECT_ROOT
    render = worker.override("fig", [], None, inline_svg=False)
    axes = next(e for e in render["manifest"]["elements"] if e["role"] == "axes")
    ylim = next(f["value"] for f in axes["editable"] if f["prop"] == "ylim")
    assert ylim == pytest.approx([7 - 0.9, 25 + 0.9], abs=1e-6)  # 3x+1 of [2,4,8] ± 5%
    assert (root / "cache.txt").read_text(encoding="utf-8") == "written"  # 相对写落进项目根
    assert (root / "data" / "x.csv").is_file()  # 删除被守卫拦下
    assert (
        not (root / "fig.pdf").exists() and not (root / "scripts" / "fig.pdf").exists()
    )  # savefig 不落盘
    assert os.path.isdir(worker.spec.sandbox)  # 沙盒仍在，只是不当 cwd
