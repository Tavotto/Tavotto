"""诊断报告随附的可选错误摘要（`engine/diagsummary.py` + `diagsend._init`，ADR 0134）。

对端是 `tests/support/diag_fakes.py` 的本地模拟：默认是**尚未部署 infra #28 的旧服务端**（`extra="forbid"`，
未知字段 422 `bad_request`）；`accepts_error_summary = True` 是升级后的新服务端。判据的主语是**服务端那一侧
收到的 init 请求体**，不是客户端自己说发了什么。

契约出处：infra 仓库 `docs/diagnostics-api/error-tracking-summary.md` / `contract.ERRSUM_*`；这里的正则表是它的镜像。
"""

from __future__ import annotations

import io
import json
import re
import ssl
import time
import zipfile
from pathlib import Path

import pytest

from support.diag_fakes import FakeDiagServer, Forced
from support.tls_certs import make_pki
from tavotto.engine import diagsend, diagsummary, taskdiag, telemetry, tlstrust

PJ = "pj-summary"
SERVER_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{2,47}$")
SERVER_MODULE_RE = re.compile(r"^tavotto(?:\.[a-z][a-z0-9_]{0,31}){1,6}$")
SERVER_FUNCTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SERVER_COMPONENTS = {
    "engine", "render", "export", "environment", "editor", "desktop", "updater", "worker", "diagnostics",
}  # fmt: skip
SERVER_OPERATIONS = {
    "render", "export", "load", "save", "startup", "update", "repair", "edit", "verify", "other",
}  # fmt: skip
CANARY = "CANARY_PATH_/Users/secret/fig.pdf"


# --------------------------------------------------------------------------- 装置
@pytest.fixture(scope="module")
def pki_files(tmp_path_factory) -> dict[str, Path]:
    return make_pki().write(tmp_path_factory.mktemp("diag-pki"))


@pytest.fixture
def srv(pki_files):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(pki_files["cert"]), str(pki_files["key"]))
    with FakeDiagServer(ctx) as s:
        yield s


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, srv, pki_files, telemetry_sent):
    """遥测打开（当前版本同意）+ 诊断发送指向模拟服务端。`telemetry_sent` 已拦下遥测传输层。"""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    for name in (diagsend.ENV_ENABLE, diagsend.ENV_HARD_OFF):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        tlstrust, "client_context", lambda: ssl.create_default_context(cafile=str(pki_files["ca"]))
    )
    monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
    monkeypatch.setenv(diagsend.ENV_ENDPOINT, srv.base)
    monkeypatch.setattr(diagsend, "_sleep", lambda s: None)
    diagsend.reset_for_tests()
    taskdiag.reset_for_tests()
    yield
    diagsend.reset_for_tests()
    taskdiag.reset_for_tests()


def _fail(kind: str = taskdiag.KIND_EXPORT, error_code: str = "export_failed", ref: str = "job1"):
    taskdiag.STORE.record(
        PJ,
        kind,
        ref,
        {"snapshot_version": 1, "kind": kind, "error": {"code": error_code}},
        outcome="failed",
        failed=True,
    )


def _bundle() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("report.json", json.dumps({"tavotto": {"version": "0.0.0"}}))
        z.writestr("manifest.json", json.dumps({"schema_version": 6}))
        z.writestr("README.txt", "x")
    return buf.getvalue()


def _wait(pid: str, states: set[str]) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        st = diagsend.status(pid, scope=PJ)
        if st["state"] in states:
            return st
        time.sleep(0.01)
    raise AssertionError(diagsend.status(pid, scope=PJ))


def _send(pid: str | None = None, *, note: str | None = None) -> str:
    if pid is None:
        pid = diagsend.prepare(_bundle(), scope=PJ)["id"]
    diagsend.start(pid, confirmed=True, category="render_failure", note=note, scope=PJ)
    return pid


def _inits(srv) -> list[dict]:
    return [c["json"] for c in srv.calls if c["path"] == "/v1/reports/init"]


# --------------------------------------------------------------------------- 契约镜像
def test_shape_constants_mirror_the_server_contract():
    assert diagsummary.ERROR_CODE_RE.pattern == SERVER_CODE_RE.pattern
    assert diagsummary.FRAME_MODULE_RE.pattern == SERVER_MODULE_RE.pattern
    assert diagsummary.FRAME_FUNCTION_RE.pattern == SERVER_FUNCTION_RE.pattern
    assert diagsummary.COMPONENTS == SERVER_COMPONENTS
    assert diagsummary.OPERATIONS == SERVER_OPERATIONS
    assert diagsummary.MAX_FRAMES == 8 and diagsummary.MAX_LINENO == 99_999
    # 我们实际会用到的每一对 (组件, 操作) 都在服务端闭集里
    for component, operation in diagsummary._SOURCES.values():
        assert component in SERVER_COMPONENTS and operation in SERVER_OPERATIONS


