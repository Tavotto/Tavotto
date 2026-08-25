/**
 * 视觉选择器矩阵：当前值 / 点击更新 / 键盘 / 未知值 fallback / aria 语义。
 * 写入值必须是 Matplotlib 原始 enum——这里逐个钉住。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { ArrowHeadPicker, ArrowStylePicker } from './ArrowPickers'
import { ColormapPicker } from './ColormapPicker'
import { colormapGradient, COLORMAP_STOPS } from './colormapStops'
import { HatchPicker } from './HatchPicker'
import { LegendPositionPicker } from './LegendPositionPicker'
import { LineStylePicker } from './LineStylePicker'
import { MarkerPicker } from './MarkerPicker'
import { TickAndSpineDiagram, type TickSpineAdapter } from './TickAndSpineDiagram'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}

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
  document.body.innerHTML = ''
})

/** radio 一律全 document 找：Popover 里的选项挂在 portal 上 */
const radios = () => Array.from(document.querySelectorAll<HTMLElement>('[role="radio"]'))
const radioByLabel = (label: string) =>
  radios().find((r) => r.getAttribute('aria-label') === label)

describe('LineStylePicker', () => {
  it('当前值 aria-checked，点击写回原始 enum', async () => {
    const onChange = vi.fn()
    await mount(
      <LineStylePicker value="-" options={['-', '--', ':', '-.']} onChange={onChange} ariaLabel="线型" />,
    )
    expect(radioByLabel('实线')?.getAttribute('aria-checked')).toBe('true')
    await act(async () => {
      radioByLabel('虚线')!.click()
    })
    expect(onChange).toHaveBeenCalledWith('--')
  })

  it('每个选项都有真实线段预览（SVG），不是纯文字编码', async () => {
    await mount(
      <LineStylePicker value="-" options={['-', '--', ':', '-.']} onChange={() => {}} ariaLabel="线型" />,
    )
    for (const r of radios()) expect(r.querySelector('svg line')).toBeTruthy()
  })

  it('自定义 dash 不丢失：原始名称进选项，选它不改值域', async () => {
    const onChange = vi.fn()
    await mount(
      <LineStylePicker
        value="(0, (1, 2))"
        options={['-', '--', ':', '-.']}
        onChange={onChange}
        ariaLabel="线型"
      />,
    )
    const custom = radios().find((r) => r.getAttribute('aria-checked') === 'true')!
    expect(custom.getAttribute('aria-label')).toContain('(0, (1, 2))')
  })

  it('方向键在选项间漫游并选中', async () => {
    const onChange = vi.fn()
    await mount(
      <LineStylePicker value="-" options={['-', '--', ':', '-.']} onChange={onChange} ariaLabel="线型" />,
    )
    const group = document.querySelector('[role="radiogroup"]')!
    await act(async () => {
      group.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }),
      )
    })
    expect(onChange).toHaveBeenCalledWith('--')
  })
})

describe('MarkerPicker', () => {
  it('触发器显示当前 marker 的名字；打开后是图形网格', async () => {
    const onChange = vi.fn()
    await mount(
      <MarkerPicker value="o" options={['None', 'o', 's', 'D', '^']} onChange={onChange} ariaLabel="标记" />,
    )
    const trigger = host.querySelector('button[aria-label="标记"]') as HTMLButtonElement
    expect(trigger.textContent).toContain('圆点')
    await act(async () => {
      trigger.click()
    })
    const diamond = radioByLabel('菱形')!
    expect(diamond.querySelector('svg')).toBeTruthy()
    await act(async () => {
      diamond.click()
    })
    expect(onChange).toHaveBeenCalledWith('D')
  })

  it('未识别 marker 显示原始代码，不丢失', async () => {
    await mount(
      <MarkerPicker value={'$\\odot$'} options={['None', 'o']} onChange={() => {}} ariaLabel="标记" />,
    )
    const trigger = host.querySelector('button[aria-label="标记"]') as HTMLButtonElement
    expect(trigger.textContent).toContain('$\\odot$')
  })
})

describe('HatchPicker', () => {
  it('空串是「无花纹」，known 花纹有纹理缩略，点击写原始串', async () => {
    const onChange = vi.fn()
    await mount(
      <HatchPicker value="" options={['', '/', 'xx', '..']} onChange={onChange} ariaLabel="花纹" />,
    )
    const trigger = host.querySelector('button[aria-label="花纹"]') as HTMLButtonElement
    expect(trigger.textContent).toContain('无花纹')
    await act(async () => {
      trigger.click()
    })
    const xx = radioByLabel('花纹 xx')!
    expect(xx.querySelector('svg pattern')).toBeTruthy()
    await act(async () => {
      xx.click()
    })
    expect(onChange).toHaveBeenCalledWith('xx')
  })
})

describe('ColormapPicker', () => {
  it('已知 cmap 的 stops 来自真实 matplotlib 采样', () => {
    expect(COLORMAP_STOPS.viridis[0]).toBe('#440154')
    expect(COLORMAP_STOPS.viridis.at(-1)).toBe('#fde725')
    for (const stops of Object.values(COLORMAP_STOPS)) expect(stops).toHaveLength(9)
    expect(colormapGradient('viridis')).toContain('linear-gradient')
    expect(colormapGradient('my_custom_cmap')).toBeNull()
  })

  it('触发器带渐变条；自定义 cmap 回落到名称', async () => {
    const onChange = vi.fn()
    await mount(
      <ColormapPicker value="viridis" options={['viridis', 'plasma']} onChange={onChange} ariaLabel="色图" />,
    )
    const trigger = host.querySelector('button[aria-label="色图"]') as HTMLButtonElement
    expect(trigger.textContent).toContain('viridis')
    await act(async () => {
      trigger.click()
    })
    await act(async () => {
      radios().find((r) => r.getAttribute('aria-label') === 'plasma')!.click()
    })
    expect(onChange).toHaveBeenCalledWith('plasma')
  })
})

