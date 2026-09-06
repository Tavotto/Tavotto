"""可信工作区根的选择与 MCP ``roots/list`` 兼容层。

工具参数里的路径来自模型，不能反过来成为授权边界。这里把「宿主允许 Tavotto
看哪里」收成一个进程内权威：显式配置优先，其次是宿主在旧版 MCP 握手里声明并
通过 ``roots/list`` 返回的目录；没有 Roots 但支持 elicitation 的 host 可以把一个
规范路径展示给用户、得到连接内确认，再往后才是兼容环境变量与安全的 cwd。

MCP Roots 从协议版本 2026-07-28 起已弃用；Tavotto 仍在 2025-era stdio 连接上
支持它，目的是兼容会提供 workspace roots 的 Codex host，而不是把它当长期唯一
配置面。新 host 可用用户确认、环境变量或服务器配置传根。

纯标准库。
"""

from __future__ import annotations

import ntpath
import os
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from urllib.request import url2pathname

ROOTS_ENV = "TAVOTTO_MCP_ROOTS"
WORKSPACE_ENVS = (
    "TAVOTTO_MCP_WORKSPACE",
    "CODEX_WORKSPACE_ROOT",
    "CODEX_PROJECT_ROOT",
    "CODEX_WORKSPACE_DIR",
)


# --------------------------- 工作区授权失败的分档 ---------------------------
#: 授权失败**不是一件事**（issue #173）。真实现场：宿主在 initialize 里声明了
#: ``elicitation``，却从没把确认框送到用户面前；server 把这次「没回应」报成了
#: 「用户拒绝」，于是用户被要求「再点一次」——而根本没有框可以点。
#:
#: **「宿主没回应」是独立一档**，不许并进相邻取值：它和「用户拒绝」的下一步
#: 正好相反（一个要去查宿主接线，一个只要再问一次）。下面这张表是「授权失败
#: 长什么样」的唯一出处：一个稳定 code ↔ 一个处置 ↔ 一句说得出「下一步做
#: 什么」的措辞。调用方只准从表里取，不许现编字符串，也不许把 code 当文案
#: 直接念给用户。

#: 处置（谁该动手）是闭集。
ASK_USER_AGAIN = "ask_user_again"
FIX_HOST_WIRING = "fix_host_wiring"
NARROW_THE_PATH = "narrow_the_path"
CONFIGURE_ROOTS = "configure_roots"
SEND_ABSOLUTE_PATH = "send_absolute_path"
DISPOSITIONS = (
    ASK_USER_AGAIN,
    FIX_HOST_WIRING,
    NARROW_THE_PATH,
    CONFIGURE_ROOTS,
    SEND_ABSOLUTE_PATH,
)

CODE_CONFIRMATION_DECLINED = "workspace_confirmation_declined"
CODE_CONFIRMATION_CANCELLED = "workspace_confirmation_cancelled"
CODE_CONFIRMATION_NO_RESPONSE = "workspace_confirmation_no_response"
CODE_CONFIRMATION_ERROR = "workspace_confirmation_error"
CODE_CONFIRMATION_STALE = "workspace_confirmation_stale"
CODE_CONFIRMATION_REQUIRED = "workspace_confirmation_required"
CODE_ROOTS_NO_RESPONSE = "workspace_roots_no_response"
CODE_ROOTS_ERROR = "workspace_roots_error"
CODE_NO_WORKSPACE_ROOT = "no_workspace_root"
CODE_PATH_OUT_OF_SCOPE = "path_out_of_scope"
CODE_AMBIGUOUS_ROOT = "ambiguous_workspace_root"


@dataclass(frozen=True)
class WorkspaceFailure:
    """一档授权失败：稳定 code + 处置 + 人话的下一步。"""

    code: str
    disposition: str
    summary: str
    next_step: str

    def payload(self) -> dict:
        return {
            "code": self.code,
            "disposition": self.disposition,
            "next_step": self.next_step,
        }


def _failure(code: str, disposition: str, summary: str, next_step: str) -> WorkspaceFailure:
    assert disposition in DISPOSITIONS, disposition
    return WorkspaceFailure(code, disposition, summary, next_step)


