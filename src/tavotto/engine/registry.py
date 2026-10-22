"""脚本注册表：stem（输出文件名主干）↔ 产出它的 matplotlib 脚本。

注册表数据随图库走——从 <figures_dir>/tavotto_registry.json 加载，本模块只负责
解析、校验与索引；没有注册表文件的图库由 engine/discover.py 静态扫描起草
（app 启动时自动做，也可手动 `python -m tavotto.engine.discover <figures_dir> --write`），
静态解不出 stem 的脚本再由 engine/probe.py 试运行按真实产出登记。

约定：
  * 重复 stem 直接报错（防归属冲突悄悄回归；Fig11_xps_chemistry* 归属
    fig11_xps_c1s_analysis.py 的裁决记录在注册表文件里，勿改）
  * entry 是入口函数名，或 "__main__"（内联脚本）。worker 就是
    `getattr(module, entry)()`，所以任何合法标识符都行——把它锁死成
    main/render 只会让「按自己习惯命名入口」的图库整个用不了
  * script 键是**图库相对路径**（POSIX 分隔符），子目录里的脚本照样登记
  * cost: "light" 秒级 | "medium" 十秒级 | "heavy" 分钟级（冷启动，
    会话建立后 override 均为亚秒级）。它是**预期时长的标注**：界面用它给冷启动
    提示（`useServerEvents.ts` 的 coldHint）与面板角标，前端那条「请求是不是
    悬挂了」的看门狗按它取 2/5/15 分钟（`renderStore.ts` 的 WATCHDOG_MS）——
    三档都短于引擎的 20 分钟静默阈值，所以在画布上它事实上也是冷启动的总时长
    上限（到点前端断开请求，服务端 build 照常继续；有意保留，#312）。
    **引擎的 build 超时不看它**——那边按「worker.log 还在不在长」判卡死
    （ADR 0050 的静默看门狗，`pool.BUILD_IDLE_TIMEOUT` / `BUILD_HARD_TIMEOUT`），
    ADR 0048 的分档已删除，标 heavy 不再改变引擎侧任何超时
  * notes: "3d" = 仅文字类元素可编辑；"dead" = 产物已不在磁盘
  * artifacts（可选，#1251）：`{项目相对路径: stem}`，「磁盘上的这个文件是这个脚本里哪张图
    写的」。同一脚本里两张不同的图写同 stem 的不同文件（`panel.png` 甲 / `panel.pdf` 乙，
    乙以 `panel-2` 登记）时，文件名 stem 回答不了「这个文件是谁的」——磁盘文件 → 图的解析
    一律走 `for_artifact()`：先查这张表，查不到再退回按 stem。字段可缺（旧文件），旧版本
    读新文件时按未知键忽略（`load_data` 只取已知键）；表是**派生数据**，坏的条目（非字符串、
    stem 不在本脚本 stems 里、同一路径被两个脚本认领）静默丢弃，不让整份注册表打不开

**一个进程可以同时端着多个项目的注册表**（不同标签页各开各的图库），所以
索引数据装在 Registry 实例里；模块级函数代理到一个默认实例，保持老调用方式。

纯标准库，Flask 父进程可安全 import。
"""

from __future__ import annotations

import json
from pathlib import Path

REGISTRY_NAME = "tavotto_registry.json"
#: 改名前（Magic Matplot 时代起一直沿用）的文件名。
#:
#: 这是**读取端唯一的兼容点**：注册表不是我们的数据，它躺在用户自己的图库目录
#: 里，多半还被手工裁决过（一脚本多产物、重复 stem 的归属）。写出一律用新名，
#: 但新名不在时回退到它——否则用户打开一个老图库看到的是「这个目录不是图库」，
#: 而真相是文件名换了。**只读不写**：一旦按新名写过一次，新名即唯一权威。
LEGACY_REGISTRY_NAME = "mm_registry.json"
INLINE_ENTRY = "__main__"
# 历史上的三方言，仍是 discover 的首选顺序；校验不再限定在这三个之内
KNOWN_ENTRIES = ("main", "render", INLINE_ENTRY)
VALID_COSTS = {"light", "medium", "heavy"}


