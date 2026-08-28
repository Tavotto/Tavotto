"""`tavotto run` 的**稳定错误码表**（ADR 0021 §13）——中英同表，唯一出处。

## 为什么码和文案分家

调用方（桌面前端、Codex 插件、脚本）要按结果分诊：「解释器找不到」该提示
换一条路径，「桌面没装」该提示去下载，「脚本还在跑」该等一等——三件事的处置
完全不同。把它们塞进一句中文里，对面只能做字符串匹配，而文案是随时会改的。

所以：**`code` 稳定，`message` 随时可改**（与 `handoff.HandoffError` /
`cli.doctor` 的 `problems` 同一条纪律）。

## 为什么中英一起写在这里

加一条码却只给中文，等于把英文用户送回 traceback（issue #30 记的正是这一族）。
两种语言写在同一张表上，是让"漏了一种"在**加码的那一刻**就看得见，而不是等
前端 i18n 检查在几周后报一条孤儿 key。`tests/native/test_run_codes.py` 逐条
钉住：每个码两种语言都非空、占位符两侧一致。

纯标准库。CLI（不 import Flask）与 app 两侧都吃这一份。
"""

from __future__ import annotations

# --------------------------- invocation（脚本还没跑） ---------------------------
RUN_COMMAND_MISSING = "run_command_missing"
UNSUPPORTED_RUN_COMMAND = "unsupported_run_command"
UNSUPPORTED_PYTHON_OPTION = "unsupported_python_option"
INTERPRETER_NOT_FOUND = "interpreter_not_found"
INTERPRETER_NOT_EXECUTABLE = "interpreter_not_executable"
UNSUPPORTED_PYTHON_VERSION = "unsupported_python_version"
UNSUPPORTED_PYTHON_IMPLEMENTATION = "unsupported_python_implementation"
SCRIPT_TARGET_MISSING = "script_target_missing"
SCRIPT_TARGET_NOT_FILE = "script_target_not_file"
INVALID_MODULE_NAME = "invalid_module_name"
PROJECT_ROOT_INVALID = "project_root_invalid"
PROJECT_UNREADABLE = "project_unreadable"

# --------------------------- handoff descriptor ---------------------------
NATIVE_HANDOFF_INVALID = "native_handoff_invalid"
NATIVE_HANDOFF_EXPIRED = "native_handoff_expired"
NATIVE_HANDOFF_CONSUMED = "native_handoff_consumed"

# --------------------------- attach / relay ---------------------------
NATIVE_DESKTOP_REQUIRED = "native_desktop_required"
NATIVE_ATTACH_CANCELLED = "native_attach_cancelled"
NATIVE_ATTACH_TIMEOUT = "native_attach_timeout"
NATIVE_ATTACH_FAILED = "native_attach_failed"
NATIVE_RELAY_FAILED = "native_relay_failed"
NATIVE_AUTH_FAILED = "native_auth_failed"

# --------------------------- session 生命周期 ---------------------------
NATIVE_SESSION_CONFLICT = "native_session_conflict"
NATIVE_ASSET_CONFLICT = "native_asset_conflict"
NATIVE_SESSION_NOT_AT_BARRIER = "native_session_not_at_barrier"
NATIVE_SESSION_OFFLINE = "native_session_offline"
NATIVE_SESSION_ENDED = "native_session_ended"
NATIVE_SESSION_DISCONNECTED = "native_session_disconnected"
NATIVE_SESSION_UNKNOWN = "native_session_unknown"
BRIDGE_CHILD_EXITED = "bridge_child_exited"

# --------------------------- 结果 / 环境 ---------------------------
NO_FIGURE_CAPTURED = "no_figure_captured"
#: 有活跃 native 会话时拒绝装依赖。**与 `pool.ENVIRONMENT_MUTATING` 是反方向
#: 的一对**：那条是「正在装包，别起会话」，这条是「正在跑脚本，别装包」。
ENVIRONMENT_IN_USE_BY_NATIVE_SESSION = "environment_in_use_by_native_session"

