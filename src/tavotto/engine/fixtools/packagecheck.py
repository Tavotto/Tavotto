"""`check_package_availability`（ADR 0221 §三，`outward` 档）：在**这个环境**里装 X 会得到哪一版、为什么不是最新版。

引擎侧的事实与判因在 `engine/packagecheck.py`（安装器同一份参数 + 同一索引选择与回退 + simple 页 + 环境自己的兼容标签）。
这里只做工具的壳：参数校验 → **每会话第一次问用户**（`change.approve`，之后同会话放行）→ 查 → 闭集输出。

* 先校验参数、再弹卡：环境 id 不认 / 包名不合语法的调用不打扰用户。
* 出站只发包名（pip 装包本来就发；simple 页请求就是 `GET <索引>/<包名>/`）。索引地址（可能带凭据）不进结果 / 审计 / 日志。
* 只读：不改环境、不打快照，所以不作废「验证过」的结论（`base.READ_ONLY_OUTWARD`）。
* 没有 `ctx.ask`（无界面）= 未获同意，不查。
"""

from __future__ import annotations

import json
import threading
import time

from packaging.version import InvalidVersion, Version

from packaging import tags as _tags

from .. import deprepair, depresolve, packagecheck as pc, packageindex
from ..agent_runner.wires import ToolResult
from . import executors
from .base import FixContext, FixTool, err, obj, ok
from .change import _script_rel, approve


def _parse_tags(raw: object) -> set:
    out: set = set()
    if isinstance(raw, list):
        for s in raw[:20000]:
            try:
                out |= set(_tags.parse_tag(str(s)))
            except Exception:  # noqa: BLE001 - 一个坏标签不拖垮整个判断
                continue
    return out


def _version(text: object) -> Version | None:
    try:
        return Version(str(text))
    except InvalidVersion:
        return None


def _result(
    would: str | None,
    latest: str | None,
    requires: dict,
    reason: str,
    index: str,
    *,
    installed: str = "",
    suggest: str | None = None,
) -> ToolResult:
    body: dict = {
        "would_install": would,
        "latest": latest,
        "latest_requires": requires,
        "reason": reason,
        "index": index,
    }
    if installed:
        body["already_installed"] = installed
    if suggest:
        body["suggest"] = {"tool": "change_python", "python": suggest}
    return ok(json.dumps(body, ensure_ascii=False, indent=1), reason)


