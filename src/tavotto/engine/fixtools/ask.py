"""「问与报」五个工具：弹卡片、等用户点（ADR 0149 §一，D2b）。

都是 `waits_for_user=True`。卡片经注入的 `ctx.ask` 出去（修复会话 `fixsession` 实现它：把卡推给界面、阻塞等回答）；
`ctx.ask is None`（没有界面）时需要用户点的一律按「未获同意」处理，**绝不默认通过**。

* `propose_fix`：一句话方案 +「确认修复」，详情折叠里列出「读过哪些项目外文件」与「没读：敏感目录直接拦」；
* `ask_user`：选择题，回选项 id；
* `pick_file`：请用户选文件 / 文件夹（走界面现成的原生选择器）。选中的路径**仍然过读取范围的敏感硬拦**；
  主目录外的路径在本会话内加入可读范围并记入 `reads`；
* `prepare_diagnostics`（`outward` 档）：只弹「发送」卡，真正上传走现成的「发送问题反馈」确认流程（ADR 0118：每次都要用户点
  「发送」）。不点就不发；本工具**永远不附源码**，要不要附只有用户在那个对话框里勾选；
* `finish`：结束会话，一句话结果。
"""

from __future__ import annotations

import os
import re

from ..agent_runner.wires import ToolResult
from . import readscope
from .base import FixContext, FixTool, err, home_relative, obj, ok

SUMMARY_MAX = 300
DETAILS_MAX = 2000
QUESTION_MAX = 200
LABEL_MAX = 80
ID_MAX = 40
OPTIONS_MAX = 8
OTHER_MAX = 300
READS_MAX = 30
DIAG_SUMMARY_MAX = 600

#: 用户没给出有效回答的几种结局
_NO_ANSWER = frozenset({"cancelled", "timeout"})

SENSITIVE_NOTE = (
    "~/.ssh, keychains, .env, browser profiles and other credential locations (blocked, never read)"
)


