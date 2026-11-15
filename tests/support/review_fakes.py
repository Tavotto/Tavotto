"""外部审阅服务端（`Tavotto/infra` 的 `serverless/review/`，线上格式 v1）的**本地模拟**，给客户端用例当对端。

只按线上格式文档独立写成（不 import infra 的任何代码）：设备签名验签（PyNaCl）、时间戳偏差、额度（每设备每天新建 N 个、
每链接每天更新 M 次）、`client_*_id` 幂等（同 id 不同指纹 → 409 invalid）、上传（回环同源 `PUT /upload/<k>`）、complete 核对大小与 PNG
魔数、feed 增量（游标 = 事件序号）、回复 / 标记已处理、停止分享。它说的是模拟契约，不是真 CloudBase：真服务联调标 NOT_RUN。

每个请求都记在 `calls`，「有没有联网」的判据是这份记录，不是客户端自己说没发。脚本化失败（`force`）：对某条路径前缀排队若干次
「先这样回」——错误状态 / 断线。
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field

_B32 = "abcdefghijklmnopqrstuvwxyz234567"


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class Forced:
    kind: str  # status | drop
    status: int = 500
    code: str = "server"
    retry_after: int | None = None


@dataclass
class Share:
    share_id: str
    owner_token: str
    review_token: str
    device: str
    kind: str
    title: str
    note: str
    versions: dict[int, dict] = field(default_factory=dict)
    uploads: dict[str, bytes] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    revoked: bool = False
    update_used: int = 0


class FakeReviewServer:
    def __init__(self, tls, *, new_limit: int = 1, update_limit: int = 5) -> None:
        self.tls = tls
        self.new_limit = new_limit
        self.update_limit = update_limit
        self.calls: list[dict] = []
        self.shares: dict[str, Share] = {}
        self.by_client_id: dict[
            str, tuple[str, str]
        ] = {}  # client id -> (fingerprint, share_id | "v:<id>:<n>")
        self.new_used: dict[str, int] = {}
        self.forced: dict[str, deque[Forced]] = {}
        self.upload_url_override: str | None = None
        self.skew_seconds = 600
        self.lock = threading.RLock()
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0
        self._seq = 0

    @property
    def base(self) -> str:
        return f"https://127.0.0.1:{self.port}"

    # ------------------------------------------------------------------ 脚本化
    def force(self, prefix: str, *items: Forced, times: int = 1) -> None:
        with self.lock:
            q = self.forced.setdefault(prefix, deque())
            for _ in range(times):
                q.extend(items)

    def paths(self) -> list[str]:
        with self.lock:
            return [f"{c['method']} {c['path']}" for c in self.calls]

    def calls_to(self, prefix: str) -> list[dict]:
        with self.lock:
            return [c for c in self.calls if c["path"].startswith(prefix)]

    # ------------------------------------------------------------------ 生命周期
    def __enter__(self):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                outer._handle(self)

            do_POST = do_PUT = do_PATCH = do_DELETE = do_GET  # noqa: N815

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def get_request(self):
                sock, addr = self.socket.accept()
                try:
                    sock = outer.tls.wrap_socket(sock, server_side=True)
                except OSError:
                    sock.close()
                    raise
                return sock, addr

        self._server = Server(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        assert self._server is not None and self._thread is not None
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(5)

    # ------------------------------------------------------------------ 构件
    def _reply(self, h, status: int, obj=None, *, headers: dict | None = None) -> None:
        body = b"" if obj is None else json.dumps(obj).encode()
        h.send_response(status)
        h.send_header("Content-Length", str(len(body)))
        if obj is not None:
            h.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(body)

    def _error(self, h, status: int, code: str, retry_after: int | None = None) -> None:
        extra = {"Retry-After": str(retry_after)} if retry_after else None
        self._reply(h, status, {"error": {"code": code, "message": "fixed wording"}}, headers=extra)

    def _take(self, path: str) -> Forced | None:
        with self.lock:
            for prefix, q in self.forced.items():
                if path.startswith(prefix) and q:
                    return q.popleft()
        return None

    def _new_id(self) -> str:
        self._seq += 1
        return "rs_" + "".join(secrets.choice(_B32) for _ in range(16))

    def _auth(self, h, method: str, path: str, body: bytes) -> str | None:
        """验设备签名；失败直接回错并返回 None。返回设备公钥（base64url）。"""
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey

        dev = h.headers.get("X-Tavotto-Device", "")
        ts = h.headers.get("X-Tavotto-Timestamp", "")
        sig = h.headers.get("X-Tavotto-Signature", "")
        try:
            vk = VerifyKey(b64u_decode(dev))
            msg = (
                f"tavotto-review-v1\n{method}\n{path}\n{ts}\n{hashlib.sha256(body).hexdigest()}"
            ).encode()
            vk.verify(msg, b64u_decode(sig))
            stamp = int(ts)
        except (BadSignatureError, ValueError, TypeError):
            self._error(h, 401, "bad_signature")
            return None
        if abs(time.time() - stamp) > self.skew_seconds:
            self._error(h, 401, "clock_skew")
            return None
        return dev

    def _share_for(self, h, share_id: str, device: str) -> Share | None:
        sh = self.shares.get(share_id)
        bearer = h.headers.get("Authorization", "")
        if sh is None or sh.revoked or sh.device != device or bearer != f"Bearer {sh.owner_token}":
            self._error(h, 404, "share_gone")
            return None
        return sh

    # ------------------------------------------------------------------ 路由
    def _handle(self, h) -> None:
        length = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(length) if length else b""
        method, path = h.command, h.path
        with self.lock:
            self.calls.append(
                {"method": method, "path": path, "body": body, "headers": dict(h.headers.items())}
            )
        forced = self._take(path)
        if forced is not None:
            if forced.kind == "drop":
                h.close_connection = True
                try:
                    h.connection.shutdown(2)
                except OSError:
                    pass
                return
            return self._error(h, forced.status, forced.code, forced.retry_after)
        if path.startswith("/upload/"):
            return self._upload(h, path, body)
        device = self._auth(h, method, path, body)
        if device is None:
            return
        bare = path.split("?", 1)[0]
        parts = [p for p in bare.split("/") if p]
        try:
            with self.lock:
                self._route(h, method, bare, parts, path, body, device)
        except (ValueError, KeyError):
            self._error(h, 422, "invalid")

    def _quota(self, device: str, sh: Share | None = None) -> dict:
        return {
            "new_left": max(0, self.new_limit - self.new_used.get(device, 0)),
            "update_left": None if sh is None else max(0, self.update_limit - sh.update_used),
            "resets_at": "2026-11-16T16:00:00Z",
        }

    def _route(self, h, method, bare, parts, full, body, device) -> None:
        if method == "POST" and bare == "/v1/shares":
            req = json.loads(body)
            fp = hashlib.sha256(
                json.dumps(
                    [req["kind"], req["title"], req["note"], req["png_bytes"], req["elements"]]
                ).encode()
            ).hexdigest()
            seen = self.by_client_id.get(device + req["client_share_id"])
            if seen is not None:
                if seen[0] != fp:
                    return self._error(h, 409, "invalid")
                sh = self.shares[seen[1]]
            else:
                if self.new_used.get(device, 0) >= self.new_limit:
                    return self._error(h, 429, "quota_new_exhausted")
                self.new_used[device] = self.new_used.get(device, 0) + 1
                sh = Share(
                    self._new_id(),
                    base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode(),
                    base64.urlsafe_b64encode(secrets.token_bytes(24)).rstrip(b"=").decode(),
                    device,
                    req["kind"],
                    req["title"],
                    req["note"],
                )
                sh.versions[1] = {
                    "png_bytes": req["png_bytes"],
                    "elements": req["elements"],
                    "complete": False,
                }
                self.shares[sh.share_id] = sh
                self.by_client_id[device + req["client_share_id"]] = (fp, sh.share_id)
            return self._reply(
                h,
                201,
                {
                    "share_id": sh.share_id,
                    "review_url": f"{self.base}/r/{sh.review_token}",
                    "owner_token": sh.owner_token,
                    "version": 1,
                    "upload": self._upload_spec(sh, 1),
                    "quota": self._quota(device),
                    "expires_at": "2026-12-15T00:00:00Z",
                },
            )
        if method == "GET" and bare == "/v1/quota":
            return self._reply(h, 200, self._quota(device))
        if len(parts) >= 3 and parts[:2] == ["v1", "shares"]:
            sh = self._share_for(h, parts[2], device)
            if sh is None:
                return
            rest = parts[3:]
            if method == "DELETE" and not rest:
                sh.revoked = True
                return self._reply(h, 204)
            if method == "POST" and rest == ["versions"]:
                req = json.loads(body)
                key = f"{device}v{req['client_version_id']}"
                seen = self.by_client_id.get(key)
                if seen is None:
                    if sh.update_used >= self.update_limit:
                        return self._error(h, 429, "quota_update_exhausted")
                    sh.update_used += 1
                    n = max(sh.versions) + 1
                    sh.versions[n] = {
                        "png_bytes": req["png_bytes"],
                        "elements": req["elements"],
                        "complete": False,
                    }
                    self.by_client_id[key] = ("", f"{sh.share_id}:{n}")
                else:
                    n = int(seen[1].rsplit(":", 1)[1])
                return self._reply(
                    h,
                    201,
                    {
                        "version": n,
                        "upload": self._upload_spec(sh, n),
                        "quota": self._quota(device, sh),
                        "expires_at": "2026-12-20T00:00:00Z",
                    },
                )
            if (
                method == "POST"
                and len(rest) == 3
                and rest[0] == "versions"
                and rest[2] == "complete"
            ):
                v = int(rest[1])
                ver = sh.versions[v]
                data = sh.uploads.get(f"{sh.share_id}/{v}")
                if (
                    data is None
                    or len(data) != ver["png_bytes"]
                    or not data.startswith(b"\x89PNG\r\n\x1a\n")
                ):
                    return self._error(h, 422, "invalid")
                ver["complete"] = True
                return self._reply(h, 200, {"version": v, "expires_at": "2026-12-25T00:00:00Z"})
            if method == "GET" and rest == ["feed"]:
                q = full.split("?since=", 1)[1] if "?since=" in full else ""
                from urllib.parse import unquote

                since = int(unquote(q)) if q else 0
                return self._reply(
                    h,
                    200,
                    {
                        "cursor": str(len(sh.events)),
                        "events": sh.events[since:],
                        "share": {
                            "expires_at": "2026-12-25T00:00:00Z",
                            "revoked": False,
                            "latest_version": max(sh.versions),
                        },
                    },
                )
            if (
                method == "POST"
                and len(rest) == 3
                and rest[0] == "comments"
                and rest[2] == "replies"
            ):
                rep = {
                    "id": "rp_" + secrets.token_hex(4),
                    "from": "owner",
                    "author": "作者",
                    "text": json.loads(body)["text"],
                    "created_at": "2026-11-15T10:00:00Z",
                }
                sh.events.append({"type": "reply", "comment_id": rest[1], "reply": rep})
                return self._reply(h, 201, {"reply": rep})
            if method == "PATCH" and len(rest) == 2 and rest[0] == "comments":
                resolved = json.loads(body)["resolved"]
                sh.events.append({"type": "resolved", "comment_id": rest[1], "resolved": resolved})
                cm = next(
                    (
                        e["comment"]
                        for e in sh.events
                        if e["type"] == "comment" and e["comment"]["id"] == rest[1]
                    ),
                    None,
                )
                if cm is None:
                    return self._error(h, 404, "share_gone")
                return self._reply(h, 200, {"comment": {**cm, "resolved": resolved}})
        self._error(h, 404, "share_gone")

    def _upload_spec(self, sh: Share, v: int) -> dict:
        return {
            "url": self.upload_url_override or f"{self.base}/upload/{sh.share_id}/{v}",
            "method": "PUT",
            "headers": {"x-fake-sig": "abc"},
            "fields": None,
            "expires_at": "2026-11-15T11:00:00Z",
        }

    def _upload(self, h, path: str, body: bytes) -> None:
        _, _, sid, v = path.split("/", 3)
        sh = self.shares.get(sid)
        if sh is None or h.headers.get("x-fake-sig") != "abc" or h.command != "PUT":
            return self._error(h, 403, "invalid")
        sh.uploads[f"{sid}/{v}"] = body
        self._reply(h, 200)

    # ------------------------------------------------------------------ 审阅者一侧（直接造事件）
    def push(self, share_id: str, event: dict) -> None:
        with self.lock:
            self.shares[share_id].events.append(event)

    def reviewer_comment(
        self, share_id: str, cid: str, text: str, *, author: str = "林老师", v: int = 1, anchor=None
    ):
        c = {
            "id": cid,
            "v": v,
            "anchor": anchor or {"kind": "overall"},
            "author": author,
            "text": text,
            "resolved": False,
            "created_at": "2026-11-15T09:00:00Z",
            "replies": [],
        }
        self.push(share_id, {"type": "comment", "comment": c})
        return c
