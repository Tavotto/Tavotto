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

**来源状态（Import Origin Resolver PR4，ADR 0061 / 0114 / 0115）**：`importscan` 给每个顶级 import 的来源与（可选的）
已装发行包证据，这里消费它们，**只收紧「装它就好」这一句，不放宽任何安装门**：

* `missing` 只剩「确实该装、且有可信安装名」的：多发行包提供同一个 import（`cv2`）而其中一个已经装着、editable /
  本地路径 / 本地 wheel / VCS / URL / Conda 专有的已装提供者、包在但导入失败（PR3 覆盖度缓存的 `import_error`）——
  这些**不进 `missing`、不转成 pip 包名、不进 `requirements`**，改进 `unknown`（名字，保持 `_plan_imports` /
  依赖门 / 覆盖度检查对它们照旧看 import 得到与否），详情进 `origins`（来源状态、候选发行包名、原因码）；
* 不新增 `blocked` 理由（`BLOCK_REASONS` 闭集不动）：这些情形不让计划不完整，只是「装不了 / 不该猜」的那一个 import；
* 已装判据按**目标事实**（`installed`），来源状态按 `static_index`（静态读目标环境 site-packages 元数据，不起进程）；
  两者都量不到时退回旧口径（按 curated / 声明的名字问 `installed`）。

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
from collections.abc import Callable
from pathlib import Path

from . import config, depresolve, distmeta, importscan, projectenv, runtime

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
#: 受管环境是项目级的、账上有别的作用域（脚本所在目录）装进去的版本，本次要装的与它们互斥——不硬合并
#: （T06 / D04）。这一条不是 `depplan.plan` 产出的（它只看声明与目标事实），由 `deprepair` 对着账加上；
#: 用户的下一步：换成这个作用域（`scope_policy=switch`，新一代、旧代留着直到没人用）或把子目录当独立项目。
BLOCK_SCOPE_CONFLICT = "dependency_scope_conflict"
BLOCK_REASONS = (
    BLOCK_UNSUPPORTED,
    BLOCK_CONFLICT,
    BLOCK_HASHES_INCOMPLETE,
    BLOCK_TARGET_UNAVAILABLE,
    BLOCK_SCOPE_CONFLICT,
)

#: `JointPlan.origins[].reason`：这个 import 为什么没进 `missing`、没有安装名（闭集；词汇复用 `distmeta.STATUSES` 与 PR3 的
#: 覆盖度 detail 码，不是发布的错误码、不是 `blocked` 理由）。前端镜像在 `web/src/lib/api.ts` 的 `JointOriginReason`，
#: 登记在 `docs/rules/repo/same-origin-pairs.md`。
ORIGIN_UNRESOLVED = "distribution_unresolved"  # 映射不到任何发行包（永远不猜，FO-034）
ORIGIN_AMBIGUOUS = distmeta.ST_AMBIGUOUS  # 多个发行包都能提供它 / 已装着其中之一以上
ORIGIN_EDITABLE = distmeta.ST_EDITABLE
ORIGIN_NOT_REPRODUCIBLE = distmeta.ST_NOT_REPRODUCIBLE  # 本地路径 / 本地 wheel / VCS / URL
ORIGIN_CONDA = distmeta.ST_CONDA
ORIGIN_UNVERIFIED = distmeta.ST_UNVERIFIED
#: 包在、导入失败（PR3 覆盖度缓存的 `import_error`）：装它救不了。与 `envadvice.DETAIL_IMPORT_FAILED_TARGET` 逐字相同
#: （`tests/test_dependency_plan.py` 钉着这一对）；这里不 import `envadvice`（它在依赖图的更上层）。
ORIGIN_IMPORT_FAILED = "module_import_failed_in_target_environment"
ORIGIN_REASONS = (
    ORIGIN_UNRESOLVED,
    ORIGIN_AMBIGUOUS,
    ORIGIN_EDITABLE,
    ORIGIN_NOT_REPRODUCIBLE,
    ORIGIN_CONDA,
    ORIGIN_UNVERIFIED,
    ORIGIN_IMPORT_FAILED,
)
#: 一个 origins 条目最多列几个候选发行包名（不透明的名字，没有路径）
MAX_ORIGIN_CANDIDATES = 8

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
import json, os, platform, site, sys
import importlib.machinery
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
# 目标解释器**看得见**的全部 site 目录（sys.path 顺序）：prefix 内的、include-system-site-packages 带进来的基础解释器层、
# 启用的 user site、sys.path 上其它叫 site-packages / dist-packages 的存在的目录。只是路径列表——静态索引按它读元数据。
known = set()
try:
    known.update(os.path.normcase(os.path.abspath(p)) for p in site.getsitepackages())
