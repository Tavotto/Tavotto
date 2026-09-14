/**
 * 图标集的几何定义——全产品 141 个界面图标的唯一出处（ADR 0052；说明书
 * `docs/ux/ICONOGRAPHY.md`）。
 *
 * 画法：24 网格；一切线条都是 2 单位描边、圆头圆角；实心形状 = 同一条路径再填色；
 * 闭合外形外角 ≥ 2.5、内角 ≥ 1；标点是直径 2.5 的纯填充圆；每个图标 ≤ 3 个可辨元素。
 * 规格是从 OpenAI 的 `@openai/apps-sdk-ui`（755 个图标）量出来再落到这个网格上的，
 * 图形本身都是自己画的。
 *
 * 每个图标：s = 描边路径，f = 实心路径，k = 挖空（蒙版里画黑），o = 挖空后再压上去的
 * 线；sel = 选中态的实心孪生（f 实心外形 + k 挖出内部细节 + s 保留的线）。只有真正
 * 用作开关 / 激活态的图标才有 sel（`filled` 属性对没有 sel 的图标不起作用）。
 *
 * 路径由下面几个小函数在模块加载时算出来（圆角矩形 / 圆 / 圆角多边形），改几何改
 * 数字就行，不用手算弧线。
 */

export interface IconPath {
  d: string
  /** 描边宽度；0 = 纯填充（点） */
  w?: number
  dash?: string
  /** 只在挖空 / 压线里用：这条按实心画 */
  solid?: boolean
}
export type IconPathSpec = string | IconPath
export interface IconVariant {
  s?: IconPathSpec[]
  f?: IconPathSpec[]
  k?: IconPathSpec[]
  o?: IconPathSpec[]
}
export interface IconDef extends IconVariant {
  sel?: IconVariant
}

const N = (v: number) => (Math.round(v * 1000) / 1000).toString()
// 圆角矩形（顺时针，从左上圆角后的点起）
const rr = (x: number, y: number, w: number, h: number, r: number) =>
  `M${N(x + r)} ${N(y)}h${N(w - 2 * r)}a${r} ${r} 0 0 1 ${r} ${r}v${N(h - 2 * r)}a${r} ${r} 0 0 1 -${r} ${r}h-${N(w - 2 * r)}a${r} ${r} 0 0 1 -${r} -${r}v-${N(h - 2 * r)}a${r} ${r} 0 0 1 ${r} -${r}z`
const circ = (cx: number, cy: number, r: number) => `M${N(cx - r)} ${N(cy)}a${r} ${r} 0 1 0 ${N(2 * r)} 0a${r} ${r} 0 1 0 -${N(2 * r)} 0z`
// 视觉半径 R 的实心点（描边 2 会向外长 1）
const dot = (cx: number, cy: number, R = 1.25): IconPath => ({ d: circ(cx, cy, R), w: 0 })
// 圆角多边形：pts 顶点，r 圆角半径（中心线）
const poly = (pts: number[][], r: number) => {
  const n = pts.length
  let area = 0
  for (let i = 0; i < n; i++) { const a = pts[i], b = pts[(i + 1) % n]; area += a[0] * b[1] - b[0] * a[1]; }
  const sweep = area > 0 ? 1 : 0
  let d = ''
  for (let i = 0; i < n; i++) {
    const p0 = pts[(i - 1 + n) % n], p1 = pts[i], p2 = pts[(i + 1) % n]
    const v1 = [p0[0] - p1[0], p0[1] - p1[1]], v2 = [p2[0] - p1[0], p2[1] - p1[1]]
    const l1 = Math.hypot(...v1), l2 = Math.hypot(...v2)
    const u1 = [v1[0] / l1, v1[1] / l1], u2 = [v2[0] / l2, v2[1] / l2]
    const cos = u1[0] * u2[0] + u1[1] * u2[1]
    const theta = Math.acos(Math.max(-1, Math.min(1, cos)))
    const t = Math.min(r / Math.tan(theta / 2), l1 / 2, l2 / 2)
    const a = [p1[0] + u1[0] * t, p1[1] + u1[1] * t], b = [p1[0] + u2[0] * t, p1[1] + u2[1] * t]
    const rr2 = t * Math.tan(theta / 2)
    d += (i === 0 ? 'M' : 'L') + `${N(a[0])} ${N(a[1])}A${N(rr2)} ${N(rr2)} 0 0 ${sweep} ${N(b[0])} ${N(b[1])}`
  }
  return d + 'z'
}
const regular = (cx: number, cy: number, R: number, n: number, rotDeg: number) => {
  const pts: number[][] = []
  for (let i = 0; i < n; i++) { const a = ((rotDeg + (360 / n) * i) * Math.PI) / 180; pts.push([cx + R * Math.cos(a), cy + R * Math.sin(a)]); }
  return pts
}
const chev = { down: 'M5.5 9l6.5 6.5L18.5 9', up: 'M5.5 15l6.5-6.5L18.5 15', right: 'M9 5.5l6.5 6.5L9 18.5', left: 'M15 5.5L8.5 12l6.5 6.5' }

