"""项目级文件 watcher：**发现变化**，然后交给统一刷新去解释它。

改造前这里只有一个「盯已登记脚本 mtime」的循环（`pool.start_watcher`）：
它按注册表里那张脚本清单逐个 `stat()`，于是——

* 在编辑器里**新建**一个 `fig3.py`：清单里没有它，永远发现不了；
* **删除**一个脚本：`stat()` 抛 OSError 被 `continue` 吞掉，也发现不了；
* **重命名**：等于一次删除加一次新增，两头都看不见；
* **原子替换**（写临时文件 → fsync → rename 覆盖）：旧 inode 被换掉，
  「同一个文件的 mtime 变了」这个判据量的是那个已经不存在的对象；
* 在编辑器里改 `tavotto_registry.json`、往图库里丢一张新 PDF：不在清单里，
  同样看不见。

也就是说，它守的其实只有「一个已登记脚本被就地改写」这一种形状。本模块把
判据换成**整个项目目录的轻量快照**：文件集合 + 每个文件的 (size, mtime_ns)。
集合变了能发现新增/删除/改名，签名变了能发现就地改写与原子替换——后者正是
「量错了对象」那一族的解药：判据的主语从「那个文件」换成「那条路径现在是
什么」。

### 边界

* **它只发现，不解释。** 「注册表该怎么合并、哪些 worker 该作废、发什么
  事件」的编排只有 `project_refresh.refresh_project_index()` 一份（ADR 0025）。
  本模块不 `discover.merge`、不 reload 注册表、不自己发 `registry.changed`
  或 `assets.changed`。
* **它不执行用户脚本**（共享规则 §4）：只 `stat()`，连文件内容都不读。
* **纯标准库**：不引入 watchdog / FSEvents / Tauri 文件监听——桌面、浏览器
  和测试必须共享同一个后端 watcher，平台专用实现会让「测试里绿的」和
  「用户机器上跑的」变成两个东西。

### 为什么仍然是轮询

原生事件（inotify/FSEvents/ReadDirectoryChangesW）省 CPU，但每个平台的语义
都不一样（重命名报几条、原子替换报什么、网络盘上报不报），而我们真正需要的
判据——「现在这棵树长什么样」——轮询能直接回答。空闲开销是每 `interval`
一次剪枝遍历 + `stat()`，不读任何内容。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import discover, pool, project_refresh, registry

LOG = logging.getLogger("tavotto.watch")

#: 轮询间隔。与改造前的脚本 watcher 保持一致——空闲时每 2 秒一次剪枝遍历，
#: 上千文件的图库也只是几毫秒的 `stat()`，而缩短它会按比例放大空闲开销。
DEFAULT_INTERVAL = 2.0

#: 防抖窗口：批次里最后一次变化之后要安静这么久才结算。
#: 一次编辑器保存常常是「写临时文件 → rename → 稍后生成图片」几步，
#: 跨过轮询边界时会被拆成两轮；防抖把它们并回一批。
DEFAULT_DEBOUNCE = 0.5

#: 批次年龄上限。防抖是「等安静」，而目录**可能永远不安静**（脚本在跑、
#: 正在拷一个大目录）——没有这个封顶的话刷新会被无限期推迟。
DEFAULT_MAX_BATCH = 5.0

#: 注册表的两个文件名（新名 + 旧名）。旧名要一起盯：从 legacy 迁移过来的
#: 项目在第一次刷新之前，磁盘上只有 `mm_registry.json`。
REGISTRY_NAMES = (registry.REGISTRY_NAME, registry.LEGACY_REGISTRY_NAME)

#: 「共享样式模块」的判据。改一次 `paper_style.py`，本项目每一张图的外观都
#: 可能变，所以作废的是整个项目的 worker 而不是某一个脚本的。
#: 前缀（不是全等）沿用 `discover.SKIP_PREFIXES` 的口径——图库里常年躺着
#: `paper_style 2.py` 这种编辑器/网盘留下的副本，而脚本 `import` 的可能正是
#: 其中一份。
_STYLE_PREFIX = "paper_style"


def _is_style_module(rel: str) -> bool:
    return Path(rel).name.startswith(_STYLE_PREFIX)


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------
#: 一个文件的廉价签名。**不读内容**：这里要回答的只是「动过没有」。
#:
#: 三个维度缺一不可：
#: * 存在性 —— 由「在不在这张表里」表达（`_sig()` 返回 None 就不进表）；
#: * `mtime_ns` —— 就地改写；
#: * `size` —— mtime 精度不够时的第二把尺。某些文件系统（FAT32、部分网络盘、
#:   老 HFS+）的时间戳只到 1~2 秒，一秒内的两次保存 mtime 完全相同；只要
#:   长度变了，size 就能把它救回来。**别只依赖秒级 mtime。**
Signature = tuple[int, int]


def _sig(path: Path) -> Signature | None:
    """(size, mtime_ns)；文件不在或读不到属性时返回 None（= 不在表里）。

    单个文件没权限不影响其它文件：这里吞掉的是**这一个** `stat()`。
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