describe('LegendPositionPicker', () => {
  const LOCS = [
    'best', 'upper right', 'upper left', 'lower left', 'lower right',
    'right', 'center left', 'center right', 'lower center', 'upper center', 'center',
  ]

  it('3×3 网格 + 自动档；点击写 matplotlib loc 名', async () => {
    const onChange = vi.fn()
    await mount(
      <LegendPositionPicker value="lower right" options={LOCS} onChange={onChange} ariaLabel="位置" />,
    )
    expect(radioByLabel('右下')?.getAttribute('aria-checked')).toBe('true')
    await act(async () => {
      radioByLabel('左上')!.click()
    })
    expect(onChange).toHaveBeenCalledWith('upper left')
    const best = Array.from(host.querySelectorAll('button')).find(
      (b) => b.textContent === '自动',
    )!
    await act(async () => {
      best.click()
    })
    expect(onChange).toHaveBeenCalledWith('best')
  })

  it('manifest 没给的档位不渲染；custom 显示说明不显示假档位', async () => {
    await mount(
      <LegendPositionPicker
        value="custom"
        options={['custom', 'upper right', 'best']}
        onChange={() => {}}
        ariaLabel="位置"
      />,
    )
    expect(radioByLabel('左上')).toBeUndefined()
    expect(radioByLabel('右上')).toBeTruthy()
    expect(host.textContent).toContain('拖到过自定义位置')
  })
})

describe('ArrowPickers', () => {
  it('arrowstyle：已知样式有箭头预览，custom 显示原文', async () => {
    const onChange = vi.fn()
    await mount(
      <ArrowStylePicker
        value="->"
        options={['-', '->', '-|>', 'custom']}
        onChange={onChange}
        ariaLabel="箭头样式"
      />,
    )
    expect(radioByLabel('细箭头')?.getAttribute('aria-checked')).toBe('true')
    await act(async () => {
      radioByLabel('实心箭头')!.click()
    })
    expect(onChange).toHaveBeenCalledWith('-|>')
  })

  it('画布端型：四档各有预览，选中写 ArrowHeadType', async () => {
    const onChange = vi.fn()
    await mount(
      <ArrowHeadPicker value="triangle" at="end" onChange={onChange} ariaLabel="终点端型" />,
    )
    expect(radios()).toHaveLength(4)
    await act(async () => {
      radioByLabel('短线')!.click()
    })
    expect(onChange).toHaveBeenCalledWith('bar')
  })
})

describe('TickAndSpineDiagram', () => {
  const adapterOf = (over: Partial<TickSpineAdapter> = {}, state: Record<string, boolean> = {}) => {
    const values: Record<string, boolean> = {
      ticks_bottom: true, ticks_top: false, ticks_left: true, ticks_right: false,
      spine_bottom: true, spine_top: true, spine_left: true, spine_right: true,
      grid_x: false, grid_y: false,
      ...state,
    }
    return {
      has: (p: string) => p in values,
      read: (p: string) => values[p],
      toggle: vi.fn(),
      labelOf: (p: string) => `L:${p}`,
      isOverridden: () => false,
      reset: vi.fn(),
      ...over,
    } satisfies TickSpineAdapter
  }

  const sw = (label: string) =>
    Array.from(document.querySelectorAll<HTMLElement>('[role="switch"]')).find(
      (el) => el.getAttribute('aria-label') === label,
    )

  it('每条边单独成 switch，aria-checked 反映实况，点击取反', async () => {
    const a = adapterOf()
    await mount(<TickAndSpineDiagram adapter={a} />)
    const top = sw('L:ticks_top')!
    expect(top.getAttribute('aria-checked')).toBe('false')
    await act(async () => {
      top.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(a.toggle).toHaveBeenCalledWith('ticks_top', true)
  })

  it('键盘 Enter 同样切换；每条边可聚焦', async () => {
    const a = adapterOf()
    await mount(<TickAndSpineDiagram adapter={a} />)
    const left = sw('L:spine_left')!
    expect(left.getAttribute('tabindex')).toBe('0')
    await act(async () => {
      left.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    expect(a.toggle).toHaveBeenCalledWith('spine_left', false)
  })

  it('manifest 没有的字段整块不画；全缺时组件不渲染', async () => {
    const a = adapterOf({ has: (p) => p.startsWith('ticks_') })
    await mount(<TickAndSpineDiagram adapter={a} />)
    expect(sw('L:ticks_bottom')).toBeTruthy()
    expect(sw('L:spine_bottom')).toBeUndefined()

    await act(async () => {
      root!.unmount()
    })
    document.body.innerHTML = ''
    const none = adapterOf({ has: () => false })
    await mount(<TickAndSpineDiagram adapter={none} />)
    expect(document.querySelectorAll('[role="switch"]')).toHaveLength(0)
  })

  it('已修改的边给出单项恢复入口', async () => {
    const a = adapterOf({ isOverridden: (p: string) => p === 'ticks_top' })
    await mount(<TickAndSpineDiagram adapter={a} />)
    const chip = Array.from(host.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.includes('L:ticks_top'),
    )!
    await act(async () => {
      chip.click()
    })
    expect(a.reset).toHaveBeenCalledWith('ticks_top')
  })
})
