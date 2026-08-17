"""脚本注册表：stem（输出文件名主干）↔ 产出它的 matplotlib 脚本。

注册表数据随图库走——从 <figures_dir>/mm_registry.json 加载，本模块只负责
解析、校验与索引；没有注册表文件的图库由 engine/discover.py 静态扫描起草
（app 启动时自动做，也可手动 `python -m magplot.engine.discover <figures_dir> --write`），
静态解不出 stem 的脚本再由 engine/probe.py 试运行按真实产出登记。

约定：
  * 重复 stem 直接报错（防归属冲突悄悄回归；Fig11_xps_chemistry* 归属
    fig11_xps_c1s_analysis.py 的裁决记录在注册表文件里，勿改）
  * entry 是入口函数名，或 "__main__"（内联脚本）。worker 就是
    `getattr(module, entry)()`，所以任何合法标识符都行——把它锁死成
    main/render 只会让「按自己习惯命名入口」的图库整个用不了
  * script 键是**图库相对路径**（POSIX 分隔符），子目录里的脚本照样登记
  * cost: "light" 秒级 | "medium" 十秒级 | "heavy" 分钟级（冷启动，
    会话建立后 override 均为亚秒级）
  * notes: "3d" = 仅文字类元素可编辑；"dead" = 产物已不在磁盘

**一个进程可以同时端着多个项目的注册表**（不同标签页各开各的图库），所以
索引数据装在 Registry 实例里；模块级函数代理到一个默认实例，保持老调用方式。

纯标准库，Flask 父进程可安全 import。
"""
from __future__ import annotations

import json
from pathlib import Path

REGISTRY_NAME = "mm_registry.json"
INLINE_ENTRY = "__main__"
# 历史上的三方言，仍是 discover 的首选顺序；校验不再限定在这三个之内
KNOWN_ENTRIES = ("main", "render", INLINE_ENTRY)
VALID_COSTS = {"light", "medium", "heavy"}


def valid_entry(entry: object) -> bool:
    return isinstance(entry, str) and (entry == INLINE_ENTRY or entry.isidentifier())


def registry_path(figures_dir: str | Path) -> Path:
    return Path(figures_dir) / REGISTRY_NAME


class Registry:
    """一个图库的 stem↔script 索引。"""

    def __init__(self) -> None:
        self._scripts: dict[str, dict] = {}
        self._index: dict[str, str] = {}
        self._source: str = "<未加载>"

    # ---------------- 装载 ----------------
    def load(self, figures_dir: str | Path) -> Path:
        """读取并校验图库目录下的注册表；文件缺失抛 FileNotFoundError。"""
        path = registry_path(figures_dir)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise FileNotFoundError(f"注册表不存在: {path}") from exc
        except ValueError as exc:
            raise RuntimeError(f"注册表不是合法 JSON: {path}: {exc}") from exc
        self.load_data(data, source=str(path))
        return path

    def load_data(self, data: dict, source: str = "<memory>") -> None:
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
            if not valid_entry(entry):
                raise RuntimeError(f"{source}: {script} entry 非法: {entry!r}"
                                   f"（入口函数名，或 {INLINE_ENTRY!r} 表示内联脚本）")
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
        self._scripts, self._index, self._source = cleaned, index, source

    # ---------------- 查询 ----------------
    def loaded(self) -> bool:
        return bool(self._scripts)

    def source(self) -> str:
        return self._source

    def for_stem(self, stem: str) -> dict | None:
        """按输出 stem 反查脚本信息；不可参数化的面板返回 None。"""
        script = self._index.get(stem)
        if script is None:
            return None
        cfg = self._scripts[script]
        return {"script": script, "entry": cfg["entry"], "cost": cfg["cost"],
                "notes": cfg["notes"]}

    def all_scripts(self) -> list[str]:
        return list(self._scripts)

    def stems_of(self, script: str) -> list[str]:
        return list(self._scripts.get(script, {}).get("stems", []))

    def entries(self) -> dict[str, dict]:
        """完整表（界面上的「脚本注册表」面板用）。"""
        return {k: dict(v) for k, v in self._scripts.items()}


def open_registry(figures_dir: str | Path) -> Registry:
    """新建一个实例并装载（多项目并存时每个项目各持一个）。"""
    reg = Registry()
    reg.load(figures_dir)
    return reg


# --------------------------------------------------------------------------
# 模块级默认实例：单项目时代的调用方式原样保留
# --------------------------------------------------------------------------
_DEFAULT = Registry()


def default() -> Registry:
    return _DEFAULT


def load(figures_dir: str | Path) -> Path:
    return _DEFAULT.load(figures_dir)


def load_data(data: dict, source: str = "<memory>") -> None:
    _DEFAULT.load_data(data, source=source)


def loaded() -> bool:
    return _DEFAULT.loaded()


def source() -> str:
    return _DEFAULT.source()


def for_stem(stem: str) -> dict | None:
    return _DEFAULT.for_stem(stem)


def all_scripts() -> list[str]:
    return _DEFAULT.all_scripts()


def stems_of(script: str) -> list[str]:
    return _DEFAULT.stems_of(script)
