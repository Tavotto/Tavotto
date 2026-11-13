"""Real install HTTP boundaries; chart positives use the original renderer.

Ordinary fixture scripts are carried data and never executed. Explicit injected
renderer/configuration/publication failures test phase receipts, not scientific
rendering success. Installation does not open or save a working document.
"""

from __future__ import annotations

import copy
import io
import json
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict
from werkzeug.test import EnvironBuilder

from tavotto import app as m, security
from tavotto.engine import (
    atomicio,
    chartprocess,
    config,
    documentpackage,
    inputremap,
    packagebudget,
    pool,
    registry,
    runconfig,
    workdir,
)
from test_chart_native_api import SOURCE, _bind, _color
from test_chart_package_http import (
    document,
    env as _package_env,
    freeze_named,
    panel,
    project as _native_project,
)
from test_chart_package_inspect_http import _add_resource, _exported, _files, _zip

env = _package_env
project = _native_project
ENDPOINT = "/api/documents/v4/package/install"
MANIFEST = documentpackage.MANIFEST_MEMBER
LAYOUT = documentpackage.LAYOUT_MEMBER
SCRIPT = "science/plot.py"
RUN = {"id": "rc_0123456789ab", "script": SCRIPT, "argv": ["--label", "中文 空格", "", "--", "-3"]}


def _upload(client, blob, destination, *, pj=None, options=None, **kwargs):
    body = {"destination": str(destination)} if options is None else options
    return client.post(
        ENDPOINT + ("" if pj is None else "?pj=" + pj),
        data={"package": (io.BytesIO(blob), "fixture.tavotto"), "options": json.dumps(body)},
        content_type="multipart/form-data",
        **kwargs,
    )


def _ready(response, destination):
    assert response.status_code == 201, response.get_json()
    result = response.get_json()
    assert result["state"] == "resources_installed"
    assert result["configuration"] == "ready"
    assert result["document_format"] == "v4" and result["document_saved"] is False
    assert Path(result["path"]) == destination
    assert destination.is_dir()
    return result


def _invalid(response, status=422, reason=None):
    assert response.status_code == status, response.get_json()
    body = response.get_json()
    assert body["code"] == "package_invalid", body
    if reason is not None:
        assert body["params"]["reason"] == reason, body
    return body


