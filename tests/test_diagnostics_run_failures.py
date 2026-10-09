"""脚本运行失败要在全局诊断报告里看得出来（bundle schema 6）。

2026-10 Windows 实测：脚本跑失败好几次（缺模块、没捕获到图），导出的报告里 `recent_errors: []`、
`worker_logs[0].empty: True`、`missing_dependencies: []`，只能靠截图判断。根因：试运行与准备两条路把
`WorkerError` 接在自己里面（200 + `error` / 会话终局），既不走 `app._worker_error`（那里才写 ERROR 日志和
缺依赖现场），脚本的异常又由 worker 协议带回、不写 worker.log。这里钉住：失败运行进 `project.recent_runs`
（只有结果分类与稳定错误码），缺依赖现场覆盖试运行与准备，敏感运行不带模块名。
"""

# ruff: noqa: F811 — 夹具从兄弟测试文件导入复用，参数名与导入名相同
from __future__ import annotations

import io
import json
import zipfile

import pytest

from tavotto import app as m
from tavotto.engine import (
    deprepair,
    diagnostics as engine_diagnostics,
    pool as engine_pool,
    taskdiag,
)
from test_preparation_api import (  # noqa: F401
    _build_resp,
    _open,
    _project,
    client as prep_client,
    fake_pool,
)
from test_task_diagnostics import (  # noqa: F401
    SECRET,
    SESSIONS,
    _clean_store,
    _run_session,
    assert_no_secret,
    run_aware_pool,
    sessions,
)

PRIVATE_MODULE = "patient_private_mod"


@pytest.fixture(autouse=True)
def _no_evidence():
    deprepair.clear_missing_dependency_evidence()
    yield
    deprepair.clear_missing_dependency_evidence()


def _probe(client, pj, argv=None, sensitive=False):
    body = {"script": "fig.py"}
    if argv is not None:
        body.update(argv=argv, argv_sensitive=sensitive)
    return client.post("/api/registry/probe", json=body, query_string={"pj": pj})


def _fail_build(monkeypatch, exc):
    def build(*_a, **_kw):
        raise exc

    monkeypatch.setattr(m.engine_pool, "build", build)
    monkeypatch.setattr(m.engine_pool, "invalidate", lambda *a, **k: None)
    monkeypatch.setattr(deprepair, "offer", lambda *a, **k: None)


def _missing(module):
    exc = engine_pool.WorkerError(
        f"缺包 {SECRET}", f"ModuleNotFoundError: {SECRET}", code="missing_dependency", module=module
    )
    exc.python_source = engine_pool.SOURCE_BUNDLED
    return exc


def _report(client, pj):
    return client.get("/api/diagnostics/summary", query_string={"pj": pj}).get_json()["report"]


def _all_texts(client, pj) -> dict[str, str]:
    res = client.get("/api/diagnostics/bundle", query_string={"pj": pj})
    z = zipfile.ZipFile(io.BytesIO(res.data))
    texts = {n: z.read(n).decode("utf-8", errors="replace") for n in z.namelist()}
    texts["<复制诊断>"] = client.get("/api/diagnostics/summary", query_string={"pj": pj}).get_data(
        as_text=True
    )
    return texts


@pytest.fixture
def opened(prep_client, tmp_path):
    root = _project(tmp_path, "PRIVATE_PROJECT_NAME")
    return prep_client, _open(prep_client, root), root


def test_failed_probe_shows_in_report_with_category_and_module(opened, monkeypatch):
    client, pj, _root = opened
    _fail_build(monkeypatch, _missing("adjustText"))
    assert (
        _probe(client, pj).status_code == 200
    )  # 试运行失败是 200 + error，这正是 recent_errors 看不到它的原因

    proj = _report(client, pj)["project"]
    runs = proj["recent_runs"]
    assert runs["counts"] == {"error": 1}
    assert runs["by_error_code"] == {"missing_dependency": 1}
    (item,) = runs["recent"]
    assert item["kind"] == "script_run" and item["outcome"] == "error"
    assert item["error_code"] == "missing_dependency"
    # 缺的包名走 missing_dependencies（试运行路径以前一条都不记）
    (dep,) = proj["missing_dependencies"]
    assert dep["import_name"] == "adjustText" and dep["python_source"] == "bundled"


