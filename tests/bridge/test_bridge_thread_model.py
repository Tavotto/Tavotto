"""线程归属与「Figure 绝不 pickle」（ADR 0020 §7 / §8）。

## 线程

matplotlib 的 Figure 不是线程安全的，而 native bridge 的控制通道天生想开一个
后台线程去读 socket。本轮的设计是**根本不开那个线程**：控制循环跑在用户的
主线程上（屏障就是主线程停在那儿服务请求的一段时间）。

"没有后台线程"比"有后台线程但约定它不碰 Figure"强：不存在的线程不会在某次
重构后开始动 Figure。`LiveFigureSession` 另有一条线程身份断言兜底——它把
约定变成断言，而约定会在某次"顺手把渲染挪到回调里"之后静默失效，失效的表现
是随机的段错误或画错的图（"碰巧没事"正是这类缺陷最常见的样子）。

## 不 pickle

Figure 始终留在创建它的进程里。跨进程只走控制命令 + manifest + SVG/PNG/PDF。
理由（ADR 0014 §6）：Figure 不可靠地 picklable（闭包 callback、打开的文件
句柄、后端画布、用户自定义 Artist 子类），失败面不可枚举；而 pickle 跨解释器
/ 跨 matplotlib 版本是未定义行为，**native 的意义恰恰是"用用户自己的
（任意版本的）环境"**。
"""

from __future__ import annotations

import ast
import json
import re
import subprocess

import pytest

from support.bridgekit import child_env, run_runner, write
from tavotto.engine import bridge

pytestmark = pytest.mark.usefixtures("clean_env")

ENGINE_DIR = bridge.RUNNER_PY.parent
REPO = ENGINE_DIR.parents[2]

SHOW_ONLY = (
    "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
    "plt.plot([1,2],[3,4])\nplt.show()\n"
)

#: 在**用户的解释器里**跑一次线程越界：本进程（.venv）没有 matplotlib，
#: 而 `LiveFigureSession` 的断言要真造一个会话才验得到。
THREAD_PROBE = """\
import importlib.util, os, sys, threading
ENGINE = sys.argv[1]
spec = importlib.util.spec_from_file_location("boot", os.path.join(ENGINE, "bridgeboot.py"))
boot = importlib.util.module_from_spec(spec); spec.loader.exec_module(boot)
pkg = boot.load_engine_modules(ENGINE, ("figcapture", "patchspec", "pathgeom", "axestraversal", "spinemodel",
                                        "tickmodel", "colorbarmodel", "legendmodel", "overrides", "manifest", "figsession"))
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig = plt.figure(); fig.add_subplot(111).plot([1, 2], [3, 4])
sess = pkg.figsession.LiveFigureSession(sys.argv[2])
sess.add_figure("t", fig, pkg.figcapture.SOURCE_PYPLOT)
sess.instrument_all()                      # 主线程：必须成立
box = []
def other():
    try:
        sess.do_render("t", [])
    except BaseException as exc:
        box.append(type(exc).__name__)
    else:
        box.append("NO_ERROR")
t = threading.Thread(target=other); t.start(); t.join()
print("RESULT", box[0])
"""