WORKSPACE_FAILURES: dict[str, WorkspaceFailure] = {
    entry.code: entry
    for entry in (
        _failure(
            CODE_CONFIRMATION_DECLINED,
            ASK_USER_AGAIN,
            "用户在工作区确认框里拒绝了这个目录。",
            "换一个目录再问一次，或请用户把 "
            f"{ROOTS_ENV} 设成工作目录后重启 Codex；不要自动循环重试。",
        ),
        _failure(
            CODE_CONFIRMATION_CANCELLED,
            ASK_USER_AGAIN,
            "工作区确认框被关掉了，没有作出选择。",
            "在可交互会话里请用户重新发起这次打开并批准；非交互会话"
            f"（codex exec）拿不到确认，请改用 {ROOTS_ENV}。不要自动循环重试。",
        ),
        _failure(
            CODE_CONFIRMATION_NO_RESPONSE,
            FIX_HOST_WIRING,
            "宿主声明支持工作区确认，却没有把确认框送到用户面前（超时或连接中断）。",
            "这不是用户拒绝——再让用户点一次也不会出现提示。请检查宿主的 "
            "elicitation 接线（版本、权限、是不是非交互模式），或直接把 "
            f"{ROOTS_ENV} 设成工作目录后重启 Codex。",
        ),
        _failure(
            CODE_CONFIRMATION_ERROR,
            FIX_HOST_WIRING,
            "宿主收下了工作区确认请求，却回了一个错误。",
            "这是宿主侧的问题，不是用户拒绝。看宿主日志确认 elicitation 是否可用，"
            f"或改用 {ROOTS_ENV} 显式配置根目录。",
        ),
        _failure(
            CODE_CONFIRMATION_STALE,
            ASK_USER_AGAIN,
            "用户批准的目录在授权落地前变了（被删除、被换成别的 symlink 目标，或已不是目录）。",
            "重新核对项目路径后再打开一次，让用户对新的规范路径重新批准。",
        ),
        _failure(
            CODE_CONFIRMATION_REQUIRED,
            SEND_ABSOLUTE_PATH,
            "宿主支持工作区确认，但还没有可以展示给用户的目录。",
            "用一个绝对、已存在的项目路径重新调用 tavotto_open_figure，"
            "Tavotto 会把这条完整路径显示出来请用户批准；不要猜测授权结果。",
        ),
        _failure(
            CODE_ROOTS_NO_RESPONSE,
            FIX_HOST_WIRING,
            "宿主声明支持 MCP roots，却没有回应工作区目录查询（超时或连接中断）。",
            "这不是用户拒绝，也不是没配置——请检查宿主的 roots 接线，或直接把 "
            f"{ROOTS_ENV} 设成工作目录后重启 Codex。",
        ),
        _failure(
            CODE_ROOTS_ERROR,
            FIX_HOST_WIRING,
            "宿主声明支持 MCP roots，但查询工作区目录时回了一个错误。",
            f"这是宿主侧的问题。看宿主日志，或改用 {ROOTS_ENV} 显式配置根目录。",
        ),
        _failure(
            CODE_NO_WORKSPACE_ROOT,
            CONFIGURE_ROOTS,
            "宿主既没有给出工作区目录，也不支持工作区确认。",
            f"把 {ROOTS_ENV} 设成你的项目目录（多个用 {os.pathsep} 分隔）后重启 Codex 再试。",
        ),
        _failure(
            CODE_PATH_OUT_OF_SCOPE,
            NARROW_THE_PATH,
            "这条路径不在当前允许的工作区范围内。",
            "改用允许的根之内的路径；确实要放开别的目录，就把 "
            f"{ROOTS_ENV} 设成它们（{os.pathsep} 分隔）后重启 Codex。",
        ),
        _failure(
            CODE_AMBIGUOUS_ROOT,
            SEND_ABSOLUTE_PATH,
            "同时有多个可信工作区根，相对路径没有唯一解释。",
            "改传绝对路径。",
        ),
    )
}

