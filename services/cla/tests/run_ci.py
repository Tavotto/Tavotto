"""Run the full isolated CLA suite; missing dependencies, missing tests or skips fail."""

import re
import sys
import unittest
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MIN_TESTS = 41


def main():
    for line in (ROOT / "services/cla/requirements-core.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_-]+)==([0-9.]+)", line)
        if not match:
            raise RuntimeError(f"Expected an exact core dependency pin, got {line!r}")
        package, expected = match.groups()
        actual = version(package)  # PackageNotFoundError is a hard failure, never a skip.
        if actual != expected:
            raise RuntimeError(f"{package}: expected {expected}, got {actual}")
    # Existing repository root is already a Ruff source root; no new import root.
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "services/cla/tests"))
    count = suite.countTestCases()
    if count < MIN_TESTS:
        raise RuntimeError(
            f"Expected at least {MIN_TESTS} CLA security/contract tests, collected {count}"
        )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped or result.expectedFailures:
        print("CLA service tests may not be skipped or expected failures", file=sys.stderr)
    return 0 if result.wasSuccessful() and not result.skipped and not result.expectedFailures else 1


if __name__ == "__main__":
    raise SystemExit(main())