except Exception:
    pass
try:
    if site.ENABLE_USER_SITE:
        known.add(os.path.normcase(os.path.abspath(site.getusersitepackages())))
except Exception:
    pass
site_roots = []
for entry in sys.path:
    if not entry or not isinstance(entry, str):
        continue
    try:
        path = os.path.abspath(entry)
        if not os.path.isdir(path):
            continue
    except Exception:
        continue
    if os.path.normcase(path) in known or os.path.basename(path).lower() in (
        "site-packages",
        "dist-packages",
    ):
        if path not in site_roots:
            site_roots.append(path)
sys.stdout.write(json.dumps({
    "marker_env": env,
    "stdlib": sorted(getattr(sys, "stdlib_module_names", ())),
    "builtin": sorted(sys.builtin_module_names),
    "ext_suffixes": list(importlib.machinery.EXTENSION_SUFFIXES),
    "installed": installed,
    "prefix": sys.prefix,
    "executable": sys.executable,
    "site_roots": site_roots,
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
    #: 目标解释器的 `sys.builtin_module_names` / `importlib.machinery.EXTENSION_SUFFIXES`（#888）。**空 = 没量到**
    #: （替身事实、老缓存）——`importscan` 据此退回「不确定」的保守口径，不拿宿主的冒充。不进 `digest()` / `to_payload()`：
    #: 它们由解释器构建决定，不是计划的输入，也就不改 `inputs_digest` / `identity`。
    builtin: frozenset[str] = frozenset()
    ext_suffixes: tuple[str, ...] = ()
    #: 目标解释器**看得见**的全部 site 目录（`sys.path` 顺序：prefix 内的、`include-system-site-packages` 的基础解释器层、
    #: 启用的 user site、其它 site-packages / dist-packages）。静态索引（`static_index`）按它读元数据，否则别的层里的替代提供者
    #: 会被漏掉（Codex #920 P1）。**空 = 没量到**（替身事实、老缓存），索引退回只读前缀的旧口径。与 `builtin` 同理不进
    #: `digest()` / `to_payload()`：它们由解释器与环境决定，不是计划的输入，也就不改 `inputs_digest` / `identity`。
    site_roots: tuple[str, ...] = ()

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
    # 空目录挡住父进程 cwd 进 `sys.path[0]`；`-B`（`runtime.probe_args`）不往目标解释器写 .pyc；
    # 目标是 Tavotto 自己的环境时带 `runtime.owned_env`（启动路径上的 sitecustomize 若 import matplotlib，
    # 缓存落回数据目录，与真正的 worker 同一份环境）。
    scratch = ""
    try:
        scratch = projectenv._probe_scratch_dir()
        proc = subprocess.run(
            [str(python), *runtime.probe_args(), "-c", _FACTS_SRC],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=FACTS_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            cwd=scratch,
            env=runtime.owned_env(python),
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
        builtin=frozenset(str(n) for n in data.get("builtin") or ()),
        ext_suffixes=tuple(str(x) for x in data.get("ext_suffixes") or ()),
        site_roots=tuple(str(x) for x in data.get("site_roots") or () if isinstance(x, str)),
    )
    with _facts_lock:
        _facts_cache[key] = facts
        _index_cache.pop(key, None)  # 重新量了事实 = 环境可能变了：旧的元数据索引一起作废
    static_index(facts)  # 与事实同一刻读（理由见 `_index_cache`）
    return facts


def _key(python: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(python))))


def reset_cache(python: str | None = None) -> None:
    with _facts_lock:
        if python is None:
            _facts_cache.clear()
            _index_cache.clear()
        else:
            _facts_cache.pop(_key(python), None)
            _index_cache.pop(_key(python), None)


# ---------------------------------------------------------------- 目标环境的静态发行包索引（不起进程）

#: 解释器键 → 目标环境 site-packages 的元数据索引。**与 `_facts_cache` 同一刻读、同一个 `reset_cache` 清**：事实说
#: 「装着什么」、索引说「谁提供什么模块」，两者必须描述同一个时间点的同一个环境（用户 venv 里装上了但验证没过的包不会
#: `reset_cache`——事实停在装之前，索引若晚一步读就会一边说没装、一边说装着）。
_index_cache: dict[str, distmeta.Index] = {}


def static_index(facts: TargetFacts | None) -> distmeta.Index | None:
    """目标解释器所在环境的**静态**发行包索引（`distmeta`：只读 site-packages 里的文本元数据，不 import、不起子进程）。

    回 None = 没有可读的环境（没有事实 / 没有前缀——私有 Python 的替身与新一代的「装之前」；前缀下找不到
    site-packages）。读不全（预算、读不动的目录）的索引照样回，`Index.complete=False`，下游据此不把「没查到」当「没装」。
    层 = 目标解释器报告的全部 site 目录（`TargetFacts.site_roots`）；其中任何一层没读成，索引 `complete=False`。
    不带项目根：目标前缀是用户已经授权运行的解释器所在的环境（`target_facts` 刚在里面跑过 Python），读它的元数据
    不增加任何暴露面；默认扫描不走这里（它自己带项目根调 `distmeta`）。"""
    if facts is None or not facts.prefix:
        return None
    key = _key(facts.python or facts.prefix)
    with _facts_lock:
        hit = _index_cache.get(key)
    if hit is not None:
        return hit
    # 读目标解释器报告的**全部**层（含基础解释器 / user site）；没量到层（替身事实）才退回只读前缀的旧口径。任何一层没读成
    # → `Index.complete=False`，下游据此不把「没查到」当「没装」（见 `plan` 的 `index_incomplete`）。
    index = distmeta.index_environment(facts.prefix, site_paths=facts.site_roots or None)
    if not index.checked:
        return None
    with _facts_lock:
        _index_cache[key] = index
    return index


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
    #: 规划输入的指纹：声明意图（各声明文件解析出的全部条目，稳定顺序）+ 脚本与跟进过的本地模块的字节。
    #: 与事实无关（替身事实与真事实算出来一样）——执行端据此判「用户看到的计划还是不是这些输入算的」。
    inputs_digest: str = ""
    #: 脚本 import 了、绑定却从未被读的名字（`importscan` 的 `unused`）：不进 needed / unknown，
    #: 只列出来；缺的话 worker 给那一行占位（ADR 0061 §二 2026-09-24 修订）。
    unused: tuple[str, ...] = ()
    #: `unknown` 里每个名字的来源状态（PR4）：`{import_name, bucket, distribution, reason, resolution_status,
    #: distribution_status, origin_kind, provenance, candidates, coverage}`——`reason` ∈ `ORIGIN_REASONS`；
    #: `candidates` 是不透明的发行包名（≤ `MAX_ORIGIN_CANDIDATES`），不带路径与文件内容。`unknown` 的名字集合恒等于
    #: 这里的 `import_name` 集合。可选字段：老客户端忽略，不改 `missing` / `requirements` 的含义，所以 `plan_version` 不升。
    origins: tuple[dict, ...] = ()

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
            "unused": list(self.unused),
            "origins": [dict(o) for o in self.origins],
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
            "inputs_digest": self.inputs_digest,
        }


