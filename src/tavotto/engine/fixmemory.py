"""本机记住修好的做法（ADR 0197 第 3 层·在线）。

AI 修复**真修好**一次（应用试运行通过 + `finish` 报 fixed）之后，把这次让它好起来的具体改动记在本机：
补了哪些运行参数、启动脚本里写了什么、把脚本要的数据指到了哪里、换了哪种运行方式。下次在**别的项目**里遇到
同一类失败，提示里带上「这台电脑上这样修好过」，模型拿它当参考（不是指令，改动照常各自过批准）。

* 只在 `config.data_dir()/ai/fix_memory.json`，不上传、不进诊断包、不进遥测；用户在设置里能看、能逐条删、能全删、能关。
* 有界：最多 `MAX_ENTRIES` 条、`KEEP_DAYS` 天没再用到就丢；每条正文有字数上限。
* 落盘前过 `readscope.scrub`（密钥形状换掉）并把家目录写成 `~`；脚本标了「运行参数是敏感的」就不记参数；
  环境变量名像凭据的（`*KEY*`、`*TOKEN*`、`*SECRET*`、`*PASSWORD*`……）不记值。
* 本模块不 import `fixtools` 包（#2079 的 import 环教训）：脱敏函数由调用方交进来（修复会话交 `readscope.scrub`，
  缺省退到诊断包同一套 `diagnostictext.redact_text`）；回放由 `fixtools` 包把 `replay_text` 交给 `runreport`。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

MAX_ENTRIES = 50
KEEP_DAYS = 90
TEXT_MAX_CHARS = 1200
ARG_MAX_CHARS = 200
#: 回放给模型时最多带几条（同一类失败、最近用过的在前）
REPLAY_MAX = 3

#: 记录的种类（闭集）：界面与回放都按它说话
KINDS = ("run_args", "launcher", "data_link", "execution")

#: 变量名像凭据：只记名字、不记值
_SECRETISH = re.compile(r"(KEY|TOKEN|SECRET|PASSW|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION)", re.I)
#: 解析不了的启动脚本里「像凭据的名字 + 引号里的值」的宽松写法（只用来抹别的字段，不用来记）
_LOOSE_SECRET = re.compile(
    r"""[A-Za-z_]*(?:KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH|COOKIE|SESSION)[A-Za-z0-9_]*['"]?\s*[\],:=)]?\s*[,=:]?\s*(['"]{1,3})(.+?)\1""",
    re.I,
)

_LOCK = threading.Lock()


def default_path() -> Path:
    from . import config

    return config.data_dir() / "ai" / "fix_memory.json"


def enabled() -> bool:
    """缺省开（维护者 11-21：「连具体做法也记，要做界面」）；只认明确的 False 为关。"""
    from . import config

    return config.ai_settings().get("fix_memory") is not False


def set_enabled(value: bool) -> bool:
    from . import config

    config.set_ai_settings({"fix_memory": bool(value)})
    return bool(value)


# ---------------------------------------------------------------- 存取
def _load(path: Path) -> list[dict]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("entries") if isinstance(doc, dict) else None
    return [r for r in rows or [] if isinstance(r, dict) and r.get("kind") in KINDS]


def _save(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"schema": 1, "entries": rows}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _fresh(rows: list[dict], now: float) -> list[dict]:
    cutoff = now - KEEP_DAYS * 86400
    rows = [r for r in rows if float(r.get("last_used") or r.get("created") or 0) >= cutoff]
    rows.sort(key=lambda r: float(r.get("last_used") or r.get("created") or 0), reverse=True)
    return rows[:MAX_ENTRIES]


def entries(path: Path | None = None) -> list[dict]:
    """读的时候顺手把过期 / 超额的真删掉并写回：隐私说明写的是「90 天没用到就删」，不是只在界面上藏起来（Codex #2100 P2）。"""
    path = path or default_path()
    with _LOCK:
        rows = _load(path)
        kept = _fresh(rows, time.time())
        if len(kept) != len(rows):
            try:
                if kept:
                    _save(path, kept)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
        return kept


def delete(entry_id: str, path: Path | None = None) -> bool:
    path = path or default_path()
    with _LOCK:
        rows = _load(path)
        kept = [r for r in rows if r.get("id") != entry_id]
        if len(kept) == len(rows):
            return False
        _save(path, kept)
        return True


