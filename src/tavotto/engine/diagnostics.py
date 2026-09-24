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
import unicodedata
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
#: 2 = 增加了 frontend-state.json / interaction-trace.jsonl / manifest.json；
#: 3 = report.json 的 project 段换形（#524）：去掉 `name`，`figures_dir` 只剩 `<project:哈希>`，
#: 新增 `location`，导出 / 备份 / 文档目录按段哈希——schema 2 的读法认不出这些，必须升号（#524 评审）。
#: 与 `web/src/diagnostics/types.ts` 的同名常量是严格同源对。
BUNDLE_SCHEMA_VERSION = 3
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


#: 云盘挂载点的目录名里带着账号：`~/Library/CloudStorage/坚果云-<邮箱>`、`GoogleDrive-<邮箱>`、
#: `OneDrive-<机构名>`。服务商名留着（「项目在云盘里」对排障有用：同步冲突、占位文件），账号哈希
_CLOUD_ACCOUNT = re.compile(r"(CloudStorage[/\\])([^/\\\s\"'-]+)-([^/\\\"']+)")
#: 邮箱出现在哪里都不该出门（云盘目录名、Git 配置、日志里的账号提示）。
#:
#: **不在 token 内部找地址的边界**（#536 评审连续五轮）：先后漏过只认 ASCII 的 `用户@例子.公司`、
#: punycode 的 `--p1ai` 尾巴、转义的 `＠` 与代理对、IDNA 的 `l·l`，以及在「分隔符」处切开
#: 的 `o'connor@…`、`user@[192.0.2.1]`——每一轮都是在 token 里判断「地址从哪到哪」时漏一类。
#: 所以不再判：
#:
#: * token = 连续的非空白字符。一行整个是 JSON（`{` / `[` / `"` 开头且解析得了）时，只在 JSON
#:   字符串字面量的内容里按空白切，引号与 `{}[],:` 结构原样留下；其余文本只按空白切；
#: * token 里只要有 `@`（`＠`、转义 `@` / `＠`、URL 编码 `%40` 都算），**整个 token**
#:   换成 `<email>`——`(user@x.com),` 连括号逗号一起抹，不猜边界；
#: * 引号括起来的本地部分（`"quoted local"@x.com`）里会有空白：`@` 前的引号数是奇数时，
#:   把 token 往左并到同一行上一个 `"` 所在的 token；
#: * 只放行负面清单（`_not_an_email`），三条都是结构上确定不是地址的格式。
_EMAIL_AT = re.compile(r"[@＠]|\\u(?:0040|[Ff][Ff]20)|%40")
_EMAIL_DOTS = ".。．｡"
#: 放行：`pkg@1.2.3` / `matplotlib@3.10` / `pkg@2.0.0-beta`——域名一侧是版本号（数字段 + 可选的
#: 预发布 / 构建后缀），不是域名。只认 ASCII 数字：`\d` 会认全角与其他文字的数字。
_VERSION_AFTER_AT = re.compile(r"v?[0-9]+(?:\.[0-9]+)*(?:[-+][0-9A-Za-z.+-]*)?")
_JSON_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')
_JSON_ESCAPE = re.compile(r"\\u([0-9A-Fa-f]{4})")


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def _root_variants(path: str) -> list[str]:
    """一条项目根在文本里可能的写法：原样、realpath（macOS 的 /tmp → /private/tmp、软链接）、
    Windows 的正斜杠写法。去掉末尾分隔符，免得 `…/paper` 与 `…/paper/` 各算一条。"""
    out: list[str] = []
    for cand in (path, os.path.realpath(path)):
        cand = cand.rstrip("/\\")
        if not cand:
            continue
        out.append(cand)
        if "\\" in cand:
            out.append(cand.replace("\\", "/"))
    return list(dict.fromkeys(out))


