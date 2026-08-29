"""Hybrid 预览：mesh 层临时 rasterize，文字 / 轴 / 图例 / 曲线保持矢量。

ADR 0022 的 Session 03，也是整条链路上唯一**真的省下那 12 秒**的一步：
Session 01 让 126 MB 的产物不进内存与 DOM，Session 02 算出「该 rasterize 谁」，
这里把名单变成 `savefig` 那一瞬的 `set_rasterized`。

这套用例钉六件事：

1. **冷 build 与热 render 走同一条策略**——只在 render request 上 rasterize 的话，
   用户**第一次打开** #181 那张图仍然要先等十几秒；
2. **用户 Figure 的真实状态精确还原**——原本 `False` 的还回 `False`，原本
   `True` 的还回 `True`，`savefig` 中途抛异常也还得回去；
3. **语义 manifest 一个字节不变**（不变量 1）——同一张图、同一个会话，
   hybrid 与纯矢量两版 manifest 逐字节相同；
4. **导出不继承任何预览专用的 rasterization**（不变量 2）；
5. **硬闸照旧生效**（不变量 3）——hybrid 产物超限时同样不读；
6. **该留矢量的真的留住了**——普通曲线 / 图例 / 坐标轴的 gid 在 SVG 里还在。

本进程不 import matplotlib（Flask 侧的依赖边界）：全部读数来自
`tests/support/preview_hybrid_probe.py`，经 worker 解释器起一次。
"""

import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool, previewbudget as pb

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

#: 探针里的 mesh 边长（`preview_hybrid_probe.DEFAULT_N`）：40 000 cells/格 =
#: `MESH_CELL_BUDGET` 的两倍。够越线，又比基线那个 470 快一个数量级。
PROBE_N = 200


