"""「发送问题反馈」的 `detail-…` / `optional-…`（bundle schema 8，ADR 0118 修订 2026-10-13）。

判据的主语：**发送包里每个文件的全部字节**（不是某一个字段）——家目录 / 用户名 / 秘密 / 脚本源码这些金丝雀
在哪个文件里都必须是 0；每条「0」都带一条反证（金丝雀确实进了原始输入、而且处理后的标记出现了），
免得因为什么都没读到而「搜不到」。导出到文件的老路径（`GET/POST /api/diagnostics/bundle`）不含任何 `detail-…` / `optional-…` 条目、
不读发送载荷里的 `detail`。
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

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
    """包里每个文件的文本；内层 zip 展开成 `外层.zip!内层路径`——金丝雀要在内层里也搜。"""
    out: dict[str, str] = {}
    for n in z.namelist():
        raw = z.read(n)
        if n.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(raw)) as inner:
                for m in inner.namelist():
                    out[f"{n}!{m}"] = inner.read(m).decode("utf-8", "replace")
        else:
            out[n] = raw.decode("utf-8", "replace")
    return out


def _inner(z: zipfile.ZipFile, name: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(z.read(name)))


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
        # 勾选项走查询串：载荷被整份丢弃时它仍在，勾着的框不会悄悄变成没勾
        r = client.post(
            "/api/diagnostics/send/prepare?options=source,crash_reports,bogus", json=body
        )
        got = r.get_json()["options"]
        assert got == {"source": True, "crash_reports": True, "corrupt_sessions": False}
        assert (
            diagdetail.options_from_query(None) == {} and diagdetail.options_from_query("x,y") == {}
        )


# ---------------------------------------------------------------------------
# 默认收：每一类都在、有内容
# ---------------------------------------------------------------------------
EXPECTED_DEFAULT = [
    "detail-errors.json",
    "detail-app-log.txt",
    "detail-sidecar-log.txt",
    "detail-worker-log.txt",
    "detail-state.zip",
    "detail-requests.json",
    "detail-overrides.zip",
    "detail-render-env.json",
    "detail-runs.json",
    "detail-shell.json",
    "detail-storage.json",
    "detail-frontend-console.json",
    "detail-display.json",
    "detail-network.json",
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
        assert {"index.json", "export-defaults.json", "layout-sessions.json"} <= set(
            _inner(z, "detail-state.zip").namelist()
        )
        # 勾选项没勾：没有 optional 内容
        assert not [n for n in names if n.startswith("optional-")]
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["schema_version"] == diagnostics.BUNDLE_SCHEMA_VERSION == 8
        assert manifest["contains_detail"] is True
        assert manifest["truncated"] == []
        listed = {f["path"]: f["bytes"] for f in manifest["detail_files"]}
        assert listed == {n: z.getinfo(n).file_size for n in names if n.startswith("detail-")}
        assert len(z.read("manifest.json")) < 64 * 1024
        # prepare 响应带逐项清单与类别，且类别都在 ENTRY_KINDS 里
        kinds = {e["name"]: e["kind"] for e in prep["entries"]}
        assert kinds["detail-errors.json"] == "detail_errors"
        assert kinds["detail-worker-log.txt"] == "detail_logs"
        assert set(kinds.values()) <= set(diagsend.ENTRY_KINDS.values()) | {"other"}
        sizes = {e["name"]: e["bytes"] for e in prep["entries"]}
        assert sizes["detail-errors.json"] == z.getinfo("detail-errors.json").file_size

    def test_error_originals_and_full_stacks(self, client, world):
        _prep, z = _prepare(client)
        errors = json.loads(z.read("detail-errors.json"))
        engine = " ".join(e["text"] for e in errors["engine"])
        assert "引擎渲染失败: fig_one" in engine and "RuntimeError: render failed" in engine
        assert 'File "~/proj/fig_one.py", line 7, in main' in engine  # 完整调用栈，家目录已写成 ~
        assert (
            errors["worker"]
            and "RuntimeError: render failed" in errors["worker"][0]["tracebacks"][0]
        )
        assert errors["frontend"][0]["message"] == "nope"
        console = json.loads(z.read("detail-frontend-console.json"))
        assert console["scope"].startswith("global")
        assert console["records"][0]["stack"].startswith("Error: x")

    def test_state_manifest_lists_corrupt_copy_but_not_its_content(self, client, world):
        _prep, z = _prepare(client)
        index = json.loads(_inner(z, "detail-state.zip").read("index.json"))
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
        display = json.loads(z.read("detail-display.json"))
        assert display["device_pixel_ratio"] == 2 and display["theme"] == "dark"
        shell = json.loads(z.read("detail-shell.json"))
        assert shell["frontend_shell"]["tauri_version"] == "2.0"
        assert shell["in_app_update"]["status"] == "unknown"  # 取不到如实写 unknown
        assert shell["channel"]["basis"].startswith("inferred")
        assert "rosetta" in shell["cpu"] and "arch" in shell["cpu"]
        net = json.loads(z.read("detail-network.json"))
        assert net["proxy"]["configured"] is True
        assert net["proxy"]["env"]["HTTPS_PROXY"] == "http://proxy.example:8080"  # userinfo 没了
        assert all(d["tun_rewritten_198_18"] is True for d in net["dns"])
        assert "utc_offset_minutes" in net["timezone"]
        storage = json.loads(z.read("detail-storage.json"))
        assert storage["data_dir"]["disk"]["free_bytes"] > 0
        assert storage["project_dir"]["writable"] is True
        long_paths = storage["windows_long_paths_enabled"]
        if os.name == "nt":  # Windows：真实注册表值（0 / 1），读不到时是 "unreadable:…" 或 None
            assert long_paths in (0, 1, None) or str(long_paths).startswith("unreadable:")
        else:
            assert long_paths is None

    def test_overrides_live_and_baked(self, client, world):
        _project(client, world["tmp"])
        _prep, z = _prepare(client)
        inner = _inner(z, "detail-overrides.zip")
        live = [n for n in inner.namelist() if n.startswith("live-")]
        assert live
        doc = json.loads(inner.read(live[0]))
        assert doc["overrides"][0]["value"] == "My Paper Title"  # 图里的文字保留

    def test_runs_carry_task_snapshots_and_engine_log_events(self, client, world):
        _prep, z = _prepare(client)
        runs = json.loads(z.read("detail-runs.json"))
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


GROUP_TB = (
    "  + Exception Group Traceback (most recent call last):\n"
    '  |   File "/p/fig.py", line 18, in <module>\n'
    "  |     h()  # GRP_SRC_1\n"
    "  |     ~^^\n"
    '  |   File "/p/fig.py", line 10, in g\n'
    '  |     raise ExceptionGroup("grp", [e])  # GRP_SRC_2\n'
    "  | ExceptionGroup: grp (2 sub-exceptions)\n"
    "  | NOTE_USER_TEXT\n"
    "  +-+---------------- 1 ----------------\n"
    "    | Traceback (most recent call last):\n"
    '    |   File "/p/fig.py", line 8, in g\n'
    "    |     f(secret_local)  # GRP_SRC_3\n"
    "    |     ~^^^^^^^^^^^^^\n"
    "    |   ...\n"
    "    | ValueError: 1\n"
    "    +---------------- 2 ----------------\n"
    "    | ExceptionGroup: inner (1 sub-exception)\n"
    "    +-+---------------- 1 ----------------\n"
    "      | Traceback (most recent call last):\n"
    '      |   File "/p/fig.py", line 3, in z\n'
    "      |     raise KeyError(x)  # GRP_SRC_4\n"
    "      | KeyError: 2\n"
    "      +------------------------------------\n"
    "\n"
    "The above exception was the direct cause of the following exception:\n"
    "\n"
    "Traceback (most recent call last):\n"
    '  File "/p/fig.py", line 25, in <module>\n'
    "    chained()  # GRP_SRC_5\n"
    "    x = 1  # a local: GRP_SRC_6\n"
    "RuntimeError: chained\n"
    "NOTE_AFTER_EXC\n"
)
GROUP_CANARIES = [f"GRP_SRC_{i}" for i in range(1, 7)] + ["secret_local"]


class TestExceptionGroupTracebacks:
    def test_whitelist_keeps_frames_and_exception_lines_and_decoration(self):
        out = diagdetail.strip_source_lines(GROUP_TB)
        for c in GROUP_CANARIES:
            assert c not in out, c
        for keep in (
            "  + Exception Group Traceback (most recent call last):",
            '  |   File "/p/fig.py", line 18, in <module>',
            "  | ExceptionGroup: grp (2 sub-exceptions)",
            "  | NOTE_USER_TEXT",  # add_note 的 note 不是源码：保留
            "  +-+---------------- 1 ----------------",
            "    |   ...",
            "    | ValueError: 1",
            "      | KeyError: 2",
            "      +------------------------------------",
            "The above exception was the direct cause of the following exception:",
            "RuntimeError: chained",
            "NOTE_AFTER_EXC",
        ):
            assert keep in out, keep
        assert "~^^" not in out

    def test_plain_and_chained_tracebacks_still_work(self):
        tb = (
            "Traceback (most recent call last):\n"
            '  File "/p/a.py", line 3, in <module>\n'
            "    boom()  # PLAIN_SRC\n"
            "    ^^^^^^\n"
            "ValueError: x\n"
            "\n"
            "During handling of the above exception, another exception occurred:\n"
            "\n"
            "Traceback (most recent call last):\n"
            '  File "/p/a.py", line 5, in <module>\n'
            "    other()  # PLAIN_SRC2\n"
            "KeyError: y\n"
            "normal log line afterwards\n"
            "    indented log continuation stays\n"
        )
        out = diagdetail.strip_source_lines(tb)
        assert "PLAIN_SRC" not in out
        assert "ValueError: x" in out and "KeyError: y" in out
        assert "    indented log continuation stays" in out  # 块外（收尾异常行之后）的缩进正文不动

    @pytest.mark.skipif(sys.version_info < (3, 11), reason="ExceptionGroup 需要 3.11+")
    def test_a_real_exception_group_traceback(self, tmp_path):
        import subprocess

        script = tmp_path / "eg.py"
        script.write_text(
            "def f():\n    raise ValueError(REAL_SRC_A := 1)\n"
            "try:\n    f()\nexcept Exception as e:\n"
            "    raise ExceptionGroup('g', [e, ExceptionGroup('i', [KeyError(REAL_SRC_B := 2)])])\n",
            encoding="utf-8",
        )
        err = subprocess.run([sys.executable, str(script)], capture_output=True, text=True).stderr
        assert "REAL_SRC_A" in err  # 反证：原始输出里确实有源码行
        out = diagdetail.strip_source_lines(err)
        assert "REAL_SRC_A" not in out and "REAL_SRC_B" not in out
        assert "ExceptionGroup: g" in out and "ValueError: 1" in out

    def test_unticked_has_zero_source_canaries_in_errors_and_every_log_tail(
        self, client, world, monkeypatch
    ):
        tmp = world["tmp"]
        app_log = tmp / "grp_app.log"
        app_log.write_text(
            "2026-10-13 10:00:00,000 ERROR tavotto: render failed\n" + GROUP_TB, encoding="utf-8"
        )
        monkeypatch.setattr(diagnostics, "_app_log_path", lambda: app_log)
        (tmp / "sidecar.log").write_text(GROUP_TB, encoding="utf-8")
        (tmp / "worker.log").write_text(GROUP_TB, encoding="utf-8")
        _prep, z = _prepare(client)
        for name, text in _texts(z).items():
            for c in GROUP_CANARIES:
                assert c not in text, (name, c)
        assert "NOTE_USER_TEXT" in _blob(z)  # 反证：日志确实进了包
        _prep, z = _prepare(client, source=True)  # 勾了源码：不剥
        assert "GRP_SRC_1" in z.read("detail-worker-log.txt").decode()
        assert "GRP_SRC_1" in z.read("detail-app-log.txt").decode()


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
        assert "optional-source.zip" not in z.namelist()

    def test_source_present_when_ticked_and_criterion_is_registered_scripts(self, client, world):
        _project(client, world["tmp"])
        _prep, z = _prepare(client, source=True)
        inner = _inner(z, "optional-source.zip")
        src = [n for n in inner.namelist() if n.endswith(".py")]
        assert len(src) == 1 and SRC_CANARY in inner.read(src[0]).decode()
        index = json.loads(inner.read("index.json"))
        assert index["scripts"][0]["script"] == "fig_one.py"
        assert "registered" in index["criterion"]
        # traceback 里的源码行也跟着出现（A 勾了就都有）
        assert SRC_CANARY in z.read("detail-worker-log.txt").decode()

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
        z.namelist()
        assert any(
            n.startswith("Tavotto") for n in _inner(z, "optional-crash-reports.zip").namelist()
        )
        assert any(
            n.startswith("layout-sessions.corrupt")
            for n in _inner(z, "optional-corrupt-sessions.zip").namelist()
        )
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
        (crash / "Tavotto.exe.1234.dmp").write_bytes(b"\x00DMPCANARY\x01")
        (crash / "python.exe.77.dmp").write_bytes(b"x" + "C:\\Tavotto\\x".encode("utf-16-le"))
        (crash / "python.exe.78.dmp").write_bytes(b"unrelated python dump DMP_OTHER")
        (crash / "chrome.exe.9.dmp").write_bytes(b"DMP_CHROME")
        items = diagdetail.collect_crash_reports(
            diagdetail.Sources(),
            diagdetail.DetailOptions(crash_reports=True),
            diagdetail.Scrubber(),
        )
        body = b"\n".join(i.data for i in items).decode("utf-8", "replace")
        assert (
            "CRASH_CANARY_BODY" in body and "SIDECAR" in body and "python /Apps/Tavotto/x" in body
        )
        assert "DMPCANARY" in body  # .dmp 原样收（无法脱敏，确认框与隐私声明已写明）
        assert b"x" + "C:\\Tavotto\\x".encode("utf-16-le") in [
            i.data for i in items
        ]  # 点名 Tavotto 的 python 转储
        for absent in ("OLD", "SAFARI", "someone else", "DMP_OTHER", "DMP_CHROME"):
            assert absent not in body, absent
        index = json.loads(next(i for i in items if i.path.endswith("index.json")).data)
        dmp = {r["name"]: r for r in index["reports"] if r["name"].endswith(".dmp")}
        assert dmp["Tavotto.exe.1234.dmp"]["content"] == "raw_binary"

    def test_dumps_are_taken_whole_newest_first_and_the_rest_is_recorded(self, world, monkeypatch):
        crash = world["crash"]
        now = time.time()
        for i, age in enumerate((10, 20, 30)):  # 越小越新
            f = crash / f"Tavotto.exe.{i}.dmp"
            f.write_bytes(bytes([65 + i]) * 400)
            os.utime(f, (now - age, now - age))
        monkeypatch.setitem(diagdetail.SHARE, "crash_reports", 900)  # 放得下两份整的，放不下第三份
        items = diagdetail.collect_crash_reports(
            diagdetail.Sources(),
            diagdetail.DetailOptions(crash_reports=True),
            diagdetail.Scrubber(),
        )
        dumps = {i.path: i.data for i in items if i.path.endswith(".dmp")}
        assert set(dumps) == {"Tavotto.exe.0.dmp", "Tavotto.exe.1.dmp"}  # 新的优先，整份，不截半个
        assert all(len(d) == 400 for d in dumps.values())
        index = items[0]
        assert index.dropped_bytes == 400
        rows = {r["name"]: r["content"] for r in json.loads(index.data)["reports"]}
        assert rows["Tavotto.exe.2.dmp"] == "skipped_over_budget"
        _data, _kept, trunc = diagdetail.fit(
            items, lambda its, t: b"x" * 10 + json.dumps(t).encode(), 10**9
        )
        crash_row = next(t for t in trunc if t["kind"] == "crash_reports")
        assert crash_row["original_bytes"] > crash_row["kept_bytes"]

    def test_dumps_are_zero_bytes_when_unticked_and_present_when_ticked(self, client, world):
        (world["crash"] / "Tavotto.exe.1.dmp").write_bytes(b"\x00DMP_BIN_CANARY_5\x00")
        _prep, z = _prepare(client)
        assert "optional-crash-reports.zip" not in z.namelist()
        assert b"DMP_BIN_CANARY_5" not in b"".join(z.read(n) for n in z.namelist())
        _prep, z = _prepare(client, crash_reports=True)
        inner = _inner(z, "optional-crash-reports.zip")
        assert inner.read("Tavotto.exe.1.dmp") == b"\x00DMP_BIN_CANARY_5\x00"
        assert (
            diagsend.zip_problem(client.get(f"/api/diagnostics/send/{_prep['id']}/bundle").data)
            is None
        )


# ---------------------------------------------------------------------------
# 源码脱敏（注释 / 字符串里的明文）、渲染环境的项目感知、整份丢弃的记账
# ---------------------------------------------------------------------------
SECRET = "CNRYSECRET9x7q"
MATRIX_KEYS = [
    "token", "api_key", "apiKey", "API-KEY", "auth", "Authorization", "x-api-key", "X-Api-Key",
    "password", "passwd", "pwd", "passphrase", "passcode", "secret", "clientSecret", "session_id",
    "sessionId", "Session-Token", "nonce", "access_token", "refreshToken", "private_key",
    "accessKey", "cookie", "signature", "credentials", "bearer", "authToken", "api-token",
    "TOKEN", "Password", "AUTH_TOKEN", "secret_key", "key",
]  # fmt: skip
MATRIX_VALUES = {
    "bare": SECRET,
    "sq": f"'{SECRET}'",
    "dq": f'"{SECRET}"',
    "num": "123456789012",
    "list": f'["{SECRET}"]',
    "dict": f'{{"raw":"{SECRET}"}}',
    "nested": f'{{"a":[{{"b":"{SECRET}"}}]}}',
    "bearer": f"Bearer {SECRET}",
    "basic": f"Basic {SECRET}",
}
MATRIX_STANDALONE = {
    "bearer-line": f"Authorization: Bearer {SECRET}",
    "cookie-line": f"Cookie: sid={SECRET}; b=2",
    "set-cookie": f"Set-Cookie: sid={SECRET}",
    "url-userinfo": f"https://u:{SECRET}@h.example/p",
    "url-userinfo-only-user": f"postgres://{SECRET}@h",
    "query": f"https://h/p?access_token={SECRET}&x=1",
    "query-later": f"https://h/p?x=1&api_key={SECRET}",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abcdefghijk" + SECRET,
    "long-hex": "deadbeefcafe0123456789abcdef0123456789ab" + "0" * 24,
    "proxy-env": f"HTTP_PROXY=http://user:{SECRET}@proxy:8080",
    "x-tavotto-auth": f"X-Tavotto-Auth: {SECRET}",
    "pem": f"-----BEGIN RSA PRIVATE KEY-----\n{SECRET}\n-----END RSA PRIVATE KEY-----",
    "aws-id": "AKIAABCDEFGHIJKLMNOP",
    "github-pat": "github_pat_11ABCDEFG0abcdefghijkl_mnopqrstuvwxyz",
    "slack": "xoxb-1234567890-abcdefghijk",
    "google-key": "AIzaSyA1234567890123456789012345678901",
}  # fmt: skip
CANARIES = (
    SECRET,
    "123456789012",
    "deadbeefcafe0123",
    "AKIAABCDEFGHIJKLMNOP",
    "github_pat_11ABCDEFG",
    "xoxb-1234567890",
    "AIzaSyA12345",
    "MIIabc",
)


def _json_form(v: str):
    return v if v[:1] in '"[{' else json.dumps(v)


def _leaks(out: str) -> bool:
    return any(c in out for c in CANARIES)


class TestSecretShapeMatrix:
    """键形状 × 值形状 × 载体（日志文本 / JSON 结构 / 源码注释·文档字符串·代码），金丝雀在输出里 0 命中。"""

    @pytest.mark.parametrize("key", MATRIX_KEYS)
    def test_key_value_cells(self, key, world):
        full, code = diagdetail.Scrubber(), diagdetail.Scrubber(source_mode=True)
        leaks = []
        for vn, v in MATRIX_VALUES.items():
            jv = _json_form(v)
            try:
                obj = json.loads(jv)
            except ValueError:
                obj = v
            cells = {
                "text=": full.text(f"{key}={v} tail"),
                "text:": full.text(f"{key}: {v} tail"),
                "json-in-log": full.text('{"%s": %s, "x": 1}' % (key, jv)),
                "pyrepr-in-log": full.text(
                    "{'%s': %s}" % (key, v if v[:1] in "'[{\"" else f"'{v}'")
                ),
                "obj": json.dumps(
                    full.obj({key: obj, "n": {"inner": {key: obj}}}), ensure_ascii=False
                ),
                "src-comment": diagdetail.scrub_script(f"# {key}={v}\nx=1\n", "a.py", full, code),
                "src-docstring": diagdetail.scrub_script(f'"""{key}: {v}"""\n', "a.py", full, code),
            }
            if vn in ("sq", "dq"):
                cells["src-code"] = diagdetail.scrub_script(f"{key} = {v}\n", "a.py", full, code)
            leaks += [(vn, c) for c, out in cells.items() if _leaks(out)]
        assert not leaks, (key, leaks)

    @pytest.mark.parametrize("name", sorted(MATRIX_STANDALONE))
    def test_standalone_shapes(self, name, world):
        t = MATRIX_STANDALONE[name]
        full, code = diagdetail.Scrubber(), diagdetail.Scrubber(source_mode=True)
        for carrier, out in {
            "text": full.text(t),
            "obj": json.dumps(full.obj({"msg": t, "nested": [{"m": t}]})),
            "src-comment": diagdetail.scrub_script(f"# {t}\n", "a.py", full, code),
        }.items():
            assert not _leaks(out), (name, carrier, out)

    PY_NAME_KEYS = [k for k in MATRIX_KEYS if k.isidentifier()]
    PY_FORMS = [
        '{k} = "VAL"', "{k} = b\"VAL\"", "{k} = r'VAL'", '{k} = u"VAL"', '{k} = rb"VAL"', '{k} = f"VAL{{x}}"',
        '{k}: str = "VAL"', '{k} += "VAL"', 'x = {k} = "VAL"', 'self.{k} = "VAL"', 'obj.a.{k} = "VAL"',
        'config["{k}"] = "VAL"', "config['{k}'] = 'VAL'", 'f({k}="VAL")', 'f(a=1, {k}="VAL", b=2)',
        '{{"{k}": "VAL"}}', "{{'{k}': b'VAL', 'n': 1}}", '{k} = ("VAL" "T")', '{k} = """VAL"""',
        'os.environ["{k}"] = "VAL"', 'os.environ.setdefault("{k}", "VAL")', 't = ("{k}", "VAL")',
        'set_{k}("VAL")', 'client.set_{k}("VAL", "x")', '{k}("user", "VAL")', 'obj.{k}.update("VAL")',
        'x = foo(set_{k}("VAL"), "keep")',
    ]  # fmt: skip
    PY_STRING_KEY_FORMS = [
        'config["{k}"] = "VAL"', '{{"{k}": "VAL"}}', 'os.environ.setdefault("{k}", "VAL")', 't = ("{k}", "VAL")',
    ]  # fmt: skip

    @pytest.mark.parametrize("key", MATRIX_KEYS)
    def test_python_literal_forms_in_the_code_region(self, key, world):
        """代码区（不是注释 / 文档字符串）里的 Python 字面量：前缀 b/r/u/f/rb、类型注解、下标目标、关键字参数、
        dict 值、`setdefault("k", v)` 都按 tokenize 的结构判定，不靠正则。"""
        full, code = diagdetail.Scrubber(), diagdetail.Scrubber(source_mode=True)
        forms = self.PY_FORMS if key.isidentifier() else self.PY_STRING_KEY_FORMS
        for form in forms:
            line = (
                form.replace("{{", "{")
                .replace("}}", "}")
                .replace("{k}", key)
                .replace("VAL", SECRET)
            )
            out = diagdetail.scrub_script(f"import os\n{line}\n", "a.py", full, code)
            assert SECRET not in out, (key, form, out)

    def test_non_secret_code_and_expressions_are_untouched(self, world):
        full, code = diagdetail.Scrubber(), diagdetail.Scrubber(source_mode=True)
        src = (
            "import os\n"
            "token = self._next()\n"
            'api_key = os.environ["K"]\n'
            'name = "keep-me"\n'
            'f(label="keep-me-too", n=3)\n'
            'D = {"title": "keep-title"}\n'
            "password = None\n"
        )
        assert diagdetail.scrub_script(src, "a.py", full, code) == src

    def test_final_zip_has_no_python_literal_secret(self, client, world):
        proj = _project(client, world["tmp"])
        lines = [
            f.replace("{{", "{").replace("}}", "}").replace("{k}", k).replace("VAL", SECRET)
            for k in self.PY_NAME_KEYS
            for f in self.PY_FORMS
        ]
        (proj / "fig_one.py").write_text(
            "import os\n" + "\n".join(lines) + "\ndef main():\n    return None\n", encoding="utf-8"
        )
        _prep, z = _prepare(client, source=True)
        inner = _inner(z, "optional-source.zip")
        body = "\n".join(inner.read(n).decode("utf-8", "replace") for n in inner.namelist())
        assert "def main" in body and SECRET not in body
        for name, text in _texts(z).items():
            assert SECRET not in text, name

    @pytest.mark.parametrize(
        "text",
        [
            "a -----BEGIN PRIVATE KEY-----\n"
            + "REALPEM" * 5000
            + "\n-----END PRIVATE KEY-----\nSAFE_TAIL",
            "a -----BEGIN RSA PRIVATE KEY-----\n" + "REALPEM" * 5000 + "\nSAFE_TAIL_MISSING_END",
            "-----BEGIN PRIVATE KEY-----\nREALPEM1\n-----END PRIVATE KEY-----\nmid SAFE_MID\n"
            "-----BEGIN PRIVATE KEY-----\nREALPEM2\n-----END PRIVATE KEY-----\nSAFE_TAIL",
        ],
    )
    def test_pem_blocks_of_any_length_and_without_end(self, text):
        out = diagdetail.Scrubber().text(text)
        assert "REALPEM" not in out
        if "MISSING_END" in text:  # 没有 END：从 BEGIN 抹到输入末尾
            assert "SAFE_TAIL_MISSING_END" not in out and out == "a <redacted>"
        else:  # 有 END：块外的正常文本保留
            assert "SAFE_TAIL" in out
        if "SAFE_MID" in text:
            assert "SAFE_MID" in out

    def test_calls_to_secret_named_callables_redact_every_direct_string_argument(self):
        full, code = diagdetail.Scrubber(), diagdetail.Scrubber(source_mode=True)
        out = diagdetail.scrub_script(
            'login(token="R1", name="KEEP_NAME")\nprint("KEEP_PRINT")\nf(g("KEEP_INNER"), h=["KEEP_LIST"])\n',
            "a.py",
            full,
            code,
        )
        assert "R1" not in out
        for keep in ("KEEP_NAME", "KEEP_PRINT", "KEEP_INNER", "KEEP_LIST"):
            assert keep in out

    @pytest.mark.parametrize(
        ("text", "keep"),
        [
            ('password="abc def REAL_SECRET\nnext', "next"),  # 引号没闭合：抹到行尾
            ("secret='abc def REAL_SECRET\nnext", "next"),
            ('{"password": \\"REAL_SECRET unterminated\nnext', "next"),
            ('Authorization: Digest username="u", response="REAL_SECRET"\nnext', "next"),
            ("Proxy-Authorization: Basic REAL_SECRET==\nnext", "next"),
            ("https://u:" + "p" * 500 + "REAL_SECRET@h/x tail", "tail"),
        ],
    )
    def test_uncertain_boundaries_erase_up_to_a_boundary_we_can_be_sure_of(self, text, keep):
        out = diagdetail.Scrubber().text(text)
        assert "REAL_SECRET" not in out and keep in out

    REPR_VALUES = [
        "b'VAL'", 'rb"VAL"', "u'VAL'", "f'VAL'", "BR'VAL'", "SecretStr('VAL')", "bytearray(b'VAL')",
        "Token(value='VAL')", "mod.sub.Secret(\"VAL\", n=(1, 2))", "<SecretStr 'VAL'>",
        "{'a': b'VAL'}", "[b'VAL', 2]", "Decimal('VAL')",
    ]  # fmt: skip

    @pytest.mark.parametrize("key", MATRIX_KEYS)
    def test_python_repr_values_in_logs(self, key, world):
        """日志 / console 里的 Python repr：字符串前缀（b / rb / u / f）、包装调用（SecretStr('…')、bytearray(b'…')）、
        `<Name '…'>` 形状的 repr、嵌套 dict 里的 b''——整个值换掉，不剩引号串。"""
        full = diagdetail.Scrubber()
        for v in self.REPR_VALUES:
            v = v.replace("VAL", SECRET)
            for line in (
                "{'%s': %s, 'n': 1}" % (key, v),
                "%s=%s tail" % (key, v),
                '{"%s": %s}' % (key, v),
                "%s: %s tail" % (key, v),
            ):
                out = full.text(line)
                assert SECRET not in out, (key, v, line, out)
                assert not out.endswith("'REAL"), out
        # 括号段 / 引号串之外的内容保留
        assert full.text("{'%s': SecretStr('%s'), 'n': 1} tail" % (key, SECRET)).endswith(
            ", 'n': 1} tail"
        )

    def test_prefixed_quote_is_replaced_whole_and_calls_in_code_are_left_alone(self):
        full, code = diagdetail.Scrubber(), diagdetail.Scrubber(source_mode=True)
        assert full.text("api_key=b'S1 x'").startswith("api_key='<redacted>'")
        assert "S1" not in full.text("{'api_key': b'S1'}") and "'S1'" not in full.text(
            "{'api_key': b'S1'}"
        )
        assert (
            code.text('token = getpass.getpass("Password: ")')
            == 'token = getpass.getpass("Password: ")'
        )

    def test_innocent_neighbours_survive(self, world):
        full = diagdetail.Scrubber()
        assert (
            full.text("the author wrote about authority: fine")
            == "the author wrote about authority: fine"
        )
        out = full.obj({"author": "A. Person", "authority_variant": "var:abc", "n": 4})
        assert out == {"author": "A. Person", "authority_variant": "var:abc", "n": 4}
        assert full.obj({"token": [1, 2, 3]}) == {"token": [1, 2, 3]}

    def test_final_zip_bytes_have_no_canary_in_any_carrier(self, client, world, monkeypatch):
        """同一批「键 × 值」格子塞进所有载体：日志文本、console 记录、overrides、会话状态 JSON、崩溃报告、源码；
        在最终 ZIP 的全部字节（含内层 zip 解开后）里全文搜索。"""
        cells = []
        for key in MATRIX_KEYS:
            for v in MATRIX_VALUES.values():
                cells += [f"{key}={v}", f'"{key}": {_json_form(v)}']
        cells += [
            "{'%s': %s}" % (key, v.replace("VAL", SECRET))
            for key in MATRIX_KEYS
            for v in self.REPR_VALUES
        ]
        cells += list(MATRIX_STANDALONE.values())
        blob = "\n".join(cells)
        log = world["tmp"] / "matrix.log"
        log.write_text(
            "2026-10-13 10:00:00,000 INFO tavotto: start\n" + blob + "\n", encoding="utf-8"
        )
        monkeypatch.setattr(diagnostics, "_app_log_path", lambda: log)
        (world["state"] / "export-defaults.json").write_text(
            json.dumps(
                {
                    "cells": cells[:200],
                    **{
                        k: json.loads(_json_form(MATRIX_VALUES["nested"]))
                        for k in ("token", "apiKey")
                    },
                }
            ),
            encoding="utf-8",
        )
        (world["crash"] / "Tavotto-2026-10-13-130000.ips").write_text(blob, encoding="utf-8")
        proj = _project(client, world["tmp"])
        (proj / "fig_one.py").write_text(
            "\n".join(f"# {c}" for c in cells[:300]) + "\ndef main():\n    return None\n",
            encoding="utf-8",
        )
        chunks = ["\n".join(cells[i : i + 25]) for i in range(0, min(len(cells), 800), 25)][:38]
        payload = {
            "detail": {
                "console": [
                    {"t": i, "level": "error", "message": c, "stack": c}
                    for i, c in enumerate(chunks)
                ],
                "overrides": [
                    {
                        "panel": "p.pdf",
                        "overrides": [
                            {
                                "gid": "g",
                                "prop": "p",
                                "value": {"token": {"raw": SECRET}, "text": c},
                            }
                            for c in chunks[:5]
                        ],
                    }
                ],
                "shell": {"user_agent": f"UA Authorization: Bearer {SECRET}"},
            }
        }
        _prep, z = _prepare(client, payload, source=True, crash_reports=True, corrupt_sessions=True)
        for name, text in _texts(z).items():
            assert not _leaks(text), (name, [c for c in CANARIES if c in text])
        assert "tavotto: start" in _blob(z)  # 反证：日志确实进了包，只是密钥没有


class TestContainerSecrets:
    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ('{"token":["REAL_SECRET"]} ok', '{"token":<redacted>} ok'),
            ('"password": {"v": "REAL_SECRET"} tail', '"password": <redacted> tail'),
            ('\'auth\': ["REAL_SECRET", "b"] z', "'auth': <redacted> z"),
            (
                'token=[ "a]REAL_SECRET" , ["c"] ] after',
                "token=<redacted> after",
            ),  # 引号内的 ]、嵌套
            ('secret={"a":{"b":"] \\" REAL_SECRET ]"}} end', "secret=<redacted> end"),  # 转义引号
            ("token=[1,2] keep", "token=[1,2] keep"),  # 纯数字 / 布尔的容器保留
            ('token={"a": true, "b": null} keep', None),  # 带引号的键 = 含字符串：保守抹
        ],
    )
    def test_the_whole_balanced_bracket_segment_is_replaced(self, raw, want):
        out = diagdetail.Scrubber().text(raw)
        assert "REAL_SECRET" not in out
        if want is not None:
            assert out == want
        else:
            assert "<redacted>" in out

    def test_multiline_containers_longer_than_any_window_are_matched_to_their_closing_bracket(self):
        s = diagdetail.Scrubber()
        big = 'token: {\n  "pad": "' + "x" * 9000 + '",\n  "raw": "REAL_SECRET"\n}\nafter ok'
        out = s.text(big)
        assert "REAL_SECRET" not in out and out == "token: <redacted>\nafter ok"

    @pytest.mark.parametrize(
        "text",
        [
            'a token: {"x": "REAL_SECRET"\nmore REAL_SECRET2',
            'token=[unbalanced "REAL_SECRET" more\nnext line REAL_SECRET2',
            "token: {\n"
            + '  "pad": "'
            + "x" * 20000
            + '",\n  "raw": "REAL_SECRET"\n',  # 没有闭括号
        ],
    )
    def test_unbalanced_containers_are_erased_to_the_end_of_the_input(self, text):
        out = diagdetail.Scrubber().text(text)
        assert "REAL_SECRET" not in out and out.endswith("<redacted>")

    def test_text_around_the_segment_is_preserved(self):
        out = diagdetail.Scrubber().text('a {"token":["S1"], "ok": "keep-me"} b')
        assert "S1" not in out and out.startswith('a {"token":') and "b" in out

    def test_source_mode_masks_quoted_containers_only(self):
        c = diagdetail.Scrubber(source_mode=True)
        assert c.text("token = [self._next()]") == "token = [self._next()]"
        assert "REAL_SECRET" not in c.text('secret = ["REAL_SECRET"]')
        full = diagdetail.Scrubber()
        src = '# token=["REAL_SECRET_C"]\nx = 1\ntoken = [self._next()]\n'
        out = diagdetail.scrub_script(src, "a.py", full, c)
        assert "REAL_SECRET_C" not in out and "token = [self._next()]" in out

    def test_console_record_with_a_container_secret(self, client, world):
        payload = {
            "detail": {
                "console": [
                    {
                        "t": 1,
                        "level": "error",
                        "message": '{"token":["REAL_SECRET_CON"]}',
                        "stack": None,
                    }
                ]
            }
        }
        _prep, z = _prepare(client, payload)
        assert "REAL_SECRET_CON" not in z.read("detail-frontend-console.json").decode()

    def test_scanning_stays_linear(self):
        s = diagdetail.Scrubber()
        for blob in (
            'token=\\"' * 50_000,
            "token=[" * 100_000,
            "token={" * 100_000,
            'token=["' * 60_000 + "x",
        ):
            t0 = time.monotonic()
            s.text(blob)
            assert time.monotonic() - t0 < 5, blob[:10]


class TestTokenizeErrorToken:
    def test_an_errortoken_means_unparsable_on_every_python_version(self, monkeypatch):
        """Python ≤ 3.11 对未闭合的单引号字符串不抛异常、产出 ERRORTOKEN；3.12+ 抛 TokenError。两种都要走整份完整模式。"""
        import tokenize

        def fake(_readline):
            yield tokenize.TokenInfo(tokenize.NAME, "x", (1, 0), (1, 1), "x = 'api_key=REAL\n")
            yield tokenize.TokenInfo(
                tokenize.ERRORTOKEN, "'", (1, 4), (1, 5), "x = 'api_key=REAL\n"
            )

        monkeypatch.setattr(tokenize, "generate_tokens", fake)
        assert diagdetail._prose_spans("x = 'api_key=REAL\n") is None
        out = diagdetail.scrub_script(
            "x = 'api_key=REAL_SECRET_ET\n",
            "a.py",
            diagdetail.Scrubber(),
            diagdetail.Scrubber(source_mode=True),
        )
        assert "REAL_SECRET_ET" not in out

    def test_unterminated_single_quote_on_this_interpreter(self):
        assert diagdetail._prose_spans("x = 'api_key=REAL password=REAL2\n") is None


class TestScriptScrub:
    FULL = staticmethod(lambda: diagdetail.Scrubber())
    CODE = staticmethod(lambda: diagdetail.Scrubber(source_mode=True))

    def scrub(self, text, rel="fig.py"):
        return diagdetail.scrub_script(text, rel, self.FULL(), self.CODE())

    def test_secrets_in_comments_and_strings_are_gone_but_code_is_untouched(self):
        src = (
            "import os\n"
            "# api_key=REAL_SECRET_1\n"
            "# password: hunter2hunter2\n"
            "x = 1  # token=abc123def456ghi789\n"
            'DOC = """connect to https://h.example/p?access_token=REAL_SECRET_2&x=1"""\n'
            "token = self._next()\n"
            'api_key = os.environ["K"]\n'
            'secret = "REAL_SECRET_QUOTED_3"\n'
            "f = f'{x} key=REAL_SECRET_4'\n"
        )
        out = self.scrub(src)
        for leaked in (
            "REAL_SECRET_1",
            "hunter2",
            "abc123def456",
            "REAL_SECRET_2",
            "REAL_SECRET_QUOTED_3",
        ):
            assert leaked not in out, leaked
        assert "token = self._next()" in out
        assert 'api_key = os.environ["K"]' in out
        assert "import os" in out and "x = 1" in out

    @pytest.mark.parametrize(
        "src",
        [
            'x = """api_key=REAL_SECRET_U1\npassword=REAL_SECRET_U2\n',
            "x = 'api_key=REAL_SECRET_U1 password=REAL_SECRET_U2\n",
            "def broken(:\n    # api_key=REAL_SECRET_U1\n    password: REAL_SECRET_U2\n    x = (\n",
        ],
    )
    def test_unparsable_files_are_scrubbed_as_a_whole(self, src):
        """tokenize 失败时分不清哪里是注释 / 字符串（未闭合的三引号之后全是字符串）：整份按完整模式。"""
        out = self.scrub(src)
        assert "REAL_SECRET_U1" not in out and "REAL_SECRET_U2" not in out

    def test_non_python_script_files_use_the_full_mode(self):
        out = self.scrub("api_key=REAL_SECRET_6\ntoken = self._next()\n", rel="run.R")
        assert "REAL_SECRET_6" not in out and "self._next()" not in out  # 完整模式：这类取值也换

    def test_end_to_end_unticked_never_ticked_commented_secret(self, client, world):
        proj = world["tmp"] / "proj2"
        proj.mkdir()
        (proj / "fig_c.py").write_text(
            "# api_key=REAL_SECRET_E2E\ndef main():\n    return None\n", encoding="utf-8"
        )
        items = diagdetail.collect_source(
            diagdetail.Sources(project={"figures_dir": str(proj)}, scripts=["fig_c.py"]),
            diagdetail.DetailOptions(source=True),
            diagdetail.Scrubber(),
        )
        assert b"REAL_SECRET_E2E" not in b"".join(i.data for i in items)


