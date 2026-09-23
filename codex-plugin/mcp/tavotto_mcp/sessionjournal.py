"""MCP 编辑会话的描述落盘——server 进程换了，会话能按 `session_id` 重建（ADR 0078）。

为什么需要：会话（项目、stem、规范、已提交的 patches、规范化合同）以前只活在 server
进程的内存里。Codex 一个任务一个长连接进程，这个前提一直成立；WorkBuddy 5.6.2 实测
在一轮对话结束后回收 Agent CLI，它启动的 stdio server 跟着退出，画布下一次反向
`tools/call` 落到**新进程**，`session_id` 在那里不存在（`unknown_session`）。Codex 改
插件配置时也会重启 server（`docs/acceptance/codex-desktop-canvas.md` 末节）。

这里只做「存 / 取 / 删 / 清理」四件事，**不做任何授权判断**：记录里的项目路径只是
候选，恢复时由 `bridge._restore_session()` 用**当前连接**的 `RootAuthority` 重新校验，
脚本 / 入口从项目注册表重新读——落盘的记录不能自证权限，也不能指定要执行的脚本。

纪律：

* 只写 `engine/config.data_dir()` 下的 `mcp-sessions/`（调用方传入目录），目录 0700、
  文件 0600，先写临时文件再 `os.replace`，半截文件不会被读到；
* `session_id` 先过正则再拼路径：`s-` + 12 位小写十六进制，别的一律当作不存在；
* 读到的记录形状不对（版本不认识、字段类型不对、id 与文件名不符）一律当作不存在；
* 过期（`TTL_SECONDS`）与超额（`MAX_RECORDS`，按保存时间淘汰最旧的）在每次保存时清理；
* 纯标准库：插件 import 不到的东西这里一个都不用。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

DIRNAME = "mcp-sessions"
VERSION = 1
#: 七天：够一次投稿修改往返；再旧的画布卡片本来也不该悄悄复活
TTL_SECONDS = 7 * 24 * 3600
MAX_RECORDS = 64
SESSION_ID_RE = re.compile(r"^s-[0-9a-f]{12}$")

#: 记录里必须有、且类型必须对的字段（其余字段可缺省）
_REQUIRED = {
    "v": int,
    "id": str,
    "project": str,
    "stem": str,
    "profile": dict,
    "patches": list,
    "saved_at": (int, float),
}


def valid_id(session_id: object) -> bool:
    return isinstance(session_id, str) and SESSION_ID_RE.fullmatch(session_id) is not None


def _path(base: Path, session_id: str) -> Path:
    return base / f"{session_id}.json"


def save(base: Path, record: dict, *, now: float | None = None) -> Path:
    """原子写入一条记录；顺手清理过期与超额。失败抛 `OSError`（调用方决定怎么说）。"""
    session_id = record.get("id")
    if not valid_id(session_id):
        raise ValueError(f"非法 session_id: {session_id!r}")
    stamp = time.time() if now is None else now
    body = {**record, "v": VERSION, "saved_at": stamp}
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=f".{session_id}.", suffix=".tmp", dir=base)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(body, fh, ensure_ascii=False, separators=(",", ":"))
        os.chmod(tmp, 0o600)
        os.replace(tmp, _path(base, session_id))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    prune(base, now=stamp)
    return _path(base, session_id)


def load(base: Path, session_id: str, *, now: float | None = None) -> dict | None:
    """取一条记录；不存在 / 过期 / 形状不对 = None（绝不抛）。"""
    if not valid_id(session_id):
        return None
    try:
        raw = _path(base, session_id).read_text(encoding="utf-8")
        record = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    for key, kind in _REQUIRED.items():
        if not isinstance(record.get(key), kind) or isinstance(record.get(key), bool):
            return None
    if record["v"] != VERSION or record["id"] != session_id:
        return None
    stamp = time.time() if now is None else now
    if stamp - float(record["saved_at"]) > TTL_SECONDS:
        return None
    for key in ("contract", "normalized"):
        if record.get(key) is not None and not isinstance(record[key], dict):
            return None
    return record


def delete(base: Path, session_id: str) -> bool:
    if not valid_id(session_id):
        return False
    try:
        _path(base, session_id).unlink()
        return True
    except OSError:
        return False


def prune(base: Path, *, now: float | None = None) -> list[str]:
    """删过期的与超额的（按保存时间，最旧的先走）。返回删掉的 id。"""
    stamp = time.time() if now is None else now
    try:
        entries = [p for p in base.iterdir() if p.suffix == ".json" and valid_id(p.stem)]
    except OSError:
        return []
    aged: list[tuple[float, Path]] = []
    removed: list[str] = []
    for p in entries:
        try:
            saved = float(json.loads(p.read_text(encoding="utf-8")).get("saved_at", 0))
        except (OSError, ValueError, AttributeError, TypeError):
            saved = 0.0
        if stamp - saved > TTL_SECONDS:
            try:
                p.unlink()
                removed.append(p.stem)
            except OSError:
                pass
        else:
            aged.append((saved, p))
    aged.sort(key=lambda item: item[0])
    while len(aged) > MAX_RECORDS:
        _, p = aged.pop(0)
        try:
            p.unlink()
            removed.append(p.stem)
        except OSError:
            pass
    return removed


__all__ = [
    "DIRNAME",
    "MAX_RECORDS",
    "SESSION_ID_RE",
    "TTL_SECONDS",
    "VERSION",
    "delete",
    "load",
    "prune",
    "save",
    "valid_id",
]
