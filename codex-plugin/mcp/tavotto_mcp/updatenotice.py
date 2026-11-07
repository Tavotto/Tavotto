"""MCP 路径上的「有新版本」提醒：给 `tavotto_health` / `tavotto_export` 的结果挂一个 `update` 字段。

版本比较、清单、24 小时缓存、`TAVOTTO_DISABLE_UPDATE_CHECK` 全在
`skills/tavotto-figure/scripts/update_check.py`（交接路径也用它）——这里**只复用**，不写第二份。

## 四条底线（与 update_check 同一套）

1. **调用线程不碰网络**：只读缓存（`check(allow_network=False)`）；缓存到期就起一条
   daemon 线程去刷新（`check()` 自带 1.5 秒总墙钟），本次调用不等它，下一次调用才看得到。
2. **不污染 stdout**：本模块与 update_check 都不 print；stdout 归 MCP 协议独占。
3. **任何异常吞掉**：找不到模块、读缓存炸了、线程起不来，一律当作「没有新版本」。
4. **只提醒，不下载、不执行。**
5. **用户关了检查就一个包都不发**：`TAVOTTO_NO_UPDATE_CHECK`、`TAVOTTO_DISABLE_UPDATE_CHECK`、
   设置里的 `auto_check`（`engine/updater.settings()`）任一关闭都不联网，只读已有缓存。

## 版本来源

传给 `check(version=…)` 的是**这个 MCP 进程自己的真实版本**（`server._version()`），
不读 plugin.json：桌面版 `tavotto-cli mcp` 里没有它。发版时插件版本 == Tavotto 版本。

## 模块在哪（找不到就静默不提示）

* 桌面包（frozen）：`<_MEIPASS>/skills/tavotto-figure/scripts/`（`packaging/tavotto.spec` 收进去）
* 源码树 / 插件包：`<插件根>/skills/tavotto-figure/scripts/`（本文件在 `<插件根>/mcp/tavotto_mcp/`）
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time

_SCRIPTS_REL = ("skills", "tavotto-figure", "scripts")
_MODULE_NAME = "tavotto_mcp_update_check"
_HANDOFF_NAME = "tavotto_mcp_skill_handoff"

_lock = threading.Lock()
_module = None  # 已加载的 update_check；False = 找过了没有
_latest: dict | None = None  # 后台线程刷新出的结果
_started_at: float | None = None
_thread: threading.Thread | None = None
_export_delivered = False


def plugin_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def scripts_dirs() -> list[str]:
    """候选目录，按优先级。frozen 只认 `_MEIPASS`；源码树认插件根。"""
    out = []
    meipass = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass:
        out.append(os.path.join(meipass, *_SCRIPTS_REL))
    out.append(os.path.join(plugin_dir(), *_SCRIPTS_REL))
    return out


def how() -> str | None:
    """这个进程是怎么起来的：`desktop`（frozen 桌面版）/ `codex_plugin`（插件包）/ 判不出 None。"""
    if getattr(sys, "frozen", False):
        return "desktop"
    if os.path.isfile(os.path.join(plugin_dir(), ".codex-plugin", "plugin.json")):
        return "codex_plugin"
    return None


def _exec(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load():
    global _module
    if _module is not None:
        return _module or None
    mod = False
    try:
        for d in scripts_dirs():
            path = os.path.join(d, "update_check.py")
            if not os.path.isfile(path):
                continue
            # 两个模块都按文件路径加载，不碰 sys.path：仓库里有两个 handoff.py，`from handoff import`
            # 在 `tavotto/engine/` 排在前面时拿到的是引擎那个，缓存读不到，提醒就静默没了
            m = _exec(path, _MODULE_NAME)
            m._config_dir = _exec(os.path.join(d, "handoff.py"), _HANDOFF_NAME).config_dir
            mod = m
            break
    except Exception:  # noqa: BLE001 — 找不到/加载失败 = 静默不提示
        mod = False
    _module = mod
    return mod or None


def _notice(update: dict, mine: str) -> dict | None:
    if not isinstance(update, dict) or update.get("status") != "available":
        return None
    latest = update.get("latest_version")
    kind = how()
    out: dict = {
        "status": "available",
        "current_version": mine,
        "latest_version": latest,
        "how": kind,
    }
    if update.get("release_notes_url"):
        out["release_notes_url"] = update["release_notes_url"]
    head = f"Tavotto 有新版本 {latest}（当前 {mine}）。"
    if kind == "desktop":
        msg = head + "请用户打开 Tavotto 桌面版更新，更新后新开一个会话。"
    elif kind == "codex_plugin":
        cmd = update.get("upgrade_command")
        out["upgrade_command"] = cmd
        msg = (
            head
            + f"在 Codex 里更新插件：{cmd}，更新后新开一个会话；其他宿主请按 Tavotto 文档更新插件。"
        )
    else:
        msg = head + "请用户更新 Tavotto 后新开一个会话。"
    out["message"] = msg + "任务照常完成，收尾时转述一次即可，不要自己执行升级。"
    return out


def network_allowed() -> bool:
    """用户没有关掉更新检查。读不到设置时按默认（开），与 `updater.settings()` 的默认一致。"""
    if (os.environ.get("TAVOTTO_NO_UPDATE_CHECK") or "").strip():
        return False
    try:
        from tavotto.engine import updater  # noqa: PLC0415

        return bool(updater.settings()["auto_check"])
    except Exception:  # noqa: BLE001 — 没有引擎 / 设置读不出
        return True


def _refresh(mod, mine: str) -> None:
    global _latest
    try:
        res = mod.check(version=mine, fetcher=mod.fetch_with_mirror)
    except Exception:  # noqa: BLE001
        return
    with _lock:
        _latest = res


def _compared_version(mod, mine: str) -> str:
    """实际拿去跟清单比的版本。

    插件启动（`codex_plugin`）时比的是**已安装插件**的版本：插件可以配独立安装的引擎，
    `mine`（引擎版本）不代表插件。旧插件 + 新引擎要提醒，新插件 + 旧引擎不能误报。
    读不到插件版本回退 `mine`；桌面版（frozen）本来就没有 plugin.json。
    """
    if how() == "codex_plugin":
        try:
            v = mod.current_version()
        except Exception:  # noqa: BLE001
            v = None
        if v:
            return v
    return mine


def _current(mine: str) -> dict | None:
    global _thread, _started_at
    mod = _load()
    if mod is None or not mine or mine == "0":
        return None
    ver = _compared_version(mod, mine)
    now = time.monotonic()
    with _lock:
        # 是否刷新交给 update_check 的 `_due`（成功 24 小时、失败 1 小时）。进程内另留一个
        # 不短于 RETRY_INTERVAL 的最小间隔：缓存写不了时 `_due` 永远为真，不能每次调用起线程。
        spaced = _started_at is None or now - _started_at >= mod.RETRY_INTERVAL
        if (
            spaced
            and not (_thread and _thread.is_alive())
            and network_allowed()
            and mod.refresh_due()
        ):
            _started_at = now
            try:
                _thread = threading.Thread(
                    target=_refresh, args=(mod, ver), name="tavotto-update-check", daemon=True
                )
                _thread.start()
            except Exception:  # noqa: BLE001
                _thread = None
        fresh = _latest
    # 缓存可能是别的进程刷新的（比内存新），也可能写不进去（比内存旧）：两份里取版本更新的那份
    res = mod.check(version=ver, allow_network=False)
    if fresh is not None and (
        res.get("status") == "unknown"
        or mod.is_newer(fresh.get("latest_version"), res.get("latest_version")) is True
    ):
        res = fresh
    return _notice(res, ver)


def notice(mine: str) -> dict | None:
    """给 `tavotto_health`：有新版就返回 `update` 字段，否则 None。永不抛、永不等网络。"""
    try:
        return _current(mine)
    except Exception:  # noqa: BLE001
        return None


def notice_once(mine: str) -> dict | None:
    """给 `tavotto_export`：同一个 MCP 进程里最多返回一次。"""
    global _export_delivered
    try:
        if _export_delivered:
            return None
        n = _current(mine)
        if n is not None:
            _export_delivered = True
        return n
    except Exception:  # noqa: BLE001
        return None
