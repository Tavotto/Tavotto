"""「这台机器上的 Tavotto 在哪」——安装清单与发现链。

背景（也是这一整个文件存在的理由）：只装了桌面版的 Windows 用户那里，Codex
插件一直报「没装 Tavotto」。桌面版装的 `Tavotto.exe` 是 **GUI 子系统**的可执行
文件，当命令行调它拿不到 stdout；插件当时只会查 `TAVOTTO_CLI` / PATH /
当前解释器，三条全落空。修法是安装包里另带一个 console 版 `tavotto-cli`，
再用一份**安装清单**把它的绝对路径记下来。

这里的 Windows 用例全部跑在 macOS/Linux 的 CI 上——`engine/locate.py` 与插件
两侧都是纯 os.path 字符串拼接，就是为了这件事（同 `engine/runtime.py`）。
真安装产物的验收在 `.github/workflows/nightly.yml` 的「装一遍再冒烟」那条链路。
"""
import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from tavotto.engine import config as engine_config, locate

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_HANDOFF = (ROOT / "codex-plugin" / "skills" / "tavotto-figure" /
                  "scripts" / "handoff.py")

WIN_ENV = {
    "LOCALAPPDATA": "C:\\Users\\张三\\AppData\\Local",
    "PROGRAMFILES": "C:\\Program Files",
    "PROGRAMFILES(X86)": "C:\\Program Files (x86)",
    "APPDATA": "C:\\Users\\张三\\AppData\\Roaming",
}
WIN_CURRENT_USER = "C:\\Users\\张三\\AppData\\Local\\Tavotto"
WIN_CLI = WIN_CURRENT_USER + "\\sidecar\\Tavotto\\tavotto-cli.exe"
WIN_DESKTOP = WIN_CURRENT_USER + "\\Tavotto.exe"
PF_ROOT = "C:\\Program Files\\Tavotto"


def only(*paths: str):
    """一个假文件系统：只有这些路径存在。"""
    existing = set(paths)
    return lambda p: p in existing


def nothing_on_path(_name):
    return None


def find(system, environ, isfile, **kw):
    return locate.find_cli(system=system, environ=environ, isfile=isfile,
                           which=nothing_on_path, reg_dirs=(), **kw)


# ------------------------------ 清单本身 ---------------------------------
def test_manifest_dir_matches_config_dir():
    """清单落在用户配置目录。规则复刻自 config.config_dir()，不许漂移。

    locate 里那份是字符串实现（为了能在 macOS 上单测 Windows 分支），
    config 里那份是 pathlib——两者对同一台机器必须给出同一个答案。
    """
    assert locate.manifest_dir() == str(engine_config.config_dir())
    assert locate.manifest_path() == str(engine_config.config_dir() / "install.json")


def test_manifest_roundtrip(tmp_path):
    path = str(tmp_path / "cfg" / "install.json")
    locate.write_manifest({"version": "9.9.9", "cli": str(tmp_path / "cli"),
                           "desktop": str(tmp_path / "app"),
                           "install_dir": str(tmp_path), "source": "installer"},
                          path=path)
    (tmp_path / "cli").write_text("x", encoding="utf-8")
    (tmp_path / "app").write_text("x", encoding="utf-8")
    got = locate.read_manifest(path=path)
    assert got["version"] == "9.9.9"
    assert got["cli"] == str(tmp_path / "cli")
    assert got["desktop"] == str(tmp_path / "app")
    assert got["stale"] is False
    assert json.loads(Path(path).read_text(encoding="utf-8"))["protocol"] == 1


def test_manifest_drops_paths_that_no_longer_exist(tmp_path):
    """清单是缓存不是真相：卸载后它还在，里面的路径已经没了。

    不核实就会拿着一条早就不存在的路径去 spawn——报出来的是「执行不了」，
    而用户需要看到的是「没装，去装一个」。
    """
    path = str(tmp_path / "install.json")
    locate.write_manifest({"version": "1", "cli": str(tmp_path / "gone"),
                           "desktop": None, "install_dir": None,
                           "source": "installer"}, path=path)
    got = locate.read_manifest(path=path)
    assert got["cli"] is None and got["stale"] is True


def test_manifest_from_another_protocol_is_ignored(tmp_path):
    path = tmp_path / "install.json"
    path.write_text(json.dumps({"protocol": 99, "cli": "/x"}), encoding="utf-8")
    assert locate.read_manifest(path=str(path)) is None


