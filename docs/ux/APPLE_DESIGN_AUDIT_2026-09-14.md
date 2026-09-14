# 设计审计：Tavotto 界面设计系统（apple-design · audit）

模式：audit ｜ 平台/输入：macOS 桌面 Web UI（Chromium 无头，1512×945 @2x；另 1024×768 / 900×700；zh-CN 为主、en-US 对照；键盘 + 鼠标）｜ 日期：2026-09-14 ｜ 基线：main `de6eeec4`（worktree `tavotto-wt/apple-design-audit`）

> 本文只审计、不改代码。规则等级：**[APPLE]** 官方原则（来源 ID 见 skill `references/sources.md`）· **[WEB]** W3C/WAI · **[TAVOTTO]** 宪法与产品既有决定 · **[PROPOSED]** 本次建议。

## 任务与证据边界

用户希望完成：在改任何页面之前，先知道 Tavotto 的共享原语（token / 基础组件）里哪些东西让界面"不像同一件精密仪器"，以及键盘、状态、图标语义、参数与单位在哪里漏了；目标是精致、克制、自然的桌面科研工具，不是 Apple 官网，不加玻璃、投影和胶囊。

**已检查（真实渲染 + 代码）**

- Token：`web/src/index.css`（`@theme`、`focus-ring`、六个 `type-*`）；`DESIGN.md` 与 `docs/ux/DESIGN_CONSTITUTION.md` 第一至十六节；门禁 `components/ui/foundation.test.ts` 的规则表。
- 全部 27 个共享原语 `web/src/components/ui/*`（Button / IconButton / TextInput / TextArea / NumberField / ColorField / Select / Checkbox / Radio / Toggle / Segmented / Tabs / StepSlider / Dialog / Popover / Menu / Tooltip / Field.Row / Section / Disclosure / Details / TreeRow / listRow / SearchInput / Notice / Badge / Kbd / EmptyState / Icon）。
- 页面（隔离实例 `--figures examples/figures`，共 40 张截图，评审用 16 张存于 `docs/ux/img/apple-design-audit-2026-09-14/`）：工作台起步态 · 素材库 · 图内编辑（整张图 / 标题 / 曲线 / 图例 / 子图 / X 轴刻度 / X 轴标题）· 面板属性页 · 画布页 · 问题面板 · 改图助手（未发任务）· 导出对话框 · 项目接入状态 · 快捷键速查 · 命令面板 · 「更多」菜单 · 图层 / 画布抽屉 · 设置十一页 · en-US 的设置三页 + 检查器三页 + 导出 + 问题面板 · 1024 与 900 宽。
- 交互实测（Playwright 键盘探针）：对话框初始焦点、Segmented / Tabs / 导出范围 / 图例位置选择器的方向键与 Tab 顺序、`focus-ring` 的计算样式。
- 对比度用 WCAG 相对亮度公式对 token 配对计算（不透明色；含 45% 透明叠加后的合成色）。

**未检查（明确列出，不下结论）**

拖动中的 CanvasHud、多选浮动栏、画布右键菜单、错误常驻 toast、色图 / 色条 / 3D 子图 / 柱形 / 散点 / 误差棒面板、素材库空态与筛选、项目选择器、Onboarding coachmark、版本抽屉、论文样式对话框（StyleDialog）、EndpointDialog、包管理「有包 / 安装中」态、更新页「有新版本」态、改图助手的 StepSlider 与差异视图、200% 文字放大、屏幕阅读器（VoiceOver）、WebKit / Windows、深色主题（产品未提供）。上一轮审计（2026-09-13）留给 P2 的条目（B24 / B09 / B10 / B52 图标、B34 / B58 窄窗口库、B36 导出默认设置共用字段组件、B03 画布列表）本轮**未逐条复核**。

**本轮不改**：科研图内容、图中字体与文字、子图布局、图例 `loc` / 锚点 / 锚框、单位换算、导出几何与参数、写回事务；官网 /try 与 Codex 内嵌画布不在范围。

## 核心判断

Tavotto 的视觉系统已经很成熟：一套字体、四档字号、四档圆角、近黑主按钮、蓝色只做小面积、持久表面无投影——这些都在真实渲染里成立，不是文档里的愿望。**不成熟感不来自"还不够 Apple"，而来自三处漏网：**

1. **键盘与焦点在共享原语这一层缺了一角**（观察，实测）：对话框一打开焦点停在右上角 ×；分段选择器和标签页方向键不动、每一格都是一个 Tab 停靠点；焦点环 45% 透明后对任何底色都只有 1.9:1。这些东西在鼠标截图里看不出来，但每个用键盘的人每天都会碰到。
2. **同一件事的第二种写法**仍然存在（观察）：第二套下拉（LineStylePicker）、第二套开关（网格 X/Y）、第二套分段（导出范围）、四种禁用态、两套数值-单位间距、三种计数格式。宪法说"出现第二套先删第二套"，门禁只守住了原生 `<select>` 和类名字面量，守不住"用 button 拼出来的 radiogroup"。
3. **文字角色的区分手段是拉丁专属的**（观察 + 推断）：`type-section` 靠大写和字距，中文没有大写，`0.06em` 在两个汉字上看不见，于是分区小标题（线条 / 数据点 / 刻度与网格）和行标签几乎同形；en-US 截图一眼能分，zh-CN 截图分不出。

以上三条修在原语与 token 上，页面自动受益。页面层的问题（富文本动作行均布、示意图常驻说明、mathtext 只在一处转可读、同一图标用于上下两个方向）多数是 P2 精修，排在原语之后。

---

## 发现 · 一、共享原语与 token（先修）

### S1 · 对话框初始焦点落在「关闭」钮

- 优先级：**P1**
- 位置与状态：`web/src/components/ui/Dialog.tsx:99-158`（`RD.Content` 的 `onOpenAutoFocus` 只记录焦点、不指定落点；`RD.Close` 的 `IconButton` 是内容里第一个可聚焦元素）。实测：导出（⌘E）、设置（左轨齿轮）、快捷键（?）三个对话框打开后 `document.activeElement` 都是 `[data-dialog-close]`；键盘触发时焦点环画在 × 上（图 01 / 02）。
- 证据：`shots` 探针输出 `export-initial-focus "BUTTON|关闭|data-dialog-close=true"`、`settings-initial-focus "BUTTON|关闭|close=true"`。
- 问题原因：打开后第一下 Enter 就是关闭；读屏先念「关闭，按钮」而不是对话框在做什么；导出对话框里要 Tab 两次才到第一个真正的控件。这与 W04「焦点初始位置需符合内容与风险」相反。
- 具体改法 [PROPOSED]：`Dialog` 增加 `initialFocus?: RefObject | 'content'`；默认在 `onOpenAutoFocus` 里 `preventDefault()` 后把焦点给 **正文里第一个非关闭的可聚焦元素**（设置 = 当前导航项；导出 = 范围分段；快捷键 = 搜索框；确认框 = 非破坏性的那颗按钮）。关闭钮在 DOM 顺序上保持在标题栏，但不作为默认落点。
- 保留项：`restoreTo` 关闭还焦逻辑、`busy` / `blockDismiss` / `covered` 三个状态、Esc 契约、`data-dialog` 锚点。
- 验收：键盘打开三个对话框后，`document.activeElement` 不是 `[data-dialog-close]`；`e2e/keyboard-golden-path.spec.ts` 第 6+7 步不再需要先 Tab 过关闭钮；axe 无新告警。
- 依据：WEB W04 · APPLE A05（自定义控件的可理解可操作）· 组件模式 §10。

