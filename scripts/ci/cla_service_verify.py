#!/usr/bin/env python3
"""Trusted read-only CI consumer for tavotto-cla-v1, including merge candidates.

Fetch this file, cla_gate.py and policy from the default branch, never PR code.
Every invocation demands a verdict issued after this invocation began. No write
permission, service secret or private signature evidence reaches the runner.
"""

import argparse
import importlib.util
import json
import os
import re
import time
import urllib.request
from pathlib import Path


def fetch_checks(repository, head, token, api_base="https://api.github.com"):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not re.fullmatch(
        r"[0-9a-f]{40}", head
    ):
        raise ValueError("invalid repository or head")

    # Never follow redirects with the read-only workflow token.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None

    opener = urllib.request.build_opener(NoRedirect())
    checks = []
    for page in range(1, 101):
        url = f"{api_base}/repos/{repository}/commits/{head}/check-runs?filter=all&per_page=100&page={page}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with opener.open(request, timeout=15) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("response limit")
            rows = json.loads(raw)["check_runs"]
        checks.extend(rows)
        if len(rows) < 100:
            return checks
    raise ValueError("pagination limit")


def event_context(event_name, event, now):
    context = {
        "kind": event_name,
        "repository_id": event["repository"]["id"],
        "not_before": now,
        "now": now,
    }
    if event_name == "pull_request":
        pr = event["pull_request"]
        context.update(pr=pr["number"], head_sha=pr["head"]["sha"])
    elif event_name == "merge_group":
        group = event["merge_group"]
        if event["action"] != "checks_requested" or group["base_ref"] != "refs/heads/main":
            raise ValueError("unsupported candidate")
        context.update(head_sha=group["head_sha"], base_sha=group["base_sha"])
    else:
        raise ValueError("unsupported event")
    return context


def verify(gate, policy, context, fetch, wait_seconds=180, sleep=time.sleep, clock=time.time):
    deadline = clock() + wait_seconds
    reason = "provider_unavailable"
    while True:
        try:
            checks = fetch()
            context["now"] = int(clock())
            if clock() > deadline:
                return {"status": "failure", "reason": "verification_deadline_exceeded"}
            signed, reason = gate.signed_logins(checks, policy, context)
            if "*" in signed:
                return {"status": "success", "reason": "fresh_authoritative_verdict"}
        except Exception:
            reason = "provider_unavailable"
        if clock() >= deadline:
            return {"status": "failure", "reason": reason}
        sleep(min(5, max(0, deadline - clock())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True, choices=("pull_request", "merge_group"))
    parser.add_argument("--event-file", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=int, default=180)
    args = parser.parse_args()
    if not 0 <= args.wait_seconds <= 300:
        parser.error("wait-seconds must be 0..300")
    try:
        spec = importlib.util.spec_from_file_location(
            "trusted_cla_gate", Path(__file__).with_name("cla_gate.py")
        )
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        policy = json.loads(args.policy.read_text())
        gate.validate_policy(policy)
        if (
            not policy["provider"]["configured"]
            or policy["provider"].get("protocol") != "tavotto-cla-v1"
        ):
            raise ValueError("service provider is not activated")
        event = json.loads(args.event_file.read_text())
        context = event_context(args.event, event, int(time.time()))
        result = verify(
            gate,
            policy,
            context,
            lambda: fetch_checks(
                event["repository"]["full_name"], context["head_sha"], os.environ["GH_TOKEN"]
            ),
            args.wait_seconds,
        )
    except Exception:
        result = {"status": "failure", "reason": "verification_unavailable"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
