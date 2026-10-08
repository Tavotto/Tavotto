"""Executable loopback HTTP + real SQLite tests; all identities and signatures synthetic."""

import base64
import hashlib
import hmac
import http.client
import importlib.util
import json
import re
import sqlite3
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import call, patch
from urllib.parse import parse_qs, urlencode, urlsplit
from wsgiref.simple_server import WSGIRequestHandler, make_server

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from services.cla.app import Application
from services.cla.config import Config, canonical, policy_digest
from services.cla.github import GitHub, UpstreamError
from services.cla.store import Store
from services.cla.worker import Worker

ROOT = Path(__file__).resolve().parents[3]
HEAD = "a" * 40
BASE = "b" * 40


def unb64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class QuietWSGI(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class Peer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.handle_api("GET")

    def do_POST(self):
        self.handle_api("POST")

    def do_PATCH(self):
        self.handle_api("PATCH")

    def handle_api(self, method):
        state = self.server.state
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        data = json.loads(raw) if raw else None
        parsed = urlsplit(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        state["requests"].append((method, path, data))
        status, result = 200, {}
        try:
            if state.get("down"):
                self.reply(503, {"error": "unavailable"})
                return
            if path == "/login/oauth/authorize":
                # Model the OAuth provider registered-redirect check, not an open redirect.
                if query.get("redirect_uri") != [state["callback_url"]]:
                    self.reply(400, {"error": "unregistered redirect URI"})
                    return
                assert query["code_challenge_method"] == ["S256"]
                code = "synthetic-code-" + str(len(state["codes"]))
                state["codes"][code] = (state["next_user"], query["code_challenge"][0])
                location = (
                    state["callback_url"]
                    + "?"
                    + urlencode({"state": query["state"][0], "code": code})
                )
                self.send_response(303)
                self.send_header("Location", location)
                self.end_headers()
                return
            if path == "/login/oauth/access_token":
                uid, challenge = state["codes"].pop(data["code"])
                assert (
                    base64.urlsafe_b64encode(
                        hashlib.sha256(data["code_verifier"].encode()).digest()
                    )
                    .rstrip(b"=")
                    .decode()
                    == challenge
                )
                assert data["client_secret"] == "synthetic-client-secret"
                result = {"access_token": "synthetic-user-" + str(uid), "token_type": "bearer"}
            elif path == "/user":
                uid = int(self.headers["Authorization"].split("-")[-1])
                result = {
                    "id": uid,
                    "login": "holder" if uid == 1 else "person" + str(uid),
                    "type": "User",
                }
            elif path == "/app" or path.endswith("/access_tokens"):
                jwt = self.headers["Authorization"].split()[1]
                header, payload, signature = jwt.split(".")
                state["public_key"].verify(
                    unb64(signature),
                    (header + "." + payload).encode(),
                    padding.PKCS1v15(),
                    hashes.SHA256(),
                )
                claims = json.loads(unb64(payload))
                assert claims["iss"] == "99" and claims["iat"] <= time.time() < claims["exp"]
                if path == "/app":
                    result = {"id": state.get("app_id", 99), "slug": "synthetic-cla"}
                else:
                    assert data == {
                        "repository_ids": [10],
                        "permissions": {
                            "contents": "read",
                            "pull_requests": "read",
                            "checks": "write",
                            **({"merge_queues": "read"} if state.get("merge_queue") else {}),
                            **({"actions": "read"} if state.get("final_enabled") else {}),
                        },
                    }
                    result = {"token": "synthetic-installation-token"}
            else:
                assert self.headers["Authorization"] == "Bearer synthetic-installation-token"
                suffix = path.removeprefix("/repos/Tavotto/Tavotto")
                if path == "/graphql":
                    assert data["variables"]["branch"] == "main"
                    nodes = state.get("queue", [])
                    result = {
                        "data": {
                            "repository": {
                                "databaseId": 10,
                                "mergeQueue": {
                                    "entries": {
                                        "nodes": nodes,
                                        "totalCount": len(nodes),
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    }
                                },
                            }
                        }
                    }
                elif suffix == "":
                    result = {"id": 10, "full_name": "Tavotto/Tavotto", "default_branch": "main"}
                elif suffix == "/contents/.github/cla-policy.json":
                    result = {
                        "content": base64.b64encode(canonical(state["policy"]).encode()).decode()
                    }
                elif suffix == "/pulls/826":
                    result = state["pr"]
                elif suffix.startswith("/compare/"):
                    page = int(query["page"][0])
                    result = {
                        "total_commits": state.get("total", len(state["commits"])),
                        "commits": state["commits"][(page - 1) * 100 : page * 100],
                    }
                elif path.startswith("/users/"):
                    result = state.get("users", {}).get(
                        path.split("/")[-1], {"id": 7, "login": "coauthor"}
                    )
                elif suffix.startswith("/commits/") and suffix.endswith("/check-runs"):
                    result = {"check_runs": list(state["checks"].values())}
                elif suffix.startswith("/actions/runs/"):
                    run_id = int(suffix.split("/")[3])
                    attempt = int(suffix.split("/")[5])
                    jobs = state.get("attempt_jobs", {}).get((run_id, attempt), [])
                    result = {"total_count": len(jobs), "jobs": jobs}
                elif suffix.startswith("/actions/workflows/"):
                    workflow_id = int(suffix.split("/")[3])
                    runs = state.get("workflow_runs", {}).get(workflow_id, [])
                    result = {"total_count": len(runs), "workflow_runs": runs}
                elif suffix.startswith("/check-runs") and method in ("POST", "PATCH"):
                    check_id = (
                        int(suffix.split("/")[-1])
                        if method == "PATCH"
                        else len(state["checks"]) + 100
                    )
                    result = dict(state["checks"].get(check_id, {}))
                    result.update(data)
                    if data.get("status") == "in_progress":
                        result["conclusion"] = None
                    result.update({"id": check_id, "app": {"id": 99, "slug": "synthetic-cla"}})
                    state["checks"][check_id] = result
                else:
                    status, result = 404, {"error": "unknown fixture path"}
        except Exception as exc:
            state["errors"].append(type(exc).__name__ + ": " + str(exc))
            status, result = 500, {"error": "contract mismatch"}
        self.reply(status, result)

    def reply(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cla-synthetic-")
        self.peer = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        self.server = make_server("127.0.0.1", 0, lambda *_: None, handler_class=QuietWSGI)
        self.config = Config(
            root=ROOT,
            data_dir=Path(self.temp.name) / "private",
            origin=f"http://127.0.0.1:{self.server.server_port}",
            repository="Tavotto/Tavotto",
            repository_id=10,
            holder_id=1,
            app_id=99,
            app_slug="synthetic-cla",
            installation_id=55,
            client_id="synthetic-client",
            client_secret="synthetic-client-secret",
            private_key=self.key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            webhook_secret=b"synthetic-webhook-secret-only-32bytes",
            evidence_key=Fernet.generate_key(),
            exempt_ids={"erwanjun": 1, "dependabot[bot]": 2, "github-actions[bot]": 3},
            signing_enabled=True,
            test_mode=True,
            api_base=f"http://127.0.0.1:{self.peer.server_port}",
            oauth_base=f"http://127.0.0.1:{self.peer.server_port}",
        )
        self.policy = self.config.validate()
        self.store = Store(self.config)
        self.store.register(self.policy)
        self.github = GitHub(self.config)
        self.app = Application(self.config, self.store, self.github, self.policy)
        self.server.set_app(self.app)
        self.peer.state = {
            "callback_url": self.config.origin + "/oauth/callback",
            "policy": json.loads(canonical(self.policy)),
            "public_key": self.key.public_key(),
            "requests": [],
            "errors": [],
            "codes": {},
            "next_user": 4,
            "checks": {},
            "pr": {
                "number": 826,
                "state": "open",
                "user": {"id": 4, "login": "person4"},
                "base": {"sha": BASE, "repo": {"id": 10}},
                "head": {"sha": HEAD},
                "commits": 1,
            },
            "commits": [
                {
                    "sha": HEAD,
                    "author": {"id": 4, "login": "person4"},
                    "commit": {
                        "author": {"email": "4+person4@users.noreply.github.com"},
                        "message": "synthetic contribution",
                    },
                }
            ],
        }
        self.threads = [
            threading.Thread(target=s.serve_forever, daemon=True) for s in (self.peer, self.server)
        ]
        for thread in self.threads:
            thread.start()
        self.worker = Worker(self.config, self.store, self.github, self.policy)

    def tearDown(self):
        self.peer.shutdown()
        self.server.shutdown()
        self.peer.server_close()
        self.server.server_close()
        self.temp.cleanup()
        self.assertEqual(self.peer.state["errors"], [])

    def request(self, path, method="GET", data=None, cookie="", extra=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        headers = {"Cookie": cookie}
        if method == "POST":
            headers.update(
                {"Origin": self.config.origin, "Content-Type": "application/x-www-form-urlencoded"}
            )
        if extra:
            headers.update(extra)
        body = urlencode(data) if isinstance(data, dict) else data
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        status, response_headers, raw = (
            response.status,
            response.getheaders(),
            response.read().decode(),
        )
        conn.close()
        return status, dict(response_headers), raw, response_headers

    def login(self, uid):
        self.peer.state["next_user"] = uid
        status, headers, _, all_headers = self.request("/login")
        self.assertEqual(status, 303)
        cookie = next(v.split(";")[0] for k, v in all_headers if k.lower() == "set-cookie")
        auth = urlsplit(headers["Location"])
        conn = http.client.HTTPConnection(auth.hostname, auth.port)
        conn.request("GET", auth.path + "?" + auth.query)
        response = conn.getresponse()
        callback = urlsplit(response.getheader("Location"))
        response.read()
        conn.close()
        status, _, _, headers = self.request(callback.path + "?" + callback.query, cookie=cookie)
        self.assertEqual(status, 303)
        return next(
            v.split(";")[0]
            for k, v in headers
            if k.lower() == "set-cookie" and v.startswith("cla-test=")
        )

    def packet(self, cookie):
        status, headers, _, _ = self.request("/", cookie=cookie)
        self.assertEqual(status, 303)
        path = headers["Location"]
        return path, self.request(path, cookie=cookie)[2]

    def form(self, body, action):
        match = re.search(r'<form method="post" action="' + action + r'">(.*?)</form>', body, re.S)
        self.assertIsNotNone(match, body[-2000:])
        return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]+)"', match[1]))

    def sign(self, cookie, path, name):
        body = self.request(path, cookie=cookie)[2]
        data = self.form(body, "/sign")
        data.update(
            {
                "name": name,
                "signature": name,
                "address": "SYNTHETIC PRIVATE ADDRESS",
                "personal": "yes",
                "electronic": "yes",
                "read": "yes",
                "identity": "yes",
                "adult": "yes",
                "own_rights": "yes",
                "independent": "yes",
                "reviewed": "yes",
            }
        )
        return self.request("/sign", "POST", data, cookie), data

    def both_sign(self):
        contributor = self.login(4)
        path, _ = self.packet(contributor)
        result, _ = self.sign(contributor, path, "Synthetic Contributor")
        self.assertEqual(result[0], 303)
        holder = self.login(1)
        result, _ = self.sign(holder, path, "Jiaqi Wan")
        self.assertEqual(result[0], 303)
        return contributor, holder, path

    def peer_authorize(self, redirect_uri, state="synthetic-state"):
        query = urlencode(
            {
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": "synthetic-challenge",
                "code_challenge_method": "S256",
            }
        )
        conn = http.client.HTTPConnection("127.0.0.1", self.peer.server_port)
        conn.request("GET", "/login/oauth/authorize?" + query)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders())
        response.read()
        conn.close()
        return result

    def test_authorization_peer_rejects_crlf_and_external_redirects(self):
        callback = self.config.origin + "/oauth/callback"
        for invalid in (
            callback + "\r\nX-Injected-Probe: confirmed",
            "https://outside.example/callback",
        ):
            with self.subTest(redirect=invalid):
                status, headers = self.peer_authorize(invalid)
                self.assertEqual(status, 400)
                self.assertNotIn("Location", headers)
                self.assertNotIn("X-Injected-Probe", headers)
        self.assertEqual(self.peer.state["codes"], {})

    def test_authorization_peer_encodes_state_with_registered_callback(self):
        callback = self.config.origin + "/oauth/callback"
        state = "synthetic\r\nX-Injected-Probe: state"
        status, headers = self.peer_authorize(callback, state)
        self.assertEqual(status, 303)
        self.assertNotIn("X-Injected-Probe", headers)
        self.assertNotIn("\r", headers["Location"])
        self.assertNotIn("\n", headers["Location"])
        self.assertEqual(headers["Location"].split("?", 1)[0], callback)
        self.assertEqual(parse_qs(urlsplit(headers["Location"]).query)["state"], [state])

    def test_pkce_s256_uses_32_random_bytes_and_matches_rfc7636_vector(self):
        # Public interoperability vector from RFC 7636 Appendix B, not a credential.
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        with patch(
            "services.cla.store.secrets.token_urlsafe",
            side_effect=["synthetic-state", "synthetic-browser", verifier],
        ) as random_token:
            status, headers, _, _ = self.request("/login")
        self.assertEqual(status, 303)
        self.assertEqual(random_token.call_args_list, [call(32), call(32), call(32)])
        query = parse_qs(urlsplit(headers["Location"]).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["code_challenge"], ["E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"])
        self.assertNotIn(verifier, headers["Location"])
        self.assertEqual(len(unb64(self.store.oauth_start()[2])), 32)

    def test_real_http_oauth_double_signature_and_check(self):
        cookie = self.login(4)
        path, page = self.packet(cookie)
        self.assertIn(self.policy["agreements"]["individual"]["sha256"], page)
        self.assertEqual(self.worker.refresh(826), "failure")
        self.assertEqual(self.sign(cookie, path, "Synthetic Contributor")[0][0], 303)
        self.assertEqual(self.worker.refresh(826), "failure")
        self.assertEqual(self.sign(self.login(1), path, "Jiaqi Wan")[0][0], 303)
        self.assertEqual(self.worker.refresh(826), "success")
        check = next(iter(self.peer.state["checks"].values()))
        self.assertEqual(check["head_sha"], HEAD)
        self.assertEqual(len(self.peer.state["checks"]), 1)
        outbound = canonical(self.peer.state["requests"])
        self.assertNotIn("SYNTHETIC PRIVATE ADDRESS", outbound)
        self.assertNotIn("Synthetic Contributor", outbound)
        with self.store.connection() as db:
            encrypted = db.execute("SELECT evidence FROM signatures LIMIT 1").fetchone()[0]
        self.assertNotIn(b"SYNTHETIC", encrypted)
        self.assertTrue(self.store.qualified(4, self.policy["agreements"]["individual"]["sha256"]))
        restarted = Store(self.config)
        self.assertTrue(restarted.qualified(4, self.policy["agreements"]["individual"]["sha256"]))

    def test_private_record_unauthorized_account(self):
        contributor, _, path = self.both_sign()
        self.assertEqual(self.request(path + "/evidence", cookie=self.login(8))[0], 403)
        response = self.request(path + "/evidence", cookie=contributor)
        self.assertEqual(response[0], 200)
        self.assertIn("SYNTHETIC PRIVATE ADDRESS", response[2])
        self.assertEqual(response[1]["Cache-Control"], "no-store")

    def test_hold_invalidates_and_queues_update_without_deleting_evidence(self):
        contributor, _, path = self.both_sign()
        self.assertEqual(self.worker.refresh(826), "success")
        form = self.form(self.request(path, cookie=contributor)[2], "/hold")
        self.assertEqual(self.request("/hold", "POST", form, contributor)[0], 303)
        self.assertFalse(self.store.qualified(4, self.policy["agreements"]["individual"]["sha256"]))
        self.assertTrue(self.worker.tick())
        self.assertEqual(next(iter(self.peer.state["checks"].values()))["conclusion"], "failure")
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM signatures").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM audit").fetchone()[0], 3)

    def test_sql_evidence_is_immutable(self):
        self.both_sign()
        with self.store.connection() as db:
            for sql in (
                "UPDATE signatures SET actor=55",
                "DELETE FROM signatures",
                "UPDATE packets SET subject=55",
                "DELETE FROM agreements",
                "UPDATE audit SET event='fake'",
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)

    def test_missing_attestations_blocks_minor_corporate_and_uncertain_identity(self):
        cookie = self.login(4)
        path, page = self.packet(cookie)
        form = self.form(page, "/sign")
        form.update(
            {"name": "Synthetic Person", "signature": "Synthetic Person", "address": "synthetic"}
        )
        self.assertEqual(self.request("/sign", "POST", form, cookie)[0], 409)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM signatures").fetchone()[0], 0)

    def test_replayed_consent_and_wrong_origin_are_rejected(self):
        cookie = self.login(4)
        path, _ = self.packet(cookie)
        result, form = self.sign(cookie, path, "Synthetic Person")
        self.assertEqual(result[0], 303)
        self.assertEqual(self.request("/sign", "POST", form, cookie)[0], 403)
        self.assertEqual(
            self.request("/sign", "POST", form, cookie, {"Origin": "https://evil.example"})[0], 403
        )

    def test_nonce_bound_to_session_and_packet(self):
        cookie = self.login(4)
        path, page = self.packet(cookie)
        form = self.form(page, "/hold")
        self.assertEqual(self.request("/hold", "POST", form, self.login(4))[0], 403)
        other_path, _ = self.packet(self.login(5))
        form["packet"] = other_path.split("/")[-1]
        self.assertEqual(self.request("/hold", "POST", form, cookie)[0], 403)

    def test_expired_session_and_intent(self):
        cookie = self.login(4)
        path, page = self.packet(cookie)
        form = self.form(page, "/hold")
        with self.store.connection() as db:
            db.execute("UPDATE intents SET expires=0")
        self.assertEqual(self.request("/hold", "POST", form, cookie)[0], 403)
        with self.store.connection() as db:
            db.execute("UPDATE sessions SET expires=0")
        self.assertEqual(self.request(path, cookie=cookie)[0], 401)

    def test_oauth_state_cookie_binding_and_replay(self):
        status, headers, _, all_headers = self.request("/login")
        query = parse_qs(urlsplit(headers["Location"]).query)
        state = query["state"][0]
        self.assertEqual(
            self.request("/oauth/callback?" + urlencode({"state": state, "code": "bad"}))[0], 403
        )
        browser = next(v.split(";")[0] for k, v in all_headers if k.lower() == "set-cookie").split(
            "=", 1
        )[1]
        self.store.oauth_consume(state, browser)
        with self.assertRaises(PermissionError):
            self.store.oauth_consume(state, browser)

    def test_holder_cannot_sign_first_or_be_impersonated(self):
        contributor = self.login(4)
        path, _ = self.packet(contributor)
        holder = self.login(1)
        self.assertNotIn('action="/sign"', self.request(path, cookie=holder)[2])
        self.sign(contributor, path, "Synthetic Contributor")
        self.assertEqual(self.sign(holder, path, "Some Other Person")[0][0], 409)
        self.assertEqual(self.request(path, cookie=self.login(9))[0], 403)

    def webhook(self, payload=None, delivery="synthetic-delivery", signature=True):
        payload = payload or {
            "repository": {"id": 10},
            "installation": {"id": 55},
            "number": 826,
            "action": "opened",
        }
        raw = canonical(payload).encode()
        value = (
            "sha256=" + hmac.new(self.config.webhook_secret, raw, hashlib.sha256).hexdigest()
            if signature
            else "sha256=bad"
        )
        return self.request(
            "/webhook",
            "POST",
            raw,
            extra={
                "X-Hub-Signature-256": value,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": delivery,
                "Content-Type": "application/json",
            },
        )

    def test_webhook_hmac_dedup_and_repository_binding(self):
        self.assertEqual(self.webhook(signature=False)[0], 403)
        self.assertEqual(self.webhook()[0], 202)
        self.assertEqual(self.webhook()[0], 202)
        wrong = {
            "repository": {"id": 11},
            "installation": {"id": 55},
            "number": 826,
            "action": "opened",
        }
        self.assertEqual(self.webhook(wrong)[0], 403)
        changed = {
            "repository": {"id": 10},
            "installation": {"id": 55},
            "number": 826,
            "action": "edited",
        }
        self.assertEqual(self.webhook(changed)[0], 403)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM deliveries").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)

    def test_every_author_and_coauthor_must_qualify(self):
        self.both_sign()
        self.peer.state["commits"][0]["commit"]["message"] += (
            "\nCo-authored-by: Synthetic <7+coauthor@users.noreply.github.com>"
        )
        self.assertEqual(self.worker.refresh(826), "failure")
        self.peer.state["commits"][0]["commit"]["message"] = (
            "Co-authored-by: Synthetic <unbound@example.test>"
        )
        with self.assertRaises(UpstreamError):
            self.worker.refresh(826)
        self.assertEqual(next(iter(self.peer.state["checks"].values()))["conclusion"], "failure")

    def test_noreply_id_mismatch_and_malformed_trailer(self):
        for trailer in (
            "Co-authored-by: S <8+coauthor@users.noreply.github.com>",
            "Co-authored-by: broken",
            "Co-authored-by: S <coauthor@users.noreply.github.com>",
        ):
            self.peer.state["commits"][0]["commit"]["message"] = trailer
            with self.assertRaises(UpstreamError):
                self.github.snapshot(826, self.policy)

    def test_pagination_and_count_validation(self):
        self.peer.state["commits"] = [
            {
                "sha": f"{n:040x}",
                "author": {"id": 4, "login": "person4"},
                "commit": {"message": "synthetic"},
            }
            for n in range(100)
        ] + self.peer.state["commits"]
        self.peer.state["pr"]["commits"] = 101
        head, people = self.github.snapshot(826, self.policy)
        self.assertEqual((head, people), (HEAD, {4: "person4"}))
        self.peer.state["total"] = 100
        with self.assertRaises(UpstreamError):
            self.github.snapshot(826, self.policy)

    def test_policy_drift_and_app_impersonation_fail_closed(self):
        self.peer.state["policy"]["exemptions"].append(
            {"login": "outsider", "kind": "bot", "reason": "untrusted"}
        )
        with self.assertRaises(UpstreamError):
            self.github.snapshot(826, self.policy)
        self.github.token = None
        self.peer.state["app_id"] = 15368
        with self.assertRaises(UpstreamError):
            self.github.installation_token()

    def test_outage_is_durable_retry_and_never_new_success(self):
        self.webhook()
        self.peer.state["down"] = True
        self.assertTrue(self.worker.tick())
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM jobs").fetchone()
            self.assertEqual(row["attempts"], 1)
            db.execute("UPDATE jobs SET due=0")
        self.assertEqual(self.peer.state["checks"], {})
        self.peer.state["down"] = False
        self.assertTrue(self.worker.tick())
        self.assertEqual(next(iter(self.peer.state["checks"].values()))["conclusion"], "failure")

    def test_crash_after_check_create_adopts_existing_run(self):
        self.worker.refresh(826)
        with self.store.connection() as db:
            db.execute("DELETE FROM checks")
        self.worker.refresh(826)
        self.assertEqual(len(self.peer.state["checks"]), 1)

    def test_wrong_encryption_key_cannot_qualify(self):
        self.both_sign()
        wrong = Store(replace(self.config, evidence_key=Fernet.generate_key()))
        with self.assertRaises(Exception):
            wrong.qualified(4, self.policy["agreements"]["individual"]["sha256"])
        worker = Worker(self.config, wrong, self.github, self.policy)
        self.worker.refresh(826)
        with self.assertRaises(UpstreamError):
            worker.refresh(826)
        self.assertEqual(next(iter(self.peer.state["checks"].values()))["conclusion"], "failure")

    def test_disabled_signing_and_production_config_guards(self):
        self.app.c = replace(self.config, signing_enabled=False)
        cookie = self.login(4)
        path, page = self.packet(cookie)
        self.assertNotIn('action="/sign"', page)
        for config in (
            replace(self.config, test_mode=False),
            replace(self.config, data_dir=ROOT / "private"),
            replace(self.config, app_id=15368),
            replace(self.config, holder_id=8),
        ):
            with self.assertRaises(ValueError):
                config.validate()

    def test_closed_pr_is_retired_and_reopen_enqueues(self):
        self.webhook()
        self.worker.tick()
        self.peer.state["pr"]["state"] = "closed"
        with self.store.connection() as db:
            db.execute("UPDATE prs SET checked=0")
        self.worker.tick()
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM prs").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)
        self.peer.state["pr"]["state"] = "open"
        self.webhook(delivery="synthetic-reopen")
        self.worker.tick()
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM prs").fetchone()[0], 1)

    def activate_queue_fixture(self):
        config = replace(self.config, merge_queue_enabled=True)
        self.github.c = config
        self.app.c = config
        self.worker.c = config
        self.peer.state["merge_queue"] = True
        self.peer.state["queue"] = [
            {
                "id": "QE1",
                "position": 1,
                "baseCommit": {"oid": BASE},
                "headCommit": {"oid": "c" * 40},
                "pullRequest": {"number": 826, "headRefOid": HEAD, "baseRefName": "main"},
            }
        ]

    def group_event(self, head="c" * 40, base=BASE, delivery="group-delivery"):
        payload = {
            "repository": {"id": 10},
            "installation": {"id": 55},
            "action": "checks_requested",
            "merge_group": {"head_sha": head, "base_sha": base, "base_ref": "refs/heads/main"},
        }
        raw = canonical(payload).encode()
        signature = (
            "sha256=" + hmac.new(self.config.webhook_secret, raw, hashlib.sha256).hexdigest()
        )
        return self.request(
            "/webhook",
            "POST",
            raw,
            extra={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "merge_group",
                "X-GitHub-Delivery": delivery,
            },
        )

    def test_merge_candidate_http_webhook_fresh_proof_and_invalidation(self):
        contributor, _, path = self.both_sign()
        self.activate_queue_fixture()
        self.assertEqual(self.group_event()[0], 202)
        self.assertEqual(self.group_event()[0], 202)
        self.assertTrue(self.worker.tick_group())
        run = next(iter(self.peer.state["checks"].values()))
        self.assertEqual(run["conclusion"], "success")
        self.assertEqual(run["head_sha"], "c" * 40)
        proof = json.loads(run["output"]["text"].split("\n")[1])
        self.assertEqual(proof["base_sha"], BASE)
        self.assertEqual(proof["members"], [{"pr": 826, "head_sha": HEAD}])
        form = self.form(self.request(path, cookie=contributor)[2], "/hold")
        self.request("/hold", "POST", form, contributor)
        self.assertTrue(self.worker.tick_group())
        self.assertEqual(next(iter(self.peer.state["checks"].values()))["conclusion"], "failure")

    def test_merge_candidate_wrong_base_rebind_and_missing_members_fail(self):
        self.activate_queue_fixture()
        self.assertEqual(self.group_event()[0], 202)
        self.assertEqual(self.group_event(base="d" * 40, delivery="different-base")[0], 403)
        self.peer.state["queue"][0]["position"] = 2
        self.worker.tick_group()
        self.assertEqual(next(iter(self.peer.state["checks"].values()))["conclusion"], "failure")

    def test_merge_candidate_disappeared_is_retired_after_grace(self):
        self.activate_queue_fixture()
        self.group_event()
        self.peer.state["queue"] = []
        with self.store.connection() as db:
            db.execute("UPDATE groups SET received=0")
        self.worker.tick_group()
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT active FROM groups").fetchone()[0], 0)
        self.assertFalse(self.worker.tick_group())

    def test_retired_candidate_new_delivery_reactivates_but_replay_does_not(self):
        self.activate_queue_fixture()
        self.group_event()
        with self.store.connection() as db:
            db.execute("UPDATE groups SET active=0,due=0,received=0,attempts=5")
        self.assertEqual(self.group_event()[0], 202)
        with self.store.connection() as db:
            old = db.execute("SELECT * FROM groups").fetchone()
            self.assertEqual((old["active"], old["due"], old["received"]), (0, 0, 0))
        self.assertEqual(self.group_event(delivery="new-live-candidate-delivery")[0], 202)
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM groups").fetchone()
            self.assertEqual((row["active"], row["attempts"]), (1, 0))
            self.assertGreater(row["received"], 0)
        self.worker.tick_group()
        self.assertEqual(next(iter(self.peer.state["checks"].values()))["conclusion"], "failure")

    def final_fixture(self):
        self.activate_queue_fixture()
        gates = {
            "CI fast gate": {
                "workflow_id": 401,
                "path": ".github/workflows/ci.yml",
                "app_id": 15368,
            },
            "CI integration gate": {
                "workflow_id": 401,
                "path": ".github/workflows/ci.yml",
                "app_id": 15368,
            },
            "CodeQL gate": {
                "workflow_id": 402,
                "path": ".github/workflows/codeql.yml",
                "app_id": 15368,
            },
        }
        config = replace(self.worker.c, final_check_enabled=True, trusted_gates=gates)
        config.validate()
        self.worker.c = config
        self.github.c = config
        self.github.token = None
        self.peer.state["final_enabled"] = True
        self.group_event()
        with self.store.connection() as db:
            return dict(db.execute("SELECT * FROM groups").fetchone())

    def complete_ci(self):
        head = "c" * 40
        self.peer.state["workflow_runs"] = {}
        self.peer.state["attempt_jobs"] = {}
        for workflow in (401, 402):
            self.peer.state["workflow_runs"][workflow] = [
                {
                    "id": workflow + 100,
                    "workflow_id": workflow,
                    "run_attempt": 1,
                    "head_sha": head,
                    "event": "merge_group",
                    "path": ".github/workflows/" + ("ci.yml" if workflow == 401 else "codeql.yml"),
                    "status": "completed",
                    "conclusion": "success",
                    "check_suite_id": workflow + 200,
                    "repository": {"id": 10},
                }
            ]
        for index, (name, workflow) in enumerate(
            (("CI fast gate", 401), ("CI integration gate", 401), ("CodeQL gate", 402))
        ):
            self.peer.state["checks"][800 + index] = {
                "id": 800 + index,
                "name": name,
                "head_sha": head,
                "app": {"id": 15368, "slug": "github-actions"},
                "check_suite": {"id": workflow + 200},
                "status": "completed",
                "conclusion": "success",
            }

            self.peer.state["attempt_jobs"].setdefault((workflow + 100, 1), []).append(
                {
                    "name": name,
                    "run_id": workflow + 100,
                    "run_attempt": 1,
                    "head_sha": head,
                    "status": "completed",
                    "conclusion": "success",
                    "check_run_url": self.config.api_base
                    + f"/repos/Tavotto/Tavotto/check-runs/{800 + index}",
                }
            )

    def test_final_decision_waits_for_ci_then_rechecks_current_signatures(self):
        contributor, _, path = self.both_sign()
        group = self.final_fixture()
        self.assertEqual(self.worker.refresh_group(group), "success")
        self.assertEqual(self.worker.final_decision(group), "pending")
        self.complete_ci()
        self.assertEqual(self.worker.final_decision(group), "success")
        form = self.form(self.request(path, cookie=contributor)[2], "/hold")
        self.request("/hold", "POST", form, contributor)
        self.assertEqual(self.worker.final_decision(group), "failure")

    def test_final_decision_rejects_spoofed_ci_wrong_sha_and_latest_failure(self):
        self.both_sign()
        group = self.final_fixture()
        for field, value in (
            ("head_sha", HEAD),
            ("app", {"id": 999, "slug": "github-actions"}),
            ("check_suite", {"id": 999}),
            ("conclusion", "failure"),
        ):
            self.complete_ci()
            self.peer.state["checks"][800][field] = value
            self.assertEqual(self.worker.final_decision(group), "pending", field)
        self.complete_ci()
        self.peer.state["workflow_runs"][401][0]["workflow_id"] = 999
        self.assertEqual(self.worker.final_decision(group), "pending")
        self.complete_ci()
        self.peer.state["workflow_runs"][401].append(
            dict(self.peer.state["workflow_runs"][401][0], id=1000, conclusion="failure")
        )
        self.assertEqual(self.worker.final_decision(group), "pending")

    def test_final_rejects_green_check_from_older_run_attempt(self):
        self.both_sign()
        group = self.final_fixture()
        self.complete_ci()
        self.peer.state["workflow_runs"][401][0]["run_attempt"] = 2
        self.assertEqual(self.worker.final_decision(group), "pending")

    def test_final_success_cleared_before_policy_failure(self):
        self.both_sign()
        group = self.final_fixture()
        self.complete_ci()
        self.assertEqual(self.worker.final_decision(group), "success")
        self.peer.state["policy"]["agreements"]["individual"]["sha256"] = "0" * 64
        self.assertTrue(self.worker.tick_group())
        final = [
            r
            for r in self.peer.state["checks"].values()
            if r["name"] == self.worker.c.final_check_name
        ]
        self.assertEqual(len(final), 1)
        self.assertNotEqual(final[0].get("conclusion"), "success")

    def test_closed_response_does_not_delete_concurrent_reopen_job(self):
        self.worker.refresh(826)
        self.peer.state["pr"]["state"] = "closed"
        with self.store.transaction() as db:
            self.store.enqueue(db, 826)
        self.assertEqual(self.worker.refresh(826), "closed")
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE pr=826").fetchone()[0], 1)

    def test_ci_consumer_reads_real_http_candidate_proof(self):
        self.both_sign()
        self.activate_queue_fixture()
        self.group_event()
        self.worker.tick_group()

        def load(name):
            spec = importlib.util.spec_from_file_location(
                name, ROOT / "scripts/ci" / (name + ".py")
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        consumer, gate = load("cla_service_verify"), load("cla_gate")
        policy = json.loads(canonical(self.policy))
        policy["provider"] = {
            "configured": True,
            "protocol": "tavotto-cla-v1",
            "name": "synthetic",
            "check_name": self.config.check_name,
            "app_id": 99,
            "app_slug": "synthetic-cla",
        }
        context = {
            "kind": "merge_group",
            "head_sha": "c" * 40,
            "base_sha": BASE,
            "repository_id": 10,
            "not_before": int(time.time()) - 1,
        }
        result = consumer.verify(
            gate,
            policy,
            context,
            lambda: consumer.fetch_checks(
                "Tavotto/Tavotto", "c" * 40, "synthetic-installation-token", self.config.api_base
            ),
            wait_seconds=5,
        )
        self.assertEqual(result["status"], "success")

    def test_final_decision_rejects_changed_queue_after_initial_success(self):
        self.both_sign()
        group = self.final_fixture()
        self.assertEqual(self.worker.refresh_group(group), "success")
        self.complete_ci()
        self.peer.state["queue"][0]["pullRequest"]["headRefOid"] = "d" * 40
        self.assertEqual(self.worker.final_decision(group), "failure")


class GateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("cla_gate", ROOT / "scripts/ci/cla_gate.py")
        cls.gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.gate)

    def setUp(self):
        self.policy = json.loads((ROOT / ".github/cla-policy.json").read_text())
        self.policy["provider"] = {
            "configured": True,
            "name": "Synthetic",
            "check_name": "Tavotto CLA",
            "app_id": 99,
            "app_slug": "synthetic-cla",
            "protocol": "tavotto-cla-v1",
        }
        self.context = {
            "repository_id": 10,
            "pr": 826,
            "head_sha": HEAD,
            "not_before": 100,
            "now": 110,
        }
        self.proof = {
            "protocol": "tavotto-cla-v1",
            "repository_id": 10,
            "pr": 826,
            "head_sha": HEAD,
            "policy_sha256": policy_digest(self.policy),
            "issued_at": 105,
            "expires_at": 225,
            "revision": 2,
        }
        self.run = {
            "id": 100,
            "name": "Tavotto CLA",
            "app": {"id": 99, "slug": "synthetic-cla"},
            "head_sha": HEAD,
            "status": "completed",
            "conclusion": "success",
        }

    def verdict(self, proof=None, runs=None, context=None):
        self.run["output"] = {"text": "tavotto-cla-v1\n" + canonical(proof or self.proof)}
        return self.gate.signed_logins(runs or [self.run], self.policy, context or self.context)[0]

    def test_fresh_proof_and_latest_failure(self):
        self.assertEqual(self.verdict(), {"*"})
        later = dict(self.run, id=101, conclusion="failure")
        self.assertEqual(self.verdict(runs=[self.run, later]), set())
        self.assertEqual(self.verdict(runs=[later, self.run]), set())

    def test_stale_wrong_head_policy_pr_and_repository(self):
        for key, bad in (
            ("issued_at", 99),
            ("expires_at", 109),
            ("expires_at", 226),
            ("head_sha", BASE),
            ("pr", 827),
            ("repository_id", 11),
            ("policy_sha256", "0" * 64),
            ("revision", -1),
        ):
            with self.subTest(key=key, bad=bad):
                self.assertEqual(self.verdict(proof=dict(self.proof, **{key: bad})), set())

    def test_missing_context_and_merge_group_block_activation(self):
        self.assertEqual(self.gate.signed_logins([self.run], self.policy)[0], set())
        result = self.gate.decide("merge_group", self.policy, [], [])
        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["reason"], "fresh_merge_verification_required")

    def test_exempt_login_cannot_bypass_authoritative_service(self):
        contributor = {"login": "erwanjun", "sources": ["pr_author", "commit_author"]}
        for checks in ([], [dict(self.run, conclusion="failure")]):
            result = self.gate.decide(
                "pull_request", self.policy, checks, [contributor], context=self.context
            )
            self.assertEqual(result["status"], "failure")

    def test_merge_proof_requires_exact_candidate_base_and_freshness(self):
        proof = dict(
            self.proof, kind="merge_group", base_sha=BASE, members=[{"pr": 826, "head_sha": HEAD}]
        )
        context = dict(self.context, kind="merge_group", base_sha=BASE)
        self.assertEqual(self.verdict(proof=proof, context=context), {"*"})
        result = self.gate.decide("merge_group", self.policy, [self.run], [], context=context)
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.verdict(proof=proof, context=dict(context, base_sha="d" * 40)), set())
        self.assertEqual(self.verdict(proof=proof, context=dict(context, not_before=106)), set())
        self.assertEqual(self.verdict(proof=proof), set())

    def test_ci_consumer_samples_time_after_network_and_respects_deadline(self):
        spec = importlib.util.spec_from_file_location(
            "consumer", ROOT / "scripts/ci/cla_service_verify.py"
        )
        consumer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(consumer)
        self.verdict()
        now = [110]

        def delayed_fetch():
            now[0] = 226
            return [self.run]

        result = consumer.verify(
            self.gate,
            self.policy,
            dict(self.context),
            delayed_fetch,
            wait_seconds=10,
            clock=lambda: now[0],
            sleep=lambda _: None,
        )
        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["reason"], "verification_deadline_exceeded")

    def test_repository_policy_stays_unconfigured(self):
        actual = json.loads((ROOT / ".github/cla-policy.json").read_text())
        self.assertIs(actual["provider"]["configured"], False)


if __name__ == "__main__":
    unittest.main()