def project_roots(project: dict | None = None) -> list[tuple[str, str]]:
    """诊断包里要整体换掉的项目根：(文本里的写法, `<project:哈希>`)，长的在前。

    **项目路径是用户最私人的那段**：云盘目录名带邮箱、课题目录带人名和论文题目，而
    `_redact_text` 原本只把主目录换成 `~`，`~/Library/CloudStorage/坚果云-<邮箱>/…/<人名>/…`
    原样出门（2026-09-23 beta 诊断包实测）。当前项目 + 最近打开过的项目都换成同一个哈希记号：
    读包的人仍能对上「这几行说的是同一个项目」，项目叫什么、在哪一个字都不知道。
    """
    paths: list[str] = []
    if project and isinstance(project.get("figures_dir"), str):
        paths.append(project["figures_dir"])
    try:
        paths += [e["path"] for e in config.load().get("recent_projects", [])]
    except Exception:  # noqa: BLE001 — 配置读不出来不该拖垮诊断
        pass
    out: list[tuple[str, str]] = []
    for path in dict.fromkeys(p for p in paths if p and os.path.isabs(p)):
        token = f"<project:{_digest(os.path.normcase(os.path.realpath(path)))}>"
        out += [(v, token) for v in _root_variants(path)]
    return sorted(out, key=lambda pair: len(pair[0]), reverse=True)


def _only_openers(text: str) -> bool:
    """全由开括号 / 开引号组成（含空串）：`@` 前只有这些时，`@` 前没有账号。"""
    return all(c in "\"'`" or unicodedata.category(c) in ("Ps", "Pi") for c in text)


def _not_an_email(local: str, domain: str) -> bool:
    """负面清单：结构上确定不是地址的已知格式。`local` / `domain` 是这个 `@` 两侧、到 token 边界
    （或同一 token 里相邻的 `@`）为止的全部字符。每条的理由：

    * `@` 前没有账号——`@app.route`、`@dataclass`、`(@某人`：装饰器与提及；
    * 域名不到两段——`user@localhost`、`HEAD@{0}`、`a@b`、`x@例子`：邮件地址的域名至少两段；
    * 域名是版本号——`numpy@1.26.4`、`pkg@2.0.0-beta`、`jsdom@30.0.1/lib/x.js`：包管理器的
      「包@版本」写法（只看第一个 `/` 之前、去掉句读之后是不是版本号，只认 ASCII 数字）。

    URL 里的 userinfo（`ssh://git@github.com/…`）不在清单上：整个 token 照样抹。
    判断前先解开 `\\uXXXX`（`json.dumps` 把全角句点写成 `\\u3002`，不解开就数不出两段）。
    """
    local, domain = (
        _JSON_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), x) for x in (local, domain)
    )
    if _only_openers(local):
        return True
    labels = re.split(f"[{_EMAIL_DOTS}]", domain.rstrip(_EMAIL_DOTS))
    if len(labels) < 2 or not all(labels):
        return True
    core = re.split(r"[/\\]", domain, maxsplit=1)[0].rstrip(_EMAIL_DOTS + ",;:)]}>\"'")
    return _VERSION_AFTER_AT.fullmatch(core) is not None


def _redact_email_tokens(text: str) -> str:
    """含 `@` 的 token 整个换成 `<email>`（判据见 `_EMAIL_AT` 上方）。token 按空白切。"""
    out: list[str] = []
    done = 0  # 已经交出去的位置
    for at in _EMAIL_AT.finditer(text):
        if at.start() < done:
            continue
        start = at.start()
        while start > done and not text[start - 1].isspace():
            start -= 1
        end = at.end()
        while end < len(text) and not text[end].isspace():
            end += 1
        # 引号括起来的本地部分里有空白：`@` 前引号是奇数个，就并到同一行上一个引号所在的 token
        if text.count('"', start, at.start()) % 2 == 1:
            quote = text.rfind('"', done, start)
            if quote >= 0 and "\n" not in text[quote:start]:
                start = quote
                while start > done and not text[start - 1].isspace():
                    start -= 1
        token = text[start:end]
        ats = [m.span() for m in _EMAIL_AT.finditer(token)]
        edges = [0] + [e for _, e in ats[:-1]]
        nexts = [s for s, _ in ats[1:]] + [len(token)]
        if all(
            _not_an_email(token[lo:s], token[e:hi])
            for (s, e), lo, hi in zip(ats, edges, nexts, strict=True)
        ):
            continue
        out += [text[done:start], "<email>"]
        done = end
    out.append(text[done:])
    return "".join(out)


