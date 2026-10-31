/**
 * 图标微动效的登记表（ADR 0154；用户 2026-10-31 在演示页上逐个挑过、46 个全要）。
 *
 * 一枚图标的动作 = 什么时候播（`on`）+ 多长（`ms`）+ 哪几条路径参与、各演什么角色（`parts`）。
 * 关键帧在同目录 `motion.css`，按 `.icon-<kebab 名>` 与角色 `[data-m=…]` 取；两边的图标名集合
 * 由 `motion.test.ts` 对拍（这张表里有的 CSS 必须有，反之亦然）。几何一律不动——动作只是在
 * `defs.ts` 原有路径上加 transform / 描边进度，播完回到静止形。
 *
 * 角色（`data-m`）：
 * - `a`：整枚一起动（不写 `parts` 时每条路径都是 `a`）
 * - `p` / `q` / `r`（不够用再有 `u` `v` `w` `x`）：同一枚图标里分开动的几组（各图标自己在 CSS 里定义含义）
 * - `t`：依次动的一排（格子、散点、柱子、三点），按出现顺序带 `--k` 下标错开
 * - `d`：一笔画出来（`pathLength=1` + 描边进度）；`e` 是晚半拍的第二笔
 *
 * 触发（`on`）：
 * - `press`：所在按钮被点一下（点击后的下一帧播，所以按钮里换了图标——复制 → 勾、
 *   眼睛 ↔ 划掉——播的是换上的那枚）
 * - `hover`：指针进入所在按钮
 * - `enter`：只在调用方写了 `playIn` 时于挂载时播一次（状态图标：同一枚警告出现在问题列表里
 *   二十次不该二十次一起晃，只有「刚冒出来的那一条提示」才晃）
 * 任何图标写了 `playIn` 都会在挂载时播自己那一下，与 `on` 无关。
 *
 * 只有登记了的图标会动；`filled`（选中态孪生）的路径条数不同，要动就在 `sel` 里另写一份角色，
 * 没写的选中态保持静止。
 */
import type { IconName, IconVariant } from './defs'

export type MotionTrigger = 'press' | 'hover' | 'enter'
export type MotionRole = 'a' | 'p' | 'q' | 'r' | 'u' | 'v' | 'w' | 'x' | 't' | 'd' | 'e'
export type MotionParts = Partial<Record<keyof IconVariant, (MotionRole | null)[]>>

export interface IconMotion {
  on: MotionTrigger
  /** 一次的时长（ms）。图标动作的时长是登记豁免（宪法第七节「图标微动效」），只写在这里 */
  ms: number
  /** 默认形各路径的角色；省略 = 整枚 `a` */
  parts?: MotionParts
  /** 选中态（`filled`）各路径的角色；省略 = 选中态不动 */
  sel?: MotionParts | 'all'
}

