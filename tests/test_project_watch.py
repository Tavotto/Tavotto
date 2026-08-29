"""项目级文件 watcher（Prompt 05 / `engine/project_watch.py`）。

守五件事：

1. **发现得全**——新增 / 删除 / 重命名 / 原子替换 / 注册表 / 素材，
   老的「盯已登记脚本 mtime」那一版一个都发现不了；
2. **只发现，不解释**——watcher 调统一刷新，不自己 merge、不自己发
   `registry.changed` / `assets.changed`（ADR 0025）；
3. **一次保存最多一次刷新**——防抖把一批连续写入并成一批；
4. **不循环**——刷新自己写的注册表认得出来，而外部紧接着再改仍要触发；
5. **不执行用户脚本、不 probe**（共享规则 §4）。

用例分两层：**语义层**用假时钟逐轮驱动 `poll()`（确定，没有 sleep），
**生命周期层**才用真线程 + `wait_until`。靠 `time.sleep(2.5)` 写 watcher
用例既慢又会偶发红，而偶发红最后总是被当成基础设施抖动忽略掉。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import (
    discover as engine_discover,
    pool as engine_pool,
    probe as engine_probe,
    project_refresh as engine_refresh,
    project_watch as engine_watch,
    registry as engine_registry,
)

# 静态可识别（有 main、有字面量 savefig），**同时是一枚运行探针**：真跑起来
# 会在图库里留下 RAN.txt。于是「watcher 没有执行用户脚本」这条不靠有没有打桩
# 来证明，而是靠磁盘上有没有那个文件。
CANARY = """\
from pathlib import Path


def main():
    Path("RAN.txt").write_text("executed", encoding="utf-8")
    fig.savefig("{stem}.pdf")
"""


# ---------------------------------------------------------------------------
# 夹具与小工具
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_stray_watchers():
    """每条用例前后都把 watcher 表清空。

    watcher 是 daemon 线程：漏掉一个不会让这条用例红，而是让**后面某一条**
    在完全无关的文件里红（它还在拍快照、还在调刷新）。
    """
    engine_watch.stop()
    yield
    engine_watch.stop()
    m.reset_projects()


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


@pytest.fixture
def sse_spy(monkeypatch):
    events: list[tuple[str, dict]] = []
    real = m.sse_publish
    monkeypatch.setattr(
        m, "sse_publish", lambda ev, data: (events.append((ev, data)), real(ev, data))[1]
    )
    return events


class FakeClock:
    """可注入的单调时钟：防抖窗口因此是**断言**，不是等待。"""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _recording_sink(*, refresh=None, script_changed=None, error=None):
    """返回 (sink, calls)。`calls` 三张表分别对应三个出口。"""
    calls: dict[str, list] = {"refresh": [], "script_changed": [], "error": []}

    def _refresh(paths):
        calls["refresh"].append(list(paths))
        if refresh is not None:
            refresh(paths)

    def _script_changed(scripts):
        calls["script_changed"].append(list(scripts))
        if script_changed is not None:
            script_changed(scripts)

    def _error(code, params):
        calls["error"].append((code, dict(params)))
        if error is not None:
            error(code, params)

    return engine_watch.WatchSink(
        refresh=_refresh, script_changed=_script_changed, error=_error
    ), calls


class _FakeCtx:
    """watcher 只要 ctx 的三样东西：path / id / registry。"""

    def __init__(self, path: Path, pid: str = "pj-test") -> None:
        self.path = Path(path)
        self.id = pid
        self.registry = engine_registry.Registry()


def _project(tmp_path, name="figs") -> Path:
    figs = tmp_path / name
    figs.mkdir()
    return figs


def _script(figs: Path, name: str, stem: str) -> Path:
    p = figs / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CANARY.format(stem=stem), encoding="utf-8")
    return p


def _pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(path)
    doc.close()


def _raster(path: Path, width: int = 8) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, width), False)
    pix.clear_with(200)
    pix.save(path)


def _write_registry(figs: Path, scripts: dict, name: str = "tavotto_registry.json") -> None:
    (figs / name).write_text(json.dumps({"version": 1, "scripts": scripts}), encoding="utf-8")


def _watcher(ctx, sink=None, *, clock=None, debounce=0.0, max_batch=1e9):
    """建一个**不启动线程**的 watcher，用例自己调 `poll()`。"""
    w = engine_watch.ProjectWatcher(
        ctx,
        sink=sink,
        interval=0.01,
        debounce=debounce,
        max_batch=max_batch,
        clock=clock or FakeClock(),
    )
    w.prime()
    return w


def wait_until(pred, timeout: float = 5.0, tick: float = 0.01) -> bool:
    """等一个条件成立；成立即返回，不成立最多等 `timeout` 秒。

    与 `time.sleep(2.5)` 的差别不只是快：固定睡眠在慢机器上会**漏**（睡醒了
    事情还没发生 → 偶发红），在快机器上又白白浪费时间。这里两头都对。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(tick)
    return pred()


def _touch(path: Path, *, ns: int) -> None:
    """把 mtime 钉到一个确定值（不动内容/长度）。"""
    os.utime(path, ns=(ns, ns))


