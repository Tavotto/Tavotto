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

**已知分岔不用 xfail 接。** 每族登记一份**形状**（`FAMILIES`：manifest 差在哪些路径、像素
是否不同），已知用例 / 已知种子命中时把实测形状与登记的逐项比对：形状不符是普通红（新问题，
不许被旧 issue 认领）；不再分岔也是红（修好了，提醒挪进 `FIXED` / 摘掉种子）。第一版用
`xfail(strict=True)` 套整条用例：strict 只防「意外通过」，不防「失败原因不一样」——worker
异常、warnings 断言、别的字段漂移全会被算成「已知分岔」（v0.15.0 验收 V015-TEST-01）。

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
import re
from dataclasses import dataclass

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


@dataclass(frozen=True)
class Divergence:
    """一次实测到的热态 ≠ 重放：第几步、manifest 差在哪些路径（已排序）、像素是否不同。"""

    step: int
    manifest: tuple[str, ...]
    pixel: bool


@dataclass(frozen=True)
class Shape:
    """一族已知分岔的形状：manifest 差异**只**落在这些路径模式上（每条模式至少命中一次；
    空元组 = manifest 逐字相等），像素是否不同。模式里的 `*` 匹配 gid / prop 里的一段
    （不跨 `.` 与方括号），同一族在不同序列里落在不同的图例项上。"""

    manifest: tuple[str, ...]
    pixel: bool


#: 已知分岔各族的形状——2026-09-19 在 `KNOWN` 四条 + `KNOWN_SEEDS` 六条上实测，两两可分：
#: #412 只差那一条 `bbox_visible` 取值且像素不同；#414 manifest 逐字相等只有像素不同
#: （#413 曾是「只差图例文字的 `bbox` 几何且像素相同」，修好后从这里摘掉，复现挪进 `FIXED`）。
#: 已知用例 / 已知种子命中时按这张表比对，形状不符就是新问题（普通红），不许被旧 issue 认领。
FAMILIES: dict[str, Shape] = {
    "#412": Shape(("elements[axes_0.legend.texts_*].editable[bbox_visible].value",), pixel=True),
    "#414": Shape((), pixel=True),
}

#: **已知分岔**：随机发现、最小化到两步之后钉在这里，每条挂一个 issue。用例断言它**今天仍按
#: 登记的形状在最后一步分岔**：不再分岔 = 修好了，红着提醒挪进 `FIXED`；形状变了 = 另一个
#: 问题。2026-09-18 首跑 8 × 10 × 2 抓到三族（#413 已修，见 `FIXED`）：
#:   * #412 文字 bbox 组：撤掉 / 关掉 `bbox_visible` 而其它 bbox_* 仍在，热态藏框、重放露框；
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
        "414-custom-binding-then-source-changes",
        "#414",
        "InvCont",
        [
            [_p("axes_0.lines_1", "marker", "None"), _p(_T0, "binding", "custom")],
            [_p("axes_0.lines_1", "marker", "o"), _p(_T0, "binding", "custom")],
        ],
    ),
]

#: 随机序列里落在上面三族上的 (stem, seed) → (issue, 首次分岔的步)。序列由种子决定，步数也
#: 就是定的；用例断言那一步按该族的形状分岔——早一步 / 晚一步 / 别的形状都是普通红。修好一族
#: 就会有 seed 因「不再分岔」变红，提醒摘掉。已知分岔之后的步与 HOT == FRESH **不量**（热态
#: 带着已知漂移，量到的分不清是新问题还是它的后果），如实记进 junit 的 `unmeasured` 属性。
KNOWN_SEEDS: dict[tuple[str, int], tuple[str, int]] = {
    ("InvMix", 5): ("#412", 2),
    ("InvMix", 6): ("#412", 9),
    ("InvCont", 0): ("#414", 6),
    ("InvCont", 5): ("#412", 4),
    ("InvCont", 6): ("#412", 3),
}

#: **固定回归**：`KNOWN` 里修好之后挪过来的序列——随机负责发现、这里负责不再回来。
FIXED: list[tuple[str, str, list[list[dict]]]] = [
    (
        # #413：第 0 步是空列表——分岔来自逐步比对时那一次 preview_png 本身：预览在 380 px
        # 的 dpi 上画过一回，随后藏起来的图例不再 draw，manifest 读到的六个文字 bbox 是预览
        # dpi 的像素。修法在 manifest：draw 跳过的图例按文档 dpi 补排一次版再量。
        "413-preview-png-then-hide-legend",
        "InvMix",
        [[], [_p("axes_0.legend", "visible", False)]],
    ),
]


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
def _list_key(a: list, b: list) -> str | None:
    """两个列表都是带 `gid`（元素表）/ `prop`（editable 表）的字典时按那个键对齐，不按下标。"""
    xs = [*a, *b]
    if xs and all(isinstance(x, dict) for x in xs):
        for key in ("gid", "prop"):
            if all(key in x for x in xs):
                return key
    return None


