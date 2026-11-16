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

# 校对结果会被CI/父进程收集；Windows管道也必须与仓库脚本合同一致。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

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

    def _heatmap_model():
        m = base_model()
        heatmap(m)
        return m

    def heatmap(m):
        m["series"][0]["kind"] = "image"
        m["series"][0]["grid"] = "g1"
        m["series"][0]["cbar_node"] = "axes_2"
        m["series"][0]["style"] = {"cmap": "viridis", "vmin": 0.0, "vmax": 1.0}
        m["series"][0].pop("x")
        m["series"][0].pop("y")
        m["dataset"]["grids"] = {"g1": {"values": [[0.1, 0.2], [0.3, 0.4]]}}
        m["data_digest"] = cm.digest_of(
            {"columns": m["dataset"]["columns"], "grids": m["dataset"]["grids"]}
        )

    def image_without_grid(m):
        m["series"][0]["kind"] = "image"
        m["series"][0].pop("x")
        m["series"][0].pop("y")

    def log_negative_range(m):
        m["axes"][0]["yscale"] = "log"
        m["axes"][0]["ylim"] = [-5.0, 105.0]

    vectors += [
        case("valid-heatmap-image", _heatmap_model(), "ok", binding),
        mutated("reject-image-without-grid", image_without_grid, "chart_data_invalid"),
        case("valid-categorical-bar", _categorical_bar_model(), "ok", binding),
        mutated("reject-log-negative-range", log_negative_range, "chart_data_invalid"),
        case("reject-binding-stale", base_model(), "chart_binding_stale", stale),
        case("reject-binding-scene-mismatch", base_model(), "chart_source_mismatch", mismatched),
    ]

    # N4 legend 合同（binding.legend）：模型本身合法，判据全部落在
    # validate_binding 里——"binding:<code>" 前缀表示该形状。
    def legend_binding(model, legend):
        return {
            "model_rev": model["rev"],
            "scene_digest": model["scene_digest"],
            "entries": [
                {"object": f"series:{sr['id']}", "node": sr["node"]} for sr in model["series"]
            ],
            "legend": legend,
        }

    def two_series_model():
        m = base_model()
        m["series"].append(
            {
                "id": "s2",
                "kind": "line",
                "axes": "ax1",
                "node": "line2d_14",
                "x": "x",
                "y": "y1",
                "style": {"color": "#ff7f0e", "linewidth": 2.0},
            }
        )
        return m

    def legend_case(name, model, legend, expect):
        return case(name, model, expect, legend_binding(model, legend))

    def legend_item(entries=None, **overrides):
        item = {
            "id": "leg1",
            "node": "legend_a",
            "rect": [10.0, 10.0, 40.0, 20.0],
            "entries": entries or [],
        }
        item.update(overrides)
        return item

    follow_custom = [
        {"index": 0, "label": "series one", "series": "s1"},
        {"index": 1, "label": "note only"},
    ]
    vectors += [
        legend_case("valid-legend-follow-custom", base_model(), [legend_item(follow_custom)], "ok"),
        # 显式指认可重复：两条各指认一个系列（同名同色歧义由显式消解）
        legend_case(
            "valid-legend-two-follows",
            two_series_model(),
            [
                legend_item(
                    [{"index": 0, "label": "a", "series": "s1"}], id="leg1", node="legend_a"
                ),
                legend_item(
                    [{"index": 0, "label": "b", "series": "s2"}], id="leg2", node="legend_b"
                ),
            ],
            "ok",
        ),
        legend_case(
            "reject-legend-duplicate-id",
            base_model(),
            [
                legend_item(follow_custom),
                legend_item(node="legend_b"),
            ],
            "binding:chart_duplicate_id",
        ),
        legend_case(
            "reject-legend-node-collides-entry",
            base_model(),
            [legend_item(follow_custom, node="line2d_13")],
            "binding:chart_duplicate_id",
        ),
        legend_case(
            "reject-legend-duplicate-index",
            base_model(),
            [legend_item([follow_custom[0], dict(follow_custom[0])])],
            "binding:chart_duplicate_id",
        ),
        legend_case(
            "reject-legend-negative-index",
            base_model(),
            [legend_item([dict(follow_custom[0], index=-1)])],
            "binding:chart_model_invalid",
        ),
        legend_case(
            "reject-legend-empty-label",
            base_model(),
            [legend_item([dict(follow_custom[0], label="")])],
            "binding:chart_model_invalid",
        ),
        legend_case(
            "reject-legend-unknown-series",
            base_model(),
            [legend_item([dict(follow_custom[0], series="ghost")])],
            "binding:chart_model_invalid",
        ),
        legend_case(
            "reject-legend-nonpositive-rect",
            base_model(),
            [legend_item(follow_custom, rect=[10.0, 10.0, 0.0, 20.0])],
            "binding:chart_model_invalid",
        ),
        legend_case(
            "reject-legend-unknown-key",
            base_model(),
            [legend_item(follow_custom, binding="follow_source")],
            "binding:chart_model_invalid",
        ),
        legend_case("reject-legend-empty-list", base_model(), [], "binding:chart_model_invalid"),
        legend_case(
            "reject-legend-without-entries",
            base_model(),
            [legend_item([])],
            "binding:chart_model_invalid",
        ),
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
