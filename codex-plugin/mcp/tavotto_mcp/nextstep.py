"""失败回包的 `next`：错误码 → 一个可以照抄的下一步（ADR 0189 第 3 条）。

只在 MCP 工具层把 `BridgeError` 变成结果的那一处挂（`server.call_tool` 与 `fix_to_journal` 的失败回包），
不散在各个 raise 点。三种形状：

* `{"tool": "tavotto_xxx", "args": {...}, "why": "一句话"}`：直接再调一个工具；
* `{"action": "edit_script", "why": "..."}`：必须回代码改（Tavotto 改不了的事）；
* `{"action": "ask_user", "question": "...", "options": [...]}`：要用户决定，Agent 不替用户猜。

规矩：

* **`code` 的取值与含义不动**，`next` 只是增量字段；`BridgeError` 自己显式带了 `next` 的不覆盖。
* 码要么在 `BUILDERS`（有可行下一步），要么在 `NO_NEXT`（没有，每项一句理由）；`tests/test_mcp_error_next.py`
  按 AST 收集桥里出现的全部错误码对账，新增一个码而忘了登记会红。
* 参数能从当次调用的入参 / 错误的 extra 补全的一律补全；补不全的写成 `<...>` 形状的占位，不编造值。
* 纯 Python、不 import `tavotto.engine`：这个模块不进桥的三处同源约束。结果有体积预算，`next` 要短。
"""

from __future__ import annotations

from typing import Callable

#: 候选 / 选项最多列几个：`next` 进错误预算，不是候选清单的存放处。
MAX_OPTIONS = 8


def tool_step(tool: str, args: dict, why: str) -> dict:
    return {"tool": tool, "args": args, "why": why}


def edit_script(why: str) -> dict:
    return {"action": "edit_script", "why": why}


def ask_user(question: str, options: list | None = None) -> dict:
    return {
        "action": "ask_user",
        "question": question,
        "options": list(options or [])[:MAX_OPTIONS],
    }


class Ctx:
    """一次失败的上下文：哪个工具、当次入参、错误回包（`BridgeError.payload()`）。"""

    def __init__(self, tool: str, args: dict, payload: dict) -> None:
        self.tool = tool
        self.args = args if isinstance(args, dict) else {}
        self.payload = payload if isinstance(payload, dict) else {}

    def arg(self, key: str):
        return self.args.get(key)

    def extra(self, key: str):
        return self.payload.get(key)

    def session_args(self) -> dict:
        sid = self.args.get("session_id")
        return {"session_id": sid} if isinstance(sid, str) and sid else {}


def _open_args(ctx: Ctx) -> dict:
    """重新 open 要的入参：当次调用里有的路径 / stem 原样带上，没有就写占位（不编造路径）。"""
    out = {
        k: ctx.args[k] for k in ("project_path", "script_path", "stem", "stems") if ctx.args.get(k)
    }
    if "project_path" not in out and "script_path" not in out:
        out["project_path"] = "<项目目录或脚本路径>"
    return out


def _reopen(ctx: Ctx) -> dict:
    return tool_step(
        "tavotto_open_figure",
        _open_args(ctx),
        "会话不在了（进程换过、已关闭或被淘汰）：重新打开这张图。",
    )


def _journal_query(ctx: Ctx) -> str:
    for key in ("query", "journal_id", "profile_id", "journal"):
        value = ctx.args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _find_journal(ctx: Ctx) -> dict:
    return tool_step(
        "tavotto_list_journals",
        {"query": _journal_query(ctx)} if _journal_query(ctx) else {},
        "库里没有这个期刊 id：按名称 / 缩写查出准确的 id 再带上；查不到就如实告诉用户，不要凭记忆编规范。",
    )


def _column_choice(ctx: Ctx) -> dict:
    columns = ctx.payload.get("columns") or []
    options = [
        f"{c.get('id')}（{c.get('label') or c.get('id')}，{c.get('width_mm'):g} mm）"
        for c in columns
        if isinstance(c, dict) and isinstance(c.get("width_mm"), (int, float))
    ]
    return ask_user("这张图要按哪个栏位的宽度改？选定后带 column=<栏位 id> 再调一次。", options)


