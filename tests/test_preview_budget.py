"""预览复杂度预算：常量、判据、两侧同源，以及**超限 SVG 根本没被读**。

ADR 0022 不变量 3 的判据只有一条能算数：

> 超过硬闸时，那份 SVG 的 `read_text()` **一次都没被调用**。

「响应里没有 svg」不算——「先读 126 MB 再把它删掉」也满足那句话，而那正是
issue #181 的成因（一次读加两次 JSON 编解码就让服务进程峰值 RSS 到 1.2 GB，
此时 SVG 一个字节都还没到浏览器）。所以这里走
`tests/support/preview_guard_probe.py`：它在 worker 解释器里把
`pathlib.Path.read_text` 换成记账实现，**阈值之上与之下各跑一次**，报的是
同一根探针在两侧的读数——一次跑出来的绿是样本，不是对照。

本进程不 import matplotlib（Flask 侧的依赖边界）；纯函数那一半直接测。
"""

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool, previewbudget as pb

REPO = Path(__file__).resolve().parent.parent
PROBE = REPO / "tests" / "support" / "preview_guard_probe.py"
MIRROR = REPO / "web" / "src" / "lib" / "previewBudget.ts"

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None


# ------------------------------- 纯函数那一半 -------------------------------
def test_soft_is_below_hard_and_both_are_positive():
    """两个数是一对：软闸在硬闸之下。反了的话 hybrid 永远触发不了。"""
    assert 0 < pb.EDITOR_SVG_SOFT_LIMIT_BYTES < pb.EDITOR_SVG_HARD_LIMIT_BYTES


def test_resolve_mode_bands():
    """字节那一维：硬闸之下是 vector，硬闸之上是 raster。"""
    assert pb.resolve_mode(svg_bytes=0) == (pb.MODE_VECTOR, pb.REASON_NORMAL)
    assert pb.resolve_mode(svg_bytes=pb.EDITOR_SVG_HARD_LIMIT_BYTES - 1)[0] == pb.MODE_VECTOR
    assert pb.resolve_mode(svg_bytes=pb.EDITOR_SVG_HARD_LIMIT_BYTES) == (
        pb.MODE_RASTER,
        pb.REASON_SVG_HARD_LIMIT,
    )


def test_resolve_mode_reports_hybrid_only_when_something_was_rasterized():
    """`hybrid` 不是一个档位名，是一句关于产物的事实陈述。

    「这一版里有 N 个 artist 被临时 rasterize 了」——`rasterized_artist_count`
    是 0 就不许说 hybrid（前端会照着它去等一份永远不来的混合产物）。
    """
    assert pb.resolve_mode(svg_bytes=1_000, rasterized_artist_count=0)[0] == pb.MODE_VECTOR
    assert pb.resolve_mode(svg_bytes=1_000, rasterized_artist_count=1) == (
        pb.MODE_HYBRID,
        pb.REASON_COMPLEXITY_BUDGET,
    )


def test_hard_limit_outranks_hybrid():
    """**顺序不能反**：一版 hybrid 产物照样可能超硬闸，那时它仍然不许被读。

    「我们已经尽力了」不是放松不变量 3 的理由——收不动的层太多、或者矢量层
    本身就巨大的图确实存在，而 126 MB 进不进内存与它是怎么来的无关。
    """
    assert pb.resolve_mode(svg_bytes=pb.EDITOR_SVG_HARD_LIMIT_BYTES, rasterized_artist_count=5) == (
        pb.MODE_RASTER,
        pb.REASON_SVG_HARD_LIMIT,
    )


def test_soft_band_asks_for_a_second_pass():
    """软闸的答案是「再画一遍」，不是一个 mode。

    这条替换的是 Session 02 时期那条「软闸区间照旧透传」的缺口看护——它的任务
    是在 hybrid 落地那一刻当场红，它做到了。软闸现在真的生效：产物越过 8 MiB
    而名单还没收满 ⇒ 把剩下的可 rasterize 层全收进来重画一次
    （`preview_hybrid.save_preview_svg`，端到端看护在
    `tests/test_preview_hybrid.py`）。
    """
    mid = (pb.EDITOR_SVG_SOFT_LIMIT_BYTES + pb.EDITOR_SVG_HARD_LIMIT_BYTES) // 2
    assert pb.wants_hybrid_escalation(mid)
    assert pb.wants_hybrid_escalation(pb.EDITOR_SVG_SOFT_LIMIT_BYTES)
    assert not pb.wants_hybrid_escalation(pb.EDITOR_SVG_SOFT_LIMIT_BYTES - 1)
    # **升档不改变这一版叫什么**：它只影响下一遍画什么。软闸区间里一版没收着
    # 任何层的产物仍然是 vector（收不动就是收不动，硬闸兜底）。
    assert pb.resolve_mode(svg_bytes=mid) == (pb.MODE_VECTOR, pb.REASON_NORMAL)


