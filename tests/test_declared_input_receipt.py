"""已捕获输入进入回执的资格；旧默认字节由改动前实现独立定格。"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from types import SimpleNamespace

import pytest

from tavotto.engine import execspec, executionfact, inputdeclaration, nativesession, receipt


def _json_sha256(value):
    wire = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(wire).hexdigest()


def _runtime():
    return {
        "runtime_report_version": 1,
        "report_origin": "build",
        "pid": 4242,
        "python_version": "3.13.11",
        "python_implementation": "CPython",
        "executable": "/private/env/bin/python",
        "prefix": "/private/env",
        "base_prefix": "/base/python",
        "platform": "unit-test",
        "machine": "fixture",
        "cwd": "/private/project/.sandbox",
        "argv0": "/private/project/plots/示例.py",
        "packages": {"matplotlib": "3.10.8", "numpy": "2.4.3"},
        "inputs": {
            "observation": "partial",
            "files": [{"path": "data/样本.csv", "sha256": "a" * 64}],
        },
    }


def _legacy_values(runtime):
    """固定路径仅在私有字段；不依赖当前平台的路径标准化或用户配置。"""
    spec = {
        "spec_version": 1,
        "profile": "safe",
        "target_kind": "script",
        "target": "plots/示例.py",
        "entry": "main",
        "argv": [],
        "argv_count": 2,
        "run_config": "rc_contract",
        "passthrough_savefig": False,
        "cwd_mode": "sandbox",
    }
    context = {
        "launch_context_version": 1,
        "profile": "safe",
        "target_kind": "script",
        "target": "plots/示例.py",
        "entry": "main",
        "argv": [],
        "argv_count": 2,
        "run_config": "rc_contract",
        "cwd_mode": "sandbox",
        "cwd_origin": "sandbox",
        "write_mode": "sandboxed",
        "passthrough_savefig": False,
        "grant": {"decided": True, "cwd_write": {"granted": False, "granted_at": None}},
    }
    return {
        "profile": "safe",
        "control_plane": "python_pool",
        "python_source": "project_venv",
        "generation": 3,
        "source_revision": "source-entry-sha1",
        "spec_stable": spec,
        "launch_context": context,
        "interpreter": "/private/env/bin/python",
        "project_root": "/private/project",
        "runtime": runtime,
        "descriptors": ({"stem": "figure", "asset_id": "runtime:plots/示例.py#figure"},),
        "pid_check": "ok" if runtime else None,
        "binding": {
            "revision": "binding-current-2",
            "expected": {"data/样本.csv": "a" * 64, "unread.csv": "b" * 64},
        },
        "launcher": {"kind": "conda", "prefix": "/private/env"},
        "session_env": {"generation": 2, "env_state": "env-token"},
        "instance": "0" * 16 + "1" * 32,
    }


# 来自 d0befe02 的实际旧 ExecutionReceipt，未以新实现推导期望值。
# 原文件 SHA256: 353502a780469b25ebe7bcd2bc79a45d8456064c91c2f1201dcd3666e2363c63。
# 同时定格无自报/有输入观察两种形态及公开、私有 JSON 的实际键顺序。
_LEGACY_ORACLE = {
    "partial": {
        "public_identity": "sha256:63aa407ba017387add9335b344c3f8bc1f4a1455854540603eb4db2fe45c5ef6",
        "private_key": "sha256:5e97f1b8680c75a43f2fe98319a61ed23a9f591e144b4cd0b71269f8f9f1633d",
        "receipt_id": "sha256:95a8ae1d6d5c490f9343ede10a957fe890ad5678208422055410227169a151b1",
        "public_wire": "387fb5c61344c0da655cbeffac4ebfb4113678a386dbdf2f81bcca3596e81ab0",
        "private_wire": "dc3ee8bdcbdb900227a4aab4967fb11c6f0425d0e05efe0dbaf8fe10c728595f",
        "facts_wire": "57473669db907899b553a25f6e3182c386f14573e98ef9995f6b99a920886792",
    },
    "observed": {
        "public_identity": "sha256:3b386c0649c4504a70a6bf5347ecf5b157c6015f0f82368d7ca1a2174aee448b",
        "private_key": "sha256:d77b042f6bccd48000129d7339f627b81a70540701b27c2c17de9507de7fbcbd",
        "receipt_id": "sha256:bab3dd1c70ba066bf4b84102be6834cf0f460b2acd3d7fdb570cd8a35e586c8c",
        "public_wire": "98ff666c04ffb4460daab7ce28cb1cf90d165750a177ca557aa043d9d02eaeca",
        "private_wire": "6c374bfb0729c2cc6734c2b208bf641c667a3b313d0c19d22a18b69e4e03a078",
        "facts_wire": "f470a168a54be7ca0d1a16ac274bb2f47f99d7b71f36a9ef0cb841e94944b868",
    },
}


@pytest.mark.parametrize("label", ["partial", "observed"])
@pytest.mark.parametrize("explicit_none", [False, True])
def test_missing_captured_inputs_keeps_prechange_identity_and_json_bytes(label, explicit_none):
    values = _legacy_values(_runtime() if label == "observed" else None)
    if explicit_none:
        values["captured_inputs"] = None
    result = receipt.ExecutionReceipt(**values)
    expected = _LEGACY_ORACLE[label]
    assert result.captured_inputs is None
    assert result.public_identity() == expected["public_identity"]
    assert result.private_invalidation_key() == expected["private_key"]
    assert result.receipt_id == expected["receipt_id"]
    assert _json_sha256(result.to_payload()) == expected["public_wire"]
    assert _json_sha256(result.to_payload(include_private=True)) == expected["private_wire"]
    assert _json_sha256(result.public_facts()) == expected["facts_wire"]
    for public in (result.to_payload(), result.public_facts()):
        assert "captured_inputs" not in public


def test_only_captured_summary_changes_semantic_identity_not_current_binding():
    original = receipt.ExecutionReceipt(**_legacy_values(_runtime()))
    first = {"version": 1, "revision": "sha256:" + "1" * 64}
    second = {"version": 1, "revision": "sha256:" + "2" * 64}
    captured = dataclasses.replace(original, captured_inputs=first)
    revised = dataclasses.replace(original, captured_inputs=second)
    assert (
        len({original.public_identity(), captured.public_identity(), revised.public_identity()})
        == 3
    )
    assert captured.public_facts()["captured_inputs"] == first
    assert captured.to_payload()["captured_inputs"] == first
    assert captured.to_payload(include_private=True)["captured_inputs"] == first
    changed_binding = dataclasses.replace(
        captured, binding={"revision": "new-export-time-binding", "expected": {}}
    )
    assert changed_binding.public_identity() == captured.public_identity()
    assert changed_binding.captured_inputs == first
    assert changed_binding.public_facts()["binding"] != captured.public_facts()["binding"]


def _worker(tmp_path, *, execution_id=None):
    spec = execspec.safe_spec(
        "plot.py", tmp_path, "main", interpreter="/env/python", sandbox=str(tmp_path / "box")
    )
    return SimpleNamespace(
        spec=spec,
        generation=3,
        script_sha1="source-entry-sha1",
        python_source="project_venv",
        child_pid=4242,
        execution_id=execution_id or executionfact.new_id(),
    )


def _from_worker(worker, response=None, *, binding=None):
    return receipt.from_worker(
        worker,
        {"runtime": _runtime()} if response is None else response,
        control_plane=receipt.CONTROL_PLANE_PYTHON,
        grant=None,
        binding=binding,
    )


def test_build_payload_and_user_binding_cannot_manufacture_captured_evidence(tmp_path):
    worker = _worker(tmp_path)
    summary = {"version": 1, "revision": "sha256:" + "a" * 64}
    binding = {
        "revision": summary["revision"],
        "expected": {"data/样本.csv": "a" * 64},
        "captured_inputs": summary,
    }
    response = {
        "runtime": {**_runtime(), "captured_inputs": summary},
        "execution_id": worker.execution_id,
        "captured_inputs": summary,
    }
    result = _from_worker(worker, response, binding=binding)
    assert result.binding_check()["matched"] is True
    assert result.captured_inputs is None
    assert "captured_inputs" not in result.public_facts()
    assert "captured_inputs" not in result.to_payload()


@pytest.mark.parametrize("bad", [None, {}, [], "sha256:" + "a" * 64, True])
def test_invalid_fact_never_changes_legacy_receipt(tmp_path, bad):
    worker = _worker(tmp_path)
    previous = _from_worker(worker)
    worker.last_captured_inputs = copy.deepcopy(bad)
    current = _from_worker(worker)
    assert current.captured_inputs is None
    assert current.to_payload(include_private=True) == previous.to_payload(include_private=True)


def _capture(tmp_path, execution_id, *, scope=None, data=b"1,2\n"):
    """用真实 begin/entered/finish 产事实；不复制 declaration/revision 算法。"""
    scope = scope or {"script": "plot.py", "cwd_mode": "sandbox", "run_config": None}
    script = tmp_path / scope["script"]
    script.write_bytes(b"def main():\n    pass\n")
    resource = tmp_path / "private-input-selection.csv"
    resource.write_bytes(data)
    sandbox = tmp_path / "box"
    sandbox.mkdir(exist_ok=True)
    candidate = {
        "version": 1,
        "scope": scope,
        "script_path": str(script),
        "files": [{"id": resource.name, "path": str(resource)}],
        "prefixes": [],
        "mappings": [],
        "rules": [],
        "limits": {"max_files": 100, "max_bytes": 1024 * 1024},
    }
    cwd = sandbox if scope["cwd_mode"] == "sandbox" else tmp_path
    capture = inputdeclaration.begin(
        candidate,
        script=scope["script"],
        script_path=str(script),
        cwd_mode=scope["cwd_mode"],
        run_config=scope["run_config"],
        actual_cwd=str(cwd),
        project_root=str(tmp_path),
        sandbox=str(sandbox),
        remap_rules=(),
    )
    assert capture is not None
    capture.entered(execution_id)
    fact = capture.finish(hashlib.sha256(script.read_bytes()).hexdigest())
    assert fact is not None
    assert inputdeclaration.validate_fact(fact, execution_id=execution_id) == fact
    return fact


@pytest.mark.parametrize(
    "control_plane", [receipt.CONTROL_PLANE_PYTHON, receipt.CONTROL_PLANE_WORKERD]
)
@pytest.mark.parametrize("response_has_id", [False, True])
def test_current_real_capture_enters_receipt_as_public_summary(
    tmp_path, control_plane, response_has_id
):
    worker = _worker(tmp_path)
    fact = _capture(tmp_path, worker.execution_id)
    worker.last_captured_inputs = fact
    response = {"runtime": _runtime()}
    if response_has_id:
        response["execution_id"] = worker.execution_id
    result = receipt.from_worker(worker, response, control_plane=control_plane, grant=None)
    summary = inputdeclaration.public_fact(fact)
    assert result.captured_inputs == summary
    assert set(summary) == {"version", "revision"}
    assert result.public_facts()["captured_inputs"] == summary
    assert result.to_payload()["captured_inputs"] == summary
    assert result.to_payload(include_private=True)["captured_inputs"] == summary
    assert (
        result.public_identity()
        != dataclasses.replace(result, captured_inputs=None).public_identity()
    )
    for public in (result.to_payload(), result.public_facts()):
        text = json.dumps(public, ensure_ascii=False)
        assert fact["execution_id"] not in text
        assert "private-input-selection.csv" not in text
        assert str(tmp_path) not in text
        assert "declaration" not in public


def test_changed_capture_contents_change_identity_in_same_execution(tmp_path):
    worker = _worker(tmp_path)
    worker.last_captured_inputs = _capture(tmp_path, worker.execution_id, data=b"1,2\n")
    first = _from_worker(worker)
    worker.last_captured_inputs = _capture(tmp_path, worker.execution_id, data=b"3,4\n")
    second = _from_worker(worker)
    assert first.instance == second.instance
    assert first.generation == second.generation
    assert first.runtime == second.runtime
    assert first.captured_inputs != second.captured_inputs
    assert first.public_identity() != second.public_identity()
    assert first.receipt_id != second.receipt_id


_FIRST = "0" * 16 + "a" * 32
_SECOND = "0" * 15 + "1" + "b" * 32


@pytest.mark.parametrize(
    ("response_id", "fact_id", "current_id"),
    [
        (_FIRST, _FIRST, _SECOND),  # 旧响应、旧证据不能借仍存活的同 generation。
        (_FIRST, _SECOND, _SECOND),
        (_SECOND, _FIRST, _SECOND),
    ],
)
def test_late_or_other_execution_fact_cannot_certify_current_receipt(
    tmp_path, response_id, fact_id, current_id
):
    worker = _worker(tmp_path, execution_id=current_id)
    worker.last_captured_inputs = _capture(tmp_path, fact_id)
    result = _from_worker(worker, {"runtime": _runtime(), "execution_id": response_id})
    assert result.instance == response_id  # 旧回执仍对应其响应；仅拒错误的正证。
    assert result.captured_inputs is None
    assert "captured_inputs" not in result.public_facts()


@pytest.mark.parametrize("bad_id", [None, "", True, 42, "not-an-execution-id"])
def test_invalid_response_execution_cannot_borrow_valid_worker_fact(tmp_path, bad_id):
    worker = _worker(tmp_path)
    worker.last_captured_inputs = _capture(tmp_path, worker.execution_id)
    result = _from_worker(worker, {"runtime": _runtime(), "execution_id": bad_id})
    assert result.captured_inputs is None
    assert result.instance == ""


@pytest.mark.parametrize("missing_attribute", [False, True])
def test_last_build_id_can_confirm_response_when_current_id_is_unavailable(
    tmp_path, missing_attribute
):
    worker = _worker(tmp_path)
    execution_id = worker.execution_id
    worker.last_build_execution_id = execution_id
    worker.last_captured_inputs = _capture(tmp_path, execution_id)
    if missing_attribute:
        del worker.execution_id
    else:
        worker.execution_id = None
    result = _from_worker(worker, {"runtime": _runtime(), "execution_id": execution_id})
    assert result.captured_inputs == inputdeclaration.public_fact(worker.last_captured_inputs)


def test_current_execution_takes_precedence_over_old_last_build_id(tmp_path):
    worker = _worker(tmp_path, execution_id=_SECOND)
    worker.last_build_execution_id = _FIRST
    worker.last_captured_inputs = _capture(tmp_path, _FIRST)
    result = _from_worker(worker, {"runtime": _runtime(), "execution_id": _FIRST})
    assert result.captured_inputs is None


@pytest.mark.parametrize("corruption", ["version", "extra", "missing", "revision", "content"])
def test_closed_fact_is_validated_before_public_summary(tmp_path, corruption):
    worker = _worker(tmp_path)
    fact = _capture(tmp_path, worker.execution_id)
    if corruption == "version":
        fact["version"] = True
    elif corruption == "extra":
        fact["trusted"] = True
    elif corruption == "missing":
        del fact["declaration"]
    elif corruption == "revision":
        fact["revision"] = "sha256:" + "0" * 64
    else:
        fact["declaration"]["files"][0]["sha256"] = "0" * 64
    worker.last_captured_inputs = fact
    assert _from_worker(worker).captured_inputs is None


@pytest.mark.parametrize(
    "scope",
    [
        {"script": "other.py", "cwd_mode": "sandbox", "run_config": None},
        {"script": "plot.py", "cwd_mode": "project_root", "run_config": None},
        {"script": "plot.py", "cwd_mode": "sandbox", "run_config": "rc_other"},
    ],
)
def test_valid_capture_for_different_execution_scope_is_not_reused(tmp_path, scope):
    worker = _worker(tmp_path)
    worker.last_captured_inputs = _capture(tmp_path, worker.execution_id, scope=scope)
    assert _from_worker(worker).captured_inputs is None


@pytest.mark.parametrize("reason", ["missing", "empty", "probe", "wrong_pid", "unknown_pid"])
def test_capture_requires_positively_accepted_runtime_owner(tmp_path, reason):
    worker = _worker(tmp_path)
    worker.last_captured_inputs = _capture(tmp_path, worker.execution_id)
    runtime = _runtime()
    if reason == "missing":
        runtime = None
    elif reason == "empty":
        runtime = {}
    elif reason == "probe":
        runtime["report_origin"] = "probe"
    elif reason == "wrong_pid":
        runtime["pid"] += 1
    else:
        worker.child_pid = None
    result = _from_worker(worker, {"runtime": runtime, "execution_id": worker.execution_id})
    assert result.captured_inputs is None
    if reason == "unknown_pid":
        assert result.runtime is not None
        assert result.pid_check == receipt.PID_CHECK_UNAVAILABLE


def test_positive_launcher_parent_pid_keeps_capture_qualification(tmp_path):
    worker = _worker(tmp_path)
    worker.last_captured_inputs = _capture(tmp_path, worker.execution_id)
    runtime = {**_runtime(), "pid": 4243, "ppid": 4242}
    result = _from_worker(worker, {"runtime": runtime, "execution_id": worker.execution_id})
    assert result.pid_check == receipt.PID_CHECK_LAUNCHER
    assert result.captured_inputs == inputdeclaration.public_fact(worker.last_captured_inputs)


def test_receipt_copies_summary_and_public_views_cannot_mutate_it(tmp_path):
    worker = _worker(tmp_path)
    worker.last_captured_inputs = _capture(tmp_path, worker.execution_id)
    result = _from_worker(worker)
    identity = result.public_identity()
    expected = dict(result.captured_inputs)
    worker.last_captured_inputs["revision"] = "sha256:" + "0" * 64
    result.public_facts()["captured_inputs"]["revision"] = "sha256:" + "1" * 64
    result.to_payload()["captured_inputs"]["revision"] = "sha256:" + "2" * 64
    assert result.captured_inputs == expected
    assert result.public_identity() == identity
    with pytest.raises(TypeError):
        result.captured_inputs["revision"] = "sha256:" + "3" * 64


def test_native_session_cannot_retrofit_safe_capture_fact(tmp_path):
    session = nativesession.NativeSession(
        session_id="native-no-captured-declaration",
        descriptor={
            "metadata": {
                "project_root": str(tmp_path),
                "interpreter": "/env/python",
                "target_kind": "script",
                "cwd": str(tmp_path),
                "arg_count": 0,
            }
        },
    )
    session.execution_id = executionfact.new_id()
    session.last_captured_inputs = _capture(tmp_path, session.execution_id)
    result = receipt.from_native_session(session, "plot.py")
    assert result.profile == execspec.PROFILE_NATIVE
    assert result.captured_inputs is None
    assert "captured_inputs" not in result.to_payload()
    assert "captured_inputs" not in result.public_facts()
