# ADR 0082：属性页按页面上的实际大小显示字号与线宽

日期：2026-09-24 · 状态：**Accepted**
修订：[0029 Style / Spec / Export 三层](0029-style-spec-profiles.md) §6a（「样式里以 pt 计的数字是页面 pt」扩展到属性页）
相关：[0032 属性能力层](0032-typography-capability-layer.md)、[0030 统一检查与问题定位](0030-validation-and-problem-navigation.md)、
`docs/rules/frontend/typography-capability-layer.md`

## 问题

图在画布上缩放以后（比如摆成原生宽度的 60%），同一个刻度字号在三个地方显示两个数：

- 右栏属性页显示脚本坐标系里的原始值 9 pt；
- 左栏样式面板（#547）显示页面上读者量到的 5.4 pt（9 × 0.6）；
- 问题面板说「当前 5.40 pt」，一键修复（#549）也按页面值修。

用户拍板：**统一按页面上的实际大小显示**。

## 裁决

### 一、换算只有一处：`stylePresets.pagePtLens(panel)`

以 pt 计、随面板线性缩放的属性（`PAGE_PT_PROPS`）在界面上**进出都是页面值**：

- 显示 = 脚本值 × `panelScale`；
- 用户输入的是页面值；
- 写 override 之前换回脚本值。

局部预览（`svgPreviewStore.previewStyle`）拿到的是换回去的脚本值，与写进 override 的是同一个数，因为预览贴在按脚本坐标系画的 SVG 上。

换算装在**写入器**里，控件拿到的已经是页面值。写入器有：

- `useTextStyleAdapter`，`useFigureTypography` 在它上面；
- `useElementWriter`；
- `useTickAxisAdapter`；
- 通用单选行 / 批量行（`ElementInspector` 的 `FieldRow` / `BatchFieldRow`）；
- 右键快捷编辑（`QuickEdit`）。

样式面板原来自己做换算，现在删掉了，和属性页走同一个写入器。界面代码不许 import `panelScale` / `toPageValue` / `toScriptValue` / `pageField`，由 `lib/pagePtLens.test.ts` 用 AST 看护。

`PAGE_PT_PROPS` 补进了属性页上与样式属性并排摆着的那几条 pt 量：

- `spine_<side>_linewidth`、`grid_linewidth`；
- `bbox_linewidth`、`stroke_width`；
- `handle_markersize`；
- `axline_width`、`arrow_width`；
- `mutation_scale`、`labelpad`。

理由是边框卡：「全部」是 `spine_linewidth`、逐边是 `spine_<side>_linewidth`，同一张卡里不能一个是页面值、一个是脚本值。散点面积 `size`（pt²）按缩放比的平方走，**不在表里**。

画布标注的 `TextObject.sizePt` 本来就是页面 pt，不换算。

### 二、取整：两位小数，`toFixed`

- 换算的取整和数字框的显示都是两位小数（`PT_DECIMALS`）。字号框原来只显示一位小数，页面值 8.25 在属性页上会显示成 8.3。
- 取整用 `toFixed`，不用 `Math.round(v * 100)`。问题面板的「当前 X pt」是预检的 `eff.toFixed(2)`（Python 侧是 `%.2f`）。半格上的值用 `Math.round` 会多进一位：缩放比 1.33 时 6.5 × 1.33 = 8.645，二进制里是 8.64499…，问题面板说 8.64，`Math.round` 却给 8.65。

### 三、往返与缩放比大于 1

manifest 把脚本值按两位小数回报，预检读的就是它，所以脚本值也按两位小数存。结果：

- **三处永远是同一个数。** 属性页和样式面板读 `toPage(脚本值)`，问题面板读 `(manifest 值 × scale).toFixed(2)`，两边读的是同一个脚本值、同一种取整。
- **缩放比 ≤ 1 时，任何两位小数的页面值都能原样往返**：输入什么，写进去再读回来还是什么。用例在 0.6 / 0.75 下覆盖了 1.00–40.00 的每一个值。
- **缩放比 > 1 时，页面上能表示的值间隔是 0.01 × 缩放比。** 可表示的值能原样往返；不可表示的输入，真实渲染离输入不超过 0.005 × 缩放比，显示差一格。1.33 时约 25% 的值属于这种情况。实测：
  - 输入 8.5：存 6.39，三处都显示 8.5，不报问题；
  - 输入 8：存 6.02，三处都显示 8.01，真实渲染 8.0066 pt，严格高于绝对下限 8 pt，不报问题。判定对的是真实渲染出来的值，没有误判。

要消掉这一格偏差，得把 manifest 里这些属性的精度提到三位小数。那要改引擎、Python 预检与 fixture，按 1.0 收敛纪律不在这里做，记作后续。

### 四、上下界约束脚本值

引擎字段的 `min` / `max` 说的是脚本值（引擎接受什么），界面把它们换到页面上显示与钳位（`min × scale`，只乘不取整），与样式面板一致。写死在浮动工具条上的线宽界（0.1–12）也过同一个函数（`lens.bound`）。

属性能力层的兜底区间（字号 1–400 pt）在页面值上判：写入器交给 `coerceTypography` 的已经是页面上的字段和值。

### 五、缩放比算不出来

`nativeW` 缺失时按 1 显示脚本值，**不另外标「原始值」**。`nativeW` 是文档模型的必填字段，迁移时补成摆放宽度，渲染回来按 manifest 的 `size_mm` 校正，所以这种情况实际上到不了。即使到了，预检也按 1 量，问题面板说的正是这个数。属性页单独标一句「这是原始值」，反而是在宣称一个产品其余部分都不承认的差别。

### 六、缩放比为 1

缩放比为 1 时换算原样进出：返回同一个字段对象，不引入任何一次取整。和改造前相比，唯一的差别是字号框从一位小数改成了两位：脚本值 8.33 以前显示 8.3，现在显示 8.33，与样式面板、问题面板一致。

## 看护

| 判据 | 在哪 |
| --- | --- |
| 0.6 / 0.75 全量往返；1.33 可表示值往返、其余偏差有界；三处同一个数；上下界；缩放比 1 原样；界面不自己换算（AST） | `web/src/lib/pagePtLens.test.ts` |
| 属性页 / 样式面板 / 问题面板对同一个标题显示同一个数；输入 8.5 后三处 8.5、问题清零；撤销 / 重做；1.33 下的 8.5 与 8 | `web/src/components/inspector/pagePtInspector.test.tsx` |
| 通用行 / 批量行 / 排版批量（「多个值」不压扁）/ 边框卡 / 刻度卡 / 浮动工具条 / 右键快捷编辑按页面值进出；局部预览与 override 同一个脚本值 | 同上 |
