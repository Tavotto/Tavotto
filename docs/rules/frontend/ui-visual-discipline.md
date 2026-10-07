# UI 视觉纪律

> 原文出自 `web/AGENTS.md`「UI 视觉纪律」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

全文是 `docs/ux/DESIGN_CONSTITUTION.md`（Paper × Instrument；2026-09-11 Visual
Consolidation Session 1 定稿），值在 `src/index.css` 的 `@theme`，门禁
`components/ui/foundation.test.ts`（类名字面量：像素圆角 / 像素字号 / ink 透明度 hover /
数字时长 / 手拼大写小标题 / 预设投影 / 第二套复选框与开关 / `variant="outline"`）。
这里只留一段速记：

**2026-09-30 重设计（宪法第二十五节）**：灰色桌面 `bg` 上放顶栏 / 左轨 / 停靠抽屉，作品在一块白色圆角工作面板
（`data-work-panel`：画布标签 + 画布 + 属性栏）里；画布灰铺到面板边缘。左轨写短名（`rail.short.*`）。带字的按钮与分段控件是胶囊，
次按钮灰底无边；链接是灰字；品牌蓝 #5A92E5 压深两档当 accent / sel。下面是此前的速记，与第二十五节冲突处以第二十五节为准。

**2026-10-07 属性栏与画布栏（宪法第二十六节末）**：属性栏是一张行网格（`Row labelWidth="grid"`：标签 `--insp-label` · 控件 full / half ·
20px 常驻状态槽），恢复钮在槽里、修改点悬挂在标签左 8px（部分修改空心环）；三级标题 32 / 24 / 32；一屏只有节间一条发丝线；折叠只有
SummaryRow / GroupToggle / Details；身份头固定两行；`ColorField` 在属性栏里带可编辑 hex 与取色面板（`ColorFieldContext`）、`disabled` 是真禁用；
OptionGrid 与 Segmented 同皮；说明条只有 `Notice`；页签 属性 | 画布 | 助手。

**2026-10-07 设计刷新 · 基础层（宪法第二十六节）**——与下文冲突处以它为准：墨阶 ink-2 `#4a4a45` / ink-3 `#6c6c66`（所有底色 ≥4.5:1）；
中性面降黄（桌面 `#efefed` / 画布灰 `#f5f5f3` / field `#f2f2f0`）；**圆角族 xs 4 / sm 6 / md 8（行、框、菜单项、说明条）/ lg 12（卡、菜单与 popover
外壳、多行浮动面板）/ panel 16（对话框、工作面板、助手输入框、命令面板）/ full（带字按钮、图标钮、分段、chip、toast、单行浮动条）**，外层 = 内层 + 内边距；
字体角色十个（新增 `type-display` 24 / `type-heading` 17 / `type-reading` 13·1.6；`type-title` 600；`type-caption` 12 / ink-3）；控件高 28 + `lg` 32
（对话框页脚 / 页面 CTA）；投影全部 `color-mix(var(--color-shadow) N%)`，`shadow-card` **只在 `ui/Card`**；状态色一个锚点派生
`-surface` / `-border` / `-content`（字用 content），旧 `*-subtle` 是别名；z-index 只用 `z-sticky … z-onboarding` 九档 token；焦点环 offset 2、
插入点 accent；滚动条 6px、只在悬停 / 内含焦点时出现；加载只有 sweep / 静态骨架 / shimmer / 转圈（禁呼吸动画）；光标一律箭头（可拖的卡用抓手）。
新原语：`Card`、`Notice`（恢复）、`StatusPill`、`dropZoneClass`（拖放接收态：静态不画、拖入才是 1.5px accent 虚线 + accent-subtle + 外发光）、`FormSection` + `FieldGroup`（`SettingRow layout="balanced"`）、EmptyState v2、`listRowClass({ size })`
+ `rowMetaClass` + `dropLineClass`、`useRowMenu` + `RowMenu`（⋯ / 右键 / ⇧F10 同一份菜单）；Dialog 宽度 sm 400 / md 480 / lg 560 / xl 760 / shell、
页脚 `{ start, secondary, primary }` 三槽、浮动毛玻璃页脚、`onEscape`（Esc = 安全答案）、栈底才画遮罩；**每个上下文一颗主按钮**；对话框页脚的破坏性动作是
`Button variant="danger-tinted"`（浅底危险胶囊，永不实心红）；按钮层级的稳定判据是 `data-variant`。门禁全在 `foundation.test`（含逐页阶段的 `LATER_PHASE` 豁免）。

