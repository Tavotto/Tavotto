# 刻度定位、边框模型与 spines 几何

> 原文出自 `src/tavotto/AGENTS.md`「渲染引擎核心机制」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **刻度定位走 Locator / Formatter，不是改已经生成出来的 Text**（2026-08-18）：
  刻度标签每次 draw 由 locator 现算、Text 对象现建，改 Text 属性只能靠
  tick_params 持久（字号/颜色/朝向那一档），而「几个刻度、落在哪、写成什么」
  只有 locator 与 formatter 说了算。模型存在**轴对象**上
  （`axis._mm_tick_cfg`，`tickmodel.tick_cfg` / `apply_tick_model` 是唯一出处；整个刻度族——
  `TickSet` / `TickLabel` 伪元素、四边开关、Locator / Formatter 模型、单条文字冻结、
  `ticklabel_memo` 记忆表——2026-09-18 起住在 `engine/tickmodel.py`，`overrides` 只展开它的
  `HANDLERS_*` / `RESTORE` 表、`manifest` 直接从它取只读判据）：
  major_mode(auto|step|fixed) / major_step / major_values / minor_visible /
  minor_mode / minor_step / format / minor_format。次刻度的格式多一档 "none"
  （不标数字）——**那才是默认**；开了之后 `TickSet.labels` 把次刻度标签也算进
  刻度组，否则那一排点不中、对齐也对不准。**没表态 = 用脚本原样**，不是我们另挑一个
  AutoLocator（对数轴的 LogLocator 换成 AutoLocator 就是把用户的图改了）；
  setter 一律写进 cfg 再**整体重建**，所以 prop 之间的应用顺序不影响结果；
  `set_[xy]scale` 之后必须 `invalidate_tick_cfg` 重采「脚本原样」。
  单条刻度文字（`ticklabel.text`）冻结整条轴（FixedLocator + FixedFormatter），
  身份是**序号**：冻结前先回模型态、再把该轴上全部仍在生效的编辑一起盖上，
  序号越界就**抛异常**（→ warning → 写回阻断），绝不静默返回。刻度伪元素
  每次 `build_manifest` 按当前状态重登记（`manifest.sync_tick_elements`），
  `FigState.resolve` 还能按 gid 形状**现解**尚未登记的那些——「先改刻度定位、
  再改新出现的那条刻度」在全量重放里才不会报「元素不存在」。
- **边框模型（2026-08-18）**：与刻度模型同一套路数（写进 cfg 再**整体重建**，
  `spine_cfg` / `apply_spine_model` 是唯一出处）。一档「全部」（`spine_color` /
  `spine_linewidth`，作用于 `ax.spines` 的**每一条**，含色条轴的 'outline'）+
  四条各自可覆盖（`spine_<side>_color` / `spine_<side>_linewidth`）。优先级：
  自己的设定 > 「全部」 > 脚本原样；撤销一条 = 退回未表态（落回上一档），
  不是把当前推断出来的值钉死。**为什么要模型化**：「全部灰色」与「上边红色」
  是两条会互相盖写的 setter，直接改的话谁先谁后就是两张图——而 patch 列表序
  在热会话与全量重放之间并不保证同序。
- **边框线几何 `spines` 与主 / 次刻度分档（2026-09-02，ADR 0035）**：直角坐标轴
  （`ax.name == "rectilinear"` 且四条命名边框齐全）的 axes 元素带 `spines`：每边
  `visible`（边框线本身）/ `ticks`（这一侧主刻度线）/ `from` / `to`（figure 分数、
  y 向下），端点取 `Spine.get_path()` 经它自己的 transform——**含**
  `set_position(("outward", n))` 的偏移；**不能用 `get_window_extent`**（它把刻度
  伸出量算进去了）。极坐标 / 3D / 色条轴不给；拥有这一边的 axis 不可见
  （twinx 的第二个 axes 关掉的 x 轴）或线退化成一点（`secondary_xaxis` 的左右）
  不出。唯一出处 `manifest.spine_geometry`，渲染派生数据、不进文档。
  刻度组元素的 `length` / `width` 只动主刻度（`tick_params(which="major")`，与
  matplotlib 默认同口径），新增 `minor_length` / `minor_width` 只动次刻度（getter
  三级真值链：Tick 对象 → `_minor_tick_kw` → rcParams，次刻度没开也有值）；
  `direction` / 颜色 / 字号仍 which="both"。看护 `tests/test_tick_sides_geometry.py`。
