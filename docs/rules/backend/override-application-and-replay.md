# override 语义、应用顺序与全量重放

> 原文出自 `src/tavotto/AGENTS.md`「渲染引擎核心机制」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **导出 PDF / PS 一律 fonttype 42**（`figsession.export_font_context`，T-122）：matplotlib 默认的
  Type 3 把 U+00FF 之外的字符画成 XObject，像素对、文本层没有它们（`⁵ μ α ≤`……），
  期刊也拒收。改回 3 的前提是先让 `tests/test_scientific_text_matrix.py` 有别的办法绿。
- override 是**全量列表**语义：worker 维护 applied/originals 两表，缺失的 key 自动
  恢复原值（undo 的基础）。前端永远发完整 `o.overrides`。
- **刻度组有 `fontfamily`、文字元素报真正画字的脸（2026-09-13，ADR 0051）**：
  `("ticks", "fontfamily")` 走 `tick_params(labelfontfamily=…)`（matplotlib ≥ 3.7）
  + 已有标签逐条 `set_math_fontfamily("custom")`——刻度**数量增长**后新建的标签里的
  mathtext 仍在默认字体集，这是明示的边界。manifest 给每个带字的元素（含刻度组与
  单条刻度）加 `face`（正文族链解析到的第一张脸的族名）与 `math_face`（文字含
  `$…$` 时：custom 集按 `mathtext.rm` 解析，内置集按 `_MATHTEXT_SET_FACES`），唯一
  出处 `manifest.font_faces()`。**请求的族名 ≠ face 就是没装上、matplotlib 静默退了**
  ——白名单式的 `font-family-substituted` 只认名字，量不到这一维；保留式规范化的
  `font_unavailable` 退出靠的就是它。`_apply_mathtext_custom_set` 是文字与刻度共用的
  那一段 rcParams 写入。
- **保留式规范化的引擎侧三模块（ADR 0051，纯标准库，Flask / MCP 两个进程都 import）**：
  `engine/normalize.py`（约定 / 计划 / 授权 / 实效比对 / 边距与图例候选 / 预算）、
  `engine/interference.py`（`text-overlap` / `text-over-axes` / `legend-over-data`，
  与预检同一形状；`measure()` / `issue_key()` 是「加没加重」的尺）、
  `engine/artifactcheck.py`（最终文件按格式验；`pdfbackend.pdf_fonts` 是它唯一的
  PDF 后端入口）。`preflight.element_overflow()` 是 `element-outside-figure` 的逐元素
  判据，`_check_panel_clipping` 与 B0 对比共用它——改判据只改这一处。三条干涉检查的
  severity 登记在 `publication.json`（warn），文案 key 在 `errors.json` 的 `preflight.*`
  与 `problems.title.*`（`test_i18n_dead_keys` 扫 `src/tavotto` 与插件目录）。
- **桌面的按规范修图（ADR 0080，`engine/specfix.py`，纯标准库）**是同一套事务的
  第二个入口（`/api/engine/specfix`）：**哪里违规只认 `preflight.run()` 的 gid**（逐 gid
  展开后按 `(规则, gid)` 配对，`element-outside-figure` 交给逐元素的几何清单），
  改成多少由 `plan()` 按**页面 pt** 算再按面板缩放换回（字号按角色层级抬升：抬了
  下层就把上层补齐，不倒挂），裁决 = `normalize.compare()` + 点名的问题真的不见了
  + **修复引入的 warn 级规范问题也挡**（`STRICT_SEVERITIES`；规范化那边只挡 error）。
  允许集合按 prop 放行全图（刻度组 / 图例的字号字体会落到子元素上），外加
  `COUPLED_PROPS` 登记的实测连带（`spine_linewidth` → 四边线宽、`linewidth` → 跟随源
  的图例示意线）；新增一类修复前先在真实渲染里看它连带改了什么再登记。「收不收这
  一轮局部修复」只有 `normalize.better_candidate()` 一处（bridge 与桌面共用）。
  **只有每一次渲染都干净的事务才算回滚成功**：任何一次带 warning 或抛了，响应带
  `replay_required`（前端按此刻的列表重放），worker 作废（`app._retire_hot_worker`
  → `pool.invalidate`，`worker_retired`）。**native 图不修**：端点在任何渲染之前回 409
  `specfix_native_unsupported`——作废这条兜底对用户自己的进程不成立（ADR 0080）。判据先看
  描述符存的档案（`profile_of`）再解析会话；事务途中每次渲染前、提交前各再比一次，变成 native
  就中止（`_require_route_unchanged`）；事务里每一处解析 worker 都带 `safe_only=True`（入口 +
  `_engine_attempt` 的依赖重试），拿到 native 会话在调它之前就拒（AST 守卫钉着）。