#: `plan(dists=)` 的默认值：由 `static_index(facts)` 现读（显式传 None = 不读）。
_AUTO = object()

_PRESENT = "present"  # 目标里有提供它的发行包（名字级）
_ABSENT = "absent"  # 没有，且没有任何证据说「装它不对」→ 缺（按 curated / 声明的名字装）
_OPEN = "open"  # 来源未定 / 装它不对：不进 missing、不转成安装名


@dataclasses.dataclass(frozen=True)
class _Verdict:
    kind: str
    dist: str = ""  # present：装着的那个发行包（规范化名）
    reason: str = ""  # open：`ORIGIN_REASONS` 之一


def _providers(c: importscan.ImportClass, installed: dict) -> list[str]:
    """`installed`（规范化发行包名 → 版本）里，名字级能认出来是在提供 `c` 的发行包：表 / 声明的名字、已装证据确认过的唯一
    提供者（`selected_distribution`）、登记过的备选发行包（`cv2` 的四个）。顺序 = 优先级；不看包里有什么（那是 `static_index`
    的事，这里只回答「按名字问，装着没有」）。"""
    out: list[str] = []

    def add(name: str) -> None:
        key = depresolve.normalize_distribution(name) if name else ""
        if key and key not in out and installed.get(key) is not None:
            out.append(key)

    if c.distribution and installed.get(c.distribution) is not None:
        out.append(c.distribution)
    add(c.selected_distribution)
    for alt in depresolve.distribution_alternatives(c.module):
        add(alt)
    return out


