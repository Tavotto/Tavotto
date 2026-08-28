"""Figure 捕获：native bridge 必须覆盖真实脚本的各种形态（ADR 0020 §5）。

捕获**策略**（stem 怎么编、怎么去重、上限多少、描述符怎么造）是
`figcapture` 那一份——safe worker 与浏览器 playground 用的是同一个函数。
这里验的是 native 这条入口有没有正确地**用**它，以及 native 独有的那几条：
savefig 透传、show 屏障、脚本抛异常/退出之后图还在。

全部真起子进程（`--report` 形态：跑一遍、写小结、退出）。
"""

from __future__ import annotations

import json

import pytest

from support.bridgekit import run_runner, write
from tavotto.engine import bridge

pytestmark = pytest.mark.usefixtures("clean_env")

HEAD = "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"


def _run(user_python, tmp_path, body: str, *, head: str = HEAD, argv=(), name="fig.py"):
    proj = tmp_path / "proj"
    write(proj / name, head + body)
    report = tmp_path / "report.json"
    r = run_runner(
        user_python,
        bridge.RUNNER_PY,
        target=proj / name,
        cwd=str(proj),
        report=report,
        out_dir=tmp_path / "out",
        argv=argv,
    )
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    return r, data


def _stems(data) -> list:
    return [f["stem"] for f in data["figures"]]


def _sources(data) -> dict:
    return {f["stem"]: f["capture_source"] for f in data["figures"]}


# ===========================================================================
# 1–4：基本形态
# ===========================================================================
def test_show_only_script_is_captured(user_python, tmp_path):
    """`plt.plot(...); plt.show()`——AI 最常写的形态，从不 savefig。

    safe worker 也能捕获它（figcapture 的 pyplot 兜底），但 safe 要求脚本能
    在沙盒 cwd + 我们挑的解释器里跑得起来。native 这条路把那两个前提都去掉了。
    """
    r, data = _run(user_python, tmp_path, "plt.plot([1,2,3],[4,5,6])\nplt.show()\n")
    assert r.returncode == 0, r.stderr
    assert _stems(data) == ["fig"]
    assert _sources(data) == {"fig": "pyplot"}


def test_savefig_is_captured_and_passed_through(user_python, tmp_path):
    """`fig.savefig(...)` 既进捕获表，**也照常写盘**。

    这是 native 与 safe 最尖锐的一条分歧：safe 吞掉写盘（沙盒纪律），
    native 透传——`tavotto run` 的承诺是"与你自己在终端里跑这条命令完全
    等同"，而用户的命令本来就会产出那个 PDF。
    """
    r, data = _run(
        user_python,
        tmp_path,
        "fig, ax = plt.subplots()\nax.plot([1,2],[3,4])\nfig.savefig('Fig1.pdf')\n",
    )
    assert r.returncode == 0, r.stderr
    assert _stems(data) == ["Fig1"]
    assert _sources(data) == {"Fig1": "savefig"}
    written = tmp_path / "proj" / "Fig1.pdf"
    assert written.is_file() and written.stat().st_size > 0, "savefig 必须真的写出文件"


def test_no_show_no_savefig_is_still_captured(user_python, tmp_path):
    """既不 show 也不 savefig——脚本返回之后从 Gcf 里收。"""
    r, data = _run(user_python, tmp_path, "fig, ax = plt.subplots()\nax.plot([1,2],[3,4])\n")
    assert r.returncode == 0, r.stderr
    assert _stems(data) == ["fig"]
    assert _sources(data) == {"fig": "pyplot"}


def test_multiple_figures_are_all_captured_in_order(user_python, tmp_path):
    """多 Figure 一张不丢，序号只由「本次捕获里的第几张」决定。"""
    r, data = _run(
        user_python,
        tmp_path,
        "for i in range(3):\n    plt.figure()\n    plt.plot([1,2],[i,i+1])\nplt.show()\n",
    )
    assert r.returncode == 0, r.stderr
    assert _stems(data) == ["fig", "fig-2", "fig-3"]


# ===========================================================================
# 5–8：去重与边界
# ===========================================================================
def test_savefig_then_still_in_gcf_is_not_captured_twice(user_python, tmp_path):
    """savefig 之后图还活在 Gcf 里——**不许**同一张图挂两个 stem。

    去重按 Figure 身份（`id(fig)`），不是按名字：不去重的话用户看到两个
    一模一样的面板，改一个另一个不动。
    """
    r, data = _run(
        user_python,
        tmp_path,
        "fig, ax = plt.subplots()\nax.plot([1,2],[3,4])\nfig.savefig('Fig1.pdf')\nplt.show()\n",
    )
    assert r.returncode == 0, r.stderr
    assert _stems(data) == ["Fig1"], "同一张图被捕获了两次"


