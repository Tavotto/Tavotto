"""Codex 插件（codex-plugin/）的形状看护。

插件是**跟着 Tavotto 一起发的**：市场清单在仓库根的 `.agents/plugins/`，
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

import tavotto

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "codex-plugin"
SKILL_DIR = PLUGIN / "skills" / "tavotto-figure"
MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))


def test_manifest_lives_where_codex_looks(manifest):
    """`.codex-plugin/plugin.json` 是 Codex 唯一认的清单位置。"""
    assert manifest["name"] == "tavotto"
    assert manifest["skills"] == "./skills/"


def test_manifest_version_tracks_the_product(manifest):
    """插件随 Tavotto 发版：版本漂了，用户装到的约定与本体不是一代。"""
    assert manifest["version"] == tavotto.__version__


def test_declared_asset_paths_exist(manifest):
    for key in ("composerIcon", "logo"):
        rel = manifest["interface"][key]
        assert (PLUGIN / rel).is_file(), f"{key} 指向不存在的文件: {rel}"


def test_marketplace_points_at_the_plugin():
    """仓库即市场根：`codex plugin marketplace add Tavotto/Tavotto` 靠它。"""
    data = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    entry = data["plugins"][0]
    assert entry["name"] == "tavotto"
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
    assert re.search(r"^name: tavotto-figure$", front, re.M)
    desc = re.search(r"^description: (.+)$", front, re.M)
    assert desc and len(desc.group(1)) > 40, "description 太短，隐式触发会命中不到"


def test_skill_states_the_script_must_sit_next_to_the_figure():
    """整条链路的地基：没有同目录的脚本，图在 Tavotto 里就是一张死图。"""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "脚本与产物同目录" in text
    assert "python -c" in text  # 明确禁掉临时出图的写法


def test_skill_asks_the_three_setup_questions():
    """开工三问是产品行为：宽度两档、字体含两个标准选项、图例加框与否。

    偏好记录过的下次不问（prefs.py），问的时候走宿主的提问工具而不是
    自由文本追问——这两条也是承诺的一部分。
    """
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "8 cm" in text and "15 cm" in text
    assert "Times New Roman" in text and "Arial" in text
    assert "图例加不加框" in text
    assert "prefs.py" in text
    assert "提问工具" in text
    # `width=ask` 是哨兵不是答案（PR #93 P2）：撞见它必须照问，
    # 不许按「记录过的不再问」跳过
    assert "宽度每次都问」，撞见它宽度照问" in text


def test_skill_forbids_uninvited_decoration():
    """克制原则：背景色块 / 箭头 / 说明文字，用户没要就不加；想加先问。

    2026-08-25 起细则拆进 references/publication-style.md（SKILL.md 声明
    画图前必读它），约定本身一条没少。
    """
    text = (SKILL_DIR / "references" / "publication-style.md").read_text(encoding="utf-8")
    assert "一个都不擅自加" in text
    for banned in ("背景色块", "箭头指向", "说明性文字"):
        assert banned in text, f"publication-style.md 没把「{banned}」列进禁加清单"


def test_skill_keeps_multi_panel_inside_matplotlib():
    """组图约定：多子图在一个 Figure 里拼成 150mm 主图，绝不用别的软件拼。

    组图版式的默认值也在这条约定里：每个子图的 x/y 轴各自标全（轴标题 +
    刻度，不共享、不许只给最左那个留）、不用 sharex/sharey、子图标题
    矩阵式各归各位（不挤左上角）、轴标题默认加粗。
    """
    text = (SKILL_DIR / "references" / "publication-style.md").read_text(encoding="utf-8")
    template = (SKILL_DIR / "references" / "figure-contract.md").read_text(encoding="utf-8")
    assert "GridSpec" in text
    assert "150 mm" in text
    assert "绝不用别的软件拼" in text
    assert "每个子图的 x 轴与 y 轴都各自标全" in text
    assert "不共享坐标轴" in text
    assert "矩阵式各归各位" in text
    # 轴标题默认加粗写进了默认值与模板两处
    assert "axes.labelweight" in text and "axes.labelweight" in template


# ---------------------- 会话入口：先检查，不安装 ---------------------------
# 2026-08-25 反转：旧契约「每个会话第一次触发时 marketplace add + plugin add」
# 已删除——同一会话里工具不重载，健康会话里那条命令只有网络与解析成本。
# 新契约是状态机：健康 = 零安装；缺什么修什么；工具缺失 = 新会话 + 停止。


def _skill_text() -> str:
    return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def _all_skill_docs() -> str:
    parts = [_skill_text()]
    for ref in sorted((SKILL_DIR / "references").glob("*.md")):
        parts.append(ref.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_skill_no_longer_reinstalls_the_plugin_every_session():
    """`marketplace add && plugin add` 的会话内自动同步契约必须消失。

    分开写的两条安装命令仍然在（恢复路径要用），但「每个会话第一次触发时
    跑一遍」的行为一个字都不许留。
    """
    everything = _all_skill_docs()
    assert (
        "codex plugin marketplace add Tavotto/Tavotto && codex plugin add tavotto@tavotto"
    ) not in everything
    assert "每个会话只跑一次" not in everything
    assert "先同步插件" not in everything


def test_skill_entry_checks_health_first_and_never_installs_when_healthy():
    text = _skill_text()
    assert "会话入口：先检查，不安装" in text
    assert "优先调用 `tavotto_health`，只调用一次" in text
    # ok: true = 本会话零安装
    assert "`ok: true`" in text
    assert "绝不执行插件安装、升级、pip/pipx" in text
    assert "provision" in text
    # 缺什么修什么：缺引擎只修引擎
    assert "缺引擎只修引擎" in text
    assert "绝不顺手重装插件" in text


def test_skill_entry_requires_a_new_session_when_tools_are_missing():
    """工具缺失 = 插件没在本会话加载：给安装命令、要求新会话、然后停止。"""
    text = _skill_text()
    assert "没有 `tavotto_health` 这个工具" in text
    assert "新开会话" in text
    assert "停止" in text
    assert "不要在旧会话里继续假装工具可用" in text
    # 两条安装命令分开写、sparse 双路径，在恢复 reference 里
    recovery = (SKILL_DIR / "references" / "first-run-and-recovery.md").read_text(encoding="utf-8")
    assert (
        "codex plugin marketplace add Tavotto/Tavotto "
        "--sparse .agents/plugins --sparse codex-plugin"
    ) in recovery
    assert "codex plugin add tavotto@tavotto" in recovery
    assert "&&" not in recovery.split("```sh")[1].split("```")[0], "安装命令要分开跑，不用 && 串联"


def test_skill_entry_desktop_only_is_not_described_as_missing():
    """desktop_only ≠ 没装 Tavotto——用户明明装了桌面版。"""
    text = _skill_text()
    assert "`desktop_only`" in text
    assert "不要说「没有安装 Tavotto」" in text
    recovery = (SKILL_DIR / "references" / "first-run-and-recovery.md").read_text(encoding="utf-8")
    assert "desktop_only" in recovery
    assert "桌面交接" in recovery
    assert 'pipx install "tavotto[worker]"' in recovery


def test_skill_entry_update_reminder_never_blocks_the_task():
    text = _skill_text()
    assert "当前任务照常完成" in text
    assert "收尾提醒一次" in text
    assert "codex plugin marketplace upgrade tavotto" in text
    assert "不自动升级、不反复提醒" in text


def test_skill_entry_failures_never_retry_or_fall_back_to_source():
    text = _skill_text()
    assert "不循环重试" in text
    assert "不退回源码构建" in text
    recovery = (SKILL_DIR / "references" / "first-run-and-recovery.md").read_text(encoding="utf-8")
    assert "不循环重试" in recovery
    assert "clone" in recovery  # 明确写出「不退回 clone 源码」


def test_skill_routes_each_reference_explicitly():
    """SKILL.md 必须写清什么情况读哪份 reference，且每份都真实存在。

    普通画图不许把故障文档全载入——路由表就是这条纪律的落点。
    """
    text = _skill_text()
    assert "什么情况下读哪份 reference" in text
    for ref in (
        "first-run-and-recovery.md",
        "figure-contract.md",
        "publication-style.md",
        "desktop-handoff.md",
        "issue-reporting.md",
        "compatibility.md",
    ):
        assert ref in text, f"SKILL.md 没写什么时候读 {ref}"
        assert (SKILL_DIR / "references" / ref).is_file(), f"references/{ref} 不存在"
    assert "用到才读" in text


def test_skill_files_issues_only_with_consent():
    """撞上 Tavotto 的缺陷时写复现 issue，但外发必须经用户明确允许。

    细则在 references/issue-reporting.md；SKILL.md 的完成判据一节指向它。
    """
    text = (SKILL_DIR / "references" / "issue-reporting.md").read_text(encoding="utf-8")
    assert "github.com/Tavotto/Tavotto/issues" in text
    assert "用户明确允许" in text
    assert "复现步骤" in text
    assert "脱敏" in text
    assert "issue-reporting.md" in _skill_text()


#: 技能自带脚本允许 import 的标准库。加新名字前先想清楚：这些脚本跑在**用户
#: 机器上**、跑在 Codex 的沙盒里，第三方依赖装不上就是整个技能不可用。
_ALLOWED_STDLIB = {
    "argparse",
    "json",
    "os",
    "shutil",
    "subprocess",
    "sys",
    "time",
    "urllib",
    "winreg",
    "__future__",
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
    assert "tavotto_missing" in src


# ---------------------- handoff.py 自己的行为契约 -------------------------
# 它跑在**用户机器上**、跑在 Codex 的沙盒里，出了错没人看得见 traceback。
# 这几条用假的 tavotto CLI 把它的判据钉住，不需要真装 Tavotto 或 matplotlib。
FAKE_CLI = """#!PYTHON
import json, os, sys
resp = json.load(open(os.environ["FAKE_RESPONSE"], encoding="utf-8"))
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
if "--no-launch" not in sys.argv:
    resp["launch"] = {"mode": "desktop"}
