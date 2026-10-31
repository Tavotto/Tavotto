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

from tavotto.engine import journals  # noqa: E402

OUT = ROOT / "tests" / "golden" / "journal_vectors.json"


def fixture_catalog() -> dict:
    """夹具期刊库：一个出版社、三本虚构期刊，覆盖「继承 / 期刊覆盖出版社 / 期刊自带出处 / 出版社与期刊都无规则」。"""
    return {
        "schema": 1,
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
                        "page": 12,
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
                    "min_raster_dpi": {"value": 600, "src": "s2", "quote": "FIXTURE 600 dpi"},
                    "line_widths_pt": {"value": [0.5, 1.0], "src": "s2", "quote": "FIXTURE lines"},
                    "severity.page-width": {
                        "value": "error",
                        "src": "s1",
                        "quote": "FIXTURE width is mandatory",
                    },
                },
            },
            "fx-bare": {"label": "Fixture Bare"},
        },
        "journals": {
            "fx-alpha": {
                "label": "Fixture Alpha",
                "publisher": "fx-press",
                "issn": "0000-0000",
                "aliases": ["FxA"],
            },
            "fx-beta": {
                "label": "Fixture Beta",
                "publisher": "fx-press",
                "sources": {
                    "s9": {
                        "url": "https://example.invalid/fx-beta/author",
                        "kind": "html",
                        "retrieved": "2000-02-02",
                    }
                },
                "rules": {
                    "widths_mm.double": {
                        "value": 180,
                        "src": "s9",
                        "quote": "FIXTURE beta double 180",
                    },
                    "min_effective_font_size_pt": {
                        "value": 7,
                        "src": "s1",
                        "quote": "FIXTURE 7 pt minimum",
                    },
                    "preferred_formats.export_default": {
                        "value": ["tiff"],
                        "src": "s9",
                        "quote": "FIXTURE tiff only",
                    },
                    "font_family.latin_accepted": {
                        "value": ["Arial", "Helvetica"],
                        "src": "s9",
                        "quote": "FIXTURE Arial or Helvetica",
                    },
                },
            },
            "fx-gamma": {"label": "Fixture Gamma", "publisher": "fx-press"},
            "fx-delta": {"label": "Fixture Delta", "publisher": "fx-bare"},
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
_W1 = [*_PUB, "rules", "widths_mm.single"]


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
    ]
    return [{"name": n, "code": code, "ops": ops} for n, code, ops in cases]


def build() -> dict:
    cat = fixture_catalog()
    cases = []
    for jid in sorted(cat["journals"]):
        override = journals.journal_override(jid, cat)
        sources = journals.journal_sources(jid, cat)
        merged = journals.journal_profile(jid, cat)
        fields = {}
        for path in sources:
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
            }
        )
    return {
        "comment": "期刊库 golden 向量：由 scripts/gen_journal_vectors.py --write 生成，pytest 与 vitest 各跑一遍。夹具期刊，非真实数据。",
        "base_profile_id": journals.BASE_PROFILE_ID,
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
