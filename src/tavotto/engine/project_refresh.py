"""项目刷新：registry / 素材 / worker 失效 / 事件的**唯一**后端入口。

改造前，「项目里的东西变了」这件事在后端有三条各自为政的路径：
`/api/registry/scan`（静态合并 + reload）、probe 成功后的那几行
（reload + 一条 `registry.changed`）、手工登记那几行（同样的两句，
措辞略有不同）。三条路径各自决定「要不要重挂 watcher」「发什么事件」
「哪些 worker 该作废」，而它们的答案本来就不一样——第四条（项目 watcher，
Prompt 05）如果照着任意一条再抄一遍，分叉就有四份。

这个模块是那个唯一答案：

    项目静态刷新 → registry 合并与重载 → 素材前后快照 → 结构化 diff
    → worker 失效 → 项目级 SSE 发布 → 结构化结果

**它不执行用户脚本。** 静态 merge 只读 AST（`engine/discover.py`），
素材 inventory 只 `stat()`，注册表只读 JSON。真正跑用户代码的入口仍然只有
显式的 probe、渲染请求与 native 会话（共享规则 §4）。

**它也不碰文档。** 派生元数据的刷新不设 dirty、不进撤销历史、不读写
autosave / 版本历史目录（Prompt 02–03 的文档合同）。这个模块连 `documents`
都不 import——边界靠依赖方向守着，不靠注释。

纯标准库：Flask 父进程 import 它。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import atomicio, discover, pool, registry

# ---------------------------------------------------------------------------
# 素材边界 —— **唯一出处**
#
# `/api/panels`（真正列给用户的素材）与刷新 inventory（判断"素材变了没有"）
# 必须用同一把尺：两份判据迟早分叉，而分叉的表现是 refresh 报了一张
# `/api/panels` 里根本不存在的图，或者反过来——用户看得见的图改了却不刷新。
# ---------------------------------------------------------------------------
#: tavottofile/ 是项目内的 Tavotto 数据收纳目录（画布/导出/版本历史）：
#: 导出的 PDF/PNG 落在里面，素材扫描必须剪掉，否则导出一次素材面板就多一堆成图。
EXCLUDE_DIRS = {"__pycache__", "_cache", "_palette_ref", "scripts", ".git", "tavottofile"}
PDF_EXT = {".pdf"}
IMG_EXT = {".png", ".jpg", ".jpeg"}

#: 允许的刷新来由。**闭集**：它进日志、进事件、以后还会进遥测的枚举维度，
#: 客户端传什么就记什么等于让外面的人往我们的指标里写自由文本。
REASONS = ("manual", "watcher", "registry", "probe", "codex", "ai", "open", "external")
DEFAULT_REASON = "manual"


def normalize_reason(raw: object) -> str:
    """未知/畸形一律归成 `manual`，**不透传**。"""
    if not isinstance(raw, str):
        return DEFAULT_REASON
    value = raw.strip().lower()
    return value if value in REASONS else DEFAULT_REASON


class RefreshError(RuntimeError):
    """刷新失败的结构化形态：稳定 `code` + `params`，中文原文只作回退。

    `code` 与 app.py 里的字面量 code 同一套约定（`tests/test_error_codes.py`
    扫本模块）。**失败时内存里的 registry 一个字节都不动**——上层拿到的是
    "这次没刷成"，不是"注册表空了"。
    """

    def __init__(self, code: str, message: str, params: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.params = params or {}

    def as_payload(self) -> dict:
        return {"error": self.message, "code": self.code, "params": self.params}


@dataclass
class RefreshState:
    """一个项目的刷新状态。

    **挂在 ProjectCtx 上，随项目一起消亡**（`state_of()`）。放模块级锁表的话
    得自己治理生命周期：打开/关闭几十个项目之后那张表还在长，而"什么时候
    能删"没有可靠信号。
    """

    #: 同一项目的所有刷新入口（手动 / scan / watcher / probe / codex / ai）
    #: 串行。**每项目一把**：另一个项目的刷新不该被这一把锁挡住。
    lock: threading.RLock

    #: 上一次看到的素材清单（id → 文件签名）。**素材 diff 必须跨轮比**，
    #: 见 `refresh_project_index()` 里的那段注释。`None` = 还没有基线。
    assets: dict[str, dict] | None = None

    #: 上一轮静态扫描看到的冲突（stem → 声明它的脚本）。`None` = 还没扫过，
    #: 与"扫过、没有冲突"是两回事——后者是 `{}`。
    conflicts: dict[str, list[str]] | None = None

    #: 上一次**我们自己**写完注册表之后它的内容修订号。Prompt 05 的 watcher
    #: 拿它认自己写的那一下（见模块末尾 `is_self_written()`）。
    registry_revision: str | None = None

    #: 上一次静态合并**写**注册表时失败了没有（磁盘满、文件本身只读、
    #: Windows 上被杀毒软件锁住）。就绪度（`engine/readiness.py`）拿它区分
    #: 「还没登记」的两种成因：一种再刷新一次就好了，另一种刷多少次都一样。
    #: 只在真的走到写那一步时更新——`allow_static_merge=False` 的刷新
    #: （probe / 手工登记之后）压根没写，不该把上一次的结论抹掉。
    registry_write_failed: bool = False

    #: 就绪度报告的项目级缓存。形状归 `engine/readiness.py` 自己管，本模块
    #: 只负责在刷新**真的**改动了事实之后把它清成 `None`。
    #: **不 import readiness**：依赖方向是 readiness → refresh，反过来成环。
    readiness: object | None = None


_STATE_LOCK = threading.Lock()


def state_of(ctx) -> RefreshState:
    """取（必要时创建）项目的刷新状态。"""
    st = getattr(ctx, "refresh_state", None)
    if st is None:
        with _STATE_LOCK:  # 首次创建本身也要串行，否则两个线程各拿一把锁
            st = getattr(ctx, "refresh_state", None)
            if st is None:
                st = RefreshState(lock=threading.RLock())
                ctx.refresh_state = st
    return st


def seed_state(ctx) -> RefreshState:
    """项目打开时给它一条**基线**：现在的素材长什么样、注册表是哪一份。

    没有基线的话，打开项目后的第一次刷新只能报"什么都没变"——而用户按下
    刷新，正是因为他刚在编辑器外面加了一张图。第一次刷新报空，比不报还糟：
    它是一句**错的断言**，不是一句"我还不知道"。
    """
    st = state_of(ctx)
    with st.lock:
        st.assets = asset_inventory(Path(ctx.path))
        st.registry_revision = atomicio.content_revision(registry.registry_path(ctx.path))
    return st


@dataclass(frozen=True)
class RefreshSink:
    """刷新的副作用出口，由 app 层注入。

    模块本身不 import Flask、也不知道 SSE 长什么样。缺省 `None`，于是纯引擎
    侧的调用（测试、CLI）什么都不发。

    这里**曾经还有一个 `watch`**：老的脚本 watcher 按注册表里那张清单逐个盯
    mtime，所以清单变了就得重挂一次。项目 watcher（`engine/project_watch.py`）
    盯的是整棵树，没有"盯谁"这个状态——那个钩子于是没有了对应的动作，
    留着它只会是一个没人调的形状。
    """

    publish: Callable[[str, dict], None] | None = None


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------
def _reraise(exc: OSError) -> None:
    """`os.walk` 的 onerror：把「这棵子树读不动」抬成异常。

    默认的 `onerror=None` 是**静默跳过**——一个临时读不动的子目录会让
    `os.walk` 少给几行而不报任何错，调用方拿到的是一张看起来完整的半张表。
    """
    raise exc


def iter_assets(root: Path, *, strict: bool = False) -> list[tuple[Path, str]]:
    """项目里的素材文件与它们的 kind —— `/api/panels` 与刷新共用这一份判据。

    * 隐藏目录与 `EXCLUDE_DIRS` **当场剪枝，不下探**：图库里常有 .venv、.git、
      工具留下的 .rendered/.qa_* 快照，爬进去既是噪音又很慢；
    * 同目录同名的 PDF 与位图只算矢量那份（有矢量版就不重复列出位图）。

    `strict=True` 时中途读不动就抛（`_reraise`），**不返回半张表**。
    `/api/panels` 与刷新照旧宽容：一个读不动的子目录不该让素材面板整个空掉；
    watcher 则必须严格——半张表与「用户删了这些文件」在 diff 里没有区别。
    """
    root = Path(root)
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_reraise if strict else None):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
        files += [Path(dirpath) / fn for fn in filenames if not fn.startswith(".")]
    files.sort()
    pdf_stems = {(p.parent, p.stem) for p in files if p.suffix.lower() in PDF_EXT}

    out: list[tuple[Path, str]] = []
    for p in files:
        ext = p.suffix.lower()
        if ext in PDF_EXT:
            out.append((p, "pdf"))
        elif ext in IMG_EXT and (p.parent, p.stem) not in pdf_stems:
            out.append((p, "raster"))
    return out


def asset_inventory(root: Path) -> dict[str, dict]:
    """素材的**文件签名**清单：id → {kind, size, mtime_ns}。

    刻意**不做内容哈希**：一次刷新要对整个图库里的 PDF/PNG 逐个读全文，
    大项目上是秒级开销，而这里要回答的只是"有没有动过"。内容哈希留在渲染
    缓存那条路上（`/api/render` 的缓存键就是内容哈希，它按需、按单个文件算）。

    也**不做 probe_asset**：那要真的打开 PDF 解析页面尺寸。原始尺寸变化会
    带来 mtime/size 变化，签名已经盖住了。
    """
    root = Path(root)
    inv: dict[str, dict] = {}
    for path, kind in iter_assets(root):
        try:
            st = path.stat()
        except OSError:  # 扫描与 stat 之间被删掉了：当它不在
            continue
        # id 与 `/api/panels` 逐字相同（Windows 上是反斜杠）——文档里的
        # fileId 就是这个串，两边不一致的话 diff 指不回任何一个面板。
        rel = str(path.relative_to(root))
        inv[rel] = {"id": rel, "kind": kind, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    return inv


def registry_snapshot(reg) -> dict[str, dict]:
    """注册表快照。`entries()` 已经逐脚本 copy，这里再把 stems 拷一份：
    快照要在 `reg.load()` 之后仍然代表**刷新前**的事实。"""
    return {k: {**v, "stems": list(v.get("stems") or [])} for k, v in reg.entries().items()}


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------
#: 「脚本配置变了」看哪几个字段。stems 单独比（顺序无意义，见下）。
_SCRIPT_FIELDS = ("entry", "cost", "notes")


def _owners(snapshot: dict[str, dict]) -> dict[str, str]:
    return {stem: script for script, cfg in snapshot.items() for stem in cfg["stems"]}


def diff_registry(before: dict[str, dict], after: dict[str, dict]) -> dict:
    """结构化 registry diff。

    **不能只比脚本名**：entry 改了（`main` → `render`）、stem 从 A 换到 B、
    cost 从 light 变 heavy，脚本清单一个字都没变，而这三件事每一件都足以
    让一个热 worker 产出错误的图。
    """
    added_scripts = sorted(set(after) - set(before))
    removed_scripts = sorted(set(before) - set(after))
    changed_scripts: list[str] = []
    script_changes: dict[str, list[str]] = {}
    for script in sorted(set(before) & set(after)):
        fields = [f for f in _SCRIPT_FIELDS if before[script].get(f) != after[script].get(f)]
        # stems 按**集合**比：注册表里 stem 的先后顺序没有语义（`register()`
        # 写的是 sorted，`merge()` 是追加），按列表比会让一次纯重排作废
        # 一批本来好好的 worker。
        if sorted(before[script]["stems"]) != sorted(after[script]["stems"]):
            fields.append("stems")
        if fields:
            changed_scripts.append(script)
            script_changes[script] = fields

    before_owner, after_owner = _owners(before), _owners(after)
    return {
        "added_scripts": added_scripts,
        "removed_scripts": removed_scripts,
        "changed_scripts": changed_scripts,
        "script_changes": script_changes,
        "added_stems": sorted(set(after_owner) - set(before_owner)),
        "removed_stems": sorted(set(before_owner) - set(after_owner)),
        "moved_stems": [
            {"stem": s, "from": before_owner[s], "to": after_owner[s]}
            for s in sorted(set(before_owner) & set(after_owner))
            if before_owner[s] != after_owner[s]
        ],
    }


def diff_assets(before: dict[str, dict], after: dict[str, dict]) -> dict:
    """结构化素材 diff：新增 / 删除 / 内容变了（kind、size 或 mtime_ns）。"""
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(
        rel
        for rel in set(before) & set(after)
        if any(before[rel][k] != after[rel][k] for k in ("kind", "size", "mtime_ns"))
    )
    return {"added": added, "removed": removed, "changed": changed}


def _registry_touched(diff: dict) -> bool:
    return bool(diff["added_scripts"] or diff["removed_scripts"] or diff["changed_scripts"])


def _assets_touched(diff: dict) -> bool:
    return bool(diff["added"] or diff["removed"] or diff["changed"])


# ---------------------------------------------------------------------------
# 自写识别（Prompt 05 的 watcher 用）
# ---------------------------------------------------------------------------
def is_self_written(ctx, path: Path | None = None) -> bool:
    """磁盘上这份注册表，内容是不是我们上一次写下/读到的那一份？

    watcher 迟早会看到刷新自己写的 `tavotto_registry.json`——不认出来的话，
    每一次刷新都会触发下一次刷新。判据是**内容修订号**，不是"写完之后忽略
    两秒"：时间窗口在慢磁盘上不够、在快机器上又白白吞掉用户真实的外部修改，
    而内容比较两头都不会错（用户把文件改回原样 = 内容没变 = 确实不用刷新）。
    """
    st = state_of(ctx)
    if st.registry_revision is None:
        return False
    target = Path(path) if path is not None else registry.registry_path(ctx.path)
    return atomicio.content_revision(target) == st.registry_revision


# ---------------------------------------------------------------------------
# 刷新
# ---------------------------------------------------------------------------
def _normalize_changed_paths(root: Path, raw: Iterable[str] | None) -> list[str]:
    """把调用方给的变更路径规整成项目相对路径；项目外的一律丢掉。

    这个入参**只给进程内的调用方**（watcher / MCP）；HTTP 端点不接受它，
    否则等于让客户端指定一段绝对路径进日志。

    分隔符统一成 POSIX（与 `discover.rel_key` 同一种写法）。它是一个**回声
    字段**：进刷新结果、进日志、给人看，没有任何一处拿它去匹配素材 id
    （那些**刻意**保留本机分隔符，因为文档里的 `fileId` 就是那个串）。
    回声字段跨平台长得不一样，只会让同一件事在两个平台上读起来像两件事。
    """
    if not raw:
        return []
    root = Path(root).resolve()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        try:
            p = Path(item)
            resolved = (p if p.is_absolute() else root / p).resolve()
            out.append(resolved.relative_to(root).as_posix())
        except (OSError, ValueError):
            continue
    return sorted(set(out))


def _static_merge(root: Path, state: RefreshState) -> tuple[dict[str, list[str]], dict]:
    """静态扫描 + 合并 + （必要时）落盘。返回 (冲突, 旧口径的 changes)。

    **只在内容真的会变时才写**。以前 `/api/registry/scan` 无条件回写：一次
    什么都没发现的扫描照样把用户的注册表重写一遍（格式被归一化、mtime 变了），
    而 mtime 一变，Prompt 05 的 watcher 就会看到一次"外部修改"。
    这里按**字节**比：老名字（`mm_registry.json`）那份天然与目标路径不同，
    因此搬迁照旧发生。
    """
    try:
        cfg, report, changes = discover.merge(root)
    except (OSError, ValueError, RuntimeError) as exc:
        # 沿用既有的 `scan_failed`：这一段就是老 `/api/registry/scan` 的内核，
        # 换个码名等于让装着旧前端的用户看到一句英文 key。
        raise RefreshError("scan_failed", f"扫描失败: {exc}", {"reason": str(exc)}) from exc

    target = registry.registry_path(root)
    try:
        current = target.read_bytes()
    except OSError:
        current = b""
    if atomicio.dumps_json(cfg, indent=1) != current:
        try:
            discover.write_config(root, cfg)
        except (OSError, ValueError) as exc:  # AtomicWriteError 是 OSError 的子类
            # 写不下去与扫描失败对用户是两件事（一个是"你的注册表存不了"，
            # 另一个是"你的脚本读不动"），但**对外的 code 不能改**——
            # `scan_failed` 是老 `/api/registry/scan` 的契约，换名字等于让装着
            # 旧前端的用户看到一句英文 key。区分留在状态里给就绪度用。
            state.registry_write_failed = True
            raise RefreshError("scan_failed", f"扫描失败: {exc}", {"reason": str(exc)}) from exc
    state.registry_write_failed = False
    return {k: list(v) for k, v in report["conflicts"].items()}, changes


def _reload(ctx, state: RefreshState) -> None:
    """重装注册表。**失败时内存里那份原封不动**——`Registry.load_data()` 在
    校验通过之前不碰自己的字段，所以"半更新状态"在结构上就不存在。"""
    try:
        ctx.registry.load(ctx.path)
    except FileNotFoundError as exc:
        raise RefreshError(
            "registry_reload_failed", f"注册表不存在: {exc}", {"reason": str(exc)}
        ) from exc
    except RuntimeError as exc:
        raise RefreshError(
            "registry_reload_failed", f"注册表读不回来: {exc}", {"reason": str(exc)}
        ) from exc
    # 装载成功 = 内存里那份与磁盘上那份一致。修订号在这里更新，于是它同时
    # 回答"我们写的"和"我们读过的"——watcher 要的正是"这份内容我已经消化过"，
    # 而不是"这个字节序列出自我们的笔"。
    state.registry_revision = atomicio.content_revision(registry.registry_path(ctx.path))


def _events(ctx, reason: str, result: dict) -> list[tuple[str, dict]]:
    """结果 → 要发的事件。**无差异一条都不发**。"""
    out: list[tuple[str, dict]] = []
    reg = result["registry"]
    if _registry_touched(reg) or reg["conflicts_changed"]:
        scripts = sorted({*reg["added_scripts"], *reg["removed_scripts"], *reg["changed_scripts"]})
        stems = sorted({*reg["added_stems"], *reg["removed_stems"]})
        payload = {
            "pj": ctx.id,
            "reason": reason,
            # 批量：一次事件带走全部，不为十几个脚本发十几条
            "scripts": scripts,
            "stems": stems,
            "added_scripts": reg["added_scripts"],
            "removed_scripts": reg["removed_scripts"],
            "changed_scripts": reg["changed_scripts"],
        }
        # 老客户端只认单脚本形态（`{script, stems}`）：**只有一个脚本变**时
        # 照旧给它，那正好是 probe / 手工登记这两条老路径的形状。
        if len(scripts) == 1:
            payload["script"] = scripts[0]
            payload["stems"] = sorted(result["scripts"].get(scripts[0], {}).get("stems", stems))
        if reg["conflicts"] is not None:
            payload["conflicts"] = reg["conflicts"]
        out.append(("registry.changed", payload))

    assets = result["assets"]
    if _assets_touched(assets):
        out.append(
            (
                "assets.changed",
                {
                    "pj": ctx.id,
                    "reason": reason,
                    "ids": sorted({*assets["added"], *assets["removed"], *assets["changed"]}),
                    **{k: assets[k] for k in ("added", "removed", "changed")},
                },
            )
        )
    return out


def refresh_project_index(
    ctx,
    *,
    reason: str,
    changed_paths: Sequence[str] | None = None,
    allow_static_merge: bool = True,
    publish: bool = True,
    sink: RefreshSink | None = None,
) -> dict:
    """刷新一个项目的派生事实，返回结构化结果。

    `ctx` 只需要三样东西：`path`（图库路径）、`id`（项目短 id，事件里的 pj）、
    `registry`（这个项目的 Registry 实例）。

    `allow_static_merge=False` 用于"注册表刚被别人写过、只要重装 + 发事件"
    的场合（probe 成功、手工登记）：那时再跑一遍 AST 扫描既慢又可能把刚刚
    人工裁决的归属重新掀一遍。

    失败一律抛 `RefreshError`，且**保证**：注册表不半更新、不清空，已打开的
    项目不关闭，worker 不作废，事件不发。
    """
    reason = normalize_reason(reason)
    state = state_of(ctx)
    root = Path(ctx.path)

    with state.lock:  # 同一项目串行；不同项目互不相干
        before_registry = registry_snapshot(ctx.registry)

        conflicts: dict[str, list[str]] | None = None
        merge_changes: dict = {"added_scripts": [], "added_stems": {}}
        if allow_static_merge:
            conflicts, merge_changes = _static_merge(root, state)

        _reload(ctx, state)

        after_registry = registry_snapshot(ctx.registry)
        registry_diff = diff_registry(before_registry, after_registry)

        # **素材 diff 跨轮比，不在一次刷新内部前后比。**
        # 刷新会改注册表（合并、重载），所以 registry 的"刷新前/刷新后"是有
        # 内容的；素材它一个字节都不碰——同一次调用里前后两张快照必然逐项
        # 相同，那样的 diff 永远是空的，而"永远是空的"看起来和"什么都没变"
        # 一模一样。基线因此存在 RefreshState 里（项目打开时 `seed_state()`
        # 落一份）。没有基线时**不报"没变化"**，报"这一轮在建基线"。
        assets_now = asset_inventory(root)
        baseline = state.assets is None
        asset_diff = diff_assets(
            state.assets if state.assets is not None else assets_now, assets_now
        )
        asset_diff["baseline"] = baseline
        state.assets = assets_now

        # 冲突「变了没有」只能跨轮比：merge 不改脚本源码，同一次刷新的前后
        # 冲突必然相同。第一次刷新没有可比的上一轮 → 不算变化（冲突在刷新
        # 之前就已经在那儿了）。
        conflicts_changed = (
            conflicts is not None and state.conflicts is not None and conflicts != state.conflicts
        )
        if conflicts is not None:
            state.conflicts = conflicts
        registry_diff["conflicts"] = conflicts
        registry_diff["conflicts_changed"] = conflicts_changed

        # worker 失效：**只动关系真的变了的那些**。新增一张不相干的图片、
        # 或者一次什么都没发现的刷新，一个会话都不该被打掉——那是几十秒的
        # 冷启动，用户会以为是自己点坏了什么。
        for script in registry_diff["removed_scripts"] + registry_diff["changed_scripts"]:
            pool.invalidate(script, str(root))

        # 就绪度缓存：**只在事实真的动了的时候**清。它的键本来就是内容签名
        # （见 `readiness._inputs_signature`），所以这一句是**第二道**判据而
        # 不是唯一那道——签名盖不住的是「同尺寸 + 同一个 mtime_ns 刻度里的
        # 就地改写」，而那正好是刷新自己写注册表时最容易撞上的形状。
        # 无差异的刷新一句都不动（不变式：无差异 = 零事件、零写盘、零失效）。
        if _registry_touched(registry_diff) or conflicts_changed or _assets_touched(asset_diff):
            state.readiness = None

        result = {
            "reason": reason,
            "registry": registry_diff,
            "assets": asset_diff,
            "scripts": after_registry,
            "changed_paths": _normalize_changed_paths(root, changed_paths),
            # 旧 `/api/registry/scan` 的 `changes` 字段原样带出（兼容用）
            "merge": merge_changes,
            "registry_revision": state.registry_revision,
        }

    # 发布放在锁外：SSE 的订阅队列是另一套并发，没有理由让它排在项目锁后面。
    # `published` 记的是**真的发出去的那些**，不是"本该发的那些"——没有 sink
    # 的调用（纯引擎侧、测试）拿到的是空表，而不是一句发生过广播的假话。
    sent: list[str] = []
    if publish and sink is not None and sink.publish is not None:
        for event, payload in _events(ctx, reason, result):
            sink.publish(event, payload)
            sent.append(event)
    result["published"] = sent
    return result
