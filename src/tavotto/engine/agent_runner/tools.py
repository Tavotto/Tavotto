"""工具层：`Tool`、任务工具集接口 `TaskToolset`、以及第一阶段唯一的实现 `FigureEditToolset`。

**ADR 0138 §四 / §三的落点**（改这里前先读）：

* `figure_edit`：模型只能读写**候选目录**里的文件，写只许目标脚本；**没有 shell、网络、MCP 工具**。
  路径一律 `realpath` 后判在候选目录内；越界、写非目标、符号链接出界都是**工具错误结果**——不抛、不执行。
* 将来 `data_to_figure` 的工具集**没有写文件工具**：模型只输出结构化的列映射建议，脚本由 Tavotto 的固定模板生成，
  模型生成的代码不被执行（ADR 0138 §四）。那个工具集实现 `TaskToolset` 时 `tools()` 里不许出现任何写盘工具。
* 默认只改表现形式（§三）：系统提示里写明；指令要求改科学内容时停下并说明需要确认。

工具说明照 Claude Code 的风格写：何时用、何时别用、失败时怎么办。
"""

from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .redact import NO_REDACTION, Redactor
from .wires import ToolResult, ToolSpec

READ_LIMIT_BYTES = 200 * 1024
LIST_LIMIT = 200


# --------------------------------------------------------------------------- 契约
@dataclass(frozen=True)
class Limits:
    max_turns: int = 24
    max_total_tokens: int = 400_000
    max_seconds: float = 600.0
    max_output_tokens: int = 8192
    #: 估计的上下文 token 超过它就压缩早先的 read_file 结果
    compress_at_tokens: int = 60_000
    same_error_limit: int = 3


@dataclass
class RunEnvelope:
    """一次运行的输入（桥接层之后会由 `ai_tasks.TaskEnvelope` 映射而来；这里不 import 它，免得把 rendercore 拖进来）。"""

    task_type: str
    cwd: Path
    target: str  # 相对 cwd
    instruction: str
    limits: Limits = field(default_factory=Limits)
    #: 调用方注入的检查回调：`() -> (ok, 报告文本)`。没注入 = `check` 工具返回 not available。
    check: Callable[[], tuple[bool, str]] | None = None
    #: 已知密钥的过滤器：写进目标脚本的内容含密钥 → `secret_in_content` 拒写（文件会被提交进用户项目）
    redactor: Redactor = NO_REDACTION


class TaskToolset(Protocol):
    """一种任务类型的工具集。`tools()` 每次运行现建（工具带本次运行的状态，例如「已读过目标脚本」）。"""

    task_type: str

    def tools(self, envelope: RunEnvelope) -> list[Tool]: ...

    def system_prompt(self, envelope: RunEnvelope) -> str: ...

    def budget(self, envelope: RunEnvelope) -> Limits: ...


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[dict], ToolResult]

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.schema)


# --------------------------------------------------------------------------- 系统提示
# 稳定部分在前、逐字不变（提示缓存）；每次运行不同的东西（目标脚本名、用户指令）不在这里：
# 目标名放在第一条用户消息里。
SYSTEM_PROMPT = """\
You are the figure-editing assistant built into Tavotto, a desktop editor for scientific figures.
You change how ONE plotting script (matplotlib or a similar scientific plotting script) looks.
You work in a scratch copy of the project directory; the user reviews your change before it is applied.

Scope rules (these are not negotiable):
- By default you only change presentation: colors, line styles and widths, markers, fonts and sizes, labels,
  legends, ticks, spines, grids, figure size, layout and spacing.
- Do NOT change the data, remove or add data points, change normalization, fitting, error definitions, units,
  or what an axis means, unless the user explicitly asks. If the instruction asks for a change to the scientific
  content (for example "remove the outliers"), do not make it: stop and reply that it needs the user's confirmation,
  and say what exactly would change.
- Make the smallest edit that satisfies the instruction. Keep the author's code style, names and comments.
- Do not add new dependencies, do not read or write anything outside the project directory,
  and never try to run the script yourself: you have no shell.

How to work:
1. Read the target script with read_file before you edit it. Use list_dir if you need to see what else is there.
2. Change it with edit_file (small exact replacements). Use write_file only if edit_file cannot express the change.
3. After editing, call check. If it reports a problem, fix it and check again.
4. Finish with one or two sentences saying what you changed. Reply in the language of the user's instruction.
"""

_PATH_DESC = "Path relative to the project directory."


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _err(name: str, call_id: str, code: str, message: str) -> ToolResult:
    return ToolResult(call_id, name, message, is_error=True, code=code, summary=f"error: {code}")


