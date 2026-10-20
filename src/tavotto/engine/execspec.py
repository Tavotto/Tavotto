"""统一执行描述 ExecutionSpec（ADR 0014 §0，Compatibility Bridge Session 2）。

「跑一个脚本」在仓库里曾经是散在各处的一把参数：`EngineWorker.__init__` 与
`_spawn_spec()` 各拼一串 argv，entry/cwd/argv 的语义藏在 worker 的命令行
参数里。native profile（`tavotto run`，PR 2）还要再带上用户自己的解释器、
cwd、argv、env——继续散着拼，就是把同一个语义写第 N 份。本模块把它收成
一个不可变描述：

* **safe**（现状唯一实现）：Tavotto 挑解释器、cwd 切沙盒、argv 只有脚本
  自身、savefig 吞掉捕获、相对路径只读回退；
* **native**（本 Session 只建模不实现，字段先占住位置）：用户 invocation
  里的解释器/cwd/argv/env 原样，savefig 透传。ADR 0014 §2 的两档定义。

三条纪律：

1. **运行时默认值只有一个权威构造函数**（`safe_spec()`）。别处不得再
   手写 safe 档的默认值。
2. **worker argv 只有一个出处**（`worker_argv()`）。Python 池与 workerd
   两条 spawn 路径都吃它，逐字节等价由 `tests/test_execspec.py` 的
   golden argv 用例看护——`pool.py` 里那两处从此是它的消费者。
3. **env 只存增量**。spec 序列化时绝不携带整份父进程环境（那里面有
   密钥）；`env=None` 表示原样继承，dict 表示要额外注入的那几个变量
   （safe + bundled runtime 时是 `runtime.child_env(base={})` 那几个）。
   两条控制面怎么把增量落成子进程环境是 `pool.py` 的机制细节
   （EngineWorker 全量合成、workerd 只传增量），不在这里。

序列化分两档：

* `to_payload()` —— 完整运行时形态（含 interpreter/cwd/project_root 这些
  机器相关路径），只用于进程内传递与调试，**不进用户文档**；
* `stable_payload()` —— 跨机器稳定的字段子集（profile/target_kind/target/
  entry/argv/passthrough_savefig + 版本号）。fingerprint 与将来要持久化的
  场合只准用这一档；`target` 是**项目相对路径（POSIX 分隔）**，这是它
  能跨机器稳定的前提。

## 进程所有权（P06，ADR 0125）

native 档有**两类**进程所有权，写进 spec 的 `owner`（safe 恒空串——它的进程归 pool）：

* `terminal`（默认，`tavotto run`）：进程是 CLI 的子进程，stdin / env / cwd / 控制台都是
  caller 的，Tavotto 不 kill 它；`run_config` 恒空、`env` 恒 None、`stdin` 恒 `inherit`。
* `desktop`（桌面拥有的脚本进程）：由 sidecar 自己 spawn，所以启动身份要在 spec 里写全——
  解释器、入口、argv（经运行配置引用）、显式 cwd、env **增量**及其来源、stdin 通道。
  这一类的 `argv` / `env` 原文只在进程内，公开投影只带数量、`run_config` 与 env 键名。

`desktop` 不是 safe 的开关：safe 的守卫与 savefig 吞捕获一字不动，`owner="desktop"` 只能和
`profile=native` 一起出现（构造时拒绝其余组合）。

纯标准库；Flask 父进程 import（与 pool 同边界）。worker/browser 子进程
不需要它——profile 常量的唯一出处在 `figcapture`（它们平铺 import 得到），
这里 re-export。
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from . import figcapture

PROFILE_SAFE = figcapture.PROFILE_SAFE
PROFILE_NATIVE = figcapture.PROFILE_NATIVE
PROFILES = (PROFILE_SAFE, PROFILE_NATIVE)

TARGET_SCRIPT = "script"
TARGET_MODULE = "module"
TARGET_KINDS = (TARGET_SCRIPT, TARGET_MODULE)

#: native 档的进程所有权（见模块头「进程所有权」）。safe 的 `owner` 恒为空串。
OWNER_TERMINAL = "terminal"
OWNER_DESKTOP = "desktop"
OWNERS = (OWNER_TERMINAL, OWNER_DESKTOP)
#: 桌面拥有的进程，env 增量叠在哪一份环境上。**只承认 sidecar 自己的环境**：激活过的 conda / IDE /
#: 内核环境没有证据就不声称恢复（ADR 0125 §四）；将来要加来源，必须同时给出可核的恢复证据。
ENV_SOURCE_SIDECAR = "sidecar_inherited"
ENV_SOURCES = (ENV_SOURCE_SIDECAR,)
#: 脚本的 stdin 通道：`inherit` = caller 的（terminal 恒是它）；`pipe` = 桌面持有写端，由桌面决定何时送入 /
#: 送 EOF；`null` = 一开始就是 EOF（脚本 `input()` 得到 EOFError，而不是永远挂着）；`bridge` = 进程的 OS stdin 是
#: null，但 Bridge Runner 把脚本的 `input` / `sys.stdin` 读取 / `getpass` 变成控制通道上的提问，由界面作答——
#: 同一个进程、同一批对象续跑（P07，ADR 0126；桌面「打开并运行」用它）。
STDIN_INHERIT = "inherit"
STDIN_PIPE = "pipe"
STDIN_NULL = "null"
STDIN_BRIDGE = "bridge"
STDIN_MODES = (STDIN_INHERIT, STDIN_PIPE, STDIN_NULL, STDIN_BRIDGE)

#: spec 序列化形态的版本。加可选字段不升；改语义 / 删字段才升。
SPEC_VERSION = 1

#: safe 档的工作目录模式（ADR 0047 / ADR 0057）。`sandbox` = 会话沙盒（默认，写入
#: 边界）；`project` = 脚本自己所在的目录——脚本用相对路径找数据（`exists` / `glob` /
#: C++ 读取器）时唯一能让它们全部成立的形态；`project_root` = 项目根（Tavotto 打开的
#: 那个目录）——`paper/scripts/figure.py` 读 `data/x.csv` 而数据在 `paper/data/` 时，
#: 用户在终端里就是站在 `paper/` 敲 `python scripts/figure.py` 的（FO02）。守卫、
#: savefig 捕获、解释器链一字不动；变的只有 cwd。**`project` 的语义不变**：它过去是、
#: 现在也是脚本目录（ADR 0047），项目根是新加的第三档而不是对它的重新解释。
CWD_SANDBOX = "sandbox"
CWD_PROJECT = "project"
CWD_PROJECT_ROOT = "project_root"
CWD_MODES = (CWD_SANDBOX, CWD_PROJECT, CWD_PROJECT_ROOT)

#: LaunchContext（统一实施包 U01，ADR 0053）：「脚本看到的 cwd 是从哪来的」
#: 与「它能往哪写」两个维度，从 spec **派生**、不另存。三个来源 + 沙盒：
#:
#: * `sandbox`        —— safe 默认：cwd 是会话沙盒（写入边界）；
#: * `script.parent`  —— safe 的 `cwd_mode=project`（ADR 0047）：cwd 是脚本自己
#:                        所在的目录。**它不是 `project.root`**——FO-041 要求把
#:                        三者分清，而现状 project 档就是 script.parent，语义不变；
#: * `project.root`   —— safe 的 `cwd_mode=project_root`（ADR 0057）：cwd 是项目根，
#:                        即 Tavotto 打开的那个目录。U01 时它只是占位；U03 起由
#:                        `workdir` 的决策（用户确认过的目录）生产，并被三条 spawn
#:                        路径真实消费到 `--cwd`；
#: * `invocation.cwd` —— native：用户敲命令时的 cwd 原样。
#:
#: 写入模式与 cwd 来源是**两个维度**：safe 两档都装着 unlink / write_text 守卫，
#: 差别只在「相对路径写到哪」；native 没有守卫，脚本拥有用户的全部权限。
CWD_ORIGIN_SANDBOX = "sandbox"
CWD_ORIGIN_SCRIPT_PARENT = "script.parent"
CWD_ORIGIN_PROJECT_ROOT = "project.root"
CWD_ORIGIN_INVOCATION = "invocation.cwd"
#: 桌面拥有的 native 进程（P06，ADR 0125）：cwd 是桌面启动时**显式**给的那个目录，不是任何 shell 的。
CWD_ORIGIN_DESKTOP_LAUNCH = "desktop.cwd"
CWD_ORIGINS = (
    CWD_ORIGIN_SANDBOX,
    CWD_ORIGIN_SCRIPT_PARENT,
    CWD_ORIGIN_PROJECT_ROOT,
    CWD_ORIGIN_INVOCATION,
    CWD_ORIGIN_DESKTOP_LAUNCH,
)
WRITE_MODE_SANDBOXED = "sandboxed"  # 相对路径写进沙盒；真实图库有删 / 写守卫
WRITE_MODE_PROJECT_DIR = "project_dir"  # 相对路径写进脚本目录；守卫照旧（ADR 0047）
WRITE_MODE_UNRESTRICTED = "unrestricted"  # native：没有守卫，与用户自己敲命令等同
WRITE_MODES = (WRITE_MODE_SANDBOXED, WRITE_MODE_PROJECT_DIR, WRITE_MODE_UNRESTRICTED)
#: `launch_context()` 形态的版本。加可选字段不升；改语义 / 删字段才升。
LAUNCH_CONTEXT_VERSION = 1

#: 用户给的脚本 argv 的上限（T03）。私有请求管道仍需有界，别让一个粘错的
#: 超长文本占满请求与运行配置。超限在边界上拒绝，不截断（截断 = 静默丢参数）。
MAX_ARGV_TOKENS = 256
MAX_ARGV_CHARS = 32 * 1024
#: JSON 载荷预算（字符，保留 T03 的输入上限）。safe argv 经私有请求管道传输，
#: 不再占 Windows 命令行；ASCII 转义仍给序列化开销一个稳定上界。
MAX_ARGV_WIRE_CHARS = 16 * 1024


def argv_wire(argv) -> str:
    """argv 的序列化预算：ASCII 转义的紧凑 JSON 数组。"""
    return json.dumps(list(argv), ensure_ascii=True, separators=(",", ":"))


#: `stable_payload()` 覆盖的字段——**跨机器稳定**的那部分执行语义。
#: `cwd_mode` 在列：它改变脚本看到的世界（相对路径指到哪），是语义不是路径。
STABLE_FIELDS = (
    "profile",
    "target_kind",
    "target",
    "entry",
    "argv",
    "passthrough_savefig",
    "cwd_mode",
)


#: 本进程的随机密钥：池键 / 缓存目录里 argv 的区分用它做 HMAC。**不落盘、不出进程**——argv 常常
#: 低熵（`--seed 1`），裸 sha256 可以被穷举反推；带随机密钥的摘要只在本进程内有意义。
_PROCESS_KEY = secrets.token_bytes(32)


class FrozenDict(dict):
    """构造后不可改的 dict（spec 里的 `env` / 改指规则用）。

    冻结的 dataclass 只冻结字段绑定，不冻结字段指向的对象：调用方保留着传进来的 dict，事后一改，
    spec 与据它算出的回执 / `env_keys` 就悄悄跟着变。所以 spec 构造时**复制**并换成它。仍是 `dict`
    子类（`dict(x)` / `{**x}` / `json.dumps` / `==` 照常），只是所有原地修改都抛 `TypeError`。"""

    __slots__ = ()

    def _ro(self, *a, **k):
        raise TypeError("ExecutionSpec 里的映射是只读的（构造时已复制）")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _ro
    __ior__ = _ro

    def __reduce__(self):  # copy / pickle 经构造函数重建，不走被禁掉的 __setitem__
        return (FrozenDict, (dict(self),))


@dataclasses.dataclass(frozen=True)
class RunSelection:
    """一次执行选中的运行配置：本机引用 + 它冻结的 argv。池 / worker / 一次性重放都吃这一个对象。"""

    config_id: str
    argv: tuple[str, ...]
    sensitive: bool = False

    def __post_init__(self) -> None:
        if not self.config_id or not isinstance(self.argv, tuple) or not self.argv:
            raise ValueError("RunSelection 需要配置引用与非空 argv（空 argv = 不选配置）")

    @property
    def key(self) -> str:
        """池键 / 缓存目录的区分段：不同 argv（或同 argv 的不同配置引用）永不共用会话。"""
        payload = json.dumps([self.config_id, list(self.argv), self.sensitive], ensure_ascii=True)
        return hmac.new(_PROCESS_KEY, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:16]

    @property
    def input_key(self) -> str:
        """Private prompt identities cannot be brute-forced from public run IDs."""
        return hmac.new(
            _PROCESS_KEY, ("input:" + self.key).encode("ascii"), hashlib.sha256
        ).hexdigest()


def run_kwargs(run: RunSelection | None) -> dict:
    """`RunSelection` → `safe_spec` 的关键字参数（没有配置 = 空，调用形状与 T03 之前一致）。

    **唯一出处**（T12）：池的三条 spawn 路径与准备计划的启动上下文都经这里，不各自拼 `argv` / `run_config`。"""
    return {} if run is None else {"argv": run.argv, "run_config": run.config_id}


def write_roots_key(roots) -> str:
    """写入根绊线的授权根 → 池键 / 缓存目录的区分段（空串 = 没装绊线）。

    **唯一出处**：`pool._worker_key` / `_worker_base` 都用它。绊线是 worker 的身份——同一脚本，MCP 带绊线的
    会话与 GUI 不带的会话必须是两条（否则 GUI 取到带绊线的会话看到「写入落在授权根之外」，MCP 取到没有绊线的
    会话等于没装）。根集合变了（用户新绑了工作区）也是另一条：根是 spawn 时烤进 worker 的。
    路径不是秘密，摘要只为得到定长、可进目录名的段。"""
    if roots is None:
        return ""
    payload = json.dumps(sorted(roots), ensure_ascii=True, separators=(",", ":"))
    return "wr" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _normalize_target(target: str, target_kind: str) -> str:
    """script 目标 → 项目相对路径（POSIX）；module 目标原样。

    绝对路径直接拒绝：spec 的 target 是要进 fingerprint / 未来进文档的，
    混进绝对路径就跨不了机器（判据与 figcapture 的描述符同一份）。
    """
    if target_kind == TARGET_MODULE:
        if not target or not all(p.isidentifier() for p in target.split(".")):
            raise ValueError(f"module 目标必须是合法模块名: {target!r}")
        return target
    return figcapture.normalize_relative_script(target)


@dataclasses.dataclass(frozen=True)
class ExecutionSpec:
    """一次脚本执行的完整描述（不可变，可 JSON 化，无不可序列化对象）。"""

    profile: str  # PROFILE_SAFE | PROFILE_NATIVE
    interpreter: str  # 解释器绝对路径（机器相关）
    target_kind: str  # TARGET_SCRIPT | TARGET_MODULE
    target: str  # script: 项目相对路径（POSIX）；module: 模块名
    entry: str | None  # safe 的入口函数；native 恒 None
    #: 脚本看到的 sys.argv[1:]（safe 缺省空；T03 起可以是用户给的精确 token）。repr 不带值：可能是路径 / 令牌。
    argv: tuple[str, ...] = dataclasses.field(repr=False)
    cwd: str  # safe: 会话沙盒；native: 用户 cwd / 桌面显式 cwd（机器相关）
    #: None = 原样继承；dict = 注入增量（见模块头）。repr 不带值：增量里可能有密钥。
    env: dict[str, str] | None = dataclasses.field(repr=False)
    project_root: str  # 项目根（机器相关；safe 即 figures_dir 原串）
    passthrough_savefig: bool  # safe False（吞掉捕获）；native True（透传）
    #: **用户原样敲进去的那个 target 串**（native 专用；safe 恒空）。
    #: `target` 是规范化过的项目相对 POSIX 路径——它是身份，跨机器稳定；
    #: 而 `sys.argv[0]` 必须是用户当初写的那一串（`python figure.py` 给
    #: 相对，`python /abs/figure.py` 给绝对）。拿规范化后的那份去当 argv[0]
    #: 会让脚本里的 `os.path.dirname(sys.argv[0])` 指到别处。
    #: 机器相关，**不进 `stable_payload()`**。
    raw_target: str = ""
    #: safe 档：cwd 是沙盒还是脚本目录（ADR 0047）。native 恒 `sandbox` 占位
    #: （它的 cwd 是用户的，没有这个维度）。
    cwd_mode: str = CWD_SANDBOX
    #: safe 档的会话沙盒目录（机器相关）。`cwd_mode == sandbox` 时与 `cwd`
    #: 相同；`project` 时 cwd 是脚本目录，而写入边界的目录仍要交给 worker
    #: （`--sandbox`）。空串 = 与 `cwd` 相同（老 payload 的形态）。
    sandbox: str = ""
    #: safe 档：用户指认过的只读改指表（ADR 0106，`figcapture.clean_remap_rules` 的形状）。
    #: 规则里全是本机绝对路径——**机器相关，不进 `stable_payload()`**；写回的重放与热态读
    #: 同一份数据，靠的是三条 spawn 路径都从 `inputremap.rules_for()` 取（与 `cwd_mode` 同一条纪律）。
    #: 空元组 = 没有规则，argv 逐字节不变。native 恒空（那是用户自己的 `python fig.py`）。
    input_remap: tuple = ()
    #: 这次执行的**运行配置引用**（T03，`engine/runconfig.py`）：本机不透明 id（`rc_…`），空串 =
    #: 没有（旧行为：argv 为空）。argv 非空时它必须有值——`argv` 的原文只活在 `to_payload()`
    #: 与私有请求管道里，**公开投影（`stable_payload` / `launch_context` / 回执）只带数量与这个 id**，
    #: 参数值可能是路径 / 令牌 / 患者编号，低熵摘要也不行。
    run_config: str = ""
    #: native 档的进程所有权（`OWNER_*`）。safe 恒空串；native 缺省（空串）归一成 `terminal`。
    owner: str = ""
    #: 仅 `owner=desktop`：`env` 增量叠在哪一份环境上（`ENV_SOURCES`）。其余恒空串。
    env_source: str = ""
    #: native 的 stdin 通道（`STDIN_*`）。terminal 恒 `inherit`；safe 不使用（恒 `inherit` 占位）。
    stdin: str = STDIN_INHERIT
    #: safe 档：写入根绊线的授权根（#898，ADR 0141）。`None` = 不装绊线（GUI / CLI 的旧行为，argv 逐字节
    #: 不变）；元组 = worker 在脚本 build 期间把根外的写打开变成 `write_outside_roots`。**它是 worker 的
    #: 身份**：进 `--write-roots`（`worker_argv` 的唯一出处），所以 workerd 的 spawn 规格哈希天然不同，池键 /
    #: 缓存目录再并入 `write_roots_key`——MCP 与 GUI 取同一脚本得不到同一条会话。机器相关路径，
    #: **不进 `stable_payload()`**。native 恒 None。
    write_roots: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.profile not in PROFILES:
            raise ValueError(f"profile 非法: {self.profile!r}（可选 {PROFILES}）")
        if self.target_kind not in TARGET_KINDS:
            raise ValueError(f"target_kind 非法: {self.target_kind!r}（可选 {TARGET_KINDS}）")
        object.__setattr__(self, "target", _normalize_target(self.target, self.target_kind))
        if not isinstance(self.interpreter, str) or not self.interpreter:
            raise ValueError("interpreter 必须是非空字符串")
        if self.entry is not None and (not isinstance(self.entry, str) or not self.entry):
            raise ValueError(f"entry 必须是 None 或非空字符串: {self.entry!r}")
        if self.profile == PROFILE_SAFE and self.entry is None:
            raise ValueError("safe profile 必须指定 entry（内联脚本用 '__main__'）")
        if not isinstance(self.argv, tuple) or not all(isinstance(a, str) for a in self.argv):
            raise ValueError(f"argv 必须是字符串元组: {self.argv!r}")
        if any("\x00" in a for a in self.argv):
            raise ValueError("argv 的 token 不能含 NUL（操作系统的 argv 装不下）")
        # T03 的输入预算只管走私有请求管道的 safe 档；native 的 argv 是用户自己的命令行
        # （走 bridge_argv，不进私有管道），沿用操作系统本身的约束，不新加更严的上限。
        if self.profile == PROFILE_SAFE:
            if len(self.argv) > MAX_ARGV_TOKENS or sum(len(a) for a in self.argv) > MAX_ARGV_CHARS:
                raise ValueError("argv 超出上限（token 数或总字符数）")
            if len(argv_wire(self.argv)) > MAX_ARGV_WIRE_CHARS:
                raise ValueError("argv 序列化后超出载荷长度上限")
        if not isinstance(self.run_config, str):
            raise ValueError("run_config 必须是字符串")
        if self.profile == PROFILE_SAFE and self.argv and not self.run_config:
            raise ValueError("safe 的 argv 非空时 run_config 必须非空（产物要绑定到它的配置）")
        if self.profile == PROFILE_NATIVE and not self.owner:
            object.__setattr__(self, "owner", OWNER_TERMINAL)  # 老调用形状 = 终端拥有
        if self.profile == PROFILE_SAFE and (
            self.owner or self.env_source or self.stdin != STDIN_INHERIT
        ):
            raise ValueError(
                "owner / env_source / stdin 只属于 native profile（safe 不接受这些开关）"
            )
        if self.owner and self.owner not in OWNERS:
            raise ValueError(f"owner 非法: {self.owner!r}（可选 {OWNERS}）")
        if self.stdin not in STDIN_MODES:
            raise ValueError(f"stdin 非法: {self.stdin!r}（可选 {STDIN_MODES}）")
        if self.profile == PROFILE_NATIVE and self.owner == OWNER_TERMINAL:
            if self.run_config:
                raise ValueError(
                    "terminal 拥有的 native 没有运行配置引用（argv 是用户自己的命令行）"
                )
            if self.env is not None or self.env_source or self.stdin != STDIN_INHERIT:
                raise ValueError("terminal 拥有的 native 原样继承 env / stdin（env 恒 None）")
        if self.profile == PROFILE_NATIVE and self.owner == OWNER_DESKTOP:
            if self.argv and not self.run_config:
                raise ValueError("桌面拥有的 native 的 argv 非空时 run_config 必须非空")
            if self.env_source not in ENV_SOURCES:
                raise ValueError(f"桌面拥有的 native 必须声明 env_source（可选 {ENV_SOURCES}）")
            if self.stdin == STDIN_INHERIT:
                raise ValueError("桌面没有可继承的 stdin：必须是 pipe 或 null")
            if not os.path.isabs(self.cwd) or not os.path.isabs(self.interpreter):
                raise ValueError("桌面拥有的 native 必须给显式的绝对 cwd 与解释器")
        if self.env is not None and (
            not isinstance(self.env, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in self.env.items())
        ):
            raise ValueError("env 必须是 None 或 str→str 的 dict（只放增量）")
        if not isinstance(self.passthrough_savefig, bool):
            raise ValueError("passthrough_savefig 必须是布尔值")

        if not isinstance(self.raw_target, str):
            raise ValueError("raw_target 必须是字符串")
        if self.cwd_mode not in CWD_MODES:
            raise ValueError(f"cwd_mode 非法: {self.cwd_mode!r}（可选 {CWD_MODES}）")
        if not isinstance(self.sandbox, str):
            raise ValueError("sandbox 必须是字符串")
        if not isinstance(self.input_remap, tuple) or figcapture.clean_remap_rules(
            list(self.input_remap)
        ) != list(self.input_remap):
            raise ValueError(f"input_remap 必须是校验过的规则元组: {self.input_remap!r}")
        if self.write_roots is not None:
            if not isinstance(self.write_roots, tuple) or not all(
                isinstance(r, str) and r for r in self.write_roots
            ):
                raise ValueError(f"write_roots 必须是非空字符串元组或 None: {self.write_roots!r}")
            if self.profile != PROFILE_SAFE:
                raise ValueError("写入根绊线只属于 safe profile（native 是用户自己的 python）")
        if self.profile == PROFILE_NATIVE:
            if self.cwd_mode != CWD_SANDBOX:
                raise ValueError("native profile 没有 cwd_mode 这个维度（cwd 是用户的）")
            if self.input_remap:
                raise ValueError("native profile 不改指输入（ADR 0106：那是用户自己的 python）")
            if self.entry is not None:
                raise ValueError("native profile 没有 entry 概念（恒 None）")
            if not self.passthrough_savefig:
                raise ValueError("native profile 的 savefig 必须透传（ADR 0014 §2）")
            if not self.raw_target:
                raise ValueError("native profile 必须带 raw_target（sys.argv[0] 的对拍口径）")

        # 校验过之后复制并冻结：spec 里不留调用方还拿得到的可变对象（argv 已是 str 元组）
        if self.env is not None:
            object.__setattr__(self, "env", FrozenDict(self.env))
        if self.input_remap:
            object.__setattr__(self, "input_remap", tuple(FrozenDict(r) for r in self.input_remap))

    # ---------------- 序列化 ----------------
    def to_payload(self) -> dict:
        """完整运行时形态（含机器相关路径）。进程内 / 调试用，不进用户文档。"""
        out = dataclasses.asdict(self)
        out["argv"] = list(self.argv)
        out["env"] = dict(self.env) if self.env is not None else None
        out["input_remap"] = [dict(r) for r in self.input_remap]
        out["write_roots"] = None if self.write_roots is None else list(self.write_roots)
        out["spec_version"] = SPEC_VERSION
        return out

    def stable_payload(self) -> dict:
        """跨机器稳定的字段子集（fingerprint / 持久化只准用这一档）。

        刻意不含 interpreter/cwd/project_root（机器相关路径）与 env
        （增量里有 MPLCONFIGDIR 这类本机路径）。
        """
        out = {k: getattr(self, k) for k in STABLE_FIELDS}
        # 空 argv 的形状逐字节不变（golden）；非空时**原文不出门**：公开身份只带数量与本机不透明引用。
        if self.profile == PROFILE_NATIVE and self.owner == OWNER_TERMINAL:
            # terminal 拥有的 native：argv 是用户自己的命令行，沿用旧形状
            out["argv"] = list(self.argv)
        else:
            out["argv"] = []
            if self.argv:
                out["argv_count"] = len(self.argv)
                out["run_config"] = self.run_config
        if self.owner == OWNER_DESKTOP:
            out["owner"] = (
                self.owner
            )  # 只有桌面拥有的才多这一键：terminal 的身份逐字节不变（golden）
        out["spec_version"] = SPEC_VERSION
        return out


def launch_context(spec: ExecutionSpec, *, grant: dict | None = None) -> dict:
    """spec → LaunchContext（04_ARCHITECTURE §2 的那一行）：跨机器稳定的执行上下文。

    只放**语义**：`interpreter` 与 `cwd` 这类机器路径不在里面（它们属于
    `to_payload()` 与 ExecutionReceipt 的私有失效键）。字段：

    * `cwd_origin` —— `CWD_ORIGINS` 之一（见常量旁的说明）；
    * `write_mode` —— `WRITE_MODES` 之一；
    * `target` / `entry` / `argv` / `profile` / `cwd_mode` —— 与 `stable_payload()` 同源；
    * `grant` —— 授权记录（safe 的 `cwd_mode=project` 由 `workdir.grant_for()` 给；
      调用方不给就是 `None`，表示「这条上下文没有附带授权信息」，**不是「没授权」**）。

    派生规则是闭集，每个 profile × cwd_mode 组合各一行；不认识的组合当场抛。
    """
    if spec.profile == PROFILE_NATIVE:
        origin = CWD_ORIGIN_DESKTOP_LAUNCH if spec.owner == OWNER_DESKTOP else CWD_ORIGIN_INVOCATION
        write_mode = WRITE_MODE_UNRESTRICTED
    elif spec.cwd_mode == CWD_SANDBOX:
        origin, write_mode = CWD_ORIGIN_SANDBOX, WRITE_MODE_SANDBOXED
    elif spec.cwd_mode == CWD_PROJECT:
        origin, write_mode = CWD_ORIGIN_SCRIPT_PARENT, WRITE_MODE_PROJECT_DIR
    elif spec.cwd_mode == CWD_PROJECT_ROOT:
        origin, write_mode = CWD_ORIGIN_PROJECT_ROOT, WRITE_MODE_PROJECT_DIR
    else:  # pragma: no cover — __post_init__ 已经拦住了不认识的 cwd_mode
        raise ValueError(f"launch_context 不认识的组合: {spec.profile}/{spec.cwd_mode}")
    if grant is not None and not isinstance(grant, dict):
        raise ValueError("grant 必须是 None 或 dict")
    return {
        "launch_context_version": LAUNCH_CONTEXT_VERSION,
        "profile": spec.profile,
        "target_kind": spec.target_kind,
        "target": spec.target,
        "entry": spec.entry,
        # 原文不进 LaunchContext（它会随计划 / 回执公开）：terminal 拥有的 native 的 argv 是用户自己的
        # 命令行（沿用旧形状）；桌面拥有的与 safe 一样只带数量与运行配置引用
        "argv": (
            list(spec.argv)
            if spec.profile == PROFILE_NATIVE and spec.owner == OWNER_TERMINAL
            else []
        ),
        "cwd_mode": spec.cwd_mode,
        "cwd_origin": origin,
        "write_mode": write_mode,
        "passthrough_savefig": spec.passthrough_savefig,
        "grant": dict(grant) if grant is not None else None,
        **(
            {"argv_count": len(spec.argv), "run_config": spec.run_config}
            if spec.argv and not (spec.profile == PROFILE_NATIVE and spec.owner == OWNER_TERMINAL)
            else {}
        ),
        # 桌面拥有的启动身份（P06）：env 只公开**键名**与来源，值永远不出进程
        **(
            {
                "owner": spec.owner,
                "env_source": spec.env_source,
                "env_keys": sorted(spec.env or ()),
                "stdin": spec.stdin,
            }
            if spec.owner == OWNER_DESKTOP
            else {}
        ),
    }


def spec_from_payload(data: dict) -> ExecutionSpec:
    """`to_payload()` 的逆——逐字段校验后重建，坏数据在边界上抛 ValueError。"""
    if not isinstance(data, dict):
        raise ValueError("ExecutionSpec payload 必须是对象")
    version = data.get("spec_version", SPEC_VERSION)
    if version != SPEC_VERSION:
        raise ValueError(f"不认识的 spec_version: {version!r}（本实现说 v{SPEC_VERSION}）")
    argv = data.get("argv", [])
    if not isinstance(argv, (list, tuple)):
        raise ValueError(f"argv 必须是数组: {argv!r}")
    return ExecutionSpec(
        profile=data.get("profile"),
        interpreter=data.get("interpreter"),
        target_kind=data.get("target_kind"),
        target=data.get("target"),
        entry=data.get("entry"),
        argv=tuple(argv),
        cwd=data.get("cwd", ""),
        env=data.get("env"),
        project_root=data.get("project_root", ""),
        passthrough_savefig=bool(data.get("passthrough_savefig", False)),
        raw_target=data.get("raw_target", "") or "",
        cwd_mode=data.get("cwd_mode", CWD_SANDBOX) or CWD_SANDBOX,
        sandbox=data.get("sandbox", "") or "",
        input_remap=tuple(figcapture.clean_remap_rules(data.get("input_remap") or [])),
        run_config=data.get("run_config", "") or "",
        owner=data.get("owner", "") or "",
        env_source=data.get("env_source", "") or "",
        stdin=data.get("stdin", STDIN_INHERIT) or STDIN_INHERIT,
        write_roots=(None if data.get("write_roots") is None else tuple(data.get("write_roots"))),
    )


def run_cwd(figures_dir: str | os.PathLike, script: str, cwd_mode: str) -> str | None:
    """safe 档脚本的 cwd：`project` = 脚本所在目录、`project_root` = 项目根、`sandbox` = None
    （由调用方给会话沙盒）。`safe_spec` 与 MCP 桥的 argv 范围检查读这同一个出处（#818）。"""
    if cwd_mode not in CWD_MODES:
        raise ValueError(f"cwd_mode 非法: {cwd_mode!r}（可选 {CWD_MODES}）")
    if cwd_mode == CWD_PROJECT:
        return str((Path(figures_dir) / figcapture.normalize_relative_script(script)).parent)
    if cwd_mode == CWD_PROJECT_ROOT:
        return str(Path(figures_dir))
    return None