# --------------------------------------------------------------------------- 带什么
def test_summary_is_the_closed_projection_with_a_consent_snapshot(srv):
    srv.accepts_error_summary = True
    _fail(error_code="export_dir_unwritable")
    pid = _send(note=CANARY)
    _wait(pid, {"done"})
    (init,) = _inits(srv)
    assert init["error_summary"] == {
        "schema": 1,
        "consent": {
            "telemetry_consent": True,
            "no_telemetry_env": False,
            "scope_version": 3,
            "purposes": ["error_summary"],
        },
        "facts": {
            "component": "export",
            "operation": "export",
            "error_code": "export_dir_unwritable",
            "frames": [],
        },
    }
    blob = json.dumps(init["error_summary"])
    assert CANARY not in blob and "install_id" not in blob and "time" not in blob
    assert telemetry.install_id() not in blob


def test_dependency_failures_map_to_environment_repair(srv):
    srv.accepts_error_summary = True
    _fail(kind=taskdiag.KIND_DEPENDENCY, error_code="pip_failed", ref="plan1")
    _wait(_send(), {"done"})
    facts = _inits(srv)[0]["error_summary"]["facts"]
    assert (facts["component"], facts["operation"], facts["error_code"]) == (
        "environment",
        "repair",
        "pip_failed",
    )


def test_the_newest_internal_failure_wins(srv):
    srv.accepts_error_summary = True
    taskdiag.STORE.record(
        PJ, taskdiag.KIND_EXPORT, "old", {"kind": "export", "error": {"code": "export_old"}},
        outcome="failed", failed=True, now=time.time() - 100,
    )  # fmt: skip
    _fail(kind=taskdiag.KIND_DEPENDENCY, error_code="pip_failed", ref="new")
    _wait(_send(), {"done"})
    assert _inits(srv)[0]["error_summary"]["facts"]["error_code"] == "pip_failed"


# --------------------------------------------------------------------------- 不带什么
@pytest.mark.parametrize("why", ["unset", "disabled", "stale_v2", "env"])
def test_no_valid_consent_means_no_summary(srv, monkeypatch, why):
    srv.accepts_error_summary = True
    _fail()
    if why == "unset":
        telemetry._save({"consent": "unset", "consent_version": 0})
    elif why == "disabled":
        telemetry.set_consent(telemetry.CONSENT_DISABLED)
    elif why == "stale_v2":
        telemetry._save({"consent": "enabled", "consent_version": 2})
    else:
        monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    _wait(_send(), {"done"})
    (init,) = _inits(srv)
    assert "error_summary" not in init


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param(lambda: None, id="no_failure_at_all"),
        pytest.param(
            lambda: _fail(kind=taskdiag.KIND_SCRIPT_RUN, error_code="script_error"), id="script_run"
        ),
        pytest.param(
            lambda: _fail(kind=taskdiag.KIND_PREPARATION, error_code="prep_error"), id="preparation"
        ),
        pytest.param(lambda: _fail(error_code="ab"), id="code_too_short_for_the_server_shape"),
        pytest.param(lambda: _fail(error_code="x" * 49), id="code_too_long"),
    ],
)
def test_without_qualifying_internal_facts_no_summary(srv, setup):
    srv.accepts_error_summary = True
    setup()
    _wait(_send(), {"done"})
    assert "error_summary" not in _inits(srv)[0]


def test_frame_validator_is_closed():
    ok = {"module": "tavotto.engine.exporter", "function": "export_pdf", "lineno": 120}
    assert diagsummary._frame_ok(ok)
    for bad in (
        {**ok, "module": "numpy.core"},
        {**ok, "module": "/Users/x/tavotto/engine.py"},
        {**ok, "module": "tavotto"},
        {**ok, "function": "has space"},
        {**ok, "lineno": 0},
        {**ok, "lineno": 100_000},
        {**ok, "lineno": True},
        {**ok, "path": "/x"},
        {"module": ok["module"], "function": ok["function"]},
    ):
        assert not diagsummary._frame_ok(bad), bad


# --------------------------------------------------------------------------- 部署顺序保险
def test_old_server_rejecting_the_field_still_gets_the_report(srv):
    """infra #28 未部署：带摘要的 init 被 422 bad_request 拒——去掉摘要重发一次，报告照样送达。"""
    srv.accepts_error_summary = False
    _fail()
    pid = _send()
    st = _wait(pid, {"done", "failed"})
    assert st["state"] == "done" and st["report_id"].startswith("TVD-")
    inits = _inits(srv)
    assert len(inits) == 2, "恰好重试一次"
    assert "error_summary" in inits[0] and "error_summary" not in inits[1]
    assert {k: v for k, v in inits[0].items() if k != "error_summary"} == inits[1]
    assert next(iter(srv.reports.values())).state == "complete"


