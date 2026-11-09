"""The no-desktop test environment must affect the actual child interpreter."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import venv
from pathlib import Path

from tests.support.mcp_desktop_isolation import hide_desktop_installations


def test_isolation_hides_desktop_roots_only_for_the_mcp_child(tmp_path: Path) -> None:
    venv_dir = tmp_path / "test venv"
    venv.EnvBuilder(with_pip=False).create(venv_dir)
    python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python3")
    plugin = tmp_path / "plugin 包"
    scripts = plugin / "skills" / "tavotto-figure" / "scripts"
    scripts.mkdir(parents=True)
    source = (
        Path(__file__).resolve().parent.parent
        / "codex-plugin"
        / "skills"
        / "tavotto-figure"
        / "scripts"
        / "handoff.py"
    )
    shutil.copyfile(source, scripts / "handoff.py")
    code = """\
import json, os, sys
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(root, "skills", "tavotto-figure", "scripts"))
import handoff
print(json.dumps({
    "roots": handoff.install_roots(system="darwin", environ={"HOME": "/example"}),
    "override": handoff.find_tavotto(environ={"TAVOTTO_CLI": "explicit-cli"})["cmd"],
    "path_cli": handoff.find_tavotto(environ={}, which=lambda name: "path-cli")["cmd"],
    "sitecustomize": getattr(sys.modules.get("sitecustomize"), "__file__", None),
}))
"""
    servers = []
    for parent in ("mcp", "control"):
        path = plugin / parent / "server.py"
        path.parent.mkdir(parents=True)
        path.write_text(code, encoding="utf-8")
        servers.append(path)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    def run(server: Path) -> dict:
        result = subprocess.run(
            [str(python), str(server)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
            check=True,
        )
        return json.loads(result.stdout)

    before = run(servers[0])
    assert before["roots"] == ["/Applications/Tavotto.app", "/example/Applications/Tavotto.app"]
    assert before["override"] == ["explicit-cli"]
    assert before["path_cli"] == ["path-cli"]
    hide_desktop_installations(python)
    after = run(servers[0])
    assert after["roots"] == []
    assert after["override"] == before["override"]
    assert after["path_cli"] == before["path_cli"]
    assert after["sitecustomize"] == before["sitecustomize"]
    assert run(servers[1]) == before
