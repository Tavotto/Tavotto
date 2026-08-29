"""把 `PreviewPlan` 变成 `savefig` 那一瞬的 `rasterized` 状态——**只在那一瞬**。

## 这个模块回答一个问题

> 分析器给了一份「该 rasterize 谁」的名单。怎么让它**只**改变这一次预览
> 长什么样，而用户 Figure 的真实状态一个字节都不动？

分析器（`preview_complexity`）刻意不设 `set_rasterized`：它一改，预览的表示法
就写进了常驻 Figure，而常驻 Figure 正是导出读的那一份——ADR 0022 不变量 2
（导出保真）就是这么破的。设与还原收在这里，是为了让「谁在什么时候动了
Figure」只有一个答案，且那个答案自带 `finally`。

## 三条纪律

1. **先读后写**：每个 artist 的旧值在设新值**之前**入账。`set_rasterized`
   自己抛了的话，账上记的仍是它当前的真值，还原是一次无害的重设。
2. **还原不许半途而废**：一个 artist 还原失败，其余的照样要还原。所以
   `finally` 里是「逐个 try / 收集失败」，不是一个大 try。半还原比不还原
   更坏——它让 Figure 处于一个谁都没设计过的中间态。
3. **还原失败要吵**：`RestoreFailed` 是缺陷不是运行时状况。静默吞掉它意味着
   下一次 `do_export` 会把预览用的位图化当成用户的选择写进论文里，而没有
   任何地方会报错（[[test-the-restored-moment]] 那一族）。

## 为什么两条入口共用它

桌面 worker（`figsession.render`）与浏览器 playground（`browser._render`）是
同一件事的两次实现。抄一份过去的代价不是多几行重复代码，而是**同一张图在
两个入口里预览表示法不一样**——而这类分叉只会在大图上、在用户那边发作。

纯标准库；与 `worker.py` 同一条 sys.path 纪律，平铺 import。
"""

from __future__ import annotations

import contextlib

import preview_complexity
import previewbudget

__all__ = ["RestoreFailed", "preview_rasterization", "save_preview_svg"]


class RestoreFailed(RuntimeError):
    """预览结束后没能把 `rasterized` 还原回去。**永远是缺陷，不是运行时状况。**"""


@contextlib.contextmanager
def preview_rasterization(artists):
    """在这个 `with` 里，`artists` 全部 `rasterized=True`；出去时逐个还原原值。

    `artists` 空着时这是一个货真价实的 no-op：一个 getter 都不调、一个 setter
    都不设。普通科研图每次预览都会走这一行，它必须真的不要钱。
    """
    previous: list[tuple[object, object]] = []
    try:
        for artist in artists:
            # **先入账再改**：`set_rasterized` 抛了的话账上记的仍是真值。
            previous.append((artist, artist.get_rasterized()))
            artist.set_rasterized(True)
        yield
    finally:
        failures = []
        # 逆序只是对称，`rasterized` 之间没有依赖；逐个 try 才是要点：
        # 第一个还原失败不能连累后面那些。
        for artist, old in reversed(previous):
            try:
                artist.set_rasterized(old)
            except Exception as exc:  # noqa: BLE001 - 收集而不是吞掉，下面重抛
                failures.append((artist, exc))
        if failures:
            raise RestoreFailed(
                f"{len(failures)} 个 artist 的 rasterized 没能还原——"
                f"常驻 Figure 现在带着预览专用的表示法，导出会把它写进论文里："
                + "；".join(f"{a!r}: {e!r}" for a, e in failures)
            )


def save_preview_svg(state, save):
    """出一版 hybrid 预览 SVG，返回 `(plan, svg_bytes)`。**升档策略只有这一处。**

    `save(plan) -> svg_bytes` 由调用方给：桌面写盘后 `stat().st_size`，
    playground 写内存缓冲后 `tell()`。量的是同一个东西，落点不同。

    ## 为什么要第二遍

    分析器量的是**原料**（artist 图上有多少 primitive），字节闸量的是**产物**
    ——两把尺子相互独立，这正是它的价值：估算漏了的时候，只有产物那把量得
    出来。软闸（`EDITOR_SVG_SOFT_LIMIT_BYTES`）之上仍有可 rasterize 的数据层
    留在矢量层，说明这一版的估价偏低，把它们一并收进来重画一遍。

    第二遍**最多一次**：还是超软闸就到此为止（再收也没东西可收了），交给按
    字节的硬闸兜底。代价是这类图上多付一次 `savefig`——只有真的画出了 8 MiB
    以上的矢量 SVG 才会发生，而那本来就是一张要送进浏览器 DOM 的巨图。
    """
    plan = preview_complexity.plan_for_state(state)
    svg_bytes = _save_with(plan, save)
    if previewbudget.wants_hybrid_escalation(svg_bytes):
        harder = preview_complexity.escalate_plan(plan)
        if harder is not None:
            plan = harder
            svg_bytes = _save_with(plan, save)
    return plan, svg_bytes


def _save_with(plan, save):
    with preview_rasterization(plan.rasterized_artists):
        return save(plan)
