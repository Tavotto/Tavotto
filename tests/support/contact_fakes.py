"""「联系作者」服务端（diagnostics-api 的 `/v1/messages/*`，契约 v1）的**本地模拟**，给 `engine/contactsend.py` 的用例当对端。

只按契约独立写成（不 import infra 的任何代码）：

* `POST /v1/messages/init|complete|cancel`、`GET /v1/messages/{id}/replies`（`Authorization: Bearer <reply_token>`，
  token 不对或信不存在一律 404）；
* 错误体是 reports 一族的实际形状：顶层 `{"ok": false, "code", "error"}`（客户端只认这一种）；
* 所有请求要带 `Tavotto-Diagnostics-Api: 1`，缺了回 400 `missing_api_header`（契约“公共”一节）；
* 幂等：同一个 `client_message_id` 重放返回同一个 message_id / reply_token；
* 附件：init 里报的 content_type 必须在白名单、至多 5 个、合计 ≤ 20 MiB；上传走回环开发模式的 `/v1/local-upload`
  （表单字段按授权给的顺序、文件字段在最后）；complete 核对每个附件对象存在、大小一致、魔数与 content_type 相符。

每个请求记在 `calls`（含请求头 / 体），“有没有联网”的判据是这份记录，不是客户端自己说没发。
脚本化失败：`fail(endpoint, status, code)`、`drop(endpoint)`（先不执行就断线）、`drop_after(endpoint)`（先执行再丢响应）、
`redirect(endpoint, location)`。
"""

from __future__ import annotations

import http.server
import json
import re
import secrets
import socket
import threading
from collections import deque

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
REPORT_ID = re.compile(r"^TVD-[0-9A-HJKMNP-TV-Z]{4}(?:-[0-9A-HJKMNP-TV-Z]{4}){3}$")
TYPES = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/gif": b"GIF8",
    "image/webp": b"RIFF",
    "application/pdf": b"%PDF-",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": b"PK\x03\x04",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": b"PK\x03\x04",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": b"PK\x03\x04",
}
INIT_FIELDS = {
    "client_message_id",
    "locale",
    "app_version",
    "os",
    "arch",
    "distribution",
    "route",
    "subject",
    "body",
    "reply_email",
    "report_id",
    "attachments",
}
MAX_TOTAL = 20 * 1024 * 1024


def _crockford(n: int) -> str:
    return "".join(secrets.choice(_CROCKFORD) for _ in range(n))


class Forced:
    def __init__(
        self,
        kind: str,
        status: int = 500,
        code: str = "internal",
        location: str = "",
        event: threading.Event | None = None,
    ):
        self.kind, self.status, self.code, self.location = kind, status, code, location
        self.event = event


class Message:
    def __init__(self, mid: str, key: str, req: dict) -> None:
        self.mid = mid
        self.key = key  # client_message_id
        self.req = req
        self.reply_token = secrets.token_hex(32)
        self.complete_token = secrets.token_hex(32)
        self.state = "pending"
        self.uploads: dict[int, bytes] = {}
        self.replies: list[dict] = []
        self.complete_runs = 0


