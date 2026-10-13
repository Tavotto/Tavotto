"""在 **worker 解释器**里跑的探针：≤3.10 的 fill 撤销写回花纹色，屏障之间用户改过色之后不许写回旧值（#951）。

≤3.10 没有 `set_hatchcolor`，用户在 native 屏障之间改花纹色只有一条路：直接写 `Patch._hatch_color`
（或经 `set_edgecolor` 间接改，但那会让 `_original_edgecolor` 不再是 None，写回根本不触发）。
探针如实报事实，判定归调用方 `tests/test_hatch_fill_legacy_staleness.py`。
"""

from __future__ import annotations

import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "src", "tavotto", "engine"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

import manifest as M  # noqa: E402
import overrides as O  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

GID = "axes_0.patches_0"
USER_COLOR = (0.0, 0.5, 0.0, 1.0)


def build():
    fig, ax = plt.subplots(figsize=(3, 2))
    with plt.rc_context({"hatch.color": "red"}):
        ax.add_patch(Rectangle((0.1, 0.1), 0.5, 0.5, facecolor="#cccccc", hatch="//"))
    state = O.FigState(fig)
    M.instrument(state)
    M.build_manifest(state, "P")
    return state


def rgba(a):
    return [round(float(x), 4) for x in a._hatch_color]  # noqa: SLF001


def run(user_edit):
    state = build()
    art = state.resolve(GID)
    created = rgba(art)
    fill_off = [{"gid": GID, "prop": "fill", "value": False}]
    warns = O.apply(state, fill_off)
    during = rgba(art)
    if user_edit:  # native 屏障之间，用户的 Python 直接改花纹色
        art._hatch_color = USER_COLOR  # noqa: SLF001
    warns += O.apply(state, [])  # 撤销 fill
    return {
        "warnings": warns,
        "created": created,
        "during": during,
        "after_clear": rgba(art),
        "fill_after_clear": bool(art.get_fill()),
    }


def run_unrelated_between():
    """Codex #993 r4259898020：fill 编辑 → 撤销 fill、另有一条无关 override 留着 → 屏障间用户改色
    → 再 apply（仍无 fill 编辑）→ 再切 fill 再撤销：花纹色应是用户的值。"""
    state = build()
    art = state.resolve(GID)
    other = {"gid": GID, "prop": "linewidth", "value": 2.0}
    warns = O.apply(state, [{"gid": GID, "prop": "fill", "value": False}, other])
    warns += O.apply(state, [other])  # 撤销 fill，无关 override 留着
    art._hatch_color = USER_COLOR  # noqa: SLF001 — 屏障之间用户改色
    warns += O.apply(state, [other])  # 这一批没有 fill 编辑
    warns += O.apply(state, [{"gid": GID, "prop": "fill", "value": False}, other])
    warns += O.apply(state, [other])
    return {"warnings": warns, "after_clear": rgba(art)}


def main() -> int:
    out = {
        "matplotlib": matplotlib.__version__,
        "has_set_hatchcolor": hasattr(Rectangle, "set_hatchcolor"),
        "user_color": list(USER_COLOR),
        "untouched": run(False),
        "user_edit": run(True),
        "unrelated_between": run_unrelated_between(),
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
