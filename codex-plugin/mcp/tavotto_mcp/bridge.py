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
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from tavotto.engine import (
    config as engine_config,
    handoff as engine_handoff,
    patchspec,
    pool as engine_pool,
    preflight as engine_preflight,
    previewbudget,
    profiles as engine_profiles,
    profilestore as engine_profilestore,
    project_refresh as engine_refresh,
    readiness as engine_readiness,
    registry as engine_registry,
    telemetry as engine_telemetry,
)

from .roots import (
    CODE_AMBIGUOUS_ROOT,
    CODE_NO_WORKSPACE_ROOT,
    CODE_PATH_OUT_OF_SCOPE,
    CODE_ROOTS_ERROR,
    CODE_ROOTS_NO_RESPONSE,
    ROOTS_ENV,
    WORKSPACE_ENVS,
    WORKSPACE_FAILURES,
    RootAuthority,
    canonical_path,
)

#: 工作区提示：装好的插件里 `.mcp.json` 的 `cwd` 指向**插件自己的目录**
#: （`./mcp/server.py` 要靠它解析），于是「不给就用进程 cwd」在真实安装下
#: 等于把用户工作区里的每一张图都判成 `path_out_of_scope`——默认流程根本
#: 跑不起来。所以 cwd 只在它**不是插件目录**时才算数（源码树里直接跑
#: `python codex-plugin/mcp/server.py` 的开发态就是这一格），否则退回宿主
#: 传过来的工作区变量。一个都拿不到时**报错说清楚要设什么**，绝不就近
#: 挑一个目录顶上。
#: 插件包自己所在的目录（`codex-plugin/`）——cwd 落在它里面就说明这是
#: Codex 用来定位 `./mcp/server.py` 的那个 cwd，不是用户的工作区。
_PLUGIN_DIR = canonical_path(os.path.join(os.path.dirname(__file__), "..", ".."))
_ROOT_AUTHORITY = RootAuthority(_PLUGIN_DIR)
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
    return list(_ROOT_AUTHORITY.snapshot().roots)


def root_diagnostics() -> dict:
    return _ROOT_AUTHORITY.diagnostics()


def observe_mcp_client(protocol_version: str | None, capabilities, client_info) -> None:
    _ROOT_AUTHORITY.observe_client(protocol_version, capabilities, client_info)


def protocol_roots_needed() -> bool:
    return _ROOT_AUTHORITY.protocol_request_needed()


def user_binding_candidate(target) -> str | None:
    return _ROOT_AUTHORITY.user_binding_candidate(target)


def accept_user_binding(candidate: str) -> bool:
    return _ROOT_AUTHORITY.accept_user_binding(candidate)


def fail_user_binding(message: str, *, state: str = "error") -> None:
    _ROOT_AUTHORITY.fail_user_binding(message, state=state)


def workspace_failure():
    return _ROOT_AUTHORITY.failure()


def accept_protocol_roots(result) -> None:
    _ROOT_AUTHORITY.accept_protocol_result(result)


def fail_protocol_roots(message: str, *, state: str = "error") -> None:
    _ROOT_AUTHORITY.fail_protocol(message, state=state)


def mark_protocol_roots_stale() -> None:
    _ROOT_AUTHORITY.mark_protocol_stale()


def reset_root_authority() -> None:
    _ROOT_AUTHORITY.reset()


def _no_roots_error() -> "BridgeError":
    """一个可用的根都没有时说人话——**并且说清是哪一档**。

    静默放行等于没有边界，静默拒绝等于「装了插件但什么都打不开」且毫无线索。
    但把失败并成一档同样不行（issue #173）：宿主声明了 elicitation 却没弹框，
    与用户看着框按了拒绝，处置正好相反。分档与措辞的唯一出处是
    `roots.WORKSPACE_FAILURES`，这里只负责把当时的细节接上去。
    """
    diagnostics = root_diagnostics()
    confirmation = diagnostics.get("workspace_confirmation") or {}
    failure = workspace_failure()
    if failure.code in {CODE_ROOTS_NO_RESPONSE, CODE_ROOTS_ERROR}:
        detail = "；".join(diagnostics.get("warnings") or ())
    elif failure.code == CODE_NO_WORKSPACE_ROOT:
        detail = (
            f"{ROOTS_ENV} 没设，宿主也没给工作区目录"
            f"（找过 {', '.join(WORKSPACE_ENVS)}），进程 cwd 也不是可用工作区"
            "（可能是插件目录，或已在插件更新时被替换）"
        )
    else:
        detail = confirmation.get("error") or ""
    message = failure.summary
    if detail:
        message += f"（{detail}）"
    return BridgeError(
        f"{message} 下一步：{failure.next_step}",
        code=failure.code,
        roots=[],
        disposition=failure.disposition,
        recovery=failure.next_step,
        workspace_confirmation=confirmation,
    )