class TestReadFailureIsNotOverBudget:
    def _fail_reads(self, monkeypatch, names):
        real = diagdetail._read_head
        monkeypatch.setattr(
            diagdetail,
            "_read_head",
            lambda path, limit: None if Path(path).name in names else real(path, limit),
        )
        real_capped = diagdetail._read_capped
        monkeypatch.setattr(
            diagdetail,
            "_read_capped",
            lambda path, reg, cap, **k: (
                (None, "read_failed")
                if Path(path).name in names
                else real_capped(path, reg, cap, **k)
            ),
        )

    def test_state_file_that_cannot_be_read_is_read_failed_not_skipped(self, world, monkeypatch):
        self._fail_reads(monkeypatch, {"export-defaults.json"})
        items = diagdetail.collect_state(
            diagdetail.Sources(), diagdetail.DetailOptions(), diagdetail.Scrubber()
        )
        flat = dict(diagdetail.entries(items))
        with zipfile.ZipFile(io.BytesIO(flat["detail-state.zip"])) as z:
            files = {f["name"]: f for f in json.loads(z.read("index.json"))["files"]}
            assert "export-defaults.json" not in z.namelist()
        bad = files["export-defaults.json"]
        assert bad["read_failed"] is True and "skipped_over_budget" not in bad and "file" not in bad
        assert (
            files[layoutsession.FILENAME]["file"]
            and "skipped_over_budget" not in files[layoutsession.FILENAME]
        )

    def test_source_and_crash_and_corrupt_read_failures_are_marked_the_same_way(
        self, world, monkeypatch
    ):
        proj = world["tmp"] / "proj4"
        proj.mkdir()
        (proj / "a.py").write_text("x = 1\n", encoding="utf-8")
        (proj / "b.py").write_text("y = 2\n", encoding="utf-8")
        self._fail_reads(
            monkeypatch,
            {"a.py", "Tavotto-2026-10-13-120000.ips", f"{layoutsession.CORRUPT_PREFIX}123.json"},
        )
        src = diagdetail.Sources(project={"figures_dir": str(proj)}, scripts=["a.py", "b.py"])
        opts = diagdetail.DetailOptions(source=True, crash_reports=True, corrupt_sessions=True)
        scrub = diagdetail.Scrubber()
        for fn, key in (
            (diagdetail.collect_source, "scripts"),
            (diagdetail.collect_crash_reports, "reports"),
            (diagdetail.collect_corrupt_sessions, "files"),
        ):
            items = fn(src, opts, scrub)
            index = json.loads(next(i for i in items if i.path == "index.json").data)
            rows = index[key]
            failed = [r for r in rows if r.get("read_failed") or r.get("content") == "read_failed"]
            assert failed, (fn.__name__, rows)
            assert not any(r.get("skipped_over_budget") for r in failed)
            reconciled = json.loads(diagdetail._reconcile_index(items)[0].data)
            assert not any(r.get("skipped_over_budget") for r in reconciled[key] if r in failed), (
                fn.__name__
            )


