"""Answer management targets one (script, run configuration, question), including its local context."""

import json

import pytest

from tavotto import app as m
from tavotto.engine import inputtranscript, runconfig, scriptanswers


@pytest.fixture
def answers(tmp_path):
    for config, answer in ((None, "legacy"), ("rc_a", "alpha"), ("rc_b", "beta")):
        scriptanswers.remember(
            tmp_path, "s.py", 1, "p: ", answer, context=f"ctx:{answer}", run_config=config
        )
        inputtranscript.bind(
            tmp_path,
            "s.py",
            config,
            [
                {
                    "index": 1,
                    "prompt": "p: ",
                    "kind": "input",
                    "answer": answer,
                    "context": f"ctx:{answer}",
                }
            ],
        )
    return tmp_path


def recall(root, config, context):
    return scriptanswers.recall(root, "s.py", 1, "p: ", context=context, run_config=config).answer


def test_management_projection_keeps_opaque_identity_without_contexts(answers):
    rows = scriptanswers.load(answers)["s.py"]
    assert {r.get("run_config"): r["answer"] for r in rows} == {
        None: "legacy",
        "rc_a": "alpha",
        "rc_b": "beta",
    }
    assert all(set(r) <= {"index", "prompt", "answer", "kind", "run_config"} for r in rows)


def test_update_and_delete_preserve_other_configurations_and_their_contexts(answers):
    assert scriptanswers.update(answers, "s.py", 1, "new-alpha", run_config="rc_a")
    assert recall(answers, "rc_a", "ctx:alpha") == "new-alpha"
    assert recall(answers, "rc_b", "ctx:beta") == "beta"
    assert recall(answers, None, "ctx:legacy") == "legacy"
    assert scriptanswers.forget(answers, "s.py", 1, run_config="rc_a")
    assert recall(answers, "rc_a", "ctx:alpha") is None
    assert recall(answers, "rc_b", "ctx:beta") == "beta"
    assert recall(answers, None, "ctx:legacy") == "legacy"
    contexts = scriptanswers.contexts_path(answers).read_text("utf-8")
    assert "ctx:alpha" not in contexts and "ctx:beta" in contexts


def test_missing_configuration_means_legacy_only_and_bulk_delete_is_explicit(answers):
    assert scriptanswers.update(answers, "s.py", 1, "new-legacy")
    assert recall(answers, "rc_b", "ctx:beta") == "beta"
    assert scriptanswers.forget(answers, "s.py", 1)
    assert not scriptanswers.update(answers, "s.py", 1, "must-not-touch-configured")
    assert not scriptanswers.forget(answers, "s.py", 1)
    assert recall(answers, "rc_a", "ctx:alpha") == "alpha"
    with pytest.raises(ValueError):
        scriptanswers.forget(answers, "s.py", run_config="rc_a")
    assert scriptanswers.forget(answers, "s.py")
    assert scriptanswers.load(answers) == {}
    assert json.loads(scriptanswers.contexts_path(answers).read_text("utf-8"))["entries"] == {}


@pytest.fixture
def client(answers):
    m.app.config["TESTING"] = True
    m.reset_projects()
    client = m.app.test_client()
    assert client.post("/api/projects/open", json={"path": str(answers)}).status_code == 200
    yield client
    m.reset_projects()


def test_http_edit_and_delete_target_one_configuration_and_transcript(client, answers):
    response = client.post(
        "/api/script_input/answers",
        json={
            "script": "s.py",
            "index": 1,
            "run_config": "rc_a",
            "answer": "new-alpha",
        },
    )
    assert response.status_code == 200
    assert recall(answers, "rc_a", "ctx:alpha") == "new-alpha"
    assert recall(answers, "rc_b", "ctx:beta") == "beta"
    assert inputtranscript.lookup(answers, "s.py", "rc_a") is None
    assert inputtranscript.lookup(answers, "s.py", "rc_b") is not None
    assert inputtranscript.lookup(answers, "s.py", None) is not None
    response = client.post(
        "/api/script_input/answers",
        json={
            "script": "s.py",
            "index": 1,
            "run_config": "rc_a",
            "forget": True,
        },
    )
    assert response.status_code == 200
    assert {e.get("run_config") for e in response.get_json()["scripts"]["s.py"]} == {None, "rc_b"}
    assert recall(answers, "rc_b", "ctx:beta") == "beta"


