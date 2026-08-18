"""Codex 插件（codex-plugin/）的形状看护。

插件是**跟着 Magplot 一起发的**：市场清单在仓库根的 `.agents/plugins/`，
插件本体在 `codex-plugin/`。这几条断言盯的都是「坏了也不报错，只是悄悄不生效」
的那类问题——清单字段错一个 Codex 就装不上，版本漂了用户装到的是另一代约定。
"""
import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # tomllib 是 3.11 才进标准库的；3.10 上只跳过用到它的那一条
    tomllib = None

import pytest

import magplot

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "codex-plugin"
SKILL_DIR = PLUGIN / "skills" / "magplot-figure"
MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))


def test_manifest_lives_where_codex_looks(manifest):
    """`.codex-plugin/plugin.json` 是 Codex 唯一认的清单位置。"""
    assert manifest["name"] == "magplot"
    assert manifest["skills"] == "./skills/"


def test_manifest_version_tracks_the_product(manifest):
    """插件随 Magplot 发版：版本漂了，用户装到的约定与本体不是一代。"""
    assert manifest["version"] == magplot.__version__


def test_declared_asset_paths_exist(manifest):
    for key in ("composerIcon", "logo"):
        rel = manifest["interface"][key]
        assert (PLUGIN / rel).is_file(), f"{key} 指向不存在的文件: {rel}"


def test_marketplace_points_at_the_plugin():
    """仓库即市场根：`codex plugin marketplace add erwanjun/magplot` 靠它。"""
    data = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    entry = data["plugins"][0]
    assert entry["name"] == "magplot"
    assert entry["source"] == {"source": "local", "path": "./codex-plugin"}
    assert (ROOT / entry["source"]["path"]).is_dir()


def test_marketplace_policy_uses_values_codex_accepts():
    """policy 是**枚举**，不是自由文本。

    实测：`authentication: "NONE"`（本插件确实不需要认证，写着最自然）会让
    `codex plugin marketplace add` 当场拒绝整个市场文件——
    `unknown variant NONE, expected ON_INSTALL or ON_USE`。整个市场都装不上，
    错误只在那一条命令里出现一次，之后就是「插件列表里没有它」。
    """
    entry = json.loads(MARKETPLACE.read_text(encoding="utf-8"))["plugins"][0]
    assert entry["policy"]["installation"] in {"AVAILABLE", "REQUIRED", "BLOCKED"}
    assert entry["policy"]["authentication"] in {"ON_INSTALL", "ON_USE"}


def test_skill_frontmatter_is_wellformed():
    """name/description 是 Codex 做隐式匹配的全部依据，缺了技能等于不存在。"""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md 缺少 frontmatter"
    front = m.group(1)
    assert re.search(r"^name: magplot-figure$", front, re.M)
    desc = re.search(r"^description: (.+)$", front, re.M)
    assert desc and len(desc.group(1)) > 40, "description 太短，隐式触发会命中不到"


def test_skill_states_the_script_must_sit_next_to_the_figure():
    """整条链路的地基：没有同目录的脚本，图在 Magplot 里就是一张死图。"""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "脚本与产物同目录" in text
    assert "python -c" in text          # 明确禁掉临时出图的写法


#: 技能自带脚本允许 import 的标准库。加新名字前先想清楚：这些脚本跑在**用户
#: 机器上**、跑在 Codex 的沙盒里，第三方依赖装不上就是整个技能不可用。
_ALLOWED_STDLIB = {
    "argparse", "json", "os", "shutil", "subprocess", "sys", "time",
    "urllib", "winreg", "__future__",
}


