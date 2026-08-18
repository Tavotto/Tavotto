"""`magplot open` 的交接链路：解析目标 → 登记 stem → 唤起界面。

平台分支（桌面 App 在哪儿）在**任意一个平台上**都必须能测——所以
engine/handoff.py 全程 os.path 拼字符串。这里的 win32 用例跑在 macOS/Linux
的 CI 上，就是那条纪律的看护。
"""
import json
import os
import subprocess
import sys

import pytest

from magplot.engine import handoff, registry as engine_registry


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
    assert info["parameterizable"] is True
    assert engine_registry.open_registry(figures).for_stem("Fig1_demo")


def test_registered_stem_leaves_registry_untouched(figures):
    handoff.ensure_registered(str(figures), "Fig1_demo")
    path = engine_registry.registry_path(figures)
    before = path.read_bytes()
    info = handoff.ensure_registered(str(figures), "Fig1_demo")
    assert info == {"registry": str(path), "created": False, "added_scripts": [],
                    "added_stems": {}, "conflicts": [], "dynamic_names": [],
                    "parameterizable": True}
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


# --------------------------- 3. 唤起界面 ---------------------------------
def test_macos_candidates_are_bundle_binaries():
    got = handoff.desktop_app_candidates(system="darwin", environ={"HOME": "/Users/x"})
    assert got == ["/Applications/Magplot.app/Contents/MacOS/Magplot",
                   "/Users/x/Applications/Magplot.app/Contents/MacOS/Magplot"]


def test_windows_candidates_start_at_localappdata():
    """NSIS 是 currentUser 安装：新装固定 $LOCALAPPDATA\\Magplot。

    这条用例跑在 macOS/Linux 上——handoff 里一个 pathlib 都不用，就是为了它。
    """
    env = {"LOCALAPPDATA": "C:\\Users\\x\\AppData\\Local",
           "PROGRAMFILES": "C:\\Program Files"}
    assert handoff.desktop_app_candidates(system="win32", environ=env) == [
        "C:\\Users\\x\\AppData\\Local\\Magplot\\Magplot.exe",
        "C:\\Program Files\\Magplot\\Magplot.exe"]


def test_linux_has_no_desktop_build():
    assert handoff.desktop_app_candidates(system="linux", environ={"HOME": "/h"}) == []


def test_env_override_wins():
    env = {handoff.APP_ENV: "/tmp/dist/Magplot/Magplot", "HOME": "/Users/x"}
    assert handoff.desktop_app_candidates(system="darwin", environ=env)[0] == \
        "/tmp/dist/Magplot/Magplot"


def test_desktop_argv_contract():
    """与 src-tauri/src/main.rs 的 parse_open_args 同源：改一边必须同步另一边。"""
    assert handoff.desktop_argv("/A/Magplot", handoff.Target("/p", "Fig1")) == \
        ["/A/Magplot", "--open", "/p", "--stem", "Fig1"]
    assert handoff.desktop_argv("/A/Magplot", handoff.Target("/p", None)) == \
        ["/A/Magplot", "--open", "/p"]


def test_launch_desktop_spawns_the_app():
    seen = []
    out = handoff.launch(handoff.Target("/p", "Fig1"), system="darwin",
                         environ={handoff.APP_ENV: "/A/Magplot"},
                         isfile=lambda p: p == "/A/Magplot",
                         spawn=lambda argv, **kw: seen.append((argv, kw)))
    assert out["mode"] == "desktop"
    assert seen[0][0] == ["/A/Magplot", "--open", "/p", "--stem", "Fig1"]


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
    assert seen[0][1:] == ["-m", "magplot", "--figures", "/p",
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
         "import sys; import magplot.engine.handoff; "
         "print([m for m in ('flask', 'matplotlib', 'numpy') if m in sys.modules])"],
        capture_output=True, text=True, check=True,
        env={**os.environ, "PYTHONPATH": src})
    assert out.stdout.strip() == "[]"
