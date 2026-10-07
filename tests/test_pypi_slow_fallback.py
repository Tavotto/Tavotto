"""PyPI 官方源太慢 / 超时 → TUNA 重试一次，两次共用 15 分钟预算，失败与换源进 app.log（ADR 0112 §二 / §三）。

2026-09-29 阿里云华东 Windows 实测：files.pythonhosted.org 约 20 KB/s，15 分钟撞 `dependency_install_timeout`，
这一代标 incomplete，而 app.log 里一个字都没有；TUNA 同样的 wheel 13 MB/s。ADR 0111（#743）的回退只认「网络类失败」，
慢而不断的线路从来不失败——这里补的是「慢」与「预算」。

判据的主语：

* 「换没换源 / 换去了哪」= **真 pip**（`pip download`，与 `pip install` 同一条下载路径、同样的
  `Downloading <url> (<大小>)` 输出）对着两个本地简单索引（`support.pypi_index`）：官方源一个、镜像一个，各自的
  请求日志说话。`build_argv(index_url)` 由用例给：`None` → 官方那个，`PYPI_MIRROR_URL` → 镜像那个——产品代码
  决定**何时**、**是否**要第二个，用例只负责把「TUNA」接到本地。
* 「用户配没配源」= 真 `pip config list`（`PIP_CONFIG_FILE` 指到用例写的文件 / `os.devnull`）。
* 阈值按用例缩短（`PIP_SLOW_GRACE_S` / `PIP_SLOW_BPS` / `PIP_STALL_S` / 预算）：判据的形状不变，只是秒数。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from support.pip_config_isolation import isolated_pip_globals
from support.pypi_index import NAME, SimpleIndex, make_wheel
from tavotto.engine import deprepair

MIRROR = deprepair.PYPI_MIRROR_URL
SOURCE_ENV = ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_NO_INDEX", "PIP_FIND_LINKS")


@pytest.fixture(autouse=True)
def _clean_pip_config(monkeypatch):
    """这台机器的 pip 配置不许进判据：配置文件一律不读，四个源变量清掉。"""
    for name in SOURCE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PIP_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("PIP_DISABLE_PIP_VERSION_CHECK", "1")


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(deprepair, "PIP_SLOW_GRACE_S", 0.5)
    monkeypatch.setattr(
        deprepair, "PIP_SLOW_BPS", 400_000
    )  # 410 kB 的 wheel：超过约 1 s 没下完即太慢
    monkeypatch.setattr(deprepair, "PIP_STALL_S", 60.0)


@pytest.fixture
def wheel(tmp_path) -> Path:
    return make_wheel(tmp_path / "wheels")


def _builder(official: SimpleIndex | None, mirror: SimpleIndex, dest: Path):
    """`build_argv(index_url)`：真 pip，官方源 / 镜像都是本地索引（`official=None`：官方源是一个没人听的端口——
    连不上）。`--retries 1` 让连不上快点收场。"""
    official_url = official.url if official is not None else _closed_port_url()

    def build(index_url):
        if index_url is not None:
            assert index_url == MIRROR, index_url  # 产品只会换到这一个
        url = mirror.url if index_url else official_url
        return [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--no-deps",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--no-input",
            "--only-binary=:all:",
            "--retries",
            "1",
            "-d",
            str(dest),
            "--index-url",
            url,
            NAME,
        ]

    return build


def _closed_port_url() -> str:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/simple"


def _install(build, *, python=sys.executable):
    sources: list[str] = []
    mirrors: list[str] = []
    logs: list[str] = []
    t0 = time.monotonic()
    code, out = deprepair._run_pip_install(
        build,
        python,
        threading.Event(),
        logs.append,
        on_mirror=mirrors.append,
        on_source=sources.append,
    )
    return code, out, sources, mirrors, time.monotonic() - t0


def _files(index: SimpleIndex) -> int:
    return sum(p.startswith("/files/") for p in index.requests)


def _messages(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records if r.name == "tavotto.deprepair")


# ---------------------------------------------------------------- 慢：换源
def test_a_slow_official_index_is_abandoned_for_the_mirror(tmp_path, wheel, fast, caplog):
    caplog.set_level(logging.INFO, logger="tavotto.deprepair")
    dest = tmp_path / "dest"
    with SimpleIndex(wheel) as official, SimpleIndex(wheel) as mirror:
        official.throttle_bps = 20 * 1024  # 实测那台机器的量级：410 kB 要 20 s
        code, out, sources, mirrors, elapsed = _install(_builder(official, mirror, dest))
        assert _files(official) == 1, "判据的前提：官方源真的开始下 wheel 了"
        assert _files(mirror) == 1
    assert code == "", out
    assert elapsed < 15, f"慢源应在量程过后就被放弃，实际 {elapsed:.1f} s"
    assert (dest / wheel.name).read_bytes() == wheel.read_bytes()
    assert sources == [deprepair.PIP_SOURCE_PYPI, deprepair.PIP_SOURCE_MIRROR]
    assert mirrors == [MIRROR]
    assert "太慢" in out and MIRROR in out  # 安装日志（界面上那份）说出来了
    text = _messages(caplog)
    assert "pip install：包源 pypi" in text, text
    assert f"换源：官方 PyPI {deprepair.PIP_SLOW_DOWNLOAD}" in text, text
    assert "pip install 完成：包源 tuna" in text, text


def test_an_index_page_that_never_answers_is_abandoned_for_the_mirror(
    tmp_path, wheel, fast, monkeypatch, caplog
):
    caplog.set_level(logging.WARNING, logger="tavotto.deprepair")
    monkeypatch.setattr(deprepair, "PIP_STALL_S", 1.5)
    dest = tmp_path / "dest"
    with SimpleIndex(wheel) as official, SimpleIndex(wheel) as mirror:
        official.mode = "hang"
        code, out, sources, _m, elapsed = _install(_builder(official, mirror, dest))
        assert official.requests, "判据的前提：pip 真的连上了官方源"
        assert _files(mirror) == 1
    assert code == "", out
    assert elapsed < 12  # pip 自己的套接字超时是 15 s：判据来自我们，不是它
    assert sources[-1] == deprepair.PIP_SOURCE_MIRROR
    assert f"换源：官方 PyPI {deprepair.PIP_STALLED}" in _messages(caplog)


def test_a_failing_official_index_still_falls_back_with_real_pip(tmp_path, wheel, fast, caplog):
    """ADR 0111 那一条（网络类失败：连不上）在真 pip 上同样成立，且结局进日志。"""
    caplog.set_level(logging.WARNING, logger="tavotto.deprepair")
    dest = tmp_path / "dest"
    with SimpleIndex(wheel) as mirror:
        code, out, sources, mirrors, _e = _install(_builder(None, mirror, dest))
        assert _files(mirror) == 1
    assert code == "", out
    assert mirrors == [MIRROR] and sources[-1] == deprepair.PIP_SOURCE_MIRROR
    assert f"换源：官方 PyPI {deprepair.ERROR_NETWORK}" in _messages(caplog)


# ---------------------------------------------------------------- 用户配过源：不测速、不换
@pytest.mark.parametrize(
    "conf",
    [
        "[global]\nindex-url = {url}\n",
        "[install]\nindex_url = {url}\n",  # pip 的键名规范化：`_` 与 `-` 是同一个键
        "[global]\nfind-links = /nonexistent-wheelhouse\n",
    ],
)
def test_a_user_configured_source_is_never_timed_or_switched(
    tmp_path, wheel, fast, monkeypatch, conf
):
    dest = tmp_path / "dest"
    with SimpleIndex(wheel) as official, SimpleIndex(wheel) as mirror:
        cfg = tmp_path / "pip.conf"
        cfg.write_text(conf.format(url=official.url), encoding="utf-8")
        monkeypatch.setenv("PIP_CONFIG_FILE", str(cfg))
        official.throttle_bps = 128 * 1024  # 约 3 s：测速开着的话早就换了
        code, out, sources, mirrors, elapsed = _install(_builder(official, mirror, dest))
        assert mirror.requests == []
    assert code == "", out
    assert elapsed > 2  # 真的等官方源下完了
    assert sources == [deprepair.PIP_SOURCE_USER] and mirrors == []


def test_a_download_only_index_setting_is_not_a_user_source(tmp_path, monkeypatch):
    """`[download]` 节不作用于 `pip install`：问 pip 之后按节筛，不按子串猜（#737 / ADR 0112 §二）。"""
    cfg = tmp_path / "pip.conf"
    cfg.write_text("[download]\nindex-url = https://pypi.corp/simple\n", encoding="utf-8")
    monkeypatch.setenv("PIP_CONFIG_FILE", str(cfg))
    env = isolated_pip_globals(dict(os.environ), tmp_path)
    monkeypatch.setenv("PYTHONPATH", env["PYTHONPATH"])
    assert deprepair.user_package_source(sys.executable) is False
    cfg.write_text("[install]\n--index-url = https://pypi.corp/simple\n", encoding="utf-8")
    assert deprepair.user_package_source(sys.executable) is True