def desktop_cwd(figures_dir: str | os.PathLike, script: str, cwd_mode: str) -> str:
    """桌面拥有的 native 进程的 cwd（P06，ADR 0125 §三）：**没有沙盒这一档**。

    `project_root` = 项目根（用户在工作目录问题里选过的那一档，ADR 0057）；其余——包括 safe 默认的
    `sandbox`——一律是脚本所在目录：用户自己的 Python「像终端里那样跑」的意思表示（ADR 0107），
    也是相对数据路径最常成立的那一个。"""
    if cwd_mode == CWD_PROJECT_ROOT:
        return str(Path(figures_dir))
    return run_cwd(figures_dir, script, CWD_PROJECT)


def safe_spec(
    script: str,
    figures_dir: str | os.PathLike,
    entry: str,
    *,
    interpreter: str,
    sandbox: str,
    env: dict[str, str] | None = None,
    cwd_mode: str = CWD_SANDBOX,
    input_remap=(),
    argv: tuple[str, ...] | list[str] = (),
    run_config: str = "",
    write_roots: tuple[str, ...] | list[str] | None = None,
) -> ExecutionSpec:
    """safe 档的**唯一权威构造函数**——运行时默认值只写在这里。

    safe 的语义（与 ADR 0014 §2 逐条对应）：target 是项目内脚本、argv 只有
    脚本自身（`sys.argv[1:]` 缺省为空，由 worker 落实；T03 起用户可以给**精确的 token 列表**——
    `argv` + 它的本机运行配置引用 `run_config`，缺省空 = 旧行为逐字节不变）、cwd 是会话沙盒（写入
    边界）、savefig 吞掉捕获（passthrough=False）。`env` 只接受增量
    （bundled runtime 时传 `runtime.child_env(base={})`，其余场合 None）。

    `cwd_mode=project`（ADR 0047）时 cwd 换成**脚本自己所在的目录**，
    `cwd_mode=project_root`（ADR 0057）时换成**项目根**（`figures_dir` 原串），其余
    一字不变：沙盒目录仍交给 worker 当写入边界的参照，守卫与 savefig 捕获
    照旧。脚本用相对路径**写**的中间文件会像终端里一样落进项目目录——
    这是这两个模式的定义，不是漏洞；文案里要如实说。
    """
    cwd = run_cwd(figures_dir, script, cwd_mode)
    if cwd is None:
        cwd = sandbox
    return ExecutionSpec(
        profile=PROFILE_SAFE,
        interpreter=interpreter,
        target_kind=TARGET_SCRIPT,
        target=script,
        entry=entry,
        argv=tuple(argv),
        cwd=cwd,
        env=env,
        project_root=str(figures_dir),
        passthrough_savefig=False,
        cwd_mode=cwd_mode,
        sandbox=sandbox,
        # ADR 0106：用户指认过的只读改指表（`inputremap.rules_for`），空 = 没有
        input_remap=tuple(figcapture.clean_remap_rules(list(input_remap or ()))),
        run_config=run_config,
        write_roots=None if write_roots is None else tuple(write_roots),
    )


