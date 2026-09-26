# 图例条目模型与位置模型

> 原文出自 `src/tavotto/AGENTS.md`「渲染引擎核心机制」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **图例条目模型（2026-09-02，ADR 0034）**：每个图例挂一份
  `legendmodel.LegendEntries`（`instrument` 时建，`_register_legend` 一处；整个图例族——条目模型、
  位置模型、示意线读写、`rebuild_legend` / `sync_legends`、重建后的接回——2026-09-18 起住在
  `engine/legendmodel.py`，`overrides` 只展开它的 `HANDLERS_*` / `RESTORE`、接线 `legend_text` 那组镜像登记，
  并以 `FigState.reapply` 给重建后的重放当分发入口；`legend_handle_props` 依赖映射判据、留在 overrides）。
  `axes_i.legend.texts_j` 的 **j 是原始序号**，重排 / 隐藏不改它；图例项的
  `_cls_key` 是 `legend_text`（text handler 逐条镜像 + 条目 handler：
  `handle_color/linestyle/linewidth/marker/markersize` / `binding` / `visible`），
  图例标题仍是 `text`。每一项按 label + 示意线指纹绑定源对象
  （`bind_legend_entries`，并列时只认 `get_legend_handles_labels()` 的位置，
  **不伪造**）；跟随的项在 `apply()` 尾部 `sync_legends` 从源重新派生示意线
  （派生显示，不进 applied）；任一 `handle_*` override 落下即脱开，**脱开的项 =
  脚本原样快照 + 文档里的 handle_***（2026-09-19，#414：原来的「源此刻派生的样子」
  只活在会话里，重放拿不到，热态 ≠ 重放；`custom_base` 字段已删）。「定格此刻」由前端
  「断开」把五条样式写成 override 兑现（`store/actions.detachLegendEntry`）。重建型
  prop（ncol / borderpad / labelspacing /
  handlelength / handletextpad / columnspacing / entry_order / 条目 visible / **fontsize**）
  一律走 `rebuild_legend`：素材是源对象或脚本原样快照，**不许把
  `leg.legend_handles` 副本喂回 `_init_legend_box`**（误差棒退化成 Line2D、
  markerscale 复利、标题字号丢——当年的 P2 就是这么来的）；重建后
  `_legend_box.set_offset(leg._findoffset)` 重挂定位回调，否则导出时图例整块
  消失。隐藏的项 Text 留在 index 里、manifest 报图例的框（否则「恢复显示」
  没入口）。前端投影 `web/src/lib/legendModel.ts`，两侧常量严格同源
  （`tests/test_legend_model_pairs.py`）。
- **自定义 handler 画的整格定格复刻（2026-09-24，用户 Figure2）**：matplotlib 不保存
  `legend(handles, …)` 收到的原始 handle，`legend_handles` 里只有 handler 回的**第一个**
  artist——脚本 `handler_map` 一格画 24 段矩形的色带，快照只剩第一段，任何重建都把它画成
  一块纯色。条目模型建时 `_freeze_entry` 比一格里的 artist 数与「从那一个示意线按默认
  handler 重派生」的数，多出来就把整格副本存成 `FrozenLegendHandle`（只收画在这一格自己
  坐标里的；否则不定格），重建时 `_FrozenHandler` 按新格尺寸等比铺回。**判据是有效绑定**
  （`is_frozen`：有定格且不在跟随）——同名同类型的源找到了、指纹对不上的项默认 custom，也得
  整格复刻（#544 评审）；跟随中的照旧从源派生（有源的误差棒），断开跟随时换回的是整格。**脚本自己画的**
  定格格子（`is_script_drawn`：色带等，默认不跟随源）只在整格同一种颜色时给 `handle_color`；本来跟随源、
  被断开的格子（有源的误差棒）照旧按示意线类型给控件——改之前给了、改完不能消失（不变式 capability
  truthfulness）。改色时整格同色（`frozen_color_uniform`）就写到这一格**每个** artist。示意线指纹带**未缩放的虚线节奏**（`_dash_key`）：
  `get_linestyle()` 对任何虚线元组都回 `'--'`，脚本给代理示意线的短虚线曾被误判成跟随源、
  第一次 apply 就换回源的长虚线。看护 `tests/test_legend_custom_handler.py`。
- **色带跟随它描述的那块图元的色图（2026-09-26，用户 Figure2 (c)：「图例无法与背景颜色相关联」）**：
  上一条定格下来的色带（脚本 handler 用网格的色图画的 24 段矩形）与网格没有任何 matplotlib 层面的
  关系，从色条 / 网格换色图后背景变了、色带还是原来那组颜色，条目在界面上显示「未关联」。
  `bind_colormap_swatches`（紧跟 `bind_legend_entries`，候选 = 同一子图里 `color_mapping_is_live`
  且有 cmap handler 的图元，figure 级图例看全图）**按颜色认**：定格格子里每一段看得见的单一填充色都
  落在候选色图的某一格上（`_RAMP_TOL` = 两级 8 位）、至少 3 个不同的格、铺开色图全长 ≥ 25%
  （`colormap_ramp`）；多块图元都对得上且不共用 norm 对象时不绑（不伪造）。认出来的项有源
  （`source_gid` 指向网格）、脚本原样 `follow_source`，记一份 `ColormapRamp`（绑定时的色图 + 每段在
  色图上的位置）。这种项**跟随中也整格定格复刻**（`is_frozen` / `base_of` 先看 `ramps`，不从网格按
  默认 handler 派生——那会画成一块纯色），`is_script_drawn` 为真（不摆 handle_* 控件）。颜色在
  `sync_legends` 里现算（`_sync_ramp`，派生显示、不进 applied）：跟随且源的色图与绑定时不同
  （`_same_cmap`：同一对象或 LUT 逐项相等）→ 每段按位置从新色图取色、透明度保留脚本的；否则写回
  定格副本的原色——所以没换色图时逐字节原样，撤销色图 / 断开后退回原色，热态 == 全量重放。
  看护 `tests/test_legend_custom_handler.py` 的「色带跟随色图」一节。