def test_broken_manifest_is_not_an_error(tmp_path):
    path = tmp_path / "install.json"
    path.write_text("{ 这不是 JSON", encoding="utf-8")
    assert locate.read_manifest(path=str(path)) is None
    assert locate.read_manifest(path=str(tmp_path / "nope.json")) is None


def test_remove_manifest_is_idempotent(tmp_path):
    path = str(tmp_path / "install.json")
    locate.write_manifest({"version": "1"}, path=path)
    assert locate.remove_manifest(path=path) is True
    assert locate.remove_manifest(path=path) is False       # 已经没了，不是错误


# ------------------------------ 发现链顺序 -------------------------------
def test_explicit_override_wins_over_everything():
    """TAVOTTO_CLI 是高级覆盖：用户指定的永远第一，哪怕别处也找得到。"""
    env = {**WIN_ENV, locate.CLI_ENV: "D:\\我的 工具\\tavotto.exe"}
    got = locate.find_cli(system="win32", environ=env, isfile=only(WIN_CLI),
                          which=lambda n: "C:\\py\\Scripts\\tavotto.exe",
                          reg_dirs=())
    assert got["cmd"] == ["D:\\我的 工具\\tavotto.exe"]
    assert got["source"] == "env"


def test_path_cli_beats_the_desktop_install():
    """PATH 里有 tavotto（pip/pipx 装的）就用它——既有行为，不许被新链路顶掉。"""
    got = locate.find_cli(system="win32", environ=WIN_ENV, isfile=only(WIN_CLI),
                          which=lambda n: "C:\\py\\Scripts\\tavotto.exe",
                          reg_dirs=())
    assert got["cmd"] == ["C:\\py\\Scripts\\tavotto.exe"]
    assert got["source"] == "path"


def test_manifest_is_used_when_path_has_nothing(tmp_path):
    """清单排在「已知安装位置」之前：用户装到别处时只有它知道。

    这条用**本平台**跑（清单要真的落在磁盘上），路径形状的跨平台部分由
    test_plugin_mirrors_the_locator 与 manifest_path 的用例覆盖。
    """
    env = {"TAVOTTO_CONFIG_DIR": str(tmp_path)}
    manifest = locate.manifest_path(environ=env)
    portable = str(tmp_path / "便携版" / "Tavotto" / "tavotto-cli")
    locate.write_manifest({"version": "1", "cli": portable, "desktop": None,
                           "install_dir": None, "source": "app"}, path=manifest)
    got = locate.find_cli(environ=env, isfile=only(portable, manifest),
                          which=nothing_on_path, reg_dirs=())
    assert got["cmd"] == [portable] and got["source"] == "manifest"


def test_known_locations_still_work_when_the_manifest_is_gone(tmp_path):
    """清单被策略删了 / 从没写成过：已知安装位置那条腿必须独立管用。

    这就是「不把任何单一机制当唯一依据」的实测——清单、注册表都可能不在。
    """
    env = {**WIN_ENV, "TAVOTTO_CONFIG_DIR": str(tmp_path)}   # 目录里没有 install.json
    got = locate.find_cli(system="win32", environ=env, isfile=only(WIN_CLI),
                          which=nothing_on_path, reg_dirs=())
    assert got["cmd"] == [WIN_CLI] and got["source"] == "install"


def test_current_user_install_is_found_without_a_manifest():
    """**这条就是那个 bug 的正面用例**：只装了桌面版，没有清单、PATH 里没有。"""
    got = find("win32", WIN_ENV, only(WIN_CLI, WIN_DESKTOP))
    assert got["cmd"] == [WIN_CLI]
    assert got["source"] == "install"
    assert got["desktop"] == WIN_DESKTOP


def test_legacy_program_files_install_is_found():
    """老的管理员安装位置仍然认——升级上来的用户 $INSTDIR 就在那儿。"""
    cli = PF_ROOT + "\\sidecar\\Tavotto\\tavotto-cli.exe"
    got = find("win32", WIN_ENV, only(cli, PF_ROOT + "\\Tavotto.exe"))
    assert got["cmd"] == [cli] and got["source"] == "install"


def test_paths_with_spaces_and_chinese_survive():
    env = {"LOCALAPPDATA": "D:\\我的 程序\\Local",
           "APPDATA": "D:\\我的 程序\\Roaming"}
    cli = "D:\\我的 程序\\Local\\Tavotto\\sidecar\\Tavotto\\tavotto-cli.exe"
    got = find("win32", env, only(cli))
    assert got["cmd"] == [cli]
    # 交给 subprocess 的是**数组**，路径原样一条，绝不在这里被空格拆开
    assert len(got["cmd"]) == 1 and " " in got["cmd"][0]


