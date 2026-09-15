/**
 * token 配对的对比度门禁（2026-09-14 审计 S2 / S10；2026-09-15 打磨批次 A 改成半透明面之后重写）。
 *
 * 面（边框 / hover / selected）从 2026-09-15 起是 ink 的半透明叠加（color-mix），落到什么底上
 * 就是什么颜色——所以这里不再读它们的 hex，而是按 sRGB 线性混合把它们**合成到每个底色上**
 * 再量。判据的主语是「合成后的那个颜色」，不是 token 字面。
 *
 * 两条有意为之的「不达标」写在明处，别再回去「修」它们：
 *   - 可编辑框的静态边 16%（对白 ≈1.4:1）、hover 25%（≈1.9:1）——OpenAI apps-sdk-ui 静态 alpha-16、
 *     Claude 产品壳 10%，都不到 3:1；3:1 由聚焦环（不透明 accent）承担。
 *   - selected 10% 在纸底上 ≈1.2:1——它只是「轻 tint」，选中态还要靠字重 / 对勾再说一遍（宪法第一节）。
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(path.resolve(HERE, 'index.css'), 'utf8')

function token(name: string): string {
  const m = CSS.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`))
  if (!m) throw new Error(`index.css 里没有不透明的 --color-${name}`)
  return m[1].toLowerCase()
}
/** `color-mix(in srgb, var(--color-ink) N%, transparent)` 里的 N */
function alphaOfInk(name: string): number {
  const m = CSS.match(
    new RegExp(`--color-${name}:\\s*color-mix\\(in srgb, var\\(--color-ink\\) ([\\d.]+)%, transparent\\)\\s*;`),
  )
  if (!m) throw new Error(`index.css 里没有 ink 半透明叠加的 --color-${name}`)
  return Number(m[1]) / 100
}
const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
const hex = (c: number[]) => '#' + c.map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')
/** ink 以 alpha 叠在 ground 上（sRGB 直接混合，与浏览器 color-mix in srgb 一致） */
export function over(alpha: number, ground: string): string {
  const g = rgb(ground)
  const i = rgb(token('ink'))
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

const GROUNDS = ['surface', 'bg', 'surface-2'] as const

describe('token 配对的对比度', () => {
  it('自检：公式对得上 WCAG 的黑白 21:1；叠加 0% 等于底色、100% 等于 ink', () => {
    expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 1)
    expect(over(0, '#ffffff')).toBe('#ffffff')
    expect(over(1, '#ffffff')).toBe(token('ink'))
  })

  it('焦点环（accent，不透明）对白 / 纸 / surface-2 / 画布灰 / 合成后的 selected ≥3:1', () => {
    for (const g of [...GROUNDS, 'canvas']) {
      expect(contrast(token('accent'), token(g)), g).toBeGreaterThanOrEqual(3)
    }
    for (const g of GROUNDS) {
      const sel = over(alphaOfInk('selected'), token(g))
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

  it('可编辑框的边是 ink 的半透明叠加：静态 16%、hover 25%，hover 比静态深（有意不到 3:1，理由见文件头）', () => {
    const rest = alphaOfInk('border-input')
    const hover = alphaOfInk('border-input-hover')
    expect(rest).toBe(0.16)
    expect(hover).toBeGreaterThan(rest)
    for (const g of GROUNDS) {
      expect(contrast(over(rest, token(g)), token(g)), `静态边 on ${g}`).toBeGreaterThanOrEqual(1.35)
      expect(contrast(over(hover, token(g)), token(g)), `hover 边 on ${g}`).toBeGreaterThan(
        contrast(over(rest, token(g)), token(g)),
      )
    }
  })

  it('交互面三档：hover < active < selected，都是 ink 的半透明叠加，selected 是 hover 的两倍', () => {
    const h = alphaOfInk('surface-hover')
    const a = alphaOfInk('surface-active')
    const s = alphaOfInk('selected')
    expect(h).toBeLessThan(a)
    expect(a).toBeLessThan(s)
    expect(s).toBeCloseTo(h * 2, 2)
  })

  it('要读的字（ink / ink-2 / ink-3）对白 / 纸 / surface-2 ≥4.5:1；ink 与 ink-2 在合成后的 selected 上也 ≥4.5:1', () => {
    for (const name of ['ink', 'ink-2', 'ink-3']) {
      for (const g of GROUNDS) {
        expect(contrast(token(name), token(g)), `${name} on ${g}`).toBeGreaterThanOrEqual(4.5)
      }
    }
    for (const name of ['ink', 'ink-2']) {
      for (const g of GROUNDS) {
        const sel = over(alphaOfInk('selected'), token(g))
        expect(contrast(token(name), sel), `${name} on selected(${g})`).toBeGreaterThanOrEqual(4.5)
      }
    }
  })

  it('语义色的字（danger / warn / ok）落在各自的 subtle 底上 ≥4.5:1', () => {
    for (const name of ['danger', 'warn', 'ok']) {
      expect(contrast(token(name), token(`${name}-subtle`)), name).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('Tooltip 是 ink 底白字：surface 对 ink ≥4.5:1', () => {
    expect(contrast(token('surface'), token('ink'))).toBeGreaterThanOrEqual(4.5)
  })
})
