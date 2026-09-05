"""`tavotto codex install / doctor / uninstall`（ADR 0012）。

判据分两层：

* **确定性那层**用一个假 `codex`（临时目录里的一个小脚本，行为由环境变量驱动）。
  幂等、错误码、JSON 形状都在这一层钉死——它们不该依赖这台机器上装没装 Codex，
  更不该依赖网络。
* **真 CLI 那层**要网络与真实 marketplace，按 ADR 的口径留成显式 opt-in
  （`TAVOTTO_CODEX_REAL_SMOKE=1`），默认 skip。从没跑过的门禁不会保持正确，
  但把网络写进快线只会让它天天红。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

#: 假 codex：`plugin list` / `marketplace list` 的输出由 STATE 文件决定，
#: `add` / `remove` 改写它。这样「装过没有」是**可观察的真状态**，不是打桩。
FAKE_CODEX = """\
import json, os, sys
state = os.environ["FAKE_CODEX_STATE"]
def load():
    try:
        return json.load(open(state))
    except Exception:
        return {"marketplace": False, "plugin": False}
def save(d):
    json.dump(d, open(state, "w"))
def log(argv):
    with open(os.environ["FAKE_CODEX_LOG"], "a") as f:
        f.write(" ".join(argv) + "\\n")

argv = sys.argv[1:]
log(argv)
d = load()
want_json = "--json" in argv
argv = [a for a in argv if a != "--json"]
if os.environ.get("FAKE_CODEX_FAIL") == " ".join(argv[:3]):
    print("boom", file=sys.stderr); sys.exit(3)
if want_json and os.environ.get("FAKE_CODEX_TEXT_ONLY"):
    # 老客户端：不认识 --json
    print("error: unexpected argument '--json'", file=sys.stderr); sys.exit(2)
mk_root = os.environ.get("FAKE_CODEX_MK_ROOT", "/home/u/.codex/.tmp/marketplaces/tavotto")
plugin_path = os.environ.get("FAKE_CODEX_PLUGIN_PATH") or (
    "https://github.com/Tavotto/Tavotto.git, path `codex-plugin`, ref `plugin-stable`")
version = os.environ.get("FAKE_CODEX_VERSION", "0.12.0")
listed = d["marketplace"] and not os.environ.get("FAKE_CODEX_ENTRY_ABSENT")
entry = {
    "pluginId": "tavotto@tavotto", "name": "tavotto", "marketplaceName": "tavotto",
    "version": version or None, "installed": bool(d["plugin"]), "enabled": bool(d["plugin"]),
    "source": {"source": "git-subdir", "url": "https://github.com/Tavotto/Tavotto.git",
               "path": "./codex-plugin", "ref": "plugin-stable"},
    "marketplaceSource": {"sourceType": "git", "source": "https://github.com/Tavotto/Tavotto.git"},
}
if argv[:3] == ["plugin", "marketplace", "list"]:
    if want_json:
        # 真 CLI 0.151 的形状：pretty-printed 多行 JSON
        mks = [{"name": "personal", "root": "/home/u/tavotto-notes",
                "marketplaceSource": {"sourceType": "local", "source": "/home/u/tavotto-notes"}}]
        if d["marketplace"]:
            mks.append({"name": "tavotto", "root": mk_root,
                        "marketplaceSource": {"sourceType": "git",
                                              "source": "https://github.com/Tavotto/Tavotto.git"}})
        print(json.dumps({"marketplaces": mks}, indent=2))
    else:
        # 真 CLI 的形状：MARKETPLACE / ROOT 两列。ROOT 里带 tavotto 是常事
        # （用户目录、缓存路径），子串判据会在这里翻车
        print("MARKETPLACE  ROOT")
        print("personal     /home/u/tavotto-notes")
        if d["marketplace"]:
            print("tavotto      " + mk_root)
elif argv[:3] == ["plugin", "marketplace", "add"]:
    d["marketplace"] = True; save(d); print("added")
elif argv[:3] == ["plugin", "marketplace", "remove"]:
    if "/" in argv[3]:
        print("invalid marketplace name: " + argv[3], file=sys.stderr); sys.exit(2)
    d["marketplace"] = False; save(d); print("removed")
elif argv[:2] == ["plugin", "list"]:
    # **marketplace 加好之后插件照样会被列出来**，只是 STATUS 是 not installed
    if want_json:
        out = {"installed": [], "available": []}
        if listed:
            out["installed" if d["plugin"] else "available"].append(entry)
        print(json.dumps(out, indent=2))
    else:
        print("Marketplace `tavotto`")
        print(mk_root + "/.agents/plugins/marketplace.json")
        print("")
        print("PLUGIN           STATUS              VERSION  PATH")
        if listed:
            st = "installed, enabled" if d["plugin"] else "not installed  "
            print("tavotto@tavotto  " + st + "  " + (version or "-").ljust(7) + "  " + plugin_path)
elif argv[:2] == ["plugin", "add"]:
    d["plugin"] = True; save(d); print("added")
elif argv[:2] == ["plugin", "remove"]:
    d["plugin"] = False; save(d); print("removed")
else:
    print("unknown: " + " ".join(argv), file=sys.stderr); sys.exit(2)
