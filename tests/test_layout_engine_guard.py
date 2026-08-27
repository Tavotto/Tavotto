"""持久 tight 布局会吃掉 axes position——能力必须挡掉，且说得出为什么（#140）。

Tavotto 落 `axes.position` override 的方式是 `ax.set_position(v)`。图上挂着**持久
的** `TightLayoutEngine` 时，它会在紧随其后的那次绘制里把位置整个算回去，于是
出现一个 silent wrong：**文档里记着 override，画面上什么都没发生**。用户拖子图、
多选对齐、改 mm 宽高、成组缩放都会撞上——点了、历史里有了、撤销栈里有了，图纹丝
不动；写回还会把一条永远不生效的改动烙进 `baked_overrides`。

这与 `position_locked`（子 axes 的落位由父级 locator 每帧重算）是同一形状，处理
方式也照抄那条既有纪律：**宁可不支持，也不给一个按了会弹回来的旋钮**——但这一次
要说得出为什么。

本进程不 import matplotlib：判据跑在 worker 的科学栈解释器里。
"""

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
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import manifest
import overrides

def make(**kw):
    call = kw.pop("_call_tight", False)
    fig, ax = plt.subplots(figsize=(4, 3), **kw)
    ax.plot([0, 1], [0, 1])
    if call:
        fig.tight_layout()
    fig.canvas.draw()
    return fig, ax

# ── 1) 探测器：只有**持久** tight 引擎算数 ─────────────────────────────────
EATS = [("layout='tight'", {"layout": "tight"}),
        ("tight_layout=True", {"tight_layout": True})]
KEEPS = [("默认", {}),
         ("fig.tight_layout() 调一次", {"_call_tight": True}),
         ("layout='constrained'", {"layout": "constrained"}),
         ("constrained_layout=True", {"constrained_layout": True}),
         ("layout='compressed'", {"layout": "compressed"})]

for how, kw in EATS:
    fig, ax = make(**dict(kw))
    assert manifest.figure_layout_engine_eats_position(fig), how
    # 现场复核：这不是靠类名猜的，是真的会被吃掉
    ax.set_position([0.30, 0.30, 0.40, 0.40]); fig.canvas.draw()
    got = tuple(round(float(v), 4) for v in ax.get_position().bounds)
    assert got != (0.3, 0.3, 0.4, 0.4), f"{how}: 判据说会被吃掉，实际没有"
    plt.close(fig)

for how, kw in KEEPS:
    fig, ax = make(**dict(kw))
    assert not manifest.figure_layout_engine_eats_position(fig), how
    ax.set_position([0.30, 0.30, 0.40, 0.40]); fig.canvas.draw()
    got = tuple(round(float(v), 4) for v in ax.get_position().bounds)
    assert got == (0.3, 0.3, 0.4, 0.4), f"{how}: 判据说保得住，实际被吃掉了"
    plt.close(fig)

# ── 2) 否定结论留档：set_in_layout(False) 救不回来 ─────────────────────────
# 这是 issue 里建议的「方案 3」，实测在 3.9 / 3.10 / 3.11 上都无效。上游哪天
# 改了行为，这条会红——那正是重新评估的时机，而不是让一条错的备选一直挂着。
fig, ax = make(layout="tight")
ax.set_in_layout(False)
ax.set_position([0.30, 0.30, 0.40, 0.40]); fig.canvas.draw()
got = tuple(round(float(v), 4) for v in ax.get_position().bounds)
assert got != (0.3, 0.3, 0.4, 0.4), (
    "set_in_layout(False) 现在挡得住 TightLayoutEngine 了——#140 的方案 3 值得重估")
plt.close(fig)

# ── 3) manifest：能力挡掉，且带上理由 ─────────────────────────────────────
def axes_entry(**kw):
    fig, ax = make(**dict(kw))
    st = overrides.FigState(fig)
    manifest.instrument(st)
    m = manifest.build_manifest(st, "T")
    plt.close(fig)
    return next(e for e in m["elements"] if e["gid"] == "axes_0")

hit = axes_entry(layout="tight")
assert hit["resizable"] is False, "持久 tight 下还宣称 resizable：画布上拖了不会动"
assert not any(f["prop"] == "position" for f in hit["editable"]), \\
    "position 字段还在：那是一个按了会被算回去的旋钮"
