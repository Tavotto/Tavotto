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
import re
import subprocess
import sys
import tempfile
import threading
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
            'password="hunter2xx"': "password=<redacted>",
            "token=abcdefgh&x=1": "token=<redacted>",  # fail-closed：替换单位是「从值起点到行尾」
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
        assert "abcd1234efgh" not in s.text('api_key = "abcd1234efgh"')


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
        err = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stderr
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

    def test_shell_written_minidumps_with_a_timestamp_are_collected(self, world):
        """壳自写的小型转储文件名是 `Tavotto.exe.<pid>.<毫秒戳>.dmp`（src-tauri/src/crash_dump.rs，#1068 r4280527495）：
        取第一个 `.` 之前的 `Tavotto` 判归属，新旧两种格式都要被收到；别人的带戳转储照旧不收。"""
        crash = world["crash"]
        (crash / "Tavotto.exe.4242.987654321.dmp").write_bytes(b"MDMP_NEW_FORMAT")
        (crash / "Tavotto.exe.4242.dmp").write_bytes(b"MDMP_OLD_FORMAT")
        (crash / "chrome.exe.9.123456.dmp").write_bytes(b"DMP_CHROME_STAMPED")
        items = diagdetail.collect_crash_reports(
            diagdetail.Sources(),
            diagdetail.DetailOptions(crash_reports=True),
            diagdetail.Scrubber(),
        )
        dumps = {i.path: i.data for i in items if i.path.endswith(".dmp")}
        assert dumps == {
            "Tavotto.exe.4242.987654321.dmp": b"MDMP_NEW_FORMAT",
            "Tavotto.exe.4242.dmp": b"MDMP_OLD_FORMAT",
        }
        index = json.loads(next(i for i in items if i.path.endswith("index.json")).data)
        rows = {r["name"]: r for r in index["reports"] if r["name"].endswith(".dmp")}
        assert rows["Tavotto.exe.4242.987654321.dmp"]["content"] == "raw_binary"

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
    "pw", "pin", "otp", "dsn", "mnemonic", "seed_phrase", "connection_string", "database_url",
    "user_pw", "db-pin", "OTP_CODE", "密码", "pass", "DB_PASS", "smtpPass", "redis-pass",
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
        ids=["long-with-end", "long-missing-end", "two-blocks"],
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
            ('password="abc def REAL_SECRET\nnext REAL_SECRET2', ""),  # 引号没闭合：抹到输入末尾
            ("secret='abc def REAL_SECRET\nnext REAL_SECRET2", ""),
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
        # 键之前的内容保留；键所在行的剩余部分按 fail-closed 一并抹掉
        assert full.text("pre {'%s': SecretStr('%s'), 'n': 1} tail" % (key, SECRET)).startswith(
            "pre {"
        )

    def test_prefixed_quote_is_replaced_whole_and_calls_in_code_are_left_alone(self):
        full, code = diagdetail.Scrubber(), diagdetail.Scrubber(source_mode=True)
        assert full.text("api_key=b'S1 x'") == "api_key=<redacted>"
        assert "S1" not in full.text("{'api_key': b'S1'}") and "'S1'" not in full.text(
            "{'api_key': b'S1'}"
        )
        assert (
            code.text('token = getpass.getpass("Password: ")')
            == 'token = getpass.getpass("Password: ")'
        )

    # 值里含转义引号 / 转义反斜杠 / 另一种引号：定界靠真正的扫描，不靠“到下一个引号为止”的正则
    QUOTE_TAILS = [
        'ab\\"S', 'ab\\\\\\"S', 'S\\\\', "ab\\'S", "a'b S", 'a"bS', 'a\\\\\\\\\\"S',
        'x\\\\\\\\"S"', 'S\\\\\\\\',
    ]  # fmt: skip

    @pytest.mark.parametrize("key", MATRIX_KEYS)
    def test_values_with_escaped_quotes_and_backslashes(self, key, world):
        full = diagdetail.Scrubber()
        for tail in self.QUOTE_TAILS:
            body = tail.replace("S", SECRET)
            for q in ('"', "'"):
                if (q == '"' and '"' in body.replace('\\"', "")) or (
                    q == "'" and "'" in body.replace("\\'", "")
                ):
                    continue  # 值里有同种的未转义引号：那是另一个值的边界，不在本矩阵里
                lit = f"{q}{body}{q}"
                for line in (
                    "{%s%s%s: %s, 'n': 1} tail" % (q, key, q, lit),
                    "%s=%s tail" % (key, lit),
                    "%s: %s tail" % (key, lit),
                ):
                    out = full.text(line)
                    assert SECRET not in out, (key, tail, line, out)
                # JSON 载体：结构化路径
                js = json.dumps(
                    ensure_ascii=False, obj={key: body.replace('\\"', '"').replace("\\\\", "\\")}
                )
                assert SECRET not in full.json_or_text(js)
                assert SECRET not in full.text(js)

    def test_state_json_goes_through_the_structured_path(self):
        full = diagdetail.Scrubber()
        raw = json.dumps(
            {"token": f'abc"{SECRET}', "nested": {"api_key": [f"x{SECRET}"]}, "keep": "me"}
        )
        out = full.json_or_text(raw)
        assert SECRET not in out and json.loads(out)["keep"] == "me"
        broken = '{"token": "abc\\"' + SECRET + "\", 'cut"  # 不是合法 JSON：退回文本兜底
        assert SECRET not in full.json_or_text(broken)

    def test_backslash_heavy_input_stays_linear(self):
        full = diagdetail.Scrubber()
        for blob in (
            'token="' + "\\" * 200_000,
            'token=\\"' + "\\" * 200_000 + '"',
            '{"token": "' + '\\"' * 100_000,
        ):
            t0 = time.monotonic()
            full.text(blob)
            assert time.monotonic() - t0 < 5

    def test_innocent_neighbours_survive(self, world):
        full = diagdetail.Scrubber()
        assert (
            full.text("the author wrote about authority: fine")
            == "the author wrote about authority: fine"
        )
        out = full.obj({"author": "A. Person", "authority_variant": "var:abc", "n": 4})
        assert out == {"author": "A. Person", "authority_variant": "var:abc", "n": 4}
        assert full.obj({"token": [1, 2, 3]}) == {"token": "<redacted>"}  # 数字也是口令
        assert full.obj({"token": [], "pin": None, "otp": False}) == {
            "token": [],
            "pin": None,
            "otp": False,
        }

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
        cells += [
            "%s=%s" % (key, tq.replace("VAL2", SECRET + "2").replace("VAL", SECRET))
            for key in MATRIX_KEYS
            for tq in TestFailClosedScanner.TRIPLES
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
            ('{"token":["REAL_SECRET"]} ok', '{"token":<redacted>'),  # fail-closed：到行尾
            ('"password": {"v": "REAL_SECRET"} tail', '"password": <redacted>'),
            ('\'auth\': ["REAL_SECRET", "b"] z', "'auth': <redacted>"),
            ('token=[ "a]REAL_SECRET" , ["c"] ] after', "token=<redacted>"),  # 引号内的 ]、嵌套
            ('secret={"a":{"b":"] \\" REAL_SECRET ]"}} end', "secret=<redacted>"),  # 转义引号
            ("token=[1,2] keep", "token=<redacted>"),  # 数字容器也抹（按键豁免，不按值类型豁免）
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
        ids=["open-brace", "open-bracket", "long-multiline-open-brace"],
    )
    def test_unbalanced_containers_are_erased_to_the_end_of_the_input(self, text):
        out = diagdetail.Scrubber().text(text)
        assert "REAL_SECRET" not in out and out.endswith("<redacted>")

    @pytest.mark.parametrize(
        "raw",
        [
            "{'auth': ('user', 'REAL_SECRET')} tail",
            "{'auth': (('a', 'REAL_SECRET'), ('b',))} tail",
            "{'password': ('REAL_SECRET',)} tail",  # 单元素元组
            "{'token': (b'REAL_SECRET', 1)} tail",  # bytes
            "{'token': ('a\\'REAL_SECRET', 2)} tail",  # 转义引号
            "auth=('user', \"REAL_SECRET\") tail",
            "{'token': frozenset({'REAL_SECRET'}), 'n': 1} tail",
            "{'token': deque(['REAL_SECRET']), 'n': 1} tail",
        ],
    )
    def test_tuples_and_other_openers(self, raw):
        out = diagdetail.Scrubber().text(raw + "\nnext line")
        assert "REAL_SECRET" not in out and "<redacted>" in out and out.endswith("\nnext line"), out

    def test_empty_and_null_tuples_are_kept_numbers_are_not(self):
        s = diagdetail.Scrubber()
        assert s.text("token=() x") == "token=() x"
        assert s.text("token=(None, False) x") == "token=(None, False) x"
        assert "2" not in s.text("token=(1, 2.5, None) x")

    def test_text_around_the_segment_is_preserved(self):
        # fail-closed：含秘密键的那一行，键之前的内容保留，键之后到行尾一并抹掉（规则文档写明的牺牲）；下一行不受影响
        out = diagdetail.Scrubber().text('a {"token":["S1"], "ok": "keep-me"} b\nnext line')
        assert out == 'a {"token":<redacted>\nnext line'

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


class TestUnicodeSeparatorsAndShortKeywords:
    @pytest.mark.parametrize(
        "line",
        [
            "token：REAL_S tail",  # 全角冒号
            "token＝REAL_S tail",  # 全角等号
            "token\u00a0:\u00a0REAL_S tail",  # NBSP
            "token\u3000=\u3000REAL_S tail",  # 全角空格
            "password\u2009:\u2009REAL_S tail",  # 细空格
            "密码：REAL_S tail",
            "口令 = REAL_S tail",
            "pw=REAL_S tail",
            "db_pin: REAL_S tail",
            "OTP_CODE=REAL_S tail",
            "dsn = REAL_S tail",
            "mnemonic: REAL_S tail",
            "seed_phrase=REAL_S tail",
            "connection_string=REAL_S tail",
            "DATABASE_URL=REAL_S tail",
        ],
    )
    def test_the_value_is_gone(self, line):
        out = diagdetail.Scrubber().text(line)
        assert "REAL_S" not in out and "<redacted>" in out, out

    def test_short_keywords_only_match_as_whole_words(self):
        s = diagdetail.Scrubber()
        innocent = (
            "pine=1 pinyin: hello spin=3 mapping: foo happin=5 pins: ok opting: x dsnap=1 pwned: no"
            " compass=1 bypass: x passenger=2 passes: ok passed=3"
        )
        assert s.text(innocent) == innocent
        assert not diagdetail.is_secret_key("pine") and not diagdetail.is_secret_key("pinyin")
        assert diagdetail.is_secret_key("pw") and diagdetail.is_secret_key("userPin")
        assert diagdetail.is_secret_key("DB_DSN") and diagdetail.is_secret_key("database_url")
        assert diagdetail.is_secret_key("DB_PASS") and diagdetail.is_secret_key("smtpPass")
        assert not diagdetail.is_secret_key("compass") and not diagdetail.is_secret_key("bypass")
        assert not diagdetail.is_secret_key("pass_count")
        out = s.obj({"pine": "keep", "pw": "S", "db-pin": "S", "密码": "S"})
        assert out == {
            "pine": "keep",
            "pw": "<redacted>",
            "db-pin": "<redacted>",
            "密码": "<redacted>",
        }


