"""Issue #181 的合成复现：确定性、规模、以及它真的把机制复现了出来。

这套用例**不测性能**（那是 `scripts/bench_render.py` + `docs/perf-baseline.md`
的事），它测的是「这个 fixture 还算不算一次可信的复现」：

1. **确定性**——两个独立进程画出来的数据逐位相同。种子写死不等于确定性，
   跨进程一致才是；同一台机器上两次跑不出同一张图的话，修复前后的对比数字
   就只是两个样本，不是对照（[[interleaved-ab-not-sequential]] 那一族）。
2. **规模是真的 primitive 数，不是文件大小**——`pcolormesh` 在 SVG 后端上是
   **一个 quad 一个 `<path>`**，这正是 #181 的机制。哪天 matplotlib 换成
   一张 `<image>`（或者谁给 fixture 加了 `rasterized=True`），文件是小了，
   但复现的东西也没了，这条用例必须当场红。
3. **它在 Tavotto 这条链路上跑得通**——注册表认得出、worker 画得出 manifest
   与预览 SVG，而不只是一个能单独运行的 matplotlib 脚本。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
用例一律用**小 n**（默认 470 是给基线用的，一跑就是一百多 MB）。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from support import large_figures
from tavotto.engine import pool

REPO = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO / "tests" / "fixtures" / "large_figures"
SCRIPT_NAME = "issue_181_large_pcolormesh.py"
ENTRY = "main"
STEM = "Issue181_large_pcolormesh"

#: 用例用的边长。小到几秒内跑完，又大到 quad 数远超其它一切 primitive
#: （n=24 → 3×576=1728 个 quad，坐标轴 / 刻度 / 图例合计七十几个）。
TEST_N = 24

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


def _digest_in_subprocess(n: int) -> str:
    """在一个**独立进程**里算数据摘要。同进程调两次证明不了确定性——
    Generator 的状态、模块级缓存、乃至一次 import 的副作用都在同一份内存里。"""
    out = subprocess.run(
        [
            WORKER_PY,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "import issue_181_large_pcolormesh as fx;"
            "print(fx.data_digest(int(sys.argv[2])))",
            str(FIXTURE_DIR),
            str(n),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return out.stdout.strip()


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    """把 fixture 摊成一个真正的图库目录（脚本 + 注册表 + 占位产物）。

    走的是**基准脚本用的同一个摊开函数**（`support/large_figures.py`）：
    那条路径只有人手工跑基线时才会被用到，不在这里过一遍的话，它坏了没人
    知道——而它一坏，#181 的复现就跑不起来。
    """
    return large_figures.materialize(tmp_path_factory.mktemp("issue181-figures"), python=WORKER_PY)


@pytest.fixture(scope="module")
def rendered(library):
    """小 n 下真跑一次：manifest + 预览 SVG 文本。"""
    prev = os.environ.get("TAVOTTO_ISSUE181_MESH_N")
    os.environ["TAVOTTO_ISSUE181_MESH_N"] = str(TEST_N)
    try:
        worker = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
        try:
            worker.ensure_built()
            resp = worker.override(STEM, [], inline_svg=True)
            yield resp
        finally:
            pool.discard(worker)
    finally:
        if prev is None:
            os.environ.pop("TAVOTTO_ISSUE181_MESH_N", None)
        else:
            os.environ["TAVOTTO_ISSUE181_MESH_N"] = prev


def test_fixture_is_deterministic_across_processes():
    """两个独立进程，同一个 n → 同一份数据。"""
    assert _digest_in_subprocess(TEST_N) == _digest_in_subprocess(TEST_N)


def test_mesh_size_knob_actually_changes_the_figure():
    """规模旋钮不是装饰：换了 n 就是另一张图（否则基线的「规模」一栏是假的）。"""
    assert _digest_in_subprocess(TEST_N) != _digest_in_subprocess(TEST_N + 1)


def test_registry_points_at_the_script_and_stem():
    reg = json.loads((FIXTURE_DIR / "tavotto_registry.json").read_text(encoding="utf-8"))
    entry = reg["scripts"][SCRIPT_NAME]
    assert entry["entry"] == ENTRY
    assert entry["stems"] == [STEM]


def test_the_fixture_reproduces_one_svg_path_per_quad(rendered):
    """#181 的机制：`pcolormesh` 在 SVG 后端上一个 cell 一个 `<path>`。

    这条不是「文件有多大」的近似——文件大小随 colormap、精度、matplotlib 版本
    浮动，而**是不是每个 cell 一个 path** 才是问题本身。谁把 fixture 改成
    `rasterized=True`（或 matplotlib 改成整块 `<image>`）都会让它当场红。
    """
    # 用例前提：**这个规模刻意落在复杂度预算之内**（576 cell/格 ≪ 20 000），
    # 所以预览仍然是纯矢量，「一个 cell 一个 `<path>`」这条机制才看得见。
    # 哪天有人把 `MESH_CELL_BUDGET` 调到 576 以下，这条会先红在这里而不是在
    # 下面那句「path 太少」上——后者会把人指向 fixture，而原因在预算。
    assert rendered["preview"]["mode"] == "vector", rendered["preview"]
    quads = 3 * TEST_N * TEST_N
    paths = rendered["svg"].count("<path")
    assert paths >= quads, f"只有 {paths} 个 path，少于 {quads} 个 quad——复现的机制没了"
    # 其余 primitive（spine / tick / 图例 / 曲线）是常数级，不该淹没 mesh
    assert paths < quads * 1.2


def test_the_fixture_keeps_normal_vector_semantics(rendered):
    """第四格与色条必须在 manifest 里可编辑——hybrid preview 之后它们还得在。

    只有 mesh 的图问不出「hybrid 有没有把该留的留住」，所以这条钉的是 fixture
    的**结构**，不是引擎的能力。
    """
    roles = [el.get("role") for el in rendered["manifest"]["elements"]]
    assert roles.count("collection") == 3, roles  # 三块 QuadMesh，各是一个 artist
    assert roles.count("axes") >= 4, roles  # 三格 mesh + 一格普通曲线
    assert roles.count("line") == 2, roles  # 第四格那两条普通曲线
    assert "legend" in roles, roles
    assert "colorbar" in roles, roles


def test_the_materialized_library_carries_a_discoverable_artifact(library):
    """摊出来的图库要能被素材扫描认出：产物 + 脚本 + 注册表三样齐全。

    少了 PDF 的话 `/api/panels` 里根本没有这个面板，基准脚本会报「注册表为
    空」——那是一条指向完全错误方向的错误信息（注册表明明是对的）。
    """
    assert (library / f"{STEM}.pdf").stat().st_size > 0
    assert (library / SCRIPT_NAME).is_file()
    assert (library / "tavotto_registry.json").is_file()


def test_running_the_fixture_standalone_does_not_write_into_the_repo():
    """产物一律摊到临时目录；仓库的 fixture 目录里一个都不该有。"""
    leftovers = [p.name for p in FIXTURE_DIR.iterdir() if p.suffix in (".svg", ".pdf", ".png")]
    assert leftovers == [], f"fixture 目录里有产物，别提交它们: {leftovers}"


if __name__ == "__main__":  # pragma: no cover — 手工探测用
    sys.exit(pytest.main([__file__, "-v"]))
