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
import { tipLabelOf } from './OptionGrid'
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
  it('空串是「无」，known 纹理有缩略图，点击写原始串', async () => {
    const onChange = vi.fn()
    await mount(
      <HatchPicker value="" options={['', '/', 'xx', '..']} onChange={onChange} ariaLabel="纹理" />,
    )
    const trigger = host.querySelector('button[aria-label="纹理"]') as HTMLButtonElement
    expect(trigger.textContent).toContain('无')
    await act(async () => {
      trigger.click()
    })
    const xx = radioByLabel('密交叉')!
    expect(xx.querySelector('svg pattern')).toBeTruthy()
    await act(async () => {
      xx.click()
    })
    expect(onChange).toHaveBeenCalledWith('xx')
  })

  /**
   * 审计 T21 的验收：**所有纹理选项有可理解的名称，图形与底层图案一一对应**。
   * 引擎的 `HATCHES` 是 16 个代码，逐个查——名字不许等于代码本身，也不许
   * 落到「纹理 <代码>」那条开集兜底上（那是给脚本自拼的花纹留的）。
   */
  it('引擎那 16 个纹理代码逐个有名字，名字里不出现代码', async () => {
    const HATCHES = ['', '/', '\\', '|', '-', '+', 'x', 'o', 'O', '.', '*', '//', '\\\\', 'xx', '..', '++']
    const onChange = vi.fn()
    await mount(
      <HatchPicker value="" options={HATCHES} onChange={onChange} ariaLabel="纹理" />,
    )
    await act(async () => {
      ;(host.querySelector('button[aria-label="纹理"]') as HTMLButtonElement).click()
    })
    const names = radios().map((r) => r.getAttribute('aria-label')!)
    expect(names).toHaveLength(HATCHES.length)
    expect(new Set(names).size, '有两个纹理重名，图形与名字对不上').toBe(HATCHES.length)
    for (const [i, name] of names.entries()) {
      const code = HATCHES[i]
      expect(name, `${JSON.stringify(code)} 落到了开集兜底`).not.toContain('纹理 ')
      if (code) expect(name, `${JSON.stringify(code)} 的名字里带着代码`).not.toContain(code)
    }
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

  it('3×3 网格 + 「最佳位置」档；点击写 matplotlib loc 名；没有模糊的「自动」按钮', async () => {
    const onChange = vi.fn()
    await mount(
      <LegendPositionPicker value="lower right" options={LOCS} onChange={onChange} ariaLabel="位置" />,
    )
    expect(radioByLabel('右下')?.getAttribute('aria-checked')).toBe('true')
    await act(async () => {
      radioByLabel('左上')!.click()
    })
    expect(onChange).toHaveBeenCalledWith('upper left')
    // matplotlib 的 `best` 是「最佳位置」（按数据避让），不是一个无上下文的「自动」
    const all = Array.from(host.querySelectorAll('button'))
    expect(all.find((b) => b.textContent === '自动')).toBeUndefined()
    const best = all.find((b) => b.textContent === '最佳位置')!
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
      resetAll: vi.fn(),
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

  it('已修改的边在图上自己标出；恢复只有一个动作（不再逐边出 chip）', async () => {
    const a = adapterOf({ isOverridden: (p: string) => p === 'ticks_top' })
    await mount(<TickAndSpineDiagram adapter={a} />)
    // 修改标记跟着那条边走：只有上边带 data-tick-modified
    expect(sw('L:ticks_top')?.getAttribute('data-tick-modified')).toBe('true')
    expect(sw('L:ticks_bottom')?.getAttribute('data-tick-modified')).toBeNull()
    // 以前这里长出一排「上边刻度线 ×」chip——与图上的状态重复表达同一组设置
    expect(
      Array.from(host.querySelectorAll('button')).some((b) =>
        b.getAttribute('aria-label')?.includes('L:ticks_top'),
      ),
    ).toBe(false)
    const reset = host.querySelector('[data-tick-reset-all]') as HTMLButtonElement
    expect(reset).toBeTruthy()
    await act(async () => {
      reset.click()
    })
    expect(a.resetAll).toHaveBeenCalledTimes(1)
  })

  it('什么都没改过时没有恢复按钮', async () => {
    await mount(<TickAndSpineDiagram adapter={adapterOf()} />)
    expect(host.querySelector('[data-tick-reset-all]')).toBeNull()
  })
})

describe('OptionGrid：内部代码不进可见文案（审计 T15 / T21）', () => {
  it('tooltip 只说名字，代码落成 data-code', async () => {
    expect(tipLabelOf({ label: '无', code: 'None' })).toBe('无')
    expect(tipLabelOf({ label: '点线', code: ':' })).toBe('点线')
    await mount(
      <LineStylePicker value="-" options={['-', '--', ':', '-.']} onChange={() => {}} ariaLabel="线型" />,
    )
    const dotted = radioByLabel('点线')!
    expect(dotted.getAttribute('data-code')).toBe(':')
    expect(dotted.getAttribute('aria-label')).toBe('点线')
  })

  /**
   * **气泡关着的时候整条判据是恒真的**——Radix 的 Content 只在打开时才进
   * DOM，所以「页面里没有 `名字 · 代码`」在任何实现下都成立。要判它就得先
   * 把气泡打开（聚焦触发器），再看气泡里那句话。
   */
  it('聚焦弹出的气泡里只有名字，没有 “点线 · :” 这种拼法', async () => {
    await mount(
      <LineStylePicker value="-" options={['-', '--', ':', '-.']} onChange={() => {}} ariaLabel="线型" />,
    )
    const dotted = radioByLabel('点线')!
    await act(async () => {
      dotted.focus()
      dotted.dispatchEvent(new FocusEvent('focus', { bubbles: false }))
      dotted.dispatchEvent(new FocusEvent('focusin', { bubbles: true }))
    })
    const tip = document.querySelector('[role="tooltip"]')
    expect(tip, '气泡没打开，这条判据就是恒真的').toBeTruthy()
    expect(tip!.textContent).toBe('点线')
  })
})
