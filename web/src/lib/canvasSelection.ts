/**
 * 画布上改字时的文字选区：底与字都由这里定（Codex P2，PR #834 两轮）。
 *
 * 全局 `::selection` 是界面的规矩：选区底是 accent 28% 的半透明 tint，选中的字换界面的 ink。画布上的字坐在**文档的底**上
 * ——页面底色（CanvasPage）与文字框底色（`obj.bg`）都由用户定：
 *   - 界面 ink 在暗色里是浅灰，落在白纸上看不见（第一版换成 paper-ink）；
 *   - paper-ink 落在深底上看不见（第一轮 P2：#000 上 1.06:1）；
 *   - 半透明 tint 叠在中灰上，两种纸墨谁都到不了 4.5:1（第二轮 P2：#7d7d7d 上 3.95:1）——只换字不够，底也得自己定。
 *
 * 所以纸上的选区是**不透明**的两块（`--color-paper-selection` 浅、`--color-paper-selection-deep` 深，都从纸与画布选中蓝
 * `sel` 派生，两套主题同值），各配一种纸墨：字对选区底的对比度是常数，与用户的底色无关。这里只挑哪一块：
 * 实际的底（文字框底色 → 页面底色 → 纸；透明页面画的是浅棋盘格，按纸算）深就用深的那块、浅就用浅的那块；
 * 那块与底拉不开 1.3:1（底色恰好与它同深浅）时换另一块——选区总看得出来。半透明的底按 sRGB 叠到下一层上；认不出的颜色串当作没有这一层。
 *
 * 编辑态的占位（不是选区）落在底上本身，按底的深浅二选一纸墨（`groundInkFor`）。
 *
 * 盲点（写在明处）：字下面压着的图 / 图形不在这条链里（只认文档里写明的底色）；字被拖到页面外、落在画布灰上时也按页面算。
 * 选区两块的对比度只取决于 token，不取决于这里的判断——判断错了最多是选区的深浅与底不搭，字照样 ≥4.5:1。
 */

/** 纸墨：`paper`（浅）/ `paper-ink`（深） */
export type SelectionInk = 'paper' | 'paper-ink'
/** 纸上的选区用哪一块：`light` = `--color-paper-selection` + paper-ink，`deep` = `--color-paper-selection-deep` + paper */
export type CanvasSelection = 'light' | 'deep'

/** 纸（`--color-paper`，两套主题同值；`tokenContrast.test` 守着它与 token 同值）——链的最底层 */
export const PAPER_RGB: readonly [number, number, number] = [255, 255, 255]
/** 纸上的墨（`--color-paper-ink`，两套主题同值；同上，`tokenContrast.test` 守着同值）——深 / 浅换手点按它算，不按纯黑 */
export const PAPER_INK_RGB: readonly [number, number, number] = [0x1b, 0x1b, 0x18]
/** 画布选中蓝（`--color-sel`，两套主题同值；同上） */
export const SEL_RGB: readonly [number, number, number] = [0x46, 0x85, 0xe2]
/** 两块选区的公式（与 index.css 的 `color-mix(in srgb, var(--color-sel) N%, …)` 同源；`tokenContrast.test` 守着） */
export const SELECTION_SEL_PCT = { light: 35, deep: 50 } as const
const mix = (a: readonly number[], pct: number, b: readonly number[]) =>
  [0, 1, 2].map((k) => a[k] * (pct / 100) + b[k] * (1 - pct / 100)) as [number, number, number]
/** 两块选区合成后的不透明颜色 */
export const SELECTION_RGB: Record<CanvasSelection, [number, number, number]> = {
  light: mix(SEL_RGB, SELECTION_SEL_PCT.light, PAPER_RGB),
  deep: mix(SEL_RGB, SELECTION_SEL_PCT.deep, PAPER_INK_RGB),
}
/** 选区与底至少拉开这么多（与界面选区同一条「看得出来」的下限） */
export const SELECTION_VISIBLE_MIN = 1.3

type Rgba = [number, number, number, number]

/** 文档里写的颜色串 → RGBA（0–255，alpha 0–1）；认不出 → null */
export function parseDocColor(raw: string | null | undefined): Rgba | null {
  if (!raw) return null
  const s = raw.trim().toLowerCase()
  if (s === 'transparent') return [0, 0, 0, 0]
  const hex = s.match(/^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/)?.[1]
  if (hex) {
    const full = hex.length <= 4 ? [...hex].map((c) => c + c).join('') : hex
    const n = (i: number) => parseInt(full.slice(i, i + 2), 16)
    return [n(0), n(2), n(4), full.length === 8 ? n(6) / 255 : 1]
  }
  const fn = s.match(/^rgba?\(([^)]*)\)$/)?.[1]
  if (fn) {
    const parts = fn.split(/[\s,/]+/).filter(Boolean)
    if (parts.length < 3) return null
    const ch = parts.slice(0, 3).map(Number)
    if (ch.some((v) => !Number.isFinite(v))) return null
    const a = parts[3] === undefined ? 1 : parts[3].endsWith('%') ? parseFloat(parts[3]) / 100 : Number(parts[3])
    if (!Number.isFinite(a)) return null
    return [ch[0], ch[1], ch[2], Math.min(Math.max(a, 0), 1)]
  }
  return null
}

/** 自下而上把各层底色叠起来（最底是纸），得到字实际坐着的那个不透明颜色 */
export function effectiveGround(layersBottomUp: readonly (string | null | undefined)[]): [number, number, number] {
  let g: [number, number, number] = [...PAPER_RGB]
  for (const raw of layersBottomUp) {
    const c = parseDocColor(raw)
    if (!c) continue
    const a = c[3]
    g = [0, 1, 2].map((k) => g[k] * (1 - a) + c[k] * a) as [number, number, number]
  }
  return g
}

function luminance([r, g, b]: readonly number[]): number {
  const f = (v: number) => {
    const x = v / 255
    return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}

const contrast = (a: readonly number[], b: readonly number[]) => {
  const [x, y] = [luminance(a), luminance(b)]
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

const groundOf = (textBg: string | null | undefined, page: { bg?: string; transparent?: boolean }) =>
  effectiveGround([page.transparent ? null : page.bg, textBg])

/** 这块底上，两种纸墨哪个对比度大（占位用；深底 → `paper`） */
export function groundInkFor(
  textBg: string | null | undefined,
  page: { bg?: string; transparent?: boolean },
): SelectionInk {
  const g = groundOf(textBg, page)
  return contrast(g, PAPER_RGB) > contrast(g, PAPER_INK_RGB) ? 'paper' : 'paper-ink'
}

/** 这段字选中时用哪一块选区：跟底同深浅的那块；与底拉不开 1.3:1 就换另一块 */
export function canvasSelectionFor(
  textBg: string | null | undefined,
  page: { bg?: string; transparent?: boolean },
): CanvasSelection {
  const g = groundOf(textBg, page)
  const prefer: CanvasSelection = groundInkFor(textBg, page) === 'paper' ? 'deep' : 'light'
  const other: CanvasSelection = prefer === 'deep' ? 'light' : 'deep'
  return contrast(SELECTION_RGB[prefer], g) >= SELECTION_VISIBLE_MIN ? prefer : other
}
