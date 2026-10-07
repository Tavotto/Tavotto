/**
 * token 配对的对比度门禁（2026-09-14 审计 S2 / S10；2026-09-15 打磨批次 A 改成半透明面之后重写）。
 *
 * 面（边框 / hover / selected）从 2026-09-15 起是 ink 的半透明叠加（color-mix），落到什么底上
 * 就是什么颜色——所以这里不再读它们的 hex，而是按 sRGB 线性混合把它们**合成到每个底色上**
 * 再量。判据的主语是「合成后的那个颜色」，不是 token 字面。
 *
 * 两条有意为之的「不达标」写在明处，别再回去「修」它们：
 *   - 可编辑框静态没有边：是一块比面板深一级的底（field 对白 ≈1.14:1，2026-09-15 参考 Codex 设置页的输入框）——
 *     OpenAI apps-sdk-ui 静态 alpha-16、Claude 产品壳 10%、Codex「底 +1 级灰」都不到 3:1；
 *     3:1 由聚焦态（不透明 accent 边）承担。
 *   - selected 10% 在纸底上 ≈1.2:1——它只是「轻 tint」，选中态还要靠字重 / 对勾再说一遍（宪法第一节）。
 *
 * **两套主题各量一遍**（2026-10-07 暗色主题，宪法第二十八节）：值表有两张——浅色是 `@theme` 里的值，暗色是
 * 文件末尾 `:root[data-theme='dark']` 那一段（媒体查询那一段与它逐字相同，这里也守着）。暗色表只写变了的那几项，
 * 没写的（公式类：hover / selected / -surface…，以及「纸」）从浅色表继承、再按暗色的值重算——与浏览器在
 * `:root` 上算 `var()` 的方式一致。下面每一条配对断言都对两套主题各跑一次，主语是「这一套主题里合成后的颜色」。
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(path.resolve(HERE, 'index.css'), 'utf8')

type Theme = 'light' | 'dark'
const THEMES: readonly Theme[] = ['light', 'dark']

/** `--name: value;` 一段里的全部变量（注释先剥掉；值可跨行） */
function cssVars(block: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const m of block.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) {
    out[m[1]] = m[2].replace(/\s+/g, ' ').trim()
  }
  return out
}
const THEME_BLOCK = CSS.match(/@theme \{([\s\S]*?)\n\}/)?.[1] ?? ''
/** `:root` 里的非 @theme 变量（--color-scrim 这类）也算浅色表 */
const ROOT_BLOCK = CSS.match(/\n:root \{([\s\S]*?)\n\}/)?.[1] ?? ''
const DARK_BLOCK = CSS.match(/\n:root\[data-theme='dark'\] \{([\s\S]*?)\n\}/)?.[1] ?? ''
const DARK_MEDIA_BLOCK =
  CSS.match(
    /@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme='light'\]\) \{([\s\S]*?)\n {2}\}\s*\}/,
  )?.[1] ?? ''
const LIGHT_VARS = { ...cssVars(ROOT_BLOCK), ...cssVars(THEME_BLOCK) }
const DARK_VARS = cssVars(DARK_BLOCK)
const VARS: Record<Theme, Record<string, string>> = {
  light: LIGHT_VARS,
  dark: { ...LIGHT_VARS, ...DARK_VARS },
}

