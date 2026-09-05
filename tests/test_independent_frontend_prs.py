"""核心验收（ADR 0043）：两个互不相关的前端 PR 不再因同一份生成物相撞。

在一个临时克隆里从同一基线开 A / B 两个分支，各改一处**会进构建结果**的生产前端位置
（两个不同的 locale JSON——它们整份进 bundle）；两边都只提交源码、不提交 HTML。先合 A，
再**不改写 B、不为生成物 rebase** 地合 B：Git 必须能完成组合。然后从组合后的树真构建画布、
组装并验证完整插件、真起 server 读回资源——两个改动都必须出现在**产物**里，而不是只 grep
合并退出码或注释。

需要 pnpm + web/node_modules（CI 的 frontend job 上有；本地没装就 skip 并说明）。这条本地
测试证明的是「生成物冲突的根因已消除」，不等于 GitHub Merge Queue 的双候选已实跑——那需要
两条真实 PR，见 docs/ci/plugin-stable-channel.md 第 6 节。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support import pluginkit as kit

ROOT = kit.ROOT

pytestmark = pytest.mark.skipif(
    shutil.which("pnpm") is None or not (ROOT / "web" / "node_modules").is_dir(),
    reason="需要 pnpm 与 web/node_modules 真构建一次画布（CI 的 frontend job 上执行）",
)

MARK_A = "PR-A-MARKER-a1b2c3d4"
MARK_B = "PR-B-MARKER-e5f6a7b8"


def _add_key(clone: Path, locale: str, key: str, value: str) -> None:
    p = clone / "web" / "src" / "i18n" / "locales" / locale / "common.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data[key] = value
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_two_frontend_branches_combine_without_a_generated_artifact_conflict(tmp_path):
    stage = kit.load_script("plugin_stage")
    clone = tmp_path / "clone"
    # --shared：对象直接借本仓库的，几秒钟；工作区是独立的
    kit.git("clone", "--quiet", "--shared", "--no-checkout", str(ROOT), str(clone))
    kit.git("checkout", "--quiet", "-b", "base", cwd=clone)
    base = kit.git("rev-parse", "HEAD", cwd=clone)
    # 克隆里自己 pnpm install：store 已经热了（本仓库刚装过），秒级；软链本仓库的
    # node_modules 会触发 pnpm 的项目路径核对并要求交互式确认
    install = subprocess.run(
        ["pnpm", "install", "--frozen-lockfile", "--prefer-offline"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(clone / "web"),
        env={**os.environ, "CI": "true"},
        timeout=600,
    )
    assert install.returncode == 0, install.stdout[-1500:] + install.stderr[-1500:]

    # ---- 两个分支，各改一个不同的生产前端文件，都不提交 HTML ----
    kit.git("checkout", "--quiet", "-b", "pr-a", "base", cwd=clone)
    _add_key(clone, "zh-CN", "acceptanceMarkerA", MARK_A)
    kit.git("commit", "--quiet", "-am", "PR A: zh-CN marker", cwd=clone)
    kit.git("checkout", "--quiet", "-b", "pr-b", "base", cwd=clone)
    _add_key(clone, "en-US", "acceptanceMarkerB", MARK_B)
    kit.git("commit", "--quiet", "-am", "PR B: en-US marker", cwd=clone)
    for branch in ("pr-a", "pr-b"):
        changed = kit.git("diff", "--name-only", base, branch, cwd=clone).splitlines()
        assert all(not c.endswith("canvas.html") for c in changed), changed

    # ---- 先集成 A，再不改写 B 地组合 B：必须成功，且 B 的提交原样在历史里 ----
    kit.git("checkout", "--quiet", "base", cwd=clone)
    kit.git("merge", "--quiet", "--ff-only", "pr-a", cwd=clone)
    b_tip = kit.git("rev-parse", "pr-b", cwd=clone)
    proc = subprocess.run(
        ["git", "-C", str(clone), "merge", "--quiet", "--no-edit", "pr-b"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@x",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@x",
        },
    )
    assert proc.returncode == 0, f"组合 B 失败：{proc.stdout}{proc.stderr}"
    assert kit.git("status", "--porcelain", cwd=clone) == ""
    assert kit.git("rev-parse", "pr-b", cwd=clone) == b_tip, "B 分支被改写了"
    assert kit.git("merge-base", "--is-ancestor", b_tip, "HEAD", cwd=clone) == ""
    merged = kit.git("rev-parse", "HEAD", cwd=clone)
    assert not (clone / "codex-plugin" / "mcp" / "widget" / "canvas.html").exists(), (
        "组合树里不该有入库的画布"
    )

    # ---- 从组合后的树真构建：产物同时体现 A 与 B ----
    out = tmp_path / "canvas.html"
    proc = subprocess.run(
        [
            sys.executable,
            str(clone / "scripts" / "build_mcp_widget.py"),
            "--out",
            str(out),
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(clone),
        env={**os.environ, "NODE_ENV": "production", "CI": "true"},
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    html = out.read_text(encoding="utf-8")
    assert MARK_A in html and MARK_B in html, "组合产物没有同时带上 A 与 B 的改动"

    # ---- 组装完整插件 + 真起 server：装到的就是这份 ----
    proc = subprocess.run(
        [
            sys.executable,
            str(clone / "scripts" / "plugin_stage.py"),
            "stage",
            "--widget",
            str(out),
            "--out",
            str(tmp_path / "stage"),
            "--source-sha",
            merged,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(clone),
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    problems = stage.verify_dir(tmp_path / "stage", source_sha=merged)
    assert problems == [], problems
    assert stage.serve_check(tmp_path / "stage", sys.executable) == []
    served = (tmp_path / "stage" / "mcp" / "widget" / "canvas.html").read_text(encoding="utf-8")
    assert MARK_A in served and MARK_B in served
