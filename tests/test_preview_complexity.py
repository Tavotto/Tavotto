"""预览复杂度分析器：裁决、成本模型、以及「它真的什么都没改」。

ADR 0022 的 Session 02。这套用例钉四件事：

1. **裁决对**——普通图 vector、大型数据层 hybrid，#181 的 fixture 稳定进 hybrid；
2. **成本模型不是拍脑袋**——模型算出来的 primitive 数与 **SVG 后端真的写出来
   的节点数**对得上（A/B 差分，见 `test_model_matches_what_the_svg_backend_
   actually_emits`）。少了这一条，「20 000 个 cell」只是一个我们自己相信的数字；
3. **分析器只读**——`artist.get_rasterized()` 前后不变，`QuadMesh` 的 paths 一次
   都没被建过。它进 render 热路径，改了 artist 就等于把预览的表示法写进常驻
   Figure，而常驻 Figure 是导出读的那一份（不变量 2）；
4. **不认识的不许静默当成 0**——进 `unknown` 清单，但也不替它做 hybrid。

本进程不 import matplotlib（Flask 侧的依赖边界）：分析器跑在
`tests/support/preview_complexity_probe.py` 里，经 worker 解释器起一次，这里只
读它报回来的事实。
"""

import json
import subprocess

import pytest

from tavotto.engine import pool, previewbudget as pb

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

#: 探针里的 mesh 边长（`preview_complexity_probe.DEFAULT_ISSUE181_N`）。
#: 40 000 cells/格 = `MESH_CELL_BUDGET` 的两倍：够越线，又比基线那个 470 快
#: 一个数量级。**用例不该为了越线跑十几秒。**
PROBE_N = 200


