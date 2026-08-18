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
import tomllib
from pathlib import Path

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
    proc = subprocess.run([sys.executable, str(HANDOFF), str(target), *args],
                          capture_output=True, text=True, env=env)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc, calls


def test_handoff_succeeds_when_the_figure_is_parameterizable(tmp_path):
    proc, calls = _run_handoff(tmp_path, {
        "ok": True, "project": "/p", "stem": "Fig1",
        "registry": {"parameterizable": True, "conflicts": [], "dynamic_names": []}})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["parameterizable"] is True and out["launch"] == "desktop"
    # 先探测（--no-launch）再交接：跑完脚本可能多出新 stem，必须重新解析
    assert len(calls) == 2 and "--no-launch" in calls[0] and "--no-launch" not in calls[1]


def test_handoff_fails_loudly_when_the_figure_has_no_script(tmp_path):
    """用户强调的那条硬约定：脚本没跟图放在一起 = 没做完，退出码必须非零。"""
    proc, _ = _run_handoff(tmp_path, {
        "ok": True, "project": "/p", "stem": "Fig1",
        "registry": {"parameterizable": False, "conflicts": [], "dynamic_names": []}})
    assert proc.returncode == 4
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "同一个目录" in out["hint"]


def test_handoff_reports_magplot_open_failure(tmp_path):
    proc, _ = _run_handoff(tmp_path, {"ok": False, "error": "注册表不是合法 JSON"})
    assert proc.returncode == 2
    assert "注册表不是合法 JSON" in proc.stdout


def test_handoff_rejects_missing_path(tmp_path):
    proc = subprocess.run([sys.executable, str(HANDOFF), str(tmp_path / "nope.pdf")],
                          capture_output=True, text=True, env={**os.environ})
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["ok"] is False


def test_plugin_is_excluded_from_the_python_package():
    """pip 用户拿到的是 Magplot，不该夹带一份 Codex 插件。"""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    exclude = cfg["tool"]["hatch"]["build"]["exclude"]
    assert "codex-plugin" in exclude and "codex-plugin/**" in exclude
    assert ".agents" in exclude and ".agents/**" in exclude
