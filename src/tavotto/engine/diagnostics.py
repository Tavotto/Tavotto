"""一键诊断包：把排障需要的东西一次性收齐，并且**先脱敏再交出去**。

存在的理由：剩下那些没法提前覆盖的 bug，来回问十次（「你什么系统」「装的哪个
Python」「日志在哪」）才能定位一次。有了这个包，用户点一下、发过来，一次定位。

包里有什么：
    report.json   版本 / 系统 / 安装方式 / 数据目录 / 渲染解释器 / matplotlib /
                  端口 / AI CLI 探测结果 / 项目与注册表概况 / 最近错误
    app.log       最近若干行日志
    config.json   用户配置（**密钥已抹掉**）

脱敏两件事，缺一不可：
    * 密钥：api_key / token / 形如 sk-… 的串一律换成 ***；
    * 个人路径：用户主目录换成 ~，用户名换成 <user>。
用户把包发到群里或贴进 issue 时，不该顺手泄露自己的密钥和目录结构。

纯标准库，Flask 父进程 import。
"""

from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import platform
import re
import sys
import time
import zipfile
from pathlib import Path

from . import ai_bridge, bootstrap, config, diagnostics_frontend, pool, runtime, telemetry, updater

LOG_TAIL_LINES = 400
ERROR_TAIL = 30  # 报告里单列的最近错误条数
#: 报告里带几份 worker.log 的尾巴、每份多少行。渲染进程死在哪一句只有它知道
#: （app.log 里只有一句「渲染进程退出了」）——#435 的诊断包里 24 条错误全是空壳，
#: 正是因为这份日志不在包里。按 mtime 取最近的几份：用户报的问题就是最近发生的。
WORKER_LOG_FILES = 3
#: 报告里每份 worker.log 最多带多少行证据（按块截，见 `last_blocks_within`）。
WORKER_LOG_TAIL_LINES = 60
#: 抽证据之前先看日志的最后多少行。**必须比一段完整的 faulthandler 栈长**：先按 60 行
#: 截、再抽证据的话，一段 80 行的崩溃栈只剩帧行、没有头，状态机一条都不认，报告里
#: 就没有崩溃位置（评审 #443）。与 app.log 的 `LOG_TAIL_LINES` 同一个量级。
WORKER_LOG_SCAN_LINES = 400
#: 读文件时的字节上限（一份 worker.log 可能被脚本刷到几十 MB）。`_scan_start` 会从这一截
#: 里回溯到最近一个崩溃头，所以它要远大于一段完整的崩溃栈。
WORKER_LOG_SCAN_BYTES = 4 * 1024 * 1024

#: 诊断包整体格式的版本。**读包的人不该靠 Tavotto 版本号去猜 schema**
#: ——manifest.json 自报这个数。1 = 只有 report/app.log/config 的那一版；
#: 2 = 增加了 frontend-state.json / interaction-trace.jsonl / manifest.json。
BUNDLE_SCHEMA_VERSION = 2
#: 两个子 schema 各自独立演进（ADR 0016 §20）。读取方**忽略不认识的字段**。
FRONTEND_SNAPSHOT_SCHEMA = 1
TRACE_SCHEMA = 1

# 形如 sk-…、ghp_…、长十六进制串等，宁可多抹一点
_SECRET_VALUE = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{10,}|[A-Fa-f0-9]{32,})\b")
_SECRET_KEYS = ("api_key", "token", "secret", "password", "auth")

#: 假名标识：不是密钥，但也不该被顺手复制进 issue 或群聊。
#: 它把这台机器的**全部**遥测事件串在一起——诊断包里带上它，等于把
#: 「这条 issue 的作者」和后台那串匿名行为对上号，而排障一次都用不到它。
#: 开关本身（enabled / consent）不脱敏：知道遥测开没开对排障是有用的。
_PSEUDONYM_KEYS = ("install_id", "anonymous_id", "distinct_id")

#: 用户自己的「东西清单」：项目名 + 路径逐条列着，对排障零帮助，
#: 对隐私却是实打实的暴露面（用户在往 issue 上贴自己所有课题的名字）。
#: 只留条数。当前打开的那个项目仍在 report.json 的 project 段里。
_USER_INVENTORY_KEYS = ("recent_projects", "projects")


def _install_id() -> str:
    """本机的匿名遥测标识（没同意过就是空串）。只用来把它从输出里抹掉。"""
    try:
        from . import telemetry

        return telemetry.install_id() or ""
    except Exception:  # noqa: BLE001 — 脱敏不该被它拖垮
        return ""


def _redact_text(text: str) -> str:
    """文本脱敏：先抹密钥再抹个人路径。顺序无所谓，但三步都不能省。"""
    text = _SECRET_VALUE.sub("***", text)
    # 按**值**再抹一次假名标识：按键名那道只挡得住结构化的
    # `"install_id": "..."`，挡不住它偶然出现在别的字符串里。
    ident = _install_id()
    if ident:
        text = text.replace(ident, "***")
    home = os.path.expanduser("~")
    if home and home != os.sep:
        text = text.replace(home, "~")
        # Windows 上日志里可能混着两种分隔符写法
        text = text.replace(home.replace("\\", "/"), "~")
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    if len(user) >= 3:  # 太短的用户名replace 会误伤正常词
        text = re.sub(rf"\b{re.escape(user)}\b", "<user>", text)
    return text


