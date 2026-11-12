/**
 * 渐变色带「两端取色」这一行（引擎字段 `gradient`，ADR 0167）。
 *
 * 钉：两个色块的名字按屏幕方向说（竖向 = 上 / 下，横向 = 左 / 右）；改一端只换那一端、另一端原样带上；
 * 渐变条用这两端画；读引擎值时形状不对就不渲染（不猜）；展示注册表把 `gradient` 类型认成这种控件、
 * 填充角色的首屏里有它的位置。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import type { EditableField } from '@/lib/api'
import { controlKindOf } from '../presentation/registry'
import { ROLE_PROFILES } from '../presentation/roleProfiles'
import { GradientStopsField, gradientPayload, readGradient, readGradientWithFact } from './GradientStopsField'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let host: HTMLDivElement

async function mount(ui: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root!.render(<TooltipProvider>{ui}</TooltipProvider>)
  })
}

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  root = null
  host?.remove()
})

function colorInput(name: RegExp): HTMLInputElement {
  const el = [...host.querySelectorAll<HTMLInputElement>('input[type="color"]')].find((i) =>
    name.test(i.getAttribute('aria-label') ?? ''),
  )
  if (!el) throw new Error(`没找到取色框 ${name}`)
  return el
}

async function pick(input: HTMLInputElement, hex: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(input, hex)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

describe('GradientStopsField', () => {
  it('竖向渐变：两个色块叫「上端 / 下端」，改上端只换上端', async () => {
    const onChange = vi.fn()
    await mount(
      <GradientStopsField
        label="渐变"
        value={{ stops: ['#ffd8b2', '#d94d0d'], direction: 'vertical' }}
        onChange={onChange}
      />,
    )
    const top = colorInput(/上端/)
    expect(colorInput(/下端/)).toBeTruthy()
    await pick(top, '#ffd9de')
    expect(onChange).toHaveBeenLastCalledWith(['#ffd9de', '#d94d0d'])
  })

  it('横向渐变：叫「左端 / 右端」，改右端只换右端', async () => {
    const onChange = vi.fn()
    await mount(
      <GradientStopsField
        label="渐变"
        value={{ stops: ['#e61a1a', '#ffe61a'], direction: 'horizontal' }}
        onChange={onChange}
      />,
    )
    expect(host.querySelector('input[aria-label*="上端"]')).toBeNull()
    await pick(colorInput(/右端/), '#0000ff')
    expect(onChange).toHaveBeenLastCalledWith(['#e61a1a', '#0000ff'])
  })

  it('渐变条用这两端画；多个值时不画任何一个渐变', async () => {
    await mount(<GradientStopsField label="渐变" value={{ stops: ['#ffd8b2', '#d94d0d'] }} onChange={() => {}} />)
    const bar = host.querySelector<HTMLElement>('[data-gradient-bar]')!
    expect(bar.style.background).toContain('linear-gradient')
    await act(async () => {
      root!.render(
        <TooltipProvider>
          <GradientStopsField label="渐变" mixed value={{ stops: ['#ffd8b2', '#d94d0d'] }} onChange={() => {}} />
        </TooltipProvider>,
      )
    })
    expect(host.querySelector<HTMLElement>('[data-gradient-bar]')!.style.background).toBe('')
  })
})

describe('readGradient', () => {
  it('认引擎发来的形状', () => {
    expect(readGradient({ stops: ['#000000', '#ffffff'], direction: 'horizontal' })).toEqual({
      stops: ['#000000', '#ffffff'],
      direction: 'horizontal',
    })
    expect(readGradient({ stops: ['#000000', '#ffffff'] })?.direction).toBe('vertical')
  })

  it.each([null, '#ff0000', { stops: ['#000000'] }, { stops: [1, 2] }, { stops: 'ab' }])('形状不对不猜：%j', (v) => {
    expect(readGradient(v)).toBeNull()
  })
})

describe('override 值缺 direction 时（Codex P2）', () => {
  const fact = { stops: ['#e61a1a', '#ffe61a'], direction: 'horizontal', screen_start: 'low' }
  const overrideValue = { stops: ['#000000', '#ffffff'] }

  it('stops 取当前值，direction / screen_start 取引擎事实：横向渐变改过后仍是「左端 / 右端」', async () => {
    const g = readGradientWithFact(overrideValue, fact)!
    expect(g).toEqual({ stops: ['#000000', '#ffffff'], direction: 'horizontal', screenStart: 'low' })
    await mount(<GradientStopsField label="渐变" value={g} onChange={() => {}} />)
    expect(colorInput(/左端/)).toBeTruthy()
    expect(colorInput(/右端/)).toBeTruthy()
    expect(host.querySelector('input[aria-label*="上端"]')).toBeNull()
  })

  it('写回带 screen_start；事实里没有时不带，形状不对仍拒收', () => {
    const g = readGradientWithFact(overrideValue, fact)!
    expect(gradientPayload(['#111111', '#222222'], g)).toEqual({
      stops: ['#111111', '#222222'],
      screen_start: 'low',
    })
    expect(gradientPayload(['#111111', '#222222'], { stops: ['#1', '#2'] })).toEqual({
      stops: ['#111111', '#222222'],
    })
    expect(readGradientWithFact({ stops: [1, 2] }, fact)).toBeNull()
  })
})

describe('展示注册表', () => {
  const field: EditableField = { prop: 'gradient', type: 'gradient', value: { stops: ['#000000', '#ffffff'] } }

  it('gradient 类型认成两端取色控件', () => {
    expect(controlKindOf('fill', field)).toBe('gradient')
  })

  it('填充角色的首屏里有它，和「填充颜色」占同一个位置', () => {
    const primary = ROLE_PROFILES.fill.primary
    expect(primary).toContain('gradient')
    expect(primary.indexOf('gradient')).toBe(primary.indexOf('facecolor') + 1)
  })
})

describe('图例色块跟着改（ADR 0167 修订）', () => {
  const cand = (ci: number, gi: number, caption?: string) => ({
    ref: [ci, gi] as [number, number],
    gid: `axes_0.fill_${ci}`,
    stops: ['#ffd8b2', '#d94d0d'] as [string, string],
    ...(caption ? { caption } : {}),
  })

  it('只有一块对得上、已经配上：整行不出现（静默跟着改，不加说明）', async () => {
    const legend = {
      candidates: [cand(4, 0, 'TiO₂')],
      linked: [4, 0] as [number, number],
      auto: [4, 0] as [number, number],
    }
    await mount(
      <GradientStopsField
        label="渐变"
        value={{ stops: ['#ffd8b2', '#d94d0d'], legend }}
        onChange={() => {}}
        onLink={() => {}}
      />,
    )
    expect(host.querySelector('[data-gradient-legend]')).toBeNull()
    expect(host.textContent).not.toContain('TiO₂')
  })

  it('几块同色：列出来、都不预选、没有提示文字，也没有「不跟」；点一块回它的 ref', async () => {
    const onLink = vi.fn()
    const legend = { candidates: [cand(2, 0, 'B1'), cand(2, 1, 'B2')], linked: null, auto: null }
    await mount(
      <GradientStopsField
        label="渐变"
        value={{ stops: ['#ffd8b2', '#d94d0d'], legend }}
        onChange={() => {}}
        onLink={onLink}
      />,
    )
    const row = host.querySelector('[data-gradient-legend]')!
    const radios = [...row.querySelectorAll('[role="radio"]')]
    expect(radios.map((r) => r.textContent)).toEqual(['B1', 'B2'])
    expect(radios.every((r) => r.getAttribute('aria-checked') === 'false')).toBe(true)
    expect(row.querySelectorAll('p')).toHaveLength(0)
    await act(async () => {
      ;(radios[1] as HTMLButtonElement).click()
    })
    expect(onLink).toHaveBeenCalledWith([2, 1])
  })

  it('多选（不给 onLink）不画这一行', async () => {
    const legend = { candidates: [cand(2, 0), cand(2, 1)], linked: null, auto: null }
    await mount(
      <GradientStopsField label="渐变" value={{ stops: ['#ffd8b2', '#d94d0d'], legend }} onChange={() => {}} />,
    )
    expect(host.querySelector('[data-gradient-legend]')).toBeNull()
  })

  it('读得出引擎的 legend；写回把此刻的配对写死，换配对时用新的', () => {
    const g = readGradientWithFact(
      { stops: ['#000000', '#ffffff'] },
      {
        stops: ['#111111', '#eeeeee'],
        direction: 'vertical',
        screen_start: 'high',
        legend: { candidates: [cand(4, 0)], linked: [4, 0], auto: [4, 0] },
      },
    )!
    expect(g.legend?.linked).toEqual([4, 0])
    expect(gradientPayload(['#000000', '#ffffff'], g)).toEqual({
      stops: ['#000000', '#ffffff'],
      screen_start: 'high',
      legend: [4, 0],
    })
    expect(gradientPayload(['#000000', '#ffffff'], g, [4, 1]).legend).toEqual([4, 1])
    // 没有候选：不带 legend 键（引擎走自动配对）
    expect('legend' in gradientPayload(['#000000', '#ffffff'], { stops: ['#000000', '#ffffff'] })).toBe(false)
  })
})
