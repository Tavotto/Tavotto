"""同一张图的几份文件（ADR 0184 §一）：图键与主文件的**唯一出处**。

素材区的单位是「图」：同一张图可以同时有 PDF / SVG / PNG……几份文件，用户要排版的是图本身，
不想挑格式。这里只回答两件事，不碰磁盘、不调用户代码：

* `figure_key(rel, reg)`——这份文件属于哪张图；
* `pick_primary(entries)`——一张图的几份文件里，画布上用哪一份。

`/api/panels`（`app.scan_panels`）与就绪度共用它们；前端只按后端给的 `figure_key` / `primary` 归组，
不自己再切一次文件名。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath

#: 按格式分目录时常见的目录名（大小写不敏感）：`figures/pdf/fig1.pdf` 与 `figures/png/fig1.png` 是同一张图。
FORMAT_DIR_NAMES = frozenset({"pdf", "png", "svg", "jpg", "jpeg", "tif", "tiff"})

SCRIPT_PREFIX = "script:"
DIR_PREFIX = "dir:"

#: 主文件的格式次序：矢量优先（用户 2026-11-14 拍板「自动挑矢量」）。
_KIND_RANK = {"pdf": 0, "svg": 1, "raster": 2}


def _norm(rel: str) -> PurePosixPath:
    return PurePosixPath(str(rel).replace("\\", "/"))


def figure_key(rel: str, reg=None) -> str:
    """项目相对路径 → 图键。

    * 注册表认得（`Registry.for_artifact`，#1251 的唯一出处）：`script:<stem>`——stem → 脚本一对一，
      跨目录、跨格式的同一张图天然同键；
    * 认不得：`dir:<归一目录>/<文件名 stem>`。归一目录 = 所在目录；目录名本身是格式名时取上一级。
      不跨普通目录（`a/fig1.png` 与 `b/fig1.png` 可能是两张不同的图）。
    """
    p = _norm(rel)
    if reg is not None:
        try:
            info = reg.for_artifact(p.as_posix())
        except Exception:  # noqa: BLE001 —— 注册表读不动不该让素材清单失败：退回目录键
            info = None
        if info is not None:
            return SCRIPT_PREFIX + str(info["stem"])
    parent = p.parent
    if parent.name.lower() in FORMAT_DIR_NAMES:
        parent = parent.parent
    base = parent.as_posix()
    base = "" if base == "." else base
    return f"{DIR_PREFIX}{base}/{p.stem}" if base else f"{DIR_PREFIX}{p.stem}"


def _primary_rank(entry: Mapping) -> tuple:
    kind = str(entry.get("kind") or "")
    px = (entry.get("px_w") or 0) * (entry.get("px_h") or 0)
    return (_KIND_RANK.get(kind, 3), -px, str(entry.get("id") or ""))


def pick_primary(entries: Iterable[Mapping]) -> str | None:
    """一张图的几份文件（`/api/panels` 条目：`id` / `kind` / `px_w` / `px_h`）→ 主文件的 id。

    PDF > SVG > 位图；位图之间像素面积大者优先；再按路径，结果确定。空 → None。
    """
    ranked = sorted(entries, key=_primary_rank)
    return str(ranked[0]["id"]) if ranked else None


def annotate(panels: list[dict], reg=None) -> None:
    """给 `/api/panels` 的条目就地挂上 `figure_key`、`primary`（bool）与 `formats`（同图全部文件 id，按路径排序）。

    加字段协议：不删、不改任何已有字段，老前端照旧一份文件一张卡。
    """
    groups: dict[str, list[dict]] = {}
    for entry in panels:
        key = figure_key(str(entry["id"]), reg)
        entry["figure_key"] = key
        groups.setdefault(key, []).append(entry)
    for members in groups.values():
        primary = pick_primary(members)
        formats = sorted(str(m["id"]) for m in members)
        for m in members:
            m["primary"] = str(m["id"]) == primary
            m["formats"] = formats
