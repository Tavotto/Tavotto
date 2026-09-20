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

假解释器（`_fake_interpreter`）是跨平台的：Windows 上一个 `.cmd`，其余平台 shebang。
它按 `-c` 里的代码文本分辨「体检」与「matplotlib 版本探测」两种问法，各回一份
**调用方指定**的答案——用例要的是分类逻辑，不是再起一次真 matplotlib。
"""

from __future__ import annotations

import ast
import json
import os
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
def _fake_interpreter(tmp_path: Path, probe_json: dict, *, mpl_version: str = "3.11.0") -> str:
    """一个「解释器」：体检问法回 `probe_json`，`import matplotlib;print(...)` 回版本号。"""
    script = tmp_path / "fake_python_impl.py"
    script.write_text(
        textwrap.dedent(
            f"""\
            import sys
            code = sys.argv[sys.argv.index("-c") + 1] if "-c" in sys.argv else ""
            if "tavotto_worker_ok" in code:
                sys.stdout.write({json.dumps(probe_json)!r})
            elif "matplotlib.__version__" in code:
                sys.stdout.write({mpl_version!r} + "\\n")
            else:
                sys.exit(3)
            """
        ),
        encoding="utf-8",
    )
    if os.name == "nt":
        exe = tmp_path / "fake-python.cmd"
        exe.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = tmp_path / "fake-python"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    return str(exe)


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
def _top_level_imports(path: Path) -> set[str]:
    """一个模块顶层 `import x` / `from x import …` 的顶级名字。"""
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_the_probe_imports_the_worker_module_itself():
    """体检 import 的是 `worker`——不是一份手抄的模块清单。

    手抄清单的问题在于它只在写下的那一天与 worker.py 相同：2026-09 之前这里是
    `figcapture, manifest, overrides`，而 worker.py 还要 `matplotlib.figure`、
    `figsession`、`wireproto`。清单不会自己跟着 worker 长。
    """
    probe = ast.parse(projectenv._PROBE_SRC)
    imported = {
        alias.name
        for node in ast.walk(probe)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "worker" in imported, imported
    # 而 worker.py 自己确实平铺 import 了那几个体检从前只抄了一部分的名字
    assert {"figcapture", "figsession", "wireproto"} <= _top_level_imports(ENGINE / "worker.py")


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
    script = tmp_path / "dies.py"
    script.write_text("import sys\nsys.stderr.write('bad executable format\\n')\nsys.exit(2)\n")
    if os.name == "nt":
        exe = tmp_path / "dies.cmd"
        exe.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = tmp_path / "dies"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    resp = client.patch("/api/engine/environment", json={"python": str(exe)})
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