def _stem_choice(ctx: Ctx) -> dict:
    stems = [s for s in ctx.payload.get("stems") or [] if isinstance(s, str)]
    return ask_user("这个项目里有多张图，要打开哪一张？选定后带 stem 再调一次。", stems)


def _session_choice(ctx: Ctx) -> dict:
    sessions = ctx.payload.get("sessions")
    options = sorted(sessions) if isinstance(sessions, dict) else []
    return ask_user("有多个项目开着会话，要处理哪一个？选定后带 session_id 再调一次。", options)


def _workspace_changed(ctx: Ctx) -> dict:
    return ask_user(
        "这个会话所在的目录已不在当前授权的工作区里。要重新授权该目录、换一个工作区，还是放弃这张图？",
        ["重新授权该目录", "换一个工作区目录", "放弃"],
    )


def _open_with_user_choice(ctx: Ctx) -> dict | None:
    """首开的「需要输入」：把 worker 给的选项原样交给用户，不替用户猜（first-open-questions 细则）。"""
    confirmation = ctx.payload.get("confirmation")
    modes = (
        [o.get("mode") for o in confirmation.get("options") or [] if isinstance(o, dict)]
        if isinstance(confirmation, dict)
        else []
    )
    if any(isinstance(m, str) for m in modes):
        return ask_user(
            "脚本要先选运行目录：用哪一种？选定后再调 tavotto_open_figure 并带 workdir=<所选>。",
            [m for m in modes if isinstance(m, str)],
        )
    dependency = ctx.payload.get("dependency_preparation")
    kinds = (
        [t.get("kind") for t in dependency.get("targets") or [] if isinstance(t, dict)]
        if isinstance(dependency, dict)
        else []
    )
    if any(isinstance(k, str) for k in kinds):
        digest = dependency.get("impact_digest")
        return ask_user(
            "脚本要的包目标环境里没有：要不要装、装到哪？选定后再调 tavotto_open_figure 带 "
            f"prepare_dependencies=<所选> 与 prepare_impact_digest={digest or '<digest>'}。",
            [k for k in kinds if isinstance(k, str)] + ["skip"],
        )
    if isinstance(ctx.payload.get("missing_input"), dict):
        return ask_user(
            "脚本要读的数据找不到：请用户在 Tavotto 窗口里指认数据位置，或把数据放回脚本写的位置；"
            "好了之后再调 tavotto_open_figure。不要改用户的脚本。",
            ["已指认 / 已放回，重新打开", "放弃这张图"],
        )
    if isinstance(ctx.payload.get("explicit"), dict):
        return ask_user(
            "指定的渲染环境用不了：请用户在设置里重新指定或清除那条设置，之后再调 tavotto_open_figure。",
            ["已处理，重新打开", "放弃"],
        )
    return None


def _ask(question: str, options: list | None = None) -> Callable[[Ctx], dict]:
    return lambda ctx: ask_user(question, options)


def _script(why: str) -> Callable[[Ctx], dict]:
    return lambda ctx: edit_script(why)


def _health(why: str) -> Callable[[Ctx], dict]:
    return lambda ctx: tool_step("tavotto_health", {}, why)


def _retry_dpi(ctx: Ctx) -> dict:
    return tool_step(ctx.tool, {**ctx.args, "dpi": 600}, "dpi 必须是正整数：改成 600 重调。")


