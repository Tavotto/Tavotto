"""「发送问题反馈」的 `detail/`（bundle schema 8，ADR 0118 修订 2026-10-13）。

判据的主语：**发送包里每个文件的全部字节**（不是某一个字段）——家目录 / 用户名 / 秘密 / 脚本源码这些金丝雀
在哪个文件里都必须是 0；每条「0」都带一条反证（金丝雀确实进了原始输入、而且处理后的标记出现了），
免得因为什么都没读到而「搜不到」。导出到文件的老路径（`GET/POST /api/diagnostics/bundle`）不含 `detail/`、
不读发送载荷里的 `detail`。
"""

from __future__ import annotations

import io
import json
import os
import time
import zipfile

import pytest

from tavotto import app as m
from tavotto.engine import (
    diagdetail,
    diagnostics,
    diagnostics_frontend as dfe,
    diagsend,
    layoutsession,
    pool,
)

REAL_PROBE = diagdetail._probe_subprocess  # conftest 的桩在 fixture 里才装；模块导入时拿到的是真的

USER = "canaryuser9"
SRC_CANARY = "SRC_CANARY_LINE_zq81"
SECRETS = [
    "sk-CANARYSECRETKEY1234567890",
    "CANARY_APIKEY_VALUE",
    "CANARY_BEARER_TOKEN_abc123",
    "CANARY_COOKIE_VALUE",
    "CANARY_NONCE_77",
    "CANARY_PROXY_PW",
    "CANARY_SIG_55",
    "CANARY_TOKEN_JSON",
    "CANARY_URL_PW",
]


