# 图内属性的展示注册表（2026-09-06，UI/UX 审计 P1 / P2）

> 原文出自 `web/AGENTS.md`「图内属性的展示注册表（2026-09-06，UI/UX 审计 P1 / P2）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

`components/inspector/presentation/` 是**排版决策的唯一出处**：manifest 是能力
权威，这里只决定「摆在哪一桶、此刻显不显示、长成哪种控件」，**字段进来多少
出去多少**。

* `roleProfiles.ts` 一个角色一张模板：`primary` / `more` / `advanced` 是顺序与
  归属；`visibleWhen` 是「开关 → 从属字段」的条件展开（文字的背景 / 描边、
  次刻度的长宽、三维的箭头与背景面板、图例项断开前的示意线样式，全在这里，
  **不写第二套判据**）；`pairRows` 是并排成一行的字段对（色阶下限 / 上限）。
* **条件展开只有 `registry.fieldVisible` 一条判据**：分桶用它，不走桶的复合
  控件（刻度卡、图例排版详情卡）也用它。「改过的字段永远显示」这条兜底也在
  它里面——override 不因折叠或条件而不可发现。
* `controlKindOf` 按 **prop + 角色**认控件形态，不按「值长得像什么」猜：图例
  位置九宫格、图例项的链接开关、纵横比、色条的方向 / 两端延伸（用当前色图画
  的小色条）、三维投影（小立方体）、透明度百分比。多数视觉选择器走
  `controls/OptionGrid`（radiogroup + 方向键漫游 + 选中角标）——线型 / 标记 /
  纹理 / 箭头 / 色条方向与延伸 / 三维投影；色图选择器与图例九宫格自己实现
  radiogroup（一个要分组长列表、一个是 3×3 几何）。哪一种都一样：**图形之外
  必须有文字名与 aria-label**，选中态不只靠颜色。
* 卡承接掉的字段要在 `ElementInspector` 的「让出」集合里点名，否则同一属性会
  出两套控件。判「有没有第二套」的用例必须**把「更多」也展开**——没让出来的
  字段落进那个默认折叠的桶，只数首屏的话那条断言恒真。
* **标记这一行的形状来自 manifest 的只读事实 `marker_current`，不是猜的**
  （2026-09-06，审计 T16 补做）：`value` 说的是「选中的是哪个取值」，形状是
  另一件事。`MarkerPicker` 的规则——取值本身就是已知图形时照旧；取值说不出
  形状时按事实画（`named` 复用同一份 switch 图形，`path` 照顶点画，
  **引擎的 y 向上、SVG 的 y 向下，要翻**）。`original` 那一档形状与 P2 加的
  继承状态点**并列**，谁都不顶替谁：形状说「图上是个圆」，状态点说「这个圆
  是脚本给的、你没设过」。文字名也把形状说出来（网格里那一格的可达名与
  tooltip 同一份）——图形之外必须有文字名。
  引擎没发事实、给了这边画不出的名字、`multiple` / `too_complex`——**一律
  退回没有这个字段时的样子**，漂移只回到原状。多选时各成员事实不一致就谁的
  都不画（`sharedMarkerShape`）：取值一致不等于形状一致，那是两个维度。
  判据的锚点是 `data-marker-preview`（触发按钮里有下拉箭头、格子里有选中
  角标，两个都是 `<svg>`，按标签名找的断言恒真）。看护
  `controls/pickers.test.tsx` / `seriesPanels.test.tsx`。