def check_package_availability(ctx: FixContext, args: dict) -> ToolResult:
    python = ctx.python_of(args.get("env"))
    if python is None:
        return err(
            "env_unknown",
            "Unknown environment id. Call list_environments first and use one of its ids.",
        )
    raw = args.get("package")
    parsed = depresolve.parse_requirement(raw.strip()) if isinstance(raw, str) else None
    if parsed is None or parsed[1]:
        return err(
            "bad_arguments",
            "`package` must be a plain package name such as `scipy` (no version, extras, path or URL).",
        )
    canonical = depresolve.normalize_distribution(parsed[0])
    # 与 `deprepair.lookup_package` 同一条纪律：归一之后再过一次语法，进 argv 的串由常量字母表重拼
    if depresolve.parse_requirement(canonical) != (canonical, ""):
        return err("bad_arguments", "That package name is not valid after normalising it.")
    try:
        canonical = deprepair.argv_package_name(canonical)
    except deprepair.RepairError:
        return err("bad_arguments", "That package name is not valid.")

    if not ctx.package_check_approved:
        denied = approve(
            ctx,
            "check_package_availability",
            f"Ask the Python package index which versions of `{canonical}` exist and which Python versions and "
            "systems they are built for, to see why it may not install here. Only the package name is sent.",
            package=canonical,
        )
        if denied is not None:
            return denied
        ctx.package_check_approved = True

    hooks = ctx.package_check or pc.PackageCheckHooks()
    cancel = ctx.cancel or threading.Event()
    facts = _call(hooks.env_probe or pc.default_env_probe, python, canonical) or {}
    env_python = str(facts.get("python") or "")
    env_tags = _parse_tags(facts.get("tags"))
    installed = str(facts.get("installed") or "")

    dry = _call(hooks.dry_run or pc.default_dry_run, python, canonical, cancel)
    if not isinstance(dry, dict):
        return err("check_failed", "The installer could not be asked what it would install.")
    code = str(dry.get("code") or "")
    source = str(dry.get("source") or deprepair.PIP_SOURCE_UNKNOWN)
    label = {
        deprepair.PIP_SOURCE_PYPI: pc.INDEX_PYPI,
        deprepair.PIP_SOURCE_MIRROR: pc.INDEX_MIRROR,
        deprepair.PIP_SOURCE_USER: pc.INDEX_USER,
    }.get(source, pc.INDEX_UNKNOWN)

    would: str | None = None
    net_failed = False
    pip_not_found = False
    if code == "":
        report = pc.parse_report(str(dry.get("out") or ""))
        if report is None:
            return err("check_failed", "The installer's report could not be read.")
        would = pc.reported_version(report, canonical)
        if would is None and installed:
            would = installed  # 已装且满足：安装器什么都不会换，就是现在这一版
        env_python = env_python or str(
            (report.get("environment") or {}).get("python_full_version") or ""
        )
    elif code in (deprepair.ERROR_NETWORK, deprepair.ERROR_TIMEOUT):
        net_failed = True
    elif code == deprepair.ERROR_NOT_FOUND:
        pip_not_found = True  # `from versions: none`：索引上没有这个名字
    elif code == deprepair.ERROR_REQUIRES_BUILD:
        pass  # 索引上有版本、没有这个环境能装的 wheel：would_install 为空，下面看最新版
    elif code == deprepair.ERROR_CANCELLED:
        return err("cancelled", "The check was cancelled.")
    else:
        detail = executors.failure_detail(deprepair._sanitize(str(dry.get("out") or "")))
        return err(
            "check_failed",
            "The installer could not work out what it would install"
            + (f":\n{detail}" if detail else "."),
        )

    empty = {"python_tags": [], "platforms": []}
    if net_failed:
        return _result(None, None, empty, pc.REASON_UNREACHABLE, label, installed=installed)

    index_url = dry.get("index_url")
    files = None
    index_code = packageindex.CODE_UNREACHABLE
    if isinstance(index_url, str) and index_url:
        try:
            files, _cached = packageindex.project_files(
                index_url, canonical, fetcher=hooks.index_fetcher, now=hooks.now or time.time
            )
        except packageindex.IndexFetchError as exc:
            index_code = exc.code
    if files is None:
        if pip_not_found or index_code == packageindex.CODE_NOT_FOUND and would is None:
            return _result(None, None, empty, pc.REASON_NOT_FOUND, label)
        return _result(would, None, empty, pc.REASON_UNREACHABLE, label, installed=installed)

    releases = pc.group_releases(files, canonical)
    rel = pc.latest_release(releases)
    if rel is None:
        if would is None:
            return _result(None, None, empty, pc.REASON_NOT_FOUND, label)
        return _result(would, None, empty, pc.REASON_HELD_BACK, label, installed=installed)

    interp, plats = pc._format_tags([w for w in rel.wheels if not w[3]])
    requires = {"python_tags": interp, "platforms": plats}
    latest = str(rel.version)
    have = _version(would)
    if have is not None and have >= rel.version:
        return _result(would, latest, requires, pc.REASON_UP_TO_DATE, label, installed=installed)
    if not env_tags or not env_python:
        return _result(would, latest, requires, pc.REASON_HELD_BACK, label, installed=installed)
    reason = pc.explain(rel, env_python, env_tags)
    suggest = None
    if reason == pc.REASON_NO_WHEEL_PYTHON:
        offered = hooks.python_offered or pc.default_python_offered(
            str(ctx.project_root), _script_rel(ctx)
        )
        suggest = pc.suggest_python(rel, env_python, env_tags, offered)
    return _result(would, latest, requires, reason, label, installed=installed, suggest=suggest)


def _call(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001 - 执行器的缺陷只算「这一步没做成」，不抛出会话
        return None


PACKAGE_CHECK_TOOLS: tuple[FixTool, ...] = (
    FixTool(
        "check_package_availability",
        "Ask the package index what installing one package would give in ONE environment, and why it is not the "
        "newest version. Returns `would_install` (what Tavotto's installer would install there, taken from the "
        "installer itself), `latest`, `latest_requires` (Python tags and platforms of the newest version's wheels), "
        "`reason` (up_to_date, no_wheel_for_python, no_wheel_for_platform, not_found, index_unreachable, or "
        "held_back when the newest version has a usable wheel but the installer did not pick it) and, when another "
        "Python version would get the newest version, `suggest` (call change_python with that version). "
        "Only the package name goes out. The user is asked once per session before the first use. "
        "`env` must be an id from list_environments; `package` is a plain distribution name.",
        obj(
            {
                "env": {"type": "string", "description": "Environment id from list_environments."},
                "package": {
                    "type": "string",
                    "description": "Package name to install, for example `scipy`.",
                },
            },
            ["env", "package"],
        ),
        check_package_availability,
        tier="outward",
        waits_for_user=True,
    ),
)