def test_macos_app_bundle_is_found():
    cli = "/Applications/Tavotto.app/Contents/Resources/sidecar/Tavotto/tavotto-cli"
    got = find("darwin", {"HOME": "/Users/张三"}, only(cli))
    assert got["cmd"] == [cli]


def test_desktop_without_cli_is_reported_separately():
    """装了桌面版但那一版没带 CLI（旧安装包）。

    这**不是**「没装 Tavotto」：用户明明装了，该提示的是升级。笼统报
    tavotto_missing 会让他去装一个已经装着的东西，然后发现还是不行。
    """
    got = find("win32", WIN_ENV, only(WIN_DESKTOP))
    assert got["cmd"] is None
    assert got["desktop"] == WIN_DESKTOP


def test_nothing_installed_reports_nothing_and_says_where_it_looked():
    got = find("win32", WIN_ENV, only())
    assert got["cmd"] is None and got["desktop"] is None
    # 找过哪儿要说出来：用户装到了别处时，这份清单就是他要看的东西
    assert any("tavotto-cli.exe" in p for p in got["searched"])


def test_registry_is_a_supplement_not_the_only_way():
    """HKCU 的 InstallLocation 只是补充。

    企业策略能锁注册表，所以它既不能是唯一依据（前两条腿仍要管用），
    也不能被忽略（用户装到了非默认位置时只有它知道）。
    """
    odd = "E:\\Tools\\Tavotto"
    cli = odd + "\\sidecar\\Tavotto\\tavotto-cli.exe"
    # 只有注册表知道这个位置
    got = locate.find_cli(system="win32", environ=WIN_ENV, isfile=only(cli),
                          which=nothing_on_path, reg_dirs=(odd,))
    assert got["cmd"] == [cli] and got["source"] == "registry"
    # 注册表读不到（被策略挡住）也不影响惯例位置那条腿
    got = find("win32", WIN_ENV, only(WIN_CLI))
    assert got["cmd"] == [WIN_CLI]


def test_hkcu_lookup_is_silent_off_windows():
    """非 Windows 上不许炸，也不许 import winreg。"""
    assert locate.hkcu_install_dirs() == [] or os.name == "nt"


# --------------------------- 我自己装在哪儿 ------------------------------
def test_describe_self_from_a_frozen_windows_sidecar():
    """sidecar 认得出自己旁边的 CLI 和上两层的壳——安装到哪个盘都不用猜。"""
    exe = WIN_CURRENT_USER + "\\sidecar\\Tavotto\\Tavotto.exe"
    me = locate.describe_self(executable=exe, frozen=True, system="win32",
                              environ=WIN_ENV, version="1.2.3",
                              isfile=only(WIN_CLI, WIN_DESKTOP))
    assert me["cli"] == WIN_CLI
    assert me["desktop"] == WIN_DESKTOP
    assert me["install_dir"] == WIN_CURRENT_USER


def test_describe_self_from_a_frozen_macos_bundle():
    app = "/Applications/Tavotto.app"
    exe = app + "/Contents/Resources/sidecar/Tavotto/Tavotto"
    cli = app + "/Contents/Resources/sidecar/Tavotto/tavotto-cli"
    desktop = app + "/Contents/MacOS/Tavotto"
    me = locate.describe_self(executable=exe, frozen=True, system="darwin",
                              environ={"HOME": "/Users/x"}, version="1.2.3",
                              isfile=only(cli, desktop))
    assert me["cli"] == cli and me["desktop"] == desktop
    assert me["install_dir"] == app


def test_describe_self_from_the_cli_shim_itself():
    """被调用的就是 tavotto-cli 时，它认自己，不去旁边找另一个。"""
    me = locate.describe_self(executable=WIN_CLI, frozen=True, system="win32",
                              environ=WIN_ENV, version="1", isfile=only(WIN_CLI))
    assert me["cli"] == WIN_CLI


def test_describe_self_from_a_pip_install(tmp_path):
    r"""pip / pipx 装的：CLI 是解释器同级的 console script。

    这条**按本平台**跑（要真的建文件），所以目录名与后缀都得跟着平台走：
    Windows 是 `Scripts\tavotto.exe`，POSIX 是 `bin/tavotto`。
    """
    win = os.name == "nt"
    scripts = tmp_path / ("Scripts" if win else "bin")
    scripts.mkdir()
    exe = scripts / ("tavotto.exe" if win else "tavotto")
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    me = locate.describe_self(executable=str(scripts / "python"), frozen=False,
                              prefix=str(tmp_path), environ={"HOME": str(tmp_path)},
                              version="1")
    assert os.path.normpath(me["cli"]) == os.path.normpath(str(exe))
    assert me["source"] == "module"


