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
import stat
import subprocess
import threading
from collections.abc import Callable, Collection, Mapping
from pathlib import Path

from . import config, depresolve, distmeta, importscan, launcher, projectenv, runtime

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

#: 选中的声明相对一个环境的差集（P02，`JointPlan.declared_gaps[].state`）：没装 / 装了但版本不满足声明的约束。
#: 满足的不出现。与 `JointPlan.satisfied[].matches_declared` 是同一件事的两个视角：后者说「脚本 import 的这个包装着、满不满足」，
#: 这里说「项目声明要的每一条，环境里差什么」（含脚本里没有任何 import 能指到它的）。
GAP_ABSENT = "absent"
GAP_VERSION_UNSATISFIED = "version_unsatisfied"
GAP_STATES = (GAP_ABSENT, GAP_VERSION_UNSATISFIED)
#: `declared_gaps[].action`：`install` = 这份计划会把它装进新一代；`recorded` = 只记录偏差（用户自己的环境能原样跑，不为它动手）
GAP_ACTION_INSTALL = "install"
GAP_ACTION_RECORDED = "recorded"
MAX_DECLARED_GAPS = 40
MAX_PROVIDER_DECISIONS = 40

#: `provider_decisions[].action`：新一代要装它 / 目标里已经有 / 来源未定（不装）
DECISION_ACTIONS = ("install", "satisfied", "undecided")


class UserSpecifiedRejected(ValueError):
    """`plan(user_specified=)` 里的某一项不被接受（Import Origin PR5b）。`kind`：`NOT_APPLICABLE` = 这个 import 此刻不是
    「来源未定、表外、没有可信安装名」的那一类（已经有可信解析 / 已经装着 / 是本地模块 / 来源有别的原因……）；
    `INVALID` = 用户给的包名 / 版本约束过不了 `depresolve.from_user_input` 的严格语法。两者都在形成计划之前拒绝，不静默丢弃。"""

    NOT_APPLICABLE = "not_applicable"
    INVALID = "invalid_requirement"

    def __init__(self, import_name: str, kind: str) -> None:
        super().__init__(f"{kind}: {import_name}")
        self.import_name = import_name
        self.kind = kind


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
import json, os, platform, re, site, sys
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
        # 同名发行包在 sys.path 上出现多次（venv 与 include-system-site-packages 的基础层各一份）：import 到的是排在前面的那份，
        # 迭代顺序就是 sys.path 顺序——首次出现即保留，不让后面被遮蔽的覆盖它
        installed.setdefault(key, d.version or "")
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
# **安装根目录本身不是包层**（site 层与额外路径层都不算）：Windows 上 `site.getsitepackages()` 连 sys.prefix 本身也列（[prefix, prefix\\Lib\\site-packages]），
# Windows 的 venv 里 sys.path 带着基础解释器的根目录和 venv 根目录（里面是 Lib / DLLs / Scripts /
# tcl / Tools 等安装物，Lib 与 DLLs 是标准库，site-packages 已在 site_roots），目录里的链接 / junction / 读不了的条目会把整个名字层
# 判成没读全，所有本该判缺的改判来源未定。探针自己的 cwd（`-c` 的 sys.path[0]）同理——worker 的脚本目录由 importscan 本地解析负责
install_roots = set()
for attr in ("prefix", "exec_prefix", "base_prefix", "base_exec_prefix", "real_prefix"):
    value = getattr(sys, attr, None)
    if isinstance(value, str) and value:
        install_roots.add(os.path.normcase(os.path.abspath(value)))
for exe in (sys.executable, getattr(sys, "_base_executable", None)):
    if isinstance(exe, str) and exe:
        install_roots.add(os.path.normcase(os.path.dirname(os.path.abspath(exe))))
try:
    install_roots.add(os.path.normcase(os.path.abspath(os.getcwd())))
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
    if os.path.normcase(path) in install_roots:
        continue
    if os.path.normcase(path) in known or os.path.basename(path).lower() in (
        "site-packages",
        "dist-packages",
    ):
        if path not in site_roots:
            site_roots.append(path)
# sys.path 上**其余**存在的目录（PYTHONPATH 的 `pip install --target` 目录、`.pth` 路径行加进来的目录，如老式 editable 的 src）：
# 不含已在 site_roots 里的、标准库目录（及其子目录）、lib-dynload / DLLs、zip 等非目录。静态索引对它们只做名字级扫描。
import sysconfig
skip = {os.path.normcase(os.path.dirname(os.path.abspath(os.__file__)))}  # venv 里 sysconfig 给的不是真标准库目录，os 的位置才是
for key in ("stdlib", "platstdlib"):
    try:
        skip.add(os.path.normcase(os.path.abspath(sysconfig.get_path(key))))
    except Exception:
        pass
extra_roots = []
# 被**有意排除**的目录（安装根目录、标准库目录及其子目录、lib-dynload / DLLs、探针 cwd）：不当包层读，但 `.pth` 路径行指向它们
# 不算「指向没读过的目录」——里面没有第三方提供者（Windows 的 venv 里 sys.path 本来就带 DLLs 与基础解释器根目录，conda / 某些发行版的
# `.pth` 也会指向标准库目录）。只是路径列表，静态索引据此认「已跟进」
skipped_roots = []
for entry in sys.path:
    if not entry or not isinstance(entry, str):
        continue
    try:
        path = os.path.abspath(entry)
        if not os.path.isdir(path) or path in site_roots or path in extra_roots:
            continue
    except Exception:
        continue
    norm = os.path.normcase(path)
    if (
        norm in install_roots
        or os.path.basename(path).lower() in ("lib-dynload", "dlls")
        or any(norm == k or norm.startswith(k + os.sep) for k in skip)
    ):
        if path not in skipped_roots:
            skipped_roots.append(path)
        continue
    extra_roots.append(path)
# sys.path 上**不是目录**却存在的条目（PYTHONPATH 上的 zip / egg 等 zipimport 来源）：静态索引读不了里面有哪些名字，只数个数，
# 由宿主据此把名字层标成没读全（Codex #920 r4237324191）。标准库自己的 `pythonXY.zip`（嵌入式发行版）不算
opaque_roots = 0
for entry in sys.path:
    if not entry or not isinstance(entry, str):
        continue
    try:
        path = os.path.abspath(entry)
        if not os.path.exists(path) or os.path.isdir(path):
            continue
    except Exception:
        continue
    if re.fullmatch(r"python\d+t?\.zip", os.path.basename(path).lower()):
        continue
    opaque_roots += 1
# site_roots 里 Python 的 `site` 真会处理 `.pth` 的那几层（getsitepackages / 启用的 user site）；经 PYTHONPATH 或 `.pth` 路径行进来的
# 目录不处理它的 `.pth`，那里的行不会被执行。只是路径列表
pth_roots = [p for p in site_roots if os.path.normcase(p) in known]
# site_roots 与 extra_roots 合起来**按 sys.path 的原顺序**排成一份（#929）：静态索引按它给各层定先后，谁遮蔽谁才和真实 import 一致
# （PYTHONPATH / `.pth` 目录排在 site-packages 前面时，前面的同名模块先被 import）。只是路径列表；成员与上面两份一致，不另做筛选
path_order = []
for entry in sys.path:
    if not entry or not isinstance(entry, str):
        continue
    try:
        path = os.path.abspath(entry)
    except Exception:
        continue
    if (path in site_roots or path in extra_roots) and path not in path_order:
        path_order.append(path)
