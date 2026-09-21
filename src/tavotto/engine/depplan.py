"""联合依赖计划（统一实施包 U04，ADR 0061）：把「项目声明了什么」「脚本开跑要什么」
「目标环境里有什么」三件事合成**一份**可读、可绑定、可执行的计划——**不装任何东西**。

三个输入各有一个权威：

* 声明 —— `depresolve.declared_intents()`（无损；unknown / unsupported 不是空）；
* 需要 —— `importscan.scan()`（脚本 + 本地模块的无条件第三方 import）；
* 现状 —— `target_facts()`：在**目标解释器里**量出来的 marker 环境（PEP 508 的
  `python_version` / `sys_platform` / …）、标准库名字表、已装 distribution 的版本。
  marker 必须按目标环境求值：脚本会在**那个**解释器里跑，Flask 进程自己的
  `sys.platform` 与它无关（判据的主语）。

输出 `JointPlan`：三种状态之一——

    nothing_needed   脚本需要的第三方包目标环境里都有（或脚本不需要第三方包）
    ready            有缺的、且能给出一份**完整已知**的安装集合（要什么 / 约束什么 / 从哪来）
    blocked          不能给出完整已知的集合：选中的声明里有 unsupported / unknown 的行、
                     声明之间确定矛盾、hash 模式下有条目没 hash、目标环境量不出来——
                     **明确停下**，不把 `^` / marker / 约束剥掉偷偷继续（FO-029 / FO-033）；
                     哪怕此刻什么都不缺也是 blocked（`missing` 为空只说明不用装）

`requirements` 是要装的（脚本需要且目标缺的那些，按项目声明的完整形态：extras / specifier
/ hash），`constraints` 是**所有**选中声明的约束 + constraints 文件——不需要的包不装，但它们
的版本约束照样管住求解（pip `-c` 的语义）。受管环境额外并入 adapter 约束（worker 侧需要的
matplotlib / numpy，`ADAPTER_REQUIREMENTS`，与 `pyproject.toml` 的 `worker` extra 同源）；
用户自己的 venv **不**并入——那会让 pip 为了满足我们的区间去动用户已装的科学栈
（不默认升级 / 降级用户包，FO-038）。

只有**模块层无条件**的 import 进 `needed`（`importscan` 的判据）；条件 / 延后 / 可选 / 动态
的只列在 `possible`——缺了在运行后由有界重计划接手。unknown 的 import（映射不到
distribution）列在 `unknown`，**永远不装、不猜**（FO-034）。

纯标准库 + `packaging`（经 `depresolve`）；`target_facts` 起一个子进程（按解释器缓存）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from . import config, depresolve, importscan, projectenv, runtime

LOG = logging.getLogger("tavotto.depplan")

PLAN_VERSION = 1

STATUS_NOTHING_NEEDED = "nothing_needed"
STATUS_READY = "ready"
STATUS_BLOCKED = "blocked"
STATUSES = (STATUS_NOTHING_NEEDED, STATUS_READY, STATUS_BLOCKED)

#: `blocked` 的原因——闭集；每一条都有用户能做的下一步（细则里写）。
BLOCK_UNSUPPORTED = "dependency_declaration_unsupported"
BLOCK_CONFLICT = "dependency_conflict"
BLOCK_HASHES_INCOMPLETE = "dependency_hashes_incomplete"
BLOCK_TARGET_UNAVAILABLE = "dependency_target_unavailable"
BLOCK_REASONS = (
    BLOCK_UNSUPPORTED,
    BLOCK_CONFLICT,
    BLOCK_HASHES_INCOMPLETE,
    BLOCK_TARGET_UNAVAILABLE,
)

#: worker 侧需要的科学栈（scientific adapter 约束）。**与 `pyproject.toml` 的
#: `[project.optional-dependencies].worker` 逐字相同**——`tests/test_dependency_plan.py`
#: 钉着这一对。受管环境按它建；用户 venv 只量不改。
ADAPTER_REQUIREMENTS = ("matplotlib>=3.8,<3.12", "numpy>=1.24,<3")

TARGET_PROJECT_VENV = "project_venv"
TARGET_MANAGED = "tavotto_managed"
TARGETS = (TARGET_PROJECT_VENV, TARGET_MANAGED)

FACTS_TIMEOUT_S = 60

#: 项目设置里记「选了哪些依赖组」的键（`config.project_settings(root)[SETTINGS_KEY]`）。
SETTINGS_KEY = "dependency_groups"

# ---------------------------------------------------------------- 目标环境事实

#: 在目标解释器里跑：PEP 508 的 marker 环境（与 `packaging.markers.default_environment()`
#: 同一套字段、同一套取法——那份实现就是这几行，这里不 import packaging：目标解释器不
#: 一定有它）、标准库名字表、已装 distribution。输出单行 JSON。**只读**。
_FACTS_SRC = r"""
import json, os, platform, sys
import importlib.metadata as m