def test_dev_tree_dist_has_no_desktop_shell():
    """`python scripts/build_desktop.py --skip-tauri` 只出 sidecar，没有壳。"""
    exe = "/repo/dist/Tavotto/Tavotto"
    me = locate.describe_self(executable=exe, frozen=True, system="darwin",
                              environ={"HOME": "/h"}, version="1",
                              isfile=only("/repo/dist/Tavotto/tavotto-cli"))
    assert me["cli"] == "/repo/dist/Tavotto/tavotto-cli"
    assert me["desktop"] is None
    assert me["install_dir"] == "/repo/dist/Tavotto"


# --------------------------- 与插件那侧同源 ------------------------------
def _plugin_module():
    spec = importlib.util.spec_from_file_location("_plugin_handoff", PLUGIN_HANDOFF)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MIRROR_ENVS = [
    ("win32", WIN_ENV),
    ("win32", {"LOCALAPPDATA": "D:\\我的 程序\\Local",
               "APPDATA": "D:\\我的 程序\\Roaming"}),
    ("win32", {}),                                   # 环境变量都没有：不许炸
    ("darwin", {"HOME": "/Users/张三"}),
    ("darwin", {}),
    ("linux", {"HOME": "/home/x"}),
    ("linux", {"HOME": "/home/x", "XDG_CONFIG_HOME": "/home/x/.conf"}),
]


def test_plugin_mirrors_the_locator():
    """插件那份路径规则与 engine/locate.py 逐条相等。

    插件跑在用户机器上，import 不到 tavotto——这份镜像无法避免。能避免的是
    「两侧悄悄漂开」：改一边不同步另一边，表现是某类安装突然发现不了，
    而两边的源码各看各的都很合理。所以在一整张环境矩阵上比，不比源码文本。
    """
    plugin = _plugin_module()
    assert plugin.PROTOCOL == locate.PROTOCOL_VERSION
    assert plugin.SIDECAR_REL == locate.SIDECAR_REL
    assert plugin.MANIFEST_NAME == locate.MANIFEST_NAME
    assert plugin.CLI_ENV == locate.CLI_ENV

    assert plugin.UNINSTALL_KEY == locate.UNINSTALL_KEY

    for system, env in MIRROR_ENVS:
        roots = locate.install_roots(system=system, environ=env)
        assert plugin.install_roots(system, env) == roots, (system, env)
        # HKCU 问出来的位置（第 5 条腿）两侧的排法也必须一样：只有一侧有的话，
        # 「装在非默认目录 + 没有清单」的机器上会出现两个不同的答案
        extra = ("E:\\Tools\\Tavotto", "  ", roots[0] if roots else "X")
        assert plugin.install_roots(system, env, extra) == \
            locate.install_roots(system=system, environ=env, extra=extra), (system, env)
        assert plugin.manifest_path(system, env) == \
            locate.manifest_path(system=system, environ=env), (system, env)
        for root in roots:
            assert plugin.cli_exe_for(root, system) == \
                locate.cli_exe_for(root, system=system), (system, root)
            assert plugin.desktop_exe_for(root, system) == \
                locate.desktop_exe_for(root, system=system), (system, root)


REG_ROOT = "E:\\Tools\\Tavotto"
REG_CLI = REG_ROOT + "\\sidecar\\Tavotto\\tavotto-cli.exe"


@pytest.mark.parametrize("present,reg,expect_source", [
    ((WIN_CLI, WIN_DESKTOP), (), "install"),
    ((WIN_DESKTOP,), (), None),
    ((), (), None),
    # 装在非默认目录、清单又没写成：只有注册表知道。**两侧都得知道。**
    ((REG_CLI,), (REG_ROOT,), "registry"),
    # 注册表读不到（组策略锁了）时，惯例位置那条腿照样管用
    ((WIN_CLI,), (), "install"),
])
def test_plugin_and_locator_agree_on_the_same_filesystem(present, reg, expect_source):
    """同一个（假的）文件系统上，两侧给出同一个答案。

    这条比「源码里都有 winreg」强得多：它比的是**结果**，包括优先级顺序。
    """
    plugin = _plugin_module()
    isfile = only(*present)
    mine = locate.find_cli(system="win32", environ=WIN_ENV, isfile=isfile,
                           which=nothing_on_path, reg_dirs=reg)
    theirs = plugin.find_tavotto("win32", WIN_ENV, isfile, nothing_on_path,
                                 reg_dirs=reg)
    for key in ("cmd", "source", "desktop"):
        assert mine[key] == theirs[key], key
    assert mine["source"] == expect_source


