"""Trusted operator configuration. Never loaded from a pull request."""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def policy_digest(policy):
    return hashlib.sha256(
        canonical({k: policy[k] for k in ("agreements", "exemptions")}).encode()
    ).hexdigest()


@dataclass(frozen=True)
class Config:
    root: Path
    data_dir: Path
    origin: str
    repository: str
    repository_id: int
    holder_id: int
    app_id: int
    app_slug: str
    installation_id: int
    client_id: str
    client_secret: str
    private_key: bytes
    webhook_secret: bytes
    evidence_key: bytes
    # Immutable IDs independently verified by operator; not inferred from policy logins.
    exempt_ids: dict = field(default_factory=dict)
    check_name: str = "Tavotto CLA"
    final_check_enabled: bool = False
    final_check_name: str = "Tavotto CLA merge decision"
    trusted_gates: dict = field(default_factory=dict)
    merge_queue_enabled: bool = False
    signing_enabled: bool = False
    test_mode: bool = False
    api_base: str = "https://api.github.com"
    oauth_base: str = "https://github.com"

    def validate(self):
        parsed = urlsplit(self.origin)
        if parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("origin must contain only scheme and host")
        if parsed.scheme != "https" and not (self.test_mode and parsed.hostname == "127.0.0.1"):
            raise ValueError("HTTPS required")
        if not self.test_mode and (
            self.api_base != "https://api.github.com" or self.oauth_base != "https://github.com"
        ):
            raise ValueError("production GitHub endpoints are pinned")
        if self.data_dir.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("private data must be outside the source checkout")
        if min(self.repository_id, self.holder_id, self.app_id, self.installation_id) <= 0:
            raise ValueError("positive immutable IDs required")
        if self.app_id == 15368 or self.app_slug == "github-actions":
            raise ValueError("independent GitHub App required")
        if len(self.webhook_secret) < 32:
            raise ValueError("webhook secret too short")
        if self.final_check_enabled:
            if not self.merge_queue_enabled or self.final_check_name == self.check_name:
                raise ValueError(
                    "final check requires independent name and merge queue verification"
                )
            if set(self.trusted_gates) != {"CI fast gate", "CI integration gate", "CodeQL gate"}:
                raise ValueError("all three trusted CI gates must be pinned")
            for gate in self.trusted_gates.values():
                if (
                    type(gate.get("workflow_id")) is not int
                    or gate["workflow_id"] <= 0
                    or gate.get("app_id") != 15368
                    or not str(gate.get("path", "")).startswith(".github/workflows/")
                ):
                    raise ValueError("trusted gate workflow/App identity required")
        policy = json.loads((self.root / ".github/cla-policy.json").read_text())
        for name, ag in policy["agreements"].items():
            raw = (self.root / ag["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != ag["sha256"] or ag["version"].endswith("-draft"):
                raise ValueError("agreement integrity failure")
            if f"CLA_VERSION: {ag['version']}" not in raw.decode():
                raise ValueError("agreement version mismatch")
        if set(self.exempt_ids) != {e["login"] for e in policy["exemptions"]}:
            raise ValueError("all policy exemptions need operator-pinned immutable IDs")
        if self.exempt_ids.get("erwanjun") != self.holder_id:
            raise ValueError("rights holder ID mismatch")
        if len(set(self.exempt_ids.values())) != len(self.exempt_ids):
            raise ValueError("duplicate exemption IDs")
        return policy
