# 设计审计（第二轮）：控件 · 字体 · 图标 · 层级 · 动效

模式：audit ｜ 平台/输入：macOS 26 · Chromium（Playwright）1512×945 @2x · zh-CN 为主、en-US 对照 · 鼠标 + 键盘探针 ｜ 日期：2026-09-14

第一轮报告：`APPLE_DESIGN_AUDIT_2026-09-14.md`（34 条，四个批次 #350 → #352 → #353 → #354）。本轮**只看四个批次叠完之后的状态**（worktree `apple-design-batch4`，tip `2c81f363`），不复述第一轮已修的条目；第一轮的「未验证」项里本轮补了 en-US 对照与真实渲染的量测，仍未做的列在文末。

## 任务与证据边界

用户希望完成：在四个批次之上，从**控件细节、字体运用、图标绘制、层级梳理、动效**五个维度再审一遍，并看 GSAP（`gsap-skills` 插件）与 rareui.com/components 里有什么值得学。

已检查：素材库首页、图内编辑（整张图 / 子图 / 标题 / 曲线 / X 轴刻度 / 图例五个检查器页）、更多菜单、命令面板、导出对话框、设置 › 常规、问题面板、改图助手、右栏页签、字体下拉、数字框聚焦态、树行 hover、素材卡选中 / hover；zh-CN 全部、en-US 的检查器 / 问题面板 / 导出；DOM 量测（页签宽度、textarea 的 scrollHeight、计算样式、分区纵坐标）；`components/ui/*` 全部原语、`index.css`、`lib/motion.ts`、`ICONOGRAPHY.md`、宪法。

未检查：屏幕阅读器、`prefers-reduced-motion` 真机、200% 放大、WebKit / Windows、深色、StepSlider（本机无 Codex，滑杆不露出）、教程层、Onboarding、playground、Codex 内嵌画布。

本轮不改：数据、渲染管线、写回事务、任何业务逻辑；报告不改代码。

## 核心判断

四个批次之后，「第二套实现」与键盘层的漏洞基本收干净了；剩下的不成熟感换了形状，集中在三处：

1. **等宽字体在当一种「技术感」装饰用**（观察，146 处 `font-mono` + `num-input`）。它同时承担数值输入、只读数值、快捷键、路径、代码五种角色；SF Mono 在 12px 上比 SF Pro 重一档，数字框里的「1.2 pt」比旁边下拉里的「实线」黑一截；导出清单「7.33 pt → 大于 8 pt」一句话里数字是 SF Mono、汉字是苹方，一行两种字。Apple 自家的检查器（Keynote / Numbers / Xcode Inspector）数值一律系统字体 + 等宽数字，等宽字体只给代码和路径。
2. **动效的地基对了，但两类最常见的变化没有被「解释」**（观察 + 量测）：页签下划线与分段选中块是**跳**到新位置的，折叠分组是**瞬间**跳出的——这两种恰好是用户每天做几百次的操作；而 53 处过渡落在 Tailwind 默认的 150ms/ease 上，绕开了「时长只来自 token」的规矩（门禁只抓字面量，抓不到「没写」）。
3. **底部有三条消息轨道**（观察）：HUD 在左、状态 toast 在中、操作提示 toast 在右，截图里两个不同形状的 toast 同时在；上下文栏那句常驻的「刚为编辑加入本文档…」存在的理由（代码注释）正是「toast 只有一个槽位、会被『渲染完成』盖掉」——是轨道设计逼出来的常驻说明。

另有两条真缺陷（不是精修）：曲线名称框按硬换行算行数，软换行的第二行被裁掉；编码 Agent 接口的「删除」一下点掉带密钥的配置，无确认无撤销。

GSAP 与 rareui 的结论先说：**不引入 GSAP**（`lib/motion.ts` 已具备它在这个产品里能派上用场的全部能力），但它的五条工程纪律值得照做；rareui 的 19 个组件里可借的是**四种行为**（就地确认、宽度稳定的数字、会滑动的选中轨、钳位反馈），不借任何一种外观。细则见第七节。

---

## 发现 · 一、控件（A）

### A1 · 文字对象的名称 / 内容框按硬换行算行数，软换行的第二行被裁掉