def test_no_figure_run_is_classified(opened, monkeypatch):
    client, pj, _root = opened
    monkeypatch.setattr(m.engine_pool, "build", lambda *a, **k: (object(), {"stems": {}}))
    monkeypatch.setattr(m.engine_pool, "invalidate", lambda *a, **k: None)
    monkeypatch.setattr(m.engine_pool, "missing_input_offer", lambda *a, **k: None)
    assert _probe(client, pj).status_code == 200
    runs = _report(client, pj)["project"]["recent_runs"]
    assert runs["by_error_code"] == {"script_no_figure": 1}
    assert runs["recent"][0]["captured_count"] == 0


def test_sensitive_run_reports_category_but_no_module_name(opened, monkeypatch):
    client, pj, _root = opened
    # worker 在敏感运行里源头就清空 module（worker.py `_module_attributable_to_script`）
    _fail_build(monkeypatch, _missing(""))
    assert _probe(client, pj, argv=["--token", SECRET], sensitive=True).status_code == 200
    proj = _report(client, pj)["project"]
    assert proj["recent_runs"]["by_error_code"] == {"missing_dependency": 1}
    assert proj["missing_dependencies"] == []
    for name, text in _all_texts(client, pj).items():
        assert_no_secret(text)
        assert PRIVATE_MODULE not in text, name


def test_run_summary_never_leaks_script_path_argv_or_message(opened, monkeypatch, tmp_path):
    client, pj, root = opened
    _fail_build(monkeypatch, _missing(PRIVATE_MODULE))
    assert _probe(client, pj, argv=["--out", SECRET]).status_code == 200
    texts = _all_texts(client, pj)
    for name, text in texts.items():
        assert_no_secret(text)
        assert str(root) not in text, name
        assert "PRIVATE_PROJECT_NAME" not in text, name
        assert "fig.py" not in json.dumps(
            json.loads(texts["report.json"])["project"]["recent_runs"]
        )
    # 私有模块名只以哈希形态出现（既有 missing_dependencies 的出处规则）
    assert PRIVATE_MODULE not in texts["report.json"]
    assert "mod:" in texts["report.json"]
    manifest = json.loads(texts["manifest.json"])
    assert manifest["schema_version"] == engine_diagnostics.BUNDLE_SCHEMA_VERSION == 6


def test_failed_preparation_is_counted_and_records_missing_dependency(
    prep_client, tmp_path, run_aware_pool, sessions
):
    client, fake_pool = prep_client, run_aware_pool
    root = _project(tmp_path, "p")
    (root / "tavotto_registry.json").unlink()
    (root / "fig.pdf").unlink()
    pj = _open(client, root)
    fake_pool["error"] = _missing("adjustText")
    _run_session(client, pj, {"script": "fig.py"})
    proj = _report(client, pj)["project"]
    assert proj["recent_runs"]["recent"][0]["kind"] == "preparation"
    assert proj["recent_runs"]["by_error_code"] == {"missing_dependency": 1}
    assert [r["import_name"] for r in proj["missing_dependencies"]] == ["adjustText"]


def test_failed_preparation_carries_repair_offer_in_missing_dependency(
    prep_client, tmp_path, run_aware_pool, sessions
):
    """准备路径的缺依赖现场带修复 offer（与试运行路径同一个 `deprepair.offer`），不是恒为 null。"""
    client, fake_pool = prep_client, run_aware_pool
    root = _project(tmp_path, "p")
    (root / "tavotto_registry.json").unlink()
    (root / "fig.pdf").unlink()
    pj = _open(client, root)
    fake_pool["error"] = _missing("scipy")
    _run_session(client, pj, {"script": "fig.py"})
    (dep,) = _report(client, pj)["project"]["missing_dependencies"]
    assert dep["import_name"] == "scipy"
    assert dep["repair"] is not None