def native_spec(
    raw_target: str,
    *,
    interpreter: str,
    cwd: str,
    project_root: str,
    target_kind: str = TARGET_SCRIPT,
    argv: tuple[str, ...] = (),
) -> ExecutionSpec:
    """native 档的**唯一权威构造函数**（ADR 0014 §2 / ADR 0020）。

    native 的语义逐条与 safe 相反，而每一条都是「与用户自己敲那条命令等同」
    的一部分：

    * `entry=None` —— 没有入口函数这个概念，`python fig.py` 跑的是整个模块；
    * `argv` —— 用户的**原样**（safe 恒空，因为那是隔离出来的）；
    * `cwd` —— 用户的原样（safe 是沙盒）；
    * `env=None` —— **原样继承**。bridge 只会额外注入 token 那一个变量，
      而且子进程一起来就把它摘掉（`bridge_runner._take_token`）；
    * `passthrough_savefig=True` —— 照常写文件。

    `raw_target` 是用户敲的那串（argv[0] 的对拍口径）；`target` 由它规范化
    成项目相对 POSIX 路径（身份，跨机器稳定）。target 落在 `project_root`
    之外时会在这里抛——描述符与 asset id 的前提就是「项目相对」。
    """
    return _native(
        raw_target,
        interpreter=interpreter,
        cwd=cwd,
        project_root=project_root,
        target_kind=target_kind,
        argv=argv,
    )


