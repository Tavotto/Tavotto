"""两条 foundation 证据 workflow 里的 `actions/setup-python`：每个 job 只有第一次（矩阵 / 宿主那一档）可以动 PATH。

#498 第一轮：`foundation-u06-rendercore.yml` 为 FO32 的「用户本来就有的项目环境」再 setup 一个 3.12，它把自己放到 PATH
最前，后面 `python -m venv` 建出来的**应用** venv 也成了 3.12——矩阵声明的 3.10 / 3.13 一条都没真跑，夹具「项目 Python ≠
应用 Python」的前提四腿全部不成立。第二次起的 setup-python 必须 `update-environment: false`，只经 `outputs.python-path`
点名（Codex #498 P1）。夹具那层另有一道防线（前提不满足 → skip 写 not_run），这里守的是 workflow 那层。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ("foundation-u06-rendercore.yml", "private-python-targets.yml")
#: 每条 workflow 里 setup-python 的次数下限——保证判据量在非空集合上（rendercore 至少两次，第二次才是被守的那次）
MIN_SETUPS = {"foundation-u06-rendercore.yml": 2, "private-python-targets.yml": 1}


def _setup_python_steps(text: str) -> dict[str, list[str]]:
    """{job id: [setup-python 步骤正文, …]}，按文件里的先后顺序。"""
    _, jobs = text.split("\njobs:\n", 1)
    out: dict[str, list[str]] = {}
    for block in re.split(r"(?m)^  (?=[A-Za-z0-9_-]+:\s*$)", jobs):
        if not block.strip():
            continue
        job_id = block.split(":", 1)[0].strip()
        steps = re.split(r"(?m)^      - ", block)[1:]
        out[job_id] = [s for s in steps if re.match(r"uses: actions/setup-python@", s)]
    return out


@pytest.mark.parametrize("name", WORKFLOWS)
def test_only_the_first_setup_python_of_a_job_may_touch_path(name):
    text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    per_job = _setup_python_steps(text)
    total = sum(len(v) for v in per_job.values())
    assert total >= MIN_SETUPS[name], (name, per_job)
    for job_id, steps in per_job.items():
        for i, step in enumerate(steps):
            has_flag = re.search(r"(?m)^\s*update-environment: false\s*$", step) is not None
            if i == 0:
                assert not has_flag, (
                    f"{name}:{job_id} 第一次 setup-python 是应用 / 宿主那一档，得留在 PATH 上"
                )
            else:
                assert has_flag, (
                    f"{name}:{job_id} 第 {i + 1} 次 setup-python 没有 `update-environment: false`——"
                    "它会顶掉矩阵解释器，后面的 venv / pytest 跑的就不是声明的那一档"
                )