- **还原失败不遗忘（Codex #549 第八轮 P1）**：`apply()` 撤掉一条 override 时还原抛了，
  这个键**不销账**——applied / originals / alias_seeded 原样留着，记进 `FigState.unrestored`；
  下一次 apply 自动重试，欠着一天每次都报 `还原失败` warning（写回遇 warning 即阻断），
  还原成功、或同一个键重新被成功应用（不走「值没变就跳过」，originals 仍是脚本原样）才清账。
  别名组：广播端欠着账时组员的代采原样不回收；半路抛的还原照样标脏几何与别名组。
  `overrides.snapshot()` 是「会话此刻是哪份列表」的唯一出处，**不含**欠账的键（状态中立预览
  的收尾、native 屏障离开时保存的列表都读它，含了就会把用户撤掉的改动重新应用回去）。
  旧实现无条件 pop，图永久停在半改状态且此后再无 warning——safe 能靠作废 worker 兜，native
  会话（用户自己的 Python）兜不了。看护 `tests/test_restore_failure_retry.py`（含热态 == 冷
  启动重放的逐字节不变量；把 `continue` 改回落到 pop，五条全红）。
- **应用顺序规范化 + figure 锚定 prop 的重放（2026-08-17，数据损坏级）**：
  `overrides.apply` 按**七档规范顺序**应用（`_apply_rank` 是唯一出处）：
  图幅 size_mm → 色条方向 → 色条 extend → 子图 position → 刻度类型
  （set_[xy]scale 会把 locator/formatter 整套换掉）→ 其余（列表序）→
  刻度定位模型 → 单条刻度文字（冻结整条轴，必须最后）。色条方向必须先于
  extend：方向要拿色条**当前**的矩形反解厚度与间距。跨档的先后不是
  口味问题：顺序一乱，同一组 patch 在热会话与全量重放里会落成两张图。
  刻度类的 prop 还必须**每次都重放**（`_must_replay`）——它们按当前状态重算，
  而 applied 表里的值一个字节没变，走「值没变就跳过」的捷径就会停在旧刻度上；
  pos_frac / loc_frac / endpoints_frac 的 setter 在应用那一刻把 figure 分数换算进
  artist 本地坐标（独立形状的 `pos_frac` 换算成叠在 transform 上的平移，同一档），**几何一变（含还原）必须重放它们**——「几何」的判据（`_is_geometry_key`）是**排在 figure 锚定 prop 之前的那几档**：图幅、色条方向 / extend、子图 position，以及 `[xy]scale`（它钉在第 4 档，热会话先拖后换 log 轴时不重放就随对数轴漂走，全量重放却落在声明处；xlim / invert 与 pos_frac 同档按列表序走，不分歧）。figure 分数 ↔ display 的换算只走 `pathgeom.frac_to_display`，它一律按**根 Figure** 算（`root_figure`）：SubFigure 里的 artist `get_figure()` 回的是子图幅，拿它的 bbox 换算会把右半边的目标落到一半处，否则热会话状态 ≠ 全量
  重放——用户「写回时的样子」重开后全体文字错位（FigS3 事故，
  test_frac_anchored_props_survive_geometry_moves 看护）。新增 figure 锚定
  prop 时记得加进 `_FRAC_ANCHORED`。aspect="equal" 的子图只有 draw 才
  apply_aspect，几何组应用完必须 `draw_without_rendering()` 刷新布局再应用
  其余 prop（套 `image_pixels_skipped`：这次 draw 只为布局，图片不重采样）。事故期间保存的旧文档用 `scripts/recover_frac_positions.py`
  修复（从写回 PDF 的文字层反推真实位置，输出另存 + POST 成布局版本）。
