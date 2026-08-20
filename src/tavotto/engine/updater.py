"""检查更新与自我升级（纯标准库，Flask 父进程 import）。

设计取舍：
- **联网是可关的**。默认每 24 小时向 GitHub Releases 查一次，用户可在
  「设置 → 检查更新」关掉；关掉后除非手动点「立即检查」，这条通道
  一个字节都不往外发。查询只发 GET，不带任何身份或使用数据。
- **它不再是唯一的对外请求**（2026-08-20）。同一个进程里还有一条
  `engine/telemetry.py` 的匿名用量通道，但那条**默认关闭、要显式同意**，
  且与本模块的开关完全独立：`TAVOTTO_NO_UPDATE_CHECK` 只管更新检查，
  `TAVOTTO_NO_TELEMETRY` 只管遥测，谁都不代管对方。
- **升级方式跟着安装方式走**。pipx 装的用 `pipx upgrade`，pip 装的用
  `pip install --upgrade`，git 检出的不代劳（只告诉用户 `git pull`）——
  在源码树里跑 pip 会把用户的工作副本覆盖掉。
- **升级后必须重启**。运行中的进程已经把旧代码 import 进内存了，就地热替换
  只会得到半新半旧的状态机。升级完成回 restart_required，由界面提示重启。
- **不做静默自动升级**。学术制图要的是可复现：版本什么时候变、变成什么，
  必须是用户按下按钮的结果。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import brand, config, runtime, telemetry

CHECK_INTERVAL_S = 24 * 3600
NETWORK_TIMEOUT_S = 6
UPGRADE_TIMEOUT_S = 600

_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 版本号
# ---------------------------------------------------------------------------
def current_version() -> str:
    from .. import __version__
    return __version__


def parse_version(v: str) -> tuple:
    """宽松解析 `v1.2.3`、`1.2.3rc1`、`1.2` → 可比较元组。

    预发布版排在同数字的正式版之前：(1,2,3,0,'rc1') < (1,2,3,1,'')。
    解析不出来的回全零，永远比不过任何真实版本——宁可不提示更新，
    也不能因为一个畸形 tag 就催用户升级。
    """
    m = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$", (v or "").strip())
    if not m:
        return (0, 0, 0, 0, "")
    major, minor, patch, rest = m.groups()
    rest = (rest or "").strip(" .-+")
    return (int(major), int(minor or 0), int(patch or 0), 0 if rest else 1, rest)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


# ---------------------------------------------------------------------------
# 安装方式探测
# ---------------------------------------------------------------------------
def install_method() -> str:
    """"pipx" / "pip" / "source" —— 决定用哪条升级命令，以及能不能代劳。"""
    pkg_root = Path(__file__).resolve().parent.parent      # …/tavotto
    # 源码检出：包目录上面两级还躺着 pyproject.toml（src 布局）
    for up in (pkg_root.parent, pkg_root.parent.parent):
        if (up / "pyproject.toml").is_file():
            return "source"
    prefix = Path(sys.prefix).resolve()
    pipx_home = os.environ.get("PIPX_HOME")
    if "pipx" in prefix.parts or (pipx_home and str(prefix).startswith(pipx_home)):
        return "pipx"
    return "pip"


def _wheel_url(release: dict) -> str | None:
    for asset in release.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.endswith(".whl") and name.startswith(brand.DIST_NAME.replace("-", "_")):
            return asset.get("browser_download_url")
    return None


def upgrade_command(release: dict | None = None) -> list[str] | None:
    """升级命令；source 安装回 None（不代劳）。

    优先装 Release 上的 wheel——这样没发 PyPI 也能升级；没有 wheel 资产时
    退回按包名装（发布到 PyPI 之后自然走这条）。
    """
    method = install_method()
    if method == "source":
        return None
    target = (release and _wheel_url(release)) or brand.DIST_NAME
    if method == "pipx":
        # pipx upgrade 只认包名；要装指定 URL 得用 install --force
        if target == brand.DIST_NAME:
            return ["pipx", "upgrade", brand.DIST_NAME]
        return ["pipx", "install", "--force", target]
    return [sys.executable, "-m", "pip", "install", "--upgrade", target]


# ---------------------------------------------------------------------------
# 设置与缓存（存在用户配置里，跟着 config.json 走）
# ---------------------------------------------------------------------------
def settings() -> dict:
    cfg = config.load().get("updates") or {}
    return {"auto_check": bool(cfg.get("auto_check", True)),
            "last_check_ms": int(cfg.get("last_check_ms") or 0),
            "last_result": cfg.get("last_result") or None}


def set_settings(patch: dict) -> dict:
    with _LOCK:
        cfg = config.load()
        merged = {**(cfg.get("updates") or {}), **patch}
        cfg["updates"] = {k: v for k, v in merged.items() if v is not None}
        config.save(cfg)
    return settings()


# ---------------------------------------------------------------------------
# 检查
# ---------------------------------------------------------------------------
def _fetch_latest_release() -> dict:
    req = urllib.request.Request(
        brand.RELEASES_API,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"{brand.PRODUCT_NAME}/{current_version()}"},
    )
    with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check(force: bool = False) -> dict:
    """回 {current, latest, update_available, …}。

    force=False 时：关了自动检查、或距上次检查不到 24 小时，都直接回缓存，
    不联网。界面上的「立即检查」传 force=True。
    """
    st = settings()
    base = {"current": current_version(), "method": install_method(),
            "auto_check": st["auto_check"], "repo_url": brand.REPO_URL,
            "releases_url": brand.RELEASES_URL}
    fresh = (time.time() * 1000 - st["last_check_ms"]) < CHECK_INTERVAL_S * 1000
    if not force and (not st["auto_check"] or fresh):
        cached = dict(st["last_result"] or {})
        # update_available 必须按**当前运行版本**现算：缓存里存的是按检查
        # 当时的版本比出来的结果，升级并重启后原样回放会出现「有新版本
        # 0.4.0（当前 0.4.0）」，纠缠用户直到 24h 节流过期
        latest = str(cached.get("latest") or "")
        cached["update_available"] = bool(latest) and is_newer(latest, current_version())
        return {**base, **cached, "cached": True,
                "checked_at_ms": st["last_check_ms"]}

    try:
        release = _fetch_latest_release()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        # 离线是常态而不是错误：如实回报，不打断任何操作
        return {**base, "error": f"检查失败: {exc}", "update_available": False,
                "cached": False, "checked_at_ms": int(time.time() * 1000)}

    latest = str(release.get("tag_name") or "").lstrip("v")
    cmd = upgrade_command(release)
    result = {
        "latest": latest,
        "update_available": bool(latest) and is_newer(latest, current_version()),
        "notes": (release.get("body") or "")[:4000],
        "published_at": release.get("published_at"),
        "html_url": release.get("html_url") or brand.RELEASES_URL,
        "can_self_update": cmd is not None,
        "upgrade_command": " ".join(cmd) if cmd else "git pull",
    }
    set_settings({"last_check_ms": int(time.time() * 1000), "last_result": result})
    return {**base, **result, "cached": False,
            "checked_at_ms": int(time.time() * 1000)}


def check_in_background() -> None:
    """启动时的静默检查——失败一律吞掉，绝不影响进程启动。

    `TAVOTTO_NO_UPDATE_CHECK=1` 完全关掉它：CI 冒烟与断网启动测试不该被它
    拖住（也不该因为 GitHub 不可达而变慢）。**只管这一条通道**——匿名遥测
    有自己的 `TAVOTTO_NO_TELEMETRY`，两个开关刻意不互相代管：把它们合成一个，
    用户想关掉用量统计就得连安全更新提醒一起关掉。
    """
    if os.environ.get("TAVOTTO_NO_UPDATE_CHECK"):
        return

    def run():
        try:
            check(force=False)
        except Exception:  # noqa: BLE001 — 后台探测不允许把主进程带下水
            pass
    if settings()["auto_check"]:
        threading.Thread(target=run, daemon=True, name="mm-update-check").start()


# ---------------------------------------------------------------------------
# 升级
# ---------------------------------------------------------------------------
def apply_upgrade() -> dict:
    """执行升级命令。回 {ok, command, log, restart_required}。"""
    try:
        release = _fetch_latest_release()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        release = None
    method = install_method()
    cmd = upgrade_command(release)
    if cmd is None:
        return {"ok": False, "command": "git pull", "restart_required": False,
                "log": "这是源码检出的运行方式，请在仓库目录执行 git pull 后重启。"}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              # 显式 UTF-8：text=True 默认跟随系统区域编码，
                              # cp936 下 pip 的进度条/中文路径一解码就抛
                              # UnicodeDecodeError，直接逃出 apply_upgrade 变 500
                              encoding="utf-8", errors="replace",
                              timeout=UPGRADE_TIMEOUT_S,
                              creationflags=runtime.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "command": " ".join(cmd), "restart_required": False,
                "log": f"升级命令执行失败: {exc}"}
    log = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0
    if ok:
        # **装成功之后**才记一条，且不带 release notes 的任何文字。
        # 埋点失败绝不影响这里的返回值：capture() 自己吞掉一切。
        telemetry.capture("update_completed", {
            "update_kind": method,
            "target_version": str((release or {}).get("tag_name") or "").lstrip("v"),
        })
    return {"ok": ok, "command": " ".join(cmd), "restart_required": ok,
            "log": log[-8000:]}