class TestFailClosedScanner:
    """默认收集的自由文本：秘密键之后「从值起点到行尾」，行尾时括号 / 引号没配平就继续抹，不确定抹到输入末尾。"""

    TRIPLES = [
        "'''VAL\nVAL2\n'''",
        '"""VAL\nVAL2\n"""',
        "b'''VAL\nVAL2'''",
        'rb"""VAL\nVAL2"""',
        "f'''VAL {x}\nVAL2'''",
        "u'''VAL'''",
        "'''VAL it's \"q\" \nVAL2'''",  # 三引号里含单 / 双引号：不能在第一个内部引号处收尾
        '"""VAL "q" \'x\' \nVAL2"""',
    ]

    @pytest.mark.parametrize("key", MATRIX_KEYS)
    def test_triple_quoted_and_multiline_values(self, key):
        full = diagdetail.Scrubber()
        for tq in self.TRIPLES:
            v = tq.replace("VAL2", SECRET + "2").replace("VAL", SECRET)
            for line in (
                "%s=%s tail\nnext" % (key, v),
                "{'%s': %s}\nnext" % (key, v),
                "%s: %s\nnext" % (key, v),
            ):
                out = full.text(line)
                assert SECRET not in out, (key, tq, line, out)
                assert out.endswith("\nnext"), (key, tq, out)  # 三引号闭合后，下一行不受影响

    @pytest.mark.parametrize(
        "text",
        [
            'token=[ "a" ) "REAL_SECRET" ]\nsafe line',  # 括号种类不匹配：结构不确定 → 抹到输入末尾
            "token=({'a': 1)]) REAL_SECRET\nsafe line",
            "token={\n  'a': 1,\n  'b': 'REAL_SECRET'\n",  # 跨行未闭合
            "token='''REAL_SECRET\nmore\nsafe line",  # 三引号未闭合
            'token="REAL_SECRET\nsafe line',  # 引号未闭合
        ],
    )
    def test_uncertain_structure_erases_to_the_end_of_the_input(self, text):
        out = diagdetail.Scrubber().text("pre\n" + text)
        assert out.startswith("pre\n") and "REAL_SECRET" not in out and "safe line" not in out

    def test_a_determinate_value_stops_at_the_end_of_its_line(self):
        out = diagdetail.Scrubber().text(
            "a token=abc, user's x\nsafe line\nb password: it's REAL fine\nsafe 2"
        )
        assert out == "a token=<redacted>\nsafe line\nb password: <redacted>\nsafe 2"

    @pytest.mark.parametrize(
        ("url", "gone"),
        [
            ("https://u:" + "p" * 6000 + "REAL_SECRET@h.example/x tail", "REAL_SECRET"),
            ("https://a@b:REAL_SECRET@c.example/p tail", "REAL_SECRET"),
            ("postgres://user:pa@ss:REAL_SECRET@db/x?x=1 tail", "REAL_SECRET"),
            ("see ftp://REAL_SECRET@host/f and more", "REAL_SECRET"),
        ],
        ids=["long-password", "multiple-at", "at-in-password", "user-only"],
    )
    def test_url_userinfo_has_no_length_window(self, url, gone):
        out = diagdetail.Scrubber().text(url)
        assert gone not in out and "<redacted>@" in out and out.endswith(("tail", "more")), out

    def test_scanner_inputs_stay_linear(self):
        full = diagdetail.Scrubber()
        blobs = [
            "token='''" * 100_000,
            "token=(] " * 100_000,
            "token=" + "([{" * 100_000,
            "https://" + "u@" * 200_000,
            "a://" * 200_000,
            "token='" + "\\\n" * 100_000,
        ]
        for blob in blobs:
            t0 = time.monotonic()
            full.text(blob)
            assert time.monotonic() - t0 < 5, blob[:12]


class TestProbeRunsInTheRenderWorkingDirectory:
    def _run(self, monkeypatch, mode, tmp_path, script="sub/fig.py"):
        from tavotto.engine import workdir

        (tmp_path / "sub").mkdir(exist_ok=True)
        (tmp_path / "sub" / "fig.py").write_text("x", encoding="utf-8")
        seen = {}
        monkeypatch.setattr(workdir, "mode_for", lambda fd: mode)
        monkeypatch.setattr(pool, "resolve_worker_python", lambda d, **k: ("/py", "bundled"))
        monkeypatch.setattr(
            diagdetail,
            "_probe_subprocess",
            lambda py, b, cwd=None, env_extra=None: seen.update(cwd=cwd) or {"status": "ok"},
        )
        src = diagdetail.Sources(
            project={"figures_dir": str(tmp_path)},
            frontend={"overrides": [{"panel": "p", "script": script}]},
        )
        items = diagdetail.collect_render_env(
            src, diagdetail.DetailOptions(), diagdetail.Scrubber()
        )
        return seen["cwd"], json.loads(items[0].data)

    def test_modes(self, monkeypatch, tmp_path):
        cwd, doc = self._run(monkeypatch, "project_root", tmp_path)
        assert cwd == str(tmp_path) and doc["probe_cwd"]["mode"] == "project_root"
        cwd, doc = self._run(monkeypatch, "project", tmp_path)
        assert cwd == str(tmp_path / "sub") and doc["probe_cwd"]["mode"] == "project"
        cwd, doc = self._run(monkeypatch, "sandbox", tmp_path)
        assert cwd and cwd != str(tmp_path)
        assert doc["probe_cwd"]["cwd"] == "<empty temporary directory>"
        cwd, doc = self._run(monkeypatch, "weird", tmp_path)
        assert cwd is None and doc["probe_cwd"] == {"mode": "unknown", "cwd": None}


class TestScriptEncodings:
    def _collect(self, proj, names):
        src = diagdetail.Sources(project={"figures_dir": str(proj)}, scripts=names)
        opts = diagdetail.DetailOptions(source=True)
        items = diagdetail.collect_source(src, opts, diagdetail.Scrubber())
        index = json.loads(next(i for i in items if i.path == "index.json").data)
        return items, {r["script"]: r for r in index["scripts"]}

    def test_written_utf8_scripts_declare_utf8(self, world):
        """按声明的编码解码后写出的是 UTF-8：前两行的 `coding:` 声明改成 utf-8，解压后读回来内容一致。"""
        import io
        import tokenize

        proj = world["tmp"] / "projdecl"
        proj.mkdir()
        cases = {
            "latin.py": ("# -*- coding: latin-1 -*-\nname = 'caf\xe9'\n", "latin-1"),
            "gbk.py": (
                "#!/usr/bin/env python\n# vim: set fileencoding=gbk :\ntitle = '中文标题'\n",
                "gbk",
            ),
            "same.py": ("# coding: utf-8\nx = 'é'\n", "utf-8"),
            "late.py": ("x = 1\n# coding: latin-1\ny = 'caf\xe9'\n", "utf-8"),  # 第 3 行的不是声明
            "bad.py": ("# coding: no-such-codec\nx = 'é'\n", "utf-8"),
        }
        for name, (text, enc) in cases.items():
            codec = "utf-8" if enc == "utf-8" else enc
            try:
                raw = text.encode(codec)
            except LookupError:
                raw = text.encode("utf-8")
            (proj / name).write_bytes(raw)
        items, rows = self._collect(proj, list(cases))
        body = {i.path.split("-", 1)[1]: i.data for i in items if i.path != "index.json"}
        for name in ("latin.py", "gbk.py", "same.py", "bad.py"):
            enc, _ = tokenize.detect_encoding(io.BytesIO(body[name]).readline)
            assert enc == "utf-8", (name, body[name][:80])
            assert body[name].decode("utf-8")  # 能按 utf-8 读回
        assert "caf\xe9" in body["latin.py"].decode("utf-8")
        assert "中文标题" in body["gbk.py"].decode("utf-8")
        assert b"vim: set fileencoding=utf-8 :" in body["gbk.py"]
        assert b"# coding: latin-1" in body["late.py"]  # 不是声明的行不动
        assert rows["latin.py"]["encoding"] == "iso-8859-1" and rows["gbk.py"]["encoding"] == "gbk"

    def test_declared_encodings_are_decoded_and_still_scrubbed(self, world):
        proj = world["tmp"] / "projenc"
        proj.mkdir()
        latin = "# -*- coding: latin-1 -*-\n# api_key=REAL_L1\nname = 'caf\xe9'\n"
        (proj / "latin.py").write_bytes(latin.encode("latin-1"))
        gbk = "# coding: gbk\n# token=REAL_GBK\ntitle = '中文标题'\n"
        (proj / "gbk.py").write_bytes(gbk.encode("gbk"))
        (proj / "bom.py").write_bytes(b"\xef\xbb\xbf# password=REAL_BOM\nx = 'ok'\n")
        (proj / "bad.py").write_bytes(b"# coding: no-such-codec\n# api_key=REAL_BAD\nx = 1\n")
        items, rows = self._collect(proj, ["latin.py", "gbk.py", "bom.py", "bad.py"])
        body = {
            i.path.split("-", 1)[1]: i.data.decode("utf-8") for i in items if i.path != "index.json"
        }
        assert "caf\xe9" in body["latin.py"] and "中文标题" in body["gbk.py"]
        assert "\ufeff" not in body["bom.py"] and "x = 'ok'" in body["bom.py"]
        for name, secret in (
            ("latin.py", "REAL_L1"),
            ("gbk.py", "REAL_GBK"),
            ("bom.py", "REAL_BOM"),
            ("bad.py", "REAL_BAD"),
        ):
            assert secret not in body[name], name
        assert rows["latin.py"]["encoding"] == "iso-8859-1" and rows["gbk.py"]["encoding"] == "gbk"
        assert rows["bad.py"].get("encoding_fallback") is True
        assert not rows["latin.py"].get("encoding_fallback")


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


class TestDeclarationsMatchTheFinalContents:
    """manifest / 内层 index / prepare 响应 / 确认框：每一处声明都从最终保留下来的条目推导。"""

    def test_more_than_the_source_cap_are_all_listed_and_counted(self, world, monkeypatch):
        proj = world["tmp"] / "projcap"
        proj.mkdir()
        names = []
        for i in range(diagdetail.SOURCE_FILES + 3):
            (proj / f"s{i:03d}.py").write_text(f"# {i}\n" + "x = 1\n" * 10, encoding="utf-8")
            names.append(f"s{i:03d}.py")
        total = sum((proj / n).stat().st_size for n in names)
        src = diagdetail.Sources(project={"figures_dir": str(proj)}, scripts=names)
        items = diagdetail.collect_source(
            src, diagdetail.DetailOptions(source=True), diagdetail.Scrubber()
        )
        index = json.loads(next(i for i in items if i.path == "index.json").data)
        rows = index["scripts"]
        assert len(rows) == len(names)  # index 列全
        capped = [r for r in rows if r.get("skipped_over_count_cap")]
        assert len(capped) == 3 and all("file" not in r for r in capped)
        flat = dict(diagdetail.entries(items))
        with zipfile.ZipFile(io.BytesIO(flat["optional-source.zip"])) as z:
            members = set(z.namelist()) - {"index.json"}
        assert (
            members == {r["file"] for r in rows if "file" in r}
            and len(members) == diagdetail.SOURCE_FILES
        )
        _d, _k, trunc = diagdetail.fit(items, lambda its, t: b"x", 10**9)
        row = next(t for t in trunc if t["kind"] == "source")
        assert (
            total <= row["original_bytes"] <= total + 32768
            and row["kept_bytes"] < row["original_bytes"]
        )

    def test_other_count_caps_are_recorded_the_same_way(self, world, monkeypatch):
        state = world["state"]
        for i in range(5):
            (state / f"{layoutsession.CORRUPT_PREFIX}c{i}.json").write_text(
                "c" * 50, encoding="utf-8"
            )
        items = diagdetail.collect_corrupt_sessions(
            diagdetail.Sources(),
            diagdetail.DetailOptions(corrupt_sessions=True),
            diagdetail.Scrubber(),
        )
        rows = json.loads(items[0].data)["files"]
        assert (
            sum(1 for r in rows if r.get("skipped_over_count_cap")) >= 3
            and items[0].dropped_bytes > 0
        )
        # 崩溃报告的数量上限（与字节份额分开写）
        monkeypatch.setattr(diagdetail, "CRASH_FILES", 2)
        for i in range(4):
            (world["crash"] / f"Tavotto.exe.{i}.dmp").write_bytes(bytes([65 + i]) * 100)
        citems = diagdetail.collect_crash_reports(
            diagdetail.Sources(),
            diagdetail.DetailOptions(crash_reports=True),
            diagdetail.Scrubber(),
        )
        crows = json.loads(citems[0].data)["reports"]
        assert sum(1 for r in crows if r["content"] == "skipped_over_count_cap") >= 2
        assert citems[0].dropped_bytes >= 200
        # 面板数量上限
        panels = [{"panel": f"p{i}.pdf", "overrides": []} for i in range(3)]
        oitems = diagdetail.collect_overrides(
            diagdetail.Sources(frontend={"overrides": panels, "overrides_omitted": 2}),
            diagdetail.DetailOptions(),
            diagdetail.Scrubber(),
        )
        assert json.loads(oitems[0].data)["skipped_over_count_cap"] == 2
        # worker 日志份数上限
        logs = []
        for i in range(3):
            w = world["tmp"] / f"w{i}.log"
            w.write_text(f"worker {i}\n" * 20, encoding="utf-8")
            logs.append((time.time() - i, w))
        monkeypatch.setattr(diagnostics, "owned_worker_logs", lambda project_dir, root=None: logs)
        monkeypatch.setattr(diagdetail, "WORKER_LOG_FILES", 1)
        litems = diagdetail.collect_logs(
            diagdetail.Sources(project={"figures_dir": "/p"}),
            diagdetail.DetailOptions(),
            diagdetail.Scrubber(),
        )
        wl = next(i for i in litems if i.path == "detail-worker-log.txt")
        assert b"2 older worker.log file(s) omitted" in wl.data and wl.dropped_bytes > 0

    def test_options_follow_the_final_contents_when_fit_drops_an_attachment(
        self, client, world, monkeypatch
    ):
        proj = _project(client, world["tmp"])
        (proj / "fig_one.py").write_text(
            "# " + base64.b64encode(os.urandom(300_000)).decode(), encoding="utf-8"
        )
        monkeypatch.setattr(diagdetail, "FIT_SLACK", 1000)
        base, _z = _prepare(client)
        # 上限只够默认内容：勾选的源码一定被 fit 整类丢掉
        monkeypatch.setattr(diagsend, "MAX_BYTES", base["size"] + 1000 + 600)
        prep, z = _prepare(client, source=True)
        manifest = json.loads(z.read("manifest.json"))
        assert "optional-source.zip" not in z.namelist()
        assert manifest["detail_options"]["source"] is False and prep["options"]["source"] is False
        row = next(t for t in manifest["truncated"] if t["kind"] == "source")
        assert row["kept_bytes"] == 0 and row["original_bytes"] > 100_000
        assert prep["truncated"] == manifest["truncated"]
        assert (
            "script source"
            not in z.read("README.txt").decode().split("Extras you ticked:")[1].split(".")[0]
        )

    def test_options_are_true_only_for_attachments_actually_present(self, client, world):
        _prep, z = _prepare(client, source=True, crash_reports=True)
        m = json.loads(z.read("manifest.json"))["detail_options"]
        assert m == {"source": True, "crash_reports": True, "corrupt_sessions": False}
        assert diagdetail.options_of([]) == {
            "source": False,
            "crash_reports": False,
            "corrupt_sessions": False,
        }


