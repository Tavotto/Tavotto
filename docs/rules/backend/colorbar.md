# 色条：方向、延伸与大小

> 原文出自 `src/tavotto/AGENTS.md`「渲染引擎核心机制」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **色条方向（2026-08-18）**：**就地**结构改造，不是普通 setter，也不是销毁
  重建。`colorbarmodel._cb_reorient`（整个色条族——`ColorbarProxy`、方向 / 延伸、
  `_cb_release_aspect` / `_cb_restore_aspect`、`colorbar_maps` / `follow_map` / 随行表——
  2026-09-18 起住在 `engine/colorbarmodel.py`，`overrides` 只展开它的 `HANDLERS` / `RESTORE`
  并在 axes position 那两处留调用点）在同一个 Axes 对象上换 orientation/ticklocation
  → 按 `_cb_place` 重算落位（竖↔横逐位可逆）→ `_reset_locator_formatter_scale()`
  + `_draw_all()` 让 matplotlib 自己重建色带/outline/刻度/xlim,ylim → 把长轴标签
  搬到新长轴（**旧轴那份要清掉**）。`fig.axes` 顺序一个字节不动 → gid 稳定 →
  撤销 / 写回 / 重开全链路照旧。落位参照取 `state.pending` 里**这一次改完之后**
  的宿主 position（只看实况的话，热会话与全量重放会算出两个位置）；用户自己
  摆过色条轴时不动它的落位，交给 position override。翻完要 `invalidate_tick_cfg`
  （locator 被整套换过）并重算 `axes_follow`。色条另有**稳定语义身份**
  `cbar:<宿主 gid>:<序号>`（manifest 的 `colorbar_key`），与 `axes_i.colorbar`
  一起登记在 index 里。manifest 还报 **`mappable_gid`**（「我给谁上色」）：
  由 `cb.mappable` 在 **`state.elements`** 里反查得来——**不是** `state.index`，
  index 里还有容器消费掉的成员别名，那些 gid 指着同一个 artist 却不在元素表
  里，界面按它 find 会扑空。脚本自己造的 `ScalarMappable` 没有登记成元素时
  **整条字段不发**（界面据此不摆那个「选中对方」的入口，见
  `web/src/components/inspector/ColorScaleLink.tsx`）。
  **两端延伸三角 `extend`**（neither/both/min/max）同样是就地结构改造，两个坑：
  ① `cb._inside` 是按 extend 切出来的那段 boundaries，**只在 `__init__` 里设过
  一次**——只改 `cb.extend` 就 `_draw_all()` 会拿 259 条边界配 256 块颜色，当场
  TypeError，两者必须一起改；② 落位其实由 matplotlib 自己的
  `_ColorbarAxesLocator` 每帧从 `get_position(original=True)` 重算，它顺手把
  `box_aspect` 改成 `aspect*shrink`，却在 extend=='neither' 时**提前 return
  不收回去**——不管这一点的话「开了又关」的色条比从没开过的宽 10%，而且回不去。
  修法是每次改 extend 前把 box_aspect 放回基线（基线在 `ColorbarProxy.__init__`
  即 instrument 时采，那一刻才是脚本原样），做完的落位与原生
  `fig.colorbar(..., extend=…)` **逐位相同**（用例是这么断言的）。翻转之后
  `_colorbar_info['aspect']=False`：落位归我们，locator 不能再按 aspect 反推厚度。
  **色条轴上的 patch 一律不登记成可编辑形状**——延伸三角就是 PathPatch，而且每次
  `_draw_all()` 都被删掉重建。看护 `tests/test_colorbar_orientation.py`。
  **色条的大小与长度（2026-09-13，用户反馈「色条拖不动」）**：色条伪元素 manifest 上
  `resizable` + `geom_gid` 指向它的轴（`axes_i`，与位图代理宿主同一机制；色条轴
  `position_locked` 时不宣称，两处判据同源），几何写的是色条轴的 `position`。
  `fig.colorbar(im, ax=ax)` 的轴带 `box_aspect=20`，`set_position` 给多宽都被
  `apply_aspect` 按回高度的 1/20——所以 `_set_axes_position` 落到色条轴时
  `_cb_release_aspect`：box_aspect 清掉、`_mm_box_aspect0` 基线清掉、
  `_colorbar_info['aspect']` 关掉（与 `_cb_reorient`「落位从此归我们」同一处置），
  三个值只在第一次记进 `_mm_cb_aspect_stash`，撤销 position 时 `_cb_restore_aspect`
  放回。`("axes","position")` 的脚本原样记 **`get_position(original=True)`** 而不是
  active：aspect 约束的轴（`aspect="equal"` 子图、带 box_aspect 的色条轴）active 是
  每次 draw 从 original 现算的结果，回灌成 original 之后再改图幅会与全新重放分岔
  （实测 6×4 改 4×6 色条轴高度 0.77 → 0.34）。看护 `tests/test_colorbar_resize.py`
  （热会话 vs 全新重放逐位相同、撤销后再改图幅仍一致）。
  **色阶兄弟（2026-09-21，用户的 PRB 三联图：从色条换色图，紧挨着色条的那块网格
  纹丝不动）**：脚本把**同一个 norm 对象**交给几块 `pcolormesh` / `imshow`、只挂
  一条色条，就是在声明它们是同一个色阶——matplotlib 眼里 vmin / vmax 经共用的 norm
  天然一起变，cmap 却各拿各的引用。判据是 norm 的**对象身份**（不是名字、不是数值
  相等），唯一出处 `colorbarmodel.scale_siblings(state, mappable)`（只在登记表里找，
  色条代理不算；**原样采不到的也不算**——`state.has_handler(a, "cmap")` 经 FollowState 协议问
  `HANDLERS`，没有数组、归线组族的 LineCollection 传了共用 norm 也不进组，它没在映射）。三处消费它：① 色条的 `cmap` setter 写到 mappable **和全部兄弟**
  （`_set_cb_cmap`，带 state），撤销 `_restore_cb_cmap` 让兄弟**各回各的原样**
  （别名组在色条动手之前替它们采的，采不到退回 mappable 的那份）；② 别名组
  `overrides._alias_colorbar_mappable` 把兄弟的 `(gid, cmap/vmin/vmax)` 一并算进组员
  ——vmin / vmax 不必逐个写（norm 是同一份），但兄弟的「脚本原样」必须在色条动过
  之前采下来；③ manifest 的色条条目发 `scale_gids`（兄弟的 gid，不含 mappable 本人，
  没有兄弟就不发；**只发此刻真在映射的**——组员按 family 定、一次会话里恒定，事实按
  `color_mapping_is_live` 说，有数组却写死颜色的线组是组员但不发；`scale_gids` 说的是**覆盖关系**，与色条自己的
  三个控件开不开闸（`colorbar_mapping_is_live`）是两个问题：闸关着时先前的 cmap override 仍在给
  兄弟上色，兄弟页「回到脚本原样」要靠这份关系找到它——所以照发；「摆不摆链接」由前端按色条
  有没有 cmap 字段判，见 `inspector-presentation-registry.md`），`_cmap_alias_gids` 也把兄弟算进「脚本原样记在谁名下」。前端
  （`lib/colormapAlias.ts` 的 `colorbarCovers`）据此把「与色条共用色阶」与「回到脚本
  原样」扩到整组——组里**任何一块**被哪条色条盖着，那条色条就在组里（两块各挂一条色条时
  A 的 override 落在 mesh_b 上，从 B 那边清不掉 A 就什么都不会变）。`cmap_original` 各说各的：
  判据是**自己名下**（色条 = 它的 mappable）有没有被采过原样——自己被 override，或一条盖着
  自己的色条广播在生效；兄弟自己的窄 override 不算证据（它从没改过我），「只改了兄弟」时
  不报，否则 A 的选择器会拿 B 的原样当 A 的、点回去清的却是 B。兄弟自己的 cmap override 仍**压过**色条（`_rank` 的组内次序：窄的
  排在广播之后）。`apply` 里「对等广播端共用一份原样」只看组员表的**第一个**（色条 → 它的
  mappable / 独立 mappable 的令牌），兄弟组员不参与——独立 mappable 的色条与登记网格共用
  norm 时，按同名去兄弟身上找会把别人的色图当成自己的原样（`J-standalone-*` 看护）。
  色条的原样**就是组员的原样**（resolver 上的 `_shared_value`：cmap 与它的 mappable 同值、
  vmin / vmax 与全部组员同值）：组员名下已有的记录优先复用，不读 getter——分两步「先改组员
  自己的、下一轮再改色条」时 getter 读到的是改过的实况，全撤会停在中间态（`K-*` 看护）。撤销时
  兄弟自己那条排在色条前面、先被还原并收走记录的，色条的 restore 不再碰它（记录不在 = 已在原样上）。看护 `tests/test_invariants_engine.py` 的 `H-shared-*`（含撤色条 /
  撤兄弟 / 全撤三种减法，像素 + 全量 manifest）、
  `test_worker_roundtrip.py::test_colorbar_colormap_reaches_every_mappable_sharing_its_norm`。
