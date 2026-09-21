"""导出产物检查的接线层（统一实施包 U08 第三切片，ADR 0068）——HTTP 与 MCP 两个入口共用的那一份。

`rendercore/inspector.py` 只认识文件与政策，不认识作业；`app.py` / `codex-plugin` 的 bridge 各有一条导出路，
「怎么把作业里的 `Produced` 变成 manifest、拒绝的换成 `artifact_rejected`」这段逻辑只有这里一份——两个入口各写一遍
就是「HTTP 导出核了、MCP 导出没核」那种形状（RC-086 / RC-087）。

纯粹的胶水：计划半张（生产者给了就用，旧后端只有请求级事实）、严格政策的规范解析（`profilestore.resolve_spec`
是唯一权威）、检查器异常的处置（全 unknown，不是通过）。Flask 父进程 import 链上（`rendercore.inspector` 的 pikepdf
只在函数里 import；没装时 PDF 走 `probe` 基本观测）。
"""

from __future__ import annotations

import logging
from typing import Callable

from . import exportjob, exportreq, profilestore

LOG = logging.getLogger(__name__)


def plan_half(job: exportjob.ExportJob, p: exportjob.Produced, *, backend: str) -> dict:
    """计划半张：生产者给了（候选路 `rendercore.job.produce` 的 `manifest["plan"]`）就用它；否则只有请求级的事实
    （页面 mm → pt、报出来的像素 / ppi、`vector`），期望文字行 / 对象框 / 回执它给不出——那几维就是 unknown。"""
    req = job.request
    given = (p.manifest or {}).get("plan") if isinstance(p.manifest, dict) else None
    if given:
        return dict(given)
    plan: dict = {"backend": backend, "plan_identity": None, "vector": bool(p.vector)}
    if p.width_mm and p.height_mm:
        plan["page_pt"] = [p.width_mm * 72.0 / 25.4, p.height_mm * 72.0 / 25.4]
    if p.width_px and p.height_px:
        plan["px"] = [int(p.width_px), int(p.height_px)]
    if p.format in exportreq.RASTER_FORMATS:
        plan["ppi"] = float(req.ppi) if req.ppi else None
    return plan


def inspection_profile(policy: exportreq.InspectionPolicy) -> dict | None:
    """严格政策的阈值只从出版规范来（`profilestore.resolve_spec` 是唯一权威，RC-071）。standard 不需要。
    规范 id 不认识 → `bad_inspection`（不退默认）。"""
    if policy.mode != exportreq.INSPECTION_STRICT:
        return None
    try:
        spec = profilestore.resolve_spec(policy.profile_id)
    except profilestore.ProfileStoreError as exc:
        raise exportreq.ExportRequestError(
            "bad_inspection",
            f"严格检查指向的出版规范不可用：{exc}",
            {"value": str(policy.profile_id or "")},
        ) from exc
    return {"profile_id": spec.get("profile_id") or policy.profile_id, **spec}


def inspect_produced(
    job: exportjob.ExportJob,
    produced: list[exportjob.Produced],
    *,
    backend: str,
    probe: Callable | None,
) -> list[exportjob.Produced]:
    """`exportjob.run` 的 `inspect` 钩子本体：每个封口的临时文件重新打开检查，拒绝的换成 `artifact_rejected`
    （带 manifest 投影），合格的附上 manifest。检查器自己炸了 = 那一项全 unknown 而不是通过（standard 如实记录并
    交付，strict 按「必需 unknown」阻断）。"""
    from ..rendercore import inspector

    policy = job.request.inspection
    profile = inspection_profile(policy)
    out: list[exportjob.Produced] = []
    for p in produced:
        if p.error_code is not None or p.tmp_path is None:
            out.append(p)
            continue
        plan = plan_half(job, p, backend=backend)
        try:
            manifest = inspector.inspect(
                p.tmp_path, p.format, plan=plan, policy=policy.mode, profile=profile, probe=probe
            )
        except Exception as exc:  # noqa: BLE001 —— 检查器炸了 = 没检查，不是通过
            LOG.warning("产物检查器异常（%s）：%s", p.format, exc, exc_info=True)
            manifest = inspector.uninspected(
                p.tmp_path, p.format, plan=plan, policy=policy.mode, reason=str(exc)[:200]
            )
        summary = inspector.summary(manifest)
        if manifest["policy"]["verdict"] == inspector.REJECTED:
            # 拦住这一项的是哪些检查：失败的；严格政策下必需 unknown 也算（D08）
            blocked = list(manifest["policy"]["failed"])
            if manifest["policy"]["mode"] == inspector.POLICY_STRICT:
                blocked += manifest["policy"]["unknown"]
            out.append(
                exportjob.Produced(
                    format=p.format,
                    error_code="artifact_rejected",
                    error_params={
                        "failed": ", ".join(blocked),
                        "policy": manifest["policy"]["mode"],
                    },
                    manifest=summary,
                )
            )
            continue
        p.manifest = summary
        out.append(p)
    return out


__all__ = ["inspect_produced", "inspection_profile", "plan_half"]
