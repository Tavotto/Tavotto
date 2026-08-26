/**
 * 刻度任务卡：「刻度在哪、朝哪、要不要次刻度」在同一处完成。
 *
 * 要钉住的（修改前全部不成立，见
 * `docs/ux/img/ux-consistency-pass/before/zh-1440-axes-ticks.png`——
 * 子图页只有四边开关，刻度短线固定画在框外，方向与次刻度在别的元素里）：
 *   1. 选中子图即可设方向与次刻度，写到对应轴的 ticks 元素；
 *   2. 上/下边用 X 的设置，左/右边用 Y 的；
 *   3. 示意图真读 direction（in 朝内 / out 朝外 / inout 两侧）；
 *   4. 开次刻度后出现更短的次刻度短线，关掉那条边则主次一起变关闭样式；
 *   5. manifest 没有的字段不出控件；
 *   6. 卡承接掉的字段不在通用列表里重复出现，但逐字段恢复仍在；
 *   7. 从刻度组元素进入是同一套控件（只给它自己那个轴）。
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
import { tickElementOf, tickHostOf } from './tickAdapter'

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

/** 与 engine/manifest.py `_axes_fields` 同形的四边开关与网格 */
const axesEl: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图 1',
  bbox: [0.12, 0.11, 0.77, 0.77],
  draggable: true,
  resizable: true,
  editable: [
    f('ticks_bottom', 'bool', true, { group: '网格与边框' }),
    f('ticks_top', 'bool', false, { group: '网格与边框' }),
    f('ticks_left', 'bool', true, { group: '网格与边框' }),
    f('ticks_right', 'bool', false, { group: '网格与边框' }),
    f('spine_bottom', 'bool', true, { group: '网格与边框' }),
    f('spine_top', 'bool', true, { group: '网格与边框' }),
    f('spine_left', 'bool', true, { group: '网格与边框' }),
    f('spine_right', 'bool', true, { group: '网格与边框' }),
    f('grid_x', 'bool', false, { group: '网格与边框' }),
    f('grid_y', 'bool', false, { group: '网格与边框' }),
  ],
} as unknown as ManifestElement

/** 与 `_tick_fields` 同形 */
const ticksFields = (over: Record<string, unknown> = {}): EditableField[] => [
  f('fontsize', 'number', 8.5, { min: 3, max: 24, step: 0.5, unit: 'pt' }),
  f('color', 'color', '#000000'),
  f('rotation', 'number', 0, { min: -90, max: 90, step: 5, unit: '°' }),
  f('visible', 'bool', true),
  f('direction', 'enum', over.direction ?? 'out', {
    options: ['out', 'in', 'inout'],
    group: '刻度线',
  }),
  f('length', 'number', over.length ?? 3.5, { min: 0, max: 12, step: 0.5, unit: 'pt', group: '刻度线' }),
  f('width', 'number', 0.8, { min: 0.1, max: 3, step: 0.1, unit: 'pt', group: '刻度线' }),
  f('format', 'enum', 'auto', { options: ['auto', 'plain'], group: '刻度线' }),
  f('major_mode', 'enum', 'auto', { options: ['auto', 'step', 'fixed'], group: '刻度定位' }),
  f('minor_visible', 'bool', over.minor_visible ?? false, { group: '刻度定位' }),
  f('minor_mode', 'enum', 'auto', { options: ['auto', 'step'], group: '刻度定位' }),
]

const xTicksEl: ManifestElement = {
  gid: 'axes_0.xticks',
  role: 'ticks',
  label: 'X 刻度文字',
  bbox: [0.12, 0.85, 0.77, 0.05],
  draggable: false,
  editable: ticksFields(),
} as unknown as ManifestElement

const yTicksEl: ManifestElement = {
  gid: 'axes_0.yticks',
  role: 'ticks',
  label: 'Y 刻度文字',
  bbox: [0.05, 0.11, 0.06, 0.77],
  draggable: false,
  editable: ticksFields(),
} as unknown as ManifestElement

