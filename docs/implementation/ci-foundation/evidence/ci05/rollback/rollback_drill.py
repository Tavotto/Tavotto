"""CI05 回退演练（本机）：在一次性 worktree（HEAD = a174eb61）上按各 PR 文档写的回退方式改 ci.yml，
跑 actionlint + 合同测试，记录退出码与红掉的用例；每条回退各用一个新 worktree，跑完就删。"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path("/Volumes/Projects/tavotto-wt/ci-foundation")
PY = REPO / ".venv/bin/python"
BASE = "a174eb61"
OUT = Path(sys.argv[1])
TESTS = [
    "tests/test_merge_queue_workflows.py",
    "tests/test_ci_baseline.py",
    "tests/test_support_matrix.py",
    "tests/test_e2e_leg_topology.py",
    "tests/test_pytest_shard.py",
    "tests/test_playwright_shard_check.py",
]


def sub1(text, old, new, label):
    n = text.count(old)
    assert n >= 1, f"{label}: 目标串不存在"
    return text.replace(old, new), n


def rollback_ci03a(y):
    y, n1 = sub1(
        y,
        '        python: ["3.10", "3.13", "3.14"]\n        shard: [1, 2]\n',
        '        python: ["3.10", "3.13", "3.14"]\n',
        "backend-fast shard 轴",
    )
    y, n2 = sub1(
        y,
        "        os: [macos-latest, windows-latest]\n        shard: [1, 2]\n",
        "        os: [macos-latest, windows-latest]\n",
        "backend-platforms shard 轴",
    )
    y, n3 = sub1(
        y,
        '          python -m pytest --shard=${{ matrix.shard }}/2\n          --shard-manifest="${{ runner.temp }}/shard-manifest.json"\n',
        "          python -m pytest\n",
        "pytest 命令",
    )
    assert n3 == 2, n3
    # artifact 名里的片号也要去掉，否则 matrix 展开后两个 python 档的名字仍唯一、但表达式引用了不存在的 matrix.shard
    y = y.replace(
        "-shard${{ matrix.shard }}\n          path: |\n            ${{ runner.temp }}/shard-manifest.json",
        "\n          path: |\n            ${{ runner.temp }}/shard-manifest.json",
    )
    return (
        y,
        "去掉两个 job 的 shard 轴；pytest 命令去掉 --shard=…/--shard-manifest=…；分片证据 artifact 名去掉片号（钩子留着）",
    )


def rollback_ci03c(y):
    start = y.index("  windows-exe-smoke:\n")
    end = y.index("  macos-app-smoke:\n")
    blk = y[start:end]
    blk, _ = sub1(blk, "    name: windows-exe-smoke (${{ matrix.shard }})\n", "", "name:")
    blk = re.sub(
        r"    strategy:\n      fail-fast: false\n      matrix:\n        include:\n(?:          - \{ shard: .*\n){2}",
        "",
        blk,
        count=1,
    )
    assert "include:" not in blk
    blk, _ = sub1(
        blk, "          pnpm e2e ${{ matrix.projects }}\n", "          pnpm e2e\n", "pnpm e2e"
    )
    blk, _ = sub1(
        blk,
        "pnpm exec playwright install ${{ matrix.browsers }}",
        "pnpm exec playwright install chromium webkit",
        "install",
    )
    blk, _ = sub1(
        blk,
        "装 web 依赖与本片的浏览器（${{ matrix.browsers }}）",
        "装 web 依赖与浏览器",
        "install step name",
    )
    # 删掉自验步骤与它的 artifact 上传步骤
    blk = re.sub(
        r"      - name: Playwright 分片自验.*?(?=      - name: Playwright 黄金路径)",
        "",
        blk,
        flags=re.S,
        count=1,
    )
    blk = re.sub(
        r"      - name: 上传分片自验证据.*?(?=\n      # 大图预览)", "", blk, flags=re.S, count=1
    )
    blk = blk.replace("，本片 ${{ matrix.projects }}）", "）")
    blk = blk.replace("-shard${{ matrix.shard }}", "")
    live = [ln for ln in blk.splitlines() if "matrix." in ln and not ln.lstrip().startswith("#")]
    assert not live, live
    return (
        y[:start] + blk + y[end:],
        "删掉 name: 与 strategy; pnpm e2e 去掉 matrix.projects; 浏览器安装写回 chromium webkit; artifact 名去掉片号; 删掉自验步骤与其 artifact（step 级 timeout 留着）",
    )


def rollback_ci01(y):
    for job in ("package", "windows-exe-smoke", "macos-app-smoke", "posix-e2e"):
        start = y.index(f"\n  {job}:\n") + 1
        m = re.compile(r"^  [a-z0-9-]+:$", re.M).search(y, start + 1)
        end = m.start() if m else len(y)
        seg, n = sub1(
            y[start:end],
            "    needs: [frontend]\n",
            "    needs: [backend-fast, frontend]\n",
            f"{job} needs",
        )
        assert n == 1, (job, n)
        y = y[:start] + seg + y[end:]
    assert y.count("    needs: [backend-fast, frontend]\n") == 4
    assert y.count("    needs: [frontend]\n") == 1  # plugin-candidate 不动
    return y, "四个重型 job 的 needs 加回 backend-fast（plugin-candidate 不动）"


def rollback_ci02(y):
    y, n = sub1(
        y,
        "pnpm exec playwright install ${{ matrix.browsers }}",
        "pnpm exec playwright install --with-deps ${{ matrix.browsers }}",
        "with-deps",
    )
    assert n == 1
    return y, "windows-exe-smoke 的 playwright install 加回 --with-deps"


DRILLS = [
    ("R1 CI03a", rollback_ci03a),
    ("R2 CI03c", rollback_ci03c),
    ("R3 CI01", rollback_ci01),
    ("R4 CI02", rollback_ci02),
]
results = []
for label, fn in DRILLS:
    wt = Path(f"/private/tmp/claude-501/ci05-rollback-{label.split()[0]}")
    if wt.exists():
        shutil.rmtree(wt)
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(wt), BASE],
        cwd=REPO,
        check=True,
        capture_output=True,
    )
    try:
        ci = wt / ".github/workflows/ci.yml"
        before = ci.read_text("utf-8")
        after, what = fn(before)
        assert after != before
        ci.write_text(after, "utf-8")
        diffstat = (
            subprocess.run(["git", "diff", "--stat"], cwd=wt, capture_output=True, text=True)
            .stdout.strip()
            .splitlines()[-1]
        )
        al = subprocess.run(
            ["/opt/homebrew/bin/actionlint", ".github/workflows/ci.yml"],
            cwd=wt,
            capture_output=True,
            text=True,
        )
        env = {
            "PYTHONPATH": f"{wt}/src:{wt}",
            "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
            "HOME": "/Users/jiaqi",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        pt = subprocess.run(
            [str(PY), "-m", "pytest", *TESTS, "-p", "no:cacheprovider", "--no-header", "-rf"],
            cwd=wt,
            capture_output=True,
            text=True,
            env=env,
        )
        failed = sorted(set(re.findall(r"^FAILED (\S+)", pt.stdout, flags=re.M)))
        summary = next(
            (
                ln
                for ln in reversed(pt.stdout.splitlines())
                if re.search(r"\d+ (passed|failed)", ln)
            ),
            "",
        )
        results.append(
            {
                "drill": label,
                "what": what,
                "diffstat": diffstat,
                "actionlint_rc": al.returncode,
                "actionlint_out": (al.stdout + al.stderr).strip()[:600],
                "pytest_rc": pt.returncode,
                "pytest_summary": summary,
                "failed_tests": failed,
            }
        )
        print(
            label,
            "|",
            diffstat,
            "| actionlint rc",
            al.returncode,
            "| pytest rc",
            pt.returncode,
            summary,
        )
        for f in failed:
            print("     RED", f)
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt)],
            cwd=REPO,
            check=True,
            capture_output=True,
        )
# 对照：不改任何东西，同一组测试在干净 worktree 上必须全绿
wt = Path("/private/tmp/claude-501/ci05-rollback-R0")
if wt.exists():
    shutil.rmtree(wt)
subprocess.run(
    ["git", "worktree", "add", "--detach", str(wt), BASE], cwd=REPO, check=True, capture_output=True
)
try:
    env = {
        "PYTHONPATH": f"{wt}/src:{wt}",
        "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
        "HOME": "/Users/jiaqi",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    pt = subprocess.run(
        [str(PY), "-m", "pytest", *TESTS, "-p", "no:cacheprovider", "--no-header"],
        cwd=wt,
        capture_output=True,
        text=True,
        env=env,
    )
    al = subprocess.run(
        ["/opt/homebrew/bin/actionlint", ".github/workflows/ci.yml"],
        cwd=wt,
        capture_output=True,
        text=True,
    )
    summary = next(
        (ln for ln in reversed(pt.stdout.splitlines()) if re.search(r"\d+ (passed|failed)", ln)), ""
    )
    results.insert(
        0,
        {
            "drill": "R0 对照（未改）",
            "what": "同一组测试在未改的 a174eb61 上",
            "actionlint_rc": al.returncode,
            "pytest_rc": pt.returncode,
            "pytest_summary": summary,
            "failed_tests": [],
        },
    )
    print("R0", "| actionlint rc", al.returncode, "| pytest rc", pt.returncode, summary)
finally:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(wt)], cwd=REPO, check=True, capture_output=True
    )
OUT.write_text(
    json.dumps(
        {"kind": "ci05_rollback_drill_local", "base": BASE, "tests": TESTS, "results": results},
        ensure_ascii=False,
        indent=1,
    )
    + "\n",
    "utf-8",
)
print(
    "worktrees left:",
    subprocess.run(
        ["git", "worktree", "list"], cwd=REPO, capture_output=True, text=True
    ).stdout.count("ci05-rollback"),
)