"""


def _shim(bindir: Path, name: str, body_posix: str, body_nt: str) -> Path:
    """在 `bindir` 里放一个**真能执行**的小程序（POSIX 一个 sh 脚本，Windows 一个 .cmd）。

    这些用例判的是「跑起来会怎样」，所以不能用 `monkeypatch` 把 `shutil.which`
    打成想要的答案——那样量的还是「PATH 里有没有」，正是 issue #172 里答错的那个问题。
    """
    bindir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        exe = bindir / f"{name}.cmd"
        exe.write_text("@echo off\r\n" + body_nt + "\r\n", encoding="utf-8")
    else:
        exe = bindir / name
        exe.write_text("#!/bin/sh\n" + body_posix + "\n", encoding="utf-8")
        exe.chmod(0o755)
    return exe


def _real_python_shim(bindir: Path, name: str) -> Path:
    """一个换了名字的真 Python。"""
    return _shim(bindir, name, f'exec "{sys.executable}" "$@"', f'"{sys.executable}" %*')


def _store_alias_shim(bindir: Path, name: str) -> Path:
    """商店别名那个形状：**命令存在**、零输出、退出码非零。

    模拟的是它可观测的行为（用户报告里的那三件事），不是别名机制本身——被测的
    判据只看这三件事。`exit 9009` 在 POSIX 上会被截成 8 位（49），无所谓：判据
    从不读那个数字，读了就成了挑平台的判据。
    """
    return _shim(bindir, name, "exit 9009", "exit /b 9009")


def _complete_fake_plugin(plugin_dir: Path) -> None:
    """把假插件补成「形状完整」：doctor 的画布步按 REQUIRED 逐条点名，少一个都红。

    画布用 pluginkit 的假单文件（有指纹戳、有 root、过体量下限）；其余文件只要在。
    """
    from tests.support import pluginkit

    pluginkit.write_fake_widget(plugin_dir / "mcp" / "widget" / "canvas.html")
    for rel in (
        "mcp/tavotto_mcp/server.py",
        "mcp/tavotto_mcp/widget.py",
        "skills/tavotto-figure/SKILL.md",
        "skills/tavotto-figure/scripts/handoff.py",
        "assets/tavotto.svg",
    ):
        p = plugin_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text("# placeholder\n", encoding="utf-8")


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    """一个假 codex + 一个假 CODEX_HOME（里面预置一份已装插件的落点）。"""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = tmp_path / "fake_codex.py"
    script.write_text(FAKE_CODEX, encoding="utf-8")
    if os.name == "nt":
        exe = bindir / "codex.cmd"
        exe.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = bindir / "codex"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)

    codex_home = tmp_path / "codexhome"
    # 真 CLI 的缓存形状：plugins/cache/<marketplace>/<plugin>/<version>
    plugin_dir = codex_home / "plugins" / "cache" / "tavotto" / "tavotto" / "0.12.0"
    (plugin_dir / ".codex-plugin").mkdir(parents=True)
    (plugin_dir / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "tavotto", "version": "0.12.0"}), encoding="utf-8"
    )
    (plugin_dir / "mcp").mkdir()
    # 假 server：--health 回一行 JSON，--provision 直接成功
    (plugin_dir / "mcp" / "server.py").write_text(
        'import sys\nprint(\'{"ok": true, "engine_version": "0.13.0"}\')\nsys.exit(0)\n',
        encoding="utf-8",
    )
    _complete_fake_plugin(plugin_dir)
    # marketplace 快照：插件条目已经是发行通道（git-subdir → plugin-stable）
    mk_root = tmp_path / "mk-snapshot"
    (mk_root / ".agents" / "plugins").mkdir(parents=True)
    (mk_root / ".agents" / "plugins" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "tavotto",
                "plugins": [
                    {
                        "name": "tavotto",
                        "source": {
                            "source": "git-subdir",
                            "url": "https://github.com/Tavotto/Tavotto.git",
                            "path": "./codex-plugin",
                            "ref": "plugin-stable",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_CODEX_MK_ROOT", str(mk_root))
    # 已装副本的两份清单（严格同源对：command 必须一致）
    (plugin_dir / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"tavotto": {"command": "python3", "args": ["./mcp/server.py"]}}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    agents = plugin_dir / "skills" / "tavotto-figure" / "agents"
    agents.mkdir(parents=True)
    (agents / "openai.yaml").write_text(
        "interface:\n"
        '  display_name: "Tavotto Figure"\n'
        "dependencies:\n"
        "  tools:\n"
        "    - type: mcp\n"
        "      value: tavotto\n"
        "      transport: stdio\n"
        "      command: python3\n"
        "policy:\n"
        "  allow_implicit_invocation: true\n",
        encoding="utf-8",
    )

    # PATH 上先摆一个**确定能跑**的 python3：`.mcp.json` 里钉的就是这个名字，
    # 不摆的话这些用例的结论取决于跑它的那台机器上 python3 是什么（Windows 上
    # 很可能就是商店别名）。要测「起不来」的用例自己再往前插一个。
    _real_python_shim(bindir, "python3")
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("FAKE_CODEX_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "calls.log"))
    return {"log": tmp_path / "calls.log", "home": codex_home, "plugin": plugin_dir}


def _run(argv: list[str], env_extra: dict | None = None) -> tuple[int, str, str]:
    env = {**os.environ, "PYTHONPATH": str(SRC), **(env_extra or {})}
    p = subprocess.run(
        [sys.executable, "-m", "tavotto.cli_entry", *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
    )
    return p.returncode, p.stdout, p.stderr


# --------------------------- 单一权威 ---------------------------
def test_readme_and_cli_use_the_same_command():
    """README 首用章节里的两条命令必须由 `brand.py` 的常量拼得出来。

    两处手写就会漂，而漂了之后的症状是「照文档做装不上」——用户没法自己发现
    是哪一边错。这条看的是**字面量同源**，不是「差不多」。
    """
    sys.path.insert(0, str(SRC))
    from tavotto.engine import brand

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    lines = [ln.strip() for ln in readme.splitlines()]

    # **整行相等，不是「包含」。** 子串匹配是一道空门禁：把 sparse 路径从两个
    # 删成一个，拼出来的短命令仍然是 README 那行的子串，判据照样绿（本用例第一版
    # 就是这么写的，变异当场抓住）。
    expected_market = " ".join(
        [
            "codex plugin marketplace add",
            brand.CODEX_MARKETPLACE,
            *[f"--sparse {p}" for p in brand.CODEX_SPARSE_PATHS],
        ]
    )
    market_lines = [ln for ln in lines if ln.startswith("codex plugin marketplace add")]
    assert market_lines, "README 首用章节里没有 marketplace add 那行了"
    assert market_lines == [expected_market], (
        f"README 与 brand.py 漂开了：\nREADME  {market_lines}\nbrand   [{expected_market}]"
    )

    expected_add = f"codex plugin add {brand.CODEX_PLUGIN_REF}"
    add_lines = [ln for ln in lines if ln.startswith("codex plugin add")]
    assert add_lines == [expected_add], (
        f"README 与 brand.py 漂开了：\nREADME  {add_lines}\nbrand   [{expected_add}]"
    )


def test_marketplace_name_matches_the_manifest():
    """`marketplace remove` 收的是**配置后的名字**，不是 `owner/repo`。

    给它源会被直接拒（`/` 不是合法名），症状是「插件删掉了、marketplace 永远留着」。
    名字的唯一出处是 `.agents/plugins/marketplace.json` 的 `name`。
    """
    sys.path.insert(0, str(SRC))
    from tavotto.engine import brand

    manifest = json.loads(
        (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )
    assert brand.CODEX_MARKETPLACE_NAME == manifest["name"]
    assert "/" not in brand.CODEX_MARKETPLACE_NAME
    assert brand.CODEX_PLUGIN_REF == f"{manifest['plugins'][0]['name']}@{manifest['name']}"


def test_a_listed_but_uninstalled_plugin_is_not_mistaken_for_installed(fake_codex):
    """marketplace 加好、插件还没装时 `plugin list` **照样列出它**（STATUS 是 not
    installed）。拿「输出里有没有 tavotto」当判据，全新安装会被判成已装而跳过
    `plugin add`——主流程反而走不通（Codex 在 PR #169 上指出，真 CLI 复核属实）。"""
    # 先只把 marketplace 加上，插件仍未装
    rc, out, _ = _run(["codex", "doctor", "--json"])
    assert rc == 1
    assert json.loads(out.strip().splitlines()[-1])["error_code"] == "marketplace_add_failed"

    rc, out, err = _run(["codex", "install", "--json"])
    assert rc == 0, err
    steps = {s["step"]: s for s in json.loads(out.strip().splitlines()[-1])["steps"]}
    assert steps["plugin"]["skipped"] is False, "全新安装被判成「已装」，plugin add 被跳过了"
    calls = fake_codex["log"].read_text(encoding="utf-8")
    assert "plugin add" in calls


