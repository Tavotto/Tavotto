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

#: 环境变量的值**默认不记**，只记名字（d2 人工核 #2100：黑名单猜凭据永远漏，`DB_PASS`、`*_PWD`、`*_DSN`……）。
#: 只有两种记值：① 已知无害、影响出图的变量；② 名字一看就是「某个目录 / 路径」、值本身也是个路径的变量（数据目录类，
#: 「缺环境变量」最常见的就是它们）。名字像凭据的一律不记值，即使碰巧落在这两类里
_SAFE_ENV = frozenset(
    {
        "MPLBACKEND",
        "MPLCONFIGDIR",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "PYTHONHASHSEED",
        "LANG",
        "LANGUAGE",
        "TZ",
        "QT_QPA_PLATFORM",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    }
)
_PATH_ENV = re.compile(r"(?:^|_)(?:DIR|DIRECTORY|PATH|ROOT|FOLDER|HOME|DATA)$", re.I)
#: 短的、常见的字面量（`"data"`、`"w"`）不当秘密：抹掉它们只会把记下的路径抹坏（d2 人工核 #2100 P2）
_SECRET_MIN_CHARS = 8


def _env_value_kept(name: str, value: str) -> bool:
    if _SECRETISH.search(name):
        return False
    if name in _SAFE_ENV or name.startswith("LC_"):
        return True
    return bool(_PATH_ENV.search(name)) and _looks_like_path(value)


def _looks_like_path(value: str) -> bool:
    v = value.strip()
    return (
        bool(v)
        and (
            v.startswith(("/", "~", "./", "../", "\\\\"))
            or (len(v) > 2 and v[1] == ":" and v[2] in "/\\")
        )
        and "\n" not in v
    )


