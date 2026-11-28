/**
 * 图层树对象图标的配色（纯函数，不碰 DOM）：把对象自己的颜色换算成「放在行首 14px 小图标上」安全的颜色。
 *
 * 规则（浅 / 深两套主题同一条）：
 * 1. **有填充的实心图形**：填充保持对象原色（白就是白、黑就是黑、橙还是橙），再叠一圈**同色系的细外框**——填充往主题的墨色
 *    混一截（浅色往深墨、深色往浅墨）。所有实心图形都有这一圈，不分形状、不分主题，不出现「有的有框、有的裸边」；
 *    框与描边类图标同细度（16px 下 1px）。不靠压暗填充保边缘，亮色不发脏。
 * 2. **只有描边 / 线型 / 文字**：用对象的描边（文字）色，但对比度夹在 [2.6, 6.5]（对该主题的面色）：
 *    太接近底色（深色下的黑字、浅色下的白字）→ 回落到暖中性灰；太重（浅色下的纯黑）→ 往暖中性灰靠到上限。
 *    有色相的颜色只调明度、保留色相与饱和度。结果始终低于正文、明显高于隐藏态。
 * 3. **隐藏的对象**：除了行本身的降透明度，颜色再去饱和 45%。
 * 解析不了的颜色（非 `#rgb` / `#rrggbb`）= 当作没有颜色，用中性灰。
 */
export type IconTheme = 'light' | 'dark'

/** 图层树面色（`index.css` 的 `--color-surface`） */
const SURFACE: Record<IconTheme, string> = { light: '#ffffff', dark: '#23221f' }
/** 暖中性灰（对面色的对比度约 5:1 / 6:1） */
export const ICON_NEUTRAL: Record<IconTheme, string> = { light: '#6b6860', dark: '#a8a59b' }
/** 外框往哪个墨色混、混多少 */
const RIM: Record<IconTheme, { ink: string; t: number }> = {
  light: { ink: '#1c1b18', t: 0.38 },
  dark: { ink: '#efeee9', t: 0.3 },
}
const LINE_MIN = 2.6
const LINE_MAX = 6.5
const HIDDEN_SATURATION = 0.55

type RGB = [number, number, number]

function parse(hex: string | null | undefined): RGB | null {
  if (!hex) return null
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})([0-9a-f]{2})?$/i.exec(hex.trim())
  if (!m) return null
  let h = m[1]
  if (h.length === 3) h = h.replace(/./g, (c) => c + c)
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB
}

const toHex = (c: RGB) => '#' + c.map((v) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, '0')).join('')

function luminance([r, g, b]: RGB): number {
  const f = (v: number) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}

/** WCAG 对比度 */
export function contrastOf(a: string, b: string): number {
  const [x, y] = [parse(a), parse(b)]
  if (!x || !y) return 1
  const [hi, lo] = [Math.max(luminance(x), luminance(y)), Math.min(luminance(x), luminance(y))]
  return (hi + 0.05) / (lo + 0.05)
}

const mix = (a: RGB, b: RGB, t: number): RGB => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]

function toHsl([r, g, b]: RGB): [number, number, number] {
  const [R, G, B] = [r / 255, g / 255, b / 255]
  const max = Math.max(R, G, B)
  const min = Math.min(R, G, B)
  const l = (max + min) / 2
  const d = max - min
  if (!d) return [0, 0, l]
  const s = d / (1 - Math.abs(2 * l - 1))
  const h = max === R ? ((G - B) / d) % 6 : max === G ? (B - R) / d + 2 : (R - G) / d + 4
  return [(h * 60 + 360) % 360, s, l]
}

function fromHsl([h, s, l]: [number, number, number]): RGB {
  const c = (1 - Math.abs(2 * l - 1)) * s
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
  const m = l - c / 2
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x]
  return [(r + m) * 255, (g + m) * 255, (b + m) * 255]
}

const desaturate = (c: RGB): RGB => {
  const [h, s, l] = toHsl(c)
  return fromHsl([h, s * HIDDEN_SATURATION, l])
}

/** 线型 / 文字色：对比度夹进 [LINE_MIN, LINE_MAX] */
function fitLine(c: RGB, theme: IconTheme): RGB {
  const bg = parse(SURFACE[theme])!
  const neutral = parse(ICON_NEUTRAL[theme])!
  const cr = (x: RGB) => (Math.max(luminance(x), luminance(bg)) + 0.05) / (Math.min(luminance(x), luminance(bg)) + 0.05)
  const now = cr(c)
  if (now >= LINE_MIN && now <= LINE_MAX) return c
  const [h, s, l] = toHsl(c)
  if (s < 0.12) {
    if (now < LINE_MIN) return neutral
    // 太重：往中性灰靠，直到不超过上限
    let lo = 0
    let hi = 1
    for (let i = 0; i < 12; i++) {
      const mid = (lo + hi) / 2
      if (cr(mix(c, neutral, mid)) > LINE_MAX) lo = mid
      else hi = mid
    }
    return mix(c, neutral, hi)
  }
  // 有色相：只动明度。太淡 / 太亮的往「离底色更远」的方向走，太重的往回走
  const away = luminance(bg) > 0.5 ? -1 : 1
  const dir = now < LINE_MIN ? away : -away
  let lo = 0
  let hi = 1 // 沿 dir 走的比例
  const at = (t: number) => fromHsl([h, s, Math.min(1, Math.max(0, l + dir * t * (dir > 0 ? 1 - l : l)))])
  const ok = (t: number) => (now < LINE_MIN ? cr(at(t)) >= LINE_MIN : cr(at(t)) <= LINE_MAX)
  for (let i = 0; i < 14; i++) {
    const mid = (lo + hi) / 2
    if (ok(mid)) hi = mid
    else lo = mid
  }
  return at(hi)
}

export interface IconPaint {
  /** 描边色（实心图形 = 同色系的细外框） */
  stroke: string
  /** 填充色（保持对象原色）；没有填充 = null */
  fill: string | null
}

/** 解析不了的颜色（`currentColor`、`rgb()`……）：没有可用的颜色，调用方用默认的中性灰线条图标 */
export function layerIconPaint(
  src: { color?: string | null; fill?: string | null },
  theme: IconTheme,
  hidden = false,
): IconPaint | null {
  const fillRgb = parse(src.fill)
  const colorRgb = parse(src.color)
  if (!fillRgb && !colorRgb) return null
  const out = (c: RGB) => toHex(hidden ? desaturate(c) : c)
  if (fillRgb) {
    const { ink, t } = RIM[theme]
    return { fill: out(fillRgb), stroke: out(mix(fillRgb, parse(ink)!, t)) }
  }
  return { fill: null, stroke: out(fitLine(colorRgb!, theme)) }
}
