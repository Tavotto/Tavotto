# ADR 0034：图例条目模型 —— 稳定序号、源对象绑定与跟随同步

状态：**Accepted**
日期：2026-09-02
相关：[0032 属性能力层](0032-typography-capability-layer.md)（图例文字的排版走同一份
Typography 控件）、[0030 统一检查与问题定位](0030-validation-and-problem-navigation.md)
（图例项的线宽规则定位到图例项本身）、[0009 写回像素门](0009-write-back-pixel-verification.md)
（跟随同步是派生显示，热态 == 重放必须继续成立），
本轨道文档 [`docs/implementation/product-ux-reliability/`](../implementation/product-ux-reliability/STATUS.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 图例项的稳定身份 | `axes_i.legend.texts_j` 的 **j 是原始序号**（创建时的第 j 项），重排 / 隐藏都不改它。此前 j 是显示位置：改过第一行的字再把它移到最后，字留在第一行 |
| 图例项与图中对象的关系 | 每一项**尽可能**绑定一个源对象（曲线 / 散点 / 填充 / 柱系列 / 误差棒容器）；判据是 label + 示意线指纹（见下）。找不到就没有绑定——**不伪造** |
| 默认绑定 | 源找到且示意线与源一致 → `follow_source`；源找到但脚本在 `legend()` 之后改过示意线（或改过源）→ `custom`。后者默认不跟随：跟随等于改掉脚本此刻画出来的东西 |
| 跟随的含义 | 示意线由源对象**派生**：每次 `apply()` 尾部按 matplotlib 自己的 handler 从源重新造一份（与 `ax.legend()` 同一条路）。**派生显示，不进文档、不进 applied、不产生历史** |
| 脱开的含义 | 任何一条 `handle_*` override 落下即 `custom`；也可显式写 `binding = custom`（冻结在此刻从源派生出来的样子）。脱开点 `custom_base` 是**源此刻**的样子，不是脚本原样——改列数重排之后它不会退回脚本原样 |
| 恢复跟随 | 删掉全部 `handle_*` override；脚本原样是 custom 的项写一条 `binding = follow_source`。一次 commit、一条撤销 |
| 「有没有 override」vs「值一不一样」 | 判 custom 的是**文档里有没有 override**，不是示意线的值是否等于源。用户把颜色改成与源相同的值，仍是「我要自己管这一项」 |
| 文字与源的 label | **不同步**（沿用既有契约，`tests/test_legend_text.py`）：改曲线 label 不覆盖图例上的字，改图例上的字不动曲线 label |
| 隐藏一项 | 整项（示意线 + 文字）从图例盒里拿掉；元素表里**留着它**（框 = 图例的框），否则「恢复显示」没有入口 |
| 重建型 prop（列数 / 间距 / 顺序 / 隐藏） | 从**源对象或脚本原样快照**派生重建，不再把示意线副本喂回 `_init_legend_box`。误差棒仍是误差棒、markerscale 只乘一次、标题字号不丢——撤销到底逐位回原样，invariants 里那条图例重建豁免已删 |
| 「自动」这个位置按钮 | 不存在了。matplotlib 的 `best` 叫**最佳位置**（按数据避让），拖动过的叫**自定义位置**；导入原图的 `best` 状态原样保留 |
| 高频项 | 位置 / 列数 / 示意线长 / 线与文字间距 / 行间距 / 列间距（多列时）/ 边框四条常驻首屏；字号与条目顺序由图例卡接管；标题 / 内边距 / 透明度进「更多」 |
| 检查 | `line-width-off-preset` 也看**自定义**图例项的示意线宽（定位到那一项）；跟随的项由源那条规则管着，不报两遍 |
| 磁盘格式 | **不升版**：新增的都是 override（`gid + prop + value`），老文档一个字节不变 |

## 1. 背景

matplotlib 的图例示意线是 `legend()` 那一刻从源对象**复制**出来的（`HandlerBase.
update_prop` → `update_from`），此后源变它不变。实测 3.10.8：`line.set_color("g")`
之后 `leg.legend_handles[0].get_color()` 仍是 `"r"`。Tavotto 改曲线颜色走的是
override → `set_color`，于是图上是绿线、图例上是红线，而且没有任何提示。

在此之上，重建型 prop（`ncol` / `labelspacing` / …）的 setter 把 `leg.legend_handles`
（副本）再喂回 `_init_legend_box`，副本再复制一次：误差棒的示意线从
`LineCollection` 退化成 `Line2D`、`markerscale` 每重建一次多乘一次
（4 → 6 → 9 → 13.5）、`set_title(prop=None)` 让标题退回默认字号。这三条是
`docs/1.0-release-readiness.md` §4.1 记的「图例重建路径不是幂等的」P2 backlog。

`texts_j` 按显示位置编号则是一条更安静的缺陷：改过第一项的字，再把它挪到最后，
字留在第一行——override 跟着位置走而不是跟着那一项走。

## 2. 条目模型

```text
engine/overrides.LegendEntries（挂在 leg._mm_entries，instrument 时建）
  n                 项数（有 handler 的项；`legend_handles` 里的 None 不算）
  orig_fp[j]        创建时示意线的指纹（绑定用）
  pristine[j]       示意线的脚本原样快照（独立对象；没有源的项重建时的素材）
  custom_base[j]    这一项「不带任何 handle_* override 时长什么样」
  texts[j]          当前 Text 对象（隐藏的项保留最后那个，gid 与 override 挂在它上面）
  order / hidden    显示顺序（原始序号的排列）/ 隐藏集
  sources[j]        源对象（artist 或容器）/ None
  source_gids[j]    源对象的元素 gid
  default_binding[j]  脚本原样：follow_source | custom
  binding_override[j] 显式 override（可缺席）
  effective_binding(j):
      None                          源缺席
      custom                        任一 handle_* override 在
      binding_override[j]           显式表态
      default_binding[j]            脚本原样
```

**指纹**（`legend_handle_fingerprint`）按示意线类型取会被 `update_from` 复制的
那几条：Line2D 取颜色 / 线型 / 线宽 / marker / markersize / 面色 / 边色 / 边宽 /
alpha；Patch 取面色 / 边色 / 线宽 / 线型 / 花纹 / alpha / fill；Collection 取首个
面色 / 边色 / 线宽 / 花纹 / alpha。类型名进指纹。

**绑定**（`bind_legend_entries`）：对每个候选源对象按 matplotlib 自己的 handler
派生一份示意线取指纹，与图例上现有的示意线比。指纹 + label 都相等且唯一 →
follow；只指纹相等且唯一 → follow（脚本把 labels 单独传了）；只 label 相等且
类型一致且唯一 → custom；并列时只在 `get_legend_handles_labels()` 的位置对得上
时选它，否则不绑。**绑错一条比不绑更坏。**

## 3. 同步与重建

* **同步**（`sync_legends`，`apply()` 尾部）：跟随的项——把 handlebox 里的子
  artist 换成从源现派生的那份（`legend_fresh_handle` 画进原来的 DrawingArea），
  只动示意线本身，不动布局盒、文字、定位回调，所以**不改包围盒**。custom 而
  没有 override 的项——示意线该是 `custom_base` 的样子（撤掉 binding override
  之后要退回脚本原样），指纹不同才换。
* **脱开**（`_detach_entry`）：第一条 `handle_*` override 落下、或显式
  `binding = custom` 时，`custom_base[j]` 记成**源此刻**派生出来的样子，盒里那份
  也换成它。必须从源现派生、不能拿盒里那份：同一批 patch 里源的改动可能排在
  前面，而同步要到整轮结束才跑——盒里那份此刻还是上一轮的样子。
* **重建**（`rebuild_legend`）：`_init_legend_box(handles, labels)` 的 handles 是
  `base_of(j)`（跟随的取源、其余取 custom_base），文字整批换新后把旧文字的
  样子（颜色 / 字体属性 / alpha / 显隐 / path effects）搬过去、标题带着字体属性
  重设；`_reindex_legend_children` 按原始序号接回 gid / 模型 / `state.index`，
  并重放已应用的 override（状态类 prop `binding` / `visible` 不重放——模型自己
  就是它们的落点）。快照派生会把 markerscale 再乘一次，事后把 markersize 放回。

热态与全量重放走的是同一条 `apply()`，所以「所见 == 文档重放 == 写回 == 重开」
继续成立（`test_hot_equals_fresh_replay` / `test_undo_to_zero_is_pixel_identical`）。

## 4. 界面

* **图例卡**（`components/inspector/LegendCard.tsx`，图例页首屏）：Typography
  控件批量作用于全部图例项（`useFigureTypography`，ADR 0032 的批量适配器）；
  条目列表按显示顺序——示意线预览（读 manifest 的 `handle_*`，不是第二份样式
  判断）、文字、「跟随 / 自定义 / 未关联」徽标、显隐、上下移动。点文字选中那
  一项。卡片承接掉 `fontsize` 与 `entry_order`，通用列表让出来。
* **图例项页**：`binding` 字段渲染成一行状态 + 动作
  （`controls/LegendBindingControl.tsx`）：跟随中 → 「改为自定义」；自定义 →
  「恢复跟随」（`store/actions.restoreLegendEntryFollow`，一次 commit）；另有
  「查看源对象」。没有源的项引擎不发 `binding`，界面就没有这一行。
* `lib/legendModel.ts` 是前端投影：显示顺序、每项的绑定（与引擎同一条规则——
  用户刚改了颜色，徽标不该等下一帧才变）、恢复跟随的计划。
  `LEGEND_ENTRY_STYLE_PROPS` / `LEGEND_BINDINGS` 与 `engine/overrides` 严格同源
  （`tests/test_legend_model_pairs.py`，顺序也比）。
* 位置控件：`best` 显示为「最佳位置」，拖动过显示「自定义位置」；没有「自动」。

## 5. 降级与限制

* **示意线类型决定能改什么**：曲线的示意线（Line2D）五条全有；柱 / 填充 /
  散点的示意线只有颜色；色图正在决定颜色的散点连颜色都没有（`set_facecolor`
  下一帧被 `update_scalarmappable()` 覆盖回去——判据与散点本体同一个）。
* **没有源的项**（脚本用了代理 artist）：示意线照常可编辑，没有绑定行。
* **手工造不出同类快照的示意线**（误差棒的 LineCollection）：`pristine` 只能是
  原对象本身，这一项的 `handle_*` override 会直接改到它——没有源的误差棒项
  撤销到底后样式回不到原样（有源的走源派生，不受影响）。
* **切回跟随后 custom overrides 一律清掉**（不保留）：保留的话「跟随」这个词
  就不再是真的——下次源变它不变。
* 源对象在脚本里被删掉时，图例本身也是脚本建的，那一项通常随之消失；
  真正会留下的是指向已消失 gid 的 override，走既有的「孤儿 override」处理。
* 曲线颜色的 SVG 局部预览只改曲线本身，图例示意线要等这一轮定稿渲染回来才
  跟着变（几百毫秒）。给示意线挂 gid 做预览是后话。

## 6. 看护

`tests/test_legend_binding.py`（绑定 / 同步 / 脱开 / 恢复 / 隐藏 / 重排 / 热态 ==
重放 / 撤销逐位 / 布局旋钮）、`tests/test_legend_text.py`（原始序号契约）、
`tests/test_invariants_engine.py`（能力真实 + 撤销逐位，图例重建豁免已删，
`test_legend_rebuild_restores_exactly` 钉住误差棒与 markerscale）、
`tests/test_legend_model_pairs.py`、`web/src/components/inspector/legendCard.test.tsx`、
`tests/golden/preflight_vectors.json` 的 `legend-entry-custom-handle-width`。
