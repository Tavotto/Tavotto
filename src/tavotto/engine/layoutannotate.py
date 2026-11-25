"""往项目里的一份排版上加矢量标注（ADR 0208 第 2 期）：读场景 → 方案（求解 + 核对 + 复述）→ 写入 → 一步撤销。

插件（无头）与以后的改图助手共用这一份；插件只翻译（`codex-plugin/mcp/tavotto_mcp/bridge.py`）。

* **读**：只读当前项目 `tavottofile/<名>.json`（落盘路径判据与 `app._project_layout_target` 一致：收纳目录本身
  或这份文件是符号链接一律拒）。认 schema 3 / 4 的项目文档（画布结构相同）。
* **量**：每张子图的真值来自它这一次导出绘制（`export(..., data_points=…)`，第 1 期修订 1）。量由调用方注入
  （`measure(panel, requests) -> {"draw_manifest", "data_points"}`，插件经它自己的会话池）；量不了的子图
  （没有脚本的素材、缺数据、要输入……）只给外框，数据 / 元素锚点回 `anchor_unresolvable`，**一张失败不拖垮整份场景**。
* **方案**：`vectorscene.solve` → `agentobjects.validate_new_objects` → `vectorcheck.check` → `describe`（复述）。
  复述**只从求解结果生成**（落到了哪个元素、它的名字与角色、哪个数据坐标），不回放 Agent 的输入。
* **写**：服务端按**同一份描述**、同一个场景指纹与 `base_revision` 重新求解、重新核对，**不收 Agent 传回的对象或坐标**；
  还要带方案里的 `echo_digest`——对不上说明 Agent 要写的不是它看过的那份复述，拒绝。只追加对象（不改已有对象），
  每个新对象带 `anchor`（锚到了哪张子图的什么上，第 4 期「可能没对准」据它重算）。锁与冲突判据与桌面保存同一份
  （`engine/doclock.py`）。核对有 `fail` 不写；`unknown` 照写但如实列出。
* **撤销**：写之前把原文件字节存进数据目录（`<状态目录>/agent-undo/`，不进项目文件夹），回执带 `undo_token`。
  撤销只在**文件还是我写下的那一份**时恢复；之后被别人改过（桌面保存、另一个 Agent）就拒绝，不替用户覆盖。

时间线（ADR 0101）按桌面每次打开的文档 id 记，插件找不到「这份排版此刻在哪条时间线上」，所以一步撤销走这里的
留存（ADR 0208 第 2 期修订）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import (
    agentobjects,
    atomicio,
    config,
    doclock,
    documents,
    layoutsession,
    vectorcheck,
    vectorscene,
)

#: 撤销留存：每份排版最多留几次（老的先删）。
UNDO_KEEP = 20

#: 错误码闭集（工具层照抄给 Agent；文案在工具层）。
ERROR_CODES = (
    "layout_not_found",
    "layout_outside_project",
    "layout_unreadable",
    "layout_format_unsupported",
    "canvas_not_found",
    "layout_changed",
    "echo_mismatch",
    "checks_failed",
    "undo_unknown",
    "undo_stale",
    "undo_unavailable",
)


class LayoutError(Exception):
    def __init__(self, code: str, **detail: object) -> None:
        assert code in ERROR_CODES, code
        super().__init__(code)
        self.code = code
        self.detail = detail

    def as_dict(self) -> dict:
        return {"code": self.code, **({"detail": self.detail} if self.detail else {})}


class PanelUnavailable(Exception):
    """`measure` 量不了这张子图（码是调用方的，原样写进场景的 `unmeasured`）。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


Measure = Callable[[dict, list], dict]


# ------------------------------------------------------------------ 读


@dataclass
class Layout:
    project: Path
    name: str
    path: Path
    revision: str
    doc: dict


def layout_target(project: str | Path, name: str) -> Path:
    """项目里一份排版的落盘路径；只能落在这个项目的 `tavottofile/` 下（与 `app._project_layout_target` 同一判据）。"""
    root = Path(project)
    if not isinstance(name, str) or not name or name != re.sub(r"[^\w\-一-鿿]+", "_", name):
        raise LayoutError("layout_not_found")
    if not documents.is_user_document_stem(name):
        raise LayoutError("layout_not_found")
    base = config.project_store_dir(root)
    path = base / f"{name}.json"
    if not base.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise LayoutError("layout_outside_project")
    return path


def list_layouts(project: str | Path) -> list[str]:
    base = config.project_store_dir(Path(project))
    if not base.is_dir() or base.is_symlink():
        return []
    out = []
    for p in sorted(base.glob("*.json")):
        if documents.is_user_document_stem(p.stem) and not p.is_symlink():
            out.append(p.stem)
    return out