# ---------------------------------------------------------------- 预算：两次共用
def test_both_attempts_share_one_budget(tmp_path, wheel, monkeypatch, caplog):
    """第一次最多用到「总预算 − 保底」，镜像那次拿剩下的；两次都慢时总耗时不超过一个总预算。"""
    caplog.set_level(logging.WARNING, logger="tavotto.deprepair")
    monkeypatch.setattr(deprepair, "INSTALL_TIMEOUT_S", 6.0)
    monkeypatch.setattr(deprepair, "PIP_MIRROR_RESERVE_S", 3.0)
    monkeypatch.setattr(deprepair, "PIP_SLOW_BPS", 1)  # 速度判据关掉：只剩预算这一条
    monkeypatch.setattr(deprepair, "PIP_STALL_S", 60.0)
    # 前提只钉「两个 pip 进程都真起来了、按先官方后镜像的顺序」：`_run_pip` 在 `Popen` 成功之后才调
    # `on_started`，这里包一层记下每次起来的是哪个索引。**不**要求哪一次走到 /files/——每次只有约 3 s，
    # 慢 runner 上 pip 光启动 + 取索引页就可能用完（镜像那次 226c2080 windows-latest、官方那次 #727 的
    # run 36790556401 windows-latest 各实测 0 次）；下没下到文件不是「两次共用一个预算」的主语。
    real_run_pip = deprepair._run_pip
    started: list[str] = []

    def _spy(argv, ev, log, *, on_started=None, **kw):
        url = argv[argv.index("--index-url") + 1]

        def _started():
            started.append(url)
            if on_started is not None:
                on_started()

        return real_run_pip(argv, ev, log, on_started=_started, **kw)

    monkeypatch.setattr(deprepair, "_run_pip", _spy)
    dest = tmp_path / "dest"
    with SimpleIndex(wheel) as official, SimpleIndex(wheel) as mirror:
        official.throttle_bps = mirror.throttle_bps = 16 * 1024  # 各要约 25 s：哪一次都不会自己下完
        code, _out, sources, _m, elapsed = _install(_builder(official, mirror, dest))
        assert started == [official.url, mirror.url], started
    assert code == deprepair.ERROR_TIMEOUT
    assert elapsed < 6.0 + 1.5, f"两次尝试共用 6 s 的预算，实际 {elapsed:.1f} s"
    assert sources == [deprepair.PIP_SOURCE_PYPI, deprepair.PIP_SOURCE_MIRROR]
    text = _messages(caplog)
    assert f"换源：官方 PyPI {deprepair.PIP_FIRST_BUDGET}" in text, text
    assert f"pip install 失败：{deprepair.ERROR_TIMEOUT}（包源 tuna）" in text, text


