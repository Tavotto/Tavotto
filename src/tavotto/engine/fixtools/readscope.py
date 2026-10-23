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

#: 任意位置、按文件名判的私钥 / 环境文件形状。
DENY_NAME_PATTERNS: tuple[str, ...] = (".env", ".env.*", "*.pem", "*.key", "*.p12", "id_*")
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
    text = _SECRET_ASSIGN.sub(_mask_secret, text)
    text = _BEARER.sub("Bearer ***", text)
    return _TOKEN_SHAPES.sub("***", text)


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
)
_KEY_ALT = "|".join(re.escape(k).replace(r"\-", "[_-]") for k in SENSITIVE_KEYS)
_KEY = rf"(?:(?:{_KEY_ALT})[\w-]*|auth(?:[_-](?:token|key))?(?![A-Za-z0-9]))"


def _value(sfx: str, min_bare: int) -> str:
    """值的三种形状（组名带后缀 `sfx` 以便同一正则里出现两次）：双引号 / 单引号（可含空格、任意长度）/ 裸值。"""
    return (
        rf"(?:(?P<dq{sfx}>\"[^\"\n]*\")|(?P<sq{sfx}>'[^'\n]*')"
        rf"|(?!\*\*\*)(?P<bare{sfx}>[^\s\"',;]{{{min_bare},}}))"
    )


#: 一条规则、两种「键的写法」，值形状共用：
#:  * `cli`：命令行旗标 `--password x` / `-token=x` / `--api-key "a b"`（旗标后空白或 `=`）；整个值（含引号）换成 `***`，只留旗标。
#:    另有短旗标 `-p 'x'`（`cliP`）：`-p` 常是端口 / 路径，所以**只在值带引号时**当密码处理。
#:  * `kv`：`password = x` / `"password": "x"` / `token: x`（`=` 或 `:`）；引号保留、内容换成 `***`；裸值**不论长短**都抹（键名已说明是凭据）。
#:    计数类键名（`max_tokens` / `num_tokens` / `token_count` / `tokenizer` …，见 `_BENIGN_KEY`）不是凭据，原样保留。
_SECRET_ASSIGN = re.compile(
    rf"(?i)(?:(?P<cli>(?<![\w-])-{{1,2}}{_KEY}(?:\s+|\s*=\s*)){_value('C', 1)}"
    rf"|(?P<cliP>(?<![\w-])-p\s+)(?:\"[^\"\n]*\"|'[^'\n]*')"
    rf"|(?P<kv>(?P<kname>[\w.-]*?{_KEY})[\"']?\s*[:=]\s*){_value('K', 1)})"
)


#: 含 token 但不是凭据的计数类键名：上下限 / 计数 / 用量 / 分词器（`max_tokens = 4096` 不该被抹）
_BENIGN_KEY = re.compile(
    r"(?i)(?:^|[_.-])(?:max|min|num|n|total|count|limit|budget|used|prompt|completion|input|output)[_-]?tokens?$"
    r"|tokens?[_-](?:count|limit|budget|used|usage|per[_-]\w+)$|tokeni[sz]er"
)


def _mask_secret(m: re.Match) -> str:
    if m.group("cli") is not None:
        return m.group("cli") + "***"
    if m.group("cliP") is not None:
        return m.group("cliP") + "***"
    head = m.group("kv")
    if _BENIGN_KEY.search(m.group("kname") or ""):
        return m.group(0)
    if m.group("dqK") is not None:
        return head + '"***"'
    if m.group("sqK") is not None:
        return head + "'***'"
    return head + "***"


_BEARER = re.compile(r"(?i)\bBearer\s+(?!\*\*\*)[A-Za-z0-9._~+/=-]{12,}")
_TOKEN_SHAPES = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-]{8,}|gh[pousr]_[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|AIza[0-9A-Za-z_\-]{30,})\b"
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

    def _path_denied(self, norm_path: str) -> bool:
        return any(self._under(norm_path, d) for d in self._deny) or self._name_denied(norm_path)

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
        if self._path_denied(self._norm(lexical)):
            return Resolved(False, reason=REASON_DENIED)
        real_raw = os.path.realpath(lexical)
        real = self._norm(real_raw)
        if self._path_denied(real):
            return Resolved(False, reason=REASON_DENIED)
        if not any(self._under(real, r) for r in self._roots):
            return Resolved(False, reason=REASON_OUTSIDE)
        return Resolved(True, real=real_raw, in_project=self._under(real, self.project_root))

    def is_denied(self, real_path: str) -> bool:
        return self._path_denied(self._norm(os.path.realpath(real_path)))


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


def read_text_capped(
    real: str, *, start_line: int = 1, max_lines: int | None = None, cap: int = MAX_READ_BYTES
) -> ReadOutcome:
    """读 `real`（已过 `check`）。二进制只给类型与大小；超过 `cap` 的给前若干行 + 截断说明。"""
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
            lineno = 0
            for raw in f:
                lineno += 1
                if lineno < start_line:
                    continue
                if max_lines is not None and len(lines) >= max_lines:
                    truncated = True
                    break
                if used + len(raw) > cap:
                    truncated = True
                    if not lines:  # 单行就超上限：截断这一行
                        lines.append(raw[:cap])
                    break
                lines.append(raw)
                used += len(raw)
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
