"""性能 fixture（计划 P7.7）：5 / 30 / 100 个脚本的项目，默认扫描的**确定性**代价。

量法与数字见 `scripts/dev/import_origin_perf.py`（审计事件计数，独立于扫描自己的预算账）。这里只断言**计数**，
不断言耗时（耗时随机器抖动，只记录在 FINAL_ACCEPTANCE.md 里）：

* 额外子进程 / 网络事件恒为 0（默认扫描不起候选环境、不起登录 Shell、不跑用户脚本、不出网）；
* 项目里的数据文件与 `.venv` 里的文件一个都没被打开（只列目录）；
* 打开的文件数与总字节随脚本数**线性**增长，且不超过项目源码总字节加一个常数（依赖声明、注册表等）；
* 同一份 cache 的第二遍一次解析都没有（命中率 100%）；
* 静态来源解析读到的恰好是各脚本的导入闭包（脚本本身 + 共享模块 + 包），与脚本数无关的模块不读。

跑在子进程里：审计钩子装上就拆不掉，不该留在 pytest 进程里拖慢后面的所有用例。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dev" / "import_origin_perf.py"

#: 依赖声明 / 注册表 / 项目标记这类固定读取的上界（字节）；与脚本数无关
CONSTANT_READS = 16 * 1024
CONSTANT_OPENS = 12


@pytest.fixture(scope="module")
def result() -> dict:
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--repeats", "1"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        timeout=300,
        check=False,
    )
    assert out.returncode == 0, out.stderr or out.stdout
    return json.loads(out.stdout)


def _rows(result):
    return {r["scripts"]: r for r in result["rows"]}


def test_the_three_sizes_ran_and_every_scan_completed(result):
    rows = _rows(result)
    assert sorted(rows) == [5, 30, 100]
    for n, r in rows.items():
        assert r["scan_state"] == "complete", (n, r["scan_state"])
        assert r["scan_scripts_found"] >= n


def test_default_scanning_starts_no_process_and_opens_no_network(result):
    for r in result["rows"]:
        assert r["extra_subprocesses"] == 0, r["scripts"]
        for key in ("default_scan_cold", "default_scan_warm", "static_resolve_all"):
            assert r[key]["forbidden_events"] == [], (r["scripts"], key)


def test_data_files_and_the_venv_are_listed_but_never_opened(result):
    for r in result["rows"]:
        assert r["data_or_venv_files_opened"] == [], r["scripts"]


def test_reads_are_bounded_by_the_project_sources_and_grow_linearly(result):
    rows = _rows(result)
    for r in rows.values():
        c = r["default_scan_cold"]
        assert c["open_bytes"] <= r["project_source_bytes"] + CONSTANT_READS, r["scripts"]
        assert c["opens"] <= r["scan_scripts_found"] + CONSTANT_OPENS, r["scripts"]
        # 扫描自己的账与独立观测对得上：它记的源码字节不多于我们看到打开的字节
        assert r["scan_budget_source_bytes"] <= c["open_bytes"]
    # 线性：多出来的每个脚本恰好多一次打开（不是平方）
    for small, big in ((5, 30), (30, 100)):
        d_found = rows[big]["scan_scripts_found"] - rows[small]["scan_scripts_found"]
        d_opens = (
            rows[big]["default_scan_cold"]["opens"] - rows[small]["default_scan_cold"]["opens"]
        )
        assert d_opens == d_found, (small, big, d_opens, d_found)


def test_the_second_pass_with_the_same_cache_parses_nothing(result):
    for r in result["rows"]:
        assert r["default_scan_cold"]["compiles"] >= r["scan_scripts_found"]
        assert r["default_scan_warm"]["compiles"] == 0, r["scripts"]
        assert r["cache_hit_rate"] == 1.0
        # 增量也不重读脚本正文：打开数只剩固定的几个声明文件
        assert r["default_scan_warm"]["opens"] <= CONSTANT_OPENS


def test_static_resolution_reads_exactly_the_import_closures(result):
    for r in result["rows"]:
        # 每个脚本一个文件 + 共享的 shared.py / lab/__init__.py / lab/util.py；40 个无关模块一个都没读
        assert r["static_resolve_all"]["files_read"] == r["scripts"] + 3, r["scripts"]
