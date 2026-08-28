"""**一个** Python 环境同时被谁占着——safe worker / native 会话 / pip 安装
共用的唯一一张表（ADR 0021 §6）。

## 为什么必须只有一张表

`#177`（ADR 0019）给依赖安装做了独占：装包期间先 `shutdown_workers_using()`
收掉池里的 worker，再让 `pool.get()` 拒起新的。那把锁装在 `pool` 里，键是
解释器路径——**而 native 会话不经过池**（它由 `tavotto run` CLI 自己
`Popen` 用户的解释器）。也就是说：

> 用户握着 live Figure 的同时，另一个请求可以往**同一个解释器**装包。
> pip 会替换 / 删除已有包的文件，而那个进程还在跑。

这不是"漏了一个分支"，是**实现方式决定的机制盲区**：那把锁只看得见它自己
管着的那些进程。所以解药不是在 pool 里再加一张 `_native_busy`——那样就有了
两张表，而"两张表"这个形状本身就保证了它们迟早会不一致。

`pool._mutating` 整个搬到这里，`pool` 变成消费者；native 会话在这里登记
租约。三方问的是同一张表。

## 状态

```text
idle              没人用
safe_workers      池里有 worker 用着这个解释器（由 pool 报告，不在这里记）
native_sessions   有 tavotto run 会话在用（在这里记）
mutating          正在装包（在这里记）
```

`safe_workers` 不在本模块存表：那是 `pool._workers` 的事实，重复存一份就是
第二个权威。本模块只回答"能不能开始改这个环境"和"能不能在这个环境上起
native 会话"，前者由 pool 在进入 mutating 时自己收掉 worker。

## 两条方向相反的拒绝

| 谁先来 | 谁被拒 | 码 |
|---|---|---|
| 正在装包 | 起 native 会话 / 起 safe worker | `environment_mutating` |
| 有活跃 native 会话 | 装包 | `environment_in_use_by_native_session` |

**有 native 会话时不自动杀它**：那个进程是用户的，里面可能有跑了两小时的
计算。装依赖是一件可以等的事，杀掉用户的脚本不是。

纯标准库。
"""

from __future__ import annotations

import contextlib
import os
import threading

from . import runcodes

STATE_IDLE = "idle"
STATE_SAFE_WORKERS = "safe_workers"
STATE_NATIVE_SESSIONS = "native_sessions"
STATE_MUTATING = "mutating"

#: 环境正在被改动时新起会话的 code（可恢复，不是故障）。
#: **与 `pool.ENVIRONMENT_MUTATING` 是同一个值**——pool re-export 它。
ENVIRONMENT_MUTATING = "environment_mutating"
ENVIRONMENT_IN_USE_BY_NATIVE = runcodes.ENVIRONMENT_IN_USE_BY_NATIVE_SESSION

_lock = threading.Lock()
#: env_key -> 这次改动的 owner key（受管环境先拿锁、后建解释器，所以一次
#: 改动可能登记两个 env_key，靠 owner 归属清理）。
_mutating: dict[str, str] = {}
#: env_key -> {session_id}
_native: dict[str, set[str]] = {}