# ---------------------------------------------------------------------------
# 快照与签名
# ---------------------------------------------------------------------------
class TestSnapshot:
    def test_signature_notices_a_same_size_rewrite(self, tmp_path):
        """就地改写、长度不变：**mtime 这一维**必须量得到。

        「保存后长度碰巧一样」在改一个数字、调一个颜色时极其常见。
        """
        figs = _project(tmp_path)
        p = figs / "a.py"
        p.write_text("x = 1\n", encoding="utf-8")
        before = engine_watch.take_snapshot(figs)

        p.write_text("x = 2\n", encoding="utf-8")  # 逐字节等长
        assert p.stat().st_size == before.scripts["a.py"][0]
        assert engine_watch.diff_snapshots(before, engine_watch.take_snapshot(figs)).scripts == {
            "a.py"
        }

    def test_signature_notices_a_same_mtime_rewrite(self, tmp_path):
        """长度变了、mtime 没变：**size 这一维**必须量得到。

        粗粒度时间戳的文件系统（FAT32、部分网络盘）上，一秒内的两次保存
        mtime 完全相同——只有 size 能把它救回来。这里把 mtime 钉死来模拟。
        """
        figs = _project(tmp_path)
        p = figs / "a.py"
        p.write_text("x = 1\n", encoding="utf-8")
        _touch(p, ns=1_000_000_000)
        before = engine_watch.take_snapshot(figs)

        p.write_text("x = 1234567\n", encoding="utf-8")
        _touch(p, ns=1_000_000_000)
        assert p.stat().st_mtime_ns == before.scripts["a.py"][1]
        assert engine_watch.diff_snapshots(before, engine_watch.take_snapshot(figs)).scripts == {
            "a.py"
        }

    def test_script_scope_matches_discover(self, tmp_path):
        """脚本快照的口径与 `discover` **逐个相同**。

        盯得比 discover 宽 = 为一个永远进不了注册表的文件反复刷新；
        盯得比它窄 = 用户新建的脚本发现不了。两边分头写判据必然分叉。
        """
        figs = _project(tmp_path)
        _script(figs, "fig1.py", "Fig1")
        _script(figs, "panels/fig2.py", "Fig2")
        (figs / "paper_style.py").write_text("# style\n", encoding="utf-8")
        (figs / "node_modules").mkdir()
        (figs / "node_modules" / "junk.py").write_text("x = 1\n", encoding="utf-8")
        (figs / "tavottofile").mkdir()
        (figs / "tavottofile" / "note.py").write_text("x = 1\n", encoding="utf-8")
        deep = figs / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        (deep / "too_deep.py").write_text("x = 1\n", encoding="utf-8")

        snap = engine_watch.take_snapshot(figs)
        expected = {
            engine_discover.rel_key(p, figs) for p in engine_discover.iter_all_scripts(figs)
        }
        assert set(snap.scripts) == expected
        assert "paper_style.py" in snap.scripts  # 基础设施脚本也要盯
        assert "node_modules/junk.py" not in snap.scripts
        assert "tavottofile/note.py" not in snap.scripts

    def test_asset_scope_matches_the_refresh_predicate(self, tmp_path):
        """素材快照用的是 `project_refresh.iter_assets()`——「哪些文件算素材」
        的唯一出处（`/api/panels` 与刷新 diff 共用同一把尺）。"""
        figs = _project(tmp_path)
        _pdf(figs / "Fig1.pdf")
        _raster(figs / "Fig2.png")
        _raster(figs / "Fig3.jpg")
        (figs / "notes.txt").write_text("hi", encoding="utf-8")

        snap = engine_watch.take_snapshot(figs)
        expected = {str(p.relative_to(figs)) for p, _ in engine_refresh.iter_assets(figs)}
        assert set(snap.assets) == expected
        assert "notes.txt" not in snap.assets

    def test_an_unreadable_project_dir_is_not_an_empty_project(self, tmp_path):
        """目录暂时不可用返回 `None`，**不是**一张空快照。

        空快照与「用户删光了所有文件」在 diff 里长得一模一样，照它行事会把
        整个项目的 worker 全部作废——而真正发生的可能只是网盘掉了两秒。
        """
        figs = _project(tmp_path)
        _script(figs, "fig1.py", "Fig1")
        assert engine_watch.take_snapshot(figs) is not None
        assert engine_watch.take_snapshot(tmp_path / "gone") is None


