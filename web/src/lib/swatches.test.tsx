/**
 * 取色面板的两组推荐色（ADR 0185 M3 第三批）：推荐配色（期刊目录里没有配色规定 → Okabe-Ito，说明里点名期刊）、
 * 图内配色（引擎报的颜色属性与渐变两端，按次数、去灰阶、截前 12 个），以及它们在面板里的样子。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import journals from '@journals'
import { ColorField } from '@/components/ui/Input'
import { ColorFieldContext } from '@/components/ui/colorPalette'
import type { Manifest } from './api'
import { figureColorsOf, OKABE_ITO, recommendedSwatches } from './swatches'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const manifest = (fields: { type: string; value: unknown }[][]): Manifest =>
  ({ elements: fields.map((editable, i) => ({ gid: `g${i}`, editable: editable.map((f, k) => ({ prop: `p${k}`, ...f })) })) }) as unknown as Manifest

describe('推荐配色', () => {
  it('期刊目录里没有任何期刊有配色规定（只有色彩模式）：所以一律给 Okabe-Ito，不编期刊配色', () => {
    const fields = new Set<string>()
    for (const j of Object.values((journals as { journals: Record<string, { rules?: Record<string, unknown> }> }).journals))
      for (const k of Object.keys(j.rules ?? {})) fields.add(k)
    expect([...fields].filter((k) => /palette|colou?rs?$|swatch/i.test(k))).toEqual([])
    expect(recommendedSwatches('Nature')).toEqual({ source: 'general', colors: OKABE_ITO, journal: 'Nature' })
    expect(recommendedSwatches(undefined).journal).toBeNull()
    expect(OKABE_ITO).toHaveLength(8)
  })
})

describe('图内配色', () => {
  it('按出现次数排、去灰阶（黑白灰是轴与文字）、渐变两端也算、截前 max 个', () => {
    const m = manifest([
      [{ type: 'color', value: '#9467bd' }],
      [{ type: 'color', value: '#1f77b4' }, { type: 'color', value: '#000000' }],
      [{ type: 'color', value: '#FF7F0E' }, { type: 'color', value: '#1F77B4' }],
      [{ type: 'gradient', value: { stops: ['#2ca02c', '#ffffff'] } }, { type: 'text', value: '#d62728' }],
      [{ type: 'color', value: 'none' }, { type: 'color', value: '#888' }],
    ])
    // 紫色最先出现但只出现一次：排在出现两次的蓝色后面
    expect(figureColorsOf([m, null])).toEqual(['#1f77b4', '#9467bd', '#ff7f0e', '#2ca02c'])
    expect(figureColorsOf([m], 1)).toEqual(['#1f77b4'])
  })
})

describe('取色面板里的两组', () => {
  let root: Root
  let host: HTMLDivElement
  beforeEach(() => {
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    host.remove()
  })

  it('图内配色、推荐配色各一组；推荐配色的说明写明所选期刊没有规定配色；点一格写下去', async () => {
    const onChange = vi.fn()
    await act(async () =>
      root.render(
        <ColorFieldContext.Provider
          value={{ rich: true, documentColors: ['#123456'], figureColors: ['#1f77b4'], recommended: recommendedSwatches('Nature') }}
        >
          <ColorField ariaLabel="颜色" value="#ff0000" onChange={onChange} />
        </ColorFieldContext.Provider>,
      ),
    )
    const swatch = host.querySelector<HTMLButtonElement>('[data-color-swatch]')!
    await act(async () => {
      swatch.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
      swatch.click()
    })
    const groups = [...document.querySelectorAll('[data-color-group]')].map((g) => g.getAttribute('data-color-group'))
    expect(groups).toEqual(['document', 'figure', 'recommended'])
    const rec = document.querySelector('[data-color-group="recommended"]')!
    expect(rec.querySelector('p')!.getAttribute('title')).toContain('Nature')
    expect(rec.querySelectorAll('[data-color-chip]')).toHaveLength(8)
    await act(async () => rec.querySelector<HTMLButtonElement>('[data-color-chip="#0072b2"]')!.click())
    expect(onChange).toHaveBeenCalledWith('#0072b2')
  })

  it('没有图内颜色就不出那一组', async () => {
    await act(async () =>
      root.render(
        <ColorFieldContext.Provider value={{ rich: true, documentColors: [], figureColors: [], recommended: recommendedSwatches(null) }}>
          <ColorField ariaLabel="颜色" value="#ff0000" onChange={() => {}} />
        </ColorFieldContext.Provider>,
      ),
    )
    const swatch = host.querySelector<HTMLButtonElement>('[data-color-swatch]')!
    await act(async () => {
      swatch.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
      swatch.click()
    })
    expect(document.querySelector('[data-color-group="figure"]')).toBeNull()
    expect(document.querySelector('[data-color-group="recommended"] p')!.getAttribute('title')).not.toContain('没有规定')
  })
})
