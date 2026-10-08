"""Manual counterexamples in isolated copies, never mutate the reviewed checkout."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MUTATIONS = [
    (
        "require_both_signatures",
        "services/cla/store.py",
        '                ("holder", self.config.holder_id),\n',
        "",
        "ServiceTests.test_real_http_oauth_double_signature_and_check",
    ),
    (
        "verify_webhook_hmac",
        "services/cla/app.py",
        "if not hmac.compare_digest(",
        "if False and not hmac.compare_digest(",
        "ServiceTests.test_webhook_hmac_dedup_and_repository_binding",
    ),
    (
        "require_authoritative_exemption_proof",
        "scripts/ci/cla_gate.py",
        'if service_mode and "*" not in signed:',
        'if False and service_mode and "*" not in signed:',
        "GateTests.test_exempt_login_cannot_bypass_authoritative_service",
    ),
    (
        "latest_verdict_wins",
        "scripts/ci/cla_gate.py",
        'run = max(own, key=lambda r: r["id"])',
        'run = min(own, key=lambda r: r["id"])',
        "GateTests.test_fresh_proof_and_latest_failure",
    ),
    (
        "wait_for_trusted_ci",
        "services/cla/worker.py",
        "if not self.github.trusted_ci_passed(head):",
        "if False and not self.github.trusted_ci_passed(head):",
        "ServiceTests.test_final_decision_waits_for_ci_then_rechecks_current_signatures",
    ),
    (
        "private_record_access",
        "services/cla/app.py",
        'if session["actor"] not in (p["subject"], self.c.holder_id):',
        'if False and session["actor"] not in (p["subject"], self.c.holder_id):',
        "ServiceTests.test_private_record_unauthorized_account",
    ),
]


def main():
    failed = False
    for name, target, original, changed, test in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="cla-counterexample-") as directory:
            root = Path(directory) / "source"
            root.mkdir()
            for folder in ("services/cla", "scripts/ci", "docs/legal"):
                shutil.copytree(
                    ROOT / folder, root / folder, ignore=shutil.ignore_patterns("__pycache__")
                )
            (root / "services/__init__.py").touch()
            (root / ".github").mkdir()
            shutil.copyfile(ROOT / ".github/cla-policy.json", root / ".github/cla-policy.json")
            path = root / target
            source = path.read_text()
            if source.count(original) != 1:
                raise RuntimeError(f"{name}: mutation does not have exactly one intended site")
            path.write_text(source.replace(original, changed, 1))
            command = [sys.executable, "-m", "unittest", "services.cla.tests.test_service." + test]
            result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
            caught = result.returncode != 0 and "FAIL:" in result.stderr
            print(
                f"{name}: {'CAUGHT' if caught else 'NOT CAUGHT'} (exit {result.returncode})",
                flush=True,
            )
            if not caught:
                failed = True
                print(result.stdout + result.stderr)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
