"""诊断包与安装日志共用的文本脱敏原语（纯标准库）。

只依赖配置，不探测解释器、不引用 bootstrap / diagnostics / telemetry。
脱敏规则唯一出处在这里；diagnostics 保留原有导出给其余调用方。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata

from . import config

# 形如 sk-…、ghp_…、长十六进制串等，宁可多抹一点
_SECRET_VALUE = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{10,}|[A-Fa-f0-9]{32,})\b")


def _install_id() -> str:
    """本机的匿名遥测标识（没同意过就是空串）。只用来把它从输出里抹掉。"""
    try:
        # 只读现有标识，不 import telemetry（它反向依赖 diagnostics），也绝不生成标识。
        ident = (config.load().get("telemetry") or {}).get("install_id")
        return ident if isinstance(ident, str) else ""
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


def _only_openers(text: str) -> bool:
    """全由开括号组成（含空串）：`@` 前只有这些时，`@` 前没有账号。

    **引号不算**（#540）：`'@example.com` / `` `@example.com `` / `“@x.com` 里那一个引号就是
    合法的本地部分（RFC 5321 的 atext 含 `'` 与 `` ` ``，SMTPUTF8 放开了其余 Unicode）。代价是
    `"@app.route"` 这种引号里的装饰器也被抹——宁可多抹。"""
    return all(unicodedata.category(c) == "Ps" for c in text)


def _not_an_email(local: str, domain: str) -> bool:
    """负面清单：结构上确定不是地址的已知格式。`local` / `domain` 是这个 `@` 两侧、到 token 边界
    （或同一 token 里相邻的 `@`）为止的全部字符。每条的理由：

    * `@` 前没有账号——`@app.route`、`@dataclass`、`(@某人`：装饰器与提及（只认开括号，引号不算）；
    * 域名不到两段——`user@localhost`、`HEAD@{0}`、`a@b`、`x@例子`：邮件地址的域名至少两段；
    * 域名是版本号——`numpy@1.26.4`、`pkg@2.0.0-beta`、`jsdom@30.0.1/lib/x.js`：包管理器的
      「包@版本」写法（只看第一个 `/` 之前、去掉句读之后是不是版本号，只认 ASCII 数字）。

    域名是方括号里的地址字面量（`user@[IPv6:2001:db8::1]`、`user@[192.0.2.1]`，#540）不算「不到
    两段」：`[` 开头的域名一律按地址抹，先于数段数。
    URL 里的 userinfo（`ssh://git@github.com/…`）不在清单上：整个 token 照样抹。
    判断前先解开 `\\uXXXX`（`json.dumps` 把全角句点写成 `\\u3002`，不解开就数不出两段）。
    """
    local, domain = (
        _JSON_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), x) for x in (local, domain)
    )
    if _only_openers(local):
        return True
    if domain.startswith("["):
        return False
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


def redact_text(text: str, roots: list[tuple[str, str]] | None = None) -> str:
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