def _installed_claims(c: importscan.ImportClass) -> bool:
    """静态索引里有没有**别的**已装发行包声称提供它（未被遮蔽），或者 site-packages 里有这个模块却没有任何发行包认领它
    （`module_present_no_metadata`：手拷进去的 / 元数据被删的 / 构建残留）。两种都说明「环境里已经有它」，
    装 curated / 声明的名字既不一定是它、也不一定救得了。"""
    return distmeta.EV_ORPHAN in c.evidence or any(
        x.get("source") == distmeta.SRC_INSTALLED and not x.get("shadowed")
        for x in c.distribution_candidates
    )


def _reason_of(status: str) -> str:
    return {
        distmeta.ST_AMBIGUOUS: ORIGIN_AMBIGUOUS,
        distmeta.ST_EDITABLE: ORIGIN_EDITABLE,
        distmeta.ST_NOT_REPRODUCIBLE: ORIGIN_NOT_REPRODUCIBLE,
        distmeta.ST_CONDA: ORIGIN_CONDA,
    }.get(status, ORIGIN_UNVERIFIED)


def _verdict(
    c: importscan.ImportClass,
    installed: dict,
    state: str,
    declared: frozenset[str],
    *,
    index_incomplete: bool = False,
) -> _Verdict:
    """一个无条件第三方 import 相对**此刻的解释器**的判决。`state` 是 PR3 覆盖度缓存里它的模块状态（没量过为 ""）。

    判据的顺序就是证据的强弱：名字级已装（旧口径，原样）> 多个备选都装着 = 歧义 > 别的已装发行包声称提供它（editable /
    本地 / VCS / Conda / 未确认）> 包在导入失败 > 缺。多个备选都装着时，项目声明里点了名的那个胜出。后三种之一成立时「装 curated / 声明的名字」都是错的或没把握的。

    `index_incomplete`：静态索引有某一层的名字没读全（层不存在 / 是链接 / 列不出来 / 条目被跳过·链接拒跟 / 超预算 / 层数超限，`Index.names_complete=False`；
    个别发行包的元数据文件读不成只让 `complete=False`，不在此列——Homebrew 的 site-packages 里 pip 的 METADATA 就是符号链接）。这时「没查到」不等于「没装」——那一层里
    可能正有别的提供者（editable 的替代包、基础解释器里的同名模块），按名字装 curated 的包会遮蔽它。所以本该判「缺」的改判
    「来源未定」（`ORIGIN_UNVERIFIED`，条目的 `distribution_status` 是 `environment_not_checked`）：宁可不完整当未定，也不漏层后去装。"""
    present = _providers(c, installed)
    if c.distribution and c.distribution in present:
        return _Verdict(_PRESENT, c.distribution)
    if len(present) > 1:
        # 项目点名了其中一个（requirements 里写的就是 `opencv-python-headless`）：用户已经替我们选了
        chosen = [d for d in present if d in declared]
        if len(chosen) == 1:
            return _Verdict(_PRESENT, chosen[0])
        return _Verdict(_OPEN, reason=ORIGIN_AMBIGUOUS)
    if present:
        return _Verdict(_PRESENT, present[0])
    if _installed_claims(c):
        return _Verdict(_OPEN, reason=_reason_of(c.distribution_status))
    if state == "import_error":
        return _Verdict(_OPEN, reason=ORIGIN_IMPORT_FAILED)
    if index_incomplete:
        return _Verdict(_OPEN, reason=ORIGIN_UNVERIFIED)
    return _Verdict(_ABSENT)