def desktop_native_spec(
    raw_target: str,
    *,
    interpreter: str,
    cwd: str,
    project_root: str,
    target_kind: str = TARGET_SCRIPT,
    argv: tuple[str, ...] = (),
    run_config: str = "",
    env: dict[str, str] | None = None,
    stdin: str = STDIN_NULL,
) -> ExecutionSpec:
    """**桌面拥有的** native 档启动身份的唯一权威构造函数（P06，ADR 0125）。

    与 `native_spec` 的差别正是「进程归谁」带来的那几条，别的一字不动（savefig 透传、没有守卫、
    `entry=None`、`raw_target` 是 `sys.argv[0]`）：

    * `cwd` **必须显式且绝对**——桌面没有「用户当时站在哪」可继承；
    * `argv` 经运行配置引用 `run_config` 绑定（原文只在进程内，公开投影只带数量与引用）；
    * `env` 是叠在 sidecar 环境上的**增量**，来源恒为 `sidecar_inherited`：没有证据就不声称
      恢复了 conda 激活 / IDE / 内核环境；
    * `stdin` 由桌面持有（`pipe` 或 `null`），不再是「继承终端」。
    """
    return _native(
        raw_target,
        interpreter=interpreter,
        cwd=cwd,
        project_root=project_root,
        target_kind=target_kind,
        argv=argv,
        owner=OWNER_DESKTOP,
        run_config=run_config,
        env=env,
        env_source=ENV_SOURCE_SIDECAR,
        stdin=stdin,
    )


