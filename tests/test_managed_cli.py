"""受控 CLI 供应（`engine/managedcli.py`，锁文件 `resources/managed_cli_lock.json`）。

假 HTTP 服务器 + 合成归档：不联网、不下载真 Codex。盯的是——
  * 来源顺序与「慢了换镜像」；hash 不符绝不重试也不换源；
  * 解包成员校验（穿越 / 绝对路径 / 链接 / 炸弹）；解开后整棵树必须与锁一致；
  * 错误码闭集（离线 / TLS / 无此文件 / 校验不符 / 不支持平台 / 磁盘不足）；
  * 装好之后 `ai_agents.resolve` 真能从托管目录找到它（source=managed）；
  * 安装状态机（进度单调、阶段闭集、完成后重新真探测）；锁文件形状与来源主机表同源。
"""

from __future__ import annotations

import gzip
import hashlib
import http.server
import io
import json
import os
import ssl
import tarfile
import threading
import time
import urllib.error
from pathlib import Path

import pytest

from tavotto.engine import ai_agents, ai_bridge, managedcli

ENTRY = "bin/codex.cmd" if os.name == "nt" else "bin/codex"
SCRIPT = b"@echo codex-cli 9.9.9\r\n" if os.name == "nt" else b"#!/bin/sh\necho 'codex-cli 9.9.9'\n"
STRIP = "package/vendor/t/"