def test_uninstall_passes_the_configured_name_not_the_source(fake_codex):
    """假 codex 照真 CLI 的行为拒绝带 `/` 的名字——传错就红。"""
    assert _run(["codex", "install", "--json"])[0] == 0
    rc, out, err = _run(["codex", "uninstall", "--json"])
    assert rc == 0, out + err
    calls = fake_codex["log"].read_text(encoding="utf-8")
    assert "marketplace remove tavotto" in calls
    assert "marketplace remove Tavotto/Tavotto" not in calls


def test_frozen_cli_does_not_use_itself_as_the_interpreter(monkeypatch, tmp_path):
    """桌面版的 `tavotto-cli` 是 PyInstaller 冻结产物，**不能当解释器用**。

    把它当 python 使只会被 `packaging/entry.py` 当成 Tavotto 的命令行参数解析掉，
    插件脚本根本不会跑（Codex 在 PR #169 上指出）。

    退回 PATH 时**候选要跑过才算数**：`shutil.which("python3")` 在 Windows 上会
    对商店别名答「有」，而那玩意儿启动起来是零输出 + 9009（issue #172）。所以这里
    摆的是真程序，不是打过桩的 which。
    """
    sys.path.insert(0, str(SRC))
    from tavotto.engine import codexinstall

    monkeypatch.delenv("TAVOTTO_MCP_PYTHON", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    bindir = tmp_path / "bin"
    _store_alias_shim(bindir, "python3")  # 存在，但起不来
    real = _real_python_shim(bindir, "python")
    monkeypatch.setenv("PATH", str(bindir))
    # Windows 上 `shutil.which` 回的是 PATHEXT 那一份的大写扩展名（`python.CMD`），
    # 路径比较必须走 normcase——否则这条判据在真 Windows 上红在大小写上。
    assert os.path.normcase(codexinstall.plugin_python()) == os.path.normcase(str(real)), (
        "把「PATH 里有」当成「能跑」了"
    )

    # PATH 上只剩那个起不来的：说清楚，别装作能跑
    real.unlink()
    assert codexinstall.plugin_python() is None
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert codexinstall.plugin_python() == sys.executable


# --------------------------- 分派与依赖 ---------------------------
def test_codex_subcommand_runs_without_flask_or_pymupdf(tmp_path):
    """三个子命令跑在**没装 Flask/PyMuPDF** 的解释器里也要能用。

    与 `test_subcommands_run_without_flask_or_pymupdf` 同一条纪律：装在用户机器上
    的 `tavotto-cli` 每次都要付冷启动，而它一个 HTTP 端点都用不上。
    """
    blocker = tmp_path / "blocker"
    blocker.mkdir()
    for name in ("flask", "fitz", "pymupdf"):
        (blocker / f"{name}.py").write_text(
            "raise ImportError('本用例故意挡住它')", encoding="utf-8"
        )
    rc, out, err = _run(["codex", "--help"], {"PYTHONPATH": f"{blocker}{os.pathsep}{SRC}"})
    assert rc == 0, err
    assert "install" in out and "doctor" in out and "uninstall" in out


# --------------------------- 失败也是一行 JSON ---------------------------
def test_missing_codex_cli_reports_a_stable_code_and_where_it_looked(capsys, monkeypatch):
    """进程内验，不用子进程：这台机器上 `/opt/homebrew/bin` 里就有真的 codex，
    而**那正是探测该找的地方**——靠清空 PATH 造不出「找不到」的现场，只会把
    「探测范围比 PATH 宽」这条正确行为误当成 bug。"""
    sys.path.insert(0, str(SRC))
    from tavotto.engine import codexinstall

    monkeypatch.setattr(codexinstall, "find_codex", lambda: (None, ["PATH", "/nowhere/bin"]))
    rc = codexinstall.cli(["doctor", "--json"])
    assert rc == 1
    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["ok"] is False
    assert data["error_code"] == "codex_cli_missing"
    # 「找不到」三个字帮不上忙：找过哪些位置要如实报出来
    assert "PATH" in data["error"] and "/nowhere/bin" in data["error"]
    assert data["steps"][0]["step"] == "codex_cli"
    # 也不该顺手代装
    assert "本命令不代装" in data["error"]


def test_find_codex_looks_beyond_path_and_says_where(monkeypatch, tmp_path):
    """PATH 里没有时还要找几个常见位置，并把找过的位置如实报出来。"""
    sys.path.insert(0, str(SRC))
    from tavotto.engine import codexinstall

    home = tmp_path / "home"
    (home / ".codex" / "bin").mkdir(parents=True)
    exe = home / ".codex" / "bin" / ("codex.cmd" if os.name == "nt" else "codex")
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(codexinstall.shutil, "which", lambda _n: None)
    monkeypatch.setattr(codexinstall.Path, "home", staticmethod(lambda: home))

    found, searched = codexinstall.find_codex()
    assert found == str(exe)
    assert searched[0] == "PATH" and str(home / ".codex" / "bin") in searched


# --------------------------- install 的幂等 ---------------------------
def test_install_is_idempotent_and_the_second_run_says_so(fake_codex):
    first_rc, first_out, first_err = _run(["codex", "install", "--json"])
    assert first_rc == 0, first_err
    first = json.loads(first_out.strip().splitlines()[-1])
    by_step = {s["step"]: s for s in first["steps"]}
    assert by_step["marketplace"]["skipped"] is False
    assert by_step["plugin"]["skipped"] is False

    second_rc, second_out, second_err = _run(["codex", "install", "--json"])
    assert second_rc == 0, second_err
    second = json.loads(second_out.strip().splitlines()[-1])
    again = {s["step"]: s for s in second["steps"]}
    # **重跑必须看得出「什么都没做」**——只看 ok 的话，装了一遍和跳过一遍长得一样
    assert again["marketplace"]["skipped"] is True
    assert again["plugin"]["skipped"] is True

    calls = fake_codex["log"].read_text(encoding="utf-8")
    assert calls.count("plugin add") == 1, "第二次又装了一遍：不幂等"
    assert calls.count("plugin marketplace add") == 1


def test_doctor_diagnoses_without_changing_anything(fake_codex):
    rc, out, err = _run(["codex", "doctor", "--json"])
    assert rc == 1, "什么都没装的时候 doctor 该报有问题"
    data = json.loads(out.strip().splitlines()[-1])
    assert data["error_code"] == "marketplace_add_failed"
    calls = fake_codex["log"].read_text(encoding="utf-8")
    assert "add" not in calls, "doctor 只诊断不改动，却调了 add"


def test_a_failing_step_stops_the_pipeline_and_names_itself(fake_codex):
    rc, out, err = _run(
        ["codex", "install", "--json"], {"FAKE_CODEX_FAIL": "plugin marketplace add"}
    )
    assert rc == 1
    data = json.loads(out.strip().splitlines()[-1])
    assert data["error_code"] == "marketplace_add_failed"
    # 失败之后不该继续往下走：后面的步骤压根不该出现
    assert [s["step"] for s in data["steps"]] == ["codex_cli", "marketplace"]


def test_uninstall_removes_both_and_does_not_touch_the_engine(fake_codex):
    assert _run(["codex", "install", "--json"])[0] == 0
    rc, out, err = _run(["codex", "uninstall", "--json"])
    assert rc == 0, err
    data = json.loads(out.strip().splitlines()[-1])
    steps = {s["step"]: s for s in data["steps"]}
    assert steps["plugin"]["ok"] and steps["marketplace"]["ok"]
    calls = fake_codex["log"].read_text(encoding="utf-8")
    assert "plugin remove" in calls and "marketplace remove" in calls
    # 引擎不归它管：卸载不许碰
    assert "provision" not in calls
    assert "engine" not in steps

    # 再卸一次：两步都该是 skipped
    rc, out, _ = _run(["codex", "uninstall", "--json"])
    assert rc == 0
    again = {s["step"]: s for s in json.loads(out.strip().splitlines()[-1])["steps"]}
    assert again["plugin"]["skipped"] and again["marketplace"]["skipped"]


def test_success_only_tells_the_user_to_open_a_new_session(fake_codex):
    """收尾只说一句。**不试图在旧会话里验证工具**——那验不出来，
    而一句「已启用」会让用户以为当场就能用（首次使用体验的原始教训）。"""
    rc, out, err = _run(["codex", "install"])
    assert rc == 0, err
    assert "新开一个 Codex 会话" in out
    assert "已启用" not in out


# ------------------- 启动命令：能不能跑，不是 PATH 里有没有 -------------------
# issue #172：`.mcp.json` 里钉死的 `python3` 在 Windows 上往往是微软商店的
# App Execution Alias——命令**存在**、退出码 9009、Codex 里一个工具都没有（连降级
# server 都起不来，也就没人能说话）。Codex 的 `.mcp.json` 没有按平台分支的字段、
# 没有候选链、`command` 也不过 shell，所以只能在安装时把已装副本换成一条真能跑的。


def _break_the_mcp_command(plugin_dir: Path, tmp_path) -> Path:
    """把已装副本的启动命令换成一个**起不来的绝对路径**。

    早先这里是往 PATH 前面插一个叫 `python3` 的 shim。真 Windows 上那招不成立：
    `subprocess` 走 CreateProcess，PATH 搜索**只补 `.exe`**，看不见 `.cmd`——名字
    照样解析到机器上真的 `python3.exe`，判据于是绿在了错的理由上（#256 的
    windows-latest 腿）。给全路径就没这个问题（日志里 `codex.CMD` 就是这么跑起来的）。
    """
    broken = _store_alias_shim(tmp_path / "storebin", "python3")
    data = json.loads((plugin_dir / ".mcp.json").read_text(encoding="utf-8"))
    next(iter(data["mcpServers"].values()))["command"] = str(broken)
    (plugin_dir / ".mcp.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return broken


def _mcp_command(plugin_dir: Path) -> str:
    data = json.loads((plugin_dir / ".mcp.json").read_text(encoding="utf-8"))
    return next(iter(data["mcpServers"].values()))["command"]


def _yaml_command(plugin_dir: Path) -> str:
    text = (plugin_dir / "skills" / "tavotto-figure" / "agents" / "openai.yaml").read_text(
        encoding="utf-8"
    )
    line = [ln for ln in text.splitlines() if ln.strip().startswith("command:")]
    assert len(line) == 1, f"openai.yaml 里的 command 行不是一条：{line}"
    return line[0].split(":", 1)[1].strip()


def test_install_pins_a_runnable_interpreter_when_the_command_cannot_start_the_launcher(
    fake_codex, tmp_path
):
    """启动命令起不来（存在、零输出、非零码）时，安装要把它换掉。

    两侧一起换：`.mcp.json` 的 `command` 与 `openai.yaml` 的
    `dependencies.tools[].command` 是根 AGENTS.md 的严格同源对（stdio 依赖按
    command 做规范键匹配），只换一侧的话技能声明的依赖对不上插件自带的 server。
    """
    broken = _break_the_mcp_command(fake_codex["plugin"], tmp_path)
    assert _mcp_command(fake_codex["plugin"]) == str(broken)

    rc, out, err = _run(["codex", "install", "--json"])
    assert rc == 0, err
    step = {s["step"]: s for s in json.loads(out.strip().splitlines()[-1])["steps"]}["interpreter"]
    assert step["ok"] and step["skipped"] is False, step

    plugin = fake_codex["plugin"]
    assert _mcp_command(plugin) != str(broken), "起不来的命令还留在 .mcp.json 里"
    assert _mcp_command(plugin) == sys.executable
    assert _yaml_command(plugin) == sys.executable, "同源对只改了一侧"

    # 幂等：换过之后再跑一次，什么都不该做
    rc, out, err = _run(["codex", "install", "--json"])
    assert rc == 0, err
    again = {s["step"]: s for s in json.loads(out.strip().splitlines()[-1])["steps"]}["interpreter"]
    assert again["skipped"] is True, "已经能跑了还再钉一次"


def test_the_pinned_interpreter_comes_from_the_plugins_own_resolver(fake_codex, tmp_path):
    """挑哪个解释器由**插件的 resolver** 说了算，安装器不自己再挑一遍。

    `mcp/server.py` 的候选链（显式覆盖 → worker 环境 → 自管 runtime → 从 CLI
    反推 → PATH）是唯一权威；安装器抄第二份的话，两边会各修一次同一个格子。
    """
    plugin = fake_codex["plugin"]
    _break_the_mcp_command(plugin, tmp_path)
    resolved = _real_python_shim(tmp_path / "resolved", "python-resolved")
    (plugin / "mcp" / "server.py").write_text(
        "import json,sys\n"
        f"print(json.dumps({{'ok': True, 'python': {str(resolved)!r}}}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    assert _run(["codex", "install", "--json"])[0] == 0
    assert _mcp_command(plugin) == str(resolved), "没用体检报出来的那个解释器"
    assert _yaml_command(plugin) == str(resolved)


def test_doctor_reports_the_unusable_command_without_writing_anything(fake_codex, tmp_path):
    """doctor 只诊断：报出「是解释器的问题」和下一步，一个字节都不改。"""
    assert _run(["codex", "install", "--json"])[0] == 0
    plugin = fake_codex["plugin"]
    assert _mcp_command(plugin) == "python3", "夹具里的 python3 本来是能跑的"
    _break_the_mcp_command(plugin, tmp_path)
    before = (plugin / ".mcp.json").read_bytes()
    before_yaml = (plugin / "skills" / "tavotto-figure" / "agents" / "openai.yaml").read_bytes()

    rc, out, err = _run(["codex", "doctor", "--json"])
    assert rc == 1, out
    data = json.loads(out.strip().splitlines()[-1])
    assert data["error_code"] == "interpreter_unusable"
    assert "9009" in data["error"], "没说清是解释器/别名的问题，用户看不出下一步"
    assert "tavotto codex install" in data["error"], "没给可执行的下一步"
    assert (plugin / ".mcp.json").read_bytes() == before, "doctor 改了 .mcp.json"
    assert (
        plugin / "skills" / "tavotto-figure" / "agents" / "openai.yaml"
    ).read_bytes() == before_yaml


def test_no_usable_interpreter_says_so_instead_of_pinning_something_broken(fake_codex, tmp_path):
    """一个能跑的都找不到时报稳定 code，**不许随便钉一个**。"""
    plugin = fake_codex["plugin"]
    broken = _break_the_mcp_command(plugin, tmp_path)
    # 插件副本坏了：谁来跑启动器都回不出体检 JSON
    (plugin / "mcp" / "server.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")

    rc, out, err = _run(["codex", "install", "--json"])
    assert rc == 1, out
    data = json.loads(out.strip().splitlines()[-1])
    assert data["error_code"] == "interpreter_unusable"
    assert _mcp_command(plugin) == str(broken), "没找到能跑的却还是改了 .mcp.json"


def test_a_launcher_that_only_degrades_still_counts_as_startable(tmp_path):
    """降级 server（退出码 3）**算起得来**：Codex 里它是有工具的。

    起不来才是绝症——那时候连「装了桌面版」这句话都没人说得出口。
    """
    sys.path.insert(0, str(SRC))
    from tavotto.engine import codexinstall

    server = tmp_path / "server.py"
    server.write_text(
        'import sys\nprint(\'{"ok": false, "code": "desktop_only"}\')\nsys.exit(3)\n',
        encoding="utf-8",
    )
    ok, detail = codexinstall.launcher_starts(sys.executable, server)
    assert ok, detail

    # 零输出 + 非零退出码 = 起不来。**判据不读那个数字**：现场报的是 9009，
    # 但 POSIX 的退出码只有 8 位（`exit 9009` 到这儿是 49），按数字认会挑平台。
    silent = tmp_path / "silent.py"
    silent.write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
    ok, detail = codexinstall.launcher_starts(sys.executable, silent)
    assert not ok and "没有体检 JSON" in detail, detail


def _plugin_with_two_manifests(tmp_path):
    plugin = tmp_path / "plug"
    (plugin / "skills" / "s" / "agents").mkdir(parents=True)
    (plugin / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"tavotto": {"command": "python3"}}}) + "\n", encoding="utf-8"
    )
    (plugin / "skills" / "s" / "agents" / "openai.yaml").write_text(
        "dependencies:\n  tools:\n    - type: mcp\n      command: python3\npolicy:\n  x: 1\n",
        encoding="utf-8",
    )
    return plugin


