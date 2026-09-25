"""还原失败不遗忘（Codex #549 第八轮 P1）。

`overrides.apply()` 撤掉一条 override 时要把它还原成脚本原样；还原抛了，Figure 停在半改
状态。旧实现只报一条 warning，随后**无条件**把这个键从 applied / originals / alias_seeded
里删掉——之后每一次全量重放都不再碰它，图永久与文档不一致，而且再也没有 warning（写回
遇 warning 才阻断，于是阻断也没了）。safe worker 可以作废重起，native 会话（用户自己的
Python，ADR 0021）不能，只能靠引擎自己把账留着。

判据：

1. 还原失败的键留账，下一次 apply（同一份全量列表）**重试**还原，失败期间每次都报
   warning；还原恢复可用后清账、warning 消失、值回到脚本原样；
2. 欠账期间同一个键重新回到列表里：按请求值重新落下（不走「值没变就跳过」），原样仍是
   脚本原样，之后再撤能回到原样；
3. 别名组：广播端还原失败时，组员的代采原样不被回收，重试成功后组员也回到原样；
4. 不变量：欠账期间经过的任何序列，还原成功之后热态与冷启动全量重放**像素与 manifest
   逐字节相同**；
5. `snapshot()` 不把欠账的键算作已应用（状态中立的预览收尾、屏障离开保存的列表都读它）。

本进程不 import matplotlib：判据跑在 worker 的科学栈解释器里（与
`test_layout_engine_pinning.py` 同一种写法）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

ENGINE_DIR = Path(__file__).resolve().parent.parent / "src" / "tavotto" / "engine"

_DRIVER = """\
import hashlib
import io
import json
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import manifest
import overrides


def make():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], linewidth=1.5, label="a")
    ax.plot([0, 1], [1, 0], linewidth=1.5, label="b")
    ax.legend(fontsize=9)
    st = overrides.FigState(fig)
    manifest.instrument(st)
    return st


def p(gid, prop, value):
    return {"gid": gid, "prop": prop, "value": value}


class Broken:
    \"\"\"把一条 prop 的还原换成必抛的（`apply` 的还原循环先查 `_RESTORE`）。\"\"\"

    def __init__(self, ck, prop):
        self.key = (ck, prop)
        self.prev = overrides._RESTORE.get(self.key, None)
        self.had = self.key in overrides._RESTORE

    def __enter__(self):
        def boom(*_a, **_k):
            raise RuntimeError("还原坏了")
        overrides._RESTORE[self.key] = boom
        return self

    def __exit__(self, *_exc):
        if self.had:
            overrides._RESTORE[self.key] = self.prev
        else:
            overrides._RESTORE.pop(self.key, None)


def fingerprint(st):
    buf = io.BytesIO()
    st.fig.savefig(buf, format="png", dpi=72)
    man = manifest.build_manifest(st, "Fig")
    return hashlib.sha256(buf.getvalue()).hexdigest(), json.dumps(man, sort_keys=True, default=str)


def owed(st):
    # 旧实现没有这个属性：反证时判据落在真正的行为上，不落在 AttributeError 上
    return getattr(st, "unrestored", set())


def restored(warnings):
    return [w for w in warnings if w.startswith("还原失败")]
"""


def _run(body: str) -> str:
    proc = subprocess.run(
        [WORKER_PY, "-c", _DRIVER + body, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def test_failed_restore_is_retried_until_it_succeeds():
    _run(
        """
st = make()
line = st.resolve("axes_0.lines_0")
LW = ("axes_0.lines_0", "linewidth")
assert not restored(overrides.apply(st, [p(*LW, 4.0)]))
assert line.get_linewidth() == 4.0
with Broken("line", "linewidth"):
    w1 = overrides.apply(st, [])
    # 同一份全量列表再来一次：旧实现这里已经销账，一声不吭
    w2 = overrides.apply(st, [])
    assert restored(w1) and restored(w2), (w1, w2)
    assert LW in st.applied and LW in st.originals and LW in owed(st)
    assert overrides.snapshot(st) == []
    assert line.get_linewidth() == 4.0
w3 = overrides.apply(st, [])
assert not restored(w3), w3
assert line.get_linewidth() == 1.5
assert LW not in st.applied and LW not in st.originals and not owed(st)
"""
    )


def test_reapplying_an_unrestored_key_keeps_the_script_original():
    _run(
        """
st = make()
line = st.resolve("axes_0.lines_0")
LW = ("axes_0.lines_0", "linewidth")
overrides.apply(st, [p(*LW, 4.0)])
with Broken("line", "linewidth"):
    assert restored(overrides.apply(st, []))
# 用户又点回来：必须真的落下（不是「值没变就跳过」），原样仍是脚本的 1.5
line.set_linewidth(2.7)  # 半改状态
assert not restored(overrides.apply(st, [p(*LW, 4.0)]))
assert line.get_linewidth() == 4.0 and not owed(st)
assert st.originals[LW] == 1.5
assert not restored(overrides.apply(st, []))
assert line.get_linewidth() == 1.5
"""
    )


def test_alias_group_members_keep_their_originals_while_the_broadcast_is_owed():
    _run(
        """
