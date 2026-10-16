"""V2 PR2：运行前路由 `runroute`（ADR 0133）的规则向量、优先级与同源钉。

向量表驱动：脚本片段 → 期望的路由 / 命中规则。每条「高置信直达 native」的向量对应一个运行期会被拦的形状
（`BlockedFileOps` 的观察：`tests/test_runroute_consistency.py` 用真 safe worker 逐条核对静态与运行期不分叉）。
"""

from __future__ import annotations

import dataclasses
import os
import textwrap

import pytest

from tavotto.engine import databinding, execpolicy, figcapture, inputremap, runroute, workdir

ROOT = "/proj"
SCRIPT = "figs/fig.py"


def _ungated(src):
    """规则引擎向量用：抹掉伤害闸门的事实（见 `_route`）。"""
    return dataclasses.replace(runroute.facts_of(src), guarded_ops=False)


def _route(src, *, cwd=runroute.CWD_SANDBOX, gate=False, **kw):
    """`gate=False`：这些向量测的是 R1–R4 **规则引擎**本身，所以抹掉伤害闸门的事实（ADR 0133「伤害闸门」）；
    闸门自己的用例显式 `gate=True`。生产路径永远带闸门：含守卫操作的脚本（R3 的 `os.replace` 就是）一律不直达 native。"""
    facts = runroute.facts_of(textwrap.dedent(src))
    if not gate:
        facts = dataclasses.replace(facts, guarded_ops=False)
    return runroute.decide(facts, cwd_mode=cwd, project_root=ROOT, script=SCRIPT, **kw)


def _rules(route):
    return sorted({e["rule"] for e in route.evidence})


PLOT = """
import matplotlib.pyplot as plt
plt.plot([1, 2])
plt.savefig("out.png")
"""

# (名称, 源码, cwd 档, 期望 execution, 期望 reason, 期望包含的规则)
NATIVE = [
    (
        "savefig_image_open",
        """
        import matplotlib.pyplot as plt
        from PIL import Image
        plt.savefig("tmp.png")
        img = Image.open("tmp.png")
        """,
        runroute.CWD_SANDBOX,
        runroute.REASON_SAVEFIG_READBACK,
        "R1",
    ),
    (
        "savefig_imread_alias",
        """
        import matplotlib.pyplot as plt
        import matplotlib.image as mpimg
        fig, ax = plt.subplots()
        fig.savefig("stage.png", dpi=100)
        data = mpimg.imread("stage.png")
        """,
        runroute.CWD_PROJECT,
        runroute.REASON_SAVEFIG_READBACK,
        "R1",
    ),
    (
        "savefig_open_rb",
        """
        import matplotlib.pyplot as plt
        plt.savefig("a.png")
        raw = open("a.png", "rb").read()
        """,
        runroute.CWD_SANDBOX,
        runroute.REASON_SAVEFIG_READBACK,
        "R1",
    ),
    (
        "savefig_pathobj_single_assignment",
        """
        from pathlib import Path
        import matplotlib.pyplot as plt
        out = Path("stage.png")
        plt.savefig(out)
        n = len(out.read_bytes())
        """,
        runroute.CWD_SANDBOX,
        runroute.REASON_SAVEFIG_READBACK,
        "R1",
    ),
    (
        "savefig_script_relative_folded",
        """
        import os
        import matplotlib.pyplot as plt
        from PIL import Image
        HERE = os.path.dirname(os.path.abspath(__file__))
        OUT = os.path.join(HERE, "build", "p.png")
        plt.savefig(OUT)
        Image.open(os.path.join(HERE, "build", "p.png"))
        """,
        runroute.CWD_SANDBOX,
        runroute.REASON_SAVEFIG_READBACK,
        "R1",
    ),
    (
        "savefig_fstring_constants",
        """
        import matplotlib.pyplot as plt
        import cv2
        NAME = "p"
        plt.savefig(f"{NAME}.png")
        cv2.imread("p.png")
        """,
        runroute.CWD_SANDBOX,
        runroute.REASON_SAVEFIG_READBACK,
        "R1",
    ),
    (
        "tmp_write_replace_read_project_cwd",
        """
        import os
        open("cache.tmp", "w").write("2")
        os.replace("cache.tmp", "cache.csv")
        v = int(open("cache.csv").read())
        """,
        runroute.CWD_PROJECT,
        runroute.REASON_NATIVE_FILE_SEMANTICS,
        "R3",
    ),
    (
        "tmp_write_replace_read_script_relative_tuple_assign",
        """
        import os
        from pathlib import Path
        here = Path(__file__).resolve().parent
        tmp, final = here / "c.tmp", here / "c.csv"
        tmp.write_text("2")
        os.replace(tmp, final)
        v = int(final.read_text())
        """,
        runroute.CWD_SANDBOX,  # 脚本目录相对：与 cwd 档无关，源在真实图库内
        runroute.REASON_NATIVE_FILE_SEMANTICS,
        "R3",
    ),
    (
        "shutil_move_then_exists",
        """
        import shutil, os
        open("a.tmp", "w").write("x")
        shutil.move("a.tmp", "a.dat")
        assert os.path.exists("a.dat")
        """,
        runroute.CWD_PROJECT_ROOT,
        runroute.REASON_NATIVE_FILE_SEMANTICS,
        "R3",
    ),
    (
        "path_replace_method",
        """
        from pathlib import Path
        t = Path("a.tmp")
        t.write_text("x")
        t.replace(Path("a.dat"))
        Path("a.dat").read_text()
        """,
        runroute.CWD_PROJECT,
        runroute.REASON_NATIVE_FILE_SEMANTICS,
        "R3",
    ),
    (
        "from_import_alias_replace",
        """
        from os import replace as swap
        open("a.tmp", "w").write("x")
        swap("a.tmp", "a.dat")
        open("a.dat").read()
        """,
        runroute.CWD_PROJECT,
        runroute.REASON_NATIVE_FILE_SEMANTICS,
        "R3",
    ),
]

SAFE = [
    ("plain_plot", PLOT, runroute.CWD_SANDBOX, runroute.CONFIDENCE_HIGH, ()),
    (
        "savefig_bytesio_then_image_open",  # 文件对象不是路径：safe 里透传，读得到
        """
        import io
        import matplotlib.pyplot as plt
        from PIL import Image
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        Image.open(buf)
        """,
        runroute.CWD_SANDBOX,
        runroute.CONFIDENCE_HIGH,
        (),
    ),
    (
        "savefig_handle_from_open",
        """
        import matplotlib.pyplot as plt
        with open("x.png", "wb") as fh:
            plt.savefig(fh)
        """,
        runroute.CWD_SANDBOX,
        runroute.CONFIDENCE_HIGH,
        (),
    ),
    (
        "savefig_then_exists_only",  # R2：运行期也刻意不算观察
        """
        import os
        import matplotlib.pyplot as plt
        plt.savefig("a.png")
        print(os.path.exists("a.png"))
        """,
        runroute.CWD_SANDBOX,
        runroute.CONFIDENCE_MEDIUM,
        ("R2",),
    ),
    (
        "read_before_savefig",
        """
        import matplotlib.pyplot as plt
        from PIL import Image
        Image.open("a.png")
        plt.savefig("a.png")
        """,
        runroute.CWD_SANDBOX,
        runroute.CONFIDENCE_HIGH,
        (),
    ),
    (
        "savefig_and_read_in_exclusive_branches",
        """
        import matplotlib.pyplot as plt
        from PIL import Image
        if True:
            plt.savefig("t.png")
        else:
            Image.open("t.png")
        """,
        runroute.CWD_SANDBOX,
        runroute.CONFIDENCE_HIGH,
        (),
    ),
    (
        "different_files",
        """
        import matplotlib.pyplot as plt
        from PIL import Image
        plt.savefig("a.png")
        Image.open("b.png")
        """,
        runroute.CWD_SANDBOX,
        runroute.CONFIDENCE_HIGH,
        (),
    ),
    (
        "replace_inside_sandbox_cwd",  # 沙盒档相对路径落沙盒：safe 本来就真执行，不升格
        """
        import os
        open("cache.tmp", "w").write("2")
        os.replace("cache.tmp", "cache.csv")
        open("cache.csv").read()
        """,
        runroute.CWD_SANDBOX,
        runroute.CONFIDENCE_HIGH,
        (),
    ),
    (
        "replace_from_system_tmp",
        """
        import os, tempfile
        t = os.path.join(tempfile.gettempdir(), "x.tmp")
        open(t, "w").write("2")
        os.replace(t, "final.csv")
        open("final.csv").read()
        """,
        runroute.CWD_PROJECT,
        runroute.CONFIDENCE_HIGH,
        (),
    ),
    (
        "replace_without_visible_write_is_medium",
        """
        import os
        os.replace("cache.tmp", "cache.csv")
        open("cache.csv").read()
        """,
        runroute.CWD_PROJECT,
        runroute.CONFIDENCE_MEDIUM,
        ("R3_partial",),
    ),
    (
        "replace_without_read_is_medium",
        """
        import os
        open("cache.tmp", "w").write("2")
        os.replace("cache.tmp", "cache.csv")
        """,
        runroute.CWD_PROJECT,
        runroute.CONFIDENCE_MEDIUM,
        ("R3_partial",),
    ),
    (
        "str_replace_is_not_a_move",  # 字符串 .replace / 非 pathlib 对象的 .rename
        """
        import os
        name = "a"
        name = name.replace("a", "b")
        cache = {}
        cache.replace("x", "y")
        os.path.exists("b")
        """,
        runroute.CWD_PROJECT,
        runroute.CONFIDENCE_HIGH,
        (),
    ),
    (
        "dynamic_path_never_escalates",  # R4
        """
        import os, sys
        import matplotlib.pyplot as plt
        from PIL import Image
        out = os.path.join(sys.argv[1], "a.png")
        plt.savefig(out)
        Image.open(out)
        """,
        runroute.CWD_SANDBOX,
        runroute.CONFIDENCE_LOW,
        ("R4",),
    ),
    (
        "path_from_function_parameter",
        """
        import matplotlib.pyplot as plt
        from PIL import Image
        def run(p):
            plt.savefig(p)
            Image.open(p)
        run("a.png")
        """,
        runroute.CWD_SANDBOX,
        runroute.CONFIDENCE_LOW,
        ("R4",),
    ),
    (
        "reassigned_name_is_dynamic",
        """
        import matplotlib.pyplot as plt
        from PIL import Image
        p = "a.png"
        p = "b.png"
        plt.savefig(p)
        Image.open(p)
        """,
        runroute.CWD_SANDBOX,
        runroute.CONFIDENCE_LOW,
        ("R4",),
    ),
    (
        "cross_function_readback_is_medium",  # 跨函数：只记证据，不直达
        """
        import matplotlib.pyplot as plt
        from PIL import Image
        def plot():
            plt.savefig("a.png")
        def check():
            Image.open("a.png")
        plot()
        check()
        """,
        runroute.CWD_SANDBOX,
        runroute.CONFIDENCE_MEDIUM,
        ("R1_cross_scope",),
    ),
    (
        "unrelated_open_shadow",  # 自己定义的 open / Image 不是内建与 PIL
        """
        import matplotlib.pyplot as plt
        def open(p):
            return p
        plt.savefig("a.png")
        open("a.png")
        """,
        runroute.CWD_SANDBOX,
        runroute.CONFIDENCE_HIGH,
        (),
    ),
]