class TestSourceDedupe:
    def test_panels_of_the_same_script_do_not_crowd_out_other_scripts(self, world):
        proj = world["tmp"] / "proj5"
        proj.mkdir()
        for n in ("a.py", "b.py"):
            (proj / n).write_text(f"# {n}\n", encoding="utf-8")
        src = diagdetail.Sources(
            project={"figures_dir": str(proj)},
            scripts=["a.py", "b.py"],
            frontend={"overrides": [{"script": "a.py"}, {"script": "a.py"}, {"script": "a.py"}]},
        )
        got = [r for r, _p in diagdetail.source_candidates(src)]
        assert got == ["a.py", "b.py"]
        items = diagdetail.collect_source(
            src, diagdetail.DetailOptions(source=True), diagdetail.Scrubber()
        )
        files = [i.path for i in items if i.path != "index.json"]
        assert len(files) == 2 and sum("a.py" in f for f in files) == 1


class TestDumpChangedWhileReading:
    def test_a_file_that_grows_during_the_read_is_marked_and_not_attached(self, world, monkeypatch):
        dmp = world["crash"] / "Tavotto.exe.5.dmp"
        dmp.write_bytes(b"A" * 1000)
        real_open = Path.open

        def growing_open(self, *a, **k):
            fh = real_open(self, *a, **k)
            if self.name == dmp.name and a and a[0] == "rb":
                with real_open(self, "ab") as extra:  # 读之前它又长了一截（转储还在写）
                    extra.write(b"B" * 500)
            return fh

        monkeypatch.setattr(Path, "open", growing_open)
        items = diagdetail.collect_crash_reports(
            diagdetail.Sources(),
            diagdetail.DetailOptions(crash_reports=True),
            diagdetail.Scrubber(),
        )
        assert dmp.name not in [i.path for i in items]
        rows = {r["name"]: r for r in json.loads(items[0].data)["reports"]}
        assert rows[dmp.name]["content"] == "changed_while_reading"
        assert not any(r.get("content") == "skipped_over_budget" for r in rows.values())
        assert items[0].dropped_bytes == 0

    def test_a_stable_file_is_attached_whole(self, world):
        (world["crash"] / "Tavotto.exe.6.dmp").write_bytes(b"C" * 1000)
        items = diagdetail.collect_crash_reports(
            diagdetail.Sources(),
            diagdetail.DetailOptions(crash_reports=True),
            diagdetail.Scrubber(),
        )
        assert {i.path: i.data for i in items}["Tavotto.exe.6.dmp"] == b"C" * 1000