def redact_text(text: str) -> str:
    """文本脱敏的**对外名字**（`engine/deprepair.py` 的安装日志走它）。

    刻意不让别的模块各写一份「抹掉主目录名」：那种规则一旦有两份，其中一份
    迟早漏掉某一条（Windows 的两种分隔符写法就是这么漏过的）。安装日志里
    pip 特有的那条（index 地址可能带凭据）归 deprepair，其余都在这里。
    """
    return _redact_text(text)


def _redact_obj(obj):
    """结构化数据脱敏：按键名判定的敏感字段整体换掉，其余走文本规则。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = str(k).lower()
            if any(s in key for s in _SECRET_KEYS) or key in _PSEUDONYM_KEYS:
                out[k] = "***" if v else v
            elif key in _USER_INVENTORY_KEYS:
                # 「用户还有哪些项目」是一份**目录清单**：每条都带项目名与路径，
                # 而排障一次都用不到它——要看的是**当前**这个项目（report.json
                # 的 project 段已经有了）。只留条数，清单本身不出门。
                out[k] = {"count": len(v)} if isinstance(v, (list, dict)) else v
            else:
                out[k] = _redact_obj(v)
        return out
    if isinstance(obj, list):
        return [_redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return _redact_text(obj)
    return obj


def _log_path() -> Path:
    return config.data_dir() / "cache" / "app.log"


def _log_tail(n: int = LOG_TAIL_LINES) -> list[str]:
    try:
        lines = _log_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-n:]


def recent_errors(lines: list[str], limit: int = ERROR_TAIL) -> list[str]:
    """app.log 尾巴里的错误条目：ERROR 行，以及每段 traceback **最后那一句**。

    以前只留含 `Traceback` 的那一行，于是 24 条 `Traceback (most recent call last):`
    并排躺在报告里，而每一段真正说了什么（`ModuleNotFoundError: …`、
    `OSError: [WinError 5] …`）一个字都没带出来——报告长了一屏，信息量为零。
    这里把 traceback 头与它的收尾异常行配成一条；文件路径那些帧不进报告
    （脱敏面更小，读的人要的也只是那一句）。
    """
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if "Traceback (most recent call last):" in ln:
            j = i + 1
            # 帧行以空白开头（`  File …` / 源码行 / `    ^^^`）；第一条不缩进的
            # 非空行就是异常本身。链式异常（`The above exception…`）中间会再出现
            # 一段 traceback，各自配对，不合并。
            while j < len(lines) and (not lines[j].strip() or lines[j][:1].isspace()):
                j += 1
            tail = lines[j].strip() if j < len(lines) else ""
            # 收尾那句与 worker 证据同一条规则（`_closer_for_export`：只留异常类型，
            # ImportError 家族只留加载器形状）——它的 message 同样可能是用户数据
            # （评审 #443 第四、七轮）
            out.append(f"{ln.strip()} → {_closer_for_export(tail)}" if tail else ln)
            i = j + 1 if tail else j
            continue
        if " ERROR " in ln:
            out.append(shorten_paths(ln))
        i += 1
    return out[-limit:]


#: worker.log 里**允许进诊断包**的只有两种**结构块**，不认单行的长相：
#:   * Python traceback 块：`Traceback (most recent call last):` 头 → 若干 `File "…", line N`
#:     帧行（帧下面那行源码丢掉）→ 第一条不缩进的非空行是收尾的异常行，块到此为止；
#:   * faulthandler 块：`Fatal Python error` / `Windows fatal exception` 头 →
#:     `Current thread` / `Thread 0x` 行与 `File "…", line N` 帧行 → `Extension modules` 收尾。
#: **不再按行首长相放行**（评审 #443 第二轮 P1）：用户脚本的 stdout 也进这份日志，
#: `print("RuntimeError: patient-123")` 或 `print("[guard] …")` 长得和引擎的证据一模一样，
#: 按前缀放行就是把用户数据当证据带出门。异常行只在它**收尾一段 traceback** 时算数——
#: 来历（前面那串帧）跟着它一起在。`[guard]` 这类引擎标记行不进包：它们不是崩溃证据，
#: 而用户完全可以打印出同样的前缀。
_TB_HEADER = re.compile(r"^Traceback \(most recent call last\):$")
_TB_FRAME = re.compile(r'^\s+File "(?P<path>[^"]*)", line (?P<line>\d+)')
#: 收尾的异常行：`ExcType: message` 或裸 `ExcType`（`KeyboardInterrupt`），类型名是点分标识符。
#: `next sample: patient-124` 这种词间带空格的不算——块没有合法收尾就整块不算。
_TB_CLOSER = re.compile(r"^[A-Za-z_][\w.]*(?::\s.*|:)?$")
#: 收尾行里**保留信息**的异常类型：它们的 message 是解释器 / 加载器生成的（缺哪个模块、
#: 哪个 DLL 加载失败），正是排障要的；其余类型的 message 是自由文本，`traceback.print_exc()`
#: 里 `KeyError: 'patient-123'` 与引擎自己的一模一样，分不出来历就只留类型名。
_CLOSER_KEEP_MESSAGE = frozenset({"ImportError", "ModuleNotFoundError"})
_FH_HEADER = re.compile(r"^(?:Fatal Python error|Windows fatal exception): \S")
_FH_THREAD = re.compile(r"^(?:Current thread|Thread) 0x[0-9a-fA-F]+")
_FH_FOOTER = re.compile(r"^Extension modules\b")
#: 崩溃头里**允许原样出门**的故障名——闭集：CPython 的信号名与 Windows 的异常名。
#: 其它（`Py_FatalError` 的自由文本、用户 print 的伪装）一律 `…`。
_FH_KNOWN_FAULTS = re.compile(
    r"^(?:Segmentation fault|Bus error|Illegal instruction|Floating-point exception|Aborted"
    r"|Stack overflow|access violation|stack overflow|int divide by zero|float divide by zero"
    r"|code 0x[0-9a-fA-F]+)$"
)
#: **块要完整才算证据**（评审 #443 第四轮）：traceback 块 = 头 + ≥1 帧 + 合法收尾；
#: faulthandler 块 = 头 + ≥1 `Current thread` / `Thread 0x…` + ≥1 帧。用户
#: `print("Fatal Python error: patient-123")` 只有一个头、`print("Traceback (most recent
#: call last):")` 后面跟一行数据——都凑不齐一个块，整块不算。
#: 链式异常的两句连接语——**逐字**匹配（用户 `print("During handling … patient-123")`
#: 那种带尾巴的不算），且只在它前面紧挨着一段刚收尾的 traceback 块时保留。
_TB_CHAIN = re.compile(
    r"^(?:The above exception was the direct cause of the following exception:"
    r"|During handling of the above exception, another exception occurred:)$"
)

#: 绝对路径的两种写法：Windows（盘符 / UNC，正反斜杠都认）与 POSIX（至少两段，免得把
#: argparse usage 里的 `-c/--clstr` 当成路径）。
#: Windows 那支的中间段允许带空格（`D:\\Study Data\\a.pdf`），只要后面还跟着分隔符——
#: 末段仍以空白收尾。POSIX 裸路径无法分辨空格是不是路径的一部分，靠引号那一支。
_ABS_PATH = re.compile(
    r"""(?:[A-Za-z]:[\\/]|\\\\)(?:[^\\/'"`<>|\r\n]*?[\\/])*[^\\/\s'"`<>|]*"""
    r"""|/(?:[^/\s'"`<>|]+/)+[^/\s'"`<>|]*"""
)


def _shorten_path(match: re.Match) -> str:
    """一条绝对路径 → 只留能定位的那一截。

    README 承诺包里不含「完整的本地文件路径」，且文件名一律换成不可逆的短哈希
    （`file:…`）；`_redact_text` 只认当前主目录，D 盘、外接盘、`\\\\wsl.localhost\\…`
    上的项目路径它一个字都不动。三档：

    * `site-packages/<已知第三方包>/…`：保留**包名**，文件名照样哈希
      （`…/site-packages/matplotlib/file:2f0c1e…py`）。包名来自闭集 `_KNOWN_SITE_PACKAGES`，
      出门的只有「属于哪个库」这一位信息；库的文件名是公开的、可枚举的，读的人拿包里的
      文件名逐个哈希就能对上，而用户的文件名不可枚举——同一个哈希对两种人两种意义。
      路径分量的名字证明不了来历（`/mnt/site-packages/numpy/private-study/patient.py`，
      评审 #443 第七、八轮），而这个进程里未必装着 matplotlib（它在 worker 的解释器里），
      按「真实安装根」验不可靠；结构上不带任何用户能控制的字符串才是稳的。
    * Tavotto 自己的引擎源码：`tavotto/engine/<引擎目录里真实存在的文件>` 保留；
    * 其余（用户的脚本、数据文件）只留 `…/file:<sha1 前 10 位><扩展名>`：行号还在，
      同一个文件的哈希稳定，读的人能对上「同一份」，反推不回名字。
    """
    raw = match.group(0)
    parts = [seg for seg in re.split(r"[\\/]+", raw) if seg]
    if not parts:
        return raw
    lowered = [seg.lower() for seg in parts]
    name = parts[-1]
    stem, dot, ext = name.rpartition(".")
    suffix = f".{ext}" if dot and stem and len(ext) <= 8 else ""
    hashed = "file:" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10] + suffix
    # `site-packages/<已知包>/…`：只留包名这一位来自闭集的信息，文件名照样哈希——
    # 包名之后的每一段都可能是用户起的（评审 #443 第八轮），一律不带。
    if "site-packages" in lowered:
        idx = len(lowered) - 1 - lowered[::-1].index("site-packages")
        rest = parts[idx + 1 :]
        if rest and rest[0] in _KNOWN_SITE_PACKAGES:
            return f"…/site-packages/{rest[0]}" + (f"/{hashed}" if len(rest) > 1 else "")
    # Tavotto 自己的引擎源码：按**真实文件清单**验（`pool.WORKER_PY` 所在目录里有这个名字），
    # 不是看路径里有没有 `tavotto` 这一段。
    if len(parts) >= 3 and lowered[-3] == "tavotto" and lowered[-2] == "engine":
        if name in _ENGINE_FILES:
            return "…/tavotto/engine/" + name
    return "…/" + hashed


