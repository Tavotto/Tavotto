"""注入模型 A/B：Bridge Runner vs sitecustomize（ADR 0020 §2）。

两种把钩子送进用户进程的办法：

    A  Bridge Runner   <用户python> /abs/bridge_runner.py … -- <目标>
    B  sitecustomize   PYTHONPATH=<注入目录> <用户python> <目标>
                       （`site` 在启动时自动 import `sitecustomize`）

**必须用测试结果决定，不能"看起来 A 更好"就选 A。** 这个文件把两种都真跑
一遍，把差异变成断言。B 有两份实现：

* `_SITECUSTOMIZE_NAIVE` —— 直接照 B 的定义写。它在**本机的默认 Python 上
  当场把用户环境打坏**（见 `test_naive_sitecustomize_breaks_homebrew_python`）。
* `_SITECUSTOMIZE_CHAINED` —— 补上"接力调用被顶掉的那一份"。B 必须做到这
  一步才不是稻草人，而"必须做到这一步"本身就是 B 的成本之一。

结论（由下面这些断言支撑，不是由偏好支撑）：**BRIDGE_RUNNER_SELECTED**。
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from support.bridgekit import child_env, run_runner, write
from tavotto.engine import bridge

pytestmark = pytest.mark.usefixtures("clean_env")

ENGINE_DIR = bridge.RUNNER_PY.parent

_HOOKS = '''\
import importlib.util, json, os, sys
_ENGINE = os.environ["TAVOTTO_SC_ENGINE"]
_spec = importlib.util.spec_from_file_location("tvt_boot", os.path.join(_ENGINE, "bridgeboot.py"))
_boot = importlib.util.module_from_spec(_spec)
sys.modules["tvt_boot"] = _boot
_spec.loader.exec_module(_boot)
_pkg = _boot.load_engine_modules(_ENGINE, ("figcapture",))
_CAPTURE = {}

def _hook_figure(mfigure):
    real = mfigure.Figure.savefig
    def patched(self, fname, *a, **kw):
        stem = _pkg.figcapture.savefig_stem(fname)
        if stem:
            _CAPTURE.setdefault(stem, self)
        return real(self, fname, *a, **kw)
    mfigure.Figure.savefig = patched

def _hook_pyplot(plt):
    def patched(*a, **kw):
        _pkg.figcapture.collect_pyplot_figures(_CAPTURE, "fig", plt)
        return None
    plt.show = patched

_boot.PostImportHook({"matplotlib.figure": _hook_figure,
                      "matplotlib.pyplot": _hook_pyplot}).install()

import atexit
@atexit.register
def _report():
    plt = sys.modules.get("matplotlib.pyplot")
    if plt is not None:
        _pkg.figcapture.collect_pyplot_figures(_CAPTURE, "fig", plt)
    dest = os.environ.get("TAVOTTO_SC_REPORT")
    if dest:
        mine = os.path.abspath(os.environ["TAVOTTO_SC_DIR"])
        with open(dest, "w", encoding="utf-8") as f:
            json.dump({"figures": sorted(_CAPTURE),
                       "sys_path_has_inject": any(os.path.abspath(p) == mine for p in sys.path)}, f)
'''

#: B 的直白实现——照定义写，什么补丁都没打。
_SITECUSTOMIZE_NAIVE = _HOOKS

#: B 的"公平版"：接力调用被我们顶掉的那份 sitecustomize。
#: **这段代码本身就是 B 的成本**：它在重新实现 CPython 的 sitecustomize 发现
#: 逻辑，而重新实现的东西迟早与真品有出入。
_SITECUSTOMIZE_CHAINED = (
    _HOOKS
    + '''
# ---- 接力：把被我们顶掉的那份 sitecustomize 找出来跑掉 ----
_mine = os.path.abspath(os.environ["TAVOTTO_SC_DIR"])
for _p in sys.path:
    try:
        if os.path.abspath(_p) == _mine:
            continue
        _cand = os.path.join(_p, "sitecustomize.py")
        if os.path.isfile(_cand):
            _s = importlib.util.spec_from_file_location("tvt_chained_sitecustomize", _cand)
            _m = importlib.util.module_from_spec(_s)
            _s.loader.exec_module(_m)
            break
    except OSError:
        continue
'''
)

SHOW_ONLY = (
    "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
    "plt.plot([1,2],[3,4])\nplt.show()\n"
)


@pytest.fixture
def sc_naive(tmp_path):
    d = tmp_path / "inject-naive"
    write(d / "sitecustomize.py", _SITECUSTOMIZE_NAIVE)
    return d


@pytest.fixture
def sc_chained(tmp_path):
    d = tmp_path / "inject-chained"
    write(d / "sitecustomize.py", _SITECUSTOMIZE_CHAINED)
    return d


def _run_b(user_python, sc_dir, target, tmp_path, *, flags=(), env_extra=None, cwd=None, tag="b"):
    report = tmp_path / f"{tag}-report.json"
    env = child_env(
        {
            "PYTHONPATH": str(sc_dir),
            "TAVOTTO_SC_ENGINE": str(ENGINE_DIR),
            "TAVOTTO_SC_DIR": str(sc_dir),
            "TAVOTTO_SC_REPORT": str(report),
            **(env_extra or {}),
        }
    )
    r = subprocess.run(
        [user_python, *flags, str(target)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    return r, data


def _run_a(user_python, target, tmp_path, *, cwd=None, env=None, tag="a"):
    report = tmp_path / f"{tag}-report.json"
    r = run_runner(
        user_python,
        bridge.RUNNER_PY,
        target=target,
        cwd=cwd,
        report=report,
        out_dir=tmp_path / f"out-{tag}",
        env=env,
    )
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    return r, data


# ===========================================================================
# 头条：B 的直白实现在本机默认 Python 上当场把环境打坏
# ===========================================================================
def test_naive_sitecustomize_breaks_homebrew_python(user_python, tmp_path, sc_naive):
    """**`sitecustomize.py` 这条坑位早就有人占着。**

    Homebrew 的 Python 自带一份 `sitecustomize.py`，而正是它把
    `/opt/homebrew/lib/pythonX.Y/site-packages`（matplotlib 装在那儿）加进
    `sys.path` 的。B 从 `PYTHONPATH` 注入自己那份就把它顶掉了——
    **一个字节都没写错，用户环境里的 matplotlib 直接消失。**

    这不是"某台机器上的意外"：Debian/Ubuntu、公司镜像、Anaconda、以及任何
    装了 `usercustomize`/证书注入的环境都用同一个坑位。B 想安全，就必须
    先把被顶掉的那份找出来接力调用（见 `_SITECUSTOMIZE_CHAINED`）——
    也就是重新实现一遍 CPython 的发现逻辑。

    用例先证明"这台机器上确实有人占着坑"，再证明后果。
    """
    has_own = subprocess.run(
        [user_python, "-c", "import sitecustomize, sys; sys.stdout.write(sitecustomize.__file__)"],
        env=child_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if has_own.returncode != 0 or not has_own.stdout.strip():
        pytest.skip("这台机器的 Python 没有自带 sitecustomize（这条只在有人占坑时才有意义）")

    proj = tmp_path / "proj"
    write(proj / "fig.py", SHOW_ONLY)
    rb, _ = _run_b(user_python, sc_naive, proj / "fig.py", tmp_path, cwd=str(proj))
    assert rb.returncode != 0
    assert "No module named 'matplotlib'" in rb.stderr, rb.stderr

    ra, a = _run_a(user_python, proj / "fig.py", tmp_path, cwd=str(proj))
    assert ra.returncode == 0, ra.stderr
    assert [f["stem"] for f in a["figures"]] == ["fig"], "A 在同一台机器上照常工作"


# ===========================================================================
# 基准：给 B 打上接力补丁之后，两者都捕获得到（B 不是稻草人）
# ===========================================================================
def test_both_models_capture_a_show_only_figure(user_python, tmp_path, sc_chained):
    """基准：show-only 脚本两种模型都捕获得到。

    这条先立住，后面的差异才不是"B 根本没跑起来"造成的假象。
    """
    proj = tmp_path / "proj"
    write(proj / "fig.py", SHOW_ONLY)
    ra, a = _run_a(user_python, proj / "fig.py", tmp_path, cwd=str(proj))
    assert ra.returncode == 0, ra.stderr
    rb, b = _run_b(user_python, sc_chained, proj / "fig.py", tmp_path, cwd=str(proj))
    assert rb.returncode == 0, rb.stderr
    assert [f["stem"] for f in a["figures"]] == ["fig"]
    assert b["figures"] == ["fig"], "方案 B（接力版）也确实捕获得到"


# ===========================================================================
# 差异 1：sys.path 污染是**结构性**的，收不回来
# ===========================================================================
def test_sitecustomize_leaves_its_directory_on_sys_path_forever(user_python, tmp_path, sc_chained):
    """B 的注入目录**必须**留在 `sys.path` 上，否则它自己就不会被 import。

    这不是实现没写好——`PYTHONPATH` 是 CPython 启动时读的，`site` 靠它找到
    `sitecustomize`。目录留在那儿 = 我们的文件参与用户之后每一次 import 的
    解析；A 则是启动第一件事就把 engine 目录收回来。
    """
    proj = tmp_path / "proj"
    write(proj / "fig.py", SHOW_ONLY)
    _, a = _run_a(user_python, proj / "fig.py", tmp_path, cwd=str(proj))
    _, b = _run_b(user_python, sc_chained, proj / "fig.py", tmp_path, cwd=str(proj))
    assert a["engine_dir_on_sys_path_now"] is False, "A：engine 目录已收回"
    assert b["sys_path_has_inject"] is True, "B：注入目录留在 sys.path 上（结构使然）"


# ===========================================================================
# 差异 2：用户自己的 sitecustomize 被顶掉，而且**排前面也救不回来**
# ===========================================================================
def test_sitecustomize_silently_replaces_the_users_own(user_python, tmp_path, sc_naive):
    """用户环境里本来就有一个 `sitecustomize.py` 时，**只有一个会跑**。

    连"把用户的目录排在前面"都救不回来——`sitecustomize` 是个普通模块名，
    第一个找到的赢，另一个连被 import 的机会都没有。而 A 完全不碰这条路。
    """
    victim = tmp_path / "victim"
    ran = tmp_path / "user_sc_ran.txt"
    write(victim / "sitecustomize.py", f"open({str(ran)!r}, 'w').write('user sitecustomize ran')\n")
    proj = tmp_path / "proj"
    write(proj / "fig.py", "print('done')\n")  # 不碰 matplotlib：这条只看 sitecustomize

    env = child_env(
        {
            "PYTHONPATH": os.pathsep.join([str(sc_naive), str(victim)]),
            "TAVOTTO_SC_ENGINE": str(ENGINE_DIR),
            "TAVOTTO_SC_DIR": str(sc_naive),
            "TAVOTTO_SC_REPORT": str(tmp_path / "b-report.json"),
        }
    )
    subprocess.run(
        [user_python, str(proj / "fig.py")],
        cwd=str(proj),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert not ran.exists(), "用例前提：两个 sitecustomize 只有一个会跑"

    ra, _ = _run_a(
        user_python,
        proj / "fig.py",
        tmp_path,
        cwd=str(proj),
        env=child_env({"PYTHONPATH": str(victim)}),
    )
    assert ra.returncode == 0, ra.stderr
    assert ran.read_text(encoding="utf-8") == "user sitecustomize ran", (
        "A 必须让用户自己的 sitecustomize 照常跑"
    )


# ===========================================================================
# 差异 3：-E 让 B 静默失效；-S 连环境都没了
# ===========================================================================
def test_sitecustomize_silently_does_nothing_under_E(user_python, tmp_path, sc_chained):
    """`python -E`（忽略 PYTHONPATH）下 B **一张图都捕获不到，而且是静默的**。

    脚本照常跑完、退出码 0、**没有一句来自 Tavotto 的话**——用户只会看到
    "Tavotto 说这个脚本不出图"。静默的错比响亮的错难排查得多。

    （stderr 上确实会多一句 matplotlib 自己的
    `FigureCanvasAgg is non-interactive`——那是 `plt.show()` 没被我们接管时
    的**原样**行为，恰恰证明钩子没装上；它不是 Tavotto 给出的诊断。）
    """
    proj = tmp_path / "proj"
    write(proj / "fig.py", SHOW_ONLY)
    r, data = _run_b(user_python, sc_chained, proj / "fig.py", tmp_path, flags=["-E"], cwd=str(proj))
    assert r.returncode == 0, r.stderr
    assert data is None, "-E 下 B 的钩子根本没装上"
    assert "tavotto" not in r.stderr.lower(), f"没有任何来自 Tavotto 的提示: {r.stderr}"
    assert "non-interactive" in r.stderr, (
        "用例前提：show 没被接管，走的是 matplotlib 原样行为"
    )


def test_sitecustomize_does_nothing_under_S(user_python, tmp_path, sc_chained):
    """`python -S`（不 import site）下 B 同样不生效。

    `-S` 还会把 site-packages 一起关掉，所以脚本本身也跑不起来——但那是
    另一件事。这里要钉的是"B 依赖 site，而 site 是可以被关掉的"。
    """
    proj = tmp_path / "proj"
    write(proj / "fig.py", "print('ran under -S')\n")  # 不碰 matplotlib
    r, data = _run_b(user_python, sc_chained, proj / "fig.py", tmp_path, flags=["-S"], cwd=str(proj))
    assert r.returncode == 0, r.stderr
    assert "ran under -S" in r.stdout
    assert data is None, "-S 下 B 的钩子根本没装上"


def test_bridge_runner_is_immune_to_E(user_python, tmp_path):
    """同样的 `-E` 下 A 照常工作。

    A 的钩子在**它自己被执行的时候**装，与 site / PYTHONPATH 无关。
    """
    proj = tmp_path / "proj"
    write(proj / "fig.py", SHOW_ONLY)
    report = tmp_path / "report.json"
    cmd = [
        user_python,
        "-E",
        str(bridge.RUNNER_PY),
        "--target-kind",
        "script",
        "--target",
        str(proj / "fig.py"),
        "--out-dir",
        str(tmp_path / "out"),
        "--report",
        str(report),
        "--",
    ]
    r = subprocess.run(
        cmd, cwd=str(proj), env=child_env(), capture_output=True, text=True, timeout=300
    )
    assert r.returncode == 0, r.stderr
    assert [f["stem"] for f in json.loads(report.read_text(encoding="utf-8"))["figures"]] == ["fig"]


# ===========================================================================
# 差异 4：env 会传染给用户脚本起的**孙进程**
# ===========================================================================
def test_sitecustomize_infects_every_grandchild_process(user_python, tmp_path, sc_chained):
    """B 的 `PYTHONPATH` 会被用户脚本起的子进程继承——钩子装进了**孙进程**。

    科研脚本调 `subprocess` 很常见（跑求解器、调 ffmpeg、并行分片）。
    A 注入的唯一变量是 token，而且 runner 一起来就把它摘掉，孙进程干干净净。
    """
    proj = tmp_path / "proj"
    grand = tmp_path / "grand.json"
    write(
        proj / "kid.py",
        "import json, os, sys\n"
        f"json.dump({{'sc': 'tvt_boot' in sys.modules,"
        f" 'pp': os.environ.get('PYTHONPATH', ''),"
        f" 'token': os.environ.get('TAVOTTO_BRIDGE_TOKEN')}}, open({str(grand)!r}, 'w'))\n",
    )
    write(
        proj / "fig.py",
        SHOW_ONLY
        + "import subprocess, sys\nsubprocess.run([sys.executable, 'kid.py'], check=True)\n",
    )
    r, _ = _run_b(user_python, sc_chained, proj / "fig.py", tmp_path, cwd=str(proj))
    assert r.returncode == 0, r.stderr
    kid = json.loads(grand.read_text(encoding="utf-8"))
    assert kid["sc"] is True, "用例前提：B 的钩子确实传染到了孙进程"
    assert str(sc_chained) in kid["pp"]

    grand.unlink()
    spec = bridge.spec_for(str(proj / "fig.py"), interpreter=user_python, cwd=str(proj))
    sess = bridge.BridgeSession(spec, out_dir=tmp_path / "out")
    try:
        sess.start()
        for _ in range(2):  # show 屏障 + 脚本结束屏障
            sess.wait_event("barrier")
            sess.resume()
        sess.wait_event("exit")
    finally:
        sess.close()
    kid = json.loads(grand.read_text(encoding="utf-8"))
    assert kid["sc"] is False, "A 不该把钩子传染给孙进程"
    assert kid["token"] is None, "A 注入的 token 更不该漏到孙进程"


# ===========================================================================
# 差异 5：B 说不出「要跑哪个目标」
# ===========================================================================
def test_sitecustomize_cannot_express_the_target_itself(user_python, tmp_path, sc_chained):
    """B 只装钩子，**跑什么仍然要靠外面那条命令**。

    好处：`python -m pkg.mod` 的语义天然是真的（就是 CPython 自己跑的）。
    代价：Tavotto 必须把用户的 invocation 原样重放一遍，于是 argv / 引号 /
    shell 转义的还原责任跑到了父进程侧，且**没有一处能断言"我跑的就是他敲
    的那条"**。A 里这件事由 `ExecutionSpec` 独家表达，并有逐字段对拍
    （`test_bridge_invocation.py`）。
    """
    assert "--target" not in _HOOKS and "run_module" not in _HOOKS, (
        "sitecustomize 天然不知道目标是什么——它只是启动时被 import 的一段代码"
    )
    proj = tmp_path / "proj"
    write(proj / "paper" / "__init__.py", "")
    write(proj / "paper" / "figure.py", SHOW_ONLY)
    r, data = _run_b(
        user_python, sc_chained, "paper.figure", tmp_path, flags=["-m"], cwd=str(proj)
    )
    assert r.returncode == 0, r.stderr
    assert data["figures"] == ["fig"], "B 的 -m 形态确实能捕获（语义由 CPython 自己给）"
