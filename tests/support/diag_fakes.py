"""诊断接收服务（`Tavotto/infra` 的 `diagnostics-api` v1.0.0）的**本地模拟**，给客户端用例当对端。

只按 `docs/diagnostics-api/openapi.json` 与其 README 的契约独立写成（不 import infra 的任何代码）：

* `POST /v1/reports/init|complete|cancel`：请求体拒绝未知字段、`client_request_id` 必须是 UUIDv4、枚举闭集、
  `size ≥ 1`、令牌 64 位十六进制；错误体统一 `{"ok": false, "code", "error"}`；429 / 503 带 `Retry-After`；
  每个响应带 `Tavotto-Diagnostics-Api: 1.0.0`；
* 幂等：同一个 `client_request_id` 的 init 重放回 200（pending 时给新的上传授权），内容不同回 409 `idempotency_conflict`；
  complete / cancel 幂等；
* `POST /v1/local-upload`：服务端 `DIAG_STORAGE=local` 时的上传端点——校验 multipart 表单字段**按授权给的顺序**、
  文件字段在最后；回 204。

它说的是模拟契约，不是真 COS：真 COS 的 C1–C16 验收标 NOT_RUN（`docs/diagnostics-api/acceptance-cos.md`）。

脚本化的失败（`forced`）：对某个端点排队若干次「先这样回」——错误状态、先执行再丢响应（complete 丢了响应）、
先不执行就断线、重定向、延迟、阻塞到某个事件。每个请求都记在 `calls`，「有没有联网」的判据是这份记录，不是猜。
"""

from __future__ import annotations

import hashlib
import http.server
import json
import re
import secrets
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field

API_VERSION = "1.0.0"
UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
TOKEN = re.compile(r"^[0-9a-f]{64}$")
REPORT_ID = re.compile(r"^TVD-[0-9A-HJKMNP-TV-Z]{4}(?:-[0-9A-HJKMNP-TV-Z]{4}){3}$")
CATEGORIES = {
    "render_failure",
    "export_failure",
    "environment",
    "editing",
    "install_update",
    "performance",
    "crash",
    "other",
}
INIT_FIELDS = {
    "client_request_id",
    "tavotto_version",
    "os",
    "arch",
    "distribution",
    "category",
    "bundle_schema",
    "size",
    "sha256",
    "note",
}
INIT_REQUIRED = {
    "client_request_id",
    "tavotto_version",
    "os",
    "arch",
    "distribution",
    "category",
    "bundle_schema",
    "size",
}
ENUMS = {
    "os": {"macos", "windows", "linux", "other"},
    "arch": {"arm64", "x86_64", "other"},
    "distribution": {"desktop", "pipx", "pip", "source", "unknown"},
}
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


@dataclass
class Forced:
    kind: str  # status | drop_after | drop_before | redirect | sleep | hold | hold_early | hold_status
    status: int = 500
    code: str = "internal"
    retry_after: int | None = None
    location: str = ""
    seconds: float = 0.0
    event: threading.Event | None = None


@dataclass
class Report:
    report_id: str
    token: str
    fingerprint: str
    size: int
    sha256: str | None
    state: str = "pending"  # pending | complete | cancelled
    upload_fields: list[str] = field(default_factory=list)
    uploaded: bytes | None = None
    note: str | None = None
    complete_runs: int = 0
    key: str = ""


