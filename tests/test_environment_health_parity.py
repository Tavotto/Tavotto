"""「渲染环境」的体检必须量 worker 真正要起的那条链——全局路径与项目路径同一份（#435）。

issue #435 那一族的形状：用户在设置里把渲染环境指到自己的 Conda / venv，界面接受了
（以前全局路径只问一句 `import matplotlib`），第一次渲染 worker 起到一半就死，用户
看到的是「渲染进程崩溃（无响应）」——而实际发生的是 `matplotlib.figure`（→ Pillow）
或 figsession 那一层 import 断了、Python 版本不在支持区间、或解释器根本起不来。

两条纪律各一组用例：

* **体检的主语是 worker 的启动导入链**，不是它的一个子集。`probe_environment`
  import 的是 `worker` 模块本身：worker.py 多一条 import，体检就多查一条。
* **全局路径与项目路径同一份体检**：`PATCH /api/engine/environment`（不带 scope）
  与 `scope=project` 走同一个 `probe_environment`，只是回给界面的 code 不同
  （全局的说「这个解释器」，项目的说「项目环境」）。

假解释器（`_fake_interpreter`）是一个真 venv 加 `sitecustomize` 劫持，两个平台同一份：
它按 `-c` 里的代码文本分辨「体检」与「matplotlib 版本探测」两种问法，各回一份
**调用方指定**的答案——用例要的是分类逻辑，不是再起一次真 matplotlib。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tavotto.engine import config as engine_config, pool as engine_pool, projectenv

ENGINE = Path(projectenv.__file__).resolve().parent

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


# ------------------------------------------------------------------ 工具
def _fake_interpreter(
    tmp_path: Path, probe_json: dict | None, *, mpl_version: str = "3.11.0", die: str = ""
) -> str:
    """一个「解释器」：真 venv + `sitecustomize` 劫持。

    体检问法（`-c` 里含 `tavotto_worker_ok`）回 `probe_json`，`import matplotlib;print(…)`
    回版本号；`die` 非空则任何调用都往 stderr 写这句并以 2 退出（「起不来」的形状）。
    为什么不是一个 `.cmd` / `.sh` 壳：体检那段 `-c` 源码是多行带引号的，cmd.exe 的
    `%*` 把它搅成一锅粥（Windows 腿实测四条全红成 interpreter_unusable）；而 venv 的
    `python.exe` 是真解释器，`site` 会在 `-c` 之前处理 site-packages 里的 `.pth`
    （`import …` 行当场执行），`sys.orig_argv` 里就有完整的命令行。走 `.pth` 而不是
    `sitecustomize`：Homebrew 的 Python 在标准库目录里自带一份 `sitecustomize.py`，
    排在 site-packages 前面，venv 里那份根本轮不到。POSIX / Windows 同一份夹具。
    """
    root = tmp_path / f"fake-{'dies' if die else 'py'}"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(root)], check=True, timeout=120
    )
    python = str(root / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    purelib = subprocess.run(
        [python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    ).stdout.strip()
    Path(purelib).mkdir(parents=True, exist_ok=True)
    (Path(purelib) / "zz_tavotto_fake_probe.pth").write_text(
        "import _tavotto_fake_probe\n", encoding="utf-8"
    )
    (Path(purelib) / "_tavotto_fake_probe.py").write_text(
        textwrap.dedent(
            f"""\
            import json, os, sys
            DIE = {die!r}
            if DIE:
                sys.stderr.write(DIE + "\\n"); sys.stderr.flush(); os._exit(2)
            argv = list(getattr(sys, "orig_argv", []))
            code = argv[argv.index("-c") + 1] if "-c" in argv else ""
            if "tavotto_worker_ok" in code:
                sys.stdout.write({json.dumps(probe_json or {})!r}); sys.stdout.flush(); os._exit(0)
            if "matplotlib.__version__" in code:
                sys.stdout.write({mpl_version!r} + "\\n"); sys.stdout.flush(); os._exit(0)
            """
        ),
        encoding="utf-8",
    )
    return python


def _probe_answer(**overrides) -> dict:
    base = {
        "executable": "/fake/python",
        "prefix": "/fake",
        "python_version": "3.12.4",
        "version_info": [3, 12, 4],
        "arch": "x86_64",
        "matplotlib_version": "3.11.0",
        "tavotto_worker_ok": True,
        "requested_module": None,
        "requested_module_ok": None,
        "error": None,
    }
    base.update(overrides)
    return base


@pytest.fixture
def client():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    return m.app.test_client()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    engine_pool.reset_worker_python()
    yield
    engine_config.set_worker_python(None)
    engine_pool.reset_worker_python()


# --------------------------------------------- 体检的主语 = worker 的启动导入链
def _engine_load_list(path: Path) -> set[str]:
    """worker.py 经 bridgeboot 装进私有包的引擎模块清单（`_ENGINE_MODULES = (...)` 字面量）。"""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "_ENGINE_MODULES" for t in node.targets
        ):
            return {elt.value for elt in node.value.elts}
    raise AssertionError("worker.py 里没有 _ENGINE_MODULES 清单")


def test_the_probe_executes_the_worker_file_itself():
    """体检执行的是 `worker.py` 这个文件——不是一份手抄的模块清单，也不是 `import worker`。

    手抄清单的问题在于它只在写下的那一天与 worker.py 相同：2026-09 之前这里是
    `figcapture, manifest, overrides`，而 worker.py 还要 `matplotlib.figure`、
    `figsession`、`wireproto`。清单不会自己跟着 worker 长。`import worker` 的问题是
    （评审 #443 第十轮）：这个解释器的 sitecustomize / .pth 若已经 import 过一个不相干的
    顶层 `worker`，import 语句拿到的是缓存里那一个、体检就绿了。
    """
    probe = ast.parse(projectenv._PROBE_SRC)
    imported = {
        alias.name
        for node in ast.walk(probe)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "worker" not in imported, imported
    assert "spec_from_file_location" in projectenv._PROBE_SRC
    assert '"worker.py"' in projectenv._PROBE_SRC
    # 而 worker.py 自己确实要那几个体检从前只抄了一部分的名字——U03（ADR 0057 / #447）起
    # 它不再顶层 `import figcapture`，而是经 bridgeboot 按 `_ENGINE_MODULES` 清单装进私有包；
    # 跑文件本身就把清单里的每一个都装了，清单才是它真正的启动导入链
    assert {"figcapture", "figsession", "wireproto"} <= _engine_load_list(ENGINE / "worker.py")


def _fake_engine_dir(tmp_path: Path, *, worker_error: str) -> Path:
    """一个假引擎目录：老清单里那三个模块都 import 得动，`worker.py` 却起不来。

    正是「老体检绿、worker 死」那个形状——真环境里造不出来（真 worker 的依赖
    要么全在要么全不在），所以用一个只在 import 那一层分叉的替身。
    """
    eng = tmp_path / "engine"
    eng.mkdir()
    for name in ("figcapture", "manifest", "overrides"):
        (eng / f"{name}.py").write_text("OK = True\n", encoding="utf-8")
    (eng / "worker.py").write_text(
        f"import figcapture, manifest, overrides  # noqa: F401\nraise ImportError({worker_error!r})\n",
        encoding="utf-8",
    )
    return eng


@needs_worker
def test_a_broken_worker_import_chain_fails_the_probe_even_when_matplotlib_imports(
    tmp_path, monkeypatch
):
    """matplotlib 完好、worker 起不来 → `project_env_worker_import_failed`，并说出断在哪。

    变异反证：把 `_PROBE_SRC` 里的 `import worker` 换回老的三个名字，这条当场绿→红
    反转（假引擎目录里那三个模块都 import 得动）。
    """
    eng = _fake_engine_dir(tmp_path, worker_error="DLL load failed while importing _imaging")
    monkeypatch.setattr(projectenv, "ENGINE_DIR", eng)
    info = projectenv.probe_environment(WORKER_PY)
    assert info["ok"] is False, info
    assert info["code"] == projectenv.ERROR_WORKER_IMPORT
    assert info["matplotlib_version"], "前提：matplotlib 本身 import 得动，断的只是 worker"
    assert "DLL load failed while importing _imaging" in (info.get("error") or "")


@needs_worker
def test_a_preimported_stranger_named_worker_does_not_pass_the_probe(tmp_path, monkeypatch):
    """评审 #443 第十轮 P2：解释器启动时（sitecustomize / .pth）已经 import 过一个不相干的
    顶层 `worker`——`import worker` 拿到的是缓存里那一个，真 worker.py 一行都没执行，
    体检却绿。按文件加载就没有这条缓存可走。这里用 `PYTHONPATH` 里的 `sitecustomize.py`
    当启动钩子（`site` 在 `-c` 之前 import 它）。"""
    hook = tmp_path / "hook"
    hook.mkdir()
    (hook / "worker.py").write_text("STRANGER = True\n", encoding="utf-8")
    (hook / "sitecustomize.py").write_text("import worker  # noqa: F401\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(hook))
    eng = _fake_engine_dir(tmp_path, worker_error="DLL load failed while importing _imaging")
    monkeypatch.setattr(projectenv, "ENGINE_DIR", eng)
    info = projectenv.probe_environment(WORKER_PY)
    assert info["ok"] is False, info
    assert info["code"] == projectenv.ERROR_WORKER_IMPORT
    assert "DLL load failed while importing _imaging" in (info.get("error") or "")


@needs_worker
def test_the_real_engine_dir_passes_the_probe_in_an_interpreter_with_matplotlib():
    """对照组：真引擎目录 + 有 matplotlib 的解释器 → ok。上一条不是「什么都判不过」。"""
    info = projectenv.probe_environment(WORKER_PY)
    assert info["ok"] is True, info
    assert info["tavotto_worker_ok"] is True


# --------------------------------------------- 全局路径与项目路径同一份体检
def test_global_scope_refuses_an_unsupported_python_version(client, tmp_path):
    """以前全局路径只看「import 得到 matplotlib」，Python 3.9 的环境能被存下来。"""
    exe = _fake_interpreter(
        tmp_path,
        _probe_answer(python_version="3.9.19", version_info=[3, 9, 19], matplotlib_version="3.9.0"),
        mpl_version="3.9.0",
    )
    resp = client.patch("/api/engine/environment", json={"python": exe})
    assert resp.status_code == 400, resp.get_json()
    body = resp.get_json()
    assert body["code"] == "interpreter_unsupported_python"
    assert body["params"]["python_version"] == "3.9.19"
    assert body["params"]["path"] == exe
    assert engine_config.worker_python() is None, "体检不过绝不先存"


def test_global_scope_refuses_a_broken_worker_import_chain(client, tmp_path):
    """matplotlib 在、worker 起不来：这是 #435 那一族最常见的断法，要点名断在哪。"""
    exe = _fake_interpreter(
        tmp_path,
        _probe_answer(
            tavotto_worker_ok=False,
            error="worker: ImportError: DLL load failed while importing _imaging",
        ),
    )
    resp = client.patch("/api/engine/environment", json={"python": exe})
    assert resp.status_code == 400, resp.get_json()
    body = resp.get_json()
    assert body["code"] == "interpreter_worker_import_failed"
    assert "_imaging" in body["params"]["detail"]
    assert engine_config.worker_python() is None