def open_layout(project: str | Path, name: str) -> Layout:
    path = layout_target(project, name)
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise LayoutError("layout_not_found") from exc
    except OSError as exc:
        raise LayoutError("layout_unreadable") from exc
    try:
        doc = documents.loads_document(data)
    except (ValueError, documents.DocumentError) as exc:
        raise LayoutError("layout_unreadable") from exc
    # schema 3（项目）与 4（项目 + 计算图表子图，#1882 单一存储）的画布结构相同；计算图表子图没有脚本，量时只给外框
    if (
        not isinstance(doc, dict)
        or doc.get("schema") not in (documents.SCHEMA_PROJECT, documents.SCHEMA_CHART_PROJECT)
        or not isinstance(doc.get("canvases"), list)
    ):
        raise LayoutError("layout_format_unsupported")
    return Layout(Path(project), name, path, atomicio.revision_of(data), doc)


def _canvas(doc: dict, canvas_id: str | None) -> dict:
    want = canvas_id if canvas_id is not None else doc.get("activeCanvasId")
    for c in doc["canvases"]:
        if isinstance(c, dict) and c.get("id") == want:
            return c
    if canvas_id is None and doc["canvases"] and isinstance(doc["canvases"][0], dict):
        return doc["canvases"][0]
    raise LayoutError("canvas_not_found")


def _figure_panels(canvas: dict) -> list[dict]:
    return [
        o
        for o in canvas.get("objects") or []
        if isinstance(o, dict) and o.get("type") == "panel" and not o.get("hidden")
    ]


def _page(canvas: dict) -> dict:
    pg = canvas.get("page") or {}
    return {"w": float(pg.get("w", 0)), "h": float(pg.get("h", 0))}


# ------------------------------------------------------------------ 量


def measure_panels(canvas: dict, items: list | None, measure: Measure) -> tuple[dict, dict, dict]:
    """→ (`panels` 给 vectorscene / vectorcheck，`refs` 短号 → 子图对象 id，`unmeasured` 短号 → 原因码)。

    短号按子图在画布上的顺序编（P1、P2…），同一份排版两次读出来一样。`items` 给了就只量它们要的数据点。"""
    wanted = vectorscene.required_data_points(items or []) if items else {}
    panels: dict = {}
    refs: dict = {}
    unmeasured: dict = {}
    for i, obj in enumerate(_figure_panels(canvas), start=1):
        pid = f"P{i}"
        refs[pid] = obj.get("id")
        reqs = wanted.get(pid, [])
        entry: dict = {"panel": obj, "requests": reqs, "data_points": []}
        try:
            got = measure(obj, reqs)
            entry["draw_manifest"] = got.get("draw_manifest")
            entry["data_points"] = got.get("data_points") or []
        except PanelUnavailable as exc:
            unmeasured[pid] = exc.code
        panels[pid] = entry
    return panels, refs, unmeasured


def read_scene(
    project: str | Path, name: str, measure: Measure, *, canvas_id: str | None = None
) -> dict:
    """给 Agent 看的场景 + 这份排版的 `base_revision`。"""
    lay = open_layout(project, name)
    canvas = _canvas(lay.doc, canvas_id)
    panels, refs, unmeasured = measure_panels(canvas, None, measure)
    page = _page(canvas)
    existing = [
        o for o in canvas.get("objects") or [] if isinstance(o, dict) and o.get("type") != "panel"
    ]
    scene = vectorscene.build_scene(panels, page=page, objects=existing)
    return {
        "layout": name,
        "canvas_id": canvas.get("id"),
        "base_revision": lay.revision,
        "page": page,
        "panels_by_ref": refs,
        "unmeasured": unmeasured,
        "scene": scene,
    }


# ------------------------------------------------------------------ 复述


def _element(panels: dict, pid: str, gid: str) -> dict | None:
    for e in ((panels.get(pid) or {}).get("draw_manifest") or {}).get("elements", []):
        if e.get("gid") == gid:
            return e
    return None


