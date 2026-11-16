"""脚本运行时从项目外加载的本地模块的持久记录（诊断包「脚本源码」用，纯标准库）。

问题：脚本先 `sys.path.insert(0, 项目外的公共目录)` 再 `from shared_plot import draw`，真正出错的 `shared_plot.py`
不在项目文件夹里，诊断包的「脚本源码」只收项目内的脚本，维护者看不到它。渲染进程在每次运行后把「脚本自己加进
`sys.path` 的项目外目录里实际加载过的本地 `.py` 模块」带回来（`figcapture.observed_external_modules`），本模块把它们
按 (项目, 脚本的项目相对路径) 记进**应用数据目录**里的一个小 JSON。持久化是必需的：用户常常重启应用之后才发反馈包，
那时本次会话里根本没跑过那个脚本。

纪律：只写应用数据目录，绝不写进项目文件夹；原子写；有界（每项目 ≤ 64 个脚本、每脚本 ≤ 20 个模块、保留 30 天、
至多 200 个项目）；读失败当没有，**任何失败都不许拖垮渲染**（`note` 永不抛）。记录只是「候选」：诊断包收集时父进程
会重新校验每一个路径（`diagdetail.collect_source`），这里不信、也不替它判。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from . import config

STORE_NAME = "diag-external-modules.json"
SCHEMA = 1
MAX_SCRIPTS_PER_PROJECT = 64
MAX_MODULES_PER_SCRIPT = 20
MAX_PROJECTS = 200
RETAIN_S = 30 * 24 * 3600
#: 内容没变时，记录的时间戳至少隔这么久才刷新（避免每次渲染都写盘）
REFRESH_S = 24 * 3600
#: 单个路径字符串的长度上限（畸形输入不进存储）
MAX_PATH_LEN = 1024

_LOCK = threading.Lock()


def store_path() -> Path:
    return config.data_path(STORE_NAME)


def project_key(figures_dir: str | os.PathLike) -> str:
    """项目 → 短键。与池 / `app._project_id` 同一个路径身份判据（`config.normalize_path_identity`）。"""
    try:
        resolved = str(Path(figures_dir).expanduser().resolve())
    except (OSError, RuntimeError):
        resolved = str(figures_dir)
    return hashlib.sha1(config.normalize_path_identity(resolved).encode("utf-8")).hexdigest()[:12]


def script_key(figures_dir: str | os.PathLike, script: str) -> str | None:
    """脚本 → 项目相对的 POSIX 写法；在项目外或写法不对回 None。"""
    if not isinstance(script, str) or not script or "\x00" in script:
        return None
    p = Path(script)
    try:
        if p.is_absolute():
            p = p.resolve().relative_to(Path(figures_dir).resolve())
    except (OSError, ValueError, RuntimeError):
        return None
    key = p.as_posix()
    return None if key.startswith("..") else key


def _load(path: Path) -> dict:
    """读存储；不存在 / 坏了 / 形状不对一律当空（绝不抛）。"""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    projects = (
        doc.get("projects") if isinstance(doc, dict) and doc.get("schema") == SCHEMA else None
    )
    return projects if isinstance(projects, dict) else {}


def _prune(projects: dict, now: float) -> dict:
    """丢过期 / 畸形的条目并施加上界；返回新字典。"""
    out: dict = {}
    for pk, scripts in projects.items():
        if not isinstance(pk, str) or not isinstance(scripts, dict):
            continue
        kept = {}
        for sk, entry in scripts.items():
            if not isinstance(sk, str) or not isinstance(entry, dict):
                continue
            t, mods = entry.get("t"), entry.get("m")
            if not isinstance(t, (int, float)) or isinstance(t, bool) or now - t > RETAIN_S:
                continue
            if not isinstance(mods, list):
                continue
            mods = [m for m in mods if isinstance(m, str) and m and len(m) <= MAX_PATH_LEN]
            if mods:
                kept[sk] = {"t": t, "m": mods[:MAX_MODULES_PER_SCRIPT]}
        if kept:
            newest = sorted(kept.items(), key=lambda kv: -kv[1]["t"])[:MAX_SCRIPTS_PER_PROJECT]
            out[pk] = dict(newest)
    if len(out) > MAX_PROJECTS:
        newest_projects = sorted(out.items(), key=lambda kv: -max(e["t"] for e in kv[1].values()))
        out = dict(newest_projects[:MAX_PROJECTS])
    return out


def _write(path: Path, projects: dict) -> None:
    """原子写：同目录临时文件 → `os.replace`。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".diag-external-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"schema": SCHEMA, "projects": projects}, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def note(
    figures_dir: str | os.PathLike,
    script: str,
    modules: object,
    *,
    now: float | None = None,
) -> None:
    """记下这个脚本这一次运行加载过的项目外模块（绝对路径列表）。**最近一次运行为准**：空列表 = 清掉这个脚本的记录。
    永不抛。没有记录、也没有可记的（绝大多数运行）时不碰磁盘。"""
    try:
        sk = script_key(figures_dir, script)
        if sk is None:
            return
        mods = (
            [m for m in modules if isinstance(m, str) and m and len(m) <= MAX_PATH_LEN]
            if isinstance(modules, list)
            else []
        )[:MAX_MODULES_PER_SCRIPT]
        t = time.time() if now is None else now
        pk = project_key(figures_dir)
        path = store_path()
        with _LOCK:
            projects = _prune(_load(path), t)
            entry = (projects.get(pk) or {}).get(sk)
            if not mods and entry is None:
                return
            if entry is not None and entry["m"] == mods and t - entry["t"] < REFRESH_S:
                return
            scripts = dict(projects.get(pk) or {})
            if mods:
                scripts[sk] = {"t": t, "m": mods}
            else:
                scripts.pop(sk, None)
            projects[pk] = scripts
            _write(path, _prune(projects, t))
    except Exception:  # noqa: BLE001 — 记录只是诊断的辅助，绝不拖垮渲染
        return


def modules_for(
    figures_dir: str | os.PathLike, script: str, *, now: float | None = None
) -> list[str]:
    """这个脚本最近一次记下的项目外模块（未校验的候选路径；过期的不给）。读失败 = 没有。"""
    try:
        sk = script_key(figures_dir, script)
        if sk is None:
            return []
        t = time.time() if now is None else now
        entry = (_prune(_load(store_path()), t).get(project_key(figures_dir)) or {}).get(sk)
        return list(entry["m"]) if entry else []
    except Exception:  # noqa: BLE001
        return []
