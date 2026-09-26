# 图标体系（Iconography）

> 第一版 2026-09-06（用户反馈第 7 条「图标非常不统一」）把尺寸、描边、别名与手绘 svg
> 统一到 lucide 一套；第二版 2026-09-15（用户反馈「所有图标都不够精致，很 demo」）把
> 图形本身换成自绘的一套，决策在 ADR 0052。代码里的唯一出处是
> `web/src/components/ui/icons/`（几何 `defs.ts`、工厂 `createIcon.tsx`、公开面 `index.ts`）
> 与 `web/src/components/ui/Icon.tsx`（尺寸阶梯 / 描边 / Provider），门禁在
> `web/src/components/ui/iconography.test.tsx`；本文是它们的说明书，不另立规则。

## 一、结论先行

- 全产品只有**一套**图标：`components/ui/icons` 里自己画的 141 个。名字与 lucide 时代
  一一相同，同一语义只用一个名字（第四节的表不动）。**任何第三方图标库都不许再
  import**（门禁按 import 路径判）。
- 画法：24 网格；所有线条 2 单位描边、圆头圆角，按比例缩放；闭合外形外角 ≥ 2.5、
  内角 ≥ 1；标点是直径 2.5 的纯填充圆；每个图标 ≤ 3 个可辨元素、元素间净空 ≥ 2；
  同一家族同一个骨架。
- 尺寸只有四档 `ICON_SIZE = { xs: 12, sm: 14, md: 16, lg: 20 }`，默认 **sm**；描边
  `ICON_STROKE.regular = 2`，`emphasis = 2.5` 只给复选框的勾；三个 React 根都套
  `IconProvider`，图标集自己的默认就是阶梯，Provider 只是「默认档在哪改」的唯一答案。
- 选中 / 激活态用实心孪生：`<Layers filled />`。只有 30 个真正用作开关的图标有孪生，
  其余忽略 `filled`（第五节）。

## 二、精致感从哪来——量出来的六条

规格不是凭感觉定的。OpenAI 自己的设计系统 `@openai/apps-sdk-ui`（ChatGPT 应用的组件库，
MIT，755 个图标源码公开）逐个量过之后，每一条都能指出 lucide 在 Tavotto 里的病根：

| 维度 | lucide 在 Tavotto 里 | OpenAI 图标集（量得） | 新规 |
| --- | --- | --- | --- |
| 网格 / 描边 | 24 / 1.75 | 24（730 / 755）/ 2，已展开成填充 | 24 / 2 |
| 16px 上的线宽 | 1.17px（14px 上 1.02px，比 13px 正文的笔画还细） | 1.33px | 1.33px |
| 端点 / 拐角 | 圆头；闭合外形多数 r=2 | 三角外角 r≈4、方框 r≈3.5、六角 r≈3；内角 r=1 | 外角 ≥ 2.5、内角 ≥ 1 |
| 标点 | 长 0.01 的线段，直径 = 线宽 | 实心圆，直径 2.3～2.5 | 实心圆，直径 2.5 |
| 元素数 | 不限（齿轮 8 齿、剪贴板 6 段） | ≤ 3（设置 = 六角 + 环） | ≤ 3，净空 ≥ 2 |
| 选中态 | 只靠底色 | 43 对 outline / filled | 30 个实心孪生 |

1. **标点得是点。** 叹号的点是长 0.01 的线段时，16px 上只剩 1.2px——警告图标「缺了
   点什么」就是它。状态家族（警告 / 错误 / 说明 / 帮助 / 问题）共用同一套点与竖条。
2. **线要比正文笔画略重，不能更轻。** 界面正文 13px 的笔画约 1.2px；1.75 / 24 在 14px
   上只有 1.02px，整套图标像线框稿。改 2 之后 12px 的折叠箭头刚好 1px。
3. **外角大、内角小、端点全圆。** 一大一小让轮廓「厚」得均匀；多边形用同一个圆角
   函数生成，没有手调的角。
