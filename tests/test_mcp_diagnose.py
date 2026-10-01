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
import os
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

#: **每个被 spawn 的假 CLI 都先钉住自己的 stdout**，形状照 `engine/cli.py::
#: use_utf8_streams()` 与 `scripts/ci/_common.py`（#284 已把它做成 scripts/ 的全仓门禁，
#: 而 tests/ 下的夹具不在那条覆盖里）。真 CLI 钉了、假 CLI 不钉，夹具就比它模拟的东西
#: **更容易失败**：Windows 的默认编码是 cp1252，子进程打一句中文当场 UnicodeEncodeError
#: 死掉，后面那行 JSON 根本没打出来，父进程拿到空输出——红的却是产品代码
#: （#314 的 Windows 腿实测；本机 `PYTHONIOENCODING=cp1252` 一模一样）。
PIN_UTF8 = (
    "import sys\n"
    "for _s in (sys.stdout, sys.stderr):\n"
    "    if hasattr(_s, 'reconfigure'):\n"
    "        try:\n"
    "            _s.reconfigure(encoding='utf-8', errors='replace')\n"
    "        except (OSError, ValueError):\n"
    "            pass\n"
)

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
        PIN_UTF8 + "import json, sys\n"
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
    silent.write_text(PIN_UTF8 + "sys.exit(2)\n", encoding="utf-8")
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
        PIN_UTF8 + "import json, sys\n"
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
        PIN_UTF8
        + "import json, sys\n"
        # 与另一条探针用例同一个形状：真 doctor 在那行 JSON 之前还会说中文，
        # 夹具照做才试得出「按 UTF-8 解码」与「从后往前找 JSON」这两件事
        + "print('体检发现 1 个问题')\n"
        + "print(json.dumps({'ok': True, 'version': '0.10.0'}))\n",
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


# ------------------- 引擎在、只是对不上：#721 的那一半 ------------------------
#: #721 的现场：pip 配了阿里云镜像，镜像上只有 0.15.0，`pipx install "tavotto[worker]"` 装到
#: 0.15.0；插件是本地市场装的（**没有** plugin-build.json，说不出下限），于是「太旧」判不出来，
#: 落回最宽的那一格 `desktop_only`——对着 pipx 用户说「装的是桌面版」。
OLD = "0.15.0"
ALIYUN = "https://mirrors.aliyun.com/pypi/simple/"
#: 体检里说出口的样子：只有协议与主机，路径整段抹掉（`_redact_url`）
ALIYUN_SAID = "https://mirrors.aliyun.com/***"