# ---------------------------------------------------------------------------
# 变化类型
# ---------------------------------------------------------------------------
class TestChangeKinds:
    def _armed(self, tmp_path, registered: dict | None = None):
        """一个已 prime 的 watcher + 记录用的 sink。"""
        figs = _project(tmp_path)
        _script(figs, "fig1.py", "Fig1")
        _pdf(figs / "Fig1.pdf")
        ctx = _FakeCtx(figs)
        ctx.registry.load_data(
            {
                "version": 1,
                "scripts": registered or {"fig1.py": {"entry": "main", "stems": ["Fig1"]}},
            }
        )
        sink, calls = _recording_sink()
        return figs, ctx, _watcher(ctx, sink), calls

    def test_registered_script_edited(self, tmp_path, monkeypatch):
        """内容改了：worker 作废 → 一次刷新 → `panel.file_changed`。"""
        figs, ctx, w, calls = self._armed(tmp_path)
        killed: list[tuple[str, str]] = []
        monkeypatch.setattr(engine_pool, "invalidate", lambda s, d: killed.append((s, d)))

        (figs / "fig1.py").write_text(CANARY.format(stem="Fig1") + "# edited\n", encoding="utf-8")
        w.poll()

        assert killed == [("fig1.py", str(figs))]
        assert calls["refresh"] == [["fig1.py"]]
        assert calls["script_changed"] == [["fig1.py"]]

    def test_new_unregistered_script_is_discovered(self, tmp_path):
        """**老 watcher 永远发现不了的那一类**：注册表里没有它，于是不在
        「要盯的清单」上。新建脚本要触发静态刷新，但不发 `panel.file_changed`
        ——那是「重渲染这张已有的图」的信号。"""
        figs, ctx, w, calls = self._armed(tmp_path)
        _script(figs, "fig_new.py", "FigNew")
        w.poll()

        assert calls["refresh"] == [["fig_new.py"]]
        assert calls["script_changed"] == []

    def test_an_unregistered_script_invalidates_nothing(self, tmp_path, monkeypatch):
        """新建一个还没登记的 `.py`：刷新要触发，但**一个会话都不该被打掉**。

        它还没有对应的 worker，而"顺手把已有的都作废一遍"是几十秒的冷启动
        ——用户会以为是自己点坏了什么。
        """
        figs, ctx, w, calls = self._armed(tmp_path)
        killed: list[str] = []
        monkeypatch.setattr(engine_pool, "invalidate", lambda s, d: killed.append(s))
        monkeypatch.setattr(engine_pool, "invalidate_project", lambda d: killed.append("*"))

        _script(figs, "fig_new.py", "FigNew")
        w.poll()
        assert calls["refresh"] == [["fig_new.py"]]
        assert killed == []

    def test_deleted_script_invalidates_but_does_not_ask_for_a_rerender(
        self, tmp_path, monkeypatch
    ):
        """删除：worker 作废 + 刷新，**不发** `panel.file_changed`。

        对一个源文件已经不在的面板发「内容变了」，前端照做只会得到一个
        渲染错误。「源文件不见了」是就绪度的事实（Prompt 07），不该伪装成
        一次内容变更。
        """
        figs, ctx, w, calls = self._armed(tmp_path)
        killed: list[str] = []
        monkeypatch.setattr(engine_pool, "invalidate", lambda s, d: killed.append(s))

        (figs / "fig1.py").unlink()
        w.poll()

        assert killed == ["fig1.py"]
        assert calls["refresh"] == [["fig1.py"]]
        assert calls["script_changed"] == []

    def test_rename_is_one_batch(self, tmp_path):
        """轮询里的重命名 = 旧路径消失 + 新路径出现。两头在**同一批**里，
        因此只刷新一次（而不是删一次、加一次各刷一遍）。"""
        figs, ctx, w, calls = self._armed(tmp_path)
        (figs / "fig1.py").rename(figs / "fig1_renamed.py")
        w.poll()

        assert calls["refresh"] == [["fig1.py", "fig1_renamed.py"]]

    def test_atomic_replace_is_detected(self, tmp_path):
        """编辑器的标准保存法：写临时文件 → fsync → rename 覆盖目标。

        旧实现量的是「**这个文件**的 mtime 变了没有」，而原子替换之后
        「这个文件」已经是另一个 inode 了——判据的主语从一开始就错了。
        快照量的是「这条路径现在是什么」，两种写法都盖得住。
        """
        figs, ctx, w, calls = self._armed(tmp_path)
        target = figs / "fig1.py"
        tmp = figs / "fig1.py.tmp"
        tmp.write_text(CANARY.format(stem="Fig1") + "# replaced\n", encoding="utf-8")
        os.replace(tmp, target)  # ← 原子替换
        w.poll()

        assert calls["refresh"] == [["fig1.py"]]
        assert calls["script_changed"] == [["fig1.py"]]

    def test_style_module_invalidates_the_whole_project_only(self, tmp_path, monkeypatch):
        """共享样式变了：本项目**全部**作废，别的项目一个都不动。"""
        figs, ctx, w, calls = self._armed(tmp_path)
        (figs / "paper_style.py").write_text("# v1\n", encoding="utf-8")
        w.poll()
        calls["refresh"].clear()

        whole: list[str] = []
        single: list[str] = []
        monkeypatch.setattr(engine_pool, "invalidate_project", lambda d: whole.append(d))
        monkeypatch.setattr(engine_pool, "invalidate", lambda s, d: single.append(s))

        (figs / "paper_style.py").write_text("# v2 —— 改了配色\n", encoding="utf-8")
        w.poll()

        assert whole == [str(figs)]
        assert single == []  # 整项目作废之后不再逐个来一遍

    def test_new_registry_edited_externally(self, tmp_path):
        figs, ctx, w, calls = self._armed(tmp_path)
        _write_registry(figs, {"fig1.py": {"entry": "render", "stems": ["Fig1"]}})
        w.poll()
        assert calls["refresh"] == [["tavotto_registry.json"]]

    def test_legacy_registry_edited_externally(self, tmp_path):
        """旧名（`mm_registry.json`）也要盯：从 legacy 迁过来的项目在第一次
        刷新之前，磁盘上只有它。"""
        figs, ctx, w, calls = self._armed(tmp_path)
        _write_registry(figs, {"fig1.py": {"entry": "main", "stems": ["Fig1"]}}, "mm_registry.json")
        w.poll()
        assert calls["refresh"] == [["mm_registry.json"]]

    def test_registry_deleted(self, tmp_path):
        figs, ctx, w, calls = self._armed(tmp_path)
        _write_registry(figs, {"fig1.py": {"entry": "main", "stems": ["Fig1"]}})
        w.poll()
        calls["refresh"].clear()

        (figs / "tavotto_registry.json").unlink()
        w.poll()
        assert calls["refresh"] == [["tavotto_registry.json"]]

    def test_new_pdf(self, tmp_path):
        figs, ctx, w, calls = self._armed(tmp_path)
        _pdf(figs / "Fig9.pdf")
        w.poll()
        assert calls["refresh"] == [["Fig9.pdf"]]

    def test_modified_png(self, tmp_path):
        figs, ctx, w, calls = self._armed(tmp_path)
        _raster(figs / "Fig2.png", width=8)
        w.poll()
        calls["refresh"].clear()

        _raster(figs / "Fig2.png", width=16)  # 内容与长度都变了
        w.poll()
        assert calls["refresh"] == [["Fig2.png"]]

    def test_deleted_jpg(self, tmp_path):
        figs, ctx, w, calls = self._armed(tmp_path)
        _raster(figs / "Fig3.jpg")
        w.poll()
        calls["refresh"].clear()

        (figs / "Fig3.jpg").unlink()
        w.poll()
        assert calls["refresh"] == [["Fig3.jpg"]]

    def test_renamed_asset_is_one_batch(self, tmp_path):
        figs, ctx, w, calls = self._armed(tmp_path)
        _raster(figs / "Fig3.png")
        w.poll()
        calls["refresh"].clear()

        (figs / "Fig3.png").rename(figs / "Fig4.png")
        w.poll()
        assert calls["refresh"] == [["Fig3.png", "Fig4.png"]]

    def test_a_new_image_does_not_invalidate_unrelated_workers(self, tmp_path, monkeypatch):
        """新增一张不相干的图片不该打掉任何渲染会话——那是几十秒的冷启动，
        用户会以为自己点坏了什么。"""
        figs, ctx, w, calls = self._armed(tmp_path)
        killed: list[str] = []
        monkeypatch.setattr(engine_pool, "invalidate", lambda s, d: killed.append(s))
        monkeypatch.setattr(engine_pool, "invalidate_project", lambda d: killed.append("*"))

        _pdf(figs / "Unrelated.pdf")
        w.poll()
        assert killed == []
        assert calls["refresh"] == [["Unrelated.pdf"]]