def test_pinning_survives_crlf_manifests(tmp_path):
    """行尾是 CRLF 时两份**都要**换上去，且行尾原样保留。

    这条是真机逼出来的：原实现用跨行正则 `^dependencies:\n…`（`re.M | re.S`），
    CRLF 下 `^dependencies:` 后面是 `\r` 不是 `\n`，**永不匹配**，于是 openai.yaml
    静默不改、连错都不报——`.mcp.json` 钉上了、同源对只剩一侧，正是 Codex 反复
    提示重装的那个状态。Git for Windows 默认 `core.autocrlf=true` + Codex 走
    sparse-checkout，用户机器上那份大概率就是 CRLF。

    判据故意**同时钉「换了」与「行尾没被改掉」**：只钉前者的话，把整份文件规范化成
    LF 也能过，而那会让下一次 git 比较整份文件都是脏的。
    """
    sys.path.insert(0, str(SRC))
    from tavotto.engine import codexinstall

    plugin = tmp_path / "plug"
    (plugin / "skills" / "s" / "agents").mkdir(parents=True)
    (plugin / ".mcp.json").write_bytes(
        (json.dumps({"mcpServers": {"tavotto": {"command": "python3"}}}) + "\n").encode()
    )
    yaml_path = plugin / "skills" / "s" / "agents" / "openai.yaml"
    crlf = (
        "interface:\r\n  display_name: keep\r\n"
        "dependencies:\r\n  tools:\r\n    - type: mcp\r\n      command: python3\r\n"
        "policy:\r\n  allow_implicit_invocation: true\r\n"
    )
    yaml_path.write_bytes(crlf.encode("utf-8"))

    changed = codexinstall.pin_launcher_command(plugin, "/opt/real/python")
    assert changed == [".mcp.json", "skills/s/agents/openai.yaml"], "CRLF 下同源对只换了一侧"
    after = yaml_path.read_bytes()
    assert b"      command: /opt/real/python\r\n" in after, after
    assert after.count(b"\r\n") == crlf.count("\r\n"), "行尾被改掉了"
    assert b"display_name: keep" in after and b"allow_implicit_invocation" in after