- 优先级：**P1**（用户看不全、也编辑不到自己正在改的值）
- 位置与状态：检查器「名称」「内容」（曲线 / 图例项 / 标题…），值超过一行宽时
- 证据：图 `a1-name-clipped.png`——曲线 `Catalyst (k = 0.125 $\mathrm{min^{-1}}$)` 只露出第一行，第二行只剩字顶。DOM 量测：`rows=1`，`scrollHeight 40 / clientHeight 24`，`overflow-y: auto`。代码 `components/inspector/ElementInspector.tsx:2197`：`rows={Math.min(4, text.split('\n').length)}`——只数 `\n`，不数软换行。
- 问题原因：多行框的高度由「硬换行数」决定，而 mathtext、长图例名、带单位的名字都是**一行源码、两行显示**。用户以为名字就那么长；要改第二行只能盲滚。
- 具体改法：按内容自适应高度——`field-sizing: content` 加 `max-height: 4 行`（Chromium 已支持），WKWebView（桌面壳）用 `scrollHeight` 回填 `height` 的 JS 兜底放进 `ui/TextArea`（一处，`TextSection.tsx:146` 的 `rows={2}` 也一起走它）；保留 4 行上限与内部滚动。
- 保留项：`beginTxn / endTxn`、Esc 失焦、`aria-label` 同一表达式、`onKeyDown` 的 stopPropagation。
- 验收：选中 `axes_0.lines_0`，名称框两行完整可见；输入第五行时框不再长高、内部可滚；jsdom 用例断言 `rows` 不再由 `\n` 决定（变异：把自适应去掉必红）。
- 依据：APPLE A01「让人掌控」/ 组件模式 §5「输入草稿态」/ WEB W05 精神 · PROPOSED

### A2 · 编码 Agent 接口「删除」一下点掉带密钥的配置，无确认无撤销

- 优先级：**P1**（不可逆丢失用户输入过的密钥与地址）
- 位置与状态：设置 › 编码 Agent › 详情 › 接口列表，每行「编辑 · 删除」
- 证据：`components/settings/AgentDetailView.tsx:266`：`onClick={() => void run(() => deleteAiEndpoint(e.id))}` → `lib/api.ts:1888` 直接 `DELETE`。对照：样式删除（`ProfilesSettings.tsx:340`）与终止会话（`NativeSessionCards.tsx:82`）都走 `askConfirm`。
- 问题原因：同一产品里三处危险操作两种契约；这一处恰好是用户最难重建的数据（密钥不回显）。
- 具体改法：**就地确认**（rareui「Delete button」的行为，不要它的弹簧）：点「删除」后那一行原位换成「删除「{label}」？ [删除] [取消]」，焦点落在「取消」，Esc / 失焦 = 取消，确认后行淡出（`animate-fade-out`）。密集列表里比模态轻，仍保住一次确认；若产品更愿意与样式页一致，退而走 `askConfirm`。
- 保留项：`run()` 的忙碌 / 错误处理、删除后 `AiCapabilities` 刷新、当前激活接口被删时的回退。
- 验收：点「删除」不发请求；确认后才发；Esc 取消；键盘可完成全程。
- 依据：APPLE A01「可恢复性支持自主性」/ 组件模式 §2「危险操作」 · PROPOSED

### A3 · 浮动栏里的取色块带着 86px 的残留宽度

- 优先级：P2（每次选中曲线 / 形状都看得见的空洞）
- 位置与状态：画布浮动栏（`ElementBar` 曲线 / 图例项、`SingleObjectBar` 两处）
- 证据：图 `a3-elementbar.png`——色块 32px 之后空 54px 才到数字框。`ColorField` 自 2026-09-11 只剩一块色块（`ui/Input.tsx:379`），四个调用点仍传 `className="w-[86px] shrink-0"`：`ElementBar.tsx:67 / :259`、`SingleObjectBar.tsx:122 / :194`。检查器里的同一控件没有这个宽度，所以只有浮动栏空。
- 具体改法：删掉四处 `w-[86px]`；`ColorField` 自己不接受外部宽度（或只接受 `className` 里的 margin）。
- 验收：浮动栏里色块与数字框间距 = `gap-1.5`；四处 grep `w-[86px]` 为 0。
- 依据：TAVOTTO 宪法第三节「行内 gap 按 4 / 8 走」 · PROPOSED

### A4 · 页签选中态换字重，拉丁文宽度抖动，邻居跟着挪

- 优先级：P2（en-US 每次切页签都抖；zh-CN 汉字等宽所以不抖）
- 位置与状态：`ui/Tabs` + `tabClass`（右栏「Properties / Assistant / Canvas」、问题面板「This figure / Whole document」、版本抽屉、刻度卡）
- 证据：DOM 量测 en-US：`Properties` 选中 55.72px → 未选中 54.22px；`Canvas` 38.11 → 38.98；于是 `Assistant` 与 `Canvas` 整体左移 1.5px。zh-CN 三个页签宽度不变（22 / 66 / 22）。
- 问题原因：`font-medium` 只在选中项上，SF Pro 的 500 比 400 宽约 2–3%。分段选择器不抖是因为 `flex-1` 等分；列表行不抖是因为块级。
- 具体改法：在 `tabClass` 给每个页签一个隐藏的加粗影子占位（`::after { content: attr(data-label); font-weight: 500; height: 0; visibility: hidden; display: block }`），文字居中；`Tab` 把 `children` 是字符串时同时写到 `data-label`。`Button active` 有文字时同法（`SIZES.sm/md`）。与 E2（下划线滑动）配合，切页签就完全不动了。
- 保留项：roving tabindex、`aria-controls`、下划线 2px 近黑。
- 验收：en-US 切换三个页签，`getBoundingClientRect().x` 全部不变（写成 keyboardPrimitives.test 的一条，变异：去掉影子必红）。
- 依据：APPLE A03「布局与分组建立层级」/ 宪法第五节 · PROPOSED