def _long_traceback(frames: int, canary: str = "ORDER_SRC") -> str:
    lines = ["Traceback (most recent call last):"]
    for i in range(frames):
        lines.append(f'  File "/p/mod{i}.py", line {i + 1}, in fn{i}')
        lines.append(f"    do_secret_thing_{i}()  # {canary}_{i}")
        lines.append("    ^^^^^^^^^^^^^^^^^^^^^^^^")
    lines.append("RuntimeError: deep")
    return "\n".join(lines) + "\n"


class TestStripBeforeTruncate:
    """所有截断（_cap_middle、按字节读尾巴、按份额截）都排在源码剥离之后。"""

    def test_a_record_longer_than_the_cap_leaks_no_source_in_errors_and_runs(
        self, client, world, monkeypatch
    ):
        tb = _long_traceback(800)
        assert len(tb) > 2 * diagdetail.ERROR_RECORD_CHARS
        app_log = world["tmp"] / "long_app.log"
        app_log.write_text(
            "2026-10-13 10:00:00,000 ERROR tavotto: 渲染失败 export\n" + tb, encoding="utf-8"
        )
        monkeypatch.setattr(diagnostics, "_app_log_path", lambda: app_log)
        _prep, z = _prepare(client)
        for name, text in _texts(z).items():
            assert "ORDER_SRC" not in text, name
        errors = json.loads(z.read("detail-errors.json"))
        assert "omitted" in errors["engine"][-1]["text"]  # 反证：确实被截断了
        assert 'File "/p/mod0.py", line 1' in errors["engine"][-1]["text"]
        _prep, z = _prepare(client, source=True)  # 勾了：保留源码
        assert "ORDER_SRC" in z.read("detail-errors.json").decode()

    @pytest.mark.parametrize("limit", [2000, 3333, 4097, 6001])
    def test_log_tails_that_start_in_the_middle_of_a_frame_leak_no_source(
        self, client, world, monkeypatch, limit
    ):
        tb = _long_traceback(300)
        tmp = world["tmp"]
        for name in ("app", "sidecar", "worker"):
            (tmp / f"{name}.log").write_text(tb, encoding="utf-8")
        app_log = tmp / "app.log"
        monkeypatch.setattr(diagnostics, "_app_log_path", lambda: app_log)
        monkeypatch.setattr(diagdetail, "sidecar_log_file", lambda: tmp / "sidecar.log")
        monkeypatch.setattr(
            diagnostics,
            "owned_worker_logs",
            lambda project_dir, root=None: [(time.time(), tmp / "worker.log")],
        )
        for const in ("APP_LOG_TAIL", "SIDECAR_LOG_TAIL", "WORKER_LOG_TAIL"):
            monkeypatch.setattr(diagdetail, const, limit)
        _prep, z = _prepare(client)
        for name in ("detail-app-log.txt", "detail-sidecar-log.txt", "detail-worker-log.txt"):
            text = z.read(name).decode()
            assert "ORDER_SRC" not in text and "do_secret_thing" not in text, (name, limit)
            assert 'File "/p/mod' in text  # 反证：栈的帧行还在

    def test_uncertain_start_drops_indented_lines_until_the_first_boundary(self):
        frag = (
            "    do_secret_thing()  # FRAG_SRC\n"
            "    ^^^^\n"
            '  File "/p/a.py", line 3, in f\n'
            "    other()  # FRAG_SRC2\n"
            "ValueError: x\n"
            "    indented log text after the block stays\n"
        )
        out = diagdetail.strip_source_lines(frag, uncertain_start=True)
        assert "FRAG_SRC" not in out and 'File "/p/a.py", line 3, in f' in out
        assert "ValueError: x" in out and "indented log text after the block stays" in out
        # 起点确定（整个文件从头读）时，开头的缩进行是正文，不动
        assert (
            "FRAG_SRC"
            in diagdetail.strip_source_lines(frag, uncertain_start=False).split("File")[0]
        )

    def test_tail_text_drops_the_partial_first_line(self, tmp_path):
        f = tmp_path / "t.log"
        f.write_bytes(b"line one\nline two\nline three\n")  # 字节写，不吃平台换行转换
        text, size, mid = diagdetail._tail_text(f, 14)
        assert mid and text == "line three\n"
        text, size, mid = diagdetail._tail_text(f, 1000)
        assert not mid and text.startswith("line one")


class TestNativeProbeEnvironment:
    class Spec:
        env_source = "sidecar_inherited"
        env = {
            "MPLBACKEND": "agg",
            "MPLCONFIGDIR": "/cfg",
            "AWS_SECRET": "must-not-pass",
            "PATH": "/x",
        }

    def _session(self, spec):
        s = TestNativePanelRenderEnv.FakeSession()
        s.spec = spec
        return s

    def _doc(self, monkeypatch, spec):
        seen = {}
        test = TestNativePanelRenderEnv()
        from tavotto.engine import enginesession, nativesession, runtimeasset

        monkeypatch.setattr(runtimeasset, "is_runtime_id", lambda f: str(f).startswith("runtime:"))
        monkeypatch.setattr(enginesession, "profile_of", lambda root, fid: "native")
        monkeypatch.setattr(
            runtimeasset, "resolve", lambda fid, reg: {"script": "s.py", "stem": "fig"}
        )
        monkeypatch.setattr(
            nativesession.REGISTRY, "route_for", lambda r, sc, st: self._session(spec)
        )
        full_probe = {
            "status": "ok",
            "python": {"version": "3.11"},
            "packages": [{"name": "matplotlib", "version": "3.9"}],
            "matplotlib": {
                "version": "3.9",
                "backend": "agg",
                "matplotlibrc": "/x/matplotlibrc",
                "configdir": "/c",
                "cachedir": "/k",
                "non_default_rcparams": {"a": "b"},
                "fonts": [{"family": "X"}],
                "font_count": 3,
            },
        }
        monkeypatch.setattr(
            diagdetail,
            "_probe_subprocess",
            lambda py, b, cwd=None, env_extra=None: (
                seen.update(env=env_extra) or json.loads(json.dumps(full_probe))
            ),
        )
        items = diagdetail.collect_render_env(
            test._src(object()), diagdetail.DetailOptions(), diagdetail.Scrubber()
        )
        return seen["env"], json.loads(items[0].data)

    def test_desktop_owned_session_reproduces_only_the_render_related_variables(self, monkeypatch):
        env, doc = self._doc(monkeypatch, self.Spec())
        assert env == {"MPLBACKEND": "agg", "MPLCONFIGDIR": "/cfg"}  # 不带 AWS_SECRET / PATH
        assert doc["probe_env"] == {
            "source": "launch_spec",
            "keys": ["MPLBACKEND", "MPLCONFIGDIR"],
            "not_applied": [],
        }
        assert doc["probe"]["matplotlib"]["backend"] == "agg"

    def test_unknown_launch_environment_marks_the_env_dependent_dimensions_unavailable(
        self, monkeypatch
    ):
        env, doc = self._doc(monkeypatch, None)  # 终端拥有的会话：没有登记启动环境
        assert env is None and doc["probe_env"]["source"] == "unknown"
        mpl = doc["probe"]["matplotlib"]
        for k in (
            "matplotlibrc",
            "backend",
            "non_default_rcparams",
            "fonts",
            "configdir",
            "cachedir",
            "font_count",
        ):
            assert mpl[k] == {"unavailable": "launch_env_unknown"}, k
        assert (
            mpl["version"] == "3.9" and doc["probe"]["packages"][0]["name"] == "matplotlib"
        )  # 与环境无关的事实保留
        assert doc["probe"]["python"]["version"] == "3.11"


class TestProbeNeverExecutesUserDirectories:
    """探针在「打开对话框」这一刻跑：cwd 是项目 / 脚本目录，但项目里的 json.py / platform.py / matplotlib.py
    绝不能被 import 执行（绕过 worker 的文件系统守卫）。"""

    def _evil_dir(self, tmp_path, name):
        d = tmp_path / name
        d.mkdir()
        marker = tmp_path / f"{name}-marker"
        body = f"open({str(marker)!r}, 'w').write('executed')\n"
        for mod in (
            "json",
            "platform",
            "matplotlib",
            "importlib",
            "sitecustomize",
            "usercustomize",
        ):
            (d / f"{mod}.py").write_text(body, encoding="utf-8")
        return d, marker

    def _python(self):
        py = pool.find_worker_python()
        return py, pool.source_of(py) == pool.SOURCE_BUNDLED

    @pytest.mark.parametrize("mode", ["project_root", "project", "sandbox"])
    def test_the_three_workdir_modes(self, mode, monkeypatch, tmp_path):
        from tavotto.engine import workdir

        proj, marker = self._evil_dir(tmp_path, "proj")
        (proj / "sub").mkdir()
        (proj / "sub" / "fig.py").write_text("x", encoding="utf-8")
        for mod in ("json", "platform", "matplotlib"):  # 脚本目录里也放一份
            (proj / "sub" / f"{mod}.py").write_text(
                f"open({str(marker)!r}, 'w').write('executed-sub')\n", encoding="utf-8"
            )
        py, bundled = self._python()
        monkeypatch.setattr(workdir, "mode_for", lambda fd: mode)
        monkeypatch.setattr(pool, "resolve_worker_python", lambda d, **k: (py, pool.source_of(py)))
        monkeypatch.setattr(diagdetail, "_probe_subprocess", REAL_PROBE)
        src = diagdetail.Sources(
            project={"figures_dir": str(proj)},
            frontend={"overrides": [{"panel": "p", "script": "sub/fig.py"}]},
        )
        items = diagdetail.collect_render_env(
            src, diagdetail.DetailOptions(), diagdetail.Scrubber()
        )
        doc = json.loads(items[0].data)
        assert not marker.exists(), "项目里的模块在探针里被执行了"
        assert doc["probe"]["status"] == "ok" and doc["probe"]["matplotlib"]["version"]

    def test_native_session_launch_environment_cannot_put_user_dirs_on_the_import_path(
        self, monkeypatch, tmp_path
    ):
        evil, marker = self._evil_dir(tmp_path, "evilpath")
        py, _bundled = self._python()

        class Spec:
            env_source = "sidecar_inherited"
            env = {"PYTHONPATH": str(evil), "PYTHONHOME": str(evil), "MPLBACKEND": "agg"}

        sess = TestNativePanelRenderEnv.FakeSession()
        sess.interpreter = py
        sess.cwd = str(evil)  # 连 cwd 也是恶意目录
        sess.spec = Spec()
        assert diagdetail._native_launch_env(sess) == {"MPLBACKEND": "agg"}
        out = REAL_PROBE(py, False, str(evil), diagdetail._native_launch_env(sess))
        assert out["status"] == "ok" and not marker.exists()

    @pytest.mark.parametrize("var", ["PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME", "PYTHONUSERBASE"])
    def test_inherited_python_env_cannot_run_user_code_in_the_probe(
        self, monkeypatch, tmp_path, var
    ):
        """Flask 进程自己的环境（`PYTHONPATH` 指向项目 / `PYTHONSTARTUP` 指向脚本）不能被探针继承。"""
        py, _b = self._python()  # 先解析解释器：这一步自己也会起子进程，不属于被测的探针
        evil, marker = self._evil_dir(tmp_path, "inherit")
        startup = tmp_path / "startup.py"
        startup.write_text(
            f"open({str(tmp_path / 'startup-marker')!r}, 'w').write('x')\n", encoding="utf-8"
        )
        monkeypatch.setenv("PYTHONPATH", str(evil))
        monkeypatch.setenv("PYTHONSTARTUP", str(startup))
        monkeypatch.setenv("__PYVENV_LAUNCHER__", str(evil / "nope"))
        monkeypatch.setenv(var, str(evil) if var != "PYTHONSTARTUP" else str(startup))
        seen = {}
        real_run = subprocess.run

        def spy(cmd, **kw):
            seen["cmd"], seen["env"] = cmd, kw.get("env")
            return real_run(cmd, **kw)

        monkeypatch.setattr(subprocess, "run", spy)
        out = REAL_PROBE(py, False, str(tmp_path))
        assert out["status"] == "ok"
        assert not marker.exists() and not (tmp_path / "startup-marker").exists()
        assert "-E" in seen["cmd"]
        assert not [
            k
            for k in seen["env"]
            if k.startswith("PYTHON") and k not in diagdetail._PROBE_KEEP_PYTHON_ENV
        ]
        assert "__PYVENV_LAUNCHER__" not in seen["env"]

    def test_probe_still_works_with_dash_E_alone(self, monkeypatch, tmp_path):
        """环境清理是第一道、`-E` 是第二道：只剩 `-E` 时（env 里被塞回 PYTHONPATH）恶意模块也不执行。"""
        evil, marker = self._evil_dir(tmp_path, "dashE")
        monkeypatch.setattr(
            diagdetail, "_probe_base_env", lambda base: {**os.environ, "PYTHONPATH": str(evil)}
        )
        py, _b = self._python()
        assert REAL_PROBE(py, False, str(tmp_path))["status"] == "ok" and not marker.exists()

    def test_the_probe_is_launched_from_a_trusted_script_not_dash_c(self, monkeypatch, tmp_path):
        seen = {}
        real_run = subprocess.run

        def spy(cmd, **kw):
            seen["cmd"], seen["env"], seen["cwd"] = cmd, kw.get("env"), kw.get("cwd")
            seen["script_exists"] = Path(cmd[-1]).is_file()
            return real_run(cmd, **kw)

        monkeypatch.setattr(subprocess, "run", spy)
        py, _b = self._python()
        out = REAL_PROBE(py, False, str(tmp_path))
        assert out["status"] == "ok"
        assert "-c" not in seen["cmd"] and "-s" in seen["cmd"] and seen["script_exists"]
        assert Path(seen["cmd"][-1]).parent != tmp_path  # 脚本在可信临时目录里，不在 cwd
        assert seen["env"]["PYTHONSAFEPATH"] == "1" and seen["cwd"] == str(tmp_path)
        assert not Path(seen["cmd"][-1]).exists()  # 跑完就删


