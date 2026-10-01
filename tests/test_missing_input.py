"""数据找不到时请用户指认一次、按项目记住的只读改指表（ADR 0106）。

四层：规则怎么匹配（`figcapture.remap_target`，父进程与 worker 同一份）、从一次指认推规则
（`inputremap.derive`）、worker 里的改道与落空记账（`install_input_remap` / `missing_input_of`，
进程内）、真 worker 经 `pool.build` 从 `missing_input` 到指认后出图（输出等于指认那份数据的真值，
旁边放一份同名不同值的文件也不影响——FO08 的反例）。
"""

from __future__ import annotations

import builtins
import io
import json
import os
import pathlib
from pathlib import Path

import pytest

from tavotto.engine import (
    execspec,
    figcapture,
    inputremap,
    pool as engine_pool,
    workdir,
)

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

P = figcapture.REMAP_PREFIX
F = figcapture.REMAP_FILE


# --------------------------------------------------------------- 匹配
def test_relative_root_rule_serves_every_relative_path():
    rules = [{"kind": P, "from": "", "to": "/B/proj"}]
    assert figcapture.remap_target(rules, "data/x.csv") == "/B/proj/data/x.csv"
    assert figcapture.remap_target(rules, "./x.csv") == "/B/proj/x.csv"
    # 相对规则不碰绝对路径
    assert figcapture.remap_target(rules, "/A/data/x.csv") is None


def test_rule_remainder_cannot_walk_above_the_selected_target():
    rules = [{"kind": P, "from": "data", "to": "/chosen"}]
    assert figcapture.remap_target(rules, "data/../outside.csv") is None
    assert figcapture.remap_target(rules, "data/sub/../../outside.csv") is None
    assert figcapture.remap_target(rules, "data/sub/../inside.csv") == "/chosen/sub/../inside.csv"
    rules = [{"kind": P, "from": "../data", "to": "/chosen"}]
    assert figcapture.remap_target(rules, "../data/x.csv") == "/chosen/x.csv"
    # 最长前缀拒绝了，不能换另一条更短的规则继续读。
    more_specific = {"kind": P, "from": "data", "to": "/most-specific"}
    fallback = {"kind": P, "from": "", "to": "/other-project"}
    for rules in [[more_specific, fallback], [fallback, more_specific]]:
        assert figcapture.remap_target(rules, "data/../x.csv") is None


def test_whole_root_targets_stay_absolute():
    for target in ["/", "C:/", "//server/share/"]:
        rule = {"kind": P, "from": "data", "to": target}
        assert figcapture.remap_target([rule], "data", whole=True) == target
    rule = {"kind": P, "from": "data", "to": "/chosen"}
    assert figcapture.remap_target([rule], "data/*.csv/") == "/chosen/*.csv/"


def test_absolute_prefix_rule_and_the_longest_prefix_wins():
    rules = [
        {"kind": P, "from": "/Users/a/proj", "to": "/Volumes/B/proj"},
        {"kind": P, "from": "/Users/a/proj/raw", "to": "/Volumes/C/raw"},
    ]
    assert (
        figcapture.remap_target(rules, "/Users/a/proj/data/x.csv") == "/Volumes/B/proj/data/x.csv"
    )
    assert figcapture.remap_target(rules, "/Users/a/proj/raw/1.dat") == "/Volumes/C/raw/1.dat"
    # 按路径段比，不按字符前缀比：`/Users/a/project` 不是 `/Users/a/proj` 下面的东西
    assert figcapture.remap_target(rules, "/Users/a/project/x.csv") is None
    # 前缀本身（目录）不是被改指的文件
    assert figcapture.remap_target(rules, "/Users/a/proj") is None


def test_file_rule_only_moves_that_one_file_and_beats_prefixes():
    rules = [
        {"kind": P, "from": "/A", "to": "/B"},
        {"kind": F, "from": "/A/x.csv", "to": "/C/renamed.csv"},
    ]
    assert figcapture.remap_target(rules, "/A/x.csv") == "/C/renamed.csv"
    assert figcapture.remap_target(rules, "/A/y.csv") == "/B/y.csv"


def test_windows_written_paths_and_parent_prefixes():
    rules = [
        {"kind": P, "from": "C:/Users/a", "to": "/Volumes/B"},
        {"kind": P, "from": "..", "to": "/Volumes/up"},
    ]
    assert figcapture.remap_target(rules, r"c:\Users\a\d\x.csv") == "/Volumes/B/d/x.csv"
    assert figcapture.remap_target(rules, "../data/x.csv") == "/Volumes/up/data/x.csv"


def test_unc_server_share_is_its_own_root():
    """UNC `\\\\server\\share\\…`：server/share 是一个根段，不是 `/` 下的两段——否则拼回去成了本机的
    `/server/share/…`，自检去查当前盘上的路径（Codex 评 #716 P2）。纯字符串，POSIX 上照样跑。"""
    unc = r"\\Server\Share\proj\data\x.csv"
    assert figcapture.remap_parts(unc) == (True, ("//server/share", "proj", "data", "x.csv"))
    assert (
        figcapture._join_parts(figcapture.remap_parts(unc)[1]) == "//server/share/proj/data/x.csv"
    )
    # `derive` 从指认那一侧截掉公共后缀再拼回去：根段必须原样留着
    assert inputremap._join(figcapture.remap_parts(unc)[1][:2]) == "//server/share/proj"
    # 只有 server 没有 share、或 `///`：不是 UNC
    assert figcapture.remap_parts("//server") == (True, ("/", "server"))
    assert figcapture.remap_parts("///a/b") == (True, ("/", "a", "b"))
    rules = [{"kind": P, "from": "C:/Users/a/proj", "to": r"\\nas\lab\proj"}]
    assert (
        figcapture.remap_target(rules, r"c:\Users\a\proj\data\x.csv") == "//nas/lab/proj/data/x.csv"
    )
    # 脚本里写的是 UNC、数据挪到了本机盘
    rules = [{"kind": P, "from": r"\\Server\Share\proj", "to": "D:/moved"}]
    assert figcapture.remap_target(rules, unc) == "D:/moved/data/x.csv"
    # 另一个 share 不配
    assert figcapture.remap_target(rules, r"\\Server\Other\proj\data\x.csv") is None


@pytest.mark.skipif(os.name != "nt", reason="UNC 管理共享只在 Windows 上有")
def test_derive_accepts_a_file_chosen_through_a_unc_share(tmp_path):
    chosen = _touch(tmp_path / "moved" / "data" / "x.csv")
    drive, rest = os.path.splitdrive(str(chosen))
    unc = rf"\\localhost\{drive[0]}${rest}"
    if not os.path.isfile(unc):
        pytest.skip("本机管理共享不可用")
    rule = inputremap.derive("C:/Users/a/proj/data/x.csv", unc, chosen_is_dir=False)
    assert rule["from"] == "c:/Users/a/proj"
    assert rule["to"] == str(Path(unc).parents[1])
    assert os.path.isfile(figcapture.remap_target([rule], "C:/Users/a/proj/data/x.csv"))


def test_bad_rules_are_dropped_not_raised():
    assert figcapture.clean_remap_rules(
        [
            {"kind": P, "from": "", "to": "relative/target"},  # 目标必须是绝对路径
            {"kind": "glob", "from": "a", "to": "/b"},
            "nope",
            {"kind": F, "from": "", "to": "/b"},  # file 规则不能是空串
            {"kind": P, "from": "d", "to": "/b"},
        ]
    ) == [{"kind": P, "from": "d", "to": "/b"}]


# --------------------------------------------------------------- 推规则
def _touch(p: Path, text: str = "1") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_derive_from_a_file_with_the_same_tail(tmp_path):
    chosen = _touch(tmp_path / "moved" / "data" / "run1" / "x.csv")
    rule = inputremap.derive("data/run1/x.csv", str(chosen), chosen_is_dir=False)
    assert rule == {"kind": P, "from": "", "to": str(tmp_path / "moved")}
    rule = inputremap.derive("/Users/a/proj/data/run1/x.csv", str(chosen), chosen_is_dir=False)
    assert rule == {"kind": P, "from": "/Users/a/proj", "to": str(tmp_path / "moved")}
    # 只对上文件名：前缀规则到文件所在目录
    other = _touch(tmp_path / "elsewhere" / "x.csv")
    rule = inputremap.derive("/Users/a/proj/data/run1/x.csv", str(other), chosen_is_dir=False)
    assert rule == {"kind": P, "from": "/Users/a/proj/data/run1", "to": str(tmp_path / "elsewhere")}


def test_derive_to_keeps_the_native_separator_and_case_on_windows_shaped_paths():
    """`derive()` 的 `to` 是用户真正指认的那个目录：不能经 `remap_parts` 按路径段重拼——重拼
    会把 Windows 路径按内部匹配的规范（正斜杠、盘符小写）改写，与用户选中的路径对不上
    （Codex 评 #716 Windows full-ci 红：真实场景是 `C:\\Users\\…\\moved` 变成了
    `c:/Users/…/moved`）。这台机器跑不了 Windows，直接喂 `ntpath.dirname` 验证纯字符串函数。"""
    import ntpath

    to = inputremap._ancestor(r"C:\Users\a\proj\moved\data\run1\x.csv", 3, dirname=ntpath.dirname)
    assert to == r"C:\Users\a\proj\moved"
    to = inputremap._ancestor(r"C:\Users\a\proj\elsewhere\x.csv", 1, dirname=ntpath.dirname)
    assert to == r"C:\Users\a\proj\elsewhere"


