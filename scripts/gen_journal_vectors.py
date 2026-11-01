#!/usr/bin/env python3
"""生成 / 校对 `tests/golden/journal_vectors.json`。

期刊库有**两份实现**：Python 的 `engine/journals.py`（MCP / 后端）与 TypeScript 的
`web/src/lib/journals.ts`（画布与选择器）。让它们不分叉的办法同预检向量：**同一份
向量，两边各跑一遍**（`tests/test_journal_catalog.py` / `journals.test.ts`）。

向量里的期刊是**测试夹具**（fx-* ，url 是 example.invalid），不是任何真实期刊，数值
也不代表任何真实要求。

    python scripts/gen_journal_vectors.py            # 校对（有分歧就非零退出）
    python scripts/gen_journal_vectors.py --write    # 按 Python 侧重新生成

Python 是参考实现：`--write` 之后必须人工读一遍 diff，再让 vitest 也绿。纯标准库。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tavotto.engine import journals, profiles  # noqa: E402

OUT = ROOT / "tests" / "golden" / "journal_vectors.json"


def fixture_catalog() -> dict:
    """夹具期刊库：两个出版社、五本虚构期刊。

    覆盖：继承 / 期刊覆盖出版社 / 期刊自带出处 / 期刊引用出版社出处 / 出版社与期刊都无规则 /
    info 展示项 / 限定词与阶段 / mark+derivation / 期刊 unset 出版社的一条 / 严重度抬升
    （含显式 severity 优先）/ 非 ASCII 别名。
    """
    return {
        "schema": 1,
        "catalog_rev": 1,
        "publishers": {
            "fx-press": {
                "label": "Fixture Press",
                "sources": {
                    "s1": {
                        "url": "https://example.invalid/fx-press/artwork",
                        "kind": "html",
                        "retrieved": "2000-01-01",
                        "archive_url": "https://example.invalid/archive/fx-press/artwork",
                    },
                    "s2": {
                        "url": "https://example.invalid/fx-press/guide.pdf",
                        "kind": "pdf",
                        "retrieved": "2000-01-01",
                        "page": 12.0,
                    },
                    # 只属于 fx-zeta 的单刊样页：别的期刊引用它是错
                    "s7": {
                        "url": "https://example.invalid/fx-press/zeta-sample",
                        "kind": "html",
                        "retrieved": "2000-03-03",
                        "coverage": "single_journal_sample",
                        "journal": "fx-zeta",
                    },
                },
                "rules": {
                    "widths_mm.single": {
                        "value": 85,
                        "src": "s1",
                        "quote": "FIXTURE single column 85",
                        "anchor": "#:~:text=FIXTURE%20single",
                    },
                    "widths_mm.double": {
                        "value": 175,
                        "src": "s1",
                        "quote": "FIXTURE double column 175",
                    },
                    "min_raster_dpi": {
                        "value": 600,
                        "src": "s2",
                        "quote": "FIXTURE at least 600 dpi",
                        "qualifier": "min",
                        "stage": "submission",
                    },
                    "line_widths_pt": {"value": [0.5, 1.0], "src": "s2", "quote": "FIXTURE lines"},
                    "severity.page-width": {
                        "value": "error",
                        "src": "s1",
                        "quote": "FIXTURE width is mandatory",
                    },
                    "info.max_height_mm": {
                        "value": 230,
                        "src": "s1",
                        "quote": "FIXTURE height must not exceed 23 cm",
                        "qualifier": "max",
                        "mark": "23 cm",
                        "derivation": "23 cm -> 230 mm",
                        "snapshot": {"file": SNAPSHOT_FILE, "width": 520, "height": 150},
                    },
                },
            },
            "fx-bare": {"label": "Fixture Bare"},
            # 期刊自有栏位（Science 式 1 / 2 / 3 栏），不硬套单栏 / 双栏
            "fx-cols": {
                "label": "Fixture Columns Press",
                "sources": {
                    "s11": {
                        "url": "https://example.invalid/fx-cols/figures",
                        "kind": "html",
                        "retrieved": "2000-04-04",
                    }
                },
                "rules": {
                    "widths_mm.single": {
                        "value": 999,
                        "src": "s11",
                        "quote": "FIXTURE ignored: page_columns wins",
                    },
                    "page_columns": {
                        "value": [
                            {
                                "id": "col1",
                                "label": {"zh": "1 栏", "en": "1 column"},
                                "width_mm": 57,
                                "qualifier": "exact",
                            },
                            {
                                "id": "col2",
                                "label": {"zh": "2 栏", "en": "2 columns"},
                                "width_mm": 121,
                            },
                            {
                                "id": "col3",
                                "label": {"zh": "3 栏", "en": "3 columns"},
                                "width_mm": 184,
                                "qualifier": "exact",
                            },
                        ],
                        "src": "s11",
                        "quote": "FIXTURE usually 5.7 cm, 12.1 cm or 18.4 cm",
                    },
                },
            },
            # 建议性提示：正文 7 pt（recommended）+ 上下标 6 pt，不是判据
            "fx-adv-press": {
                "label": "Fixture Advice Press",
                "sources": {
                    "s12": {
                        "url": "https://example.invalid/fx-adv/sizing",
                        "kind": "html",
                        "retrieved": "2000-05-05",
                    }
                },
                "rules": {
                    "min_effective_font_size_pt": {
                        "value": 7,
                        "src": "s12",
                        "quote": "FIXTURE 7 pt for normal text",
                        "qualifier": "recommended",
                    },
                    "min_script_font_size_pt": {
                        "value": 6,
                        "src": "s12",
                        "quote": "FIXTURE no smaller than 6 pt for subscripts",
                        "qualifier": "min",
                    },
                },
            },
        },
        "journals": {
            "fx-alpha": {
                "label": "Fixture Alpha",
                "publisher": "fx-press",
                "issn": "0000-0000",
                "aliases": ["FxA", "\u0130stanbul Alpha"],
            },
            "fx-beta": {
                "label": "Fixture Beta",
                "publisher": "fx-press",
                "sources": {
                    "s9": {
                        "url": "https://example.invalid/fx-beta/author",
                        "kind": "html",
                        "retrieved": "2000-02-02",
                        "coverage": "single_journal_sample",
                    }
                },
                "rules": {
                    "widths_mm.double": {
                        "value": 180,
                        "src": "s9",
                        "quote": "FIXTURE beta double 18 cm",
                        "mark": "18 cm",
                        "derivation": "18 cm -> 180 mm",
                        "snapshot": {"file": SNAPSHOT_FILE, "width": 520, "height": 150},
                    },
                    "min_effective_font_size_pt": {
                        "value": 7,
                        "src": "s1",
                        "quote": "FIXTURE 7 pt minimum",
                        "qualifier": "min",
                    },
                    "preferred_formats.export_default": {
                        "value": ["tiff"],
                        "src": "s9",
                        "quote": "FIXTURE tiff only",
                        "stage": "production",
                    },
                    "font_family.latin_accepted": {
                        "value": ["Arial", "Helvetica"],
                        "src": "s9",
                        "quote": "FIXTURE Arial or Helvetica",
                    },
                    "info.one_half_mm": {
                        "value": 120,
                        "value_alt": 136,
                        "src": "s9",
                        "quote": "FIXTURE 1.5 columns 120 mm or 136 mm",
                        "qualifier": "typical",
                    },
                },
            },
            "fx-gamma": {"label": "Fixture Gamma", "publisher": "fx-press"},
            "fx-delta": {"label": "Fixture Delta", "publisher": "fx-bare"},
            # 限定词折叠：typical / recommended / 方向不符的不折进 override，exact 与方向相符的 min/max 才折
            "fx-zeta": {
                "label": "Fixture Zeta",
                "publisher": "fx-press",
                "rules": {
                    "min_effective_font_size_pt": {
                        "value": 5,
                        "src": "s7",
                        "quote": "FIXTURE about 7 pt, not smaller than 5",
                        "qualifier": "typical",
                    },
                    "font_family.latin_accepted": {
                        "value": ["Arial"],
                        "src": "s7",
                        "quote": "FIXTURE preferably Arial",
                        "qualifier": "recommended",
                    },
                    "max_font_size_pt": {
                        "value": 5,
                        "src": "s7",
                        "quote": "FIXTURE at least 5 pt",
                        "qualifier": "min",
                    },
                    "legend_policy.max_font_size_pt": {
                        "value": 9,
                        "src": "s7",
                        "quote": "FIXTURE at most 9 pt",
                        "qualifier": "max",
                    },
                    "min_raster_dpi": {"value": 450, "src": "s7", "quote": "FIXTURE 450 dpi"},
                },
            },
            "fx-cols": {
                "label": "Fixture Columns",
                "publisher": "fx-cols",
                "sources": {
                    "s13": {
                        "url": "https://example.invalid/fx-cols/one",
                        "kind": "html",
                        "retrieved": "2000-06-06",
                    }
                },
                "rules": {
                    # 期刊自己写的容差：page_columns 派生 single / double 时不许把它冲掉
                    "widths_mm.tolerance_mm": {
                        "value": 2,
                        "src": "s13",
                        "quote": "FIXTURE within 2 mm",
                    },
                    # 只有一档 exact + 一档 typical：double 为 null，typical 不参与判据
                    "page_columns": {
                        "value": [
                            {
                                "id": "narrow",
                                "label": {"zh": "窄栏", "en": "Narrow"},
                                "width_mm": 90,
                            },
                            {
                                "id": "wide",
                                "label": {"zh": "宽栏", "en": "Wide"},
                                "width_mm": 150,
                                "qualifier": "typical",
                            },
                        ],
                        "src": "s13",
                        "quote": "FIXTURE 9 cm; wide typically 15 cm",
                    },
                },
            },
            "fx-cols-inherit": {"label": "Fixture Columns Inherit", "publisher": "fx-cols"},
            "fx-adv": {"label": "Fixture Advice", "publisher": "fx-adv-press"},
            "fx-adv-own": {
                "label": "Fixture Advice Own",
                "publisher": "fx-bare",
                "sources": {
                    "s14": {
                        "url": "https://example.invalid/fx-adv-own/sizing",
                        "kind": "html",
                        "retrieved": "2000-07-07",
                    }
                },
                "rules": {
                    "min_effective_font_size_pt": {
                        "value": 5,
                        "src": "s14",
                        "quote": "FIXTURE about 7 pt, not smaller than 5",
                        "qualifier": "typical",
                    }
                },
            },
            "fx-epsilon": {
                "label": "Fixture Epsilon",
                "publisher": "fx-press",
                "rules": {
                    "min_raster_dpi": {
                        "unset": True,
                        "src": "s1",
                        "quote": "FIXTURE epsilon is exempt from the dpi rule",
                    }
                },
            },
        },
    }


def apply_ops(catalog: dict, ops: list[dict]) -> dict:
    """把向量里的编辑操作套到夹具上：`{"path": [...], "value": v}` 设值，`{"path": [...], "delete": true}` 删键。
    两侧测试各有一份同样十行的实现（向量只存操作，不存 22 份整库）。"""
    out = copy.deepcopy(catalog)
    for op in ops:
        node = out
        for part in op["path"][:-1]:
            node = node[part]
        if op.get("delete"):
            del node[op["path"][-1]]
        else:
            node[op["path"][-1]] = copy.deepcopy(op["value"])
    return out


_PUB = ["publishers", "fx-press"]
#: 夹具快照图的文件名（`scripts/gen_journal_snapshot_fixture.py` 生成，内容寻址）
SNAPSHOT_FILE = "snapshots/b884e3345464d614a90400b84e3ec3df8ca6f820a9473de261ae3cc7a63bcb4e.webp"
_W1 = [*_PUB, "rules", "widths_mm.single"]
_SNAP_OK = {"file": SNAPSHOT_FILE, "width": 520, "height": 150}


def _set(path: list, value) -> list[dict]:
    return [{"path": path, "value": value}]


def _del(path: list) -> list[dict]:
    return [{"path": path, "delete": True}]


def _new_rule(path: str, value, src: str = "s1", quote: str = "q") -> list[dict]:
    return _set([*_PUB, "rules", path], {"value": value, "src": src, "quote": quote})


def invalid_cases() -> list[dict]:
    cases: list[tuple[str, str, list[dict]]] = [
        ("schema-version", "journal_catalog_schema", _set(["schema"], 2)),
        (
            "source-bad-kind",
            "journal_source_invalid",
            _set([*_PUB, "sources", "s1", "kind"], "docx"),
        ),
        (
            "source-url-with-fragment",
            "journal_source_invalid",
            _set([*_PUB, "sources", "s1", "url"], "https://example.invalid/a#x"),
        ),
        (
            "source-no-retrieved",
            "journal_source_invalid",
            _del([*_PUB, "sources", "s2", "retrieved"]),
        ),
        (
            "journal-publisher-unknown",
            "journal_publisher_unknown",
            _set(["journals", "fx-alpha", "publisher"], "nobody"),
        ),
        (
            "alias-conflict",
            "journal_alias_conflict",
            _set(["journals", "fx-beta", "aliases"], ["FXA"]),
        ),
        ("rule-value-null", "journal_rule_value_missing", _set([*_W1, "value"], None)),
        ("rule-no-src", "journal_rule_src_missing", _del([*_W1, "src"])),
        ("rule-src-unknown", "journal_rule_src_unknown", _set([*_W1, "src"], "s404")),
        (
            # fx-alpha 看不见 fx-beta 自带的 s9
            "rule-src-of-other-journal",
            "journal_rule_src_unknown",
            _set(
                ["journals", "fx-alpha", "rules"],
                {"min_raster_dpi": {"value": 300, "src": "s9", "quote": "q"}},
            ),
        ),
        ("rule-quote-blank", "journal_rule_quote_empty", _set([*_W1, "quote"], "   ")),
        ("rule-no-quote", "journal_rule_quote_empty", _del([*_W1, "quote"])),
        ("path-unknown-field", "journal_rule_path_unknown", _new_rule("no_such_field", 1)),
        ("path-unknown-subkey", "journal_rule_path_unknown", _new_rule("widths_mm.nope", 1)),
        (
            "path-whole-subobject",
            "journal_rule_path_unknown",
            _new_rule("widths_mm", {"single": 1}),
        ),
        ("path-identity-field", "journal_rule_path_unknown", _new_rule("profile_id", "x")),
        (
            "path-into-non-deep-object",
            "journal_rule_path_unknown",
            _new_rule("palette_policy.url", "u"),
        ),
        ("value-type-string-for-number", "journal_rule_value_type", _set([*_W1, "value"], "85")),
        ("value-type-bool-for-number", "journal_rule_value_type", _set([*_W1, "value"], True)),
        ("merge-negative-width", "journal_merge_invalid", _set([*_W1, "value"], -5)),
        (
            "merge-bad-severity",
            "journal_merge_invalid",
            _set([*_PUB, "rules", "severity.page-width", "value"], "fatal"),
        ),
        (
            "merge-line-widths-not-numbers",
            "journal_merge_invalid",
            _set([*_PUB, "rules", "line_widths_pt", "value"], ["thin"]),
        ),
        (
            "source-url-trailing-newline",
            "journal_source_invalid",
            _set([*_PUB, "sources", "s1", "url"], "https://example.invalid/a\n"),
        ),
        (
            "rule-anchor-trailing-newline",
            "journal_rule_anchor_invalid",
            _set([*_W1, "anchor"], "#:~:text=abc\n"),
        ),
        (
            "value-type-on-null-base-field",
            "journal_rule_value_type",
            _new_rule("axis_policy.tick_direction", 5),
        ),
        (
            "other-journal-cites-a-single-sample-source",
            "journal_source_coverage",
            _set(
                ["journals", "fx-alpha", "rules"],
                {"min_raster_dpi": {"value": 300, "src": "s7", "quote": "q"}},
            ),
        ),
        (
            "single-sample-source-owned-by-other-publishers-journal",
            "journal_source_coverage",
            _set([*_PUB, "sources", "s7", "journal"], "fx-delta"),
        ),
        (
            "single-sample-publisher-source-without-journal",
            "journal_source_coverage",
            _del([*_PUB, "sources", "s7", "journal"]),
        ),
        (
            "journal-field-on-a-general-source",
            "journal_source_invalid",
            _set([*_PUB, "sources", "s1", "journal"], "fx-alpha"),
        ),
        (
            "page-columns-duplicate-id",
            "journal_merge_invalid",
            _set(
                ["journals", "fx-cols", "rules", "page_columns", "value", 1, "id"],
                "narrow",
            ),
        ),
        (
            "page-columns-width-out-of-range",
            "journal_merge_invalid",
            _set(["publishers", "fx-cols", "rules", "page_columns", "value", 0, "width_mm"], 5),
        ),
        (
            "page-columns-label-missing-en",
            "journal_merge_invalid",
            _del(["publishers", "fx-cols", "rules", "page_columns", "value", 0, "label", "en"]),
        ),
        (
            "page-columns-bad-column-qualifier",
            "journal_merge_invalid",
            _set(
                ["publishers", "fx-cols", "rules", "page_columns", "value", 0, "qualifier"],
                "about",
            ),
        ),
        (
            "page-columns-rule-not-exact",
            "journal_rule_qualifier_invalid",
            _set(["publishers", "fx-cols", "rules", "page_columns", "qualifier"], "typical"),
        ),
        (
            "script-font-value-type",
            "journal_rule_value_type",
            _set(["publishers", "fx-adv-press", "rules", "min_script_font_size_pt", "value"], "6"),
        ),
        (
            "script-font-value-not-positive",
            "journal_merge_invalid",
            _set(["publishers", "fx-adv-press", "rules", "min_script_font_size_pt", "value"], 0),
        ),
        ("catalog-rev-missing", "journal_catalog_schema", _del(["catalog_rev"])),
        ("catalog-rev-zero", "journal_catalog_schema", _set(["catalog_rev"], 0)),
        (
            "source-url-http",
            "journal_source_invalid",
            _set([*_PUB, "sources", "s1", "url"], "http://example.invalid/a"),
        ),
        (
            "source-url-javascript",
            "journal_source_invalid",
            _set([*_PUB, "sources", "s1", "url"], "javascript:alert(1)"),
        ),
        (
            "source-retrieved-not-a-date",
            "journal_source_invalid",
            _set([*_PUB, "sources", "s1", "retrieved"], "yesterday"),
        ),
        (
            "source-retrieved-impossible-date",
            "journal_source_invalid",
            _set([*_PUB, "sources", "s1", "retrieved"], "2000-02-30"),
        ),
        (
            "source-archive-url-not-https",
            "journal_source_invalid",
            _set([*_PUB, "sources", "s1", "archive_url"], "ftp://example.invalid/x"),
        ),
        (
            "source-coverage-bad-enum",
            "journal_source_invalid",
            _set([*_PUB, "sources", "s1", "coverage"], "everything"),
        ),
        (
            "source-page-fraction",
            "journal_source_invalid",
            _set([*_PUB, "sources", "s2", "page"], 1.5),
        ),
        ("source-page-zero", "journal_source_invalid", _set([*_PUB, "sources", "s2", "page"], 0)),
        (
            "single-sample-source-on-publisher-rules",
            "journal_source_coverage",
            _set([*_PUB, "sources", "s2", "coverage"], "single_journal_sample"),
        ),
        (
            "publisher-not-a-string",
            "journal_publisher_unknown",
            _set(["journals", "fx-alpha", "publisher"], ["fx-press"]),
        ),
        (
            "alias-conflict-after-nfkc",
            "journal_alias_conflict",
            _set(["journals", "fx-beta", "aliases"], ["\uff26\uff38\uff21"]),
        ),
        (
            "alias-conflict-non-ascii-case",
            "journal_alias_conflict",
            _set(["journals", "fx-beta", "aliases"], ["i\u0307stanbul alpha"]),
        ),
        (
            "rule-anchor-not-text-fragment",
            "journal_rule_anchor_invalid",
            _set([*_W1, "anchor"], "#foo"),
        ),
        (
            "rule-qualifier-bad-enum",
            "journal_rule_qualifier_invalid",
            _set([*_W1, "qualifier"], "about"),
        ),
        (
            "rule-qualifier-not-exact-on-width",
            "journal_rule_qualifier_invalid",
            _set([*_W1, "qualifier"], "max"),
        ),
        ("rule-stage-bad-enum", "journal_rule_stage_invalid", _set([*_W1, "stage"], "later")),
        (
            "snapshot-file-with-path",
            "journal_rule_snapshot_invalid",
            _set([*_W1, "snapshot"], {**_SNAP_OK, "file": "../" + SNAPSHOT_FILE}),
        ),
        (
            "snapshot-file-url",
            "journal_rule_snapshot_invalid",
            _set([*_W1, "snapshot"], {**_SNAP_OK, "file": "https://example.invalid/a.webp"}),
        ),
        (
            "snapshot-file-uppercase-hash",
            "journal_rule_snapshot_invalid",
            _set(
                [*_W1, "snapshot"],
                {
                    **_SNAP_OK,
                    "file": SNAPSHOT_FILE.upper()
                    .replace("SNAPSHOTS/", "snapshots/")
                    .replace(".WEBP", ".webp"),
                },
            ),
        ),
        (
            "snapshot-file-not-webp",
            "journal_rule_snapshot_invalid",
            _set([*_W1, "snapshot"], {**_SNAP_OK, "file": SNAPSHOT_FILE[:-4] + "png"}),
        ),
        (
            "snapshot-file-trailing-newline",
            "journal_rule_snapshot_invalid",
            _set([*_W1, "snapshot"], {**_SNAP_OK, "file": SNAPSHOT_FILE + "\n"}),
        ),
        (
            "snapshot-width-zero",
            "journal_rule_snapshot_invalid",
            _set([*_W1, "snapshot"], {**_SNAP_OK, "width": 0}),
        ),
        (
            "snapshot-height-bool",
            "journal_rule_snapshot_invalid",
            _set([*_W1, "snapshot"], {**_SNAP_OK, "height": True}),
        ),
        (
            "snapshot-missing-height",
            "journal_rule_snapshot_invalid",
            _set([*_W1, "snapshot"], {"file": SNAPSHOT_FILE, "width": 520}),
        ),
        (
            "snapshot-extra-key",
            "journal_rule_snapshot_invalid",
            _set([*_W1, "snapshot"], {**_SNAP_OK, "url": "https://example.invalid/a.webp"}),
        ),
        ("snapshot-not-object", "journal_rule_snapshot_invalid", _set([*_W1, "snapshot"], "x")),
        (
            "info-snapshot-invalid",
            "journal_rule_snapshot_invalid",
            _set([*_PUB, "rules", "info.max_height_mm", "snapshot"], {**_SNAP_OK, "width": -1}),
        ),
        ("rule-mark-not-in-quote", "journal_rule_mark_invalid", _set([*_W1, "mark"], "86 mm")),
        ("rule-derivation-blank", "journal_rule_mark_invalid", _set([*_W1, "derivation"], " ")),
        ("rule-value-alt-wrong-type", "journal_rule_value_type", _set([*_W1, "value_alt"], "x")),
        (
            "unset-on-publisher-rule",
            "journal_rule_unset_invalid",
            _set([*_PUB, "rules", "min_raster_dpi"], {"unset": True, "src": "s1", "quote": "q"}),
        ),
        (
            "unset-with-value",
            "journal_rule_unset_invalid",
            _set(["journals", "fx-epsilon", "rules", "min_raster_dpi", "value"], 300),
        ),
        (
            "unset-without-publisher-rule",
            "journal_rule_unset_invalid",
            _set(
                ["journals", "fx-epsilon", "rules"],
                {"min_effective_font_size_pt": {"unset": True, "src": "s1", "quote": "q"}},
            ),
        ),
        (
            "unset-not-true",
            "journal_rule_unset_invalid",
            _set(["journals", "fx-epsilon", "rules", "min_raster_dpi", "unset"], False),
        ),
        ("info-id-unknown", "journal_rule_path_unknown", _new_rule("info.nonsense", 1)),
        (
            "info-without-quote",
            "journal_rule_quote_empty",
            _set([*_PUB, "rules", "info.max_height_mm", "quote"], ""),
        ),
        (
            "info-value-null",
            "journal_rule_value_missing",
            _set([*_PUB, "rules", "info.max_height_mm", "value"], None),
        ),
    ]
    return [{"name": n, "code": code, "ops": ops} for n, code, ops in cases]


def _flatten(d: dict, prefix: str = "") -> list[str]:
    """嵌套覆盖的叶子路径（列表 / 标量是叶子）。"""
    out: list[str] = []
    for k, v in d.items():
        path = f"{prefix}{k}"
        out += _flatten(v, path + ".") if isinstance(v, dict) else [path]
    return out


def build() -> dict:
    cat = fixture_catalog()
    cases = []
    for jid in sorted(cat["journals"]):
        override = journals.journal_override(jid, cat)
        sources = journals.journal_sources(jid, cat)
        merged = journals.journal_profile(jid, cat)
        fields = {}
        for path in _flatten(override):
            node = merged
            for part in path.split("."):
                node = node[part]
            fields[path] = node
        cases.append(
            {
                "journal_id": jid,
                "override": override,
                "sources": sources,
                "merged_fields": fields,
                "page_columns": profiles.derive_page_columns(merged),
            }
        )
    return {
        "comment": "期刊库 golden 向量：由 scripts/gen_journal_vectors.py --write 生成，pytest 与 vitest 各跑一遍。夹具期刊，非真实数据。",
        "base_profile_id": journals.BASE_PROFILE_ID,
        "constants": {
            "info_ids": list(journals.INFO_IDS),
            "qualifiers": list(journals.QUALIFIERS),
            "stages": list(journals.STAGES),
            "coverages": list(journals.COVERAGES),
            "exact_only_paths": list(journals.EXACT_ONLY_PATHS),
            "severity_bump": journals.SEVERITY_BUMP,
            "bumped_severity": journals.BUMPED_SEVERITY,
            "kind_overrides": journals.KIND_OVERRIDES,
            "qualifier_direction": journals.QUALIFIER_DIRECTION,
            "advisory_paths": journals.ADVISORY_PATHS,
            "advisory_qualifiers": list(journals.ADVISORY_QUALIFIERS),
            "advisory_check": journals.ADVISORY_CHECK,
            "advisory_roles": list(profiles.ADVISORY_ROLES),
            "error_codes": list(journals.ERROR_CODES),
        },
        "catalog_rev": journals.catalog_rev(cat),
        "catalog": cat,
        "list": journals.list_journals(cat),
        "cases": cases,
        "unknown_journal_code": "journal_unknown",
        "invalid": invalid_cases(),
    }


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _self_check(vectors: dict) -> None:
    """生成时顺手确认每条 invalid 向量真的只在它声明的失败码上失败。"""
    for case in vectors["invalid"]:
        try:
            journals.validate_catalog(apply_ops(fixture_catalog(), case["ops"]))
        except journals.JournalCatalogError as exc:
            if exc.code != case["code"]:
                raise SystemExit(f"向量 {case['name']}: 期望 {case['code']}，实际 {exc.code}")
        else:
            raise SystemExit(f"向量 {case['name']}: 期望失败 {case['code']}，实际通过")


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="按 Python 侧重新生成")
    args = ap.parse_args(argv)

    fresh = build()
    _self_check(fresh)
    text = json.dumps(fresh, ensure_ascii=False, indent=1) + "\n"
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"已写入 {OUT}（{len(fresh['cases'])} 本 / {len(fresh['invalid'])} 条失败向量）")
        return 0
    if not OUT.is_file():
        print(f"缺少 {OUT}，先跑一次 --write", file=sys.stderr)
        return 1
    if OUT.read_text(encoding="utf-8") != text:
        print(
            f"{OUT} 与当前 Python 实现不一致（--write 重新生成后人工过一遍 diff）", file=sys.stderr
        )
        return 1
    print(f"{OUT} 与 Python 实现一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
