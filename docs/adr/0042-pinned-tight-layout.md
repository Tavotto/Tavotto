# ADR 0042：持久 tight 布局下的子图位置——钉住被编辑的，其余照旧自动排版

日期：2026-09-03 · 状态：已接受 · 关联：issue #162（本条）/ #140（guard 落地）/
#76（reason 的渲染出口）/ #137（几何权威缺席时的置灰）/ ADR 0009（写回验证的像素门）

## 背景

`plt.subplots(layout="tight")` 与 `plt.subplots(tight_layout=True)` 会在 figure 上挂一个
**持久的** `TightLayoutEngine`。它在**每一次绘制**里重算所有带 `SubplotSpec` 的子图落位，
所以 Tavotto 落 `axes.position` 的方式（`ax.set_position(v)`）在这类图上一按就被算回去。

这条路走过两步：

* #140 之前是 **silent wrong**：文档里记着 override、`baked_overrides` 里烙着它、
  撤销栈里有它，而画面纹丝不动。
* #140（PR #161）把它改成**不宣称这条能力**：`resizable=false`、`position` 字段不出、
  `unsupported_props` 给出 reason `layout_engine_tight`。不再骗人了，但用这种写法建图的
  用户从此**拖不动子图、不能多选对齐、不能改 mm 宽高、不能成组缩放**——而
  `layout="tight"` 是论文图里最常见的写法之一。

本 ADR 是第二步：把能力拿回来。

## 量过的三条路（3.9.4 / 3.10.8 / 3.11.1 各跑一遍，结论完全一致）

| 做法 | 结论 |
|---|---|
| `ax.set_in_layout(False)` | **无效**。三版都挡不住 `TightLayoutEngine`（`tests/test_layout_engine_pinning.py` 里有一条断言固化了这个否定结论：它哪天红了就是上游改了行为） |
| 应用 position 前 `fig.set_layout_engine("none")` | 能 work，但**同时关掉这张图对其它元素的自动排版**：用户改字号时不再自动让位。副作用面比现状更糟 |
| 自定义 layout engine | **成立**，但只有一种写法成立，见下 |

## 决定

`overrides.PinnedTightLayoutEngine`——`TightLayoutEngine` 的子类。落第一条
`axes.position` override 时把持久 tight 引擎换成它。它每次 `execute()` 做三件事：

1. 把**被 override 过的**轴放回 `SubplotSpec` 该给它的格子；
2. 让 `TightLayoutEngine.execute()` 照常算它自己那份；
3. 再把用户摆过的位置盖回去。

于是被编辑过的子图钉得住，**其余一切照旧由 tight 自动排版**。

### 第 1 步不是可有可无的——它是写回事务不变式的所在

最直觉的写法只有第 2、3 步（先算 tight，再盖回去）。它画得出正确的画面，却让
**「热态所见 == 写进文件的 == 重开后重放出来的」当场破掉**，原因在
`matplotlib._tight_layout.get_tight_layout_figure` 里：它拿 `ss.get_position(fig)`
（**gridspec 该给这个格子的位置**）当 `ax_bbox`、拿 axes **当前**的 tight bbox 当
`tight_bbox`，两者相减得到边距。被 pin 的轴一旦离开自己的格子，这个差就不再是
「装饰物探出去多少」。实测（三版一致）：

* 连画 10 次**都没有收敛**；
* 「先画两次再 pin」与「一次性 pin 再画」收敛到**不同的结果**——热态 ≠ 重放，
  而写回自检的几何比对正好量得到这一维（409，或者更坏）。

加上第 1 步之后，tight 的输入与「一条 override 都没有」时逐位相同，输出也逐位相同：
未 pin 的轴在 10 次绘制上与零 override 基准**全部逐位相等**，热态与重放收敛到同一张图。

### 裁决：未 override 的其它元素，自动排版**保留**

这是 issue #162 的关闭条件之三，不许静默换语义。三个可选答案里选「保留」：

* **保留**（本 ADR）：只有被拖过的子图不再自动排版，别的照旧。用户改字号、加长轴标签
  时其余子图仍然自动让位。代价是被拖过的那个不再让位——但那正是用户明确表达过的意图。
* 关闭整张图的自动排版（`set_layout_engine("none")`）：一次编辑换掉整张图的语义，
  副作用面最大，已排除。