def test_derive_from_a_renamed_file_moves_only_that_file(tmp_path):
    chosen = _touch(tmp_path / "y.csv")
    rule = inputremap.derive("/Users/a/x.csv", str(chosen), chosen_is_dir=False)
    assert rule == {"kind": F, "from": "/Users/a/x.csv", "to": str(chosen)}


def test_derive_from_a_folder_uses_the_longest_tail_that_exists(tmp_path):
    _touch(tmp_path / "new" / "data" / "x.csv")
    rule = inputremap.derive("data/x.csv", str(tmp_path / "new"), chosen_is_dir=True)
    assert rule == {"kind": P, "from": "", "to": str(tmp_path / "new")}
    rule = inputremap.derive("data/x.csv", str(tmp_path / "new" / "data"), chosen_is_dir=True)
    assert rule == {"kind": P, "from": "data", "to": str(tmp_path / "new" / "data")}
    with pytest.raises(inputremap.RemapError) as err:
        inputremap.derive("data/z.csv", str(tmp_path / "new"), chosen_is_dir=True)
    assert err.value.code == inputremap.ERROR_NOT_FOUND_IN_DIR


def test_derive_refuses_what_it_cannot_honor(tmp_path):
    with pytest.raises(inputremap.RemapError) as err:
        inputremap.derive("x.csv", "relative.csv", chosen_is_dir=False)
    assert err.value.code == inputremap.ERROR_CHOSEN_INVALID
    with pytest.raises(inputremap.RemapError) as err:
        inputremap.derive("x.csv", str(tmp_path / "absent.csv"), chosen_is_dir=False)
    assert err.value.code == inputremap.ERROR_CHOSEN_INVALID
    with pytest.raises(inputremap.RemapError) as err:
        inputremap.derive("", str(tmp_path), chosen_is_dir=True)
    assert err.value.code == inputremap.ERROR_REQUESTED_INVALID