def test_a_silent_no_op_substitution_fails_loudly_instead_of_leaving_half_a_pair(
    tmp_path, monkeypatch
):
    """替换**静默没换上去**时要当场炸，不能只钉一侧就报成功。

    这条判据的主语不是正则，是「万一以后又静默失配会怎样」——CRLF 那次正是这个
    形状：函数原样返回、零报错、`.mcp.json` 钉上了、`openai.yaml` 没动，用户端
    表现成「每装一次被告知一次没装」。所以在计划阶段当场验一次目标行真的落进了
    文件，验的判据与那个扫描器无关（不拿它自己验自己）。

    注入点就是「扫描器原样返回」：任何一次静默失配都长这样。
    """
    sys.path.insert(0, str(SRC))
    from tavotto.engine import codexinstall

    plugin = _plugin_with_two_manifests(tmp_path)
    mcp, yml = plugin / ".mcp.json", plugin / "skills" / "s" / "agents" / "openai.yaml"
    before = (mcp.read_bytes(), yml.read_bytes())

    monkeypatch.setattr(codexinstall, "_replace_dependency_command", lambda text, command: text)
    with pytest.raises(OSError):
        codexinstall.pin_launcher_command(plugin, "/opt/real/python")
    assert (mcp.read_bytes(), yml.read_bytes()) == before, "静默失配之后还是留下了半套状态"