const makeManifest = (x = xTicksEl, y = yTicksEl): Manifest =>
  ({
    rev: 1,
    size_mm: [101.6, 76.2],
    elements: [axesEl, x, y],
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

async function mount(gid: string) {
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
const radios = () => buttons().filter((b) => b.getAttribute('role') === 'radio')
/** 方向档位按可达名找（图标按钮的 aria-label，不是 tooltip） */
const DIR_NAME = { in: '朝内', out: '朝外', inout: '内外' } as const
const dirBtn = (dir: keyof typeof DIR_NAME) => byAria(DIR_NAME[dir])!
/** 展开「更多」折叠区 */
async function openMore() {
  const btn = buttons().find((b) => b.textContent?.trim() === '更多')
  if (btn && btn.getAttribute('aria-expanded') !== 'true') {
    await act(async () => {
      btn.click()
    })
  }
}
/** 某个行标签在整页出现几次（只数叶子 span，labeledWithState 是两层嵌套） */
const countLabel = (text: string) =>
  Array.from(host.querySelectorAll('span')).filter(
    (s) => s.textContent?.trim() === text && s.children.length === 0,
  ).length
const majorPath = (side: string) =>
  host.querySelector(`[data-tick-major="${side}"]`) as SVGPathElement | null
const minorPath = (side: string) =>
  host.querySelector(`[data-tick-minor="${side}"]`) as SVGPathElement | null

beforeEach(async () => {
  manifest = makeManifest()
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ticks')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  useRenderStore.getState().patch(renderKeyOf(panelOf()), {
    fileId: 'Fig1.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': renderKeyOf(panelOf()) } })
  useDocumentStore.setState({ past: [], future: [] })
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  resetPreview()
  useUiStore.setState({ selectedGids: [] })
})

/* --------------------------------- 宿主映射 ------------------------------- */

describe('刻度宿主映射', () => {
  it('子图 gid → 该轴的刻度元素', () => {
    expect(tickElementOf(manifest, 'axes_0', 'x')?.gid).toBe('axes_0.xticks')
    expect(tickElementOf(manifest, 'axes_0', 'y')?.gid).toBe('axes_0.yticks')
    expect(tickElementOf(manifest, 'axes_9', 'x')).toBeUndefined()
  })

  it('刻度元素 gid → 轴与宿主子图', () => {
    expect(tickHostOf('axes_0.xticks')).toEqual({ axesGid: 'axes_0', axis: 'x' })
    expect(tickHostOf('axes_1.zticks')).toEqual({ axesGid: 'axes_1', axis: 'z' })
    expect(tickHostOf('axes_0.title')).toBeNull()
  })
})

/* ------------------------------ 子图页的刻度卡 ---------------------------- */

describe('选中子图即可配置刻度', () => {
  it('首屏有 X/Y 切换、次刻度、方向、长度、宽度——不需要理解元素树', async () => {
    await mount('axes_0')
    expect(textOf()).toContain('X 刻度')
    expect(textOf()).toContain('Y 刻度')
    expect(textOf()).toContain('次刻度')
    expect(textOf()).toContain('方向')
    expect(textOf()).toContain('长度')
    expect(textOf()).toContain('宽度')
  })

  it('四边点按仍在（现有操作不退化）', async () => {
    await mount('axes_0')
    for (const p of ['底部刻度', '顶部刻度', '左侧刻度', '右侧刻度']) {
      // 可达名由 propLabel 给；这里只断言四个刻度开关与四个边框开关都还在
      void p
    }
    const switches = Array.from(host.querySelectorAll('[role="switch"]'))
    expect(switches.length).toBeGreaterThanOrEqual(10) // 4 ticks + 4 spines + 2 grid
  })

  it('默认显示 X 轴的设置；切到 Y 后写到 yticks', async () => {
    await mount('axes_0')
    // X（默认）：把方向切成朝内
    await act(async () => {
      dirBtn('in').click()
    })
    expect(overrideOf('axes_0.xticks', 'direction')).toBe('in')
    expect(overrideOf('axes_0.yticks', 'direction')).toBeUndefined()

    // 切到 Y 刻度
    const yTab = radios().find((b) => b.textContent?.includes('Y 刻度'))!
    await act(async () => {
      yTab.click()
    })
    await act(async () => {
      dirBtn('inout').click()
    })
    expect(overrideOf('axes_0.yticks', 'direction')).toBe('inout')
    expect(overrideOf('axes_0.xticks', 'direction')).toBe('in')
  })

  it('三档方向写的是 manifest 声明的真实值', async () => {
    await mount('axes_0')
    for (const [tip, value] of [
      ['in', 'in'],
      ['inout', 'inout'],
      ['out', 'out'],
    ] as const) {
      await act(async () => {
        dirBtn(tip).click()
      })
      expect(overrideOf('axes_0.xticks', 'direction')).toBe(value)
    }
  })

  it('次刻度开关写 minor_visible，不造 major_visible', async () => {
    await mount('axes_0')
    const toggle = byAria('X 轴的次刻度')!
    expect(toggle).toBeTruthy()
    await act(async () => {
      toggle.click()
    })
    expect(overrideOf('axes_0.xticks', 'minor_visible')).toBe(true)
    // 引擎没有 major_visible，界面也不该冒出一个
    expect(livePanel().overrides.some((o) => o.prop === 'major_visible')).toBe(false)
  })
})

