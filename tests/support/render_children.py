"""用例结束时核对「本用例起的 render child 都已退出」（#1285）。conftest 的 autouse fixture 是薄壳，
逻辑放这里，tests/test_render_children_guard.py 才能直接对它做反证。

判据的主语：**本用例期间新建的 `RenderHost`** 起的 child——不是机器上所有 renderchild；进来之前就建好的宿主
（模块 / 会话级 fixture 持有的，首次使用才起 child）归它的 fixture，不算本用例。时刻是用例 teardown。进程级共享实例（`renderhost.shared()` 与旧代）是合法的会话级资源，但生命周期归用例：
按**模块**替它收（`reap_shared`，下一次 `shared()` 懒重建）。自己 `RenderHost(...)` 却没 `close()` 的没有这个豁免。
"""

from __future__ import annotations

import weakref

#: 本会话里 `RenderHost._start` 起过的 child：(宿主弱引用, 宿主序号, Popen)
_CHILDREN: list = []
_HOSTS_CREATED = 0
_PATCHED = False


def install() -> None:
    """包住 `RenderHost.__init__`（给宿主编序号）与 `_start`（登记它起的每个 child）。只装一次；与用例里
    monkeypatch `_start` 兼容（用例的包装拿到的 `real_start` 就是这个包装，登记照常发生）。"""
    global _PATCHED
    if _PATCHED:
        return
    from tavotto.rendercore import renderhost

    real_init = renderhost.RenderHost.__init__
    real_start = renderhost.RenderHost._start

    def _init(self, *args, **kwargs):
        global _HOSTS_CREATED
        real_init(self, *args, **kwargs)
        _HOSTS_CREATED += 1
        self._guard_seq = _HOSTS_CREATED

    def _start(self):
        real_start(self)
        if self._proc is not None:
            _CHILDREN[:] = [c for c in _CHILDREN if c[2].poll() is None]  # 已退出的不用留着
            _CHILDREN.append((weakref.ref(self), getattr(self, "_guard_seq", 0), self._proc))

    renderhost.RenderHost.__init__ = _init
    renderhost.RenderHost._start = _start
    _PATCHED = True


def mark() -> int:
    """此刻之后新建的宿主序号都 > 返回值。"""
    return _HOSTS_CREATED


def _shared_ids() -> set[int]:
    from tavotto.rendercore import renderhost

    return {id(h) for h in (renderhost._SHARED, *renderhost._SHARED_HOSTS) if h is not None}


def reap_shared() -> None:
    """模块 teardown：收掉进程级共享实例（含旧代）。它是懒建的会话级资源，生命周期按**模块**收，
    不按用例（每条用例都关会让下一条重起 child，大量用共享宿主的文件慢约两成，#1285 实测）；
    效果相同——共享 child 不会跨模块活下去，后面模块里的「此刻没有子进程」类断言看不到它。
    连绑定一起丢（`shutdown_shared()`）：只 close 的话，下一次 `shared()` 拿回同一个旧宿主。"""
    from tavotto.rendercore import renderhost

    renderhost.shutdown_shared()
    for host in tuple(renderhost._SHARED_HOSTS):
        host.close()


def reap(since: int) -> None:
    """用例 teardown：本用例期间新建的**非共享** `RenderHost` 起的 child 仍活着 → 杀掉（只杀登记过的 PID）并抛
    AssertionError，带完整命令行。共享实例不在此判（见 `reap_shared`）；先于用例建好的宿主归它的 fixture。"""
    shared = _shared_ids()
    mine = [c for c in _CHILDREN if c[1] > since and id(c[0]()) not in shared]
    _CHILDREN[:] = [c for c in _CHILDREN if c not in mine and c[2].poll() is None]
    leaked = []
    for _ref, _seq, proc in mine:
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - TimeoutExpired：真没退
            pass
        if proc.poll() is None:
            args = proc.args if isinstance(proc.args, str) else " ".join(map(str, proc.args))
            leaked.append((proc.pid, args))
            proc.kill()
            proc.wait(timeout=10)
    assert not leaked, (
        "本用例起了 render child 却没有关（自己建的 RenderHost 要 `close()`；"
        "进程级共享实例按模块由 conftest 的 `_render_shared_host_reaped_per_module` 代关，不在此列）：\n"
        + "\n".join(f"  pid={pid} {cmd}" for pid, cmd in leaked)
    )