function tokenIn(theme: Theme, name: string): string {
  const v = VARS[theme][`color-${name}`]
  if (!v || !/^#[0-9a-f]{6}$/i.test(v)) throw new Error(`${theme} 表里没有不透明的 --color-${name}（${v}）`)
  return v.toLowerCase()
}
/** `color-mix(in srgb, var(--color-ink) N%, transparent)` 里的 N（公式两套主题共用；墨跟着主题换） */
function alphaOfInkIn(theme: Theme, name: string): number {
  const m = (VARS[theme][`color-${name}`] ?? '').match(
    /^color-mix\(in srgb, var\(--color-ink\) ([\d.]+)%, transparent\)$/,
  )
  if (!m) throw new Error(`${theme} 表里没有 ink 半透明叠加的 --color-${name}`)
  return Number(m[1]) / 100
}
const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
const hex = (c: number[]) => '#' + c.map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')
/** ink 以 alpha 叠在 ground 上（sRGB 直接混合，与浏览器 color-mix in srgb 一致） */
export function over(alpha: number, ground: string, theme: Theme = 'light'): string {
  const g = rgb(ground)
  const i = rgb(tokenIn(theme, 'ink'))
  return hex(g.map((gv, k) => gv * (1 - alpha) + i[k] * alpha))
}
function luminance(h: string): number {
  const c = rgb(h).map((v) => v / 255)
  const f = (v: number) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
  const [r, g, b] = c.map(f)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
export function contrast(a: string, b: string): number {
  const la = luminance(a)
  const lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

/* ---- 状态色锚点派生（2026-10-07 设计审计 §1）：`color-mix(in oklab, anchor N%, base)` 在这里按 CSS Color 4
   的 oklab 插值重算，量的是**合成后的颜色**（与 ink 叠加那一族同一个主语），不是 token 字面。 ---- */
const toLin = (v: number) => {
  const c = v / 255
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}
const fromLin = (v: number) => {
  const c = v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055
  return Math.max(0, Math.min(255, c * 255))
}
function toOklab(h: string): number[] {
  const [r, g, b] = rgb(h).map(toLin)
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ]
}
function fromOklab([L, A, B]: number[]): string {
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3
  const s = (L - 0.0894841775 * A - 1.291485548 * B) ** 3
  return hex(
    [
      4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
      -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
      -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
    ].map(fromLin),
  )
}
/** 解析 `--color-<name>`：hex 直接回；`var(--color-x)` 跟过去；`color-mix(in oklab, A N%, B)` 按 oklab 混 */
export function resolveColor(name: string, theme: Theme = 'light'): string {
  const v = VARS[theme][`color-${name}`]
  if (!v) throw new Error(`${theme} 表里没有 --color-${name}`)
  const ref = (x: string): string => {
    x = x.trim()
    if (x === 'black') return '#000000'
    if (x === 'white') return '#ffffff'
    if (/^#[0-9a-f]{6}$/i.test(x)) return x.toLowerCase()
    const r = x.match(/^var\(--color-([a-z0-9-]+)\)$/)
    if (r) return resolveColor(r[1], theme)
    throw new Error(`解析不了的颜色：${x}`)
  }
  const mix = v.match(/^color-mix\(in oklab, (.+?) ([\d.]+)%, (.+)\)$/)
  if (mix) {
    const p = Number(mix[2]) / 100
    const a = toOklab(ref(mix[1]))
    const b = toOklab(ref(mix[3]))
    return fromOklab(a.map((x, i) => x * p + b[i] * (1 - p)))
  }
  return ref(v)
}

const GROUNDS = ['surface', 'bg', 'surface-2'] as const

for (const theme of THEMES) {
  describe(`token 配对的对比度（${theme}）`, () => {
    // 这一组里的 token / 叠加 / 派生全按这一套主题的值表算（暗色表没写的项从浅色继承、按暗色的值重算）
    const token = (name: string) => tokenIn(theme, name)
    const alphaOfInk = (name: string) => alphaOfInkIn(theme, name)
    const rc = (name: string) => resolveColor(name, theme)
    const ov = (alpha: number, ground: string) => over(alpha, ground, theme)

    it('自检：公式对得上 WCAG 的黑白 21:1；叠加 0% 等于底色、100% 等于 ink', () => {
      expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 1)
      expect(ov(0, '#ffffff')).toBe('#ffffff')
      expect(ov(1, '#ffffff')).toBe(token('ink'))
    })

    it('焦点环（accent，不透明）对白 / 纸 / surface-2 / 画布灰 / 合成后的 selected ≥3:1', () => {
      for (const g of [...GROUNDS, 'canvas']) {
        expect(contrast(token('accent'), token(g)), g).toBeGreaterThanOrEqual(3)
      }
      for (const g of GROUNDS) {
        const sel = ov(alphaOfInk('selected'), token(g))
        expect(contrast(token('accent'), sel), `selected on ${g} = ${sel}`).toBeGreaterThanOrEqual(3)
      }
    })

    it('画布上唯一的一种彩色线（sel：选框 / 参考线 / 元素框）对纸白 / 画布灰 ≥3:1（2026-09-15 打磨 · 画布 C1 / C2）', () => {
      for (const g of ['surface', 'canvas']) {
        expect(contrast(token('sel'), token(g)), g).toBeGreaterThanOrEqual(3)
      }
    })

    it('focus-ring 用的是不透明 accent，不是 color-mix 出来的透明版', () => {
      const ring = CSS.match(/@utility focus-ring \{([\s\S]*?)\}/)?.[1] ?? ''
      expect(ring).toContain('var(--color-accent)')
      expect(ring).not.toContain('color-mix')
    })

    it('控件边界（border-control：复选框 / 单选 / 关态开关）对白 / 纸 / surface-2 ≥3:1', () => {
      for (const g of GROUNDS) {
        expect(contrast(token('border-control'), token(g)), `border-control on ${g}`).toBeGreaterThanOrEqual(3)
      }
    })

    it('可编辑框的底：field 对白是看得见的一级台阶（≥1.1:1，有意不到 3:1，理由见文件头），hover 比静态深，聚焦边 accent 对 field / 白 ≥3:1', () => {
      const field = token('field')
      const hover = token('field-hover')
      expect(contrast(field, token('surface'))).toBeGreaterThanOrEqual(1.1)
      expect(contrast(hover, token('surface'))).toBeGreaterThan(contrast(field, token('surface')))
      for (const g of [field, hover, token('surface')]) {
        expect(contrast(token('accent'), g), `accent edge on ${g}`).toBeGreaterThanOrEqual(3)
      }
    })

    it('field 底上要读的字（值 ink、占位 / 单位 / 前缀 ink-3）≥4.5:1；hover 底上也是', () => {
      for (const name of ['ink', 'ink-2', 'ink-3']) {
        for (const g of ['field', 'field-hover']) {
          expect(contrast(token(name), token(g)), `${name} on ${g}`).toBeGreaterThanOrEqual(4.5)
        }
      }
    })

    it('墨阶拉开（2026-10-07 设计审计 §1.1）：ink-2 与 ink-3 在白上至少差 1.5:1 的对比度，不再几乎同色', () => {
      expect(
        contrast(token('ink-2'), token('surface')) - contrast(token('ink-3'), token('surface')),
      ).toBeGreaterThanOrEqual(1.5)
    })

    it('交互面三档：hover < active < selected，都是 ink 的半透明叠加，selected 是 hover 的两倍', () => {
      const h = alphaOfInk('surface-hover')
      const a = alphaOfInk('surface-active')
      const s = alphaOfInk('selected')
      expect(h).toBeLessThan(a)
      expect(a).toBeLessThan(s)
      expect(s).toBeCloseTo(h * 2, 2)
    })

    it('要读的字（ink / ink-2 / ink-3）对白 / 桌面 / surface-2 / 画布灰 ≥4.5:1；ink 与 ink-2 在合成后的 selected 上也 ≥4.5:1', () => {
      for (const name of ['ink', 'ink-2', 'ink-3']) {
        for (const g of [...GROUNDS, 'canvas']) {
          expect(contrast(token(name), token(g)), `${name} on ${g}`).toBeGreaterThanOrEqual(4.5)
        }
      }
      for (const name of ['ink', 'ink-2']) {
        for (const g of GROUNDS) {
          const sel = ov(alphaOfInk('selected'), token(g))
          expect(contrast(token(name), sel), `${name} on selected(${g})`).toBeGreaterThanOrEqual(4.5)
        }
      }
    })

    it('自检：oklab 混色在两端等于原色，派生链跟得过 var() 与别名', () => {
      expect(fromOklab(toOklab('#c4442a'))).toBe('#c4442a')
      expect(rc('info')).toBe(token('accent'))
      expect(rc('danger-subtle')).toBe(rc('danger-surface'))
    })

    it('状态色锚点派生（danger / warn / ok / info）：-content 落在自己的 -surface 底上、白上 ≥4.5:1；锚点（图标 / 圆点）对白 / 桌面 / 自己的底 ≥3:1；-border 比 -surface 深', () => {
      for (const s of ['danger', 'warn', 'ok', 'info']) {
        const anchor = rc(s)
        const surface = rc(`${s}-surface`)
        const border = rc(`${s}-border`)
        const content = rc(`${s}-content`)
        expect(contrast(content, surface), `${s}-content on ${s}-surface`).toBeGreaterThanOrEqual(4.5)
        expect(contrast(content, token('surface')), `${s}-content on surface`).toBeGreaterThanOrEqual(4.5)
        for (const g of [token('surface'), token('bg'), surface]) {
          expect(contrast(anchor, g), `${s} anchor on ${g}`).toBeGreaterThanOrEqual(3)
        }
        expect(contrast(border, token('surface')), `${s}-border`).toBeGreaterThan(contrast(surface, token('surface')))
      }
    })

    it('代码着色七档（syntax-*）在白 / 桌面 / surface-2 上都是要读的字 ≥4.5:1', () => {
      for (const k of ['keyword', 'function', 'string', 'number', 'comment', 'type', 'builtin']) {
        for (const g of GROUNDS) {
          expect(contrast(token(`syntax-${k}`), token(g)), `syntax-${k} on ${g}`).toBeGreaterThanOrEqual(4.5)
        }
      }
    })

    it('当字用的锚点（text-danger / text-ok：红字 ghost 按钮、行内失败字）对白 ≥4.5:1；warn 锚点不当字（字一律 warn-content）', () => {
      for (const s of ['danger', 'ok']) {
        expect(contrast(rc(s), token('surface')), s).toBeGreaterThanOrEqual(4.5)
      }
    })

    it('对话框页脚的危险浅底胶囊：danger-content 在 hover 底上也 ≥4.5:1', () => {
      expect(contrast(rc('danger-content'), rc('danger-surface-hover'))).toBeGreaterThanOrEqual(4.5)
    })

    it('Tooltip 是 ink 底白字：surface 对 ink ≥4.5:1', () => {
      expect(contrast(token('surface'), token('ink'))).toBeGreaterThanOrEqual(4.5)
    })
  })
}

/** 任意颜色以 alpha 叠在 ground 上（sRGB，与 `color-mix(in srgb, X N%, transparent)` 落在不透明底上一致） */
const mixOver = (color: string, alpha: number, ground: string) =>
  hex(rgb(ground).map((g, k) => g * (1 - alpha) + rgb(color)[k] * alpha))
/** `color-mix(in srgb, var(--color-<x>) N%, transparent)` → [x, N/100] */
function srgbAlphaOf(theme: Theme, name: string): [string, number] {
  const m = (VARS[theme][`color-${name}`] ?? '').match(
    /^color-mix\(in srgb, var\(--color-([a-z0-9-]+)\) ([\d.]+)%, transparent\)$/,
  )
  if (!m) throw new Error(`${theme} 表里 --color-${name} 不是 srgb 半透明叠加`)
  return [m[1], Number(m[2]) / 100]
}
/** 两套主题都不变的不透明 token：纸与纸上的墨是文档内容；sel 是画在纸上的那一种彩色线（对纸白与两种画布灰都 ≥3） */
const THEME_INVARIANT = ['paper', 'paper-ink', 'sel']

describe('暗色主题的值表（宪法第二十八节）', () => {
  it('两段生效条件写的是同一张表：媒体查询（没选浅色）那一段与 data-theme="dark" 那一段逐字相同', () => {
    expect(Object.keys(DARK_VARS).length).toBeGreaterThan(20)
    expect(cssVars(DARK_MEDIA_BLOCK)).toEqual(DARK_VARS)
    // 两段都把原生控件切成暗色
    expect(DARK_MEDIA_BLOCK).toMatch(/color-scheme: dark;/)
    expect(DARK_BLOCK).toMatch(/color-scheme: dark;/)
  })

  it('闭集：浅色表里每个不透明颜色，暗色表要么重写、要么在「两套同值」名单里（纸 / 纸上的墨 / sel）', () => {
    const opaque = Object.entries(LIGHT_VARS)
      .filter(([k, v]) => k.startsWith('color-') && /^#[0-9a-f]{6}$/i.test(v))
      .map(([k]) => k.slice('color-'.length))
    const missing = opaque.filter((k) => !(`color-${k}` in DARK_VARS) && !THEME_INVARIANT.includes(k))
    expect(missing, '暗色表漏了的颜色').toEqual([])
  })

  it('纸不跟主题走：--color-paper / --color-paper-ink 不在暗色表里，两套主题解析出同一个值', () => {
    for (const k of ['paper', 'paper-ink']) {
      expect(DARK_VARS[`color-${k}`], k).toBeUndefined()
      expect(tokenIn('dark', k)).toBe(tokenIn('light', k))
    }
    expect(tokenIn('light', 'paper')).toBe('#ffffff')
  })
})

for (const theme of THEMES) {
  describe(`两套主题都要过的配对（${theme}）`, () => {
    const t = (name: string) => tokenIn(theme, name)
    const rc = (name: string) => resolveColor(name, theme)

    it('纸上的线与字：sel 对纸 ≥3:1；纸上的墨对纸 ≥4.5:1（网格、占位框的字都画在纸上）', () => {
      expect(contrast(t('sel'), t('paper'))).toBeGreaterThanOrEqual(3)
      expect(contrast(t('paper-ink'), t('paper'))).toBeGreaterThanOrEqual(4.5)
      // 纸上的二 / 三级墨（缩略图里的文字与标注线）：从 paper-ink 派生、合成到纸白上 ≥4.5:1——不是界面的 ink-2 / ink-3
      // （那两档在暗色里变浅，落在不变的白纸上只剩 1.8 / 2.8）
      for (const name of ['paper-ink-2', 'paper-ink-3']) {
        const [base, alpha] = srgbAlphaOf(theme, name)
        expect(base, name).toBe('paper-ink')
        expect(contrast(mixOver(t(base), alpha, t('paper')), t('paper')), `${name} on paper`).toBeGreaterThanOrEqual(4.5)
      }
    })

    it('浮起的 thumb（分段 / 选项格 / 开关钮）上的字 ink / ink-2 ≥4.5:1；暗色里 thumb 比面板亮（浮起靠更亮）', () => {
      for (const name of ['ink', 'ink-2']) {
        expect(contrast(t(name), rc('thumb')), `${name} on thumb`).toBeGreaterThanOrEqual(4.5)
      }
      if (theme === 'dark') expect(contrast(rc('thumb'), t('surface'))).toBeGreaterThan(1.2)
    })

    it('文字选区（::selection）合成到白 / 桌面 / surface-2 上，ink 与 ink-2 照样 ≥4.5:1', () => {
      const [base, alpha] = srgbAlphaOf(theme, 'text-selection')
      for (const g of ['surface', 'bg', 'surface-2']) {
        const sel = mixOver(rc(base), alpha, t(g))
        for (const name of ['ink', 'ink-2']) {
          expect(contrast(t(name), sel), `${name} on selection(${g})`).toBeGreaterThanOrEqual(4.5)
        }
      }
    })

    it('遮罩与投影从 --color-shadow 派生，shadow 比桌面暗（暗色里遮罩是压暗、不是提亮）', () => {
      expect(srgbAlphaOf(theme, 'scrim')[0]).toBe('shadow')
      expect(contrast(t('shadow'), '#000000')).toBeLessThan(contrast(t('bg'), '#000000'))
    })

    it('坐在纸上的界面（空画布提示）：浅色里底透明、字直接落在纸上 ≥4.5:1；暗色里有自己的面板底，字落在它上面 ≥4.5:1', () => {
      const v = VARS[theme]['color-paper-chrome']
      if (theme === 'light') {
        expect(v).toBe('transparent')
        for (const name of ['ink', 'ink-2', 'ink-3']) {
          expect(contrast(t(name), t('paper')), `${name} on paper`).toBeGreaterThanOrEqual(4.5)
        }
      } else {
        const ground = rc('paper-chrome')
        for (const name of ['ink', 'ink-2', 'ink-3']) {
          expect(contrast(t(name), ground), `${name} on paper-chrome`).toBeGreaterThanOrEqual(4.5)
        }
      }
    })

    it('代码注释与 ink-3 同值（注释就是三级正文）', () => {
      expect(t('syntax-comment')).toBe(t('ink-3'))
    })
  })
}
