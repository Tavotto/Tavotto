"""成功启动上下文（P09）：一次执行**真的成功**之后留下什么、再次打开时凭什么复用。

只存成功事实，不存「用户点过允许」，也不另建成功数据库：记录落在 `runconfig` 同一份登记的
`launches[脚本]` 里（`runconfig.set_launch_record`），由 `app._finalize_script_attempt` 在已有的成功收口里写。
记录是一份**可验证的引用**：

| 字段 | 内容 | 再次打开时怎么核 |
|---|---|---|
| `interpreter` | 路径 + 来源标签 + `projectenv.environment_generation` | 当前解释器同路径同来源同一代 |
| `launcher` | 启动方式的结构化引用（`direct` / `conda`：conda 路径 + prefix） + Conda 环境版本 `env_state` | 引用仍有效；装 / 升降包后 `env_state` 变了 = 重新核验 |
| `cwd_mode` | safe 档 cwd 模式 | 项目当前模式相同 |
| `run_config` | 运行配置**引用**（`rc_…`）；敏感配置只记「有过」 | 登记仍在、此刻可执行；敏感的不自动复用 |
| `source` | 脚本 sha1 + 观察到的本地 helper 模块 / 数据文件的 sha256（回执的自报） | 逐个重算；任何一个变了 / 没了 = 重新核验 |
| `binding_revision` | 预检时的数据绑定摘要（`databinding.binding_for().revision`） | 重算相同 |

**不存**：激活后的环境变量、argv 值、`getpass` / 敏感输入、答案原文（非敏感答案已有自己的登记
`scriptanswers`，不在这里复制第二份）。回执不完整（`partial`，没有自报的 helper 依据）→ 不记录：证据不全就不
假装可复用。

v2（V2 PR2，ADR 0133）起记录多一个 `profile`（`safe` | `native`）：safe 成功由 app 的成功收口按回执写，native 成功由同一个成功收口按计划快照写
（`NativeBasis`，由 app 成功收口经 `record_success` 同一事务写入；native 没有 helper 观察回执，`source.basis_observed: false`）。运行前路由读它回答「上次成功用哪种执行语义」。

记录只是**事实**，不驱动启动决策：启动方式是解释器路径的纯函数（`launcher.launcher_for_interpreter`），与脚本、项目、记录
无关——所以以前裸起成功过的 Conda 脚本，今后会带上激活环境（这本来就是用户对 Conda 环境的预期）。

`verify`（全部依据）——供「再次打开时沿用上次成功配置」的入口用。消费端是 `reusable_run_config`（V2 PR4，#982）：
只在请求**明确**带 `reuse_last_success: true`（界面的「打开 / 再次打开脚本」动作）且没有给 argv 时才用——不能靠省略 argv 推断沿用；
记录里的路径也只用来对账、不当可执行路径。

纯标准库；Flask 父进程侧。
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import time
from pathlib import Path

from . import databinding, figcapture, launcher, projectenv, runconfig, scriptargs, workdir

#: v2（V2 PR2，ADR 0133）：记录多了 `profile`（这次成功用的执行语义 `safe` | `native`）。v1 记录照常可读，按 `safe` 算——
#: 它们只可能来自 safe 回执。
RECORD_VERSION = 2
READABLE_VERSIONS = (1, 2)

PROFILE_SAFE = "safe"
PROFILE_NATIVE = "native"
PROFILES = (PROFILE_SAFE, PROFILE_NATIVE)

FACET_INTERPRETER = "interpreter"
FACET_ENVIRONMENT = "environment"
FACET_SOURCE = "source"
FACET_CWD = "cwd"
FACET_RUN_CONFIG = "run_config"
FACET_BINDING = "binding"
FACETS_ALL = (
    FACET_INTERPRETER,
    FACET_ENVIRONMENT,
    FACET_SOURCE,
    FACET_CWD,
    FACET_RUN_CONFIG,
    FACET_BINDING,
)

#: `verify` 的失败原因（闭集；给日志 / 测试，不进用户文案）
REASON_NO_RECORD = "no_record"
REASON_VERSION = "record_version"
REASON_INTERPRETER = "interpreter_changed"
REASON_ENVIRONMENT = "environment_changed"
REASON_LAUNCHER = "launcher_invalid"
REASON_SCRIPT = "script_changed"
REASON_HELPER = "helper_changed"
REASON_CWD = "cwd_mode_changed"
REASON_RUN_CONFIG = "run_config_unavailable"
REASON_BINDING = "binding_changed"

#: 记 helper 依据的数量上限（与回执的观察上限同量级，防登记膨胀）
MAX_BASIS = 200


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _project_decided(project_root: str | Path, interpreter: str) -> bool:
    try:
        rec = projectenv.remembered_record(project_root)
    except Exception:  # noqa: BLE001 — 读不出就当「定过」：宁可少复用
        return True
    return rec is not None and rec.get("mode") != projectenv.MODE_DEFAULT_CHAIN


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _basis_of(rcpt) -> dict[str, str | None] | None:
    """回执自报里的 helper 依据：本地模块 + 观察到的数据文件 → {项目相对路径: sha256|None}。

    依据不完整（观察器已截断，或条数超过 `MAX_BASIS`）→ None：存不全的依据不能冒充充分证据，调用方不记录、不复用。"""
    out: dict[str, str | None] = {}
    inputs = rcpt.inputs or {}
    if inputs.get("truncated"):
        return None
    for m in inputs.get("local_modules") or []:
        if isinstance(m, dict) and isinstance(m.get("path"), str):
            out[m["path"]] = m.get("sha256") if isinstance(m.get("sha256"), str) else None
    out.update(rcpt.observed_files())
    if len(out) > MAX_BASIS or any(v is None for v in out.values()):
        # 条数存不全，或有输入没算出摘要（过大）：核验只能看「还在不在」，内容原地改了发现不了——这样的依据不充分
        return None
    return dict(sorted(out.items()))


def build_record(project_root: str | Path, script: str, rcpt, *, run=None) -> dict | None:
    """成功执行的回执 → 启动上下文记录；证据不全（回执 partial / 非 safe / 没有 launcher 信息可判）→ None。

    `run` 是这次执行用的 `execspec.RunSelection`（None = 无参数）。"""
    if rcpt is None or rcpt.completeness != "complete" or rcpt.profile != "safe":
        return None
    spec_cwd = rcpt.spec_stable.get("cwd_mode")
    if not isinstance(spec_cwd, str):
        return None
    lau = rcpt.launcher if isinstance(getattr(rcpt, "launcher", None), dict) else None
    lau_ref = dict(lau) if lau else {"kind": launcher.KIND_DIRECT}
    # 解释器在 Conda 环境里时，无论这次是 direct 还是 conda 启动，都记环境版本：装 / 升降包之后「裸起成功过」的结论
    # 同样不再成立（激活后才能 import 的东西可能是新装的），要重新判断启动方式
    # 环境版本与“代”取**起会话那一刻**的值（回执里的 `session_env`），不取收口这一刻的：脚本跑的途中环境被改了，
    # 记录就该对不上现在的环境，而不是把改动之后的状态当成“跑成功时的样子”
    sess = rcpt.session_env if isinstance(getattr(rcpt, "session_env", None), dict) else {}
    env_prefix = launcher.conda_prefix_of(rcpt.interpreter)
    env_state = sess.get("env_state")
    if not isinstance(env_state, str):
        env_state = launcher.prefix_state(env_prefix) if env_prefix else ""
    generation = sess.get("generation")
    if generation is None:
        generation = projectenv.environment_generation(rcpt.interpreter)
    binding = rcpt.binding if isinstance(rcpt.binding, dict) else {}
    basis = _basis_of(rcpt)
    if basis is None:
        return None
    return {
        "version": RECORD_VERSION,
        "profile": PROFILE_SAFE,
        "recorded_at": time.time(),
        "interpreter": {
            "path": rcpt.interpreter,
            "source": rcpt.python_source,
            "generation": generation,
            # 当时项目有没有定过解释器（True = 项目记住 / 用户选的；False = 默认链条给的）：
            # 再次打开时 `python=None`（项目没有决定）只对得上后者
            "project_decided": _project_decided(project_root, rcpt.interpreter),
        },
        "launcher": lau_ref,
        "env_state": env_state,
        "cwd_mode": spec_cwd,
        "run_config": run.config_id if run is not None else None,
        "run_sensitive": bool(run.sensitive) if run is not None else False,
        "source": {"script_sha1": rcpt.source_revision, "basis": basis},
        "binding_revision": binding.get("revision") or "",
    }


def profile_of(record: dict | None) -> str | None:
    """记录里的执行语义：v2 取 `profile`，v1（只可能是 safe 回执）按 `safe`；没有记录 / 形状不对 = None。"""
    if not isinstance(record, dict):
        return None
    if record.get("version") == 1:
        return PROFILE_SAFE
    profile = record.get("profile")
    return profile if profile in PROFILES else None


def build_native_record(project_root: str | Path, script: str, plan, *, run=None) -> dict | None:
    """桌面 native 成功（V2 PR2）→ 启动上下文记录。native 没有 safe 那样的 helper 观察回执，所以依据取**计划那一刻**的事实：

    解释器 / 环境代 / 运行目录档 / 数据绑定都是 `plan` 的快照，脚本字节要与计划的 `script_revision` 仍一致（执行期间被改了就不记）。
    `source.basis` 为空且 `basis_observed: false`：native 没有观察 helper 模块与数据文件，核验只覆盖脚本本身——这条记录
    只用来回答「上次成功用哪种语义」，**不是**「数据与 helper 也没变」的证据（#982 的沿用入口读它）。"""
    if getattr(plan, "execution", None) != PROFILE_NATIVE or not plan.interpreter:
        return None
    root = Path(project_root)
    try:
        if scriptargs.source_revision(root / script) != plan.script_revision:
            return None
    except OSError:
        return None
    sha1 = _sha1(root / script)
    if not sha1:
        return None
    env = plan.environment if isinstance(plan.environment, dict) else {}
    generation = env.get("generation")
    if not isinstance(generation, str):
        generation = projectenv.environment_generation(plan.interpreter)
    lau = launcher.launcher_for_interpreter(plan.interpreter)
    lau_ref = lau.public() if lau is not None else {"kind": launcher.KIND_DIRECT}
    prefix = launcher.conda_prefix_of(plan.interpreter)
    decision = plan.workdir_decision if isinstance(plan.workdir_decision, dict) else {}
    binding = plan.binding if isinstance(plan.binding, dict) else {}
    return {
        "version": RECORD_VERSION,
        "profile": PROFILE_NATIVE,
        "recorded_at": time.time(),
        "interpreter": {
            "path": plan.interpreter,
            "source": str(env.get("source") or ""),
            "generation": generation,
            "project_decided": _project_decided(project_root, plan.interpreter),
        },
        "launcher": lau_ref,
        "env_state": launcher.prefix_state(prefix) if prefix else "",
        "cwd_mode": decision.get("mode") or workdir.mode_for(project_root),
        "run_config": run.config_id if run is not None else None,
        "run_sensitive": bool(run.sensitive) if run is not None else False,
        "source": {"script_sha1": sha1, "basis": {}, "basis_observed": False},
        "binding_revision": binding.get("revision") or "",
    }