def _guard_no_user_execution_or_activation(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("resource installation executed a user script or opened/saved a document")

    for name in ("get", "build", "one_shot"):
        monkeypatch.setattr(pool, name, forbidden)
    monkeypatch.setattr(m, "open_project", forbidden)
    monkeypatch.setattr(config, "touch_recent", forbidden)


def _ordinary_package(env):
    _blob, members, doc, _manifest = _exported(env)
    doc["canvases"][0]["objects"] = [panel(file="ordinary.svg")]
    second = copy.deepcopy(doc["canvases"][0])
    second.update(id="inactive", objects=[{"id": "note", "type": "text", "text": "保留 中文"}])
    doc["canvases"].append(second)
    members[LAYOUT] = json.dumps(doc, ensure_ascii=False).encode()
    members = _add_resource(members, "file", "ordinary.svg", SOURCE)
    members = _add_resource(members, "script", SCRIPT, b"raise RuntimeError('MUST NOT RUN')\n")
    members = _add_resource(members, "file", "data/input.csv", b"x,y\n0,2\n")
    manifest = json.loads(members[MANIFEST])
    manifest["execution"] = {
        "version": 1,
        "registry": {
            "scripts": {
                SCRIPT: {"entry": "__main__", "cost": "light", "notes": "原身份", "stems": ["F"]}
            }
        },
        "runs": [RUN],
        "contexts": [],
        "remaps": [{"kind": "file", "from": "legacy/input.csv", "to_resource": "data/input.csv"}],
        "coverage": {
            "state": "incomplete",
            "issues": [{"script": SCRIPT, "reason": "dependency_set_undeclared"}],
        },
    }
    members[MANIFEST] = json.dumps(manifest, ensure_ascii=False).encode()
    return _zip(members), doc, manifest


@pytest.mark.parametrize("without_project", [False, True])
def test_empty_package_installs_without_opening_or_saving_a_document(
    env, tmp_path, monkeypatch, without_project
):
    blob, _members, doc, _manifest = _exported(env)
    if without_project:
        m.reset_projects(wait=True)
    before_projects = (dict(m.PROJECTS), m.DEFAULT_PROJECT)
    before_source = _files(env[1])
    before_autosaves = _files(config.data_dir() / "layouts")
    _guard_no_user_execution_or_activation(monkeypatch)
    target = tmp_path / "空项目 中文"
    result = _ready(_upload(env[0], blob, target), target)
    assert result["doc"] == doc and result["references"] == []
    assert result["missing"] == result["warnings"] == []
    assert registry.open_registry(target).entries() == {}
    assert (dict(m.PROJECTS), m.DEFAULT_PROJECT) == before_projects
    assert _files(env[1]) == before_source
    assert _files(config.data_dir() / "layouts") == before_autosaves
    assert (
        set(path.name for path in tmp_path.iterdir() if path.name.startswith(".tavotto-install-"))
        == set()
    )


def test_mixed_resources_restore_exact_registry_argv_and_new_root_default_remap(
    env, tmp_path, monkeypatch
):
    blob, doc, manifest = _ordinary_package(env)
    before_projects = (dict(m.PROJECTS), m.DEFAULT_PROJECT)
    before_source = _files(env[1])
    before_autosaves = _files(config.data_dir() / "layouts")
    _guard_no_user_execution_or_activation(monkeypatch)
    target = tmp_path / "资源 中文"
    result = _ready(_upload(env[0], blob, target, pj=env[3]), target)
    assert result["doc"] == doc and result["execution"] == manifest["execution"]
    assert (target / SCRIPT).read_bytes() == b"raise RuntimeError('MUST NOT RUN')\n"
    assert (target / "ordinary.svg").read_bytes() == SOURCE
    assert (target / "data/input.csv").read_bytes() == b"x,y\n0,2\n"
    assert registry.open_registry(target).entries() == manifest["execution"]["registry"]["scripts"]
    restored = runconfig.get(target, RUN["id"], script=SCRIPT)
    assert restored.id == RUN["id"] and list(restored.argv) == RUN["argv"]
    assert runconfig.default_selection(target, SCRIPT) is None
    assert runconfig.launch_record(target, SCRIPT) is None
    rules = [{"kind": "file", "from": "legacy/input.csv", "to": str(target / "data/input.csv")}]
    assert inputremap.rules_for(target) == rules
    assert (
        inputremap.fingerprint(target)
        == result["remap_fingerprint"]
        == inputremap.fingerprint_rules(rules)
    )
    assert (dict(m.PROJECTS), m.DEFAULT_PROJECT) == before_projects
    assert (
        _files(env[1]) == before_source
        and _files(config.data_dir() / "layouts") == before_autosaves
    )


def test_explicit_local_rule_table_is_the_only_installed_fingerprint(env, tmp_path, monkeypatch):
    blob, doc, _manifest = _ordinary_package(env)
    target = tmp_path / "明确本机表"
    rules = [{"kind": "file", "from": "new/request.csv", "to": str(target / "data/input.csv")}]
    _guard_no_user_execution_or_activation(monkeypatch)
    result = _ready(
        _upload(env[0], blob, target, options={"destination": str(target), "remap_rules": rules}),
        target,
    )
    assert result["doc"] == doc and inputremap.rules_for(target) == rules
    assert result["remap_fingerprint"] == inputremap.fingerprint(target)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_existing_target_has_zero_overwrite_and_does_not_install_under_another_name(
    env, tmp_path, kind
):
    blob, _members, _doc, _manifest = _exported(env)
    target = tmp_path / "already"
    if kind == "file":
        target.write_bytes(b"keep")
    else:
        target.mkdir()
        (target / "sentinel").write_bytes(b"keep")
    names = set(tmp_path.iterdir())
    _invalid(_upload(env[0], blob, target), 409, "installation_target_exists")
    assert set(tmp_path.iterdir()) == names
    assert (target if kind == "file" else target / "sentinel").read_bytes() == b"keep"


@pytest.mark.parametrize("address", ["query", "header"])
def test_unknown_project_is_rejected_before_touching_the_destination(env, tmp_path, address):
    blob, _members, _doc, _manifest = _exported(env)
    target = tmp_path / "not-created"
    kwargs = (
        {"pj": "unknown-project"}
        if address == "query"
        else {"headers": {"X-Tavotto-Project": "unknown-project"}}
    )
    response = _upload(env[0], blob, target, **kwargs)
    assert response.status_code == 409 and response.get_json()["code"] == "no_project"
    assert not target.exists()


def test_install_is_protected_by_the_existing_session_secret(env, tmp_path, monkeypatch):
    blob, _members, _doc, _manifest = _exported(env)
    state = security.SessionState("install-nonce", api_secret="install-secret")
    monkeypatch.setitem(m.app.config, security.STATE_KEY, state)
    for index, headers in enumerate(({}, {"X-Tavotto-Auth": state.status_token})):
        target = tmp_path / str(index)
        response = _upload(env[0], blob, target, headers=headers)
        assert (
            response.status_code == 401 and response.get_json()["code"] == "session_auth_required"
        )
        assert not target.exists()
    target = tmp_path / "authenticated"
    _ready(_upload(env[0], blob, target, headers={"X-Tavotto-Auth": "install-secret"}), target)


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"destination": None},
        {"destination": ""},
        {"destination": "relative"},
        {"destination": "/", "extra": 1},
        {"destination": "/", "remap_rules": None},
        {"destination": "/", "remap_rules": [{"kind": "file", "from": "x"}]},
    ],
)
def test_closed_install_options_reject_unknown_or_incomplete_values(env, options):
    blob, _members, _doc, _manifest = _exported(env)
    options = copy.deepcopy(options)
    if options.get("destination") == "/":
        # A slash alone is not absolute on Windows. Keep a valid destination
        # so these cases actually reach unknown-field/remap validation.
        options["destination"] = str(env[1].parent / "unused")
    _invalid(
        _upload(env[0], blob, env[1].parent / "unused", options=options),
        422,
        "installation_invalid",
    )


