"""失败时就给提示（ADR 0197 第 1 层）：一张表，键是「失败形状」，值是几句对症建议。

形状 = 运行报告的结局码 / 错误码；`script_error` 再按 traceback 里一眼可认的样子细分（`os.environ` 的 KeyError、相对导入、
`input()` 的 EOFError）。这是**唯一**一份形状判据：`ask.prepare_diagnostics` 的「用户侧失败先别报缺陷」闸门也从这里取。

每句建议声明它点名的工具；当前注册表里没有那个工具（或会话不让用），这句就不说——提示里永远不出现模型调不到的工具。
第一句是主动作：它说不了，整条提示都不说（后面的句子都接着它，单独留下就成了半截话）。
提示只指方向，不替模型做决定，也不越过同意：会改东西的工具照常各自问用户。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable

#: traceback 里一眼可认的用户侧形状：(形状, 必须同时出现的片段)
TRACE_SHAPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("env_var_missing", ("KeyError", "os.environ")),
    ("relative_import", ("ImportError", "attempted relative import")),
    ("input_eof", ("EOFError", "input(")),
)

#: 用户这一侧就能补上的形状（在没试过任何改动之前不许当产品缺陷报）
USER_SIDE_SHAPES = frozenset(
    {
        "script_needs_arguments",
        "script_needs_input",
        "missing_input",
        "missing_dependency",
        "workdir_confirmation_required",
        *(shape for shape, _ in TRACE_SHAPES),
    }
)


def shape_of(code: str, trace: str = "") -> str:
    """(错误码, traceback 文本) → 形状。认不出细分就是错误码本身；什么都没有回空串。"""
    for shape, parts in TRACE_SHAPES:
        if all(p in trace for p in parts):
            return shape
    return str(code or "")


def report_error(report: object) -> tuple[str, str]:
    """运行报告里的 (错误码, traceback 文本)；不是报告回空串。"""
    if not isinstance(report, dict):
        return "", ""
    outcome = report.get("outcome") if isinstance(report.get("outcome"), dict) else {}
    result = report.get("result") if isinstance(report.get("result"), dict) else {}
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    if not error and isinstance(report.get("error"), dict):
        error = report["error"]
    code = str(outcome.get("code") or error.get("code") or "")
    trace = " ".join(str(error.get(k) or "") for k in ("message", "traceback"))
    return code, trace


def shape_of_report(report: object) -> str:
    return shape_of(*report_error(report))


@dataclasses.dataclass(frozen=True)
class Hint:
    id: str
    #: (这句点名的工具, 这句话)；工具都可用才说
    lines: tuple[tuple[tuple[str, ...], str], ...]


def _h(hint_id: str, *lines: tuple[tuple[str, ...], str]) -> Hint:
    return Hint(hint_id, tuple(lines))


HINTS: dict[str, Hint] = {
    "script_needs_arguments": _h(
        "args",
        (
            ("read_file",),
            "The script needs command-line arguments: read its argparse / sys.argv code to see which.",
        ),
        (
            ("run_snippet",),
            "You can print the parser's usage with run_snippet instead of guessing.",
        ),
        (
            ("set_run_arguments", "ask_user", "try_run"),
            "Then set them with set_run_arguments (values found in the project, or ask_user) and try_run again.",
        ),
    ),
    "script_needs_input": _h(
        "stdin",
        (
            ("write_launcher", "try_run"),
            "The script waits for input(). Use write_launcher to add a launcher that answers it (replace builtins.input before run_original()), then try_run.",
        ),
    ),
    "input_eof": _h(
        "stdin",
        (
            ("write_launcher", "try_run"),
            "The script waits for input(). Use write_launcher to add a launcher that answers it (replace builtins.input before run_original()), then try_run.",
        ),
    ),
    "env_var_missing": _h(
        "env",
        (
            ("write_launcher",),
            "The script reads an environment variable that is not set. Use write_launcher to add a launcher that sets it with os.environ.setdefault(...) before run_original().",
        ),
        (
            ("find_files", "ask_user"),
            "Take the value from the project (README, config, the folder that holds the data; find_files helps), or ask_user.",
        ),
    ),
    "relative_import": _h(
        "package",
        (
            ("write_launcher",),
            "The script uses relative imports but is run outside its package. Use write_launcher to add a launcher that puts the package's parent folder on sys.path and sets __package__ before run_original().",
        ),
    ),
    "missing_input": _h(
        "data",
        (
            ("find_files", "link_data_file"),
            "A data file the script reads is missing. Look for it with find_files, then connect it with link_data_file using the path the script asks for exactly.",
        ),
        (
            ("pick_file",),
            "If you cannot find it, ask the user to pick it with pick_file. Never invent data.",
        ),
    ),
    "missing_dependency": _h(
        "package_missing",
        (
            ("try_import",),
            "A package is missing. Confirm with try_import in each candidate environment.",
        ),
        (
            ("install_packages", "propose_fix", "select_environment"),
            "Then install it with install_packages (after propose_fix), or select_environment an environment that already has it.",
        ),
    ),
    "script_is_library": _h(
        "library",
        (
            ("grep_files",),
            "The script only defines functions. Check with grep_files whether another script in the project calls them.",
        ),
        (
            ("finish",),
            "If another script is the real entry point, do not run it for this one: finish with outcome not_fixed and tell the user, in one sentence, to open that script instead.",
        ),
        (
            ("write_launcher",),
            "Only if no other script calls them, use write_launcher to add a launcher that calls the plotting function with the project's own data.",
        ),
    ),
    "no_figure_captured": _h(
        "no_figure",
        (
            ("read_file",),
            "The script ran but drew nothing Tavotto could capture. Read it: are the plotting functions only defined, or is drawing behind a condition?",
        ),
        (
            ("grep_files", "finish"),
            "If another script is the real entry point, finish with outcome not_fixed and tell the user to open that script instead.",
        ),
        (
            ("write_launcher",),
            "Otherwise use write_launcher to add a launcher that calls the plotting code with the project's own data.",
        ),
    ),
    "script_uses_gui_dialog": _h(
        "gui_dialog",
        (
            ("write_launcher",),
            "The script opens a file or input dialog. Use write_launcher to add a launcher that replaces that call with the value the dialog would return (a path found in the project), before run_original().",
        ),
    ),
    "safe_file_operation_blocked": _h(
        "safe_blocked",
        (
            ("read_logs",),
            "Safe mode blocked a file operation the script needs (often reading back a file it just saved). Check read_logs for the operation.",
        ),
        (
            ("switch_execution", "propose_fix"),
            "If the script must really write and read files, switch_execution to the native mode (after propose_fix).",
        ),
    ),
}


def hint_for(shape: str, available: Callable[[str], bool]) -> tuple[str, str] | None:
    """形状 → (提示 id, 正文)。只说工具都可用的句子；一句都不剩就没有提示。"""
    hint = HINTS.get(shape)
    if hint is None:
        return None
    # 第一句是这条提示的主动作，后面的句子都接着它说：主动作调不到，整条不说（不留半截话）
    if not all(available(t) for t in hint.lines[0][0]):
        return None
    text = " ".join(line for tools, line in hint.lines if all(available(t) for t in tools))
    return (hint.id, text) if text else None


def tools_named(hints: Iterable[Hint] | None = None) -> set[str]:
    """表里点名过的全部工具（看护用例拿它核对注册表）。"""
    return {t for h in (hints or HINTS.values()) for tools, _ in h.lines for t in tools}
