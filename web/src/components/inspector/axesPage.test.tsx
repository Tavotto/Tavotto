/**
 * 子图页（审计 T12）：
 *   1. 纵横比是「自动 / 等比例 / 自定义比例」三档，**不是**文字编辑器；写回值
 *      与引擎 setter 可还原的形式一致（'auto' / 'equal' / 数字串）；
 *   2. 范围 → 坐标变换 → 刻度与网格 → 边框，三类任务互不混杂；反转 X / Y 并成一行；
 *   3. 边框默认四边联动（写「全部」那一档），需要差异时再展开逐边；四边不一致时
 *      联动行说「多个值」、逐边区自动展开且收不起来；联动写入会先清掉逐边覆盖；
 *   4. 按比例缩放是一次性动作：输入不生效，按「应用」才写，写完回到 100%。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EditableField, EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { resetPreview, setHistoryMode } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ElementInspector } from './ElementInspector'
import { aspectModeOf, aspectValueOf } from './controls/AspectControl'

const engineRender = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/* -------------------------------- 测试数据 -------------------------------- */

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

const SIDES = ['top', 'right', 'bottom', 'left'] as const
const lw = (side: string, v: number) =>
  f(`spine_${side}_linewidth`, 'number', v, { min: 0.1, max: 3, step: 0.1, unit: 'pt', group: '边框（逐条）' })
const col = (side: string, v: string) =>
  f(`spine_${side}_color`, 'color', v, { group: '边框（逐条）' })

/** 与 engine/manifest.py `_axes_fields` 同形 */
const axesFields = (over: { sideWidths?: Record<string, number>; aspect?: string } = {}): EditableField[] => [
  f('position', 'rect', [0.12, 0.11, 0.77, 0.77]),
  f('visible', 'bool', true),
  f('xlim', 'pair', [-2.33, 1.83], { group: '数据范围' }),
  f('ylim', 'pair', [-2.05, 1.61], { group: '数据范围' }),
  f('xscale', 'enum', 'linear', { options: ['linear', 'log'], group: '数据范围' }),
  f('yscale', 'enum', 'linear', { options: ['linear', 'log'], group: '数据范围' }),
  f('invert_x', 'bool', false, { group: '数据范围' }),
  f('invert_y', 'bool', false, { group: '数据范围' }),
  f('aspect', 'text', over.aspect ?? 'auto', { group: '数据范围' }),
  f('ticks_bottom', 'bool', true, { group: '刻度线' }),
  f('ticks_top', 'bool', false, { group: '刻度线' }),
  f('ticks_left', 'bool', true, { group: '刻度线' }),
  f('ticks_right', 'bool', false, { group: '刻度线' }),
  f('grid_x', 'bool', false, { group: '网格与边框' }),
  f('grid_y', 'bool', false, { group: '网格与边框' }),
  f('grid_color', 'color', '#b0b0b0', { group: '网格与边框' }),
  f('spine_top', 'bool', true, { group: '网格与边框' }),
  f('spine_right', 'bool', true, { group: '网格与边框' }),
  f('spine_bottom', 'bool', true, { group: '网格与边框' }),
  f('spine_left', 'bool', true, { group: '网格与边框' }),
  f('spine_color', 'color', '#000000', { group: '网格与边框' }),
  f('spine_linewidth', 'number', 0.8, { min: 0.1, max: 3, step: 0.1, unit: 'pt', group: '网格与边框' }),
  ...SIDES.flatMap((s) => [col(s, '#000000'), lw(s, over.sideWidths?.[s] ?? 0.8)]),
  f('facecolor', 'color', '#ffffff', { group: '网格与边框' }),
]

const axesEl = (over: Parameters<typeof axesFields>[0] = {}): ManifestElement =>
  ({
    gid: 'axes_0',
    role: 'axes',
    label: '子图 1',
    bbox: [0.12, 0.11, 0.77, 0.77],
    draggable: true,
    resizable: true,
    editable: axesFields(over),
  }) as unknown as ManifestElement

