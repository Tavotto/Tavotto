"""前端诊断载荷的**服务端第二道校验**（ADR 0016 §8 / §10）。

前端已经脱敏过一遍（`web/src/diagnostics/sanitize.ts` 的字段 allowlist）。
这里再来一遍**不是**因为不信任自己的前端，而是因为这个端点接受的是**请求体**
——「结构性防线」的意思就是「就算调用方把整条路径塞进来也走不出这一步」，
与 `/api/telemetry/event` 同一套理由。

两侧的判据**刻意不一样**，这样它们才是两道防线而不是一道的复读：

    前端：这种事件**允许哪些字段**（按事件类型逐字段的表）
    后端：任何字段的**值只能是什么形状**（无自由文本、无未知容器）

后端这一侧的核心规则只有一条：**字符串必须是短技术标识**
（`^[A-Za-z0-9_.:-]{1,64}$`，且过一遍既有的密钥/路径脱敏器且不被改动）。
「Experimental results for Fig. 3」有空格，出局；`/Users/alice/paper.py` 有
斜杠，出局；`sk-live-…` 字符集过得了，但 `_redact_text` 会把它改写掉，
一旦被改写整条字段丢弃。

纯标准库，Flask 父进程 import（边界同 diagnostics.py）。
"""
from __future__ import annotations

import json
import re

#: 与 `web/src/diagnostics/types.ts` 的可辨识联合逐条对应。
#: `tests/test_diagnostics_bundle.py::test_event_types_match_frontend_union`
#: 直接读 TS 源码比对——两边加事件时漏了一边，那条用例先红。
EVENT_TYPES: frozenset[str] = frozenset({
    "document.commit",
    "transaction.begin", "transaction.end", "transaction.cancel",
    "undo.request", "undo.complete",
    "redo.request", "redo.complete",
    "selection.changed",
    "render.request", "render.success", "render.error", "render.stale",
    "display.source_changed",
    "authority.ready", "authority.unavailable",
    "element.drag.begin", "element.drag.commit", "element.drag.cancel",
    "axes.drag.begin", "axes.drag.commit",
    "resize.begin", "resize.commit",
    "align.request", "align.blocked", "align.commit", "align.noop",
    "preview.begin", "preview.commit", "preview.cancel", "preview.retire",
    "layout_version.save",
    "layout_version.restore.request", "layout_version.restore.complete",
    "invariant.violation",
    "diagnostics.export",
})

#: **字段名 allowlist**：所有事件允许出现的字段名之并集。
#:
#: 后端刻意**不复制一份前端那种「逐事件」的字段表**（那份表迟早与前端分叉，
#: 而分叉的表现是诊断里悄悄少了几个字段）。但「值的形状」这一条挡不住
#: 「未登记的字段名 + 恰好形状合法的值」——`secret_unexpected_field:
#: "SUPER_SECRET_API_KEY_67890"` 就是这样溜过去的。所以这里加一层**扁平的
#: 名字集合**：粒度比前端粗（不管哪个事件，只问「这个名字在不在册」），
#: 但足以把未登记字段整个挡在外面，而且只有一处要维护。
#:
#: 与前端的一致性由 `test_event_field_names_match_frontend_schema` 看护。
ALLOWED_FIELDS: frozenset[str] = frozenset({
    "anchor_from_document", "authority_variant", "auto",
    "auto_backup_created", "await_variant", "cancelled", "code",
    "display_variant", "document_hash", "document_hash_after",
    "document_hash_before", "document_variant", "duration_ms",
    "element_count", "exact", "exact_authority", "file", "future_count",
    "gid", "history_mode", "input_geometry", "kind", "label_key",
    "mode", "move_count", "object_count", "ok", "operation",
    "output_geometry", "panel", "panel_count", "past_count",
    "patch_count", "patches", "policy", "preview_dpi", "prop", "reason",
    "render_status", "render_variant", "replaced_open_txn", "rev",
    "selected_count", "selected_gids", "selection_kind", "session",
    "size_mm", "stale", "trace_count", "txn_open", "variant",
    "variant_count", "version", "warning_count"
})