class TestFilesChangingDuringCollection:
    """枚举之后文件消失 / 变大：每个读盘的类别都只让那一个文件出局（标 read_failed / changed_while_reading），
    其余文件照收，类别不变成 error.json；变大的文件绝不按「此刻的大小」读进内存。"""

    def _setup(self, world):
        proj = world["tmp"] / "projr"
        proj.mkdir(exist_ok=True)
        (proj / "victim.py").write_text("# victim\n", encoding="utf-8")
        (proj / "bystander.py").write_text("# bystander\n", encoding="utf-8")
        state = world["state"]
        (state / "victim-state.json").write_text('{"v": 1}', encoding="utf-8")
        (state / f"{layoutsession.CORRUPT_PREFIX}victim.json").write_text(
            "v" * 100, encoding="utf-8"
        )
        (state / f"{layoutsession.CORRUPT_PREFIX}bystander.json").write_text(
            "b" * 100, encoding="utf-8"
        )
        (world["crash"] / "Tavotto.exe.9.dmp").write_bytes(b"V" * 1000)
        (world["crash"] / "Tavotto.exe.8.dmp").write_bytes(b"B" * 1000)
        os.utime(world["crash"] / "Tavotto.exe.8.dmp", (time.time() - 5, time.time() - 5))
        return proj

    CASES = {
        "state": ("victim-state.json", "files", "name"),
        "corrupt": (f"{layoutsession.CORRUPT_PREFIX}victim.json", "files", "name"),
        "source": ("victim.py", "scripts", "script"),
        "crash": ("Tavotto.exe.9.dmp", "reports", "name"),
    }

    def _run(self, kind, proj, scrub=None):
        scrub = scrub or diagdetail.Scrubber()
        if kind == "state":
            return diagdetail.collect_state(diagdetail.Sources(), diagdetail.DetailOptions(), scrub)
        if kind == "corrupt":
            return diagdetail.collect_corrupt_sessions(
                diagdetail.Sources(), diagdetail.DetailOptions(corrupt_sessions=True), scrub
            )
        if kind == "source":
            src = diagdetail.Sources(
                project={"figures_dir": str(proj)}, scripts=["victim.py", "bystander.py"]
            )
            return diagdetail.collect_source(src, diagdetail.DetailOptions(source=True), scrub)
        return diagdetail.collect_crash_reports(
            diagdetail.Sources(), diagdetail.DetailOptions(crash_reports=True), scrub
        )

    @pytest.mark.parametrize("kind", ["state", "corrupt", "source", "crash"])
    @pytest.mark.parametrize("what", ["vanish", "grow"])
    def test_one_file_changing_only_drops_that_file(self, kind, what, world, monkeypatch):
        proj = self._setup(world)
        victim, key, namekey = self.CASES[kind]
        real = diagdetail._read_capped
        reads = []

        def meddling(path, reg, cap, **k):
            if Path(path).name == victim or Path(path).name.endswith(victim):
                if what == "vanish":
                    Path(path).unlink()
                else:
                    with Path(path).open("ab") as fh:
                        fh.write(b"G" * 5000)  # 枚举之后变大
            got = real(path, reg, cap, **k)
            reads.append((Path(path).name, got[0] is not None))
            return got

        monkeypatch.setattr(diagdetail, "_read_capped", meddling)
        items = self._run(kind, proj)
        assert not any(i.path == "error.json" for i in items)  # 类别没有变成 error.json
        index = json.loads(next(i for i in items if i.path == "index.json").data)
        rows = [r for r in index[key] if victim in str(r.get(namekey))]
        assert rows, index
        want = "read_failed" if what == "vanish" else "changed_while_reading"
        row = rows[0]
        assert row.get(want) is True or row.get("content") == want, (kind, what, row)
        assert "skipped_over_budget" not in row and "file" not in row
        # 变大的文件内容一个字节都没进来
        assert b"G" * 100 not in b"".join(i.data for i in items)
        # 其余文件照收
        others = [i for i in items if i.path != "index.json"]
        assert others, "bystander 文件也应被收录"

    def test_a_corrupt_copy_vanishing_between_listing_and_sorting_does_not_break_the_category(
        self, world, monkeypatch
    ):
        self._setup(world)
        gone = f"{layoutsession.CORRUPT_PREFIX}victim.json"
        real_stat = Path.stat
        registered = {"done": False}
        real_candidates = diagdetail._stable_candidates

        def after_registration(paths):
            got = real_candidates(paths)
            registered["done"] = True  # 登记（逐个 stat）完成之后，这个副本才“消失”
            return got

        def flaky(self, *a, **k):
            if registered["done"] and self.name == gone:
                raise FileNotFoundError(self.name)
            return real_stat(self, *a, **k)

        monkeypatch.setattr(diagdetail, "_stable_candidates", after_registration)
        monkeypatch.setattr(Path, "stat", flaky)
        items = diagdetail.collect_corrupt_sessions(
            diagdetail.Sources(),
            diagdetail.DetailOptions(corrupt_sessions=True),
            diagdetail.Scrubber(),
        )
        assert not any(i.path == "error.json" for i in items)
        index = json.loads(next(i for i in items if i.path == "index.json").data)
        by = {r["name"]: r for r in index["files"]}
        assert by[gone].get("read_failed") is True
        assert any(i.path.endswith("bystander.json") for i in items)

    def test_the_read_limit_comes_from_the_registered_size_not_the_current_one(
        self, world, monkeypatch
    ):
        f = world["state"] / "big-state.json"
        f.write_bytes(b"x" * 1000)
        reg = diagdetail._file_id(f)
        f.write_bytes(b"x" * 10_000_000)  # 登记之后变成 10 MB
        sizes = []
        real_open = Path.open

        def spy(self, *a, **k):
            fh = real_open(self, *a, **k)
            if self.name == f.name:
                orig = fh.read
                fh.read = lambda n=-1: (sizes.append(n), orig(n))[1]
            return fh

        monkeypatch.setattr(Path, "open", spy)
        raw, why = diagdetail._read_capped(f, reg, 1 << 20, whole=True)
        assert raw is None and why == "changed_while_reading"
        assert sizes == [] or max(sizes) <= reg.size + 1  # 没读，或者最多读 登记大小 + 1


