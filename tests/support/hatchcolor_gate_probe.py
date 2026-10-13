"""在 **worker 解释器**里跑的探针：manifest 发不发 `hatchcolor` 字段，看的是 artist 有没有 `set_hatchcolor`（#874）。

用两个替身类（一个有 setter、一个没有）直接问 `manifest._hatch_style_fields`；判定归调用方
`tests/test_hatchcolor_editing.py::test_manifest_gate_is_the_real_setter_not_the_version_string`。
"""

from __future__ import annotations

import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "src", "tavotto", "engine"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")

import manifest as M  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


class Base:
    _hatch_color = "edge"

    def get_hatch_linewidth(self):
        return 1.0

    def set_hatch_linewidth(self, v):
        pass

    def get_hatchcolor(self):
        return (0.0, 0.5, 0.0, 1.0)


class WithSetter(Base):
    def set_hatchcolor(self, v):
        pass


class WithoutSetter(Base):
    pass


def _field(pt):
    return next((f for f in M._hatch_style_fields(pt) if f["prop"] == "hatchcolor"), None)


def main() -> int:
    explicit = WithSetter()
    explicit._hatch_color = (0.0, 0.0, 1.0, 1.0)
    print(
        json.dumps(
            {
                "with_setter": _field(WithSetter()),
                "without_setter": _field(WithoutSetter()),
                "explicit_follows_edge": _field(explicit)["follows_edge"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
