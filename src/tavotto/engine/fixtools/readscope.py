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
    text = _KV_SECRET.sub(_mask_kv, text)
    text = _CLI_SECRET.sub(lambda m: f"{m.group(1)}***", text)
    text = _BEARER.sub("Bearer ***", text)
    return _TOKEN_SHAPES.sub("***", text)


_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S
)
#: 键名（含 `password` / `token` 等）+ 分隔符（`=` 或 `:`，两侧可有空白）+ 值。值要么是引号包着的任意内容（含空格、
#: 任意长度，`"password": "x"` 也抹），要么是 >=6 个非空白字符的裸值。命名组 `head` = 键与分隔符（保留），其余整体换成 `***`。
_KV_SECRET = re.compile(
    r"(?i)(?P<head>(?:api[_-]?key|secret|passwd|password|token|auth[_-]?token|access[_-]?key)[\w-]*[\"']?\s*[:=]\s*)"
    r"(?:(?P<dq>\"[^\"\n]*\")|(?P<sq>'[^'\n]*')|(?!\*\*\*)(?P<bare>[^\s\"',;]{6,}))"
)
#: 命令行形状 `--password hunter22` / `--api-key=abc`：只保留旗标
_CLI_SECRET = re.compile(
    r"(?i)(--(?:api[_-]?key|secret|passwd|password|token|auth[_-]?token|access[_-]?key)(?:\s+|=))"
    r"(?!\*\*\*)[^\s\"']+"
)


def _mask_kv(m: re.Match) -> str:
    """键与分隔符原样，值整体换成 `***`（引号保留）。"""
    head = m.group("head")
    if m.group("dq") is not None:
        return head + '"***"'
    if m.group("sq") is not None:
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