// 圆形状态家族的公共外圈（中心线半径 9，外沿到 r=10）
const RING = circ(12, 12, 9)
const RING_SOLID = circ(12, 12, 9)
// 文档外形（15×17，右上折角）
const DOC = 'M13.5 3.5H7A2.5 2.5 0 0 0 4.5 6v12A2.5 2.5 0 0 0 7 20.5h10a2.5 2.5 0 0 0 2.5-2.5V9.5z'
const DOC_FOLD = 'M13.5 3.5v6h6'
// 盾
const SHIELD = 'M12 3l7.5 2.8v6.2c0 4.4-3 7.7-7.5 9.5C7.5 19.7 4.5 16.4 4.5 12V5.8z'
// 三角警示
const TRI = 'M9.9 5.5a2.4 2.4 0 0 1 4.2 0l7.4 12.7a2.4 2.4 0 0 1-2.1 3.6H4.6a2.4 2.4 0 0 1-2.1-3.6z'
// 大火花（四角凹边星）
const SPARK = 'M11 4C11.55 9.2 12.8 11.7 20 12.5 12.8 13.3 11.55 15.8 11 21 10.45 15.8 9.2 13.3 2 12.5 9.2 11.7 10.45 9.2 11 4z'
const SPARK_SM = 'M19 2.5c.2 1.7.7 2.3 2.5 2.5-1.8.2-2.3.8-2.5 2.5-.2-1.7-.7-2.3-2.5-2.5 1.8-.2 2.3-.8 2.5-2.5z'
// 坐标轴 L
const AXES = 'M4 4v14.5A1.5 1.5 0 0 0 5.5 20H20'
const PIN_BODY = 'M8.5 3.5h7V9l2 2.5V14h-11v-2.5L8.5 9z'
const EYE = 'M2.5 12c2.4-4.4 5.6-6.5 9.5-6.5s7.1 2.1 9.5 6.5c-2.4 4.4-5.6 6.5-9.5 6.5S4.9 16.4 2.5 12z'
const BULB = 'M9.5 16.5c-2.5-1.3-4-3.6-4-6.5a6.5 6.5 0 0 1 13 0c0 2.9-1.5 5.2-4 6.5'
const BULB_BASE = 'M9.5 16.5v1.5a2.5 2.5 0 0 0 2.5 2.5a2.5 2.5 0 0 0 2.5-2.5v-1.5'
const FOLDER = 'M3.5 7A2.5 2.5 0 0 1 6 4.5h3.2a2 2 0 0 1 1.4.6L12 6.5h6a2.5 2.5 0 0 1 2.5 2.5v8.5A2.5 2.5 0 0 1 18 20H6a2.5 2.5 0 0 1-2.5-2.5z'
const HEX = poly(regular(12, 12, 9.3, 6, -90), 2.4)
// 八角（平边朝上，边心距 9 与 RING 同外沿）
const OCT = poly(regular(12, 12, 9 / Math.cos(Math.PI / 8), 8, -67.5), 2.5)
const TWO = (x: number, y: number) => `M${x} ${y}a2 2 0 0 1 3.5 1.3c0 1.5-3.5 2.4-3.5 4.7h4`
const CURSOR = 'M6 5.5l11.5 4.6-5 1.9-1.9 5z'
const DIAMOND = poly([[12, 3], [21, 12], [12, 21], [3, 12]], 1.6)
const CARD = rr(3, 8, 13, 13, 2.5)
const CARD_BACK = 'M8 8V5.5A2.5 2.5 0 0 1 10.5 3h8A2.5 2.5 0 0 1 21 5.5v8a2.5 2.5 0 0 1-2.5 2.5H16'
const TAG = 'M5.5 3.5h5.2a2 2 0 0 1 1.4.6l8 8a2 2 0 0 1 0 2.8l-5.4 5.4a2 2 0 0 1-2.8 0l-8-8a2 2 0 0 1-.6-1.4V5.5a2 2 0 0 1 2-2z'
const IMG_FRAME = rr(3, 4, 18, 16, 2.5)
const MOUNTAIN = 'M3.5 17l4.5-4.5 3 3 3-3 6.5 6.5'
const CLIP_BOARD = 'M8.5 4.5H6.5A2.5 2.5 0 0 0 4 7v11.5A2.5 2.5 0 0 0 6.5 21h11a2.5 2.5 0 0 0 2.5-2.5V7a2.5 2.5 0 0 0-2.5-2.5h-2'
const CLIP = rr(8.5, 2.5, 7, 4, 1.5)
const CUBE = 'M12 3l8 4.5v9L12 21l-8-4.5v-9z'
const PENCIL = 'M4 20l4.1-.9L19.6 7.6a2.2 2.2 0 0 0-3.1-3.1L5 16z'