#: 硬上限。超出一律**截断而不是失败**——用户点导出是为了拿到一个包，
#: 因为 trace 太长而两手空空是最糟的结果。截断的事实记进 manifest。
MAX_EVENTS = 300
MAX_REQUEST_BYTES = 512 * 1024
MAX_STRING = 64
MAX_LIST = 64
MAX_DEPTH = 6
MAX_FIELDS = 32
MAX_PANELS = 64
MAX_INT = 1_000_000_000
#: 时间戳单独给上界。epoch 毫秒现在就是 1.7e12，用 MAX_INT 卡它等于把
#: **每一条**事件都判为非法。4e12 ≈ 公元 2096 年。
MAX_TIMESTAMP = 4_000_000_000_000

#: 字段名：我们自己定的 snake_case，不接受别的形状
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
#: 值：短技术标识。**没有任何一条路径能让自由文本通过**
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

#: **身份字段必须是 hash**。
#:
#: 光有 `_TOKEN_RE` 不够：`SUPER_SECRET_PAPER_TITLE_12345` 也是全大写加下划线
#: 加数字，字符集完全合法——后端没法从形状上判断它是个标识还是用户的论文标题。
#: 但**身份字段**我们知道它该长什么样：`doc:81af27cc9d10`。按字段名把这条更强
#: 的判据加上去，就把「标识位上出现内容」这一类整个堵死，而且不需要在后端复制
#: 一份前端的逐事件字段表（那份表迟早与前端分叉）。
_HASH_VALUE_RE = re.compile(r"^[a-z_]+:[0-9a-f]{8,16}$")
#: 内部操作键 / 历史标签 key：没有冒号，比 token 更紧
_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")
#: 技术 gid：**小写字母开头、不含大写**。理由与前端 sanitize.ts 的同名判据一致
#: ——gid 是少数几个原样进包的字符串，只按字符集判断挡不住全大写的内容串。
_GID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
#: 必须全小写的字段（操作键）。形状规则，不是名单
_LOWER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,47}$")
_LOWER_FIELDS = frozenset({"mode", "operation"})
_HASH_FIELD_EXACT = frozenset({
    "panel", "file", "session", "version", "active_panel",
    "document_hash", "variant",
})


def _is_hash_field(name: str) -> bool:
    return (name in _HASH_FIELD_EXACT
            or name.endswith("_hash")
            or name.endswith("_variant"))


def _hash_or_none(value):
    """身份字段：要么是合法 hash，要么什么都不是。**不做「顺手替它 hash」**
    ——那会让「调用点忘了 hash」永远不被发现。"""
    if value is None:
        return None
    if isinstance(value, str) and _HASH_VALUE_RE.match(value):
        return value
    raise _Reject("hash shape")
#: 复合值（patch 身份 / 几何目标）允许的键
_PATCH_KEYS = frozenset({"gid", "prop", "domain"})
_GEOM_KEYS = frozenset({"gid", "bbox", "anchor"})


class _Reject(Exception):
    """这个值过不了判据。只在模块内部用，绝不把收到了什么回声给调用方。"""


def _scalar(value, redact):
    """标量：bool / int / float / None / 短技术标识串。其余一律拒绝。"""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if not -MAX_INT <= value <= MAX_INT:
            raise _Reject("int range")
        return value
    if isinstance(value, float):
        # NaN / inf 进 JSON 是非法的，而且它们只可能来自算错的几何
        if value != value or value in (float("inf"), float("-inf")):
            raise _Reject("float")
        return round(value, 6)
    if isinstance(value, str):
        if len(value) > MAX_STRING or not _TOKEN_RE.match(value):
            raise _Reject("string shape")
        # 既有的密钥 / 主目录 / 用户名脱敏器。**被改动过就整条丢弃**——
        # 一个本该是技术标识的字段里出现了需要脱敏的东西，那它就不是
        # 技术标识，写个 *** 进去只会让人以为这里本来有个有效的 id
        if redact(value) != value:
            raise _Reject("sensitive")
        return value
    raise _Reject("type")


