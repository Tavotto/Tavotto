"""MCP 工具与 Tavotto 引擎之间的那一层 —— **只翻译，不实现**。

会话、manifest、override 语义、patch 规范化、导出全部落回
`tavotto.engine.{pool,registry,handoff,patchspec,profiles,preflight}`。
本模块负责的只有三件事：

1. **路径范围校验**：Codex 会把任意路径喂进来，越界的一律拒；
2. **会话账本**：session_id ↔ (项目, stem, worker)；
3. **响应形状**：把引擎的返回整理成 Codex 读得懂的 JSON。

不变式与 Tavotto 本体完全一致（`docs/adr/0003-worker-protocol-v1.md`）：

    hot_apply(canonical_patches)
      == fresh_worker_replay(canonical_patches)
      == writeback_then_reopen(canonical_patches)

之所以成立，是因为这里发给 worker 的 patches 与 Flask 发的是同一条路径
（`pool.EngineWorker.override` / `.export`），**没有第二套应用逻辑**。

纯标准库 + tavotto 本体。
"""
from __future__ import annotations

import base64
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from tavotto.engine import (
    config as engine_config,
    handoff as engine_handoff,
    patchspec,
    pool as engine_pool,
    preflight as engine_preflight,
    profiles as engine_profiles,
    registry as engine_registry,
    telemetry as engine_telemetry,
)

#: 允许打开的项目根（os.pathsep 分隔）。
ROOTS_ENV = "TAVOTTO_MCP_ROOTS"
#: 工作区提示：装好的插件里 `.mcp.json` 的 `cwd` 指向**插件自己的目录**
#: （`./mcp/server.py` 要靠它解析），于是「不给就用进程 cwd」在真实安装下
#: 等于把用户工作区里的每一张图都判成 `path_out_of_scope`——默认流程根本
#: 跑不起来。所以 cwd 只在它**不是插件目录**时才算数（源码树里直接跑
#: `python codex-plugin/mcp/server.py` 的开发态就是这一格），否则退回宿主
#: 传过来的工作区变量。一个都拿不到时**报错说清楚要设什么**，绝不就近
#: 挑一个目录顶上。
WORKSPACE_ENVS = ("TAVOTTO_MCP_WORKSPACE", "CODEX_WORKSPACE_ROOT",
                  "CODEX_PROJECT_ROOT", "CODEX_WORKSPACE_DIR")
#: 插件包自己所在的目录（`codex-plugin/`）——cwd 落在它里面就说明这是
#: Codex 用来定位 `./mcp/server.py` 的那个 cwd，不是用户的工作区。
_PLUGIN_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
#: 会话上限：一个 Codex 会话同时端着几十张图没有意义，而每个 worker 都是一个
#: 常驻 Python 进程（几百 MB）。超了按最久未用淘汰。
MAX_SESSIONS = 8
#: 导出格式白名单。svg 走 matplotlib 自己的序列化，与 PDF 同源。
EXPORT_FORMATS = ("pdf", "png", "svg")


class BridgeError(RuntimeError):
    """带机器可读 code 的失败。工具层转成 `isError` 结果，绝不吞。"""

    def __init__(self, message: str, code: str = "bridge_error", **extra) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra

    def payload(self) -> dict:
        return {"ok": False, "code": self.code, "error": str(self), **self.extra}


# ----------------------------- 路径范围校验 ---------------------------------
def allowed_roots() -> list[str]:
    raw = (os.environ.get(ROOTS_ENV) or "").strip()
    if raw:
        return [os.path.realpath(os.path.expanduser(p))
                for p in raw.split(os.pathsep) if p.strip()]
    for name in WORKSPACE_ENVS:
        hint = (os.environ.get(name) or "").strip()
        if hint:
            return [os.path.realpath(os.path.expanduser(hint))]
    cwd = os.path.realpath(os.getcwd())
    if not _within(cwd, _PLUGIN_DIR):
        return [cwd]
    return []


def _no_roots_error() -> "BridgeError":
    """一个可用的根都没有时说人话。

    静默放行等于没有边界，静默拒绝等于「装了插件但什么都打不开」且毫无线索
    ——两种都不行，所以这里把**要设哪个变量**直接写出来。
    """
    return BridgeError(
        f"没有可用的项目根：{ROOTS_ENV} 没设，宿主也没给工作区目录"
        f"（找过 {', '.join(WORKSPACE_ENVS)}），而进程 cwd 是插件自己的目录。"
        f"把 {ROOTS_ENV} 设成你的工作目录（{os.pathsep} 分隔多个）再试。",
        code="no_workspace_root", roots=[])


