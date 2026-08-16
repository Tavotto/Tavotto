"""脚本注册表：stem（输出文件名主干）↔ 产出它的 matplotlib 脚本。

注册表数据随图库走——从 <figures_dir>/mm_registry.json 加载，本模块只负责
解析、校验与索引；没有注册表文件的图库由 engine/discover.py 静态扫描起草
（app 启动时自动做，也可手动 `python -m engine.discover <figures_dir> --write`）。

约定与硬编码时代不变：
  * 重复 stem 直接报错（防归属冲突悄悄回归；Fig11_xps_chemistry* 归属
    fig11_xps_c1s_analysis.py 的裁决记录在注册表文件里，勿改）
  * entry 三方言：main / render / __main__（内联脚本）
  * cost: "light" 秒级 | "medium" 十秒级 | "heavy" 分钟级（冷启动，
    会话建立后 override 均为亚秒级）
  * notes: "3d" = 仅文字类元素可编辑；"dead" = 产物已不在磁盘

纯标准库，Flask 父进程可安全 import。
"""
from __future__ import annotations

import json
from pathlib import Path

REGISTRY_NAME = "mm_registry.json"
VALID_ENTRIES = {"main", "render", "__main__"}
VALID_COSTS = {"light", "medium", "heavy"}

_SCRIPTS: dict[str, dict] = {}
_STEM_INDEX: dict[str, str] = {}
_SOURCE: str = "<未加载>"


def registry_path(figures_dir: str | Path) -> Path:
    return Path(figures_dir) / REGISTRY_NAME


def load(figures_dir: str | Path) -> Path:
    """读取并校验图库目录下的注册表；文件缺失抛 FileNotFoundError。"""
    path = registry_path(figures_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FileNotFoundError(f"注册表不存在: {path}") from exc
    except ValueError as exc:
        raise RuntimeError(f"注册表不是合法 JSON: {path}: {exc}") from exc
    load_data(data, source=str(path))
    return path


def load_data(data: dict, source: str = "<memory>") -> None:
    """从 dict 装载（load 的内核；测试与草稿流程也直接用）。"""
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        raise RuntimeError(f"注册表缺少 scripts 表: {source}")
    cleaned: dict[str, dict] = {}
    index: dict[str, str] = {}
    for script, cfg in scripts.items():
        if not isinstance(cfg, dict):
            raise RuntimeError(f"{source}: {script} 的配置必须是对象")
        entry = cfg.get("entry", "main")
        if entry not in VALID_ENTRIES:
            raise RuntimeError(f"{source}: {script} entry 非法: {entry!r}"
                               f"（可选 {sorted(VALID_ENTRIES)}）")
        cost = cfg.get("cost", "medium")
        if cost not in VALID_COSTS:
            raise RuntimeError(f"{source}: {script} cost 非法: {cost!r}"
                               f"（可选 {sorted(VALID_COSTS)}）")
        stems = cfg.get("stems") or []
        if not isinstance(stems, list) or not all(isinstance(s, str) for s in stems):
            raise RuntimeError(f"{source}: {script} stems 必须是字符串列表")
        for stem in stems:
            if stem in index:
                raise RuntimeError(
                    f"stem 重复注册: {stem} ({index[stem]} vs {script})")
            index[stem] = script
        cleaned[script] = {"entry": entry, "cost": cost,
                           "notes": str(cfg.get("notes", "")),
                           "stems": list(stems)}
    global _SCRIPTS, _STEM_INDEX, _SOURCE
    _SCRIPTS, _STEM_INDEX, _SOURCE = cleaned, index, source


def loaded() -> bool:
    return bool(_SCRIPTS)


def source() -> str:
    return _SOURCE


def for_stem(stem: str) -> dict | None:
    """按输出 stem 反查脚本信息；不可参数化的面板返回 None。"""
    script = _STEM_INDEX.get(stem)
    if script is None:
        return None
    cfg = _SCRIPTS[script]
    return {"script": script, "entry": cfg["entry"], "cost": cfg["cost"],
            "notes": cfg["notes"]}


def all_scripts() -> list[str]:
    return list(_SCRIPTS)


def stems_of(script: str) -> list[str]:
    return list(_SCRIPTS.get(script, {}).get("stems", []))