def _secret_err(name: str, call_id: str) -> ToolResult:
    return _err(
        name,
        call_id,
        "secret_in_content",
        "The content contains a credential and was not written. Never put keys or tokens into the script.",
    )


# --------------------------------------------------------------------------- figure_edit
class FigureEditSession:
    """一次 figure_edit 运行里工具共享的状态：候选根、目标、是否已读、是否改过。"""

    def __init__(self, envelope: RunEnvelope):
        self.root = os.path.realpath(envelope.cwd)
        self.target_rel = envelope.target
        self.check_fn = envelope.check
        self.redactor = envelope.redactor
        self.read_target = False
        self.mutated = False
        self.envelope_target = envelope.target
        self.target_path = self._resolve(envelope.target)  # 构造时目标就须合法，否则 None

    def target_usable(self) -> bool:
        """目标必须是候选目录里一个能读的普通文本文件（不是符号链接）——否则 read_file 永远开不了写工具。"""
        if self.target_path is None:
            return False
        raw = os.path.join(self.root, self.envelope_target)
        if os.path.islink(raw):
            return False
        return not isinstance(self._read_text(self.target_path), str)

    # ---- 路径判据：一处 -------------------------------------------------
    def _resolve(self, rel: object) -> str | None:
        """候选目录内的 realpath；越界 / 非法回 None。符号链接先解开再判：链出界即越界。"""
        if not isinstance(rel, str) or not rel or "\x00" in rel:
            return None
        real = os.path.realpath(os.path.join(self.root, rel))
        try:
            inside = os.path.commonpath([os.path.normcase(real), os.path.normcase(self.root)]) == (
                os.path.normcase(self.root)
            )
        except ValueError:  # Windows 上不同盘符
            inside = False
        return real if inside else None

    def _is_target(self, real: str) -> bool:
        return (
            self.target_path is not None
            and os.path.normcase(real) == os.path.normcase(self.target_path)
            and not os.path.islink(os.path.join(self.root, self.target_rel))
        )

    # ---- 读文本（统一换行） -----------------------------------------------
    def _read_text(self, real: str) -> tuple[str, str] | str:
        """(文本[\\n 换行], eol) 或错误码。"""
        if not os.path.isfile(real):
            return "not_a_file"
        if os.path.getsize(real) > READ_LIMIT_BYTES:
            return "too_large"
        raw = Path(real).read_bytes()
        if len(raw) > READ_LIMIT_BYTES:
            return "too_large"
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return "not_text"
        # 以文件里**先出现**的那种换行为准（混合换行时一处 CRLF 不该把整份改成 CRLF）
        first_nl = text.find("\n")
        eol = "\r\n" if first_nl > 0 and text[first_nl - 1] == "\r" else "\n"
        return text.replace("\r\n", "\n"), eol

    def _write_text(self, real: str, text: str, eol: str) -> None:
        data = text.replace("\n", eol).encode("utf-8")
        fd, tmp = tempfile.mkstemp(prefix=".agent-", dir=os.path.dirname(real))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            shutil.copymode(real, tmp)
            os.replace(tmp, real)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _commit_write(
        self, name: str, call_id: str, real: str, before: str, after: str, eol: str
    ) -> ToolResult | None:
        """所有写目标文件的路径唯一经过这里：检查**写入后的完整文本**，再原子写。

        只看单次的 `new` / `content` 会被拆成多次写入绕过（先写 `sk-…PAD`，再把 `PAD` 换成
        剩余后缀：两次都不含完整密钥，结果脚本却含）。规则：写入后的文本含密钥、而写入前的
        文本不含，才拒（`secret_in_content`，文件零改动）；原文件本来就含密钥（用户自己写的）
        时不因此拒绝普通编辑。拒绝返回 ToolResult，成功返回 None。
        """
        if len(after.replace("\n", eol).encode("utf-8")) > READ_LIMIT_BYTES:
            # 按写回时的真实编码长度判（\n 恢复成 \r\n 会膨胀）：写出去读不回来的文件不写
            return _err(name, call_id, "too_large", "Result is larger than the allowed size.")
        if self.redactor.contains(after) and not self.redactor.contains(before):
            return _secret_err(name, call_id)
        self._write_text(real, after, eol)
        self.mutated = True
        return None

    # ---- 工具 ------------------------------------------------------------
    def read_file(self, args: dict, call_id: str = "") -> ToolResult:
        name = "read_file"
        real = self._resolve(args.get("path"))
        if real is None:
            return _err(name, call_id, "path_outside", "Path is not inside the project directory.")
        got = self._read_text(real)
        if isinstance(got, str):
            msg = {
                "not_a_file": "Not a regular file (use list_dir to see what exists).",
                "too_large": f"File is larger than {READ_LIMIT_BYTES // 1024} KB; reading it is refused.",
                "not_text": "File is not UTF-8 text.",
            }[got]
            return _err(name, call_id, got, msg)
        text, _eol = got
        if self._is_target(real):
            self.read_target = True
        n = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        sha = _sha(text.encode("utf-8"))[:8]
        rel = os.path.relpath(real, self.root).replace(os.sep, "/")
        return ToolResult(
            call_id,
            name,
            text if text else "(empty file)",
            summary=f"read {n} lines",
            compact=f"[already read {rel} (lines 1-{n}, sha {sha}); read it again if you need the text]",
        )

    def list_dir(self, args: dict, call_id: str = "") -> ToolResult:
        name = "list_dir"
        rows = []
        try:
            entries = sorted(os.scandir(self.root), key=lambda e: e.name)
        except OSError:
            return _err(name, call_id, "list_failed", "Could not list the project directory.")
        for e in entries[:LIST_LIMIT]:
            if e.is_symlink():
                rows.append(f"{e.name}  (symlink, not followed)")
            elif e.is_dir(follow_symlinks=False):
                rows.append(f"{e.name}/")
            else:
                try:
                    rows.append(f"{e.name}  ({e.stat(follow_symlinks=False).st_size} bytes)")
                except OSError:
                    rows.append(e.name)
        more = f"\n... and {len(entries) - LIST_LIMIT} more" if len(entries) > LIST_LIMIT else ""
        return ToolResult(
            call_id,
            name,
            "\n".join(rows) + more or "(empty)",
            summary=f"listed {len(rows)} entries",
        )

    def _target_for_write(self, name: str, call_id: str, path: object) -> str | ToolResult:
        real = self._resolve(path)
        if real is None:
            return _err(name, call_id, "path_outside", "Path is not inside the project directory.")
        if not self._is_target(real):
            return _err(
                name,
                call_id,
                "not_target",
                f"Only the target script ({self.target_rel}) may be modified.",
            )
        return real

    def edit_file(self, args: dict, call_id: str = "") -> ToolResult:
        name = "edit_file"
        old, new = args.get("old"), args.get("new")
        if not isinstance(old, str) or not isinstance(new, str):
            return _err(name, call_id, "bad_arguments", "`old` and `new` must be strings.")
        real = self._target_for_write(name, call_id, args.get("path", self.target_rel))
        if isinstance(real, ToolResult):
            return real
        if not self.read_target:
            return _err(
                name,
                call_id,
                "not_read",
                "Read the target script with read_file before editing it.",
            )
        if old == "":
            return _err(name, call_id, "empty_old", "`old` must not be empty.")
        if old == new:
            return _err(name, call_id, "no_change", "`old` and `new` are identical; nothing to do.")
        got = self._read_text(real)
        if isinstance(got, str):
            return _err(name, call_id, got, "The target script can no longer be read as text.")
        text, eol = got
        old_n, new_n = old.replace("\r\n", "\n"), new.replace("\r\n", "\n")
        count = text.count(old_n)
        if count == 0:
            return _err(
                name,
                call_id,
                "old_not_found",
                "`old` was not found in the file. It must match exactly, including whitespace and "
                "indentation. Read the file again and copy the text verbatim.",
            )
        if count > 1:
            return _err(
                name,
                call_id,
                "old_not_unique",
                f"`old` appears {count} times in the file. Include more surrounding lines so that "
                "it matches exactly once.",
            )
        updated = text.replace(old_n, new_n, 1)
        refused = self._commit_write(name, call_id, real, text, updated, eol)
        if refused is not None:
            return refused
        changed = sum(
            1
            for ln in difflib.ndiff(text.splitlines(), updated.splitlines())
            if ln.startswith("+ ")
        )
        changed = max(changed, 1) if updated != text else 0
        return ToolResult(
            call_id,
            name,
            f"Edited {self.target_rel}.",
            summary=f"changed {changed} lines",
            mutated=True,
        )

    def write_file(self, args: dict, call_id: str = "") -> ToolResult:
        name = "write_file"
        content = args.get("content")
        if not isinstance(content, str):
            return _err(name, call_id, "bad_arguments", "`content` must be a string.")
        real = self._target_for_write(name, call_id, args.get("path", self.target_rel))
        if isinstance(real, ToolResult):
            return real
        if not self.read_target:
            return _err(
                name,
                call_id,
                "not_read",
                "Read the target script with read_file before writing it.",
            )
        if len(content.encode("utf-8")) > READ_LIMIT_BYTES:
            return _err(name, call_id, "too_large", "Content is larger than the allowed size.")
        got = self._read_text(real)
        if isinstance(got, str):
            # 读过之后目标被换成非文本 / 超大：不覆盖那份新字节（同 edit_file）
            return _err(name, call_id, got, "The target script can no longer be read as text.")
        before, eol = got
        content_n = content.replace("\r\n", "\n")
        refused = self._commit_write(name, call_id, real, before, content_n, eol)
        if refused is not None:
            return refused
        changed = sum(
            1
            for ln in difflib.ndiff(before.splitlines(), content.splitlines())
            if ln.startswith("+ ")
        )
        return ToolResult(
            call_id,
            name,
            f"Wrote {self.target_rel}.",
            summary=f"rewrote file ({changed} lines changed)",
            mutated=True,
        )

    def check(self, args: dict, call_id: str = "") -> ToolResult:
        name = "check"
        if self.check_fn is None:
            return _err(name, call_id, "not_available", "check is not available in this session.")
        try:
            ok, report = self.check_fn()
        except Exception as exc:  # noqa: BLE001 - 回调是调用方的；只报类型名
            return _err(
                name, call_id, "check_crashed", f"check failed to run ({type(exc).__name__})."
            )
        return ToolResult(
            call_id,
            name,
            report or ("OK" if ok else "Problems found."),
            summary="check passed" if ok else "check found problems",
            # 报告是给模型的信息，不是工具错误；失败的 check 不计入「同一工具错误」
        )


