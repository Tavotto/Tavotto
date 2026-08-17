"""渲染环境自助安装：状态探测、隔离边界、失败路径。

真去建 venv 装 matplotlib 要几十秒和一次网络下载，这里只有一个用例做真实安装，
且默认跳过（-m slow 才跑）。其余全部打桩——重点是验证**边界**：
永远不往用户已有的环境里装东西。
"""
import subprocess

import pytest

from magplot.engine import bootstrap, config, pool


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    """每个用例独立的数据目录与干净的解释器缓存。"""
    monkeypatch.setenv("MAGPLOT_DATA_DIR", str(tmp_path / "data"))
    pool.reset_worker_python()
    bootstrap._progress.update(state="idle", log="", error=None)
    yield
    pool.reset_worker_python()


# ---------------- 状态 --------------------------------------------------------
def test_status_ok_when_interpreter_found(monkeypatch):
    monkeypatch.setattr(pool, "find_worker_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p: "3.11.1")
    st = bootstrap.status()
    assert st["ok"] is True and st["matplotlib"] == "3.11.1"
    assert st["managed"] is False        # 用的是用户自己的环境


def test_status_offers_install_when_base_python_exists(monkeypatch):
    def boom():
        raise pool.WorkerError("no", code="no_worker_python")
    monkeypatch.setattr(pool, "find_worker_python", boom)
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: "/usr/bin/python3")
    st = bootstrap.status()
    assert st["ok"] is False and st["can_install"] is True


def test_status_admits_it_cannot_help_without_any_python(monkeypatch):
    """一个 Python 都没有时不能假装能修——venv 得由某个真解释器创建。"""
    def boom():
        raise pool.WorkerError("no", code="no_worker_python")
    monkeypatch.setattr(pool, "find_worker_python", boom)
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: None)
    st = bootstrap.status()
    assert st["ok"] is False and st["can_install"] is False


# ---------------- 隔离边界（最要紧的一条） -------------------------------------
def test_install_never_touches_the_users_own_environment(monkeypatch, tmp_path):
    """安装必须发生在 Magplot 的数据目录里，且 pip 只对着那个 venv 跑。

    往用户的 conda / 系统 Python 里 pip install 是能省事，但那是他做研究用的
    环境——这条断言就是防止哪天有人图省事把它改回去。
    """
    users_python = "/opt/homebrew/bin/python3"
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: users_python)
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p: "3.11.1")

    calls: list[list[str]] = []

    def fake_run(cmd):
        calls.append(cmd)
        if "venv" in cmd:                       # 假装 venv 建好了
            bootstrap.venv_python().parent.mkdir(parents=True, exist_ok=True)
            bootstrap.venv_python().write_text("#!/bin/sh\n")
        return 0, "ok\n"

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    out = bootstrap.install()
    assert out["ok"] is True

    venv_root = str(bootstrap.venv_python().parent.parent)
    assert calls[0] == [users_python, "-m", "venv", venv_root]

    pip_cmd = calls[1]
    assert pip_cmd[0] == str(bootstrap.venv_python()), "pip 必须对着自建 venv 跑"
    assert pip_cmd[1:4] == ["-m", "pip", "install"]
    # 用户自己的解释器绝不能出现在任何一条 pip 命令里
    assert not any(users_python == c[0] and "pip" in c for c in calls)


def test_install_records_choice_so_next_launch_uses_it(monkeypatch):
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p: "3.11.1")

    def fake_run(cmd):
        if "venv" in cmd:
            bootstrap.venv_python().parent.mkdir(parents=True, exist_ok=True)
            bootstrap.venv_python().write_text("#!/bin/sh\n")
        return 0, ""

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    bootstrap.install()
    assert config.worker_python() == str(bootstrap.venv_python())
    # 缓存要被清掉，否则本次进程仍认为「找不到」
    assert pool._worker_python is None


