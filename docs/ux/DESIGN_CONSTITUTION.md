# Tavotto Design Constitution（1.0）

方向：**Paper × Instrument**。一件用于科研图制作与论文排版的精密仪器——微微的纸张感、
工程工具的精确、桌面软件的成熟；极简但不空洞，克制但有设计。精致来自比例、对齐、
间距、字体层级、图标与状态，**不来自装饰**。

本文是全部视觉参数的唯一出处的**说明**；值本身在 `web/src/index.css` 的 `@theme`，
门禁在 `web/src/components/ui/foundation.test.ts`（类名字面量）与
`iconography.test.tsx`（图标）。改值先改 index.css，改规矩先改这里，两边同一次提交。

## 一、颜色

| 语义 | 工具类 | 值 | 用途 |
| --- | --- | --- | --- |
| surface-app | `bg` | `#f2f2ef` | 应用底：微暖纸白，不黄、不米 |
| surface-panel | `surface` | `#ffffff` | 面板 / 输入框 / 浮层 |
| surface-subtle | `surface-2` | `#f7f7f4` | 只读值、数字框静态底、徽章底 |
| surface-hover | `surface-hover` | ink 4.5% | hover。三档里最弱 |
| surface-active | `surface-active` | ink 8% | 按下、小 chip 的静态底 |
| surface-selected | `selected` | `#e6e6e0` | 选中：只比背景稍深，配字重 / 对勾再说一遍 |
| ink-1 | `ink` | `#1b1b18` | 主文字，不是纯黑 |
| ink-2 | `ink-2` | `#5c5c55` | 次级文字、标签 |
| ink-3 | `ink-3` | `#6b6b64` | 元数据、单位、占位。仍 ≥4.5:1 |
| ink-disabled | `ink-faint` | `#a3a39a` | 禁用 / 装饰。不用于要读的字 |
| border | `border` | `#e3e3dd` | hairline。只给输入框、区域边界、浮层 |
| border-strong | `border-strong` | `#cfcfc7` | hover 中的输入框、复选框 |
| accent | `accent` / `accent-subtle` | `#2868b7` | **小面积**：焦点环、链接、AI、画布选择框 |
| danger / warning / success | `danger` / `warn` / `ok`（各带 `-subtle`） | | 只表达语义 |

工具类名沿用旧名（不为了改名动七百处调用），对照表也写在 index.css 顶部。

规矩：蓝色不做任何大块背景、不做按钮填色（主按钮是近黑 `bg-ink`）；持久表面不用投影，
浮层只用 `shadow-pop`；surface 之间靠极轻的明度差与 hairline 分层，不靠框。

## 二、圆角

`--radius-*` 先整个清空再定义，Tailwind 自带的 xl / 2xl 写了也不生效。

| 档 | 值 | 给谁 |
| --- | --- | --- |
| `rounded-xs` | 3 | 16px 高以下的小片：kbd、计数角标、缩略图上的标签 |
| `rounded-sm` | 6 | 控件：输入框、按钮、图标钮、选项格、树行、tooltip |
| `rounded-md` | 8 | 卡片与浮层：菜单、popover、select 弹层 |
| `rounded-lg` | 12 | 对话框、命令面板 |
| `rounded-full` | | 圆点、开关、徽章（`Badge` 是唯一的胶囊文字元素） |

## 三、密度

Tavotto 是「紧凑工具」那一档：**控件一律 28px（`h-7`）**——按钮、输入框、下拉、图标钮、
树行、菜单项全部同高，一行里的东西天然在一条中线上。行内 gap 按 4 / 8 走
（`gap-1` / `gap-1.5` / `gap-2`），分区之间靠 `Section` 的固定留白。

三档定义（供页面级 Session 落地，目前只有第一档有消费者）：

- compact row 28：控件与列表行（已落地）
- normal control 32：对话框脚部主动作（待定，看真实页面再决定要不要拉高）
- setting row 48：设置页一行（待定；`SettingRow` 现在 min-h 28 + 标签 py-1）

不允许某个页面自己决定按钮高度。

## 四、图标

见 `docs/ux/ICONOGRAPHY.md`：只有 lucide 一套，四档 12 / 14 / 16 / 20，描边 1.75 按比例
缩放，`IconProvider` 给默认值。下拉的记号**只有 chevron-down**（收起朝下、展开转到朝上），
不许用「<」或别的字形表示下拉。图标钮（Pin / Close / Copy / Refresh / More）走 `IconButton`：
28×28、16px 图标、透明底、hover 才浮出 surface-hover。低频操作不给更重的视觉。

## 五、控件

- **Button**：四档 `primary`（近黑填色，每个上下文最多一个）/ `secondary`（细边白底，
  工具操作默认）/ `ghost`（无边无底）/ `danger`（红字 ghost）。`active` 是 selected 轻 tint +
  字重。忙碌态自带。
