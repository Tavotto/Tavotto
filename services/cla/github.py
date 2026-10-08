"""Real GitHub HTTPS adapter. Tests exercise this against a controlled HTTP peer."""

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .config import canonical, policy_digest


class UpstreamError(RuntimeError):
    pass


class ClosedPR(UpstreamError):
    """The PR is terminal; retire its polling job until an explicit reopen webhook."""


class CandidateGone(UpstreamError):
    """Candidate is absent from a successfully fetched live queue."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward tokens to a redirect target.
        return None


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class GitHub:
    def __init__(self, config):
        self.c = config
        self.opener = urllib.request.build_opener(NoRedirect())
        self.token = None
        self.token_until = 0

    def request(self, method, path, data=None, token=None, oauth=False):
        if not path.startswith("/") or path.startswith("//"):
            raise UpstreamError("invalid API path")
        base = self.c.oauth_base if oauth else self.c.api_base
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Tavotto-CLA",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = "Bearer " + token
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = canonical(data).encode()
        req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=15) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise UpstreamError("response too large")
                return json.loads(raw) if raw else None
        except (urllib.error.URLError, ValueError):
            # Never propagate response bodies or request URLs (OAuth secrets/codes).
            raise UpstreamError("GitHub request failed") from None

    def oauth_user(self, code, verifier):
        result = self.request(
            "POST",
            "/login/oauth/access_token",
            {
                "client_id": self.c.client_id,
                "client_secret": self.c.client_secret,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": self.c.origin + "/oauth/callback",
            },
            oauth=True,
        )
        token = result.get("access_token")
        if not isinstance(token, str) or result.get("token_type", "").lower() != "bearer":
            raise UpstreamError("OAuth exchange failed")
        user = self.request("GET", "/user", token=token)
        # Access/refresh tokens are deliberately not persisted.
        if (
            type(user.get("id")) is not int
            or user["id"] <= 0
            or not isinstance(user.get("login"), str)
            or user.get("type") != "User"
        ):
            raise UpstreamError("human GitHub identity required")
        return user

    def app_jwt(self):
        now = int(time.time())
        header = b64(canonical({"alg": "RS256", "typ": "JWT"}).encode())
        payload = b64(
            canonical({"iat": now - 60, "exp": now + 480, "iss": str(self.c.app_id)}).encode()
        )
        signing_input = (header + "." + payload).encode()
        key = serialization.load_pem_private_key(self.c.private_key, password=None)
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return signing_input.decode() + "." + b64(signature)

    def installation_token(self):
        if self.token and time.time() < self.token_until:
            return self.token
        jwt = self.app_jwt()
        app = self.request("GET", "/app", token=jwt)
        if app.get("id") != self.c.app_id or app.get("slug") != self.c.app_slug:
            raise UpstreamError("GitHub App identity mismatch")
        result = self.request(
            "POST",
            f"/app/installations/{self.c.installation_id}/access_tokens",
            {
                "repository_ids": [self.c.repository_id],
                "permissions": {
                    "contents": "read",
                    "pull_requests": "read",
                    "checks": "write",
                    **({"merge_queues": "read"} if self.c.merge_queue_enabled else {}),
                    **({"actions": "read"} if self.c.final_check_enabled else {}),
                },
            },
            token=jwt,
        )
        if not isinstance(result.get("token"), str):
            raise UpstreamError("installation token unavailable")
        self.token = result["token"]
        self.token_until = time.time() + 2400
        return self.token

    def api(self, method, path, data=None):
        return self.request(method, path, data, token=self.installation_token())

    def repo_path(self, suffix):
        return f"/repos/{self.c.repository}{suffix}"

    def snapshot(self, number, policy):
        repo = self.api("GET", self.repo_path(""))
        if (
            repo.get("id") != self.c.repository_id
            or repo.get("full_name", "").lower() != self.c.repository.lower()
        ):
            raise UpstreamError("repository identity mismatch")
        ref = urllib.parse.quote(repo["default_branch"], safe="")
        remote = self.api("GET", self.repo_path("/contents/.github/cla-policy.json?ref=" + ref))
        decoded = json.loads(base64.b64decode(remote["content"]))
        if policy_digest(decoded) != policy_digest(policy):
            raise UpstreamError("trusted policy changed; operator review required")
        pr = self.api("GET", self.repo_path(f"/pulls/{number}"))
        if pr["base"]["repo"]["id"] != self.c.repository_id or pr["number"] != number:
            raise UpstreamError("PR identity mismatch")
        if pr.get("state") != "open":
            raise ClosedPR("PR is not open")
        base, head = pr["base"]["sha"], pr["head"]["sha"]
        if not all(re.fullmatch("[0-9a-f]{40}", s) for s in (base, head)):
            raise UpstreamError("invalid SHA")
        expected = pr["commits"]
        if type(expected) is not int or expected < 1 or expected > 10000:
            raise UpstreamError("unsupported commit count")
        commits = []
        for page in range(1, (expected + 99) // 100 + 1):
            comparison = self.api(
                "GET", self.repo_path(f"/compare/{base}...{head}?per_page=100&page={page}")
            )
            if comparison.get("total_commits") != expected:
                raise UpstreamError("comparison count mismatch")
            commits.extend(comparison["commits"])
        if (
            len(commits) != expected
            or len({c["sha"] for c in commits}) != expected
            or head not in {c["sha"] for c in commits}
        ):
            raise UpstreamError("incomplete PR commits")
        people = self.contributors(pr["user"], commits)
        # Refetch after pagination: no stale author list may qualify a newer head.
        fresh = self.api("GET", self.repo_path(f"/pulls/{number}"))
        if any(fresh[k] != pr[k] for k in ("head", "base", "commits", "user", "state")):
            raise UpstreamError("PR changed during enumeration")
        return head, people

    def contributors(self, author, commits):
        people = {}

        def add(user):
            if (
                not isinstance(user, dict)
                or type(user.get("id")) is not int
                or user["id"] <= 0
                or not user.get("login")
            ):
                raise UpstreamError("unresolved author")
            people[user["id"]] = user["login"]

        def email_identity(email):
            match = re.fullmatch(
                r"([0-9]+)\+([A-Za-z0-9][A-Za-z0-9-]*)@users\.noreply\.github\.com", email, re.I
            )
            if not match:
                raise UpstreamError(
                    "co-author requires immutable GitHub ID; manual resolution needed"
                )
            user = self.api("GET", "/users/" + urllib.parse.quote(match[2], safe=""))
            if user.get("id") != int(match[1]):
                raise UpstreamError("noreply identity mismatch")
            add(user)

        add(author)
        for commit in commits:
            if commit.get("author"):
                add(commit["author"])
            else:
                email_identity(commit["commit"]["author"]["email"])
            for line in commit["commit"]["message"].splitlines():
                if re.match(r"\s*Co-authored-by\s*:", line, re.I):
                    match = re.fullmatch(
                        r"\s*Co-authored-by:\s*[^<>\r\n]+<([^<>\r\n]+)>\s*", line, re.I
                    )
                    if not match:
                        raise UpstreamError("malformed co-author trailer")
                    email_identity(match[1].strip())
        return people

    def queue_entries(self, branch):
        owner, name = self.c.repository.split("/")
        query = """query($owner:String!,$name:String!,$branch:String!,$cursor:String) {
          repository(owner:$owner,name:$name) { databaseId mergeQueue(branch:$branch) {
            entries(first:100,after:$cursor) { totalCount pageInfo { hasNextPage endCursor }
              nodes { id position baseCommit { oid } headCommit { oid }
                pullRequest { number headRefOid baseRefName } } } } } }"""
        cursor, entries, total = None, [], None
        for _ in range(100):
            result = self.api(
                "POST",
                "/graphql",
                {
                    "query": query,
                    "variables": {"owner": owner, "name": name, "branch": branch, "cursor": cursor},
                },
            )
            if result.get("errors"):
                raise UpstreamError("merge queue query failed")
            repo = result["data"]["repository"]
            if repo["databaseId"] != self.c.repository_id or not repo["mergeQueue"]:
                raise UpstreamError("merge queue identity unavailable")
            page = repo["mergeQueue"]["entries"]
            if total is not None and total != page["totalCount"]:
                raise UpstreamError("merge queue changed during pagination")
            total = page["totalCount"]
            entries.extend(page["nodes"])
            if not page["pageInfo"]["hasNextPage"]:
                if len(entries) != total or len({e["id"] for e in entries}) != total:
                    raise UpstreamError("incomplete merge queue")
                return entries
            next_cursor = page["pageInfo"]["endCursor"]
            if not next_cursor or next_cursor == cursor:
                raise UpstreamError("merge queue pagination stalled")
            cursor = next_cursor
        raise UpstreamError("merge queue pagination limit")

    def group_snapshot(self, head, base, base_ref, policy):
        if not self.c.merge_queue_enabled or base_ref != "refs/heads/main":
            raise UpstreamError("merge queue is not activated for this branch")
        entries = self.queue_entries("main")
        matches = [
            e
            for e in entries
            if (e.get("headCommit") or {}).get("oid") == head
            and (e.get("baseCommit") or {}).get("oid") == base
        ]
        if len(matches) != 1:
            raise CandidateGone("candidate/base is not uniquely present in live merge queue")
        position = matches[0]["position"]
        prefix = sorted(
            (e for e in entries if e["position"] <= position), key=lambda e: e["position"]
        )
        if [e["position"] for e in prefix] != list(range(1, position + 1)):
            raise UpstreamError("incomplete queue prefix")
        # Conservative superset: every earlier queued PR, including preceding groups,
        # must qualify. Never infer members from a PR-controlled branch name or message.
        people, members = {}, []
        for entry in prefix:
            pr = entry["pullRequest"]
            if pr["baseRefName"] != "main":
                raise UpstreamError("queue branch mismatch")
            pr_head, authors = self.snapshot(pr["number"], policy)
            if pr_head != pr["headRefOid"]:
                raise UpstreamError("queued PR head changed")
            people.update(authors)
            members.append({"pr": pr["number"], "head_sha": pr_head})
        if entries != self.queue_entries("main"):
            raise UpstreamError("queue composition changed during verification")
        return people, members

    def publish(self, head, number, conclusion, proof, run_id=None, name=None):
        payload = {
            "name": name or self.c.check_name,
            "status": "completed",
            "conclusion": conclusion,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "details_url": self.c.origin + "/",
            "output": {
                "title": "CLA qualification"
                if conclusion == "success"
                else "CLA qualification blocked",
                "summary": "Both parties must personally sign the current agreement. GitHub login is not legal identity verification.",
                "text": "tavotto-cla-v1\n" + canonical(proof),
            },
        }
        if conclusion is None:
            payload["status"] = "in_progress"
            payload.pop("conclusion")
            payload.pop("completed_at")
            payload["output"]["title"] = "Awaiting trusted CI gates and a fresh final CLA decision"
        if run_id:
            result = self.api("PATCH", self.repo_path(f"/check-runs/{run_id}"), payload)
        else:
            payload.update({"head_sha": head, "external_id": f"tavotto-cla-v1:{number}:{head}"})
            result = self.api("POST", self.repo_path("/check-runs"), payload)
        if (
            result.get("app", {}).get("id") != self.c.app_id
            or result.get("app", {}).get("slug") != self.c.app_slug
            or result.get("head_sha") != head
            or type(result.get("id")) is not int
        ):
            raise UpstreamError("check-run response identity mismatch")
        return result["id"]

    def discover_check(self, head, name=None):
        # Crash after GitHub accepted create, before local commit: adopt the latest own run.
        found = []
        for page in range(1, 101):
            result = self.api(
                "GET",
                self.repo_path(f"/commits/{head}/check-runs?filter=all&per_page=100&page={page}"),
            )
            rows = result["check_runs"]
            for row in rows:
                if (
                    row.get("name") == (name or self.c.check_name)
                    and row.get("app", {}).get("id") == self.c.app_id
                    and row.get("app", {}).get("slug") == self.c.app_slug
                ):
                    found.append(row["id"])
            if len(rows) < 100:
                return max(found) if found else None
        raise UpstreamError("check-run pagination limit")

    def trusted_ci_passed(self, head):
        """Check names/App alone are insufficient: bind each gate to its workflow run."""
        runs_by_workflow = {}
        jobs_by_attempt = {}
        checks = []
        for page in range(1, 101):
            response = self.api(
                "GET",
                self.repo_path(f"/commits/{head}/check-runs?filter=all&per_page=100&page={page}"),
            )
            checks.extend(response["check_runs"])
            if len(response["check_runs"]) < 100:
                break
        else:
            raise UpstreamError("check pagination incomplete")
        for name, identity in self.c.trusted_gates.items():
            workflow_id = identity["workflow_id"]
            if workflow_id not in runs_by_workflow:
                runs, total = [], None
                for page in range(1, 101):
                    response = self.api(
                        "GET",
                        self.repo_path(
                            f"/actions/workflows/{workflow_id}/runs?head_sha={head}&event=merge_group&per_page=100&page={page}"
                        ),
                    )
                    if total is not None and total != response["total_count"]:
                        raise UpstreamError("workflow runs changed during pagination")
                    total = response["total_count"]
                    runs.extend(response["workflow_runs"])
                    if len(runs) >= total:
                        if len(runs) != total:
                            raise UpstreamError("workflow count mismatch")
                        break
                else:
                    raise UpstreamError("workflow pagination limit")
                runs_by_workflow[workflow_id] = runs
            runs = runs_by_workflow[workflow_id]
            if not runs:
                return False
            run = max(runs, key=lambda r: (r["id"], r["run_attempt"]))
            if (
                run.get("head_sha") != head
                or run.get("event") != "merge_group"
                or run.get("workflow_id") != workflow_id
                or run.get("path") != identity["path"]
                or run.get("status") != "completed"
                or run.get("conclusion") != "success"
                or run.get("repository", {}).get("id") != self.c.repository_id
            ):
                return False
            attempt_key = (run["id"], run["run_attempt"])
            if attempt_key not in jobs_by_attempt:
                jobs, count = [], None
                for page in range(1, 101):
                    response = self.api(
                        "GET",
                        self.repo_path(
                            f"/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100&page={page}"
                        ),
                    )
                    if count is not None and count != response["total_count"]:
                        raise UpstreamError("attempt jobs changed during pagination")
                    count = response["total_count"]
                    jobs.extend(response["jobs"])
                    if len(jobs) >= count:
                        if len(jobs) != count:
                            raise UpstreamError("attempt jobs incomplete")
                        break
                else:
                    raise UpstreamError("attempt jobs pagination limit")
                jobs_by_attempt[attempt_key] = jobs
            jobs = [j for j in jobs_by_attempt[attempt_key] if j.get("name") == name]
            if len(jobs) != 1:
                return False
            job = jobs[0]
            if (
                job.get("head_sha") != head
                or job.get("run_id") != run["id"]
                or job.get("run_attempt") != run["run_attempt"]
                or job.get("status") != "completed"
                or job.get("conclusion") != "success"
            ):
                return False
            check_url = job.get("check_run_url", "")
            expected_prefix = self.c.api_base + self.repo_path("/check-runs/")
            if (
                not check_url.startswith(expected_prefix)
                or not check_url[len(expected_prefix) :].isdigit()
            ):
                return False
            current_check_id = int(check_url[len(expected_prefix) :])
            candidates = [
                c
                for c in checks
                if c.get("name") == name
                and c.get("id") == current_check_id
                and c.get("app", {}).get("id") == identity["app_id"]
                and c.get("app", {}).get("slug") == "github-actions"
                and c.get("head_sha") == head
                and c.get("check_suite", {}).get("id") == run["check_suite_id"]
            ]
            if not candidates:
                return False
            check = max(candidates, key=lambda c: c["id"])
            if check.get("status") != "completed" or check.get("conclusion") != "success":
                return False
        return bool(self.c.trusted_gates)
