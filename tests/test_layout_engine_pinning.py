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

# -- 9) 不可逆的那一步不许排在可能失败的那一步之前 --------------------------
# `set_position` 对长度不是 4 的 bounds 抛 TypeError。setter 里如果先 pin 再
# set_position，异常抛出时 pin 已经落下——而 `apply` 只把它收成一条 warning、
# **不记进 `state.applied`**，于是还原那条路永远不会跑、永远不会 unpin。
#
# 判据的主语是**引擎上有没有残留的 pin**，不是「抛没抛」：抛异常本来就该抛，
# 那一半在下面只是前置条件。
fig, ax = make(layout="tight")
try:
    set_pos(ax, [0.1, 0.1, 0.5])
except TypeError:
    pass
else:
    raise AssertionError("长度 3 的 bounds 被放行了——前置条件都不成立，下面那条判据没意义")
leftover = overrides.pinnable_layout_engine(fig)
assert leftover is None or not leftover.is_pinned(ax), (
    "setter 抛了异常，pin 却已经落下：这条 override 进不了 state.applied，"
    "撤销那条路永远不会 unpin 它")
# 前置条件的另一半：这张图还得画得出来
fig.canvas.draw()
plt.close(fig)

# 后果留档：万一坏 pin 真的留下了会怎样。`Figure.draw` **只吞 ValueError**，
# 而 `Bbox.from_bounds()` 抛的是 TypeError——它一路冒出去，这张图从此画不出来
# （不是「静默重试」）。三版实测一致。这条断言盯着上游那个 except 子句：
# 它哪天改成吞 Exception，第 9 节的理由要重写（那时的后果会变成静默重试）。
fig, ax = make(layout="tight")
engine = overrides.ensure_pinnable_layout_engine(fig)
engine._pinned[ax] = (0.1, 0.1, 0.5)
try:
    fig.canvas.draw()
except TypeError:
    pass
else:
    raise AssertionError(
        "坏 pin 留在引擎里，绘制却没抛——上游改了吞异常的范围，第 9 节的理由要重写")
plt.close(fig)

# -- 10) 用户自己的 TightLayoutEngine 子类：接管它，不是替换它 --------------
# `isinstance` 判据同样会选中用户脚本挂上来的子类。用 `PinnedTightLayoutEngine(
# **inner.get())` 重建的话，它重写过的 `execute()` 与全部子类状态会被**静默丢掉**
# （每一个没被 pin 的轴的落位跟着变），而 `get()` 多回一个键时重建会当场 TypeError、
# 这条编辑直接失败。包住原件再委派，两种都不发生。
class UserTight(TightLayoutEngine):
    # 一个会留下可观测痕迹、且 get() 多一个键的子类。
    # （驱动本身就是个三引号字符串，这里只能用 # 注释，不能写 docstring。）

    def __init__(self, **kw):
        super().__init__(**kw)
        self.ran = 0
        self.flavour = "用户自己的"

    def execute(self, fig):
        self.ran += 1
        super().execute(fig)

    def get(self):
        # 子类往参数表里多塞一个键：重建那种写法会在这里 TypeError
        return {**super().get(), "flavour": self.flavour}


fig, axs = make(2, layout="tight")
mine = UserTight(pad=2.0)
fig.set_layout_engine(mine)
fig.canvas.draw()
ran_before = mine.ran
assert ran_before > 0, "夹具自己没跑起来——下面那条判据没意义"

engine = overrides.ensure_pinnable_layout_engine(fig)
assert engine is not None, "自定义子类没被接管——position 会被静默吃掉（#140 的形状）"
assert engine.get() == mine.get(), ("参数表没跟着原件走：", engine.get(), mine.get())
set_pos(axs[0], PIN)
fig.canvas.draw()
assert box(axs[0]) == rounded(PIN), "接管之后钉不住"
assert mine.ran > ran_before, (
    "用户重写的 execute() 没再跑过——它被替换掉了，这张图的排版语义已经不是脚本写的那个")
plt.close(fig)

# -- 11) 寄生轴（#217/#258）与持久 tight（#162）相遇时谁说了算 --------------
# 两条规则管的是**不同的轴**，而且顺序天然是对的：布局引擎在 `Figure.draw` 的最
# 前面跑（钉住宿主），宿主的 `draw()` 随后把自己的 rect 推给寄生轴（寄生跟着走）。
# 所以「在 tight 图上拖 host_subplot」= 宿主到位、右轴跟着；而寄生轴**自己的**
# position 照旧是死开关，manifest 照旧不宣称它。
from mpl_toolkits.axes_grid1 import host_subplot

PAR_PIN = [0.20, 0.25, 0.40, 0.45]


def par_fig():
    fig = plt.figure(figsize=(5, 3), layout="tight")
    h = host_subplot(111, figure=fig)
    pa = h.twinx()
    h.plot([0, 1], [0, 1]); pa.plot([0, 1], [0, 60])
    h.set_ylabel("host y"); pa.set_ylabel("par y")
    st = overrides.FigState(fig)
    manifest.instrument(st)
    return fig, h, pa, st


