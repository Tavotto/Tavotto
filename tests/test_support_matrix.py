"""支持矩阵（docs/support-matrix.json）与事实对拍（1.0 审计 P1-06）。

承诺与事实分叉的方式从来不是有人撒谎，而是两处各写一份、改了一处忘了另一处。
所以矩阵里能机器核对的每一条都在这里与权威来源对拍：Python 范围对 pyproject、
macOS Intel 的不支持状态对 runtime-lock 的 shipped 标记、README 的引用对文件
本身。改任何一侧，这里会先红。
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MATRIX = ROOT / "docs" / "support-matrix.json"


def _release_section():
    """按路径加载渲染脚本（scripts/ 不是包，也不该为了测试变成包）。"""
    path = ROOT / "scripts" / "make_release_support_section.py"
    spec = importlib.util.spec_from_file_location("make_release_support_section", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _matrix() -> dict:
    return json.loads(MATRIX.read_text(encoding="utf-8"))


def _targets() -> dict:
    return {t["id"]: t for t in _matrix()["targets"]}


def test_python_range_matches_pyproject():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'requires-python\s*=\s*"([^"]+)"', pyproject)
    assert m, "pyproject 里找不到 requires-python"
    assert _matrix()["python"]["requires"] == m.group(1), (
        "支持矩阵的 Python 范围与 pyproject 不一致——两边必须一起改")


def test_tested_pythons_match_classifiers():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    classified = set(re.findall(
        r'"Programming Language :: Python :: (3\.\d+)"', pyproject))
    assert classified, "pyproject 没有列出已验证的 Python 小版本 classifiers"
    assert set(_matrix()["python"]["tested"]) == classified


def test_macos_intel_stays_honest_with_runtime_lock():
    lock = json.loads((ROOT / "packaging" / "runtime-lock.json")
                      .read_text(encoding="utf-8"))
    shipped = bool(lock["targets"]["macos-x86_64"].get("shipped"))
    status = _targets()["macos-x86_64"]["status"]
    if shipped:
        assert status != "unsupported", (
            "runtime-lock 说 Intel 已构建发行，矩阵还标着 unsupported")
    else:
        assert status == "unsupported", (
            "Intel 没构建过也没冒烟过（runtime-lock shipped=false），"
            "矩阵不得声称支持")


def test_supported_targets_are_exactly_the_shipping_desktops():
    supported = {tid for tid, t in _targets().items()
                 if t["status"] == "supported"}
    assert supported == {"windows-x64-desktop", "macos-arm64-desktop"}, (
        "supported 档只留真有安装包 + 真产物门禁的两个桌面目标；"
        "要扩就先把产物与验收建起来")


def test_readme_references_the_matrix():
    for name in ("README.md", "README.zh-CN.md"):
        readme = (ROOT / name).read_text(encoding="utf-8")
        assert "support-matrix" in readme, (
            f"{name} 必须引用 docs/support-matrix.json——不引用它就会自己另写一份")


def test_every_target_has_the_required_fields():
    for tid, t in _targets().items():
        assert t.get("label") and t.get("status"), tid
        assert t["status"] in ("supported", "beta", "unsupported"), tid
        if t["status"] != "supported":
            assert t.get("note") or t.get("channel"), (
                f"{tid}：非 supported 档要么给出路（channel）要么说明为什么")


# ---------------------------------------------------------------------------
# 发布页的下载与支持段从矩阵生成（issue #34）：手写副本必然漂移，所以
# Release body 的那段英文由 scripts/make_release_support_section.py 渲染，
# 这里守住「每个目标都出现、状态词只从 status 派生、release.yml 真的在用」。
# ---------------------------------------------------------------------------


def test_every_target_carries_release_page_english():
    for tid, t in _targets().items():
        assert t.get("label_en") and t.get("en"), (
            f"{tid}：发布页从矩阵渲染，label_en / en 英文成文必须写在矩阵里")


def test_release_section_renders_every_target_with_derived_status():
    mod = _release_section()
    out = mod.render(_matrix())
    for t in _matrix()["targets"]:
        assert t["label_en"] in out, t["id"]
        assert f"**{t['label_en']}** — {mod.STATUS_EN[t['status']]}." in out, (
            f"{t['id']}：状态词必须由 status 派生，不能在 en 成文里另写一份")
    # Python 范围来自矩阵，不是脚本里写死的
    tested = _matrix()["python"]["tested"]
    assert f"Python {tested[0]}–{tested[-1]}" in out
    assert "support-matrix.json" in out


def test_release_section_refuses_unknown_status_and_missing_english():
    mod = _release_section()
    bad_status = json.loads(MATRIX.read_text(encoding="utf-8"))
    bad_status["targets"][0]["status"] = "experimental"
    with pytest.raises(SystemExit, match="unknown status"):
        mod.render(bad_status)
    missing_en = json.loads(MATRIX.read_text(encoding="utf-8"))
    del missing_en["targets"][0]["en"]
    with pytest.raises(SystemExit, match="label_en/en"):
        mod.render(missing_en)


def test_release_workflow_appends_the_generated_section():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8")
    assert "make_release_support_section.py" in workflow, (
        "release.yml 不再追加生成的支持段——发布页的平台清单会退回手写漂移")


def test_readme_explains_smartscreen():
    """#34 明令禁止「Windows 未签名时仍不解释 SmartScreen 状态」。"""
    for name in ("README.md", "README.zh-CN.md"):
        readme = (ROOT / name).read_text(encoding="utf-8")
        assert "SmartScreen" in readme, (
            f"{name} 必须解释未签名安装包会触发 SmartScreen 及用户该怎么办")
