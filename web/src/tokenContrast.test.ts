/**
 * token 层的对比度门禁（2026-09-14 apple-design 审计 S2 / S10 / S8）。
 *
 * `e2e/contrast.ts` 量的是真实页面上的文字；这里量的是 `index.css` 里的**不透明 token 配对**，
 * 便宜、离线、改 token 当场红。它不是整页合规证明（透明叠加、渐变、真实状态叠加不在内），
 * 只保证下面这些「边界就是全部识别信息」的配对不会被一次顺手调色悄悄调回去：
 *
 *   - 焦点环 accent 对每种底色 ≥3:1（此前 45% 透明版只有 1.9:1）
 *   - 复选框 / 单选未选边框、开关关态轨道 border-control 对白 / 纸 ≥3:1（此前 border-strong 1.57:1）
 *   - 可编辑框边框 border-input 对白 / 纸 / surface-2 ≥3:1（S8 甲，用户拍板 A 档）
 *   - ink-2 / ink-3 作为要读的字，对白 / 纸 / surface-2 ≥4.5:1（ink-3 在画布灰上本来就不达标，
 *     index.css 的注释是权威，不在这里判）
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
function luminance(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
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
  it('自检：公式对得上 WCAG 的黑白 21:1', () => {
    expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 1)
  })

  it('焦点环（accent，不透明）对白 / 纸 / surface-2 / selected / 画布灰 ≥3:1', () => {
    for (const g of [...GROUNDS, 'selected', 'canvas']) {
      expect(contrast(token('accent'), token(g)), g).toBeGreaterThanOrEqual(3)
    }
  })

  it('focus-ring 用的是不透明 accent，不是 color-mix 出来的透明版', () => {
    const ring = CSS.match(/@utility focus-ring \{([\s\S]*?)\}/)?.[1] ?? ''
    expect(ring).toContain('var(--color-accent)')
    expect(ring).not.toContain('color-mix')
  })

  it('控件边界（border-control / border-input）对白 / 纸 / surface-2 ≥3:1', () => {
    for (const name of ['border-control', 'border-input']) {
      for (const g of GROUNDS) {
        expect(contrast(token(name), token(g)), `${name} on ${g}`).toBeGreaterThanOrEqual(3)
      }
    }
  })

  it('要读的字（ink / ink-2 / ink-3）对白 / 纸 / surface-2 ≥4.5:1', () => {
    for (const name of ['ink', 'ink-2', 'ink-3']) {
      for (const g of GROUNDS) {
        expect(contrast(token(name), token(g)), `${name} on ${g}`).toBeGreaterThanOrEqual(4.5)
      }
    }
  })

  it('语义色的字（danger / warn / ok）落在各自的 subtle 底上 ≥4.5:1', () => {
    for (const name of ['danger', 'warn', 'ok']) {
      expect(contrast(token(name), token(`${name}-subtle`)), name).toBeGreaterThanOrEqual(4.5)
    }
  })
})
