"""发送问题反馈的诊断包：`detail-…` / `optional-…` 条目（bundle schema 8，ADR 0118 修订 2026-10-13）。

**只有「发送」路径用它。** 导出到文件的 `GET/POST /api/diagnostics/bundle` 与「复制诊断」不调本模块，
那一份仍是严格脱敏、用户可能贴进公开 issue 的包。发送包多出来的东西全部放在 ZIP 的扁平条目 `detail-…` / `optional-…` 里（服务端 ZIP 检查要求条目名无目录、整包 ≤ 32 条、压缩比 ≤ 200；多文件的类别各打成一个内层 zip），
老三件（report.json / app.log / config.json）的名字与语义不动。

维护者裁决（2026-10-13）：包以「能帮维护者详细定位问题」为准，**不再因为报错或文件里可能带着用户文字就
丢掉**；发送前的逐次确认（能看到包里有哪些文件、能把同一份 ZIP 存到本地）是最后一道关。所以这里：

* 默认收：报错原文与完整调用栈、sidecar / app / worker 日志尾巴、会话状态文件、最近的请求记录、样式改动、
  渲染环境、运行记录、桌面壳与安装信息、存储坑、前端 console 环、显示环境、网络概况；
* 勾选才收（`DetailOptions`，默认全 false、不记忆）：A 脚本源码、B 系统崩溃报告、C 会话损坏副本的内容；
* **始终处理**（`Scrubber`，发送包所有新文件）：家目录 → `~`、系统用户名 → `<user>`，秘密
  （凭证 / cookie / nonce / API key / token / 代理 userinfo）一律去掉。项目文件夹名、文件名、图里的文字保留。

体积：每类一个份额；总量超限时 `fit` 按优先级从低往高截断（勾选项最低：C < B < A，之后才是默认项
的末尾几类），截断写进 manifest 的 `truncated`。核心文件（report.json 等）不截。

**没勾「脚本源码」就一个字节源码都不进包**：除了不放脚本文件，日志与报错里 Python traceback 的源码行
（`File …, line N` 下面缩进的那几行）也在这里剥掉（`strip_source_lines`），金丝雀看护见
`tests/test_diag_send_detail.py`。
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import ipaddress
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import zlib
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, diagnostics, layoutsession, logsafe, perfprobe, pool, runtime, updater

LOG = logging.getLogger("tavotto.diagdetail")

DETAIL_DIR = "detail"
#: detail 条目内部各文件的形状版本（与 bundle schema 各自演进；读取方忽略不认识的字段）
DETAIL_SCHEMA = 1

# ---------------------------------------------------------------------------
# 类别与优先级（编号 = 保留优先级；数字大的先被截断）
# ---------------------------------------------------------------------------
#: kind id → 优先级。1–12 是默认项（与 lead 的编号一致），13–15 是勾选项：A 源码 > B 崩溃报告 > C 损坏副本
PRIORITY: dict[str, int] = {
    "errors": 1,
    "logs": 2,
    "state": 3,
    "requests": 4,
    "overrides": 5,
    "render_env": 6,
    "runs": 7,
    "shell": 8,
    "storage": 9,
    "console": 10,
    "display": 11,
    "network": 12,
    "source": 13,
    "crash_reports": 14,
    "corrupt_sessions": 15,
}

#: 多文件的类别各打成**一个内层 zip**（内层可以有目录与 index.json）：服务端 ZIP 检查要求条目名扁平且整包 ≤ 32 条，
#: 其余类别一类一个扁平条目。内层 zip 在外层里 STORED（已经压缩过）。
CONTAINERS: dict[str, str] = {
    "state": "detail-state.zip",
    "overrides": "detail-overrides.zip",
    "source": "optional-source.zip",
    "crash_reports": "optional-crash-reports.zip",
    "corrupt_sessions": "optional-corrupt-sessions.zip",
}

#: 日志里 kind 位明文放行的闭集（`logsafe.known` 要求模块常量）
KINDS = frozenset(PRIORITY)

MiB = 1024 * 1024

#: 每类的原始字节份额（解压后）。总量超限时才会进一步截断；份额本身是「每类最多收这么多」
SHARE: dict[str, int] = {
    "errors": 1 * MiB,
    "logs": 8 * MiB,
    "state": 2 * MiB,
    "requests": 256 * 1024,
    "overrides": 2 * MiB,
    "render_env": 256 * 1024,
    "runs": 1 * MiB,
    "shell": 64 * 1024,
    "storage": 64 * 1024,
    "console": 512 * 1024,
    "display": 32 * 1024,
    "network": 32 * 1024,
    "source": 16 * MiB,
    "crash_reports": 16 * MiB,
    "corrupt_sessions": 4 * MiB,
}

APP_LOG_TAIL = 4 * MiB
SIDECAR_LOG_TAIL = 1 * MiB
WORKER_LOG_TAIL = 1 * MiB
WORKER_LOG_FILES = 3
ERROR_RECORDS = 40
ERROR_RECORD_CHARS = 16 * 1024
TRACEBACKS_PER_LOG = 5
STATE_FILE_CAP = 512 * 1024
CORRUPT_FILE_CAP = 512 * 1024
CORRUPT_FILES = 3
SOURCE_FILE_CAP = 1 * MiB
SOURCE_FILES = 100
CRASH_FILES = 40
CRASH_WINDOW_S = 7 * 24 * 3600
PROBE_TIMEOUT_S = 10
DNS_TIMEOUT_S = 2.0
RING_CAPACITY = 400

# ---------------------------------------------------------------------------
# 选项（确认框里的三个复选框）
# ---------------------------------------------------------------------------
OPTION_KEYS = ("source", "crash_reports", "corrupt_sessions")


@dataclasses.dataclass(frozen=True)
class DetailOptions:
    """三个勾选项。**默认全 false**；引擎侧白名单校验：只认字面 `True`（不是 1 / "true" / 非空）。"""

    source: bool = False
    crash_reports: bool = False
    corrupt_sessions: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {k: bool(getattr(self, k)) for k in OPTION_KEYS}


#: 勾选项 → 它对应的附件类别
OPTION_KINDS = {
    "source": "source",
    "crash_reports": "crash_reports",
    "corrupt_sessions": "corrupt_sessions",
}


def options_of(kept: list) -> dict[str, bool]:
    """最终包里**实际带着**哪些勾选附件（按保留下来的条目推导，不是按请求）。"""
    kinds = {i.kind for i in kept}
    return {k: OPTION_KINDS[k] in kinds for k in OPTION_KEYS}


def options_from_query(raw: str | None) -> dict[str, bool]:
    """`?options=source,crash_reports` → `{"source": True, "crash_reports": True}`；只认 `OPTION_KEYS` 里的名字。"""
    names = {n.strip() for n in (raw or "").split(",")}
    return {k: True for k in OPTION_KEYS if k in names}


def parse_options(raw: object) -> DetailOptions:
    """请求体里的 `detail_options` → `DetailOptions`。不认识的键丢掉，值不是字面 `True` 就是 false。"""
    if not isinstance(raw, dict):
        return DetailOptions()
    return DetailOptions(**{k: raw.get(k) is True for k in OPTION_KEYS})


@dataclasses.dataclass
class Sources:
    """app 层递进来的、本模块不该自己去找的东西（保持本模块不依赖 Flask 与「当前项目」）。"""

    project: dict | None = None  # `_diagnostics_project_status()`
    scripts: list[str] = dataclasses.field(default_factory=list)  # 项目登记的脚本（项目相对路径）
    baked: dict | None = None  # `load_baked(ctx)`：{stem: {"versions": [...]}}
    task_section: dict | None = None  # `taskdiag.bundle_section(..., limit=24)`
    frontend: dict | None = None  # 经 `diagnostics_frontend.sanitize_detail` 的前端 detail 子对象
    registry: object | None = (
        None  # 项目的脚本注册表（把焦点面板的 runtime 素材 id 解析成 script / stem）
    )


@dataclasses.dataclass
class DetailRequest:
    """一次 prepare 的 detail 请求：勾选项 + 前端 `detail` 子对象（已经过 `sanitize_detail`）。"""

    options: DetailOptions = dataclasses.field(default_factory=DetailOptions)
    frontend: dict = dataclasses.field(default_factory=dict)


#: `fit` 重建 ZIP 的次数上限（首建之外最多再建这么多次）。64 MiB 上限下各类份额之和（`SHARE`，约 51 MiB）本身就小于
#: 预算（63 MiB），最坏的全勾、各类超份额也只会在份额阶段被截，`fit` 通常一次建成；上限只是兜底。
FIT_MAX_REBUILDS = 24

#: 截断预算总额 = 上限 - 余量（64 MiB 上限下为 63 MiB）：留给 manifest（截断记录）、ZIP 目录与压缩比改存带来的增量
FIT_SLACK = 1 * MiB


@dataclasses.dataclass
class Item:
    """一个将进 ZIP 的 detail 文件。`shrink(cap)` 给出不超过 cap 字节的缩小版；没有就整份保留或整份丢。"""

    kind: str
    path: str  # 扁平类别：外层条目名；`CONTAINERS` 里的类别：内层 zip 里的路径
    data: bytes
    shrink: Callable[[int], bytes] | None = None
    #: 内容被份额 / 单文件上限截过时的原始字节数（None = 没截）
    original_bytes: int | None = None
    #: 采集阶段整份放不下而没收的字节数（崩溃报告按文件整份取舍）：fit 把它算进 manifest 的 `truncated`
    dropped_bytes: int = 0

    @property
    def size(self) -> int:
        return len(self.data)


# ---------------------------------------------------------------------------
# 进程内请求记录环（不写盘）
# ---------------------------------------------------------------------------
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class RequestRing:
    """最近的后端请求：方法、Flask 路由**规则**、状态码、耗时、错误码。**没有请求体、查询串、实参。**"""

    def __init__(self, capacity: int = RING_CAPACITY) -> None:
        self._dq: collections.deque[dict] = collections.deque(maxlen=capacity)
        self._lock = threading.Lock()

    def record(
        self, method: str, rule: str, status: int, ms: int | None, code: object = None
    ) -> None:
        entry = {
            "t": int(time.time() * 1000),
            "method": str(method)[:8],
            "rule": str(rule)[:200],
            "status": int(status),
            "ms": ms,
            "code": code if isinstance(code, str) and _CODE_RE.match(code) else None,
        }
        with self._lock:
            self._dq.append(entry)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._dq)

    def clear(self) -> None:
        with self._lock:
            self._dq.clear()

    @property
    def capacity(self) -> int:
        return self._dq.maxlen or 0


REQUESTS = RequestRing()


# ---------------------------------------------------------------------------
# 脱敏：家目录 → ~，用户名 → <user>，秘密去掉
# ---------------------------------------------------------------------------
_GENERIC_USERNAMES = frozenset(
    {"user", "root", "admin", "administrator", "guest", "default", "test"}
)

#: 分隔符两侧允许的空白：ASCII 空格 / 制表符，加 NBSP、en/em 空格…、窄 NBSP、全角空格（不含换行）
_SEP_SPACE = "[ \t  -   　]"
#: `key=value` / `key: value` / `"key": "value"` / `'key': 'value'` 的**键 token + 分隔符**。只做「找位置」：键 token 是一段连续的
#: `[\w.\-]`（含中文键字符），**整段贪婪取、不设长度上限**；起点由 lookbehind 钉在 token 开头，所以不会逐位回溯成 O(n²)（日志里
#: 几十万字符的十六进制 / base64 长串照样线性）。是不是秘密键由 `is_secret_key(整个 token)` 判断（与结构化侧同一个判据，键再长也能判）。
#: 看护：`test_scrubber_is_linear_on_long_tokens`、`TestLongKeys`
_KV_SECRET = re.compile(
    rf"""(?x)
    (?<![\w.\-\\])\\*["']?(?P<key>[\w.\-]+)\\*["']?
    (?P<sep>{_SEP_SPACE}*[:=：＝]{_SEP_SPACE}*)
    """
)
# Cookie / Authorization / Proxy-Authorization 头行不再另走行正则：它们的键名在 `_SECRET_WORDS` 里，值同样交给 `_scan_value`
# （值可以是跨行的三引号串，行正则只抹第一行）
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_SCHEME = re.compile(r"(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]{0,31}://")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")
_REDACTED = "<redacted>"
#: 没有键名可依的形状：PEM 私钥块、常见服务商的令牌前缀（AWS 访问密钥 id、GitHub 细粒度、Slack、Google API key）
_PEM_BEGIN = re.compile(r"-----BEGIN[ \t]+([A-Za-z0-9][A-Za-z0-9 \t]{0,60}?)[ \t]*-----", re.I)
#: 标签里含这些词的 armor 块是秘密：`PRIVATE KEY`、`ENCRYPTED PRIVATE KEY`、`OPENSSH PRIVATE KEY`、`SSH2 ENCRYPTED PRIVATE KEY`、
#: `PGP PRIVATE KEY BLOCK`、`PUTTY`…；标签里有 `PUBLIC` 的（公钥、`PGP PUBLIC KEY BLOCK`）、证书、`PGP SIGNATURE` 不是秘密，保留
_PEM_SECRET_WORDS = ("PRIVATE", "SECRET", "ENCRYPTED", "KEY BLOCK", "OPENSSH", "PUTTY")
#: PuTTY 的 `.ppk` 不是 armor：`Private-Lines: N` 之后的 N 行是私钥体
_PPK_PRIVATE = re.compile(r"^Private-Lines:[ \t]*(\d{1,6})[ \t]*\n", re.M | re.I)


def _pem_is_secret(label: str) -> bool:
    n = " ".join(label.upper().split())
    return "PUBLIC" not in n and any(w in n for w in _PEM_SECRET_WORDS)


def _redact_ppk(text: str) -> str:
    if "rivate-Lines" not in text and "RIVATE-LINES" not in text.upper():
        return text
    out: list[str] = []
    pos = 0
    for m in _PPK_PRIVATE.finditer(text):
        if m.start() < pos:
            continue
        end = m.end()
        for _ in range(int(m.group(1))):
            nl = text.find("\n", end)
            if nl < 0:
                end = len(text)
                break
            end = nl + 1
        out += [text[pos : m.end()], _REDACTED, "\n" if text[end - 1 : end] == "\n" else ""]
        pos = end
    out.append(text[pos:])
    return "".join(out)


def _redact_pem(text: str) -> str:
    """私钥 armor 块整段换掉（PEM 私钥、加密私钥、OpenSSH、SSH2、PGP 私钥块、PuTTY…，见 `_PEM_SECRET_WORDS`）。线性：每个
    BEGIN 向后找**同一标签**的 END（`BEGIN PRIVATE KEY` 只认 `END PRIVATE KEY`，大小写 / 空白变体算同一个；中间夹着别的标签的
    END——比如证书——不算收尾），换掉之后从块尾继续、不回头，所以总扫描量不超过输入长度。**找不到配对的 END（截断 / 不完整
    的块）就一直抹到输入末尾**。armor 头字段（`Version:` / `Comment:`）和 CRC 行在块内，一起抹。`.ppk` 另按 `Private-Lines: N`。"""
    text = _redact_ppk(text)
    if "-----" not in text:
        return text
    out: list[str] = []
    pos = 0
    search = 0
    while True:
        m = _PEM_BEGIN.search(text, search)
        if m is None:
            break
        if not _pem_is_secret(m.group(1)):
            search = m.end()
            continue
        words = m.group(1).upper().split()
        end_re = re.compile(
            r"-----END[ \t]+" + r"[ \t]+".join(map(re.escape, words)) + r"[ \t]*-----", re.I
        )
        e = end_re.search(text, m.end())
        stop = e.end() if e is not None else len(text)
        out.append(text[pos : m.start()])
        out.append(_REDACTED)
        pos = search = stop
    out.append(text[pos:])
    return "".join(out)


def _nl(text: str) -> str:
    """换行规范化：`\\r\\n` 和单独的 `\\r` 都变成 `\\n`。**所有按行处理的清理与分类（traceback 块、帧行、YAML 块缩进、
    「抹到行尾」、PEM、tokenize）只在入口做这一次**，不在每个正则里各补 `\\r?`：Windows 日志是 CRLF，行尾带 `\\r`
    会让帧行 / 块头匹配失败，源码就漏出去了。输出一律 `\\n`。"""
    return text.replace("\r\n", "\n").replace("\r", "\n") if "\r" in text else text


_PROVIDER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:AKIA[0-9A-Z]{16}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{10,}"
    r"|sk-[A-Za-z0-9_\-]{8,}|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,})"
)
_FIRST_TOKEN = re.compile(r"[\"']?[^\s\"',;&)\]}]*[\"']?")
#: 秘密键下**无信息**的值：true/false/null/none、空值，以及**我们自己产出的**占位记号的显式白名单——`<redacted>`、`***`、
#: `<user>`、`<email>`、`<project:十六进制>`、`seg:` / `str:` / `file:` / `mod:` / `url:` / `acct:` + 十六进制、`~`、`...` / `…`。
#: 任意 `<…>` 不算占位符（`password=<REAL_SECRET>` 是口令）。text / obj / 容器判据共用这一个正则。
_BORING_VALUE = re.compile(
    r"""^["']?(?:|true|false|none|null|<redacted>|\*\*\*|<user>|<email>|<project:[0-9a-f]{4,64}>"""
    r"""|(?:seg|str|file|mod|url|acct):[0-9a-f]{4,64}(?:\.[\w]{1,8})?|~|\.\.\.|…)["']?$""",
    re.I,
)

_CONTAINER_SEPARATORS = re.compile(r"[\s\[\]{}(),:]+")
_BORING_WORDS = frozenset({"true", "false", "null", "none"})


def _has_quote(segment: str) -> bool:
    return '"' in segment or "'" in segment


def _containers_are_boring(segment: str) -> bool:
    """括号段里只有布尔 / null / 分隔符（**数字也不行**：`password=[123456]` 是口令）：保留，如 `token=[]`、`token=(None)`。
    每个白名单字面量都要是独立的词：`password=[truefalse]`、`token=(nullnone)` 是口令。"""
    # 按分隔符切成整词再逐个比：`[truefalse]` / `(nullnone)` 是一个词，不在白名单里
    return all(w.lower() in _BORING_WORDS for w in _CONTAINER_SEPARATORS.split(segment) if w)


_CLOSER = {"(": ")", "[": "]", "{": "}"}
_STRING_PREFIX_LETTERS = frozenset("bBrRuUfF")


def _starts_string(text: str, k: int, start: int) -> bool:
    """`text[k]` 是引号：它是字符串的开头，还是散文里的撇号（`don't`）？开头 = 值的起点，或前一个字符不是字母数字下划线，
    或前面紧挨着的是字符串前缀字母（`b'x'`、`rb"x"`）且前缀之前不是字母数字。"""
    if k == start:
        return True
    prev = text[k - 1]
    if not (prev.isalnum() or prev == "_"):
        return True
    j = k
    while j > start and text[j - 1] in _STRING_PREFIX_LETTERS and k - j < 2:
        j -= 1
    if j == k:
        return False
    return j == start or not (text[j - 1].isalnum() or text[j - 1] == "_")


def _scan_value(text: str, i: int, *, whole_line: bool) -> int:
    """**唯一的值定界扫描器**（默认收集的自由文本里，秘密键后面的值到哪结束）：从 `text[i]` 起单趟线性扫描，同时跟踪
    所有开括号 `( [ {`（闭括号必须与栈顶种类匹配，不匹配 = 结构不确定）、单 / 双引号与三引号（含字符串前缀，撇号不算）、
    反斜杠转义。回结束位置（不含）。

    * `whole_line=True`（默认）：**替换单位是「从值起点到行尾」**。行尾时栈为空且没有未闭合的引号才收尾（确定在一行内结束）；
      否则继续往下一行抹，直到配平或输入末尾。栈为空时遇到闭括号 = 它关的是我们没跟踪的外层结构，忽略；栈非空时种类不匹配
      = 不确定，一直抹到输入末尾（fail-closed）。
    * `whole_line=False`（源码模式的代码区，仅用于「第一个值」）：值在配平的第一个字符串 / 括号段 / 裸词之后结束。"""
    n = len(text)
    stack: list[str] = []
    quote = ""
    k = i
    while k < n:
        ch = text[k]
        if quote:
            if ch == "\\":
                k += 2
            elif text.startswith(quote, k):
                k += len(quote)
                quote = ""
                if not stack and not whole_line:
                    return k
            else:
                k += 1
            continue
        if ch == "\n":
            if not stack and whole_line:
                return k
            k += 1
            continue
        if ch == "\\":
            k += 2
            continue
        if ch in "\"'" and _starts_string(text, k, i):
            quote = ch * 3 if text.startswith(ch * 3, k) else ch
            k += len(quote)
            continue
        if ch in _CLOSER:
            stack.append(_CLOSER[ch])
            k += 1
            continue
        if ch in ")]}":
            if not stack:
                if whole_line:
                    k += 1
                    continue
                return k
            if stack.pop() != ch:
                return n  # 种类不匹配：结构不确定，抹到输入末尾
            k += 1
            if not stack and not whole_line:
                return k
            continue
        if not whole_line and not stack and ch in " \t,;&":
            return k
        k += 1
    return n


_YAML_BLOCK_INDICATOR = re.compile(r"[|>][-+0-9]*[ \t]*(?:#.*)?")


def _ws_width(ws: str) -> int:
    return len(ws) + 7 * ws.count("\t")  # tab 按 8 列算


#: YAML 节点属性：锚点 `&name` 与标签 `!tag` / `!!str` / `!<…>`，可以叠，写在值前面。它们不是值本身，判值之前跳过
_YAML_NODE_PROPS = re.compile(r"^(?:[&!][^\s]*[ \t]*)+")


def _yaml_block_end(text: str, key_start: int, vs: int) -> int | None:
    """值从下一行才开始的写法：`token: |` / `token: >-` / `token: |2`（YAML 块标量）、`token:`（行尾空值，下面缩进的
    是值）、行尾续行符 `\\`。fail-closed：键所在行之后**所有比键行缩进更深的行**（以及续行）都是值，一直抹到遇到缩进不深于
    键行的非空行或输入末尾；空行不终止块。空值键之后与键同列的 `- ` 项（YAML 不缩进的序列）也算值。回块的结束位置（不含）；这一处不是这种写法回 None。"""
    n = len(text)
    nl = text.find("\n", vs)
    eol = n if nl < 0 else nl
    rest = text[vs:eol]
    stripped = _YAML_NODE_PROPS.sub(
        "", rest.strip()
    )  # `token: &anchor` / `token: !!str |`：属性不是值
    if stripped.startswith("#"):
        stripped = ""  # 只有行尾注释（`password:   # prod`）= 空值：值在下面缩进的行里
        rest = ""
    continued = rest.rstrip().endswith("\\")
    if not (stripped == "" or _YAML_BLOCK_INDICATOR.fullmatch(stripped) or continued):
        return None
    ls = text.rfind("\n", 0, key_start) + 1
    line = text[ls:key_start]
    kws = line[: len(line) - len(line.lstrip(" \t"))]
    indent = _ws_width(kws)
    last = None  # 最后一行被纳入的非空行的末尾
    j = eol
    while j < n:  # text[j] 是上一行末尾的换行
        k = text.find("\n", j + 1)
        line_end = n if k < 0 else k
        body = text[j + 1 : line_end]
        if body.strip() == "":
            j = line_end
            continue
        bws = body[: len(body) - len(body.lstrip(" \t"))]
        # 缩进里 tab 与空格混用（键行与这一行一边有 tab 一边没有）：宽度没法比，fail-closed 当作更深
        deeper = _ws_width(bws) > indent or (bool(bws) and ("\t" in bws) != ("\t" in kws))
        # 不缩进的序列（`token:` 下一行 `- REAL`，和键同列）：空值键之后同列的 `-` 项也是值
        item = body.lstrip(" \t")
        indentless = (
            stripped == ""
            and not continued
            and _ws_width(bws) == indent
            and item[:1] == "-"
            and item[1:2] in ("", " ", "\t", "\r")
        )
        if not (continued or deeper or indentless):
            break
        last = line_end
        continued = body.rstrip().endswith("\\")
        j = line_end
    if last is None:
        return None if stripped == "" else eol
    return last


def _redact_userinfo(text: str) -> str:
    """URL 的 userinfo：`scheme://` 之后到第一个空白 / 引号 / `<>` 之前，只要出现 `@`，就把 scheme 之后到**最后一个**
    这样的 `@` 全部换掉——不设长度窗口。线性：每个 scheme 起点只扫到下一个分隔符，换掉之后从区间末尾继续。"""
    out: list[str] = []
    pos = 0
    scanned = 0  # 已经扫过的区间末尾：落在里面的 scheme 起点共用同一个分隔符，不重扫（否则平方）
    for m in _URL_SCHEME.finditer(text):
        if m.start() < max(pos, scanned):
            continue
        j = m.end()
        n = len(text)
        while j < n and not text[j].isspace() and text[j] not in "\"'<>":
            j += 1
        scanned = j
        at = text.rfind("@", m.end(), j)
        if at < 0:
            continue
        out.append(text[pos : m.end()])
        out.append(_REDACTED + "@")
        pos = at + 1
    out.append(text[pos:])
    return "".join(out)


#: 结构化数据里这些键名（子串）的值整体换掉。`diagnostics._SECRET_KEYS` 的超集
#: 结构化数据里的秘密键名判据：键名**去掉所有非字母数字、转小写**之后包含这些词（`API-KEY`、`apiKey`、`x-api-key`、
#: `Session_Token` 都归一成同一个形状）。与文本侧 `_SECRET_WORDS` 是同一张词表的两种写法，`TestSecretShapeMatrix` 逐键对拍。
_SHORT_SECRET_TOKENS = frozenset({"pw", "pwd", "pass", "pin", "otp", "dsn"})
_SECRET_KEY_PARTS = (
    "mnemonic",
    "seedphrase",
    "connectionstring",
    "databaseurl",
    "密码",
    "口令",
    "密钥",
    "秘钥",
    "令牌",
    "uploadurl",
    "apikey",
    "token",
    "secret",
    "password",
    "passwd",
    "pwd",
    "passphrase",
    "passcode",
    "cookie",
    "nonce",
    "credential",
    "signature",
    "sessionid",
    "sessionkey",
    "privatekey",
    "accesskey",
    "bearer",
)
_AUTH_KEY = re.compile(
    r"auth(?!or(?!iz)|ority)"
)  # auth / authorization / authToken；不含 author / authority


#: 计数 / 配置类键名：按**词**切开（`_` `-` `.` 空白与驼峰边界）之后，**最后一个词**是计数词，或**第一个词**是
#: `max` / `min` / `num` ——`max_tokens`、`token_count`、`tokens_used`、`token_ttl`、`key_size`、`password_max_len`、
#: `otp_age`、`secret_version`。**按键名放行，不按值类型放行**：它们不当秘密键（数字与非数字值都保留）；其余秘密键下的
#: 数字一律抹（`password=123456`、`pin=1234`、`otp: 593201`）。
#: 刻意**不放行**会和秘密词组成真实凭据名的词：`number` / `num`（`pin_number`、`otp_num`）、`code`（`pin_code`）、
#: `value` / `val`（`password_value`）；按词匹配也避免 `token_admin` 因子串 `min` 被放行。
_COUNT_WORDS = frozenset(
    {
        "count", "len", "length", "size", "limit", "max", "min", "index", "idx", "ttl", "timeout",
        "expires", "expiry", "age", "version", "retries", "bits", "used",
    }
)  # fmt: skip
_COUNT_PREFIX_WORDS = frozenset({"max", "min", "num"})


def is_count_key(raw: str) -> bool:
    words = [w.lower() for w in re.split(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])", raw) if w]
    return bool(words) and (words[-1] in _COUNT_WORDS or words[0] in _COUNT_PREFIX_WORDS)


def is_secret_key(key: object) -> bool:
    raw = str(key)
    if is_count_key(raw):
        return False
    if any(
        t.lower() in _SHORT_SECRET_TOKENS
        for t in re.split(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])", raw)
    ):
        return True  # 短词只认整词（`pw` / `db_pin` / `userPin` / `DB_PASS`），`pine` / `pinyin` / `compass` 不算
    n = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", raw.lower())
    return n == "key" or bool(_AUTH_KEY.search(n)) or any(p in n for p in _SECRET_KEY_PARTS)