### A5 · 同一列里的数字框三种宽度

- 优先级：P3
- 位置与状态：X 轴刻度页：`3.5 pt` / `0.8 pt` / `8 pt` 框 62px、`0 °` 57px、图例「列数 1」85px；子图页范围框 57px
- 证据：图 `08-xticks-inspector-panel.png`、`10-axes-inspector-panel.png`（截图目录）；宽度随单位字符数变。
- 具体改法：`NumberField` 的宽度按**列**定而不是按单位定：检查器列里统一一档（建议 `7ch + 单位列`），`MmField` 那种 6 字符规则保留给毫米。
- 验收：同一页里单独成行的数字框右缘对齐。
- 依据：PROPOSED

### A6 · 「显示」开关排在它管辖的一组字段最后

- 优先级：P3
- 位置与状态：X / Y 轴刻度页「文字」组：字体 → 字号 → 颜色 → 数值格式 → 旋转 → **显示**
- 证据：图 `a6-visible-last.png`。
- 问题原因：关掉「显示」之后上面五行全部失效，但它在最后一行；用户从上往下改完才发现整组是关的。
- 具体改法：「显示」上移到组首（或做成分区小标题右侧的开关）；关态时下面的行 `opacity-40`（禁用一档）而不是消失。
- 保留项：写入语义（`labelsvisible` 那一类属性名不动）。
- 依据：APPLE A02「清楚的位置与行动」 · PROPOSED

### A7 · 「+ 添加背景」是检查器里唯一一颗整行宽的按钮

- 优先级：P3
- 位置与状态：文本角色页「背景」行
- 证据：图 `a7-add-bg.png`——secondary 整行宽，视觉分量高过它上面的所有属性行。
- 具体改法：行内值「无」+ 右侧 ghost「添加」；或与「透明背景」同形的开关。动作不变。
- 依据：宪法第五节「低频操作不给更重的视觉」 · PROPOSED

### A8 · 尺寸对（宽×高）的写法三种并存（S11 的兄弟）

- 优先级：P3
- 证据：zh-CN 文案里 `{{w}}×{{h}} mm` 10 处、`{{w}} × {{h}} mm` 5 处、`{{w}}×{{h}}cm` 1 处（素材卡「7.3×5.8cm」，图 `a8-cm-format.png`）、`{{w}}×{{h}} 毫米` 1 处；导出对话框同一屏里「80 × 57.6 mm」与「1890 × 1361 px」有空格、素材卡没有。
- 具体改法：`i18n/format` 加 `formatSize(w, h, unit)`（`×` 两侧一个空格、单位前一个空格），文案里只留 `{{size}}`；S11 那条 lint 扩一条正则。
- 依据：TAVOTTO 宪法第六节「数值与单位只有一个写法」 · PROPOSED

---

## 发现 · 二、字体（B）

### B1 · 等宽字体承担五种角色；数值与句子里都用了它

- 优先级：P2（贯穿检查器、浮动栏、导出、问题面板、顶栏的观感；是「工程工具」与「精密仪器」之间最大的一处差别）
- 位置与状态：`num-input`（NumberField 的值与单位）、顶栏缩放「126%」、HUD 读数、导出清单「7.33 pt → 大于 8 pt」、问题面板同句、路径 / 文件名 / 脚本名、快捷键（Tooltip / 菜单 / Kbd）、代码块
- 证据：146 处 `font-mono`（`grep` 不含测试）+ `index.css:415` 的 `num-input`。图 `b1-linewidth.png`：数字框「1.2 pt」的 SF Mono 在 12px 上比同列下拉里的「实线」黑一档；图 `b1-export-mono-zh.png` / `b1-export-mono-en.png`：清单每一行「7.33 pt → 大于 8 pt / above 8 pt」整句等宽，汉字与英文单词也被拉进等宽字体，一行两种字。计算样式：`input.num-input` = `ui-monospace 12px 400`，同列 `[role=combobox]` = `-apple-system 12px 400`。
- 问题原因：等宽在这里表达的是「这是技术信息」，不是「需要对齐」。数值对齐要的是等宽**数字**（`tabular-nums`），不是等宽**字体**；句子里的数值更不需要。系统字体 + `tabular-nums` 在 macOS 上就是 Keynote / Numbers 检查器的写法。
- 具体改法（分三档，逐档拍板）：
  1. **数值**（NumberField 值与单位、HUD、缩放、问题面板与导出清单里的「当前 → 要求」、树行计数）：`font-sans` + `tabular-nums`；`num-input` 改名 `type-number`（12px、tabular），单位列 ink-3 不变。
  2. **快捷键**：`Kbd` / Tooltip / 菜单右侧的「⌘K」用系统字体（SF 自带 ⌘⇧⌥⏎ 字形，等宽栈里这些符号本来就是回退出来的）。
  3. **路径 / 文件名 / 脚本名 / 代码**：保留等宽——这是它唯一的正当角色。
