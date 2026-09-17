# 色条：方向、延伸与大小

> 原文出自 `src/tavotto/AGENTS.md`「渲染引擎核心机制」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **色条方向（2026-08-18）**：**就地**结构改造，不是普通 setter，也不是销毁
  重建。`overrides._cb_reorient` 在同一个 Axes 对象上换 orientation/ticklocation
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