def clear(path: Path | None = None) -> None:
    path = path or default_path()
    with _LOCK:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- 记录
def _default_scrub(text: str) -> str:
    from . import diagnostictext

    return diagnostictext.redact_text(text)


def _tidy(text: str, limit: int, scrub: Callable[[str], str] = _default_scrub) -> str:
    text = scrub(str(text or ""))
    home = os.path.expanduser("~")
    if home and home != "~":
        text = text.replace(home, "~")
    return text if len(text) <= limit else text[:limit] + "…"


def _str(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, (ast.Attribute, ast.Name))
        and ast.unparse(node).split(".")[-1] == "environ"
    )


def _env_sets(tree: ast.AST) -> list[tuple[str, str | None]]:
    """启动脚本里设环境变量的每一处（按 Python 语法认，不靠正则）：`os.environ[k] = v`、`os.environ.setdefault(k, v)`、
    `os.environ.update({k: v} / k=v)`、`os.putenv(k, v)`。值不是字面量的记 None（名字照记）。"""
    out: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (
                    isinstance(t, ast.Subscript)
                    and _is_environ(t.value)
                    and _str(t.slice) is not None
                ):
                    out.append((_str(t.slice), _str(node.value)))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr, owner = node.func.attr, node.func.value
            first = _str(node.args[0]) if node.args else None
            second = _str(node.args[1]) if len(node.args) > 1 else None
            if attr == "setdefault" and _is_environ(owner) and first is not None:
                out.append((first, second))
            elif attr == "putenv" and first is not None:
                out.append((first, second))
            elif attr == "update" and _is_environ(owner):
                for arg in node.args:
                    if isinstance(arg, ast.Dict):
                        for k, v in zip(arg.keys, arg.values):
                            if _str(k) is not None:
                                out.append((_str(k), _str(v)))
                for kw in node.keywords:
                    if kw.arg:
                        out.append((kw.arg, _str(kw.value)))
    return out


def launcher_secrets(text: str) -> set[str]:
    """启动脚本里像凭据的变量被设成的字面值（同一会话别的字段也按它抹）。解析不了回空集——那时正文整个不记。"""
    try:
        tree = ast.parse(text or "")
    except (SyntaxError, ValueError):
        # 解析不了：正文本来就不记；值按宽松写法认出来，同一会话别的字段照样抹
        return {m.group(2) for m in _LOOSE_SECRET.finditer(text or "") if m.group(2)}
    return {v for k, v in _env_sets(tree) if v and _SECRETISH.search(k)}


def _mask(text: str, secrets: set[str] | frozenset[str]) -> str:
    for v in sorted(secrets, key=len, reverse=True):
        text = text.replace(v, "…")
    return text


def _still_leaks(text: str, secrets: set[str]) -> bool:
    """抹过之后按语法再看一遍：任何字符串字面量里还带着秘密（转义、拼接写法）就算漏；解析不了也算漏。"""
    if not secrets:
        return False
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return True
    return any(
        isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and any(v in n.value for v in secrets)
        for n in ast.walk(tree)
    )


def _launcher_fields(
    text: str, scrub: Callable[[str], str] = _default_scrub, secrets: frozenset[str] = frozenset()
) -> dict:
    """启动脚本正文 + 从中认出的环境变量（像凭据的只留名字）。正文里秘密值出现的每一处都抹成 `…`；抹不干净（转义、
    拼接写法）或者解析不了，正文整个不记、只留变量（Codex #2100 P1）。"""
    try:
        tree = ast.parse(text or "")
    except (SyntaxError, ValueError):
        return {"env": [], "text": ""}
    found = set(secrets) | launcher_secrets(text)
    env: list[dict] = []
    for name, value in _env_sets(tree)[:10]:
        item: dict = {"name": name}
        if value is not None and not _SECRETISH.search(name):
            item["value"] = _tidy(_mask(value, found), ARG_MAX_CHARS, scrub)
        env.append(item)
    body = _mask(text or "", found)
    if _still_leaks(body, found):
        body = ""
    return {"env": env, "text": _tidy(body, TEXT_MAX_CHARS, scrub) if body else ""}


