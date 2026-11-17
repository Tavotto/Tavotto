"""计算图表的渲染协议身份与有界全量 edits（纯标准库）。

这里只确认协议/JSON形状，不授予 setter、场景所有权、字体文件或执行权限。
真实 gid/HANDLERS 资格与 warnings/unrestored 必须由对应 worker 检查。
"""

from __future__ import annotations

import hashlib
import json

from tavotto.rendercore.svgcheck import SVGBudget

from . import chartmodel, patchspec

LEGACY_PROFILE = "legacy-scaffold/1"
NATIVE_PROFILE = "native-manifest/1"
COMPLETE_PROFILE = "native-manifest/2"
NATIVE_PROFILES = frozenset({NATIVE_PROFILE, COMPLETE_PROFILE})
PROFILES = frozenset({LEGACY_PROFILE, *NATIVE_PROFILES})
MAX_STATE_BYTES = SVGBudget().max_text_chars // 2
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = SVGBudget().max_expanded_elements
_PATCH_REQUIRED = frozenset({"gid", "prop", "value"})
_PATCH_ALLOWED = _PATCH_REQUIRED | {"identity"}


class ChartProfileError(ValueError):
    """渲染合同的稳定拒绝原因；不是 worker 能力结论。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


def validate_profile(profile: object) -> str:
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ChartProfileError("chart_model_unsupported", "unsupported chart render profile")
    return profile


def check_json_budget(
    value, *, max_nodes: int = MAX_JSON_NODES, max_depth: int = MAX_JSON_DEPTH
) -> None:
    """包与独立 edits 共用的结构预算；JSON 值形状仍由既有 patchspec 判定。"""
    count = 0
    pending = [(value, 0)]
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > max_nodes or depth > max_depth:
            raise ChartProfileError("svg_resource_limit", "chart JSON budget exceeded")
        if isinstance(node, dict):
            if not all(isinstance(key, str) for key in node):
                raise ChartProfileError("chart_model_invalid", "chart JSON keys must be strings")
            pending.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, (list, tuple)):
            pending.extend((child, depth + 1) for child in node)


def _validated_overrides(
    overrides: object,
    *,
    render_profile: str = LEGACY_PROFILE,
    max_bytes: int | None = None,
) -> tuple[list[dict], bytes]:
    """返回原序完整列表的 JSON 副本；不拿 canonical 排序/去重结果重放。

    只兼容 API 缺省的空 tuple；wire payload 必须是 list。预算先于 canonical
    diagnostics，字段闭集在本边界拒绝，值的合法性只消费 patchspec 的判据。
    """
    profile = validate_profile(render_profile)
    if isinstance(overrides, tuple) and not overrides:
        overrides = []
    if not isinstance(overrides, list):
        raise ChartProfileError("chart_model_invalid", "chart overrides must be a list")
    check_json_budget(overrides, max_nodes=MAX_JSON_NODES, max_depth=MAX_JSON_DEPTH)
    limit = MAX_STATE_BYTES if max_bytes is None else min(max_bytes, MAX_STATE_BYTES)
    encoded: list[bytes] = []
    size = 0
    try:
        encoder = json.JSONEncoder(
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        for piece in encoder.iterencode(overrides):
            raw = piece.encode("utf-8")
            size += len(raw)
            if size > limit:
                raise ChartProfileError("svg_resource_limit", "chart edits byte budget exceeded")
            encoded.append(raw)
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        if isinstance(exc, ChartProfileError):
            raise
        raise ChartProfileError(
            "chart_model_invalid", "chart edits are not finite UTF-8 JSON"
        ) from exc
    for entry in overrides:
        if (
            not isinstance(entry, dict)
            or not _PATCH_REQUIRED <= set(entry)
            or not set(entry) <= _PATCH_ALLOWED
        ):
            raise ChartProfileError("chart_model_invalid", "invalid chart patch field set")
    _canonical, dropped = patchspec.canonicalize_with_diagnostics(overrides)
    if dropped:
        raise ChartProfileError("chart_model_invalid", dropped[0]["reason"])
    if profile == LEGACY_PROFILE and overrides:
        raise ChartProfileError("chart_model_invalid", "legacy chart render profile has no edits")
    raw = b"".join(encoded)
    return json.loads(raw), raw


def validate_overrides(
    overrides: object,
    *,
    render_profile: str = LEGACY_PROFILE,
    max_bytes: int | None = None,
) -> list[dict]:
    """校验并复制原序完整 edits；不授予 gid/setter 能力，不静默丢条目。"""
    return _validated_overrides(overrides, render_profile=render_profile, max_bytes=max_bytes)[0]


def identity(base_revision: str, render_profile: str, overrides: object = ()) -> str:
    """旧空编辑身份原样返回；新身份包含 profile 与原序完整 edits。"""
    profile = validate_profile(render_profile)
    edits, encoded = _validated_overrides(overrides, render_profile=profile)
    if not isinstance(base_revision, str) or not base_revision:
        raise ChartProfileError("chart_model_invalid", "chart base revision must be text")
    if profile == LEGACY_PROFILE:
        return base_revision
    return chartmodel.digest_of(
        {
            "base_revision": base_revision,
            "render_profile": profile,
            "overrides": edits,
            # identity_bytes 正常按 JS 数值语义记1/1.0及大整数；text setter
            # 的str(value)却区分它们。额外钉住精确JSON，不改旧身份或patch_hash。
            "overrides_sha256": hashlib.sha256(encoded).hexdigest(),
        }
    )


def validate_rebound_edit_ownership(previous: object, current: dict, overrides: list) -> None:
    """重绑预览只沿用同一科学对象的 edits；不是 setter/gid 能力授权。

    数据、范围与样式改变不改变对象身份。重排/替换系列则可能把相同原生 gid
    分给另一节点，不能仅凭 worker 的 positional gid 接纳旧编辑。结构迁移需要
    显式逐对象映射，本闸保留旧稿而不默清 edits。
    """
    if not overrides:
        return

    def ownership(state):
        if not isinstance(state, dict) or set(state) != {"model", "binding"}:
            raise ChartProfileError("chart_binding_stale", "previous chart ownership is required")
        model, binding = state["model"], state["binding"]
        try:
            chartmodel.validate_model(model)
            chartmodel.validate_binding(binding, model)
        except chartmodel.ChartModelError as exc:
            raise ChartProfileError(exc.code, exc.detail) from exc
        series = []
        for item in model["series"]:
            # Errorbar presence and bar cardinality affect native member indices.
            topology = (
                bool(item.get("yerr")),
                len(model["dataset"]["columns"][item["y"]]) if item["kind"] == "bar" else None,
            )
            series.append(
                (
                    item["id"],
                    item["node"],
                    item["kind"],
                    item["axes"],
                    item.get("cbar_node"),
                    topology,
                )
            )
        return (
            model["scene_digest"],
            [(axis["id"], axis.get("kind", "numeric")) for axis in model["axes"]],
            series,
            sorted((entry["object"], entry["node"]) for entry in binding["entries"]),
            # N4 legend 归属：图例区域、区域节点或条目→系列指认变了 = 所有权变了
            # = 旧 edits 不迁移（handle_* override 套到新图例归属上是数据事故）。
            sorted(
                (
                    item["id"],
                    item["node"],
                    tuple((e["index"], e.get("series")) for e in item["entries"]),
                )
                for item in binding.get("legend", [])
            ),
        )

    if ownership(previous) != ownership(current):
        raise ChartProfileError(
            "chart_binding_stale",
            "chart object ownership changed; existing edits were not migrated",
        )