def _say_anchor(anchor: dict, panels: dict) -> dict:
    """一条锚点 → 它在求解里**实际落到**的东西（结构化 + 一句话）。"""
    kind, spec = next(iter(anchor.items()))
    if kind == "data":
        pid = spec.get("panel")
        p = panels.get(pid) or {}
        q = {"axes": spec.get("axes"), "x": spec.get("x"), "y": spec.get("y")}
        reqs = p.get("requests") or []
        res = (p.get("data_points") or [None] * len(reqs))[reqs.index(q)] if q in reqs else None
        ax = _element(panels, pid, spec.get("axes")) if isinstance(spec.get("axes"), str) else None
        return {
            "kind": "data",
            "panel": pid,
            "axes": spec.get("axes"),
            "axes_label": (ax or {}).get("label"),
            "x": spec.get("x"),
            "y": spec.get("y"),
            "measured": isinstance(res, dict) and res.get("status") == "ok",
            "text": f"{pid} 坐标轴 {spec.get('axes')} 上的数据点 (x={spec.get('x')}, y={spec.get('y')})",
        }
    if kind == "element":
        pid, gid = spec.get("panel"), spec.get("gid")
        el = _element(panels, pid, gid) or {}
        label, role = el.get("label"), el.get("role")
        # 「有名字」只认脚本显式起的 `label=`（manifest 的 `identity`，ADR 0083）；`label` 是界面上的显示名
        # （「误差棒 1」「曲线 “Sample B”」），没起名字的元素也有，不能当作用户能拿来比对的名字
        named = isinstance(el.get("identity"), str)
        shown = label if isinstance(label, str) and label else (role or "元素")
        who = f"{shown}" if named else f"{shown}（没有名字，{gid}）"
        return {
            "kind": "element",
            "panel": pid,
            "gid": gid,
            "role": role,
            "label": label,
            "named": named,
            "point": spec.get("point", "center"),
            "text": f"{pid} 里 {who} 的 {spec.get('point', 'center')}",
        }
    if kind == "panel":
        return {
            "kind": "panel",
            "panel": spec.get("id"),
            "point": spec.get("point", "center"),
            "text": f"子图 {spec.get('id')} 的 {spec.get('point', 'center')}",
        }
    return {
        "kind": "item",
        "item": spec.get("name"),
        "point": spec.get("point", "center"),
        "text": f"本批元素「{spec.get('name')}」的 {spec.get('point', 'center')}",
    }


_KIND_WORD = {
    "arrow": "箭头",
    "text": "文字",
    "shape": "形状",
    "bracket": "显著性括号",
    "connector": "连线",
    "box": "外框",
}
_ROLE_WORD = {
    "from": "从",
    "to": "指向",
    "at": "放在",
    "left": "左端",
    "right": "右端",
    "member": "框住",
}


def describe(items: list, solved: dict, panels: dict) -> list[dict]:
    """每个元素一条复述：它是什么（种类、文字内容）、每个锚点实际落到了什么上、意图能不能被复述确认。

    `intent`：所有元素锚点都落在**有名字**（脚本 `label=`）的元素上、数据点都量到了 → `describable`；
    否则 `unnamed`（复述只说得出 gid / 坐标，Agent 没法拿用户的原话去比）。"""
    by_item: dict[str, list] = {}
    for rec in solved.get("anchors") or []:
        by_item.setdefault(rec["item"], []).append(rec)
    out = []
    for it in items:
        name = it.get("name")
        parts = []
        for rec in by_item.get(name, []):
            said = _say_anchor(rec["anchor"], panels)
            said["role"] = rec.get("role")
            parts.append(said)
        sure = all(
            (p["kind"] != "element" or p["named"]) and (p["kind"] != "data" or p["measured"])
            for p in parts
        )
        head = _KIND_WORD.get(it.get("kind"), it.get("kind"))
        if it.get("kind") == "text":
            head += f"「{it.get('text')}」"
        body = "；".join(f"{_ROLE_WORD.get(p['role'], p['role'] or '')} {p['text']}" for p in parts)
        out.append(
            {
                "item": name,
                "kind": it.get("kind"),
                "anchors": parts,
                "intent": "describable" if sure else "unnamed",
                "text": f"{head}：{body}" if body else head,
            }
        )
    return out


