"""统一的本地会话认证——浏览器模式与桌面模式共享同一道安全边界。

历史：这套「一次性 nonce → HttpOnly cookie + Host/Origin 校验」最初只给
桌面 sidecar（见 desktop.py 与 ADR 0002）；浏览器/PyPI 模式曾是无认证的
localhost 应用，而它暴露的是目录浏览、项目打开/创建、写回源文件、AI CLI
调用这类高权限能力——这是 1.0 审计确认的 P0（ADR 0008）。现在两种模式
共用本模块，差别只剩「谁负责拉起窗口、nonce 怎么交接」：

    桌面：Tauri 生成 nonce ──stdin──▶ sidecar；cookie 是会话级（窗口即进程）
    浏览器：main() 自己生成 nonce，落地 URL 带 `#dnonce=`（fragment 不进
    HTTP 请求行与访问日志）；cookie 带 Max-Age（服务器常驻、浏览器会重开）

认证模型（一次性 bootstrap）：

    页面 POST /api/session/bootstrap {nonce} ──▶ 验证后当场作废 nonce，
        Set-Cookie: HttpOnly + SameSite=Strict 的会话 cookie
    此后 /api、/exports、渲染图片、SSE 一律凭 cookie；Host/Origin 同时校验。
    /api/desktop/bootstrap 是同一处理器的兼容别名。

本机进程（CLI / 冒烟 / `tavotto open` 交接）另有一条凭据：浏览器模式启动时
把随机 secret 写进 0600 的凭据文件（engine/session_client.py），持有者可用
`X-Tavotto-Auth` 请求头直连，或经 POST /api/session/relaunch 换一枚新的
一次性 nonce 给新开的浏览器标签页——「已有实例在跑，把浏览器指过去」因此
是一次安全的 token 交接。**能读这份文件的进程本来就能读用户的任何文件**，
网页读不到，安全边界不变。

威胁模型（与 ADR 0002 相同）：本机其他页面 / drive-by localhost / DNS
rebinding。Host 只认 `127.0.0.1:<port>` 一种写法；带 Origin 的请求必须
严格同源；未认证请求对除首屏静态资源与 /api/version 之外的一切路径 401。

测试 / 开发旁路：app.config 里没有 `TAVOTTO_SESSION_STATE` 时 guard 全部
放行（test_client、`--insecure-no-auth`、vite dev proxy 的后端就是这一档）。
"""
from __future__ import annotations

import hmac
import logging
import secrets as _secrets
import threading
import time

from flask import jsonify, request

from .engine import session_client

LOG = logging.getLogger("tavotto.security")

COOKIE_NAME = "tavotto_session"
STATE_KEY = "TAVOTTO_SESSION_STATE"
BOOTSTRAP_PATH = "/api/session/bootstrap"
LEGACY_BOOTSTRAP_PATH = "/api/desktop/bootstrap"  # 兼容别名，同一处理器
RELAUNCH_PATH = session_client.RELAUNCH_PATH
PING_PATH = "/api/session/ping"

# 浏览器模式 cookie 的 Max-Age：服务器进程常驻、浏览器会整个重开，
# 会话级 cookie 会让「第二天再打开标签页」全是 401。token 只在进程内存里，
# 服务器一重启 cookie 自然作废——这不是把有效期放宽到 30 天的许可。
BROWSER_COOKIE_MAX_AGE = 30 * 24 * 3600

# 无会话即可访问的路径：首屏 HTML 与静态构建产物（不含任何用户数据）、
# bootstrap/relaunch 本身（各有各的凭据校验），以及 /api/version——
# 实例探测（resolve_port / handoff）靠它区分「Tavotto 在跑」与「别的程序」，
# 它只回版本号与 build 标记。其余一律凭 cookie 或本机凭据头。
_PUBLIC_PATHS = {"/", "/favicon.ico", "/api/version",
                 BOOTSTRAP_PATH, LEGACY_BOOTSTRAP_PATH, RELAUNCH_PATH}
_PUBLIC_PREFIXES = ("/assets/",)

_MAX_TOKENS = 8          # 并存的已认证浏览器上下文上限（relaunch 一次多一个）
_MAX_PENDING = 8         # 未兑换的一次性 nonce 上限
_RELAUNCH_NONCE_TTL = 300.0