@pytest.mark.parametrize(
    "damage",
    [
        "duplicate_options",
        "duplicate_package",
        "unknown_form",
        "unknown_file",
        "options_array",
        "options_invalid_json",
        "missing_package",
        "json_body",
    ],
)
def test_upload_fields_are_closed_and_do_not_weaken_the_inspection_contract(env, tmp_path, damage):
    blob, _members, _doc, _manifest = _exported(env)
    target = tmp_path / "not-created"
    value = json.dumps({"destination": str(target)})
    form = MultiDict([("package", (io.BytesIO(blob), "fixture.tavotto")), ("options", value)])
    if damage == "duplicate_options":
        form.add("options", value)
    elif damage == "duplicate_package":
        form.add("package", (io.BytesIO(blob), "second.tavotto"))
    elif damage == "unknown_form":
        form.add("extra", "unrecognized")
    elif damage == "unknown_file":
        form.add("extra", (io.BytesIO(b"unknown"), "extra.bin"))
    elif damage == "options_array":
        form.setlist("options", ["[]"])
    elif damage == "options_invalid_json":
        form.setlist("options", ["{"])
    elif damage == "missing_package":
        del form["package"]
    if damage == "json_body":
        response = env[0].post(ENDPOINT, json={"destination": str(target)})
    else:
        response = env[0].post(ENDPOINT, data=form, content_type="multipart/form-data")
    if damage == "missing_package":
        assert response.status_code == 400 and response.get_json()["code"] == "package_file_missing"
    else:
        _invalid(response, 400, "invalid_upload")
    assert not target.exists()
    # Install's options field remains illegal on the original inspect route.
    inspected = env[0].post(
        "/api/documents/v4/package/inspect",
        data={"package": (io.BytesIO(blob), "fixture.tavotto"), "options": value},
        content_type="multipart/form-data",
    )
    _invalid(inspected, 400, "invalid_upload")


@pytest.mark.parametrize("known_length", [False, True])
def test_install_actual_archive_budget_is_enforced_with_or_without_content_length(
    env, tmp_path, monkeypatch, known_length
):
    blob, _members, _doc, _manifest = _exported(env)
    budget = replace(packagebudget.input_budget(), max_archive_bytes=len(blob) - 1)
    monkeypatch.setattr(packagebudget, "input_budget", lambda: budget)
    target = tmp_path / "too-large"
    builder = EnvironBuilder(
        path=ENDPOINT,
        method="POST",
        data={
            "package": (io.BytesIO(blob), "fixture.tavotto"),
            "options": json.dumps({"destination": str(target)}),
        },
    )
    environ = builder.get_environ()
    if not known_length:
        environ.pop("CONTENT_LENGTH", None)
        environ["wsgi.input_terminated"] = True
    try:
        _invalid(env[0].open(environ), 413)
    finally:
        builder.close()
    assert not target.exists()


