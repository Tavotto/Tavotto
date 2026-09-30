"""真 Tauri 窗口用例（issue #542）的夹具：装好的 NSIS 产物 + WebView2 + msedgedriver 附着。

**只有一条腿真跑它们**：`nightly.yml` 的 `windows-install`（「无 Python」档），在那条腿
刚装好的安装产物上，带 `TAVOTTO_DESKTOP_WINDOW_REQUIRED=1`——那里缺任何前提都是**红**，
不是 skip（`tests/test_e2e_leg_topology.py::TestDesktopWindowLeg` 钉住这条腿存在、带着这个
开关、跑的正是本目录）。别处（开发机、PR 快档的 pytest 分片、sdist）没有装好的桌面版，
整目录 skip，理由里点名那条腿。

需要的输入（都由那条 CI 步骤给）：

* `TAVOTTO_DESKTOP_EXE` —— 装出来的壳 `Tavotto.exe`；
* `TAVOTTO_MSEDGEDRIVER` —— 与本机 WebView2 Runtime 同版本的 msedgedriver（验过签名）；
* `TAVOTTO_WEBVIEW2_DEBUG_PORT` —— HKLM 策略打开的调试端口（缺省 9222）；
* `TAVOTTO_DW_ARTIFACTS` —— 可选，失败时截图与页面状态落在这里（CI 上传）；
* `TAVOTTO_DW_INJECT_JS` —— **只给反证用**：每次界面就绪后注进页面的一段脚本（把被测行为
  弄坏，确认用例会红）。日常运行不设；设了会在报告头上打出来，免得有人拿反证的结果当真。

输入的主语见 `_webdriver.py` / `_win32.py` 的模块说明：键鼠经 CDP 派进这个 WebView 的渲染
进程，窗口级动作按窗口句柄投递消息，**不注入任何全局输入**。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

#: 真跑本目录的那条腿（skip 理由里必须点名它；拓扑用例按这个字符串核对）
LEG = "nightly.yml → windows-install（无 Python 档）"
REQUIRED = os.environ.get("TAVOTTO_DESKTOP_WINDOW_REQUIRED") == "1"
#: 单条用例的总预算（秒）。实测最慢的一条（关掉再开）约 60 s；超出就当卡死处理
CASE_BUDGET_S = 300


def _port() -> int:
    return int(os.environ.get("TAVOTTO_WEBVIEW2_DEBUG_PORT", "9222"))


def _missing() -> list[str]:
    if sys.platform != "win32":
        return [f"不是 Windows（{sys.platform}）"]
    out = []
    exe = os.environ.get("TAVOTTO_DESKTOP_EXE")
    if not exe or not Path(exe).is_file():
        out.append(f"TAVOTTO_DESKTOP_EXE 没指到装好的壳（{exe!r}）")
    drv = os.environ.get("TAVOTTO_MSEDGEDRIVER")
    if not drv or not Path(drv).is_file():
        out.append(f"TAVOTTO_MSEDGEDRIVER 没指到 msedgedriver.exe（{drv!r}）")
    if exe and Path(exe).is_file():
        sys.path.insert(0, str(HERE))
        from _app import policy_problem

        problem = policy_problem(Path(exe), _port())
        if problem:
            out.append(problem)
    return out


_MISSING = _missing()


def pytest_report_header(config):
    lines = [f"desktop_windows: {'缺前提 → ' + '；'.join(_MISSING) if _MISSING else '前提齐全'}"]
    if os.environ.get("TAVOTTO_DW_INJECT_JS"):
        lines.append(
            f"desktop_windows: ⚠ 反证模式，注入 {os.environ['TAVOTTO_DW_INJECT_JS']}（结果不作数）"
        )
    return lines


def pytest_collection_modifyitems(config, items):
    if not _MISSING or REQUIRED:
        return
    mark = pytest.mark.skip(
        reason=f"真窗口用例只在 {LEG} 上真跑（那里带 TAVOTTO_DESKTOP_WINDOW_REQUIRED=1，缺前提即红）；"
        f"本机缺：{'；'.join(_MISSING)}"
    )
    for item in items:
        if HERE in Path(str(item.fspath)).resolve().parents:
            item.add_marker(mark)


@pytest.fixture(scope="session", autouse=True)
def _prerequisites():
    if _MISSING and REQUIRED:
        pytest.fail(
            f"TAVOTTO_DESKTOP_WINDOW_REQUIRED=1 但前提不齐：{'；'.join(_MISSING)}", pytrace=False
        )
    if sys.path[0] != str(HERE):
        sys.path.insert(0, str(HERE))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


# ---------------------------------------------------------------- 驱动


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def msedgedriver(_prerequisites):
    port = _free_port()
    proc = subprocess.Popen(
        [os.environ["TAVOTTO_MSEDGEDRIVER"], f"--port={port}", "--allowed-ips=127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while True:
        try:
            with urllib.request.urlopen(url + "/status", timeout=2) as r:
                if r.status == 200:
                    break
        except OSError:
            pass
        if proc.poll() is not None or time.monotonic() > deadline:
            raise AssertionError(f"msedgedriver 没起来（退出码 {proc.poll()}）")
        time.sleep(0.25)
    yield url
    proc.terminate()
    proc.wait(timeout=15)


@pytest.fixture
def win32():
    import _win32

    return _win32


@pytest.fixture
def desktop_app(request, msedgedriver, tmp_path):
    from _app import DesktopApp

    app = DesktopApp(
        Path(os.environ["TAVOTTO_DESKTOP_EXE"]), msedgedriver, _port(), tmp_path / "home"
    )
    inject = os.environ.get("TAVOTTO_DW_INJECT_JS")
    if inject:
        app.inject_js = Path(inject).read_text(encoding="utf-8")
    # 每条用例的总预算：到点就把被测实例整棵杀掉，后面的 WebDriver 调用当场失败——
    # 不让一个卡住的窗口把整条 nightly 腿拖到 job 超时（那时连是哪条卡住的都看不到）
    fired = threading.Event()

    def _budget_exceeded():
        fired.set()
        app.stop()

    dog = threading.Timer(CASE_BUDGET_S, _budget_exceeded)
    dog.daemon = True
    dog.start()
    yield app
    dog.cancel()
    if fired.is_set():
        pytest.fail(f"用例超过 {CASE_BUDGET_S}s 预算，被测实例已被强杀（卡在哪一步见上面的调用栈）")
    failed = getattr(request.node, "rep_call", None)
    if failed is not None and failed.failed:
        _dump_evidence(app, request.node.name)
    app.stop()


def _dump_evidence(app, name: str) -> None:
    out = os.environ.get("TAVOTTO_DW_ARTIFACTS")
    if not out or app.driver is None:
        return
    d = Path(out) / name
    d.mkdir(parents=True, exist_ok=True)
    try:
        import base64

        png = app.driver._call("GET", "/screenshot")
        (d / "window.png").write_bytes(base64.b64decode(png))
        state = app.driver.js(
            "return {url: location.href, text: document.body.innerText.slice(0, 4000),"
            " ls: Object.fromEntries(Object.keys(localStorage).map(k => [k, String(localStorage.getItem(k)).slice(0, 300)])),"
            " leftDrawer: !!document.querySelector('[data-left-drawer]')}"
        )
        (d / "page.txt").write_text(repr(state), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - 取证失败不该盖住真正的失败
        (d / "evidence-error.txt").write_text(repr(e), encoding="utf-8")


# ---------------------------------------------------------------- 项目


@pytest.fixture
def project_dir(tmp_path) -> Path:
    """中文 + 空格的项目目录，只放三张 PDF（「仅排版」素材，打开时不跑任何脚本）。"""
    proj = tmp_path / "我的 图库"
    proj.mkdir()
    pdfs = sorted((ROOT / "examples" / "figures").glob("*.pdf"))
    assert len(pdfs) >= 2, f"examples/figures 里的 PDF 不够：{pdfs}"
    for p in pdfs:
        shutil.copy2(p, proj / p.name)
    return proj