- 保留项：`tabular-nums`、数字右对齐、单位在框内、`MmField` 的 6 字符宽度（改用 `ch` 时以新字体重算）。
- 验收：检查器一列里数字框与下拉的字重目视一致；导出清单一行只有一种字体；`font-mono` 只剩 `<code>/<pre>`、路径、脚本名三类（写成 foundation.test 的白名单，按文件按个数）。
- 依据：APPLE A04「少量字族与明确层级」/ TAVOTTO profile §4「可变数值用 tabular-nums，不必把整行变等宽」 · PROPOSED（**需用户拍板**：这是既定「紧凑工具手感」的一次转向）

### B2 · 英文分区小标题仍靠大写 + 字距；中英两种骨架

- 优先级：P2
- 位置与状态：`type-section`（en-US 全部分区标题：TICK MARKS / LABELS / …）
- 证据：图 `b2-en-sections.png` vs `b2-zh-sections.png`。S9 之后 zh 用「12 / 500 / ink」与行标签拉开，en 在此之上**还**叠着 `uppercase + 0.06em`，于是英文标题读起来像 14px 页头，同一页在两种语言下不是同一个骨架。计算样式确认 zh 也在应用 0.72px 字距（汉字上看不见，但在英文名混排的标题上会露出来）。
- 问题原因：两种区分手段叠加；大写 + 字距是 web 后台的习惯，不是 Apple 检查器的（Keynote 是句首大写的 500 字重）。
- 具体改法：`type-section` 去掉 `text-transform` 与 `letter-spacing`，两种语言同一套「12 / 500 / ink」；菜单组标题（`MenuLabel`）同步。
- 保留项：与行标签（12 / 400 / ink-2）的「深 + 重 vs 浅 + 常规」。
- 验收：en / zh 同一页面截图，标题高度与视觉分量一致；`designMd.test` 的 token 对拍更新。
- 依据：APPLE A04 / 用户标准「什么语言都是同一个骨架」 · PROPOSED（S9 只拍板了 zh，en 待拍板）

### B3 · 顶栏缩放「126%」11px 等宽，与两侧 12px 控件不在一档

- 优先级：P3 · 随 B1 解决（改成 `type-control` + tabular）。

---

## 发现 · 三、图标（C）

整体：lucide 单库、四档尺寸、1.75 描边按比例缩放，在 14 / 16px 上与 1px 分隔线同重量，真实渲染里成立；刻度方向、图例位置、线型样张这些自绘 SVG 与 lucide 同一重量；B / I / ↵ / x² / Aa 这组字形钮在 12px 文字旁体量合适。**没有发现绘制层面的缺陷**，两条是语义与层级。

### C1 · 阻断与警告是同一个三角，只靠红 / 琥珀色区分

- 优先级：P2（问题面板、导出清单、左轨角标三处共用）
- 位置与状态：`lib/validationText.ts:26` `SEVERITY_ICON`：`error: TriangleAlert, warn: TriangleAlert`
- 证据：图 `c1-severity.png`——「字号低于绝对下限（阻断）」与「线宽不在规范档位上（警告）」同一形状；色弱或灰度打印时只剩文字「阻断 / 警告」可辨。
- 具体改法：`error → OctagonAlert`（lucide 有），`warn` 留三角，`suggestion` 灯泡、`not_verifiable` 盾形问号不动；表是同一份，面板 / 导出 / 角标自动一致；`ICONOGRAPHY.md` 第四节登记「阻断 = 八角」。
- 保留项：颜色 token（danger / warn）、`severityLabel` 文字。
- 验收：问题面板里阻断组与警告组在灰度截图下可分。
- 依据：WEB 精神「状态不能只靠颜色」/ ICONOGRAPHY「同一语义一个图标」（反过来：不同语义不共用一个图标） · PROPOSED

### C2 · 「问题」一个概念三个数字

- 优先级：P3（需产品决定）
- 证据：左轨红角标 **14**（整个文档），面板标题「问题 **13**」（当前图），页签「当前图 13 · 整个文档 14」。
- 具体改法：面板标题去掉数字（页签已经说了两次）；角标是「文档」还是「当前图」由产品定一次，不再各说各的。
- 依据：TAVOTTO 宪法第十六节「三处同一个词」精神 · PROPOSED

### C3 · 「更多 ›」与「源文件与高级 ›」同一字形、不同层级

