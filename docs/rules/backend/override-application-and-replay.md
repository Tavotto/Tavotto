# override 语义、应用顺序与全量重放

> 原文出自 `src/tavotto/AGENTS.md`「渲染引擎核心机制」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`src/tavotto/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_invariants_engine.py`、`test_override_identity.py`、`test_equivalence_matrix.py`、`test_layout_engine_pinning.py`、`test_normalize.py`、`test_specfix*.py`、`test_value_original_pair.py`；其余看护用例见细则
> `src/tavotto/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：override 是全量列表；七档顺序是契约；刻度类与 frac 锚定 prop 每次重放；带 `identity` 的 patch 对不上目标身份就不应用（ADR 0083）

<!-- agents-route
sheet: src/tavotto/AGENTS.md
order: 70
改到: `engine/overrides.py` 的 `apply` / `_apply_rank` / `_FRAC_ANCHORED`、tight 布局、字体脸、`normalize.py` 三模块、`specfix.py`
主题: override 语义、应用顺序与全量重放 → `override-application-and-replay.md`
必守要点: override 是全量列表；七档顺序是契约；刻度类与 frac 锚定 prop 每次重放；带 `identity` 的 patch 对不上身份就不应用
-->

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
  `_engine_attempt` 的依赖重试），拿到 native 会话在调它之前就拒（AST 守卫钉着）。任何渲染留下的欠账（v1 render / export / preview_png 结果的
  `unrestored > 0`）在 native 上就是「与文档不一致」，挡编辑、导出与 continue
  （`native_figure_inconsistent`，见 `tavotto-run-control-plane.md`）。
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
  `overrides.apply` 按**七档规范顺序**应用（`_apply_rank` 是唯一出处；ADR 0098 在最前面加了
  一档 `figure.frame`（`_RANK_FRAME = -1`：图幅按 figsize 还是按脚本存盘的裁切框——之后每一档
  的尺寸与分数都以它为准；它这一轮换了，`_FRAME_RELATIVE` 里的 `size_mm` / `position` /
  三个 `*_frac` 值没变也重放）：
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
  单次被拦截保存的标准 tight 引擎另有首轮种子重放，范围与边界只见
  `figure-capture-and-execution.md`；它不替代本节的用户位置 pin。
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
  `tests/test_text_drag_anchor.py`。
  **同一张 pin 表的其它住户（ADR 0100，2026-09-27 拖动全族排查）**：锚定框 / AnnotationBbox
  （`("artist", "pos_frac")`）、图例（`loc_frac`，在 `legendmodel` 外包一层登记）、形状
  （`("patch", "pos_frac")`）、独立箭头（`endpoints_frac`）与 **constrained 图上的子图**
  （`position`）。放回原样 / 落回去各自怎么做只在 `_pin_put_native` / `_pin_place` 一处分派；
  子图先落、其余按**收过长宽比之后**的子图框换算（hook 里先替 draw 做一次 `apply_aspect`，
  否则带位图的子图上拖出去的图例差 4 mm）。constrained 的子图「原样」= 参与排版 + 色条轴
  被解开的长宽比——`set_position` 会把轴踢出排版，不放回的话拖 A、B 跳 7.8 mm，拖色条则
  `reposition_colorbar` 把长宽比按成 0、整张图画不出来。撤销各自摘掉登记。
- **落位靠定位器的轴（ADR 0100）**：`_set_axes_position` 把 axes locator 摘下来
  （`_release_axes_locator`；色条轴摘的是 `_ColorbarAxesLocator._orig_locator`），原定位器与
  `set_position` 顺手改掉的 `in_layout` 记在 artist 上，撤销（`_restore_axes_position`）放回。
  覆盖 `ax.inset_axes`、mpl_toolkits 的 `inset_axes`、`make_axes_locatable` / `ImageGrid`——
  修前后三者宣称可拖、松手弹回原处。次坐标轴仍锁（`position_locked`）。上游性质（与拖动无关）：**没拖过的** 'figure fraction' 注释
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
- **Collection 只改 RGB 时保留源颜色的逐元素 alpha**：matplotlib 的 `set_color` / `set_facecolor` /
  `set_edgecolor` 整体替换，`_alpha is None` 时把每段补成不透明——`LineCollection(colors=[逐段 RGBA])` 改一次色就全实心。
  `overrides._rgb_keeping_source_alpha` 是 2D Collection 面 / 边色（`_set_coll_color`）与线组 `color` 的唯一入口：`v` 不带
  alpha（`_rgb_only`）且源设定（`_original_*`）有不全为 1 的 alpha 列时，只换 RGB、留 alpha 列；Artist 全局 alpha（`_alpha`）
  不碰（改 alpha 滑块覆盖颜色 alpha，清掉后逐元素透明度回来，matplotlib 语义）；用户显式给的 alpha 原样用；`alpha=数组`、
  3D 集合原样透传。线组 `color` 的 getter 回灌 `_original_edgecolor`（设定）而不是 `get_color()`（已被标量 alpha 抹平）。
  看护 `tests/test_collection_color_alpha.py`（原生对照、逐段 alpha 反推、改色→改 alpha→留 alpha→清空 冷热一致、同色独立
  marker / proxy / 自定义 handler / 重复 label 不被改）。
- **会读环境重解析的 setter，调用期间把「未设」花纹色钉成创建时的值、跑完还原模式（#874）**：matplotlib ≥3.11 的
  `Patch.set_alpha` / `Patch.set_fill` / `Collection.set_alpha` 把 `_original_hatchcolor is None` 的花纹色
  按**此刻**的 `rcParams['hatch.color']` 重解析。脚本在 `rc_context` / `style.context` 里改过 `hatch.color`
  时，创建时的 red / `'edge'` / 半透明 RGBA 被环境值盖掉，撤销回不去——编辑那一刻热态与重放一致，分叉只在
  撤销之后（像素不同、manifest 相同、warnings 为空，写回 verify 报 409 `replay_divergence`）。
  `overrides._with_hatch_pinned` 包住 alpha / fill setter：动手**之前**`set_hatchcolor(创建时已解析的值)`，
  跑完 setter **立刻把 `_original_hatchcolor` 还原成 None**（只还原模式，`_hatch_color` 留着按当前 alpha 合成的值，
  不重解析 rc）。所以钉只活在 setter 调用期间：native 屏障释放 `apply([])`、safe worker 撤空、编辑进行中，
  任何时刻这个 artist 的模式都与没被 Tavotto 编辑过的一样，用户恢复运行的 Python 再改 rc 并 `set_alpha` 照常跟随
  新 rc（Codex #950 r4245786615；还原挂在 setter 里而不是某条 restore 上，因为 restore 本来就走同一个 setter）。
  还原只在跑完后模式**仍是我们钉进去的那个对象**时才做（钉的是独有副本、身份比较）：用户子类若重写
  `set_alpha` / `set_fill` 并自己调 `set_hatchcolor`，那是它有意装的模式，保留；setter 抛异常时同样（Codex #950
  r4246080546；`tests/test_hatch_mode_restored.py` 子类用例）。
  创建时的值另记在 artist 的 `_mm_hatch_base = (创建时的值, 我们上次写完的样子)`：撤销要找回 rc 里的内在 alpha，
  当下的值已被 alpha 编辑合成过；第二项对不上说明屏障之间用户代码动过，按实况重记。脚本显式给过 hatchcolor 的
  artist 原样跑。≤3.10 没有重解析，但 `set_fill(False)` 把 `_hatch_color` 改成边色而
  `set_fill(True)` 不写回：`_set_patch_fill` 用记在 artist 上的创建时值（`_ORIG_HATCH`，边色 getter 也采）
  在 `_original_edgecolor is None` 时写回。看护 `tests/test_override_sequences.py::test_rc_context_hatch_*`、
  `tests/test_hatch_rc_context_writeback.py` 与 `tests/test_hatch_mode_restored.py`。
  **消费方清单（#956，共享判据修一处不算修完）**：凡作用于可能带花纹的 Patch / Collection 的 alpha / fill 入口，
  一律经 `_set_alpha_pinned` / `_set_patch_fill`，不得直接 `set_alpha` / `set_fill`——柱 / 矩形 / fill_between /
  scatter（`_CAP_ALPHA`，#950）、箭头 patch（`arrowpatch.alpha`）、**文本背景框 `text.bbox_alpha`**、**图例框
  `legend.framealpha`**（legendmodel 是叶子、不 import overrides，由 overrides 导入时 `install_alpha_setter` 接线，
  没接线就抛而不是退回裸调用）、**线集合 `linecoll.alpha`**。不钉的直接调用：Text（`text.alpha`、图例文字 / 标题）、AxesImage、Line2D 没有花纹色；
  误差线 / 茎线系列 alpha 的 LineCollection 成员会重解析，但它们是两点线段、没有可填充面积，花纹画不出来
  （3.11.2 / 3.10.9 实测前后像素逐字节相同），无可观测分叉，故不钉（将来若支持多点线集合的系列，要回头补）。
  登记清单在 `tests/test_hatch_alpha_consumers.py`（AST 扫 overrides /
  legendmodel 的每一处 `set_alpha(` / `set_fill(`，新增一处会红）；行为看护在 `tests/support/rc_hatch_text_legend_figure.py`
  那张图上的 `test_override_sequences.py::test_rc_context_hatch_text_legend_*`、写回 `test_hatch_rc_context_writeback.py`
  与 `test_hatch_mode_restored.py`。
- **`hatchcolor` 可编辑：getter 回「模式」不回值，setter 复用钉住（#874 第二步）**：只在有 `set_hatchcolor` 的 artist 上生效（≤3.10 getter 回 None、
  setter 空操作）。getter 的两条分支——显式模式（`_original_hatchcolor` 非 None）回它的副本（RGBA / 颜色名 / `'edge'`，setter
  吃得回去）；「脚本没设」模式回 `_HatchUnset(base)` 哨兵（`base` = `_hatch_base`，创建时已解析的花纹色，与 `_with_hatch_pinned`
  共用一份判据）。**不能回解析后的当前值**：按值写回会把模式永久换成显式，native 屏障释放后用户的 Python 改 rc 再 `set_alpha`
  就不再跟随（与 `_PatchEdge` 同一个坑，Codex #950 r4245786615）；撤销到哨兵时 `_restore_hatch_unset` 用 `base` 配**当前** alpha
  重新合成，再把 `_original_hatchcolor` 还原成 None。setter 设显式值是直接 `set_hatchcolor(颜色)`（不读 rc，不需要钉住；包进 `_with_hatch_pinned` 做过变异，是等价变异，故不留死分支）。与 alpha / fill 的应用顺序无关
  （先 alpha：钉住 → 收尾还原 None → 再显式设；先 hatchcolor：模式已显式，alpha 的钉住见显式模式原样放行，花纹色以显式色配当前 alpha）。
  哨兵只活在 `originals` 里，不过 JSON。`bar_series` 照 `hatch` 加进 `for _bprop` 一串。**柱系列代采原样（#968，已修）**：系列代采每根柱的「原样」时，若该柱在单柱层已有 `originals`（单柱先改过），取那一份脚本原样而不是此刻的热态——否则全撤时系列的还原把单柱的编辑写回。`ALIAS_GROUPS[("bar_series", *)]` 带 `_member_aligned` 标记（成员序与 getter 列表序一致），`apply()` 采原样那段据此替换，对 `_bprop` 整串通用（hatch / hatch_linewidth / hatchcolor / 颜色 / 线宽…）。看护 `test_single_and_series_undo_orders_match_replay_and_return_to_the_script`。
  看护 `tests/test_hatchcolor_editing.py`、`tests/test_hatch_mode_restored.py`、`tests/test_override_sequences.py`（采样器 `_ENABLERS["hatchcolor"]`）。
- **`value_original`：override 之前脚本的值，与 `value` 同一口径（ADR 0081 §十三，2026-09-25）**。
  前端拿它判「脚本重跑后改没改这一项」：样式写的 override 在脚本改过的那一项上让位（用户裁决：
  重跑后脚本赢）。`originals` 存的是 getter 的原始对象（元组 / Colormap / 哨兵），前端没法拿它与
  manifest 的 `value` 比，所以另记一份 `state.original_values`：
  * **读法就是 manifest 的**（`manifest._field_values` → `_fields_for`），由 manifest 在 import 时登记
    （`overrides.set_original_reader`，反过来 import 会成环）；没登记就不记。
  * **在这一轮任何 setter 动手之前**，把这一轮要第一次采样的元素全读一遍：元素之间有联动（色条外框
    就是色条轴的边框，同一轮先改 `spine_linewidth` 的话外框读到的是改后的值——实测踩到过）。
    广播代采的组员（`alias_seeded`）同样记；借来的原样（对等广播端 / 组员的 `_seeded`）连 `value_original`
    一起借对方那一份——此刻自己的实况已经被对方改过，读不得；不借的话热会话与全新重放报出两份不同的
    manifest（`test_invariants_engine.py::test_hot_equals_replay_after_removal` 的色条共用组当场红过）。
  * 与 `originals` **同生同灭**：还原 / 广播退场时一起删；rebase（native 屏障、脚本重跑）清空后按
    新的脚本值重采。
  * 只在 `state.applied` 有这一条时发（与 `marker_original` 同一判据）；采不到就缺席 = 不知道，前端按
    保守路径走（不让位）。`marker_original` / `cmap_original` 不并进来：那两个是形状 / 色图的只读事实。
  * 键名与前端严格同源（`manifest.VALUE_ORIGINAL_KEY` ↔ `EditableField.value_original`）。
  看护 `tests/test_manifest_value_original.py`（样式能写的每一项全写 / 逐条写各一遍、rebase 重采、没登记
  读法时缺席）、`tests/test_value_original_pair.py`、
  `tests/native/test_native_barrier_semantics.py::test_rebase_reports_the_new_script_value_as_value_original`。
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
  已应用条目带来的身份记在 `FigState.applied_identity`，`FigSession.snapshot()` 原样交出——native
  屏障离开时存的就是这份快照，丢了身份，下一个屏障 rebase 又按位置落到别的对象上（#602 评审）。
  看护 `tests/test_override_identity.py`、`tests/native/test_native_barrier_semantics.py::test_target_identity_survives_the_barrier_snapshot`。
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

速查表看护列精简时挪出的用例（仍是本主题的看护）：`tests/test_text_bbox_visibility.py`、`test_patch_edgecolor_mode.py`、`test_text_drag_anchor.py`、`test_drag_coverage.py`、`test_scientific_text_matrix.py`、`test_restore_failure_retry.py`、`test_manifest_value_original.py`。
