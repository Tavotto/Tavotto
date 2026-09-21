#!/usr/bin/env python3
"""Derive U00_FACADE_LEDGER.md from U00_FACADE_LEDGER.json (one truth, one view).

Pure standard library. `python tools/generate_facade_ledger.py [root] [--check]`:
without `--check` it rewrites the Markdown; with `--check` it exits 1 when the
Markdown on disk differs from what the JSON would render (drift guard used by
`tests/test_foundation_facade_ledger.py`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Windows 上 stdout / stderr 一被重定向就退回系统区域编码（cp1252 / cp936），第一句
# 中文输出就 UnicodeEncodeError；这些工具都会被 subprocess 捕获着调用，两条流一起钉。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

JSON_NAME = "U00_FACADE_LEDGER.json"
MD_NAME = "U00_FACADE_LEDGER.md"


def cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def render(ledger: dict) -> str:
    lines = [
        "# pdfbackend facade 迁移清单（生成物）",
        "",
        f"真值在 `{JSON_NAME}`（`tools/generate_facade_ledger.py` 派生本文件，手改无效）。",
        f"采样 SHA `{ledger['source_sha']}`（{ledger['generated_on']}）；facade `{ledger['facade_module']}`，"
        f"实现 `{ledger['implementation_module']}`。",
        "",
        "## 类别",
        "",
        "| 类别 | 含义 |",
        "|---|---|",
    ]
    for key, meaning in ledger["categories"].items():
        lines.append(f"| `{key}` | {cell(meaning)} |")
    lines += ["", "## 测试分类", "", "| 类 | 含义 |", "|---|---|"]
    for key, meaning in ledger["test_classes"].items():
        lines.append(f"| `{key}` | {cell(meaning)} |")
    lines += [
        "",
        f"## `__all__` 的 {len(ledger['exports'])} 个导出项",
        "",
        "| 导出项 | 类别 | 状态 | 产品调用方（file:line · 函数） | 现有测试（类） | 已知缺陷 | 迁移判据 |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in ledger["exports"]:
        callers = (
            "<br>".join(f"`{c['file']}:{c['line']}` · `{c['function']}`" for c in e["callers"])
            or "（无产品调用方）"
        )
        tests = (
            "<br>".join(f"`{t['file']}:{t['line']}` ({t['class']})" for t in e["tests"]) or "（无）"
        )
        issues = "<br>".join(e.get("known_issues", [])) or "—"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{e['name']}`",
                    f"`{e['category']}`",
                    cell(e["status"]),
                    callers,
                    tests,
                    cell(issues),
                    cell(e["migration_criterion"]),
                ]
            )
            + " |"
        )
    lines += ["", "### 各项备注", ""]
    for e in ledger["exports"]:
        extra = []
        if e.get("signature"):
            extra.append(f"签名 `{e['signature']}`")
        if e.get("value_now"):
            extra.append(f"现值 `{e['value_now']}`")
        if e.get("same_origin_pair"):
            extra.append(f"严格同源对：{e['same_origin_pair']}")
        if e.get("note"):
            extra.append(e["note"])
        for m in e.get("migration_evidence", []):
            extra.append(f"{m['stage']} 迁移证据 `{m['test']}`")
        if extra:
            lines.append(f"- **`{e['name']}`**：" + "；".join(extra))
    cm = ledger["canvas_methods"]
    lines += [
        "",
        "## Canvas 面（RC-002）",
        "",
        cm["note"],
        "",
        "| 方法 | 签名 | 调用方 | 作用 |",
        "|---|---|---|---|",
    ]
    for m in cm["methods"]:
        callers = "<br>".join(
            f"`{c['file']}:{c['line']}` · `{c['function']}`" for c in m["callers"]
        )
        lines.append(f"| `{m['name']}` | `{m['signature']}` | {callers} | {cell(m['role'])} |")
    lines += [
        "",
        f"迁移判据：{cm['migration_criterion']}",
        "",
        "## 不经 facade 的路径",
        "",
        ledger["bypasses"]["note"],
        "",
        "| 路径 | 类别 | 说明 |",
        "|---|---|---|",
    ]
    for b in ledger["bypasses"]["items"]:
        lines.append(f"| `{b['path']}` | `{b['category']}` | {cell(b['detail'])} |")
    isd = ledger["implementation_specific_test_dependencies"]
    lines += [
        "",
        "## 测试对实现模块的直接依赖",
        "",
        isd["note"],
        "",
        f"- 私有名：{', '.join(f'`{n}`' for n in isd['private_names'])}",
        f"- 非 facade 的公开名：{', '.join(f'`{n}`' for n in isd['public_but_not_facade'])}",
        f"- 涉及文件：{', '.join(f'`{n}`' for n in isd['files_using_these'])}",
        f"- 直接 `import pymupdf` 的测试文件数：{isd['test_files_importing_pymupdf_directly']}；角色：{isd['pymupdf_in_tests_role']}",
        "",
        "## 只在注释里承诺的",
        "",
        "| 出处 | 承诺 | 现实 |",
        "|---|---|---|",
    ]
    for p in ledger["promised_only_in_comments"]:
        lines.append(f"| {cell(p['where'])} | {cell(p['claim'])} | {cell(p['reality'])} |")
    lines += ["", "## 本轮新增目标", ""]
    lines += [f"- {t}" for t in ledger["new_targets_this_program"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", nargs="?", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)
    ledger = json.loads((root / JSON_NAME).read_text(encoding="utf-8"))
    text = render(ledger)
    target = root / MD_NAME
    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != text:
            print(f"{MD_NAME} 与 {JSON_NAME} 不一致：重新生成后再提交")
            return 1
        print(f"{MD_NAME} 与 {JSON_NAME} 一致")
        return 0
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