# ---------------------------------------------------------------------------
# 防抖与批次
# ---------------------------------------------------------------------------
class TestBatching:
    def _armed(self, tmp_path, *, debounce=0.5, max_batch=1e9):
        figs = _project(tmp_path)
        ctx = _FakeCtx(figs)
        ctx.registry.load_data({"version": 1, "scripts": {}})
        clock = FakeClock()
        sink, calls = _recording_sink()
        return (
            figs,
            _watcher(ctx, sink, clock=clock, debounce=debounce, max_batch=max_batch),
            (
                clock,
                calls,
            ),
        )

    def test_a_burst_of_writes_becomes_one_refresh(self, tmp_path):
        """一次编辑器保存动作（脚本 + 稍后生成的图）→ **最多一次刷新**。"""
        figs, w, (clock, calls) = self._armed(tmp_path)

        _script(figs, "fig1.py", "Fig1")
        w.poll()
        assert calls["refresh"] == []  # 还没安静，先攒着

        clock.advance(0.1)
        _pdf(figs / "Fig1.pdf")
        w.poll()
        assert calls["refresh"] == []  # 新变化把批次的结束**往后推**

        clock.advance(0.6)  # 安静够久了
        w.poll()
        assert calls["refresh"] == [["Fig1.pdf", "fig1.py"]]

    def test_a_never_quiet_directory_still_gets_refreshed(self, tmp_path):
        """防抖是「等安静」，而目录可能**永远不安静**（脚本正在跑、正在拷
        一个大目录）。没有年龄上限的话，刷新会被无限期推迟。"""
        figs, w, (clock, calls) = self._armed(tmp_path, debounce=0.5, max_batch=1.0)

        for i in range(6):  # 每一轮都有新变化 → 防抖永远不满足
            _pdf(figs / f"Fig{i}.pdf")
            clock.advance(0.3)
            w.poll()

        assert len(calls["refresh"]) == 1
        assert len(calls["refresh"][0]) >= 4

    def test_changes_during_a_refresh_are_not_lost(self, tmp_path):
        """刷新执行期间到达的写入进**下一批**，不丢。

        快照在 dispatch **之前**就换掉了，所以刷新期间落盘的文件会与那张新
        快照比较。反过来（处理完再换快照）会把它算进已经结算的这一批，
        下一轮 diff 为空——那正是「保存了没反应」最难查的成因。
        """
        figs = _project(tmp_path)
        ctx = _FakeCtx(figs)
        ctx.registry.load_data({"version": 1, "scripts": {}})
        clock = FakeClock()

        def _slow_refresh(paths):
            # 「刷新正在跑」的时候用户又保存了一次
            if len(calls["refresh"]) == 1:
                _pdf(figs / "DuringRefresh.pdf")

        sink, calls = _recording_sink(refresh=_slow_refresh)
        w = _watcher(ctx, sink, clock=clock, debounce=0.0)

        _script(figs, "fig1.py", "Fig1")
        w.poll()
        assert calls["refresh"] == [["fig1.py"]]

        clock.advance(1.0)
        w.poll()
        assert calls["refresh"][1] == ["DuringRefresh.pdf"]

    def test_stop_cancels_the_pending_batch(self, tmp_path):
        """项目关掉之后不许再发事件：那个 pj 对前端已经不存在了。"""
        figs, w, (clock, calls) = self._armed(tmp_path)
        _script(figs, "fig1.py", "Fig1")
        w.poll()
        assert calls["refresh"] == []

        w.stop()
        clock.advance(10.0)
        w.poll()
        assert calls["refresh"] == []


