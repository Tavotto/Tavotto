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
  PyMuPDF 入口）。`preflight.element_overflow()` 是 `element-outside-figure` 的逐元素
  判据，`_check_panel_clipping` 与 B0 对比共用它——改判据只改这一处。三条干涉检查的
  severity 登记在 `publication.json`（warn），文案 key 在 `errors.json` 的 `preflight.*`
  与 `problems.title.*`（`test_i18n_dead_keys` 扫 `src/tavotto` 与插件目录）。
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
  artist 本地坐标（独立形状的 `pos_frac` 换算成叠在 transform 上的平移，同一档），**几何一变（含还原）必须重放它们**——「几何」的判据（`_is_geometry_key`）是**排在 figure 锚定 prop 之前的那几档**：图幅、色条方向 / extend、子图 position，以及 `[xy]scale`（它钉在第 4 档，热会话先拖后换 log 轴时不重放就随对数轴漂走，全量重放却落在声明处；xlim / invert 与 pos_frac 同档按列表序走，不分歧），否则热会话状态 ≠ 全量
  重放——用户「写回时的样子」重开后全体文字错位（FigS3 事故，
  test_frac_anchored_props_survive_geometry_moves 看护）。新增 figure 锚定
  prop 时记得加进 `_FRAC_ANCHORED`。aspect="equal" 的子图只有 draw 才
  apply_aspect，几何组应用完必须 `draw_without_rendering()` 刷新布局再应用
  其余 prop。事故期间保存的旧文档用 `scripts/recover_frac_positions.py`
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
- 坐标约定：manifest bbox/anchor 均为 figure 分数坐标、**y 向下**（top-origin）；
  worker 内部转 matplotlib 的 bottom-origin。