def _within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:      # Windows 上跨盘符 commonpath 直接抛
        return False


def check_scope(path: str) -> str:
    """把用户给的路径规范化，并确认它落在允许的根之内。

    **越界一律拒绝，绝不「就近找一个能用的」**：Codex 传来的路径可能来自模型
    的推断，静默换一个目录打开等于在用户没看见的地方改文件。
    """
    real = os.path.realpath(os.path.expanduser(str(path)))
    roots = allowed_roots()
    if not roots:
        raise _no_roots_error()
    if any(_within(real, r) for r in roots):
        return real
    raise BridgeError(
        f"路径不在允许的范围内: {real}（允许的根: {os.pathsep.join(roots)}）。"
        f"要放开别的目录，把 {ROOTS_ENV} 设成它们（{os.pathsep} 分隔）。",
        code="path_out_of_scope", roots=roots, path=real)


# -------------------------------- 会话 --------------------------------------
@dataclass
class Session:
    id: str
    project: str
    stem: str
    script: str
    entry: str
    profile: dict
    #: 最近一次成功应用的 patches（**全量列表语义**，与前端一致）
    patches: list = field(default_factory=list)
    manifest: dict | None = None
    svg: str | None = None
    rev: int = 0
    created: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)

    def patch_hash(self) -> str:
        return patchspec.patch_hash(self.patches)

    def acquire(self):
        """**每次操作前**从池里重新取 worker，绝不长期抱着引用。

        两个上限是不同的数，而且必然会打架：池按 `MAX_ALIVE`（3）做 LRU
        淘汰，这里的会话上限是 `MAX_SESSIONS`（8）。开到第 4 个脚本时，
        第 1 个会话手里那个 worker 已经被池 shutdown 了，可它在账本里还
        「开着」——用户看到的是「会话明明还在，一 apply 就说 worker 死了」，
        而且没有任何办法恢复。`pool.get()` 本来就负责「死了就重建」，
        每次问它一遍即可，代价是一次字典查找。
        """
        try:
            return engine_pool.get(self.script, self.project, self.entry)
        except engine_pool.WorkerError as exc:
            raise BridgeError(str(exc), code=exc.code or "worker_error",
                              traceback=exc.traceback_text) from exc


_SESSIONS: dict[str, Session] = {}


def sessions() -> dict[str, Session]:
    return _SESSIONS


def get_session(session_id: str) -> Session:
    s = _SESSIONS.get(session_id)
    if s is None:
        known = ", ".join(sorted(_SESSIONS)) or "（没有打开的会话）"
        raise BridgeError(
            f"没有这个会话: {session_id}。先调用 tavotto_open_figure。已打开: {known}",
            code="unknown_session")
    s.last_used = time.time()
    return s


def close_session(session_id: str) -> dict:
    s = _SESSIONS.pop(session_id, None)
    if s is None:
        return {"ok": True, "closed": False,
                "note": f"会话 {session_id} 已经不在了（重复关闭不算错）"}
    # worker 归 pool 管（同一个脚本可能还有别的用户）：这里只丢引用与会话账本。
    # 用户的项目数据一个字节都不动。
    return {"ok": True, "closed": True, "session_id": session_id,
            "project": s.project, "stem": s.stem}


def _evict_if_needed() -> None:
    while len(_SESSIONS) > MAX_SESSIONS:
        oldest = min(_SESSIONS.values(), key=lambda s: s.last_used)
        _SESSIONS.pop(oldest.id, None)


def shutdown_all() -> None:
    """进程退出前收摊：会话账本清空 + 关掉 worker 子进程（不留孤儿）。"""
    _SESSIONS.clear()
    try:
        engine_pool.shutdown_all(wait=True)
    except Exception:                       # noqa: BLE001 — 收尾不许连累退出
        pass


