"""nightly 的注册表状态断言 ↔ 引擎实际产出的同源对。

这条用例存在的理由：断言写死过一个**结构性不可达**的值（`already`），连红
一周到没人再看那盏灯。

**判据必须走 open_target，不能直接调 ensure_registered。** 第一版就是直接
调、还把 `None` 写死在用例里——那测的是「ensure_registered 拿到 None 会怎样」，
而真正要守的是「open_target 到底传了什么」。量错对象的用例看起来全绿，却
挡不住它该挡的那次回归。

判据本身不在 Python 里，在一段 PowerShell 里，只有 Windows 夜跑会执行；所以
这里把那段 PowerShell 的取值解析出来，和引擎在同一个 fixture 上真跑出来的
值对拍。任一边先动都会在普通 CI 上红，不必等到夜里。

与 tests/test_merge_queue_workflows.py 同一条纪律：不用 PyYAML，用只认本
仓库形状的字符串判据，解析不出预期形状时当场抛。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from tavotto.engine import handoff

ROOT = Path(__file__).resolve().parents[1]
NIGHTLY = ROOT / ".github" / "workflows" / "nightly.yml"
FIXTURE = ROOT / "examples" / "runtime_check"

#: ensure_registered 的四态，唯一出处是它自己的 docstring。
ALL_STATES = {"already", "created", "merged", "unchanged"}
DRIFT = ("import matplotlib.pyplot as plt\n"
         "def main():\n"
         "    fig, ax = plt.subplots()\n"
         "    fig.savefig('Fig_extra.pdf')\n")


def _accepted_by_nightly() -> set[str]:
    """从 nightly.yml 里解析出断言**接受**的状态集合。"""
    text = NIGHTLY.read_text(encoding="utf-8")
    m = re.search(r'\$j\.registry_status\s+-ne\s+"([^"]+)"', text)
    if m:
        return {m.group(1)}
    m = re.search(r'\$j\.registry_status\s+-notin\s+@\(([^)]*)\)', text)
    if m:
        return set(re.findall(r'"([^"]+)"', m.group(1)))
    raise AssertionError(
        "nightly.yml 里找不到 registry_status 的判据——形状变了，"
        "这条用例已经失效，先修用例再说")


def _status_via_open_target(project: Path) -> str:
    """**走真实路径**：nightly 跑的就是 open_target（经插件脚本）。"""
    script = project / "fig_stack.py"
    result = handoff.open_target(str(script), launch_ui=False, no_probe=True)
    return result["registry"]["status"]


def _copy(tmp_path: Path, name: str = "我的 图库") -> Path:
    proj = tmp_path / name
    shutil.copytree(FIXTURE, proj)
    return proj


# ---------------------------------------------------------------------------
# 判据形状
# ---------------------------------------------------------------------------
def test_parsed_set_is_a_real_subset_not_everything():
    accepted = _accepted_by_nightly()
    assert accepted, "解析出空集合，判据失效"
    assert accepted < ALL_STATES, f"接受了全部四态，等于没判：{accepted}"


def test_nightly_rejects_already_because_it_means_the_scan_was_skipped():
    """`already` 看着人畜无害（也表示没写盘），接受它却会让掉漂移检测。

    那个提前返回是在 engine_discover.merge() **之前**短路的：拿到 already
    就意味着全量扫描根本没跑，于是 fixture 漂移永远报不出 merged，而这盏灯
    照样绿。见 test_scan_actually_runs_so_fixture_drift_is_detectable。
    """
    assert "already" not in _accepted_by_nightly()


def test_nightly_rejects_the_two_states_that_mean_a_write_happened():
    assert _accepted_by_nightly().isdisjoint({"created", "merged"})


# ---------------------------------------------------------------------------
# 走真实路径：open_target 传什么，才是这里唯一要量的东西
# ---------------------------------------------------------------------------
def test_open_target_status_is_actually_accepted_by_nightly(tmp_path):
    """**那次连红的形状**：断言要的值这条代码路径产不出来。"""
    status = _status_via_open_target(_copy(tmp_path))
    assert status in ALL_STATES
    assert status in _accepted_by_nightly(), (
        f"open_target 产出 {status}，但 nightly 只接受 "
        f"{sorted(_accepted_by_nightly())}——夜跑会连红")


def test_scan_actually_runs_so_fixture_drift_is_detectable(tmp_path):
    """**本文件最重要的一条。** 漂移必须能被看见。

    open_target 若回归成把已解析的 stem 传进 ensure_registered，短路会跳过
    全量扫描，这里就会拿到 `already` 而不是 `merged`——夜跑照绿，漂移检测
    却已经没了。这条用例守的是那个**性质**，不是某一行实现。
    """
    proj = _copy(tmp_path)
    (proj / "fig_extra.py").write_text(DRIFT, encoding="utf-8")
    assert _status_via_open_target(proj) == "merged", (
        "漂移过的图库没报 merged——全量扫描没跑，八成是 open_target "
        "把 stem 传进 ensure_registered 短路了")


def test_created_is_reachable_through_the_real_path(tmp_path):
    """被拒绝的态必须真的产得出来，否则「拒绝」是句空话。"""
    proj = _copy(tmp_path)
    (proj / "tavotto_registry.json").unlink()
    assert _status_via_open_target(proj) == "created"


# ---------------------------------------------------------------------------
# 根因本身
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("stem,expect", [(None, "unchanged"),
                                         ("Fig_runtime_stack", "already")])
def test_already_requires_a_stem(tmp_path, stem, expect):
    """钉住根因：不给 stem 拿不到 `already`，给了才拿得到。

    哪天 open_target 改成把 stem 传进来，上面那条漂移用例会红——这条负责
    解释为什么。
    """
    assert handoff.ensure_registered(str(_copy(tmp_path, "lib")), stem)["status"] == expect