def _diff_paths(a, b, path: str = "") -> set[str]:
    """两份 manifest 差在哪些路径：`elements[<gid>].editable[<prop>].value` 这种形状。
    标量列表（`bbox` 四个数、`size_mm`）当一个叶子——差异形状登记的是「哪个字段」，
    不是「第几个数」。"""
    if isinstance(a, dict) and isinstance(b, dict):
        out: set[str] = set()
        for k in a.keys() | b.keys():
            p = f"{path}.{k}" if path else str(k)
            out |= _diff_paths(a[k], b[k], p) if k in a and k in b else {p}
        return out
    if isinstance(a, list) and isinstance(b, list) and (key := _list_key(a, b)):
        da, db = {x[key]: x for x in a}, {x[key]: x for x in b}
        out = set()
        for k in da.keys() | db.keys():
            p = f"{path}[{k}]"
            out |= _diff_paths(da[k], db[k], p) if k in da and k in db else {p}
        return out
    return set() if a == b else {path}


def _path_matches(pattern: str, path: str) -> bool:
    """`Shape.manifest` 里的模式：字面匹配，`*` 匹配一段不含 `.` / 方括号的字符。
    不用 fnmatch——路径里的方括号会被它当成字符类。"""
    rx = re.escape(pattern).replace(r"\*", r"[^.\[\]]+")
    return re.fullmatch(rx, path) is not None


def _diverges_hot_vs_replay(
    hot, replay, stem, steps: list[list[dict]], tag: str
) -> Divergence | None:
    """热路连续走一遍：第 k 步之后热态 ≠ 清空重放（manifest **或**像素）就返回那一步的
    `Divergence`（步数 + 差异形状），否则 None。

    **不清热路**——热态的定义就是「带着前面每一步的历史」；清空重放在另一条 worker 上做。
    像素也逐步比：第一版只比 manifest，InvCont seed 0 十步走完 manifest 一路相等、最后像素
    却不同——几何尺量不到的漂移（颜色 / dash / 图例示意线）只有像素量得到，而且要在它
    出现的那一步抓住，不是走完再猜。manifest 已经不同时像素照样比：形状要两个维度都在
    （#412 与 #413 都差 manifest，分开它们的是像素同不同）。返回时两条会话都留在分岔那一步。
    """
    for k, step in enumerate(steps):
        hot_man = _apply(hot, stem, step)
        _apply(replay, stem, [])
        replay_man = _apply(replay, stem, step)
        paths = _diff_paths(hot_man, replay_man)
        pixel = _png(hot, stem, step, f"{tag}-hot-{k}") != _png(
            replay, stem, step, f"{tag}-replay-{k}"
        )
        if paths or pixel:
            return Divergence(k, tuple(sorted(paths)), pixel)
    return None