def _within(path: str, root: str) -> bool:
    try:
        common = os.path.commonpath([path, root])
        return os.path.normcase(common) == os.path.normcase(root)
    except ValueError:  # Windows 上跨盘符 commonpath 直接抛
        return False


def check_scope(path: str) -> str:
    """把用户给的路径规范化，并确认它落在允许的根之内。

    **越界一律拒绝，绝不「就近找一个能用的」**：Codex 传来的路径可能来自模型
    的推断，静默换一个目录打开等于在用户没看见的地方改文件。
    """
    roots = allowed_roots()
    if not roots:
        raise _no_roots_error()
    # Resolve the untrusted target only after a usable boundary exists.
    # Windows' ``ntpath.realpath`` may consult cwd even for an absolute path;
    # doing this first would turn the same deleted-cwd case back into ENOENT.
    target = os.path.expanduser(str(path))
    if not os.path.isabs(target):
        if len(roots) != 1:
            failure = WORKSPACE_FAILURES[CODE_AMBIGUOUS_ROOT]
            raise BridgeError(
                f"{failure.summary} 下一步：{failure.next_step}",
                code=failure.code,
                disposition=failure.disposition,
                recovery=failure.next_step,
                roots=roots,
                path=target,
            )
        target = os.path.join(roots[0], target)
    real = canonical_path(target)
    if any(_within(real, r) for r in roots):
        return real
    failure = WORKSPACE_FAILURES[CODE_PATH_OUT_OF_SCOPE]
    raise BridgeError(
        f"{failure.summary}不在范围内的是 {real}；"
        f"当前允许的根: {os.pathsep.join(roots)}。下一步：{failure.next_step}",
        code=failure.code,
        disposition=failure.disposition,
        recovery=failure.next_step,
        roots=roots,
        path=real,
    )


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
    #: 这一版的预览表示法元数据（ADR 0022）。`mode == "raster"` 时 `svg` 是
    #: None——那是一次**成功**的渲染，只是引擎按硬闸决定不把 SVG 读出来。
    preview: dict | None = None
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
            raise BridgeError(
                str(exc), code=exc.code or "worker_error", traceback=exc.traceback_text
            ) from exc


_SESSIONS: dict[str, Session] = {}


def sessions() -> dict[str, Session]:
    return _SESSIONS


def get_session(session_id: str) -> Session:
    s = _SESSIONS.get(session_id)
    if s is None:
        known = ", ".join(sorted(_SESSIONS)) or "（没有打开的会话）"
        raise BridgeError(
            f"没有这个会话: {session_id}。先调用 tavotto_open_figure。已打开: {known}",
            code="unknown_session",
        )
    roots = allowed_roots()
    current_project: str | None = None
    resolution_error: str | None = None
    if roots:
        try:
            # ``s.project`` was canonical when the session opened, but the path can
            # later be replaced by a symlink/junction. Re-resolve it before every
            # operation so the stored lexical path cannot outlive its authority.
            current_project = canonical_path(s.project)
        except (OSError, ValueError) as exc:
            resolution_error = str(exc)
    if (
        not roots
        or current_project is None
        or not os.path.isdir(current_project)
        or not any(_within(current_project, root) for root in roots)
    ):
        _SESSIONS.pop(session_id, None)
        raise BridgeError(
            f"会话 {session_id} 的项目已不在当前工作区根内，请重新打开。",
            code="workspace_root_changed",
            roots=roots,
            project=s.project,
            resolved_project=current_project,
            resolution_error=resolution_error,
        )
    s.last_used = time.time()
    return s


def close_session(session_id: str) -> dict:
    s = _SESSIONS.pop(session_id, None)
    if s is None:
        return {
            "ok": True,
            "closed": False,
            "note": f"会话 {session_id} 已经不在了（重复关闭不算错）",
        }
    # worker 归 pool 管（同一个脚本可能还有别的用户）：这里只丢引用与会话账本。
    # 用户的项目数据一个字节都不动。
    return {
        "ok": True,
        "closed": True,
        "session_id": session_id,
        "project": s.project,
        "stem": s.stem,
    }


def _evict_if_needed() -> None:
    while len(_SESSIONS) > MAX_SESSIONS:
        oldest = min(_SESSIONS.values(), key=lambda s: s.last_used)
        _SESSIONS.pop(oldest.id, None)