@pytest.fixture
def client(monkeypatch):
    for name in (
        diagsend.ENV_ENABLE,
        diagsend.ENV_HARD_OFF,
        diagsend.ENV_ENDPOINT,
        diagsend.ENV_DEV_LOOPBACK,
    ):
        monkeypatch.delenv(name, raising=False)
    m.app.config["TESTING"] = True
    m.reset_projects()
    diagsend.reset_for_tests()
    diagdetail.REQUESTS.clear()
    yield m.app.test_client()
    diagsend.reset_for_tests()
    m.reset_projects()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """一台「有金丝雀」的机器：家目录与用户名带标记，各类日志 / 状态文件 / 崩溃报告里都埋了东西。"""
    home = tmp_path / USER
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for name in ("USER", "USERNAME", "LOGNAME"):
        monkeypatch.setenv(name, USER)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxyuser:CANARY_PROXY_PW@proxy.example:8080")

    traceback = (
        "Traceback (most recent call last):\n"
        f'  File "{home}/proj/fig_one.py", line 7, in main\n'
        f"    {SRC_CANARY} = boom(api_key=CANARY_APIKEY_VALUE)\n"
        "    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
        "RuntimeError: render failed at "
        f"{home}/proj/data.csv Authorization: Bearer CANARY_BEARER_TOKEN_abc123\n"
    )
    app_log = tmp_path / "app.log"
    app_log.write_text(
        "2026-10-13 10:00:00,000 INFO tavotto: 开始渲染 fig_one.py\n"
        f"2026-10-13 10:00:01,000 ERROR tavotto: 引擎渲染失败: fig_one\n{traceback}"
        f"2026-10-13 10:00:02,000 INFO tavotto: Cookie: tavotto_session=CANARY_COOKIE_VALUE nonce=CANARY_NONCE_77 user {USER}\n"
        f'2026-10-13 10:00:03,000 INFO tavotto: {{"token": "CANARY_TOKEN_JSON", "q": "https://u:CANARY_URL_PW@h.example/x?q-signature=CANARY_SIG_55"}} sk-CANARYSECRETKEY1234567890\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(diagnostics, "_app_log_path", lambda: app_log)
    sidecar = tmp_path / "sidecar.log"
    sidecar.write_text(f"sidecar started in {home}/Library as {USER}\n", encoding="utf-8")
    monkeypatch.setattr(diagdetail, "sidecar_log_file", lambda: sidecar)
    worker = tmp_path / "worker.log"
    worker.write_text(traceback, encoding="utf-8")
    monkeypatch.setattr(
        diagnostics, "owned_worker_logs", lambda project_dir, root=None: [(time.time(), worker)]
    )

    state = tmp_path / "state"
    state.mkdir()
    (state / layoutsession.FILENAME).write_text(
        json.dumps({"version": 1, "path": f"{home}/proj", "owner": USER}), encoding="utf-8"
    )
    (state / (layoutsession.FILENAME + layoutsession.BACKUP_SUFFIX)).write_text(
        "{}", encoding="utf-8"
    )
    (state / f"{layoutsession.CORRUPT_PREFIX}123.json").write_text(
        "CORRUPT_CANARY_BYTES {{{", encoding="utf-8"
    )
    (state / layoutsession.LOCK_FILENAME).write_text("lock", encoding="utf-8")
    (state / "export-defaults.json").write_text('{"fmt": "pdf"}', encoding="utf-8")
    monkeypatch.setattr(diagdetail, "state_dir", lambda: state)
    monkeypatch.setattr(layoutsession, "state_path", lambda: state / layoutsession.FILENAME)

    crash = tmp_path / "DiagnosticReports"
    crash.mkdir()
    (crash / "Tavotto-2026-10-13-120000.ips").write_text(
        f'{{"app_name":"Tavotto"}}\nCRASH_CANARY_BODY at {home}/x as {USER}', encoding="utf-8"
    )
    monkeypatch.setattr(diagdetail, "crash_dirs", lambda: [("macos_diagnostic_reports", crash)])
    return {"home": home, "tmp": tmp_path, "state": state, "crash": crash}


def _project(client, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig_one.py").write_text(
        "import matplotlib.pyplot as plt\n\n\ndef main():\n"
        f"    {SRC_CANARY} = 1\n    fig, ax = plt.subplots()\n    fig.savefig('fig_one.pdf')\n    return fig\n",
        encoding="utf-8",
    )
    assert client.post("/api/projects/open", json={"path": str(proj)}).status_code == 200
    return proj


PAYLOAD = {
    "detail": {
        "console": [
            {
                "t": 1,
                "level": "error",
                "message": f"console failure for {USER} key=CANARY_APIKEY_VALUE",
                "stack": "Error: x\n    at foo (app.js:1:1)",
            }
        ],
        "errors": [{"t": 2, "level": "unhandledrejection", "message": "nope", "stack": None}],
        "display": {
            "device_pixel_ratio": 2,
            "window_width": 1280,
            "language": "zh-CN",
            "theme": "dark",
        },
        "shell": {
            "app_version": "0.1.0",
            "tauri_version": "2.0",
            "webview_version": "Chrome/120",
            "desktop": True,
        },
        "overrides": [
            {
                "panel": "Fig One.pdf",
                "kind": "pdf",
                "script": "fig_one.py",
                "overrides": [{"gid": "axes_0.title", "prop": "text", "value": "My Paper Title"}],
            }
        ],
    }
}


def _prepare(client, payload=None, **opts):
    body = dict(payload if payload is not None else PAYLOAD)
    if opts:
        body["detail_options"] = opts
    r = client.post("/api/diagnostics/send/prepare", json=body)
    assert r.status_code == 200, r.get_json()
    prep = r.get_json()
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/diagnostics/send/{prep['id']}/bundle").data))
    return prep, z


def _texts(z: zipfile.ZipFile) -> dict[str, str]:
    return {n: z.read(n).decode("utf-8", "replace") for n in z.namelist()}


def _blob(z: zipfile.ZipFile) -> str:
    return "\n".join(_texts(z).values())


# ---------------------------------------------------------------------------
# 选项
# ---------------------------------------------------------------------------
class TestOptions:
    def test_default_is_all_false_and_only_literal_true_counts(self):
        assert diagdetail.parse_options(None).as_dict() == {
            "source": False,
            "crash_reports": False,
            "corrupt_sessions": False,
        }
        for bad in ("true", 1, "1", [True], {"x": 1}, 0.5):
            assert diagdetail.parse_options({"source": bad}).source is False
        got = diagdetail.parse_options({"source": True, "crash_reports": True, "junk": True})
        assert got.as_dict() == {"source": True, "crash_reports": True, "corrupt_sessions": False}

    def test_prepare_never_remembers_a_previous_choice(self, client, world):
        _prep, z1 = _prepare(client, source=True)
        assert json.loads(z1.read("manifest.json"))["detail_options"]["source"] is True
        prep2, z2 = _prepare(client)  # 同一进程里下一次 prepare 不带选项
        manifest = json.loads(z2.read("manifest.json"))
        assert manifest["detail_options"] == {
            "source": False,
            "crash_reports": False,
            "corrupt_sessions": False,
        }
        assert prep2["options"] == manifest["detail_options"]

    def test_changed_options_make_a_new_bundle_and_a_new_session(self, client, world):
        a, _ = _prepare(client)
        b, _ = _prepare(client, source=True)
        assert a["id"] != b["id"] and a["sha256"] != b["sha256"]
        # 旧会话被替换：拿旧 id 取包 404
        assert client.get(f"/api/diagnostics/send/{a['id']}/bundle").status_code == 404

    def test_oversize_payload_drops_options_back_to_false(self, client, world):
        body = {"detail_options": {"source": True}, "pad": "x" * (dfe.MAX_SEND_REQUEST_BYTES + 10)}
        r = client.post("/api/diagnostics/send/prepare", json=body)
        prep = r.get_json()
        assert prep["options"]["source"] is False


# ---------------------------------------------------------------------------
# 默认收：每一类都在、有内容
# ---------------------------------------------------------------------------
EXPECTED_DEFAULT = [
    "detail/errors.json",
    "detail/logs/app.log.tail",
    "detail/logs/sidecar.log.tail",
    "detail/logs/worker.log.tail",
    "detail/state/index.json",
    "detail/state/layout-sessions.json",
    "detail/state/export-defaults.json",
    "detail/requests.json",
    "detail/render-env.json",
    "detail/runs.json",
    "detail/shell.json",
    "detail/storage.json",
    "detail/frontend-console.json",
    "detail/display.json",
    "detail/network.json",
]


class TestDefaultContents:
    def test_every_default_category_is_present_and_non_empty(self, client, world):
        _project(client, world["tmp"])
        client.get("/api/project")  # 让请求环里有东西
        prep, z = _prepare(client)
        names = set(z.namelist())
        for n in EXPECTED_DEFAULT:
            assert n in names, n
            assert z.read(n).strip(), n
        assert any(n.startswith("detail/overrides/") for n in names)
        # 勾选项没勾：没有 optional 内容
        assert not [n for n in names if n.startswith("detail/optional/")]
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["schema_version"] == diagnostics.BUNDLE_SCHEMA_VERSION == 8
        assert manifest["contains_detail"] is True
        assert manifest["truncated"] == []
        listed = {f["path"]: f["bytes"] for f in manifest["detail_files"]}
        assert listed == {n: z.getinfo(n).file_size for n in names if n.startswith("detail/")}
        # prepare 响应带逐项清单与类别，且类别都在 ENTRY_KINDS 里
        kinds = {e["name"]: e["kind"] for e in prep["entries"]}
        assert kinds["detail/errors.json"] == "detail_errors"
        assert kinds["detail/logs/worker.log.tail"] == "detail_logs"
        assert set(kinds.values()) <= set(diagsend.ENTRY_KINDS.values()) | {"other"}
        sizes = {e["name"]: e["bytes"] for e in prep["entries"]}
        assert sizes["detail/errors.json"] == z.getinfo("detail/errors.json").file_size

    def test_error_originals_and_full_stacks(self, client, world):
        _prep, z = _prepare(client)
        errors = json.loads(z.read("detail/errors.json"))
        engine = " ".join(e["text"] for e in errors["engine"])
        assert "引擎渲染失败: fig_one" in engine and "RuntimeError: render failed" in engine
        assert 'File "~/proj/fig_one.py", line 7, in main' in engine  # 完整调用栈，家目录已写成 ~
        assert (
            errors["worker"]
            and "RuntimeError: render failed" in errors["worker"][0]["tracebacks"][0]
        )
        assert errors["frontend"][0]["message"] == "nope"
        console = json.loads(z.read("detail/frontend-console.json"))
        assert console["scope"].startswith("global")
        assert console["records"][0]["stack"].startswith("Error: x")

    def test_state_manifest_lists_corrupt_copy_but_not_its_content(self, client, world):
        _prep, z = _prepare(client)
        index = json.loads(z.read("detail/state/index.json"))
        kinds = {f["name"]: f["kind"] for f in index["files"]}
        assert kinds[layoutsession.FILENAME] == "primary"
        assert kinds[layoutsession.FILENAME + ".bak"] == "backup"
        assert kinds[f"{layoutsession.CORRUPT_PREFIX}123.json"] == "corrupt"
        assert layoutsession.LOCK_FILENAME not in kinds  # 锁文件除外
        assert "CORRUPT_CANARY_BYTES" not in _blob(z)

    def test_display_shell_network_storage_facts(self, client, world, monkeypatch):
        monkeypatch.setattr(diagdetail, "_resolve", lambda host: ["198.19.0.7"])
        _project(client, world["tmp"])
        _prep, z = _prepare(client)
        display = json.loads(z.read("detail/display.json"))
        assert display["device_pixel_ratio"] == 2 and display["theme"] == "dark"
        shell = json.loads(z.read("detail/shell.json"))
        assert shell["frontend_shell"]["tauri_version"] == "2.0"
        assert shell["in_app_update"]["status"] == "unknown"  # 取不到如实写 unknown
        assert shell["channel"]["basis"].startswith("inferred")
        assert "rosetta" in shell["cpu"] and "arch" in shell["cpu"]
        net = json.loads(z.read("detail/network.json"))
        assert net["proxy"]["configured"] is True
        assert net["proxy"]["env"]["HTTPS_PROXY"] == "http://proxy.example:8080"  # userinfo 没了
        assert all(d["tun_rewritten_198_18"] is True for d in net["dns"])
        assert "utc_offset_minutes" in net["timezone"]
        storage = json.loads(z.read("detail/storage.json"))
        assert storage["data_dir"]["disk"]["free_bytes"] > 0
        assert storage["project_dir"]["writable"] is True
        assert storage["windows_long_paths_enabled"] is None  # 非 Windows

    def test_overrides_live_and_baked(self, client, world):
        _project(client, world["tmp"])
        _prep, z = _prepare(client)
        live = [n for n in z.namelist() if n.startswith("detail/overrides/live-")]
        assert live
        doc = json.loads(z.read(live[0]))
        assert doc["overrides"][0]["value"] == "My Paper Title"  # 图里的文字保留

    def test_runs_carry_task_snapshots_and_engine_log_events(self, client, world):
        _prep, z = _prepare(client)
        runs = json.loads(z.read("detail/runs.json"))
        assert "task_snapshots" in runs
        assert any("引擎渲染失败" in e["text"] for e in runs["log_events"])


# ---------------------------------------------------------------------------
# 金丝雀：家目录 / 用户名 / 秘密在每个文件里都是 0
# ---------------------------------------------------------------------------
class TestScrubbing:
    def test_home_user_and_secrets_are_zero_in_every_file(self, client, world):
        _project(client, world["tmp"])
        _prep, z = _prepare(client, source=True, crash_reports=True, corrupt_sessions=True)
        for name, text in _texts(z).items():
            assert str(world["home"]) not in text, name
            assert USER not in text, name
            for secret in SECRETS:
                assert secret not in text, (name, secret)
        # 反证：确实读到了、而且被处理成了约定的标记
        blob = _blob(z)
        assert "~/proj" in blob and "<user>" in blob and "<redacted>" in blob
        assert "CRASH_CANARY_BODY" in blob and "CORRUPT_CANARY_BYTES" in blob  # 勾了才有
        assert "引擎渲染失败: fig_one" in blob and "fig_one.py" in blob  # 文件名、项目内容保留

    def test_scrubber_shapes(self, world):
        s = diagdetail.Scrubber()
        cases = {
            "Authorization: Bearer abcdef0123456789": "Authorization: <redacted>",
            "got Bearer abcdef0123456789 here": "got Bearer <redacted> here",
            "Cookie: a=b; c=d": "Cookie: <redacted>",
            "Set-Cookie: sid=1": "Set-Cookie: <redacted>",
            'password="hunter2xx"': 'password="<redacted>"',
            "token=abcdefgh&x=1": "token=<redacted>&x=1",
            "https://user:pw@host.example/a": "https://<redacted>@host.example/a",
            "X-Tavotto-Auth: deadbeefdeadbeef": "X-Tavotto-Auth: <redacted>",
        }
        for raw, want in cases.items():
            assert s.text(raw) == want, (raw, s.text(raw))
        assert s.text("max_tokens: 4096") == "max_tokens: 4096"  # 纯数字值不动
        assert s.text("the author wrote") == "the author wrote"  # auth 不是 author
        out = s.obj({"api_key": "k", "nested": {"Session_Token": "v"}, "name": f"/{USER}/x"})
        assert out["api_key"] == "<redacted>" and out["nested"]["Session_Token"] == "<redacted>"
        assert USER not in json.dumps(out)

    def test_scrubber_is_linear_on_long_tokens(self, world):
        s = diagdetail.Scrubber()
        for blob in ("a" * 800_000, "ab-" * 300_000, ("x=" + "k" * 50) * 20_000, "token" * 100_000):
            t0 = time.monotonic()
            s.text(blob)
            assert time.monotonic() - t0 < 5, blob[:12]

    def test_home_variants(self, world):
        home = str(world["home"])
        s = diagdetail.Scrubber()
        for raw in (home, home.replace("/", "\\"), home.replace("\\", "\\\\"), "file://" + home):
            assert home not in s.text(f"x {raw}/a") and USER not in s.text(f"x {raw}/a")
        assert s.text(f"{home}/a b/c") == "~/a b/c"

    def test_source_mode_keeps_expressions_but_drops_literals(self, world):
        s = diagdetail.Scrubber(source_mode=True)
        assert s.text("token = self._next_token()") == "token = self._next_token()"
        assert s.text('api_key = "abcd1234efgh"') == 'api_key = "<redacted>"'


class TestStripSourceLines:
    def test_removes_code_and_carets_keeps_frames_and_exception(self):
        text = (
            "Traceback (most recent call last):\n"
            '  File "/a/b.py", line 3, in <module>\n'
            "    x = foo(\n"
            "        1)\n"
            "    ~~~^^^\n"
            '  File "/a/c.py", line 9, in foo\n'
            "    raise ValueError(SRC)\n"
            "ValueError: bad\n"
            "next record\n"
        )
        out = diagdetail.strip_source_lines(text)
        assert "x = foo(" not in out and "raise ValueError(SRC)" not in out and "~~~^^^" not in out
        assert (
            '  File "/a/b.py", line 3, in <module>' in out
            and '  File "/a/c.py", line 9, in foo' in out
        )
        assert "ValueError: bad" in out and "next record" in out

    def test_syntax_error_and_warning_forms(self):
        text = (
            '  File "/a/fig.py", line 3\n'
            "    y = (\n"
            "        ^\n"
            "SyntaxError: '(' was never closed\n"
            "/a/fig.py:5: UserWarning: careful\n"
            "  plt.plot(SECRETCODE)\n"
        )
        out = diagdetail.strip_source_lines(text)
        assert "y = (" not in out and "SECRETCODE" not in out
        assert "SyntaxError: '(' was never closed" in out and "UserWarning: careful" in out


# ---------------------------------------------------------------------------
# 勾选项：源码 / 崩溃报告 / 损坏副本
# ---------------------------------------------------------------------------
class TestOptionalContent:
    def test_source_is_zero_bytes_when_unticked_everywhere(self, client, world):
        _project(client, world["tmp"])
        _prep, z = _prepare(client)
        for name, text in _texts(z).items():
            assert SRC_CANARY not in text, name
        assert not [n for n in z.namelist() if n.startswith("detail/optional/source")]

    def test_source_present_when_ticked_and_criterion_is_registered_scripts(self, client, world):
        _project(client, world["tmp"])
        _prep, z = _prepare(client, source=True)
        src = [
            n for n in z.namelist() if n.startswith("detail/optional/source/") and n.endswith(".py")
        ]
        assert len(src) == 1 and SRC_CANARY in z.read(src[0]).decode()
        index = json.loads(z.read("detail/optional/source/index.json"))
        assert index["scripts"][0]["script"] == "fig_one.py"
        assert "registered" in index["criterion"]
        # traceback 里的源码行也跟着出现（A 勾了就都有）
        assert SRC_CANARY in z.read("detail/logs/worker.log.tail").decode()

    def test_source_outside_project_is_never_collected(self, client, world, tmp_path):
        proj = _project(client, world["tmp"])
        outside = tmp_path / "outside.py"
        outside.write_text("OUTSIDE_CANARY = 1\n", encoding="utf-8")
        src = diagdetail.Sources(
            project={"figures_dir": str(proj)}, scripts=["../outside.py", "fig_one.py"]
        )
        got = dict(diagdetail.source_candidates(src))
        assert list(got) == ["fig_one.py"]

    def test_crash_reports_and_corrupt_contents_only_when_ticked(self, client, world):
        _prep, z = _prepare(client)
        blob = _blob(z)
        assert "CRASH_CANARY_BODY" not in blob and "CORRUPT_CANARY_BYTES" not in blob
        _prep, z = _prepare(client, crash_reports=True, corrupt_sessions=True)
        names = z.namelist()
        assert any(n.startswith("detail/optional/crash-reports/Tavotto") for n in names)
        assert any(n.startswith("detail/optional/corrupt-sessions/") for n in names)
        blob = _blob(z)
        assert "CRASH_CANARY_BODY" in blob and "CORRUPT_CANARY_BYTES" in blob

    def test_crash_report_selection(self, world, tmp_path):
        crash = world["crash"]
        now = time.time()
        old = crash / "Tavotto-2026-10-01-000000.ips"
        old.write_text("OLD", encoding="utf-8")
        os.utime(old, (now - 9 * 86400, now - 9 * 86400))
        (crash / "Safari-2026-10-13-000000.ips").write_text("SAFARI", encoding="utf-8")
        (crash / "python3.13-2026-10-13-000001.ips").write_text(
            "python of someone else", encoding="utf-8"
        )
        (crash / "python3.13-2026-10-13-000002.ips").write_text(
            "python /Apps/Tavotto/x", encoding="utf-8"
        )
        (crash / "tavotto-sidecar-2026-10-13-000003.crash").write_text("SIDECAR", encoding="utf-8")
        (crash / "Tavotto.exe.1234.dmp").write_bytes(b"\x00DMPCANARY")
        items = diagdetail.collect_crash_reports(
            diagdetail.Sources(),
            diagdetail.DetailOptions(crash_reports=True),
            diagdetail.Scrubber(),
        )
        body = "\n".join(i.data.decode("utf-8", "replace") for i in items)
        assert (
            "CRASH_CANARY_BODY" in body and "SIDECAR" in body and "python /Apps/Tavotto/x" in body
        )
        for absent in ("OLD", "SAFARI", "someone else", "DMPCANARY"):
            assert absent not in body, absent
        index = json.loads(next(i for i in items if i.path.endswith("index.json")).data)
        dmp = [r for r in index["reports"] if r["name"].endswith(".dmp")]
        assert dmp and dmp[0]["content"] == "not_included_binary_dump"  # 二进制转储只列清单


# ---------------------------------------------------------------------------
# 请求记录环
# ---------------------------------------------------------------------------
class TestRequestRing:
    def test_records_method_rule_status_ms_code_and_nothing_else(self, client, world):
        client.get("/api/project?secretq=CANARY_QS_91")
        client.post("/api/perf/report", data=b"CANARY_BODY_92", content_type="application/json")
        _prep, z = _prepare(client)
        doc = json.loads(z.read("detail/requests.json"))
        recs = doc["records"]
        text = z.read("detail/requests.json").decode()
        assert "CANARY_QS_91" not in text and "CANARY_BODY_92" not in text
        by_rule = {r["rule"]: r for r in recs}
        assert (
            by_rule["/api/project"]["method"] == "GET" and by_rule["/api/project"]["status"] == 200
        )
        bad = by_rule["/api/perf/report"]
        assert (
            bad["status"] == 400
            and bad["code"] == "perf_report_rejected"
            and isinstance(bad["ms"], int)
        )
        assert set(recs[0]) == {"t", "method", "rule", "status", "ms", "code"}
        # 对话框的轮询不进环
        assert not [r for r in recs if r["rule"].startswith("/api/diagnostics/send")]

    def test_ring_is_bounded(self):
        ring = diagdetail.RequestRing(capacity=5)
        for i in range(20):
            ring.record("GET", f"/r{i}", 200, 1)
        snap = ring.snapshot()
        assert len(snap) == 5 and snap[-1]["rule"] == "/r19"

    def test_code_shape_is_enforced(self):
        ring = diagdetail.RequestRing()
        ring.record("GET", "/x", 400, 1, "Bad Code With Spaces CANARY")
        ring.record("GET", "/x", 400, 1, "ok_code")
        assert [r["code"] for r in ring.snapshot()] == [None, "ok_code"]


# ---------------------------------------------------------------------------
# 渲染环境 / 网络 / 存储
# ---------------------------------------------------------------------------
class TestProbes:
    def test_render_env_real_probe(self):
        python = pool.find_worker_python()
        out = REAL_PROBE(python, pool.source_of(python) == pool.SOURCE_BUNDLED)
        assert out["status"] == "ok", out
        assert out["matplotlib"]["version"] and out["matplotlib"]["backend"]
        assert out["python"]["version"] and out["package_count"] > 0
        assert any(p["name"] == "matplotlib" for p in out["packages"])
        fams = out["matplotlib"]["fonts"]
        assert any(f.get("generic") for f in fams) and any("present" in f for f in fams)

    def test_probe_failures_are_reported_not_raised(self, monkeypatch):
        bad = REAL_PROBE("/nonexistent/python", False)
        assert bad["status"] == "failed" and bad["reason"].startswith("spawn_error")
        monkeypatch.setattr(diagdetail, "_PROBE_CODE", "import time; time.sleep(30)")
        monkeypatch.setattr(diagdetail, "PROBE_TIMEOUT_S", 1)
        t0 = time.monotonic()
        slow = REAL_PROBE(pool.find_worker_python(), False)
        assert slow["status"] == "failed" and slow["reason"] == "timeout"
        assert time.monotonic() - t0 < 8
        monkeypatch.setattr(diagdetail, "_PROBE_CODE", "import sys; sys.exit(3)")
        assert REAL_PROBE(pool.find_worker_python(), False)["reason"] == "exit:3"
        monkeypatch.setattr(diagdetail, "_PROBE_CODE", "print('not json')")
        assert REAL_PROBE(pool.find_worker_python(), False)["reason"] == "bad_output"

    def test_failed_probe_does_not_fail_the_bundle(self, client, world, monkeypatch):
        monkeypatch.setattr(
            diagdetail, "_probe_subprocess", lambda p, b: {"status": "failed", "reason": "timeout"}
        )
        _prep, z = _prepare(client)
        env = json.loads(z.read("detail/render-env.json"))
        assert env["probe"] == {"status": "failed", "reason": "timeout"}

    def test_a_collector_that_raises_degrades_to_an_error_file(self, client, world, monkeypatch):
        def boom(src, opts, scrub):
            raise RuntimeError(f"explode in {world['home']}")

        monkeypatch.setattr(
            diagdetail,
            "DEFAULT_COLLECTORS",
            [(k, p, boom if k == "requests" else f) for k, p, f in diagdetail.DEFAULT_COLLECTORS],
        )
        prep, z = _prepare(client)
        assert not prep["too_large"]
        err = json.loads(z.read("detail/requests.json"))
        assert err["collection_error"].startswith("RuntimeError: explode in ~")
        assert "detail/errors.json" in z.namelist()  # 其他类不受影响

    def test_sync_providers_and_proxy_userinfo(self):
        assert "onedrive" in diagdetail.sync_providers("C:/Users/x/OneDrive - Org/p")
        assert "icloud" in diagdetail.sync_providers(
            "/Users/x/Library/Mobile Documents/com~apple~CloudDocs/p"
        )
        assert diagdetail.sync_providers("/Users/x/Documents/p") == []
        assert (
            diagdetail._strip_userinfo("http://u:p@h.example:81/x?y=1") == "http://h.example:81/x"
        )

    def test_dns_timeout_and_error_are_facts(self, monkeypatch):
        monkeypatch.setattr(diagdetail, "_resolve", lambda h: (_ for _ in ()).throw(OSError("nx")))
        assert diagdetail.dns_fact("a.example")["error"] == "OSError"
        monkeypatch.setattr(diagdetail, "DNS_TIMEOUT_S", 0.05)
        monkeypatch.setattr(diagdetail, "_resolve", lambda h: time.sleep(0.5) or ["1.1.1.1"])
        assert diagdetail.dns_fact("b.example")["error"] == "timeout"


# ---------------------------------------------------------------------------
# 体积与截断
# ---------------------------------------------------------------------------
def _items(**sizes):
    return [
        diagdetail.Item(kind, f"{kind}.bin", os.urandom(n), shrink=lambda cap, n=n: os.urandom(cap))
        for kind, n in sizes.items()
    ]


def _build_factory(core: int):
    pad = os.urandom(core)

    def build(items, truncated):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("report.json", pad)
            for i in items:
                z.writestr(f"detail/{i.path}", i.data)
            z.writestr("manifest.json", json.dumps({"truncated": truncated}))
        return buf.getvalue()

    return build


class TestFit:
    def test_within_limit_nothing_is_cut(self):
        items = _items(errors=50_000, logs=50_000)
        data, kept, trunc = diagdetail.fit(items, _build_factory(1000), 1_000_000)
        assert trunc == [] and len(kept) == 2

    def test_truncates_lowest_priority_first_and_never_the_core(self):
        items = _items(
            errors=100_000,
            logs=100_000,
            source=400_000,
            crash_reports=400_000,
            corrupt_sessions=400_000,
        )
        build = _build_factory(50_000)
        limit = 50_000 + 100_000 + 100_000 + 300_000  # 默认项 + 一部分勾选项
        data, kept, trunc = diagdetail.fit(items, build, limit)
        assert len(data) <= limit
        order = [t["kind"] for t in trunc]
        assert order[0] == "corrupt_sessions"  # C 最先
        by = {t["kind"]: t for t in trunc}
        assert by["corrupt_sessions"]["kept_bytes"] <= by["corrupt_sessions"]["original_bytes"]
        # 默认项（errors / logs）没被动
        kept_by_kind = diagdetail._kind_bytes(kept)
        assert kept_by_kind["errors"] == 100_000 and kept_by_kind["logs"] == 100_000
        assert "errors" not in by and "logs" not in by
        # 核心文件还在
        assert zipfile.ZipFile(io.BytesIO(data)).getinfo("report.json").file_size == 50_000

    def test_order_is_c_then_b_then_a_then_defaults_from_the_tail(self):
        sizes = dict(
            errors=80_000,
            network=80_000,
            console=80_000,
            source=80_000,
            crash_reports=80_000,
            corrupt_sessions=80_000,
        )
        build = _build_factory(10_000)
        seen: list[str] = []
        for limit in (420_000, 330_000, 240_000, 150_000, 70_000):
            items = _items(**sizes)
            _data, kept, trunc = diagdetail.fit(items, build, limit)
            seen.append(",".join(t["kind"] for t in trunc))
        # 随着限额收紧，被动到的类别按 c, b, a, network, console … 逐步增加，errors 最后
        flat = [k for s in seen for k in s.split(",") if k]
        first_hit = {}
        for k in flat:
            first_hit.setdefault(k, len(first_hit))
        assert list(first_hit) == [
            "corrupt_sessions",
            "crash_reports",
            "source",
            "network",
            "console",
            "errors",
        ]

    def test_real_ten_mib_cap_with_incompressible_content(self, monkeypatch):
        for k in diagdetail.SHARE:
            monkeypatch.setitem(diagdetail.SHARE, k, 6 * 1024 * 1024)
        items = _items(errors=3_000_000, logs=3_000_000, source=3_000_000, crash_reports=3_000_000)
        limit = diagsend.MAX_BYTES - diagdetail.FIT_SLACK
        data, kept, trunc = diagdetail.fit(items, _build_factory(200_000), limit)
        assert len(data) <= diagsend.MAX_BYTES
        assert [t["kind"] for t in trunc][0] == "crash_reports"

    def test_end_to_end_manifest_and_prepare_report_truncation(self, client, world, monkeypatch):
        # 把上限压到 60 KiB 左右：核心包约 10 KiB，日志与源码是随机内容
        _project(client, world["tmp"])
        (world["tmp"] / "proj" / "big.py").write_bytes(b"# " + os.urandom(200_000).hex().encode())
        monkeypatch.setattr(diagsend, "MAX_BYTES", 120 * 1024)
        big = world["tmp"] / "big.log"
        big.write_text(
            "2026-10-13 10:00:00,000 INFO tavotto: " + os.urandom(400_000).hex(), encoding="utf-8"
        )
        monkeypatch.setattr(diagnostics, "_app_log_path", lambda: big)
        prep, z = _prepare(client, source=True, crash_reports=True)
        assert not prep["too_large"] and prep["size"] <= 120 * 1024
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["truncated"], "超限必须在 manifest 里留下记录"
        for t in manifest["truncated"]:
            assert {"kind", "original_bytes", "kept_bytes"} <= set(t)
            assert t["kept_bytes"] < t["original_bytes"]
        assert prep["truncated"] == manifest["truncated"]
        # 核心文件完整
        assert json.loads(z.read("report.json"))["tavotto"]["version"]
        assert "app.log" in z.namelist() and "README.txt" in z.namelist()

    def test_core_alone_too_large_still_goes_through_too_large(self, client, world, monkeypatch):
        monkeypatch.setattr(diagsend, "MAX_BYTES", 2000)
        r = client.post("/api/diagnostics/send/prepare", json=PAYLOAD)
        assert r.get_json()["too_large"] is True and r.get_json()["id"] is None


# ---------------------------------------------------------------------------
# 导出路径不变
# ---------------------------------------------------------------------------
class TestExportPathUnchanged:
    OLD_NAMES = {"report.json", "app.log", "config.json", "manifest.json", "README.txt"}

    def _export(self, client, method="post", body=None):
        r = (
            client.post("/api/diagnostics/bundle", json=body or {})
            if method == "post"
            else client.get("/api/diagnostics/bundle")
        )
        return zipfile.ZipFile(io.BytesIO(r.data))

    def test_export_has_no_detail_and_ignores_detail_payload(self, client, world):
        body = {
            **PAYLOAD,
            "detail_options": {"source": True, "crash_reports": True, "corrupt_sessions": True},
        }
        _project(client, world["tmp"])
        for z in (self._export(client, "get"), self._export(client, "post", body)):
            names = set(z.namelist())
            assert not [n for n in names if n.startswith("detail")]
            assert names <= self.OLD_NAMES | {"task-diagnostics.json"}
            manifest = json.loads(z.read("manifest.json"))
            assert manifest["contains_detail"] is False and manifest["detail_files"] == []
            assert manifest["truncated"] == []
            assert manifest["detail_options"] == {
                "source": False,
                "crash_reports": False,
                "corrupt_sessions": False,
            }
            blob = _blob(z)
            for canary in (SRC_CANARY, "My Paper Title", "console failure", "CRASH_CANARY_BODY"):
                assert canary not in blob, canary

    def test_export_logs_are_still_the_strict_export_version(self, client, world):
        z = self._export(client, "get")
        # 完整日志里的报错原文 / 栈不会出现在导出包的 app.log 里（出门版只有模板 + 换形参数）
        assert "RuntimeError: render failed" not in z.read("app.log").decode()
        assert "CANARY_BEARER_TOKEN_abc123" not in _blob(z)

    def test_export_readme_keeps_its_promises_but_send_readme_does_not(self, client, world):
        export_readme = self._export(client, "get").read("README.txt").decode()
        assert "不包含 / It does NOT intentionally contain:" in export_readme
        _prep, z = _prepare(client)
        send_readme = z.read("README.txt").decode()
        assert "不包含 / It does NOT intentionally contain:" not in send_readme
        assert "detail/" in send_readme and 'Without "script source" ticked' in send_readme

    def test_report_json_is_identical_between_export_and_send(self, client, world):
        a = self._export(client, "get").read("report.json")
        _prep, z = _prepare(client)
        b = json.loads(z.read("report.json"))
        a = json.loads(a)
        # render 段含进程池的实时会话状态、recent_errors 随日志增长：同一进程里两次采集之间可能变，
        # 只对拍键集；其余段逐字节相同
        volatile = {"render", "recent_errors"}
        assert list(a) == list(b) and list(a["render"]) == list(b["render"])
        assert {k: (a[k], b[k]) for k in a if k not in volatile and a[k] != b[k]} == {}


# ---------------------------------------------------------------------------
# 前端载荷（第二道校验）
# ---------------------------------------------------------------------------
class TestSanitizeDetail:
    def test_non_dict_and_unknown_keys(self):
        assert dfe.sanitize_detail(None) == {}
        assert dfe.sanitize_detail([1]) == {}
        out = dfe.sanitize_detail({"console": "x", "evil": {"a": 1}})
        assert out["console"] == [] and "evil" not in out

    def test_bounds(self):
        big = [
            {"t": i, "level": "error", "message": "m" * 99999, "stack": "s" * 99999}
            for i in range(200)
        ]
        out = dfe.sanitize_detail({"console": big, "errors": big})
        assert (
            len(out["console"]) == dfe.DETAIL_MAX_CONSOLE
            and len(out["errors"]) == dfe.DETAIL_MAX_ERRORS
        )
        assert len(out["console"][0]["message"]) == dfe.DETAIL_MAX_MESSAGE
        assert len(out["console"][0]["stack"]) == dfe.DETAIL_MAX_STACK
        panels = [
            {"panel": "p", "overrides": [{"gid": "g", "value": list(range(99999))}] * 999}
        ] * 9
        ov = dfe.sanitize_detail({"overrides": panels})["overrides"]
        assert len(ov) == dfe.DETAIL_MAX_OVERRIDE_PANELS
        assert len(ov[0]["overrides"]) <= dfe.DETAIL_MAX_OVERRIDES

    def test_types_are_enforced(self):
        out = dfe.sanitize_detail(
            {
                "display": {
                    "device_pixel_ratio": "2",
                    "window_width": float("nan"),
                    "language": 5,
                    "theme": "dark",
                },
                "shell": {"desktop": "yes", "app_version": "1"},
                "console": [{"t": "x", "level": "weird", "message": 5}, "junk"],
            }
        )
        assert out["display"] == {"theme": "dark"}
        assert out["shell"] == {"app_version": "1"}
        assert out["console"][0]["level"] == "error" and out["console"][0]["message"] == ""
        assert len(out["console"]) == 1