# ---------------------------------------------------------------------------
# 自写循环防护
# ---------------------------------------------------------------------------
class TestSelfWriteLoop:
    """刷新自己会写 `tavotto_registry.json`，watcher 下一轮必然看到它。

    认不出来的话，每一次刷新都会触发下一次刷新——一个永不停歇的循环。
    """

    def _real_refresh_watcher(self, client, tmp_path):
        figs = _project(tmp_path)
        _script(figs, "fig1.py", "Fig1")
        _pdf(figs / "Fig1.pdf")
        body = client.post("/api/projects/open", json={"path": str(figs)}).get_json()
        ctx = m.PROJECTS[body["id"]]
        engine_watch.stop()  # 换成用例自己驱动的那一个（不叠加线程）
        sink, calls = _recording_sink(
            refresh=lambda paths: m.refresh_project(ctx, reason="watcher", changed_paths=paths)
        )
        return figs, ctx, _watcher(ctx, sink), calls

    def test_our_own_registry_write_does_not_loop(self, client, tmp_path):
        figs, ctx, w, calls = self._real_refresh_watcher(client, tmp_path)

        _script(figs, "fig2.py", "Fig2")
        _pdf(figs / "Fig2.pdf")
        w.poll()  # 发现新脚本 → 刷新 → 刷新自己回写了注册表
        assert len(calls["refresh"]) == 1
        assert json.loads((figs / "tavotto_registry.json").read_text(encoding="utf-8"))[
            "scripts"
        ].get("fig2.py")

        w.poll()  # 看到那次回写
        w.poll()  # 再看一轮，确认不是「延后了一拍」
        assert len(calls["refresh"]) == 1, "刷新自己写的注册表把 watcher 带进了循环"

    def test_an_external_edit_right_after_our_write_still_triggers(self, client, tmp_path):
        """**判据是内容修订号，不是「写完忽略两秒」。** 时间窗在慢磁盘上不够
        长，在快机器上又会吞掉用户紧接着做出的真实修改。"""
        figs, ctx, w, calls = self._real_refresh_watcher(client, tmp_path)

        _script(figs, "fig2.py", "Fig2")
        _pdf(figs / "Fig2.pdf")
        w.poll()
        w.poll()
        assert len(calls["refresh"]) == 1

        cfg = json.loads((figs / "tavotto_registry.json").read_text(encoding="utf-8"))
        cfg["scripts"]["fig1.py"]["notes"] = "用户在编辑器外改的"
        (figs / "tavotto_registry.json").write_text(json.dumps(cfg), encoding="utf-8")
        w.poll()
        assert len(calls["refresh"]) == 2, "自写之后的外部修改被误当成自己写的了"

    def test_a_self_written_registry_does_not_swallow_the_rest_of_the_batch(self, client, tmp_path):
        """摘掉的只是**注册表那几个路径**，不是整批。

        一次保存完全可能同时改了脚本、生成了图片，并让刷新回写了注册表——
        那时仍然要刷新，否则用户新加的那张图会被自写防护顺手吞掉。
        """
        figs, ctx, w, calls = self._real_refresh_watcher(client, tmp_path)
        _script(figs, "fig2.py", "Fig2")
        _pdf(figs / "Fig2.pdf")
        w.poll()
        assert len(calls["refresh"]) == 1

        _pdf(figs / "Later.pdf")  # 注册表的自写与这张新图落在同一批
        w.poll()
        assert len(calls["refresh"]) == 2
        assert calls["refresh"][1] == ["Later.pdf"]  # 注册表被摘掉，图还在


