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
if os.environ.get("FAKE_CODEX_FAIL") == " ".join(argv[:3]):
    print("boom", file=sys.stderr); sys.exit(3)
if argv[:3] == ["plugin", "marketplace", "list"]:
    # 真 CLI 的形状：MARKETPLACE / ROOT 两列。ROOT 里带 tavotto 是常事
    # （用户目录、缓存路径），子串判据会在这里翻车
    print("MARKETPLACE  ROOT")
    print("personal     /home/u/tavotto-notes")
    if d["marketplace"]:
        print("tavotto      /home/u/.codex/.tmp/marketplaces/tavotto")
elif argv[:3] == ["plugin", "marketplace", "add"]:
    d["marketplace"] = True; save(d); print("added")
elif argv[:3] == ["plugin", "marketplace", "remove"]:
    if "/" in argv[3]:
        print("invalid marketplace name: " + argv[3], file=sys.stderr); sys.exit(2)
    d["marketplace"] = False; save(d); print("removed")
elif argv[:2] == ["plugin", "list"]:
    # **marketplace 加好之后插件照样会被列出来**，只是 STATUS 是 not installed
    print("PLUGIN           STATUS              VERSION  PATH")
    if d["marketplace"]:
        st = "installed, enabled" if d["plugin"] else "not installed"
        print("tavotto@tavotto  " + st + "  0.12.0  /tmp/p")
elif argv[:2] == ["plugin", "add"]:
    d["plugin"] = True; save(d); print("added")
elif argv[:2] == ["plugin", "remove"]:
    d["plugin"] = False; save(d); print("removed")
else:
    print("unknown: " + " ".join(argv), file=sys.stderr); sys.exit(2)
"""


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
    plugin_dir = codex_home / "plugins" / "cache" / "abc123"
    (plugin_dir / ".codex-plugin").mkdir(parents=True)
    (plugin_dir / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "tavotto"}), encoding="utf-8"
    )
    (plugin_dir / "mcp").mkdir()
    # 假 server：--health 回一行 JSON，--provision 直接成功
    (plugin_dir / "mcp" / "server.py").write_text(
        "import sys\nprint('{\"ok\": true}')\nsys.exit(0)\n", encoding="utf-8"
    )

    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("FAKE_CODEX_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "calls.log"))
    return {"log": tmp_path / "calls.log", "home": codex_home}


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
    """
    sys.path.insert(0, str(SRC))
    from tavotto.engine import codexinstall

    monkeypatch.delenv("TAVOTTO_MCP_PYTHON", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    real = tmp_path / "python3"
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(
        codexinstall.shutil, "which", lambda n: str(real) if n == "python3" else None
    )
    assert codexinstall.plugin_python() == str(real)

    # PATH 上一个真 python 都没有：说清楚，别装作能跑
    monkeypatch.setattr(codexinstall.shutil, "which", lambda _n: None)
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
