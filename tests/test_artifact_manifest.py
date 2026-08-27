"""发行产物清单的看护。

这份清单存在的理由是 2026-08-22 v0.9.1 发版时的 #63：SBOM 那步把
`dist/*.whl` 喂给了只认单个路径的 syft，syft 把那串字符原样当文件名。
根因不是那一处写错，是**七个下游步骤各自在猜产物叫什么**。

所以这里的用例大多在钉「猜错会怎样」，而不是「正常路径能跑通」。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import artifact_manifest as AM  # noqa: E402

SHA = "a" * 40


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    d.mkdir()
    (d / "tavotto-0.9.1-py3-none-any.whl").write_bytes(b"wheel-bytes")
    (d / "tavotto-0.9.1.tar.gz").write_bytes(b"sdist-bytes")
    return tmp_path


def _m(dist, *entries, version="0.9.1", sha=SHA):
    return AM.build(version, sha, list(entries), base=dist)


# ── 造清单 ────────────────────────────────────────────────────────────────


def test_build_records_role_path_hash_and_platform(dist):
    m = _m(
        dist,
        ("wheel", "dist/tavotto-0.9.1-py3-none-any.whl", "python"),
        ("sdist", "dist/tavotto-0.9.1.tar.gz", "python"),
    )
    assert m["schema"] == AM.SCHEMA and m["version"] == "0.9.1"
    assert m["source_sha"] == SHA
    wheel = next(a for a in m["artifacts"] if a["role"] == "wheel")
    assert wheel["sha256"] == hashlib.sha256(b"wheel-bytes").hexdigest()
    assert wheel["size"] == len(b"wheel-bytes")
    assert wheel["platform"] == "python"


def test_a_missing_file_fails_instead_of_being_recorded(dist):
    with pytest.raises(AM.ManifestError, match="文件不存在"):
        _m(dist, ("wheel", "dist/nope.whl", "python"))


def test_a_glob_can_never_enter_the_manifest(dist):
    """**#63 的根因，直接钉死。**

    glob 进了清单，就等于把「下游拿到一串通配符当文件名」这个 bug
    搬到了一个新地方——而且这次它会被七个消费者一起读到。
    """
    (dist / "dist" / "x.whl").write_bytes(b"x")
    with pytest.raises(AM.ManifestError, match="通配符"):
        _m(dist, ("wheel", "dist/*.whl", "python"))


def test_unknown_role_is_refused(dist):
    with pytest.raises(AM.ManifestError, match="不认识的 role"):
        _m(dist, ("linux-appimage", "dist/tavotto-0.9.1.tar.gz", "linux"))


def test_a_duplicate_unique_role_is_refused(dist):
    """两个 wheel 谁都不会报错，而用户装到的和我们验过的不是同一个。"""
    (dist / "dist" / "tavotto-0.9.1-py3-none-any2.whl").write_bytes(b"other")
    with pytest.raises(AM.ManifestError, match="恰好一个"):
        _m(
            dist,
            ("wheel", "dist/tavotto-0.9.1-py3-none-any.whl", "python"),
            ("wheel", "dist/tavotto-0.9.1-py3-none-any2.whl", "python"),
        )


def test_source_sha_must_look_like_a_sha(dist):
    with pytest.raises(AM.ManifestError, match="40 位"):
        _m(dist, ("wheel", "dist/tavotto-0.9.1-py3-none-any.whl", "python"), sha="v0.9.1")


# ── 校验 ──────────────────────────────────────────────────────────────────


def test_verify_passes_on_a_fresh_manifest(dist):
    m = _m(dist, ("wheel", "dist/tavotto-0.9.1-py3-none-any.whl", "python"))
    assert AM.verify(m, ["wheel"], dist, "0.9.1", SHA) == []


def test_verify_catches_a_swapped_artifact(dist):
    """产物在造好之后被换过——哈希对不上。

    这条挡的是「build 之后、发布之前有人重造了一遍」：文件名一样、内容不同，
    而 Release 上挂的与 lab gate 验过的于是不是同一个东西。
    """
    m = _m(dist, ("wheel", "dist/tavotto-0.9.1-py3-none-any.whl", "python"))
    (dist / "dist" / "tavotto-0.9.1-py3-none-any.whl").write_bytes(b"tampered")
    problems = AM.verify(m, ["wheel"], dist)
    assert any("sha256 对不上" in p for p in problems)


def test_verify_catches_a_missing_required_role(dist):
    m = _m(dist, ("wheel", "dist/tavotto-0.9.1-py3-none-any.whl", "python"))
    problems = AM.verify(m, ["wheel", "windows-installer"], dist)
    assert any("windows-installer" in p for p in problems)


def test_verify_catches_a_version_or_sha_mismatch(dist):
    m = _m(dist, ("wheel", "dist/tavotto-0.9.1-py3-none-any.whl", "python"))
    assert any("版本对不上" in p for p in AM.verify(m, [], dist, version="0.9.2"))
    assert any("source SHA 对不上" in p for p in AM.verify(m, [], dist, source_sha="b" * 40))


def test_verify_catches_a_deleted_file(dist):
    m = _m(dist, ("wheel", "dist/tavotto-0.9.1-py3-none-any.whl", "python"))
    (dist / "dist" / "tavotto-0.9.1-py3-none-any.whl").unlink()
    assert any("文件不在" in p for p in AM.verify(m, ["wheel"], dist))


# ── 合并（这是「同一个 SHA」真正被证明的地方）──────────────────────────────


def test_merge_refuses_two_legs_from_different_commits(dist):
    """**「同一个 tag」证明不了「同一个 commit」。**

    从前 release.yml 与 desktop-tauri.yml 由同一个 tag 各自触发、各自
    checkout。tag 是可移动的引用；两条腿之间它被指向别处，产物就分叉了，
    而没有任何一步会报错。合并这一步是唯一能挡住它的地方。
    """
    a = _m(dist, ("wheel", "dist/tavotto-0.9.1-py3-none-any.whl", "python"))
    b = _m(dist, ("sdist", "dist/tavotto-0.9.1.tar.gz", "python"), sha="b" * 40)
    with pytest.raises(AM.ManifestError, match="source SHA 不一致"):
        AM.merge([a, b])


def test_merge_refuses_two_legs_with_different_versions(dist):
    a = _m(dist, ("wheel", "dist/tavotto-0.9.1-py3-none-any.whl", "python"))
    b = _m(dist, ("sdist", "dist/tavotto-0.9.1.tar.gz", "python"), version="0.9.2")
    with pytest.raises(AM.ManifestError, match="版本不一致"):
        AM.merge([a, b])


def test_merge_enforces_unique_roles_across_legs(dist):
    """unique 约束在**合并之后**才真正生效——两条腿各出一个 wheel 也不行。"""
    a = _m(dist, ("wheel", "dist/tavotto-0.9.1-py3-none-any.whl", "python"))
    b = _m(dist, ("wheel", "dist/tavotto-0.9.1.tar.gz", "python"))
    with pytest.raises(AM.ManifestError, match="恰好一个"):
        AM.merge([a, b])


def test_merge_keeps_every_artifact(dist):
    a = _m(dist, ("wheel", "dist/tavotto-0.9.1-py3-none-any.whl", "python"))
    b = _m(dist, ("sdist", "dist/tavotto-0.9.1.tar.gz", "python"))
    m = AM.merge([a, b])
    assert {x["role"] for x in m["artifacts"]} == {"wheel", "sdist"}


# ── 取路径（单值 action 输入唯一的合法来源）──────────────────────────────


def test_path_returns_one_concrete_file(dist):
    m = _m(dist, ("wheel", "dist/tavotto-0.9.1-py3-none-any.whl", "python"))
    p = AM.path_of(m, "wheel")
    assert p == "dist/tavotto-0.9.1-py3-none-any.whl"
    assert "*" not in p and "?" not in p, "单值输入永远不该拿到通配符"


def test_path_refuses_when_the_role_is_ambiguous(dist):
    m = {
        "schema": 1,
        "version": "0.9.1",
        "source_sha": SHA,
        "artifacts": [
            {"role": "sbom", "path": "a.json", "sha256": "0" * 64, "platform": "python"},
            {"role": "sbom", "path": "b.json", "sha256": "1" * 64, "platform": "python"},
        ],
    }
    with pytest.raises(AM.ManifestError, match="取不出"):
        AM.path_of(m, "sbom")


def test_path_refuses_a_missing_role(dist):
    m = _m(dist, ("wheel", "dist/tavotto-0.9.1-py3-none-any.whl", "python"))
    with pytest.raises(AM.ManifestError, match="没有 role"):
        AM.path_of(m, "macos-installer")


# ── CLI ───────────────────────────────────────────────────────────────────


def test_cli_build_then_verify_then_path(dist, tmp_path, capsys):
    out = tmp_path / "artifact-manifest.json"
    rc = AM.main(
        [
            "build",
            "--version",
            "0.9.1",
            "--source-sha",
            SHA,
            "--base",
            str(dist),
            "--out",
            str(out),
            "--add",
            "wheel:dist/tavotto-0.9.1-py3-none-any.whl:python",
            "--add",
            "sdist:dist/tavotto-0.9.1.tar.gz:python",
        ]
    )
    assert rc == 0 and out.is_file()

    assert (
        AM.main(
            [
                "verify",
                str(out),
                "--require",
                "wheel,sdist",
                "--base",
                str(dist),
                "--version",
                "0.9.1",
                "--source-sha",
                SHA,
            ]
        )
        == 0
    )

    capsys.readouterr()
    assert AM.main(["path", str(out), "--role", "wheel"]) == 0
    assert capsys.readouterr().out.strip() == "dist/tavotto-0.9.1-py3-none-any.whl"


def test_cli_verify_exits_nonzero_and_says_why(dist, tmp_path, capsys):
    out = tmp_path / "m.json"
    AM.main(
        [
            "build",
            "--version",
            "0.9.1",
            "--source-sha",
            SHA,
            "--base",
            str(dist),
            "--out",
            str(out),
            "--add",
            "wheel:dist/tavotto-0.9.1-py3-none-any.whl:python",
        ]
    )
    (dist / "dist" / "tavotto-0.9.1-py3-none-any.whl").write_bytes(b"tampered")
    assert AM.main(["verify", str(out), "--base", str(dist)]) == 1
    assert "::error::" in capsys.readouterr().out


def test_cli_reports_a_bad_add_spec_instead_of_guessing(dist, tmp_path):
    assert (
        AM.main(
            [
                "build",
                "--version",
                "0.9.1",
                "--source-sha",
                SHA,
                "--base",
                str(dist),
                "--out",
                str(tmp_path / "m.json"),
                "--add",
                "wheel:dist/tavotto-0.9.1-py3-none-any.whl",
            ]
        )
        == 1
    )


def test_manifest_is_stable_across_runs(dist, tmp_path):
    """同样的输入必须产出**逐字节相同**的清单。

    清单自己也要进 checksum 与 provenance；不稳定的话每次重跑都会显示
    「产物变了」，而那条信号一旦开始撒谎就再没人看。
    """
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    for out in (a, b):
        AM.main(
            [
                "build",
                "--version",
                "0.9.1",
                "--source-sha",
                SHA,
                "--base",
                str(dist),
                "--out",
                str(out),
                "--add",
                "sdist:dist/tavotto-0.9.1.tar.gz:python",
                "--add",
                "wheel:dist/tavotto-0.9.1-py3-none-any.whl:python",
            ]
        )
    assert a.read_bytes() == b.read_bytes()