def _redact_json_line(line: str) -> str | None:
    """一行整个是 JSON 时：只在字符串字面量的内容里抹，结构原样。不是 JSON 就 None。"""
    head = line.lstrip()[:1]
    if head not in ("{", "[", '"'):
        return None
    try:
        json.loads(line)
    except ValueError:
        return None
    return _JSON_STRING.sub(lambda m: f'"{_redact_email_tokens(m.group()[1:-1])}"', line)


def _redact_emails(text: str) -> str:
    if not _EMAIL_AT.search(text):
        return text
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        redacted = _redact_json_line(body)
        if redacted is None:
            redacted = _redact_email_tokens(body)
        out.append(redacted + line[len(body) :])
    return "".join(out)


def _redact_text(text: str, roots: list[tuple[str, str]] | None = None) -> str:
    """文本脱敏：密钥 → 假名标识 → 项目根 → 主目录 → 用户名 → 云盘账号 → 邮箱。

    **项目根必须在主目录之前换**：先换主目录的话，项目根的原文就不在文本里了，
    `~/…/<人名>/paper` 再也认不出来。"""
    text = _SECRET_VALUE.sub("***", text)
    # 按**值**再抹一次假名标识：按键名那道只挡得住结构化的
    # `"install_id": "..."`，挡不住它偶然出现在别的字符串里。
    ident = _install_id()
    if ident:
        text = text.replace(ident, "***")
    for raw, token in roots or ():
        text = text.replace(raw, token)
    home = os.path.expanduser("~")
    if home and home != os.sep:
        text = text.replace(home, "~")
        # Windows 上日志里可能混着两种分隔符写法
        text = text.replace(home.replace("\\", "/"), "~")
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    if len(user) >= 3:  # 太短的用户名replace 会误伤正常词
        text = re.sub(rf"\b{re.escape(user)}\b", "<user>", text)
    text = _CLOUD_ACCOUNT.sub(
        lambda m: f"{m.group(1)}{m.group(2)}-acct:{_digest(m.group(3))}", text
    )
    return _redact_emails(text)


def redact_text(text: str) -> str:
    """文本脱敏的**对外名字**（`engine/deprepair.py` 的安装日志走它）。

    刻意不让别的模块各写一份「抹掉主目录名」：那种规则一旦有两份，其中一份
    迟早漏掉某一条（Windows 的两种分隔符写法就是这么漏过的）。安装日志里
    pip 特有的那条（index 地址可能带凭据）归 deprepair，其余都在这里。
    """
    return _redact_text(text)


def _redact_obj(obj, roots: list[tuple[str, str]] | None = None):
    """结构化数据脱敏：按键名判定的敏感字段整体换掉，其余走文本规则（`roots` 同 `_redact_text`）。"""
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
                out[k] = _redact_obj(v, roots)
        return out
    if isinstance(obj, list):
        return [_redact_obj(v, roots) for v in obj]
    if isinstance(obj, str):
        return _redact_text(obj, roots)
    return obj


#: 路径出门时原样保留的段：Tavotto 自己起的目录名、解释器布局、系统的通用目录。其余一律 `seg:<哈希>`
_KNOWN_SEGMENTS = frozenset(
    {
        config.PROJECT_STORE_DIRNAME,
        "export",
        "exports",
        "canvases",
        "layouts",
        "original_backups",
        "cache",
        "tavotto",
        ".venv",
        "venv",
        "env",
        "bin",
        "Scripts",
        "lib",
        "site-packages",
        "python",
        "python3",
        "python.exe",
        "Users",
        "home",
        "Volumes",
        "Library",
        "CloudStorage",
        "Mobile Documents",
        "Documents",
        "Desktop",
        "Downloads",
        "tmp",
        "private",
        "var",
        "opt",
        "Applications",
    }
)
#: 「项目在云盘同步目录里」：同步冲突 / 按需下载的占位文件是一类常见故障，值得留一个布尔
_CLOUD_HINT = re.compile(
    r"CloudStorage|Mobile Documents|OneDrive|Dropbox|Google Drive|Nutstore|坚果云|iCloud", re.I
)