* 把被 pin 的轴从 tight 计算里**整个排除**：也能收敛（实测第 1 次绘制就到不动点），
  但它会让**没被拖的**子图跟着重排（实测挪 0.07 以上）。「我拖了 A，B 跟着跳」
  比「A 不再自动让位」更难解释，排除。

**用户可见的表现**：把子图拖到别处之后，那个子图就停在那儿；别的子图、以及这张图上
其它元素的自动排版一切照旧。撤销那条 override 之后，它立刻回到自动排版
（`unpin` 是 `_RESTORE` 的一部分，实测逐位回到「从没被 override 过」的位置）。

### 只有一个安装点，而它是热态与重放共用的那条路

「解引擎这一步必须同时发生在热态与重放两侧」（issue #162 点名的约束）落地的办法
**不是两边各调一次，而是只有一个调用点**：`overrides._set_axes_position`。两侧都在
`overrides.apply()` 里、在同一个规范顺序档位上、在这张图的第一条 position override
落下的那一刻走到它——**同一条代码路径，没有第二份需要对齐的实现**。旧文档与直接调
API / MCP 的来路也一样走它。

`manifest.instrument()` 里原本也调过一次（想在建 manifest 之前就换掉）。变异反证证明
那一次**杀不死**：删掉它整套用例全绿，因为 setter 已经覆盖同一件事。同一条保证实现两遍，
坏掉一份另一份会替它兜住——两条变异一起存活。删掉之后 setter 那条变异当场变红。
同一轮里还抓到一对同构的冗余：`ensure_pinnable_layout_engine` 的「已经换过就早退」与
`figure_layout_engine_eats_position` 里「排除自己的子类」也是同一条保证的两份实现，
收敛成后者一份。

换上去是无条件的：pin 表为空时它与原生 `TightLayoutEngine` **逐字节相同**（实测像素与
位置），所以从没被编辑过的图零影响。

### 接管的是**原件实例**，不是它的参数

`ensure_pinnable_layout_engine` 把原引擎**包起来**（`PinnedTightLayoutEngine(inner)`），
`execute()` 里调 `inner.execute(fig)`，`_adjust_compatible` / `_colorbar_gridspec` 跟着
原件走，`set` / `get` 转发。**不用 `PinnedTightLayoutEngine(**inner.get())` 重建**：
用户脚本可以挂自己的 `TightLayoutEngine` 子类，`isinstance` 判据同样选中它，重建会把
它重写过的 `execute()` 与子类状态静默丢掉（每个没被 pin 的轴的落位跟着变），而子类的
`get()` 多回一个键时重建会当场 TypeError。#140 的 guard 已经拆掉，这条路上的失败就是
静默的。

`set` 的签名必须与 `TightLayoutEngine.set` **逐字相同**（`*, pad, w_pad, h_pad, rect`）
——上游那份的实现是 `for td in self.set.__kwdefaults__`，而 `self.set` 解析到的是子类
的 override；写成 `**kwargs` 的话 `__kwdefaults__` 是 `None`，第一次建引擎就
`TypeError`（实测）。

### setter 里的顺序：可能失败的那一步排在不可逆的两步之前

`_set_axes_position` 先 `a.set_position(bounds)`，**之后**才换引擎、落 pin。反过来写
会烧掉一张图：`set_position` 对长度不是 4 的 bounds 抛 `TypeError`，而 pin 已经落下——
`apply` 把异常收成一条 warning、**不记进 `state.applied`**，于是还原那条路永远不跑、
永远不 `unpin`。坏 bounds 从此留在引擎里，而 `Figure.draw` **只吞 `ValueError`**：
`Bbox.from_bounds()` 抛的是 `TypeError`，它一路冒出去，**这张图再也画不出来，且撤销
不回来**（三版实测）。

选换顺序而不是校验长度：校验只挡这一种坏输入，换顺序挡 `set_position` 的**每一种**
失败。这与 #190 那一族是同一句话——不可逆的那一步不许排在可能失败的那一步之前。

### 与寄生轴（#217 / ADR 无，见 `src/tavotto/AGENTS.md`）相遇时谁说了算

`host_subplot(...).twinx()` 造出来的**寄生轴**被宿主的 `draw()` 每帧按宿主 rect
重置，所以它的 `position` 是死开关（`position_locked`，reason `parasite_host_rect`）。
本 ADR 让宿主的 `position` 可编辑了，两条规则因此在同一张图上相遇。