@dataclasses.dataclass(frozen=True)
class NativeBasis:
    """native 成功的依据（计划快照，不是回执）：交给 `record_success` 在**同一个**成功事务里写 native 记录与运行默认值。"""

    plan: object


def basis_of(result, plan, partial: bool):
    """成功收口的启动记录依据：有图失败（partial）= None（清旧记录）；native 计划 = `NativeBasis`；其余取 safe 的回执。"""
    if partial:
        return None
    if getattr(plan, "execution", None) == PROFILE_NATIVE:
        return NativeBasis(plan)
    return getattr(result, "receipt", None)


def reusable_run_config(
    project_root: str | Path, script: str, *, python: str | None, python_source: str | None
) -> str | None:
    """「再次打开时沿用上次成功的运行配置」：上次成功用的运行配置引用（`rc_…`），**此刻全部依据仍成立**才回；否则 None。

    只回引用，不回 argv：调用方用 `runconfig.selection` 取（敏感值此刻不在也会在那里被拒）。拒绝的情形（都回 None，让用户重新给）：

    * 没有记录 / 记录读不出 / 上次是无参数运行（没有可沿用的）；
    * 敏感配置（`run_sensitive`）：不自动复用，秘密值本来也没落盘；
    * `source.basis_observed` 为 False（桌面 native 成功）：native 没有观察 helper 模块与数据文件，核验只覆盖脚本本身，
      凑不齐「解释器 / 环境 / 脚本 / helper / 数据」五样依据——证据不全就不假装可复用（见 `build_native_record`）；
    * `verify` 的全部依据（解释器、环境、脚本字节、helper 与数据文件摘要、运行目录档、运行配置、数据绑定）任何一处对不上。

    纯读：只 stat / 读文件算摘要；不执行用户代码。"""
    try:
        record = runconfig.launch_record(project_root, script)
    except runconfig.RunConfigError:
        return None
    if not isinstance(record, dict):
        return None
    cid = record.get("run_config")
    if not isinstance(cid, str) or not cid or record.get("run_sensitive"):
        return None
    if (record.get("source") or {}).get("basis_observed") is False:
        return None
    reason = verify(
        record,
        project_root=project_root,
        script=script,
        python=python,
        python_source=python_source,
    )
    return cid if reason is None else None


