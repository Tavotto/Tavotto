"""插件的「有没有新版本」检查。

这东西的危险不在于它做错事，而在于它**挡在出图前面**：一个 5 秒的 DNS 超时、
一句写进 stdout 的提醒、一次把 JSON 弄脏的 print，都会让「画张图」这件事整个
失败，而失败原因跟画图毫无关系。所以下面的用例基本都在验「它不作恶」：

  * 不打印到 stdout（调用方读的是那一行 JSON）
  * 不联网超过 2 秒，网络挂了不报错、不阻塞
  * 不往插件目录里写任何东西（那目录归 Codex 管，可能只读）
  * 不按字符串比版本号（0.10.0 vs 0.9.0）
  * 不把插件版本当成 Tavotto 版本

跑的是插件自己的脚本（`codex-plugin/skills/tavotto-figure/scripts/`），
不是 tavotto 包里的代码。
"""

import ast
import http.server
import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import tavotto

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "codex-plugin" / "skills" / "tavotto-figure" / "scripts"
PLUGIN_JSON = ROOT / "codex-plugin" / ".codex-plugin" / "plugin.json"


@pytest.fixture(scope="module")
def uc():
    """import 插件的 update_check（它会 `from handoff import config_dir`）。"""
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("update_check", SCRIPTS / "update_check.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["update_check"] = mod
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.path.remove(str(SCRIPTS))
        sys.modules.pop("update_check", None)


@pytest.fixture
def serve_manifest():
    """把清单用**真 HTTP**（loopback）发出去，不要用 `file://`。

    起子进程的那几条用例吃的是脚本里的 `TIMEOUT = 1.5`，而那是一条**总墙钟**。
    用 `file://` 时这条预算是坏的：

    * `urlopen(timeout=)` 对 HTTP 有效（`AbstractHTTPHandler.do_open` 收这个参数），
      对 `file://` **完全无效**——CPython 的 `FileHandler.open_local_file(self, req)`
      连 timeout 参数都不接；
    * 于是"打开文件"这一步不受任何约束（Windows 上 Defender 扫一个刚建的临时
      文件足以卡住它），等它慢慢成功返回之后，读循环第一次掐表就已经超时，
      `fetch()` 回 `None`——**一个字节都还没读**。

    表现是 `status: "unknown"`，而 `fetch()` 按设计吞掉全部失败原因（生产上
    这个取舍是对的：用户是来画图的），所以用例只能告诉你"取不到"，
    告诉不了你为什么。2026-08-28 它在合并队列的 Windows 腿上红过一次。

    改用 loopback HTTP 之后，那条预算恢复了它本来的语义（它防的是"挂了的代理、
    被限速的镜像"，见脚本 docstring），而本地回环快到不可能吃掉 1.5 秒。
    """
    box = {"body": b"{}"}

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler 的接口
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(box["body"])))
            self.end_headers()
            self.wfile.write(box["body"])

        def log_message(self, *a):  # 别把 CI 日志刷满
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    def publish(obj) -> str:
        box["body"] = json.dumps(obj).encode("utf-8")
        return f"http://127.0.0.1:{srv.server_address[1]}/latest.json"

    try:
        yield publish
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def manifest(version="0.9.9", **extra):
    out = {
        "schema": 1,
        "plugin": "tavotto",
        "channel": "stable",
        "latest_version": version,
        "download_url": "https://example.com/p.zip",
        "release_notes_url": "https://example.com/notes",
        "published_at": "2026-08-18T00:00:00Z",
    }
    out.update(extra)
    return out


def fetcher_for(payload, calls=None):
    def _fetch(url, timeout):
        if calls is not None:
            calls.append((url, timeout))
        return payload

    return _fetch


# ------------------------------ 版本比较 ---------------------------------
@pytest.mark.parametrize(
    "newer,older",
    [
        ("0.7.1", "0.7.0"),
        ("0.10.0", "0.9.0"),  # 按字符串比会判反——发到两位数小版本就踩
        ("1.0.0", "0.99.99"),
        ("0.8", "0.7.9"),  # 位数不齐要补零
        ("v0.7.1", "0.7.0"),  # 前缀 v
        ("0.7.1", "0.7.1-rc.1"),  # 正式版 > 预发布版
        ("0.7.1-rc.2", "0.7.1-rc.1"),
    ],
)
def test_semantic_version_ordering(uc, newer, older):
    assert uc.is_newer(newer, older) is True
    assert uc.is_newer(older, newer) is False