- **持久 tight 布局下的子图位置（ADR 0042，issue #162）**：
  `layout="tight"` / `tight_layout=True` 会挂一个每次绘制都重算落位的
  `TightLayoutEngine`。落第一条 `axes.position` 时 `_set_axes_position` 把它换成
  `overrides.PinnedTightLayoutEngine`：**被 override 过的轴钉住，其余照旧自动
  排版**。**安装点只有这一个**（热态与重放共用的同一条路，不是两边各调一次），
  接管的是**原件实例**（包起来委派，不是按 `get()` 重建——用户自己的
  `TightLayoutEngine` 子类会被重建静默丢掉）。setter 里 `set_position` 必须排在
  换引擎 / 落 pin **之前**：反过来写时坏 bounds 会留在引擎里，而 `Figure.draw`
  只吞 `ValueError`，`Bbox.from_bounds()` 抛的 `TypeError` 会让这张图再也画不出来。
  `execute()` 的顺序是「先把被 pin 的轴放回 gridspec 格子 → 让 tight
  照常算 → 再盖回 pin」——**第一步不能省**：`get_tight_layout_figure` 拿
  gridspec 格子当 ax_bbox、拿当前 tight bbox 算边距，被 pin 的轴离开格子之后
  这个差就不再是「装饰物探出去多少」，实测 10 次绘制不收敛、且热态与重放收敛到
  两个结果。撤销必须 `unpin`（`_RESTORE` 里那条），否则 axes 被永久钉在「脚本
  原样」那组算出来的数上。**上游性质**：零 override 的 tight 图连画 14 次会出现
  4 种画面（飘的是 ylabel 落点），所以这类图上「两侧画的次数不同就不能比像素」。
  **与寄生轴（#217）不冲突**：布局引擎在 `Figure.draw` 最前面跑（钉住宿主），
  宿主的 `draw()` 随后把自己的 rect 推给寄生轴（寄生跟着走）；寄生轴自己的
  position 照旧是死开关（reason `parasite_host_rect`）。
- **拖过的文字（`("text", "pos_frac")`）落在写下的 figure 分数上（2026-09-25，QA GEO-B1 / GEO-B2）**：
  ① 注释文字的换算按它自己的 `anncoords` **现算**（`_get_xy_transform`），不许用 `get_transform()`——
  那是上一次 draw 冻下来的快照，预览 SVG 按 72 dpi 画、manifest 按 figure dpi 画，拿快照逆算会按
  dpi 之比落错。'pixels' / 'fontsize' / 可调用 / Artist 坐标系的注释**不宣称可拖**，manifest 与
  setter 共用 `annotation_text_draggable` 一份判据（硬写一条 → warning，不静默落错）。
  ② 有布局引擎（constrained / compressed / tight）的图：setter 把拖过的文字登记进根 Figure 的
  `_mm_text_pins`，并给**当前引擎实例**的 `execute` 包一层（`_ensure_text_pin_hook`，幂等）——
  排版前把登记的文字放回脚本原样（自动定位照开），引擎照常算，排完再按新的子图框落回写下的
  分数。与 `PinnedTightLayoutEngine` 同一条理由：**布局的输入必须与「没拖过」逐位相同**，否则拖一个
  标题会让子图跳、文字又被跳走的子图带走（y 分量等于没写），热态与重放也不收敛到同一张图。
  撤销（`_restore_text_pos`）同时摘掉登记。没有布局引擎的图不装这层。看护
  `tests/test_text_drag_anchor.py`。上游性质（与拖动无关）：**没拖过的** 'figure fraction' 注释
  在 constrained 图里就会让子图每画一次挪一次（钉在 figure 上的注释进了布局，边距不收敛）。