export const ICON_MOTION: Partial<Record<IconName, IconMotion>> = {
  // ───────── 点一下播 ─────────
  Play: { on: 'press', ms: 420, parts: { s: ['p'] } },
  RefreshCw: { on: 'press', ms: 600 },
  RotateCcw: { on: 'press', ms: 600 },
  Download: { on: 'press', ms: 520, parts: { s: ['q', 'p', 'p'] } },
  Upload: { on: 'press', ms: 520, parts: { s: [null, 'p', 'p'] } },
  Copy: { on: 'press', ms: 420, parts: { s: ['q', 'p'] } },
  Check: { on: 'press', ms: 360, parts: { s: ['d'] } },
  Undo2: { on: 'press', ms: 440 },
  Redo2: { on: 'press', ms: 440 },
  Trash2: { on: 'press', ms: 480, parts: { s: ['p', 'p', null, null, null] } },
  Sparkles: { on: 'press', ms: 640, parts: { s: ['p'], f: ['q'] }, sel: { f: ['p', 'q'] } },
  Plus: { on: 'press', ms: 420 },
  Pencil: { on: 'press', ms: 520 },
  Save: { on: 'press', ms: 380, parts: { s: ['q', 'q', 'p'] } },
  ExternalLink: { on: 'press', ms: 420, parts: { s: [null, 'p', 'p'] } },
  Crop: { on: 'press', ms: 460, parts: { s: ['p', 'q'] } },
  Group: { on: 'press', ms: 460, parts: { s: ['r'], f: ['p', 'q'] } },
  Ungroup: { on: 'press', ms: 460, parts: { s: ['r', 'r'], f: ['p', 'q'] } },
  Maximize2: { on: 'press', ms: 440, parts: { s: ['p', 'p', 'q', 'q'] } },
  Pin: { on: 'press', ms: 460, sel: 'all' },
  Link2: { on: 'press', ms: 480, parts: { s: ['p', 'q', 'r'] } },
  Eye: { on: 'press', ms: 420, parts: { s: ['p', 'p'] }, sel: { f: ['p'], k: ['p'] } },
  EyeOff: { on: 'press', ms: 420, parts: { s: ['p', 'p'], k: ['d'], o: ['d'] } },
  Lock: { on: 'press', ms: 380, parts: { s: ['p', null], f: [null] }, sel: { s: ['p'], f: [null], k: [null] } },
  LockOpen: { on: 'press', ms: 380, parts: { s: ['p', null], f: [null] } },

  // ───────── 出现时播（调用方写 playIn） ─────────
  CircleCheck: { on: 'enter', ms: 560, parts: { s: ['p', 'd'] }, sel: { f: ['p'], k: ['d'] } },
  TriangleAlert: { on: 'enter', ms: 600, sel: 'all' },
  CircleAlert: { on: 'enter', ms: 480, sel: 'all' },
  CircleX: { on: 'enter', ms: 480, sel: 'all' },
  Info: { on: 'enter', ms: 480, parts: { s: [null, 'd'], f: ['p'] }, sel: { f: [null], k: ['d', 'p'] } },
  Unplug: { on: 'enter', ms: 520, parts: { s: ['p', 'p', 'p', 'q', 'q'] } },

  // ───────── 悬停播 ─────────
  // 左轨（选中的那颗是实心孪生，所以都写 sel）
  Folder: { on: 'hover', ms: 440, parts: { s: [null, 'p'] }, sel: { f: [null], k: ['p'] } },
  SquareMousePointer: { on: 'hover', ms: 480, parts: { s: ['q'], f: ['p'] }, sel: { f: ['q'], k: ['p'], o: ['p'] } },
  Paintbrush: { on: 'hover', ms: 560, sel: 'all' },
  ListChecks: { on: 'hover', ms: 520, parts: { s: ['d', 'e', null, null] }, sel: { f: [null, null], k: ['d', 'e'], s: [null, null] } },
  ClipboardList: { on: 'hover', ms: 520, parts: { s: [null, 'p', 'd'] }, sel: { f: [null, 'p'], k: ['d'] } },
  // 顶栏 / 状态
  Mail: { on: 'hover', ms: 520, parts: { s: [null, 'p'] } },
  HardDrive: { on: 'hover', ms: 520, parts: { s: [null, null], f: ['p'] } },
  Lightbulb: { on: 'hover', ms: 620, sel: 'all' },
  // 画布底部工具胶囊
  MousePointerClick: { on: 'hover', ms: 480, parts: { f: ['p'], s: ['t', 't', 't'] } },
  Type: { on: 'hover', ms: 460, parts: { s: ['p', 'd'] } },
  ArrowUpRight: { on: 'hover', ms: 480 },
  Square: { on: 'hover', ms: 460, sel: 'all' },
  Circle: { on: 'hover', ms: 520, sel: 'all' },
  Slash: { on: 'hover', ms: 380, parts: { s: ['d'] } },
  Shapes: { on: 'hover', ms: 460, parts: { s: ['p', 'q'] } },
  Tags: { on: 'hover', ms: 560, sel: 'all' },
  LayoutGrid: { on: 'hover', ms: 420, parts: { s: ['t', 't', 't', 't'] }, sel: { f: ['t', 't', 't', 't'] } },
  Layers: { on: 'hover', ms: 480, parts: { s: ['p', 'q', null] }, sel: { f: ['p'], s: ['q', null] } },
  Settings: { on: 'hover', ms: 520, parts: { s: ['p', null] }, sel: { f: ['p'], k: [null] } },
  SlidersHorizontal: { on: 'hover', ms: 700, parts: { s: ['u', null, 'p', null, 'x', 'q', 'v', 'w'] } },
  Search: { on: 'hover', ms: 620 },
  ChartLine: { on: 'hover', ms: 520, parts: { s: [null, 'd'] } },
  ChartScatter: { on: 'hover', ms: 380, parts: { s: [null], f: ['t', 't', 't', 't'] } },
  ChartColumn: { on: 'hover', ms: 380, parts: { s: [null], f: ['t', 't', 't'] } },
  RotateCcwClock: { on: 'hover', ms: 640, parts: { s: ['q', 'q', 'p'] } },
  Images: { on: 'hover', ms: 440, parts: { s: ['q', 'p', 'p'] }, sel: { s: ['q'], f: ['p'], k: ['p'] } },
  Image: { on: 'hover', ms: 520, parts: { s: [null, 'q'], f: ['p'] } },
  Ellipsis: { on: 'hover', ms: 360, parts: { f: ['t', 't', 't'] } },
  Wrench: { on: 'hover', ms: 520 },
  X: { on: 'hover', ms: 360 },
  ArrowRight: { on: 'hover', ms: 480 },
}

const LISTS = ['s', 'f', 'k', 'o'] as const

/**
 * 某一形（默认 / 选中）每条路径的角色与错开下标，供 `createIcon` 逐条贴 `data-m`。
 * 返回 null = 这一形不动。
 */
export function motionRoles(
  motion: IconMotion | undefined,
  variant: IconVariant,
  isSel: boolean,
): Partial<Record<keyof IconVariant, { role: MotionRole | null; k: number }[]>> | null {
  if (!motion) return null
  const spec = isSel ? motion.sel : motion.parts
  if (isSel && !spec) return null
  let k = 0
  const out: Partial<Record<keyof IconVariant, { role: MotionRole | null; k: number }[]>> = {}
  for (const list of LISTS) {
    const paths = variant[list]
    if (!paths) continue
    const roles = spec === undefined || spec === 'all' ? null : spec[list]
    out[list] = paths.map((_, i) => {
      const role: MotionRole | null = roles ? (roles[i] ?? null) : spec === undefined || spec === 'all' ? 'a' : null
      return { role, k: role === 't' ? k++ : 0 }
    })
  }
  return out
}