# ------------------------------ 打包卫生 ---------------------------------
def test_locator_never_instantiates_a_foreign_pathlib():
    """两侧都不许出现 `Path(...)`。

    `Path()` 按 `os.name` 分派，在 macOS 上构造 Windows 路径直接抛
    UnsupportedOperation——上面那一整批 win32 用例会连跑都跑不起来。
    （同 engine/runtime.py 的同名纪律。）
    """
    for path in (ROOT / "src" / "tavotto" / "engine" / "locate.py", PLUGIN_HANDOFF):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in {"Path", "PurePath", "WindowsPath", "PosixPath"}:
                raise AssertionError(f"{path.name} 第 {node.lineno} 行用了 pathlib")


def test_spec_ships_a_console_cli_next_to_the_gui_binary():
    """`packaging/tavotto.spec` 必须出第二个 console 版 exe，名字对得上。

    少了它，桌面安装包里就只有不能当 CLI 用的 GUI 可执行文件——功能一样不缺，
    只有「Codex 插件找不到 Tavotto」这一种表现，而那要等用户装完才发现。
    """
    spec = (ROOT / "packaging" / "tavotto.spec").read_text(encoding="utf-8")
    assert 'name="tavotto-cli"' in spec
    assert "console=True" in spec
    # 两个 exe 必须进同一个 COLLECT：分开打就是两份 _internal，包大一倍
    collect = spec.split("coll = COLLECT(")[1].split(")")[0]
    assert "exe," in collect and "cli," in collect
    # 名字是发现链的一部分
    assert locate.CLI_NAME.replace(".exe", "") == "tavotto-cli"


def test_frozen_entry_dispatches_subcommands_before_importing_flask():
    """`tavotto-cli open …` 不该为一次交接付整个 Flask 的冷启动。"""
    entry = (ROOT / "packaging" / "entry.py").read_text(encoding="utf-8")
    before, after = entry.split("from tavotto.app import main as app_main")
    assert "engine_cli.dispatch" in before, "子命令分派必须在 import app 之前"
    assert "from tavotto.engine import cli" in before


# ============================ `tavotto doctor` ===========================
# 装完之后的**无 GUI 健康检查**，安装器跑的就是它。这几条起真进程、读真 JSON
# ——不是对源码的断言。

import subprocess  # noqa: E402