def _path_fact(value: str, roots: list[tuple[str, str]]) -> str:
    """一条路径出门的样子：项目根 → `<project:…>`、主目录 → `~`，之后的每一段只有 `_KNOWN_SEGMENTS`
    里的名字原样，其余 `seg:<sha1 前 10 位>`——导出目录可以被用户改到 `~/Desktop/<论文题目>/`，只换主目录
    挡不住题目。"""
    text = _redact_text(value, roots)
    out: list[str] = []
    for seg in re.split(r"[/\\]", text):
        keep = (
            seg in ("", "~", "<user>")
            or seg in _KNOWN_SEGMENTS
            or seg.startswith(("<project:", "seg:"))
            or "-acct:" in seg
            or re.fullmatch(r"[A-Za-z]:", seg) is not None
        )
        out.append(seg if keep else f"seg:{_digest(seg)}")
    return "/".join(out)


def _paths_in(obj, roots: list[tuple[str, str]]):
    """结构里凡是长得像绝对路径（或 `~` 开头）的字符串都走 `_path_fact`；其余原样交给后面的 `_redact_obj`。"""
    if isinstance(obj, dict):
        return {k: _paths_in(v, roots) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_paths_in(v, roots) for v in obj]
    if isinstance(obj, str) and (os.path.isabs(obj) or obj.startswith("~")):
        return _path_fact(obj, roots)
    return obj


def _project_section(project: dict | None, roots: list[tuple[str, str]]) -> dict:
    """report.json 的 project 段：**项目在哪、叫什么不出门**，对排障有用的事实留下。

    * `name`（项目目录名，常常就是论文题目 / 人名）去掉；
    * `figures_dir` 只剩 `<project:哈希>`，另给 `location`：在不在云盘同步目录、有没有非 ASCII
      字符、有没有空格——这三样是真实故障的来源（同步占位文件、编码、命令行引号），名字本身不是；
    * 导出 / 备份 / 文档目录与项目设置里的路径走 `_path_fact`。
    """
    if not project or not project.get("open"):
        return project or {"open": False}
    out = dict(project)
    out.pop("name", None)
    fig = out.get("figures_dir")
    if isinstance(fig, str):
        out["location"] = {
            "cloud_storage": bool(_CLOUD_HINT.search(fig)),
            "non_ascii": not fig.isascii(),
            "has_space": " " in fig,
        }
    for key in ("figures_dir", "export_dir", "backup_dir", "document_dir"):
        if isinstance(out.get(key), str):
            out[key] = _path_fact(out[key], roots)
    if isinstance(out.get("settings"), dict):
        out["settings"] = _paths_in(out["settings"], roots)
    return out


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
    return _shorten_path_text(match.group(0))


def _shorten_path_text(raw: str) -> str:
    """一条路径（或帧里的任何「文件名」）→ 只留能定位的那一截。

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

    帧里的文件名不一定是绝对路径（评审 #443 第十轮）：`exec(compile(src, "patient_123.py",
    "exec"))` 打出来的帧是相对名，`<string>` / `<frozen runpy>` 是虚拟名——都是用户能起的
    字符串，与绝对路径同一条规则（`<frozen runpy>` 也哈希：CPython 的伪文件名可枚举，
    读的人对得上）。
    """
    parts = [seg for seg in re.split(r"[\\/]+", raw) if seg]
    if not parts:
        return raw
    lowered = [seg.lower() for seg in parts]
    name = parts[-1]
    stem, dot, ext = name.rpartition(".")
    # 扩展名也是用户起的字符串（`data.patient`，评审 #443 第十二轮）：只带闭集里的那些
    suffix = f".{ext.lower()}" if dot and stem and ext.lower() in _KNOWN_EXTENSIONS else ""
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


#: 哈希后的文件名还带着的扩展名——闭集：Python 源码 / 扩展模块 / 常见的图与数据格式。
#: 不在表里的（`.patient`）一个字不带，不看长度。
_KNOWN_EXTENSIONS = frozenset(
    {
        "py", "pyc", "pyi", "pyx", "pyw", "so", "pyd", "dll", "dylib",
        "png", "pdf", "svg", "eps", "ps", "tif", "tiff", "jpg", "jpeg", "gif", "bmp", "webp",
        "csv", "tsv", "txt", "json", "yaml", "yml", "toml", "ini", "cfg", "log", "md", "dat",
        "npy", "npz", "pkl", "pickle", "h5", "hdf5", "parquet", "feather", "xlsx", "xls", "mat",
        "zip", "gz", "tar", "bz2", "xz", "fasta", "fa", "fastq", "gff", "gtf", "bed", "vcf", "bam",
    }
)  # fmt: skip
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
#: 加载器文案里**允许原样出门**的模块名：标准库（CPython 自己的闭集）与已知第三方包的
#: **顶层名**；点分名的余下部分与不认识的名字一律 `mod:<sha1 前 10 位>`（`numpy.mod:…` /
#: `mod:…`）——`raise ModuleNotFoundError("No module named 'patient_123'")` 再 `print_exc()`
#: 形状与加载器一模一样，名字却是用户的（评审 #443 第十二轮）；真缺的私有包名同样可能是
#: 项目术语。与路径、异常类型名同一条规则：出门的每一段要么是闭集成员要么是哈希。
_STDLIB_MODULES = frozenset(getattr(sys, "stdlib_module_names", ()))


