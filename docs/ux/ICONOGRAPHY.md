# 图标体系（Iconography）

> 用户反馈第 7 条：「目前 Tavotto 里面的图标非常不统一，太丑了，参考
> morphicons.com 来统一图标。」分支 `uf/07-icon-unify`（基线 `5bad5de5`）。
> 代码里的唯一出处是 `web/src/components/ui/Icon.tsx`，门禁在同目录的
> `iconography.test.tsx`；本文是它们的说明书，不另立规则。

## 一、结论先行

- 全产品只有**一套**图标：`lucide-react`（描边图标，24 网格）。不换库——
  morphicons 本身就跑在 Lucide / Heroicons / Tabler 之上，它统一的是**变形动画**，
  不是图形语言；仓库里 82 个文件早已在用 lucide。丑的根源是**同一套图标被用成了
  十种尺寸**，加上手绘 svg、别名、浏览器自带的折叠三角混在一起。
- 尺寸只有四档 `ICON_SIZE = { xs: 12, sm: 14, md: 16, lg: 20 }`，默认 **sm**；
  描边 `1.75`，**按比例缩放**；三个 React 根都套 `IconProvider`，不写 `size`
  的图标拿到默认档而不是 lucide 自己的 24 / 2。
- 同一语义全产品只用同一个图标（表见第四节）；有歧义的三组留给用户拍板（第五节）。
- 不接 morphicons：数据在第六节。

## 二、修改前的盘点

扫描范围：`web/src`（含 `mcp/`、`playground/`、`embedded/`）与 `src-tauri/`。
`src-tauri` 的界面只有两张启动 / 错误页（`shell/splash.html`、`shell/error.html`），
没有图标；桌面壳的其余界面全由 Flask 提供、与浏览器同一份，所以下表就是全部。

| 来源 | 数量 | 用在哪 | 尺寸 / 描边现状 |
| --- | --- | --- | --- |
| `lucide-react` 直接渲染 | 323 处 JSX，101 个不同图标，82 个文件 | 全部界面 | `size` 用了 **10 种**数值：12（145）、13（75）、11（58）、14（24）、10（7）、15（6）、16（3）、9（2）、18（1）、`SPINNER[size]`（2）；描边全部走 lucide 默认 2（按比例，即 11px 上 0.92px、14px 上 1.17px），只有一处手写 `strokeWidth={3}` |
| `lucide-react` 间接渲染（`icon: Icon` 再 `<Icon size={n}>`） | 20 处 | 菜单项、排列按钮表、左侧轨道、空状态、Agent 状态 | 11 / 12 / 13 / 14 / 16 / 20 六种 |
| 别名引入（同一张图两个名字） | 11 个名字，32 处 | `AlertTriangle`↔`TriangleAlert`、`Loader2`↔`LoaderCircle`、`CheckCircle2`↔`CircleCheck`、`XCircle`、`CircleHelp`、`MinusCircle`、`FileCode2`、`History`、`FileWarning`、`MoreHorizontal`、`ShieldQuestion` | — |
| 手写内联 `<svg>` 当图标 | 1 处 | `settings/SettingRow.tsx` 的折叠箭头（11 网格、描边 1.3） | 与 lucide 的 ChevronRight 不同重量 |
| 手写内联 `<svg>` **不是**图标 | 13 处，12 个文件 | 画布形状 / 箭头 / 选框本体（3）、检查器里跟着用户样式变的样本图（线型 / 标记 / 纹样 / 箭头头尾 / 图例句柄 / 刻度示意，7）、画布布局缩略图（1）、品牌标（1） | 保留，按个数豁免 |
| 浏览器自带的 `<details>` 折叠三角 | 16 处，10 个文件 | 导出高级选项、问题面板技术详情、脚本库、注册表对话框、Agent 详情、样式对话框、playground | 每个浏览器长得都不一样 |
| Unicode 字符当图标 | 1 处 | 刻度示意图里「已修改的边」芯片上的 `×` | 字体渲染，随字号变 |
| emoji、CSS 背景图、自定义图标字体 | 0 | — | — |
| 快捷键符号 ⌘ ⇧ ⌥ ⏎ ↑ ↓ | 若干 | 快捷键提示、菜单右侧 | **不是图标**，是按键名，不动 |
| `×` 作乘号（`80 × 57.6 mm`、`×3`） | 若干 | 尺寸、计数徽标 | 不是图标，不动 |
| 品牌标 `BrandMark` | 3 处（20 / 24 / 54） | 顶栏、项目选择器、关于页 | 唯一出处 `lib/brand.ts`，不在统一范围 |
| `AgentIcon` 外框（36 / 40） | 2 处 | 编码 Agent 列表 / 详情 | 外框不是图标；框里的字形已改走阶梯 |
| `generative-loaders` 的 `InlineLoader`（24） | 1 处 | AI 面板等待态 | 不是图标 |

