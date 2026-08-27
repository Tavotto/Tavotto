"""Magplot 0.7 → Tavotto 的用户数据迁移（`tavotto doctor --migrate`）。

2026-08-20 的改名选的是**干净断裂**（见 `engine/brand.py`）：运行时不认任何
Magplot 时代的键与路径，brand 模块里也没有 LEGACY_ 常量。但「不做运行时
兼容」≠「让最早那批用户丢掉工作」——1.0 审计（P1-08）要求给 0.7.x 用户一条
产品化的迁移路：这份模块就是当时说的那个「一次性转换脚本」，Magplot 的
旧目录名只活在这里，别处永远不 import 它做运行时判断。

原则（每一条都是审计的退出条件）：

* **只复制，绝不动旧数据**——Magplot 的目录一个字节不改不删，回滚天然成立；
* **绝不覆盖**——目标位置已有同名文件时跳过并记进冲突报告，两边内容一致时
  记成 `identical`（不算冲突）；
* **幂等**——重跑一遍的动作数为零（复制目标都已存在）；
* **dry-run**——`--dry-run` 输出完整计划，一个字节不写；
* **可回滚**——迁移报告（`<数据目录>/migration/from-magplot.json`）逐条记录
  本次创建的路径，`--rollback` 按报告删除且只删报告里的（旧数据无关）。

迁什么 / 不迁什么：

* 配置 `config.json`：**合并**——recent_projects 取并集（Tavotto 现有的
  在前）、projects 逐项补缺、其余顶层键只在 Tavotto 侧缺失时补入；
* 数据目录的 layouts/（含 _versions、_autosave、_styles.json）、
  baked_overrides/ 与旧全局 baked_overrides.json、ai_history.sqlite3、
  ai_snapshots/：原样复制；
* cache/ 不迁（全部可再生），exports 不迁（0.7 的导出在项目侧或用户自选
  目录，数据目录里没有权威份）。

图库侧（用户自己磁盘上的项目）**不碰**：`mm_registry.json` 读取端本来就
兼容（`registry.existing_registry_path()`），写出时自动落新名——那是唯二
运行时回退之一，语义不变。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import config

REPORT_REL = os.path.join("migration", "from-magplot.json")

#: 数据目录里值得带走的条目（相对路径）。cache/session 刻意不在。
_DATA_ITEMS = (
    "layouts",
    "baked_overrides",
    "baked_overrides.json",
    "ai_history.sqlite3",
    "ai_snapshots",
)


# ------------------------------ 旧目录定位 ---------------------------------
def legacy_config_dir(platform: str | None = None, environ: dict | None = None) -> Path:
    """Magplot 0.7 的用户配置目录（形状与 config.config_dir 逐条对应）。"""
    env = os.environ if environ is None else environ
    override = env.get("TAVOTTO_MIGRATE_LEGACY_CONFIG_DIR")
    if override:
        return Path(override)
    plat = platform or sys.platform
    if plat == "darwin":
        return Path.home() / "Library" / "Application Support" / "Magplot"
    if plat.startswith("win") or os.name == "nt":
        return Path(env.get("APPDATA", str(Path.home()))) / "Magplot"
    base = env.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "magplot"


def legacy_data_dir(platform: str | None = None, environ: dict | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("TAVOTTO_MIGRATE_LEGACY_DATA_DIR")
    if override:
        return Path(override)
    plat = platform or sys.platform
    if plat == "darwin":
        return Path.home() / "Library" / "Application Support" / "Magplot"
    if plat.startswith("win") or os.name == "nt":
        base = env.get("LOCALAPPDATA") or env.get("APPDATA")
        return Path(base or Path.home()) / "Magplot"
    base = env.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(base) / "magplot"


def legacy_found() -> bool:
    """有没有可迁移的 Magplot 数据（doctor 体检项与界面提示的判据）。"""
    return legacy_config_dir().is_dir() or legacy_data_dir().is_dir()


def report_path() -> Path:
    return config.data_dir() / REPORT_REL


# ------------------------------ 计划与执行 ---------------------------------
def _walk_files(root: Path) -> list[Path]:
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.startswith("."):  # .DS_Store 之流不值得带走
                continue
            out.append(Path(base) / name)
    return sorted(out)


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def build_plan() -> dict:
    """迁移计划：copy / skip（原因逐条给）。只读，不写任何东西。"""
    plan: dict = {
        "legacy_config_dir": str(legacy_config_dir()),
        "legacy_data_dir": str(legacy_data_dir()),
        "target_config_dir": str(config.config_dir()),
        "target_data_dir": str(config.data_dir()),
        "copies": [],
        "conflicts": [],
        "identical": [],
        "config_merge": None,
        "nothing_to_migrate": False,
    }

    lcfg = legacy_config_dir() / "config.json"
    if lcfg.is_file():
        plan["config_merge"] = str(lcfg)

    ldata = legacy_data_dir()
    tdata = config.data_dir()
    for item in _DATA_ITEMS:
        src = ldata / item
        if src.is_file():
            files = [src]
        elif src.is_dir():
            files = _walk_files(src)
        else:
            continue
        for f in files:
            # 计划与报告里的相对路径一律 POSIX 形式：报告是回滚的账本，
            # 格式必须跨平台稳定（Windows 的反斜杠会让同一份账本两种写法）
            rel = f.relative_to(ldata).as_posix()
            dst = tdata / rel
            if dst.exists():
                bucket = "identical" if _same_bytes(f, dst) else "conflicts"
                plan[bucket].append(rel)
            else:
                plan["copies"].append(rel)

    if (
        plan["config_merge"] is None
        and not plan["copies"]
        and not plan["conflicts"]
        and not plan["identical"]
    ):
        plan["nothing_to_migrate"] = True
    return plan


def _merge_config(legacy_path: Path, report: dict) -> None:
    """合并旧 config.json：Tavotto 现有内容永远优先，只补缺。"""
    try:
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report["config"] = {"merged": False, "reason": f"旧配置读不出来: {exc}"}
        return
    if not isinstance(legacy, dict):
        report["config"] = {"merged": False, "reason": "旧配置不是 JSON 对象"}
        return

    target_path = config.config_path()
    current: dict = {}
    if target_path.is_file():
        try:
            current = json.loads(target_path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            # Tavotto 自己的配置坏了不归迁移管，也绝不敢覆盖它
            report["config"] = {"merged": False, "reason": "Tavotto 配置存在但读不出来，跳过合并"}
            return

    added: dict = {"recent_projects": 0, "projects": 0, "top_level_keys": []}
    merged = dict(current)
    recents = list(current.get("recent_projects") or [])
    for p in legacy.get("recent_projects") or []:
        if isinstance(p, str) and p not in recents:
            recents.append(p)
            added["recent_projects"] += 1
    if recents:
        merged["recent_projects"] = recents
    projects = dict(current.get("projects") or {})
    for key, val in (legacy.get("projects") or {}).items():
        if key not in projects:
            projects[key] = val
            added["projects"] += 1
    if projects:
        merged["projects"] = projects
    for key, val in legacy.items():
        if key in ("recent_projects", "projects"):
            continue
        if key not in merged:
            merged[key] = val
            added["top_level_keys"].append(key)

    if merged != current:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = target_path.with_name(target_path.name + ".tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target_path)
        report["created_config_backup"] = None  # 只补缺，没有被覆盖的内容
    report["config"] = {"merged": True, "added": added, "changed": merged != current}


def execute(dry_run: bool = False) -> dict:
    """按计划执行；返回报告（dry_run 时只带计划）。"""
    plan = build_plan()
    report: dict = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "dry_run": dry_run,
        "plan": plan,
        "created": [],
        "config": None,
    }
    if plan["nothing_to_migrate"] or dry_run:
        return report

    ldata = legacy_data_dir()
    tdata = config.data_dir()
    for rel in plan["copies"]:
        src = ldata / rel
        dst = tdata / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():  # 计划与执行之间冒出来的，绝不覆盖
            plan["conflicts"].append(rel)
            continue
        shutil.copy2(src, dst)
        report["created"].append(rel)

    if plan["config_merge"]:
        _merge_config(Path(plan["config_merge"]), report)

    # 报告是回滚的账本：什么都没做的重跑**不许**盖掉它——盖了的话第一次
    # 迁移创建了哪些文件就没人记得，--rollback-migration 从此失灵。
    config_changed = bool((report.get("config") or {}).get("changed"))
    if report["created"] or config_changed or not report_path().is_file():
        rp = report_path()
        rp.parent.mkdir(parents=True, exist_ok=True)
        tmp = rp.with_name(rp.name + ".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(rp)
    return report


def rollback() -> dict:
    """按上次迁移报告删除**本次创建的**文件；旧 Magplot 数据从头到尾没动过。

    只删报告里逐条记录的相对路径（含清空后的空目录）；配置合并只补缺、
    不覆盖，回滚不去动 config.json——补进去的键对新装的 Tavotto 无害，
    真要清理由用户在设置里改。
    """
    rp = report_path()
    if not rp.is_file():
        return {"rolled_back": False, "reason": "没有迁移报告，无可回滚"}
    try:
        report = json.loads(rp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"rolled_back": False, "reason": f"迁移报告读不出来: {exc}"}
    tdata = config.data_dir()
    removed, missing = [], []
    for rel in report.get("created") or []:
        p = tdata / rel
        try:
            p.unlink()
            removed.append(rel)
        except FileNotFoundError:
            missing.append(rel)
        except OSError:
            missing.append(rel)
        # 清空的目录顺手收掉（失败无妨）
        parent = p.parent
        while parent != tdata:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    try:
        rp.unlink()
    except OSError:
        pass
    return {"rolled_back": True, "removed": removed, "not_found": missing}