def _coverage_states(
    coverage: Callable[[tuple[str, ...]], dict | None] | None,
    scan: importscan.ScanResult,
    plain_unknown: set[str],
) -> dict[str, str]:
    """PR3 覆盖度缓存里目标解释器对这组 import 的模块状态（`found` / `not_found` / `import_error` / …）。只读缓存，
    不起进程；没量过、环境本身不健康（覆盖度的 `unusable`）、缓存键对不上都回空（= 不知道，不冒充）。查询用的名字集合与
    依赖门 / 检测的 `_plan_imports` 同一份（缺的 + 装着的 + 映射不到的），键才对得上。"""
    if coverage is None:
        return {}
    names = tuple(sorted({c.module for c in scan.needed} | plain_unknown))
    if not names:
        return {}
    health = coverage(names)
    if not isinstance(health, dict) or not health.get("ok"):
        return {}
    detail = health.get("modules_detail") or {}
    return {n: str(detail[n]) for n in names if isinstance(detail.get(n), str)}


def _origin_entry(c: importscan.ImportClass, reason: str, state: str) -> dict:
    if reason == ORIGIN_UNRESOLVED and _installed_claims(c):
        reason = _reason_of(c.distribution_status)  # 映射表没有，但已装的某个发行包声称提供它
    candidates = sorted(
        {
            depresolve.normalize_distribution(str(x.get("distribution") or ""))
            for x in c.distribution_candidates
            if x.get("distribution")
        }
    )
    return {
        "import_name": c.module,
        "bucket": c.bucket,
        "distribution": c.distribution,
        "reason": reason,
        "resolution_status": c.resolution_status,
        "distribution_status": c.distribution_status,
        "origin_kind": c.origin_kind,
        "provenance": c.distribution_provenance,
        "candidates": candidates[:MAX_ORIGIN_CANDIDATES],
        "coverage": state,
    }


