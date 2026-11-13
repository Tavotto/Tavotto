"""macOS 系统 Helvetica 的小字号不再让整张图打不开（Nature 的 5–7 pt 写法）。

根因：用户直接 `python fig.py` 走 macosx 后端（Retina 上 figure dpi 200），worker 固定 Agg、dpi 100；
Helvetica.ttc 在像素尺寸很小且带 hinting 时 FreeType 报 `division by zero`，`plt.tight_layout()` 里抛
RuntimeError。修法：Agg 文字入口碰到这类错误时只对那一次调用按无 hinting 重试。
"""

from __future__ import annotations

import os
import types

import pytest

from tavotto.engine import figcapture, pool

HELVETICA = "/System/Library/Fonts/Helvetica.ttc"
try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

SCRIPT = """\
import matplotlib.pyplot as plt
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Helvetica"]
plt.rcParams["font.size"] = 5
fig, ax = plt.subplots(figsize=(3.5, 2.5))
ax.plot([0, 1], [0, 1]); ax.set_xlabel("Time (s)"); ax.set_title("Demo")
plt.tight_layout()
fig.savefig("fig.png", dpi=300)
"""


@pytest.mark.skipif(
    WORKER_PY is None or not os.path.exists(HELVETICA), reason="没有 worker 解释器或系统 Helvetica"
)
def test_real_worker_opens_small_helvetica_script(tmp_path):
    (tmp_path / "fig.py").write_text(SCRIPT, encoding="utf-8")
    w = pool.one_shot("fig.py", str(tmp_path), "__main__")
    try:
        w.ensure_built()
        assert w.last_build_descriptors
    finally:
        pool.discard(w)


def _fake_agg(fail_when_hinted: bool):
    """只带两个入口的假 backend_agg：带 hinting 时抛 FreeType 那类 RuntimeError。"""
    rc = {"text.hinting": "default"}
    calls = []

    class Renderer:
        def _go(self):
            calls.append(rc["text.hinting"])
            if fail_when_hinted and rc["text.hinting"] != "no_hinting":
                raise RuntimeError("failed to load glyph")
            return rc["text.hinting"]

        def get_text_width_height_descent(self, *a):
            return self._go()

        def draw_text(self, *a):
            return self._go()

    mod = types.SimpleNamespace(RendererAgg=Renderer, mpl=types.SimpleNamespace(rcParams=rc))
    return mod, rc, calls


def test_retries_once_unhinted_and_restores_hinting():
    mod, rc, calls = _fake_agg(True)
    figcapture.install_agg_unhinted_glyph_fallback(mod)
    r = mod.RendererAgg()
    assert r.get_text_width_height_descent("x") == "no_hinting"
    assert r.draw_text("x") == "no_hinting"
    assert rc["text.hinting"] == "default"  # 只在重试那一次里放开
    assert calls == ["default", "no_hinting"] * 2


def test_install_is_idempotent_and_other_errors_pass_through():
    mod, rc, calls = _fake_agg(False)
    figcapture.install_agg_unhinted_glyph_fallback(mod)
    first = mod.RendererAgg.draw_text
    figcapture.install_agg_unhinted_glyph_fallback(mod)
    assert mod.RendererAgg.draw_text is first
    assert mod.RendererAgg().draw_text() == "default" and calls == ["default"]

    def boom(self, *a):
        raise RuntimeError("something else")

    mod.RendererAgg._go = boom
    with pytest.raises(RuntimeError, match="something else"):
        mod.RendererAgg().draw_text()


def test_still_failing_unhinted_raises_and_restores():
    mod, rc, _ = _fake_agg(True)

    def always(self, *a):
        raise RuntimeError("FT_Load_Glyph failed with error 0x85")

    mod.RendererAgg._go = always
    figcapture.install_agg_unhinted_glyph_fallback(mod)
    with pytest.raises(RuntimeError, match="FT_Load_Glyph"):
        mod.RendererAgg().draw_text()
    assert rc["text.hinting"] == "default"
