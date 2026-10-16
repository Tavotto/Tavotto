"""交接三态的「渲染回执」通道（ADR 0131 第六节，纯标准库）。

`tavotto open` 的 `ok` 只说「解析、登记、唤起成功」，不说图画出来没有。要诚实地回答
「渲染了吗」，只能去看**桌面端本来就会做的那一次渲染**的结果（不为验证去执行用户脚本）。
这个模块是 CLI 与那个实例之间的一条只读小通道：

    CLI（`handoff.open_target`）
      1. 唤起之前写一份**期望**：`<data_dir>/session/handoff/<不透明 ID>.json`
         {schema, id, project, stem, created_at, expires_at, result: null}
      2. 唤起，然后有界轮询这份文件
    实例（sidecar / 浏览器模式服务端，`/api/engine/render` 收尾处）
      3. 这一次渲染的项目 + stem 与某份未过期、尚无结果的期望对上：把结果写进同一份文件
         （`record_ok` / `record_failed`，原子替换）
    CLI
      4. 读到结果（或到点没读到）→ `launch.render`；退出前删掉自己的期望

**为什么是文件而不是 HTTP 端点**：桌面 sidecar 的端口由壳建议、实绑后只写进壳的握手文件，
磁盘上没有任何本机凭据（ADR 0008「桌面模式的差异」），CLI 既找不到端口也没有凭据可带；
要查就得新造一个桌面凭据发布面，那是在放宽 ADR 0008 划的边界。这里的信任锚与
`engine/session_client.py` 的凭据文件、ADR 0021 §4 的 native descriptor **同一个论证**：
`<data_dir>/session/` 是 0700 目录、文件 0600，能写进去的进程本来就能读用户的任何文件，
网页（含 DNS rebinding 页面）碰不到。没有新端点，所以没有可被绕过的 guard。

**已知盲区（#961 现场，未验证）**：路径是 `config.data_dir()`。CLI 若跑在 MSIX 打包进程（Codex Store 版）的上下文里，
`%LOCALAPPDATA%` 的写入可能被虚拟化到 `Packages\\<pkg>\\LocalCache\\...`，而非打包的 sidecar 读真实路径，两边错位——结果是
永远 `unverified/timeout`（不误报，但没有信号）。仓库里没有现成的「真实路径」解析可复用；需要 Windows 真机确认后再决定出路。

约束（每条都有用例）：
  * ID 严格 `^[0-9a-f]{32}$`，文件名只由 ID 构成，永远在固定目录里解析；
  * 文件必须是本人的普通文件（lstat，不跟符号链接）、不超过 `MAX_BYTES`；
  * 期望有 `expires_at`（等待上限 + 宽限），过期的一律当没有；陈旧文件在写入与查询时顺手清；
  * 结果是一次性的：第一份结论赢，之后的渲染不改写它——写入走每份回执一把的写锁
    （`<id>.json.lock`，`O_CREAT|O_EXCL`，拿到后重读仍无结论才写，写完删）；拿不到锁 = 别人正在写第一份，跳过；
    崩溃残留的锁按 mtime 超过 `LOCK_STALE_S` 视为陈旧并清理；
  * 等待预算必须是有限非负数：`nan` / `inf` / 负数一律按 0（不等）处理，CLI 入口在 argparse 层就拒绝；
  * 回执里只有 stem、计数与稳定错误码，**不含脚本、路径之外的内容、traceback**。
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import stat
import time
from typing import NamedTuple

from . import config, session_client

SCHEMA = 1
DIR_NAME = "handoff"
ID_RE = re.compile(r"^[0-9a-f]{32}$")
CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
#: 单份回执的体积上限（只有几个标量；超了就是被人塞了东西）
MAX_BYTES = 4096
#: 期望在 CLI 等待上限之外多留的宽限（秒）：CLI 慢一点读到也算数，但不会无限悬着
GRACE_S = 30.0
#: CLI 崩了没来得及删的期望，超过这个年龄一律清掉
STALE_S = 3600.0
#: 写锁文件的后缀 / 超过这个年龄（秒）视为写者崩溃残留
LOCK_SUFFIX = ".lock"
LOCK_STALE_S = 5.0
#: 一份回执能被扫描的上限条数（目录被塞满时不让每次渲染都扫一整片）
MAX_SCAN = 64

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_UNVERIFIED = "unverified"
#: `render.reason` 的闭集（status == unverified 时给出，说清为什么没法下结论）
REASON_NOT_WAITED = "not_waited"
REASON_TIMEOUT = "timeout"
REASON_CHANNEL_UNAVAILABLE = "channel_unavailable"
#: 目录级 / 多 Figure 交接：没有「目标 stem」，不存在「那一次渲染」可看
REASON_NO_TARGET_STEM = "no_target_stem"
#: 登记结果说这张图不可参数化（无脚本）：界面不会为它发起引擎渲染，等也是白等
REASON_NOT_RENDERABLE = "not_renderable"
REASONS = (
    REASON_NOT_WAITED,
    REASON_TIMEOUT,
    REASON_CHANNEL_UNAVAILABLE,
    REASON_NO_TARGET_STEM,
    REASON_NOT_RENDERABLE,
)
#: 实例报「渲染失败」却没给合法 code（或给了不合规的串）时用的稳定码
CODE_RENDER_FAILED = "render_failed"


class Expectation(NamedTuple):
    id: str
    path: str
    project: str
    stem: str
    expires_at: float


def finite_budget(value: float) -> float:
    """等待预算归一：非有限（nan / inf）或负数 = 0（不等），绝不让轮询无界。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) and v > 0 else 0.0