def test_invalid_package_and_known_missing_resource_do_not_create_a_final_root(env, tmp_path):
    target = tmp_path / "bad"
    _invalid(_upload(env[0], b"not a package", target))
    assert not target.exists()
    blob, members, doc, _manifest = _exported(env)
    doc["canvases"][0]["objects"] = [panel(file="missing.png")]
    members[LAYOUT] = json.dumps(doc).encode()
    _invalid(_upload(env[0], _zip(members), target), 422, "installation_resources_missing")
    assert not target.exists()


@pytest.mark.parametrize("fault", ["run_config", "post_publication_fsync"])
def test_post_publication_failures_report_the_real_path_and_never_roll_back_the_new_tree(
    env, tmp_path, monkeypatch, fault
):
    blob, doc, _manifest = _ordinary_package(env)
    target = tmp_path / "保留已发布项目"
    _guard_no_user_execution_or_activation(monkeypatch)
    if fault == "run_config":

        def fail(*_args, **_kwargs):
            raise runconfig.RunConfigUnreadable(
                "injected configuration failure", reason="portable_write_failed"
            )

        monkeypatch.setattr(runconfig, "import_portable", fail)
    else:
        original = atomicio.publish_directory_if_absent

        def fail(stage, destination):
            original(stage, destination)
            raise atomicio.AtomicWriteError(
                "dir_fsync_failed", "injected after real publication", destination.parent
            )

        monkeypatch.setattr(atomicio, "publish_directory_if_absent", fail)
    response = _upload(env[0], blob, target)
    assert response.status_code == 503, response.get_json()
    body = response.get_json()
    assert Path(body["path"]) == target and body["doc"] == doc
    assert body["configuration"] == "pending" and body["document_saved"] is False
    assert (target / SCRIPT).read_bytes() == b"raise RuntimeError('MUST NOT RUN')\n"
    assert body["state"] == (
        "publication_unconfirmed" if fault == "post_publication_fsync" else "resources_installed"
    )
    assert (target / "data/input.csv").read_bytes() == b"x,y\n0,2\n"


def test_prior_path_configuration_is_retained_and_requires_explicit_review(
    env, tmp_path, monkeypatch
):
    blob, doc, _manifest = _ordinary_package(env)
    target = tmp_path / "有历史设置但目录不存在"
    runconfig.import_portable(target, [RUN])
    runconfig.set_default(target, SCRIPT, RUN["id"])
    before = workdir.set_mode(target, workdir.MODE_PROJECT_ROOT)
    assert not target.exists()
    _guard_no_user_execution_or_activation(monkeypatch)
    response = _upload(env[0], blob, target)
    assert response.status_code == 503, response.get_json()
    result = response.get_json()
    assert result["configuration"] == "needs_review" and result["doc"] == doc
    assert result["state"] == "resources_installed" and result["document_saved"] is False
    assert target.is_dir() and workdir.state(target) == before
    assert runconfig.default_selection(target, SCRIPT).config_id == RUN["id"]
    review = result["configuration_review"]
    assert review["existing_local_metadata"] is True
    assert review["defaults"][0]["selection"] == runconfig.get(target, RUN["id"]).public()
    assert "argv" not in review["defaults"][0]["selection"]
    assert RUN["argv"][1] not in json.dumps(review, ensure_ascii=False)