#: `site-packages/<这些>/…` 保留包名：我们发行 / 认识的第三方科学栈与命令行库。
#: 闭集，不在表里的包名连同文件名一起哈希——不认识的名字可能是用户自己 pip 安装的私有包。
_KNOWN_SITE_PACKAGES = frozenset(
    {
        "matplotlib", "mpl_toolkits", "numpy", "pandas", "scipy", "seaborn", "PIL",
        "contourpy", "cycler", "fontTools", "kiwisolver", "packaging", "pyparsing", "dateutil",
        "six", "click", "typer", "docopt", "fire", "IPython", "matplotlib_inline",
    }
)  # fmt: skip
#: 引擎目录里真实存在的文件名（一次性读，进程内不会变）。
_ENGINE_FILES = (
    frozenset(p.name for p in pool.WORKER_PY.parent.iterdir() if p.is_file())
    if pool.WORKER_PY.parent.is_dir()
    else frozenset()
)


#: 引号里的路径整体算一个（`File "C:\\Clinical Trial\\x.py", line 3` / `'/mnt/a b/c.csv'`）：
#: 裸路径的正则在第一个空格就停了，会把 `Clinical Trial\\…` 那一截留在外面（评审 #443）。
_QUOTED_PATH = re.compile(r"""(['"])((?:[A-Za-z]:[\\/]|\\\\|/)[^'"]*)\1""")