# ------------------------------ 打开一张图 -----------------------------------
def _pick_stem(project: str, stem: str | None, registry) -> str:
    if stem:
        if registry.for_stem(stem) is None:
            raise BridgeError(
                f"注册表里没有 stem「{stem}」——这张图没有对应脚本，只能当素材排版。"
                "把产出它的 .py 放到产物同一个目录，并让产物名是脚本里的字面量。",
                code="stem_not_parameterizable",
                known=sorted(registry.entries()) and _all_stems(registry))
        return stem
    on_disk = []
    for script in registry.all_scripts():
        for s in registry.stems_of(script):
            for ext in engine_handoff.OUT_EXTS:
                if os.path.isfile(os.path.join(project, s + ext)):
                    on_disk.append(s)
                    break
    if not on_disk:
        raise BridgeError(
            f"{project} 里没有任何已登记且产物在磁盘上的图。先把脚本跑一遍。",
            code="no_figure")
    if len(on_disk) > 1:
        raise BridgeError(
            f"这个项目里有多张图，得点名要哪一张: {', '.join(sorted(on_disk))}",
            code="stem_required", stems=sorted(on_disk))
    return on_disk[0]


def _all_stems(registry) -> list[str]:
    return sorted({s for script in registry.all_scripts()
                   for s in registry.stems_of(script)})


def open_figure(target: str, *, stem: str | None = None,
                profile_id: str | None = None,
                journal: dict | None = None,
                include_png: bool = False) -> dict:
    """解析 → 登记 → 起会话 → 渲染一次。返回给 Codex 的第一份快照。

    `target` 可以是产物、脚本或图库目录——解析规则复用 `engine/handoff.py`
    （`tavotto open` 走的是同一条），**这里不另写一套判断**。
    """
    real = check_scope(target)
    if not os.path.exists(real):
        raise BridgeError(f"路径不存在: {real}", code="not_found")
    try:
        found = engine_handoff.resolve_target(real)
    except engine_handoff.HandoffError as exc:
        raise BridgeError(str(exc), code="handoff_failed") from exc

    # **范围校验必须在 `ensure_registered` 之前**：解析会沿目录向上找
    # `tavotto_registry.json`（最多三层），允许的根嵌套在一个本身已是图库的目录
    # 下面时，`found.project` 会落到根之外。而 `ensure_registered` 是**写**
    # 操作（合并并写回注册表）——先登记后校验的话，一次「最终被拒绝」的
    # 调用照样改了范围外的文件，边界就形同虚设了。
    project = check_scope(found.project)
    try:
        reg_info = engine_handoff.ensure_registered(project, found.stem or stem)
    except engine_handoff.HandoffError as exc:
        raise BridgeError(str(exc), code="handoff_failed") from exc
    try:
        registry = engine_registry.open_registry(project)
    except FileNotFoundError as exc:
        raise BridgeError(
            f"{project} 里没有脚本注册表（tavotto_registry.json），"
            "这个目录还不是一个 Tavotto 图库。",
            code="no_registry") from exc
    except RuntimeError as exc:                 # 注册表损坏 / 重复 stem
        raise BridgeError(f"注册表无法加载: {exc}", code="bad_registry") from exc

    want = stem or found.stem
    chosen = _pick_stem(project, want, registry)
    info = registry.for_stem(chosen)
    assert info is not None

    # 目录级交接时 `ensure_registered` 还不知道要哪个 stem，`parameterizable`
    # 会是 None。stem 定下来之后必须补判——留着 None 等于把「这张图能不能进
    # 图内编辑」这个最要紧的结论交白卷。
    if reg_info.get("parameterizable") is None:
        reg_info["parameterizable"] = registry.for_stem(chosen) is not None

    # `run_preflight` 那条路早就把 ProfileError 翻成了 `unknown_profile`，
    # 这条入口漏了——同一个坏 profile_id，从 open 进来是一条泛化的 JSON-RPC
    # internal error（调用方分诊不了），从 preflight 进来才是可读的 code。
    try:
        profile = engine_profiles.load(profile_id, journal)
    except engine_profiles.ProfileError as exc:
        raise BridgeError(str(exc), code="unknown_profile") from exc

    session = Session(id="s-" + uuid.uuid4().hex[:12], project=project, stem=chosen,
                      script=info["script"], entry=info["entry"], profile=profile)
    # **先渲染成功，再登记会话**：脚本 build 阶段抛异常时调用方只拿到一个
    # 错误，永远拿不到 session_id，也就永远关不掉它。反复失败的 open 会把
    # 账本堆满，再靠 `_evict_if_needed()` 把**真正在用的**会话挤出去。
    render = _render(session, [], preview_dpi=None)
    _SESSIONS[session.id] = session
    _evict_if_needed()
    out = {
        "ok": True,
        "session_id": session.id,
        "project": project,
        "stem": chosen,
        "script": info["script"],
        "entry": info["entry"],
        "cost": info.get("cost", ""),
        "registry": {
            "parameterizable": reg_info.get("parameterizable"),
            "conflicts": reg_info.get("conflicts", []),
            "dynamic_names": reg_info.get("dynamic_names", []),
            "stems": _all_stems(registry),
        },
        "profile": engine_profiles.stamp(profile),
        **render,
    }
    if include_png:
        # 位图是**顺带产物**，不是这次 open 的成败判据。
        #
        # 它由第二次独立的 worker 调用产出（超时/崩溃/磁盘错误都可能），而
        # 会话此刻**已经建好并登记**了。让它抛出去的话，调用方收到的是一条
        # isError 结果，`BridgeError.payload()` 里没有 session_id——于是这条
        # 会话谁也关不掉，占着账本直到被 `_evict_if_needed()` 挤出去，
        # 而被挤掉的往往是**真正在用**的那条。
        #
        # 失败不静默：降级但如实回一个 code，调用方要么重试要么就看 SVG
        # （显示本来就走 SVG，位图只是给不能渲染 SVG 的 host 兜底）。
        try:
            out["preview_png_base64"] = preview_png(session, [], 1600)
        except BridgeError as exc:
            out["preview_png_error"] = exc.code or "preview_failed"
    return out