def _norm_variants(raw: str) -> set[str]:
    """一个路径的各种写法：原样 / 正反斜杠 / JSON 转义的双反斜杠。"""
    out = {raw}
    out.add(raw.replace("\\", "/"))
    out.add(raw.replace("/", "\\"))
    out.add(raw.replace("\\", "\\\\"))
    return {v for v in out if len(v) >= 3}


class Scrubber:
    """发送包所有新文件都过它。构造时取一次家目录 / 用户名，之后纯字符串处理。"""

    def __init__(self, *, source_mode: bool = False) -> None:
        self.source_mode = source_mode
        homes: set[str] = set()
        candidates = []
        try:
            candidates.append(os.path.expanduser("~"))
            candidates.append(str(Path.home()))
        except (RuntimeError, OSError, KeyError):
            pass
        for env in ("HOME", "USERPROFILE"):
            candidates.append(os.environ.get(env, ""))
        for home in list(candidates):
            if not home or home in ("/", "\\"):
                continue
            homes |= _norm_variants(home)
            try:
                homes |= _norm_variants(os.path.realpath(home))
            except OSError:
                pass
        flags = re.IGNORECASE if os.name == "nt" else 0
        ordered = sorted(homes, key=len, reverse=True)
        self._home = re.compile("|".join(re.escape(h) for h in ordered), flags) if ordered else None
        names: set[str] = set()
        try:
            import getpass

            names.add(getpass.getuser())
        except Exception:  # noqa: BLE001 — 取不到用户名就不换，脱敏不该被它拖垮
            pass
        for env in ("USER", "USERNAME", "LOGNAME"):
            names.add(os.environ.get(env, ""))
        try:
            names.add(Path.home().name)
        except (RuntimeError, OSError, KeyError):
            pass
        names = {n for n in names if len(n) >= 3 and n.lower() not in _GENERIC_USERNAMES}
        self._user = (
            re.compile(
                r"(?<![\w])(?:"
                + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                + r")(?![\w])",
                flags,
            )
            if names
            else None
        )
        self._install_id = diagnostics._install_id()

    # ---- 文本 ----
    def json_or_text(self, text: str) -> str:
        """整份文本若是合法 JSON：走结构化 `obj()` 再序列化（结构化路径没有引号边界问题）；解析失败（截断的、
        损坏的、不是 JSON 的）才退回文本正则。"""
        try:
            parsed = json.loads(text)
        except ValueError:
            return self.text(_nl(text))
        if not isinstance(parsed, (dict, list)):
            return self.text(text)
        return json.dumps(self.obj(parsed), ensure_ascii=False, indent=1)

    def _kv_all(self, text: str) -> str:
        """`_KV_SECRET`（秘密键 + 分隔符）命中之后的值替换。默认收集的自由文本一律 **fail-closed**：替换单位是
        「从值起点到行尾」，行尾时括号 / 引号还没配平就继续抹（`_scan_value`）；确定无信息的值（短数字、布尔、null、
        `<redacted>`、全是数字的括号段）原样保留。源码模式（代码区）只换带引号的字面量 / Bearer 形状 / 带引号的括号段。"""
        out: list[str] = []
        pos = 0
        for m in _KV_SECRET.finditer(text):
            if m.start() < pos:
                continue  # 落在前一个已换掉的区间里
            vs = m.end()
            if vs >= len(text):
                continue
            if not is_secret_key(m.group("key")):
                continue  # 不是秘密键（含计数 / 配置类键名：`max_tokens`、`token_count`）
            if not self.source_mode:
                yaml_end = _yaml_block_end(text, m.start(), vs)
                if yaml_end is not None:
                    out.append(text[pos:vs])
                    out.append(_REDACTED)
                    pos = max(yaml_end, _scan_value(text, vs, whole_line=True))
                    continue
            fs = vs
            if text[vs] in "&!" and any(c in m.group("sep") for c in ":："):
                props = _YAML_NODE_PROPS.match(
                    text, vs
                )  # `password: &shared REAL` / `token: !!str REAL`
                fs = props.end() if props else vs
                if fs < len(text) and text[fs] not in "\r\n#":
                    # 属性后面还有值：整段（属性 + 值）都当值处理，不再走「无信息值」豁免
                    out.append(text[pos:vs])
                    out.append(_REDACTED)
                    pos = _scan_value(text, fs, whole_line=True)
                    continue
            ft = _FIRST_TOKEN.match(text, fs)
            first, after = ft.group(0), ft.end()
            if _BORING_VALUE.match(first) and (
                after >= len(text) or text[after] in " \t\r\n,;&)]}"
            ):
                continue  # 无信息的**整个**值（`max_tokens: 4096`、`token=None`、已脱敏的 `<redacted>`）
            if self.source_mode:
                end = self._source_value_end(text, vs)
                if end is None:
                    continue
            else:
                if text[vs] in _CLOSER:
                    e1 = _scan_value(text, vs, whole_line=False)
                    if _containers_are_boring(text[vs:e1]):
                        continue  # `token=[1,2]` / `token=()`：第一个括号段里只有数字 / 布尔 / null
                end = _scan_value(text, vs, whole_line=True)
            out.append(text[pos:vs])
            out.append(_REDACTED)
            pos = end
        out.append(text[pos:])
        return "".join(out)

    @staticmethod
    def _source_value_end(text: str, vs: int) -> int | None:
        """源码模式：值只有三种算明文——带引号的字面量（≥ 8 字符）、`Bearer xxx`、带引号的括号段；其余（表达式）原样。"""
        b = re.match(r"(?i)(?:Bearer|Basic|Token)[ \t]+[^\s\"',;&)\]}]+", text[vs : vs + 4096])
        if b:
            return vs + b.end()
        end = _scan_value(text, vs, whole_line=False)
        literal = text[vs:end]
        quoted = bool(re.match(r"[bBrRuUfF]{0,2}[\"']", literal))
        if quoted and len(literal) >= 8:
            return end
        if literal[:1] in _CLOSER and ('"' in literal or "'" in literal):
            return end
        return None

    def text(self, text: str) -> str:
        text = self._kv_all(_nl(text))
        text = _BEARER.sub(lambda m: f"{m.group(1)} {_REDACTED}", text)
        text = _redact_userinfo(text)
        text = _JWT.sub(_REDACTED, text)
        text = _redact_pem(text)
        text = _PROVIDER_TOKEN.sub(_REDACTED, text)
        text = diagnostics._SECRET_VALUE.sub("***", text)
        if self._install_id:
            text = text.replace(self._install_id, "***")
        if self._home is not None:
            text = self._home.sub("~", text)
        if self._user is not None:
            text = self._user.sub("<user>", text)
        return text

    def _key(self, k, taken: dict):
        """字典的**键**也是写出去的文本：`{"https://alice:REAL@proxy": true}`、`{"api_key=REAL": true}`。字符串键过 `text()`
        （userinfo / KV / PEM / 令牌前缀 / 家目录 / 用户名）；清理后和已有键撞了就追加 `#2`、`#3`…，不丢条目。"""
        if not isinstance(k, str):
            return k
        ck = self.text(k)
        if ck not in taken:
            return ck
        n = 2
        while f"{ck}#{n}" in taken:
            n += 1
        return f"{ck}#{n}"

    def obj(self, obj):
        """JSON 可序列化的结构：秘密键名下的值**不论类型**整体换掉（str / 数 / dict / list / 嵌套容器；
        只有全是数字·布尔·null 的容器按文本侧同一判据保留），其余字符串过 `text`。"""
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if is_secret_key(k):  # 分类用**原始键**
                    if isinstance(v, (dict, list)):
                        nv = v if _containers_are_boring(json.dumps(v, default=str)) else _REDACTED
                    elif v is None or isinstance(v, bool) or v == "":
                        nv = v
                    elif isinstance(v, str) and _BORING_VALUE.match(v):
                        nv = v  # 已有占位符 / true / null / `...`
                    else:
                        nv = _REDACTED  # 数字也抹
                else:
                    nv = self.obj(v)
                out[self._key(k, out)] = nv
            return out
        if isinstance(obj, list):
            return [self.obj(v) for v in obj]
        if isinstance(obj, str):
            return self.text(obj)
        return obj