def receipt_dir() -> str:
    return os.path.join(session_client.session_dir(), DIR_NAME)


def _identity(project: str) -> str:
    """项目身份串：与 `pool._norm_dir` / `app._project_id` 同一份大小写判断。"""
    return config.normalize_path_identity(os.path.realpath(project))


def _ensure_dir() -> str | None:
    """建（或核实）回执目录；任何一步不对返回 None——通道不可用不是错误，只是没法确认。"""
    d = receipt_dir()
    try:
        os.makedirs(d, exist_ok=True)
        if os.name == "posix":
            os.chmod(d, stat.S_IRWXU)
            st = os.lstat(d)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
                return None
    except OSError:
        return None
    return d


def _read_checked(path: str) -> dict | None:
    """读一份回执；不是本人的普通小文件 / 读不懂 / schema 不对一律 None。"""
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            return None
        if os.name == "posix" and st.st_uid != os.getuid():
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    if not isinstance(data.get("id"), str) or not ID_RE.match(data["id"]):
        return None
    return data


def _write_atomic(path: str, payload: dict) -> bool:
    tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    return True


def _entries(d: str) -> list[str]:
    """目录里合规命名的回执文件（`<id>.json`），最多 `MAX_SCAN` 条。"""
    try:
        names = os.listdir(d)
    except OSError:
        return []
    out = [n for n in names if n.endswith(".json") and ID_RE.match(n[: -len(".json")])]
    return out[:MAX_SCAN]


def _prune(d: str, now: float) -> None:
    """清过期与陈旧的期望（含命名不合规的残留 .tmp 与崩溃残留的 .lock）。"""
    try:
        names = os.listdir(d)
    except OSError:
        return
    for name in names:
        path = os.path.join(d, name)
        try:
            limit = LOCK_STALE_S if name.endswith(LOCK_SUFFIX) else STALE_S
            if now - os.lstat(path).st_mtime > limit:
                os.unlink(path)
        except OSError:
            pass


# ------------------------------- CLI 侧 ------------------------------------
def expect(project: str, stem: str, wait_s: float, *, now=time.time) -> Expectation | None:
    """写一份期望。返回 None = 通道不可用（数据目录写不了 / 目录不是本人的）。"""
    d = _ensure_dir()
    if d is None:
        return None
    t = now()
    _prune(d, t)
    eid = secrets.token_hex(16)
    path = os.path.join(d, eid + ".json")
    ident = _identity(project)
    expires = t + finite_budget(wait_s) + GRACE_S
    payload = {
        "schema": SCHEMA,
        "id": eid,
        "project": ident,
        "stem": stem,
        "created_at": t,
        "expires_at": expires,
        "result": None,
    }
    if not _write_atomic(path, payload):
        return None
    return Expectation(eid, path, ident, stem, expires)


def read_result(exp: Expectation) -> dict | None:
    """读实例写回来的结果；还没有 / 不合规返回 None。返回的是**净化过**的字段。"""
    data = _read_checked(exp.path)
    if data is None or data.get("id") != exp.id:
        return None
    res = data.get("result")
    if not isinstance(res, dict):
        return None
    status = res.get("outcome")
    if status == STATUS_OK:
        count = res.get("manifest_elements")
        return {
            "status": STATUS_OK,
            "stem": res.get("stem") if isinstance(res.get("stem"), str) else None,
            "registry_match": res.get("registry_match") is True,
            "manifest_elements": count if isinstance(count, int) and count >= 0 else 0,
        }
    if status == STATUS_FAILED:
        code = res.get("code")
        return {
            "status": STATUS_FAILED,
            "code": code if isinstance(code, str) and CODE_RE.match(code) else CODE_RENDER_FAILED,
        }
    return None


