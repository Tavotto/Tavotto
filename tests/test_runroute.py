"""V2 PR2：运行前路由 `runroute`（ADR 0128）的规则向量、优先级与同源钉。

向量表驱动：脚本片段 → 期望的路由 / 命中规则。每条「高置信直达 native」的向量对应一个运行期会被拦的形状
（`BlockedFileOps` 的观察：`tests/test_runroute_consistency.py` 用真 safe worker 逐条核对静态与运行期不分叉）。
"""

from __future__ import annotations

import textwrap

import pytest

from tavotto.engine import databinding, execpolicy, figcapture, inputremap, runroute, workdir

ROOT = "/proj"
SCRIPT = "figs/fig.py"


def _route(src, *, cwd=runroute.CWD_SANDBOX, **kw):
    facts = runroute.facts_of(textwrap.dedent(src))
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
        "savefig_in_loop_then_read",
        """
        import matplotlib.pyplot as plt
        from PIL import Image
        for i in range(3):
            plt.savefig("t.png")
            Image.open("t.png")
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
    inside = f"""
        import os
        open({ROOT + "/a.tmp"!r}, "w").write("1")
        os.replace({ROOT + "/a.tmp"!r}, {ROOT + "/a.dat"!r})
        open({ROOT + "/a.dat"!r}).read()
        """
    outside = inside.replace(ROOT, "/elsewhere")
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
    assert runroute.time.perf_counter() - start < 5.0  # 上限护栏，不是基准；基准数字记在 ADR 0128


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
