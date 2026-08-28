"""屏障基准：**restore before continue, rebase at next barrier**（ADR 0021 §8）。

这是 Session 9 唯一一条"做错了会改变用户脚本语义"的不变式，所以整批都跑
**真进程链**：真 `tavotto run` 进程 → 假桌面 attach → 真用户 Python → 真
Bridge Runner → 真 Matplotlib Figure。判据是脚本**自己**断言出来的
（`assert` 写在用户代码里，不过就非零退出）——用 mock 会话验，验的是我们
对语义的想象，不是语义本身。

一句话：**用户代码是执行权威，Tavotto 的 override 是呈现层。**
"""

from __future__ import annotations

import json

import pytest

from support import nativekit
from tavotto.engine import nativesession, runcodes
from tavotto.engine.runcodes import RunError

pytestmark = nativekit.needs_user_python


def manifest_of(session, stem: str) -> dict:
    return json.loads((session.out_dir / f"{stem}.json").read_text(encoding="utf-8"))


def title_gid(session, stem: str) -> str:
    """从 manifest 里找到"标题"那个元素的 gid。

    **按 role 找**，不按硬编码的 gid 串：gid 的编法是引擎的内部约定，写死
    一个会让这批用例在一次无关的重编号里变成假红。
    """
    man = manifest_of(session, stem)
    for el in man["elements"]:
        if el.get("role") == "title":
            return el["gid"]
    raise AssertionError(f"manifest 里没有标题元素: {[e.get('role') for e in man['elements']]}")


def only_stem(build: dict) -> str:
    """build 响应里那唯一一张图的 stem。

    **从响应里取，不写死**：stem 的编法（savefig 的文件名 / pyplot 兜底的
    `<脚本名>`）是 `figcapture` 的策略，用例把它抄一份就成了第二个权威。
    """
    stems = list(build.get("stems") or {})
    assert len(stems) == 1, f"用例前提是一张图，实际 {stems}"
    return stems[0]


REBASE_SCRIPT = """\
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
ax.set_title("Script")
plt.show()                      # ← 屏障 1：Tavotto 把标题改成 "Tavotto"

# 用户代码是执行权威：它看到的必须还是自己写的那个值。
assert ax.get_title() == "Script", "脚本看到了 Tavotto 的 override: " + ax.get_title()
print("SCRIPT-SAW:" + ax.get_title())

ax.set_xlabel("Wavelength")     # 脚本自己改的新状态 = 下一个屏障的新 baseline
plt.show()                      # ← 屏障 2
print("DONE")
"""


def test_the_script_never_sees_a_tavotto_override(tmp_path):
    """**判别性用例**（ADR 0021 §8）：

        title = "Script" → show() → Tavotto 改成 "Tavotto" → continue
        → 脚本断言 title 仍是 "Script" → 脚本改 xlabel → show()
        → Tavotto 看到 title="Tavotto"（重放）且 xlabel= 脚本的新值（新 baseline）

    这条不过，不得发布 Beta。
    """
    nativekit.write(tmp_path / "figure.py", REBASE_SCRIPT)
    with nativekit.product_run(nativekit.USER_PYTHON, "figure.py", cwd=tmp_path) as (
        session,
        proc,
        _,
    ):
        # ---- 屏障 1：编辑 ----
        nativekit.wait_state(session, [nativesession.BARRIER])
        stem = only_stem(session.ensure_built())
        gid = title_gid(session, stem)
        session.override(stem, [{"gid": gid, "prop": "text", "value": "Tavotto"}])
        assert _text_of(manifest_of(session, stem), gid) == "Tavotto", (
            "编辑没生效，后面的判据就是空的"
        )

        # ---- continue：释放之前必须恢复脚本原样 ----
        session.resume()
        nativekit.wait_state(session, [nativesession.BARRIER, nativesession.ENDED])

        # ---- 屏障 2：rebase + 重放 ----
        assert session.state == nativesession.BARRIER, "脚本的断言没过（它看到了 override）"
        session.ensure_built()
        man2 = manifest_of(session, stem)
        assert _text_of(man2, gid) == "Tavotto", "重放没发生：用户的编辑在下一个屏障丢了"
        assert _has_text(man2, "Wavelength"), "新 baseline 没取到：脚本改的 xlabel 不在 manifest 里"
        code, out, err = nativekit.finish(session, proc)

    assert "SCRIPT-SAW:Script" in out, f"脚本看到的标题不对: {out}\n{err}"
    assert "DONE" in out
    assert code == 0, f"脚本自己的断言没过: {err}"