@pytest.fixture(scope="module")
def probe(request):
    """整套用例共用一次探针（起一次解释器 + 建十几张图是这里唯一慢的一步）。"""
    from pathlib import Path

    script = Path(__file__).resolve().parent / "support" / "preview_complexity_probe.py"
    out = subprocess.run(
        [WORKER_PY, str(script), "--issue181-n", str(PROBE_N)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def cases(probe):
    return probe["cases"]


# --------------------------------------------------------------------------
# 1. 裁决：哪些图走 vector、哪些走 hybrid
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "case",
    ["normal_line", "small_pcolormesh", "small_scatter", "small_polycollection", "small_contour"],
)
def test_ordinary_figures_stay_vector(cases, case):
    """普通科研图**行为与今天完全一致**。

    这一半比 hybrid 那一半更要紧：分析器要是把正常图也判进 hybrid，代价是
    每张图的编辑期都变成位图，而 #181 的用户一共就那么几张大图。
    """
    plan = cases[case]
    assert plan["mode"] == pb.MODE_VECTOR, plan["detail"]
    assert plan["reason"] == pb.REASON_NORMAL
    assert plan["rasterized_artist_count"] == 0


@pytest.mark.parametrize(
    ("case", "family"),
    [
        ("large_pcolormesh", "mesh"),
        ("huge_scatter", "scatter"),
        ("large_polycollection", "poly"),
        ("large_contour", "contour"),
    ],
)
def test_large_data_layers_go_hybrid(cases, case, family):
    """四个族各自越自己那条线时都要被认出来。

    **四条判据不是同一条**：mesh 越的是 cell 数、scatter 越的是实例数，而
    `poly` 与 `contour` 越的是顶点数——等值线只有十几个 `<path>`，只看节点数
    的判据整族看不见它（实测 300×300 网格 40 层 = 32 个节点、223 451 个顶点）。
    """
    plan = cases[case]
    assert plan["mode"] == pb.MODE_HYBRID, plan["detail"]
    assert plan["reason"] == pb.REASON_COMPLEXITY_BUDGET
    assert plan["rasterized_families"] == [family]
    assert plan["vector_primitives"] < plan["estimated_primitives"]


def test_thresholds_are_the_reason_these_cases_split(cases, probe):
    """小图与大图**是同一族**，分开的只有规模——否则上面两组用例只是巧合。

    少了这一条，「small_pcolormesh 是 vector」可能是因为分析器根本没认出
    QuadMesh，而不是因为它在预算之内。
    """
    budgets = probe["budgets"]
    assert budgets["MESH_CELL_BUDGET"] == pb.MESH_CELL_BUDGET, "探针与 pytest 侧读的不是同一份常量"
    small, large = cases["small_pcolormesh"], cases["large_pcolormesh"]
    assert small["families"] == large["families"] == ["mesh"]
    assert small["estimated_primitives"] <= pb.MESH_CELL_BUDGET < large["estimated_primitives"]


def test_a_single_artist_may_not_eat_the_whole_figure_budget(cases):
    """四格各 15 129 个 cell：**逐族预算一格都不越**，合计 60 516 越图级预算。

    没有第二轮裁决的话这张图会被判成 vector，然后把 6 万个节点交给 DOM——
    而 #181 的用户环境正是「多个大 mesh 面板同时在画布上」。
    """
    plan = cases["many_medium_meshes"]
    per_artist = [c["primitive_count"] for c in plan["costs"]]
    assert max(per_artist) <= pb.MESH_CELL_BUDGET, "这张图应该逐族全部合规"
    assert sum(per_artist) > pb.TOTAL_VECTOR_PRIMITIVE_BUDGET
    assert plan["mode"] == pb.MODE_HYBRID, plan["detail"]
    assert plan["vector_primitives"] <= pb.TOTAL_VECTOR_PRIMITIVE_BUDGET


# --------------------------------------------------------------------------
# 2. issue #181 的 fixture —— 整轮的验收
# --------------------------------------------------------------------------
def test_issue_181_fixture_is_recognised_as_hybrid(cases):
    """**Session 02 的验收条件**：#181 那张图稳定地被判成 hybrid。

    而且要判对**哪几个**：三块 mesh 进名单，第四格那两条普通曲线不进——
    fixture 的第四格是判据的一部分，不是装饰（ADR 0022 §2：文字、坐标轴、
    图例、标注、普通曲线保持 vector）。
    """
    plan = cases["issue_181"]
    assert plan["mode"] == pb.MODE_HYBRID, plan["detail"]
    assert plan["reason"] == pb.REASON_COMPLEXITY_BUDGET
    assert plan["rasterized_families"] == ["mesh"]
    assert plan["rasterized_artist_count"] == 3, "三格 pcolormesh，一格都不能漏"

    picked = [c for c in plan["costs"] if c["should_rasterize"]]
    assert {c["type"] for c in picked} == {"QuadMesh"}
    assert all(c["primitive_count"] == PROBE_N**2 for c in picked)


def test_the_vector_control_panel_survives(cases):
    """第四格的普通曲线**一条都不许进 rasterize 名单**。

    `Line2D` 的 `rasterizable=False` 是**策略不是能力**：`set_rasterized` 在它
    身上当然设得进去。hybrid 的承诺是「大型数据层临时变位图，你的曲线和文字
    还是矢量」，把曲线也糊掉就等于把承诺退回成 raster 档。
    """
    plan = cases["issue_181"]
    lines = [c for c in plan["costs"] if c["family"] == "line"]
    assert len(lines) == 2, "fixture 第四格是两条曲线"
    assert not any(c["rasterizable"] or c["should_rasterize"] for c in lines)
    assert plan["vector_primitives"] > 0, "hybrid 之后矢量层不该是空的"


# --------------------------------------------------------------------------
# 3. 成本模型 vs 后端真的吐出来的东西（对拍）
# --------------------------------------------------------------------------
def test_model_matches_what_the_svg_backend_actually_emits(probe):
    """**这条是成本模型的全部凭据。**

    两侧必须独立：一侧是分析器算的 `primitive_count`，另一侧是同一张图
    `savefig(svg)` 之后**数出来的 `<path>` / `<use>` 节点**。同一张图与一张
    除了这个 artist 之外完全相同的对照图相减，剩下的就是它自己摊出来的量。

    这条用例第一次跑就抓到两处（都是「我以为」而不是「我量过」）：网格每个
    cell 是 **5** 个坐标对不是 4；空的 contour 层**照样**写出一个 `<path>` 节点。
    两处都是模型偏低——而偏低是错的那个方向。

    差分本身也踩过一次：第一版没关坐标轴，scatter 那格量出来的 `<use>` 差是
    476 而不是 500——**刻度文字在 SVG 里也是 `<use>`**，两侧的刻度不同就把差分
    污染了。A/B 只有在「除了这一个 artist 之外完全相同」时才是对照。
    """
    for row in probe["crosscheck"]:
        model = row["model_primitives"]
        path, use = row["svg_delta_path"], row["svg_delta_use"]
        if use:
            # 几何进了 `<defs>`：每个实例一个 `<use>`，几何本身只写一遍。
            # 这同时验了 `_shares_geometry`——它要是判反了，这里会是
            # use=0 而 path=model。
            assert use == model, f"{row['case']}: 模型 {model} 个实例，SVG 里 {use} 个 <use>"
            assert path == 1, f"{row['case']}: 共享几何时 defs 里应当只有一条 path，实得 {path}"
        else:
            assert path == model, f"{row['case']}: 模型 {model} 个节点，SVG 里 {path} 个 <path>"


#: 顶点估值允许偏离后端实测值的带宽。五族实测：mesh / scatter / poly /
#: linecoll 逐个**精确相等**，contour 0.916（被裁剪的等值线上后端给每个
#: `CLOSEPOLY` 多写一条回起点的 `L`）。±15% 容得下那一条，又足够窄
#: ——变异实测：网格顶点数写成 4（比 0.800）与 `_shares_geometry` 判反
#: （散点比 500）都当场红。
_VERTEX_BAND = (0.85, 1.15)


def test_vertex_model_tracks_what_the_backend_writes(probe):
    """顶点估值必须落在后端实测值的 ±15% 带内——**散点也在这条里**。

    第一版把这条写成「模型 ≥ 后端」，尺子用的是 `M`/`L` 指令数。那把尺子
    量不了贝塞尔（一个 `C` 吃掉 3 个顶点），于是散点那一格恒等成立，而**恒等
    成立的判据挡不住任何东西**：变异实测把 `_shares_geometry` 改成恒 False
    （散点的顶点估值从 26 变成 13 000，500 倍），整套用例全绿。换成按指令
    权重折算之后同一个变异当场红。

    `_shares_geometry` 是这条用例真正守着的东西：它决定几何进 `<defs>` 只写
    一遍、还是每个实例各写一遍——两者差 `n_instances` 倍。
    """
    lo, hi = _VERTEX_BAND
    for row in probe["crosscheck"]:
        assert row["unknown_cmds"] == [], f"{row['case']}: 出现了换算表外的指令，这个数不可信"
        model, real = row["model_vertices"], row["svg_delta_vertices"]
        assert real > 0, f"{row['case']}: 对拍那一侧没量到东西，判据是空的"
        assert lo <= model / real <= hi, (
            f"{row['case']}: 模型 {model} vs 后端实际 {real}（比 {model / real:.3f}）"
        )


# --------------------------------------------------------------------------
# 4. 分析器是只读的
# --------------------------------------------------------------------------
def test_analyzer_never_touches_artist_rasterized(cases):
    """**不变量 2 的第一道防线**：分析器不设 `set_rasterized`。

    设了的话预览的表示法就被写进了常驻 Figure，而 `do_export` 读的就是它
    ——用户投出去的 PDF 里那块 mesh 会是位图。真正在 `savefig` 前后设 / 还原
    它是 Session 03 的事，且必须在 `finally` 里还原。

    色条的色带（`cb.solids`）**本来就是 `rasterized=True`**（matplotlib 自己
    设的），所以这里比的是「前后相同」，不是「全都是 False」——后者会把一个
    我们没碰过的既有事实说成违规。
    """
    for name, plan in cases.items():
        assert plan["rasterized_before"] == plan["rasterized_after"], name


def test_quadmesh_paths_are_never_built(cases):
    """`QuadMesh.get_paths()` 一次都不许被调到。

    调了就是当场把网格摊成 M×N 个 `Path`（实测 40 000 个 cell 要 37.8 ms），
    而 `QuadMesh` 的 draw 走 `draw_quad_mesh`、**根本不经过 paths**——那笔钱
    连 render 自己都不付，分析器更不该替它付。判据是「建完之后 `_paths` 就
    不再是 None」这个状态读数，不是「看起来挺快」。
    """
    for name, plan in cases.items():
        built = plan["quadmesh_paths_built"]
        assert not any(built), f"{name}: 有 QuadMesh 的 paths 被建出来了（{built}）"
    assert cases["issue_181"]["quadmesh_paths_built"], "这张图该有 QuadMesh，读数不能是空的"


def test_unbuilt_lazy_collection_is_reported_not_priced_as_zero(cases):
    """paths 还没建的 `TriMesh`：**绕开，但不假装它便宜**。

    它进 `unknown` 清单（诊断看得见），不进 rasterize 名单（量不出来就不知道
    该不该动它），也不谎报一个 hybrid。兜底的是 Session 01 那道按字节的硬闸
    ——**它不需要认识任何人**。
    """
    plan = cases["trimesh_unbuilt"]
    assert plan["unknown_families"] == ["collection_unmeasured"]
    assert plan["mode"] == pb.MODE_VECTOR
    assert plan["rasterized_artist_count"] == 0
    # 对照：同一个类，paths 已经建好时就按 family 正常定价
    built = cases["trimesh_gouraud"]
    assert built["unknown_families"] == []
    assert built["estimated_primitives"] > 0


def test_analyzer_is_deterministic(cases):
    """同一张图连分析两次，结论逐字段相同。

    不确定的判据会让「这张图有时是 hybrid、有时是 vector」，而那种缺陷只会在
    用户那边、在大图上发作。抽样估顶点数是**确定性抽样**（永远取前 N 个）
    就是为了这一条。
    """
    for name, plan in cases.items():
        assert plan["twice_identical"], name


# --------------------------------------------------------------------------
# 5. 按 family 判，不按类名 / API
# --------------------------------------------------------------------------
def test_user_subclass_lands_in_the_same_family(cases):
    """`class MyMesh(QuadMesh)` 不用我们改一行代码就该被认出来。

    这是 capability map §0 那个问题的成本侧版本：matplotlib 明天加一个新的
    pyplot 函数，只要它仍然产出已有 family 的 artist，Tavotto 是否不用改代码
    就理解它。按类名分派的实现在这里当场红。
    """
    plain, sub = cases["large_pcolormesh"], cases["user_subclass_mesh"]
    assert sub["families"] == ["mesh"]
    assert {c["type"] for c in sub["costs"]} == {"MyMesh"}
    assert sub["mode"] == plain["mode"] == pb.MODE_HYBRID
    assert sub["estimated_primitives"] == plain["estimated_primitives"]


def test_unknown_artist_neither_crashes_nor_moves_the_verdict(cases):
    """认不出来的轻量 Artist：不许崩，不许被静默丢掉，也不许改掉裁决。

    ADR 0022 不变量 5 说「不认识时按贵的算」，Session 02 的边界说「不要在
    analyzer 里随意把 unknown 判成 raster」。两句话说的是两件事：**不假装它
    便宜**（它进 unknown 清单，诊断看得见），**也不替它做 hybrid**（我们不
    知道它被 rasterize 之后长什么样）。
    """
    plan = cases["custom_artist"]
    assert plan["mode"] == pb.MODE_VECTOR
    assert "unknown" in plan["families"]
    unknown = [c for c in plan["costs"] if c["family"] == "unknown"]
    assert [c["type"] for c in unknown] == ["Doodad"]
    assert not any(c["rasterizable"] for c in unknown)


# --------------------------------------------------------------------------
# 6. 色条轴与热路径开销
# --------------------------------------------------------------------------
def test_plan_for_state_skips_colorbar_internals(probe):
    """色条的色带与分隔线不是用户的数据层。

    `cb.solids` 是一个 `QuadMesh`、`cb.dividers` 是一个 `LineCollection`，两者
    每次 `_draw_all()` 都被删掉重建（capability map §4 的 `ephemeral`）——
    名单里不该出现随时换身份的对象。哪些轴是色条轴由 `manifest.instrument`
    算过一次，`plan_for_state` 读它，不另算一遍。
    """
    row = probe["state_case"]
    assert row["colorbar_axes_count"] == 1
    with_internals = row["with_colorbar_internals"]
    without = row["skipping_colorbar_axes"]
    assert "mesh" in with_internals["families"], "对照那一侧本来就该看得见色带"
    assert without["families"] == ["image"], f"色条内部件没被跳过：{without['families']}"
    assert without["estimated_primitives"] < with_internals["estimated_primitives"]


def test_analyzer_cost_is_negligible_next_to_the_render_it_guards(cases):
    """分析器进 render 热路径，它必须比它要省下的那件事便宜好几个数量级。

    这条是**粗闸**，不是性能基准（真实数字记在 `docs/perf-baseline.md` 的
    「复杂度分析器开销」一节，实测 #181 fixture 0.03 ms）。50 ms 留了三个数量
    级的余量，它挡的是一整类回归：谁在热路径上调了 `QuadMesh.get_paths()`
    （三格 40 000 cell ≈ 110 ms）或者顺手复制了一次数据数组。
    """
    for name, plan in cases.items():
        assert plan["analyze_ms"] < 50.0, f"{name}: 分析耗时 {plan['analyze_ms']} ms"
