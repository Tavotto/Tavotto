# ADR 0154：界面图标微动效（46 个图标，一次性、跟着按钮走）

日期：2026-10-31 · 状态：**Accepted**（用户 2026-10-31 在演示页上逐个看过：第一批 16 个「都要」，第二批 30 个「全部都要」）
相关：[0052 自绘图标集](0052-in-house-icon-set.md)；`docs/ux/DESIGN_CONSTITUTION.md` 第七节「图标微动效」；`docs/ux/ICONOGRAPHY.md`「微动效」。

## 动机

用户看了一组细线图标的微动效（光标沿路径画出、星星闪一下、表盘指针扫一格……），要 Tavotto 的界面图标也有。品牌标不动，
只改界面图标。演示页先用 `defs.ts` 的真实几何把 46 个动作做出来、实际尺寸（14 / 16 px）放进顶栏 / 左轨 / 图层面板样机里给用户
挑，两轮全部通过。

宪法第七节原来的形态只有「opacity + ≤4px 位移 + scale 0.97~1」，时长只来自四个 token——图标动作（转 90° / 360°、格子缩到
0.6、斜线一笔画出、时长 360–700ms）在它之外，所以这里登记成一条豁免，范围只到图标集。

## 决定

### 登记表、关键帧、播放端三处分开

- **登记**：`web/src/components/ui/icons/motion.ts` 的 `ICON_MOTION`——每枚图标的触发（`press` / `hover` / `enter`）、时长、
  每条路径的角色（`a` 整枚 / `p` `q` `r` 分组 / `t` 依次 / `d` 一笔画出）。选中态（`filled`）路径条数不同，要动另写 `sel`，没写就不动。
- **关键帧**：同目录 `motion.css`，按 `.icon-<名>[data-play] [data-m=<角色>]` 写；只在 svg 带 `data-play` 时动，没有 `forwards`，
  播完回到静止形。几何一律不改，只有一处例外：`SlidersHorizontal` 会缩短的两段改成从外端反向画，会变长的两段
  静止不动、另加两条静止不画的延长线（`ghost`：描边透明，只在动作里显形）；轨道按 `stroke-dasharray` 伸缩，旋钮滑到哪线接到哪。
  静止形 Chromium / WebKit 14–32px 实测与原来零像素差。也试过「短段画成全长 + dash 只露一截」：WebKit 里 dash 的圆头抗锯齿略不同，弃用，旋钮滑到哪线接到哪。试过「整条线 + 蒙版挖口」：Chromium 静止形一致，
  WebKit（macOS 应用）里蒙版的抗锯齿与普通描边不同，14 / 16px 上有像素差，弃用。
- **播放端**：`web/src/components/ui/iconMotion.ts`。`installIconMotion()` 在三个入口（桌面 / playground / MCP）各装一次，
  在 document 上委托：点按钮 → 下一帧播按钮里的 `press` 图标（按钮里换了图标——复制 → 勾、眼睛 ↔ 划掉、锁 ↔ 开锁——播的是换上的那枚）；
  指针进按钮 → 播 `hover` 图标。图标只归属离它最近的那颗可点的东西；禁用的不播。`playIcon(el)` 手动重播（`CopyButton`
  的勾一直挂着、靠淡换，所以在「已复制」那一刻手动播）。
- **`enter`**：状态图标（完成、警告、失败、提示、断开）同一枚可能在列表里出现二十次，所以只在调用方写 `playIn` 时于挂载那一下播：
  `Notice`、状态 toast（`StatusBar.StatusIcon`）、准备卡状态图标、导出核验通过、教程完成。

### 第三轮补充（2026-10-31）

用户指出左轨的图内 / 样式 / 问题、顶栏联系作者、画布底部工具胶囊里的图标还不动：补了 16 枚（`Folder` `SquareMousePointer` `Paintbrush` `ListChecks` `ClipboardList` `Mail` `HardDrive` `Lightbulb` 与工具胶囊的 8 枚），左轨的都写了选中态角色。另加**通用轻点**：按钮里没有自己点按动作的图标（没登记的、或登记的是悬停 / 出现），点的时候整枚缩到 0.84 再弹回（260ms，只动 svg 本身），免得任何一颗图标按钮点了毫无反应。

### 命令面板补齐（2026-10-31）

用户：命令面板里的图标也要一致、清晰且有动画。面板用到的 30 个图标里补了 11 个此前没有动作的（布局横排 / 竖排、书签、快捷键帮助、
打开 / 存为排版、适应画布、图层轮换、元素列表、标尺、缩放到选中）。面板的行多半是键盘 ↑↓ 走过去的，点了就执行并关面板，
悬停 / 点按两种触发都看不见，所以播放端加一条：**列表项变成高亮**（`[role=option|menuitem|tab]` 的 `aria-selected` 变 true，
键盘或指针都算）时，它的图标不分触发方式播一次（document 上一个 MutationObserver，只看这一个属性）。

### 边界

- 只有「进行中」转圈循环（原来的 `Spinner`，不在本表）；其余全部一次性，时长 300–720ms（用例钉住），CSS 里不许 `infinite`。
- `prefers-reduced-motion` 下一律不播（CSS 与 JS 两道）。
- 画布、图本身、品牌标不加动效。
- 展开 / 下拉的箭头转向已经由各处 `transition-transform` + `rotate-90 / 180` 做了，不重复登记。

### 看护

`web/src/components/ui/icons/motion.test.tsx`：登记表 ↔ `motion.css` 双向同源（登记了没写关键帧、写了关键帧没登记都红）；
角色条数 = `defs.ts` 那一形的路径条数（改几何时角色不会错位到别的路径上）；委托的归属 / 禁用 / 悬停不重播 / 减少动态效果；
`playIn` 与调用方 `style` 合并。

## 后果

- 加一枚动作：`motion.ts` 登记 + `motion.css` 写关键帧，两边缺一就红。改 `defs.ts` 里一枚有动作图标的路径条数，要同步角色表。
- 图标多了 `data-motion` / `data-m` / `pathLength` 属性；按 DOM 结构认图标的用例只认类名 `icon-<名>`，不受影响。