def _maybe_secret(value: str) -> bool:
    """要从别的字段里抹掉的候选：够长、不是路径（路径抹了记录就没用了，口令里极少有斜杠）。"""
    return (
        len(value) >= _SECRET_MIN_CHARS
        and not _looks_like_path(value)
        and "/" not in value
        and "\\" not in value
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


def _env_sets(tree: ast.AST, used: set[int] | None = None) -> list[tuple[str, str | None]]:
    """启动脚本里设环境变量的每一处（按 Python 语法认，不靠正则）：`os.environ[k] = v`、`os.environ.setdefault(k, v)`、
    `os.environ.update({k: v} / k=v)`、`os.putenv(k, v)`。值不是字面量的记 None（名字照记）。`used` 收下认出来的那几处
    `environ` / `putenv` 节点的 id（`launcher_is_risky` 拿它判断有没有认不出的写法）。"""
    out: list[tuple[str, str | None]] = []
    used = used if used is not None else set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (
                    isinstance(t, ast.Subscript)
                    and _is_environ(t.value)
                    and _str(t.slice) is not None
                ):
                    out.append((_str(t.slice), _str(node.value)))
                    used.add(id(t.value))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr, owner = node.func.attr, node.func.value
            first = _str(node.args[0]) if node.args else None
            second = _str(node.args[1]) if len(node.args) > 1 else None
            if attr == "setdefault" and _is_environ(owner) and first is not None:
                out.append((first, second))
                used.add(id(owner))
            elif attr == "putenv" and first is not None:
                out.append((first, second))
                used.add(id(node.func))
            elif attr == "update" and _is_environ(owner):
                used.add(id(owner))
                for arg in node.args:
                    if isinstance(arg, ast.Dict):
                        for k, v in zip(arg.keys, arg.values):
                            if _str(k) is not None:
                                out.append((_str(k), _str(v)))
                for kw in node.keywords:
                    if kw.arg:
                        out.append((kw.arg, _str(kw.value)))
    return out


def _quoted(line: str) -> list[str]:
    """一行里引号包着的每一段（线性扫描，不用正则，免得在不可信文本上回溯；只用来抹别的字段）。"""
    out: list[str] = []
    quote, start = "", 0
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                if i > start:
                    out.append(line[start:i])
                quote = ""
        elif ch in "'\"":
            quote, start = ch, i + 1
    return out


def _mask(text: str, secrets: set[str] | frozenset[str]) -> str:
    for v in sorted(secrets, key=len, reverse=True):
        text = text.replace(v, "…")
    return text


def _kept_env(tree: ast.AST) -> list[tuple[str, str | None]]:
    """要记的环境变量 (名字, 值)：名字是字面量（按解码后的值判断，`\\x4b` 之类转义照样认得出）；名字像凭据的、值不是字面量的，
    值记 None（只记名字）。"""
    return [
        (name, value if value is not None and _env_value_kept(name, value) else None)
        for name, value in _env_sets(tree)[:10]
    ]


def _ident_tokens(tree: ast.AST):
    """解码后的名字与字符串：常量、变量名、属性名、关键字参数名、函数 / 参数名、导入名。"""
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n.value
        elif isinstance(n, ast.Name):
            yield n.id
        elif isinstance(n, ast.Attribute):
            yield n.attr
        elif isinstance(n, ast.keyword) and n.arg:
            yield n.arg
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield n.name
        elif isinstance(n, ast.arg):
            yield n.arg
        elif isinstance(n, ast.alias):
            yield n.asname or n.name


def launcher_is_risky(text: str) -> bool:
    """这版启动脚本碰没碰凭据——碰了，这次会话的运行参数与数据路径**整条不记**（d2 人工核后 Codex #2100 再报两条：
    从别的字段里挑值去抹，总有漏的形状；不记就没有漏）。算「碰了」的：
    解析不了；任何解码后的名字 / 字符串像凭据；设了不在白名单里的环境变量值（含值不是字面量的）；
    出现了认不出写法的 `environ` / `putenv`（别名、标注赋值、`from os import putenv`……）。"""
    try:
        tree = ast.parse(text or "")
    except (SyntaxError, ValueError):
        return True
    if any(_SECRETISH.search(tok) for tok in _ident_tokens(tree)):
        return True
    used: set[int] = set()
    sets = _env_sets(tree, used)
    if any(v is None or not _env_value_kept(k, v) for k, v in sets):
        return True
    for n in ast.walk(tree):
        name = n.attr if isinstance(n, ast.Attribute) else n.id if isinstance(n, ast.Name) else None
        if name in ("environ", "putenv", "unsetenv") and id(n) not in used:
            return True
        if isinstance(n, ast.alias) and n.name.split(".")[-1] in ("environ", "putenv"):
            return True
    return False


def launcher_secrets(text: str) -> set[str]:
    """同一会话别的字段要抹掉的值：启动脚本里**所有**字符串字面量（解码后的值），只除去上面那张表里明确要记的非凭据变量值。
    不按拼写豁免任何值（Codex #2100 四轮 P1：`'mysecret123'` 这样的口令本身就像个名字）。解析不了：每一段引号里的内容都算。"""
    try:
        tree = ast.parse(text or "")
    except (SyntaxError, ValueError):
        return {v for line in (text or "").splitlines() for v in _quoted(line) if _maybe_secret(v)}
    kept = {v for _, v in _kept_env(tree) if v is not None}
    # 环境变量里没记值的那些（多半是凭据）不论长短都抹；其余字面量只抹够长、不是路径的
    unkept = {v for n, v in _env_sets(tree) if v and v not in kept}
    return unkept | {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and n.value not in kept
        and _maybe_secret(n.value)
    }


def _launcher_fields(
    text: str, scrub: Callable[[str], str] = _default_scrub, secrets: frozenset[str] = frozenset()
) -> dict:
    """启动脚本只记它设了哪些环境变量，**正文一律不记**（Codex #2100 四轮 P1：按「是不是凭据」去决定记不记正文，转义、拼接、
    别名的写法认不完）。正文要看时诊断包的修复经过里有当次那份（ADR 0197 第 4 条，同样脱敏）。"""
    try:
        tree = ast.parse(text or "")
    except (SyntaxError, ValueError):
        return {"env": [], "text": ""}
    env = [
        {"name": n}
        if v is None
        else {"name": n, "value": _tidy(_mask(v, set(secrets)), ARG_MAX_CHARS, scrub)}
        for n, v in _kept_env(tree)
    ]
    return {"env": env, "text": ""}


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
        return _launcher_fields(text, scrub, secrets)
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
    secrets: frozenset[str] | set[str] = frozenset(),
    risky: bool = False,
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
            secrets = frozenset(secrets) | frozenset(
                v
                for kind, change in changes
                if kind == "launcher" and isinstance((change or {}).get("text"), str)
                for v in launcher_secrets(change["text"])
            )
            risky = risky or any(
                kind == "launcher"
                and isinstance((change or {}).get("text"), str)
                and launcher_is_risky(change["text"])
                for kind, change in changes
            )
            for kind, change in changes:
                if risky and kind in ("run_args", "data_link"):
                    continue  # 这次会话的启动脚本碰过凭据：参数与路径整条不记
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
