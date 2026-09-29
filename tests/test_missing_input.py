"""数据找不到时请用户指认一次、按项目记住的只读改指表（ADR 0106）。

四层：规则怎么匹配（`figcapture.remap_target`，父进程与 worker 同一份）、从一次指认推规则
（`inputremap.derive`）、worker 里的改道与落空记账（`install_input_remap` / `missing_input_of`，
进程内）、真 worker 经 `pool.build` 从 `missing_input` 到指认后出图（输出等于指认那份数据的真值，
旁边放一份同名不同值的文件也不影响——FO08 的反例）。
"""

from __future__ import annotations

import builtins
import io
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
    assert rule["to"].lower().startswith("//localhost/")
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

    def install(rules):
        misses = figcapture.InputMisses()
        installed.append(figcapture.install_input_remap(rules, misses, str(box)))
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
        {"path": "data/values.txt", "absolute": False, "via": inputremap.VIA_PROBE}
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
        {"path": "data/values.txt", "absolute": False, "via": inputremap.VIA_PROBE}
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
        'e = np.load(os.path.join(RAW, "e.npy"))\n',
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
