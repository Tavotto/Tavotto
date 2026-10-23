"""「改」组的快照与回滚（ADR 0149 §七）。

`listed` / `user_env` / `tavotto` 三档的改动**执行前**自动打一个快照，存 `data_dir()/cache/fix_snapshots/<会话>/<id>.json`：

* **本项目相关设置**：项目设置里的 `execution_policy`（运行方式）与 `environment`（项目用哪个环境）两个键，原值整段照存（含「没有这个键」）；
* **环境包清单**：目标环境的 `pip freeze` 解析成 `{规范名: 版本}`（只留 `名==版本` 行，URL / 路径行的内容一概不存，免得带进机器路径与凭据），
  外加环境目录顶层的条目名（只存名字）。

回滚（`rollback`）：设置**精确恢复**（原来没有的键清掉）；包按差异——快照后新装的卸掉，被改过版本 / 被卸掉的装回快照里的版本。
装 / 卸走 `FixContext` 上注入的执行器（缺省是应用里现有的 pip 路径，见 `change.py`），所以回滚与正向安装是同一条路、同样的护栏。
快照只认**本会话**的目录；id 形状固定，拒绝任何路径片段。
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from .. import scriptlock, sourcelang
from ..agent_runner.wires import ToolResult
from . import executors
from .base import FixContext, err, ok

#: 快照 id 的形状（也是文件名）；`rollback` 只接受这个形状，不会被拿去拼出路径外的文件
SNAP_ID_RE = re.compile(r"snap_\d{8}T\d{6}_[0-9a-f]{6}")
#: 每个会话最多留多少份（超了丢最老的）
KEEP_PER_SESSION = 30
#: 环境目录清单最多记多少个顶层条目名
LISTING_MAX = 200
#: 回滚时绝不卸的包（卸掉环境就坏了）
PROTECTED = frozenset({"pip", "setuptools", "wheel"})

#: 用户脚本备份的上限（`run_command` 执行前把项目里的用户脚本字节存进快照）：超了就拒绝执行（`scripts_too_large`），不裸跑
SCRIPTS_MAX_FILES = 1000
SCRIPTS_MAX_BYTES = 32 * 1024 * 1024
#: 备份时不下探的目录（环境 / 版本库 / 缓存 / Tavotto 自己的收纳目录）
SCRIPTS_SKIP_DIRS = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "venv", ".hg", ".svn", "tavottofile"}
)

_FREEZE_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;#@]+)\s*$")
_SESSION_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def settings_keys() -> tuple[str, ...]:
    from .. import execpolicy, projectenv

    return (execpolicy.SETTINGS_KEY, projectenv.SETTINGS_KEY)


def snapshot_root(session_id: str) -> Path:
    from ..config import data_dir

    safe = _SESSION_SAFE.sub("_", str(session_id))[:64] or "session"
    return data_dir() / "cache" / "fix_snapshots" / safe


def parse_freeze(text: str) -> dict[str, str]:
    """`pip freeze` 文本 → `{规范名: 版本}`；只认 `名==版本` 行。"""
    from .. import depresolve

    out: dict[str, str] = {}
    for line in str(text or "").splitlines():
        m = _FREEZE_LINE.match(line.strip())
        if m:
            out[depresolve.normalize_distribution(m.group(1))] = m.group(2)
    return out


def default_freezer(python: str) -> str | None:
    """`pip freeze` 文本（已脱敏）。**成功但输出为空（全新空环境）回 `""`，问不出来（退出码非 0 / 起不来）回 `None`**——
    两者必须分开：空环境的快照是有效的「什么都没有」，装了第一个包之后回滚要能把它卸掉（#1371）。"""
    from .. import deprepair

    try:
        rc, out = deprepair._run(
            [str(python), "-m", "pip", "freeze", "--disable-pip-version-check"],
            deprepair.FREEZE_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001 - 问不出来就是没有清单，不是工具崩
        return None
    return deprepair._sanitize(out) if rc == 0 else None


def _freeze_of(ctx: FixContext, python: str) -> str | None:
    """同 `default_freezer` 的约定：`None` = 没拿到清单，`""` = 拿到了、是空的。"""
    try:
        out = (ctx.freezer or default_freezer)(python)
    except Exception:  # noqa: BLE001
        return None
    return out if isinstance(out, str) else None


def _prefix_listing(python: str) -> list[str]:
    """环境目录（`<prefix>/bin/python` → `<prefix>`）的顶层条目名；读不了就是空。"""
    p = Path(os.path.abspath(python)).parent
    prefix = p.parent if p.name.lower() in ("bin", "scripts") else p
    try:
        return sorted(e.name for e in os.scandir(prefix))[:LISTING_MAX]
    except OSError:
        return []


def _current_settings(ctx: FixContext) -> dict:
    from .. import config

    cur = config.project_settings(str(ctx.project_root)) or {}
    return {k: copy.deepcopy(cur.get(k)) for k in settings_keys()}


# --------------------------------------------------------------------------- 用户脚本的备份与复核
def is_user_script(name: str) -> bool:
    """什么算用户脚本：与「生成可编辑版本」认的来源同一判据——`.py`，加 `sourcelang.extension_hint` 认的 R / MATLAB 后缀。"""
    return Path(name).suffix == ".py" or sourcelang.extension_hint(name) is not None


#: 找脚本时最多看这么多个目录条目（跟随目录链接后可能撞上一棵巨树）；超了按「备份不了」处理
SCRIPTS_MAX_ENTRIES = 200_000


def list_scripts(project_root: Path, scope=None) -> list[str] | None:
    """项目里所有用户脚本的**真实路径**（去重）。回 None = 规模超限（文件数 / 总字节 / 目录条目数），备份不了就不让命令去跑。

    **跟随目录符号链接**（`shared -> ../shared` 里的脚本命令一样改得到），按目录的真实路径去重防环；`scope`（`ReadScope`）给了，
    不在允许范围内或在敏感区（`DENY`）的目录 / 文件不下探、不收。只 stat、不读内容。"""
    found: dict[str, int] = {}
    seen_dirs: set[str] = set()
    stack = [os.path.realpath(project_root)]
    entries = total = 0
    while stack:
        cur = stack.pop()
        if cur in seen_dirs:
            continue
        seen_dirs.add(cur)
        try:
            with os.scandir(cur) as it:
                listing = list(it)
        except OSError:
            continue
        for e in listing:
            entries += 1
            if entries > SCRIPTS_MAX_ENTRIES:
                return None
            try:
                is_dir = e.is_dir()  # 跟随链接
                if not is_dir and not (is_user_script(e.name) and e.is_file()):
                    continue
                if is_dir and e.name in SCRIPTS_SKIP_DIRS:
                    continue
                real = os.path.realpath(e.path)
                if scope is not None and not scope.check(e.path).ok:
                    continue
                if is_dir:
                    stack.append(real)
                    continue
                if real in found:
                    continue
                size = os.stat(real).st_size
            except OSError:
                return None  # 看得见却读不了：保护不了，当作备份不了
            total += size
            if len(found) >= SCRIPTS_MAX_FILES or total > SCRIPTS_MAX_BYTES:
                return None
            found[real] = size
    return sorted(found)


def read_scripts(paths: list[str]) -> dict[str, bytes] | None:
    """读出这些脚本的字节（基线）；读不了回 None。**调用方要先持有它们的锁**（见 `hold_scripts`）。"""
    out: dict[str, bytes] = {}
    for real in paths:
        try:
            out[real] = Path(real).read_bytes()
        except OSError:
            return None
    return out


@contextlib.contextmanager
def hold_scripts(paths: list[str]):
    """依次（按规范化真实路径排序，防死锁）持有这些脚本的 `scriptlock.script_guard`，整段「取基线 → 命令 → 核对 / 恢复」不放。
    界面里的保存走同一把锁（`scriptbackup.replace` 等），会**等**到这一段结束再落盘，所以用户的合法编辑不会被当成命令的改动写回旧版。"""
    with contextlib.ExitStack() as stack:
        for real in sorted(paths, key=scriptlock.script_key):
            stack.enter_context(scriptlock.script_guard(real))
        yield


def restore_script(path: str | os.PathLike, data: bytes) -> bool:
    """把一份用户脚本恢复成给定字节：**只经 `scriptlock`**（持这份脚本的锁、`write_script` 原子写）。成功回 True。"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with scriptlock.script_guard(path):
            scriptlock.write_script(path, data)
    except OSError:
        return False
    return True


def verify_scripts(saved: dict[str, bytes]) -> tuple[list[str], list[str]]:
    """逐个核对备份：被改 / 被删的经 `restore_script` 恢复原字节。回 (已恢复, 恢复失败) 的真实路径。"""
    restored: list[str] = []
    failed: list[str] = []
    for path, data in saved.items():
        try:
            same = hashlib.sha256(Path(path).read_bytes()).digest() == hashlib.sha256(data).digest()
        except OSError:
            same = False  # 删了 / 读不了：当作被改
        if same:
            continue
        (restored if restore_script(path, data) else failed).append(path)
    return restored, failed


def _display(project_root: Path, real: str) -> str:
    try:
        return Path(real).relative_to(os.path.realpath(project_root)).as_posix()
    except ValueError:
        return Path(real).name  # 项目外（经链接）只给文件名，不给路径


def display_names(ctx: FixContext, paths: list[str]) -> str:
    shown = [_display(ctx.project_root, p) for p in paths[:8]]
    return ", ".join(shown) + (", ..." if len(paths) > 8 else "")


def mark_unrestored(ctx: FixContext, snap_id: str, paths: list[str]) -> None:
    """把「命令改了、而且没能恢复」的脚本记进快照，`rollback` 会再试一次恢复它们。"""
    path = snapshot_root(ctx.session_id) / f"{snap_id}.json"
    try:
        doc = json.loads(path.read_text("utf-8"))
        doc["unrestored"] = sorted(set(doc.get("unrestored") or []) | set(paths))
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except (OSError, ValueError):
        pass


def _restore_backed_up_scripts(ctx: FixContext, doc: dict, lines: list[str]) -> bool:
    """回滚时：只恢复快照里标了「命令改过、当时没恢复成」的脚本（用户后来自己改的不动）。"""
    blobs = snapshot_root(ctx.session_id) / f"{doc['id']}.files"
    index = {e.get("path"): e for e in doc.get("scripts") or [] if isinstance(e, dict)}
    good = True
    for real in doc.get("unrestored") or []:
        entry = index.get(real)
        try:
            data = (blobs / str(entry["file"])).read_bytes() if entry else None
        except (OSError, KeyError):
            data = None
        if data is None or not restore_script(real, data):
            good = False
            lines.append(f"script {_display(ctx.project_root, real)}: could not be restored")
        else:
            lines.append(
                f"script {_display(ctx.project_root, real)}: restored to its original bytes"
            )
    return good


def take(
    ctx: FixContext,
    tool: str,
    env_ids: list[str] | None = None,
    scripts: dict[str, bytes] | None = None,
) -> str | None:
    """打一个快照，回快照 id；写不出来回 None（调用方据此拒绝这次改动：没有退路就不改）。
    `scripts`：项目里用户脚本的字节（`collect_scripts`），存在 `<id>.files/` 里，索引（路径 + sha256 + 大小）进 JSON。"""
    snap_id = f"snap_{time.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    envs: dict[str, dict] = {}
    for eid in env_ids or []:
        python = ctx.python_of(eid)
        if python is None:
            continue
        text = _freeze_of(ctx, python)
        envs[eid] = {
            "packages": parse_freeze(text or ""),
            "freeze_available": text is not None,  # 按「拿没拿到」判，不按输出是否为空
            "listing": _prefix_listing(python),
        }
    try:
        doc = {
            "id": snap_id,
            "ts": round(time.time(), 3),
            "tool": tool,
            "project": os.path.normcase(os.path.abspath(str(ctx.project_root))),
            "settings": _current_settings(ctx),
            "envs": envs,
            "scripts": [],
        }
        root = snapshot_root(ctx.session_id)
        root.mkdir(parents=True, exist_ok=True)
        if scripts:
            blobs = root / f"{snap_id}.files"
            blobs.mkdir(parents=True, exist_ok=True)
            for i, (real, data) in enumerate(sorted(scripts.items())):
                (blobs / f"{i}.bin").write_bytes(data)
                doc["scripts"].append(
                    {
                        "path": real,
                        "file": f"{i}.bin",
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size": len(data),
                    }
                )
        tmp = root / f"{snap_id}.json.tmp"
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, root / f"{snap_id}.json")
        for stale in sorted(root.glob("snap_*.json"))[:-KEEP_PER_SESSION]:
            stale.unlink(missing_ok=True)
            shutil.rmtree(root / f"{stale.stem}.files", ignore_errors=True)
    except (OSError, ValueError):
        return None
    ctx.call_meta["snapshot"] = snap_id
    return snap_id


def load(ctx: FixContext, snap_id: object) -> dict | None:
    if not isinstance(snap_id, str) or not SNAP_ID_RE.fullmatch(snap_id):
        return None
    try:
        doc = json.loads((snapshot_root(ctx.session_id) / f"{snap_id}.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("id") != snap_id:
        return None
    here = os.path.normcase(os.path.abspath(str(ctx.project_root)))
    return doc if doc.get("project") == here else None  # 别的项目的快照不在这个项目上回滚


def list_ids(ctx: FixContext) -> list[str]:
    try:
        return [p.stem for p in sorted(snapshot_root(ctx.session_id).glob("snap_*.json"))]
    except OSError:
        return []


# --------------------------------------------------------------------------- 回滚
def _restore_settings(ctx: FixContext, saved: dict) -> bool:
    from .. import config, projectenv

    patch = {k: copy.deepcopy(saved.get(k)) for k in settings_keys()}  # None = 清掉这个键
    try:
        config.set_project_settings(str(ctx.project_root), patch)
    except OSError:
        return False
    projectenv.reset_cache(ctx.project_root)  # 进程内缓存的「项目用哪个环境」也要跟着回去
    return True


def _restore_packages(ctx: FixContext, eid: str, saved: dict, lines: list[str]) -> bool:
    """按差异恢复一个环境的包。回是否全部成功；每一步都写进 `lines`。"""
    from .. import depresolve

    python = ctx.python_of(eid)
    if python is None:
        lines.append(f"environment {eid}: no longer known in this session, skipped")
        return False
    if not saved.get("freeze_available"):
        lines.append(
            f"environment {eid}: the snapshot has no package list, packages left as they are"
        )
        return False
    before = {k: str(v) for k, v in (saved.get("packages") or {}).items()}
    current_text = _freeze_of(ctx, python)
    current = parse_freeze(current_text or "")
    if current_text is None:
        lines.append(f"environment {eid}: could not read the installed packages, nothing changed")
        return False
    remove = sorted(n for n in current if n not in before and n not in PROTECTED)
    reinstall = []
    for name, version in sorted(before.items()):
        if current.get(name) != version:
            req = f"{name}=={version}"
            if depresolve.parse_requirement(req) is None:
                lines.append(
                    f"environment {eid}: skipped {name} (its old version has an unusual form)"
                )
                continue
            reinstall.append(req)
    good = True
    if remove:
        res = executors.run_uninstaller(ctx, python, remove)
        good &= bool(res.get("ok"))
        lines.append(
            f"environment {eid}: removed {', '.join(remove)}"
            if res.get("ok")
            else f"environment {eid}: could not remove {', '.join(remove)} ({res.get('code') or 'failed'})"
        )
    if reinstall:
        res = executors.run_installer(ctx, python, reinstall)
        good &= bool(res.get("ok"))
        lines.append(
            f"environment {eid}: restored {', '.join(reinstall)}"
            if res.get("ok")
            else f"environment {eid}: could not restore {', '.join(reinstall)} ({res.get('code') or 'failed'})"
        )
    if not remove and not reinstall:
        lines.append(f"environment {eid}: packages already match the snapshot")
    return good


def rollback(ctx: FixContext, args: dict) -> ToolResult:
    snap_id = args.get("snapshot")
    doc = load(ctx, snap_id)
    if doc is None:
        known = list_ids(ctx)
        return err(
            "snapshot_unknown",
            "No such snapshot in this session."
            + (
                f" Available: {', '.join(known[-8:])}."
                if known
                else " No snapshots were taken yet."
            ),
        )
    lines: list[str] = []
    good = _restore_settings(ctx, doc.get("settings") or {})
    lines.append(
        "project settings restored to the state of the snapshot"
        if good
        else "project settings could not be written back"
    )
    for eid, saved in (doc.get("envs") or {}).items():
        if isinstance(saved, dict):
            good &= _restore_packages(ctx, str(eid), saved, lines)
    good &= _restore_backed_up_scripts(ctx, doc, lines)
    text = "\n".join(lines)
    if not good:
        return err("rollback_incomplete", "The rollback did not fully succeed:\n" + text)
    res = ok(f"Rolled back to {snap_id}:\n{text}", "rolled back")
    res.mutated = True
    return res
