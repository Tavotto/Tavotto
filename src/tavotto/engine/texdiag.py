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

_CMD_MISSING = re.compile(r"Failed to process string with tex because (\S+) could not be found")
_COMPILE = re.compile(r"(\S+) was not able to process the following string:")
_LATEX_FILE = re.compile(r"! LaTeX Error: File `([^'\n]+)' not found\.")
_TFM = re.compile(r"! Font [^\n=]*=(\S+) at [^\n]* not loadable: Metric \(TFM\) file not found")
_TEXMF = re.compile(
    r"Matplotlib's TeX implementation searched for a file named '([^'\n]+)' in your texmf tree"
)
_BANG = re.compile(r"^! (.+)$", re.M)
#: 没有模板可认、但 traceback 落在 matplotlib 的 TeX 模块里：是 TeX 这一步，原因说不清
_TEX_FRAME = re.compile(r"matplotlib[\\/](?:texmanager|dviread)\.py")
_SAFE_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,80}")
_LINE_LIMIT = 200


@dataclass(frozen=True)
class TexFailure:
    kind: str
    command: str = ""
    file: str = ""
    line: str = ""

    @property
    def code(self) -> str:
        return _CODE_OF[self.kind]

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


def classify(text: str | None) -> TexFailure | None:
    """一次失败的 message + traceback 文本 → `TexFailure`；不是 TeX 链路的失败回 None。"""
    text = text or ""
    m = _CMD_MISSING.search(text)
    if m:
        return TexFailure(KIND_COMMAND_MISSING, command=_command(m.group(1)))
    m = _TEXMF.search(text)
    if m:
        # `dviread.find_tex_file` 把「kpsewhich 起不来」与「文件真的不在」吞成同一句（实测：PATH 上
        # 没有 TeX、DVI 又命中了 matplotlib 的 tex 缓存时，报的正是 `cmr9.tfm` 找不到）——证据不足以
        # 判成缺宏包，归「原因不明」，文件名只作事实留着
        return TexFailure(KIND_UNKNOWN, file=_file(m.group(1)))
    m = _COMPILE.search(text)
    if m:
        command = _command(m.group(1))
        report = text[m.end() :]
        f = _LATEX_FILE.search(report)
        if f:
            return TexFailure(KIND_PACKAGE_MISSING, command=command, file=_file(f.group(1)))
        f = _TFM.search(report)
        if f:
            return TexFailure(
                KIND_PACKAGE_MISSING, command=command, file=_file(f.group(1) + ".tfm")
            )
        bang = _BANG.search(report)
        line = bang.group(1).strip()[:_LINE_LIMIT] if bang else ""
        return TexFailure(KIND_COMPILE_ERROR, command=command, line=line)
    if _TEX_FRAME.search(text):
        return TexFailure(KIND_UNKNOWN)
    return None


def params_of(fact: object) -> dict:
    """`extra["tex"]` → 文案占位符（`command` / `file` / `line`），值都是字符串；认不出的事实回空。"""
    if not isinstance(fact, dict) or fact.get("kind") not in _CODE_OF:
        return {}
    return {
        k: str(fact[k])
        for k in ("command", "file", "line")
        if isinstance(fact.get(k), str) and fact[k]
    }
