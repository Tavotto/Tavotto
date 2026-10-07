"""渲染环境自助安装：状态探测、隔离边界、失败路径。

真去建 venv 装 matplotlib 要几十秒和一次网络下载，这里只有一个用例做真实安装，
且默认跳过（-m slow 才跑）。其余全部打桩——重点是验证**边界**：
永远不往用户已有的环境里装东西。
"""

import subprocess

import pytest

from tavotto.engine import bootstrap, config, pool, projectenv


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    """每个用例独立的数据目录与干净的解释器缓存。"""
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    pool.reset_worker_python()
    bootstrap._progress.update(state="idle", log="", error=None)
    yield
    pool.reset_worker_python()


# ---------------- 状态 --------------------------------------------------------
def test_status_ok_when_interpreter_found(monkeypatch):
    monkeypatch.setattr(pool, "find_worker_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p, **_kw: "3.11.1")
    st = bootstrap.status()
    assert st["ok"] is True and st["matplotlib"] == "3.11.1"
    assert st["managed"] is False  # 用的是用户自己的环境


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
    """安装必须发生在 Tavotto 的数据目录里，且 pip 只对着那个 venv 跑。

    往用户的 conda / 系统 Python 里 pip install 是能省事，但那是他做研究用的
    环境——这条断言就是防止哪天有人图省事把它改回去。
    """
    users_python = "/opt/homebrew/bin/python3"
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: users_python)
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p, **_kw: "3.11.1")

    calls: list[list[str]] = []

    def fake_run(cmd):
        calls.append(cmd)
        if "venv" in cmd:  # 假装 venv 建好了
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
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p, **_kw: "3.11.1")

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


def test_pip_failure_keeps_a_redacted_excerpt(monkeypatch):
    """pip 非 0：日志尾巴是 stage=pip，用户看见的那句仍是「日志见下方」。"""
    monkeypatch.setenv("TAVOTTO_PIP_EVIDENCE_SECRET", "envdump-secret")
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: "/usr/bin/python3")
    data = str(config.data_dir())

    def fake_run(cmd):
        if "venv" in cmd:
            bootstrap.venv_python().parent.mkdir(parents=True, exist_ok=True)
            bootstrap.venv_python().write_text("#!/bin/sh\n")
            return 0, ""
        return (
            1,
            "ERROR: resolution impossible\n"
            "https://user:secret@pypi.example/simple?token=secret\n"
            f"{data}/worker-env/evidence-marker\n",
        )

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    out = bootstrap.install()
    log = bootstrap.progress()["log"]
    assert out == {"ok": False, "error": "安装 matplotlib 失败，日志见下方。"}
    assert bootstrap.progress()["state"] == "failed"
    assert "ERROR: resolution impossible" in log
    assert log.rstrip().endswith("[bootstrap] stage=pip class=exit:1")
    assert "pypi.example" in log and "<data>/worker-env/evidence-marker" in log
    assert "secret" not in log and "token=" not in log and data not in log
    assert "envdump-secret" not in log


def test_venv_failure_keeps_a_redacted_excerpt(monkeypatch):
    """venv 非 0：分类是 stage=venv，错误文案仍是「创建虚拟环境失败」。"""
    monkeypatch.setenv("TAVOTTO_PIP_EVIDENCE_SECRET", "envdump-secret")
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: "/usr/bin/python3")
    data = str(config.data_dir())

    def fake_run(cmd):
        return (
            1,
            "Error: venv creation failed\n"
            "https://user:secret@pypi.example/simple?token=secret\n"
            f"{data}/worker-env/evidence-marker\n",
        )

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    out = bootstrap.install()
    log = bootstrap.progress()["log"]
    assert out["ok"] is False and "创建虚拟环境失败" in out["error"]
    assert bootstrap.progress()["state"] == "failed"
    assert "Error: venv creation failed" in log
    assert log.rstrip().endswith("[bootstrap] stage=venv class=exit:1")
    assert "pypi.example" in log and "<data>/worker-env/evidence-marker" in log
    assert "secret" not in log and "token=" not in log and data not in log
    assert "envdump-secret" not in log