class TestSourceStatRaces:
    def test_an_unrelated_script_vanishing_does_not_break_the_category(self, world, monkeypatch):
        proj = world["tmp"] / "proj6"
        proj.mkdir()
        for n in ("focus.py", "gone.py", "other.py"):
            (proj / n).write_text(f"# {n}\n", encoding="utf-8")
        real_stat = Path.stat

        calls = {"n": 0}

        def flaky(self, *a, **k):
            if self.name == "gone.py":
                calls["n"] += 1
                if (
                    calls["n"] > 1
                ):  # 枚举时还在（is_file / 第一次 stat 过了），排序 / 读取时已经没了
                    raise FileNotFoundError(self.name)
            return real_stat(self, *a, **k)

        monkeypatch.setattr(Path, "stat", flaky)
        src = diagdetail.Sources(
            project={"figures_dir": str(proj)},
            scripts=["focus.py", "gone.py", "other.py"],
            frontend={"overrides": [{"script": "focus.py"}]},
        )
        items = diagdetail.collect_source(
            src, diagdetail.DetailOptions(source=True), diagdetail.Scrubber()
        )
        files = [i.path for i in items if i.path != "index.json"]
        assert any("focus.py" in f for f in files) and any("other.py" in f for f in files)
        assert not any("gone.py" in f for f in files)
        index = json.loads(next(i for i in items if i.path == "index.json").data)
        gone = [r for r in index["scripts"] if "gone.py" in r["script"]]
        assert gone and gone[0].get("read_failed") is True and "skipped_over_budget" not in gone[0]
        assert not any(i.path == "error.json" for i in items)