def _doctor(tmp_path, *args, env=None):
    environ = {**os.environ, "TAVOTTO_CONFIG_DIR": str(tmp_path),
               "PYTHONPATH": str(ROOT / "src"), **(env or {})}
    proc = subprocess.run([sys.executable, "-m", "tavotto", "doctor", *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=environ)
    return proc


def test_doctor_json_is_machine_readable_and_starts_nothing(tmp_path):
    proc = _doctor(tmp_path, "--json")
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is True
    assert out["product"] == "Tavotto"
    assert out["protocol"] == locate.PROTOCOL_VERSION
    assert out["manifest"]["path"].endswith("install.json")
    # 只体检不写盘
    assert out["manifest"]["written"] is False
    assert not (tmp_path / "install.json").exists()
    # 没起服务：端口/浏览器一个字都不该出现
    assert "http://" not in proc.stdout


def test_doctor_writes_the_manifest_for_the_installer(tmp_path):
    proc = _doctor(tmp_path, "--json", "--write-manifest")
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["manifest"]["written"] is True
    written = json.loads((tmp_path / "install.json").read_text(encoding="utf-8"))
    assert written["protocol"] == locate.PROTOCOL_VERSION
    assert written["product"] == "Tavotto"
    assert written["cli"] == out["cli"]


def test_doctor_removes_the_manifest_for_the_uninstaller(tmp_path):
    _doctor(tmp_path, "--json", "--write-manifest")
    assert (tmp_path / "install.json").exists()
    proc = _doctor(tmp_path, "--json", "--remove-manifest")
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["manifest"]["removed"] is True
    assert not (tmp_path / "install.json").exists()


def test_doctor_reads_back_what_it_wrote(tmp_path):
    _doctor(tmp_path, "--json", "--write-manifest")
    out = json.loads(_doctor(tmp_path, "--json").stdout.strip().splitlines()[-1])
    assert out["manifest"]["found"] is True
    assert out["manifest"]["stale"] is False


def test_doctor_speaks_human_without_json(tmp_path):
    proc = _doctor(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "Tavotto" in proc.stdout and "交接协议" in proc.stdout


def test_doctor_rejects_contradictory_flags(tmp_path):
    """参数拼错了也要回 JSON——`--json` 给了就一律给 JSON。

    这条最该被程序读懂：它恰恰是调用方自己把参数拼错了。只往 stderr 写一句
    中文，对面只能去匹配字符串（与 `tavotto open` 的 bad_launch_mode 同一条纪律）。
    """
    proc = _doctor(tmp_path, "--json", "--write-manifest", "--remove-manifest")
    assert proc.returncode == 2
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is False
    assert out["code"] == "bad_manifest_action"
    assert out["problems"][0]["code"] == "bad_manifest_action"
    assert not (tmp_path / "install.json").exists()    # 什么都没动


def test_doctor_rejects_contradictory_flags_in_human_mode(tmp_path):
    """不给 --json 时照旧说人话，别把 JSON 糊到终端上。"""
    proc = _doctor(tmp_path, "--write-manifest", "--remove-manifest")
    assert proc.returncode == 2
    assert proc.stdout.strip() == ""
    assert "不能同时给" in proc.stderr


def test_every_doctor_json_failure_has_a_code():
    """doctor 的每一条 `--json` 失败出口都要带 code。

    新增一条早退分支时最容易漏掉的就是这件事——`open` 那边已经栽过一次。
    """
    src = (ROOT / "src" / "tavotto" / "engine" / "cli.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    dumps = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "dumps"]
    assert dumps, "cli.py 里没有 json.dumps"
    for node in dumps:
        payload = node.args[0]
        if not isinstance(payload, ast.Dict):
            continue
        keys = {k.value for k in payload.keys if isinstance(k, ast.Constant)}
        if keys and keys != {"code", "message"}:
            assert "code" in keys, f"第 {node.lineno} 行的 JSON 出口没有 code"


def test_open_and_doctor_are_dispatched_before_argparse():
    """子命令都必须在主入口的 argparse 之前拦下来。

    主入口是纯 flag 形态（`tavotto --figures …`），改成 subparsers 会把既有
    命令行整个换掉；而 argparse 见到位置参数 `doctor` 只会报 unrecognized。
    """
    from tavotto.engine import cli as engine_cli
    assert set(engine_cli.COMMANDS) == {"open", "doctor", "codex"}
    assert engine_cli.dispatch([]) is None
    assert engine_cli.dispatch(["--figures", "/tmp"]) is None
    app_src = (ROOT / "src" / "tavotto" / "app.py").read_text(encoding="utf-8")
    body = app_src.split("def main():")[1]
    assert body.index("engine_cli.dispatch") < body.index("argparse.ArgumentParser")


# --------------------- doctor 的失败也要有稳定 code ----------------------
# 与 `tavotto open` 同一条纪律：文案随时可改，code 不行。只给一句中文的话，
# 调用方要区分「清单写不出来」和「这个包漏打了 CLI」就只能去匹配字符串——
# 而这两件事的处置完全不同：前者还能用，后者得重装。

def test_doctor_problems_carry_stable_codes(tmp_path):
    """配置目录写不进去 → `manifest_write_failed`，不是一句散文。"""
    blocked = tmp_path / "ro"
    blocked.mkdir()
    if os.name == "nt" or getattr(os, "geteuid", lambda: -1)() == 0:
        pytest.skip("Windows 上 chmod 挡不住写入；root 无视权限位")
    blocked.chmod(0o500)
    try:
        proc = _doctor(blocked, "--json", "--write-manifest")
    finally:
        blocked.chmod(0o700)
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert proc.returncode == 1
    assert out["ok"] is False
    assert out["code"] == "manifest_write_failed"
    assert [p["code"] for p in out["problems"]] == ["manifest_write_failed"]
    assert out["problems"][0]["message"]              # 人话也还在


def test_doctor_reports_no_code_when_healthy(tmp_path):
    out = json.loads(_doctor(tmp_path, "--json").stdout.strip().splitlines()[-1])
    assert out["ok"] is True and out["code"] is None and out["problems"] == []


def test_every_doctor_problem_is_a_coded_dict():
    """新增 problem 时不许再往里塞裸字符串。"""
    src = (ROOT / "src" / "tavotto" / "engine" / "cli.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "append" \
                and getattr(node.func.value, "id", "") == "problems":
            arg = node.args[0]
            assert isinstance(arg, ast.Dict), f"第 {node.lineno} 行 append 了非 dict"
            keys = {k.value for k in arg.keys if isinstance(k, ast.Constant)}
            assert keys == {"code", "message"}, f"第 {node.lineno} 行缺 code/message"


def test_nightly_uninstall_assertion_is_not_vacuous():
    """nightly 里「卸载后清单不该还在」必须真的验到东西。

    那条链路中间为了测「清单缺失时的回退」会**自己把清单删掉**。删了不补，
    后面那条断言就恒真——卸载钩子完全失灵也照样绿。这正是本仓库最忌讳的
    那种门禁：它还在报平安。（Codex review 抓到过一次。）

    判据是顺序：删 → …… → 重新确认它在 → 卸载 → 断言它没了。
    """
    text = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    removed = text.index("Remove-Item $manifest")
    uninstalled = text.index("Start-Process -Wait $uninst")
    gone = text.index('throw "卸载后安装清单仍在')
    between = text[removed:uninstalled]
    assert "Test-Path $manifest" in between, \
        "删掉清单之后、卸载之前没有再确认它存在——那条卸载断言是空的"
    assert removed < uninstalled < gone


def test_nightly_shell_probe_dumps_the_log_before_it_cleans_up():
    """壳没拉起 sidecar 时，**日志转储必须排在清理动作前面**。

    2026-08-18 夜里这条腿红了，而 CI 上留下的唯一线索是一句
    `Stop-Process: Cannot find a process with the process identifier 7176`
    ——壳自己先退了（正是「起不来」的典型样子），`Stop-Process` 于是失败，
    在 `$ErrorActionPreference = "Stop"` 下当场中断脚本，紧跟其后的
    sidecar.log 转储一行都没跑到。**门禁把自己的证据吞了**，剩下的报错与
    病因毫不相干。

    判据同样是顺序：先转储、再清理（且清理带 -ErrorAction SilentlyContinue）。
    """
    text = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    # 只看代码那一半：注释里提到 Stop-Process 是在解释这条纪律本身
    def code_only(block: str) -> str:
        return "\n".join(ln for ln in block.splitlines()
                          if not ln.strip().startswith("#"))

    branch = code_only(
        text[text.index("if (-not $child) {"):text.index('throw "装好的壳没有拉起 sidecar')])
    dump = branch.index("sidecar.log")
    kill = branch.index("Stop-Process")
    assert dump < kill, "清理排在日志转储前面：壳先退时会把真正的失败原因吞掉"
    assert "-ErrorAction SilentlyContinue" in branch[kill:], \
        "Stop-Process 没带 -ErrorAction SilentlyContinue：进程已退时它自己会抛"
    # 整个 nightly 里不许再有会自己抛的 Stop-Process
    for i, line in enumerate(text.splitlines(), 1):
        bare = line.strip()
        if bare.startswith("#"):
            continue
        if "Stop-Process" in bare and "SilentlyContinue" not in bare:
            raise AssertionError(
                f"nightly.yml:{i} 的 Stop-Process 没兜住「进程已经退了」：{bare}")


# ------------------- 刷新清单只补充，不抹掉（Codex #6） -------------------
def test_refresh_keeps_a_desktop_path_it_cannot_rediscover(tmp_path, monkeypatch):
    """pip 装的 tavotto 跑一次，不许把桌面版记下的 desktop 抹成空。

    同一台机器上可以既有装在非惯例位置的桌面版、又有 pip 装的 tavotto。
    pip 那次是非冻结进程，`describe_self()` 只去惯例位置找壳、找不到就是 None；
    无条件写下去 = 桌面版那条仍然有效的路径没了，此后 `tavotto open` 再也
    定位不到那个窗口，**静默**退回浏览器模式。
    """
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path))
    moved = tmp_path / "Tools" / "Tavotto.app" / "Contents" / "MacOS" / "Tavotto"
    moved.parent.mkdir(parents=True)
    moved.write_text("gui", encoding="utf-8")
    # 桌面版（冻结）先写下它在哪
    locate.write_manifest({"version": "1", "cli": None, "desktop": str(moved),
                           "install_dir": str(tmp_path / "Tools" / "Tavotto.app"),
                           "source": "app"})

    # pip 装的那份跑一次。用 system="linux" 表示「惯例位置里没有壳」——那儿
    # 本来就没有桌面发行形态，install_roots 恒为空，与开发机上 /Applications
    # 里是否真装着一份无关（那是绝对路径，env 隔离不掉）。
    locate.refresh_manifest(frozen=False, system="linux", prefix=str(tmp_path),
                            environ={"HOME": str(tmp_path)}, version="1")

    got = locate.read_manifest()
    assert got["desktop"] == str(moved), "刷新把桌面版的路径抹掉了"


def test_refresh_drops_a_desktop_path_that_no_longer_exists(tmp_path, monkeypatch):
    """沿用的前提是它**还在**——read_manifest 已经核实过，别把死路径留下来。"""
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path))
    locate.write_manifest({"version": "1", "cli": None,
                           "desktop": str(tmp_path / "gone" / "Tavotto"),
                           "install_dir": None, "source": "app"})
    locate.refresh_manifest(frozen=False, system="linux", prefix=str(tmp_path),
                            environ={"HOME": str(tmp_path)}, version="1")
    assert locate.read_manifest()["desktop"] is None