def axes_boxes(st):
    m = manifest.build_manifest(st, "T")
    return {e["gid"]: [round(v, 5) for v in e["bbox"]]
            for e in m["elements"]
            if e["gid"].startswith("axes_") and e["gid"][5:].isdigit()}


fig, h, pa, st = par_fig()
fig.canvas.draw()
assert overrides.figure_layout_engine_eats_position(fig), "夹具没挂上持久 tight，下面白测"
m = manifest.build_manifest(st, "T")
by = {e["gid"]: e for e in m["elements"]}
assert by["axes_0"]["resizable"] is True, "宿主该能拖"
assert by["axes_1"]["resizable"] is False, "寄生轴不该能拖（#258 实测它是死开关）"
reasons = {u["prop"]: u["reason"] for u in by["axes_1"].get("unsupported_props", [])}
assert reasons.get("position") == "parasite_host_rect", reasons
before = axes_boxes(st)
assert before["axes_0"] == before["axes_1"], ("寄生轴本来就该与宿主同框：", before)

warns = overrides.apply(st, [{"gid": "axes_0", "prop": "position", "value": PAR_PIN}])
assert not warns, warns
fig.canvas.draw()
assert box(h) == rounded(PAR_PIN), ("tight 图上的 host_subplot 没钉住：", box(h))
after = axes_boxes(st)
assert after["axes_0"] != before["axes_0"], "宿主没动"
assert after["axes_1"] == after["axes_0"], ("拖了宿主，右轴没跟着走：", after)
plt.close(fig)


def par_leg(with_override, draws=10):
    fig, _h, _pa, st = par_fig()
    for k in range(draws):
        if with_override and k == 1:
            overrides.apply(st, [{"gid": "axes_0", "prop": "position", "value": PAR_PIN}])
        if with_override and k == 3:
            overrides.apply(st, [])
        fig.canvas.draw()
    out = axes_boxes(st)
    plt.close(fig)
    return out


assert par_leg(True) == par_leg(False), (
    "host_subplot 图上改了又撤销，没有逐位回到从没 override 过的样子：",
    par_leg(True), par_leg(False))

# -- 12) 与刻度记忆表（#220 / #273）的时序 -----------------------------------
# `build_manifest` 开着 `overrides.ticklabel_memo()`，前提是「作用域里没有任何
# 东西会改刻度」；而**本引擎的 execute() 正是跑在那个作用域里**（`build_manifest`
# 进来第一件事就是 `fig.canvas.draw()`）。git 说这两个改动不冲突，可它们动的是
# 同一份状态——所以这条时序要有判据看着，不能靠读代码放心。
#
# 安全的理由**不是**「引擎不碰刻度」（它移动 axes，轴一变长短刻度就会重算），
# 而是**移动发生在记忆表还空着的时候**：作用域内唯一的 draw 是第一条语句。
# 谁哪天在缓存之后再插一次 draw，下面这条会红。
memo_state = []
_orig_execute = overrides.PinnedTightLayoutEngine.execute


def _spy(self, f):
    table = getattr(overrides._ticklabel_memo, "table", None)
    memo_state.append(None if table is None else len(table))
    return _orig_execute(self, f)


fig, axs = make(2, layout="tight")
st = overrides.FigState(fig)
manifest.instrument(st)
warns = overrides.apply(st, [{"gid": "axes_0", "prop": "position", "value": PIN}])
assert not warns, warns
overrides.PinnedTightLayoutEngine.execute = _spy
try:
    # apply() 那道 RuntimeError 守卫不在本引擎的路径上——execute 不调 apply
    m1 = manifest.build_manifest(st, "T")
    m2 = manifest.build_manifest(st, "T")
finally:
    overrides.PinnedTightLayoutEngine.execute = _orig_execute

in_scope = [n for n in memo_state if n is not None]
assert in_scope, "execute 一次都没跑在记忆表作用域里——夹具没覆盖到这条时序，判据是空的"
assert set(in_scope) == {0}, (
    "引擎在记忆表**已经缓存过刻度之后**才移动 axes：缓存的是移动前那一版刻度，"
    "而轴一变长短刻度就会重算。作用域内的 draw 必须只有第一条语句那一次。",
    memo_state)


def _xticks(man, gid):
    return [
        e["editable"][0]["value"]
        for e in man["elements"]
        if e["gid"].startswith(gid + ".xticklabels_")
    ]


assert _xticks(m1, "axes_0") == _xticks(m2, "axes_0"), (
    "连着两次 build_manifest 的刻度就不一样了", _xticks(m1, "axes_0"), _xticks(m2, "axes_0"))
b = next(e["bbox"] for e in m2["elements"] if e["gid"] == "axes_0")
want = [PIN[0], 1.0 - PIN[1] - PIN[3], PIN[2], PIN[3]]
assert all(abs(x - y) < 1e-9 for x, y in zip(b, want)), ("记忆表作用域里钉住的位置不对", b, want)
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