@pytest.fixture(scope="module")
def probe():
    """整套用例共用一次探针（起解释器 + 画三块 4 万 cell 的 mesh 是唯一慢的一步）。"""
    script = Path(__file__).resolve().parent / "support" / "preview_hybrid_probe.py"
    out = subprocess.run(
        [WORKER_PY, str(script), "--n", str(PROBE_N)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def life(probe):
    return probe["lifecycle"]


# --------------------------------------------------------------------------
# 1. 冷 build 与热 render：同一条策略
# --------------------------------------------------------------------------
def test_cold_build_already_produces_the_hybrid_preview(life):
    """**第一次打开就得是 hybrid。**

    冷 build 那条路（`instrument_all()` → `render()`）与热 render 是同一个
    `render()`。它们要是分叉，用户打开 #181 那张图仍然先等十几秒把 66 万个
    `<path>` 画出来，然后才在第一次拖动时"变快"——那不叫修好。
    """
    assert life["cold"]["paths"] == life["hot"]["paths"]
    assert life["cold"]["bytes"] == life["hot"]["bytes"]
    # 三块 mesh 各换成一个 `<image>`（第 4 个是色条色带，它本来就是位图）
    assert life["cold"]["images"] == 4
    assert life["cold"]["paths"] < 1_000, "冷 build 仍然吐出了大量矢量 path"


def test_hot_render_reports_hybrid_metadata(life):
    """协议里那几个字段：mode / reason / 数量 / 两个估算。"""
    preview = life["preview"]
    assert preview["mode"] == pb.MODE_HYBRID
    assert preview["reason"] == pb.REASON_COMPLEXITY_BUDGET
    assert preview["rasterized_artist_count"] == 3
    assert preview["svg_bytes"] == life["hot"]["bytes"]
    # 估算报的是**纯矢量画法下的开销**（= 我们省下来的那些），不是产物的实况
    assert preview["estimated_primitives"] > 100_000
    assert preview["estimated_vertices"] > preview["estimated_primitives"]
    assert life["has_svg_in_response"], "hybrid 有 SVG，它仍然走内联那条路"


def test_preview_plan_costs_nothing_next_to_the_savefig_it_saves(life):
    """分析器进的是 render 热路径，它必须比它省下的那件事便宜几个数量级。"""
    assert life["timings"]["preview_plan_ms"] < 1.0
    assert life["timings"]["preview_plan_ms"] * 1000 < life["timings"]["canvas_draw_ms"]


def test_each_timing_key_measures_the_span_it_claims_to(probe):
    """**两个方向各注入一次 50 ms**：只钉一侧的判据在两段互换标签时全绿。

    这两个数会进 `docs/perf-baseline.md`，而量错对象的数字看起来和真的一模
    一样——接线的第一版把 `preview_plan_ms` 掐在「掐表到调用之间」，稳定报
    0.007 ms（分析真实是 0.0165 ms），没有任何地方会因此报错。
    """
    t = probe["timing_attribution"]
    slow_plan, slow_draw = t["slow_plan"], t["slow_savefig"]
    assert slow_plan["preview_plan_ms"] >= 50.0, slow_plan
    assert slow_plan["canvas_draw_ms"] < 50.0, slow_plan
    assert slow_draw["canvas_draw_ms"] >= 50.0, slow_draw
    assert slow_draw["preview_plan_ms"] < 50.0, slow_draw


# --------------------------------------------------------------------------
# 2. 相对回归门禁：数量级下降，不钉绝对值
# --------------------------------------------------------------------------
def test_hybrid_is_an_order_of_magnitude_smaller_than_the_vector_baseline(life):
    """A/B **在同一进程同一次运行里交替**：同一张图、同一个会话、紧挨着的两次。

    绝对 wall time 不稳定，字节数与节点数稳定——所以门禁钉后两者。比值是
    「数量级下降」这句话的可证形式：0.15 / 0.10 都比实测宽一个数量级以上
    （实测约 0.026 / 0.0006），留的是 matplotlib 版本与 colormap 的浮动余量，
    不是给回归留的。
    """
    vec, hyb = life["vector"], life["hot"]
    assert vec["paths"] > 100_000, "对照组自己就没走纯矢量，这条比值恒等于 1"
    assert hyb["bytes"] <= vec["bytes"] * 0.15, (vec, hyb)
    assert hyb["paths"] <= vec["paths"] * 0.10, (vec, hyb)


def test_the_two_ways_of_counting_the_same_svg_agree(life):
    """基线表里那两个数出自**分块**计数——它多数一个还是少数一个不会有人报错。

    所以这里用三种数法数同一份产物：整份文本一遍、1 MiB 块一遍、997 字节块
    （小到会把 `<path` 切成两半）一遍。三者必须相等。
    `scripts/bench_render.py` 当年缺了「减去重叠区里数得完整的那些」，#181 的
    基线因此把 662 772 报成了 662 773——一个没有任何地方会报错的数字。
    """
    c = life["counter_agreement"]
    assert c["whole_text"]["paths"] == c["chunked_1mib"]["paths"] == c["chunked_997b"]["paths"]
    assert c["whole_text"]["images"] == c["chunked_1mib"]["images"] == c["chunked_997b"]["images"]
    assert c["whole_text"]["bytes"] == c["chunked_997b"]["bytes"]
    # 用例前提：小块那一遍真的切开了 needle（否则三者相等是恒真的）
    assert c["chunked_997b"]["bytes"] > 997 * 4


def test_the_vector_control_group_really_is_vector(life):
    """对照组必须真的是对照组。

    六个闸只抬掉一个的话，"纯矢量"那一版自己也会掉进 hybrid——那时 A/B 两侧
    一模一样，上面那条比值恒成立，尺子量不到它要量的那一维。
    """
    assert life["vector_preview"]["mode"] == pb.MODE_VECTOR
    assert life["vector_preview"]["rasterized_artist_count"] == 0
    assert life["vector"]["images"] == 1  # 只剩色条色带那一张


# --------------------------------------------------------------------------
# 3. 精确还原（这一条错了，用户的论文会糊）
# --------------------------------------------------------------------------
def test_user_state_is_exactly_restored_after_every_preview(life):
    assert life["rasterized_before"] == [False, False, False]
    assert life["rasterized_after_cold"] == [False, False, False]
    assert life["rasterized_after_hot"] == [False, False, False]
    assert life["rasterized_after_export"] == [False, False, False]


def test_restoring_means_the_original_value_not_false(probe):
    """用户自己设了 `rasterized=True` 的那块 mesh，预览之后**仍然是 True**。

    「还原」不是「关掉」。把还原写成 `set_rasterized(False)` 的实现在这条
    用例上才会红——在上面那条（原值全是 False）上它一路绿
    （[[getter-must-be-restorable]]）。
    """
    u = probe["user_rasterized"]
    assert u["before"] == [True, False, False]
    assert u["after_cold"] == [True, False, False]
    assert u["after_hot"] == [True, False, False]
    assert u["after_export"] == [True, False, False]
    # 导出里那块仍然是位图：用户的选择照旧生效（一块 mesh 少 40 000 个 path）
    assert u["export"]["images"] == 2
    assert u["export"]["paths"] == 80_072


def test_savefig_raising_still_restores_every_artist(probe):
    """`savefig` 抛了：`finally` 照旧还原。

    不还原的话下一次导出会把预览用的位图化当成用户的选择写进论文里，而中间
    没有任何一步会报错。
    """
    r = probe["savefig_raises"]
    assert r["raised"], "用例前提：savefig 真的抛了"
    # **窗口里确实设上了**——不设也能"还原正确"，那是一条恒真的判据
    assert r["rasterized_inside_window"] == [[True, True, True]]
    assert r["after"] == [False, False, False]


# --------------------------------------------------------------------------
# 4. context manager 自己的纪律（故障注入）
# --------------------------------------------------------------------------
def test_context_manager_restores_each_original_value(probe):
    cm = probe["contextmanager"]
    assert cm["empty_is_noop"]
    assert cm["inside"] == [True, True]
    assert cm["restored"] == [False, True]
    # 窗口体自己抛：异常照旧传出去，状态照旧还原
    assert cm["body_raise"] == "body"
    assert cm["body_raise_values"] == [False, True]


def test_a_failure_while_entering_rolls_back_what_was_already_set(probe):
    """进窗口时第二个炸了：**第一个必须被还回去**，第三个根本不该被碰。

    半设一半的 Figure 是谁都没设计过的中间态，而它会被下一次导出读到。
    """
    cm = probe["contextmanager"]
    assert cm["enter_boom"], "用例前提：进窗口时真的抛了"
    assert cm["enter_boom_values"] == [False, False, False]
    assert cm["enter_boom_third_untouched"]


def test_a_failure_while_restoring_still_restores_the_others_and_shouts(probe):
    """还原时第二个炸了：其余两个照样还原，并且**要吵出来**。

    静默吞掉它 = 常驻 Figure 带着预览专用的表示法活下去，下一次导出把它写进
    论文，而没有任何地方会报错。
    """
    cm = probe["contextmanager"]
    assert "没能还原" in cm["exit_boom"], cm["exit_boom"]
    # 第一个（原值 False）与第三个（原值 True）都回到了各自的原值
    assert cm["exit_boom_values"][0] is False
    assert cm["exit_boom_values"][2] is True


# --------------------------------------------------------------------------
# 5. 五条不变量里与这一步相关的三条
# --------------------------------------------------------------------------
def test_semantic_manifest_does_not_change_with_the_representation(life):
    """不变量 1：同一组 patches、同一个 stem，两档下 manifest **逐字节相同**。

    违反它的表现是"切到 hybrid 之后某个元素在属性页里消失了"——前端按 gid
    索引一切，那是数据级错位，且只在大图上、在用户那边发作。
    """
    assert life["manifest_identical"]
    assert life["manifest_elements"] == 95


def test_export_inherits_nothing_from_the_preview(life):
    """不变量 2：导出仍然是**用户原来的矢量语义**。

    预览糊一点没人会因此撤稿，导出糊一点会。
    """
    assert life["export"]["paths"] == life["vector"]["paths"]
    assert life["export"]["images"] == 1
    assert life["export"]["bytes"] > 20_000_000


def test_the_hard_gate_still_bites_on_a_hybrid_product(probe):
    """不变量 3 不因为"我们已经尽力了"而放松。

    hybrid 产物照样可能超硬闸（收不动的层太多、或者矢量层本身就巨大）。
    判据仍然是"那次读根本没有发生"，不是"响应里没有 svg"。
    """
    g = probe["hard_guard"]
    assert g["preview"]["mode"] == pb.MODE_RASTER
    assert g["preview"]["reason"] == pb.REASON_SVG_HARD_LIMIT
    assert not g["has_svg_in_response"]
    assert g["svg_read_text_calls"] == 0
    # 仍然是一次**成功的**渲染：manifest 齐全
    assert g["manifest_elements"] == 95
    # 名单照旧报出来——降到 raster 不代表这一版没 rasterize 过任何东西
    assert g["preview"]["rasterized_artist_count"] == 3


# --------------------------------------------------------------------------
# 6. 该留矢量的留住了 / 该丢的 gid 允许丢
# --------------------------------------------------------------------------
def test_text_axes_legend_and_ordinary_curves_stay_vector(life):
    """hybrid 的契约：**科研数据层 = 位图，语义编辑层 = 矢量**。

    #181 fixture 的第四格（两条普通曲线 + 图例）是判据的一部分，不是装饰：
    一张只有 mesh 的图问不出"hybrid 有没有把该留的留住"。
    """
    assert life["vector_gids_in_svg"] == life["vector_gids"]
    assert len(life["vector_gids"]) == 4


def test_rasterized_layers_may_lose_their_gid_node(life):
    """rasterize 掉的 artist 在 SVG DOM 里**没有自己的节点了**——这是允许的。

    不为了保住假实时预览去造几千个隐藏占位节点、不重新矢量化 mesh、不动
    manifest 的 gid 语义。前端在 `findGidNode` 返回 null 时安静退出、覆盖层
    接管（`web/src/store/svgPreviewStore.ts`），几何权威照旧是 exact manifest。
    """
    assert life["mesh_gids"], "用例前提：manifest 里这三块 mesh 确实各有 gid"
    assert life["mesh_gids_in_svg"] == []


# --------------------------------------------------------------------------
# 7. 写回：这是表示法策略，不是用户改动
# --------------------------------------------------------------------------
def test_rasterized_is_not_a_property_the_override_layer_knows_about(probe):
    """`overrides.HANDLERS` 里没有 `rasterized` 这一项——**结构性的，不是纪律性的**。

    写回 payload 与 bake 进用户脚本的东西都出自 override 表。表里没有这个属性，
    "预览的表示法漏进用户的文件"就不是一件需要谁记得避免的事。哪天有人为了
    别的需求把它注册进去，这条会当场红，那时才需要重新想一遍。
    """
    assert probe["handlers_know_rasterized"] is False


def test_the_preview_representation_never_enters_the_override_table(life):
    """hybrid 渲染之后会话的 override 快照仍然是空的。

    写回事务的不变式是"热态所见 == 写进文件的 == 重开后重放出来的"。预览的
    rasterization 一旦漏进 `state.applied`，它就会被 bake 进用户的脚本——
    而 `rasterized` 根本不是 `patchspec` 认的属性，这条用例钉的是它**永远
    不该被加进去**。
    """
    assert life["snapshot_after_hybrid"] == []


# --------------------------------------------------------------------------
# 8. 软闸：第二把尺子
# --------------------------------------------------------------------------
def test_the_byte_gate_catches_what_the_complexity_model_underestimated(probe):
    """三块**都在逐族预算之内**的 mesh：分析器判 vector，字节闸把它捞回来。

    两把尺子相互独立才有意义：一把量原料（artist 图上有多少 primitive），
    一把量产物（`savefig` 出来多少字节）。同源的两把只是自己验自己
    （[[crosscheck-needs-independent-sides]]）。
    """
    e = probe["escalation"]
    assert e["baseline_preview"]["mode"] == pb.MODE_VECTOR, "用例前提：第一遍确实判的是 vector"
    assert e["baseline_savefig_calls"] == 1
    assert e["baseline_svg"]["bytes"] > e["soft_limit"], "用例前提：产物确实越过了软闸"
    # 升档：第二遍把三块全收进来
    assert e["savefig_calls"] == 2, "越过软闸却没有第二遍"
    assert e["preview"]["mode"] == pb.MODE_HYBRID
    assert e["preview"]["rasterized_artist_count"] == 3
    assert e["svg"]["bytes"] < e["soft_limit"]


def test_no_second_pass_when_there_is_nothing_left_to_collect(probe):
    """越过软闸、但**一个可 rasterize 的层都没有**：只画一遍，老实报 vector。

    六十条普通曲线按契约一条都不许收，再画一遍不会变小——那一次 `savefig`
    就是白付的钱。报一个我们做不到的 `hybrid` 更坏：前端会去等一份永远不来
    的混合产物。
    """
    n = probe["no_escalation_possible"]
    assert n["svg"]["bytes"] > n["soft_limit"], "用例前提：产物确实越过了软闸"
    assert n["savefig_calls"] == 1, "没东西可收，却还是画了第二遍"
    assert n["preview"]["mode"] == pb.MODE_VECTOR
    assert n["preview"]["rasterized_artist_count"] == 0


def test_ordinary_figures_are_byte_identical_to_a_world_without_hybrid(probe):
    """普通科研图：名单是空的，产物与直接 `savefig` **逐字节相同**。

    hybrid 的第一条代价必须是零——#181 的用户一共就那么几张大图，其余全是
    正常图，它们不该为这条通路付任何东西。
    """
    nf = probe["normal_figure"]
    assert nf["preview"]["mode"] == pb.MODE_VECTOR
    assert nf["plan_empty"]
    assert nf["identical_to_plain_savefig"]