## 三、定下的纪律（与理由）

### 尺寸阶梯

| 档 | px | 用途 |
| --- | --- | --- |
| `xs` | 12 | 折叠 / 下拉的箭头（一律 xs，它们是从属指示物）、徽标 / 角标里的小记号、11px 说明文字旁 |
| `sm` | 14（默认） | 与 12–13px 界面文字并排：菜单项、带文字的按钮、检查器行首、树行状态标记、横幅提示 |
| `md` | 16 | **只有图标**的按钮（28px 点击区）、顶栏工具、左侧图标轨道、对话框标题栏的关闭钮 |
| `lg` | 20 | 空状态、引导卡片、对话框级别的强调图 |

为什么是 12 / 14 / 16 / 20 而不是 14 / 16 / 20：界面字号 11–14px、控件高 28px，
密度比通常的 web app 高一档；14 是与 12–13px 正文并排时视觉体量相当的尺寸，
16 在 28px 点击区里留有 6px 呼吸，12 给箭头与徽标。原来的 11 / 13 / 15 三个
「中间值」全部归并——它们正是不统一的来源。

### 描边

`strokeWidth = 1.75`，`absoluteStrokeWidth = false`（按比例缩放）。

- 默认档 14px 上正好画成 **1.02px**，与界面其它 1px 分隔线同一重量；16px ≈ 1.17px、
  20px ≈ 1.46px、12px ≈ 0.88px，随尺寸自然加重而不是所有档一样粗。
- 不用绝对描边的硬理由：lucide 图形在 24 网格上留的最小间隙是 2 单位，12px 档上
  只剩 1px，描边固定到 1.5px 以上细节就糊成一团。
- 1.75 落在 morphicons 的 1.5–2.5 区间内，日后要接变形过渡不必再调重量。
- 加粗 `ICON_STROKE.emphasis = 2.5` 只给一种场景：填色小方块里的对勾（复选框选中态），
  1px 的勾在 14px 的蓝底上看不见。

### 对齐与间距

- 图标在 `inline-flex items-center` 的行里靠 flex 居中，不调 baseline；行内文字中
  偶尔夹一个图标（导出对话框的「编辑」链接）用 `inline` + 右边距。
- 按钮里图标与文字的间距由 `ui/Button` 定：`sm` 4px、`md` 6px；菜单项 8px。
- `IconProvider` 顺带给每个图标 `shrink-0`：之前它在三百多处被手写，漏一处就是
  窄行里被压成椭圆的图标。

### 机制

- `IconProvider` = lucide 1.31 的 `LucideProvider`（context），套在三个根：
  `main.tsx`、`playground/main.tsx`、`mcp/McpProviders.tsx`。
- 写法：`<X size={ICON_SIZE.md} />`；不写 `size` 即 sm。不写 `strokeWidth`。
- 折叠块：`ui/Details` 的 `Details` / `Summary`（原生 `<details>` 加 lucide 箭头）。

### 门禁（`web/src/components/ui/iconography.test.tsx`，TypeScript AST）

1. 非测试源码里没有内联 `<svg>`，豁免表**按文件按个数**（多画一个就红）；
2. 任何大写标签上 `size={数字}` 都红（间接渲染也抓），lucide 标签上的 `size`
   只能是 `ICON_SIZE.*`；`strokeWidth` 只能是 `ICON_STROKE.*`；不许 `absoluteStrokeWidth`；
3. 从 `lucide-react` 引入的名字必须在 `icons` 表里（规范名）；
4. JSX 文本里单独撑起一个元素的 ✕ × ▸ ▾ … 与任何 emoji 都红（`×{used}`、`{w} × {h}` 不算）；
5. 没有裸的原生 `<summary>`。

每条规则都有正反两组自检样例；上线前对着真源码跑过一次：改造前它报出
342 处尺寸字面量、1 处内联 svg、1 处手写描边、32 处别名、1 处字符图标。

## 四、语义统一表（同一含义只用一个图标）

