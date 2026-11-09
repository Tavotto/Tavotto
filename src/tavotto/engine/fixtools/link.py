"""`link_data_file`：把 AI 找到的数据文件接到脚本上（ADR 0106 修订 2026-10-24 / ADR 0149 §七）。

数据不在脚本写的位置时，产品本来就有一条路：`missing_input` → 用户指认 → `inputremap` 推一条规则 → 按项目记在本机设置
`input_remap` → worker 只读改道，**脚本不改**。本工具只是让 AI 能替用户「找」，找到的东西接上去走**同一套**推导与落盘
（`inputremap.link`，数据失联弹窗的 HTTP 端点调的也是它，不复制第二份校验），并且**一律先问用户**：

1. `requested` 只认三处出处（`inputremap.known_requests`：报告里的 `missing_input` 载荷、脚本里以常量出现且此刻找不到的路径、
   已经接上的改指——接错了文件时 AI 要能改过来，TVD-6Z9R），
   而且得是改指救得回来的读取（`open`）；exists / glob / C++ 读取器改指救不回来，要改脚本——AI 永远不改脚本，直接拒。
2. `chosen` 先过读取范围（`ctx.scope`，凭据位置 / 范围外拒绝），再要存在；规则当场推演，推不出来就不打扰用户。
3. 弹 approve 卡（`ctx.ask`）：没有界面 = `needs_approval`，用户说不 = 什么都不写。
4. 同意后先打快照（项目设置的 `input_remap` 键在快照里，回滚精确恢复，含「原来没有」），再 `inputremap.link`。
   不改脚本、不复制数据、不放宽写入边界；规则只影响只读打开，且只在原路径打不开时才查。

目录前缀没有能认出的具体文件时，报告给既有规则一个本进程、项目与代次绑定的不可猜标识；
`mode=prefix` 只允许把该既有前缀改指到用户确认的目录，不凭前缀猜文件。范围、确认、快照和换代照旧。
"""

from __future__ import annotations

import os

from ..agent_runner.wires import ToolResult
from .base import FixContext, FixTool, err, home_relative, obj, ok, resolve_or_error
from .change import _done, _snapshot_or_error, approve
from .readscope import scrub

REQUESTED_MAX = 1000
EVIDENCE_MAX = 400
SUMMARY_MAX = 300


def _script_rel(ctx: FixContext) -> str | None:
    if ctx.script_path is None:
        return None
    try:
        rel = os.path.relpath(os.path.realpath(ctx.script_path), os.path.realpath(ctx.project_root))
    except ValueError:  # Windows 跨盘
        return None
    return None if rel.startswith("..") else rel.replace("\\", "/")


def _reported_payload(ctx: FixContext) -> dict | None:
    """报告里 `input_location` 需求的载荷（= 数据失联弹窗那份）。"""
    report = ctx.report() if ctx.report else None
    for item in (report or {}).get("requirements") or []:
        if isinstance(item, dict) and item.get("kind") == "input_location":
            payload = item.get("payload")
            return payload if isinstance(payload, dict) else None
    return None


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    return " ".join(value.split())[:limit]


def describe_rule(rule: dict) -> str:
    if rule["kind"] == "file":
        return f"{rule['from']} -> {rule['to']}"
    src = rule["from"] or "(any relative path)"
    return f"{src} -> {rule['to']}"


def _notify_remap(ctx: FixContext, state: dict) -> None:
    if ctx.on_input_remap_changed is not None:
        try:
            ctx.on_input_remap_changed(state, "added")
        except Exception:  # noqa: BLE001 - 通知窗口失败不影响已落盘的规则
            pass


