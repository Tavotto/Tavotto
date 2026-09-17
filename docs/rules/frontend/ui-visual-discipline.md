# UI 视觉纪律

> 原文出自 `web/AGENTS.md`「UI 视觉纪律」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

全文是 `docs/ux/DESIGN_CONSTITUTION.md`（Paper × Instrument；2026-09-11 Visual
Consolidation Session 1 定稿），值在 `src/index.css` 的 `@theme`，门禁
`components/ui/foundation.test.ts`（类名字面量：像素圆角 / 像素字号 / ink 透明度 hover /
数字时长 / 手拼大写小标题 / 预设投影 / 第二套复选框与开关 / `variant="outline"`）。
这里只留一段速记：

暖灰白 `#F2F2EF` 底 + 白色 surface；层级靠留白 / 字号 / 轻微背景差，
边框只给区域边界、选择状态与浮层；可编辑框是 `field` 底、静态无边（聚焦 accent 边）。**持久表面里只有
「真的是一张卡」的东西有投影（`--shadow-card`：素材卡 / 会话卡 / 任务行 / 诊断卡），浮层用 `--shadow-pop` /
`--shadow-dialog`；改图助手输入框是浮在对话流上的玻璃（`--color-glass` + `backdrop-blur-lg` + `--shadow-composer`）**
（宪法第二十二节，2026-09-15）。
radius 四档：`xs` 3（≤16px 小片）、`sm` 6（控件）、`md` 8（浮层 / 卡片）、`lg` 12（对话框）；
Tailwind 自带的 xl 以上已清空。UI 字号 11-14px（`xs/sm/base/lg`）、六个 `type-*` 字体角色；
控件高 28px、树行高 28px、图标点击区 ≥28px。交互面三档 token：`surface-hover` <
`surface-active` ≈ `selected`（#e6e6e0，轻 tint + 字重，不靠深灰块）。主按钮近黑色
（`bg-ink`）；按钮四档 primary / secondary / ghost / danger；蓝色只用于选择 / 焦点 / 链接；
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
（未选中的分段标签、折叠 summary 都是要读的字，用 `ink-3`）。选中态不只靠颜色（字重 / check /
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

公共 primitive 只在 `components/ui/`：Button / IconButton、TextInput（框内 `suffix`）、
NumberField（框内 `unit`）、Select、Checkbox、Radio、Toggle、Badge、Kbd、Tabs（视图）、`listRowClass`、
TreeRow（`treeIndent` / `TreeChevron` / `TreeIcon` / `TreeCount`）、SearchInput、Notice、
Section / Disclosure / Details、Dialog、Popover、Menu、Tooltip、Segmented（取值）、StepSlider、
EmptyState。**同类控件出现第二套实现先删第二套，不给新写法开豁免。**
跨区域的语义图标在 `ui/semanticIcons.ts`（「可编辑的图」= `EditableFigureIcon`，左轨 / 图层
角标 / 素材卡 / 元素树空态从同一处取）；角色图标在 `inspector/roles/roleIcons.ts`。
图内元素的**归属**（谁挂在谁下面、面包屑里子图与元素之间那一级）只有
`inspector/roles/hierarchy.ts` 一份判据，元素树建树与身份头共用；对象头显示的名字过
`identityCrumbs.displayLabel`（mathtext → 可读文本），源码只在输入框里。首屏字段的分组小
标题由 `RoleProfile.primaryGroups` 声明（曲线 = 线条 / 数据点），不在组件里手排
（2026-09-13 审计 P1 第二批，细则在宪法第十六节）。

工作台结构：顶栏 44px（左=品牌/文档名/autosave，中=撤销重做+工具，
右=缩放/导出/更多）；左侧 44px 常驻图标轨道（素材/结构/图内元素）+
280–360px 上下文抽屉（再点收起）；右栏 296–320px 三模式（属性/改图助手/
画布），无选择且未钉住时不占位；断点 ≥1440 双栏可钉住、1024–1439 左右
互斥、<1024 覆盖式抽屉。底部无常驻状态栏：坐标/选区尺寸只在拖动中出现（HUD，左下），
通知只有一条轨（`NotificationRail`，底部居中、最多两条叠着）：普通状态短暂即逝、错误常驻可关、
操作提示可关、「已为编辑加入本文档」带撤销；autosave 显示在顶栏文档名旁。

**图标**（2026-09-06 统一，2026-09-15 换成自绘图标集，ADR 0052；细则 `docs/ux/ICONOGRAPHY.md`）：
全产品只有 `components/ui/icons` 一套（几何 `defs.ts`、工厂 `createIcon.tsx`，141 个名字与
lucide 时代相同），**任何第三方图标库都不许再 import**；尺寸四档 `ICON_SIZE.{xs,sm,md,lg}`
= 12 / 14 / 16 / 20，默认 sm，描边 2 按比例缩放，都由 `components/ui/Icon.tsx` 的
`IconProvider` 在三个根上给。写法 `<X size={ICON_SIZE.md} />`，不写 size 即默认档，不写
strokeWidth；开关 / 激活态写 `filled`（28 个有实心孪生，其余忽略）；折叠 / 下拉箭头一律 xs；
折叠块用 `ui/Details`。不许手写内联 svg 当图标（画用户数据的样本图、品牌标与图标集本体
按个数豁免）、不许引入图标集里没有的名字、不许拿字符 / emoji 当图标、不许裸 `<summary>`
——`iconography.test.tsx` 用 AST 逐条守着。同一语义只用一个图标（表在文档第四节）；加新
图标是在 `defs.ts` 里**画**，不是去别的库挑。
