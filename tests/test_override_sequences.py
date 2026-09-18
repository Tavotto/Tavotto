"""引擎 override 的**操作序列**不变式（审计任务书 §4.2 后端那一半，2026-09-18）。

`test_invariants_engine.py` 的不变式 3 钉的是几条**手写**的含删除序列（别名 / 广播 ↔ 窄
那几组重叠），`test_equivalence_matrix.py` 钉的是**累加**的 patch 组。两者都是人挑出来的
形状；这里把它们推广成**带种子的随机序列**：在一张图宣称的可编辑字段里随机加 / 改 / 删，
每一步都是一份全量 override 列表（与前端发来的形状相同），每一步之后量：

    HOT(P_k)  ==  CLEAR + REPLAY(P_k)          （热态 vs 另一条常驻 worker 上的清空重放）

序列结束再起一条**全新** worker 只见最终那份 P_n，量：

    HOT(P_n)  ==  FRESH(P_n)                   （manifest 逐字相等 + 像素逐字节相等）

失败时**最小化**：按步骤做 delta-debugging（丢掉一段仍红就丢），报最短的复现序列；
`FIXED` 里钉的是最小化之后的固定回归——随机只负责发现，回归靠固定用例。

与前端 `documentStore.sequences.test.ts` 同一套路数（200 × 60 是纯内存；这里每一步要过
worker 子进程，所以 8 条 × 10 步 × 两张图，本机约 2 分钟）。判据复用不变式 3 那把最严的尺
（整份 manifest + 像素），采样器与不变式 1 / 2 共用一份（`tests/support/overridesample.py`）。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

from __future__ import annotations

import hashlib
import json
import os
import random

import pytest

from support.invariant_figures import ENTRY, LIBRARY, SCRIPT_NAME
from support.overridesample import _editable_targets, _patch_for, _sample_value
from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

#: 随机序列的规模：种子固定，`TAVOTTO_SEQ_SEEDS` 可以临时放大（本地找 bug 用，CI 不动）。
SEEDS = tuple(range(int(os.environ.get("TAVOTTO_SEQ_SEEDS", "8"))))
STEPS = 10
STEMS = ("InvMix", "InvCont")


def _p(gid: str, prop: str, value) -> dict:
    return {"gid": gid, "prop": prop, "value": value}


_T0, _T1 = "axes_0.legend.texts_0", "axes_0.legend.texts_1"

#: **已知分岔**：随机发现、最小化到两步之后钉在这里，每条挂一个 issue，`xfail(strict=True)`——
#: 修好那天它会 xpass 变红，提醒把它挪进 `FIXED`。2026-09-18 首跑 8 × 10 × 2 抓到三族：
#:   * #412 文字 bbox 组：撤掉 / 关掉 `bbox_visible` 而其它 bbox_* 仍在，热态藏框、重放露框；
#:   * #413 `preview_png` 不是状态中立：预览那次 draw 的几何进了藏起来的图例文字的 bbox；
#:   * #414 显式 `binding=custom` 冻结的样子只活在会话里，源随后变了重放对不上。
KNOWN: list[tuple[str, str, str, list[list[dict]]]] = [
    (
        "412-bbox-visible-removed",
        "#412",
        "InvMix",
        [
            [_p(_T1, "bbox_visible", True), _p(_T1, "bbox_edgecolor", "#ff00ff")],
            [_p(_T1, "bbox_edgecolor", "#ff00ff")],
        ],
    ),
    (
        "412-bbox-visible-toggled-off",
        "#412",
        "InvCont",
        [
            [_p(_T1, "bbox_visible", True), _p(_T1, "bbox_linewidth", 2.0)],
            [_p(_T1, "bbox_visible", False), _p(_T1, "bbox_linewidth", 2.0)],
        ],
    ),
    (
        "413-preview-png-then-hide-legend",
        "#413",
        "InvMix",
        # 第 0 步是空列表：分岔来自逐步比对时那一次 preview_png（状态中立的承诺）本身
        [[], [_p("axes_0.legend", "visible", False)]],
    ),
    (
        "414-custom-binding-then-source-changes",
        "#414",
        "InvCont",
        [
            [_p("axes_0.lines_1", "marker", "None"), _p(_T0, "binding", "custom")],
            [_p("axes_0.lines_1", "marker", "o"), _p(_T0, "binding", "custom")],
        ],
    ),
]

#: 随机序列里落在上面三族上的 (stem, seed)：同样 xfail(strict)，修好一族就会有 seed 变红提醒摘掉。
KNOWN_SEEDS: dict[tuple[str, int], str] = {
    ("InvMix", 2): "#413",
    ("InvMix", 5): "#412",
    ("InvMix", 6): "#412",
    ("InvCont", 0): "#414",
    ("InvCont", 5): "#412",
    ("InvCont", 6): "#412",
}

#: **固定回归**：`KNOWN` 里修好之后挪过来的序列——随机负责发现、这里负责不再回来。
FIXED: list[tuple[str, str, list[list[dict]]]] = []


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("sequence-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(library):
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    w.ensure_built()
    return w


@pytest.fixture
def hot(library):
    """热路：整条序列**连续**走在它上面，中途不清——清了就不是热态了。

    **每条序列一条新 worker**（function scope）。共用一条时前一条序列留下的漂移会成为
    下一条的起点：第一版这么写，16 条里 7 条红，其中两条（InvCont seed 4 / 7）把序列单独
    重跑一遍却全绿——量到的是「上一条序列的残留」，不是这条序列自己。跨序列的残留是
    另一个问题（长会话累积漂移），要量它得专门设计，不该混进这里。
    """
    w = _worker(library)
    yield w
    pool.discard(w)


@pytest.fixture
def replay(library):
    """清空重放那条腿：每一步先清空再一次性应用同一份全量列表。同样每条序列一条新的。"""
    w = _worker(library)
    yield w
    pool.discard(w)


def _png(worker, stem, patches, tag) -> str:
    path = worker.preview_png(stem, list(patches), 380, tag)
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _apply(worker, stem, patches=()) -> dict:
    resp = worker.override(stem, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


# ---------------------------------------------------------------------------
# 序列生成：在宣称的可编辑字段里随机加 / 改 / 删
# ---------------------------------------------------------------------------
def _candidates(base: dict) -> tuple[list[list[dict]], dict[tuple[str, str], dict]]:
    """每个候选 = 一组 patch（使能项 + 目标 prop），与不变式 1 / 2 发的是同一批；
    另回一张 (gid, prop) → 字段元数据的表，「改值」那步按字段类型重新采样时用。"""
    advertised = {el["gid"]: {f["prop"] for f in el["editable"]} for el in base["elements"]}
    fields = {(el["gid"], f["prop"]): f for el in base["elements"] for f in el["editable"]}
    out = []
    for gid, field in _editable_targets(base):
        _enablers, full = _patch_for(gid, field, advertised)
        if full:
            out.append(full)
    return out, fields


def _key(p: dict) -> tuple[str, str]:
    return (p["gid"], p["prop"])


def _sequence(
    rng: random.Random,
    candidates: list[list[dict]],
    fields: dict[tuple[str, str], dict],
    steps: int,
) -> list[list[dict]]:
    """一串全量 override 列表。三种动作按权重抽：加一组（含使能项）/ 删掉正在生效的一条 /
    改掉正在生效的一条的值——**按字段类型重新采样**（枚举换另一个合法选项、数值挪一档、
    颜色换一个、布尔取反），不是拿字符串瞎改：`'best' + 'y'` 那种值会被 setter 当场拒掉，
    量到的就成了「非法值被拒」而不是热态 / 重放。采不出不同值的（结构化类型）改成删。"""
    current: dict[tuple[str, str], dict] = {}
    out: list[list[dict]] = []
    for _ in range(steps):
        action = rng.choices(("add", "remove", "change"), weights=(5, 3, 2))[0]
        if action == "add" or not current:
            group = rng.choice(candidates)
            for p in group:
                current[_key(p)] = dict(p)
        elif action == "remove":
            current.pop(rng.choice(list(current)), None)
        else:
            k = rng.choice(list(current))
            p = current[k]
            nv = _sample_value({**fields[k], "value": p["value"]})
            if nv is None or nv == p["value"]:
                current.pop(k, None)
            else:
                p["value"] = nv
        out.append([dict(p) for p in current.values()])
    return out


# ---------------------------------------------------------------------------
# 判据与最小化
# ---------------------------------------------------------------------------
def _diverges_hot_vs_replay(hot, replay, stem, steps: list[list[dict]], tag: str) -> int | None:
    """热路连续走一遍：第 k 步之后热态 ≠ 清空重放（manifest **或**像素）就返回 k，否则 None。

    **不清热路**——热态的定义就是「带着前面每一步的历史」；清空重放在另一条 worker 上做。
    像素也逐步比：第一版只比 manifest，InvCont seed 0 十步走完 manifest 一路相等、最后像素
    却不同——几何尺量不到的漂移（颜色 / dash / 图例示意线）只有像素量得到，而且要在它
    出现的那一步抓住，不是走完再猜。返回时两条会话都留在最后一步的状态。
    """
    for k, step in enumerate(steps):
        hot_man = _apply(hot, stem, step)
        _apply(replay, stem, [])
        replay_man = _apply(replay, stem, step)
        if hot_man != replay_man:
            return k
        if _png(hot, stem, step, f"{tag}-hot-{k}") != _png(replay, stem, step, f"{tag}-replay-{k}"):
            return k
    return None


def _minimize(library, stem, steps: list[list[dict]], tag: str) -> list[list[dict]]:
    """delta-debugging：能丢掉一段仍然分岔就丢，直到丢任何一段都不再分岔。

    每次试跑都起**两条新 worker**——不能靠 `override([])` 把热会话清回原样再试：被量的
    正是「清不干净」这类漂移，拿它当复位手段等于用被测物校准尺子。
    """

    def diverges(trial: list[list[dict]]) -> bool:
        h, r = _worker(library), _worker(library)
        try:
            return _diverges_hot_vs_replay(h, r, stem, trial, f"{tag}-min") is not None
        finally:
            pool.discard(h)
            pool.discard(r)

    cur = list(steps)
    chunk = max(1, len(cur) // 2)
    while chunk >= 1 and len(cur) > 1:
        shrunk = False
        i = 0
        while i < len(cur):
            trial = cur[:i] + cur[i + chunk :]
            if trial and diverges(trial):
                cur = trial
                shrunk = True
            else:
                i += chunk
        if not shrunk:
            chunk //= 2
    return cur


def _describe(steps: list[list[dict]]) -> str:
    return "\n".join(
        f"  step {k}: {json.dumps(s, ensure_ascii=False)}" for k, s in enumerate(steps)
    )


def _check_final_against_fresh(hot, library, stem, final: list[dict], tag: str) -> None:
    """热路此刻停在 `final`（序列的最后一步，带着全部历史）；起一条只见过 `final` 的全新 worker 比。"""
    hot_man = _apply(hot, stem, final)  # 同一份列表再发一次：全量语义下是 no-op，只为取 manifest
    hot_png = _png(hot, stem, final, f"seq-hot-{tag}")
    fresh = _worker(library)
    try:
        fresh_man = _apply(fresh, stem, final)
        fresh_png = _png(fresh, stem, final, f"seq-fresh-{tag}")
    finally:
        pool.discard(fresh)
    _apply(hot, stem, [])
    assert hot_man == fresh_man, (
        f"{tag}：序列走完之后，热态与全新 worker 的 manifest 不一致——"
        f"用户「写回时的样子」与「重开后的样子」会不同\n{_describe([final])}"
    )
    assert hot_png == fresh_png, f"{tag}：manifest 一样但**画出来**不一样\n{_describe([final])}"


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("stem", STEMS)
def test_the_candidate_pool_is_wide(hot, stem):
    """前提：随机序列真的有东西可抽——候选组不少于 20，且跨越不止一种角色。"""
    base = _apply(hot, stem)
    cands, _fields = _candidates(base)
    roles = {
        next(el["role"] for el in base["elements"] if el["gid"] == g[-1]["gid"]) for g in cands
    }
    assert len(cands) >= 20, f"{stem} 只有 {len(cands)} 组候选——序列量在太窄的集合上"
    assert len(roles) >= 3, f"{stem} 的候选只覆盖 {roles}"


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("stem", STEMS)
def test_random_sequences_keep_hot_equal_to_replay(hot, replay, library, stem, seed, request):
    """随机加 / 改 / 删之后每一步 HOT == CLEAR+REPLAY；序列结束 HOT == FRESH（manifest + 像素）。

    分岔时先最小化再报：报出来的是最短的复现序列，直接可以抄进 `KNOWN`（开 issue）或 `FIXED`。
    """
    known = KNOWN_SEEDS.get((stem, seed))
    if known:
        request.applymarker(
            pytest.mark.xfail(strict=True, reason=f"已知分岔 {known}（修好会 xpass）")
        )
    base = _apply(hot, stem)
    cands, fields = _candidates(base)
    steps = _sequence(random.Random(f"{stem}:{seed}"), cands, fields, STEPS)
    k = _diverges_hot_vs_replay(hot, replay, stem, steps, f"{stem}-s{seed}")
    if k is not None:
        minimal = _minimize(library, stem, steps[: k + 1], f"{stem}-s{seed}")
        pytest.fail(
            f"{stem} seed={seed}：第 {k} 步之后热态 ≠ 清空重放。最小化后的复现序列"
            f"（{len(minimal)} 步，抄进 FIXED）：\n{_describe(minimal)}"
        )
    _check_final_against_fresh(hot, library, stem, steps[-1], f"{stem}-s{seed}")


@pytest.mark.parametrize("case_id,issue,stem,steps", KNOWN, ids=[c[0] for c in KNOWN])
def test_known_divergences_still_diverge(
    hot, replay, library, case_id, issue, stem, steps, request
):
    """最小化后的复现（每条挂一个 issue）：今天必须红（xfail strict），修好那天 xpass → 挪进 FIXED。"""
    request.applymarker(pytest.mark.xfail(strict=True, reason=f"已知分岔 {issue}（修好会 xpass）"))
    k = _diverges_hot_vs_replay(hot, replay, stem, steps, case_id)
    assert k is None, f"{case_id}（{issue}）：第 {k} 步之后热态 ≠ 清空重放\n{_describe(steps)}"
    _check_final_against_fresh(hot, library, stem, steps[-1], case_id)


@pytest.mark.parametrize("case_id,stem,steps", FIXED, ids=[c[0] for c in FIXED])
def test_fixed_regressions(hot, replay, library, case_id, stem, steps):
    k = _diverges_hot_vs_replay(hot, replay, stem, steps, case_id)
    assert k is None, f"{case_id}：第 {k} 步之后热态 ≠ 清空重放\n{_describe(steps)}"
    _check_final_against_fresh(hot, library, stem, steps[-1], case_id)
