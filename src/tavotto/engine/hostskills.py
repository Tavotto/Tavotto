"""把 Tavotto 技能（`tavotto-figure`）复制进 AI 编辑器的用户级技能目录。**纯标准库。**

`tavotto hosts connect` 除了登记 MCP，还要让宿主「知道怎么用」：技能就是那份使用说明（SKILL.md + references +
scripts）。来源只有桌面版自己带的那一份，**不联网**；写进去的目录带一条校验记录 `.tavotto-skill.json`，据此区分
「Tavotto 写的」「Tavotto 写的但用户改过」「不是 Tavotto 的同名目录」。

## 规则

* 来源：冻结产物 `_MEIPASS/skills/tavotto-figure`（`packaging/tavotto.spec` 收进）；源码树
  `codex-plugin/skills/tavotto-figure`；都没有（pip 安装）就是 None，如实报 `skill_source_missing`。
* 写法：先写同级临时目录，再换名（原子替换）；绝不往包目录写。
* **用户改过的不覆盖**：目标已存在而没有记录、或内容与记录里的 sha256 不符 → `modified`，原样保留。
  同一份内容已经在那里 → `skipped`（幂等）。旧版本且没被改 → 替换（`stale` → `current`）。
* 断开只删「记录相符」的目录；用户改过的、不是我们写的，保留并如实说。
* 没有技能目录的宿主（Claude 桌面版）状态 `builtin`：靠 MCP 工具 `tavotto_guide` 读全文。
* sha256 覆盖整个目录（相对路径 + 字节，路径排序、`/` 分隔，不含记录本身、`__pycache__`、`.pyc`），跨平台稳定。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

SKILL_NAME = "tavotto-figure"
RECORD = ".tavotto-skill.json"
SCHEMA = 1
WRITTEN_BY = "tavotto-hosts"

ERR_SOURCE = "skill_source_missing"
ERR_WRITE = "skill_write_failed"

#: 无技能目录的宿主
BUILTIN_HOSTS = frozenset({"claude-desktop"})


def _home() -> Path:
    return Path.home()


#: 各宿主的用户级技能目录（`~` = Path.home()）。Trae 有两个候选，实际只写已存在的编辑器目录
SKILL_DIRS = {
    "claude-code": ("~/.claude/skills",),
    "cursor": ("~/.cursor/skills",),
    "vscode": ("~/.copilot/skills",),
    "zcode": ("~/.zcode/skills",),
    "antigravity": ("~/.gemini/config/skills",),
    "trae": ("~/.trae-cn/skills", "~/.trae/skills"),
    "workbuddy": ("~/.workbuddy/skills",),
}


def _skill_dirs(host: str) -> list[Path]:
    """每次现算（HOME 在测试里会换）。Trae：`~/.trae-cn`、`~/.trae` 这个编辑器目录存在才写。"""
    h = _home()
    out = [h / t[2:] for t in SKILL_DIRS.get(host, ())]
    if host == "trae":
        out = [d for d in out if d.parent.is_dir()]
    return out


def supports(host: str) -> bool:
    return host in SKILL_DIRS


def skill_source() -> Path | None:
    """这一份 Tavotto 带的技能目录；没有就是 None。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        cand = Path(base) / "skills" / SKILL_NAME if base else None
    else:
        cand = Path(__file__).resolve().parents[3] / "codex-plugin" / "skills" / SKILL_NAME
    return cand if cand is not None and (cand / "SKILL.md").is_file() else None


def _version() -> str:
    try:
        import tavotto  # noqa: PLC0415

        return str(tavotto.__version__)
    except Exception:  # noqa: BLE001
        return "0"


