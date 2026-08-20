"""支持矩阵（docs/support-matrix.json）与事实对拍（1.0 审计 P1-06）。

承诺与事实分叉的方式从来不是有人撒谎，而是两处各写一份、改了一处忘了另一处。
所以矩阵里能机器核对的每一条都在这里与权威来源对拍：Python 范围对 pyproject、
macOS Intel 的不支持状态对 runtime-lock 的 shipped 标记、README 的引用对文件
本身。改任何一侧，这里会先红。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATRIX = ROOT / "docs" / "support-matrix.json"


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
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "support-matrix" in readme, (
        "README 必须引用 docs/support-matrix.json——不引用它就会自己另写一份")


def test_every_target_has_the_required_fields():
    for tid, t in _targets().items():
        assert t.get("label") and t.get("status"), tid
        assert t["status"] in ("supported", "beta", "unsupported"), tid
        if t["status"] != "supported":
            assert t.get("note") or t.get("channel"), (
                f"{tid}：非 supported 档要么给出路（channel）要么说明为什么")