# ---------------- 失败路径 ----------------------------------------------------
def test_install_reports_pip_failure(monkeypatch):
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: "/usr/bin/python3")

    def fake_run(cmd):
        if "venv" in cmd:
            bootstrap.venv_python().parent.mkdir(parents=True, exist_ok=True)
            bootstrap.venv_python().write_text("#!/bin/sh\n")
            return 0, ""
        return 1, "ERROR: 下载超时\n"

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    out = bootstrap.install()
    assert out["ok"] is False
    assert bootstrap.progress()["state"] == "failed"
    assert "下载超时" in bootstrap.progress()["log"]


def test_install_without_any_python_is_honest(monkeypatch):
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: None)
    out = bootstrap.install()
    assert out["ok"] is False and "安装 Python" in out["error"]


def test_only_one_install_at_a_time(monkeypatch):
    bootstrap._lock.acquire()
    try:
        out = bootstrap.install()
        assert out["ok"] is False and "进行中" in out["error"]
    finally:
        bootstrap._lock.release()


# ---------------- 真实安装（默认跳过） ----------------------------------------
@pytest.mark.slow
def test_real_install_end_to_end(monkeypatch):
    """真建 venv、真装 matplotlib。跑法：pytest -m slow"""
    if bootstrap.find_base_python() is None:
        pytest.skip("这台机器上没有可用来建 venv 的 Python")
    out = bootstrap.install()
    assert out["ok"] is True, out
    assert subprocess.run([out["python"], "-c", "import matplotlib"],
                          capture_output=True).returncode == 0


# ---------------- HTTP 端点 ---------------------------------------------------
@pytest.fixture
def client():
    from magplot import app as m
    m.app.config["TESTING"] = True
    return m.app.test_client()


def test_environment_endpoint_reports_status(client, monkeypatch):
    monkeypatch.setattr(pool, "find_worker_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p: "3.11.1")
    body = client.get("/api/engine/environment").get_json()
    assert body["ok"] is True and body["matplotlib"] == "3.11.1"


def test_install_endpoint_refuses_without_any_python(client, monkeypatch):
    def boom():
        raise pool.WorkerError("no", code="no_worker_python")
    monkeypatch.setattr(pool, "find_worker_python", boom)
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: None)
    resp = client.post("/api/engine/environment/install")
    assert resp.status_code == 400
    assert "安装 Python" in resp.get_json()["error"]


def test_set_python_rejects_interpreter_without_matplotlib(client, monkeypatch, tmp_path):
    fake = tmp_path / "python3"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p: None)
    resp = client.patch("/api/engine/environment", json={"python": str(fake)})
    assert resp.status_code == 400
    assert "matplotlib" in resp.get_json()["error"]


def test_set_python_accepts_and_persists(client, monkeypatch, tmp_path):
    fake = tmp_path / "python3"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p: "3.11.1")
    monkeypatch.setattr(pool, "find_worker_python", lambda: str(fake))
    resp = client.patch("/api/engine/environment", json={"python": str(fake)})
    assert resp.status_code == 200
    assert config.worker_python() == str(fake)


def test_set_python_empty_clears_back_to_autodetect(client, monkeypatch, tmp_path):
    config.set_worker_python(str(tmp_path / "old"))
    monkeypatch.setattr(pool, "find_worker_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p: "3.11.1")
    client.patch("/api/engine/environment", json={"python": ""})
    assert config.worker_python() is None


def test_render_failure_carries_machine_readable_code(client, monkeypatch, tmp_path):
    """前端靠 code 区分「缺环境」与「脚本报错」，不能只回一段文字。"""
    from magplot import app as m

    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "p1.pdf").write_bytes(b"%PDF-1.4\n")
    m.open_project(str(figs))
    monkeypatch.setattr(
        m.engine_registry.Registry, "for_stem",
        lambda self, s: {"script": "x.py", "entry": "main", "cost": "light"})

    def boom(*a, **kw):
        raise pool.WorkerError("找不到装有 matplotlib 的 Python",
                               code="no_worker_python")
    monkeypatch.setattr(m.engine_pool, "get", boom)

    resp = client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []})
    assert resp.status_code == 500
    assert resp.get_json()["code"] == "no_worker_python"
