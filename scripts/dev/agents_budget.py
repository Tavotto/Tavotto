"""按**实际加载路径**量指导文档的体积，对照试行预算打印一张表。

试行预算只报告，不当门禁（`--strict` 才按预算退非零）。理由写在 docs/rules/README.md：预算是
本项目试行的数字，超标先报告、不为达标粗暴删规则；量的是原始字节，**不是 token 数**，
也不代表任何账户的实际扣费。硬线有四条，`--check` 按它们退非零，
`tests/test_agents_rules_index.py` 钉着同样的几条：

1. **Codex 自动拼接不越过 32 KiB**（#608）——那不是我们定的预算，是 Codex 的默认上限，
   越过就静默截掉末尾。
2. **每份常驻指令不越过自己的棘轮上限**（`tests/fixtures/agents_ceilings.json`，2026-10-23）：
   每份 `AGENTS.md` 与每份 `SKILL.md` 各有一个上限，要长大就在同一个 PR 里
   `--write-ceilings` 并提交那份 JSON——增长在 diff 里看得见、评审要问理由，而不是一路涨到
   撞上第 1 条才临时腾字。
3. **速查表每格不越过 `SHEET_CELL_MAX` 字节**：「改到」写目录 / 入口文件，「必守要点」写一句
   索引；完整清单与全文放细则。

两种加载方式各量一份：

* **Codex**：沿「仓库根 → cwd」拼接每层 `AGENTS.md`，默认 `project_doc_max_bytes` =
  32 KiB，到顶就停——所以按 cwd 给出「自动加载的那一串」有没有越过上限。
* **按任务读**（Claude Code 与 Codex 里按根的指示手动读的那条路）：根 + 本层速查表 +
  这项任务要读的细则，就是这次会话为规则付出的字节。

用法：`python scripts/dev/agents_budget.py [--strict] [--check] [--json] [--write-ceilings]`
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

#: 本项目试行预算（字节）。不是行业标准；来自 2026-09-17 可维护性审计任务书。
BUDGET_ROOT = (4_000, 6_000)
BUDGET_SHEET = (6_000, 10_000)
BUDGET_CHAIN = (16_000, 24_000)
CODEX_DEFAULT_CAP = 32 * 1024
#: 速查表一格的上限（UTF-8 字节，约 100 个汉字）。
SHEET_CELL_MAX = 300
#: 每份常驻指令的棘轮上限（CRLF 口径字节）；`--write-ceilings` 重写。
CEILINGS_FILE = ROOT / "tests" / "fixtures" / "agents_ceilings.json"
#: `--write-ceilings` 在现状上留的余量：小修小补不必每次改 JSON，成段加料必须改。
CEILING_SLACK = 256

#: 审计基线（main @ 6fe6e38d，2026-09-17）量到的原始字节数，用来给出「治理前后」的差。
AUDIT_BASELINE_BYTES = {
    "AGENTS.md": 14_773,
    "src/tavotto/AGENTS.md": 143_922,
    "web/AGENTS.md": 147_812,
    ".github/AGENTS.md": 31_773,  # 2026-09-18 纳入（任务书之外，用户拍板）
    "codex-plugin/AGENTS.md": 35_199,  # 2026-09-25 纳入（#608，main @ e6e6e643）
}

#: 三类代表任务（任务书点名的）+ 一个「只开会话」的对照。每项 = 根 + 速查表 + 细则。
TASK_CHAINS: dict[str, list[str]] = {
    "只开会话（每次都读）": ["CLAUDE.md", "AGENTS.md"],
    "前端样式": [
        "CLAUDE.md",
        "AGENTS.md",
        "web/AGENTS.md",
        "docs/rules/frontend/ui-visual-discipline.md",
    ],
    "后端导出": [
        "CLAUDE.md",
        "AGENTS.md",
        "src/tavotto/AGENTS.md",
        "docs/rules/backend/export-pipeline.md",
        "docs/rules/repo/same-origin-pairs.md",
    ],
    "worker（协议 / 生命周期 / 捕获）": [
        "CLAUDE.md",
        "AGENTS.md",
        "src/tavotto/AGENTS.md",
        "docs/rules/backend/worker-protocol-and-lifecycle.md",
        "docs/rules/backend/figure-capture-and-execution.md",
        "docs/rules/backend/execution-entries.md",
    ],
}

#: 有「改到 | 主题 | 必守要点」路由表的速查表（与 `tests/test_agents_rules_index.py` 的 `SHEETS` 对齐）。
SHEETS_WITH_TABLE = (
    "src/tavotto/AGENTS.md",
    "web/AGENTS.md",
    ".github/AGENTS.md",
    "codex-plugin/AGENTS.md",
)

#: 速查表、细则层、Codex 拼接的 cwd 都从仓库现状推出来（`agents_files()` / `rule_layers()`），
#: 不写死清单：写死的清单漏掉新加的一层时报告照样「全 ok」（#620 评审）。


def size(rel: str) -> int:
    p = ROOT / rel
    return p.stat().st_size if p.is_file() else 0


def crlf_bytes(data: bytes) -> int:
    """按 CRLF 口径量字节：先把 CRLF / 孤立 CR 统一成 LF，再给每个 LF 补 1 字节。

    Windows 检出是 CRLF、macOS/Linux 是 LF，同一份内容原始字节不同（#935）；统一按 CRLF
    量，各平台算出同一个数，且已是 CRLF 的输入不会被重复计数。
    """
    lf = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return len(lf) + lf.count(b"\n")


def codex_size(rel: str) -> int:
    """Codex 拼接用的体积：CRLF 口径（见 `crlf_bytes`）。"""
    p = ROOT / rel
    return crlf_bytes(p.read_bytes()) if p.is_file() else 0


def verdict(n: int, budget: tuple[int, int]) -> str:
    lo, hi = budget
    if n <= hi:
        return "ok"
    return f"OVER +{n - hi}"


def agents_files() -> list[str]:
    """仓库**跟踪**的每一份 AGENTS.md（仓库根相对、posix），含隐藏目录（`.agents/`、`.codex-plugin/`…）。

    判据的主语是「这个仓库发出去的指令」：CI 的 checkout 里只有跟踪的文件，所以按
    `git ls-files` 枚举；本机未跟踪的 AGENTS.md（个人草稿）不是仓库的契约，不算。
    不用目录遍历：遍历要剪掉 node_modules / .venv 之类，剪枝规则一宽就把 `.agents/` 这种
    正经目录一起剪掉了，门禁对那里的文件是瞎的（#620 评审实测 40 KB 也绿）。
    git 不可用时直接抛——返回空列表会让「都没越线」在空集合上恒真。
    """
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "AGENTS.md", "*/AGENTS.md"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    files = {f for f in out.split("\0") if f and (ROOT / f).is_file()}
    return sorted(files, key=lambda f: (f.count("/"), f))


def codex_chain(cwd: str) -> list[str]:
    """从 cwd 开工时 Codex 沿「根 → cwd」拼进来的那几份（只算跟踪的）。"""
    tracked = set(agents_files())
    parts = [] if cwd == "." else cwd.split("/")
    out = []
    for i in range(len(parts) + 1):
        rel = "/".join(parts[:i] + ["AGENTS.md"])
        if rel in tracked:
            out.append(rel)
    return out


def codex_cwds() -> list[str]:
    """每一份 AGENTS.md 所在的目录。更深的 cwd 拼到的是最近祖先那一串，不会更长，所以这就是全集。"""
    return [f.rsplit("/", 1)[0] if "/" in f else "." for f in agents_files()]


def skill_files() -> list[str]:
    """仓库跟踪的每一份 `SKILL.md`：技能触发时整份进上下文，和 AGENTS.md 一样是常驻成本。"""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "SKILL.md", "*/SKILL.md"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return sorted(f for f in out.split("\0") if f and (ROOT / f).is_file())


def ceiling_targets() -> list[str]:
    """要登记棘轮上限的文件：每份跟踪的 AGENTS.md 与 SKILL.md。"""
    return agents_files() + skill_files()


def load_ceilings() -> dict[str, int]:
    return json.loads(CEILINGS_FILE.read_text(encoding="utf-8"))["ceilings"]


def ceiling_violations() -> list[str]:
    """越过上限的、没登记上限的、登记了却已不存在的——三种都算违规。"""
    ceilings = load_ceilings()
    targets = ceiling_targets()
    out = [f"{f}: 没有登记上限（--write-ceilings）" for f in targets if f not in ceilings]
    out += [f"{f}: 登记了上限但文件不在了" for f in ceilings if f not in targets]
    for f in targets:
        if f in ceilings and codex_size(f) > ceilings[f]:
            out.append(f"{f}: {codex_size(f):,} B > 上限 {ceilings[f]:,} B")
    return out


def write_ceilings() -> dict[str, int]:
    ceilings = {f: -(-(codex_size(f) + CEILING_SLACK) // 256) * 256 for f in ceiling_targets()}
    doc = {
        "_说明": "每份常驻指令的棘轮上限（CRLF 口径字节）。要长大就在同一个 PR 里跑 "
        "`python scripts/dev/agents_budget.py --write-ceilings` 并提交本文件，PR 正文写明为什么；"
        "缩小了也重写一次把上限降下来。见 docs/rules/README.md。",
        "ceilings": ceilings,
    }
    CEILINGS_FILE.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ceilings


def sheet_rows(rel: str) -> list[tuple[int, list[str]]]:
    """速查表「改到 | 主题 | 必守要点」那张表的数据行：(行号, 各格)。"""
    rows, in_table = [], False
    for i, ln in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
        if ln.startswith("| 改到 |"):
            in_table = True
            continue
        if in_table and ln.startswith("| ---"):
            continue
        if in_table and ln.startswith("|"):
            rows.append((i, [c.strip() for c in ln.strip()[1:-1].split(" | ")]))
        elif in_table:
            in_table = False
    return rows


def oversized_cells(sheets: list[str]) -> list[str]:
    out = []
    for rel in sheets:
        for line, cells in sheet_rows(rel):
            for name, cell in zip(("改到", "主题", "必守要点"), cells):
                n = len(cell.encode("utf-8"))
                if n > SHEET_CELL_MAX:
                    out.append(f"{rel}:{line}「{name}」{n} B > {SHEET_CELL_MAX} B")
    return out


def stale_route_tables() -> list[str]:
    """速查表路由表相对细则末尾 `agents-route` 块是否过期（复用 `gen_agents_sheets.py` 的判定）。

    空列表 = 都是新的；块写得不合规矩时把错误信息当成一条违规返回。
    """
    spec = importlib.util.spec_from_file_location(
        "gen_agents_sheets", ROOT / "scripts" / "dev" / "gen_agents_sheets.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        diff = mod.stale()
    except mod.RouteError as exc:
        return [str(exc)]
    if not diff:
        return []
    return ["速查表路由表已过期（跑 `python scripts/dev/gen_agents_sheets.py --write`）"] + diff


def rule_layers() -> list[str]:
    """`docs/rules/` 下的每一层（子目录名）。"""
    return sorted(p.name for p in (ROOT / "docs" / "rules").iterdir() if p.is_dir())


def report() -> dict:
    sheets = []
    for s in agents_files():
        n = size(s)
        budget = BUDGET_ROOT if s == "AGENTS.md" else BUDGET_SHEET
        sheets.append(
            {
                "file": s,
                "bytes": n,
                "baseline": AUDIT_BASELINE_BYTES.get(s),
                "budget": budget,
                "verdict": verdict(n, budget),
            }
        )
    rules = []
    for layer in rule_layers():
        for p in sorted((ROOT / "docs" / "rules" / layer).glob("*.md")):
            rules.append({"file": p.relative_to(ROOT).as_posix(), "bytes": p.stat().st_size})
    chains = []
    for name, files in TASK_CHAINS.items():
        total = sum(size(f) for f in files)
        chains.append(
            {"task": name, "files": files, "bytes": total, "verdict": verdict(total, BUDGET_CHAIN)}
        )
    codex = []
    for cwd in codex_cwds():
        files = codex_chain(cwd)
        total = sum(codex_size(f) for f in files)
        codex.append(
            {
                "cwd": cwd,
                "files": files,
                "bytes": total,
                "within_default_cap": total <= CODEX_DEFAULT_CAP,
            }
        )
    return {"sheets": sheets, "rules": rules, "chains": chains, "codex": codex}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--strict", action="store_true", help="任一速查表或任务链超预算即退 1")
    ap.add_argument(
        "--check", action="store_true", help="任一 Codex 自动拼接越过默认上限即退 1（硬线，#608）"
    )
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument(
        "--write-ceilings",
        action="store_true",
        help=f"按现状（+{CEILING_SLACK} B 余量）重写 {CEILINGS_FILE.relative_to(ROOT).as_posix()}",
    )
    args = ap.parse_args(argv)
    # Windows 上 stdout 被重定向成管道时会退回系统区域编码（cp1252/cp936），报告里全是中文。
    # 放在 main 里：被 import（门禁用例）时不去动调用方的 stdout。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if args.write_ceilings:
        for f, n in write_ceilings().items():
            print(f"  {n:>7,} B  {f}")
        return 0
    r = report()
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print("== 速查表（每次会话都读的那一层）")
        for s in r["sheets"]:
            base = f"（审计基线 {s['baseline']:,}）" if s["baseline"] else ""
            print(
                f"  {s['bytes']:>7,} B  预算 {s['budget'][0]:,}–{s['budget'][1]:,}  {s['verdict']:<12} {s['file']}{base}"
            )
        print("== 细则（按需读）")
        for layer in rule_layers():
            items = [x for x in r["rules"] if x["file"].startswith(f"docs/rules/{layer}/")]
            total = sum(x["bytes"] for x in items)
            biggest = max(items, key=lambda x: x["bytes"]) if items else None
            print(
                f"  {layer:<9} {len(items):>2} 份，共 {total:,} B；最大 {biggest['bytes']:,} B {biggest['file']}"
                if biggest
                else f"  {layer}: 0"
            )
        print("== 按任务的加载链（根 + 速查表 + 细则）")
        for c in r["chains"]:
            print(
                f"  {c['bytes']:>7,} B  预算 {BUDGET_CHAIN[0]:,}–{BUDGET_CHAIN[1]:,}  {c['verdict']:<12} {c['task']}"
            )
            for f in c["files"]:
                print(f"           {size(f):>7,}  {f}")
        print(
            f"== Codex 自动拼接（默认 project_doc_max_bytes = {CODEX_DEFAULT_CAP:,}；按 CRLF 口径）"
        )
        for c in r["codex"]:
            flag = "ok" if c["within_default_cap"] else "OVER（后面的文件会被截掉）"
            print(f"  {c['bytes']:>7,} B  {flag:<28} cwd={c['cwd']}  ← {' + '.join(c['files'])}")
    over = [s for s in r["sheets"] if s["verdict"] != "ok"] + [
        c for c in r["chains"] if c["verdict"] != "ok"
    ]
    if args.strict and over:
        return 1
    if args.check:
        problems = ceiling_violations() + oversized_cells(SHEETS_WITH_TABLE) + stale_route_tables()
        for msg in problems:
            print(f"!! {msg}", file=sys.stderr)
        if problems or not all(c["within_default_cap"] for c in r["codex"]):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
