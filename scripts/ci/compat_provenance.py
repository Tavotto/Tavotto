"""工作流出处：研究语料的 setup receipt 与 Guardian 报告从同一处环境变量取值，并互相核对。

只依赖标准库（workflow 的 receipt 步骤用系统 Python 直接导入它）。

约定：
- `workflow` 字段缺席 = 出处功能上线前产生的旧证据，按旧规则（只核 SHA 与 case 身份）。
- `workflow` 为 null = 在非 Actions 环境（缺 GITHUB_RUN_ID / GITHUB_RUN_ATTEMPT / GITHUB_JOB）生成。
- `workflow` 为对象 = {run_id, run_attempt, job, case_id}。
- 配对：两边字段都缺席，或都为 null → 放行旧规则；其余情况一边有一边没有（缺席 / null / 对象互相混）= 失败；
  两边都是对象则 run_id、run_attempt、job、case_id 必须一致。
"""

from __future__ import annotations

import os

_FIELDS = ("run_id", "run_attempt", "job", "case_id")
ABSENT = object()


class ProvenanceError(ValueError):
    pass


def current(case_id: str | None, env=None) -> dict | None:
    """本次 Actions job 的出处；缺任何一个环境变量（本地 / 非 Actions）则为 None。"""
    env = os.environ if env is None else env
    values = [env.get(name) for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")]
    if not all(values):
        return None
    return {
        "run_id": values[0],
        "run_attempt": values[1],
        "job": values[2],
        "case_id": case_id,
    }


def report_case(selection: str | None) -> str | None:
    """报告侧出处里的 case：`--case` 恰好选了一个登记 id 时就是它；逗号分隔的多个 / 未选（= 全部）为 None，
    此时报告与每份 receipt 只按 run_id / run_attempt / job 配对（`check_pair` 允许报告 case 为 None）。"""
    ids = [c.strip() for c in (selection or "").split(",") if c.strip()]
    return ids[0] if len(ids) == 1 else None


def check_shape(workflow) -> None:
    if workflow is None:
        return
    if (
        not isinstance(workflow, dict)
        or set(workflow) != set(_FIELDS)
        or not all(isinstance(workflow[k], str) and workflow[k] for k in _FIELDS[:3])
        or not (workflow["case_id"] is None or isinstance(workflow["case_id"], str))
    ):
        raise ProvenanceError("workflow provenance has an unexpected shape")


def check_pair(report_workflow, receipt_workflow, case_id: str) -> None:
    """报告与 receipt 的出处必须一致；`ABSENT` 表示该文档没有这个字段（旧证据）。"""
    if report_workflow is ABSENT and receipt_workflow is ABSENT:
        return
    if report_workflow is ABSENT or receipt_workflow is ABSENT:
        raise ProvenanceError("workflow provenance exists on only one of report and setup receipt")
    check_shape(report_workflow)
    check_shape(receipt_workflow)
    if report_workflow is None and receipt_workflow is None:
        return
    if report_workflow is None or receipt_workflow is None:
        raise ProvenanceError("workflow provenance exists on only one of report and setup receipt")
    for key in _FIELDS[:3]:
        if report_workflow[key] != receipt_workflow[key]:
            raise ProvenanceError(f"report and setup receipt come from different workflow {key}")
    if receipt_workflow["case_id"] != case_id:
        raise ProvenanceError("setup receipt workflow case differs from the paired case")
    if report_workflow["case_id"] not in (None, case_id):
        raise ProvenanceError("report workflow case differs from the paired case")