def test_same_version_is_not_newer(uc):
    assert uc.is_newer("0.7.0", "0.7.0") is False
    assert uc.is_newer("0.7.0+build.9", "0.7.0") is False  # 构建元数据不参与比较


@pytest.mark.parametrize("junk", ["", "latest", "0.7.x", None, 7, "1.2.3.4.5"])
def test_unparsable_versions_never_guess(uc, junk):
    """解不出就说不知道。猜一个方向 = 要么漏提醒，要么天天提醒一个假新版。"""
    assert uc.is_newer(junk, "0.7.0") is None
    assert uc.parse_version(junk) is None


# --------------------------- 版本号只有一处 -------------------------------
def test_current_version_comes_from_plugin_json(uc, tmp_path):
    """当前版本从 plugin.json 读——发版只改那一个文件。"""
    assert uc.current_version() == json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
    assert uc.current_version() == tavotto.__version__  # 既有约定：随产品发版

    other = tmp_path / "plugin.json"
    other.write_text(json.dumps({"version": "1.2.3"}), encoding="utf-8")
    assert uc.current_version(str(other)) == "1.2.3"


def test_no_version_string_is_hardcoded_in_the_script():
    """代码里不许再写一份版本号——两份必然漂。"""
    src = (SCRIPTS / "update_check.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value != tavotto.__version__, f"第 {node.lineno} 行写死了版本号"


def test_missing_plugin_json_is_not_a_crash(uc, tmp_path):
    assert uc.current_version(str(tmp_path / "nope.json")) is None


# ------------------------------ 缓存与频率 -------------------------------
def test_first_check_hits_the_network_then_caches(uc, tmp_path):
    calls = []
    env = {"TAVOTTO_CONFIG_DIR": str(tmp_path)}
    got = uc.check(environ=env, fetcher=fetcher_for(manifest(), calls), version="0.7.0", now=1000.0)
    assert got["status"] == "available" and got["source"] == "network"
    assert got["latest_version"] == "0.9.9"
    assert len(calls) == 1
    assert (tmp_path / "codex-plugin-update.json").is_file()


def test_second_check_within_24h_does_not_touch_the_network(uc, tmp_path):
    """默认每 24 小时一次。每次画图都发一个请求 = 我们在替用户交网络税。"""
    env = {"TAVOTTO_CONFIG_DIR": str(tmp_path)}
    uc.check(environ=env, fetcher=fetcher_for(manifest()), version="0.7.0", now=1000.0)
    calls = []
    got = uc.check(
        environ=env, fetcher=fetcher_for(manifest(), calls), version="0.7.0", now=1000.0 + 23 * 3600
    )
    assert calls == [], "24 小时内又发请求了"
    assert got["source"] == "cache" and got["status"] == "available"

    # 满 24 小时才再问
    calls = []
    uc.check(
        environ=env, fetcher=fetcher_for(manifest(), calls), version="0.7.0", now=1000.0 + 25 * 3600
    )
    assert len(calls) == 1


def test_force_ignores_the_cache(uc, tmp_path):
    env = {"TAVOTTO_CONFIG_DIR": str(tmp_path)}
    uc.check(environ=env, fetcher=fetcher_for(manifest()), version="0.7.0", now=1000.0)
    calls = []
    uc.check(
        environ=env, fetcher=fetcher_for(manifest(), calls), version="0.7.0", now=1001.0, force=True
    )
    assert len(calls) == 1


def test_network_failure_falls_back_to_the_last_good_answer(uc, tmp_path):
    env = {"TAVOTTO_CONFIG_DIR": str(tmp_path)}
    uc.check(environ=env, fetcher=fetcher_for(manifest()), version="0.7.0", now=1000.0)
    got = uc.check(environ=env, fetcher=fetcher_for(None), version="0.7.0", now=1000.0 + 25 * 3600)
    assert got["status"] == "available"  # 断网不该让提醒凭空消失
    assert got["source"] == "cache"


def test_network_failure_without_a_cache_says_nothing(uc, tmp_path):
    """问不到又没缓存：闭嘴。绝不报错，也绝不假装「已是最新」。"""
    env = {"TAVOTTO_CONFIG_DIR": str(tmp_path)}
    got = uc.check(environ=env, fetcher=fetcher_for(None), version="0.7.0", now=1000.0)
    assert got["status"] == "unknown"
    assert got["latest_version"] is None


def test_failure_backs_off_but_not_for_a_whole_day(uc, tmp_path):
    """失败后 1 小时内不再请求——离线的人不该每次画图都白等 1.5 秒；
    但也不该为一次超时等满一天。"""
    env = {"TAVOTTO_CONFIG_DIR": str(tmp_path)}
    uc.check(environ=env, fetcher=fetcher_for(None), version="0.7.0", now=1000.0)
    calls = []
    uc.check(environ=env, fetcher=fetcher_for(None, calls), version="0.7.0", now=1000.0 + 600)
    assert calls == []
    calls = []
    uc.check(
        environ=env, fetcher=fetcher_for(manifest(), calls), version="0.7.0", now=1000.0 + 3700
    )
    assert len(calls) == 1


def test_changing_the_url_invalidates_the_cache(uc, tmp_path):
    env = {"TAVOTTO_CONFIG_DIR": str(tmp_path)}
    uc.check(environ=env, fetcher=fetcher_for(manifest()), version="0.7.0", now=1000.0)
    calls = []
    uc.check(
        environ={**env, "TAVOTTO_UPDATE_URL": "https://elsewhere/x.json"},
        fetcher=fetcher_for(manifest(), calls),
        version="0.7.0",
        now=1001.0,
    )
    assert len(calls) == 1 and calls[0][0] == "https://elsewhere/x.json"


def test_corrupt_cache_is_not_a_crash(uc, tmp_path):
    (tmp_path / "codex-plugin-update.json").write_text("{ 不是 JSON", encoding="utf-8")
    env = {"TAVOTTO_CONFIG_DIR": str(tmp_path)}
    got = uc.check(environ=env, fetcher=fetcher_for(manifest()), version="0.7.0", now=1000.0)
    assert got["status"] == "available"


_is_root = getattr(os, "geteuid", lambda: -1)() == 0


@pytest.mark.skipif(
    os.name == "nt" or _is_root, reason="Windows 上 chmod 挡不住写入；root 无视权限位"
)
def test_readonly_cache_dir_is_not_a_crash(uc, tmp_path):
    """配置目录只读时照样能用，只是每次都问一遍。"""
    readonly = tmp_path / "ro"
    readonly.mkdir()
    readonly.chmod(0o500)
    try:
        got = uc.check(
            environ={"TAVOTTO_CONFIG_DIR": str(readonly)},
            fetcher=fetcher_for(manifest()),
            version="0.7.0",
            now=1000.0,
        )
    finally:
        readonly.chmod(0o700)
    assert got["status"] == "available"


def test_a_garbage_config_dir_is_not_a_crash(uc, tmp_path):
    """`TAVOTTO_CONFIG_DIR` 是用户给的，可能压根不是个合法路径。

    `os.makedirs` 对空字节抛的是 ValueError 不是 OSError——只接 OSError
    的话，一个环境变量就能让「画张图」整个失败。
    """
    got = uc.check(
        environ={"TAVOTTO_CONFIG_DIR": str(tmp_path / "\0bad")},
        fetcher=fetcher_for(manifest()),
        version="0.7.0",
        now=1000.0,
    )
    assert got["status"] == "available"


def test_the_cache_never_lands_in_the_plugin_directory(uc, tmp_path):
    """插件目录归 Codex 管，可能只读，升级时还会被整个换掉。"""
    env = {"TAVOTTO_CONFIG_DIR": str(tmp_path)}
    before = {p for p in (ROOT / "codex-plugin").rglob("*")}
    uc.check(environ=env, fetcher=fetcher_for(manifest()), version="0.7.0", now=1000.0)
    assert {p for p in (ROOT / "codex-plugin").rglob("*")} == before
    assert str(SCRIPTS) not in uc.cache_path(env)


# ------------------------------ 开关与地址 -------------------------------
def test_disable_env_sends_nothing(uc, tmp_path):
    calls = []
    for value in ("1", "true", "yes", "on"):
        got = uc.check(
            environ={"TAVOTTO_CONFIG_DIR": str(tmp_path), "TAVOTTO_DISABLE_UPDATE_CHECK": value},
            fetcher=fetcher_for(manifest(), calls),
            version="0.7.0",
        )
        assert got["status"] == "disabled"
    assert calls == [], "关掉了还发请求"


def test_custom_url_env_is_honoured(uc, tmp_path):
    calls = []
    uc.check(
        environ={
            "TAVOTTO_CONFIG_DIR": str(tmp_path),
            "TAVOTTO_UPDATE_URL": "https://mirror.internal/plugin.json",
        },
        fetcher=fetcher_for(manifest(), calls),
        version="0.7.0",
    )
    assert calls[0][0] == "https://mirror.internal/plugin.json"


def test_default_url_is_a_release_asset(uc):
    """默认地址跟着 Release 走，不是某个分支的当前内容。"""
    assert uc.DEFAULT_URL.startswith("https://github.com/Tavotto/Tavotto/releases/")
    assert uc.DEFAULT_URL.endswith(".json")


def test_network_timeout_is_short(uc):
    """1–2 秒。用户在等着看图，不是在等我们问版本号。"""
    assert 0 < uc.TIMEOUT <= 2.0


def test_manifest_from_another_schema_is_ignored(uc, tmp_path):
    env = {"TAVOTTO_CONFIG_DIR": str(tmp_path)}
    got = uc.check(
        environ=env, version="0.7.0", now=1000.0, fetcher=fetcher_for(None)
    )  # fetch 自己会挡掉 schema
    assert got["status"] == "unknown"
    # fetch 层的判据
    assert uc.SCHEMA == 1


def test_real_fetch_rejects_a_wrong_schema(uc, tmp_path):
    bad = tmp_path / "m.json"
    bad.write_text(json.dumps({"schema": 99, "latest_version": "9.9.9"}), encoding="utf-8")
    assert uc.fetch(bad.as_uri(), timeout=2.0) is None
    good = tmp_path / "g.json"
    good.write_text(json.dumps(manifest()), encoding="utf-8")
    assert uc.fetch(good.as_uri(), timeout=2.0)["latest_version"] == "0.9.9"


def test_real_fetch_survives_a_dead_address(uc, tmp_path):
    assert uc.fetch((tmp_path / "nope.json").as_uri(), timeout=2.0) is None
    assert uc.fetch("http://127.0.0.1:1/x.json", timeout=1.0) is None


# --------------------- 插件版本 ≠ Tavotto 版本 ---------------------------
def test_min_tavotto_version_is_compared_against_tavotto_not_the_plugin(uc, tmp_path):
    """两个版本号各有各的升级节奏，绝不能混。

    插件 0.7.0、要求 Tavotto ≥ 0.7.0：本机 Tavotto 是 0.6.0 就该提示升级
    **Tavotto**；插件自己的版本一个字都不该被拿来顶替它。
    """
    env = {"TAVOTTO_CONFIG_DIR": str(tmp_path)}
    payload = manifest(min_tavotto_version="0.7.0")
    got = uc.check(
        environ=env,
        fetcher=fetcher_for(payload),
        version="0.7.0",
        tavotto_version="0.6.0",
        now=1000.0,
    )
    assert got["tavotto"] == {
        "status": "too_old",
        "current_version": "0.6.0",
        "required_version": "0.7.0",
    }
    assert "Tavotto 是 0.6.0" in uc.tavotto_hint(got)


def test_new_enough_tavotto_is_not_nagged(uc, tmp_path):
    env = {"TAVOTTO_CONFIG_DIR": str(tmp_path)}
    got = uc.check(
        environ=env,
        fetcher=fetcher_for(manifest(min_tavotto_version="0.7.0")),
        version="0.7.0",
        tavotto_version="0.7.0",
        now=1000.0,
    )
    assert "tavotto" not in got


def test_unknown_tavotto_version_says_nothing(uc, tmp_path):
    """发现链是从 PATH 走的时候拿不到版本——那就别猜。"""
    env = {"TAVOTTO_CONFIG_DIR": str(tmp_path)}
    got = uc.check(
        environ=env,
        fetcher=fetcher_for(manifest(min_tavotto_version="9.9.9")),
        version="0.7.0",
        tavotto_version=None,
        now=1000.0,
    )
    assert "tavotto" not in got


# -------------------------------- 提示语 ---------------------------------
def test_hint_only_speaks_when_there_is_an_update(uc):
    assert uc.hint({"status": "current"}) is None
    assert uc.hint({"status": "unknown"}) is None
    assert uc.hint({}) is None
    text = uc.hint(
        {
            "status": "available",
            "latest_version": "0.7.1",
            "current_version": "0.7.0",
            "upgrade_command": "codex plugin marketplace upgrade tavotto",
            "release_notes_url": "https://example.com/n",
        }
    )
    assert "0.7.1" in text and "0.7.0" in text and "upgrade tavotto" in text


# ------------------------- 与 handoff.py 的接线 ---------------------------
def _run_handoff(tmp_path, target, env_extra, *args):
    env = {
        **os.environ,
        "TAVOTTO_CONFIG_DIR": str(tmp_path / "cfg"),
        "TAVOTTO_DATA_DIR": str(tmp_path / "data"),
        **env_extra,
    }
    env.pop("TAVOTTO_CLI", None)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "handoff.py"), str(target), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