def _compound(value, allowed_keys, redact, depth):
    if not isinstance(value, dict):
        raise _Reject("compound")
    out = {}
    for key, item in value.items():
        if key not in allowed_keys:
            raise _Reject("compound key")
        # 复合值里的 gid 走的是与顶层同一条 gid 判据——只在顶层把关，
        # 几何目标里的 gid 就成了绕过去的近路
        if key == "gid":
            if not isinstance(item, str) or not _GID_RE.match(item):
                raise _Reject("gid shape")
            out[key] = item
            continue
        out[key] = _value(item, redact, depth + 1)
    if not out:
        raise _Reject("compound empty")
    return out


def _value(value, redact, depth=0):
    if depth > MAX_DEPTH:
        raise _Reject("depth")
    if isinstance(value, list):
        if len(value) > MAX_LIST:
            value = value[:MAX_LIST]
        return [_value(v, redact, depth + 1) for v in value]
    if isinstance(value, dict):
        keys = set(value)
        # 只认两种复合形状：patch 身份与几何目标。别的字典一律不认识
        if keys <= _PATCH_KEYS:
            return _compound(value, _PATCH_KEYS, redact, depth)
        if keys <= _GEOM_KEYS:
            return _compound(value, _GEOM_KEYS, redact, depth)
        raise _Reject("dict shape")
    return _scalar(value, redact)


def sanitize_event(raw, redact) -> dict | None:
    """一条 trace 事件。任何一处过不了判据就**整条丢弃**，不做部分保留。

    部分保留在这里是错的：一条缺了关键字段的事件读起来像「当时确实没有这个
    值」，而真相是「那个值被我们扔了」——那会把读包的人引到错误的结论上。
    """
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind not in EVENT_TYPES:
        return None
    try:
        seq = raw.get("seq")
        ts = raw.get("ts")
        t_ms = raw.get("t_ms")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (seq, ts, t_ms)):
            return None
        if not (0 <= seq <= MAX_INT and 0 <= ts <= MAX_TIMESTAMP and 0 <= t_ms <= MAX_INT):
            return None
        out = {"seq": seq, "ts": ts, "t_ms": t_ms, "type": kind}
        fields = 0
        for key, value in raw.items():
            if key in ("seq", "ts", "t_ms", "type"):
                continue
            fields += 1
            if fields > MAX_FIELDS or not _FIELD_RE.match(str(key)):
                return None
            if str(key) not in ALLOWED_FIELDS:
                # 未登记的字段名：**整条事件丢弃**。留下「除了这个字段之外」的
                # 半条会让读包的人以为那就是全部
                return None
            name = str(key)
            if _is_hash_field(name):
                out[key] = _hash_or_none(value)
            elif name in _LOWER_FIELDS:
                # `mode` / `operation` 是我们自己写死的操作键，实际取值全是小写
                # （left / centerh / align.left / scale.group）。要求小写就足以
                # 把 `SUPER_SECRET_…` 这类全大写内容串挡在外面，而且是**形状规则
                # 不是名单**——加一个新的对齐模式不需要回来改这里。
                # `label_key` / `prop` / `code` 不在此列：它们是驼峰的开集标识
                # （setProp / moveElement / fontsize），只能靠前端那张逐事件的
                # 字段表约束「哪个字段允许放什么」。
                if (not isinstance(value, str) or not _LOWER_RE.match(value)
                        or redact(value) != value):
                    raise _Reject("lower field")
                out[key] = value
            else:
                out[key] = _value(value, redact)
        return out
    except _Reject:
        return None


def sanitize_trace(raw, redact) -> tuple[list[dict], bool]:
    """整条 trace。返回 (事件列表, 有没有被截断)。

    截断保留**最近的** MAX_EVENTS 条：事故就在末尾，开头那些离得最远。
    """
    if not isinstance(raw, list):
        return [], False
    truncated = len(raw) > MAX_EVENTS
    tail = raw[-MAX_EVENTS:] if truncated else raw
    out = []
    for item in tail:
        clean = sanitize_event(item, redact)
        if clean is not None:
            out.append(clean)
    # 有条目在校验里被丢掉，同样算「这份 trace 不完整」——读包的人有权知道
    if len(out) != len(tail):
        truncated = True
    return out, truncated