### S2 · `focus-ring` 45% 透明，对所有底色只有 1.86–1.98:1

- 优先级：**P1**
- 位置与状态：`web/src/index.css:346-349`（`outline: 2px solid color-mix(accent 45%, transparent)`）；实测计算样式 `color(srgb 0.157 0.408 0.718 / 0.45) 2px solid offset=1px`。全部可聚焦原语（Button / Segmented / Tabs / listRow / Toggle / Checkbox / Select / Summary / SearchInput 清除钮）共用它。
- 证据（合成后对底色的对比度）：白 surface 1.98:1、纸底 1.91:1、surface-2 1.93:1、selected 1.87:1、画布灰 1.86:1；同一 accent 不透明时 4.6–5.6:1。图 01 / 02 里的焦点环肉眼即是淡蓝。
- 问题原因：焦点指示是识别「组件状态」所需的非文字视觉信息，W02 要求 ≥3:1；45% 透明在四种底色上全部不达标，而 Tavotto 又刻意去掉了别的蓝色，这一圈是键盘用户唯一的位置线索。
- 具体改法 [PROPOSED]：`outline: 2px solid var(--color-accent); outline-offset: 1px`（不透明，5.6:1）；要柔和感可以再叠一层 `box-shadow: 0 0 0 4px color-mix(accent 20%)` 做光晕，但对比度由不透明的那一圈负责。StepSlider 里的 `peer-focus-visible:ring-accent/50` 同步。
- 保留项：`outline-offset`、只在 `focus-visible` 出现、选中态 / hover 不变、`e2e/contrast.ts` 的量法。
- 验收：`e2e/contrast.ts` 加一条「焦点环合成色 vs 相邻底色 ≥3:1」，对白 / 纸 / selected 三种底各量一次；反证：把 alpha 改回 45% 必红。
- 依据：WEB W02（1.4.11 非文字对比）· 无障碍 §3「focus-visible 在浅色、深色和选中背景上均可辨认」。

### S3 · Segmented 没有方向键与 roving tabindex；同形缺陷复制到了导出范围

- 优先级：**P1**
- 位置与状态：`web/src/components/ui/Segmented.tsx:54-70`（`role="radiogroup"` + 每格 `role="radio"`，没有 `onKeyDown`、没有 `tabIndex` 管理）；`web/src/components/ExportDialog.tsx:746-761` 的 `ScopeButton` 是另一份手拼的 radiogroup，同样没有键盘处理。
- 证据（实测）：对齐分段 `tab=0,0,0`；聚焦「居中」按 → 焦点不动 `checked=true` 仍在居中；按 Tab 落到「右对齐」。导出范围：Tab 到「原图尺寸」、再 Tab 到「当前画布」、按 → `checked=false` 不变。对照：`LegendPositionPicker` / `OptionGrid` 的 16 格里 `tabbable=1`，按 → 直接选中「右侧上」——**正确实现已经在仓库里**。
- 问题原因：radio 组的键盘契约（W07）是一个 Tab 停靠点 + 方向键换值；现在每格一个停靠点，Tab 顺序被分段拉长 3–4 倍，方向键无效，读屏念出「N 之 K」却换不了值。
- 具体改法 [PROPOSED]：给 `Segmented` 加 roving tabindex（选中项 `tabIndex=0`，其余 `-1`；`value` 为 null 时第一项 0）+ `onKeyDown` 处理 ← → Home End（跳过 `disabled` 项，Home/End 到首尾）；从 `OptionGrid` 抽出同一份键盘处理复用。`ExportDialog` 的范围切换换成 `Segmented`（值 `original / canvas`，`disabled` 与 `title` 走现有字段）。
- 保留项：`value === null` 时无一选中（混合值）；`disabled` 项留在原位灰掉、原因走 `title`；`tip` / `ariaLabel`；首尾圆角；导出范围的 `data-onboarding-anchor` 与 `changeScope` 语义。
- 验收：jsdom 用例——聚焦选中格按 → 选中下一格并把 `aria-checked` 与 `tabIndex` 一起挪；Tab 从分段离开只用一步；导出对话框范围段同样通过。反证：删掉 `onKeyDown` 用例必红。
- 依据：WEB W07（radio group 模式）· APPLE A05 · 宪法第五节「Segmented」。

### S4 · Tabs 无方向键、无 `aria-controls` / `tabpanel`；右栏三模式由「两个页签 + 一颗按下的按钮」拼成

- 优先级：**P2**
- 位置与状态：`web/src/components/ui/Tabs.tsx:19-35`（`role="tablist"` / `role="tab"`，无键盘处理、无 roving tabindex）；`web/src/components/inspector/Inspector.tsx:115-130`（「属性 / 画布」是 `Tab`，「改图助手」是 `Button active aria-pressed`）。
- 证据（实测）：两个 tab `tabIndex=0/0`，按 → 焦点不动；`[role=tabpanel]` 数量 0；助手打开时 tablist 里**没有任何 `aria-selected=true`**（图 10）。
- 问题原因：W09 的 tabs 模式要求方向键换页、只有当前页在 Tab 顺序里；一个没有选中项的 tablist 在辅助技术里读作"没在看任何一页"。三个互斥视图（属性 / 助手 / 画布）在语义上是一组，却用了两种控件。
- 具体改法 [PROPOSED]：`TabList` 内部做 roving tabindex + ← → Home End（自动激活即可，切换视图不昂贵）；`Tab` 接 `id` / `aria-controls`，内容容器加 `role="tabpanel"` + `aria-labelledby`。右栏改成三个 tab（属性 / 改图助手 / 画布），助手图标保留为 tab 的前缀；如果产品坚持助手是"叠加态"而不是页，那就让它成为独立面板（`aria-expanded` 的开关 + 自己的 landmark），不要停在 tablist 里没有选中。
- 保留项：`TAB_UNDERLINE` 视觉、`uiStore.autoHideProperties`（无选中切画布）、助手运行中的角标与 `assistantRunningTip`、画布标签栏（只借视觉不借组件）。
- 验收：Tab 到 tablist 只停一次；→ 换到下一页并更新 `aria-selected`；助手态下 tablist 恒有一个 `aria-selected=true`（或助手不在 tablist 里）；`Inspector.test` 断言 `aria-controls` 指向存在的 `tabpanel`。
- 依据：WEB W09（tabs 模式）· 宪法第五节「Tabs 只负责切换视图」。

### S5 · 第二套下拉：`LineStylePicker` 与 `MarkerPicker` / `Select` 三种触发器、两种弹层机制

