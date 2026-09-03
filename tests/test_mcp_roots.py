"""可信 workspace-root 权威的独立安全矩阵。"""

import ntpath
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto_mcp.roots import (  # noqa: E402
    DISPOSITIONS,
    ROOTS_ENV,
    WORKSPACE_ENVS,
    WORKSPACE_FAILURES,
    RootAuthority,
    _windows_absolute_realpath,
)


@pytest.fixture
def authority(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.chdir(plugin)
    monkeypatch.delenv(ROOTS_ENV, raising=False)
    for name in WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    return RootAuthority(str(plugin))


def test_explicit_configuration_wins_over_protocol_roots(authority, tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    host = tmp_path / "host"
    explicit.mkdir()
    host.mkdir()
    authority.observe_client("2025-11-25", {"roots": {}}, {"name": "codex", "version": "1"})
    authority.accept_protocol_result({"roots": [{"uri": host.as_uri()}]})
    monkeypatch.setenv(ROOTS_ENV, str(explicit))

    snap = authority.snapshot()
    assert snap.source == "explicit_env"
    assert snap.roots == (str(explicit.resolve()),)
    assert authority.protocol_request_needed() is False


def test_windows_absolute_realpath_resolves_without_reading_cwd():
    calls = []

    def resolver(path):
        calls.append(path)
        if ntpath.normcase(path) == ntpath.normcase(r"C:\workspace"):
            return r"\\?\C:\Workspace"
        raise FileNotFoundError(2, "missing")

    result = _windows_absolute_realpath(r"C:\workspace\new\figure.svg", resolver)
    assert result == r"C:\Workspace\new\figure.svg"
    assert calls == [
        r"C:\workspace\new\figure.svg",
        r"C:\workspace\new",
        r"C:\workspace",
    ]


def test_windows_absolute_realpath_normalises_unc_prefix():
    def resolver(_path):
        return r"\\?\UNC\server\share\Workspace"

    assert (
        _windows_absolute_realpath(
            r"\\server\share\workspace",
            resolver,
        )
        == r"\\server\share\Workspace"
    )


def test_windows_absolute_realpath_never_downgrades_permission_errors():
    def resolver(_path):
        raise PermissionError(13, "denied")

    with pytest.raises(PermissionError):
        _windows_absolute_realpath(r"C:\workspace\secret", resolver)


def test_explicit_absolute_root_survives_a_deleted_cwd(authority, tmp_path, monkeypatch):
    configured = tmp_path / "configured"
    configured.mkdir()
    resolved = str(configured.resolve())
    monkeypatch.setenv(ROOTS_ENV, str(configured))

    def deleted_cwd():
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(os, "getcwd", deleted_cwd)
    snap = authority.snapshot()
    assert snap.source == "explicit_env"
    assert snap.roots == (resolved,)
    assert snap.warnings == ()


def test_even_explicit_configuration_rejects_fs_root_and_plugin_cache(authority, monkeypatch):
    monkeypatch.setenv(ROOTS_ENV, os.pathsep.join([os.path.abspath(os.sep), authority.plugin_dir]))
    snap = authority.snapshot()
    assert snap.source == "explicit_env"
    assert snap.roots == ()
    assert len(snap.warnings) == 2


def test_empty_protocol_result_is_authoritative_and_does_not_fall_back_to_cwd(
    authority, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    authority.observe_client("2025-11-25", {"roots": {}}, {})
    authority.accept_protocol_result({"roots": []})

    snap = authority.snapshot()
    assert snap.source == "mcp_roots"
    assert snap.roots == ()


def test_protocol_roots_accept_only_existing_local_directories(authority, tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    not_a_dir = tmp_path / "figure.py"
    not_a_dir.write_text("pass\n", encoding="utf-8")
    authority.observe_client("2025-11-25", {"roots": {}}, {})
    authority.accept_protocol_result(
        {
            "roots": [
                {"uri": good.as_uri()},
                {"uri": "https://example.com/workspace"},
                {"uri": not_a_dir.as_uri()},
                {"uri": Path(os.path.abspath(os.sep)).as_uri()},
            ]
        }
    )

    snap = authority.snapshot()
    assert snap.source == "mcp_roots"
    assert snap.roots == (str(good.resolve()),)
    assert len(snap.warnings) == 3


def test_plugin_cache_is_never_accepted_as_a_protocol_workspace(authority, tmp_path):
    nested = Path(authority.plugin_dir) / "mcp"
    nested.mkdir()
    authority.observe_client("2025-11-25", {"roots": {}}, {})
    authority.accept_protocol_result({"roots": [{"uri": nested.as_uri()}]})

    snap = authority.snapshot()
    assert snap.roots == ()
    assert any("插件缓存" in warning for warning in snap.warnings)


def test_roots_changed_becomes_pending_until_a_fresh_atomic_result(authority, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    authority.observe_client("2025-11-25", {"roots": {"listChanged": True}}, {})
    authority.accept_protocol_result({"roots": [{"uri": first.as_uri()}]})
    first_generation = authority.snapshot().generation

    authority.mark_protocol_stale()
    pending = authority.snapshot()
    assert pending.source == "mcp_roots_pending" and pending.roots == ()
    assert authority.protocol_request_needed() is True

    authority.accept_protocol_result({"roots": [{"uri": second.as_uri()}]})
    refreshed = authority.snapshot()
    assert refreshed.roots == (str(second.resolve()),)
    assert refreshed.generation > first_generation


def test_capability_probe_reports_exact_client_claim(authority):
    authority.observe_client(
        "2025-06-18",
        {
            "roots": {"listChanged": False},
            "elicitation": {},
            "experimental": {"vendor.example": {}},
        },
        {"name": "Codex Desktop", "version": "42"},
    )
    report = authority.diagnostics()
    assert report["client"] == {
        "name": "Codex Desktop",
        "version": "42",
        "protocol_version": "2025-06-18",
        "capabilities": {
            "advertised": ["elicitation", "experimental", "roots"],
            "roots": True,
            "elicitation": True,
            "sampling": False,
        },
    }
    assert report["mcp_roots"]["advertised"] is True
    assert report["mcp_roots"]["state"] == "pending"
    assert report["mcp_roots"]["compatibility_only"] is True


def test_elicitation_candidate_is_not_authority_until_user_accepts(authority, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    authority.observe_client("2025-06-18", {"elicitation": {}}, {"name": "codex", "version": "1"})

    candidate = authority.user_binding_candidate(str(project))
    assert candidate == str(project.resolve())
    assert authority.snapshot().roots == ()
    assert authority.diagnostics()["workspace_confirmation"]["state"] == "available"

    assert authority.accept_user_binding(candidate) is True
    snap = authority.snapshot()
    assert snap.source == "user_elicitation"
    assert snap.roots == (str(project.resolve()),)
    assert authority.diagnostics()["workspace_confirmation"] == {
        "advertised": True,
        "state": "accepted",
        "root": str(project.resolve()),
        "error": None,
        "lifetime": "mcp_connection",
    }


def test_user_binding_requires_absolute_existing_non_plugin_directory(authority, tmp_path):
    authority.observe_client("2025-06-18", {"elicitation": {}}, {})
    assert authority.user_binding_candidate("relative/project") is None
    assert authority.user_binding_candidate(str(tmp_path / "missing")) is None
    assert authority.user_binding_candidate(authority.plugin_dir) is None
    assert authority.user_binding_candidate(os.path.abspath(os.sep)) is None


def test_explicit_or_protocol_authority_cannot_be_expanded_by_elicitation(
    authority, tmp_path, monkeypatch
):
    configured = tmp_path / "configured"
    outside = tmp_path / "outside"
    configured.mkdir()
    outside.mkdir()
    authority.observe_client("2025-06-18", {"elicitation": {}}, {})
    monkeypatch.setenv(ROOTS_ENV, str(configured))
    assert authority.user_binding_candidate(str(outside)) is None

    monkeypatch.delenv(ROOTS_ENV)
    authority.observe_client(
        "2025-06-18",
        {
            "roots": {},
            "elicitation": {},
        },
        {},
    )
    assert authority.user_binding_candidate(str(outside)) is None


def test_user_binding_expires_on_a_new_initialize(authority, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    authority.observe_client("2025-06-18", {"elicitation": {}}, {})
    candidate = authority.user_binding_candidate(str(project))
    assert candidate and authority.accept_user_binding(candidate)

    authority.observe_client("2025-06-18", {"elicitation": {}}, {})
    assert authority.snapshot().roots == ()
    assert authority.diagnostics()["workspace_confirmation"]["state"] == "available"


# --------------------- #173：授权失败的分档是一张闭表 ------------------------
def test_every_failure_bucket_is_a_stable_code_with_a_next_step():
    """表里的每一档都要有稳定 code、闭集里的处置、和一句能照做的下一步。

    **code 不许当文案**：它是给机器的稳定标识，念给用户听等于没说。
    """
    for key, failure in WORKSPACE_FAILURES.items():
        assert failure.code == key
        assert failure.disposition in DISPOSITIONS, key
        assert failure.summary.strip() and failure.next_step.strip(), key
        assert failure.code not in failure.next_step, key
        assert failure.code not in failure.summary, key
    # 一个 code 只能有一份措辞
    assert len({f.next_step for f in WORKSPACE_FAILURES.values()}) == len(WORKSPACE_FAILURES)


def test_the_four_issue_buckets_map_to_four_different_dispositions():
    """issue #173 点名的四种情况，处置必须两两不同。"""
    four = [
        WORKSPACE_FAILURES["workspace_confirmation_declined"],
        WORKSPACE_FAILURES["workspace_confirmation_no_response"],
        WORKSPACE_FAILURES["path_out_of_scope"],
        WORKSPACE_FAILURES["no_workspace_root"],
    ]
    assert len({f.disposition for f in four}) == 4
    assert len({f.code for f in four}) == 4


@pytest.mark.parametrize(
    "state,code,disposition",
    [
        ("available", "workspace_confirmation_required", "send_absolute_path"),
        ("declined", "workspace_confirmation_declined", "ask_user_again"),
        ("cancelled", "workspace_confirmation_cancelled", "ask_user_again"),
        ("no_response", "workspace_confirmation_no_response", "fix_host_wiring"),
        ("error", "workspace_confirmation_error", "fix_host_wiring"),
        ("stale", "workspace_confirmation_stale", "ask_user_again"),
        ("unsupported", "no_workspace_root", "configure_roots"),
    ],
)
def test_each_confirmation_state_selects_its_own_bucket(authority, state, code, disposition):
    authority.observe_client("2025-06-18", {"elicitation": {}}, {})
    if state != "available":
        authority.fail_user_binding("宿主没回来", state=state)
    failure = authority.failure()
    assert failure.code == code
    assert failure.disposition == disposition


@pytest.mark.parametrize(
    "state,code",
    [
        ("no_response", "workspace_roots_no_response"),
        ("error", "workspace_roots_error"),
    ],
)
def test_a_silent_roots_host_is_not_an_unconfigured_host(authority, state, code):
    """声明了 roots 却没给出目录 ≠ 宿主根本没配——下一步指向的地方不一样。"""
    authority.observe_client("2025-11-25", {"roots": {}}, {})
    authority.fail_protocol("宿主没回来", state=state)
    failure = authority.failure()
    assert failure.code == code
    assert failure.disposition == "fix_host_wiring"
    assert failure.code != WORKSPACE_FAILURES["no_workspace_root"].code
    # 没回应仍要在下一次 tools/call 重试，而不是从此不再问
    assert authority.protocol_request_needed() is True


def test_an_empty_but_answered_roots_list_is_a_configuration_problem(authority):
    authority.observe_client("2025-11-25", {"roots": {}}, {})
    authority.accept_protocol_result({"roots": []})
    assert authority.failure().code == "no_workspace_root"
    assert authority.failure().disposition == "configure_roots"


def test_a_directory_that_changes_during_confirmation_is_its_own_bucket(authority, tmp_path):
    """确认框显示之后目录没了：既不是用户拒绝，也不是宿主接线坏了。"""
    project = tmp_path / "project"
    project.mkdir()
    authority.observe_client("2025-06-18", {"elicitation": {}}, {})
    candidate = authority.user_binding_candidate(str(project))
    assert candidate
    project.rmdir()
    assert authority.accept_user_binding(candidate) is False
    failure = authority.failure()
    assert failure.code == "workspace_confirmation_stale"
    assert failure.disposition == "ask_user_again"


def test_diagnostics_expose_the_bucket_without_failing_a_call_first(authority, tmp_path):
    """体检里就看得见这一档——不必先让一次 open 失败才知道该找谁。"""
    project = tmp_path / "project"
    project.mkdir()
    authority.observe_client("2025-06-18", {"elicitation": {}}, {})
    authority.fail_user_binding("300s 内没有响应", state="no_response")
    assert authority.diagnostics()["authorization"] == {
        "code": "workspace_confirmation_no_response",
        "disposition": "fix_host_wiring",
        "next_step": WORKSPACE_FAILURES["workspace_confirmation_no_response"].next_step,
    }

    candidate = authority.user_binding_candidate(str(project))
    assert candidate and authority.accept_user_binding(candidate)
    assert authority.diagnostics()["authorization"] is None