print(json.dumps(resp, ensure_ascii=False))
"""

HANDOFF = SKILL_DIR / "scripts" / "handoff.py"

#: 假 CLI 是个带 shebang 的脚本文件。Windows 上 `shutil.which` 只认 PATHEXT 里的
#: 后缀，而 .bat/.cmd 又不能被 CreateProcess 直接拉起（subprocess 不走 shell）。
#: 这三条验的是与平台无关的判据（退出码、调用次序），Windows 那侧真正的风险是
#: 编码，由 tests/test_windows_regressions.py 的两条专门看着。
posix_shim_only = pytest.mark.skipif(
    os.name == "nt", reason="假 CLI 用 shebang 脚本，Windows 上起不来"
)


def _run_handoff(tmp_path, response: dict, *args, _target=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "tavotto"
    # PATH 只留假 CLI 这一个目录（真 tavotto 绝不能抢答），所以 shebang 必须是
    # 绝对路径的解释器——`/usr/bin/env python3` 在这种 PATH 下解析不出来。
    fake.write_text(FAKE_CLI.replace("#!PYTHON", "#!" + sys.executable), encoding="utf-8")
    fake.chmod(0o755)
    resp_file = tmp_path / "response.json"
    resp_file.write_text(json.dumps(response), encoding="utf-8")
    log = tmp_path / "calls.log"

    if _target is None:
        target = tmp_path / "Fig1.pdf"
        target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    else:
        target = _target

    env = {
        **os.environ,
        "PATH": str(bin_dir),
        "FAKE_RESPONSE": str(resp_file),
        "FAKE_LOG": str(log),
    }
    env.pop("TAVOTTO_CLI", None)
    # 子进程按 UTF-8 写（它自己 reconfigure 过），这边解码也得钉死——
    # 不钉就跟随系统区域编码，Windows 上读中文 JSON 当场变乱码
    proc = subprocess.run(
        [sys.executable, str(HANDOFF), str(target), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc, calls


@posix_shim_only
def test_handoff_succeeds_when_the_figure_is_parameterizable(tmp_path):
    proc, calls = _run_handoff(
        tmp_path,
        {
            "ok": True,
            "project": "/p",
            "stem": "Fig1",
            "registry": {"parameterizable": True, "conflicts": [], "dynamic_names": []},
        },
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["parameterizable"] is True and out["launch"] == "desktop"
    # **稳定产物（.pdf）只交接一次**：needs_run 恒为 False，先探测那跳只会把
    # 同一份注册表再读一遍，白付一次 CLI 冷启动（frozen CLI 一跳几百 ms）
    assert len(calls) == 1 and "--no-launch" not in calls[0]
    assert "timings" in out and "open_ms" in out["timings"]


def _load_plugin_handoff():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_plugin_handoff_env", SKILL_DIR / "scripts" / "handoff.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_env_is_headless_with_a_stable_mpl_cache(tmp_path, monkeypatch):
    """跑用户脚本的环境必须无头（Agg）且字体缓存目录固定——沙箱里默认 GUI
    backend 会崩在 AppKit 初始化上，HOME 只读时 matplotlib 每次重建字体缓存
    白付十来秒（2026-08-20 实测的两条慢因）。"""
    mod = _load_plugin_handoff()
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("MPLBACKEND", raising=False)
    monkeypatch.delenv("MPLCONFIGDIR", raising=False)
    env = mod.script_env()
    assert env["MPLBACKEND"] == "Agg"
    assert env["MPLCONFIGDIR"].startswith(str(tmp_path / "cfg"))
    assert os.path.isdir(env["MPLCONFIGDIR"]), "缓存目录要建好，matplotlib 不会自己建"
    # 第二次拿到同一个目录——缓存才能复用
    assert mod.script_env()["MPLCONFIGDIR"] == env["MPLCONFIGDIR"]


def test_script_env_never_overrides_the_users_choice(monkeypatch):
    mod = _load_plugin_handoff()
    monkeypatch.setenv("MPLBACKEND", "QtAgg")
    monkeypatch.setenv("MPLCONFIGDIR", "/my/own")
    env = mod.script_env()
    assert env["MPLBACKEND"] == "QtAgg" and env["MPLCONFIGDIR"] == "/my/own"


@posix_shim_only
def test_handoff_probes_before_running_a_script(tmp_path):
    """给的是 .py 时仍要**先探测再交接**：跑完脚本可能多出新 stem，
    第一次探测时它还不在磁盘上（登记与定位都会落空）。"""
    script = tmp_path / "fig1.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    proc, calls = _run_handoff(
        tmp_path,
        {
            "ok": True,
            "project": str(tmp_path),
            "stem": None,
            "registry": {"parameterizable": True, "conflicts": [], "dynamic_names": []},
        },
        _target=script,
    )
    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 2
    assert "--no-launch" in calls[0] and "--no-launch" not in calls[1]


@posix_shim_only
def test_handoff_fails_loudly_when_the_figure_has_no_script(tmp_path):
    """用户强调的那条硬约定：脚本没跟图放在一起 = 没做完，退出码必须非零。"""
    proc, _ = _run_handoff(
        tmp_path,
        {
            "ok": True,
            "project": "/p",
            "stem": "Fig1",
            "registry": {"parameterizable": False, "conflicts": [], "dynamic_names": []},
        },
    )
    assert proc.returncode == 4
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "同一个目录" in out["hint"]


@posix_shim_only
def test_handoff_reports_tavotto_open_failure(tmp_path):
    proc, _ = _run_handoff(tmp_path, {"ok": False, "error": "注册表不是合法 JSON"})
    assert proc.returncode == 2
    assert "注册表不是合法 JSON" in proc.stdout


def test_handoff_rejects_missing_path(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(HANDOFF), str(tmp_path / "nope.pdf")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ},
    )
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["ok"] is False


@pytest.mark.skipif(tomllib is None, reason="需要 tomllib（Python ≥ 3.11）")
def test_plugin_is_excluded_from_the_python_package():
    """pip 用户拿到的是 Tavotto，不该夹带一份 Codex 插件。"""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    exclude = cfg["tool"]["hatch"]["build"]["exclude"]
    assert "codex-plugin" in exclude and "codex-plugin/**" in exclude
    assert ".agents" in exclude and ".agents/**" in exclude


# ==================== 只装了桌面版时的发现链（回归） =====================
# 起因：Windows 用户只装了 Tavotto 桌面程序，插件一直报 tavotto_missing。
# 桌面版的 Tavotto.exe 是 GUI 子系统的可执行文件，当命令行调它拿不到 stdout；
# 插件当时只会查 TAVOTTO_CLI / PATH / 当前解释器，三条全落空。
#
# 这几条端到端跑真进程：真 argv、真 JSON、真的从磁盘上找 CLI。路径规则本身
# 的跨平台矩阵在 tests/test_install_locate.py（那边用注入的假文件系统，
# 每个平台都测得了）。

FAKE_BRIDGE = """#!PYTHON
import json, os, sys
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\\n")
resp = json.load(open(os.environ["FAKE_RESPONSE"], encoding="utf-8"))
if "--no-launch" not in sys.argv:
    resp["launch"] = {"mode": "desktop"}