def record_success(
    project_root: str | Path, script: str, rcpt, *, run=None, update_default: bool = False
) -> bool:
    """成功收口调：算记录（哈希在事务外）→ `runconfig.commit_success` 一次落地。回「记录是否写入」。

    依据不全（`build_record` 回 None）也要提交：它**清掉**旧记录，不让上一次成功的 A 在这次成功的 B 之后继续冒充
    「最近成功」。`update_default=True` 时磁盘面板默认配置同一事务里一起更新（`run` 的引用；无参数 = 清掉）。"""
    if isinstance(rcpt, NativeBasis):
        record = build_native_record(project_root, script, rcpt.plan, run=run)
    else:
        record = build_record(project_root, script, rcpt, run=run)
    try:
        runconfig.commit_success(
            project_root,
            script,
            record,
            default_config_id=run.config_id if run is not None else None,
            update_default=update_default,
        )
    except runconfig.RunConfigError:
        if update_default and run is not None:
            raise  # 与此前 `set_default` 一致：带配置的成功遇到坏登记失败关闭
        return False
    return record is not None


def verify(
    record: dict | None,
    *,
    project_root: str | Path,
    script: str,
    python: str | None,
    python_source: str | None,
    facets: tuple[str, ...] = FACETS_ALL,
) -> str | None:
    """记录此刻还成立吗？成立 `None`；否则返回 `REASON_*`（第一处不符）。纯读：只 stat / 读文件算摘要。"""
    if not isinstance(record, dict):
        return REASON_NO_RECORD
    if record.get("version") not in READABLE_VERSIONS:
        return REASON_VERSION
    root = Path(project_root)
    interp = record.get("interpreter") or {}
    if FACET_INTERPRETER in facets:
        if not isinstance(interp.get("path"), str):
            return REASON_INTERPRETER
        if python is None:
            # 项目没有定过解释器（默认链条）：只对得上当时同样没定过的记录
            if interp.get("project_decided", True):
                return REASON_INTERPRETER
        elif _norm(interp["path"]) != _norm(python) or (
            python_source is not None and interp.get("source") != python_source
        ):  # python_source=None：预检探针不知道来源标签，只比路径
            return REASON_INTERPRETER
    if FACET_ENVIRONMENT in facets:
        env_python = python if python is not None else interp.get("path")
        if not isinstance(env_python, str) or interp.get(
            "generation"
        ) != projectenv.environment_generation(env_python):
            return REASON_ENVIRONMENT
        lau = record.get("launcher") or {}
        # prefix 从解释器现场推，记录里的只用来对账；记录里的路径不当可执行 / 探测对象
        prefix = launcher.conda_prefix_of(env_python)
        if lau.get("kind") == launcher.KIND_CONDA and (
            prefix is None or _norm(prefix) != _norm(str(lau.get("prefix") or ""))
        ):
            return REASON_LAUNCHER
        if prefix is not None and launcher.prefix_state(prefix) != (record.get("env_state") or ""):
            # Conda 环境（direct 与 conda 记录都算）：装 / 升降包后环境版本变了 = 重新核验
            return REASON_ENVIRONMENT
    if FACET_SOURCE in facets:
        src = record.get("source") or {}
        if _sha1(root / script) != src.get("script_sha1") or not src.get("script_sha1"):
            return REASON_SCRIPT
        for rel, want in (src.get("basis") or {}).items():
            real = projectenv.contained_path(root, root / rel) if isinstance(rel, str) else None
            if real is None or not Path(real).is_file():
                return REASON_HELPER
            if want is None:
                return REASON_HELPER  # 没有摘要 = 不可核验（新记录不会有；防御旧 / 手改的记录）
            try:
                got = figcapture.hash_file(real)[0]
            except OSError:
                return REASON_HELPER
            if got != want:
                return REASON_HELPER
    if FACET_CWD in facets and record.get("cwd_mode") != workdir.mode_for(project_root):
        return REASON_CWD
    if FACET_RUN_CONFIG in facets:
        cid = record.get("run_config")
        if cid is not None:
            if record.get("run_sensitive"):
                return REASON_RUN_CONFIG  # 敏感配置不自动复用：让用户重新给
            try:
                runconfig.get(project_root, cid, script=script)
            except runconfig.RunConfigError:
                return REASON_RUN_CONFIG
    if FACET_BINDING in facets:
        mode = record.get("cwd_mode") or workdir.mode_for(project_root)
        try:
            now = databinding.binding_for(root / script, root, mode).get("revision") or ""
        except Exception:  # noqa: BLE001 — 算不出 = 不能证明没变
            return REASON_BINDING
        if now != (record.get("binding_revision") or ""):
            return REASON_BINDING
    return None
