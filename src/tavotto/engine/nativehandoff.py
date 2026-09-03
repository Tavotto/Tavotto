"""CLI → 桌面的**一次性 native 交接凭据**（ADR 0021 §4）。

    <data_dir>/session/native/<不透明 ID>.json      目录 0700，文件 0600

## 为什么不走 argv

`tavotto run` 要告诉桌面四件事：连哪个端口、拿什么 token 认证、这条 invocation
长什么样、属于哪个项目。前三件里有凭据，而 **argv 在同一台机器上对别的用户
可见**（`ps` / `wmic process`）。所以 argv 只带一个**不透明 ID**：

    Tavotto --open <project> --native-session <ID>

其余全部在这份只有属主读得到的文件里。这与 ADR 0008 的本机会话凭据同一个
安全论证：**能读到这个文件的进程本来就能读用户的任何文件；网页读不到。**

## 为什么是一次性的

descriptor 是"允许连上这条 relay 一次"的凭据。留着它 = 会话结束之后那枚
token 还躺在磁盘上。所以 attach / cancel 之后原文件立刻被一份**墓碑**替换：
墓碑里没有 token、没有 metadata、没有端口，只留 `state` 与 `expires_at`。

留墓碑而不是直接删，是为了让第二次尝试拿到**准确的**码
（`native_handoff_consumed` / `native_attach_cancelled`）而不是笼统的
"无效"——那两种情况用户的下一步动作完全不同（一个是"已经在另一个窗口开了"，
一个是"你自己取消的"）。墓碑到期由 `prune_stale()` 清掉。

## Trusted descriptor API（TOCTOU）

网页请求**只能**提交 `native_id`。后端在**固定目录**里解析，`realpath` 之后
仍必须落在那个目录下；metadata 与 token **来自文件，不来自请求体**。

> 界面确认的是哪条 invocation，执行端就只能执行那条。

纯标准库。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import time

from . import config, runcodes
from .runcodes import RunError

SCHEMA = 1

#: ID 的**严格**格式。松一点（比如允许 `-`）就等于把路径穿越的判断推给
#: 后面的 realpath 检查——那条检查当然也在（`_path_for` 里），但两道都要有：
#: 格式判据挡的是"这个串根本不该被当成 ID"，realpath 挡的是"它指到了别处"。
_ID_RE = re.compile(r"^[0-9a-f]{32}$")

#: pending descriptor 的有效期。够用户读完确认文案并想一想，短到不至于让
#: 一条昨天的请求今天还能被 attach。
TTL_SECONDS = 600.0

STATE_PENDING = "pending"
STATE_CONSUMED = "consumed"
STATE_CANCELLED = "cancelled"


def native_dir() -> str:
    return os.path.join(str(config.data_dir()), "session", "native")


def _ensure_dir() -> str:
    d = native_dir()
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, stat.S_IRWXU)  # 0700；Windows 没有等价位，靠用户 profile 的 ACL
    except OSError:
        pass
    return d


def new_id() -> str:
    return secrets.token_hex(16)


def _path_for(native_id: str) -> str:
    """ID → 文件路径。格式不合法 / realpath 逃出固定目录 → 当场抛。"""
    if not isinstance(native_id, str) or not _ID_RE.match(native_id):
        raise RunError(runcodes.NATIVE_HANDOFF_INVALID)
    root = native_dir()
    path = os.path.join(root, f"{native_id}.json")
    # realpath 两侧都算：symlink 可以把 `<root>/<id>.json` 指到任何地方，
    # 而这个目录是**用户自己**能写的（0700 挡的是别人，不是他自己被骗着建了
    # 一个 symlink）。判据必须是"解析之后仍在这个目录下"。
    real_root = os.path.realpath(root)
    real_path = os.path.realpath(path)
    if os.path.dirname(real_path) != real_root:
        raise RunError(runcodes.NATIVE_HANDOFF_INVALID)
    return path


def _write_private(path: str, payload: dict) -> None:
    """0600 原子写。**先建再改权限有窗口**，所以用 `os.open` 一次到位。"""
    tmp = f"{path}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        with _suppress():
            os.unlink(tmp)
        raise
    os.replace(tmp, path)


def create(
    *,
    native_id: str = "",
    out_dir: str = "",
    project_root: str,
    interpreter: str,
    cwd: str,
    target_kind: str,
    target_display: str,
    arg_count: int,
    command_fingerprint: str,
    permission_key: str,
    python_version: str,
    attach_host: str,
    attach_port: int,
    attach_token: str,
    ttl: float = TTL_SECONDS,
    now: float | None = None,
) -> tuple[str, str]:
    """写一份 pending descriptor，返回 `(native_id, path)`。

    **参数表就是"允许放什么"的清单**：这里没有 `env`、没有 `argv`、没有
    `package_index`，也没有子进程那枚 token——加一个字段要先回答"它会不会
    出现在前端 / 日志 / 诊断包里"（ADR 0021 §4）。
    """
    _ensure_dir()
    prune_stale()
    native_id = native_id or new_id()
    t = time.time() if now is None else now
    payload = {
        "schema": SCHEMA,
        "native_id": native_id,
        "state": STATE_PENDING,
        "created_at": t,
        "expires_at": t + float(ttl),
        "relay": {"host": attach_host, "attach_port": int(attach_port)},
        "attach_token": attach_token,
        # 会话产物目录（预览 SVG / manifest / PNG）。**不在 metadata 里**：
        # metadata 整份会 sanitize 之后交给前端，而这是后端内部的路径。
        "out_dir": out_dir,
        "metadata": {
            "project_root": project_root,
            "interpreter": interpreter,
            "cwd": cwd,
            "target_kind": target_kind,
            "target_display": target_display,
            "arg_count": int(arg_count),
            "command_fingerprint": command_fingerprint,
            "permission_key": permission_key,
            "python_version": python_version,
        },
    }
    path = _path_for(native_id)
    _write_private(path, payload)
    return native_id, path


def _load(native_id: str) -> dict:
    path = _path_for(native_id)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise RunError(runcodes.NATIVE_HANDOFF_INVALID) from exc
    except (OSError, ValueError) as exc:
        raise RunError(runcodes.NATIVE_HANDOFF_INVALID) from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RunError(runcodes.NATIVE_HANDOFF_INVALID)
    if data.get("native_id") != native_id:
        # 文件名与内容对不上：要么被人动过，要么是一次半截写入。两种都不该
        # 继续，而"内容自证身份"是最便宜的那道判据。
        raise RunError(runcodes.NATIVE_HANDOFF_INVALID)
    return data


def _check_live(data: dict, now: float | None = None) -> dict:
    t = time.time() if now is None else now
    state = data.get("state")
    if state == STATE_CONSUMED:
        raise RunError(runcodes.NATIVE_HANDOFF_CONSUMED)
    if state == STATE_CANCELLED:
        raise RunError(runcodes.NATIVE_ATTACH_CANCELLED)
    if state != STATE_PENDING:
        raise RunError(runcodes.NATIVE_HANDOFF_INVALID)
    if t > float(data.get("expires_at") or 0):
        raise RunError(runcodes.NATIVE_HANDOFF_EXPIRED)
    return data


def peek(native_id: str, now: float | None = None) -> dict:
    """只读地取一份 live descriptor（**含 token**，只给后端内部用）。"""
    return _check_live(_load(native_id), now)


def sanitized(native_id: str, now: float | None = None) -> dict:
    """给前端的那一份：**没有 token、没有端口、没有 host**。

    端口也不给，是因为前端根本不需要知道它——它能提交的只有 `native_id`，
    连哪儿是后端的事。少给一个字段就少一处可能被拼进 URL 或日志的地方。
    """
    data = peek(native_id, now)
    meta = dict(data.get("metadata") or {})
    return {
        "native_id": data["native_id"],
        "created_at": data["created_at"],
        "expires_at": data["expires_at"],
        **meta,
    }


def _tombstone(native_id: str, state: str, now: float | None = None) -> None:
    """把原文件替换成一份**没有 token / 没有 metadata / 没有端口**的墓碑。"""
    t = time.time() if now is None else now
    path = _path_for(native_id)
    try:
        expires = float(_load(native_id).get("expires_at") or (t + TTL_SECONDS))
    except RunError:
        expires = t + TTL_SECONDS
    _write_private(
        path,
        {
            "schema": SCHEMA,
            "native_id": native_id,
            "state": state,
            "created_at": t,
            "expires_at": expires,
        },
    )


def consume(native_id: str, now: float | None = None) -> dict:
    """取用一次：返回**含 token 的**完整 descriptor，同时立刻墓碑化。

    顺序是刻意的——先读出来再墓碑化。反过来（先标记再读）在并发下会让两个
    请求都读到墓碑；而这里两个请求里只有一个能读到 pending，另一个拿到
    `native_handoff_consumed`。

    **读出来之后还有会失败的一步时别用它**：`app.py` 的确认端点走的是
    `peek()` → attach → `mark_consumed()`，因为在 attach **之前**烧掉凭据会把
    一次可恢复的失败变成不可逆的（issue #190）。这一条留给"读出来就算取用了"
    的调用方。
    """
    data = peek(native_id, now)
    mark_consumed(native_id, now)
    return data


def mark_consumed(native_id: str, now: float | None = None) -> bool:
    """attach **成功之后**把凭据换成墓碑。**不校验 live，也绝不抛。**回成没成。

    不校验：走到这一行时会话已经连上了，而 descriptor 在 attach 那几百毫秒里
    可能刚好过期——那时抛出去等于"会话正跑着，界面收到一条失败"。一次性由
    attach **之前**的 `peek()` 保证，这一步只负责把 token 从磁盘上抹掉。

    不抛是同一条理由的下半句：盘满 / 权限没了会让 `_write_private()` 失败，
    而那时 relay 已认证、会话已注册、CLI 马上要 spawn 用户的 Python。让这个
    异常冒上去 = 一次**成功的** attach 被报成 500。所以退而求其次去删文件，
    再不行就交给 `prune_stale()` 与 TTL——留在盘上的那份仍然 0600，而且它
    对应的会话已经活着，再 attach 一次会被 `native_session_conflict` 挡掉。
    调用方拿 False 去**记一条日志**，不去改响应。
    """
    try:
        _tombstone(native_id, STATE_CONSUMED, now)
        return True
    except (OSError, RunError):
        discard(native_id)  # 自己会吞 OSError：这里已经是兜底的兜底
        return False


def cancel(native_id: str, now: float | None = None) -> None:
    """用户点了取消 / CLI 自己收摊。墓碑化之后**再也 attach 不了**。"""
    try:
        _check_live(_load(native_id), now)
    except RunError as exc:
        if exc.code in (runcodes.NATIVE_ATTACH_CANCELLED, runcodes.NATIVE_HANDOFF_CONSUMED):
            return  # 幂等：已经是终态就别再抛
        raise
    _tombstone(native_id, STATE_CANCELLED, now)


def discard(native_id: str) -> None:
    """彻底删掉（CLI 退出时收尾用，不留墓碑）。失败静默——收尾不该抛。"""
    with _suppress():
        os.unlink(_path_for(native_id))


def prune_stale(now: float | None = None) -> int:
    """清掉过期的 descriptor 与墓碑；回清了几个。

    每次 `create()` 都跑一次：这个目录只有本用户在写，量很小，而"启动时
    清理 stale"这件事没有别的地方会做（CLI 崩了不会有人替它收尾）。
    """
    t = time.time() if now is None else now
    d = native_dir()
    removed = 0
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(d, name)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            expires = float(data.get("expires_at") or 0)
        except (OSError, ValueError, TypeError):
            expires = 0.0  # 读不懂的一律当过期：它对任何人都没有用了
        if t > expires:
            with _suppress():
                os.unlink(path)
                removed += 1
    return removed


class _suppress:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, OSError)
