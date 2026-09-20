#!/usr/bin/env python3
"""Generate traceability views from plan.json/registry.json, not product results."""

import argparse
import json
from pathlib import Path


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def generate(root):
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    phases = json.loads((root / "phase_mapping.json").read_text(encoding="utf-8"))
    entries = registry["entries"]
    lines = [
        "# 原包到统一计划的追踪表（生成物）",
        "",
        "来源原文和完整原始字段保存在 `../registry.json` 的 original 字段；本表不作为第二套可编辑台账。",
        "188条要求与32个场景分开，不等于220条独立测试；当前全部产品结果not_run。",
        "",
        "## 原阶段映射",
        "",
        "| 原阶段 | 统一阶段 |",
        "|---|---|",
    ]
    for old, targets in phases.items():
        lines.append(f"| {old} | {', '.join(targets)} |")
    for kind, title in [("requirement", "188条要求"), ("scenario", "32个首开场景")]:
        lines += [
            "",
            f"## {title}",
            "",
            "| 原ID | 原要求/场景 | 当前实现阶段 | 处置 | 当前解释 |",
            "|---|---|---|---|---|",
        ]
        for e in entries:
            if e["kind"] != kind:
                continue
            original = e["original"]
            content = original.get("requirement", original.get("title", ""))
            lines.append(
                "| "
                + " | ".join(
                    cell(x)
                    for x in [
                        e["id"],
                        content,
                        e["implementation_stage"],
                        e["disposition"],
                        e["current_interpretation"],
                    ]
                )
                + " |"
            )
    (root / "generated/TRACEABILITY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    lines = [
        "# FirstOpenBench 场景准入建议（生成物）",
        "",
        "这是未来测试分层建议，不是已安装的runner配置。所有场景当前planned/later、not_run。",
        "旧minimum_deep_lane保存在registry原字段，本表以统一阶段和03政策重排。",
        "",
        "| ID | 场景 | 实现阶段 | 建议深度lane | 预期产品结果（原合同） | 准入 | 产品结果 |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in entries:
        if e["kind"] == "scenario":
            o = e["original"]
            lines.append(
                "| "
                + " | ".join(
                    cell(x)
                    for x in [
                        e["id"],
                        o["title"],
                        e["implementation_stage"],
                        e["recommended_deep_lane"],
                        o["expected_product_outcome"],
                        e["enrollment"],
                        e["execution_status"],
                    ]
                )
                + " |"
            )
    lines += [
        "",
        "contractual案例必须在执行前拆成具体成功/停止合同；不能运行后选择更容易过的解释。",
        "严禁用safe_stop测试pass计入自动兼容成功。FO23/FO32保留最终真实安装资格；FO14的新增命名Conda适配后置X01。",
    ]
    (root / "generated/FIRST_OPEN_SCHEDULE.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    order = [
        "README.md",
        "00_MASTER_PROMPT.md",
        "01_SCOPE_AND_DECISIONS.md",
        "02_ROADMAP.md",
        "03_CI_POLICY.md",
        "04_ARCHITECTURE.md",
        "05_TEST_STRATEGY.md",
        "06_ENABLE_AND_RELEASE.md",
    ]
    order += [s["document"] for s in plan["stages"]]
    order += ["07_HANDOFF.md", "SOURCES.md"]
    out = [
        "# Tavotto 统一实施计划 · 完整执行合并版",
        "",
        "本文件由包内规范文档生成，替代三个旧master的并行执行。引用路径按ZIP内结构解析；archive旧材料不叠加生效。",
        "阶段及台账为计划，不代表Tavotto产品测试通过。完整原ID追踪在ZIP内registry.json/generated目录。",
        "",
        "## 目录",
        "",
    ]
    out += [f"- `{name}`" for name in order]
    for name in order:
        out += [
            "",
            "---",
            "",
            f"<!-- 包内来源：{name} -->",
            "",
            (root / name).read_text(encoding="utf-8"),
        ]
    (root / "ALL_PROMPTS.md").write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    generate(Path(args.root))