def shorten_paths(line: str) -> str:
    def quoted(match: re.Match) -> str:
        inner = match.group(2)
        # 只认「像路径」的：POSIX 至少两段（`/--clstr` 不算）
        if inner.startswith("/") and inner.count("/") < 2:
            return match.group(0)
        return match.group(1) + _shorten_path(re.match(r".*", inner, re.S)) + match.group(1)

    line = _QUOTED_PATH.sub(quoted, line)

    def unquoted(match: re.Match) -> str:
        # 引号那一支已经缩过的（`…/site-packages/…`）别再缩一遍
        if match.start() > 0 and line[match.start() - 1] == "…":
            return match.group(0)
        return _shorten_path(match)

    return _ABS_PATH.sub(unquoted, line)


#: 加载器生成的 ImportError 文案——**只认这几种形状**，且里面的名字必须是点分标识符：
#: `ImportError` 是普通的公开异常类，用户 `raise ImportError("patient-123 …")` 再
#: `print_exc()` 一样是这个类型（评审 #443 第六轮）。形状对不上的一律只留类型。
_LOADER_MESSAGES = (
    re.compile(
        r"^No module named '(?P<a>[A-Za-z_][\w.]*)'(?:; '(?P<b>[A-Za-z_][\w.]*)' is not a package)?$"
    ),
    re.compile(r"^cannot import name '(?P<a>[A-Za-z_]\w*)' from '(?P<b>[A-Za-z_][\w.]*)'"),
    re.compile(r"^DLL load failed while importing (?P<a>[A-Za-z_]\w*)\b"),
)


def _frame_for_export(line: str) -> str:
    """帧行 → `  File "<缩写路径>", line N`：函数名不带（`in analyze_patient_123` 是用户的
    标识符，评审 #443 第七轮），faulthandler 的 `line N in f` 与 traceback 的
    `line N, in f` 归成一种写法。"""
    m = _TB_FRAME.match(line)
    assert m is not None  # 调用方已经用同一个正则筛过
    path = shorten_paths(f'"{m.group("path")}"')[1:-1] if m.group("path") else ""
    return f'  File "{path}", line {m.group("line")}'


def _fh_header_for_export(line: str) -> str:
    head, _, fault = line.partition(": ")
    return f"{head}: {fault.strip()}" if _FH_KNOWN_FAULTS.match(fault.strip()) else f"{head}: …"


def _fh_thread_for_export(line: str) -> str:
    m = re.match(r"^((?:Current thread|Thread) 0x[0-9a-fA-F]+)", line)
    return (
        f"{m.group(1)} (most recent call first):"
        if m
        else "Current thread (most recent call first):"
    )


def _fh_footer_for_export(line: str) -> str:
    m = re.search(r"\(total: \d+\)\s*$", line)
    return f"Extension modules: … {m.group(0)}" if m else "Extension modules: …"


#: 收尾行里**允许原样出门**的异常类型名：本进程 `builtins` 里的异常类（闭集——traceback
#: 打印 builtins 与 `__main__` 里定义的类都不带模块前缀，`Patient_123Error` 与 `KeyError`
#: 长得一样，只有查表分得开），以及 `matplotlib.units.ConversionError` 这种以已知第三方包
#: 开头的点分名。其它一律 `exc:<sha1 前 10 位>`：用户 `class Patient_123Error(Exception)`
#: 再 `traceback.print_exc()`，类型名就是用户源码里的标识符（评审 #443 第九轮）。
_BUILTIN_EXCEPTIONS = frozenset(
    name
    for name, obj in vars(builtins).items()
    if isinstance(obj, type) and issubclass(obj, BaseException)
)


def _exception_type_for_export(head: str) -> str:
    name = head.strip()
    if name in _BUILTIN_EXCEPTIONS:
        return name
    segments = name.split(".")
    if len(segments) > 1 and segments[0] in _KNOWN_SITE_PACKAGES:
        if all(seg.isidentifier() for seg in segments):
            return name
    return "exc:" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]