def _native(
    raw_target: str,
    *,
    interpreter: str,
    cwd: str,
    project_root: str,
    target_kind: str,
    argv: tuple[str, ...],
    owner: str = OWNER_TERMINAL,
    run_config: str = "",
    env: dict[str, str] | None = None,
    env_source: str = "",
    stdin: str = STDIN_INHERIT,
) -> ExecutionSpec:
    if target_kind == TARGET_MODULE:
        target = raw_target
    else:
        try:
            target = os.path.relpath(os.path.abspath(raw_target), os.path.abspath(project_root))
        except ValueError as exc:  # Windows：脚本与项目根不在同一个盘符上
            raise ValueError(
                f"脚本与项目根不在同一个盘符上，算不出项目相对路径: "
                f"{raw_target!r} / {project_root!r}"
            ) from exc
    return ExecutionSpec(
        profile=PROFILE_NATIVE,
        interpreter=interpreter,
        target_kind=target_kind,
        target=target,
        entry=None,
        argv=tuple(argv),
        cwd=cwd,
        env=env,
        project_root=project_root,
        passthrough_savefig=True,
        raw_target=raw_target,
        run_config=run_config,
        owner=owner,
        env_source=env_source,
        stdin=stdin,
    )


def bridge_argv(
    spec: ExecutionSpec,
    *,
    runner_py: str | os.PathLike,
    out_dir: str | os.PathLike,
    preview_dpi: int = 200,
    control_host: str = "",
    control_port: int = 0,
    report: str = "",
) -> list[str]:
    """native bridge 子进程的完整命令行——**唯一出处**（对照 `worker_argv`）。

    形状：

        <用户的解释器> <runner.py> <bridge 参数…> -- <用户的 argv…>

    `--` 之后是用户脚本自己的参数，runner 一个字都不解释。**解释器不加任何
    标志**（没有 `-B`、没有 `-S`、没有 `-I`）：那是用户环境的地盘，加一个
    标志就不是「与你自己敲那条命令等同」了。
    """
    if spec.profile != PROFILE_NATIVE:
        raise ValueError("bridge_argv 只服务 native profile（safe 走 worker_argv）")
    out = [
        spec.interpreter,
        str(runner_py),
        "--target-kind",
        spec.target_kind,
        "--target",
        spec.raw_target,
        "--out-dir",
        str(out_dir),
        "--preview-dpi",
        str(int(preview_dpi)),
        "--project-root",
        spec.project_root,
    ]
    if control_port:
        out += ["--control-host", control_host or "127.0.0.1", "--control-port", str(control_port)]
    if report:
        out += ["--report", report]
    if spec.run_config:
        # 桌面拥有的、带运行配置的启动：不透明引用（不是 argv 值）交给 runner，描述符 / 资产 id 才带 `~rc_…`，
        # 不会和不带配置的那次互相覆盖。terminal 拥有的恒空，命令行逐字节不变
        out += ["--run-config", spec.run_config]
    if spec.stdin == STDIN_BRIDGE:
        # 桌面拥有、stdin 走输入桥：脚本的 input / stdin / getpass 变成控制通道上的提问（P07，ADR 0126）。
        # 其余通道（terminal 的真 stdin、桌面的 pipe / null）命令行逐字节不变（golden）
        out.append("--input-bridge")
    if spec.owner == OWNER_DESKTOP:
        # 桌面拥有的启动：runner 观察脚本读到的项目内文件与 import 的本地模块（与 safe 同口径），native 成功记录据此
        # 带上 helper / 数据依据、可被沿用（#1047）。terminal 拥有的命令行逐字节不变（golden）
        out.append("--observe-inputs")
    out.append("--")
    out.extend(spec.argv)
    return out


