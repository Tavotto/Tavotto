#!/usr/bin/env python3
"""插件自己的「有没有新版本」检查：读远程清单 → 比版本 → 把提醒挂进结果里。

    python3 scripts/update_check.py            # 显式检查（人类可读）
    python3 scripts/update_check.py --json     # 显式检查（机器可读）
    python3 scripts/update_check.py --json --force   # 忽略 24 小时缓存

`handoff.py` 每次交接完会顺手调一次 `check()`，把结果放进那行 JSON 的
`update` 字段，并在有新版时往 **stderr** 写一句人话。

## 四条不许破的底线

1. **绝不阻塞出图。** 网络请求 1.5 秒超时；任何异常（DNS、代理、证书、清单
   是垃圾）都吞掉并回 `status: "unknown"`。用户是来画图的，不是来等我们
   问版本号的。
2. **绝不污染 stdout。** 调用方读的是 stdout 的最后一行 JSON，往里写一句
   「有新版本」就等于把整条链路弄坏。提醒只走 JSON 字段与 stderr。
3. **绝不自动下载或执行任何东西。** 第一阶段只提醒 + 给链接/命令。
4. **插件版本 ≠ Tavotto 版本。** 当前版本从 `.codex-plugin/plugin.json` 读
   （代码里不写第二份版本号）；`min_tavotto_version` 比的是**这次真正为你
   干活的那个 Tavotto** 报回来的版本，两者互不代表。

## 缓存

落在 Tavotto 的用户配置目录（`handoff.config_dir()`，同一份规则不抄第三遍），
文件名 `codex-plugin-update.json`：

    {"schema": 1, "checked_at": <上次成功>, "attempted_at": <上次尝试>,
     "url": "...", "manifest": {...}}

成功 24 小时内不再请求；失败 1 小时内不再请求（离线的人不该每次画图都白等
1.5 秒，但也不该为一次超时等满一天）。缓存读写失败一律无视——它是加速器，
不是状态机。

## 环境变量

* `TAVOTTO_UPDATE_URL` —— 自定义清单地址（自建分发、内网镜像、测试）
* `TAVOTTO_DISABLE_UPDATE_CHECK=1` —— 完全关掉（一个包都不发）

纯标准库，Python 3.8+。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

#: 远程清单的 schema。对不上就当没有这份清单——宁可不提醒，也不能按别的
#: 一代约定去解读字段，那样提示出来的东西可能根本是错的。
SCHEMA = 1
#: 默认清单地址。与 Tauri updater 的 `latest.json` 同一条惯例
#: （`releases/latest/download/<名字>`），发布时作为 Release 资产上传。
DEFAULT_URL = "https://github.com/Tavotto/Tavotto/releases/latest/download/codex-plugin.json"
URL_ENV = "TAVOTTO_UPDATE_URL"
DISABLE_ENV = "TAVOTTO_DISABLE_UPDATE_CHECK"
CACHE_NAME = "codex-plugin-update.json"
#: 成功之后多久再问一次
INTERVAL = 24 * 3600
#: 失败之后多久再问一次（比成功短得多：离线只是暂时的）
RETRY_INTERVAL = 3600
#: 网络超时。**这是硬上限**，用户在等着看图
TIMEOUT = 1.5
#: 这个插件的升级方式（比一个 zip 链接有用：它就是用户要敲的那一行）
UPGRADE_COMMAND = "codex plugin marketplace upgrade tavotto"

_HERE = os.path.dirname(os.path.abspath(__file__))


# ------------------------------ 版本号 -----------------------------------
def plugin_manifest_path() -> str:
    """`.codex-plugin/plugin.json`：插件版本的**唯一**出处。

    scripts/ → tavotto-figure/ → skills/ → codex-plugin/ → .codex-plugin/
    """
    return os.path.normpath(os.path.join(_HERE, "..", "..", "..", ".codex-plugin", "plugin.json"))


def current_version(path: str | None = None) -> str | None:
    """读插件自己的版本。**代码里不写第二份版本号**——发版只改 plugin.json。"""
    try:
        with open(path or plugin_manifest_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) and version.strip() else None


def parse_version(text: object) -> tuple | None:
    """语义化版本 → 可比较的 key。解不出回 None（**绝不猜**）。

    `0.7.1` / `v0.7.1` / `0.7.1-rc.2` / `0.7.1+build.5` 都认。规则按 semver：
    预发布版排在同号正式版**之前**（`0.7.1-rc.1` < `0.7.1`），构建元数据不参与比较。

    为什么不能按字符串比：`0.10.0` < `0.9.0` 在字符串序里成立，
    而那正是发到两位数小版本时会踩的坑。
    """
    if not isinstance(text, str):
        return None
    raw = text.strip().lstrip("vV")
    if not raw:
        return None
    raw = raw.split("+", 1)[0]  # 构建元数据不参与比较
    core, _, pre = raw.partition("-")
    parts = core.split(".")
    if not parts or len(parts) > 4:
        return None
    numbers = []
    for part in parts:
        if not part.isdigit():
            return None
        numbers.append(int(part))
    while len(numbers) < 3:
        numbers.append(0)
    # 没有预发布后缀的排在后面（正式版 > 预发布版）
    if not pre:
        return (tuple(numbers), 1, ())
    pieces: tuple = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (tuple(numbers), 0, pieces)


def is_newer(candidate: object, baseline: object) -> bool | None:
    """candidate 比 baseline 新吗？任一解不出就回 None（= 不知道，别提醒）。"""
    a, b = parse_version(candidate), parse_version(baseline)
    if a is None or b is None:
        return None
    return a > b


# -------------------------------- 缓存 -----------------------------------
def cache_path(environ: dict | None = None) -> str:
    from handoff import config_dir  # 目录规则只有一份

    return os.path.join(config_dir(environ=environ), CACHE_NAME)


def read_cache(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {}
    return data


def write_cache(path: str, data: dict) -> None:
    """写缓存。**失败一律无视**——它是加速器，不是状态机。

    连 `ValueError` 都要接住：`TAVOTTO_CONFIG_DIR` 是用户给的，里面可能有
    空字节之类 `os.makedirs` 直接拒收的东西。为了一个缓存文件把出图打断，
    怎么算都不划算。
    """
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except (OSError, ValueError):
        pass


# ------------------------------ 远程清单 ---------------------------------
#: 一次读多少。分块读是为了能在**块与块之间**掐表——见 `fetch` 的说明。
_CHUNK = 8 * 1024


def fetch(url: str, timeout: float = TIMEOUT) -> dict | None:
    """拉清单。**任何失败都回 None**，不抛、不打日志、不拖时间。

    `timeout` 是**总墙钟**，不只是 socket 超时。这两件事差得很远：
    `urlopen(timeout=)` 管的是「单次 IO 等多久」，每成功读到一点数据就重新
    计时。一个每 1.4 秒挤出几个字节的服务器（挂了的代理、被限速的镜像、
    存心的慢速响应）能让 1.5 秒的「硬上限」变成无限久——而这次检查是
    **同步跑在出图那条路上**的（`emit()` 里到点就查一次），于是一个跟出图
    毫无关系的更新端点把用户的图卡在那儿。所以逐块读、每块之间掐一次表，
    超了就当没查到。
    """
    deadline = time.monotonic() + max(0.1, float(timeout))
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            chunks, total = [], 0
            while total < 64 * 1024:  # 清单是几百字节，封个顶
                if time.monotonic() >= deadline:
                    return None
                block = resp.read(min(_CHUNK, 64 * 1024 - total))
                if not block:
                    break
                chunks.append(block)
                total += len(block)
            raw = b"".join(chunks)
        data = json.loads(raw.decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    if not isinstance(data.get("latest_version"), str):
        return None
    return data


# ------------------------------- 主判断 ----------------------------------
def check(
    *,
    force: bool = False,
    environ: dict | None = None,
    tavotto_version: str | None = None,
    now: float | None = None,
    fetcher=fetch,
    version: str | None = None,
) -> dict:
    """回一份 `update` 字段。**永远返回 dict，永远不抛。**

    `status`：
      disabled   用户关掉了检查（调用方据此不往结果里塞任何东西）
      current    已经是最新
      available  有新版本
      unknown    问不到 / 版本号解不出 —— 什么都别说，也别报错
    """
    env = os.environ if environ is None else environ
    now = time.time() if now is None else now
    mine = current_version() if version is None else version
    base = {"status": "unknown", "current_version": mine, "latest_version": None}

    if (env.get(DISABLE_ENV) or "").strip() in ("1", "true", "yes", "on"):
        return {**base, "status": "disabled"}

    url = (env.get(URL_ENV) or "").strip() or DEFAULT_URL
    try:
        path = cache_path(env)
        cache = read_cache(path)
    except (OSError, ValueError, ImportError):
        path, cache = "", {}  # 没缓存也能工作，只是每次都问
    manifest = cache.get("manifest") if cache.get("url") == url else None

    due = force or _due(cache, url, now)
    if due:
        fresh = fetcher(url, TIMEOUT)
        entry = {
            "schema": SCHEMA,
            "url": url,
            "attempted_at": now,
            "checked_at": cache.get("checked_at") if cache.get("url") == url else None,
            "manifest": manifest,
        }
        if fresh is not None:
            entry["checked_at"] = now
            entry["manifest"] = manifest = fresh
        if path:
            write_cache(path, entry)
        source = "network" if fresh is not None else ("cache" if manifest else "none")
    else:
        source = "cache"

    if not isinstance(manifest, dict):
        return base  # 问不到又没缓存：闭嘴
    latest = manifest.get("latest_version")
    newer = is_newer(latest, mine)
    out = {
        "status": "unknown" if newer is None else ("available" if newer else "current"),
        "current_version": mine,
        "latest_version": latest,
        "release_notes_url": manifest.get("release_notes_url"),
        "download_url": manifest.get("download_url"),
        "upgrade_command": UPGRADE_COMMAND,
        "channel": manifest.get("channel"),
        "source": source,
    }
    # min_tavotto_version 比的是**这次真正为你干活的那个 Tavotto**，不是插件自己。
    # 两个版本号各有各的升级节奏，混为一谈会提示用户去升级一个根本没问题的东西。
    required = manifest.get("min_tavotto_version")
    if tavotto_version and isinstance(required, str):
        too_old = is_newer(required, tavotto_version)
        if too_old:
            out["tavotto"] = {
                "status": "too_old",
                "current_version": tavotto_version,
                "required_version": required,
            }
    return out


def _due(cache: dict, url: str, now: float) -> bool:
    """该问了吗？成功 24 小时一次；失败 1 小时后可重试。"""
    if cache.get("url") != url:
        return True  # 换过地址：缓存不作数
    checked = cache.get("checked_at")
    if isinstance(checked, (int, float)) and now - checked < INTERVAL:
        return False
    attempted = cache.get("attempted_at")
    if isinstance(attempted, (int, float)) and now - attempted < RETRY_INTERVAL:
        return False  # 刚失败过，别每次画图都白等
    return True


def hint(update: dict) -> str | None:
    """给人看的那几句。**调用方必须写到 stderr 或 JSON 里，不许进 stdout。**"""
    if not update or update.get("status") != "available":
        return None
    lines = [
        f"Tavotto Codex 插件有新版本：{update.get('latest_version')}",
        f"当前版本：{update.get('current_version')}",
        f"更新：{update.get('upgrade_command')}",
    ]
    if update.get("release_notes_url"):
        lines.append(f"更新说明：{update['release_notes_url']}")
    return "\n".join(lines)


def tavotto_hint(update: dict) -> str | None:
    info = (update or {}).get("tavotto")
    if not info:
        return None
    return (
        f"这台机器上的 Tavotto 是 {info['current_version']}，"
        f"新版插件要求 {info['required_version']} 或更高："
        "https://github.com/Tavotto/Tavotto/releases"
    )


# ------------------------------ 显式入口 ---------------------------------
def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="检查 Tavotto Codex 插件有没有新版本（只提醒，不下载、不安装）"
    )
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    ap.add_argument("--force", action="store_true", help="忽略 24 小时缓存，立刻问一次")
    ap.add_argument(
        "--tavotto-version", default=None, help="本机 Tavotto 的版本（用于比 min_tavotto_version）"
    )
    args = ap.parse_args(argv)

    update = check(force=args.force, tavotto_version=args.tavotto_version)
    if args.json:
        print(json.dumps(update, ensure_ascii=False))
        return 0
    status = update.get("status")
    if status == "disabled":
        print(f"更新检查已关闭（{DISABLE_ENV}）")
    elif status == "available":
        print(hint(update))
    elif status == "current":
        print(f"已是最新：{update.get('current_version')}")
    else:
        print(f"查不到最新版本（当前 {update.get('current_version')}）")
    extra = tavotto_hint(update)
    if extra:
        print(extra)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
