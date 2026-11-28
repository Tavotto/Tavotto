"""「在这个环境里装 X 会得到哪一版、为什么不是最新版」（ADR 0221 §三 `check_package_availability` 的引擎侧）。

两件事分开、各自只有一个出处：

* **会装哪一版 = 安装器自己的回答**：`deprepair.pip_install_argv(..., dry_run_report=True)`（与装包同一份参数构造）经
  `deprepair._run_pip_install`（同一套镜像 / 用户自配源判据与回退）在**目标环境**的 pip 里 `--dry-run --report -` 一次，
  读报告里要装的版本。**不另写一套 wheel 标签判断来决定「会装哪一版」。**
* **为什么不是最新版 = 索引事实 + 环境标签**：读同一索引的 simple 页（`packageindex`）列出最新版的 wheel 文件名，
  与目标环境自己报出的兼容标签（`pip._vendor.packaging.tags.sys_tags()`，在目标环境的子进程里问）对照。
  标签 / 平台取自**目标环境**，不取父进程（父进程是 Tavotto 自己的运行时，平台与 Python 版本都可能不同）。

**进程边界**：pip dry-run 与标签探测在目标环境的子进程里（用户的 Python 不保证装了 `packaging`，所以探测优先用 pip 自带的
vendored 副本）；simple 页抓取与文件名解析（`packaging.utils.parse_wheel_filename`，Flask 父进程的运行时闭包里有 `packaging`）
在父进程。文件名只是字符串，解析不执行任何东西。

出站只发包名；索引 URL 若带凭据只在 `packageindex` 里拆成请求头，**绝不进结果 / 审计 / 日志 / 缓存键**：本模块对外只露
`INDEX_LABELS` 里的闭集标签。
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version

from packaging import tags as _tags

from . import deprepair, packageindex, projectenv

LOG = logging.getLogger("tavotto.packagecheck")

#: 结果 `reason` 的闭集。前五个是 ADR 0221 §三 写定的；`held_back` 是实现补的一个：最新版对这个环境明明有 wheel，
#: 安装器却没选它（依赖约束 / 已装的旧版满足需求 / 最新版被撤回等）——不归进前五个，免得把一个没查清的情形说成别的原因。
REASON_UP_TO_DATE = "up_to_date"
REASON_NO_WHEEL_PYTHON = "no_wheel_for_python"
REASON_NO_WHEEL_PLATFORM = "no_wheel_for_platform"
REASON_NOT_FOUND = "not_found"
REASON_UNREACHABLE = "index_unreachable"
REASON_HELD_BACK = "held_back"
REASONS = (
    REASON_UP_TO_DATE,
    REASON_NO_WHEEL_PYTHON,
    REASON_NO_WHEEL_PLATFORM,
    REASON_NOT_FOUND,
    REASON_UNREACHABLE,
    REASON_HELD_BACK,
)

#: 结果里「用了哪个索引」的闭集标签（不含地址）
INDEX_PYPI = "pypi"
INDEX_MIRROR = "mirror"
INDEX_USER = "user_config"
INDEX_UNKNOWN = "unknown"
INDEX_LABELS = (INDEX_PYPI, INDEX_MIRROR, INDEX_USER, INDEX_UNKNOWN)

#: 总预算：dry-run 解析依赖可能下几个元数据；比装包的 15 分钟短得多，镜像保底按比例缩
DRY_RUN_BUDGET_S = 180.0
PROBE_TIMEOUT_S = 30
#: 结果里 `platforms` / `python_tags` 的条数上限（manylinux 变体很多，给模型看的只要大意）
LIST_MAX = 24

#: 在**目标环境**里问：解释器完整版本、本环境兼容的全部 `解释器-abi-平台` 标签、这个包已装的版本。
#: 只读、不联网、不 import 用户代码（`sys_tags` 只看解释器自己）。pip 自带的 vendored `packaging` 优先——
#: 用户环境不保证装了 `packaging`；pip 太旧 / 没 pip 才退回环境自己的。包名走 argv，不拼进代码。
ENV_PROBE = """\
import json, sys
out = {"python": ".".join(str(x) for x in sys.version_info[:3])}
try:
    try:
        from pip._vendor.packaging import tags
    except Exception:
        from packaging import tags
    out["tags"] = [str(t) for t in tags.sys_tags()]
except BaseException as e:
    out["error"] = type(e).__name__
try:
    import importlib.metadata as md
    out["installed"] = md.version(sys.argv[1])
except Exception:
    out["installed"] = ""