def valid_entry(entry: object) -> bool:
    return isinstance(entry, str) and (entry == INLINE_ENTRY or entry.isidentifier())


def registry_path(figures_dir: str | Path) -> Path:
    """**写入**用的路径——永远是新名，绝不回写旧名。"""
    return Path(figures_dir) / REGISTRY_NAME


def existing_registry_path(figures_dir: str | Path) -> Path | None:
    """磁盘上实际存在的那一份（新名优先），两个都没有时返回 None。

    「这个目录是不是图库」的判据只有这一个出处——handoff 的项目探测、
    discover 的合并、probe 的写回都得走它，各写各的必然分叉，而分叉的表现
    正是「界面认得这个图库、命令行说它不是」。
    """
    base = Path(figures_dir)
    for name in (REGISTRY_NAME, LEGACY_REGISTRY_NAME):
        p = base / name
        if p.is_file():
            return p
    return None


class Registry:
    """一个图库的 stem↔script 索引。"""

    def __init__(self) -> None:
        self._scripts: dict[str, dict] = {}
        self._index: dict[str, str] = {}
        #: 磁盘文件（项目相对路径，POSIX）→ stem；见模块说明的 artifacts
        self._artifacts: dict[str, str] = {}
        self._source: str = "<未加载>"

    # ---------------- 装载 ----------------
    def load(self, figures_dir: str | Path) -> Path:
        """读取并校验图库目录下的注册表；文件缺失抛 FileNotFoundError。

        新名不在时读旧名（见 `LEGACY_REGISTRY_NAME`）。
        """
        path = existing_registry_path(figures_dir) or registry_path(figures_dir)
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
        per_script: dict[str, dict[str, str]] = {}
        for script, cfg in scripts.items():
            if not isinstance(cfg, dict):
                raise RuntimeError(f"{source}: {script} 的配置必须是对象")
            entry = cfg.get("entry", "main")
            if not valid_entry(entry):
                raise RuntimeError(
                    f"{source}: {script} entry 非法: {entry!r}"
                    f"（入口函数名，或 {INLINE_ENTRY!r} 表示内联脚本）"
                )
            cost = cfg.get("cost", "medium")
            if cost not in VALID_COSTS:
                raise RuntimeError(
                    f"{source}: {script} cost 非法: {cost!r}（可选 {sorted(VALID_COSTS)}）"
                )
            stems = cfg.get("stems") or []
            if not isinstance(stems, list) or not all(isinstance(s, str) for s in stems):
                raise RuntimeError(f"{source}: {script} stems 必须是字符串列表")
            for stem in stems:
                if stem in index:
                    raise RuntimeError(f"stem 重复注册: {stem} ({index[stem]} vs {script})")
                index[stem] = script
            mine = clean_artifacts(cfg.get("artifacts"), stems)
            per_script[script] = mine
            cleaned[script] = {
                "entry": entry,
                "cost": cost,
                "notes": str(cfg.get("notes", "")),
                "stems": list(stems),
            }
        # 同一路径被两个脚本认领 = 歧义：整条丢掉（解析退回按文件名 stem），与脚本在 JSON 里的顺序无关
        artifacts = unambiguous_artifacts(per_script)
        for script, mine in per_script.items():
            mine = {a: s for a, s in mine.items() if a in artifacts}
            # 有才带：`entries()` 要与磁盘上的脚本条目同形——`project_refresh` 拿「登记时的条目」和
            # 「此刻的 entries()」比相等来判这次执行是否仍有效，少一个键会让活 worker 被当成过期收掉
            if mine:
                cleaned[script]["artifacts"] = dict(mine)
        self._scripts, self._index, self._source = cleaned, index, source
        self._artifacts = artifacts

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
        return {"script": script, "entry": cfg["entry"], "cost": cfg["cost"], "notes": cfg["notes"]}

    def for_artifact(self, rel: str) -> dict | None:
        """磁盘上的一个素材文件（项目相对路径）→ 产出它的那张图：`for_stem` 的字段加 `stem`。

        **「磁盘文件 → 图」的唯一解析出处**（#1251）：先查注册表登记的 `artifacts`（哪张图写了这个
        文件），查不到再退回按文件名 stem——老注册表、手写注册表、静态起草登记的图都没有这张表。
        不是「stem 的原件在哪」（那是 `figcapture.find_original_artifact`），方向相反。
        """
        key = str(rel).replace("\\", "/")
        stem = self._artifacts.get(key)
        if stem is None or stem not in self._index:
            stem = Path(key).stem
        info = self.for_stem(stem)
        return None if info is None else {**info, "stem": stem}

    def artifact_stem(self, rel: str) -> str:
        """磁盘文件 → 它属于哪个 stem：`for_artifact` 认得就用登记的，认不得退回文件名 stem。

        app 的 `_disk_panel_rel` 与 CLI 的 `handoff.resolve_target` **共用这一个函数**：app 记交接回执的键
        与 CLI 轮询回执的键必须逐字相同，两边各算一份迟早分叉（#1251）。
        """
        info = self.for_artifact(rel)
        return info["stem"] if info is not None else Path(str(rel).replace("\\", "/")).stem

    def artifacts_of(self, script: str) -> dict[str, str]:
        """某脚本登记的 `{文件: stem}`（只读副本）。"""
        stems = set(self._scripts.get(script, {}).get("stems", []))
        return {
            a: s for a, s in self._artifacts.items() if s in stems and self._index.get(s) == script
        }

    def all_scripts(self) -> list[str]:
        return list(self._scripts)

    def stems_of(self, script: str) -> list[str]:
        return list(self._scripts.get(script, {}).get("stems", []))

    def entries(self) -> dict[str, dict]:
        """完整表（界面上的「脚本注册表」面板用）。"""
        return {k: dict(v) for k, v in self._scripts.items()}