def _module_name_for_export(name: str) -> str:
    top, dot, rest = name.partition(".")
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    if top in _STDLIB_MODULES or top in _KNOWN_SITE_PACKAGES:
        return f"{top}.mod:{digest}" if dot else top
    return "mod:" + digest


def _loader_message_for_export(m: re.Match) -> str:
    """命中的加载器文案 → 形状原样、里面的名字按闭集放行或哈希。"""
    text = m.group(0)
    # 从后往前换，前面的位置不漂
    for key, value in sorted(m.groupdict().items(), key=lambda kv: -m.start(kv[0])):
        if value is None:
            continue
        start, end = m.span(key)
        text = text[:start] + _module_name_for_export(value) + text[end:]
    return text


def _frame_for_export(line: str) -> str:
    """帧行 → `  File "<缩写路径>", line N`：函数名不带（`in analyze_patient_123` 是用户的
    标识符，评审 #443 第七轮），faulthandler 的 `line N in f` 与 traceback 的
    `line N, in f` 归成一种写法。"""
    m = _TB_FRAME.match(line)
    assert m is not None  # 调用方已经用同一个正则筛过
    # 直接对文件名做缩写，不经 `shorten_paths`：那条只认绝对路径，相对名 / 虚拟名
    # （`patient_123.py`、`<string>`）会原样漏出去（评审 #443 第十轮）
    path = _shorten_path_text(m.group("path")) if m.group("path") else ""
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


#: 收尾行里**允许原样出门**的异常类型名：只有本进程 `builtins` 里的异常类（闭集——traceback
#: 打印 builtins 与 `__main__` 里定义的类都不带模块前缀，`Patient_123Error` 与 `KeyError`
#: 长得一样，只有查表分得开）。点分名只留来自 `_KNOWN_SITE_PACKAGES` 的包名，其余
#: `exc:<sha1 前 10 位>`：用户 `class Patient_123Error(Exception)` 再 `traceback.print_exc()`，
#: 类型名就是用户源码里的标识符（评审 #443 第九、十一轮）。
_BUILTIN_EXCEPTIONS = frozenset(
    name
    for name, obj in vars(builtins).items()
    if isinstance(obj, type) and issubclass(obj, BaseException)
)


def _exception_type_for_export(head: str) -> str:
    """异常类型名 → 进包的形态：builtins 原样；点分名只留来自闭集的**包名**，其余哈希
    （`matplotlib.units.ConversionError` → `matplotlib.exc:…`）——`__module__` 是用户能改的
    （`Patient_123Error.__module__ = "numpy"` 打出来就是 `numpy.Patient_123Error`，评审 #443
    第十一轮），包名之后的每一段都当用户起的名字看。库的异常类可枚举，读的人对得上。"""
    name = head.strip()
    if name in _BUILTIN_EXCEPTIONS:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    pkg, dot, _rest = name.partition(".")
    if dot and pkg in _KNOWN_SITE_PACKAGES:
        return f"{pkg}.exc:{digest}"
    return "exc:" + digest


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
                return f"{exc_type}: {_loader_message_for_export(m)}"
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
            mtime = p.stat().st_mtime
            # 这个目录最近一次 spawn 的时刻也算：worker 一个字没写就死时 worker.log 的
            # mtime 不会动，只按它排会漏掉刚失败的那一份（评审 #443 第十四轮）
            try:
                mtime = max(mtime, (p.parent / pool.LOG_GENERATION_FILE).stat().st_mtime)
            except OSError:
                pass
            stamped.append((mtime, p))
        except OSError:
            continue
    stamped.sort(reverse=True)
    out: list[dict] = []
    for mtime, p in stamped[:files]:
        try:
            # 只看**这一代**：worker.log 是跨代追加的，`pool.start_log_generation` 在 spawn
            # 前把起点落在旁边；不带这个边界会把上一代的 traceback 当成这一代的
            text = _read_tail_bytes(
                p, WORKER_LOG_SCAN_BYTES, start=pool.log_generation_start(p)
            ).decode("utf-8", errors="replace")
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


