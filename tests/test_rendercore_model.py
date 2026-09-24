"""RenderCore 的边界守卫（统一实施包 U06，ADR 0059；RC-007、D03）。

三条判据，主语各不相同：

1. **静态 import 图**（`tests/support/importgraph.py`，AST 解析、不 import 产品模块）：纯模型
   那几份模块的外部依赖 ⊆ 标准库；整个 `tavotto/rendercore/` 没有任何模块引用 `pymupdf` /
   `fitz`，也没有边进 `pdfbackend` / worker 侧（层规则在 importgraph 的 `LAYER_RULES`，
   `test_import_architecture.py` 已经跑；这里再把「外部名字」这一维钉住——层规则看的是
   仓库内的边，看不见第三方名字）。
2. **真的 import 一次**：在子进程里把 matplotlib / numpy / pymupdf / fitz / flask / pikepdf /
   fontTools / uharfbuzz / pypdfium2 全部变成 import 就炸，再 import 纯模型的每个模块并编译
   一页——纯模型「不需要」这些包这句话，只有在它们不存在的世界里跑通才算数。
3. **native 适配层的候选包只在函数 / 类内部 import**：模块层 import 会让 `import
   tavotto.rendercore.pdfwriter` 在没装 extra 的机器上直接炸，而产品要的是「装了才有」。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

import importgraph  # noqa: E402

ROOT = importgraph.ROOT
PKG = importgraph.PKG / "rendercore"

MODEL = importgraph.LAYERS["rendercore_model"]
#: 纯模型之外允许的标准库以外的名字：无。仓库内模块（`tavotto.*`）不算外部依赖。
FORBIDDEN_NAMES = frozenset(
    {
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "pymupdf",
        "fitz",
        "flask",
        "werkzeug",
        "pikepdf",
        "fontTools",
        "uharfbuzz",
        "pypdfium2",
        "PIL",
        "lxml",
    }
)


def _stdlib() -> frozenset[str]:
    names = set(sys.stdlib_module_names)
    names.add("__future__")
    return frozenset(names)


@pytest.fixture(scope="module")
def graph() -> importgraph.Graph:
    return importgraph.build()


def test_the_model_layer_is_actually_populated(graph: importgraph.Graph):
    """判据的前提：这些模块真的在图里、真的有外部依赖被记下来（空集合上的「⊆ 标准库」恒真）。"""
    for m in MODEL:
        assert m in graph.nodes, m
    assert any(graph.externals.get(m) for m in MODEL), "纯模型一个外部名字都没记到——图没扫到它们？"


@pytest.mark.parametrize("module", MODEL)
def test_pure_model_modules_only_import_the_standard_library(graph: importgraph.Graph, module: str):
    """RC-007：IR / Plan 不泄漏 native 对象、Flask 上下文或科学栈——从 import 这一层就不许。"""
    external = graph.externals.get(module, set())
    offenders = sorted(external - _stdlib())
    assert offenders == [], f"{module} import 了标准库之外的名字: {offenders}"


def test_no_module_in_rendercore_mentions_pymupdf(graph: importgraph.Graph):
    """D03：新核心零 `import pymupdf`。主语是 rendercore 包里**每一个**模块的外部名字（含 native 适配层）。"""
    bad = {
        node: sorted(names & {"pymupdf", "fitz"})
        for node, names in graph.externals.items()
        if node.startswith("tavotto/rendercore/") and names & {"pymupdf", "fitz"}
    }
    assert bad == {}, bad
    edges = [
        (e.src, e.dst)
        for e in graph.runtime_edges()
        if e.src.startswith("tavotto/rendercore/") and e.dst.startswith("tavotto/pdfbackend/")
    ]
    assert edges == [], f"rendercore 有边进 pdfbackend: {edges}"


def test_native_adapters_import_candidate_packages_lazily():
    """`hbshaper` / `pdfwriter` / `rasterio` / `renderchild` 若存在：模块层不许 import 候选包（那会让没装
    extra 的机器连 `import tavotto.rendercore.pdfwriter` 都炸；`renderchild` 被父进程 import 时更不该拉起
    PDFium——native 只在 child 进程里）；函数 / 类里再 import。"""
    candidates = {"pikepdf", "fontTools", "uharfbuzz", "pypdfium2", "PIL"}
    for name in (
        "hbshaper.py",
        "pdfwriter.py",
        "rasterio.py",
        "renderchild.py",
        "renderhost.py",
        "preview.py",
    ):
        path = PKG / name
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                top += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                top.append(node.module.split(".")[0])
        assert not (set(top) & candidates), (
            f"{name} 在模块层 import 了候选包: {sorted(set(top) & candidates)}"
        )


_BLOCKER = r"""
import sys
BAD = %r
class _Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BAD:
            raise ImportError("纯模型不该 import " + name)
        return None