class FakeDiagServer:
    """`with FakeDiagServer(tls_ctx) as srv:`；`srv.base` 是 `https://127.0.0.1:<port>`。"""

    def __init__(
        self, tls, *, max_bytes: int = 10 * 1024 * 1024, notes_enabled: bool = True
    ) -> None:
        self.tls = tls
        self.max_bytes = max_bytes
        self.notes_enabled = notes_enabled
        self.api_header: str | None = API_VERSION
        self.upload_url: str | None = None  # 覆盖 init 响应里的 upload.url（SSRF 用例）
        self.extra_response_fields: dict = {}
        self.calls: list[dict] = []
        self.reports: dict[str, Report] = {}
        self.by_idem: dict[str, str] = {}
        self.forced: dict[str, deque[Forced]] = {}
        self.connections = 0
        self.lock = threading.RLock()
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    # ------------------------------------------------------------------ 脚本化
    def force(self, endpoint: str, *items: Forced, times: int = 1) -> None:
        with self.lock:
            q = self.forced.setdefault(endpoint, deque())
            for _ in range(times):
                q.extend(items)

    def fail(
        self,
        endpoint: str,
        status: int,
        code: str,
        *,
        retry_after: int | None = None,
        times: int = 1,
    ):
        self.force(endpoint, Forced("status", status, code, retry_after), times=times)

    def paths(self) -> list[str]:
        with self.lock:
            return [c["path"] for c in self.calls]

    def count(self, path: str) -> int:
        return self.paths().count(path)

    @property
    def base(self) -> str:
        return f"https://127.0.0.1:{self.port}"

    # ------------------------------------------------------------------ 生命周期
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
                outer.connections += 1
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
        for q in self.forced.values():
            for f in q:
                if f.event is not None:
                    f.event.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)

    # ------------------------------------------------------------------ 响应
    def _send(
        self,
        h,
        status: int,
        obj: dict | None,
        *,
        retry_after: int | None = None,
        raw: bytes | None = None,
    ):
        body = raw if raw is not None else (json.dumps(obj).encode() if obj is not None else b"")
        h.send_response(status)
        if self.api_header is not None:
            h.send_header("Tavotto-Diagnostics-Api", self.api_header)
        if obj is not None or raw is not None:
            h.send_header("Content-Type", "application/json")
        if retry_after is not None:
            h.send_header("Retry-After", str(retry_after))
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        if body:
            h.wfile.write(body)
        h.wfile.flush()

    def _error(self, h, status: int, code: str, retry_after: int | None = None):
        self._send(
            h,
            status,
            {"ok": False, "code": code, "error": "fixed wording"},
            retry_after=retry_after,
        )

    @staticmethod
    def _drop(h):
        h.close_connection = True
        try:
            h.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        h.connection.close()

    def _take(self, endpoint: str) -> Forced | None:
        with self.lock:
            q = self.forced.get(endpoint)
            return q.popleft() if q else None

    # ------------------------------------------------------------------ 路由
    def _handle(self, h) -> None:
        path = h.path
        endpoint = {
            "/v1/reports/init": "init",
            "/v1/reports/complete": "complete",
            "/v1/reports/cancel": "cancel",
            "/v1/local-upload": "upload",
        }.get(path)
        with self.lock:
            q = self.forced.get(endpoint or "")
            early = q.popleft() if q and q[0].kind == "hold_early" else None
        if early is not None and early.event is not None:
            early.event.wait(30)  # 读请求体之前先停住：客户端的发送会在内核缓冲写满后阻塞
        length = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(length) if length else b""
        record = {"path": path, "ctype": h.headers.get("Content-Type", ""), "len": len(body)}
        if endpoint != "upload":
            try:
                record["json"] = json.loads(body.decode("utf-8")) if body else None
            except ValueError:
                record["json"] = "<invalid>"
        with self.lock:
            self.calls.append(record)
        if endpoint is None:
            self._error(h, 404, "not_found")
            return
        forced = self._take(endpoint)
        if forced is not None:
            if forced.kind == "sleep":
                time.sleep(forced.seconds)
                forced = None
            elif forced.kind == "hold_status":
                # 先阻塞到事件、再回错误状态：请求在途时用户点了取消，请求随后以错误结束
                assert forced.event is not None
                forced.event.wait(30)
                forced = (
                    Forced("drop_before")
                    if forced.status == 0
                    else Forced("status", forced.status, forced.code, forced.retry_after)
                )
            elif forced.kind == "hold":
                assert forced.event is not None
                forced.event.wait(30)
                forced = None
        after_drop = False
        if forced is not None:
            if forced.kind == "status":
                self._error(h, forced.status, forced.code, forced.retry_after)
                return
            if forced.kind == "drop_before":
                self._drop(h)
                return
            if forced.kind == "redirect":
                h.send_response(
                    forced.status if forced.status in (301, 302, 303, 307, 308) else 307
                )
                h.send_header("Location", forced.location)
                h.send_header("Content-Length", "0")
                h.end_headers()
                return
            if forced.kind == "drop_after":
                after_drop = True
        if endpoint == "upload":
            self._upload(h, body, record, after_drop)
        else:
            self._api(h, endpoint, record, after_drop)

    def _api(self, h, endpoint: str, record: dict, after_drop: bool) -> None:
        req = record.get("json")
        if not isinstance(req, dict):
            self._error(h, 400, "bad_json")
            return
        if not h.headers.get("Content-Type", "").startswith("application/json"):
            self._error(h, 415, "bad_content_type")
            return
        status, out = getattr(self, f"_do_{endpoint}")(req)
        if after_drop:
            self._drop(h)  # 动作已执行，响应丢在路上
            return
        if out.get("ok") is False:
            self._error(h, status, out["code"], out.get("retry_after"))
        else:
            self._send(h, status, {**out, **self.extra_response_fields})

    # -- init
    def _do_init(self, req: dict) -> tuple[int, dict]:
        extra = set(req) - INIT_FIELDS
        if extra:
            return 422, {
                "ok": False,
                "code": "identity_field_forbidden"
                if extra & {"install_id", "distinct_id"}
                else "bad_request",
            }
        if not INIT_REQUIRED <= set(req):
            return 422, {"ok": False, "code": "bad_request"}
        if not isinstance(req["client_request_id"], str) or not UUID4.match(
            req["client_request_id"]
        ):
            return 422, {"ok": False, "code": "bad_request"}
        for key, allowed in ENUMS.items():
            if req[key] not in allowed:
                return 422, {"ok": False, "code": "bad_request"}
        if req["category"] not in CATEGORIES:
            return 422, {"ok": False, "code": "bad_request"}
        size, schema = req["size"], req["bundle_schema"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            return 422, {"ok": False, "code": "bad_request"}
        if isinstance(schema, bool) or not isinstance(schema, int) or not 1 <= schema <= 99:
            return 422, {"ok": False, "code": "bad_request"}
        if not re.match(r"^[0-9A-Za-z.+_-]{1,32}$", str(req["tavotto_version"])):
            return 422, {"ok": False, "code": "bad_request"}
        note = req.get("note")
        if note and not self.notes_enabled:
            return 422, {"ok": False, "code": "notes_disabled"}
        if size > self.max_bytes:
            return 413, {"ok": False, "code": "too_large"}
        fingerprint = hashlib.sha256(
            json.dumps(
                {k: v for k, v in req.items() if k != "client_request_id"}, sort_keys=True
            ).encode()
        ).hexdigest()
        with self.lock:
            rid = self.by_idem.get(req["client_request_id"])
            if rid is not None:
                rep = self.reports[rid]
                if rep.fingerprint != fingerprint:
                    return 409, {"ok": False, "code": "idempotency_conflict"}
                if rep.state == "complete":
                    return 200, {"ok": True, "report_id": rid, "state": "complete"}
                if rep.state == "cancelled":
                    return 409, {"ok": False, "code": "session_closed"}
                return 200, self._init_body(rep)
            rid = "TVD-" + "-".join(
                "".join(secrets.choice(_CROCKFORD) for _ in range(4)) for _ in range(4)
            )
            rep = Report(
                rid, secrets.token_hex(32), fingerprint, size, req.get("sha256"), note=note
            )
            self.reports[rid] = rep
            self.by_idem[req["client_request_id"]] = rid
            return 201, self._init_body(rep)

    def _init_body(self, rep: Report) -> dict:
        fields = {
            "key": "incoming/2026/10/10/" + secrets.token_hex(8) + ".zip",
            "Content-Type": "application/zip",
            "policy": "p-" + secrets.token_hex(8),
            "signature": "s-" + secrets.token_hex(8),
        }
        rep.upload_fields = list(fields)
        rep.key = fields["key"]
        return {
            "ok": True,
            "report_id": rep.report_id,
            "state": "pending",
            "complete_token": rep.token,
            "expires_at": int(time.time()) + 900,
            "upload": {
                "method": "POST",
                "url": self.upload_url or f"{self.base}/v1/local-upload",
                "fields": fields,
                "file_field": "file",
                "content_type": "application/zip",
                "max_bytes": rep.size,
                "expires_at": int(time.time()) + 900,
            },
        }

    def _auth(self, req: dict) -> Report | None:
        rid, tok = req.get("report_id"), req.get("complete_token")
        if set(req) != {"report_id", "complete_token"}:
            return None
        if (
            not isinstance(rid, str)
            or not isinstance(tok, str)
            or not REPORT_ID.match(rid)
            or not TOKEN.match(tok)
        ):
            return None
        rep = self.reports.get(rid)
        return rep if rep is not None and secrets.compare_digest(rep.token, tok) else None

    # -- complete / cancel
    def _do_complete(self, req: dict) -> tuple[int, dict]:
        with self.lock:
            rep = self._auth(req)
            if rep is None:
                return 404, {"ok": False, "code": "not_found"}
            if rep.state == "complete":
                return 200, self._complete_body(rep, replay=True)
            if rep.state == "cancelled":
                return 409, {"ok": False, "code": "cancelled"}
            if rep.uploaded is None:
                return 409, {"ok": False, "code": "upload_missing", "retry_after": 1}
            if len(rep.uploaded) != rep.size:
                return 422, {"ok": False, "code": "rejected"}
            rep.state = "complete"
            rep.complete_runs += 1
            return 200, self._complete_body(rep, replay=False)

    @staticmethod
    def _complete_body(rep: Report, replay: bool) -> dict:
        return {
            "ok": True,
            "report_id": rep.report_id,
            "state": "complete",
            "replay": replay,
            "size": rep.size,
            "sha256": hashlib.sha256(rep.uploaded or b"").hexdigest(),
        }

    def _do_cancel(self, req: dict) -> tuple[int, dict]:
        with self.lock:
            rep = self._auth(req)
            if rep is None:
                return 404, {"ok": False, "code": "not_found"}
            replay = rep.state == "cancelled"
            if rep.state == "complete":
                return 409, {"ok": False, "code": "already_complete"}
            rep.state = "cancelled"
            return 200, {
                "ok": True,
                "report_id": rep.report_id,
                "state": "cancelled",
                "replay": replay,
            }

    # -- 上传
    def _upload(self, h, body: bytes, record: dict, after_drop: bool) -> None:
        ctype = h.headers.get("Content-Type", "")
        m = re.match(r"^multipart/form-data; boundary=(.+)$", ctype)
        if not m:
            self._error(h, 415, "bad_content_type")
            return
        boundary = ("--" + m.group(1)).encode()
        parts = body.split(boundary)
        if parts[0] != b"" or parts[-1] != b"--\r\n":
            self._error(h, 400, "bad_request")
            return
        names: list[str] = []
        file_bytes: bytes | None = None
        values: dict[str, bytes] = {}
        for raw in parts[1:-1]:
            head, _, rest = raw.partition(b"\r\n\r\n")
            nm = re.search(rb'name="([^"]+)"', head)
            if not nm or not rest.endswith(b"\r\n"):
                self._error(h, 400, "bad_request")
                return
            name = nm.group(1).decode()
            names.append(name)
            if b"filename=" in head:
                file_bytes = rest[:-2]
            else:
                values[name] = rest[:-2]
        record["field_order"] = names
        record["values"] = {k: v.decode("utf-8", "replace") for k, v in values.items()}
        with self.lock:
            rep = next(
                (
                    r
                    for r in self.reports.values()
                    if r.key and r.key.encode() == values.get("key") and r.state == "pending"
                ),
                None,
            )
            ok = (
                rep is not None
                and names[:-1] == rep.upload_fields
                and names[-1] == "file"
                and file_bytes is not None
                and len(file_bytes) == rep.size
            )
            if not ok:
                record["why"] = [
                    rep is not None,
                    names,
                    rep and rep.upload_fields,
                    len(file_bytes or b""),
                    rep and rep.size,
                ]
                self._error(h, 403, "forbidden")
                return
            rep.uploaded = file_bytes
        if after_drop:
            self._drop(h)
            return
        h.send_response(204)
        h.send_header("Content-Length", "0")
        h.end_headers()
