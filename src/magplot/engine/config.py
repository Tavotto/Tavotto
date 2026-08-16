"""用户级持久配置与可写数据目录（纯标准库，Flask 父进程 import）。

配置目录（最近项目、每项目设置、CLI 路径等用户级偏好）：
  macOS   ~/Library/Application Support/Magplot/config.json
  Linux   $XDG_CONFIG_HOME/magplot/config.json（缺省 ~/.config/magplot/）
  Windows %APPDATA%/Magplot/config.json

数据目录（渲染缓存、布局、AI 快照、原图备份等运行时产物）：
  macOS   ~/Library/Application Support/Magplot/
  Linux   $XDG_DATA_HOME/magplot/（缺省 ~/.local/share/magplot/）
  Windows %LOCALAPPDATA%/Magplot/

装成 pip 包后 site-packages 不可写，运行时产物一律落在数据目录；
测试可用 MAGPLOT_CONFIG_DIR / MAGPLOT_DATA_DIR 分别重定向。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

RECENT_KEEP = 20

_LOCK = threading.Lock()


def config_dir() -> Path:
    override = os.environ.get("MAGPLOT_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Magplot"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "Magplot"
    base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "magplot"


def config_path() -> Path:
    return config_dir() / "config.json"


def data_dir() -> Path:
    """可写数据根目录（运行时产物）。macOS 上与配置目录同址，符合平台惯例。"""
    override = os.environ.get("MAGPLOT_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Magplot"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base or Path.home()) / "Magplot"
    base = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(base) / "magplot"


def data_path(*parts: str) -> Path:
    """数据目录下的子路径（只拼路径，不建目录——写入方各自 mkdir）。"""
    return data_dir().joinpath(*parts)


def _defaults() -> dict:
    return {"recent_projects": [], "projects": {}, "ai": {}, "updates": {},
            "worker": {}}


def load() -> dict:
    """读配置；缺失/损坏一律回默认值（损坏文件不覆盖，等下次 save）。"""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _defaults()
    if not isinstance(data, dict):
        return _defaults()
    out = _defaults()
    if isinstance(data.get("recent_projects"), list):
        out["recent_projects"] = [
            e for e in data["recent_projects"]
            if isinstance(e, dict) and isinstance(e.get("path"), str)
        ]
    if isinstance(data.get("projects"), dict):
        out["projects"] = data["projects"]
    if isinstance(data.get("ai"), dict):
        out["ai"] = data["ai"]
    if isinstance(data.get("updates"), dict):
        out["updates"] = data["updates"]
    if isinstance(data.get("worker"), dict):
        out["worker"] = data["worker"]
    return out


def save(cfg: dict) -> None:
    """临时文件 + replace 原子落盘；目录不存在自动创建。"""
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = config_path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def touch_recent(path: str, name: str | None = None) -> None:
    """把项目提到最近列表首位（读-改-写持锁）。"""
    with _LOCK:
        cfg = load()
        path = str(Path(path))
        entry = {"path": path, "name": name or Path(path).name,
                 "last_opened": int(time.time() * 1000)}
        cfg["recent_projects"] = (
            [entry] + [e for e in cfg["recent_projects"] if e["path"] != path]
        )[:RECENT_KEEP]
        save(cfg)


def remove_recent(path: str) -> bool:
    """从最近列表移除（不动磁盘上的项目内容）。"""
    with _LOCK:
        cfg = load()
        kept = [e for e in cfg["recent_projects"] if e["path"] != str(Path(path))]
        removed = len(kept) != len(cfg["recent_projects"])
        if removed:
            cfg["recent_projects"] = kept
            save(cfg)
        return removed


def recent_projects() -> list[dict]:
    return load()["recent_projects"]


def last_project() -> str | None:
    recent = recent_projects()
    return recent[0]["path"] if recent else None


def project_settings(path: str) -> dict:
    """每项目设置（写回权限 / 备份目录等）；缺省全空 dict。"""
    return load()["projects"].get(str(Path(path)), {})


def set_project_settings(path: str, patch: dict) -> dict:
    with _LOCK:
        cfg = load()
        key = str(Path(path))
        merged = {**cfg["projects"].get(key, {}), **patch}
        # 置 None 的键视为清除
        merged = {k: v for k, v in merged.items() if v is not None}
        cfg["projects"][key] = merged
        save(cfg)
        return merged


def worker_python() -> str | None:
    """用户指定或 Magplot 自建的渲染解释器（绝对路径）。"""
    return (load().get("worker") or {}).get("python") or None


def set_worker_python(path: str | None) -> None:
    with _LOCK:
        cfg = load()
        worker = dict(cfg.get("worker") or {})
        if path:
            worker["python"] = str(path)
        else:
            worker.pop("python", None)
        cfg["worker"] = worker
        save(cfg)


def ai_settings() -> dict:
    """AI CLI 的用户级设置（自定义可执行路径等）。"""
    return load()["ai"]


def set_ai_settings(patch: dict) -> dict:
    with _LOCK:
        cfg = load()
        merged = {**cfg["ai"], **patch}
        merged = {k: v for k, v in merged.items() if v is not None}
        cfg["ai"] = merged
        save(cfg)
        return merged