def test_global_scope_keeps_the_old_code_for_a_missing_matplotlib(client, tmp_path):
    """老 code `interpreter_no_matplotlib` 原样保留：前端与文档都认它。"""
    exe = _fake_interpreter(
        tmp_path, _probe_answer(matplotlib_version=None, tavotto_worker_ok=False), mpl_version=""
    )
    resp = client.patch("/api/engine/environment", json={"python": exe})
    assert resp.status_code == 400, resp.get_json()
    assert resp.get_json()["code"] == "interpreter_no_matplotlib"


def test_global_scope_reports_an_interpreter_that_cannot_start(client, tmp_path):
    """连体检都跑不起来（非零退出）→ `interpreter_unusable`，带上它自己说了什么。"""
    exe = _fake_interpreter(tmp_path, None, die="bad executable format")
    resp = client.patch("/api/engine/environment", json={"python": exe})
    assert resp.status_code == 400, resp.get_json()
    body = resp.get_json()
    assert body["code"] == "interpreter_unusable"
    assert "bad executable format" in body["params"]["detail"]


def test_global_scope_accepts_a_healthy_interpreter_and_stores_it(client, tmp_path):
    """对照组：体检全过 → 存下来。上面四条不是「什么都拒绝」。"""
    exe = _fake_interpreter(tmp_path, _probe_answer())
    resp = client.patch("/api/engine/environment", json={"python": exe})
    assert resp.status_code == 200, resp.get_json()
    assert engine_config.worker_python() == exe


def test_global_scope_absolutizes_a_relative_path_before_probing(client, tmp_path, monkeypatch):
    """评审 #443 第十四轮 P2：相对路径 `.venv/bin/python` 按 Flask 的 cwd `is_file()` 判得过，
    体检却在一个空的 scratch 目录里 spawn 它 → ENOENT → `interpreter_unusable`。先绝对化
    （不 resolve：venv 的 python 是软链接，落到真身就丢了 venv），存下去的也是绝对形态。"""
    exe = _fake_interpreter(tmp_path, _probe_answer())
    monkeypatch.chdir(tmp_path)
    rel = os.path.relpath(exe, tmp_path)
    assert not os.path.isabs(rel)
    resp = client.patch("/api/engine/environment", json={"python": rel})
    assert resp.status_code == 200, resp.get_json()
    stored = engine_config.worker_python()
    assert os.path.isabs(stored) and Path(stored) == Path(exe), stored