**2026-10-07 改图助手重做（宪法第十八节「2026-10-07 重做」小节）**：转录是一段对话而不是一轮一张卡——用户消息右对齐气泡（超 3 行折叠，hover 出复制 / 重新发送），
助手回答不装卡、`type-reading` ink；过程折成一行「已思考 Ns · N 步」的环境行（耗时来自 `AiEntry.at` / `AiSession.finishedAt`，缺了不说），
改了脚本是唯一带边框的显著卡（`DiffView`：文件头 + hover 替换计数的操作 + 22px 行 + 4px 变更条 + 行号 + 词级高亮）；代码块无边框 + 语言 + 复制 +
`--color-syntax-*` 语法色（/try 的 Code Sheet 共用）；回答里的链接 accent 是「链接灰字」的唯一例外；输入框 42px 胶囊 ↔ 两行两态、聚焦只加深到
border-strong、上方玻璃上下文带（目标 · 作用范围 chip）、工具行两颗模型 / 推理强度胶囊、26px 圆形发送 / 中止（交叉淡化不缩放，进行中 1px 轨道）；
任务历史是 320px 弹层；错误是 danger `Notice` + 重试。600 字重在 `ai/Markdown`（h2 / h3）与 `ai/DiffView`（「已修改」）有带个数的门禁豁免。
选择器认 `data-ai-*`（`data-ai-input` / `-composer[data-layout]` / `-context` / `-target` / `-pill` / `-user` / `-process-toggle` / `-step` /
`-diff` / `-revert` / `-code` / `-history` …）。