def _closer_for_export(line: str) -> str:
    """traceback 的收尾行 → 进包的形态：类型名按闭集放行（否则哈希），自由文本的
    message 换成 `…`。

    用户脚本 `except … : traceback.print_exc()` 打出来的块与引擎自己的结构一模一样
    （评审 #443 第五轮）：结构齐全证明不了来历，能保证的只有「message 不出门」、
    「类型名不是用户起的」。`ImportError` / `ModuleNotFoundError` 的 message 只在长成
    加载器那几种形状时保留，而且只保留形状本身（模块名是标识符，`DLL load failed while
    importing X` 之后的操作系统文案也不带）。
    """
    head, sep, message = line.partition(":")
    exc_type = _exception_type_for_export(head)
    if not sep or not message.strip():
        return exc_type
    if head.strip() in _CLOSER_KEEP_MESSAGE:
        for pattern in _LOADER_MESSAGES:
            m = pattern.match(message.strip())
            if m:
                return f"{exc_type}: {m.group(0)}"
    return f"{exc_type}: …"


def evidence_blocks(tail: list[str]) -> tuple[list[list[str]], int]:
    """worker.log 尾巴 → (可进诊断包的证据块, 略去的行数)。

    只认上面说的两种结构块；留下的行里绝对路径缩成 `…/site-packages/pkg/mod.py` 或
    `…/文件名`。块外的一切（脚本自己 print 的、matplotlib 的噪音、引擎的标记行）
    都略去只计数。按块交出去是为了让调用方**按块截尾**：一个块被拦腰截断之后
    剩下的帧行没有头，读的人不知道它们是谁的。
    """
    blocks: list[list[str]] = []
    dropped = 0
    i = 0
    n = len(tail)
    #: 上一个非空行是不是刚收尾的 traceback 块——链接语只在这时才算数
    just_closed_tb = False
    while i < n:
        ln = tail[i].rstrip()
        if not ln.strip():
            i += 1
            continue
        if _TB_HEADER.match(ln):
            block = [ln]
            frames = 0
            i += 1
            closed = False
            # 帧行留、源码行丢，直到第一条不缩进的非空行——那是异常本身
            while i < n:
                cur = tail[i].rstrip()
                if not cur.strip():
                    i += 1
                    continue
                if cur[:1].isspace():
                    if _TB_FRAME.match(cur):
                        block.append(_frame_for_export(cur))
                        frames += 1
                    else:
                        dropped += 1
                    i += 1
                    continue
                if _TB_CLOSER.match(cur):
                    block.append(_closer_for_export(cur))  # 收尾的异常行
                    i += 1
                    closed = True
                break
            if not (closed and frames):
                # 没凑齐「头 + 帧 + 收尾」的不是 traceback，是长得像的用户输出
                dropped += len(block)
                just_closed_tb = False
                continue
            # 链式异常：上一块以链接语结尾时，这一块并进去（它们本来就是一段）
            if blocks and _TB_CHAIN.match(blocks[-1][-1]):
                blocks[-1].extend(block)
            else:
                blocks.append(block)
            just_closed_tb = True
            continue
        if _FH_HEADER.match(ln):
            block = [_fh_header_for_export(ln)]
            threads = frames = 0
            i += 1
            while i < n:
                cur = tail[i].rstrip()
                if not cur.strip():
                    i += 1
                    continue
                if _FH_THREAD.match(cur):
                    block.append(_fh_thread_for_export(cur))
                    threads += 1
                    i += 1
                    continue
                if _TB_FRAME.match(cur):
                    block.append(_frame_for_export(cur))
                    frames += 1
                    i += 1
                    continue
                if _FH_FOOTER.match(cur):
                    block.append(_fh_footer_for_export(cur))
                    i += 1
                break
            if not (threads and frames):
                dropped += len(block)
                just_closed_tb = False
                continue
            blocks.append(block)
            just_closed_tb = False
            continue
        if just_closed_tb and _TB_CHAIN.match(ln):
            # 逐字相同的链接语，且紧跟在一段刚收尾的 traceback 后面：接到那一块上
            blocks[-1].append(ln)
            just_closed_tb = False
            i += 1
            continue
        dropped += 1
        just_closed_tb = False
        i += 1
    # 以链接语收尾却没等到下一段 traceback（尾巴正好截在中间）：链接语本身不是证据
    if blocks and _TB_CHAIN.match(blocks[-1][-1]):
        blocks[-1].pop()
        dropped += 1
    return blocks, dropped


def evidence_lines(tail: list[str]) -> tuple[list[str], int]:
    """`evidence_blocks` 摊平成行（老口径）。"""
    blocks, dropped = evidence_blocks(tail)
    return [ln for block in blocks for ln in block], dropped


def last_blocks_within(blocks: list[list[str]], budget: int) -> list[list[str]]:
    """从尾巴往前**整块**地取，直到行数超预算；最后那一块再长也要——它是最新的崩溃。"""
    out: list[list[str]] = []
    total = 0
    for block in reversed(blocks):
        if out and total + len(block) > budget:
            break
        out.append(block)
        total += len(block)
    out.reverse()
    return out


