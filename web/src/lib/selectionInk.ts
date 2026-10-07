/**
 * 画布上改字时，选中的字换成哪一种纸上的墨（Codex P2，PR #834）。
 *
 * 全局 `::selection` 把选中的字换成界面的 ink（选区底只是 accent 28% 的半透明 tint，原色的浅字落在上面不够格）。
 * 画布上的字坐在**文档的底**上，不在界面上：暗色主题里界面 ink 是浅灰，落在白纸上看不见，所以 TextView 曾一律换成
 * paper-ink——可页面底色（CanvasPage）与文字框底色（`obj.bg`）都是用户定的，深底上换成深色的 paper-ink 又成了深字压深底。
 *
 * 所以墨由「这段字实际坐在什么上」决定：文字框自己的底 → 页面底色（透明页面画的是浅棋盘格，按纸算）→ 纸。
 * 半透明的底按 sRGB 叠到下一层上（与浏览器合成一致）；认不出的颜色串当作没有这一层。
 * 判据：合成后的底上，纸色（`paper`）与纸上的墨（`paper-ink`）哪个 WCAG 对比度大就用哪个——深底用纸色，浅底用纸上的墨。
 * 两个都是两套主题同值的 token；合成上选区 tint 之后的对比度由 `tokenContrast.test` 用真实 token 值量。
 *
 * 盲点（写在明处）：字下面压着的图 / 图形不在这条链里（只认文档里写明的底色）；字被拖到页面外、落在画布灰上时也按页面算。
 */

/** 选中的字用的 token：`selection:text-<这个>` */
export type SelectionInk = 'paper' | 'paper-ink'

/** 纸（`--color-paper`，两套主题同值；`tokenContrast.test` 守着它与 token 同值）——链的最底层 */
export const PAPER_RGB: readonly [number, number, number] = [255, 255, 255]
/** 纸上的墨（`--color-paper-ink`，两套主题同值；同上，`tokenContrast.test` 守着同值）——深 / 浅换手点按它算，不按纯黑 */
export const PAPER_INK_RGB: readonly [number, number, number] = [0x1b, 0x1b, 0x18]

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

/**
 * 这段字选中时换哪一种墨。`page.transparent` 时页面画的是浅棋盘格（`--color-paper-checker`），按纸算。
 */
export function selectionInkFor(
  textBg: string | null | undefined,
  page: { bg?: string; transparent?: boolean },
): SelectionInk {
  const L = luminance(effectiveGround([page.transparent ? null : page.bg, textBg]))
  const vsPaper = (luminance(PAPER_RGB) + 0.05) / (L + 0.05)
  const vsInk = (L + 0.05) / (luminance(PAPER_INK_RGB) + 0.05)
  return vsPaper > vsInk ? 'paper' : 'paper-ink'
}
