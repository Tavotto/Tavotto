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

`overrides.PinnedTightLayoutEngine`——`TightLayoutEngine` 的子类，
`manifest.instrument()` 无条件把持久 tight 引擎换成它。它每次 `execute()` 做三件事：

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

### 两个消费点，一份实现

`ensure_pinnable_layout_engine(fig)` 只有两个调用点，而热态与重放**都会走到它们**：

* `manifest.instrument()`——三个入口（`figsession` / `browser` / `bridge_runner`）
  都是 `FigState` + `instrument`；必须在这里换，因为「position 能不能编辑」是 manifest
  当场就要回答的问题；
* `_set_axes_position`——**第二个消费点**，挡的是没走过 instrument 的来路
  （1.0 之前存下的旧文档、直接调 API / MCP 的调用）。

无条件换是安全的：pin 表为空时它与原生 `TightLayoutEngine` **逐字节相同**（实测像素与
位置），所以从没被编辑过的图零影响。

### `layout_engine_tight` 这条 reason 连同 i18n 文案一并删除

guard 拆掉才算真修完（#162 的关闭条件之五）。`position_locked` 现在只剩一个来源
`child_axes_locator`（子 axes 的父级 `_axes_locator` 每帧重算）。

## 已知的上游性质：tight 图的画面永远到不了不动点

这一条与本 ADR 的改动**无关**，但值得记下来，因为它决定了判据该用什么尺子。
一张**零 override** 的 `layout="tight"` 图连画 14 次，会出现 4 种不同的画面
（三版实测；位置早已收敛，飘的是 ylabel 的落点——`_update_label_position` 拿上一帧的
刻度包围盒算这一帧的偏移，与 tight 互相追着跑）。同一张图不挂引擎时 14 次全同一帧。

后果：**在持久 tight 图上，「两侧画的次数不同就不能比像素」**。
`tests/test_layout_engine_pinning.py` 的判据因此一律用位置，只在两侧绘制次数与顺序
完全对齐处比像素，并留了一条断言盯着上游——它哪天不红了，才谈得上给这类图加像素判据。

同源的一条：**「整份 manifest 逐位相同」这把尺在这类图上也不成立**，而且与
override 无关——`InvTight` 上量的是零 override 时热态与全新重放差 1.93e-6，
拖过子图之后差 1.63e-6（**更小**）。走 Tavotto 真实渲染路径时两侧不受影响：
`preview_png` 是状态中立的（应用 → 出图 → 还原），两条腿走同一条路，实测
`_compare_manifests` 两种情形都**零分歧**、像素**逐字节相同**。所以写回的
两道门（ADR 0009）在这类图上照常放行，`test_invariants_engine.py` 的这条用例也
就用它们当尺子，而不是用别处那把「整份 manifest 逐位相同」。