reasons = {u["prop"]: u["reason"] for u in hit.get("unsupported_props", [])}
assert reasons.get("position") == "layout_engine_tight", reasons

ok = axes_entry()
assert ok["resizable"] is True
assert any(f["prop"] == "position" for f in ok["editable"]), \\
    "对照组的 position 一起丢了——把一个真能力藏起来比不支持更糟"
assert "unsupported_props" not in ok, ok.get("unsupported_props")

con = axes_entry(layout="constrained")
assert con["resizable"] is True, "constrained 不受影响（实测），不该被一起挡掉"
assert "unsupported_props" not in con

# ── 4) 逐轴，不是图级一刀切 ───────────────────────────────────────────────
# 同一张 tight 图上 `fig.add_axes()` 建的轴没有 SubplotSpec，tight_layout 根本
# 不算它——位置是真能改的。图级一刀切会把这个真能力藏起来（比不支持更糟）。
fig, ax = make(layout="tight")
cax = fig.add_axes([0.85, 0.1, 0.03, 0.8])
fig.canvas.draw()
assert manifest.axes_position_eaten_by_layout(fig, ax), "有 SubplotSpec 的子图该被判为「会被吃掉」"
assert not manifest.axes_position_eaten_by_layout(fig, cax), \
    "add_axes 建的轴不参与 tight 计算，位置保得住——不许一起挡掉"
cax.set_position([0.30, 0.30, 0.40, 0.40]); fig.canvas.draw()
got = tuple(round(float(v), 4) for v in cax.get_position().bounds)
assert got == (0.3, 0.3, 0.4, 0.4), f"判据说保得住，实际被吃掉了：{got}"
plt.close(fig)

st = overrides.FigState(fig := plt.figure(figsize=(4, 3)))
plt.close(fig)

fig, ax = make(layout="tight")
cax = fig.add_axes([0.85, 0.1, 0.03, 0.8])
fig.canvas.draw()
st = overrides.FigState(fig)
manifest.instrument(st)
m = manifest.build_manifest(st, "T")
by_gid = {e["gid"]: e for e in m["elements"]}
sub = by_gid["axes_0"]
free = next(e for g, e in by_gid.items() if g != "axes_0" and e["role"] in ("axes", "axes3d"))
assert sub["resizable"] is False and free["resizable"] is True, (sub["resizable"], free["resizable"])
assert any(f["prop"] == "position" for f in free["editable"]), \
    "add_axes 的轴丢了 position 字段——真能力被一起藏起来了"
assert "unsupported_props" not in free
plt.close(fig)

# ── 5) 第二个消费点：setter 必须拦住旧文档 / 直接调 API 发来的 override ──
fig, ax = make(layout="tight")
_, set_pos = overrides.HANDLERS[("axes", "position")]
try:
    set_pos(ax, [0.30, 0.30, 0.40, 0.40])
except ValueError as exc:
    assert "layout_engine_tight" in str(exc), exc
else:
    raise AssertionError("setter 放行了一条永远不生效的 position override——"
                         "manifest 不宣称挡不住旧文档，只修一处等于没修")
# 同一张图上 add_axes 的轴照旧放行
cax = fig.add_axes([0.85, 0.1, 0.03, 0.8])
set_pos(cax, [0.30, 0.30, 0.40, 0.40])
# 撤销那条路不走 guard：还原脚本原样在 tight 图上必须不抛
overrides._RESTORE[("axes", "position")](ax, [0.1, 0.1, 0.8, 0.8])
plt.close(fig)

# 对照组：默认建法的 setter 一条都不许挡
fig, ax = make()
set_pos(ax, [0.30, 0.30, 0.40, 0.40])
fig.canvas.draw()
assert tuple(round(float(v), 4) for v in ax.get_position().bounds) == (0.3, 0.3, 0.4, 0.4)
plt.close(fig)

print("OK")
"""


def test_a_persistent_tight_layout_hides_position_and_says_why():
    """一次跑三件事：探测器判得准、否定结论仍成立、manifest 挡掉能力并给出理由。

    合成一个 driver 是因为它们共用同一套建图与实测，且每条断言各自失败时的
    消息足够指名道姓——拆成三个 subprocess 只会让同一份 matplotlib 起三遍。
    """
    out = subprocess.run(
        [WORKER_PY, "-c", _DRIVER, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("OK")