def _read_tail_bytes(path: Path, limit: int, *, start: int = 0) -> bytes:
    """`start` 之后、最多最后 `limit` 字节——**seek 过去再读**，不是整个读进来再切（评审
    #443 第十一轮）：一份被脚本刷了几个小时的 worker.log 能有几百 MB，`read_bytes()[-limit:]`
    会先把整个文件装进 Flask 进程的内存，「扫描上限」就成了一句空话。`start` 是这一代的
    起点（第十四轮）：之前的字节属于上一代，一个都不读。"""
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        begin = max(start, size - limit, 0)
        fh.seek(begin)
        return fh.read(max(0, size - begin))


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
    roots = project_roots(project)
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
        "project": _project_section(project, roots),
        "recent_errors": errors,
    }
    return _redact_obj(report, roots)


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
    roots = project_roots(project)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.json", json.dumps(report, ensure_ascii=False, indent=1))
        z.writestr("app.log", _redact_text("\n".join(_log_tail()), roots))
        try:
            cfg = json.loads(config.config_path().read_text(encoding="utf-8"))
            z.writestr(
                "config.json", json.dumps(_redact_obj(cfg, roots), ensure_ascii=False, indent=1)
            )
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
        z.writestr(
            "README.txt",
            _readme(snapshot is not None, bool(trace), list(report.get("project") or {})),
        )
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


def _readme(has_state: bool, has_trace: bool, project_keys: list[str] | None = None) -> str:
    """包里有什么、**没有什么**。双语——用户得看得懂自己在往 issue 上贴什么。

    「不含」那一段是承诺，不是免责声明：它对应的是代码里的字段 allowlist
    与服务端校验（ADR 0016 §4 / §8），不是「我们尽量不放」。

    project 段带了哪些字段**不手写**：`project_keys` 是这一份 report.json 里实际写出的键，
    手写的清单在 `project_status` / `_diagnostics_project_status` 加字段那天就漂了（#536 评审）。
    """
    keys = ", ".join(project_keys or ["open"])
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
        "- 项目名称与项目所在位置（统一换成 <project:哈希>）、邮箱、云盘账号\n"
        "\n"
        "- text drawn inside your figures (titles, axis labels, legends, annotations)\n"
        "- Python source code or scripts\n"
        "- raw datasets or data arrays\n"
        "- SVG / PDF / PNG image content\n"
        "- API keys or tokens\n"
        "- full local file paths, usernames, or home directories\n"
        "- project names and where they live (replaced by <project:hash>), email addresses,\n"
        "  cloud-storage account names\n"
        "\n"
        "当前打开的项目叫什么、在哪，在 report.json 里只以 <project:哈希> 记号和 location 的\n"
        "三个是 / 否（在不在云盘同步目录、路径有没有非 ASCII 字符、有没有空格）出现；\n"
        "其余目录按段换成哈希，其余项目只留条数。\n"
        "The open project's name and location appear in report.json only as <project:hash>\n"
        "and three yes/no facts under location (cloud-sync folder, non-ASCII path, spaces);\n"
        "other directories are hashed segment by segment, other projects are only counted.\n"
        "这一份 report.json 的 project 段含这些字段 / Fields in this report's project section:\n"
        f"  {keys}\n"
        "\n"
        "文件名、路径与图内文字在诊断包里一律换成不可逆的短哈希（doc:… / "
        "panel:… / file:… / var:…），\n"
        "只用来判断「两个状态是不是同一个」，反推不回原值。\n"
        "Identifiers are replaced with irreversible short hashes; they only tell\n"
        "whether two states are the same, and cannot be reversed.\n"
        "\n"
        "发出去之前仍建议自己扫一眼。/ You may still want to skim it before sharing.\n"
    )