* **「脚本原始」那一格画的是 `marker_original`，不是 `marker_current`**
  （2026-09-07，cap-marker-orig 补做）：换过标记之后 `marker_current` 读的是
  图上此刻那条路径，脚本原来那条已经不在图上——那一格于是只剩一个空的继承
  状态点，用户看不出点下去会变成什么。引擎**只在真的有 override 时才发**
  `marker_original`（缺席 = 与 current 相同），所以前端的规则就一句：
  `original` 那一格用 `marker_original ?? （它正好是当前值时的 marker_current）`，
  **其余格子照旧只有当前值那一格有事实可用**。文字名同理（网格里那一格的
  可达名与 tooltip 同一份）。缺席时退回今天的样子，漂移只回到原状。
  多选走 `sharedMarkerShape(elements, prop, 'marker_original')`：两份事实
  **各自判一致性**（「此刻都是菱形」推不出「原来都是圆」），而且原样多一种
  不一致——有的成员改过、有的没改，那时同样谁的都不画。
* `pairRows` 的查表键由 `pairKey` 自己生成，别手写字面量：`['vmin','vmax']`
  排序之后是 `vmax|vmin`，手写的键查不到就安静退回两行，界面上看不出异常。
* 色阶共用关系（`inspector/ColorScaleLink.tsx`）判据只认 manifest 的两条事实：
  `mappable_gid`（色条直接挂着的那个）与 `scale_gids`（与它共用同一份 norm 对象的
  **色阶兄弟**，引擎 `colorbarmodel.scale_siblings` 判、2026-09-21），唯一谓词
  `lib/colormapAlias.colorbarCovers(colorbar, gid)`；不猜「两边 cmap 名字相同」；
  引擎没给就整行不出现。色条页的对家仍是 `mappable_gid` 那个（兄弟页各自指回色条）。
* **色图选择器（`controls/ColormapPicker.tsx`，2026-09-13）**：白名单之外的色图长什么样
  由引擎的两条事实说——`cmap_current`（此刻这张：`custom` / `stops` / `discrete`）与
  `cmap_original`（换走之后脚本原来那张，多一个 `name`）。渐变的唯一出处
  `colormapStops.colormapGradient(name, facts)`：事实优先、其次离线表、都没有才回落
  「?」；离散的画硬边色块。`custom` 的显示成「自定义」（原名留在可达名与 title 里），
  它**不是可写的取值**：当前那一格点了什么都不发生（`data-cmap-entry="keep"`），
  「脚本原样」那一格（`restore`）走 `clearOverrides`，清的是 `lib/colormapAlias.
  colormapAliasGids()` 算出的整组 gid——色条 ↔ mappable ↔ 色阶兄弟（`scale_gids`）是
  同一份色图状态的一组 gid，override 落在哪一边取决于用户从哪边改的；给组里**任何一块**上色的色条都算进组
  （两块各挂一条色条时另一条也在）；兄弟的 `cmap_original` 是它**自己**的脚本原样
  （引擎按各自代采的那份报）。多选时事实**全体一致才给**
  （`sharedCmapFacts`，与 `sharedMarkerShape` 同一条纪律）。色条的方向 / 延伸小色条
  预览同样吃 `cmap_current`（`cmapFacts`），不再对自定义色图退回灰阶。
  看护 `colorScalePanels.test.tsx` 的「脚本自定义的色图」一组。
* **字体下拉并上本机字体族（2026-09-13）**：引擎按元素发的 `fontfamily.options` 只有
  首选项，本机的几百个族在 manifest **顶层** `font_families`（整份只发一次）。并表
  **只在 `lib/typography.withMachineFamilies` 一处**——`useFigureTypography` 的 `fieldOf`
  与写入前的 `coerceTypography` 拿的必须是同一份表，否则下拉里选得到、写下去却被判成
  「不是选项」；`ElementInspector` 兜底用的两个通用字体 `Select` 也过它。老引擎不发
  这张表时行为一字不变。看护 `figureFontFamilies.test.tsx`。
* 看护：`presentation/registry.test.ts`、`legendCard.test.tsx`、
  `legendSpacingCard.test.tsx`、`colorScalePanels.test.tsx`、
  `axes3dPanel.test.tsx`、`tickTaskCard.test.tsx`、`lib/viewAngle.test.ts`
  （三维方向示意的期望值取自真 matplotlib 的 `proj3d._view_axes`，
  文件头写了重新生成的脚本）。