sys.stdout.write(json.dumps({
    "marker_env": env,
    "stdlib": sorted(getattr(sys, "stdlib_module_names", ())),
    "builtin": sorted(sys.builtin_module_names),
    "ext_suffixes": list(importlib.machinery.EXTENSION_SUFFIXES),
    "installed": installed,
    "prefix": sys.prefix,
    "executable": sys.executable,
    "site_roots": site_roots,
    "extra_roots": extra_roots,
    "opaque_roots": opaque_roots,
    "pth_roots": pth_roots,
    "path_order": path_order,
    "skipped_roots": skipped_roots,
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
    #: 量这份事实时的启动上下文（`launcher.context_key`）：缓存分区用，不进 digest / payload。
    context: str = ""
    #: 目标解释器**看得见**的全部 site 目录（`sys.path` 顺序：prefix 内的、`include-system-site-packages` 的基础解释器层、
    #: 启用的 user site、其它 site-packages / dist-packages）。静态索引（`static_index`）按它读元数据，否则别的层里的替代提供者
    #: 会被漏掉（Codex #920 P1）。**空 = 没量到**（替身事实、老缓存），索引退回只读前缀的旧口径。与 `builtin` 同理不进
    #: `digest()` / `to_payload()`：它们由解释器与环境决定，不是计划的输入，也就不改 `inputs_digest` / `identity`。
    site_roots: tuple[str, ...] = ()
    #: `sys.path` 上其余存在的目录（`PYTHONPATH` / `.pth` 路径行加进来的；不含标准库与 `site_roots`）。静态索引只做名字级扫描：
    #: 模块名出现在那里 = 别处已有提供者，不判缺。同 `site_roots`：空 = 没量到，不进 digest / payload。
    extra_roots: tuple[str, ...] = ()
    #: `sys.path` 上存在、但不是目录的 import 来源（zip / egg）的个数。里面的名字静态读不到：> 0 时索引的名字层不完整，
    #: 本该判缺的改判来源未定，不去装同名包遮蔽它。同上：不进 digest / payload。
    opaque_roots: int = 0
    #: `site_roots` 里 Python 真会处理 `.pth` 的那几层（`site.getsitepackages()` / 启用的 user site）。静态索引据此只对这几层
    #: 的 `.pth` 行计 `uncovered_paths`：经 `PYTHONPATH` 或 `.pth` 路径行进 `sys.path` 的 site-packages 不处理它的 `.pth`（#925）。
    #: 空 = 没量到，每一层都当作会处理（旧口径）。不进 `digest()` / `to_payload()`。
    pth_roots: tuple[str, ...] = ()
    #: `site_roots` ∪ `extra_roots` 按 `sys.path` 原顺序排成的一份（#929）。静态索引据此交错给各层定先后（遮蔽语义沿用跨层规则）；
    #: 空 = 没量到，退回「先 site 层、再额外层」的旧排法。不进 `digest()` / `to_payload()`。
    path_order: tuple[str, ...] = ()
    #: 被有意排除、不当包层的 `sys.path` 目录（安装根目录 / 标准库及其子目录 / lib-dynload / DLLs / 探针 cwd）。静态索引据此把指向它们
    #: 的 `.pth` 路径行算已跟进（不计 `uncovered_paths`）。不进 `digest()` / `to_payload()`，也不进 `layer_digest`：它们由解释器
    #: 的安装布局决定，不是会在确认窗口里变的 import 层（要变的话 site / extra / path_order 会先变）。空 = 没量到。
    skipped_roots: tuple[str, ...] = ()
    #: `layer_digest` 在**量事实的同一刻**算好的值（真探测才有；与静态索引同一刻读，计划与它描述同一个时间点）。空 = 没量到，
    #: `layer_digest(facts)` 退回现算。不进 `digest()` / `to_payload()`。
    layers: str = ""

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


#: `layer_digest` 读单个 `.pth` 的字节上限 / 每个 site 层最多看几个 `.pth`：超出的只记个数与大小，不无限读。
_PTH_READ_LIMIT = 256 * 1024
_PTH_MAX_PER_ROOT = 200


def layer_digest(facts: TargetFacts | None) -> str:
    """目标解释器**import 层**的指纹（#922）：`site_roots` / `extra_roots` / `opaque_roots`（sys.path 顺序）加上 site 层里
    每个 `.pth` 的内容摘要（`.pth` 会往 `sys.path` 加目录、或执行 `import` 行）。

    只用于**执行期**的过期校验（`deprepair.prepare` 重量事实后与计划形成时的值比）：`TargetFacts.digest()` 只看 marker 与
    已装集合，`.pth` / 可见 site 层变了而发行包名字版本表没变时它不变，旧计划会被照单接受；而新出现的提供者本会改变
    missing / requirements。**不进** `digest()` / `to_payload()` / `inputs_digest` / `identity`——无关的重装不会因此发生。
    没有层信息（替身事实、老缓存、`fresh_venv_facts`）回空串：两侧都空 = 没有可比的东西，不拦。读文件，不起进程。"""
    if facts is None or not (facts.site_roots or facts.extra_roots or facts.opaque_roots):
        return ""
    if facts.layers:
        return facts.layers
    return _measure_layers(facts)


def _measure_layers(facts: TargetFacts) -> str:
    pth: dict[str, list] = {}
    for root in facts.site_roots:
        rows: list = []
        try:
            # 符号链接的 `.pth` 也计入（`site` 会跟随它）：下面记链接本身 + 目标内容摘要
            names = sorted(
                e.name
                for e in os.scandir(root)
                if e.name.lower().endswith(".pth") and (e.is_file() or e.is_symlink())
            )
        except OSError:
            pth[root] = ["unreadable"]
            continue
        for name in names[:_PTH_MAX_PER_ROOT]:
            full = os.path.join(root, name)
            try:
                link = os.readlink(full) if os.path.islink(full) else ""
                if not stat.S_ISREG(
                    os.stat(full).st_mode
                ):  # 指向设备 / 管道 / 目录的链接：不打开，记成不可读
                    rows.append([name, link, "unreadable"])
                    continue
                with open(full, "rb") as fh:
                    body = fh.read(_PTH_READ_LIMIT + 1)
                rows.append(
                    [name, link, len(body), hashlib.sha256(body[:_PTH_READ_LIMIT]).hexdigest()]
                )
            except OSError:
                rows.append([name, "unreadable"])
        if len(names) > _PTH_MAX_PER_ROOT:
            rows.append(["…", len(names)])
        pth[root] = rows
    text = json.dumps(
        {
            "site": list(facts.site_roots),
            "extra": list(facts.extra_roots),
            "opaque": facts.opaque_roots,
            "order": list(facts.path_order),
            "pth": pth,
        },
        sort_keys=True,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def provider_digest(facts: TargetFacts | None, names) -> str:
    """计划相关的这些 import 名在目标环境各层里的**提供状态**指纹（#932）：`Index.name_state`（哪些层的目录清单里有它、没人认领的层的
    形状、发行包候选与遮蔽）加索引的名字层是否读全。

    补 `layer_digest` 的缺口：层指纹只记层路径、顺序与 `.pth` 内容，不记层**里面**的名字——确认后往更前的 `PYTHONPATH` 目录新加一个
    `yaml.py`，层指纹不变，旧计划本会被接受并去装 PyYAML，运行时 import 到的却是新出现的遮蔽模块。**只记计划用到的名字**：
    无关包的安装 / 卸载不会让计划过期。读索引不起进程；没有索引（替身事实、全新代）回空串（两侧都空 = 没有可比的东西，不拦）。"""
    index = static_index(facts)
    wanted = sorted({str(n) for n in names or ()})
    if index is None or not wanted:
        return ""
    text = json.dumps(
        {
            "names": {n: index.name_state(n) for n in wanted},
            "names_complete": index.names_complete,
            "uncovered": index.uncovered_paths,
        },
        sort_keys=True,
        default=list,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


_facts_lock = threading.Lock()
_facts_cache: dict[str, TargetFacts] = {}


def target_facts(python: str, *, use_cache: bool = True) -> TargetFacts | None:
    """在目标解释器里量一次事实；起不来回 None（调用方据此 `blocked`）。

    按解释器路径缓存：同一进程里同一个环境反复问是常态（每次准备 / 每次渲染前的门）。
    环境被改动（装完包 / 换代）时调 `reset_cache(python)`——`deprepair` 在事务结束时做。
    """
    ctx = launcher.context_key(python)
    key = (
        f"{_key(python)}|{ctx}"  # 启动上下文分区：裸起量到的事实不能在 Conda 激活下复用（反之亦然）
    )
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
        # 激活捕获与事实探测共用 `FACTS_TIMEOUT_S` 一个预算
        env, remaining = launcher.activate_within(
            python, runtime.owned_env(python), FACTS_TIMEOUT_S
        )
        proc = subprocess.run(
            [str(python), *runtime.probe_args(), "-c", _FACTS_SRC],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=remaining,
            stdin=subprocess.DEVNULL,
            cwd=scratch,
            env=env,
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except launcher.LauncherUnavailable as exc:
        # 激活取环境失败：瞬时、不缓存，**原样抛出**——不能折成 None（那会被当成“目标不可用”给出错误的恢复动作）；
        # 稳定码由入口（`deprepair._facts_for` / `decide_environment_pinned` / pool）交给用户
        LOG.warning("目标环境事实探测失败（%s）: %s", exc.code, python)
        raise
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
        extra_roots=tuple(str(x) for x in data.get("extra_roots") or () if isinstance(x, str)),
        opaque_roots=int(data.get("opaque_roots") or 0)
        if isinstance(data.get("opaque_roots"), int)
        else 0,
        pth_roots=tuple(str(x) for x in data.get("pth_roots") or () if isinstance(x, str)),
        path_order=tuple(str(x) for x in data.get("path_order") or () if isinstance(x, str)),
        skipped_roots=tuple(str(x) for x in data.get("skipped_roots") or () if isinstance(x, str)),
    )
    facts = dataclasses.replace(facts, layers=_measure_layers(facts), context=ctx)
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
            prefix = f"{_key(python)}|"  # 该解释器在所有启动上下文下的结论一起作废
            for cache in (_facts_cache, _index_cache):
                for k in [k for k in cache if k.startswith(prefix)]:
                    cache.pop(k, None)


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
    key = f"{_key(facts.python or facts.prefix)}|{facts.context}"
    with _facts_lock:
        hit = _index_cache.get(key)
    if hit is not None:
        return hit
    # 读目标解释器报告的**全部**层（含基础解释器 / user site）；没量到层（替身事实）才退回只读前缀的旧口径。任何一层没读成
    # → `Index.complete=False`，下游据此不把「没查到」当「没装」（见 `plan` 的 `index_incomplete`）。
    index = distmeta.index_environment(
        facts.prefix,
        site_paths=facts.site_roots or None,
        extra_paths=facts.extra_roots,
        # 目标解释器的精确后缀集（#925）；没量到保持宽松口径
        ext_suffixes=facts.ext_suffixes or None,
        pth_paths=facts.pth_roots or None,
        layer_order=facts.path_order or None,
        skipped_paths=facts.skipped_roots,
    )
    if not index.checked:
        return None
    if facts.opaque_roots > 0:  # zip / egg 里的名字读不到：名字层不完整
        index.names_complete = False
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
    #: 只有**安装目标**要装、当前解释器并不报缺的条目（形状同 `missing`）：当前环境经 editable / 本地 / Conda / 备选发行包 / 已装
    #: 名字提供了这个 import（所以不在 `missing`），而目标是另一个托管代，那里没有，`requirements` 里照装。账本 / 验证 import
    #: 要覆盖 `requirements` 里的全部条目，所以 `deprepair` 用 `install_entries`（= missing + 这里）。**不进 `to_payload`**
    #: （披露与 `missing` 语义不变，`plan_version` 不升）。
    install_only: tuple[dict, ...] = ()
    #: 用户在确认界面接受（可改）预填后提交的「用户指定」需求（Import Origin PR5b，维护者裁决 C1）：`{import_name, distribution,
    #: specifier, requirement, resolution_source=user_specified, verified_source=False}`。它们**不是**可信来源解析出来的——
    #: 披露里必须标明「用户指定、未经可信来源核实」（`deprepair.impact_of` 的 `user_specified`）。只在有时才进 `to_payload`
    #: （老客户端忽略，`plan_version` 不升）。
    user_specified: tuple[dict, ...] = ()
    #: 选中的声明相对**此刻跑脚本的环境**的差集（P02）：`{distribution, specifier, state, installed_version, relevant, action}`。
    #: `relevant` = 脚本里有 import 能指到它、或脚本有未解释的 import / 动态导入（这时声明就是权威，不被 AST 否决）；只有相关的差集才让
    #: 托管环境的计划 `ready`，不相关的只记录。`action` 见 `GAP_ACTION_*`。只在有差集时才进 `to_payload`（老客户端忽略，`plan_version` 不升）。
    declared_gaps: tuple[dict, ...] = ()
    #: 每个提供者决策的依据与环境里**观察到**的提供者（P03，AC-08 / AC-09）：`{import_name, distribution, basis, action,
    #: observed: [{distribution, version, provenance, reproducible}], replaces_observed}`。`basis` ∈ `depresolve.INSTALLABLE_SOURCES`
    #: （project_declared / curated / user_specified；没有「观察」这一档）；`action` ∈ `DECISION_ACTIONS`；`observed` 是此刻跑脚本的环境里
    #: 未被遮蔽的已装提供者（editable / 本地 / VCS 也列出，带 `provenance` 与 `reproducible=false`，**从不**折成 `name==version`）；
    #: `replaces_observed` = 新一代要装的发行包与观察到的都不同（例：运行环境用 headless、新一代按表装 `opencv-python`）。
    #: 这是**披露**：不进 `requirements` / `identity` / `inputs_digest` / 影响摘要，观察到的提供者不授权安装。只在有话可说时才进 `to_payload`
    #: （老客户端忽略，`plan_version` 不升）。
    provider_decisions: tuple[dict, ...] = ()
    #: 差集总数；超过 `MAX_DECLARED_GAPS` 被截断时 > `len(declared_gaps)`（状态判定用的是完整集合，只有对外载荷截断）。
    declared_gaps_total: int = 0
    #: 计划里点名、**不送 pip** 的已知装不了的包（`depresolve.NOT_PIP_INSTALLABLE`；ADR 0061 修订 2026-10-21）：
    #: `{name, distribution, hint, sources: [声明文件], imports: [import 名]}`。只在有时才进 `to_payload`（`plan_version` 不升）。
    not_pip_installable: tuple[dict, ...] = ()

    @property
    def install_entries(self) -> tuple[dict, ...]:
        return (*self.missing, *self.install_only)

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
            **(
                {"user_specified": [dict(u) for u in self.user_specified]}
                if self.user_specified
                else {}
            ),
            **(
                {"declared_gaps": [dict(g) for g in self.declared_gaps]}
                if self.declared_gaps
                else {}
            ),
            **(
                {"provider_decisions": [dict(d) for d in self.provider_decisions]}
                if self.provider_decisions
                else {}
            ),
            **(
                {"not_pip_installable": [dict(n) for n in self.not_pip_installable]}
                if self.not_pip_installable
                else {}
            ),
            **(
                {"declared_gaps_total": self.declared_gaps_total}
                if self.declared_gaps_total > len(self.declared_gaps)
                else {}
            ),
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


def _shadowed_names(c: importscan.ImportClass, present: list[str]) -> bool:
    """名字级「装着」的那些发行包，在静态索引里是不是**全被排在前面的别的提供者遮蔽了**（#929）：`PYTHONPATH` / `.pth` 目录里自己的
    同名模块、前面一层的 editable fork 排在 site-packages 前面，真实 import 到的是它们，不是名字级装着的那个。没有索引（候选里
    没有已装行）或还有未被遮蔽的行时回 False。"""
    if not present:
        return False
    rows = [x for x in c.distribution_candidates if x.get("source") == distmeta.SRC_INSTALLED]
    live = {
        depresolve.normalize_distribution(str(x.get("distribution") or ""))
        for x in rows
        if not x.get("shadowed")
    }
    shadowed = {
        depresolve.normalize_distribution(str(x.get("distribution") or ""))
        for x in rows
        if x.get("shadowed")
    }
    return all(d in shadowed and d not in live for d in present) and _installed_claims(c)


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
    if present and state == "import_error":
        # 名字级「装着」不等于满足：装了但导入失败（装坏的 PyYAML）——覆盖度缓存的判决先于名字级，装它救不了（Codex #920 P2）
        return _Verdict(_OPEN, reason=ORIGIN_IMPORT_FAILED)
    if present and "layer_order_unresolved" in c.compatibility:
        # 前面有一层的提供者形状推不出（可能是常规包、也可能不是）：名字级装着的那个是不是胜出者判不出来——不冒充满足（#929）
        return _Verdict(_OPEN, reason=ORIGIN_AMBIGUOUS)
    if _shadowed_names(c, present):
        # 名字级装着的发行包被 sys.path 上排在前面的别的提供者遮蔽（#929）：import 到的是前面那个，不是它——不算满足，也不装
        return _Verdict(_OPEN, reason=_reason_of(c.distribution_status))
    if len(present) > 1:
        # 多个提供者同时装着：歧义 / 声明判定**先于**映射提供者的早返回（#924：`opencv-python` 与 `-headless` 都装着、项目没点名时
        # 不能因为映射表里的 `opencv-python` 在就报满足）。项目点名了其中一个（requirements 里写的就是 `opencv-python-headless`）：
        # 用户已经替我们选了
        chosen = [d for d in present if d in declared]
        if len(chosen) == 1:
            return _Verdict(_PRESENT, chosen[0])
        return _Verdict(_OPEN, reason=ORIGIN_AMBIGUOUS)
    if c.distribution and c.distribution in present:
        return _Verdict(_PRESENT, c.distribution)
    if present:
        return _Verdict(_PRESENT, present[0])
    if _installed_claims(c):
        return _Verdict(_OPEN, reason=_reason_of(c.distribution_status))
    if state == "import_error":
        return _Verdict(_OPEN, reason=ORIGIN_IMPORT_FAILED)
    if index_incomplete:
        return _Verdict(_OPEN, reason=ORIGIN_UNVERIFIED)
    return _Verdict(_ABSENT)


def _installed_index_satisfies(
    c: importscan.ImportClass, installed: dict, state: str, index_incomplete: bool
) -> bool:
    """表外名（`bucket=unknown`，没有可信安装名）是不是已经被**环境里装好的、可由包索引重现的唯一发行包**满足（#938）。

    成立要同时满足（任何一条不成立都维持原判——来源未定）：静态索引读全（`index_incomplete` 为 False）；候选里恰有一个已装行、
    未被遮蔽，且是 `index` 来源 + 可重现 + PyPI 生态，状态为 `installed_confirmed` / `resolved`（唯一、已确认；多个提供者的
    歧义、editable / 本地 / VCS / URL / Conda、无元数据、`layer_order_unresolved` 都到不了这里）；该发行包确在目标解释器的
    已装集合里；覆盖度缓存没说它导入失败（`import_error`）。

    只回答「判满足」：调用方据此不把它放进 `unknown` / `origins`，**不产生任何安装名**——已装元数据是证据，不是安装授权。"""
    if c.bucket != importscan.BUCKET_UNKNOWN or index_incomplete or state == "import_error":
        return False
    if (
        c.distribution_status != distmeta.ST_CONFIRMED
        or c.resolution_status != importscan.STATUS_RESOLVED
        or c.distribution_provenance != distmeta.PROV_INDEX
        or "layer_order_unresolved" in c.compatibility
        or distmeta.EV_ORPHAN in c.evidence
    ):
        return False
    rows = [x for x in c.distribution_candidates if x.get("source") == distmeta.SRC_INSTALLED]
    live = [x for x in rows if not x.get("shadowed")]
    if len(live) != 1:
        return False
    (row,) = live
    if (
        row.get("provenance") != distmeta.PROV_INDEX
        or row.get("reproducible") is not True
        or row.get("ecosystem") != distmeta.ECO_PYPI
    ):
        return False
    key = depresolve.normalize_distribution(str(row.get("distribution") or ""))
    return bool(key) and installed.get(key) is not None


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


def _effective_reason(c: importscan.ImportClass, reason: str) -> str:
    if reason == ORIGIN_UNRESOLVED and _installed_claims(c):
        return _reason_of(c.distribution_status)  # 映射表没有，但已装的某个发行包声称提供它
    return reason


def _specifiable(
    c: importscan.ImportClass, reason: str, state: str, index_incomplete: bool
) -> bool:
    """这个 import 能不能接受「用户指定包名」（也是 `suggested_distribution` 预填的前提）：它必须是**纯粹的表外名**——
    没有可信安装名（既不在 curated 也没被项目声明点名）、没有任何证据说环境里已有别的提供者（已装发行包声称提供 / 模块在而无元数据 /
    editable / Conda / 本地归档……都是 `reason != ORIGIN_UNRESOLVED`）、覆盖度缓存没说它能 import（`found`）或「包在导入失败」
    （`import_error`：装它救不了）、而且静态索引把各层的名字读全了（读不全时「没查到」不等于「没装」，宁可不预填）。"""
    return (
        c.bucket == importscan.BUCKET_UNKNOWN
        and not c.distribution
        and c.resolution_status == importscan.STATUS_UNRESOLVED
        and reason == ORIGIN_UNRESOLVED
        and state not in ("found", "import_error")
        and not index_incomplete
    )


def _origin_entry(
    c: importscan.ImportClass,
    reason: str,
    state: str,
    *,
    suggest: str = "",
    literal_dirs: list[str] | None = None,
) -> dict:
    reason = _effective_reason(c, reason)
    candidates = sorted(
        {
            depresolve.normalize_distribution(str(x.get("distribution") or ""))
            for x in c.distribution_candidates
            if x.get("distribution")
        }
    )
    entry = {
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
    not_pip = depresolve.not_pip_installable(c.module)
    if not_pip is not None:
        # 已知装不了（ROOT 之类）：计划阶段点名，不预填、不送 pip
        entry["not_pip_installable"] = {"label": not_pip["label"], "hint": not_pip["hint"]}
    elif (
        literal_dirs
        and c.bucket == importscan.BUCKET_UNKNOWN
        and not c.distribution
        and c.resolution_status == importscan.STATUS_UNRESOLVED
    ):
        # 脚本把这些目录加进了搜索路径、里面却没有这个模块：本地模块缺失，不是缺 PyPI 包
        entry["local_module_missing"] = {"search_dirs": list(literal_dirs[:4])}
    if suggest:
        # 预填（Import Origin PR5b，C1）：**不是**可信来源、不进 requirements、不自动装——确认界面输入框的初始值，
        # 用户接受（可改）后经 `plan(user_specified=)` 才进计划
        entry["suggested_distribution"] = suggest
    return entry


def _root_state(
    intents: list[depresolve.DependencyIntent], installed: Mapping[str, str]
) -> tuple[str, str]:
    """一条声明（同名可能来自多个文件）相对一个环境的状态 `(GAP_*或"", 已装版本)`。版本读不懂 / 没有版本（替身事实里
    adapter 的空串）不判不满足——「判不出就别判」。"""
    name = intents[0].name
    version = installed.get(name)
    if version is None:
        return GAP_ABSENT, ""
    for it in intents:
        if it.specifier and depresolve.version_satisfies(it.specifier, version) is False:
            return GAP_VERSION_UNSATISFIED, version
    return "", version


#: 脚本「指到」某个发行包时算数的 import 上下文：模块层无条件、函数体内（延后）、别名 / 非恒认形式的动态导入。
#: `optional`（`try … except ImportError`，脚本自己处理缺席）、`conditional`、`type_checking` 不算——那几种缺了脚本照样跑，
#: 不为它们启动安装（与 `possible` 「只列出、不在跑前装」的旧合同一致）。
_BINDING_CONTEXTS = (
    importscan.CONTEXT_UNCONDITIONAL,
    importscan.CONTEXT_DEFERRED,
    importscan.CONTEXT_DYNAMIC,
)


def _is_binding(c) -> bool:
    """这条 import 分类算不算「指到发行包」：第三方 / 未知桶、绑定上下文、没被弃用。声明相关性与互斥 provider 的冲突触发
    共用这一处（`_relevant_declarations` / `_arbitrate_providers`）——可选 / 条件 / `TYPE_CHECKING` / 未使用的 import 两处都不算。"""
    return (
        c.bucket in (importscan.BUCKET_THIRD_PARTY, importscan.BUCKET_UNKNOWN)
        and any(ctx in _BINDING_CONTEXTS for ctx in (c.enclosing_contexts or (c.context,)))
        and not c.unused
    )


def _binding_dynamic(scan: importscan.ScanResult) -> bool:
    """有没有落在绑定上下文里的非字面量动态导入（外围是 `optional` / `conditional` / `type_checking` 的不算——
    缺了脚本照样跑，不需要跑前装什么）。外围没记（旧构造的 `ImportUse`）按保守的「算」。"""
    return any(
        (u.enclosing or importscan.CONTEXT_UNCONDITIONAL) in _BINDING_CONTEXTS for u in scan.dynamic
    )


def _relevant_declarations(
    names,
    scan: importscan.ScanResult,
    arbitrated: Mapping[str, str],
    plain_unknown: set[str],
) -> set[str]:
    """选中的声明里，哪些与这个脚本有可见的关联（P02）：脚本里有 import 能指到这个发行包，或脚本有无法归给任何发行包的 import
    （表外名、函数体内 / 别名的未知名字）、有非字面量的动态导入——后一种里没法逐条对应，声明就是权威（AST 不否决已选声明）。
    其余的声明（requirements 里躺着、这个脚本从没碰过的包）不因此装进来，也不让计划 `ready`：不污染不相干的脚本。"""
    bound = [c for c in scan.classes if c.distribution and _is_binding(c)]
    # 仲裁出的 provider 只对确实 import 了它那个模块的脚本相关（Codex #960 P2）：模块名已知，没 import 就不相关——
    # 共享 requirements 里点名 headless，只 `import numpy` 的脚本不因此装 OpenCV
    attached = {
        depresolve.normalize_distribution(arbitrated.get(c.module, c.distribution)) for c in bound
    }
    unimported = {
        depresolve.normalize_distribution(d)
        for m, d in arbitrated.items()
        if m not in {c.module for c in bound}
    } - attached
    unexplained = (
        bool(plain_unknown)
        or _binding_dynamic(scan)
        or any(c.bucket == importscan.BUCKET_UNKNOWN and _is_binding(c) for c in scan.classes)
    )
    return (set(names) - unimported) if unexplained else set(names) & attached


def _declared_gaps(
    by_name: dict[str, list[depresolve.DependencyIntent]],
    installed: Mapping[str, str] | None,
    relevant: set[str],
) -> tuple[dict, ...]:
    """选中的声明相对**此刻跑脚本的环境**的差集（`JointPlan.declared_gaps`，未带 `action`）；`relevant` 见
    `_relevant_declarations`。回**完整**集合：状态判定用它，对外载荷由调用方再截到 `MAX_DECLARED_GAPS`。"""
    if installed is None:
        return ()
    out: list[dict] = []
    for name, items in sorted(by_name.items()):
        state, version = _root_state(items, installed)
        if not state:
            continue
        out.append(
            {
                "distribution": name,
                "specifier": ",".join(sorted({it.specifier for it in items if it.specifier})),
                "state": state,
                "installed_version": version,
                "relevant": name in relevant,
            }
        )
    return tuple(out)


def _source_of(c: importscan.ImportClass, dist: str, arbitrated: Mapping[str, str]) -> str:
    """装 `dist` 的依据：`importscan` 按**它自己认的名字**（表 / 声明）给的 `resolution_source`；`dist` 若是 `_arbitrate_providers`
    让声明点名的互斥 provider 替掉的，依据就是项目声明。"""
    if arbitrated.get(c.module) == dist and dist != c.distribution:
        return depresolve.SOURCE_PROJECT_DECLARED
    return c.resolution_source


def _arbitrate_providers(
    classes,
    by_name: dict[str, list[depresolve.DependencyIntent]],
    installing: Collection[str] | None,
) -> tuple[dict[str, str], list[dict]]:
    """同一个 import 名有多个互斥发行包（`depresolve.ALTERNATIVE_DISTRIBUTIONS`，`cv2` 的四个）时的最小仲裁（AC-08 启用门）。

    回 `(import 名 → 项目声明点名的那个发行包, 冲突)`。只认**声明**：观察到的 provider 不是授权（装哪个不由已装元数据决定）；
    点名了恰好一个 = 它胜出，替掉 curated 的名字；点名了两个及以上 = 冲突（互斥发行包同装一代是已知会互相覆盖的坏环境），
    不是「都装」。声明里一个都没点名 = 不仲裁，维持 curated 行为。冲突只在脚本确实 import 那个名字、或点名的 provider **真的在这次计划的
    安装集合里**（`installing`：受管环境按声明装 roots 的那些、hash 模式整份锁）时报；`None` = 还没算出安装集合，只判前一种（取 `chosen` 用）。
    不看「是不是 roots 模式」这种全局开关：无关脚本的计划不装那两个 provider，不该被拦；hash 锁整份都装，同锁里的两个 provider 必须拦。"""
    scanned = {c.module for c in classes if _is_binding(c)}
    chosen: dict[str, str] = {}
    conflicts: list[dict] = []
    for module, alts in depresolve.ALTERNATIVE_DISTRIBUTIONS.items():
        named = sorted({depresolve.normalize_distribution(a) for a in alts} & set(by_name))
        if len(named) == 1:
            chosen[module] = named[0]
        elif len(named) > 1 and (
            module in scanned or (installing is not None and any(n in installing for n in named))
        ):
            items = [it for n in named for it in by_name[n]]
            conflicts.append(
                {
                    "name": module,
                    "specifiers": sorted({it.specifier for it in items if it.specifier}),
                    "sources": [it.source for it in items],
                    "kinds": ["requirement"],
                    "reasons": ["mutually_exclusive_providers: " + ", ".join(named)],
                }
            )
    return chosen, conflicts


def _observed_providers(c: importscan.ImportClass) -> list[dict]:
    """此刻跑脚本的环境里，**未被遮蔽**的已装提供者（来自静态索引的候选）。只带发行包名 / 版本 / 来源种类 / 能否由包索引重现，
    不带路径与文件内容；editable / 本地 wheel / VCS 照样列出（`reproducible=false`），让披露说得出「它们不可重建」。"""
    out: list[dict] = []
    for x in c.distribution_candidates:
        if x.get("source") != distmeta.SRC_INSTALLED or x.get("shadowed"):
            continue
        out.append(
            {
                "distribution": depresolve.normalize_distribution(str(x.get("distribution") or "")),
                "version": str(x.get("version") or ""),
                "provenance": str(x.get("provenance") or ""),
                "reproducible": bool(x.get("reproducible")),
            }
        )
    return out[:MAX_ORIGIN_CANDIDATES]


def _provider_decisions(
    scan: importscan.ScanResult,
    verdicts: Mapping[str, _Verdict],
    arbitrated: Mapping[str, str],
    by_name: Mapping[str, list],
    to_install: set[str],
    user_entries,
    target_installed: Mapping[str, str] | None = None,
) -> tuple[dict, ...]:
    """每个第三方 import 最终装 / 认哪个发行包、凭什么，以及环境里观察到的提供者（P03）。**只披露，不决定**：
    这里读观察（`distribution_candidates`），但结果不流回 `requirements` / `missing` / `identity`——观察到的提供者是证据，不是授权
    （`test_the_observed_provider_never_becomes_an_install_name`；看护用例：有无索引时 `requirements` / `identity` 逐字相同）。
    只有「有观察」或「该 import 有互斥的备选发行包」的条目才列出。

    `target_installed`：安装目标与运行解释器不是同一个环境时给（目标的已装集合）。`satisfied` 说的是**目标里**谁在满足它——
    判决（`verdicts`）按运行解释器量，换了目标就不能沿用；目标里认不出唯一提供者（没有 / 多个且声明没点名）如实标 `undecided`。"""
    out: list[dict] = []
    for c in scan.needed:
        verdict = verdicts[c.module]
        install_name = arbitrated.get(c.module, c.distribution)
        observed = _observed_providers(c)
        if not observed and not depresolve.distribution_alternatives(c.module):
            continue
        if install_name and install_name in to_install:
            # 新一代要装它（可能当前环境里是另一个提供者在满足，所以与 `missing` 不是一回事）
            action, chosen = "install", install_name
        elif verdict.kind == _OPEN:
            action, chosen = "undecided", install_name
        else:
            # 「已满足」的提供者必须有独立的授权：被声明点名，或正是表 / 用户确认给出的安装名。
            # 只是观察到的别家发行包（如 yaml 由 yaml-fork 提供）不能借 curated 的名义披露成已满足（Codex #964）。
            def _authorized(d: str) -> bool:
                return bool(by_name.get(d)) or d == install_name

            if target_installed is None:
                present = [verdict.dist] if verdict.dist else []
            else:
                present = _providers(c, dict(target_installed))
            authorized = [d for d in present if _authorized(d)]
            if target_installed is None and not present:
                action, chosen = "satisfied", install_name
            elif len(authorized) == 1:
                action, chosen = "satisfied", authorized[0]
            else:
                action, chosen = "undecided", install_name
        if not chosen:
            continue
        basis = (
            depresolve.SOURCE_PROJECT_DECLARED
            if by_name.get(chosen)
            else (c.resolution_source or depresolve.SOURCE_CURATED)
        )
        have = {o["distribution"] for o in observed}
        out.append(
            {
                "import_name": c.module,
                "distribution": chosen,
                "basis": basis,
                "action": action,
                "observed": observed,
                "replaces_observed": action == "install" and bool(have) and chosen not in have,
            }
        )
    for entry in user_entries:
        out.append(
            {
                "import_name": entry["import_name"],
                "distribution": entry["distribution"],
                "basis": depresolve.SOURCE_USER_SPECIFIED,
                "action": "install",
                "observed": [],
                "replaces_observed": False,
            }
        )
    return tuple(out[:MAX_PROVIDER_DECISIONS])


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
    user_specified: Mapping[str, str] | None = None,
    run_env_replaceable: bool | None = None,
) -> JointPlan:
    """拼一份联合计划。`facts` 是**此刻会跑脚本的**解释器的事实（缺什么按它量——门问的是
    「现在起会话会不会缺包」）；`target_kind` 说安装会落到哪种环境（受管环境并入 adapter 约束，
    用户 venv 不并入）；`install_facts` 是**装到哪**的事实——安装目标不是 `facts` 那个环境时给
    （选中的是项目 venv / 系统解释器、目标是受管环境：active 那一代，或从 base 新建的一代——
    marker 环境与 stdlib 按 base、已装集合为空）。交给安装器的集合按它量，否则新的一代会漏装
    选中环境里碰巧有的包（Codex #461 P1）；marker 与 stdlib 也按它——脚本装完是在它里面跑。

    来源状态（PR4）：`dists` 是目标环境的静态发行包索引（默认 `static_index(facts)`，只读文件，不起进程；
    显式传 None = 不读）；`coverage(names)` 是 PR3 覆盖度的**只读缓存**查询（`userenvs.cached_probe`，没量过回 None，
    不起进程）——只用来认出「包在、导入失败」。两者都没有时判据与以前逐字相同。

    `user_specified`（Import Origin PR5b，维护者裁决 C1）：`{import 名: "包名[版本约束]"}`，用户在确认界面接受（可改）预填后
    提交的**用户指定**需求。只收「纯粹的表外名」（`_specifiable`），包名与约束过 `depresolve.from_user_input` 的严格语法；
    不满足一律抛 `UserSpecifiedRejected`，不静默丢弃。收下的那些离开 `unknown` / `origins`，作为 `resolution_source=user_specified`
    的条目进 `missing` 与 `requirements`，并列在 `JointPlan.user_specified`（披露：用户指定、未经可信来源核实）。hash 模式（锁文件语义）
    下它没有 hash，整份计划 `dependency_hashes_incomplete`。

    声明 roots（P02）：目标是受管环境时，选中的 runtime 声明（组 / marker 已按目标求值；纯 constraint 不算）本身就是安装 roots——
    不必有一条被 AST 认出的 import 指到它；目标里没有、或装着的版本不满足声明的约束，就进 `requirements`。`run_env_replaceable`
    说此刻跑脚本的环境是不是 Tavotto 自己的（受管代 / 内置 runtime / 尚未落盘的新一代）：是，**相关的**差集（见 `declared_gaps`）才让计划
    `ready`；不是（用户交给我们的系统解释器；缺省 None = 受管目标一律视为是）只记录偏差、不为它动手。用户自己的 venv 目标永不并入 roots。
    同一个 import 名有多个互斥发行包（`cv2`）时，项目声明点名的那个胜出，点名了两个 = `dependency_conflict`（roots 不与 curated 取并集）。"""
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
    hash_mode = any(it.hashes for it in selection.requirements)
    # 声明 roots 只服务受管环境（用户自己的 venv 是只进不退的原地安装，不为声明多几项去改它）；hash 模式下整份选中集合本来就都装
    roots_active = target_kind == TARGET_MANAGED and not hash_mode
    # 「计划能不能因声明而 ready」只看目标是不是受管环境：hash 模式整份锁本来就都装，相关缺口同样值得启动一次安装
    replaceable = target_kind == TARGET_MANAGED and (
        True if run_env_replaceable is None else bool(run_env_replaceable)
    )
    arbitrated, _ = _arbitrate_providers(scan.classes, by_name, None)

    # ---- 每个第三方 import 的来源判决（present / absent / open）-----------------------------
    states = _coverage_states(coverage, scan, plain_unknown)
    declared_names = frozenset(by_name)
    # 静态索引读不全（有一层没读成）：名字级查不到的不当「缺」（见 `_verdict`）。没给索引（None）= 没量，维持旧口径
    # 同样当「看不全」：site 层里有**未建模**的可执行 `.pth` 钩子 / 没跟进的路径行（`uncovered_paths > 0`，已知良性钩子已在
    # distmeta 建模、不计入）——钩子可能装了自定义 finder，名字层里没有的 import 不等于没装（#925）
    index_incomplete = (
        isinstance(dists, distmeta.Index)
        and dists.checked
        and (not dists.names_complete or dists.uncovered_paths > 0)
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
        dist = (
            verdict.dist if verdict.kind == _PRESENT else arbitrated.get(c.module, c.distribution)
        )
        declared_for = by_name.get(dist, [])
        entry = {
            "import_name": c.module,
            "distribution": dist,
            # 仲裁让声明点名的 provider 替掉了表里的名字时，装它的依据是声明，不是表（P03：回执 / 披露不能把它记成 curated）
            "resolution_source": _source_of(c, dist, arbitrated),
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

    # 表外名已被环境里装好的唯一 index 来源发行包满足（#938）：不进 unknown / origins，也不产生安装名
    # 只在运行目标 == 安装目标时成立：装到另一个环境（新托管代）时，当前解释器里装着的包不会出现在那里，
    # 表外名既不在 requirements 也无安装名，激活后会 import 不到——维持来源未定（Codex #944 P1）
    same_target = install_facts is None or install_facts is facts
    index_satisfied = {
        c.module
        for c in scan.classes
        if same_target
        and c.module in plain_unknown
        and c.module not in open_classes
        and _installed_index_satisfies(c, installed, states.get(c.module, ""), index_incomplete)
    }
    origin_classes = [
        c
        for c in scan.classes
        if c.module in open_classes
        or (c.module in plain_unknown and c.module not in index_satisfied)
    ]
    # 脚本字面量加进 sys.path 的目录（`importscan` 的 `sys_path_literal` 搜索根）：那里没找到的 import 是「本地模块缺失」，
    # 不预填包名、不接受用户指定、绝不送 pip（ADR 0061 修订 2026-10-21）
    literal_dirs = [
        str(r.get("path") or ".") for r in scan.search_roots if r.get("tag") == "sys_path_literal"
    ]
    specifiable = {
        c.module
        for c in origin_classes
        if not literal_dirs
        and depresolve.not_pip_installable(c.module) is None
        and _specifiable(
            c,
            _effective_reason(c, open_classes.get(c.module, ORIGIN_UNRESOLVED)),
            states.get(c.module, ""),
            index_incomplete,
        )
    }
    chosen: dict[str, tuple[depresolve.DependencyIntent, importscan.ImportClass]] = {}
    for imp, text in sorted((user_specified or {}).items()):
        cls = next((c for c in origin_classes if c.module == imp), None)
        if cls is None or imp not in specifiable:
            raise UserSpecifiedRejected(str(imp), UserSpecifiedRejected.NOT_APPLICABLE)
        asked = depresolve.from_user_input(imp, str(text or ""))
        if asked is not None and depresolve.not_pip_for_distribution(asked.distribution):
            raise UserSpecifiedRejected(str(imp), UserSpecifiedRejected.NOT_APPLICABLE)
        intent = (
            depresolve.parse_intent(asked.requirement())
            if asked is not None and asked.installable
            else None
        )
        if intent is None or not intent.declared:
            raise UserSpecifiedRejected(str(imp), UserSpecifiedRejected.INVALID)
        chosen[imp] = (intent, cls)
    origins = tuple(
        _origin_entry(
            c,
            open_classes.get(c.module, ORIGIN_UNRESOLVED),
            states.get(c.module, ""),
            suggest=depresolve.suggest_distribution(c.module) if c.module in specifiable else "",
            literal_dirs=literal_dirs,
        )
        for c in origin_classes
        if c.module not in chosen
    )
    unknown = tuple(o["import_name"] for o in origins)
    user_entries = tuple(
        {
            "import_name": imp,
            "distribution": intent.name,
            "resolution_source": depresolve.SOURCE_USER_SPECIFIED,
            "declared": False,
            "specifiers": [intent.specifier] if intent.specifier else [],
            "via": list(cls.via),
        }
        for imp, (intent, cls) in chosen.items()
    )
    missing.extend(user_entries)

    # ---- 交给安装器的集合（按**装到哪**量：目标已有的不装、其余 needed 的全装） --------------
    # 来源判决（`verdicts`）只说**当前解释器**里这个 import 是谁提供的，只能抑制当前环境的 missing。装到当前环境
    # （`install_facts` 是 None / 就是 `facts`）时它同样决定装不装：来源未定的（open）一概不装，其余按已装集合的名字级判据。
    # 装到**另一个**环境（新托管代 / active 那一代）时当前环境里的 editable / 本地 / Conda 提供者、缓存的 import_error、
    # 读不全的索引都不在那里——新代没有那个提供者，不装就会 import 失败、验证失败（Codex #920 P1）。所以按目标的已装集合
    # 单独判：目标里名字级已有的不装，其余按可信安装名（表 / 声明，`c.distribution`）装；从不拿已装元数据造名字。
    # 当前环境里是 editable 等、可信解析又给不出安装名的 import（`unknown` 桶）本来就不在 `scan.needed`：它们留在
    # `unknown` / `origins`（带 editable / 本地 / Conda 的来源状态），如实标出新环境装不出来，不猜 PyPI 名。
    to_install = {
        arbitrated.get(c.module, c.distribution)
        for c in scan.needed
        if c.distribution
        and (same_target is False or verdicts[c.module].kind != _OPEN)
        and not _providers(c, target_installed)
        # 点名了 provider 的 import：装哪个由下面的声明 roots 说了算（目标里没有它才装），不再回头装 curated 的名字
        and not (roots_active and c.module in arbitrated)
    }
    # ---- 声明 roots（P02）：选中的声明本身是新托管环境的安装根；扫描结果只补充与解释，不否决 -------------------------
    relevant = _relevant_declarations(by_name, scan, arbitrated, plain_unknown)
    declared_install: set[str] = set()
    if target_kind == TARGET_MANAGED:
        declared_install = {
            name
            for name, items in by_name.items()
            if name in relevant and _root_state(items, target_installed)[0]
        }
    if roots_active:
        to_install |= declared_install
    # 已知装不了的发行包（`depresolve.NOT_PIP_INSTALLABLE`）：计划阶段点名（来自哪个声明文件 / 哪个 import），不送 pip
    not_pip_entries: list[dict] = []
    # 候选 = 要装的 + 声明 roots + 本计划 missing 里点到的 + hash 模式下整份选中集合：任何一处都不许把它们带进 pip（Codex #1276 P1）
    not_pip_candidates = (
        to_install
        | declared_install
        | {str(m.get("distribution") or "") for m in missing}
        | (set(by_name) if hash_mode else set())
    )
    not_pip_names: set[str] = set()
    for name in sorted(n for n in not_pip_candidates if n):
        hit = depresolve.not_pip_for_distribution(name)
        if hit is None:
            continue
        not_pip_names.add(name)
        to_install.discard(name)
        declared_install.discard(name)
        not_pip_entries.append(
            {
                "name": hit["label"],
                "distribution": name,
                "hint": hit["hint"],
                "sources": sorted({it.source for it in by_name.get(name, ()) if it.source})[:4],
                "imports": sorted(
                    c.module
                    for c in scan.classes
                    if arbitrated.get(c.module, c.distribution) == name
                )[:4],
            }
        )
    if not_pip_names:
        # missing 里同样不留：否则计划仍是 ready、空需求集、装后核验 ROOT 必失败
        missing[:] = [m for m in missing if str(m.get("distribution") or "") not in not_pip_names]
    # 本计划实际会装的名字：hash 模式 = 整份选中集合（去掉装不了的）；否则 = 上面算出的 to_install（含按声明装的 roots）
    installing = (set(by_name) - not_pip_names) if hash_mode else set(to_install)
    _, provider_conflicts = _arbitrate_providers(scan.classes, by_name, installing)
    missing_imports = {m["import_name"] for m in missing}
    install_only = tuple(
        {
            "import_name": c.module,
            "distribution": arbitrated.get(c.module, c.distribution),
            "resolution_source": _source_of(
                c, arbitrated.get(c.module, c.distribution), arbitrated
            ),
            "declared": bool(by_name.get(arbitrated.get(c.module, c.distribution))),
            "specifiers": sorted(
                {
                    it.specifier
                    for it in by_name.get(arbitrated.get(c.module, c.distribution), [])
                    if it.specifier
                }
            ),
            "via": list(c.via),
        }
        for c in scan.needed
        # hash 锁会安装整份集合：active 中的互斥 provider 不能让新代回执丢掉这个 import（#970）。
        if arbitrated.get(c.module, c.distribution) in installing
        and c.module not in missing_imports
    )
    if target_kind == TARGET_MANAGED:
        # 没有任何 import 条目指到的声明 root：照样进账（重建时装得回来）与装后核验（发行包 / 版本），import 名未知就留空
        # hash 锁会重装整份集合，但这里只登记既有判据选中的相关声明，不把无关锁条目抬成 roots（#963）。
        recorded_roots = (relevant & installing) if hash_mode else to_install
        covered = {m["distribution"] for m in (*missing, *install_only)}
        modules_of = {
            arbitrated.get(c.module, c.distribution): c.module
            for c in scan.classes
            if c.distribution or c.module in arbitrated
        }
        install_only = (
            *install_only,
            *(
                {
                    "import_name": modules_of.get(name, ""),
                    "distribution": name,
                    "resolution_source": depresolve.SOURCE_PROJECT_DECLARED,
                    "declared": True,
                    "specifiers": sorted({it.specifier for it in by_name[name] if it.specifier}),
                    "via": [],
                }
                for name in sorted(recorded_roots)
                if name in by_name and name not in covered
            ),
        )
    reqs: list[str] = []
    hashes: dict[str, list[str]] = {}
    cons: list[str] = []
    blocked: list[dict] = []
    if hash_mode:
        # hash 模式 = 锁文件语义：整份选中集合就是闭包，全部按 hash 装（pip 要求每一条
        # 都有 hash，缺一条整次拒绝——那是它的判据，这里提前说出来）。
        without = [
            it for it in selection.requirements if not it.hashes and it.name not in not_pip_names
        ]
        if without:
            blocked.append(
                {
                    "code": BLOCK_HASHES_INCOMPLETE,
                    "lines": [it.raw for it in without][:20],
                    "count": len(without),
                }
            )
        for it in selection.requirements:
            if it.name in not_pip_names:
                continue
            text = depresolve.requirement_string(it)
            if text not in reqs:
                reqs.append(text)
            hashes.setdefault(text, [])
            for h in it.hashes:
                if h not in hashes[text]:
                    hashes[text].append(h)
        if user_entries:
            # 用户指定的需求没有 hash：锁文件语义下 pip 会整次拒绝——提前说出来（复用既有理由，不新增 `BLOCK_REASONS`）
            blocked.append(
                {
                    "code": BLOCK_HASHES_INCOMPLETE,
                    "user_specified": [e["import_name"] for e in user_entries],
                    "count": len(user_entries),
                }
            )
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
        for intent, _cls in chosen.values():
            text = depresolve.requirement_string(intent)
            if text not in reqs:
                reqs.append(text)
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
    if selection.conflicts or provider_conflicts:
        blocked.append(
            {
                "code": BLOCK_CONFLICT,
                "conflicts": [dict(c) for c in (*selection.conflicts, *provider_conflicts)],
            }
        )
    # blocked 优先于「没缺的」：选中的声明里有 unsupported / 矛盾 / 缺 hash / 目标量不出，这份
    # 计划就不能自称完整——哪怕此刻什么都不缺（Codex #459 P2）。要不要装看 `missing`。
    gaps = _declared_gaps(by_name, installed if facts is not None else None, relevant)
    # 相关的差集 + 此刻跑脚本的是我们自己的环境 + 目标里确实没有 → 值得启动一次真实的安装任务（不只是改 requirements 数组）
    root_trigger = replaceable and any(
        g["relevant"] and g["distribution"] in declared_install for g in gaps
    )
    if blocked:
        status = STATUS_BLOCKED
    elif missing or root_trigger:
        status = STATUS_READY
    else:
        status = STATUS_NOTHING_NEEDED
    gaps_total = len(gaps)
    gaps = tuple(
        {
            **g,
            "action": GAP_ACTION_INSTALL
            if status == STATUS_READY
            and (g["distribution"] in to_install or g["distribution"] in declared_install)
            else GAP_ACTION_RECORDED,
        }
        for g in gaps[:MAX_DECLARED_GAPS]
    )
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
        user_specified=tuple(
            {
                "import_name": e["import_name"],
                "distribution": e["distribution"],
                "specifier": e["specifiers"][0] if e["specifiers"] else "",
                "requirement": f"{e['distribution']}{e['specifiers'][0] if e['specifiers'] else ''}",
                "resolution_source": depresolve.SOURCE_USER_SPECIFIED,
                "verified_source": False,
            }
            for e in user_entries
        ),
        install_only=install_only,
        declared_gaps=gaps,
        provider_decisions=_provider_decisions(
            scan,
            verdicts,
            arbitrated,
            by_name,
            installing,
            user_entries,
            None if same_target else target_installed,
        ),
        declared_gaps_total=gaps_total,
        not_pip_installable=tuple(not_pip_entries),
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
    return dataclasses.replace(
        facts,
        installed=installed,
        prefix="",
        executable="",
        site_roots=(),
        extra_roots=(),
        opaque_roots=0,
        pth_roots=(),
        path_order=(),
        skipped_roots=(),
        layers="",
    )


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
