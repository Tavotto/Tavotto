"""完整插件（staging / zip / 发行分支）测试的共用夹具。

合成 staging = 仓库里**真实跟踪的**插件源码（`git ls-files codex-plugin`）+ 一份假画布
+ LICENSE + 由 `plugin_stage.write_build_manifest` 写的真清单。假画布只要满足画布检查
的形状（指纹戳、`<div id="root">`、体量下限）；它不是被测对象，被测的是清单、摘要、
归档与发布器对**这份内容**的处理。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
FAKE_SHA = "0123456789abcdef0123456789abcdef01234567"


def load_script(name: str):
    """按路径 import scripts/<name>.py（它们互相 import 靠同目录 sys.path）。"""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    modname = name.replace("/", "_")
    spec = importlib.util.spec_from_file_location(modname, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def fake_widget_bytes(fingerprint: str = "feedfacecafebeef", *, salt: str = "") -> bytes:
    stage = load_script("build_mcp_widget")
    filler = ("<!-- " + ("x" * 96) + " -->\n") * 1200  # ≈ 120 KB，过体量下限
    html = (
        f'{stage.STAMP}{fingerprint} -->\n<!doctype html>\n<html><head><meta charset="utf-8">'
        f'<title>t {salt}</title></head><body><div id="root"></div>'
        f'<script type="module">/* {salt} */</script>{filler}</body></html>\n'
    )
    return html.encode("utf-8")


def write_fake_widget(path: Path, **kw) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(fake_widget_bytes(**kw))
    return path


def synthetic_staging(
    dest: Path,
    *,
    source_sha: str = FAKE_SHA,
    version: str | None = None,
    widget_salt: str = "",
    fingerprint: str = "feedfacecafebeef",
    audit: dict | None = None,
) -> dict:
    """在 `dest` 摆一份形状真实的插件目录并写清单；返回清单。"""
    stage = load_script("plugin_stage")
    dest.mkdir(parents=True, exist_ok=True)
    modes: dict[str, str] = {}
    for rel, mode in stage.tracked_plugin_files(ROOT):
        src = ROOT / "codex-plugin" / rel
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(src.read_bytes())
        if os.name != "nt" and mode == "100755":
            out.chmod(0o755)
        modes[rel] = mode
    write_fake_widget(
        dest / "mcp" / "widget" / "canvas.html", fingerprint=fingerprint, salt=widget_salt
    )
    modes["mcp/widget/canvas.html"] = "100644"
    (dest / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
    modes["LICENSE"] = "100644"
    if version is not None:
        pj = dest / ".codex-plugin" / "plugin.json"
        import json

        data = json.loads(pj.read_text(encoding="utf-8"))
        data["version"] = version
        pj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return stage.write_build_manifest(
        dest,
        modes=modes,
        source_sha=source_sha,
        fingerprint=fingerprint,
        lockfile_sha256="0" * 64,
        toolchain={"python": "3.13.0", "node": "22.0.0", "pnpm": "11.0.0"},
        audit=audit,
        min_tavotto_version="0.13.0",
    )


def git(*args: str, cwd: Path | None = None, env: dict | None = None) -> str:
    base_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_TERMINAL_PROMPT": "0",
    }
    if env:
        base_env.update(env)
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=base_env,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 失败：{proc.stderr}")
    return proc.stdout.strip()


def bare_remote_with_main(tmp_path: Path) -> tuple[Path, str]:
    """一个带 `main` 分支（一个提交）的 bare 仓库；返回 (路径, main 的 SHA)。"""
    remote = tmp_path / "remote.git"
    git("init", "--quiet", "--bare", str(remote))
    work = tmp_path / "seed"
    work.mkdir()
    git("init", "--quiet", "-b", "main", cwd=work)
    (work / "README").write_text("seed\n", encoding="utf-8")
    git("add", "README", cwd=work)
    git("commit", "--quiet", "-m", "seed", cwd=work)
    sha = git("rev-parse", "HEAD", cwd=work)
    git("push", "--quiet", str(remote), "HEAD:refs/heads/main", cwd=work)
    return remote, sha


def remote_tip(remote: Path, branch: str = "plugin-stable") -> str | None:
    out = git("ls-remote", "--refs", str(remote), f"refs/heads/{branch}")
    return out.split()[0] if out else None