def worker_argv(
    spec: ExecutionSpec,
    *,
    worker_py: str | os.PathLike,
    out_dir: str | os.PathLike,
    runtime_args: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """safe worker 子进程的完整命令行——**两条控制面共用的唯一出处**。

    `EngineWorker.__init__` 的 Popen 与 workerd 的 spawn 规格都吃这份；
    形状与 2026-08-25 之前两处手拼的完全一致（golden 用例钉住），这是
    重构不是改语义。`out_dir` 是会话产物目录（不属于执行语义，所以不在
    spec 里）；`runtime_args` 是 bundled runtime 的 `-B` 那一类解释器参数。
    """
    if spec.profile != PROFILE_SAFE or spec.target_kind != TARGET_SCRIPT:
        raise ValueError("worker_argv 目前只服务 safe/script（native 是 PR 2）")
    out = [
        spec.interpreter,
        *runtime_args,
        str(worker_py),
        "--script",
        str(Path(spec.project_root) / spec.target),
        "--figures-dir",
        spec.project_root,
        "--out-dir",
        str(out_dir),
        "--sandbox",
        spec.sandbox or spec.cwd,
        "--entry",
        spec.entry,
    ]
    if spec.cwd_mode != CWD_SANDBOX:
        # 只在非默认模式下多两个 token：默认模式的 argv 逐字节不变（golden）。
        # `project`（脚本目录）与 `project_root`（项目根）都走这两个 token——worker 只
        # 认「cwd 换到哪」，模式的名字是控制面的事。
        out += ["--cwd", spec.cwd]
    if spec.input_remap:
        # ADR 0106：只在有规则时多两个 token——没有规则的 argv 逐字节不变（golden）
        out += [
            "--input-remap",
            json.dumps(list(spec.input_remap), ensure_ascii=False, separators=(",", ":")),
        ]
    if spec.argv:
        # 只传不透明引用：精确 token 经私有请求管道传，既不出现在 OS 进程列表，
        # 也不受 Windows list2cmdline 的引号膨胀影响。引用仍参与 workerd 的 spawn
        # 身份；worker 缺少匹配载荷时拒绝执行，绝不回落为空 argv。
        out += ["--run-config", spec.run_config]
    if spec.write_roots is not None:
        # #898：写入根绊线。只在 MCP 来源的运行里多两个 token——没有它的 argv 逐字节不变（golden）。
        # 根进 argv（而不是环境变量）：workerd 的 spawn 规格哈希只看 argv / env / cwd，
        # 身份天然分开；env 在 workerd 路径下只是增量，易遗漏。
        out += [
            "--write-roots",
            json.dumps(list(spec.write_roots), ensure_ascii=False, separators=(",", ":")),
        ]
    return out
