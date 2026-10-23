"""AI 修复工具箱 D1（ADR 0149）：注册表 · 读取范围 · 「看」「试」工具 · 本地审计。

判据的主语：

* 读取范围：**`ReadScope.check` / 工具返回的内容**，用假主目录（`tmp_path/home`）里真实存在的文件。从不读真实的 `~/.ssh` 等：
  每条用例都把 `HOME` / `USERPROFILE` / 数据目录 / 配置目录指到 tmp_path。
* 脱敏：给模型的**返回文本**里没有金丝雀，且先证明金丝雀确实在磁盘文件里（否则「没有」可能只是没读到）。
* 审计：**磁盘上的 jsonl**（字段齐、内容不落盘——金丝雀串先证明到过工具参数里）。
* 「试」：真实子进程（当前解释器），断言用户目录逐字节不变。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tavotto.engine.agent_runner import tools as runner_tools, wires
from tavotto.engine.fixtools import (
    REGISTRY,
    AuditLog,
    FixContext,
    build_agent_tools,
    observe,
    readscope,
    run_tool,
    trial,
)

SECRET = "sk-ant-api03-CANARYsecretValue1234567890"


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = home / "proj"
    proj.mkdir(parents=True)
    cfg = tmp_path / "cfg"
    data = tmp_path / "data"
    cfg.mkdir()
    data.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    (cfg / "config.json").write_text('{"ai": {"providers": []}}', encoding="utf-8")
    return type("Box", (), {"home": home, "proj": proj, "cfg": cfg, "data": data, "tmp": tmp_path})


def make_ctx(box, **kw) -> FixContext:
    kw.setdefault("case_insensitive", False)
    return FixContext.for_fix_session("s1", box.proj, home=box.home, **kw)


def call(ctx, name, args, audit=None):
    return run_tool(REGISTRY[name], ctx, args, audit)


def put(path: Path, text: str | bytes = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


# =========================================================================== readscope
def test_plain_files_under_fake_home_are_readable(box):
    f = put(box.home / "notes" / "a.txt", "hello")
    ctx = make_ctx(box)
    r = call(ctx, "read_file", {"path": str(f)})
    assert not r.is_error and "hello" in r.content


DENIED_REL = [
    ".ssh/id_rsa",
    ".ssh/config",
    ".gnupg/secring.gpg",
    ".aws/credentials",
    ".azure/accessTokens.json",
    ".kube/config",
    ".docker/config.json",
    ".config/gcloud/credentials.db",
    "Library/Keychains/login.keychain-db",
    ".netrc",
    ".pypirc",
    ".git-credentials",
    ".codex/auth.json",
    ".claude/.credentials.json",
    "Library/Application Support/Google/Chrome/Default/Cookies",
    "Library/Application Support/Firefox/Profiles/x/logins.json",
    "Library/Safari/History.db",
    ".config/google-chrome/Default/Login Data",
    ".mozilla/firefox/x/key4.db",
    "proj/.env",
    "proj/.env.production",
    "proj/sub/server.pem",
    "proj/sub/tls.key",
    "proj/sub/store.p12",
    "proj/sub/id_ed25519",
    "elsewhere/.env",
]


@pytest.mark.parametrize("rel", DENIED_REL)
def test_every_deny_class_is_refused_even_though_the_file_exists(box, rel):
    f = put(box.home / rel, "TOP-SECRET")
    assert f.exists()
    ctx = make_ctx(box)
    r = call(ctx, "read_file", {"path": str(f)})
    assert r.is_error and r.code == "denied" and "TOP-SECRET" not in r.content
    assert ctx.reads == []  # 被拒的读取不算读到


def test_tavotto_own_config_file_is_denied(box):
    ctx = make_ctx(box)
    r = call(ctx, "read_file", {"path": str(box.cfg / "config.json")})
    assert r.is_error and r.code in ("denied", "outside_scope")
    # 也不能经"配置目录在主目录下"的巧合放行：在主目录下的配置目录同样拦
    cfg_in_home = box.home / "Library" / "Application Support" / "Tavotto"
    put(cfg_in_home / "config.json", '{"api_key": "sk-abc"}')
    ctx2 = make_ctx(box, extra_deny=[cfg_in_home / "config.json"])
    r2 = call(ctx2, "read_file", {"path": str(cfg_in_home / "config.json")})
    assert r2.is_error and r2.code == "denied"


def test_public_key_is_not_a_secret_shape(box):
    f = put(box.proj / "id_rsa.pub", "ssh-rsa AAAA")
    assert not call(make_ctx(box), "read_file", {"path": str(f)}).is_error


def test_case_variants_are_denied_on_case_insensitive_platforms(box):
    # 不依赖文件系统是否真大小写不敏感：变体路径可以不存在，判据只看规范化后的写法
    ctx = make_ctx(box, case_insensitive=True)
    variants = (
        box.home / ".SSH" / "config",
        box.home / ".Aws" / "credentials",
        box.home / "LIBRARY" / "keychains" / "x",
        box.proj / ".ENV",
    )
    for p in variants:
        r = call(ctx, "read_file", {"path": str(p)})
        assert r.is_error and r.code == "denied", p


def test_dotdot_cannot_walk_around_the_deny_list(box):
    put(box.home / ".ssh" / "id_rsa", "S")
    ctx = make_ctx(box)
    r = call(ctx, "read_file", {"path": str(box.proj / ".." / ".ssh" / "id_rsa")})
    assert r.is_error and r.code == "denied"
    r = call(ctx, "read_file", {"path": "../.ssh/id_rsa"})  # 相对项目根
    assert r.is_error and r.code == "denied"
    r = call(ctx, "read_file", {"path": "~/.ssh/id_rsa"})
    assert r.is_error and r.code == "denied"


def test_symlink_into_a_denied_area_is_denied(box):
    secret = put(box.home / ".ssh" / "id_rsa", "S")
    (box.proj / "notes.txt").symlink_to(secret)
    (box.proj / "dirlink").symlink_to(box.home / ".ssh", target_is_directory=True)
    ctx = make_ctx(box)
    for p in ("notes.txt", "dirlink/id_rsa", "dirlink/config"):
        r = call(ctx, "read_file", {"path": p})
        assert r.is_error and r.code == "denied", p
    # 名字是 .env 的链接，词法上就拦
    ok_target = put(box.proj / "harmless.txt", "fine")
    (box.proj / ".env").symlink_to(ok_target)
    assert call(ctx, "read_file", {"path": ".env"}).code == "denied"


def test_paths_outside_home_project_and_env_are_refused(box):
    sysdir = put(box.tmp / "system" / "etc" / "hosts", "127.0.0.1")
    ctx = make_ctx(box)
    r = call(ctx, "read_file", {"path": str(sysdir)})
    assert r.is_error and r.code == "outside_scope"
    assert call(ctx, "read_file", {"path": "/etc/passwd"}).code == "outside_scope"


def test_environment_prefix_outside_home_is_readable_but_its_neighbour_is_not(box):
    prefix = box.tmp / "opt" / "conda" / "envs" / "x"
    lib = put(prefix / "lib" / "site.txt", "pkg")
    other = put(box.tmp / "opt" / "other" / "f.txt", "no")
    ctx = make_ctx(box, env_prefixes=[prefix])
    assert "pkg" in call(ctx, "read_file", {"path": str(lib)}).content
    assert call(ctx, "read_file", {"path": str(other)}).code == "outside_scope"
    assert [r["path"] for r in ctx.reads] == [os.path.realpath(lib)]


def test_project_outside_home_is_allowed(box):
    proj = box.tmp / "work" / "paper"
    f = put(proj / "a.py", "print(1)")
    ctx = FixContext(session_id="s", project_root=proj, home=box.home, case_insensitive=False)
    assert "print(1)" in call(ctx, "read_file", {"path": str(f)}).content
    assert ctx.reads == []


def test_reads_list_records_only_reads_outside_the_project(box):
    inside = put(box.proj / "a.py", "1")
    outside = put(box.home / "other" / "b.txt", "2")
    ctx = make_ctx(box)
    call(ctx, "read_file", {"path": str(inside)})
    assert ctx.reads == []
    call(ctx, "read_file", {"path": str(outside)})
    call(ctx, "list_dir", {"path": str(box.home / "other")})
    assert [(r["tool"], r["path"]) for r in ctx.reads] == [
        ("read_file", os.path.realpath(outside)),
        ("list_dir", os.path.realpath(box.home / "other")),
    ]


def test_read_is_capped_at_256_kib_with_a_truncation_note(box):
    line = "ghijklmnop" * 9 + "\n"  # 91 字节
    big = put(box.proj / "big.txt", line * 4000)  # ~364 KB
    ctx = make_ctx(box)
    r = call(ctx, "read_file", {"path": str(big)})
    assert not r.is_error and "[truncated" in r.content
    body = r.content.split("\n[truncated")[0]
    assert len(body.encode()) <= readscope.MAX_READ_BYTES
    assert body.count("\n") >= 2000  # 给的是前 N 行，不是几个字节
    # 续读
    r2 = call(ctx, "read_file", {"path": str(big), "start_line": 3000, "max_lines": 2})
    assert r2.content.count("ghijklmnop") == 18 and "[truncated" in r2.content


def test_binary_file_reports_type_and_size_only(box):
    png = put(box.proj / "a.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    r = call(make_ctx(box), "read_file", {"path": str(png)})
    assert not r.is_error and "PNG image" in r.content and "108 bytes" in r.content
    assert "\x89" not in r.content


def test_read_content_is_scrubbed_of_secret_shapes(box):
    f = put(
        box.proj / "settings.py",
        f'API_KEY = "{SECRET}"\nheader = "Bearer abcdefghijklmnop1234"\nlr = 0.1\n',
    )
    assert SECRET in f.read_text()  # 先证明金丝雀确实在磁盘上
    r = call(make_ctx(box), "read_file", {"path": str(f)})
    assert SECRET not in r.content and "CANARY" not in r.content
    assert "abcdefghijklmnop1234" not in r.content
    assert "lr = 0.1" in r.content  # 普通内容原样


@pytest.mark.parametrize(
    ("raw", "secret", "kept"),
    [
        ("password=correcthorsebattery", "correcthorsebattery", "password=***"),
        ("password = correcthorsebattery", "correcthorsebattery", "password = ***"),
        ("PASSWORD: correcthorsebattery", "correcthorsebattery", "PASSWORD: ***"),
        ('password = "correct horse battery"', "horse", 'password = "***"'),
        ("api_key: 'hunter2'", "hunter2", "api_key: '***'"),
        ('{"password": "x"}', '"x"', '{"password": "***"}'),
        ('{"auth_token": "abc def ghi"}', "abc def", '{"auth_token": "***"}'),
        ("--password hunter22 -v", "hunter22", "--password *** -v"),
        ("--api-key=abcdefghij", "abcdefghij", "--api-key=***"),
        ('--password "correct horse battery" -v', "horse", "--password *** -v"),
        ("--api-key 'long secret' -v", "secret'", "--api-key *** -v"),
        ('--token="x y" -v', "x y", "--token=*** -v"),
        ("--TOKEN='x y' -v", "x y", "--TOKEN=*** -v"),
        ("-p 'secret words' -v", "secret", "-p *** -v"),
        ('--Access_Key "a b c"', "a b", "--Access_Key ***"),
        ("--private-key 'k k k'", "k k", "--private-key ***"),
        ("--credentials=abc", "abc", "--credentials=***"),
        ("--pwd=hunter22", "hunter22", "--pwd=***"),
        ("--auth bearerthing", "bearerthing", "--auth ***"),
        ('secret = "it\'s fine"', "fine", 'secret = "***"'),
    ],
)
def test_key_value_secrets_keep_the_key_and_drop_the_whole_value(raw, secret, kept):
    out = readscope.scrub(raw)
    assert secret not in out and "***" in out
    assert kept in out and '***"***' not in out and "****" not in out
    assert readscope.scrub(out) == out  # 幂等


@pytest.mark.parametrize(
    "benign",
    [
        '--output "my file.png"',
        "-p 8080",
        "--dpi 300 --format 'svg and png'",
        "author: Someone Quite Long",
        "docker login --password-stdin",
        "max_tokens = 4096",
    ],
)
def test_non_sensitive_flags_and_values_are_not_masked(benign):
    assert readscope.scrub(benign) == benign


def test_every_scrub_rule_drops_the_captured_secret_itself():
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowCANARY\n-----END RSA PRIVATE KEY-----"
    for raw, canary in [
        ("Authorization: Bearer abcdefghijklmnop1234", "abcdefghijklmnop1234"),
        (key, "MIIEowCANARY"),
        ("x sk-abcdefghij12345 y", "abcdefghij12345"),
        ("ghp_abcdefghij1234567890", "abcdefghij1234567890"),
        (
            "AKIA" + "ABCDEFGHIJKLMNOP",
            "ABCDEFGHIJKLMNOP",
        ),  # 拼接：源码里不留完整的密钥形状（推送保护）
    ]:
        assert canary not in readscope.scrub(raw), raw
    assert readscope.scrub("max_tokens = 4096 lr = 0.1") == "max_tokens = 4096 lr = 0.1"


def test_private_key_block_is_masked(box):
    f = put(
        box.proj / "notes.txt",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----\nafter",
    )
    r = call(make_ctx(box), "read_file", {"path": str(f)})
    assert "MIIEow" not in r.content and "after" in r.content


# =========================================================================== observe
def test_read_file_failures(box):
    ctx = make_ctx(box)
    assert call(ctx, "read_file", {"path": "missing.py"}).code == "not_a_file"
    assert call(ctx, "read_file", {"path": ""}).code == "bad_path"
    assert call(ctx, "read_file", {}).code == "bad_path"
    assert call(ctx, "read_file", {"path": str(box.proj)}).code == "not_a_file"


def test_list_dir_lists_and_hides_protected_entries(box):
    put(box.proj / "a.py")
    put(box.proj / ".env", "S")
    put(box.proj / "sub" / "b.py")
    r = call(make_ctx(box), "list_dir", {"path": "."})
    assert "a.py" in r.content and "sub/" in r.content
    assert ".env" not in r.content and "1 protected" in r.content
    assert call(make_ctx(box), "list_dir", {"path": "nope"}).code == "not_a_dir"


def test_find_files_globs_and_skips_big_dirs_and_protected(box):
    put(box.proj / "a.py")
    put(box.proj / "sub" / "b.py")
    put(box.proj / "node_modules" / "x" / "c.py")
    put(box.proj / ".git" / "d.py")
    put(box.proj / "key.pem")
    ctx = make_ctx(box)
    r = call(ctx, "find_files", {"root": ".", "glob": "*.py"})
    assert sorted(r.content.splitlines()) == ["a.py", "sub/b.py"]
    assert "key.pem" not in call(ctx, "find_files", {"root": ".", "glob": "*"}).content
    r = call(ctx, "find_files", {"root": ".", "glob": "*.py", "limit": 1})
    assert "showing the first 1" in r.content
    assert call(ctx, "find_files", {"root": str(box.home / ".ssh")}).code == "denied"
    assert "(no matches)" in call(ctx, "find_files", {"root": ".", "glob": "*.zzz"}).content


def test_find_does_not_follow_a_directory_symlink_into_a_denied_area(box):
    put(box.home / ".ssh" / "leak.txt", "S")
    (box.proj / "dl").symlink_to(box.home / ".ssh", target_is_directory=True)
    r = call(make_ctx(box), "find_files", {"root": ".", "glob": "*"})
    assert "leak.txt" not in r.content


def test_find_and_grep_stop_at_the_time_limit(box, monkeypatch):
    for i in range(5):
        put(box.proj / f"f{i}.py", "needle")
    monkeypatch.setattr(observe, "SCAN_SECONDS", -1.0)
    ctx = make_ctx(box)
    assert "time limit" in call(ctx, "find_files", {"root": "."}).content
    assert "time limit" in call(ctx, "grep_files", {"root": ".", "pattern": "needle"}).content


def test_grep_matches_limits_and_scrubs(box):
    put(box.proj / "a.py", f"x = 1\nkey = '{SECRET}'\nneedle here\n")
    put(box.proj / "b.bin", b"needle\x00\x01")
    put(box.proj / ".env", "needle")
    ctx = make_ctx(box)
    r = call(ctx, "grep_files", {"root": ".", "pattern": "needle"})
    assert r.content.strip() == "a.py:3: needle here"
    r = call(ctx, "grep_files", {"root": ".", "pattern": "key"})
    assert "a.py:2" in r.content and SECRET not in r.content
    put(box.proj / "many.txt", "m\n" * 20)
    r = call(ctx, "grep_files", {"root": ".", "pattern": "m", "glob": "many.txt", "limit": 3})
    assert len(r.content.splitlines()) == 4 and "first 3" in r.content
    assert (
        call(ctx, "grep_files", {"root": ".", "pattern": "("}).content == "(no matches)"
    )  # 字面量
    assert call(ctx, "grep_files", {"root": "."}).code == "bad_arguments"


def test_read_run_report_precis_and_absence(box):
    ctx = make_ctx(box)
    assert call(ctx, "read_run_report", {}).code == "no_report"
    long_err = "\n".join(f"line {i}" for i in range(500))
    ctx.report = lambda: {
        "phase": "failed",
        "outcome": {"kind": "failed", "code": "missing_module"},
        "error_tail": long_err,
        "attempt_history": [{"n": i} for i in range(30)],
        "remedy": {"kind": "install", "token": SECRET},
        "plan": {"huge": "x" * 100000},  # 不在精简版里
    }
    r = call(ctx, "read_run_report", {})
    body = json.loads(r.content)
    assert "plan" not in body and body["phase"] == "failed"
    assert len(body["attempt_history"]) == 12
    assert "line 499" in body["error_tail"] and "line 0\n" not in body["error_tail"]
    assert SECRET not in r.content


def test_read_logs_tail_scrubbed_and_validated(box):
    log = box.data / "cache" / "app.log"
    put(log, "\n".join(f"l{i}" for i in range(200)) + f"\ntoken={SECRET}\n")
    ctx = make_ctx(box)
    r = call(ctx, "read_logs", {"source": "app", "lines": 5})
    assert r.content.splitlines()[0] == "l196" and SECRET not in r.content
    assert call(ctx, "read_logs", {"source": "worker"}).code == "no_log"
    assert call(ctx, "read_logs", {"source": "etc"}).code == "bad_arguments"
    ctx.log_paths["worker"] = box.data / "w.log"
    put(box.data / "w.log", "w1\nw2")
    assert call(ctx, "read_logs", {"source": "worker"}).content == "w1\nw2"


def test_lookup_package_uses_the_curated_table_offline(box):
    ctx = make_ctx(box)
    assert "opencv-python" in call(ctx, "lookup_package", {"name": "cv2"}).content
    r = call(ctx, "lookup_package", {"name": "totallyunknownmod"})
    assert not r.is_error and "not looked up online" in r.content
    assert call(ctx, "lookup_package", {"name": "a b; rm"}).code == "bad_arguments"


def test_list_environments_registers_ids_and_shows_rejection_reasons(box):
    entries = [
        {"python": sys.executable, "source": "project", "label": "mine", "satisfies": True},
        {
            "python": "/nonexistent/python",
            "source": "conda",
            "label": "old",
            "satisfies": False,
            "ok": True,
            "missing": ["scipy"],
        },
    ]
    ctx = make_ctx(box, environment_source=lambda: entries)
    r = call(ctx, "list_environments", {})
    rows = json.loads(r.content)
    assert [x["status"] for x in rows] == ["usable", "missing_imports"]
    assert rows[1]["missing"] == ["scipy"]
    assert set(ctx.environments) == {x["id"] for x in rows}
    assert "/nonexistent" not in r.content  # id 不透明、不出路径


def test_inspect_environment_real_subprocess_and_unknown_id(box):
    ctx = make_ctx(box, environments={"env-1": sys.executable})
    r = call(ctx, "inspect_environment", {"env": "env-1"})
    info = json.loads(r.content)
    assert info["python_version"].startswith(f"{sys.version_info.major}.{sys.version_info.minor}")
    assert info["package_count"] >= 1
    # 任意解释器路径不接受
    r = call(ctx, "inspect_environment", {"env": sys.executable})
    assert r.is_error and r.code == "env_unknown"


# =========================================================================== trial
def test_try_import_ok_and_failure_tail(box):
    ctx = make_ctx(box, environments={"e": sys.executable})
    r = call(ctx, "try_import", {"env": "e", "module": "json.decoder"})
    assert not r.is_error and "OK" in r.content
    r = call(ctx, "try_import", {"env": "e", "module": "definitely_not_a_module_xyz"})
    assert not r.is_error and "FAILED" in r.content and "ModuleNotFoundError" in r.content


@pytest.mark.parametrize(
    "bad",
    ["", "a b", "os;import sys", "x.", ".x", "a..b", "1abc", "os\n", "a-b", None, 3, "x" * 300],
)
def test_try_import_rejects_malformed_module_names(box, bad):
    ctx = make_ctx(box, environments={"e": sys.executable})
    r = call(ctx, "try_import", {"env": "e", "module": bad})
    assert r.is_error and r.code == "bad_module"


def test_try_import_unknown_env_and_timeout(box, monkeypatch):
    ctx = make_ctx(box, environments={"e": sys.executable})
    assert call(ctx, "try_import", {"env": "zzz", "module": "os"}).code == "env_unknown"
    slow = box.tmp / "slowpkg"
    put(slow / "slowmod_fx.py", "import time\ntime.sleep(30)\n")
    monkeypatch.setenv("PYTHONPATH", str(slow))
    monkeypatch.setattr(trial, "IMPORT_TIMEOUT_S", 1.5)
    r = call(ctx, "try_import", {"env": "e", "module": "slowmod_fx"})
    assert r.is_error and r.code == "timeout"


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_try_run_reports_exit_code_stderr_tail_and_never_touches_user_files(box):
    script = put(
        box.proj / "fig.py",
        "import sys\nopen('written_by_script.txt', 'w').write('x')\n"
        "print('boom', file=sys.stderr)\nraise SystemExit(3)\n",
    )
    put(box.proj / "data.csv", "1,2\n")
    before = _tree_bytes(box.proj)
    ctx = make_ctx(box, environments={"e": sys.executable}, script_path=script)
    r = call(ctx, "try_run", {"env": "e"})
    assert not r.is_error and "exit code 3" in r.content and "boom" in r.content
    assert _tree_bytes(box.proj) == before  # 用户目录逐字节不变，脚本写的文件落在临时副本里


def test_try_run_counts_figures_and_traceback_tail_is_limited(box):
    pytest.importorskip("matplotlib")
    script = put(
        box.proj / "fig.py",
        "import matplotlib.pyplot as plt\nplt.figure(); plt.figure()\nplt.show()\n",
    )
    ctx = make_ctx(box, environments={"e": sys.executable}, script_path=script)
    r = call(ctx, "try_run", {"env": "e"})
    assert "exit code 0" in r.content and "figures: 2" in r.content
    bad = put(box.proj / "bad.py", "def f(n):\n    return f(n - 1) if n else 1 / 0\nf(80)\n")
    ctx.script_path = bad
    r = call(ctx, "try_run", {"env": "e"})
    assert "exit code 1" in r.content
    tail = r.content.split("error output ---\n")[1]
    assert len(tail.splitlines()) <= trial.ERROR_TAIL_LINES and "ZeroDivisionError" in tail


def _guarded_ctx(box, script_text):
    script = put(box.proj / "s.py", script_text)
    return make_ctx(box, environments={"e": sys.executable}, script_path=script)


def test_try_run_blocks_writes_outside_the_copy_and_leaves_real_files_untouched(box):
    outside = put(box.tmp / "elsewhere" / "precious.txt", "ORIGINAL")
    victim = put(box.tmp / "elsewhere" / "victim.txt", "KEEP")
    project_file = put(box.proj / "data.csv", "1,2\n")
    fake_home_target = box.home / "dropped.txt"
    script = (
        "import os, pathlib\n"
        "for fn in (\n"
        f"    lambda: open({str(outside)!r}, 'w').write('CLOBBER'),\n"
        f"    lambda: open({str(project_file)!r}, 'w').write('CLOBBER'),\n"
        "    lambda: (pathlib.Path.home() / 'dropped.txt').write_text('x'),\n"
        f"    lambda: os.remove({str(victim)!r}),\n"
        "):\n"
        "    try:\n"
        "        fn()\n"
        "    except PermissionError:\n"
        "        pass\n"
    )
    ctx = _guarded_ctx(box, script)
    before_all = _tree_bytes(box.tmp / "elsewhere")
    r = call(ctx, "try_run", {"env": "e"})
    assert not r.is_error, r.content
    assert "blocked_writes: 4" in r.content and "blocked" in r.summary
    assert _tree_bytes(box.tmp / "elsewhere") == before_all  # 逐字节不变，victim 仍在
    assert victim.exists() and outside.read_bytes() == b"ORIGINAL"
    assert project_file.read_text() == "1,2\n" and not fake_home_target.exists()


def test_try_run_blocks_even_when_the_script_swallows_the_error(box):
    outside = put(box.tmp / "elsewhere" / "p.txt", "ORIGINAL")
    ctx = _guarded_ctx(
        box,
        f"try:\n    open({str(outside)!r}, 'w').write('X')\nexcept Exception:\n    pass\n",
    )
    r = call(ctx, "try_run", {"env": "e"})
    assert "exit code 0" in r.content and "blocked_writes: 1" in r.content
    assert outside.read_text() == "ORIGINAL"


def test_try_run_allows_writes_inside_the_copy_and_its_private_tmp(box):
    script = (
        "import tempfile, os\n"
        "open('inside.txt', 'w').write('ok')\n"
        "os.makedirs('sub', exist_ok=True); open('sub/a.txt', 'w').write('ok')\n"
        "with tempfile.NamedTemporaryFile('w', delete=True) as f: f.write('t')\n"
        "print(os.path.dirname(f.name))\n"
    )
    ctx = _guarded_ctx(box, script)
    r = call(ctx, "try_run", {"env": "e"})
    assert not r.is_error and "exit code 0" in r.content and "blocked_writes" not in r.content


def test_try_run_refuses_when_the_guard_cannot_be_enabled(box, monkeypatch):
    bad = put(box.tmp / "not_a_guard.py", "raise RuntimeError('no guard')\n")
    monkeypatch.setattr(trial, "_guard_path", lambda: str(bad))
    ctx = _guarded_ctx(box, "open('x.txt', 'w').write('x')\n")
    r = call(ctx, "try_run", {"env": "e"})
    assert r.is_error and r.code == "trial_unguarded"
    assert not (box.proj / "x.txt").exists()


def test_native_trial_is_not_an_observe_level_mode(box):
    ctx = _guarded_ctx(box, "pass\n")
    r = call(ctx, "try_run", {"env": "e", "mode": "native"})
    assert r.is_error and r.code == "native_trial_not_observe"
    assert "native" not in json.dumps(REGISTRY["try_run"].schema)


def test_runaway_output_keeps_a_bounded_tail_and_the_process_is_stopped(box, monkeypatch):
    monkeypatch.setattr(trial, "OUTPUT_LIMIT_BYTES", 512 * 1024)
    monkeypatch.setattr(trial, "OUTPUT_TAIL_BYTES", 4096)
    started = []
    real_popen = trial.subprocess.Popen

    def spy(*a, **kw):
        p = real_popen(*a, **kw)
        started.append(p)
        return p

    monkeypatch.setattr(trial.subprocess, "Popen", spy)
    res = trial.run_python(
        sys.executable,
        code="import sys\nwhile True:\n    print('x' * 999)\n    print('e' * 999, file=sys.stderr)",
        timeout=60,
        session_id="s1",
    )
    assert res["status"] == "ok" and res["output_truncated"] is True
    assert len(res["stdout"].encode()) <= 4096 and len(res["stderr"].encode()) <= 4096
    assert res["output_bytes"] > 512 * 1024
    assert started[0].poll() is not None  # 子进程已被结束（按自己的 PID / 进程组）
    ctx = _guarded_ctx(box, "while True:\n    print('y' * 999)\n")
    r = call(ctx, "try_run", {"env": "e"})
    assert not r.is_error and "output_truncated" in r.content


def test_try_run_copy_lives_in_tavottos_cache_and_failed_cleanup_is_reported(box, monkeypatch):
    import tempfile

    seen = {}

    def fake(python, work, name, mode, timeout):
        seen["work"] = work
        return {"status": "ok", "returncode": 0, "figures": 0, "stderr": ""}

    ctx = make_ctx(box, environments={"e": "py"}, script_path=put(box.proj / "s.py"), runner=fake)
    real = trial._rmtree_once

    def boom(path):
        raise OSError("file in use")

    monkeypatch.setattr(trial, "_rmtree_once", boom)
    monkeypatch.setattr(trial, "CLEANUP_DELAY_S", 0)
    r = call(ctx, "try_run", {"env": "e"})
    assert not r.is_error and "cleanup_pending" in r.content and "cleanup_pending" in r.summary
    left = Path(seen["work"]).parent
    assert left.parent == box.data / "cache" / "fix_trials" == trial.trial_root()
    assert not str(left).startswith(os.path.realpath(tempfile.gettempdir()) + os.sep + "tavotto-")
    assert (left / trial.PENDING_MARKER).is_file()
    monkeypatch.setattr(trial, "_rmtree_once", real)
    assert trial.sweep_stale_trials() == 1 and not left.exists()


def test_sweep_removes_old_unmarked_trials_but_not_fresh_ones(box):
    root = trial.trial_root()
    old, fresh = root / "old-1", root / "fresh-1"
    for d in (old, fresh):
        put(d / "work" / "a.txt")
    os.utime(old, (1, 1))
    assert trial.sweep_stale_trials() == 1
    assert not old.exists() and fresh.exists()


def test_try_run_timeout_unknown_env_no_script_and_bad_mode(box):
    script = put(box.proj / "hang.py", "import time\ntime.sleep(60)\n")
    ctx = make_ctx(box, environments={"e": sys.executable}, script_path=script)
    monkey_timeout = call(ctx, "try_run", {"env": "e", "timeout_s": 5})
    assert monkey_timeout.is_error and monkey_timeout.code == "timeout"
    assert call(ctx, "try_run", {"env": "nope"}).code == "env_unknown"
    assert call(ctx, "try_run", {"env": "e", "mode": "rm -rf"}).code == "bad_arguments"
    ctx.script_path = None
    assert call(ctx, "try_run", {"env": "e"}).code == "no_script"


def test_try_run_uses_an_injected_runner_and_scrubs_its_output(box):
    script = put(box.proj / "s.py", "pass\n")
    seen = {}

    def fake(python, work, name, mode, timeout):
        seen.update(python=python, work=work, name=name, mode=mode)
        assert (Path(work) / name).is_file() and work != str(box.proj)
        return {"status": "ok", "returncode": 2, "figures": 0, "stderr": f"token={SECRET}\nlast"}

    ctx = make_ctx(box, environments={"e": "py"}, script_path=script, runner=fake)
    r = call(ctx, "try_run", {"env": "e"})
    assert seen["python"] == "py" and seen["name"] == "s.py" and seen["mode"] == "script"
    assert "exit code 2" in r.content and "last" in r.content and SECRET not in r.content
    assert not os.path.exists(seen["work"])  # 临时副本跑完即删


# =========================================================================== audit
def test_audit_line_has_all_fields_and_no_content(box):
    f = put(box.proj / "a.py", "FILE-CONTENT-CANARY")
    audit = AuditLog(box.data / "ai" / "fix_audit.jsonl")
    ctx = make_ctx(box)
    pat = "PATTERN-CANARY"
    call(ctx, "read_file", {"path": str(f)}, audit)
    call(ctx, "grep_files", {"root": ".", "pattern": pat}, audit)
    call(ctx, "read_file", {"path": str(box.home / ".ssh" / "id_rsa")}, audit)
    raw = (box.data / "ai" / "fix_audit.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(x) for x in raw.splitlines()]
    assert [r["step"] for r in rows] == [1, 2, 3]
    for r in rows:
        assert set(r) == {"ts", "session", "step", "tool", "tier", "args", "approved_by", "result"}
        assert r["session"] == "s1" and r["tier"] == "observe"
        assert set(r["result"]) == {"ok", "code", "summary"}
    assert rows[0]["args"]["path"] == str(f)  # 路径保留
    assert rows[1]["args"]["pattern"] == f"<str len={len(pat)}>"  # 内容只留长度
    assert [r["approved_by"] for r in rows] == ["policy", "policy", "blocked"]
    assert rows[2]["result"]["code"] == "denied"
    assert "FILE-CONTENT-CANARY" not in raw and pat not in raw


def test_audit_rolls_over_at_the_size_cap(box):
    p = box.data / "ai" / "fix_audit.jsonl"
    audit = AuditLog(p, max_bytes=400)
    ctx = make_ctx(box)
    for _ in range(12):
        call(ctx, "read_logs", {"source": "etc"}, audit)
    assert p.exists() and p.with_name(p.name + ".1").exists()
    assert p.stat().st_size < 400 + 600


def test_audit_failure_never_breaks_the_tool(box):
    blocker = put(box.data / "ai", "i am a file, not a directory")
    audit = AuditLog(blocker / "x.jsonl")
    f = put(box.proj / "a.py", "ok")
    assert not call(make_ctx(box), "read_file", {"path": str(f)}, audit).is_error


# =========================================================================== registry / adapter
def test_registry_contains_the_look_and_try_groups_with_run_tier_for_code_running_tools():
    assert set(REGISTRY) == {
        "read_run_report",
        "read_file",
        "list_dir",
        "find_files",
        "grep_files",
        "inspect_environment",
        "list_environments",
        "read_logs",
        "lookup_package",
        "try_import",
        "try_run",
    }
    assert {n for n, t in REGISTRY.items() if t.tier == "run"} == {
        "inspect_environment",
        "try_import",
        "try_run",
    }
    assert {t.tier for t in REGISTRY.values()} == {"observe", "run"}
    for name in ("try_run", "try_import", "inspect_environment"):
        assert "changes nothing" not in REGISTRY[name].description
        assert "never touched" not in REGISTRY[name].description
    assert not any(t.waits_for_user for t in REGISTRY.values())


def test_build_agent_tools_adapts_registry_and_audits(box):
    f = put(box.proj / "a.py", "adapter-ok")
    audit = AuditLog(box.data / "ai" / "fix_audit.jsonl")
    ctx = make_ctx(box)
    agent_tools = build_agent_tools(ctx, audit)
    assert [t.name for t in agent_tools] == list(REGISTRY)
    assert all(
        isinstance(t, runner_tools.Tool) and t.tier == REGISTRY[t.name].tier for t in agent_tools
    )
    by = {t.name: t for t in agent_tools}
    res = by["read_file"].handler({"path": str(f)})
    assert isinstance(res, wires.ToolResult) and "adapter-ok" in res.content
    assert (box.data / "ai" / "fix_audit.jsonl").read_text().count("\n") == 1


def test_tools_beyond_the_allowed_tiers_are_refused_and_audited_as_blocked(box):
    from tavotto.engine.fixtools.base import FixTool, obj, ok

    risky = FixTool("poke", "x", obj({}), lambda c, a: ok("done"), tier="user_env")
    audit = AuditLog(box.data / "ai" / "fix_audit.jsonl")
    ctx = make_ctx(box)
    r = run_tool(risky, ctx, {}, audit)
    assert r.is_error and r.code == "tier_not_allowed"
    assert json.loads((box.data / "ai" / "fix_audit.jsonl").read_text())["approved_by"] == "blocked"
    ctx.allowed_tiers = frozenset({"observe", "user_env"})
    assert not run_tool(risky, ctx, {}).is_error


def test_a_crashing_handler_and_non_object_args_are_tool_errors(box):
    from tavotto.engine.fixtools.base import FixTool, obj

    def boom(c, a):
        raise RuntimeError("secret detail")

    t = FixTool("boom", "x", obj({}), boom)
    ctx = make_ctx(box)
    r = run_tool(t, ctx, {})
    assert r.code == "tool_crashed" and "secret detail" not in r.content
    assert run_tool(REGISTRY["read_file"], ctx, ["not", "a", "dict"]).code == "bad_arguments"


def test_tier_is_never_sent_to_the_model(box):
    specs = [t.spec() for t in build_agent_tools(make_ctx(box), AuditLog(box.data / "a.jsonl"))]
    assert {s.tier for s in specs} == {"observe", "run"}
    assert all(s.waits_for_user is False for s in specs)
    for name, wire in wires.WIRES.items():
        if name == "demo":
            continue
        try:
            req = wire.build(
                base_url="https://example.invalid",
                api_key="k",
                model="m",
                system="s",
                tools=specs,
                history=[wires.Msg("user", text="hi")],
                effort=None,
                max_output_tokens=100,
            )
        except TypeError:
            pytest.skip("wire.build signature changed")
        text = json.dumps(req.body)
        assert "tier" not in text and "waits_for_user" not in text and "observe" not in text, name
        assert "read_file" in text


def test_toolspec_defaults_leave_existing_tools_unchanged():
    s = wires.ToolSpec("n", "d", {"type": "object"})
    assert s.tier == "observe" and s.waits_for_user is False
    t = runner_tools.Tool("n", "d", {"type": "object"}, lambda a: None)
    assert t.spec() == s


# =========================================================================== 第二轮：结构性修法
def _fake_module_env(box, name: str, body: str) -> dict:
    mods = box.tmp / "fakemods"
    put(mods / f"{name}.py", body)
    return {"PYTHONPATH": str(mods)}


def test_try_import_runs_under_the_write_guard_and_blocks_import_time_writes(box, monkeypatch):
    victim = put(box.tmp / "elsewhere" / "real.txt", "ORIGINAL")
    body = (
        "import pathlib\n"
        "for fn in (\n"
        f"    lambda: pathlib.Path({str(victim)!r}).write_text('CLOBBER'),\n"
        "    lambda: (pathlib.Path.home() / 'x').write_text('dropped'),\n"
        "):\n"
        "    try:\n"
        "        fn()\n"
        "    except PermissionError:\n"
        "        pass\n"
    )
    monkeypatch.setenv("PYTHONPATH", _fake_module_env(box, "writer_fx", body)["PYTHONPATH"])
    ctx = make_ctx(box, environments={"e": sys.executable})
    r = call(ctx, "try_import", {"env": "e", "module": "writer_fx"})
    assert "blocked_writes: 2" in r.content and "import writer_fx: OK" in r.content
    assert victim.read_text() == "ORIGINAL" and not (box.home / "x").exists()


def test_inspect_environment_runs_with_the_guard_armed(box, monkeypatch):
    seen = {}
    real = trial.run_python

    def spy(python, **kw):
        res = real(python, **kw)
        seen.update(res)
        return res

    monkeypatch.setattr(trial, "run_python", spy)
    code = (
        "import json, sys\n"
        "try:\n"
        "    open(sys.argv[1], 'w').write('x')\n"
        "    print(json.dumps({'python_version': 'x.y', 'wrote': True}))\n"
        "except PermissionError:\n"
        "    print(json.dumps({'python_version': 'x.y', 'wrote': False}))\n"
    )
    outside = box.tmp / "elsewhere" / "probe.txt"
    outside.parent.mkdir()
    res = real(sys.executable, code=code, argv=(str(outside),), timeout=30, session_id="s1")
    assert res["status"] == "ok" and '"wrote": false' in res["stdout"] and not outside.exists()
    assert res["blocked_writes"]
    ctx = make_ctx(box, environments={"e": sys.executable})
    r = call(ctx, "inspect_environment", {"env": "e"})
    assert not r.is_error and seen["status"] == "ok" and "blocked_writes" not in seen["stderr"]


def test_unguardable_python_refuses_import_and_inspect_too(box, monkeypatch):
    bad = put(box.tmp / "not_a_guard.py", "raise RuntimeError('no guard')\n")
    monkeypatch.setattr(trial, "_guard_path", lambda: str(bad))
    ctx = make_ctx(box, environments={"e": sys.executable})
    assert call(ctx, "try_import", {"env": "e", "module": "json"}).code == "trial_unguarded"
    assert call(ctx, "inspect_environment", {"env": "e"}).code == "trial_unguarded"


def test_every_subprocess_scratch_lives_in_the_cache_dir_and_failed_cleanup_is_reported(
    box, monkeypatch
):
    import tempfile

    systmp = Path(tempfile.gettempdir())
    before = {p.name for p in systmp.glob("tavotto-fixtool-*")}
    ctx = make_ctx(box, environments={"e": sys.executable})
    call(ctx, "try_import", {"env": "e", "module": "json"})
    call(ctx, "inspect_environment", {"env": "e"})
    assert {p.name for p in systmp.glob("tavotto-fixtool-*")} == before
    assert list(trial.trial_root().iterdir()) == []  # 正常路径：清得干干净净
    monkeypatch.setattr(trial, "_rmtree_once", lambda path: (_ for _ in ()).throw(OSError("busy")))
    monkeypatch.setattr(trial, "CLEANUP_DELAY_S", 0)
    r = call(ctx, "try_import", {"env": "e", "module": "json"})
    assert not r.is_error and "cleanup_pending" in r.content
    left = list(trial.trial_root().iterdir())
    assert len(left) == 1 and (left[0] / trial.PENDING_MARKER).is_file()
    r = call(ctx, "inspect_environment", {"env": "e"})
    assert "cleanup_pending" in r.content
    monkeypatch.undo()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(box.data))
    assert trial.sweep_stale_trials() >= 1


def test_grep_is_a_literal_search_and_never_backtracks(box):
    import time

    put(box.proj / "evil.txt", "a" * 40 + "!\n")
    put(box.proj / "lit.txt", "x (a+)+$ y\nplain Needle\n")
    ctx = make_ctx(box)
    t0 = time.monotonic()
    r = call(ctx, "grep_files", {"root": ".", "pattern": "(a+)+$"})
    assert time.monotonic() - t0 < 2.0
    assert r.content.strip() == "lit.txt:1: x (a+)+$ y"
    assert call(ctx, "grep_files", {"root": ".", "pattern": "a.*"}).content == "(no matches)"
    assert "lit.txt:2" in call(ctx, "grep_files", {"root": ".", "pattern": "Needle"}).content
    assert call(ctx, "grep_files", {"root": ".", "pattern": "needle"}).content == "(no matches)"
    ci = call(ctx, "grep_files", {"root": ".", "pattern": "needle", "ignore_case": True})
    assert "lit.txt:2" in ci.content
    assert "regular expression" not in REGISTRY["grep_files"].description.split("NOT")[0]
    assert "NOT a regular expression" in REGISTRY["grep_files"].description


def test_followed_file_symlink_is_judged_and_recorded_by_its_target(box):
    outside = put(box.home / "other" / "t.txt", "needle outside")
    inside = put(box.proj / "real.txt", "needle inside")
    (box.proj / "ln_out.txt").symlink_to(outside)
    (box.proj / "ln_in.txt").symlink_to(inside)
    ctx = make_ctx(box)
    r = call(ctx, "grep_files", {"root": ".", "pattern": "needle"})
    assert "needle outside" in r.content and "needle inside" in r.content
    assert [x["path"] for x in ctx.reads] == [os.path.realpath(outside)]
    assert ctx.reads[0]["tool"] == "grep_files"
    ctx2 = make_ctx(box)
    call(ctx2, "read_file", {"path": "ln_out.txt"})
    call(ctx2, "read_file", {"path": "ln_in.txt"})
    assert [x["path"] for x in ctx2.reads] == [os.path.realpath(outside)]
    # 指向拦截区 / 范围外的链接不被跟随
    put(box.home / ".ssh" / "k.txt", "needle secret")
    (box.proj / "ln_ssh.txt").symlink_to(box.home / ".ssh" / "k.txt")
    ctx3 = make_ctx(box)
    assert "secret" not in call(ctx3, "grep_files", {"root": ".", "pattern": "needle"}).content


# --------------------------------------------------------------------------- Codex #1353 第三轮
@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("password=abc", "password=***"),
        ("token=12345", "token=***"),
        ("api_token=xyz", "api_token=***"),
        ("PWD: a1", "PWD: ***"),
        ("max_tokens = 4096", "max_tokens = 4096"),
        ("num_tokens: 12", "num_tokens: 12"),
        ("token_count=7", "token_count=7"),
        ("tokenizer=bert", "tokenizer=bert"),
    ],
)
def test_short_credentials_are_masked_but_count_keys_are_not(text, want):
    assert readscope.scrub(text) == want


def test_audit_keeps_only_paths_and_enum_values(box):
    from tavotto.engine.fixtools import audit

    got = audit.summarize_args(
        {
            "path": "~/x.csv",
            "glob": SECRET,
            "module": SECRET,
            "name": "x" * 9,
            "mode": "script",
            "source": "worker",
            "pattern": SECRET,
        }
    )
    assert got["path"] == "~/x.csv" and got["mode"] == "script" and got["source"] == "worker"
    for k in ("glob", "module", "name", "pattern"):
        assert got[k].startswith("<str len=") and SECRET not in got[k]
    assert audit.summarize_args({"mode": SECRET})["mode"].startswith("<str len=")


def test_directory_enumeration_is_bounded_and_lazy(tmp_path, monkeypatch):
    big = tmp_path / "big"
    big.mkdir()
    for i in range(60):
        (big / f"f{i:03}.txt").write_text("x")
    monkeypatch.setattr(observe, "DIR_ENTRY_CAP", 10)
    entries, partial = observe._scan_bounded(str(big), deadline=float("inf"))
    assert len(entries) == 10 and partial
    import time as _t

    assert observe._scan_bounded(str(big), deadline=_t.monotonic() - 1) == ([], True)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ('{"password":"abc\\"def"}', '{"password":"***"}'),
        ("pwd='it\\'s'", "pwd='***'"),
        ("tokenizer_api_token=abc", "tokenizer_api_token=***"),
        ("my_tokenizer_password=abc", "my_tokenizer_password=***"),
        ("tokenizer=bert", "tokenizer=bert"),
        ("gen.max_tokens = 4096", "gen.max_tokens = 4096"),
    ],
)
def test_escaped_quotes_and_anchored_count_keys(text, want):
    assert readscope.scrub(text) == want


def test_a_partial_directory_scan_is_reported_not_silent(box, monkeypatch):
    monkeypatch.setattr(observe, "DIR_ENTRY_CAP", 3)
    proj = box.proj
    for i in range(10):
        (proj / f"n{i:02}.txt").write_text("x")
    ctx = make_ctx(box)
    res = call(ctx, "grep_files", {"root": str(proj), "pattern": "zzz-nothing"})
    assert "incomplete" in res.content
    res2 = call(ctx, "find_files", {"root": str(proj), "glob": "*.nope"})
    assert "incomplete" in res2.content
    res3 = call(ctx, "list_dir", {"path": str(proj)})
    assert "incomplete" in res3.content


# --------------------------------------------------------------------------- Codex #1353 第五轮
def test_try_run_refuses_to_start_other_processes(box):
    """写入绊线只管本解释器：子进程不在视野里，所以试跑里起进程一律拦（写不出去的路就断了）。"""
    outside = put(box.tmp / "elsewhere" / "p.txt", "ORIGINAL")
    child = f"open({str(outside)!r}, 'w').write('CLOBBER')"
    script = (
        "import os, subprocess, sys\n"
        "for fn in (\n"
        f"    lambda: subprocess.run([sys.executable, '-c', {child!r}]),\n"
        f"    lambda: os.system(sys.executable + ' -c \"pass\"'),\n"
        "):\n"
        "    try:\n"
        "        fn()\n"
        "    except PermissionError:\n"
        "        pass\n"
    )
    r = call(_guarded_ctx(box, script), "try_run", {"env": "e"})
    assert not r.is_error, r.content
    assert "blocked_spawns: 2" in r.content
    assert outside.read_bytes() == b"ORIGINAL"


def test_a_copy_failure_whose_cleanup_also_fails_reports_cleanup_pending(box, monkeypatch):
    ctx = make_ctx(box, environments={"e": "py"}, script_path=put(box.proj / "s.py"))

    def half_copy(src_dir, script, dest, scope=None, skipped=None):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "partial.txt").write_text("x")
        raise OSError("disk full")

    monkeypatch.setattr(trial, "_copy_project", half_copy)
    monkeypatch.setattr(
        trial, "_rmtree_once", lambda path: (_ for _ in ()).throw(OSError("in use"))
    )
    monkeypatch.setattr(trial, "CLEANUP_DELAY_S", 0)
    r = call(ctx, "try_run", {"env": "e"})
    assert r.is_error and r.code == "copy_failed" and "cleanup_pending" in r.content


def test_copy_enumeration_stops_early_on_a_huge_folder(tmp_path, monkeypatch):
    src = tmp_path / "proj"
    src.mkdir()
    script = put(src / "fig.py", "print(1)\n")
    for i in range(40):
        (src / f"d{i:02}.txt").write_text("x")
    monkeypatch.setattr(trial, "COPY_MAX_FILES", 5)
    dest = tmp_path / "dest"
    name, whole = trial._copy_project(src, script, dest)
    assert (name, whole) == ("fig.py", False)
    assert sorted(p.name for p in dest.iterdir()) == ["fig.py"]


# =========================================================================== run 档
def test_bare_context_allows_only_observe_and_fix_session_adds_run(box):
    bare = FixContext(session_id="s", project_root=box.proj, home=box.home)
    assert bare.allowed_tiers == frozenset({"observe"})
    script = put(box.proj / "s.py", "print(1)\n")
    bare.environments, bare.script_path = {"e": sys.executable}, script
    for name, args in (("try_run", {"env": "e"}), ("try_import", {"env": "e", "module": "os"})):
        r = run_tool(REGISTRY[name], bare, args)
        assert r.is_error and r.code == "tier_not_allowed"
    assert run_tool(REGISTRY["read_logs"], bare, {"source": "bad"}).code == "bad_arguments"
    fix = FixContext.for_fix_session(
        "s", box.proj, home=box.home, environments={"e": sys.executable}, script_path=script
    )
    assert fix.allowed_tiers == frozenset({"observe", "run"})
    assert not run_tool(REGISTRY["try_run"], fix, {"env": "e"}).is_error
    assert fix.session_id == "s" and fix.project_root == box.proj


def _sandbox_kind():
    for kind in ("macos-sandbox-exec", "bwrap"):
        tool = trial._find_tool(kind)
        if tool and trial._probe(kind, tool):
            return kind
    return None


_C_WRITE = (
    "import sqlite3, sys\n"
    "try:\n"
    "    c = sqlite3.connect({path!r}); c.execute('create table t(x)'); c.commit(); c.close()\n"
    "    print('WROTE')\n"
    "except Exception as e:\n"
    "    print('FAILED', type(e).__name__)\n"
)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec")
def test_macos_sandbox_blocks_a_c_level_write_the_tripwire_cannot_see(box):
    if _sandbox_kind() != "macos-sandbox-exec":
        pytest.skip("sandbox-exec not usable here (e.g. already inside a sandbox)")
    outside = box.tmp / "elsewhere" / "x.db"
    outside.parent.mkdir()
    ctx = _guarded_ctx(box, _C_WRITE.format(path=str(outside)))
    r = call(ctx, "try_run", {"env": "e"})
    assert not r.is_error, r.content
    assert "FAILED" not in r.content  # stdout 不进结果；看文件
    assert not outside.exists()
    assert "os_sandbox: macos-sandbox-exec" in r.content
    # 同一段代码在专用目录里写得成（沙盒没有把整个试跑废掉）
    ok_ctx = _guarded_ctx(
        box,
        _C_WRITE.format(path="inside.db") + "assert __import__('os').path.exists('inside.db')\n",
    )
    r2 = call(ok_ctx, "try_run", {"env": "e"})
    assert "exit code 0" in r2.content, r2.content


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux bwrap")
def test_bwrap_blocks_a_c_level_write_the_tripwire_cannot_see(box):
    if _sandbox_kind() != "bwrap":
        pytest.skip("bwrap not available or user namespaces disabled")
    outside = box.tmp / "elsewhere" / "x.db"
    outside.parent.mkdir()
    ctx = _guarded_ctx(box, _C_WRITE.format(path=str(outside)))
    r = call(ctx, "try_run", {"env": "e"})
    assert not r.is_error, r.content
    assert not outside.exists() and "os_sandbox: bwrap" in r.content


@pytest.mark.skipif(
    os.name == "nt", reason="sandbox-exec 只在 macOS；目录名里的引号在 Windows 上不合法"
)
def test_macos_profile_text_is_exactly_the_narrow_write_allowlist(tmp_path):
    d = tmp_path / 'we"ird'
    d.mkdir()
    prof = trial.macos_profile(str(d))
    real = os.path.realpath(d).replace('"', '\\"')
    assert prof == (
        "(version 1)(allow default)(deny file-write*)"
        f'(allow file-write* (subpath "{real}") (literal "/dev/null") (literal "/dev/tty"))'
    )
    assert prof.count("subpath") == 1  # 只放试跑目录这一个根，不放系统临时目录根


def test_without_a_sandbox_command_the_trial_falls_back_to_the_tripwire_and_says_none(
    box, monkeypatch
):
    monkeypatch.setattr(trial, "_find_tool", lambda kind: None)
    outside = put(box.tmp / "elsewhere" / "p.txt", "ORIGINAL")
    ctx = _guarded_ctx(box, f"open({str(outside)!r}, 'w').write('X')\n")
    r = call(ctx, "try_run", {"env": "e"})
    assert not r.is_error and "os_sandbox: none" in r.content and "blocked_writes: 1" in r.content
    assert outside.read_text() == "ORIGINAL"
    r2 = call(ctx, "try_import", {"env": "e", "module": "json"})
    assert "os_sandbox: none" in r2.content
    assert (
        json.loads(call(ctx, "inspect_environment", {"env": "e"}).content)["os_sandbox"] == "none"
    )


def test_a_sandbox_that_fails_to_start_falls_back_instead_of_erroring(box, monkeypatch):
    monkeypatch.setattr(trial, "_find_tool", lambda kind: "/nonexistent/sandbox-tool")
    monkeypatch.setattr(trial, "_PROBE_CACHE", {})
    ctx = _guarded_ctx(box, "print('hi')\n")
    r = call(ctx, "try_run", {"env": "e"})
    assert not r.is_error and "exit code 0" in r.content and "os_sandbox: none" in r.content


# =========================================================================== HTTP 凭据头 / 有界读取
@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
        ('authorization: Digest username="a", response="b"', 'response="b"'),
        ("Proxy-Authorization: Basic cHJveHk6cHc=", "cHJveHk6cHc"),
        ("Cookie: a=b; c=d", "c=d"),
        ("  Set-Cookie: sid=abc123; Path=/; HttpOnly", "sid=abc123"),
        ("X-Api-Key: abcdefgh ijkl", "ijkl"),
        ("x-auth-token: tok tok tok", "tok tok"),
        ('{"Authorization":"Basic x\\"y"}', 'x\\"y'),
        ("curl -H 'Authorization: Basic abc' https://h", "Basic abc"),
        ('curl -H "Cookie: a=b; c=d" https://h', "c=d"),
        ("authorization=Basic dXNlcjpwdw==", "dXNlcjpwdw"),
        ('headers = {"authorization": "Bearer tt tt"}', "tt tt"),
    ],
)
def test_http_credential_headers_are_masked_whole(raw, secret):
    out = readscope.scrub(raw)
    assert secret not in out and "***" in out, out
    assert readscope.scrub(out) == out
    assert "*** ***" not in out


def test_http_credential_masking_keeps_the_rest_and_ignores_lookalikes():
    assert readscope.scrub("curl -H 'Authorization: Basic abc' https://h") == (
        "curl -H 'Authorization: ***' https://h"
    )
    assert readscope.scrub('{"Authorization":"Basic x\\"y","n":1}') == (
        '{"Authorization":"***","n":1}'
    )
    for benign in ("author: bob", "Authors: a, b", "x-cookie-banner: on", "cookies are good"):
        assert readscope.scrub(benign) == benign


def test_read_file_on_a_huge_single_line_is_bounded_in_memory(box):
    import tracemalloc

    big = box.proj / "one_line.txt"
    with open(big, "wb") as f:
        for _ in range(50):
            f.write(b"a" * (1024 * 1024))
    assert big.stat().st_size == 50 * 1024 * 1024
    ctx = make_ctx(box)
    tracemalloc.start()
    try:
        r = call(ctx, "read_file", {"path": str(big)})
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert not r.is_error and "[truncated" in r.content and "longer than 256 KiB" in r.content
    body = r.content.split("\n[truncated")[0]
    assert len(body.encode()) <= readscope.MAX_READ_BYTES
    assert peak < 8 * 1024 * 1024, peak


def test_scrub_is_linear_on_a_very_long_word_run():
    import time

    text = "x" * 262144 + "\n[truncated: showing lines 4-4 of a file]"
    t0 = time.monotonic()
    readscope.scrub(text)
    assert time.monotonic() - t0 < 2.0
    assert "***" in readscope.scrub("a.b." * 10 + "password = hunter22")


def test_bounded_read_keeps_line_semantics(box):
    f = box.proj / "l.txt"
    f.write_bytes(
        b"one\ntwo\nthree\n" + b"x" * 300_000 + b"\nlast\n"
    )  # 按字节写：Windows 上 write_text 会写成 CRLF
    ctx = make_ctx(box)
    r = call(ctx, "read_file", {"path": str(f), "max_lines": 2})
    assert r.content.startswith("one\ntwo\n") and "three" not in r.content.split("[truncated")[0]
    r = call(ctx, "read_file", {"path": str(f), "start_line": 3})
    body = r.content.split("[truncated")[0]
    assert (
        body.startswith("three\n") and "longer than" not in r.content
    )  # 第 4 行放不下：停在它前面
    r = call(ctx, "read_file", {"path": str(f), "start_line": 4})
    assert "line 4 is longer than 256 KiB" in r.content
    r = call(ctx, "read_file", {"path": str(f), "start_line": 5})
    assert r.content.strip() == "last"
    g = put(box.proj / "nonl.txt", "no newline at end")
    assert call(ctx, "read_file", {"path": str(g)}).content == "no newline at end"


# --------------------------------------------------------------------------- Codex #1353 第七轮
def test_read_logs_is_listed_in_the_outside_reads(box):
    log = put(box.data / "cache" / "app.log", "line1\nline2\n")
    ctx = make_ctx(box, log_paths={"app": str(log)})
    r = call(ctx, "read_logs", {"source": "app", "lines": 10})
    assert not r.is_error, r.content
    assert any(x["tool"] == "read_logs" and x["path"] == os.path.realpath(log) for x in ctx.reads)


def test_a_script_exiting_97_is_a_real_exit_code_not_a_guard_failure(box):
    r = call(_guarded_ctx(box, "import sys\nsys.exit(97)\n"), "try_run", {"env": "e"})
    assert not r.is_error, r.content
    assert "exit code 97" in r.content


def test_the_sandbox_probe_dir_lives_in_the_cache_and_cleans_up(box, monkeypatch):
    import tempfile

    trial._PROBE_CACHE.clear()
    seen = {}

    def fake_args(kind, tool, probe_dir):
        seen["dir"] = probe_dir
        return []

    monkeypatch.setattr(trial, "_sandbox_args", fake_args)
    trial._probe("macos-sandbox-exec", "/usr/bin/true")
    trial._PROBE_CACHE.clear()
    d = Path(seen["dir"])
    assert d.parent == trial.trial_root()
    assert not str(d).startswith(os.path.realpath(tempfile.gettempdir()) + os.sep + "tavotto-sbx")
    assert not d.exists()


# --------------------------------------------------------------------------- Codex #1353 第八轮
@pytest.mark.parametrize(
    "rel",
    [
        ".cache/huggingface/token",
        ".npmrc",
        ".cargo/credentials.toml",
        ".config/gh/hosts.yml",
        ".kaggle/kaggle.json",
        ".pgpass",
        "proj/api.token",
        "proj/credentials",
        "proj/client_secret.json",
    ],
)
def test_more_credential_stores_and_names_are_denied(box, rel):
    p = put(box.home / rel, "hf_abcdefghijklmnopqrstuvwxyz")
    r = call(make_ctx(box), "read_file", {"path": str(p)})
    assert r.is_error and r.code == "denied"


@pytest.mark.parametrize(
    "name", ["tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"]
)
def test_tokenizer_files_are_still_readable(box, name):
    p = put(box.proj / name, "{}")
    assert not call(make_ctx(box), "read_file", {"path": str(p)}).is_error


@pytest.mark.parametrize(
    "tok",
    [
        "hf_" + "a" * 34,
        "npm_" + "b" * 36,
        "glpat-" + "c" * 20,
        "github_pat_" + "d" * 30,
        "pypi-" + "e" * 40,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
    ],
)
def test_more_token_shapes_are_scrubbed(tok):
    assert tok not in readscope.scrub(f"value {tok} end")


def test_trial_never_captures_a_cold_conda_activation(box, monkeypatch):
    """冷缓存的激活捕获会跑 conda 与 activate.d：不许在沙盒 / 守卫外跑——只用缓存，没缓存就不激活并说明。"""
    from tavotto.engine import launcher

    calls = []

    def cold(python, base=None, *, timeout=None):
        calls.append(getattr(launcher._no_capture, "on", False))
        raise launcher.CaptureDeferred("cold")

    monkeypatch.setattr(launcher, "activate_env", cold)
    r = call(_guarded_ctx(box, "print('hi')\n"), "try_run", {"env": "e"})
    assert calls == [True]  # 只在 no_capture 里问过缓存
    assert not r.is_error and "activation: skipped" in r.content


def test_package_count_is_the_real_total_with_a_truncation_marker(box, monkeypatch):
    big = {
        "python_version": "3.12.1",
        "packages": [f"p{i}==1" for i in range(400)],
        "package_total": 812,
        "packages_truncated": "only the first 400",
    }

    def fake_run(python, **kw):
        return {
            "status": "ok",
            "returncode": 0,
            "stdout": json.dumps(big),
            "stderr": "",
            "os_sandbox": "none",
        }

    monkeypatch.setattr(trial, "run_python", fake_run)
    r = call(make_ctx(box, environments={"e": "py"}), "inspect_environment", {"env": "e"})
    info = json.loads(r.content.split("\n\n")[0] if "\n\n" in r.content else r.content)
    assert info["package_count"] == 812 and "only the first 400" in info["packages_truncated"]


# --------------------------------------------------------------------------- Codex #1353 第九轮
@pytest.mark.parametrize(
    ("raw", "gone", "kept"),
    [
        (
            "DefaultEndpointsProtocol=https;AccountName=acct1;AccountKey="
            + "AbCdEfGh1234567890+/" * 3
            + "AbCdEfGh12==;EndpointSuffix=core.windows.net",
            "AbCdEfGh1234567890",
            "AccountName=acct1;AccountKey=***;EndpointSuffix=core.windows.net",
        ),
        (
            "Endpoint=sb://x.servicebus.windows.net/;SharedAccessKeyName=root;SharedAccessKey=Zm9vYmFy+k9=",
            "Zm9vYmFy",
            "SharedAccessKeyName=root;SharedAccessKey=***",
        ),
        ('clientSecret: "s3cr3t value"', "s3cr3t", 'clientSecret: "***"'),
        ("aws_secret_access_key = wJalrXUtn", "wJalrXUtn", "aws_secret_access_key = ***"),
        ("x-ms-signature: abc123", "abc123", "x-ms-signature: ***"),
        ("sas_sig=se%3D2026", "se%3D2026", "sas_sig=***"),
        ("Server=db;User Id=u;Pwd=hunter2;Database=d", "hunter2", "Pwd=***;Database=d"),
        ("postgres://u:pw@h/db", "pw", "postgres://u:***@h/db"),
        ("redis://:pw0rd@cache:6379/0", "pw0rd", "redis://:***@cache"),
        ("pip install --client-secret abc -v", "abc", "--client-secret *** -v"),
    ],
)
def test_key_name_suffix_family_and_connection_strings_are_masked(raw, gone, kept):
    out = readscope.scrub(raw)
    assert gone not in out and kept in out, out
    assert readscope.scrub(out) == out


@pytest.mark.parametrize(
    "text",
    [
        "primary_key = 3",
        "sort_key: name",
        "cache_key=abc",
        "partition_key: user",
        "hotkey = ctrl",
        "monkey=1",
        "sorted(rows, key=len)",
        "max_tokens = 4096",
        "--sort-key name --max-tokens 100",
        "keyboard_layout = us",
        "signal = 3",
        "author: Someone",
    ],
)
def test_non_credential_key_names_are_left_alone(text):
    assert readscope.scrub(text) == text


def test_high_entropy_fallback_masks_mixed_case_digit_runs_only():
    secret = (
        "q8Zk3VbN7xPw2LmT" + "9dRf5HjC1sGyA4eU6oIt0BhW"
    )  # 40 字符，大小写数字齐全；拼接以免触发推送保护
    out = readscope.scrub(f"value is {secret} end")
    assert secret not in out and "value is *** end" in out
    for keep in (
        "matplotlib-3.9.2-cp312-cp312-macosx_11_0_arm64.whl",
        "/Users/alice/Library/Caches/pip",
        "/Users/alice/Library/Caches/pip/wheels/3d/ab/AbC9/matplotlib_3_9_2_py3",
        "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
        "test_export_png_dpi300_for_SVGRenderer_ok_2024_case",
        "z" * 64,
    ):
        assert readscope.scrub(keep) == keep, keep
    # 小写十六进制哈希：高熵规则本身不碰（诊断包的 redact_text 另有自己的一条）
    sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert readscope._HIGH_ENTROPY.sub(readscope._mask_entropy, sha) == sha


def test_scrub_stays_linear_with_the_new_key_family():
    import time

    t0 = time.monotonic()
    for text in ("aB3_" * 70000, "a" * 300000 + "password=x", "a-" * 130000, "x" * 262144):
        readscope.scrub(text)
    assert time.monotonic() - t0 < 4.0


@pytest.mark.parametrize(
    "rel",
    [
        "proj/token_counts.csv",
        "proj/password_strength.py",
        "proj/tokenized-data/part1.txt",
        "proj/secret_santa.txt",
        "proj/credential_helper.py",
    ],
)
def test_ordinary_files_with_credential_words_in_the_name_are_readable(box, rel):
    p = put(box.home / rel, "ok")
    r = call(make_ctx(box), "read_file", {"path": str(p)})
    assert not r.is_error, r.content


def test_directories_are_not_denied_by_name_and_can_be_scan_roots(box):
    put(box.home / "token-analysis" / "a.py", "needle = 1\n")
    put(box.proj / "tokenized-data" / "b.txt", "needle here\n")
    ctx = make_ctx(box)
    put(box.proj / "credentials" / "c.txt", "needle too\n")
    put(box.proj / "id_cache" / "d.txt", "needle too\n")
    roots = (
        box.home / "token-analysis",
        box.proj / "tokenized-data",
        box.proj / "credentials",
        box.proj / "id_cache",
    )
    for root in roots:
        assert not call(ctx, "list_dir", {"path": str(root)}).is_error
        assert "needle" in call(ctx, "grep_files", {"root": str(root), "pattern": "needle"}).content
        assert not call(ctx, "find_files", {"root": str(root), "glob": "*"}).is_error


@pytest.mark.parametrize(
    "rel",
    [
        "proj/credentials.json",
        "proj/client_secret_123.json",
        "proj/secrets.yaml",
        "proj/app.secret",
        "proj/service-account-prod.json",
        "proj/password.txt",
        "proj/vault.kdbx",
        "proj/token",
        ".cache/huggingface/token",
        "proj/.envrc",
    ],
)
def test_explicit_credential_file_shapes_are_denied(box, rel):
    p = put(box.home / rel, "TOP-SECRET")
    r = call(make_ctx(box), "read_file", {"path": str(p)})
    assert r.is_error and r.code == "denied" and "TOP-SECRET" not in r.content


def _copy(box, scope=None):
    dest = box.tmp / "dest"
    skipped: dict = {}
    name, whole = trial._copy_project(
        box.proj, box.proj / "s.py", dest, scope or make_ctx(box).scope, skipped
    )
    return dest, name, whole, skipped


def test_readable_file_symlink_is_copied_as_a_regular_file(box):
    put(box.proj / "s.py", "print(1)\n")
    put(box.home / "shared" / "data.csv", "1,2\n")
    (box.proj / "data.csv").symlink_to(Path("..") / "shared" / "data.csv")
    dest, _, whole, skipped = _copy(box)
    assert whole and (dest / "data.csv").read_text() == "1,2\n"
    assert not (dest / "data.csv").is_symlink()
    assert skipped == {"files": [], "dirs": []}


def test_blocked_dangling_and_folder_links_are_not_copied_and_are_reported(box):
    put(box.proj / "s.py", "print(1)\n")
    put(box.home / ".ssh" / "x", "PRIVATE")
    (box.proj / "k.txt").symlink_to(box.home / ".ssh" / "x")
    (box.proj / "gone.csv").symlink_to(box.home / "nowhere.csv")
    put(box.home / "shared" / "d" / "f.txt", "x")
    (box.proj / "linked_dir").symlink_to(box.home / "shared" / "d", target_is_directory=True)
    dest, _, _, skipped = _copy(box)
    assert sorted(p.name for p in dest.iterdir()) == ["s.py"]
    assert sorted(skipped["files"]) == ["gone.csv", "k.txt"] and skipped["dirs"] == ["linked_dir"]
    note = trial._copy_incomplete_note(skipped)
    assert "copy_incomplete: 2 linked file(s) were not copied" in note
    assert "1 linked folder(s)" in note and "PRIVATE" not in note and ".ssh" not in note


def test_try_run_reads_through_a_file_link_and_reports_blocked_ones(box):
    put(box.home / "shared" / "data.csv", "1,2\n")
    (box.proj / "data.csv").symlink_to(Path("..") / "shared" / "data.csv")
    put(box.home / ".ssh" / "x", "PRIVATE")
    (box.proj / "k.txt").symlink_to(box.home / ".ssh" / "x")
    script = (
        "import os, sys\n"
        "d = os.path.dirname(os.path.abspath(__file__))\n"
        "ok = open(os.path.join(d, 'data.csv')).read() == '1,2\\n'\n"
        "sys.exit(0 if ok and not os.path.exists(os.path.join(d, 'k.txt')) else 3)\n"
    )
    r = call(_guarded_ctx(box, script), "try_run", {"env": "e"})
    assert not r.is_error, r.content
    assert "exit code 0" in r.content, r.content
    assert "copy_incomplete: 1 linked file(s) were not copied (k.txt)" in r.content
    assert "PRIVATE" not in r.content


# --------------------------------------------------------------------------- Codex #1353 第十轮
@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("connect(password=secret)", "connect(password=***)"),
        ('{"password": null}', '{"password": null}'),
        ("f(token=abc, x=1)", "f(token=***, x=1)"),
        ("[pwd=hunter2]", "[pwd=***]"),
        ("<auth=xyz>", "<auth=***>"),
    ],
)
def test_bare_secret_values_keep_surrounding_delimiters(text, want):
    assert readscope.scrub(text) == want


def test_site_initialization_runs_under_the_guard(box, monkeypatch):
    """解释器自己 site-packages 里的 `.pth` / `sitecustomize` 在启动时自动执行：守卫必须先装好
    （-S 启动、守卫后再 site.main()）。造一个临时 venv，往它的 site-packages 放一个往项目外写的 `.pth`。"""
    import subprocess as _sp
    import venv

    outside = put(box.tmp / "elsewhere" / "victim.txt", "ORIGINAL")
    vdir = box.tmp / "v"
    venv.EnvBuilder(with_pip=False).create(vdir)
    vpy = str(vdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    sp = _sp.run(
        [vpy, "-c", "import site; print(site.getsitepackages()[0])"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    (Path(sp) / "evil.pth").write_text(
        f"import os; open({str(outside)!r}, 'w').write('CLOBBER')\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        trial, "_find_tool", lambda kind: None
    )  # 只测进程内守卫（没有 OS 沙盒的 Windows 就是这样）
    res = trial.run_python(vpy, code="print('ok')", timeout=60, session_id="s1")
    assert outside.read_bytes() == b"ORIGINAL"
    assert res.get("status") == "ok" and res.get("blocked_writes"), res