@dataclass(frozen=True)
class Snapshot:
    """项目目录的一张轻量快照，按**变化要触发什么**分成三类。

    分类在拍快照的时候就做掉，而不是等到 diff 之后再按后缀猜：三类的
    遍历规则本来就不同（脚本走 `discover` 的剪枝与深度，素材走
    `project_refresh.iter_assets` 的口径），把它们混成一张表就得再写一份
    分类判据——而那正是第三份判据的来处。
    """

    #: 项目相对 POSIX 路径（与注册表里的脚本键同一种写法）→ 签名
    scripts: dict[str, Signature] = field(default_factory=dict)
    #: 文件名（`REGISTRY_NAMES` 的子集）→ 签名
    registry: dict[str, Signature] = field(default_factory=dict)
    #: 素材 id（与 `/api/panels` 逐字相同）→ 签名
    assets: dict[str, Signature] = field(default_factory=dict)


def take_snapshot(root: Path) -> Snapshot | None:
    """拍一张快照；项目目录当前不可用时返回 `None`。

    **`None` 不等于「空目录」。** 网盘掉线、外接盘没挂上、用户临时把目录改了
    名——这几种情况下遍历会得到一张空表，而空表与「用户删光了所有文件」在
    diff 里长得一模一样，照它行事会把整个项目的 worker 全部作废、把一次
    刷新变成一次全量「删除」。宁可这一轮什么都不做：目录回来之后，下一次
    diff 仍然会把这段时间里真正发生的变化算出来（快照比的是两个状态，不是
    重放事件）。
    """
    root = Path(root)
    if not root.is_dir():
        return None

    snap = Snapshot()
    try:
        # 脚本走 discover 的那一份遍历规则（PRUNE_DIRS / MAX_DEPTH / 隐藏项）。
        # **必须与它一致**：watcher 盯得比 discover 宽，会为一个永远进不了
        # 注册表的文件反复刷新；盯得比它窄，用户新建的脚本就发现不了。
        # 用 `iter_all_scripts`（含基础设施脚本）而不是 `iter_scripts`——
        # `paper_style.py` 正在被 SKIP 挡在起草之外，而它恰恰是最需要盯的
        # 那一个。
        for path in discover.iter_all_scripts(root, strict=True):
            sig = _sig(path)
            if sig is not None:
                snap.scripts[discover.rel_key(path, root)] = sig

        for name in REGISTRY_NAMES:
            sig = _sig(root / name)
            if sig is not None:
                snap.registry[name] = sig

        # 素材走 `project_refresh.iter_assets` —— 「哪些文件算素材」的唯一
        # 出处（`/api/panels` 与刷新 diff 共用）。watcher 另写一份的话，
        # 表现会是「用户看得见的图改了却不刷新」或者反过来。
        for path, _kind in project_refresh.iter_assets(root, strict=True):
            sig = _sig(path)
            if sig is not None:
                snap.assets[str(path.relative_to(root))] = sig
    except OSError as exc:
        # 遍历中途出错（目录被删、权限变了、网盘上的子目录掉线）：这一轮作废，
        # 线程继续。**两处遍历都必须 `strict=True`**——它们的默认行为是静默
        # 跳过读不动的那棵子树，于是这个 except 一次都不会执行，而 `snap` 会
        # 是一张少了一截的表：上面那段 docstring 承诺挡住的正是它。
        LOG.debug("项目快照遍历失败，跳过这一轮: %s", exc)
        return None
    return snap