# ---------------------------------------------------------------------------
# 没勾「脚本源码」时剥掉 traceback 里的源码行
# ---------------------------------------------------------------------------
_FRAME = re.compile(r'^\s*File "[^"]*", line \d+(?:, in .*)?$')
_WARNING_HEAD = re.compile(r"^\S[^\n]*:\d+: \w*Warning\b")
_TB_HEADER = re.compile(r"^(?:Exception Group )?Traceback \(most recent call last\):$")
_ELLIPSIS = re.compile(r"^(?:\.\.\.|\[Previous line repeated \d+ more times?\])$")
#: Python 3.11+ ExceptionGroup 的装饰前缀：任意层 `|`（后跟至多一个空格）、`+ ` 头、`+-+---- 1 ----` 分隔行
_DECO = re.compile(r"^(?:[ ]*(?:\|[ ]?|\+[ ]))+")
_GROUP_SEP = re.compile(r"^[ ]*(?:\|[ ]*)*\+[-+]+[ ]*(?:\d+[ ]*-*)?[-+ ]*$")


#: ANSI 控制序列（CSI / OSC / 两字符转义）：3.13+ 的 `PYTHON_COLORS=1` 会给 traceback 着色，
#: `File \x1b[35m"…"` 这样的帧行不去色就认不出
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


def strip_source_lines(text: str, *, uncertain_start: bool = False) -> str:
    """去掉 Python traceback / SyntaxError / warning 显示里附带的**用户代码**（源码行、`^^^` / `~~^~~` 指示符、局部变量 repr）。

    **白名单，不是黑名单**：traceback 块里（从 `Traceback (most recent call last):` / `Exception Group Traceback`、
    或任何 `File "…", line N` 帧行、或 `file.py:N: XxxWarning` 起），先剥掉 ExceptionGroup 的装饰前缀再分类——缩进的行
    只留 `File "…", line N[, in …]` 帧行与 `...` / `[Previous line repeated N more times]`，其余一律删；
    不缩进的行（块头、收尾 `Type: message`、链式连接语、`add_note` 的 note、group 分隔行）原样保留，装饰前缀原样还回去。
    块外的行是日志正文，不动。

    `uncertain_start=True`：文本是从文件中间读出来的尾巴，起点可能落在某个帧的中间——在遇到第一个可识别的边界
    （块头 / 帧行 / 不缩进的普通行）之前，缩进行一律当「块内非白名单行」删掉（fail-closed）。
    **顺序纪律**：任何截断（`_cap_middle`、头尾截取、按份额截、按字节读尾巴）都必须发生在剥离**之后**——
    截断点落在帧里、省略标记又不缩进，会让剥离器退出帧模式，后缀里的源码就漏出来了。
    """
    text = _nl(text)
    out: list[str] = []
    in_frames = uncertain_start  # True：正在读帧——缩进的行不是帧就是用户代码
    for line in text.split("\n"):
        line = _ANSI.sub("", line)  # 先去色再分类；输出也用去色后的行
        if _GROUP_SEP.match(line):
            out.append(line)
            continue
        m = _DECO.match(line)
        core = line[m.end() :] if m else line
        stripped = core.strip()
        if _TB_HEADER.match(stripped) or _FRAME.match(core) or _WARNING_HEAD.match(core):
            out.append(line)
            in_frames = True
            continue
        if in_frames:
            if not stripped:
                out.append(line)
                continue
            if core[:1] in (" ", "\t"):
                if _ELLIPSIS.match(stripped):
                    out.append(line)
                continue  # 源码行 / 指示符 / 局部变量：删
            in_frames = False  # 不缩进：收尾异常行（之后的 note、连接语都属于块外正文，原样）
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _dumps(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str).encode("utf-8")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", name)[:120] or "file"


#: 清理过的名字仍保留下来的常见扩展名（`token=REAL.pdf` 抹成 `token=<redacted>` 后丢了 `.pdf`，维护者还要靠它认文件类型）
_KEEP_EXTS = frozenset(
    {
        ".py",
        ".json",
        ".txt",
        ".log",
        ".pdf",
        ".png",
        ".svg",
        ".wer",
        ".dmp",
        ".mdmp",
        ".hdmp",
        ".ips",
        ".crash",
        ".zip",
    }
)


def _name_id(raw: str) -> str:
    """原名的不可逆短标识（sha1 前 10 位）：index 里的名字被清理后，维护者靠它把多份对应起来。"""
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:10]


def _clean_name(scrub: "Scrubber", raw: str) -> str:
    """名字（文件名 / 面板标识 / 脚本相对路径）的清理 = `Scrubber.text()`。令牌贴在下划线后面（WER 目录名
    `AppCrash_Tavotto.exe_ghp_…`）也要认得出，所以 `_PROVIDER_TOKEN` 的起点不用 `\\b`（`_` 是词字符）。"""
    return scrub.text(raw)


def _member_name(
    scrub: "Scrubber", raw: str, taken: set[str] | None = None, slash: str = "_"
) -> str:
    """**所有**从用户可控字符串（图 / 脚本 / 状态文件 / 崩溃报告 / 损坏副本 / WER 目录名）派生出的内层 zip 成员名都走这里：
    先过 `Scrubber.text()`（家目录、用户名、KV、令牌前缀、userinfo）**再** `_safe_name()`——顺序不能反，`_safe_name` 会把
    `token=REAL` 的 `=` 换成 `_`，之后任何正则都认不出来了。清理后撞名（含 `index.json`）追加 `-2`、`-3`…；`taken` 给了就登记。"""
    clean = _clean_name(scrub, raw).replace(
        "/", slash
    )  # 先清理、后替换分隔符：`https://u:REAL@h` 在被拆开之前才认得出
    name = _safe_name(clean)
    ext = Path(raw).suffix.lower()
    if clean != raw and ext in _KEEP_EXTS and not name.lower().endswith(ext):
        name = name[: 120 - len(ext)] + ext
    if taken is not None:
        base, dot, tail = name.rpartition(".")
        stem, suffix = (base, dot + tail) if dot and base else (name, "")
        n = 1
        while name in taken:
            n += 1
            name = f"{stem}-{n}{suffix}"
        taken.add(name)
    return name


def _marker(kept: int, original: int, how: str) -> bytes:
    return f"[truncated: kept {how} {kept} of {original} bytes]\n".encode()


def _tail_bytes(data: bytes, cap: int) -> bytes:
    """保留最后 `cap` 字节（对齐到整行），前面加一行说明。"""
    if len(data) <= cap:
        return data
    cut = data[-cap:]
    nl = cut.find(b"\n")
    if 0 <= nl < len(cut) - 1:
        cut = cut[nl + 1 :]
    return _marker(len(cut), len(data), "last") + cut


def _head_bytes(data: bytes, cap: int) -> bytes:
    if len(data) <= cap:
        return data
    cut = data[:cap]
    nl = cut.rfind(b"\n")
    if nl > 0:
        cut = cut[: nl + 1]
    return cut + b"\n" + _marker(len(cut), len(data), "first")


def _read_tail(path: Path, limit: int) -> tuple[bytes, int] | None:
    """文件最后 `limit` 字节 + 文件总大小；seek 过去读，不整读。"""
    try:
        size = path.stat().st_size
        return diagnostics._read_tail_bytes(path, limit), size
    except OSError:
        return None


def _tail_text(path: Path, limit: int) -> tuple[str, int, bool] | None:
    """日志尾巴的文本：`(文本, 文件总大小, 起点是否落在文件中间)`。从中间读时第一行（很可能是半行）整行丢掉——
    半行没法判断它是不是源码的一部分；之后由 `strip_source_lines(uncertain_start=True)` 处理起点落在帧中间的情况。"""
    got = _read_tail(path, limit)
    if got is None:
        return None
    raw, size = got
    mid = size > limit
    text = _nl(raw.decode("utf-8", errors="replace"))
    if mid:
        nl = text.find("\n")
        text = text[nl + 1 :] if nl >= 0 else ""
    return text, size, mid


def _read_head(path: Path, limit: int) -> tuple[bytes, int] | None:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            return fh.read(limit), size
    except OSError:
        return None


@dataclasses.dataclass(frozen=True)
class FileId:
    """枚举（登记候选）那一刻看到的文件身份：大小 + mtime_ns + 文件 id。之后读它必须还是这个身份，
    读取上限也只来自登记时的大小与份额——**不按「此刻文件多大就读多大」**（文件在枚举之后变大不会 OOM）。"""

    size: int
    mtime_ns: int
    ino: int

    @staticmethod
    def of(st) -> "FileId":
        return FileId(st.st_size, st.st_mtime_ns, st.st_ino)


def _file_id(path: Path) -> FileId | None:
    try:
        return FileId.of(path.stat())
    except OSError:
        return None


def _stable_candidates(paths) -> tuple[list[tuple[Path, FileId]], list[Path]]:
    """逐个 stat 登记：`([(路径, 身份)], [stat 失败的路径])`。失败只让那个文件自己出局（调用方标 read_failed），
    排序只用登记到的 mtime，不再碰文件系统。"""
    ok: list[tuple[Path, FileId]] = []
    bad: list[Path] = []
    for p in paths:
        fid = _file_id(p)
        if fid is None:
            bad.append(p)
        else:
            ok.append((p, fid))
    return ok, bad


def _read_capped(
    path: Path, reg: FileId, cap: int, *, whole: bool = False
) -> tuple[bytes | None, str]:
    """按登记的身份读：读之前的 stat 必须与 `reg` 一致，读到的字节数等于预期，读之后再 stat 仍一致。
    `whole=False`：读登记大小与 `cap` 的较小者（头部，超出的部分不读）；`whole=True`：要整份（`reg.size` ≤ `cap`
    才读），最多读 `reg.size + 1` 字节——多读到的那个字节就是「读取期间在长」的证据。
    回 `(字节, "")`，或 `(None, "read_failed" | "changed_while_reading" | "skipped_over_budget")`。"""
    if whole and reg.size > cap:
        return None, "skipped_over_budget"
    want = reg.size if whole else min(reg.size, cap)
    try:
        if FileId.of(path.stat()) != reg:
            return None, "changed_while_reading"
        with path.open("rb") as fh:
            raw = fh.read(want + 1 if whole else want)
        after = FileId.of(path.stat())
    except OSError:
        return None, "read_failed"
    if after != reg or len(raw) != want:
        return None, "changed_while_reading"
    return raw, ""


def _list_shrinker(build: Callable[[list], bytes], records: list, *, newest_last: bool = True):
    """记录列表型 JSON 的缩小器：从最旧的一端丢记录直到序列化后 ≤ cap。"""

    def shrink(cap: int) -> bytes:
        recs = list(records)
        while recs:
            data = build(recs)
            if len(data) <= cap:
                return data
            if len(recs) == 1:
                break
            recs = recs[1:] if newest_last else recs[:-1]
        return build([])

    return shrink


def _failed(kind: str, path: str, exc: BaseException, scrub: Scrubber) -> Item:
    """某一类采集失败：如实写失败原因，不让备包失败。"""
    reason = f"{type(exc).__name__}: {exc}"
    return Item(
        kind,
        path,
        _dumps({"collection_error": scrub.text(reason)[:500], "schema": DETAIL_SCHEMA}),
    )


# ---------------------------------------------------------------------------
# 1 报错原文与调用栈
# ---------------------------------------------------------------------------
_LOG_RECORD = re.compile(
    r"^(?P<time>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) (?P<level>[A-Z]+) (?P<logger>[\w.\-]+): ",
    re.M,
)


