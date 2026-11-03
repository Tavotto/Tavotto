"""Project-local authority for actual executions, independent of their output set.

Only trusted worker/bridge facts enter this ledger. Saved owners come from the
validated immutable archive; browser-supplied owner labels never authorize it.
No process arguments or machine paths are persisted here.
"""

from __future__ import annotations

import threading

from . import figcapture


def owner(script, run_config=None):
    if not isinstance(script, str) or not script:
        return None
    try:
        canonical = figcapture.normalize_relative_script(script)
    except ValueError:
        return None
    if canonical != script or (
        run_config is not None and (not isinstance(run_config, str) or not run_config)
    ):
        return None
    return {"script": canonical, "run_config": run_config}


def key(value):
    return value["script"], value.get("run_config")


class Ledger:
    """Retained for the open project lifetime, never used as cold authority."""

    def __init__(self):
        self.lock = threading.RLock()
        self.owners = {}

    def observe(self, identity, execution_id, started_ns, worker):
        with self.lock:
            previous = self.owners.get(key(identity))
            if previous is not None and previous["started_ns"] >= started_ns:
                return previous if previous["execution_id"] == execution_id else None
            state = {
                "owner": identity,
                "execution_id": execution_id,
                "started_ns": started_ns,
                "worker": worker,
                "receipts": set(),
                "sources": set(),
                "retired_receipts": set(),
                "unknown": False,
            }
            self.owners[key(identity)] = state
            return state

    def get(self, identity):
        with self.lock:
            return self.owners.get(key(identity)) if identity else None

    def uncertain(self, identity, worker, attempted_ns):
        with self.lock:
            state = self.get(identity)
            if state is None:
                self.owners[key(identity)] = {
                    "owner": identity,
                    "execution_id": None,
                    "started_ns": attempted_ns,
                    "worker": worker,
                    "receipts": set(),
                    "sources": set(),
                    "retired_receipts": set(),
                    "unknown": True,
                }
            elif attempted_ns >= state["started_ns"]:
                state["unknown"] = True
                state["started_ns"] = attempted_ns
                state["worker"] = worker

    def admit(self, identity, worker, execution_id, receipt_id):
        with self.lock:
            state = self.get(identity)
            if state is None:
                return True  # Legacy/test workers without an actual-start channel.
            if (
                state["worker"] is not worker
                or state["execution_id"] != execution_id
                or state["unknown"]
            ):
                return False
            state["receipts"].add(receipt_id)
            return True

    def accepts(self, identity, receipt_id):
        with self.lock:
            state = self.get(identity)
            return (
                None if state is None else not state["unknown"] and receipt_id in state["receipts"]
            )

    def fact(self, state):
        with self.lock:
            return {
                "scope": "owner",
                **state["owner"],
                "execution_id": state["execution_id"],
                "source_ids": sorted(state["sources"]),
                "receipt_ids": sorted(state["retired_receipts"]),
            }
