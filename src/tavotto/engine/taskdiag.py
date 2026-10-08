"""任务绑定诊断（T04）：把「失败的那一次」的现场存成一份不可变的小快照，按需交出去。

诊断包（`diagnostics.build_bundle`）回答「这台机器现在什么样」，它在采集时会去探测解释器；用户在导出失败、
准备失败、脚本跑挂的那个提示旁边要的是另一件事：**那一次**尝试发生了什么。两者的主语不同，所以这里
不碰 `build_report`——没有解释器探测、没有安装、没有联网，也不会拿此刻的环境冒充当时的环境。

三条纪律（每一条都有看护用例）：

* **白名单投影，不是递归删键。** 各来源在自己的模块里写 `diagnostic_projection()`（`exportjob` / `preparation` /
  `probe`），逐字段挑：闭集枚举、稳定码、计数、数字、不透明 id。完整请求、文件名、路径、argv、答案、stdout、
  报错文字、`error.params` 一个都不进快照——不是「过一遍正则」，而是根本不读。本模块只提供取值的形状守卫
  （`code` / `ident` / `closed` / `count`…）：不合形状的值**丢掉**，不哈希（低熵值的哈希是可猜的）。
* **终局即冻结。** 作业走到终局那一刻写一次，之后不可改。重试是新的 attempt、新的快照；「谁重试了谁」用
  私有的 `subject`（同一个文档 / 同一份脚本）在**写入时**连成 `retry_of`，后来的成功不会回头改写旧失败。
* **有界。** 条数、单条字节、总字节、保留期都有上限；超出时明确标 `truncated`，逐出时留墓碑（只有同一
  项目问得到「过期」与「从未有过」的区别——别的项目的 id 一律当不存在）。活跃任务不进这张表（只收终局），
  所以清理动不到它们。

纯标准库；Flask 父进程侧。
"""

from __future__ import annotations

import collections
import json
import re
import threading
import time
from dataclasses import dataclass, field

SNAPSHOT_VERSION = 1
DOCUMENT_SCHEMA = "tavotto.task-diagnostic"
DOCUMENT_VERSION = 1

KIND_EXPORT = "export"
KIND_PREPARATION = "preparation"
KIND_SCRIPT_RUN = "script_run"
KINDS = (KIND_EXPORT, KIND_PREPARATION, KIND_SCRIPT_RUN)

#: 登记表的上限。快照本身已被白名单限住（通常 1–3 KB），这里是第二道、与来源无关的闸。
MAX_ENTRIES = 48
MAX_ENTRY_BYTES = 6 * 1024
MAX_TOTAL_BYTES = 128 * 1024
#: 终局快照保留多久（秒）。比作业登记表（15 分钟）长得多：用户回来点「本次问题的诊断」时，作业早该过期了。
RETENTION_S = 6 * 3600
#: 被逐出 / 过期的 id 记多少条墓碑（只为答「过期」还是「从未有过」）。
MAX_TOMBSTONES = 128
#: 一条失败的后续尝试最多列几条。
MAX_LATER_ATTEMPTS = 8
#: 全局诊断包里带几份最近的任务快照。
BUNDLE_SNAPSHOTS = 8
#: 交出去的整份文档（单条 / 包内合集）的字节上限。
MAX_DOCUMENT_BYTES = 64 * 1024

REASON_NOT_FOUND = "not_found"
REASON_EXPIRED = "expired"

_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_HEX = re.compile(r"[0-9a-f]{6,64}")
_VERSION = re.compile(r"[0-9]{1,3}(\.[0-9]{1,4}){0,3}((a|b|rc)[0-9]{1,3})?")


# ------------------------------------------------------------------ 取值的形状守卫
# 投影函数用它们挑字段。**不合形状 → None（丢掉）**，不哈希、不截断后放行。


def code(value) -> str | None:
    """Tavotto 自己起的稳定码（小写标识符）。"""
    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


def ident(value) -> str | None:
    """不透明 id（作业 id / 计划 id / 本机引用）：字母数字加 `_-`，有长度上限。"""
    return value if isinstance(value, str) and _IDENT.fullmatch(value) else None