def test_timeout_keeps_partial_output_and_not_the_argv(monkeypatch):
    """真 _run：subprocess.run 抛 TimeoutExpired 时留下摘录，不留下命令行。"""
    monkeypatch.setenv("TAVOTTO_PIP_EVIDENCE_SECRET", "envdump-secret")
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: "/usr/bin/python3")
    data = str(config.data_dir())
    timeouts: list[int | None] = []

    def fake_run(cmd, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        if "venv" in cmd:
            bootstrap.venv_python().parent.mkdir(parents=True, exist_ok=True)
            bootstrap.venv_python().write_text("#!/bin/sh\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise subprocess.TimeoutExpired(
            cmd,
            kwargs.get("timeout", 0),
            output=(
                b"partial wheel listing\n"
                b"https://user:secret@pypi.example/simple?token=secret\n"
                + data.encode()
                + b"/worker-env/evidence-marker\n"
            ),
            stderr="index stalled\n",
        )

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    out = bootstrap.install()
    log = bootstrap.progress()["log"]
    assert out == {"ok": False, "error": "安装 matplotlib 失败，日志见下方。"}
    assert "partial wheel listing" in log and "index stalled" in log
    assert "b'partial" not in log
    assert log.rstrip().endswith("[bootstrap] stage=pip class=timeout")
    assert "--disable-pip-version-check" not in log
    assert bootstrap.INSTALL_TIMEOUT_S == 900
    assert timeouts == [900, 900]
    assert "pypi.example" in log and "<data>/worker-env/evidence-marker" in log
    assert "secret" not in log and "token=" not in log and data not in log
    assert "envdump-secret" not in log


def test_import_failure_reads_the_probe_excerpt(monkeypatch):
    """装完之后走真的 matplotlib_version / _probe；失败摘录来自那一次，不是再探一次。"""
    monkeypatch.setenv("TAVOTTO_PIP_EVIDENCE_SECRET", "envdump-secret")
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: "/usr/bin/python3")
    data = str(config.data_dir())
    probes = 0

    def fake_run(cmd):
        if "venv" in cmd:
            bootstrap.venv_python().parent.mkdir(parents=True, exist_ok=True)
            bootstrap.venv_python().write_text("#!/bin/sh\n")
        return 0, ""

    def fake_probe(cmd, **kwargs):
        nonlocal probes
        probes += 1
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr=(
                "ModuleNotFoundError: No module named 'matplotlib'\n"
                "https://user:secret@pypi.example/simple?token=secret\n"
                f"{data}/worker-env/evidence-marker\n"
            ),
        )

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    monkeypatch.setattr(bootstrap.subprocess, "run", fake_probe)
    out = bootstrap.install()
    log = bootstrap.progress()["log"]
    assert out["error"] == "装完仍然 import 不到 matplotlib。"
    assert probes == 1
    assert "ModuleNotFoundError: No module named 'matplotlib'" in log
    assert log.rstrip().endswith("[bootstrap] stage=import class=import_failed")
    assert config.worker_python() is None
    assert "pypi.example" in log and "<data>/worker-env/evidence-marker" in log
    assert "secret" not in log and "token=" not in log and data not in log
    assert "envdump-secret" not in log


@pytest.mark.parametrize("failure", ["timeout", "import"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("private_prefix", ["url", "data_dir"])
def test_failure_excerpts_redact_before_truncation(
    monkeypatch, tmp_path, failure, stream, private_prefix
):
    """安装进度与事件里的摘录，不能因截断丢掉识别凭据 / 私有目录所需的前缀。"""
    canary = "synthetic-private-boundary-value"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / canary))
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: "/usr/bin/python3")
    prefix = (
        "https://index.example/simple?signature="
        if private_prefix == "url"
        else str(config.data_dir())[: -len(canary)]
    )
    # 原实现的 4000 字原文尾巴正好从金丝雀开始；不能只测没被切开的短 URL。
    suffix = "\n" + "x" * (bootstrap._EXCERPT_CHARS - len(canary) - 1)
    payload = prefix + canary + suffix
    assert payload[-bootstrap._EXCERPT_CHARS :].startswith(canary)
    probes = 0

    def fake_run(cmd, **kwargs):
        nonlocal probes
        if "venv" in cmd:
            bootstrap.venv_python().parent.mkdir(parents=True, exist_ok=True)
            bootstrap.venv_python().write_text("#!/bin/sh\n")
        elif "pip" in cmd and failure == "timeout":
            output = payload.encode() if stream == "stdout" else None
            stderr = payload if stream == "stderr" else None
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"], output=output, stderr=stderr)
        elif "-c" in cmd:
            probes += 1
            return subprocess.CompletedProcess(
                cmd, 1, **{"stdout": "", "stderr": "", stream: payload}
            )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    events = []
    out = bootstrap.install(events.append)
    assert out["ok"] is False
    assert probes == (1 if failure == "import" else 0)
    assert events
    for event in events:
        assert canary not in event["log"]
        assert len(event["log"]) <= 8000
    log = bootstrap.progress()["log"]
    assert canary not in log
    assert "x" * 100 in log, "不能通过扔掉所有输出来让隐私断言假绿"
    marker = (
        "stage=pip class=timeout" if failure == "timeout" else "stage=import class=import_failed"
    )
    assert log.rstrip().endswith(f"[bootstrap] {marker}")


