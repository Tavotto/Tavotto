"""`tavotto open` 的交接链路：解析目标 → 登记 stem → 唤起界面。

平台分支（桌面 App 在哪儿）在**任意一个平台上**都必须能测——所以
engine/handoff.py 全程 os.path 拼字符串。这里的 win32 用例跑在 macOS/Linux
的 CI 上，就是那条纪律的看护。
"""
import ast
import json
import os
import pathlib
import subprocess
import sys

import pytest

from tavotto.engine import handoff, registry as engine_registry


SCRIPT = '''\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    fig.savefig("Fig1_demo.pdf")
'''


@pytest.fixture()
def figures(tmp_path):
    """一个最小图库：一个脚本 + 它的产物，没有注册表。"""
    d = tmp_path / "figures"
    d.mkdir()
    (d / "fig1_demo.py").write_text(SCRIPT, encoding="utf-8")
    (d / "Fig1_demo.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    return d


# --------------------------- 1. 解析目标 ---------------------------------
def test_directory_target_has_no_stem(figures):
    t = handoff.resolve_target(str(figures))
    assert t == handoff.Target(str(figures), None)


def test_product_target_yields_stem(figures):
    t = handoff.resolve_target(str(figures / "Fig1_demo.pdf"))
    assert t == handoff.Target(str(figures), "Fig1_demo")


def test_script_target_resolves_its_own_product(figures):
    """给脚本也行：产物名由静态扫描解出，用户不必知道 stem 是什么。"""
    t = handoff.resolve_target(str(figures / "fig1_demo.py"))
    assert t == handoff.Target(str(figures), "Fig1_demo")


def test_script_without_resolvable_stem_still_opens_project(tmp_path):
    """静态解不出产出名时只打开项目，**不猜一个 stem**（猜错=定位到别人的图）。"""
    d = tmp_path / "figures"
    d.mkdir()
    (d / "gen.py").write_text(
        "import sys\n"
        "import matplotlib.pyplot as plt\n"
        "def main():\n"
        "    fig, ax = plt.subplots()\n"
        "    fig.savefig(sys.argv[1])\n",
        encoding="utf-8")
    assert handoff.resolve_target(str(d / "gen.py")) == handoff.Target(str(d), None)


def test_project_root_is_the_registry_layer(figures):
    """子目录里的图：项目 = 注册表所在的那一层，不是图自己的目录。"""
    engine_registry.registry_path(figures).write_text(
        json.dumps({"version": 1, "scripts": {}}), encoding="utf-8")
    sub = figures / "panels"
    sub.mkdir()
    (sub / "Fig9_extra.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    t = handoff.resolve_target(str(sub / "Fig9_extra.pdf"))
    assert t == handoff.Target(str(figures), "Fig9_extra")


def test_registry_search_stops_after_max_parents(tmp_path):
    """向上找有上限：绝不静默把某个上层目录当图库（那会扫一整棵源码树）。"""
    engine_registry.registry_path(tmp_path).write_text(
        json.dumps({"version": 1, "scripts": {}}), encoding="utf-8")
    deep = tmp_path
    for i in range(handoff.MAX_PARENTS + 2):
        deep = deep / f"lvl{i}"
    deep.mkdir(parents=True)
    (deep / "Fig1.pdf").write_bytes(b"%PDF")
    assert handoff.resolve_target(str(deep / "Fig1.pdf")).project == str(deep)


@pytest.mark.parametrize("raw", ["", "   "])
def test_empty_path_rejected(raw):
    with pytest.raises(handoff.HandoffError):
        handoff.resolve_target(raw)


def test_missing_path_rejected(tmp_path):
    with pytest.raises(handoff.HandoffError, match="路径不存在"):
        handoff.resolve_target(str(tmp_path / "nope.pdf"))


def test_unknown_extension_rejected(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hi", encoding="utf-8")
    with pytest.raises(handoff.HandoffError, match="不认识的文件类型"):
        handoff.resolve_target(str(p))


# --------------------------- 2. 登记 stem --------------------------------
def test_registry_drafted_when_missing(figures):
    info = handoff.ensure_registered(str(figures), "Fig1_demo")
    assert info["created"] is True
    assert info["status"] == "created"
    assert info["parameterizable"] is True
    assert engine_registry.open_registry(figures).for_stem("Fig1_demo")


def test_registered_stem_leaves_registry_untouched(figures):
    handoff.ensure_registered(str(figures), "Fig1_demo")
    path = engine_registry.registry_path(figures)
    before = path.read_bytes()
    info = handoff.ensure_registered(str(figures), "Fig1_demo")
    assert info == {"registry": str(path), "status": "already", "created": False,
                    "added_scripts": [], "added_stems": {}, "conflicts": [],
                    "dynamic_names": [], "parameterizable": True}
    assert path.read_bytes() == before      # 已经登记过就一个字节都别动


def test_new_script_merges_without_touching_existing_entries(figures):
    """用户手工裁决过的条目永远优先——合并只追加，绝不改写。"""
    path = engine_registry.registry_path(figures)
    path.write_text(json.dumps({"version": 1, "scripts": {
        "fig1_demo.py": {"entry": "main", "cost": "heavy",
                         "notes": "手工裁决", "stems": ["Fig1_demo"]}}}),
        encoding="utf-8")
    (figures / "fig2_new.py").write_text(
        SCRIPT.replace("Fig1_demo", "Fig2_new"), encoding="utf-8")

    info = handoff.ensure_registered(str(figures), "Fig2_new")

    assert info["created"] is False
    assert info["added_scripts"] == ["fig2_new.py"]
    assert info["parameterizable"] is True
    cfg = json.loads(path.read_text(encoding="utf-8"))
    assert cfg["scripts"]["fig1_demo.py"]["cost"] == "heavy"
    assert cfg["scripts"]["fig1_demo.py"]["notes"] == "手工裁决"


def test_merge_with_nothing_new_leaves_the_file_byte_identical(figures):
    """合并只追加：没有新东西就**一个字节都不写**。

    重写一遍在内容上是等价的，但会抹掉用户手写的缩进与注释、并动 mtime——
    注册表是用户的资产，不是我们的缓存。
    """
    path = engine_registry.registry_path(figures)
    path.write_text(
        '{\n  "version": 1,\n  "_comment": "手写的，别动我的排版",\n'
        '  "scripts": {\n    "fig1_demo.py": {"entry": "main", "cost": "light",\n'
        '                     "stems": ["Fig1_demo"]}\n  }\n}\n',
        encoding="utf-8")
    before = path.read_bytes()

    info = handoff.ensure_registered(str(figures), None)   # 目录级交接，无 stem

    assert info["added_scripts"] == [] and info["added_stems"] == {}
    assert path.read_bytes() == before


def test_product_without_script_is_reported_not_parameterizable(tmp_path):
    d = tmp_path / "figures"
    d.mkdir()
    (d / "Scan.png").write_bytes(b"\x89PNG\r\n")
    info = handoff.ensure_registered(str(d), "Scan")
    assert info["parameterizable"] is False


def test_dynamic_names_reported(tmp_path):
    d = tmp_path / "figures"
    d.mkdir()
    (d / "gen.py").write_text(
        "import sys\n"
        "import matplotlib.pyplot as plt\n"
        "def main():\n"
        "    fig, ax = plt.subplots()\n"
        "    fig.savefig(sys.argv[1])\n",
        encoding="utf-8")
    info = handoff.ensure_registered(str(d), None)
    assert info["dynamic_names"] == ["gen.py"]


def test_broken_registry_is_never_overwritten(figures):
    """注册表是用户手写的资产：读不懂就报错，绝不当没看见重写一份。"""
    path = engine_registry.registry_path(figures)
    path.write_text("{ 这不是 JSON", encoding="utf-8")
    with pytest.raises(handoff.HandoffError):
        handoff.ensure_registered(str(figures), "Fig1_demo")
    assert path.read_text(encoding="utf-8") == "{ 这不是 JSON"


@pytest.mark.parametrize("body", [
    '{"scripts": "not-a-dict"}',          # 结构合法、类型不对
    '{"scripts": {"a.py": "not-a-dict"}}',  # 某个脚本条目不是对象
    '{"scripts": {"a.py": {"stems": "Fig1"}}}',  # stems 不是列表
    '["不是对象"]',                        # 顶层不是对象
])
def test_structurally_broken_registry_still_carries_a_code(figures, body):
    """**结构**坏掉的注册表也要走 HandoffError，不能抛裸异常。

    以前只有「JSON 语法坏」这一条被接住：`{"scripts": "x"}` 会在
    `discover.merge` 里 `scripts.values()` 抛 AttributeError——既不是
    ValueError 也不是 OSError，穿透 handoff 的 try/except、`cli()` 的
    HandoffError 捕获与所有外层，于是 `tavotto open --json` 吐的是一段
    traceback 而不是契约里那行 JSON，调用方（Codex 插件读最后一行）的
    分诊逻辑当场失灵。用户手写/误改注册表就够触发。
    """
    path = engine_registry.registry_path(figures)
    path.write_text(body, encoding="utf-8")
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.ensure_registered(str(figures), None)
    assert exc.value.code == "registry_invalid"
    assert path.read_text(encoding="utf-8") == body      # 一个字节都没动


# --------------------------- 3. 唤起界面 ---------------------------------
def test_macos_candidates_are_bundle_binaries():
    got = handoff.desktop_app_candidates(system="darwin", environ={"HOME": "/Users/x"})
    assert got == ["/Applications/Tavotto.app/Contents/MacOS/Tavotto",
                   "/Users/x/Applications/Tavotto.app/Contents/MacOS/Tavotto"]


def test_windows_candidates_start_at_localappdata():
    """NSIS 是 currentUser 安装：新装固定 $LOCALAPPDATA\\Tavotto。

    这条用例跑在 macOS/Linux 上——handoff 里一个 pathlib 都不用，就是为了它。
    """
    env = {"LOCALAPPDATA": "C:\\Users\\x\\AppData\\Local",
           "PROGRAMFILES": "C:\\Program Files"}
    assert handoff.desktop_app_candidates(system="win32", environ=env) == [
        "C:\\Users\\x\\AppData\\Local\\Tavotto\\Tavotto.exe",
        "C:\\Program Files\\Tavotto\\Tavotto.exe"]


def test_linux_has_no_desktop_build():
    assert handoff.desktop_app_candidates(system="linux", environ={"HOME": "/h"}) == []


def test_env_override_wins():
    env = {handoff.APP_ENV: "/tmp/dist/Tavotto/Tavotto", "HOME": "/Users/x"}
    assert handoff.desktop_app_candidates(system="darwin", environ=env)[0] == \
        "/tmp/dist/Tavotto/Tavotto"


def test_desktop_argv_contract():
    """与 src-tauri/src/main.rs 的 parse_open_args 同源：改一边必须同步另一边。"""
    assert handoff.desktop_argv("/A/Tavotto", handoff.Target("/p", "Fig1")) == \
        ["/A/Tavotto", "--open", "/p", "--stem", "Fig1"]
    assert handoff.desktop_argv("/A/Tavotto", handoff.Target("/p", None)) == \
        ["/A/Tavotto", "--open", "/p"]


def test_launch_desktop_spawns_the_app():
    seen = []
    out = handoff.launch(handoff.Target("/p", "Fig1"), system="darwin",
                         environ={handoff.APP_ENV: "/A/Tavotto"},
                         isfile=lambda p: p == "/A/Tavotto",
                         spawn=lambda argv, **kw: seen.append((argv, kw)))
    assert out["mode"] == "desktop"
    assert seen[0][0] == ["/A/Tavotto", "--open", "/p", "--stem", "Fig1"]


def test_launch_desktop_required_but_missing():
    with pytest.raises(handoff.HandoffError, match="桌面应用"):
        handoff.launch(handoff.Target("/p", None), prefer="desktop",
                       system="darwin", environ={}, isfile=lambda p: False)


def test_launch_browser_hands_off_to_running_instance():
    """已经有实例在跑：让它开这个项目并带上 pj——绝不再起一个去抢端口。"""
    calls, opened = [], []

    def http(url, payload=None, timeout=1.0):
        calls.append((url, payload))
        if url.endswith("/api/version"):
            return {"version": "0.6.0"}
        return {"id": "abc123", "figures_dir": "/p"}

    out = handoff.launch(handoff.Target("/p", "Fig1"), prefer="browser",
                         http=http, browse=opened.append,
                         spawn=lambda *a, **k: pytest.fail("不该再起进程"))
    assert out["mode"] == "browser-existing"
    assert calls[1][1] == {"path": "/p"}
    assert opened == ["http://127.0.0.1:5089/?pj=abc123&open=Fig1"]


def test_launch_browser_starts_a_new_instance():
    seen = []
    out = handoff.launch(handoff.Target("/p", "Fig1"), prefer="browser",
                         http=lambda *a, **k: None,
                         spawn=lambda argv, **kw: seen.append(argv),
                         browse=lambda url: pytest.fail("新进程自己开浏览器"))
    assert out["mode"] == "browser-new"
    assert seen[0][1:] == ["-m", "tavotto", "--figures", "/p",
                           "--port", "5089", "--open-stem", "Fig1"]


def test_browser_url_escapes_stem():
    """stem 里有空格 / 中文 / & 都得能过——URL 是拼出来的，不转义就串参数。"""
    url = handoff.browser_url(5089, handoff.Target("/p", "图 1&2"))
    assert url == "http://127.0.0.1:5089/?open=%E5%9B%BE%201%262"


def test_browser_url_without_stem_is_bare_root():
    assert handoff.browser_url(5089, handoff.Target("/p", None)) == \
        "http://127.0.0.1:5089/"


# ------------------------------ CLI 契约 ---------------------------------
def test_cli_json_no_launch(figures, capsys):
    code = handoff.cli([str(figures / "Fig1_demo.pdf"), "--json", "--no-launch"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["stem"] == "Fig1_demo"
    assert data["registry"]["parameterizable"] is True
    assert data["launch"] is None


def test_cli_reports_failure_as_json(tmp_path, capsys):
    code = handoff.cli([str(tmp_path / "nope.pdf"), "--json"])
    assert code == 2
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "路径不存在" in data["error"]


def test_cli_rejects_conflicting_flags(figures, capsys):
    assert handoff.cli([str(figures), "--desktop", "--browser"]) == 2


def test_handoff_stays_stdlib_only():
    """Flask 父进程与 CLI 都 import 它：绝不能把 flask / matplotlib 拖进来。"""
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; import tavotto.engine.handoff; "
         "print([m for m in ('flask', 'matplotlib', 'numpy') if m in sys.modules])"],
        capture_output=True, text=True, check=True,
        env={**os.environ, "PYTHONPATH": src})
    assert out.stdout.strip() == "[]"


# ========================= `tavotto open` 的机器接口 ======================
# 外部程序（Codex 插件、编辑器、安装器）读的就是这一层：一行 JSON + 稳定的
# error code + 「--no-launch 真的不起界面」。这几条把它钉住。

def _run_cli(argv, monkeypatch):
    """跑一次 `tavotto open …`，返回 (退出码, 解析出来的 JSON, 起过的界面)。

    桌面唤起的两条真实现（LaunchServices / spawn+就绪轮询）各有自己的单测；
    这里只验 CLI 契约，把它们替换成「记下 argv 契约、立刻就绪」的假实现。
    """
    launched = []

    def fake_launch(app, *a, **kw):
        target = a[-1] if a and isinstance(a[-1], handoff.Target) else a[0]
        argv_contract = handoff.desktop_argv(app, target)
        launched.append(argv_contract)
        return {"mode": "desktop", "app": app, "argv": argv_contract,
                "via": "fake", "handoff": "launched", "pid": 4242,
                "ready": "process_alive", "ready_ms": 1}

    monkeypatch.setattr(handoff, "_launch_desktop_via_open",
                        lambda app, bundle, target, **kw: fake_launch(app, target))
    monkeypatch.setattr(handoff, "_launch_desktop_via_spawn",
                        lambda app, target, **kw: fake_launch(app, target))
    monkeypatch.setattr(handoff, "find_desktop_app",
                        lambda **kw: "/Applications/Tavotto.app/Contents/MacOS/Tavotto")
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = handoff.cli(argv)
    lines = buf.getvalue().strip().splitlines()
    payload = json.loads(lines[-1]) if lines else None
    return rc, payload, launched


def test_no_launch_registers_without_starting_anything(figures, monkeypatch):
    """`--json --no-launch`：登记做完，界面一个都不起（浏览器也不许开）。"""
    opened = []
    monkeypatch.setattr(handoff.webbrowser, "open", lambda url: opened.append(url))
    rc, out, launched = _run_cli(
        [str(figures / "Fig1_demo.pdf"), "--json", "--no-launch"], monkeypatch)
    assert rc == 0
    assert out["ok"] is True and out["protocol"] == 1
    assert out["stem"] == "Fig1_demo"
    assert out["registry"]["parameterizable"] is True
    assert out["registry"]["status"] == "created"
    assert out["launch"] is None
    assert launched == [] and opened == []
    assert engine_registry.registry_path(figures).is_file()


def test_second_call_launches_the_native_app(figures, monkeypatch):
    """不带 --no-launch 就唤起桌面应用——**不是浏览器**。"""
    opened = []
    monkeypatch.setattr(handoff.webbrowser, "open", lambda url: opened.append(url))
    rc, out, launched = _run_cli([str(figures / "Fig1_demo.pdf"), "--json"], monkeypatch)
    assert rc == 0
    assert out["launch"]["mode"] == "desktop"
    assert launched == [["/Applications/Tavotto.app/Contents/MacOS/Tavotto",
                         "--open", str(figures), "--stem", "Fig1_demo"]]
    assert opened == []                     # 装了桌面版就绝不弹浏览器


def test_registry_is_left_alone_on_the_second_call(figures, monkeypatch):
    rc, first, _ = _run_cli([str(figures / "Fig1_demo.pdf"), "--json",
                             "--no-launch"], monkeypatch)
    rc, second, _ = _run_cli([str(figures / "Fig1_demo.pdf"), "--json",
                              "--no-launch"], monkeypatch)
    assert first["registry"]["status"] == "created"
    assert second["registry"]["status"] == "already"


def test_paths_with_spaces_and_chinese_are_not_split(tmp_path, monkeypatch):
    """带空格与中文的路径原样走完全程——参数是数组，不是拼出来的命令行。"""
    project = tmp_path / "我的 图库"
    project.mkdir()
    (project / "图 1.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    rc, out, launched = _run_cli([str(project / "图 1.pdf"), "--json"], monkeypatch)
    assert rc == 0
    assert out["project"] == str(project) and out["stem"] == "图 1"
    assert launched[0][2] == str(project) and launched[0][4] == "图 1"


def test_missing_path_has_a_stable_code(tmp_path, monkeypatch):
    rc, out, _ = _run_cli([str(tmp_path / "没有这张图.pdf"), "--json"], monkeypatch)
    assert rc == 2
    assert out["ok"] is False and out["code"] == "path_not_found"


def test_unsupported_file_type_has_a_stable_code(tmp_path, monkeypatch):
    target = tmp_path / "notes.txt"
    target.write_text("x", encoding="utf-8")
    rc, out, _ = _run_cli([str(target), "--json"], monkeypatch)
    assert rc == 2 and out["code"] == "unsupported_file"


def test_broken_registry_has_a_stable_code(figures, monkeypatch):
    engine_registry.registry_path(figures).write_text("{ 不是 JSON", encoding="utf-8")
    rc, out, _ = _run_cli([str(figures / "Fig1_demo.pdf"), "--json"], monkeypatch)
    assert rc == 2 and out["code"] in {"registry_invalid", "project_unreadable"}


#: `os.geteuid` 在 Windows 上根本不存在，而 skipif 的参数是 **import 期**求值的
#: ——写成 `os.geteuid() == 0` 会让整个文件在 Windows 上收集失败（连带把别的
#: 用例一起藏起来，CI 实测）。判据本身要在能求值的前提下才谈得上跳过。
_is_root = getattr(os, "geteuid", lambda: -1)() == 0


@pytest.mark.skipif(os.name == "nt" or _is_root,
                    reason="Windows 上 chmod 挡不住写入；root 无视权限位")
def test_unwritable_project_reports_registry_write_failed(figures, monkeypatch):
    """图库目录只读时报 `registry_write_failed`，**不是** traceback。

    以前 write_config 的 OSError 会一路冒到 `tavotto open` 外面：插件那侧看到的
    是「脚本挂了」，用户完全不知道要去改目录权限。
    """
    figures.chmod(0o500)
    try:
        rc, out, _ = _run_cli([str(figures / "Fig1_demo.pdf"), "--json"], monkeypatch)
    finally:
        figures.chmod(0o700)
    assert rc == 2
    assert out["code"] == "registry_write_failed"
    assert "写不进去" in out["error"]


def test_launch_failure_has_a_stable_code(figures, monkeypatch):
    """桌面应用在、但起不来（权限/被杀软拦）——与「没装」是两回事。"""
    monkeypatch.setattr(handoff, "find_desktop_app", lambda **kw: "/A/Tavotto")

    def boom(argv, **kw):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(handoff, "_spawn_detached", boom)
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = handoff.cli([str(figures / "Fig1_demo.pdf"), "--json"])
    out = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert rc == 2 and out["code"] == "launch_failed"


def test_conflicting_launch_flags_still_emit_json(figures, monkeypatch):
    rc, out, _ = _run_cli([str(figures / "Fig1_demo.pdf"), "--json",
                           "--desktop", "--browser"], monkeypatch)
    assert rc == 2 and out["code"] == "bad_launch_mode"


def test_every_handoff_error_carries_a_code():
    """`HandoffError` 不许再裸抛：没有 code 的那一条，调用方只能去匹配中文。"""
    import inspect
    src = inspect.getsource(handoff)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) \
                and getattr(node.exc.func, "id", "") == "HandoffError":
            assert len(node.exc.args) == 2, \
                f"handoff.py 第 {node.lineno} 行的 HandoffError 没给 code"


# ============ 唤起：惯例位置之外的安装（Codex review 的两条） =============
# 发现链找得到 CLI、唤起却按惯例位置找 = 用户明明装了桌面版，交接却静默退回
# 浏览器模式。这几条把「唤起也认清单 / 也认自己旁边那个壳」钉住。

def test_launch_uses_the_desktop_recorded_in_the_manifest(tmp_path, monkeypatch):
    """用户把 Tavotto.app 拖出了 /Applications：清单里记着它在哪。"""
    moved = tmp_path / "Tools" / "Tavotto.app" / "Contents" / "MacOS" / "Tavotto"
    moved.parent.mkdir(parents=True)
    moved.write_text("gui", encoding="utf-8")
    from tavotto.engine import locate
    locate.write_manifest({"version": "1", "cli": None, "desktop": str(moved),
                           "install_dir": None, "source": "app"})

    got = handoff.desktop_app_candidates(environ=dict(os.environ))
    assert str(moved) in got, "清单里的桌面 App 没进候选"
    assert handoff.find_desktop_app(environ=dict(os.environ)) == str(moved)


def test_manifest_desktop_that_no_longer_exists_is_ignored(tmp_path):
    """清单是缓存不是真相：路径没了就当没有，别拿它去 spawn。

    （不断言「找不到任何桌面 App」——开发机上 /Applications 里可能真装着一个，
    那属于惯例位置那条腿，与这里要验的事无关。）
    """
    from tavotto.engine import locate
    gone = str(tmp_path / "gone" / "Tavotto")
    locate.write_manifest({"version": "1", "cli": None, "desktop": gone,
                           "install_dir": None, "source": "app"})
    assert gone not in handoff.desktop_app_candidates(environ=dict(os.environ))


def test_frozen_prefers_the_shell_sitting_next_to_itself(tmp_path, monkeypatch):
    """冻结产物：壳与 CLI 的相对位置是打包时定死的，比惯例位置和清单都准。

    落点用 locate 自己算（各平台形状不同，写死就只在写它的那个平台上成立）；
    这条验的是**优先级**，形状本身由 test_install_locate 的 describe_self 用例看着。
    """
    from tavotto.engine import locate
    root = tmp_path / ("Tavotto.app" if sys.platform == "darwin" else "Tavotto")
    cli = pathlib.Path(locate.cli_exe_for(str(root)))
    shell = pathlib.Path(locate.desktop_exe_for(str(root)))
    for path, body in ((cli, "cli"), (shell, "gui")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(cli))
    got = handoff.desktop_app_candidates(environ=dict(os.environ))
    assert got[0] == str(shell), f"冻结产物没把身边那个壳排在最前: {got[:3]}"


def test_candidates_have_no_duplicates(tmp_path):
    """惯例位置与清单可能指同一个文件——候选里不该出现两遍。"""
    got = handoff.desktop_app_candidates(system="darwin",
                                         environ={"HOME": "/Users/x"})
    assert len(got) == len(set(got))


def test_frozen_browser_fallback_never_builds_dash_m_tavotto(figures, monkeypatch):
    """冻结产物里没有 `-m tavotto` 这回事。

    那时 `sys.executable` 就是 Tavotto 自己（tavotto-cli.exe / Tavotto.exe），
    拼成 `tavotto-cli -m tavotto --figures …` 会在 argparse 里报
    unrecognized arguments 当场退出——用户看到的是「点了没反应」。
    """
    spawned = []
    monkeypatch.setattr(handoff, "find_desktop_app", lambda **kw: None)
    monkeypatch.setattr(handoff, "_spawn_detached",
                        lambda argv, **kw: spawned.append(argv))
    monkeypatch.setattr(handoff, "_http_json", lambda *a, **kw: None)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "C:\\P\\Tavotto\\tavotto-cli.exe")
    out = handoff.launch(handoff.Target(str(figures), "Fig1_demo"),
                         prefer="browser", http=lambda *a, **kw: None)
    assert out["mode"] == "browser-new"
    assert "-m" not in spawned[0], f"冻结产物拼出了 -m: {spawned[0]}"
    assert spawned[0][:2] == ["C:\\P\\Tavotto\\tavotto-cli.exe", "--figures"]


def test_source_mode_browser_fallback_still_uses_dash_m(figures, monkeypatch):
    """源码 / pip 模式照旧走 `python -m tavotto`——那条路一个字没改。"""
    spawned = []
    monkeypatch.setattr(handoff, "find_desktop_app", lambda **kw: None)
    monkeypatch.setattr(handoff, "_spawn_detached",
                        lambda argv, **kw: spawned.append(argv))
    monkeypatch.delattr(sys, "frozen", raising=False)
    handoff.launch(handoff.Target(str(figures), None), prefer="browser",
                   http=lambda *a, **kw: None)
    assert spawned[0][:3] == [sys.executable, "-m", "tavotto"]
