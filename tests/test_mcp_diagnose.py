"""MCP 诊断的第四态：引擎**装了**，但比插件要求的下限还旧（issue #285）。

`resolve()` 探的是**桥真正 import 的那组引擎模块**（`_BRIDGE_IMPORT`），0.12 及更早缺
其中五个，于是每个候选都 `importable=False`；而诊断的第一句只问「PATH / 安装位置上
有没有 tavotto」，于是 `pip install tavotto==0.10` 的用户被告知「这台机器上装的是
Tavotto 桌面版」——一句假话。原来的三态枚举的是**安装形态**，这里的失败轴却是
**版本**：一条正交的轴被折进了同一个枚举，只能落到最近的那个取值上。

**判据的主语**：`found["cmd"]` 那个 tavotto **自报**的版本（不是插件自己的版本，也不
是当前解释器里 import 得到的那个 tavotto），拿它跟**已装插件的构建清单**里那个
`min_tavotto_version` 比。三个输入缺一——清单没有、CLI 问不出、版本号解不出——都是
「不知道」，是独立一档，不许折进「太旧」。
"""

import ast
import importlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "codex-plugin"
sys.path.insert(0, str(PLUGIN / "mcp"))

launcher = importlib.import_module("server")

#: 「一个候选都没探通」的 resolver 结论——第四态与原来三态共同的前提
NOTHING_IMPORTABLE = {
    "python": None,
    "source": None,
    "tried": [
        {
            "python": "/usr/bin/python3",
            "source": "discovered",
            "exists": True,
            "importable": False,
            "ms": 7,
        },
        {
            "python": "/opt/py/bin/python3",
            "source": "worker_env",
            "exists": True,
            "importable": False,
            "ms": 9,
        },
    ],
}
#: pip 装的旧引擎在 PATH 上的样子：`shutil.which("tavotto")` 命中，仅此而已
PIP_INSTALLED = {"cmd": ["/usr/local/bin/tavotto"], "desktop": None}


@pytest.fixture()
def versions(monkeypatch):
    """把两个版本号钉住：清单要求的下限 + CLI 自报的版本。"""

    def pin(required, have):
        monkeypatch.setattr(launcher, "required_tavotto_version", lambda: required)
        monkeypatch.setattr(launcher, "_tavotto_cli_version", lambda cmd, **kw: have)

    return pin


# ------------------------------ 第四态本身 ---------------------------------
@pytest.mark.parametrize(
    ("have", "required"),
    [
        ("0.10.0", "0.13.0"),
        # 字符串序里 "0.9.0" > "0.10.0"，按字符串比会把这一格判反——两位数小版本
        # 正是本 issue 的现场，所以版本比较必须走 update_check.parse_version
        ("0.9.0", "0.10.0"),
    ],
)
def test_an_old_engine_is_not_reported_as_a_desktop_install(versions, have, required):
    """cmd 有 + 一个候选都没探通 + 版本低于下限 → `engine_too_old`，且说出两个版本号。"""
    versions(required, have)
    assert NOTHING_IMPORTABLE["python"] is None, "前提：resolver 一个都没探通"
    assert all(t["importable"] is False for t in NOTHING_IMPORTABLE["tried"])

    code, hint = launcher.diagnose_resolved(PIP_INSTALLED, NOTHING_IMPORTABLE)

    assert code == "engine_too_old"
    assert have in hint and required in hint, f"两个版本号必须都说出口：{hint}"
    assert "这台机器上装的是 Tavotto 桌面版" not in hint, "对着 pip 装的用户说他装了桌面版"
    # 恢复方向是升级引擎，不是在旁边再建一个环境
    assert "--provision" not in hint


@pytest.mark.parametrize("have", ["0.13.0", "0.14.0", "1.0.0"])
def test_a_good_enough_engine_never_reaches_the_new_state(versions, have):
    """版本**不低于**下限时行为一字不变：探不通另有原因，不许赖到版本头上。"""
    versions("0.13.0", have)
    code, hint = launcher.diagnose_resolved(PIP_INSTALLED, NOTHING_IMPORTABLE)
    assert code == "desktop_only"
    assert hint == launcher.DESKTOP_ONLY_HINT


def test_an_unknown_version_is_its_own_bucket(versions):
    """CLI 问不出版本（太老 / 起不来 / 输出不是 JSON）→ **不是** `engine_too_old`。

    说不出「你的是 X」就没有资格进这一格：把「不知道」折进来，新格立刻变成
    下一个万能兜底，而那正是这条 issue 的根因。
    """
    versions("0.13.0", None)
    code, _ = launcher.diagnose_resolved(PIP_INSTALLED, NOTHING_IMPORTABLE)
    assert code == "desktop_only"


