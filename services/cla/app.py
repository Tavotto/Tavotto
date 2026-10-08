"""WSGI signing UI and webhook. All signatures require a party's fresh explicit POST."""

import hashlib
import hmac
import html
import json
import re
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlencode, urlsplit

from .config import canonical
from .github import b64


class HTTPError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Application:
    def __init__(self, config, store, github, policy):
        self.c, self.store, self.github, self.policy = config, store, github, policy
        self.cookie_name = "cla-test" if config.test_mode else "__Host-cla"
        self.oauth_cookie = self.cookie_name + "-oauth"

    def cookie(self, name, value, age):
        return (
            "Set-Cookie",
            f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}"
            + ("" if self.c.test_mode else "; Secure"),
        )

    @staticmethod
    def fields(raw):
        values = parse_qs(raw, keep_blank_values=True, max_num_fields=25)
        if any(len(v) != 1 for v in values.values()):
            raise HTTPError(400, "Duplicate fields rejected")
        return {k: v[0] for k, v in values.items()}

    def __call__(self, env, start_response):
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Cache-Control", "no-store"),
            ("Pragma", "no-cache"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Content-Type-Options", "nosniff"),
            (
                "Content-Security-Policy",
                "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            ),
            ("Strict-Transport-Security", "max-age=31536000"),
        ]
        status = 200
        try:
            if env.get("HTTP_HOST") != urlsplit(self.c.origin).netloc or (
                not self.c.test_mode and env.get("wsgi.url_scheme") != "https"
            ):
                raise HTTPError(400, "Invalid request origin")
            body, extra, status = self.route(env)
            headers += extra
        except HTTPError as exc:
            status, body = exc.status, html.escape(exc.message)
        except PermissionError:
            status, body = (
                403,
                "Request not authorized, expired, or already completed. Reload and review the current state.",
            )
        except KeyError:
            status, body = 404, "Not found"
        except (ValueError, UnicodeError):
            status, body = 400, "Invalid request"
        except Exception:
            # No stack trace, OAuth code, token, evidence or address in HTTP/log output.
            status, body = (
                503,
                "Service unavailable. No signature or qualification should be assumed.",
            )
        reason = {
            200: "OK",
            202: "Accepted",
            303: "See Other",
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            409: "Conflict",
            413: "Payload Too Large",
            415: "Unsupported Media Type",
            503: "Service Unavailable",
        }[status]
        raw = body.encode() if isinstance(body, str) else body
        headers.append(("Content-Length", str(len(raw))))
        start_response(f"{status} {reason}", headers)
        return [raw]

    def route(self, env):
        method, path = env["REQUEST_METHOD"], env.get("PATH_INFO", "/")
        cookies = SimpleCookie()
        cookies.load(env.get("HTTP_COOKIE", ""))

        def get_cookie(name):
            return cookies[name].value if name in cookies else ""

        if method == "POST" and path == "/webhook":
            return self.webhook(env)
        if method == "GET" and path == "/login":
            state, browser, verifier = self.store.oauth_start()
            query = urlencode(
                {
                    "client_id": self.c.client_id,
                    "redirect_uri": self.c.origin + "/oauth/callback",
                    "state": state,
                    "code_challenge": b64(hashlib.sha256(verifier.encode()).digest()),
                    "code_challenge_method": "S256",
                    "allow_signup": "false",
                }
            )
            return (
                "",
                [
                    ("Location", self.c.oauth_base + "/login/oauth/authorize?" + query),
                    self.cookie(self.oauth_cookie, browser, 300),
                ],
                303,
            )
        if method == "GET" and path == "/oauth/callback":
            query = self.fields(env.get("QUERY_STRING", ""))
            verifier = self.store.oauth_consume(
                query.get("state", ""), get_cookie(self.oauth_cookie)
            )
            user = self.github.oauth_user(query.get("code", ""), verifier)
            token = self.store.login(user["id"], user["login"])
            return (
                "",
                [
                    ("Location", "/"),
                    self.cookie(self.cookie_name, token, 900),
                    self.cookie(self.oauth_cookie, "", 0),
                ],
                303,
            )
        session = self.store.session(get_cookie(self.cookie_name))
        if not session:
            if method == "GET" and path == "/":
                return (
                    self.page(
                        "Tavotto CLA",
                        '<p>Both parties personally sign. GitHub authentication binds an account ID; it does not establish legal identity, age, ownership, or corporate authority. This service does not guarantee legal effectiveness.</p><a href="/login">Sign in with GitHub</a>',
                    ),
                    [],
                    200,
                )
            raise HTTPError(401, "Sign in again to continue")
        if method == "GET" and path == "/":
            if session["actor"] == self.c.holder_id:
                with self.store.connection() as db:
                    rows = db.execute(
                        "SELECT id,subject FROM packets WHERE digest=? ORDER BY created DESC LIMIT 200",
                        (self.policy["agreements"]["individual"]["sha256"],),
                    ).fetchall()
                listing = "".join(
                    f'<li><a href="/packet/{html.escape(r["id"])}">Review contributor GitHub ID {r["subject"]}</a></li>'
                    for r in rows
                )
                return (
                    self.page(
                        "Jiaqi Wan: review and personally countersign", "<ul>" + listing + "</ul>"
                    ),
                    [],
                    200,
                )
            p = self.store.packet(
                session["actor"], self.policy["agreements"]["individual"]["sha256"]
            )
            return "", [("Location", "/packet/" + p["id"])], 303
        match = re.fullmatch(r"/packet/([A-Za-z0-9_-]{32})(/evidence)?", path)
        if method == "GET" and match:
            packet = self.authorized_packet(session, match[1])
            if match[2]:
                data = {k: v for k, v in packet.items() if k not in ("signatures", "body")}
                data["agreement"] = packet["body"].decode()
                data["signatures"] = [
                    json.loads(self.store.cipher.decrypt(s["evidence"]))
                    for s in packet["signatures"]
                ]
                return (
                    canonical(data),
                    [
                        ("Content-Type", "application/json"),
                        ("Content-Disposition", 'attachment; filename="executed-cla.json"'),
                    ],
                    200,
                )
            return self.packet_page(session, packet), [], 200
        if method == "POST" and path in ("/sign", "/hold"):
            if env.get("HTTP_ORIGIN") != self.c.origin:
                raise HTTPError(403, "Origin check failed")
            if env.get("CONTENT_TYPE", "").split(";")[0] != "application/x-www-form-urlencoded":
                raise HTTPError(415, "Form submission required")
            data = self.fields(self.read_body(env, 32768).decode())
            packet = self.authorized_packet(session, data.get("packet", ""))
            if path == "/hold":
                self.store.hold(session, packet["id"], data.get("nonce", ""))
            else:
                if not self.c.signing_enabled:
                    raise HTTPError(409, "Signing is not activated")
                if (
                    packet["digest"] != self.policy["agreements"]["individual"]["sha256"]
                    or packet["held"]
                ):
                    raise HTTPError(409, "Agreement changed or review required")
                role = "holder" if session["actor"] == self.c.holder_id else "contributor"
                required = ("personal", "electronic", "read", "identity") + (
                    ("adult", "own_rights", "independent")
                    if role == "contributor"
                    else ("reviewed",)
                )
                if any(data.get(key) != "yes" for key in required):
                    raise HTTPError(
                        409,
                        "All attestations must be personally made. Minors, employer/corporate contributions, and uncertain identity need a separate reviewed route.",
                    )
                for key in ("name", "address", "signature"):
                    if not 1 <= len(data.get(key, "").strip()) <= 1000:
                        raise HTTPError(
                            400,
                            "Name, private postal address and personally typed signature are required",
                        )
                if data["name"].strip() != data["signature"].strip():
                    raise HTTPError(400, "Type your full name as your signature")
                if role == "holder" and data["name"].strip() != "Jiaqi Wan":
                    raise HTTPError(409, "Only Jiaqi Wan may countersign here")
                evidence = {
                    "name": data["name"].strip(),
                    "address": data["address"].strip(),
                    "signature": data["signature"].strip(),
                    "title": data.get("title", "")[:200],
                    "attestations": {k: True for k in required},
                    "agreement_version": packet["version"],
                    "consent_ui_version": "1",
                    "notice": "Personal electronic signature; GitHub identity is account authentication, not legal identity assurance.",
                }
                self.store.sign(session, packet["id"], data.get("nonce", ""), evidence)
            return "", [("Location", "/packet/" + packet["id"])], 303
        raise HTTPError(404, "Not found")

    def authorized_packet(self, session, packet_id):
        p = self.store.get_packet(packet_id)
        if session["actor"] not in (p["subject"], self.c.holder_id):
            raise PermissionError("private packet")
        return p

    @staticmethod
    def page(title, body):
        return (
            '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'
            + html.escape(title)
            + "</title><body><h1>"
            + html.escape(title)
            + "</h1>"
            + body
            + "</body></html>"
        )

    def packet_page(self, session, p):
        escape = html.escape
        roles = {s["role"] for s in p["signatures"]}
        role = "holder" if session["actor"] == self.c.holder_id else "contributor"
        status = (
            "Review hold (not qualified)"
            if p["held"]
            else (
                "Both parties signed"
                if len(roles) == 2
                else "Awaiting " + ("Jiaqi Wan" if "contributor" in roles else "contributor")
            )
        )
        text = f"<p>{status}. Contributor GitHub ID: {p['subject']}.</p><p>Version {escape(p['version'])}. SHA-256: {p['digest']}.</p><p>The following is the exact immutable agreement text. Read it in full before signing. Dates are recorded by the server in UTC.</p><pre>{escape(p['body'].decode())}</pre>"
        if role == "holder" and "contributor" in roles:
            contributor = next(s for s in p["signatures"] if s["role"] == "contributor")
            evidence = json.loads(self.store.cipher.decrypt(contributor["evidence"]))
            text += (
                "<h2>Private contributor evidence</h2><pre>"
                + escape(canonical(evidence))
                + "</pre>"
            )
        if (
            self.c.signing_enabled
            and not p["held"]
            and role not in roles
            and (role == "contributor" or "contributor" in roles)
            and p["digest"] == self.policy["agreements"]["individual"]["sha256"]
        ):
            nonce = self.store.intent(session, p["id"], role, p["digest"])
            text += f'<h2>Personally sign as {role}</h2><p>GitHub does not verify your legal identity. If identity, capacity, or ownership is uncertain, stop and arrange private review. No legal-effectiveness guarantee is made.</p><form method="post" action="/sign"><input type="hidden" name="packet" value="{p["id"]}"><input type="hidden" name="nonce" value="{nonce}">'
            for name, label in (
                ("name", "Full legal name"),
                ("address", "Private postal address"),
                ("title", "Title (if applicable)"),
                ("signature", "Type your full legal name as your electronic signature"),
            ):
                text += (
                    f'<p><label>{label} <input name="{name}" maxlength="1000" autocomplete="off"'
                    + (" required" if name != "title" else "")
                    + "></label></p>"
                )
            attestations = [
                ("personal", "I am personally making this signature; nobody is signing for me."),
                (
                    "electronic",
                    "I intend this typed name and submission to be my electronic signature on the exact agreement above.",
                ),
                ("read", "I have read and agree to the exact agreement above."),
                ("identity", "The legal name and private address entered are mine and correct."),
            ]
            if role == "contributor":
                attestations += [
                    ("adult", "I am at least 18 years old and have legal capacity."),
                    ("own_rights", "I personally own all rights needed for this contribution."),
                    (
                        "independent",
                        "No employer, corporation, other author or guardian authorization is needed for this contribution.",
                    ),
                ]
            else:
                attestations += [
                    (
                        "reviewed",
                        "I, Jiaqi Wan, have reviewed this person's identity and capacity and this exact contributor-signed agreement; there is no unresolved uncertainty.",
                    )
                ]
            for name, label in attestations:
                text += f'<p><label><input type="checkbox" name="{name}" value="yes" required>{label}</label></p>'
            text += '<button type="submit">Personally sign this agreement</button></form>'
        elif not self.c.signing_enabled:
            text += "<p>Signing is not activated.</p>"
        nonce = self.store.intent(session, p["id"], "hold", p["digest"])
        text += f'<p><a href="/packet/{p["id"]}/evidence">Download your private record</a></p><form method="post" action="/hold"><input type="hidden" name="packet" value="{p["id"]}"><input type="hidden" name="nonce" value="{nonce}"><p>A review hold blocks automated qualification; it does not purport to rescind a licence or change the legal agreement.</p><button type="submit">Place qualification on review hold</button></form>'
        return self.page("Tavotto individual CLA", text)

    @staticmethod
    def read_body(env, limit):
        length = int(env.get("CONTENT_LENGTH", "0") or 0)
        if length < 0 or length > limit:
            raise HTTPError(413, "Body too large")
        raw = env["wsgi.input"].read(length)
        if len(raw) != length:
            raise HTTPError(400, "Truncated body")
        return raw

    def webhook(self, env):
        raw = self.read_body(env, 1024 * 1024)
        expected = "sha256=" + hmac.new(self.c.webhook_secret, raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(env.get("HTTP_X_HUB_SIGNATURE_256", ""), expected):
            raise PermissionError("webhook signature")
        event = env.get("HTTP_X_GITHUB_EVENT")
        payload = json.loads(raw)
        if event == "ping":
            return "ok", [], 200
        if (
            payload.get("repository", {}).get("id") != self.c.repository_id
            or payload.get("installation", {}).get("id") != self.c.installation_id
        ):
            raise PermissionError("webhook target")
        if event == "merge_group" and payload.get("action") == "checks_requested":
            if not self.c.merge_queue_enabled:
                raise HTTPError(409, "Merge queue verification is not activated")
            group = payload.get("merge_group", {})
            head, base, base_ref = (group.get(k, "") for k in ("head_sha", "base_sha", "base_ref"))
            delivery = env.get("HTTP_X_GITHUB_DELIVERY", "")
            if (
                not all(re.fullmatch(r"[0-9a-f]{40}", s) for s in (head, base))
                or base_ref != "refs/heads/main"
                or not re.fullmatch(r"[A-Za-z0-9-]{1,100}", delivery)
            ):
                raise HTTPError(400, "Invalid merge candidate")
            self.store.group_webhook(
                delivery, hashlib.sha256(raw).hexdigest(), head, base, base_ref
            )
            return "queued", [], 202
        if event != "pull_request" or payload.get("action") not in (
            "opened",
            "reopened",
            "synchronize",
            "edited",
            "ready_for_review",
        ):
            return "ignored", [], 200
        number = payload.get("number")
        delivery = env.get("HTTP_X_GITHUB_DELIVERY", "")
        if (
            type(number) is not int
            or number <= 0
            or not re.fullmatch(r"[A-Za-z0-9-]{1,100}", delivery)
        ):
            raise HTTPError(400, "Invalid delivery")
        self.store.webhook(delivery, hashlib.sha256(raw).hexdigest(), number)
        return "queued", [], 202
