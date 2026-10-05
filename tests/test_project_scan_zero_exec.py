"""导入即扫描的「零执行」证明（T02，验收 O01 / E07 / C15 同族）。

主语：**谁**没执行——用户的脚本、项目里的解释器、用户的登录 shell、`--help`；**哪个时刻**——项目
打开 / 恢复之后的自动结构扫描与它周围的刷新；**哪个维度**——不只是「脚本没被改动」，而是三类哨兵
（脚本、项目解释器、登录 shell）的触发计数都是 0，并且扫描期间进程/网络入口一次都没被调用。

证明分两层，缺一不可：

* **行为哨兵**（POSIX）：项目里有会写哨兵文件的脚本、会写哨兵的假 `.venv` 解释器、会写哨兵的假
  `$SHELL`，且项目**已经记住了**那个 venv（这是 T00 F6 实测里 `resolve_worker_python(discover=False)`
  仍会起一次解释器的前提）。扫描 / 刷新 / 清单之后哨兵都是空的。
* **结构守卫**（全平台）：扫描期间 `subprocess.Popen` / `os.system` / `os.exec*` / `os.posix_spawn*` /
  `socket.socket.connect` 被换成会失败的桩——任何一条从扫描函数栈里出去的进程或网络调用都会当场炸，
  而不是靠某个哨兵文件碰巧没被写。另有 AST 门禁：`projscan.py` 自己不引用会起进程 / 采用环境的名字。
"""

from __future__ import annotations

import ast
import os
import socket
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import (
    discover as engine_discover,
    probe as engine_probe,
    project_refresh as engine_refresh,
    projectenv as engine_projectenv,
    registry as engine_registry,
    userenvs as engine_userenvs,
)

posix_only = pytest.mark.skipif(os.name == "nt", reason="哨兵是 POSIX shell 脚本")

SCRIPT_BODY = """\
from pathlib import Path


def main():
    Path({sentinel!r}, "script_run." + str(__import__("os").getpid())).write_text("ran")
    fig.savefig("curve.pdf")


if __name__ == "__main__":
    main()
"""


class World:
    """一个带三类哨兵的项目。哨兵目录在项目**之外**，扫描写不进也读得出。"""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.sentinel = base / "sentinel"
        self.sentinel.mkdir()
        self.root = base / "项目 空格"  # 中文 + 空格路径同时钉着
        self.root.mkdir()
        (self.root / "plot.py").write_text(SCRIPT_BODY.format(sentinel=str(self.sentinel)), "utf-8")
        (self.root / "tools").mkdir()
        (self.root / "tools" / "helper.py").write_text("VALUE = 1\n", "utf-8")
        (self.root / "requirements.txt").write_text("numpy>=1.20\nmatplotlib\n", "utf-8")
        # 假 .venv：解释器一被启动就写哨兵；pyvenv.cfg 让 projectenv 认它是 venv
        venv = self.root / ".venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = /nowhere\n", "utf-8")
        self.venv_python = venv / "bin" / "python"
        self.venv_python.write_text(
            f'#!/bin/sh\ntouch "{self.sentinel}/venv_python.$$"\nexit 0\n', "utf-8"
        )
        self.venv_python.chmod(self.venv_python.stat().st_mode | stat.S_IXUSR)
        # 假登录 shell
        self.shell = base / "fakeshell.sh"
        self.shell.write_text(
            f'#!/bin/sh\ntouch "{self.sentinel}/login_shell.$$"\necho nothing\n', "utf-8"
        )
        self.shell.chmod(self.shell.stat().st_mode | stat.S_IXUSR)

    def fired(self) -> list[str]:
        return sorted(p.name.split(".")[0] for p in self.sentinel.iterdir())

    def remember_venv(self) -> None:
        """项目已经「记住」了这个 venv——T00 F6：此时 `discover=False` 的解析仍会起解释器。"""
        assert engine_projectenv.remember(
            self.root, str(self.venv_python), automatic=True, trigger="first_open"
        )


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = World(tmp_path)
    monkeypatch.setenv("SHELL", str(w.shell))
    monkeypatch.setenv("TAVOTTO_USER_ENV_DISCOVERY", "1")
    monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    engine_projectenv.reset_cache()
    engine_userenvs.reset_cache()
    return w