def discard(exp: Expectation) -> None:
    for p in (exp.path, exp.path + LOCK_SUFFIX):
        try:
            os.unlink(p)
        except OSError:
            pass


def unverified(reason: str, waited_ms: int = 0) -> dict:
    """没法下结论（不是失败）：`launch.render` 的 unverified 形状只此一处。"""
    return {
        "status": STATUS_UNVERIFIED,
        "reason": reason,
        "verified": False,
        "waited_ms": waited_ms,
    }


def summarize(exp: Expectation, got: dict | None, waited_ms: int, *, reason: str) -> dict:
    """把结果收成 `launch.render`。

    `verified` 是 `verified_success` 的**唯一判据**（插件只消费它，不另写一份）：
    渲染成功、结果里的 stem 与期望的目标一致、注册表里确有这个 stem、manifest 非空。
    少一条都不是 verified——渲染出来了不等于渲染的是目标那张图。
    """
    if got is None:
        return unverified(reason, waited_ms)
    if got["status"] == STATUS_FAILED:
        return {
            "status": STATUS_FAILED,
            "code": got["code"],
            "verified": False,
            "waited_ms": waited_ms,
        }
    verified = (
        got["stem"] == exp.stem and got["registry_match"] is True and got["manifest_elements"] > 0
    )
    return {
        "status": STATUS_OK,
        "verified": verified,
        "stem": got["stem"],
        "registry_match": got["registry_match"],
        "manifest_elements": got["manifest_elements"],
        "waited_ms": waited_ms,
    }


def wait(
    exp: Expectation,
    budget_s: float,
    *,
    clock=time.monotonic,
    sleep=time.sleep,
    interval: float = 0.25,
) -> dict:
    """有界轮询（可注入时钟，不是裸 sleep）。到点没有结论 = unverified/timeout，**不是 failed**。"""
    budget_s = finite_budget(budget_s)  # nan / inf / 负数按 0：读一次就收，不无界等
    start = clock()
    while True:
        got = read_result(exp)
        elapsed_ms = int((clock() - start) * 1000)
        if got is not None:
            return summarize(exp, got, elapsed_ms, reason=REASON_TIMEOUT)
        if clock() - start >= budget_s:
            return summarize(exp, None, elapsed_ms, reason=REASON_TIMEOUT)
        sleep(interval)


# ------------------------------- 实例侧 ------------------------------------
def _acquire_lock(lock: str) -> bool:
    """拿一份回执的写锁（`O_EXCL`，跨平台）。拿不到 = 别人正在写；陈旧锁先清一次再试。"""
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
        except FileExistsError:
            try:
                if time.time() - os.lstat(lock).st_mtime <= LOCK_STALE_S:
                    return False
                os.unlink(lock)
            except OSError:
                return False
            continue
        except OSError:
            return False
        os.close(fd)
        return True
    return False


def _record(project: str, stem: str, result: dict, *, now=time.time) -> int:
    """把结果写进所有匹配的、未过期且尚无结论的期望。返回写了几份。"""
    d = os.path.join(session_client.session_dir(), DIR_NAME)
    if not os.path.isdir(d):  # 绝大多数渲染不是交接触发的：目录不在 = 零成本返回
        return 0
    t = now()
    ident = _identity(project)
    wrote = 0
    for name in _entries(d):
        path = os.path.join(d, name)
        data = _read_checked(path)
        if data is None or data.get("result") is not None:
            continue
        expires = data.get("expires_at")
        if not isinstance(expires, (int, float)) or expires < t:
            continue
        if data.get("project") != ident or data.get("stem") != stem:
            continue
        lock = path + LOCK_SUFFIX
        if not _acquire_lock(lock):
            continue  # 别人正在写这份的第一个结论：第一份赢，我们让路
        try:
            fresh = _read_checked(path)  # 拿到锁后重读：读到写之间可能已有人写完
            if fresh is None or fresh.get("result") is not None:
                continue
            fresh["result"] = {**result, "recorded_at": t}
            if _write_atomic(path, fresh):
                wrote += 1
        finally:
            try:
                os.unlink(lock)
            except OSError:
                pass
    return wrote


def record_ok(
    project: str, stem: str, *, registry_match: bool, manifest_elements: int, now=time.time
) -> int:
    return _record(
        project,
        stem,
        {
            "outcome": STATUS_OK,
            "stem": stem,
            "registry_match": bool(registry_match),
            "manifest_elements": int(manifest_elements),
        },
        now=now,
    )


def record_failed(project: str, stem: str, code: str, *, now=time.time) -> int:
    safe = code if isinstance(code, str) and CODE_RE.match(code) else CODE_RENDER_FAILED
    return _record(project, stem, {"outcome": STATUS_FAILED, "code": safe}, now=now)
