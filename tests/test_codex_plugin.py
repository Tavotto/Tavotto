"""Codex 插件（codex-plugin/）的形状看护。

插件是**跟着 Magplot 一起发的**：市场清单在仓库根的 `.agents/plugins/`，
插件本体在 `codex-plugin/`。这几条断言盯的都是「坏了也不报错，只是悄悄不生效」
的那类问题——清单字段错一个 Codex 就装不上，版本漂了用户装到的是另一代约定。
"""
import ast
import json
import os
import re
import subprocess
import sys
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


def test_handoff_script_is_stdlib_only_and_parses():
    """技能自带脚本跑在用户机器上：不许有第三方依赖，也不许有语法错。"""
    src = (SKILL_DIR / "scripts" / "handoff.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    third_party = imported - {
        "argparse", "json", "os", "shutil", "subprocess", "sys", "__future__"}
    assert not third_party, f"handoff.py 引入了非标准库: {sorted(third_party)}"


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
    """一个干净到底的环境：PATH 里没有 magplot，也没有 MAGPLOT_CLI。"""
    empty = tmp_path / "empty-bin"
    empty.mkdir(exist_ok=True)
    env = {**os.environ, "PATH": str(empty), "HOME": str(tmp_path),
           "MAGPLOT_CONFIG_DIR": str(tmp_path / "config")}
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
    assert out["error_code"] == "open_failed"
    assert out["code"] == "registry_write_failed"


def test_unrunnable_cli_is_not_reported_as_missing(clean_python, tmp_path):
    """MAGPLOT_CLI 指到了不存在的东西：说「执行不了」，不说「没装」。"""
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    env = _plugin_env(tmp_path, MAGPLOT_CLI=str(tmp_path / "没有这个文件"))
    proc, out, _ = _run_plugin(clean_python, tmp_path, env, str(target))
    assert proc.returncode == 2
    assert out["error_code"] == "cli_exec_failed"


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