- **文字背景框的显隐只由 `bbox_visible` 决定（2026-09-19，#412）**：`true` 显示；显式 `false`
  或不在列表里 = 脚本原样（脚本 `set_bbox` 过就显示，没有就不显示）。`bbox_facecolor` 等五条
  只改样式、**永不改显隐**——框还没有时现建一个不可见的（`_BBOX_CREATE` 带
  `visible=False`），样式写进去等开关来开。第一版「首次改任何背景属性即出现背景框」让同一份
  列表两条路两张图：热态撤掉 / 关掉开关时其它样式值没变被跳过、框留在隐藏，重放时样式的
  setter 把框建出来并露出来。前端早已按开关建模（`web/src/lib/textEffects.ts`）。采样器
  （`tests/support/overridesample.py`）里 `bbox_visible` 采成 True。看护
  `tests/test_text_bbox_visibility.py`、`test_invariants_engine.py::test_undoing_a_background_edit_removes_the_box_it_created`。
- **getter 回的必须是 setter 能还原的形式，而原样有时是一个模式不是一个值（#423）**：
  `Patch.get_edgecolor()` 回解析后的 RGBA，脚本原样却常是 `_original_edgecolor is None`
  （没设）。按值写回把「没设」换成「显式透明」，3.10 及以前还顺手把 `_hatch_color` 写成
  同一个值——撤销边色后加花纹，斜线透明；`set_fill(False)` 按原样重算轮廓，也画不出来。
  所以 Patch / bar / bar_series 的 `edgecolor` getter 回 `_PatchEdge`（原始设定 + ≤3.10 的
  花纹颜色快照），setter 认得它（3.11 起花纹颜色独立，快照留 None）；`facecolor` 同样回
  `_PatchFace`（`_original_facecolor`）——像素上看不出差别，但 manifest 按「开了会画的那个色」
  报面色（#427）之后，按值写回 fill 关着时那个 alpha 已清零的 RGBA 会让撤销前后读到两个值。
  同族先例：
  `_AUTOSCALE`（自动缩放）、`_NO_BBOX`（没有框）、`_get_coll_edgecolor`（映射通道）。
  哨兵只活在 `originals` 里，不进 patch、不过 JSON。看护 `tests/test_patch_edgecolor_mode.py`。
- **override 的目标身份（2026-09-25，ADR 0083，QA SCI-03-B1）**：gid 是位置式的，脚本
  重排 / 插入 / 删除曲线或子图之后同一个 gid 指向别的对象。patch 可带 `identity`（写编辑
  那一刻 manifest 里这个 gid 的身份，前端提交时抄写）；`apply` 建 `new` 表时比对
  `state.identity`：对不上（含此刻没有身份、带了却不是非空串）的那条**当作不在列表里**
  （上次应用过的照常还原）并报「编辑的对象已找不到（脚本结构可能已改动，未应用）: gid.prop」
  ——写回一条 warning 即阻断；gid 整个不存在仍走「元素不存在」；不带 `identity` 按位置
  匹配（旧文档、跨图同步），不认识的方案前缀不核对。身份唯一出处
  `overrides.artist_identity`：**只取脚本显式起的 label**（`_` 开头与空串不算）的摘要
  `l1:` + 16 位十六进制，不取类型 / 数据 / 父 axes / 文字内容（取舍表见 ADR）；在
  **baseline 那一刻**由 `manifest._register` 采进 `FigState.identity`（`instrument` 清表重采，
  label 是可编辑 prop，编辑之后再采就采成了用户的编辑），manifest 元素发 `identity`。
  看护 `tests/test_override_identity.py`。
- 坐标约定：manifest bbox/anchor 均为 figure 分数坐标、**y 向下**（top-origin）；
  worker 内部转 matplotlib 的 bottom-origin。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 七档顺序是契约
- 刻度类与 frac 锚定 prop 每次重放
- `PinnedTightLayoutEngine` 安装点只有一个、`set_position` 排在换引擎之前
- `face` / `math_face` 唯一出处 `manifest.font_faces()`
- 文字背景框显隐只归 `bbox_visible`，样式不露框
- 原样是模式的 getter 回哨兵（`_AUTOSCALE` / `_NO_BBOX` / `_PatchEdge` / `_PatchFace`）
- 颜色字段 alpha 0 报 `NO_COLOR`
- 拖过的文字不进重排（`_ensure_text_pin_hook`）