def _text(value: object, limit: int) -> str | None:
    """模型给的文字参数：非空字符串，超长截断；不是字符串 / 空白 → None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:limit]


def _ask(ctx: FixContext, card: dict) -> dict | None:
    """弹卡等回答；没有界面 / 回答畸形 → None（调用方按「未获同意」处理）。"""
    if ctx.ask is None:
        return None
    try:
        res = ctx.ask(card)
    except Exception:  # noqa: BLE001 - 界面侧的缺陷不拖垮会话
        return None
    if not isinstance(res, dict) or not isinstance(res.get("answer"), str):
        return None
    return res


def _needs_approval(tool: str) -> ToolResult:
    return err(
        "needs_approval",
        f"`{tool}` needs the user to answer a card, but there is no interface to show it in this session.",
    )


def _no_answer(res: dict) -> ToolResult:
    what = "did not answer in time" if res.get("answer") == "timeout" else "closed the session"
    return err("no_answer", f"The user {what}. Do not assume anything; finish with what you know.")


def _reads_for_card(ctx: FixContext) -> list[str]:
    seen: list[str] = []
    for r in ctx.reads:
        shown = home_relative(ctx, str(r.get("path") or ""))
        if shown and shown not in seen:
            seen.append(shown)
    return seen[:READS_MAX]


# --------------------------------------------------------------------------- propose_fix
def _packages(raw: object) -> list[tuple[str, str]] | None:
    """`propose_fix` 的 `packages`：缺省 = 空；每项过 `depresolve.parse_requirement`（与 install_packages 同一窄文法）。"""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 20 or not all(isinstance(x, str) for x in raw):
        return None
    from .. import depresolve

    out: list[tuple[str, str]] = []
    for item in raw:
        parsed = depresolve.parse_requirement(item.strip())
        if parsed is None:
            return None
        out.append((parsed[0], parsed[1]))
    return out


def propose_fix(ctx: FixContext, args: dict) -> ToolResult:
    summary = _text(args.get("summary"), SUMMARY_MAX)
    if summary is None:
        return err("bad_arguments", "`summary` must be one sentence describing the fix.")
    details = args.get("details")
    if details is not None and not isinstance(details, str):
        return err("bad_arguments", "`details` must be a string.")
    packages = _packages(args.get("packages"))
    if packages is None:
        return err(
            "bad_requirement",
            "`packages` must be a list of plain package names such as `colorcet` or `numpy==1.26.4`.",
        )
    card = {
        "kind": "approve",
        "tool": "propose_fix",
        "summary": readscope.scrub(summary),
        "details": readscope.scrub(_text(details, DETAILS_MAX) or ""),
        "reads": _reads_for_card(ctx),
        "not_read": SENSITIVE_NOTE,
    }
    if packages:
        card["packages"] = [f"{n}{v}" for n, v in packages]
    res = _ask(ctx, card)
    if res is None:
        return _needs_approval("propose_fix")
    if res["answer"] in _NO_ANSWER:
        return _no_answer(res)
    if res["answer"] == "yes":
        from .change import canonical_name

        for name, _v in packages:  # 用户点头的包：install_packages 对完全同名的不再问第二遍
            ctx.approved_installs[canonical_name(name)] = ctx.step
        return ok("The user approved this fix. Carry it out now.", "fix approved")
    return ok(
        "The user declined this fix. Do not apply it; propose something different or finish.",
        "fix declined",
    )


# --------------------------------------------------------------------------- ask_user
def _options(raw: object) -> list[dict] | None:
    if not isinstance(raw, list) or not 1 <= len(raw) <= OPTIONS_MAX:
        return None
    out: list[dict] = []
    ids: set[str] = set()
    for o in raw:
        if not isinstance(o, dict):
            return None
        oid, label = _text(o.get("id"), ID_MAX), _text(o.get("label"), LABEL_MAX)
        if oid is None or label is None or oid in ids or oid in ("other", "skip"):
            return None
        ids.add(oid)
        opt = {"id": oid, "label": readscope.scrub(label)}
        path = o.get("path")
        if path is not None:
            if not isinstance(path, str) or "\x00" in path or len(path) > 1000:
                return None
            if path.strip():
                opt["path"] = path.strip()
        out.append(opt)
    return out


def ask_user(ctx: FixContext, args: dict) -> ToolResult:
    question = _text(args.get("question"), QUESTION_MAX)
    options = _options(args.get("options"))
    allow_other = args.get("allow_other", False)
    if question is None or options is None or not isinstance(allow_other, bool):
        return err(
            "bad_arguments",
            f"`question` (string), `options` (1-{OPTIONS_MAX} items with unique string `id` and `label`; "
            "ids `other` and `skip` are reserved) and optional boolean `allow_other` are required.",
        )
    res = _ask(
        ctx,
        {
            "kind": "choice",
            "tool": "ask_user",
            "summary": readscope.scrub(question),
            # 候选文件的 `path` 只留在后端（用户选中它才记为「亲手选的」）；公开卡片只有 id + label
            "options": [{"id": o["id"], "label": o["label"]} for o in options],
            "allow_other": allow_other,
        },
    )
    if res is None:
        return _needs_approval("ask_user")
    answer = res["answer"]
    if answer in _NO_ANSWER:
        return _no_answer(res)
    if answer == "skip":
        return ok("The user skipped this question.", "question skipped")
    if answer == "other" and allow_other:
        typed = _text(res.get("text"), OTHER_MAX)
        if typed is None:
            return ok("The user skipped this question.", "question skipped")
        return ok(f"The user typed: {readscope.scrub(typed)}", "answered (typed)")
    if answer in {o["id"] for o in options}:
        chosen = next(o for o in options if o["id"] == answer)
        _remember_chosen_path(ctx, [chosen.get("path")], literal=True)
        _remember_chosen_path(ctx, [chosen.get("label"), chosen.get("id")])
        return ok(f"The user chose: {answer}", "answered")
    return err(
        "bad_answer", "The answer did not match any option; ask again or continue without it."
    )


# --------------------------------------------------------------------------- pick_file
_PATH_START = re.compile(r"^(~|/|[A-Za-z]:[\\/]|\\\\)")


def _remember_chosen_path(ctx: FixContext, texts: list, *, literal: bool = False) -> None:
    """用户选中的选项里写着一个存在的、读得了的路径（整段文字或其中一个词，`~` 展开）：记为「用户亲手选的」。"""
    for text in texts:
        if not isinstance(text, str):
            continue
        # 词尾不属于路径的标点（`path: 说明` 的冒号、句号、括号、引号）先剥掉再判存在；`.csv` 的点在词中间，不受影响
        words = [t.strip("`'\"([").rstrip("`'\"):,;.]}") for t in text.split()]
        for cand in [text.strip()] if literal else [text.strip(), *words]:
            if not _PATH_START.match(cand):
                continue
            r = ctx.scope.check(cand)
            if r.ok and os.path.exists(r.real):
                ctx.user_chosen_paths[os.path.normcase(r.real)] = ctx.step
                return


def pick_file(ctx: FixContext, args: dict) -> ToolResult:
    purpose = _text(args.get("purpose"), QUESTION_MAX)
    kind = args.get("kind", "file")
    if purpose is None or kind not in ("file", "dir"):
        return err("bad_arguments", "`purpose` (string) and `kind` (`file` or `dir`) are required.")
    res = _ask(
        ctx,
        {"kind": "pick", "tool": "pick_file", "summary": readscope.scrub(purpose), "pick": kind},
    )
    if res is None:
        return _needs_approval("pick_file")
    answer = res["answer"]
    if answer in _NO_ANSWER:
        return _no_answer(res)
    if answer in ("skip", "no"):
        return ok("The user did not pick anything.", "nothing picked")
    # 回答是路径：先过敏感硬拦，再按类型核对，最后才加进本会话的可读范围
    granted = ctx.scope.grant(answer)
    if not granted.ok:
        if granted.reason == readscope.REASON_DENIED:
            return err(
                readscope.REASON_DENIED,
                "That location holds credentials or private browser data and cannot be read.",
            )
        return err("bad_path", "The picked path could not be used.")
    real = granted.real
    if os.path.dirname(real) == real:  # 文件系统根 / 盘符根：不整个放开
        return err("bad_path", "Pick a specific file or folder, not a drive root.")
    if not os.path.exists(real):
        return err("not_found", "The picked path does not exist.")
    if (kind == "dir") != os.path.isdir(real):
        return err("wrong_kind", f"A {'folder' if kind == 'dir' else 'file'} was expected.")
    ctx.note_read(granted, "pick_file")
    ctx.user_chosen_paths[os.path.normcase(real)] = ctx.step
    shown = home_relative(ctx, real)
    return ok(
        f"The user picked {shown}. You may read it now with read_file / list_dir / find_files.",
        "path picked",
    )


# --------------------------------------------------------------------------- prepare_diagnostics
def prepare_diagnostics(ctx: FixContext, args: dict) -> ToolResult:
    summary = _text(args.get("summary"), DIAG_SUMMARY_MAX)
    include_source = args.get("include_source", False)
    if summary is None or not isinstance(include_source, bool):
        return err(
            "bad_arguments",
            "`summary` (what you found and what you tried, for the maintainers) is required; "
            "`include_source` is an optional boolean.",
        )
    res = _ask(
        ctx,
        {
            "kind": "send",
            "tool": "prepare_diagnostics",
            "summary": readscope.scrub(summary),
            # 只是「AI 想看脚本」的提示：源码勾选框在发送对话框里默认不勾，只有用户能勾
            "wants_source": include_source,
        },
    )
    if res is None:
        return _needs_approval("prepare_diagnostics")
    if res["answer"] in _NO_ANSWER:
        return _no_answer(res)
    if res["answer"] == "sent":
        return ok(
            "The user reviewed and sent the diagnostics. Now call finish.", "diagnostics sent"
        )
    return ok("The user chose not to send diagnostics. Now call finish.", "diagnostics not sent")


# --------------------------------------------------------------------------- finish
def finish(ctx: FixContext, args: dict) -> ToolResult:
    outcome = args.get("outcome")
    summary = _text(args.get("summary"), SUMMARY_MAX)
    if outcome not in ("fixed", "not_fixed", "convert") or summary is None:
        return err(
            "bad_arguments",
            "`outcome` (`fixed`, `not_fixed` or `convert`) and `summary` (one sentence) are required.",
        )
    if outcome == "convert" and ctx.handoff is None:
        return err(
            "bad_arguments",
            "`convert` is only valid after convert_script succeeded; otherwise finish with `fixed` or `not_fixed`.",
        )
    # 非阻塞：只是把结局交给会话；没有界面（None）也照样结束，只是没人记
    _ask(
        ctx,
        {
            "kind": "done",
            "tool": "finish",
            "outcome": outcome,
            "summary": readscope.scrub(summary),
        },
    )
    return ok("The session is finished. Reply with nothing more.", f"finished: {outcome}")


# --------------------------------------------------------------------------- 登记
ASK_TOOLS: tuple[FixTool, ...] = (
    FixTool(
        "propose_fix",
        "Show the user a one-sentence plan with a Confirm button and wait for the answer. Use it right before any "
        "change to the user's environment or files. `summary` is ONE plain sentence a non-programmer understands "
        "(no jargon, no paths). `details` (optional) is folded away on the card. Returns whether the user approved; "
        "if declined, do not apply the change.",
        obj(
            {
                "summary": {
                    "type": "string",
                    "description": "One plain sentence: what you will change and why.",
                },
                "details": {
                    "type": "string",
                    "description": "Optional longer explanation, folded by default.",
                },
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Packages this fix will install (for example `colorcet`). They are listed on the card; "
                    "if the user confirms, install_packages will not ask again for exactly these names.",
                },
            },
            ["summary"],
        ),
        propose_fix,
        tier="observe",
        waits_for_user=True,
    ),
    FixTool(
        "ask_user",
        "Ask the user a multiple-choice question when you cannot find the answer yourself (for example which folder "
        "holds the data file). 2-6 short options are best. Returns the chosen option id. When the options are "
        "candidate files or folders, fill each option's `path` (the user's choice then counts as their confirmation "
        "for link_data_file). Do not ask for things you can look up with the read tools.",
        obj(
            {
                "question": {"type": "string"},
                "options": {
                    "type": "array",
                    "items": obj(
                        {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                            "path": {
                                "type": "string",
                                "description": "Optional. Absolute (or ~/) path of the file or folder this option stands for.",
                            },
                        },
                        ["id", "label"],
                    ),
                    "description": f"1-{OPTIONS_MAX} options; ids `other` and `skip` are reserved.",
                },
                "allow_other": {
                    "type": "boolean",
                    "description": "Let the user type a free answer.",
                },
            },
            ["question", "options"],
        ),
        ask_user,
        tier="observe",
        waits_for_user=True,
    ),
    FixTool(
        "pick_file",
        "Ask the user to pick a file or a folder with the system file chooser. Use it when you need something "
        "outside the places you can already read (your read range is the home folder and the project). The picked "
        "path becomes readable for this session; credential locations stay blocked even if picked.",
        obj(
            {
                "purpose": {
                    "type": "string",
                    "description": "Why you need it, in one short sentence.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["file", "dir"],
                    "description": "Default `file`.",
                },
            },
            ["purpose"],
        ),
        pick_file,
        tier="observe",
        waits_for_user=True,
    ),
    FixTool(
        "prepare_diagnostics",
        "Last resort when you could not fix the problem. Prepares a diagnostics package with your note and shows the "
        "user a Send card; nothing leaves the computer unless the user reviews it and presses Send. The package never "
        "includes the script source unless the user ticks that box themselves. `summary` = what you found and tried.",
        obj(
            {
                "summary": {
                    "type": "string",
                    "description": "What you found and what you tried (for the maintainers).",
                },
                "include_source": {
                    "type": "boolean",
                    "description": "Only mentions that the script would help; the user decides in the dialog.",
                },
            },
            ["summary"],
        ),
        prepare_diagnostics,
        tier="outward",
        waits_for_user=True,
    ),
    FixTool(
        "finish",
        "End the session. Call it exactly once, last. `outcome` is `fixed` only if you saw the problem go away "
        "(a trial run that now produces the figure); `convert` only after convert_script succeeded; otherwise `not_fixed`. `summary` is one plain sentence for the user.",
        obj(
            {
                "outcome": {"type": "string", "enum": ["fixed", "not_fixed", "convert"]},
                "summary": {"type": "string"},
            },
            ["outcome", "summary"],
        ),
        finish,
        tier="observe",
        waits_for_user=True,
    ),
)
