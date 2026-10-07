# 设计语言审计 · 对照 OpenBitFun（2026-10-07）

> 性质：**审计 + 改进建议**，不是规则。本文件里的任何条目要落地，仍须先改
> `docs/ux/DESIGN_CONSTITUTION.md` / `docs/rules/frontend/ui-visual-discipline.md`
> 这两份权威，并同步 `DESIGN.md`（`web/src/designMd.test.ts` 看护）与
> `web/src/components/ui/foundation.test.ts` 的闸门——不在这里另立一份规则。
>
> 对照对象：[GCWing/OpenBitFun](https://github.com/GCWing/OpenBitFun)（`main`，只读源码，未渲染）。
> 下文 `OBF:` 前缀的路径相对其仓库根；`DS` = `design-system/packages`，`WEB` = `src/web-ui/src`。

---

## 0. 一页结论

Tavotto 的设计系统**治理很强、品味层偏弱**：单一 token 出处、对比度测试、
正则闸门、键盘契约、减弱动效都已到位，这是 OpenBitFun 也未必有的纪律。
差距不在「乱」，而在三件事：

1. **层级被压扁**——UI 主字号 11px（`text-xs` 627 处 vs `text-sm` 86 处），
   字阶 11/12/13/14/15 每级只差 1px，字重只有 400/500；`ink-2 #5c5c55` 与
   `ink-3 #6b6b64` 几乎同色；助手回答正文用的是 ink-2（灰）。
   OpenBitFun 是 13px UI / 14px 阅读、alpha 墨阶 80/60/40/30、选中用 600 字重。
2. **形状与高度只有一档**——所有控件 28px，文字按钮是胶囊、图标按钮 10px 方、
   toast/HUD 10px 方、tooltip/行 6px；首页和命令面板只能各自发明 `h-9`/`h-12`。
   OpenBitFun 有 24/32/40/48 四档高度、一个半径族、且「外层半径 = 内层 + 内边距」。
3. **改图助手像日志而不是对话**——一张卡里塞提示词+回答；代码块无高亮/语言/复制；
   diff 无行号/文件头；工具调用是 11px 单行、无状态；发送键是方块；无暗色。
   OpenBitFun 的 FlowChat 恰好是这一块最值得学的样板（环境/显著两级工具卡、
   思考块停靠、composer 一张卡、paint-only 流式淡入）。

最高杠杆的五件事（详见 §8）：**字阶上调一档 + 墨阶拉开**、**Card 原语**、
**控件高度/半径族**、**助手转录重做**、**token 去 rgba 化为暗色铺路**。

---

## 1. 基础层（tokens）对比

| 维度 | Tavotto 现状（`web/src/index.css @theme`） | OpenBitFun（`OBF: DS/theme-openbitfun/src/*.tokens.json`） | 判断 |
|---|---|---|---|
| 分层 | 单层：色值直接是语义名；部分 `color-mix(ink …)` | reference（不出变量）→ semantic → component 三层，`:where()` 零特异性主题选择器 | **学**：拆出 palette 层，语义层只引用 |
| 墨色 | `ink #1b1b18 / ink-2 #5c5c55 / ink-3 #6b6b64 / faint #a3a39a`（暖灰实色） | `rgba(0,0,0,.80/.60/.40/.30)`；暗色为白 alpha | **学**：ink-2/ink-3 至少拉开到 ≈60%/≈45% 的感知差；可保留暖色相 |
| 中性面 | `bg #eeede9 / field #f1f0ec / canvas #f3f2ee / surface-2 #f7f7f4`——四个近似的米白 | `canvas #fdfdfd / chrome #f8f8f9 / tertiary #f7f7f7 / subtle rgba(16,26,39,.03)` | **改**：合并为 2–3 档，降低黄度；保留「纸感」只在画布（`--color-canvas`） |
| 边线 | `border` ink 12% / `strong` 18% / `control #84847c` | navy 微染 `rgba(16,26,39,.08/.15/.34)` | 可选：边线略带冷调更「精」；与 Paper 方向冲突时保留暖调 |
| 状态色 | 各自手调 subtle/solid 两值 | 一个锚点派生：surface 10% / border 30% / content `color-mix(anchor 70%, black)`；成功/危险 = diff 增/删色 | **学**：派生公式 → 新增状态色零手调，且 diff 与状态同源 |
| 阴影 | `--shadow-card 0 0 0 1px rgba(27,27,24,.04), 0 2px 8px …04`（白底上几乎看不见）；全部硬写 rgba | xs→xl 五级 + `menu/overlay/composer/innerHighlight`；暗色另一套更重 | **学**：阶梯化，并改为 `color-mix(var(--color-shadow) N%)` |
| 字阶 | 11/12/13/14/15，权重 400/500（600 仅选中 Tab） | UI 13、阅读 14/1.58、标题 15/17/18/20/24/32；400/500/600/700；**复合角色**（一个 token 带 family/size/weight/lh/ls 五项） | **学**：见 §2 |
| 间距 | Tailwind 4px 栅格；散落 `gap-1.75`、`py-1.25`、`pl-6.5`、72 处 `-[Npx]` | 4px 栅格 + `space.component.inline/block` + 密度覆盖（compact/touch） | 清理即可 |
| 半径 | `xs3 sm6 md10 lg14 panel16`；注释还写着 md8/lg12 | `4/6/8/12/16/20/24/32/pill`，对话框 28 | 见 §3 半径族 |
| 动效 | 120/180/240/90；`ease-pop (.16,1,.3,1)` 等；减弱动效全局处理 ✅ | 80/140/220/320/420/720；单一 `standard (0.23,1,0.32,1)` + `smooth` + `exit` | 两者都好；Tavotto 要清掉 Tailwind 默认 `animate-spin/pulse` 约 25 处 |
| z-index | 无 token：`z-10…z-50`、`z-[59]`、`z-[1]`；Dialog 内容与 Popover 同层 | `layer.*` 15 档 + 运行时 LayerStack 按打开顺序排 | **学**：至少 token 化 6–8 档 |
| 暗色 | **无**（0 处 `prefers-color-scheme` / `dark:`） | light/dark/高对比×2 | **最大缺口**；本轮先把 token 做成「可换值」 |

### 1.1 建议的新墨阶（示意，需过 `tokenContrast.test.ts`）

```css
/* palette 层（不直接给组件用） */
--p-ink-950: #1b1b18;
/* semantic 层 */
--color-ink:       var(--p-ink-950);                                  /* 正文 / 标题 */
--color-ink-2:     color-mix(in oklab, var(--p-ink-950) 68%, var(--color-surface)); /* 次要 ≈ #5f5f5a */
--color-ink-3:     color-mix(in oklab, var(--p-ink-950) 50%, var(--color-surface)); /* 说明/元信息 ≈ #8d8d88，需验 4.5:1 → 不过则只用于 ≥12px+500 */
--color-ink-faint: color-mix(in oklab, var(--p-ink-950) 32%, var(--color-surface)); /* 占位/禁用 */
```

要点是**让 ink-2 与 ink-3 有可见差**；若 ink-3 过不了正文对比度，就把它限定为
「元信息角色」并在 `ui-visual-discipline.md` 写明用途，而不是两档都挤在 4.5:1 附近。

---

## 2. 字体与层级

**现状问题**（`index.css:134–151`, `594–645`）

- 角色工具类存在但只覆盖约 25% 文本：`type-meta` 82、`type-section` 56、`type-caption` 25、`type-title` 13、`type-body` **5**、`type-control` **1**；其余是手拼 `text-xs text-ink-3`。
- 最大应用内标题 15px；首页只能用 `text-[20px]/[24px]/[26px]` 例外。
- Inspector 名称用裸 `text-xl`（`inspector/Inspector.tsx:409–530`）而非 `type-title`。

**向 OpenBitFun 学的**（`OBF: DS/design-tokens/src/system.tokens.json` `type.*`）

1. **复合角色**：一个角色名同时定 size/weight/line-height/letter-spacing，组件只写角色。Tavotto 已有 `@utility type-*`，缺的是**覆盖率**和**更多档位**。
2. **上调一档基准**：建议 `type-control/body` 12→13，`type-meta/caption` 11→12，新增：
   - `type-reading` 13/1.6（助手正文、长说明）
   - `type-heading` 17/600/-0.01em（Inspector 名称、设置页标题）
   - `type-display` 24/600/-0.02em（首页、空态大标题——取代 `text-[24px]`）
3. **选中/按下用 600 字重而非变色**（OBF 的 label.selected 13/600）——Tavotto 已在 Tab/Segmented 这样做，可推广到 list row 选中（现为 `font-medium`）。
4. 闸门：在 `foundation.test.ts` 加一条「组件目录内禁止 `text-xs|text-sm` 与 `text-ink-*` 同 class 串出现」→ 迫使走角色。

> 密度顾虑：Tavotto 是右侧 ~300px 的 Inspector 工具，上调 1px 会挤。建议**先只上调助手转录与对话框/设置**（阅读型表面），Inspector 字段行保持 12px——这正是 OBF 的做法（chrome 13、flow 14，各自一套）。

---

## 3. 原语组件

### 3.1 控件高度与半径族

| | Tavotto | OpenBitFun | 建议 |
|---|---|---|---|
| 高度 | 全部 `h-7`（28） | xs24 / sm32 / md40 / lg48 | 增 `size="lg"` = 32（对话框主按钮、首页 CTA、命令面板输入）；保留 28 为工具默认 |
| 文字按钮 | 胶囊 | 胶囊 | 保持 |
| 图标按钮 | `rounded-md` 10px 方 | quiet 圆角 / 胶囊 | **统一为全圆**或统一 8px；关键是发送键不再是胶囊堆里的方块 |
| toast / HUD | `rounded-md` 矩形，紧挨胶囊工具条 | 12px 浮层 + 毛玻璃 | toast 改胶囊或与工具条同族 |
| tooltip / 行 | 6px | tooltip 6px，行 8px | OK |
| 卡片 / 菜单 | 10px | 12 / 16px | 菜单 10→12，卡片 10→12 |
| 面板 | 16px | 场景 24px（仅左缘） | 可选：工作面板只圆外侧角 |

**半径族规则**（OBF `UserMessageItem` 的 `card radius + 2`、Dialog 28 包 12 的卡）：
外层半径 = 内层半径 + 内边距。写进宪法 §2，替代当前逐个指定。

### 3.2 Button（`components/ui/Button.tsx`）

- **学**：填充放 `::before`（`OBF: DS/ui/src/components/Button/Button.module.css`），背景与边框独立过渡；loading 时内容 `opacity:0`、spinner 叠加——Tavotto 已用 `loadingLabel` 锁宽，效果等价，可不改。
- **学**：尾随图标 `opacity:.5`；hover 效果用 `@media (hover:hover) and (pointer:fine)` 包住，触屏不粘滞。
- **改**：`primary` 的 `bg-ink text-white` → `text-surface`（为暗色做准备，消掉 19 处 `bg-white`/14 处 `text-white` 中的这一类）。
- **不学**：OBF 禁止按下缩放；Tavotto 宪法允许 0.97–1，保留即可，但修掉 `SendStopGlyph` 的 `scale-50`（`ai/AiPanel.tsx:504–517`）。

### 3.3 输入框 / 焦点

- 现状：按钮焦点 = 2px 实线 outline offset 1；输入框焦点 = 1px 边框变色。两套视觉。
- OBF：输入框同样只变边框色（**刻意不叠第二道环**）；菜单行用 `inset 0 0 0 2px` 防止被滚动裁切；其它 `outline 2px offset 2px`。
- 建议：保留输入框方案；把全局 focus-ring offset 1→2，并把列表/菜单行的焦点改 inset。加 `caret-color: var(--color-accent)`（一行 CSS，很显精致）。

### 3.4 Menu / Popover / Tooltip（`ui/Menu.tsx`, `Tooltip.tsx`）

| 项 | 现状 | 学什么 |
|---|---|---|
| 菜单容器 | `rounded-md p-1 shadow-pop`，无边线 | 半径 12、内边距 6–8、加 `border-subtle` 1px + menu 阴影；行间距 2px |
| 菜单分组标题 | `text-sm text-ink-3`（与宪法 `type-section` 冲突） | 改 `type-meta` 大写不必，但用 caption 色 + 11–12px 统一 |
| 进出场 | pop-in scale .97 | 方向感：`translate 0 -4px` → 0，`transform-origin` 跟随 Radix `--radix-*-transform-origin`；退出 100ms |
| Tooltip | 实心 `bg-ink` 黑块，无箭头 | 可保留黑块（Tavotto 的「仪器」气质），但加 ±3px 朝向位移 + `transform-origin` 指向触发器 |

### 3.5 Dialog（`ui/Dialog.tsx`）

- **学**：浮动页脚——页脚 `::before` = 渐变 + `backdrop-filter: blur(10px)` + `mask-image`，正文加 `scroll-padding-block-end`，长内容（ExportDialog 1900 行那种）从页脚下滑过。
- **学**：遮罩加轻 `backdrop-filter: blur(…)`（OBF 20px；Tavotto 画布内容复杂，建议 4–6px 或不加，须实测性能）。
- **改**：CommandPalette 自建遮罩/盒子（`CommandPalette.tsx:343–371`）→ 走 `Dialog` 的新 `chrome="palette"`，一处维护。
- 主按钮在对话框里用 32px（§3.1）。

### 3.6 Toast（`StatusBar.tsx:153–232`）

- **学**：自动消失倒计时画成关闭按钮外的 SVG 环（`OBF: WEB/shared/announcement-system/styles/AnnouncementToast.scss`，`stroke-dasharray:100`，1.5px）。Tavotto 已有 hover 暂停（`lib/dismissTimer`），配一个可见的倒计时就闭环了。
- 形状并入胶囊族（§3.1）。

### 3.7 滚动条 / 溢出文本

- **学**：滚动条 6px，**仅当该视口被 hover 或内含焦点时显示**（`OBF: DS/ui/src/styles/scrollbars.css`）。Tavotto 现在 10px 常显（`index.css` ~473），在 300px 窄栏里很占眼。
- **学**：边缘渐隐用 alpha `mask-image`（16px）而不是叠色块——适用于 CanvasTabs 横向溢出、助手消息列表顶部。
- **学**：OverflowText——截断用 16px 尾部渐隐代替省略号，且**只在真溢出时**出 tooltip。适合 Inspector 面包屑、资源卡片名、TargetChip。

### 3.8 加载态（目前 5 种方言）

统一为三种，全部走 token 时长：

| 用途 | 现状 | 建议 |
|---|---|---|
| 按钮/行内 | `LoaderCircle animate-spin` | 保留，改用 `--duration-*` 的自定义 `@keyframes spin`；或学 OBF 3×3 点阵（更有品牌感，但须在 ICONOGRAPHY 登记） |
| 不定进度条 | `animate-pulse w-1/3 bg-ink` ×4 处 vs `animate-sweep` | **全部改 `animate-sweep`**（`UpdateNoticeDialog.tsx:95`、`settings/UpdateSettings.tsx:324`、`PackagesSettings.tsx:665`、`RepairProgressLine.tsx:97`） |
| 文本「进行中」 | `text-shimmer`（已是 token） | 保留；可升级为 OBF 的 mask-shimmer（作用于 currentColor，暗色自动可用） |
| 骨架 | `AssetBrowser.GridSkeleton` 的 `animate-pulse` | 改为静态 `bg-surface-hover` + `opacity .65`（OBF 设置页骨架就是不动的） |

### 3.9 EmptyState（`ui/EmptyState.tsx`）

- 现状：20px 灰图标 + 12px 标题，几乎看不见。
- OBF：媒体 24/32/40 三档、**不透明低强调色**（避免描边重叠处发黑）、标题 13、描述 `max-inline-size:42ch`。
- 建议：图标放进 40px `rounded-lg bg-surface-hover` 圆角底座、标题 `type-heading` 小号（15/600）、描述 `type-caption` 限宽 42ch；主动作用 32px 按钮。

---

## 4. 页面与壳层

### 4.1 App 壳（`App.tsx:240–330`, `TopBar.tsx`, `left/LeftRail.tsx`）

- **好**：灰桌面 + 白工作面板（09-30 重做）方向与 OBF「chrome 底 + 抬升场景面」一致。
- **改**：
  - `App.tsx:244` 的 `[&>header]:bg-bg` 覆盖 TopBar 自己的 `bg-surface` → 给 TopBar 加 `tone="chrome"` 属性，去掉级联补丁。
  - LeftRail 激活态 `bg-surface shadow-card`：阴影太淡等于没有；改为 `bg-surface` + 1px `border-subtle` 或 OBF 的 `selection.surface`（纯填充、无阴影）。
- **学**：
  - 工作面板只圆**外侧**角（OBF `border-radius: 24px 0 0 24px` + `-4px 0 12px -6px` 方向阴影），让面板像从桌面「浮出」而不是一张贴纸。
  - 抽屉 resize 把手 hover 时画 1px 发丝线（现为 `w-2` 透明条，看不出可拖）。
  - macOS 上侧栏用原生 vibrancy（Tauri `window-vibrancy`），非 mac 回落到 `color-mix(chrome 90%) + blur`；需配 `prefers-reduced-transparency` 回落。

### 4.2 首页 HomeView（`home/HomeView.tsx`）

- 现状：`text-[26px]/[24px]/[20px]` 例外字号、虚线 DropZone on `surface-2`（2018 年感）、最近项目是 hover 行。
- **学 OBF WelcomePanel**（`OBF: WEB/flow_chat/components/WelcomePanel.css`）的「叙事句」：一句 15px 话里嵌入 24px 行内 chip（「打开 **[最近项目 ▾]** 或 **[拖入脚本]** 开始」），比两个大按钮更有产品感。
- DropZone 改为：静态时是普通卡片（无虚线）；**拖入时**才出现 1.5px 虚线 accent 边 + `accent-subtle` 填充 + 外发光（OBF 拖放接受态）。
- 最近项目改为 Card 原语网格（§5），每张卡带缩略图（已有 `CanvasThumb`）。
- 字号走 `type-display` / `type-heading`，删掉 `HERO_BUTTON` 用 `size="lg"`。

### 4.3 设置页（`SettingsDialog.tsx`, `settings/SettingRow.tsx`）

- 现状：分区 = 标题 + 发丝线，无分组底。
- **学 OBF FieldGroup**：每个分区是 12px 圆角、`rgba(0,0,0,.03)` 底的一组，行间 `border-top: border-subtle`，行内边距 16/20；分区标题 15/600。这是 macOS 系统设置 / Linear 设置的标准形态，「产品感」提升最明显的单项。
- 左侧导航行 30px、8px 圆角、选中纯填充 + 600 字重。
- 全部 `cursor-pointer`（`SettingRow.tsx:191` 等 16 处）按宪法改回箭头。

### 4.4 Inspector（`inspector/Inspector.tsx`）

- 身份头：角色图标底座 `rounded-sm`（6）与 IconButton `rounded-md`（10）不一致 → 统一；名称用 `type-heading`。
- 分区发丝线已经好；可学 OBF 的分区标题 hover 才显示操作按钮（不占位），减少常驻噪音。

---

## 5. 卡片

**现状**：没有 `Card` 原语；同一配方 `rounded-md bg-surface p-{2|3} shadow-card` 手抄 13+ 处，内边距 p-2/p-3、间距 1.5/2/2.5 漂移；只有 AssetBrowser 有 hover 态；NativeSessionCards 是 `rounded-sm + shadow-pop` 的异类（`NativeSessionCards.tsx:97`）；Playground ExampleCard 用边框不用阴影。

**建议新增 `components/ui/Card.tsx`**（参考 `OBF: DS/ui/src/components/Card/`, `ActionCard/`）：

```tsx
type CardProps = {
  appearance?: 'raised' | 'subtle' | 'plain'; // raised=白底+阴影；subtle=填充无阴影；plain=透明
  padding?: 'sm' | 'md';                      // 8 / 12 —— 只两档
  interactive?: boolean;                      // 带 hover/selected/focus
  selected?: boolean;
};
```

交互态用 **outline 而非 border**，几何永不变化：

```css
.card[data-interactive] { outline: 1px solid transparent; outline-offset: -1px;
  transition: outline-color var(--duration-fast), background-color var(--duration-fast); }
.card[data-interactive]:hover    { outline-color: var(--color-border); }
.card[data-selected]             { outline-color: var(--color-border-strong); background: var(--color-selected); }
.card:has(:focus-visible)        { box-shadow: 0 0 0 2px var(--color-accent); }
```

- `shadow-card` 从 4% 提到可感知的两层（例：`0 0 0 1px ink/6%, 0 1px 2px ink/4%, 0 4px 12px ink/4%`）。
- 迁移清单：`AssetBrowser.tsx:1057`、`AiPanel.tsx:985,1123`、`EngineEnvironmentCard.tsx:65,301`、`DependencyRepairCard.tsx` ×6、`ProblemCards.tsx:276`、`NativeSessionCards.tsx:97`、`playground/components/ExampleCard.tsx:150`、`VersionDialog.tsx:1018`。
- `foundation.test.ts` 加闸：组件目录内禁止 `shadow-card` 出现在 `Card.tsx` 以外。

---

## 6. 改图助手（重点）

文件：`web/src/components/ai/AiPanel.tsx`（1267 行）、`ai/Markdown.tsx`、`ai/DiffView.tsx`、`lib/streamMarkdown.ts`。
对照：`OBF: DS/ui/src/flow-chat/*`、`WEB/flow_chat/*`。

### 6.1 转录结构：从「一张卡」到「一段对话」

| | 现状（`SessionBlock`, `AiPanel.tsx:1111–1176`） | OpenBitFun | 建议 |
|---|---|---|---|
| 用户消息 | 卡内灰底块 `rounded-sm bg-surface-2`，12px | 右对齐气泡 `max-width:min(72%,48rem)`，10px 圆角，填充是独立 `__surface` 层；超 3 行折叠；时间与操作 hover 才出现 | 右对齐、`fit-content`、12px 圆角、`bg-surface-hover`；3 行折叠 + 展开；hover 显示「复制 / 重新发送」 |
| 助手回答 | 同卡内，**ink-2 灰** 12px | 无框，14/1.58，80% 墨 | **无卡片**，`type-reading` 13/1.6，**ink 正文色** |
| 轮次节奏 | 卡间 `gap-3` | 组合层统一管间距：段 12 / 项 8 / 轮 16；叶子组件不带 margin | 在列表容器定义 `--turn-gap / --item-gap`，子组件清零 margin |
| 发送反馈 | 无 | WAAPI：气泡 `scale(.985,.94)→1` 220ms，文字晚 55ms；只在真发送时播，重挂载不播 | 用已有 `--ease-pop` 做同等效果，必须尊重减弱动效 |
| 流式出字 | 每词 span + `animate-stream-in`（`streamMarkdown.ts`） | CSS Custom Highlight API 按 32 档给新字形上色，**零 DOM** | 可选升级：保留现实现作回落，支持 `CSS.highlights` 时走 paint-only（选区与虚拟化更稳） |

### 6.2 工具调用 / 思考：两级注意力模型

现状 `ProcessGroup` 折成一行「N 步 · 前 16 字」，展开后 `ProcessRow` 是 11px 单行：无状态、无耗时、无输出，图标靠 `mt-[3px]` 对齐。

学 OBF `AmbientToolCard` / `ProminentToolCard`（`OBF: DS/ui/src/flow-chat/tool-cards/FlowChatToolCard.module.css`）：

- **环境行（读文件、思考、跑命令成功）**：无框、一行正文高；14px 图标列；**hover 时图标换成 chevron**（不 hover 不显示展开提示）；展开为 `bg-surface-2` 圆角面板显示参数/输出（截断 + 「查看全部」）。主语/结果放在小号填充 chip 里。
- **显著卡（改了脚本、失败、需要确认）**：1px 边框 12px 圆角；40px 摘要行：**粗体动作 · 次要主语 · 右侧 `+N −N` · 竖线分隔 · 状态图标**（成功 ok 色 ✓、失败 danger 色 ✕、运行中 shimmer）；操作按钮（回滚、在编辑器打开）hover 时**替换** `+N −N` 位置，不占常驻空间。
- **思考块**：流式时限高 `7lh` 或 1 行滚动替换，上下 24px mask 渐隐；完成后折叠为一行「已思考 12s」，可展开。
- **折叠动画**统一 `grid-template-rows: 0fr→1fr`——Tavotto 的 `Reveal` 已是这个技术，直接复用。
- 每步显示耗时（`tabular-nums` 右对齐 meta）；失败步自动展开。

### 6.3 Markdown 与代码块（`ai/Markdown.tsx`）

| 项 | 现状 | 建议（参考 `OBF: WEB/infrastructure/markdown/Markdown.scss`） |
|---|---|---|
| 正文色 | `text-ink-2` | `text-ink` |
| 标题 | 全部压成 `text-sm font-medium` | h1–h3 → 15/14/13 + 600，`mt-3` |
| 代码块 | `rounded-sm border bg-surface-2 p-2`，无高亮、无语言、无复制 | **无边框** 12px 圆角，`color-mix(surface-2 60%, surface)`；顶部 28px 工具带：语言名（meta）+ 复制 IconButton（复制后图标换 ✓ 1.2s）；**复用 `playground/pythonHighlight.ts`**；hover 行高亮 |
| 行内代码 | OK | 加 `box-decoration-break: clone`（换行时两端都有圆角） |
| 链接 | 灰色下划线 | accent 色 + `underline-offset:2px` + 1px 下划线（宪法 09-30 把链接改灰，助手转录可例外，需在宪法 §18 注明） |
| 表格 | `border text-xs` | 无外框 12px 圆角包裹，表头 `surface-2`，行 hover |
| 首尾元素 | 自带 margin | `:first-child{margin-top:0}` / `:last-child{margin-bottom:0}` |

语法色需新增 6–8 个 token（keyword/function/string/number/comment/type），先只出浅色值，结构上留暗色位；同时把 `ExampleCodeSheet.tsx:26–28` 的三个裸 hex 收进来。

### 6.4 Diff（`ai/DiffView.tsx`）

现状：一个发丝框 + `+N −N` + 放大按钮；行只有底色，无行号、无文件头、无字级高亮；回滚是游离的红胶囊。

学 `OBF: WEB/flow_chat/components/InlineDiffPreview.scss`：

- **文件头**：文件名（mono，中段省略）+ `+N −N` + 操作（回滚 / 放大 / 复制补丁）——即 §6.2 的显著卡摘要行。
- **行**：22px 行高、整行淡底；左侧 **4px 粘性变更条**：新增实色、删除 `repeating-linear-gradient` 1px 条纹（色盲也能区分）；粘性行号列 `min-width: Nch` + `tabular-nums`。
- 折叠未改区为「⋯ 展开 18 行」分隔行（`bg-surface-2`）。
- 可选：字级高亮（增 `ok-subtle` 加深一档）。
- 回滚按钮并入文件头，不再单独一行。

### 6.5 Composer（`AiPanel.tsx:406–494`）

| 项 | 现状 | 建议（参考 `OBF: DS/ui/src/flow-chat/composer/ChatComposer.module.css`, `WEB/flow_chat/components/ChatInput.scss`） |
|---|---|---|
| 外壳 | 玻璃 `rounded-lg bg-glass shadow-composer`，焦点 `border-accent` | 保留玻璃；焦点改为**边框加深到 border-strong**、不用 accent（OBF：只变色无环，更安静） |
| 输入字号 | **11px**（比渲染后的提示词还小） | 13px，`caret-color: accent` |
| 布局 | 固定 rows=2 | 两态：空/短时 42px 单行胶囊（左 scope、右发送）；多行时展开为「内容 / 工具行」两行，最小 96px |
| 上下文 | 底部 ghost 文字「作用于：X · Agent」 | 输入框**上方** 28–32px 半透明上下文带：`[● 图 2 · 坐标轴 ×]` chip，可点切换；模型/推理强度以独立小胶囊放在工具行（不要埋进 popover） |
| 发送/停止 | 28px 黑色**方块**，`scale-50` 换形 | **24px 圆**；停止 = 圆内 9px 圆角方块；运行中 1px 弧线绕圈 spinner；交叉淡化，不缩放 |
| 建议 chip | 灰色 secondary 胶囊，输入即收起 | 保留收起逻辑；chip 加前置小图标、`bg-surface` + 1px border-subtle，hover 填充；最多一行，溢出横向渐隐 |
| 附件/提及 | 无 | 中期：`@` 提及图元素（Tavotto 已有元素树/选择模型，天然适合），提及渲染为 5% 填充 tag；粘贴大段文本折成 chip；拖入图片的接受/拒绝态 |
| 滚到底按钮 | secondary IconButton `rounded-full` | 34px 毛玻璃方/圆 + 未读数小徽标 |

### 6.6 历史（`TaskHistory`, `AiPanel.tsx:844–962`）

现状：覆盖整个面板 `absolute inset-0 z-20`。建议改为 Popover/下拉列表（宽 320，最近 20 条 + 搜索），或在面板顶部做 OBF 式「轮次轨」（5×2px 小条，hover 扇形展开 + 预览 tooltip）用于在长会话中跳转——后者是 OBF 很有辨识度的一笔，但优先级低。

### 6.7 错误与空态

- 错误：现为 `text-xs text-danger` 一行。改为 OBF `TurnFailureNoticeItem` 式内联横幅：6px 圆角、`danger-subtle` 底 + 30% 边、图标 + 600 标题 + 描述 + 「重试」。
- 空态：§3.9 的新 EmptyState，配 3 条可点击的示例提示（取 `chipsFor` 的结果），而不是一句说明。

---

## 7. 明确**不**学的

| OpenBitFun 做法 | 不学的原因 |
|---|---|
| Lucide 全量 + 1.6 描边 | ADR 0052 自有图标集；但**描边 1.6 vs 2** 值得在 `ICONOGRAPHY.md` 里评估——2px 在 14px 图标上偏粗，是「不精致」的一个隐性来源 |
| 青色 accent `#059cb0` | 品牌蓝由 `brand.ts` 定，不换 |
| 模型选择器「星云」渐变 / 胶囊碎裂动画 | 与 Paper × Instrument 的克制方向冲突；只学其「状态有专属动效」的思路 |
| 对话框 28px 圆角 + 24px/700 标题 | 对 Tavotto 的工具密度过大；对话框 14→16 即可 |
| 全应用 13px chrome | Inspector 字段行仍需 12px（§2 密度说明） |
| 去掉按下缩放 | 宪法允许 0.97–1，已有体系，不折腾 |

---

## 8. 路线图

每项落地前：改宪法对应章节 → 改 `DESIGN.md` → 改/加 `foundation.test.ts` 闸门 → 改代码。

### P0 · 一周内，低风险高感知

1. **墨阶拉开 + 助手正文改 ink**：`index.css` ink-2/ink-3；`ai/Markdown.tsx` 全部 `text-ink-2` → `text-ink`。过 `tokenContrast.test.ts`。
2. **代码块**：高亮（复用 `pythonHighlight.ts`）+ 语言标签 + 复制按钮；无边框 12px 圆角。
3. **发送键**：圆形、停止为圆角方块、去 `scale-50`；composer 输入 13px + accent caret。
4. **加载方言统一**：4 处 `animate-pulse` 进度条 → `animate-sweep`；骨架去 pulse。
5. **滚动条**：6px、仅 hover/焦点视口可见。
6. **清理**：stale 注释（`index.css:74,101–106,177–180`、`Button.tsx` 头注、`Badge.tsx` docstring）、`Rulers.tsx:44–47,123` 与 `main.tsx:37` 的旧色、`Input.tsx:499` `#d0342c`→danger、16 处 `cursor-pointer`、`App.tsx:244` 级联补丁、`Menu` 标签色。

### P1 · 两到三周，结构性

7. **Card 原语** + 13 处迁移 + 闸门（§5）。
8. **控件 `size="lg"` 32px** + 半径族规则；toast 并入胶囊族；IconButton 形状统一（§3.1）。
9. **字阶**：新增 `type-reading / type-heading / type-display`，助手、对话框、设置、首页先迁；Inspector 暂不动（§2）。
10. **助手转录**：用户/助手分离、轮次节奏、工具调用两级卡、思考块折叠、Diff 文件头 + 行号 + 变更条（§6.1–6.4）。
11. **设置页 FieldGroup**（§4.3）。
12. **z-index token**：`--z-sticky/drawer/overlay/dialog/popover/tooltip/toast`，替换 42 处裸值。

### P2 · 一个月+，为暗色与差异化

13. **token 分层**：palette / semantic；阴影、`bg-white`/`text-white`、`color-mix(ink…)` 全部改为引用语义变量；状态色锚点派生。完成后暗色只是换一张值表。
14. **暗色主题**（含 Tauri 窗口跟随系统、画布保持「纸」色）。
15. Composer 上下文带 + `@` 提及图元素；首页叙事句 + 卡片网格；Dialog 浮动毛玻璃页脚；OverflowText；toast 倒计时环；工作面板外侧圆角 + vibrancy。
16. 可选：paint-only 流式淡入（Custom Highlight API）、轮次轨。

---

## 附：OpenBitFun 参考文件索引

| 主题 | 路径（相对 OpenBitFun 根） |
|---|---|
| token 源 | `design-system/packages/design-tokens/src/system.tokens.json`；`design-system/packages/theme-openbitfun/src/{light,dark}.tokens.json` |
| 级联层 | `design-system/packages/ui/src/styles/layers.css` |
| 滚动条 | `design-system/packages/ui/src/styles/scrollbars.css` |
| Button / Menu / Tooltip / Dialog | `design-system/packages/ui/src/components/{Button,Menu,Tooltip,Dialog}/*.module.css` |
| Card / ActionCard | `design-system/packages/ui/src/components/{Card,ActionCard}/*.module.css` |
| 工具卡 | `design-system/packages/ui/src/flow-chat/tool-cards/FlowChatToolCard.module.css` |
| Composer | `design-system/packages/ui/src/flow-chat/composer/ChatComposer.module.css`；`src/web-ui/src/flow_chat/components/ChatInput.scss` |
| 思考块 | `design-system/packages/ui/src/flow-chat/conversation/ConversationBlocks.css` |
| 用户消息 / 发送动效 | `src/web-ui/src/flow_chat/components/modern/UserMessageItem.scss`；`useSubmittedMessageMotion.ts` |
| Markdown / 代码块 | `src/web-ui/src/infrastructure/markdown/Markdown.scss`；`useStreamingTextReveal.ts` |
| Diff | `src/web-ui/src/flow_chat/components/InlineDiffPreview.scss` |
| 毛玻璃配方 | `src/web-ui/src/shared/styles/_surface-recipes.scss` |
| 欢迎页 | `src/web-ui/src/flow_chat/components/WelcomePanel.css` |
| 壳层 | `src/web-ui/src/app/layout/{AppLayout,WorkspaceBody}.scss` |

---

## 9. 逐页审计（第二轮，2026-10-07）

> 前提：§1–§8 的原则已认同（墨阶 ink-2 #4a4a45 / ink-3 #74746e、字阶角色、32px lg、
> 半径族 行 8 / 卡 12 / 面板 16、Card 原语、状态色锚点派生）。下文所有「→」都按这些值写。
> 画布对应画板：「设置页 · 逐页」「属性侧栏」「画布侧栏」「左侧 · 问题面板」。

### 9.1 设置页（`SettingsDialog.tsx`, `components/settings/*`）

总评：`SettingRow` 的行语法（`1fr | 240`、48/32px）很守纪律，但约 40% 的可见内容绕开了它
（引擎卡、Codex 面板、更新块、包安装/任务面板、样式编辑器）；反馈落点离触发点很远；
`SummaryRow` 有三份、局部 `FieldGroup` 有两份（与要新增的原语重名，先改名）。

**全局外壳**
- 内容列止于 640，而可用约 760 → 右侧 120px 空带，控件浮在对话框中部。→ 680 列居中。
- 无页头（宪法 §13「导航项即其名」），钻入页与长页滚动后失去上下文；导航词与页内首行用词不一致。→ 页头 `type-heading` 17/600 + 一行说明（需修 §13）。
- 导航项 28px / 6px / 12px / 选中 500。→ 30px / 8px / 13px / 选中 600；顶部 28px 搜索（OBF `SettingsNav.tsx:52–145` + `settingsRegistry.ts` keywords）。
- 未保存草稿无标记。→ 导航 6px 蓝点 + 离开确认。

**原语**
- 新增 `ui/FieldGroup`（12px 圆角、ink 3% 底、行 12/16、内缩分隔线）与 `FormSection`（标题 13/600、组间 24）。
- `status` 槽只放文字：`PathValue` 的 28px 按钮、`DirectoryRow` 的编辑器（`ProjectSettings.tsx:215–249`）移到跨两列的 fill 行。
- `InlineWarning` → 锚点派生的 `Notice`，作为组内最后一行。
- 行增加 `layout="balanced"`（4:6），给格式复选框、库选择、安装表单用（OBF `ConfigPageLayout.tsx:85–140`）。

**逐页**
| 页 | 级别 | 问题 | 建议 |
|---|---|---|---|
| 通用 | P1 | 首个分区无标题；5 个相同次级按钮，其中 3 个关于教程；「界面看起来不对？」重置后要用户自己刷新 | 四组：语言与布局 / 侧栏 / 画布 / 学习；教程合并为一行 + ⋯；重置直接生效 |
| 项目 | **P0** | 「运行脚本」= 重复标题 + 段落 + `border-t` 碎片 + 左对齐按钮（`EngineEnvironmentCard.tsx:92,105,229`, `WorkdirRow.tsx:63,98–159,183,252`）；目录编辑器塞进 meta 行，打开时跳 36px；label `htmlFor` 指向未挂载的输入 | 「Python 与运行」组：解释器 / 项目环境 / 运行目录 / 记住的输入 / 脚本备份 各一行；缺包是组内 danger Notice |
| 期刊规范 / 样式 | **P0** | 切导航、关对话框静默丢草稿（代码注释自承，`ProfilesSettings.tsx:664`）；两个 15/500 名称竞争；「跟随更新」在最底；错误远离保存键；库控件 2–4 项 Segmented、5+ 项 Select，宽度跳变 | 吸底保存条（放弃 / 保存 32px）+ 导航蓝点 + 离开确认；「正在使用」组含名称、KeyRules 四栏统计、跟随更新；库固定一个 Select |
| 导出 | P1 | 三个分区各一行；格式复选框标签 11px | 合为「默认导出」一组；格式用 balanced 行 |
| 改图助手 | **P0** | 重新扫描错误在页底（`CodingAgentsSection.tsx:242–246`）；每行单选 + 开关 + 整行 + chevron 四种操作；「就绪」只有绿点、其他状态有字；Codex 面板左对齐、`StepList` 是设置里唯一带框列表 | 「默认助手」改为组首一个 Select；行内只留开关；状态统一 StatusPill；错误是组首 Notice；详情页删除收进 ⋯ + 确认 |
| Python 库 | **P0** | 进度条轨道与面板同为 `surface-2` 看不见 + `animate-pulse`（`PackagesSettings.tsx:633,663,665`）；安装反馈渲染在表格下方；每行升级/卸载两个等重胶囊 | 任务行紧贴安装表单；2px sweep 条在 `border` 色轨道上；每行一个 ghost「升级」+ ⋯（卸载为危险项） |
| 帮助与诊断 | **P0** | 「环境是否正常」排在最后；导出结果挤进分区头（`PrivacyAboutSettings.tsx:270–281`）；结论当行标签、OK 点是灰色 | 顺序：健康 → 报告 → 开发者；结论用 StatusPill |
| 关于 | P1 | 更新分区无标题；「有新版本」块打破行语法、`<pre>` 原样输出；遥测状态重复开关 | 产品信息作页头；更新可用 = accent Notice + 32px 主按钮；遥测只显示「未选择 / 需重新同意」 |

### 9.2 属性侧栏（`components/inspector/*`）

总评：信息架构（三层、角色注册表、可视选择器、逐字段重置）是对的，问题在执行几何。

**P0**
1. **混合值谎报**：多选时色块画 `#000000`、开关显示「关」（`ElementInspector.tsx:1840–1853`），箭头/形状颜色回落 `#1B1B18` 且无混合提示（`StrokeSection.tsx:258,324`）；`OverrideState 'some'` 被压成「全部」（`TypographyControls.tsx:76`）。→ 双色斜分色块、居中带横线的开关（`aria-checked="mixed"`）、删掉行尾「多个值」文字；部分修改用空心环。
2. **控件列没有右缘**：Select 全宽、NumberField `calc(4ch+.75rem)`、ColorField 32px、`[data-stroke-fields]` 40px（`index.css:674`）、成对格 `6ch`。→ 一张行网格：
   ```css
   --insp-label: clamp(88px, 28%, 112px);
   .insp-row { display:grid; grid-template-columns: var(--insp-label) minmax(0,1fr) 20px; column-gap:8px; min-height:28px; }
   ```
   控件只有 `full` / `half` 两档；导出 `INSPECTOR_CONTROL_X`，替换 `pl-20`（`:789,1989`）、`paddingLeft: labelWidth`（`TypographyControls.tsx:124`）。
3. **改值移动布局**：重置键只在已修改时追加（控件少 34px），修改点让标签右移 8px。→ 20px 状态槽常驻；点悬挂在 x = −8px。
4. **三级同形**：Section / GroupHead / SummaryRow 都是 12/500/ink。→ 节 32px 12/500 ink · 组 24px 11/500 ink-3 · 折叠行 32px 12/400 ink + ink-3 摘要。

**P1**
- 身份头只在有祖先/有修改时出面包屑 → 切换选择跳 24px。改固定两行（24 + 32）；面包屑 24px 命中区 + `TruncateMiddle`；名称 `type-heading` + `title`。
- 修改徽章 hover 由 `bg-selected` 变 `bg-surface-hover`（变浅），违反「悬停只加深」。
- 标签 11px（`Field.tsx:102`）与宪法 §6 的 12px 冲突；三种标签溢出策略（折行 / 截断 / 截断）→ 统一折两行。
- 发丝线每屏约 8 条 → 只在节之间 1 条；折叠行 40 → 32px；披露方言 5 → 3（SummaryRow / GroupToggle / Details）。
- 颜色字段只在 `title` 里有 hex → 可编辑 hex + Popover（文档色、最近色、alpha、「无」）；`#d0342c` → danger token。
- 选项格是第三种控件皮肤 + 角落对勾压预览 → Segmented 皮肤。
- 图例行常驻 3 个 28px 按钮、每项 4 个 Tab 停点（`LegendCard.tsx:113–137`），与宪法 §21.2 冲突 → hover/focus-within 的 ⋯ + ⌥↑↓ 排序；色块按真实 marker 绘制。
- 四种提示框皮肤 → Card `subtle`；`ElementEditEntry` 常驻两行隐形占位（约 42px，`PanelSection.tsx:766–779`）→ 单行 SwapText。
- 字体：字号行内标签、B/I 行错位 8px。

**已拍板（2026-10-07，按建议）**：
① 取消字号行内标签——字体组改为「字体 [全宽] / 字号 [半宽][B I 图标组] / 颜色 [色块+hex] / 对齐 [Segmented]」；落地前先改宪法 §20。
② 标签页顺序改为 属性 | 画布 | 助手；落地前先改 ADR 0010 §3。
③ 字段标签用 ink-2（新值 #4a4a45），单位、元信息、摘要值用 ink-3。

### 9.3 画布侧栏（`inspector/CanvasPage.tsx`）

- **P1 假禁用**：透明背景时背景色用 `pointer-events-none opacity-40`（`:191`），键盘仍能改色，也违反宪法 §5。→ `ColorField` 增 `disabled`。
- **P1 预设卡**：4 张 88px 卡占首屏约 25%，是第四种选中皮肤，尺寸不匹配预设时无「自定义」。→ 一行 Select（选项带缩略图、含「自定义」）+ W/H 半宽 + 方向 Segmented。
- **P1 头部**：与属性页同一套两行头（「画布」/ 文档名 / 尺寸 meta）。
- **P2**：手风琴互斥（`:73–76`）→ 可多开并记住；关掉自动对齐时子开关卸载 → 保留并变暗；参考线列表每行重复「垂直参考线」→ │ / ─ 字形列；可加只读「导出 · PDF · 300 ppi ↗」摘要行（不新增第二个导出入口）。
- 未选中时属性页的空态改为文档摘要卡（尺寸、N 张图、N 处修改、「画布设置 ›」）。

### 9.4 左侧问题面板（`components/left/ProblemPanel.tsx`, `ProblemCards.tsx`, `LeftRail.tsx`）

**P0**
1. 吸顶组头 `bg-surface`（`ProblemPanel.tsx:623`），停靠抽屉是 `bg-bg` → 每个规则头是一条白带。→ `--drawer-bg` 变量。
2. 技术细节 `group-hover/row:not-open:block`（`:796`）→ 逐行扫视时每行涨约 20px。→ 尾随格 ⓘ → Popover。
3. 首项之前约 245px 头部（标题 / 范围 / 总数 + 比例条 + 统计 + 自动修复 / 视图切换），违反宪法「行上方至多三层」。→ 范围并入标题行胶囊、视图切换进 ⋯、删总数与比例条：36 + 32 = 68px。
4. 严重度无层级：轨上阻断是灰点（`LeftRail.tsx:94–105`），建议点 `ink-faint` 约 2.5:1，`not_verifiable` 两处图标不同。→ 锚点派生 danger/warn 三值 + 一张图标表（OctagonAlert / TriangleAlert / Lightbulb / CircleDashed）；只有错误计数用着色胶囊。
5. 画布定位高亮用 `animate-pulse`（`OverlaySvg.tsx:237`）→ token 关键帧。

**P1**
- 卡片 → 披露树：图 32 / 子图 28 / 规则 28 / 对象 28，全部基于 `listRowClass`、行圆角 8，就地展开，不再整页钻入；`pr-24` 硬预留导致 280px 宽时标题只剩约 118px。
- 尾随格 88px：静止「6.5 → 8 pt」，悬停同格换「修复」，宽高不变。
- 一个动词：只留 修复 / 修复… / 修复 N（现有 8 种说法）。
- 悬停行即在画布描边（复用 OverlaySvg hovered outline）。
- 检查中无提示（`validationStore.ts:98`）→ 头部 shimmer；首检用静态骨架；空态给证据（「按某规范检查了 4 张图 · 刚刚」）。
- 左轨：红点（只表示阻断）、提示「3 项阻断（共 20）」；图标与警告同形 → 换 ListChecks（需在 ICONOGRAPHY 登记）；「工作区 / 项目」名称不一致。

**P2**：画布严重度角标、Inspector「2 个问题 ›」芯片——`openProblemAt`（`lib/issueFocus.ts:228`）目前**没有生产调用方**，样式面板直达未接通；F8 / ⇧F8；`--drawer-bg` 横扫（`ElementTree.tsx:459`, `AssetBrowser.tsx:1305`）；缩略图半径统一；文档漂移（规则文档说当前行有左竖条，代码明确不画）。

保留 `data-issue-row` / `data-issue-rule` / `data-issue-object` / `data-problem-card-key` / `data-rail-blocking` 钩子（引导与 e2e 依赖）；`batchable()` 与 `lib/problemList.ts` 不动——这是纯表现层改动。