def test_run_summary_is_scoped_to_its_project_and_kind(opened, monkeypatch):
    client, pj, _root = opened
    _fail_build(monkeypatch, _missing("adjustText"))
    _probe(client, pj)
    store = taskdiag.STORE
    # 导出不是脚本运行；别的项目的记录不出现在本项目
    store.record(
        "other",
        taskdiag.KIND_SCRIPT_RUN,
        "run-x",
        {"kind": "script_run"},
        outcome="error",
        failed=True,
    )
    store.record(
        pj, taskdiag.KIND_EXPORT, "exp-1", {"kind": "export"}, outcome="failed", failed=True
    )
    runs = taskdiag.run_summary(store, pj)
    assert runs["window"]["entries"] == 1 and len(runs["recent"]) == 1


def test_run_summary_is_bounded_and_tolerates_unreadable_blobs():
    store = taskdiag.Store()
    for i in range(20):
        store.record(
            "p",
            taskdiag.KIND_SCRIPT_RUN,
            f"run-{i}",
            {"kind": "script_run", "error": {"code": "script_probe_failed"}},
            outcome="error",
            failed=True,
        )
    # 读不出结构的快照当不存在，不抛
    (entry,) = [e for e in store._entries.values() if e.ref == "run-3"]
    store._entries[("p", "script_run", "run-3")] = type(entry)(
        **{**entry.__dict__, "blob": "{not json"}
    )
    runs = taskdiag.run_summary(store, "p")
    assert len(runs["recent"]) == taskdiag.RUN_SUMMARY_LIMIT
    assert runs["counts"] == {"error": 20}
    assert runs["by_error_code"] == {"script_probe_failed": 19}


def test_finalizer_failure_after_ready_is_a_failed_run_with_its_code(
    prep_client, tmp_path, run_aware_pool, sessions, monkeypatch
):
    """准备已按「执行成功」冻成 ready，之后构建后的登记（finalizer）失败、会话终局是 partial：
    recent_runs / 单次诊断都要读成失败并带登记错误码，不能算 ready。"""
    client = prep_client
    root = _project(tmp_path, "p")
    (root / "tavotto_registry.json").unlink()
    (root / "fig.pdf").unlink()
    pj = _open(client, root)
    monkeypatch.setattr(
        m.engine_probe,
        "register_probed",
        lambda *a, **k: {"registered": False, "error": {"code": "stem_conflict"}},
    )
    _run_session(client, pj, {"script": "fig.py"})
    runs = _report(client, pj)["project"]["recent_runs"]
    assert runs["counts"] == {"error": 1}
    assert runs["by_error_code"] == {"stem_conflict": 1}
    item = runs["recent"][0]
    assert item["kind"] == "preparation" and item["outcome"] == "error"
    assert item["error_code"] == "stem_conflict"
    assert item["captured_count"] == 1


def test_finalizer_failure_code_is_read_from_nested_error_shape(
    prep_client, tmp_path, run_aware_pool, sessions, monkeypatch
):
    """finalizer 自己返回 `register_probed` 的嵌套形状（`error.code`）时，recent_runs 也要带该码，
    不能落成 registration_failed。上一条用例经过真实的 `_finalize_script_attempt`（它把码展平到顶层），
    测不到 `_done` 对嵌套形状的读取，这里直接替换 finalizer。"""
    client = prep_client
    root = _project(tmp_path, "p")
    (root / "tavotto_registry.json").unlink()
    (root / "fig.pdf").unlink()
    pj = _open(client, root)
    monkeypatch.setattr(
        m,
        "_finalize_script_attempt",
        lambda *a, **k: {"registered": False, "error": {"code": "stem_conflict"}},
    )
    _run_session(client, pj, {"script": "fig.py"})
    runs = _report(client, pj)["project"]["recent_runs"]
    assert runs["by_error_code"] == {"stem_conflict": 1}
    assert runs["recent"][0]["error_code"] == "stem_conflict"


def test_preparation_entry_has_elapsed_ms(prep_client, tmp_path, run_aware_pool, sessions):
    client = prep_client
    root = _project(tmp_path, "p")
    (root / "tavotto_registry.json").unlink()
    (root / "fig.pdf").unlink()
    pj = _open(client, root)
    _run_session(client, pj, {"script": "fig.py"})
    item = _report(client, pj)["project"]["recent_runs"]["recent"][0]
    assert item["kind"] == "preparation"
    assert isinstance(item["elapsed_ms"], int) and item["elapsed_ms"] >= 0