def digest(value) -> str | None:
    """文档修订这类十六进制摘要（前端按请求结构算的 64 位 FNV，不含任何原文）。"""
    return value if isinstance(value, str) and _HEX.fullmatch(value) else None


def version(value) -> str | None:
    return value if isinstance(value, str) and _VERSION.fullmatch(value) else None


def closed(value, allowed) -> str | None:
    """值必须是源码里某个闭集的成员。"""
    return value if isinstance(value, str) and value in allowed else None


def count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def number(value) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(value, 3) if isinstance(value, float) else value


def flag(value) -> bool | None:
    return value if isinstance(value, bool) else None


def stages(trace_payload: dict | None, phases) -> dict:
    """`Trace.to_payload()` → 阶段投影：阶段名、相对毫秒、结果、稳定码。**`facts` 不读**（那里可以放任意短串）。"""
    if not isinstance(trace_payload, dict):
        return {"events": [], "truncated": False, "dropped": 0}
    events = []
    for e in trace_payload.get("events") or []:
        if not isinstance(e, dict):
            continue
        item = {
            "phase": closed(e.get("phase"), phases),
            "at_ms": number(e.get("at_ms")),
            "outcome": closed(e.get("outcome"), ("ok", "failed", "cancelled")),
        }
        c = code(e.get("code"))
        if c:
            item["code"] = c
        events.append(item)
    return {
        "events": events,
        "truncated": bool(trace_payload.get("truncated")),
        "dropped": count(trace_payload.get("dropped")) or 0,
        "failed_phase": closed(trace_payload.get("failed_phase"), phases),
        "failed_code": code(trace_payload.get("failed_code")),
    }


def clean(obj):
    """投影出口的最后一道：去掉值为 None 的键（「没有」与「不合形状被丢」在快照里都读作缺席）。"""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [clean(v) for v in obj if v is not None]
    return obj


# ------------------------------------------------------------------ 预算
_LIST_KEYS = ("stages", "outputs")
_CORE_KEYS = ("snapshot_version", "kind", "attempt_id", "outcome", "target", "error", "timing")


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def fit(projection: dict, limit: int = MAX_ENTRY_BYTES) -> tuple[str, list[str]]:
    """序列化并压进单条上限。回 (JSON 文本, 被截掉的字段名)。**截断一定标出来**，不静默变短。"""
    work = json.loads(_dumps(projection))
    cut: list[str] = []
    text = _dumps(work)
    if len(text) <= limit:
        return text, cut
    for key in _LIST_KEYS:
        section = work.get(key)
        events = section.get("events") if isinstance(section, dict) else None
        if isinstance(events, list) and len(events) > 8:
            # 头（哪里开始）与尾（哪里坏）留着，丢中间
            section["omitted"] = len(events) - 8
            section["events"] = events[:4] + events[-4:]
            cut.append(key)
        elif isinstance(section, list) and len(section) > 4:
            work[key] = section[:4]
            work[f"{key}_omitted"] = len(section) - 4
            cut.append(key)
        if cut:
            work["truncated_fields"] = list(cut)
        text = _dumps(work)
        if len(text) <= limit:
            return text, cut
    core = {k: work[k] for k in _CORE_KEYS if k in work}
    cut = sorted(k for k in work if k not in core and k != "truncated_fields")
    core["truncated_fields"] = cut
    core["truncated"] = True
    text = _dumps(core)
    if len(text) > limit:  # 核心字段本身已被白名单限住；到这里只剩防御
        core = {k: core[k] for k in ("snapshot_version", "kind", "attempt_id", "outcome")}
        core["truncated"] = True
        text = _dumps(core)
    return text, cut


# ------------------------------------------------------------------ 登记表
@dataclass(frozen=True)
class Entry:
    project_id: str
    kind: str
    ref: str
    outcome: str
    failed: bool
    recorded_at: float
    blob: str  # 已冻结的投影 JSON（不可变，字节记账以它为准）
    subject: frozenset = field(
        default_factory=frozenset, repr=False
    )  # 私有：只用来连重试关系，不出门
    retry_of: str | None = None
    truncated_fields: tuple = ()

    @property
    def nbytes(self) -> int:
        return len(self.blob)