- 优先级：P3
- 证据：DOM 纵坐标：「标记」下拉底 403 → 「更多」409（6px，是组的尾巴）；「更多」437 → 「源文件与高级」453（16px，是分区）。两行同样的 chevron 行，一个是组内展开、一个是分区。
- 具体改法：组内的「更多」改成分区末尾右对齐的 ghost 文字链接「更多…」（无 chevron），chevron 行只表示「分区」。
- 依据：PROPOSED

---

## 发现 · 四、层级（D）

### D1 · 底部三条消息轨道；上下文栏的常驻说明是它逼出来的

- 优先级：P2
- 位置与状态：`StatusBar.tsx`（HUD `bottom-3 left-3`；状态 toast `bottom-3` 居中）、`onboarding/HintToast.tsx`（`bottom-12 right-3`）、`WorkspaceContextBar.tsx:180`（「刚为编辑加入本文档，撤销即可移除。问题面板也会列出它的问题。」）
- 证据：图 `d1-two-toasts.png`——「✓ 渲染完成：Fig1_kinetics」与「💡 双击进入图内编辑。 ×」同时在屏，两种盒子、两个位置；图 `d1-contextbar-note.png`——上下文栏第二行的常驻说明。代码注释原话：「不是 toast——进快速编辑紧接着的『渲染完成』会把单槽位的状态盖掉」。
- 问题原因：消息没有一个归属，于是每种消息各开一条轨；单槽位 toast 又把一条本该短暂的状态变成常驻文字（用户标准：常驻说明不要）。
- 具体改法：一条通知轨（底部居中），**两个槽位**（新的在上、旧的顺延，最多两条），一种盒子（图标 + 文字 + 至多一个动作 / ×）；错误常驻可关、普通状态 4s、带动作的提示到用户操作为止。「刚为编辑加入」变成一条带「撤销」动作的通知，上下文栏第二行删除。HUD 留在左下——它是读数不是消息。操作提示（onboarding）并入同一轨。
- 保留项：`data-status-live` 的 aria-live 契约、错误 assertive、`usePresence` 退场、教程层的定位。
- 验收：进入图内编辑那一刻屏幕上只有一种盒子；「撤销」可从通知里触发；上下文栏只剩一行。
- 依据：APPLE A02「渐进披露」/ 组件模式 §12「Banner：详情已打开时避免重复播报」/ 用户标准「做减法」 · PROPOSED

### D2 · 改图助手空态：上半全空，下半四层叠着

- 优先级：P2
- 位置与状态：右栏「改图助手」无会话时
- 证据：图 `d2-assistant.png`——上下文卡之下约 1250px（@2x）空白；底部依次：说明句「助手会改图的脚本。改完可查看差异、回滚。」→ 四颗建议钮 → 「没有可用的编码 Agent。[打开编码 Agent 设置]」→ 输入框。
- 具体改法：说明句 + 建议钮作为**空态**放进空白区（居中、上方留 1/3），输入框与 Agent 状态留底；有会话后空态消失。说明句不新增，只是搬家。
- 保留项：作用范围行、⌘↵、`aria-live` 的状态。
- 依据：APPLE A02「内容组织」/ 组件模式 §13 · PROPOSED

### D3 · 问题面板把说明截成一行省略号