- **图例位置模型（2026-09-07，ADR 0034 修订）**：「图例摆在哪」的三条 prop
  （`loc` 预设 / `loc_frac` 画布拖动 / `loc_anchor` 外侧锚点）改的是同一件事，
  而且会互相盖写（`set_loc` 之前必须清锚框，设锚框又不能动 loc）——所以走边框 /
  刻度那套路数：各写自己的槽位（`legend_pos_cfg`，`instrument` 时采 `orig`），
  再 `apply_legend_pos_model` **整体重建**。**应用顺序不影响结果**（三条同在
  `_RANK_REST` 一档，各自当 setter 的话谁在列表里靠后谁赢，热态与全量重放当场
  分岔）；撤销一条 = 那个槽位退回未表态。优先级只写在模型里一处：**拖动过就是
  绝对定位，锚框强制清掉**。`loc_anchor` 的值是**父容器分数坐标里的一个点**
  `[x, y]`，`null` 是一个取值（不要锚框），与「没表态」（用脚本原样的锚框）
  不是一回事。脚本原样是 `(leg._loc, leg._bbox_to_anchor)` 这一对，**锚框存原
  对象**（`set_bbox_to_anchor` 会把 `TransformedBbox` 再包一层，坐标爆炸）。
  能力判据 `legend_anchor_state`：只有「父容器分数坐标里的一个点」才发字段，
  4 元组锚框与非父容器 `bbox_transform`（拿三个点量数值等价，不比对象身份）
  **不发字段、改发 `unsupported_props`**——把 4 元组显示成「没有锚点」是个语义
  错的精确值。看护 `tests/test_legend_anchor.py`。
- **隐藏图例的文字几何按文档 dpi 现排（2026-09-19，#413）**：图例文字的像素位置在
  `Legend.draw → OffsetBox.draw → TextArea.set_offset` 里写死，图例（或它住的 axes）
  一隐藏 `draw` 就跳过它，那组像素冻结在**上一次画它那回**——而 `preview_png` / `export` /
  `render_png` 在别的 dpi 上的 savefig 恰好会画一回，之后 manifest 量到的六个文字 bbox
  就是那次 dpi 的坐标除以文档像素（连常规 `render` 的 SVG 那次 `PREVIEW_DPI` draw 都会
  留下来）。契约：**隐藏图例文字的 bbox = 它在当前状态、文档 dpi 下显示时的 bbox**，与
  历史上谁画过它无关。落地在 `manifest._layout_legends_for_measure`：draw 会跳过的图例在一张
  一次性 `RendererAgg(W, H, fig.dpi)`（文字度量与 manifest 其余部分同一把矢量尺，见
  `marker-and-path-geometry.md`）上走一遍 `_legend_box.draw`（`_findoffset` 也在这条
  路上，`loc='best'` 照常），真 canvas 不碰。**不用「preview 之后补一次文档 dpi 的 draw」**：
  预览里图例可见、会话里图例隐藏时，补的那次 draw 同样跳过图例（3.8.4 / 3.10.8 / 3.11.1
  都量过）。看护 `tests/test_hidden_legend_geometry.py`（七条：三种别的 dpi 的 draw、预览
  显示 / 会话隐藏那格、藏 axes、隐藏 == 显示、可见图例不受影响）。
- **图例字号 = matplotlib 原生语义（2026-09-25，ADR 0034 修订）**：`legend.fontsize` 的标量值等于
  `ax.legend(fontsize=v)`——改 `Legend._fontsize`（`prop` 先拷一份再改，不碰脚本传进来的
  FontProperties）再 `rebuild_legend`，边距 / 行距 / 示意线长 / 线字间距 / 列距 / 行高下限都以它为单位、
  随之等比；那五条间距仍是各自的 override（以字号为单位的倍数），叠在新字号上单独调。标题字号不跟
  （原生也不跟，归 `title_fontsize`）。getter 仍回逐条列表：列表值 = **撤销形态**，盒按脚本原样的
  `_fontsize`（第一次改动前记在 `_mm_script_fontsize`）重排 + 文字逐条放回——只写回逐条字号的话框停在
  改过的尺寸上。广播语义不变（广播先于窄的，单条字号由重建后的接回重放保住）。**重建换掉文字对象后
  `_reindex_legend_children` 递增 `state.index_generation`**：`apply` 里按对象身份反查 gid 的缓存认这个
  代号，不认的话撤销图例字号时别名组组员静默变空、单条字号被冲掉。看护
  `tests/test_legend_fontsize_native.py`（对拍的另一侧是脚本自己的 `legend(fontsize=…)`）、
  `tests/test_worker_roundtrip.py::test_overlapping_override_undo_of_the_broadcast_keeps_the_narrow_one`、
  `tests/test_invariants_engine.py` 的 `A-legend-drop-broadcast`。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 重建型 prop 一律 `rebuild_legend`，不把 `legend_handles` 副本喂回
- 脱开的项 = 脚本原样 + 文档里的 handle_*（没有会话内的 custom_base）
- 三条位置 prop 写槽位再整体重建，拖动过即绝对定位
- `loc_anchor` 的 `null` 是取值
- 隐藏图例的文字几何按文档 dpi 现排（`manifest._layout_legends_for_measure`），不靠上一次 draw
- 自定义 handler 画的整格在不跟随时定格复刻
- 指纹带虚线节奏