def worker_log_tails(
    root: Path | None = None,
    *,
    project_dir: str | Path | None,
    files: int = WORKER_LOG_FILES,
    lines: int = WORKER_LOG_TAIL_LINES,
) -> list[dict]:
    """**当前项目**最近几份 `worker.log` 尾巴里的证据行（未脱敏——调用方经 `_redact_obj`）。

    只读、不起任何子进程。目录名是 `pool._cache_slug` 拼出来的 `<项目哈希>-<脚本名>`
    （一次性重放是 `_replay-<nonce>-<项目哈希>-<脚本名>`），只取哈希等于
    `pool.cache_digest(project_dir)` 的那些：诊断包是「当前打开的这个项目」的，
    别的项目的脚本名与报错一个字都不该跟着出门（评审 #443 P1）。**没打开项目就
    一份都不带**——没有项目就没有「属于谁」这个判据，宁可少给。
    `empty` 说的是这一份日志尾巴**整个**是空的（进程一个字没留下就没了——硬崩溃
    的形状），`omitted` 是按 `evidence_lines` 略去的行数。
    """
    if not project_dir:
        return []
    digest = pool.cache_digest(project_dir)
    owned = re.compile(rf"^(?:_replay-[0-9a-f]+-)?{re.escape(digest)}-")
    base = Path(root) if root is not None else pool.ENGINE_CACHE
    try:
        candidates = [
            p for p in base.glob("*/worker.log") if p.is_file() and owned.match(p.parent.name)
        ]
    except OSError:
        return []
    stamped: list[tuple[float, Path]] = []
    for p in candidates:
        try:
            stamped.append((p.stat().st_mtime, p))
        except OSError:
            continue
    stamped.sort(reverse=True)
    out: list[dict] = []
    for mtime, p in stamped[:files]:
        try:
            text = p.read_bytes()[-WORKER_LOG_SCAN_BYTES:].decode("utf-8", errors="replace")
        except OSError:
            continue
        all_lines = text.splitlines()
        scanned = all_lines[_scan_start(all_lines) :]
        blocks, omitted = evidence_blocks(scanned)
        kept = last_blocks_within(blocks, lines)
        out.append(
            {
                # README 承诺文件名一律换成不可逆的短哈希：目录名里带着脚本名，只出哈希。
                # 同一台机器上同一个 (项目, 脚本) 的哈希稳定，读的人能对上「是同一份」。
                "session": "session:"
                + hashlib.sha1(p.parent.name.encode("utf-8")).hexdigest()[:12],
                "replay": p.parent.name.startswith("_replay-"),
                "modified": _iso(mtime),
                "empty": not any(t.strip() for t in scanned),
                "omitted": omitted,
                "tail": "\n".join(ln for block in kept for ln in block),
            }
        )
    return out


def _scan_start(all_lines: list[str]) -> int:
    """从哪一行开始抽证据：默认最后 `WORKER_LOG_SCAN_LINES` 行，但**至少回溯到最近一个
    崩溃头**——`all_threads=True` 的 faulthandler 遇上几条深栈线程，一段就能超过 400 行，
    按行截会把唯一的 `Fatal Python error` 头切掉，后面的帧一条都不算（评审 #443）。"""
    start = max(0, len(all_lines) - WORKER_LOG_SCAN_LINES)
    for idx in range(len(all_lines) - 1, -1, -1):
        if _FH_HEADER.match(all_lines[idx]) or _TB_HEADER.match(all_lines[idx]):
            return min(start, idx)
    return start


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def install_kind() -> str:
    """怎么装的——升级指令、路径写权限、能不能自己修都由它决定。

    **这是安装方式的唯一出处**：诊断报告与遥测的 `distribution` 属性都调它。
    埋点为了拿这个值另写一份探测，就是制造第二个权威，两边迟早给出不同答案。
    """
    if pool.is_frozen():
        return "desktop"  # .app / .exe 独立应用
    return updater.install_method()


def _runtime_section() -> dict:
    """内置渲染 runtime 的现状 + **实测** import 结果。

    只在 runtime 完好时才真去 import：坏掉的时候那一轮探测必然全 None，
    白白让用户等上一分钟。
    """
    st = runtime.status()
    info = st.get("manifest") or {}
    section = {
        "present": st["present"],
        "valid": st["valid"],
        "expected": runtime.ships_bundled_runtime(),
        "root": st["root"],
        "code": st["code"],
        "python": (info.get("python") or {}).get("version"),
        "build": info.get("build") or {},
        "packages": info.get("packages") or {},
        "imports": {},
    }
    if st["valid"] and st["python"]:
        section["imports"] = runtime.probe_packages(st["python"])
    return section


