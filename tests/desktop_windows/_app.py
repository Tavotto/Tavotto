"""装好的 Tavotto 桌面版：起、关（WM_CLOSE）、强杀、附着 WebDriver。

主语钉死：被测对象是 `TAVOTTO_DESKTOP_EXE` 指向的**安装产物里的壳**（NSIS 装出来的
`Tavotto.exe`），它自己拉起 sidecar、自己建 WebView2——用例不碰 sidecar、不走浏览器。
WebView2 的远程调试端口由 HKLM 策略 `AdditionalBrowserArguments`（值名 `Tavotto.exe`）
打开：环境变量 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` 会被 Tauri 自带的参数覆盖、HKCU 策略
不生效（2026-09-29 在 Windows Server 2025 + 正式版 0.17.0 上实测），这一步归 CI 步骤，
这里只核对它在不在（`policy_problem()`）。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import _win32 as w
from _webdriver import Driver

SHELL_WINDOW_CLASS = "Tauri Window"
POLICY_KEY = r"SOFTWARE\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments"

#: 页面「起来了」：在 sidecar 的 http 源上（不是 tauri:// 的启动页），React 根上有东西
PAGE_READY = """
return location.protocol === 'http:' && document.readyState === 'complete'
  && !!document.querySelector('#root') && document.querySelector('#root').children.length > 0