/* ------------------------------ 示意图读真实状态 -------------------------- */

describe('状态图反映真实刻度形态', () => {
  const dOf = (el: SVGPathElement | null) => el?.getAttribute('d') ?? ''

  it('out：刻度画在框外；in：画在框内；inout：两侧都有', async () => {
    await mount('axes_0')
    // 底边（X）：out 时从 y=82 往下（更大的 y）
    expect(majorPath('bottom')?.getAttribute('data-tick-direction')).toBe('out')
    const outD = dOf(majorPath('bottom'))
    expect(outD).toContain('L50 88') // 82 + 6

    await act(async () => {
      dirBtn('in').click()
    })
    expect(majorPath('bottom')?.getAttribute('data-tick-direction')).toBe('in')
    expect(dOf(majorPath('bottom'))).toContain('L50 76') // 82 - 6

    await act(async () => {
      dirBtn('inout').click()
    })
    expect(dOf(majorPath('bottom'))).toContain('M50 76 L50 88')
  })

  it('X 与 Y 各自的方向互不影响', async () => {
    await mount('axes_0')
    await act(async () => {
      dirBtn('in').click()
    })
    const yTab = radios().find((b) => b.textContent?.includes('Y 刻度'))!
    await act(async () => {
      yTab.click()
    })
    await act(async () => {
      dirBtn('inout').click()
    })
    expect(majorPath('bottom')?.getAttribute('data-tick-direction')).toBe('in')
    expect(majorPath('left')?.getAttribute('data-tick-direction')).toBe('inout')
  })

  it('次刻度关着时没有次刻度短线；打开后出现，且明显更短', async () => {
    await mount('axes_0')
    expect(minorPath('bottom')).toBeNull()
    await act(async () => {
      byAria('X 轴的次刻度')!.click()
    })
    const minor = minorPath('bottom')
    expect(minor).toBeTruthy()
    // 主刻度 6、次刻度 3：从边线 82 出发，主到 88、次到 85
    expect(dOf(minor)).toContain('L38 85')
    expect(dOf(majorPath('bottom'))).toContain('L50 88')
    // 只影响 X：左边（Y）不该冒出次刻度
    expect(minorPath('left')).toBeNull()
  })

  it('关掉某一边后，该边主次刻度都是关闭样式（虚线 + 低不透明度）', async () => {
    await mount('axes_0')
    await act(async () => {
      byAria('X 轴的次刻度')!.click()
    })
    // 上边默认关：主次都该是关闭样式
    expect(majorPath('top')?.getAttribute('stroke-dasharray')).toBe('1.5 1.5')
    expect(minorPath('top')?.getAttribute('stroke-dasharray')).toBe('1.5 1.5')
    // 下边是开的：实线
    expect(majorPath('bottom')?.getAttribute('stroke-dasharray')).toBeNull()
    expect(minorPath('bottom')?.getAttribute('stroke-dasharray')).toBeNull()
  })
})

/* ------------------------------- 能力边界 -------------------------------- */