def build_report(project: dict | None = None, port: int | None = None) -> dict:
    """结构化诊断报告（已脱敏）。project 由 app 层传入，避免这里反向依赖。"""
    from .. import __version__

    worker_python, worker_error = None, None
    try:
        worker_python = pool.find_worker_python()
    except pool.WorkerError as exc:
        worker_error = str(exc)

    mpl = bootstrap.matplotlib_version(worker_python) if worker_python else None
    caps = ai_bridge.capabilities()
    lines = _log_tail()
    errors = recent_errors(lines)

    report = {
        "tavotto": {
            "version": __version__,
            "install": install_kind(),
            "frozen": pool.is_frozen(),
            "executable": sys.executable,
            "port": port,
        },
        "system": {
            "platform": platform.platform(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "os_name": os.name,
            "encoding": {
                "stdout": getattr(sys.stdout, "encoding", None),
                "filesystem": sys.getfilesystemencoding(),
                "preferred": __import__("locale").getpreferredencoding(False),
            },
        },
        "paths": {
            "data_dir": str(config.data_dir()),
            "config_dir": str(config.config_dir()),
            "log": str(_log_path()),
        },
        "render": {
            "worker_python": worker_python,
            "worker_source": pool.source_of(worker_python) if worker_python else None,
            "worker_error": worker_error,
            "matplotlib": mpl,
            # 走 pool 那份读取器：诊断包要如实反映**实际生效的**那个值，
            # 用户设的是旧名 MM_WORKER_PYTHON 时直接读新名会报 null，
            # 于是「明明设了却没生效」在诊断包里看起来像「压根没设」。
            "env_override": pool.worker_python_env(),
            # 控制面（Rust supervisor / Python 池）+ 池里每条会话实际走的哪条。
            # workerd 建会话失败是静默回退的，「装了但没用上」只有这里看得出来。
            "control_plane": pool.control_plane(),
            # 「装了但用不了」全靠这一段：内置 runtime 在不在、装的是哪些版本、
            # 实测能不能 import。只贴 manifest 不够——杀毒软件隔离掉一个 .pyd
            # 时 manifest 照样完好。
            "bundled_runtime": _runtime_section(),
            # 渲染进程自己最后说了什么：崩在 import 哪一句、faulthandler 的栈、
            # 脚本自己的报错都只在这里。app.log 里对应的只有一句「渲染进程退出了」。
            "worker_logs": worker_log_tails(project_dir=(project or {}).get("figures_dir")),
        },
        # 每个已注册编码 Agent 的探测结论。**不含就绪检查的账号细节**——
        # 那条只回 ready/needs_auth/unknown，邮箱与组织名一个字都不出现。
        "ai": {
            entry["id"]: {
                "installed": entry["installed"],
                "enabled": entry["enabled"],
                "state": entry["state"],
                "path": entry["executable_path"],
                "source": entry["detection_source"],
                "version": entry["version"],
            }
            for entry in caps.get("agents", [])
        },
        "ai_endpoints": [
            {
                "id": e["id"],
                "label": e["label"],
                "agent": e["agent"],
                "base_url": e["base_url"],
                "has_key": e["has_key"],
            }
            for e in caps.get("endpoints", [])
        ],
        # 遥测**开没开**对排障有用（「我关了它为什么还联网」），
        # 所以这里给状态；假名 id 由 _redact_obj / _redact_text 抹掉。
        "telemetry": {
            "consent": telemetry.settings()["consent"],
            "enabled": telemetry.enabled(),
            "hard_disabled": telemetry.hard_disabled(),
        },
        "project": project or {"open": False},
        "recent_errors": errors,
    }
    return _redact_obj(report)


def render_text(report: dict) -> str:
    """把（已脱敏的）报告摊平成可粘贴的文本：`a.b.c: value` 一行一条。

    给设置里的「复制诊断」用。不另起一份采集逻辑——输入就是 `build_report()`
    的产物，所以脱敏那一道它天然过了；这里只做排版。列表按序号展开，长文本
    （最近错误）原样多行。
    """
    lines: list[str] = []

    def walk(prefix: str, value) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix}.{k}" if prefix else str(k), v)
        elif isinstance(value, list):
            if not value:
                lines.append(f"{prefix}: []")
            for i, v in enumerate(value):
                walk(f"{prefix}[{i}]", v)
        else:
            text = "" if value is None else str(value)
            if "\n" in text:
                lines.append(f"{prefix}:")
                lines.extend("    " + ln for ln in text.splitlines())
            else:
                lines.append(f"{prefix}: {text}")

    walk("", report)
    return "\n".join(lines) + "\n"


def build_bundle(
    project: dict | None = None,
    port: int | None = None,
    frontend: dict | None = None,
    frontend_dropped: bool = False,
) -> bytes:
    """诊断包 zip 的字节流（全部内容已脱敏）。

    `frontend` 是前端在用户点「导出诊断包」那一刻现采的载荷
    （`{frontend_state, interaction_trace}`，见 ADR 0016）。**它是可选的**：
    老的 GET 端点不带，出的包就是 schema 2 但只有老三件 + manifest。
    """
    report = build_report(project, port)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.json", json.dumps(report, ensure_ascii=False, indent=1))
        z.writestr("app.log", _redact_text("\n".join(_log_tail())))
        try:
            cfg = json.loads(config.config_path().read_text(encoding="utf-8"))
            z.writestr("config.json", json.dumps(_redact_obj(cfg), ensure_ascii=False, indent=1))
        except (OSError, ValueError):
            pass

        # ---- 前端状态与交互轨迹（ADR 0016）。没带就干脆不放这两个文件 ----
        snapshot, trace, truncated = _frontend_sections(frontend)
        # 请求体超限被整份丢掉时：包照出（用户要的是一个包，不是一个错误），
        # 但 manifest 必须如实说「这份 trace 不完整」
        truncated = truncated or frontend_dropped
        if snapshot is not None:
            z.writestr("frontend-state.json", json.dumps(snapshot, ensure_ascii=False, indent=1))
        if trace:
            z.writestr("interaction-trace.jsonl", diagnostics_frontend.trace_to_jsonl(trace))

        z.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema_version": BUNDLE_SCHEMA_VERSION,
                    "created_at": _now_iso(),
                    "tavotto_version": report.get("tavotto", {}).get("version"),
                    "contains_frontend_state": snapshot is not None,
                    "contains_interaction_trace": bool(trace),
                    "privacy_mode": "safe-default",
                    "trace_event_count": len(trace),
                    "trace_truncated": truncated,
                    "frontend_snapshot_schema": FRONTEND_SNAPSHOT_SCHEMA,
                    "trace_schema": TRACE_SCHEMA,
                },
                ensure_ascii=False,
                indent=1,
            ),
        )
        z.writestr("README.txt", _readme(snapshot is not None, bool(trace)))
    return buf.getvalue()


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _frontend_sections(frontend: dict | None) -> tuple[dict | None, list[dict], bool]:
    """前端载荷 → (快照, 事件列表, 有没有被截断)。**服务端的第二道校验在这里**。

    `frontend` 为 None（老的 GET 端点、或者前端没给）时三个都空，包里就没有
    那两个文件——**不放一个空壳**：空的 frontend-state.json 读起来像
    「前端当时什么状态都没有」，而真相是「这次根本没采集」。
    """
    if not isinstance(frontend, dict):
        return None, [], False
    snapshot = diagnostics_frontend.sanitize_snapshot(frontend.get("frontend_state"), _redact_text)
    trace, truncated = diagnostics_frontend.sanitize_trace(
        frontend.get("interaction_trace"), _redact_text
    )
    return snapshot, trace, truncated


