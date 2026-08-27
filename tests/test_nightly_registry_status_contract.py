"""nightly 的注册表状态断言 ↔ 引擎实际产出的同源对。

这条用例存在的理由：断言写死过一个**结构性不可达**的值，连红到没人再看
那盏灯。`already` 只由 ensure_registered 的第一个提前返回产生（前提是
`stem is not None`），而 nightly 跑的是脚本目标，open_target 对脚本目标
刻意传 `stem=None`——扫描真跑了，按 docstring 就该是 `unchanged`。

判据本身不在 Python 里，在一段 PowerShell 里，只有 Windows 夜跑会执行；
所以这里**把那段 PowerShell 的取值集合解析出来**，和引擎在同一个 fixture
上真跑出来的值对拍。任一边先动都会在普通 CI 上红，不必等到夜里。

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
#: 写了盘的那两个——它们出现就是真事故，断言必须继续拒绝。
WROTE = {"created", "merged"}


def _accepted_by_nightly() -> set[str]:
    """从 nightly.yml 里解析出断言**接受**的状态集合。"""
    text = NIGHTLY.read_text(encoding="utf-8")
    m = re.search(r'\$j\.registry_status\s+-notin\s+@\(([^)]*)\)', text)
    if not m:
        raise AssertionError(
            "nightly.yml 里找不到 registry_status 的 -notin 判据——"
            "形状变了，这条用例已经失效，先修用例再说")
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_parsed_set_is_a_real_subset_not_everything():
    """解析出全集等于没判——空门禁的经典形状，先把它挡掉。"""
    accepted = _accepted_by_nightly()
    assert accepted, "解析出空集合，判据失效"
    assert accepted < ALL_STATES, f"接受了全部四态，等于没判：{accepted}"


def test_nightly_still_rejects_the_two_states_that_mean_a_write_happened():
    """`created` = 没认出图库又写了第二份；`merged` = fixture 漂移。"""
    assert _accepted_by_nightly().isdisjoint(WROTE)


def test_script_target_status_is_actually_accepted_by_nightly(tmp_path):
    """引擎在 nightly 那个 fixture 上真跑出来的值，必须在接受集合里。

    **这就是那次连红的形状**：断言要的值这条代码路径产不出来。
    """
    proj = tmp_path / "我的 图库"
    shutil.copytree(FIXTURE, proj)
    # open_target 对脚本目标传的就是 stem=None（handoff.py 的静态发现那一遍）
    info = handoff.ensure_registered(str(proj), None)
    assert info["status"] in ALL_STATES
    assert info["status"] in _accepted_by_nightly(), (
        f"引擎产出 {info['status']}，但 nightly 只接受 "
        f"{sorted(_accepted_by_nightly())}——夜跑会连红")


def test_already_is_unreachable_without_a_stem(tmp_path):
    """钉住根因本身：不给 stem 就拿不到 `already`，给了才拿得到。

    哪天 open_target 改成把 stem 传进来，这条会红——那时该回头看 nightly
    的断言还对不对，而不是让夜跑替我们发现。
    """
    proj = tmp_path / "lib"
    shutil.copytree(FIXTURE, proj)
    assert handoff.ensure_registered(str(proj), None)["status"] == "unchanged"
    assert handoff.ensure_registered(str(proj), "Fig_runtime_stack")["status"] == "already"


@pytest.mark.parametrize("mutate,expect", [
    (lambda p: (p / "tavotto_registry.json").unlink(), "created"),
    (lambda p: (p / "fig_extra.py").write_text(
        "import matplotlib.pyplot as plt\n"
        "def main():\n"
        "    fig, ax = plt.subplots()\n"
        "    fig.savefig('Fig_extra.pdf')\n", encoding="utf-8"), "merged"),
])
def test_the_two_rejected_states_are_actually_reachable(tmp_path, mutate, expect):
    """被拒绝的那两个态必须真的产得出来，否则「拒绝」是句空话。"""
    proj = tmp_path / "lib"
    shutil.copytree(FIXTURE, proj)
    mutate(proj)
    assert handoff.ensure_registered(str(proj), None)["status"] == expect