def plan(
    root: str | Path,
    script: str,
    *,
    facts: TargetFacts | None,
    target_kind: str,
    groups: list[str] | None = None,
    intents: list[depresolve.DependencyIntent] | None = None,
    install_facts: TargetFacts | None = None,
    dists: distmeta.Index | None | object = _AUTO,
    coverage: Callable[[tuple[str, ...]], dict | None] | None = None,
) -> JointPlan:
    """拼一份联合计划。`facts` 是**此刻会跑脚本的**解释器的事实（缺什么按它量——门问的是
    「现在起会话会不会缺包」）；`target_kind` 说安装会落到哪种环境（受管环境并入 adapter 约束，
    用户 venv 不并入）；`install_facts` 是**装到哪**的事实——安装目标不是 `facts` 那个环境时给
    （选中的是项目 venv / 系统解释器、目标是受管环境：active 那一代，或从 base 新建的一代——
    marker 环境与 stdlib 按 base、已装集合为空）。交给安装器的集合按它量，否则新的一代会漏装
    选中环境里碰巧有的包（Codex #461 P1）；marker 与 stdlib 也按它——脚本装完是在它里面跑。

    来源状态（PR4）：`dists` 是目标环境的静态发行包索引（默认 `static_index(facts)`，只读文件，不起进程；
    显式传 None = 不读）；`coverage(names)` 是 PR3 覆盖度的**只读缓存**查询（`userenvs.cached_probe`，没量过回 None，
    不起进程）——只用来认出「包在、导入失败」。两者都没有时判据与以前逐字相同。"""
    if target_kind not in TARGETS:
        raise ValueError(f"target_kind 非法: {target_kind!r}")
    root_p = Path(root)
    intents = depresolve.declared_intents(root_p, script) if intents is None else list(intents)
    target = install_facts if install_facts is not None else facts
    marker_env = target.marker_env if target is not None else None
    selection = select(intents, marker_env=marker_env, groups=groups)
    declared = {}
    for it in selection.requirements:
        declared.setdefault(it.name, it.specifier)
    if dists is _AUTO:
        dists = static_index(facts)
    scan = importscan.scan(
        root_p,
        script,
        declared=declared,
        stdlib=target.stdlib if target is not None else None,
        # 目标解释器的事实（#888）；没量到（替身 / facts=None）就不传，importscan 走保守口径
        builtin=(target.builtin or None) if target is not None else None,
        ext_suffixes=(target.ext_suffixes or None) if target is not None else None,
        dists=dists,
    )
    installed = facts.installed if facts is not None else {}
    target_installed = target.installed if target is not None else {}
    _requirements, _markers, specifiers, _utils, _version = depresolve._pkg()

    needed = tuple(c.to_payload() for c in scan.needed)
    plain_unknown = {
        c.module
        for c in scan.classes
        if c.bucket == importscan.BUCKET_UNKNOWN
        and c.context == importscan.CONTEXT_UNCONDITIONAL
        and not c.unused
    }
    unused = tuple(c.module for c in scan.classes if c.unused)
    possible = tuple(
        c.to_payload()
        for c in scan.classes
        if c.bucket in (importscan.BUCKET_THIRD_PARTY, importscan.BUCKET_UNKNOWN)
        and c.context != importscan.CONTEXT_UNCONDITIONAL
    )
    by_name: dict[str, list[depresolve.DependencyIntent]] = {}
    for it in selection.requirements:
        by_name.setdefault(it.name, []).append(it)

    # ---- 每个第三方 import 的来源判决（present / absent / open）-----------------------------
    states = _coverage_states(coverage, scan, plain_unknown)
    declared_names = frozenset(by_name)
    # 静态索引读不全（有一层没读成）：名字级查不到的不当「缺」（见 `_verdict`）。没给索引（None）= 没量，维持旧口径
    index_incomplete = (
        isinstance(dists, distmeta.Index) and dists.checked and not dists.names_complete
    )
    verdicts = {
        c.module: _verdict(
            c,
            installed,
            states.get(c.module, ""),
            declared_names,
            index_incomplete=index_incomplete,
        )
        for c in scan.needed
    }

    missing: list[dict] = []
    satisfied: list[dict] = []
    open_classes: dict[str, str] = {}  # import 名 → 原因（没进 missing / satisfied 的第三方）
    for c in scan.needed:
        verdict = verdicts[c.module]
        if verdict.kind == _OPEN:
            open_classes[c.module] = verdict.reason
            continue
        dist = verdict.dist if verdict.kind == _PRESENT else c.distribution
        declared_for = by_name.get(dist, [])
        entry = {
            "import_name": c.module,
            "distribution": dist,
            "resolution_source": c.resolution_source,
            "declared": bool(declared_for),
            "specifiers": sorted({it.specifier for it in declared_for if it.specifier}),
            "via": list(c.via),
        }
        # 来源状态（可选字段，只在有话可说时出现）：不改 missing / satisfied 的含义
        if c.resolution_status:
            entry["resolution_status"] = c.resolution_status
        if c.distribution_status:
            entry["distribution_status"] = c.distribution_status
        if verdict.kind == _PRESENT and dist != c.distribution:
            entry["mapped_distribution"] = c.distribution  # 表 / 声明里的名字不是装着的这个
        version = installed.get(dist) if verdict.kind == _PRESENT else None
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

    origins = tuple(
        _origin_entry(c, open_classes.get(c.module, ORIGIN_UNRESOLVED), states.get(c.module, ""))
        for c in scan.classes
        if c.module in open_classes or c.module in plain_unknown
    )
    unknown = tuple(o["import_name"] for o in origins)

    # ---- 交给安装器的集合（按**装到哪**量：目标已有的不装、其余 needed 的全装） --------------
    # 来源判决（`verdicts`）只说**当前解释器**里这个 import 是谁提供的，只能抑制当前环境的 missing。装到当前环境
    # （`install_facts` 是 None / 就是 `facts`）时它同样决定装不装：来源未定的（open）一概不装，其余按已装集合的名字级判据。
    # 装到**另一个**环境（新托管代 / active 那一代）时当前环境里的 editable / 本地 / Conda 提供者、缓存的 import_error、
    # 读不全的索引都不在那里——新代没有那个提供者，不装就会 import 失败、验证失败（Codex #920 P1）。所以按目标的已装集合
    # 单独判：目标里名字级已有的不装，其余按可信安装名（表 / 声明，`c.distribution`）装；从不拿已装元数据造名字。
    # 当前环境里是 editable 等、可信解析又给不出安装名的 import（`unknown` 桶）本来就不在 `scan.needed`：它们留在
    # `unknown` / `origins`（带 editable / 本地 / Conda 的来源状态），如实标出新环境装不出来，不猜 PyPI 名。
    same_target = install_facts is None or install_facts is facts
    to_install = {
        c.distribution
        for c in scan.needed
        if c.distribution
        and (same_target is False or verdicts[c.module].kind != _OPEN)
        and not _providers(c, target_installed)
    }
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
        for name in sorted(to_install):
            for it in by_name.get(name, ()):
                text = depresolve.requirement_string(it)
                if text not in reqs:
                    reqs.append(text)
            if not by_name.get(name):
                # 没有项目声明、只有 curated 映射：裸名（不钉版本——我们不替项目决定版本）
                if name not in reqs:
                    reqs.append(name)
    for it in selection.requirements:
        if hash_mode or it.name in to_install:
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
    if hash_mode and adapter:
        # 锁文件语义下 pip 要每一条都有 hash，adapter 那几条我们给不出 hash——**不写进需求文件**
        # （`deprepair.generation_requirements` hash 模式只给锁本身），改为要求锁已经把它们钉住
        # （`==` 且在 adapter 范围内）。锁就是闭包：它没锁 matplotlib，新的一代就装不出 worker 能跑
        # 的环境，那是 blocked，不是「偷偷不带 hash 装一条」（Codex #461 P1）。
        unlocked, outside = _adapter_against_lock(adapter, selection.requirements)
        if unlocked:
            blocked.append(
                {"code": BLOCK_HASHES_INCOMPLETE, "adapter": unlocked, "count": len(unlocked)}
            )
        if outside:
            blocked.append({"code": BLOCK_CONFLICT, "conflicts": outside})

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
    if install_facts is not None:
        facts_payload["install"] = install_facts.to_payload()
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
        unused=unused,
        origins=origins,
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
        inputs_digest=inputs_digest(root_p, intents, scan.files),
    )


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def inputs_digest(
    root: str | Path, intents: list[depresolve.DependencyIntent], files: tuple[str, ...]
) -> str:
    """规划输入的指纹（见 `JointPlan.inputs_digest`）。文件按字节 sha256；读不了的记 `<unreadable>`——
    读不了也是一种输入状态，变成读得了同样算变了。"""
    root_p = Path(root)
    hashes: list[list[str]] = []
    for rel in files:
        try:
            digest = hashlib.sha256((root_p / rel).read_bytes()).hexdigest()
        except OSError:
            digest = "<unreadable>"
        hashes.append([rel, digest])
    return _digest({"intents": [it.to_payload() for it in intents], "files": hashes})


