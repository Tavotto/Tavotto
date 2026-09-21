#!/usr/bin/env python3
"""候选后端对拍：把旧 facade 的契约用例在 `TAVOTTO_RENDER_BACKEND=rendercore` 下逐字重跑（统一实施包 U08，ADR 0067）。

    <rc-venv>/bin/python scripts/dev/u08_parity.py [--out docs/implementation/tavotto-foundation/evidence/u08]

清单只有一份：`U00_FACADE_LEDGER.json` 的 `candidate_parity`——哪些套件重跑、哪些用例在候选下 deselect。
**运行器拒绝没有替代证据的 deselect**（每条都要 `replacement` 指向真实存在的候选侧用例，按 AST 找函数名），
deselect 的用例本身也必须存在（防止「deselect 一个早就改名的用例」让清单看起来仍然成立）。用户合同一条不删
——这里删的是实现特定断言（monkeypatch 旧 facade 内部 / base-14 名字 / 旧字体 oracle / 旧栅格器阈值）。

结果写成 `parity.json`（pytest 退出码、通过 / 失败 / skip 计数、deselect 计数、候选包版本、字体政策版本），
是 U08 evidence 的一部分；退出码 = pytest 的退出码（0 才算对拍通过），skip 不是绿——`skipped` 单独记。
`--check` 只做清单核对（不跑 pytest），给主 `.venv` 的看护用例用。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# 两条流都钉成 UTF-8：Windows 上被捕获 / 重定向时中文输出会静默丢掉（tests/test_windows_regressions.py）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs" / "implementation" / "tavotto-foundation" / "U00_FACADE_LEDGER.json"
DEFAULT_OUT = ROOT / "docs" / "implementation" / "tavotto-foundation" / "evidence" / "u08"


def _functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def check_plan(plan: dict) -> list[str]:
    """清单自洽：套件存在、deselect 的用例存在、每条都有存在的替代证据。回问题列表（空 = 合格）。"""
    problems: list[str] = []
    for suite in plan["suites"]:
        if not (ROOT / suite).is_file():
            problems.append(f"套件不存在: {suite}")
    cache: dict[Path, set[str]] = {}

    def exists(node: str) -> bool:
        file, _, func = node.partition("::")
        path = ROOT / file
        if not path.is_file():
            return False
        if path not in cache:
            cache[path] = _functions(path)
        return func in cache[path]

    for item in plan["deselected"]:
        if not exists(item["test"]):
            problems.append(f"deselect 的用例不存在: {item['test']}")
        if item.get("class") != "implementation_specific":
            problems.append(
                f"只许 deselect 实现特定断言，{item['test']} 标的是 {item.get('class')!r}"
            )
        if not item.get("reason", "").strip():
            problems.append(f"没有理由: {item['test']}")
        repl = item.get("replacement") or []
        if not repl:
            problems.append(f"没有替代证据: {item['test']}")
        for node in repl:
            if not exists(node):
                problems.append(f"替代证据不存在: {node}（for {item['test']}）")
    return problems


def _versions() -> dict:
    out: dict = {"python": sys.version.split()[0]}
    from importlib import metadata

    for mod, dist in (
        ("pikepdf", "pikepdf"),
        ("pypdfium2", "pypdfium2"),
        ("uharfbuzz", "uharfbuzz"),
        ("fontTools", "fonttools"),
        ("PIL", "pillow"),
        ("pymupdf", "pymupdf"),
    ):
        try:
            ver = metadata.version(dist)
            if mod == "pypdfium2":
                m = __import__(mod)
                ver = f"{ver} (pdfium {m.PDFIUM_INFO.version})"
            out[mod] = str(ver)
        except Exception:  # noqa: BLE001 —— 记「没装」，不炸
            out[mod] = None
    try:
        from tavotto.rendercore import preview

        out["fonts_policy_version"] = preview.fonts_policy_version()
    except Exception:  # noqa: BLE001
        out["fonts_policy_version"] = None
    return out


def _counts(junit: Path) -> dict:
    """计数从 junit XML 读（`-q` 下最后那行统计不一定打印；数字要从结构化产物来，不从终端猜）。"""
    import xml.etree.ElementTree as ET

    root = ET.parse(junit).getroot()
    suites = root.iter("testsuite")
    total = failures = errors = skipped = 0
    for s in suites:
        total += int(s.get("tests", 0))
        failures += int(s.get("failures", 0))
        errors += int(s.get("errors", 0))
        skipped += int(s.get("skipped", 0))
    return {
        "passed": total - failures - errors - skipped,
        "failed": failures,
        "errors": errors,
        "skipped": skipped,
        "total": total,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--check", action="store_true", help="只核清单，不跑 pytest")
    args = ap.parse_args()
    plan = json.loads(LEDGER.read_text(encoding="utf-8"))["candidate_parity"]
    problems = check_plan(plan)
    if problems:
        print("清单不合格：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    if args.check:
        print(
            f"清单合格：{len(plan['suites'])} 个套件，{len(plan['deselected'])} 条 deselect 各有替代证据"
        )
        return 0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    junit = out_dir / "parity-junit.xml"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *plan["suites"],
        "-q",
        "-p",
        "no:warnings",
        "-rfEs",
        f"--junitxml={junit}",
    ]
    for item in plan["deselected"]:
        cmd += ["--deselect", item["test"]]
    env = {
        **os.environ,
        "TAVOTTO_RENDER_BACKEND": "rendercore",
        "TAVOTTO_NO_TELEMETRY": "1",
        "PYTHONPATH": str(ROOT / "src"),
    }
    t0 = time.time()
    proc = subprocess.run(
        cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8"
    )
    elapsed = round(time.time() - t0, 1)
    counts = _counts(junit) if junit.is_file() else {"error": "junit 没写出来"}
    counts["deselected"] = len(plan["deselected"])
    failed = [
        line.split(" ", 1)[1].split(" - ")[0]
        for line in proc.stdout.splitlines()
        if line.startswith("FAILED ") or line.startswith("ERROR ")
    ]
    skipped = [line for line in proc.stdout.splitlines() if line.startswith("SKIPPED ")]
    report = {
        "schema": 1,
        "kind": "u08_candidate_parity",
        "backend": "rendercore",
        "suites": plan["suites"],
        "deselected": [item["test"] for item in plan["deselected"]],
        "exit_code": proc.returncode,
        "counts": counts,
        "failed": failed,
        "skipped": skipped,
        "elapsed_s": elapsed,
        "versions": _versions(),
        "platform": sys.platform,
    }
    (out_dir / "parity.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    # 完整输出留在 evidence 旁边（不进 git：.gitignore 挡 *.log），排查「为什么红」不用重跑
    (out_dir / "parity.log").write_text(
        proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8"
    )
    tail = "\n".join(proc.stdout.splitlines()[-15:])
    print(tail)
    print(f"parity: exit={proc.returncode} {counts} ({elapsed}s) → {out_dir / 'parity.json'}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
