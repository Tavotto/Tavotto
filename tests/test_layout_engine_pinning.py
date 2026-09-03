"""持久 tight 布局下，用户摆过的子图位置必须钉得住（#162，#140 的真修）。

`plt.subplots(layout="tight")` / `tight_layout=True` 会在图上挂一个**持久的**
`TightLayoutEngine`，它在每次绘制里把所有有 SubplotSpec 的子图位置整个算回去。
Tavotto 落 `axes.position` 的方式是 `ax.set_position(v)`，于是 #140 之前是一个
silent wrong（文档里记着 override、画面上什么都没发生），#140 之后是「不宣称这
条能力」——用这种写法建图的用户从此拖不动子图、不能多选对齐、不能改 mm 宽高、
不能成组缩放。

#162 的修法是 `overrides.PinnedTightLayoutEngine`：`instrument()` 无条件把持久
tight 引擎换成它，被 override 过的轴钉住、其余照旧由 tight 自动排版。

本文件量的是这几件事（每一条都在 3.9.4 / 3.10.8 / 3.11.1 上跑过，结论一致）：

1. **探测器判得准** —— 哪些建图方式真的会吃掉 `set_position`；
2. **换引擎是零影响的** —— pin 表为空时像素与位置与原生 tight 逐字节相同；
3. **钉得住** —— 被 pin 的轴每一次 draw 都精确落在请求的位置上；
4. **只动我拖的那个** —— 没被 pin 的轴与「一条 override 都没有」时逐位相同；
5. **热态 == 重放** —— 逐步 pin 与一次性 pin 收敛到同一张图（这条是写回事务
   不变式的直接体现，也是「先算 tight、后盖回去」那种写法的照妖镜）；
6. **撤销回得去** —— unpin 之后逐位回到从没被 override 过的位置；
7. manifest 与 setter 两个消费点都通了，`layout_engine_tight` 那条 reason 没了。

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
import io
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.layout_engine import TightLayoutEngine

import manifest
import overrides

PIN = [0.12, 0.55, 0.30, 0.35]


def rounded(v):
    return tuple(round(float(x), 6) for x in v)


def box(ax):
    return rounded(ax.get_position().bounds)


def make(n=1, **kw):
    call = kw.pop("_call_tight", False)
    fig, axs = plt.subplots(1, n, figsize=(6, 3), squeeze=False, **kw)
    axs = list(axs[0])
    for i, ax in enumerate(axs):
        ax.plot([0, 1], [0, i + 1])
        ax.set_xlabel("x label")
        ax.set_ylabel("y label")
    if call:
        fig.tight_layout()
    fig.canvas.draw()
    return fig, (axs[0] if n == 1 else axs)


def png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=72)
    return buf.getvalue()


# -- 1) 探测器：只有**持久** tight 引擎算数 --------------------------------
EATS = [("layout='tight'", {"layout": "tight"}),
        ("tight_layout=True", {"tight_layout": True})]
KEEPS = [("默认", {}),
         ("fig.tight_layout() 调一次", {"_call_tight": True}),
         ("layout='constrained'", {"layout": "constrained"}),
         ("constrained_layout=True", {"constrained_layout": True}),
         ("layout='compressed'", {"layout": "compressed"})]

for how, kw in EATS:
    fig, ax = make(**dict(kw))
    assert overrides.figure_layout_engine_eats_position(fig), how
    # 现场复核：这不是靠类名猜的，是真的会被吃掉。**这条断言也是
    # PinnedTightLayoutEngine 存在的全部理由**——它哪天不红了，说明上游改了
    # 行为，那时该重估的是「还需不需要这个引擎」。
    ax.set_position(PIN); fig.canvas.draw()
    assert box(ax) != rounded(PIN), f"{how}: 判据说会被吃掉，实际没有"
    plt.close(fig)

for how, kw in KEEPS:
    fig, ax = make(**dict(kw))
    assert not overrides.figure_layout_engine_eats_position(fig), how
    ax.set_position(PIN); fig.canvas.draw()
    assert box(ax) == rounded(PIN), f"{how}: 判据说保得住，实际被吃掉了"
    plt.close(fig)

# 否定结论留档：`set_in_layout(False)` 救不回来（#140 里建议过的那条捷径），
# 三个版本上都无效。上游哪天改了行为这条会红——那正是重新评估的时机。
fig, ax = make(layout="tight")
ax.set_in_layout(False)
ax.set_position(PIN); fig.canvas.draw()
assert box(ax) != rounded(PIN), (
    "set_in_layout(False) 现在挡得住 TightLayoutEngine 了——那条更便宜的路值得重估")
plt.close(fig)

# -- 2) 换引擎本身是零影响的 -----------------------------------------------
# `instrument()` 对**每一张**持久 tight 图都换引擎，包括用户从没编辑过的。
# 所以「pin 表为空时与原生 tight 逐字节相同」是这个无条件换法的前提。
fig, axs = make(2, layout="tight")
base_png, base_pos = png(fig), [box(a) for a in axs]
plt.close(fig)

fig, axs = make(2, layout="tight")
engine = overrides.ensure_pinnable_layout_engine(fig)
assert isinstance(engine, overrides.PinnedTightLayoutEngine)
assert isinstance(engine, TightLayoutEngine), "子类身份没了，colorbar 与 subplots_adjust 会改行为"
assert engine.adjust_compatible is True and engine.colorbar_gridspec is True
assert png(fig) == base_png, "空 pin 表下换了引擎，像素就变了——那不是零影响"
assert [box(a) for a in axs] == base_pos
# 幂等：再调一次不许把引擎（连同 pin 表）换掉
engine.pin(axs[0], PIN)
assert overrides.ensure_pinnable_layout_engine(fig) is engine, \
    "第二次调用又换了一个新引擎——用户摆过的每一个位置会连同 pin 表一起丢掉"
assert engine.is_pinned(axs[0])
plt.close(fig)

# -- 2b) 这里为什么**不能**拿像素当尺子 ------------------------------------
# 一张零 override 的原生 tight 图，连画 14 次会出现**4 种不同的画面**（三版
# 实测一致；位置早就收敛了，飘的是 ylabel 的落点——`_update_label_position`
# 拿上一帧的刻度包围盒算这一帧的偏移，与 tight 互相追着跑）。同一张图没有
# 引擎时 14 次全同一帧。所以「画同样多次才能比像素」在这类图上根本不成立，
# 本文件的判据一律用**位置**，像素只用在下面那种两侧 draw 次数与顺序完全对齐
# 的地方（第 2 节）。
#
# 这条断言是给后来人的路标：它哪天红了，说明上游把这个循环修好了，那时才谈
# 得上给 tight 图加像素判据。
fig, _axs = make(2, layout="tight")
frames = set()
for _ in range(14):
    fig.canvas.draw()
    frames.add(png(fig))
plt.close(fig)
assert len(frames) > 1, (
    "原生 tight 图现在逐帧稳定了——上游修好了 tight ↔ label 的互相追逐，"
    "本文件（以及写回的像素门）可以重新考虑用像素当判据")

# -- 3) 钉得住 + 4) 只动我拖的那个 + 5) 热态 == 重放 ------------------------
# 三条一起量：同一张图、同一组 pin，两条腿只差「什么时候 pin」。
def run(pin_before_draw, draws=6):
    fig, axs = make(3, layout="tight")
    engine = overrides.ensure_pinnable_layout_engine(fig)
    if not pin_before_draw:
        fig.canvas.draw(); fig.canvas.draw()
    engine.pin(axs[0], PIN)
    seq = []
    for _ in range(draws):
        fig.canvas.draw()
        seq.append([box(a) for a in axs])
    plt.close(fig)
    return seq

# 零 override 的基准：同一张图画同样多次
fig, axs = make(3, layout="tight")
overrides.ensure_pinnable_layout_engine(fig)
native = []
for _ in range(6):
    fig.canvas.draw()
    native.append([box(a) for a in axs])
plt.close(fig)

replay_seq = run(True)
hot_seq = run(False)

assert all(row[0] == rounded(PIN) for row in replay_seq + hot_seq), (
    "被 pin 的轴没有精确落在请求的位置上：", replay_seq[0][0], hot_seq[0][0])
assert [row[1:] for row in replay_seq] == [row[1:] for row in native], (
    "没被 pin 的轴跟着动了——「我拖了 A，B 不该跳」")
assert replay_seq[-1] == hot_seq[-1], (
    "逐步 pin 与一次性 pin 收敛到不同的图：热态所见 != 重开后重放出来的")

# -- 6) 撤销逐位回得去 -----------------------------------------------------
# 两条腿画**同样多次**：一条从头到尾没 override 过，另一条改了又撤。tight 自己
# 要三四次 draw 才收敛，比较必须在两侧都收敛之后做——7 次起两侧逐位相同（三版
# 实测），这里取 10 次留足余量。
get_pos, set_pos = overrides.HANDLERS[("axes", "position")]

def restore_leg(with_override, draws=10, set_at=1, restore_at=3):
    fig, axs = make(2, layout="tight")
    overrides.ensure_pinnable_layout_engine(fig)
    orig = None
    for k in range(draws):
        if with_override and k == set_at:
            orig = get_pos(axs[0])
            set_pos(axs[0], PIN)
        if with_override and k == restore_at:
            assert box(axs[0]) == rounded(PIN), "setter 这条路没把位置钉住"
            overrides._RESTORE[("axes", "position")](axs[0], orig)
        fig.canvas.draw()
    out = [box(a) for a in axs]
    plt.close(fig)
    return out

never, restored = restore_leg(False), restore_leg(True)
assert restored == never, (
    "撤销之后没有逐位回到「从没 override 过」的位置——unpin 漏了的话它会被钉在"
    "「脚本原样」那个数上，再也不跟着字号 / 标签变化重排：", restored, never)

# -- 7) 两个消费点都通，且没走 instrument 的来路也钉得住 --------------------
# setter 是第二个消费点：旧文档 / 直接调 API 的 override 可能落在一个没走过
# `instrument()` 的 FigState 上，那时引擎还是原生 tight。
fig, ax = make(layout="tight")
assert overrides.pinnable_layout_engine(fig) is None, "这张图还没被接管"
set_pos(ax, PIN)
fig.canvas.draw()
assert box(ax) == rounded(PIN), "setter 自己没把引擎换上——旧文档的 override 照旧被吃掉"
plt.close(fig)


def axes_entry(**kw):
    fig, ax = make(**dict(kw))
    st = overrides.FigState(fig)
    manifest.instrument(st)
    m = manifest.build_manifest(st, "T")
    entry = next(e for e in m["elements"] if e["gid"] == "axes_0")
    plt.close(fig)
    return entry

for how, kw in EATS + [("默认", {})]:
    hit = axes_entry(**dict(kw))
    assert hit["resizable"] is True, f"{how}: 还在置灰——画布上拖不动"
    assert any(f["prop"] == "position" for f in hit["editable"]), \
        f"{how}: position 字段不出，mm 宽高与成组缩放都用不了"
    assert "unsupported_props" not in hit, (how, hit.get("unsupported_props"))

# manifest 走完整条路：instrument 之后位置真的改得动，撤销之后真的还回去
fig, ax = make(layout="tight")
st = overrides.FigState(fig)
manifest.instrument(st)
warns = overrides.apply(st, [{"gid": "axes_0", "prop": "position", "value": PIN}])
assert not warns, warns
fig.canvas.draw()
assert box(ax) == rounded(PIN), "走 apply 这条正门落不下去"
warns = overrides.apply(st, [])
fig.canvas.draw()
assert box(ax) != rounded(PIN), "撤销之后还钉在那儿"
assert not warns, warns
plt.close(fig)

# -- 8) 逐轴：add_axes 的轴（本来就没被吃）也照样钉得住 ---------------------
fig, ax = make(layout="tight")
cax = fig.add_axes([0.85, 0.1, 0.03, 0.8])
fig.canvas.draw()
engine = overrides.ensure_pinnable_layout_engine(fig)
set_pos(cax, [0.60, 0.20, 0.05, 0.50])
fig.canvas.draw()
assert box(cax) == (0.6, 0.2, 0.05, 0.5), box(cax)
# 被 pin 的轴 remove 掉之后 execute 不许炸
cax.remove()
fig.canvas.draw()
plt.close(fig)

print("OK")
"""


def test_a_persistent_tight_layout_no_longer_eats_edited_positions():
    """一次跑八件事：探测器、零影响换引擎、钉得住、只动被拖的那个、热态 ==
    重放、撤销逐位回去、两个消费点、逐轴。

    合成一个 driver 是因为它们共用同一套建图与实测，且每条断言各自失败时的
    消息足够指名道姓——拆成八个 subprocess 只会让同一份 matplotlib 起八遍。
    """
    out = subprocess.run(
        [WORKER_PY, "-c", _DRIVER, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip().endswith("OK")