def _tar(members: list[tuple[str, bytes | None, dict]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data, extra in members:
            info = tarfile.TarInfo(name)
            for k, v in extra.items():
                setattr(info, k, v)
            if data is None:
                info.type = extra.get("type", tarfile.DIRTYPE)
                tar.addfile(info)
            else:
                info.size = len(data)
                info.mode = extra.get("mode", 0o755 if name.endswith(("codex", ".cmd")) else 0o644)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _files(pad: int = 0) -> dict[str, bytes]:
    files = {ENTRY: SCRIPT, "codex-package.json": b'{"layoutVersion":1}'}
    if pad:
        files["codex-resources/blob.bin"] = os.urandom(pad)  # 不可压缩：让归档有体积
    return files


def _tree_digest(files: dict[str, bytes]) -> str:
    rows = sorted((n, len(d), hashlib.sha256(d).hexdigest()) for n, d in files.items())
    h = hashlib.sha256()
    for rel, size, sha in rows:
        h.update(f"{rel}\t{size}\t{sha}\n".encode())
    return h.hexdigest()


def _archives(files: dict[str, bytes]) -> tuple[bytes, bytes]:
    gh = _tar([(n, d, {}) for n, d in files.items()])
    npm = _tar(
        [("package/package.json", b"{}", {})] + [(STRIP + n, d, {}) for n, d in files.items()]
    )
    return gh, npm


class _Server:
    """按路径回字节的本地服务器；记请求次数；可限速 / 可回 404。"""

    def __init__(self, routes: dict[str, bytes], throttle: dict[str, float] | None = None):
        self.routes = routes
        self.throttle = throttle or {}
        self.hits: dict[str, int] = {}
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401
                pass

            def do_GET(self):  # noqa: N802
                outer.hits[self.path] = outer.hits.get(self.path, 0) + 1
                body = outer.routes.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                delay = outer.throttle.get(self.path, 0.0)
                try:
                    for i in range(0, len(body), 64 * 1024):
                        self.wfile.write(body[i : i + 64 * 1024])
                        if delay:
                            self.wfile.flush()
                            time.sleep(delay)
                except OSError:
                    pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _cli(
    files, gh_bytes, npm_bytes, srv: _Server, *, tree=None, size_gh=None
) -> managedcli.ManagedCli:
    return managedcli.ManagedCli(
        name="codex",
        version="9.9.9",
        platform="test-x64",
        triple="t",
        entrypoint=ENTRY,
        tree_sha256=tree or _tree_digest(files),
        unpacked_size=sum(len(d) for d in files.values()),
        file_count=len(files),
        sources=(
            managedcli.ArchiveSource(
                srv.url("/gh"), size_gh or len(gh_bytes), hashlib.sha256(gh_bytes).hexdigest(), ""
            ),
            managedcli.ArchiveSource(
                srv.url("/npm"), len(npm_bytes), hashlib.sha256(npm_bytes).hexdigest(), STRIP
            ),
        ),
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(managedcli, "DOWNLOAD_ATTEMPTS", 1)
    servers: list[_Server] = []

    def make(routes, throttle=None):
        s = _Server(routes, throttle)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


def _fake_lock_for(monkeypatch, cli):
    monkeypatch.setattr(
        managedcli,
        "cli_for",
        lambda name, platform_key=None, lock=None: cli if name == "codex" else None,
    )


# ---------------------------------------------------------------- 成功路径
def test_provision_from_github_installs_probes_and_points_current(env, monkeypatch):
    files = _files()
    gh, npm = _archives(files)
    srv = env({"/gh": gh, "/npm": npm})
    cli = _cli(files, gh, npm, srv)
    _fake_lock_for(monkeypatch, cli)
    phases: list[str] = []
    entry = managedcli.provision(
        "codex",
        cli=cli,
        on_progress=lambda p, d, t: phases.append(p) if not phases or phases[-1] != p else None,
    )
    assert Path(entry).is_file() and Path(entry).parent.name == "bin"
    assert (
        phases[0] == "download" and phases[-1] == "probe" and set(phases) <= set(managedcli.PHASES)
    )
    assert srv.hits == {"/gh": 1}
    ptr = json.loads((managedcli.root_dir("codex") / "current.json").read_text())
    assert ptr == {"id": cli.id, "version": "9.9.9"}
    assert managedcli.entrypoint("codex") == Path(entry)
    assert managedcli.bin_dir("codex") == Path(entry).parent
    # 下载缓存与 staging 都不留
    root = managedcli.root_dir("codex")
    assert [p.name for p in (root / ".downloads").iterdir()] == []
    assert not [p for p in root.iterdir() if p.name.startswith(".staging-")]
    # 再来一次：不联网
    assert managedcli.provision("codex", cli=cli) == entry
    assert srv.hits == {"/gh": 1}


def test_mirror_archive_is_a_different_file_but_the_same_tree(env, monkeypatch):
    """GitHub 失败（404）→ npmmirror 的 tgz（不同字节、带 package/vendor 前缀）解开后是同一棵树。"""
    files = _files()
    gh, npm = _archives(files)
    assert hashlib.sha256(gh).digest() != hashlib.sha256(npm).digest()
    srv = env({"/npm": npm})  # /gh → 404
    cli = _cli(files, gh, npm, srv)
    entry = managedcli.provision("codex", cli=cli)
    assert Path(entry).is_file()
    assert srv.hits == {"/gh": 1, "/npm": 1}


def test_slow_primary_switches_to_mirror(env, monkeypatch):
    files = _files(pad=700_000)
    gh, npm = _archives(files)
    srv = env({"/gh": gh, "/npm": npm}, throttle={"/gh": 0.15})
    cli = _cli(files, gh, npm, srv)
    monkeypatch.setattr(managedcli, "SLOW_GRACE_S", 0.3)
    monkeypatch.setattr(managedcli, "SLOW_ETA_S", 0.5)
    entry = managedcli.provision("codex", cli=cli)
    assert Path(entry).is_file()
    assert srv.hits.get("/npm") == 1 and srv.hits.get("/gh") == 1  # 主源只试了一次就放弃


# ---------------------------------------------------------------- 拒绝路径
def test_hash_mismatch_is_not_retried_and_not_switched(env, monkeypatch):
    monkeypatch.setattr(managedcli, "DOWNLOAD_ATTEMPTS", 3)
    files = _files()
    gh, npm = _archives(files)
    tampered = gh[:-1] + bytes([gh[-1] ^ 0xFF])
    srv = env({"/gh": tampered, "/npm": npm})
    cli = _cli(files, gh, npm, srv)
    with pytest.raises(managedcli.ManagedCliError) as exc:
        managedcli.provision("codex", cli=cli)
    assert exc.value.code == managedcli.ERROR_HASH_MISMATCH
    assert srv.hits == {"/gh": 1}  # 不重试、不换镜像
    assert managedcli.entrypoint("codex", cli) is None
    assert not (managedcli.root_dir("codex") / "current.json").exists()


def test_tree_mismatch_after_extract_is_hash_mismatch(env):
    files = _files()
    gh, npm = _archives(files)
    srv = env({"/gh": gh, "/npm": npm})
    cli = _cli(files, gh, npm, srv, tree="0" * 64)
    with pytest.raises(managedcli.ManagedCliError) as exc:
        managedcli.provision("codex", cli=cli)
    assert exc.value.code == managedcli.ERROR_HASH_MISMATCH
    assert not (managedcli.root_dir("codex") / cli.id).exists()


@pytest.mark.parametrize(
    "bad",
    [
        ("../evil", b"x", {}),
        ("/abs/evil", b"x", {}),
        ("a\\b", b"x", {}),
        ("C:evil", b"x", {}),
        ("link", None, {"type": tarfile.SYMTYPE, "linkname": "/etc/passwd"}),
        ("hard", None, {"type": tarfile.LNKTYPE, "linkname": "bin/codex"}),
    ],
)
def test_extract_rejects_traversal_and_links(env, bad):
    files = _files()
    evil = _tar([(n, d, {}) for n, d in files.items()] + [bad])
    srv = env({"/gh": evil, "/npm": evil})
    cli = _cli(files, evil, evil, srv)
    with pytest.raises(managedcli.ManagedCliError) as exc:
        managedcli.provision("codex", cli=cli)
    assert exc.value.code == managedcli.ERROR_INVALID_ARCHIVE
    assert not (managedcli.root_dir("codex") / "evil").exists()
    assert not list(managedcli.root_dir("codex").parent.glob("**/evil"))


def test_extract_rejects_an_archive_bigger_than_the_lock_says(env):
    files = _files()
    big = dict(files, **{"codex-resources/more.bin": b"y" * 5000})
    gh = _tar([(n, d, {}) for n, d in big.items()])
    srv = env({"/gh": gh, "/npm": gh})
    cli = _cli(files, gh, gh, srv)  # 锁里登记的 unpacked_size 来自 files，小于实际
    with pytest.raises(managedcli.ManagedCliError) as exc:
        managedcli.provision("codex", cli=cli)
    assert exc.value.code == managedcli.ERROR_INVALID_ARCHIVE


def test_unsupported_platform_is_a_stable_code(monkeypatch):
    assert managedcli.cli_for("codex", "linux-arm64") is None
    assert managedcli.cli_for("codex", "win32-arm64") is None
    monkeypatch.setattr(managedcli, "host_platform", lambda: None)
    assert managedcli.offered("codex") is False
    with pytest.raises(managedcli.ManagedCliError) as exc:
        managedcli.provision("codex")
    assert exc.value.code == managedcli.ERROR_UNSUPPORTED_PLATFORM


def test_not_enough_disk_stops_before_any_download(env, monkeypatch):
    files = _files()
    gh, npm = _archives(files)
    srv = env({"/gh": gh, "/npm": npm})
    cli = _cli(files, gh, npm, srv)

    class Usage:
        free = 1024

    monkeypatch.setattr(managedcli.shutil, "disk_usage", lambda p: Usage())
    with pytest.raises(managedcli.ManagedCliError) as exc:
        managedcli.provision("codex", cli=cli)
    assert exc.value.code == managedcli.ERROR_DISK_FULL and srv.hits == {}


def test_offline_and_source_unavailable_and_tls(env, monkeypatch):
    files = _files()
    gh, npm = _archives(files)
    srv = env({})
    cli = _cli(files, gh, npm, srv)
    with pytest.raises(managedcli.ManagedCliError) as exc:  # 两个来源都 404
        managedcli.provision("codex", cli=cli)
    assert exc.value.code == managedcli.ERROR_SOURCE_UNAVAILABLE
    srv.close()  # 端口关了：连接被拒
    with pytest.raises(managedcli.ManagedCliError) as exc:
        managedcli.provision("codex", cli=cli)
    assert exc.value.code == managedcli.ERROR_OFFLINE

    def tls_fail(*a, **k):
        raise urllib.error.URLError(ssl.SSLCertVerificationError(1, "bad cert"))

    monkeypatch.setattr(managedcli, "_fetch", tls_fail)
    with pytest.raises(managedcli.ManagedCliError) as exc:
        managedcli.provision("codex", cli=cli)
    assert exc.value.code == managedcli.ERROR_TLS


def test_cancel_before_start_raises_cancelled(env):
    files = _files()
    gh, npm = _archives(files)
    srv = env({"/gh": gh, "/npm": npm})
    ev = threading.Event()
    ev.set()
    with pytest.raises(managedcli.ManagedCliError) as exc:
        managedcli.provision("codex", cli=_cli(files, gh, npm, srv), cancel_ev=ev)
    assert exc.value.code == managedcli.ERROR_CANCELLED and srv.hits == {}


def test_a_version_that_does_not_match_the_lock_is_launch_failed(env):
    files = _files()
    files[ENTRY] = SCRIPT.replace(b"9.9.9", b"1.0.0")
    gh, npm = _archives(files)
    srv = env({"/gh": gh, "/npm": npm})
    with pytest.raises(managedcli.ManagedCliError) as exc:
        managedcli.provision("codex", cli=_cli(files, gh, npm, srv))
    assert exc.value.code == managedcli.ERROR_LAUNCH_FAILED


def test_pointer_cannot_escape_the_managed_directory(env):
    root = managedcli.root_dir("codex")
    root.mkdir(parents=True)
    (root / "current.json").write_text('{"id": "../../etc", "version": "1.2.3"}')
    assert managedcli.entrypoint("codex") is None and managedcli.bin_dir("codex") is None


# ---------------------------------------------------------------- 装好之后能被找到
def test_resolve_finds_the_managed_install_first(env, monkeypatch, tmp_path):
    files = _files()
    gh, npm = _archives(files)
    srv = env({"/gh": gh, "/npm": npm})
    cli = _cli(files, gh, npm, srv)
    _fake_lock_for(monkeypatch, cli)
    monkeypatch.setattr(ai_agents, "path_override", lambda agent_id: None)
    ai_agents.clear_cache()
    agent = next(a for a in ai_agents.agents() if a.id == "codex")
    assert ai_agents.agent_search_locations(agent)[0].source != "managed"  # 还没装
    entry = managedcli.provision("codex", cli=cli)
    ai_agents.clear_cache()
    locs = ai_agents.agent_search_locations(agent)
    assert locs[0].source == "managed" and locs[0].path == str(Path(entry).parent)
    assert ai_agents.candidates(agent)[0].source == "managed"
    res = ai_agents.resolve(agent, probe_readiness=False)
    # Windows 上 which 按 PATHEXT 给出大写扩展名（codex.CMD）：路径按 normcase 比
    assert os.path.normcase(res.path) == os.path.normcase(entry)
    assert res.source == "managed" and "9.9.9" in (res.version or "")
    ai_agents.clear_cache()


# ---------------------------------------------------------------- 安装状态机（ai_bridge）
def _wait_install(agent_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = ai_bridge.install_status(agent_id)
        if st["status"] != "running":
            return st
        time.sleep(0.01)
    raise AssertionError("安装状态机没有收尾")


def test_start_install_prefers_managed_and_reports_monotonic_progress(monkeypatch):
    monkeypatch.setattr(ai_bridge, "_INSTALLS", {})
    monkeypatch.setattr(ai_bridge.managedcli, "offered", lambda name: name == "codex")
    seen: list[dict] = []
    gate = threading.Event()

    def fake_provision(name, on_progress=None, **kw):
        assert gate.wait(5)
        on_progress("download", 50, 100)
        seen.append(ai_bridge.install_status("codex"))
        on_progress("download", 10, 100)  # 换源重来：进度条不倒退
        seen.append(ai_bridge.install_status("codex"))
        on_progress("extract", 100, 100)
        on_progress("probe", 1, 1)
        return "/x/codex"

    monkeypatch.setattr(ai_bridge.managedcli, "provision", fake_provision)
    monkeypatch.setattr(
        ai_bridge,
        "capabilities",
        lambda refresh=False: {"agents": [{"id": "codex", "installed": True}]},
    )
    monkeypatch.setattr(ai_bridge, "_npm_argv", lambda: pytest.fail("托管可用时不许碰 npm"))
    first = ai_bridge.start_install("codex")
    assert first["status"] == "running" and first["via"] == "managed" and "progress" in first
    gate.set()
    st = _wait_install("codex")
    assert st["status"] == "done" and st["progress"] == 1.0 and st["via"] == "managed"
    assert seen[0]["progress"] == seen[1]["progress"] > 0 and seen[0]["phase"] == "download"
    assert set(st) <= {"status", "code", "log", "progress", "phase", "via"}


def test_managed_error_codes_pass_through_and_success_still_needs_a_real_probe(monkeypatch):
    monkeypatch.setattr(ai_bridge, "_INSTALLS", {})
    monkeypatch.setattr(ai_bridge.managedcli, "offered", lambda name: True)

    def boom(name, **kw):
        raise managedcli.ManagedCliError(managedcli.ERROR_OFFLINE, "x")

    monkeypatch.setattr(ai_bridge.managedcli, "provision", boom)
    ai_bridge.start_install("codex")
    st = _wait_install("codex")
    assert st["status"] == "error" and st["code"] == "managed_cli_offline"

    monkeypatch.setattr(ai_bridge.managedcli, "provision", lambda name, **kw: "/x/codex")
    monkeypatch.setattr(
        ai_bridge,
        "capabilities",
        lambda refresh=False: {"agents": [{"id": "codex", "installed": False}]},
    )
    ai_bridge.start_install("codex")
    st = _wait_install("codex")
    assert st["status"] == "error" and st["code"] == "installed_but_not_found"


def test_capabilities_install_block_says_managed_needs_no_npm(monkeypatch):
    monkeypatch.setattr(ai_bridge.managedcli, "offered", lambda name: name == "codex")
    monkeypatch.setattr(ai_bridge, "_npm_argv", lambda: None)
    ai_bridge.invalidate_capabilities()
    try:
        caps = ai_bridge.capabilities(refresh=True)
        by = {a["id"]: a for a in caps["agents"]}
        assert (
            by["codex"]["install"]["managed"] is True
            and by["codex"]["install"]["available"] is True
        )
        assert (
            by["claude"]["install"]["managed"] is False
            and by["claude"]["install"]["available"] is False
        )
    finally:
        ai_bridge.invalidate_capabilities()


# ---------------------------------------------------------------- 锁文件
def test_shipped_lock_is_valid_pinned_and_urls_derive_from_the_template():
    lock = managedcli.load_lock()
    cli = lock["clis"]["codex"]
    assert set(cli["platforms"]) == {"darwin-arm64", "darwin-x64", "win32-x64", "linux-x64"}
    for key in cli["platforms"]:
        c = managedcli.cli_for("codex", key, lock)
        gh, mirror = c.sources
        assert gh.url == (
            f"https://github.com/openai/codex/releases/download/rust-v{c.version}/codex-package-{c.triple}.tar.gz"
        )
        assert (
            mirror.url
            == f"https://registry.npmmirror.com/@openai/codex/-/codex-{c.version}-{key}.tgz"
        )
        assert mirror.strip == f"package/vendor/{c.triple}/" and gh.strip == ""
        assert gh.sha256 != mirror.sha256  # 两份不同的归档，各锁各的
        assert c.id == f"{c.version}-{c.tree_sha256[:12]}"


def test_source_hosts_allowlist_matches_the_shipped_lock():
    lock = managedcli.load_lock()
    hosts = set()
    for key in lock["clis"]["codex"]["platforms"]:
        for s in managedcli.cli_for("codex", key, lock).sources:
            hosts.add(s.host)
    assert hosts == set(managedcli.SOURCE_HOSTS)


def test_validate_lock_rejects_loose_locks():
    import copy

    good = managedcli.load_lock()

    def mutate(fn):
        bad = copy.deepcopy(good)
        fn(bad["clis"]["codex"])
        with pytest.raises(ValueError):
            managedcli.validate_lock(bad)

    mutate(lambda c: c.update(version="latest"))
    mutate(lambda c: c.update(github_url="https://evil.example/{version}/{triple}"))
    mutate(lambda c: c["platforms"]["darwin-arm64"].update(tree_sha256="abc"))
    mutate(lambda c: c["platforms"]["darwin-arm64"]["github"].update(sha256="G" * 64))
    mutate(lambda c: c["platforms"]["darwin-arm64"].update(entrypoint="../x"))
    mutate(lambda c: c["platforms"]["darwin-arm64"]["mirror"].update(integrity="md5-x"))


def test_lock_script_check_passes_on_the_shipped_lock():
    import importlib.util

    script = Path(__file__).resolve().parent.parent / "scripts" / "managed_cli_lock.py"
    spec = importlib.util.spec_from_file_location("managed_cli_lock_script", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--check"]) == 0
    assert gzip  # 保持 import 被使用（归档助手以后可能直接用）


# ---------------------------------------------------------------- 插件安装流水线也认托管那份
def test_codexinstall_and_hostconnect_see_the_managed_codex(env, monkeypatch):
    """只有托管 Codex（PATH / 固定目录都没有）：find_codex 返回托管路径、hostconnect.installed 为真、
    `_codex_or_fail` 不在第一步阻断。看护「托管路径只有 `ai_agents.managed_paths` 一份」。"""
    from tavotto.engine import codexinstall, hostconnect

    files = _files()
    gh, npm = _archives(files)
    srv = env({"/gh": gh, "/npm": npm})
    cli = _cli(files, gh, npm, srv)
    _fake_lock_for(monkeypatch, cli)
    monkeypatch.setattr(ai_agents, "path_override", lambda agent_id: None)
    monkeypatch.setenv("PATH", "/nonexistent-path-for-test")
    monkeypatch.setattr(codexinstall, "_search_dirs", lambda: [])
    monkeypatch.setattr(codexinstall, "_is_windows", lambda: False)
    ai_agents.clear_cache()
    assert codexinstall.find_codex()[0] is None  # 还没装
    assert hostconnect.installed("codex") is False
    entry = managedcli.provision("codex", cli=cli)
    ai_agents.clear_cache()
    found, _searched = codexinstall.find_codex()
    assert found is not None and Path(found).parent == Path(entry).parent
    assert hostconnect.installed("codex") is True
    steps: list[dict] = []
    assert codexinstall._codex_or_fail(steps) == found
    assert steps[0]["ok"] is True
    ai_agents.clear_cache()
