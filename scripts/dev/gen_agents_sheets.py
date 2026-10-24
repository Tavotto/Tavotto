"""由细则头部的 `agents-route` 块生成四份速查表里「改到 | 主题 | 必守要点」那张路由表。

为什么：速查表 60 天里被 207 个提交改过，几乎每个功能 PR 都去改同一张表的某一行——又长又互相
冲突（docs/rules/README.md「棘轮」）。路由信息改住在各自的细则文件里，功能 PR 只改自己那份细则，
速查表由本脚本生成（参照 gstack：SKILL.md 由 .tmpl 生成 + 检查生成物没过期的测试）。

块的写法（放在 `docs/rules/<层>/<主题>.md` 头部（出处引用块之后；放末尾会和每个往末尾加规则的在飞 PR 冲突）；一份细则可以有多个块，各路由到不同速查表）::

    <!-- agents-route
    sheet: src/tavotto/AGENTS.md
    order: 120
    改到: `engine/brand.py`、……
    主题: 品牌与命名 → `brand-and-naming.md`
    必守要点: ……
    -->

* `sheet`：目标速查表（仓库根相对、posix），必须是 `SHEET_LAYERS` 里的一份。
* `order`：整数，同一张速查表内不得重复；只决定行序，插新行取个空档就行，不用动别人。
* 三个值原样进表格（`\\|` 之类转义照写），单行，不得含 ` | `、`-->` 与换行。

速查表里 `BEGIN GENERATED` / `END GENERATED` 两行标记之间（含表头与分隔行）整段由本脚本重写，
手改会在下次 `--write` 时丢掉，`--check` 会报过期。

用法：`python scripts/dev/gen_agents_sheets.py [--write | --check]`（默认等同 `--check`）。
纯标准库。
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RULES = ROOT / "docs" / "rules"

#: 有路由表的速查表 → 它的细则层目录名（与 `tests/test_agents_rules_index.py` 的 `SHEETS` 对齐）。
SHEET_LAYERS: dict[str, str] = {
    "src/tavotto/AGENTS.md": "backend",
    "web/AGENTS.md": "frontend",
    ".github/AGENTS.md": "ci",
    "codex-plugin/AGENTS.md": "plugin",
    "packaging/AGENTS.md": "packaging",
    "src-tauri/AGENTS.md": "desktop",
}

BEGIN_MARK = "<!-- BEGIN GENERATED: agents-routes -->"
END_MARK = "<!-- END GENERATED: agents-routes -->"

#: 块里必须有的键（值按这个顺序进表格三格）。
CELL_KEYS = ("改到", "主题", "必守要点")
REQUIRED_KEYS = ("sheet", "order", *CELL_KEYS)

BLOCK_RE = re.compile(r"<!-- agents-route\n(.*?)\n-->", re.S)
LINE_RE = re.compile(r"^([^:\n]+): (.*)$")


class RouteError(ValueError):
    """块写得不合规矩（键缺失 / 未知 / order 不是整数 / 目标速查表不存在）。"""


def header(sheet: str) -> list[str]:
    layer = SHEET_LAYERS[sheet]
    return [
        f"| 改到 | 主题（细则在 `docs/rules/{layer}/`） | 必守要点 |",
        "| --- | --- | --- |",
    ]


def rules_files() -> list[Path]:
    return sorted(p for p in RULES.rglob("*.md") if p.name != "README.md")


def parse_blocks(path: Path) -> list[dict[str, str]]:
    """一份细则里的全部 agents-route 块。"""
    rel = path.relative_to(ROOT).as_posix()
    out = []
    for m in BLOCK_RE.finditer(path.read_text(encoding="utf-8")):
        block: dict[str, str] = {}
        for ln in m.group(1).split("\n"):
            lm = LINE_RE.match(ln)
            if not lm or lm.group(1) not in REQUIRED_KEYS:
                raise RouteError(
                    f"{rel}: 无法识别的行 {ln!r}（键只能是 {', '.join(REQUIRED_KEYS)}）"
                )
            if lm.group(1) in block:
                raise RouteError(f"{rel}: 键 {lm.group(1)} 重复")
            block[lm.group(1)] = lm.group(2).strip()
        missing = [k for k in REQUIRED_KEYS if k not in block]
        if missing:
            raise RouteError(f"{rel}: 块缺少键 {missing}")
        if not re.fullmatch(r"-?\d+", block["order"]):
            raise RouteError(f"{rel}: order 必须是整数，得到 {block['order']!r}")
        for k in CELL_KEYS:
            if " | " in block[k]:
                raise RouteError(f"{rel}: 「{k}」含 ' | '，表格会多出一格（竖线请写成 \\|）")
        block["_file"] = rel
        out.append(block)
    return out


def collect() -> dict[str, list[dict[str, str]]]:
    """按速查表分组、按 order 排序的全部块。未知 sheet / 同表 order 重复抛 RouteError。"""
    by_sheet: dict[str, list[dict[str, str]]] = {s: [] for s in SHEET_LAYERS}
    for path in rules_files():
        for block in parse_blocks(path):
            if block["sheet"] not in SHEET_LAYERS:
                raise RouteError(
                    f"{block['_file']}: sheet {block['sheet']!r} 不是有路由表的速查表"
                    f"（{', '.join(SHEET_LAYERS)}）"
                )
            by_sheet[block["sheet"]].append(block)
    for sheet, blocks in by_sheet.items():
        seen: dict[int, str] = {}
        for b in blocks:
            n = int(b["order"])
            if n in seen:
                raise RouteError(f"{sheet}: order {n} 重复（{seen[n]} 与 {b['_file']}）")
            seen[n] = b["_file"]
        blocks.sort(key=lambda b: int(b["order"]))
    return by_sheet


def render_table(sheet: str, blocks: list[dict[str, str]]) -> list[str]:
    rows = [f"| {b['改到']} | {b['主题']} | {b['必守要点']} |" for b in blocks]
    return header(sheet) + rows


def regenerate(sheet: str, text: str, blocks: list[dict[str, str]]) -> str:
    """把 `text` 里两行标记之间的内容换成新表；标记缺失 / 乱序抛 RouteError。"""
    lines = text.split("\n")
    try:
        b, e = lines.index(BEGIN_MARK), lines.index(END_MARK)
    except ValueError:
        raise RouteError(f"{sheet}: 找不到 BEGIN/END GENERATED 标记行") from None
    if e <= b:
        raise RouteError(f"{sheet}: END 标记在 BEGIN 之前")
    return "\n".join(lines[: b + 1] + render_table(sheet, blocks) + lines[e:])


def expected() -> dict[str, str]:
    """每份速查表「应有」的全文。"""
    by_sheet = collect()
    return {
        sheet: regenerate(sheet, (ROOT / sheet).read_text(encoding="utf-8"), blocks)
        for sheet, blocks in by_sheet.items()
    }


def stale() -> list[str]:
    """过期的速查表的 unified diff（空列表 = 都是新的）。"""
    out = []
    for sheet, new in expected().items():
        old = (ROOT / sheet).read_text(encoding="utf-8")
        if old != new:
            out += list(
                difflib.unified_diff(
                    old.splitlines(),
                    new.splitlines(),
                    f"{sheet}（现状）",
                    f"{sheet}（应为）",
                    lineterm="",
                )
            )
    return out


def write() -> list[str]:
    changed = []
    for sheet, new in expected().items():
        p = ROOT / sheet
        if p.read_text(encoding="utf-8") != new:
            p.write_text(new, encoding="utf-8", newline="")
            changed.append(sheet)
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="重写四份速查表里的生成段")
    mode.add_argument("--check", action="store_true", help="不写；过期则打印 diff 并退 1（默认）")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        if args.write:
            changed = write()
            print("已重写: " + (", ".join(changed) if changed else "（无变化）"))
            return 0
        diff = stale()
    except RouteError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 1
    if diff:
        print("\n".join(diff))
        print(
            "!! 速查表路由表已过期：改细则头部的 agents-route 块后跑 "
            "`python scripts/dev/gen_agents_sheets.py --write`",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