print(json.dumps(resp, ensure_ascii=False))
"""

OK_RESPONSE = {
    "ok": True,
    "protocol": 1,
    "project": "/p",
    "stem": "Fig1",
    "registry": {
        "parameterizable": True,
        "status": "created",
        "conflicts": [],
        "dynamic_names": [],
    },
}

desktop_discovery_only = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="假 bridge 要 shebang 脚本（Windows 起不来），"
    "Linux 没有桌面发行形态（install_roots 本来就是空的）",
)

#: macOS 的 `/Applications` 是绝对路径，env 隔离不掉它——**开发机上真装着的
#: 那份 Tavotto 会真的被发现**（这本身正是发现链在干活）。所以「机器上没有
#: 桌面版」这类模拟只在真没装时才成立；同一判据的注入版（假文件系统，与机器
#: 无关）在 tests/test_install_locate.py，那边任何机器上都跑。
REAL_APP = "/Applications/Tavotto.app/Contents/MacOS/Tavotto"
REAL_APP_CLI = "/Applications/Tavotto.app/Contents/Resources/sidecar/Tavotto/tavotto-cli"
needs_no_real_desktop = pytest.mark.skipif(
    os.path.isfile(REAL_APP), reason="这台机器上真装着 Tavotto 桌面版，「什么都没装」模拟不出来"
)
needs_no_real_cli = pytest.mark.skipif(
    os.path.isfile(REAL_APP_CLI),
    reason="这台机器上真装着带 CLI 的 Tavotto 桌面版，「装了但没 CLI」模拟不出来",
)

#: 假 bridge 是带 shebang 的脚本。Windows 的 CreateProcess 起不了它（也起不了
#: .bat/.cmd，subprocess 不走 shell），所以这一类只在 POSIX 上跑——与文件上半部
#: 分那个 posix_shim_only 同一个理由。**Windows 上的等价覆盖有两条**：
#: tests/test_install_locate.py 用注入的假文件系统测同一套判据（平台无关），
#: 下面 test_real_cli_handoff_end_to_end 用 pip 装出来的真 tavotto 走完整链路。
posix_bridge_only = pytest.mark.skipif(
    os.name == "nt", reason="假 bridge 用 shebang 脚本，Windows 上起不来"
)


@pytest.fixture(scope="module")
def clean_python(tmp_path_factory):
    """一个 import 不到 tavotto 的解释器。

    插件最后一条兜底是「当前解释器里有 tavotto 模块」。测试要是用仓库的
    .venv 跑它，那条兜底永远成立——「没装 Tavotto」这一类用例会被它悄悄
    救活，而它们恰恰是这次要修的东西。
    """
    venv = tmp_path_factory.mktemp("clean-venv") / "v"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, capture_output=True
    )
    exe = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not exe.is_file():
        pytest.skip("建不出干净的解释器")
    probe = subprocess.run([str(exe), "-c", "import tavotto"], capture_output=True)
    if probe.returncode == 0:
        pytest.skip("干净解释器里居然有 tavotto")
    return str(exe)


def _write_bridge(path: Path) -> Path:
    """把假 bridge 写到 path（当成装好的 tavotto-cli）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FAKE_BRIDGE.replace("#!PYTHON", "#!" + sys.executable), encoding="utf-8")
    path.chmod(0o755)
    return path


def _plugin_env(tmp_path, **extra):
    """一个干净到底的环境：PATH 里没有 tavotto，也没有 TAVOTTO_CLI。

    **已知安装位置也要一起指到临时目录**：Windows 的 `install_roots()` 读的是
    `%LOCALAPPDATA%` / `%PROGRAMFILES%`，不改它们的话 runner（或开发机）上真装
    着的 Tavotto 会被发现——「什么都没装」就模拟不出来了。macOS 的
    `/Applications` 是绝对路径改不掉，那条由 needs_no_real_desktop 兜。
    """
    empty = tmp_path / "empty-bin"
    empty.mkdir(exist_ok=True)
    roots = tmp_path / "roots"
    (roots / "local").mkdir(parents=True, exist_ok=True)
    (roots / "pf").mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PATH": str(empty),
        "HOME": str(tmp_path),
        "LOCALAPPDATA": str(roots / "local"),
        "PROGRAMFILES": str(roots / "pf"),
        "TAVOTTO_CONFIG_DIR": str(tmp_path / "config"),
    }
    env.pop("PROGRAMFILES(X86)", None)
    env.pop("TAVOTTO_CLI", None)
    env.update(extra)
    return env