const ticksFields = (): EditableField[] => [
  f('fontsize', 'number', 8.5, { min: 3, max: 24, step: 0.5, unit: 'pt' }),
  f('color', 'color', '#000000'),
  f('direction', 'enum', 'out', { options: ['out', 'in', 'inout'], group: '刻度线' }),
  f('length', 'number', 3.5, { min: 0, max: 12, step: 0.5, unit: 'pt', group: '刻度线' }),
  f('width', 'number', 0.8, { min: 0.1, max: 3, step: 0.1, unit: 'pt', group: '刻度线' }),
  f('minor_visible', 'bool', false, { group: '刻度定位' }),
]

const ticksEl = (axis: 'x' | 'y'): ManifestElement =>
  ({
    gid: `axes_0.${axis}ticks`,
    role: 'ticks',
    label: `${axis.toUpperCase()} 刻度文字`,
    bbox: axis === 'x' ? [0.12, 0.85, 0.77, 0.05] : [0.05, 0.11, 0.06, 0.77],
    draggable: false,
    editable: ticksFields(),
  }) as unknown as ManifestElement

const makeManifest = (over: Parameters<typeof axesFields>[0] = {}): Manifest =>
  ({
    rev: 1,
    size_mm: [101.6, 76.2],
    elements: [
      { gid: 'figure', role: 'figure', label: '整张图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
      axesEl(over),
      ticksEl('x'),
      ticksEl('y'),
    ],
  }) as unknown as Manifest

let manifest = makeManifest()

const panelOf = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 101.6,
    h: 76.2,
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 101.6,
    nativeH: 76.2,
    script: 'fig.py',
    overrides: [],
  }) as unknown as PanelObject

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}
const overrideOf = (gid: string, prop: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value

/* --------------------------------- 挂载 ---------------------------------- */

let root: Root
let host: HTMLDivElement

function Harness() {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return (
    <TooltipProvider>
      <ElementInspector panel={panel} />
    </TooltipProvider>
  )
}

function seedRender(m: Manifest) {
  manifest = m
  // 写入会触发渲染：mock 回的必须是这份 manifest，否则渲染回来换成另一份形状
  engineRender.mockResolvedValue({ rev: 2, manifest: m, svg: MATPLOTLIB_SVG, warnings: [] })
  useRenderStore.getState().patch(renderKeyOf(panelOf()), {
    fileId: 'Fig1.pdf',
    manifest: m,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': renderKeyOf(panelOf()) } })
}

async function mount(gid = 'axes_0') {
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: [gid] })
  host = document.createElement('div')
  document.body.appendChild(host)
  const svgHost = document.createElement('div')
  svgHost.setAttribute('data-element-svg', 'p1')
  svgHost.innerHTML = MATPLOTLIB_SVG
  document.body.appendChild(svgHost)
  root = createRoot(host)
  await act(async () => {
    root.render(<Harness />)
  })
}

const textOf = () => host.textContent ?? ''
const buttons = () => Array.from(host.querySelectorAll('button'))
const byAria = (name: string) => buttons().find((b) => b.getAttribute('aria-label') === name)
const byText = (text: string) => buttons().find((b) => b.textContent?.trim() === text)

async function typeNumber(input: HTMLInputElement, text: string) {
  await act(async () => {
    input.focus()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(input, text)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => {
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  })
}

beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_axes')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  seedRender(makeManifest())
  useDocumentStore.setState({ past: [], future: [] })
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  resetPreview()
  useUiStore.setState({ selectedGids: [] })
})

/* -------------------------------- 纵横比 ---------------------------------- */