def clean_artifacts(raw: object, stems: list[str]) -> dict[str, str]:
    """`artifacts` 字段的宽容清洗：只留「字符串路径 → 本脚本 stems 里的 stem」，其余丢弃。

    读（`load_data`）与写（`discover.register`）共用这一份，两边对「什么算有效条目」不会分叉。
    """
    if not isinstance(raw, dict):
        return {}
    allowed = set(stems)
    out: dict[str, str] = {}
    clash: set[str] = set()
    for art, stem in raw.items():
        if isinstance(art, str) and art and isinstance(stem, str) and stem in allowed:
            key = art.replace("\\", "/")
            if out.setdefault(key, stem) != stem:
                clash.add(key)  # `a\b` 与 `a/b` 归一后指向不同的图：歧义，不记
    return {a: s for a, s in out.items() if a not in clash}


def unambiguous_artifacts(per_script: dict[str, dict[str, str]]) -> dict[str, str]:
    """各脚本清洗后的 `{文件: stem}` → 全表：**同一路径被两个及以上脚本认领就整条不要**（#1251）。

    「有歧义就不记」的**唯一判据**：读（`load_data`）用它；写（`discover.register`）靠「本次执行写出了它，别处的认领
    摘掉」保证落盘的文件里不会出现争用——所以读到争用只会来自手写 / 合并出来的文件，此时哪个脚本都不该赢，
    也不取决于 JSON 里脚本键的顺序。第三个认领者同样不会把它加回来。
    """
    seen: dict[str, str] = {}
    contested: set[str] = set()
    for mine in per_script.values():
        for art, stem in mine.items():
            if art in seen:
                contested.add(art)
            else:
                seen[art] = stem
    return {a: s for a, s in seen.items() if a not in contested}


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


def for_artifact(rel: str) -> dict | None:
    return _DEFAULT.for_artifact(rel)


def all_scripts() -> list[str]:
    return _DEFAULT.all_scripts()


def stems_of(script: str) -> list[str]:
    return _DEFAULT.stems_of(script)