@pytest.mark.parametrize("name,src,cwd,reason,rule", NATIVE, ids=[c[0] for c in NATIVE])
def test_high_confidence_shapes_go_straight_to_native(name, src, cwd, reason, rule):
    route = _route(src, cwd=cwd)
    assert (route.execution, route.reason, route.confidence) == (
        runroute.EXECUTION_NATIVE,
        reason,
        runroute.CONFIDENCE_HIGH,
    ), route
    assert rule in _rules(route)
    assert route.required_execution == runroute.REQUIRED_EXECUTION_FILE_SEMANTICS
    # 证据只有规则 / 类别 / 行号，没有路径与源码
    for e in route.evidence:
        assert set(e) <= {"rule", "kind", "op", "line", "read_line", "source"}
        assert e["source"] == "static"


@pytest.mark.parametrize("name,src,cwd,confidence,rules", SAFE, ids=[c[0] for c in SAFE])
def test_everything_else_stays_safe_and_low_confidence_never_upgrades(
    name, src, cwd, confidence, rules
):
    route = _route(src, cwd=cwd)
    assert (
        route.execution == runroute.EXECUTION_SAFE and route.reason == runroute.REASON_ORDINARY_PLOT
    )
    assert route.confidence == confidence
    assert tuple(_rules(route)) == tuple(sorted(rules))
    assert route.required_execution == ""


def test_dynamic_paths_are_counted_but_do_not_change_the_route():
    route = _route(
        """
        import sys
        import matplotlib.pyplot as plt
        plt.savefig(sys.argv[1])
        plt.savefig(sys.argv[2])
        """
    )
    assert route.execution == "safe" and route.dynamic_paths == 2 and "R4" in _rules(route)


def test_the_cwd_mode_decides_whether_a_relative_replace_source_is_in_the_real_library():
    src = """
        import os
        open("c.tmp", "w").write("1")
        os.replace("c.tmp", "c.csv")
        open("c.csv").read()
        """
    assert _route(src, cwd=runroute.CWD_SANDBOX).execution == "safe"
    assert _route(src, cwd=runroute.CWD_PROJECT).execution == "native"
    assert _route(src, cwd=runroute.CWD_PROJECT_ROOT).execution == "native"
    assert _route(src, cwd=None).execution == "safe"  # 档位未知：不升格


def test_savefig_readback_does_not_depend_on_the_cwd_mode():
    src = """
        import matplotlib.pyplot as plt
        from PIL import Image
        plt.savefig("t.png")
        Image.open("t.png")
        """
    for cwd in (runroute.CWD_SANDBOX, runroute.CWD_PROJECT, runroute.CWD_PROJECT_ROOT, None):
        assert _route(src, cwd=cwd).execution == "native", cwd


def test_absolute_literal_paths_are_in_the_library_only_under_the_project_root():
    # 字面路径用本平台上项目根的真实绝对形式：Windows 上 `/proj` 会被补成 `D:\\proj`，而脚本里不带盘符的 `/proj/...`
    # 指向运行时当前盘的根，静态证明不了在项目里（判 safe 是对的）
    root = os.path.abspath(ROOT)
    inside = f"""
        import os
        open({root + "/a.tmp"!r}, "w").write("1")
        os.replace({root + "/a.tmp"!r}, {root + "/a.dat"!r})
        open({root + "/a.dat"!r}).read()
        """
    outside = inside.replace(repr(root)[1:-1], "/elsewhere")
    assert _route(inside, cwd=runroute.CWD_SANDBOX).execution == "native"
    assert _route(outside, cwd=runroute.CWD_SANDBOX).execution == "safe"


def test_script_relative_and_project_cwd_spellings_of_one_file_are_the_same_file():
    # 脚本在 figs/ 下；cwd=project 时 cwd 相对 "t.png" 就是脚本目录下的 t.png
    src = """
        from pathlib import Path
        import matplotlib.pyplot as plt
        from PIL import Image
        plt.savefig("t.png")
        Image.open(Path(__file__).parent / "t.png")
        """
    assert _route(src, cwd=runroute.CWD_PROJECT).execution == "native"
    assert _route(src, cwd=runroute.CWD_SANDBOX).execution == "safe"  # 沙盒里它们是两个文件


# ---------------------------------------------------------------- 优先级与开关
def test_precedence_user_policy_then_operator_then_previous_success_then_static():
    readback = """
        import matplotlib.pyplot as plt
        from PIL import Image
        plt.savefig("t.png")
        Image.open("t.png")
        """
    facts = runroute.facts_of(textwrap.dedent(readback))

    def go(**kw):
        return runroute.decide(facts, cwd_mode="sandbox", project_root=ROOT, script=SCRIPT, **kw)

    assert go().reason == runroute.REASON_SAVEFIG_READBACK
    safe_only = go(policy=runroute.POLICY_SAFE_ONLY, last_success="native")
    assert (safe_only.execution, safe_only.reason) == ("safe", runroute.REASON_USER_EXPLICIT)
    off = go(operator_enabled=False, last_success="native")
    assert (off.execution, off.reason) == ("safe", runroute.REASON_OPERATOR_DISABLED)
    prev_native = go(last_success="native")
    assert (prev_native.execution, prev_native.reason) == (
        "native",
        runroute.REASON_PREVIOUS_SUCCESS,
    )
    # 上次 safe 成功：沿用 safe，即使静态证据说 native（safe 成功是被账本验证过的）
    prev_safe = go(last_success="safe")
    assert (prev_safe.execution, prev_safe.reason) == ("safe", runroute.REASON_PREVIOUS_SUCCESS)