describe('纵横比：三档控件，不是文字编辑器', () => {
  it('值的解析与还原：auto / equal / 数字串一一对应', () => {
    expect(aspectModeOf('auto')).toEqual({ mode: 'auto', ratio: null })
    expect(aspectModeOf('equal')).toEqual({ mode: 'equal', ratio: null })
    expect(aspectModeOf('1.5')).toEqual({ mode: 'custom', ratio: 1.5 })
    // 引擎 getter 给的是 str(round(float, 3))：还原回去仍是数字串
    expect(aspectValueOf('custom', 1.5)).toBe('1.5')
    expect(aspectValueOf('auto')).toBe('auto')
    expect(aspectValueOf('equal')).toBe('equal')
    // 认不出的值按「自动」画，不发明一个数字
    expect(aspectModeOf('weird')).toEqual({ mode: 'auto', ratio: null })
  })

  it('纵横比行里没有任何文字排版控件（textarea / 上下标 / 换行）', async () => {
    await mount()
    const row = host.querySelector('[data-prop="aspect"]')!
    expect(row).toBeTruthy()
    expect(row.querySelector('textarea')).toBeNull()
    expect(row.querySelector('[role="radiogroup"][aria-label="纵横比"]')).toBeTruthy()
    // 三档都在
    expect(row.textContent).toContain('自动')
    expect(row.textContent).toContain('等比例')
    expect(row.textContent).toContain('自定义比例')
  })

  it('换档写引擎能还原的值：等比例 = "equal"，自动 = "auto"，自定义从 1 起', async () => {
    await mount()
    await act(async () => {
      byText('等比例')!.click()
    })
    expect(overrideOf('axes_0', 'aspect')).toBe('equal')
    await act(async () => {
      byText('自定义比例')!.click()
    })
    expect(overrideOf('axes_0', 'aspect')).toBe('1')
    const input = host.querySelector('input[data-inspector-prop="aspect"]') as HTMLInputElement
    expect(input).toBeTruthy()
    await typeNumber(input, '1.5')
    expect(overrideOf('axes_0', 'aspect')).toBe('1.5')
    await act(async () => {
      byText('自动')!.click()
    })
    expect(overrideOf('axes_0', 'aspect')).toBe('auto')
    // 回到自动：数值框收起
    expect(host.querySelector('input[data-inspector-prop="aspect"]')).toBeNull()
  })

  it('脚本原样就是数字（1.25）时显示为自定义档并带数值', async () => {
    seedRender(makeManifest({ aspect: '1.25' }))
    await mount()
    const input = host.querySelector('input[data-inspector-prop="aspect"]') as HTMLInputElement
    expect(input).toBeTruthy()
    expect(input.value).toBe('1.25')
    const custom = byText('自定义比例')!
    expect(custom.getAttribute('aria-checked')).toBe('true')
  })
})

/* -------------------------------- 分段顺序 -------------------------------- */