# ---------------------------------------------------------------- 信任：换了源，hash 照样核
def test_a_mirror_serving_tampered_bytes_is_refused(tmp_path, wheel, fast, caplog):
    """镜像回的字节对不上索引给的 sha256：pip 拒绝、不落盘，结局是 hash 不符，不会再换第三个源。"""
    caplog.set_level(logging.WARNING, logger="tavotto.deprepair")
    dest = tmp_path / "dest"
    with SimpleIndex(wheel) as mirror:
        mirror.mode = "corrupt"
        code, out, _s, _m, _e = _install(_builder(None, mirror, dest))
        assert _files(mirror) == 1, "判据的前提：确实从镜像拿了字节"
    assert code == deprepair.ERROR_HASH_MISMATCH, out
    assert not (dest / wheel.name).exists()
    assert f"pip install 失败：{deprepair.ERROR_HASH_MISMATCH}（包源 tuna）" in _messages(caplog)


# ---------------------------------------------------------------- 判据单测
def _watch(now=0.0, first_deadline=1e9):
    return deprepair._PipWatch(now, first_deadline)


def test_the_watch_only_convicts_a_download_that_is_provably_slower_than_the_line(monkeypatch):
    monkeypatch.setattr(deprepair, "PIP_SLOW_GRACE_S", 30.0)
    monkeypatch.setattr(deprepair, "PIP_SLOW_BPS", 100_000)
    w = _watch()
    w.feed("  Downloading numpy-2.3.0-cp313-cp313-win_amd64.whl (12.9 MB)\n", 0.0)
    assert w.verdict(29.0) == ""  # 宽限期内
    assert w.verdict(128.0) == ""  # 12.9 MB / 100 kB/s = 129 s：还没有证据
    assert w.verdict(130.0) == deprepair.PIP_SLOW_DOWNLOAD
    w = _watch()
    w.feed("  Downloading tiny.whl.metadata (60 kB)\n", 0.0)
    assert w.verdict(20.0) == ""  # 小文件在宽限期里不判
    w.feed("Collecting next\n", 20.5)  # 下一行来了 = 上一个文件下完了
    assert w.download is None