# ------------------------------- 渲染 / 应用 ---------------------------------
def _render(session: Session, patches: list, *, preview_dpi: int | None) -> dict:
    worker = session.acquire()
    try:
        resp = worker.override(session.stem, patches, preview_dpi,
                               inline_svg=True)
    except engine_pool.WorkerError as exc:
        raise BridgeError(str(exc), code=exc.code or "render_failed",
                          traceback=exc.traceback_text,
                          module=getattr(exc, "module", "")) from exc
    session.patches = list(patches)
    session.manifest = resp["manifest"]
    session.svg = resp.get("svg")
    session.rev = getattr(worker, "rev", session.rev + 1)
    session.last_used = time.time()
    return {
        "manifest": session.manifest,
        "svg": session.svg,
        "patch_hash": session.patch_hash(),
        "worker_generation": getattr(worker, "generation", None),
        "render_revision": session.rev,
        "warnings": resp.get("warnings", []),
        "timings": resp.get("timings", {}),
    }


def apply_overrides(session_id: str, patches: object, *,
                    preview_dpi: int | None = None) -> dict:
    """应用**全量** override 列表并重渲染。

    「全量列表」是 Tavotto 的 override 语义：worker 维护 applied/originals 两表，
    列表里没有的 key 自动恢复原值。**别发增量补丁**——那样撤销就没有基准了。

    脏条目不静默丢：`patchspec.canonicalize_with_diagnostics` 把它们连同原因
    一起交出来，随响应回给 Codex（`rejected`）。发给 worker 的是**过滤后仍保持
    原始顺序**的那份，与 Flask `/api/engine/render` 走的完全一样。
    """
    session = get_session(session_id)
    canonical, dropped = patchspec.canonicalize_with_diagnostics(patches)
    if dropped and any(d["index"] == -1 for d in dropped):
        raise BridgeError("patches 必须是数组", code="bad_patches", rejected=dropped)
    bad = {d["index"] for d in dropped}
    clean = [p for i, p in enumerate(patches or []) if i not in bad]

    out = _render(session, clean, preview_dpi=preview_dpi)
    out.update({"ok": True, "session_id": session.id, "stem": session.stem,
                "applied": len(clean), "rejected": dropped,
                "canonical_patch_count": len(canonical)})
    return out