- 优先级：**P2**
- 位置与状态：`web/src/components/inspector/controls/LineStylePicker.tsx:115-160`（白底 + `border-border` + `text-sm` 12px；`aria-haspopup="listbox"` 但弹层是 `OptionGrid` 的 radiogroup；弹层用 `absolute` 挂在面板里、不经 Portal；自写 pointerdown / Esc 监听）；`MarkerPicker.tsx:289-311`（`bg-surface-2 border-transparent text-xs` 11px；Radix `Popover` Portal）；`ui/Select.tsx`（`bg-surface-2 border-transparent text-xs`）。三者在**同一张曲线属性页**上下相邻（图 04：「线型」白框内容宽，「标记」灰框整行宽）。
- 证据：图 04；`nativeSelect.test.ts` 只判原生 `<select>`，判不到这种手拼下拉。
- 问题原因：用户在同一列看到两种"下拉"——一种像输入框（白、有边）、一种像数字框（灰、无边）；字号也差一档。弹层不经 Portal 会被面板 `overflow` 裁切；`haspopup=listbox` 与实际 radiogroup 不符，读屏预期落空。
- 具体改法 [PROPOSED]：把 `LineStylePicker` 改成与 `MarkerPicker` 同一个外壳（`Popover` + `OptionGrid`，触发器类名同源——最好把「触发器」抽成 `ui/PickerTrigger`，`Select` 的 Trigger 也从它取样式）；`aria-haspopup` 改成 `"dialog"` 或让弹层真的是 listbox；宽度规则二选一（见 A6）。
- 保留项：线型样张 SVG、混合值时「多个值」文案、Esc 还焦到触发器、`OptionGrid` 的方向键与角标勾。
- 验收：曲线页「线型」「标记」两个触发器的 `getComputedStyle` 背景 / 边框 / 字号一致；弹层挂在 `body` 下（`closest('[data-radix-popper-content-wrapper]')` 非空）；`iconography` / `foundation` 门禁新增「`aria-haspopup` 的元素必须来自 `ui/`」的 AST 判据（否则下一个手拼下拉照样进来）。
- 依据：TAVOTTO 宪法第五节「Select 全仓唯一的下拉」「出现第二套先删第二套」· 组件模式 §1。

### S6 · 第二套开关：网格 X / Y 的 `role="switch"` 按钮；关态读作静态文字

- 优先级：**P2**
- 位置与状态：`web/src/components/inspector/controls/TickAndSpineDiagram.tsx:555-598`——`justify-between` 把两颗开关推到右缘、`border-t` 画了一根分隔线、按钮是 `role="switch"` + `bg-selected` 开态、关态只有 ink-2 文字 + 一颗 3px 的空位点，图标是内联 svg。子图页与 X 轴刻度页各出现一次（图 05 / 07）。
- 证据：图 07 放大：「网格   ‖ X 轴   ═ Y 轴」——关态与旁边的行标签同色同字号，没有轨道、没有框。
- 问题原因：宪法第五节「Toggle 唯一的滑动开关」；`Field.Row` 的规矩是控件从同一条竖线起排、不推到右缘；组间「靠留白不画线」。三条都在这一行里破了。状态只靠底色 + 3px 点，关态无法与文字区分（组件模式 §8「保持 on/off 的清楚差异」）。
- 具体改法 [PROPOSED]：这一行改成两个标准 `Row`：`网格 X  [Toggle]` / `网格 Y  [Toggle]`（或一行里两组 `Toggle + 标签`，与「反转 X / Y」那一行同一形态——同一页已经有这个先例）；去掉 `border-t`；图标（如需）走 lucide `AlignVerticalJustifyCenter` 一类，不内联 svg。
- 保留项：`adapter.has/toggle` 语义、示意图实时预览网格（宪法第十五节的决定）、可达名 `gridLabel`。
- 验收：`document.querySelectorAll('[role=switch]')` 里每一个都是 `ui/Toggle` 渲染的（AST 门禁：`role="switch"` 字面量只允许出现在 `ui/Toggle.tsx`）；截图里网格行与「反转」行控件左缘对齐。
- 依据：TAVOTTO 宪法第三、五、八节 · PROPOSED。

### S7 · 禁用态四种写法，其中两处用了宪法明令不用的 `pointer-events-none`

- 优先级：**P2**
- 位置与状态：`Button.tsx:142` `opacity-35 + cursor-not-allowed`；`Checkbox.tsx:32` / `Radio.tsx:28` `opacity-40 + cursor-not-allowed`；`Toggle.tsx:55` `opacity-40`（无 cursor）；`Input.tsx:26` TextInput `opacity-60 + bg-surface-2 + cursor-not-allowed`；`Input.tsx:257` NumberField `opacity-40 + pointer-events-none`；`Select.tsx:51` `disabled:pointer-events-none disabled:opacity-40`；`Menu.tsx:16` `opacity-35`；`StepSlider.tsx:99` `opacity-40`。
- 证据：代码；导出对话框里「应用」「开始导出」「安装」三处禁用按钮观感一致，但 NumberField / Select 禁用时 `title` 解释与 tooltip 一起被 `pointer-events-none` 吞掉。
- 问题原因：宪法第五节写明「disabled 统一 opacity-35~40 + cursor-not-allowed，不用 pointer-events-none」；TextInput 的 60 与其它相差近一倍，禁用输入框看起来像"只读但还能改"。
- 具体改法 [PROPOSED]：定一个 `--opacity-disabled: 0.4` token（或 `@utility disabled-look`），八处统一；NumberField / Select 去掉 `pointer-events-none`，保留原生 `disabled`；TextInput 禁用底色保留 surface-2 但透明度回到 0.4。
- 保留项：Button 的 `aria-busy` 忙碌态、Segmented `disabled` 项的 `title` 原因、Menu `data-disabled`。
- 验收：`foundation.test.ts` 加规则「`opacity-(35|45|50|60)` 与 `disabled:` / `pointer-events-none` 同现即红」，豁免个数写死为 0；反证：把 Select 改回 `pointer-events-none` 必红。
- 依据：TAVOTTO 宪法第五节 · 组件模式 §2「解释原因的文本放在用户可读可聚焦的位置」。

### S8 · 可编辑框两套语法：文字框白底有边，数字 / 选择框灰底无边

- 优先级：**P2**
- 位置与状态：`TextInput`（`BOX_CLASS`：白 + hairline）vs `NumberField`（`bg-surface-2 border-transparent`，`Input.tsx:279`）/ `Select` / `SearchInput` / `MarkerPicker`（同 surface-2）。同一页：标题属性页「内容」白框、「字体」「字号」灰框（图 03）；曲线页「名称」白框、「线宽」「标记」灰框、「线型」白框（图 04）。
- 证据：surface-2 对白 surface 的明度差 1.07:1——灰框在白面板上几乎不存在，可编辑性靠 hover 才浮出边框；截图里数字框只是一块比纸稍暗的影子。
- 问题原因：这是宪法第一节的既定分工（surface-2 = 只读值 / 数字框静态底 / 徽章底），不是失误；但"只读值"与"可编辑数字"共用一个 surface，静态时分不出谁能改。Apple 的做法（A03）是同一密度下所有可编辑字段共用一种边界；macOS 检查器里数值框与文本框长得一样。
- 具体改法 [PROPOSED，需产品决定]：二选一。**甲**：全部可编辑字段统一成 TextInput 的白 + hairline（NumberField / Select / 搜索框改 token，一处改全站受益；只读摘要继续无框，于是"有框 = 能改"成为唯一规则）。**乙**：保留灰框，但把 `--color-surface-2` 对白的差拉到 ≥1.15:1（例如 `#f0f0ec`），并让 TextInput 也用同一灰底——两种之一，不要两种并存。本报告推荐甲：它同时解决 S5 的三种触发器。
- 保留项：单位在框内、数字右对齐、标签拖动改数、`fill` / 定宽、混合值占位、`focus-within:border-accent`。
- 验收：任一属性页里所有 `input / [role=combobox] / [aria-haspopup]` 的静态背景与边框计算值相同；`e2e/contrast.ts` 增加「可编辑框边界 vs 面板底 ≥3:1」（走甲时 hairline `#e3e3dd` 对白只有 1.29:1，需要把输入框边框升到 `border-strong` 或更深——见 S10）。
- 依据：APPLE A03（布局与分组建层级、同类控件同形）· TAVOTTO profile §5「`border-control` 用于识别输入框」· PROPOSED。

### S9 · `type-section` 的区分手段是拉丁专属：中文分区小标题与行标签同形