# ---------------------------------------------------------------------------
# 错误处理
# ---------------------------------------------------------------------------
class TestErrors:
    def test_broken_registry_reports_and_recovers(self, client, tmp_path):
        """非法 JSON：发一条可操作的错误、**保留上一次有效的注册表**、
        线程继续；文件修好之后自动恢复。"""
        figs = _project(tmp_path)
        _script(figs, "fig1.py", "Fig1")
        _pdf(figs / "Fig1.pdf")
        body = client.post("/api/projects/open", json={"path": str(figs)}).get_json()
        ctx = m.PROJECTS[body["id"]]
        engine_watch.stop()
        m.refresh_project(ctx, reason="manual")
        good = dict(ctx.registry.entries())
        assert good

        sink, calls = _recording_sink(
            refresh=lambda paths: m.refresh_project(ctx, reason="watcher", changed_paths=paths)
        )
        w = _watcher(ctx, sink)

        (figs / "tavotto_registry.json").write_text("{ 这不是 JSON", encoding="utf-8")
        w.poll()
        assert [c for c, _ in calls["error"]] == ["scan_failed"]
        assert dict(ctx.registry.entries()) == good, "刷新失败时内存里那份被动过了"

        (figs / "tavotto_registry.json").write_text(
            json.dumps({"version": 1, "scripts": good}), encoding="utf-8"
        )
        w.poll()
        assert len(calls["refresh"]) == 2
        assert len(calls["error"]) == 1, "修好之后不该再报错"

    def test_a_throwing_callback_does_not_kill_the_loop(self, tmp_path):
        """回调抛出不许弄死 watcher：下一轮照常工作。"""
        figs = _project(tmp_path)
        _script(figs, "fig1.py", "Fig1")
        ctx = _FakeCtx(figs)
        ctx.registry.load_data(
            {"version": 1, "scripts": {"fig1.py": {"entry": "main", "stems": ["Fig1"]}}}
        )

        def _boom(_paths):
            raise RuntimeError("回调炸了")

        sink, calls = _recording_sink(refresh=_boom, script_changed=_boom)
        w = _watcher(ctx, sink)

        (figs / "fig1.py").write_text("# v2\n", encoding="utf-8")
        w.poll()  # 两个回调都抛了，poll 本身不该抛
        _pdf(figs / "Later.pdf")
        w.poll()
        assert len(calls["refresh"]) == 2

    def test_a_vanished_project_dir_is_not_a_mass_delete(self, tmp_path, monkeypatch):
        """目录暂时不见：这一轮什么都不做（不刷新、不作废），**也不空转**。

        「看不见」不等于「不存在」——把它当成「用户删光了」会在一次网盘抖动
        之后打掉整个项目的渲染会话。
        """
        figs = _project(tmp_path)
        _script(figs, "fig1.py", "Fig1")
        ctx = _FakeCtx(figs)
        ctx.registry.load_data(
            {"version": 1, "scripts": {"fig1.py": {"entry": "main", "stems": ["Fig1"]}}}
        )
        sink, calls = _recording_sink()
        w = _watcher(ctx, sink)

        moved = tmp_path / "moved_away"
        figs.rename(moved)
        for _ in range(3):
            w.poll()
        assert calls["refresh"] == []
        assert calls["script_changed"] == []

        # 「不高 CPU」的结构面：目录不在时**连遍历都不进**（每轮之间还有
        # `stop_event.wait(interval)` 挡着，所以线程也不会空转）。遍历入口
        # 一被碰就炸，于是这条不靠计时来断言。
        def _boom(*a, **k):  # pragma: no cover - 触发即失败
            raise AssertionError("目录不在时仍然遍历了整棵树")

        with monkeypatch.context() as mp:
            # 用 context 而不是 undo()：`monkeypatch` 是函数级 fixture，
            # conftest 的用户配置隔离用的是**同一个实例**，undo() 会把它一起
            # 还原掉（于是这条用例之后的代码会写到真实的用户配置目录）。
            mp.setattr(engine_discover, "iter_all_scripts", _boom)
            mp.setattr(engine_refresh, "iter_assets", _boom)
            w.poll()

        moved.rename(figs)  # 回来了
        _pdf(figs / "New.pdf")
        w.poll()
        assert calls["refresh"] == [["New.pdf"]]


# ---------------------------------------------------------------------------
# 「绝不静默执行用户脚本」
# ---------------------------------------------------------------------------
class TestNoSideEffects:
    def test_watcher_never_runs_user_code_or_probes(self, client, tmp_path, monkeypatch):
        """watcher 只 `stat()` 和读 AST：不 probe、不起 worker、不跑 main()。

        证据有两层：桩（起 worker / probe 的入口一被碰就炸）与磁盘
        （CANARY 真跑起来会留下 `RAN.txt`）。只有桩的话，一条绕开桩的新路径
        会静默地把这条用例变成空门禁。
        """
        figs = _project(tmp_path)
        _script(figs, "fig1.py", "Fig1")
        _pdf(figs / "Fig1.pdf")
        body = client.post("/api/projects/open", json={"path": str(figs)}).get_json()
        ctx = m.PROJECTS[body["id"]]
        engine_watch.stop()

        def boom(*a, **k):  # pragma: no cover - 触发即失败
            raise AssertionError("watcher 起了 worker / probe")

        monkeypatch.setattr(engine_pool, "get", boom)
        monkeypatch.setattr(engine_pool, "one_shot", boom)
        monkeypatch.setattr(engine_pool, "_new_worker", boom)
        monkeypatch.setattr(engine_probe, "probe", boom)
        monkeypatch.setattr(engine_probe, "probe_and_register", boom)

        sink, calls = _recording_sink(
            refresh=lambda paths: m.refresh_project(ctx, reason="watcher", changed_paths=paths)
        )
        w = _watcher(ctx, sink)

        _script(figs, "fig2.py", "Fig2")
        _pdf(figs / "Fig2.pdf")
        w.poll()
        w.poll()

        assert len(calls["refresh"]) == 1
        assert not (figs / "RAN.txt").exists(), "watcher 执行了用户脚本"