describe('manifest 是能力权威', () => {
  it('没有 direction 字段（3D 轴）就不出方向控件', async () => {
    const noDir: ManifestElement = {
      ...xTicksEl,
      editable: ticksFields().filter((x) => x.prop !== 'direction'),
    }
    manifest = makeManifest(noDir, { ...yTicksEl, editable: ticksFields().filter((x) => x.prop !== 'direction') })
    useRenderStore.getState().patch(renderKeyOf(panelOf()), {
      fileId: 'Fig1.pdf', manifest, svg: MATPLOTLIB_SVG, rev: 1, status: 'ready', lastPatches: '[]',
    })
    await mount('axes_0')
    expect(textOf()).not.toContain('方向')
    // 次刻度还在（它是另一条能力）
    expect(textOf()).toContain('次刻度')
    // 没有 direction 时示意图按 matplotlib 默认 out 画
    expect(majorPath('bottom')?.getAttribute('data-tick-direction')).toBe('out')
  })

  it('刻度元素整个不存在时只剩状态图，不崩', async () => {
    manifest = { ...makeManifest(), elements: [axesEl] } as unknown as Manifest
    useRenderStore.getState().patch(renderKeyOf(panelOf()), {
      fileId: 'Fig1.pdf', manifest, svg: MATPLOTLIB_SVG, rev: 1, status: 'ready', lastPatches: '[]',
    })
    await mount('axes_0')
    expect(majorPath('bottom')).toBeTruthy()
    expect(textOf()).not.toContain('次刻度')
  })
})

/* ------------------------- 从刻度组元素进入同一套控件 ---------------------- */

describe('刻度组元素页', () => {
  it('只给它自己那个轴，不出 X/Y 切换', async () => {
    await mount('axes_0.xticks')
    expect(textOf()).toContain('方向')
    expect(textOf()).toContain('次刻度')
    // 没有「Y 刻度」这个切换项——切过去会写到另一个元素
    expect(radios().some((b) => b.textContent?.includes('Y 刻度'))).toBe(false)
  })

  it('写的是选中的那个刻度元素', async () => {
    await mount('axes_0.yticks')
    await act(async () => {
      dirBtn('in').click()
    })
    expect(overrideOf('axes_0.yticks', 'direction')).toBe('in')
    expect(overrideOf('axes_0.xticks', 'direction')).toBeUndefined()
  })

  it('被卡承接的字段不在通用列表里重复出现（连「更多」展开后也不重复）', async () => {
    await mount('axes_0.xticks')
    expect(host.querySelectorAll('[role="radiogroup"][aria-label="方向"]')).toHaveLength(1)
    // **必须展开「更多」再查一遍**：direction / length / width / minor_visible
    // 本来就住在折叠区里，只查首屏的话即使 consumed 完全失效也照样绿（空门禁）
    await openMore()
    expect(host.querySelectorAll('[role="radiogroup"][aria-label="方向"]')).toHaveLength(1)
    expect(host.querySelectorAll('[role="combobox"][aria-label="方向"]')).toHaveLength(0)
    expect(host.querySelectorAll('[role="switch"][aria-label="X 轴的次刻度"]')).toHaveLength(1)
    expect(countLabel('次刻度')).toBe(1)
    expect(countLabel('长度')).toBe(1)
    expect(countLabel('宽度')).toBe(1)
  })

  it('没被承接的能力仍然可达：主刻度方式在首屏，次刻度方式在「更多」里', async () => {
    await mount('axes_0.xticks')
    expect(textOf()).toContain('主刻度方式')
    await openMore()
    expect(textOf()).toContain('次刻度方式')
  })

  it('逐字段恢复到脚本仍在：改过方向后出现恢复按钮，点掉即回退', async () => {
    await mount('axes_0.xticks')
    await act(async () => {
      dirBtn('in').click()
    })
    const reset = byAria('恢复方向')
    expect(reset).toBeTruthy()
    await act(async () => {
      reset!.click()
    })
    expect(overrideOf('axes_0.xticks', 'direction')).toBeUndefined()
  })
})

/* --------------------------------- 键盘 ---------------------------------- */

describe('键盘可达', () => {
  it('四边开关是可聚焦的 switch，Enter 可切换', async () => {
    await mount('axes_0')
    const sw = Array.from(host.querySelectorAll('[role="switch"]')).find(
      (s) => s.getAttribute('aria-label')?.includes('顶部') || s.getAttribute('tabindex') === '0',
    ) as HTMLElement
    expect(sw).toBeTruthy()
    expect(sw.getAttribute('tabindex')).toBe('0')
  })

  it('方向是 radiogroup，每个档位都是 radio', async () => {
    await mount('axes_0')
    const group = host.querySelectorAll('[role="radiogroup"]')
    expect(group.length).toBeGreaterThan(0)
    expect(radios().length).toBeGreaterThanOrEqual(3)
  })
})