def test_pinning_is_all_or_nothing_when_the_second_file_fails(tmp_path, monkeypatch):
    """第二份换不上去时，**磁盘上两份都保持原样**。

    半套状态正是这条 issue 想避免的那个坏结局：Codex 按 command 匹配 stdio 依赖，
    `.mcp.json` 换了、`openai.yaml` 没换的话，插件自带的 server 会被当成「还没装」，
    用户每装一次被告知一次没装。

    判据的主语是**那两个文件的字节**，不是「有没有抛异常」——只写一侧的实现照样抛，
    抛的时候磁盘已经坏了。故障注入钉在 `os.replace` 上：那是任何一份正确实现都必须
    经过的那一步（不是某个 helper），所以退回「两次独立写」时它同样会被打到。
    """
    sys.path.insert(0, str(SRC))
    from tavotto.engine import atomicio, codexinstall

    plugin = _plugin_with_two_manifests(tmp_path)
    mcp, yml = plugin / ".mcp.json", plugin / "skills" / "s" / "agents" / "openai.yaml"
    before = (mcp.read_bytes(), yml.read_bytes())

    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:  # 第二份的那一次 rename
            raise OSError(28, "No space left on device")
        return real_replace(src, dst, *a, **kw)

    # **先钉死「要落的就是两份」**。计划被静默削成一份时（CRLF 那条缺陷就是这么
    # 干的），第二次 replace 压根不会发生，本用例会以 DID NOT RAISE 的形式空转——
    # 缺陷存在时判据反而不红，那是最坏的一种。
    assert len(codexinstall._pin_plan(plugin, "/opt/real/python")) == 2

    monkeypatch.setattr(atomicio.os, "replace", flaky)
    with pytest.raises(OSError):
        codexinstall.pin_launcher_command(plugin, "/opt/real/python")

    assert (mcp.read_bytes(), yml.read_bytes()) == before, "留下了半套状态"
    assert calls["n"] >= 3, "根本没试过回滚"
    leftovers = sorted(q.name for q in plugin.rglob("*.tmp"))
    assert leftovers == [], f"留下了临时文件：{leftovers}"


def test_pinning_writes_through_a_symlinked_manifest(tmp_path):
    """清单是符号链接时，换的是**它指向的那个文件**，不是把链接替换成普通文件。

    `os.replace()` 换的是路径本身——PR #254 上正因为这条吃过一条 P1：链接被换成了
    普通文件，旧内容原封不动留在那头，而调用方报「换好了」。
    """
    sys.path.insert(0, str(SRC))
    from tavotto.engine import codexinstall

    plugin = _plugin_with_two_manifests(tmp_path)
    real = tmp_path / "elsewhere" / "openai.yaml"
    real.parent.mkdir()
    link = plugin / "skills" / "s" / "agents" / "openai.yaml"
    real.write_bytes(link.read_bytes())
    link.unlink()
    try:
        link.symlink_to(real)
    except OSError as exc:  # Windows 上没开发者模式/无权限时建不了
        pytest.skip(f"这台机器建不了符号链接：{exc}")

    codexinstall.pin_launcher_command(plugin, "/opt/real/python")
    assert link.is_symlink(), "符号链接被换成了普通文件"
    assert "command: /opt/real/python" in real.read_text(encoding="utf-8"), "写的不是链接指向的那份"


def test_pinning_only_touches_the_dependency_command(tmp_path):
    """钉命令只动 `dependencies:` 块里那一行，`interface:` / `policy:` 不许被扫到。"""
    sys.path.insert(0, str(SRC))
    from tavotto.engine import codexinstall

    plugin = tmp_path / "plug"
    (plugin / "skills" / "s" / "agents").mkdir(parents=True)
    (plugin / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"tavotto": {"command": "python3"}}}), encoding="utf-8"
    )
    yaml_path = plugin / "skills" / "s" / "agents" / "openai.yaml"
    yaml_path.write_text(
        "interface:\n"
        "  command: 别动我\n"
        "dependencies:\n"
        "  tools:\n"
        "    - type: mcp\n"
        "      command: python3\n"
        "policy:\n"
        "  command: 也别动我\n",
        encoding="utf-8",
    )
    changed = codexinstall.pin_launcher_command(plugin, "/opt/py 3/bin/python")
    assert changed == [".mcp.json", "skills/s/agents/openai.yaml"]
    text = yaml_path.read_text(encoding="utf-8")
    assert "  command: 别动我" in text and "  command: 也别动我" in text
    assert "      command: /opt/py 3/bin/python\n" in text, text

    # `#` 会把后面吃成注释：这种值要加引号
    codexinstall.pin_launcher_command(plugin, "/opt/py #1/python")
    text = yaml_path.read_text(encoding="utf-8")
    assert "      command: '/opt/py #1/python'\n" in text, text


# --------------------------- 真 CLI（显式 opt-in） ---------------------------
@pytest.mark.skipif(
    not os.environ.get("TAVOTTO_CODEX_REAL_SMOKE"),
    reason="要网络与真实 marketplace：设 TAVOTTO_CODEX_REAL_SMOKE=1 才跑",
)
def test_real_codex_cli_install_then_doctor(tmp_path):
    import shutil

    if shutil.which("codex") is None:
        pytest.skip("这台机器上没有 codex CLI")
    env = {"CODEX_HOME": str(tmp_path / "codexhome")}
    rc, out, err = _run(["codex", "install", "--json"], env)
    assert rc == 0, out + err
    rc, out, err = _run(["codex", "doctor", "--json"], env)
    assert rc == 0, out + err
    assert json.loads(out.strip().splitlines()[-1])["ok"] is True