- 优先级：**P2**
- 位置与状态：`index.css` `@utility type-section`（11px · 500 · `uppercase` · `letter-spacing .06em` · ink-3）；`ElementInspector.tsx:1162` `GroupHead` 用它渲染「线条 / 数据点 / 文字 / 刻度与网格 / 子图尺寸 / 范围与变换」。
- 证据：图 05（zh）与图 06（en）同一页对照：en 的「AXES SIZE / RANGE & TRANSFORM」一眼是分区；zh 的「子图尺寸 / 范围与变换」与下一行「X 范围」只差 1px 字号和一档灰，放大图 04 里「线条」与「颜色」几乎同形。
- 问题原因：`uppercase` 对汉字无效，`.06em` 在两三个字上不可见，剩下的差别是 11 vs 12px 与 500 vs 400——低于可辨阈值。层级只剩上方留白在撑。
- 具体改法 [PROPOSED]：给 `type-section` 一个不依赖字形的第二线索，二选一：**甲** 12px / 500 / ink（与正文同号、更深、有字重）+ 上方 12px 留白；**乙** 保持 11px 但 ink-2 + 500 + 上方留白 12px，行标签改 ink-3 400——让「深 + 重」永远是标题、「浅 + 常规」永远是标签。任选一种后 en-US 的大写字距可保留。
- 保留项：设置导航组名、菜单组标题（`MenuLabel`）、问题面板组头都走同一角色，改一处全站改；`plainTitle` 的内容型标题不受影响。
- 验收：zh-CN 下子图页截图，分区标题与行标签在字号或颜色上至少有一个可量化差异 ≥（2px 或 一档 ink）；`designMd.test.ts` 对拍 `DESIGN.md` 的 `section` 角色同步。
- 依据：APPLE A04（明确文字层级）· 宪法第六节 · PROPOSED。

### S10 · 未选中复选框 / 单选 / 关态开关的边界 1.57:1

- 优先级：**P2**
- 位置与状态：`Checkbox.tsx:30` / `Radio.tsx:26` `border-border-strong`（`#cfcfc7`）；`Toggle.tsx:60` 关态轨道 `bg-border-strong`，白色滑块压在上面。
- 证据：`#cfcfc7` 对白 1.57:1、对纸底 1.40:1；图 14「固定左侧栏」关态开关在 48px 行里只剩一枚浅灰胶囊；导出对话框 EPS / TIFF 未选框同样淡。
- 问题原因：W02 对识别控件所需的边界要求 ≥3:1；复选框的方框、开关的轨道就是它们的全部识别信息。选中态（近黑）没问题，问题只在"关 / 未选"。
- 具体改法 [PROPOSED]：新增 `--color-border-control: #8a8a82`（对白 3.48:1、对纸 3.10:1、对 surface-2 3.24:1，三种底都过 3:1）专给控件边界：Checkbox / Radio 未选边框、Toggle 关态轨道、（若采纳 S8 甲）输入框边框。`border-strong` 继续给 hover 输入框和区域边界。
- 保留项：14px 尺寸、xs 圆角、选中近黑 + 白勾、开关 28px 点击区、`aria-checked`。
- 验收：`e2e/contrast.ts` 增加「复选框 / 单选 / 开关的边界 vs 相邻底色 ≥3:1」；截图对照 30-settings-interface。
- 依据：WEB W02 · profile §5「`border-subtle` 与 `border-control` 分开」。

### S11 · 数值与单位的间距两套并存

- 优先级：**P2**
- 位置与状态：i18n zh-CN：`{{x}}pt` 无空格 21 处、`{{x}} pt` 6 处；`{{x}}mm` 7 处、`{{x}} mm` 22 处（en-US 同样 21 / 6）。`NumberField` 框内单位视觉上有间隙；`validationText.ts:77-83` 的 `unit` 由消费方各自拼接。
- 证据：图 09 问题面板同一屏：「7.33pt → 大于 8pt」与「有元素超出图幅 1.87 mm」；图 13 en-US 导出对话框却是「7.33 pt → above 8 pt」。
- 问题原因：参数与单位的排版是科研工具的门面；两种写法在一屏里出现，读者会怀疑是两个不同的量。
- 具体改法 [PROPOSED]：在 `i18n/format.ts` 加 `fmtQuantity(value, unit)`：`pt / mm / px / ppi` 一律窄空格或普通空格（与 NumberField 框内一致），`% / °` 不空格；i18n 文案里的 `{{x}}pt` 改为 `{{x}}`，单位交给 formatter；`validationText.issueValues` 只从这一处出。
- 保留项：`%g` 精度、`VALUES` 表的边界包含性、`mmSize` 的 `×`。
- 验收：`grep -c '}}pt\b' i18n/locales/*/*.json` 为 0；问题面板 / 导出清单 / 检查器 hint 三处同一条规则的字符串逐字相同（只差语言）。
- 依据：TAVOTTO profile §6「输入精度、单位来自业务层」· PROPOSED。

### S12 · 计数格式三种

- 优先级：P3
- 位置与状态：抽屉标题 `图内元素 24` / `图 3` / `已关联 2`（type-meta 数字，读屏另有全文）；`图例项（2）` `可编辑（3）` `全部脚本（3）` `内置包（1 个，只读）`（全角括号）；`工具与配置脚本 (1)`（半角括号）；问题抽屉标题 `问题 13` 与左轨角标 `14` 同屏（一个按当前页签、一个按整个文档）。
- 证据：图 15 / 08 / 11；i18n 里 `（{{count}}）` 系列 12 处。
- 具体改法 [PROPOSED]：抽屉 / 分区标题统一为「名字 + type-meta 数字」（宪法第九节已定），括号形式只保留在句子里（「N 个，只读」）；半角括号那一处改全角；问题抽屉标题跟角标一致（都写文档总数，页签上各自计数）。
- 保留项：读屏的完整句子（`groupAria` 等）。
- 验收：`grep ' ({{' i18n/locales/zh-CN` 为 0；截图三处标题形态一致。
- 依据：PROPOSED（一致性）。

### S13 · NumberField 的键盘与指针修饰键不对称；无 spinbutton 语义

- 优先级：P3
- 位置与状态：`Input.tsx:230-237`（scrub：Shift ×10、Alt ×0.1）vs `Input.tsx:313-318`（↑↓：只有 Shift ×10，无 Alt）；`<input type="text" inputMode="decimal">` 无 `role="spinbutton"` / `aria-valuemin/max`。
- 具体改法 [PROPOSED]：↑↓ 加 Alt ×0.1（与 scrub 同表）；`role="spinbutton"` + `aria-valuenow/min/max/text` 是可选加分，加了要保证草稿态文本仍可输入 `-`、`.`。
- 保留项：Enter 提交失焦、Esc 还原、Tab 路过不提交、混合值占位。
- 验收：jsdom：Alt+↑ 步进 0.1×step；反证删掉必红。
- 依据：组件模式 §5 / §6 · WEB W08 精神。

### S14 · 59 处 `Tip + Button size="icon"` 绕过 `IconButton`

- 优先级：P3（代码卫生，用户不可见）
- 位置与状态：`grep size="icon"` 59 处 vs `<IconButton` 35 处；其中 Tip `label` 与 `aria-label` 表达式不同 21 处（多数是"名字 vs 说明"的合法分工，但没走 `IconButton tip="…"` 通道，两份文案在两处维护）。
- 具体改法 [PROPOSED]：凡不带 `active` / `aria-pressed` 的图标钮迁到 `IconButton`（`tip` 传说明句）；带 pressed 语义的可给 `IconButton` 加 `pressed?: boolean`。
- 验收：AST 门禁：`size="icon"` 只允许出现在 `ui/Button.tsx`。
- 依据：TAVOTTO 宪法第四节。