# ---------------------------------------------------------------------------
# 多项目与生命周期
# ---------------------------------------------------------------------------
class TestLifecycle:
    def test_two_projects_are_isolated(self, tmp_path):
        """一个项目的变化只触发它自己的刷新。"""
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        for figs in (a, b):
            _script(figs, "fig1.py", "Fig1")
        ctx_a, ctx_b = _FakeCtx(a, "pj-a"), _FakeCtx(b, "pj-b")
        for ctx in (ctx_a, ctx_b):
            ctx.registry.load_data(
                {"version": 1, "scripts": {"fig1.py": {"entry": "main", "stems": ["Fig1"]}}}
            )
        sink_a, calls_a = _recording_sink()
        sink_b, calls_b = _recording_sink()
        wa, wb = _watcher(ctx_a, sink_a), _watcher(ctx_b, sink_b)

        _pdf(a / "OnlyInA.pdf")
        wa.poll()
        wb.poll()
        assert calls_a["refresh"] == [["OnlyInA.pdf"]]
        assert calls_b["refresh"] == []

    def test_same_named_scripts_in_two_projects_do_not_cross(self, tmp_path, monkeypatch):
        """两个图库里同名的 `fig1.py`：作废的是**带项目路径**的那一把键。"""
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        for figs in (a, b):
            _script(figs, "fig1.py", "Fig1")
        ctx_a = _FakeCtx(a, "pj-a")
        ctx_a.registry.load_data(
            {"version": 1, "scripts": {"fig1.py": {"entry": "main", "stems": ["Fig1"]}}}
        )
        killed: list[tuple[str, str]] = []
        monkeypatch.setattr(engine_pool, "invalidate", lambda s, d: killed.append((s, d)))
        sink, _ = _recording_sink()
        wa = _watcher(ctx_a, sink)

        (a / "fig1.py").write_text("# 只有 A 改了\n", encoding="utf-8")
        wa.poll()
        assert killed == [("fig1.py", str(a))]

    def test_style_change_only_invalidates_its_own_project(self, tmp_path, monkeypatch):
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        for figs in (a, b):
            (figs / "paper_style.py").write_text("# v1\n", encoding="utf-8")
        ctx_a = _FakeCtx(a, "pj-a")
        ctx_a.registry.load_data({"version": 1, "scripts": {}})
        whole: list[str] = []
        monkeypatch.setattr(engine_pool, "invalidate_project", lambda d: whole.append(d))
        sink, _ = _recording_sink()
        wa = _watcher(ctx_a, sink)

        (a / "paper_style.py").write_text("# v2\n", encoding="utf-8")
        wa.poll()
        assert whole == [str(a)]

    def test_invalidate_project_only_touches_its_own_project(self, tmp_path):
        """`pool.invalidate_project()` 的项目隔离是**结构性**的：池键是
        `(项目, 脚本)`，所以另一个图库里同名的 `fig1.py` 不会被顺手打掉。

        这条不打桩——上面那些用例桩掉了 `invalidate_project` 本身，桩不会
        告诉你它内部的过滤条件写对了没有。
        """

        class _StubWorker:
            script_name = "fig1.py"

            def shutdown(self):
                pass

        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        key_a = (engine_pool.norm_dir(str(a)), "fig1.py")
        key_b = (engine_pool.norm_dir(str(b)), "fig1.py")
        with engine_pool._lock:
            engine_pool._workers[key_a] = _StubWorker()
            engine_pool._workers[key_b] = _StubWorker()
        try:
            engine_pool.invalidate_project(str(a))
            with engine_pool._lock:
                assert key_a not in engine_pool._workers
                assert key_b in engine_pool._workers, "别的项目的会话被打掉了"
        finally:
            with engine_pool._lock:
                engine_pool._workers.pop(key_a, None)
                engine_pool._workers.pop(key_b, None)

    def test_restarting_the_same_path_leaves_one_thread(self, tmp_path):
        """同路径重复 start 换掉旧的，**不叠加线程**。

        只从注册表里摘掉是不够的：那个线程还在跑、还在拍快照、还会继续
        调刷新。要量到旧那一个真的停了。
        """
        figs = _project(tmp_path)
        ctx = _FakeCtx(figs)
        ctx.registry.load_data({"version": 1, "scripts": {}})

        def _live() -> int:
            return sum(
                1 for t in threading.enumerate() if t.name.startswith("tavotto-project-watch")
            )

        base = _live()
        first = engine_watch.start(ctx, interval=0.02)
        assert wait_until(lambda: _live() == base + 1)

        second = engine_watch.start(ctx, interval=0.02)
        assert first.stop_event.is_set()
        assert second is engine_watch.watcher_of(figs)
        assert len(engine_watch.watched_dirs()) == 1
        assert wait_until(lambda: _live() == base + 1), "旧线程没退，两个 watcher 在同时跑"

        engine_watch.stop()
        assert wait_until(lambda: _live() == base)

    def test_close_project_stops_the_watcher(self, client, tmp_path):
        figs = _project(tmp_path)
        _pdf(figs / "Fig1.pdf")
        body = client.post("/api/projects/open", json={"path": str(figs)}).get_json()
        assert engine_watch.watched_dirs() == [engine_pool.norm_dir(str(figs))]
        w = engine_watch.watcher_of(figs)

        assert m.close_project(body["id"]) is True
        assert engine_watch.watched_dirs() == []
        assert w.stop_event.is_set()

    def test_reset_projects_stops_every_watcher(self, client, tmp_path):
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        for figs in (a, b):
            _pdf(figs / "Fig1.pdf")
            client.post("/api/projects/open", json={"path": str(figs)})
        assert len(engine_watch.watched_dirs()) == 2

        m.reset_projects()
        assert engine_watch.watched_dirs() == []

    def test_the_thread_survives_a_polling_exception(self, tmp_path, monkeypatch):
        """一轮里的任何异常都不该终结循环（`run()` 的兜底）。"""
        figs = _project(tmp_path)
        ctx = _FakeCtx(figs)
        ctx.registry.load_data({"version": 1, "scripts": {}})
        w = engine_watch.ProjectWatcher(ctx, interval=0.01)
        w.prime()

        polls: list[int] = []
        real_poll = engine_watch.ProjectWatcher.poll

        def flaky(self):
            polls.append(1)
            if len(polls) <= 2:
                raise RuntimeError("这一轮炸了")
            return real_poll(self)

        monkeypatch.setattr(engine_watch.ProjectWatcher, "poll", flaky)
        threading.Thread(target=w.run, daemon=True).start()
        try:
            assert wait_until(lambda: len(polls) >= 5), "第一次异常就把线程杀掉了"
        finally:
            w.stop()