def echo_digest(echo: list[dict], items: list) -> str:
    """复述 + 原描述的摘要：写入时必须带回它。描述改了（哪怕只改了偏移，复述不变）摘要就变，
    Agent 要写的就不是它看过、拿去和用户原话比过的那一份。"""
    raw = json.dumps(
        {"echo": echo, "items": items}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ 方案


def _anchor_specs(it: object) -> list[dict]:
    """一个元素描述里出现的全部锚点（端点、落点、括号两端、外框成员不算——成员是本批名字）。"""
    out = []
    if not isinstance(it, dict):
        return out
    for v in it.values():
        if isinstance(v, dict) and isinstance(v.get("anchor"), dict) and len(v["anchor"]) == 1:
            out.append(v["anchor"])
    return out


def _taken_ids(doc: dict) -> set[str]:
    taken: set[str] = set()
    for c in doc.get("canvases") or []:
        for o in (c or {}).get("objects") or []:
            if isinstance(o, dict):
                if isinstance(o.get("id"), str):
                    taken.add(o["id"])
                for g in [o.get("groupId"), *(o.get("groupPath") or [])]:
                    if isinstance(g, str):
                        taken.add(g)
    return taken


def _persisted_anchor(name: str, solved: dict, refs: dict) -> dict | None:
    """写进对象的 `anchor`：这个元素的每个锚点（子图换成子图对象 id，第 4 期按它重算）。"""
    roles = []
    for rec in solved.get("anchors") or []:
        if rec.get("item") != name:
            continue
        kind, spec = next(iter(rec["anchor"].items()))
        spec = dict(spec)
        if kind in ("data", "element"):
            spec["panel"] = refs.get(spec.get("panel"))
        elif kind == "panel":
            spec["id"] = refs.get(spec.get("id"))
        entry = {"role": rec.get("role"), kind: spec}
        if rec.get("offset_mm") not in (None, [0.0, 0.0], [0, 0]):
            entry["offset_mm"] = rec["offset_mm"]
        if isinstance(rec.get("identity"), str):
            entry["identity"] = rec["identity"]
        roles.append(entry)
    return {"v": 1, "by": "agent", "anchors": roles} if roles else None


def _plan(lay: Layout, canvas: dict, items: list, measure: Measure, fingerprint, profile) -> dict:
    panels, refs, unmeasured = measure_panels(canvas, items, measure)
    page = _page(canvas)
    # 量不了的子图上要数据 / 元素锚点：说清楚是这张图没量到，不让它在求解里变成「找不到这个元素」
    for it in items if isinstance(items, list) else []:
        for spec in _anchor_specs(it):
            kind, body = next(iter(spec.items()))
            pid = body.get("panel") if isinstance(body, dict) else None
            if kind in ("data", "element") and pid in unmeasured:
                err = {
                    "code": "anchor_unresolvable",
                    "item": it.get("name"),
                    "detail": {"panel": pid, "reason": unmeasured[pid]},
                }
                return {"ok": False, "errors": [err], "unmeasured": unmeasured}
    # 指纹只含子图落位与这次导出绘制的 manifest（不含数据点请求）：读场景时拿到的那个在这里照样核得上
    if fingerprint is not None:
        if fingerprint != vectorscene.scene_fingerprint(panels, page):
            return {"ok": False, "errors": [{"code": "scene_stale"}], "unmeasured": unmeasured}
    solved = vectorscene.solve(items, panels, page=page)
    if not solved["ok"]:
        return {"ok": False, "errors": solved["errors"], "unmeasured": unmeasured}
    checked = agentobjects.validate_new_objects(solved["objects"], taken_ids=_taken_ids(lay.doc))
    if not checked["ok"]:
        return {"ok": False, "errors": checked["errors"], "unmeasured": unmeasured}
    existing = [
        o
        for o in canvas.get("objects") or []
        if isinstance(o, dict) and o.get("type") == "text" and not o.get("hidden")
    ]
    verdict = vectorcheck.check(
        solved,
        checked["objects"],
        panels,
        items,
        page=page,
        profile=profile,
        existing_texts=existing,
    )
    echo = describe(items, solved, panels)
    objects = []
    by_real = {real: local for local, real in checked["id_map"].items()}
    for obj in checked["objects"]:
        a = _persisted_anchor(by_real.get(obj["id"], ""), solved, refs)
        objects.append({**obj, **({"anchor": a} if a else {})})
    failed = [f for f in verdict["facts"] if f["status"] == "fail"]
    return {
        "ok": not failed,
        "objects": objects,
        "facts": verdict["facts"],
        "failed": failed,
        "unverified": [f for f in verdict["facts"] if f["status"] == "unknown"],
        "statistical": solved.get("statistical") or [],
        "echo": echo,
        "echo_digest": echo_digest(echo, items),
        "unmeasured": unmeasured,
        "errors": [{"code": "checks_failed"}] if failed else [],
    }


def propose(
    project: str | Path,
    name: str,
    items: list,
    measure: Measure,
    *,
    base_revision: str,
    fingerprint: str | None = None,
    canvas_id: str | None = None,
    profile: dict | None = None,
) -> dict:
    """方案：不写盘。`ok` 为真 = 核对没有 `fail`（可以写）；回复述、核对事实、待写对象。"""
    lay = open_layout(project, name)
    if doclock.revision_conflict(base_revision, lay.revision):
        raise LayoutError("layout_changed", revision=lay.revision)
    canvas = _canvas(lay.doc, canvas_id)
    return {
        "base_revision": lay.revision,
        **_plan(lay, canvas, items, measure, fingerprint, profile),
    }


# ------------------------------------------------------------------ 写 / 撤销


def _undo_dir(project: Path, name: str) -> Path:
    """撤销留存的目录：按项目的**真实路径**取键——插件拿到的项目路径与桌面后端的不一定是同一种写法（macOS 上
    `/var/...` 与 `/private/var/...`、符号链接别名），按原样算会把同一个项目的留存放进两个目录，桌面认不出插件写的。"""
    key = hashlib.sha256(
        layoutsession.project_key(Path(project).resolve()).encode("utf-8")
    ).hexdigest()[:16]
    return layoutsession.canonical_state_dir() / "agent-undo" / key / name


def commit(
    project: str | Path,
    name: str,
    items: list,
    measure: Measure,
    *,
    base_revision: str,
    echo_digest_seen: str,
    fingerprint: str | None = None,
    canvas_id: str | None = None,
    profile: dict | None = None,
) -> dict:
    """服务端重新求解、重新核对，复述摘要对得上才写；只追加对象。回 `{revision, added, undo_token, plan}`。"""
    lay = open_layout(project, name)
    if doclock.revision_conflict(base_revision, lay.revision):
        raise LayoutError("layout_changed", revision=lay.revision)
    canvas = _canvas(lay.doc, canvas_id)
    plan = _plan(lay, canvas, items, measure, fingerprint, profile)
    if not plan["ok"]:
        return {"written": False, "plan": plan}
    if plan["echo_digest"] != echo_digest_seen:
        raise LayoutError("echo_mismatch")
    with doclock.document_lock(lay.path):
        try:
            before = lay.path.read_bytes()
        except OSError as exc:
            raise LayoutError("layout_unreadable") from exc
        if atomicio.revision_of(before) != lay.revision:
            raise LayoutError("layout_changed", revision=atomicio.revision_of(before))
        doc = documents.loads_document(before)
        target = _canvas(doc, canvas.get("id"))
        target.setdefault("objects", []).extend(plan["objects"])
        documents.validate_document(doc)
        data = atomicio.dumps_json(doc, indent=1)
        after = atomicio.revision_of(data)
        # 撤销副本**先**落盘，再换排版文件：副本存不下（数据目录只读、磁盘满）就不写——不交出一次没有撤销的写入
        try:
            token = _save_undo(Path(project), name, before, lay.revision, after)
        except OSError as exc:
            raise LayoutError("undo_unavailable") from exc
        atomicio.write_bytes(lay.path, data)
    return {
        "written": True,
        "revision": after,
        "added": [o["id"] for o in plan["objects"]],
        "undo_token": token,
        "plan": plan,
    }


def _save_undo(
    project: Path, name: str, before: bytes, before_rev: str, after_rev: str | None
) -> str:
    d = _undo_dir(project, name)
    d.mkdir(parents=True, exist_ok=True)
    token = f"u{int(time.time() * 1000):x}{os.urandom(3).hex()}"
    atomicio.write_bytes(d / f"{token}.json", before)
    atomicio.write_json(d / f"{token}.meta", {"before": before_rev, "after": after_rev})
    # 清理老副本只是省地方：失败了不影响这一次写入
    try:
        metas = sorted(d.glob("*.meta"), key=lambda p: p.stat().st_mtime)
        for old in metas[:-UNDO_KEEP]:
            for p in (old, old.with_suffix(".json")):
                p.unlink(missing_ok=True)
    except OSError:
        pass
    return token


def undo(project: str | Path, name: str, token: str) -> dict:
    """把排版恢复到那次写入之前——只在磁盘上还是那次写下的那一份时。"""
    if not isinstance(token, str) or not re.fullmatch(r"u[0-9a-f]+", token):
        raise LayoutError("undo_unknown")
    path = layout_target(project, name)
    d = _undo_dir(Path(project), name)
    meta_p, body_p = d / f"{token}.meta", d / f"{token}.json"
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        before = body_p.read_bytes()
    except (OSError, ValueError) as exc:
        raise LayoutError("undo_unknown") from exc
    with doclock.document_lock(path):
        current = atomicio.existing_revision(path)
        if current != meta.get("after"):
            raise LayoutError("undo_stale", revision=current)
        atomicio.write_bytes(path, before)
        restored = atomicio.content_revision(path)
    for p in (meta_p, body_p):
        p.unlink(missing_ok=True)
    return {"revision": restored, "restored_to": meta.get("before")}