sys.meta_path.insert(0, _Block())
import tavotto.rendercore
import tavotto.rendercore.ir, tavotto.rendercore.geometry, tavotto.rendercore.typography
import tavotto.rendercore.sources, tavotto.rendercore.plan
sys.path.insert(0, %r)
import fakeface
from tavotto.rendercore import plan, sources
compiled = plan.compile_page(
    100, 80,
    [{"type": "text", "id": "t", "text": "E = mc^{2} 图 ∇", "x_mm": 5, "y_mm": 5, "w_mm": 60, "h_mm": 10, "size_pt": 9},
     {"type": "shape", "id": "s", "shape": "ellipse", "x_mm": 5, "y_mm": 20, "w_mm": 30, "h_mm": 20, "fill": "#ff0000"},
     {"type": "arrow", "id": "a", "x_mm": 5, "y_mm": 50, "w_mm": 30, "h_mm": 10, "start": {"rx": 0, "ry": 0.5}, "end": {"rx": 1, "ry": 0.5}}],
    sources=sources.StaticSourceResolver(%r),
    faces=fakeface.FakeProvider(),
)
print(len(compiled.page.children), sorted(m for m in sys.modules if m.split(".")[0] in BAD))
"""


def _blocker_env() -> dict[str, str]:
    """子解释器的环境：只带 PYTHONPATH / 一条最小 PATH，**stdout / stderr 钉成 UTF-8**。

    不钉的话 Windows 上子进程的 stderr 是 cp1252：traceback 里的中文异常文本会被
    backslashreplace 写成 `\\u7eaf\\u6a21…`，父进程按 UTF-8 解出来的是六个 ASCII 字符，
    `"纯模型不该 import pymupdf" in proc.stderr` 于是只在 Windows 那条腿红
    （PR #458 backend-platforms windows 片 2 / u06-rendercore.yml windows）。
    本机反证：把这里改成 PYTHONIOENCODING=cp1252，macOS 上同一条用例同一句红。
    """
    return {
        "PYTHONPATH": str(ROOT / "src"),
        "PATH": "/usr/bin:/bin",
        "TAVOTTO_NO_TELEMETRY": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }


def test_the_pure_model_imports_and_compiles_with_every_heavy_package_blocked(tmp_path: Path):
    """在 matplotlib / numpy / pymupdf / flask / 候选包全部「不存在」的解释器里：纯模型 import
    得起来、编译得出一页（文字 + 形状 + 箭头）。`sys.modules` 里也不许出现它们。"""
    code = _BLOCKER % (sorted(FORBIDDEN_NAMES), str(SUPPORT), str(tmp_path))
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_blocker_env(),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "3 []", proc.stdout


def test_the_blocker_itself_works():
    """反证的反证：同一个 meta_path 钩子对着一个真的 import 被禁包的模块必须炸——否则上一条
    用例的绿只说明钩子没起作用。主语是一个**真会在模块层 import flask 的产品模块**（`tavotto.app`）：
    U10 之前这里拿旧实现 `pymupdf_backend` 当靶子，它随 PyMuPDF 退役删除（ADR 0072）；契约层
    `tavotto.pdfbackend` 按策略懒装载实现（ADR 0067），import 它本身不拉起任何后端，当不了靶子。"""
    code = _BLOCKER.split("import tavotto.rendercore\n", 1)[0] % (sorted(FORBIDDEN_NAMES),)
    code += "\nimport tavotto.app\n"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_blocker_env(),
        timeout=60,
    )
    assert proc.returncode != 0
    assert "纯模型不该 import flask" in proc.stderr