def fresh_venv_facts(
    base: str, *, use_cache: bool = True, provided: tuple[str, ...] = ()
) -> TargetFacts | None:
    """从 `base` 新建的一代**装之前**的事实：marker 环境与 stdlib 是 base 的（venv 继承解释器），
    已装集合为空（不带 `--system-site-packages`，`managedenv.create_generation_venv`）——只有
    `provided`（这一代必然会带上的 distribution：受管环境的 adapter，`adapter_distributions()`）
    算作已有，版本未知记空串；它们不进 `requirements`（adapter 自己会装）。"""
    facts = target_facts(base, use_cache=use_cache)
    if facts is None:
        return None
    installed = {depresolve.normalize_distribution(name): "" for name in provided}
    return dataclasses.replace(facts, installed=installed, prefix="", executable="", site_roots=())


def adapter_distributions() -> tuple[str, ...]:
    """adapter 那几条的 distribution 名（PEP 503）——受管环境的每一代都会带上它们。"""
    requirements, _markers, _specifiers, _utils, _version = depresolve._pkg()
    return tuple(
        depresolve.normalize_distribution(requirements.Requirement(text).name)
        for text in ADAPTER_REQUIREMENTS
    )


def _adapter_against_lock(
    adapter: tuple[str, ...], lock: list[depresolve.DependencyIntent]
) -> tuple[list[str], list[dict]]:
    """hash 模式：adapter 的每一条在锁里有没有 `==` 钉住、钉住的版本在不在 adapter 范围内。
    回 (没钉住的名字, 钉在范围外的冲突条目——与 `depresolve.conflicts()` 同一形状)。"""
    requirements, _markers, specifiers, _utils, _version = depresolve._pkg()
    unlocked: list[str] = []
    outside: list[dict] = []
    for text in adapter:
        req = requirements.Requirement(text)
        name = depresolve.normalize_distribution(req.name)
        pins = [it for it in lock if it.name == name and _pinned_version(it.specifier, specifiers)]
        if not pins:
            unlocked.append(name)
            continue
        for it in pins:
            version = _pinned_version(it.specifier, specifiers)
            if not req.specifier.contains(version, prereleases=True):
                outside.append(
                    {
                        "name": name,
                        "specifiers": [it.specifier, str(req.specifier)],
                        "sources": [it.source, "adapter"],
                        "kinds": [it.kind],
                        "reasons": [
                            f"锁里 {name}{it.specifier} 不在 Tavotto 需要的 {req.specifier} 内"
                        ],
                    }
                )
    return unlocked, outside


def _pinned_version(specifier: str, specifiers) -> str:
    """`==X`（不带通配、只有这一条）→ X；否则空串。"""
    try:
        parsed = list(specifiers.SpecifierSet(specifier))
    except (specifiers.InvalidSpecifier, ValueError):
        return ""
    if len(parsed) != 1 or parsed[0].operator != "==" or parsed[0].version.endswith("*"):
        return ""
    return parsed[0].version


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
