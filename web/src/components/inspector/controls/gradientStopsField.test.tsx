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
import { GradientStopsField, readGradient } from './GradientStopsField'

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
    await mount(
      <GradientStopsField label="渐变" value={{ stops: ['#ffd8b2', '#d94d0d'] }} onChange={() => {}} />,
    )
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

  it.each([null, '#ff0000', { stops: ['#000000'] }, { stops: [1, 2] }, { stops: 'ab' }])(
    '形状不对不猜：%j',
    (v) => {
      expect(readGradient(v)).toBeNull()
    },
  )
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
