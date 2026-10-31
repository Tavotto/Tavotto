"""按项目记住的「这个文件夹别让脚本去那里 import」决定（ADR 0154）。

场景（反馈包 TVD-ZCR4）：脚本把项目里一个 `pip install --target` 出来的私有依赖目录（`.kegg_python`）插到
`sys.path` 最前，里面的编译扩展是为**别的** Python 小版本编的；Tavotto 选中的环境里同名包本来完好，却被那个目录
盖住，导入以 ImportError 收场。换 Python 版本救不了；让那个目录对**这个项目**不生效就够了，**脚本和那个目录都不动**。

本模块是 Flask 侧（纯标准库）唯一管这件事的地方：

* 存储 —— 项目设置 `config.project_settings(<项目>)["ignored_import_paths"]`（与 `workdir` / `input_remap` 同处，
  本机、按项目）：路径列表，项目里的存相对项目根的 POSIX 路径（项目整个挪走仍成立），项目外的存绝对路径。
* 读取 —— `paths_for(项目)` 给绝对路径元组；三条 spawn 路径（safe 的 Python 池 / workerd、native bridge）都从这一个
  出处取，经 `execspec` 的 `--ignore-paths` 交给 worker / bridge（`bridgeboot.IgnoredImportPaths`）。
* 令牌 —— 失败那一刻（`pool.foreign_abi_error`）登记 (项目, 目录)，换成不透明令牌给前端；`POST /api/engine/path-ignore`
  只收令牌，路径不出后端；AI 修复的工具走同一条 `ignore_folder`。令牌绑项目、会过期。

只对名单里的目录生效；名单里永远不收项目根或它的祖先（那是用户自己的模块所在，忽略它们会让用户的同名模块失效）。
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from pathlib import Path

from . import config

SETTINGS_KEY = "ignored_import_paths"

#: 一个项目最多记多少个（防设置文件被塞满；正常用法一两个）。
MAX_PATHS = 16
#: 令牌寿命（秒）与登记表上限。失败卡一直开着，超时后再点得到 `path_ignore_token_invalid`，重跑一次即出新令牌。
TOKEN_TTL = 3600.0
MAX_RECORDS = 64

ERROR_TOKEN_INVALID = "path_ignore_token_invalid"
ERROR_REJECTED = "path_ignore_rejected"
ERROR_CODES = (ERROR_TOKEN_INVALID, ERROR_REJECTED)


class IgnoreError(Exception):
    """稳定码的失败；`code` 在 `ERROR_CODES` 里。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _key(project) -> str:
    return os.path.normcase(str(Path(project).resolve(strict=False))) if project else ""


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _stored(project) -> list[str]:
    raw = (config.project_settings(str(Path(project))) or {}).get(SETTINGS_KEY)
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, str) and p][:MAX_PATHS]


def _absolute(project, stored: str) -> str:
    return stored if os.path.isabs(stored) else str(Path(project) / stored)


def _storable(project, path: str) -> str:
    """项目里的目录存相对项目根的 POSIX 路径，项目外的存绝对路径。"""
    root = os.path.abspath(str(project))
    absolute = os.path.abspath(path)
    if _under(_norm(absolute), _norm(root)) and _norm(absolute) != _norm(root):
        return Path(os.path.relpath(absolute, root)).as_posix()
    return absolute


def paths_for(project) -> tuple[str, ...]:
    """这个项目里用户选择「跳过」的目录（绝对路径，去重，保序）。没有决定回空元组。

    项目根本身与它的祖先一律不收（即使设置文件被手改过）：那里面是用户自己的模块。"""
    if not project:
        return ()
    root = _norm(str(Path(project)))
    out: list[str] = []
    seen: set[str] = set()
    for stored in _stored(project):
        absolute = os.path.abspath(_absolute(project, stored))
        n = _norm(absolute)
        if n in seen or _under(root, n):
            continue
        seen.add(n)
        out.append(absolute)
    return tuple(out)