#: 码 → {"zh": …, "en": …}。**两种语言都必须非空**（用例逐条钉）。
#: `{}` 占位符两侧必须一致——只在一侧带参数的文案，另一侧会静默丢掉信息。
MESSAGES: dict[str, dict[str, str]] = {
    RUN_COMMAND_MISSING: {
        "zh": "缺少要运行的命令。用法：tavotto run -- python 你的脚本.py",
        "en": "No command to run. Usage: tavotto run -- python your_script.py",
    },
    UNSUPPORTED_RUN_COMMAND: {
        "zh": "Tavotto Run 只能运行 Python 本身：tavotto run -- python 脚本.py"
        "（或 -m 模块）。不支持 {command}。",
        "en": "Tavotto Run only runs Python itself: tavotto run -- python script.py"
        " (or -m module). {command} is not supported.",
    },
    UNSUPPORTED_PYTHON_OPTION: {
        "zh": "不支持的 Python 选项 {option}。这一版只认 `python 脚本.py` 与 `python -m 模块`。",
        "en": "Unsupported Python option {option}. This version accepts only"
        " `python script.py` and `python -m module`.",
    },
    INTERPRETER_NOT_FOUND: {
        "zh": "找不到解释器 {interpreter}。Tavotto 绝不替你挑一个 Python——"
        "请先激活你的环境，或写出解释器的完整路径。",
        "en": "Interpreter {interpreter} not found. Tavotto never picks a Python for you —"
        " activate your environment first, or give the full path to the interpreter.",
    },
    INTERPRETER_NOT_EXECUTABLE: {
        "zh": "解释器 {interpreter} 不能执行（不是可执行文件，或权限不足）。",
        "en": "Interpreter {interpreter} cannot be executed (not an executable,"
        " or permission denied).",
    },
    UNSUPPORTED_PYTHON_VERSION: {
        "zh": "这个 Python 是 {version}，Tavotto Run 需要 {minimum} 或更高。",
        "en": "This Python is {version}; Tavotto Run needs {minimum} or newer.",
    },
    UNSUPPORTED_PYTHON_IMPLEMENTATION: {
        "zh": "这个 Python 是 {implementation}，Tavotto Run 这一版只支持 CPython。",
        "en": "This Python is {implementation}; Tavotto Run only supports CPython in this version.",
    },
    SCRIPT_TARGET_MISSING: {
        "zh": "找不到脚本 {target}（相对路径按当前工作目录解析）。",
        "en": "Script {target} not found (relative paths resolve against the"
        " current working directory).",
    },
    SCRIPT_TARGET_NOT_FILE: {
        "zh": "{target} 不是一个文件。",
        "en": "{target} is not a file.",
    },
    INVALID_MODULE_NAME: {
        "zh": "{target} 不是合法的模块名。",
        "en": "{target} is not a valid module name.",
    },
    PROJECT_ROOT_INVALID: {
        "zh": "--project 指定的不是一个目录：{project}",
        "en": "--project is not a directory: {project}",
    },
    PROJECT_UNREADABLE: {
        "zh": "项目目录读不了：{project}",
        "en": "Project directory is not readable: {project}",
    },
    NATIVE_HANDOFF_INVALID: {
        "zh": "这条 Tavotto Run 交接请求无效。",
        "en": "This Tavotto Run handoff request is not valid.",
    },
    NATIVE_HANDOFF_EXPIRED: {
        "zh": "这条 Tavotto Run 交接请求已过期，请重新运行原命令。",
        "en": "This Tavotto Run handoff request has expired; run the command again.",
    },
    NATIVE_HANDOFF_CONSUMED: {
        "zh": "这条 Tavotto Run 交接请求已经被处理过了。",
        "en": "This Tavotto Run handoff request has already been handled.",
    },
    NATIVE_DESKTOP_REQUIRED: {
        "zh": "Tavotto Run 需要 Tavotto 桌面应用。装一个（GitHub Releases），"
        "或用 TAVOTTO_DESKTOP_APP 指到它的可执行文件。",
        "en": "Tavotto Run needs the Tavotto desktop app. Install it (GitHub Releases),"
        " or point TAVOTTO_DESKTOP_APP at its executable.",
    },
    NATIVE_ATTACH_CANCELLED: {
        "zh": "已取消——你的脚本一行都没有运行。",
        "en": "Cancelled — not a single line of your script was run.",
    },
    NATIVE_ATTACH_TIMEOUT: {
        "zh": "等 Tavotto 桌面连接超时（{seconds}s）——你的脚本一行都没有运行。",
        "en": "Timed out waiting for the Tavotto desktop to connect ({seconds}s) —"
        " not a single line of your script was run.",
    },
    NATIVE_ATTACH_FAILED: {
        "zh": "Tavotto 桌面没能连上这次运行。",
        "en": "The Tavotto desktop could not attach to this run.",
    },
    NATIVE_RELAY_FAILED: {
        "zh": "Tavotto Run 的控制通道断了。",
        "en": "The Tavotto Run control channel failed.",
    },
    NATIVE_AUTH_FAILED: {
        "zh": "Tavotto Run 控制通道认证失败。",
        "en": "Authentication failed on the Tavotto Run control channel.",
    },
    NATIVE_SESSION_CONFLICT: {
        "zh": "这个项目已经有一个 Tavotto Run 会话在进行中。",
        "en": "This project already has a Tavotto Run session in progress.",
    },
    NATIVE_ASSET_CONFLICT: {
        "zh": "这张图已经绑在另一个 Tavotto Run 会话上了。那个会话结束后再试。",
        "en": "This figure is already bound to another Tavotto Run session."
        " Try again once that session ends.",
    },
    NATIVE_SESSION_NOT_AT_BARRIER: {
        "zh": "脚本正在运行，等下一个 Matplotlib figure。等它停下来才能编辑。",
        "en": "The script is running and waiting for the next Matplotlib figure."
        " Editing resumes when it stops.",
    },
    NATIVE_SESSION_OFFLINE: {
        "zh": "这张图来自已结束的 Tavotto Run 会话。重新运行原命令后可继续对象级编辑。",
        "en": "This figure came from a Tavotto Run session that has ended."
        " Re-run the original command to resume object-level editing.",
    },
    NATIVE_SESSION_ENDED: {
        "zh": "这个 Tavotto Run 会话已经结束。",
        "en": "This Tavotto Run session has ended.",
    },
    NATIVE_SESSION_DISCONNECTED: {
        "zh": "与 Tavotto Run 会话的连接断开了；你的脚本仍在自己继续运行。",
        "en": "Disconnected from the Tavotto Run session; your script keeps running on its own.",
    },
    NATIVE_SESSION_UNKNOWN: {
        "zh": "没有这个 Tavotto Run 会话。",
        "en": "No such Tavotto Run session.",
    },
    BRIDGE_CHILD_EXITED: {
        "zh": "你的 Python 还没连上控制通道就退出了（退出码 {code}）。",
        "en": "Your Python exited before connecting to the control channel (exit code {code}).",
    },
    NO_FIGURE_CAPTURED: {
        "zh": "脚本跑完了，但没有捕获到任何 Matplotlib figure。",
        "en": "The script finished, but no Matplotlib figure was captured.",
    },
    ENVIRONMENT_IN_USE_BY_NATIVE_SESSION: {
        "zh": "这个 Python 环境正在被 Tavotto Run 使用。请先结束正在运行的脚本，再安装依赖。",
        "en": "This Python environment is in use by Tavotto Run. Stop the running script"
        " before installing dependencies.",
    },
}