def test_without_a_build_manifest_nothing_is_asked_of_the_cli(monkeypatch):
    """清单读不到（源码目录 / 旧形态的安装）也是「不知道」，而且**一个子进程都不起**。

    顺序：先读清单（一次本地文件读）再问 CLI。反过来的话，每一次降级判定都要多付一次
    进程启动——而降级判定是有时间预算的（tests/test_mcp_stdio.py）。
    """
    monkeypatch.setattr(launcher, "required_tavotto_version", lambda: None)

    def never(*a, **kw):
        raise AssertionError("清单里没有下限就不该去问 CLI 的版本")

    monkeypatch.setattr(launcher, "_tavotto_cli_version", never)
    code, _ = launcher.diagnose_resolved(PIP_INSTALLED, NOTHING_IMPORTABLE)
    assert code == "desktop_only"


def test_an_explicit_interpreter_still_wins_over_the_version_verdict(versions):
    """顺序的另一端：显式 `TAVOTTO_MCP_PYTHON` 用不了时，该修的是那个变量。

    先报版本会把他支去升级一个可能完全够用的引擎，而毛病在他自己设的那个变量上。
    """
    versions("0.13.0", "0.10.0")
    resolution = {
        "python": None,
        "source": None,
        "tried": [
            {"python": "/x/py", "source": "mcp_env", "exists": True, "importable": False, "ms": 1}
        ],
    }
    code, hint = launcher.diagnose_resolved(PIP_INSTALLED, resolution)
    assert code == "engine_unavailable"
    assert "TAVOTTO_MCP_PYTHON" in hint


def test_the_other_three_states_are_untouched(versions):
    """没有 cmd 的两格与第四态无关：它们连版本都问不着。"""
    versions("0.13.0", "0.10.0")
    code, _ = launcher.diagnose_resolved({"cmd": None, "desktop": "/x"}, NOTHING_IMPORTABLE)
    assert code == "desktop_found_cli_missing"
    code, _ = launcher.diagnose_resolved({"cmd": None, "desktop": None}, NOTHING_IMPORTABLE)
    assert code == "tavotto_missing"


# ------------------------------ 恢复步骤 -----------------------------------
def test_recovery_sends_this_user_to_upgrade_not_to_a_second_environment():
    """这一格的用户已经装了引擎，缺的是新版本；`--provision` 只会在旁边再建一个。"""
    steps = launcher._recovery_steps("engine_too_old")
    assert any("升级引擎" in s for s in steps), steps
    assert not any("--provision" in s for s in steps), steps
    # 对照：原来那几格的 `--provision` 一字未动
    assert any("--provision" in s for s in launcher._recovery_steps("desktop_only"))


