"""项目接入就绪度：素材与源脚本的关系**事实模型**（只诊断，绝不动手）。

界面上「这张图能不能进图内编辑」这句话，此前散在三个地方各答一次：
`/api/panels` 给不给 `script`（注册表映射）、`/api/registry` 的 candidates
（静态扫描）、`probe.script_inventory()` 的 reason（脚本视角）。三份判据的
主语各不相同——**一份按素材、一份按 stem、一份按脚本**——于是同一张图在素材
面板里"不可编辑"、在注册表对话框里"有候选脚本"、在脚本清单里"可试运行"，
三句话都对，合起来却没有一句回答了用户的问题。

本模块把那三份事实合成**一句**，主语固定为 `/api/panels` 的那一个素材：

    editable       已连接源脚本，可进图内编辑
    auto_linkable  静态已能唯一确定脚本，但还没登记成功
    needs_probe    有产图脚本，但输出文件名要跑一遍才知道
    conflict       同一个 stem 被多个脚本认领，机器不裁决
    source_missing 注册表声明了映射，脚本文件已不在
    layout_only    没有可靠源脚本，仍可缩放/裁剪/对齐/标注/导出

裁决与理由的权威是 `docs/adr/0027-panel-readiness-fact-model.md`；这里只放
读代码时够用的那一层。

### 边界

* **不执行用户脚本**（共享规则 §4）：只读注册表 JSON、只读 AST
  （`discover.discover()`）、只 `stat()`。probe 是用户显式动作，本模块连
  `engine/probe.py` 都不 import——边界靠依赖方向守着，不靠注释。
* **不写盘、不改注册表、不发事件、不作废 worker。** 它是 `refresh` 的读者，
  不是第二个刷新器（ADR 0025：编排只有 `refresh_project_index()` 一份）。
* **不增强解析器**：候选关系全部来自 `discover` 已有的输出。

### 「没测量」不是「测量结果是零」

静态扫描失败时（目录读不动）本模块**不报 `no_source_candidate`**——那是一句
错的断言。它报 `layout_only` + `source_scan_unavailable`，`conflicts` 给
`null` 而不是 `[]`，项目级 `scan_ok: false`。状态仍然是 `layout_only`，因为
那是此刻唯一还成立的能力陈述（这张图还能排版）；reason code 负责说清
「我们这一轮没看见」。

纯标准库：Flask 父进程 import 它。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import discover, project_refresh, registry

# ---------------------------------------------------------------------------
# 状态与 reason code —— **闭集**，前端按 code 查自己的文案
#
# 枚举一旦发布就不能改名（与 app.py 的错误 code 同一套约定）：改了等于让装着
# 旧前端的用户看到一串 key。不同 API、不同界面**不许另起同义状态**。
# ---------------------------------------------------------------------------
EDITABLE = "editable"
AUTO_LINKABLE = "auto_linkable"
NEEDS_PROBE = "needs_probe"
CONFLICT = "conflict"
SOURCE_MISSING = "source_missing"
LAYOUT_ONLY = "layout_only"

#: summary 里逐项计数的顺序，也是「合法状态」的唯一出处。
STATUSES = (EDITABLE, AUTO_LINKABLE, NEEDS_PROBE, CONFLICT, SOURCE_MISSING, LAYOUT_ONLY)

REASON_REGISTERED = "registered_source"
REASON_STATIC_UNIQUE = "static_unique_candidate"
REASON_RUNTIME_UNKNOWN = "runtime_output_unknown"
REASON_MULTIPLE_CANDIDATES = "multiple_source_candidates"
REASON_SCRIPT_MISSING = "registered_script_missing"
REASON_NO_CANDIDATE = "no_source_candidate"
REASON_READ_ONLY = "project_read_only"
REASON_REGISTRY_INVALID = "registry_invalid"
REASON_REGISTRY_WRITE_FAILED = "registry_write_failed"
#: 这一轮**没有**跑成静态扫描。与 `no_source_candidate` 是两件事：后者是
#: 「量过了，没有候选」，前者是「没量」。合并成一个 code 的话，一次瞬时的
#: 目录读错误会让整个项目看起来"确实没有可编辑的图"。
REASON_SCAN_UNAVAILABLE = "source_scan_unavailable"

#: 状态 → 它允许出现的 reason code。**判定表的机器可读版本**，`compute()`
#: 的输出逐条对着它断言（`tests/test_project_readiness.py`）：判定分支以后
#: 再长，也不会悄悄冒出一个前端没见过的组合。
REASONS_BY_STATUS: dict[str, tuple[str, ...]] = {
    EDITABLE: (REASON_REGISTERED,),
    AUTO_LINKABLE: (
        REASON_REGISTRY_INVALID,
        REASON_READ_ONLY,
        REASON_REGISTRY_WRITE_FAILED,
        REASON_STATIC_UNIQUE,
    ),
    NEEDS_PROBE: (REASON_RUNTIME_UNKNOWN,),
    CONFLICT: (REASON_MULTIPLE_CANDIDATES,),
    SOURCE_MISSING: (REASON_SCRIPT_MISSING,),
    LAYOUT_ONLY: (REASON_NO_CANDIDATE, REASON_SCAN_UNAVAILABLE),
}

#: `/api/panels` 每个 panel 上挂的 capability 子集。**与就绪度同源**：
#: 这里列的每个键都直接取自同一份报告，`/api/panels` 不自己算第二遍。
CAPABILITY_FIELDS = (
    "status",
    "reason_code",
    "script",
    "candidates",
    "can_probe",
    "can_manual_link",
)


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
@dataclass
class ReadinessCache:
    """项目级缓存。挂在 `RefreshState.readiness` 上，随项目一起消亡。

    两层，各自的键都是**输入的内容签名**而不是时间：

    * `scan_sig` / `report` —— 贵的那一层（逐脚本 `ast.parse`）；
    * `body_sig` / `body`  —— 整份报告，键里含注册表、素材集合、可写性、
      注册表合法性与上一次写失败与否。

    **失败一律不进缓存**：静态扫描抛错时两层都不落，下一次请求重新试。
    缓存一次失败等于让一次瞬时错误把就绪度永久钉死在「没测量」，而用户
    看到的现象是"修好了它还是说不行"。

    **进出都深拷贝**：缓存里那份是唯一权威，调用方拿到的永远是它的副本。
    共享一个可变 dict 出去的话，某个消费者往 `candidates` 里 append 一下，
    之后每一次请求都会带着那条脏数据——而它看起来完全像是后端算出来的。
    内存上限就是一份报告（O(素材数) 个小 dict），随项目关闭一起消失。
    """

    scan_sig: str | None = None
    report: dict | None = None
    body_sig: str | None = None
    body: dict | None = None


def cache_of(ctx) -> ReadinessCache:
    """取（必要时创建）项目的就绪度缓存。**调用方必须已持有刷新锁。**"""
    state = project_refresh.state_of(ctx)
    cache = state.readiness
    if not isinstance(cache, ReadinessCache):
        cache = ReadinessCache()
        state.readiness = cache
    return cache


def invalidate(ctx) -> None:
    """丢掉这个项目的就绪度缓存（测试与非刷新路径用）。

    统一刷新走的是另一条：它在**确认事实真的动了**之后直接把
    `RefreshState.readiness` 清成 `None`（`project_refresh` 不 import 本模块，
    否则依赖成环）。
    """
    state = project_refresh.state_of(ctx)
    with state.lock:
        state.readiness = None


# ---------------------------------------------------------------------------
# 签名与 fingerprint
# ---------------------------------------------------------------------------
def _canonical(payload: object) -> bytes:
    """规范化 JSON 字节：键排序、无空白、非 ASCII 不转义。

    `sort_keys` 是关键——fingerprint 不能依赖 dict 的插入顺序，否则某天有人
    调换两行赋值，前端就会以为整个项目变了。
    """
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _digest(payload: object) -> str:
    """仓库既有的哈希规范：SHA-256 取前 32 个十六进制位
    （`atomicio.content_revision` 同一口径）。"""
    return hashlib.sha256(_canonical(payload)).hexdigest()[:32]


def fingerprint(body: dict) -> str:
    """报告 → 稳定 fingerprint。

    **定义就是「这份报告的内容哈希」**，而不是「输入的哈希」。于是四条要求
    自动成立，不用逐条去防：`generated_at` 不在 body 里所以进不来；素材
    mtime、脚本 mtime 这些**没有进报告**的输入变了它不动；绝对路径本来就
    一个都不在报告里；键序由 `_canonical` 排掉。反过来，任何一个会被用户
    看见的事实变了，它必然变——因为那个事实就在被哈希的字节里。
    """
    return _digest(body)


def _script_signature(root: Path) -> str:
    """静态扫描输入的签名：候选脚本集合 + 各自的 `(size, mtime_ns)`。

    与项目 watcher（`engine/project_watch.py`）**用同一把尺**，不另立一套更严
    的判据：watcher 发现不了的就地改写（同尺寸 + 同一个 mtime_ns 刻度），
    这里也发现不了，而那时根本不会有人来刷新。真要收紧得两边一起收紧，
    只把就绪度改成内容哈希只会让两个模块对"变了没有"给出不同答案。
    刷新那一侧另有一道显式失效（见 `ReadinessCache` 的说明）。
    """
    parts: list[list] = []
    for path in discover.iter_scripts(root):
        try:
            st = path.stat()
        except OSError:  # 遍历与 stat 之间被删掉了：当它不在
            continue
        parts.append([discover.rel_key(path, root), st.st_size, st.st_mtime_ns])
    return _digest(parts)


# ---------------------------------------------------------------------------
# 事实采集
# ---------------------------------------------------------------------------
def _registry_on_disk_ok(root: Path) -> bool | None:
    """磁盘上那份注册表读得回来吗？`None` = 项目里根本没有注册表文件。

    校验复用 `Registry.load_data()`（判据的唯一出处），且用**新实例**——
    绝不能碰 ctx 手里那份：就绪度是只读诊断，把内存里正在用的注册表换掉
    是它最不该做的事。
    """
    path = registry.existing_registry_path(root)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    try:
        registry.Registry().load_data(data, source=str(path))
    except RuntimeError:
        return False
    return True


def _static_report(root: Path, cache: ReadinessCache, scan_sig: str) -> dict | None:
    """静态候选关系。`None` = 这一轮没扫成（**不是**「没有候选」）。"""
    if cache.scan_sig == scan_sig and cache.report is not None:
        return cache.report
    try:
        report = discover.discover(root)
    except (OSError, ValueError, RuntimeError):
        cache.scan_sig, cache.report = None, None  # 失败不进缓存
        return None
    cache.scan_sig, cache.report = scan_sig, report
    return report


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
def _blocked_reason(registry_valid: bool | None, writable: bool, write_failed: bool) -> str | None:
    """静态已能唯一确定脚本，却还没变成 `editable`——卡在哪一步？

    优先级从"再刷新多少次都没用"往"下一次刷新就好了"排：注册表本身读不回来
    时，可写不可写都无关紧要；能写但上一次真的写失败了，比"还没轮到写"更
    具体。`None` = 没有已知阻塞，正常刷新一次就会登记上（这也是可写项目里
    刚加进来的脚本会短暂停留的那一档）。
    """
    if registry_valid is False:
        return REASON_REGISTRY_INVALID
    if not writable:
        return REASON_READ_ONLY
    if write_failed:
        return REASON_REGISTRY_WRITE_FAILED
    return None


def _capability(
    stem: str,
    *,
    owner: dict[str, str],
    scripts: dict[str, dict],
    claims: dict[str, list[str]] | None,
    dynamic: list[str],
    root: Path,
    writable: bool,
    blocked: str | None,
) -> dict:
    """一个 stem 的能力事实。**每个 panel 只落在一个状态上**（分支互斥）。

    判定顺序即优先级：

    1. 注册表声明了映射 → 脚本在不在决定 `editable` / `source_missing`。
       **注册表优先于静态扫描**：它就是人工裁决的落处（一脚本多产物、
       归属有歧义的 stem 都记在图库自己的注册表文件里），拿静态报告去推翻
       它等于每次刷新都把用户的裁决重新掀一遍；
    2. 静态扫描没跑成 → `layout_only` + 「没测量」；
    3. 多个脚本认领同一个 stem → `conflict`，**绝不按文件名相似度、修改时间
       或任何猜测自动选一个**；
    4. 恰好一个脚本认领 → `auto_linkable`；
    5. 项目里有产图但文件名要跑才知道的脚本 → `needs_probe`；
    6. 其余 → `layout_only`。
    """
    script = owner.get(stem)
    if script is not None:
        cfg = scripts.get(script) or {}
        details = {"entry": cfg.get("entry", ""), "cost": cfg.get("cost", "")}
        if (root / script).is_file():
            return _entry(EDITABLE, REASON_REGISTERED, script, [], writable, details)
        # 脚本没了，图还在：仍然能排版，**不是**「文件损坏」。别的脚本此刻
        # 正好认领同一个 stem（改名/重构最常见的形状）就把它列成候选——
        # 状态照旧是 source_missing（注册表说的还是那个不存在的文件），
        # 但用户有一条可执行的出路，而不是只有一句"它不见了"。
        others = [s for s in (claims or {}).get(stem, []) if s != script]
        return _entry(
            SOURCE_MISSING, REASON_SCRIPT_MISSING, script, sorted(others), writable, details
        )

    if claims is None:
        return _entry(LAYOUT_ONLY, REASON_SCAN_UNAVAILABLE, None, [], writable, {})

    candidates = sorted(claims.get(stem, []))
    if len(candidates) > 1:
        return _entry(CONFLICT, REASON_MULTIPLE_CANDIDATES, None, candidates, writable, {})
    if len(candidates) == 1:
        return _entry(
            AUTO_LINKABLE, blocked or REASON_STATIC_UNIQUE, None, candidates, writable, {}
        )
    if dynamic:
        # 候选是**项目级**的：静态扫描解不出这些脚本的输出文件名，所以说不出
        # 「这张图来自其中哪一个」，只能说「跑一个就知道了」。`candidate_scope`
        # 让界面能如实措辞，而不是把一份项目级清单说成这张图的来源。
        return _entry(
            NEEDS_PROBE,
            REASON_RUNTIME_UNKNOWN,
            None,
            list(dynamic),
            writable,
            {"candidate_scope": "project"},
        )
    return _entry(LAYOUT_ONLY, REASON_NO_CANDIDATE, None, [], writable, {})


def _entry(
    status: str,
    reason_code: str,
    script: str | None,
    candidates: list[str],
    writable: bool,
    details: dict,
) -> dict:
    """能力事实的统一形状。

    `can_probe` 只有一条规则：**手里有具体候选脚本才为 true**。于是
    `editable`（已绑定，没有待确认的候选）与 `layout_only`（压根没有候选）
    自然是 false，不用为每个状态各写一遍。
    `can_manual_link` 看项目可不可写——手工登记要落进
    `tavotto_registry.json`，只读项目上给出这个按钮等于让用户按了才发现不行。
    两者都只说"界面可以提供这个动作"，**不代表本模块会去执行它**（§十）。
    """
    if candidates:
        details = {**details, "candidate_scope": details.get("candidate_scope", "panel")}
    return {
        "status": status,
        "reason_code": reason_code,
        "script": script,
        "candidates": candidates,
        "can_probe": bool(candidates),
        "can_manual_link": writable,
        "details": details,
    }


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def compute(ctx) -> dict:
    """算一份就绪度报告（**不含 `generated_at`**，那是发响应时才盖的时间）。

    `ctx` 与刷新要的是同三样东西：`path` / `id` / `registry`。

    全程持有该项目的刷新锁（`RefreshState.lock`，可重入）：读到半更新的注册表
    比读到旧的更糟——旧的至少自洽。跨项目不互相阻塞（锁是每项目一把），
    同项目的第二个请求排在后面，而它一进来就命中缓存。
    """
    root = Path(ctx.path)
    state = project_refresh.state_of(ctx)

    with state.lock:
        cache = cache_of(ctx)

        entries = ctx.registry.entries()
        # 顺序**不在这里定**：`iter_assets()` 的遍历顺序是它自己的事
        # （确定，但不是本模块的契约），报告的排序规则只有下面 `panels` 那一处。
        # 两处各排一次的话，谁也杀不死谁——删掉任意一处都还有另一处兜着，
        # 于是"排序稳定"这条判据在变异下永远是绿的。
        assets = [(str(p.relative_to(root)), kind) for p, kind in project_refresh.iter_assets(root)]
        writable = os.access(root, os.W_OK)
        registry_valid = _registry_on_disk_ok(root)
        write_failed = state.registry_write_failed
        scan_sig = _script_signature(root)

        body_sig = _digest(
            {
                "registry": entries,
                "assets": assets,
                "scan": scan_sig,
                "writable": writable,
                "registry_valid": registry_valid,
                "write_failed": write_failed,
            }
        )
        if cache.body_sig == body_sig and cache.body is not None:
            return copy.deepcopy(cache.body)

        report = _static_report(root, cache, scan_sig)
        claims = None if report is None else discover.claims_of(report["scripts"])
        dynamic = (
            []
            if report is None
            else sorted(s for s, info in report["scripts"].items() if info["dynamic_names"])
        )
        owner = {stem: script for script, cfg in entries.items() for stem in cfg["stems"]}
        blocked = _blocked_reason(registry_valid, writable, write_failed)

        # 同一个 stem 的多份素材（Fig1.pdf 与另一个目录下的 Fig1.png）**共享
        # 同一条来源关系**：算一次，两个 panel 各计一次数。
        by_stem: dict[str, dict] = {}
        panels: list[dict] = []
        for rel, _kind in assets:
            stem = Path(rel).stem
            cap = by_stem.get(stem)
            if cap is None:
                cap = by_stem[stem] = _capability(
                    stem,
                    owner=owner,
                    scripts=entries,
                    claims=claims,
                    dynamic=dynamic,
                    root=root,
                    writable=writable,
                    blocked=blocked,
                )
            panels.append({"id": rel, **cap})

        summary = {"total": len(panels)}
        summary.update({s: 0 for s in STATUSES})
        for p in panels:
            summary[p["status"]] += 1

        body = {
            "project_id": ctx.id,
            "summary": summary,
            # 排序稳定（按 id），否则 UI 每次刷新都在抖
            "panels": sorted(panels, key=lambda p: p["id"]),
            # `null` = 这一轮没跑静态扫描。**缺席不是零**——把没测量说成
            # 「没有冲突」，用户会一直等一个永远不来的提示。
            "conflicts": None
            if report is None
            else [
                {"stem": s, "candidates": list(cs), "resolved_by": owner.get(s)}
                for s, cs in sorted(report["conflicts"].items())
            ],
            "project": {
                "writable": writable,
                # `None` = 项目里没有注册表文件（还没起草过），与"有、但坏了"
                # 是两回事。
                "registry_valid": registry_valid,
                "scan_ok": report is not None,
                # 重扫是项目级动作，不按 panel 给：一次扫描覆盖整个项目。
                "can_rescan": True,
            },
            "issues": _issues(registry_valid, writable, write_failed, report is not None),
        }
        body["fingerprint"] = fingerprint(body)
        if report is not None:  # 扫描失败的那一份不缓存，下次请求重试
            cache.body_sig, cache.body = body_sig, copy.deepcopy(body)
        return body


def _issues(
    registry_valid: bool | None, writable: bool, write_failed: bool, scan_ok: bool
) -> list[dict]:
    """项目级问题：稳定 code + 结构化 params，**不含绝对路径**。

    注册表校验失败的异常原文里带着文件的绝对路径（`Registry.load_data` 的
    `source`），所以这里只给文件名——诊断细节走既有的
    `/api/diagnostics`，不从就绪度这条只读通道漏出用户的目录结构。
    """
    out: list[dict] = []
    if registry_valid is False:
        out.append({"code": REASON_REGISTRY_INVALID, "params": {"file": registry.REGISTRY_NAME}})
    if not writable:
        out.append({"code": REASON_READ_ONLY, "params": {}})
    if write_failed:
        out.append({"code": REASON_REGISTRY_WRITE_FAILED, "params": {}})
    if not scan_ok:
        out.append({"code": REASON_SCAN_UNAVAILABLE, "params": {}})
    return out


def capability_map(ctx) -> dict[str, dict]:
    """素材 id → capability 子集，给 `/api/panels` 挂在每个 panel 上。

    **与 `/api/project/readiness` 同源**：同一次 `compute()` 的输出投影出去，
    `/api/panels` 不自己算第二遍。两处各算一遍的话，界面上"素材面板说可编辑、
    就绪度面板说要试运行"这种自相矛盾只是时间问题。
    """
    body = compute(ctx)
    return {p["id"]: {k: p[k] for k in CAPABILITY_FIELDS} for p in body["panels"]}