def shutdown_all() -> None:
    """进程退出前收摊：会话账本清空 + 关掉 worker 子进程（不留孤儿）。"""
    _SESSIONS.clear()
    try:
        engine_pool.shutdown_all(wait=True)
    except Exception:  # noqa: BLE001 — 收尾不许连累退出
        pass


# ------------------------------ 打开一张图 -----------------------------------
def _pick_stem(project: str, stem: str | None, registry) -> str:
    if stem:
        if registry.for_stem(stem) is None:
            raise BridgeError(
                f"注册表里没有 stem「{stem}」——这张图没有对应脚本，只能当素材排版。"
                "把产出它的 .py 放到产物同一个目录，并让产物名是脚本里的字面量。",
                code="stem_not_parameterizable",
                known=sorted(registry.entries()) and _all_stems(registry),
            )
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
            f"{project} 里没有任何已登记且产物在磁盘上的图。先把脚本跑一遍。", code="no_figure"
        )
    if len(on_disk) > 1:
        raise BridgeError(
            f"这个项目里有多张图，得点名要哪一张: {', '.join(sorted(on_disk))}",
            code="stem_required",
            stems=sorted(on_disk),
        )
    return on_disk[0]


def _all_stems(registry) -> list[str]:
    return sorted({s for script in registry.all_scripts() for s in registry.stems_of(script)})


def open_figure(
    target: str,
    *,
    stem: str | None = None,
    profile_id: str | None = None,
    journal: dict | None = None,
    include_png: bool = False,
) -> dict:
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
            code="no_registry",
        ) from exc
    except RuntimeError as exc:  # 注册表损坏 / 重复 stem
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
    # 用户自建的规范也要认得（`profilestore.resolve_spec` 是「任意 id → 规范」
    # 的唯一入口；`engine_profiles.load` 只读内置那份 canonical JSON）。
    try:
        profile = engine_profilestore.resolve_spec(profile_id, journal)
    except (engine_profiles.ProfileError, engine_profilestore.ProfileStoreError) as exc:
        raise BridgeError(str(exc), code="unknown_profile") from exc

    session = Session(
        id="s-" + uuid.uuid4().hex[:12],
        project=project,
        stem=chosen,
        script=info["script"],
        entry=info["entry"],
        profile=profile,
    )
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
        # raster 档的渲染已经在同一次响应里带了一张（ADR 0022）——**别再画一次**。
        # 那张更小（RASTER_PREVIEW_WIDTH_PX），但它是画布此刻要显示的东西，
        # 而 `include_png` 要的只是「顺带给我一张位图」。为了 400px 的差别
        # 让 #181 那种图多画一遍不划算。
        if "preview_png_base64" not in out:
            try:
                out["preview_png_base64"] = preview_png(session, [], 1600)
            except BridgeError as exc:
                out["preview_png_error"] = exc.code or "preview_failed"
    return out


# ------------------------------- 刷新（ADR 0041） ----------------------------
#: 刷新的来由固定是 codex：它进日志、进事件、进遥测维度，模型传什么都不透传。
REFRESH_REASON = "codex"
#: 结果里 `delivered` 的两个取值。**不是成败**——两条路都成功刷新了磁盘上的
#: 事实；区别只在运行中的 Tavotto 界面这一次有没有同步到。
DELIVERED_APP = "app"
DELIVERED_LOCAL = "local"


def project_id(project: str) -> str:
    """项目短 id，与 `app._project_id()` 同一把尺（`normalize_path_identity`）。
    刷新结果里**用它代替绝对路径**：Codex 要的是「这个项目变了什么」，不是
    用户的目录结构。"""
    key = engine_config.normalize_path_identity(project)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


class _RefreshCtx:
    """`refresh_project_index()` / `readiness.compute()` 要的三样东西。

    这是**本进程**那份项目状态，不是运行中的 Tavotto 手里那份：它只在
    Tavotto 没开着时才被用到（`DELIVERED_LOCAL`），刷新状态（素材基线、
    注册表修订号、就绪度缓存）挂在它身上，同一个项目跨调用复用，于是第二次
    起素材 diff 是真的跨轮比（第一次如实报 `baseline: true`）。
    """

    def __init__(self, project: str, registry) -> None:
        self.path = Path(project)
        self.id = project_id(project)
        self.registry = registry


_REFRESH_CTX: dict[str, _RefreshCtx] = {}


def _local_refresh_ctx(project: str) -> _RefreshCtx:
    ctx = _REFRESH_CTX.get(project)
    if ctx is not None:
        return ctx
    try:
        registry = engine_registry.open_registry(project)
    except FileNotFoundError as exc:
        raise BridgeError(
            "这个目录还不是一个 Tavotto 图库（没有 tavotto_registry.json）。"
            "先用 tavotto_open_figure 打开其中一张图（它会登记），再刷新。",
            code="no_registry",
        ) from exc
    except RuntimeError as exc:
        raise BridgeError(f"注册表无法加载: {exc}", code="bad_registry") from exc
    ctx = _REFRESH_CTX[project] = _RefreshCtx(project, registry)
    return ctx