@pytest.mark.parametrize(
    "scope",
    [
        {"index": None},
        {"run_config": "rc_a"},
        {"index": 1, "run_config": 3},
        {"index": 1, "run_config": ""},
        {"forget": "true"},
    ],
)
def test_malformed_or_incomplete_delete_scope_never_becomes_bulk(client, answers, scope):
    before = scriptanswers.answers_path(answers).read_bytes()
    response = client.post(
        "/api/script_input/answers", json={"script": "s.py", "forget": True, **scope}
    )
    assert response.status_code == 400
    assert scriptanswers.answers_path(answers).read_bytes() == before


def test_unknown_reference_never_falls_back_to_a_different_answer(client, answers):
    before = scriptanswers.answers_path(answers).read_bytes()
    for action in ({"answer": "wrong"}, {"forget": True}):
        response = client.post(
            "/api/script_input/answers",
            json={"script": "s.py", "index": 1, "run_config": "rc_unknown", **action},
        )
        assert response.status_code == 404
    assert scriptanswers.answers_path(answers).read_bytes() == before


def test_http_legacy_and_explicit_bulk_delete(client, answers):
    response = client.post(
        "/api/script_input/answers", json={"script": "s.py", "index": 1, "answer": "new-legacy"}
    )
    assert response.status_code == 200
    assert recall(answers, "rc_a", "ctx:alpha") == "alpha"
    assert inputtranscript.lookup(answers, "s.py", "rc_a") is not None
    assert (
        client.post(
            "/api/script_input/answers", json={"script": "s.py", "forget": True}
        ).status_code
        == 200
    )
    assert scriptanswers.load(answers) == {}
    assert inputtranscript.lookup(answers, "s.py", "rc_a") is None


def test_probe_resolves_only_the_requested_configuration(client, answers, monkeypatch):
    (answers / "s.py").write_text("pass\n", "utf-8")
    cfg = runconfig.put(answers, "s.py", ["--token", "SENTINEL-PRIVATE"], sensitive=True)
    calls = []

    def probe(root, script, **kwargs):
        calls.append(kwargs.get("run"))
        return {"registered": False, "error": None, "descriptors": []}

    monkeypatch.setattr(m.engine_probe, "probe_and_register", probe)
    response = client.post("/api/registry/probe", json={"script": "s.py", "run_config": cfg.id})
    assert response.status_code == 200
    assert calls[-1] == runconfig.selection(answers, cfg.id)
    assert "SENTINEL-PRIVATE" not in response.get_data(as_text=True)
    response = client.post("/api/registry/probe", json={"script": "s.py", "run_config": None})
    assert response.status_code == 200 and calls[-1] is None
    runconfig.forget_secrets()
    response = client.post("/api/registry/probe", json={"script": "s.py", "run_config": cfg.id})
    assert response.get_json()["code"] == "run_config_secret_missing"
    assert len(calls) == 2


@pytest.mark.parametrize(
    "extra", [{"run_config": 3}, {"run_config": "rc_unknown"}, {"run_config": "rc_a", "argv": []}]
)
def test_probe_rejects_invalid_or_ambiguous_reference_without_executing(
    client, answers, monkeypatch, extra
):
    (answers / "s.py").write_text("pass\n", "utf-8")
    calls = []
    monkeypatch.setattr(m.engine_probe, "probe_and_register", lambda *a, **kw: calls.append(kw))
    response = client.post("/api/registry/probe", json={"script": "s.py", **extra})
    assert response.status_code in (400, 409)
    assert calls == []
