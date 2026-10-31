"""脚本要的 Python 版本与当前环境不合：自动另建一个版本合适的 Tavotto 管理环境来跑（ADR 0114 修订 2026-10-23）。

维护者裁决（2026-10-23）：「换 Python 版本要不要每次问？」→ **不问，直接换**。护栏（本模块与调用方必须守）：

* **只在 Tavotto 自己的管理环境里另建**（`managedenv` 新一代，`deprepair` 的代事务）；用户的环境 / 系统 Python /
  conda 一个字节不碰，原环境原样保留。目标永远是 `deprepair.TARGET_MANAGED`。
* **基础解释器只有一个挑选处**：`managedenv.base_pick(within=)`——先找合格的系统 Python（范围内取最高），没有再用私有
  Python（`privatepython`：已就位 / 缓存 / 要下载，下载字节如实进计划）。私有 Python 的锁里只有**一个**版本：它不在
  需要的范围内就不是候选（不能凭空下载别的版本，锁里没有它的 sha256）。
* **只针对「版本不合」这一类失败**（`FAILURE_CODES`）；**需要的范围推不出来就不换**（`required_range` 回 None），
  不猜——交给后面的 AI 修复 / 卡片。
* 范围来源只有现成信息：失败本身带的事实（ROOT 为哪个 Python 编译、环境是哪个版本）、Tavotto 的支持区间、脚本的
  PEP 723 `requires-python`、项目 `pyproject.toml` 的 `requires-python`。解析不了的约束 = 推不出 = 不换。
* 每次运行最多换一次（调用方 `prepsession` 记账，本模块无状态）；换完记住这个环境走的是现有的代事务
  （`projectenv.remember`）；新环境里补依赖走现有联合计划（`create_joint_plan` + `prepare`），名单内包的口径不变。
* 取消可停；失败不留半成品（代事务把未提交的一代标 incomplete，active 不动）。

标准库 + `packaging`（Flask 父进程运行时闭包里本来就有；补丁位约束按完整版本用 `packaging.specifiers` 判，不自写）。
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import threading
from pathlib import Path
from typing import Callable

from . import deprepair, managedenv, pool, privatepython, projectenv, rootenv

LOG = logging.getLogger("tavotto.pyversionfix")

#: 运维开关：`0` / `false` / `off` / `no` 整个关掉自动换版本（同 `TAVOTTO_EXECUTION_FALLBACK` 的写法）。
ENABLE_ENV = "TAVOTTO_AUTO_PYTHON_CHANGE"
_OFF = frozenset({"0", "false", "off", "no"})

# ---------------------------------------------------------------- 失败词汇（闭集，只含「版本不合」）
CODE_UNSUPPORTED = projectenv.ERROR_UNSUPPORTED_PYTHON  # "project_env_unsupported_python"
CODE_OUT_OF_RANGE = "python_out_of_range"  # `userenvs.REJECT_PYTHON_OUT_OF_RANGE`
CODE_ROOT_MISMATCH = "root_python_mismatch"  # `userenvs.REJECT_ROOT_PYTHON_MISMATCH`
CODE_UNSUPPORTED_VERSION = "unsupported_python_version"  # `runcodes.UNSUPPORTED_PYTHON_VERSION`
FAILURE_CODES = frozenset(
    {CODE_UNSUPPORTED, CODE_OUT_OF_RANGE, CODE_ROOT_MISMATCH, CODE_UNSUPPORTED_VERSION}
)

BASE_SYSTEM = "system"
BASE_PRIVATE = "private"

#: 进度阶段（闭集）。`downloading` / `verifying` / `extracting` 是私有 Python 的；其余是代事务的。
STAGE_DOWNLOADING = privatepython.STAGE_DOWNLOADING
STAGE_PREPARING = "preparing"
STAGE_CREATING = "creating"
STAGE_INSTALLING = "installing"
STAGE_VERIFYING = "verifying"

ERROR_CANCELLED = "python_change_cancelled"
ERROR_FAILED = "python_change_failed"
ERROR_UNAVAILABLE = "python_change_unavailable"

Minor = tuple[int, int]
MinorRange = tuple[Minor, Minor]  # 半开 [lo, hi)


# ---------------------------------------------------------------- 计划
@dataclasses.dataclass(frozen=True)
class PythonChangePlan:
    """要换成哪个 Python、基础解释器从哪来、要下载多少字节。`public()` 没有路径（ADR 0053 §二）。"""

    from_version: str  # 失败的环境的主.次；不知道回空串
    to_version: str  # 主.次
    base: str  # BASE_SYSTEM | BASE_PRIVATE
    download_bytes: int  # 私有 Python 要联网下载的字节；系统 / 已就位 / 本地缓存为 0
    code: str  # 触发它的失败码（FAILURE_CODES）
    project: str
    script: str
    #: 私有 Python 的来源（`privatepython.ORIGINS` / `REQUIRE_PRESENT`）：执行时只认这一处
    private_origin: str = ""
    #: 系统基础解释器的路径（私有 Python 就位后才知道，计划里为空）。私有，不进 `public()`
    base_python: str = ""

    def public(self) -> dict:
        return {
            "from": self.from_version,
            "to": self.to_version,
            "base": self.base,
            "download_bytes": int(self.download_bytes),
        }


# ---------------------------------------------------------------- 范围推断
def _parse_minor(text: object) -> Minor | None:
    m = re.fullmatch(r"\s*(\d+)\.(\d+)(?:\.\d+)*\s*", str(text or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _next_minor(v: Minor) -> Minor:
    return (v[0], v[1] + 1)


@dataclasses.dataclass(frozen=True)
class _Span:
    lo: Minor
    hi: Minor
    excluded: frozenset[Minor] = frozenset()
    #: 带补丁位的 specifier 子句（`>=3.11.2` / `==3.12.1` …）原串：小版本区间装不下它们，选解释器时按**完整版本**判
    #: （`packaging.specifiers`，ADR 0114 §七）。没有补丁位的子句不在这里（它们已被区间完整表达）
    patches: tuple[str, ...] = ()

    def meet(self, other: "_Span") -> "_Span":
        return _Span(
            max(self.lo, other.lo),
            min(self.hi, other.hi),
            self.excluded | other.excluded,
            tuple(dict.fromkeys((*self.patches, *other.patches))),
        )

    def patch_spec(self) -> str:
        return ",".join(self.patches)

    def minors(self) -> list[Minor]:
        """范围内（含上界之下）的小版本，从高到低；只数同一个主版本（3.x）。"""
        out: list[Minor] = []
        if self.lo[0] != self.hi[0] and self.hi[0] != self.lo[0] + 1:
            return out
        major = self.lo[0]
        top = self.hi[1] - 1 if self.hi[0] == major else 99
        for minor in range(top, self.lo[1] - 1, -1):
            if (major, minor) not in self.excluded and self.lo <= (major, minor) < self.hi:
                out.append((major, minor))
        return out


_SPEC = re.compile(r"^(~=|==|!=|>=|<=|>|<)\s*(\d+(?:\.\d+)*)(\.\*)?$")


def parse_requires_python(text: str) -> _Span | None:
    """PEP 440 的 `requires-python` → 小版本级的半开区间；**任何一段读不懂都回 None**（推不出就不换，不猜）。

    补丁号不进区间（区间只到小版本），但带补丁位的子句原样记进 `patches`，选解释器时按完整版本判（`>=3.11.2,<3.12` 不会
    选到 3.11.0）。区间上：`>=3.11.2` 当 `>=3.11`；`<=3.12` / `<=3.12.4` 当 `<3.13`（作者写 `<=3.12` 几乎
    都是「3.12 都行」）；`>3.11` 当 `>=3.12`（更严，宁可少选）；`!=3.12.*` / `!=3.12` 把 3.12 整个排除。"""
    span = _Span((0, 0), (99, 0))
    patches: list[str] = []
    parts = [p for p in re.split(r"\s*,\s*", str(text or "").strip()) if p]
    if not parts:
        return None
    for part in parts:
        m = _SPEC.match(part.strip())
        if not m:
            return None
        op, num, star = m.group(1), m.group(2), m.group(3)
        nums = [int(x) for x in num.split(".")]
        if len(nums) >= 3:
            patches.append(f"{op}{num}{star or ''}")
        if len(nums) < 2 and not (op in (">=", ">", "<", "<=", "==") and len(nums) == 1):
            return None
        major = nums[0]
        minor = nums[1] if len(nums) > 1 else None
        if minor is None:
            # 只写了主版本：`>=3` / `==3.*`（`.*` 已被 group(3) 吃掉，所以这里是 `==3` + star）
            cur: _Span | None
            if op == ">=":
                cur = _Span((major, 0), (99, 0))
            elif op == "<":
                cur = _Span((0, 0), (major, 0))
            elif op == "<=":
                cur = _Span((0, 0), (major + 1, 0))
            elif op == "==" and star:
                cur = _Span((major, 0), (major + 1, 0))
            else:
                return None
        elif op == ">=":
            cur = _Span((major, minor), (99, 0))
        elif op == ">":
            cur = _Span((major, minor + 1), (99, 0))
        elif op == "<":
            cur = _Span((0, 0), (major, minor))
        elif op == "<=":
            cur = _Span((0, 0), (major, minor + 1))
        elif op == "==":
            # `==3.12.4`：小版本是 3.12，补丁号钉死——系统里的 3.12.x 不一定是它，由 `patches` 在选解释器时按完整版本判
            cur = _Span((major, minor), (major, minor + 1))
        elif op == "~=":
            cur = (
                _Span((major, minor), (major, minor + 1))
                if len(nums) >= 3
                else _Span((major, minor), (major + 1, 0))
            )
        else:  # "!="
            if star or len(nums) == 2:
                cur = _Span((0, 0), (99, 0), frozenset({(major, minor)}))
            else:
                # `!=3.12.4` 排除的只是一个补丁号，对小版本的选择没有约束
                cur = _Span((0, 0), (99, 0))
        span = span.meet(cur)
    return dataclasses.replace(span, patches=tuple(dict.fromkeys(patches)))


_PEP723_BLOCK = re.compile(r"(?m)^# /// script\s*$(?P<body>(?:\n#(?: .*)?$)*?)\n# ///\s*$")
_READ_LIMIT = 256 * 1024


def _read_text(root: str, path: Path) -> str | None:
    """只读、有界、钉在项目根内（`projectenv.contained_path` 是这类路径的唯一净化器）。"""
    safe = projectenv.contained_path(root, path)
    if safe is None:
        return None
    try:
        with open(safe, "rb") as fh:
            return fh.read(_READ_LIMIT).decode("utf-8", errors="replace")
    except OSError:
        return None


def _declared_requires_python(root: str, script: str) -> list[str] | None:
    """项目 / 脚本里声明的 `requires-python` 原串（可能多份）。`[]` = 没有任何声明；None = 声明在但读不出 / 不是字符串
    （推不出）。只读：PEP 723 内联块 + 从脚本所在目录向上到项目根的第一个 `pyproject.toml`。"""
    found: list[str] = []
    if script:
        script_path = Path(root) / script
        text = _read_text(root, script_path)
        if text:
            m = _PEP723_BLOCK.search(text)
            if m:
                body = "\n".join(
                    line[2:] if line.startswith("# ") else line[1:]
                    for line in m.group("body").splitlines()
                    if line.startswith("#")
                )
                spec = re.search(r'(?m)^requires-python\s*=\s*["\']([^"\']*)["\']\s*$', body)
                if spec:
                    found.append(spec.group(1))
        start = script_path.parent
    else:
        start = Path(root)
    real_root = os.path.realpath(root)
    here = Path(os.path.realpath(start))
    for directory in (here, *here.parents):
        if directory != Path(real_root) and Path(real_root) not in directory.parents:
            break
        text = _read_text(root, directory / "pyproject.toml")
        if text is not None:
            value = _pyproject_requires_python(text)
            if value is False:
                return None
            if isinstance(value, str):
                found.append(value)
                break
        if directory == Path(real_root):
            break
    return found


def _pyproject_requires_python(text: str) -> str | bool | None:
    """`[project].requires-python`：字符串 / None（没写）/ False（写了但读不懂）。"""
    try:
        import tomllib  # noqa: PLC0415 — 3.11+；3.10 走下面的正则
    except ImportError:
        tomllib = None  # type: ignore[assignment]
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
        except Exception:  # noqa: BLE001 — 坏的 pyproject = 读不出
            return False
        value = (data.get("project") or {}).get("requires-python")
        if value is None:
            return None
        return value if isinstance(value, str) else False
    # 3.10 没有 tomllib：正则兜底只认最常见的写法；表头长得不像 TOML（`[[[`、没闭合）就当「读不懂」，宁可不换
    in_project = False
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith("["):
            if not re.fullmatch(r"\[\[?[A-Za-z0-9_.\-\s\"']+\]\]?", stripped):
                return False
            in_project = stripped == "[project]"
            continue
        if (
            in_project
            and stripped.startswith("requires-python")
            and not re.match(r'requires-python\s*=\s*["\'][^"\']*["\']\s*$', stripped)
        ):
            return False
        m = re.match(r'requires-python\s*=\s*["\']([^"\']*)["\']\s*(?:#.*)?$', stripped)
        if in_project and m:
            return m.group(1)
    return None


def _supported_span() -> _Span:
    return _Span(projectenv.PYTHON_MIN, projectenv.PYTHON_MAX_EXCLUSIVE)


def required_span(failure: dict, project: str | Path) -> _Span | None:
    """这次失败需要的 Python 小版本范围；推不出回 None。

    起点：`root_python_mismatch` 要 ROOT 为之编译的那个小版本（`failure["root_python"]`，读不出就推不出）；
    其余「版本不合」码起点是 Tavotto 的支持区间。再与声明的 `requires-python` 求交（`failure["requires_python"]` 优先
    当作已知事实，同时读脚本 / 项目的声明）。交集为空、或失败的环境其实已经在范围里（换了也没用）都是推不出。"""
    code = str(failure.get("code") or "")
    if code not in FAILURE_CODES:
        return None
    span = _supported_span()
    if code == CODE_ROOT_MISMATCH:
        built = _parse_minor(failure.get("root_python"))
        if built is None:
            return None
        span = span.meet(_Span(built, _next_minor(built)))
    declared: list[str] = []
    if failure.get("requires_python"):
        declared.append(str(failure["requires_python"]))
    from_files = _declared_requires_python(str(project), str(failure.get("script") or ""))
    if from_files is None:
        return None
    declared.extend(from_files)
    for text in declared:
        parsed = parse_requires_python(text)
        if parsed is None:
            return None
        span = span.meet(parsed)
    if not span.minors():
        return None
    current = _parse_minor(failure.get("env_python") or failure.get("python_version"))
    if current is not None and current in span.minors() and code != CODE_UNSUPPORTED_VERSION:
        # 环境的版本其实就在范围里：问题不在版本，换一个同版本的环境不会变好
        return None
    return span


def auto_change_enabled() -> bool:
    raw = os.environ.get(ENABLE_ENV, "").strip().lower()
    return raw not in _OFF


# ---------------------------------------------------------------- 入口 1：计划（只读，不联网、不起重进程）
def plan_python_change(failure: dict, project: str | Path) -> PythonChangePlan | None:
    """`failure` → 要不要换、换成什么。回 None = 不自动换（交给后续的 AI 修复 / 卡片）。

    `failure`：`{"code": FAILURE_CODES 之一, "script": 项目相对脚本路径, "root_python"?: "3.12", "env_python"?:
    "3.14", "python_version"?: 失败环境的版本, "requires_python"?: "已知的 requires-python 原串"}`。

    只读：看磁盘上的声明文件、问 `managedenv.base_pick`（系统候选的版本探测）、看私有 Python 锁与磁盘。**不下载**。"""
    if not auto_change_enabled():
        return None
    if pool.explicit_worker_python():
        return None  # 全局显式解释器压着项目级决定：换了也不会被用（与 deprepair._refuse_if_pinned 同一条判据）
    root = str(Path(project))
    span = required_span(failure, root)
    if span is None:
        return None
    minors = span.minors()
    within = (minors[-1], _next_minor(minors[0]))
    from_version = ".".join(
        str(failure.get("env_python") or failure.get("python_version") or "").split(".")[:2]
    )
    code = str(failure.get("code") or "")
    script = str(failure.get("script") or "")

    spec = span.patch_spec()
    if spec and not _valid_spec(spec):
        return None  # 补丁位约束读不懂 = 推不出
    system = _system_pick(within, span)
    if system is not None:
        python, version = system
        return PythonChangePlan(
            from_version=from_version,
            to_version=version,
            base=BASE_SYSTEM,
            download_bytes=0,
            code=code,
            project=root,
            script=script,
            base_python=python,
        )
    source = privatepython.source_for()
    if source is None or not privatepython.offered(source):
        return None
    version = ".".join(source.version.split(".")[:2])
    if _parse_minor(version) not in minors:
        return None  # 锁里只有一个私有 Python 版本：不在范围内就没有可供应的
    if not _full_version_ok(source.version, spec):
        return None  # 锁版本是精确补丁版本：补丁位约束同样按它判（不满足就不下载）
    present = privatepython.python_of(source) is not None
    offer = None if present else privatepython.offer_payload(source)
    if not present and offer is None:
        return None
    return PythonChangePlan(
        from_version=from_version,
        to_version=version,
        base=BASE_PRIVATE,
        download_bytes=int(offer["download_bytes"]) if offer else 0,
        code=code,
        project=root,
        script=script,
        private_origin=privatepython.REQUIRE_PRESENT if present else str(offer["origin"]),
    )


def _valid_spec(spec: str) -> bool:
    from packaging.specifiers import InvalidSpecifier, SpecifierSet

    try:
        SpecifierSet(spec)
    except InvalidSpecifier:
        return False
    return True


def _full_version_ok(version: str, spec: str) -> bool:
    """完整版本（「主.次.微」）满足补丁位约束吗？没有约束恒真；拿不到完整版本而有约束 = 不满足（宁可不换）。"""
    if not spec:
        return True
    parts = str(version or "").strip().split(".")
    if len(parts) != 3 or not all(x.isdigit() for x in parts):
        return False
    from packaging.specifiers import SpecifierSet

    return SpecifierSet(spec).contains(".".join(parts), prereleases=True)


def _system_pick(within: MinorRange, span: _Span) -> tuple[str, str] | None:
    """范围内（排除 `!=` 的）最高的合格系统 Python。私有 Python（已就位的）在 `managedenv.base_pick` 里是兜底，
    这里把它排除出系统一支——它走计划里的私有 Python 一支（披露口径不同）。"""
    pick = managedenv.base_pick(within=within, patch_spec=span.patch_spec())
    if pick is None:
        return None
    python, version = pick
    minor = _parse_minor(version)
    if minor is None or minor not in span.minors():
        return None
    source = privatepython.source_for()
    if source is not None and privatepython.python_of(source) == python:
        return None
    return python, version


# ---------------------------------------------------------------- 入口 2：执行
ProgressFn = Callable[[dict], None]


def apply_python_change(
    plan: PythonChangePlan, progress: ProgressFn | None, cancel: threading.Event | None
) -> dict:
    """执行计划：（需要就）供应私有 Python → 在 Tavotto 的受管环境里**另建一代**（该 Python 为基础）→ 按现有联合计划
    补齐脚本要的依赖 → 验过才切 active（并记住为本项目的解释器）。回 `{"ok": bool, "code": str, "to": str}`；
    取消 / 失败时 active 不动、未提交的一代留成 incomplete（代事务，`deprepair._run_generation`），用户的环境没被碰过。

    `progress(dict)`：`{"stage": STAGE_*, "done": int, "total": int}`，在调用线程上同步回调。"""
    cancel = cancel or threading.Event()

    def note(stage: str, done: int = 0, total: int = 0) -> None:
        if progress is not None:
            try:
                progress({"stage": stage, "done": int(done), "total": int(total)})
            except Exception:  # noqa: BLE001 — 进度回调不许让换环境失败
                LOG.debug("换 Python 的进度回调异常", exc_info=True)

    def failed(code: str) -> dict:
        return {"ok": False, "code": code, "to": plan.to_version}

    if cancel.is_set():
        return failed(ERROR_CANCELLED)
    note(STAGE_PREPARING)
    base = plan.base_python
    if plan.base == BASE_PRIVATE:
        try:
            base = privatepython.provision(
                privatepython.source_for(),
                cancel_ev=cancel,
                on_progress=lambda stage, done, total: note(stage, done, total),
                required_origin=plan.private_origin or None,
            )
        except privatepython.ProvisionError as exc:
            if exc.code == privatepython.ERROR_CANCELLED:
                return failed(ERROR_CANCELLED)
            LOG.warning("换 Python：私有 Python 供应失败 %s", exc.code)
            return failed(exc.code)
    if not base:
        return failed(ERROR_UNAVAILABLE)
    if cancel.is_set():
        return failed(ERROR_CANCELLED)
    return _build_generation(plan, base, note, cancel)


def _run_prepare(plan_id: str, on_event, digest: str):
    """执行这一代的联合计划。摘要由调用方传入：这里的授权来自维护者 2026-10-23 的裁决（版本不合时**不问、直接换**，
    且只在 Tavotto 自己的受管环境里另建、绝不碰用户环境），所以绑定的是同一次调用里刚算出的计划摘要——
    与 `convertdeps._run_prepare` 对「已同意的环境」同一口径；`tests/test_dependency_executor_entry.py` 逐个登记。"""
    return deprepair.prepare(plan_id, on_event, confirmed_impact=digest)


def _build_generation(
    plan: PythonChangePlan, base: str, note: Callable[..., None], cancel: threading.Event
) -> dict:
    """钉住基础解释器后走现有联合计划：`create_joint_plan`（目标强制受管）→ `prepare`（代事务）。"""
    stage_of = {
        deprepair.STATE_PREPARING: STAGE_PREPARING,
        deprepair.STATE_CREATING_ENV: STAGE_CREATING,
        deprepair.STATE_INSTALLING: STAGE_INSTALLING,
        deprepair.STATE_VERIFYING: STAGE_VERIFYING,
    }
    if cancel.is_set():  # 阶段边界：建一代之前再查一次
        return {"ok": False, "code": ERROR_CANCELLED, "to": plan.to_version}
    done = threading.Event()
    watched: dict[str, str] = {}

    def on_event(snap: dict) -> None:
        stage = stage_of.get(str(snap.get("state") or ""))
        if stage:
            note(stage)

    def watch() -> None:
        # 取消事件在别的线程上被置：转成代事务认的取消（提交点之前才算数，之后 `cancel_status` 会拒绝）
        while not done.is_set():
            if cancel.wait(0.2) and watched.get("plan_id"):
                deprepair.cancel_status(watched["plan_id"])
                return

    watcher = threading.Thread(target=watch, daemon=True, name="tavotto-pychange-cancel")
    try:
        with deprepair.pinned_base(base):
            joint = deprepair.create_joint_plan(
                plan.project, plan.script, target_kind=deprepair.TARGET_MANAGED
            )
            if cancel.is_set():  # 阶段边界：联合计划只是计划，此刻停下什么都没建
                return {"ok": False, "code": ERROR_CANCELLED, "to": plan.to_version}
            watched["plan_id"] = joint.plan_id
            watcher.start()
            result = _run_prepare(joint.plan_id, on_event, joint.impact_digest)
    except deprepair.RepairError as exc:
        LOG.warning("换 Python：受管环境准备失败 %s", exc.code)
        return {"ok": False, "code": exc.code, "to": plan.to_version}
    finally:
        done.set()
    if not isinstance(result, dict) or not result.get("ok"):
        code = str((result or {}).get("code") or "")
        return {
            "ok": False,
            "code": ERROR_CANCELLED if code == deprepair.ERROR_CANCELLED else code or ERROR_FAILED,
            "to": plan.to_version,
        }
    return {"ok": True, "code": "", "to": plan.to_version}


# ---------------------------------------------------------------- 从会话事实里认出「版本不合」
def failure_from_report(
    *, script: str, error: dict | None, not_used: list[dict] | None
) -> dict | None:
    """一次尝试以 `error`（`PreparationResult.error`）收场、计划的环境事实里有落选原因 `not_used`
    （`envadvice._public_rejection` 的形状）→ `plan_python_change` 要的 `failure`；不是「版本不合」这一类回 None。

    * 失败码本身就是版本类（`FAILURE_CODES`）：直接用，版本事实取自错误参数 / 落选条目里的 `env_python`。
    * 脚本因 `ROOT` 缺失而收场（`missing_dependency` 且缺的是 `rootenv.IMPORT_NAME`），而落选里有 `root_python_mismatch`
      且两边小版本都读得出（#1333 带的 `root_python` / `env_python`）：失败 = ROOT 版本不合。读不出两边版本就不认。"""
    err = error or {}
    code = str(err.get("code") or "")
    params = err.get("params") if isinstance(err.get("params"), dict) else {}
    rows = [r for r in (not_used or []) if isinstance(r, dict)]
    if code in FAILURE_CODES:
        env_python = next((str(r["env_python"]) for r in rows if r.get("env_python")), "")
        return {
            "code": code,
            "script": script,
            "env_python": str(params.get("version") or params.get("env_python") or env_python),
        }
    if code == "missing_dependency" and str(err.get("module") or "") == rootenv.IMPORT_NAME:
        for r in rows:
            if (
                r.get("reason") == CODE_ROOT_MISMATCH
                and r.get("root_python")
                and r.get("env_python")
            ):
                return {
                    "code": CODE_ROOT_MISMATCH,
                    "script": script,
                    "root_python": str(r["root_python"]),
                    "env_python": str(r["env_python"]),
                }
    return None
