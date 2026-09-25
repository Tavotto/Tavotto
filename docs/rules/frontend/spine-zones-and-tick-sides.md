# 坐标轴边框的语义命中区与四边刻度（2026-09-02，ADR 0035）

> 原文出自 `web/AGENTS.md`「坐标轴边框的语义命中区与四边刻度（2026-09-02，ADR 0035）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

完整版在 `docs/adr/0035-axis-tick-direct-manipulation.md`，改动前先读。

* **点内侧控向内、点外侧控向外、线本身选中子图**。命中函数是纯的
  `lib/tickSides.spineZoneAt`：边框线端点来自 manifest 的 `spines`（引擎按画出来
  的那条线给，**含偏出去的边框**），带宽按**屏幕像素**定（`ZONE_PX` /
  `ZONE_PX_TOUCH`，调用方传「一个分数单位 = 几个屏幕像素」= 面板内容边长 ×
  zoom），旋转由 `ElementHitLayer.frac` 反旋转——命中函数不知道 zoom 与旋转。
  高亮条用同一把尺（`zoneRectFrac`），与命中带逐像素重合。
* **优先级**：`pickElement` 命中文字 / 曲线 / 别的子图 / 刻度文字时边框命中区
  让路，只有命中 figure 或那条边所属的子图本身（含铺满它的位图）才算；resize
  手柄在 OverlaySvg 层天然在上。角落并列：更近的边 > 此刻画着刻度的边 >
  固定次序（下、左、上、右）；twinx / secondary 与宿主重合的边同一条规则。
* **状态是派生的**：matplotlib 的 `direction` 是整条轴的，`ticks_<side>` 是边的，
  `inward = 边可见 && 方向含 in`。**三处同源**——画布命中区、示意图
  （`TickAndSpineDiagram` 的内 / 外两带）、刻度卡（方向四档）都读
  `readAxesTickModel`、走 `toggleSidePlan` / `axisChoicePlan` / `sideVisiblePlan`、
  经 `store/actions.applyTickSidePlan` **一次 commit**（方向落刻度元素、显隐落子图，
  拆开会渲染出一帧半新半旧）。计划的 `effect.coupled` 是「方向那一步连带改到的
  同轴另一边」——hover 文字、示意图 tooltip 必须说出来，不装作每边独立。
* 「隐藏」是四档里的派生态（两边都不显示），不是第四个真值；从它选回方向时
  **删**两边的 `ticks_<side>` override 回到脚本的边，不猜。
* **每组设置只有一处控件**（审计 T13）：「在哪几条边显示」只在示意图上点
  （内 / 外两带，键盘可达），刻度卡不再摆第二排「显示边」开关；改过的边由
  图上那条边自己标（accent + tooltip），恢复只有一个动作（`resetAll`，一条
  历史），不逐边出 chip。刻度组页分「刻度 / 文字」两段（`TickPage`），X / Y /
  Z 同一套；次刻度关着时从属字段按展示注册表的 `visibleWhen` 收起——刻度卡
  与通用列表共用 `registry.fieldVisible` 这一条判据，不各写一套。
* **不支持就不摆**：manifest 没有 `spines`（极坐标 / 3D / 色条轴）画布无命中区；
  引擎没发某条轴的刻度元素时那两条边方向未知，示意图退回单个 `ticks_<side>`
  开关。刻度卡承接 `minor_length` / `minor_width`（`length` / `width` 只动主刻度），
  方向档带 `data-prop="direction"` 锚点供问题面板定位。
* 看护：`lib/tickSides.test.ts`（几何 + 映射全状态扫描）、`canvas/spineZones.test.tsx`
  （命中层：hover / 点击 / 优先级 / zoom / 触控 / 旋转 / 偏出去的边框）、
  `inspector/tickTaskCard.test.tsx`（示意图两带 + 四档 + 显示边 + 锚点）。
* **e2e 里「点图内空白」的点必须避开这条带**（2026-09-13，#337 posix-e2e 真红）：
  带按屏幕像素定宽，图显示得越小它盖住绘图区的份额越大；「适应画布」成为模式
  之后快速编辑里的图按侧栏展开后的舞台适配、比此前小了一截，
  `e2e/element-path-selection.spec.ts` 原先在 marker 包围盒里搜出来的「空白点」
  就落进了下边框的带里——那一下是切刻度、选区照旧，60 段子路径原样留在覆盖层。
  空白点现在由 `blankSpot` 统一挑：离 marker / 曲线最远**且**离边框
  > `ZONE_PX.band` + 4px（e2e 的 tsconfig 不解析 `@/`，常量照抄一份并点名出处；
  产品把带加宽只会让它红，不会假绿）、**且**不压着别的元素的 bbox——那一项按
  render 响应里 manifest 的 bbox 算（图例的 bbox 含看不见的边框内边距，比 SVG 上画
  出来的大好几个像素），曲线那条原先猜的「离曲线最远的 bbox 角」正是图例，bbox
  命中的变异照样绿。墨迹与边框仍从 SVG 的 DOM 上量，与命中层两把尺子。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 命中函数纯、带宽按屏幕像素
- 状态派生自 `direction` + `ticks_<side>`，三处同源
- e2e 的「空白点」由 `blankSpot` 统一挑
