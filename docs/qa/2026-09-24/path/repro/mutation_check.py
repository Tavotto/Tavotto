"""§9 反证：对产品代码做一次定点变异 → 跑指定用例看是否以预期不变量变红 → `git checkout --` 还原 → 再跑确认回绿。

    python docs/qa/2026-09-24/path/repro/mutation_check.py <MUTATION_ID>

纪律：先确认目标串**恰好出现一次**（变异落在预期位置），变异子进程带 PYTHONDONTWRITEBYTECODE=1，
结论看退出码 + 失败断言文本；变异永远不进提交（跑前要求工作树里这些产品文件是干净的）。
日志写到 docs/qa/2026-09-24/path/logs/MUT-<ID>-{mutated,restored}.log。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
RUNNER = "docs/qa/2026-09-24/path/repro/qa_pytest.sh"
T = "tests/test_first_open_paths_d1.py"

MUTATIONS = {
    # PATH-02 / PATH-04：把项目根当脚本目录（project_root 模式的 cwd 换成脚本所在目录）
    "M1-root-as-scriptdir": {
        "file": "src/tavotto/engine/execspec.py",
        "old": "    elif cwd_mode == CWD_PROJECT_ROOT:\n        cwd = str(Path(figures_dir))\n",
        "new": (
            "    elif cwd_mode == CWD_PROJECT_ROOT:\n"
            "        cwd = str((Path(figures_dir) / figcapture.normalize_relative_script(script)).parent)\n"
        ),
        "tests": [
            f"{T}::test_path02_split_directories_ask_once_remember_project_root_and_survive_a_restart",
            f"{T}::test_path04_same_name_decoy_asks_without_preselection_and_reads_only_the_chosen_file",
        ],
    },
    # PATH-04：同名不同内容不再判歧义（静默按默认跑 → 沙盒回退读到脚本目录里的干扰那份）
    "M2-ambiguity-suppressed": {
        "file": "src/tavotto/engine/databinding.py",
        "old": "    elif conflicts:\n        verdict = VERDICT_AMBIGUOUS\n",
        "new": "    elif conflicts:\n        verdict = VERDICT_DEFAULT_OK\n",
        "tests": [
            f"{T}::test_path04_same_name_decoy_asks_without_preselection_and_reads_only_the_chosen_file"
        ],
    },
    # PATH-01 / PATH-06：沙盒只读回退整体失效
    "M3-fallback-off": {
        "file": "src/tavotto/engine/figcapture.py",
        "old": "        if os.path.exists(name):\n            return None  # 已经有了——脚本自己写出来的那份优先\n",
        "new": "        if True:\n            return None  # 已经有了——脚本自己写出来的那份优先\n",
        "tests": [
            f"{T}::test_path01_same_directory_csv_from_a_foreign_cwd_first_open_replay_and_export",
            f"{T}::test_path06_probe_then_read_is_a_guided_stop_in_the_sandbox_and_correct_in_project_mode",
        ],
    },
    # PATH-07：回退不再核「符号链接解开后仍在项目根内」
    "M4-fallback-follows-escaping-symlink": {
        "file": "src/tavotto/engine/figcapture.py",
        "old": "            if os.path.commonpath([real, root]) != root:\n                return None\n        except ValueError:\n            return None\n        if not os.path.isfile(cand):",
        "new": "            if False:\n                return None\n        except ValueError:\n            return None\n        if not os.path.isfile(cand):",
        "tests": [
            f"{T}::test_path07_symlinks_inside_are_read_and_escaping_ones_are_not_followed_by_the_fallback"
        ],
    },
    # PATH-09 / PATH-06：「脚本目录」模式的 cwd 换成项目根（相对写落错地方）
    "M5-project-mode-at-root": {
        "file": "src/tavotto/engine/execspec.py",
        "old": "    if cwd_mode == CWD_PROJECT:\n        cwd = str((Path(figures_dir) / figcapture.normalize_relative_script(script)).parent)\n",
        "new": "    if cwd_mode == CWD_PROJECT:\n        cwd = str(Path(figures_dir))\n",
        "tests": [f"{T}::test_path09_relative_writes_land_only_where_the_current_grant_allows"],
    },
    # PATH-03：绝对路径被当成可回退（沙盒外的绝对路径也按 basename 在脚本目录里找）
    "M6-absolute-remapped": {
        "file": "src/tavotto/engine/figcapture.py",
        "old": "            rel = _within_sandbox(name)\n            if rel is None:\n                return None\n",
        "new": "            rel = _within_sandbox(name)\n            if rel is None:\n                rel = os.path.join('..', 'data', os.path.basename(name))\n",
        "tests": [
            f"{T}::test_path03_file_relative_and_absolute_outside_are_kept_and_a_stale_absolute_path_fails"
        ],
    },
}


def run(log: str, tests: list[str]) -> int:
    return subprocess.run(["bash", RUNNER, log, *tests, "-vv"], cwd=ROOT).returncode


def main() -> int:
    mid = sys.argv[1]
    m = MUTATIONS[mid]
    path = ROOT / m["file"]
    dirty = subprocess.run(
        ["git", "status", "--porcelain", m["file"]], cwd=ROOT, capture_output=True, text=True
    ).stdout
    assert not dirty.strip(), f"{m['file']} 不干净，拒绝变异：{dirty}"
    text = path.read_text(encoding="utf-8")
    count = text.count(m["old"])
    print(f"[{mid}] 目标串出现 {count} 次（必须为 1）")
    assert count == 1, "目标串不唯一 / 不存在：变异落点不确定"
    path.write_text(text.replace(m["old"], m["new"]), encoding="utf-8")
    try:
        rc_mut = run(f"MUT-{mid}-mutated", m["tests"])
    finally:
        subprocess.run(["git", "checkout", "--", m["file"]], cwd=ROOT, check=True)
    rc_rest = run(f"MUT-{mid}-restored", m["tests"])
    print(f"[{mid}] mutated rc={rc_mut}（期望 ≠0）  restored rc={rc_rest}（期望 0）")
    return 0 if (rc_mut != 0 and rc_rest == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
