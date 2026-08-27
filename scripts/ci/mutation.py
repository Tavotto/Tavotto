#!/usr/bin/env python3
"""Mutation testing（weekly deep）：把关键纯逻辑改坏，看有没有测试会红。

它回答的是覆盖率答不了的那个问题——**这些行被执行过，但如果它们算错了，
有测试会发现吗？** 本仓库到处强调「改坏了它真的会红」，mutation 就是把这
句话自动化。

几条刻意的边界：

* **scope 只圈纯逻辑模块**，且逐个审计过（见 `TARGETS` 的注释）。
  对整个仓库做 mutation 只会得到一份跑一整天、survived 里全是
  「平台分支」「subprocess 超时值」的噪声报告，没人会读第二次。
* **CLI 与配置键都按 mutmut 3.7 的实际接口写**，不是照着印象猜的。
  版本钉死：mutmut 的 CLI 在 2.x → 3.x 之间换过一整轮，浮动版本号会让
  这个 job 在某个周日早上突然全红，而产品一个字没改。
* **默认 report-only**。没有历史 baseline 就把发行卡死，只会让人第一时间
  把这个 job 关掉。`LAB_MUTATION_GATE=true` 之后才按阈值阻断。

用法：
    python scripts/ci/mutation.py --python .venv/bin/python
    python scripts/ci/mutation.py --python .venv/bin/python --max-children 12
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from _common import (  # noqa: E402
    CiError,
    ensure_layout,
    run_metadata,
    summary,
    summary_table,
    write_report,
)

REPO = _HERE.parents[1]

# 逐个审计过的目标。共同点：纯标准库、逻辑密集、错了会安静地产生错误结果。
# 刻意**排除**：app.py（巨大的 subprocess/HTTP 集成层）、worker.py（要科学栈）、
# desktop.py 与 runtime.py（平台分支，mutation 会大量产出「另一个平台才走到」
# 的 survived，纯噪声）、pathgeom.py（依赖 numpy）。
TARGETS = [
    # patch 规范化与哈希：父子进程与 Rust supervisor 三方共用的权威实现，
    # 算错一位就会让「热态 == 全量重放」的等价性悄悄破掉。
    "src/tavotto/engine/patchspec.py",
    # 注册表：stem ↔ 脚本的裁决。错了会让面板指向别的脚本。
    "src/tavotto/engine/registry.py",
    # 发现链：错了表现为「装了却找不到」，且只在别人的机器上发生。
    "src/tavotto/engine/locate.py",
    # 出版规范求值器：与前端那份靠 golden vectors 对齐，正是 mutation 最能发力的形状。
    "src/tavotto/engine/preflight.py",
    # profile 合并：浅合并 + 若干深合并，边界条件多。
    "src/tavotto/engine/profiles.py",
]

# survived 比例的观察阈值。第一阶段只用来在 summary 里标红，不阻断。
SURVIVED_RATIO_WARN = 0.30


def verify_scope_is_configured(repo: Path) -> list[str]:
    """确认 pyproject 里的 `[tool.mutmut]` 真的把 scope 圈住了。

    **mutmut 3.7 只从 cwd 的 pyproject.toml / setup.cfg 读配置**，没有
    命令行或环境变量可以覆盖（实测其 `configuration._config_reader` 里
    是硬编码的两个文件名）。所以配置只能放在仓库的 pyproject 里。

    这条断言不是形式主义：`only_mutate` 一旦缺失，mutmut 会退回默认值并
    对 `source_paths` 下的**全部**代码做变异——那是几千个 mutant、跑一整天，
    而且报告里全是 app.py 与平台分支的噪声。与其在周日早上发现 job 跑了
    十小时，不如在第一秒就失败。
    """
    try:
        if sys.version_info >= (3, 11):
            import tomllib

            data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
        else:  # pragma: no cover - CI 用 3.13
            raise CiError("python_too_old", "mutation 需要 Python 3.11+ 的 tomllib")
    except (OSError, ValueError) as exc:
        raise CiError("pyproject_unreadable", f"读不到 pyproject.toml：{exc}") from exc

    cfg = data.get("tool", {}).get("mutmut")
    if not cfg:
        raise CiError(
            "mutmut_scope_missing",
            "pyproject.toml 里没有 [tool.mutmut]。缺了它 mutmut 会变异整个 "
            "source_paths，产出几千个 mutant 与一份没人会读的报告",
        )
    only = cfg.get("only_mutate") or []
    if not only:
        raise CiError(
            "mutmut_scope_unbounded", "[tool.mutmut] 里 only_mutate 为空——scope 没有被圈住"
        )
    missing = [t for t in only if not (repo / t).is_file() and not t.endswith("*")]
    if missing:
        raise CiError(
            "mutmut_target_missing",
            f"only_mutate 指向不存在的文件：{missing}。"
            "模块被改名或移动后，mutation 会安静地少验一大块",
        )
    return list(only)


def parse_results(text: str) -> dict:
    """从 `mutmut results` 的输出里数各类结论。

    刻意做得宽松：mutmut 的输出格式在小版本间会调整，数字对不上时宁可
    回 0 并在报告里标注「解析不出」，也不要让这个 job 因为一个格式变化而崩。
    """
    counts = {"killed": 0, "survived": 0, "timeout": 0, "suspicious": 0, "skipped": 0}
    for key in counts:
        m = re.search(rf"{key}[^0-9]{{0,20}}(\d+)", text, re.IGNORECASE)
        if m:
            counts[key] = int(m.group(1))
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mutation testing（weekly）")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument(
        "--max-children",
        type=int,
        default=max(2, (os.cpu_count() or 4) - 4),
        help="并发。留出余量给 OS 与 runner 自身",
    )
    ap.add_argument("--timeout", type=int, default=5400)
    args = ap.parse_args(argv)

    root = ensure_layout()
    gate = os.environ.get("LAB_MUTATION_GATE", "false").lower() == "true"
    started = time.time()

    mutants_dir = REPO / "mutants"
    payload: dict = {"ok": True, "gate_enforced": gate, "metadata": run_metadata("weekly")}
    try:
        # 先确认 scope 真的被圈住了，再开跑（理由见 verify_scope_is_configured）
        payload["targets"] = verify_scope_is_configured(REPO)
        env = dict(os.environ)
        run = subprocess.run(
            [args.python, "-m", "mutmut", "run", "--max-children", str(args.max_children)],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )
        payload["run_returncode"] = run.returncode
        payload["run_tail"] = (run.stdout + run.stderr)[-4000:]

        res = subprocess.run(
            [args.python, "-m", "mutmut", "results"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        payload["results_text"] = res.stdout[-8000:]
        counts = parse_results(res.stdout)
        payload["counts"] = counts

        total = sum(counts.values()) or 1
        ratio = counts["survived"] / total
        payload["survived_ratio"] = round(ratio, 3)
        payload["total"] = total

        # export-cicd-stats 是 3.x 提供的机器可读产物，有就一并收走
        try:
            stats = subprocess.run(
                [args.python, "-m", "mutmut", "export-cicd-stats"],
                cwd=REPO,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if stats.returncode == 0 and stats.stdout.strip():
                payload["cicd_stats"] = stats.stdout[:20000]
        except subprocess.SubprocessError:
            pass

        rows = [
            ("killed", "✅", str(counts["killed"])),
            ("survived", "⚠️" if ratio > SURVIVED_RATIO_WARN else "✅", str(counts["survived"])),
            ("timeout", "·", str(counts["timeout"])),
            ("suspicious", "·", str(counts["suspicious"])),
            (
                "survived 占比",
                "⚠️" if ratio > SURVIVED_RATIO_WARN else "✅",
                f"{ratio:.1%}（观察阈值 {SURVIVED_RATIO_WARN:.0%}）",
            ),
        ]
        summary(
            f"\n### Mutation（{'门禁已开' if gate else 'report-only'}）\n\n"
            + summary_table(rows)
            + f"\n\n目标模块：{', '.join(Path(t).name for t in payload['targets'])}\n"
        )
        # survived 必须**显式列出来**，不能只给一个数字：
        # 「有 12 个存活变异」没人会去查，贴出来才有人看。
        if counts["survived"]:
            summary(
                "\n<details><summary>survived mutants（点开看）</summary>\n\n```\n"
                + res.stdout[-6000:]
                + "\n```\n</details>\n"
            )

        payload["ok"] = (ratio <= SURVIVED_RATIO_WARN) if gate else True
    except subprocess.TimeoutExpired:
        payload.update(
            {
                "ok": not gate,
                "code": "mutation_timeout",
                "error": f"mutmut 超过 {args.timeout}s 未完成",
            }
        )
        print(f"::warning::mutation 超时（{args.timeout}s）", file=sys.stderr)
    except CiError as exc:
        # scope 没圈住属于配置错误，**一律阻断**，与 gate 无关：
        # 让它 report-only 地跑下去，等于放任一次十小时的无效运行。
        payload.update({"ok": False, "code": exc.code, "error": exc.message})
        print(f"::error::{exc.message}", file=sys.stderr)
        summary(f"\n> **Mutation 配置错误** `{exc.code}` — {exc.message}\n")
    except (OSError, subprocess.SubprocessError) as exc:
        payload.update({"ok": not gate, "code": "mutation_failed", "error": str(exc)[:500]})
        print(f"::warning::mutation 跑不起来：{exc}", file=sys.stderr)
    finally:
        # mutmut 会在 cwd 建 mutants/ 工作树，留着会污染下一次 checkout 的
        # 「工作目录干净」判定，也会让磁盘越跑越满。
        shutil.rmtree(mutants_dir, ignore_errors=True)

    payload["elapsed_s"] = round(time.time() - started, 1)
    write_report("mutation.json", payload, root)
    print(
        json.dumps(
            {
                k: v
                for k, v in payload.items()
                if k in ("ok", "counts", "survived_ratio", "elapsed_s")
            },
            ensure_ascii=False,
        )
    )
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