def preview_png(session: Session, patches: list, width_px: int) -> str:
    """按 patches 出一张高清位图（base64）。**状态中立**：worker 出完就还原。"""
    tag = "v" + patchspec.patch_hash(patches).split(":")[-1][:12]
    worker = session.acquire()
    try:
        path = worker.preview_png(session.stem, patches, int(width_px), tag=tag)
    except engine_pool.WorkerError as exc:
        raise BridgeError(str(exc), code=exc.code or "preview_failed",
                          traceback=exc.traceback_text) from exc
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        # worker 说成了、文件却读不出来（被杀毒隔离、磁盘满、缓存目录被清）。
        # 这一步以前在 try 之外，裸 OSError 会绕过所有 BridgeError 处理——
        # `open_figure` 的降级也就接不住它，那条刚登记的会话又变回谁也够不着
        # 的幽灵。**这个函数对外只许抛 BridgeError**。
        raise BridgeError(f"位图出来了却读不出来 {path}: {exc}",
                          code="preview_unreadable") from exc
    return base64.b64encode(data).decode("ascii")


# -------------------------------- 预检 --------------------------------------
def resolve_profile(session: Session, profile_id: str | None,
                    journal: dict | None) -> dict:
    if profile_id is None and journal is None:
        return session.profile
    try:
        return engine_profiles.load(profile_id or session.profile["profile_id"], journal)
    except engine_profiles.ProfileError as exc:
        raise BridgeError(str(exc), code="unknown_profile") from exc


def export_raster_issues(profile: dict, formats: list[str] | None,
                         dpi: int | None) -> list[dict]:
    """「这次导出请求本身」带来的检查项：位图格式的 dpi 够不够规范。

    `engine/preflight.py` 的 `raster-dpi` 判的是**面板素材**的等效分辨率
    （画布那条入口有 px_w，MCP 这条没有）；这里判的是「现在就按这个 dpi 出
    一张图」。判据是同一条规范，所以**复用同一个 id 与同一张 severity 表**
    ——另起一个 code 的话，期刊覆盖里把 `raster-dpi` 调成 warn 对导出这条路
    就不生效了，同一份规范在两条入口上说不同的话。
    """
    if not formats or dpi is None:
        return []
    raster = {str(f).lower() for f in
              ((profile.get("preferred_formats") or {}).get("raster") or ())}
    hit = [f for f in formats if f in raster]
    if not hit:
        return []
    try:
        min_dpi, got = float(profile.get("min_raster_dpi") or 0), float(dpi)
    except (TypeError, ValueError):
        return []
    if not min_dpi or got >= min_dpi:
        return []
    return [{
        "id": "raster-dpi",
        "severity": engine_profiles.severity_of(profile, "raster-dpi"),
        "text": f"导出 {'/'.join(hit)} 用的 {got:g}dpi 低于规范的 {min_dpi:g}dpi",
        "object_ids": [], "gids": [],
        "detail": {"dpi": got, "min_dpi": min_dpi, "formats": hit},
    }]


def run_preflight(session_id: str, *, profile_id: str | None = None,
                  journal: dict | None = None,
                  export_formats: list[str] | None = None,
                  export_dpi: int | None = None) -> dict:
    session = get_session(session_id)
    if session.manifest is None:
        raise BridgeError("会话还没有 manifest（先 apply 一次 override 或重新 open）",
                          code="no_manifest")
    profile = resolve_profile(session, profile_id, journal)
    spec = engine_preflight.spec_from_manifest(
        session.manifest, panel_id=session.stem, kind="pdf", scale=1.0)
    issues = engine_preflight.run(spec, profile)
    issues += export_raster_issues(profile, export_formats, export_dpi)
    summary = engine_preflight.summarize(issues)
    # 匿名用量统计：**结果算完之后**记一次，且只记四个计数 + 一个布尔。
    # 检查项的文案、字体名、gid、对象 id、stem 一个都不发（白名单里没有这些
    # 属性）。没同意 / 硬开关关着时这一行什么都不做。画布那一侧的预检由前端
    # 的求值器记（两个求值器的分工见 CLAUDE.md），两条入口对应两种用户流程。
    counts = summary["counts"]
    engine_telemetry.capture("preflight_completed", {
        "errors": min(counts.get("error", 0), 1000),
        "warnings": min(counts.get("warn", 0), 1000),
        "not_verifiable": min(counts.get("not_verifiable", 0), 1000),
        "suggestions": min(counts.get("suggestion", 0), 1000),
        "passed": not counts.get("error", 0) and not counts.get("warn", 0),
    })
    return {
        "ok": True,
        "session_id": session.id,
        "stem": session.stem,
        "profile": engine_profiles.stamp(profile),
        "size_mm": session.manifest.get("size_mm"),
        "patch_hash": session.patch_hash(),
        "errors": summary["errors"],
        "warnings": summary["warnings"],
        "not_verifiable": summary["not_verifiable"],
        "suggestions": summary["suggestions"],
        "counts": summary["counts"],
        "blocking": summary["blocking"],
        # 「要用户点头」≠「有阻断项」：`not_verifiable` 按定义查不了
        # （位图内部的文字），规范要求人工确认并写进 proof。导出对话框一直是
        # 这么判的（`needsConfirm = errors || notVerifiable`），MCP 这条入口
        # 只看 error，于是同一份图在两条路上给出不同的放行结论。
        "needs_confirm": bool(summary["blocking"] or summary["not_verifiable"]),
        "report": format_preflight(session, profile, issues, summary),
    }