class _Ctx:
    def __init__(self, root: Path) -> None:
        self.path = root
        self.id = "pj-scan"
        self.registry = engine_registry.Registry()


@contextmanager
def no_process_no_network(monkeypatch):
    """扫描期间：起进程 / 连网络的入口一次都不许被调用。调用即失败（记下来并抛）。"""
    calls: list[str] = []

    def _boom(name):
        def inner(*a, **k):
            calls.append(name)
            raise AssertionError(f"scan reached a process/network entry: {name}")

        return inner

    monkeypatch.setattr(subprocess.Popen, "__init__", _boom("subprocess.Popen"))
    for name in ("system", "execv", "execve", "execvp", "execvpe", "fork", "popen"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _boom(f"os.{name}"))
    for name in ("posix_spawn", "posix_spawnp", "spawnv", "spawnve", "spawnvp", "spawnvpe"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _boom(f"os.{name}"))
    monkeypatch.setattr(socket.socket, "connect", _boom("socket.connect"))
    monkeypatch.setattr(socket, "create_connection", _boom("socket.create_connection"))
    yield calls


# ---------------------------------------------------------------------------
# 既有读路径里的「复检」洞（T00 F6：记住的解释器 → 每次刷新都 import matplotlib）
# ---------------------------------------------------------------------------
@posix_only
def test_a_refresh_with_a_remembered_interpreter_starts_no_interpreter(world):
    world.remember_venv()
    ctx = _Ctx(world.root)
    engine_discover.write_config(world.root, engine_discover.build_draft(world.root)[0])
    ctx.registry.load(world.root)

    engine_refresh.refresh_project_index(ctx, reason="open")

    assert world.fired() == []


@posix_only
def test_the_script_inventory_with_a_remembered_interpreter_starts_no_interpreter(world):
    world.remember_venv()

    rows = engine_probe.script_inventory(world.root, registered=set())

    assert {r["script"] for r in rows} == {"plot.py", "tools/helper.py"}
    assert world.fired() == []


# ---------------------------------------------------------------------------
# 候选线索与体检分开（userenvs）
# ---------------------------------------------------------------------------
@posix_only
def test_reading_environment_clues_does_not_ask_the_login_shell(world):
    (world.root / ".python-version").write_text("3.12.1\n", "utf-8")

    rows = engine_userenvs.discover(world.root, "plot.py", ask_login_shell=False)

    assert all(r["source"] != engine_userenvs.SOURCE_LOGIN_SHELL for r in rows)
    assert world.fired() == []


@posix_only
def test_the_default_form_still_asks_the_login_shell(world):
    """反证这个哨兵有效：默认形态（T05 的明确动作）仍然会问登录 shell。"""
    engine_userenvs.discover(world.root, "plot.py")

    assert "login_shell" in world.fired()


# ---------------------------------------------------------------------------
# 扫描本体
# ---------------------------------------------------------------------------
@posix_only
def test_the_scan_runs_nothing_with_every_sentinel_armed(world, monkeypatch):
    from tavotto.engine import projscan

    world.remember_venv()
    with no_process_no_network(monkeypatch) as calls:
        report = projscan.scan(world.root)

    assert calls == []
    assert world.fired() == []
    assert {t["script"] for t in report["targets"]} == {"plot.py"}
    # 记住的解释器与 venv 候选作为「未核验」线索出现，而不是被体检
    sources = {c["source"] for c in report["environment"]["candidates"]}
    assert "project_venv" in sources
    assert {c["status"] for c in report["environment"]["candidates"]} <= {
        "unchecked",
        "remembered_unverified",
    }
    assert report["environment"]["remembered"]["automatic"] is True
    assert report["environment"]["verified"] is False