def test_rules_are_project_scoped_and_removable(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    inputremap.add_rule(a, {"kind": P, "from": "", "to": str(tmp_path)})
    assert inputremap.rules_for(a) == [{"kind": P, "from": "", "to": str(tmp_path)}]
    assert inputremap.rules_for(b) == []
    # 同一处重新指认：替换，不叠加
    inputremap.add_rule(a, {"kind": P, "from": "", "to": str(b)})
    assert inputremap.rules_for(a) == [{"kind": P, "from": "", "to": str(b)}]
    assert inputremap.state(a)["rules"][0]["target_exists"] is True
    inputremap.remove_rule(a, P, "")
    assert inputremap.rules_for(a) == []
    with pytest.raises(inputremap.RemapError) as err:
        inputremap.remove_rule(a, P, "")
    assert err.value.code == inputremap.ERROR_RULE_UNKNOWN


# --------------------------------------------------------------- 代次（ADR 0106 §五）
def test_concurrent_mutations_keep_both_rules_and_advance_the_generation(tmp_path, monkeypatch):
    """两个窗口同时指认（Codex 评 #716 P2）：读、改、写与换代同一把锁，后写的不吞掉先写的。
    读到旧表之后停一下，让另一个线程也读到同一张旧表——没有锁时后写的一方会整值覆盖。"""
    import threading

    root = tmp_path / "proj"
    root.mkdir()
    real = inputremap.config.project_settings
    both_read = threading.Barrier(2, timeout=0.5)

    def slow_read(r):
        out = real(r)
        try:
            both_read.wait()  # 有锁时第二个线程进不来，等不到：超时后照常往下走
        except threading.BrokenBarrierError:
            pass
        return out

    monkeypatch.setattr(inputremap.config, "project_settings", slow_read)
    before = inputremap.generation(root)
    rules = [
        {"kind": P, "from": "a", "to": str(tmp_path)},
        {"kind": P, "from": "b", "to": str(tmp_path)},
    ]
    threads = [threading.Thread(target=inputremap.add_rule, args=(root, r)) for r in rules]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    got = sorted(r["from"] for r in inputremap.rules_for(root))
    assert got == ["a", "b"]
    assert inputremap.generation(root) == before + 2
    inputremap.remove_rule(root, P, "a")
    assert inputremap.generation(root) == before + 3
    with pytest.raises(inputremap.RemapError):
        inputremap.remove_rule(root, P, "a")  # 没改成：不换代
    assert inputremap.generation(root) == before + 3


def test_landing_refuses_work_started_under_an_older_generation(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    gen, rules = inputremap.snapshot(root)
    assert rules == []
    with inputremap.landing(root, gen, None):  # None：不带代次的工作不拦
        pass
    inputremap.add_rule(root, {"kind": P, "from": "", "to": str(tmp_path)})
    with pytest.raises(inputremap.RemapChanged) as err:
        with inputremap.landing(root, gen):
            raise AssertionError("旧代次的工作不许落地")
    assert err.value.code == inputremap.ERROR_CHANGED


def test_a_probe_that_ran_under_the_old_table_is_not_registered(tmp_path, monkeypatch):
    """试运行途中改了指认（Codex 评 #716 P1）：结果按旧位置的数据来，登记在锁里核对代次——
    丢弃、可重试，注册表零改动。"""
    from tavotto.engine import discover, probe as engine_probe

    root = tmp_path / "proj"
    root.mkdir()
    registered: list = []

    def run_while_the_table_changes(figures_dir, script, should_cancel=None):
        gen = inputremap.generation(figures_dir)  # 起 worker 的那一刻
        inputremap.add_rule(figures_dir, {"kind": P, "from": "", "to": str(tmp_path)})
        return {
            "script": script,
            "entry": "__main__",
            "stems": ["fig"],
            "descriptors": [],
            "tried": ["__main__"],
            "error": None,
            "remap_generation": gen,
        }

    monkeypatch.setattr(engine_probe, "probe", run_while_the_table_changes)
    monkeypatch.setattr(discover, "register", lambda *a, **k: registered.append(a))
    got = engine_probe.probe_and_register(root, "fig.py")
    assert got["registered"] is False
    assert got["error"]["code"] == inputremap.ERROR_CHANGED
    assert registered == []


def test_a_probe_registration_remembers_the_table_it_ran_under(tmp_path, monkeypatch):
    """试运行登记时记下改指表指纹：之后表一变，`registration_stale` 才说得出这条登记要跟着重来。"""
    from tavotto.engine import discover, probe as engine_probe

    root = tmp_path / "proj"
    root.mkdir()

    def ran(figures_dir, script, should_cancel=None):
        return {
            "script": script,
            "entry": "__main__",
            "stems": ["fig"],
            "descriptors": [],
            "tried": ["__main__"],
            "error": None,
            "remap_generation": inputremap.generation(figures_dir),
        }

    monkeypatch.setattr(engine_probe, "probe", ran)
    monkeypatch.setattr(discover, "register", lambda *a, **k: None)
    monkeypatch.setattr(engine_probe.registry, "load", lambda *a, **k: None)
    assert engine_probe.probe_and_register(root, "fig.py")["registered"] is True
    assert not inputremap.registration_stale(root, "fig.py")
    inputremap.add_rule(root, {"kind": P, "from": "", "to": str(tmp_path)})
    assert inputremap.registration_stale(root, "fig.py")


def test_a_pooled_session_from_an_older_table_is_not_reused(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()

    class _W:
        remap_generation = inputremap.generation(root)

    assert engine_pool._remap_current(_W(), str(root))
    inputremap.add_rule(root, {"kind": P, "from": "", "to": str(tmp_path)})
    assert not engine_pool._remap_current(_W(), str(root))


# --------------------------------------------------------------- worker 里的改道（进程内）
@pytest.fixture
def remapped(tmp_path, monkeypatch):
    """在一个空 cwd 里装改指；用例结束卸载，四个入口还原。"""
    box = tmp_path / "box"
    box.mkdir()
    monkeypatch.chdir(box)
    originals = (builtins.open, io.open, pathlib.Path.open)
    installed = []

    def install(rules, **kw):
        misses = figcapture.InputMisses()
        installed.append(figcapture.install_input_remap(rules, misses, str(box), **kw))
        return misses

    yield install
    for undo in reversed(installed):
        undo()
    assert (builtins.open, io.open, pathlib.Path.open) == originals


def test_missing_read_is_served_from_the_rule(remapped, tmp_path):
    _touch(tmp_path / "moved" / "data" / "x.csv", "moved")
    misses = remapped([{"kind": P, "from": "", "to": str(tmp_path / "moved")}])
    assert open("data/x.csv").read() == "moved"
    assert Path("data/x.csv").read_text() == "moved"
    assert misses.items == []


def test_the_original_path_always_wins(remapped, tmp_path):
    _touch(tmp_path / "moved" / "x.csv", "moved")
    _touch(Path("x.csv"), "here")
    remapped([{"kind": P, "from": "", "to": str(tmp_path / "moved")}])
    assert open("x.csv").read() == "here"


def test_writes_are_never_redirected(remapped, tmp_path):
    _touch(tmp_path / "moved" / "out" / "x.csv", "moved")
    misses = remapped([{"kind": P, "from": "", "to": str(tmp_path / "moved")}])
    with pytest.raises(FileNotFoundError):
        open("out/x.csv", "w")
    with pytest.raises(FileNotFoundError):
        open("out/x.csv", "r+")
    assert (tmp_path / "moved" / "out" / "x.csv").read_text() == "moved"
    assert misses.items == [], "写模式的落空不是输入"


def test_unresolved_reads_are_noted_and_raise_the_original_error(remapped, tmp_path):
    misses = remapped([{"kind": P, "from": "", "to": str(tmp_path / "nowhere")}])
    with pytest.raises(FileNotFoundError) as err:
        open("data/x.csv")
    assert err.value.filename == "data/x.csv"
    assert misses.names() == ["data/x.csv"]
    fact = figcapture.missing_input_of(err.value, misses)
    assert fact is not None
    assert fact["requested"] == "data/x.csv" and fact["absolute"] is False


def test_a_miss_normalized_against_the_base_is_matched_after_a_chdir(
    remapped, tmp_path, monkeypatch
):
    """脚本 chdir 进子目录后打开 base 下缺失的**绝对**路径：记账记的是相对 base 的那一段，它相对的
    就是 base，不是此刻的 cwd——按 cwd 拼回去是 base/sub/sub/x.csv，分类对不上（Codex 评 #716 P2）。
    脚本自己写的相对路径仍相对当时的 cwd。"""
    misses = remapped([{"kind": P, "from": "", "to": str(tmp_path / "nowhere")}])
    box = pathlib.Path.cwd()
    (box / "sub").mkdir()
    monkeypatch.chdir(box / "sub")
    with pytest.raises(FileNotFoundError) as err:
        open(pathlib.Path("x.csv").resolve())
    fact = figcapture.missing_input_of(err.value, misses)
    assert fact is not None and fact["requested"] == "sub/x.csv"
    assert os.path.realpath(fact["cwd"]) == os.path.realpath(box)
    with pytest.raises(FileNotFoundError) as err:
        open("y.csv")
    fact = figcapture.missing_input_of(err.value, misses)
    assert fact is not None and fact["requested"] == "y.csv"
    assert os.path.realpath(fact["cwd"]) == os.path.realpath(box / "sub")


def test_a_relpath_computed_with_backslashes_is_shown_with_forward_slashes(
    remapped, tmp_path, monkeypatch
):
    """`os.path.relpath` 在 Windows 上回反斜杠分隔的字符串；记账 / 弹窗展示的名字统一按正斜杠算，
    不随运行的 OS 变出两种拼法（Codex 评 #716 Windows full-ci 红：
    test_a_miss_normalized_against_the_base_is_matched_after_a_chdir 在 Windows 腿上得到
    `'sub\\x.csv'`，和其余平台的 `'sub/x.csv'` 不一致）。这台机器跑不了 Windows，直接把
    `os.path.relpath` 换成一个回反斜杠的版本来模拟。"""
    misses = remapped([])
    real_relpath = os.path.relpath
    monkeypatch.setattr(
        os.path, "relpath", lambda *a, **kw: real_relpath(*a, **kw).replace("/", "\\")
    )
    box = pathlib.Path.cwd()
    (box / "sub").mkdir()
    with pytest.raises(FileNotFoundError) as err:
        open(str(box / "sub" / "x.csv"))
    fact = figcapture.missing_input_of(err.value, misses)
    assert fact is not None and fact["requested"] == "sub/x.csv"


def test_an_absolute_path_the_script_wrote_keeps_its_absolute_identity(remapped, tmp_path):
    """`cwd_mode=project`：脚本显式写的 `<项目>/data/x.csv`（落在 cwd 里）缺了——一条宽泛的相对规则不许悄悄接管它，
    落空也按**绝对**路径记（弹窗据此推出的是绝对的 `from`，不是影响全项目相对读取的 `from=""`，Codex 评 #716 P1）。
    证据是脚本源码：只有相对常量能认领、绝对常量没认领的，才当成相对路径被库规范化出来的（PIL 那种）。"""
    _touch(tmp_path / "moved" / "data" / "x.csv", "moved")
    box = pathlib.Path.cwd()
    absolute = str(box / "data" / "x.csv")
    rules = [{"kind": P, "from": "", "to": str(tmp_path / "moved")}]
    # 脚本里恰好也有个 `"data"` 常量（字典键）：相对常量能认领它，但绝对常量认领在先——仍是绝对身份
    source = f"import pandas as pd\ncfg = {{'data': 1}}\npd.read_csv({absolute!r})\n"
    misses = remapped(rules, script_source=source)
    with pytest.raises(FileNotFoundError) as err:
        open(absolute)
    fact = figcapture.missing_input_of(err.value, misses)
    assert fact is not None and fact["requested"] == absolute
    # 同一个绝对串，脚本写的是相对的 `data/x.csv`（库先 realpath 再打开）：仍按相对那一段查表、被规则救回
    remapped(rules, script_source='from PIL import Image\nImage.open("data/x.csv")\n')
    assert open(absolute).read() == "moved"


def test_a_path_built_from_file_dunder_keeps_its_absolute_identity(remapped, tmp_path):
    """`Path(__file__).parent / "data" / "x.csv"`：`"data"`、`"x.csv"` 是拼绝对路径用的片段，不是
    「脚本写着相对路径 data/x.csv」的证据——宽泛的相对规则不许接管它（Codex 评 #716 P1
    "Require actual relative-use evidence before remapping"）。"""
    _touch(tmp_path / "moved" / "data" / "x.csv", "moved")
    box = pathlib.Path.cwd()
    absolute = str(box / "data" / "x.csv")
    rules = [{"kind": P, "from": "", "to": str(tmp_path / "moved")}]
    source = "from pathlib import Path\np = Path(__file__).parent / 'data' / 'x.csv'\n"
    misses = remapped(rules, script_source=source)
    with pytest.raises(FileNotFoundError) as err:
        open(absolute)
    fact = figcapture.missing_input_of(err.value, misses)
    assert fact is not None and fact["requested"] == absolute


def test_a_path_built_with_os_path_join_and_dirname_keeps_its_absolute_identity(remapped, tmp_path):
    """同上，换成 `os.path.join(os.path.dirname(__file__), "data", "x.csv")` 这种拼法。"""
    _touch(tmp_path / "moved" / "data" / "x.csv", "moved")
    box = pathlib.Path.cwd()
    absolute = str(box / "data" / "x.csv")
    rules = [{"kind": P, "from": "", "to": str(tmp_path / "moved")}]
    source = "import os\np = os.path.join(os.path.dirname(__file__), 'data', 'x.csv')\n"
    misses = remapped(rules, script_source=source)
    with pytest.raises(FileNotFoundError) as err:
        open(absolute)
    fact = figcapture.missing_input_of(err.value, misses)
    assert fact is not None and fact["requested"] == absolute


def test_path_literals_excludes_fragments_used_to_build_an_absolute_path():
    """`path_literals` 本身的单测：绝对锚点后面的拼接片段一个都不进相对/绝对证据。"""
    abs_lits, rel_lits = figcapture.path_literals(
        "from pathlib import Path\np = Path(__file__).parent / 'data' / 'x.csv'\n"
    )
    assert abs_lits == [] and rel_lits == []
    abs_lits, rel_lits = figcapture.path_literals(
        "import os\np = os.path.join(os.path.dirname(__file__), 'data', 'x.csv')\n"
    )
    assert abs_lits == [] and rel_lits == []


def test_path_literals_still_counts_a_join_with_no_absolute_anchor():
    """`os.path.join("data", "x.csv")` 拼出来的结果本身就是相对路径：没有绝对锚点时两段仍然算数。"""
    abs_lits, rel_lits = figcapture.path_literals("import os\np = os.path.join('data', 'x.csv')\n")
    assert abs_lits == []
    assert rel_lits == [("data",), ("x.csv",)]


def test_a_path_built_from_an_anchor_assigned_to_a_local_keeps_its_absolute_identity(
    remapped, tmp_path
):
    """`root = Path(__file__).parent; root / "data" / "x.csv"`：把绝对锚点先赋给局部变量再拼接，
    拼接的操作数是一个解析不了的名字（`Name("root")`），不是「已知的相对常量」——不去追踪 `root`
    是不是绝对锚点的赋值，解析不了就不算数（Codex 评 #716 第二轮 P1
    "Reject unresolved anchors as relative-path evidence"）。"""
    _touch(tmp_path / "moved" / "data" / "x.csv", "moved")
    box = pathlib.Path.cwd()
    absolute = str(box / "data" / "x.csv")
    rules = [{"kind": P, "from": "", "to": str(tmp_path / "moved")}]
    source = "from pathlib import Path\nroot = Path(__file__).parent\np = root / 'data' / 'x.csv'\n"
    misses = remapped(rules, script_source=source)
    with pytest.raises(FileNotFoundError) as err:
        open(absolute)
    fact = figcapture.missing_input_of(err.value, misses)
    assert fact is not None and fact["requested"] == absolute


def test_a_path_built_with_os_path_join_of_an_assigned_dirname_keeps_its_absolute_identity(
    remapped, tmp_path
):
    """同上，换成 `base = os.path.dirname(__file__); os.path.join(base, "data", "x.csv")`。"""
    _touch(tmp_path / "moved" / "data" / "x.csv", "moved")
    box = pathlib.Path.cwd()
    absolute = str(box / "data" / "x.csv")
    rules = [{"kind": P, "from": "", "to": str(tmp_path / "moved")}]
    source = (
        "import os\nbase = os.path.dirname(__file__)\np = os.path.join(base, 'data', 'x.csv')\n"
    )
    misses = remapped(rules, script_source=source)
    with pytest.raises(FileNotFoundError) as err:
        open(absolute)
    fact = figcapture.missing_input_of(err.value, misses)
    assert fact is not None and fact["requested"] == absolute


def test_path_literals_excludes_fragments_behind_an_assigned_anchor():
    """`path_literals` 单测：锚点先赋给局部变量再拼接，`Name` 操作数一样建不了相对证据。"""
    abs_lits, rel_lits = figcapture.path_literals(
        "from pathlib import Path\nroot = Path(__file__).parent\np = root / 'data' / 'x.csv'\n"
    )
    assert abs_lits == [] and rel_lits == []
    abs_lits, rel_lits = figcapture.path_literals(
        "import os\nbase = os.path.dirname(__file__)\np = os.path.join(base, 'data', 'x.csv')\n"
    )
    assert abs_lits == [] and rel_lits == []


def test_a_compound_operand_behind_an_absolute_anchor_keeps_its_absolute_identity(
    remapped, tmp_path
):
    """`root / Path("data") / "x.csv"`：绝对锚点后面跟复合操作数，`Path("data")` 里嵌套的字面量
    不是「脚本写过相对路径 data」——整个操作数子树都不算证据（Codex 评 #716 P1
    "Suppress nested literals under unresolved join operands"）。"""
    _touch(tmp_path / "moved" / "data" / "x.csv", "moved")
    box = pathlib.Path.cwd()
    absolute = str(box / "data" / "x.csv")
    rules = [{"kind": P, "from": "", "to": str(tmp_path / "moved")}]
    source = (
        "from pathlib import Path\nroot = Path(__file__).parent\n"
        "p = root / Path('data') / 'x.csv'\n"
    )
    misses = remapped(rules, script_source=source)
    with pytest.raises(FileNotFoundError) as err:
        open(absolute)
    fact = figcapture.missing_input_of(err.value, misses)
    assert fact is not None and fact["requested"] == absolute


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\nroot = Path(__file__).parent\np = root / Path('data') / 'x.csv'\n",
        "from pathlib import Path\nroot = Path(__file__).parent\np = root / str('data') / 'x.csv'\n",
        "from pathlib import Path\nroot = Path(__file__).parent\np = root / ('da' + 'ta') / 'x.csv'\n",
        "from pathlib import Path\nroot = Path(__file__).parent\np = root / f'data{1}' / 'x.csv'\n",
        "import os\nbase = os.path.dirname(__file__)\np = os.path.join(base, os.path.join('data'), 'x.csv')\n",
        "import os\np = os.path.join(os.path.dirname(__file__), str('data'), 'x.csv')\n",
    ],
)
def test_path_literals_excludes_nested_literals_under_a_non_constant_operand(source):
    """锚点后面的操作数是调用 / 拼接 / f-string 时，嵌套在里面的字符串常量一样不进证据。"""
    abs_lits, rel_lits = figcapture.path_literals(source)
    assert abs_lits == [] and rel_lits == []


def test_numpy_loadtxt_is_remapped_and_named_without_a_filename(remapped, tmp_path):
    np = pytest.importorskip("numpy")
    _touch(tmp_path / "moved" / "d.txt", "1 2 3")
    remapped([{"kind": P, "from": "", "to": str(tmp_path / "moved")}])
    assert np.loadtxt("d.txt").tolist() == [1.0, 2.0, 3.0]
    # numpy 自己的 "x not found."——没有 filename，靠落空记账说出是哪个
    misses = remapped([])
    with pytest.raises(FileNotFoundError) as err:
        np.loadtxt("absent.txt")
    fact = figcapture.missing_input_of(err.value, misses)
    assert fact is not None and fact["requested"] == "absent.txt"


def test_a_file_not_found_that_was_not_a_read_is_not_claimed(remapped):
    misses = remapped([])
    with pytest.raises(FileNotFoundError):
        open("x.csv")  # 一次只读落空……
    try:
        open("no_dir/out.csv", "w")  # ……但抛出去的是写
    except FileNotFoundError as exc:
        assert figcapture.missing_input_of(exc, misses) is None
    # 没有任何落空记账：不判
    assert (
        figcapture.missing_input_of(FileNotFoundError(2, "x", "y.csv"), figcapture.InputMisses())
        is None
    )


# --------------------------------------------------------------- 执行描述
def _spec(rules=()):
    return execspec.safe_spec(
        "fig.py", "/proj", "main", interpreter="/usr/bin/python3", sandbox="/box", input_remap=rules
    )


def test_no_rules_leaves_the_argv_byte_for_byte():
    plain = execspec.worker_argv(_spec(), worker_py="/w.py", out_dir="/o")
    rules = [{"kind": P, "from": "", "to": "/data"}]
    with_rules = execspec.worker_argv(_spec(rules), worker_py="/w.py", out_dir="/o")
    assert "--input-remap" not in plain
    assert with_rules[: len(plain)] == plain
    assert with_rules[len(plain)] == "--input-remap"
    # 规则是本机路径：不进跨机器稳定的那一档
    assert _spec(rules).stable_payload() == _spec().stable_payload()
    assert execspec.spec_from_payload(_spec(rules).to_payload()) == _spec(rules)


# --------------------------------------------------------------- 真 worker
SCRIPT_REL = """\
import matplotlib.pyplot as plt
with open("data/values.txt") as f:
    value = f.read().strip()
print(f"[value] {value}")
fig, ax = plt.subplots()
ax.set_title(value)
fig.savefig("values.png")
"""


@pytest.fixture
def figs(tmp_path):
    root = tmp_path / "figs"
    root.mkdir()
    yield root
    engine_pool.shutdown_all(str(root), wait=True)
    for r in inputremap.rules_for(root):
        inputremap.remove_rule(root, r["kind"], r["from"])


@needs_worker
def test_relative_data_missing_then_pointed_at(figs, tmp_path):
    (figs / "fig.py").write_text(SCRIPT_REL, encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    # FO08 的反例：项目里别处有一份同名不同值的文件——不搜同名，只认用户指认的
    _touch(figs / "old" / "data" / "values.txt", "decoy")
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("fig.py", str(figs), "__main__")
    assert err.value.code == engine_pool.MISSING_INPUT_CODE
    offer = err.value.missing_input
    assert offer is not None and offer["requested"] == "data/values.txt"
    assert offer["absolute"] is False and offer["script"] == "fig.py"

    chosen = _touch(tmp_path / "moved" / "data" / "values.txt", "truth-42")
    inputremap.add_rule(
        figs, inputremap.derive("data/values.txt", str(chosen), chosen_is_dir=False)
    )
    engine_pool.shutdown_all(str(figs), wait=True)
    worker, resp = engine_pool.build("fig.py", str(figs), "__main__")
    assert sorted(resp.get("stems") or {}) == ["values"]
    assert "[value] truth-42" in worker._log_tail()
    assert worker.spec.input_remap == tuple(inputremap.rules_for(figs))


@needs_worker
def test_absolute_data_moved_then_pointed_at(figs, tmp_path):
    gone = tmp_path / "gone" / "proj" / "data" / "values.txt"
    (figs / "fig.py").write_text(
        SCRIPT_REL.replace('"data/values.txt"', repr(gone.as_posix())), encoding="utf-8"
    )
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("fig.py", str(figs), "__main__")
    assert err.value.code == engine_pool.MISSING_INPUT_CODE
    assert err.value.missing_input["requested"] == gone.as_posix()
    assert err.value.missing_input["absolute"] is True

    chosen = _touch(tmp_path / "new" / "proj" / "data" / "values.txt", "truth-7")
    inputremap.add_rule(figs, inputremap.derive(gone.as_posix(), str(chosen), chosen_is_dir=False))
    engine_pool.shutdown_all(str(figs), wait=True)
    worker, resp = engine_pool.build("fig.py", str(figs), "__main__")
    assert sorted(resp.get("stems") or {}) == ["values"]
    assert "[value] truth-7" in worker._log_tail()
    # 原路径回来了：规则不起作用，读原件
    _touch(gone, "original")
    engine_pool.shutdown_all(str(figs), wait=True)
    worker, _ = engine_pool.build("fig.py", str(figs), "__main__")
    assert "[value] original" in worker._log_tail()


@needs_worker
def test_the_same_rules_hold_in_the_script_dir_mode(figs, tmp_path):
    (figs / "fig.py").write_text(SCRIPT_REL, encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_PROJECT)
    chosen = _touch(tmp_path / "moved" / "data" / "values.txt", "truth-3")
    inputremap.add_rule(
        figs, inputremap.derive("data/values.txt", str(chosen), chosen_is_dir=False)
    )
    try:
        worker, resp = engine_pool.build("fig.py", str(figs), "__main__")
        assert "[value] truth-3" in worker._log_tail()
    finally:
        workdir.set_mode(figs, workdir.MODE_SANDBOX)


@needs_worker
def test_a_script_that_checks_first_gets_the_static_list(figs):
    (figs / "fig.py").write_text(
        'import os\nif os.path.exists("data/values.txt"):\n'
        "    import matplotlib.pyplot as plt\n    plt.plot([1])\n"
        'else:\n    print("找不到数据")\n',
        encoding="utf-8",
    )
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    worker, resp = engine_pool.build("fig.py", str(figs), "__main__")
    assert resp.get("stems") == {}
    err = engine_pool.missing_stem_error(worker, "fig", [])
    assert err is not None and err.code == engine_pool.NO_FIGURES_CODE
    assert err.missing_input is not None and err.missing_input["requested"] is None
    assert err.missing_input["others"] == [
        {
            "path": "data/values.txt",
            "absolute": False,
            "via": inputremap.VIA_PROBE,
            "probe_kind": inputremap.PROBE_ANY,
        }
    ]


@needs_worker
def test_a_script_that_checks_then_exits_gets_the_static_list(figs):
    """最常见的写法是判空后自己 `sys.exit("找不到数据")`——码是 `script_exited`，同样挂静态载荷。"""
    (figs / "fig.py").write_text(
        'import os, sys\nif not os.path.exists("data/values.txt"):\n    sys.exit("找不到数据")\n',
        encoding="utf-8",
    )
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("fig.py", str(figs), "__main__")
    assert err.value.code == engine_pool.SCRIPT_EXITED_CODE
    assert err.value.missing_input is not None
    assert err.value.missing_input["others"] == [
        {
            "path": "data/values.txt",
            "absolute": False,
            "via": inputremap.VIA_PROBE,
            "probe_kind": inputremap.PROBE_ANY,
        }
    ]


def test_absolute_paths_that_are_only_probed_get_no_picker(tmp_path):
    """绝对路径被 `exists()` / `listdir()` / `glob()` / `Path(...).exists()` 问的是探路，不是读：改指表救不回，
    给了选择器就是死循环（指认 → 重跑 → `exists()` 照样 False → 同一个框再弹）。同一串还被读过也算探路；
    先赋给一个只赋值一次的名字再探（`DATA = "…"; exists(DATA)`）同样算。"""
    (tmp_path / "fig.py").write_text(
        "import glob, os\nfrom pathlib import Path\n"
        'if not os.path.exists("/nonexistent/tavotto/a/x.csv"):\n    raise SystemExit(1)\n'
        'os.listdir("/nonexistent/tavotto/b/runs")\n'
        'glob.glob("/nonexistent/tavotto/c/*.csv")\n'
        'Path("/nonexistent/tavotto/d/y.csv").exists()\n'
        'open("/nonexistent/tavotto/e/z.csv")\n'
        'open("/nonexistent/tavotto/a/x.csv")\n'
        'DATA = "/nonexistent/tavotto/f/w.csv"\n'  # 最常见的写法：常量先进名字，再被探
        "if not os.path.exists(DATA):\n    raise SystemExit(1)\n"
        'TWICE = "/nonexistent/tavotto/g/v.csv"\nTWICE = "/nonexistent/tavotto/g/u.csv"\n'
        "os.path.exists(TWICE)\n"  # 赋值两次：说不清探的是哪个，不跟
        # 别名与首开探路同一份（`databinding._Aliases`）：换个名字 import 照样是探路
        "import glob as g\nfrom glob import iglob as gg\nfrom pathlib import Path as P\n"
        'g.glob("/nonexistent/tavotto/h/*.csv")\n'
        'gg("/nonexistent/tavotto/i/*.csv")\n'
        'P("/nonexistent/tavotto/j/t.csv").exists()\n'
        # glob 的起算目录 `root_dir=` 同样是 glob 在问（模式是变量也一样）
        'glob.glob("*.csv", root_dir="/nonexistent/tavotto/k")\n'
        'ROOT = "/nonexistent/tavotto/l"\npat = "*.csv"\ng.glob(pat, root_dir=ROOT)\n',
        encoding="utf-8",
    )
    got = {o["path"]: o["via"] for o in inputremap.static_missing("fig.py", tmp_path, [])}
    assert got == {
        "/nonexistent/tavotto/a/x.csv": inputremap.VIA_PROBE,
        "/nonexistent/tavotto/b/runs": inputremap.VIA_PROBE,
        "/nonexistent/tavotto/c/*.csv": inputremap.VIA_GLOB,
        "/nonexistent/tavotto/d/y.csv": inputremap.VIA_PROBE,
        "/nonexistent/tavotto/e/z.csv": inputremap.VIA_OPEN,
        "/nonexistent/tavotto/f/w.csv": inputremap.VIA_PROBE,
        # 赋值两次的 TWICE：说不清被探的是哪一个，两个值又都不在任何读取调用里——不是能证明的输入，不列
        "/nonexistent/tavotto/h/*.csv": inputremap.VIA_GLOB,
        "/nonexistent/tavotto/i/*.csv": inputremap.VIA_GLOB,
        "/nonexistent/tavotto/j/t.csv": inputremap.VIA_PROBE,
        "/nonexistent/tavotto/k": inputremap.VIA_GLOB,
        "/nonexistent/tavotto/l": inputremap.VIA_GLOB,
    }


def test_only_arguments_of_read_calls_count_as_missing_data(tmp_path):
    """「一并修好」里只列脚本**真要读**的（用户 09-29 截图）：坐标轴标签、存图 / 写出的目标、写模式打开、
    `.py`、当输出目录用的路径都不是缺的数据；`color.txt` 这类名字按用法——只出现在写出调用里的不算。
    从来源判（进没进读取调用），不按字符串长相事后去猜。"""
    (tmp_path / "fig.py").write_text(
        "import os, numpy as np, pandas as pd, matplotlib.pyplot as plt\n"
        "from pathlib import Path\n"
        'OUT = "数据汇总/part2/绘图缓存"\n'
        "os.makedirs(OUT, exist_ok=True)\n"
        'ax = plt.gca(); ax.set_xlabel("Distance $z$ ($\\mu$m)")\n'
        'ax.set_ylabel("rate/day (a.u.)")\n'
        'plt.savefig(os.path.join(OUT, "fig2.png"))\n'
        'plt.savefig("results/fig1.pdf")\n'
        'with open("color.txt", "w") as f:\n    f.write("red")\n'
        'open("logs/stream.txt", mode="a").write("x")\n'
        'exec(open("helpers/util.py").read()) if False else None\n'
        'np.savetxt("out/table.csv", [1])\n'
        # 真要读的：相对 / 绝对 / 拼出来的 / 先赋给名字的 / Path 的读
        'df = pd.read_csv("data/points.csv")\n'
        'arr = np.loadtxt("/nonexistent/tavotto/raw/a.dat")\n'
        'SRC = "/nonexistent/tavotto/raw/b.csv"\n'
        "b = pd.read_csv(SRC)\n"
        'c = Path("inputs/c.json").read_text()\n'
        'd = open(os.path.join("inputs", "d.txt")).read()\n'
        # 目录常量拼出来的：打头的那一段算（它是那条路径的前缀）
        'RAW = "/nonexistent/tavotto/raw2"\n'
        'e = np.load(os.path.join(RAW, "e.npy"))\n'
        'f = pd.read_csv(Path("inputs") / "f.csv")\n',
        encoding="utf-8",
    )
    got = sorted(o["path"] for o in inputremap.static_missing("fig.py", tmp_path))
    assert got == sorted(
        [
            "data/points.csv",
            "/nonexistent/tavotto/raw/a.dat",
            "/nonexistent/tavotto/raw/b.csv",
            "inputs/c.json",
            "/nonexistent/tavotto/raw2",
        ]
    ), got
    # `open(os.path.join("inputs", "d.txt"))`：打头的 `inputs` 不像数据路径、后面的 `d.txt` 只是片段——不列，
    # 运行时 worker 会说出真正缺的那一串；`helpers/util.py` 是在读，但读的是代码不是数据


def test_the_requested_path_keeps_the_probe_classification_of_the_same_literal(tmp_path):
    """运行时缺的那一串在脚本里也被 `exists()` 问过（`present = exists("x.csv"); open("x.csv")`）：顶层
    `via` 跟着静态那一条走，不给选择器——改指只救得回 open，探路照样落空、脚本照样退出（Codex 评 #716 P2）。"""
    (tmp_path / "fig.py").write_text(
        'import os, sys\npresent = os.path.exists("x.csv")\ndata = open("x.csv").read()\n'
        "if not present:\n    sys.exit(1)\n"
        'open("y.csv")\n',
        encoding="utf-8",
    )
    got = inputremap.payload_for("fig.py", tmp_path, {"requested": "x.csv", "absolute": False})
    assert got is not None and got["via"] == inputremap.VIA_PROBE
    assert [o["path"] for o in got["others"]] == ["y.csv"]
    # 只被 open 读的那一串：照旧给选择器
    got = inputremap.payload_for("fig.py", tmp_path, {"requested": "y.csv", "absolute": False})
    assert got is not None and got["via"] == inputremap.VIA_OPEN


@needs_worker
def test_a_plain_script_error_stays_a_script_error(figs):
    (figs / "fig.py").write_text("raise ValueError('boom')\n", encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("fig.py", str(figs), "__main__")
    assert err.value.code == "script_error"
    assert err.value.missing_input is None


# --------------------------------------------------------------- HTTP
@pytest.fixture
def client():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    return m.app.test_client()


def test_runtime_materialization_from_an_older_generation_is_dropped(
    client, figs, tmp_path, monkeypatch
):
    """试运行 / 渲染途中改了指认：物化在改指表的锁里核对代次——旧代次的预览不写进 cache，也就不会带着
    **新**表的指纹被当成新鲜（Codex 评 #716 P1）。代次对得上照常物化。"""
    import types

    from tavotto import app as m

    m.open_project(str(figs))
    try:
        materialized: list = []
        monkeypatch.setattr(
            m.engine_runtimeasset, "materialize", lambda *a, **k: materialized.append(a)
        )
        monkeypatch.setattr(
            m, "_safe_worker", lambda *a, **k: types.SimpleNamespace(svg_path=lambda st: figs / st)
        )
        old = inputremap.generation(figs)
        inputremap.add_rule(figs, {"kind": P, "from": "", "to": str(tmp_path)})
        with m.app.test_request_context():
            m._materialize_runtime("fig.py", "__main__", [{"stem": "fig"}], remap_generation=old)
            assert materialized == []
            m._materialize_runtime(
                "fig.py",
                "__main__",
                [{"stem": "fig"}],
                remap_generation=inputremap.generation(figs),
            )
        assert len(materialized) == 1
    finally:
        m.close_project(
            next(p for p, c in m.PROJECTS.items() if str(c.path) == str(figs)), wait=True
        )


def test_http_add_list_and_remove(client, figs, tmp_path):
    from tavotto import app as m

    m.open_project(str(figs))
    try:
        chosen = _touch(tmp_path / "moved" / "data" / "values.txt")
        resp = client.post(
            "/api/engine/input-remap",
            json={"requested": "data/values.txt", "chosen": str(chosen), "chosen_kind": "file"},
        )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["rule"] == {"kind": P, "from": "", "to": str(tmp_path / "moved")}
        env = client.get("/api/engine/environment").get_json()
        assert env["project"]["input_remap"]["rules"][0]["to"] == str(tmp_path / "moved")
        resp = client.post(
            "/api/engine/input-remap",
            json={"requested": "data/z.txt", "chosen": str(tmp_path), "chosen_kind": "dir"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["code"] == inputremap.ERROR_NOT_FOUND_IN_DIR
        assert resp.get_json()["params"]["name"] == "z.txt"
        resp = client.delete("/api/engine/input-remap", json={"kind": P, "from": ""})
        assert resp.status_code == 200 and resp.get_json()["input_remap"]["rules"] == []
        resp = client.delete("/api/engine/input-remap", json={"kind": P, "from": ""})
        assert resp.get_json()["code"] == inputremap.ERROR_RULE_UNKNOWN
    finally:
        for pid in [p for p, ctx in list(m.PROJECTS.items()) if str(ctx.path) == str(figs)]:
            m.close_project(pid, wait=True)


def test_every_window_of_the_project_hears_that_the_remap_table_changed(client, figs, tmp_path):
    """两个窗口开着同一个项目（两条事件流）：A 改指 / 删指，B 同样收到 `input_remap_changed`（带代次），
    据此作废按旧表画的东西（Codex 评 #716 P1）。事件带的代次与接口响应里的一致，前端据此去重。"""
    import queue as _queue

    from tavotto import app as m

    m.open_project(str(figs))
    a, b = _queue.Queue(), _queue.Queue()
    m._sse_subs.extend([a, b])
    try:
        chosen = _touch(tmp_path / "moved" / "data" / "values.txt")
        resp = client.post(
            "/api/engine/input-remap",
            json={"requested": "data/values.txt", "chosen": str(chosen), "chosen_kind": "file"},
        )
        assert resp.status_code == 200, resp.get_json()
        added_gen = resp.get_json()["input_remap"]["generation"]
        resp = client.delete("/api/engine/input-remap", json={"kind": P, "from": ""})
        assert resp.status_code == 200
        removed_gen = resp.get_json()["input_remap"]["generation"]

        def remap_events(q):
            out = []
            while not q.empty():
                ev, data = q.get_nowait()
                if ev == "input_remap_changed":
                    out.append(data)
            return out

        got_a, got_b = remap_events(a), remap_events(b)
        assert [e["reason"] for e in got_b] == ["added", "removed"]
        assert got_a == got_b
        assert got_b[1]["generation"] == got_b[0]["generation"] + 1
        assert got_b[1]["generation"] == inputremap.generation(figs)
        # 发起的窗口按接口响应本地作废的那一代，就是事件里的那一代
        assert [e["generation"] for e in got_b] == [added_gen, removed_gen]
        assert {e["pj"] for e in got_b} == {m.current_ctx().id}
    finally:
        for q in (a, b):
            if q in m._sse_subs:
                m._sse_subs.remove(q)
        for pid in [p for p, ctx in list(m.PROJECTS.items()) if str(ctx.path) == str(figs)]:
            m.close_project(pid, wait=True)


def test_probe_registered_stems_follow_the_table_after_a_rebuild(
    client, figs, tmp_path, monkeypatch
):
    """试运行在旧表下登记了 `group_A`；改指之后按新数据只捕获得到 `group_B`。这个脚本的会话按新表 build 之后
    （渲染里、`unknown_stem` 或成功都一样），注册表按这次 build 的真实产出重新登记，自动重渲染不再一直
    `unknown_stem`（Codex 评 #716 P1）。不多跑一次脚本；代次不对、没记过的不动。"""
    import types

    from tavotto import app as m
    from tavotto.engine import discover, registry

    m.open_project(str(figs))
    try:
        (figs / "fig.py").write_text("print(1)\n", encoding="utf-8")
        discover.register(figs, "fig.py", ["group_A"], entry="__main__")
        m.refresh_project(m.current_ctx(), reason="probe", allow_static_merge=False)
        inputremap.record_registration(figs, "fig.py")  # 在「空表」下登记的
        inputremap.add_rule(figs, {"kind": P, "from": "", "to": str(tmp_path)})

        def stems_on_disk():
            path = registry.existing_registry_path(figs)
            return json.loads(path.read_text(encoding="utf-8"))["scripts"]["fig.py"]["stems"]

        def worker(gen):
            return types.SimpleNamespace(
                script_name="fig.py",
                figures_dir=str(figs),
                entry="__main__",
                remap_generation=gen,
                last_build_descriptors=[{"stem": "group_B"}],
            )

        ctx = m.current_ctx()
        stale_gen = inputremap.generation(figs) - 1
        assert m._resync_registration(ctx, worker(stale_gen)) is False  # 按旧表 build 的：不动
        assert stems_on_disk() == ["group_A"]
        assert m._resync_registration(ctx, worker(inputremap.generation(figs))) is True
        assert stems_on_disk() == ["group_B"]
        assert not inputremap.registration_stale(figs, "fig.py")
        # 再来一次：已对上，不重复登记
        assert m._resync_registration(ctx, worker(inputremap.generation(figs))) is False
        # 没登记过的脚本不在这里替它登记（那是试运行 / 发现的事）
        (figs / "other.py").write_text("print(2)\n", encoding="utf-8")
        other = worker(inputremap.generation(figs))
        other.script_name = "other.py"
        assert m._resync_registration(ctx, other) is False
        assert "other.py" not in ctx.registry.all_scripts()
    finally:
        for pid in [p for p, c in list(m.PROJECTS.items()) if str(c.path) == str(figs)]:
            m.close_project(pid, wait=True)


def test_a_remap_that_yields_no_figures_removes_the_old_registration(client, figs, tmp_path):
    """改指之后按新数据一张图都没出（`no_figures_captured`）：这也是按新表跑出的权威结果——旧 stems 整条摘掉，
    素材库不再挂着必然失败的条目（Codex 评 #716 P2）。没跑完的 build 说明不了什么，不动；没改指的项目不受影响。"""
    import types

    from tavotto import app as m
    from tavotto.engine import discover, registry

    m.open_project(str(figs))
    try:
        (figs / "fig.py").write_text("print(1)\n", encoding="utf-8")
        discover.register(figs, "fig.py", ["group_A"], entry="__main__")
        ctx = m.current_ctx()
        m.refresh_project(ctx, reason="probe", allow_static_merge=False)
        inputremap.record_registration(figs, "fig.py")

        def scripts_on_disk():
            path = registry.existing_registry_path(figs)
            return json.loads(path.read_text(encoding="utf-8"))["scripts"]

        def worker(**kw):
            return types.SimpleNamespace(
                script_name="fig.py",
                figures_dir=str(figs),
                entry="__main__",
                remap_generation=inputremap.generation(figs),
                last_build_descriptors=[],
                built=True,
                build_failed=False,
                **kw,
            )

        # 表没变：一张没出也不动登记（不是改指引起的）
        assert m._resync_registration(ctx, worker()) is False
        assert "fig.py" in scripts_on_disk()
        inputremap.add_rule(figs, {"kind": P, "from": "", "to": str(tmp_path)})
        failed = worker()
        failed.build_failed = True
        assert m._resync_registration(ctx, failed) is False  # build 没跑完
        assert "fig.py" in scripts_on_disk()
        assert m._resync_registration(ctx, worker()) is True
        assert "fig.py" not in scripts_on_disk()
        assert "fig.py" not in m.current_ctx().registry.all_scripts()
        # 渲染入口看到的是换过的码：`no_figures_captured*` 同样触发重新登记
        assert m.engine_pool.NO_FIGURES_CODE in m._RESYNC_RENDER_CODES
        assert m.engine_pool.NO_FIGURES_SILENT_CODE in m._RESYNC_RENDER_CODES
    finally:
        for pid in [p for p, c in list(m.PROJECTS.items()) if str(c.path) == str(figs)]:
            m.close_project(pid, wait=True)


def test_a_probe_only_miss_stays_offered_when_a_broad_rule_resolves_it(tmp_path):
    """一条宽泛的改指规则碰巧让某条只被 exists / glob 问过的路径「解析得了」：探路不查改指表，脚本照样走
    「不存在」那一支——这一条仍要在载荷里，按「改指救不回」说（Codex 评 #716 P2）。真读取（open）的照旧被压掉。"""
    root = tmp_path / "proj"
    root.mkdir()
    moved = tmp_path / "moved"
    _touch(moved / "data" / "values.txt")
    _touch(moved / "data" / "flag.txt")
    (root / "fig.py").write_text(
        "import os\n"
        'if not os.path.exists("data/flag.txt"):\n'
        "    raise SystemExit(1)\n"
        'open("data/values.txt").read()\n',
        encoding="utf-8",
    )
    rules = [{"kind": P, "from": "", "to": str(moved)}]
    got = {o["path"]: o["via"] for o in inputremap.static_missing("fig.py", root, rules)}
    assert got == {"data/flag.txt": inputremap.VIA_PROBE}
    before = {o["path"] for o in inputremap.static_missing("fig.py", root, [])}
    assert before == {"data/flag.txt", "data/values.txt"}


def test_a_legacy_registration_without_a_mark_reconciles_conservatively(
    client, figs, tmp_path, monkeypatch
):
    """升级前登记的脚本没有指纹标记（Codex 评 #716 P1）：项目里有任何改指规则，就不知道它是在哪张表下登记的，
    按过期处理——下一次 build 按真实产出重新登记一次；没有规则的维持原样（只可能是在「无表」下登记的）。"""
    import types

    from tavotto import app as m
    from tavotto.engine import discover, registry

    m.open_project(str(figs))
    try:
        (figs / "fig.py").write_text("print(1)\n", encoding="utf-8")
        discover.register(figs, "fig.py", ["group_A"], entry="__main__")
        ctx = m.current_ctx()
        m.refresh_project(ctx, reason="probe", allow_static_merge=False)
        assert inputremap.REGISTERED_KEY not in inputremap.config.project_settings(str(figs))

        def stems_on_disk():
            path = registry.existing_registry_path(figs)
            return json.loads(path.read_text(encoding="utf-8"))["scripts"]["fig.py"]["stems"]

        def worker():
            return types.SimpleNamespace(
                script_name="fig.py",
                figures_dir=str(figs),
                entry="__main__",
                remap_generation=inputremap.generation(figs),
                last_build_descriptors=[{"stem": "group_B"}],
            )

        # 无规则：没标记也不动
        assert not inputremap.registration_stale(figs, "fig.py")
        assert m._resync_registration(ctx, worker()) is False
        assert stems_on_disk() == ["group_A"]
        # 有规则、没标记：保守对账，按这次 build 重新登记并补上标记
        inputremap.add_rule(figs, {"kind": P, "from": "", "to": str(tmp_path)})
        assert inputremap.registration_stale(figs, "fig.py")
        assert m._resync_registration(ctx, worker()) is True
        assert stems_on_disk() == ["group_B"]
        assert not inputremap.registration_stale(figs, "fig.py")
        assert m._resync_registration(ctx, worker()) is False
    finally:
        for pid in [p for p, c in list(m.PROJECTS.items()) if str(c.path) == str(figs)]:
            m.close_project(pid, wait=True)


def test_a_remap_during_the_writeback_replace_loop_waits_for_the_commit(
    client, tmp_path, monkeypatch
):
    """写回在替换循环中途（第一个目标已换、第二个还没换）时，另一个窗口改了指认（Codex 评 #716 P1）：改指持项目锁，
    要等这次提交整个落完才能改表、换代——写回不会一半按旧表、一半在新表之后落地。barrier 把写回钉在替换中途。"""
    import threading

    import pymupdf

    from tavotto import app as m

    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "Fig1.pdf")
    doc.close()
    (figs / "Fig1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (figs / "tavotto_registry.json").write_text(
        json.dumps({"version": 1, "scripts": {"fig1.py": {"entry": "main", "stems": ["Fig1"]}}}),
        encoding="utf-8",
    )
    (figs / "fig1.py").write_text("def main():\n    pass\n", encoding="utf-8")
    payload = {"pdf": (figs / "Fig1.pdf").read_bytes(), "png": (figs / "Fig1.png").read_bytes()}
    out = tmp_path / "_replay_out"
    out.mkdir()
    man = {"stem": "Fig1", "size_mm": [35.28, 17.64], "elements": []}
    (out / "Fig1.json").write_text(json.dumps(man), encoding="utf-8")

    class FakeWorker:
        script_name, entry = "fig1.py", "main"
        figures_dir = str(figs)
        base = out_dir = out
        built = True
        script_sha1 = ""
        last_patch_hash = ""
        remap_generation = inputremap.generation(figs)

        def override(self, stem, patches, preview_dpi=None, inline_svg=False):
            return {"ok": True, "manifest": man, "warnings": []}

        def export(self, stem, patches, path, fmt="pdf", dpi=600):
            Path(path).write_bytes(payload[fmt])
            return {"ok": True, "path": path, "warnings": []}

        def shutdown(self):
            pass

    w = FakeWorker()
    monkeypatch.setattr(m.engine_pool, "get", lambda *a, **k: w)
    monkeypatch.setattr(m.engine_pool, "one_shot", lambda *a, **k: w)
    monkeypatch.setattr(m.engine_pool, "discard", lambda _w: None)
    monkeypatch.setattr(m, "_stop_remap_dependent_work", lambda root: None)

    mid_replace = threading.Barrier(2, timeout=10)
    release = threading.Event()
    real_replace = Path.replace
    order: list[str] = []

    def replace_then_pause(self, target):
        got = real_replace(self, target)
        if Path(target).stem != "Fig1":
            return got  # 配置 / 备份等别的原子写不算
        order.append(f"replaced {Path(target).name}")
        if Path(target).suffix == ".pdf":
            mid_replace.wait()  # 第一个目标已换：告诉主线程「此刻在替换循环中途」
            release.wait(10)
        return got

    monkeypatch.setattr(Path, "replace", replace_then_pause)
    m.open_project(str(figs))
    try:
        before = inputremap.generation(figs)
        result: dict = {}

        def write_back():
            with m.app.test_client() as c:
                result["resp"] = c.post(
                    "/api/engine/update_source", json={"id": "Fig1.pdf", "patches": []}
                )

        def remap():
            inputremap.add_rule(figs, {"kind": P, "from": "", "to": str(tmp_path)})
            order.append("remapped")

        wb = threading.Thread(target=write_back)
        wb.start()
        mid_replace.wait()
        rm = threading.Thread(target=remap)
        rm.start()
        rm.join(0.5)
        # 改指在等：表没改、代次没动
        assert rm.is_alive(), "改指没有等在途的写回提交"
        assert inputremap.generation(figs) == before
        release.set()
        wb.join(10)
        rm.join(10)
        assert result["resp"].status_code == 200, result["resp"].get_json()
        assert order == ["replaced Fig1.pdf", "replaced Fig1.png", "remapped"]
        assert inputremap.generation(figs) == before + 1
    finally:
        release.set()
        for pid in [p for p, c in list(m.PROJECTS.items()) if str(c.path) == str(figs)]:
            m.close_project(pid, wait=True)


def _export_job(tmp_path):
    from tavotto.engine import exportjob

    return exportjob.prepare(
        {
            "scope": "canvas",
            "filename": "F",
            "formats": ["pdf"],
            "overwrite": "replace",
            "canvas": {"page_w_mm": 10, "page_h_mm": 10, "objects": []},
        },
        tmp_path / "out",
    )


def test_an_export_is_not_published_when_the_table_changed_after_it_started(tmp_path):
    """导出作业开始时记下代次，一路带到发布那一步（Codex 评 #716 P1）：渲染途中改了指认，提交守卫在锁里再核一次，
    对不上就一个文件都不发布、报 `input_remap_changed`（可重试）；没改的照常发布。"""
    from tavotto import app as m
    from tavotto.engine import exportjob

    root = tmp_path / "proj"
    root.mkdir()

    def produce_while(change):
        def produce(j, tmp_dir):
            p = tmp_dir / "F.pdf"
            p.write_bytes(b"%PDF-1.4\n")
            if change:
                inputremap.add_rule(root, {"kind": P, "from": "", "to": str(tmp_path)})
            return [exportjob.Produced(format="pdf", tmp_path=p, vector=True)]

        return produce

    job = _export_job(tmp_path)
    payload = exportjob.run(
        job,
        produce_while(True),
        classify_error=m._classify_export_error,
        commit_guard=m._export_commit_guard(root),
    )
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == inputremap.ERROR_CHANGED
    assert not (tmp_path / "out" / "F.pdf").exists()
    assert not list((tmp_path / "out").glob(exportjob.TMP_PREFIX + "*"))

    job = _export_job(tmp_path)
    payload = exportjob.run(
        job,
        produce_while(False),
        classify_error=m._classify_export_error,
        commit_guard=m._export_commit_guard(root),
    )
    assert payload["status"] == "done", payload
    assert (tmp_path / "out" / "F.pdf").exists()


def test_a_remap_during_export_publishing_waits_for_the_publish(tmp_path, monkeypatch):
    """发布循环中途改指：改指的项目锁等发布落完，发布出去的每个文件都属于同一代（barrier 把作业钉在发布中途）。"""
    import threading

    from tavotto import app as m
    from tavotto.engine import atomicio, exportjob

    root = tmp_path / "proj"
    root.mkdir()
    mid_publish = threading.Barrier(2, timeout=10)
    release = threading.Event()
    real_publish = atomicio.publish_file

    def publish_then_pause(src, dest):
        got = real_publish(src, dest)
        mid_publish.wait()
        release.wait(10)
        return got

    monkeypatch.setattr(exportjob.atomicio, "publish_file", publish_then_pause)

    def produce(j, tmp_dir):
        p = tmp_dir / "F.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        return [exportjob.Produced(format="pdf", tmp_path=p, vector=True)]

    job = _export_job(tmp_path)
    before = inputremap.generation(root)
    result: dict = {}
    t = threading.Thread(
        target=lambda: result.setdefault(
            "payload",
            exportjob.run(
                job,
                produce,
                classify_error=m._classify_export_error,
                commit_guard=m._export_commit_guard(root),
            ),
        )
    )
    t.start()
    try:
        mid_publish.wait()
        rm = threading.Thread(
            target=inputremap.add_rule, args=(root, {"kind": P, "from": "", "to": str(tmp_path)})
        )
        rm.start()
        rm.join(0.5)
        assert rm.is_alive(), "改指没有等在途的导出发布"
        assert inputremap.generation(root) == before
    finally:
        release.set()
    t.join(10)
    rm.join(10)
    assert result["payload"]["status"] == "done"
    assert inputremap.generation(root) == before + 1


def test_two_concurrent_re_registrations_keep_both_updates(tmp_path, monkeypatch):
    """两个脚本同时重新登记（Codex 评 #716 P1）：注册表整段读改写在项目锁里，后写的不整值覆盖先写的。
    读完文件之后停一下，让另一个线程也读到同一份——没有锁时后写的一方吞掉前一方的 stems。"""
    import threading

    from tavotto.engine import discover, registry

    root = tmp_path / "proj"
    root.mkdir()
    discover.register(root, "a.py", ["a_old"], entry="__main__")
    discover.register(root, "b.py", ["b_old"], entry="__main__")
    real_write = discover.write_config
    both_read = threading.Barrier(2, timeout=0.5)

    def slow_write(figures_dir, cfg):
        try:
            both_read.wait()  # 有锁时另一个线程进不来，等不到：超时后照常写
        except threading.BrokenBarrierError:
            pass
        return real_write(figures_dir, cfg)

    monkeypatch.setattr(discover, "write_config", slow_write)
    threads = [
        threading.Thread(
            target=discover.register, args=(root, name, [stem]), kwargs={"entry": "__main__"}
        )
        for name, stem in (("a.py", "a_new"), ("b.py", "b_new"))
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    path = registry.existing_registry_path(root)
    scripts = json.loads(path.read_text(encoding="utf-8"))["scripts"]
    assert scripts["a.py"]["stems"] == ["a_new"]
    assert scripts["b.py"]["stems"] == ["b_new"]


def test_state_reads_the_table_and_its_generation_together(tmp_path, monkeypatch):
    """设置页读改指表时另一个窗口正在改（Codex 评 #716 P2）：表与代次在同一次持锁里取——读到的永远是配套的
    一对，不会是「旧表 + 新代次」（前端记下新代次之后会把随后的事件当已见，列表就一直是旧的）。"""
    import threading

    root = tmp_path / "proj"
    root.mkdir()
    before = inputremap.generation(root)
    real_entries = inputremap._entries
    in_state = threading.Event()
    proceed = threading.Event()
    state_thread: dict = {}

    def slow_entries(r):
        out = real_entries(r)
        if threading.get_ident() == state_thread.get("id"):
            in_state.set()
            proceed.wait(0.5)  # 有锁时改指进不来；没锁时它此刻改表、换代
        return out

    monkeypatch.setattr(inputremap, "_entries", slow_entries)
    got: dict = {}

    def read_state():
        state_thread["id"] = threading.get_ident()
        got["state"] = inputremap.state(root)

    t = threading.Thread(target=read_state)
    t.start()
    assert in_state.wait(5)
    w = threading.Thread(
        target=inputremap.add_rule, args=(root, {"kind": P, "from": "", "to": str(tmp_path)})
    )
    w.start()
    w.join(0.3)
    proceed.set()
    t.join(10)
    w.join(10)
    st = got["state"]
    assert (st["rules"], st["generation"]) == ([], before), "读到的表与代次不是同一刻的"
    assert inputremap.generation(root) == before + 1


def test_the_project_mutex_is_reentrant_on_one_thread(tmp_path):
    """同一线程落地里再落地、落地里改表（登记 → 记标记、物化 → 取状态）不自锁。"""
    root = tmp_path / "proj"
    root.mkdir()
    with inputremap.landing(root):
        with inputremap.landing(root):
            inputremap.add_rule(root, {"kind": P, "from": "", "to": str(tmp_path)})
        assert inputremap.state(root)["rules"]


def test_canonically_equal_sources_replace_each_other(tmp_path):
    """`data` / `./data` / `data/`、反斜杠写法是同一处：新指认的那条替换旧的，不被旧的遮住（Codex 评 #716 P2）。"""
    root = tmp_path / "proj"
    root.mkdir()
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    (tmp_path / "three").mkdir()
    inputremap.add_rule(root, {"kind": P, "from": "data", "to": str(tmp_path / "one")})
    inputremap.add_rule(root, {"kind": P, "from": "./data", "to": str(tmp_path / "two")})
    assert inputremap.rules_for(root) == [
        {"kind": P, "from": "./data", "to": str(tmp_path / "two")}
    ]
    assert (
        figcapture.remap_target(inputremap.rules_for(root), "data/x.csv")
        == str(tmp_path / "two").replace("\\", "/") + "/x.csv"
    )
    inputremap.add_rule(root, {"kind": P, "from": "C:\\Users\\a", "to": str(tmp_path / "one")})
    inputremap.add_rule(root, {"kind": P, "from": "c:/Users/a/", "to": str(tmp_path / "three")})
    froms = [r["from"] for r in inputremap.rules_for(root)]
    assert froms == ["./data", "c:/Users/a/"]
    inputremap.remove_rule(root, P, "data/")  # 删也按同一处认
    assert [r["from"] for r in inputremap.rules_for(root)] == ["c:/Users/a/"]


def test_all_three_spawn_paths_take_the_rules_from_one_place(monkeypatch, tmp_path):
    box = {}

    class _Rec:
        def __init__(self, argv, **kw):
            box.setdefault("argv", []).append(argv)
            self.pid = 1

        def poll(self):
            return None

    monkeypatch.setattr(engine_pool.subprocess, "Popen", _Rec)
    monkeypatch.setattr(
        engine_pool, "select_worker_python", lambda: ("/usr/bin/python3", engine_pool.SOURCE_SYSTEM)
    )
    rule = {"kind": P, "from": "", "to": str(tmp_path / "data")}
    inputremap.add_rule(tmp_path, rule)
    try:
        w = engine_pool.EngineWorker("fig.py", str(tmp_path), "draw")
        assert w.spec.input_remap == (rule,)
        assert "--input-remap" in box["argv"][-1]
        spec = engine_pool._spawn_spec(
            "fig.py",
            str(tmp_path),
            "draw",
            w.out_dir,
            w.sandbox,
            w.log_path,
            "/usr/bin/python3",
            engine_pool.SOURCE_SYSTEM,
        )
        assert spec["argv"] == box["argv"][-1]
        shot = engine_pool.one_shot("fig.py", str(tmp_path), "draw")
        try:
            assert shot.spec.input_remap == (rule,)
        finally:
            engine_pool.discard(shot)
    finally:
        inputremap.remove_rule(tmp_path, P, "")


# --------------------------------------------------------------- 试运行（素材库「运行并发现图」）
@needs_worker
def test_the_probe_entry_carries_the_same_offer(figs, tmp_path):
    from tavotto.engine import probe as engine_probe

    (figs / "fig.py").write_text(SCRIPT_REL, encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    result = engine_probe.probe(figs, "fig.py")
    err = result["error"]
    assert err["code"] == engine_probe.ERROR_MISSING_INPUT
    assert err["missing_input"]["requested"] == "data/values.txt"
    assert "FileNotFoundError" in err["params"]["error"]

    chosen = _touch(tmp_path / "moved" / "data" / "values.txt", "truth-9")
    inputremap.add_rule(
        figs, inputremap.derive("data/values.txt", str(chosen), chosen_is_dir=False)
    )
    engine_pool.shutdown_all(str(figs), wait=True)
    result = engine_probe.probe(figs, "fig.py")
    assert result["error"] is None and result["stems"] == ["values"]


@needs_worker
def test_the_probe_no_figure_path_carries_the_static_list(figs):
    from tavotto.engine import probe as engine_probe

    (figs / "fig.py").write_text(
        'import os\nif os.path.exists("data/values.txt"):\n'
        "    import matplotlib.pyplot as plt\n    plt.plot([1])\n",
        encoding="utf-8",
    )
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    err = engine_probe.probe(figs, "fig.py")["error"]
    assert err["code"] == engine_probe.ERROR_NO_FIGURE
    assert [o["path"] for o in err["missing_input"]["others"]] == ["data/values.txt"]
