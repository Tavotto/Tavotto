"""Tavotto-FigureBench 的共享读取件：场景、编辑落点、夹具副本、原生探针调用（`tests/fixtures/figurebench/`）。

`test_figurebench_fixtures.py`（基准自证，原生 matplotlib）与 `test_figure_verifier.py`（产品验证器，Tavotto worker 的
manifest）共用同一份场景与编辑——两边量同一组变体，结论才能对拍。不 import 任何产品模块。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "figurebench"
SCENARIO_FILES = {"dev": ROOT / "scenarios.json", "holdout": ROOT / "scenarios_holdout.json"}
FIGURE_FIXTURES = (
    "line_two_series",
    "multi_panel",
    "errorbar_fit",
    "log_decay",
    "heatmap_colorbar",
    "cjk_labels",
    "grouped_bars",
)


def load(split: str) -> dict:
    return json.loads(SCENARIO_FILES[split].read_text(encoding="utf-8"))


def all_scenarios() -> list[dict]:
    return [s for split in SCENARIO_FILES for s in load(split)["scenarios"]]


def probe_scenarios() -> list[dict]:
    return [s for s in all_scenarios() if s["check"] == "probe"]


def apply_edits(src: str, edits: list[dict], sid: str) -> str:
    """按场景登记的精确文本编辑改 `figure.py`；**先验落点**：`find` 必须恰好出现 `count` 次。"""
    for e in edits:
        assert e["file"] == "figure.py", sid
        n = src.count(e["find"])
        assert n == e["count"], f"{sid}: 编辑落点 {e['find']!r} 出现 {n} 次，登记的是 {e['count']}"
        src = src.replace(e["find"], e["replace"])
    return src


def stage(work: Path, job_id: str, fixture: str, edits: list[dict], sid: str) -> Path:
    """把夹具复制进 `work/job_id` 并应用编辑，回副本里的 `figure.py`。夹具目录本身不动。"""
    dest = work / job_id
    shutil.copytree(ROOT / fixture, dest)
    script = dest / "figure.py"
    script.write_text(apply_edits(script.read_text(encoding="utf-8"), edits, sid), encoding="utf-8")
    return script


def variants() -> list[tuple[str, str, list[dict], str]]:
    """全部探针场景的变体：`(job_id, fixture, edits, scenario_id)`——每个夹具的基线 + 每个场景的参考解与反例。"""
    out = [(f"base__{fx}", fx, [], fx) for fx in FIGURE_FIXTURES]
    for s in probe_scenarios():
        out.append((f"{s['id']}__ref", s["fixture"], s["reference_solution"], s["id"]))
        out += [
            (f"{s['id']}__{n['id']}", s["fixture"], n["edits"], s["id"])
            for n in s["negative_variants"]
        ]
    return out


def run_probe(python: str, jobs: list[dict], work: Path) -> dict:
    """在 `python`（装了 matplotlib 的解释器）里一次跑完全部作业，回 `{job_id: {ok, facts|error}}`。"""
    (work / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "MPL"))}
    proc = subprocess.run(
        [python, "-B", str(ROOT / "_probe.py"), str(work / "jobs.json"), str(work / "out.json")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        env={**env, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads((work / "out.json").read_text(encoding="utf-8"))["results"]