@dataclass
class Delta:
    """两张快照之间的差异，按类别分开。**存的是路径，不是事件**——
    「这算新增还是改名」由统一刷新在拿到权威状态之后回答。"""

    scripts: set[str] = field(default_factory=set)
    registry: set[str] = field(default_factory=set)
    assets: set[str] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.scripts or self.registry or self.assets)

    def absorb(self, other: "Delta") -> None:
        self.scripts |= other.scripts
        self.registry |= other.registry
        self.assets |= other.assets

    def paths(self) -> list[str]:
        """批次里全部变化路径（给 `refresh(changed_paths=…)`）。"""
        return sorted(self.scripts | self.registry | self.assets)


def _changed_keys(before: dict[str, Signature], after: dict[str, Signature]) -> set[str]:
    """出现、消失、或签名变了的键。

    三种都算一次变化，且**不区分**——重命名在轮询里的样子就是
    「旧路径消失 + 新路径出现」，硬要在这一层判成 rename 得靠 inode，而
    inode 在 Windows 上不可靠、在原子替换之后也确实变了。合并成一批交给
    刷新，它按权威状态给出结论。
    """
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


def diff_snapshots(before: Snapshot, after: Snapshot) -> Delta:
    return Delta(
        scripts=_changed_keys(before.scripts, after.scripts),
        registry=_changed_keys(before.registry, after.registry),
        assets=_changed_keys(before.assets, after.assets),
    )


# ---------------------------------------------------------------------------
# 副作用出口
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WatchSink:
    """watcher 的三个出口，由 app 层注入。

    与 `project_refresh.RefreshSink` 同样的理由：本模块不 import Flask、
    不知道 SSE 长什么样，也不该知道 `refresh_project()` 住在哪。缺省全是
    `None`，于是纯引擎侧的调用（测试、CLI）什么都不发。
    """

    #: 统一刷新入口。参数是本批次的变化路径（项目相对）。
    refresh: Callable[[list[str]], None] | None = None
    #: 已登记脚本的**内容**变了（→ `panel.file_changed`）。参数是脚本键。
    script_changed: Callable[[list[str]], None] | None = None
    #: 后台刷新失败（→ 项目级可恢复错误）。参数是稳定 code 与它的 params。
    error: Callable[[str, dict], None] | None = None


