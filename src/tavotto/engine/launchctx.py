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

两个消费者，核验范围不同（`FACETS_*`）：

* `launcher_for`（`pool.EngineWorker` 起会话时）——只核解释器 / 环境 / 源码：这条解释器这份脚本上次成功用的启动方式，
  还成立就沿用（裸起成功过的不会被自动改成 Conda 包装），任何一项变了就重新检测；
* `reusable_run`（再次打开的入口 `app._session_target`）——全部核：成立才把上次成功的运行配置带回来，少让用户再给一次参数；
  不成立就当没有，**不静默沿用**。

纯标准库；Flask 父进程侧。
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from . import databinding, figcapture, launcher, projectenv, runconfig, workdir

RECORD_VERSION = 1

FACET_INTERPRETER = "interpreter"
FACET_ENVIRONMENT = "environment"
FACET_SOURCE = "source"
FACET_CWD = "cwd"
FACET_RUN_CONFIG = "run_config"
FACET_BINDING = "binding"
FACETS_LAUNCHER = (FACET_INTERPRETER, FACET_ENVIRONMENT, FACET_SOURCE)
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
    if len(out) > MAX_BASIS:
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
    env_state = ""
    if lau_ref.get("kind") == launcher.KIND_CONDA and isinstance(lau_ref.get("prefix"), str):
        env_state = launcher.prefix_state(lau_ref["prefix"])
    binding = rcpt.binding if isinstance(rcpt.binding, dict) else {}
    basis = _basis_of(rcpt)
    if basis is None:
        return None
    return {
        "version": RECORD_VERSION,
        "recorded_at": time.time(),
        "interpreter": {
            "path": rcpt.interpreter,
            "source": rcpt.python_source,
            "generation": projectenv.environment_generation(rcpt.interpreter),
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


def record_success(
    project_root: str | Path, script: str, rcpt, *, run=None, update_default: bool = False
) -> bool:
    """成功收口调：算记录（哈希在事务外）→ `runconfig.commit_success` 一次落地。回「记录是否写入」。

    依据不全（`build_record` 回 None）也要提交：它**清掉**旧记录，不让上一次成功的 A 在这次成功的 B 之后继续冒充
    「最近成功」。`update_default=True` 时磁盘面板默认配置同一事务里一起更新（`run` 的引用；无参数 = 清掉）。"""
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
    if record.get("version") != RECORD_VERSION:
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
        elif _norm(interp["path"]) != _norm(python) or interp.get("source") != python_source:
            return REASON_INTERPRETER
    if FACET_ENVIRONMENT in facets:
        env_python = python if python is not None else interp.get("path")
        if not isinstance(env_python, str) or interp.get(
            "generation"
        ) != projectenv.environment_generation(env_python):
            return REASON_ENVIRONMENT
        lau = record.get("launcher") or {}
        if lau.get("kind") == launcher.KIND_CONDA:
            # prefix 从解释器现场推，记录里的只用来对账；记录里的路径不当可执行 / 探测对象
            prefix = launcher.conda_prefix_of(env_python)
            if prefix is None or _norm(prefix) != _norm(str(lau.get("prefix") or "")):
                return REASON_LAUNCHER
            if launcher.prefix_state(prefix) != record.get("env_state"):
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
                continue  # 当时没算出摘要（过大）：只核仍在
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


def _record_of(project_root: str | Path, script: str) -> dict | None:
    try:
        return runconfig.launch_record(project_root, script)
    except runconfig.RunConfigError:
        return None


def launcher_for(
    project_root: str | Path, script: str, python: str, python_source: str, *, bundled: str
) -> launcher.CondaLauncher | None:
    """`pool.EngineWorker` 起会话时用的启动方式：None = 裸起。

    内置 runtime（`python_source == bundled`）永远裸起。这条解释器这份脚本上次成功的启动方式还成立（解释器 / 环境 /
    源码三项核验通过）就沿用——裸起成功过的不会被改成 Conda 包装；否则现场检测（Conda 环境 + 找得到 conda → 包装）。"""
    if python_source == bundled:
        return None
    record = _record_of(project_root, script)
    if (
        record is not None
        and verify(
            record,
            project_root=project_root,
            script=script,
            python=python,
            python_source=python_source,
            facets=FACETS_LAUNCHER,
        )
        is None
    ):
        lau = record.get("launcher") or {}
        if lau.get("kind") == launcher.KIND_DIRECT:
            return None
    # 记录里的 `conda_exe` 来自项目目录里的登记文件，**不拿它当可执行路径**（克隆来的项目
    # 可以写任意路径）：要执行的 conda 永远从这条解释器现场检测出来。
    return launcher.detect(python)


def reusable_run(
    project_root: str | Path, script: str, *, python: str | None, python_source: str | None
):
    """再次打开的入口：上次成功的运行配置（`RunSelection`）此刻能不能直接带回来。

    全部核验通过才返回；没有记录 / 任一项变了 / 配置是敏感的 / 记录是无参数运行 → None（调用方照旧：
    请求没给参数就是无参数，要参数让用户给）。**不静默沿用不再成立的配置。**"""
    record = _record_of(project_root, script)
    if record is None or not record.get("run_config"):
        return None
    if (
        verify(
            record,
            project_root=project_root,
            script=script,
            python=python,
            python_source=python_source,
        )
        is not None
    ):
        return None
    try:
        return runconfig.selection(project_root, record["run_config"], script=script)
    except runconfig.RunConfigError:
        return None