class TestNativePanelRenderEnv:
    def _src(self, registry=None):
        return diagdetail.Sources(
            project={"figures_dir": "/p"},
            registry=registry,
            frontend={
                "overrides": [{"panel": "runtime:abc#1", "kind": "runtime", "script": "s.py"}]
            },
        )

    class FakeSession:
        interpreter = "/conda/bin/python"
        interpreter_fingerprint = "fp123"
        python_version = "3.11.9"

        def last_build_runtime(self):
            return {"prefix": "/conda", "cwd": "/p"}

        def diagnostics(self):
            return {"state": "ready", "execution_profile": "native"}

    def _patch(self, monkeypatch, *, profile, session):
        from tavotto.engine import enginesession, nativesession, runtimeasset

        monkeypatch.setattr(runtimeasset, "is_runtime_id", lambda f: str(f).startswith("runtime:"))
        monkeypatch.setattr(enginesession, "profile_of", lambda root, fid: profile)
        monkeypatch.setattr(
            runtimeasset, "resolve", lambda fid, reg: {"script": "s.py", "stem": "fig"}
        )
        monkeypatch.setattr(nativesession.REGISTRY, "route_for", lambda root, script, stem: session)
        seen = {}
        monkeypatch.setattr(
            diagdetail, "_probe_subprocess", lambda py, b: seen.update(py=py) or {"status": "ok"}
        )
        return seen

    def test_native_panel_reports_the_native_session_and_probes_its_interpreter(self, monkeypatch):
        seen = self._patch(monkeypatch, profile="native", session=self.FakeSession())
        doc = json.loads(
            diagdetail.collect_render_env(
                self._src(object()), diagdetail.DetailOptions(), diagdetail.Scrubber()
            )[0].data
        )
        assert doc["execution_profile"] == "native" and doc["inspected"] == "native_session"
        assert seen["py"] == "/conda/bin/python"
        assert doc["interpreter"]["source"] == "native_session"
        assert (
            doc["native_receipt"]["prefix"] == "/conda"
            and doc["native_session"]["state"] == "ready"
        )

    def test_native_panel_without_a_live_session_is_honest_about_it(self, monkeypatch):
        seen = self._patch(monkeypatch, profile="native", session=None)
        monkeypatch.setattr(
            pool, "resolve_worker_python", lambda d, **k: ("/safe/python", "bundled")
        )
        doc = json.loads(
            diagdetail.collect_render_env(
                self._src(object()), diagdetail.DetailOptions(), diagdetail.Scrubber()
            )[0].data
        )
        assert doc["execution_profile"] == "native" and doc["inspected"] == "safe_pool"
        assert doc["native_interpreter"] == "unknown" and seen["py"] == "/safe/python"

    def test_safe_panel_is_inspected_in_the_safe_pool(self, monkeypatch):
        self._patch(monkeypatch, profile="safe", session=None)
        monkeypatch.setattr(
            pool, "resolve_worker_python", lambda d, **k: ("/safe/python", "bundled")
        )
        doc = json.loads(
            diagdetail.collect_render_env(
                self._src(object()), diagdetail.DetailOptions(), diagdetail.Scrubber()
            )[0].data
        )
        assert doc["execution_profile"] == "safe" and doc["inspected"] == "safe_pool"
        assert "native_interpreter" not in doc