@dataclass(frozen=True)
class Lookup:
    entry: Entry | None
    reason: str  # "" | REASON_NOT_FOUND | REASON_EXPIRED


class Store:
    def __init__(
        self,
        *,
        max_entries: int = MAX_ENTRIES,
        max_entry_bytes: int = MAX_ENTRY_BYTES,
        max_total_bytes: int = MAX_TOTAL_BYTES,
        retention_s: float = RETENTION_S,
        max_tombstones: int = MAX_TOMBSTONES,
    ) -> None:
        self.max_entries = max_entries
        self.max_entry_bytes = max_entry_bytes
        self.max_total_bytes = max_total_bytes
        self.retention_s = retention_s
        self._entries: collections.OrderedDict[tuple, Entry] = collections.OrderedDict()
        self._tombstones: collections.OrderedDict[tuple, None] = collections.OrderedDict()
        self._max_tombstones = max_tombstones
        self._lock = threading.Lock()

    # ---- 写（只在终局调用一次；同一个 (项目, 类别, id) 第二次写入忽略——快照不可变）----
    def record(
        self,
        project_id,
        kind: str,
        ref,
        projection: dict,
        *,
        outcome: str,
        failed: bool,
        subject=(),
        now: float | None = None,
    ) -> Entry | None:
        if kind not in KINDS or not project_id or ident(ref) is None:
            return None
        now = time.time() if now is None else now
        text, cut = fit(projection, self.max_entry_bytes)
        key = (project_id, kind, ref)
        subj = frozenset(subject)
        with self._lock:
            self._expire(now)
            if key in self._entries:
                return self._entries[key]
            retry_of = None
            if subj:
                prev = self._latest_same_subject(project_id, kind, subj)
                if prev is not None and prev.failed:
                    retry_of = prev.ref
            entry = Entry(
                project_id=project_id,
                kind=kind,
                ref=ref,
                outcome=outcome,
                failed=bool(failed),
                recorded_at=now,
                blob=text,
                subject=subj,
                retry_of=retry_of,
                truncated_fields=tuple(cut),
            )
            self._entries[key] = entry
            self._tombstones.pop(key, None)
            self._evict(keep=key)
            return entry

    def _latest_same_subject(self, project_id, kind, subj) -> Entry | None:
        best = None
        for e in self._entries.values():
            if e.project_id == project_id and e.kind == kind and e.subject & subj:
                if best is None or e.recorded_at >= best.recorded_at:
                    best = e
        return best

    def _drop(self, key) -> None:
        self._entries.pop(key, None)
        self._tombstones[key] = None
        while len(self._tombstones) > self._max_tombstones:
            self._tombstones.popitem(last=False)

    def _expire(self, now: float) -> None:
        for key, e in list(self._entries.items()):
            if now - e.recorded_at > self.retention_s:
                self._drop(key)

    def _evict(self, keep) -> None:
        def total() -> int:
            return sum(e.nbytes for e in self._entries.values())

        while len(self._entries) > 1 and (
            len(self._entries) > self.max_entries or total() > self.max_total_bytes
        ):
            # 先逐最老的「没出问题」的：用户要找的是失败；成功的只是为了对照重试
            # 刚写进来的这一条不逐（否则「刚结束的成功」会被自己挤掉）
            victim = next((k for k, e in self._entries.items() if not e.failed and k != keep), None)
            if victim is None:
                victim = next(k for k in self._entries if k != keep)
            self._drop(victim)

    # ---- 读（调用方必须带项目 id；别的项目的记录对它不存在）----
    def get(self, project_id, kind: str, ref, *, now: float | None = None) -> Lookup:
        now = time.time() if now is None else now
        key = (project_id, kind, ref)
        with self._lock:
            self._expire(now)
            e = self._entries.get(key)
            if e is not None:
                return Lookup(e, "")
            return Lookup(None, REASON_EXPIRED if key in self._tombstones else REASON_NOT_FOUND)

    def latest_failure(self, project_id, kind: str | None = None) -> Entry | None:
        with self._lock:
            self._expire(time.time())
            hits = [
                e
                for e in self._entries.values()
                if e.project_id == project_id and e.failed and (kind is None or e.kind == kind)
            ]
        return max(hits, key=lambda e: e.recorded_at, default=None)

    def recent(self, project_id, limit: int = BUNDLE_SNAPSHOTS) -> list[Entry]:
        with self._lock:
            self._expire(time.time())
            mine = [e for e in self._entries.values() if e.project_id == project_id]
        # 失败在前，同档新的在前
        mine.sort(key=lambda e: (not e.failed, -e.recorded_at))
        return mine[:limit]

    def later_attempts(self, entry: Entry) -> list[dict]:
        with self._lock:
            hits = [
                e
                for e in self._entries.values()
                if e.project_id == entry.project_id
                and e.kind == entry.kind
                and e.retry_of == entry.ref
            ]
        hits.sort(key=lambda e: e.recorded_at)
        return [
            {"ref": e.ref, "outcome": e.outcome, "recorded_at": round(e.recorded_at, 3)}
            for e in hits[:MAX_LATER_ATTEMPTS]
        ]

    def stats(self) -> dict:
        with self._lock:
            return {
                "entries": len(self._entries),
                "bytes": sum(e.nbytes for e in self._entries.values()),
                "tombstones": len(self._tombstones),
            }

    def reset_for_tests(self) -> None:
        with self._lock:
            self._entries.clear()
            self._tombstones.clear()