def state(project) -> dict:
    """项目设置页 / 诊断用的只读视图：只有项目相对（或目录名）的显示形式，不带绝对路径。"""
    root = _norm(str(Path(project)))
    shown = []
    for absolute in paths_for(project):
        n = _norm(absolute)
        shown.append(
            Path(os.path.relpath(absolute, str(project))).as_posix()
            if _under(n, root)
            else os.path.basename(absolute) or absolute
        )
    return {"paths": shown}


def ignore_folder(project, path) -> dict:
    """把 `path`（目录）记进这个项目的「跳过」名单；回 `state`。拒绝：不是字符串 / 不是绝对路径 / 项目根或它的祖先 / 满了。

    令牌兑现（`path_of_token` → 这里）与 AI 修复的工具共用这一处，**没有第二份逻辑**。调用方负责关掉这个项目的会话
    （worker 的 `--ignore-paths` 是 spawn 时交的）。"""
    if not project or not isinstance(path, str) or not path or not os.path.isabs(path):
        raise IgnoreError(ERROR_REJECTED)
    absolute = os.path.abspath(path)
    if _under(_norm(str(Path(project))), _norm(absolute)):
        raise IgnoreError(ERROR_REJECTED)
    stored = _stored(project)
    n = _norm(absolute)
    if any(_norm(_absolute(project, s)) == n for s in stored):
        return state(project)
    if len(stored) >= MAX_PATHS:
        raise IgnoreError(ERROR_REJECTED)
    config.set_project_settings(
        str(Path(project)), {SETTINGS_KEY: [*stored, _storable(project, absolute)]}
    )
    return state(project)


def forget(project, path=None) -> dict:
    """撤销：给了 `path` 只去掉它，否则整个清空（回到「没有决定」）。"""
    if path is None:
        config.set_project_settings(str(Path(project)), {SETTINGS_KEY: None})
        return state(project)
    n = _norm(os.path.abspath(path))
    kept = [s for s in _stored(project) if _norm(_absolute(project, s)) != n]
    config.set_project_settings(str(Path(project)), {SETTINGS_KEY: kept or None})
    return state(project)


# --------------------------------------------------------------------------- 令牌


class _Record:
    __slots__ = ("project", "path", "expires")

    def __init__(self, project: str, path: str, expires: float):
        self.project = project
        self.path = path
        self.expires = expires


_RECORDS: dict[str, _Record] = {}
_LOCK = threading.Lock()


def _purge(now: float) -> None:
    for token in [t for t, r in _RECORDS.items() if r.expires <= now]:
        del _RECORDS[token]
    while len(_RECORDS) > MAX_RECORDS:
        del _RECORDS[next(iter(_RECORDS))]


def issue(project, path, *, now: float | None = None) -> str | None:
    """失败那一刻登记 (项目, 目录)：回不透明令牌；缺项目 / 路径回 `None`（不出按钮）。同一 (项目, 目录) 换新令牌。"""
    if not project or not isinstance(path, str) or not path or not os.path.isabs(path):
        return None
    t = time.monotonic() if now is None else now
    project_key = _key(project)
    target = _norm(path)
    token = secrets.token_urlsafe(24)
    with _LOCK:
        for old in [
            k for k, r in _RECORDS.items() if r.project == project_key and _norm(r.path) == target
        ]:
            del _RECORDS[old]
        _RECORDS[token] = _Record(project_key, os.path.abspath(path), t + TOKEN_TTL)
        _purge(t)
    return token


def path_of_token(token, project, *, now: float | None = None) -> str:
    """令牌对应的目录（绝对路径，只在后端用）：核对存在、没过期、绑的是这个项目，否则 `path_ignore_token_invalid`。"""
    t = time.monotonic() if now is None else now
    with _LOCK:
        record = _RECORDS.get(token) if isinstance(token, str) else None
        if record is None or record.expires <= t or record.project != _key(project):
            raise IgnoreError(ERROR_TOKEN_INVALID)
        return record.path


def consume_token(token) -> None:
    """令牌一次性：兑现成功之后作废。"""
    with _LOCK:
        _RECORDS.pop(token, None)


def reset() -> None:
    """清空令牌（测试之间）。"""
    with _LOCK:
        _RECORDS.clear()