# ---------------------------------------------------------------------------
# watcher
# ---------------------------------------------------------------------------
class ProjectWatcher:
    """一个项目的 watcher。

    循环体拆成 `prime()` + `poll()` 两个纯同步方法，线程只负责按 `interval`
    反复调 `poll()`。测试因此能注入一个假时钟、**逐轮**驱动它，把「防抖有没有
    合并这两次保存」变成一句确定的断言，而不是 `time.sleep(2.5)` 之后碰运气
    ——靠睡眠写出来的 watcher 用例既慢又会在 CI 上偶发红，而偶发红最后总是
    被当成「基础设施抖动」忽略掉。
    """

    def __init__(
        self,
        ctx,
        *,
        sink: WatchSink | None = None,
        interval: float = DEFAULT_INTERVAL,
        debounce: float = DEFAULT_DEBOUNCE,
        max_batch: float = DEFAULT_MAX_BATCH,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ctx = ctx
        self.root = Path(ctx.path)
        self.sink = sink or WatchSink()
        self.interval = interval
        self.debounce = debounce
        self.max_batch = max_batch
        self.clock = clock
        self.stop_event = threading.Event()

        self._snapshot: Snapshot | None = None
        self._pending = Delta()
        self._first_seen: float | None = None
        self._last_seen: float | None = None
        self._missing = False  # 目录当前不可用（只为日志去重）
        # `poll()` 跑在 watcher 线程，`absorb()` 从 AI 会话的 pump 线程进来：
        # 两边都要动快照与 pending。锁只护这两份账，**不**在锁里调刷新回调之外
        # 的任何等待——刷新自己有项目锁，顺序永远是 watcher 锁 → 项目锁。
        self._lock = threading.RLock()

    # ---------------- 一轮 ----------------
    def prime(self) -> None:
        """建立初始快照。**start 时同步做掉**，不放进线程第一轮——否则
        「打开项目」与「第一次 poll」之间的所有改动会被算成一批变化。"""
        self._snapshot = take_snapshot(self.root)

    def poll(self) -> None:
        """一轮：拍快照 → 累积 → 够安静（或够久）就结算这一批。"""
        with self._lock:
            self._poll_locked()

    def _poll_locked(self) -> None:
        snap = take_snapshot(self.root)
        if snap is None:
            # 目录暂时不可用。**不清空 pending、不动上一张快照**——把
            # 「看不见」当成「不存在」正是 absence-is-not-evidence 那一族。
            if not self._missing:
                self._missing = True
                LOG.warning("项目目录当前不可读，watcher 暂停这一轮（线程继续）")
            return
        if self._missing:
            self._missing = False
            LOG.info("项目目录恢复可读，watcher 继续")

        if self._snapshot is None:  # 从没拍到过基线：这一轮只建基线
            self._snapshot = snap
            return

        now = self.clock()
        delta = diff_snapshots(self._snapshot, snap)
        # **先换快照，再处理。** 处理（刷新）期间到达的写入会与这张新快照
        # 比较，于是进入下一批而不是丢失。反过来（处理完再换）会把处理期间
        # 的变化算进已经结算的这一批，下一轮 diff 为空——那正是「保存了没
        # 反应」这类问题最难查的成因。
        self._snapshot = snap

        if delta:
            self._pending.absorb(delta)
            self._last_seen = now
            if self._first_seen is None:
                self._first_seen = now

        if not self._pending:
            return
        quiet = self._last_seen is not None and now - self._last_seen >= self.debounce
        aged = self._first_seen is not None and now - self._first_seen >= self.max_batch
        if not (quiet or aged):
            return

        batch, self._pending = self._pending, Delta()
        self._first_seen = self._last_seen = None
        self._dispatch(batch)

    # ---------------- 别人先处理过的那一次写入 ----------------
    def absorb(self, rel_paths: Iterable[str]) -> list[str]:
        """把这几条脚本路径**此刻**的签名记成「已消化」，返回真的被吸收的那些。

        谁调它：AI 修改完成后的后端路径（`app._after_ai_change`）。那条路已经
        作废了 worker、跑过统一刷新、发过 `panel.file_changed`——watcher 下一轮
        再看到同一次写入时不该把这三件事再做一遍（前端会收到第二份 stale、
        第二条提示，同一张图重建两次）。

        判据是**签名相等**，不是时间窗：调用之后用户又改了一次，签名不同，
        照常触发。只动脚本表——素材（AI 不生成）与注册表（走
        `is_self_written()`）各有各的判据。「真的被吸收」= 快照里记的还是旧
        签名、或这条路径正躺在 pending 里；两者都不成立说明 watcher 已经自己
        结算过这一次写入，调用方就不该再发第二份事件。
        """
        fresh: list[str] = []
        with self._lock:
            for raw in rel_paths:
                rel = str(raw).replace("\\", "/")
                sig = _sig(self.root / rel)
                known = self._snapshot.scripts.get(rel) if self._snapshot is not None else None
                if known != sig or rel in self._pending.scripts:
                    fresh.append(rel)
                if self._snapshot is not None:
                    if sig is None:
                        self._snapshot.scripts.pop(rel, None)
                    else:
                        self._snapshot.scripts[rel] = sig
                self._pending.scripts.discard(rel)
            if not self._pending:
                self._first_seen = self._last_seen = None
        return fresh

    # ---------------- 结算一批 ----------------
    def _dispatch(self, batch: Delta) -> None:
        """一批变化 → 至多一次刷新、至多一组事件。

        顺序是有理由的：**先作废 worker，再刷新，最后才告诉前端**。反过来
        的话，前端收到「这张图变了」立刻重渲染，而池子里还是那个装着旧代码
        的热 worker——用户看到的是「改了没生效」，再点一次却好了。
        """
        # 停了就别再动。`stop()` 会清掉 pending，但**这一批可能已经被取走**
        # 了——线程刚把它从 `_pending` 里换出来，`stop()` 才落下。只靠清
        # pending 的话，那一批照旧会发出去，而它的 `pj` 对前端已经不存在。
        if self.stop_event.is_set():
            return
        registered = set(self.ctx.registry.all_scripts())
        touched_scripts = sorted(batch.scripts & registered)
        style_changed = any(_is_style_module(rel) for rel in batch.scripts)

        if style_changed:
            # 共享样式变了：本项目全部作废（别的项目一个都不动）。
            LOG.info("共享样式模块变更，作废本项目全部渲染会话")
            pool.invalidate_project(str(self.root))
        else:
            for rel in touched_scripts:
                pool.invalidate(rel, str(self.root))

        paths = self._refreshable_paths(batch)
        if paths:
            self._refresh(paths)

        # `panel.file_changed` 只发给**还在磁盘上**的已登记脚本：删掉的那些
        # 让前端去重渲染只会得到一个错误，「源文件不见了」是 readiness 的
        # 事实（Prompt 07），由刷新的 `registry.changed` 与后续的就绪度模型
        # 表达，不该伪装成一次内容变更。
        alive = [rel for rel in touched_scripts if (self.root / rel).exists()]
        if alive and self.sink.script_changed is not None:
            try:
                self.sink.script_changed(alive)
            except Exception:  # noqa: BLE001 — 回调抛出不许弄死 watcher 线程
                LOG.exception("watcher 回调抛出，已忽略")

    def _refreshable_paths(self, batch: Delta) -> list[str]:
        """摘掉「我们自己刚写下的那一份注册表」之后，这批还剩什么。

        统一刷新自己会写 `tavotto_registry.json`，watcher 下一轮必然看到它。
        认不出来的话，每一次刷新都会触发下一次刷新——一个永不停歇的循环。

        判据是 `project_refresh.is_self_written()`（**内容修订号**），不是
        「写完之后忽略两秒」：时间窗在慢磁盘上不够长、在快机器上又会吞掉
        用户紧接着做出的真实修改，而内容比较两头都不会错。

        注意摘掉的只是**注册表那几个路径**，不是整批：一次保存完全可能同时
        改了脚本、生成了图片，并让刷新回写了注册表——那时仍然要刷新。
        """
        keep = Delta(scripts=set(batch.scripts), assets=set(batch.assets))
        if batch.registry:
            authoritative = registry.registry_path(self.root).name
            self_written = project_refresh.is_self_written(self.ctx)
            keep.registry = {
                name for name in batch.registry if not (name == authoritative and self_written)
            }
            if batch.registry and not keep.registry:
                LOG.debug("注册表变化来自本次刷新自己的写入，不再触发第二轮")
        return keep.paths()

    def _refresh(self, paths: list[str]) -> None:
        if self.sink.refresh is None:
            return
        try:
            self.sink.refresh(paths)
        except project_refresh.RefreshError as exc:
            # 刷新失败是**可恢复**的：内存里的注册表原封不动（ADR 0025），
            # watcher 线程继续。注册表被改成非法 JSON 走的正是这条路——
            # 修好之后的那次写入会被下一轮 diff 看到，自动重试。
            LOG.warning("watcher 触发的刷新失败（%s），保留上一次的状态", exc.code)
            if self.sink.error is not None:
                try:
                    self.sink.error(exc.code, exc.params)
                except Exception:  # noqa: BLE001
                    LOG.exception("watcher 错误回调抛出，已忽略")
        except Exception:  # noqa: BLE001 — 任何一次异常都不许永久杀死线程
            LOG.exception("watcher 触发的刷新抛出未预期异常，保留上一次的状态")

    # ---------------- 线程主体 ----------------
    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                self.poll()
            except Exception:  # noqa: BLE001 — 兜底：一轮的异常不该终结整个循环
                LOG.exception("watcher 轮询异常，继续下一轮")

    def stop(self) -> None:
        """停下线程，并丢掉还没结算的那一批。

        项目关闭之后再发事件是错的：那个 pj 对前端已经不存在，而对后端
        `ctx` 也已经从 `PROJECTS` 里摘走了。
        """
        self.stop_event.set()
        self._pending = Delta()


# ---------------------------------------------------------------------------
# 注册表（每个项目一个 watcher）
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_watchers: dict[str, ProjectWatcher] = {}


def _key(figures_dir: str | Path) -> str:
    """与 `pool._norm_dir()` / `app._project_id()` 同一把尺（按卷判大小写
    敏感性）。三处分头判断的话，一个认为是同一个项目、另一个认为是两个。"""
    return pool.norm_dir(figures_dir)


def start(
    ctx,
    *,
    sink: WatchSink | None = None,
    interval: float = DEFAULT_INTERVAL,
    debounce: float = DEFAULT_DEBOUNCE,
    max_batch: float = DEFAULT_MAX_BATCH,
) -> ProjectWatcher:
    """开始盯一个项目；同一路径重复调用**替换**旧 watcher（不叠加线程）。

    `ctx` 要三样东西：`path`、`id`、`registry`——与 `refresh_project_index()`
    的要求相同。
    """
    key = _key(ctx.path)
    watcher = ProjectWatcher(
        ctx, sink=sink, interval=interval, debounce=debounce, max_batch=max_batch
    )
    watcher.prime()
    with _lock:
        old = _watchers.get(key)
        _watchers[key] = watcher
    if old is not None:
        old.stop()  # 放在锁外：stop 只是 set 一个 Event，但别在锁里做任何等待
    threading.Thread(
        target=watcher.run, daemon=True, name=f"tavotto-project-watch-{key[-24:]}"
    ).start()
    return watcher


def stop(figures_dir: str | Path | None = None) -> None:
    """停掉一个项目的 watcher；不给目录则全停（进程退出 / 测试隔离）。"""
    with _lock:
        if figures_dir is None:
            victims = list(_watchers.values())
            _watchers.clear()
        else:
            w = _watchers.pop(_key(figures_dir), None)
            victims = [w] if w is not None else []
    for w in victims:
        w.stop()


def absorb(figures_dir: str | Path, rel_paths: Iterable[str]) -> list[str] | None:
    """`ProjectWatcher.absorb` 的模块级入口。**没有 watcher 时回 `None`**——
    与「有 watcher 但它已经处理过」（空列表）是两回事：前者调用方要自己把
    作废 / 事件做全，后者一件都不该再做。"""
    with _lock:
        w = _watchers.get(_key(figures_dir))
    if w is None:
        return None
    return w.absorb(rel_paths)


def watched_dirs() -> list[str]:
    """当前被盯着的项目（诊断用）。"""
    with _lock:
        return list(_watchers)


def watcher_of(figures_dir: str | Path) -> ProjectWatcher | None:
    """取某个项目的 watcher（诊断与测试用）。"""
    with _lock:
        return _watchers.get(_key(figures_dir))