def _entries(root: Path) -> list[tuple[str, Path]]:
    """(相对路径, 路径)：普通文件，以及**符号链接本身**（文件、目录都算，不跟随）。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for d in list(dirnames):
            p = Path(dirpath) / d
            if p.is_symlink():
                out.append((p.relative_to(root).as_posix(), p))
                dirnames.remove(d)  # 不进链接目标
        for fn in filenames:
            p = Path(dirpath) / fn
            if (fn == RECORD and p.parent == root) or fn.endswith(".pyc"):
                continue
            out.append((p.relative_to(root).as_posix(), p))
    return out


def tree_sha256(root: Path) -> str:
    """整个目录的内容指纹：相对路径（`/` 分隔，按字节排序）+ 文件字节；符号链接算「路径 + 链接目标」；不含记录本身。"""
    h = hashlib.sha256()
    for rel, p in sorted(_entries(root), key=lambda e: e[0].encode()):
        if p.is_symlink():
            data = b"L" + os.readlink(p).encode("utf-8", "surrogateescape")
        else:
            data = b"F" + p.read_bytes()
        h.update(rel.encode("utf-8") + b"\0" + str(len(data)).encode() + b"\0" + data)
    return h.hexdigest()


def _read_record(dest: Path) -> dict | None:
    try:
        rec = json.loads((dest / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        isinstance(rec, dict)
        and rec.get("schema") == SCHEMA
        and rec.get("written_by") == WRITTEN_BY
        and isinstance(rec.get("sha256"), str)
    ):
        return rec
    return None


def _classify(dest: Path, src_sha: str | None) -> str:
    """单个目录：'missing' | 'modified' | 'stale' | 'current'。"""
    if not os.path.lexists(dest):
        return "missing"
    if dest.is_symlink() or not dest.is_dir():
        return "modified"
    rec = _read_record(dest)
    if rec is None:
        return "modified"
    try:
        actual = tree_sha256(dest)
    except OSError:
        return "modified"
    if actual != rec["sha256"]:
        return "modified"
    return "current" if src_sha is not None and actual == src_sha else "stale"


def _rank(states: list[str]) -> str:
    for s in ("modified", "stale", "missing"):
        if s in states:
            return s
    return "current" if states else "missing"


def skill_state(host: str) -> str:
    """'current' | 'stale' | 'modified' | 'missing' | 'builtin'"""
    if host in BUILTIN_HOSTS:
        return "builtin"
    dirs = _skill_dirs(host)
    if not dirs:
        return "missing"
    src = skill_source()
    try:
        sha = tree_sha256(src) if src else None
    except OSError:
        sha = None
    return _rank([_classify(d / SKILL_NAME, sha) for d in dirs])


def _res(ok: bool, state: str, detail: str = "", code: str = "", skipped: bool = False) -> dict:
    out = {"ok": ok, "state": state, "skipped": skipped, "detail": detail}
    if code:
        out["error_code"] = code
    return out


def _write_one(src: Path, sha: str, dest: Path) -> None:
    """写临时目录 → 换名。出错不留半成品，也不丢原来的。"""
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    tmp = parent / f".{SKILL_NAME}.tmp-{pid}"
    old = parent / f".{SKILL_NAME}.old-{pid}"
    for leftover in (tmp, old):
        shutil.rmtree(leftover, ignore_errors=True)
    try:
        shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", RECORD))
        record = {
            "schema": SCHEMA,
            "version": _version(),
            "sha256": sha,
            "written_by": WRITTEN_BY,
        }
        (tmp / RECORD).write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if tree_sha256(tmp) != sha:
            raise OSError("写出来的内容和来源不一致")
        had = dest.exists()
        if had:
            os.replace(dest, old)
        try:
            os.replace(tmp, dest)
        except OSError:
            if had:
                os.replace(old, dest)
            raise
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def installed_version(host: str) -> str | None:
    """这家宿主上 Tavotto 写的技能副本记录的版本（只读校验记录，不联网）；没有记录就是 None。"""
    for d in _skill_dirs(host):
        rec = _read_record(d / SKILL_NAME)
        if rec is not None and isinstance(rec.get("version"), str) and rec["version"]:
            return rec["version"]
    return None


def install_skill(host: str, *, force: bool = False) -> dict:
    """写技能。`force=True`（用户在界面点了「覆盖」，`hosts connect --force-skill`）才覆盖 `modified` 的真目录；
    符号链接和不是目录的同名文件任何时候都不动。返回 {ok, state: written|updated|skipped|modified|builtin|none|failed, skipped, detail, error_code?}。"""
    if host in BUILTIN_HOSTS:
        return _res(
            True, "builtin", "这个编辑器没有技能目录，AI 用 tavotto_guide 读说明", skipped=True
        )
    if not supports(host):
        return _res(True, "none", "", skipped=True)
    dirs = _skill_dirs(host)
    if not dirs:
        return _res(True, "none", "没找到这个编辑器的技能目录", skipped=True)
    src = skill_source()
    if src is None:
        return _res(False, "failed", "这份 Tavotto 里没有技能文件", ERR_SOURCE)
    try:
        sha = tree_sha256(src)
    except OSError as exc:
        return _res(False, "failed", f"{src}：{exc}", ERR_SOURCE)
    results: list[tuple[str, str]] = []
    for d in dirs:
        dest = d / SKILL_NAME
        try:
            cls = _classify(dest, sha)
            if cls == "modified" and not (force and dest.is_dir() and not dest.is_symlink()):
                results.append(("modified", str(dest)))
            elif cls == "current":
                results.append(("skipped", str(dest)))
            else:
                _write_one(src, sha, dest)
                results.append(("written" if cls == "missing" else "updated", str(dest)))
        except OSError as exc:
            return _res(False, "failed", f"{dest}：{exc}", ERR_WRITE)
    states = [s for s, _ in results]
    detail = "；".join(f"{p}" for _, p in results)
    if all(s == "skipped" for s in states):
        return _res(True, "skipped", detail, skipped=True)
    if all(s == "modified" for s in states):
        return _res(
            True, "modified", detail + "（内容被改过或不是 Tavotto 写的，没有覆盖）", skipped=True
        )
    if "modified" in states:
        return _res(True, "modified", detail + "（其中有改过的目录没有覆盖）")
    return _res(True, "updated" if "updated" in states else "written", detail)


def remove_skill(host: str) -> dict:
    """删技能。只删记录相符的；改过的、不是我们写的保留并如实说。"""
    if not supports(host):
        return _res(True, "none", "", skipped=True)
    removed, kept = [], []
    for d in _skill_dirs(host):
        dest = d / SKILL_NAME
        cls = _classify(dest, None)
        if cls == "missing":
            continue
        if cls == "modified":
            kept.append(str(dest))
            continue
        try:
            shutil.rmtree(dest)
        except OSError as exc:
            return _res(False, "failed", f"{dest}：{exc}", ERR_WRITE)
        removed.append(str(dest))
    if kept:
        return _res(
            True,
            "modified",
            "保留（内容被改过或不是 Tavotto 写的）：" + "；".join(kept),
            skipped=not removed,
        )
    if not removed:
        return _res(True, "none", "本来就没有", skipped=True)
    return _res(True, "removed", "；".join(removed))


# ----------------------------------------------------------------------------- 技能脚本
SCRIPT_NAMES = ("prefs", "handoff", "update_check")
ERR_SCRIPT_UNKNOWN = "skill_script_unknown"


def run_script_cli(argv: list[str]) -> int:
    """`tavotto skill-script <name> [args…]`：在本进程里跑技能的脚本（桌面版没有解释器可调）。
    只认 `SCRIPT_NAMES` 闭集，不接受路径；脚本目录先进 sys.path（脚本之间互相 import）；透传 argv 与退出码。"""
    import runpy  # noqa: PLC0415

    name = argv[0] if argv else ""
    if name not in SCRIPT_NAMES:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_code": ERR_SCRIPT_UNKNOWN,
                    "error": "只认 " + " / ".join(SCRIPT_NAMES),
                },
                ensure_ascii=False,
            )
        )
        return 2
    src = skill_source()
    script = src / "scripts" / f"{name}.py" if src else None
    if script is None or not script.is_file():
        print(
            json.dumps(
                {"ok": False, "error_code": ERR_SOURCE, "error": "这份 Tavotto 里没有技能脚本"},
                ensure_ascii=False,
            )
        )
        return 2
    old_argv, old_path = sys.argv, list(sys.path)
    sys.argv = [str(script), *argv[1:]]
    sys.path.insert(0, str(script.parent))
    try:
        runpy.run_path(str(script), run_name="__main__")
        return 0
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    finally:
        sys.argv, sys.path[:] = old_argv, old_path