def format_preflight(session: Session, profile: dict, issues: list[dict],
                     summary: dict) -> str:
    """人类可读的那一份（Codex 会把它念给用户听）。"""
    head = (f"《{profile.get('label', profile['profile_id'])}》"
            f" v{profile['version']} · {session.stem}")
    size = session.manifest.get("size_mm") if session.manifest else None
    lines = [head]
    if size:
        lines.append(f"尺寸 {size[0]}×{size[1]} mm")
    if not issues:
        lines.append("✓ 全部通过")
        return "\n".join(lines)
    label = {"error": "✗ 阻断", "warn": "! 警告",
             "not_verifiable": "? 无法核验", "suggestion": "· 建议"}
    for level in ("error", "warn", "not_verifiable", "suggestion"):
        group = [i for i in issues if i["severity"] == level]
        if not group:
            continue
        lines.append("")
        lines.append(f"{label[level]}（{len(group)}）")
        for issue in group:
            where = "、".join(issue["gids"][:4]) or "、".join(issue["object_ids"][:4])
            lines.append(f"  - {issue['text']}" + (f"  [{where}]" if where else ""))
    if summary["blocking"]:
        lines.append("")
        lines.append("有阻断项：tavotto_export 会拒绝导出，"
                     "除非用户明确要求（explicit_confirm=true）。")
    return "\n".join(lines)


