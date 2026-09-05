"""发行分支发布器（scripts/plugin_publish.py）的行为契约——全部对着**临时 bare 仓库**真推真读。

主语是远端分支的 ref 与树，不是发布器的返回值：每条断言都回头 `ls-remote` /
`ls-tree` 看远端到底变没变、变成了什么。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from tests.support import pluginkit as kit

stage = kit.load_script("plugin_stage")
pub = kit.load_script("plugin_publish")

BR = "plugin-stable"


@pytest.fixture()
def remote(tmp_path):
    return kit.bare_remote_with_main(tmp_path)


def _staging(tmp_path, name, *, sha, version=None, salt=""):
    d = tmp_path / name
    kit.synthetic_staging(d, source_sha=sha, version=version, widget_salt=salt)
    return d


def _run(args: list[str]) -> int:
    return pub.main(args)


def _receipt(remote: Path, sha: str) -> dict:
    return json.loads(kit.git("show", f"{sha}:{pub.RECEIPT}", cwd=remote))


def _bootstrap(remote, tmp_path, sha, **kw) -> tuple[Path, str]:
    d = _staging(tmp_path, kw.pop("name", "s1"), sha=sha, **kw)
    rc = _run(
        [
            "bootstrap",
            "--remote",
            str(remote),
            "--staging",
            str(d),
            "--source-sha",
            sha,
            "--engine-check",
            "none",
            "--reason",
            "test",
            "--yes",
        ]
    )
    assert rc == 0
    tip = kit.remote_tip(remote)
    assert tip
    return d, tip


# ================================================================ 演练与目标


def test_plan_and_no_yes_never_touch_the_remote(remote, tmp_path):
    r, sha = remote
    d = _staging(tmp_path, "s", sha=sha)
    assert (
        _run(
            [
                "plan",
                "--remote",
                str(r),
                "--staging",
                str(d),
                "--source-sha",
                sha,
                "--engine-check",
                "none",
                "--reason",
                "t",
            ]
        )
        == 0
    )
    assert kit.remote_tip(r) is None
    assert (
        _run(
            [
                "bootstrap",
                "--remote",
                str(r),
                "--staging",
                str(d),
                "--source-sha",
                sha,
                "--engine-check",
                "none",
                "--reason",
                "t",
            ]
        )
        == 0
    )
    assert kit.remote_tip(r) is None, "没有 --yes 也推了"


def test_main_can_never_be_the_target_branch(remote, tmp_path):
    r, sha = remote
    d = _staging(tmp_path, "s", sha=sha)
    rc = _run(
        [
            "bootstrap",
            "--remote",
            str(r),
            "--branch",
            "main",
            "--staging",
            str(d),
            "--source-sha",
            sha,
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == pub.EXIT_ERROR
    assert kit.git("rev-parse", "refs/heads/main", cwd=r) == sha, "main 被动了"


def test_default_remote_is_the_brand_repository():
    from tavotto.engine import brand

    assert pub.default_remote() == brand.REPO_URL + ".git"


# ================================================================ bootstrap


def test_bootstrap_creates_a_full_projection_with_receipt(remote, tmp_path):
    r, sha = remote
    d, tip = _bootstrap(r, tmp_path, sha)
    names = kit.git("ls-tree", "-r", "--name-only", tip, cwd=r).splitlines()
    assert "codex-plugin/.codex-plugin/plugin.json" in names
    assert "codex-plugin/.mcp.json" in names
    assert "codex-plugin/mcp/widget/canvas.html" in names
    assert "codex-plugin/plugin-build.json" in names
    assert ".gitattributes" in names and ".agents/plugins/marketplace.json" in names
    assert "README.md" in names and "LICENSE" in names
    assert not any(n.startswith("web/") or n.startswith("src/") for n in names), (
        "源码混进了发行分支"
    )
    receipt = _receipt(r, tip)
    m = stage.read_manifest(d)
    assert receipt["kind"] == "bootstrap"
    assert receipt["version"] == m["plugin_version"]
    assert receipt["content_digest"] == m["content_digest"]
    assert receipt["source_sha"] == sha
    assert receipt["previous"] is None
    assert receipt["engine_check"]["skipped_reason"] == "test"
    assert kit.git("rev-list", "--count", tip, cwd=r) == "1"
    assert kit.git("show", f"{tip}:.gitattributes", cwd=r) == "* -text"
    mk = json.loads(kit.git("show", f"{tip}:.agents/plugins/marketplace.json", cwd=r))
    assert mk["plugins"][0]["source"] == {"source": "local", "path": "./codex-plugin"}


def test_bootstrap_refuses_when_the_branch_exists(remote, tmp_path):
    r, sha = remote
    d, tip = _bootstrap(r, tmp_path, sha)
    rc = _run(
        [
            "bootstrap",
            "--remote",
            str(r),
            "--staging",
            str(d),
            "--source-sha",
            sha,
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == pub.EXIT_REFUSED
    assert kit.remote_tip(r) == tip


def test_bootstrap_refuses_an_unverified_staging(remote, tmp_path):
    r, sha = remote
    d = _staging(tmp_path, "s", sha=sha)
    (d / "mcp" / "widget" / "canvas.html").unlink()
    rc = _run(
        [
            "bootstrap",
            "--remote",
            str(r),
            "--staging",
            str(d),
            "--source-sha",
            sha,
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == pub.EXIT_ERROR
    assert kit.remote_tip(r) is None


def test_source_sha_must_match_the_manifest(remote, tmp_path):
    r, sha = remote
    d = _staging(tmp_path, "s", sha=sha)
    rc = _run(
        [
            "bootstrap",
            "--remote",
            str(r),
            "--staging",
            str(d),
            "--source-sha",
            "e" * 40,
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc != 0
    assert kit.remote_tip(r) is None


# ================================================================ promote


def test_promote_is_a_noop_for_the_same_content(remote, tmp_path):
    r, sha = remote
    d, tip = _bootstrap(r, tmp_path, sha)
    rc = _run(
        [
            "promote",
            "--remote",
            str(r),
            "--staging",
            str(d),
            "--source-sha",
            sha,
            "--expected-remote-sha",
            tip,
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == 0
    assert kit.remote_tip(r) == tip, "no-op 不许造新提交"


def test_same_version_different_content_is_refused(remote, tmp_path):
    r, sha = remote
    _d, tip = _bootstrap(r, tmp_path, sha)
    other = _staging(tmp_path, "s2", sha=sha, salt="different bytes")
    rc = _run(
        [
            "promote",
            "--remote",
            str(r),
            "--staging",
            str(other),
            "--source-sha",
            sha,
            "--expected-remote-sha",
            tip,
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == pub.EXIT_REFUSED
    assert kit.remote_tip(r) == tip


def test_an_older_version_arriving_late_is_refused(remote, tmp_path):
    r, sha = remote
    _d, tip = _bootstrap(r, tmp_path, sha, version="0.14.0")
    old = _staging(tmp_path, "old", sha=sha, version="0.13.9")
    rc = _run(
        [
            "promote",
            "--remote",
            str(r),
            "--staging",
            str(old),
            "--source-sha",
            sha,
            "--expected-remote-sha",
            tip,
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == pub.EXIT_REFUSED
    assert kit.remote_tip(r) == tip


def test_a_newer_version_fast_forwards_on_top_of_the_old_release(remote, tmp_path):
    r, sha = remote
    _d, tip = _bootstrap(r, tmp_path, sha, version="0.13.0")
    new = _staging(tmp_path, "new", sha=sha, version="0.14.0", salt="v14")
    rc = _run(
        [
            "promote",
            "--remote",
            str(r),
            "--staging",
            str(new),
            "--source-sha",
            sha,
            "--expected-remote-sha",
            tip,
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == 0
    tip2 = kit.remote_tip(r)
    assert tip2 != tip
    assert kit.git("rev-parse", f"{tip2}^", cwd=r) == tip, "不是以旧发行提交为父"
    rec = _receipt(r, tip2)
    assert rec["kind"] == "promote" and rec["version"] == "0.14.0" and rec["previous"] == tip
    assert (
        pub.tree_digest(_clone(r, tmp_path / "c"), tip2)
        == stage.read_manifest(new)["content_digest"]
    )


def test_promote_refuses_when_the_remote_moved(remote, tmp_path):
    r, sha = remote
    _d, tip = _bootstrap(r, tmp_path, sha, version="0.13.0")
    new = _staging(tmp_path, "new", sha=sha, version="0.14.0")
    rc = _run(
        [
            "promote",
            "--remote",
            str(r),
            "--staging",
            str(new),
            "--source-sha",
            sha,
            "--expected-remote-sha",
            "0" * 40,
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == pub.EXIT_REFUSED
    assert kit.remote_tip(r) == tip


def test_promote_requires_expected_remote_sha(remote, tmp_path):
    r, sha = remote
    _d, tip = _bootstrap(r, tmp_path, sha)
    new = _staging(tmp_path, "new", sha=sha, version="0.14.0")
    rc = _run(
        [
            "promote",
            "--remote",
            str(r),
            "--staging",
            str(new),
            "--source-sha",
            sha,
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == pub.EXIT_REFUSED
    assert kit.remote_tip(r) == tip


def test_promote_refuses_a_source_sha_not_reachable_from_main(remote, tmp_path):
    r, sha = remote
    _d, tip = _bootstrap(r, tmp_path, sha)
    # 在别的分支上造一个提交：真实存在、可 fetch，但不可达 main
    work = tmp_path / "fork"
    kit.git("clone", "--quiet", str(r), str(work))
    (work / "fork.txt").write_text("fork\n", encoding="utf-8")
    kit.git("add", "fork.txt", cwd=work)
    kit.git("commit", "--quiet", "-m", "fork", cwd=work)
    fork_sha = kit.git("rev-parse", "HEAD", cwd=work)
    kit.git("push", "--quiet", str(r), "HEAD:refs/heads/feature", cwd=work)
    new = _staging(tmp_path, "new", sha=fork_sha, version="0.14.0")
    rc = _run(
        [
            "promote",
            "--remote",
            str(r),
            "--staging",
            str(new),
            "--source-sha",
            fork_sha,
            "--expected-remote-sha",
            tip,
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == pub.EXIT_REFUSED
    assert kit.remote_tip(r) == tip


def test_a_hand_edited_branch_is_refused_before_anything_is_pushed(remote, tmp_path):
    r, sha = remote
    _d, tip = _bootstrap(r, tmp_path, sha)
    work = tmp_path / "hand"
    kit.git("clone", "--quiet", "--branch", BR, str(r), str(work))
    (work / "codex-plugin" / "README.md").write_text("edited by hand\n", encoding="utf-8")
    kit.git("commit", "--quiet", "-am", "hand edit", cwd=work)
    kit.git("push", "--quiet", str(r), f"HEAD:refs/heads/{BR}", cwd=work)
    hand_tip = kit.remote_tip(r)
    new = _staging(tmp_path, "new", sha=sha, version="0.14.0")
    rc = _run(
        [
            "promote",
            "--remote",
            str(r),
            "--staging",
            str(new),
            "--source-sha",
            sha,
            "--expected-remote-sha",
            hand_tip,
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == pub.EXIT_REFUSED
    assert kit.remote_tip(r) == hand_tip


def test_engine_availability_gates_promotion(remote, tmp_path, monkeypatch):
    r, sha = remote
    _d, tip = _bootstrap(r, tmp_path, sha)
    new = _staging(tmp_path, "new", sha=sha, version="0.14.0")
    seen: list[str] = []

    def fake_http(url, fetch=None):
        seen.append(url)
        return ("pypi" not in url), "HTTP 404" if "pypi" in url else "HTTP 200"

    monkeypatch.setattr(pub, "_http_ok", fake_http)
    rc = _run(
        [
            "promote",
            "--remote",
            str(r),
            "--staging",
            str(new),
            "--source-sha",
            sha,
            "--expected-remote-sha",
            tip,
            "--yes",
        ]
    )
    assert rc == pub.EXIT_REFUSED
    assert kit.remote_tip(r) == tip
    assert any("releases/tags/v0.14.0" in u for u in seen) and any(
        "pypi.org/pypi/tavotto/0.14.0" in u for u in seen
    )

    monkeypatch.setattr(pub, "_http_ok", lambda url, fetch=None: (True, "HTTP 200"))
    rc = _run(
        [
            "promote",
            "--remote",
            str(r),
            "--staging",
            str(new),
            "--source-sha",
            sha,
            "--expected-remote-sha",
            tip,
            "--yes",
        ]
    )
    assert rc == 0
    rec = _receipt(r, kit.remote_tip(r))
    assert set(rec["engine_check"]["checks"]) == {"github-release", "pypi"}


def test_skipping_the_engine_check_needs_a_reason(remote, tmp_path):
    r, sha = remote
    d = _staging(tmp_path, "s", sha=sha)
    rc = _run(
        [
            "bootstrap",
            "--remote",
            str(r),
            "--staging",
            str(d),
            "--source-sha",
            sha,
            "--engine-check",
            "none",
            "--yes",
        ]
    )
    assert rc == pub.EXIT_REFUSED
    assert kit.remote_tip(r) is None


# ================================================================ 推送故障


def test_push_with_a_stale_lease_is_rejected(remote, tmp_path):
    r, sha = remote
    d, tip = _bootstrap(r, tmp_path, sha)
    repo = tmp_path / "wk"
    repo.mkdir()
    kit.git("init", "--quiet", cwd=repo)
    pub.fetch_branch(repo, str(r), BR)
    commit = pub.build_commit(
        repo,
        plugin_dir=d,
        receipt={
            "schema": 1,
            "branch": BR,
            "version": "0.13.0",
            "content_digest": "x",
            "source_sha": sha,
        },
        parent=tip,
        message="m",
    )
    outcome, _detail = pub.push(str(r), BR, commit, expected_old="0" * 40, repo=repo)
    assert outcome == "rejected"
    assert kit.remote_tip(r) == tip


def test_readback_decides_landed_not_landed_moved(monkeypatch):
    monkeypatch.setattr(pub, "remote_tip", lambda remote, branch: "new")
    assert pub.readback("r", BR, "new", "old") == "landed"
    monkeypatch.setattr(pub, "remote_tip", lambda remote, branch: "old")
    assert pub.readback("r", BR, "new", "old") == "not_landed"
    monkeypatch.setattr(pub, "remote_tip", lambda remote, branch: "someone")
    assert pub.readback("r", BR, "new", "old") == "moved"


def test_an_unknown_push_outcome_is_resolved_by_reading_back(remote, tmp_path, monkeypatch):
    """推送响应丢了但其实落地了：读回判 landed，退出 0 且远端只有一个新提交。"""
    r, sha = remote
    _d, tip = _bootstrap(r, tmp_path, sha)
    new = _staging(tmp_path, "new", sha=sha, version="0.14.0")
    real_push = pub.push

    def flaky_push(remote_url, branch, commit, *, expected_old, repo):
        real_push(remote_url, branch, commit, expected_old=expected_old, repo=repo)
        return "unknown", "connection reset after push"

    monkeypatch.setattr(pub, "push", flaky_push)
    rc = _run(
        [
            "promote",
            "--remote",
            str(r),
            "--staging",
            str(new),
            "--source-sha",
            sha,
            "--expected-remote-sha",
            tip,
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == 0
    assert kit.git("rev-parse", f"{kit.remote_tip(r)}^", cwd=r) == tip


def test_a_push_that_never_landed_is_retryable(remote, tmp_path, monkeypatch):
    r, sha = remote
    _d, tip = _bootstrap(r, tmp_path, sha)
    new = _staging(tmp_path, "new", sha=sha, version="0.14.0")
    monkeypatch.setattr(pub, "push", lambda *a, **k: ("unknown", "timeout"))
    rc = _run(
        [
            "promote",
            "--remote",
            str(r),
            "--staging",
            str(new),
            "--source-sha",
            sha,
            "--expected-remote-sha",
            tip,
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == pub.EXIT_NOT_LANDED
    assert kit.remote_tip(r) == tip


# ================================================================ rollback


def test_rollback_restores_an_old_snapshot_as_a_new_commit(remote, tmp_path):
    r, sha = remote
    d1, tip1 = _bootstrap(r, tmp_path, sha, version="0.13.0")
    new = _staging(tmp_path, "new", sha=sha, version="0.14.0", salt="v14")
    assert (
        _run(
            [
                "promote",
                "--remote",
                str(r),
                "--staging",
                str(new),
                "--source-sha",
                sha,
                "--expected-remote-sha",
                tip1,
                "--engine-check",
                "none",
                "--reason",
                "t",
                "--yes",
            ]
        )
        == 0
    )
    tip2 = kit.remote_tip(r)
    rc = _run(
        [
            "rollback",
            "--remote",
            str(r),
            "--to",
            tip1,
            "--expected-remote-sha",
            tip2,
            "--authorized-by",
            "maintainer",
            "--reason",
            "0.14.0 broke the canvas",
            "--yes",
        ]
    )
    assert rc == 0
    tip3 = kit.remote_tip(r)
    assert tip3 not in (tip1, tip2)
    assert kit.git("rev-parse", f"{tip3}^", cwd=r) == tip2, "回退也是快进，历史保留"
    rec = _receipt(r, tip3)
    assert rec["kind"] == "rollback" and rec["restores"] == tip1 and rec["version"] == "0.13.0"
    assert rec["authorization"] == {
        "authorized_by": "maintainer",
        "reason": "0.14.0 broke the canvas",
    }
    c = _clone(r, tmp_path / "c")
    assert pub.tree_digest(c, tip3) == stage.read_manifest(d1)["content_digest"]


def test_rollback_needs_authorization_and_the_current_tip(remote, tmp_path):
    r, sha = remote
    _d1, tip1 = _bootstrap(r, tmp_path, sha, version="0.13.0")
    new = _staging(tmp_path, "new", sha=sha, version="0.14.0")
    assert (
        _run(
            [
                "promote",
                "--remote",
                str(r),
                "--staging",
                str(new),
                "--source-sha",
                sha,
                "--expected-remote-sha",
                tip1,
                "--engine-check",
                "none",
                "--reason",
                "t",
                "--yes",
            ]
        )
        == 0
    )
    tip2 = kit.remote_tip(r)
    assert (
        _run(
            [
                "rollback",
                "--remote",
                str(r),
                "--to",
                tip1,
                "--expected-remote-sha",
                tip2,
                "--reason",
                "x",
                "--yes",
            ]
        )
        == pub.EXIT_REFUSED
    )
    assert (
        _run(
            [
                "rollback",
                "--remote",
                str(r),
                "--to",
                tip1,
                "--expected-remote-sha",
                tip1,
                "--authorized-by",
                "m",
                "--reason",
                "x",
                "--yes",
            ]
        )
        == pub.EXIT_REFUSED
    )
    assert (
        _run(
            [
                "rollback",
                "--remote",
                str(r),
                "--to",
                sha,
                "--expected-remote-sha",
                tip2,
                "--authorized-by",
                "m",
                "--reason",
                "x",
                "--yes",
            ]
        )
        == pub.EXIT_REFUSED
    )
    assert kit.remote_tip(r) == tip2


# ================================================================ legacy bootstrap


def test_legacy_bootstrap_keeps_the_zip_bytes_and_records_the_external_checksum(remote, tmp_path):
    r, sha = remote
    src = _staging(tmp_path, "legacy-src", sha=sha, version="0.12.0")
    (src / stage.BUILD_MANIFEST).unlink()
    (src / "LICENSE").unlink()
    z = tmp_path / "codex-plugin-0.12.0.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                zf.write(p, f"codex-plugin/{p.relative_to(src).as_posix()}")
    digest = stage.sha256_file(z)
    rc = _run(
        [
            "bootstrap",
            "--remote",
            str(r),
            "--legacy-zip",
            str(z),
            "--legacy-sha256",
            "0" * 64,
            "--legacy-asset-url",
            "https://example/x.zip",
            "--release-tag",
            "v0.12.0",
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == pub.EXIT_REFUSED, "外部校验和不符必须拒绝"
    assert kit.remote_tip(r) is None
    rc = _run(
        [
            "bootstrap",
            "--remote",
            str(r),
            "--legacy-zip",
            str(z),
            "--legacy-sha256",
            digest,
            "--legacy-asset-url",
            "https://example/x.zip",
            "--release-tag",
            "v0.12.0",
            "--engine-check",
            "none",
            "--reason",
            "t",
            "--yes",
        ]
    )
    assert rc == 0
    tip = kit.remote_tip(r)
    rec = _receipt(r, tip)
    assert rec["kind"] == "bootstrap" and rec["version"] == "0.12.0"
    assert rec["legacy_bootstrap"]["sha256"] == digest
    assert rec["legacy_bootstrap"]["release_tag"] == "v0.12.0"
    assert rec["plugin_build"] is None
    names = kit.git("ls-tree", "-r", "--name-only", tip, cwd=r).splitlines()
    assert "codex-plugin/plugin-build.json" not in names, "legacy 包内不许多出清单"
    with zipfile.ZipFile(z) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            blob = subprocess.run(
                ["git", "-C", str(r), "show", f"{tip}:{info.filename}"],
                capture_output=True,
                check=True,
            ).stdout
            assert blob == zf.read(info), info.filename


# ================================================================ 两种分发是同一份内容


def _clone(remote: Path, dest: Path, *extra: str) -> Path:
    kit.git("clone", "--quiet", *extra, "--branch", BR, str(remote), str(dest))
    return dest


def _files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }


def test_zip_and_branch_carry_identical_bytes(remote, tmp_path):
    r, sha = remote
    d, tip = _bootstrap(r, tmp_path, sha)
    z = stage.write_zip(d, tmp_path / "p.zip")
    unpacked = stage.unpack_zip(z, tmp_path / "unz")
    checkout = _clone(r, tmp_path / "co") / "codex-plugin"
    assert _files(unpacked) == _files(checkout)


def test_an_autocrlf_checkout_still_matches_the_zip(remote, tmp_path):
    """Windows Git 默认 autocrlf=true：分支根的 `* -text` 让检出字节与 zip 逐字节相同。"""
    r, sha = remote
    d, tip = _bootstrap(r, tmp_path, sha)
    checkout = _clone(r, tmp_path / "crlf", "-c", "core.autocrlf=true") / "codex-plugin"
    assert _files(checkout) == _files(
        stage.unpack_zip(stage.write_zip(d, tmp_path / "p.zip"), tmp_path / "unz")
    )
    # 分支里的 blob 也必须与 staging 逐字节相同：提交时没被 autocrlf 改写，检出时也没被
    # 改写（staging 在 Windows runner 上本身就是 CRLF 检出的，所以这里不断言「没有 CRLF」，
    # 断言的是三处同一份字节）
    blob = subprocess.run(
        ["git", "-C", str(r), "show", f"{tip}:codex-plugin/.mcp.json"],
        capture_output=True,
        check=True,
    ).stdout
    assert blob == (d / ".mcp.json").read_bytes() == (checkout / ".mcp.json").read_bytes()


def test_a_codex_style_sparse_clone_gets_the_whole_plugin(remote, tmp_path):
    """按 Codex 客户端的克隆形状（filter=blob:none + 稀疏 no-cone `codex-plugin`）拿到的目录，
    验证通过且与 staging 一致——dotfiles 一个不少。"""
    r, sha = remote
    d, tip = _bootstrap(r, tmp_path, sha)
    dest = tmp_path / "sparse"
    kit.git(
        "clone", "--quiet", "--filter=blob:none", "--sparse", "--no-checkout", str(r), str(dest)
    )
    kit.git("sparse-checkout", "set", "--no-cone", "--", "codex-plugin", cwd=dest)
    kit.git("checkout", "--quiet", BR, cwd=dest)
    plugin = dest / "codex-plugin"
    assert (
        stage.verify_dir(plugin, expect_content_digest=stage.read_manifest(d)["content_digest"])
        == []
    )
    assert _files(plugin) == _files(d)


def test_a_sparse_autocrlf_clone_is_byte_identical_too(remote, tmp_path):
    """稀疏检出时 `.gitattributes` 不在工作区，git 要从索引读它——这条钉住那件事。"""
    r, sha = remote
    d, tip = _bootstrap(r, tmp_path, sha)
    dest = tmp_path / "sparse-crlf"
    kit.git(
        "clone",
        "--quiet",
        "-c",
        "core.autocrlf=true",
        "--filter=blob:none",
        "--sparse",
        "--no-checkout",
        str(r),
        str(dest),
    )
    kit.git(
        "-c",
        "core.autocrlf=true",
        "sparse-checkout",
        "set",
        "--no-cone",
        "--",
        "codex-plugin",
        cwd=dest,
    )
    kit.git("-c", "core.autocrlf=true", "checkout", "--quiet", BR, cwd=dest)
    assert _files(dest / "codex-plugin") == _files(d)


def test_inspect_reports_the_remote_state(remote, tmp_path, capsys):
    r, sha = remote
    assert _run(["inspect", "--remote", str(r)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["tip"] is None and out["receipt"] is None
    _d, tip = _bootstrap(r, tmp_path, sha)
    capsys.readouterr()
    assert _run(["inspect", "--remote", str(r)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (
        out["tip"] == tip
        and out["receipt"]["version"]
        and out["tree_digest"] == out["receipt"]["content_digest"]
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")
def test_publisher_never_executes_plugin_code(remote, tmp_path, monkeypatch):
    """发布器只搬字节：往插件里放一个会写哨兵文件的 sitecustomize，发布全程它不该被执行。"""
    r, sha = remote
    d = _staging(tmp_path, "s", sha=sha)
    sentinel = tmp_path / "executed"
    (d / "mcp" / "sitecustomize.py").write_text(
        f"open({str(sentinel)!r}, 'w').write('x')\n", encoding="utf-8"
    )
    # 重写清单让它「合法」
    kit.synthetic_staging(d, source_sha=sha)
    monkeypatch.setenv("PYTHONPATH", str(d / "mcp"))
    assert (
        _run(
            [
                "bootstrap",
                "--remote",
                str(r),
                "--staging",
                str(d),
                "--source-sha",
                sha,
                "--engine-check",
                "none",
                "--reason",
                "t",
                "--yes",
            ]
        )
        == 0
    )
    assert not sentinel.exists()
    monkeypatch.delenv("PYTHONPATH")
    assert os.environ.get("PYTHONPATH") is None
