"""桌面 worker 与浏览器 playground 的 **Figure 捕获语义必须一致**。

2026-08-21 之前不是：`worker.py` 只认 `Figure.savefig` / `paper_style.save`，
而 `browser.py` 多一条 pyplot 兜底。于是最常见的那种 AI 输出——

    import matplotlib.pyplot as plt
    plt.plot([1, 2, 3], [4, 5, 6])
    plt.show()

——在网站 `/try` 里能打开能编辑，在桌面版上一张图都捕获不到，而且不解释。
两个产品入口之间的语义分叉是不可接受的：同一份脚本，两个答案。

策略收在 `engine/figcapture.py`，两边各调一次。这个文件钉住的是**结果**：

* Regression A —— `plt.plot(...); plt.show()`，桌面与浏览器都必须捕获；
* Regression B —— 多张 pyplot Figure 的 fallback stem 确定、稳定、互不相同，
  且**不按 figure 号编号**（中间 `plt.close()` 过一次就会跳号）；
* Regression C —— 相对路径读盘（`open()` 与 `pathlib` 两条）按脚本目录解析，
  而**写**仍然只落沙盒；
* 另加两条边界：没有原始产物的 stem 不会伪装成可写回的面板；
  纯 OO（不 import pyplot）的脚本不该被兜底多捕获一张。

本进程不 import matplotlib：桌面侧经 `pool.one_shot()` 起真 worker，
浏览器侧 spawn 同一个解释器直接跑 `engine/browser.py`（与
`tests/test_browser_session.py` 同一条纪律）。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）")

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "src" / "tavotto" / "engine"

SHOW_ONLY = '''\
import matplotlib.pyplot as plt

plt.plot([1, 2, 3], [4, 5, 6])
plt.title("AI generated")
plt.show()
'''

MULTI_NO_SAVEFIG = '''\
import matplotlib.pyplot as plt

plt.figure(figsize=(3.0, 2.0))
plt.plot([1, 2, 3])
plt.title("first")

throwaway = plt.figure()      # 中间关掉一张：figure 号会跳到 4
plt.close(throwaway)

plt.figure(figsize=(3.0, 2.0))
plt.plot([3, 2, 1])
plt.title("second")

plt.figure(figsize=(3.0, 2.0))
plt.plot([2, 3, 1])
plt.title("third")

plt.show()
'''

OO_NO_PYPLOT = '''\
from matplotlib.figure import Figure

fig = Figure(figsize=(3.0, 2.0))
ax = fig.add_subplot(1, 1, 1)
ax.plot([1, 2, 3])
fig.savefig("only_one.pdf")
'''

#: 浏览器侧驱动：一个全新解释器跑一串命令，末行吐 JSON（一个 Worker 一个会话）。
_BROWSER_DRIVER = """
import json, sys
sys.path.insert(0, sys.argv[1])
import browser
out = [json.loads(browser.handle(json.dumps(r))) for r in json.load(sys.stdin)]
sys.stdout.write("\\n" + json.dumps(out))
"""


def browser_load(source: str, filename: str, workspace: Path) -> dict:
    proc = subprocess.run(
        [WORKER_PY, "-c", _BROWSER_DRIVER, str(ENGINE_DIR)],
        input=json.dumps([{"cmd": "load", "filename": filename,
                           "source": source, "workspace": str(workspace)}]),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300)
    assert proc.returncode == 0, f"浏览器驱动失败:\n{proc.stderr[-2000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])[0]


def desktop_build(figures: Path, script: str, entry: str = "__main__") -> dict:
    w = pool.one_shot(script, str(figures), entry)
    try:
        return w.ensure_built()
    finally:
        pool.discard(w)


def write(figures: Path, name: str, source: str) -> None:
    figures.mkdir(parents=True, exist_ok=True)
    (figures / name).write_text(source, encoding="utf-8")


# ===========================================================================
# Regression A：plt.plot + plt.show，一次 savefig 都没有
# ===========================================================================
class TestNoSavefigCapture:
    def test_desktop_captures_a_figure_that_was_never_saved(self, tmp_path):
        """桌面侧必须捕获。以前这里是空 dict，用户看到「捕获到 0 张图」。"""
        figs = tmp_path / "figs"
        write(figs, "show_only.py", SHOW_ONLY)
        stems = desktop_build(figs, "show_only.py").get("stems") or {}
        assert list(stems) == ["show_only"], stems

    def test_desktop_marks_it_as_having_no_original_artifact(self, tmp_path):
        """来源必须如实标成 pyplot。

        这不是装饰：`savefig` 捕获的 stem **可能**在磁盘上有一份真实产物，
        pyplot 兜底捕获的**一定没有**。渲染 / 编辑 / 导出都成立，
        「写回原始文件」无从谈起——调用方必须能分辨这两件事。
        """
        figs = tmp_path / "figs"
        write(figs, "show_only.py", SHOW_ONLY)
        stems = desktop_build(figs, "show_only.py")["stems"]
        assert stems["show_only"]["source"] == "pyplot"

    def test_browser_captures_the_same_figure(self, tmp_path):
        resp = browser_load(SHOW_ONLY, "show_only.py", tmp_path / "ws")
        assert resp.get("ok"), resp
        assert [f["stem"] for f in resp["figures"]] == ["show_only"]

    def test_both_entry_points_agree(self, tmp_path):
        """同一份脚本，两个入口必须给出**同一串 stem**。

        这条是整个文件的理由：不是「两边都能跑」，而是「两边跑出同一个答案」。
        """
        figs = tmp_path / "figs"
        write(figs, "show_only.py", SHOW_ONLY)
        desktop = sorted(desktop_build(figs, "show_only.py")["stems"])
        browser = sorted(f["stem"]
                         for f in browser_load(SHOW_ONLY, "show_only.py",
                                               tmp_path / "ws")["figures"])
        assert desktop == browser == ["show_only"]

    def test_savefig_captured_stems_are_not_marked_pyplot(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "only_one.py", OO_NO_PYPLOT)
        stems = desktop_build(figs, "only_one.py")["stems"]
        assert stems["only_one"]["source"] == "savefig"

    def test_pure_oo_script_gets_no_extra_figure(self, tmp_path):
        """完全不 import pyplot 的脚本，兜底一张都不该多捕获。

        兜底只在 `sys.modules` 里真有 pyplot 时才问它——没 import 过就不可能
        有 pyplot figure，而在 build 末尾 import 一次要白付几十毫秒。
        """
        figs = tmp_path / "figs"
        write(figs, "only_one.py", OO_NO_PYPLOT)
        assert list(desktop_build(figs, "only_one.py")["stems"]) == ["only_one"]


# ===========================================================================
# Regression B：多张 pyplot Figure 的 fallback stem
# ===========================================================================
class TestFallbackStems:
    EXPECT = ["multi", "multi-2", "multi-3"]

    def test_desktop_fallback_stems_are_deterministic(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "multi.py", MULTI_NO_SAVEFIG)
        assert sorted(desktop_build(figs, "multi.py")["stems"]) == self.EXPECT

    def test_browser_fallback_stems_match(self, tmp_path):
        resp = browser_load(MULTI_NO_SAVEFIG, "multi.py", tmp_path / "ws")
        assert resp.get("ok"), resp
        assert sorted(f["stem"] for f in resp["figures"]) == self.EXPECT

    def test_numbering_follows_capture_order_not_figure_numbers(self, tmp_path):
        """**不许按 `plt.get_fignums()` 的号编号。**

        脚本中间 `plt.close()` 过一次，figure 号就跳到 4，按号编的话第三张会
        叫 `multi-4`。号是 pyplot 的全局计数器——同一份脚本换个 matplotlib
        版本、或在同一个解释器里跑第二遍，用户的 override 就挂在一个不存在的
        stem 上，界面表现是「打开是空白的，什么都没报错」。
        """
        figs = tmp_path / "figs"
        write(figs, "multi.py", MULTI_NO_SAVEFIG)
        stems = sorted(desktop_build(figs, "multi.py")["stems"])
        assert "multi-4" not in stems
        assert stems == self.EXPECT

    def test_stems_are_stable_across_runs(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "multi.py", MULTI_NO_SAVEFIG)
        first = sorted(desktop_build(figs, "multi.py")["stems"])
        second = sorted(desktop_build(figs, "multi.py")["stems"])
        assert first == second == self.EXPECT

    def test_savefig_stems_win_and_fallback_fills_the_rest(self, tmp_path):
        """显式 savefig 认领过的名字不会被兜底顶掉，也不会重复捕获同一张图。"""
        figs = tmp_path / "figs"
        write(figs, "mixed.py", '''\
import matplotlib.pyplot as plt

fig1 = plt.figure(figsize=(3.0, 2.0))
fig1.gca().plot([1, 2, 3])
fig1.savefig("mixed.pdf")          # 认领 "mixed"

plt.figure(figsize=(3.0, 2.0))     # 没存过，靠兜底
plt.plot([3, 2, 1])
plt.show()
''')
        stems = desktop_build(figs, "mixed.py")["stems"]
        assert sorted(stems) == ["mixed", "mixed-2"]
        # 同一张 Figure 绝不出现两次（savefig 之后它还活在 pyplot 里）
        assert stems["mixed"]["source"] == "savefig"
        assert stems["mixed-2"]["source"] == "pyplot"


# ===========================================================================
# Regression C：相对路径读盘
# ===========================================================================
class TestRelativeFileIO:
    """`python figure.py` 的语义是「相对路径 = 脚本旁边那一份」。

    worker 把 cwd 切到沙盒是**写入**边界（挡住脚本用相对路径写/删真实图库），
    代价是相对**读**全部失效。修法只放开只读那一支，写、改、删、重命名一个
    字节都不经过它——见 `engine/figcapture.install_relative_read_fallback`。
    """

    def test_builtin_open_resolves_next_to_the_script(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "reader.py", '''\
import csv
import matplotlib.pyplot as plt

with open("data.csv", newline="", encoding="utf-8") as fh:
    rows = list(csv.DictReader(fh))
fig, ax = plt.subplots()
ax.plot([float(r["x"]) for r in rows], [float(r["y"]) for r in rows])
fig.savefig("reader.pdf")
''')
        (figs / "data.csv").write_text("x,y\n1,2\n2,4\n3,9\n", encoding="utf-8")
        assert list(desktop_build(figs, "reader.py")["stems"]) == ["reader"]

    def test_pathlib_read_text_resolves_too(self, tmp_path):
        """pathlib 走的是 `io.open`，**不是** `builtins.open`。

        它们指向同一个 C 函数，却是两个独立的名字绑定：只 patch builtins 的话
        `open("x")` 好使而 `Path("x").read_text()` 报 FileNotFoundError——
        两种等价写法行为不一致，是最难查的那种。
        """
        figs = tmp_path / "figs"
        write(figs, "preader.py", '''\
import json
from pathlib import Path

import matplotlib.pyplot as plt

cfg = json.loads(Path("cfg.json").read_text(encoding="utf-8"))
fig, ax = plt.subplots()
ax.plot(cfg["points"])
fig.savefig("preader.pdf")
''')
        (figs / "cfg.json").write_text('{"points": [1, 4, 9]}', encoding="utf-8")
        assert list(desktop_build(figs, "preader.py")["stems"]) == ["preader"]

    def test_relative_write_still_lands_in_the_sandbox(self, tmp_path):
        """脚本写出来的中间文件绝不能落进用户的图库目录。"""
        figs = tmp_path / "figs"
        write(figs, "writer.py", '''\
import matplotlib.pyplot as plt

with open("scratch.txt", "w", encoding="utf-8") as fh:
    fh.write("3 1 2\\n")
with open("scratch.txt", encoding="utf-8") as fh:
    ys = [float(v) for v in fh.read().split()]
fig, ax = plt.subplots()
ax.plot(ys)
fig.savefig("writer.pdf")
''')
        assert list(desktop_build(figs, "writer.py")["stems"]) == ["writer"]
        assert not (figs / "scratch.txt").exists(), \
            "脚本的相对写落进了用户的图库目录——沙盒边界被打穿了"

    def test_sandbox_copy_wins_over_the_script_directory(self, tmp_path):
        """脚本自己写出来的那一份优先——只读回退不能把它顶掉。"""
        figs = tmp_path / "figs"
        write(figs, "shadow.py", '''\
import matplotlib.pyplot as plt

with open("values.txt", "w", encoding="utf-8") as fh:
    fh.write("9 9 9\\n")
with open("values.txt", encoding="utf-8") as fh:
    ys = [float(v) for v in fh.read().split()]
assert ys == [9.0, 9.0, 9.0], f"读到的是图库里那一份: {ys}"
fig, ax = plt.subplots()
ax.plot(ys)
fig.savefig("shadow.pdf")
''')
        (figs / "values.txt").write_text("1 2 3\n", encoding="utf-8")
        assert list(desktop_build(figs, "shadow.py")["stems"]) == ["shadow"]

    def test_reads_outside_the_project_are_not_redirected(self, tmp_path):
        """越界的读不「就近找一个能用的」——原样交给真正的 open 去报错。"""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("nope", encoding="utf-8")
        figs = tmp_path / "figs"
        write(figs, "escape.py", '''\
import matplotlib.pyplot as plt

try:
    with open("../outside/secret.txt", encoding="utf-8") as fh:
        fh.read()
except FileNotFoundError:
    leaked = False
else:
    leaked = True
assert not leaked, "沙盒外的文件被只读回退送进来了"
fig, ax = plt.subplots()
ax.plot([1, 2, 3])
fig.savefig("escape.pdf")
''')
        assert list(desktop_build(figs, "escape.py")["stems"]) == ["escape"]


# ===========================================================================
# 边界：没有原始产物的图不许伪装成可写回的面板
# ===========================================================================
class TestNoFakeWriteBackTarget:
    """`plt.show()` 出来的图**没有原始文件**。渲染 / 编辑 / 导出都成立，
    「写回原始文件」必须诚实地无从谈起——绝不能给用户一个看起来成功、
    实际上没有原件可写的按钮。

    桌面的面板列表扫的是磁盘上的**产物**，所以这类 stem 天然不会成为面板；
    这里钉住的是那条结构性保证本身（它一旦松动，写回就会指向一个不存在的
    文件）。
    """

    def test_panel_scan_only_lists_real_files(self, tmp_path, monkeypatch):
        from tavotto import app
        figs = tmp_path / "figs"
        write(figs, "show_only.py", SHOW_ONLY)
        (figs / "tavotto_registry.json").write_text(json.dumps({
            "scripts": {"show_only.py": {"entry": "__main__", "cost": "light",
                                         "stems": ["show_only"]}}}),
            encoding="utf-8")
        info = app.open_project(str(figs))
        ctx = app.PROJECTS[info["id"]]
        monkeypatch.setattr(app, "current_ctx", lambda: ctx)
        monkeypatch.setattr(app, "current_registry", lambda: ctx.registry)
        monkeypatch.setattr(app, "require_project", lambda: ctx.path)
        panels = app.scan_panels()
        assert not [p for p in panels if p["name"] == "show_only"], \
            "注册表里有这个 stem，但磁盘上没有产物——它不该出现在面板列表里"

    def test_write_back_target_must_exist_on_disk(self, tmp_path, monkeypatch):
        """指向一个不存在的产物时，写回端点必须 4xx，而不是假装成功。"""
        from werkzeug.exceptions import NotFound

        from tavotto import app
        figs = tmp_path / "figs"
        figs.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(app, "require_project", lambda: figs.resolve())
        with pytest.raises(NotFound):
            app.safe_resolve("show_only.pdf")


# ===========================================================================
# 两个入口**刻意保留**的那一条差异：entry
# ===========================================================================
class TestEntrySemanticsDifferBySide:
    """桌面的 `entry` 机制是超集，浏览器按 `python figure.py` 跑——**这不是缺陷**。

    只有 `def main():` 而没人调用的脚本，在原生 Python 下也什么都不画。
    浏览器忠实复现了这一点；桌面靠注册表多知道一件事（入口叫什么），
    于是能把它跑出来。

    钉住它是为了让这条差异**显式**：将来谁想在 playground 里加一条
    「没捕获到就试着调 main()」的启发式，会先撞到这个用例，并且必须在这里
    说明为什么那样做比复现 `python figure.py` 更好。
    """

    ENTRY_ONLY = '''\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3])
    fig.savefig("entry_only.pdf")
'''

    def test_desktop_runs_the_registered_entry(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "entry_only.py", self.ENTRY_ONLY)
        stems = desktop_build(figs, "entry_only.py", entry="main")["stems"]
        assert list(stems) == ["entry_only"]

    def test_browser_captures_nothing_and_says_so(self, tmp_path):
        resp = browser_load(self.ENTRY_ONLY, "entry_only.py", tmp_path / "ws")
        assert resp.get("ok"), resp
        assert resp["figures"] == [], (
            "playground 应当复现 `python entry_only.py` 的行为（什么都不画），"
            "而不是自作主张替用户调 main()")

    def test_a_guarded_script_works_on_both_sides(self, tmp_path):
        """加上 `if __name__ == "__main__"` 守卫之后两边一致——AI 生成的脚本
        绝大多数带这条守卫，所以这条差异在实践中很少咬人。"""
        source = self.ENTRY_ONLY + '\n\nif __name__ == "__main__":\n    main()\n'
        figs = tmp_path / "figs"
        write(figs, "guarded.py", source)
        desktop = sorted(desktop_build(figs, "guarded.py", entry="__main__")["stems"])
        browser = sorted(f["stem"] for f in
                         browser_load(source, "guarded.py", tmp_path / "ws")["figures"])
        assert desktop == browser == ["entry_only"]