STORE = Store()


# ------------------------------------------------------------------ 对外的一份文档
def document(store: Store, entry: Entry, *, now: float | None = None) -> dict:
    """交出去的一条：冻结的快照 + 重试关系 + 一段说清「这份东西是怎么来的」的声明。"""
    now = time.time() if now is None else now
    return {
        "schema": DOCUMENT_SCHEMA,
        "schema_version": DOCUMENT_VERSION,
        "available": True,
        "collection": {
            # 采集这份文档时没有执行任何脚本 / 安装 / 联网 / 解释器探测；下面的事实都是终局那一刻写下的
            "executed_anything": False,
            "current_state_included": False,
            "captured_at": round(entry.recorded_at, 3),
            "generated_at": round(now, 3),
        },
        "truncated": bool(entry.truncated_fields),
        "truncated_fields": list(entry.truncated_fields),
        "retry_of": entry.retry_of,
        "later_attempts": store.later_attempts(entry),
        "snapshot": json.loads(entry.blob),
    }


def unavailable(reason: str, *, kind: str | None = None) -> dict:
    """记录不存在 / 已过期时交回的**明确**答案——不生成当前状态、不回落到别的记录。"""
    return {
        "schema": DOCUMENT_SCHEMA,
        "schema_version": DOCUMENT_VERSION,
        "available": False,
        "reason": reason if reason in (REASON_NOT_FOUND, REASON_EXPIRED) else REASON_NOT_FOUND,
        "kind": kind if kind in KINDS else None,
        "collection": {"executed_anything": False, "current_state_included": False},
    }


def bundle_section(store: Store, project_id, *, limit: int = BUNDLE_SNAPSHOTS) -> dict:
    """全局诊断包里的「最近的任务快照」：同一份冻结投影，整体有字节上限。"""
    docs: list[dict] = []
    used = 0
    omitted = 0
    for entry in store.recent(project_id, limit):
        doc = document(store, entry)
        size = len(_dumps(doc))
        if used + size > MAX_DOCUMENT_BYTES:
            omitted += 1
            continue
        used += size
        docs.append(doc)
    return {
        "schema": DOCUMENT_SCHEMA,
        "schema_version": DOCUMENT_VERSION,
        "snapshots": docs,
        "omitted": omitted,
        "collection": {"executed_anything": False, "current_state_included": False},
    }


def reset_for_tests() -> None:
    STORE.reset_for_tests()