4. **一个图标最多三个能分辨的元素。** 现状的齿轮、剪贴板、双框加太阳在 16px 上糊成一团。
5. **用实心表达选中，不只靠底色。** 外形不变、内部细节挖空，切换时形状不跳。
6. **同一家族同一个骨架。** 五个圆形状态共用 r=9 的圆，四个文件共用一个折角文档，
   八个对齐都是「一条线 + 两根实心条」，四个箭头方向是同一条路径旋转。

## 三、定下的纪律（与理由）

### 尺寸阶梯

| 档 | px | 用途 |
| --- | --- | --- |
| `xs` | 12 | 折叠 / 下拉的箭头（一律 xs，它们是从属指示物）、徽标 / 角标里的小记号、11px 说明文字旁 |
| `sm` | 14（默认） | 与 12–13px 界面文字并排：菜单项、带文字的按钮、检查器行首、树行状态标记、横幅提示 |
| `md` | 16 | **只有图标**的按钮（28px 点击区）、顶栏工具、左侧图标轨道、对话框标题栏的关闭钮 |
| `lg` | 20 | 空状态、引导卡片、对话框级别的强调图 |

界面字号 11–14px、控件高 28px，密度比通常的 web app 高一档；14 是与 12–13px 正文并排
时视觉体量相当的尺寸，16 在 28px 点击区里留有 6px 呼吸，12 给箭头与徽标。

### 描边

`strokeWidth = 2`，按比例缩放：12 / 14 / 16 / 20 上 1.0 / 1.17 / 1.33 / 1.67px。不能固定成
绝对像素——图形在 24 网格上留的最小净空是 2 单位，12px 档上只剩 1px，描边一固定到
1.5px 以上细节就糊成一团。`ICON_STROKE.emphasis = 2.5` 只给一种场景：填色小方块里的
对勾（复选框选中态）。

### 画一个新图标

在 `defs.ts` 里按上面的画法写几何（`rr` 圆角矩形、`circ` 圆、`dot` 实心点、`poly` /
`regular` 圆角多边形；`s` 描边、`f` 实心、`k` 挖空、`o` 挖空后压线、`sel` 孪生），在
`index.ts` 加一行导出，跑 `iconography.test.tsx`，再用真浏览器截一张 16px 看一眼。
**加图标是画，不是去别的库挑**——挑出来的图标就是「不统一」的来源。

### 对齐与间距

- 图标在 `inline-flex items-center` 的行里靠 flex 居中，不调 baseline。
- 按钮里图标与文字的间距由 `ui/Button` 定：`sm` 4px、`md` 6px；菜单项 8px。
- 每个图标自带 `shrink-0`（Provider 的默认类名），窄行里不会被压成椭圆。

### 机制

- `IconProvider`（`Icon.tsx`）是一个 React context，套在三个根：`main.tsx`、
  `playground/main.tsx`、`mcp/McpProviders.tsx`。
- 写法：`<X size={ICON_SIZE.md} />`；不写 `size` 即 sm；不写 `strokeWidth`；开关 / 激活态
  写 `filled`。
- 无障碍：没有 `aria-label` / `aria-labelledby` / 子元素时自动 `aria-hidden`；类名
  `icon icon-<kebab>`，用例按它认形状（`svg.icon-triangle-alert`）。
- 折叠块：`ui/Details` 的 `Details` / `Summary`（原生 `<details>` 加图标集的箭头）。

### 门禁（`web/src/components/ui/iconography.test.tsx`，TypeScript AST）

1. 非测试源码里没有内联 `<svg>`，豁免表**按文件按个数**（图标集本体 `createIcon.tsx`
   那一个也在表里；多画一个就红）；
2. 任何大写标签上 `size={数字}` 都红（间接渲染也抓），图标标签上的 `size` 只能是
   `ICON_SIZE.*`；`strokeWidth` 只能是 `ICON_STROKE.*`；
3. 从 `components/ui/icons` 引入的名字必须在 `ICON_DEFS` 里；**任何第三方图标库**
   （lucide-react、heroicons、react-icons、tabler、radix、phosphor）的 import 一律红；
4. JSX 文本里单独撑起一个元素的 ✕ × ▸ ▾ … 与任何 emoji 都红；
5. 没有裸的原生 `<summary>`；
6. 每个几何定义都渲染得出来；`filled` 渲染孪生、同页两个蒙版 id 不串。

