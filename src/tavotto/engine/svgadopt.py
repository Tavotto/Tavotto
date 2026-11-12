"""可信 SVG 来源的采用判定（ADR 0181，SG04）——纯决策，零 I/O。

一份 SVG 进编辑器前要回答「它是谁的图」：
- 带 `tavotto.svg-source-meta/1` 元数据、且与调用方提供的 svgstore / 执行事实
  对得上 → **figure 来源**：复用既有 live-Figure 管线（快照引用进 SG11/12）；
- 没有元数据 → **declarative 候选**：走用户显式绑定（SG06），不猜测来源；
- 元数据对不上 → **refused** + 稳定理由：同名邻居 `.py`、文件名、缓存都
  **不构成证明**（`exportroute` 的同一纪律：这里只收调用方给的事实）。

本模块是 `engine/exportroute.py` 的同族：不执行、不读文件、不跑脚本；
所有事实由调用方注入。元数据是**纯数据闭式 schema**——没有可执行代码、
没有机器路径的容身之处（复用 chartmodel 的判据纪律）。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

#: 元数据 schema（次版本向后兼容，主版本变化拒读）。
ADOPT_SCHEMA = "tavotto.svg-source-meta/1"
_ADOPT_SCHEMA_RE = re.compile(r"^tavotto\.svg-source-meta/(\d+)$")
_SUPPORTED_MAJOR = 1

#: 元数据顶层闭式字段集。
METADATA_KEYS = ("schema", "source", "scene_sha256", "capabilities", "completeness")
#: 来源对象闭式字段集（对应 svgstore 快照引用的身份五元组的可携带子集）。
SOURCE_KEYS = ("kind", "source_id", "receipt_id", "generation", "patch_hash", "execution_owner")
#: 能力声明闭式字段集。
CAPABILITY_KEYS = ("portable_model", "requires_python_transforms")

#: completeness 闭集：self-contained = 自包含（模型+数据齐全）；reference-only =
#: 只含来源引用，离开原环境如实提示缺来源（两者在产品里含义不同，SG12 分开导出）。
COMPLETENESS = ("self-contained", "reference-only")

#: 稳定拒绝理由闭集（前端文案与 errors.json 落地时同步）。
REFUSALS = (
    "adopt_metadata_invalid",
    "adopt_schema_unsupported",
    "adopt_source_changed",
    "adopt_snapshot_missing",
    "adopt_receipt_mismatch",
    "adopt_native_offline",
)

#: 来源种类闭集（与 chartmodel.SOURCE_KINDS 同义）。
SOURCE_FIGURE = "figure"
SOURCE_DECLARATIVE = "declarative"


class AdoptionError(ValueError):
    """元数据本身不合法（schema/字段集层面）。adopt_* 拒绝走 verdict 不走异常。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in REFUSALS:
            raise AssertionError(f"undisclosed refusal: {reason}")
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def parse_metadata(text: str | bytes) -> dict | None:
    """解析来源元数据；空/无内容返回 None（→ declarative 候选）。

    NaN/Infinity 与未知字段在这里就死（闭式 schema，没有可执行代码的容身处）。
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="strict")
    if not text.strip():
        return None

    def _reject(token: str) -> Any:
        raise AdoptionError("adopt_metadata_invalid", f"non-finite literal {token!r}")

    try:
        meta = json.loads(text, parse_constant=_reject)
    except AdoptionError:
        raise
    except ValueError as exc:
        raise AdoptionError("adopt_metadata_invalid", f"not JSON: {exc}") from exc
    if not isinstance(meta, dict):
        raise AdoptionError("adopt_metadata_invalid", "metadata must be an object")
    unknown = sorted(set(meta) - set(METADATA_KEYS))
    if unknown:
        raise AdoptionError("adopt_metadata_invalid", f"unknown keys {unknown}")
    schema = meta.get("schema")
    m = _ADOPT_SCHEMA_RE.match(schema) if isinstance(schema, str) else None
    if m is None:
        raise AdoptionError("adopt_metadata_invalid", f"schema {schema!r} malformed")
    if int(m.group(1)) > _SUPPORTED_MAJOR:
        raise AdoptionError("adopt_schema_unsupported", f"{schema}")
    source = meta.get("source")
    if not isinstance(source, dict):
        raise AdoptionError("adopt_metadata_invalid", "source must be an object")
    unknown_source = sorted(set(source) - set(SOURCE_KEYS))
    if unknown_source:
        raise AdoptionError("adopt_metadata_invalid", f"source: unknown keys {unknown_source}")
    for key in ("kind", "source_id", "receipt_id", "generation", "patch_hash"):
        value = source.get(key)
        if not isinstance(value, str) or not value:
            raise AdoptionError(
                "adopt_metadata_invalid", f"source.{key} must be a non-empty string"
            )
    if source.get("kind") != "figure":
        raise AdoptionError(
            "adopt_metadata_invalid", f"source.kind must be 'figure', got {source.get('kind')!r}"
        )
    owner = source.get("execution_owner")
    if owner is not None and (not isinstance(owner, str) or not owner):
        raise AdoptionError("adopt_metadata_invalid", "source.execution_owner must be a string")
    scene_sha = meta.get("scene_sha256")
    if not isinstance(scene_sha, str) or len(scene_sha) != 64:
        raise AdoptionError("adopt_metadata_invalid", "scene_sha256 must be a sha256 hex string")
    caps = meta.get("capabilities", {})
    if not isinstance(caps, dict):
        raise AdoptionError("adopt_metadata_invalid", "capabilities must be an object")
    unknown_caps = sorted(set(caps) - set(CAPABILITY_KEYS))
    if unknown_caps:
        raise AdoptionError("adopt_metadata_invalid", f"capabilities: unknown keys {unknown_caps}")
    if meta.get("completeness") not in COMPLETENESS:
        raise AdoptionError("adopt_metadata_invalid", f"completeness must be one of {COMPLETENESS}")
    return meta


def scene_bytes_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decide_adoption(
    scene_bytes: bytes,
    metadata_text: str | bytes | None,
    *,
    snapshot_static_sha256: str | None,
    receipt_matches: bool,
    native_offline: bool = False,
) -> dict:
    """采用判定：返回 figure（带引用事实）/ declarative / refused。

    参数全部是**调用方核实过的事实**：
    - ``snapshot_static_sha256``：svgstore 里这笔快照绑定的磁盘字节摘要（无快照 None）；
    - ``receipt_matches``：快照回执与元数据身份（source_id/receipt_id/generation/patch_hash）
      一致——**伪造 owner 在这里被拒**：元数据声称的出处对不上真实回执就进不了任何 Figure；
    - ``native_offline``：来源挂在 native 路由且会话不在线（绝不 fallback safe）。
    ```
    """
    if metadata_text is None or (
        isinstance(metadata_text, (str, bytes)) and not str(metadata_text).strip()
    ):
        return {"kind": SOURCE_DECLARATIVE}
    meta = parse_metadata(metadata_text)

    if meta["scene_sha256"] != scene_bytes_digest(scene_bytes):
        return _refused("adopt_source_changed", "metadata scene digest differs from the bytes")
    if native_offline:
        # native 会话不在线：不猜、不 fallback、不执行用户代码（ADR 0181 §3）
        return _refused("adopt_native_offline", "the originating native session is not running")
    if snapshot_static_sha256 is None:
        return _refused("adopt_snapshot_missing", "no verified snapshot for this reference")
    if snapshot_static_sha256 != scene_bytes_digest(scene_bytes):
        return _refused("adopt_source_changed", "snapshot is bound to different bytes")
    if not receipt_matches:
        return _refused("adopt_receipt_mismatch", "snapshot receipt does not match the metadata")
    caps = meta.get("capabilities", {})
    return {
        "kind": SOURCE_FIGURE,
        "reference": {
            "source_id": meta["source"]["source_id"],
            "receipt_id": meta["source"]["receipt_id"],
            "generation": meta["source"]["generation"],
            "patch_hash": meta["source"]["patch_hash"],
            "execution_owner": meta["source"].get("execution_owner"),
        },
        "completeness": meta["completeness"],
        # 自定义 Python transform/formatter 不是可移植模型：能力前提如实声明
        "portable_model": bool(caps.get("portable_model", True))
        and not bool(caps.get("requires_python_transforms", False)),
    }


def _refused(reason: str, detail: str) -> dict:
    return {"kind": "refused", "reason": reason, "detail": detail}
