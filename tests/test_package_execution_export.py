"""包收集不执行脚本；批准文件、旧输入依据与未声明依赖各量其实际主语。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tavotto.engine import (
    chartpackage,
    figcapture,
    inputremap,
    packageexecution,
    packageexecution_export,
    projectenv,
    registry,
    runconfig,
    workdir,
)

SCRIPT = "scripts/figure.py"
SOURCE = "raise AssertionError('collector must never execute this script')\nopen('data/base.csv')\n"


def use(reference=None, *, known=True, script=SCRIPT):
    return {"script": script, "run_config": reference, "run_config_known": known}


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    for relative, content in {
        SCRIPT: SOURCE,
        "scripts/helper.py": "FACTOR = 2\n",
        "data/base.csv": "x,y\n0,1\n",
        "data/dynamic.csv": "x,y\n0,7\n",
        "other.py": "raise AssertionError('unrelated script must not be packaged')\n",
    }.items():
        path = root / relative
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(content, encoding="utf-8")
    registered = registry.Registry()
    registered.load_data(
        {
            "scripts": {
                SCRIPT: {
                    "entry": "__main__",
                    "cost": "light",
                    "notes": "",
                    "stems": ["curve"],
                    "artifacts": {"out/curve.svg": "curve"},
                },
                "other.py": {"entry": "__main__", "stems": ["other"]},
            }
        }
    )
    workdir.set_mode(root, workdir.MODE_PROJECT_ROOT)
    calls = []

    def resource(kind, resource_id):
        calls.append((kind, resource_id))
        approved = projectenv.contained_path(root, resource_id)
        assert approved is not None, "collector must approve before asking for file bytes"
        source = Path(approved)
        if not source.is_file():
            return None
        digest, size = figcapture.hash_file(source)
        return chartpackage.ResourceFile(
            {
                "kind": kind,
                "id": resource_id,
                "path": ("scripts/" if kind == "script" else "assets/") + resource_id,
                "sha256": digest,
                "bytes": size,
            },
            source,
        )

    return root, registered, resource, calls


def record(root, reference=None, *, basis=None, script_sha1=None, **extra):
    source = {
        "script_sha1": script_sha1 or hashlib.sha1((root / SCRIPT).read_bytes()).hexdigest(),
        "basis": basis
        if basis is not None
        else {
            relative: figcapture.hash_file(root / relative)[0]
            for relative in ("scripts/helper.py", "data/dynamic.csv")
        },
    }
    runconfig.set_launch_record(
        root,
        SCRIPT,
        {
            "version": 2,
            "profile": "native",
            "cwd_mode": "sandbox",
            "run_config": reference,
            "interpreter": {
                "path": "/machine/private/python",
                "source": "user",
                "generation": "g1",
            },
            "launcher": {"kind": "direct"},
            "env_state": "",
            "source": source,
            **extra,
        },
    )


def collect(case, uses=None, *, resource=None):
    root, registered, callback, _calls = case
    return packageexecution_export.collect_execution(
        root, registered, uses if uses is not None else [use()], resource=resource or callback
    )


def reasons(result, script=SCRIPT):
    return {
        row["reason"] for row in result.execution["coverage"]["issues"] if row["script"] == script
    }


def rows(result):
    return {(item.record["kind"], item.record["id"]): item.record for item in result.resources}


def test_collects_actual_csv_helper_and_original_argv_without_running_script(case):
    root, registered, _resource, calls = case
    tokens = ["--input", "data/dynamic.csv", "", "--label", "带 空格", "--", "-3"]
    cfg = runconfig.put(root, SCRIPT, tokens)
    record(root, cfg.id)
    result = collect(case, [use(cfg.id), use(cfg.id)])
    value = result.execution
    assert value["registry"]["scripts"] == {SCRIPT: registered.entries()[SCRIPT]}
    assert value["runs"] == [{"id": cfg.id, "script": SCRIPT, "argv": tokens}]
    assert len(value["contexts"]) == 1
    context = value["contexts"][0]
    assert context["cwd_mode"] == "project_root"
    assert context["profile"] is None  # 当前建议不能把旧 native 记录伪装成当前执行资格。
    assert {row["id"] for row in context["basis"]} == {"scripts/helper.py", "data/dynamic.csv"}
    assert set(rows(result)) == {
        ("script", SCRIPT),
        ("script", "scripts/helper.py"),
        ("file", "data/base.csv"),
        ("file", "data/dynamic.csv"),
    }
    assert len(calls) == len(set(calls))
    assert result.missing == ()
    assert reasons(result) == {"dependency_set_undeclared"}
    assert value["coverage"]["state"] == "incomplete"
    assert packageexecution.validate_execution(value) == value
    packageexecution.validate_resources(value, [item.record for item in result.resources])
    serialized = json.dumps(value)
    for forbidden in (
        str(root),
        "interpreter",
        "granted_at",
        "defaults",
        "/machine/private/python",
    ):
        assert forbidden not in serialized
    value["registry"]["scripts"][SCRIPT]["stems"].append("caller_edit")
    assert registered.stems_of(SCRIPT) == ["curve"]


def test_same_script_multi_config_and_mixed_paramfree_unknown_sources(case):
    root = case[0]
    first = runconfig.put(root, SCRIPT, ["--n", "1"])
    second = runconfig.put(root, SCRIPT, ["--n", "2"])
    runconfig.set_default(root, SCRIPT, second.id)
    record(root, first.id)
    result = collect(
        case, [use(first.id), use(second.id), use(), use(known=False), use(known=False)]
    )
    contexts = result.execution["contexts"]
    assert [(row["run_config"], row["run_config_known"]) for row in contexts] == [
        (first.id, True),
        (second.id, True),
        (None, True),
        (None, False),
    ]
    assert [row["id"] for row in result.execution["runs"]] == [first.id, second.id]
    assert contexts[0]["basis"]
    assert all(row["basis"] == [] for row in contexts[1:])
    assert {"run_config_unresolved", "source_basis_missing"} <= reasons(result)


def test_unknown_configuration_never_uses_current_default_or_launch_basis(case, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("unknown historic config must not borrow current state")

    monkeypatch.setattr(runconfig, "get", forbidden)
    monkeypatch.setattr(runconfig, "default_selection", forbidden)
    monkeypatch.setattr(runconfig, "launch_record", forbidden)
    result = collect(case, [use(known=False)])
    assert result.execution["runs"] == []
    assert result.execution["contexts"][0]["run_config_known"] is False
    assert "run_config_unresolved" in reasons(result)


@pytest.mark.parametrize("forget", [False, True])
def test_sensitive_config_values_never_leave_memory_even_if_available(case, forget):
    root = case[0]
    secret = "SECRET-NOT-IN-PACKAGE-ARGV"
    cfg = runconfig.put(root, SCRIPT, ["--token", secret], sensitive=True)
    if forget:
        runconfig.forget_secrets()
    result = collect(case, [use(cfg.id)])
    assert result.execution["runs"] == []
    assert "sensitive_run_config" in reasons(result)
    assert secret not in json.dumps(result.execution)
    assert result.execution["contexts"][0]["run_config"] == cfg.id


def test_missing_known_config_keeps_reference_and_accurate_issue(case):
    result = collect(case, [use("rc_ffffffffffff")])
    assert result.execution["runs"] == []
    assert "run_config_missing" in reasons(result)
    assert result.execution["contexts"][0]["run_config"] == "rc_ffffffffffff"


def test_changed_main_script_does_not_reuse_old_helper_basis(case):
    root = case[0]
    record(root)
    (root / SCRIPT).write_text(SOURCE + "# source changed\n", encoding="utf-8")
    result = collect(case)
    assert "source_basis_stale" in reasons(result)
    assert result.execution["contexts"][0]["basis"] == []
    assert ("script", "scripts/helper.py") not in rows(result)


def test_changed_basis_does_not_relabel_current_file_as_old_observed_input(case):
    root = case[0]
    record(root)
    old = figcapture.hash_file(root / "data/dynamic.csv")[0]
    (root / "data/dynamic.csv").write_text("x,y\n0,999\n", encoding="utf-8")
    result = collect(case)
    assert "source_basis_stale" in reasons(result)
    assert all(row["id"] != "data/dynamic.csv" for row in result.execution["contexts"][0]["basis"])
    assert ("file", "data/dynamic.csv") not in rows(result)
    assert old not in {item.record["sha256"] for item in result.resources}


def test_missing_basis_returns_missing_resource_and_never_dangling_basis(case):
    root = case[0]
    record(root, basis={"data/not-there.csv": "a" * 64})
    result = collect(case)
    assert result.missing == ("data/not-there.csv",)
    assert "resource_missing" in reasons(result)
    assert result.execution["contexts"][0]["basis"] == []


@pytest.mark.parametrize("record_update", [{"version": 100}, {"run_config": "rc_eeeeeeeeeeee"}])
def test_unreliable_or_other_configuration_record_is_not_current_basis(case, record_update):
    record(case[0], **record_update)
    result = collect(case)
    assert "source_basis_missing" in reasons(result)
    assert result.execution["contexts"][0]["basis"] == []


def test_unobserved_native_basis_is_not_promoted_to_proven_inputs(case):
    root = case[0]
    record(
        root,
        source={
            "script_sha1": hashlib.sha1((root / SCRIPT).read_bytes()).hexdigest(),
            "basis": {},
            "basis_observed": False,
        },
    )
    result = collect(case)
    assert "source_basis_missing" in reasons(result)


def test_oversized_success_basis_is_not_reinterpreted_as_complete_observation(case):
    record(case[0], basis={f"data/extra-{i}.csv": "a" * 64 for i in range(201)})
    result = collect(case)
    assert "source_basis_missing" in reasons(result)
    assert result.execution["contexts"][0]["basis"] == []
    assert all("extra-" not in resource_id for _kind, resource_id in case[3])


def test_external_or_unsafe_basis_is_not_read_or_passed_to_callback(case):
    root = case[0]
    record(root, basis={"../outside.csv": "a" * 64, "data/../odd.csv": "b" * 64})
    result = collect(case)
    assert "external_input_requires_relink" in reasons(result)
    assert "source_basis_stale" in reasons(result)
    assert all(
        "outside" not in resource_id and "odd" not in resource_id for _kind, resource_id in case[3]
    )
    assert result.execution["contexts"][0]["basis"] == []


def test_global_missing_remap_is_removed_and_all_script_owners_are_incomplete(case):
    root = case[0]
    inputremap.add_rule(
        root, {"kind": "file", "from": "old.csv", "to": str(root / "data/gone.csv")}
    )
    result = collect(case, [use(), use(script="other.py")])
    assert result.execution["remaps"] == []
    assert result.missing == ("data/gone.csv",)
    assert all("resource_missing" in reasons(result, script) for script in (SCRIPT, "other.py"))


@pytest.mark.parametrize("requested", ["old/input.csv", r"..\old\input.csv"])
def test_precise_relative_file_remap_keeps_original_request_and_packaged_target(case, requested):
    root = case[0]
    inputremap.add_rule(
        root, {"kind": "file", "from": requested, "to": str(root / "data/dynamic.csv")}
    )
    result = collect(case)
    assert result.execution["remaps"] == [
        {"kind": "file", "from": requested, "to_resource": "data/dynamic.csv"}
    ]
    assert ("file", "data/dynamic.csv") in rows(result)
    assert str(root) not in json.dumps(result.execution)


@pytest.mark.parametrize(
    "kind,requested,target,expected",
    [
        ("prefix", "old", "data", "prefix_members_undeclared"),
        ("file", "C:/old/data.csv", "data/dynamic.csv", "unsupported_absolute_request"),
        ("file", "old.csv", "../external.csv", "external_input_requires_relink"),
    ],
)
def test_nonportable_global_remap_never_serializes_machine_target(
    case, kind, requested, target, expected
):
    root = case[0]
    inputremap.add_rule(root, {"kind": kind, "from": requested, "to": str(root / target)})
    result = collect(case, [use(), use(script="other.py")])
    assert result.execution["remaps"] == []
    assert all(expected in reasons(result, script) for script in (SCRIPT, "other.py"))
    assert ("file", target) not in case[3]


def test_static_missing_literal_resolved_by_remap_is_not_false_missing(case):
    root = case[0]
    (root / SCRIPT).write_text(SOURCE + "open('old.csv')\n", encoding="utf-8")
    inputremap.add_rule(
        root, {"kind": "file", "from": "old.csv", "to": str(root / "data/dynamic.csv")}
    )
    result = collect(case)
    assert result.missing == ()
    assert ("file", "old.csv") not in rows(result)
    assert ("file", "data/dynamic.csv") in rows(result)


def test_static_missing_resource_keeps_accurate_project_relative_id(case):
    root = case[0]
    (root / SCRIPT).write_text(SOURCE + "open('data/absent.csv')\n", encoding="utf-8")
    result = collect(case)
    assert result.missing == ("data/absent.csv",)
    assert "resource_missing" in reasons(result)


def test_external_static_literal_does_not_ask_callback_for_external_file(case):
    root = case[0]
    (root / SCRIPT).write_text(SOURCE + "open('../../outside.csv')\n", encoding="utf-8")
    result = collect(case)
    assert "external_input_requires_relink" in reasons(result)
    assert all("outside" not in resource_id for _kind, resource_id in case[3])


def test_unapproved_main_path_never_gets_opened(case, monkeypatch):
    root = case[0]
    outside = root.parent / "private.py"
    original_open = Path.open

    def guarded(path, *args, **kwargs):
        assert path != outside, "collector read an unapproved machine file"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)

    def bad_resource(kind, resource_id):
        return chartpackage.ResourceFile({"kind": kind, "id": resource_id}, outside)

    with pytest.raises(chartpackage.PackageError, match="escaped"):
        collect(case, resource=bad_resource)


def test_main_approval_bytes_are_not_substituted_with_different_disk_source(case):
    original = case[2]("script", SCRIPT)

    def byte_resource(_kind, _resource_id):
        return chartpackage.ResourceFile(original.record, original.source.read_bytes())

    with pytest.raises(chartpackage.PackageError, match="canonical"):
        collect(case, resource=byte_resource)


def test_approved_main_cannot_be_substituted_with_another_project_script(case):
    original = case[2]("script", SCRIPT)

    def wrong_file(_kind, _resource_id):
        return chartpackage.ResourceFile(original.record, case[0] / "other.py")

    with pytest.raises(chartpackage.PackageError, match="another project file"):
        collect(case, resource=wrong_file)


def test_unavailable_primary_source_does_not_read_static_or_historical_source(case, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("unapproved source must not be read for execution facts")

    monkeypatch.setattr(packageexecution_export.databinding, "binding_for", forbidden)
    monkeypatch.setattr(runconfig, "launch_record", forbidden)
    result = collect(case, resource=lambda *_args: None)
    assert result.resources == ()
    assert result.missing == (SCRIPT,)
    assert {"resource_missing", "source_basis_missing"} <= reasons(result)


def test_changed_script_after_approval_refuses_package_instead_of_mixing_facts(case):
    root, _reg, original, _calls = case

    def changed(kind, resource_id):
        item = original(kind, resource_id)
        if resource_id == SCRIPT:
            (root / SCRIPT).write_text("# raced after callback\n", encoding="utf-8")
        return item

    with pytest.raises(chartpackage.PackageError, match="changed after"):
        collect(case, resource=changed)


def test_aliased_resource_kinds_with_conflicting_bytes_are_rejected(case, monkeypatch):
    root, _reg, original, _calls = case
    updated = b"FACTOR = 99\n"
    record(root, basis={"scripts/helper.py": hashlib.sha256(updated).hexdigest()})
    monkeypatch.setattr(
        packageexecution_export.databinding,
        "binding_for",
        lambda *_args: {
            "expected": {"scripts/helper.py": figcapture.hash_file(root / "scripts/helper.py")[0]},
            "outside": [],
            "missing": [],
        },
    )

    # 相同 id 被 static file 与历史 helper/script 认领时，出口不得放过不同摘要。
    def conflicting(kind, resource_id):
        if kind == "script" and resource_id == "scripts/helper.py":
            (root / resource_id).write_bytes(updated)
        return original(kind, resource_id)

    with pytest.raises(chartpackage.PackageError, match="disagree on bytes"):
        collect(case, resource=conflicting)


def test_empty_source_set_is_valid_data_and_never_reads_configuration(case, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("empty source set must not access machine execution config")

    monkeypatch.setattr(inputremap, "rules_for", forbidden)
    monkeypatch.setattr(workdir, "resolve_mode", forbidden)
    result = collect(case, [])
    assert result.execution["registry"] == {"scripts": {}}
    assert result.resources == result.missing == ()


@pytest.mark.parametrize(
    "raw",
    [
        {"script": SCRIPT, "run_config": None},
        use(known="yes"),
        use("rc_ffffffffffff", known=False),
        use(script="not-registered.py"),
    ],
)
def test_bad_resolver_identity_fails_before_resource_callback(case, raw):
    with pytest.raises(chartpackage.PackageError, match="resolved"):
        collect(case, [raw])
    assert case[3] == []
