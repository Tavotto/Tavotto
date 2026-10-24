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

import copy
import json
import os
import re
import time
import uuid
from pathlib import Path

from .. import scriptlock
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


# --------------------------------------------------------------------------- 恢复用户脚本的原字节（convert_script 用）
def restore_script(path: str | os.PathLike, data: bytes, *, mode: int | None = None) -> bool:
    """把一份用户脚本恢复成给定字节：**只经 `scriptlock`**（持这份脚本的锁、`write_script` 原子写）。成功回 True。
    `mode`：原来的权限位（保留可执行位）。"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with scriptlock.script_guard(path):
            scriptlock.write_script(path, data, mode=mode)
    except OSError:
        return False
    return True


def _managed_state(ctx: FixContext) -> dict | None:
    """项目受管环境此刻的 active 代（回滚要把它指回来；`change_python` 切代后旧代只靠租约保着）。没有受管环境 = None。"""
    from .. import managedenv

    try:
        python = managedenv.python_of(ctx.project_root)
        gen = managedenv.active_generation(ctx.project_root)
    except Exception:  # noqa: BLE001 - 问不出来 = 快照里没有这一项
        return None
    return {"generation": gen} if (python and gen) else None


def take(ctx: FixContext, tool: str, env_ids: list[str] | None = None) -> str | None:
    """打一个快照，回快照 id；写不出来回 None（调用方据此拒绝这次改动：没有退路就不改）。"""
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
            "managed": _managed_state(ctx),
        }
        root = snapshot_root(ctx.session_id)
        root.mkdir(parents=True, exist_ok=True)
        tmp = root / f"{snap_id}.json.tmp"
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, root / f"{snap_id}.json")
        for stale in sorted(root.glob("snap_*.json"))[:-KEEP_PER_SESSION]:
            stale.unlink(missing_ok=True)
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


def _gone(what: str) -> ToolResult:
    return err(
        "rollback_target_gone",
        f"{what} no longer exists on this computer (it was cleaned up), so the project cannot be pointed back at it. "
        "Create a new environment (change_python) or choose another one instead.",
    )


def _managed_target_gone(ctx: FixContext, managed: object) -> ToolResult | None:
    """快照里记的受管环境那一代还在不在（解释器文件真的在）。不在就**一个字都不改**，如实报错，不报成功。"""
    if not isinstance(managed, dict) or not managed.get("generation"):
        return None
    from .. import managedenv

    gen = str(managed["generation"])
    try:
        known = gen in managedenv.generations(ctx.project_root)
        python = managedenv.generation_python(ctx.project_root, gen)
        present = known and python.is_file()
    except Exception:  # noqa: BLE001
        present = False
    return None if present else _gone("The project's previous Tavotto environment")


def _restore_managed(ctx: FixContext, managed: object, lines: list[str]) -> bool:
    """把受管环境的 active 指回快照时的那一代（`managedenv.activate`，manifest 的既有提交点），并让池 / 缓存放弃新一代。"""
    if not isinstance(managed, dict) or not managed.get("generation"):
        return True
    from .. import managedenv, pool, projectenv

    gen = str(managed["generation"])
    if managedenv.active_generation(ctx.project_root) != gen:
        try:
            managedenv.activate(ctx.project_root, gen)
        except (OSError, ValueError):
            lines.append("the previous Tavotto environment could not be made active again")
            return False
        pool.reset_worker_python()
        pool.invalidate_project(str(ctx.project_root))
        projectenv.reset_cache(ctx.project_root)
        lines.append("the project uses its previous Tavotto environment again")
    return True


def _remembered_target_gone(ctx: FixContext) -> ToolResult | None:
    """恢复设置之后核对：项目设置里记着的解释器真的在（显式选择失效要说出来，不静默当成功）。"""
    from .. import projectenv

    try:
        rec = projectenv.remembered_record(ctx.project_root)
    except Exception:  # noqa: BLE001
        return None
    if rec and rec.get("path") and rec.get("exists") is False:
        return _gone("The environment the project was set to use")
    return None


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
    gone = _managed_target_gone(ctx, doc.get("managed"))
    if gone is not None:
        return gone
    lines: list[str] = []
    good = _restore_settings(ctx, doc.get("settings") or {})
    if good:
        good = _restore_managed(ctx, doc.get("managed"), lines)
    lines.append(
        "project settings restored to the state of the snapshot"
        if good
        else "project settings could not be written back"
    )
    for eid, saved in (doc.get("envs") or {}).items():
        if isinstance(saved, dict):
            good &= _restore_packages(ctx, str(eid), saved, lines)
    text = "\n".join(lines)
    gone = _remembered_target_gone(ctx)
    if gone is not None:
        return gone
    if not good:
        return err("rollback_incomplete", "The rollback did not fully succeed:\n" + text)
    res = ok(f"Rolled back to {snap_id}:\n{text}", "rolled back")
    res.mutated = True
    return res