def _halve_limit(ctx: Ctx) -> dict:
    raw = ctx.args.get("limit")
    limit = max(1, int(raw) // 2) if isinstance(raw, int) and raw > 1 else 5
    # 同一页重读：offset / expected_catalog_rev 原样保留（后续页缺了它会被拒）
    return tool_step(
        "tavotto_get_journal_facts",
        {**ctx.args, "limit": limit},
        "这一页超出体积预算：缩小 limit 重读。",
    )


def _restart_page(ctx: Ctx) -> dict:
    args = {k: v for k, v in ctx.args.items() if k not in ("expected_catalog_rev", "offset")}
    return tool_step(
        "tavotto_get_journal_facts", {**args, "offset": 0}, "期刊目录版本变了：从第一页重新读取。"
    )


def _forward(ctx: Ctx, *keys: str) -> dict:
    """原调用带了的规范参数原样转发（否则预检按会话自己的规范判，与导出那次不是同一份）。"""
    return {k: ctx.args[k] for k in keys if ctx.args.get(k) not in (None, "", {})}


def _preflight_first(ctx: Ctx) -> dict:
    return tool_step(
        "tavotto_preflight",
        {**ctx.session_args(), **_forward(ctx, "profile_id", "journal", "journal_id")},
        "先看有哪些阻断项并修掉；用户明确要求照样导出时再带 explicit_confirm=true。",
    )


def _refresh_after_script(ctx: Ctx) -> dict:
    return edit_script("先把脚本跑一遍出图；跑完 tavotto_refresh_project，再重新打开。")


_WORKSPACE_ASK = "需要用户给出可用的工作区目录：请用户确认要用的目录（或在宿主里授权）后再试。"

#: 码 → 构造器。构造器返回 None 表示「这次拿不到足够的上下文，不给 next」（比给错的好）。
BUILDERS: dict[str, Callable[[Ctx], dict | None]] = {
    # 会话没了 / 没法用
    "unknown_session": _reopen,
    "no_manifest": _reopen,
    "session_restore_failed": _reopen,
    "native_session_ended": _reopen,
    "no_project": _reopen,
    "workspace_root_changed": _workspace_changed,
    "ambiguous_project": _session_choice,
    "stem_required": _stem_choice,
    "no_figure": _refresh_after_script,
    "no_registry": _refresh_after_script,
    "stem_not_parameterizable": _script(
        "这张图没有对应脚本：把产出它的 .py 放到产物同一个目录、让产物名是脚本里的字面量，再 tavotto_refresh_project。"
    ),
    "not_found": _ask("给的路径不存在：请用户确认正确的项目 / 脚本 / 图路径。"),
    "handoff_failed": _ask("这个路径不是 Tavotto 能识别的项目、脚本或图：请用户确认要打开哪个。"),
    # 引擎版本
    "engine_too_old": _health(
        "引擎版本过旧：看 health 里的版本与更新提醒，请用户升级 Tavotto 后新开会话。"
    ),
    "engine_capability_missing": _health(
        "引擎没有这项能力：看 health 里的版本与更新提醒，请用户升级 Tavotto 后新开会话。"
    ),
    # 期刊
    "journal_unknown": _find_journal,
    "unknown_profile": _find_journal,
    "journal_catalog_changed": _restart_page,
    "journal_facts_page_too_large": _halve_limit,
    "unknown_column": _column_choice,
    "column_choice_required": _column_choice,
    # 导出
    "preflight_blocked": _preflight_first,
    "export_conflict": _ask(
        "导出目录里这几个名字正被另一次导出占用：等那次结束，还是换一个 out_dir？",
        ["等一会儿再导出", "换一个 out_dir"],
    ),
    "acceptance_failed": _script(
        "产物核验没过（尺寸 / 字体对不上）：按 error 里的失败项回脚本改，再 refresh 重开导出。"
    ),
    "artifact_rejected": _script(
        "导出的文件没过完整性检查：回脚本检查图是否能正常保存，再 refresh 重开导出。"
    ),
    "render_failed": _script(
        "脚本渲染失败：按 error / traceback 回脚本修好，再 tavotto_refresh_project 重开。"
    ),
    # 参数
    "bad_dpi": _retry_dpi,
    "environment_busy": lambda ctx: tool_step(
        ctx.tool, ctx.args, "环境正被另一次操作占用：稍等几秒原样重试一次，仍忙就告诉用户。"
    ),
    # 首开的「需要输入」（也按 extra 兜底，见 `next_for`）
    "workdir_confirmation_required": lambda ctx: _open_with_user_choice(ctx),
    "dependency_preparation_required": lambda ctx: _open_with_user_choice(ctx),
    "script_needs_arguments": _ask(
        "脚本需要命令行参数：请用户提供（或在 Tavotto 窗口里输入），拿到后用 argv 再打开。"
    ),
    "script_needs_input": _ask(
        "脚本在等输入（口令等不经 Agent 转交）：请用户在 Tavotto 窗口里处理。"
    ),
    "missing_dependency": _ask("脚本缺依赖：请用户确认要不要安装，或在 Tavotto 窗口里准备环境。"),
    "write_outside_roots": _script(
        "脚本想写已授权工作区之外的位置：改成写到项目内，不要换写法绕过。"
    ),
    "dependency_impact_required": _ask("需要用户看过依赖安装的影响摘要并确认，才能继续准备。"),
    "dependency_target_invalid": _ask(
        "依赖准备目标不对：请用户在 tavotto_managed / project_venv / skip 里选一个。",
        ["tavotto_managed", "project_venv", "skip"],
    ),
    "environment_generation_required": _ask(
        "采用环境要带上候选的 generation：重新 open 取到候选后再带上 adopt_environment 与 generation。"
    ),
    "argv_unverifiable": _ask(
        "这个脚本的参数无法静态确认：请用户在 Tavotto 窗口里自己输入参数并运行。"
    ),
    "argv_path_out_of_scope": _ask(
        "argv 里有路径指到工作区之外：请用户在 Tavotto 窗口里自己输入参数并运行。"
    ),
    # 工作区授权（roots.WORKSPACE_FAILURES）
    "workspace_confirmation_declined": _ask(
        "用户拒绝了这个目录：要换一个目录再试吗？", ["换一个目录", "放弃"]
    ),
    "workspace_confirmation_cancelled": _ask(
        "工作区确认框被关掉了：要重新发起一次吗？", ["重新发起", "放弃"]
    ),
    "workspace_confirmation_stale": _ask(_WORKSPACE_ASK),
    "workspace_confirmation_required": _ask(_WORKSPACE_ASK),
    "no_workspace_root": _ask(_WORKSPACE_ASK),
    "ambiguous_workspace_root": _ask("有多个候选工作区：请用户指定要用哪一个。"),
    "workspace_root_too_broad": _ask(
        "给的目录范围太宽（主目录或其上级）：请用户指定一个具体的项目目录。"
    ),
    "path_out_of_scope": _ask(
        "这个路径在已授权的工作区之外：请用户把它所在目录加入授权，或换一个工作区内的路径。"
    ),
    "path_denied": _ask("这个路径被安全策略拒绝：请用户换一个路径，不要尝试绕过。"),
}

#: 显式的「没有可行下一步」：每项一句理由（为什么 Agent 手里没有可抄的动作）。
NO_NEXT: dict[str, str] = {
    # 参数 / 协议形状：error 文案已指出哪项不对，没有一个固定的正确值可抄
    "bad_arguments": "参数组合或取值不对，error 已指出是哪一项；没有固定的正确值可抄。",
    "bad_format": "导出格式名不在支持集合里，error 列出了支持的格式；由调用方按需选。",
    "bad_patches": "patches 形状不对，rejected 已列出被拒的条目；要调用方按需改。",
    "bad_request": "查询、期刊 ID 或分页参数不正确，没有可抄的固定值。",
    "bad_registry": "图库登记表本身损坏，不是 Agent 能修的事。",
    # 环境 / 外部条件
    "journal_catalog_disabled": "期刊库总开关被用户关掉了，不是 Agent 能改的。",
    "journal_facts_unavailable": "期刊事实服务暂不可用，只能稍后重试，没有可换的路。",
    "journal_offline": "期刊库联网被禁用或网络不通，只能稍后重试或请用户联网。",
    "journal_quota_exceeded": "期刊库的额度用完了，只能等额度恢复。",
    "journal_verification_failed": "这本刊的数据验签没过，已整份弃用，不能采用其中任何数值。",
    "remote_open_failed": "运行中的 Tavotto 打不开这个项目，error 里是对方的原因。",
    "native_session_unsupported": "这张图改用真实 Python 运行，这项操作在 MCP 里不做。",
    "svg_source_unsupported": "引擎没能产出经过核验的 PDF/SVG 配对，换格式导出是调用方的取舍。",
    "preview_unreadable": "预览位图读不出来，重试也不会变，请用户在 Tavotto 窗口里看。",
    "normalize_internal": "规范化内部不变量被破坏（bug），没有任何用户或 Agent 可做的修补。",
    "batch_all_failed": "批量里每一张都失败了，各张自己的 code 与 stem 在 results 里，没有统一下一步。",
    # 工作区授权：宿主接线问题，只能由用户改配置
    "workspace_confirmation_no_response": "宿主没把确认框送到用户面前，要用户改宿主配置，recovery 已写明。",
    "workspace_confirmation_auto_declined": "宿主按审批设置替用户拒绝了，要用户改宿主审批配置，recovery 已写明。",
    "workspace_confirmation_error": "确认框本身出错，要用户检查宿主接线，recovery 已写明。",
    "workspace_roots_no_response": "宿主没有回应 roots 请求，要用户检查宿主配置，recovery 已写明。",
    "workspace_roots_error": "宿主的 roots 请求报错，要用户检查宿主配置，recovery 已写明。",
    # 来自 worker / 引擎的泛化失败：具体原因在 error 与 traceback，没有统一动作
    "worker_error": "worker 的泛化失败，原因在 error / traceback，没有统一下一步。",
    "bridge_error": "未归类的桥错误，原因在 error 文案里。",
    "export_failed": "导出作业失败的泛化码，原因在 error / traceback。",
    "replay_failed": "重放失败的泛化码，原因在 error。",
    "preview_failed": "预览失败的泛化码，原因在 error。",
    "environment_adoption_failed": "采用环境失败，原因在 error。",
    "requires_authorization": "规范化需要用户授权（Agent 不能替用户授权），见 recovery。",
    "workdir_mode_invalid": "workdir 取值不对，error 列出了可选值，按需改即可。",
}

#: 以下是 fix_to_journal 的「结局码」（回包 `exit`，不是 BridgeError.code）：见 `fix_next`。
FIX_EXITS = (
    "column_choice_required",
    "constraint_conflict",
    "unsupported",
    "not_resolved",
    "font_unavailable",
    "acceptance_failed",
    "protected_changed",
    "budget_exceeded",
)


def next_for(code: str, tool: str, args: dict, payload: dict) -> dict | None:
    """错误码 → `next`；没有映射或上下文不足返回 None。绝不抛：这是失败路径上的附加信息。"""
    try:
        ctx = Ctx(tool, args, payload)
        builder = BUILDERS.get(code)
        if builder is not None:
            return builder(ctx)
        if code in NO_NEXT:
            return None
        # 未登记的码（多半是 worker 新增的）：只按「需要输入」的结构化载荷兜底，其余不编
        return _open_with_user_choice(ctx)
    except Exception:  # noqa: BLE001
        return None


def attach(payload: dict, tool: str, args: dict) -> dict:
    """给 `BridgeError.payload()` 加 `next`（已显式带 next 的不覆盖）。就地改并返回。"""
    if "next" not in payload:
        step = next_for(str(payload.get("code") or ""), tool, args, payload)
        if step:
            payload["next"] = step
    return payload


# ----------------------------- fix_to_journal 的失败回包 -----------------------------


def fix_next(out: dict, args: dict) -> dict | None:
    """`tavotto_fix_to_journal` 没改成（`ok` 为假）时的 `next`；点名的冲突在 `out["conflicts"]`。"""
    try:
        exit_code = str(out.get("exit") or "")
        stage = out.get("stage")
        if exit_code == "column_choice_required":
            return _column_choice(Ctx("tavotto_fix_to_journal", args, out))
        if exit_code == "font_unavailable":
            want = next(
                (
                    s.get("want")
                    for s in out.get("skipped") or []
                    if s.get("reason") == "font_unavailable"
                ),
                None,
            )
            return ask_user(
                f"本机没有规范要的字体{'「' + want + '」' if want else ''}：装上后重试，还是保留现字体？",
                ["装上字体后重试", "保留现字体（其余照改）"],
            )
        if exit_code in FIX_EXITS:
            if stage == "size":
                return edit_script(
                    "改图幅会让 conflicts 里点名的元素出界 / 重叠：回脚本把 figsize 设成目标尺寸并重排版面，"
                    "tavotto_refresh_project 后重新打开再调。"
                )
            return edit_script(
                "按 conflicts 里点名的规则与元素回脚本改（有 prop 的就是该改的属性），"
                "tavotto_refresh_project 后重新打开再调。"
            )
    except Exception:  # noqa: BLE001
        return None
    return None
