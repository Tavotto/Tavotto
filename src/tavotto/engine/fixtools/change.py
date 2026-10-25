"""「改」组工具（ADR 0149 §七）：装包 / 切运行方式 / 换 Python / 换环境 / 转写 / 回滚（`run_command` 在 `command.py`，两档，见 ADR 0149 §七 #1374 修订）。

**每个动作的执行都在自己的出处里**，本模块只做「守门 → 问用户 → 打快照 → 调出处」：

* 装 / 卸包：`deprepair._pip_install` / `_pip_uninstall`（同一份 argv 出处、只装 wheel、镜像回退、取消）+ `pool.mutating_environment`
  （环境独占锁，池里用这个解释器的 worker 先收掉）。需求串先过 `depresolve.parse_requirement`（`-r` / URL / 路径 / 带标记的都死在那里）。
  系统自带 / 内置 runtime 的解释器永不装（`convertdeps.describe_env` 同一口径）。「常见大包」名单 = `convertdeps.in_allowlist`
  （`autoinstall_dists.txt`，PyPI 公开下载量生成）：名单内不问，名单外与 `select_environment` 经 `ctx.ask` 问用户。
* 切运行方式：`execpolicy.set_mode`（项目级 `execution_policy`；`safe` = `safe_only`，`native` = `compatible`，即允许 safe 被自己的守卫拦下时回退 native）。
* 换环境：`projectenv.remember(..., automatic=False)`（与界面「选择运行环境」同一个写入）。
* 换 Python：执行器由 `FixContext` 注入（`python_changer`），缺省走 `executors`；转写 `convert_script` 不执行任何东西，只记 `ctx.handoff`，会话收尾后交给「生成可编辑版本」（ADR 0143）。

所有会改东西的工具在执行前打快照（`snapshots.py`），快照 id 进审计行与工具返回，`rollback` 用它恢复。
**没有 `ctx.ask`（无界面）= 未获同意**：需要点头的动作一律 `needs_approval`，审计记 `blocked`。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .. import rasterhint
from ..agent_runner.wires import ToolResult
from . import executors, snapshots
from .base import FixContext, FixTool, err, obj, ok
from .readscope import scrub

PACKAGES_MAX = 20

_VERSION_RE = re.compile(r"3\.\d{1,2}(\.\d{1,2})?")

# --------------------------------------------------------------------------- 询问用户
_NO_ASK = "This needs the user's approval, and no approval could be asked for in this session, so nothing was done."
_DECLINED = "The user did not approve this action, so nothing was done."


def approve(ctx: FixContext, tool: str, summary: str, **extra) -> ToolResult | None:
    """问用户点头。通过回 None（并在 `ctx.call_meta` 记 `approved_by=user`），否则回 `needs_approval` 错误。

    卡片里的文字先脱敏。没有 `ctx.ask`、用户说不、超时、`ask` 自己抛了，一律按未获同意。"""
    if ctx.ask is None:
        return err("needs_approval", _NO_ASK)
    card = {"kind": "approve", "tool": tool, "summary": scrub(summary)[:600], **extra}
    try:
        answer = ctx.ask(card)
    except Exception:  # noqa: BLE001 - 界面那头出任何问题都不能当作同意
        return err("needs_approval", _DECLINED)
    if isinstance(answer, dict) and answer.get("answer") == "yes":
        ctx.call_meta["approved_by"] = "user"
        return None
    return err("needs_approval", _DECLINED)


def _snapshot_or_error(
    ctx: FixContext, tool: str, env_ids: list[str] | None = None
) -> ToolResult | None:
    """改动前打快照；打不出来就不改（没有退路的改动不做）。"""
    if snapshots.take(ctx, tool, env_ids) is None:
        return err(
            "snapshot_failed", "Could not save a snapshot to roll back to, so nothing was changed."
        )
    return None


def _with_snapshot(ctx: FixContext, text: str) -> str:
    snap = ctx.call_meta.get("snapshot")
    return f"{text}\nsnapshot: {snap} (use rollback to undo)" if snap else text


def _done(ctx: FixContext, text: str, summary: str) -> ToolResult:
    res = ok(_with_snapshot(ctx, text), summary)
    res.mutated = True
    return res


def _forget_env_caches(ctx: FixContext) -> None:
    from .. import projectenv

    try:
        projectenv.reset_cache(ctx.project_root)  # 装过包：此前的体检结论作废
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- install_packages
def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


#: 安装失败码 → 给模型的一句话（它看不到完整 stderr，靠这句分清下一步）
_INSTALL_HINTS = {
    "dependency_requires_build": "The package exists on the index but has no ready-made wheel for this Python "
    "(source-only or built for other Python versions); Tavotto never compiles packages. Do not try another "
    "name: use a different Python version/environment, or tell the user it must be built or installed by hand.",
    "dependency_not_found": "pip found no version it can install on this Python and platform. The name may be "
    "wrong, or the package may only be published for other Python versions or platforms. Check the name with "
    "lookup_package first; if the name is right, try another Python version or environment, or tell the user.",
    "dependency_network_unavailable": "The package index could not be reached; this is a network problem, not a "
    "problem with the package. Do not change the package name.",
}


def install_packages(ctx: FixContext, args: dict) -> ToolResult:
    from .. import convertdeps, depresolve

    python = ctx.python_of(args.get("env"))
    if python is None:
        return err(
            "env_unknown",
            "Unknown environment id. Call list_environments first and use one of its ids.",
        )
    raw = args.get("packages")
    if (
        not isinstance(raw, list)
        or not raw
        or len(raw) > PACKAGES_MAX
        or not all(isinstance(x, str) for x in raw)
    ):
        return err(
            "bad_arguments",
            f"`packages` must be a list of 1 to {PACKAGES_MAX} requirement strings such as `seaborn` or `numpy==1.26.4`.",
        )
    reqs: list[str] = []
    names: list[str] = []
    for item in raw:
        parsed = depresolve.parse_requirement(item.strip())
        if parsed is None:
            return err(
                "bad_requirement",
                "Only `name`, `name==1.2.3`, `name>=1.2` (optionally with `,<2`) are accepted: "
                "no options, URLs, paths, extras or markers.",
            )
        reqs.append(f"{parsed[0]}{parsed[1]}")
        names.append(parsed[0])
    kind = convertdeps.describe_env(python, "")["kind"]
    if kind in (convertdeps.KIND_SYSTEM, convertdeps.KIND_BUILTIN):
        return err(
            "env_not_installable",
            "Packages are never installed into the operating system's Python or Tavotto's built-in runtime. "
            "Choose another environment (list_environments) or create one with change_python.",
        )
    outside = sorted({n for n in names if not convertdeps.in_allowlist(n)})
    agreed = [ctx.approved_installs.get(canonical_name(n)) for n in outside]
    if outside and all(a is not None for a in agreed):
        # 这些包用户已在 propose_fix 卡上点过头（完全同名才算）：不再弹第二张卡，审计记 user 与同意它的那一步
        ctx.call_meta["approved_by"] = "user"
        ctx.call_meta["approved_step"] = max(a for a in agreed if a is not None)
    elif outside:  # 名单外 = user_env：一律问
        denied = approve(
            ctx,
            "install_packages",
            f"Install {', '.join(reqs)} into a Python environment on this computer. "
            f"{', '.join(outside)} is not on Tavotto's list of common packages.",
            packages=reqs,
            outside_list=outside,
        )
        if denied is not None:
            return denied
    bad = _snapshot_or_error(ctx, "install_packages", [str(args.get("env"))])
    if bad is not None:
        return bad
    res = executors.run_installer(ctx, python, reqs)
    _forget_env_caches(ctx)
    if not res.get("ok"):
        done = [d for d in (res.get("done") or []) if isinstance(d, str)]
        code = str(res.get("code") or "install_failed")[:60]
        detail = res.get("detail")
        text = (
            "The installation did not finish"
            + (f" (installed before it stopped: {', '.join(done)})" if done else "")
            + "."
        )
        if code in _INSTALL_HINTS:
            text += " " + _INSTALL_HINTS[code]
        if isinstance(detail, str) and detail.strip():
            text += "\nInstaller said: " + scrub(detail.strip())[:500]
        return err(code, _with_snapshot(ctx, text))
    return _done(ctx, f"Installed: {', '.join(reqs)}.", "packages installed")


# --------------------------------------------------------------------------- switch_execution
_EXECUTION_MODES = {"safe": "safe_only", "native": "compatible"}


def switch_execution(ctx: FixContext, args: dict) -> ToolResult:
    from .. import execpolicy

    mode = args.get("mode")
    if mode not in _EXECUTION_MODES:
        return err("bad_arguments", "`mode` must be `safe` or `native`.")
    target = _EXECUTION_MODES[mode]
    root = str(ctx.project_root)
    current = execpolicy.mode_for(root)
    if current == target:
        return ok(f"Already set: {mode}. Nothing changed.", "no change")
    if execpolicy.decided(root) and current == execpolicy.MODE_SAFE_ONLY:
        # 用户明确选过「只用安全运行」：放宽它是推翻用户的决定，要问
        denied = approve(
            ctx,
            "switch_execution",
            "Allow this project to run natively when the safe mode cannot do what the script needs? "
            "You had chosen to only use the safe mode.",
        )
        if denied is not None:
            return denied
    bad = _snapshot_or_error(ctx, "switch_execution")
    if bad is not None:
        return bad
    try:
        execpolicy.set_mode(root, target)
    except (OSError, ValueError):
        return err("settings_not_saved", "The project setting could not be saved.")
    note = (
        "The project now only runs in the safe mode."
        if mode == "safe"
        else "The project may now run natively (on your own Python, with your files) when the safe mode is "
        "blocked by its own guard."
    )
    return _done(ctx, note, f"execution set to {mode}")


# --------------------------------------------------------------------------- change_python
def _script_rel(ctx: FixContext) -> str:
    """被诊断脚本的项目相对路径（执行器据此读脚本声明的 `requires-python`）；没有脚本回空串。"""
    if ctx.script_path is None:
        return ""
    try:
        return Path(ctx.script_path).relative_to(ctx.project_root).as_posix()
    except ValueError:
        return ""


def _lease_current_environment(ctx: FixContext) -> list[tuple[str, str]]:
    """项目当前用的受管环境那一代，在本修复会话结束前加**保留型**租约（`envlease.acquire_retain`）：
    换到新一代后 `managedenv.retire_unused` 经 `deprepair._generation_in_use` 看到它就不删旧代，`rollback` 才能把项目指回去。
    保留型不挡换代事务自己的 `envlease.mutating()`（用 native 租约会把 `change_python` 自己挡死）。"""
    from .. import envlease, managedenv, projectenv

    root = ctx.project_root
    targets: list[str] = []
    active = managedenv.python_of(root)
    if active:
        targets.append(str(active))
    try:
        remembered = projectenv.remembered(root)
    except Exception:  # noqa: BLE001 - 取不到只是少租一个
        remembered = None
    if remembered and managedenv.is_managed_python(root, remembered):
        targets.append(str(remembered))
    owner = f"fix:{ctx.session_id}"
    added: list[tuple[str, str]] = []
    for python in dict.fromkeys(targets):
        if (python, owner) in ctx.leases:
            continue  # 本会话此前的换代已经保留着它
        envlease.acquire_retain(python, owner)
        ctx.leases.append((python, owner))
        added.append((python, owner))
    return added


def change_python(ctx: FixContext, args: dict) -> ToolResult:
    version = args.get("version")
    if version is not None and (
        not isinstance(version, str) or not _VERSION_RE.fullmatch(version.strip())
    ):
        return err("bad_arguments", "`version` must look like `3.12` or `3.12.4`, or be left out.")
    version = version.strip() if isinstance(version, str) else None
    bad = _snapshot_or_error(ctx, "change_python")
    if bad is not None:
        return bad
    added = _lease_current_environment(ctx)
    res = executors.run_python_changer(
        ctx,
        {
            "version": version,  # None = 由执行器按脚本 / 项目声明和 Tavotto 支持区间挑
            "project": str(ctx.project_root),
            "script": _script_rel(ctx),
        },
    )
    if not res.get("ok"):
        # 没换成：只撤销**这一次**新加的租约；本会话此前成功换代保留的旧代要留到会话收尾，否则它的快照回滚不了
        ctx.release_leases(added)
        return err(
            str(res.get("code") or "change_python_failed")[:60],
            _with_snapshot(
                ctx, str(res.get("message") or "The new environment could not be created.")
            ),
        )
    shown = version or str(res.get("to") or "")
    text = str(res.get("message") or f"Created a Tavotto environment with Python {shown}.")
    python = res.get("python")
    if isinstance(python, str) and python:
        from .. import userenvs

        eid = userenvs.env_id(python)
        ctx.environments[eid] = (
            python  # 新环境的 id：之后 inspect_environment / install_packages 可以指认
        )
        text += f"\nnew environment id: {eid}"
    _forget_env_caches(ctx)
    return _done(ctx, text, f"python {shown or 'new'}")


# --------------------------------------------------------------------------- select_environment
def select_environment(ctx: FixContext, args: dict) -> ToolResult:
    from .. import projectenv

    env = args.get("env")
    python = ctx.python_of(env)
    if python is None:
        return err(
            "env_unknown",
            "Unknown environment id. Call list_environments first and use one of its ids.",
        )
    if not os.path.isfile(python):
        return err("env_missing", "That environment's Python is no longer there.")
    denied = approve(
        ctx,
        "select_environment",
        "Make this project use another Python environment that already exists on this computer.",
        env=str(env),
    )
    if denied is not None:
        return denied
    bad = _snapshot_or_error(ctx, "select_environment")
    if bad is not None:
        return bad
    saved = projectenv.remember(
        ctx.project_root,
        python,
        automatic=False,
        trigger="user_selected",
        env_overlay=ctx.environment_overlays.get(str(env)),
    )
    if not saved:
        return err("settings_not_saved", "The project setting could not be saved.")
    return _done(ctx, "This project now uses that environment.", "environment selected")


# --------------------------------------------------------------------------- convert_script
def _inside(root: Path, path: Path) -> bool:
    try:
        r, p = os.path.realpath(root), os.path.realpath(path)
    except OSError:
        return False
    return p == r or p.startswith(r + os.sep)


def _hinted_library(ctx: FixContext) -> str | None:
    """准备报告里「跑完没出图」的位图提示（`no_figure_hint`，`rasterhint.detect` 的形状）指认的库；没有 / 不合法 = None。"""
    try:
        report = ctx.report() if ctx.report is not None else None
    except Exception:  # noqa: BLE001 - 报告取不到 = 没有提示
        return None
    hint = (report or {}).get("no_figure_hint")
    if isinstance(hint, dict) and hint.get("kind") == "raster_script":
        lib = hint.get("library")
        if lib in rasterhint.LIBRARIES:
            return lib
    return None


def _hinted_path(ctx: FixContext, rel: str) -> str | None:
    """报告里位图提示的原输出图路径（项目相对），仅当这份报告说的就是被交接的这个脚本；否则 None（不拿别的脚本的图当参考）。"""
    try:
        report = ctx.report() if ctx.report is not None else None
    except Exception:  # noqa: BLE001 - 报告取不到 = 没有参考图
        return None
    report = report or {}
    hint = report.get("no_figure_hint")
    if not (isinstance(hint, dict) and hint.get("kind") == "raster_script"):
        return None
    path = hint.get("path")
    if not (isinstance(path, str) and path):
        return None
    target = report.get("target")
    said = target.get("script") if isinstance(target, dict) else None
    if not isinstance(said, str) or not said:
        return None
    try:
        said_rel = Path(os.path.realpath(ctx.project_root / said)).relative_to(
            os.path.realpath(ctx.project_root)
        )
    except ValueError:
        return None
    return path if said_rel.as_posix() == rel else None


def convert_script(ctx: FixContext, args: dict) -> ToolResult:
    """不写任何文件：只在 `ctx.handoff` 记一笔，会话收尾后界面把它交给「生成可编辑版本」（ADR 0143）。"""
    from .. import ai_convert

    target, library = args.get("target"), args.get("library")
    if target is not None and not (isinstance(target, str) and target.strip()):
        return err(
            "bad_arguments", "`target` must be a script path inside the project, or omitted."
        )
    if library is not None and library not in rasterhint.LIBRARIES:
        return err("bad_arguments", f"`library` must be one of: {', '.join(rasterhint.LIBRARIES)}.")
    if isinstance(target, str):
        script = Path(target.strip())
        if not script.is_absolute():
            script = ctx.project_root / script
    elif ctx.script_path is not None:
        script = Path(ctx.script_path)
    else:
        return err("no_script", "There is no script to convert in this session.")
    if not script.is_file():
        return err("no_script", "That script file is not there.")
    if not _inside(ctx.project_root, script):
        return err("outside_project", "Only a script inside the project can be converted.")
    if script.suffix.lower() != ".py":
        return err("bad_arguments", "Only a Python script (.py) can be converted.")
    library = library or _hinted_library(ctx)
    if library is None:
        return err(
            "bad_arguments",
            "Say which library draws the figure: `library` is one of "
            f"{', '.join(rasterhint.LIBRARIES)}.",
        )
    if not ai_convert.enabled():
        return err("unavailable", "Making an editable version is switched off on this computer.")
    try:
        rel = Path(os.path.realpath(script)).relative_to(os.path.realpath(ctx.project_root))
    except ValueError:
        return err("outside_project", "Only a script inside the project can be converted.")
    ctx.handoff = {
        "kind": "convert",
        "script": rel.as_posix(),
        "library": library,
        "path": _hinted_path(ctx, rel.as_posix()),
    }
    return ok(
        "Noted. Nothing was written. After this session Tavotto will make a Matplotlib version for the user to "
        "compare and accept; the original script is not changed. Now call finish with outcome `convert`.",
        "editable version handed off",
    )


# --------------------------------------------------------------------------- 描述与登记
_ENV = {"type": "string", "description": "Environment id from list_environments."}

CHANGE_TOOLS: tuple[FixTool, ...] = (
    FixTool(
        "install_packages",
        "Install packages into one environment (wheels only, through Tavotto's own installer). Packages on "
        "Tavotto's list of common packages are installed without asking; any other package asks the user first. "
        "Never installs into the operating system's Python or Tavotto's built-in runtime. A snapshot is taken "
        "first; the result names it so rollback can undo this. `env` must be an id returned by list_environments.",
        obj(
            {
                "env": _ENV,
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Requirements such as `seaborn` or `numpy==1.26.4` (no options, URLs or paths).",
                },
            },
            ["env", "packages"],
        ),
        install_packages,
        tier="listed",
        waits_for_user=True,
    ),
    FixTool(
        "switch_execution",
        "Change how this project runs scripts: `safe` = only Tavotto's guarded safe mode, `native` = allow running "
        "natively (on the user's own Python and files) when the safe mode is blocked by its own guard. Asks the "
        'user first only when it would undo a choice of "safe only" the user made. Takes a snapshot.',
        obj({"mode": {"type": "string", "enum": ["safe", "native"]}}, ["mode"]),
        switch_execution,
        tier="tavotto",
        waits_for_user=True,
    ),
    FixTool(
        "change_python",
        "Create a Tavotto-managed Python environment for this project, also installing what the script imports. Use it "
        "when there is no usable environment (list_environments is empty, or none can run the script) or when the "
        "packages need another Python version. Does not touch the user's own Pythons. Takes a snapshot; the result "
        "gives the new environment id for install_packages / try_run. `version` is optional (for example `3.12`): "
        "leave it out and Tavotto picks one that suits the script and the project.",
        obj(
            {"version": {"type": "string", "description": "Optional, for example `3.12`."}},
        ),
        change_python,
        tier="tavotto",
    ),
    FixTool(
        "select_environment",
        "Make this project use another environment that already exists on the computer. Always asks the user. "
        "Takes a snapshot. `env` must be an id returned by list_environments.",
        obj({"env": _ENV}, ["env"]),
        select_environment,
        tier="user_env",
        waits_for_user=True,
    ),
    FixTool(
        "convert_script",
        "Use when the script runs but draws its figure with Pillow, OpenCV, imageio, scikit-image, plotly or ROOT "
        '(so Tavotto cannot edit it). Writes nothing: it hands the script to Tavotto\'s own "make an editable version" '
        "flow, which the user reviews and accepts after this session; the original script is never changed. Then "
        "call finish with outcome `convert`. `target` is a .py path inside the project (default: the script being "
        "fixed); `library` is the drawing library (default: what the run report points at).",
        obj(
            {
                "target": {"type": "string", "description": "Script path inside the project."},
                "library": {"type": "string", "enum": list(rasterhint.LIBRARIES)},
            }
        ),
        convert_script,
        tier="tavotto",
    ),
    FixTool(
        "rollback",
        "Undo changes back to a snapshot named in an earlier result of this session: project settings go back "
        "exactly; packages installed since are removed and changed or removed ones go back to the old version.",
        obj(
            {
                "snapshot": {
                    "type": "string",
                    "description": "Snapshot id such as snap_20261023T051200_ab12cd.",
                }
            },
            ["snapshot"],
        ),
        snapshots.rollback,
        tier="tavotto",
    ),
)