def _imports_of(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_skill_scripts_are_stdlib_only_and_parse():
    """技能自带脚本跑在用户机器上：不许有第三方依赖，也不许有语法错。

    互相 import 是允许的（handoff.py ↔ update_check.py 是同一个技能的两半），
    别的一概不行。
    """
    scripts = sorted((SKILL_DIR / "scripts").glob("*.py"))
    assert scripts, "技能里一个脚本都没有？"
    siblings = {p.stem for p in scripts}
    for path in scripts:
        extra = _imports_of(path) - _ALLOWED_STDLIB - siblings
        assert not extra, f"{path.name} 引入了非标准库: {sorted(extra)}"


def test_handoff_script_reads_the_parameterizable_verdict():
    """自检判据必须真的在脚本里，不能只写在 SKILL.md 的说明里。"""
    src = (SKILL_DIR / "scripts" / "handoff.py").read_text(encoding="utf-8")
    assert "parameterizable" in src
    assert "magplot_missing" in src


# ---------------------- handoff.py 自己的行为契约 -------------------------
# 它跑在**用户机器上**、跑在 Codex 的沙盒里，出了错没人看得见 traceback。
# 这几条用假的 magplot CLI 把它的判据钉住，不需要真装 Magplot 或 matplotlib。
FAKE_CLI = '''#!PYTHON
import json, os, sys
resp = json.load(open(os.environ["FAKE_RESPONSE"], encoding="utf-8"))
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
if "--no-launch" not in sys.argv:
    resp["launch"] = {"mode": "desktop"}
print(json.dumps(resp, ensure_ascii=False))
'''

HANDOFF = SKILL_DIR / "scripts" / "handoff.py"

#: 假 CLI 是个带 shebang 的脚本文件。Windows 上 `shutil.which` 只认 PATHEXT 里的
#: 后缀，而 .bat/.cmd 又不能被 CreateProcess 直接拉起（subprocess 不走 shell）。
#: 这三条验的是与平台无关的判据（退出码、调用次序），Windows 那侧真正的风险是
#: 编码，由 tests/test_windows_regressions.py 的两条专门看着。
posix_shim_only = pytest.mark.skipif(
    os.name == "nt", reason="假 CLI 用 shebang 脚本，Windows 上起不来")


def _run_handoff(tmp_path, response: dict, *args):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "magplot"
    # PATH 只留假 CLI 这一个目录（真 magplot 绝不能抢答），所以 shebang 必须是
    # 绝对路径的解释器——`/usr/bin/env python3` 在这种 PATH 下解析不出来。
    fake.write_text(FAKE_CLI.replace("#!PYTHON", "#!" + sys.executable), encoding="utf-8")
    fake.chmod(0o755)
    resp_file = tmp_path / "response.json"
    resp_file.write_text(json.dumps(response), encoding="utf-8")
    log = tmp_path / "calls.log"

    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")

    env = {**os.environ, "PATH": str(bin_dir), "FAKE_RESPONSE": str(resp_file),
           "FAKE_LOG": str(log)}
    env.pop("MAGPLOT_CLI", None)
    # 子进程按 UTF-8 写（它自己 reconfigure 过），这边解码也得钉死——
    # 不钉就跟随系统区域编码，Windows 上读中文 JSON 当场变乱码
    proc = subprocess.run([sys.executable, str(HANDOFF), str(target), *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc, calls


@posix_shim_only
def test_handoff_succeeds_when_the_figure_is_parameterizable(tmp_path):
    proc, calls = _run_handoff(tmp_path, {
        "ok": True, "project": "/p", "stem": "Fig1",
        "registry": {"parameterizable": True, "conflicts": [], "dynamic_names": []}})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["parameterizable"] is True and out["launch"] == "desktop"
    # 先探测（--no-launch）再交接：跑完脚本可能多出新 stem，必须重新解析
    assert len(calls) == 2 and "--no-launch" in calls[0] and "--no-launch" not in calls[1]


@posix_shim_only
def test_handoff_fails_loudly_when_the_figure_has_no_script(tmp_path):
    """用户强调的那条硬约定：脚本没跟图放在一起 = 没做完，退出码必须非零。"""
    proc, _ = _run_handoff(tmp_path, {
        "ok": True, "project": "/p", "stem": "Fig1",
        "registry": {"parameterizable": False, "conflicts": [], "dynamic_names": []}})
    assert proc.returncode == 4
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "同一个目录" in out["hint"]


@posix_shim_only
def test_handoff_reports_magplot_open_failure(tmp_path):
    proc, _ = _run_handoff(tmp_path, {"ok": False, "error": "注册表不是合法 JSON"})
    assert proc.returncode == 2
    assert "注册表不是合法 JSON" in proc.stdout


def test_handoff_rejects_missing_path(tmp_path):
    proc = subprocess.run([sys.executable, str(HANDOFF), str(tmp_path / "nope.pdf")],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env={**os.environ})
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["ok"] is False


@pytest.mark.skipif(tomllib is None, reason="需要 tomllib（Python ≥ 3.11）")
def test_plugin_is_excluded_from_the_python_package():
    """pip 用户拿到的是 Magplot，不该夹带一份 Codex 插件。"""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    exclude = cfg["tool"]["hatch"]["build"]["exclude"]
    assert "codex-plugin" in exclude and "codex-plugin/**" in exclude
    assert ".agents" in exclude and ".agents/**" in exclude


# ==================== 只装了桌面版时的发现链（回归） =====================
# 起因：Windows 用户只装了 Magplot 桌面程序，插件一直报 magplot_missing。
# 桌面版的 Magplot.exe 是 GUI 子系统的可执行文件，当命令行调它拿不到 stdout；
# 插件当时只会查 MAGPLOT_CLI / PATH / 当前解释器，三条全落空。
#
# 这几条端到端跑真进程：真 argv、真 JSON、真的从磁盘上找 CLI。路径规则本身
# 的跨平台矩阵在 tests/test_install_locate.py（那边用注入的假文件系统，
# 每个平台都测得了）。

FAKE_BRIDGE = '''#!PYTHON
import json, os, sys
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\\n")
resp = json.load(open(os.environ["FAKE_RESPONSE"], encoding="utf-8"))
if "--no-launch" not in sys.argv:
    resp["launch"] = {"mode": "desktop"}
print(json.dumps(resp, ensure_ascii=False))
'''

OK_RESPONSE = {"ok": True, "protocol": 1, "project": "/p", "stem": "Fig1",
               "registry": {"parameterizable": True, "status": "created",
                            "conflicts": [], "dynamic_names": []}}

desktop_discovery_only = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="假 bridge 要 shebang 脚本（Windows 起不来），"
           "Linux 没有桌面发行形态（install_roots 本来就是空的）")

#: macOS 的 `/Applications` 是绝对路径，env 隔离不掉它——**开发机上真装着的
#: 那份 Magplot 会真的被发现**（这本身正是发现链在干活）。所以「机器上没有
#: 桌面版」这类模拟只在真没装时才成立；同一判据的注入版（假文件系统，与机器
#: 无关）在 tests/test_install_locate.py，那边任何机器上都跑。
REAL_APP = "/Applications/Magplot.app/Contents/MacOS/Magplot"
REAL_APP_CLI = ("/Applications/Magplot.app/Contents/Resources/"
                "sidecar/Magplot/magplot-cli")
needs_no_real_desktop = pytest.mark.skipif(
    os.path.isfile(REAL_APP),
    reason="这台机器上真装着 Magplot 桌面版，「什么都没装」模拟不出来")
needs_no_real_cli = pytest.mark.skipif(
    os.path.isfile(REAL_APP_CLI),
    reason="这台机器上真装着带 CLI 的 Magplot 桌面版，「装了但没 CLI」模拟不出来")

#: 假 bridge 是带 shebang 的脚本。Windows 的 CreateProcess 起不了它（也起不了
#: .bat/.cmd，subprocess 不走 shell），所以这一类只在 POSIX 上跑——与文件上半部
#: 分那个 posix_shim_only 同一个理由。**Windows 上的等价覆盖有两条**：
#: tests/test_install_locate.py 用注入的假文件系统测同一套判据（平台无关），
#: 下面 test_real_cli_handoff_end_to_end 用 pip 装出来的真 magplot 走完整链路。
posix_bridge_only = pytest.mark.skipif(
    os.name == "nt", reason="假 bridge 用 shebang 脚本，Windows 上起不来")


@pytest.fixture(scope="module")
def clean_python(tmp_path_factory):
    """一个 import 不到 magplot 的解释器。

    插件最后一条兜底是「当前解释器里有 magplot 模块」。测试要是用仓库的
    .venv 跑它，那条兜底永远成立——「没装 Magplot」这一类用例会被它悄悄
    救活，而它们恰恰是这次要修的东西。
    """
    venv = tmp_path_factory.mktemp("clean-venv") / "v"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                   check=True, capture_output=True)
    exe = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not exe.is_file():
        pytest.skip("建不出干净的解释器")
    probe = subprocess.run([str(exe), "-c", "import magplot"], capture_output=True)
    if probe.returncode == 0:
        pytest.skip("干净解释器里居然有 magplot")
    return str(exe)


def _write_bridge(path: Path) -> Path:
    """把假 bridge 写到 path（当成装好的 magplot-cli）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FAKE_BRIDGE.replace("#!PYTHON", "#!" + sys.executable),
                    encoding="utf-8")
    path.chmod(0o755)
    return path


def _plugin_env(tmp_path, **extra):
    """一个干净到底的环境：PATH 里没有 magplot，也没有 MAGPLOT_CLI。

    **已知安装位置也要一起指到临时目录**：Windows 的 `install_roots()` 读的是
    `%LOCALAPPDATA%` / `%PROGRAMFILES%`，不改它们的话 runner（或开发机）上真装
    着的 Magplot 会被发现——「什么都没装」就模拟不出来了。macOS 的
    `/Applications` 是绝对路径改不掉，那条由 needs_no_real_desktop 兜。
    """
    empty = tmp_path / "empty-bin"
    empty.mkdir(exist_ok=True)
    roots = tmp_path / "roots"
    (roots / "local").mkdir(parents=True, exist_ok=True)
    (roots / "pf").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PATH": str(empty), "HOME": str(tmp_path),
           "LOCALAPPDATA": str(roots / "local"),
           "PROGRAMFILES": str(roots / "pf"),
           "MAGPLOT_CONFIG_DIR": str(tmp_path / "config")}
    env.pop("PROGRAMFILES(X86)", None)
    env.pop("MAGPLOT_CLI", None)
    env.update(extra)
    return env


def _run_plugin(python, tmp_path, env, *args, response=None):
    resp_file = tmp_path / "response.json"
    resp_file.write_text(json.dumps(response or OK_RESPONSE, ensure_ascii=False),
                         encoding="utf-8")
    log = tmp_path / "calls.log"
    env = {**env, "FAKE_RESPONSE": str(resp_file), "FAKE_LOG": str(log)}
    proc = subprocess.run([python, str(HANDOFF), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env)
    calls = [json.loads(line) for line in
             log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    out = None
    if proc.stdout.strip():
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    return proc, out, calls


@desktop_discovery_only
@needs_no_real_cli
def test_desktop_only_install_is_discovered(clean_python, tmp_path):
    """**这条就是那个 bug 的正面回归。**

    只装了桌面版：PATH 里没有 magplot，没设 MAGPLOT_CLI，当前解释器也 import
    不到它。插件必须靠安装位置里的 magplot-cli 把交接做完。
    """
    app = tmp_path / "Applications" / "Magplot.app"
    _write_bridge(app / "Contents" / "Resources" / "sidecar" / "Magplot" / "magplot-cli")
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "Magplot").write_text("gui", encoding="utf-8")
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")

    proc, out, calls = _run_plugin(clean_python, tmp_path,
                                   _plugin_env(tmp_path), str(target))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["ok"] is True and out["parameterizable"] is True
    assert out["magplot"]["source"] == "install"
    assert out["launch"] == "desktop"          # 原生窗口，不是浏览器
    # 先探测（--no-launch）再交接
    assert len(calls) == 2
    assert "--no-launch" in calls[0] and "--no-launch" not in calls[1]
    assert calls[0][:3] == ["open", str(target), "--json"]


@desktop_discovery_only
@needs_no_real_cli
def test_desktop_installed_but_without_cli_is_its_own_error(clean_python, tmp_path):
    """装了桌面版、那一版没带 CLI——**不能报「没装 Magplot」**。

    用户明明装了。报 magplot_missing 会让他再去装一遍已经装着的东西，
    然后发现还是不行。该说的是「升级」。
    """
    app = tmp_path / "Applications" / "Magplot.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    (app / "Magplot").write_text("gui", encoding="utf-8")
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")

    proc, out, _ = _run_plugin(clean_python, tmp_path,
                               _plugin_env(tmp_path), str(target))
    assert proc.returncode == 3
    assert out["error_code"] == "desktop_found_cli_missing"
    assert out["desktop"].endswith("Magplot.app/Contents/MacOS/Magplot")
    assert "最新版" in out["hint"]


@posix_bridge_only
def test_manifest_discovery_survives_spaces_and_chinese(clean_python, tmp_path):
    """安装清单指到带空格和中文的路径：一路到 bridge 都不许被拆开。

    这条平台无关（清单是绝对路径，不依赖平台惯例位置），所以三个平台都跑。
    """
    bridge = _write_bridge(tmp_path / "我的 程序" / "Magplot" / "magplot-cli")
    config = tmp_path / "config"
    config.mkdir()
    (config / "install.json").write_text(json.dumps(
        {"protocol": 1, "product": "Magplot", "version": "9.9.9",
         "cli": str(bridge), "desktop": None, "install_dir": None,
         "source": "installer"}), encoding="utf-8")
    project = tmp_path / "我的 图库"
    project.mkdir()
    target = project / "图 1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")

    proc, out, calls = _run_plugin(clean_python, tmp_path,
                                   _plugin_env(tmp_path), str(target))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["magplot"]["source"] == "manifest"
    assert out["magplot"]["cmd"] == str(bridge)
    # bridge 收到的是**一个**参数，不是被空格切成两半的两个
    assert calls[0][1] == str(target)


@posix_bridge_only
def test_explicit_env_override_still_wins(clean_python, tmp_path):
    """既有行为不许被新链路顶掉：MAGPLOT_CLI 指到哪儿就用哪儿。"""
    chosen = _write_bridge(tmp_path / "chosen" / "magplot")
    ignored = _write_bridge(tmp_path / "ignored" / "magplot-cli")
    config = tmp_path / "config"
    config.mkdir()
    (config / "install.json").write_text(json.dumps(
        {"protocol": 1, "cli": str(ignored)}), encoding="utf-8")
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")

    env = _plugin_env(tmp_path, MAGPLOT_CLI=str(chosen))
    proc, out, _ = _run_plugin(clean_python, tmp_path, env, str(target))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["magplot"] == {"source": "env", "cmd": str(chosen)}


@posix_bridge_only
def test_path_cli_still_wins_over_the_install(clean_python, tmp_path):
    """PATH 里的 magplot（pip/pipx 装的）优先级仍在 CLI shim 之前。"""
    bin_dir = tmp_path / "bin"
    _write_bridge(bin_dir / "magplot")
    _write_bridge(tmp_path / "config-cli" / "magplot-cli")
    config = tmp_path / "config"
    config.mkdir()
    (config / "install.json").write_text(json.dumps(
        {"protocol": 1, "cli": str(tmp_path / "config-cli" / "magplot-cli")}),
        encoding="utf-8")
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")

    env = _plugin_env(tmp_path, PATH=str(bin_dir))
    proc, out, _ = _run_plugin(clean_python, tmp_path, env, str(target))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["magplot"]["source"] == "path"


@needs_no_real_desktop
def test_nothing_installed_reports_magplot_missing(clean_python, tmp_path):
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    proc, out, _ = _run_plugin(clean_python, tmp_path,
                               _plugin_env(tmp_path), str(target))
    assert proc.returncode == 3
    assert out["error_code"] == "magplot_missing"
    assert out["magplot_missing"] is True        # 旧字段保留（SKILL.md 认它）
    assert "releases" in out["hint"]


@posix_bridge_only
def test_no_launch_reaches_the_bridge(clean_python, tmp_path):
    """`--no-launch` 一路传到 CLI，且第二次调用不再发生。"""
    bridge = _write_bridge(tmp_path / "cli" / "magplot-cli")
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    env = _plugin_env(tmp_path, MAGPLOT_CLI=str(bridge))
    proc, out, calls = _run_plugin(clean_python, tmp_path, env,
                                   str(target), "--no-launch")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert all("--no-launch" in call for call in calls)
    assert out["launch"] is None                 # 一个界面都没起


@posix_bridge_only
def test_open_error_code_is_passed_through(clean_python, tmp_path):
    """`magplot open` 自己的 code（比如注册表写不进去）要原样带出来。

    统一压成一句「交接失败」，用户就不知道该去改目录权限还是去装东西。
    """
    bridge = _write_bridge(tmp_path / "cli" / "magplot-cli")
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    env = _plugin_env(tmp_path, MAGPLOT_CLI=str(bridge))
    proc, out, _ = _run_plugin(
        clean_python, tmp_path, env, str(target),
        response={"ok": False, "code": "registry_write_failed",
                  "error": "注册表写不进去 /p/mm_registry.json"})
    assert proc.returncode == 2
    # **原样带出来**，不是压成 open_failed：SKILL.md 教 Codex 的就是按
    # error_code 分支（registry_write_failed → 换个可写目录）。藏进第二层
    # 等于那条指引永远走不到。
    assert out["error_code"] == "registry_write_failed"
    assert out["code"] == "registry_write_failed"


def test_unrunnable_cli_is_not_reported_as_missing(clean_python, tmp_path):
    """MAGPLOT_CLI 指到了不存在的东西：说「执行不了」，不说「没装」。"""
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    env = _plugin_env(tmp_path, MAGPLOT_CLI=str(tmp_path / "没有这个文件"))
    proc, out, _ = _run_plugin(clean_python, tmp_path, env, str(target))
    assert proc.returncode == 2
    assert out["error_code"] == "cli_exec_failed"


@posix_bridge_only
def test_open_failure_without_a_code_still_has_one(clean_python, tmp_path):
    """老版本 magplot 不带 code：那时才回落到 open_failed。"""
    bridge = _write_bridge(tmp_path / "cli" / "magplot-cli")
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    env = _plugin_env(tmp_path, MAGPLOT_CLI=str(bridge))
    proc, out, _ = _run_plugin(clean_python, tmp_path, env, str(target),
                               response={"ok": False, "error": "说不清哪儿错了"})
    assert proc.returncode == 2
    assert out["error_code"] == "open_failed"


def test_skill_documents_every_error_code_it_can_emit():
    """SKILL.md 里教 Codex 分支的那几个 code，插件真的发得出来。

    这条挡的正是 Codex review 抓到的那种错位：文档说「看到
    registry_write_failed 就换个可写目录」，而实现把它压成了 open_failed，
    于是那段指引永远走不到，两边各看各的都很合理。
    """
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    documented = set(re.findall(r'"error_code": "(\w+)"', skill))
    assert documented, "SKILL.md 里一个 error_code 都没写"
    src = HANDOFF.read_text(encoding="utf-8")
    for code in documented:
        if code in {"magplot_missing", "desktop_found_cli_missing"}:
            assert code in src                       # 插件自己的 code
        else:
            # 来自 magplot open 的 code：靠 _open_failure 原样透传
            assert "code or \"open_failed\"" in src, \
                f"SKILL.md 承诺了 {code}，但插件没有透传 CLI 的 code"


def test_plugin_consults_the_registry_like_the_engine_locator():
    """HKCU 那条腿两侧都要有。

    engine.locate.find_cli 有、插件没有的话，「装在非默认目录 + 清单又没写成」
    的 Windows 机器上，插件会报 magplot_missing 而 Magplot 自己找得到——
    同一台机器两个答案。
    """
    src = HANDOFF.read_text(encoding="utf-8")
    assert "hkcu_install_dirs" in src
    assert "winreg" in src
    from magplot.engine import locate
    assert locate.UNINSTALL_KEY.replace("\\", "\\\\") in src or \
        locate.UNINSTALL_KEY in src.replace("\\\\", "\\")


def test_every_failure_payload_carries_an_error_code():
    """插件的每一条失败出口都要带 error_code——调用方按它分诊。"""
    src = HANDOFF.read_text(encoding="utf-8")
    tree = ast.parse(src)
    emits = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "emit"]
    assert emits, "找不到 emit 调用"
    for node in emits:
        code = node.args[1]
        if isinstance(code, ast.Constant) and code.value == 0:
            continue                              # 成功那条不需要
        payload = node.args[0]
        if isinstance(payload, ast.Dict):
            keys = {k.value for k in payload.keys if isinstance(k, ast.Constant)}
            assert "error_code" in keys, f"第 {node.lineno} 行的失败出口没有 error_code"


@pytest.mark.skipif(shutil.which("magplot") is None,
                    reason="PATH 里没有 pip 装出来的 magplot")
def test_real_cli_handoff_end_to_end(tmp_path):
    """拿**真的** magplot CLI 走完整条链路——Windows 上也跑。

    上面那批用假 bridge 的用例在 Windows 上起不来（shebang 脚本），可
    「路径带空格和中文时会不会被拆开」恰恰是 Windows 最容易出事的地方。
    这一条用 `pip install -e .` 装出来的 `magplot`（Windows 上是真的 .exe）
    补上那段覆盖：真 argv、真注册表、真 JSON，只是不唤起界面。
    """
    project = tmp_path / "我的 图库"
    project.mkdir()
    (project / "fig_demo.py").write_text(
        "from pathlib import Path\n\n"
        "import matplotlib.pyplot as plt\n\n"
        "OUT = Path(__file__).resolve().parent\n\n\n"
        "def main():\n"
        "    fig, ax = plt.subplots()\n"
        '    fig.savefig(OUT / "Fig1_演示.pdf")\n',
        encoding="utf-8")
    (project / "Fig1_演示.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")

    env = {**os.environ,
           "MAGPLOT_CLI": shutil.which("magplot"),
           "MAGPLOT_CONFIG_DIR": str(tmp_path / "cfg"),
           "MAGPLOT_DATA_DIR": str(tmp_path / "data")}
    proc = subprocess.run(
        [sys.executable, str(HANDOFF), str(project / "fig_demo.py"),
         "--run", "never", "--no-launch"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is True
    assert out["parameterizable"] is True
    assert out["project"] == str(project)          # 空格与中文原样，没被拆开
    assert out["stem"] == "Fig1_演示"
    assert out["launch"] is None                   # --no-launch：一个界面都没起
    assert out["magplot"]["source"] == "env"
    registry = json.loads((project / "mm_registry.json").read_text(encoding="utf-8"))
    assert "fig_demo.py" in registry["scripts"]


# ===================== 插件的更新通道（发布侧） ==========================
# 用户装了插件之后不会自动收到更新——Codex 不管这件事。所以插件自己查一份
# 清单，而那份清单是发版时生成的。这几条盯的是「发版时它真的被生成、内容对」。

def _manifest_module():
    spec = importlib.util.spec_from_file_location(
        "make_plugin_manifest", ROOT / "scripts" / "make_plugin_manifest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_manifest(tmp_path, tag):
    mod = _manifest_module()
    out = tmp_path / "codex-plugin.json"
    mod.main(["--tag", tag, "--out", str(out)])
    return mod, json.loads(out.read_text(encoding="utf-8"))


def test_plugin_manifest_matches_what_the_plugin_reads(tmp_path):
    """生成的清单，插件那侧要认得出来（schema 与字段名同源）。"""
    mod, data = _make_manifest(tmp_path, "v" + magplot.__version__)
    spec = importlib.util.spec_from_file_location(
        "_uc", SKILL_DIR / "scripts" / "update_check.py")
    uc = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    try:
        spec.loader.exec_module(uc)
    finally:
        sys.path.remove(str(SKILL_DIR / "scripts"))
    assert data["schema"] == uc.SCHEMA
    assert data["latest_version"] == uc.current_version()
    # 清单地址是发布资产，文件名不能漂——插件拉的就是这个名字
    assert uc.DEFAULT_URL.endswith("/codex-plugin.json")


def test_plugin_manifest_refuses_a_tag_that_disagrees(tmp_path):
    """tag 与 plugin.json 对不上就失败。

    发一份说自己是 0.7.1、里面装着 0.7.0 的清单，用户会永远看到「有新版本」，
    更新完还是看到——而且没有任何报错。
    """
    with pytest.raises(SystemExit) as err:
        _make_manifest(tmp_path, "v99.0.0")
    assert "对不上" in str(err.value)


def test_plugin_manifest_min_magplot_version_is_real(tmp_path):
    """`min_magplot_version` 必须是真发过的版本，且不高于当前版本。

    随手往上调会让一批老用户看到「去升级 Magplot」，而他们的 Magplot 可能
    完全够用。当前值是第一个带 `magplot open` 的版本。
    """
    mod, data = _make_manifest(tmp_path, "v" + magplot.__version__)
    required = data["min_magplot_version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", required), required
    assert tuple(map(int, required.split("."))) <= \
        tuple(map(int, magplot.__version__.split(".")))
    assert mod.MIN_MAGPLOT_VERSION == required


def test_plugin_zip_contains_the_skill(tmp_path):
    """安装包里要有技能本体，不能只有清单。"""
    import zipfile
    target = _manifest_module().build_zip(tmp_path / "p.zip")
    names = zipfile.ZipFile(target).namelist()
    for needed in ("codex-plugin/.codex-plugin/plugin.json",
                   "codex-plugin/skills/magplot-figure/SKILL.md",
                   "codex-plugin/skills/magplot-figure/scripts/handoff.py",
                   "codex-plugin/skills/magplot-figure/scripts/update_check.py"):
        assert needed in names, f"插件包里缺 {needed}"
    assert not [n for n in names if "__pycache__" in n]


def test_release_workflow_publishes_the_plugin_channel():
    """发版流水线真的会生成并挂上去。

    **刻意不在 desktop-tauri.yml 的 updater-manifest 里**：那个 job 依赖桌面
    产物与 minisign 私钥，没配私钥时整个跳过——插件的更新通道会跟着悄悄停，
    而且全绿。
    """
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "make_plugin_manifest.py" in release
    assert "out/codex-plugin.json" in release
    desktop = (ROOT / ".github" / "workflows" /
               "desktop-tauri.yml").read_text(encoding="utf-8")
    assert "make_plugin_manifest" not in desktop


# --------------------------- MCP server 的清单接线 ---------------------------
# 这几条盯的是「Codex 装上了、但一个工具都看不见」——清单字段错一个字，
# 症状就是插件安安静静地只剩技能。字段形状取自官方插件（`codex plugin` 装出来的
# `~/.codex/plugins/cache/**/.codex-plugin/plugin.json` 与它们的 `.mcp.json`）。
MCP_JSON = PLUGIN / ".mcp.json"


def test_manifest_declares_the_mcp_server(manifest):
    """`mcpServers` 指向一个**存在的** .mcp.json，且技能仍在。"""
    assert manifest["mcpServers"] == "./.mcp.json"
    assert MCP_JSON.is_file()
    assert manifest["skills"] == "./skills/", "加 MCP 不能把技能挤掉"


def test_mcp_json_shape_matches_what_codex_reads():
    data = json.loads(MCP_JSON.read_text(encoding="utf-8"))
    servers = data["mcpServers"]
    assert list(servers) == ["magplot"]
    entry = servers["magplot"]
    # 本地 stdio：command + args + cwd。远程 HTTP 那套字段这里一个都不该有
    assert entry["command"] == "python3"
    assert entry["args"] == ["./mcp/server.py"]
    assert entry["cwd"] == "."
    assert "url" not in entry
    # 起 worker 要跑用户的脚本，heavy 的图是分钟级——超时不能用默认的那点
    assert entry["tool_timeout_sec"] >= 600
    for name in ("MAGPLOT_CLI", "MAGPLOT_MCP_ROOTS", "PATH"):
        assert name in entry["env_vars"], f"{name} 没进 env_vars，server 那边读不到"
    assert (PLUGIN / "mcp" / "server.py").is_file()


def test_launcher_is_stdlib_only_and_parses():
    """启动器跑在**用户机器上的任意 python3**（可能没装 magplot）。

    `handoff` 是插件自带的那份定位器（同一个包里，按相对路径 import），
    不是第三方依赖。
    """
    src = (PLUGIN / "mcp" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    allowed = {"json", "os", "shutil", "subprocess", "sys", "__future__",
               "magplot", "magplot_mcp", "handoff"}
    assert not (imported - allowed), f"启动器引入了非标准库: {sorted(imported - allowed)}"


def test_launcher_degrades_instead_of_dying_without_magplot():
    """跑不起来时**不能静默退出**——那在 Codex 里就是「插件没有工具」。"""
    src = (PLUGIN / "mcp" / "server.py").read_text(encoding="utf-8")
    assert "_degraded_server" in src
    assert "pipx install magplot" in src


def test_launcher_reuses_the_plugin_locator_instead_of_a_third_copy():
    """路径规则已经有两份（`engine/locate.py` + 插件的 handoff），不许有第三份。

    启动器与 handoff.py 同属一个插件包，直接 import 那份就好；自己再抄一遍
    的话，「只装了桌面版」这类格子会在两处各修一次——而 #7 刚为此付过一次账。
    """
    src = (PLUGIN / "mcp" / "server.py").read_text(encoding="utf-8")
    assert "find_magplot" in src, "启动器没有复用插件自带的定位器"
    for owned_by_the_locator in ("LOCALAPPDATA", "install.json", "SIDECAR_REL",
                                 "UNINSTALL_KEY", "/Applications/Magplot.app"):
        assert owned_by_the_locator not in src, (
            f"启动器里出现了 {owned_by_the_locator}——路径规则该由定位器说了算")


def test_launcher_tells_desktop_only_users_the_truth():
    """**只装桌面版**要单独报，不能笼统说「没装 Magplot」——他明明装了。

    交接只要能*执行* `magplot open`，桌面版带的 `magplot-cli` 就够；但 MCP
    server 要 `import magplot` 在进程内驱动引擎，而那个 CLI 是 frozen 的，
    给不出解释器。三态互斥，各有各的下一步动作。
    """
    sys.path.insert(0, str(PLUGIN / "mcp"))
    import importlib
    launcher = importlib.import_module("server")

    code, hint = launcher.diagnose({"cmd": ["/Applications/Magplot.app/…/magplot-cli"],
                                    "desktop": "/Applications/Magplot.app/…/Magplot"})
    assert code == "desktop_only"
    assert "pipx install magplot" in hint
    assert "没装" not in hint, "对着装了桌面版的用户说「没装」"

    code, hint = launcher.diagnose({"cmd": None, "desktop": "/Applications/Magplot.app"})
    assert code == "desktop_found_cli_missing"

    code, hint = launcher.diagnose({"cmd": None, "desktop": None})
    assert code == "magplot_missing"


def test_launcher_only_takes_interpreters_it_can_actually_use():
    """frozen 的 `magplot-cli` 给不出解释器：它没有 shebang，旁边也没有 python。

    反过来，pip / pipx 装的 `magplot` 是带 shebang 的小脚本——这条区分就是
    「桌面版」与「Python 环境」两格的分界线。
    """
    sys.path.insert(0, str(PLUGIN / "mcp"))
    import importlib
    launcher = importlib.import_module("server")

    with tempfile.TemporaryDirectory() as tmp:
        frozen = os.path.join(tmp, "magplot-cli")
        with open(frozen, "wb") as f:                 # ELF/PE 头，不是 shebang
            f.write(b"\x7fELF\x02\x01\x01\x00")
        assert launcher._shebang_interpreter(frozen) is None
        assert launcher._interpreter_beside(frozen) == []

        shim = os.path.join(tmp, "magplot")
        with open(shim, "w", encoding="utf-8") as f:
            f.write(f"#!{sys.executable}\nprint(1)\n")
        assert launcher._shebang_interpreter(shim) == sys.executable


def test_the_plugin_is_not_shipped_in_the_wheel():
    """插件随 Codex 市场分发，不属于 pip 包（pyproject 的 exclude 看着）。"""
    if tomllib is None:
        pytest.skip("需要 tomllib（Python 3.11+）")
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    exclude = cfg["tool"]["hatch"]["build"]["exclude"]
    assert "codex-plugin" in exclude and "codex-plugin/**" in exclude


def test_widget_artifact_is_committed_next_to_the_server():
    """产物不在仓库里 = 用户装完插件只有一个空目录（server 会如实降级）。"""
    canvas = PLUGIN / "mcp" / "widget" / "canvas.html"
    if not canvas.is_file():
        pytest.skip("画布产物未构建（跑一次 scripts/build_mcp_widget.py）")
    text = canvas.read_text(encoding="utf-8")
    assert text.startswith("<!-- magplot-mcp-widget ")
    assert "<div id=\"root\">" in text