# ------------------- 诊断要回答的四个问题（ADR 0043） -------------------
# 装的是哪份插件、来自哪里、画布完整吗、引擎版本满足吗。判据的主语是 `--json`
# 输出里的 `summary` 与各步的 `error_code`，不是 stdout 的措辞。


def _doctor_json(env_extra: dict | None = None) -> tuple[int, dict, str]:
    rc, out, err = _run(["codex", "doctor", "--json"], env_extra)
    return rc, json.loads(out.strip().splitlines()[-1]), err


def _tree_digest(root: Path) -> dict[str, str]:
    import hashlib

    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_doctor_answers_the_four_questions_when_healthy(fake_codex):
    assert _run(["codex", "install", "--json"])[0] == 0
    rc, data, err = _doctor_json()
    assert rc == 0, err
    s = data["summary"]
    assert s["plugin"]["version"] == "0.12.0"
    assert s["plugin"]["install_dir"] == str(fake_codex["plugin"])
    assert s["marketplace"]["registered"] is True
    assert s["marketplace"]["source"] == "https://github.com/Tavotto/Tavotto.git"
    assert s["channel"]["channel"] == "stable"
    assert s["canvas"]["complete"] is True
    assert s["engine"]["version"] == "0.13.0"
    steps = {st["step"]: st for st in data["steps"]}
    assert steps["canvas"]["ok"] and steps["canvas"]["skipped"], (
        "健康时画布步是「跳过（本来就好）」"
    )


def test_doctor_is_read_only_even_when_it_finds_problems(fake_codex):
    assert _run(["codex", "install", "--json"])[0] == 0
    (fake_codex["plugin"] / "mcp" / "widget" / "canvas.html").unlink()
    before = _tree_digest(fake_codex["home"])
    calls_before = fake_codex["log"].read_text(encoding="utf-8")
    rc, data, _ = _doctor_json()
    assert rc == 1 and data["error_code"] == "canvas_incomplete"
    assert _tree_digest(fake_codex["home"]) == before, "doctor 改动了 CODEX_HOME 里的文件"
    new_calls = fake_codex["log"].read_text(encoding="utf-8")[len(calls_before) :]
    assert " add " not in new_calls and " remove " not in new_calls, (
        f"doctor 跑了 add / remove：{new_calls}"
    )


def test_a_missing_canvas_is_named_not_folded_into_reinstall(fake_codex):
    assert _run(["codex", "install", "--json"])[0] == 0
    canvas = fake_codex["plugin"] / "mcp" / "widget" / "canvas.html"
    canvas.write_bytes(b"")
    rc, data, _ = _doctor_json()
    assert data["error_code"] == "canvas_incomplete"
    assert "空文件" in data["error"]
    assert data["summary"]["canvas"]["complete"] is False
    # install 也不会假装修好：画布不是它补的，它如实报同一个 code
    rc, out, _ = _run(["codex", "install", "--json"])
    assert rc == 1 and json.loads(out.strip().splitlines()[-1])["error_code"] == "canvas_incomplete"
    assert canvas.stat().st_size == 0, "install 不许悄悄补画布"


def test_a_pinned_launcher_does_not_make_the_canvas_step_cry_wolf(fake_codex, tmp_path):
    """已装副本合法的本地修改（两份清单一起钉 command）不算损坏。

    让 install 走「钉解释器」那条路要用 `_break_the_mcp_command`（绝对路径的坏命令）：
    往 PATH 前面插 `python3` shim 在真 Windows 上不成立——CreateProcess 只补 `.exe`，
    看不见 `.cmd`（#256 与本 PR 的 windows-latest 腿都撞过）。
    """
    broken = _break_the_mcp_command(fake_codex["plugin"], tmp_path)
    assert _run(["codex", "install", "--json"])[0] == 0
    assert _mcp_command(fake_codex["plugin"]) != str(broken), "启动命令没被换掉"
    rc, data, err = _doctor_json()
    assert rc == 0, err
    assert data["summary"]["canvas"]["complete"] is True


def test_a_half_pinned_pair_is_reported_as_incomplete(fake_codex):
    assert _run(["codex", "install", "--json"])[0] == 0
    mcp = fake_codex["plugin"] / ".mcp.json"
    data = json.loads(mcp.read_text(encoding="utf-8"))
    data["mcpServers"]["tavotto"]["command"] = sys.executable
    mcp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    rc, out, _ = _doctor_json()
    assert out["error_code"] == "canvas_incomplete" and "只钉了一侧" in out["error"]


def test_unknown_marketplace_state_is_not_treated_as_absent(fake_codex):
    """`marketplace list` 本身失败 = 不知道；install 不许对着「不知道」跑 add。"""
    rc, data, _ = _doctor_json({"FAKE_CODEX_FAIL": "plugin marketplace list"})
    assert rc == 1 and data["error_code"] == "marketplace_state_unknown"
    rc, out, _ = _run(
        ["codex", "install", "--json"], {"FAKE_CODEX_FAIL": "plugin marketplace list"}
    )
    assert rc == 1
    assert json.loads(out.strip().splitlines()[-1])["error_code"] == "marketplace_state_unknown"
    calls = fake_codex["log"].read_text(encoding="utf-8")
    assert "marketplace add" not in calls


def test_a_registered_marketplace_whose_entry_the_client_skips_is_named(fake_codex):
    """快照登记了、插件却没被列出：来源类型客户端不认识（或快照过旧），不是「没装」。"""
    assert _run(["codex", "install", "--json"])[0] == 0  # 先把 marketplace 登记上
    rc, data, _ = _doctor_json({"FAKE_CODEX_ENTRY_ABSENT": "1"})
    assert rc == 1 and data["error_code"] == "plugin_source_unsupported"
    assert "marketplace upgrade" in data["error"]
    before = fake_codex["log"].read_text(encoding="utf-8").count("plugin add")
    rc, out, _ = _run(["codex", "install", "--json"], {"FAKE_CODEX_ENTRY_ABSENT": "1"})
    assert json.loads(out.strip().splitlines()[-1])["error_code"] == "plugin_source_unsupported"
    after = fake_codex["log"].read_text(encoding="utf-8").count("plugin add")
    assert after == before, "来源不支持时不该盲跑 plugin add"