def resolve_refresh_project(
    *, session_id: str | None = None, project_path: str | None = None
) -> str:
    """刷新哪个项目。**项目上下文来自授权，不来自模型的自由文本。**

    三条路，按可信度排：
    1. `session_id` → 那个会话的项目（`get_session` 会重新做范围校验）；
    2. `project_path` → 与 `tavotto_open_figure` 同一套：`check_scope` →
       `handoff.resolve_target` 沿目录向上找图库 → 再 `check_scope` 一次；
    3. 都不传 → 当前**恰好一个**项目有会话时用它；零个报 `no_project`，
       多个报 `ambiguous_project`（列的是会话 id，不是路径）。
    """
    if session_id:
        return get_session(str(session_id)).project
    if project_path:
        real = check_scope(str(project_path))
        if not os.path.exists(real):
            raise BridgeError(f"路径不存在: {real}", code="not_found")
        try:
            found = engine_handoff.resolve_target(real)
        except engine_handoff.HandoffError as exc:
            raise BridgeError(str(exc), code="handoff_failed") from exc
        return check_scope(found.project)
    projects = sorted({s.project for s in _SESSIONS.values()})
    if len(projects) == 1:
        return check_scope(projects[0])
    if not projects:
        raise BridgeError(
            "没有打开的会话，也没有传 project_path：先 tavotto_open_figure 打开一张图，"
            "或传一个已授权工作区内的项目目录。",
            code="no_project",
        )
    raise BridgeError(
        "有多个项目开着会话，说不清要刷新哪一个：传 session_id 或 project_path。",
        code="ambiguous_project",
        sessions={sid: project_id(s.project) for sid, s in _SESSIONS.items()},
    )


def _app_reachable(port: int, http_status) -> bool:
    st, _ = http_status(f"http://127.0.0.1:{port}/api/version", timeout=0.6)
    return st is not None


def _remote_refresh(port: int, project: str, http_status) -> tuple[dict, dict | None]:
    """把刷新**委托给运行中的 Tavotto**：它持有项目的 ctx、watcher 与 SSE，
    刷新在那儿做完，前端当场收到 `registry.changed` / `assets.changed`。

    `default: false`——只让它端着这个项目，不改别的标签页的默认落点
    （与 `handoff._remote_probe` 同一条纪律）。刷新失败原样带回它的 code，
    **不退回本地再试一遍**：同一份磁盘事实，它刷不成本地也刷不成。
    """
    base = f"http://127.0.0.1:{port}"
    st, opened = http_status(
        f"{base}/api/projects/open", {"path": project, "default": False}, timeout=10.0
    )
    pj = (opened or {}).get("id")
    if st != 200 or not pj:
        raise BridgeError(
            f"运行中的 Tavotto 打不开这个项目: {(opened or {}).get('error') or f'HTTP {st}'}",
            code="remote_open_failed",
        )
    q = f"?pj={quote(pj, safe='')}"
    st, result = http_status(
        f"{base}/api/project/refresh{q}", {"reason": REFRESH_REASON}, timeout=60.0
    )
    if st != 200 or not isinstance(result, dict):
        raise BridgeError(
            f"运行中的 Tavotto 刷新失败: {(result or {}).get('error') or f'HTTP {st}'}",
            code=str((result or {}).get("code") or "refresh_failed"),
            params=(result or {}).get("params") or {},
        )
    st, readiness = http_status(f"{base}/api/project/readiness{q}", timeout=30.0)
    return result, readiness if st == 200 and isinstance(readiness, dict) else None


def _local_refresh(project: str) -> tuple[dict, dict]:
    """Tavotto 没开着：在本进程调**同一份**刷新服务（`engine/project_refresh`），
    不复制扫描算法、不 probe、不跑脚本。没有 SSE 可发（没人在听），下次
    Tavotto 打开这个项目时读到的就是刷新后的注册表。"""
    ctx = _local_refresh_ctx(project)
    try:
        result = engine_refresh.refresh_project_index(ctx, reason=REFRESH_REASON, publish=False)
    except engine_refresh.RefreshError as exc:
        raise BridgeError(exc.message, code=exc.code, params=exc.params) from exc
    return result, engine_readiness.compute(ctx)