# -------------------------------- 导出 --------------------------------------
def export(session_id: str, *, formats: list[str], dpi: int = 600,
           stem: str | None = None, out_dir: str | None = None,
           profile_id: str | None = None, journal: dict | None = None,
           explicit_confirm: bool = False, proof: bool = True) -> dict:
    """先预检，再导出。**有阻断项且没有明确确认时一张图都不出。**"""
    session = get_session(session_id)
    fmts = [f.lower().strip() for f in (formats or []) if str(f).strip()]
    bad = [f for f in fmts if f not in EXPORT_FORMATS]
    if bad:
        raise BridgeError(f"不支持的导出格式: {', '.join(bad)}"
                          f"（支持 {', '.join(EXPORT_FORMATS)}）",
                          code="bad_format")
    # 默认格式取**这次调用的** profile，不是会话打开时那份：调用方带了
    # `profile_id` 或期刊覆盖时，预检与 proof 盖的都是新 profile 的章，
    # 而格式却还按旧的来——一份说「默认出 SVG」的覆盖会静默出成 PDF+PNG。
    call_profile = resolve_profile(session, profile_id, journal)
    if not fmts:
        fmts = list(call_profile["preferred_formats"]["export_default"])
    try:
        dpi = int(dpi)
    except (TypeError, ValueError):
        raise BridgeError(f"dpi 必须是整数: {dpi!r}", code="bad_dpi") from None
    if dpi <= 0:
        raise BridgeError(f"dpi 必须为正: {dpi}", code="bad_dpi")

    checks = run_preflight(session.id, profile_id=profile_id, journal=journal,
                           export_formats=fmts, export_dpi=dpi)
    if checks["needs_confirm"] and not explicit_confirm:
        raise BridgeError(
            f"预检有 {len(checks['errors'])} 类阻断性问题、"
            f"{len(checks['not_verifiable'])} 类无法核验项，未导出。"
            "修好它们，或者在用户明确要求后带 explicit_confirm=true 再调一次。",
            code="preflight_blocked", preflight=checks)

    if out_dir:
        target_dir = Path(check_scope(out_dir))
    else:
        # 项目设置里的 `export_dir` 可以是任意绝对路径（桌面版下完全合法），
        # 但 MCP 这条入口的边界是 `TAVOTTO_MCP_ROOTS`。默认值不过尺的话，
        # 一个对桌面版有效的项目就能让导出落到范围之外——而调用方**显式**
        # 传同一个路径反而会被拒。边界只有一条，默认值也得走它。
        target_dir = Path(check_scope(str(
            engine_config.project_export_dir(session.project))))
    target_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_stem(stem or session.stem)
    ts = time.strftime("%m%d_%H%M%S")

    files, warnings = [], []
    worker = session.acquire()
    for fmt in fmts:
        path = target_dir / f"{name}_{ts}.{fmt}"
        try:
            resp = worker.export(session.stem, session.patches,
                                 str(path), fmt, dpi)
        except engine_pool.WorkerError as exc:
            raise BridgeError(f"导出 {fmt} 失败: {exc}",
                              code=exc.code or "export_failed",
                              traceback=exc.traceback_text) from exc
        for w in (resp.get("warnings") or []):
            if w not in warnings:
                warnings.append(w)
        files.append({"format": fmt, "path": str(path),
                      "bytes": path.stat().st_size if path.exists() else 0,
                      # PDF/SVG 是 matplotlib 直接序列化的真矢量；PNG 才吃 dpi
                      "vector": fmt in ("pdf", "svg"),
                      "dpi": dpi if fmt == "png" else None})

    result = {"ok": True, "session_id": session.id, "stem": session.stem,
              "export_dir": str(target_dir), "files": files,
              "patch_hash": session.patch_hash(),
              "profile": checks["profile"], "warnings": warnings,
              "preflight": {k: checks[k] for k in
                            ("counts", "blocking", "needs_confirm", "errors",
                             "warnings", "not_verifiable", "suggestions")},
              # `forced` 只说「有 error 却还是出了」；无法核验项要的是确认、
              # 不是强制，两件事在留档里必须分得开
              "forced": bool(checks["blocking"] and explicit_confirm),
              "acknowledged": ([i["id"] for i in checks["errors"]] +
                               [i["id"] for i in checks["not_verifiable"]])
                              if (checks["needs_confirm"] and explicit_confirm) else []}
    if proof:
        result["proof_path"] = _write_proof(target_dir, f"{name}_{ts}", session,
                                            checks, files, dpi, fmts,
                                            forced=result["forced"],
                                            acknowledged=result["acknowledged"])
    return result


def _safe_stem(raw: str) -> str:
    import re
    return re.sub(r"[^\w\-一-鿿]+", "_", raw or "figure") or "figure"


