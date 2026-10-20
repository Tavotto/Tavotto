"""PNG 采用失败原因的闭集：后端发出的 reason ⊆ 前端 `ArtifactReason`（ADR 0139 PR1）。

判据的主语：**后端源码里 `ArtifactSourceUnavailable(...)` / `ArtifactContextError(...)` 构造调用的 reason
实参**（AST 取字面量；条件表达式里的两支也算；变量实参读不出，只许出现在已登记的转发点）。
前端那一侧是 `web/src/lib/artifactReason.ts` 的 `export type ArtifactReason` 联合——reason → 可恢复性 →
文案的分类表只有那一处。后端新增一个 reason 而前端没登记，用户就只会看到兜底文案、也拿不到出路。

反向（前端多出来的）只有四个本地发的原因：它们不由后端发出，写在 `FRONTEND_ONLY` 里，漂了就红。
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.support.tsconst import exported_string_union

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "tavotto"
REASON_TS = ROOT / "web" / "src" / "lib" / "artifactReason.ts"

_CTORS = {"ArtifactSourceUnavailable", "ArtifactContextError"}

#: 前端本地发的 reason（`ArtifactValidationError` / 本地前置判定），后端不发
FRONTEND_ONLY = {
    "frame_changed",
    "invalid_guard",
    "writeback_not_supported",
    "unsupported_response",
}

#: reason 实参是变量的转发点（值来自别处已被上面收集的字面量）：文件 → 个数。个数变了说明有新的读不出的调用
FORWARDING_SITES = {
    # 第三处是 `_refuse_adopted_source(reason=…)`（服务端写回闸，ADR 0139 PR2），转发调用方给的 reason
    "app.py": 3,
    "engine/artifactcontext.py": 2,
}


#: PR2 后端才会发的 reason：前端先登记（闭集是后端的超集即可），PR2 落地后它们进入后端集合，这里不必改
PR2_REASONS = {
    "artist_profile",
    "policy_disabled",
    "confirmation_required",
    "reconstruction_changed",
    "adoption_store_full",
}


def _backend_reasons() -> tuple[set[str], dict[str, int]]:
    reasons: set[str] = set()
    forwarded: dict[str, int] = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if name not in _CTORS:
                continue
            arg = (
                node.args[0]
                if node.args
                else next((k.value for k in node.keywords if k.arg == "reason"), None)
            )
            assert arg is not None, f"{path}:{node.lineno} 构造调用没有 reason 实参"
            if isinstance(arg, ast.Name):
                key = str(path.relative_to(SRC)).replace("\\", "/")
                forwarded[key] = forwarded.get(key, 0) + 1
                continue
            literals = [
                n.value
                for n in ast.walk(arg)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            assert literals, f"{path}:{node.lineno} reason 实参读不出字面量，也不是已登记的转发变量"
            reasons.update(literals)
    return reasons, forwarded


def test_every_backend_reason_is_classified_by_the_frontend() -> None:
    backend, _ = _backend_reasons()
    frontend = set(exported_string_union(REASON_TS.read_text(encoding="utf-8"), "ArtifactReason"))
    assert backend, "没从后端源码里收到任何 reason——收集器空转"
    assert (
        "unsupported_render_state" in backend and "source_mismatch" in backend
    )  # 抽样：收集器真读到了
    missing = backend - frontend
    assert not missing, (
        f"后端发出的 reason 前端没有分类：{sorted(missing)}（改 web/src/lib/artifactReason.ts 的联合与表）"
    )


def test_frontend_only_reasons_are_exactly_the_locally_emitted_ones() -> None:
    backend, _ = _backend_reasons()
    frontend = set(exported_string_union(REASON_TS.read_text(encoding="utf-8"), "ArtifactReason"))
    assert FRONTEND_ONLY <= frontend
    assert not (FRONTEND_ONLY & backend)
    assert frontend - backend - PR2_REASONS == FRONTEND_ONLY


def test_reason_forwarding_sites_are_the_registered_ones() -> None:
    _, forwarded = _backend_reasons()
    assert forwarded == FORWARDING_SITES