@pytest.fixture()
def project(tmp_path):
    d = tmp_path / "figures"
    d.mkdir()
    (d / "Fig1.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    return d


@pytest.mark.skipif(not (ROOT / "src" / "tavotto" / "__init__.py").is_file(), reason="需要源码树")
def test_handoff_stdout_stays_one_parseable_json_line(tmp_path, project, serve_manifest):
    """**最重要的一条**：提醒绝不能把那行 JSON 弄脏。

    调用方读的是 stdout 的最后一行。往 stdout 里 print 一句「有新版本」，
    整条链路当场从「能用」变成「json.loads 报错」。
    """
    url = serve_manifest(manifest("99.0.0"))
    proc = _run_handoff(
        tmp_path,
        project / "Fig1.pdf",
        {"TAVOTTO_UPDATE_URL": url, "TAVOTTO_CLI": str(tmp_path / "没有这个 CLI")},
    )
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 1, f"stdout 不止一行: {lines}"
    out = json.loads(lines[0])
    assert out["update"]["status"] == "available"
    assert out["update"]["latest_version"] == "99.0.0"
    # 人话只在 stderr
    assert "有新版本" in proc.stderr
    assert "有新版本" not in proc.stdout


@pytest.mark.skipif(not (ROOT / "src" / "tavotto" / "__init__.py").is_file(), reason="需要源码树")
def test_handoff_omits_the_field_entirely_when_disabled(tmp_path, project):
    proc = _run_handoff(
        tmp_path,
        project / "Fig1.pdf",
        {"TAVOTTO_DISABLE_UPDATE_CHECK": "1", "TAVOTTO_CLI": str(tmp_path / "没有这个 CLI")},
    )
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "update" not in out
    assert proc.stderr.strip() == "" or "有新版本" not in proc.stderr


@pytest.mark.skipif(not (ROOT / "src" / "tavotto" / "__init__.py").is_file(), reason="需要源码树")
def test_a_broken_update_check_never_breaks_the_handoff(tmp_path, project):
    """更新检查炸了，交接照常。它是提醒，不是功能。"""
    proc = _run_handoff(
        tmp_path,
        project / "Fig1.pdf",
        {
            "TAVOTTO_UPDATE_URL": "http://127.0.0.1:1/x.json",
            "TAVOTTO_CLI": str(tmp_path / "没有这个 CLI"),
        },
    )
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["error_code"] == "cli_exec_failed"  # 该报的还是照报
    assert out.get("update", {}).get("status") in (None, "unknown")


# ------------------------------ 显式入口 ---------------------------------
def test_explicit_entry_point_json(tmp_path, serve_manifest):
    env = {
        **os.environ,
        "TAVOTTO_CONFIG_DIR": str(tmp_path / "cfg"),
        "TAVOTTO_UPDATE_URL": serve_manifest(manifest("1.0.0")),
    }
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "update_check.py"), "--json", "--force"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    # **两条分开写**：`assert A and B` 时 pytest 只报左边那个合取项，于是失败
    # 信息永远是 `assert ('unknown' == 'available')`，看不出 latest_version 是
    # None（问不到）还是 "1.0.0"（问到了但版本号解不出）——而这两种成因的修法
    # 完全不同。失败信息本身也是断言。
    assert out["latest_version"] == "1.0.0", f"清单没取到或解错了: {out}"
    assert out["status"] == "available", f"取到了清单但状态不对: {out}"


def test_explicit_entry_point_human(tmp_path, serve_manifest):
    env = {
        **os.environ,
        "TAVOTTO_CONFIG_DIR": str(tmp_path / "cfg"),
        "TAVOTTO_UPDATE_URL": serve_manifest(manifest(tavotto.__version__)),
    }
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "update_check.py"), "--force"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "已是最新" in proc.stdout


def test_no_download_or_execution_anywhere():
    """第一阶段只提醒。**绝不自动下载安装包、绝不执行它。**

    自动更新一个 Agent 会调用的脚本，等于给远程内容一条到本机执行的路——
    那是另一个量级的决定，不能顺手做掉。
    """
    src = (SCRIPTS / "update_check.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {
        "urlretrieve",
        "extractall",
        "system",
        "Popen",
        "run",
        "call",
        "check_output",
        "exec",
        "eval",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            assert name not in banned, f"第 {node.lineno} 行出现 {name}"
    assert "subprocess" not in src.split('"""', 2)[-1]
