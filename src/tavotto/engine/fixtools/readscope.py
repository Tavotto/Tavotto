"""读取范围：「看」组工具能读哪里（ADR 0149 §三）。判据只有这一处。

允许：用户主目录下任意路径 + 项目根 + 当前项目所用 Python 环境的前缀（可能在主目录外，如 /opt、/usr/local 的 conda / venv）。
其余（系统目录等）拒绝。硬拦（`DENY`）压过允许：凭据目录 / 文件、私钥形状、`.env`、浏览器 profile、Tavotto 自己的密钥文件。

判定在**规范化 + 解析符号链接之后**做，且词法路径与解析后的路径都要过一遍（项目里一个叫 `notes.txt`
的符号链接指向 `~/.ssh/id_rsa` 解析后被拦；一个叫 `.env` 的链接词法上就被拦）。大小写不敏感于 macOS / Windows。
读到的内容过 `scrub()`（诊断包同一套脱敏 + 几种常见密钥形状）才交给调用方。
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

#: 单次读取上限（字节）。更大的给前 N 行 + 截断说明。
MAX_READ_BYTES = 256 * 1024
#: 判二进制的采样字节数
_SNIFF = 8192

#: 相对主目录的硬拦路径（目录整棵拦，文件只拦它自己）。
DENY_HOME_RELATIVE: tuple[str, ...] = (
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    ".docker/config.json",
    ".config/gcloud",
    "Library/Keychains",
    ".netrc",
    "_netrc",
    ".pypirc",
    ".git-credentials",
    ".codex/auth.json",
    ".claude/.credentials.json",
    # 常见开发工具的凭据存放处（Codex #1353：逐个补会一直漏，这里按「工具 → 默认凭据位置」整批列）
    ".cache/huggingface/token",
    ".cache/huggingface/stored_tokens",
    ".huggingface/token",
    ".npmrc",
    ".yarnrc",
    ".yarnrc.yml",
    ".cargo/credentials",
    ".cargo/credentials.toml",
    ".config/gh/hosts.yml",
    ".config/hub",
    ".kaggle/kaggle.json",
    ".config/openai",
    ".config/anthropic",
    ".wandb",
    ".config/wandb",
    ".databrickscfg",
    ".terraform.d/credentials.tfrc.json",
    ".config/rclone",
    ".s3cfg",
    ".boto",
    ".pgpass",
    ".my.cnf",
    ".jupyter/runtime",
    ".local/share/jupyter/runtime",
    "Library/Jupyter/runtime",
    ".config/configstore",
    # 浏览器 profile（Cookie / 登录态 / 保存的密码都在里面）
    "Library/Application Support/Google/Chrome",
    "Library/Application Support/Chromium",
    "Library/Application Support/Microsoft Edge",
    "Library/Application Support/BraveSoftware",
    "Library/Application Support/Firefox",
    "Library/Safari",
    "Library/Cookies",
    ".config/google-chrome",
    ".config/chromium",
    ".config/BraveSoftware",
    ".mozilla",
    "snap/firefox",
    "AppData/Local/Google/Chrome",
    "AppData/Local/Microsoft/Edge",
    "AppData/Local/BraveSoftware",
    "AppData/Roaming/Mozilla",
)

#: 任意位置、按**文件名**判的明确凭据文件形状（整名或固定后缀；Codex #1353 第九轮：不再用 `*token*` / `*secret*` 这类
#: 子串通配——`token_counts.csv`、`password_strength.py` 是普通文件）。**目录不按名拦**（`tokenized-data/` 可读、可当 find / grep 的根），
#: 目录只由 `DENY_HOME_RELATIVE` 的固定位置拦。内容里的密钥由 `scrub()` 抹，不靠文件名猜。
DENY_NAME_PATTERNS: tuple[str, ...] = (
    ".env*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.keystore",
    "*.jks",
    "*.kdbx",
    "id_*",
    "token",
    ".token",
    "*.token",
    "credentials",
    "credentials.json",
    "credentials.toml",
    "credentials.yml",
    "credentials.yaml",
    "*-credentials.json",
    "client_secret*.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "secrets.toml",
    "*.secret",
    "*.secrets",
    "service-account*.json",
    "password.txt",
    "passwords.txt",
)
#: `id_rsa.pub` 之类公钥不是秘密
_NAME_EXEMPT_SUFFIX = (".pub",)

REASON_DENIED = "denied"
REASON_OUTSIDE = "outside_scope"
REASON_BAD_PATH = "bad_path"


@dataclass(frozen=True)
class Resolved:
    ok: bool
    real: str = ""  # 解析后的绝对路径（ok 时）
    reason: str = ""  # REASON_*（不 ok 时）
    in_project: bool = False


def _tavotto_secret_paths() -> list[Path]:
    """Tavotto 自己存密钥的地方：`config.json`（AI 服务商的 api_key 明文在里面）。"""
    try:
        from .. import config

        return [config.config_path()]
    except Exception:  # noqa: BLE001 - 取不到就退回只拦通用的
        return []


def scrub(text: str) -> str:
    """给模型看的内容一律先过这里：诊断包同一套文本脱敏 + 常见密钥形状。幂等。"""
    from .. import diagnostics

    try:
        text = diagnostics.redact_text(text)
    except Exception:  # noqa: BLE001 - 脱敏不该被它拖垮，退到下面的形状规则
        pass
    text = _PRIVATE_KEY_BLOCK.sub("[redacted private key]", text)
    text = _HTTP_CRED.sub(_mask_http_cred, text)
    text = _SECRET_ASSIGN.sub(_mask_secret, text)
    text = _BEARER.sub("Bearer ***", text)
    text = _TOKEN_SHAPES.sub("***", text)
    text = _URL_USERINFO.sub(r"\g<pre>***@", text)
    return _HIGH_ENTROPY.sub(_mask_entropy, text)


#: HTTP 凭据头：整行（冒号后到行尾；JSON 形则是那一个字符串值；`curl -H '…'` 则到包着它的引号）换成 `***`。
#: 值里常有空格（`Basic xxx` / `Digest a="b", c="d"` / `a=b; c=d`），所以不能走「键 + 单个裸值」的规则。
#: 名字前须是行首 / 空白 / 引号 / `{` / `,`（`author:`、`x-cookie` 之类不命中）；`=` 写法只认 authorization。
_HTTP_CRED_NAMES = r"(?:proxy-)?authorization|set-cookie|cookie|x-api-key|x-auth-token"
_HTTP_CRED = re.compile(
    rf"(?im)(?P<lead>^|[\s{{,\"'])(?P<name>{_HTTP_CRED_NAMES})(?P<cq>[\"']?)"
    rf"(?P<sep>[ \t]*[:=][ \t]*)(?P<val>[^\n]*)"
)


def _mask_http_cred(m: re.Match) -> str:
    name, sep, val, lead = m.group("name"), m.group("sep"), m.group("val"), m.group("lead")
    if "=" in sep and not name.lower().endswith("authorization"):
        return m.group(0)
    head = m.group("lead") + name + m.group("cq") + sep
    if m.group("cq"):  # JSON / 字典形：`"Authorization": "Basic x\"y"`
        for pat in (_DQ, _SQ):
            q = re.match(pat, val)
            if q:
                return head + val[0] + "***" + val[0] + val[q.end() :]
        return head + "***"
    if lead in ("'", '"'):  # `curl -H 'Authorization: …'`：到包着它的引号为止
        end = re.search(rf"(?<!\\){lead}", val)
        if end:
            return head + "***" + val[end.start() :]
    return head + "***"


_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S
)
#: 敏感键名表（一张）：`scrub` 的「键 + 值」类规则全由它驱动。大小写不敏感，`-` / `_` 可互换（`api-key` = `api_key`）。
#: `auth` 单独收紧（`auth` / `auth_token` / `auth-key`），不然 `author: ...` 会被当成密钥。
SENSITIVE_KEYS: tuple[str, ...] = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api-key",
    "apikey",
    "access-key",
    "credential",
    "credentials",
    "private-key",
    "authorization",
)
_KEY_ALT = "|".join(re.escape(k).replace(r"\-", "[_-]") for k in SENSITIVE_KEYS)
_KEY = rf"(?:(?:{_KEY_ALT})[\w-]*|auth(?:[_-](?:token|key))?(?![A-Za-z0-9]))"
#: **后缀族**（Codex #1353 第九轮：不再逐个列名）：键名（驼峰 / 下划线 / 连字符 / 点分，大小写不敏感）**以**这些词结尾就是凭据键，
#: `AccountKey` / `SharedAccessKey` / `clientSecret` / `aws_secret_access_key` / `x-ms-signature` / `sas_sig` 一网打尽。
#: 紧跟其后必须是引号 / 空白 / `=` `:`（规则本身要求），所以 `keyboard`、`signal` 不会命中；以 `key` 结尾的常见非凭据走 `_BENIGN_KEY`。
_SUFFIX_KEY = r"(?:key|tokens?|secrets?|passwords?|passwd|pwd|credentials?|auth|signature|sig)"


#: 引号值：认反斜杠转义（`"abc\"def"` 是一整个值，不在转义的引号处截断）
_DQ = r'"(?:\\.|[^"\\\n])*"'
_SQ = r"'(?:\\.|[^'\\\n])*'"


def _value(sfx: str, min_bare: int) -> str:
    """值的三种形状（组名带后缀 `sfx` 以便同一正则里出现两次）：双引号 / 单引号（可含空格、任意长度）/ 裸值。"""
    return (
        rf"(?:(?P<dq{sfx}>{_DQ})|(?P<sq{sfx}>{_SQ})"
        rf"|(?!\*\*\*)(?P<bare{sfx}>[^\s\"',;()\[\]{{}}<>]{{{min_bare},}}))"
    )


#: 一条规则、两种「键的写法」，值形状共用：
#:  * `cli`：命令行旗标 `--password x` / `-token=x` / `--api-key "a b"`（旗标后空白或 `=`）；整个值（含引号）换成 `***`，只留旗标。
#:    另有短旗标 `-p 'x'`（`cliP`）：`-p` 常是端口 / 路径，所以**只在值带引号时**当密码处理。
#:  * `kv`：`password = x` / `"password": "x"` / `token: x`（`=` 或 `:`）；引号保留、内容换成 `***`；裸值**不论长短**都抹（键名已说明是凭据）。
#:    键名从**词首**起匹配（`(?<![\w.-])`，前缀最多 400 字符）：超长单词串上每个词只试一次，不会二次方扫描（读文件的超长单行）。
#:    计数类键名（`max_tokens` / `num_tokens` / `token_count` / `tokenizer` …，见 `_BENIGN_KEY`）不是凭据，原样保留。
_SECRET_ASSIGN = re.compile(
    rf"(?i)(?:(?P<cli>(?<![\w-])-{{1,2}}(?P<cname>{_KEY}|[\w-]{{0,80}}?{_SUFFIX_KEY})(?:\s+|\s*=\s*)){_value('C', 1)}"
    rf"|(?P<cliP>(?<![\w-])-p\s+)(?:{_DQ}|{_SQ})"
    rf"|(?P<kv>(?P<kname>(?<![\w.-])[\w.-]{{0,400}}?(?:{_KEY}|{_SUFFIX_KEY}))[\"']?\s*[:=]\s*){_value('K', 1)})"
)


#: 含 token 但不是凭据的计数类键名：上下限 / 计数 / 用量 / 分词器（`max_tokens = 4096` 不该被抹）
_BENIGN_KEY = re.compile(
    # 整个键名（可带 `a.b.` 命名空间前缀）**完全**是计数 / 分词器字段才豁免；`tokenizer_api_token` 之类一律照抹
    r"(?i)(?:[\w-]+\.)*(?:"
    r"(?:max|min|num|n|total|count|limit|budget|used|prompt|completion|input|output)[_-]?tokens?"
    r"|tokens?[_-](?:count|limit|budget|used|usage)"
    r"|tokeni[sz]er(?:[_-](?:name|path|type))?"
    # 以 key 结尾但不是凭据：数据库 / 缓存 / 排序用的键，和几个碰巧以 key 结尾的英文词（整键匹配）
    r"|(?:primary|foreign|sort|cache|partition|hot|unique|group|join|lookup|index|row|map|dict|shortcut|"
    r"composite|natural|surrogate|order|range|hash|shard|s3[_-]?object)[_-]?keys?"
    r"|monkey|turkey|hockey|donkey|whiskey|jockey|lackey|keyboard\w*"
    r")"
)


_LITERAL_NON_SECRETS = frozenset({"null", "none", "true", "false", "nil", "undefined"})


def _mask_secret(m: re.Match) -> str:
    if m.group("cli") is not None:
        if _BENIGN_KEY.fullmatch(m.group("cname") or ""):
            return m.group(0)
        return m.group("cli") + "***"
    if m.group("cliP") is not None:
        return m.group("cliP") + "***"
    head = m.group("kv")
    kname = m.group("kname") or ""
    if _BENIGN_KEY.fullmatch(kname):
        return m.group(0)
    # 光秃秃的 `key`：代码里 `sorted(x, key=len)` / `key=lambda …` 远多于真密钥，只在值是引号字符串时才当凭据
    if kname.lower().rsplit(".", 1)[-1] == "key" and m.group("bareK") is not None:
        return m.group(0)
    if m.group("dqK") is not None:
        return head + '"***"'
    if m.group("sqK") is not None:
        return head + "'***'"
    if (m.group("bareK") or "").lower() in _LITERAL_NON_SECRETS:
        return m.group(
            0
        )  # `"password": null` / `token=None`：空值不是凭据，原样保留（结构也不被破坏）
    return head + "***"


_BEARER = re.compile(r"(?i)\bBearer\s+(?!\*\*\*)[A-Za-z0-9._~+/=-]{12,}")
_TOKEN_SHAPES = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-]{8,}|gh[pousr]_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_\-]{30,}|hf_[A-Za-z0-9]{20,}|npm_[A-Za-z0-9]{20,}"
    r"|glpat-[A-Za-z0-9_\-]{20,}|pypi-[A-Za-z0-9_\-]{20,}|sk_live_[A-Za-z0-9]{16,}|rk_live_[A-Za-z0-9]{16,}"
    r"|SG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}|ya29\.[A-Za-z0-9_\-]{20,}"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})\b"
)


class ReadScope:
    """一次修复会话的读取范围。构造时把所有根与硬拦路径解析成真实路径，之后 `check` 纯比较。"""

    def __init__(
        self,
        *,
        home: str | os.PathLike,
        project_root: str | os.PathLike,
        env_prefixes: Iterable[str | os.PathLike] = (),
        extra_deny: Iterable[str | os.PathLike] | None = None,
        case_insensitive: bool | None = None,
    ) -> None:
        if case_insensitive is None:
            case_insensitive = os.name == "nt" or sys.platform == "darwin"
        self._ci = case_insensitive
        self._home_orig = os.path.realpath(os.fspath(home))
        self._proj_orig = os.path.realpath(os.fspath(project_root))
        self.home = self._real(home)
        self.project_root = self._real(project_root)
        self._roots = [self.home, self.project_root] + [self._real(p) for p in env_prefixes]
        secret = _tavotto_secret_paths() if extra_deny is None else [Path(p) for p in extra_deny]
        deny: list[str] = []
        for rel in DENY_HOME_RELATIVE:
            deny.append(self._real(Path(self.home, *rel.split("/"))))
        for p in secret:
            deny.append(self._real(p))
            deny.append(self._norm(os.path.abspath(p)))
        # 词法形式（home 本身可能是符号链接，两种写法都拦）
        raw_home = os.path.abspath(os.fspath(home))
        for rel in DENY_HOME_RELATIVE:
            deny.append(self._norm(os.path.join(raw_home, *rel.split("/"))))
        self._deny = sorted(set(deny))

    # ---- 规范化 -----------------------------------------------------------
    def _norm(self, p: str) -> str:
        p = os.path.normpath(p)
        return p.casefold() if self._ci else p

    def _real(self, p: str | os.PathLike) -> str:
        return self._norm(os.path.realpath(os.fspath(p)))

    def _under(self, path: str, root: str) -> bool:
        return path == root or path.startswith(root.rstrip(os.sep) + os.sep)

    def _name_denied(self, path: str) -> bool:
        name = os.path.basename(path)
        if self._ci:
            name = name.casefold()
        if name.endswith(_NAME_EXEMPT_SUFFIX):
            return False
        return any(fnmatch.fnmatchcase(name, pat) for pat in DENY_NAME_PATTERNS)

    def _path_denied(self, norm_path: str, *, is_dir: bool = False) -> bool:
        """固定位置表整棵拦；文件名形状只对**文件**生效（目录不按名拦）。"""
        if any(self._under(norm_path, d) for d in self._deny):
            return True
        return not is_dir and self._name_denied(norm_path)

    # ---- 对外 -------------------------------------------------------------
    def expand(self, raw: object) -> str | None:
        """模型给的路径 → 绝对词法路径。`~` 指会话主目录；相对路径相对项目根。"""
        if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
            return None
        raw = raw.strip()
        if raw == "~" or raw.startswith(("~/", "~\\")):
            raw = os.path.join(self._home_orig, raw[2:])
        elif not os.path.isabs(raw):
            raw = os.path.join(self._proj_orig, raw)
        return os.path.abspath(raw)

    def check(self, raw: object) -> Resolved:
        lexical = self.expand(raw)
        if lexical is None:
            return Resolved(False, reason=REASON_BAD_PATH)
        is_dir = os.path.isdir(lexical)
        if self._path_denied(self._norm(lexical), is_dir=is_dir):
            return Resolved(False, reason=REASON_DENIED)
        real_raw = os.path.realpath(lexical)
        real = self._norm(real_raw)
        if self._path_denied(real, is_dir=is_dir):
            return Resolved(False, reason=REASON_DENIED)
        if not any(self._under(real, r) for r in self._roots):
            return Resolved(False, reason=REASON_OUTSIDE)
        return Resolved(True, real=real_raw, in_project=self._under(real, self.project_root))

    def is_denied(self, real_path: str) -> bool:
        return self._path_denied(
            self._norm(os.path.realpath(real_path)), is_dir=os.path.isdir(real_path)
        )


# --------------------------------------------------------------------------- 读文件
@dataclass
class ReadOutcome:
    kind: str  # "text" | "binary" | "error"
    text: str = ""
    size: int = 0
    truncated: bool = False
    note: str = ""
    code: str = ""
    start_line: int = 1
    end_line: int = 0


#: 分块读取的块大小；任何一次分配都不超过 `cap + _CHUNK`
_CHUNK = 64 * 1024


def read_text_capped(
    real: str, *, start_line: int = 1, max_lines: int | None = None, cap: int = MAX_READ_BYTES
) -> ReadOutcome:
    """读 `real`（已过 `check`）。二进制只给类型与大小；超过 `cap` 的给前若干行 + 截断说明。

    **有界读取**：按 64 KiB 块读、自己切行，当前行只留前 `cap` 字节（其余丢弃），所以无论文件多大、有没有换行，
    内存里同时只有 O(cap + 块) 字节；单行超过 `cap` 就截断该行并在说明里讲清。"""
    try:
        if not os.path.isfile(real):
            return ReadOutcome("error", code="not_a_file")
        size = os.path.getsize(real)
        with open(real, "rb") as f:
            head = f.read(_SNIFF)
            if b"\x00" in head:
                return ReadOutcome("binary", size=size, note=_binary_kind(head, real))
            f.seek(0)
            lines: list[bytes] = []
            used = 0
            truncated = False
            cut_line = 0  # 被截断的那一行的行号（0 = 没有）
            lineno = 0
            cur = bytearray()
            clipped = False
            done = False

            def finish() -> bool:
                """一行收齐了；回 True = 该停了。"""
                nonlocal lineno, used, truncated, cut_line, clipped
                lineno += 1
                line, was_clipped = bytes(cur), clipped
                cur.clear()
                clipped = False
                if lineno < start_line:
                    return False
                if max_lines is not None and len(lines) >= max_lines:
                    truncated = True
                    return True
                if used + len(line) > cap or (was_clipped and lines):
                    truncated = True
                    if not lines:  # 单行就超上限：截断这一行
                        lines.append(line[:cap])
                        cut_line = lineno
                    return True
                lines.append(line)
                used += len(line)
                if was_clipped:  # 恰好填满 cap 的超长行：留下，但标明被截断
                    truncated = True
                    cut_line = lineno
                    return True
                return False

            while not done:
                chunk = f.read(_CHUNK)
                if not chunk:
                    if cur or clipped:
                        finish()
                    break
                pos = 0
                while pos < len(chunk):
                    nl = chunk.find(b"\n", pos)
                    stop = len(chunk) if nl < 0 else nl + 1
                    room = cap - len(cur)
                    if room > 0:
                        cur += chunk[pos : min(stop, pos + room)]
                    if stop - pos > max(room, 0):
                        clipped = True
                    pos = stop
                    if nl >= 0 and finish():
                        done = True
                        break
    except OSError:
        return ReadOutcome("error", code="read_failed")
    text = b"".join(lines).decode("utf-8", errors="replace")
    end = start_line + len(lines) - 1 if lines else start_line - 1
    note = ""
    if truncated:
        note = (
            f"[truncated: showing lines {start_line}-{end} of a {size}-byte file; "
            f"limit is {cap // 1024} KiB per read. Use start_line / max_lines to continue.]"
        )
        if cut_line:
            note += (
                f" [line {cut_line} is longer than {cap // 1024} KiB and was cut there; "
                "the rest of that line is not shown.]"
            )
    return ReadOutcome(
        "text",
        text=text,
        size=size,
        truncated=truncated,
        note=note,
        start_line=start_line,
        end_line=end,
    )


def _binary_kind(head: bytes, real: str) -> str:
    sigs = (
        (b"\x89PNG", "PNG image"),
        (b"\xff\xd8\xff", "JPEG image"),
        (b"GIF8", "GIF image"),
        (b"%PDF", "PDF document"),
        (b"PK\x03\x04", "ZIP archive (zip / whl / docx / xlsx)"),
        (b"\x7fELF", "ELF executable"),
        (b"MZ", "Windows executable"),
        (b"\xcf\xfa\xed\xfe", "Mach-O executable"),
        (b"SQLite format 3", "SQLite database"),
    )
    for sig, name in sigs:
        if head.startswith(sig):
            return name
    ext = os.path.splitext(real)[1].lower()
    return f"binary data ({ext or 'no extension'})"


#: `scheme://user:password@host`（数据库 / 代理 / 镜像 URL）：抹掉 password。各段都有长度上限，线性。
_URL_USERINFO = re.compile(
    r"(?P<pre>\b[A-Za-z][A-Za-z0-9+.-]{0,30}://[^\s:/@?#*]{0,100}:)(?!\*\*\*@)[^\s@/?#]{1,200}@"
)

#: 高熵兜底：长度 ≥ 32、字符集 `[A-Za-z0-9+/=_-]`、**同时含大写、小写、数字**的连续串。小写十六进制哈希 / UUID / 纯小写路径片段
#: 不命中（对诊断有用）；单个正则扫描、无回溯，线性。
_HIGH_ENTROPY = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{32,}")


def _mask_entropy(m: re.Match) -> str:
    tok = m.group(0)
    if not (
        any(c.isupper() for c in tok)
        and any(c.islower() for c in tok)
        and any(c.isdigit() for c in tok)
    ):
        return tok
    # 路径 / 标识符：被 `/` `_` `-` 切开后有 ≥3 个纯字母单词（`/Users/alice/Library/...`、`test_export_SVGRenderer_dpi300_ok`）
    # 不是密钥——真密钥（base64 / base64url）被分隔符切出纯字母单词的概率极低
    if sum(1 for seg in re.split(r"[_/-]", tok) if len(seg) >= 3 and seg.isalpha()) >= 3:
        return tok
    return "***"
