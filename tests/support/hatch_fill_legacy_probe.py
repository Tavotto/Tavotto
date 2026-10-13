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


def main() -> int:
    out = {
        "matplotlib": matplotlib.__version__,
        "has_set_hatchcolor": hasattr(Rectangle, "set_hatchcolor"),
        "user_color": list(USER_COLOR),
        "untouched": run(False),
        "user_edit": run(True),
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