class EnvironmentBusy(RuntimeError):
    """这个环境上已经有一个改动在跑（同一环境不允许并发 pip）。

    `code` 让调用方分诊两种完全不同的忙：另一次安装（`environment_mutating`）
    还是有人在跑脚本（`environment_in_use_by_native_session`）。前者等几十秒
    就好，后者要用户自己去结束那个脚本。
    """

    def __init__(self, message: str, code: str = ENVIRONMENT_MUTATING, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


def env_key_of(python: str) -> str:
    """解释器路径 → 环境 key（与 `deprepair._env_key` 同一份归一）。"""
    return os.path.normcase(os.path.normpath(os.path.abspath(str(python))))


# --------------------------------------------------------------------------
# 查询
# --------------------------------------------------------------------------
def is_mutating(python: str) -> bool:
    with _lock:
        return bool(python) and env_key_of(python) in _mutating


def native_sessions_on(python: str) -> list[str]:
    with _lock:
        return sorted(_native.get(env_key_of(python), ()))


def state_of(python: str, *, safe_workers: int = 0) -> str:
    """这个环境现在是什么状态。`safe_workers` 由调用方（pool）报，不在这里存。"""
    key = env_key_of(python)
    with _lock:
        if key in _mutating:
            return STATE_MUTATING
        if _native.get(key):
            return STATE_NATIVE_SESSIONS
    return STATE_SAFE_WORKERS if safe_workers else STATE_IDLE


def snapshot() -> dict:
    """诊断用：每个环境上现在有什么。**只回数量与 session id，不回路径内容。**"""
    with _lock:
        return {
            "mutating": sorted(_mutating),
            "native": {k: sorted(v) for k, v in _native.items() if v},
        }


# --------------------------------------------------------------------------
# native 租约
# --------------------------------------------------------------------------
def acquire_native(python: str, session_id: str) -> None:
    """给一条 native 会话登记租约。环境正在装包时**拒绝**。

    在**起用户 Python 之前**调用：半装完的 site-packages 上 import 到一半是
    最难解释的一档失败（有时成功、有时缺一个子模块），而 native 里那次失败
    发生在**用户自己的脚本**上，他会去怀疑自己的代码。
    """
    key = env_key_of(python)
    with _lock:
        if key in _mutating:
            raise EnvironmentBusy(
                "这个 Python 环境正在安装依赖，请稍候再试。", code=ENVIRONMENT_MUTATING
            )
        _native.setdefault(key, set()).add(session_id)


def release_native(python: str, session_id: str) -> None:
    """会话结束（正常 / 崩溃 / CLI 挂了）——**必须在 finally 里调**。

    漏掉一次的后果不是"这条会话还占着"，是**这个环境从此再也装不了依赖**，
    而用户完全看不出为什么（他明明已经关掉了那个终端）。
    """
    key = env_key_of(python)
    with _lock:
        alive = _native.get(key)
        if not alive:
            return
        alive.discard(session_id)
        if not alive:
            _native.pop(key, None)


@contextlib.contextmanager
def native_lease(python: str, session_id: str):
    """`acquire_native` + 保证释放。拿不到时**不进入 `try`**——那样 finally
    会去释放一条从没登记过的租约，把别人的那条也一起清掉。"""
    acquire_native(python, session_id)
    try:
        yield
    finally:
        release_native(python, session_id)


# --------------------------------------------------------------------------
# 改动（pip 安装）独占
# --------------------------------------------------------------------------
@contextlib.contextmanager
def mutating(key: str, python: str = ""):
    """装依赖期间独占一个环境。**行为与 `#177` 的 `pool.mutating_environment`
    逐条相同**，只多了一条：有活跃 native 会话时拒绝。

    `key` 由调用方给（受管环境还没建出来时它还没有解释器路径）。解释器路径
    已知时**两个 key 都登记**，否则"建完 venv 再装包"那段窗口里，另一个请求
    可以按解释器路径拿到锁。
    """
    keys = {k for k in (key, env_key_of(python) if python else "") if k}
    with _lock:
        busy = [k for k in keys if k in _mutating]
        if busy:
            raise EnvironmentBusy(
                f"这个环境上已经有一个安装在进行中: {busy[0]}", code=ENVIRONMENT_MUTATING
            )
        held = [k for k in keys if _native.get(k)]
        if held:
            raise EnvironmentBusy(
                runcodes.message_for(ENVIRONMENT_IN_USE_BY_NATIVE),
                code=ENVIRONMENT_IN_USE_BY_NATIVE,
                native_sessions=sorted(_native[held[0]]),
            )
        for k in keys:
            _mutating[k] = key
    try:
        yield
    finally:
        with _lock:
            # **按归属清，不是按进入时那几个 key 清**：受管环境是先拿锁、
            # 后建出解释器的，那条路径上 `note_mutating_python()` 会再登记
            # 一个 key。只清进入时那几个的话，解释器那条会永远留在表里，
            # 之后对这个环境的任何操作都被判成"正在安装"。
            for k in [k for k, owner in _mutating.items() if owner == key]:
                _mutating.pop(k, None)


def note_mutating_python(key: str, python: str) -> None:
    """受管环境**建出来之后**把解释器路径也登记进同一次改动。"""
    if not python:
        return
    with _lock:
        if key in _mutating:
            _mutating[env_key_of(python)] = key


def reset_for_tests() -> None:
    """只给用例用：把两张表清空。"""
    with _lock:
        _mutating.clear()
        _native.clear()
