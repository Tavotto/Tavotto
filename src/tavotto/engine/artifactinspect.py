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
from pathlib import Path
from typing import Callable

from . import databinding, exportjob, exportreq, figcapture, pool, profilestore, receipt, workdir

LOG = logging.getLogger(__name__)


def execution_provenance(
    job: exportjob.ExportJob,
    worker,
    *,
    script: str,
    source_id: str,
    patch_hash: str,
    tmp_path,
    kind: str,
) -> dict:
    """worker **直接序列化**那条导出路（MCP `tavotto_export`：matplotlib 自己写 PDF / PNG，没有 RenderPlan）
    的计划半张补丁（U09，ADR 0070）：回执从这条会话的账本装配（`receipt.from_worker`，与 HTTP 候选路同一份
    `grant` / `binding`），产出的文件本身就是 `origin=execution` 的源产物，`plan_identity` 经
    `exportreq.render_plan_ref()`（同一份身份算法：请求语义 + 源的语义坐标，字节 hash 不进），`render_identity`
    由 `rendercore.identity.render_identity`（backend=worker、backend_version=matplotlib 版本）。

    结果只含公开事实（`receipt.public_facts()` / 身份串），可以直接进 manifest。
    """
    from ..rendercore import identity as rc_identity

    spec = worker.spec
    binding = None
    try:
        binding = databinding.binding_for(
            Path(spec.project_root) / figcapture.normalize_relative_script(script),
            spec.project_root,
            spec.cwd_mode,
        )
    except (OSError, ValueError):
        binding = None
    rcpt = receipt.from_worker(
        worker,
        {
            "descriptors": list(getattr(worker, "last_build_descriptors", None) or []),
            "runtime": getattr(worker, "last_build_runtime", None),
        },
        control_plane=pool.control_plane_of(worker),
        grant=workdir.grant_for(spec.project_root),
        binding=binding,
    )
    sha, size = figcapture.hash_file(tmp_path)
    # 源是**这次执行的 Figure**（同一份热态图写成 PDF / PNG 是同一个语义），不是产出文件的扩展名——
    # `kind=figure` 让 semantic 身份对格式不变；产出文件的字节 hash 是 artifact 身份，不进 semantic
    art = figcapture.SourceArtifact(
        source_id=source_id,
        origin=figcapture.ORIGIN_EXECUTION,
        kind="figure",
        bytes_sha256=sha,
        size_bytes=size,
        receipt_id=rcpt.receipt_id,
        generation=rcpt.generation,
        patch_hash=patch_hash,
        receipt_identity=rcpt.public_identity(),
    )
    ref = exportreq.render_plan_ref(job.request, [art.to_payload()])
    facts = rcpt.public_facts()
    plan_identity = ref["plan_identity"]
    return {
        "plan_identity": plan_identity,
        "render_identity": rc_identity.render_identity(
            plan_identity,
            backend="worker",
            backend_version=str(facts.get("packages", {}).get("matplotlib") or ""),
            renderer="matplotlib",
            renderer_version=str(facts.get("packages", {}).get("matplotlib") or ""),
            ppi=float(job.request.ppi)
            if kind in exportreq.RASTER_FORMATS and job.request.ppi
            else None,
        ),
        "sources": [
            {
                "source_id": art.source_id,
                "origin": art.origin,
                "kind": art.kind,
                "bytes_sha256": art.bytes_sha256,
                "receipt_identity": art.receipt_identity,
                "patch_hash": art.patch_hash,
            }
        ],
        "execution_receipts": [art.receipt_identity],
        "receipts": [facts],
        "nodes": [
            {
                "id": source_id,
                "node": source_id,
                "instance": 1,
                "kind": "figure",
                "source_id": source_id,
                "origin": art.origin,
                "receipt_identity": art.receipt_identity,
                "internal": None,
            }
        ],
        "nodes_truncated": False,
    }


def plan_half(job: exportjob.ExportJob, p: exportjob.Produced, *, backend: str) -> dict:
    """计划半张：生产者给了（候选路 `rendercore.job.produce` 的 `manifest["plan"]`）就用它；否则只有请求级的事实
    （页面 mm → pt、报出来的像素 / ppi、`vector`），期望文字行 / 对象框 / 回执它给不出——那几维就是 unknown。
    生产者只给了来源补丁（`manifest["provenance"]`，worker 直出那条路的 `execution_provenance()`）时，请求级事实
    + 补丁。"""
    req = job.request
    given = (p.manifest or {}).get("plan") if isinstance(p.manifest, dict) else None
    if given:
        return dict(given)
    plan: dict = {"backend": backend, "plan_identity": None, "vector": bool(p.vector)}
    patch = (p.manifest or {}).get("provenance") if isinstance(p.manifest, dict) else None
    if isinstance(patch, dict):
        plan.update(patch)
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
                p.tmp_path,
                p.format,
                plan=plan,
                policy=policy.mode,
                profile=profile,
                probe=probe,
                run_id=job.id,
            )
        except Exception as exc:  # noqa: BLE001 —— 检查器炸了 = 没检查，不是通过
            LOG.warning("产物检查器异常（%s）：%s", p.format, exc, exc_info=True)
            manifest = inspector.uninspected(
                p.tmp_path,
                p.format,
                plan=plan,
                policy=policy.mode,
                reason=str(exc)[:200],
                run_id=job.id,
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


__all__ = ["execution_provenance", "inspect_produced", "inspection_profile", "plan_half"]