def test_oo_figure_never_owned_by_pyplot_is_captured_via_savefig(user_python, tmp_path):
    """纯 OO（`Figure()` 自己 new，不经 pyplot）+ savefig 也要捕获得到。

    这类图**不在** Gcf 里，pyplot 兜底看不到它——只有 savefig 钩子能认领。
    脚本连 pyplot 都不 import，所以顺便证明 show 钩子没被强行装上去。
    """
    r, data = _run(
        user_python,
        tmp_path,
        "fig = Figure(figsize=(3,2))\n"
        "ax = fig.add_subplot(111)\n"
        "ax.plot([1,2],[3,4])\n"
        "fig.savefig('OO.pdf')\n",
        head="import matplotlib\nmatplotlib.use('Agg')\nfrom matplotlib.figure import Figure\n",
    )
    assert r.returncode == 0, r.stderr
    assert _stems(data) == ["OO"]
    assert _sources(data) == {"OO": "savefig"}


def test_closed_figures_are_not_captured(user_python, tmp_path):
    """`plt.close()` 掉的图不该出现——用户明确把它扔了。

    但**savefig 认领过的除外**：那时我们已经持有强引用，图仍可编辑。
    这条同时钉住"close 不等于捕获表也跟着清空"。
    """
    r, data = _run(
        user_python,
        tmp_path,
        "a = plt.figure(); plt.plot([1,2],[1,2])\n"
        "a.savefig('Kept.pdf')\n"
        "plt.close(a)\n"
        "b = plt.figure(); plt.plot([1,2],[2,1])\n"
        "plt.close(b)\n"
        "c = plt.figure(); plt.plot([1,2],[3,3])\n"
        "plt.show()\n",
    )
    assert r.returncode == 0, r.stderr
    assert _stems(data) == ["Kept", "fig"], "被 close 的裸图不该进表，savefig 认领过的要留下"


def test_figure_number_gaps_never_leak_into_the_stem(user_python, tmp_path):
    """figure 号有洞时 stem 仍然连号。

    号是 pyplot 的全局计数器：脚本中途 close 过一次号就跳了。stem 里编进
    figure 号的话，同一份脚本换个 matplotlib 版本、或者在同一个解释器里跑
    第二遍，用户的 override 就挂在一个不存在的 stem 上——界面表现是
    「打开是空白的，什么都没报错」。
    """
    r, data = _run(
        user_python,
        tmp_path,
        "f1 = plt.figure(); f2 = plt.figure(); f3 = plt.figure()\n"
        "plt.close(f2)\n"  # 号变成 1, 3
        "f4 = plt.figure()\n"  # 号 4
        "for f in (f1, f3, f4):\n"
        "    f.add_subplot(111).plot([1,2],[1,2])\n"
        "plt.show()\n"
        "import sys; print('NUMS', plt.get_fignums(), file=sys.stderr)\n",
    )
    assert r.returncode == 0, r.stderr
    assert "NUMS [1, 3, 4]" in r.stderr, "用例前提：figure 号确实有洞"
    assert _stems(data) == ["fig", "fig-2", "fig-3"]


# ===========================================================================
# 9–12：生命周期与 stdout
# ===========================================================================
def test_repeated_show_captures_each_new_figure_once(user_python, tmp_path):
    """重复 `show()`：每次都收一遍 Gcf，已捕获的按身份去重。"""
    r, data = _run(
        user_python,
        tmp_path,
        "plt.figure(); plt.plot([1,2],[1,2])\n"
        "plt.show()\n"
        "plt.figure(); plt.plot([1,2],[2,1])\n"
        "plt.show()\n"
        "plt.show()\n",
    )
    assert r.returncode == 0, r.stderr
    assert _stems(data) == ["fig", "fig-2"]


def test_a_figure_created_after_the_first_barrier_reaches_the_session(
    user_python, tmp_path, bridge_session
):
    """`show()` → 编辑 → 继续 → **再画一张** → 再 `show()`：第二张必须出现。

    钩子写的是模块级捕获表（它们是类属性级 monkeypatch，拿不到会话实例），
    而会话是在**第一个屏障**那一刻才建的。此后产的图只落进模块级表——不把
    它们同步进会话，第二个屏障里 stems / build 响应 / 可编辑会话里都没有它，
    而脚本明明画出来了。**用户会数图。**

    上面那条 `test_repeated_show_captures_each_new_figure_once` 测不到这个：
    它跑在 `--report` 形态（没有控制通道），屏障立刻返回，会话是脚本跑完
    才建的一次性对象——那时所有图早就都在表里了。**判据必须走真屏障。**

    反证：把 `BridgeRun._ensure_session` 里复用分支的 `_sync_captures()`
    去掉，本条当场红（屏障 2 只有一张图）。
    """
    proj = tmp_path / "proj"
    write(
        proj / "fig.py",
        HEAD + "plt.figure(); plt.plot([1,2],[1,2]); plt.title('first')\n"
        "plt.show()\n"
        "plt.figure(); plt.plot([1,2],[2,1]); plt.title('second')\n"
        "plt.show()\n",
    )
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        first = sess.wait_event("barrier")
        assert first["stems"] == ["fig"], "屏障 1 只该有第一张"
        assert list(sess.ensure_built()["stems"]) == ["fig"]
        sess.resume()

        second = sess.wait_event("barrier")
        assert second["stems"] == ["fig", "fig-2"], "屏障 2 少了脚本刚画的那张"
        build = sess.ensure_built()
        assert list(build["stems"]) == ["fig", "fig-2"]
        # 新图要真的可编辑（不只是名字出现在清单里）
        assert sess.override("fig-2", [])["warnings"] == []
        assert (sess.out_dir / "fig-2.svg").is_file()
        sess.resume()
        sess.wait_event("barrier")
        sess.resume()
        sess.wait_event("exit")


