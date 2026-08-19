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

#: 「这个卷大小写敏感吗」的探测结果，按绝对路径缓存（探测要 stat，而问它的
#: 那两个地方在热路径上）。
_CASEFOLD_CACHE: dict[str, bool] = {}


def _casefold_default() -> bool:
    """探不动时的兜底：Windows 与 macOS 的默认文件系统都是大小写不敏感的。"""
    return os.name == "nt" or sys.platform == "darwin"


def path_is_case_insensitive(path: str | Path) -> bool:
    """`path` 所在的卷是不是**大小写不敏感**（用于把路径归一化成项目身份）。

    **不能按 `os.name` 判。** macOS 默认的 APFS/HFS+ 同样是大小写不敏感、
    大小写保留的，而那里 `os.name` 是 `"posix"`；Linux 上也可能挂着
    exFAT/NTFS。按平台硬编码的后果是：同一个图库用不同大小写的路径打开两次
    （从 Finder 拖进来一次、从「最近项目」里手输一次就够）会被当成**两个
    互不知情的项目**——各自的 worker 池、各自的 `baked_overrides/<项目id>.json`
    写回基线，用户在一边做的事另一边完全看不见。
    `Path.resolve()` 在 POSIX 上只解析符号链接与 `.`/`..`，不会向文件系统
    问「规范大小写是什么」，所以它救不了这件事。

    探测法：把路径最后一段的大小写整体翻转，看是否仍指向同一个 inode。
    路径不存在就向上找最近的存在的祖先；实在探不动（没有字母、权限不够）
    按平台惯例兜底。
    """
    key = os.path.abspath(os.fspath(path))
    cached = _CASEFOLD_CACHE.get(key)
    if cached is not None:
        return cached

    result = _casefold_default()
    probe = key
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:                     # 到根了还不存在
            probe = ""
            break
        probe = parent
    if probe:
        head, tail = os.path.split(probe)
        flipped = tail.swapcase()
        if flipped and flipped != tail:         # 有字母可翻才谈得上探测
            try:
                st = os.stat(probe)
                other = os.path.join(head, flipped)
                result = (os.path.exists(other)
                          and os.path.samestat(st, os.stat(other)))
            except OSError:
                pass                            # 权限/竞态：留兜底值
    _CASEFOLD_CACHE[key] = result
    return result


def normalize_path_identity(path: str | Path) -> str:
    """把路径变成**项目身份**用的规范串：大小写不敏感的卷上统一小写。

    `app._project_id()` 与 `pool._norm_dir()` 必须用同一份判断，否则一个
    认为是同一个项目、另一个认为是两个，池与写回基线当场对不上。
    """
    text = os.fspath(path)
    return text.lower() if path_is_case_insensitive(text) else text


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


#: 项目内的 Magplot 收纳目录名（画布 / 导出 / 版本历史都在里面）
PROJECT_STORE_DIRNAME = "magplotfile"


def project_store_dir(project: str | Path) -> Path:
    """`<项目>/magplotfile/` —— 与该项目相关的 Magplot 文件统一收纳处。"""
    return Path(project) / PROJECT_STORE_DIRNAME


def project_export_dir(project: str | Path | None,
                       fallback: Path | None = None) -> Path:
    """项目的导出目录（项目设置可覆盖）。**规则的唯一出处**。

    缺省 `<项目>/magplotfile/export/`——成图要交给投稿/合作者，跟着项目走才
    找得到。项目目录建不出来（只读、网络盘）退回 `fallback`；没有项目
    （纯文字/形状导出）也用它。`fallback` 不给时是数据目录的 exports/。

    Flask 的 `app.project_export_dir()` 与 Codex 插件的 MCP server 都调这里：
    两条入口各写一份的话，用户会在两个地方找同一张图。**`fallback` 是参数而
    不是就地取常量**：app 侧的 `EXPORT_DIR` 是模块级常量，测试会 monkeypatch
    它，读死在这里会让那些用例静默写到真实数据目录。
    """
    fallback = fallback if fallback is not None else data_path("exports")
    if project is None:
        return fallback
    configured = project_settings(str(project)).get("export_dir")
    if configured:
        return Path(configured).expanduser()
    store = project_store_dir(project) / "export"
    try:
        store.mkdir(parents=True, exist_ok=True)
        return store
    except OSError:
        return fallback


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
