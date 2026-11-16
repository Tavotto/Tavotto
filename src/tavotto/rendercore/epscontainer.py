"""固定 Matplotlib EPS 容器；不排字、不重新绘图、不启动外部进程。

输入只来自 ``originaleps.PreparedScenePS.to_payload``，不是 HTTP/用户 PS 接口。
摘要用于跨 worker 的冻结身份核对，不是把任意 PostScript 变安全的沙盒。
模块 import 仅依赖标准库；用户 worker 不需要 RenderCore 的 native extras。
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re

from ..engine.brand import PRODUCT_NAME
from .svgcheck import SVGBudget

SCHEMA = "scene-ps/1"
# 同一个静态矢量预算；不是另一份可独立调整的任意 MB 阈值。
MAX_BYTES = SVGBudget().max_bytes
MAX_EXPANDED_COST = SVGBudget().max_expanded_elements
_FIELDS = {
    "schema",
    "ps_ascii",
    "width_pt",
    "height_pt",
    "source_svg_sha256",
    "ps_sha256",
    "expanded_cost",
}
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class SceneEPSError(RuntimeError):
    def __init__(self, code: str, message: str, params: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.params = params or {}


def validate_payload(payload: dict) -> dict:
    """闭式内部 payload；完整 JSON、PS、展开执行成本均受既有预算限制。"""
    if (
        not isinstance(payload, dict)
        or payload.keys() != _FIELDS
        or payload.get("schema") != SCHEMA
    ):
        raise SceneEPSError("native_eps_invalid_payload", "Invalid prepared scene schema")
    for name in ("width_pt", "height_pt"):
        value = payload[name]
        try:
            valid = type(value) in (int, float) and math.isfinite(value) and value > 0
        except OverflowError:
            valid = False
        if not valid:
            raise SceneEPSError("native_eps_invalid_payload", "Invalid physical page size")
    cost = payload["expanded_cost"]
    if type(cost) is not int or not 0 <= cost <= MAX_EXPANDED_COST:
        raise SceneEPSError("native_eps_budget", "Expanded scene exceeds vector budget")
    for name in ("source_svg_sha256", "ps_sha256"):
        if not isinstance(payload[name], str) or not _SHA.fullmatch(payload[name]):
            raise SceneEPSError("native_eps_invalid_payload", "Invalid frozen scene digest")
    ps = payload["ps_ascii"]
    if not isinstance(ps, str) or len(ps) > MAX_BYTES:
        raise SceneEPSError("native_eps_budget", "Prepared PS exceeds vector byte budget")
    try:
        ps_bytes = ps.encode("ascii")
    except UnicodeEncodeError as exc:
        raise SceneEPSError("native_eps_invalid_payload", "Prepared PS must be ASCII") from exc
    if hashlib.sha256(ps_bytes).hexdigest() != payload["ps_sha256"]:
        raise SceneEPSError("native_eps_invalid_payload", "Prepared PS digest mismatch")
    # PS escaping/newlines and closed metadata count too, not just len(ps).
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    if len(encoded) > MAX_BYTES:
        raise SceneEPSError("native_eps_budget", "Prepared request exceeds vector byte budget")
    return payload


class _BoundedEPS(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_BYTES:
            raise SceneEPSError("native_eps_budget", "EPS exceeds vector byte budget")
        return super().write(data)


def pack_prepared_eps(payload: dict) -> bytes:
    """可信已排字 PS → EPS bytes；只能在既有固定 owned worker 中调用。"""
    payload = validate_payload(payload)
    try:
        import matplotlib as mpl
        from matplotlib.artist import Artist
        from matplotlib.backends.backend_ps import FigureCanvasPS, RendererPS
        from matplotlib.figure import Figure
    except ImportError as exc:
        raise SceneEPSError("native_eps_backend", "Matplotlib EPS backend is unavailable") from exc

    class SceneArtist(Artist):
        def draw(self, renderer):
            ps = getattr(renderer, "_renderer", renderer)
            stream = getattr(ps, "_pswriter", None)
            if not isinstance(ps, RendererPS) or not callable(getattr(stream, "write", None)):
                raise SceneEPSError("native_eps_backend", "Unsupported Matplotlib PS renderer seam")
            stream.write(payload["ps_ascii"])

    # Covers creation and draw, not merely savefig. Hostile rc must not turn this
    # fixed, already laid out scene into TeX/distiller/tight-bbox execution.
    with mpl.rc_context(
        {
            "text.usetex": False,
            "ps.usedistiller": None,
            "ps.useafm": False,
            "ps.fonttype": 42,
            "savefig.bbox": None,
            "savefig.pad_inches": 0,
        }
    ):
        figure = Figure(
            figsize=(payload["width_pt"] / 72, payload["height_pt"] / 72),
            dpi=72,
            frameon=False,
        )
        figure.add_artist(SceneArtist())
        out = _BoundedEPS()
        FigureCanvasPS(figure).print_eps(out, metadata={"Creator": f"{PRODUCT_NAME} scene EPS"})
        return out.getvalue()