#: CLI 退出码（ADR 0021 §10.2）。**不把所有失败都返回 1。**
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CANCELLED = 3
EXIT_ATTACH_FAILED = 4
EXIT_TERMINATED = 5

#: 码 → CLI 退出码。**只覆盖"用户脚本还没启动"的那些**——脚本一旦起来，
#: 退出码就是它自己的（§10.2），这张表不参与。
EXIT_FOR_CODE: dict[str, int] = {
    RUN_COMMAND_MISSING: EXIT_USAGE,
    UNSUPPORTED_RUN_COMMAND: EXIT_USAGE,
    UNSUPPORTED_PYTHON_OPTION: EXIT_USAGE,
    INTERPRETER_NOT_FOUND: EXIT_USAGE,
    INTERPRETER_NOT_EXECUTABLE: EXIT_USAGE,
    UNSUPPORTED_PYTHON_VERSION: EXIT_USAGE,
    UNSUPPORTED_PYTHON_IMPLEMENTATION: EXIT_USAGE,
    SCRIPT_TARGET_MISSING: EXIT_USAGE,
    SCRIPT_TARGET_NOT_FILE: EXIT_USAGE,
    INVALID_MODULE_NAME: EXIT_USAGE,
    PROJECT_ROOT_INVALID: EXIT_USAGE,
    PROJECT_UNREADABLE: EXIT_USAGE,
    NATIVE_ATTACH_CANCELLED: EXIT_CANCELLED,
    NATIVE_DESKTOP_REQUIRED: EXIT_ATTACH_FAILED,
    NATIVE_ATTACH_TIMEOUT: EXIT_ATTACH_FAILED,
    NATIVE_ATTACH_FAILED: EXIT_ATTACH_FAILED,
    NATIVE_RELAY_FAILED: EXIT_ATTACH_FAILED,
    NATIVE_AUTH_FAILED: EXIT_ATTACH_FAILED,
    NATIVE_HANDOFF_INVALID: EXIT_ATTACH_FAILED,
    NATIVE_HANDOFF_EXPIRED: EXIT_ATTACH_FAILED,
    NATIVE_HANDOFF_CONSUMED: EXIT_ATTACH_FAILED,
    NATIVE_SESSION_CONFLICT: EXIT_ATTACH_FAILED,
    BRIDGE_CHILD_EXITED: EXIT_ATTACH_FAILED,
}


class RunError(RuntimeError):
    """`tavotto run` 的结构化失败。`code` 给机器，`message` 给人。

    **`fields` 只放能公开的东西**：路径、版本、数量。绝不放 token、
    完整环境、用户 argv 的值（ADR 0021 §15）。
    """

    def __init__(self, code: str, /, **fields):
        if code not in MESSAGES:
            raise KeyError(f"未登记的错误码: {code!r}（先加进 runcodes.MESSAGES）")
        self.code = code
        self.fields = fields
        super().__init__(message_for(code, **fields))

    def exit_code(self) -> int:
        return EXIT_FOR_CODE.get(self.code, EXIT_ATTACH_FAILED)

    def payload(self) -> dict:
        return {"ok": False, "code": self.code, "error": str(self), **self.fields}


def message_for(code: str, lang: str = "zh", /, **fields) -> str:
    """码 → 文案。未登记的码**当场抛**，不给一句"未知错误"糊过去。"""
    entry = MESSAGES.get(code)
    if entry is None:
        raise KeyError(f"未登记的错误码: {code!r}（先加进 runcodes.MESSAGES）")
    text = entry.get(lang) or entry["zh"]
    try:
        return text.format(**fields)
    except (KeyError, IndexError):
        # 少给一个占位符不该让报错本身变成崩溃——错误路径上尤其如此。
        return text
