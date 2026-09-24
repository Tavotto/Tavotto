# 图例条目与绑定（2026-09-02，ADR 0034）

> 原文出自 `web/AGENTS.md`「图例条目与绑定（2026-09-02，ADR 0034）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

完整版在 `docs/adr/0034-legend-entry-binding.md`，改动前先读。

* **图例项 = 一段文字 + 一个条目**：文字那半走 ADR 0032 的 Typography 控件；
  条目那半（`binding` / `handle_*` / `visible`）是 `legend_text` 元素上的普通
  manifest 字段。`lib/legendModel.ts` 是前端投影：显示顺序、每项此刻的绑定
  （判据与引擎 `effective_binding` 同一条——任一 `handle_*` override 在即
  custom，**不是**「值和源一不一样」）、「恢复跟随」的计划。
  `LEGEND_ENTRY_STYLE_PROPS` / `LEGEND_BINDINGS` 与 `engine/overrides` 严格同源。
* **图例卡**（`inspector/LegendCard.tsx`）承接 `fontsize`（Typography 批量作用
  于全部项）与 `entry_order`（条目列表的上下移动），通用列表让出这两条
  （`LEGEND_CARD_PROPS`）；没有项的图例不出卡、字段留在通用列表。示意线
  预览读 manifest 的 `handle_*`，**不是第二份样式判断**。
* **排版详情卡**（`controls/LegendSpacingCard.tsx`，审计 T17）承接五条间距
  （`LEGEND_SPACING_PROPS`），与有没有条目无关。默认折叠、改过任意一条自动
  展开；标签**不定宽**（72px 的标签列正是把「线与文字间距」截成「线与文字间…」
  的那个机制）；单位写 `em`——matplotlib 这五条按字号的倍数计，引擎不发 `unit`。
* **图例项与源对象是一个链条开关**（审计 T18）：一行「链接到：曲线 “sin”」+
  开关（断开 ↔ 恢复），来源入口两种状态下都在。示意线的五条样式**只在断开后
  出现**（`visibleWhen`，判据 `binding !== 'follow_source'`）。**脱开的判据没变**
  ——任一 `handle_*` override 在即 custom（`legendModel.entryBinding`），它仍然
  管着老文档；变的只是界面上没有「改样式即脱开」这条路，提示文案也跟着改了。
* **恢复跟随**只有 `store/actions.restoreLegendEntryFollow` 一处：删全部
  `handle_*` override + 按 `binding_default` 决定写 `binding=follow_source` 还是
  删 binding override，**一次 commit**。别在组件里逐条 `clearOverride`——那是
  一串撤销记录，中间态还会渲染出半跟随半自定义的图例。
* 位置控件的档位名没有「自动」：`best` 叫「最佳位置」，拖过叫「自定义位置」。
  2026-09-15 打磨把它的**形态**改成这一组的第十格（32px 方格，与九宫格 / 外侧位同一副），
  格子里写短写「自动 / Auto」，**可达名与气泡仍是 `optionLabel('loc','best')`**——
  同组那九格连可见文字都没有，名字这一份不许分叉。九宫格
  那个方框就是参照的容器，框下写出它叫什么（「相对子图 1」），认不出来就不写。
* **位置控件是内 / 外两带的一个控件**（2026-09-07，ADR 0034 修订）：内 = 九宫格 +
  「最佳位置」，外 = 六个常用外侧位（`lib/legendModel.LEGEND_OUTSIDE_PRESETS`，
  **纯界面预设、不是同源对**）+ 自定义锚点 x / y。写的是 `loc` 与 `loc_anchor`
  两条 prop，但**一次点击一次 commit**（`store/actions.setLegendPlacement`）：
  写 `loc`、按需写 `loc_anchor`、把拖动留下的 `loc_frac` 一并删掉——不删的话
  引擎里拖动压过锚点，用户点了预设看不见任何变化。选内侧时**此刻确实有锚点
  才写 `loc_anchor: null`**（`null` 是「不要锚框」这个取值，不是「没表态」）。
  控件里那张示意图按当前值重画（静态内联 SVG、无动画）：虚线框是参照的容器，
  实心块是图例落点，算法与 matplotlib 同源（锚框上取 `loc` 那个角、图例同名角
  贴上去），算不出来时只画容器、不画一个猜的方块。三个入口（属性页 /
  `QuickEdit` / `ElementBar`）与多选路径都给外侧带；多选时锚点**全体一致才给**
  （与 `sharedMarkerShape` 同一条纪律）。引擎不发 `loc_anchor` 时整带不出现，
  理由由 `UnsupportedProps` 按 reason code 说出口。
* **整体缩放 = 拖图例的四个角**（2026-09-25 用户反馈，`canvas/interactions.startLegendScale`
  + `lib/legendScale`）。图例盒尺寸 = 文字字号 × 一组以 `Legend._fontsize` 为单位的构建期
  参数，而引擎的图例 `fontsize` override 只改每条文字（`legendmodel._set_legend_fontsize`），
  `_fontsize` 不动——**只写字号不等于整体缩放**（字变大、边距和示意线原样）。所以倍数 s
  落成：`fontsize`、有标题时的 `title_fontsize`、`borderpad` / `labelspacing` /
  `handlelength` / `handletextpad` / `columnspacing` 各乘 s，再加一条 `loc_frac`
  把对角钉住（`loc_frac` 是图例框左下角；不写的话会绕 `loc` 预设那个角缩放）。全是已有的、
  可写回可重放的属性，引擎不加新属性。不缩的：`ncol`、标记大小（matplotlib 自己改字号也
  不缩）、边框线宽。倍数取光标在对角线方向的投影（内容像素空间），夹进每条属性的
  min / max 与 [0.25, 4]；基准优先取文档里尚未渲染回来的 override。一次 = 一条撤销 =
  一次渲染。**做** SVG 缩放预览（`svgPreviewStore.previewScale`，绕不动点的 `matrix`）：
  与子图缩放不同，这里字号与所有间距同乘一个倍数，成图几乎就是线性缩放（实测宽高
  1.16 : 1.16，预览 1.2 : 1.2——只差标记与示意高度）。
  已知限制（不是这次引入的）：manifest 在**文档 dpi（100）的 Agg** 上量图例框，与画布
  上的矢量 SVG 差得不小（用户那张图的 6.9pt 图例：高 0.135 vs 0.121），锚在预设位置的
  图例第一次被拖 / 缩放写成 `loc_frac` 时会跳几个像素（拖动本来就有）。
* 看护：`inspector/legendCard.test.tsx`、`inspector/legendSpacingCard.test.tsx`、
  `inspector/controls/pickers.test.tsx`、`canvas/inFigureDrag.test.tsx`（整体缩放）；Python 侧 `tests/test_legend_binding.py`、
  `tests/test_legend_anchor.py`。