def test_refresh_still_updates_what_it_does_know(tmp_path, monkeypatch):
    """「只补充不抹掉」不等于「不再更新」：问得到的照旧覆盖。"""
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path))
    old_cli = tmp_path / "old" / "tavotto"
    old_cli.parent.mkdir()
    old_cli.write_text("x", encoding="utf-8")
    locate.write_manifest({"version": "0.0.1", "cli": str(old_cli), "desktop": None,
                           "install_dir": None, "source": "installer"})

    scripts = tmp_path / ("Scripts" if os.name == "nt" else "bin")
    scripts.mkdir()
    new_cli = scripts / ("tavotto.exe" if os.name == "nt" else "tavotto")
    new_cli.write_text("x", encoding="utf-8")
    locate.refresh_manifest(frozen=False, prefix=str(tmp_path),
                            environ={"HOME": str(tmp_path)}, version="9.9.9")

    got = locate.read_manifest()
    assert os.path.normpath(got["cli"]) == os.path.normpath(str(new_cli))
    assert got["version"] == "9.9.9"


# ---------------------------------------------------------------------------
# 子命令必须在 import Flask **之前**分派掉
# ---------------------------------------------------------------------------
#: 让 Flask / PyMuPDF 一 import 就炸，用来证明子命令根本没走到它们。
_BLOCK_UI_IMPORTS = """
import sys


class _Block:
    BAD = {"flask", "werkzeug", "pymupdf", "fitz"}

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.BAD:
            raise ImportError("这次交接不该 import " + name)
        return None


sys.meta_path.insert(0, _Block())
"""