class TestAnsiColouredTracebacks:
    COLOURED = (
        "Traceback \x1b[1;35m(most recent call last)\x1b[0m:\n"
        '  File \x1b[35m"/p/a.py"\x1b[0m, line \x1b[35m3\x1b[0m, in \x1b[35mf\x1b[0m\n'
        "    \x1b[1;31mboom_COLOR_SRC()\x1b[0m\n"
        "    \x1b[1;31m^^^^^^^^^^^^^^^\x1b[0m\n"
        "\x1b[1;35mValueError\x1b[0m: x\n"
    )
    SYNTAX_COLOUR = (
        '  File \x1b[35m"/p/b.py"\x1b[0m, line \x1b[35m2\x1b[0m\n'
        "    x = (COLOR_SYNTAX_SRC\n"
        "         \x1b[1;31m^\x1b[0m\n"
        "\x1b[1;35mSyntaxError\x1b[0m: '(' was never closed\n"
    )
    SYNTAX_PLAIN = "  File \"/p/b.py\", line 2\n    x = (PLAIN_SYNTAX_SRC\n         ^\nSyntaxError: '(' was never closed\n"

    @pytest.mark.parametrize("text", [COLOURED, SYNTAX_COLOUR, SYNTAX_PLAIN])
    def test_no_source_remains_and_the_output_is_uncoloured(self, text):
        out = diagdetail.strip_source_lines(text)
        assert "_SRC" not in out and "\x1b" not in out
        assert "File" in out and ("ValueError: x" in out or "SyntaxError" in out)

    def test_end_to_end_in_the_log_tails(self, client, world, monkeypatch):
        tmp = world["tmp"]
        (tmp / "col.log").write_text(
            "2026-10-13 10:00:00,000 ERROR tavotto: x\n" + self.COLOURED + self.SYNTAX_COLOUR,
            encoding="utf-8",
        )
        monkeypatch.setattr(diagnostics, "_app_log_path", lambda: tmp / "col.log")
        (tmp / "sidecar.log").write_text(self.SYNTAX_COLOUR + self.SYNTAX_PLAIN, encoding="utf-8")
        _prep, z = _prepare(client)
        for name, text in _texts(z).items():
            assert "_SRC" not in text, name


class TestCrashMemberNamesAreUnique:
    def test_the_same_wer_report_in_queue_and_archive_gets_distinct_member_names(
        self, world, monkeypatch, tmp_path
    ):
        archive, queue = tmp_path / "archive", tmp_path / "queue"
        for base in (archive, queue):
            d = base / "AppCrash_Tavotto.exe_abc"
            d.mkdir(parents=True)
            (d / "Report.wer").write_text(f"report from {base.name}", encoding="utf-8")
        monkeypatch.setattr(
            diagdetail,
            "crash_dirs",
            lambda: [("windows_wer_user_archive", archive), ("windows_wer_machine_queue", queue)],
        )
        items = diagdetail.collect_crash_reports(
            diagdetail.Sources(),
            diagdetail.DetailOptions(crash_reports=True),
            diagdetail.Scrubber(),
        )
        names = [i.path for i in items if i.path != "index.json"]
        assert len(names) == len(set(names)) == 2
        assert any(n.startswith("user-archive-") for n in names) and any(
            n.startswith("machine-queue-") for n in names
        )
        rows = json.loads(items[0].data)["reports"]
        assert sorted(r["file"] for r in rows) == sorted(names)
        flat = dict(diagdetail.entries(items))
        with zipfile.ZipFile(io.BytesIO(flat["optional-crash-reports.zip"])) as z:
            assert len(z.namelist()) == len(set(z.namelist())) == 3

    def test_identical_member_names_get_numeric_suffixes(self, world, monkeypatch, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        for base in (a, b):
            base.mkdir()
            (base / "Tavotto-2026-10-13-120000.ips").write_text(base.name, encoding="utf-8")
        monkeypatch.setattr(
            diagdetail,
            "crash_dirs",
            lambda: [("macos_diagnostic_reports", a), ("macos_diagnostic_reports", b)],
        )
        items = diagdetail.collect_crash_reports(
            diagdetail.Sources(),
            diagdetail.DetailOptions(crash_reports=True),
            diagdetail.Scrubber(),
        )
        names = sorted(i.path for i in items if i.path != "index.json")
        assert names == ["Tavotto-2026-10-13-120000-2.ips", "Tavotto-2026-10-13-120000.ips"]
        rows = json.loads(items[0].data)["reports"]
        assert sorted(r["file"] for r in rows) == names


class TestProbeDirectoryCleanup:
    def test_rmtree_retries_a_bounded_number_of_times(self, tmp_path, monkeypatch):
        import shutil

        d = tmp_path / "x"
        d.mkdir()
        calls = {"n": 0}
        real = shutil.rmtree

        def flaky(path, *a, **k):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("busy")  # Windows：杀软 / 索引器短暂占着
            return real(path, *a, **k)

        monkeypatch.setattr(shutil, "rmtree", flaky)
        assert diagdetail._rmtree_with_retry(d, attempts=3, delay=0) is True
        assert calls["n"] == 3 and not d.exists()

    def test_a_failure_that_never_goes_away_is_recorded_and_logged(
        self, tmp_path, monkeypatch, caplog
    ):
        import shutil

        monkeypatch.setattr(
            shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(PermissionError("busy"))
        )
        monkeypatch.setattr(diagdetail, "PROBE_RMTREE_ATTEMPTS", 2)
        monkeypatch.setattr(diagdetail, "_probe_in", lambda *a, **k: {"status": "ok"})
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        import logging

        with caplog.at_level(logging.WARNING, logger="tavotto.diagdetail"):
            out = REAL_PROBE("/py", False)
        assert out["status"] == "ok" and out["probe_cleanup"] == "failed"
        assert any("探针临时目录" in r.getMessage() for r in caplog.records)

    def test_cleanup_failure_is_hoisted_into_the_render_env_document(self, monkeypatch):
        monkeypatch.setattr(pool, "resolve_worker_python", lambda d, **k: ("/py", "bundled"))
        monkeypatch.setattr(
            diagdetail,
            "_probe_subprocess",
            lambda py, b, cwd=None, env_extra=None: {"status": "ok", "probe_cleanup": "failed"},
        )
        items = diagdetail.collect_render_env(
            diagdetail.Sources(project={"figures_dir": "/p"}),
            diagdetail.DetailOptions(),
            diagdetail.Scrubber(),
        )
        assert json.loads(items[0].data)["probe_cleanup"] == "failed"

    def test_the_next_probe_sweeps_only_our_own_old_private_leftovers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        now = time.time()

        def mk(name, age, mode=0o700):
            d = tmp_path / name
            d.mkdir()
            (d / "probe_main.py").write_text("x", encoding="utf-8")
            os.chmod(d, mode)
            os.utime(d, (now - age, now - age))
            return d

        old = mk("tavotto-probe-script-old", 2 * 3600)
        fresh = mk("tavotto-probe-script-fresh", 60)
        open_ = mk("tavotto-probe-script-open", 2 * 3600, 0o755)
        other = mk("tavotto-something-else", 2 * 3600)
        assert diagdetail._sweep_probe_leftovers(now) == (1 if os.name != "nt" else 2)
        assert not old.exists()
        assert fresh.exists() and other.exists()
        if os.name != "nt":
            assert open_.exists()  # 权限不是 0700：不是我们建的，不碰


class TestNativeCwdUnavailable:
    def _doc(self, monkeypatch, cwd, configure=None):
        seen = {}
        test = TestNativePanelRenderEnv()
        from tavotto.engine import enginesession, nativesession, runtimeasset

        monkeypatch.setattr(runtimeasset, "is_runtime_id", lambda f: str(f).startswith("runtime:"))
        monkeypatch.setattr(enginesession, "profile_of", lambda root, fid: "native")
        monkeypatch.setattr(
            runtimeasset, "resolve", lambda fid, reg: {"script": "s.py", "stem": "fig"}
        )
        sess = TestNativePanelRenderEnv.FakeSession()
        sess.cwd = cwd
        sess.spec = TestNativeProbeEnvironment.Spec()
        if configure:
            configure(sess)
        monkeypatch.setattr(nativesession.REGISTRY, "route_for", lambda r, sc, st: sess)
        probe = {
            "status": "ok",
            "python": {"version": "3.11"},
            "matplotlib": {
                "version": "3.9",
                "backend": "agg",
                "matplotlibrc": "/x",
                "fonts": [1],
                "configdir": "/c",
            },
        }
        monkeypatch.setattr(
            diagdetail,
            "_probe_subprocess",
            lambda py, b, cwd=None, env_extra=None: (
                seen.update(cwd=cwd) or json.loads(json.dumps(probe))
            ),
        )
        items = diagdetail.collect_render_env(
            test._src(object()), diagdetail.DetailOptions(), diagdetail.Scrubber()
        )
        return seen["cwd"], json.loads(items[0].data)

    def test_a_usable_session_cwd_is_used_and_reported(self, monkeypatch, tmp_path):
        cwd, doc = self._doc(monkeypatch, str(tmp_path))
        shown = diagdetail.Scrubber().text(
            str(tmp_path)
        )  # 文档里的路径已过脱敏（用户名换成 <user>）
        assert cwd == str(tmp_path)
        assert doc["probe_cwd"] == {"mode": "native_session", "cwd": shown}
        assert doc["probe"]["matplotlib"]["backend"] == "agg"

    @pytest.mark.parametrize("which", ["missing", "empty", "file"])
    def test_an_unusable_session_cwd_is_never_reported_as_the_probe_cwd(
        self, monkeypatch, tmp_path, which
    ):
        bad = {"missing": str(tmp_path / "nope"), "empty": "", "file": None}[which]
        if which == "file":
            f = tmp_path / "f.txt"
            f.write_text("x", encoding="utf-8")
            bad = str(f)
        cwd, doc = self._doc(monkeypatch, bad)
        assert cwd and cwd != bad  # 在一个中性的空目录里取版本类事实
        assert (
            doc["probe_cwd"]["cwd"] is None
            and doc["probe_cwd"]["reason"] == "native_cwd_unavailable"
        )
        mpl = doc["probe"]["matplotlib"]
        for k in ("backend", "matplotlibrc", "fonts", "configdir"):
            assert mpl[k] == {"unavailable": "native_cwd_unavailable"}, k
        assert mpl["version"] == "3.9" and doc["probe"]["python"]["version"] == "3.11"


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
        cwd = tempfile.gettempdir()
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
            diagdetail,
            "_probe_subprocess",
            lambda py, b, cwd=None, env_extra=None: seen.update(py=py) or {"status": "ok"},
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
            lambda python, bundled, cwd=None, env_extra=None: (
                seen.update(python=python, bundled=bundled) or {"status": "ok"}
            ),
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
            diagdetail,
            "_probe_subprocess",
            lambda python, bundled, cwd=None, env_extra=None: {"bundled": bundled},
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
            # 按字节写：Windows 上 write_text 会写成 CRLF，读入时规范成 LF，原始字节数就对不上
            (proj / f"s{i}.py").write_bytes((f"# script {i}\n" + "x = 1\n" * 300).encode())
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
            diagdetail,
            "_probe_subprocess",
            lambda p, b, cwd=None, env_extra=None: {"status": "failed", "reason": "timeout"},
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


class TestPemAndYamlBlocks:
    """PEM 的 END 必须与 BEGIN 同一标签；YAML 块标量 / 空值 / 续行的值从下一行才开始。"""

    PEMS = [
        # END 标签不匹配：中间夹的证书 END 不算收尾
        "-----BEGIN PRIVATE KEY-----\nS1\n-----END CERTIFICATE-----\nS2\n-----END PRIVATE KEY-----\nkeep",
        # 嵌套 BEGIN
        "-----BEGIN PRIVATE KEY-----\nS1\n-----BEGIN RSA PRIVATE KEY-----\nS2\n-----END RSA PRIVATE KEY-----\nS3\n-----END PRIVATE KEY-----\nkeep",
        # 标签大小写 / 空白变体
        "-----begin  rsa  private   key-----\nS1\n-----END RSA PRIVATE KEY-----\nkeep",
        "-----BEGIN\tENCRYPTED PRIVATE KEY-----\nS1\n-----end encrypted private key-----\nkeep",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nS1\n-----END OPENSSH PRIVATE KEY-----\nkeep",
        # END 标签始终不配对：抹到输入末尾
        "-----BEGIN EC PRIVATE KEY-----\nS1\n-----END PRIVATE KEY-----\nS2",
    ]

    @pytest.mark.parametrize("text", PEMS)
    def test_pem_blocks(self, text):
        out = diagdetail.Scrubber().text(text)
        assert not re.search(r"S\d", out), out
        if text.endswith("keep"):
            assert out.endswith("keep")

    def test_mismatched_end_erases_to_end_of_input(self):
        out = diagdetail.Scrubber().text(
            "x\n-----BEGIN EC PRIVATE KEY-----\nS1\n-----END PRIVATE KEY-----\nS2"
        )
        assert out == "x\n" + diagdetail._REDACTED

    def test_pem_scan_is_linear(self):
        import time

        t = time.monotonic()
        diagdetail.Scrubber().text(
            "-----BEGIN RSA PRIVATE KEY-----\n" * 4000 + "-----END PRIVATE KEY-----"
        )
        assert time.monotonic() - t < 3

    @pytest.mark.parametrize("key", ["token", "api_key", "password", "client_secret"])
    @pytest.mark.parametrize(
        "block",
        [
            "|\n  S1\n  S2\n",
            ">-\n  S1\n\n  S2\n",
            "|2\n   S1\n",
            "|+ # c\n  S1\n",
            "\n  - S1\n  - S2\n",
            "\n    nested: S1\n",
            "\\\n  S1\n  S2\n",
        ],
    )
    def test_yaml_block_values(self, key, block):
        for indent in ("", "  "):
            first, *rest = block.split("\n")
            body = "\n".join([first, *((indent + ln if ln.strip() else ln) for ln in rest)])
            text = f"top:\n{indent}{key}: {body}{indent}sibling: keep_me\n"
            out = diagdetail.Scrubber().text(text)
            assert not re.search(r"S\d", out), (key, block, out)
            assert f"{indent}sibling: keep_me" in out, out

    def test_yaml_block_ends_at_dedent_and_blank_lines_do_not_end_it(self):
        text = "a:\n  token: |\n    S1\n\n    S2\n  other: ok\nb: fine\n"
        out = diagdetail.Scrubber().text(text)
        assert "S1" not in out and "S2" not in out and "other: ok" in out and "b: fine" in out

    def test_empty_value_without_indented_children_is_kept(self):
        assert diagdetail.Scrubber().text("token:\nnext: ok") == "token:\nnext: ok"


class TestNumbersUnderSecretKeys:
    """秘密键下的数字一律抹；计数 / 配置类字段靠键名放行，不靠值类型放行。"""

    NUMS = [
        "123456",
        "1234",
        "593201",
        "-7",
        "+42",
        "0",
        "00000",
        "3.14159",
        "123456789012345678",
        "1e9",
    ]

    @pytest.mark.parametrize("key", MATRIX_KEYS)
    def test_numeric_values_are_erased_under_every_secret_key(self, key):
        s = diagdetail.Scrubber()
        for n in self.NUMS:
            for line in (
                f"{key}={n}",
                f"{key}: {n}",
                f'"{key}": {n}',
                f"{key}=[{n}]",
                f"{key}=({n}, {n})",
                f'{{"{key}": [{n}, {n}]}}',
            ):
                out = s.text(line)
                assert n not in out, (line, out)
            assert s.obj({key: int(float(n)) if n.lstrip("+-").isdigit() else float(n)}) == {
                key: "<redacted>"
            }
            assert s.obj({key: [int(float(n))]}) == {key: "<redacted>"}

    @pytest.mark.parametrize(
        "key",
        [
            "max_tokens", "token_count", "tokens_used", "token_limit", "token_ttl", "key_size",
            "key_bits", "password_max_len", "maxTokens", "num_tokens", "min_password_length",
            "secret_version", "token_expires", "api_key_index", "session_timeout_seconds_max",
        ],
    )  # fmt: skip
    def test_count_style_keys_are_kept(self, key):
        s = diagdetail.Scrubber()
        assert s.text(f"{key}=4096 x") == f"{key}=4096 x"
        assert s.text(f"{key}: 12") == f"{key}: 12"
        assert s.obj({key: 2048, "n": 1}) == {key: 2048, "n": 1}
        assert s.obj({key: "abc"}) == {key: "abc"}

    @pytest.mark.parametrize(
        "key",
        ["pin_number", "otp_number", "pin_code", "password_value", "password_number", "otp_num",
         "pin_val", "secret_code", "token_admin", "passwordMin_admin", "secret_number"],
    )  # fmt: skip
    def test_credential_names_made_of_a_secret_word_plus_a_look_alike_suffix_are_erased(self, key):
        s = diagdetail.Scrubber()
        assert "1234" not in s.text(f"{key}=1234 x")
        assert "1234" not in s.text(f"{key}: 1234")
        assert s.obj({key: 1234}) == {key: "<redacted>"}

    def test_placeholders_and_nulls_are_still_kept(self):
        s = diagdetail.Scrubber()
        for v in ("true", "false", "null", "None", "<redacted>", "***", "...", "…"):
            assert s.text(f"token={v} x") == f"token={v} x", v
        assert s.text("token= ") == "token= "
        assert s.obj({"password": ""}) == {"password": ""}


NEWLINES = pytest.mark.parametrize("nl", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])