#: 连接内确认状态 → 失败档。``accepted`` / ``unsupported`` / ``unavailable``
#: 不在表里：前者没失败，后两者归 ``no_workspace_root``（宿主根本没这条路）。
_CONFIRMATION_FAILURE_CODES = {
    "available": CODE_CONFIRMATION_REQUIRED,
    "declined": CODE_CONFIRMATION_DECLINED,
    "cancelled": CODE_CONFIRMATION_CANCELLED,
    "no_response": CODE_CONFIRMATION_NO_RESPONSE,
    "error": CODE_CONFIRMATION_ERROR,
    "stale": CODE_CONFIRMATION_STALE,
}


def _within(path: str, root: str) -> bool:
    try:
        common = os.path.commonpath([path, root])
        return os.path.normcase(common) == os.path.normcase(root)
    except ValueError:  # Windows 上跨盘符 commonpath 会抛
        return False


def _windows_absolute_realpath(path: str, resolver) -> str:
    """不用进程 cwd 解析一个 Windows 绝对路径。

    CPython 的 ``ntpath.realpath`` 会在判断路径是否绝对之前无条件读取 cwd；
    插件更新替换掉旧 cwd 后，这会让一个完全合法的绝对 workspace path 也抛
    ``FileNotFoundError``。Windows 自己的 ``_getfinalpathname`` 不需要 cwd，
    所以这里直接解析最长的现存祖先，再接回尚未创建的尾部。权限等非
    ``not found`` 错误仍然原样抛出，不能用词法路径降级绕过 symlink/junction。
    """
    normalized = ntpath.normpath(path)
    prefix = "\\\\?\\"
    unc_prefix = "\\\\?\\UNC\\"
    had_prefix = ntpath.normcase(normalized).startswith(ntpath.normcase(prefix))
    probe = normalized
    tail: list[str] = []
    while True:
        try:
            resolved = resolver(probe)
            break
        except OSError as exc:
            if not isinstance(exc, FileNotFoundError) and getattr(exc, "winerror", None) not in {
                2,
                3,
            }:
                raise
            parent, name = ntpath.split(probe)
            if not name or parent == probe:
                raise
            tail.append(name)
            probe = parent
    for name in reversed(tail):
        resolved = ntpath.join(resolved, name)
    resolved = ntpath.normpath(resolved)
    if not had_prefix:
        folded = ntpath.normcase(resolved)
        if folded.startswith(ntpath.normcase(unc_prefix)):
            resolved = "\\\\" + resolved[len(unc_prefix) :]
        elif folded.startswith(ntpath.normcase(prefix)):
            resolved = resolved[len(prefix) :]
    return resolved


def canonical_path(path: str) -> str:
    """等价于 ``realpath``，但 Windows 绝对路径不依赖一个仍存在的 cwd。"""
    raw = os.path.expanduser(str(path))
    try:
        return os.path.realpath(raw)
    except FileNotFoundError:
        if os.name != "nt" or not ntpath.isabs(raw):
            raise
        resolver = getattr(os.path, "_getfinalpathname", None)
        if not callable(resolver):
            raise
        return _windows_absolute_realpath(raw, resolver)


def is_filesystem_root(path: str) -> bool:
    """判断 POSIX 根、盘符根或 UNC share 根，不读取当前工作目录。"""
    normalized = os.path.normpath(path)
    if not os.path.isabs(normalized):
        return False
    return os.path.normcase(os.path.dirname(normalized)) == os.path.normcase(normalized)


@dataclass(frozen=True)
class RootSnapshot:
    """一次不可变的授权根选择结果。"""

    roots: tuple[str, ...]
    source: str
    generation: int
    warnings: tuple[str, ...] = ()

    def payload(self) -> dict:
        return {
            "roots": list(self.roots),
            "source": self.source,
            "generation": self.generation,
            "warnings": list(self.warnings),
        }