print(json.dumps(out))
"""


@dataclass
class PackageCheckHooks:
    """测试 / 无界面时可注入的四个执行器；缺省 = 应用里现有的真实路径。每一个都不许抛（抛了按「这一步没做成」处理）。"""

    #: `(python, 规范化包名, cancel) -> {"code": 安装器错误码或 "", "out": 输出, "source": deprepair.PIP_SOURCES 之一,
    #:   "index_url": 这次实际用的索引地址或 None}`
    dry_run: Callable[[str, str, threading.Event], dict] | None = None
    #: `(python, 规范化包名) -> ENV_PROBE 形状的 dict | None`
    env_probe: Callable[[str, str], dict | None] | None = None
    #: `(索引地址, 请求头) -> (content_type, body)`；读不到抛 `packageindex.IndexFetchError`
    index_fetcher: packageindex.Fetcher | None = None
    #: `("3.12") -> 受管环境能不能造出这个 Python`；缺省 = `pyversionfix.plan_python_change`（与 `change_python` 同一判据）
    python_offered: Callable[[str], bool] | None = None
    now: Callable[[], float] | None = None


# --------------------------------------------------------------------------- 默认执行器
def default_dry_run(python: str, canonical: str, cancel: threading.Event) -> dict:
    seen: list[str] = []
    code, out = deprepair._run_pip_install(
        lambda index_url: deprepair.pip_install_argv(
            python, canonical, index_url=index_url, dry_run_report=True
        ),
        python,
        cancel,
        None,
        on_source=seen.append,
        budget_s=DRY_RUN_BUDGET_S,
    )
    source = seen[-1] if seen else deprepair.PIP_SOURCE_UNKNOWN
    return {
        "code": code,
        "out": out,
        "source": source,
        "index_url": _index_url_for(python, source),
    }


def _index_url_for(python: str, source: str) -> str | None:
    """这次 pip 实际用的索引地址（只在进程内部用，不外露）。读不出（问不到配置 / 离线 wheelhouse）回 None。"""
    if source == deprepair.PIP_SOURCE_PYPI:
        return deprepair.PYPI_DEFAULT_INDEX
    if source == deprepair.PIP_SOURCE_MIRROR:
        return deprepair.PYPI_MIRROR_URL
    if source == deprepair.PIP_SOURCE_USER:
        opts = deprepair.pip_install_options(python)
        if not opts or opts.get("no_index") or opts.get("find_links"):
            return None
        url = str(opts.get("index_url") or "").strip()
        return url or None
    return None


def default_env_probe(python: str, canonical: str) -> dict | None:
    rc, out = deprepair._run([str(python), "-c", ENV_PROBE, canonical], PROBE_TIMEOUT_S)
    if rc != 0:
        return None
    for line in reversed((out or "").strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        return data if isinstance(data, dict) else None
    return None


def default_python_offered(project: str, script: str = "") -> Callable[[str], bool]:
    from . import pyversionfix

    def offered(version: str) -> bool:
        failure = {
            "code": pyversionfix.CODE_UNSUPPORTED,
            "requires_python": f"=={version}.*",
            "script": script,
        }
        try:
            return pyversionfix.plan_python_change(failure, project) is not None
        except Exception:  # noqa: BLE001 - 判不出 = 不建议
            return False

    return offered


# --------------------------------------------------------------------------- 解析
def parse_report(out: str) -> dict | None:
    """pip `--report -` 的输出（可能前后夹着别的行）→ 报告 dict；找不到回 None。"""
    dec = json.JSONDecoder()
    pos = 0
    text = out or ""
    while True:
        i = text.find("{", pos)
        if i < 0:
            return None
        if i == 0 or text[i - 1] == "\n":
            try:
                obj, _end = dec.raw_decode(text, i)
            except ValueError:
                obj = None
            if isinstance(obj, dict) and "install" in obj:
                return obj
        pos = i + 1


def reported_version(report: dict, canonical: str) -> str | None:
    """报告里这个包（被请求的那一个）要装的版本；没有要装的（已装且满足）回 None。"""
    for item in report.get("install") or ():
        meta = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(meta, dict):
            continue
        if canonicalize_name(str(meta.get("name") or "")) == canonical and (
            item.get("requested", True)
        ):
            ver = meta.get("version")
            return str(ver) if ver else None
    return None


@dataclass
class _Release:
    version: Version
    wheels: list = field(default_factory=list)  # [(filename, frozenset[Tag], requires_python)]
    has_sdist: bool = False
    yanked: bool = True  # 只要有一个文件没被撤回就是 False


def group_releases(files: list[packageindex.IndexFile], canonical: str) -> dict[Version, _Release]:
    out: dict[Version, _Release] = {}
    for f in files:
        try:
            if f.filename.endswith(".whl"):
                name, ver, _build, wtags = parse_wheel_filename(f.filename)
                kind = "wheel"
            else:
                name, ver = parse_sdist_filename(f.filename)
                wtags, kind = frozenset(), "sdist"
        except (InvalidWheelFilename, InvalidSdistFilename, InvalidVersion, ValueError):
            continue
        if canonicalize_name(str(name)) != canonical:
            continue
        rel = out.setdefault(ver, _Release(ver))
        if not f.yanked:
            rel.yanked = False
        if kind == "wheel":
            rel.wheels.append((f.filename, frozenset(wtags), f.requires_python, f.yanked))
        else:
            rel.has_sdist = True
    return out


def latest_release(releases: dict[Version, _Release]) -> _Release | None:
    """最新的**正式**版本（不含预发布与全部被撤回的）——pip 默认也不会选预发布。只有预发布时回 None。"""
    live = [r for r in releases.values() if not r.yanked and not r.version.is_prerelease]
    return max(live, key=lambda r: r.version) if live else None


def _spec_allows(requires_python: str, version: str) -> bool:
    if not requires_python:
        return True
    try:
        return SpecifierSet(requires_python).contains(version, prereleases=True)
    except InvalidSpecifier:
        return True  # 读不懂的约束：pip 也会忽略它，不据此判错


def _format_tags(wheels) -> tuple[list[str], list[str]]:
    interp: set[str] = set()
    plats: set[str] = set()
    for _fn, wtags, _rp, _y in wheels:
        for t in wtags:
            interp.add(t.interpreter)
            plats.add(t.platform)
    return sorted(interp)[:LIST_MAX], sorted(plats)[:LIST_MAX]


def _minor(version: str) -> str:
    return ".".join(version.split(".")[:2])


def _candidate_tags(minor: str, platforms: list[str]) -> set:
    """把环境换成 `minor` 这个 Python 小版本（同平台）时 pip 会接受的全部标签（`packaging.tags` 的参考实现）。"""
    try:
        major_s, minor_s = minor.split(".")
        ver = (int(major_s), int(minor_s))
    except ValueError:
        return set()
    out = set(_tags.cpython_tags(python_version=ver, platforms=platforms))
    out |= set(
        _tags.compatible_tags(
            python_version=ver, interpreter=f"cp{ver[0]}{ver[1]}", platforms=platforms
        )
    )
    return out


def explain(
    rel: _Release,
    env_python: str,
    env_tags: set,
) -> str:
    """最新版为什么装不上：`no_wheel_for_python` / `no_wheel_for_platform` / `held_back`（有适用的 wheel）。"""
    wheels = [w for w in rel.wheels if not w[3]]
    pairs = {(t.interpreter, t.abi) for t in env_tags}
    py_ok = [
        w
        for w in wheels
        if _spec_allows(w[2], env_python) and any((t.interpreter, t.abi) in pairs for t in w[1])
    ]
    if not py_ok:
        return REASON_NO_WHEEL_PYTHON  # 含「只有源码包」：Tavotto 只装 wheel，对它来说这个 Python 没有可装的文件
    if not any(any(t in env_tags for t in w[1]) for w in py_ok):
        return REASON_NO_WHEEL_PLATFORM
    return REASON_HELD_BACK


def suggest_python(
    rel: _Release, env_python: str, env_tags: set, offered: Callable[[str], bool]
) -> str | None:
    """最新版有本平台 wheel 的、受管环境供得出的最高 Python 小版本（只在 `no_wheel_for_python` 时用）。
    候选范围 = Tavotto 支持的区间（`projectenv.PYTHON_MIN` / `PYTHON_MAX_EXCLUSIVE`，唯一出处）。"""
    plats = sorted({t.platform for t in env_tags})
    cur = _minor(env_python)
    lo, hi = projectenv.PYTHON_MIN, projectenv.PYTHON_MAX_EXCLUSIVE
    for m in range(hi[1] - 1, lo[1] - 1, -1):
        minor = f"{hi[0]}.{m}"
        if minor == cur:
            continue
        cand = _candidate_tags(minor, plats)
        ok = any(
            not w[3] and _spec_allows(w[2], f"{minor}.0") and any(t in cand for t in w[1])
            for w in rel.wheels
        )
        if ok and offered(minor):
            return minor
    return None