def test_complexity_budgets_are_positive_and_ordered():
    """复杂度那一侧的四个数：都为正，且**逐族预算不高于图级预算**。

    反了的话第二轮裁决永远轮不到——一个 artist 自己就能吃掉整张图的额度而
    不被认出来，而那正是 `test_a_single_artist_may_not_eat_the_whole_figure_
    budget` 守的场景（多面板大 mesh，#181 的用户环境）。
    """
    per_family = (pb.MESH_CELL_BUDGET, pb.SCATTER_INSTANCE_BUDGET)
    assert all(b > 0 for b in (*per_family, pb.COLLECTION_VERTEX_BUDGET))
    assert pb.TOTAL_VECTOR_PRIMITIVE_BUDGET > 0
    for b in per_family:
        assert b <= pb.TOTAL_VECTOR_PRIMITIVE_BUDGET, (
            f"逐族预算 {b} 高过图级预算 {pb.TOTAL_VECTOR_PRIMITIVE_BUDGET}"
        )


def test_complexity_budgets_land_in_the_same_band_as_the_byte_gates():
    """两侧闸说的是同一件事：一条量原料、一条量产物，**换算完要在同一量级**。

    换算按 #181 实测的 126 132 735 字节 / 662 773 个 `<path>` ≈ 190 字节一个
    primitive。图级预算换算回去要落在软闸附近——比软闸低太多会把正常图误伤，
    高太多则等于这条闸不存在（真到那时字节闸已经先响了，而它要先付 12 秒）。
    """
    bytes_per_primitive = 190
    total = pb.TOTAL_VECTOR_PRIMITIVE_BUDGET * bytes_per_primitive
    assert 0.5 <= total / pb.EDITOR_SVG_SOFT_LIMIT_BYTES <= 2.0, (
        f"图级预算换算成 {total} 字节，与 {pb.EDITOR_SVG_SOFT_LIMIT_BYTES} 的软闸不在同一量级"
    )
    # 逐族预算要**先于**软闸触发：复杂度闸的全部价值就是不必先付那 12 秒
    assert pb.MESH_CELL_BUDGET * bytes_per_primitive < pb.EDITOR_SVG_SOFT_LIMIT_BYTES


def test_metadata_shape_and_optional_fields():
    m = pb.metadata(svg_bytes=123, mode=pb.MODE_RASTER, reason=pb.REASON_SVG_HARD_LIMIT)
    assert m == {
        "mode": "raster",
        "reason": "svg_hard_limit",
        "svg_bytes": 123,
        "rasterized_artist_count": 0,
    }
    # 没估过就**不出现**，别用 0 冒充「估出来是 0」
    assert "estimated_primitives" not in m
    full = pb.metadata(
        svg_bytes=1,
        mode=pb.MODE_HYBRID,
        reason=pb.REASON_COMPLEXITY_BUDGET,
        rasterized_artist_count=3,
        estimated_primitives=700_000,
        estimated_vertices=2_800_000,
    )
    assert full["estimated_primitives"] == 700_000
    assert full["rasterized_artist_count"] == 3


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "raster-ish", "reason": pb.REASON_NORMAL},
        {"mode": pb.MODE_VECTOR, "reason": "because"},
    ],
)
def test_metadata_rejects_unknown_enum_values(kwargs):
    """协议里的枚举写错了要当场炸，不要静默传给前端一个它不认识的档位。"""
    with pytest.raises(ValueError):
        pb.metadata(svg_bytes=0, **kwargs)


# ------------------------------- 两侧同源 -----------------------------------
def _ts_number(name: str) -> int:
    """从前端镜像里取一个 `export const NAME = <算术表达式>`。"""
    m = re.search(rf"export const {name} = ([^\n]+)\n", MIRROR.read_text(encoding="utf-8"))
    assert m, f"{MIRROR} 里没有 {name}"
    return _eval_arith(ast.parse(m.group(1).strip(), mode="eval").body)