def _run_plugin(python, tmp_path, env, *args, response=None):
    resp_file = tmp_path / "response.json"
    resp_file.write_text(json.dumps(response or OK_RESPONSE, ensure_ascii=False), encoding="utf-8")
    log = tmp_path / "calls.log"
    env = {**env, "FAKE_RESPONSE": str(resp_file), "FAKE_LOG": str(log)}
    proc = subprocess.run(
        [python, str(HANDOFF), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    calls = (
        [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        if log.exists()
        else []
    )
    out = None
    if proc.stdout.strip():
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    return proc, out, calls


@desktop_discovery_only
@needs_no_real_cli
def test_desktop_only_install_is_discovered(clean_python, tmp_path):
    """**这条就是那个 bug 的正面回归。**

    只装了桌面版：PATH 里没有 tavotto，没设 TAVOTTO_CLI，当前解释器也 import
    不到它。插件必须靠安装位置里的 tavotto-cli 把交接做完。
    """
    app = tmp_path / "Applications" / "Tavotto.app"
    _write_bridge(app / "Contents" / "Resources" / "sidecar" / "Tavotto" / "tavotto-cli")
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "Tavotto").write_text("gui", encoding="utf-8")
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")

    proc, out, calls = _run_plugin(clean_python, tmp_path, _plugin_env(tmp_path), str(target))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["ok"] is True and out["parameterizable"] is True
    assert out["tavotto"]["source"] == "install"
    assert out["launch"] == "desktop"  # 原生窗口，不是浏览器
    # 稳定产物（.pdf）单跳交接，不再有多余的探测那一跳
    assert len(calls) == 1
    assert "--no-launch" not in calls[0]
    assert calls[0][:3] == ["open", str(target), "--json"]


@desktop_discovery_only
@needs_no_real_cli
def test_desktop_installed_but_without_cli_is_its_own_error(clean_python, tmp_path):
    """装了桌面版、那一版没带 CLI——**不能报「没装 Tavotto」**。

    用户明明装了。报 tavotto_missing 会让他再去装一遍已经装着的东西，
    然后发现还是不行。该说的是「升级」。
    """
    app = tmp_path / "Applications" / "Tavotto.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    (app / "Tavotto").write_text("gui", encoding="utf-8")
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")

    proc, out, _ = _run_plugin(clean_python, tmp_path, _plugin_env(tmp_path), str(target))
    assert proc.returncode == 3
    assert out["error_code"] == "desktop_found_cli_missing"
    assert out["desktop"].endswith("Tavotto.app/Contents/MacOS/Tavotto")
    assert "最新版" in out["hint"]


@posix_bridge_only
def test_manifest_discovery_survives_spaces_and_chinese(clean_python, tmp_path):
    """安装清单指到带空格和中文的路径：一路到 bridge 都不许被拆开。

    这条平台无关（清单是绝对路径，不依赖平台惯例位置），所以三个平台都跑。
    """
    bridge = _write_bridge(tmp_path / "我的 程序" / "Tavotto" / "tavotto-cli")
    config = tmp_path / "config"
    config.mkdir()
    (config / "install.json").write_text(
        json.dumps(
            {
                "protocol": 1,
                "product": "Tavotto",
                "version": "9.9.9",
                "cli": str(bridge),
                "desktop": None,
                "install_dir": None,
                "source": "installer",
            }
        ),
        encoding="utf-8",
    )
    project = tmp_path / "我的 图库"
    project.mkdir()
    target = project / "图 1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")

    proc, out, calls = _run_plugin(clean_python, tmp_path, _plugin_env(tmp_path), str(target))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["tavotto"]["source"] == "manifest"
    assert out["tavotto"]["cmd"] == str(bridge)
    # bridge 收到的是**一个**参数，不是被空格切成两半的两个
    assert calls[0][1] == str(target)


@posix_bridge_only
def test_explicit_env_override_still_wins(clean_python, tmp_path):
    """既有行为不许被新链路顶掉：TAVOTTO_CLI 指到哪儿就用哪儿。"""
    chosen = _write_bridge(tmp_path / "chosen" / "tavotto")
    ignored = _write_bridge(tmp_path / "ignored" / "tavotto-cli")
    config = tmp_path / "config"
    config.mkdir()
    (config / "install.json").write_text(
        json.dumps({"protocol": 1, "cli": str(ignored)}), encoding="utf-8"
    )
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")

    env = _plugin_env(tmp_path, TAVOTTO_CLI=str(chosen))
    proc, out, _ = _run_plugin(clean_python, tmp_path, env, str(target))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["tavotto"] == {"source": "env", "cmd": str(chosen)}


@posix_bridge_only
def test_path_cli_still_wins_over_the_install(clean_python, tmp_path):
    """PATH 里的 tavotto（pip/pipx 装的）优先级仍在 CLI shim 之前。"""
    bin_dir = tmp_path / "bin"
    _write_bridge(bin_dir / "tavotto")
    _write_bridge(tmp_path / "config-cli" / "tavotto-cli")
    config = tmp_path / "config"
    config.mkdir()
    (config / "install.json").write_text(
        json.dumps({"protocol": 1, "cli": str(tmp_path / "config-cli" / "tavotto-cli")}),
        encoding="utf-8",
    )
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")

    env = _plugin_env(tmp_path, PATH=str(bin_dir))
    proc, out, _ = _run_plugin(clean_python, tmp_path, env, str(target))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["tavotto"]["source"] == "path"


@needs_no_real_desktop
def test_nothing_installed_reports_tavotto_missing(clean_python, tmp_path):
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    proc, out, _ = _run_plugin(clean_python, tmp_path, _plugin_env(tmp_path), str(target))
    assert proc.returncode == 3
    assert out["error_code"] == "tavotto_missing"
    assert out["tavotto_missing"] is True  # 旧字段保留（SKILL.md 认它）
    assert "releases" in out["hint"]


@posix_bridge_only
def test_no_launch_reaches_the_bridge(clean_python, tmp_path):
    """`--no-launch` 一路传到 CLI，且第二次调用不再发生。"""
    bridge = _write_bridge(tmp_path / "cli" / "tavotto-cli")
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    env = _plugin_env(tmp_path, TAVOTTO_CLI=str(bridge))
    proc, out, calls = _run_plugin(clean_python, tmp_path, env, str(target), "--no-launch")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert all("--no-launch" in call for call in calls)
    assert out["launch"] is None  # 一个界面都没起


@posix_bridge_only
def test_open_error_code_is_passed_through(clean_python, tmp_path):
    """`tavotto open` 自己的 code（比如注册表写不进去）要原样带出来。

    统一压成一句「交接失败」，用户就不知道该去改目录权限还是去装东西。
    """
    bridge = _write_bridge(tmp_path / "cli" / "tavotto-cli")
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    env = _plugin_env(tmp_path, TAVOTTO_CLI=str(bridge))
    proc, out, _ = _run_plugin(
        clean_python,
        tmp_path,
        env,
        str(target),
        response={
            "ok": False,
            "code": "registry_write_failed",
            "error": "注册表写不进去 /p/tavotto_registry.json",
        },
    )
    assert proc.returncode == 2
    # **原样带出来**，不是压成 open_failed：SKILL.md 教 Codex 的就是按
    # error_code 分支（registry_write_failed → 换个可写目录）。藏进第二层
    # 等于那条指引永远走不到。
    assert out["error_code"] == "registry_write_failed"
    assert out["code"] == "registry_write_failed"


def test_unrunnable_cli_is_not_reported_as_missing(clean_python, tmp_path):
    """TAVOTTO_CLI 指到了不存在的东西：说「执行不了」，不说「没装」。"""
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    env = _plugin_env(tmp_path, TAVOTTO_CLI=str(tmp_path / "没有这个文件"))
    proc, out, _ = _run_plugin(clean_python, tmp_path, env, str(target))
    assert proc.returncode == 2
    assert out["error_code"] == "cli_exec_failed"


@posix_bridge_only
def test_open_failure_without_a_code_still_has_one(clean_python, tmp_path):
    """老版本 tavotto 不带 code：那时才回落到 open_failed。"""
    bridge = _write_bridge(tmp_path / "cli" / "tavotto-cli")
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    env = _plugin_env(tmp_path, TAVOTTO_CLI=str(bridge))
    proc, out, _ = _run_plugin(
        clean_python, tmp_path, env, str(target), response={"ok": False, "error": "说不清哪儿错了"}
    )
    assert proc.returncode == 2
    assert out["error_code"] == "open_failed"


def test_skill_documents_every_error_code_it_can_emit():
    """SKILL.md 里教 Codex 分支的那几个 code，插件真的发得出来。

    这条挡的正是 Codex review 抓到的那种错位：文档说「看到
    registry_write_failed 就换个可写目录」，而实现把它压成了 open_failed，
    于是那段指引永远走不到，两边各看各的都很合理。
    """
    skill = (SKILL_DIR / "references" / "desktop-handoff.md").read_text(encoding="utf-8")
    documented = set(re.findall(r'"error_code": "(\w+)"', skill))
    assert documented, "references/desktop-handoff.md 里一个 error_code 都没写"
    src = HANDOFF.read_text(encoding="utf-8")
    for code in documented:
        if code in {"tavotto_missing", "desktop_found_cli_missing"}:
            assert code in src  # 插件自己的 code
        else:
            # 来自 tavotto open 的 code：靠 _open_failure 原样透传
            assert 'code or "open_failed"' in src, (
                f"SKILL.md 承诺了 {code}，但插件没有透传 CLI 的 code"
            )


def test_plugin_consults_the_registry_like_the_engine_locator():
    """HKCU 那条腿两侧都要有。

    engine.locate.find_cli 有、插件没有的话，「装在非默认目录 + 清单又没写成」
    的 Windows 机器上，插件会报 tavotto_missing 而 Tavotto 自己找得到——
    同一台机器两个答案。
    """
    src = HANDOFF.read_text(encoding="utf-8")
    assert "hkcu_install_dirs" in src
    assert "winreg" in src
    from tavotto.engine import locate

    assert locate.UNINSTALL_KEY.replace("\\", "\\\\") in src or locate.UNINSTALL_KEY in src.replace(
        "\\\\", "\\"
    )


def test_every_failure_payload_carries_an_error_code():
    """插件的每一条失败出口都要带 error_code——调用方按它分诊。"""
    src = HANDOFF.read_text(encoding="utf-8")
    tree = ast.parse(src)
    emits = [
        n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "emit"
    ]
    assert emits, "找不到 emit 调用"
    for node in emits:
        code = node.args[1]
        if isinstance(code, ast.Constant) and code.value == 0:
            continue  # 成功那条不需要
        payload = node.args[0]
        if isinstance(payload, ast.Dict):
            keys = {k.value for k in payload.keys if isinstance(k, ast.Constant)}
            assert "error_code" in keys, f"第 {node.lineno} 行的失败出口没有 error_code"


@pytest.mark.skipif(shutil.which("tavotto") is None, reason="PATH 里没有 pip 装出来的 tavotto")
def test_real_cli_handoff_end_to_end(tmp_path):
    """拿**真的** tavotto CLI 走完整条链路——Windows 上也跑。

    上面那批用假 bridge 的用例在 Windows 上起不来（shebang 脚本），可
    「路径带空格和中文时会不会被拆开」恰恰是 Windows 最容易出事的地方。
    这一条用 `pip install -e .` 装出来的 `tavotto`（Windows 上是真的 .exe）
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
        encoding="utf-8",
    )
    (project / "Fig1_演示.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")

    env = {
        **os.environ,
        "TAVOTTO_CLI": shutil.which("tavotto"),
        "TAVOTTO_CONFIG_DIR": str(tmp_path / "cfg"),
        "TAVOTTO_DATA_DIR": str(tmp_path / "data"),
    }
    proc = subprocess.run(
        [
            sys.executable,
            str(HANDOFF),
            str(project / "fig_demo.py"),
            "--run",
            "never",
            "--no-launch",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is True
    assert out["parameterizable"] is True
    assert out["project"] == str(project)  # 空格与中文原样，没被拆开
    assert out["stem"] == "Fig1_演示"
    assert out["launch"] is None  # --no-launch：一个界面都没起
    assert out["tavotto"]["source"] == "env"
    registry = json.loads((project / "tavotto_registry.json").read_text(encoding="utf-8"))
    assert "fig_demo.py" in registry["scripts"]


# ===================== 插件的更新通道（发布侧） ==========================
# 用户装了插件之后不会自动收到更新——Codex 不管这件事。所以插件自己查一份
# 清单，而那份清单是发版时生成的。这几条盯的是「发版时它真的被生成、内容对」。


def _manifest_module():
    spec = importlib.util.spec_from_file_location(
        "make_plugin_manifest", ROOT / "scripts" / "make_plugin_manifest.py"
    )
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
    mod, data = _make_manifest(tmp_path, "v" + tavotto.__version__)
    spec = importlib.util.spec_from_file_location("_uc", SKILL_DIR / "scripts" / "update_check.py")
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


def test_plugin_manifest_min_tavotto_version_is_real(tmp_path):
    """`min_tavotto_version` 必须是真发过的版本，且不高于当前版本。

    随手往上调会让一批老用户看到「去升级 Tavotto」，而他们的 Tavotto 可能
    完全够用。当前值是第一个带 `tavotto open` 的版本。
    """
    mod, data = _make_manifest(tmp_path, "v" + tavotto.__version__)
    required = data["min_tavotto_version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", required), required
    assert tuple(map(int, required.split("."))) <= tuple(map(int, tavotto.__version__.split(".")))
    assert mod.MIN_TAVOTTO_VERSION == required


def _plugin_dir_with_built_widget(tmp_path):
    """插件目录的一份副本，画布产物**一定在**（缺就放一个占位）。

    画布不进版本库（2026-08-30），而 `build_zip` 现在断言它必须在——那条断言
    是对的（发布链忘了构建 = 用户装到一个没有 UI 的插件）。但**它不该把打包
    形态的用例连坐**：那些用例问的是「zip 里有没有技能本体」，跟画布内容无关，
    在一台没跑过前端构建的机器上不该红，也不该 skip（skip 掉就没人看着 zip
    的内容了，而这个 job 本来就没有 Node，等于永远 skip）。
    """
    import shutil

    src = ROOT / "codex-plugin"
    dst = tmp_path / "codex-plugin"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    canvas = dst / "mcp" / "widget" / "canvas.html"
    if not canvas.is_file():
        canvas.parent.mkdir(parents=True, exist_ok=True)
        canvas.write_text(
            '<!-- tavotto-mcp-widget 0000000000000000 -->\n<div id="root"></div>\n',
            encoding="utf-8",
        )
    return dst


def test_plugin_zip_contains_the_skill(tmp_path):
    """安装包里要有技能本体，不能只有清单。"""
    import zipfile

    target = _manifest_module().build_zip(
        tmp_path / "p.zip", source=_plugin_dir_with_built_widget(tmp_path)
    )
    names = zipfile.ZipFile(target).namelist()
    for needed in (
        "codex-plugin/.codex-plugin/plugin.json",
        "codex-plugin/skills/tavotto-figure/SKILL.md",
        "codex-plugin/skills/tavotto-figure/agents/openai.yaml",
        # SKILL.md 按需引用的细则必须随包走，缺一份 = 状态机断链
        "codex-plugin/skills/tavotto-figure/references/first-run-and-recovery.md",
        "codex-plugin/skills/tavotto-figure/references/figure-contract.md",
        "codex-plugin/skills/tavotto-figure/references/publication-style.md",
        "codex-plugin/skills/tavotto-figure/references/desktop-handoff.md",
        "codex-plugin/skills/tavotto-figure/references/issue-reporting.md",
        "codex-plugin/skills/tavotto-figure/references/compatibility.md",
        "codex-plugin/skills/tavotto-figure/scripts/handoff.py",
        "codex-plugin/skills/tavotto-figure/scripts/update_check.py",
    ):
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
    desktop = (ROOT / ".github" / "workflows" / "desktop-tauri.yml").read_text(encoding="utf-8")
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
    assert list(servers) == ["tavotto"]
    entry = servers["tavotto"]
    # 本地 stdio：command + args + cwd。远程 HTTP 那套字段这里一个都不该有
    assert entry["command"] == "python3"
    assert entry["args"] == ["./mcp/server.py"]
    assert entry["cwd"] == "."
    assert "url" not in entry
    # 起 worker 要跑用户的脚本，heavy 的图是分钟级——超时不能用默认的那点
    assert entry["tool_timeout_sec"] >= 600
    for name in ("TAVOTTO_CLI", "TAVOTTO_MCP_ROOTS", "PATH"):
        assert name in entry["env_vars"], f"{name} 没进 env_vars，server 那边读不到"
    assert (PLUGIN / "mcp" / "server.py").is_file()


def test_launcher_is_stdlib_only_and_parses():
    """启动器跑在**用户机器上的任意 python3**（可能没装 tavotto）。

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
    allowed = {
        "json",
        "os",
        "shutil",
        "subprocess",
        "sys",
        "time",
        "__future__",
        "tavotto",
        "tavotto_mcp",
        "handoff",
    }
    assert not (imported - allowed), f"启动器引入了非标准库: {sorted(imported - allowed)}"


def test_launcher_degrades_instead_of_dying_without_tavotto():
    """跑不起来时**不能静默退出**——那在 Codex 里就是「插件没有工具」。"""
    src = (PLUGIN / "mcp" / "server.py").read_text(encoding="utf-8")
    assert "_degraded_server" in src
    assert "pipx install tavotto" in src


def test_launcher_reuses_the_plugin_locator_instead_of_a_third_copy():
    """路径规则已经有两份（`engine/locate.py` + 插件的 handoff），不许有第三份。

    启动器与 handoff.py 同属一个插件包，直接 import 那份就好；自己再抄一遍
    的话，「只装了桌面版」这类格子会在两处各修一次——而 #7 刚为此付过一次账。
    """
    src = (PLUGIN / "mcp" / "server.py").read_text(encoding="utf-8")
    assert "find_tavotto" in src, "启动器没有复用插件自带的定位器"
    for owned_by_the_locator in (
        "LOCALAPPDATA",
        "install.json",
        "SIDECAR_REL",
        "UNINSTALL_KEY",
        "/Applications/Tavotto.app",
    ):
        assert owned_by_the_locator not in src, (
            f"启动器里出现了 {owned_by_the_locator}——路径规则该由定位器说了算"
        )


def test_launcher_tells_desktop_only_users_the_truth():
    """**只装桌面版**要单独报，不能笼统说「没装 Tavotto」——他明明装了。

    交接只要能*执行* `tavotto open`，桌面版带的 `tavotto-cli` 就够；但 MCP
    server 要 `import tavotto` 在进程内驱动引擎，而那个 CLI 是 frozen 的，
    给不出解释器。三态互斥，各有各的下一步动作。
    """
    sys.path.insert(0, str(PLUGIN / "mcp"))
    import importlib

    launcher = importlib.import_module("server")

    code, hint = launcher.diagnose(
        {
            "cmd": ["/Applications/Tavotto.app/…/tavotto-cli"],
            "desktop": "/Applications/Tavotto.app/…/Tavotto",
        }
    )
    assert code == "desktop_only"
    assert "pipx install tavotto" in hint
    assert "没装" not in hint, "对着装了桌面版的用户说「没装」"

    code, hint = launcher.diagnose({"cmd": None, "desktop": "/Applications/Tavotto.app"})
    assert code == "desktop_found_cli_missing"

    code, hint = launcher.diagnose({"cmd": None, "desktop": None})
    assert code == "tavotto_missing"


def test_launcher_only_takes_interpreters_it_can_actually_use():
    """frozen 的 `tavotto-cli` 给不出解释器：它没有 shebang，旁边也没有 python。

    反过来，pip / pipx 装的 `tavotto` 是带 shebang 的小脚本——这条区分就是
    「桌面版」与「Python 环境」两格的分界线。
    """
    sys.path.insert(0, str(PLUGIN / "mcp"))
    import importlib

    launcher = importlib.import_module("server")

    with tempfile.TemporaryDirectory() as tmp:
        frozen = os.path.join(tmp, "tavotto-cli")
        with open(frozen, "wb") as f:  # ELF/PE 头，不是 shebang
            f.write(b"\x7fELF\x02\x01\x01\x00")
        assert launcher._shebang_interpreter(frozen) is None
        assert launcher._interpreter_beside(frozen) == []

        shim = os.path.join(tmp, "tavotto")
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


def test_plugin_zip_refuses_to_ship_without_the_widget(tmp_path):
    """画布产物不在时**打包必须当场失败**，不许打出一个没有 UI 的插件。

    这是 `canvas.html` 移出版本库（2026-08-30）换来的那个新风险的唯一兜底：
    老形态里「产物过期」由 `--check` 看着，新形态里「产物根本不在」由这里看着。
    MCP server 缺产物时会**如实降级成没有 UI、零报错**——正因为它安静，
    这条闸才必须响。
    """
    src = _plugin_dir_with_built_widget(tmp_path)
    (src / "mcp" / "widget" / "canvas.html").unlink()
    with pytest.raises(SystemExit) as e:
        _manifest_module().build_zip(tmp_path / "nope.zip", source=src)
    assert "canvas.html" in str(e.value)
    assert "build_mcp_widget" in str(e.value), "报错要说清楚怎么补"
    assert not (tmp_path / "nope.zip").exists(), "拦住了就不该留下半个 zip"


def test_widget_artifact_has_the_shape_the_server_expects():
    """构建出来的产物必须是 server 认得的那个形状。

    它**不进版本库**（2026-08-30，见 `.gitignore`），所以这里 skip 掉「还没
    构建」是对的——那是新克隆上的正常状态，不是错误。**「产物必须在」这条
    另有其人**：`scripts/make_plugin_manifest.py` 打 zip 时断言它在，因为
    「发布链忘了构建 = 用户装到一个没有画布的插件」才是真的失败路径。
    两件事分开写，混成一条就会得到一个「不在也算过」的空门禁。
    """
    canvas = PLUGIN / "mcp" / "widget" / "canvas.html"
    if not canvas.is_file():
        pytest.skip("画布产物未构建（跑一次 scripts/build_mcp_widget.py）")
    text = canvas.read_text(encoding="utf-8")
    assert text.startswith("<!-- tavotto-mcp-widget ")
    assert '<div id="root">' in text


# ------------------------- prefs.py 的行为契约 ----------------------------
# 开工三问的答案落在用户配置目录。这几条盯的是「偏好文件坏了/写不进去时
# 技能仍然能工作（大不了重新问）」与「键是闭集，杂物进不来」。


def _load_prefs_module():
    spec = importlib.util.spec_from_file_location("_prefs", SKILL_DIR / "scripts" / "prefs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prefs_roundtrip_and_key_closure(tmp_path):
    """写进去的读得回来；不认识的键与不合法的值在读取时被丢弃。"""
    mod = _load_prefs_module()
    path = str(tmp_path / "prefs.json")
    assert mod.write_prefs({"width": "double", "font": "Arial", "legend_frame": "off"}, path)
    assert mod.read_prefs(path) == {"width": "double", "font": "Arial", "legend_frame": "off"}
    # 手工塞进垃圾键/垃圾值：读取端按闭集过滤，不报错也不透传
    (tmp_path / "prefs.json").write_text(
        json.dumps(
            {
                "schema": mod.SCHEMA,
                "prefs": {
                    "width": "wide",
                    "font": "",
                    "legend_frame": "off",
                    "favorite_color": "blue",
                },
            }
        ),
        encoding="utf-8",
    )
    assert mod.read_prefs(path) == {"legend_frame": "off"}


def test_prefs_read_never_raises_on_garbage(tmp_path):
    """文件缺失 / 是垃圾 / schema 对不上，一律当「什么都没记」。"""
    mod = _load_prefs_module()
    assert mod.read_prefs(str(tmp_path / "missing.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")
    assert mod.read_prefs(str(bad)) == {}
    bad.write_text(json.dumps({"schema": 999, "prefs": {"font": "Arial"}}), encoding="utf-8")
    assert mod.read_prefs(str(bad)) == {}


def test_prefs_cli_writes_only_into_the_config_dir(tmp_path):
    """端到端：--set 落在 TAVOTTO_CONFIG_DIR，绝不写插件目录。"""
    env = {**os.environ, "TAVOTTO_CONFIG_DIR": str(tmp_path / "cfg")}
    script = SKILL_DIR / "scripts" / "prefs.py"
    before = {p for p in SKILL_DIR.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    out = subprocess.run(
        [
            sys.executable,
            str(script),
            "--set",
            "font=Times New Roman",
            "--set",
            "width=single",
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=True,
    )
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["saved"] is True
    assert data["prefs"] == {"font": "Times New Roman", "width": "single"}
    assert (tmp_path / "cfg" / "codex-plugin-figure-prefs.json").is_file()
    after = {p for p in SKILL_DIR.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    assert after == before, "prefs.py 往插件目录里写了东西"
    # 再跑一次读 + unset：记录过的读得回来，退回后消失
    out = subprocess.run(
        [sys.executable, str(script), "--unset", "width", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=True,
    )
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["prefs"] == {"font": "Times New Roman"}


def test_prefs_cli_rejects_unknown_keys(tmp_path):
    """键是闭集：不认识的键当场拒绝，退出码非零，偏好文件不落地。"""
    env = {**os.environ, "TAVOTTO_CONFIG_DIR": str(tmp_path / "cfg")}
    script = SKILL_DIR / "scripts" / "prefs.py"
    out = subprocess.run(
        [sys.executable, str(script), "--set", "favorite_color=blue", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert out.returncode != 0
    assert not (tmp_path / "cfg" / "codex-plugin-figure-prefs.json").exists()
    out = subprocess.run(
        [sys.executable, str(script), "--set", "width=huge", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert out.returncode != 0


# ==================== 首次使用体验（2026-08-25 反转） =====================
# README 是普通用户的唯一安装入口；SKILL.md 的会话入口是「先检查，不安装」。
# 这一段盯的是：入口文案、sparse 双路径、openai.yaml 的 MCP 依赖声明，
# 以及行为场景与文档锚点的绑定。

READMES = {
    "zh": ROOT / "README.zh-CN.md",
    "en": ROOT / "README.md",
}
SPARSE_CMD = (
    "codex plugin marketplace add Tavotto/Tavotto --sparse .agents/plugins --sparse codex-plugin"
)


def test_readme_first_use_entry_says_no_clone_no_build():
    """首次安装用户看到的第一条说明就是「不 clone、不构建源码」，中英一致。"""
    zh = READMES["zh"].read_text(encoding="utf-8")
    en = READMES["en"].read_text(encoding="utf-8")
    assert "在 Codex 中第一次使用 Tavotto" in zh
    assert "普通用户不要克隆或构建这个仓库" in zh
    assert "Using Tavotto with Codex for the first time" in en
    assert "do not clone or build this repository" in en
    # 源码开发是贡献者的路，不是普通用户的 fallback
    assert "贡献者：从源码开发" in zh
    assert "Contributors: developing from source" in en


def test_readme_install_is_two_codex_commands_plus_engine_plus_new_session():
    """安装步骤 = 两条 Codex 命令（分开、双 sparse）+ 一条引擎命令 + 新开会话。"""
    for lang, path in READMES.items():
        text = path.read_text(encoding="utf-8")
        assert SPARSE_CMD in text, f"{path.name} 缺 sparse 双路径安装命令"
        assert "codex plugin add tavotto@tavotto" in text
        assert 'pipx install "tavotto[worker]"' in text
        # 两条 codex 命令不许再用 && 串成一条
        assert "Tavotto/Tavotto && codex plugin add" not in text
    zh = READMES["zh"].read_text(encoding="utf-8")
    en = READMES["en"].read_text(encoding="utf-8")
    assert "新开一个会话" in zh
    assert "start a new one" in en


def test_readme_names_the_surfaces_that_do_not_load_local_plugins():
    """不加载本机插件的宿主界面要明确说出来，别让用户在那儿反复排障。"""
    zh = READMES["zh"].read_text(encoding="utf-8")
    en = READMES["en"].read_text(encoding="utf-8")
    assert "不读取本机插件的界面" in zh
    assert "does not load local plugins" in en


def test_plugin_readme_uses_the_same_install_shape():
    """codex-plugin/README.md 与根 README 同一套安装形状（sparse、分开跑）。"""
    text = (PLUGIN / "README.md").read_text(encoding="utf-8")
    assert SPARSE_CMD in text
    assert "Tavotto/Tavotto && codex plugin add" not in text


def test_openai_yaml_declares_the_mcp_dependency():
    """agents/openai.yaml 用 `dependencies.tools` 声明本插件的 MCP server。

    schema 依据是 codex-rs 的 SkillToolDependency（type/value/description/
    transport/command/url；loader 在 codex-rs/ext/skills/src/loader/metadata.rs）：
    `value` 必须与 .mcp.json 的 server key 一致，stdio 依赖按 `command`
    做规范键匹配（canonical_mcp_dependency_key），所以 `command` 也必须与
    .mcp.json 的一致——对上了，插件自带的 server 就满足依赖，Codex 不会再弹
    安装提示。测试不引第三方 yaml 库（.venv 纯净），按受控文件形状做行级断言。
    """
    yaml_text = (SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")
    mcp = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
    (server_key,) = mcp["mcpServers"].keys()
    command = mcp["mcpServers"][server_key]["command"]

    m = re.search(r"^dependencies:\n(.*?)(?=^\w|\Z)", yaml_text, re.M | re.S)
    assert m, "openai.yaml 没有顶层 dependencies 块"
    block = m.group(1)
    assert re.search(r"^\s*tools:\s*$", block, re.M), "dependencies 下缺 tools 列表"
    assert re.search(r"^\s*-\s*type:\s*mcp\s*$", block, re.M)
    assert re.search(rf"^\s*value:\s*{re.escape(server_key)}\s*$", block, re.M), (
        f"依赖的 value 必须等于 .mcp.json 的 server key（{server_key}）"
    )
    assert re.search(r"^\s*transport:\s*stdio\s*$", block, re.M)
    assert re.search(rf"^\s*command:\s*{re.escape(command)}\s*$", block, re.M), (
        f"stdio 依赖按 command 匹配，必须等于 .mcp.json 的 command（{command}）"
    )
    # interface 与 policy 原样保留——加依赖不能把显示名与隐式触发挤掉
    assert re.search(r"^interface:", yaml_text, re.M)
    assert re.search(r"^\s*allow_implicit_invocation:\s*true\s*$", yaml_text, re.M)


def _squash(text: str) -> str:
    """空白不敏感匹配：文档会重新折行，锚点不该因此失效。"""
    return re.sub(r"\s+", "", text)


def test_first_use_scenarios_are_anchored_in_the_docs():
    """八个行为场景逐一绑定到文档锚点。

    这是**结构性绑定**：文档是 Codex 的行为来源，锚点消失 = 该行为失去出处。
    它验证的是「文档承诺了这个行为」，不是「真实 agent 在该场景下真的这么做」
    ——后者需要真 Codex 会话，见下面的真实 CLI 冒烟与 PR 里的验证清单。
    """
    data = json.loads(
        (ROOT / "tests" / "fixtures" / "codex_first_use_scenarios.json").read_text(encoding="utf-8")
    )
    scenarios = data["scenarios"]
    assert len(scenarios) >= 8, "八个基本场景一个都不能少"
    ids = [s["id"] for s in scenarios]
    assert len(ids) == len(set(ids))
    for scenario in scenarios:
        for anchor in scenario["anchors"]:
            doc_path = ROOT / anchor["doc"]
            assert doc_path.is_file(), f"{scenario['id']}: 文档不存在 {anchor['doc']}"
            doc = _squash(doc_path.read_text(encoding="utf-8"))
            for needle in anchor["must"]:
                assert _squash(needle) in doc, (
                    f"场景 {scenario['id']} 的锚点在 {anchor['doc']} 里找不到：{needle!r}"
                )
            for needle in anchor.get("must_not", []):
                assert _squash(needle) not in doc, (
                    f"场景 {scenario['id']} 禁止的内容出现在 {anchor['doc']}：{needle!r}"
                )


# -------------------- 真实 Codex CLI 安装冒烟（有 CLI 才跑） ---------------
# 用真的 `codex plugin` 命令 + 全新 CODEX_HOME 装本仓库工作副本：
# 验证市场清单、插件形状与 openai.yaml 真的能被当前 Codex CLI 接受。
# CI 的 runner 没有 codex CLI 时自动跳过（保留给本机与 self-hosted nightly）；
# 走 GitHub 源 + --sparse 的联网变体由 TAVOTTO_CODEX_NET_SMOKE=1 显式开启。

codex_cli = shutil.which("codex")
needs_codex_cli = pytest.mark.skipif(codex_cli is None, reason="PATH 里没有 codex CLI")


def _codex(args, codex_home, cwd=None, timeout=120):
    env = {**os.environ, "CODEX_HOME": str(codex_home)}
    return subprocess.run(
        [codex_cli, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd,
        timeout=timeout,
    )


@needs_codex_cli
def test_real_codex_installs_the_plugin_from_a_local_marketplace(tmp_path):
    """fresh CODEX_HOME：marketplace add（本地路径）→ plugin add → plugin list。

    校验 checkout/缓存里确实有市场清单认识的插件（plugin.json + SKILL.md +
    .mcp.json + openai.yaml），而不是只看命令退出码。
    """
    home = tmp_path / "codex-home"
    home.mkdir()
    proc = _codex(["plugin", "marketplace", "add", str(ROOT)], home)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = _codex(["plugin", "add", "tavotto@tavotto"], home)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = _codex(["plugin", "list"], home)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "tavotto" in proc.stdout

    # CODEX_HOME 里能找到插件本体的关键文件（缓存布局是实现细节，按内容找）
    found = {name: False for name in ("plugin.json", "SKILL.md", ".mcp.json", "openai.yaml")}
    for path in home.rglob("*"):
        if path.name in found:
            found[path.name] = True
    missing = [name for name, ok in found.items() if not ok]
    assert not missing, f"CODEX_HOME 的插件 checkout 里缺 {missing}"


@needs_codex_cli
@pytest.mark.skipif(
    os.environ.get("TAVOTTO_CODEX_NET_SMOKE") != "1",
    reason="联网冒烟需 TAVOTTO_CODEX_NET_SMOKE=1（nightly/手动）",
)
def test_real_codex_sparse_install_from_github(tmp_path):
    """README 教用户的那条 sparse 命令，对着真 GitHub 仓库跑一遍。"""
    home = tmp_path / "codex-home"
    home.mkdir()
    proc = _codex(
        [
            "plugin",
            "marketplace",
            "add",
            "Tavotto/Tavotto",
            "--sparse",
            ".agents/plugins",
            "--sparse",
            "codex-plugin",
        ],
        home,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = _codex(["plugin", "add", "tavotto@tavotto"], home, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = _codex(["plugin", "list"], home)
    assert proc.returncode == 0 and "tavotto" in proc.stdout, proc.stdout + proc.stderr


def test_readme_desktop_only_route_never_advertises_a_bare_tavotto_command():
    """P1（PR #118 评审）：桌面安装刻意不动 PATH，机器上没有叫 `tavotto` 的命令。

    桌面收尾那条路必须走插件的 handoff.py 定位器，并明说裸 `tavotto open`
    只在 PyPI 安装之后才存在。
    """
    zh = READMES["zh"].read_text(encoding="utf-8")
    en = READMES["en"].read_text(encoding="utf-8")
    assert "scripts/handoff.py" in zh and "scripts/handoff.py" in en
    assert "刻意不改你的 PATH" in zh
    assert "deliberately leave your `PATH` untouched" in en
    # 桌面收尾小节里不许再教用户跑裸命令
    zh_section = zh.split("### 只交给桌面版收尾")[1].split("###")[0]
    en_section = en.split("### Handing off to the desktop app only")[1].split("###")[0]
    for section in (zh_section, en_section):
        assert "tavotto open path/to/figure.py" not in section


def test_shipped_skill_docs_never_reference_repo_relative_docs():
    """P2（PR #118 评审）：sparse 安装与插件发行包里没有仓库的 docs/。

    随包分发的技能文档引用仓库文档一律用 GitHub URL，不用相对路径。
    """
    for path in [SKILL_DIR / "SKILL.md", *sorted((SKILL_DIR / "references").glob("*.md"))]:
        text = path.read_text(encoding="utf-8")
        assert "../../../docs/" not in text and "../../docs/" not in text, (
            f"{path.name} 引用了包外的仓库 docs/ 相对路径"
        )