白色 surface；层级靠留白 / 字号 / 轻微背景差，
边框只给区域边界、选择状态与浮层；可编辑框是 `field` 底、静态无边（聚焦 accent 边）。**持久表面里只有
「真的是一张卡」的东西有投影（`--shadow-card`：素材卡 / 诊断卡；助手的会话卡与任务行 2026-10-07 起撤了卡），浮层用 `--shadow-pop` /
`--shadow-dialog`；改图助手输入框是浮在对话流上的玻璃（`--color-glass` + `backdrop-blur-lg` + `--shadow-composer`）**
（宪法第二十二节，2026-09-15）。
radius（2026-10-07 起，见上）：`xs` 4、`sm` 6、`md` 8（行 / 输入框 / 说明条）、`lg` 12（卡片 / 浮层外壳）、`panel` 16（对话框 / 工作面板）、`full`（带字的按钮 / 图标钮 / 分段）；
Tailwind 自带的 xl 以上已清空。UI 字号 11-14px（`xs/sm/base/lg`）、六个 `type-*` 字体角色；
控件高 28px、树行高 28px、图标点击区 ≥28px。交互面三档 token：`surface-hover` <
`surface-active` ≈ `selected`（#e6e6e0，轻 tint + 字重，不靠深灰块）。主按钮近黑色
（`bg-ink`）；按钮四档 primary / secondary / ghost / danger；蓝色只用于选择 / 焦点（链接是灰字，2026-09-30）；
每个上下文最多一个填色主动作（顶栏=导出、助手=发送、弹窗=确认）。禁用态一档 `opacity-40 +
cursor-not-allowed`；未选中复选框 / 单选边框与关态开关轨道 `border-control`（≥3:1）；焦点环
`focus-ring` 不透明。**跟着选中项走的指示物只有一份实现**（2026-09-14 二审 E2 / E3）：Tabs 的下划线与 Segmented 的选中底
由 `ui/slidingIndicator` 滑动（jsdom 量不到几何，用例要自己给 `offsetLeft / offsetWidth` 装值）；折叠
分组的展开走 `ui/Field.Reveal`（`usePresence` 保活退场那 90ms，所以收起后内容还在 DOM 里一小会儿）；
没写时长的 `transition-*` 默认就是 `--duration-fast` + `--ease-standard`（`motion.test` 守着）。
**键盘契约在原语里**（2026-09-14 apple-design 审计批次 1）：Dialog 打开后焦点
在容器、关闭钮 DOM 排最后；Segmented / Tabs 一个 Tab 停靠点 + 方向键；`keyboardPrimitives.test`
与 `foundation.test`（`role="radio"` / `aria-haspopup` / disabled 写法 / `ring-accent/N`）守着。
**可编辑框只有一副**（`ui/fieldBox.ts`：`field` 底、无边，聚焦 accent 边；TextInput / NumberField / Select /
SearchInput / `inspector/controls/PickerTrigger` 共用；批次 2，形态 2026-09-15 参考 Codex 改成只换底色）；带样张的取值选择器一律 `Popover + PickerTrigger + OptionGrid`
（`onPick` 收弹层、方向键漫游不收）；数值与单位 `formatQuantity`；token 配对对比度由
`src/tokenContrast.test.ts` 守着（焦点环 / 控件边界 ≥3:1，要读的字 ≥4.5:1）。文字对比：
`ink-2`/`ink-3` 均 ≥4.5:1，`ink-faint` 仅装饰 / 禁用——装饰记号（`当前 → 要求` 的箭头、
`状态 · 时间` 的间隔点）必须 `aria-hidden`：e2e 的自算对比度尺子（`e2e/contrast.ts`）只放过
「aria-hidden **且**自己的文字里没有字母数字」的元素，其余用 `ink-faint` 的字照样量、照样红
（折叠 summary 是要读的字，用 `ink-3`；未选中的分段标签用 `ink-2`——2026-09-30 起分段槽叠在灰桌面上，ink-3 不到 4.5:1）。选中态不只靠颜色（字重 / check /
形状变化）。下拉的记号只有 chevron-down。支持 `prefers-reduced-motion`。
Document 字体（Times）与 UI 字体严格分离。

设置窗口（Session 5 / 6）：`SettingsDialog` 1000×680、`Dialog chrome="shell"`、导航四组
（`NAV_GROUPS`）、内容模式 `CONTENT_MODE`（normal 最大宽 640 / wide 铺满）；一行设置是
`settings/SettingRow`（标题列弹性 + 控件列定宽 240、normal 48 / compact 32、`control="fill"`
整行宽；**控件对齐 28px 的标题行而不是整行中线**，`description` / `status` / `illustration`
都在标题列），分区 `SettingSection`（小标题 + 可选说明 + 行间 hairline，不是卡片）；页面
不带页标题、不带外层 gap（`display: contents`），分区间距由外壳给。样式 / 规范页是
「左库（`listRowClass` 行）右编辑器」，规范页顶部四个关键数；`CopyButton` 建在 `Button` 上；
键位提示用 `ui/Kbd`。细则在 Design Constitution 第十二、十三节。**用例里渲染任何含
`IconButton` 的设置页要包 `TooltipProvider`**（与 RegistryDialog.test 同一写法）。

公共 primitive 只在 `components/ui/`：Button / IconButton（长得像按钮的 `<a href>` 用 `ui/buttonClass` 取同一份外观，不手写按钮类名）、TextInput（框内 `suffix`）、
NumberField（框内 `unit`）、Select、Checkbox、Radio、Toggle、Badge、Kbd、Tabs（视图）、`listRowClass`、
TreeRow（`treeIndent` / `TreeChevron` / `TreeIcon` / `TreeCount`）、SearchInput、Notice、StatusPill、Card、
FormSection / FieldGroup、RowMenu、Section / Disclosure / Details、Dialog、Popover、Menu、Tooltip、Segmented（取值）、StepSlider、
EmptyState。**同类控件出现第二套实现先删第二套，不给新写法开豁免。**
跨区域的语义图标在 `ui/semanticIcons.ts`（「可编辑的图」= `EditableFigureIcon`，左轨 / 图层
角标 / 素材卡 / 元素树空态从同一处取）；角色图标在 `inspector/roles/roleIcons.ts`。
图内元素的**归属**（谁挂在谁下面、面包屑里子图与元素之间那一级）只有
`inspector/roles/hierarchy.ts` 一份判据，元素树建树与身份头共用；对象头显示的名字过
`identityCrumbs.displayLabel`（mathtext → 可读文本），源码只在输入框里。首屏字段的分组小
标题由 `RoleProfile.primaryGroups` 声明（曲线 = 线条 / 数据点），不在组件里手排
（2026-09-13 审计 P1 第二批，细则在宪法第十六节）。