def test_installed_entry_point_dispatches_before_importing_the_app():
    """pip / pipx 装出来的 `tavotto` 指向轻量入口，不是 `tavotto.app:main`。

    冻结产物（`packaging/entry.py`）一直是先分派后 import，pip 这条却不是——
    同一条命令在两种安装形态下行为不同：插件的交接连着调两次 `tavotto open`，
    每次都白付一整个 Flask + PyMuPDF 的冷启动。
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'tavotto = "tavotto.cli_entry:main"' in text
    src = (ROOT / "src" / "tavotto" / "cli_entry.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    for node in top:
        mod = getattr(node, "module", "") or ""
        names = {a.name for a in node.names}
        assert "app" not in mod and "app" not in names, \
            "cli_entry 在模块层 import 了 app —— 那就等于没有轻量入口"


@pytest.mark.parametrize("argv", [
    ["doctor", "--json"],
    ["open", "--help"],
])
def test_subcommands_run_without_flask_or_pymupdf(tmp_path, argv):
    """把 flask / pymupdf 变成 import 就炸，子命令仍要跑通。

    这条同时是 `doctor` 的存在理由：它本该是「装坏了怎么查」的那把工具，
    可只要某个界面依赖 import 失败（缺 DLL、装了一半、wheel 与解释器不匹配），
    旧入口会先崩在 import 上——最需要它的时候正好用不了。
    """
    import subprocess
    env = {**os.environ,
           "PYTHONPATH": str(ROOT / "src"),
           "TAVOTTO_CONFIG_DIR": str(tmp_path / "cfg"),
           "TAVOTTO_DATA_DIR": str(tmp_path / "data")}
    code = _BLOCK_UI_IMPORTS + (
        "import sys\n"
        f"sys.argv = ['tavotto', {', '.join(repr(a) for a in argv)}]\n"
        "from tavotto.cli_entry import main\n"
        "try:\n"
        "    main()\n"
        "except SystemExit as e:\n"
        "    raise SystemExit(e.code)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
    assert "不该 import" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, proc.stderr