def _repoint_prefix(
    ctx: FixContext, reference: str, chosen: object, summary: str, evidence: str
) -> ToolResult:
    """Re-point an existing directory rule without guessing an individual source file."""
    from .. import inputremap

    root = str(ctx.project_root)
    found = inputremap.existing_prefix(root, reference)
    if found is None:
        return err(
            "requested_unknown",
            "No current prefix data link matches `requested`; read the report again.",
        )
    previous, generation = found
    resolved = resolve_or_error(ctx, chosen, "link_data_file")
    if isinstance(resolved, ToolResult):
        return resolved
    real = resolved.real
    if not os.path.isdir(real):
        return err("chosen_not_directory", "A prefix link requires the destination folder itself.")
    if os.path.normcase(os.path.realpath(previous["to"])) == os.path.normcase(real):
        if inputremap.existing_prefix(root, reference) != (previous, generation):
            return err("input_remap_changed", "The data links changed; read the report again.")
        return ok(
            "This prefix already points to that folder. Data links apply to safe execution; "
            "native execution reads the script's original paths instead. Nothing changed.",
            "no change",
        )
    source_label = previous["from"] or "(any relative path)"
    denied = approve(
        ctx,
        "link_data_file",
        summary,
        details=scrub(
            "This can change where all missing read-only paths using this prefix are looked up, "
            "not just one file; more specific links still take precedence. Your script and data are "
            "not changed. Native execution does not use data links.\n"
            f"New destination: {real}\nExisting source prefix: {source_label}\n"
            f"Current destination: {previous['to']}\nWhy this folder: {evidence}"
        ),
    )
    if denied is not None:
        return denied
    if ctx.cancel is not None and ctx.cancel.is_set():
        return err("cancelled", "The repair was cancelled; the data link was not changed.")
    # Approval described this exact rule. Re-check the target and keep snapshot
    # plus compare-and-replace under the project's mapping lock.
    checked = resolve_or_error(ctx, real, "link_data_file")
    if isinstance(checked, ToolResult):
        return checked
    if os.path.normcase(checked.real) != os.path.normcase(real):
        return err("chosen_changed", "The selected folder changed; choose and confirm it again.")
    try:
        with inputremap.project_mutex(root):
            current = inputremap.existing_prefix(root, reference)
            if current is None or current != (previous, generation):
                raise inputremap.RemapChanged("数据改指表已变化，请重新检查并确认")
            bad = _snapshot_or_error(ctx, "link_data_file")
            if bad is not None:
                return bad
            state = inputremap.replace_existing_prefix(root, previous, generation, checked.real)
    except inputremap.RemapChanged:
        return err(
            "input_remap_changed", "The data links changed; read the report and confirm again."
        )
    except inputremap.RemapError as exc:
        return err(str(exc.code)[:60], f"That folder cannot be linked: {exc}")
    except OSError:
        return err("settings_not_saved", "The project setting could not be saved.")
    _notify_remap(ctx, state)
    return _done(
        ctx,
        f"Updated the existing prefix {source_label} to {home_relative(ctx, checked.real)}. "
        "It can affect all matching paths in safe execution; more specific links still take precedence. "
        "Native execution does not use data links. "
        "Now call try_run to verify the result.",
        "data prefix linked",
    )