def _assert_known_shape(seen: Divergence, issue: str, step: int, tag: str) -> None:
    """实测的分岔必须与登记的 issue **逐项**对得上：步数、manifest 差异路径（只落在该族的
    模式里且每条模式都命中）、像素同不同。差一项就是另一个问题，不许挂在旧 issue 名下。"""
    shape = FAMILIES[issue]
    problems = []
    if seen.step != step:
        problems.append(f"分岔在第 {seen.step} 步，登记的是第 {step} 步")
    stray = [p for p in seen.manifest if not any(_path_matches(m, p) for m in shape.manifest)]
    if stray:
        problems.append(f"manifest 差异落在 {issue} 之外：{stray}")
    unhit = [m for m in shape.manifest if not any(_path_matches(m, p) for p in seen.manifest)]
    if unhit:
        problems.append(f"{issue} 该差的路径这次没差：{unhit}")
    if seen.pixel != shape.pixel:
        problems.append(
            f"像素{'不同' if seen.pixel else '相同'}，{issue} 登记的是{'不同' if shape.pixel else '相同'}"
        )
    assert not problems, (
        f"{tag}：分岔与登记的 {issue} 形状不符——这不是同一个问题，别让旧 issue 接住它\n  "
        + "\n  ".join(problems)
    )


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
def test_shape_matcher_semantics():
    """形状比对的两把尺子自己先钉住（纯函数，不起 worker）：
    `*` 只匹配一段——`texts_*` 认 `texts_1`，`axes_0.*` 不认 `axes_0.legend.texts_1`；
    差异路径按 gid / prop 对齐而不按下标，标量列表（bbox）是一个叶子，单侧缺的元素报整条。
    夹具按真实 manifest 的形状造：editable 条目只有 prop / value，没有 gid。"""

    def _f(prop, value):
        return {"prop": prop, "type": "number", "value": value}

    assert _path_matches(
        "elements[axes_0.legend.texts_*].bbox", "elements[axes_0.legend.texts_1].bbox"
    )
    assert not _path_matches("elements[axes_0.*].bbox", "elements[axes_0.legend.texts_1].bbox")
    assert not _path_matches(
        "elements[axes_0.legend.texts_*].bbox",
        "elements[axes_0.legend.texts_1].editable[bbox_visible].value",
    )
    a = {
        "elements": [
            {"gid": "g", "bbox": [0, 0, 1, 1], "editable": [_f("a", 1), _f("b", 2)]},
            {"gid": "g2", "bbox": [0, 0, 1, 1], "editable": []},
        ]
    }
    b = {
        "elements": [
            {"gid": "g", "bbox": [0, 0, 1, 2], "editable": [_f("b", 3), _f("a", 1)]},
        ]
    }
    assert _diff_paths(a, b) == {
        "elements[g].bbox",
        "elements[g].editable[b].value",
        "elements[g2]",
    }
    assert _diff_paths(a, a) == set()


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
def test_random_sequences_keep_hot_equal_to_replay(
    hot, replay, library, stem, seed, record_testsuite_property
):
    """随机加 / 改 / 删之后每一步 HOT == CLEAR+REPLAY；序列结束 HOT == FRESH（manifest + 像素）。

    分岔时先最小化再报：报出来的是最短的复现序列，直接可以抄进 `KNOWN`（开 issue）或 `FIXED`。
    `KNOWN_SEEDS` 里的种子：断言它**仍在登记的那一步按登记的形状**分岔，之后不再往下量。
    """
    known = KNOWN_SEEDS.get((stem, seed))
    base = _apply(hot, stem)
    cands, fields = _candidates(base)
    steps = _sequence(random.Random(f"{stem}:{seed}"), cands, fields, STEPS)
    tag = f"{stem}-s{seed}"
    seen = _diverges_hot_vs_replay(hot, replay, stem, steps, tag)
    if known:
        issue, step = known
        assert seen is not None, (
            f"{tag}：登记为 {issue} 的分岔不见了——修好了就把 ({stem!r}, {seed}) 从 KNOWN_SEEDS 摘掉"
        )
        _assert_known_shape(seen, issue, step, tag)
        # 已知种子的最小复现钉在 KNOWN 里（那才是该修的样本），这里不再最小化（每次试跑起
        # 一条新 worker，13–54 s）。分岔之后的步与 HOT == FRESH 没量，写进 junit 别装量过
        # （testsuite 级属性：testcase 级的 record_property 在 xunit2 下要吵一条 warning）。
        later = f"steps {seen.step + 1}..{len(steps) - 1} + " if seen.step < len(steps) - 1 else ""
        record_testsuite_property("unmeasured", f"{tag}: {later}final-vs-fresh ({issue})")
        return
    if seen is not None:
        minimal = _minimize(library, stem, steps[: seen.step + 1], tag)
        pytest.fail(
            f"{tag}：第 {seen.step} 步之后热态 ≠ 清空重放（manifest 差 {list(seen.manifest)}，"
            f"像素{'不同' if seen.pixel else '相同'}）。最小化后的复现序列"
            f"（{len(minimal)} 步，抄进 FIXED）：\n{_describe(minimal)}"
        )
    _check_final_against_fresh(hot, library, stem, steps[-1], tag)


@pytest.mark.parametrize("case_id,issue,stem,steps", KNOWN, ids=[c[0] for c in KNOWN])
def test_known_divergences_still_diverge(hot, replay, case_id, issue, stem, steps):
    """最小化后的复现（每条挂一个 issue）：今天必须**在最后一步按登记的形状**分岔——最小化
    的定义就是丢掉任何一步都不再分岔，所以分岔只能在末步。修好那天它不再分岔 → 红 → 挪进
    FIXED；形状变了 → 红 → 那是另一个问题，另开 issue。"""
    seen = _diverges_hot_vs_replay(hot, replay, stem, steps, case_id)
    assert seen is not None, (
        f"{case_id}（{issue}）不再分岔——修好了就把它挪进 FIXED\n{_describe(steps)}"
    )
    _assert_known_shape(seen, issue, len(steps) - 1, case_id)


@pytest.mark.parametrize("case_id,stem,steps", FIXED, ids=[c[0] for c in FIXED])
def test_fixed_regressions(hot, replay, library, case_id, stem, steps):
    seen = _diverges_hot_vs_replay(hot, replay, stem, steps, case_id)
    assert seen is None, (
        f"{case_id}：第 {seen.step} 步之后热态 ≠ 清空重放（manifest 差 {list(seen.manifest)}，"
        f"像素{'不同' if seen.pixel else '相同'}）\n{_describe(steps)}"
    )
    _check_final_against_fresh(hot, library, stem, steps[-1], case_id)