describe('几何 → 范围与变换 → 刻度与网格 → 边框，四类任务互不混杂', () => {
  it('子图尺寸在页首的「几何」段，不再排在整页刻度设置之后（2026-09-13 审计 B53）', async () => {
    await mount()
    const size = host.querySelector('[data-axes-size-block]')!
    const rangeTransform = host.querySelector('[data-axes-section="range-transform"]')!
    expect(size).toBeTruthy()
    expect(size.textContent).toContain('子图尺寸')
    expect(size.compareDocumentPosition(rangeTransform) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // 只有一份：页尾不再另出一块
    expect(host.querySelectorAll('[data-axes-size-block]')).toHaveLength(1)
  })

  it('三段按顺序出现，各自只装自己的字段；范围与变换是一个组头（2026-09-12 起）', async () => {
    await mount()
    const rangeTransform = host.querySelector('[data-axes-section="range-transform"]')!
    const frame = host.querySelector('[data-spine-frame]')!
    const diagram = host.querySelector('[aria-label="刻度与边框状态图"]')!
    expect(rangeTransform && frame && diagram).toBeTruthy()
    // 以前是「范围」「坐标变换」两个组头（审计 T12）：子图页六块跨两屏，收成一块
    expect(host.querySelector('[data-axes-section="range"]')).toBeNull()
    expect(host.querySelector('[data-axes-section="transform"]')).toBeNull()
    expect(rangeTransform.querySelector('p')?.textContent).toBe('范围与变换')
    expect(rangeTransform.compareDocumentPosition(diagram) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(diagram.compareDocumentPosition(frame) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // 顺序不变：范围在前，缩放 / 反转 / 纵横比在后
    const props = Array.from(rangeTransform.querySelectorAll('[data-prop]')).map((n) =>
      n.getAttribute('data-prop'),
    )
    expect(props.indexOf('xlim')).toBeLessThan(props.indexOf('xscale'))
    expect(props.indexOf('xscale')).toBeLessThan(props.indexOf('invert_x'))
    expect(props.indexOf('invert_x')).toBeLessThan(props.indexOf('aspect'))
    // 这些字段没有在「更多」里再出现一遍
    expect(host.querySelectorAll('[data-prop="aspect"]')).toHaveLength(1)
    expect(host.querySelectorAll('[data-prop="spine_linewidth"]')).toHaveLength(1)
  })

  it('示意图的用法说明只给读屏（aria-describedby，不常驻）；「边框」总行不再画那个像复选框的方框', async () => {
    await mount()
    const caption = host.querySelector('[data-tick-diagram-caption]')!
    expect(caption.textContent).toBe('点四条边切换刻度线与边框')
    expect(caption.className).toContain('sr-only')
    expect(host.querySelector('[role="group"][aria-describedby]')?.getAttribute('aria-describedby')).toBe(caption.id)
    const frame = host.querySelector('[data-spine-frame]')!
    // 总行的字形位是个空占位（对齐用）：收起时整张卡里没有任何边位字形
    expect(frame.querySelector('[data-side-glyph]')).toBeNull()
    // 逐边行在「分别设置各边」里，展开后各有自己点亮的那条边
    await act(async () => {
      ;(host.querySelector('[data-spine-per-side]') as HTMLButtonElement).click()
    })
    const glyphs = frame.querySelectorAll('[data-side-glyph]')
    expect(Array.from(glyphs).map((g) => g.getAttribute('data-side-glyph'))).toEqual([
      'top',
      'right',
      'bottom',
      'left',
    ])
    for (const g of glyphs) expect(g.querySelector('path'), '每个字形都点亮一条边').toBeTruthy()
  })

  it('反转 X / Y 在同一行，各自写自己的字段', async () => {
    await mount()
    await act(async () => {
      byAria('反转 X 轴')!.click()
    })
    expect(overrideOf('axes_0', 'invert_x')).toBe(true)
    expect(overrideOf('axes_0', 'invert_y')).toBeUndefined()
    await act(async () => {
      byAria('反转 Y 轴')!.click()
    })
    expect(overrideOf('axes_0', 'invert_y')).toBe(true)
  })
})

/* ------------------- 2026-09-15 全面打磨（L2 / L11 / E1 / E2 / E3） ------------------ */

describe('子图页的版式（2026-09-15 全面打磨）', () => {
  it('几何字段的单字母标记进框内，与画布页 / 对象页同一种写法（L2）', async () => {
    await mount()
    const size = host.querySelector('[data-axes-size-block]')!
    const input = size.querySelector('input')!
    // 「框」= 挂着 border-input 的那一层；W 在框里，不再漂在框外
    const box = input.parentElement!
    expect(box.className).toContain('border-border-input')
    const marks = [...box.querySelectorAll('span')].map((el) => el.textContent?.trim())
    expect(marks).toContain('W')
    // 框外那一层不再放字母
    expect(box.parentElement!.firstElementChild).toBe(box)
  })

  it('范围那一对框平分控件列，不再在格子里左靠定宽（E1）', async () => {
    await mount()
    const pair = host.querySelector('[data-prop="xlim"]')!
    const boxes = [...pair.querySelectorAll('input')].map((i) => i.className)
    expect(boxes).toHaveLength(2)
    // `fill` 档：输入框撑满自己的格子（此前是 6ch 定宽 + 左靠）
    for (const cls of boxes) expect(cls).toContain('w-full')
  })

  it('「居中」是标签列后的两颗 ghost 命令，不是全页最重的两颗钮（E2）', async () => {
    await mount()
    const size = host.querySelector('[data-axes-size-block]') as HTMLElement
    const center = [...size.querySelectorAll('button')].filter((b) =>
      ['水平', '垂直'].includes(b.textContent?.trim() ?? ''),
    )
    expect(center, '找不到居中那两颗').toHaveLength(2)
    for (const b of center) {
      expect(b.className).not.toContain('flex-1')
      // ghost：没有 secondary 那副细边白底
      expect(b.className).toContain('hover:bg-surface-hover')
      expect(b.className).not.toContain('border border-border')
      // 可达名仍是完整的说法（「水平居中」），可见文字只是它的前半截
      expect(b.getAttribute('aria-label')).toContain(b.textContent!.trim())
    }
    // 行标签把「居中」说了一次，两颗钮不再各带一遍
    const label = [...size.querySelectorAll('span')].find((el) => el.textContent === '居中')
    expect(label, '居中没有自己的标签列').toBeTruthy()
  })

  it('整图尺寸并进「按比例缩放」行尾，不再单独占一行（E3）', async () => {
    await mount()
    const size = host.querySelector('[data-axes-size-block]')!
    const meta = [...size.querySelectorAll('span')].find((el) =>
      el.textContent?.startsWith('整图 '),
    )!
    expect(meta, '整图尺寸不见了').toBeTruthy()
    expect(meta.className).toContain('type-meta')
    // 与对象页「原始 80.0 × 57.6」同一个位置：缩放那一行的行尾
    const scaleApply = size.querySelector('[data-scale-apply]')!
    expect(meta.parentElement!.contains(scaleApply)).toBe(true)
  })

  it('边框收成一种行语法：没有列头，每一行都是标签列 + 色块 + 线宽（L11）', async () => {
    await mount()
    const frame = host.querySelector('[data-spine-frame]') as HTMLElement
    // 列头「颜色 / 线宽」删掉（色块与框内的 pt 已经自说明）
    const texts = [...frame.querySelectorAll('span')].map((el) => el.textContent?.trim())
    expect(texts).not.toContain('颜色')
    // 标签列与全页同宽
    const labelCol = frame.querySelector('span[style*="width"]') as HTMLElement
    expect(labelCol.style.width).toBe('88px')
    // 线宽回到 compact 档：不再是撑满一列的 142px 框
    const input = frame.querySelector('input[data-inspector-prop="spine_linewidth"]') as HTMLInputElement
    expect(input.className).not.toContain('w-full')
  })
})

/* -------------------------------- 边框联动 -------------------------------- */

describe('边框：默认四边联动，需要差异时再展开', () => {
  it('四边一致时逐边区收起、没有「多个值」；联动线宽写 spine_linewidth', async () => {
    await mount()
    const disclosure = host.querySelector('[data-spine-per-side]') as HTMLButtonElement
    expect(disclosure.getAttribute('aria-expanded')).toBe('false')
    expect(host.querySelector('[data-spine-mixed]')).toBeNull()
    expect(textOf()).not.toContain('各边不同')
    const input = host.querySelector('input[data-inspector-prop="spine_linewidth"]') as HTMLInputElement
    await typeNumber(input, '1.2')
    expect(overrideOf('axes_0', 'spine_linewidth')).toBe(1.2)
    // 没有偷偷写成四条逐边 override
    expect(overrideOf('axes_0', 'spine_top_linewidth')).toBeUndefined()
  })

  it('展开后逐边可改；改过一边之后联动行显示「多个值」且逐边区收不起来', async () => {
    await mount()
    const disclosure = host.querySelector('[data-spine-per-side]') as HTMLButtonElement
    await act(async () => {
      disclosure.click()
    })
    expect(disclosure.getAttribute('aria-expanded')).toBe('true')
    const top = host.querySelector('input[data-inspector-prop="spine_top_linewidth"]') as HTMLInputElement
    expect(top).toBeTruthy()
    await typeNumber(top, '2')
    expect(overrideOf('axes_0', 'spine_top_linewidth')).toBe(2)
    expect(host.querySelector('[data-spine-mixed="linewidth"]')).toBeTruthy()
    expect(textOf()).toContain('各边不同')
    const again = host.querySelector('[data-spine-per-side]') as HTMLButtonElement
    expect(again.disabled).toBe(true)
    expect(again.getAttribute('aria-expanded')).toBe('true')
  })

  it('有逐边覆盖时再写联动线宽：先清掉逐边覆盖，再写「全部」——四边真的一起变', async () => {
    await mount()
    await act(async () => {
      ;(host.querySelector('[data-spine-per-side]') as HTMLButtonElement).click()
    })
    await typeNumber(
      host.querySelector('input[data-inspector-prop="spine_top_linewidth"]') as HTMLInputElement,
      '2',
    )
    expect(overrideOf('axes_0', 'spine_top_linewidth')).toBe(2)
    await typeNumber(
      host.querySelector('input[data-inspector-prop="spine_linewidth"]') as HTMLInputElement,
      '1.5',
    )
    expect(overrideOf('axes_0', 'spine_linewidth')).toBe(1.5)
    // 引擎优先级是逐边 > 全部：留着那条逐边覆盖，上边就纹丝不动
    expect(overrideOf('axes_0', 'spine_top_linewidth')).toBeUndefined()
    expect(host.querySelector('[data-spine-mixed="linewidth"]')).toBeNull()
  })

  it('脚本本来就四边不同：一进来就展开并标明「各边不同」', async () => {
    seedRender(makeManifest({ sideWidths: { top: 0.8, right: 0.8, bottom: 1.6, left: 1.6 } }))
    await mount()
    const disclosure = host.querySelector('[data-spine-per-side]') as HTMLButtonElement
    expect(disclosure.getAttribute('aria-expanded')).toBe('true')
    expect(disclosure.disabled).toBe(true)
    expect(host.querySelector('[data-spine-mixed="linewidth"]')).toBeTruthy()
    expect(host.querySelector('input[data-inspector-prop="spine_bottom_linewidth"]')).toBeTruthy()
  })
})

/* -------------------------------- 按比例缩放 ------------------------------ */

describe('按比例缩放是一次性动作', () => {
  it('输入不生效，按「应用」才写 position，写完回到 100%', async () => {
    await mount()
    const apply = host.querySelector('[data-scale-apply]') as HTMLButtonElement
    expect(apply).toBeTruthy()
    expect(apply.disabled).toBe(true)
    const input = byAria('按比例缩放') as HTMLInputElement | undefined
    const numberInput = (host.querySelector('input[aria-label="按比例缩放"]') ??
      input) as HTMLInputElement
    expect(numberInput).toBeTruthy()
    await typeNumber(numberInput, '150')
    // 只是输入：文档一个字节没动
    expect(overrideOf('axes_0', 'position')).toBeUndefined()
    const before = useDocumentStore.getState().past.length
    const apply2 = host.querySelector('[data-scale-apply]') as HTMLButtonElement
    expect(apply2.disabled).toBe(false)
    await act(async () => {
      apply2.click()
    })
    const pos = overrideOf('axes_0', 'position') as number[]
    expect(pos).toBeTruthy()
    expect(pos[2]).toBeCloseTo(0.77 * 1.5, 3)
    expect(useDocumentStore.getState().past.length).toBe(before + 1)
    // 做完回到 100%，按钮重新置灰
    expect((host.querySelector('input[aria-label="按比例缩放"]') as HTMLInputElement).value).toBe('100')
    expect((host.querySelector('[data-scale-apply]') as HTMLButtonElement).disabled).toBe(true)
    // 那句「相对当前大小；应用后回到 100%」的解释不再需要
    expect(textOf()).not.toContain('应用后回到')
  })
})