def _compact_readiness(report: dict | None) -> dict | None:
    """就绪度报告 → Codex 要看的那几列。id / stem / 脚本都是项目相对的。"""
    if not isinstance(report, dict):
        return None
    panels = [
        {
            "id": p.get("id"),
            "stem": p.get("stem"),
            "status": p.get("status"),
            "reason_code": p.get("reason_code"),
            "script": p.get("script"),
            "candidates": list(p.get("candidates") or []),
        }
        for p in report.get("panels") or []
    ]
    return {
        "summary": dict(report.get("summary") or {}),
        "panels": panels,
        "conflicts": report.get("conflicts"),
    }


def refresh_project(
    *,
    session_id: str | None = None,
    project_path: str | None = None,
    port: int | None = None,
    http_status=None,
) -> dict:
    """`tavotto_refresh_project` 的实现：解析授权的项目 → 优先委托运行中的
    Tavotto → 不可达再本地 → 结构化 diff + 就绪度摘要。

    **不复制 discover、不 probe、不运行用户脚本**：两条路都落在
    `engine/project_refresh.refresh_project_index()`（ADR 0025）。结果里没有
    绝对路径：项目用短 id，脚本 / 图都是项目相对名。
    """
    project = resolve_refresh_project(session_id=session_id, project_path=project_path)
    port = engine_handoff.DEFAULT_PORT if port is None else int(port)
    http_status = engine_handoff.http_json_status if http_status is None else http_status

    if _app_reachable(port, http_status):
        result, readiness = _remote_refresh(port, project, http_status)
        delivered = DELIVERED_APP
    else:
        result, readiness = _local_refresh(project)
        delivered = DELIVERED_LOCAL

    registry = dict(result.get("registry") or {})
    assets = dict(result.get("assets") or {})
    return {
        "ok": True,
        "project_id": project_id(project),
        "reason": REFRESH_REASON,
        "delivered": delivered,
        "registry": {
            k: registry.get(k)
            for k in (
                "added_scripts",
                "removed_scripts",
                "changed_scripts",
                "script_changes",
                "added_stems",
                "removed_stems",
                "moved_stems",
                "conflicts",
                "conflicts_changed",
            )
        },
        "assets": {k: assets.get(k) for k in ("added", "removed", "changed", "baseline")},
        "readiness": _compact_readiness(readiness),
        "sessions": sorted(sid for sid, s in _SESSIONS.items() if s.project == project),
    }


# ------------------------------- 渲染 / 应用 ---------------------------------
def _render(session: Session, patches: list, *, preview_dpi: int | None) -> dict:
    worker = session.acquire()
    try:
        resp = worker.override(session.stem, patches, preview_dpi, inline_svg=True)
    except engine_pool.WorkerError as exc:
        raise BridgeError(
            str(exc),
            code=exc.code or "render_failed",
            traceback=exc.traceback_text,
            module=getattr(exc, "module", ""),
        ) from exc
    session.patches = list(patches)
    session.manifest = resp["manifest"]
    session.svg = resp.get("svg")
    session.preview = resp.get("preview")
    session.rev = getattr(worker, "rev", session.rev + 1)
    session.last_used = time.time()
    out = {
        "manifest": session.manifest,
        "svg": session.svg,
        "patch_hash": session.patch_hash(),
        "worker_generation": getattr(worker, "generation", None),
        "render_revision": session.rev,
        "warnings": resp.get("warnings", []),
        "timings": resp.get("timings", {}),
    }
    if session.preview is not None:
        out["preview"] = session.preview
    # raster 档下 `svg` 是 None，而**内嵌画布里没有可连的 HTTP 服务**——
    # 不在同一次响应里把位图带上，Codex 那边的画布就整个空掉（ADR 0022
    # 「不变量 5」：降级是换一种画法，不是不给画）。
    #
    # 与位图**同一次响应**也不只是省一跳：另开一个工具去取，取回来的可能已经
    # 是另一组 patches 的像素——SVG 与 manifest 的原子配对纪律（web/AGENTS.md
    # 「渲染态」①）在这里同样成立。
    #
    # 尺寸受控（`RASTER_PREVIEW_WIDTH_PX`）：绝不把 giant SVG 转成 base64
    # 塞回来，那只是把同一个 payload 换个编码再放大三分之一。
    if (session.preview or {}).get("mode") == previewbudget.MODE_RASTER:
        try:
            out["preview_png_base64"] = preview_png(
                session, list(patches), previewbudget.RASTER_PREVIEW_WIDTH_PX
            )
        except BridgeError as exc:
            # 位图失败不该把这次**成功的渲染**变成一条错误：manifest 是对的、
            # 编辑语义是完整的，缺的只是画面。如实回一个 code，别静默。
            out["preview_png_error"] = exc.code or "preview_failed"
    return out