def test_the_watch_counts_stalls_only_in_the_network_phase(monkeypatch):
    monkeypatch.setattr(deprepair, "PIP_STALL_S", 90.0)
    w = _watch()
    w.feed("Collecting matplotlib\n", 0.0)
    assert w.verdict(95.0) == deprepair.PIP_STALLED
    w = _watch()
    w.feed("Installing collected packages: numpy, matplotlib\n", 0.0)
    assert w.verdict(500.0) == ""  # 装的阶段几分钟没输出（杀软扫 wheel）不是网络
    w = _watch(first_deadline=10.0)
    assert w.verdict(10.0) == deprepair.PIP_FIRST_BUDGET


@pytest.mark.parametrize(
    "opts,user,custom",
    [
        ({"index_url": "https://pypi.org/simple", "extra_index_urls": []}, False, False),
        ({"index_url": "https://pypi.org/simple/", "extra_index_urls": []}, False, False),
        ({"index_url": "https://pypi.corp/simple", "extra_index_urls": []}, True, True),
        ({"index_url": "https://pypi.org/simple", "extra_index_urls": ["https://x"]}, True, True),
        ({"index_url": "https://pypi.org/simple", "no_index": True}, True, False),
        ({"index_url": "https://pypi.org/simple", "find_links": ["/w"]}, True, False),
    ],
)
def test_the_options_verdicts(opts, user, custom):
    """pip 解析出的 install 选项 → 「用户说过从哪装」（含离线 wheelhouse）/「自定义索引」（诊断只问 index）。"""
    assert deprepair.options_name_a_user_source(opts) is user
    assert deprepair.options_name_a_custom_index(opts) is custom


# ---------------------------------------------------------------- 失败进 app.log（四个线程入口）
@pytest.mark.parametrize(
    "entry,call,inner",
    [
        ("联合依赖准备", lambda pid: deprepair._prepare_guarded(pid, None), "prepare"),
        ("依赖修复", lambda pid: deprepair._install_guarded(pid, None), "install"),
        (
            "受管环境重建",
            lambda pid: deprepair._rebuild_guarded("/nowhere", None, pid),
            "rebuild_managed",
        ),
        ("包操作", lambda pid: deprepair._run_package_job_guarded(pid, None), "run_package_job"),
    ],
)
def test_every_threaded_entry_logs_its_failure_with_code_and_source(
    monkeypatch, caplog, entry, call, inner
):
    caplog.set_level(logging.WARNING, logger="tavotto.deprepair")
    pid = "f" * 32

    def _boom(*_a, **_kw):
        raise deprepair.RepairError(deprepair.ERROR_TIMEOUT, "超时")

    monkeypatch.setattr(deprepair, inner, _boom)
    with deprepair._lock:
        deprepair._progress[pid] = {"log": "", "pypi_source": deprepair.PIP_SOURCE_MIRROR}
    try:
        call(pid)
    finally:
        with deprepair._lock:
            deprepair._progress.pop(pid, None)
    assert f"{entry}失败：{deprepair.ERROR_TIMEOUT}（包源 tuna）" in _messages(caplog)