def test_the_scan_reaches_no_process_or_network_entry_on_any_platform(tmp_path, monkeypatch):
    from tavotto.engine import projscan

    root = tmp_path / "p"
    root.mkdir()
    (root / "a.py").write_text("import matplotlib.pyplot as plt\nplt.savefig('a.pdf')\n", "utf-8")
    (root / "b.py").write_text("def broken(:\n", "utf-8")  # 语法错误：不许借目标解释器再解析
    with no_process_no_network(monkeypatch) as calls:
        report = projscan.scan(root)

    assert calls == []
    broken = next(s for s in report["scripts"] if s["script"] == "b.py")
    assert broken["problem"]["kind"] == "syntax_error"
    assert broken["parser"] is None  # 没有目标解析器：未核验，不是「确认不是脚本」


def test_the_scan_module_references_no_process_or_adoption_entry():
    """AST 门禁：判的是 `projscan.py` 自己引用了哪些名字，不是子串（注释里提到不算）。"""
    path = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine" / "projscan.py"
    tree = ast.parse(path.read_text("utf-8"))
    imported: set[str] = set()
    attrs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported |= {a.name for a in node.names}
            if node.module:
                imported.add(node.module.split(".")[-1])
        elif isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.Attribute):
            attrs.add(node.attr)
    forbidden_modules = {
        "subprocess",
        "pool",
        "preparation",
        "prepsession",
        "deprepair",
        "managedenv",
        "privatepython",
        "workerd_client",
        "worker",
        "socket",
        "urllib",
        "requests",
    }
    forbidden_attrs = {
        "resolve_worker_python",
        "decide_environment",
        "plan_for",
        "gate",
        "first_open_candidate",
        "probe_environment",
        "analyze_in_interpreter",
        "login_shell_pythons",
        "system",
        "Popen",
        "run",
    }
    assert imported & forbidden_modules == set()
    assert attrs & forbidden_attrs == set()
    assert sys.version_info >= (3, 10)


@posix_only
def test_open_scan_refresh_and_readiness_over_http_start_nothing(world, tmp_path, monkeypatch):
    """真 HTTP 入口走一遍「导入之后」：打开项目 → 自动扫描 → 手动刷新 → 就绪度 → 素材清单。

    项目已经记住了假 venv、`$SHELL` 是假的、脚本会写哨兵、`TAVOTTO_USER_ENV_DISCOVERY=1`（conftest 默认关掉
    的那类副作用在这里是**开着的**）。三类哨兵都是空——不是靠某个入口碰巧没触发，而是整条导入后链路。"""
    import time

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    world.remember_venv()
    client = m.app.test_client()
    try:
        opened = client.post("/api/projects/open", json={"path": str(world.root)})
        assert opened.status_code == 200, opened.get_json()
        pj = opened.get_json()["id"]
        q = {"pj": pj}
        assert client.post("/api/project/scan", json={}, query_string=q).status_code == 202
        deadline = time.time() + 10
        while client.get("/api/project/scan", query_string=q).get_json()["state"] == "running":
            assert time.time() < deadline
            time.sleep(0.02)
        assert client.post("/api/project/refresh", json={}, query_string=q).status_code == 200
        assert client.get("/api/project/readiness", query_string=q).status_code == 200
        assert client.get("/api/panels", query_string=q).status_code == 200
        report = client.get("/api/project/scan", query_string=q).get_json()
        # 图名静态可解的脚本在打开项目时就被静态登记了（既有打开行为）：扫描看到的是「已连接」
        assert report["state"] == "complete"
        assert [
            (x["script"], x["registered"]) for x in report["scripts"] if x["script"] == "plot.py"
        ] == [("plot.py", True)]
        assert world.fired() == []
    finally:
        m.reset_projects()