def _eval_arith(node) -> int:
    """只认整数字面量与乘法——不是通用求值器，别让它长成一个。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _eval_arith(node.left) * _eval_arith(node.right)
    raise AssertionError(f"镜像里出现了看不懂的表达式: {ast.dump(node)}")


def test_complexity_budgets_are_deliberately_not_mirrored():
    """复杂度预算**没有前端镜像，这是有意的**。

    前端那份存在的理由只有「二道闸」：后端不返回 `preview` 时维持既有行为、
    返回超大 `svg` 时自己丢掉。它**从不评估复杂度**——artist 图只在 worker
    进程里，前端手上只有裁决结果（`mode`）。凭空镜像过去就是造第二份权威，
    而两份权威一定会漂。

    这条用例是那个不对称的**说明**，不是它的辩护词：哪天前端真的要自己算
    复杂度，它会红，逼着一起把同源看护建起来。
    """
    src = MIRROR.read_text(encoding="utf-8")
    for name in (
        "MESH_CELL_BUDGET",
        "SCATTER_INSTANCE_BUDGET",
        "COLLECTION_VERTEX_BUDGET",
        "TOTAL_VECTOR_PRIMITIVE_BUDGET",
    ):
        assert name not in src, f"{name} 出现在前端镜像里——要么删掉，要么给它建同源看护"


def test_frontend_mirror_carries_the_same_numbers():
    """前端那份是**二道闸**，不是第二份权威——但数字必须是同一个。

    漂了的表现最恶心：后端说 vector（15 MiB，闸内），前端按自己那份更小的闸
    把 svg 丢了 → 画布走 raster，而后端从来不知道。两边都「按自己的规则正确
    工作」，用户看到的是一张莫名其妙变成位图的图。
    """
    assert _ts_number("EDITOR_SVG_SOFT_LIMIT_BYTES") == pb.EDITOR_SVG_SOFT_LIMIT_BYTES
    assert _ts_number("EDITOR_SVG_HARD_LIMIT_BYTES") == pb.EDITOR_SVG_HARD_LIMIT_BYTES


def _ts_union(name: str) -> set[str]:
    src = MIRROR.read_text(encoding="utf-8")
    m = re.search(rf"export type {name} =([^\n]*(?:\n(?!\n)[^\n]*)*?)\n\n", src)
    assert m, f"{MIRROR} 里没有 type {name}"
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def test_frontend_mirror_knows_the_same_modes_and_reasons():
    assert _ts_union("PreviewMode") == set(pb.MODES)
    assert _ts_union("PreviewReason") == set(pb.REASONS)


# ------------------------------- 不变量 3 的证明 -----------------------------
def _probe(hard_limit: int) -> dict:
    out = subprocess.run(
        [WORKER_PY, str(PROBE), "--hard-limit", str(hard_limit)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def sides():
    """先量一张真实的小图有多大，再把闸放在它的两侧。

    阈值取「实测大小 ± 1」而不是拍两个数：探针那张图将来变了（多一条曲线、
    换个 matplotlib 版本），两侧仍然分别落在闸的两边。
    """
    size = _probe(1)["svg_bytes_on_disk"]
    return {"over": _probe(size), "under": _probe(size + 1)}


@pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)
class TestOversizedSvgIsNeverRead:
    """同一根探针，阈值之上与之下各一次。**对照，不是两个样本。**"""

    def test_oversized_svg_is_not_read_at_all(self, sides):
        """**这条是整轮的验收**：闸外那次 `read_text()` 一次都没调。"""
        assert sides["over"]["svg_read_text_calls"] == 0
        assert sides["over"]["all_read_text_calls"] == []

    def test_the_under_limit_side_really_does_read_it(self, sides):
        """对照的另一半。少了它，上面那条在「read_text 压根没被 patch 到」
        的情况下也会绿——那是一条空门禁。"""
        assert sides["under"]["svg_read_text_calls"] == 1
        assert sides["under"]["has_svg_in_response"] is True

    def test_oversized_render_omits_svg_but_stays_a_success(self, sides):
        """超限**不是渲染失败**：manifest / warnings 一样不少，只是没有 svg。"""
        over = sides["over"]
        assert over["has_svg_in_response"] is False
        assert over["manifest_elements"] > 0
        assert over["has_warnings_key"] is True

    def test_preview_metadata_says_why(self, sides):
        assert sides["over"]["preview"]["mode"] == pb.MODE_RASTER
        assert sides["over"]["preview"]["reason"] == pb.REASON_SVG_HARD_LIMIT
        assert sides["over"]["preview"]["svg_bytes"] == sides["over"]["svg_bytes_on_disk"]
        assert sides["under"]["preview"]["mode"] == pb.MODE_VECTOR
        assert sides["under"]["preview"]["reason"] == pb.REASON_NORMAL