### S15 · 树行标签是引擎按字数截断，不按可用宽度

- 优先级：P3
- 位置与状态：元素树「X 轴 “Reaction time (mi…”」「曲线 “Catalyst (k = 0.1…”」在 380px 抽屉里只占一半宽就截断（图 16 / 70）；`identityCrumbs.untruncatedLabel` 只在身份头把它还原。
- 具体改法 [PROPOSED]：树行、问题行、导出清单用完整 label + CSS `truncate` + `title`；引擎侧的固定截断只做兜底。
- 保留项：读屏 `aria-label` 用完整文本；有区分力的部分（k 值）不丢。
- 验收：380px 抽屉里同一行显示的字符数 ≥ 现在的 1.5 倍；两条曲线名仍可区分。
- 依据：组件模式 §4「长名字保留有区分力的部分」。

---

## 发现 · 二、页面（原语修完再做）

### A · 图内编辑 / 检查器

**A1 · 富文本动作行四颗图标钮 `justify-between` 均布** — P2 — `components/inspector/TextActions.tsx:64`。图 03 / 04：↵ x² x₂ Aa 各占控件列四分之一，读作四个互不相干的东西。改法：`flex gap-1` 左对齐、跟在输入框左缘（或作为输入框 `suffix` 内的工具条）；保留四个动作与 `onPointerDown preventDefault` 的不抢焦点。验收：四颗钮间距 4px、左缘与输入框对齐。[APPLE A03 分组表达层级 · PROPOSED]

**A2 · 「文字」组两种布局** — P2 — 文本角色（标题 / 轴标题 / 图例）：`字体 + 字号` 一行 → `B I` → `颜色`（图 03 / 08）；刻度角色：`字号 → 颜色 → 数值格式 → 旋转 → 显示 → 字体（整行宽，排在最后）`（图 07）。ADR 0032 说控件只有 `TypographyControls` 一份，但字段顺序与行布局由各角色模板决定，于是同一组属性在两页上顺序相反。改法：`RoleProfile` 里「文字」组固定顺序 `字体 · 字号 · 字重/字形 · 颜色`，刻度页缺 B/I 就跳过那一行，不换序；`字体` 的宽度规则与 A6 一致。保留：刻度页特有的数值格式 / 旋转 / 显示。验收：三种角色页「文字」组前四个字段顺序一致。[TAVOTTO ADR 0032 · PROPOSED]

**A3 · mathtext → 可读文本只在身份头生效** — P2 — `identityCrumbs.displayLabel` 只被 `Inspector.tsx:336` 调用；`LegendCard.tsx:105` 图例项列表、问题面板、导出清单、上下文栏面包屑都显示原始 `$\mathrm{min^{-1}}$`（图 08「Catalyst (k = 0.125 $\mathrm…」；图 13 导出清单同）。改法：`displayLabel` 下沉到 label 的生产处（`identityCrumbs` 之外给一个 `readableLabel(el)`，树 / 问题 / 导出 / 图例项统一取）；源码只在「名称 / 内容」输入框里。保留：输入框里的原文、`untruncatedLabel`。验收：搜索整仓 UI 文本节点里 `\\mathrm` 出现 0 次（输入框除外）。[TAVOTTO 宪法第十六节 · PROPOSED]

**A4 · 同一图标 `CornerUpLeft` 用于三个方向；面包屑不可点，「所属子图」行重复面包屑** — P2 — `ElementInspector.tsx:587`（子图页「↰ X 轴刻度 / ↰ Y 轴刻度」= 往下到子元素）、`:2725`（「↰ 选择宿主子图」= 往上）、刻度页「↰ 所属子图：子图 1」（往上，且与头部面包屑「Fig1_kinetics / 子图 1」是同一信息；图 05 / 07）。改法：面包屑每一级做成可点的 ghost 链接（往上的路径就此消失，「所属子图」行删除）；往下的入口用 `ChevronRight` 尾随的列表行（「X 轴刻度 ›」），不用回转箭头。保留：`setSelectedGid` 目标、`hierarchy.containerGid` 判据、可达名。验收：刻度页没有「所属子图」行；面包屑「子图 1」可键盘激活；`CornerUpLeft` 在检查器里出现次数为 0（`ICONOGRAPHY.md` 第四节登记「跳转到相关对象」的唯一图标）。[TAVOTTO ICONOGRAPHY「同一语义只用一个图标」 · APPLE A02 清楚的位置与行动]

**A5 · 刻度示意图常驻说明 + 同一示意图在两页各出现一次** — P2 — `TickAndSpineDiagram.tsx:552`「点四条边切换刻度线与边框」（`data-tick-diagram-caption`）在子图页与 X 轴刻度页都在；示意图 190×135px，占面板约五分之一（图 05 / 07）。用户既定标准是"常驻说明与术语提示不要"。改法：说明改成示意图的 `aria-description` + 首次 hover 边缘时的 tooltip（「点击切换 X 下边刻度」）；边缘 hover 给可见的高亮态（现在只有点击后才变）；刻度页只保留示意图或只保留字段（一处编辑一份状态），另一处给「在子图页编辑边框 ›」链接。保留：四边联动 / 逐边差异的语义、混合值不被统一预览覆盖（组件模式 §9）。验收：截图里没有常驻说明；hover 边缘有可见反馈；同一份 `spine_*` 状态只在一个页面上可编辑。[TAVOTTO 用户标准「打磨是做减法」 · 组件模式 §9 · PROPOSED]

**A6 · 下拉宽度两种规则** — P3 — 曲线页「线型」内容宽（`LineStylePicker` 的 `w-full` 被外层限宽）、「标记」整行宽；文本页「字体」126px 与「字号」并排、刻度页「字体」整行宽。改法：`Select` / picker 默认整行宽（`fill`），与另一个字段并排时由 `PairRow` 决定；数字框继续包住数字。验收：同一页所有单独成行的下拉右缘对齐。[PROPOSED]

**A7 · 图例位置：常驻说明 + 「锚点」折叠推到右缘** — P3 — 「放到外面可能超出图幅，检查会提示。」常驻在选择器下方；「锚点 ⌄」`ml-auto`（图 71）。改法：说明并进外侧格子的 tooltip / 选中外侧时才出现一行 status；「锚点」用 `Disclosure` 左起排。保留：`loc` / 锚点 / 锚框语义、外侧定位能力（组件模式 §7）。[PROPOSED]

**A8 · 同一位置两套词汇：浮动栏「左上」 vs 选择器「右侧上」** — P2（需产品确认）— 图 71：图例放到子图外右上后，画布浮动栏显示 `loc` 的角（左上），右栏显示空间名（右侧上）。两处都"对"，但用户看到的是矛盾。改法：浮动栏也用空间名（`LegendPositionPicker` 的十六个可达名就是这套词汇），`loc` 只在「排版详情」里出现。保留：`loc` 与 `bbox_to_anchor` 写回值。验收：同一状态下两处文案相同。[TAVOTTO 宪法第十六节「三处同一个词」精神 · PROPOSED]

**A9 · <1024 覆盖态右栏盖住上下文栏的主动作与出口** — P2 — 图 12（900 宽）：右栏以覆盖层出现时，顶部上下文栏的「添加到画布」按钮与说明被盖掉，「返回画布 Esc」只剩一半。`context-bar/position.ts` 的避让只算画布内对象，不算覆盖层。改法：上下文栏在覆盖态按右栏宽度让位（最大宽 = 视口 − 右栏 − 边距），或覆盖态下把主动作折进 ⋯。保留：Esc 出口、面包屑、B47 的对象避让。验收：900 宽截图里「添加到画布」完整可见可点。[APPLE A02 清楚的位置与行动 · PROPOSED]

### B · 导出对话框

**B1 · 输出范围用两颗按钮拼 radiogroup** — P2 — `ExportDialog.tsx:746-761` + `ScopeButton`（无方向键，见 S3）。改法：换 `Segmented`；「原图尺寸」不可选时 `disabled + title`。验收：同 S3。[WEB W07 · 宪法第五节「作用范围用 Segmented」]

**B2 · ppi 下拉没有可见标签，坐在「文件名」标题下** — P2 — `ExportDialog.tsx:826-834` 只有 `ariaLabel`；图 01 / 13：`[Fig1_kinetics ……… 600 ppi ⌄]` 像文件名的一部分。设置 › 导出页同一控件有「分辨率」标签（图 30-settings-export）。改法：给它自己的 `Row`（「分辨率」），或至少一个可见小标签；只在选了位图格式时出现的规则不变。保留：`PPI_VALUES`、`raster` 条件、`ppi` 只在有位图格式时是数字。验收：`getByLabelText('分辨率')` 可见且与下拉关联。[APPLE A02 · 组件模式 §11「格式相关项按条件显示」]

**B3 · 「已知悉上述 {{errors}} 类阻断性问题」传的是条数** — P2 — `ExportDialog.tsx:970-972` 传 `errors.length`（6 条 · 实际 2 类），zh-CN `dialogs.json:21-23` 写「类」；en-US 写 "6 blocking problems" 是对的。改法：zh 文案改「N 条」或传 `kinds` 数；两语言同一语义。验收：`settingsCopy.test` 一类的文案用例钉住「条」。[TAVOTTO 宪法「诊断不夸大风险 / 与真实规则一致」]

**B4 · 三句话解释同一件事；检查清单规则名重复五次** — P3 — 图 01：「磁盘上的原件是…按图幅…出图。」「按这张图自己的尺寸出图，不受它在画布上摆放的影响。」「画布上的缩放不会带进导出。」三句都在说"按原图尺寸"，而范围分段已经选着「原图尺寸」。清单「字号低于绝对下限 · 图例 / · 图例项 / · X 轴刻度…」规则名重复 5 行，问题面板已按规则分组。改法：只留第一句（含两个尺寸的事实句），其余进「要按原图尺寸导出的图」折叠；清单按规则分组「字号低于绝对下限 ×5 › 定位」。保留：磁盘原件与图幅不一致这一事实（它是用户要知道的）、「还有 N 条」入口、确认勾选。[TAVOTTO 用户标准「已表达过的不重复」 · 组件模式 §11]

### C · 问题面板

**C1 · 每条问题下一行独立的「技术详情」折叠** — P3 — 图 09：5 条同类问题 = 5 行「› 技术详情」。改法：技术详情做成行尾 `IconButton`（`Info`）触发的 Popover，或整组一个折叠；保留 gid / 规则码只在这里出现的纪律。[PROPOSED]

（单位间距归 S11；组头 / 筛选条 / 自动修复行形态与宪法第十节一致，未发现问题。）

### D · 设置

**D1 · 编码 Agent「当前默认 / 设为默认」同一位置一会儿是状态一会儿是动作** — P2 — `settings/AgentList.tsx:92-105`：默认项渲染成 `Button active`（按下态、不可点），其它项是「设为默认」动作（图 11）。组件模式 §2「动作不应伪装为持久选中态」。改法：行首 `Radio`（组名「默认助手」，与 EndpointDialog 的模型服务同一控件），行尾只留可用性徽标 + 开关 + ›；或行尾 `Badge 默认` + 非默认项 ghost「设为默认」。保留：不可用 Agent 不能设默认、`currentDefaultAria`。验收：默认项不再是 `button[data-active]`；键盘 ↑↓ 可换默认。[组件模式 §1 / §2 · 宪法第十三节「单选用 ui/Radio」]

**D2 · 界面页「移动子图时…」一行三种辅助** — P3 — `?` 弹层 + 一句说明 + 示意图（图 14）。用户标准"常驻说明与术语提示不要"。改法：留示意图 + 一句说明，`?` 弹层的内容并入说明或删掉。[PROPOSED]

**D3 · 诊断页两颗同权重 secondary + 脱离 SettingRow 网格** — P3 — 「复制诊断」「导出诊断包」并排等权，动作贴在标题列而非控件列（宪法第十三节允许管理页各自 IA）。改法（可选）：主路径「导出诊断包」secondary、「复制诊断」ghost；两颗放进控件列。[PROPOSED]

### E · 改图助手

**E1 · `⌘↵` 提示用 ink-faint（2.5:1）** — P3 — `AiPanel.tsx:355`；它是发送快捷键唯一常显的位置（tooltip 是第二处）。改法：`Kbd size="sm"`（ink-3）。[WEB W01 精神 · 宪法「faint 不用于要读的字」]

**E2 · StepSlider 56px 拇指 / 48px 轨道** — 未截图（未检查）— 代码 `ui/StepSlider.tsx:100-104` 给的是触屏尺度，宿主是 296–320px 的桌面面板；`StepSlider.tsx:34` 注释里的颜色（`#2868b7`）与实现（`bg-ink`）已不一致。建议下一轮真机看一眼再定：桌面档可用 28px 轨 + 20px 钮，命中区靠 padding 撑到 28px。[PROPOSED · profile §4「不把 iPhone 的触摸尺寸套到桌面检查器」]

### F · 左栏 / 顶栏 / 素材库

本轮未发现原语之外的问题：树行四列对齐、hover 出 ⋯、素材卡两行文字、脚本行状态点、顶栏一个填色主动作，都与宪法第九节一致。计数格式归 S12，标签截断归 S15。

---

## 分批实施

**批次 1 · 共享原语（一个 PR，页面零改动即受益）**
S1 Dialog 初始焦点 → S2 focus-ring 不透明 → S3 Segmented 键盘（顺手 B1 换 Segmented）→ S4 Tabs 键盘 + 右栏三模式 → S7 禁用态统一 → S10 `border-control` token。
依赖：S2 与 S10 一起改 `e2e/contrast.ts` 的量法；S3 从 `OptionGrid` 抽键盘处理。
风险：S4 改右栏模式会碰 `uiStore.autoHideProperties` 与 e2e `inspector-redesign.spec`；S7 会让禁用 Select 重新收到 hover（这是想要的）。
门禁同批加：`role="switch"` / `role="radio"` / `aria-haspopup` / `size="icon"` 字面量只允许出现在 `ui/`（AST），每条先反证一次。

**批次 2 · 回收第二套实现**
S5 LineStylePicker → 与 MarkerPicker 同壳 → S6 网格开关 → Toggle → S8（产品先拍板甲 / 乙）→ S11 单位 formatter → S12 计数格式 → A1 动作行。
依赖：S8 的选择决定 S5 触发器长什么样，先拍板再动手。

**批次 3 · 检查器页**
S9 type-section 中文线索 → A2 文字组顺序 → A3 `readableLabel` 下沉 → A4 面包屑可点 + 图标 → A5 示意图说明与双处编辑 → A6 宽度规则 → A7 / A8 图例位置文案 → A9 覆盖态避让 → S13 / S15。
代表页：曲线页（S8/S5/A1/A6）、子图页（S9/A4/A5）、图例页（A3/A7/A8）各截一张前后对照。

**批次 4 · 导出 / 设置 / 助手**
B2 ppi 标签 → B3 文案 → B4 做减法 → D1 Agent 默认项 → D2 / D3 → C1 → E1 → E2 真机看过再定。

每批的验收硬门槛：`pnpm test && pnpm build` 绿；`e2e/a11y.spec` + `keyboard-golden-path` + `contrast` 本地各跑一次；每条新增判据先删掉修复看它红；导出 PDF/PNG 的 `ExportRequest` 与写回产物逐字节不变（这些批次不碰引擎）。

## 未验证与分歧

**分歧 1 · 11px 控件文字（type-control / Select / NumberField 值）**
[APPLE A04] macOS 的默认文字 13pt、最小 10pt，是平台指导不是 CSS 处方；[TAVOTTO] 宪法第六节与 2026-09-13 审计的处置明确拒绝改成 13/20。本轮不作为发现，只记录：用户要**读**的值（「9 pt」「衬线」「600 ppi」）现在是 11px，说明文字也是 11px；profile §4 的候选是 13–14px 操作文字。建议只做一个小实验：把 `type-control` 与 Select / NumberField 的值提到 12px（正文档），标签留 11px，看密度是否仍在 28px 行里成立——这是产品决定，不是缺陷。

**分歧 2 · 数字框的"安静灰框"**（S8）
是宪法第一节的既定分工，Apple 的检查器则是同一密度下所有字段同一种边界。两种都成立；不成立的是两种并存。需要产品拍板甲 / 乙。

**分歧 3 · Toggle 24×14 的迷你尺寸**
点击区已是 28px，语义正确；视觉上比 macOS 的 NSSwitch（38×22）小一半，在 48px 设置行里显得轻。是风格选择，不列缺陷；若改，只改视觉尺寸，不改 28px 命中区。

**分歧 4 · 图例位置「左上」 vs 「右侧上」**（A8）
需要产品确认浮动栏那一格到底想表达 `loc` 还是空间位置；本报告只指出两处不一致。

**未验证事实**
- 屏幕阅读器实际朗读（VoiceOver / NVDA）一次都没跑；S1 / S3 / S4 的读屏影响是从 DOM 语义推断的。
- WebKit 上 `focus-visible` 的触发条件与 Chromium 不同，S2 的观感需要在 WKWebView 再看一次。
- 200% 文字放大、`prefers-reduced-motion`、`forced-colors` 未测。
- S9 提出的两种 token 值没有做过 zh / en 双语的真实渲染对照，只有 zh 的问题证据与 en 的对照证据。

## 附：本次截图索引（`docs/ux/img/apple-design-audit-2026-09-14/`）

01 导出对话框初始焦点在 ×（zh） · 02 快捷键对话框初始焦点在 × · 03 标题属性页（zh） · 04 曲线属性页（zh，三种下拉 / 两套框） · 05 子图页（zh） · 06 子图页（en，分区标题对照） · 07 X 轴刻度页（示意图说明 / 网格开关 / 分隔线） · 08 图例页（原始 mathtext / 位置选择器） · 09 问题面板（单位间距） · 10 助手态的右栏头部（tablist 无选中） · 11 编码 Agent 设置（当前默认 / 设为默认） · 12 900 宽覆盖态盖住上下文栏 · 13 导出对话框（en） · 14 界面设置（关态开关） · 15 工作台起步态 · 16 图内编辑态。

原始 40 张 @2x PNG 与探针脚本 `shot.mjs` / `s01–s07.json` 在本次会话的 scratchpad，未入库。

## 拍板记录（2026-09-14，用户决定）

| 项 | 决定 |
| --- | --- |
| S8 可编辑框语法 | **甲**：全部白底 + hairline（NumberField / Select / SearchInput / picker 迁到 TextInput 的框；边框用 `border-control`），只读摘要继续无框 |
| S9 分区小标题 | **甲**：`type-section` 改 12px / 500 / ink，上方留白 12px；en-US 的大写字距保留 |
| S4 右栏三模式 | **三个 tab**（属性 / 改图助手 / 画布）；`autoHideProperties` 逻辑不变 |
| A8 图例位置词汇 | 浮动栏改用**空间名**（与 `LegendPositionPicker` 同一套）；`loc` 只在「排版详情」 |
| D1 Agent 默认项 | **行首 `ui/Radio`**（组名「默认助手」），行尾只留可用性徽标 + 开关 + › |
| 分歧 1 控件字号 | **只把「要读的值」试到 12px**（`type-control` 与 Select / NumberField / picker 的值），标签 / caption / meta 留 11px，28px 不变；截图看过密度再定去留 |
| 分歧 3 Toggle 尺寸 | **保持 24×14**，只修 S10 的关态轨道对比度 |
| A5 刻度示意图 | **子图页留示意图，刻度页只留字段 + 「在子图页编辑边框 ›」**；常驻说明改 `aria-description` + 边缘 hover 反馈 |
| S1 对话框初始焦点 | **落在对话框容器本身**（`role=dialog` 容器 `tabIndex=-1`），不预选控件；读屏先念标题与说明 |
| 下一步 | **立即实施批次 1**（S1 S2 S3 S4 S7 S10 + AST 门禁），报告先单独提交；批次 2–4 另开 |

## 批次 1 落地记录（2026-09-14）

分支 `ux/apple-design-audit`，提交「共享原语批次 1」。改动与验收：

| 发现 | 改法（已落地） | 验收（实测） |
| --- | --- | --- |
| S1 Dialog 初始焦点 | `onOpenAutoFocus` 后焦点给 `role=dialog` 容器；关闭钮 DOM 移到正文与脚部之后、视觉钉在右上角 | 导出 / 设置 / 快捷键三处 `activeElement` = 容器；第一下 Tab 分别落到范围分段 / 当前导航项 / 搜索框；Esc 后焦点回到树行 |
| S2 focus-ring | `outline: 2px solid var(--color-accent)`（不透明）；StepSlider 同步 | 计算样式不再带 alpha；截图里焦点环是实心蓝 |
| S3 Segmented 键盘 | roving tabindex + ← → Home End，禁用项跳过；导出范围换 `Segmented` | 对齐分段 tabIndex `-1,0,-1`、→ 选中下一格；导出范围 → / ← 换值 |
| S4 Tabs 键盘 + 右栏三页签 | `TabList` 方向键自动激活、`Tab` 带 `panelId` / `aria-controls`、新 `TabPanel`；右栏「属性 / 改图助手 / 画布」三个页签（ADR 0010 §3 修订） | 三个 tab 各带 `aria-controls`，→ 切到助手且 tabpanel id 对上；en-US 头部宽 360 无溢出 |
| S7 禁用态 | 全站 `opacity-40 + cursor-not-allowed`；NumberField / Select 去掉 `pointer-events-none` | 门禁两条 |
| S10 border-control | 新 token `#8a8a82`；Checkbox / Radio 未选边框、Toggle 关态轨道 | 关态开关在设置页可见（截图） |
| 门禁 | `foundation.test` 五条新规则（`role="radio"` / `aria-haspopup` 只在 ui/，LineStylePicker / StrokeSection 豁免到批次 2；disabled 只有 40；disabled 不配 pointer-events-none；`ring-accent/N` 不许）；`keyboardPrimitives.test` 七条 | 11 条变异逐条红（Segmented 删 onKeyDown / 停靠点全 0、Tabs 删方向键 / 停靠点 / aria-controls、Dialog 不聚焦容器、Button 35、Input pointer-events-none、StepSlider ring/50、ExportDialog 手拼 radio、新写 aria-haspopup） |

顺手：`multiSelectionBar.test` 原来断言「每个按钮 tabIndex ≥ 0」——那句话把缺陷钉成了规范，改成「radiogroup 只有一个停靠点」。

**没做（留给后续批次）**：ProblemPanel / TickTaskCard / VersionDialog 的 tablist 已得到方向键，但内容区还没套 `TabPanel`（批次 3 顺手）；S5 / S6 / S8 / S9 / S11 / S12 与页面项按拍板记录进批次 2–4。

## 批次 2 落地记录（2026-09-14）

分支 `ux/apple-design-batch2`（stacked 在批次 1 上）。拍板：S8 甲 + 边框 A 档 `#8a8a82`；S12 不做。

| 发现 | 改法（已落地） | 验收（实测） |
| --- | --- | --- |
| S8 可编辑框两套语法 | `ui/fieldBox.ts` 一份框（白底 + `border-input #8a8a82`，hover ink-3，聚焦 / 打开 accent）；TextInput / TextArea / NumberField / Select / SearchInput 共用；新 token 进 DESIGN.md 与宪法第一、五节 | 标题页 / 曲线页截图：名称 / 字号 / 字体 / 线宽 / 线型 / 标记同一副框；`tokenContrast.test` 钉 ≥3:1 |
| S5 三种下拉触发器、两种弹层 | `inspector/controls/PickerTrigger.tsx` 一份触发器；LineStylePicker、StrokeSection 的箭头端型 / 线型、Marker / Hatch / Colormap 五个都走 `Popover + PickerTrigger + OptionGrid`；`OptionGrid.onPick` 点选才收起、方向键漫游不收；`aria-haspopup` 两条豁免删除 | 曲线页「线型」打开：portal 弹层、触发器 accent 边 + 箭头朝上；`pickers.test` 46 条含「漫游不收 / 点选收」 |
| S6 网格 X / Y 第二套开关 | `Row + Toggle`（与「反转 X / Y」同形），去掉 `border-t` 与 `justify-between`，标签列跟 `LABEL_W`；`role=switch` 豁免 3→2、内联 svg 豁免 2→1 | 刻度页截图 |
| S9 type-section 中文同形 | 12px / 500 / ink（DESIGN.md typography.section、宪法第六节同步） | 曲线页「线条 / 数据点」、设置导航组名一眼可分 |
| S11 数值 - 单位两套间距 | `formatQuantity`；locale 54 处 `{{x}}pt` / `{{x}}mm` 统一成 `{{x}} pt` / `{{x}} mm`；`validationText` / `PanelSection` 走它；用例里钉住的「7.50pt」改「7.50 pt」 | 问题面板截图「7.33 pt → 大于 8 pt」；`pnpm i18n:check` 绿 |
| A1 富文本动作行均布 | `gap-1` 靠左 | 标题页截图 |
| 顺手 | `tokenContrast.test` 第一次跑就抓到 `ok #2e7d4f` 在 ok-subtle 上 4.41:1 → `#2b7649` | — |

变异反证：focus-ring 回 color-mix / border-input 回 #cfcfc7 / border-control 回 #cfcfc7 / ok 回 #2e7d4f / LineStylePicker 手写 aria-haspopup / OptionGrid 删 onPick——六条全红（最后一条第一次跑是绿的，补了用例再红）。

**接受的差异**：S12 抽屉标题「名字 + 小号数字」与分区标题「名字（N）」两种计数形态各自一致、语义不同，不统一。

## 批次 3 落地记录（2026-09-14）

分支 `ux/apple-design-batch3`（stacked 在批次 2 上）。

| 发现 | 改法（已落地） | 验收 |
| --- | --- | --- |
| 分歧 1 控件里的值 | `type-control` / `fieldBox` / `Segmented` / `OptionGrid` / `Select` 选项 / `num-input` 12px；标签 / caption / meta 留 11；DESIGN.md `control` 同步 | `designMd.test` |
| A2 文字组两种顺序 | `ticks` 角色 primary 加 `fontfamily`：字体 → 字号 → 颜色，与文本角色同序（并排 / 分行的布局差异接受） | 刻度页 |
| A3 mathtext 只在一处可读 | `roles/mathtext.ts`，`displayLabel` 挂在 `engineLabel` 出口；LegendCard 条目、QuickEdit 头同步 | `registry.test`「min⁻¹」；变异红 |
| A4 同一图标三个方向 + 面包屑不可点 | 面包屑祖先各是按钮（`data-crumb`）；`relatedGids` 删「所属子图 / 所属系列」；去到链接尾随 `ChevronRight`；`CornerUpLeft` 退场 | `identityHeader.test`「面包屑可点」；变异红 |
| A5 示意图两处 + 常驻说明 | 刻度页只留字段 + 「在子图页编辑刻度线与边框 ›」；说明 `sr-only` + `aria-describedby` | `tickTaskCard.test` / `axesPage.test`；变异红 |
| A6 下拉宽度 | 随 S5 的 `PickerTrigger`（w-full）自然解决 | 曲线页 |
| A7 锚点折叠推到右缘 | 左起排 | 图例页 |
| A8 浮动栏「左上」 vs 选择器「右侧上」 | 浮动栏落在外侧预设上时用空间名（`outsidePresetOf`） | `elementBar.test`；变异红 |
| A9 覆盖态盖住浮条 | `WorkspaceContextBar` 按覆盖式侧栏宽度留边 | `workspaceContextBar.test`；变异红 |
| S13 键盘缺 Alt | NumberField ↑↓ Alt ×0.1 | `numberField.test`；变异红 |
| S15 树行按字数截 | `untruncatedLabel` + CSS truncate | `elementTree.test`；变异红 |
| S4 收尾 | ProblemPanel / TickTaskCard / VersionDialog 的页签带 `panelId`、内容区 `TabPanel` | 现有用例 |

## 批次 4 落地记录（2026-09-14）

分支 `ux/apple-design-batch4`（stacked 在批次 3 上）。

| 发现 | 改法（已落地） | 验收 |
| --- | --- | --- |
| B2 ppi 无可见标签 | `<label>` 包住 Select，标签「位图分辨率」 | 导出对话框截图 |
| B3「6 类阻断性问题」 | zh 四条文案「类」→「条」（en 本来就对） | `ExportDialog.test` |
| B4 三句解释 + 规则名重复五次 | 删 `scopeCanvasNote` / `scopeOriginalNote`（只留磁盘原件与图幅不一致、忽略的画布变换、占位值、为什么灰）；阻断清单按规则分组（`data-blocking-group`，组头一次 + `N 项`），行里主语 · 数值 · 定位 | `ExportDialog.test` T33 改写；变异红 |
| D1 Agent「当前默认 / 设为默认」 | 行首 `ui/Radio`（`name=default-coding-agent`，不可用的禁用，title = 可达名） | `CodingAgentsSection.test` 四条改写；变异红 |
| D2 一行三种辅助 | 删与小问号重复的说明行 | 界面页截图 |
| D3 两颗同权重 | 「导出诊断包」secondary、「复制诊断」ghost | — |
| C1 每条一行「技术详情」 | 折叠只在行 hover / focus-within / 当前项出现（Tailwind `not-open:` 变体，open 态常驻） | 问题面板截图；`problemPanel.test` 仍钉「默认收起 + 含 gid」 |
| E1 ⌘↵ ink-faint | `Kbd` | — |
| E2 StepSlider | **未检查**：本机无可用 Codex，「作用范围与执行器」弹层里只出现模型下拉，推理强度滑杆没露出来 | — |
