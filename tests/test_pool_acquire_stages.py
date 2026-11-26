"""取会话的阶段观察者（#479）：段名 → 用户看得懂的阶段，回调坏了不拖垮取会话。"""

from __future__ import annotations

from tavotto.engine import pool


def test_stage_observer_sees_only_staged_segments_and_restores_outer() -> None:
    seen: list[str] = []
    outer: list[str] = []
    with pool.stage_observer(outer.append):
        with pool.stage_observer(seen.append):
            for name in (
                "pool_wait_ms",
                "resolve_python_ms",
                "fingerprint_ms",
                "spawn_ms",
                "evict_ms",
            ):
                with pool._seg(name):
                    pass
        with pool._seg("handshake_ms"):
            pass
    assert seen == [pool.STAGE_ENVIRONMENT, pool.STAGE_STARTING]
    assert outer == [pool.STAGE_STARTING]  # 内层退出后外层复原
    with pool._seg("spawn_ms"):  # 没有观察者：什么都不做
        pass


def test_a_broken_stage_callback_does_not_break_the_segment() -> None:
    def boom(_stage: str) -> None:
        raise RuntimeError("SSE 队列坏了")

    ran = []
    with pool.stage_observer(boom), pool._seg("spawn_ms"):
        ran.append(True)
    assert ran == [True]


def test_every_staged_segment_is_a_known_acquire_segment() -> None:
    assert set(pool.ACQUIRE_STAGE_OF) <= set(pool.ACQUIRE_SEGMENTS)


def test_frontend_stage_union_matches_the_pool() -> None:
    """前端 `PreparingStage` 与池的阶段值是同一个闭集：后端加一个阶段，界面要跟着认。"""
    import re
    from pathlib import Path

    api = (Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "api.ts").read_text(
        encoding="utf-8"
    )
    m = re.search(r"export type PreparingStage = ([^\n]+)", api)
    assert m, "web/src/lib/api.ts 里找不到 PreparingStage"
    assert set(re.findall(r"'(\w+)'", m.group(1))) == set(pool.ACQUIRE_STAGE_OF.values())