class FakeContactServer:
    """`with FakeContactServer(tls) as srv:`；`srv.base` 是 `https://127.0.0.1:<port>`。"""

    def __init__(self, tls, *, share_with: FakeContactServer | None = None) -> None:
        self.tls = tls
        self.calls: list[dict] = []
        # `share_with`：另一条线路背后是**同一个后端**（api.tavotto.com 的 Worker 只转发到 tavotto.cn），共用信件状态
        self.messages: dict[str, Message] = share_with.messages if share_with else {}
        self.by_key: dict[str, str] = share_with.by_key if share_with else {}
        self.forced: dict[str, deque[Forced]] = {}
        self.upload_url: str | None = None  # 覆盖 init 响应里的 upload.url（SSRF 用例）
        self.lock = share_with.lock if share_with else threading.RLock()
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    # ---------------------------------------------------------------- 脚本化
    def _force(self, endpoint: str, item: Forced, times: int) -> None:
        with self.lock:
            q = self.forced.setdefault(endpoint, deque())
            q.extend(item for _ in range(times))

    def fail(self, endpoint: str, status: int, code: str, *, times: int = 1) -> None:
        self._force(endpoint, Forced("status", status, code), times)

    def drop(self, endpoint: str, *, times: int = 1) -> None:
        self._force(endpoint, Forced("drop_before"), times)

    def drop_after(self, endpoint: str, *, times: int = 1) -> None:
        self._force(endpoint, Forced("drop_after"), times)

    def redirect(self, endpoint: str, location: str, *, status: int = 307, times: int = 1) -> None:
        self._force(endpoint, Forced("redirect", status=status, location=location), times)

    def hold_early(self, endpoint: str, event: threading.Event) -> None:
        """读请求体之前先停住，直到 `event` 被置位：客户端的大块发送会在内核缓冲写满后阻塞（取消用例）。"""
        self._force(endpoint, Forced("hold_early", event=event), 1)

    def reply(self, mid: str, body: str, rid: str | None = None) -> str:
        """作者在 admin CLI 里回信。"""
        with self.lock:
            msg = self.messages[mid]
            rid = rid or "r_" + _crockford(10)
            msg.replies.append({"id": rid, "created_at": "2026-10-22T08:00:00Z", "body": body})
            msg.state = "replied"
            return rid

    def paths(self) -> list[str]:
        with self.lock:
            return [c["path"] for c in self.calls]

    def count(self, path: str) -> int:
        return self.paths().count(path)

    def only_message(self) -> Message:
        with self.lock:
            (msg,) = self.messages.values()
            return msg

    @property
    def base(self) -> str:
        return f"https://127.0.0.1:{self.port}"

    # ---------------------------------------------------------------- 生命周期
    def __enter__(self):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):  # noqa: N802
                outer._handle(self)

            def do_GET(self):  # noqa: N802
                outer._handle(self)

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
        self._thread.join(timeout=10)

    # ---------------------------------------------------------------- 响应
    def _send(self, h, status: int, obj: dict | None) -> None:
        body = json.dumps(obj).encode() if obj is not None else b""
        h.send_response(status)
        h.send_header("Tavotto-Diagnostics-Api", "1.0.0")
        if obj is not None:
            h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        if body:
            h.wfile.write(body)
        h.wfile.flush()

    def _error(self, h, status: int, code: str) -> None:
        self._send(h, status, {"ok": False, "code": code, "error": "fixed wording"})

    @staticmethod
    def _drop(h) -> None:
        h.close_connection = True
        try:
            h.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        h.connection.close()

    # ---------------------------------------------------------------- 路由
    def _handle(self, h) -> None:
        path = h.path
        endpoint = {
            "/v1/messages/init": "init",
            "/v1/messages/complete": "complete",
            "/v1/messages/cancel": "cancel",
            "/v1/local-upload": "upload",
        }.get(path)
        if endpoint is None and re.match(r"^/v1/messages/[^/]+/replies$", path):
            endpoint = "replies"
        with self.lock:
            q = self.forced.get(endpoint or "")
            early = q.popleft() if q and q[0].kind == "hold_early" else None
        if early is not None and early.event is not None:
            early.event.wait(30)
        length = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(length) if length else b""
        record = {
            "path": path,
            "method": h.command,
            "headers": {k.lower(): v for k, v in h.headers.items()},
            "len": len(body),
        }
        if endpoint not in ("upload", "replies"):
            try:
                record["json"] = json.loads(body.decode("utf-8")) if body else None
            except ValueError:
                record["json"] = "<invalid>"
        with self.lock:
            self.calls.append(record)
            q = self.forced.get(endpoint or "")
            forced = q.popleft() if q else None
        if endpoint is None:
            self._error(h, 404, "not_found")
            return
        after_drop = False
        if forced is not None:
            if forced.kind == "status":
                self._error(h, forced.status, forced.code)
                return
            if forced.kind == "drop_before":
                self._drop(h)
                return
            if forced.kind == "redirect":
                h.send_response(forced.status)
                h.send_header("Location", forced.location)
                h.send_header("Content-Length", "0")
                h.end_headers()
                return
            after_drop = forced.kind == "drop_after"
        if endpoint == "upload":
            self._upload(h, body, record, after_drop)
            return
        if h.headers.get("Tavotto-Diagnostics-Api") != "1":
            self._error(h, 400, "missing_api_header")
            return
        if endpoint == "replies":
            self._replies(h, record)
            return
        req = record.get("json")
        if not isinstance(req, dict):
            self._error(h, 400, "bad_json")
            return
        if not h.headers.get("Content-Type", "").startswith("application/json"):
            self._error(h, 415, "bad_content_type")
            return
        status, out = getattr(self, f"_do_{endpoint}")(req)
        if after_drop:
            self._drop(h)
            return
        self._send(h, status, out)

    # -- init
    def _do_init(self, req: dict) -> tuple[int, dict]:
        extra = set(req) - INIT_FIELDS
        key = req.get("client_message_id")
        body = req.get("body")
        if extra or not isinstance(key, str) or not UUID4.match(key):
            return 422, {"ok": False, "code": "bad_request", "error": "x"}
        if not isinstance(body, str) or not 1 <= len(body) <= 5000:
            return 422, {"ok": False, "code": "bad_request", "error": "x"}
        if len(req.get("subject") or "") > 120:
            return 422, {"ok": False, "code": "bad_request", "error": "x"}
        email = req.get("reply_email")
        if email is not None and (
            email.count("@") != 1
            or any(c.isspace() or c in ',;<>()"' for c in email)
            or "." not in email.partition("@")[2]
        ):
            return 422, {"ok": False, "code": "invalid_email", "error": "x"}
        rid = req.get("report_id")
        if rid is not None and (not REPORT_ID.match(rid) or rid != "TVD-AAAA-BBBB-CCCC-DDDD"):
            return 422, {"ok": False, "code": "report_not_found", "error": "x"}
        atts = req.get("attachments") or []
        if len(atts) > 5 or sum(a.get("size", 0) for a in atts) > MAX_TOTAL:
            return 413, {"ok": False, "code": "payload_too_large", "error": "x"}
        if any(a.get("content_type") not in TYPES for a in atts):
            return 415, {"ok": False, "code": "unsupported_media_type", "error": "x"}
        with self.lock:
            mid = self.by_key.get(key)
            if mid is None:
                mid = f"TVM-{_crockford(4)}-{_crockford(4)}-{_crockford(4)}-{_crockford(4)}"
                self.messages[mid] = Message(mid, key, req)
                self.by_key[key] = mid
                status = 201
            else:
                status = 200
            msg = self.messages[mid]
            if msg.state in ("cancelled",):
                return 409, {"ok": False, "code": "session_closed", "error": "x"}
        out_atts = []
        if msg.state != "pending":  # 已 complete 的信重放：200 + 空票据（服务端实际行为）
            atts = []
        for i, a in enumerate(atts):
            out_atts.append(
                {
                    "index": i,
                    "upload": {
                        "url": self.upload_url or f"{self.base}/v1/local-upload",
                        "fields": {"key": f"{mid}/{i}", "policy": "p" * 8},
                        "file_field": "file",
                        "max_bytes": a["size"],
                    },
                }
            )
        return status, {
            "message_id": mid,
            "reply_token": msg.reply_token,
            "complete_token": msg.complete_token,
            "expires_at": "2026-10-22T09:00:00Z",
            "attachments": out_atts,
        }

    def _find(self, req: dict) -> Message | None:
        msg = self.messages.get(req.get("message_id") or "")
        if msg is None or not secrets.compare_digest(
            msg.complete_token, str(req.get("complete_token"))
        ):
            return None
        return msg

    def _do_complete(self, req: dict) -> tuple[int, dict]:
        with self.lock:
            msg = self._find(req)
            if msg is None:
                return 404, {"ok": False, "code": "not_found", "error": "x"}
            if msg.state == "cancelled":
                return 409, {"ok": False, "code": "session_closed", "error": "x"}
            msg.complete_runs += 1
            if msg.state in ("new", "replied", "closed"):
                return 200, {"message_id": msg.mid, "state": msg.state}
            atts = msg.req.get("attachments") or []
            for i, a in enumerate(atts):
                data = msg.uploads.get(i)
                if (
                    data is None
                    or len(data) != a["size"]
                    or not data.startswith(TYPES[a["content_type"]])
                ):
                    msg.state = "rejected"
                    return 422, {"ok": False, "code": "attachment_invalid", "error": "x"}
            msg.state = "new"
            return 200, {"message_id": msg.mid, "state": "new"}

    def _do_cancel(self, req: dict) -> tuple[int, dict]:
        with self.lock:
            msg = self._find(req)
            if msg is None:
                return 404, {"ok": False, "code": "not_found", "error": "x"}
            if msg.state == "pending":
                msg.state = "cancelled"
            return 200, {"message_id": msg.mid, "state": msg.state}

    # -- replies
    def _replies(self, h, record: dict) -> None:
        mid = h.path.split("/")[3]
        auth = h.headers.get("Authorization", "")
        with self.lock:
            msg = self.messages.get(mid)
            ok = (
                msg is not None
                and auth.startswith("Bearer ")
                and secrets.compare_digest(msg.reply_token, auth[7:])
            )
            if not ok:
                self._error(h, 404, "not_found")
                return
            self._send(
                h,
                200,
                {
                    "message_id": mid,
                    "state": msg.state if msg.state in ("new", "replied", "closed") else "new",
                    "replies": list(msg.replies),
                },
            )

    # -- 上传
    def _upload(self, h, body: bytes, record: dict, after_drop: bool) -> None:
        m = re.match(r"^multipart/form-data; boundary=(.+)$", h.headers.get("Content-Type", ""))
        if not m:
            self._error(h, 415, "bad_content_type")
            return
        boundary = ("--" + m.group(1)).encode()
        parts = body.split(boundary)
        if parts[0] != b"" or parts[-1] != b"--\r\n":
            self._error(h, 400, "bad_request")
            return
        names: list[str] = []
        values: dict[str, bytes] = {}
        file_bytes = None
        for raw in parts[1:-1]:
            head, _, rest = raw.partition(b"\r\n\r\n")
            nm = re.search(rb'name="([^"]+)"', head)
            if not nm or not rest.endswith(b"\r\n"):
                self._error(h, 400, "bad_request")
                return
            names.append(nm.group(1).decode())
            if b"filename=" in head:
                file_bytes = rest[:-2]
                record["file_head"] = head.decode("ascii", "replace")
            else:
                values[names[-1]] = rest[:-2]
        record["field_order"] = names
        key = values.get("key", b"").decode()
        mid, _, idx = key.rpartition("/")
        with self.lock:
            msg = self.messages.get(mid)
            if (
                msg is None
                or msg.state != "pending"
                or names != ["key", "policy", "file"]
                or file_bytes is None
                or not idx.isdigit()
            ):
                self._error(h, 403, "forbidden")
                return
            msg.uploads[int(idx)] = file_bytes
        if after_drop:
            self._drop(h)
            return
        h.send_response(204)
        h.send_header("Content-Length", "0")
        h.end_headers()