- 优先级：P2（Tooltip 成了读错误原因的唯一途径）
- 证据：图 `d3-truncate.png`——「有元素超出图幅 1.87 mm，导出时会裁掉超出的部…」；对象名「X 轴 “Reaction time (mi…”」。
- 具体改法：说明 `line-clamp-2`（允许两行，行高自然增高），对象名中间省略并保留引号内有区分力的尾部；`title` 保留完整值。
- 依据：组件模式 §4「不要通过固定很矮的行截断两行文本」「Tooltip 不应是读取核心错误信息的唯一途径」 · PROPOSED

### D4 · 设置 › 常规的「自动保存」是一行没有控件的设置行

- 优先级：P3
- 证据：图 `14-settings.png`（截图目录）——标题 + 说明「编辑停顿后自动写入本机」，控件列空。顶栏已有「已保存 21:32」。
- 具体改法：若自动保存不可关，删掉这一行（顶栏已表达）；若可关，给它真开关。
- 依据：用户标准「已表达过的不重复」 · PROPOSED

### D5 · 导出对话框仍剩一句常驻解释

- 优先级：P3
- 证据：「画布上的缩放不会带进导出。」常驻在范围说明之下，而范围分段已选着「原图尺寸」，第一句事实句也已说明按图幅出图。
- 具体改法：删除，或并进「要按原图尺寸导出的图」折叠。
- 依据：第一轮 B4 的残余 · 用户标准 · PROPOSED

---

## 发现 · 五、动效（E）

地基：`index.css` 四档时长 + 两条缓动、`lib/motion.ts` 的 `tween`（可取消、reduced-motion 同步落终态）/ `usePresence` / `useFlip` / `drawerMotion`，浮层进退场、抽屉、缩放、toast 都在 token 内且可打断——这一层不缺东西。缺的是下面几处「变化没有被解释」。

### E1 · 53 处过渡落在 Tailwind 默认的 150ms / ease 上（结构性）

- 优先级：P2（一行改法，覆盖全部；把纪律做进结构）
- 证据：`grep transition-(transform|colors|opacity) … | grep -v duration-` = 53 处（`Details.tsx:35`、`Field.tsx:67`、`SettingRow.tsx:360`、`Radio/Checkbox`… 全部折叠箭头都是）。它们跑的是 `--default-transition-duration: 150ms` 与 `cubic-bezier(.4,0,.2,1)`，两个都不是 token；`foundation.test` 抓 `duration-150` 字面量，抓不到「没写」。于是折叠箭头 150ms、树行箭头 120ms（`TreeRow` 写了 `duration-fast`）。
- 具体改法：`@theme` 里加 `--default-transition-duration: var(--duration-fast)` 与 `--default-transition-timing-function: <定一条 ease-standard>`——默认值本身成为 token，53 处不用动；门禁改成「不许写 `duration-[0-9]`」即可。
- 依据：GSAP 纪律「`gsap.defaults()` 一次设定」/ 宪法第七节 · PROPOSED

### E2 · 页签下划线与分段选中块不滑动，是「跳」到新位置

- 优先级：P2（日常路径里最频繁的两种切换；也是 rareui「Hook Sidebar」与 iOS / macOS 分段控件唯一值得学的一点）
- 位置与状态：`ui/Tabs`（右栏三页签、问题面板两页签、版本抽屉、刻度卡）、`ui/Segmented`（对齐 / 刻度方向 / 纵横比 / 作用范围）
- 证据：`tabClass.ts:4` 下划线是每个页签自己的 `after:` 伪元素，选中项换一个；`Segmented.tsx:133` 选中 tint 是每格自己的背景。切换时旧的消失、新的出现，中间没有轨迹。
- 具体改法：**共享指示物**——`TabList` 渲染**一条**下划线，按当前页签的 `offsetLeft / offsetWidth` 用 `transform: translateX() scaleX()`（或 `left/width`，两者之一）在 `--duration-base` + `--ease-pop` 内滑过去；`Segmented` 同法渲染一块 `bg-selected` 的选中底在按钮之下。首帧无动画（挂载时直接落位），reduced-motion 下即时。可以直接复用 `useFlip` 的量测。
- 与宪法的冲突：第七节「形态只有 opacity + ≤4px 位移」——**需要加一条修订**：「位置跟随型指示物（页签下划线、分段选中底）允许在同一控件内滑动，时长 base、无回弹」。这是本轮唯一一条要动宪法的建议。
- 保留项：`role=tab/radio` 语义、roving tabindex、方向键当场切换、`first:rounded-l-sm` 那类圆角。
- 验收：切页签 / 换分段值时指示物连续移动；jsdom 用例只断言「指示物只有一个、位置由选中项决定」，真浏览器看一次。
- 依据：APPLE A01「动效解释变化」/ WWDC 分段控件行为 · PROPOSED（含宪法修订）

### E3 · 折叠分组展开是瞬间跳出

- 优先级：P2
- 位置与状态：`ui/Field.Disclosure`（`{open && …}`，检查器「更多」「源文件与高级」「锚点」）、`ui/Details`（原生 `<details>`：导出高级选项、问题技术详情、设置页折叠）
- 证据：`Field.tsx:79`；`Details.tsx` 只换箭头。
- 具体改法：`Disclosure` 的内容包一层 `grid-template-rows: 0fr → 1fr` + `opacity`（内层 `min-h-0 overflow-hidden`），`--duration-base`；CSS 过渡天然可打断、无 JS；`Details` 用 `::details-content` 的同一手法（Chromium 131+），WKWebView 不支持时保持即时（不损失信息）。
- 与宪法的冲突：同 E2，属「布局跟随」而非位移，修订同一条。
- 依据：APPLE A01 · PROPOSED

### E4 · 开关的滑块动的是 `left`

- 优先级：P3
- 证据：`Toggle.tsx:66` `transition-[left]`。
- 具体改法：`translate-x-[10px]` + `transition-transform duration-fast`（GSAP 性能纪律：变换优先于布局属性）。
- 依据：gsap-performance · PROPOSED

### E5 · 复选框的勾 / 单选的点没有过渡，而框的颜色有

- 优先级：P3
- 证据：`Checkbox.tsx:40`、`Radio.tsx:34`：`opacity-0 peer-checked:opacity-100` 无 `transition`；框本身 `transition-colors duration-fast`——勾先到、底色后到。
- 具体改法：勾 / 点加 `transition-opacity duration-fast`（可加 `scale-90 → 100`，在宪法 0.97~1 之内）。
- 依据：宪法第七节 · PROPOSED

### E6 · 数字框钳位到上下界时没有反馈

- 优先级：P3（rareui「Duration Picker」值得借的一点：超出上限时钳位**并告诉你**）
- 证据：`NumberField` 有 `min/max`（如线宽 0.1–12），输入 20 回车显示 12，静默。
- 具体改法：钳位那一刻边框闪一次 `warn`（`--duration-slow`），或 2px 横向一次摆动（macOS 登录框的语言；≤4px、无回弹、reduced-motion 下不动）。
- 依据：APPLE A01「让人掌控」 · PROPOSED

### E7 · 「复制 → 已复制」换字换图标，按钮宽度跳

- 优先级：P3（rareui「Code Block」的复制钮之所以顺，是宽度不变 + 图标淡换）
- 证据：`settings/CopyButton.tsx:59-60`：`done ? <Check/> : <Copy/>`，文案「复制 / 已复制」，宽度随之变；`Button` 已有 `loadingLabel` 的双层网格占位，这里没用。
- 具体改法：复用那个占位（两份文案叠在同一格，取宽者），图标 `transition-opacity` 淡换。
- 依据：宪法第五节「忙碌按钮保留原尺寸」精神 · PROPOSED

---

## 六、分批实施

**批次 5 · 缺陷与一行改法（先做，互不依赖）**
A1 TextArea 自适应 → A2 接口删除就地确认 → A3 删四处 `w-[86px]` → C1 阻断换八角 → A8 `formatSize` → E1 默认过渡 token。
每条附变异反证；A1 / A2 / E1 各有 jsdom 判据。

**批次 6 · 动效（先改宪法第七节，再改原语）**
E2 共享指示物（Tabs → Segmented）→ E3 折叠展开 → E4 / E5 / E7 / A4 影子占位。
真浏览器各截一次；`keyboardPrimitives.test` 加「指示物唯一 + 位置由选中项决定」「页签 x 不变」。

**批次 7 · 层级**
D1 通知轨（含上下文栏第二行删除）→ D3 问题说明两行 → D2 助手空态 → D5 / D4。
D1 要过 `e2e` 里认 `data-status-live` 的用例。

**待拍板后单独一批**：B1（等宽的三档去留）、B2（en 去大写）、A6 / A7 / C2 / A5 / C3。

---

## 七、从 GSAP 与 rareui 能学什么、不学什么

### GSAP（`gsap-skills` 插件的八份技能）

**不引入库。** 理由：`lib/motion.ts` 已有 `tween`（缓动、取消、reduced-motion 同步落终态）、`usePresence`、`useFlip`、`drawerMotion`；GSAP core 约 23 KB gzip，且时长 / 缓动会变成第二个出处（宪法第七节「时长只来自 token」），Codex 内嵌画布的单文件产物还要再背一份；ScrollTrigger、stagger 入场、弹性缓动、SVG 形变在这个产品里没有用武之地（morphicons 的评估结论同样适用）。

**照做的五条纪律**（不需要库）：

| GSAP 的做法 | 对应到 Tavotto |
| --- | --- |
| 变换别名优先于布局属性（`x` 而不是 `left`） | E4 开关滑块；E2 指示物用 `transform` |
| `gsap.defaults()` 一次设定时长与缓动 | E1：`--default-transition-*` 成为 token |
| `overwrite: 'auto'`——新动画打断旧动画 | 缩放 tween 已做（`viewportStore.ts:87` 先 cancel）；E2 / E3 用 CSS 过渡天然可打断 |
| FLIP 处理布局变化 | `useFlip` 已有（画布页签、图层树），E2 可复用它的量测 |
| `matchMedia` 管 reduced-motion | `prefersReducedMotion()` 已有；E2 / E3 的新动画走它 |

### rareui.com/components（19 个组件，全部基于 `motion` + 弹簧 / gooey）

**借行为，不借外观**：

| rareui 组件 | 借什么 | 落到哪 |
| --- | --- | --- |
| Delete button | 就地确认、Esc 退回、确认后原位淡出 | A2 |
| Animated counter | 「宽度始终匹配数字」——变化的数字不让容器跳 | HUD 已做（`min-w-[13ch]`）；E7 CopyButton；不借滚轮数字 |
| Hook Sidebar | 选中轨滑到位；hover 画第二条更淡的轨 | E2 共享指示物（hover 轨对应现有 `surface-hover`） |
| Duration Picker | 超出上限钳位并反馈 | E6 |
| Code Block | 复制钮 → 对勾，宽度不变 | E7（去掉弹簧） |
| Notification bell | 计数归零角标缩走 | 左轨角标已是 fade（宪法内），不动 |

**不借**：Fluid / Matrix Orb、Gooey nav、Gravity letters、Folder 3D、Proximity / Bounce sidebar、Grid reveal、Step player 的图标形变——装饰性、弹簧、发光，与「画布是主角、工具安静」直接冲突；其中图标形变即 morphicons 一类，`ICONOGRAPHY.md` 第六节已否。

---

## 八、未验证与分歧

**分歧 1 · 等宽字体（B1）**：「紧凑工具手感」是 2026-09-11 定下的取舍；本轮的证据是同列两种字重、同句两种字体。建议先只做第 1 档（数值）在一个检查器页上 A/B 截图，再决定要不要动快捷键与顶栏。

**分歧 2 · 英文大写小标题（B2）**：S9 只拍板了 zh；en 保留大写是默认行为不是决定。两种语言一个骨架是用户自己的标准，但要用户看过 en 截图再定。

**分歧 3 · 宪法第七节的修订（E2 / E3）**：「≤4px 位移」写下时针对的是浮层进场；页签下划线滑 60px 不是那一类。建议加一句限定而不是放宽整条。

**分歧 4 · 角标数哪一个（C2）**：产品决定。

**未验证事实**
- 屏幕阅读器、`prefers-reduced-motion`、200% 放大、WebKit（桌面壳）、Windows 均未跑；E3 在 WKWebView 上的 `::details-content` 支持情况未查证。
- StepSlider（第一轮 E2）仍未看到。
- A1 的 `field-sizing: content` 在 WKWebView 的支持未查证，所以改法里保留 JS 兜底。
- 本轮量测都在 Chromium；页签宽度抖动的具体像素在 WebKit 的字体度量下可能不同，但方向一致。

## 附：截图索引（`docs/ux/img/apple-design-audit-2026-09-14-r2/`）

`a1-name-clipped` · `a3-elementbar` · `a6-visible-last` · `a7-add-bg` · `a8-cm-format` · `b1-linewidth` · `b1-export-mono-zh` · `b1-export-mono-en` · `b2-en-sections` · `b2-zh-sections` · `c1-severity` · `d1-two-toasts` · `d1-contextbar-note` · `d2-assistant` · `d3-truncate`。
整页截图（首页、工作台、五个检查器页、菜单、命令面板、导出、设置、问题、助手、en-US 对照）在会话 scratchpad `shots/` / `shots-en/`，未入库。

---

## 落地记录（2026-09-14 晚，分支 `ux/apple-design-r2`，叠在 batch4 之上）

拍板：1 乙 · 2 甲 · 3 甲 · 4 乙；五项小决定按建议。四个批次各一个提交：

| 批次 | 条目 | 提交 |
| --- | --- | --- |
| 5 缺陷与一行改法 | A1 A2 A3 C1 A8 E1 | `d9461d9a` |
| 6 动效 | E2 E3 E4 E5 E7 A4 + 宪法第七节修订 | `01240587` |
| 7 层级 | D1 D2 D3 D4（D5 撤回） | `0ba1e07f` |
| 8 拍板后 | B1（乙）B2 C2 A5 A6 A7 C3 E6 | 见 git log |

与报告不同的处置：

- **A4** 没用「隐藏的加粗影子」：页签的子元素是图标 + 文字 + 计数 + 运行点的组合，渲染两遍会把
  运行点与计数也复制一份、`textContent` 类的判据全部翻倍；改为选中项**不再加粗**（下划线已是不靠
  颜色的第二重线索），抖动归零（实测 en-US 三个页签 x 不变）。
- **D2** 只把说明句搬回滚动区正中（空态），起手式仍贴输入框——它们是输入的快捷方式，跟输入框走；
  部分收回 2026-09-13 审计 T37。
- **D5** 撤回：「画布上的{{list}}不会带进导出」只在画布上有被忽略的变换时出现，是状态不是解释。
- **E3** 的 `Reveal` 没包 `SpineFrameCard` 的逐边行（行是父级 flex 的直接子项，包一层会吃掉 gap）与
  问题面板的分组展开（列表语义）；原生 `<details>` 只在支持 `interpolate-size` 的引擎里长高。
- **E6** 选了「边框闪一次 warn」，没做横向摆动。
- **D1** 的动作叫「移除」不叫「撤销」：chromium e2e 首轮 7 条红全是 `getByRole('button', { name: '撤销' })`
  命中两颗（顶栏那颗 + 通知轨这颗）——同名两颗读屏也分不清；且只在撤销栈还停在加入那一刻才给这颗钮
  （`workspace.addedForEditDepth`），用户在图内又改了别的之后撤销撤的不是加入。

实测（Chromium，r2 构建）：分段选中底 79 → 124 → 134 → 136px、页签下划线 0 → 92 → 109 → 112px（180ms
ease-pop 的轨迹）；「更多」展开 grid 行高 107 → 171 → 194 → 204px；名称框 scrollHeight 40 = height 40（两行
完整）。jsdom：262 → 263 个用例文件，3952 → 3979 条全绿；`pnpm build`、`pnpm i18n:check` 通过；chromium e2e（除 playground / mcp）84 条全绿——首轮 8 红：7 条是上面那颗同名「撤销」、1 条是 `large-figure` 调 `python3` 时 PATH 里系统 3.9 排在前面（环境）。