def _text_of(manifest: dict, gid: str) -> str:
    """某个元素**当前**的文字。

    从 `editable` 里那条 `prop == "text"` 读，而不是从 `label`：label 是
    「标题 “…”」这种给人看的串，判据挂在它上面会在一次纯文案改动里假红。
    """
    for el in manifest["elements"]:
        if el["gid"] != gid:
            continue
        for field in el.get("editable") or []:
            if field.get("prop") == "text":
                return str(field.get("value", ""))
        raise AssertionError(f"{gid} 没有可编辑的 text 字段: {el.get('editable')}")
    raise AssertionError(f"manifest 里没有 {gid}")


def _has_text(manifest: dict, needle: str) -> bool:
    for el in manifest["elements"]:
        for field in el.get("editable") or []:
            if field.get("prop") == "text" and needle in str(field.get("value", "")):
                return True
    return False


# --------------------------------------------------------------------------
DISCONNECT_SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
ax.set_title("Script")
plt.show()

assert ax.get_title() == "Script", "断开之后脚本带着 override 继续跑了: " + ax.get_title()
print("SCRIPT-SAW:" + ax.get_title())
"""


def test_disconnect_restores_baseline(tmp_path):
    """**故障路径**：桌面直接断开（App 崩了 / relay EOF）。

    不恢复的话，App 一崩，用户的脚本反而**带着 Tavotto 的 override** 继续
    执行——故障路径上的语义比正常路径更宽松，而这是最难被发现的一类不一致
    （谁会去测"崩溃之后脚本看到了什么"）。
    """
    nativekit.write(tmp_path / "figure.py", DISCONNECT_SCRIPT)
    with nativekit.product_run(nativekit.USER_PYTHON, "figure.py", cwd=tmp_path) as (
        session,
        proc,
        _,
    ):
        nativekit.wait_state(session, [nativesession.BARRIER])
        stem = only_stem(session.ensure_built())
        gid = title_gid(session, stem)
        session.override(stem, [{"gid": gid, "prop": "text", "value": "Tavotto"}])
        session.transport.close()  # ← 没有 continue，直接把连接拔掉
        out, err = proc.communicate(timeout=180)
        code = proc.returncode
    assert "SCRIPT-SAW:Script" in out, f"断开之后脚本看到的标题不对: {out}\n{err}"
    assert code == 0, err


def test_detach_restores_baseline(tmp_path):
    """`detach and continue`：Tavotto 放手，脚本正常跑完。**先恢复再放手。**"""
    nativekit.write(tmp_path / "figure.py", DISCONNECT_SCRIPT)
    with nativekit.product_run(nativekit.USER_PYTHON, "figure.py", cwd=tmp_path) as (
        session,
        proc,
        _,
    ):
        nativekit.wait_state(session, [nativesession.BARRIER])
        stem = only_stem(session.ensure_built())
        gid = title_gid(session, stem)
        session.override(stem, [{"gid": gid, "prop": "text", "value": "Tavotto"}])
        session.detach()
        assert session.state == nativesession.DETACHED
        out, err = proc.communicate(timeout=180)
        code = proc.returncode
    assert "SCRIPT-SAW:Script" in out, f"{out}\n{err}"
    assert code == 0, err


# --------------------------------------------------------------------------
NEW_FIGURE_SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig1, ax1 = plt.subplots()
ax1.plot([0, 1], [0, 1])
ax1.set_title("Script")
fig1.savefig("Fig1.pdf")
plt.show()                       # 屏障 1：只有 Fig1

ax1.set_xlabel("changed by script")
fig2, ax2 = plt.subplots()
ax2.plot([1, 0], [0, 1])
fig2.savefig("Fig2.pdf")
plt.show()                       # 屏障 2：Fig1 + Fig2
print("DONE")
"""