def _nl(text: str, nl: str) -> str:
    return text.replace("\n", nl)


class TestLineEndingsAreNormalisedAtTheEntrance:
    """Windows 的日志 / 脚本是 CRLF（老 Mac 是单独 CR）：所有按行处理的清理与分类在入口规范成 `\\n`，
    在 macOS 上也能复现 Windows 的行为。"""

    TB = (
        "2026-10-13 10:00:00,000 ERROR tavotto: x\n"
        "Traceback (most recent call last):\n"
        '  File "/p/a.py", line 3, in f\n'
        "    LOR_SYNTAX_SRC(password_arg)\n"
        "    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
        "ValueError: x\n"
        'warn.py:7: UserWarning: w\n  WARN_SRC = 1\n  File "/p/b.py", line 2\n    x = (PLAIN_SYNTAX_SRC\n         ^\n'
        "SyntaxError: '(' was never closed\n"
    )

    @NEWLINES
    def test_traceback_source_is_stripped(self, nl):
        out = diagdetail.strip_source_lines(_nl(self.TB, nl))
        assert "_SRC" not in out and "password_arg" not in out
        assert "File" in out and "ValueError: x" in out and "\r" not in out

    @NEWLINES
    def test_uncertain_start_tail_is_stripped(self, nl):
        out = diagdetail.strip_source_lines(
            _nl("    PARTIAL_SRC()\n    ^^^^\nValueError: y\n", nl), uncertain_start=True
        )
        assert "PARTIAL_SRC" not in out and "ValueError: y" in out

    @NEWLINES
    def test_tail_text_drops_the_partial_first_line_and_has_no_cr(self, tmp_path, nl):
        f = tmp_path / "t.log"
        f.write_bytes(_nl("line one\nline two\nline three\n", nl).encode())
        limit = len(_nl("ne two\nline three\n", nl))
        text, _size, mid = diagdetail._tail_text(f, limit)
        assert mid and text == "line three\n"
        text, _size, mid = diagdetail._tail_text(f, 1000)
        assert not mid and text == "line one\nline two\nline three\n"

    @NEWLINES
    def test_end_to_end_in_the_log_tails(self, client, world, monkeypatch, nl):
        tmp = world["tmp"]
        (tmp / "crlf.log").write_bytes(_nl(self.TB, nl).encode())
        monkeypatch.setattr(diagnostics, "_app_log_path", lambda: tmp / "crlf.log")
        (tmp / "sidecar.log").write_bytes(_nl(self.TB, nl).encode())
        _prep, z = _prepare(client)
        for name, text in _texts(z).items():
            assert "_SRC" not in text, name

    @NEWLINES
    @pytest.mark.parametrize(
        "body",
        [
            "token: |\n  S1\n\n  S2\nsibling: keep_me\n",
            "token:\n  - S1\n  - S2\nsibling: keep_me\n",
            "token='''S1\nS2'''\nsibling: keep_me\n",
            "token=[\n  'S1',\n  'S2',\n]\nsibling: keep_me\n",
            "-----BEGIN PRIVATE KEY-----\nS1\nS2\n-----END PRIVATE KEY-----\nsibling: keep_me\n",
            "password=S1 tail\nsibling: keep_me\n",
            "Authorization: Bearer S1abcdefgh\nsibling: keep_me\n",
        ],
    )
    def test_secret_values_across_lines(self, nl, body):
        out = diagdetail.Scrubber().text(_nl(body, nl))
        assert not re.search(r"S\d", out), repr(out)
        assert "sibling: keep_me" in out and "\r" not in out

    @NEWLINES
    def test_script_comments_and_strings(self, nl):
        src = _nl(
            '# api_key=S1real\nx = """\npassword: S2real\n"""\ntoken = "S3real"\nkeep = 1\n', nl
        )
        out = diagdetail.scrub_script(
            src, "a.py", diagdetail.Scrubber(), diagdetail.Scrubber(source_mode=True)
        )
        assert not re.search(r"S\dreal", out) and "keep = 1" in out


class TestSecretsInDictKeys:
    """结构化数据的**键**也会被写出去：键过 `text()`，分类用原始键，撞键加 `#n`。"""

    KEYS = [
        "https://alice:KEYSECRETxyz@proxy.example/x",
        "api_key=KEYSECRETxyz",
        "-----BEGIN PRIVATE KEY-----\nKEYSECRETxyz\n-----END PRIVATE KEY-----",
        "ghp_" + "KEYSECRETxyz" * 3,
        "github_pat_" + "KEYSECRETxyz" * 3,
        "AKIAKEYSECRETXYZ1234",
        "Authorization: Bearer KEYSECRETxyz1234",
        "password: KEYSECRETxyz",
    ]

    @pytest.mark.parametrize("key", KEYS)
    def test_secret_in_a_key_at_every_nesting(self, key):
        s = diagdetail.Scrubber()
        for doc in (
            {key: True},
            {"outer": {key: 1}},
            {"outer": [{key: "v"}, {"x": {key: None}}]},
            [{key: [1, 2]}],
        ):
            assert "KEYSECRETxyz" not in json.dumps(s.obj(doc)), (key, doc)
            assert "KEYSECRETxyz" not in s.json_or_text(json.dumps(doc))

    def test_home_and_user_in_keys_are_scrubbed(self):
        s = diagdetail.Scrubber()
        home = os.path.expanduser("~")
        out = s.obj({f"{home}/proj/a.py": 1})
        assert home not in json.dumps(out)

    def test_colliding_cleaned_keys_keep_every_entry(self):
        s = diagdetail.Scrubber()
        out = s.obj(
            {
                "https://u1:AAAA1111@h/x": 1,
                "https://u2:BBBB2222@h/x": 2,
                "https://u3:CCCC3333@h/x": 3,
            }
        )
        assert sorted(out.values()) == [1, 2, 3] and len(out) == 3
        assert (
            len(set(out)) == 3
            and any(k.endswith("#2") for k in out)
            and any(k.endswith("#3") for k in out)
        )
        assert "AAAA1111" not in json.dumps(out)

    def test_classification_uses_the_original_key(self):
        s = diagdetail.Scrubber()
        out = s.obj({"api_key=x": "VALUE_SECRET_9", "plain": "ok"})
        assert "VALUE_SECRET_9" not in json.dumps(out) and out["plain"] == "ok"

    def test_ordinary_keys_are_untouched(self):
        s = diagdetail.Scrubber()
        doc = {"name": 1, "项目/图1.py": 2, 3: 4, "max_tokens": 5}
        assert s.obj(doc) == doc


