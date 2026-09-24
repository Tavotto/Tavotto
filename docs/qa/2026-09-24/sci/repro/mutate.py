"""变异反证（规范 §9）：先验落点 → 变异 → 跑用例看红 → `git checkout -- <file>` 还原 → 再跑看绿。

用法（worktree 根目录，工作树须干净——变异永远不进提交）：
    "$PY" docs/qa/2026-09-24/sci/repro/mutate.py <CASE_ID> <label> <target_file> <old> <new> -- <pytest.sh 参数...>
`old` 必须在目标文件里**恰好出现一次**（先验落点），否则不变异直接退出 2。
结果写进 logs/<CASE_ID>.log（经 run_logged.sh），并在 stdout 打一行 JSON：
    {"target_file", "mutation", "observed": "red"|"green", "red_exit", "restored_green": bool, "restored_exit"}
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]


def main() -> int:
    case, label, target, old, new, sep, *pytest_args = sys.argv[1:]
    assert sep == "--", "参数格式：... <old> <new> -- <pytest 参数>"
    path = ROOT / target
    src = path.read_text(encoding="utf-8")
    hits = src.count(old)
    if hits != 1:
        print(f"落点校验失败：{old!r} 在 {target} 出现 {hits} 次", file=sys.stderr)
        return 2
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", target], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    if dirty:
        print(f"{target} 有未提交改动，拒绝变异：{dirty}", file=sys.stderr)
        return 2
    runner = [
        "bash",
        str(HERE / "run_logged.sh"),
        case,
    ]
    path.write_text(src.replace(old, new), encoding="utf-8")
    try:
        red = subprocess.run(
            [*runner, f"mutation-{label}", "--", "bash", str(HERE / "pytest.sh"), *pytest_args],
            cwd=ROOT,
        ).returncode
    finally:
        subprocess.run(["git", "checkout", "--", target], cwd=ROOT, check=True)
    assert path.read_text(encoding="utf-8") == src, "还原失败"
    green = subprocess.run(
        [*runner, f"mutation-{label}-restored", "--", "bash", str(HERE / "pytest.sh"), *pytest_args],
        cwd=ROOT,
    ).returncode
    print(
        json.dumps(
            {
                "target_file": target,
                "mutation": f"{old!r} -> {new!r}",
                "observed": "red" if red != 0 else "green",
                "red_exit": red,
                "restored_green": green == 0,
                "restored_exit": green,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
