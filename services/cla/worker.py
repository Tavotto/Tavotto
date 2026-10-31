"""Durable retry worker. Run one instance; never execute PR code or publish private evidence."""

import fcntl
import time

from .config import policy_digest
from .github import CandidateGone, ClosedPR, UpstreamError


class Worker:
    def __init__(self, config, store, github, policy):
        self.c, self.store, self.github, self.policy = config, store, github, policy

    def refresh(self, number):
        head = None
        failure = False
        try:
            head, people = self.github.snapshot(number, self.policy)
            with self.store.transaction() as db:
                db.execute(
                    "INSERT INTO prs(number,sha) VALUES (?,?) ON CONFLICT(number) DO UPDATE SET sha=excluded.sha",
                    (number, head),
                )
                revision = db.execute("SELECT value FROM state WHERE key='revision'").fetchone()[0]
            exemption_ids = set(self.c.exempt_ids.values())
            conclusion = (
                "success"
                if people
                and all(
                    uid in exemption_ids
                    or self.store.qualified(uid, self.policy["agreements"]["individual"]["sha256"])
                    for uid in people
                )
                else "failure"
            )
        except ClosedPR:
            with self.store.transaction() as db:
                db.execute("DELETE FROM prs WHERE number=?", (number,))
                # A reopen webhook may already have queued new work. Preserve it.
            return "closed"
        except Exception:
            failure = True
            conclusion, revision = "failure", -1
            with self.store.connection() as db:
                row = db.execute("SELECT sha FROM prs WHERE number=?", (number,)).fetchone()
                if row:
                    head = row[0]
            if not head:
                raise UpstreamError(
                    "cannot establish PR head; no success may be published"
                ) from None
        with self.store.connection() as db:
            row = db.execute("SELECT run_id FROM checks WHERE sha=?", (head,)).fetchone()
        run_id = row[0] if row else self.github.discover_check(head)
        now = int(time.time())
        proof = {
            "protocol": "tavotto-cla-v1",
            "repository_id": self.c.repository_id,
            "pr": number,
            "head_sha": head,
            "policy_sha256": policy_digest(self.policy),
            "issued_at": now,
            "expires_at": now + 120,
            "revision": revision,
        }
        # Serialize publication with signature/hold changes. No verdict can overtake an
        # invalidation committed while evidence was checked. HTTP timeout is bounded.
        with self.store.transaction() as db:
            current = db.execute("SELECT value FROM state WHERE key='revision'").fetchone()[0]
            if current != revision:
                conclusion = "failure"
            published = self.github.publish(head, number, conclusion, proof, run_id)
            db.execute(
                "INSERT INTO checks VALUES (?,?) ON CONFLICT(sha) DO UPDATE SET run_id=excluded.run_id",
                (head, published),
            )
            db.execute("UPDATE prs SET checked=? WHERE number=?", (now, number))
        if failure:
            raise UpstreamError("qualification failed; blocking check published, retry pending")
        return conclusion

    def refresh_group(self, group):
        head, base = group["head"], group["base"]
        with self.store.connection() as db:
            revision = db.execute("SELECT value FROM state WHERE key='revision'").fetchone()[0]
            row = db.execute("SELECT run_id FROM checks WHERE sha=?", (head,)).fetchone()
        failed, retired, members = False, False, []
        try:
            people, members = self.github.group_snapshot(head, base, group["base_ref"], self.policy)
            conclusion = (
                "success"
                if people
                and all(
                    uid in self.c.exempt_ids.values()
                    or self.store.qualified(uid, self.policy["agreements"]["individual"]["sha256"])
                    for uid in people
                )
                else "failure"
            )
        except CandidateGone:
            failed, conclusion = True, "failure"
            retired = int(time.time()) - group["received"] > 600
        except Exception:
            failed, conclusion = True, "failure"
        now = int(time.time())
        proof = {
            "protocol": "tavotto-cla-v1",
            "kind": "merge_group",
            "repository_id": self.c.repository_id,
            "head_sha": head,
            "base_sha": base,
            "policy_sha256": policy_digest(self.policy),
            "issued_at": now,
            "expires_at": now + 120,
            "revision": revision,
            "members": members,
        }
        run_id = row[0] if row else self.github.discover_check(head)
        with self.store.transaction() as db:
            if db.execute("SELECT value FROM state WHERE key='revision'").fetchone()[0] != revision:
                conclusion = "failure"
            published = self.github.publish(head, "merge-group", conclusion, proof, run_id)
            db.execute(
                "INSERT INTO checks VALUES (?,?) ON CONFLICT(sha) DO UPDATE SET run_id=excluded.run_id",
                (head, published),
            )
        if retired:
            with self.store.transaction() as db:
                db.execute("UPDATE groups SET active=0 WHERE head=?", (head,))
            return "retired"
        if failed:
            raise UpstreamError("candidate cannot be fully verified; blocking verdict published")
        return conclusion

    def pending_final(self, group):
        if not self.c.final_check_enabled:
            return None
        head = group["head"]
        with self.store.connection() as db:
            row = db.execute("SELECT run_id FROM final_checks WHERE sha=?", (head,)).fetchone()
        run_id = row[0] if row else self.github.discover_check(head, self.c.final_check_name)
        # Always remove a previous final success before reevaluating prerequisites.
        pending = {"protocol": "tavotto-cla-v1", "kind": "final_merge_decision", "head_sha": head}
        run_id = self.github.publish(head, "final", None, pending, run_id, self.c.final_check_name)
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO final_checks VALUES (?,?) ON CONFLICT(sha) DO UPDATE SET run_id=excluded.run_id",
                (head, run_id),
            )
        return run_id

    def final_decision(self, group, pending_run=None):
        if not self.c.final_check_enabled:
            return "disabled"
        head = group["head"]
        run_id = pending_run or self.pending_final(group)
        if not self.github.trusted_ci_passed(head):
            return "pending"
        # CI completion can take hours. Re-read the authoritative live queue, source
        # policy, all authors and encrypted evidence *after* those gates completed.
        with self.store.connection() as db:
            revision = db.execute("SELECT value FROM state WHERE key='revision'").fetchone()[0]
        try:
            people, members = self.github.group_snapshot(
                head, group["base"], group["base_ref"], self.policy
            )
            good = people and all(
                uid in self.c.exempt_ids.values()
                or self.store.qualified(uid, self.policy["agreements"]["individual"]["sha256"])
                for uid in people
            )
        except Exception:
            good, members = False, []
        proof = {
            "protocol": "tavotto-cla-v1",
            "kind": "final_merge_decision",
            "head_sha": head,
            "base_sha": group["base"],
            "members": members,
            "policy_sha256": policy_digest(self.policy),
            "revision": revision,
            "issued_at": int(time.time()),
        }
        with self.store.transaction() as db:
            if db.execute("SELECT value FROM state WHERE key='revision'").fetchone()[0] != revision:
                good = False
            self.github.publish(
                head,
                "final",
                "success" if good else "failure",
                proof,
                run_id,
                self.c.final_check_name,
            )
        return "success" if good else "failure"

    def tick_group(self):
        now = int(time.time())
        with self.store.connection() as db:
            group = db.execute(
                "SELECT * FROM groups WHERE active=1 AND due<=? ORDER BY due LIMIT 1", (now,)
            ).fetchone()
            observed_revision = db.execute(
                "SELECT value FROM state WHERE key='revision'"
            ).fetchone()[0]
        if not group:
            return False
        try:
            pending_run = self.pending_final(group)
            self.refresh_group(group)
            self.final_decision(group, pending_run)
            due, attempts = now + 60, 0
        except Exception:
            attempts = group["attempts"] + 1
            due = now + min(300, 2 ** min(attempts, 8))
        with self.store.transaction() as db:
            # Conditional update preserves an invalidation queued during the HTTP work.
            db.execute(
                "UPDATE groups SET due=?,attempts=? WHERE head=? AND due=? AND (SELECT value FROM state WHERE key='revision')=?",
                (due, attempts, group["head"], group["due"], observed_revision),
            )
        return True

    def tick(self):
        now = int(time.time())
        with self.store.transaction() as db:
            for row in db.execute("SELECT number FROM prs WHERE checked<?", (now - 60,)).fetchall():
                db.execute("INSERT OR IGNORE INTO jobs(pr,due) VALUES (?,?)", (row[0], now))
            job = db.execute(
                "SELECT * FROM jobs WHERE due<=? ORDER BY due,id LIMIT 1", (now,)
            ).fetchone()
            if not job:
                return False
            # Remove before work; changes during work enqueue another refresh. On crash,
            # tracked PR reconciliation/webhook redelivery restores work within 60 sec.
            db.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
            db.execute("INSERT OR IGNORE INTO prs(number,sha) VALUES (?,?)", (job["pr"], ""))
        try:
            self.refresh(job["pr"])
        except Exception:
            with self.store.transaction() as db:
                db.execute(
                    "INSERT INTO jobs(pr,due,attempts) VALUES (?,?,?) ON CONFLICT(pr) DO UPDATE SET due=min(jobs.due,excluded.due),attempts=excluded.attempts",
                    (
                        job["pr"],
                        now + min(300, 2 ** min(job["attempts"] + 1, 8)),
                        job["attempts"] + 1,
                    ),
                )
        return True

    def run(self):
        with (self.c.data_dir / "worker.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            while True:
                group_work = self.tick_group()
                if not self.tick() and not group_work:
                    time.sleep(1)