# --------------------------- 下限版本的唯一出处 -----------------------------
def test_the_minimum_version_comes_from_the_build_manifest(tmp_path, monkeypatch):
    """写的一侧是引擎的 `pluginmanifest.write_build_manifest`，读的一侧是启动器。

    这条把两侧接起来：字段改名、文件改名都会当场红。启动器里**没有第二份**
    `MIN_TAVOTTO_VERSION`——那个常量住在 `scripts/make_plugin_manifest.py`，
    经构建写进 `plugin-build.json`，插件只是它的消费者。
    """
    from tavotto.engine import pluginmanifest

    assert launcher.BUILD_MANIFEST == pluginmanifest.BUILD_MANIFEST

    fake = tmp_path / "codex-plugin"
    (fake / ".codex-plugin").mkdir(parents=True)
    (fake / ".codex-plugin" / "plugin.json").write_text(
        (PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (fake / "mcp").mkdir()
    pluginmanifest.write_build_manifest(
        fake,
        modes={},
        source_sha="0" * 40,
        fingerprint="f" * 16,
        lockfile_sha256=None,
        toolchain={},
        min_tavotto_version="9.9.9",
    )
    monkeypatch.setattr(launcher, "HERE", str(fake / "mcp"))
    assert launcher.required_tavotto_version() == "9.9.9"


def test_a_broken_manifest_reads_as_unknown(tmp_path, monkeypatch):
    """清单坏了 / 没写 `min_tavotto_version` → None（不知道），不是某个默认下限。"""
    fake = tmp_path / "codex-plugin"
    (fake / "mcp").mkdir(parents=True)
    monkeypatch.setattr(launcher, "HERE", str(fake / "mcp"))
    assert launcher.required_tavotto_version() is None  # 清单根本不在

    (fake / launcher.BUILD_MANIFEST).write_text("{ 不是 JSON", encoding="utf-8")
    assert launcher.required_tavotto_version() is None

    (fake / launcher.BUILD_MANIFEST).write_text(json.dumps({"plugin": "tavotto"}), encoding="utf-8")
    assert launcher.required_tavotto_version() is None


def test_the_launcher_hardcodes_no_version_number():
    """启动器里不许出现第二份版本号——版本只从清单与 plugin.json 读。

    判源码结构用 AST：注释与文档里写 `0.8.0`（说明「这个子命令自哪一版就在」）是
    散文，不是常量；这条只看真的字符串常量。**主语是「Tavotto 版本号的形状」**——
    三段 semver（`pluginmanifest` 也是这么钉插件版本的），所以 JSON-RPC 那个
    `"2.0"` 不在其内：它是协议版本，不是产品版本。
    """
    tree = ast.parse((PLUGIN / "mcp" / "server.py").read_text(encoding="utf-8"))
    shaped = re.compile(r"v?\d+\.\d+\.\d+")
    hits = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and shaped.fullmatch(node.value.strip())
    ]
    assert not hits, f"启动器里写死了版本号：{hits}"


# ------------------------------ 版本探测本身 --------------------------------
def test_the_probe_asks_doctor_json_and_ignores_the_exit_code(tmp_path):
    """问的是 `tavotto doctor --json`（自 0.8.0 就在，纯标准库那一层、只读）。

    **退出码不作数**：`doctor` 发现问题时返回非 0，而那行 JSON 照样带着版本号。
    """
    log = tmp_path / "argv.json"
    fake = tmp_path / "fake_cli.py"
    fake.write_text(
        "import json, sys\n"
        f"open({str(log)!r}, 'w', encoding='utf-8').write(json.dumps(sys.argv[1:]))\n"
        "print('体检发现 1 个问题')\n"  # 那行 JSON 前面还可能有别的输出
        "print(json.dumps({'ok': False, 'version': '0.10.0'}, ensure_ascii=False))\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    assert launcher._tavotto_cli_version([sys.executable, str(fake)]) == "0.10.0"
    assert json.loads(log.read_text(encoding="utf-8")) == ["doctor", "--json"]


def test_the_probe_says_unknown_when_it_cannot_ask(tmp_path):
    """CLI 不存在 / 不认得这个子命令 / 输出里没有版本号 → None，不猜。"""
    assert launcher._tavotto_cli_version([str(tmp_path / "not-here")]) is None

    silent = tmp_path / "silent.py"
    silent.write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
    assert launcher._tavotto_cli_version([sys.executable, str(silent)]) is None


# --------------------- 那两个「插件自带模块」的两个前提 -----------------------
def test_the_version_comparison_ships_in_the_bundle():
    """启动器 import 的插件内模块，必须真的进发行件。

    `test_launcher_is_stdlib_only_and_parses` 的白名单里每多一个名字，就多一条
    「用户机器上得有这个文件」的隐性要求。发行件装什么由
    `plugin_stage.tracked_plugin_files()` 说了算——问它，别问工作区（工作区里有的
    文件未必进包，#289 的教训）。
    """
    from tests.support import pluginkit as kit

    stage = kit.load_script("plugin_stage")
    shipped = {rel for rel, _mode in stage.tracked_plugin_files(ROOT)}
    for rel in (
        "skills/tavotto-figure/scripts/handoff.py",  # 定位器
        "skills/tavotto-figure/scripts/update_check.py",  # 版本比较
    ):
        assert rel in shipped, f"启动器 import 了 {rel}，但它不在发行件里"


def _fresh_probe(plugin_dir, cli, snippet_tail):
    """在**全新解释器**里 import 启动器并跑一句，回 (returncode, stdout)。"""
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(plugin_dir / 'mcp')!r})\n"
        "import server\n" + snippet_tail
    )
    # encoding 必须钉死：这条探针的诊断信息全是中文，Windows 上退回系统代码页
    # 会让读线程当场死掉、stdout 变空，而 returncode 照样拿得到——断言失败时
    # 报错信息也跟着空了（tests/test_source_hygiene.py 那条门禁守的正是这个）。
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    return proc


def test_the_version_comparison_import_resolves_in_a_fresh_interpreter(tmp_path):
    """真跑一次：全新进程里 import 启动器、走到版本比较那一步，**不炸**。

    判据的主语是**运行时**，不是那张白名单：门禁绿了不等于用户机器上那句
    `import update_check` 解析得开——它与 `server.py` 不同目录，够得着全靠
    `_plugin_locator()` 往 sys.path 里插的那一行，而那一行正是 `diagnose()` 的第一句。
    门禁绿而运行时 ImportError 的话，用户拿到的是「MCP 一个工具都没有」。

    删掉那个文件的第二问回答另一半：够不着时**回「不知道」，不是崩**——这一句跑在
    降级路径上，异常逃出去就没有降级 server 了。
    """
    from tavotto.engine import pluginmanifest

    plugin = tmp_path / "codex-plugin"
    shutil.copytree(PLUGIN, plugin)
    pluginmanifest.write_build_manifest(
        plugin,
        modes={},
        source_sha="0" * 40,
        fingerprint="f" * 16,
        lockfile_sha256=None,
        toolchain={},
        min_tavotto_version="0.13.0",
    )
    cli = tmp_path / "fake_tavotto.py"
    cli.write_text(
        "import json, sys\nprint(json.dumps({'ok': True, 'version': '0.10.0'}))\n",
        encoding="utf-8",
    )
    tail = f"print(json.dumps(server.engine_too_old([{sys.executable!r}, {str(cli)!r}])))\n"

    proc = _fresh_probe(plugin, cli, tail)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == ["0.10.0", "0.13.0"], proc.stdout

    (plugin / "skills" / "tavotto-figure" / "scripts" / "update_check.py").unlink()
    proc = _fresh_probe(plugin, cli, tail)
    assert proc.returncode == 0, f"版本比较 import 不到就崩了：{proc.stderr}"
    assert json.loads(proc.stdout.strip()) is None, proc.stdout
