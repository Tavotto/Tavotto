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
        errors="replace",
        check=False,
    )
    # **不用 `check=True`**：它抛的 `CalledProcessError` 里没有子进程的 stderr，
    # 于是探针挂掉时日志上只有一句 "returned non-zero exit status 1"，27 条用例
    # 一起 ERROR 而没人说得出为什么。实测在 Windows 上撞过一次（探针把带中文的
    # JSON 打到 cp1252 的管道），从日志里查不出真因，只能本地拿
    # `PYTHONIOENCODING=cp1252` 重现。判据要说得出自己看见了什么。
    if out.returncode != 0:
        raise AssertionError(
            f"探针退出码 {out.returncode}\n"
            f"--- stderr ---\n{out.stderr[-4000:]}\n"
            f"--- stdout 尾部 ---\n{out.stdout[-1000:]}"
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

    **两个版本上逐个数字相同**（playground 的 3.10.8 与桌面 runtime 的
    3.11.1，含 contour 那 0.916）——模型抄的是 matplotlib 的绘制路径，所以
    「它会不会随版本漂」这个问题必须真的量一次，不能只在一个版本上跑完就
    宣称它稳。
    """
    for row in probe["crosscheck"]:
        model = row["model_primitives"]
        path, use, image = row["svg_delta_path"], row["svg_delta_use"], row["svg_delta_image"]
        if image:
            # 已经是位图的 artist：整个摊成**一个** `<image>`。
            assert (image, path, use) == (model, 0, 0), f"{row['case']}: {(image, path, use)}"
        elif use:
            # 几何进了 `<defs>`：每个实例一个 `<use>`，几何本身只写一遍。
            # 这同时验了 `_shares_geometry`——它要是判反了，这里会是
            # use=0 而 path=model。
            assert use == model, f"{row['case']}: 模型 {model} 个实例，SVG 里 {use} 个 <use>"
            assert path == 1, f"{row['case']}: 共享几何时 defs 里应当只有一条 path，实得 {path}"
        else:
            assert path == model, f"{row['case']}: 模型 {model} 个节点，SVG 里 {path} 个 <path>"


#: 节点估值允许偏离后端实测值的带宽。十格实测最大偏差 9.1%（contour 那格
#: 10 vs 11——差的就是一个容器 `<g>`，而它只有 10 个 primitive，常数占比大）。
#: ±15% 容得下那个常数，又足够窄：`line` 记 1 而不是 2 会是 0.500、
#: colormapped 散点少记一倍会是 0.499，两个变异都当场红。
_NODE_BAND = (0.85, 1.15)


def test_node_model_matches_what_the_svg_backend_actually_emits(probe):
    """`node_count` 与后端**真的吐出来的元素数**对一次。

    **`primitive_count` 那条对拍证明不了这一条**：两者在 mesh 上恰好相等
    （一个 cell 一个 `<path>`），在 `Line2D` 上差一倍（一个 `<path>` **外加
    一个 `<g>`**）。#181 的残余缺口就藏在这个差别里——4 万条 `plot()` 的
    primitive 数在图级预算之内、字节数在硬闸之下，三条闸一条都不响，DOM 里
    却是 20 万个节点。

    `line` 这一族**原本被对拍豁免掉了**（探针的 `skip` 里有 `FAMILY_LINE`），
    被豁免的那一族正好是出问题的那一族——所以这一轮把它补进了对拍。
    """
    for row in probe["crosscheck"]:
        model = row["model_nodes"]
        actual = (
            row["svg_delta_path"]
            + row["svg_delta_use"]
            + row["svg_delta_image"]
            + row["svg_delta_g"]
        )
        if actual <= 0:
            # **后端没为这个 artist 写任何数据层节点的那几格**（不可见 artist：
            # `_iter_artists` 过滤掉它，后端也一个都不写）。带宽判据在这里问的
            # 是另一个问题——0/0 会除零，而隐藏整个 axes 那一格差分还会是
            # **-1**（带 artist 的图比对照图少一个容器 `<g>`）。两种都表示
            # 「没有数据层节点」，判据要说的话是「模型也不该记账」。
            assert model == 0, f"{row['case']}: 后端写出 {actual} 个，模型却记了 {model} 个"
            continue
        ratio = model / actual
        assert _NODE_BAND[0] <= ratio <= _NODE_BAND[1], (
            f"{row['case']}: 模型 {model} 个节点，后端写出 {actual} 个（{ratio:.3f}×）"
        )


def test_a_line_costs_two_dom_nodes_not_one(probe):
    """`Line2D`：**一个 `<path>` 是对的，一个节点是错的。**

    对拍实测 400 条线 = 400 个 `<path>` + 400 个 `<g>`。这条与上面那条带宽
    判据不同——它要的是**精确的 2 倍**，因为整条修复就架在这个系数上。
    """
    row = next(r for r in probe["crosscheck"] if r["case"] == "lines")
    assert row["svg_delta_path"] == 400, row
    assert row["svg_delta_g"] == 400, f"每条线应当各带一个 <g>: {row}"
    assert row["model_nodes"] == 800 == row["svg_delta_path"] + row["svg_delta_g"]
    assert row["model_primitives"] == 400, "primitive 仍然是一条线一个——变的是节点口径"


def test_colormapped_scatter_costs_two_nodes_per_instance(probe):
    """逐实例着色的散点同样是 2 倍——**而 contour 不是**。

    `c=<数组>` 时后端要给每个实例单独写 style，于是每个 `<use>` 外面再包一个
    `<g>`（实测 500 个 `<use>` + 501 个 `<g>`）。判据必须同时要求「几何共享」：
    contour 也有 `get_array()`，但它每层本来就是独立 `<path>`、自带 style，
    不需要那个 `<g>`——少了这半个条件，contour 整族的节点数凭空翻倍。
    """
    mapped = next(r for r in probe["crosscheck"] if r["case"] == "scatter_mapped")
    assert mapped["model_nodes"] == 1000 == mapped["model_primitives"] * 2
    assert mapped["svg_delta_use"] == 500 and mapped["svg_delta_g"] == 501

    uniform = next(r for r in probe["crosscheck"] if r["case"] == "scatter_uniform")
    assert uniform["model_nodes"] == uniform["model_primitives"] == 500, "纯色散点不该翻倍"

    contour = next(r for r in probe["crosscheck"] if r["case"] == "contour")
    assert contour["model_nodes"] == contour["model_primitives"], "contour 不该翻倍"


def test_many_plain_lines_fall_back_to_raster(cases):
    """**#181 的残余缺口，这条是它的看护。**

    4 万次 `ax.plot()` 是普通 matplotlib 写法。它的 primitive 数（40 000）在
    `TOTAL_VECTOR_PRIMITIVE_BUDGET`（50 000）之内、字节数 9.33 MB 在 16 MiB
    硬闸之下——**原有三条闸一条都不响**，而实测它往 DOM 里挂 201 977 个节点、
    330–360 MB、一次挂载 410 ms。`Line2D` 按契约不可 rasterize，收不动，
    所以唯一正确的出路是降到 raster（不变量 5）。
    """
    plan = cases["many_lines_over_node_budget"]
    assert plan["mode"] == pb.MODE_RASTER, plan["detail"]
    assert plan["reason"] == pb.REASON_COMPLEXITY_BUDGET
    # 用例前提：另外两条闸确实都不响，否则这条测的是别人
    assert plan["estimated_primitives"] <= pb.TOTAL_VECTOR_PRIMITIVE_BUDGET
    assert plan["vector_nodes"] > pb.TOTAL_VECTOR_NODE_BUDGET
    assert plan["rasterized_artist_count"] == 0, "线一条都收不动，这正是降档的理由"


def test_lines_under_the_node_budget_stay_vector(cases):
    """**分界线的另一侧**：1 万条线（20 000 个节点）照旧内联。

    只有两侧都钉住，这条预算才是一条线而不是一个方向。实测 1 万条 =
    51 650 个 DOM 节点、一次挂载 103 ms——100 ms 是「瞬时」的人机边界，
    阈值就是照着它选的。
    """
    plan = cases["many_lines_under_node_budget"]
    assert plan["mode"] == pb.MODE_VECTOR, plan["detail"]
    assert plan["vector_nodes"] <= pb.TOTAL_VECTOR_NODE_BUDGET


def test_collectible_layers_are_collected_further_not_downgraded(cases):
    """**能收就收满，不是收一半就降档。**

    四格各 15 129 个 cell：只看 primitive 预算的话收掉一格就「达标」了
    （45 387 ≤ 50 000），却把 45 388 个元素交给 DOM——正是节点预算要拦的
    量级，而那些 mesh **本来就是可以收的**。两条图级预算必须一起收敛。
    """
    plan = cases["many_medium_meshes"]
    assert plan["mode"] == pb.MODE_HYBRID, plan["detail"]
    assert plan["rasterized_artist_count"] == 3, "收一格是不够的"
    assert plan["vector_nodes"] <= pb.TOTAL_VECTOR_NODE_BUDGET
    assert plan["vector_primitives"] <= pb.TOTAL_VECTOR_PRIMITIVE_BUDGET


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
        if row["svg_delta_image"]:
            # 已经是位图：两侧都该是 0 个顶点，比值没有意义
            assert (model, real) == (0, 0), f"{row['case']}: {(model, real)}"
            continue
        if row["case"].startswith("mesh_hidden"):
            # 不可见那两格：后端一个顶点都不写，模型也必须报 0。这里比的是
            # 「两侧都是 0」，不是比值——0/0 没有意义，而**恒等成立的判据挡不住
            # 任何东西**，所以它必须走一条自己的断言，不能混进带宽那条里。
            assert (model, real) == (0, 0), f"{row['case']}: {(model, real)}"
            continue
        assert real > 0, f"{row['case']}: 对拍那一侧没量到东西，判据是空的"
        assert lo <= model / real <= hi, (
            f"{row['case']}: 模型 {model} vs 后端实际 {real}（比 {model / real:.3f}）"
        )


def test_vertex_sampling_covers_the_whole_collection_not_just_one_end(probe):
    """**异构 collection 上的顶点抽样不许只看一端——两端都不许。**

    重几何排在一头的形状很常见（`PolyCollection` 的等值面、分箱统计、地理
    边界）。只看一端的抽样在它上面会系统性低估，于是一个真有几十万顶点的层被
    估成便宜的、停在 `vector`——而那正是这个分析器存在的理由。

    判据是**一对**图 ×（真值 / 前缀 / 后缀 / 等距）四个数，全部由探针现算：

    | | 真值 | 前缀 | 后缀 | 等距 |
    |---|---:|---:|---:|---:|
    | 重几何在尾 | 33 006 | **21 030** | 33 328 | 31 278 |
    | 重几何在头 | 33 006 | 33 328 | **21 030** | 33 328 |

    只留尾重那张的话，把实现从「取前 N 条」换成「取后 N 条」照样全绿——同一个
    缺陷换了个方向（[[gate-pinned-on-one-side-only]]，变异实测过）。等距抽样
    要在**两张图上都落进带内**，而两种只看一端的数法要各自在一张图上出局。

    评审 P2（PR #199）。修的是实例，判据钉的是这一族形状。
    """
    v = probe["vertex_sampling"]
    lo, hi = _VERTEX_BAND
    for name in ("tail_heavy", "head_heavy"):
        row = v[name]
        assert row["paths"] > v["sample_cap"], f"{name}: 用例前提——path 数确实超过取样上限"
        assert row["exact_flag"] is False, f"{name}: 用例前提——这一格走的是抽样那一支"
        assert lo <= row["stride"] / row["exact"] <= hi, (
            f"{name}: 等距抽样 {row['stride']} vs 真值 {row['exact']}"
            f"（比 {row['stride'] / row['exact']:.3f}）"
        )
    # **两种只看一端的数法各在一张图上出局**——否则这对图区分不开它们，
    # 上面那两条就是恒真的
    assert v["tail_heavy"]["prefix"] / v["tail_heavy"]["exact"] < lo, "尾重那张上前缀抽样应当出局"
    assert v["head_heavy"]["suffix"] / v["head_heavy"]["exact"] < lo, "头重那张上后缀抽样应当出局"


def test_invisible_artists_are_not_priced(probe):
    """`visible=False` 的 artist 与 axes **一分钱都不该记**。

    后端对不可见的 artist 一个节点都不写（对拍两格的 `svg_delta_path` 都是 0）。
    按全价记进账的话，一块藏起来的大 mesh 就能凭空逼出一次 hybrid——用户看到
    的是「明明没显示那层图，画面却糊了」。这不是保守，是量错了对象。

    判据取自与后端的 A/B 差分，不是「读一遍代码觉得对」。评审 P2（PR #199）。
    """
    rows = {r["case"]: r for r in probe["crosscheck"]}
    for case in ("mesh_hidden", "mesh_hidden_axes"):
        row = rows[case]
        assert row["svg_delta_path"] == 0, f"{case}: 用例前提——后端确实什么都没画"
        assert row["model_primitives"] == 0, f"{case}: 模型仍然在给不可见的层记账"
        assert row["model_vertices"] == 0, case
    # 对照：**同一块网格显示出来时两侧都不是 0**，否则上面那两条恒成立
    visible = rows["mesh"]
    assert visible["svg_delta_path"] > 0 and visible["model_primitives"] > 0


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
    # 对照：**同一个类**，paths 已经建好时就按 family 正常定价。少了这一半，
    # 上面那条在「分析器把所有 Collection 都标成量不出来」时也会绿。
    #
    # 这一格的状态由探针显式建出来（`get_paths()`），不借 matplotlib 的副作用
    # ——`ax.tripcolor` 建不建 paths 是**版本相关**的：3.10.8 建（`add_collection`
    # 的 `get_datalim`），3.11.1 不建。第一版靠那条副作用，于是本机绿、CI 红。
    built = cases["trimesh_built"]
    assert built["unknown_families"] == []
    assert built["estimated_primitives"] > 0


def test_already_rasterized_artists_cost_one_image_not_n_paths(cases):
    """`rasterized=True` 的 artist 在 SVG 里是**一个 `<image>`**。

    按它的 family 定价就是给 DOM 记一笔不存在的账：色条的色带是 matplotlib
    自己设成 rasterized 的 `QuadMesh`，不认这一条，每张带色条的图都凭空多背
    256 个 primitive（#181 fixture 上模型算 662 959、后端实际 662 773 个
    `<path>` + 1 个 `<image>`，差的就是它）。

    这也是 Session 03 落地之后该看到的账：它给 mesh 层设上 `rasterized`，
    再分析同一张图就会走到这一族——不用另写一套。
    """
    solids = [
        c
        for c in cases["imshow_colorbar"]["costs"]
        if c["family"] == "rasterized" and c["type"] == "QuadMesh"
    ]
    assert len(solids) == 1, cases["imshow_colorbar"]["costs"]
    assert solids[0]["primitive_count"] == 1
    assert solids[0]["vertex_count"] == 0
    assert not solids[0]["rasterizable"], "已经是位图了，没有什么可再 rasterize 的"


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


def test_an_artist_that_cannot_say_whether_it_is_visible_is_still_priced(cases):
    """**可见性问不出来时按「会画」算**——这个方向是选过的，所以要有人钉着。

    过滤不可见的 artist（评审 P2）之后多了一个新的失败模式：`get_visible()`
    自己抛的 artist（第三方库包装过 `visible` 属性的那些）如果被当成不可见，
    就会**整个从账上消失**——那是漏判方向，正是这个分析器要防的事。
    高估只是多一次不必要的 rasterize，画质降级；漏判是把 66 万个 `<path>`
    放进浏览器。

    变异实测：把 `_visible` 的兜底从 `True` 改成 `False`，只有这条会红。
    """
    plan = cases["blind_artist"]
    assert "unknown" in plan["families"], plan["families"]
    assert [c["type"] for c in plan["costs"] if c["family"] == "unknown"] == ["BlindDoodad"]
    # 不许崩，也不许因为它把裁决改掉
    assert plan["mode"] == pb.MODE_VECTOR


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
    # 对照那一侧本来就该看得见色带（QuadMesh，matplotlib 自己设成了
    # rasterized）与分隔线（LineCollection）——少了这一半，下面那条断言在
    # 「这张图压根没有色条」的情况下也会绿。
    types = {(c["family"], c["type"]) for c in with_internals["costs"]}
    assert ("rasterized", "QuadMesh") in types, types
    assert ("linecoll", "LineCollection") in types, types
    assert without["families"] == ["image"], f"色条内部件没被跳过：{without['families']}"
    assert without["estimated_primitives"] < with_internals["estimated_primitives"]


def test_analyzer_cost_is_negligible_next_to_the_render_it_guards(cases):
    """分析器进 render 热路径，它必须比它要省下的那件事便宜好几个数量级。

    这条是**粗闸**，不是性能基准（真实数字记在 `docs/perf-baseline.md` 的
    「复杂度分析器开销」一节，实测 #181 fixture 0.03 ms）。

    上限从 50 ms 放宽到 200 ms，理由不是「它总红」：4 万条 `plot()` 的图实测
    52.6 ms，撑破它的是 **artist 数量**（4 万个 × 1.3 µs），不是每个 artist
    变贵了。对着那张图自己 1 250 ms 的 `savefig` 仍然是 1 : 24。

    **试过把它改成「按 artist 摊薄的单价」，没做成**：不同 family 的工作量
    模型不同（`large_polycollection` 是 1 个 artist 却要遍历几千条 path，
    单价 181 µs），分母换成 primitive 数又会被 mesh 的估算撑大、把真实回归
    掩盖掉。造一个自己都说不清的维度不如不造。

    **真正挡住那类回归的不是这条**，是
    `test_quadmesh_paths_are_never_built`——它读的是 `_paths is not None` 这个
    结构性事实，不受机器快慢与规模影响。这一条只挡「大到离谱」。
    """
    for name, plan in cases.items():
        assert plan["analyze_ms"] < 200.0, f"{name}: 分析耗时 {plan['analyze_ms']} ms"
