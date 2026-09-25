"""带**历史**与**几何**的随机操作序列（QA 规范 §7 RAND-01，2026-09-24）。

`test_override_sequences.py` 的随机序列只在「宣称的可编辑字段」里加 / 改 / 删样式：候选池
刻意跳过 figure / axes / ticks 角色，pair / rect 类型也采不出值——于是**拖动**（`pos_frac` /
`loc_frac`）、**挪子图**（`axes.position`）、**改图幅**（`size_mm`）一条都不会出现（QA 实测
32 种子 × 2 图 × 10 步，几何类 prop 出现 0 次），撤销 / 重做也只以「删一条」的形状出现。
而数据损坏级的那一族（FigS3：先拖文字再挪子图 / 改图幅，热态漂走、重放落回声明处）恰好住在
这几条 prop 的交界上。

这里在同一个图库、同一把尺（那边的 HOT == CLEAR+REPLAY，manifest + 像素）之上换一个生成器：
一个**测试侧独立的参考模型**维护前端那种撤销栈——

    提交（样式 / 拖动 / 挪子图 / 改图幅）→ 新的全量列表入栈、截掉 redo
    undo / redo                           → 指针挪回栈里**已经出现过**的那份列表

每一步之后量三条不变式：

1. **HOT(P_k) == CLEAR + REPLAY(P_k)**（manifest 逐字 + 380 px 预览像素逐字节）；
2. **拖动锚点**：每一条生效中的 `pos_frac` / `loc_frac`，manifest 里该元素的 `anchor` 等于
   声明值（容差 `ANCHOR_ATOL`，figure 分数）。期望值是测试侧从**当时** manifest 的锚点加固定
   种子的偏移算出来的，不经产品的坐标换算；
3. **历史回访**：undo / redo 回到之前出现过的列表时，HOT 的 manifest 与像素和第一次逐字相同。

序列结束再与只见过最终列表的全新 worker 比（HOT == FRESH）。HOT ≠ REPLAY 时按列表序列做
delta-debugging 最小化（复用那边的 `_minimize`），失败信息带种子、动作日志与第一条不成立的不变式。

**生成器自己的强度也钉住**（规范 RAND-02「不能用大量空白点击伪造覆盖」）：每种动作都出现、
列表真的变化的步 ≥ 80%、undo / redo 确实回访了已有状态。

规模：`TAVOTTO_HIST_SEEDS`（默认 3）× 12 步 × 两张图，本机约 1 分钟。本进程不 import matplotlib。
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field

import pytest

from support.invariant_figures import ENTRY, LIBRARY, SCRIPT_NAME
from tavotto.engine import pool
from test_override_sequences import (
    WORKER_PY,
    _apply,
    _candidates,
    _check_final_against_fresh,
    _describe,
    _diff_paths,
    _minimize,
    _png,
)

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SEEDS = tuple(range(int(os.environ.get("TAVOTTO_HIST_SEEDS", "3"))))
STEPS = 12
STEMS = ("InvMix", "InvCont")
#: figure 分数。拖动值按 round4 存（1e-4）；manifest 的锚点是 draw 之后量出来的真实位置。
#: 校准（QA 2026-09-24，`docs/qa/2026-09-24/long/repro/rand_history_calibration.py`，两图 × 16 种子
#: × 12 步、400 次锚点比对，含挪子图 / 改图幅生效之后）：最大残差 2.8e-16——setter 把分数换进本地
#: 坐标、manifest 再换回来，只剩浮点误差。0.002 在 105 mm 宽的图上约 0.2 mm；FigS3 那类漂移是
#: 「跟着子图 / 图幅走」，量级是子图位移本身（±0.03 起）与图幅比例（0.85–1.2 倍），远大于它。
ANCHOR_ATOL = 0.002
#: 动作权重：四种提交 + 两种历史。历史动作**只在可用时参与抽签**（栈底没有 undo、redo 段空
#: 时没有 redo）：不可用时退化成别的动作会让序列在栈底空转，也会让 redo 几乎抽不到——
#: redo 只在 undo 之后、下一次提交之前可用，按固定权重抽，12 步里常常一次都碰不上。
OPS = (("style", 3), ("drag", 4), ("move_axes", 2), ("resize", 2), ("undo", 3), ("redo", 5))
_FRAC = ("pos_frac", "loc_frac")


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("history-sequence-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(library):
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    w.ensure_built()
    return w


@pytest.fixture
def hot(library):
    """整条序列连续走在它上面（每条序列一条新的，理由见 test_override_sequences.hot）。"""
    w = _worker(library)
    yield w
    pool.discard(w)


@pytest.fixture
def replay(library):
    w = _worker(library)
    yield w
    pool.discard(w)


# ---------------------------------------------------------------------------
# 参考模型：全量列表 + 撤销栈
# ---------------------------------------------------------------------------
def _field(el: dict, prop: str) -> dict | None:
    return next((f for f in el.get("editable", []) if f["prop"] == prop), None)


def _set(cur: list[dict], patches: list[dict]) -> list[dict]:
    """把 patches 写进全量列表：同 (gid, prop) 原位替换、新的追加——前端 commit 的形状。"""
    out = [dict(p) for p in cur]
    for p in patches:
        hit = next((q for q in out if (q["gid"], q["prop"]) == (p["gid"], p["prop"])), None)
        if hit is not None:
            hit["value"] = p["value"]
        else:
            out.append(dict(p))
    return out


@dataclass
class History:
    """撤销栈：`states[ptr]` 是此刻的全量列表；提交截掉 ptr 之后的 redo 段。"""

    states: list[list[dict]] = field(default_factory=lambda: [[]])
    ptr: int = 0

    @property
    def current(self) -> list[dict]:
        return self.states[self.ptr]

    def commit(self, new: list[dict]) -> None:
        self.states = [*self.states[: self.ptr + 1], new]
        self.ptr += 1


class Generator:
    """按种子抽动作。拖动的目标 = 那个元素**此刻**的锚点 + 偏移，所以 `step()` 要当前列表下
    的 manifest——主用例给热 worker 的真实 manifest，强度用例给基线。"""

    def __init__(self, rng: random.Random, base: dict):
        self.rng = rng
        self.cands, _fields = _candidates(base)
        self.axes = next(
            (e for e in base["elements"] if e["role"] == "axes" and _field(e, "position")), None
        )
        fig = next((e for e in base["elements"] if e["gid"] == "figure"), None)
        self.size = _field(fig, "size_mm")["value"] if fig and _field(fig, "size_mm") else None

    def step(self, h: History, man_now: dict) -> str:
        rng = self.rng
        can = {"undo": h.ptr > 0, "redo": h.ptr < len(h.states) - 1}
        ops = [(k, w) for k, w in OPS if can.get(k, True)]
        kind = rng.choices([k for k, _w in ops], [w for _k, w in ops])[0]
        if kind == "undo":
            h.ptr -= 1
            return "undo"
        if kind == "redo":
            h.ptr += 1
            return "redo"
        cur = h.current
        if kind == "drag":
            els = [
                e
                for e in man_now["elements"]
                if e.get("draggable") and e.get("drag_prop") in _FRAC and e.get("anchor")
            ]
            if els:
                e = rng.choice(els)
                val = [
                    round(min(0.95, max(0.05, a + rng.uniform(-0.06, 0.06))), 4)
                    for a in e["anchor"]
                ]
                h.commit(_set(cur, [{"gid": e["gid"], "prop": e["drag_prop"], "value": val}]))
                return f"drag:{e['gid']}"
            kind = "style"
        if kind == "move_axes" and self.axes is not None:
            x0, y0, w, hh = _field(self.axes, "position")["value"]
            val = [
                round(x0 + rng.uniform(-0.03, 0.03), 4),
                round(y0 + rng.uniform(-0.03, 0.03), 4),
                round(w * rng.uniform(0.9, 1.0), 4),
                round(hh * rng.uniform(0.9, 1.0), 4),
            ]
            h.commit(_set(cur, [{"gid": self.axes["gid"], "prop": "position", "value": val}]))
            return "move_axes"
        if kind == "resize" and self.size is not None:
            s = rng.uniform(0.85, 1.2)
            val = [round(self.size[0] * s, 1), round(self.size[1] * s, 1)]
            h.commit(_set(cur, [{"gid": "figure", "prop": "size_mm", "value": val}]))
            return "resize"
        h.commit(_set(cur, rng.choice(self.cands)))
        return "style"


def _anchor_problems(man: dict, patches: list[dict]) -> list[str]:
    els = {e["gid"]: e for e in man["elements"]}
    out = []
    for p in patches:
        if p["prop"] not in _FRAC:
            continue
        a = (els.get(p["gid"]) or {}).get("anchor")
        if a is None:
            out.append(f"{p['gid']} 生效着 {p['prop']}={p['value']}，manifest 里却没有锚点")
        elif max(abs(a[0] - p["value"][0]), abs(a[1] - p["value"][1])) > ANCHOR_ATOL:
            out.append(
                f"{p['gid']} 锚点 {a} ≠ 声明的 {p['prop']} {p['value']}（容差 {ANCHOR_ATOL}）"
            )
    return out


def _k(patches: list[dict]) -> str:
    return json.dumps(patches, sort_keys=True)


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("stem", STEMS)
def test_generator_really_exercises_history_and_geometry(hot, stem):
    """生成器强度（只量序列形状，拖动锚点取基线）：跨种子每种动作都出现；列表真的变化的步
    ≥ 80%；undo / redo 回访已有状态至少两次。弱了这条先红，免得主用例绿在空转上。"""
    base = _apply(hot, stem)
    kinds, changed, total, revisits = set(), 0, 0, 0
    for seed in range(max(len(SEEDS), 3)):
        gen, h, seen = Generator(random.Random(f"hist:{stem}:{seed}"), base), History(), {_k([])}
        for _ in range(STEPS):
            prev = h.current
            kind = gen.step(h, base)
            kinds.add(kind.split(":")[0])
            total += 1
            changed += h.current != prev
            revisits += kind in ("undo", "redo") and _k(h.current) in seen
            seen.add(_k(h.current))
    assert {"style", "drag", "move_axes", "resize", "undo", "redo"} <= kinds, kinds
    assert changed / total >= 0.8, f"只有 {changed}/{total} 步改变了列表——序列在空转"
    assert revisits >= 2, f"undo / redo 回访已有状态只有 {revisits} 次"


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("stem", STEMS)
def test_history_and_geometry_sequences(hot, replay, library, stem, seed):
    """每一步：HOT == CLEAR+REPLAY、拖动锚点守恒、回访状态逐字相同；结束 HOT == FRESH。"""
    base = _apply(hot, stem)
    tag = f"hist-{stem}-s{seed}"
    gen, h = Generator(random.Random(f"hist:{stem}:{seed}"), base), History()
    first_seen = {_k([]): (base, _png(hot, stem, [], f"{tag}-base"))}
    man_now = base
    seq: list[list[dict]] = []
    log: list[str] = []
    for k in range(STEPS):
        kind = gen.step(h, man_now)
        one = [dict(p) for p in h.current]
        seq.append(one)
        log.append(kind)
        hot_man = _apply(hot, stem, one)
        _apply(replay, stem, [])
        replay_man = _apply(replay, stem, one)
        hot_png = _png(hot, stem, one, f"{tag}-hot-{k}")
        replay_png = _png(replay, stem, one, f"{tag}-replay-{k}")
        where = f"{tag} 第 {k} 步（{kind}）；动作 {log}\n{_describe(seq)}"
        paths = _diff_paths(hot_man, replay_man)
        if paths or hot_png != replay_png:
            minimal = _minimize(library, stem, seq, tag)
            pytest.fail(
                f"{where}\n不变式 1：热态 ≠ 清空重放（manifest 差 {sorted(paths)[:8]}，"
                f"像素{'不同' if hot_png != replay_png else '相同'}）。最小化后：\n{_describe(minimal)}"
            )
        bad = _anchor_problems(hot_man, one)
        assert not bad, f"{where}\n不变式 2：拖动锚点不守恒：{bad}"
        if _k(one) in first_seen:
            m0, p0 = first_seen[_k(one)]
            diff = sorted(_diff_paths(m0, hot_man))
            assert not diff, f"{where}\n不变式 3：回到已出现过的状态，manifest 差 {diff[:8]}"
            assert p0 == hot_png, f"{where}\n不变式 3：回到已出现过的状态，像素不同"
        else:
            first_seen[_k(one)] = (hot_man, hot_png)
        man_now = hot_man
    _check_final_against_fresh(hot, library, stem, seq[-1], tag)