def test_redaction_failure_omits_output_and_keeps_the_failure_marker(monkeypatch):
    """诊断脱敏器异常时也不能把未脱敏正文交给进度接口，失败分类仍须保留。"""
    from tavotto.engine import diagnostictext

    canary = "sk-SyntheticRedactionFailure123"
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: "/usr/bin/python3")

    def broken_redactor(text):
        raise RuntimeError(canary)

    def fake_run(cmd):
        if "venv" in cmd:
            bootstrap.venv_python().parent.mkdir(parents=True, exist_ok=True)
            bootstrap.venv_python().write_text("#!/bin/sh\n")
            return 0, ""
        return 1, f"pip failed with {canary}\n"

    monkeypatch.setattr(diagnostictext, "redact_text", broken_redactor)
    monkeypatch.setattr(bootstrap, "_run", fake_run)
    events = []
    assert bootstrap.install(events.append)["ok"] is False
    assert all(canary not in event["log"] for event in events)
    log = bootstrap.progress()["log"]
    assert "[bootstrap] output omitted: redaction failed" in log
    assert log.rstrip().endswith("[bootstrap] stage=pip class=exit:1")


def test_bootstrap_and_diagnostics_share_text_redaction(monkeypatch):
    """诊断包兼容入口与 bootstrap 共用同一份规则，抽取不能丢掉任何一类脱敏。"""
    from tavotto.engine import diagnostics, diagnostictext

    assert diagnostics._redact_text is diagnostictext.redact_text
    ident = "12345678-1234-1234-1234-123456789abc"
    config.save({"telemetry": {"install_id": ident}})
    before = config.load()
    monkeypatch.setattr(diagnostictext.os.path, "expanduser", lambda _: "/home/synthetic-user")
    monkeypatch.setenv("USER", "synthetic-user")
    for raw, expected in (
        ("sk-SyntheticCredential123", "***"),
        ("ghp_SyntheticCredential123", "***"),
        ("person@example.com", "<email>"),
        ("/home/synthetic-user/file", "~/file"),
        ("synthetic-user", "<user>"),
        (ident, "***"),
    ):
        assert diagnostictext.redact_text(raw) == expected
        assert diagnostics.redact_text(raw) == expected
        assert bootstrap._sanitize(raw) == expected
    assert diagnostics._redact_text(
        "/private-project/file", [("/private-project", "<project>")]
    ) == ("<project>/file")
    assert config.load() == before, "脱敏只能读取现有标识，不生成或修改遥测配置"


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
    assert out["ok"] is True, f"{out}\n{bootstrap.progress()['log']}"
    assert (
        subprocess.run([out["python"], "-c", "import matplotlib"], capture_output=True).returncode
        == 0
    )


# ---------------- HTTP 端点 ---------------------------------------------------
@pytest.fixture
def client():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    return m.app.test_client()


def test_environment_endpoint_reports_status(client, monkeypatch):
    monkeypatch.setattr(pool, "find_worker_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p, **_kw: "3.11.1")
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
    # 全局路径与项目路径同一份体检（#435）：这里假装体检的结论是「没有 matplotlib」
    fake = tmp_path / "python3"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setattr(
        projectenv,
        "probe_environment",
        lambda p, module=None: {"ok": False, "code": projectenv.ERROR_NO_MATPLOTLIB},
    )
    resp = client.patch("/api/engine/environment", json={"python": str(fake)})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "interpreter_no_matplotlib"
    assert "matplotlib" in resp.get_json()["error"]


def test_set_python_accepts_and_persists(client, monkeypatch, tmp_path):
    fake = tmp_path / "python3"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setattr(projectenv, "probe_environment", lambda p, module=None: {"ok": True})
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p, **_kw: "3.11.1")
    monkeypatch.setattr(pool, "find_worker_python", lambda: str(fake))
    resp = client.patch("/api/engine/environment", json={"python": str(fake)})
    assert resp.status_code == 200
    assert config.worker_python() == str(fake)


def test_set_python_empty_clears_back_to_autodetect(client, monkeypatch, tmp_path):
    config.set_worker_python(str(tmp_path / "old"))
    monkeypatch.setattr(pool, "find_worker_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p, **_kw: "3.11.1")
    client.patch("/api/engine/environment", json={"python": ""})
    assert config.worker_python() is None


def test_render_failure_carries_machine_readable_code(client, monkeypatch, tmp_path):
    """前端靠 code 区分「缺环境」与「脚本报错」，不能只回一段文字。"""
    from tavotto import app as m

    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "p1.pdf").write_bytes(b"%PDF-1.4\n")
    m.open_project(str(figs))
    monkeypatch.setattr(
        m.engine_registry.Registry,
        "for_stem",
        lambda self, s: {"script": "x.py", "entry": "main", "cost": "light"},
    )

    def boom(*a, **kw):
        raise pool.WorkerError("找不到装有 matplotlib 的 Python", code="no_worker_python")

    monkeypatch.setattr(m.engine_pool, "get", boom)

    resp = client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []})
    assert resp.status_code == 500
    assert resp.get_json()["code"] == "no_worker_python"
