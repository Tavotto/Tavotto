"""推荐配色目录（ADR 0210）：加载、校验、按许可分档。**色值的唯一权威是两份 JSON**：

    src/tavotto/profiles/palettes.json            许可宽松（MIT / BSD / Apache / 事实性资料）
    src/tavotto/profiles/palettes_copyleft.json   GPL 的期刊风格色板（ggsci）

前端经 `@palettes` / `@palettesCopyleft` 别名把同一份文件整份 import 进 bundle，本模块给引擎侧
（测试、以后的 MCP 工具）读同一份；两侧都不另抄色值。重新生成见 `scripts/dev/gen_palettes.py`。

**为什么分两份文件**：开源版是 AGPL，收 GPL-3.0 的色值没有问题；闭源高级版组件（ADR 0198）不许带
GPL 的东西。拆成两个文件，闭源那边只许读宽松的那份——边界是文件，不是一个容易漏判的字段。
`catalog(include_copyleft=False)` 与前端构建的 `TAVOTTO_NO_COPYLEFT_PALETTES=1`（换成空表）是同一条线。

纯标准库：Flask 父进程与 MCP server 都可以 import 它。
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

#: 开发态兜底：engine/ 的上一级就是包目录（装成 wheel 后走 `palettes_path` 里的 `importlib.resources`）
PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"
PERMISSIVE_FILE = "palettes.json"
COPYLEFT_FILE = "palettes_copyleft.json"

#: 设成 1 = 不收 GPL 的期刊风格色板（与前端构建同名开关，见模块说明）
NO_COPYLEFT_ENV = "TAVOTTO_NO_COPYLEFT_PALETTES"

#: 宽松那份里允许出现的许可（SPDX 写法；`LicenseRef-Factual` = 公开的事实性色值，如 Okabe–Ito）
PERMISSIVE_LICENSES = frozenset(
    {"MIT", "BSD-3-Clause", "Apache-2.0", "CC0-1.0", "LicenseRef-Factual"}
)
#: copyleft 那份里允许出现的许可
COPYLEFT_LICENSES = frozenset({"GPL-3.0-or-later", "GPL-3.0-only"})

#: 第一期只有分类色板；数值色图走现有的 cmap 白名单，目录色图注册是第二期（ADR 0210 §六）
KINDS = frozenset({"categorical"})

#: 一套方案至少几个颜色才算一套（再少就不是「配色」了）
MIN_COLORS = 3

_HEX = re.compile(r"^#[0-9A-F]{6}$")
_ID = re.compile(r"^[a-z0-9][a-zA-Z0-9.-]*$")


class PaletteCatalogError(ValueError):
    """目录文件不合规（CI 里由测试兜住，运行时读到坏文件也不静默吞掉）。"""


def palettes_path(name: str) -> Path:
    """目录文件的位置：包内 → 源码树（同 `profiles.profiles_path`；装成 wheel 后源码树的相对路径不一定在）。"""
    try:
        from importlib.resources import files

        cand = Path(str(files("tavotto").joinpath("profiles", name)))
        if cand.is_file():
            return cand
    except (ImportError, ModuleNotFoundError, TypeError, OSError):
        pass
    return PROFILES_DIR / name


def _read(name: str) -> dict:
    path = palettes_path(name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaletteCatalogError(f"{name}: {exc}") from exc


def validate(doc: dict, *, copyleft: bool, name: str = "<palettes>") -> None:
    """一份目录文件的结构与许可判据。不过就抛 `PaletteCatalogError`，消息说清是哪一条。"""
    if doc.get("version") != 1:
        raise PaletteCatalogError(f"{name}: version 必须是 1")
    if bool(doc.get("copyleft")) is not copyleft:
        raise PaletteCatalogError(f"{name}: copyleft 标记与文件不符")
    allowed = COPYLEFT_LICENSES if copyleft else PERMISSIVE_LICENSES
    sources = doc.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise PaletteCatalogError(f"{name}: 缺 sources")
    for sid, src in sources.items():
        lic = src.get("license") if isinstance(src, dict) else None
        if lic not in allowed:
            raise PaletteCatalogError(f"{name}: 来源 {sid} 的许可 {lic!r} 不能放进这份文件")
        if not src.get("name") or not src.get("url"):
            raise PaletteCatalogError(f"{name}: 来源 {sid} 缺 name / url")
    neutral = doc.get("neutral")
    if neutral is not None and not (isinstance(neutral, str) and _HEX.match(neutral)):
        raise PaletteCatalogError(f"{name}: neutral 不是 #RRGGBB")
    seen: set[str] = set()
    palettes = doc.get("palettes")
    if not isinstance(palettes, list) or not palettes:
        raise PaletteCatalogError(f"{name}: 缺 palettes")
    for p in palettes:
        pid = p.get("id")
        if not isinstance(pid, str) or not _ID.match(pid):
            raise PaletteCatalogError(f"{name}: 非法 id {pid!r}")
        if pid in seen:
            raise PaletteCatalogError(f"{name}: id {pid} 重复")
        seen.add(pid)
        if p.get("source") not in sources:
            raise PaletteCatalogError(f"{name}: {pid} 的来源 {p.get('source')!r} 没登记")
        if p.get("kind") not in KINDS:
            raise PaletteCatalogError(f"{name}: {pid} 的 kind {p.get('kind')!r} 不认识")
        if not isinstance(p.get("name"), str) or not p["name"].strip():
            raise PaletteCatalogError(f"{name}: {pid} 缺 name")
        colors = p.get("colors")
        if not isinstance(colors, list) or len(colors) < MIN_COLORS:
            raise PaletteCatalogError(f"{name}: {pid} 少于 {MIN_COLORS} 色")
        bad = [c for c in colors if not (isinstance(c, str) and _HEX.match(c))]
        if bad:
            raise PaletteCatalogError(f"{name}: {pid} 有非法色值 {bad}")
        if len(set(colors)) != len(colors):
            raise PaletteCatalogError(f"{name}: {pid} 有重复色值")
        nc = p.get("neutral")
        if nc is not None and not (isinstance(nc, str) and _HEX.match(nc)):
            raise PaletteCatalogError(f"{name}: {pid} 的 neutral 不是 #RRGGBB")


@lru_cache(maxsize=1)
def _load_permissive() -> dict:
    doc = _read(PERMISSIVE_FILE)
    validate(doc, copyleft=False, name=PERMISSIVE_FILE)
    return doc


@lru_cache(maxsize=1)
def _load_copyleft() -> dict:
    """GPL 那份：只在调用方要它时才读（排除时**不读、不验**，缺了这个文件的发行版照样能用宽松目录）。"""
    doc = _read(COPYLEFT_FILE)
    validate(doc, copyleft=True, name=COPYLEFT_FILE)
    clash = {p["id"] for p in _load_permissive()["palettes"]} & {p["id"] for p in doc["palettes"]}
    if clash:
        raise PaletteCatalogError(f"两份目录 id 重复：{sorted(clash)}")
    return doc


def _load() -> tuple[dict, dict]:
    """两份都读（目录自检用；运行时走 `catalog`，按开关只读需要的那份）。"""
    return _load_permissive(), _load_copyleft()


def _clear_cache() -> None:
    _load_permissive.cache_clear()
    _load_copyleft.cache_clear()


def copyleft_enabled() -> bool:
    """这次运行收不收 GPL 的期刊风格色板（`TAVOTTO_NO_COPYLEFT_PALETTES=1` = 不收）。"""
    return os.environ.get(NO_COPYLEFT_ENV, "").strip() != "1"


def catalog(*, include_copyleft: bool | None = None) -> dict:
    """合并后的目录：`{neutral, sources, palettes}`。每个方案带上它来源的 `license` 与 `copyleft`。

    `include_copyleft=None` 时按环境开关；闭源组件的调用方一律显式传 `False`。返回的是新对象，可以随便改。
    """
    if include_copyleft is None:
        include_copyleft = copyleft_enabled()
    # 先定开关再读：排除时 GPL 文件一个字节都不碰（文件级边界，profiles-and-preflight.md）
    permissive = _load_permissive()
    docs = [(permissive, False)] + ([(_load_copyleft(), True)] if include_copyleft else [])
    sources: dict[str, dict] = {}
    palettes: list[dict] = []
    for doc, is_copyleft in docs:
        for sid, src in doc["sources"].items():
            sources[sid] = {**src, "copyleft": is_copyleft}
        for p in doc["palettes"]:
            lic = doc["sources"][p["source"]]["license"]
            palettes.append(
                {**p, "colors": list(p["colors"]), "license": lic, "copyleft": is_copyleft}
            )
    return {"neutral": permissive.get("neutral"), "sources": sources, "palettes": palettes}


def palette(pid: str, *, include_copyleft: bool | None = None) -> dict | None:
    """按 id 取一套方案；没有（或被许可开关排除了）就是 None。"""
    for p in catalog(include_copyleft=include_copyleft)["palettes"]:
        if p["id"] == pid:
            return p
    return None