#: frontend-state.json 的骨架。**按 schema 拉取，不遍历输入的键**
#: （与前端 serializeEvent 同一条纪律：多出来的字段是根本没被读过，
#: 而不是读了再丢掉）。
_SNAPSHOT_SHAPE = {
    "schema_version": "int",
    "session_ms": "int",
    "document": {
        "document_hash": "hash",
        "object_count": "int",
        "panel_count": "int",
        "canvas_count": "int",
        "history": {
            "past": "int",
            "future": "int",
            "txn_open": "scalar",
            "txn_label_key": "key",
        },
    },
    "selection": {
        "active_panel": "hash",
        "selection_kind": "scalar",
        "element_count": "int",
        "element_gids": "gids",
        "object_count": "int",
    },
    "preview": {
        "active_sessions": "int",
        "settled": "scalar",
        "history_mode": "scalar",
    },
}

_PANEL_SHAPE = {
    "panel": "hash", "file": "hash", "kind": "scalar",
    "override_count": "int",
    "document_variant": "hash", "display_variant": "hash",
    "authority_variant": "hash",
    "display_exact": "scalar", "exact_manifest_available": "scalar",
    "render_status": "scalar", "stale": "scalar", "element_count": "int",
}


def _pull(src, shape, redact):
    """按 shape **拉取**。src 不是 dict、字段缺失或过不了判据都退化成 None。"""
    out = {}
    for key, spec in shape.items():
        raw = src.get(key) if isinstance(src, dict) else None
        if isinstance(spec, dict):
            out[key] = _pull(raw if isinstance(raw, dict) else {}, spec, redact)
            continue
        try:
            if spec == "int":
                value = _scalar(raw, redact)
                out[key] = value if isinstance(value, int) and not isinstance(value, bool) else 0
            elif spec == "hash":
                out[key] = _hash_or_none(raw)
            elif spec == "key":
                if raw is None:
                    out[key] = None
                elif isinstance(raw, str) and _KEY_RE.match(raw):
                    out[key] = raw
                else:
                    raise _Reject("key shape")
            elif spec == "gids":
                rows = raw[:MAX_LIST] if isinstance(raw, list) else []
                out[key] = [g for g in rows
                            if isinstance(g, str) and _GID_RE.match(g)
                            and redact(g) == g]
            elif spec == "list":
                out[key] = _value(raw, redact) if isinstance(raw, list) else []
            else:
                out[key] = _scalar(raw, redact)
        except _Reject:
            out[key] = 0 if spec == "int" else ([] if spec in ("list", "gids") else None)
    return out


def sanitize_snapshot(raw, redact) -> dict | None:
    """frontend-state.json。整体形状不对就返回 None（包里干脆不放这个文件）。"""
    if not isinstance(raw, dict):
        return None
    out = _pull(raw, _SNAPSHOT_SHAPE, redact)
    panels = raw.get("panels")
    rows = panels[:MAX_PANELS] if isinstance(panels, list) else []
    out["panels"] = [_pull(p if isinstance(p, dict) else {}, _PANEL_SHAPE, redact) for p in rows]
    return out


def payload_too_large(raw_bytes: int) -> bool:
    return raw_bytes > MAX_REQUEST_BYTES


def trace_to_jsonl(events: list[dict]) -> str:
    """一行一个 event。

    **不存成一个巨大的 JSON 数组**：jsonl 人能直接读、grep 得动、坏了一行
    其余照样能解析，贴进 issue 之后搜一个 gid 就能定位。每一行都是独立合法
    的 JSON。
    """
    return "".join(
        json.dumps(e, ensure_ascii=False, sort_keys=False, separators=(",", ":")) + "\n"
        for e in events
    )