def apply_overrides(session_id: str, patches: object, *, preview_dpi: int | None = None) -> dict:
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
    out.update(
        {
            "ok": True,
            "session_id": session.id,
            "stem": session.stem,
            "applied": len(clean),
            "rejected": dropped,
            "canonical_patch_count": len(canonical),
        }
    )
    return out


def preview_png(session: Session, patches: list, width_px: int) -> str:
    """按 patches 出一张高清位图（base64）。**状态中立**：worker 出完就还原。"""
    tag = "v" + patchspec.patch_hash(patches).split(":")[-1][:12]
    worker = session.acquire()
    try:
        path = worker.preview_png(session.stem, patches, int(width_px), tag=tag)
    except engine_pool.WorkerError as exc:
        raise BridgeError(
            str(exc), code=exc.code or "preview_failed", traceback=exc.traceback_text
        ) from exc
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        # worker 说成了、文件却读不出来（被杀毒隔离、磁盘满、缓存目录被清）。
        # 这一步以前在 try 之外，裸 OSError 会绕过所有 BridgeError 处理——
        # `open_figure` 的降级也就接不住它，那条刚登记的会话又变回谁也够不着
        # 的幽灵。**这个函数对外只许抛 BridgeError**。
        raise BridgeError(f"位图出来了却读不出来 {path}: {exc}", code="preview_unreadable") from exc
    return base64.b64encode(data).decode("ascii")


# -------------------------------- 预检 --------------------------------------
def resolve_profile(session: Session, profile_id: str | None, journal: dict | None) -> dict:
    if profile_id is None and journal is None:
        return session.profile
    try:
        return engine_profilestore.resolve_spec(
            profile_id or session.profile["profile_id"], journal
        )
    except (engine_profiles.ProfileError, engine_profilestore.ProfileStoreError) as exc:
        raise BridgeError(str(exc), code="unknown_profile") from exc


def export_raster_issues(profile: dict, formats: list[str] | None, dpi: int | None) -> list[dict]:
    """「这次导出请求本身」带来的检查项：位图格式的 dpi 够不够规范。

    `engine/preflight.py` 的 `raster-dpi` 判的是**面板素材**的等效分辨率
    （画布那条入口有 px_w，MCP 这条没有）；这里判的是「现在就按这个 dpi 出
    一张图」。判据是同一条规范，所以**复用同一个 id 与同一张 severity 表**
    ——另起一个 code 的话，期刊覆盖里把 `raster-dpi` 调成 warn 对导出这条路
    就不生效了，同一份规范在两条入口上说不同的话。
    """
    if not formats or dpi is None:
        return []
    raster = {
        str(f).lower() for f in ((profile.get("preferred_formats") or {}).get("raster") or ())
    }
    hit = [f for f in formats if f in raster]
    if not hit:
        return []
    try:
        min_dpi, got = float(profile.get("min_raster_dpi") or 0), float(dpi)
    except (TypeError, ValueError):
        return []
    if not min_dpi or got >= min_dpi:
        return []
    return [
        {
            "id": "raster-dpi",
            "severity": engine_profiles.severity_of(profile, "raster-dpi"),
            "text": f"导出 {'/'.join(hit)} 用的 {got:g}dpi 低于规范的 {min_dpi:g}dpi",
            # issue #30：widget 按自己的 locale 渲染，key 登记在前端
            # errors:preflight.exportRasterDpi（i18n:check 看护双语齐全）
            "message": {
                "key": "exportRasterDpi",
                "params": {"formats": "/".join(hit), "dpi": f"{got:g}", "min": f"{min_dpi:g}"},
            },
            "object_ids": [],
            "gids": [],
            "detail": {"dpi": got, "min_dpi": min_dpi, "formats": hit},
        }
    ]


def run_preflight(
    session_id: str,
    *,
    profile_id: str | None = None,
    journal: dict | None = None,
    export_formats: list[str] | None = None,
    export_dpi: int | None = None,
) -> dict:
    session = get_session(session_id)
    if session.manifest is None:
        raise BridgeError(
            "会话还没有 manifest（先 apply 一次 override 或重新 open）", code="no_manifest"
        )
    profile = resolve_profile(session, profile_id, journal)
    spec = engine_preflight.spec_from_manifest(
        session.manifest, panel_id=session.stem, kind="pdf", scale=1.0
    )
    issues = engine_preflight.run(spec, profile)
    issues += export_raster_issues(profile, export_formats, export_dpi)
    summary = engine_preflight.summarize(issues)
    # 匿名用量统计：**结果算完之后**记一次，且只记四个计数 + 一个布尔。
    # 检查项的文案、字体名、gid、对象 id、stem 一个都不发（白名单里没有这些
    # 属性）。没同意 / 硬开关关着时这一行什么都不做。画布那一侧的预检由前端
    # 的求值器记（两个求值器的分工见 CLAUDE.md），两条入口对应两种用户流程。
    counts = summary["counts"]
    engine_telemetry.capture(
        "preflight_completed",
        {
            "errors": min(counts.get("error", 0), 1000),
            "warnings": min(counts.get("warn", 0), 1000),
            "not_verifiable": min(counts.get("not_verifiable", 0), 1000),
            "suggestions": min(counts.get("suggestion", 0), 1000),
            "passed": not counts.get("error", 0) and not counts.get("warn", 0),
        },
    )
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


