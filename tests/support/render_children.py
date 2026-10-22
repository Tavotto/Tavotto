"""用例结束时核对「本用例起的 render child 都已退出」（#1285）。conftest 的 autouse fixture 是薄壳，
逻辑放这里，tests/test_render_children_guard.py 才能直接对它做反证。

判据的主语：**本用例期间新建的 `RenderHost`** 起的 child——不是机器上所有 renderchild；进来之前就建好的宿主
（模块 / 会话级 fixture 持有的，首次使用才起 child）归它的 fixture，不算本用例。时刻是用例 teardown。进程级共享实例（`renderhost.shared()` 与旧代）是合法的会话级资源，但生命周期归用例：
这里替它 `close()`（下一次 `shared()` 懒重建）。自己 `RenderHost(...)` 却没 `close()` 的没有这个豁免。
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


def reap(since: int) -> None:
    """关掉 `since` 之后登记的共享宿主；仍活着的 child 杀掉（只杀登记过的 PID）并抛 AssertionError，带完整命令行。"""
    from tavotto.rendercore import renderhost

    # 进程级共享实例（含旧代）不分先后一律由本用例收：它是懒建的会话级资源，哪条用例碰了它哪条负责关
    # （先于用例建好的、或被上一条用例遗留的线程在 teardown 之后才建的也一样，否则它的 child 会漏过判据）。
    # 连绑定一起丢：只 close 的话，下条用例的 `shared()` 拿回同一个旧宿主，它的新 child 又被当成「先于该用例」。
    renderhost.shutdown_shared()
    for host in tuple(renderhost._SHARED_HOSTS):
        host.close()
    # 其余宿主（用例自己 `RenderHost(...)`）只认本用例期间新建的；先于用例建好的归它的 fixture。
    mine = [c for c in _CHILDREN if c[1] > since]
    _CHILDREN[:] = [c for c in _CHILDREN if c[1] <= since and c[2].poll() is None]
    leaked = []
    for _ref, _seq, proc in mine:
        try:
            proc.wait(timeout=5)  # 刚 close 的 child 可能还在退出中
        except Exception:  # noqa: BLE001 - TimeoutExpired：真没退
            pass
        if proc.poll() is None:
            args = proc.args if isinstance(proc.args, str) else " ".join(map(str, proc.args))
            leaked.append((proc.pid, args))
            proc.kill()
            proc.wait(timeout=10)
    assert not leaked, (
        "本用例起了 render child 却没有关（自己建的 RenderHost 要 `close()`；"
        "进程级共享实例由 conftest 的 `_render_children_reaped` 代关，不在此列）：\n"
        + "\n".join(f"  pid={pid} {cmd}" for pid, cmd in leaked)
    )
