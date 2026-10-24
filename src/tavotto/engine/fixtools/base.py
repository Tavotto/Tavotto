"""注册表的公共件：`FixTool`、会话上下文 `FixContext`、结果构造（ADR 0149 §六）。

注册表与「接法」无关：内置执行器经 `build_agent_tools` 适配成原生工具调用；CLI 经 MCP 的接法（D3）读同一张 `REGISTRY`。
工具拿到的是**注入的上下文对象**，不 import app / Flask。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..agent_runner.wires import ToolResult
from . import readscope

#: `observe` = 只读；`run` = 以与准备会话 safe 运行同等的保护运行用户代码（写入绊线 + 禁起进程 + 只在专用目录里跑 + 审计 + 有则加操作系统沙盒）。
#: 用户点「使用你的 AI 来修复」开启修复会话即视为授权 `run` 这一档；它**不是**「什么都不改」的承诺，盲区见 ADR 0149。
Tier = Literal["observe", "run", "tavotto", "listed", "user_env", "outward"]
TIERS: tuple[str, ...] = ("observe", "run", "tavotto", "listed", "user_env", "outward")

#: 修复会话（D2 创建方）默认放行的档。`tavotto` / `listed` / `user_env` / `outward` 放行 ≠ 不问：
#: 要不要问用户由各工具自己的 `ctx.ask` 把关（listed 名单内不问、user_env 一律问、outward 永远问）；没有 `ask` 一律按未获同意处理
FIX_SESSION_TIERS: frozenset[str] = frozenset(
    {"observe", "run", "tavotto", "listed", "user_env", "outward"}
)

#: 这些错误码 = 「被拦」：审计里记 approved_by=blocked
BLOCKED_CODES = frozenset(
    {
        readscope.REASON_DENIED,
        readscope.REASON_OUTSIDE,
        "tier_not_allowed",
        "env_unknown",
        "needs_approval",  # 需要用户点头而没拿到（没有界面 / 用户说不 / 超时）
        "hard_blocked",  # `run_command` 的硬拦表：问也不给
    }
)


@dataclass
class FixContext:
    session_id: str
    project_root: Path
    #: 用户主目录；测试里一律给 tmp_path 假主目录，不碰真实的
    home: Path = field(default_factory=Path.home)
    #: 当前项目所用 Python 环境的前缀（可能在主目录外）
    env_prefixes: list[Path] = field(default_factory=list)
    #: 环境 id → 解释器路径。`inspect_environment` / `try_*` 只认这张表里的 id，不接受任意解释器路径
    environments: dict[str, str] = field(default_factory=dict)
    #: 当前准备会话的报告（原始 dict）；没有会话就是 None
    report: Callable[[], dict | None] | None = None
    #: 候选环境清单 `() -> [evaluate() 结果形状的 entry]`；None = 用 `userenvs.discover(project_root)`
    environment_source: Callable[[], list[dict]] | None = None
    #: 日志来源 → 文件路径；缺省 app = `data_dir()/cache/app.log`
    log_paths: dict[str, Path] = field(default_factory=dict)
    #: 被诊断的脚本（`try_run` 复制它去跑）
    script_path: Path | None = None
    #: `try_run` 的执行器 `(python, work_dir, script_name, mode, timeout) -> dict`；None = 内置的一次性子进程
    runner: Callable[..., dict] | None = None
    #: 弹卡并阻塞等用户点（D2b 的修复会话实现；None = 没有界面，需要用户点的一律按「未获同意」处理）。
    #: 入参 card: {"kind": "approve"|"choice"|"pick"|"send", "tool": str, "summary": str, ...}
    #: 返回 {"answer": "yes"|"no"|<选项 id>|<路径>|"sent"|"cancelled"|"timeout", ...}
    ask: Callable[[dict], dict] | None = None
    #: 修复会话的取消信号（`fixsession` 传 `s.cancel`）；耗时的执行器（换 Python 的下载 / 建环境）据此中途停下。None = 不可取消
    cancel: threading.Event | None = None
    #: 「改」组的执行器，全部可注入（测试一律用假的，绝不往真实环境装包）。None = 用应用里现有的那条路径（见 `change.py`）：
    #: 装 / 卸包 `(python, [需求串]) -> {"ok": bool, "code": str, "done": [需求串]}`
    installer: Callable[[str, list[str]], dict] | None = None
    uninstaller: Callable[[str, list[str]], dict] | None = None
    #: `pip freeze` 文本 `(python) -> str | None`：成功回文本（全新空环境成功是 `""`），问不出来（退出码非 0 / 起不来）回 `None`
    freezer: Callable[[str], str | None] | None = None
    #: 另建合适版本的 Tavotto 私有环境 `({"version", "project"}) -> {"ok", "code", "message", "python"?}`；None = 缺省走 `pyversionfix`（`executors._default_python_changer`）；请求另带 `script`（项目相对，可缺）
    python_changer: Callable[[dict], dict] | None = None
    #: `convert_script` 的交接记录 `{"kind": "convert", "script": 项目相对 posix 路径, "library": 位图库}`：工具只记这一笔、不写任何文件，
    #: 会话收尾后界面把它交给「生成可编辑版本」（ADR 0143 / 0149 §七）；None = 没有交接
    handoff: dict | None = None
    #: 数据改指表被 AI 修复改了 / 回滚了 `(新状态, "added"|"restored") -> None`：应用侧据此收掉这个项目的会话、取消在跑的试运行、
    #: 告诉开着这个项目的窗口（与数据失联弹窗的端点同一串）。None = 没有应用（测试 / 无界面），规则照写
    on_input_remap_changed: Callable[[dict, str], None] | None = None
    #: 环境 id → 叠加环境（`list_environments` 登记；`select_environment` 记住环境时要带上）
    environment_overlays: dict[str, dict] = field(default_factory=dict)
    #: 本次调用的元信息：`run_tool` 每次调用前清空，工具写 `approved_by`（"user"）与 `snapshot`（快照 id），审计读它
    call_meta: dict = field(default_factory=dict)
    #: 本会话允许的档；裸构造只放行 observe（只读），修复会话用 `for_fix_session` 加上 run
    allowed_tiers: frozenset[str] = frozenset({"observe"})
    #: 用户在 `propose_fix` 卡上已同意安装的包：规范名 -> 同意它的那一步（`install_packages` 名单外的包只要都在这里就不再问第二遍）
    approved_installs: dict = field(default_factory=dict)
    #: 用户亲手选过的路径：真实路径 -> 选它的那一步（`pick_file` 的回答、`ask_user` 选中的选项里写着的存在的路径）。
    #: `link_data_file` 的 `chosen` 在这里就不再弹第二张卡（选择本身就是确认）
    user_chosen_paths: dict = field(default_factory=dict)
    #: 本会话持有的环境租约 `[(解释器, 租约 id)]`：`change_python` 动手前给项目当前用的受管环境那一代加租约（`envlease`），
    #: 换到新一代后 `retire_unused` 才不会删掉旧代、`rollback` 才有地方回去；会话收尾时 `release_leases()`
    leases: list = field(default_factory=list)
    #: 项目外的读取清单（供卡片详情列出）
    reads: list[dict] = field(default_factory=list)
    step: int = 0
    case_insensitive: bool | None = None
    extra_deny: list[Path] | None = None

    def __post_init__(self) -> None:
        self.project_root = Path(self.project_root)
        self.home = Path(self.home)
        self.scope = readscope.ReadScope(
            home=self.home,
            project_root=self.project_root,
            env_prefixes=self.env_prefixes,
            extra_deny=self.extra_deny,
            case_insensitive=self.case_insensitive,
        )

    @classmethod
    def for_fix_session(cls, session_id: str, project_root: Path, **kw) -> FixContext:
        """修复会话的构造帮手：放行 `FIX_SESSION_TIERS`（需要用户点头的动作由各工具经 `ctx.ask` 把关）；其余字段同构造。"""
        kw.setdefault("allowed_tiers", FIX_SESSION_TIERS)
        return cls(session_id=session_id, project_root=project_root, **kw)

    def release_leases(self, only: list[tuple[str, str]] | None = None) -> None:
        """释放本会话的环境租约（幂等）。不带参数 = 全部，会话任何一种收尾（finish / 取消 / 失败 / 自动取消）都要调；
        `only` = 只放这几条（一次换代失败时撤销它自己新加的，此前成功换代保留的不动）。"""
        from .. import envlease

        if only is None:
            leases, self.leases = self.leases, []
        else:
            leases = [x for x in only if x in self.leases]
            self.leases = [x for x in self.leases if x not in leases]
        for python, owner in leases:
            envlease.release_retain(python, owner)

    def python_of(self, env_id: object) -> str | None:
        return self.environments.get(env_id) if isinstance(env_id, str) else None

    def note_read(self, resolved: readscope.Resolved, tool: str) -> None:
        """项目外的读取才记；项目内不记。"""
        if resolved.in_project:
            return
        self.reads.append({"tool": tool, "path": resolved.real, "step": self.step})


@dataclass(frozen=True)
class FixTool:
    name: str
    description: str
    schema: dict
    handler: Callable[[FixContext, dict], ToolResult]
    tier: Tier = "observe"
    waits_for_user: bool = False


def ok(text: str, summary: str = "", *, compact: str | None = None) -> ToolResult:
    return ToolResult("", "", readscope.scrub(text), summary=summary, compact=compact)


def err(code: str, message: str) -> ToolResult:
    return ToolResult("", "", message, is_error=True, code=code, summary=f"error: {code}")


def obj(props: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": required or [],
        "additionalProperties": False,
    }


def resolve_or_error(ctx: FixContext, raw: object, tool: str) -> readscope.Resolved | ToolResult:
    r = ctx.scope.check(raw)
    if r.ok:
        ctx.note_read(r, tool)
        return r
    msg = {
        readscope.REASON_DENIED: "That location holds credentials or private browser data and cannot be read.",
        readscope.REASON_OUTSIDE: "That path is outside the folders you may read (your home folder, "
        "the project, and the Python environment in use).",
        readscope.REASON_BAD_PATH: "`path` must be a non-empty string.",
    }[r.reason]
    return err(r.reason, msg)


def home_relative(ctx: FixContext, real: str) -> str:
    """给模型看的路径：主目录下的写成 `~/…`（少带一层本机用户名）。"""
    home = os.path.realpath(ctx.home)
    if real == home or real.startswith(home + os.sep):
        return "~" + real[len(home) :].replace(os.sep, "/")
    return real.replace(os.sep, "/")
