"""生成 / 校对 `tests/golden/chart_model_vectors.json`。

跨语言看护向量：Python（tests/test_chart_model_vectors.py）与前端
（web/src/types/chartModel.golden.test.ts）各跑一遍同一份，断言两侧对
同一模型/绑定给出相同的「接受 / 拒绝 + 同一错误码」。

    python scripts/gen_chart_model_vectors.py            # 校对（有分歧就非零退出）
    python scripts/gen_chart_model_vectors.py --write    # 按 Python 侧重新生成
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tavotto.engine import chartmodel as cm  # noqa: E402

OUT = ROOT / "tests" / "golden" / "chart_model_vectors.json"


def base_model() -> dict:
    columns = {"x": [0.0, 1.0, 2.0], "y1": [0.0, 1.0, 4.0]}
    return {
        "schema": cm.MODEL_SCHEMA,
        "rev": 1,
        "axes": [
            {
                "id": "ax1",
                "rect": [51.5, 26.1, 297.6, 183.7],
                "xlim": [0.0, 10.0],
                "ylim": [0.0, 100.0],
                "xscale": "linear",
                "yscale": "linear",
                "invert_x": False,
                "invert_y": False,
            }
        ],
        "series": [
            {
                "id": "s1",
                "kind": "line",
                "axes": "ax1",
                "node": "line2d_13",
                "x": "x",
                "y": "y1",
                "style": {"color": "#1f77b4", "linewidth": 2.0},
            }
        ],
        "dataset": {"columns": columns},
        "scene_digest": "a" * 64,
        "data_digest": cm.digest_of({"columns": columns}),
        "engine": {"matplotlib": "3.11.2", "numpy": "2.1.0", "tavotto": "0.19.2"},
    }


def case(name: str, model, expect: str | None, binding=None) -> dict:
    return {"name": name, "model": model, "binding": binding, "expect": expect}


def build_vectors() -> list[dict]:
    good = base_model()
    binding = {
        "model_rev": 1,
        "scene_digest": good["scene_digest"],
        "entries": [{"object": "series:s1", "node": "line2d_13"}],
    }
    vectors = [case("valid-minimal", good, "ok", binding)]

    def mutated(name: str, mutate, code: str, with_binding=None) -> dict:
        m = base_model()
        mutate(m)
        return case(name, m, code, with_binding)

    def set_schema_v2(m):
        m["schema"] = "tavotto.chart-model/2"

    def add_unknown_key(m):
        m["script"] = "import os"

    def set_infinite(m):
        m["axes"][0]["xlim"] = [0.0, 10**400]

    def duplicate_series(m):
        m["series"].append(dict(m["series"][0]))

    def unknown_axes_ref(m):
        m["series"][0]["axes"] = "ghost"

    def unknown_column(m):
        m["series"][0]["y"] = "ghost"

    def bad_data_digest(m):
        m["data_digest"] = "0" * 64

    def bad_scale(m):
        m["axes"][0]["xscale"] = "squircle"

    def empty_range(m):
        m["axes"][0]["ylim"] = [5.0, 5.0]

    vectors += [
        mutated("reject-schema-major", set_schema_v2, "chart_model_unsupported"),
        mutated("reject-unknown-key", add_unknown_key, "chart_model_invalid"),
        mutated("reject-infinite", set_infinite, "chart_model_invalid"),
        mutated("reject-duplicate-series", duplicate_series, "chart_duplicate_id"),
        mutated("reject-unknown-axes-ref", unknown_axes_ref, "chart_model_invalid"),
        mutated("reject-unknown-column", unknown_column, "chart_data_invalid"),
        mutated("reject-data-digest-mismatch", bad_data_digest, "chart_data_changed"),
        mutated("reject-bad-scale", bad_scale, "chart_model_invalid"),
        mutated("reject-empty-range", empty_range, "chart_model_invalid"),
    ]

    stale = {
        "model_rev": 2,
        "scene_digest": good["scene_digest"],
        "entries": [{"object": "series:s1", "node": "line2d_13"}],
    }
    mismatched = {
        "model_rev": 1,
        "scene_digest": "b" * 64,
        "entries": [{"object": "series:s1", "node": "line2d_13"}],
    }

    def _categorical_bar_model():
        m = base_model()
        categorical_bar(m)
        return m

    def categorical_bar(m):
        m["axes"][0]["kind"] = "categorical"
        m["axes"][0]["categories"] = ["A", "B", "C", "D"]
        m["axes"][0]["xlim"] = [-0.5, 3.5]
        m["series"][0]["kind"] = "bar"
        m["series"][0]["yerr"] = "err"
        m["dataset"]["columns"]["cat"] = ["A", "B", "C", "D"]
        m["dataset"]["columns"]["err"] = [1.0, 2.0, 3.0, 4.0]
        m["series"][0]["x"] = "cat"
        m["data_digest"] = cm.digest_of({"columns": m["dataset"]["columns"]})

    def log_negative_range(m):
        m["axes"][0]["yscale"] = "log"
        m["axes"][0]["ylim"] = [-5.0, 105.0]

    vectors += [
        case("valid-categorical-bar", _categorical_bar_model(), "ok", binding),
        mutated("reject-log-negative-range", log_negative_range, "chart_data_invalid"),
        case("reject-binding-stale", base_model(), "chart_binding_stale", stale),
        case("reject-binding-scene-mismatch", base_model(), "chart_source_mismatch", mismatched),
    ]
    return vectors


def main() -> int:
    doc = {
        "schema": 1,
        "comment": "生成物：ChartModel/Binding 跨语言看护向量。唯一产生者 "
        "scripts/gen_chart_model_vectors.py；手改无效。pytest "
        "(tests/test_chart_model_vectors.py) 与 vitest "
        "(web/src/types/chartModel.golden.test.ts) 各跑一遍同一份。",
        "vectors": build_vectors(),
    }
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    if "--write" in sys.argv:
        OUT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {OUT}")
        return 0
    if not OUT.exists():
        print(f"missing {OUT}; run with --write first", file=sys.stderr)
        return 1
    current = OUT.read_text(encoding="utf-8")
    if current != text:
        print(
            "golden vectors out of date; run scripts/gen_chart_model_vectors.py --write",
            file=sys.stderr,
        )
        return 1
    print(f"{len(doc['vectors'])} vectors verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