**它们不冲突，因为管的是不同的轴，而且顺序天然是对的**：布局引擎在
`Figure.draw` 的**最前面**跑（钉住宿主），宿主的 `draw()` 随后把自己的 rect 推给
寄生轴（寄生跟着走）。三版实测：tight 图上拖 `host_subplot`，宿主逐位落在请求的
位置、寄生轴的 manifest bbox 与宿主**逐位相同**、寄生轴自己照旧不宣称 `position`；
绘制次数对齐时「改了又撤销」逐位回到从没 override 过的样子。

用户看到的：拖宿主 = 宿主到位、它的右轴跟着走；右轴自己拖不动（本来也不该动，
它没有独立的落位）。看护 `tests/test_layout_engine_pinning.py` 第 11 节。

### `layout_engine_tight` 这条 reason 连同 i18n 文案一并删除

guard 拆掉才算真修完（#162 的关闭条件之五）。`position_locked` 现在只剩一个来源
`child_axes_locator`（子 axes 的父级 `_axes_locator` 每帧重算）。

## 已知的上游性质：tight 图的画面永远到不了不动点

这一条与本 ADR 的改动**无关**，但值得记下来，因为它决定了判据该用什么尺子。
一张**零 override** 的 `layout="tight"` 图连画 14 次，会出现 4 种不同的画面
（三版实测；位置早已收敛，飘的是 ylabel 的落点——`_update_label_position` 拿上一帧的
刻度包围盒算这一帧的偏移，与 tight 互相追着跑）。同一张图不挂引擎时 14 次全同一帧。

后果一：**在持久 tight 图上，像素这把尺基本是坏的**。走 Tavotto 真实渲染路径量的话，
同一个 stem、同一组 patch 连要三张 `preview_png`（状态中立：应用 → 出图 → 还原），
出来的是**三张不同的图**。所以这类图上任何「两张渲染逐字节相同 / 不同」的判据都会
时红时绿，而「改过之后画面不一样」这种 `!=` 判据更会被这层噪声**白送**——它看起来在
守着 #140 那个 silent wrong，其实什么都没守。

后果二（同一个坑的浮点版）：**`!=` 不能当「真的动了」的判据**。原生 tight 的收敛残差
让相邻两轮的包围盒差 6e-10，Python 的 `!=` 照样为真。变异反证抓到过：把
`execute()` 末尾「盖回 pin」那一步删掉，端到端那条用例**全绿**。判据必须问
「**拖到哪就是哪**」——钉住之后 `axes_0` 的 bbox 逐位等于请求的矩形（实测连画几轮
都不变，差 0.0），做错的实现与它差 0.34。

`tests/test_layout_engine_pinning.py` 的判据因此一律用位置，只在两侧绘制次数与顺序
完全对齐处比像素（换引擎那一节：两张全新图各画同样多次），并留了一条断言盯着上游
——它哪天不红了，才谈得上给这类图加像素判据。

还有一条同源的：**判据的两条腿之间不许夹一次别的尺度的渲染**。`preview_png` 画的是
380 px 的预览，那一次渲染把 tight 的迭代带到另一个尺度上，回到正常尺寸后要几个回合才
收敛回来——这个瞬态在 3.11.1 上有 **9.8e-3**（比 `REPLAY_GEOM_TOL` 还大），在 3.10.8 上
只有 1.5e-4。夹在两条被比较的腿中间，量到的就是它。节奏对齐之后，「拖子图」与「换个
颜色」对邻居的影响**逐位相同**（三版 6.1e-9 / 4.0e-9 / 4.0e-9）——也就是说邻居**根本
没动**。

同源的还有一条：**「整份 manifest 逐位相同」这把尺在这类图上也不成立**，而且与
override 无关——`InvTight` 上量的是零 override 时热态与全新重放差 1.93e-6，
拖过子图之后差 1.63e-6（**更小**）。走 Tavotto 真实渲染路径时两侧不受影响：
`preview_png` 是状态中立的（应用 → 出图 → 还原），两条腿走同一条路，实测
`_compare_manifests` 两种情形都**零分歧**、像素**逐字节相同**。所以写回的
两道门（ADR 0009）在这类图上照常放行，`test_invariants_engine.py` 的这条用例也
就用它们当尺子，而不是用别处那把「整份 manifest 逐位相同」。
