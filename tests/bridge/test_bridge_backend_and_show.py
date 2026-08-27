"""后端时序与 `plt.show()` 语义（ADR 0020 §4 / §5）。

两条主张：

1. **bridge 绝不提前 import pyplot。** 用户脚本有权决定后端，而
   `matplotlib.use()` 只在 pyplot 还没 import 时是"纯"的——之后它变成
   `switch_backend()`，语义、告警、乃至（视版本而定）活着的 Figure 都可能
   不一样。实测 matplotlib 3.10.8 的 `switch_backend` 源码里确实有
   `close("all")`，只是当前版本走不到那条分支；**这种"当前版本碰巧没事"
   的事实不该被依赖**。
2. `show()` 的阻塞语义按脚本自己说的算：默认阻塞（进屏障），
   `block=False` 立刻返回。不把 show 永久换成 no-op。
"""

from __future__ import annotations

import json

import pytest

from support.bridgekit import run_runner, write
from tavotto.engine import bridge

pytestmark = pytest.mark.usefixtures("clean_env")


def _run(user_python, tmp_path, body: str, **kw):
    proj = tmp_path / "proj"
    write(proj / "fig.py", body)
    report = tmp_path / "report.json"
    r = run_runner(
        user_python,
        bridge.RUNNER_PY,
        target=proj / "fig.py",
        cwd=str(proj),
        report=report,
        out_dir=tmp_path / "out",
        **kw,
    )
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    return r, data


def test_user_code_is_the_first_to_import_pyplot(user_python, tmp_path):
    """脚本第一行就断言 pyplot 还没被 import——bridge 抢先一步就当场红。

    这是本条最直接的判据：判的是"谁决定 pyplot 什么时候进来"，
    而不是某个后端名字碰巧对不对。

    反证：在 `bridge_runner` 顶上加 `import matplotlib.pyplot`，本条红。
    """
    r, data = _run(
        user_python,
        tmp_path,
        "import sys\n"
        "assert 'matplotlib.pyplot' not in sys.modules, 'bridge 提前 import 了 pyplot'\n"
        "assert 'matplotlib' not in sys.modules, 'bridge 提前 import 了 matplotlib'\n"
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.plot([1,2],[3,4])\n"
        "plt.show()\n",
    )
    assert r.returncode == 0, r.stderr
    assert [f["stem"] for f in data["figures"]] == ["fig"]


@pytest.mark.parametrize("backend", ["Agg", "pdf", "svg"])
def test_the_backend_the_script_asked_for_is_the_one_it_gets(user_python, tmp_path, backend):
    """`matplotlib.use(X)` 在脚本里的**位置**仍然有效，选中的就是 X。

    三个后端都是非交互的（无头机上必然可用），差别足以证明"我们没有
    悄悄替他选一个"。
    """
    r, data = _run(
        user_python,
        tmp_path,
        "import matplotlib\n"
        f"matplotlib.use({backend!r})\n"
        "import matplotlib.pyplot as plt\n"
        f"assert matplotlib.get_backend().lower() == {backend.lower()!r}, matplotlib.get_backend()\n"
        "plt.plot([1,2],[3,4])\n"
        "plt.show()\n",
    )
    assert r.returncode == 0, r.stderr
    assert data["matplotlib_backend"].lower() == backend.lower()
    assert [f["stem"] for f in data["figures"]] == ["fig"]


def test_a_script_that_never_touches_matplotlib_runs_clean(user_python, tmp_path):
    """从不 import matplotlib 的脚本：钩子一辈子不响，报告里零张图。

    钩子挂在 `sys.meta_path` 上而不是靠"先 import 一下看看"，这条才成立。
    """
    r, data = _run(user_python, tmp_path, "print('NO MPL')\n")
    assert r.returncode == 0, r.stderr
    assert "NO MPL" in r.stdout
    assert data["figures"] == []
    assert data["matplotlib_backend"] == ""


def test_show_blocks_by_default_and_returns_after_continue(user_python, tmp_path, bridge_session):
    """默认 `show()` 进屏障；收到 `continue` 之后 show() 返回、脚本接着跑。

    与交互式后端的行为同构（窗口关掉之前 show() 不返回）。判据是脚本里
    show() **之后**那一行的输出：屏障没放开之前它不该出现。
    """
    proj = tmp_path / "proj"
    marker = tmp_path / "after.txt"
    write(
        proj / "fig.py",
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "plt.plot([1,2],[3,4])\n"
        "plt.show()\n"
        f"open({str(marker)!r}, 'w').write('after show')\n",
    )
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        ev = sess.wait_event("barrier")
        assert ev["reason"] == "show"
        assert not marker.exists(), "屏障没放开，show() 后面那行就不该跑到"
        sess.ensure_built()
        sess.resume()
        sess.wait_event("barrier")  # 脚本跑完的那一次
        assert marker.read_text(encoding="utf-8") == "after show"
        sess.resume()
        sess.wait_event("exit")


def test_show_block_false_returns_immediately(user_python, tmp_path, bridge_session):
    """`show(block=False)`：脚本明确说了不要阻塞，我们就不阻塞。

    图仍然进捕获表——脚本结束时的那次屏障里还在。第一个屏障事件因此是
    `script_end` 而不是 `show`。
    """
    proj = tmp_path / "proj"
    write(
        proj / "fig.py",
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "plt.plot([1,2],[3,4])\n"
        "plt.show(block=False)\n"
        "print('KEPT GOING', flush=True)\n",
    )
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        ev = sess.wait_event("barrier")
        assert ev["reason"] == "script_end", "block=False 不该产生 show 屏障"
        build = sess.ensure_built()
        assert list(build["stems"]) == ["fig"]
        sess.resume()
        sess.wait_event("exit")


def test_show_is_not_replaced_by_a_permanent_noop(user_python, tmp_path):
    """`plt.show` 被换掉了，但换成的**不是** no-op：它仍然收 Gcf、仍然区分 block。

    判据：`block=False` 之后图在表里（收了），且脚本没被挂住（没阻塞）。
    把 show 换成 `lambda *a, **k: None` 的话第一条会塌。
    """
    r, data = _run(
        user_python,
        tmp_path,
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1,2],[3,4])\n"
        "plt.show(block=False)\n"
        "import sys; print('ALIVE', file=sys.stderr)\n",
    )
    assert r.returncode == 0, r.stderr
    assert "ALIVE" in r.stderr
    assert [f["stem"] for f in data["figures"]] == ["fig"]