const bar = (x: number, y: number, w: number, h: number) => rr(x, y, w, h, 1.5)

export const ICON_DEFS = {
  // ───────── 左轨 ─────────
  LayoutGrid: { s: [rr(3, 3, 7, 7, 2), rr(14, 3, 7, 7, 2), rr(3, 14, 7, 7, 2), rr(14, 14, 7, 7, 2)],
    sel: { f: [rr(3, 3, 7, 7, 2), rr(14, 3, 7, 7, 2), rr(3, 14, 7, 7, 2), rr(14, 14, 7, 7, 2)] } },
  Images: { s: ['M7 7V5.5A2.5 2.5 0 0 1 9.5 3h9A2.5 2.5 0 0 1 21 5.5v9a2.5 2.5 0 0 1-2.5 2.5H17', rr(3, 7, 14, 14, 2.5), 'M3.5 17.5l4-4 3 3 2.5-2.5 3.5 3.5'],
    sel: { s: ['M7 7V5.5A2.5 2.5 0 0 1 9.5 3h9A2.5 2.5 0 0 1 21 5.5v9a2.5 2.5 0 0 1-2.5 2.5H17'], f: [rr(3, 7, 14, 14, 2.5)], k: ['M3.5 17.5l4-4 3 3 2.5-2.5 3.5 3.5'] } },
  Layers: { s: [poly([[12, 3], [20, 7], [12, 11], [4, 7]], 1.2), 'M4 13l8 4 8-4', 'M4 17l8 4 8-4'],
    sel: { f: [poly([[12, 3], [20, 7], [12, 11], [4, 7]], 1.2)], s: ['M4 13l8 4 8-4', 'M4 17l8 4 8-4'] } },
  SquareMousePointer: { s: ['M13.5 21H5.5A2.5 2.5 0 0 1 3 18.5v-13A2.5 2.5 0 0 1 5.5 3h13A2.5 2.5 0 0 1 21 5.5V13.5'], f: ['M12.5 12.5L21 15.6l-3.3 1.4-1.4 3.3z'],
    sel: { f: [rr(3, 3, 18, 18, 2.5)], k: [{ d: 'M12.5 12.5L21 15.6l-3.3 1.4-1.4 3.3z', w: 4 }], o: [{ d: 'M12.5 12.5L21 15.6l-3.3 1.4-1.4 3.3z', solid: true }] } },
  TriangleAlert: { s: [TRI, 'M12 9.5v4'], f: [dot(12, 17.2)],
    sel: { f: [TRI], k: ['M12 9.5v4', dot(12, 17.2)] } },
  Settings: { s: [HEX, circ(12, 12, 2.75)],
    sel: { f: [HEX], k: [{ d: circ(12, 12, 2.4), solid: true }] } },
  ClipboardList: { s: [CLIP_BOARD, CLIP, 'M8.5 13.5l2.4 2.4 4.6-5'],
    sel: { f: [CLIP_BOARD + 'z', CLIP], k: ['M8.5 13.5l2.4 2.4 4.6-5'] } },

  // ───────── 顶栏 ─────────
  Undo2: { s: ['M7 7h8a5 5 0 0 1 0 10h-4', 'M10.5 3.5L7 7l3.5 3.5'] },
  Redo2: { s: ['M17 7H9a5 5 0 0 0 0 10h4', 'M13.5 3.5L17 7l-3.5 3.5'] },
  RotateCcw: { s: ['M3.5 12a8.5 8.5 0 1 0 2.49-6.01', 'M6 2.5V6h3.5'] },
  RefreshCw: { s: ['M20.5 12a8.5 8.5 0 1 1-2.49-6.01', 'M18 2.5V6h-3.5'] },
  RotateCcwClock: { s: ['M3.5 12a8.5 8.5 0 1 0 2.49-6.01', 'M6 2.5V6h3.5', 'M12 8v4.5l2.8 1.8'] },
  Type: { s: ['M5.5 5.5h13', 'M12 5.5V19'] },
  Shapes: { s: ['M10.6 13H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v5.6', 'M13 10.6A5.5 5.5 0 1 1 10.6 13'] },
  ArrowUpRight: { s: ['M7 17L17 7', 'M8.5 7H17v8.5'] },
  Square: { s: [rr(3.5, 3.5, 17, 17, 2.5)], sel: { f: [rr(3.5, 3.5, 17, 17, 2.5)] } },
  Circle: { s: [circ(12, 12, 8.5)], sel: { f: [circ(12, 12, 8.5)] } },
  Slash: { s: ['M6 18L18 6'] },
  Tags: { s: [TAG], f: [dot(8, 8, 1.4)], sel: { f: [TAG], k: [dot(8, 8, 1.4)] } },
  Minus: { s: ['M6 12h12'] },
  Plus: { s: ['M12 6v12', 'M6 12h12'] },
  Maximize2: { s: ['M14.5 3.5h6v6', 'M20.5 3.5L14 10', 'M9.5 20.5h-6v-6', 'M3.5 20.5L10 14'] },
  Minimize2: { s: ['M20.5 9.5h-6v-6', 'M14.5 9.5L21 3', 'M3.5 14.5h6v6', 'M9.5 14.5L3 21'] },
  Download: { s: ['M4 15v2.5A2.5 2.5 0 0 0 6.5 20h11a2.5 2.5 0 0 0 2.5-2.5V15', 'M12 3.5V14.5', 'M7.5 10l4.5 4.5 4.5-4.5'] },
  Upload: { s: ['M4 15v2.5A2.5 2.5 0 0 0 6.5 20h11a2.5 2.5 0 0 0 2.5-2.5V15', 'M12 14.5V3.5', 'M7.5 8L12 3.5 16.5 8'] },
  Ellipsis: { f: [dot(5, 12, 1.9), dot(12, 12, 1.9), dot(19, 12, 1.9)] },
  ChevronDown: { s: [chev.down] },
  ChevronUp: { s: [chev.up] },
  ChevronRight: { s: [chev.right] },

  // ───────── 右栏 / 助手 ─────────
  Sparkles: { s: [SPARK], f: [SPARK_SM], sel: { f: [SPARK, SPARK_SM] } },
  Pin: { s: [PIN_BODY, 'M12 14v6.5'], sel: { f: [PIN_BODY], s: ['M12 14v6.5'] } },
  MousePointerClick: { f: [CURSOR], s: ['M4 8.5H2.5', 'M8.5 4V2.5', 'M5 5L3.9 3.9'] },
  SlidersHorizontal: { s: ['M3.5 7.5h8.75', 'M18.75 7.5h1.75', circ(15.5, 7.5, 2.25), 'M3.5 16.5h1.75', 'M11.75 16.5h8.75', circ(8.5, 16.5, 2.25)] },
  X: { s: ['M6 6l12 12', 'M18 6L6 18'] },
  Check: { s: ['M5 12.5l4.5 4.5L19 7'] },
  Pencil: { s: [PENCIL, 'M14.6 5.5l3.9 3.9'] },
  Bot: { s: [rr(4, 8, 16, 11, 3), 'M12 8V5.5'], f: [dot(12, 4, 1.4), dot(9, 13.5, 1.5), dot(15, 13.5, 1.5)],
    sel: { f: [rr(4, 8, 16, 11, 3), dot(12, 4, 1.4)], s: ['M12 8V5.5'], k: [dot(9, 13.5, 1.5), dot(15, 13.5, 1.5)] } },

  // ───────── 状态家族（同一个圆圈、同一套标点） ─────────
  CircleAlert: { s: [RING, 'M12 8v4'], f: [dot(12, 16.5)], sel: { f: [RING_SOLID], k: ['M12 8v4', dot(12, 16.5)] } },
  // 阻断（问题等级 error）：停车牌的八角，标点与 CircleAlert 同一套（2026-09-14 二审 C1 把阻断与警告分成两个形状）
  OctagonAlert: { s: [OCT, 'M12 8v4'], f: [dot(12, 16.5)] },
  Info: { s: [RING, 'M12 11.5V16'], f: [dot(12, 8)], sel: { f: [RING_SOLID], k: ['M12 11.5V16', dot(12, 8)] } },
  CircleCheck: { s: [RING, 'M8 12.5l3 3 5-6.5'], sel: { f: [RING_SOLID], k: ['M8 12.5l3 3 5-6.5'] } },
  CircleX: { s: [RING, 'M9 9l6 6', 'M15 9l-6 6'], sel: { f: [RING_SOLID], k: ['M9 9l6 6', 'M15 9l-6 6'] } },
  CircleQuestionMark: { s: [RING, 'M9.6 9.5a2.4 2.4 0 1 1 3.6 2.1c-.8.5-1.2 1-1.2 1.9v.3'], f: [dot(12, 16.8)],
    sel: { f: [RING_SOLID], k: ['M9.6 9.5a2.4 2.4 0 1 1 3.6 2.1c-.8.5-1.2 1-1.2 1.9v.3', dot(12, 16.8)] } },
  CircleMinus: { s: [RING, 'M8.5 12h7'], sel: { f: [RING_SOLID], k: ['M8.5 12h7'] } },
  CircleDashed: { s: [{ d: RING, dash: '5.42 4' }] },
  Ban: { s: [RING, 'M5.7 5.7l12.6 12.6'] },
  LoaderCircle: { s: ['M21 12a9 9 0 1 1-2.64-6.36'] },
  Lightbulb: { s: [BULB, BULB_BASE], sel: { f: [BULB + 'z'], s: [BULB_BASE] } },
  ShieldAlert: { s: [SHIELD, 'M12 8v4'], f: [dot(12, 15.8)], sel: { f: [SHIELD], k: ['M12 8v4', dot(12, 15.8)] } },
  ShieldCheck: { s: [SHIELD, 'M8.5 12l2.5 2.5 4.5-5'], sel: { f: [SHIELD], k: ['M8.5 12l2.5 2.5 4.5-5'] } },
  ShieldQuestionMark: { s: [SHIELD, 'M9.8 9.3a2.3 2.3 0 1 1 3.4 2c-.8.5-1.2 1-1.2 1.8v.2'], f: [dot(12, 16.2)],
    sel: { f: [SHIELD], k: ['M9.8 9.3a2.3 2.3 0 1 1 3.4 2c-.8.5-1.2 1-1.2 1.8v.2', dot(12, 16.2)] } },
  Zap: { s: [poly([[13.5, 3], [4.5, 13.5], [11.5, 13.5], [10.5, 21], [19.5, 10.5], [12.5, 10.5]], 0.9)], sel: { f: [poly([[13.5, 3], [4.5, 13.5], [11.5, 13.5], [10.5, 21], [19.5, 10.5], [12.5, 10.5]], 0.9)] } },

  // ───────── 通用动作 ─────────
  Copy: { s: [CARD_BACK, CARD] },
  Trash2: { s: ['M4 7h16', 'M9.5 7V5.5a1.5 1.5 0 0 1 1.5-1.5h2a1.5 1.5 0 0 1 1.5 1.5V7', 'M6 7l.8 11.7A2.5 2.5 0 0 0 9.3 21h5.4a2.5 2.5 0 0 0 2.5-2.3L18 7', 'M10 11v6', 'M14 11v6'] },
  Eye: { s: [EYE, circ(12, 12, 3.25)], sel: { f: [EYE], k: [{ d: circ(12, 12, 3.25), solid: true }] } },
  EyeOff: { s: [EYE, circ(12, 12, 3.25)], k: [{ d: 'M4 4l16 16', w: 6 }], o: ['M4 4l16 16'] },
  Lock: { s: ['M8 10.5V8a4 4 0 0 1 8 0v2.5', rr(4.5, 10.5, 15, 10, 2.5)], f: [dot(12, 15.5, 1.6)],
    sel: { s: ['M8 10.5V8a4 4 0 0 1 8 0v2.5'], f: [rr(4.5, 10.5, 15, 10, 2.5)], k: [dot(12, 15.5, 1.6)] } },
  LockOpen: { s: ['M8 10.5V8a4 4 0 0 1 7.9-.9', rr(4.5, 10.5, 15, 10, 2.5)], f: [dot(12, 15.5, 1.6)] },
  Link2: { s: ['M10 7H7.5a5 5 0 0 0 0 10H10', 'M14 7h2.5a5 5 0 0 1 0 10H14', 'M8.5 12h7'] },
  Unlink2: { s: ['M9.5 7H7.5a5 5 0 0 0 0 10h2', 'M14.5 7h2a5 5 0 0 1 0 10h-2', 'M10.5 13.5l3-3'] },
  Play: { f: ['M7.5 5.2v13.6a1.2 1.2 0 0 0 1.8 1L20 13a1.2 1.2 0 0 0 0-2L9.3 4.2a1.2 1.2 0 0 0-1.8 1z'] },
  Pause: { f: [rr(6, 5, 4, 14, 1.5), rr(14, 5, 4, 14, 1.5)] },
  Search: { s: [circ(10.5, 10.5, 7), 'M15.8 15.8L21 21'] },
  SearchX: { s: [circ(10.5, 10.5, 7), 'M15.8 15.8L21 21', 'M8 8l5 5', 'M13 8l-5 5'] },
  ExternalLink: { s: ['M11 4.5H6.5A2.5 2.5 0 0 0 4 7v10.5A2.5 2.5 0 0 0 6.5 20H17a2.5 2.5 0 0 0 2.5-2.5V13', 'M14 3.5h6.5V10', 'M20.5 3.5L12 12'] },
  Save: { s: ['M4 6.5A2.5 2.5 0 0 1 6.5 4H15l5 5v8.5a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5z', 'M8 4v4.5h6', 'M8 20v-5.5h8V20'] },
  Bookmark: { s: ['M6 4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V20.5l-6-4-6 4z'], sel: { f: ['M6 4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V20.5l-6-4-6 4z'] } },
  Wrench: { s: ['M20.5 8.5a5.5 5.5 0 0 1-7.2 5.2L6.7 20.3a2.1 2.1 0 0 1-3-3l6.6-6.6a5.5 5.5 0 0 1 5.2-7.2l.5.1-3.3 3.3.7 2.6 2.6.7 3.3-3.3z'] },
  ListFilter: { s: ['M4 7h16', 'M7 12h10', 'M10 17h4'] },
  Pipette: { s: ['M15.5 8.5l-9 9a2 2 0 0 1-1.2.6l-1.8.4.4-1.8a2 2 0 0 1 .6-1.2l9-9', 'M13 6l5 5', 'M17.2 3.6a2 2 0 0 1 2.8 2.8l-1.4 1.4-2.8-2.8z'] },
  Paintbrush: { s: ['M18.3 3.5a2.2 2.2 0 0 1 2.2 2.2l-8 8-3-3z', 'M9.5 10.5c-2.5.2-4.2 1.7-4.5 4.2-.2 1.6-1 2.6-2.5 3.3 3.2 1 7.5.2 8.7-3.2.3-1-.2-2.5-1.7-4.3z'] },
  KeyRound: { s: [circ(7.5, 7.5, 4), 'M10.5 10.5L20.5 20.5', 'M17.5 17.5l2.5-2.5', 'M14.5 14.5l2.5-2.5'] },
  Unplug: { s: ['M9 3.5V7', 'M15 3.5V7', 'M5.5 7h13v3a6.5 6.5 0 0 1-13 0z', 'M12 16.5v2.5', 'M9.5 21.5L12 19l2.5 2.5'] },
  Crop: { s: ['M6.5 2.5v13a2 2 0 0 0 2 2h13', 'M2.5 6.5h13a2 2 0 0 1 2 2v13'] },
  Group: { s: [{ d: rr(3, 3, 18, 18, 2.5), dash: '3.6 2.65' }], f: [rr(7, 7, 4, 4, 1), rr(13, 13, 4, 4, 1)] },
  Ungroup: { s: ['M3 8V5.5A2.5 2.5 0 0 1 5.5 3H8', 'M16 21h2.5a2.5 2.5 0 0 0 2.5-2.5V16'], f: [rr(7, 7, 4, 4, 1), rr(13, 13, 4, 4, 1)] },
  Crosshair: { s: [circ(12, 12, 8), 'M12 2.5v4', 'M12 17.5v4', 'M2.5 12h4', 'M17.5 12h4'] },
  Clipboard: { s: [CLIP_BOARD, CLIP] },
  ClipboardPaste: { s: [CLIP_BOARD, CLIP, 'M9 12h6', 'M9 16h4'] },
  Ratio: { s: [rr(3, 3, 11, 18, 2.5), 'M14 21h4.5a2.5 2.5 0 0 0 2.5-2.5V12'] },
  Replace: { s: [rr(13, 3, 8, 8, 2), rr(3, 13, 8, 8, 2), 'M9.5 7H8a3 3 0 0 0-3 3v1', 'M3.5 9.5L5 11l1.5-1.5'] },
  Scaling: { s: [rr(3, 3, 18, 18, 2.5), 'M9 15l7-7', 'M11 8h5v5'] },
  FlipHorizontal2: { s: [poly([[3.5, 7], [8.5, 12], [3.5, 17]], 0.9), poly([[20.5, 7], [15.5, 12], [20.5, 17]], 0.9), { d: 'M12 3.5v17', dash: '3 2.5' }] },
  FlipVertical2: { s: [poly([[7, 3.5], [12, 8.5], [17, 3.5]], 0.9), poly([[7, 20.5], [12, 15.5], [17, 20.5]], 0.9), { d: 'M3.5 12h17', dash: '3 2.5' }] },
  HardDrive: { s: [rr(3, 6, 18, 12, 2.5), 'M3 12h18'], f: [dot(17, 15, 1.4)] },
  BookOpen: { s: ['M12 6.5c-1.6-1.7-4.4-2.2-8.5-2v14.5c4.1-.2 6.9.3 8.5 2 1.6-1.7 4.4-2.2 8.5-2V4.5c-4.1-.2-6.9.3-8.5 2z', 'M12 6.5V21'] },
  Folder: { s: [FOLDER, 'M3.5 10h17'] },
  FolderOpen: { s: ['M3.5 11V7A2.5 2.5 0 0 1 6 4.5h3.2a2 2 0 0 1 1.4.6L12 6.5h6a2.5 2.5 0 0 1 2.5 2.5V11', 'M2.9 11h17.3a1.5 1.5 0 0 1 1.4 2l-2 5.5A2 2 0 0 1 17.7 20H5.3a2 2 0 0 1-1.9-1.5L2.9 11z'] },
  FolderPlus: { s: [FOLDER, 'M12 10.5v6', 'M9 13.5h6'] },
  FileUp: { s: [DOC, DOC_FOLD, 'M12 17.5v-6', 'M9.5 14l2.5-2.5 2.5 2.5'] },
  FileCodeCorner: { s: [DOC, DOC_FOLD, 'M10 12l-2 2.5 2 2.5', 'M14 12l2 2.5-2 2.5'] },
  FileExclamationPoint: { s: [DOC, DOC_FOLD, 'M12 11v3.5'], f: [dot(12, 17.5)] },
  FileSliders: { s: [DOC, DOC_FOLD, 'M8.5 13.5h7', 'M8.5 17h7'], f: [dot(13.5, 13.5, 1.5), dot(10.5, 17, 1.5)] },
  ImageOff: { s: [IMG_FRAME, MOUNTAIN], f: [dot(8, 9, 1.6)], k: [{ d: 'M4 4l16 16', w: 6 }], o: ['M4 4l16 16'] },
  Layers2: { s: [poly([[12, 5], [20, 9.5], [12, 14], [4, 9.5]], 1.2), 'M4 15.5l8 4.5 8-4.5'] },
  CornerDownLeft: { s: ['M19 4v7.5a2.5 2.5 0 0 1-2.5 2.5H5', 'M9 10l-4 4 4 4'] },
  CornerUpLeft: { s: ['M19 20v-7.5a2.5 2.5 0 0 0-2.5-2.5H5', 'M9 6l-4 4 4 4'] },
  ArrowUp: { s: ['M12 19V5', 'M6.5 10.5L12 5l5.5 5.5'] },
  ArrowDown: { s: ['M12 5v14', 'M6.5 13.5L12 19l5.5-5.5'] },
  ArrowLeft: { s: ['M19 12H5', 'M10.5 6.5L5 12l5.5 5.5'] },
  ArrowLeftRight: { s: ['M4 12h16', 'M8 8l-4 4 4 4', 'M16 8l4 4-4 4'] },
  ArrowUpToLine: { s: ['M4 4h16', 'M12 20V8', 'M7.5 12.5L12 8l4.5 4.5'] },
  ArrowDownToLine: { s: ['M4 20h16', 'M12 4v12', 'M7.5 11.5L12 16l4.5-4.5'] },
  MoveUp: { s: ['M12 21.5V3.5', 'M7.5 8L12 3.5 16.5 8'] },
  MoveDown: { s: ['M12 2.5v18', 'M7.5 16l4.5 4.5 4.5-4.5'] },
  MoveVertical: { s: ['M12 3v18', 'M7.5 7.5L12 3l4.5 4.5', 'M7.5 16.5L12 21l4.5-4.5'] },
  MoveHorizontal: { s: ['M3 12h18', 'M7.5 7.5L3 12l4.5 4.5', 'M16.5 7.5L21 12l-4.5 4.5'] },
  MoveUpRight: { s: ['M6 18L18 6', 'M8.5 6H18v9.5'] },

  // ───────── 排列（对齐 / 分布） ─────────
  AlignStartHorizontal: { s: ['M3.5 4h17'], f: [bar(6, 7, 4, 10), bar(14, 7, 4, 6)] },
  AlignCenterHorizontal: { s: ['M3.5 12h17'], f: [bar(6, 7, 4, 10), bar(14, 9, 4, 6)] },
  AlignEndHorizontal: { s: ['M3.5 20h17'], f: [bar(6, 7, 4, 10), bar(14, 11, 4, 6)] },
  AlignStartVertical: { s: ['M4 3.5v17'], f: [bar(7, 6, 10, 4), bar(7, 14, 6, 4)] },
  AlignCenterVertical: { s: ['M12 3.5v17'], f: [bar(7, 6, 10, 4), bar(9, 14, 6, 4)] },
  AlignEndVertical: { s: ['M20 3.5v17'], f: [bar(7, 6, 10, 4), bar(11, 14, 6, 4)] },
  AlignHorizontalDistributeCenter: { s: ['M7.5 3v18', 'M16.5 3v18'], f: [bar(5.5, 6, 4, 12), bar(14.5, 8.5, 4, 7)] },
  AlignVerticalDistributeCenter: { s: ['M3 7.5h18', 'M3 16.5h18'], f: [bar(6, 5.5, 12, 4), bar(8.5, 14.5, 7, 4)] },

  // ───────── 文字 ─────────
  Bold: { s: [{ d: 'M7.5 4h5.5a3.5 3.5 0 0 1 0 7H7.5z', w: 2.75 }, { d: 'M7.5 11h6.5a4.25 4.25 0 0 1 0 8.5h-6.5z', w: 2.75 }] },
  Italic: { s: ['M13.5 4.5h5', 'M5.5 19.5h5', 'M14.5 4.5l-5 15'] },
  Underline: { s: ['M6.5 3.5v7a5.5 5.5 0 0 0 11 0v-7', 'M4.5 20.5h15'] },
  Superscript: { s: ['M4 7l7 8', 'M11 7l-7 8', TWO(15.5, 4.5)] },
  Subscript: { s: ['M4 5l7 8', 'M11 5l-7 8', TWO(15.5, 13.5)] },
  CaseSensitive: { s: ['M3.5 15.5l3.5-9 3.5 9', 'M4.9 12h4.2', 'M20 15.5v-5.5a2.5 2.5 0 0 0-5 0', 'M20 12.5h-3a2 2 0 0 0 0 4h.5a2.5 2.5 0 0 0 2.5-2.5'] },
  TextAlignStart: { s: ['M4 6h16', 'M4 12h10', 'M4 18h14'] },
  TextAlignCenter: { s: ['M4 6h16', 'M7 12h10', 'M5 18h14'] },
  TextAlignEnd: { s: ['M4 6h16', 'M10 12h10', 'M6 18h14'] },

  // ───────── 图内元素角色 ─────────
  Fullscreen: { s: ['M3 8V5.5A2.5 2.5 0 0 1 5.5 3H8', 'M16 3h2.5A2.5 2.5 0 0 1 21 5.5V8', 'M21 16v2.5a2.5 2.5 0 0 1-2.5 2.5H16', 'M8 21H5.5A2.5 2.5 0 0 1 3 18.5V16', rr(8, 8, 8, 8, 1.5)] },
  Box: { s: [CUBE, 'M12 12l8-4.5', 'M12 12L4 7.5', 'M12 12v9'] },
  Image: { s: [IMG_FRAME, MOUNTAIN], f: [dot(8, 9, 1.6)] },
  ChartLine: { s: [AXES, 'M7.5 15l4-5 3.5 3 5-6.5'] },
  ChartScatter: { s: [AXES], f: [dot(8.5, 14, 1.6), dot(12, 9, 1.6), dot(15.5, 12.5, 1.6), dot(18.5, 7, 1.6)] },
  ChartColumn: { s: [AXES], f: [rr(7, 12, 3, 5, 1), rr(11.5, 7.5, 3, 9.5, 1), rr(16, 10, 3, 7, 1)] },
  ChartArea: { s: [AXES], f: ['M7.5 16.5V13l4-5 3.5 3 5-6.5v12z'] },
  GitCommitVertical: { s: ['M12 4v16', 'M8.5 4h7', 'M8.5 20h7'], f: [dot(12, 12, 2.4)] },
  WavesHorizontal: { s: ['M3 7.5c2-2 4-2 6 0s4 2 6 0 4-2 6 0', 'M3 12c2-2 4-2 6 0s4 2 6 0 4-2 6 0', 'M3 16.5c2-2 4-2 6 0s4 2 6 0 4-2 6 0'] },
  Ruler: { s: [rr(3, 8, 18, 8, 2), 'M7 8v3.5', 'M11 8v5', 'M15 8v3.5', 'M19 8v5'] },
  LayoutList: { f: [rr(3.5, 5, 5, 5, 1.2), rr(3.5, 14, 5, 5, 1.2)], s: ['M12 7.5h8.5', 'M12 16.5h8.5'] },
  Blend: { s: [rr(6.5, 3, 7, 18, 2.5), "M6.5 9h7", "M6.5 15h7", "M16 6h2.5", "M16 12h2.5", "M16 18h2.5"] },
  Diamond: { s: [DIAMOND], sel: { f: [DIAMOND] } },
  Hexagon: { s: [poly(regular(12, 12, 9.3, 6, -90), 1.6)] },
  Triangle: { s: [poly([[12, 4], [21, 20], [3, 20]], 1.8)] },
  Braces: { s: ['M8 3.5H7.5a2 2 0 0 0-2 2v4a2 2 0 0 1-2 2 2 2 0 0 1 2 2v4a2 2 0 0 0 2 2H8', 'M16 3.5h.5a2 2 0 0 1 2 2v4a2 2 0 0 0 2 2 2 2 0 0 0-2 2v4a2 2 0 0 1-2 2H16'] },

} satisfies Record<string, IconDef>

export type IconName = keyof typeof ICON_DEFS