def test_a_legacy_local_snapshot_is_reported_with_the_upgrade_path(fake_codex, tmp_path):
    """旧用户：快照里的插件条目还是 local ./codex-plugin。不是错误，但要说出下一步。"""
    mk_root = Path(os.environ["FAKE_CODEX_MK_ROOT"])
    (mk_root / ".agents" / "plugins" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "tavotto",
                "plugins": [
                    {"name": "tavotto", "source": {"source": "local", "path": "./codex-plugin"}}
                ],
            }
        ),
        encoding="utf-8",
    )
    assert _run(["codex", "install", "--json"])[0] == 0
    rc, data, err = _doctor_json()
    assert rc == 0, err
    assert data["summary"]["channel"]["channel"] == "legacy-local"
    steps = {st["step"]: st for st in data["steps"]}
    assert "codex plugin marketplace upgrade tavotto" in steps["marketplace"]["detail"]


def test_a_custom_source_is_left_alone(fake_codex):
    mk_root = Path(os.environ["FAKE_CODEX_MK_ROOT"])
    (mk_root / ".agents" / "plugins" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "tavotto",
                "plugins": [
                    {
                        "name": "tavotto",
                        "source": {
                            "source": "git-subdir",
                            "url": "https://github.com/someone/fork.git",
                            "path": "./codex-plugin",
                            "ref": "main",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert _run(["codex", "install", "--json"])[0] == 0
    rc, data, err = _doctor_json()
    assert rc == 0, err
    assert data["summary"]["channel"]["channel"] == "custom"
    calls = fake_codex["log"].read_text(encoding="utf-8")
    assert calls.count("marketplace add") == 1 and "marketplace remove" not in calls


def test_the_install_dir_is_the_version_codex_says_it_enabled(fake_codex, tmp_path):
    """git 来源的插件：`plugin list` 的 PATH 列是来源描述不是路径（codex 0.151 实测），
    安装目录 = cache/<marketplace>/<plugin>/<Codex 报的版本>。同名缓存并存时不按最高
    版本号猜；连版本都报不出来时才看缓存，且只认恰好一份。"""
    other = fake_codex["home"] / "plugins" / "cache" / "tavotto" / "tavotto" / "9.9.9"
    import shutil

    shutil.copytree(fake_codex["plugin"], other)
    assert _run(["codex", "install", "--json"])[0] == 0
    rc, data, err = _doctor_json()
    assert rc == 0, err
    assert data["summary"]["plugin"]["install_dir"] == str(fake_codex["plugin"]), (
        "该用 Codex 报的版本那份（0.12.0），不是最高版本号（9.9.9）"
    )
    # 客户端报不出版本：两份并存 → 歧义，把候选列出来
    rc, data, _ = _doctor_json({"FAKE_CODEX_VERSION": ""})
    assert rc == 1 and data["error_code"] == "plugin_install_ambiguous"
    assert "9.9.9" in data["error"] and "0.12.0" in data["error"]
    shutil.rmtree(other)
    rc, data, err = _doctor_json({"FAKE_CODEX_VERSION": ""})
    assert rc == 0, err
    assert data["summary"]["plugin"]["install_dir"] == str(fake_codex["plugin"]), "恰好一份就认它"


def test_a_local_source_plugin_is_located_by_the_reported_path(fake_codex, tmp_path):
    """本地来源（指向工作副本的 marketplace）：PATH 列就是 Codex 加载的目录。"""
    local = tmp_path / "workcopy" / "codex-plugin"
    import shutil

    shutil.copytree(fake_codex["plugin"], local)
    assert _run(["codex", "install", "--json"], {"FAKE_CODEX_PLUGIN_PATH": str(local)})[0] == 0
    rc, data, err = _doctor_json({"FAKE_CODEX_PLUGIN_PATH": str(local)})
    assert rc == 0, err
    assert data["summary"]["plugin"]["install_dir"] == str(local)


def test_a_text_only_client_still_gets_a_correct_diagnosis(fake_codex):
    """老客户端没有 --json：状态从文本表读，路径从 PATH 列读。"""
    env = {"FAKE_CODEX_TEXT_ONLY": "1"}
    assert _run(["codex", "install", "--json"], env)[0] == 0
    rc, data, err = _doctor_json(env)
    assert rc == 0, err
    assert data["summary"]["plugin"]["install_dir"] == str(fake_codex["plugin"])
    assert data["summary"]["plugin"]["version"] == "0.12.0"
    assert data["summary"]["marketplace"]["registered"] is True


def test_an_engine_older_than_the_plugin_requires_is_named(fake_codex):
    """随包清单说最低 0.13.0，体检报引擎 0.5.0 → engine_too_old，不是笼统的 health_failed。"""
    from tavotto.engine import pluginmanifest

    plugin = fake_codex["plugin"]
    (plugin / "LICENSE").write_text("AGPL\n", encoding="utf-8")
    pluginmanifest.write_build_manifest(
        plugin,
        modes={},
        source_sha="a" * 40,
        fingerprint="feedfacecafebeef",
        lockfile_sha256=None,
        toolchain={},
        min_tavotto_version="0.13.0",
    )
    (plugin / "mcp" / "server.py").write_text(
        'import sys\nprint(\'{"ok": true, "engine_version": "0.5.0"}\')\nsys.exit(0)\n',
        encoding="utf-8",
    )
    # 清单之后又改了 server.py：先把清单重写一遍，否则画布步会先报「发行文件被改过」
    pluginmanifest.write_build_manifest(
        plugin,
        modes={},
        source_sha="a" * 40,
        fingerprint="feedfacecafebeef",
        lockfile_sha256=None,
        toolchain={},
        min_tavotto_version="0.13.0",
    )
    assert _run(["codex", "install", "--json"])[0] == 1
    rc, data, _ = _doctor_json()
    assert rc == 1 and data["error_code"] == "engine_too_old"
    assert data["summary"]["engine"] == {
        **data["summary"]["engine"],
        "version": "0.5.0",
        "min_required": "0.13.0",
        "satisfied": False,
    }


def test_json_output_parser_handles_pretty_printed_and_last_line_shapes():
    from tavotto.engine import codexinstall

    assert codexinstall._json_output('{\n  "a": [\n    1\n  ]\n}\n') == {"a": [1]}
    assert codexinstall._json_output('warning: x\n{\n  "a": 1\n}') == {"a": 1}
    assert codexinstall._json_output('noise\n{"ok": true}') == {"ok": True}
    assert codexinstall._json_output("nothing here") is None