def link_data_file(ctx: FixContext, args: dict) -> ToolResult:
    from .. import inputremap

    requested = args.get("requested")
    mode = args.get("mode", "file")
    if mode not in ("file", "prefix"):
        return err("bad_arguments", "`mode` must be `file` or `prefix`.")
    if (
        not isinstance(requested, str)
        or (mode == "file" and not requested.strip())
        or "\x00" in requested
        or len(requested) > REQUESTED_MAX
    ):
        return err(
            "bad_arguments",
            "`requested` must be the path string the script uses, exactly as the run report shows it.",
        )
    evidence = _text(args.get("evidence"), EVIDENCE_MAX)
    summary = _text(args.get("summary"), SUMMARY_MAX)
    if evidence is None or summary is None:
        return err(
            "bad_arguments",
            "`summary` (one plain sentence for the user) and `evidence` (why this is the right file) are required.",
        )
    script = _script_rel(ctx)
    if script is None:
        return err("no_script", "There is no script to connect data to in this session.")
    if mode == "prefix":
        return _repoint_prefix(ctx, requested, args.get("chosen"), summary, evidence)
    root = str(ctx.project_root)

    known = inputremap.known_requests(script, root, _reported_payload(ctx))
    matched = inputremap.match_request(known, requested)
    if matched is None:
        return err(
            "requested_unknown",
            "`requested` is not a path this script was found to be missing. Use the path from the run report "
            "(read_run_report: `missing_input`, or a `requested` string of an existing data link) exactly as "
            "written; Tavotto will not link a path it cannot tie to the script.",
        )
    requested = matched
    if known[requested] != inputremap.VIA_OPEN:
        return err(
            "link_unreachable",
            "The script checks that path in a way a data link cannot reach (folder listing / glob / size or "
            "stat check, or a native reader), so linking would not help. The script itself would have to change, and this "
            "assistant never edits scripts. Tell the user in plain words and finish with not_fixed.",
        )

    r = resolve_or_error(ctx, args.get("chosen"), "link_data_file")
    if isinstance(r, ToolResult):
        return r
    real = r.real
    if os.path.isdir(real):
        kind = "dir"
    elif os.path.isfile(real):
        kind = "file"
    else:
        return err("chosen_not_found", "`chosen` is not an existing file or folder.")
    try:
        rule = inputremap.rule_for_choice(requested, real, kind)  # 当场推演：推不出来就不打扰用户
    except inputremap.RemapError as exc:
        return err(str(exc.code)[:60], f"That location cannot be linked: {exc}")

    shown = home_relative(ctx, real)
    details = (
        f"The script asks for: {requested}\n"
        f"Found here: {real}\n"
        f"Why this one: {evidence}\n"
        f"If the script cannot open that path, Tavotto will read from here instead ({describe_rule(rule)}). "
        "Your script and your data are not changed or copied. You can remove this link in the project's data settings."
    )
    picked = ctx.user_chosen_paths.get(os.path.normcase(real))
    if picked is not None:
        # 用户刚在 ask_user / pick_file 里亲手选了这个路径：选择本身就是确认，不再弹第二张卡（同 `approved_installs` 的口径）
        ctx.call_meta["approved_by"] = "user"
        ctx.call_meta["approved_step"] = picked
    else:
        denied = approve(ctx, "link_data_file", summary, details=scrub(details)[:1500])
        if denied is not None:
            return denied
    bad = _snapshot_or_error(ctx, "link_data_file")
    if bad is not None:
        return bad
    try:
        rule, state = inputremap.link(root, requested, real, kind)
    except inputremap.RemapError as exc:
        return err(str(exc.code)[:60], f"That location cannot be linked: {exc}")
    except OSError:
        return err("settings_not_saved", "The project setting could not be saved.")
    _notify_remap(ctx, state)
    return _done(
        ctx,
        f"Linked: {requested} -> {shown} (rule: {describe_rule(rule)}). The script was not changed. "
        "Now call try_run to check it reads the data.",
        "data file linked",
    )


LINK_TOOLS: tuple[FixTool, ...] = (
    FixTool(
        "link_data_file",
        "Connect a data file you found to the script's missing input, so the script reads it without any change "
        "to the script. It brings its own confirmation card (your summary and evidence), so do NOT call "
        "propose_fix first; if the user declines nothing is written. A file the user already chose in ask_user "
        "or pick_file is linked without a second card. `requested` = the path string the script uses, exactly as in the run report. "
        "`chosen` = the file (or the folder that holds it) you found; it must be somewhere you may read. "
        "Only reading is redirected, and only when the original path cannot be opened. It also re-points an "
        "existing data link (run report `data_links`) whose linked file turns out to be the wrong one. "
        "If a prefix link has no known individual requested paths, use mode=prefix and its `prefix_id` "
        "as `requested`, and ask the user for the destination folder. This can change all matching paths under the "
        "existing prefix, so it always needs explicit approval; never infer a missing filename. "
        "Data links apply in safe execution, not native execution. "
        "Afterwards call try_run.",
        obj(
            {
                "requested": {
                    "type": "string",
                    "description": "The path the script asks for, or in prefix mode the opaque prefix_id from the run report.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["file", "prefix"],
                    "description": "Default file; prefix only re-points an existing directory link to a chosen folder.",
                },
                "chosen": {
                    "type": "string",
                    "description": "Absolute path of the file you found, or of the folder that contains it.",
                },
                "summary": {
                    "type": "string",
                    "description": "ONE plain sentence for the user, e.g. 'I found x.csv in your Downloads folder; its columns match "
                    "what the script uses. Use it?' No jargon.",
                },
                "evidence": {
                    "type": "string",
                    "description": "Why you believe this is the file the script means (matching columns, shape, name).",
                },
            },
            ["requested", "chosen", "summary", "evidence"],
        ),
        link_data_file,
        tier="tavotto",
        waits_for_user=True,
    ),
)

__all__ = ["LINK_TOOLS", "link_data_file"]