class SessionState:
    """一次性 nonce 与进程内会话 token。全部只存内存，进程退出即失效。

    desktop.DesktopState 是它的别名——桌面 sidecar 与浏览器模式共用同一份
    实现，唯二差别由参数表达：`api_secret`（浏览器模式的本机进程凭据；
    桌面为 None，磁盘上不存在任何凭据）与 `persistent_cookie`。
    """

    def __init__(self, nonce: str, *, api_secret: str | None = None,
                 persistent_cookie: bool = False) -> None:
        # nonce → 过期时刻（None = 启动 nonce 不过期：窗口/浏览器可能起得慢）
        self._pending: dict[str, float | None] = {nonce: None}
        self._tokens: list[str] = []
        self._lock = threading.Lock()
        self.port: int | None = None
        self.api_secret = api_secret
        self.persistent_cookie = persistent_cookie

    def redeem(self, submitted: str) -> str | None:
        """核对并作废 nonce，签发会话 token；错误的猜测不作废任何 nonce
        （否则任何本地进程都能抢在真页面前面用错误 nonce 把应用 DoS 掉；
        256 位随机值也没有可行的爆破空间）。"""
        with self._lock:
            self._prune_locked()
            if not submitted:
                return None
            matched = None
            for nonce in self._pending:
                if hmac.compare_digest(nonce, submitted):
                    matched = nonce
                    break
            if matched is None:
                return None
            del self._pending[matched]
            token = _secrets.token_urlsafe(32)
            self._tokens.append(token)
            del self._tokens[:-_MAX_TOKENS]
            return token

    def issue_nonce(self, ttl: float = _RELAUNCH_NONCE_TTL) -> str:
        """签发一枚新的一次性 nonce（实例复用的 relaunch 交接用）。"""
        nonce = _secrets.token_urlsafe(32)
        with self._lock:
            self._prune_locked()
            while len(self._pending) >= _MAX_PENDING:
                self._pending.pop(next(iter(self._pending)))
            self._pending[nonce] = time.monotonic() + ttl
        return nonce

    def valid_cookie(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            return any(hmac.compare_digest(t, token) for t in self._tokens)

    def valid_api_secret(self, submitted: str | None) -> bool:
        return bool(self.api_secret) and bool(submitted) \
            and hmac.compare_digest(self.api_secret, submitted)

    def _prune_locked(self) -> None:
        now = time.monotonic()
        for nonce, expiry in list(self._pending.items()):
            if expiry is not None and expiry < now:
                del self._pending[nonce]


def install(flask_app) -> None:
    """在 app 创建期挂上会话认证钩子与 bootstrap / relaunch / ping 端点。

    必须在处理第一个请求前调用（app.py import 期）；`TAVOTTO_SESSION_STATE`
    不存在时（test_client / --insecure-no-auth）钩子直接放行、端点 404。
    """

    def _bootstrap():
        state: SessionState | None = flask_app.config.get(STATE_KEY)
        if state is None:
            return jsonify({"error": "会话认证未启用", "code": "no_session_mode"}), 404
        body = request.get_json(force=True, silent=True) or {}
        token = state.redeem(str(body.get("nonce") or ""))
        if token is None:
            return jsonify({"error": "启动凭据无效或已使用",
                            "code": "bad_nonce"}), 403
        resp = jsonify({"ok": True})
        max_age = BROWSER_COOKIE_MAX_AGE if state.persistent_cookie else None
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="Strict",
                        secure=False, path="/", max_age=max_age)
        return resp

    flask_app.add_url_rule(BOOTSTRAP_PATH, "session_bootstrap",
                           _bootstrap, methods=["POST"])
    flask_app.add_url_rule(LEGACY_BOOTSTRAP_PATH, "desktop_bootstrap",
                           _bootstrap, methods=["POST"])

    @flask_app.post(RELAUNCH_PATH)
    def _session_relaunch():
        state: SessionState | None = flask_app.config.get(STATE_KEY)
        if state is None or not state.api_secret:
            # 桌面模式也走这条 404：它没有磁盘凭据，实例复用由壳的单实例转发负责
            return jsonify({"error": "会话认证未启用", "code": "no_session_mode"}), 404
        body = request.get_json(force=True, silent=True) or {}
        if not state.valid_api_secret(str(body.get("secret") or "")):
            return jsonify({"error": "本机凭据无效", "code": "bad_secret"}), 403
        return jsonify({"nonce": state.issue_nonce()})

    @flask_app.get(PING_PATH)
    def _session_ping():
        # 认证由 guard 负责：走到这里即已通过（或根本没启用认证）
        return jsonify({"ok": True})

    @flask_app.before_request
    def _session_guard():
        state: SessionState | None = flask_app.config.get(STATE_KEY)
        if state is None:
            return None  # 测试 / --insecure-no-auth：不做任何校验
        # Host / Origin：本进程只被 127.0.0.1:<port> 上的本应用访问。
        # DNS rebinding、localhost 花式写法、别的本地页面发起的跨源请求全拒。
        if state.port is not None:
            if request.headers.get("Host", "") != f"127.0.0.1:{state.port}":
                return jsonify({"error": "拒绝的 Host", "code": "bad_host"}), 403
            origin = request.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{state.port}":
                return jsonify({"error": "拒绝的来源", "code": "bad_origin"}), 403
        p = request.path
        if p in _PUBLIC_PATHS or p.startswith(_PUBLIC_PREFIXES):
            return None
        if state.valid_cookie(request.cookies.get(COOKIE_NAME)):
            return None
        if state.valid_api_secret(request.headers.get(session_client.AUTH_HEADER)):
            return None
        return jsonify({"error": "会话未建立或已失效",
                        "code": "session_auth_required"}), 401


def new_browser_state(port: int) -> tuple[SessionState, str]:
    """浏览器模式的会话状态：生成 nonce 与本机凭据、写凭据文件。

    返回 (state, nonce)；调用方把 state 放进 app.config、把 nonce 拼进
    落地 URL 的 fragment。凭据文件在进程退出时由调用方清理。
    """
    nonce = _secrets.token_urlsafe(32)
    secret = _secrets.token_urlsafe(32)
    state = SessionState(nonce, api_secret=secret, persistent_cookie=True)
    state.port = port
    session_client.publish_secret(port, secret)
    return state, nonce