# ---------------------------------------------------------------------------
# 端到端：真事件、真 pj
# ---------------------------------------------------------------------------
class TestEndToEnd:
    def test_events_carry_the_right_project(self, client, tmp_path, sse_spy):
        """watcher 发出去的每条事件都要带**本项目**的 pj——别的标签页才不会
        因为另一个图库的变动去重渲染自己的面板。"""
        figs = _project(tmp_path)
        _script(figs, "fig1.py", "Fig1")
        _pdf(figs / "Fig1.pdf")
        body = client.post("/api/projects/open", json={"path": str(figs)}).get_json()
        ctx = m.PROJECTS[body["id"]]
        engine_watch.stop()

        w = engine_watch.ProjectWatcher(ctx, sink=m._watch_sink(ctx), interval=0.01, debounce=0.0)
        w.prime()
        m.refresh_project(ctx, reason="manual")  # 让 fig1.py 真的进注册表
        sse_spy.clear()

        (figs / "fig1.py").write_text(CANARY.format(stem="Fig1") + "# edited\n", encoding="utf-8")
        _pdf(figs / "Fig2.pdf")
        w.poll()

        kinds = [ev for ev, _ in sse_spy]
        assert "panel.file_changed" in kinds
        assert "assets.changed" in kinds
        assert all(data.get("pj") == ctx.id for _, data in sse_spy), sse_spy
        panel = next(data for ev, data in sse_spy if ev == "panel.file_changed")
        assert panel["scripts"] == ["fig1.py"]
        assert panel["stems"] == ["Fig1"]

    def test_the_watcher_does_not_publish_a_second_registry_event(self, client, tmp_path, sse_spy):
        """`registry.changed` / `assets.changed` **只由统一刷新发**。watcher
        自己再发一份的话，前端会收到两条互相矛盾的 diff（ADR 0025）。"""
        figs = _project(tmp_path)
        _script(figs, "fig1.py", "Fig1")
        _pdf(figs / "Fig1.pdf")
        body = client.post("/api/projects/open", json={"path": str(figs)}).get_json()
        ctx = m.PROJECTS[body["id"]]
        engine_watch.stop()
        m.refresh_project(ctx, reason="manual")

        w = engine_watch.ProjectWatcher(ctx, sink=m._watch_sink(ctx), interval=0.01, debounce=0.0)
        w.prime()
        sse_spy.clear()

        _script(figs, "fig2.py", "Fig2")
        _pdf(figs / "Fig2.pdf")
        w.poll()

        kinds = [ev for ev, _ in sse_spy]
        assert kinds.count("registry.changed") == 1
        assert kinds.count("assets.changed") == 1
        assert all(data.get("reason") == "watcher" for _, data in sse_spy)

    def test_a_live_watcher_picks_up_a_new_script(self, client, tmp_path, sse_spy):
        """真线程跑一遍：打开项目 → 在编辑器里新建一个脚本 → 无须点刷新。

        上面全部用例都是逐轮驱动的（确定、快），这条负责证明**线程真的
        在跑**——`poll()` 全对而线程从没被启动过，那些绿是假的。
        """
        figs = _project(tmp_path)
        _pdf(figs / "Fig1.pdf")
        client.post("/api/projects/open", json={"path": str(figs)})
        assert len(engine_watch.watched_dirs()) == 1

        # 换成短周期的那一个（默认 2 秒轮询会让这条用例等太久）
        ctx = m.PROJECTS[next(iter(m.PROJECTS))]
        engine_watch.start(ctx, sink=m._watch_sink(ctx), interval=0.02, debounce=0.02)
        sse_spy.clear()

        _script(figs, "fig_live.py", "FigLive")
        _pdf(figs / "FigLive.pdf")
        assert wait_until(lambda: any(ev == "registry.changed" for ev, _ in sse_spy)), sse_spy
        payload = next(data for ev, data in sse_spy if ev == "registry.changed")
        assert "fig_live.py" in payload["added_scripts"]
        assert payload["pj"] == ctx.id
        assert not (figs / "RAN.txt").exists()