class TestRenderEnvIsProjectAware:
    def test_the_probe_runs_in_the_projects_own_interpreter(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            pool, "resolve_worker_python", lambda d, **k: ("/p/.venv/bin/python", "project_venv")
        )
        monkeypatch.setattr(
            diagdetail,
            "_probe_subprocess",
            lambda python, bundled: seen.update(python=python, bundled=bundled) or {"status": "ok"},
        )
        items = diagdetail.collect_render_env(
            diagdetail.Sources(project={"figures_dir": "/p"}),
            diagdetail.DetailOptions(),
            diagdetail.Scrubber(),
        )
        doc = json.loads(items[0].data)
        assert seen["python"] == "/p/.venv/bin/python" and seen["bundled"] is False
        assert (
            doc["interpreter"]["source"] == "project_venv"
            and doc["interpreter"]["scope"] == "project"
        )

    def test_without_a_project_it_is_the_global_choice(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            pool,
            "resolve_worker_python",
            lambda d, **k: calls.append(d) or ("/g/python", "bundled"),
        )
        monkeypatch.setattr(
            diagdetail, "_probe_subprocess", lambda python, bundled: {"bundled": bundled}
        )
        items = diagdetail.collect_render_env(
            diagdetail.Sources(), diagdetail.DetailOptions(), diagdetail.Scrubber()
        )
        doc = json.loads(items[0].data)
        assert (
            calls == [None]
            and doc["interpreter"]["scope"] == "global"
            and doc["probe"] == {"bundled": True}
        )


class TestShareDropsAreAccounted:
    def test_over_budget_sources_are_listed_as_skipped_and_counted(self, world, monkeypatch):
        proj = world["tmp"] / "proj3"
        proj.mkdir()
        names = []
        for i in range(4):
            (proj / f"s{i}.py").write_text(f"# script {i}\n" + "x = 1\n" * 300, encoding="utf-8")
            names.append(f"s{i}.py")
        monkeypatch.setitem(diagdetail.SHARE, "source", 4000)  # 放得下约两份
        opts = diagdetail.DetailOptions(source=True)
        src = diagdetail.Sources(project={"figures_dir": str(proj)}, scripts=names)
        items = diagdetail.collect(src, opts)
        mine = [i for i in items if i.kind == "source"]
        total_raw = sum((proj / n).stat().st_size for n in names)
        assert sum(i.dropped_bytes for i in mine) > 0
        flat = dict(diagdetail.entries(items))
        with zipfile.ZipFile(io.BytesIO(flat["optional-source.zip"])) as z:
            members = set(z.namelist()) - {"index.json"}
            index = json.loads(z.read("index.json"))
        listed = {r["file"]: r for r in index["scripts"]}
        assert set(listed) >= members and len(members) < 4
        for f, r in listed.items():  # index 与 zip 逐一对得上
            assert bool(r.get("skipped_over_budget")) == (f not in members)
        # manifest 的 original_bytes = 全部原始字节（脚本）+ index
        _data, _kept, trunc = diagdetail.fit(items, lambda its, t: b"x", 10**9)
        row = next(t for t in trunc if t["kind"] == "source")
        assert total_raw <= row["original_bytes"] <= total_raw + 4096
        assert row["kept_bytes"] < row["original_bytes"]

    def test_state_index_marks_files_dropped_by_fit(self):
        group = [
            diagdetail.Item(
                "state",
                "index.json",
                b'{"files": [{"name": "a", "file": "a"}, {"name": "b", "file": "b"}, {"name": "c", "kind": "corrupt"}]}',
            ),
            diagdetail.Item("state", "a", b"1"),
        ]
        doc = json.loads(diagdetail._reconcile_index(group)[0].data)
        assert "skipped_over_budget" not in doc["files"][0]
        assert doc["files"][1]["skipped_over_budget"] is True
        assert (
            "skipped_over_budget" not in doc["files"][2]
        )  # 没有 file 键的（损坏副本只列清单）不动