def format_preflight(session: Session, profile: dict, issues: list[dict], summary: dict) -> str:
    """人类可读的那一份（Codex 会把它念给用户听）。"""
    head = (
        f"《{profile.get('label', profile['profile_id'])}》 v{profile['version']} · {session.stem}"
    )
    size = session.manifest.get("size_mm") if session.manifest else None
    lines = [head]
    if size:
        lines.append(f"尺寸 {size[0]}×{size[1]} mm")
    if not issues:
        lines.append("✓ 全部通过")
        return "\n".join(lines)
    label = {
        "error": "✗ 阻断",
        "warn": "! 警告",
        "not_verifiable": "? 无法核验",
        "suggestion": "· 建议",
    }
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
        lines.append(
            "有阻断项：tavotto_export 会拒绝导出，除非用户明确要求（explicit_confirm=true）。"
        )
    return "\n".join(lines)


# -------------------------------- 导出 --------------------------------------
def export(
    session_id: str,
    *,
    formats: list[str],
    dpi: int = 600,
    stem: str | None = None,
    out_dir: str | None = None,
    profile_id: str | None = None,
    journal: dict | None = None,
    explicit_confirm: bool = False,
    proof: bool = True,
) -> dict:
    """先预检，再导出。**有阻断项且没有明确确认时一张图都不出。**"""
    session = get_session(session_id)
    fmts = [f.lower().strip() for f in (formats or []) if str(f).strip()]
    bad = [f for f in fmts if f not in EXPORT_FORMATS]
    if bad:
        raise BridgeError(
            f"不支持的导出格式: {', '.join(bad)}（支持 {', '.join(EXPORT_FORMATS)}）",
            code="bad_format",
        )
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

    checks = run_preflight(
        session.id, profile_id=profile_id, journal=journal, export_formats=fmts, export_dpi=dpi
    )
    if checks["needs_confirm"] and not explicit_confirm:
        raise BridgeError(
            f"预检有 {len(checks['errors'])} 类阻断性问题、"
            f"{len(checks['not_verifiable'])} 类无法核验项，未导出。"
            "修好它们，或者在用户明确要求后带 explicit_confirm=true 再调一次。",
            code="preflight_blocked",
            preflight=checks,
        )

    if out_dir:
        target_dir = Path(check_scope(out_dir))
    else:
        # 项目设置里的 `export_dir` 可以是任意绝对路径（桌面版下完全合法），
        # 但 MCP 这条入口的边界是 `TAVOTTO_MCP_ROOTS`。默认值不过尺的话，
        # 一个对桌面版有效的项目就能让导出落到范围之外——而调用方**显式**
        # 传同一个路径反而会被拒。边界只有一条，默认值也得走它。
        target_dir = Path(check_scope(str(engine_config.project_export_dir(session.project))))
    target_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_stem(stem or session.stem)
    ts = time.strftime("%m%d_%H%M%S")

    files, warnings = [], []
    worker = session.acquire()
    for fmt in fmts:
        path = target_dir / f"{name}_{ts}.{fmt}"
        try:
            resp = worker.export(session.stem, session.patches, str(path), fmt, dpi)
        except engine_pool.WorkerError as exc:
            raise BridgeError(
                f"导出 {fmt} 失败: {exc}",
                code=exc.code or "export_failed",
                traceback=exc.traceback_text,
            ) from exc
        for w in resp.get("warnings") or []:
            if w not in warnings:
                warnings.append(w)
        files.append(
            {
                "format": fmt,
                "path": str(path),
                "bytes": path.stat().st_size if path.exists() else 0,
                # PDF/SVG 是 matplotlib 直接序列化的真矢量；PNG 才吃 dpi
                "vector": fmt in ("pdf", "svg"),
                "dpi": dpi if fmt == "png" else None,
            }
        )

    result = {
        "ok": True,
        "session_id": session.id,
        "stem": session.stem,
        "export_dir": str(target_dir),
        "files": files,
        "patch_hash": session.patch_hash(),
        "profile": checks["profile"],
        "warnings": warnings,
        "preflight": {
            k: checks[k]
            for k in (
                "counts",
                "blocking",
                "needs_confirm",
                "errors",
                "warnings",
                "not_verifiable",
                "suggestions",
            )
        },
        # `forced` 只说「有 error 却还是出了」；无法核验项要的是确认、
        # 不是强制，两件事在留档里必须分得开
        "forced": bool(checks["blocking"] and explicit_confirm),
        "acknowledged": (
            [i["id"] for i in checks["errors"]] + [i["id"] for i in checks["not_verifiable"]]
        )
        if (checks["needs_confirm"] and explicit_confirm)
        else [],
    }
    if proof:
        result["proof_path"] = _write_proof(
            target_dir,
            f"{name}_{ts}",
            session,
            checks,
            files,
            dpi,
            fmts,
            forced=result["forced"],
            acknowledged=result["acknowledged"],
        )
    return result