def test_upload_cleanup_failure_after_commit_retains_the_actual_installed_path(
    env, tmp_path, monkeypatch
):
    blob, _members, doc, _manifest = _exported(env)
    target = tmp_path / "上传清理失败但项目已发布"
    original = tempfile.TemporaryDirectory

    class FailAfterCleanup:
        def __init__(self, context):
            self.context = context

        def __enter__(self):
            return self.context.__enter__()

        def __exit__(self, *args):
            result = self.context.__exit__(*args)
            if args[0] is None:
                raise OSError("injected upload directory cleanup failure")
            return result

    def temporary(*args, **kwargs):
        context = original(*args, **kwargs)
        return FailAfterCleanup(context) if kwargs.get("prefix") == "package-upload-" else context

    monkeypatch.setattr(tempfile, "TemporaryDirectory", temporary)
    _guard_no_user_execution_or_activation(monkeypatch)
    response = _upload(env[0], blob, target)
    assert response.status_code == 503, response.get_json()
    result = response.get_json()
    assert result["state"] == "installation_unconfirmed" and result["upload_cleanup"] == "pending"
    assert result["configuration"] == "ready" and result["document_saved"] is False
    assert Path(result["path"]) == target and result["doc"] == doc
    assert registry.open_registry(target).entries() == {}


def _chart_package(env):
    state = _bind(env[0])
    edits = [
        _color("#00ff00"),
        {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 4},
        _color("#ff0000"),
    ]
    frozen = freeze_named(env, document((state, edits)))
    (env[1] / "chart.svg").unlink()
    blob, members, doc, manifest = _exported(env, frozen)
    assert doc["canvases"][0]["objects"][0]["chartSource"]
    return blob, members, doc, manifest


@pytest.mark.parametrize("without_project", [False, True])
def test_real_chart_renderer_verifies_carried_input_using_a_trusted_engine_root(
    env, tmp_path, monkeypatch, without_project
):
    _blob, members, doc, _manifest = _chart_package(env)
    members = _add_resource(members, "file", ".venv/pyvenv.cfg", b"home = untrusted-upload\n")
    blob = _zip(members)
    if without_project:
        m.reset_projects(wait=True)
    expected_projects = (dict(m.PROJECTS), m.DEFAULT_PROJECT)
    _guard_no_user_execution_or_activation(monkeypatch)
    original = chartprocess.run_request
    roots = []

    def actual_render(root, request, **kwargs):
        roots.append(Path(root))
        if without_project:
            assert Path(root).name == "verification-engine"
            assert Path(root).is_relative_to(config.data_dir())
        else:
            assert Path(root) == env[1]
        return original(root, request, **kwargs)

    monkeypatch.setattr(chartprocess, "run_request", actual_render)
    target = tmp_path / "真实计算恢复"
    result = _ready(_upload(env[0], blob, target), target)
    assert roots, "The actual chart producer must be reached"
    assert result["doc"] == doc
    assert (target / ".venv/pyvenv.cfg").read_bytes() == b"home = untrusted-upload\n"
    assert not (target / "chart.svg").exists()
    assert (dict(m.PROJECTS), m.DEFAULT_PROJECT) == expected_projects


def test_actual_renderer_rejects_unrestorable_scientific_edits_before_publication(
    env, tmp_path, monkeypatch
):
    _blob, members, doc, _manifest = _chart_package(env)
    doc["canvases"][0]["objects"][0]["overrides"] = [
        {"gid": "unowned-nonexistent-object", "prop": "linewidth", "value": 9}
    ]
    members[LAYOUT] = json.dumps(doc).encode()
    calls = []
    original = chartprocess.run_request

    def actual_render(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(chartprocess, "run_request", actual_render)
    _guard_no_user_execution_or_activation(monkeypatch)
    target = tmp_path / "must-not-publish"
    response = _upload(env[0], _zip(members), target)
    assert response.status_code == 503, response.get_json()
    assert response.get_json()["code"] == "chart_manifest_target_unowned", response.get_json()
    assert calls, "Failure must be produced by the original renderer, not just container parsing"
    assert not target.exists()


def test_renderer_callback_failure_keeps_both_final_root_and_parent_stage_unpublished(
    env, tmp_path, monkeypatch
):
    blob, _members, _doc, _manifest = _chart_package(env)
    target = tmp_path / "callback-failure"
    before = set(tmp_path.iterdir())

    def fail(*_args, **_kwargs):
        raise pool.WorkerError("injected renderer failure", code="svg_scaffold_invalid")

    monkeypatch.setattr(m, "_render_chart_snapshot", fail)
    response = _upload(env[0], blob, target)
    assert response.status_code == 503 and response.get_json()["code"] == "svg_scaffold_invalid"
    assert not target.exists() and set(tmp_path.iterdir()) == before