# ===========================================================================
# 线程归属
# ===========================================================================
def test_the_session_refuses_to_be_touched_from_another_thread(user_python, tmp_path):
    """从别的线程动 Figure —— 抛 `WrongThread`，不是"碰巧没事"。

    反证：把 `LiveFigureSession._own()` 的方法体改成 `return None`，
    本条当场红（拿到 `NO_ERROR`）。
    """
    probe = tmp_path / "thread_probe.py"
    probe.write_text(THREAD_PROBE, encoding="utf-8")
    r = subprocess.run(
        [user_python, str(probe), str(ENGINE_DIR), str(tmp_path / "out")],
        env=child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert r.returncode == 0, r.stderr
    assert "RESULT WrongThread" in r.stdout, r.stdout + r.stderr


def test_every_mutating_entry_carries_the_thread_assertion():
    """`LiveFigureSession` 每个会动 Figure 的入口都调了 `_own()`。

    上面那条只走到 `do_render` 一处。这条按源码盖住整族——将来新加的入口
    忘了带断言，这里当场红（判据是"机制"，不是"某一行"）。
    """
    src = (ENGINE_DIR / "figsession.py").read_text(encoding="utf-8")
    bodies = dict(re.findall(r"\n    def (\w+)\(.*?\n(.*?)(?=\n    def |\Z)", src, re.S))
    must_guard = {
        "instrument_all",
        "render",
        "do_render",
        "do_render_png",
        "do_preview_png",
        "do_export",
    }
    assert must_guard <= set(bodies), f"入口名字对不上: {sorted(must_guard - set(bodies))}"
    missing = {n for n in must_guard if "self._own()" not in bodies[n]}
    assert not missing, f"这些入口没有线程断言: {sorted(missing)}"


def test_the_runner_starts_no_background_thread():
    """runner 一侧全程只有主线程——**结构性**保证，不是"我们记得不这么写"。

    判据按源码：`bridge_runner.py` / `bridgeboot.py` / `figsession.py` 里
    不许出现起线程的调用。父进程侧（`bridge.py`）可以用线程读 socket——
    那边没有 Figure，而且那条超时读法与 `pool._readline` 同源。
    """
    starter = re.compile(r"threading\.Thread\(|_thread\.start_new|concurrent\.futures")
    for name in ("bridge_runner.py", "bridgeboot.py", "figsession.py"):
        src = (ENGINE_DIR / name).read_text(encoding="utf-8")
        assert not starter.search(src), f"{name} 里起了后台线程"
    assert starter.search((ENGINE_DIR / "bridge.py").read_text(encoding="utf-8")), (
        "用例前提：父进程侧确实用线程做超时读（不是笔误）"
    )


def test_figures_are_rendered_on_the_thread_that_owns_them(user_python, tmp_path):
    """运行时的事实：渲染时的线程 id == 建 Figure 时的线程 id。

    脚本自己把主线程 id 写出来，runner 的报告带上会话的 `owner_thread`。
    这条不靠"看代码没有 Thread()"，靠真跑出来的两个数字。
    """
    proj = tmp_path / "proj"
    report = tmp_path / "report.json"
    tid = tmp_path / "script_thread.txt"
    write(
        proj / "fig.py",
        "import threading\n"
        f"open({str(tid)!r}, 'w').write(str(threading.get_ident()))\n" + SHOW_ONLY,
    )
    r = run_runner(
        user_python,
        bridge.RUNNER_PY,
        target=proj / "fig.py",
        cwd=str(proj),
        report=report,
        out_dir=tmp_path / "out",
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["stems"] == ["fig"], "用例前提：报告里的 stems 是真 instrument 出来的"
    assert data["owner_thread"] == int(tid.read_text(encoding="utf-8")), (
        "渲染发生在与建 Figure 不同的线程上"
    )
    assert data["owner_thread"] == data["main_thread"]


# ===========================================================================
# 不 pickle
# ===========================================================================
_FORBIDDEN_MODULES = ("pickle", "multiprocessing", "copyreg", "dill", "cloudpickle")
#: 唯一登记的例外（ADR 0139 §九，保存瞬间快照）：`figcapture.py` 里这四处、且只有这四处可以碰 `pickle`。
#: 它们是**同一进程内存里**的快照：字节只落在 `SaveMomentLedger._rows`，不写盘、不进任何 IPC、不进 worker 协议。
#: 登记粒度是「类 / 函数」，不是整个模块——别的函数里多一行 `pickle.dumps(fig)` 门禁照样红。
_SNAPSHOT_PICKLE_SCOPES = {
    "_RestrictedUnpickler",
    "_SnapshotPickler",
    "_snapshot_dumps",
    "SaveMomentLedger._take_into",
}
#: 快照字节的流向（正面写）：谁可以引用谁
_SNAPSHOT_REFERENCES = {
    "_snapshot_dumps": {"SaveMomentLedger._take_into"},
    "_SnapshotPickler": {"_snapshot_dumps"},
    "_RestrictedUnpickler": {"SaveMomentLedger.restore"},
}
_DYNAMIC_NAME_CALLS = {"globals", "vars", "locals", "eval", "exec", "__import__"}
_IO_CALLS = {"open", "print"}
_IO_ATTRS = {"write", "write_bytes", "write_text", "send", "sendall", "sendto", "sendmsg", "put"}


def _registered(scope) -> bool:
    return scope is not None and (
        scope in _SNAPSHOT_PICKLE_SCOPES or scope.split(".")[0] in _SNAPSHOT_PICKLE_SCOPES
    )


def _scoped_nodes(tree):
    """(节点, 所在作用域)——作用域是顶层函数名，或「类.方法」。"""
    out = []

    def walk(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, child.name if scope is None else scope)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if scope is None:
                    inner = child.name
                elif "." not in scope and node is not None and isinstance(node, ast.ClassDef):
                    inner = f"{scope}.{child.name}"
                else:
                    inner = scope
                out.append((child, inner))
                walk(child, inner)
            else:
                out.append((child, scope))
                walk(child, scope)

    walk(tree, None)
    return out


def test_no_engine_module_ever_pickles_a_figure():
    """引擎里没有任何一处把 Figure 交给 pickle / multiprocessing——**跨进程**传 Figure 的路一条都不许有。

    ADR 0014 §6 / ADR 0020 §8 防的是 Figure 越过进程边界（失败面不可枚举、跨解释器 / matplotlib 版本未定义）。
    唯一的例外是 ADR 0139 §九 的保存瞬间快照：只在本进程内存里序列化再还原，字节不出进程。例外按「类 / 函数」登记在
    `_SNAPSHOT_PICKLE_SCOPES`（不是整个模块），并且下面用 AST 正面断言字节的流向。
    """
    offenders = []
    trees = {}
    for path in sorted(ENGINE_DIR.glob("*.py")):
        tree = trees[path.name] = ast.parse(path.read_text(encoding="utf-8"))
        for node, scope in _scoped_nodes(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for mod in names:
                if mod not in _FORBIDDEN_MODULES:
                    continue
                if path.name == "figcapture.py" and mod == "pickle" and scope is None:
                    continue  # 模块级 `import pickle`；使用处由下面按作用域查
                offenders.append(f"{path.name}: {mod}")
            if (
                path.name != "figcapture.py"
                and isinstance(node, ast.Name)
                and node.id in _FORBIDDEN_MODULES
            ):
                offenders.append(f"{path.name}: 使用 {node.id}")
    for node, scope in _scoped_nodes(trees["figcapture.py"]):
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_MODULES:
            if node.id != "pickle" or not _registered(scope):
                offenders.append(f"figcapture.py: 在 {scope or '<module>'} 里使用 {node.id}")
    assert not offenders, (
        f"这些模块可能把 Figure 序列化出去: {offenders}。"
        f"Figure 必须留在创建它的进程里（ADR 0014 §6 / ADR 0020 §8）；"
        f"唯一登记的例外是同进程内存里的保存瞬间快照（ADR 0139 §九，`_SNAPSHOT_PICKLE_SCOPES`）"
    )


def test_the_snapshot_bytes_never_leave_the_process():
    """ADR 0139 §九 的例外只有在字节出不了进程时才成立——用 AST 正面写出流向。

    * `_snapshot_dumps` / `_SnapshotPickler` / `_RestrictedUnpickler` 只许被 `_SNAPSHOT_REFERENCES` 登记的那几处引用，且全引擎只有 figcapture
      里有。**没有自作用域豁免**：它们自己的函数体 / 类体里再写 `escaped = _snapshot_dumps`、`_alias = _RestrictedUnpickler`
      也判红（只有 `def` / `class` 语句本身不产生 `ast.Name`，天然不受限）；递归需要的话单独登记；
    * 同样判红的还有：`global` / `nonlocal` 声明点名它们、`ast.Attribute` 形式（`figcapture._snapshot_dumps`、`self._snapshot_dumps`）、
      `from … import _snapshot_dumps` 的别名、以及整个字符串常量恰好等于这些名字（`getattr(mod, "_snapshot_dumps")`）；
    * 字符串键 `"blob"`（快照字节存放处）只许出现在 `SaveMomentLedger` 的方法里；
    * `SaveMomentLedger` 的方法与 `_snapshot_dumps` 里没有任何写盘 / 发送 / 打印调用，也不 `return` 字节；
    * 引擎里只有 `figsession.py` 引用 `SaveMomentLedger`（建账），且只在 `_new_save_moments` 里；
    * 快照相关作用域（和模块作用域）里不许调用 `globals` / `vars` / `locals` / `eval` / `exec` / `__import__`。

    **AST 判不出的边界**（写明，不假装）：运行期拼出来的名字（`getattr(m, "_snap" + "shot_dumps")`）、经第三方对象间接取名字、
    别的非 engine 目录里的代码对 figcapture 的引用。前者靠上面对 `globals()` 等调用的封堵与代码评审；后者靠 import 架构测试
    （别的目录不得 import engine 内部名字）。
    """
    problems = []
    for path in sorted(ENGINE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node, scope in _scoped_nodes(tree):
            ref = (
                node.id
                if isinstance(node, ast.Name)
                else node.attr
                if isinstance(node, ast.Attribute)
                else None
            )
            if ref in _SNAPSHOT_REFERENCES:
                # 只有登记的调用方可以引用；受保护符号自己的函数体 / 类体里也一样（没有自作用域豁免）
                if path.name != "figcapture.py" or scope not in _SNAPSHOT_REFERENCES[ref]:
                    problems.append(f"{path.name}: {ref} 被 {scope or '<module>'} 引用")
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                for n in node.names:
                    if n in _SNAPSHOT_REFERENCES:
                        problems.append(
                            f"{path.name}: {scope or '<module>'} 里 global/nonlocal 点名 {n}"
                        )
            if isinstance(node, ast.alias) and node.name.split(".")[-1] in _SNAPSHOT_REFERENCES:
                problems.append(f"{path.name}: import 别名引用 {node.name}")
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in _SNAPSHOT_REFERENCES
            ):
                problems.append(
                    f"{path.name}: 字符串常量 {node.value!r} 出现在 {scope or '<module>'}"
                )
            snapshot_scope = path.name == "figcapture.py" and (
                scope is None
                or scope.split(".")[0]
                in {
                    "SaveMomentLedger",
                    "_snapshot_dumps",
                    "_SnapshotPickler",
                    "_RestrictedUnpickler",
                }
            )
            if (
                snapshot_scope
                and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _DYNAMIC_NAME_CALLS
            ):
                problems.append(
                    f"{scope or '<module>'}: 调用 {node.func.id}（可不经 Name 读到绑定）"
                )
            if ref == "SaveMomentLedger" and not (
                (path.name == "figcapture.py")
                or (path.name == "figsession.py" and scope == "LiveFigureSession._new_save_moments")
            ):
                problems.append(f"{path.name}: SaveMomentLedger 被 {scope} 引用")
            if (
                isinstance(node, ast.Constant)
                and node.value == "blob"
                and not (
                    path.name == "figcapture.py" and (scope or "").startswith("SaveMomentLedger")
                )
            ):
                problems.append(f"{path.name}: 快照字节键 'blob' 出现在 {scope}")
            in_ledger = path.name == "figcapture.py" and (
                (scope or "").startswith("SaveMomentLedger") or scope == "_snapshot_dumps"
            )
            if in_ledger and isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id in _IO_CALLS:
                    problems.append(f"{scope}: 调用 {fn.id}")
                if isinstance(fn, ast.Attribute) and fn.attr in _IO_ATTRS:
                    problems.append(f"{scope}: 调用 .{fn.attr}")
            if in_ledger and isinstance(node, ast.Return) and node.value is not None:
                for sub in ast.walk(node.value):
                    if (isinstance(sub, ast.Constant) and sub.value == "blob") or (
                        isinstance(sub, ast.Name) and sub.id == "blob"
                    ):
                        problems.append(f"{scope}: return 里带出快照字节")
    assert not problems, "保存瞬间快照的字节流向越界（ADR 0139 §九）：\n  " + "\n  ".join(problems)


def test_the_control_channel_only_ever_carries_json(user_python, tmp_path, bridge_session):
    """控制通道上跑的全是 JSON 行——**没有任何二进制 Figure**。

    判据：会话里每一份响应都能重新 JSON 编码，PDF 是**子进程自己写盘**
    产出的（通道上传的只是一个路径）。
    """
    proj = tmp_path / "proj"
    write(proj / "fig.py", SHOW_ONLY)
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        build = sess.ensure_built()
        stem = next(iter(build["stems"]))
        resp = sess.override(stem, [], inline_svg=True)
        png = sess.render_png(stem, 300)
        pdf = tmp_path / "out.pdf"
        exported = sess.export(stem, [], str(pdf))
        sess.resume()

    for frame in (build, resp, png, exported):
        json.dumps(frame, ensure_ascii=False)  # 不给 default：真有 bytes 就当场抛
    assert isinstance(resp["svg"], str) and resp["svg"].lstrip().startswith("<?xml")
    assert pdf.is_file() and pdf.read_bytes()[:4] == b"%PDF"
    assert "manifest" in resp


@pytest.mark.parametrize("term", ["pickle", "multiprocessing"])
def test_not_pickling_the_figure_is_written_down(term):
    """ADR 0020 明文交代不 pickle——文档与代码不许各说各话。"""
    adr = (REPO / "docs" / "adr" / "0020-native-matplotlib-bridge.md").read_text(encoding="utf-8")
    assert term in adr, f"ADR 0020 没有交代 {term}"