每条规则都有正反两组自检样例。

## 四、语义统一表（同一含义只用一个图标）

| 语义 | 图标 | 曾经的并存者 |
| --- | --- | --- |
| 关闭 / 移除 | `X` | 刻度示意图芯片上的字符 `×` |
| 撤销 / 重做 | `Undo2` / `Redo2` | Codex 内嵌画布与 playground 用的 `RotateCcw` / `RotateCw` |
| 恢复到脚本原值 | `RotateCcw` | — |
| 刷新 / 重新扫描 | `RefreshCw` | 素材库刷新用的 `RotateCw` |
| 复制到剪贴板 | `Copy` | `ClipboardCopy` |
| 外部链接 / 在文件管理器里显示 | `ExternalLink` | `SquareArrowOutUpRight` |
| 打开设置对话框 | `Settings` | `Settings2` |
| 编辑（改名、改规范） | `Pencil` | `PenLine`、`Settings2` |
| 调整参数 / 更多属性 | `SlidersHorizontal` | `Settings2` |
| 警告 | `TriangleAlert` | 别名 `AlertTriangle`；`ShieldAlert` 只留给「来源已分叉」 |
| 阻断（问题等级 error） | `OctagonAlert`（停车牌的形状） | 此前与警告共用 `TriangleAlert`、只靠红 / 琥珀色区分（2026-09-14 二审 C1）；表在 `lib/validationText.SEVERITY_ICON`，问题面板与导出清单同一份 |
| 折叠 / 展开 | `ChevronRight` 转 90° | 手绘 svg、浏览器 `<details>` 三角 |
| 下拉 | `ChevronDown` | — |
| 加载中 | `LoaderCircle` | 别名 `Loader2` |
| 帮助 | `CircleQuestionMark` | 别名 `CircleHelp` |
| 成功 | `CircleCheck`（状态）/ `Check`（选中标记） | 别名 `CheckCircle2` |
| 可编辑的图（由脚本生成、能改图内对象） | `SquareMousePointer`，唯一出处 `ui/semanticIcons.EditableFigureIcon` | `Braces` |
| 整张图（图内编辑的图幅） | `Fullscreen`（`roles/roleIcons`） | `Frame` |
| 改图助手 | `Sparkles`（一大一小两颗火花） | — |
| 样式（左轨「样式」面板，2026-09-24） | `Paintbrush` | — |

三个在第二版换了隐喻的（其余 138 个都是同一隐喻的重画）：设置（8 齿齿轮 → 六角 + 环）、
图层（三层菱 → 一层实心 + 两道）、项目接入状态（六段剪贴板 → 剪贴板 + 勾）。

## 五、有实心孪生的 30 个（`filled`）

左轨：`Folder`（工作区，2026-09-24 补）`LayoutGrid` `Images` `Layers` `SquareMousePointer` `Paintbrush` `TriangleAlert` `Settings`
`ClipboardList`；状态：`CircleAlert` `Info` `CircleCheck` `CircleX` `CircleQuestionMark`
`CircleMinus` `ShieldAlert` `ShieldCheck` `ShieldQuestionMark` `Lightbulb` `Zap`；开关：
`Sparkles` `Pin` `Eye` `Lock` `Bookmark` `Square` `Circle` `Tags` `Diamond` `Bot`。

接在已有状态上的地方：左轨七个上下文（`aria-expanded`）、右栏助手钮（`aria-pressed`）、
三处图钉、顶栏当前标注工具、图层树的锁。元素树的类型图标**不做**实心态。

## 六、第一版的盘点（2026-09-06，历史）

改造前 `web/src` 里 lucide 直接渲染 323 处 / 101 个图标 / 82 个文件，`size` 用了 10 种
数值、别名 11 个 32 处、手写 svg 1 处、浏览器 `<details>` 三角 16 处、字符图标 1 处。
第一版把这些全部归到四档 / 一套名字 / `ui/Details`，并评估过 morphicons（不接：13 KB gzip
换四对列表行里的变形，还要在 Codex 内嵌画布里再背一份）。第二版在此之上只换图形。