class TestPlaceholderWhitelist:
    """秘密键下只有我们自己产出的记号算「无信息」；任意 `<…>` 不算。"""

    KEPT = [
        "<redacted>", "***", "<user>", "<email>", "<project:0123456789ab>", "seg:0123456789",
        "str:abcdef0123", "file:0123456789abcdef.png", "mod:abcdef0123", "url:0123456789",
        "acct:0123456789", "~", "...", "…", "true", "false", "null", "None",
    ]  # fmt: skip
    ERASED = [
        "<REAL>",
        "<hunter2>",
        "<a b>",
        "<REAL_SECRET>",
        "<project:zz>",
        "seg:hunter2",
        "str:",
        "<>",
    ]

    @pytest.mark.parametrize("v", KEPT)
    def test_own_tokens_are_kept(self, v):
        s = diagdetail.Scrubber()
        assert s.text(f"password={v} x") == f"password={v} x"
        assert s.obj({"password": v}) == {"password": v}

    @pytest.mark.parametrize("v", ERASED)
    def test_anything_else_is_erased(self, v):
        s = diagdetail.Scrubber()
        for line in (f"password={v}", f"password: {v}", f'"password": "{v}"'):
            out = s.text(line)
            assert v.strip("<>") not in out or v in ("<>", "str:"), (line, out)
        assert s.obj({"password": v}) == {"password": "<redacted>"}
        if v not in ("<>", "str:"):
            assert "REAL" not in s.text(f"password=[{v}]") and "hunter2" not in s.text(
                f"token=({v})"
            )


class TestPrivateKeyArmorFamily:
    """任何标签含 PRIVATE / SECRET / ENCRYPTED / KEY BLOCK / OPENSSH / PUTTY 的 armor 块都按 PEM 规则抹到同标签 END。"""

    PGP = (
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\nVersion: GnuPG v2\nComment: alice KEYSECRETxyz\n\n"
        "lQOYBF9KEYSECRETxyzAAA\n=abcd\n-----END PGP PRIVATE KEY BLOCK-----\n"
    )
    SECRET_BLOCKS = [
        PGP,
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\nlQOYBF9KEYSECRETxyz\n",  # 无 END：抹到末尾
        "-----BEGIN SSH2 ENCRYPTED PRIVATE KEY-----\nKEYSECRETxyz\n-----END SSH2 ENCRYPTED PRIVATE KEY-----\n",
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\nKEYSECRETxyz\n-----END ENCRYPTED PRIVATE KEY-----\n",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nKEYSECRETxyz\n-----END OPENSSH PRIVATE KEY-----\n",
        "-----BEGIN RSA PRIVATE KEY-----\nKEYSECRETxyz\n-----END RSA PRIVATE KEY-----\n",
        "-----BEGIN SECRET KEY-----\nKEYSECRETxyz\n-----END SECRET KEY-----\n",
        "-----BEGIN PUTTY KEY-----\nKEYSECRETxyz\n-----END PUTTY KEY-----\n",
    ]
    PUBLIC_BLOCKS = [
        "-----BEGIN PUBLIC KEY-----\nPUBKEYok\n-----END PUBLIC KEY-----\n",
        "-----BEGIN RSA PUBLIC KEY-----\nPUBKEYok\n-----END RSA PUBLIC KEY-----\n",
        "-----BEGIN CERTIFICATE-----\nPUBKEYok\n-----END CERTIFICATE-----\n",
        "-----BEGIN PGP PUBLIC KEY BLOCK-----\nPUBKEYok\n-----END PGP PUBLIC KEY BLOCK-----\n",
        "-----BEGIN PGP SIGNATURE-----\nPUBKEYok\n-----END PGP SIGNATURE-----\n",
        "-----BEGIN SSH2 PUBLIC KEY-----\nPUBKEYok\n-----END SSH2 PUBLIC KEY-----\n",
    ]

    @pytest.mark.parametrize("block", [b for b in SECRET_BLOCKS])
    def test_secret_blocks_are_erased(self, block):
        s = diagdetail.Scrubber()
        for text in (f"before\n{block}after", f"before\n{block}"):
            out = s.text(text)
            assert (
                "KEYSECRETxyz" not in out and "=abcd" not in out and out.startswith("before\n")
            ), out
        assert "KEYSECRETxyz" not in diagdetail.scrub_script(
            "# " + block.replace("\n", "\n# "), "a.py", s, diagdetail.Scrubber(source_mode=True)
        )

    def test_pgp_block_with_end_keeps_what_follows(self):
        out = diagdetail.Scrubber().text("x\n" + self.PGP + "after: kept")
        assert out == "x\n<redacted>\nafter: kept"

    @pytest.mark.parametrize("block", PUBLIC_BLOCKS)
    def test_public_material_is_kept(self, block):
        s = diagdetail.Scrubber()
        assert s.text(f"tls:\n{block}end") == f"tls:\n{block}end"

    def test_public_block_before_a_private_one_is_kept_and_private_is_erased(self):
        text = self.PUBLIC_BLOCKS[2] + self.PGP + self.PUBLIC_BLOCKS[0]
        out = diagdetail.Scrubber().text(text)
        assert out.count("PUBKEYok") == 2 and "KEYSECRETxyz" not in out

    def test_putty_ppk_private_lines(self):
        ppk = (
            "PuTTY-User-Key-File-3: ssh-rsa\nEncryption: none\nComment: me\nPublic-Lines: 2\n"
            "PUBLINE1\nPUBLINE2\nPrivate-Lines: 3\nKEYSECRETxyz1\nKEYSECRETxyz2\nKEYSECRETxyz3\n"
            "Private-MAC: abc123\ntrailer: kept\n"
        )
        out = diagdetail.Scrubber().text(ppk)
        assert "KEYSECRETxyz" not in out and "trailer: kept" in out and "PUBLINE1" in out
        assert out.count("<redacted>") == 1
        short = diagdetail.Scrubber().text("Private-Lines: 9\nKEYSECRETxyz1\n")
        assert "KEYSECRETxyz" not in short

    def test_pem_scan_stays_linear(self):
        import time

        t = time.monotonic()
        diagdetail.Scrubber().text("-----BEGIN PGP PRIVATE KEY BLOCK-----\n" * 4000)
        diagdetail.Scrubber().text("-----BEGIN CERTIFICATE-----\n" * 20000)
        diagdetail.Scrubber().text("-----BEGIN " + "A" * 5000)
        assert time.monotonic() - t < 3