def _bin(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts" if sys.platform == "win32" else "bin")


def _py(venv_dir: Path) -> Path:
    return _bin(venv_dir) / ("python.exe" if sys.platform == "win32" else "python3")


@pytest.fixture()
def old_engine(tmp_path):
    """一个**真的**装着 tavotto 0.15.0 分发元数据、却 import 不到桥那组模块的 venv，外加
    它的 console script（shebang 指回这个 venv）——pipx 装旧版之后磁盘上就是这个形状。

    CLI 本身是哑的（`doctor --json` 什么都不说、退出 1）：版本必须从它背后的解释器问出来。
    """
    import venv

    env_dir = tmp_path / "pipx-venv"
    venv.EnvBuilder(with_pip=False).create(str(env_dir))
    py = _py(env_dir)
    purelib = subprocess.run(
        [str(py), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    dist = Path(purelib) / f"tavotto-{OLD}.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: tavotto\nVersion: {OLD}\n", encoding="utf-8"
    )
    cli = _bin(env_dir) / "tavotto"
    cli.write_text(f"#!{py}\nimport sys\nsys.exit(1)\n", encoding="utf-8")
    cli.chmod(0o755)
    # 前提：这个 venv 过不了桥的 import（不带 PYTHONPATH——测试进程的 src 不是它的）
    bare_env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    probe = subprocess.run(
        [str(py), "-c", launcher._BRIDGE_IMPORT], env=bare_env, capture_output=True
    )
    assert probe.returncode != 0, "前提：这个 venv 过不了桥的 import"
    return {"python": str(py), "cli": str(cli), "found": {"cmd": [str(cli)], "desktop": None}}


def _no_mirror(monkeypatch):
    monkeypatch.setattr(launcher, "pip_index", lambda environ=None: None)
    monkeypatch.setattr(launcher, "pip_index_of", lambda python, **kw: None)


def _mirror(monkeypatch):
    monkeypatch.setattr(
        launcher,
        "pip_index",
        lambda environ=None: {"url": ALIYUN, "source": "pip_config", "mirror": True},
    )
    monkeypatch.setattr(launcher, "pip_index_of", lambda python, **kw: None)


def test_an_old_pipx_engine_without_a_manifest_is_not_a_desktop_install(old_engine, monkeypatch):
    """没有清单（说不出下限）→ `engine_incompatible`：「不知道是不是太旧」是独立一档，
    既不折进 `engine_too_old`，更不落回 `desktop_only`。版本号照样说出口。"""
    monkeypatch.setattr(launcher, "required_tavotto_version", lambda: None)
    _no_mirror(monkeypatch)

    def never(*a, **kw):
        raise AssertionError("清单里没有下限就不该去问 CLI 的版本")

    monkeypatch.setattr(launcher, "_tavotto_cli_version", never)
    code, hint = launcher.diagnose_resolved(old_engine["found"], NOTHING_IMPORTABLE)

    assert code == "engine_incompatible", hint
    assert OLD in hint and "虚拟环境" in hint
    assert old_engine["python"] not in hint, "路径不出门：只报环境类别（Codex #724 P1）"
    assert "装的是 Tavotto 桌面版" not in hint
    assert "--provision" not in hint, "引擎已经在了，恢复方向是升级，不是旁边再建一个"
    steps = launcher._recovery_steps("engine_incompatible")
    assert any(s.startswith("升级引擎") for s in steps), steps
    assert not any("--provision" in s for s in steps), steps


def test_an_old_pipx_engine_is_too_old_even_when_its_cli_cannot_answer(old_engine, monkeypatch):
    """有清单（下限 0.17.0）、CLI 问不出版本：版本从 CLI 背后的解释器问出来 →
    `engine_too_old`，两个版本号 + 钉在下限上的 pipx 命令都说出口。"""
    monkeypatch.setattr(launcher, "required_tavotto_version", lambda: "0.17.0")
    _no_mirror(monkeypatch)
    code, hint = launcher.diagnose_resolved(old_engine["found"], NOTHING_IMPORTABLE)

    assert code == "engine_too_old", hint
    assert OLD in hint and "0.17.0" in hint
    assert 'pipx install --force "tavotto[worker]==0.17.0"' in hint
    assert "pipx upgrade tavotto" in hint
    assert "--index-url" not in hint, "没配镜像时不该出现绕开镜像的写法"


def test_a_mirror_turns_the_upgrade_into_a_pypi_pinned_command(old_engine, monkeypatch):
    """pip 指向镜像：说出镜像地址与它可能滞后，每条命令都绕开镜像；**不给**裸的
    `pipx upgrade tavotto`（它照样去问那个镜像，升不上去）。"""
    monkeypatch.setattr(launcher, "required_tavotto_version", lambda: "0.17.0")
    _mirror(monkeypatch)
    code, hint = launcher.diagnose_resolved(old_engine["found"], NOTHING_IMPORTABLE)

    assert code == "engine_too_old"
    assert ALIYUN in hint and "同步" in hint
    assert (
        'pipx install --force "tavotto[worker]==0.17.0" --index-url https://pypi.org/simple' in hint
    )
    assert "pipx upgrade tavotto" not in hint
    steps = launcher._recovery_steps("engine_too_old")
    assert any("--index-url https://pypi.org/simple" in s for s in steps), steps
    assert not any("pipx upgrade tavotto" in s for s in steps), steps


def test_a_frozen_desktop_cli_is_still_desktop_only(tmp_path, monkeypatch):
    """对照：桌面版的 frozen `tavotto-cli` 背后没有解释器——仍是 `desktop_only`，而且
    一个版本探测进程都不起（降级判定有时间预算）。"""
    frozen_dir = tmp_path / "Tavotto" / "bin"
    frozen_dir.mkdir(parents=True)
    frozen = frozen_dir / "tavotto-cli"
    frozen.write_bytes(b"\x7fELF" + b"\0" * 4096)
    monkeypatch.setattr(launcher, "required_tavotto_version", lambda: "0.17.0")
    monkeypatch.setattr(launcher, "_tavotto_cli_version", lambda cmd, **kw: None)

    def never(*a, **kw):
        raise AssertionError("frozen CLI 背后没有解释器，不该起版本探测")

    monkeypatch.setattr(launcher, "_dist_version", never)
    code, hint = launcher.diagnose_resolved(
        {"cmd": [str(frozen)], "desktop": str(frozen_dir)}, NOTHING_IMPORTABLE
    )
    assert code == "desktop_only"
    assert hint == launcher.DESKTOP_ONLY_HINT


def test_health_names_the_old_engine_end_to_end(old_engine, tmp_path):
    """真跑一次 `--health`（用那个 import 不到桥的旧 venv 当启动器解释器，发现环境清空），
    插件带清单（下限 0.17.0）+ pip 配了镜像：体检回 `engine_too_old`，带引擎版本、下限与
    镜像，且话术里是绕开镜像的命令。换成「没清单」则是 `engine_incompatible`。那个旧 venv 与 pipx 建的
    一样不带 pip：索引经 `PIPX_SHARED_LIBS` 里的共享 pip 问出来（#737）。"""
    from tavotto.engine import pluginmanifest

    plugin = tmp_path / "codex-plugin"
    shutil.copytree(PLUGIN, plugin, ignore=shutil.ignore_patterns("__pycache__"))
    pip_conf = tmp_path / "pip.conf"
    pip_conf.write_text(f"[global]\nindex-url = {ALIYUN}\n", encoding="utf-8")
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    env = {
        **os.environ,
        "PATH": str(empty),
        "HOME": str(tmp_path),
        "TAVOTTO_CONFIG_DIR": str(tmp_path / "config"),
        "LOCALAPPDATA": str(tmp_path / "lapp"),
        "PROGRAMFILES": str(tmp_path / "pf"),
        "TAVOTTO_CLI": old_engine["cli"],
        "PIP_CONFIG_FILE": str(pip_conf),
        "PIPX_SHARED_LIBS": str(_pipx_shared_libs(tmp_path / "pipx-shared")),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in (
        "TAVOTTO_MCP_PYTHON",
        "TAVOTTO_WORKER_PYTHON",
        "MM_WORKER_PYTHON",
        "TAVOTTO_MCP_EXECED",
        "PYTHONPATH",
        "PIP_INDEX_URL",
    ):
        env.pop(name, None)

    def health():
        proc = subprocess.run(
            [old_engine["python"], str(plugin / "mcp" / "server.py"), "--health"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=120,
        )
        assert proc.returncode == 3, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    (plugin / launcher.BUILD_MANIFEST).unlink(missing_ok=True)
    report = health()  # 没有清单：说不出下限
    assert report["code"] == "engine_incompatible", report
    assert report["engine_version"] == OLD
    assert report["min_tavotto_version"] is None
    # 找到的引擎只报环境类别、不报路径：话术与字段都进模型看得见的 tavotto_health（Codex #724 P1）
    assert old_engine["python"] not in report["error"], report["error"]
    assert "engine_python" not in report
    assert report["engine_where"] == "一个虚拟环境（venv）", report

    pluginmanifest.write_build_manifest(
        plugin,
        modes={},
        source_sha="0" * 40,
        fingerprint="f" * 16,
        lockfile_sha256=None,
        toolchain={},
        min_tavotto_version="0.17.0",
    )
    report = health()
    assert report["code"] == "engine_too_old", report
    assert report["engine_version"] == OLD and report["min_tavotto_version"] == "0.17.0"
    assert report["pip_index"] == {"url": ALIYUN_SAID, "source": "pip_config", "mirror": True}
    assert "--index-url https://pypi.org/simple" in report["error"]
    assert "--index-url https://pypi.org/simple" in " ".join(report["recovery"])


# ------------------------------ pip 的索引探测 --------------------------------
# #737 起直接问 pip（`python -m pip config list`），配置发现（平台位置、编码、覆盖顺序、PIP_CONFIG_FILE、
# site、商店版虚拟化）全归 pip 自己；这里的用例都起真的 pip。读法（按节筛 + 键名规范化）的判据在
# `tests/test_pip_config_pair.py`，与引擎对拍同一份向量。
def _pip_env(tmp_path: Path, **extra: str) -> dict:
    """真 pip 的子进程环境：继承本进程（Windows 要 SYSTEMROOT 等），去掉外面的 PIP_* 与用户目录。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PIP_", "PIPX_"))}
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        APPDATA=str(home / "AppData"),
        XDG_CONFIG_HOME=str(home / ".config"),
    )
    env.update(extra)
    return env


def _pip_conf(tmp_path: Path, body: str) -> str:
    conf = tmp_path / "pip.conf"
    conf.write_text(body, encoding="utf-8")
    return str(conf)


def test_pip_index_asks_pip_for_the_install_sections(tmp_path):
    """真 pip：`[install]` 压过 `[global]`，`[download]` 不作用于安装；`PIP_INDEX_URL` 压过配置文件；
    `PIP_CONFIG_FILE=os.devnull` 是 pip 约定的「一个配置文件都不读」→ 没配（None）。"""
    conf = _pip_conf(
        tmp_path,
        "[global]\nindex-url = https://pypi.org/simple\n"
        f"[install]\nindex-url = {ALIYUN}\n"
        "[download]\nindex-url = https://download-only.example/simple\n",
    )
    got = launcher.pip_index(_pip_env(tmp_path, PIP_CONFIG_FILE=conf))
    assert got == {"url": ALIYUN_SAID, "source": "pip_config", "mirror": True}

    got = launcher.pip_index(
        _pip_env(tmp_path, PIP_CONFIG_FILE=conf, PIP_INDEX_URL="https://pypi.org/simple")
    )
    assert got == {"url": "https://pypi.org/***", "source": "PIP_INDEX_URL", "mirror": False}

    assert launcher.pip_index(_pip_env(tmp_path, PIP_CONFIG_FILE=os.devnull)) is None


def test_pip_index_ignores_download_only_and_normalizes_keys(tmp_path):
    """只有 `[download] index-url` 时 pip install 仍走 PyPI → None；`--index-url` 写法照 pip 规范化后认得
    （#724 第 8 轮 Codex P2）。"""
    only_download = _pip_conf(tmp_path, f"[download]\nindex-url = {ALIYUN}\n")
    assert launcher.pip_index(_pip_env(tmp_path, PIP_CONFIG_FILE=only_download)) is None
    dashed = _pip_conf(tmp_path, f"[global]\n--index-url = {ALIYUN}\n")
    got = launcher.pip_index(_pip_env(tmp_path, PIP_CONFIG_FILE=dashed))
    assert got == {"url": ALIYUN_SAID, "source": "pip_config", "mirror": True}


def test_pip_index_never_repeats_credentials(tmp_path):
    got = launcher.pip_index(
        _pip_env(
            tmp_path,
            PIP_CONFIG_FILE=os.devnull,
            PIP_INDEX_URL="https://alice:s3cret@pypi.corp.example/simple",
        )
    )
    assert got["mirror"] is True
    assert "s3cret" not in got["url"] and "alice" not in got["url"]
    assert got["url"] == "https://***@pypi.corp.example/***"


def test_pip_index_never_repeats_query_credentials(tmp_path):
    """签名查询串也是凭据（Codex #724 P1）：`?token=…` 整段抹掉，片段去掉，地址本身照报。"""
    got = launcher.pip_index(
        _pip_env(
            tmp_path,
            PIP_CONFIG_FILE=os.devnull,
            PIP_INDEX_URL="https://mirror.example/simple?token=s3cret&sig=abc#frag",
        )
    )
    assert got["mirror"] is True
    assert "s3cret" not in got["url"] and "abc" not in got["url"] and "frag" not in got["url"]
    assert got["url"] == "https://mirror.example/***?***"
    assert launcher._redact_url("https://pypi.org/simple") == "https://pypi.org/***"


def test_pip_index_never_repeats_path_credentials():
    """私有索引常把令牌放在路径里，而路径段没有类型——令牌恰好叫 `api` 也分不出来。所以路径整段抹掉，
    只说协议与主机（Codex #724 P1 两轮）。"""
    for url in (
        "https://mirror.example/s3cret-t0ken/simple/",
        "https://mirror.example/api/simple",  # `api` 本身就是令牌
        "https://nexus.corp/repository/pypi-proxy/simple",
    ):
        said = launcher._redact_url(url)
        assert said == url.split("/", 3)[0] + "//" + url.split("/")[2] + "/***", said
    assert launcher._redact_url("https://mirror.example") == "https://mirror.example"
    assert launcher._redact_url(ALIYUN) == ALIYUN_SAID


def test_pip_index_never_reports_local_paths(tmp_path):
    """体检结果进模型看得见的 `tavotto_health`：`source` 只报类别，`file://` 索引的路径也抹掉（Codex #724 P1）。"""
    conf = _pip_conf(tmp_path, "[global]\nindex-url = file:///home/alice/wheels\n")
    got = launcher.pip_index(_pip_env(tmp_path, PIP_CONFIG_FILE=conf))
    assert got["source"] == "pip_config"
    assert "alice" not in json.dumps(got) and got["url"] == "file://***", got


def _venv_without_pip(where: Path) -> str:
    import venv

    venv.EnvBuilder(with_pip=False).create(str(where))
    return str(_py(where))


def _pipx_shared_libs(where: Path) -> Path:
    """pipx 共享库的形状：一个 venv，pip 在它的 site-packages 里看得见（这里用 .pth 指向测试进程的 pip）。"""
    import pip

    py = _venv_without_pip(where)
    purelib = subprocess.run(
        [py, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    (Path(purelib) / "test_shared_pip.pth").write_text(
        str(Path(pip.__file__).resolve().parents[1]) + "\n", encoding="utf-8"
    )
    return where


def test_duplicate_index_urls_in_one_section_ask_pip_which_one_wins(monkeypatch):
    """同一节的 index-url 来自好几个文件时，`pip config list` 把每一条都打印、按值排序（给人看的顺序，不是
    覆盖顺序）：谁生效再问一次 `pip config get`，不从行序推；`get` 也问不到就是不知道（#767 Codex P1）。"""
    listed = (
        f"global.index-url='{ALIYUN}'\n"  # 生效的那个按值排在前面
        "global.index-url='https://pypi.org/simple'\n"
    )
    asked: list = []

    def fake(argv, environ, timeout, *, answer_get=ALIYUN):
        asked.append(argv[argv.index("config") + 1 :])
        if argv[-2:] == ["config", "list"]:
            return listed
        return None if answer_get is None else answer_get + "\n"

    monkeypatch.setattr(launcher, "_run_pip_config_list", fake)
    got = launcher.pip_index_of("/env/bin/python", {})
    assert got == {"url": ALIYUN_SAID, "source": "pip_config", "mirror": True}, got
    assert asked == [["list"], ["get", "global.index-url"]]
    # `get` 问不到：不知道，不按行序挑一个
    monkeypatch.setattr(
        launcher,
        "_run_pip_config_list",
        lambda argv, environ, timeout: fake(argv, environ, timeout, answer_get=None),
    )
    assert launcher.pip_index_of("/env/bin/python", {}) == launcher.PIP_INDEX_UNKNOWN
    # 只有一个值时不多问
    asked.clear()
    monkeypatch.setattr(
        launcher,
        "_run_pip_config_list",
        lambda argv, environ, timeout: (asked.append(argv), listed.splitlines()[0])[1],
    )
    assert launcher.pip_index_of("/env/bin/python", {})["mirror"] is True
    assert len(asked) == 1


@pytest.mark.skipif(
    os.name == "nt",
    reason="旧 / 新两个用户配置位置是 POSIX 的形状（~/.pip 与 $XDG_CONFIG_HOME/pip）；逻辑由上一条覆盖",
)
def test_real_pip_with_a_legacy_and_a_current_user_config_reports_the_effective_one(tmp_path):
    """真 pip：旧位置 `~/.pip/pip.conf` 写 PyPI、新位置 `$XDG_CONFIG_HOME/pip/pip.conf` 写镜像——新位置生效，
    而 `pip config list` 两条都打印、生效的那条（按值排序）在前；只认最后一行会把它判成「没配镜像」。"""
    env = _pip_env(tmp_path)
    home = Path(env["HOME"])
    (home / ".pip").mkdir()
    (home / ".pip" / "pip.conf").write_text(
        "[global]\nindex-url = https://pypi.org/simple\n", "utf-8"
    )
    (home / ".config" / "pip").mkdir(parents=True)
    (home / ".config" / "pip" / "pip.conf").write_text(f"[global]\nindex-url = {ALIYUN}\n", "utf-8")
    listed = subprocess.run(
        [sys.executable, "-m", "pip", "config", "list"], env=env, capture_output=True, text=True
    ).stdout
    assert listed.count("global.index-url=") == 2, f"前提：两个位置都被 pip 读到并打印：{listed!r}"
    got = launcher.pip_index_of(sys.executable, env)
    assert got == {"url": ALIYUN_SAID, "source": "pip_config", "mirror": True}, (got, listed)


def test_a_pipx_venv_without_pip_is_asked_through_pipxs_shared_pip(tmp_path):
    """pipx 建的 venv 默认不带 pip：先试 pipx 共享库里的 pip（`--python` 按目标解释器求值，所以目标 venv 的
    site 配置——`pip config --site` 写的那份——照样读到）；共享库也没有就是**不知道**，不猜（#737）。"""
    pipx_home = tmp_path / "pipx"
    target = _venv_without_pip(pipx_home / "venvs" / "tavotto")
    site = pipx_home / "venvs" / "tavotto" / ("pip.ini" if os.name == "nt" else "pip.conf")
    site.write_text(f"[global]\nindex-url = {ALIYUN}\n", encoding="utf-8")
    env = _pip_env(tmp_path)

    assert launcher.pip_index_of(target, env) == launcher.PIP_INDEX_UNKNOWN, "前提：它自己没有 pip"

    _pipx_shared_libs(pipx_home / "shared")  # pipx 的目录形状：<PIPX_HOME>/venvs/<名> 旁边的 shared
    got = launcher.pip_index_of(target, env)
    assert got == {"url": ALIYUN_SAID, "source": "pip_config", "mirror": True}

    # 目录形状对不上时认 PIPX_SHARED_LIBS
    elsewhere = _venv_without_pip(tmp_path / "loose-venv")
    assert launcher.pip_index_of(elsewhere, env) == launcher.PIP_INDEX_UNKNOWN
    got = launcher.pip_index_of(
        elsewhere, {**env, "PIPX_SHARED_LIBS": str(pipx_home / "shared"), "PIP_INDEX_URL": ALIYUN}
    )
    assert got == {"url": ALIYUN_SAID, "source": "PIP_INDEX_URL", "mirror": True}


def test_every_pip_config_probe_is_read_only(monkeypatch, tmp_path):
    """两跳都是只读体检：起的解释器带 `-B`；`pip --python` 再起的目标解释器拿不到命令行，靠
    `PYTHONDONTWRITEBYTECODE=1` 管住（被探的解释器与 pipx 目录都不许留 .pyc，同 Codex #724 那条）。"""
    pipx_home = tmp_path / "pipx"
    target = pipx_home / "venvs" / "tavotto" / "bin" / "python"
    shared = pipx_home / "shared" / "bin" / "python"
    for p in (target, shared):
        p.parent.mkdir(parents=True)
        p.write_text("", encoding="utf-8")
    calls: list = []

    def run(argv, **kw):
        calls.append((argv, kw.get("env") or {}))
        return subprocess.CompletedProcess(argv, 1, b"", b"")

    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.pip_index_of(str(target), {}) == launcher.PIP_INDEX_UNKNOWN
    assert [a[0] for a, _env in calls] == [str(target), str(shared)], calls
    for argv, env in calls:
        assert argv[1] == "-B" and env.get("PYTHONDONTWRITEBYTECODE") == "1", (argv, env)


def test_an_unknown_index_is_said_not_guessed(monkeypatch):
    """问不到 pip：升级命令照常（不凭空加 `--index-url`），但话里说「不知道」并给出镜像滞后时的绕法；
    引擎那边问不到时用启动器这边问到的结论。"""
    unknown = dict(launcher.PIP_INDEX_UNKNOWN)
    assert launcher.upgrade_commands("0.17.0", unknown) == launcher.upgrade_commands("0.17.0", None)
    note = launcher.mirror_note("0.17.0", unknown)
    assert "问不到" in note and f"--index-url {launcher.PYPI_SIMPLE}" in note
    assert launcher.mirror_note("0.17.0", None) == ""

    mirror = {"url": ALIYUN_SAID, "source": "pip_config", "mirror": True}
    pypi = {"url": "https://pypi.org/***", "source": "pip_config", "mirror": False}
    for here, there, want in (
        (pypi, unknown, pypi),
        (None, unknown, None),
        (unknown, pypi, pypi),
        (pypi, mirror, mirror),
        (mirror, unknown, mirror),
        (unknown, unknown, unknown),
    ):
        monkeypatch.setattr(launcher, "pip_index", lambda environ=None, _h=here: _h)
        monkeypatch.setattr(launcher, "pip_index_of", lambda python, _t=there, **kw: _t)
        assert launcher.effective_pip_index("/engine/python") == want, (here, there)


def test_upgrade_commands_follow_the_mirror_verdict():
    plain = launcher.upgrade_commands("0.17.0", None)
    assert plain[0] == "pipx upgrade tavotto"
    assert 'pipx install --force "tavotto[worker]==0.17.0"' in plain
    # 同版本装残的引擎（engine_incompatible）也要真的重装：pip 那条带 --force-reinstall
    assert 'pip install -U --force-reinstall "tavotto[worker]==0.17.0"' in plain
    assert not any("--index-url" in c for c in plain)
    pypi = launcher.upgrade_commands("0.17.0", {"url": "https://pypi.org/simple", "mirror": False})
    assert pypi == plain
    mirrored = launcher.upgrade_commands("0.17.0", {"url": ALIYUN, "mirror": True})
    assert all(c.endswith("--index-url https://pypi.org/simple") for c in mirrored)
    assert not any(c.startswith("pipx upgrade") for c in mirrored)


# ------------------------ 以引擎那边的解释器为准（#721 真机） ------------------------
def test_the_engine_interpreters_view_of_pip_wins(monkeypatch):
    """启动器解释器 ≠ 装引擎的解释器时，以引擎背后那个解释器看到的 pip 配置为准：
    这边读到 PyPI，那边（真起一个子进程跑本文件的 `pip_index()`）读到镜像 → 报镜像。"""
    monkeypatch.setattr(
        launcher,
        "pip_index",
        lambda environ=None: {"url": "https://pypi.org/simple", "source": "x", "mirror": False},
    )
    monkeypatch.setenv("PIP_INDEX_URL", ALIYUN)  # 只有子进程里那份真的 pip_index 读得到
    there = launcher.pip_index_of(sys.executable)
    assert there == {"url": ALIYUN_SAID, "source": "PIP_INDEX_URL", "mirror": True}
    assert launcher.effective_pip_index(sys.executable)["mirror"] is True
    assert launcher.pip_index_of(None) is None
    assert launcher.effective_pip_index(None)["mirror"] is False


def test_diagnosis_takes_the_index_from_the_engines_interpreter(old_engine, monkeypatch, tmp_path):
    """接线：诊断的升级命令用的是引擎 venv 那边看到的镜像，即使启动器这边看到的是 PyPI。那个 venv
    与 pipx 建的一样不带 pip，经共享的 pip 问（#737）。"""
    monkeypatch.setattr(launcher, "required_tavotto_version", lambda: "0.16.0")
    monkeypatch.setattr(launcher, "pip_index", lambda environ=None: None)
    monkeypatch.setenv("PIP_INDEX_URL", ALIYUN)
    monkeypatch.setenv("PIPX_SHARED_LIBS", str(_pipx_shared_libs(tmp_path / "pipx-shared")))
    monkeypatch.delenv("PYTHONPATH", raising=False)
    code, hint = launcher.diagnose_resolved(old_engine["found"], NOTHING_IMPORTABLE)
    assert code == "engine_too_old"
    assert "--index-url https://pypi.org/simple" in hint and ALIYUN_SAID in hint


def test_the_degraded_payload_names_the_found_engine_without_its_path():
    """降级 server 的 `tavotto_health`：找到的引擎只报版本 + 环境类别，用户目录下的路径不出门（Codex #724 P1）。"""
    py = "/home/alice/.local/pipx/venvs/tavotto/bin/python"
    payload = launcher._degraded_payload(
        "engine_too_old",
        "hint",
        {"found_engine": {"python": py, "version": "0.15.0"}, "pip_index": None},
    )
    assert payload["engine"]["found"] == {"version": "0.15.0", "where": "pipx 的 tavotto 环境"}
    assert "alice" not in json.dumps(payload, ensure_ascii=False)


def test_a_healthy_resolution_asks_the_resolved_engines_pip(monkeypatch, tmp_path):
    """启动器解释器 import 不到、解析到另一个能用的引擎解释器（健康一路）：pip 索引也要问**那个**
    解释器（它 venv 里 `pip config --site` 配的镜像），与降级一路同一个 `effective_pip_index`（Codex #724）。"""
    other = str(tmp_path / "engine-venv" / "bin" / "python")
    asked: list = []
    monkeypatch.setattr(launcher, "_current_engine_ok", lambda: False)
    monkeypatch.setattr(
        launcher, "resolve", lambda found: {"python": other, "source": "env", "tried": []}
    )
    monkeypatch.setattr(launcher, "pip_index", lambda environ=None: None)

    def pip_index_of(python, **kw):
        asked.append(python)
        return {"url": ALIYUN, "source": "site", "mirror": True}

    monkeypatch.setattr(launcher, "pip_index_of", pip_index_of)
    report, _rc = launcher.health()
    assert report["mode"] == "engine" and report["python"] == other, report
    assert asked == [other], asked
    assert report["pip_index"]["mirror"] is True, report["pip_index"]


def test_version_numbers_never_touch_chinese_characters(old_engine, monkeypatch):
    """#721 真机看到「插件 0.17.0的桥」：话术里每个版本号两侧都要留空格。"""
    monkeypatch.setattr(launcher, "_plugin_version", lambda: "0.17.0")
    _mirror(monkeypatch)
    texts = [
        launcher.engine_incompatible_hint(
            {"python": "/p/python", "version": "0.15.0"}, plugin="0.17.0", index=None
        ),
        launcher.engine_too_old_hint("0.15.0", "0.16.0", plugin="0.17.0", index=None),
        launcher.mirror_note("0.16.0", {"url": ALIYUN, "source": "x", "mirror": True}),
        *launcher._recovery_steps("engine_incompatible"),
    ]
    glued = re.compile(r"[\u4e00-\u9fff]\d+\.\d+\.\d+|\d+\.\d+\.\d+[\u4e00-\u9fff]")
    for text in texts:
        assert not glued.search(text), text
    # 没有版本号时也不能多出空格
    bare = launcher.engine_incompatible_hint({"python": "/p", "version": "0.15.0"}, plugin=None)
    assert "这个插件的桥" in bare
