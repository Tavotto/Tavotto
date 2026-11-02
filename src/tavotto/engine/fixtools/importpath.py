"""`ignore_import_folder`：让脚本自带的、为别的 Python 版本装的私有依赖文件夹对这个项目不生效（ADR 0154 / 0149 §七）。

场景（TVD-ZCR4）：脚本把项目里的 `pip install --target` 目录（`.kegg_python`，cp312 的编译扩展）插到 `sys.path` 最前，环境里
同名的包本来完好，却被它盖住。报告里的错误码是 `import_folder_abi_mismatch`。**换 Python 版本救不了**（那个目录永远是为别的版本装的），
正确的出路是「跳过这个文件夹」——与失败卡上的主按钮是**同一条路径**（`pathignore.remember`）。

1. 工具**不收路径**：要忽略的是哪个文件夹由 Tavotto 在失败那一刻登记（`pathignore.latest_path`），模型只能说「忽略失败里点名的那个」，
   不能让它指认任意目录；没有登记（过期 / 这次失败不是这个码）直接拒。
2. 弹 approve 卡（`ctx.ask`）：没有界面 = `needs_approval`，用户说不 = 什么都不写。文案如实说明偏离「与终端里一致」的这一点。
3. 同意后先打快照（项目设置的 `ignored_import_paths` 键在快照里，回滚精确恢复，含「原来没有」），再 `pathignore.remember`。
   不改脚本、不动那个文件夹；只对这个项目、只对这一个目录、顶层导入生效，可撤销。
"""

from __future__ import annotations

import os

from ..agent_runner.wires import ToolResult
from .base import FixContext, FixTool, err, obj
from .change import _done, _snapshot_or_error, approve
from .readscope import scrub

SUMMARY_MAX = 300


def _shown(root: str, path: str) -> str:
    """给用户看的目录：项目内是相对项目根的路径，项目外只有目录名。"""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        rel = ""
    if rel and not rel.startswith(".."):
        return rel.replace("\\", "/")
    return os.path.basename(path) or path


def ignore_import_folder(ctx: FixContext, args: dict) -> ToolResult:
    from .. import pathignore

    summary = args.get("summary")
    if not isinstance(summary, str) or not summary.strip() or "\x00" in summary:
        return err(
            "bad_arguments", "`summary` (one plain sentence for the user, no jargon) is required."
        )
    root = str(ctx.project_root)
    path = pathignore.latest_path(root)
    if path is None:
        return err(
            "no_foreign_folder",
            "There is no folder to skip right now: the last failure was not `import_folder_abi_mismatch` "
            "(or it is too old). Run try_run again, or use another remedy.",
        )
    shown = _shown(root, path)
    details = (
        f"Folder: {shown}\n"
        "The script puts this folder on its import path first, but the packages inside were installed for a different "
        "Python version, so they cannot work here. If you agree, this project will skip that folder when the script "
        "imports things, and use the packages from the current environment instead (installing any that are missing). "
        "Your script and the folder are not changed. You can undo this in the project's settings."
    )
    denied = approve(
        ctx, "ignore_import_folder", summary.strip()[:SUMMARY_MAX], details=scrub(details)
    )
    if denied is not None:
        return denied
    if ctx.cancel is not None and ctx.cancel.is_set():
        return err("cancelled", "The repair was cancelled; nothing was changed.")
    bad = _snapshot_or_error(ctx, "ignore_import_folder")
    if bad is not None:
        return bad
    try:
        state = pathignore.remember(root, path, on_changed=ctx.on_ignored_paths_changed)
    except pathignore.IgnoreError:
        return err("ignore_rejected", "That folder cannot be skipped.")
    except OSError:
        return err("settings_not_saved", "The project setting could not be saved.")
    ctx.verification = None
    return _done(
        ctx,
        f"The project now skips the folder {shown} when the script imports things "
        f"({len(state['paths'])} skipped folder(s) in total). The script was not changed. "
        "Now call try_run to check it runs; if a package is reported missing, install it into the managed environment as usual.",
        "folder skipped",
    )


IMPORT_PATH_TOOLS: tuple[FixTool, ...] = (
    FixTool(
        "ignore_import_folder",
        "Skip the script's own private dependency folder that was installed for a different Python version "
        "(report error code `import_folder_abi_mismatch`). Use this INSTEAD of change_python / install_packages for that "
        "case: switching Python cannot help because that folder is always for another version. It takes no path "
        "(Tavotto knows which folder failed) and brings its own confirmation card (your summary), so do NOT call "
        "propose_fix first; if the user declines nothing is written. The script and the folder are never changed. "
        "Afterwards call try_run.",
        obj(
            {
                "summary": {
                    "type": "string",
                    "description": "ONE plain sentence for the user, e.g. 'The script's own folder was installed for Python 3.12 "
                    "and cannot work here. Skip it for this project?' No jargon.",
                }
            },
            ["summary"],
        ),
        ignore_import_folder,
        tier="tavotto",
        waits_for_user=True,
    ),
)

__all__ = ["IMPORT_PATH_TOOLS", "ignore_import_folder"]
