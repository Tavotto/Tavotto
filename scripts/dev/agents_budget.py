"""按**实际加载路径**量指导文档的体积，对照试行预算打印一张表。

试行预算只报告，不当门禁（`--strict` 才按预算退非零）。理由写在 docs/rules/README.md：预算是
本项目试行的数字，超标先报告、不为达标粗暴删规则；量的是原始字节，**不是 token 数**，
也不代表任何账户的实际扣费。**唯一的硬线是 Codex 自动拼接不越过 32 KiB**（#608）——那不是
我们定的预算，是 Codex 的默认上限，越过就静默截掉末尾；`--check` 按它退非零，
`tests/test_agents_rules_index.py` 钉着同一条。

两种加载方式各量一份：

* **Codex**：沿「仓库根 → cwd」拼接每层 `AGENTS.md`，默认 `project_doc_max_bytes` =
  32 KiB，到顶就停——所以按 cwd 给出「自动加载的那一串」有没有越过上限。
* **按任务读**（Claude Code 与 Codex 里按根的指示手动读的那条路）：根 + 本层速查表 +
  这项任务要读的细则，就是这次会话为规则付出的字节。

用法：`python scripts/dev/agents_budget.py [--strict] [--check] [--json]`
"""

from __future__ import annotations

import argparse
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

#: 速查表、细则层、Codex 拼接的 cwd 都从仓库现状推出来（`agents_files()` / `rule_layers()`），
#: 不写死清单：写死的清单漏掉新加的一层时报告照样「全 ok」（#620 评审）。


def size(rel: str) -> int:
    p = ROOT / rel
    return p.stat().st_size if p.is_file() else 0


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
        total = sum(size(f) for f in files)
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
    args = ap.parse_args(argv)
    # Windows 上 stdout 被重定向成管道时会退回系统区域编码（cp1252/cp936），报告里全是中文。
    # 放在 main 里：被 import（门禁用例）时不去动调用方的 stdout。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
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
        print(f"== Codex 自动拼接（默认 project_doc_max_bytes = {CODEX_DEFAULT_CAP:,}）")
        for c in r["codex"]:
            flag = "ok" if c["within_default_cap"] else "OVER（后面的文件会被截掉）"
            print(f"  {c['bytes']:>7,} B  {flag:<28} cwd={c['cwd']}  ← {' + '.join(c['files'])}")
    over = [s for s in r["sheets"] if s["verdict"] != "ok"] + [
        c for c in r["chains"] if c["verdict"] != "ok"
    ]
    if args.strict and over:
        return 1
    if args.check and not all(c["within_default_cap"] for c in r["codex"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