def parse_log_records(text: str) -> list[dict]:
    """`cache/app.log`（`%(asctime)s %(levelname)s %(name)s: %(message)s`）→ 记录（含多行 traceback）。"""
    heads = list(_LOG_RECORD.finditer(text))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[m.end() : end].rstrip("\n")
        out.append(
            {
                "time": m.group("time"),
                "level": m.group("level"),
                "logger": m.group("logger"),
                "text": body,
            }
        )
    return out


def traceback_blocks(text: str, limit: int = TRACEBACKS_PER_LOG) -> list[str]:
    """worker.log 里最近几段 Python traceback（原文，含异常行）。"""
    lines = text.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("Traceback (most recent call last):"):
            j = i + 1
            while j < len(lines) and (lines[j][:1] in (" ", "\t")):
                j += 1
            end = min(j + 1, len(lines))
            blocks.append("\n".join(lines[i:end]))
            i = end
        else:
            i += 1
    return blocks[-limit:]


def _clean(text: str, opts: DetailOptions, *, uncertain_start: bool = False) -> str:
    """没勾源码时剥掉 traceback 里的用户代码；勾了不动。所有截断都要排在它之后。"""
    return text if opts.source else strip_source_lines(text, uncertain_start=uncertain_start)


def _cap_middle(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    half = cap // 2
    return text[:half] + f"\n[... {len(text) - cap} chars omitted ...]\n" + text[-half:]


def _app_log_text() -> str:
    got = _tail_text(diagnostics._app_log_path(), 2 * 1024 * 1024)
    return got[0] if got else ""


def _worker_logs(project: dict | None) -> list[tuple[float, Path]]:
    return diagnostics.owned_worker_logs((project or {}).get("figures_dir"))[:WORKER_LOG_FILES]


def _worker_logs_over_cap(project: dict | None) -> list[tuple[float, Path]]:
    """超过份数上限（`WORKER_LOG_FILES`）而没收的 worker.log：要在声明面上记账。"""
    return diagnostics.owned_worker_logs((project or {}).get("figures_dir"))[WORKER_LOG_FILES:]


def collect_errors(src: Sources, opts: DetailOptions, scrub: Scrubber) -> list[Item]:
    text = _app_log_text()
    engine = [r for r in parse_log_records(text) if r["level"] in ("ERROR", "CRITICAL")][
        -ERROR_RECORDS:
    ]
    for r in engine:
        # 先剥离再截断（顺序纪律，见 `strip_source_lines`）
        r["text"] = _cap_middle(_clean(r["text"], opts), ERROR_RECORD_CHARS)
    worker = []
    for mtime, path in _worker_logs(src.project):
        got = _tail_text(path, WORKER_LOG_TAIL)
        if not got:
            continue
        blocks = traceback_blocks(got[0])
        if blocks:
            worker.append(
                {
                    "modified": _iso(mtime),
                    "tracebacks": [
                        _cap_middle(_clean(b, opts), ERROR_RECORD_CHARS) for b in blocks
                    ],
                }
            )
    fe = (src.frontend or {}).get("errors") or []

    def build(recs: list) -> bytes:
        engine_n = [r for r in recs if r["_src"] == "engine"]
        other = {
            "schema": DETAIL_SCHEMA,
            "engine": [{k: v for k, v in r.items() if k != "_src"} for r in engine_n],
            "worker": [r["w"] for r in recs if r["_src"] == "worker"],
            "frontend": [r["f"] for r in recs if r["_src"] == "frontend"],
        }
        other = scrub.obj(other)
        if not opts.source:
            other = _strip_obj(other)
        return _dumps(other)

    records = (
        [{**r, "_src": "engine"} for r in engine]
        + [{"_src": "worker", "w": w} for w in worker]
        + [{"_src": "frontend", "f": f} for f in fe]
    )
    data = build(records)
    return [
        Item(
            "errors",
            "detail-errors.json",
            data,
            shrink=_list_shrinker(build, records),
        )
    ]


def _strip_obj(obj):
    """对结构里每个字符串去掉 traceback 源码行。"""
    if isinstance(obj, dict):
        return {k: _strip_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_obj(v) for v in obj]
    if isinstance(obj, str):
        return strip_source_lines(obj)
    return obj


# ---------------------------------------------------------------------------
# 2 日志尾巴
# ---------------------------------------------------------------------------
def sidecar_log_file() -> Path | None:
    from . import handoff

    raw = handoff.sidecar_log_path()
    return Path(raw) if raw else None


def collect_logs(src: Sources, opts: DetailOptions, scrub: Scrubber) -> list[Item]:
    items: list[Item] = []

    def add(path: str, file: Path | None, limit: int) -> None:
        if file is None:
            return
        got = _tail_text(file, limit)
        if got is None:
            return
        text, size, mid = got
        text = scrub.text(_clean(text, opts, uncertain_start=mid))
        data = text.encode("utf-8")
        items.append(
            Item(
                "logs",
                path,
                data,
                shrink=lambda cap, d=data: _tail_bytes(d, cap),
                original_bytes=size if size > limit else None,
            )
        )

    add("detail-app-log.txt", diagnostics._app_log_path(), APP_LOG_TAIL)
    add("detail-sidecar-log.txt", sidecar_log_file(), SIDECAR_LOG_TAIL)
    # 当前项目最近几份 worker.log 合成一个条目（扁平名只有一个 detail-worker-log.txt），每份前面一行出处
    parts: list[str] = []
    sizes = 0
    cut = False
    for _mtime, path in _worker_logs(src.project):
        got = _tail_text(path, WORKER_LOG_TAIL)
        if got is None:
            continue
        text, size, mid = got
        sizes += size
        cut = cut or mid
        text = _clean(text, opts, uncertain_start=mid)
        parts.append(f"===== worker.log (modified {_iso(_mtime)}) =====\n{text}")
    over = _worker_logs_over_cap(src.project)
    over_bytes = sum((_file_id(p) or FileId(0, 0, 0)).size for _m, p in over)
    if over:  # 份数上限之外的更早的 worker.log：文件头里如实写，字节数计入 manifest 的 truncated
        parts.append(
            f"===== {len(over)} older worker.log file(s) omitted (over the count cap of {WORKER_LOG_FILES}) ====="
        )
    if parts:
        data = scrub.text("\n".join(parts)).encode("utf-8")
        items.append(
            Item(
                "logs",
                "detail-worker-log.txt",
                data,
                shrink=lambda cap, d=data: _tail_bytes(d, cap),
                original_bytes=sizes if cut else None,
                dropped_bytes=over_bytes,
            )
        )
    return items


# ---------------------------------------------------------------------------
# 3 会话状态文件
# ---------------------------------------------------------------------------
def state_dir() -> Path:
    return layoutsession.state_path().parent


def _state_kind(name: str) -> str:
    if name.startswith(layoutsession.CORRUPT_PREFIX):
        return "corrupt"
    if name == layoutsession.FILENAME:
        return "primary"
    if name.endswith(layoutsession.BACKUP_SUFFIX):
        return "backup"
    return "other"


def _state_files() -> list[Path]:
    try:
        return sorted(
            p
            for p in state_dir().iterdir()
            if p.is_file()
            and not p.name.endswith(".lock")
            and p.name != layoutsession.LOCK_FILENAME
        )
    except OSError:
        return []


def collect_state(src: Sources, opts: DetailOptions, scrub: Scrubber) -> list[Item]:
    index: list[dict] = []
    items: list[Item] = []
    taken: set[str] = {"index.json"}
    cands, bad = _stable_candidates(_state_files())
    index.extend(
        {
            "name": _clean_name(scrub, p.name),
            "id": _name_id(p.name),
            "kind": _state_kind(p.name),
            "read_failed": True,
        }
        for p in bad
    )
    for p, fid in cands:
        kind = _state_kind(p.name)
        entry = {
            "name": _clean_name(scrub, p.name),
            "id": _name_id(p.name),
            "kind": kind,
            "bytes": fid.size,
            "mtime": _iso(fid.mtime_ns / 1e9),
        }
        index.append(entry)
        if kind == "corrupt":
            continue  # 默认只有清单；内容归勾选项 C
        raw, why = _read_capped(p, fid, STATE_FILE_CAP)
        if raw is None:
            entry[why] = (
                True  # read_failed / changed_while_reading：不写 `file`，`_reconcile_index` 不会误标超预算
            )
            continue
        member = _member_name(scrub, p.name, taken)
        entry["file"] = member  # 读成功才写：内容在内层 zip 里的名字（`_reconcile_index` 据此核对）
        size = fid.size
        # 状态文件多半是 JSON：能解析就走结构化清理（没有引号边界问题），截断 / 损坏的才退回文本
        data = scrub.json_or_text(raw.decode("utf-8", errors="replace")).encode("utf-8")
        if size > STATE_FILE_CAP:
            data += b"\n" + _marker(len(raw), size, "first")
        items.append(
            Item(
                "state",
                member,
                data,
                shrink=lambda cap, d=data: _head_bytes(d, cap),
                original_bytes=size if size > STATE_FILE_CAP else None,
            )
        )
    listing = {
        "schema": DETAIL_SCHEMA,
        "dir": scrub.text(str(state_dir())),
        "files": index,
        "corrupt_contents_included": opts.corrupt_sessions,
    }
    items.insert(0, Item("state", "index.json", _dumps(listing)))
    return items


def collect_corrupt_sessions(src: Sources, opts: DetailOptions, scrub: Scrubber) -> list[Item]:
    items: list[Item] = []
    index: list[dict] = []
    taken: set[str] = {"index.json"}
    corrupt = [p for p in _state_files() if _state_kind(p.name) == "corrupt"]
    cands, bad = _stable_candidates(corrupt)  # 逐个 stat 缓存；排序只用登记到的 mtime
    index.extend(
        {"name": _clean_name(scrub, p.name), "id": _name_id(p.name), "read_failed": True}
        for p in bad
    )
    cands.sort(key=lambda pf: -pf[1].mtime_ns)
    over_cap = sum(fid.size for _p, fid in cands[CORRUPT_FILES:])
    for p, fid in cands[CORRUPT_FILES:]:  # 份数上限之外：index 里列出来，字节计入 truncated
        index.append(
            {
                "name": _clean_name(scrub, p.name),
                "id": _name_id(p.name),
                "bytes": fid.size,
                "skipped_over_count_cap": True,
            }
        )
    for p, fid in cands[:CORRUPT_FILES]:
        raw, why = _read_capped(p, fid, CORRUPT_FILE_CAP)
        if raw is None:
            index.append(
                {"name": _clean_name(scrub, p.name), "id": _name_id(p.name), why: True}
            )  # 读失败 / 读取期间在变：都不是超预算
            continue
        size = fid.size
        data = scrub.text(raw.decode("utf-8", errors="replace")).encode("utf-8")
        member = _member_name(scrub, p.name, taken)
        index.append(
            {
                "name": _clean_name(scrub, p.name),
                "id": _name_id(p.name),
                "file": member,
                "bytes": size,
            }
        )
        items.append(
            Item(
                "corrupt_sessions",
                member,
                data,
                shrink=lambda cap, d=data: _head_bytes(d, cap),
                original_bytes=size if size > CORRUPT_FILE_CAP else None,
            )
        )
    items.insert(
        0,
        Item(
            "corrupt_sessions",
            "index.json",
            _dumps({"schema": DETAIL_SCHEMA, "files": index}),
            dropped_bytes=over_cap,
        ),
    )
    return items


# ---------------------------------------------------------------------------
# 4 请求记录
# ---------------------------------------------------------------------------
def collect_requests(src: Sources, opts: DetailOptions, scrub: Scrubber) -> list[Item]:
    records = REQUESTS.snapshot()

    def build(recs: list) -> bytes:
        return _dumps(
            {
                "schema": DETAIL_SCHEMA,
                "capacity": REQUESTS.capacity,
                "count": len(recs),
                "note": "method / Flask route rule / status / ms / error code only; no body, query string or arguments",
                "records": recs,
            }
        )

    return [
        Item(
            "requests",
            "detail-requests.json",
            build(records),
            shrink=_list_shrinker(build, records),
        )
    ]


# ---------------------------------------------------------------------------
# 5 样式改动（overrides）
# ---------------------------------------------------------------------------
MAX_OVERRIDE_PANELS = 3


def collect_overrides(src: Sources, opts: DetailOptions, scrub: Scrubber) -> list[Item]:
    items: list[Item] = []
    panels = (src.frontend or {}).get("overrides") or []
    stems: list[str] = []
    for n, panel in enumerate(panels[:MAX_OVERRIDE_PANELS]):
        file = str(panel.get("panel") or "")
        stem = Path(file).stem if file else ""
        if stem:
            stems.append(stem)
        doc = scrub.obj({"schema": DETAIL_SCHEMA, "source": "live document (hot state)", **panel})
        items.append(
            Item(
                "overrides",
                f"live-{n + 1}-{_member_name(scrub, stem or 'panel')}.json",
                _dumps(doc),
            )
        )
    baked = src.baked or {}
    baked_taken: set[str] = set()
    for stem in dict.fromkeys(stems):
        entry = baked.get(stem)
        if entry is None:
            continue
        doc = scrub.obj(
            {
                "schema": DETAIL_SCHEMA,
                "source": "baked baseline (write-back)",
                "stem": stem,
                "entry": entry,
            }
        )
        items.append(
            Item("overrides", f"baked-{_member_name(scrub, stem, baked_taken)}.json", _dumps(doc))
        )
    omitted = max(0, len(panels) - MAX_OVERRIDE_PANELS) + int(
        (src.frontend or {}).get("overrides_omitted") or 0
    )
    items.insert(
        0,
        Item(
            "overrides",
            "index.json",
            _dumps(
                {
                    "schema": DETAIL_SCHEMA,
                    "panels": [
                        _clean_name(scrub, str(p.get("panel") or "")[:200])
                        for p in panels[:MAX_OVERRIDE_PANELS]
                    ],
                    "panel_ids": [
                        _name_id(str(p.get("panel") or "")) for p in panels[:MAX_OVERRIDE_PANELS]
                    ],
                    "skipped_over_count_cap": omitted,  # 数量上限（每次至多 3 张面板）之外没收的面板数
                    **({} if panels else {"note": "no panel was being edited or selected"}),
                }
            ),
        ),
    )
    return items


# ---------------------------------------------------------------------------
# 6 渲染环境
# ---------------------------------------------------------------------------
_PROBE_CODE = r"""
import json, sys, platform, os
out = {"python": {
    "version": sys.version.split()[0], "implementation": platform.python_implementation(),
    "executable": sys.executable, "prefix": sys.prefix, "base_prefix": sys.base_prefix,
    "in_venv": sys.prefix != sys.base_prefix, "platform": platform.platform()}}
_tmp_cache, _own_cache = os.environ.get("TAVOTTO_PROBE_FONTCACHE"), False
try:
    import matplotlib
    # 探针是只读的：font_manager 在没有字体缓存时会把 fontlist-v*.json 写进用户的 Matplotlib 缓存目录。
    # 配置事实（matplotlibrc / configdir / cachedir / rcParams）照旧取真实目录；字体缓存换到一个临时目录——
    # 已有的 fontlist 复制过去照样命中（不白重建），没有就在临时目录里建。临时目录由父进程在可信目录下给出
    # （探针超时被杀时也由父进程清掉）；没给就自己建、自己删。
    import glob, shutil, tempfile
    real_cache = matplotlib.get_cachedir()
    if not _tmp_cache:
        _tmp_cache, _own_cache = tempfile.mkdtemp(prefix="tavotto-mplcache-"), True
    for _f in glob.glob(os.path.join(real_cache, "fontlist-v*.json")):
        try:
            shutil.copy(_f, _tmp_cache)
        except OSError:
            pass
    matplotlib.get_cachedir = lambda: _tmp_cache
    from matplotlib import font_manager as fm
    rc, rcd = matplotlib.rcParams, matplotlib.rcParamsDefault
    get = getattr(rc, "_get", None) or (lambda k: dict.__getitem__(rc, k))
    try:
        backend = str(get("backend"))
    except Exception:
        backend = "unknown"
    nondefault = {}
    for k in sorted(rcd.keys()):
        if k == "backend":
            continue
        try:
            v, d = get(k), rcd[k]
            if v != d:
                nondefault[k] = repr(v)[:200]
        except Exception:
            continue
        if len(nondefault) >= 300:
            break
    names = set()
    for e in fm.fontManager.ttflist:
        names.add(e.name.lower())
    generic = {"serif", "sans-serif", "sans serif", "cursive", "fantasy", "monospace", "sans"}
    wanted = []
    fam = rc["font.family"]
    wanted += [("font.family", f) for f in (fam if isinstance(fam, (list, tuple)) else [fam])]
    for key in ("font.sans-serif", "font.serif", "font.monospace", "font.cursive", "font.fantasy"):
        wanted += [(key, f) for f in list(rc[key])[:12]]
    fonts = []
    seen = set()
    for key, f in wanted:
        if (key, f) in seen:
            continue
        seen.add((key, f))
        if str(f).lower() in generic:
            try:
                resolved = fm.findfont(fm.FontProperties(family=f), fallback_to_default=True)
                fonts.append({"list": key, "family": f, "generic": True, "resolves_to": os.path.basename(resolved)})
            except Exception as exc:
                fonts.append({"list": key, "family": f, "generic": True, "error": type(exc).__name__})
        else:
            fonts.append({"list": key, "family": f, "present": str(f).lower() in names})
    out["matplotlib"] = {
        "version": matplotlib.__version__, "backend": backend,
        "matplotlibrc": matplotlib.matplotlib_fname(), "configdir": matplotlib.get_configdir(),
        "cachedir": real_cache, "non_default_rcparams": nondefault,
        "font_count": len(fm.fontManager.ttflist), "font_family_count": len(names), "fonts": fonts,
        "font_note": "system fonts known to matplotlib in this probe; fonts registered by the Tavotto worker at render time are not loaded here"}
except Exception as exc:
    out["matplotlib"] = {"error": type(exc).__name__ + ": " + str(exc)[:300]}
if _own_cache:
    import shutil
    shutil.rmtree(_tmp_cache, ignore_errors=True)
try:
    from importlib import metadata
    pk = sorted({(d.metadata["Name"] or "").lower(): d.version for d in metadata.distributions() if d.metadata["Name"]}.items())
    out["packages"] = [{"name": n, "version": v} for n, v in pk[:400]]
    out["package_count"] = len(pk)
except Exception as exc:
    out["packages_error"] = type(exc).__name__
sys.stdout.write(json.dumps(out))
"""


PROBE_DIR_PREFIX = "tavotto-probe-script-"
PROBE_LEFTOVER_AGE_S = 3600
PROBE_RMTREE_ATTEMPTS = 3


def _rmtree_with_retry(
    path: str | Path, attempts: int = PROBE_RMTREE_ATTEMPTS, delay: float = 0.05
) -> bool:
    """有界重试地删目录树（Windows 上杀软 / 索引器短暂占着文件，一次 `rmtree` 常常失败）。仓库里没有现成的目录树重试删除
    （`figcapture._replace_with_retry` 是文件替换），所以这里是唯一一份。成功回 True；一直失败回 False。"""
    import shutil

    for i in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if i + 1 < attempts:
                time.sleep(delay * (i + 1))
    return not Path(path).exists()


def _sweep_probe_leftovers(now: float | None = None) -> int:
    """清掉**我们自己**上次没删掉的探针脚本目录：前缀 `tavotto-probe-script-`、是目录、属于当前用户、权限 0700、
    超过 1 小时。别的任何东西不碰。回清掉几个。"""
    import tempfile

    now = time.time() if now is None else now
    root = Path(tempfile.gettempdir())
    removed = 0
    try:
        entries = [p for p in root.iterdir() if p.name.startswith(PROBE_DIR_PREFIX)]
    except OSError:
        return 0
    for p in entries:
        try:
            st = p.stat()
            if not p.is_dir() or p.is_symlink() or now - st.st_mtime < PROBE_LEFTOVER_AGE_S:
                continue
            if hasattr(os, "getuid") and (st.st_uid != os.getuid() or st.st_mode & 0o077):
                continue
        except OSError:
            continue
        if _rmtree_with_retry(p):
            removed += 1
    return removed


def _probe_subprocess(
    python: str, bundled: bool, cwd: str | None = None, env_extra: dict[str, str] | None = None
) -> dict:
    """对渲染解释器跑**一次**有超时的只读采集；任何失败都如实回 `{"status": "failed", "reason": …}`。
    探针脚本放进我们自己建的临时目录（见 `_probe_in`），用完有界重试地删；删不掉就在结果里记 `probe_cleanup: "failed"`
    并写一条日志，下次再探之前由 `_sweep_probe_leftovers` 清遗留。"""
    import tempfile

    _sweep_probe_leftovers()
    trusted = tempfile.mkdtemp(prefix=PROBE_DIR_PREFIX)
    try:
        result = _probe_in(trusted, python, bundled, cwd, env_extra)
    finally:
        cleaned = _rmtree_with_retry(trusted)
    if not cleaned:
        LOG.warning("探针临时目录没能删掉")
        result["probe_cleanup"] = "failed"
    return result


#: 探针继承环境时**保留**的 `PYTHON*`：只影响编码 / 字节码，不影响 import 与启动
_PROBE_KEEP_PYTHON_ENV = frozenset(
    {"PYTHONIOENCODING", "PYTHONUTF8", "PYTHONSAFEPATH", "PYTHONDONTWRITEBYTECODE"}
)


def _probe_base_env(base: dict[str, str] | None) -> dict[str, str]:
    """探针子进程的基础环境：从调用方（Flask 进程 / 用户 shell）继承，但**剔除所有能改 import 与启动行为的变量**——
    `PYTHONPATH` / `PYTHONHOME` / `PYTHONSTARTUP` / `PYTHONUSERBASE` / `PYTHON*` 其余一律 / `__PYVENV_LAUNCHER__`。
    探针脚本在可信临时目录里，不剔除的话 Flask 继承来的 `PYTHONPATH` 会让探针 import 到项目里的 `json.py`。
    解释器另加 `-E` 兜底（连将来新增的 `PYTHON*` 变量一起忽略）；`VIRTUAL_ENV` 等无害变量照旧继承。"""
    src = os.environ if base is None else base
    return {
        k: v
        for k, v in src.items()
        if k != "__PYVENV_LAUNCHER__"
        and (not k.upper().startswith("PYTHON") or k in _PROBE_KEEP_PYTHON_ENV)
    }


def _probe_in(
    trusted: str, python: str, bundled: bool, cwd: str | None, env_extra: dict[str, str] | None
) -> dict:
    env = _probe_base_env(runtime.probe_env(python, bundled=bundled))
    env.update(env_extra or {})
    # 3.11+：sys.path 里不放脚本目录 / cwd（PYTHONSAFEPATH）；老版本靠「脚本在可信临时目录里」达到同一效果
    env["PYTHONSAFEPATH"] = "1"
    # **探针不能用 `-c`，也不能让 cwd 进 sys.path**：cwd 是项目 / 脚本目录（matplotlibrc 等相对查找需要它），项目里的
    # `json.py` / `platform.py` / `matplotlib.py` 会在「打开对话框」这一刻就被 import 执行，绕过 worker 的文件系统守卫。
    # 所以把探针代码写进我们自己建的临时目录（0700）里的脚本文件，`python -s -B <脚本>` 启动：
    # `sys.path[0]` 是那个临时目录，不是 cwd；`-s` 不加用户 site。不用 `-I`（会丢掉我们刻意复现的 MPL* 变量）。
    probe_main = Path(trusted) / "probe_main.py"
    probe_main.write_text(_PROBE_CODE, encoding="utf-8")
    fontcache = (
        Path(trusted) / "mplcache"
    )  # 探针的字体缓存落点（见 `_PROBE_CODE`），随 trusted 一起删
    fontcache.mkdir(exist_ok=True)
    env["TAVOTTO_PROBE_FONTCACHE"] = str(fontcache)
    try:
        proc = subprocess.run(
            [python, "-E", *runtime.probe_args(bundled=bundled), "-s", str(probe_main)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=runtime.CREATE_NO_WINDOW,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return {"status": "failed", "reason": "timeout", "timeout_s": PROBE_TIMEOUT_S}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "failed", "reason": f"spawn_error:{type(exc).__name__}"}
    if proc.returncode != 0:
        return {
            "status": "failed",
            "reason": f"exit:{proc.returncode}",
            "stderr_tail": (proc.stderr or "")[-2000:],
        }
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return {
            "status": "failed",
            "reason": "bad_output",
            "stdout_tail": (proc.stdout or "")[-500:],
        }
    return (
        {"status": "ok", **data}
        if isinstance(data, dict)
        else {"status": "failed", "reason": "bad_output"}
    )


#: 会话启动环境里与渲染相关、可以安全复现给探针的变量（只取这几个，其余一律不碰）
RENDER_ENV_KEYS = frozenset(
    {"MPLCONFIGDIR", "MPLBACKEND", "MATPLOTLIBRC", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"}
)
#: **不复现**的：能把项目目录放进导入路径、让探针去 import / 执行用户目录里的模块（只记键名，不记值）
NOT_APPLIED_ENV_KEYS = frozenset(
    {"PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONNOUSERSITE", "PYTHONSTARTUP"}
)
#: 探针结果里依赖启动环境变量的维度：启动环境不明时整个标 `unavailable`
_ENV_DEPENDENT = (
    "matplotlibrc",
    "configdir",
    "cachedir",
    "backend",
    "non_default_rcparams",
    "font_count",
    "font_family_count",
    "fonts",
    "font_note",
)


def _native_launch_env(session: object) -> dict[str, str] | None:
    """native 会话的启动环境里能安全复现的渲染相关变量；**环境不明回 None**。桌面拥有的会话登记了启动规格
    （`spec.env` 是叠在 sidecar 自己环境上的增量，`env_source == sidecar_inherited`——探针本来就继承 sidecar 的环境），
    只取 `RENDER_ENV_KEYS`；终端拥有的会话（`tavotto run`）原样继承调用者的环境，我们不知道——不读进程环境之类。"""
    spec = getattr(session, "spec", None)
    if spec is None or not getattr(spec, "env_source", ""):
        return None
    env = getattr(spec, "env", None) or {}
    return {k: v for k, v in env.items() if k in RENDER_ENV_KEYS}


def _mark_unavailable(probe: dict, reason: str) -> dict:
    """把依赖「启动环境 / 工作目录」的维度整个标 `unavailable`（只保留与之无关的版本、包列表这类事实）。"""
    mpl = probe.get("matplotlib")
    if isinstance(mpl, dict):
        for k in _ENV_DEPENDENT:
            if k in mpl:
                mpl[k] = {"unavailable": reason}
    return probe


#: 探针里不论用户装没装都要看有没有被遮挡的模块（探针自己和 matplotlib 的导入链会用到）
_SHADOW_ALWAYS = frozenset(
    {"matplotlib", "numpy", "json", "platform", "importlib", "sitecustomize", "usercustomize"}
)
_SHADOW_SCAN_LIMIT = 5000
_SHADOW_SUFFIXES = (".py", ".pyc", ".pyd", ".so")


def shadowing_candidates(dirs: list[str], names: set[str]) -> list[str]:
    """项目目录里有没有会遮挡这些模块的同名文件或包（`matplotlib.py` / `matplotlib/` / `numpy.py`……）。
    **只看文件名，不 import、不读内容、不执行**；只扫每个目录的第一层，至多 `_SHADOW_SCAN_LIMIT` 项。"""
    found: list[str] = []
    for d in dirs:
        try:
            with os.scandir(d) as it:
                for n, entry in enumerate(it):
                    if n >= _SHADOW_SCAN_LIMIT:
                        break
                    stem = entry.name.split(".", 1)[0]
                    if stem not in names:
                        continue
                    try:
                        is_dir = (
                            entry.is_dir()
                        )  # 目录符号链接（`numpy -> vendor/numpy`）也算包目录；只 stat，不读不导入
                    except OSError:
                        is_dir = False
                    if is_dir:
                        label = entry.name + "/"
                    elif entry.name.endswith(_SHADOW_SUFFIXES):
                        label = entry.name
                    else:
                        continue
                    if label not in found:
                        found.append(label)
        except OSError:
            continue
    return sorted(found)[:50]


def _annotate_isolated_probe(doc: dict, figures_dir: object) -> None:
    """隔离探针用 `-E` 在项目外运行，看不到 safe worker 实际会优先导入的项目目录 / `PYTHONPATH`：
    依赖导入结果的事实（matplotlib 版本、包列表、backend、rcParams）可能与渲染时不同——如实标出来，
    并列出项目目录里可能遮挡它们的同名文件（只看文件名）。隔离本身不变。"""
    probe = doc.get("probe")
    if not isinstance(probe, dict) or probe.get("status") != "ok":
        return
    probe["may_differ"] = "user_import_paths_not_reproduced"
    dirs: list[str] = []
    cwd = (doc.get("probe_cwd") or {}).get("cwd")
    for d in (figures_dir, cwd):
        if isinstance(d, str) and os.path.isdir(d) and d not in dirs:
            dirs.append(d)
    names = set(_SHADOW_ALWAYS)
    for pk in probe.get("packages") or []:
        if isinstance(pk, dict) and isinstance(pk.get("name"), str):
            names.add(pk["name"].lower().replace("-", "_"))
    probe["shadowing_candidates"] = shadowing_candidates(dirs, names)


def _probe_in_render_cwd(src: Sources, python: str, bundled: bool, doc: dict) -> dict:
    """在**渲染这个项目时实际用的工作目录**里跑探针（cwd 决定 matplotlibrc / 相对路径怎么找）。模式取
    `workdir.mode_for`（safe worker spawn 的同一个出处）：项目根 → 项目根；脚本目录 → 焦点面板脚本所在目录（没有就项目根）；
    沙盒 → 一个空的临时目录（与会话沙盒同义）。取不到如实写 unknown，探针照旧在引擎进程的 cwd 里跑。"""
    import tempfile

    from . import workdir

    figures_dir = (src.project or {}).get("figures_dir")
    try:
        mode = workdir.mode_for(figures_dir) if figures_dir else "unknown"
    except Exception:  # noqa: BLE001
        mode = "unknown"
    if mode == "project_root" and figures_dir:
        doc["probe_cwd"] = {"mode": mode, "cwd": str(figures_dir)}
        return _probe_subprocess(python, bundled, str(figures_dir))
    if mode == "project" and figures_dir:
        script = next(
            (
                p.get("script")
                for p in (src.frontend or {}).get("overrides") or []
                if p.get("script")
            ),
            None,
        )
        cwd = Path(figures_dir)
        if script and (Path(figures_dir) / script).parent.is_dir():
            cwd = (Path(figures_dir) / script).parent
        doc["probe_cwd"] = {"mode": mode, "cwd": str(cwd)}
        return _probe_subprocess(python, bundled, str(cwd))
    if mode == "sandbox":
        with tempfile.TemporaryDirectory(prefix="tavotto-probe-") as tmp:
            doc["probe_cwd"] = {"mode": mode, "cwd": "<empty temporary directory>"}
            return _probe_subprocess(python, bundled, tmp)
    doc["probe_cwd"] = {"mode": "unknown", "cwd": None}
    return _probe_subprocess(python, bundled)


def _native_session_for(src: Sources, panel: dict) -> tuple[str, object | None]:
    """焦点面板这一刻由谁渲染：`(execution_profile, 活着的 native 会话或 None)`。判据与渲染路由同一个出处
    （`enginesession.profile_of` + `nativesession.REGISTRY.route_for`），只读已有登记，不起会话、不执行任何东西。"""
    from . import enginesession, nativesession, runtimeasset

    figures_dir = (src.project or {}).get("figures_dir")
    fid = panel.get("panel")
    if not figures_dir or not runtimeasset.is_runtime_id(fid):
        return enginesession.PROFILE_SAFE, None  # 磁盘面板永远是 safe
    profile = enginesession.profile_of(figures_dir, fid)
    if profile != enginesession.PROFILE_NATIVE:
        return profile, None
    info = runtimeasset.resolve(fid, src.registry) if src.registry is not None else None
    if not info:
        return profile, None
    return profile, nativesession.REGISTRY.route_for(str(figures_dir), info["script"], info["stem"])


def collect_render_env(src: Sources, opts: DetailOptions, scrub: Scrubber) -> list[Item]:
    from .. import __version__

    doc: dict = {"schema": DETAIL_SCHEMA, "tavotto_version": __version__}
    figures_dir = (src.project or {}).get("figures_dir")
    panels = (src.frontend or {}).get("overrides") or []
    profile, session = "safe", None
    try:
        if panels:
            profile, session = _native_session_for(src, panels[0])
    except Exception as exc:  # noqa: BLE001 — 取不到焦点面板的路由就按 safe 池如实标注
        doc["execution_profile_error"] = f"{type(exc).__name__}"
    doc["execution_profile"] = profile
    interpreter = getattr(session, "interpreter", "") if session is not None else ""
    if session is not None and interpreter:
        # native 面板实际在用户自己的解释器里跑：优先报那条会话已有的事实（回执 + 脱敏诊断），再对**那个**解释器
        # 跑同一个只读探针（与安全池探针同一份代码）
        doc["inspected"] = "native_session"
        doc["interpreter"] = {
            "found": True,
            "path": interpreter,
            "source": "native_session",
            "scope": "project",
            "fingerprint": getattr(session, "interpreter_fingerprint", None),
            "python_version": getattr(session, "python_version", None),
        }
        doc["native_receipt"] = session.last_build_runtime()
        doc["native_session"] = session.diagnostics()
        native_cwd = getattr(session, "cwd", "") or None
        cwd_ok = (
            bool(native_cwd)
            and os.path.isdir(native_cwd)
            and os.access(native_cwd, os.R_OK | os.X_OK)
        )
        launch_env = _native_launch_env(session)
        spec_env = getattr(getattr(session, "spec", None), "env", None) or {}
        not_applied = sorted(k for k in spec_env if k in NOT_APPLIED_ENV_KEYS)
        doc["probe_env"] = (
            {"source": "launch_spec", "keys": sorted(launch_env), "not_applied": not_applied}
            if launch_env is not None
            else {
                "source": "unknown",
                "note": "terminal-owned session: launch environment not recorded",
            }
        )
        if cwd_ok:
            doc["probe_cwd"] = {"mode": "native_session", "cwd": native_cwd}
            probe = _probe_subprocess(interpreter, False, native_cwd, launch_env or None)
        else:
            # 会话的工作目录不存在 / 读不了：不能在别处探这些维度还说成是会话目录。版本类事实在一个中性空目录里取，
            # `probe_cwd` 如实写 null 和原因；依赖 cwd 的维度（matplotlibrc / backend / rcparams / fonts…）标 unavailable
            import tempfile

            with tempfile.TemporaryDirectory(prefix="tavotto-probe-") as neutral:
                doc["probe_cwd"] = {
                    "mode": "native_session",
                    "cwd": None,
                    "reason": "native_cwd_unavailable",
                    "version_probe_cwd": "<empty temporary directory>",
                }
                probe = _probe_subprocess(interpreter, False, neutral, launch_env or None)
            probe = _mark_unavailable(probe, "native_cwd_unavailable")
        if launch_env is None and cwd_ok:
            probe = _mark_unavailable(probe, "launch_env_unknown")
        doc["probe"] = probe
        if not_applied:  # 导入路径没复现：包列表可能与会话里实际能 import 的不同
            doc["probe"]["may_differ"] = {"packages": "pythonpath_not_applied"}
        if probe.get("probe_cleanup"):
            doc["probe_cleanup"] = probe["probe_cleanup"]
        return [Item("render_env", "detail-render-env.json", _dumps(scrub.obj(doc)))]
    doc["inspected"] = "safe_pool"
    if profile == "native":  # native 面板但拿不到它的会话 / 解释器：如实说，下面探的是安全池
        doc["native_interpreter"] = "unknown"
    # 探的必须是**渲染这个项目时实际用的**解释器：项目采用了自己的 venv / 记住的解释器时，全局选择链
    # （`find_worker_python`）探的是另一个对象。`resolve_worker_python` 是项目级决策的唯一出处，`discover=False`
    # 不起发现子进程（读「此刻的决策」）；没打开项目时它退化成全局链。
    try:
        python, source = pool.resolve_worker_python(figures_dir, discover=False)
    except pool.WorkerError as exc:
        doc["interpreter"] = {
            "found": False,
            "error": str(exc),
            "scope": "project" if figures_dir else "global",
        }
        python, source = None, None
    if python:
        doc["interpreter"] = {
            "found": True,
            "path": python,
            "source": source,  # 项目 venv / 记住的 / 全局显式 / 内置 / 当前进程 …（pool.SOURCE_LABELS）
            "scope": "project" if figures_dir else "global",
            "env_override": pool.worker_python_env(),
        }
        doc["probe"] = _probe_in_render_cwd(src, python, source == pool.SOURCE_BUNDLED, doc)
        _annotate_isolated_probe(doc, figures_dir)
        if doc["probe"].get("probe_cleanup"):
            doc["probe_cleanup"] = doc["probe"]["probe_cleanup"]
    return [Item("render_env", "detail-render-env.json", _dumps(scrub.obj(doc)))]


# ---------------------------------------------------------------------------
# 7 运行记录
# ---------------------------------------------------------------------------
_RUN_LOG = re.compile(r"渲染|导出|试运行|准备|运行|脚本|export|render|prepar|worker|probe", re.I)
RUN_LOG_EVENTS = 80


def collect_runs(src: Sources, opts: DetailOptions, scrub: Scrubber) -> list[Item]:
    records = [
        r
        for r in parse_log_records(_app_log_text())
        if _RUN_LOG.search(r["text"].split("\n", 1)[0])
    ][-RUN_LOG_EVENTS:]
    for r in records:
        r["text"] = _cap_middle(_clean(r["text"], opts), 4096)
    snapshots = src.task_section

    def build(recs: list) -> bytes:
        doc = {
            "schema": DETAIL_SCHEMA,
            "note": "task snapshots are the frozen terminal projections (same as task-diagnostics.json); "
            "log_events are raw engine log records about renders / exports / script runs",
            "task_snapshots": snapshots,
            "log_events": recs,
        }
        doc = scrub.obj(doc)
        return _dumps(doc if opts.source else _strip_obj(doc))

    return [Item("runs", "detail-runs.json", build(records), shrink=_list_shrinker(build, records))]


# ---------------------------------------------------------------------------
# 8 桌面壳与安装信息
# ---------------------------------------------------------------------------
def _channel_of(version: str) -> str:
    return "beta" if re.search(r"(?:a|b|rc|dev)\d*$", version or "") else "stable"


def _windows_info() -> dict:
    if os.name != "nt":
        return {}
    try:
        ver = platform.win32_ver()
        return {
            "release": ver[0],
            "version": ver[1],
            "csd": ver[2],
            "edition": platform.win32_edition(),
        }
    except Exception:  # noqa: BLE001
        return {}


def collect_shell(src: Sources, opts: DetailOptions, scrub: Scrubber) -> list[Item]:
    from .. import __version__

    frozen = pool.is_frozen()
    exe = sys.executable or ""
    bundle = None
    if frozen:
        bundle = (
            "macos-app-bundle" if ".app/Contents/MacOS" in exe.replace("\\", "/") else "windows-exe"
        )
    try:
        facts = perfprobe.system_facts()
    except Exception:  # noqa: BLE001 — 机器事实取不到不拖垮备包
        facts = {}
    rosetta = facts.get("rosetta") if sys.platform == "darwin" else None
    fe = (src.frontend or {}).get("shell") or {}
    update = (fe.get("update") if isinstance(fe, dict) else None) or None
    doc = {
        "schema": DETAIL_SCHEMA,
        "app": {
            "tavotto_version": __version__,
            "install_kind": diagnostics.install_kind(),
            "frozen": frozen,
            "bundle": bundle,
            "installer_flavor": "unknown",  # dmg / NSIS / portable 在运行时没有可靠记号，不猜
        },
        "channel": {"value": _channel_of(__version__), "basis": "inferred from the version string"},
        "os": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "mac_ver": platform.mac_ver()[0] if sys.platform == "darwin" else None,
            "windows": _windows_info() or None,
            "os_version": facts.get("os_version"),
        },
        "cpu": {
            "arch": platform.machine(),
            "cpu_count": os.cpu_count(),
            "model": facts.get("model"),
            "cpu": facts.get("cpu"),
            "rosetta": rosetta,
        },
        "frontend_shell": fe or None,
        "in_app_update": update
        or {
            "status": "unknown",
            "note": "no update check / install has happened in this window session",
        },
        "pip_updater": None if frozen else updater.settings(),
    }
    return [Item("shell", "detail-shell.json", _dumps(scrub.obj(doc)))]


# ---------------------------------------------------------------------------
# 9 存储坑
# ---------------------------------------------------------------------------
_SYNC_MARKERS = (
    ("onedrive", re.compile(r"onedrive", re.I)),
    ("icloud", re.compile(r"Mobile Documents|com~apple~CloudDocs|iCloud", re.I)),
    ("dropbox", re.compile(r"dropbox", re.I)),
    ("google_drive", re.compile(r"Google ?Drive|GoogleDrive", re.I)),
    ("nutstore", re.compile(r"Nutstore|坚果云", re.I)),
    ("box", re.compile(r"[/\\]Box( Sync)?[/\\]", re.I)),
    ("cloudstorage", re.compile(r"CloudStorage", re.I)),
)


def sync_providers(path: str) -> list[str]:
    return [name for name, rx in _SYNC_MARKERS if rx.search(path)]


def _disk(path: Path) -> dict:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        u = shutil.disk_usage(probe)
        return {"free_bytes": u.free, "total_bytes": u.total, "checked": str(probe)}
    except OSError as exc:
        return {"error": type(exc).__name__}


def _long_paths_enabled() -> int | str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as k:
            return int(winreg.QueryValueEx(k, "LongPathsEnabled")[0])
    except Exception as exc:  # noqa: BLE001
        return f"unreadable:{type(exc).__name__}"


def collect_storage(src: Sources, opts: DetailOptions, scrub: Scrubber) -> list[Item]:
    data = config.data_dir()
    doc: dict = {
        "schema": DETAIL_SCHEMA,
        "data_dir": {
            "path": str(data),
            "exists": data.exists(),
            "writable": os.access(data if data.exists() else data.parent, os.W_OK),
            "disk": _disk(data),
            "sync_providers": sync_providers(str(data)),
        },
        "windows_long_paths_enabled": _long_paths_enabled(),
    }
    fig = (src.project or {}).get("figures_dir")
    if fig:
        p = Path(fig)
        doc["project_dir"] = {
            "path": str(p),
            "path_length": len(str(p)),
            "exists": p.is_dir(),
            "writable": os.access(p, os.W_OK) if p.exists() else False,
            "disk": _disk(p),
            "sync_providers": sync_providers(str(p)),
        }
    return [Item("storage", "detail-storage.json", _dumps(scrub.obj(doc)))]


# ---------------------------------------------------------------------------
# 10 前端 console 环   11 显示环境
# ---------------------------------------------------------------------------
def collect_console(src: Sources, opts: DetailOptions, scrub: Scrubber) -> list[Item]:
    records = list((src.frontend or {}).get("console") or [])

    def build(recs: list) -> bytes:
        return _dumps(
            scrub.obj(
                {
                    "schema": DETAIL_SCHEMA,
                    "scope": "global (not cleared when switching projects)",
                    "client_truncated": (src.frontend or {}).get("client_truncated") or [],
                    "records": recs,
                }
            )
        )

    return [
        Item(
            "console",
            "detail-frontend-console.json",
            build(records),
            shrink=_list_shrinker(build, records),
        )
    ]


def collect_display(src: Sources, opts: DetailOptions, scrub: Scrubber) -> list[Item]:
    display = (src.frontend or {}).get("display")
    doc = {"schema": DETAIL_SCHEMA, **(display or {"status": "unknown"})}
    return [Item("display", "detail-display.json", _dumps(scrub.obj(doc)))]


# ---------------------------------------------------------------------------
# 12 网络概况
# ---------------------------------------------------------------------------
TUN_NET = ipaddress.ip_network("198.18.0.0/15")
DNS_HOSTS = ("tavotto.cn", "github.com")


def _strip_userinfo(url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    try:
        p = urlsplit(url if "://" in url else "//" + url)
        host = p.hostname or ""
        netloc = f"[{host}]" if ":" in host else host
        if p.port:
            netloc += f":{p.port}"
        return urlunsplit((p.scheme, netloc, p.path, "", "")) if host else _REDACTED
    except ValueError:
        return _REDACTED


def proxy_facts() -> dict:
    import urllib.request

    # `NO_PROXY` / `no_proxy` 的内容常是内部域名 / 内网地址：只记条目数，不记主机名与 IP
    env: dict[str, str] = {}
    no_proxy_entries = 0
    for k, v in os.environ.items():
        if "proxy" not in k.lower() or not v:
            continue
        if k.lower() == "no_proxy":
            no_proxy_entries = max(no_proxy_entries, len([e for e in v.split(",") if e.strip()]))
            continue
        env[k] = _strip_userinfo(v)
    try:
        system = {
            k: _strip_userinfo(v)
            for k, v in urllib.request.getproxies().items()
            if v and k.lower() not in ("no", "no_proxy", "exclude", "exceptions")
        }
    except Exception:  # noqa: BLE001
        system = {}
    return {
        "configured": bool(env or system),
        "env": env,
        "resolved": system,
        "no_proxy_entries": no_proxy_entries,
    }


def _resolve(host: str) -> list[str]:
    """系统解析器查一次地址（只做名字解析，不建立连接）。"""
    infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    return sorted({i[4][0] for i in infos})


#: 仍在跑的名字解析线程（按主机）。超时后线程还会跑到系统解析器返回为止：同一主机上一个没结束就不再起新的，
#: 反复点发送也不会堆出越来越多的遗留线程——总数以 `len(DNS_HOSTS)` 为上界。
_DNS_PENDING: dict[str, threading.Thread] = {}
_DNS_LOCK = threading.Lock()


def dns_fact(host: str) -> dict:
    box: dict = {}

    def run() -> None:
        try:
            box["addrs"] = _resolve(host)
        except OSError as exc:
            box["error"] = type(exc).__name__

    with _DNS_LOCK:
        old = _DNS_PENDING.get(host)
        if old is not None and old.is_alive():
            return {"host": host, "result": "unknown", "reason": "previous_lookup_pending"}
        t = threading.Thread(target=run, name="diag-dns", daemon=True)
        _DNS_PENDING[host] = t
        t.start()
    t.join(DNS_TIMEOUT_S)
    if t.is_alive():
        return {"host": host, "result": "unknown", "reason": "timeout"}
    if "addrs" not in box:
        return {"host": host, "result": "unknown", "reason": box.get("error", "unknown")}
    rewritten = False
    for a in box["addrs"]:
        try:
            if ipaddress.ip_address(a.split("%")[0]) in TUN_NET:
                rewritten = True
        except ValueError:
            continue
    return {"host": host, "addresses": box["addrs"], "tun_rewritten_198_18": rewritten}


def collect_network(src: Sources, opts: DetailOptions, scrub: Scrubber) -> list[Item]:
    now = datetime.now().astimezone()
    off = now.utcoffset() or timedelta(0)
    doc = {
        "schema": DETAIL_SCHEMA,
        "proxy": proxy_facts(),
        "dns": [dns_fact(h) for h in DNS_HOSTS],
        "timezone": {
            "name": time.tzname[0] if not time.daylight else list(time.tzname),
            "utc_offset_minutes": int(off.total_seconds() // 60),
        },
    }
    return [Item("network", "detail-network.json", _dumps(scrub.obj(doc)))]


# ---------------------------------------------------------------------------
# A 脚本源码（勾选）
# ---------------------------------------------------------------------------
def source_candidates(
    src: Sources,
    failed: list[str] | None = None,
    ids: dict[str, FileId] | None = None,
    over_cap: list[str] | None = None,
) -> list[tuple[str, Path]]:
    """要收的脚本：判据 = 当前项目**登记的脚本**（项目相对路径），正在编辑 / 选中的面板的脚本排最前，
    其余按修改时间新的在前。只收解析后仍在项目目录内的文件（登记表是用户文件，不信它）。
    **每个候选单独 stat**：一个无关脚本在枚举与排序之间被删 / 不可读，只让它自己进 `failed`（调用方标 read_failed），
    不拖垮整个源码类别。"""
    fig = (src.project or {}).get("figures_dir")
    if not fig:
        return []
    root = Path(fig).resolve()
    focus = [
        str(p.get("script"))
        for p in ((src.frontend or {}).get("overrides") or [])
        if p.get("script")
    ]
    seen: dict[str, Path] = {}
    fids: dict[str, FileId] = {}
    tried: set[str] = set()
    for rel in [*focus, *src.scripts]:
        if rel in tried:
            continue
        tried.add(rel)
        try:
            path = (root / rel).resolve()
            path.relative_to(root)
        except (OSError, ValueError):
            continue
        try:
            if not path.is_file():
                continue
            fids[rel] = FileId.of(path.stat())
        except OSError:
            if failed is not None:
                failed.append(rel)
            continue
        seen[rel] = path
    ordered = list(dict.fromkeys(r for r in focus if r in seen))  # 多张面板同一脚本：按首次出现去重
    rest = [r for r in seen if r not in ordered]
    rest.sort(
        key=lambda r: -fids[r].mtime_ns
    )  # 用先前各自 stat 到的 mtime 排，排序期间不再碰文件系统
    if ids is not None:
        ids.update(fids)
    chosen = [*ordered, *rest]
    if over_cap is not None:  # 数量上限之外的：调用方在 index 与 truncated 里记账
        over_cap.extend(chosen[SOURCE_FILES:])
    return [(r, seen[r]) for r in chosen][:SOURCE_FILES]


def _prose_spans(text: str) -> list[tuple[int, int]] | None:
    """Python 源码里的**注释与字符串字面量**区间 `[(起, 止)]`（字符偏移）；tokenize 失败（语法坏的脚本）回 None。"""
    import io
    import tokenize

    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    kinds = {tokenize.COMMENT, tokenize.STRING}
    kinds |= {
        getattr(tokenize, n)
        for n in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END")
        if hasattr(tokenize, n)
    }
    spans: list[tuple[int, int]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.ERRORTOKEN:
                # Python ≤ 3.11 的 tokenize 对未闭合的单引号字符串**不抛异常**，而是产出 ERRORTOKEN（后面的内容
                # 就不再是字符串 token）：视同解析失败，整份走完整模式
                return None
            if tok.type in kinds:
                a = starts[tok.start[0] - 1] + tok.start[1]
                b = starts[tok.end[0] - 1] + tok.end[1]
                spans.append((a, b))
    except (tokenize.TokenError, IndentationError, SyntaxError, IndexError):
        return None
    return spans


_SKIP_TOKENS = ("COMMENT", "NL", "INDENT", "DEDENT", "ENCODING")
_STR_PREFIX = re.compile(r"^([A-Za-z]*)(\"\"\"|'''|\"|')")


def _logical_tokens(text: str) -> list[tuple[str, str, int, int, int]] | None:
    """`.py` 的有意义 token：`(类型名, 文本, 起, 止, 逻辑行号)`。3.12+ 把 f-string 拆成 FSTRING_START / MIDDLE / END，
    这里并回一个 STRING（整段）；注释、空行、缩进不要。tokenize 失败 / ERRORTOKEN 回 None。"""
    import io
    import tokenize

    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    out: list[tuple[str, str, int, int, int]] = []
    line_id = 0
    depth = 0
    fs_start = 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            name = tokenize.tok_name.get(tok.type, "")
            if tok.type == tokenize.ERRORTOKEN:
                return None
            a = starts[tok.start[0] - 1] + tok.start[1]
            b = starts[tok.end[0] - 1] + tok.end[1]
            if name == "FSTRING_START":
                if depth == 0:
                    fs_start = a
                depth += 1
                continue
            if name == "FSTRING_END":
                depth -= 1
                if depth == 0:
                    out.append(("STRING", text[fs_start:b], fs_start, b, line_id))
                continue
            if depth > 0 or name in _SKIP_TOKENS or name == "ENDMARKER":
                continue
            if name == "NEWLINE":
                line_id += 1
                continue
            out.append((name, tok.string, a, b, line_id))
    except (tokenize.TokenError, IndentationError, SyntaxError, IndexError):
        return None
    return out


def _is_secret_name(tok: tuple[str, str, int, int, int]) -> bool:
    kind, string = tok[0], tok[1]
    if kind == "NAME":
        return is_secret_key(string)
    if kind == "STRING":
        m = _STR_PREFIX.match(string)
        if not m or len(string) > 80:
            return False
        return is_secret_key(string[m.end() :].rstrip("\"'"))
    return False


def _callee_is_secret(name: str) -> bool:
    """被调用名：除了 `is_secret_key`，名字里作为一个**词**出现的 `key`（`set_key` / `setKey`）也算
    （裸 `key` 作为整个名字才算秘密键，但 `set_key("…")` 的实参就是密钥）。"""
    words = re.split(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])", name)
    return is_secret_key(name) or any(w.lower() == "key" for w in words)


CALL_SCAN_TOKENS = 400  # 向前找外层 `(` 的 token 数上限（超长字面量列表里别做成平方）


def _in_secret_call(toks: list[tuple[str, str, int, int, int]], i: int) -> bool:
    """`toks[i]` 是不是某个**被调用名含秘密词**的调用的直接实参（位置或关键字都算，嵌套调用只看直接实参）：
    `set_password("…")`、`client.set_token("…")`、`auth("user", "pw")`、`credentials.update("…")`。
    被调用名 = `(` 前面的属性链（`a.b.c`）里任意一段。"""
    line = toks[i][4]
    depth = 0
    k = i - 1
    floor = max(-1, i - CALL_SCAN_TOKENS)
    while k > floor and toks[k][4] == line:
        t = toks[k][1]
        if t in (")", "]", "}"):
            depth += 1
        elif t in ("(", "[", "{"):
            if depth == 0:
                break
            depth -= 1
        k -= 1
    else:
        return False
    if k < 1 or toks[k][1] != "(" or toks[k - 1][0] != "NAME":
        return False
    n = k - 1
    while n >= 0 and toks[n][4] == line and toks[n][0] == "NAME":
        if _callee_is_secret(toks[n][1]):
            return True
        if n >= 1 and toks[n - 1][1] == ".":
            n -= 2
        else:
            break
    return False


def _secret_string_spans(text: str) -> list[tuple[int, int, str]]:
    """**结构判定**：哪些字符串字面量（含 b / r / u / f / rb… 前缀、f-string 整段）处在「秘密上下文」里——
    `[(起, 止, 替换文本)]`。上下文有三种：
    ① 赋值 / 注解赋值 / 增量赋值 / 关键字参数的**目标**含秘密名（`api_key = b"…"`、`token: str = "…"`、
       `config["password"] = "…"`、`self.api_key = "…"`、`f(api_key="…")`）；
    ② dict 字面量里键含秘密名的值（`{"token": "…"}`）；
    ③ 紧跟在秘密名字符串参数后面的值（`setdefault("api_key", "…")`、`("token", "…")`）。
    隐式拼接（`("a" "b")`）一并算。数字 / 非字符串表达式不动（`token = self._next()` 原样）。"""
    toks = _logical_tokens(text)
    if toks is None:
        return []
    spans: list[tuple[int, int, str]] = []
    for i, tok in enumerate(toks):
        if tok[0] != "STRING":
            continue
        m = _STR_PREFIX.match(tok[1])
        if m is None or len(tok[1]) <= len(m.group(0)) * 2:
            continue  # 空字符串
        line = tok[4]
        j = i - 1
        while j >= 0 and toks[j][4] == line and (toks[j][0] == "STRING" or toks[j][1] == "("):
            j -= 1  # 隐式拼接 / 包着的括号
        if j < 0 or toks[j][4] != line:
            continue
        op = toks[j][1]
        secret = False
        if op in ("=", ":=", "+=", "|=", ":"):
            depth = 0
            k = j - 1
            while k >= 0 and toks[k][4] == line:
                t = toks[k][1]
                if t in (")", "]", "}"):
                    depth += 1
                elif t in ("(", "[", "{"):
                    if depth == 0:
                        break
                    depth -= 1
                elif t == "," and depth == 0:
                    break
                k -= 1
            secret = any(_is_secret_name(t) for t in toks[k + 1 : j])
        elif op == "," and j >= 1 and toks[j - 1][0] == "STRING":
            secret = _is_secret_name(toks[j - 1])
        if not secret:
            secret = _in_secret_call(toks, i)
        if secret:
            prefix = "".join(c for c in m.group(1) if c in "bBuU")
            spans.append((tok[2], tok[3], f'{prefix}"{_REDACTED}"'))
    return spans


def scrub_script(text: str, rel: str, full: Scrubber, code: Scrubber) -> str:
    """脚本源码的脱敏。可执行代码用 `code`（源码模式：`token = self._next()` 原样）；**注释和字符串字面量里**
    的 `key=value` 是明文，必须用 `full`（完整 KV 替换）——`# api_key=REAL_SECRET` 不带引号，源码模式会放过它。
    非 .py 一律 `full`；tokenize 失败（语法坏的 .py、未闭合的字符串）时整份按 `full` 处理。"""
    text = _nl(text)
    if not rel.lower().endswith(".py"):
        return full.text(text)
    text = _redact_pem(text)  # 多行的块先整体换掉：注释里的 PEM 每行是一个独立的 COMMENT token
    spans = _prose_spans(text)
    if spans is None:
        # 语法坏 / 字符串没闭合：分不清哪里是注释哪里是字符串（`x = """api_key=REAL` 之后全是字符串），整份按完整模式
        return full.text(text)
    secret = {(a, b): rep for a, b, rep in _secret_string_spans(text)}
    merged = sorted(
        {*spans, *secret}, key=lambda ab: (ab[0], -ab[1])
    )  # 同起点先处理更长的（f-string 整段）
    out: list[str] = []
    pos = 0
    for a, b in merged:
        if a < pos:
            continue
        out.append(text[pos:a])
        out.append(secret[(a, b)] if (a, b) in secret else full.text(text[a:b]))
        pos = b
    out.append(text[pos:])
    # 先把注释 / 字符串里的明文换掉，再整体过一遍源码模式（带引号的 `api_key = "…"` 字面量归它）
    return code.text("".join(out))


def _decode_script(raw: bytes, rel: str) -> tuple[str, str, bool]:
    """按脚本**声明的编码**解码（PEP 263 的 coding 行 + BOM，`tokenize.detect_encoding`）：`(文本, 编码, 是否退回)`。
    声明不合法 / 不认识的编码退回 UTF-8 + replace，并让调用方在 index 里记 `encoding_fallback`。
    非 `.py` 一律 UTF-8。头部截断可能切在多字节字符中间，所以解码本身总是 replace。"""
    if not rel.lower().endswith(".py"):
        return raw.decode("utf-8", errors="replace"), "utf-8", False
    import io
    import tokenize

    try:
        enc, _lines = tokenize.detect_encoding(io.BytesIO(raw).readline)
        return raw.decode(enc, errors="replace"), enc, False
    except (SyntaxError, LookupError):
        return raw.decode("utf-8", errors="replace"), "utf-8", True


_CODING_DECL = re.compile(r"^([ \t\f]*#.*?coding[:=][ \t]*)[-\w.]+")


def _declare_utf8(text: str) -> str:
    """脚本按声明的编码解码后我们**一律写出 UTF-8**：前两行（PEP 263）里的 `coding:` 声明改成 `utf-8`（行里其余内容不动），
    否则解压后的 `# coding: latin-1` 会让 Python / 编辑器把 UTF-8 字节读成乱码。BOM 已在解码时去掉、写出时不加。"""
    lines = text.split("\n", 2)
    for i in range(min(2, len(lines))):
        if i == 1 and not re.match(r"^[ \t\f]*(?:#.*)?$", lines[0]):
            break  # 第一行是代码：第二行的声明不生效
        new = _CODING_DECL.sub(r"\1utf-8", lines[i], count=1)
        if new != lines[i]:
            lines[i] = new
            break
    return "\n".join(lines)


def collect_source(src: Sources, opts: DetailOptions, scrub: Scrubber) -> list[Item]:
    items: list[Item] = []
    code_scrub = Scrubber(source_mode=True)
    full_scrub = Scrubber()
    index = []
    stat_failed: list[str] = []
    ids: dict[str, FileId] = {}
    over_cap: list[str] = []
    candidates = source_candidates(src, stat_failed, ids, over_cap)
    index.extend(
        {"script": _clean_name(scrub, r), "bytes": ids[r].size, "skipped_over_count_cap": True}
        for r in over_cap
    )
    index.extend({"script": _clean_name(scrub, rel), "read_failed": True} for rel in stat_failed)
    for n, (rel, path) in enumerate(candidates):
        raw, why = _read_capped(path, ids[rel], SOURCE_FILE_CAP)
        if raw is None:
            index.append({"script": _clean_name(scrub, rel), why: True})
            continue
        size = ids[rel].size
        text, encoding, fell_back = _decode_script(raw, rel)
        out_text = scrub_script(text, rel, full_scrub, code_scrub)
        if rel.lower().endswith(".py"):
            out_text = _declare_utf8(out_text)
        data = out_text.encode("utf-8")
        if size > SOURCE_FILE_CAP:
            data += b"\n" + _marker(len(raw), size, "first")
        zip_name = f"{n + 1:02d}-{_member_name(scrub, rel, slash='__')}"
        row = {
            "script": _clean_name(scrub, rel),
            "id": _name_id(rel),
            "file": zip_name,
            "bytes": size,
            "encoding": encoding,
        }
        if fell_back:
            row["encoding_fallback"] = (
                True  # 声明的编码不合法 / 不认识：按 UTF-8 + replace 解的（文件里的字节不保证准）
            )
        index.append(row)
        items.append(
            Item(
                "source",
                zip_name,
                data,
                shrink=lambda cap, d=data: _head_bytes(d, cap),
                original_bytes=size if size > SOURCE_FILE_CAP else None,
            )
        )
    items.insert(
        0,
        Item(
            "source",
            "index.json",
            _dumps(
                {
                    "schema": DETAIL_SCHEMA,
                    "criterion": "scripts registered in the open project (panel scripts first, then newest first)",
                    "scripts": index,
                }
            ),
            dropped_bytes=sum(ids[r].size for r in over_cap),
        ),
    )
    return items


# ---------------------------------------------------------------------------
# B 系统崩溃报告（勾选）
# ---------------------------------------------------------------------------
_OWN_PROCESS = re.compile(r"^(tavotto|workerd)", re.I)


def crash_dirs() -> list[tuple[str, Path]]:
    """(来源标签, 目录)。macOS DiagnosticReports；Windows CrashDumps 与 WER 的 ReportArchive / ReportQueue（用户级与机器级）。"""
    home = Path.home()
    out: list[tuple[str, Path]] = []
    if sys.platform == "darwin":
        out.append(("macos_diagnostic_reports", home / "Library" / "Logs" / "DiagnosticReports"))
    elif os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            out.append(("windows_crashdumps", Path(local) / "CrashDumps"))
            wer = Path(local) / "Microsoft" / "Windows" / "WER"
            out.append(("windows_wer_user_archive", wer / "ReportArchive"))
            out.append(("windows_wer_user_queue", wer / "ReportQueue"))
        pdata = os.environ.get("PROGRAMDATA")
        if pdata:
            wer = Path(pdata) / "Microsoft" / "Windows" / "WER"
            out.append(("windows_wer_machine_archive", wer / "ReportArchive"))
            out.append(("windows_wer_machine_queue", wer / "ReportQueue"))
    return out


def _process_name(file_name: str) -> str:
    stem = file_name.rsplit(".", 1)[0]
    m = re.match(r"^(.*?)[-_]\d{4}-\d\d-\d\d[-_]\d{6}", stem)
    return m.group(1) if m else stem


DUMP_SCAN_BYTES = 4 * MiB


def _mentions_tavotto(path: Path) -> bool:
    """二进制转储里有没有点名 Tavotto（UTF-8 或 UTF-16LE，大小写不敏感）：python.exe 的转储靠它认归属。"""
    got = _read_head(path, DUMP_SCAN_BYTES)
    if got is None:
        return False
    low = got[0].lower()
    return b"tavotto" in low or "tavotto".encode("utf-16-le") in low


def _source_tag(label: str) -> str:
    """内层 zip 成员名的来源前缀：同一份 WER 报告目录可以同时出现在 ReportQueue 与 ReportArchive、用户级与机器级。"""
    return (
        label.replace("windows_wer_", "")
        .replace("windows_", "")
        .replace("macos_diagnostic_reports", "macos")
        .replace("_", "-")
    )


def _crash_candidates(now: float) -> list[dict]:
    """7 天内、属于 Tavotto / sidecar / 渲染 python 进程的崩溃文件：文本报告（脱敏后收）与二进制转储（原样收）。"""
    found: list[dict] = []
    for label, base in crash_dirs():
        if not base.is_dir():
            continue
        try:
            entries = list(base.iterdir())
        except OSError:
            continue
        for p in entries:
            try:
                st = p.stat()
            except OSError:
                continue
            if now - st.st_mtime > CRASH_WINDOW_S:
                continue
            name, low = p.name, p.name.lower()
            members: list[tuple[Path, str]] = []  # (文件, 条目名)
            if p.is_dir():  # WER：一个目录一份报告，里头有 Report.wer 和可能的 .dmp
                if not label.startswith("windows_wer"):
                    continue
                try:
                    inside = [q for q in p.iterdir() if q.is_file()]
                except OSError:
                    inside = []  # 目录读不了：只让这一份报告出局
                own = bool(_OWN_PROCESS.match(name.split("_", 1)[-1])) or "tavotto" in low
                if not own and "python" in low:
                    wer = p / "Report.wer"
                    own = wer.is_file() and _mentions_tavotto(wer)
                if not own:
                    continue
                members = [
                    (q, f"{name}-{q.name}")
                    for q in inside
                    if q.suffix.lower() in (".wer", ".dmp", ".mdmp", ".hdmp", ".txt")
                ]
            else:
                ext = low.rsplit(".", 1)[-1]
                if ext not in ("ips", "crash", "dmp", "mdmp"):
                    continue
                proc = _process_name(name) if ext in ("ips", "crash") else name.split(".", 1)[0]
                own = bool(_OWN_PROCESS.match(proc))
                if not own and proc.lower().startswith(("python", "sidecar")):
                    own = _mentions_tavotto(p)  # 报告 / 转储里点名 Tavotto 才算我们的
                if not own:
                    continue
                members = [(p, name)]
            for q, entry in members:
                try:
                    qs = q.stat()
                except OSError:
                    found.append({"label": label, "name": q.name, "mtime": 0, "id": None})
                    continue
                binary = q.suffix.lower() in (".dmp", ".mdmp", ".hdmp")
                found.append(
                    {
                        "label": label,
                        "name": q.name,
                        "path": q,
                        "entry": (
                            f"{_source_tag(label)}-{entry}"
                            if label.startswith("windows_wer")
                            else entry
                        ),
                        "mtime": qs.st_mtime,
                        "size": qs.st_size,
                        "id": FileId.of(qs),
                        "binary": binary,
                    }
                )
    found.sort(key=lambda c: -c["mtime"])
    return found


def collect_crash_reports(
    src: Sources, opts: DetailOptions, scrub: Scrubber, now: float | None = None
) -> list[Item]:
    """崩溃报告（勾选 B）。文本报告（.ips / .crash / Report.wer）过脱敏；**Windows 崩溃转储（.dmp）是程序内存快照，
    可能含正在打开的文件内容与路径，没法脱敏——按维护者裁决原样收**（确认框与隐私声明都写明）。
    份额按**文件整份**取舍、新的优先，不截半个文件；放不下的记进索引与 manifest 的 `truncated`。"""
    now = time.time() if now is None else now
    budget = SHARE["crash_reports"]
    used = 0
    index: list[dict] = []
    items: list[Item] = []
    omitted = 0
    taken: set[str] = {"index.json"}
    for c in _crash_candidates(now):
        if c["id"] is None:  # 登记时 stat 失败：只让它自己出局
            index.append(
                {
                    "source": c["label"],
                    "name": _clean_name(scrub, c["name"]),
                    "content": "read_failed",
                }
            )
            continue
        row = {
            "source": c["label"],
            "name": _clean_name(scrub, c["name"]),
            "id": _name_id(c["name"]),
            "bytes": c["size"],
            "mtime": _iso(c["mtime"]),
            "content": "raw_binary" if c["binary"] else "redacted_text",
        }
        if len(items) >= CRASH_FILES or used + c["size"] > budget:
            # 数量上限与字节份额是两种原因，index 里分开写（都计入 truncated 的 original_bytes）
            row["content"] = (
                "skipped_over_count_cap" if len(items) >= CRASH_FILES else "skipped_over_budget"
            )
            omitted += c["size"]
            index.append(row)
            continue
        # 读之前的身份必须与登记时一致；上限 = 登记大小（已确认放得进剩余份额）+ 1
        raw, why = _read_capped(c["path"], c["id"], budget - used, whole=True)
        if raw is None:
            # 读失败（读不到）与读取期间文件在变（只拿到前缀）是两回事，都不收内容，也都不是超预算
            row["content"] = why
            index.append(row)
            continue
        if c["binary"]:
            data = raw
        else:
            text = raw.decode(
                "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8", errors="replace"
            )
            data = scrub.text(text).encode("utf-8")
        used += len(data)
        member = _member_name(
            scrub, c["entry"], taken
        )  # 重名追加 -2、-3…，index 这一行指向自己的成员名
        row["file"] = member
        index.append(row)
        items.append(Item("crash_reports", member, data))
    items.insert(
        0,
        Item(
            "crash_reports",
            "index.json",
            _dumps(
                {
                    "schema": DETAIL_SCHEMA,
                    "window_days": CRASH_WINDOW_S // 86400,
                    "note": "Windows crash dumps (.dmp) are raw memory snapshots and cannot be redacted",
                    "dirs": [scrub.text(str(b)) for _l, b in crash_dirs()],
                    "reports": scrub.obj(index),
                }
            ),
            dropped_bytes=omitted,
        ),
    )
    return items


# ---------------------------------------------------------------------------
# 汇总、截断
# ---------------------------------------------------------------------------
DEFAULT_COLLECTORS: list[tuple[str, str, Callable]] = [
    ("errors", "detail-errors.json", collect_errors),
    ("logs", "detail-app-log.txt", collect_logs),
    ("state", "detail-state.zip", collect_state),
    ("requests", "detail-requests.json", collect_requests),
    ("overrides", "detail-overrides.zip", collect_overrides),
    ("render_env", "detail-render-env.json", collect_render_env),
    ("runs", "detail-runs.json", collect_runs),
    ("shell", "detail-shell.json", collect_shell),
    ("storage", "detail-storage.json", collect_storage),
    ("console", "detail-frontend-console.json", collect_console),
    ("display", "detail-display.json", collect_display),
    ("network", "detail-network.json", collect_network),
]


def collect(src: Sources, opts: DetailOptions) -> list[Item]:
    """全部 detail 文件。某一类采集失败只让那一类变成一份 `collection_error`。"""
    scrub = Scrubber()
    plan = list(DEFAULT_COLLECTORS)
    if opts.source:
        plan.append(("source", "optional-source.zip", collect_source))
    if opts.crash_reports:
        plan.append(("crash_reports", "optional-crash-reports.zip", collect_crash_reports))
    if opts.corrupt_sessions:
        plan.append(("corrupt_sessions", "optional-corrupt-sessions.zip", collect_corrupt_sessions))
    items: list[Item] = []
    for kind, path, fn in plan:
        try:
            got = fn(src, opts, scrub)
        except Exception as exc:  # noqa: BLE001 — 采集失败不让备包失败
            LOG.warning("detail 采集失败: %s", logsafe.known(kind, KINDS))
            # 扁平类别的失败文件顶替它自己的条目名；装进内层 zip 的类别写成内层的 error.json
            got = [_failed(kind, "error.json" if kind in CONTAINERS else path, exc, scrub)]
        for it in got:
            # 份额：同一类里超出的整份往后丢（先到先得，顺序 = 采集顺序）
            items.append(it)
    return _apply_shares(items)


def _apply_shares(items: list[Item]) -> list[Item]:
    used: dict[str, int] = {}
    dropped: dict[str, int] = {}
    out: list[Item] = []
    for it in items:
        cap = SHARE.get(it.kind, 64 * 1024)
        left = cap - used.get(it.kind, 0)
        if it.size > left:
            if it.shrink is not None and left > 2048:
                it = dataclasses.replace(
                    it, data=it.shrink(left), original_bytes=it.original_bytes or it.size
                )
            else:
                dropped[it.kind] = (
                    dropped.get(it.kind, 0) + it.size
                )  # 整份没收：记账（manifest 的 original_bytes）
                continue
        used[it.kind] = used.get(it.kind, 0) + it.size
        out.append(it)
    for kind, n in dropped.items():  # 记在该类的第一份上（fit 汇总 `dropped_bytes`）
        for idx, it in enumerate(out):
            if it.kind == kind:
                out[idx] = dataclasses.replace(it, dropped_bytes=it.dropped_bytes + n)
                break
    return out


def _reconcile_index(group: list[Item]) -> list[Item]:
    """内层 zip 的 `index.json` 必须与 zip 实际内容一致：index 里写着 `file` 的条目，对应文件不在这个 zip 里
    （份额阶段或 `fit` 整份丢掉了）就标 `skipped_over_budget: true`。"""
    members = {it.path for it in group}
    out: list[Item] = []
    for it in group:
        if it.path != "index.json":
            out.append(it)
            continue
        try:
            doc = json.loads(it.data)
        except ValueError:
            out.append(it)
            continue

        def walk(o):
            if isinstance(o, dict):
                f = o.get("file")
                if isinstance(f, str) and f not in members:
                    o["skipped_over_budget"] = True
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(doc)
        out.append(dataclasses.replace(it, data=_dumps(doc)))
    return out


def entries(items: list[Item]) -> list[tuple[str, bytes]]:
    """items → 外层 ZIP 的扁平条目 `[(条目名, 字节)]`：`CONTAINERS` 里的类别合成一个内层 zip，其余一类一个条目。"""
    import io
    import zipfile

    flat: list[tuple[str, bytes]] = []
    inner: dict[str, list[Item]] = {}
    for it in items:
        if it.kind in CONTAINERS:
            inner.setdefault(it.kind, []).append(it)
        else:
            flat.append((it.path, it.data))
    for kind, group in inner.items():
        group = _reconcile_index(group)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for it in group:
                z.writestr(it.path, it.data)
        flat.append((CONTAINERS[kind], buf.getvalue()))
    return sorted(flat)


def ratio_safe(data: bytes) -> bytes:
    """服务端拒收单条目 `file_size > 1024 且 file_size > 200 × compress_size`（高度重复的日志会压到这个比值以上）：
    出包后逐条核对，超了的条目改 ZIP_STORED 重写（内容与顺序不变）。没有超的包原样返回。"""
    import io
    import zipfile

    limit = 200
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        bad = {
            i.filename
            for i in z.infolist()
            if i.file_size > 1024 and i.file_size > limit * max(i.compress_size, 1)
        }
        if not bad:
            return data
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as w:
            for i in z.infolist():
                content = z.read(i)
                info = zipfile.ZipInfo(i.filename, i.date_time)
                info.external_attr = i.external_attr
                info.compress_type = zipfile.ZIP_STORED if i.filename in bad else i.compress_type
                w.writestr(info, content)
    return out.getvalue()


def _kind_bytes(items: list[Item]) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        out[it.kind] = out.get(it.kind, 0) + it.size
    return out


def _compress_ratio(items: list[Item], kind: str) -> float:
    raw = b"".join(i.data for i in items if i.kind == kind)
    if not raw:
        return 1.0
    return max(0.02, len(zlib.compress(raw, 6)) / len(raw))


def fit(
    items: list[Item],
    build: Callable[[list[Item], list[dict]], bytes],
    limit: int,
) -> tuple[bytes, list[Item], list[dict]]:
    """把 detail 放进 `limit` 字节的 ZIP：超限时按优先级从低（编号大）往高截断。

    `build(items, truncated)` 回完整 ZIP 字节（核心文件 + detail + manifest）。返回
    `(zip 字节, 最终保留的 items, truncated 记录)`。核心文件不在这里，永远不截；全部 detail 截光仍超限
    就如实返回（调用方走 `too_large`）。"""
    original = _kind_bytes(items)
    current = list(items)
    truncated: dict[str, dict] = {}
    for it in items:  # 采集阶段整份放不下的文件（崩溃报告按文件整份取舍）也是被截掉的内容
        if it.dropped_bytes:
            original[it.kind] = original.get(it.kind, 0) + it.dropped_bytes
            truncated.setdefault(it.kind, {"original_bytes": 0})

    def record() -> list[dict]:
        kept = _kind_bytes(current)
        return [
            {"kind": k, "original_bytes": v["original_bytes"], "kept_bytes": kept.get(k, 0)}
            for k, v in sorted(truncated.items(), key=lambda kv: -PRIORITY.get(kv[0], 99))
        ]

    # 份额阶段就被截短的单个文件也记一笔
    for it in current:
        if it.original_bytes is not None:
            truncated.setdefault(it.kind, {"original_bytes": 0})
    for k in list(truncated):
        truncated[k]["original_bytes"] = max(
            original.get(k, 0), sum(i.original_bytes or i.size for i in current if i.kind == k)
        )
    data = build(current, record())
    for _ in range(FIT_MAX_REBUILDS):
        if len(data) <= limit:
            return data, current, record()
        kept = _kind_bytes(current)
        victim = max(
            (k for k, v in kept.items() if v > 0), key=lambda k: PRIORITY.get(k, 99), default=None
        )
        if victim is None:
            return data, current, record()
        excess = len(data) - limit
        ratio = _compress_ratio(current, victim)
        cut = int(excess / ratio * 1.15) + 1024
        target = kept[victim] - cut
        truncated.setdefault(victim, {"original_bytes": original.get(victim, kept[victim])})
        if target < 2048:
            current = [i for i in current if i.kind != victim]
        else:
            current = _shrink_kind(current, victim, target)
        data = build(current, record())
    return data, current, record()


def _shrink_kind(items: list[Item], kind: str, target: int) -> list[Item]:
    """把某一类缩到 ≤ target 字节：能缩的文件按比例缩，不能缩的从末尾整份丢。"""
    mine = [i for i in items if i.kind == kind]
    total = sum(i.size for i in mine)
    if total <= target:
        return items
    f = target / total
    shrunk: dict[str, Item] = {}
    for it in mine:
        if it.shrink is not None:
            cap = max(0, int(it.size * f))
            data = it.shrink(cap) if cap >= 512 else b""
            if data:
                shrunk[it.path] = dataclasses.replace(it, data=data)
        else:
            shrunk[it.path] = it
    # 不能缩的文件合起来仍超标：从末尾整份丢
    order = [i.path for i in mine if i.path in shrunk]
    while order and sum(shrunk[p].size for p in order) > target:
        shrunk.pop(order.pop())
    return [
        shrunk[i.path] if i.kind == kind and i.path in shrunk else i
        for i in items
        if i.kind != kind or i.path in shrunk
    ]