def test_unparsable_and_oversized_scripts_route_safe_and_say_why():
    bad = runroute.decide(
        runroute.facts_of("def (:"), cwd_mode="sandbox", project_root=ROOT, script=SCRIPT
    )
    assert bad.execution == "safe" and bad.confidence == "low"
    assert runroute.BLIND_UNPARSED in bad.blind_spots
    big = runroute.decide(
        runroute.facts_of("x = 1\n" * (runroute.MAX_SOURCE_BYTES // 4)),
        cwd_mode="sandbox",
        project_root=ROOT,
        script=SCRIPT,
    )
    assert big.execution == "safe" and runroute.BLIND_TOO_LARGE in big.blind_spots


def test_the_blind_spots_are_always_reported_for_analyzed_scripts():
    assert set(_route(PLOT).blind_spots) == set(runroute.BLIND_SPOTS)


def test_downgrade_keeps_reason_and_evidence_but_runs_safe():
    route = _route(
        """
        import matplotlib.pyplot as plt
        from PIL import Image
        plt.savefig("t.png")
        Image.open("t.png")
        """
    ).downgraded("no_interpreter")
    assert route.execution == "safe" and route.reason == runroute.REASON_SAVEFIG_READBACK
    assert route.declined == "no_interpreter" and route.public()["declined"] == "no_interpreter"


# ---------------------------------------------------------------- 文件缓存与耗时
def test_file_facts_parse_once_per_file_revision(tmp_path, monkeypatch):
    path = tmp_path / "s.py"
    path.write_text(PLOT, encoding="utf-8")
    runroute.clear_cache()
    calls = []
    real = runroute.ast.parse
    monkeypatch.setattr(runroute.ast, "parse", lambda *a, **k: calls.append(1) or real(*a, **k))
    for _ in range(3):
        runroute.route_for_file(path, cwd_mode="sandbox", project_root=tmp_path, script="s.py")
    assert len(calls) == 1
    path.write_text(PLOT + "\n# edited\n", encoding="utf-8")
    runroute.route_for_file(path, cwd_mode="sandbox", project_root=tmp_path, script="s.py")
    assert len(calls) == 2


def test_the_route_records_its_own_elapsed_time_and_skips_reading_when_not_needed(tmp_path):
    path = tmp_path / "s.py"
    path.write_text(PLOT, encoding="utf-8")
    runroute.clear_cache()
    route = runroute.route_for_file(path, cwd_mode="sandbox", project_root=tmp_path, script="s.py")
    assert route.elapsed_ms > 0 and route.public()["elapsed_ms"] > 0
    # 用户显式 safe_only：不读脚本、不解析
    runroute.clear_cache()
    runroute.route_for_file(
        path,
        cwd_mode="sandbox",
        project_root=tmp_path,
        script="s.py",
        policy=runroute.POLICY_SAFE_ONLY,
    )
    assert not runroute._cache


def test_a_large_script_is_analyzed_in_milliseconds_not_seconds():
    body = "import matplotlib.pyplot as plt\n" + "".join(
        f"x{i} = {i}\nplt.plot([x{i}])\n" for i in range(8000)
    )
    start = runroute.time.perf_counter()
    runroute.decide(runroute.facts_of(body), cwd_mode="sandbox", project_root=ROOT, script=SCRIPT)
    assert runroute.time.perf_counter() - start < 5.0  # 上限护栏，不是基准；基准数字记在 ADR 0133


# ---------------------------------------------------------------- 同源钉
def test_vocabularies_mirror_their_authorities():
    assert runroute.REQUIRED_EXECUTION_FILE_SEMANTICS == "preserve_file_semantics"
    assert runroute.KIND_RENAME == figcapture.BlockedFileOps.KIND_RENAME
    assert runroute.KIND_WRITE == figcapture.BlockedFileOps.KIND_WRITE
    assert runroute.KIND_SAVEFIG == figcapture.BlockedFileOps.KIND_SAVEFIG
    assert runroute.POLICY_COMPATIBLE == execpolicy.MODE_COMPATIBLE
    assert runroute.POLICY_SAFE_ONLY == execpolicy.MODE_SAFE_ONLY
    assert runroute.CWD_SANDBOX == workdir.MODE_SANDBOX
    assert runroute.CWD_PROJECT == workdir.MODE_PROJECT
    assert runroute.CWD_PROJECT_ROOT == workdir.MODE_PROJECT_ROOT
    assert set(runroute.EXECUTIONS) == {execpolicy.PROFILE_SAFE, execpolicy.PROFILE_NATIVE}


def test_the_call_tables_stay_inside_the_authoritative_tail_name_sets():
    # 本模块的表是「按 import 绑定解析的限定名」，比别处的尾名表更严；但每个尾名都必须是权威表认得的读 / 写名
    reads = {"imread", *runroute._NUMPY_READ}
    assert reads <= inputremap.READ_FUNCS | {"fromfile"}
    writes = set(runroute._DATA_WRITE_TAILS) | set(runroute._NUMPY_WRITE)
    assert writes <= inputremap.WRITE_FUNCS | databinding.SAVE_FUNCS
    assert "savefig" in databinding.SAVE_FUNCS and "imsave" in databinding.SAVE_FUNCS


def test_every_route_reason_and_rule_is_in_a_closed_set():
    route = _route(PLOT)
    assert route.reason in runroute.REASONS
    for e in _route(
        """
        import matplotlib.pyplot as plt
        from PIL import Image
        plt.savefig("t.png")
        Image.open("t.png")
        """
    ).evidence:
        assert e["rule"] in runroute.RULES


# ---------------------------------------------------------------- Codex #1034 r2：接收者与可达性
READ_BACK = '\nopen("x.png", "rb").read()\n'

NOT_MATPLOTLIB_SAVEFIG = [
    (
        "custom_object_method",
        """
        class Report:
            def savefig(self, p): ...
        report = Report()
        report.savefig("x.png")
        """,
    ),
    (
        "unresolved_parameter_receiver",
        """
        def go(report):
            report.savefig("x.png")
            open("x.png", "rb").read()
        go(None)
        """,
    ),
    (
        "user_defined_bare_savefig",
        """
        def savefig(p): ...
        savefig("x.png")
        """,
    ),
    (
        "import_alias_rebound_to_another_module",
        """
        import mymod as plt
        plt.savefig("x.png")
        """,
    ),
]


@pytest.mark.parametrize(
    "name,body", NOT_MATPLOTLIB_SAVEFIG, ids=[c[0] for c in NOT_MATPLOTLIB_SAVEFIG]
)
def test_savefig_on_something_that_is_not_matplotlib_never_goes_native(name, body):
    route = _route(textwrap.dedent(body) + READ_BACK)
    assert route.execution == "safe", route.public()
    assert route.confidence == runroute.CONFIDENCE_MEDIUM and "R1_unproven" in _rules(route)


MATPLOTLIB_SAVEFIG = [
    ("plt_savefig", "import matplotlib.pyplot as plt\nplt.savefig('x.png')"),
    ("from_import_pyplot", "from matplotlib import pyplot as p\np.savefig('x.png')"),
    ("from_import_function", "from matplotlib.pyplot import savefig\nsavefig('x.png')"),
    ("figure_call", "import matplotlib.pyplot as plt\nf = plt.figure()\nf.savefig('x.png')"),
    (
        "subplots_unpack",
        "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nfig.savefig('x.png')",
    ),
    ("gcf", "import matplotlib.pyplot as plt\nplt.gcf().savefig('x.png')"),
    ("figure_class", "from matplotlib.figure import Figure\nf = Figure()\nf.savefig('x.png')"),
]


@pytest.mark.parametrize("name,body", MATPLOTLIB_SAVEFIG, ids=[c[0] for c in MATPLOTLIB_SAVEFIG])
def test_recognized_matplotlib_savefig_receivers_still_go_native(name, body):
    route = _route(textwrap.dedent(body) + READ_BACK)
    assert route.execution == "native" and "R1" in _rules(route), route.public()


def test_a_reassigned_figure_name_is_not_a_recognized_receiver():
    route = _route(
        """
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        fig = make_report()
        fig.savefig("x.png")
        """.replace("\n        ", "\n")
        + READ_BACK
    )
    assert route.execution == "safe" and "R1_unproven" in _rules(route)


UNREACHED = [
    (
        "unused_helper",
        """
        import matplotlib.pyplot as plt
        def helper():
            plt.savefig("x.png")
            open("x.png", "rb").read()
        """,
    ),
    (
        "never_called_main",
        """
        import matplotlib.pyplot as plt
        def main():
            plt.savefig("x.png")
            open("x.png", "rb").read()
        if __name__ == "__main__":
            pass
        """,
    ),
    (
        "method_never_called_by_name",
        """
        import matplotlib.pyplot as plt
        class A:
            def run(self):
                plt.savefig("x.png")
                open("x.png", "rb").read()
        """,
    ),
    (
        "replace_chain_in_unused_function",
        """
        import os
        def helper():
            open("c.tmp", "w").write("1")
            os.replace("c.tmp", "c.csv")
            open("c.csv").read()
        """,
    ),
]


@pytest.mark.parametrize("name,body", UNREACHED, ids=[c[0] for c in UNREACHED])
def test_evidence_only_inside_functions_the_module_never_calls_is_not_high(name, body):
    route = _route(body, cwd=runroute.CWD_PROJECT)
    assert route.execution == "safe", route.public()
    assert route.confidence == runroute.CONFIDENCE_MEDIUM


REACHED = [
    (
        "called_at_module_level",
        """
        import matplotlib.pyplot as plt
        def helper():
            plt.savefig("x.png")
            open("x.png", "rb").read()
        helper()
        """,
    ),
    (
        "called_under_main_guard",
        """
        import matplotlib.pyplot as plt
        def main():
            plt.savefig("x.png")
            open("x.png", "rb").read()
        if __name__ == "__main__":
            main()
        """,
    ),
    (
        "transitively_called",
        """
        import matplotlib.pyplot as plt
        def inner():
            plt.savefig("x.png")
            open("x.png", "rb").read()
        def outer():
            inner()
        outer()
        """,
    ),
    (
        "nested_function_called_by_parent",
        """
        import matplotlib.pyplot as plt
        def outer():
            def inner():
                plt.savefig("x.png")
                open("x.png", "rb").read()
            inner()
        outer()
        """,
    ),
]


@pytest.mark.parametrize("name,body", REACHED, ids=[c[0] for c in REACHED])
def test_functions_the_module_calls_keep_their_evidence_high(name, body):
    route = _route(body)
    assert route.execution == "native" and "R1" in _rules(route), route.public()


# ---------------------------------------------------------------- Codex #1034 r3：遮蔽 / 常量假分支 / Windows 根路径
MPL = "import matplotlib.pyplot as plt\n"

SHADOWED = [
    (
        "parameter_named_like_the_alias",
        MPL
        + 'def run(plt):\n    plt.savefig("x.png")\n    open("x.png", "rb").read()\nrun(object())\n',
    ),
    (
        "alias_rebound_later",
        MPL + 'plt = Other()\nplt.savefig("x.png")\nopen("x.png", "rb").read()\n',
    ),
    (
        "alias_rebound_in_another_function",
        MPL
        + 'def patch():\n    global plt\n    plt = Other()\nplt.savefig("x.png")\nopen("x.png", "rb").read()\n',
    ),
    (
        "alias_rebound_by_for_target",
        MPL + 'for plt in things:\n    pass\nplt.savefig("x.png")\nopen("x.png", "rb").read()\n',
    ),
    (
        "alias_rebound_by_def",
        MPL + 'def plt(): ...\nplt.savefig("x.png")\nopen("x.png", "rb").read()\n',
    ),
    (
        "figure_name_rebound_in_another_scope",
        MPL
        + 'fig = plt.figure()\ndef other():\n    fig = make()\nfig.savefig("x.png")\nopen("x.png", "rb").read()\n',
    ),
    (
        "figure_name_is_a_parameter_elsewhere",
        MPL
        + 'fig = plt.figure()\ndef other(fig):\n    return fig\nfig.savefig("x.png")\nopen("x.png", "rb").read()\n',
    ),
]


@pytest.mark.parametrize("name,src", SHADOWED, ids=[c[0] for c in SHADOWED])
def test_a_rebound_alias_or_receiver_name_anywhere_in_the_module_is_not_trusted(name, src):
    route = _route(src)
    assert route.execution == "safe" and "R1" not in _rules(route), route.public()
    assert "R1_unproven" in _rules(route)


DEAD = [
    (
        "if_false_calls_helper",
        MPL
        + 'def helper():\n    plt.savefig("x.png")\n    open("x.png", "rb").read()\nif False:\n    helper()\n',
    ),
    (
        "if_zero_calls_helper",
        MPL
        + 'def helper():\n    plt.savefig("x.png")\n    open("x.png", "rb").read()\nif 0:\n    helper()\n',
    ),
    (
        "type_checking_branch",
        "from typing import TYPE_CHECKING\n"
        + MPL
        + 'def helper():\n    plt.savefig("x.png")\n    open("x.png", "rb").read()\nif TYPE_CHECKING:\n    helper()\n',
    ),
    (
        "while_false",
        MPL
        + 'def helper():\n    plt.savefig("x.png")\n    open("x.png", "rb").read()\nwhile False:\n    helper()\n',
    ),
    (
        "evidence_written_directly_in_a_dead_branch",
        MPL + 'if False:\n    plt.savefig("x.png")\n    open("x.png", "rb").read()\n',
    ),
]


@pytest.mark.parametrize("name,src", DEAD, ids=[c[0] for c in DEAD])
def test_calls_and_evidence_under_a_constant_false_branch_do_not_count(name, src):
    route = _route(src)
    assert route.execution == "safe" and "R1" not in _rules(route), route.public()


def test_the_else_arm_of_a_constant_false_branch_still_counts():
    route = _route(
        MPL
        + 'def helper():\n    plt.savefig("x.png")\n    open("x.png", "rb").read()\nif False:\n    pass\nelse:\n    helper()\n'
    )
    assert route.execution == "native"


WINDOWS_ROOTED = [
    r"\cache.tmp",
    "C:/cache.tmp",
    r"C:\cache.tmp",
    r"\\server\share\cache.tmp",
    "C:cache.tmp",
]


@pytest.mark.parametrize("tmp", WINDOWS_ROOTED)
def test_windows_rooted_paths_are_never_project_local(tmp):
    final = tmp.replace("cache.tmp", "cache.csv")
    src = f"""
import os
open({tmp!r}, "w").write("1")
os.replace({tmp!r}, {final!r})
open({final!r}).read()
"""
    for cwd in (runroute.CWD_PROJECT, runroute.CWD_PROJECT_ROOT):
        route = _route(src, cwd=cwd)
        assert route.execution == "safe", (tmp, cwd, route.public())


def test_a_plain_relative_name_is_still_project_local():
    src = """
import os
open("cache.tmp", "w").write("1")
os.replace("cache.tmp", "cache.csv")
open("cache.csv").read()
"""
    assert _route(src, cwd=runroute.CWD_PROJECT).execution == "native"


# ---------------------------------------------------------------- Codex #1034 r5：符号链接 / 重新绑定的函数名
def test_an_r3_source_behind_a_symlink_leaving_the_project_is_not_high(tmp_path):
    """safe 的改名守卫先 resolve 再判「在不在真实图库内」：项目里的 `out -> 项目外` 下的改名 safe 照常真执行，不能升 native。"""
    import os

    proj, outside = tmp_path / "proj", tmp_path / "elsewhere"
    proj.mkdir()
    outside.mkdir()
    os.symlink(outside, proj / "out")
    src = """
import os
from pathlib import Path
Path("out/t").write_text("1")
os.replace("out/t", "out/f")
open("out/f").read()
"""
    facts = runroute.facts_of(textwrap.dedent(src))
    route = runroute.decide(facts, cwd_mode="project_root", project_root=proj, script="fig.py")
    assert route.execution == "safe", route.public()
    assert "R3" not in _rules(route)


def test_a_symlink_that_stays_inside_the_project_is_still_high(tmp_path):
    import os

    proj = tmp_path / "proj"
    (proj / "real").mkdir(parents=True)
    os.symlink(proj / "real", proj / "out")
    src = """
import os
open("out/t", "w").write("1")
os.replace("out/t", "out/f")
open("out/f").read()
"""
    route = runroute.decide(
        _ungated(textwrap.dedent(src)),
        cwd_mode="project_root",
        project_root=proj,
        script="fig.py",
    )
    assert route.execution == "native", route.public()


REBOUND_FUNCTIONS = [
    (
        "reassigned_after_def",
        MPL
        + 'def helper():\n    plt.savefig("x.png")\n    open("x.png", "rb").read()\nhelper = lambda: None\nhelper()\n',
    ),
    (
        "defined_twice",
        MPL
        + 'def helper():\n    plt.savefig("x.png")\n    open("x.png", "rb").read()\ndef helper():\n    pass\nhelper()\n',
    ),
    (
        "shadowed_by_import",
        MPL
        + 'def helper():\n    plt.savefig("x.png")\n    open("x.png", "rb").read()\nfrom other import helper\nhelper()\n',
    ),
    (
        "same_name_param_elsewhere",
        MPL
        + 'def helper():\n    plt.savefig("x.png")\n    open("x.png", "rb").read()\ndef go(helper):\n    helper()\ngo(None)\nhelper()\n',
    ),
    (
        "same_name_def_in_a_class_or_function",
        MPL
        + 'def helper():\n    plt.savefig("x.png")\n    open("x.png", "rb").read()\ndef outer():\n    def helper():\n        pass\n    helper()\nouter()\nhelper()\n',
    ),
]


@pytest.mark.parametrize("name,src", REBOUND_FUNCTIONS, ids=[c[0] for c in REBOUND_FUNCTIONS])
def test_a_function_name_rebound_anywhere_is_not_proven_reachable(name, src):
    route = _route(src)
    assert route.execution == "safe" and "R1" not in _rules(route), route.public()


# ---------------------------------------------------------------- Windows 真路径（ntpath 风格，在任何平台上模拟）
@pytest.fixture
def windows_paths(monkeypatch):
    """把 `runroute` 里的 `os` 换成 ntpath 语义的最小替身（`os.path` / `os.sep`），在非 Windows 上复现 Windows 的路径判断。"""
    import ntpath
    import types

    fake = types.SimpleNamespace(
        path=ntpath, sep="\\", fspath=__import__("os").fspath, PathLike=__import__("os").PathLike
    )
    monkeypatch.setattr(runroute, "os", fake)


def _win_chain(tmp, final):
    return f"""
import os
open({tmp!r}, "w").write("1")
os.replace({tmp!r}, {final!r})
open({final!r}).read()
"""


def _win_route(src, cwd=runroute.CWD_PROJECT_ROOT):
    return runroute.decide(
        _ungated(src), cwd_mode=cwd, project_root=r"C:\proj", script=r"figs\fig.py"
    )


@pytest.mark.parametrize(
    "tmp,final",
    [
        (r"C:\proj\figs\a.tmp", r"C:\proj\figs\a.dat"),
        ("C:/proj/figs/a.tmp", "C:/proj/figs/a.dat"),
        (r"c:\PROJ\figs\a.tmp", r"c:\PROJ\figs\a.dat"),  # 盘符与目录大小写不敏感
    ],
)
def test_windows_absolute_paths_under_the_project_root_are_inside(windows_paths, tmp, final):
    route = _win_route(_win_chain(tmp, final))
    assert route.execution == "native" and "R3" in _rules(route), route.public()


@pytest.mark.parametrize(
    "tmp,final",
    [
        (r"D:\other\a.tmp", r"D:\other\a.dat"),
        (r"C:\proj2\a.tmp", r"C:\proj2\a.dat"),  # 前缀相同但不是子目录
        (r"\\server\share\a.tmp", r"\\server\share\a.dat"),
        (r"\cache.tmp", r"\cache.dat"),
        ("C:cache.tmp", "C:cache.dat"),
    ],
)
def test_windows_paths_outside_the_project_root_never_promote(windows_paths, tmp, final):
    assert _win_route(_win_chain(tmp, final)).execution == "safe"


def test_windows_relative_names_use_the_cwd_mode_and_backslash_spellings_compare_equal(
    windows_paths,
):
    src = r"""
import os
open("sub\\a.tmp", "w").write("1")
os.replace("sub/a.tmp", "sub\\a.dat")
open("sub/a.dat").read()
"""
    assert _win_route(src, runroute.CWD_PROJECT_ROOT).execution == "native"
    assert _win_route(src, runroute.CWD_SANDBOX).execution == "safe"


# ---------------------------------------------------------------- Codex #1034 r6：high 只给「无条件执行」的位置
SAVE = 'plt.savefig("x.png")'
READ = 'open("x.png", "rb").read()'


def _ind(code, n=4):
    return "\n".join(" " * n + ln for ln in code.splitlines())


NOT_UNCONDITIONAL = [
    ("if_body", MPL + f"if flag:\n{_ind(SAVE)}\n{_ind(READ)}\n"),
    ("elif_branch", MPL + f"if a:\n    pass\nelif b:\n{_ind(SAVE)}\n{_ind(READ)}\n"),
    ("else_branch", MPL + f"if a:\n    pass\nelse:\n{_ind(SAVE)}\n{_ind(READ)}\n"),
    ("for_body", MPL + f"for i in items:\n{_ind(SAVE)}\n{_ind(READ)}\n"),
    ("while_body", MPL + f"while cond():\n{_ind(SAVE)}\n{_ind(READ)}\n"),
    ("try_body", MPL + f"try:\n{_ind(SAVE)}\n{_ind(READ)}\nexcept Exception:\n    pass\n"),
    ("except_handler", MPL + f"try:\n    pass\nexcept Exception:\n{_ind(SAVE)}\n{_ind(READ)}\n"),
    ("finally_block", MPL + f"try:\n    pass\nfinally:\n{_ind(SAVE)}\n{_ind(READ)}\n"),
    ("with_body", MPL + f"with ctx():\n{_ind(SAVE)}\n{_ind(READ)}\n"),
    ("match_case", MPL + f"match v:\n    case 1:\n{_ind(SAVE, 8)}\n{_ind(READ, 8)}\n"),
    ("save_top_but_read_conditional", MPL + f"{SAVE}\nif flag:\n{_ind(READ)}\n"),
    ("read_top_but_save_in_loop", MPL + f"for i in items:\n{_ind(SAVE)}\n{READ}\n"),
    ("lambda_body", MPL + f"f = lambda: plt.savefig('x.png')\n{READ}\n"),
    ("short_circuit", MPL + f"flag and plt.savefig('x.png')\n{READ}\n"),
    ("ternary", MPL + f"plt.savefig('x.png') if flag else None\n{READ}\n"),
    (
        "function_called_only_from_a_branch",
        MPL + f"def helper():\n{_ind(SAVE)}\n{_ind(READ)}\nif flag:\n    helper()\n",
    ),
    (
        "function_called_only_in_a_loop",
        MPL + f"def helper():\n{_ind(SAVE)}\n{_ind(READ)}\nfor i in items:\n    helper()\n",
    ),
    (
        "function_called_only_in_try",
        MPL
        + f"def helper():\n{_ind(SAVE)}\n{_ind(READ)}\ntry:\n    helper()\nexcept Exception:\n    pass\n",
    ),
    (
        "branch_inside_the_called_function",
        MPL + f"def helper():\n    if flag:\n{_ind(SAVE, 8)}\n{_ind(READ, 8)}\nhelper()\n",
    ),
]


@pytest.mark.parametrize("name,src", NOT_UNCONDITIONAL, ids=[c[0] for c in NOT_UNCONDITIONAL])
def test_r1_evidence_outside_unconditional_positions_is_never_high(name, src):
    route = _route(src)
    assert route.execution == "safe" and "R1" not in _rules(route), route.public()


R3_CHAIN = 'open("c.tmp", "w").write("1")\nos.replace("c.tmp", "c.csv")\nopen("c.csv").read()'
R3_NOT_UNCONDITIONAL = [
    ("if_body", f"import os\nif flag:\n{_ind(R3_CHAIN)}\n"),
    ("for_body", f"import os\nfor i in items:\n{_ind(R3_CHAIN)}\n"),
    ("try_body", f"import os\ntry:\n{_ind(R3_CHAIN)}\nexcept Exception:\n    pass\n"),
    ("with_body", f"import os\nwith ctx():\n{_ind(R3_CHAIN)}\n"),
    (
        "only_the_write_is_conditional",
        'import os\nif flag:\n    open("c.tmp", "w").write("1")\nos.replace("c.tmp", "c.csv")\nopen("c.csv").read()\n',
    ),
    (
        "only_the_read_is_conditional",
        'import os\nopen("c.tmp", "w").write("1")\nos.replace("c.tmp", "c.csv")\nif flag:\n    open("c.csv").read()\n',
    ),
    (
        "only_the_move_is_conditional",
        'import os\nopen("c.tmp", "w").write("1")\nif flag:\n    os.replace("c.tmp", "c.csv")\nopen("c.csv").read()\n',
    ),
]


@pytest.mark.parametrize("name,src", R3_NOT_UNCONDITIONAL, ids=[c[0] for c in R3_NOT_UNCONDITIONAL])
def test_r3_evidence_outside_unconditional_positions_is_never_high(name, src):
    route = _route(src, cwd=runroute.CWD_PROJECT)
    assert route.execution == "safe" and "R3" not in _rules(route), route.public()


UNCONDITIONAL = [
    ("module_top_level", MPL + f"{SAVE}\n{READ}\n"),
    ("main_guard_body", MPL + f'if __name__ == "__main__":\n{_ind(SAVE)}\n{_ind(READ)}\n'),
    (
        "with_item_expression_runs_before_the_body",
        MPL + 'with open("x.png", "rb") as f:\n    pass\n',
    ),
    (
        "function_called_unconditionally",
        MPL + f"def helper():\n{_ind(SAVE)}\n{_ind(READ)}\nhelper()\n",
    ),
    (
        "function_called_from_the_main_guard",
        MPL + f'def main():\n{_ind(SAVE)}\n{_ind(READ)}\nif __name__ == "__main__":\n    main()\n',
    ),
]


@pytest.mark.parametrize("name,src", UNCONDITIONAL, ids=[c[0] for c in UNCONDITIONAL])
def test_unconditional_positions_still_qualify(name, src):
    route = _route(src)
    if name.startswith(
        "with_item"
    ):  # 只有读、没有 savefig：不构成证据，仅验证 with 的 item 表达式被当作无条件
        assert route.execution == "safe"
        return
    assert route.execution == "native" and "R1" in _rules(route), route.public()


def test_a_with_item_open_is_an_unconditional_write_for_r3():
    src = """
import os
with open("c.tmp", "w") as f:
    f.write("1")
os.replace("c.tmp", "c.csv")
open("c.csv").read()
"""
    assert _route(src, cwd=runroute.CWD_PROJECT).execution == "native"


def test_a_savefig_in_a_loop_then_read_is_only_medium():
    route = _route(MPL + f"for i in range(3):\n{_ind(SAVE)}\n{_ind(READ)}\n")
    assert route.execution == "safe" and route.confidence == runroute.CONFIDENCE_MEDIUM


# ---- #1034 第五轮：无后缀 savefig 与无条件终止之后的语句
EXTENSIONLESS = [
    ("plt_no_suffix", 'plt.savefig("stage")\nopen("stage", "rb").read()\n'),
    (
        "fig_no_suffix_trailing_dot",
        'fig = plt.figure()\nfig.savefig("stage.")\nopen("stage.", "rb").read()\n',
    ),
    ("format_none_literal", 'plt.savefig("stage", format=None)\nopen("stage", "rb").read()\n'),
    (
        "format_not_literal",
        'F = input()\nplt.savefig("stage", format=F)\nopen("stage", "rb").read()\n',
    ),
    ("kwargs_may_carry_format", 'plt.savefig("stage", **{})\nopen("stage", "rb").read()\n'),
]


@pytest.mark.parametrize("name,body", EXTENSIONLESS, ids=[c[0] for c in EXTENSIONLESS])
def test_a_savefig_whose_written_name_depends_on_rcparams_never_goes_native(name, body):
    route = _route(MPL + body)
    assert route.execution == "safe" and "R1" not in _rules(route), route.public()


def test_a_literal_format_keeps_the_target_name_and_stays_high_confidence():
    route = _route(MPL + 'plt.savefig("stage", format="svg")\nopen("stage", "rb").read()\n')
    assert route.execution == "native" and "R1" in _rules(route)


def test_an_extensionless_savefig_does_not_make_the_rename_source_certain():
    route = _route(
        MPL + 'import os\nplt.savefig("t")\nos.replace("t", "f.png")\nopen("f.png", "rb").read()\n',
        cwd=runroute.CWD_PROJECT,
    )
    assert route.execution == "safe"


UNREACHABLE = [
    ("raise", "raise SystemExit\n"),
    ("sys_exit", "import sys\nsys.exit(0)\n"),
    ("os_exit", "import os\nos._exit(0)\n"),
    ("exit_builtin", "exit()\n"),
    ("quit_builtin", "quit()\n"),
    ("assert_false", "assert False\n"),
    ("while_true_no_break", "while True:\n    pass\n"),
    ("main_guard_raise", 'if __name__ == "__main__":\n    raise SystemExit\n'),
]


@pytest.mark.parametrize("name,stop", UNREACHABLE, ids=[c[0] for c in UNREACHABLE])
def test_evidence_after_an_unconditional_terminator_does_not_count(name, stop):
    tail = 'plt.savefig("x.png")\nopen("x.png", "rb").read()\n'
    route = _route(MPL + 'from pathlib import Path\nPath("victim").unlink()\n' + stop + tail)
    assert route.execution == "safe" and "R1" not in _rules(route), route.public()


def test_a_return_stops_the_function_body_and_not_the_module():
    route = _route(
        MPL
        + 'def helper():\n    return\n    plt.savefig("x.png")\n    open("x.png", "rb").read()\n'
        + "helper()\n"
    )
    assert route.execution == "safe"
    route = _route(
        MPL
        + 'def helper():\n    return\nhelper()\nplt.savefig("x.png")\nopen("x.png", "rb").read()\n'
    )
    assert route.execution == "native"


def test_a_conditional_terminator_does_not_hide_real_evidence():
    tail = 'plt.savefig("x.png")\nopen("x.png", "rb").read()\n'
    route = _route(MPL + 'if len(__import__("sys").argv) > 5:\n    raise SystemExit\n' + tail)
    assert route.execution == "native"
    route = _route(MPL + "while True:\n    break\n" + tail)
    assert route.execution == "native"


# ---- #1034 第五轮续：常量真分支 / try·with / 总是 raise 的函数 / 类体
TAIL = 'plt.savefig("x.png")\nopen("x.png", "rb").read()\n'
UNREACHABLE_MORE = [
    ("if_true_raise", "if True:\n    raise SystemExit\n"),
    ("if_one_exit", "import sys\nif 1:\n    sys.exit()\n"),
    ("try_raise", "try:\n    raise SystemExit\nexcept Exception:\n    pass\n"),
    ("try_finally_exit", "import sys\ntry:\n    pass\nfinally:\n    sys.exit()\n"),
    (
        "with_raise",
        "import contextlib\nwith contextlib.suppress(Exception):\n    raise SystemExit\n",
    ),
    ("function_always_raises", "def die():\n    raise SystemExit\ndie()\n"),
    ("function_calls_exit", "import sys\ndef die():\n    print(1)\n    sys.exit(1)\ndie()\n"),
    ("function_chain", "def a():\n    raise SystemExit\ndef b():\n    a()\nb()\n"),
    ("class_body_raise", "class Boom:\n    raise SystemExit\n"),
]


@pytest.mark.parametrize("name,stop", UNREACHABLE_MORE, ids=[c[0] for c in UNREACHABLE_MORE])
def test_evidence_after_a_provable_or_unprovable_terminator_does_not_count(name, stop):
    route = _route(MPL + stop + TAIL)
    assert route.execution == "safe" and "R1" not in _rules(route), route.public()


NOT_TERMINATORS = [
    ("if_false_raise", "if False:\n    raise SystemExit\n"),
    ("conditional_function", "def f(x):\n    if x:\n        raise SystemExit\nf(1)\n"),
    ("function_returns_first", "def f():\n    return\n    raise SystemExit\nf()\n"),
    ("function_plain_return", "def f():\n    return 1\nf()\n"),
    (
        "function_early_conditional_return",
        "def f(x):\n    if x:\n        return\n    raise SystemExit\nf(0)\n",
    ),
    (
        "decorated_function",
        "def deco(fn):\n    return fn\n@deco\ndef f():\n    raise SystemExit\nf()\n",
    ),
    ("generator_function", "def f():\n    raise SystemExit\n    yield\nf()\n"),
    ("rebound_function", "def f():\n    raise SystemExit\nf = print\nf()\n"),
    ("uncalled_function", "def f():\n    raise SystemExit\n"),
    ("try_without_terminator", "try:\n    pass\nexcept Exception:\n    pass\n"),
    ("class_body_plain", "class K:\n    x = 1\n"),
]


@pytest.mark.parametrize("name,pre", NOT_TERMINATORS, ids=[c[0] for c in NOT_TERMINATORS])
def test_things_that_are_not_provable_terminators_keep_the_real_r1_high(name, pre):
    route = _route(MPL + pre + TAIL)
    assert route.execution == "native" and "R1" in _rules(route), route.public()


# ---- #1034 第六轮：async def / 生成器的裸调用不执行函数体
HELPER_BODY = '    plt.savefig("x.png")\n    open("x.png", "rb").read()\n'
NOT_RUN_ON_CALL = [
    ("async_def", "async def helper():\n" + HELPER_BODY + "helper()\n"),
    ("generator", "def helper():\n    yield 1\n" + HELPER_BODY + "helper()\n"),
    ("yield_from", "def helper():\n    yield from []\n" + HELPER_BODY + "helper()\n"),
    (
        "async_def_run",
        "import asyncio\nasync def helper():\n" + HELPER_BODY + "asyncio.run(helper())\n",
    ),
]


@pytest.mark.parametrize("name,src", NOT_RUN_ON_CALL, ids=[c[0] for c in NOT_RUN_ON_CALL])
def test_a_bare_call_of_an_async_or_generator_function_does_not_make_its_body_live(name, src):
    route = _route(MPL + src)
    assert route.execution == "safe" and "R1" not in _rules(route), route.public()


def test_a_nested_yield_does_not_make_the_outer_function_a_generator():
    route = _route(
        MPL
        + "def helper():\n    def inner():\n        yield 1\n    f = lambda: (yield)\n"
        + HELPER_BODY
        + "helper()\n"
    )
    assert route.execution == "native"


def test_a_call_to_an_async_or_generator_function_is_not_a_terminator():
    for kind in (
        "async def die():\n    raise SystemExit\n",
        "def die():\n    raise SystemExit\n    yield\n",
    ):
        route = _route(MPL + kind + "die()\n" + TAIL)
        assert route.execution == "native", kind


# ---- 伤害闸门（#1034 第七轮）：存在 safe 守卫会拦的文件操作，运行前一律不直达 native
GATED = [
    # Codex r4282091155：条件绑定路径 + 被守卫的 unlink
    (
        "conditional_path_binding",
        MPL
        + 'from pathlib import Path\nPath("victim").unlink()\n'
        + 'if len(__import__("sys").argv) > 1:\n    p = "x.png"\nelse:\n    raise SystemExit\n'
        + "plt.savefig(p)\nopen(p).read()\n",
    ),
    ("plain_write_then_readback", MPL + 'open("victim", "w").write("x")\n' + TAIL),
    ("path_unlink_in_a_dead_function", MPL + "def f(p):\n    p.unlink()\n" + TAIL),
    (
        "path_unlink_attribute",
        MPL + 'from pathlib import Path\nPath("v").unlink(missing_ok=True)\n' + TAIL,
    ),
    ("os_rename", MPL + 'import os\nos.rename("a", "b")\n' + TAIL),
    ("os_unlink_from_import", MPL + "from os import unlink as rm\nrm('a')\n" + TAIL),
    ("os_rmdir", MPL + 'import os\nos.rmdir("d")\n' + TAIL),
    ("shutil_rmtree", MPL + 'import shutil\nshutil.rmtree("d")\n' + TAIL),
    ("shutil_move_from_import", MPL + 'from shutil import move\nmove("a", "b")\n' + TAIL),
    ("path_replace_one_arg", MPL + 'from pathlib import Path\nPath("a").replace("b")\n' + TAIL),
    ("path_write_bytes", MPL + 'from pathlib import Path\nPath("a").write_bytes(b"")\n' + TAIL),
    ("path_write_text", MPL + 'from pathlib import Path\nPath("a").write_text("x")\n' + TAIL),
    ("rebound_os_head", MPL + 'import os\nos = os\nos.remove("a")\n' + TAIL),
    ("inside_lambda", MPL + 'import os\ncb = lambda: os.remove("a")\n' + TAIL),
    ("inside_class_body", MPL + 'import os\nclass K:\n    os.remove("a")\n' + TAIL),
    # Codex r4283254482：已知写出器（与事件收集同一份集合）+ 常见科学 / 数据库写出口
    (
        "numpy_save_before_dead_pair",
        MPL
        + 'import numpy as np\nnp.save("victim.npy", [1])\n'
        + 'if len(__import__("sys").argv) > 1:\n    p = "x.png"\nelse:\n    raise SystemExit\n'
        + "plt.savefig(p)\nopen(p).read()\n",
    ),
    ("pandas_to_csv", MPL + "import pandas as pd\npd.DataFrame().to_csv('victim.csv')\n" + TAIL),
    ("pil_save", MPL + "from PIL import Image\nImage.new('L', (1, 1)).save('v.png')\n" + TAIL),
    ("scipy_savemat", MPL + "from scipy.io import savemat\nsavemat('v.mat', {})\n" + TAIL),
    ("zipfile_write_mode", MPL + "import zipfile\nzipfile.ZipFile('v.zip', 'w')\n" + TAIL),
    ("h5py_mode_kw", MPL + "import h5py\nh5py.File('v.h5', mode='a')\n" + TAIL),
    ("tarfile_w_gz", MPL + "import tarfile\ntarfile.open('v.tgz', 'w:gz')\n" + TAIL),
    ("sqlite_connect", MPL + "import sqlite3\nsqlite3.connect('v.db')\n" + TAIL),
    ("os_symlink", MPL + "import os\nos.symlink('a', 'b')\n" + TAIL),
    ("path_rename_one_arg", MPL + "from pathlib import Path\nPath('a').rename('b')\n" + TAIL),
    # Codex r4286232612：`to_*` / `write_*` 写出族按形态收口，不逐个列名
    ("pandas_to_xml", MPL + "import pandas as pd\npd.DataFrame().to_xml('victim.xml')\n" + TAIL),
    ("pandas_to_latex", MPL + "import pandas as pd\npd.DataFrame().to_latex(buf='v.tex')\n" + TAIL),
    ("polars_write_csv", MPL + "import polars as pl\npl.DataFrame().write_csv('v.csv')\n" + TAIL),
    (
        "astropy_writeto",
        MPL + "from astropy.io import fits\nfits.PrimaryHDU().writeto('v.fits')\n" + TAIL,
    ),
    # Codex r4286291290：写出器 / 守卫操作不经直接调用地出现（别名、按字面名取属性）
    (
        "bound_write_text_alias",
        MPL
        + 'from pathlib import Path\nwriter = Path("victim").write_text\nwriter("destroyed")\n'
        + 'if len(__import__("sys").argv) > 1:\n    p = "x.png"\nelse:\n    raise SystemExit\n'
        + "plt.savefig(p)\nopen(p).read()\n",
    ),
    ("os_remove_alias", MPL + "import os\nrm = os.remove\nrm('a')\n" + TAIL),
    (
        "imported_remove_passed_on",
        MPL + "from os import remove\nfs = [remove]\nfs[0]('a')\n" + TAIL,
    ),
    (
        "getattr_literal_writer",
        MPL + "from pathlib import Path\ngetattr(Path('a'), 'unlink')()\n" + TAIL,
    ),
    (
        "operator_methodcaller",
        MPL
        + "import operator\noperator.methodcaller('unlink')(__import__('pathlib').Path('a'))\n"
        + TAIL,
    ),
    # Codex r4286453690：日志写文件
    (
        "logging_basicconfig_filename",
        MPL + "import logging\nlogging.basicConfig(filename='victim.log', filemode='w')\n" + TAIL,
    ),
    ("logging_file_handler", MPL + "import logging\nlogging.FileHandler('v.log')\n" + TAIL),
    (
        "logging_rotating_handler",
        MPL
        + "from logging.handlers import RotatingFileHandler\nRotatingFileHandler('v.log')\n"
        + TAIL,
    ),
    ("logging_dict_config", MPL + "import logging.config\nlogging.config.dictConfig({})\n" + TAIL),
    # Codex r4286515577：写出器对象 / 建档即落盘的容器
    (
        "pandas_excel_writer",
        MPL + "import pandas as pd\nw = pd.ExcelWriter('victim.xlsx')\n" + TAIL,
    ),
    ("imageio_get_writer", MPL + "import imageio\nimageio.get_writer('v.mp4')\n" + TAIL),
    ("pandas_hdfstore", MPL + "import pandas as pd\npd.HDFStore('v.h5')\n" + TAIL),
    (
        "pdfpages",
        MPL + "from matplotlib.backends.backend_pdf import PdfPages\nPdfPages('v.pdf')\n" + TAIL,
    ),
    ("excel_writer_alias", MPL + "import pandas as pd\nW = pd.ExcelWriter\nW('v.xlsx')\n" + TAIL),
    # Codex r4286575586：不经直接调用的 open
    (
        "path_open_alias",
        MPL + "from pathlib import Path\no = Path('victim').open\no('w').write('x')\n" + TAIL,
    ),
    ("builtin_open_alias", MPL + "o = open\no('victim', 'w').write('x')\n" + TAIL),
    (
        "getattr_literal_open",
        MPL + "from pathlib import Path\ngetattr(Path('v'), 'open')('w')\n" + TAIL,
    ),
    ("numpy_save_alias", MPL + "import numpy as np\nsv = np.save\nsv('v.npy', [1])\n" + TAIL),
]


@pytest.mark.parametrize("name,src", GATED, ids=[c[0] for c in GATED])
def test_any_guarded_file_operation_in_the_script_keeps_the_route_in_safe(name, src):
    route = _route(src, gate=True)
    assert route.execution == "safe" and route.reason == runroute.REASON_GUARDED_FILE_OPS, route
    assert route.confidence == runroute.CONFIDENCE_MEDIUM


UNGATED = [
    ("plain_readback", MPL + TAIL),
    ("str_replace_two_args", MPL + 'name = "a.b".replace(".", "_")\n' + TAIL),
    ("list_remove", MPL + "xs = [1]\nxs.remove(1)\n" + TAIL),
    (
        "conversions_are_not_writes",
        MPL
        + "import matplotlib.colors as mc\nc = mc.to_rgba('r')\nimport pandas as pd\nd = pd.DataFrame().to_dict('list')\n"
        + TAIL,
    ),
    ("read_only_pathlib", MPL + 'from pathlib import Path\nPath("a").read_text()\n' + TAIL),
    (
        "pandas_rename_columns",
        MPL + "import pandas as pd\npd.DataFrame().rename(columns={'a': 'b'})\n" + TAIL,
    ),
    ("str_replace_mode_like", MPL + 'name = "x".replace("x", "a")\n' + TAIL),
    ("zipfile_read_mode", MPL + "import zipfile\nzipfile.ZipFile('v.zip', 'r')\n" + TAIL),
]


@pytest.mark.parametrize("name,src", UNGATED, ids=[c[0] for c in UNGATED])
def test_scripts_without_guarded_operations_keep_the_rule_engine_verdict(name, src):
    route = _route(src, gate=True)
    assert route.execution == "native" and route.reason == runroute.REASON_SAVEFIG_READBACK, route


def test_the_gate_table_is_the_safe_guard_table():
    from tavotto.engine import figcapture

    ops = {o for o in figcapture.BlockedFileOps.OPS if o != "savefig"}
    assert ops == {
        "Path.unlink", "Path.write_text", "os.remove", "os.unlink", "os.rmdir",
        "os.rename", "os.replace", "shutil.rmtree", "shutil.move",
    }  # fmt: skip
    for op in sorted(ops):
        mod, _, fn = op.rpartition(".")
        src = (
            MPL
            + (
                f"import {mod}\n{mod}.{fn}('a')\n"
                if mod in ("os", "shutil")
                else f"from pathlib import Path\nPath('a').{fn}('a')\n"
            )
            + TAIL
        )
        assert _route(src, gate=True).reason == runroute.REASON_GUARDED_FILE_OPS, op


def test_the_r3_rule_still_reports_its_evidence_when_the_gate_holds_it_back():
    src = """
        import os
        open("c.tmp", "w").write("1")
        os.replace("c.tmp", "c.csv")
        open("c.csv").read()
        """
    route = _route(src, cwd=runroute.CWD_PROJECT, gate=True)
    assert route.execution == "safe" and "R3" in _rules(route), route


# ---- 写入也是闸门的一部分；`__main__` 守卫例外要求 `__name__` 从没被绑定
WRITES = [
    ("open_w", 'open("a", "w")'),
    ("open_a", 'open("a", mode="a")'),
    ("open_x", 'open("a", "x")'),
    ("open_rplus", 'open("a", "r+")'),
    ("open_wb", 'open("a", "wb")'),
    ("open_mode_variable", 'M = input()\nopen("a", M)'),
    ("open_star_args", 'open(*["a", "w"])'),
    ("open_kwargs", 'open("a", **{"mode": "w"})'),
    ("io_open", 'import io\nio.open("a", "w")'),
    ("path_open_w", 'from pathlib import Path\nPath("a").open("w")'),
    ("path_open_mode_kw", 'from pathlib import Path\nPath("a").open(mode="a")'),
    ("path_open_mode_variable", 'from pathlib import Path\nM = input()\nPath("a").open(M)'),
    ("os_open_creat", 'import os\nos.open("a", os.O_WRONLY | os.O_CREAT)'),
    ("os_makedirs", 'import os\nos.makedirs("d")'),
    ("os_mkdir", 'import os\nos.mkdir("d")'),
    ("path_mkdir", 'from pathlib import Path\nPath("d").mkdir()'),
    ("path_touch", 'from pathlib import Path\nPath("d").touch()'),
    ("shutil_copy", 'import shutil\nshutil.copy("a", "b")'),
    ("shutil_copyfile", 'import shutil\nshutil.copyfile("a", "b")'),
    ("shutil_copytree", 'from shutil import copytree\ncopytree("a", "b")'),
]


@pytest.mark.parametrize("name,call", WRITES, ids=[c[0] for c in WRITES])
def test_any_write_in_the_script_keeps_the_route_in_safe(name, call):
    route = _route(MPL + call + "\n" + TAIL, gate=True)
    assert route.execution == "safe" and route.reason == runroute.REASON_GUARDED_FILE_OPS, route


READS = [
    ("open_default", 'open("a")'),
    ("open_r", 'open("a", "r")'),
    ("open_rb", 'open("a", mode="rb")'),
    ("image_open_variable", "from PIL import Image\nP = input()\nImage.open(P)"),
    ("image_open_literal", 'from PIL import Image\nImage.open("a.png")'),
    ("path_open_read", 'from pathlib import Path\nPath("a").open("rb")'),
    ("os_open_rdonly", 'import os\nos.open("a", os.O_RDONLY)'),
]


@pytest.mark.parametrize("name,call", READS, ids=[c[0] for c in READS])
def test_read_only_opens_do_not_trip_the_gate(name, call):
    route = _route(MPL + call + "\n" + TAIL, gate=True)
    assert route.execution == "native" and route.reason == runroute.REASON_SAVEFIG_READBACK, route


REBINDS = [
    ("assign", '__name__ = "disabled"'),
    ("augassign", '__name__ += "x"'),
    ("annassign", '__name__: str = "x"'),
    ("walrus", '(__name__ := "x")'),
    ("delete", "del __name__"),
    ("for_target", 'for __name__ in ["x"]:\n    pass'),
    ("with_target", "import io\nwith io.StringIO() as __name__:\n    pass"),
    ("import_as", "import os as __name__"),
    ("from_import_as", "from os import path as __name__"),
    ("global_stmt", 'def f():\n    global __name__\n    __name__ = "x"'),
    ("def_name", "def __name__():\n    pass"),
    ("param", "def f(__name__):\n    pass"),
    ("globals_call", 'globals()["__name__"] = "x"'),
    ("except_target", "try:\n    pass\nexcept Exception as __name__:\n    pass"),
]


@pytest.mark.parametrize("name,stmt", REBINDS, ids=[c[0] for c in REBINDS])
def test_a_main_guard_body_is_not_unconditional_when_name_is_rebound_anywhere(name, stmt):
    src = (
        MPL
        + stmt
        + '\nif __name__ == "__main__":\n    plt.savefig("x.png")\n    open("x.png").read()\n'
    )
    route = _route(src, gate=True)
    assert route.execution == "safe" and "R1" not in _rules(route), route


def test_an_untouched_name_keeps_the_main_guard_exception():
    route = _route(
        MPL + 'if __name__ == "__main__":\n    plt.savefig("x.png")\n    open("x.png").read()\n',
        gate=True,
    )
    assert route.execution == "native"


def test_a_plain_write_followed_by_a_dead_main_guard_pair_stays_in_safe():
    """Codex r4283063681：写入 + 改写 `__name__` + 走不到的守卫体；守卫例外不成立，R1 不出，且写入本身也触发闸门。"""
    src = (
        MPL
        + 'open("victim", "w").write("x")\n__name__ = "disabled"\n'
        + 'if __name__ == "__main__":\n    plt.savefig("x.png")\n    open("x.png").read()\n'
    )
    route = _route(src, gate=True)
    assert route.execution == "safe" and "R1" not in _rules(route), route
    assert runroute.facts_of(src).guarded_ops


# ---- 求值顺序（Codex #1034 r4283403948）：嵌在 savefig 实参里的读先于 savefig 求值，不是「存后读回」
@pytest.mark.parametrize(
    "src",
    [
        MPL + 'plt.savefig("x.png", metadata={"Title": open("x.png").read()})\n',
        MPL
        + 'plt.savefig(\n    "x.png",\n    metadata={"Title": open("x.png", "rb").read().hex()},\n)\n',
    ],
    ids=["same_line", "multi_line"],
)
def test_a_read_nested_in_the_savefig_arguments_is_not_a_readback(src):
    route = _route(src)
    assert route.execution == "safe", route


def test_a_read_after_the_savefig_call_on_the_same_line_is_still_a_readback():
    route = _route(MPL + 'plt.savefig("x.png"); open("x.png", "rb").read()\n')
    assert route.execution == "native" and route.reason == runroute.REASON_SAVEFIG_READBACK, route


# ---- 源码编码（Codex #1034 r4283569553）：按 PEP 263 解码，不用 replacement 把不同路径折成同一个
def test_a_latin1_script_keeps_distinct_paths_distinct(tmp_path):
    src = '# -*- coding: latin-1 -*-\nimport matplotlib.pyplot as plt\nplt.savefig("caf\xe9.png")\nopen("caf\xf1.png", "rb").read()\n'
    script = tmp_path / "fig.py"
    script.write_bytes(src.encode("latin-1"))
    route = runroute.route_for_file(
        script, cwd_mode="project", project_root=tmp_path, script="fig.py"
    )
    assert route.execution == "safe", route
    route = runroute.route_for_file(
        script,
        cwd_mode="project",
        project_root=tmp_path,
        script="fig.py",
        source=script.read_bytes(),
    )
    assert route.execution == "safe", route


def test_a_latin1_script_with_a_real_readback_is_still_understood(tmp_path):
    src = '# -*- coding: latin-1 -*-\nimport matplotlib.pyplot as plt\nplt.savefig("caf\xe9.png")\nopen("caf\xe9.png", "rb").read()\n'
    script = tmp_path / "fig.py"
    script.write_bytes(src.encode("latin-1"))
    route = runroute.route_for_file(
        script, cwd_mode="project", project_root=tmp_path, script="fig.py"
    )
    assert route.execution == "native" and route.reason == runroute.REASON_SAVEFIG_READBACK, route


def test_an_undecodable_script_is_not_routed(tmp_path):
    script = tmp_path / "fig.py"
    script.write_bytes(
        b'import matplotlib.pyplot as plt\nplt.savefig("\xff.png")\nopen("\xff.png", "rb").read()\n'
    )
    route = runroute.route_for_file(
        script, cwd_mode="project", project_root=tmp_path, script="fig.py"
    )
    assert route.execution == "safe", route


# ---- `with_suffix("")` 去掉后缀（Codex #1034 r4283716227）
def test_with_suffix_empty_removes_the_suffix():
    src = (
        MPL
        + 'from pathlib import Path\np = Path("x.png").with_suffix("")\n'
        + 'plt.savefig(p, format="png")\nopen("x.png", "rb").read()\n'
    )
    route = _route(src)
    assert route.execution == "safe", route


def test_with_suffix_replacement_still_folds():
    src = (
        MPL
        + 'from pathlib import Path\np = Path("x.jpg").with_suffix(".png")\n'
        + 'plt.savefig(p)\nopen("x.png", "rb").read()\n'
    )
    route = _route(src)
    assert route.execution == "native" and route.reason == runroute.REASON_SAVEFIG_READBACK, route


# ---- 改当前目录（Codex #1034 r4283860478）：cwd 相对的存与读可能不在同一目录
@pytest.mark.parametrize(
    "mid",
    [
        'import os\nos.chdir("sub")\n',
        "from os import chdir\nchdir('sub')\n",
        "import contextlib\nwith contextlib.chdir('sub'):\n    pass\n",
    ],
    ids=["os_chdir", "from_import", "contextlib_chdir"],
)
def test_a_cwd_change_keeps_cwd_relative_readbacks_out_of_native(mid):
    src = MPL + 'plt.savefig("x.png")\n' + mid + 'open("x.png", "rb").read()\n'
    route = _route(src)
    assert route.execution == "safe" and route.confidence == runroute.CONFIDENCE_MEDIUM, route


# ---- 路径规范化保留文件系统语义（Codex #1034 r4283860490）
@pytest.mark.skipif(os.name == "nt", reason="Windows 上反斜杠就是分隔符")
def test_a_backslash_is_a_filename_character_on_posix():
    route = _route(MPL + 'plt.savefig(r"a\\b.png")\nopen("a/b.png", "rb").read()\n')
    assert route.execution == "safe", route


def test_a_dotdot_component_is_not_folded_through_a_possible_symlink():
    route = _route(MPL + 'plt.savefig("link/../x.png")\nopen("x.png", "rb").read()\n')
    assert route.execution == "safe", route


def test_path_parent_still_folds():
    src = (
        MPL
        + 'from pathlib import Path\np = Path("out/sub/x.png")\n'
        + 'plt.savefig(p.parent / "y.png")\nopen("out/sub/y.png", "rb").read()\n'
    )
    route = _route(src)
    assert route.execution == "native" and route.reason == runroute.REASON_SAVEFIG_READBACK, route


# ---- f-string 转换与异风格纯路径（Codex #1034 r4284099930 / r4284099949）
@pytest.mark.parametrize("conv", ["!r", "!a"])
def test_an_fstring_conversion_is_not_folded_as_the_raw_value(conv):
    src = (
        MPL
        + f'p = "x.png"\nplt.savefig(f"{{p{conv}}}", format="png")\nopen("x.png", "rb").read()\n'
    )
    route = _route(src)
    assert route.execution == "safe", route


def test_an_fstring_str_conversion_still_folds():
    src = MPL + 'p = "x.png"\nplt.savefig(f"{p!s}")\nopen("x.png", "rb").read()\n'
    route = _route(src)
    assert route.execution == "native" and route.reason == runroute.REASON_SAVEFIG_READBACK, route


@pytest.mark.skipif(os.sep == "\\", reason="Windows 上 PureWindowsPath 就是本机风格")
def test_a_foreign_flavor_pure_path_is_not_comparable_on_posix():
    src = (
        MPL
        + 'from pathlib import PureWindowsPath\nplt.savefig(PureWindowsPath("a/b.png"))\n'
        + 'open("a/b.png", "rb").read()\n'
    )
    route = _route(src)
    assert route.execution == "safe", route


def test_repeated_savefig_readback_pairs_yield_one_proof_per_path_and_stay_bounded():
    """Codex #1034 r4286453702：重复的同一对 savefig / 读回不按 n² 产证据。"""
    pair = 'plt.savefig("x.png")\nopen("x.png", "rb").read()\n'
    route = _route(MPL + pair * 300)
    assert route.execution == "native"
    assert len([e for e in route.evidence if e["rule"] == "R1"]) == 1
    huge = _route(MPL + pair * 1000)  # 超出配对预算：不给高置信，回 safe
    assert huge.execution == "safe" and len(huge.evidence) < 10