st = make()
leg = st.resolve("axes_0.legend")
t0, t1 = leg.get_texts()
FS = ("axes_0.legend", "fontsize")
N0 = ("axes_0.legend.texts_0", "fontsize")
# 广播端 + 一条窄端：广播代采了 texts_1 的原样
overrides.apply(st, [p(*FS, 14.0), p(*N0, 20.0)])
assert t0.get_fontsize() == 20.0 and t1.get_fontsize() == 14.0
with Broken("legend", "fontsize"):
    assert restored(overrides.apply(st, [p(*N0, 20.0)]))
    assert restored(overrides.apply(st, [p(*N0, 20.0)]))
    # 组员的代采原样还在（旧实现在这里就被回收了）
    assert ("axes_0.legend.texts_1", "fontsize") in st.originals
assert not restored(overrides.apply(st, [p(*N0, 20.0)]))
leg = st.resolve("axes_0.legend")
t0, t1 = leg.get_texts()
assert t1.get_fontsize() == 9.0, t1.get_fontsize()
assert t0.get_fontsize() == 20.0, t0.get_fontsize()
assert not owed(st)
"""
    )


@pytest.mark.parametrize(
    "case",
    # 每组：初始列表 → 坏掉一条还原 → 欠账期间经过的几份列表 → 修好之后的最终列表
    ["line", "legend"],
)
def test_hot_state_converges_to_a_fresh_replay_once_restore_succeeds(case):
    _run(
        f"""
CASE = {case!r}
if CASE == "line":
    first = [p("axes_0.lines_0", "linewidth", 4.0), p("axes_0.lines_1", "color", "#cc0000")]
    broken = ("line", "linewidth")
    during = [[p("axes_0.lines_1", "color", "#cc0000")], [], [p("axes_0.lines_0", "linewidth", 3.0)], []]
    final = [p("axes_0.lines_1", "color", "#00aa00")]
else:
    first = [p("axes_0.legend", "fontsize", 14.0), p("axes_0.legend.texts_0", "fontsize", 20.0)]
    broken = ("legend", "fontsize")
    during = [[p("axes_0.legend.texts_0", "fontsize", 20.0)], [p("axes_0.legend.texts_0", "fontsize", 20.0)]]
    final = [p("axes_0.legend.texts_0", "fontsize", 20.0)]

hot = make()
overrides.apply(hot, first)
with Broken(*broken):
    for lst in during:
        overrides.apply(hot, lst)
w = overrides.apply(hot, final)
cold = make()
assert not restored(overrides.apply(cold, final))
a, b = fingerprint(hot), fingerprint(cold)
assert a[1] == b[1], "manifest 分岔"
assert a[0] == b[0], "像素分岔"
assert not restored(w), w
assert not owed(hot)
"""
    )


def test_v1_render_reports_the_owed_count_and_legacy_stays_untouched(tmp_path):
    """`unrestored` 是 v1 render 结果里的结构化字段（native 会话据此标「与文档不一致」），
    legacy 扁平信封一字不动（`test_worker_roundtrip.py::test_legacy_envelope_keeps_the_old_response_shape`）。"""
    (tmp_path / "fig_owed.py").write_text(
        "import matplotlib.pyplot as plt\n\n\ndef main():\n"
        "    fig, ax = plt.subplots()\n    ax.plot([0, 1], [0, 1])\n    fig.savefig('Owed.png')\n",
        encoding="utf-8",
    )
    w = pool.one_shot("fig_owed.py", str(tmp_path), "main")
    try:
        w.ensure_built()
        resp = w.override("Owed", [{"gid": "axes_0.lines_0", "prop": "linewidth", "value": 3.0}])
        assert resp["unrestored"] == 0
        assert w.override("Owed", [])["unrestored"] == 0
    finally:
        w.shutdown()


def test_v1_export_and_preview_report_the_owed_count_after_restoring(tmp_path):
    """export / preview_png 临时套用一份列表再还原：还原之后欠几条也要报（native 会话据此
    记「与文档不一致」，Codex #549 第九轮 P1 r4105547311）。"""
    (tmp_path / "fig_owed2.py").write_text(
        "import matplotlib.pyplot as plt\n\n\ndef main():\n"
        "    fig, ax = plt.subplots()\n    ax.plot([0, 1], [0, 1])\n    fig.savefig('Owed2.png')\n",
        encoding="utf-8",
    )
    w = pool.one_shot("fig_owed2.py", str(tmp_path), "main")
    try:
        w.ensure_built()
        lw = [{"gid": "axes_0.lines_0", "prop": "linewidth", "value": 3.0}]
        resp = w.export("Owed2", lw, str(tmp_path / "o.pdf"))
        assert resp["unrestored"] == 0
        resp = w.request({"cmd": "preview_png", "stem": "Owed2", "patches": lw, "width": 200})
        assert resp["unrestored"] == 0
    finally:
        w.shutdown()