class TestLongKeys:
    """键名不设长度窗口：任意长的键也要判得出（Codex P1：41+ 字符前缀让 `password=REAL` 整条不匹配）。"""

    @pytest.mark.parametrize("n", [41, 200, 100_000])
    @pytest.mark.parametrize(
        "tail",
        [
            "password=",
            "_token: ",
            "-api_key=",
            "Authorization: ",
            "secret_key = ",
            "_pin=",
            "_otp:",
            '"password": ',
        ],
    )
    def test_long_prefix_before_a_secret_word(self, n, tail):
        out = diagdetail.Scrubber().text(("x" * n) + tail + "REAL_SECRET9 tail")
        assert "REAL_SECRET9" not in out, (n, tail)

    @pytest.mark.parametrize("n", [41, 200, 100_000])
    def test_long_suffix_and_camel_case_keys(self, n):
        s = diagdetail.Scrubber()
        assert "REAL_SECRET9" not in s.text("password" + "x" * n + "=REAL_SECRET9")
        assert "REAL_SECRET9" not in s.text("myVeryLong" * (n // 10 + 1) + "ApiKey: REAL_SECRET9")
        assert "REAL_SECRET9" not in s.text("a." * (n // 2) + "token=REAL_SECRET9")

    def test_long_innocent_keys_are_kept(self):
        s = diagdetail.Scrubber()
        line = ("x" * 300) + "=hello"
        assert s.text(line) == line
        line = ("max_" + "x" * 300 + "_tokens") + "=4096"
        assert s.text(line) == line

    def test_linear_on_many_long_tokens(self):
        import time

        s = diagdetail.Scrubber()
        t = time.monotonic()
        s.text(("x" * 5000 + " ") * 200)  # 大量长 token、无分隔符
        s.text(("x" * 5000 + "=1 ") * 200)  # 大量长 token、带分隔符
        s.text("a" * 400_000)
        s.text("a-" * 200_000 + "=")
        assert time.monotonic() - t < 5


class TestNamesDerivedFromUserStringsAreScrubbed:
    """用户可控字符串（文件名 / 面板名 / 脚本名）派生出的内层 zip 成员名与 index 里的名字字段：先过 `Scrubber.text()` 再 `_safe_name()`。"""

    BAD = [  # 文件系统里合法的名字
        "token=NAMESECRETxyz",
        "ghp_" + "NAMESECRETxyz" * 3,
        "api_key=NAMESECRETxyz",
        "password: NAMESECRETxyz" if os.name != "nt" else "password=NAMESECRETxyz",
    ]
    PANEL_BAD = BAD + ["https://alice:NAMESECRETxyz@proxy.example/fig"]  # 面板标识不一定是文件名

    def _everything(self, items):
        """全部成员名 + 全部内容（含内层 zip 解开）+ 外层 zip 原始字节。"""
        blob = b""
        for name, data in diagdetail.entries(items):
            blob += name.encode() + b"\0" + data
            if name.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for n in z.namelist():
                        blob += n.encode() + b"\0" + z.read(n)
        return blob

    def test_member_name_helper(self):
        s = diagdetail.Scrubber()
        taken = {"index.json"}
        a = diagdetail._member_name(s, "token=NAMESECRETxyz.pdf", taken)
        b = diagdetail._member_name(s, "token=OTHERSECRETabc.pdf", taken)
        c = diagdetail._member_name(s, "index.json", taken)
        assert "NAMESECRETxyz" not in a and a.endswith(".pdf") and b != a and c != "index.json"
        assert len({a, b, c, "index.json"}) == 4
        assert diagdetail._name_id("x") == diagdetail._name_id("x") != diagdetail._name_id("y")
        assert diagdetail._member_name(s, "plain name.py") == "plain_name.py"

    def test_state_and_corrupt_sessions(self, world, monkeypatch):
        state = world["tmp"] / "state2"
        state.mkdir()
        for i, bad in enumerate(self.BAD):
            (state / f"{bad}-{i}.json").write_text('{"a": 1}', encoding="utf-8")
            (state / f"{bad}-{i}.corrupt-1").write_text("CORRUPTBODY", encoding="utf-8")
        monkeypatch.setattr(diagdetail, "state_dir", lambda: state)
        opts = diagdetail.DetailOptions(corrupt_sessions=True)
        items = diagdetail.collect_state(diagdetail.Sources(), opts, diagdetail.Scrubber())
        items += diagdetail.collect_corrupt_sessions(
            diagdetail.Sources(), opts, diagdetail.Scrubber()
        )
        assert b"NAMESECRETxyz" not in self._everything(items)
        idx = json.loads(
            next(i for i in items if i.path == "index.json" and i.kind == "state").data
        )
        assert all(len(r["id"]) == 10 for r in idx["files"])

    def test_colliding_cleaned_names_keep_every_entry(self, world, monkeypatch):
        state = world["tmp"] / "state3"
        state.mkdir()
        for tail in ("AAAA1111", "BBBB2222", "CCCC3333"):
            (state / f"token={tail}.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(diagdetail, "state_dir", lambda: state)
        items = diagdetail.collect_state(
            diagdetail.Sources(), diagdetail.DetailOptions(), diagdetail.Scrubber()
        )
        members = [i.path for i in items if i.path != "index.json"]
        assert len(members) == len(set(members)) == 3
        idx = json.loads(next(i for i in items if i.path == "index.json").data)
        assert sorted(r["file"] for r in idx["files"]) == sorted(members)
        assert len({r["id"] for r in idx["files"]}) == 3

    def test_scripts_and_overrides(self, world):
        proj = world["tmp"] / "projnames"
        proj.mkdir()
        names = []
        for i, bad in enumerate(self.BAD):
            (proj / f"{bad}-{i}.py").write_text("x = 1\n", encoding="utf-8")
            names.append(f"{bad}-{i}.py")
        src = diagdetail.Sources(
            project={"figures_dir": str(proj)},
            scripts=names,
            baked={f"{b}-{i}": {"versions": []} for i, b in enumerate(self.PANEL_BAD)},
            frontend={
                "overrides": [
                    {"panel": f"{b}-{i}.pdf", "x": 1} for i, b in enumerate(self.PANEL_BAD)
                ]
            },
        )
        items = diagdetail.collect_source(
            src, diagdetail.DetailOptions(source=True), diagdetail.Scrubber()
        )
        items += diagdetail.collect_overrides(
            src, diagdetail.DetailOptions(), diagdetail.Scrubber()
        )
        blob = self._everything(items)
        assert b"NAMESECRETxyz" not in blob, blob[:300]

    def test_crash_report_names(self, world, monkeypatch):
        base = world["tmp"] / "crashnames"
        base.mkdir()
        for i, bad in enumerate(self.BAD):
            d = base / f"AppCrash_Tavotto.exe_{bad}-{i}"
            d.mkdir()
            (d / "Report.wer").write_text("report", encoding="utf-8")
            (base / f"Tavotto-{bad}-{i}.ips").write_text("ips", encoding="utf-8")
        monkeypatch.setattr(
            diagdetail,
            "crash_dirs",
            lambda: [("windows_wer_user_archive", base), ("macos_diagnostic_reports", base)],
        )
        items = diagdetail.collect_crash_reports(
            diagdetail.Sources(),
            diagdetail.DetailOptions(crash_reports=True),
            diagdetail.Scrubber(),
        )
        assert len(items) > 1
        assert b"NAMESECRETxyz" not in self._everything(items)


class TestYamlCommentOnlyAndIndicatorComments:
    """秘密键后面只有注释 / 块标量记号加注释：值在下面缩进的行里。"""

    SHAPES = [
        "{k}:   # prod\n  REAL_SECRET\n",
        "{k}: # x\n  - REAL_SECRET\n  - REAL_SECRET2\n",
        "{k}: #\n  nested: REAL_SECRET\n",
        "{k}: | # c\n  REAL_SECRET\n",
        "{k}: >- # c\n  REAL_SECRET\n\n  REAL_SECRET2\n",
        "{k}: |2+ #c\n   REAL_SECRET\n",
        "{k}:\t# tab comment\n\tREAL_SECRET\n",  # 子行用 tab 缩进
        "{k}: # x\n\t- REAL_SECRET\n",
        "{k}:    \n  REAL_SECRET\n",
    ]

    @NEWLINES
    @pytest.mark.parametrize("key", ["password", "token", "api_key", "client_secret"])
    @pytest.mark.parametrize("shape", SHAPES)
    def test_value_is_gone_and_siblings_stay(self, nl, key, shape):
        for ws in ("", "  "):
            block = shape.format(k=key)
            lines = block.split("\n")
            body = "\n".join([lines[0]] + [ws + ln if ln.strip() else ln for ln in lines[1:]])
            text = f"top:\n{ws}before: keep_a\n{ws}{body}{ws}after: keep_b\n"
            out = diagdetail.Scrubber().text(_nl(text, nl))
            assert "REAL_SECRET" not in out, repr(out)
            assert "before: keep_a" in out and "after: keep_b" in out, repr(out)

    def test_mixed_tab_and_space_indent_fails_closed(self):
        out = diagdetail.Scrubber().text(
            "a:\n" + " " * 12 + "password: # c\n\tREAL_SECRET\n" + " " * 12 + "after: keep_b\n"
        )
        assert "REAL_SECRET" not in out

    def test_comment_only_value_without_children_is_not_a_leak(self):
        out = diagdetail.Scrubber().text("password: # prod\nnext: ok\n")
        assert "next: ok" in out

    def test_final_zip_has_no_hits(self, client, world):
        (world["tmp"] / "yaml.log").write_text(
            "password:   # prod\n  REAL_SECRET\ntoken: # x\n  - REAL_SECRET\napi_key: #\n  nested: REAL_SECRET\nsib: ok\n",
            encoding="utf-8",
        )
        diagnostics_log = world["tmp"] / "yaml.log"
        import tavotto.engine.diagnostics as dg

        orig = dg._app_log_path
        dg._app_log_path = lambda: diagnostics_log
        try:
            _prep, z = _prepare(client)
        finally:
            dg._app_log_path = orig
        texts = _texts(z)
        assert any("sib: ok" in t for t in texts.values()), "日志没进包：用例是空的"
        for name, text in texts.items():
            assert "REAL_SECRET" not in text, name


class TestPassKeysInTheFinalZip:
    """`DB_PASS` / `SMTP_PASS` / `REDIS_PASS` 这类 `*_PASS` 键：默认收集的日志、对象里都要抹，最终 ZIP 0 命中。"""

    def test_object_values_are_redacted(self):
        out = diagdetail.Scrubber().obj(
            {"DB_PASS": "PASS_CANARY_1", "smtpPass": "PASS_CANARY_2", "compass": "N"}
        )
        assert out == {"DB_PASS": "<redacted>", "smtpPass": "<redacted>", "compass": "N"}

    def test_final_zip_has_no_hits(self, client, world):
        log = world["tmp"] / "pass.log"
        log.write_text(
            "DB_PASS=TAVOTTO_DB_PASS_CANARY_7f3b\nSMTP_PASS: PASS_CANARY_8\n"
            'cfg {"REDIS_PASS": "PASS_CANARY_9"}\ncompass=keep_me\n',
            encoding="utf-8",
        )
        import tavotto.engine.diagnostics as dg

        orig = dg._app_log_path
        dg._app_log_path = lambda: log
        try:
            _prep, z = _prepare(client)
        finally:
            dg._app_log_path = orig
        texts = _texts(z)
        assert any("compass=keep_me" in t for t in texts.values()), "日志没进包：用例是空的"
        for name, text in texts.items():
            assert "CANARY" not in text, name


class TestYamlIndentlessSequence:
    """YAML 允许列表项和键同列：`token:` 下一行直接 `- REAL`。空值秘密键后同列的 `-` 项都是值，后面的同级键保留。"""

    @pytest.mark.parametrize("nl", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
    @pytest.mark.parametrize("pad", ["", "  "], ids=["top", "nested"])
    @pytest.mark.parametrize(
        "shape",
        [
            "{p}{k}:{nl}{p}- REAL_SECRET{nl}{p}- REAL_SECRET2{nl}",
            "{p}{k}: # c{nl}{p}- REAL_SECRET{nl}",
            "{p}{k}:{nl}{p}- a: REAL_SECRET{nl}{p}  b: REAL_SECRET2{nl}",
            "{p}{k}:{nl}{p}-{nl}{p}  REAL_SECRET{nl}",
            "{p}{k}:{nl}{nl}{p}- REAL_SECRET{nl}",
        ],
        ids=["plain", "comment", "item-map", "bare-dash", "blank-line"],
    )
    @pytest.mark.parametrize("key", ["token", "password", "DB_PASS"])
    def test_items_are_redacted_and_siblings_kept(self, key, shape, pad, nl):
        text = (
            f"{pad}before: keep_a{nl}"
            + shape.format(k=key, p=pad, nl=nl)
            + f"{pad}after: keep_b{nl}"
        )
        out = diagdetail.Scrubber().text(text)
        assert "REAL_SECRET" not in out, out
        assert "keep_a" in out and "keep_b" in out, out

    def test_same_column_dash_after_non_empty_value_is_not_swallowed(self):
        out = diagdetail.Scrubber().text("token: REAL_SECRET\n- keep_item\n")
        assert "REAL_SECRET" not in out and "keep_item" in out, out

    def test_final_zip_has_no_hits(self, client, world):
        log = world["tmp"] / "seq.log"
        log.write_text("token:\n- REAL_SECRET\n- REAL_SECRET2\nsib: ok_seq\n", encoding="utf-8")
        import tavotto.engine.diagnostics as dg

        orig = dg._app_log_path
        dg._app_log_path = lambda: log
        try:
            _prep, z = _prepare(client)
        finally:
            dg._app_log_path = orig
        texts = _texts(z)
        assert any("sib: ok_seq" in t for t in texts.values()), "日志没进包：用例是空的"
        for name, text in texts.items():
            assert "REAL_SECRET" not in text, name


class TestBoringContainerWordsNeedBoundaries:
    """容器里的 true/false/null/none 豁免只认独立的词；拼在一起的哨兵词是口令。"""

    @pytest.mark.parametrize(
        "value",
        [
            "[truefalse]",
            "(nullnone)",
            "[TrueNone]",
            "{falsetrue}",
            "[true,falsenull]",
            "(none nulltrue)",
            "[truex]",
        ],
    )
    @pytest.mark.parametrize("key", ["password", "token", "DB_PASS"])
    def test_concatenated_sentinels_are_redacted(self, key, value):
        out = diagdetail.Scrubber().text(f"{key}={value} tail=keep")
        assert value not in out and "<redacted>" in out, out

    @pytest.mark.parametrize("value", ["[]", "(None)", "[true, false]", "{null: none}", "( TRUE )"])
    def test_real_boring_containers_are_kept(self, value):
        out = diagdetail.Scrubber().text(f"token={value}")
        assert out == f"token={value}", out

    def test_final_zip_has_no_hits(self, client, world):
        log = world["tmp"] / "boring.log"
        log.write_text("password=[truefalse]\ntoken=(nullnone)\nsib: ok_boring\n", encoding="utf-8")
        import tavotto.engine.diagnostics as dg

        orig = dg._app_log_path
        dg._app_log_path = lambda: log
        try:
            _prep, z = _prepare(client)
        finally:
            dg._app_log_path = orig
        texts = _texts(z)
        assert any("sib: ok_boring" in t for t in texts.values()), "日志没进包：用例是空的"
        for name, text in texts.items():
            assert "truefalse" not in text and "nullnone" not in text, name


class TestYamlNodePropertiesBeforeSecretValues:
    """YAML 锚点 / 标签写在值前面：`password: &shared REAL`、`token: !!str REAL`。属性不是值，后面的值照样抹。"""

    @pytest.mark.parametrize("nl", ["\n", "\r\n"], ids=["lf", "crlf"])
    @pytest.mark.parametrize(
        "shape",
        [
            "{k}: &shared REAL_SECRET{nl}",
            "{k}: !!str REAL_SECRET{nl}",
            "{k}: &a !!str REAL_SECRET{nl}",
            "{k}: !<tag:yaml.org,2002:str> REAL_SECRET{nl}",
            "{k}: &a 'REAL_SECRET x'{nl}",
            "{k}: &a{nl}  REAL_SECRET{nl}",
            "{k}: &a |{nl}  REAL_SECRET{nl}",
            "{k}: !!str >-{nl}  REAL_SECRET{nl}",
            "{k}: &a{nl}- REAL_SECRET{nl}",
            "{k}：&a REAL_SECRET{nl}",
        ],
        ids=["anchor", "tag", "anchor-tag", "verbatim-tag", "quoted", "anchor-block", "anchor-literal",
             "tag-folded", "anchor-indentless", "fullwidth-colon"],
    )  # fmt: skip
    @pytest.mark.parametrize("key", ["password", "token", "DB_PASS"])
    def test_value_behind_properties_is_redacted(self, key, shape, nl):
        text = f"before: keep_a{nl}" + shape.format(k=key, nl=nl) + f"after: keep_b{nl}"
        out = diagdetail.Scrubber().text(text)
        assert "REAL_SECRET" not in out, out
        assert "keep_a" in out and "keep_b" in out, out

    def test_query_string_empty_value_before_ampersand_is_kept(self):
        assert diagdetail.Scrubber().text("https://h/p?token=&x=1") == "https://h/p?token=&x=1"

    def test_final_zip_has_no_hits(self, client, world):
        log = world["tmp"] / "anchor.log"
        log.write_text(
            "password: &shared REAL_SECRET\ntoken: !!str REAL_SECRET\nsib: ok_anchor\n",
            encoding="utf-8",
        )
        import tavotto.engine.diagnostics as dg

        orig = dg._app_log_path
        dg._app_log_path = lambda: log
        try:
            _prep, z = _prepare(client)
        finally:
            dg._app_log_path = orig
        texts = _texts(z)
        assert any("sib: ok_anchor" in t for t in texts.values()), "日志没进包：用例是空的"
        for name, text in texts.items():
            assert "REAL_SECRET" not in text, name


class TestIssue1012BackendItems:
    """#1012 第 3、5、8、9 项。"""

    def _python(self):
        py = pool.find_worker_python()
        return py, pool.source_of(py) == pool.SOURCE_BUNDLED

    def test_probe_does_not_write_the_matplotlib_font_cache(self, tmp_path, monkeypatch):
        """3：空缓存目录跑真探针，前后各列一次目录，内容不变；字体数量与字体族仍报得出。"""
        py, bundled = self._python()
        cfg = tmp_path / "mplcfg"  # 故意不预建：新用户环境里它本来就不存在
        monkeypatch.setattr(
            diagdetail, "PROBE_TIMEOUT_S", 120
        )  # 空缓存要现建字体表，慢机器上超过默认 10 s
        out = REAL_PROBE(py, bundled, str(tmp_path), {"MPLCONFIGDIR": str(cfg)})
        assert out["status"] == "ok", out
        assert not cfg.exists(), (
            "探针（含 import matplotlib / get_cachedir）不许把真实缓存目录建出来"
        )
        mpl = out["matplotlib"]
        assert mpl["font_count"] > 0 and mpl["font_family_count"] > 0 and mpl["fonts"]
        assert mpl["cachedir"] == str(cfg)  # 报的是真实缓存目录，不是临时目录
        assert mpl["configdir"] == str(cfg)

    def test_probe_leaves_an_existing_cache_dir_untouched(self, tmp_path, monkeypatch):
        """3：目录已存在时前后列表不变（空缓存也不写 fontlist）。"""
        py, bundled = self._python()
        cfg = tmp_path / "mplcfg"
        cfg.mkdir()
        monkeypatch.setattr(diagdetail, "PROBE_TIMEOUT_S", 120)
        out = REAL_PROBE(py, bundled, str(tmp_path), {"MPLCONFIGDIR": str(cfg)})
        assert out["status"] == "ok", out
        assert sorted(p.name for p in cfg.rglob("*")) == []
        assert out["matplotlib"]["cachedir"] == str(cfg)

    def _mpl_dirs(
        self, monkeypatch, tmp_path, platform, *, localappdata_rule=True, old_exists=False
    ):
        """进程内 exec 探针里的路径规则片段，模拟平台与环境；返回 (配置目录, 缓存目录, 家目录, LOCALAPPDATA)。"""
        import sys
        from pathlib import Path

        home = tmp_path / "home"
        home.mkdir()
        if old_exists:
            (home / ".matplotlib").mkdir()
        la = tmp_path / "LocalAppData"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.delenv("MPLCONFIGDIR", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(la))
        monkeypatch.setattr(sys, "platform", platform)
        ns = {"os": os, "sys": sys, "Path": Path}
        exec(diagdetail._MPL_DIRS_CODE, ns)
        ns["_mpl_uses_localappdata"] = lambda: localappdata_rule
        return (
            ns["_real_dir"]("XDG_CONFIG_HOME", ".config"),
            ns["_real_dir"]("XDG_CACHE_HOME", ".cache"),
            home,
            la,
        )

    def test_mpl_dir_rules_on_windows_follow_matplotlib_3_11(self, monkeypatch, tmp_path):
        """3：模拟 win32（matplotlib ≥3.11 的规则）：旧 ~/.matplotlib 不存在 → %LOCALAPPDATA%/matplotlib，什么都不建。"""
        cfg, cache, home, la = self._mpl_dirs(monkeypatch, tmp_path, "win32")
        assert cfg == cache == str((la / "matplotlib").resolve())
        assert not la.exists() and not (home / ".matplotlib").exists()

    def test_mpl_dir_rules_on_windows_keep_an_existing_old_dir(self, monkeypatch, tmp_path):
        """3：win32（≥3.11）旧 ~/.matplotlib 已存在 → 沿用旧的，不去 LOCALAPPDATA。"""
        cfg, cache, home, la = self._mpl_dirs(monkeypatch, tmp_path, "win32", old_exists=True)
        assert cfg == cache == str((home / ".matplotlib").resolve())
        assert not la.exists()

    def test_mpl_dir_rules_on_windows_before_3_11_use_dot_matplotlib(self, monkeypatch, tmp_path):
        """3：matplotlib ≤3.10（源码里没有 LOCALAPPDATA 分支）：Linux / FreeBSD 以外一律 ~/.matplotlib，不去碰 LOCALAPPDATA。"""
        cfg, cache, home, la = self._mpl_dirs(
            monkeypatch, tmp_path, "win32", localappdata_rule=False
        )
        assert cfg == cache == str((home / ".matplotlib").resolve())
        assert not la.exists() and not (home / ".matplotlib").exists()

    def test_mpl_dir_rules_on_other_platforms(self, monkeypatch, tmp_path):
        cfg, cache, home, _ = self._mpl_dirs(monkeypatch, tmp_path, "darwin")
        assert cfg == cache == str((home / ".matplotlib").resolve())
        sub = tmp_path / "linux"
        sub.mkdir()
        cfg, cache, home, _ = self._mpl_dirs(monkeypatch, sub, "linux")
        assert cfg == str((home / ".config" / "matplotlib").resolve())
        assert cache == str((home / ".cache" / "matplotlib").resolve())
        assert not (home / ".config").exists() and not (home / ".cache").exists()

    def test_the_localappdata_rule_is_read_from_the_installed_matplotlib_source(self):
        """规则随版本变：探针读（不 import）matplotlib/__init__.py 判有没有 LOCALAPPDATA 分支。"""
        import importlib.util
        from pathlib import Path

        ns = {"os": os, "sys": __import__("sys"), "Path": Path}
        exec(diagdetail._MPL_DIRS_CODE, ns)
        spec = importlib.util.find_spec("matplotlib")
        if spec is None:
            pytest.skip("本进程没有 matplotlib")
        assert ns["_mpl_uses_localappdata"]() == (
            "LOCALAPPDATA" in Path(spec.origin).read_text(encoding="utf-8")
        )

    def _render_env(self, monkeypatch, project, overrides=None):
        py, bundled = self._python()
        from tavotto.engine import workdir

        monkeypatch.setattr(workdir, "mode_for", lambda fd: "project_root")
        monkeypatch.setattr(pool, "resolve_worker_python", lambda d, **k: (py, pool.source_of(py)))
        monkeypatch.setattr(diagdetail, "_probe_subprocess", REAL_PROBE)
        src = diagdetail.Sources(
            project={"figures_dir": str(project)}, frontend={"overrides": overrides or []}
        )
        items = diagdetail.collect_render_env(
            src, diagdetail.DetailOptions(), diagdetail.Scrubber()
        )
        return json.loads(items[0].data)

    def test_isolated_probe_is_marked_and_lists_shadowing_files_without_running_them(
        self, monkeypatch, tmp_path
    ):
        """5：项目里放 matplotlib.py / numpy.py / matplotlib/ 时带 may_differ，shadowing_candidates 列出文件名，探针不执行它们。"""
        proj = tmp_path / "proj"
        proj.mkdir()
        marker = tmp_path / "marker"
        body = f"open({str(marker)!r}, 'w').write('executed')\n"
        (proj / "matplotlib.py").write_text(body, encoding="utf-8")
        (proj / "numpy.py").write_text(body, encoding="utf-8")
        (proj / "numpy").mkdir()
        (proj / "matplotlib").mkdir()
        (proj / "notes.txt").write_text("x", encoding="utf-8")
        doc = self._render_env(monkeypatch, proj)
        probe = doc["probe"]
        assert probe["status"] == "ok"
        assert probe["may_differ"] == "user_import_paths_not_reproduced"
        assert probe["shadowing_candidates"] == [
            "matplotlib.py",
            "matplotlib/",
            "numpy.py",
            "numpy/",
        ]
        assert not marker.exists(), "探针不能执行项目里的同名模块"

    def test_a_symlinked_package_directory_counts_as_a_shadowing_candidate(
        self, monkeypatch, tmp_path
    ):
        proj = tmp_path / "proj"
        (tmp_path / "vendor" / "numpy").mkdir(parents=True)
        proj.mkdir()
        try:
            os.symlink(tmp_path / "vendor" / "numpy", proj / "numpy", target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable (Windows without privilege)")
        probe = self._render_env(monkeypatch, proj)["probe"]
        assert probe["shadowing_candidates"] == ["numpy/"]

    def test_scan_also_covers_the_focused_scripts_own_directory(self, monkeypatch, tmp_path):
        """#1041 1：safe worker 把脚本所在目录也放到 sys.path 最前——`panels/chart.py` 旁边的 `panels/numpy.py` 也要列出来。"""
        proj = tmp_path / "proj"
        (proj / "panels").mkdir(parents=True)
        (proj / "panels" / "chart.py").write_text("x", encoding="utf-8")
        marker = tmp_path / "marker"
        (proj / "panels" / "numpy.py").write_text(
            f"open({str(marker)!r}, 'w').write('executed')\n", encoding="utf-8"
        )
        probe = self._render_env(monkeypatch, proj, [{"panel": "p", "script": "panels/chart.py"}])[
            "probe"
        ]
        assert probe["shadowing_candidates"] == ["numpy.py"]
        assert not marker.exists()
        assert self._render_env(monkeypatch, proj)["probe"]["shadowing_candidates"] == []

    def _native(self, monkeypatch, tmp_path, kind, *, target=None, with_spec=True):
        """原生会话：项目根与 cwd 是两个不同的目录，各放一个会遮挡的文件。"""
        proj, cwd = tmp_path / "proj", tmp_path / "cwd"
        (proj / "panels").mkdir(parents=True)
        cwd.mkdir()
        (proj / "numpy.py").write_text("x", encoding="utf-8")  # 项目根：不是导入根
        (proj / "panels" / "numpy.py").write_text("x", encoding="utf-8")  # 脚本所在目录
        (cwd / "matplotlib.py").write_text("x", encoding="utf-8")

        def configure(sess):
            sess.target_kind = kind
            sess.project_root = str(proj)
            if with_spec:
                sess.spec.project_root = str(proj)
                sess.spec.target = target

        _c, doc = TestNativeCwdUnavailable()._doc(monkeypatch, str(cwd), configure)
        assert doc["inspected"] == "native_session"
        assert doc["probe"]["may_differ"] == "user_import_paths_not_reproduced"
        return doc["probe"]["shadowing_candidates"]

    def test_native_module_target_scans_only_the_cwd_not_the_project_root(
        self, monkeypatch, tmp_path
    ):
        """#1041 4：`-m` 目标只有 cwd 在 sys.path 最前（`_run_module`）；project_root 里的同名模块不能报成遮挡。"""
        assert self._native(monkeypatch, tmp_path, "module", target="pkg.fig") == ["matplotlib.py"]

    def test_native_script_target_scans_the_scripts_directory_only(self, monkeypatch, tmp_path):
        """script 目标：sys.path[0] 是脚本所在目录（不是项目根、也不是 cwd）。"""
        assert self._native(monkeypatch, tmp_path, "script", target="panels/fig.py") == ["numpy.py"]

    def test_native_script_target_without_a_launch_spec_is_left_out(self, monkeypatch, tmp_path):
        """终端拥有的会话只记了文件名、算不出脚本目录：不列（宁可漏，不报假的）。"""
        assert self._native(monkeypatch, tmp_path, "script", with_spec=False) == []

    def test_clean_project_has_the_mark_and_an_empty_candidate_list(self, monkeypatch, tmp_path):
        proj = tmp_path / "clean"
        proj.mkdir()
        probe = self._render_env(monkeypatch, proj)["probe"]
        assert probe["may_differ"] == "user_import_paths_not_reproduced"
        assert probe["shadowing_candidates"] == []

    def test_dns_probe_threads_are_bounded_after_timeouts(self, monkeypatch):
        """8：解析卡住（超时）后反复发送，遗留线程数不超过主机数；上一个没结束就不再起新的。"""
        release = threading.Event()
        started = []

        def stuck(host):
            started.append(host)
            release.wait(30)
            return ["203.0.113.7"]

        monkeypatch.setattr(diagdetail, "_resolve", stuck)
        monkeypatch.setattr(diagdetail, "DNS_TIMEOUT_S", 0.05)
        diagdetail._DNS_PENDING.clear()
        try:
            first = [diagdetail.dns_fact(h) for h in diagdetail.DNS_HOSTS]
            assert all(f["reason"] == "timeout" for f in first)
            for _ in range(5):
                again = [diagdetail.dns_fact(h) for h in diagdetail.DNS_HOSTS]
                assert all(f["reason"] == "previous_lookup_pending" for f in again)
            assert len(started) == len(diagdetail.DNS_HOSTS), "重复发送不许再起新线程"
            assert sum(t.name == "diag-dns" for t in threading.enumerate()) <= len(
                diagdetail.DNS_HOSTS
            )
        finally:
            release.set()
            for t in list(diagdetail._DNS_PENDING.values()):
                t.join(5)
            diagdetail._DNS_PENDING.clear()
        assert diagdetail.dns_fact("tavotto.cn")["addresses"] == ["203.0.113.7"]

    def test_no_proxy_hosts_are_reduced_to_a_count(self, monkeypatch):
        """9：NO_PROXY / no_proxy 里的主机名与 IP 不进 detail-network.json，只剩条目数。"""
        # Windows 的环境变量名不分大小写：NO_PROXY 与 no_proxy 是同一个，只设一个
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.setenv(
            "NO_PROXY",
            "corp.internal.example,10.1.2.3, .intra.example,localhost,secret-host.example",
        )
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
        src = diagdetail.Sources()
        items = diagdetail.collect_network(src, diagdetail.DetailOptions(), diagdetail.Scrubber())
        text = items[0].data.decode("utf-8")
        for leak in ("corp.internal", "10.1.2.3", "intra.example", "secret-host", "localhost"):
            assert leak not in text
        net = json.loads(text)
        assert net["proxy"]["no_proxy_entries"] == 5
        assert "NO_PROXY" not in net["proxy"]["env"] and "no_proxy" not in net["proxy"]["env"]
        assert net["proxy"]["env"]["HTTPS_PROXY"] == "http://proxy.example:8080"