- **IconButton**：`label` 既是可达名也是气泡，一份文案两处用。
- **TextInput**：`invalid`（红边 + aria-invalid）、`suffix`（**框内**后缀，数字自动右对齐）。
- **NumberField**：`unit` 是框内单位 `[ 393.7      mm ]`；`suffix`（框外）是 1.0 前的形态，
  页面级 Session 逐页迁完就删。
- **Select**：全仓唯一的下拉（`nativeSelect.test` 守着）；`title` 给当前取值的解释。
- **Checkbox**：14px 方块、xs 圆角、选中近黑 + 白勾（唯一允许加粗描边的图标）。
- **Toggle**：唯一的滑动开关，名字必填。
- **Badge**：胶囊、16px 高、五种语义色。
- **Tabs / tabClass**：下划线标签页，选中 = 字重 + 2px 近黑线。
- **listRowClass**：树行 / 列表行的共同外观（28px、hover / selected / hidden 三态）。
- **TreeRow**（`treeIndent` / `TreeChevron` / `TreeIcon` / `TreeCount`）：树行的固定列——
  缩进 8 + 14 × 层级、16px 折叠箭头列、16px 类型图标列、右对齐计数。图层树与图内
  元素树共用；叶子行留空的箭头列，同层的图标才对得齐。层级只靠缩进与箭头，不靠留白。
- **SearchInput**：面板顶部的搜索框，唯一的一种——安静的 surface-2 填充框，hover 才有
  hairline、聚焦才白底 + accent 边；左侧放大镜固定列，有内容才出清除钮；Esc 先清空再失焦。
- **Notice**：低权重说明条（Info + caption + 至多一个 ghost 小动作），surface-2 底、无边框；
  它是脚注不是卡片。要警告语义用设置页的 `InlineWarning`。
- **Section / SettingSection / Disclosure / Details**：分区与折叠。

状态四态必须可辨：hover（surface-hover）< active（surface-active）≈ selected（selected +
字重 / 对勾）；disabled 统一 `opacity-35~40 + cursor-not-allowed`，不用 pointer-events-none
（那会连 tooltip 一起吞掉）；destructive 只有红字，不用红底。

## 六、文字

六个角色（`index.css` 的 `@utility type-*`），层级由角色定，不由页面自己挑组合：

| 角色 | 值 | 给谁 |
| --- | --- | --- |
| `type-title` | 14 / 20 · 500 · ink | 对话框 / 页面标题 |
| `type-section` | 11 · 500 · 大写 · 字距 .06em · ink-3 | 分区小标题、菜单组标题 |
| `type-body` | 12 / 16 · ink | 正文 |
| `type-control` | 11 · 颜色随控件 | 控件文字 |
| `type-caption` | 11 · 行距 1.5 · ink-2 | 说明文字 |
| `type-meta` | 11 · ink-3 | 元数据、路径、计数 |

字号阶梯只有 xs 11 / sm 12 / base 13 / lg 14 四档；`text-[Npx]` 不许出现（营销页 /try 的
三个展示级字号按个数豁免在门禁里）。字重只有 400 / 500；`font-semibold` 现存 9 处待
页面级 Session 收成 `type-title`。

## 七、动效

时长只来自 token：`fast` 120 / `base` 180 / `slow` 240 / `exit` 90；形态只有
opacity + ≤4px 位移 + scale 0.97~1；没有弹簧、缩放炫技、漂浮。`prefers-reduced-motion`
是硬约束（JS 动画走 `lib/motion.tween()`）。

## 八、少用容器

留白、对齐、字体层级、hairline 优先；卡片只给「真的是一张卡」的东西（注册表条目、
会话卡）。一个页面里所有东西都有框，说明设计失败。

## 九、素材库 / 脚本区 / 树（Session 3 定下的形态）

- **素材卡**：预览 3:2 白底，上面**不压任何标签**（格式 / 尺寸 / 接入状态 / 使用次数全在
  下方两行文字里，用「·」串起来，使用次数靠右）；hairline 常态、hover 加深一档、选中是
  selected 轻 tint + 名字加粗。抽屉最窄 280px 也是双列——这是素材库不是看图器。
  悬停时右下角的就近入口是 24px 图标小片（名字在 title 与读屏文本里）。
- **脚本行（File Row）**：`● 文件名   状态一句话   ▶`，28px 一行，状态点 6px 坐在 16px 列里
  （实心 = 已关联、空心 = 未运行、呼吸 = 运行中、红 = 失败），运行 / 取消是同一颗
  IconButton，常态 ink-3、行 hover 才与文字同色。分组名 + 计数是 type-meta，不是标题。
  恢复路径（可能需要原环境）是这一行的第二行，缩进到文件名列，不套框。
- **抽屉标题**：`名字  计数`，计数只是一个 type-meta 数字，完整的「N 个元素」给读屏；
  钉住是 IconButton。
- **树**：TreeRow 四列；聚类行有类型图标与右对齐计数；⋯ 菜单 hover / 键盘落到行里才出现。

## 十、2026-09-11 Session 1 的处置记录

改了什么、哪些页面自动受益、哪些留给后续 Session，见同日提交信息与
`web/AGENTS.md` 的「UI 视觉纪律」段。
