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

纯标准库；Flask 父进程 import（与 pool 同边界）。worker/browser 子进程
不需要它——profile 常量的唯一出处在 `figcapture`（它们平铺 import 得到），
这里 re-export。
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

from . import figcapture

PROFILE_SAFE = figcapture.PROFILE_SAFE
PROFILE_NATIVE = figcapture.PROFILE_NATIVE
PROFILES = (PROFILE_SAFE, PROFILE_NATIVE)

TARGET_SCRIPT = "script"
TARGET_MODULE = "module"
TARGET_KINDS = (TARGET_SCRIPT, TARGET_MODULE)

#: spec 序列化形态的版本。加可选字段不升；改语义 / 删字段才升。
SPEC_VERSION = 1

#: `stable_payload()` 覆盖的字段——**跨机器稳定**的那部分执行语义。
STABLE_FIELDS = ("profile", "target_kind", "target", "entry", "argv", "passthrough_savefig")


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
    argv: tuple[str, ...]  # 脚本看到的 sys.argv[1:]（safe 恒空）
    cwd: str  # safe: 会话沙盒；native: 用户 cwd（机器相关）
    env: dict[str, str] | None  # None = 原样继承；dict = 注入增量（见模块头）
    project_root: str  # 项目根（机器相关；safe 即 figures_dir 原串）
    passthrough_savefig: bool  # safe False（吞掉捕获）；native True（透传）
    #: **用户原样敲进去的那个 target 串**（native 专用；safe 恒空）。
    #: `target` 是规范化过的项目相对 POSIX 路径——它是身份，跨机器稳定；
    #: 而 `sys.argv[0]` 必须是用户当初写的那一串（`python figure.py` 给
    #: 相对，`python /abs/figure.py` 给绝对）。拿规范化后的那份去当 argv[0]
    #: 会让脚本里的 `os.path.dirname(sys.argv[0])` 指到别处。
    #: 机器相关，**不进 `stable_payload()`**。
    raw_target: str = ""

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
        if self.env is not None and (
            not isinstance(self.env, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in self.env.items())
        ):
            raise ValueError("env 必须是 None 或 str→str 的 dict（只放增量）")
        if not isinstance(self.passthrough_savefig, bool):
            raise ValueError("passthrough_savefig 必须是布尔值")
        if not isinstance(self.raw_target, str):
            raise ValueError("raw_target 必须是字符串")
        if self.profile == PROFILE_NATIVE:
            if self.entry is not None:
                raise ValueError("native profile 没有 entry 概念（恒 None）")
            if not self.passthrough_savefig:
                raise ValueError("native profile 的 savefig 必须透传（ADR 0014 §2）")
            if not self.raw_target:
                raise ValueError("native profile 必须带 raw_target（sys.argv[0] 的对拍口径）")

    # ---------------- 序列化 ----------------
    def to_payload(self) -> dict:
        """完整运行时形态（含机器相关路径）。进程内 / 调试用，不进用户文档。"""
        out = dataclasses.asdict(self)
        out["argv"] = list(self.argv)
        out["env"] = dict(self.env) if self.env is not None else None
        out["spec_version"] = SPEC_VERSION
        return out

    def stable_payload(self) -> dict:
        """跨机器稳定的字段子集（fingerprint / 持久化只准用这一档）。

        刻意不含 interpreter/cwd/project_root（机器相关路径）与 env
        （增量里有 MPLCONFIGDIR 这类本机路径）。
        """
        out = {k: getattr(self, k) for k in STABLE_FIELDS}
        out["argv"] = list(self.argv)
        out["spec_version"] = SPEC_VERSION
        return out


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
    )


def safe_spec(
    script: str,
    figures_dir: str | os.PathLike,
    entry: str,
    *,
    interpreter: str,
    sandbox: str,
    env: dict[str, str] | None = None,
) -> ExecutionSpec:
    """safe 档的**唯一权威构造函数**——运行时默认值只写在这里。

    safe 的语义（与 ADR 0014 §2 逐条对应）：target 是项目内脚本、argv 只有
    脚本自身（`sys.argv[1:]` 为空，由 worker 落实）、cwd 是会话沙盒（写入
    边界）、savefig 吞掉捕获（passthrough=False）。`env` 只接受增量
    （bundled runtime 时传 `runtime.child_env(base={})`，其余场合 None）。
    """
    return ExecutionSpec(
        profile=PROFILE_SAFE,
        interpreter=interpreter,
        target_kind=TARGET_SCRIPT,
        target=script,
        entry=entry,
        argv=(),
        cwd=sandbox,
        env=env,
        project_root=str(figures_dir),
        passthrough_savefig=False,
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
        env=None,
        project_root=project_root,
        passthrough_savefig=True,
        raw_target=raw_target,
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
    return [
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
        spec.cwd,
        "--entry",
        spec.entry,
    ]
