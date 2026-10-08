import type { RowLabelWidth } from '../ui/Field'

/**
 * 检查器的行版式常量。
 *
 * **一张行网格**（2026-10-07 设计审计 §9.2 P0-2 / P0-3，取代 2026-09-15 打磨 L1 的「标签列一个数 88」）：
 *
 *     [ 标签 var(--insp-label) ] 8 [ 控件 minmax(0,1fr) ] 8 [ 状态槽 20 ]
 *
 * - 标签列 `--insp-label = clamp(88px, 28cqi, 112px)`：属性栏的容器（`Inspector` 内层的 `@container`）
 *   定义，窄栏 88（en-US「Stacking order / Frame opacity / Major tick mode」放得下，2026-09-12 critique P2），
 *   宽栏最多 112；
 * - 控件列**有右缘**：控件只有两档——`full`（撑满控件列）与 `half`（`INSPECTOR_HALF`，半列）；
 *   此前 Select 全宽、NumberField 4ch、ColorField 32px、`[data-stroke-fields]` 40px、成对格 6ch，各漂各的；
 * - 状态槽 20px **常驻**：恢复到脚本的 ↺ 住在里面，改值不再让控件少 34px；修改点悬挂在标签左边 8px
 *   （`controls/textRows.labeledWithState`），标签也不再右移。
 *
 * 所有检查器的行都传 `labelWidth={INSPECTOR_LABEL_W}`（= `'grid'`，`ui/Field.Row` 认它）。
 * 不走 `Row`、却要与控件列对齐的块（字段警告、方向示意、「全部清除」、恢复链接）用 `INSPECTOR_CONTROL_X`，
 * 不再手写 `pl-20` / `paddingLeft: 88`。
 */
export const INSPECTOR_LABEL_W: RowLabelWidth = 'grid'

/** 把一块内容的左缘对到控件列（标签列 + 8px 间距）、右缘对到状态槽左边 */
export const INSPECTOR_CONTROL_X = 'pl-[calc(var(--insp-label,88px)+0.5rem)] pr-7'

/** 半列控件：控件列的一半（两个半列之间是 Row 的 6px 间距） */
export const INSPECTOR_HALF = 'w-[calc((100%-0.375rem)/2)] shrink-0'

/** 标签列同宽的类名，给不走 `Row`（自己排的开关行 / 列表行）的地方用 */
export const INSPECTOR_LABEL_COL = 'w-[var(--insp-label,88px)]'

/** 属性栏容器上的变量定义（`Inspector` 内层 div 挂它；用例单独渲染某一页时可以自己包一层） */
export const INSPECTOR_CONTAINER = '@container [--insp-label:clamp(88px,28cqi,112px)]'