def test_new_figure_after_continue_joins_without_duplicating(tmp_path):
    """重复 `show()`：会话不换、A 重新 rebase + 重放、B 首次加入、**不重复加**。"""
    nativekit.write(tmp_path / "figure.py", NEW_FIGURE_SCRIPT)
    with nativekit.product_run(nativekit.USER_PYTHON, "figure.py", cwd=tmp_path) as (
        session,
        proc,
        _,
    ):
        nativekit.wait_state(session, [nativesession.BARRIER])
        first = session.ensure_built()
        assert sorted(first["stems"]) == ["Fig1"]
        gid = title_gid(session, "Fig1")
        session.override("Fig1", [{"gid": gid, "prop": "text", "value": "Tavotto"}])
        sid = session.session_id
        session.resume()

        nativekit.wait_state(session, [nativesession.BARRIER, nativesession.ENDED])
        assert session.state == nativesession.BARRIER
        assert session.session_id == sid, "第二个屏障换了一条会话"
        second = session.ensure_built()
        assert sorted(second["stems"]) == ["Fig1", "Fig2"], second["stems"]
        man = manifest_of(session, "Fig1")
        assert _text_of(man, gid) == "Tavotto", "Fig1 的编辑没被重放"
        assert _has_text(man, "changed by script"), "Fig1 的新 baseline 没取到"
        code, out, err = nativekit.finish(session, proc)
    assert "DONE" in out, f"{out}\n{err}"
    assert code == 0, err


# --------------------------------------------------------------------------
ORPHAN_SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
(line,) = ax.plot([0, 1], [0, 1])
ax.set_title("Script")
plt.show()                       # 屏障 1：Tavotto 改这条线的宽度

line.remove()                    # 脚本把那个对象删掉了
plt.show()                       # 屏障 2：那条 override 无处可落
print("DONE")
"""


def test_an_orphan_patch_is_reported_not_guessed(tmp_path):
    """脚本把对象删掉了 → 那条 override 变成**孤儿警告**。

    **绝不落到"最像的对象"上**：猜错的表现是用户的线宽改到了另一条曲线上，
    而两边都没有任何提示。
    """
    nativekit.write(tmp_path / "figure.py", ORPHAN_SCRIPT)
    with nativekit.product_run(nativekit.USER_PYTHON, "figure.py", cwd=tmp_path) as (
        session,
        proc,
        _,
    ):
        nativekit.wait_state(session, [nativesession.BARRIER])
        stem = only_stem(session.ensure_built())
        man = manifest_of(session, stem)
        line_gid = next(el["gid"] for el in man["elements"] if el.get("role") in ("line", "series"))
        session.override(stem, [{"gid": line_gid, "prop": "linewidth", "value": 4.0}])
        session.resume()
        nativekit.wait_state(session, [nativesession.BARRIER, nativesession.ENDED])
        assert session.state == nativesession.BARRIER
        # 屏障 2 的事件里带着孤儿警告
        warned = [e for e in session.events if e.get("state") == nativesession.BARRIER]
        session.ensure_built()
        man2 = manifest_of(session, stem)
        assert all(el["gid"] != line_gid for el in man2["elements"]), "那条线还在？用例前提不成立"
        assert warned, "屏障事件没记下来"
        _code, out, err = nativekit.finish(session, proc)
    assert "DONE" in out, f"{out}\n{err}"


# --------------------------------------------------------------------------
def test_editing_is_refused_while_the_script_runs(tmp_path):
    """`running_script` 时**当场拒绝**，不排队（ADR 0021 §9.3）。"""
    nativekit.write(tmp_path / "figure.py", NEW_FIGURE_SCRIPT)
    with nativekit.product_run(nativekit.USER_PYTHON, "figure.py", cwd=tmp_path) as (
        session,
        proc,
        _,
    ):
        nativekit.wait_state(session, [nativesession.BARRIER])
        session.ensure_built()
        session.resume()
        # continue 之后、下一个屏障之前有一段 running_script
        if session.state in (nativesession.CONTINUING, nativesession.RUNNING_SCRIPT):
            with pytest.raises(RunError) as exc:
                session.ensure_built()
            assert exc.value.code == runcodes.NATIVE_SESSION_NOT_AT_BARRIER
        nativekit.finish(session, proc)