def _safe_stem(raw: str) -> str:
    import re

    return re.sub(r"[^\w\-一-鿿]+", "_", raw or "figure") or "figure"


def _write_proof(
    out_dir: Path,
    base: str,
    session: Session,
    checks: dict,
    files: list[dict],
    dpi: int,
    formats: list[str],
    *,
    forced: bool,
    acknowledged: list[str],
) -> str:
    """proof report：profile 身份 + 全部检查结果 + 无法核验项 + 是否强制导出。

    与画布导出的 proof 同一个 kind/version（`web/src/lib/preflight.ts` 的
    `buildProofPayload`）——两条入口出的留档得能放在一起看。
    """
    from tavotto.engine.brand import PROOF_KIND  # 品牌常量唯一出处

    payload = {
        "kind": PROOF_KIND,
        "version": 2,
        "source": "codex-mcp",
        "stem": session.stem,
        "project": session.project,
        "script": session.script,
        "profile": checks["profile"],
        "page_mm": {
            "w": (session.manifest or {}).get("size_mm", [0, 0])[0],
            "h": (session.manifest or {}).get("size_mm", [0, 0])[1],
            "margin": 0,
        },
        "dpi": dpi,
        "formats": formats,
        "patch_hash": session.patch_hash(),
        "patches": session.patches,
        "checks": [
            {
                "id": i["id"],
                "severity": i["severity"],
                "text": i["text"],
                "count": len(i["object_ids"]),
                "object_ids": i["object_ids"],
                "gids": i["gids"],
                "detail": i["detail"],
            }
            for level in ("errors", "warnings", "not_verifiable", "suggestions")
            for i in checks[level]
        ],
        "check_counts": checks["counts"],
        "not_verifiable": [
            {"id": i["id"], "text": i["text"], "object_ids": i["object_ids"]}
            for i in checks["not_verifiable"]
        ],
        "forced": forced,
        "acknowledged": acknowledged,
        "files": [f["path"] for f in files],
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = out_dir / f"{base}_proof.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
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
        raise BridgeError(
            f"重放失败: {exc}", code=exc.code or "replay_failed", traceback=exc.traceback_text
        ) from exc
    finally:
        engine_pool.discard(worker)

    diffs, compared = compare_manifests(session.manifest, fresh)
    return {
        "ok": not diffs,
        "session_id": session.id,
        "stem": session.stem,
        "patch_hash": session.patch_hash(),
        "compared_elements": compared,
        "divergence": diffs,
        "fresh_manifest_hash": manifest_hash(fresh),
        "hot_manifest_hash": manifest_hash(session.manifest),
    }


_BBOX_TOL = 0.005  # figure 分数
_SIZE_TOL = 0.01  # mm


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
            diffs.append({"gid": "figure", "field": f"size_mm.{axis}", "hot": a, "fresh": b})
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
            diffs.append({"gid": gid, "field": "missing_in_fresh", "hot": "present", "fresh": None})
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
                    diffs.append(
                        {
                            "gid": el.get("gid"),
                            "field": f"{field_name}[{k}]",
                            "hot": fx,
                            "fresh": fy,
                        }
                    )
    # 只在重放里出现的元素同样要报：热会话少画了一个 artist 与多画了一个，
    # 对「用户拿去投稿的是重放那份」来说是同一类问题
    for gid in by_gid:
        if gid not in matched:
            diffs.append({"gid": gid, "field": "missing_in_hot", "hot": None, "fresh": "present"})
    return diffs, compared


def manifest_hash(manifest: dict) -> str:
    """manifest 的内容指纹（canonical JSON 的 sha256）——与 app.py 同法。"""
    import hashlib

    text = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