class RootAuthority:
    """选择可信根，并记录宿主 workspace capabilities 的真实运行时状态。"""

    def __init__(self, plugin_dir: str) -> None:
        self.plugin_dir = canonical_path(plugin_dir)
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """清掉当前 MCP 连接的状态（公开给测试，也用于重复 initialize）。"""
        with self._lock:
            self._client_protocol: str | None = None
            self._client_name: str | None = None
            self._client_version: str | None = None
            self._client_capability_keys: tuple[str, ...] = ()
            self._client_elicitation = False
            self._client_sampling = False
            self._user_binding_state = "unavailable"
            self._user_root: str | None = None
            self._user_binding_error: str | None = None
            self._protocol_supported = False
            self._protocol_list_changed = False
            self._protocol_state = "uninitialized"
            self._protocol_roots: tuple[str, ...] = ()
            self._protocol_error: str | None = None
            self._protocol_warnings: tuple[str, ...] = ()
            self._generation = 0
            self._last_identity: tuple[str, tuple[str, ...], tuple[str, ...]] | None = None

    def observe_client(
        self, protocol_version: str | None, capabilities: Any, client_info: Any
    ) -> None:
        """记录 initialize 中由宿主自己声明的 capability；不猜。"""
        caps = capabilities if isinstance(capabilities, dict) else {}
        roots_cap = caps.get("roots")
        supported = isinstance(roots_cap, dict)
        info = client_info if isinstance(client_info, dict) else {}
        with self._lock:
            self._client_protocol = str(protocol_version or "") or None
            self._client_name = str(info.get("name") or "") or None
            self._client_version = str(info.get("version") or "") or None
            self._client_capability_keys = tuple(sorted(str(key) for key in caps))
            self._client_elicitation = isinstance(caps.get("elicitation"), dict)
            self._client_sampling = isinstance(caps.get("sampling"), dict)
            self._user_binding_state = "available" if self._client_elicitation else "unsupported"
            self._user_root = None
            self._user_binding_error = None
            self._protocol_supported = supported
            self._protocol_list_changed = bool(supported and roots_cap.get("listChanged"))
            self._protocol_state = "pending" if supported else "unsupported"
            self._protocol_roots = ()
            self._protocol_error = None
            self._protocol_warnings = ()

    def mark_protocol_stale(self) -> None:
        with self._lock:
            if self._protocol_supported:
                self._protocol_state = "stale"
                self._protocol_error = None

    def protocol_request_needed(self) -> bool:
        """是否应在当前 client request 内嵌套发一次 ``roots/list``。"""
        # 明确配置是最高权威；哪怕值写坏，也应 fail-closed 而不是悄悄换来源。
        if (os.environ.get(ROOTS_ENV) or "").strip():
            return False
        with self._lock:
            return self._protocol_supported and self._protocol_state in {
                "pending",
                "stale",
                "error",
                "no_response",
            }

    def user_binding_candidate(self, target: Any) -> str | None:
        """返回可展示给用户确认的精确目录；不能安全确认就返回 ``None``。

        ``target`` 来自模型，所以这里只把它变成候选值，绝不在这个方法里授予
        权限。相对路径在没有可信根时也没有稳定语义（进程 cwd 是插件目录），
        因而必须让调用方改传绝对路径。
        """
        if not isinstance(target, str) or not target.strip():
            return None
        raw = os.path.expanduser(target.strip())
        if not os.path.isabs(raw):
            return None
        with self._lock:
            if not self._client_elicitation or self._protocol_supported:
                return None
        # 显式服务器配置是管理员边界，不允许一次交互把它扩宽。
        if (os.environ.get(ROOTS_ENV) or "").strip():
            return None
        try:
            real = canonical_path(raw)
            if not os.path.exists(real):
                return None
            candidate = real if os.path.isdir(real) else os.path.dirname(real)
            candidate = self._normalise_dir(candidate)
            if is_filesystem_root(candidate) or _within(candidate, self.plugin_dir):
                return None
        except (OSError, ValueError):
            return None
        snap = self.snapshot()
        if any(_within(real, root) for root in snap.roots):
            return None
        return candidate

    def accept_user_binding(self, candidate: str) -> bool:
        """在 host 回报用户明确同意后，绑定本连接内的一个精确目录。"""
        try:
            real = self._normalise_dir(candidate)
            if is_filesystem_root(real):
                raise ValueError("不接受文件系统根目录")
            if _within(real, self.plugin_dir):
                raise ValueError("指向插件缓存目录，不是用户工作区")
        except (OSError, ValueError) as exc:
            self.fail_user_binding(f"确认后的目录已失效：{exc}", state="stale")
            return False
        # 防止确认框显示后目录被换成另一个 symlink 目标。
        if real != candidate:
            self.fail_user_binding("确认期间目录的规范路径发生变化", state="stale")
            return False
        with self._lock:
            if not self._client_elicitation:
                return False
            self._user_root = real
            self._user_binding_state = "accepted"
            self._user_binding_error = None
        return True

    def fail_user_binding(self, message: str, *, state: str = "error") -> None:
        with self._lock:
            self._user_root = None
            self._user_binding_state = state
            self._user_binding_error = str(message)

    def accept_protocol_result(self, result: Any) -> None:
        """校验 ``roots/list`` 结果并原子替换，不接受非本地 URI/文件。"""
        if not isinstance(result, dict) or not isinstance(result.get("roots"), list):
            self.fail_protocol("roots/list 响应缺少 roots 数组")
            return
        accepted: list[str] = []
        warnings: list[str] = []
        for index, item in enumerate(result["roots"]):
            if not isinstance(item, dict) or not isinstance(item.get("uri"), str):
                warnings.append(f"root[{index}] 缺少 file:// uri，已忽略")
                continue
            try:
                path = self._path_from_file_uri(item["uri"])
                real = self._normalise_dir(path)
                if is_filesystem_root(real):
                    raise ValueError("不接受文件系统根目录")
                if _within(real, self.plugin_dir):
                    raise ValueError("指向插件缓存目录，不是用户工作区")
            except (OSError, ValueError) as exc:
                warnings.append(f"root[{index}] 无效：{exc}")
                continue
            if real not in accepted:
                accepted.append(real)
        with self._lock:
            self._protocol_roots = tuple(accepted)
            self._protocol_state = "ready"
            self._protocol_error = None
            self._protocol_warnings = tuple(warnings)

    def fail_protocol(self, message: str, *, state: str = "error") -> None:
        """``roots/list`` 失败。

        ``state`` 区分「宿主根本没回应」（``no_response``：超时、EOF、没有可等待
        的传输）与「宿主回了一个错误」（``error``）。合并这两者会让「声明了
        capability 却没接线」的宿主看起来像「用户没批准」，处置就反了。
        """
        with self._lock:
            if not self._protocol_supported:
                return
            self._protocol_roots = ()
            self._protocol_state = state
            self._protocol_error = str(message)

    def snapshot(self) -> RootSnapshot:
        with self._lock:
            roots, source, warnings = self._select_unlocked()
            identity = (source, roots, warnings)
            if identity != self._last_identity:
                self._generation += 1
                self._last_identity = identity
            return RootSnapshot(roots, source, self._generation, warnings)

    def failure(self) -> WorkspaceFailure:
        """一个可信根都没有时，这次失败属于哪一档（issue #173）。

        **各档的处置互相相反**，所以这里按「谁该动手」分类，绝不合并：宿主没
        回应时再让用户点一次是白点，用户拒绝时去查宿主接线是白查。判据的主语
        是**这条连接此刻的授权来源**，不是模型传了什么路径。
        """
        source = self.snapshot().source
        if source == "mcp_roots_no_response":
            return WORKSPACE_FAILURES[CODE_ROOTS_NO_RESPONSE]
        if source == "mcp_roots_error":
            return WORKSPACE_FAILURES[CODE_ROOTS_ERROR]
        if source in {"explicit_env", "mcp_roots", "mcp_roots_pending"}:
            # 显式配置与宿主明确给出的（空）清单都是「已经回答过了，只是没有
            # 可用目录」——那是配置问题，不是没弹框。
            return WORKSPACE_FAILURES[CODE_NO_WORKSPACE_ROOT]
        with self._lock:
            state = self._user_binding_state
        return WORKSPACE_FAILURES[_CONFIRMATION_FAILURE_CODES.get(state) or CODE_NO_WORKSPACE_ROOT]

    def diagnostics(self) -> dict:
        snap = self.snapshot()
        with self._lock:
            out = snap.payload()
            out["client"] = {
                "name": self._client_name,
                "version": self._client_version,
                "protocol_version": self._client_protocol,
                "capabilities": {
                    "advertised": list(self._client_capability_keys),
                    "roots": self._protocol_supported,
                    "elicitation": self._client_elicitation,
                    "sampling": self._client_sampling,
                },
            }
            out["mcp_roots"] = {
                "advertised": self._protocol_supported,
                "list_changed": self._protocol_list_changed,
                "state": self._protocol_state,
                "error": self._protocol_error,
                "compatibility_only": True,
                "deprecated_since": "2026-07-28",
            }
            out["workspace_confirmation"] = {
                "advertised": self._client_elicitation,
                "state": self._user_binding_state,
                "root": self._user_root,
                "error": self._user_binding_error,
                "lifetime": "mcp_connection",
            }
        # 锁外调用：failure() 自己要拿锁。有根时不是失败，如实报 None。
        out["authorization"] = None if snap.roots else self.failure().payload()
        return out

    def _select_unlocked(self) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
        raw = (os.environ.get(ROOTS_ENV) or "").strip()
        if raw:
            roots, warnings = self._paths_from_config(raw, reject_plugin=True, reject_fs_root=True)
            return roots, "explicit_env", warnings

        if self._protocol_supported:
            if self._protocol_state == "ready":
                return self._protocol_roots, "mcp_roots", self._protocol_warnings
            if self._protocol_state in {"pending", "stale"}:
                return (), "mcp_roots_pending", ()
            warnings = tuple(x for x in (self._protocol_error,) if x)
            if self._protocol_state == "no_response":
                return (), "mcp_roots_no_response", warnings
            return (), "mcp_roots_error", warnings

        if self._user_root is not None:
            return (self._user_root,), "user_elicitation", ()

        for name in WORKSPACE_ENVS:
            hint = (os.environ.get(name) or "").strip()
            if hint:
                roots, warnings = self._paths_from_config(
                    hint, reject_plugin=True, reject_fs_root=True
                )
                return roots, f"workspace_env:{name}", warnings

        try:
            cwd = self._normalise_dir(os.getcwd())
        except OSError as exc:
            return (), "none", (f"cwd 不可用：{exc}",)
        if _within(cwd, self.plugin_dir):
            return (), "none", ("进程 cwd 是插件目录，不作为用户工作区",)
        if is_filesystem_root(cwd):
            return (), "none", ("进程 cwd 是文件系统根目录，不作为工作区",)
        return (cwd,), "cwd", ()

    def _paths_from_config(
        self, raw: str, *, reject_plugin: bool, reject_fs_root: bool
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        accepted: list[str] = []
        warnings: list[str] = []
        for item in (part.strip() for part in raw.split(os.pathsep)):
            if not item:
                continue
            try:
                real = self._normalise_dir(item)
                if reject_fs_root and is_filesystem_root(real):
                    raise ValueError("不接受文件系统根目录")
                if reject_plugin and _within(real, self.plugin_dir):
                    raise ValueError("指向插件目录")
            except (OSError, ValueError) as exc:
                warnings.append(f"{item}: {exc}")
                continue
            if real not in accepted:
                accepted.append(real)
        return tuple(accepted), tuple(warnings)

    @staticmethod
    def _normalise_dir(path: str) -> str:
        real = canonical_path(path)
        if not os.path.isdir(real):
            raise ValueError("目录不存在或不是目录")
        return real

    @staticmethod
    def _path_from_file_uri(uri: str) -> str:
        parsed = urlsplit(uri)
        if parsed.scheme.lower() != "file":
            raise ValueError("只接受 file:// URI")
        if parsed.query or parsed.fragment:
            raise ValueError("file URI 不能带 query/fragment")
        if parsed.netloc not in ("", "localhost"):
            raise ValueError("不接受远程 file URI")
        return url2pathname(parsed.path)