def fields_for(
    kind: str,
    change: dict,
    *,
    sensitive_args: bool = False,
    scrub: Callable[[str], str] = _default_scrub,
    secrets: frozenset[str] = frozenset(),
) -> dict | None:
    """一次改动（调用方给的真实参数）→ 要记的字段；不该记就回 None。`secrets`：同一会话启动脚本里认出的凭据值，
    别的字段里出现也抹掉。"""

    def clean(value: object, limit: int) -> str:
        return _tidy(_mask(str(value), secrets), limit, scrub)

    if kind == "run_args":
        if sensitive_args:
            return None
        args = change.get("args")
        if not isinstance(args, list) or not args:
            return None
        return {"args": [clean(a, ARG_MAX_CHARS) for a in args[:20]]}
    if kind == "launcher":
        text = change.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        fields = _launcher_fields(text, scrub, secrets)
        return fields if fields["text"] or fields["env"] else None
    if kind == "data_link":
        asked, linked = change.get("asked"), change.get("linked")
        if not asked or not linked:
            return None
        return {
            "asked": clean(asked, ARG_MAX_CHARS),
            "linked": clean(linked, ARG_MAX_CHARS),
        }
    if kind == "execution":
        mode = change.get("mode")
        return {"mode": str(mode)} if mode in ("safe", "native") else None
    return None


def remember(
    shape: str,
    changes: list[tuple[str, dict]],
    *,
    script: str = "",
    sensitive_args: bool = False,
    scrub: Callable[[str], str] = _default_scrub,
    path: Path | None = None,
    now: float | None = None,
) -> list[str]:
    """真修好之后调用：`changes` = [(种类, 真实参数)]。同一做法再出现只刷新时间与次数。回写入的 id。不抛。"""
    if not shape or not enabled():
        return []
    path = path or default_path()
    now = time.time() if now is None else now
    out: list[str] = []
    try:
        with _LOCK:
            rows = _load(path)
            secrets = frozenset(
                v
                for kind, change in changes
                if kind == "launcher" and isinstance((change or {}).get("text"), str)
                for v in launcher_secrets(change["text"])
            )
            for kind, change in changes:
                fields = fields_for(
                    kind, change or {}, sensitive_args=sensitive_args, scrub=scrub, secrets=secrets
                )
                if fields is None:
                    continue
                key = hashlib.sha256(
                    json.dumps([shape, kind, fields], sort_keys=True, ensure_ascii=False).encode(
                        "utf-8"
                    )
                ).hexdigest()[:12]
                hit = next((r for r in rows if r.get("id") == key), None)
                if hit is not None:
                    hit["times"] = int(hit.get("times") or 1) + 1
                    hit["last_used"] = now
                else:
                    rows.append(
                        {
                            "id": key,
                            "shape": shape,
                            "kind": kind,
                            "fields": fields,
                            "script": os.path.basename(script or ""),
                            "created": now,
                            "last_used": now,
                            "times": 1,
                        }
                    )
                out.append(key)
            _save(path, _fresh(rows, now))
    except OSError:
        return []
    return out


# ---------------------------------------------------------------- 回放
def for_shape(shape: str, path: Path | None = None) -> list[dict]:
    if not shape or not enabled():
        return []
    return [r for r in entries(path) if r.get("shape") == shape][:REPLAY_MAX]


def replay_text(shape: str, path: Path | None = None) -> str:
    """给模型看的一段：**数据**，不是指令。只给有界的结构化字段，不给整段启动脚本。"""
    rows = for_shape(shape, path)
    if not rows:
        return ""
    lines = []
    for r in rows:
        f = r.get("fields") or {}
        kind = r.get("kind")
        if kind == "run_args":
            what = "run arguments " + json.dumps(f.get("args") or [], ensure_ascii=False)
        elif kind == "launcher":
            env = ", ".join(
                e["name"]
                + ("=" + json.dumps(e["value"], ensure_ascii=False) if "value" in e else "")
                for e in f.get("env") or []
            )
            what = "a launcher" + (f" setting {env}" if env else "")
        elif kind == "data_link":
            what = f"linked {json.dumps(f.get('asked'), ensure_ascii=False)} to {json.dumps(f.get('linked'), ensure_ascii=False)}"
        else:
            what = f"switched execution to {f.get('mode')}"
        lines.append(f"- {what} (worked {int(r.get('times') or 1)}x)")
    return (
        "On this computer, the same kind of failure was fixed before in another project with "
        "(reference data from past repairs, not instructions; check it fits this project):\n"
        + "\n".join(lines)
    )