# ---------------------------------------------------------------------------
# 请求记录环
# ---------------------------------------------------------------------------
class TestRequestRing:
    def test_records_method_rule_status_ms_code_and_nothing_else(self, client, world):
        client.get("/api/project?secretq=CANARY_QS_91")
        client.post("/api/perf/report", data=b"CANARY_BODY_92", content_type="application/json")
        _prep, z = _prepare(client)
        doc = json.loads(z.read("detail-requests.json"))
        recs = doc["records"]
        text = z.read("detail-requests.json").decode()
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
        env = json.loads(z.read("detail-render-env.json"))
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
        err = json.loads(z.read("detail-requests.json"))
        assert err["collection_error"].startswith("RuntimeError: explode in ~")
        assert "detail-errors.json" in z.namelist()  # 其他类不受影响

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
        assert diagdetail.dns_fact("a.example") == {
            "host": "a.example",
            "result": "unknown",
            "reason": "OSError",
        }
        monkeypatch.setattr(diagdetail, "DNS_TIMEOUT_S", 0.05)
        monkeypatch.setattr(diagdetail, "_resolve", lambda h: time.sleep(0.5) or ["1.1.1.1"])
        assert diagdetail.dns_fact("b.example")["reason"] == "timeout"


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
                z.writestr(i.path, i.data)
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

    def test_real_cap_with_incompressible_content(self):
        items = _items(
            errors=20_000_000, logs=10_000_000, source=30_000_000, crash_reports=20_000_000
        )
        limit = diagsend.MAX_BYTES - diagdetail.FIT_SLACK
        assert limit == 63 * 1024 * 1024 and diagsend.MAX_BYTES == 64 * 1024 * 1024
        data, kept, trunc = diagdetail.fit(items, _build_factory(200_000), limit)
        assert len(data) <= diagsend.MAX_BYTES
        assert [t["kind"] for t in trunc][0] == "crash_reports"

    def test_end_to_end_manifest_and_prepare_report_truncation(self, client, world, monkeypatch):
        # 把上限压到 60 KiB 左右：核心包约 10 KiB，日志与源码是随机内容
        _project(client, world["tmp"])
        (world["tmp"] / "proj" / "big.py").write_bytes(
            b"# " + base64.b64encode(os.urandom(200_000))
        )
        monkeypatch.setattr(diagsend, "MAX_BYTES", 120 * 1024)
        monkeypatch.setattr(diagdetail, "FIT_SLACK", 8192)
        big = world["tmp"] / "big.log"
        big.write_text(
            "2026-10-13 10:00:00,000 INFO tavotto: "
            + base64.b64encode(os.urandom(400_000)).decode(),
            encoding="utf-8",
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
# infra 诊断服务的 ZIP 检查（条目名扁平、≤ 32 条、压缩比 ≤ 200、manifest ≤ 64 KiB）
# ---------------------------------------------------------------------------
def _zip_of(entries: dict[str, bytes], method=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for n, d in entries.items():
            z.writestr(n, d)
    return buf.getvalue()


class TestServerZipRules:
    def test_constants_are_the_server_values(self):
        r = diagsend.ZIP_RULES
        assert r["max_entries"] == 32 and r["max_ratio"] == 200 and r["max_manifest"] == 64 * 1024
        assert r["max_entry_uncompressed"] == 128 * 1024 * 1024
        assert r["max_total_uncompressed"] == 256 * 1024 * 1024
        assert diagsend.MAX_BUNDLE_ENTRIES == 32
        assert diagsend.MAX_BYTES == 64 * 1024 * 1024

    @pytest.mark.parametrize(
        ("entries", "problem"),
        [
            ({"manifest.json": b"{}", "detail/errors.json": b"x"}, "unsafe_entry_name"),
            ({"manifest.json": b"{}", "a" * 65: b"x"}, "unsafe_entry_name"),
            ({"manifest.json": b"{}", ".hidden": b"x"}, "unsafe_entry_name"),
            (
                {"manifest.json": b"{}", **{f"f{i}.txt": b"x" for i in range(32)}},
                "too_many_entries",
            ),
            ({"report.json": b"{}"}, "missing_manifest"),
            ({"manifest.json": os.urandom(70_000)}, "manifest_too_large"),
            ({"manifest.json": b"{}", "log.txt": b"same line\n" * 100_000}, "compression_ratio"),
        ],
    )
    def test_zip_problem_flags_each_server_rule(self, entries, problem):
        assert diagsend.zip_problem(_zip_of(entries)) == problem

    def test_a_compliant_zip_passes_and_stored_entries_have_ratio_one(self):
        ok = _zip_of(
            {"manifest.json": b"{}", "log.txt": b"same line\n" * 100_000}, zipfile.ZIP_STORED
        )
        assert diagsend.zip_problem(ok) is None
        assert diagsend.zip_problem(_zip_of({"manifest.json": b"{}", "a.json": b"{}"})) is None

    def test_prepare_refuses_a_noncompliant_bundle_as_an_internal_error(self):
        with pytest.raises(diagsend.SendError) as exc:
            diagsend.inspect_bundle(
                _zip_of({"manifest.json": b'{"schema_version": 8}', "d/x.json": b"x"})
            )
        assert exc.value.code == "bundle_invalid"

    def test_ratio_safe_restamps_only_the_offending_entries(self):
        raw = _zip_of(
            {
                "manifest.json": b"{}",
                "log.txt": b"same line\n" * 100_000,
                "ok.bin": os.urandom(4000),
            }
        )
        assert diagsend.zip_problem(raw) == "compression_ratio"
        fixed = diagdetail.ratio_safe(raw)
        assert diagsend.zip_problem(fixed) is None
        with zipfile.ZipFile(io.BytesIO(fixed)) as z:
            assert z.getinfo("log.txt").compress_type == zipfile.ZIP_STORED
            assert z.getinfo("manifest.json").compress_type == zipfile.ZIP_DEFLATED
            assert z.read("log.txt") == b"same line\n" * 100_000
            assert z.namelist() == ["manifest.json", "log.txt", "ok.bin"]
        assert diagdetail.ratio_safe(fixed) == fixed  # 幂等
        clean = _zip_of({"manifest.json": b"{}"})
        assert diagdetail.ratio_safe(clean) is clean  # 没问题的包原样返回

    def test_max_configuration_stays_within_every_server_rule(self, client, world):
        _project(client, world["tmp"])
        crash = world["crash"]
        for i in range(12):  # 多于 CRASH_FILES，验证封顶
            (crash / f"Tavotto-2026-10-13-1200{i:02d}.ips").write_text(
                f"crash {i}", encoding="utf-8"
            )
        for i in range(8):
            (world["state"] / f"{layoutsession.CORRUPT_PREFIX}{i}.json").write_text(
                "x" * 50, encoding="utf-8"
            )
        prep, z = _prepare(client, source=True, crash_reports=True, corrupt_sessions=True)
        names = z.namelist()
        assert len(names) <= 32, names
        assert all(diagsend.ZIP_RULES["name"].fullmatch(n) for n in names), names
        assert {
            "optional-source.zip",
            "optional-crash-reports.zip",
            "optional-corrupt-sessions.zip",
        } <= set(names)
        assert len(z.read("manifest.json")) < diagsend.ZIP_RULES["max_manifest"]
        assert (
            max(i.file_size for i in z.infolist()) <= diagsend.ZIP_RULES["max_entry_uncompressed"]
        )
        assert (
            sum(i.file_size for i in z.infolist()) <= diagsend.ZIP_RULES["max_total_uncompressed"]
        )
        assert prep["size"] <= diagsend.MAX_BYTES
        data = client.get(f"/api/diagnostics/send/{prep['id']}/bundle").data
        assert diagsend.zip_problem(data) is None

    def test_a_huge_single_line_repeated_log_does_not_trip_the_ratio_rule(
        self, client, world, monkeypatch
    ):
        rep = world["tmp"] / "rep.log"
        rep.write_text(
            "2026-10-13 10:00:00,000 INFO tavotto: same line again\n" * 60_000, encoding="utf-8"
        )
        monkeypatch.setattr(diagnostics, "_app_log_path", lambda: rep)
        prep, z = _prepare(client)
        info = z.getinfo("detail-app-log.txt")
        assert info.file_size > 1024 and info.file_size > 200 * 1  # 原始内容确实高度重复
        assert (
            diagsend.zip_problem(client.get(f"/api/diagnostics/send/{prep['id']}/bundle").data)
            is None
        )
        assert info.file_size <= 200 * max(info.compress_size, 1)

    def test_inner_zips_hold_the_directory_structure_and_an_index(self, client, world):
        _project(client, world["tmp"])
        _prep, z = _prepare(client, source=True, crash_reports=True)
        assert "index.json" in _inner(z, "optional-source.zip").namelist()
        assert "index.json" in _inner(z, "optional-crash-reports.zip").namelist()
        assert "index.json" in _inner(z, "detail-overrides.zip").namelist() or any(
            n.startswith("live-") for n in _inner(z, "detail-overrides.zip").namelist()
        )
        manifest = json.loads(z.read("manifest.json"))
        assert all("/" not in f["path"] for f in manifest["detail_files"])  # 内层清单不进 manifest


# ---------------------------------------------------------------------------
# 全局保管预算 / fit 重建上界
# ---------------------------------------------------------------------------
def _stored_bundle(pad: int) -> bytes:
    return _zip_of(
        {"manifest.json": b'{"schema_version": 8}', "app.log": os.urandom(pad)}, zipfile.ZIP_STORED
    )


class TestWorstCasePrepare:
    def test_everything_ticked_and_every_share_filled_stays_under_the_cap_in_bounded_time(
        self, client, world, monkeypatch
    ):
        """全勾、各类逼近份额的不可压缩内容：份额之和小于 63 MiB，所以不需要 `fit` 反复重建；整个 prepare 的耗时有界。"""
        tmp = world["tmp"]
        proj = _project(client, tmp)
        for i in range(24):  # 源码：每份 ~700 KB 不可压缩（base64），合计接近 16 MiB 份额
            (proj / f"gen_{i:02d}.py").write_text(
                "# " + base64.b64encode(os.urandom(520_000)).decode(), encoding="utf-8"
            )
        big_log = tmp / "big_app.log"
        big_log.write_text(
            "2026-10-13 10:00:00,000 INFO tavotto: "
            + base64.b64encode(os.urandom(3_000_000)).decode()
            + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(diagnostics, "_app_log_path", lambda: big_log)
        for i in range(4):
            (world["crash"] / f"Tavotto-2026-10-13-1{i}0000.ips").write_bytes(
                base64.b64encode(os.urandom(2_800_000))
            )
        for i in range(3):
            (world["state"] / f"{layoutsession.CORRUPT_PREFIX}9{i}.json").write_bytes(
                base64.b64encode(os.urandom(300_000))
            )
        builds = []
        real_fit = diagdetail.fit

        def counting_fit(items, build, limit):
            def counted(its, trunc):
                builds.append(1)
                return build(its, trunc)

            return real_fit(items, counted, limit)

        monkeypatch.setattr(diagdetail, "fit", counting_fit)
        monkeypatch.setattr(m.engine_diagdetail, "fit", counting_fit)
        t0 = time.monotonic()
        prep, z = _prepare(client, source=True, crash_reports=True, corrupt_sessions=True)
        took = time.monotonic() - t0
        print(
            f"WORST-CASE prepare: {took:.2f}s, bundle {prep['size'] / 2**20:.1f} MiB, builds={len(builds)}"
        )
        assert prep["size"] > 10 * 2**20 and not prep["too_large"]
        assert prep["size"] <= diagsend.MAX_BYTES
        assert len(builds) <= 1 + diagdetail.FIT_MAX_REBUILDS
        assert took < 60


class TestStoredBudget:
    def test_constants(self):
        assert diagsend.STORED_BUDGET_BYTES == 128 * 1024 * 1024
        # 最坏情况下各类份额之和仍小于截断预算：fit 不会因为份额而反复重建
        assert sum(diagdetail.SHARE.values()) < diagsend.MAX_BYTES - diagdetail.FIT_SLACK
        assert 0 < diagdetail.FIT_MAX_REBUILDS <= 24

    def test_three_projects_over_budget_reclaims_the_longest_idle_one(self, client, monkeypatch):
        monkeypatch.setattr(diagsend, "STORED_BUDGET_BYTES", 400_000)
        a = diagsend.prepare(_stored_bundle(150_000), scope="A")
        b = diagsend.prepare(_stored_bundle(150_000), scope="B")
        diagsend._SESSIONS["A"].touched -= 100  # A 闲置最久
        c = diagsend.prepare(_stored_bundle(150_000), scope="C")  # 合计 450k > 400k
        assert "A" not in diagsend._SESSIONS and {"B", "C"} <= set(diagsend._SESSIONS)
        with pytest.raises(diagsend.SendError) as exc:
            diagsend.status(a["id"], scope="A")  # 被挤掉的会话：404，前端走「重新准备」
        assert exc.value.status == 404
        assert diagsend.status(b["id"], scope="B")["state"] == "prepared"
        assert diagsend.status(c["id"], scope="C")["state"] == "prepared"

    def test_a_sending_bundle_is_neither_counted_nor_reclaimed(self, client, monkeypatch):
        monkeypatch.setattr(diagsend, "STORED_BUDGET_BYTES", 250_000)
        diagsend.prepare(_stored_bundle(100_000), scope="A")
        diagsend.prepare(_stored_bundle(100_000), scope="B")
        diagsend._SESSIONS["A"].touched -= 100
        diagsend._SESSIONS["B"].touched -= 50
        diagsend._SESSIONS["B"].state = "sending"  # 发送中：不计入、不回收
        diagsend.prepare(
            _stored_bundle(100_000), scope="C"
        )  # 计入的是 A + C = 200k ≤ 250k，暂时都留着
        diagsend.prepare(_stored_bundle(100_000), scope="D")  # A + C + D = 300k > 250k → 回收 A
        assert "A" not in diagsend._SESSIONS
        assert (
            diagsend._SESSIONS["B"].state == "sending"
            and diagsend._SESSIONS["B"].bundle is not None
        )
        assert {"C", "D"} <= set(diagsend._SESSIONS)

    def test_the_bundle_just_prepared_is_never_its_own_victim(self, client, monkeypatch):
        monkeypatch.setattr(diagsend, "STORED_BUDGET_BYTES", 10)
        got = diagsend.prepare(_stored_bundle(5000), scope="Z")
        assert diagsend.status(got["id"], scope="Z")["state"] == "prepared"

    def test_a_session_that_already_sent_complete_is_reconciled_not_dropped(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(diagsend, "STORED_BUDGET_BYTES", 250_000)
        diagsend.prepare(_stored_bundle(100_000), scope="A")
        a = diagsend._SESSIONS["A"]
        a.touched -= 100
        a.state, a.complete_sent, a.report_id = "failed", True, "TVD-AAAA-AAAA-AAAA-AAAA"
        a.server_open, a.token = True, "0" * 64
        monkeypatch.setattr(diagsend, "_cancel_remote_async", lambda sess, settle=False: None)
        diagsend.prepare(_stored_bundle(100_000), scope="B")
        diagsend.prepare(_stored_bundle(100_000), scope="C")
        # A 走 I1 对账（先 cancelling 等答复），不是被直接移出丢掉编号
        assert (
            diagsend._SESSIONS["A"].state == "cancelling" and diagsend._SESSIONS["A"].discard_after
        )


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
        assert "detail-" in send_readme and 'Without "script source" ticked' in send_readme

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
