# ADR 0167：渐变色带填充按「两端颜色」编辑

日期：2026-11-03 · 状态：**Accepted**（维护者 2026-11-03 裁决：做渐变取色；行样式、图例不联动、多色改两色三项按推荐；1.0 豁免已给）
相关：[0086 精确命中几何](0086-precise-hit-geometry-for-series-shapes-and-annotations.md)（#1688：被 `set_clip_path` 裁成峰形的色带按裁剪路径命中）、
`docs/rules/backend/axes-and-artist-families.md`（能力层）、`docs/rules/frontend/inspector-presentation-registry.md`（展示注册表）。

## 动机

科研图里的「渐变填充峰」常见写法（用户同门的 XPS 分峰图，Ti 2p / C 1s 四温度）：一个 `PolyCollection`，几百条**轴对齐**长条
逐条一个颜色、拼成一个方向的渐变，再 `set_clip_path(PathPatch(峰形))` 裁成峰。检查器把它当普通填充，只有一个「填充颜色」
——值是第一条的颜色，改它就把 256 条一起刷成同一个颜色，渐变没了。用户要的是「把这个峰换个颜色，渐变还在」。

## 裁决

### 一、认出来（引擎，`overrides.gradient_detect`）

满足全部条件才算渐变色带：`PolyCollection`、没在做颜色映射；≥ 8 条路径；每条都是轴对齐矩形；所有条**同宽**（横条叠成竖向渐变）
或**同高**（竖条排成横向渐变）、不是两者都同；逐条颜色真的在变。两列并排的小色块（图例样式）、`fill_between`、纯色、条数太少的
一律照旧出「填充颜色」。认出来就**不再出 facecolor**，改出一个字段：

```json
{"prop": "gradient", "type": "gradient", "group": "渐变填充",
 "value": {"stops": ["#起端", "#止端"], "direction": "vertical" | "horizontal"}}
```

起端 / 止端按**屏幕**定：竖向是上 / 下、横向是左 / 右（经 `get_transform()` 落到 display 再排，反转的坐标轴照样对）。

### 二、怎么改（`_gradient_apply`）

写入 `{"stops": [新起端, 新止端]}`。每一条在**原**两端连线上的位置 `s`（sRGB 投影，夹到 [0, 1]）不变，新颜色 =
新起端 + s ·（新止端 − 新起端）——原渐变的深浅节奏（不均匀的色标位置）原样保留。alpha 列原样。原始逐条颜色在第一次改之前
记在 artist 上（`_mm_gradient_base`），之后每次都从它算，热会话与全量重放逐位相同；override 的原样（还原值）是逐条颜色数组，
「恢复脚本原样」回到脚本那一份。基准与还原值取脚本给的**源颜色设定**（`_original_facecolor`，同 facecolor 能力），不是已和 Artist 级 alpha 合成过的 `get_facecolor()`——否则「透明度」滑块的值会被烤进基准，单独清掉 alpha 后热态 ≠ 重放。override 值同时记 `screen_start`（`"high"`/`"low"`：屏幕起端对应数据坐标大的/小的一头，manifest 随字段发出）：引擎按数据坐标顺序重配色，与 setter 运行时 y 轴是否反转无关（`invert_y` 与 `gradient` 同序，热态与重放时看到的变换会不同）；缺 `screen_start` 的旧形状才退回当时变换。三段以上的多色渐变改完是两色直线过渡（裁决：可接受，恢复可回）。

### 三、界面（`controls/GradientStopsField`）

填充角色首屏里与「填充颜色」同一个位置：一行「起端色块 · 渐变条 · 止端色块」。色块是普通 `ColorField`（同一套取色面板、
连续取色同一套事务），止端色块的面板向左展开（`popoverAlign="end"`）；渐变条只是预览。多选时两端写给全部成员。

### 四、不做的（本 ADR 首版）

- **图例色块不联动**：脚本自己用色带画的图例方块不是 matplotlib 图例，引擎不知道它对应哪个峰；首版改峰不改方块，方块单独
  选中照样能改「填充颜色」。按颜色配对、一起改另立 PR（作为本 ADR 的修订段）。
- `imshow` + 裁剪路径的渐变位图已有「渐变基色」（单色 → 白），不动。

## 后果

- 认出来的渐变峰在检查器里多一个新控件、少一个「填充颜色」；没认出来的填充一个字节不变。
- 脚本一个字不改：改动只是一条 override（`gradient`），写回事务照常全量重放校验。
- 看护：`tests/test_gradient_fill_stops.py`（认得出 / 只认它、屏幕方向、改两端、撤销回原样、热态 == 全新重放）、
  `web/src/components/inspector/controls/gradientStopsField.test.tsx`（命名、只换一端、渐变条、读值不猜、注册表与版面）。