| 语义 | 图标 | 改造前的并存者 |
| --- | --- | --- |
| 关闭 / 移除 | `X` | 刻度示意图芯片上的字符 `×` |
| 撤销 / 重做 | `Undo2` / `Redo2` | Codex 内嵌画布与 playground 用的 `RotateCcw` / `RotateCw` |
| 恢复到脚本原值 | `RotateCcw` | — |
| 刷新 / 重新扫描 | `RefreshCw` | 素材库刷新用的 `RotateCw` |
| 复制到剪贴板 | `Copy` | `ClipboardCopy`（脚本库诊断、设置页复制按钮） |
| 外部链接 / 在文件管理器里显示 | `ExternalLink` | `SquareArrowOutUpRight`（项目切换器） |
| 打开设置对话框 | `Settings` | `Settings2`（脚本库「打开环境设置」） |
| 编辑（改名、改规范） | `Pencil` | `PenLine`（素材卡「编辑图」）、`Settings2`（导出对话框「编辑规范」） |
| 调整参数 / 更多属性 | `SlidersHorizontal` | `Settings2`（AI 面板作用范围与 Agent） |
| 警告 | `TriangleAlert` | 别名 `AlertTriangle`；问题面板「检查失败」用的 `ShieldAlert` |
| 折叠 / 展开 | `ChevronRight` 转 90° | 手绘 svg（设置页）、浏览器 `<details>` 三角（16 处） |
| 下拉 | `ChevronDown` | — |
| 加载中 | `LoaderCircle` | 别名 `Loader2` |
| 帮助 | `CircleQuestionMark` | 别名 `CircleHelp` |
| 成功 | `CircleCheck`（状态）/ `Check`（选中标记） | 别名 `CheckCircle2` |

## 五、留给用户拍板的三组

1. **`Sparkles` 一图两义**：右栏「改图助手」入口与编码 Agent 注册表里 Claude 的头像框
   都是 `Sparkles`。建议助手保留 `Sparkles`，Claude 头像框换 `MessageSquareText`
   或 `Bot`（`AgentIcon.tsx` 的 `GLYPHS` 表一行）。未动。
2. **`ShieldAlert` 是否保留为「完整性 / 来源变了」的专用警告**：更新源文件按钮的
   「脚本与图已分叉」、playground 的「脚本被改过」仍用盾形；普通警告一律三角。
   这次只把问题面板里那一处明显是普通警告的换成了三角。若要一刀切，全部改
   `TriangleAlert` 是三处一行改动。
3. **AI 面板「作用范围 · Agent」按钮**：从 `Settings2` 改成了 `SlidersHorizontal`
   （与画布上下文栏「更多属性」同一个）。如果更愿意它读作「设置」，改回 `Settings`。

## 六、morphicons 评估（不接入）

| 项 | 数据 |
| --- | --- |
| 版本 / 许可 | 1.7.1，MIT |
| React 绑定体积 | `react.js` 5.1 KB + core（controller 5.2 KB、spring 18.1 KB、normalize 13.5 KB）≈ **42 KB 原始 / 13.3 KB gzip** |
| 依赖 | 消费的是**图标数据**，要另装 vanilla `lucide` 包（1.41.0），且要求与 `lucide-react`（1.31.0）版本对齐——多一对必须同步的版本 |
| 渲染方式 | 把图标折成**一条 `<path>`** 做形变；静止态 DOM 与 lucide-react 的多元素 svg 不同，现有按 `svg.lucide-braces` 取节点的用例会失效 |
| reduced-motion | 默认**无视**系统设置，需显式 `reducedMotion="user"`（仓库纪律要求支持） |
| jsdom | 靠 rAF + 弹簧插值；未验证，未在仓库里装 |
| 仓库里的状态切换对 | Eye/EyeOff（树、检查器）、Lock/LockOpen、Play/Pause（原生会话卡）、Check/Copy（复制按钮）——都在密集列表行里 |

结论：13 KB gzip 换四对小图标在列表行里的变形，还要在 Codex 内嵌画布（`canvas.html`
单文件产物）里再背一份，并新增一对「必须同步」的版本，不值。若以后要做，先接
`Play/Pause` 与 `Eye/EyeOff` 两对，并把 `reducedMotion="user"` 写进封装。

## 七、验证

- `cd web && pnpm test`：190 个文件 / 2601 条通过（改造前 189 / 2586，新增的是门禁）。
- `pnpm build`：通过。
- 真浏览器（agent-browser，1440×900，zh-CN）对顶栏、检查器、问题面板、导出对话框、
  命令面板、设置页、更多菜单各截修改前 / 后一张，见 PR 描述。
- 受管产物 `codex-plugin/mcp/widget/canvas.html` 与 `web/dist-playground` **未重建**
  （按任务约定留给合并前统一做）。
