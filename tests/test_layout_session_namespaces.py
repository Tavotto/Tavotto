"""同文件会话命名空间：主体/归属/清理/未来格式均以当前文档格式为主语。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tavotto.engine import atomicio, layoutsession as sessions


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    directory = tmp_path / "data"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(directory))
    yield directory
    assert sessions._NAMESPACE.get() is None


def _raw():
    return json.loads(sessions.state_path().read_bytes())


def _install(value):
    atomicio.write_json(sessions.state_path(), value, indent=1)


def test_default_write_bytes_and_paths_are_unchanged_before_namespace_use(data_dir, monkeypatch):
    monkeypatch.setattr(sessions.time, "time", lambda: 1.0)
    sessions.set_last("/p", "d", "Name")
    expected = {
        "version": 1,
        "projects": {"/p": {"last": {"doc_id": "d", "name": "Name", "at": 1000}}},
        "no_project": {},
        "owners": {},
        "owner_revisions": {},
    }
    assert sessions.state_path().read_bytes() == json.dumps(
        expected, ensure_ascii=False, indent=1, allow_nan=False
    ).encode("utf-8")
    assert sessions.state_path() == data_dir / "state/layout-sessions.json"
    assert sessions.lock_path() == data_dir / "state/layout-sessions.lock"
    assert "document_namespaces" not in _raw()


@pytest.mark.parametrize("project", ["/project", None])
def test_same_doc_id_and_project_can_have_independent_pending_owner_and_durable_views(
    data_dir, project
):
    sessions.set_last(project, "d_same", "Legacy", slot_ok=False)
    sessions.commit_slot("d_same", project, "legacy-revision")
    with sessions.using_namespace("v4"):
        assert sessions.last_for(project) is None and sessions.owners() == {}
        sessions.set_last(project, "d_same", "Chart", slot_ok=False)
        assert sessions.commit_slot("d_same", project, "chart-revision")["promoted"] is True
        view = sessions.view_for(project, lambda _: "chart-revision")
        assert view["last"]["name"] == "Chart"
        assert view["last"]["revision"] == "chart-revision"
        assert sessions.owner_verdict("d_same", project, "legacy-revision") == "unverified"
    assert sessions.view_for(project, lambda _: "legacy-revision")["last"]["name"] == "Legacy"
    assert sessions.owner_verdict("d_same", project, "legacy-revision") == "this"
    raw = _raw()
    assert raw["owner_revisions"]["d_same"] == "legacy-revision"
    assert raw["document_namespaces"]["v4"]["owner_revisions"]["d_same"] == "chart-revision"
    assert raw["document_namespaces"]["v4"]["version"] == 1


def test_context_nesting_exception_and_thread_do_not_leak_the_format(data_dir):
    sessions.record_owner("d_legacy", "/p")
    with pytest.raises(RuntimeError), sessions.using_namespace("v4"):
        sessions.record_owner("d_chart", "/p")
        with sessions.using_namespace(None):
            assert sessions.owners() == {"d_legacy": "/p"}
        observed = []
        thread = threading.Thread(target=lambda: observed.append(sessions.owners()))
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert observed == [{"d_legacy": "/p"}]
        assert sessions.owners() == {"d_chart": "/p"}
        raise RuntimeError("leave context")
    assert sessions.owners() == {"d_legacy": "/p"}


@pytest.mark.parametrize("bad", ["legacy", "v5", "", True, 4, {}, []])
def test_unknown_namespace_never_selects_legacy_by_accident(data_dir, bad):
    with sessions.using_namespace("v4"):
        with pytest.raises(ValueError), sessions.using_namespace(bad):
            pytest.fail("unknown namespace entered")
        assert sessions._NAMESPACE.get() == "v4"
    assert not sessions.state_path().exists()


@pytest.mark.parametrize("namespace,other", [(None, "v4"), ("v4", None)])
def test_pruning_delete_and_forget_operate_only_on_current_namespace(data_dir, namespace, other):
    with sessions.using_namespace(other):
        sessions.set_last("/p", "d_keep", "Protected")
        sessions.commit_slot("d_keep", "/p", "keep")
        before = sessions.snapshot()
    with sessions.using_namespace(namespace):
        sessions.record_owner("d_stale", "/p", "stale")
        assert sessions.drop_owners_not_in(set(), lambda _: False) == 1
        sessions.forget_documents(["d_keep"])
        assert sessions.delete_slot("d_keep", lambda: True)
        assert sessions.owners() == {}
    with sessions.using_namespace(other):
        assert sessions.snapshot() == before


@pytest.mark.parametrize("version", [0, -1, 2, None, True, 1.0, "1"])
def test_unknown_namespace_version_is_readonly_and_preserves_both_files(data_dir, version):
    raw = {
        "version": 1,
        "document_namespaces": {"v4": {"version": version, "owners": {"d_future": "/p"}}},
    }
    _install(raw)
    sessions.backup_path().write_bytes(b"unmodified backup")
    before = sessions.state_path().read_bytes(), sessions.backup_path().read_bytes()
    with sessions.using_namespace("v4"):
        assert sessions.read_status() == "degraded"
        state, status = sessions.read_readonly()
        assert state["owners"] == {"d_future": "/p"} and status == "degraded"
        with pytest.raises(sessions.SessionStateUnavailable):
            sessions.record_owner("d_new", "/p")
    assert (sessions.state_path().read_bytes(), sessions.backup_path().read_bytes()) == before
    # 更新旧格式元数据可原样保留尚不认识的v4视图，而不能重写它的版本。
    sessions.record_owner("d_legacy", "/old")
    assert _raw()["document_namespaces"] == raw["document_namespaces"]


@pytest.mark.parametrize("container", [[], "unknown", {"v4": None}, {"v4": []}])
def test_malformed_namespace_container_is_not_overwritten_as_empty(data_dir, container):
    _install({"version": 1, "document_namespaces": container})
    before = sessions.state_path().read_bytes()
    with sessions.using_namespace("v4"):
        assert sessions.read_readonly()[1] == "degraded"
        with pytest.raises(sessions.SessionStateUnavailable):
            sessions.set_last("/p", "d", "Name")
    assert sessions.state_path().read_bytes() == before


@pytest.mark.parametrize("namespace", [None, "v4"])
def test_missing_primary_never_replaces_the_only_future_namespace_backup(data_dir, namespace):
    backup = {
        "version": 1,
        "document_namespaces": {"v4": {"version": 2, "owners": {"d_future": "/p"}}},
    }
    atomicio.write_json(sessions.backup_path(), backup)
    before = sessions.backup_path().read_bytes()
    with sessions.using_namespace(namespace):
        assert sessions.read_status() == "degraded"
        with pytest.raises(sessions.SessionStateUnavailable):
            sessions.record_owner("d_new", "/p")
    assert not sessions.state_path().exists()
    assert sessions.backup_path().read_bytes() == before


def test_outer_future_version_blocks_v4_writes_and_readonly_discovery_has_no_side_effect(data_dir):
    _install({"version": 2, "document_namespaces": {"v4": {"version": 1, "owners": {"d": "/p"}}}})
    before = {p.name: p.read_bytes() for p in sessions.state_path().parent.iterdir()}
    with sessions.using_namespace("v4"):
        assert sessions.read_readonly()[1] == "degraded"
        assert sessions.read_status() == "degraded"
    assert {p.name: p.read_bytes() for p in sessions.state_path().parent.iterdir()} == before
    with sessions.using_namespace("v4"), pytest.raises(sessions.SessionStateUnavailable):
        sessions.record_owner("d_new", "/p")
    assert sessions.state_path().read_bytes() == before["layout-sessions.json"]


def test_v4_write_preserves_unknown_outer_sibling_and_group_fields(data_dir):
    future = {"version": 9, "payload": {"unknown": [1, 2, 3]}}
    _install(
        {
            "version": 1,
            "future_outer": {"keep": True},
            "document_namespaces": {
                "v5": future,
                "v4": {
                    "version": 1,
                    "future_inner": "keep",
                    "projects": {"/p": {"future_group": 42}},
                },
            },
        }
    )
    with sessions.using_namespace("v4"):
        sessions.set_last("/p", "d", "Name")
    raw = _raw()
    assert raw["future_outer"] == {"keep": True}
    assert raw["document_namespaces"]["v5"] == future
    assert raw["document_namespaces"]["v4"]["future_inner"] == "keep"
    assert raw["document_namespaces"]["v4"]["projects"]["/p"]["future_group"] == 42


def test_corrupt_primary_recovers_v4_from_the_same_backup_and_preserves_original(data_dir):
    with sessions.using_namespace("v4"):
        sessions.record_owner("d", "/p", "revision")
    sessions.state_path().write_bytes(b"{broken-primary")
    with sessions.using_namespace("v4"):
        assert sessions.read_status() == "recovered"
        assert sessions.owners() == {"d": "/p"}
        sessions.set_last("/p", "d", "Recovered")
    assert [
        p.read_bytes() for p in sessions.state_path().parent.glob("layout-sessions.corrupt-*.json")
    ] == [b"{broken-primary"]
    assert _raw()["document_namespaces"]["v4"]["owners"] == {"d": "/p"}


PROCESS = r"""
import pathlib,sys,time
sys.path.insert(0,sys.argv[1])
from tavotto.engine import layoutsession as ls
namespace=None if sys.argv[2]=='legacy' else 'v4'
go=pathlib.Path(sys.argv[3])
deadline=time.monotonic()+10
while not go.exists():
    assert time.monotonic()<deadline
    time.sleep(.01)
with ls.using_namespace(namespace):
    for i in range(5):
        doc='d_'+sys.argv[2]+'_'+str(i)
        ls.commit_slot(doc,'/p','rev'+str(i))
        ls.set_last('/p',doc,doc)
"""


def test_two_real_processes_share_one_file_lock_without_losing_either_namespace(data_dir, tmp_path):
    root = Path(__file__).resolve().parents[1]
    marker = tmp_path / "go"
    processes = []
    try:
        for namespace in ("legacy", "v4"):
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        PROCESS,
                        str(root / "src"),
                        namespace,
                        str(marker),
                    ],
                    env=dict(os.environ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            )
        marker.write_bytes(b"go")
        for process in processes:
            _out, errors = process.communicate(timeout=30)
            assert process.returncode == 0, errors
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
    for namespace in (None, "v4"):
        with sessions.using_namespace(namespace):
            name = namespace or "legacy"
            assert sessions.owners() == {f"d_{name}_{i}": "/p" for i in range(5)}
            assert sessions.last_for("/p")["doc_id"] == f"d_{name}_4"
    assert {p.name for p in sessions.state_path().parent.iterdir()} == {
        "layout-sessions.json",
        "layout-sessions.json.bak",
        "layout-sessions.lock",
    }