工作台结构：顶栏 44px（左=品牌/文档名/autosave，中=撤销重做，右=导出/更多）；
画布工具（选择 / 文字 / 标注 ▾ / 序号 | 适应）在画布底部居中的**浮动工具条**
（`CanvasToolbar`，`data-canvas-toolbar`，只在排版模式出现、快速编辑时整条不在），缩放菜单在画布
标签行最右（快速编辑没有标签行，悬在画布右上角），写回是「⋯」菜单第一项
（`useWriteBackMenuEntry`，计数 n 在项右侧，属性栏「源文件」里那一颗不动）。工具条占掉画布
底边约 64px：底部居中的 toast 列与左下 HUD 在它显示时抬到 `bottom-16`（`useCanvasToolbarVisible`
一处判据），`fixed` 的选中浮动栏 / 右键快编落位时给窗口底边留 `BOTTOM_SAFE`
（`canvas/context-bar/position.ts`），三者都不许盖住工具条上的按钮；
`data-tool` / `data-fit-canvas` / `data-zoom-menu` / `data-write-back` 钩子跟着元素搬，
写回在菜单里，e2e 要先开 `data-more-menu`；
左侧 44px 常驻图标轨道（素材/结构/图内元素）+
280–360px 上下文抽屉（再点收起）；右栏 296–320px 三模式（属性/改图助手/
画布），无选择且未钉住时不占位；断点 ≥1440 双栏可钉住、1024–1439 左右
互斥、<1024 覆盖式抽屉。底部无常驻状态栏：坐标/选区尺寸只在拖动中出现（HUD，左下），
通知只有一条轨（`NotificationRail`，底部居中、最多两条叠着）：普通状态短暂即逝、错误常驻可关、
操作提示可关、「已为编辑加入本文档」带撤销；autosave 显示在顶栏文档名旁。

**图标**（2026-09-06 统一，2026-09-15 换成自绘图标集，ADR 0052；细则 `docs/ux/ICONOGRAPHY.md`）：
全产品只有 `components/ui/icons` 一套（几何 `defs.ts`、工厂 `createIcon.tsx`，142 个名字与
lucide 时代相同），**任何第三方图标库都不许再 import**；尺寸四档 `ICON_SIZE.{xs,sm,md,lg}`
= 12 / 14 / 16 / 20，默认 sm，描边 2 按比例缩放，都由 `components/ui/Icon.tsx` 的
`IconProvider` 在三个根上给。写法 `<X size={ICON_SIZE.md} />`，不写 size 即默认档，不写
strokeWidth；开关 / 激活态写 `filled`（29 个有实心孪生，其余忽略）；折叠 / 下拉箭头一律 xs；
折叠块用 `ui/Details`。不许手写内联 svg 当图标（画用户数据的样本图、品牌标与图标集本体
按个数豁免）、不许引入图标集里没有的名字、不许拿字符 / emoji 当图标、不许裸 `<summary>`
——`iconography.test.tsx` 用 AST 逐条守着。同一语义只用一个图标（表在文档第四节）；加新
图标是在 `defs.ts` 里**画**，不是去别的库挑。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- radius 四档、字号 11–14、控件高 28
- 同类控件第二套先删
- 图标只有一套、加图标是画不是挑
- 装饰记号 `aria-hidden`
- `IconButton` 的用例包 `TooltipProvider`