def _write_proof(out_dir: Path, base: str, session: Session, checks: dict,
                 files: list[dict], dpi: int, formats: list[str],
                 *, forced: bool, acknowledged: list[str]) -> str:
    """proof report：profile 身份 + 全部检查结果 + 无法核验项 + 是否强制导出。

    与画布导出的 proof 同一个 kind/version（`web/src/lib/preflight.ts` 的
    `buildProofPayload`）——两条入口出的留档得能放在一起看。
    """
    from tavotto.engine.brand import PROOF_KIND       # 品牌常量唯一出处
    payload = {
        "kind": PROOF_KIND,
        "version": 2,
        "source": "codex-mcp",
        "stem": session.stem,
        "project": session.project,
        "script": session.script,
        "profile": checks["profile"],
        "page_mm": {"w": (session.manifest or {}).get("size_mm", [0, 0])[0],
                    "h": (session.manifest or {}).get("size_mm", [0, 0])[1],
                    "margin": 0},
        "dpi": dpi,
        "formats": formats,
        "patch_hash": session.patch_hash(),
        "patches": session.patches,
        "checks": [{"id": i["id"], "severity": i["severity"], "text": i["text"],
                    "count": len(i["object_ids"]), "object_ids": i["object_ids"],
                    "gids": i["gids"], "detail": i["detail"]}
                   for level in ("errors", "warnings", "not_verifiable", "suggestions")
                   for i in checks[level]],
        "check_counts": checks["counts"],
        "not_verifiable": [{"id": i["id"], "text": i["text"],
                            "object_ids": i["object_ids"]}
                           for i in checks["not_verifiable"]],
        "forced": forced,
        "acknowledged": acknowledged,
        "files": [f["path"] for f in files],
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = out_dir / f"{base}_proof.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return str(path)


# --------------------------- 等价性自检（可选） -------------------------------
def verify_replay(session_id: str) -> dict:
    """`hot_apply(patches) == fresh_worker_replay(patches)` 的现场自检。

    起一个**一次性 worker**（不进池、目录独立、用完即毁）从零全量重放同一组
    patches，把两份 manifest 逐元素比几何——判据直接复用 Tavotto 写回事务用的
    那把尺（`app._compare_manifests` 的容差口径：bbox/anchor 0.5% figure 分数、
    size_mm 0.01mm）。热会话是增量的，「现在的样子」未必等于「从零重放一次的
    样子」，而用户拿去投稿的是后者。
    """
    session = get_session(session_id)
    if session.manifest is None:
        raise BridgeError("会话还没有 manifest", code="no_manifest")
    worker = engine_pool.one_shot(session.script, session.project, session.entry)
    try:
        resp = worker.override(session.stem, session.patches, None, inline_svg=False)
        fresh = resp["manifest"]
    except engine_pool.WorkerError as exc:
        raise BridgeError(f"重放失败: {exc}", code=exc.code or "replay_failed",
                          traceback=exc.traceback_text) from exc
    finally:
        engine_pool.discard(worker)

    diffs, compared = compare_manifests(session.manifest, fresh)
    return {"ok": not diffs, "session_id": session.id, "stem": session.stem,
            "patch_hash": session.patch_hash(),
            "compared_elements": compared, "divergence": diffs,
            "fresh_manifest_hash": manifest_hash(fresh),
            "hot_manifest_hash": manifest_hash(session.manifest)}


_BBOX_TOL = 0.005      # figure 分数
_SIZE_TOL = 0.01       # mm


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compare_manifests(hot: dict, fresh: dict) -> tuple[list[dict], int]:
    """两份 manifest 的几何比对。与 `app._compare_manifests` 同口径。"""
    diffs: list[dict] = []
    hs, fs = hot.get("size_mm") or [], fresh.get("size_mm") or []
    for i, axis in enumerate("wh"):
        a, b = _f(hs[i] if i < len(hs) else None), _f(fs[i] if i < len(fs) else None)
        if a is None or b is None or abs(a - b) > _SIZE_TOL:
            diffs.append({"gid": "figure", "field": f"size_mm.{axis}",
                          "hot": a, "fresh": b})
    by_gid = {e.get("gid"): e for e in fresh.get("elements") or []}
    matched: set = set()
    compared = 0
    for el in hot.get("elements") or []:
        gid = el.get("gid")
        other = by_gid.get(gid)
        if other is None:
            # **结构分歧也是分歧**：重放里根本没有这个元素时静默跳过，等于
            # 让 `ok: true` 出现在两张画得完全不一样的图上——而「脚本不确定
            # / 重放有 bug」恰恰是这个自检唯一要抓的东西。
            diffs.append({"gid": gid, "field": "missing_in_fresh",
                          "hot": "present", "fresh": None})
            continue
        matched.add(gid)
        compared += 1
        for field_name in ("bbox", "anchor"):
            a, b = el.get(field_name), other.get(field_name)
            if not isinstance(a, list) or not isinstance(b, list) or len(a) != len(b):
                continue
            for k, (x, y) in enumerate(zip(a, b)):
                fx, fy = _f(x), _f(y)
                if fx is None or fy is None or abs(fx - fy) > _BBOX_TOL:
                    diffs.append({"gid": el.get("gid"), "field": f"{field_name}[{k}]",
                                  "hot": fx, "fresh": fy})
    # 只在重放里出现的元素同样要报：热会话少画了一个 artist 与多画了一个，
    # 对「用户拿去投稿的是重放那份」来说是同一类问题
    for gid in by_gid:
        if gid not in matched:
            diffs.append({"gid": gid, "field": "missing_in_hot",
                          "hot": None, "fresh": "present"})
    return diffs, compared


def manifest_hash(manifest: dict) -> str:
    """manifest 的内容指纹（canonical JSON 的 sha256）——与 app.py 同法。"""
    import hashlib
    text = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