def test_editing_survives_the_next_barrier(user_python, tmp_path, bridge_session):
    """同步新图**不许**把已经在编辑的那张重建掉。

    `instrument_all()` 只给还没有 FigState 的图建状态——已经在编辑的那些
    带着用户的 override，重建等于把编辑丢掉。这条与上一条是同一次修复的
    两面：一面是"新的要进来"，一面是"旧的不许被撞掉"。
    """
    proj = tmp_path / "proj"
    write(
        proj / "fig.py",
        HEAD + "plt.figure(); plt.plot([1,2],[1,2]); plt.title('T')\n"
        "plt.show()\n"
        "plt.figure(); plt.plot([1,2],[2,1])\n"
        "plt.show()\n",
    )
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        man = sess.ensure_built()
        stem = next(iter(man["stems"]))
        doc = json.loads((sess.out_dir / f"{stem}.json").read_text(encoding="utf-8"))
        gid = next(
            el["gid"]
            for el in doc["elements"]
            for f in el.get("editable", [])
            if f["prop"] == "text" and f["value"] == "T"
        )
        patches = [{"gid": gid, "prop": "fontsize", "value": 21.0}]
        assert sess.override(stem, patches)["warnings"] == []
        sess.resume()

        sess.wait_event("barrier")
        again = sess.override(stem, patches)  # 全量列表语义：同一组 patch 应当仍然成立
        el = next(e for e in again["manifest"]["elements"] if e["gid"] == gid)
        size = next(f["value"] for f in el["editable"] if f["prop"] == "fontsize")
        assert size == 21.0, "屏障之后这张图的编辑被冲掉了"
        sess.resume()
        # 两次 `show()` 之后还有**脚本结束**那一次屏障——不应答它就是两边
        # 各等各的（我等 exit，它等 continue），一路挂到 BARRIER_TIMEOUT。
        sess.wait_event("barrier")
        sess.resume()
        sess.wait_event("exit")


def test_sys_exit_still_hands_over_the_figures(user_python, tmp_path):
    """脚本 `sys.exit(0)`——图已经画出来了，不该跟着蒸发。

    退出码原样带出去（`sys.exit(3)` 就是 3）：bridge 不改用户程序的结果。
    """
    r, data = _run(
        user_python,
        tmp_path,
        "import sys\nplt.plot([1,2],[3,4])\nsys.exit(3)\n",
    )
    assert r.returncode == 3, f"退出码必须原样透传，得到 {r.returncode}"
    assert _stems(data) == ["fig"]
    assert data["exit_code"] == 3
    assert data["script_error"] is None, "sys.exit 不是脚本错误"


def test_exception_after_the_figure_still_hands_it_over(user_python, tmp_path):
    """脚本在画完图之后炸了——已有的图仍然交出去，错误如实报告。

    这正是用户最需要 Tavotto 的时刻之一：图画好了，后面某一步崩了，
    而那张图本身是对的。
    """
    r, data = _run(
        user_python,
        tmp_path,
        "plt.plot([1,2],[3,4])\nraise ValueError('boom')\n",
    )
    assert r.returncode == 1
    assert _stems(data) == ["fig"]
    assert data["script_error"]["code"] == "script_error"
    assert "boom" in data["script_error"]["message"]
    assert "ValueError" in r.stderr, "traceback 必须照常打给用户看"


def test_user_stdout_is_never_mistaken_for_protocol(user_python, tmp_path, capfd):
    """用户的 stdout / stderr 原样归他，哪怕他打印的是一行合法协议 JSON。

    这是"控制通道不能偷 stdout"那条判断的判据：协议走 loopback socket，
    stdout 一个字节都不解释。
    """
    noisy = (
        "import sys\n"
        "print('hello stdout')\n"
        'print(\'{"protocol_version":1,"cmd":"shutdown","request_id":"x"}\')\n'
        "print('hello stderr', file=sys.stderr)\n"
        "plt.plot([1,2],[3,4])\nplt.show()\n"
    )
    r, data = _run(user_python, tmp_path, noisy)
    assert r.returncode == 0, r.stderr
    assert "hello stdout" in r.stdout
    assert '"cmd":"shutdown"' in r.stdout, "用户打印的协议样文本必须原样出现在他的 stdout 上"
    assert "hello stderr" in r.stderr
    assert _stems(data) == ["fig"]