def _obj(props: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


class FigureEditToolset:
    task_type = "figure_edit"

    def tools(self, envelope: RunEnvelope) -> list[Tool]:
        s = FigureEditSession(envelope)
        if not s.target_usable():
            # 越界、不存在、目录、符号链接、超大、非 UTF-8：起跑即 bad_target，不发任何（计费的）模型请求
            raise ValueError("target not usable")

        def bind(fn):
            return lambda args: fn(args)

        return [
            Tool(
                "read_file",
                "Read a text file in the project directory (at most 200 KB). Use it on the target script before "
                "you edit it, and on helper files such as a shared style module. Do not use it to look for files "
                "(use list_dir) and do not re-read a file you already have unless it changed.",
                _obj({"path": {"type": "string", "description": _PATH_DESC}}, ["path"]),
                bind(s.read_file),
            ),
            Tool(
                "list_dir",
                "List the files directly inside the project directory (one level, no recursion). Use it when you "
                "are unsure which files exist. It cannot list anything outside the project directory.",
                _obj({}, []),
                bind(s.list_dir),
            ),
            Tool(
                "edit_file",
                "Replace one exact piece of text in the target script. `old` must appear in the file exactly once: "
                "if it appears several times or not at all the call fails and nothing changes, so include enough "
                "surrounding lines to make it unique and copy whitespace verbatim. Prefer this over write_file; "
                "make several small edits rather than one large one. You must read_file the target first. "
                "It can only modify the target script.",
                _obj(
                    {
                        "path": {
                            "type": "string",
                            "description": _PATH_DESC + " Optional; must be the target.",
                        },
                        "old": {
                            "type": "string",
                            "description": "Exact text to replace (non-empty).",
                        },
                        "new": {"type": "string", "description": "Replacement text."},
                    },
                    ["old", "new"],
                ),
                bind(s.edit_file),
            ),
            Tool(
                "write_file",
                "Overwrite the whole target script with `content`. Only use this when edit_file cannot express "
                "the change (for example a large restructuring); it is easy to lose code this way, so otherwise "
                "use edit_file. You must read_file the target first. It can only modify the target script.",
                _obj(
                    {
                        "path": {
                            "type": "string",
                            "description": _PATH_DESC + " Optional; must be the target.",
                        },
                        "content": {
                            "type": "string",
                            "description": "The complete new file content.",
                        },
                    },
                    ["content"],
                ),
                bind(s.write_file),
            ),
            Tool(
                "check",
                "Check the current state of the target script (for example that it still runs and that the "
                "figure is still produced). Call it after you finished editing, and again after fixing a "
                "reported problem. It takes no arguments and changes nothing.",
                _obj({}, []),
                bind(s.check),
            ),
        ]

    def system_prompt(self, envelope: RunEnvelope) -> str:
        return SYSTEM_PROMPT

    def budget(self, envelope: RunEnvelope) -> Limits:
        return envelope.limits


TOOLSETS: dict[str, TaskToolset] = {FigureEditToolset.task_type: FigureEditToolset()}
