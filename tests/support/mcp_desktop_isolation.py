"""Keep no-desktop MCP scenarios independent of the machine running the tests.

Only a fresh test venv is changed. Its startup hook hides desktop install roots
from the real plugin locator; explicit CLI overrides and PATH discovery still
use the real code. The installed application itself is never touched. A .pth
hook also runs when the base Python already has its own sitecustomize module.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_STARTUP = """\
import os
import sys

try:
    script = os.path.abspath(sys.argv[0])
    if os.path.basename(script) == "server.py" and os.path.basename(os.path.dirname(script)) == "mcp":
        plugin = os.path.dirname(os.path.dirname(script))
        scripts = os.path.join(plugin, "skills", "tavotto-figure", "scripts")
        sys.path.insert(0, scripts)
        import handoff

        handoff.install_roots = lambda *args, **kwargs: []
        handoff.hkcu_install_dirs = lambda: []
except BaseException as exc:
    # Ordinary exceptions from startup hooks can be ignored by Python.
    raise SystemExit("test desktop discovery isolation failed") from exc
"""


def hide_desktop_installations(python: str | Path) -> None:
    """Install the hook in a fresh venv, without inheriting the caller's PYTHONPATH."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            str(python),
            "-c",
            'import json, sysconfig; print(json.dumps(sysconfig.get_path("purelib")))',
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=True,
    )
    purelib = Path(json.loads(result.stdout.strip().splitlines()[-1]))
    startup = purelib / "_tavotto_test_desktop_isolation.py"
    pth = purelib / "_tavotto_test_desktop_isolation.pth"
    if startup.exists() or pth.exists():
        raise RuntimeError("desktop isolation requires a fresh test venv")
    startup.write_text(_STARTUP, encoding="utf-8")
    pth.write_text("import _tavotto_test_desktop_isolation\n", encoding="utf-8")