def _readme(has_state: bool, has_trace: bool) -> str:
    """包里有什么、**没有什么**。双语——用户得看得懂自己在往 issue 上贴什么。

    「不含」那一段是承诺，不是免责声明：它对应的是代码里的字段 allowlist
    与服务端校验（ADR 0016 §4 / §8），不是「我们尽量不放」。
    """
    extra_zh, extra_en = "", ""
    if has_state:
        extra_zh += "- frontend-state.json：导出那一刻的前端状态摘要（匿名）\n"
        extra_en += "- frontend-state.json: anonymized snapshot of the app state\n"
    if has_trace:
        extra_zh += "- interaction-trace.jsonl：最近的编辑操作记录（匿名，一行一条）\n"
        extra_en += "- interaction-trace.jsonl: recent anonymized interaction events\n"
    return (
        "Tavotto 诊断包 / Tavotto diagnostic package\n"
        "\n"
        "包含 / This package contains:\n"
        "- report.json：系统、运行环境与探测结果\n"
        "- app.log：最近的应用日志\n"
        "- config.json：用户配置（密钥已抹掉）\n"
        "  （report.json 的 render.worker_logs 段：渲染进程日志里的 Python 报错块与崩溃栈——\n"
        "  只留 traceback 的帧行（文件名换成哈希 + 行号，函数名不带；第三方库的文件多留一个\n"
        "  包名）与异常类型名；崩溃栈只留故障名与帧行、扩展模块只留计数；报错文字、脚本自己\n"
        "  打印的内容与源码行已略去）\n" + extra_zh + "- manifest.json：本诊断包自身的格式说明\n"
        "\n"
        "- report.json: system and runtime information\n"
        "- app.log: recent Tavotto application logs\n"
        "- config.json: user configuration (secrets removed)\n"
        "  (report.json, render.worker_logs: Python traceback blocks and crash stacks from\n"
        "  the render process logs — only frame lines (hashed file name + line number, no\n"
        "  function names; files of known third-party libraries also carry the package name)\n"
        "  and the exception type; crash stacks keep the fault name, frames and the\n"
        "  extension-module count; error messages, anything your script printed, and source\n"
        "  lines are left out)\n"
        + extra_en
        + "- manifest.json: describes this package's own format\n"
        "\n"
        "不包含 / It does NOT intentionally contain:\n"
        "- 图中文字（标题、坐标轴标签、图例、标注）\n"
        "- Python 脚本与源代码\n"
        "- 科研数据、数据数组\n"
        "- SVG / PDF / PNG 图像内容\n"
        "- API 密钥、令牌\n"
        "- 完整的本地文件路径、用户名、主目录\n"
        "\n"
        "- text drawn inside your figures (titles, axis labels, legends, annotations)\n"
        "- Python source code or scripts\n"
        "- raw datasets or data arrays\n"
        "- SVG / PDF / PNG image content\n"
        "- API keys or tokens\n"
        "- full local file paths, usernames, or home directories\n"
        "\n"
        "仍会包含 / Still included: 当前打开的项目**文件夹名**（report.json 的\n"
        "project 段，排障需要它判断目录权限与注册表冲突）。其余项目的清单不出门。\n"
        "The folder name of the currently open project is included; the list of\n"
        "your other projects is not.\n"
        "\n"
        "文件名、路径与图内文字在诊断包里一律换成不可逆的短哈希（doc:… / "
        "panel:… / file:… / var:…），\n"
        "只用来判断「两个状态是不是同一个」，反推不回原值。\n"
        "Identifiers are replaced with irreversible short hashes; they only tell\n"
        "whether two states are the same, and cannot be reversed.\n"
        "\n"
        "发出去之前仍建议自己扫一眼。/ You may still want to skim it before sharing.\n"
    )
