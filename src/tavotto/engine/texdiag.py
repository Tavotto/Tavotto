"""TeX 工具链失败的分类：缺命令 / 缺宏包或字体 / 其他编译错误 / 认得是 TeX 但说不清原因。

`text.usetex` 的脚本在**独立原生 matplotlib** 里也会因为同一个原因失败（实测：缺 `type1ec.sty`，
`latex` 命令却在）。那不是 Tavotto 的渲染不兼容，也不是缺 Python 包——给一句「脚本执行失败」
加一段 LaTeX 日志，用户既不知道该装什么，也可能被引去 pip 装一个不存在的包。

**判据只读 matplotlib 自己的固定英文模板**（3.8–3.11 同文，`texmanager._run_checked_subprocess`、
`dviread.find_tex_file`），外加 LaTeX 自己的 `! LaTeX Error: File ... not found.` /
`! Font ... Metric (TFM) file not found.` 两行。不按「出现了 latex 这个词」猜：用户脚本自己的
异常文字里也可能有它。认不出的 TeX 失败落 `tex_failed`（只说「TeX 这一步失败了」），
**不是 TeX 的一律回 None**，交还原来的错误码。

失败出现在哪一步不影响分类：首次 build（脚本自己 savefig）、之后的 draw / render / export
（manifest 量文字、预览、全质量导出）都把同一条 matplotlib 异常交到父进程的错误收口
（`pool._error_of` / `pool._worker_error`），在那里分类一次。超时与取消有自己的码（看门狗 /
`cancel`），这里不碰：超时那一刻手上没有任何能证明「卡在 TeX」的证据。

纯标准库：父进程（Flask 侧）与测试都能 import。分类只是**读已经发生的那次执行**，不另起探测、
不执行用户代码——静态发现 / 扫描 / cache-only 查询的零执行契约不受影响。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 用户可见的稳定码（`errors:backend.*` 各有两种语言的文案）
CODE_COMMAND_MISSING = "tex_command_missing"
CODE_PACKAGE_MISSING = "tex_package_missing"
CODE_COMPILE_FAILED = "tex_compile_failed"
CODE_FAILED = "tex_failed"
ERROR_CODES = (CODE_COMMAND_MISSING, CODE_PACKAGE_MISSING, CODE_COMPILE_FAILED, CODE_FAILED)

KIND_COMMAND_MISSING = "command_missing"
KIND_PACKAGE_MISSING = "package_missing"
KIND_COMPILE_ERROR = "compile_error"
KIND_UNKNOWN = "unknown"
_CODE_OF = {
    KIND_COMMAND_MISSING: CODE_COMMAND_MISSING,
    KIND_PACKAGE_MISSING: CODE_PACKAGE_MISSING,
    KIND_COMPILE_ERROR: CODE_COMPILE_FAILED,
    KIND_UNKNOWN: CODE_FAILED,
}

#: matplotlib 的 TeX 链路会调起的命令（闭集；别的名字报 `other`，不把任意字符串带出去）
COMMANDS = frozenset({"latex", "dvipng", "dvips", "kpsewhich", "luatex", "ps2pdf", "pdftops"})

#: 模板一律按**固定子串 + 逐行**认，不用跨行 / 无界的正则：LaTeX 日志是外部文本、可以很长，回溯型
#: 正则在病态输入上是多项式时间（CodeQL py/polynomial-redos）。下面只剩一条有界的帧名正则。
_CMD_MISSING = ("Failed to process string with tex because ", " could not be found")
_COMPILE = " was not able to process the following string:"
_TEXMF = ("Matplotlib's TeX implementation searched for a file named '", "' in your texmf tree")
_LATEX_FILE = ("! LaTeX Error: File `", "' not found.")
_TFM = ("! Font ", " not loadable: Metric (TFM) file not found")
#: 没有模板可认、但 traceback 落在 matplotlib 的 TeX 模块里：是 TeX 这一步，原因说不清
_TEX_FRAME = re.compile(r"matplotlib[\\/](?:texmanager|dviread)\.py")
_SAFE_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,80}")
_TOKEN_LIMIT = 256
_LINE_LIMIT = 200


#: 各类文案里**必需**的占位符（`errors:backend.<code>`）：缺了就不能发这个码，否则界面露出字面的 `{{file}}`
_REQUIRED = {KIND_PACKAGE_MISSING: "file", KIND_COMPILE_ERROR: "line"}


def code_for(fact: object) -> str:
    """事实（`as_fact()` / `extra["tex"]`）→ 用户可见的码；**唯一**决定码的地方。

    必需参数缺失就降级成 `tex_failed`（文案只用 `{{error}}`，由前端从 traceback 填）：LaTeX 报的文件名
    被白名单拒掉（含空格 / 非 ASCII）、或日志里没有 `! …` 行时，宁可说「原因不明」也不露占位符。"""
    kind = fact.get("kind") if isinstance(fact, dict) else None
    if kind not in _CODE_OF:
        return CODE_FAILED
    need = _REQUIRED.get(kind)
    if need and not (isinstance(fact.get(need), str) and fact[need]):
        return CODE_FAILED
    return _CODE_OF[kind]


@dataclass(frozen=True)
class TexFailure:
    kind: str
    command: str = ""
    file: str = ""
    line: str = ""

    @property
    def code(self) -> str:
        return code_for(self.as_fact())

    def as_fact(self) -> dict:
        """挂在 `WorkerError.extra["tex"]` 上的结构化事实（键全是闭集或经过白名单清洗的值）。"""
        return {
            "kind": self.kind,
            **({"command": self.command} if self.command else {}),
            **({"file": self.file} if self.file else {}),
            **({"line": self.line} if self.line else {}),
        }


def _command(raw: str) -> str:
    name = raw.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name if name in COMMANDS else "other"


def _file(raw: str) -> str:
    name = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name if _SAFE_FILE.fullmatch(name) else ""


def _between(text: str, head: str, tail: str) -> str | None:
    """`head` 之后、同一行里 `tail` 之前的那段；没有回 None。纯子串查找，线性时间。"""
    i = text.find(head)
    if i < 0:
        return None
    start = i + len(head)
    end = text.find(tail, start)
    nl = text.find("\n", start)
    if end < 0 or (0 <= nl < end) or end - start > _TOKEN_LIMIT:
        return None
    return text[start:end]


def _token_before(text: str, marker: str) -> tuple[str, int] | None:
    """`marker` 前面紧挨着的那个不含空白的词与 `marker` 结束的位置；没有回 None。"""
    i = text.find(marker)
    if i < 0:
        return None
    head = text[max(0, i - _TOKEN_LIMIT) : i]
    words = head.split()
    if not words or head[-1:].isspace():
        return None
    return words[-1], i + len(marker)


def _tex_report(report: str, command: str) -> TexFailure:
    """`latex was not able to process …` 之后的 LaTeX 输出 → 缺文件 / 编译错误。逐行看 `! …`。"""
    first_bang = ""
    for raw in report.splitlines():
        line = raw.strip()
        if not line.startswith("! "):
            continue
        name = _between(line, *_LATEX_FILE)
        if name is not None:
            return TexFailure(KIND_PACKAGE_MISSING, command=command, file=_file(name))
        if line.startswith(_TFM[0]) and _TFM[1] in line:
            spec = line[len(_TFM[0]) : line.index(_TFM[1])]
            # `T1/cmr/m/n/10=ecrm1000 at 10.0pt`：`=` 之后、` at ` 之前是字体名
            font = spec.partition("=")[2].partition(" at ")[0]
            if font:
                return TexFailure(KIND_PACKAGE_MISSING, command=command, file=_file(font + ".tfm"))
        if not first_bang:
            first_bang = line[2:].strip()[:_LINE_LIMIT]
    return TexFailure(KIND_COMPILE_ERROR, command=command, line=first_bang)


def classify(text: str | None) -> TexFailure | None:
    """一次失败的 message + traceback 文本 → `TexFailure`；不是 TeX 链路的失败回 None。"""
    text = text or ""
    cmd = _between(text, *_CMD_MISSING)
    if cmd and not any(c.isspace() for c in cmd):
        return TexFailure(KIND_COMMAND_MISSING, command=_command(cmd))
    name = _between(text, *_TEXMF)
    if name is not None:
        # `dviread.find_tex_file` 把「kpsewhich 起不来」与「文件真的不在」吞成同一句（实测：PATH 上
        # 没有 TeX、DVI 又命中了 matplotlib 的 tex 缓存时，报的正是 `cmr9.tfm` 找不到）——证据不足以
        # 判成缺宏包，归「原因不明」，文件名只作事实留着
        return TexFailure(KIND_UNKNOWN, file=_file(name))
    hit = _token_before(text, _COMPILE)
    if hit is not None:
        return _tex_report(text[hit[1] :], _command(hit[0]))
    if _TEX_FRAME.search(text):
        return TexFailure(KIND_UNKNOWN)
    return None


KINDS = frozenset(_CODE_OF)


def clean_fact(fact: dict) -> dict:
    """跨进程收到的 `tex` 事实再过一遍闭集 / 白名单（不信任对端拼的值）。"""
    out = {"kind": fact["kind"]}
    command = fact.get("command")
    if isinstance(command, str) and command:
        out["command"] = command if command in COMMANDS else "other"
    name = fact.get("file")
    if isinstance(name, str) and _file(name):
        out["file"] = _file(name)
    line = fact.get("line")
    if isinstance(line, str) and line:
        out["line"] = line[:_LINE_LIMIT]
    return out


def params_of(fact: object) -> dict:
    """`extra["tex"]` → 文案占位符（`command` / `file` / `line`），值都是字符串；认不出的事实回空。"""
    if not isinstance(fact, dict) or fact.get("kind") not in _CODE_OF:
        return {}
    return {
        k: str(fact[k])
        for k in ("command", "file", "line")
        if isinstance(fact.get(k), str) and fact[k]
    }