def fmt(info):
    version = "{0.major}.{0.minor}.{0.micro}".format(info)
    kind = info.releaselevel
    if kind != "final":
        version += kind[0] + str(info.serial)
    return version


env = {
    "implementation_name": sys.implementation.name,
    "implementation_version": fmt(sys.implementation.version),
    "os_name": os.name,
    "platform_machine": platform.machine(),
    "platform_release": platform.release(),
    "platform_system": platform.system(),
    "platform_version": platform.version(),
    "python_full_version": platform.python_version(),
    "platform_python_implementation": platform.python_implementation(),
    "python_version": ".".join(platform.python_version_tuple()[:2]),
    "sys_platform": sys.platform,
}
installed = {}
for d in m.distributions():
    name = d.metadata.get("Name") or ""
    if name:
        key = "".join(ch if ch.isalnum() else "-" for ch in name.lower())
        while "--" in key:
            key = key.replace("--", "-")
        installed[key] = d.version or ""
sys.stdout.write(json.dumps({
    "marker_env": env,
    "stdlib": sorted(getattr(sys, "stdlib_module_names", ())),
    "installed": installed,
    "prefix": sys.prefix,
    "executable": sys.executable,
}))
"""


@dataclasses.dataclass(frozen=True)
class TargetFacts:
    """目标解释器里量出来的事实。`installed` 的键是 PEP 503 规范化名。"""

    python: str
    marker_env: dict
    stdlib: frozenset[str]
    installed: dict
    prefix: str = ""
    executable: str = ""

    @property
    def python_version(self) -> str:
        return str(self.marker_env.get("python_full_version", ""))

    def digest(self) -> str:
        """marker 环境 + 已装集合的指纹（计划过期判据的一部分）。"""
        text = json.dumps({"env": self.marker_env, "installed": self.installed}, sort_keys=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def to_payload(self) -> dict:
        return {
            "python_version": self.python_version,
            "sys_platform": self.marker_env.get("sys_platform", ""),
            "platform_machine": self.marker_env.get("platform_machine", ""),
            "installed_count": len(self.installed),
            "digest": self.digest(),
        }


_facts_lock = threading.Lock()
_facts_cache: dict[str, TargetFacts] = {}


def target_facts(python: str, *, use_cache: bool = True) -> TargetFacts | None:
    """在目标解释器里量一次事实；起不来回 None（调用方据此 `blocked`）。

    按解释器路径缓存：同一进程里同一个环境反复问是常态（每次准备 / 每次渲染前的门）。
    环境被改动（装完包 / 换代）时调 `reset_cache(python)`——`deprepair` 在事务结束时做。
    """
    key = _key(python)
    if use_cache:
        with _facts_lock:
            hit = _facts_cache.get(key)
        if hit is not None:
            return hit
    # **启动条件与 worker 对齐**（`projectenv.probe_environment` 同一条纪律）：不带 `-I`、
    # env 原样继承——`pip install --user` 装的包 worker 看得见，事实表就得看得见；cwd 换成
    # 空目录挡住父进程 cwd 进 `sys.path[0]`。
    scratch = ""
    try:
        scratch = projectenv._probe_scratch_dir()
        proc = subprocess.run(
            [str(python), "-c", _FACTS_SRC],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=FACTS_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            cwd=scratch,
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.warning("目标环境事实探测失败: %s: %s", python, exc)
        return None
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
    if proc.returncode != 0:
        LOG.warning("目标环境事实探测退出 %s: %s", proc.returncode, (proc.stderr or "")[-400:])
        return None
    try:
        data = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("marker_env"), dict):
        return None
    facts = TargetFacts(
        python=str(python),
        marker_env=dict(data["marker_env"]),
        stdlib=frozenset(str(n) for n in data.get("stdlib") or ()),
        installed={str(k): str(v) for k, v in (data.get("installed") or {}).items()},
        prefix=str(data.get("prefix", "")),
        executable=str(data.get("executable", "")),
    )
    with _facts_lock:
        _facts_cache[key] = facts
    return facts


def _key(python: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(python))))


def reset_cache(python: str | None = None) -> None:
    with _facts_lock:
        if python is None:
            _facts_cache.clear()
        else:
            _facts_cache.pop(_key(python), None)


# ---------------------------------------------------------------- 选择


@dataclasses.dataclass(frozen=True)
class Selection:
    """按组与 marker 选出来的声明。"""

    requirements: tuple[depresolve.DependencyIntent, ...]
    constraints: tuple[depresolve.DependencyIntent, ...]
    skipped_marker: tuple[depresolve.DependencyIntent, ...]
    unselected_groups: tuple[str, ...]
    selected_groups: tuple[str, ...]
    available_groups: tuple[str, ...]
    unsupported: tuple[depresolve.DependencyIntent, ...]
    conflicts: tuple[dict, ...]

    def to_payload(self) -> dict:
        return {
            "selected_groups": list(self.selected_groups),
            "available_groups": list(self.available_groups),
            "unselected_groups": list(self.unselected_groups),
            "requirements": [i.to_payload() for i in self.requirements],
            "constraints": [i.to_payload() for i in self.constraints],
            "skipped_marker": [i.to_payload() for i in self.skipped_marker],
            "unsupported": [i.to_payload() for i in self.unsupported],
            "conflicts": [dict(c) for c in self.conflicts],
        }


def _is_constraint_source(group: str) -> bool:
    return bool(group) and ":" not in group and Path(group).name == depresolve.CONSTRAINTS_NAME


def selected_groups_setting(root: str | Path) -> list[str]:
    """用户为这个项目点名的组（项目设置）；没设过回空列表（= 只有默认组）。"""
    raw = (config.project_settings(str(Path(root))) or {}).get(SETTINGS_KEY)
    if not isinstance(raw, list):
        return []
    return [str(g) for g in raw if isinstance(g, str) and g]


def select(
    intents: list[depresolve.DependencyIntent],
    *,
    marker_env: dict | None,
    groups: list[str] | None = None,
) -> Selection:
    """按组（默认组 + 用户点名的）与 marker（按目标环境求值）选出要参与求解的声明。

    * constraint 类的声明**不分组**：约束永远生效（pip `-c` 的语义）；
    * marker 为假的 requirement 进 `skipped_marker`（不装、也不算缺）；marker 求不出（目标
      事实拿不到时 `marker_env=None`）按**选中**处理——宁可多列一条让用户看见；
    * 选中组里的 unsupported / unknown 行、以及所有 constraint 来源里的这类行进
      `unsupported`（它们让「完整已知」不成立）；未选中组里的不算——那些组本来就不装。
    """
    _requirements, markers, _specifiers, _utils, _version = depresolve._pkg()
    chosen = set(groups or ())
    available = sorted({i.group for i in intents if i.group})
    selected = tuple(g for g in available if depresolve.default_group(g) or g in chosen)
    unselected = tuple(g for g in available if g not in selected)
    reqs: list = []
    cons: list = []
    skipped: list = []
    unsupported: list = []
    for it in intents:
        # 约束不分组；约束**来源**里认不出的行（`constraints.txt` 里一条 `git+…`）同样
        # 永远在范围内——它们的 kind 是 unsupported，只能按来源判。
        in_scope = (
            it.kind == depresolve.INTENT_KIND_CONSTRAINT
            or it.group in selected
            or _is_constraint_source(it.group)
        )
        if not in_scope:
            continue
        if not it.declared:
            unsupported.append(it)
            continue
        if it.marker and marker_env is not None:
            try:
                if not markers.Marker(it.marker).evaluate(dict(marker_env)):
                    skipped.append(it)
                    continue
            except (markers.InvalidMarker, markers.UndefinedEnvironmentName) as exc:
                LOG.debug("marker 求值失败，按选中处理: %s: %s", it.raw, exc)
        (cons if it.kind == depresolve.INTENT_KIND_CONSTRAINT else reqs).append(it)
    return Selection(
        requirements=tuple(reqs),
        constraints=tuple(cons),
        skipped_marker=tuple(skipped),
        unselected_groups=unselected,
        selected_groups=selected,
        available_groups=tuple(available),
        unsupported=tuple(unsupported),
        conflicts=tuple(depresolve.conflicts(reqs + cons)),
    )


# ---------------------------------------------------------------- 计划


@dataclasses.dataclass(frozen=True)
class JointPlan:
    """一份联合依赖计划（不可变；执行由 `deprepair` 按它做）。"""

    status: str
    target_kind: str
    script: str
    needed: tuple[dict, ...]  # importscan 的 needed 分类（第三方 + 无条件）
    missing: tuple[dict, ...]  # needed 里目标环境没有的
    satisfied: tuple[dict, ...]  # needed 里目标环境有的（含版本是否满足声明）
    unknown: tuple[str, ...]  # 无条件 import 却映射不到 distribution 的名字
    possible: tuple[dict, ...]  # 条件 / 延后 / 可选 / 动态的第三方 import（不在跑前装）
    requirements: tuple[str, ...]  # 交给安装器的需求（规范串）
    constraints: tuple[str, ...]  # 交给安装器的约束（规范串）
    hashes: dict  # {规范串: [hash, …]}（hash 模式才有）
    require_hashes: bool
    adapter: tuple[str, ...]
    blocked: tuple[dict, ...]
    selection: dict
    scan: dict
    facts: dict
    marker_env_digest: str
    identity: str

    @property
    def actionable(self) -> bool:
        return self.status == STATUS_READY

    def to_payload(self) -> dict:
        return {
            "plan_version": PLAN_VERSION,
            "status": self.status,
            "target_kind": self.target_kind,
            "script": self.script,
            "needed": [dict(n) for n in self.needed],
            "missing": [dict(m) for m in self.missing],
            "satisfied": [dict(s) for s in self.satisfied],
            "unknown": list(self.unknown),
            "possible": [dict(p) for p in self.possible],
            "requirements": list(self.requirements),
            "constraints": list(self.constraints),
            "hashes": {k: list(v) for k, v in self.hashes.items()},
            "require_hashes": self.require_hashes,
            "adapter": list(self.adapter),
            "blocked": [dict(b) for b in self.blocked],
            "selection": dict(self.selection),
            "scan": dict(self.scan),
            "facts": dict(self.facts),
            "marker_env_digest": self.marker_env_digest,
            "identity": self.identity,
        }


def plan(
    root: str | Path,
    script: str,
    *,
    facts: TargetFacts | None,
    target_kind: str,
    groups: list[str] | None = None,
    intents: list[depresolve.DependencyIntent] | None = None,
) -> JointPlan:
    """拼一份联合计划。`facts` 是**当前选中的**解释器的事实（缺什么按它量）；`target_kind`
    说安装会落到哪种环境（受管环境并入 adapter 约束，用户 venv 不并入）。"""
    if target_kind not in TARGETS:
        raise ValueError(f"target_kind 非法: {target_kind!r}")
    root_p = Path(root)
    intents = depresolve.declared_intents(root_p, script) if intents is None else list(intents)
    marker_env = facts.marker_env if facts is not None else None
    selection = select(intents, marker_env=marker_env, groups=groups)
    declared = {}
    for it in selection.requirements:
        declared.setdefault(it.name, it.specifier)
    scan = importscan.scan(
        root_p, script, declared=declared, stdlib=facts.stdlib if facts is not None else None
    )
    installed = facts.installed if facts is not None else {}
    _requirements, _markers, specifiers, _utils, _version = depresolve._pkg()

    needed = tuple(c.to_payload() for c in scan.needed)
    unknown = tuple(
        c.module
        for c in scan.classes
        if c.bucket == importscan.BUCKET_UNKNOWN and c.context == importscan.CONTEXT_UNCONDITIONAL
    )
    possible = tuple(
        c.to_payload()
        for c in scan.classes
        if c.bucket in (importscan.BUCKET_THIRD_PARTY, importscan.BUCKET_UNKNOWN)
        and c.context != importscan.CONTEXT_UNCONDITIONAL
    )
    by_name: dict[str, list[depresolve.DependencyIntent]] = {}
    for it in selection.requirements:
        by_name.setdefault(it.name, []).append(it)

    missing: list[dict] = []
    satisfied: list[dict] = []
    for c in scan.needed:
        dist = c.distribution
        declared_for = by_name.get(dist, [])
        entry = {
            "import_name": c.module,
            "distribution": dist,
            "resolution_source": c.resolution_source,
            "declared": bool(declared_for),
            "specifiers": sorted({it.specifier for it in declared_for if it.specifier}),
            "via": list(c.via),
        }
        version = installed.get(dist)
        if version is None:
            missing.append(entry)
            continue
        matches = True
        for it in declared_for:
            if it.specifier:
                try:
                    matches = matches and specifiers.SpecifierSet(it.specifier).contains(
                        version, prereleases=True
                    )
                except (specifiers.InvalidSpecifier, ValueError):
                    matches = False
        satisfied.append({**entry, "installed_version": version, "matches_declared": matches})

    # ---- 交给安装器的集合 ------------------------------------------------------
    missing_names = {m["distribution"] for m in missing}
    hash_mode = any(it.hashes for it in selection.requirements)
    reqs: list[str] = []
    hashes: dict[str, list[str]] = {}
    cons: list[str] = []
    blocked: list[dict] = []
    if hash_mode:
        # hash 模式 = 锁文件语义：整份选中集合就是闭包，全部按 hash 装（pip 要求每一条
        # 都有 hash，缺一条整次拒绝——那是它的判据，这里提前说出来）。
        without = [it for it in selection.requirements if not it.hashes]
        if without:
            blocked.append(
                {
                    "code": BLOCK_HASHES_INCOMPLETE,
                    "lines": [it.raw for it in without][:20],
                    "count": len(without),
                }
            )
        for it in selection.requirements:
            text = depresolve.requirement_string(it)
            if text not in reqs:
                reqs.append(text)
            hashes.setdefault(text, [])
            for h in it.hashes:
                if h not in hashes[text]:
                    hashes[text].append(h)
    else:
        for name in sorted(missing_names):
            for it in by_name.get(name, ()):
                text = depresolve.requirement_string(it)
                if text not in reqs:
                    reqs.append(text)
            if not by_name.get(name):
                # 没有项目声明、只有 curated 映射：裸名（不钉版本——我们不替项目决定版本）
                if name not in reqs:
                    reqs.append(name)
    for it in selection.requirements:
        if hash_mode or it.name in missing_names:
            continue  # 要装的不再当约束（hash 模式下全部都是要装的）
        if it.specifier:
            text = f"{it.name}{it.specifier}"
            if text not in cons:
                cons.append(text)
    for it in selection.constraints:
        if it.specifier:
            text = f"{it.name}{it.specifier}"
            if text not in cons:
                cons.append(text)
    adapter = ADAPTER_REQUIREMENTS if target_kind == TARGET_MANAGED else ()

    # ---- 状态 ------------------------------------------------------------------
    if facts is None:
        blocked.append({"code": BLOCK_TARGET_UNAVAILABLE})
    if selection.unsupported:
        blocked.append(
            {
                "code": BLOCK_UNSUPPORTED,
                "declarations": [it.to_payload() for it in selection.unsupported][:40],
                "count": len(selection.unsupported),
            }
        )
    if selection.conflicts:
        blocked.append(
            {"code": BLOCK_CONFLICT, "conflicts": [dict(c) for c in selection.conflicts]}
        )
    # blocked 优先于「没缺的」：选中的声明里有 unsupported / 矛盾 / 缺 hash / 目标量不出，这份
    # 计划就不能自称完整——哪怕此刻什么都不缺（Codex #459 P2）。要不要装看 `missing`。
    if blocked:
        status = STATUS_BLOCKED
    elif missing:
        status = STATUS_READY
    else:
        status = STATUS_NOTHING_NEEDED
    facts_payload = facts.to_payload() if facts is not None else {}
    identity = _identity(
        target_kind=target_kind,
        requirements=reqs,
        constraints=cons,
        adapter=adapter,
        hashes=hashes,
        marker_env=marker_env or {},
    )
    return JointPlan(
        status=status,
        target_kind=target_kind,
        script=Path(script).as_posix(),
        needed=needed,
        missing=tuple(missing),
        satisfied=tuple(satisfied),
        unknown=unknown,
        possible=possible,
        requirements=tuple(reqs),
        constraints=tuple(cons),
        hashes={k: tuple(v) for k, v in hashes.items()},
        require_hashes=hash_mode,
        adapter=adapter,
        blocked=tuple(blocked),
        selection=selection.to_payload(),
        scan=scan.to_payload(),
        facts=facts_payload,
        marker_env_digest=_digest(marker_env or {}),
        identity=identity,
    )


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _identity(
    *,
    target_kind: str,
    requirements: list[str],
    constraints: list[str],
    adapter: tuple[str, ...],
    hashes: dict,
    marker_env: dict,
) -> str:
    """计划的公开身份：只由**意图**决定（要装什么 / 约束什么 / 目标类型 / 目标 Python 的
    版本与平台），不含任何机器路径（04 §3：公开身份不含路径）。同一份意图在同一种目标上
    永远同一个身份——受管环境的代目录按它命名。"""
    env_part = {
        k: marker_env.get(k, "")
        for k in ("python_version", "sys_platform", "platform_machine", "implementation_name")
    }
    return _digest(
        {
            "target": target_kind,
            "requirements": sorted(requirements),
            "constraints": sorted(constraints),
            "adapter": list(adapter),
            "hashes": {k: sorted(v) for k, v in hashes.items()},
            "env": env_part,
        }
    )
