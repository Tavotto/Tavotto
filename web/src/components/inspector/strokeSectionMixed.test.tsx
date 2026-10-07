/**
 * 标注（箭头 / 形状）外观分组在多选下的「多个值」（2026-10-07 审计 §9.2 P0）。
 *
 * 判据的主语：**属性栏里那块色块 / 那个数字框此刻显示的是什么**——不是文档里的值。
 * 之前颜色不一致时 `shared()` 给 undefined，色块退回一个写死的 #1B1B18（与默认墨色
 * 同一个数，看起来就像「都是黑的」）；填充不透明度退回 100%。两处都是控件在谎报。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import type { ArrowObject, ShapeObject } from '@/types/document'
import { ArrowSection, ShapeSection } from './StrokeSection'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const arrowOf = (id: string, color: string): ArrowObject => ({
  id,
  type: 'arrow',
  x: 0,
  y: 0,
  w: 40,
  h: 10,
  start: { rx: 0, ry: 1 },
  end: { rx: 1, ry: 0 },
  color,
  strokePt: 1,
  head: 'end',
})

const shapeOf = (id: string, over: Partial<ShapeObject> = {}): ShapeObject => ({
  id,
  type: 'shape',
  shape: 'rect',
  x: 0,
  y: 0,
  w: 30,
  h: 20,
  color: '#1B1B18',
  strokePt: 1,
  fill: null,
  ...over,
})

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

const render = (node: React.ReactNode) =>
  act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))

/** 取色盘的可达名是「<标签>：取色盘」；色块是它的父元素 */
const swatchOf = (label: string) => {
  const input = host.querySelector<HTMLInputElement>(`input[type=color][aria-label="${label}：取色盘"]`)
  expect(input, `${label} 的取色盘不见了`).toBeTruthy()
  return input!.parentElement as HTMLElement
}
const fillsOf = (swatch: HTMLElement) =>
  Array.from(swatch.querySelectorAll('div')).map((d) => d.style.background)

describe('箭头：颜色不一致', () => {
  it('色块是「多个值」，不退回 #1B1B18 也不画任何一个成员的颜色', async () => {
    await render(<ArrowSection objs={[arrowOf('a1', '#ff0000'), arrowOf('a2', '#0000ff')]} />)
    const swatch = swatchOf('颜色')
    expect(swatch.getAttribute('data-mixed')).toBe('true')
    expect(swatch.title).toBe('多个值')
    expect(fillsOf(swatch)).not.toContain('rgb(27, 27, 24)')
    expect(fillsOf(swatch)).not.toContain('rgb(255, 0, 0)')
    expect(fillsOf(swatch)).not.toContain('rgb(0, 0, 255)')
  })

  it('颜色一致时色块就是那个色（对照组）', async () => {
    await render(<ArrowSection objs={[arrowOf('a1', '#ff0000'), arrowOf('a2', '#ff0000')]} />)
    const swatch = swatchOf('颜色')
    expect(swatch.getAttribute('data-mixed')).toBeNull()
    expect(swatch.title).toBe('#FF0000')
  })
})

describe('形状：描边 / 填充 / 填充不透明度不一致', () => {
  it('描边色不一致：色块是「多个值」', async () => {
    await render(
      <ShapeSection objs={[shapeOf('s1', { color: '#ff0000' }), shapeOf('s2', { color: '#00ff00' })]} />,
    )
    const swatch = swatchOf('描边')
    expect(swatch.getAttribute('data-mixed')).toBe('true')
    expect(fillsOf(swatch)).not.toContain('rgb(27, 27, 24)')
    expect(fillsOf(swatch)).not.toContain('rgb(255, 0, 0)')
  })

  it('都有填充但颜色不同：填充仍是开着的，色块是「多个值」（不退回「添加填充」）', async () => {
    await render(
      <ShapeSection objs={[shapeOf('s1', { fill: '#ffffff' }), shapeOf('s2', { fill: '#ffcc00' })]} />,
    )
    const swatch = swatchOf('填充')
    expect(swatch.getAttribute('data-mixed')).toBe('true')
    expect(host.textContent).not.toContain('添加填充')
  })

  it('填充不透明度不一致：数字框留空 +「多个值」占位，不谎报 100%', async () => {
    await render(
      <ShapeSection
        objs={[shapeOf('s1', { fill: '#ffffff', fillOpacity: 0.5 }), shapeOf('s2', { fill: '#ffffff' })]}
      />,
    )
    const opacity = Array.from(host.querySelectorAll('label'))
      .find((l) => l.textContent?.includes('不透明度'))
      ?.querySelector('input') as HTMLInputElement
    expect(opacity, '不透明度输入框不见了').toBeTruthy()
    expect(opacity.value).toBe('')
    expect(opacity.placeholder).toBe('多个值')
  })

  it('填充不透明度一致时照旧显示百分数（对照组）', async () => {
    await render(
      <ShapeSection
        objs={[
          shapeOf('s1', { fill: '#ffffff', fillOpacity: 0.5 }),
          shapeOf('s2', { fill: '#ffffff', fillOpacity: 0.5 }),
        ]}
      />,
    )
    const opacity = Array.from(host.querySelectorAll('label'))
      .find((l) => l.textContent?.includes('不透明度'))
      ?.querySelector('input') as HTMLInputElement
    expect(opacity.value).toBe('50')
  })
})