"""
#: 两种落点：项目编辑器 / 主页
PROJECT_OPEN = "return !!document.querySelector('[data-project-switcher]')"
HOME_SHOWN = "return !!document.querySelector('[data-home-variant]')"


def policy_problem(exe: Path, port: int) -> str | None:
    """HKLM 策略没配好时说清是哪一项；配好了回 None。"""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY) as k:
            value, _ = winreg.QueryValueEx(k, exe.name)
    except OSError:
        return f"HKLM\\{POLICY_KEY} 下没有值 {exe.name}（CI 步骤负责写入，见 nightly.yml windows-install）"
    if f"--remote-debugging-port={port}" not in value:
        return f"HKLM 策略 {exe.name} = {value!r}，里面没有 --remote-debugging-port={port}"
    return None


def cdp_version(port: int, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
            return json.loads(r.read())
    except OSError:
        return None


class DesktopApp:
    """一个被测实例。用例之间不共用：每条用例自己起、自己关，失败时 `stop()` 兜底强杀。"""

    def __init__(self, exe: Path, driver_url: str, port: int, home: Path):
        self.exe = exe
        self.driver_url = driver_url
        self.port = port
        #: 数据 / 配置目录跨「关掉再开」保持不变——第 9 / 12 项量的正是它们
        self.data_dir = home / "data"
        self.config_dir = home / "config"
        self.proc: subprocess.Popen | None = None
        #: 本实例的子进程（sidecar / WebView2），按**句柄**持有：PID 会被复用，陈旧 PID 绝不拿来杀
        self.tree: list[w.ProcHandle] = []
        self.driver: Driver | None = None
        #: 反证用（conftest 的 TAVOTTO_DW_INJECT_JS）：每次界面就绪后注进页面
        self.inject_js: str | None = None
        #: 这条用例还没起过应用：第一次起来时先清掉 WebView2 里这个 origin 的本机存储（见 `launch`）
        self.fresh = True

    # ---------------------------------------------------------------- 环境

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        # 仓库 tests/conftest.py 为后端单测设的几条不该漏进被测产物：装好的桌面版要自己
        # 发现 workerd 与内置 runtime（nightly 冒烟的同一条约定：不设 TAVOTTO_WORKERD）
        for k in ("TAVOTTO_WORKERD", "TAVOTTO_WORKER_PYTHON", "TAVOTTO_USER_ENV_DISCOVERY"):
            env.pop(k, None)
        env.update(
            TAVOTTO_DATA_DIR=str(self.data_dir),
            TAVOTTO_CONFIG_DIR=str(self.config_dir),
            TAVOTTO_NO_TELEMETRY="1",
            TAVOTTO_NO_UPDATE_CHECK="1",
        )
        return env

    def foreign_shells(self) -> list[w.Window]:
        """别的 Tavotto 壳：单实例插件会把我们这次启动转交给它，然后我们的进程直接退出。"""
        mine = {self.proc.pid} if self.proc and self.proc.poll() is None else set()
        return [
            win
            for win in w.top_windows()
            if win.cls == SHELL_WINDOW_CLASS and win.title == "Tavotto" and win.pid not in mine
        ]

    # ---------------------------------------------------------------- 起

    def launch(self, *args: str, expect: str = "project", timeout: float = 120.0) -> Driver:
        assert self.proc is None or self.proc.poll() is not None, "上一个实例还活着"
        others = self.foreign_shells()
        assert not others, f"已有别的 Tavotto 窗口在跑（单实例会吞掉这次启动）：{others}"
        assert cdp_version(self.port) is None, (
            f"127.0.0.1:{self.port} 在应用起来之前就已经有调试端点——附着上去的不会是这次起的窗口"
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._release_tree()
        self.proc = subprocess.Popen([str(self.exe), *args], env=self.env(), cwd=str(self.data_dir))
        deadline = time.monotonic() + timeout

        # ① 壳的主窗口（按 pid 过滤：只认这次起的这个进程）
        while self.window() is None:
            if self.proc.poll() is not None:
                raise AssertionError(f"壳自己退了（退出码 {self.proc.returncode}），连窗口都没建")
            if time.monotonic() > deadline:
                raise TimeoutError("壳的主窗口一直没出现")
            time.sleep(0.25)

        # ② WebView2 的调试端点（策略打开的那个端口）
        while cdp_version(self.port) is None:
            if self.proc.poll() is not None:
                raise AssertionError(f"壳在 WebView2 起来之前退了（退出码 {self.proc.returncode}）")
            if time.monotonic() > deadline:
                raise TimeoutError(f"{self.port} 上一直没有 WebView2 调试端点（HKLM 策略没生效？）")
            time.sleep(0.25)

        # ③ 附着并等页面换到 sidecar 的源上
        self.driver = Driver(self.driver_url, f"127.0.0.1:{self.port}")
        self.ready(expect, timeout=max(5.0, deadline - time.monotonic()))
        # ④ 用例之间的隔离：壳把端口记在 Tauri 的 app_config_dir（#718，ADR 0108——这正是 #715 要的
        # 「origin 跨启动不变」），WebView2 的用户数据目录也是全机一份。于是本 job 里所有启动（前面装机
        # 冒烟的、上一条用例的）落在同一个 origin，UI 偏好（左栏收起、上次打开的排版……）跨用例带过来：
        # 2026-09-30 nightly 36588916009 里 9/12 条都卡在「左栏收起、找不到素材卡」。以前每次启动端口随机
        # = 新 origin = 空存储，隔离是白捡的。这里在**本条用例第一次**起来时清掉这个 origin 的本机存储
        # 再重载；同一条用例里的「关掉再开 / 强杀再开」不清——第 9 / 12 项量的正是它们跨启动留下来。
        # 不靠 WEBVIEW2_USER_DATA_FOLDER / 改 LOCALAPPDATA：Tauri 自己给 WebView2 传数据目录（取自系统
        # 已知文件夹），环境变量是否生效要另证，而这条做法不依赖它
        if self.fresh:
            self.fresh = False
            left = self.driver.js(
                "localStorage.clear(); sessionStorage.clear(); return localStorage.length"
            )
            assert left == 0, f"清不掉这个 origin 的本机存储（还剩 {left} 条）：用例之间没有隔离"
            # 重载时应用自己的 beforeunload（documentStore 的兜底冲刷）会把内存里带过来的状态写回去：
            # 在 pagehide 上再清一次——它晚于 beforeunload、且排在应用已挂的监听者之后
            self.driver.js(
                "addEventListener('pagehide', () => { localStorage.clear(); sessionStorage.clear() },"
                " { once: true }); location.reload()"
            )
            self.ready(expect, timeout=max(5.0, deadline - time.monotonic()))
        # sidecar 等子进程：关掉 / 杀掉之后要逐个确认它们跟着没了
        self._capture_tree()
        return self.driver

    def _capture_tree(self) -> None:
        root = w.open_process(self.proc.pid, self.exe.name)  # Popen 自己也持着句柄：PID 此刻可信
        assert root is not None, "壳刚起来就没了"
        try:
            self.tree = w.descendants(root)
        finally:
            root.close()

    def _release_tree(self) -> None:
        for p in self.tree:
            p.close()
        self.tree = []

    def ready(self, expect: str = "project", timeout: float = 60.0) -> None:
        """等界面落到 `expect`（project / home / any）；页面重新加载之后也调它。"""
        d = self.driver
        assert d is not None
        d.wait_js(PAGE_READY, timeout=timeout, what="界面在 sidecar 的 http 源上渲染出来")
        if expect == "project":
            d.wait_js(PROJECT_OPEN, timeout=timeout, what="项目编辑器（data-project-switcher）")
        elif expect == "home":
            d.wait_js(HOME_SHOWN, timeout=timeout, what="主页（data-home-variant）")
        if self.inject_js:
            d.js(self.inject_js)

    def window(self) -> w.Window | None:
        if self.proc is None:
            return None
        for win in w.top_windows({self.proc.pid}):
            if win.cls == SHELL_WINDOW_CLASS and win.visible:
                return win
        return None

    def hwnd(self) -> int:
        win = self.window()
        assert win is not None, "找不到壳的主窗口"
        return win.hwnd

    def origin(self) -> str:
        assert self.driver is not None
        return self.driver.js("return location.origin")

    # ---------------------------------------------------------------- 关 / 杀

    def _detach(self) -> None:
        if self.driver is not None:
            self.driver.close()
            self.driver = None

    def close_window(self, timeout: float = 60.0) -> int:
        """与点标题栏 × 同一条消息（WM_CLOSE），等壳自己退出。回壳的退出码。"""
        hwnd = self.hwnd()
        self._detach()
        w.post_close(hwnd)
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"WM_CLOSE 之后 {timeout:.0f}s 壳还没退（关窗询问闸卡住？）"
            ) from None

    def kill(self) -> None:
        """强杀壳（TerminateProcess）：没有关窗询问闸、没有 beforeunload、没有最后一次冲刷。"""
        assert self.proc is not None and self.proc.poll() is None, "要杀的实例已经不在了"
        self._detach()
        self.proc.kill()  # Popen 经它自己持有的进程句柄调 TerminateProcess
        self.proc.wait(timeout=30)

    def leftovers(self, timeout: float = 30.0) -> list[w.ProcHandle]:
        """壳没了之后，它起过的子进程（sidecar / WebView2）在 `timeout` 内还活着的。"""
        deadline = time.monotonic() + timeout
        while True:
            alive = [p for p in self.tree if p.alive()]
            if not alive or time.monotonic() > deadline:
                return alive
            time.sleep(0.5)

    def stop(self) -> None:
        """兜底收尾：只杀本实例起的进程树，别的一概不碰。"""
        self._detach()
        if self.proc is not None and self.proc.poll() is None:
            if not self.tree:
                self._capture_tree()
            self.proc.kill()
            self.proc.wait(timeout=30)
        for p in self.leftovers(timeout=15):
            p.terminate()
        self._release_tree()
        # 调试端点跟着 WebView2 一起没了，下一条用例才能确认它附着的是新窗口
        deadline = time.monotonic() + 20
        while cdp_version(self.port) is not None and time.monotonic() < deadline:
            time.sleep(0.25)