def test_after_a_refusal_the_session_stops_sending_the_summary(srv):
    srv.accepts_error_summary = False
    _fail()
    srv.force("upload", Forced("status", 503, "storage_unavailable"))
    pid = _send()
    assert _wait(pid, {"failed"})["state"] == "failed"
    n = len(_inits(srv))
    _send(pid)  # 用户再点一次发送
    assert _wait(pid, {"done"})["state"] == "done"
    later = _inits(srv)[n:]
    assert later and all("error_summary" not in b for b in later), "此会话不再带摘要"


def test_other_422s_are_not_retried_without_the_summary(srv):
    """只有「请求形状」类（bad_request）才去摘要重发；服务端因别的原因拒（如 notes_disabled）原样失败。"""
    srv.accepts_error_summary = True
    srv.notes_enabled = False
    _fail()
    pid = _send(note="hello")
    st = _wait(pid, {"failed"})
    assert st["code"] == "notes_disabled"
    assert len(_inits(srv)) == 1


# --------------------------------------------------------------------------- 幂等
def test_retry_with_the_same_summary_reuses_the_request_id(srv):
    srv.accepts_error_summary = True
    _fail()
    srv.force("upload", Forced("status", 503, "storage_unavailable"))
    pid = _send()
    _wait(pid, {"failed"})
    _send(pid)
    _wait(pid, {"done"})
    a, b = _inits(srv)[:2]
    assert (
        a["client_request_id"] == b["client_request_id"]
        and a["error_summary"] == b["error_summary"]
    )


def test_withdrawing_consent_between_attempts_drops_the_summary_without_a_409(srv):
    """摘要是 init 不可变提交的一部分：关掉统计后再点发送，换新的 client_request_id、不带摘要，而不是撞 409。"""
    srv.accepts_error_summary = True
    _fail()
    srv.force("upload", Forced("status", 503, "storage_unavailable"))
    pid = _send()
    _wait(pid, {"failed"})
    telemetry.set_consent(telemetry.CONSENT_DISABLED)
    _send(pid)
    st = _wait(pid, {"done", "failed"})
    assert st["state"] == "done"
    a, b = _inits(srv)[:2]
    assert "error_summary" in a and "error_summary" not in b
    assert a["client_request_id"] != b["client_request_id"]


# --------------------------------------------------------------------------- 摘要坏了不挡报告（Codex #1103 P2）
def test_malformed_saved_consent_version_still_sends_the_report_without_summary(srv):
    """真实形状：手改 / 旧版遗留的 `consent_version: "v2"`。报告照发，只是不带摘要。"""
    srv.accepts_error_summary = True
    _fail()
    with telemetry.config.transaction() as cfg:
        cfg["telemetry"]["consent_version"] = "v2"
    pid = _send()
    _wait(pid, {"done"})
    (init,) = _inits(srv)
    assert "error_summary" not in init


def test_any_failure_while_building_the_summary_is_omission_not_an_error(srv, monkeypatch):
    srv.accepts_error_summary = True
    _fail()

    def boom(project_id):
        raise RuntimeError("summary exploded")

    monkeypatch.setattr(diagsummary, "_facts", boom)
    pid = _send()
    _wait(pid, {"done"})
    (init,) = _inits(srv)
    assert "error_summary" not in init


# --------------------------------------------------------------------------- 部分成功的导出（Codex #1103 第二轮 P2）
def _partial(ref: str, *, failed_code=None, output_code=None, now=None):
    """`exportjob.diagnostic_projection` 的部分成功形状：没有顶层 error，码在 stages / outputs。"""
    snap = {
        "snapshot_version": 1,
        "kind": "export",
        "outcome": "partial",
        "stages": {"events": [], "truncated": False, "dropped": 0, "failed_code": failed_code},
        "outputs": [
            {"format": "pdf", "status": "done", "error_code": None},
            {"format": "png", "status": "failed", "error_code": output_code},
        ],
    }
    taskdiag.STORE.record(
        PJ, taskdiag.KIND_EXPORT, ref, snap, outcome="partial", failed=True,
        **({"now": now} if now is not None else {}),
    )  # fmt: skip


@pytest.mark.parametrize(
    ("failed_code", "output_code", "expected"),
    [("raster_failed", None, "raster_failed"), (None, "png_encode_failed", "png_encode_failed")],
)
def test_partial_export_snapshot_yields_its_stable_code(srv, failed_code, output_code, expected):
    srv.accepts_error_summary = True
    _partial("p1", failed_code=failed_code, output_code=output_code)
    _wait(_send(), {"done"})
    assert _inits(srv)[0]["error_summary"]["facts"]["error_code"] == expected


def test_newest_failure_without_a_code_falls_back_to_an_older_one(srv):
    srv.accepts_error_summary = True
    taskdiag.STORE.record(
        PJ, taskdiag.KIND_EXPORT, "old", {"kind": "export", "error": {"code": "export_old"}},
        outcome="failed", failed=True, now=time.time() - 100,
    )  # fmt: skip
    _partial("new")  # 最新的这条一个合格的码都没有
    _wait(_send(), {"done"})
    assert _inits(srv)[0]["error_summary"]["facts"]["error_code"] == "export_old"
