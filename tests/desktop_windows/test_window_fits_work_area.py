"""主窗口不大于所在显示器的工作区（QA-WIN-10 / 17；`src-tauri/src/window_fit.rs`）。

窗口的初始尺寸与最小尺寸是逻辑像素（1280×860 / 1024×680）。屏幕或工作区装不下时，网页以为视口有
那么高、窗口下缘却落在屏幕 / 任务栏之外：底部按钮被挡住、滚轮也滚不出来（页面根本没有溢出）。
壳在建窗时按工作区夹一次，恢复上次状态之后再夹一次（插件的 `restore_state` 原样设回保存过的物理尺寸）。

只验「按工作区夹取」这条逻辑：Lab / CI 的交互会话不是高 DPI，125% 缩放本身验不到（单位换算由
`window_fit.rs` 的 cargo 用例覆盖）。若这台机器的屏幕本来就比 1280×860 大，用例仍成立，但前一条
没有「撤修复必红」的力度——超大的保存状态那一条在任何屏幕上都有。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

#: GetWindowRect 含系统的不可见缩放边框：容差只放这一圈，不放整块
SLACK = 16
STATE_NAME = ".window-state.json"


def _state_file() -> Path:
    # Tauri 的 app_config_dir（identifier = com.tavotto.tavotto），window-state 插件存在这里
    return Path(os.environ["APPDATA"]) / "com.tavotto.tavotto" / STATE_NAME


@pytest.fixture
def window_state():
    """把壳的窗口状态文件换成这条用例要的内容，用完原样还回去（排在 desktop_app 之前 = 它停了之后才还）。"""
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_suffix(".json.bak-dwtest")
    had = path.exists()
    if had:
        shutil.copy2(path, backup)
        path.unlink()

    def write(state: dict | None) -> None:
        if state is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps({"main": state}), encoding="utf-8")

    yield write
    path.unlink(missing_ok=True)
    if had:
        shutil.move(backup, path)


def _inside(rect, work) -> list[str]:
    left, top, right, bottom = rect
    wl, wt, wr, wb = work
    bad = []
    if left < wl - SLACK:
        bad.append(f"左缘 {left} < 工作区 {wl}")
    if top < wt - SLACK:
        bad.append(f"上缘 {top} < 工作区 {wt}")
    if right > wr + SLACK:
        bad.append(f"右缘 {right} > 工作区 {wr}")
    if bottom > wb + SLACK:
        bad.append(f"下缘 {bottom} > 工作区 {wb}")
    return bad


def _settled_rect(win32, hwnd, timeout=15.0):
    """窗口尺寸连续两次读数相同才算落定（启动后还有恢复状态 / 夹取的几次 set_size）。"""
    last, deadline = None, time.monotonic() + timeout
    while time.monotonic() < deadline:
        cur = win32.window_rect(hwnd)
        if cur == last:
            return cur
        last = cur
        time.sleep(0.5)
    return last


def test_main_window_fits_the_work_area_and_its_minimum_does_too(desktop_app, window_state, win32):
    win32.make_dpi_aware()
    window_state(None)  # 没有保存的状态：走建窗时的 1280×860 / 1024×680
    desktop_app.launch(expect="home")  # 全新配置目录、不带 --open：落在主页
    hwnd = desktop_app.hwnd()
    work = win32.work_area_of(hwnd)
    assert not win32.is_maximized(hwnd), "新装状态下主窗口不该是最大化的"
    rect = _settled_rect(win32, hwnd)
    bad = _inside(rect, work)
    assert not bad, f"窗口外框 {rect} 超出工作区 {work}：{'；'.join(bad)}"

    # 往小拖：系统按窗口的最小尺寸限制夹住它，量出来的最小外框也得在工作区以内
    win32.resize_window(hwnd, 200, 200)
    time.sleep(1.0)
    left, top, right, bottom = win32.window_rect(hwnd)
    wl, wt, wr, wb = work
    assert right - left <= (wr - wl) + SLACK, f"最小宽度 {right - left} > 工作区宽 {wr - wl}"
    assert bottom - top <= (wb - wt) + SLACK, f"最小高度 {bottom - top} > 工作区高 {wb - wt}"


def test_oversized_saved_window_state_is_clamped_to_the_work_area(desktop_app, window_state, win32):
    win32.make_dpi_aware()
    # 老用户在大屏 / 低缩放下保存过的巨大窗口（物理像素）：升级后在小屏上恢复
    window_state(
        {
            "width": 5000,
            "height": 3000,
            "x": 0,
            "y": 0,
            "prev_x": 0,
            "prev_y": 0,
            "maximized": False,
            "visible": True,
            "decorated": False,
            "fullscreen": False,
        }
    )
    desktop_app.launch(expect="home")  # 全新配置目录、不带 --open：落在主页
    hwnd = desktop_app.hwnd()
    work = win32.work_area_of(hwnd)
    assert not win32.is_maximized(hwnd)
    rect = _settled_rect(win32, hwnd)
    bad = _inside(rect, work)
    assert not bad, f"保存状态 5000×3000 恢复后窗口外框 {rect} 超出工作区 {work}：{'；'.join(bad)}"
