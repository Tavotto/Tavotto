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
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


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


def test_project_env_mirrors_the_matrix():
    """`engine/projectenv.py` 的支持区间是矩阵与 pyproject 的**运行时镜像**。

    那两份文件不随 wheel 发布，运行时读不到，所以只能在代码里再写一遍——
    于是必须有人盯着两侧别漂。矩阵放宽到 3.14 而 projectenv 还卡在 3.13 的话，
    用户的 3.14 环境会被判成 `project_env_unsupported_python`，而 README 上
    白纸黑字写着支持——这里先红。
    """
    from tavotto.engine import projectenv

    matrix = _matrix()
    m = re.match(r">=(\d+)\.(\d+),<(\d+)\.(\d+)$", matrix["python"]["requires"])
    assert m, matrix["python"]["requires"]
    assert projectenv.PYTHON_MIN == (int(m.group(1)), int(m.group(2)))
    assert projectenv.PYTHON_MAX_EXCLUSIVE == (int(m.group(3)), int(m.group(4)))
    tested = tuple(tuple(int(x) for x in v.split(".")) for v in matrix["python"]["tested"])
    assert projectenv.PYTHON_TESTED == tested

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    mm = re.search(r'worker\s*=\s*\["matplotlib>=(\d+)\.(\d+),<(\d+)\.(\d+)"', pyproject)
    assert mm, "pyproject 的 worker extra 里读不到 matplotlib 区间"
    assert projectenv.MPL_MIN == (int(mm.group(1)), int(mm.group(2)))
    assert projectenv.MPL_MAX_EXCLUSIVE == (int(mm.group(3)), int(mm.group(4)))


def test_python_range_matches_pyproject():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'requires-python\s*=\s*"([^"]+)"', pyproject)
    assert m, "pyproject 里找不到 requires-python"
    assert _matrix()["python"]["requires"] == m.group(1), (
        "支持矩阵的 Python 范围与 pyproject 不一致——两边必须一起改"
    )


def _backend_fast_pythons() -> list[str]:
    """从 ci.yml 里切出 `backend-fast` 的 `strategy.matrix`，返回它跑的 Python 档
    （按出现顺序）。

    **不用 PyYAML**（与 tests/test_merge_queue_workflows.py 同一条纪律：它不在
    `.venv` 里，importorskip 会让整组判据静默跳过——那正是空门禁）。改为只认
    本仓库的缩进形状：先按两格缩进切出 job 块，再在 `matrix:` 下面**更深缩进**
    的行里取 `python: ["3.10", …]` 那根轴（CI03a 起 matrix 是 python × shard 的轴，
    不再是 `include` 列表），注释行跳过，缩进回到 `matrix:` 那一层就停。
    切不出 job、找不到 matrix、轴读不出——三种都当场抛，不许安静地返回空集让
    下面的判据恒真。
    """
    assert CI_WORKFLOW.is_file(), (
        f"读不到 {CI_WORKFLOW.relative_to(ROOT)}——这条判据的输入是仓库级 workflow"
    )
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    job = re.search(r"(?ms)^  backend-fast:\n(.*?)(?=^  [\w-]+:|\Z)", text)
    assert job, "ci.yml 里切不出 job `backend-fast`——缩进形状变了？"
    lines = job.group(0).splitlines()
    heads = [i for i, ln in enumerate(lines) if ln.strip() == "matrix:"]
    assert len(heads) == 1, f"backend-fast 里应恰有一个 `matrix:`，读到 {len(heads)} 个"
    head = heads[0]
    depth = len(lines[head]) - len(lines[head].lstrip())
    pythons: list[str] | None = None
    for ln in lines[head + 1 :]:
        if not ln.strip():
            continue
        indent = len(ln) - len(ln.lstrip())
        if indent <= depth:
            break
        if ln.lstrip().startswith("#"):
            continue
        axis = re.fullmatch(r"\s*python:\s*\[([^\]]*)\]\s*", ln)
        if axis:
            assert pythons is None, "backend-fast 的 matrix 里有两根 python 轴"
            pythons = [v.strip().strip("\"'") for v in axis.group(1).split(",") if v.strip()]
    assert pythons, "backend-fast 的 matrix 下读不出 `python: [...]` 那根轴——形状变了？"
    for py in pythons:
        assert re.fullmatch(r"\d+\.\d+", py), f"python 轴上有一条不是 `X.Y`：{py!r}"
    return pythons


def test_backend_fast_runs_both_ends_of_the_tested_range():
    """矩阵里 `tested` 的两端（最低与最高）都必须在 ci.yml 的 backend-fast 矩阵里有腿。

    `requires-python` 的上界是承诺，CI 矩阵是兑现：放开 `<3.15` 却没有任何一条腿
    在 3.14 上跑，就是「pip 允许装」不等于「我们真的测过」（issue #33 的完成定义
    写的是「3.14 全矩阵通过」，而不是「元数据改成 3.14」）。下界同理——3.10 那腿
    掉了，`target-version = "py310"` 之外就没有任何东西在证明 3.10 还能跑。
    backend-fast 是每个 PR push 都跑的那档，所以两端钉在它上面而不是 merge_group
    才跑的 backend-platforms。
    """
    tested = _matrix()["python"]["tested"]
    assert tested, "矩阵里没有 tested 列表"
    by_version = sorted(tested, key=lambda v: tuple(int(x) for x in v.split(".")))
    lowest, highest = by_version[0], by_version[-1]
    legs = _backend_fast_pythons()
    assert highest in legs, (
        f"support-matrix 说 tested 到 {highest}，但 ci.yml 的 backend-fast 矩阵里没有 {highest} 那一档"
        f"（现有：{legs}）——放开了上界却没有腿在跑它，是一句空承诺"
    )
    assert lowest in legs, (
        f"support-matrix 说 tested 从 {lowest} 起，但 ci.yml 的 backend-fast 矩阵里没有 {lowest} 那一档"
        f"（现有：{legs}）"
    )


