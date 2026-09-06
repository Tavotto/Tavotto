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
import type { MarkerShape } from '@/lib/api'
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

/* ------------------- MarkerPicker：引擎发来的「真实形状」 ------------------- */

const trig = () => host.querySelector('button[aria-label="标记"]') as HTMLButtonElement

/** 一个闭合三角（单位框 [-0.5, 0.5]，y 向上；末尾那个是 CLOSEPOLY 占位点） */
const TRIANGLE: MarkerShape = {
  kind: 'path',
  vertices: [
    [0, 0.5],
    [0.5, -0.5],
    [-0.5, -0.5],
    [0, 0],
  ],
  codes: [1, 2, 2, 79],
}

/** codes 为 null = 「首点 MOVETO，其余 LINETO」，不是「没有路径」 */
const DIAGONAL: MarkerShape = {
  kind: 'path',
  vertices: [
    [-0.5, -0.5],
    [0.5, 0.5],
  ],
  codes: null,
}

describe('MarkerPicker：脚本原始也画得出真实形状', () => {
  it('值 = original 且引擎认出名字：画那个图形，继承状态点仍在', async () => {
    await mount(
      <MarkerPicker
        value="original"
        options={['original', 'o', 's']}
        current={{ kind: 'named', name: 'o' }}
        onChange={() => {}}
        ariaLabel="标记"
      />,
    )
    // 形状与状态点是**并列**的两件事：形状说图上是个圆，状态点说这是继承来的
    expect(trig().querySelector('[data-marker-preview] circle')).toBeTruthy()
    expect(trig().querySelector('[data-marker-inherited]')).toBeTruthy()
    // 文字名把形状也说出来（网格里那一格的可达名与 tooltip 同一份）
    expect(trig().textContent).toContain('脚本原始')
    expect(trig().textContent).toContain('圆点')
  })

  it('引擎只给几何时照顶点画，路径码逐个翻成 SVG 指令', async () => {
    await mount(
      <MarkerPicker
        value="original"
        options={['original', 'o']}
        current={TRIANGLE}
        onChange={() => {}}
        ariaLabel="标记"
      />,
    )
    const d = trig().querySelector('[data-marker-preview] path')!.getAttribute('d')!
    // y 要翻过来：引擎的 y 向上，SVG 的 y 向下 —— 顶点 (0, 0.5) 必须落在**上**边
    expect(d.startsWith('M6.00 1.80')).toBe(true)
    expect(d).toContain('L10.20 10.20')
    expect(d).toContain('L1.80 10.20')
    expect(d.endsWith('Z')).toBe(true)
    // 同时填充与描边：开放子路径与来回穿过中心的闭合路径填出来都是零面积
    const path = trig().querySelector('[data-marker-preview] path')!
    expect(path.getAttribute('fill')).toBe('currentColor')
    expect(path.getAttribute('stroke')).toBe('currentColor')
    expect(trig().querySelector('[data-marker-inherited]')).toBeTruthy()
  })

  it('codes 为 null = 首点 MOVETO 其余 LINETO，不是「没有路径」', async () => {
    await mount(
      <MarkerPicker
        value="original"
        options={['original']}
        current={DIAGONAL}
        onChange={() => {}}
        ariaLabel="标记"
      />,
    )
    expect(trig().querySelector('[data-marker-preview] path')!.getAttribute('d')).toBe(
      'M1.80 10.20 L10.20 1.80',
    )
  })

  it('多个形状：不画其中任何一个，文字说「多个形状」', async () => {
    await mount(
      <MarkerPicker
        value="original"
        options={['original', 'o']}
        current={{ kind: 'multiple' }}
        onChange={() => {}}
        ariaLabel="标记"
      />,
    )
    expect(trig().querySelector('[data-marker-preview]')).toBeNull()
    expect(trig().querySelector('[data-marker-inherited]')).toBeTruthy()
    expect(trig().textContent).toContain('多个形状')
  })

  it('引擎没发事实（老引擎）：退回只有继承状态点，一个字节不变', async () => {
    await mount(
      <MarkerPicker value="original" options={['original', 'o']} onChange={() => {}} ariaLabel="标记" />,
    )
    expect(trig().querySelector('[data-marker-preview]')).toBeNull()
    expect(trig().querySelector('[data-marker-inherited]')).toBeTruthy()
    expect(trig().textContent).toContain('脚本原始')
  })

  it('too_complex / none 都不画形状：没有「那一个形状」可画', async () => {
    const cases: MarkerShape[] = [{ kind: 'too_complex' }, { kind: 'none' }]
    for (const current of cases) {
      await mount(
        <MarkerPicker
          value="original"
          options={['original']}
          current={current}
          onChange={() => {}}
          ariaLabel="标记"
        />,
      )
      expect(trig().querySelector('[data-marker-preview]')).toBeNull()
      await act(async () => {
        root?.unmount()
      })
      root = null
      document.body.innerHTML = ''
    }
  })

  it('引擎给了这边画不出的名字：退回代码字样，不画错一个形状', async () => {
    await mount(
      <MarkerPicker
        value={'$\\odot$'}
        options={['None', 'o']}
        current={{ kind: 'named', name: 'H' }}
        onChange={() => {}}
        ariaLabel="标记"
      />,
    )
    expect(trig().querySelector('[data-marker-preview]')).toBeNull()
    expect(trig().textContent).toContain('$\\odot$')
  })

  it('认不出的取值 + 几何：画形状，代码仍在文字里（不丢失）', async () => {
    await mount(
      <MarkerPicker
        value="(5, 1, 0)"
        options={['None', 'o']}
        current={TRIANGLE}
        onChange={() => {}}
        ariaLabel="标记"
      />,
    )
    expect(trig().querySelector('[data-marker-preview] path')).toBeTruthy()
    expect(trig().textContent).toContain('(5, 1, 0)')
  })

  it('事实只描述当前值那一格：别的格子照旧', async () => {
    await mount(
      <MarkerPicker
        value="original"
        options={['original', 'o', 's']}
        current={TRIANGLE}
        onChange={() => {}}
        ariaLabel="标记"
      />,
    )
    await act(async () => {
      trig().click()
    })
    // 当前那一格（脚本原始）画的是引擎发来的三角
    const cur = radios().find((r) => r.getAttribute('aria-checked') === 'true')!
    expect(cur.querySelector('[data-marker-preview] path')!.getAttribute('d')).toContain(
      'M6.00 1.80',
    )
    // 方块那一格仍是方块，没被事实污染
    expect(radioByLabel('方块')!.querySelector('[data-marker-preview] rect')).toBeTruthy()
  })

  it('取值自己就是已知图形时不补那半句（「圆点（圆点）」是噪音）', async () => {
    await mount(
      <MarkerPicker
        value="o"
        options={['None', 'o']}
        current={{ kind: 'named', name: 'o' }}
        onChange={() => {}}
        ariaLabel="标记"
      />,
    )
    expect(trig().textContent).toBe('圆点')
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