def test_tested_pythons_match_classifiers():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    classified = set(re.findall(r'"Programming Language :: Python :: (3\.\d+)"', pyproject))
    assert classified, "pyproject 没有列出已验证的 Python 小版本 classifiers"
    assert set(_matrix()["python"]["tested"]) == classified


def test_macos_intel_stays_honest_with_runtime_lock():
    lock = json.loads((ROOT / "packaging" / "runtime-lock.json").read_text(encoding="utf-8"))
    shipped = bool(lock["targets"]["macos-x86_64"].get("shipped"))
    status = _targets()["macos-x86_64-desktop"]["status"]
    if shipped:
        assert status != "unsupported", "runtime-lock 说 Intel 已构建发行，矩阵还标着 unsupported"
    else:
        assert status == "unsupported", (
            "Intel 没构建过也没冒烟过（runtime-lock shipped=false），矩阵不得声称支持"
        )


def test_supported_targets_are_exactly_the_shipping_desktops():
    supported = {tid for tid, t in _targets().items() if t["status"] == "supported"}
    assert supported == {"windows-x64-desktop", "macos-arm64-desktop", "macos-x86_64-desktop"}, (
        "supported 档只留真有安装包 + 真产物门禁的三个桌面目标；要扩就先把产物与验收建起来"
    )


def test_readme_references_the_matrix():
    for name in ("README.md", "README.zh-CN.md"):
        readme = (ROOT / name).read_text(encoding="utf-8")
        assert "support-matrix" in readme, (
            f"{name} 必须引用 docs/support-matrix.json——不引用它就会自己另写一份"
        )


def test_readme_does_not_offer_windows_arm_silently():
    """矩阵里 `windows-arm64` 是 unsupported，而两个 README 都不带架构限定地给出
    Windows `.exe`——Windows-on-ARM 的读者从任一语言的 README 都得不到警告。
    只要矩阵还这么判，README 的 Windows 安装包那句就必须把 ARM 说出来。"""
    assert _targets()["windows-arm64"]["status"] == "unsupported"
    en = (ROOT / "README.md").read_text(encoding="utf-8")
    zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "Windows on ARM" in en and "neither" in en.split("Windows on ARM", 1)[1][:60]
    assert "Windows ARM" in zh and "没有构建" in zh.split("Windows ARM", 1)[1][:40]


def test_every_target_has_the_required_fields():
    for tid, t in _targets().items():
        assert t.get("label") and t.get("status"), tid
        assert t["status"] in ("supported", "beta", "unsupported"), tid
        if t["status"] != "supported":
            assert t.get("note") or t.get("channel"), (
                f"{tid}：非 supported 档要么给出路（channel）要么说明为什么"
            )


# ---------------------------------------------------------------------------
# 发布页的下载与支持段从矩阵生成（issue #34）：手写副本必然漂移，所以
# Release body 的那段英文由 scripts/make_release_support_section.py 渲染，
# 这里守住「每个目标都出现、状态词只从 status 派生、release.yml 真的在用」。
# ---------------------------------------------------------------------------


def test_every_target_carries_release_page_english():
    for tid, t in _targets().items():
        assert t.get("label_en") and t.get("en"), (
            f"{tid}：发布页从矩阵渲染，label_en / en 英文成文必须写在矩阵里"
        )


def test_release_section_renders_every_target_with_derived_status():
    mod = _release_section()
    out = mod.render(_matrix())
    for t in _matrix()["targets"]:
        assert t["label_en"] in out, t["id"]
        assert f"**{t['label_en']}** — {mod.STATUS_EN[t['status']]}." in out, (
            f"{t['id']}：状态词必须由 status 派生，不能在 en 成文里另写一份"
        )
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
    """拼 release body 的那一步在发布链**第二段** `release-publish.yml`（`validate_artifacts`，
    F.2 之前在 release.yml）。"""
    workflow = (ROOT / ".github" / "workflows" / "release-publish.yml").read_text(encoding="utf-8")
    assert "make_release_support_section.py" in workflow, (
        "release-publish.yml 不再追加生成的支持段——发布页的平台清单会退回手写漂移"
    )


def test_readme_explains_smartscreen():
    """#34 明令禁止「Windows 未签名时仍不解释 SmartScreen 状态」。"""
    for name in ("README.md", "README.zh-CN.md"):
        readme = (ROOT / name).read_text(encoding="utf-8")
        assert "SmartScreen" in readme, (
            f"{name} 必须解释未签名安装包会触发 SmartScreen 及用户该怎么办"
        )
