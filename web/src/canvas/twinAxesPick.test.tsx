/**
 * 重叠候选之间的**点击轮换**（issue #216）。
 *
 * 背景：twinx 的孪生轴与宿主是 `fig.axes` 里两个独立的 Axes，bbox **逐位相同**、
 * role 同为 `axes`，命中评分打平。旧 `pickElement` 只在「严格小于」时才换优胜者，
 * 于是先登记的宿主恒胜，twin 容器在画布上永远点不中——两个 bbox 之间没有任何
 * 空间信号可用，只能让用户说一句「换下一个」。
 *
 * 这份用例两边都钉：
 *   A. **不轮换时行为一个字节没变**——普通点击仍选宿主、曲线仍从容器手里抢回
 *      点击、隐藏 / 锁定仍不参与、边框命中区仍切刻度；
 *   B. **⌥ 点击能换到 twin**——而且换到的是谁**说得出口**（toast 用元素树那份
 *      措辞「子图 2（右轴）」），键盘经命令面板的同一条动作也走得通。
 *
 * jsdom 说明：`getBoundingClientRect` 恒为 0，这里给命中层桩一个与
 * layout × zoom 同口径的矩形（与 spineZones.test 同一套），断言的是选中了谁、
 * 写没写文档、toast 说了什么，不依赖 CSS 命中测试。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { ElementGeometry, Manifest, ManifestElement, SpineGeom, SpineSide } from '@/lib/api'
import { literal } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { PanelView } from './PanelView'
import {
  canCycleOverlapSelection,
  cycleElementAt,
  cycleOverlapSelection,
  pickElement,
  pickElementStack,
} from './interactions'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/* ------------------------------ 测试用 manifest ------------------------------ */

const f = (prop: string, value: unknown, extra: Record<string, unknown> = {}) =>
  ({ prop, type: typeof value === 'boolean' ? 'bool' : 'enum', value, ...extra }) as never

/** 宿主与孪生轴共用的边框几何：twinx 之后两者的四条边**逐位重合** */
const SPINES: Record<SpineSide, SpineGeom> = {
  bottom: { visible: true, ticks: true, from: [0.1, 0.9], to: [0.9, 0.9] },
  top: { visible: true, ticks: false, from: [0.1, 0.1], to: [0.9, 0.1] },
  left: { visible: true, ticks: true, from: [0.1, 0.9], to: [0.1, 0.1] },
  right: { visible: true, ticks: false, from: [0.9, 0.9], to: [0.9, 0.1] },
}

/** 绘图区容器：宿主与 twin 只有 gid / label 不同，bbox 与 role 完全一样 */
const axesBox = (gid: string, label: string): ManifestElement =>
  ({
    gid,
    role: 'axes',
    label,
    bbox: [0.1, 0.1, 0.8, 0.8],
    draggable: false,
    resizable: true,
    editable: [
      f('ticks_bottom', true),
      f('ticks_top', false),
      f('ticks_left', true),
      f('ticks_right', false),
    ],
    spines: SPINES,
  }) as unknown as ManifestElement

const ticksEl = (gid: string, label: string, bbox: number[]): ManifestElement =>
  ({
    gid,
    role: 'ticks',
    label,
    bbox,
    draggable: false,
    editable: [f('direction', 'out', { options: ['out', 'in', 'inout'] })],
  }) as unknown as ManifestElement

/** 对角曲线 y = x：只有那条线本身该命中，bbox 的空白角不算 */
const lineGeom: ElementGeometry = {
  kind: 'polyline',
  paths: [{ points: [[0.2, 0.2], [0.8, 0.8]], closed: false }],
  fill: false,
  stroke: true,
}

const manifest = (over: { hidden?: boolean } = {}): Manifest =>
  ({
    stem: 'Fig1',
    size_mm: [100, 80],
    elements: [
      { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
      // 左上角另有一个毫不相干的小子图：它不该混进重叠候选
      {
        gid: 'axes_0',
        role: 'axes',
        label: '子图 1',
        bbox: [0.01, 0.01, 0.06, 0.06],
        editable: [],
        draggable: false,
        resizable: true,
      },
      axesBox('axes_1', '子图 2'), // 宿主：登记在前
      axesBox('axes_2', '子图 2（右轴）'), // twinx 的孪生轴：bbox 与宿主逐位相同
      ticksEl('axes_1.xticks', 'x 刻度文字', [0.1, 0.93, 0.4, 0.05]),
      ticksEl('axes_1.yticks', 'y 刻度文字', [0.03, 0.1, 0.05, 0.8]),
      {
        gid: 'axes_1.lines_0',
        role: 'line',
        label: '曲线 “左轴数据”',
        bbox: [0.2, 0.2, 0.6, 0.6],
        geometry: lineGeom,
        editable: over.hidden ? [f('visible', false)] : [],
        draggable: false,
      },
      {
        gid: 'axes_1.texts_0',
        role: 'text',
        label: '文字 “注”',
        bbox: [0.6, 0.6, 0.2, 0.1],
        editable: [],
        draggable: true,
        anchor: [0.7, 0.65],
        drag_prop: 'pos_frac',
      },
    ],
  }) as unknown as Manifest

const panel = (over: Partial<PanelObject> = {}): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 100,
    h: 80,
    fileId: 'f1',
    fileKind: 'pdf',
    nativeW: 100,
    nativeH: 80,
    script: 'fig.py',
    overrides: [],
    ...over,
  }) as PanelObject

/**
 * 两个容器都盖着、离曲线与文字都远的一点。宿主与 twin 在这里评分逐位相同，
 * 正是旧实现「先登记者恒胜」的现场。
 */
const OVERLAP: [number, number] = [0.5, 0.3]
/** 对角曲线上的一点：内容元素仍该赢过两个容器 */
const ON_LINE: [number, number] = [0.5, 0.5]

/* --------------------------------- 挂载 ---------------------------------- */

let root: Root
let container: HTMLDivElement

const LAYOUT = { width: mmToWorld(100), height: mmToWorld(80) }

function Harness() {
  const p = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return <PanelView obj={p} />
}

const hitLayer = () => container.querySelector('[data-authority="ready"]') as HTMLDivElement

async function mount() {
  await act(async () => {
    root.render(<Harness />)
  })
  const layer = hitLayer()
  layer.getBoundingClientRect = () =>
    ({
      left: 0,
      top: 0,
      width: LAYOUT.width,
      height: LAYOUT.height,
      right: LAYOUT.width,
      bottom: LAYOUT.height,
      x: 0,
      y: 0,
      toJSON() {},
    }) as DOMRect
  return layer
}

const clientAt = (fx: number, fy: number) => ({
  clientX: fx * LAYOUT.width,
  clientY: fy * LAYOUT.height,
})

/**
 * 修饰键必须从**构造参数**进去：`altKey` / `shiftKey` 在 MouseEvent 上只有
 * getter，`Object.assign` 会当场抛（jsdom 与真浏览器同样如此）。
 */
async function press([fx, fy]: [number, number], init: MouseEventInit = {}) {
  const ev = new MouseEvent('pointerdown', {
    bubbles: true,
    cancelable: true,
    button: 0,
    ...clientAt(fx, fy),
    ...init,
  })
  Object.assign(ev, { pointerType: 'mouse', pointerId: 1 })
  await act(async () => {
    hitLayer().dispatchEvent(ev)
  })
  // 没被吃掉的按下会开始一次拖动（trackPointer 挂在 window 上）：松手
  await act(async () => {
    window.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, ...clientAt(fx, fy) }))
  })
}

const selected = () => useUiStore.getState().selectedGids
const livePanel = () => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}

beforeEach(async () => {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', elementPanelId: 'p1', selectedGids: [], status: null })
  useSelectionStore.getState().clear()
  useInteractionStore.getState().end()
  useRenderStore.getState().clear()
  useRenderStore.setState({ render: async () => {} })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_twin_pick')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panel())
  })
  seedExactRender(panel(), manifest())
  useDocumentStore.setState({ past: [], future: [] })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => {
    root.unmount()
  })
  container.remove()
})

/* ----------------------- A. 不轮换时行为一个字节没变 ----------------------- */

describe('不按 ⌥ 时的命中：与轮换之前逐位相同', () => {
  it('两个容器压在一起：普通点击仍然选中先登记的宿主', async () => {
    expect(pickElement(manifest(), ...OVERLAP)?.gid).toBe('axes_1')
    await mount()
    await press(OVERLAP)
    expect(selected()).toEqual(['axes_1'])
  })

  it('曲线仍然从容器手里把点击拿回来（内容优先于外壳）', () => {
    expect(pickElement(manifest(), ...ON_LINE)?.gid).toBe('axes_1.lines_0')
  })

  it('bbox 的空白角仍然落回容器，不选那条斜曲线', () => {
    expect(pickElement(manifest(), 0.75, 0.3)?.gid).toBe('axes_1')
  })

  it('不相干的小子图不进重叠候选', () => {
    expect(pickElementStack(manifest(), ...OVERLAP).map((e) => e.gid)).not.toContain('axes_0')
  })

  it('隐藏的元素不挡点击、也不进候选', () => {
    const m = manifest({ hidden: true })
    expect(pickElement(m, ...ON_LINE)?.gid).toBe('axes_1')
    expect(pickElementStack(m, ...ON_LINE).map((e) => e.gid)).not.toContain('axes_1.lines_0')
  })

  it('锁定的元素不进候选（只能从元素树选中）', () => {
    const stack = pickElementStack(manifest(), ...OVERLAP, ['axes_1'])
    expect(stack.map((e) => e.gid)).toEqual(['axes_2'])
  })

  it('边框命中区照常切刻度：普通点击写 override', async () => {
    await mount()
    await press([0.9 - 4 / LAYOUT.width, 0.5]) // 右边框内侧带
    expect(livePanel().overrides.length).toBeGreaterThan(0)
  })
})

/* --------------------------- B. ⌥ 点击能换到 twin --------------------------- */

describe('重叠候选表', () => {
  it('宿主与孪生轴评分逐位相同 ⇒ 在表里相邻，宿主在前', () => {
    expect(pickElementStack(manifest(), ...OVERLAP).map((e) => e.gid)).toEqual([
      'axes_1',
      'axes_2',
    ])
  })

  it('曲线上的一点：内容排第一，两个容器跟在后面', () => {
    expect(pickElementStack(manifest(), ...ON_LINE).map((e) => e.gid)).toEqual([
      'axes_1.lines_0',
      'axes_1',
      'axes_2',
    ])
  })

  it('cycleElementAt：从宿主走一格到孪生轴，再走一格绕回宿主', () => {
    const m = manifest()
    expect(cycleElementAt(m, ...OVERLAP, undefined, 'axes_1')).toMatchObject({
      index: 2,
      total: 2,
    })
    expect(cycleElementAt(m, ...OVERLAP, undefined, 'axes_1')?.el.gid).toBe('axes_2')
    expect(cycleElementAt(m, ...OVERLAP, undefined, 'axes_2')?.el.gid).toBe('axes_1')
  })

  it('当前选中的不在这堆里：第一下落在普通点击会选中的那一个上', () => {
    const m = manifest()
    expect(cycleElementAt(m, ...OVERLAP, undefined, null)?.el.gid).toBe('axes_1')
    expect(cycleElementAt(m, ...ON_LINE, undefined, 'axes_1.texts_0')?.el.gid).toBe(
      'axes_1.lines_0',
    )
  })
})

describe('⌥ 点击：画布上换得到孪生轴，并且说得出换到了谁', () => {
  it('第一下选宿主（= 普通点击的结果），第二下换到孪生轴', async () => {
    await mount()
    await press(OVERLAP, { altKey: true })
    expect(selected()).toEqual(['axes_1'])
    await press(OVERLAP, { altKey: true })
    expect(selected()).toEqual(['axes_2'])
  })

  it('toast 说出选中的是谁：措辞与元素树 / 属性页同一份', async () => {
    await mount()
    await press(OVERLAP, { altKey: true })
    await press(OVERLAP, { altKey: true })
    expect(useUiStore.getState().status).toEqual({
      key: 'status.elementCycled',
      ns: 'workspace',
      values: { label: '子图 2（右轴）', index: 2, total: 2 },
    })
  })

  it('再按一下绕回宿主（轮换，不是单程）', async () => {
    await mount()
    await press(OVERLAP, { altKey: true })
    await press(OVERLAP, { altKey: true })
    await press(OVERLAP, { altKey: true })
    expect(selected()).toEqual(['axes_1'])
  })

  it('换完之后普通点击仍然回到宿主：轮换是一次性的，不改变默认命中', async () => {
    await mount()
    await press(OVERLAP, { altKey: true })
    await press(OVERLAP, { altKey: true })
    expect(selected()).toEqual(['axes_2'])
    await press(OVERLAP)
    expect(selected()).toEqual(['axes_1'])
  })

  it('只换选中：不写文档、不进历史', async () => {
    await mount()
    await press(OVERLAP, { altKey: true })
    await press(OVERLAP, { altKey: true })
    expect(livePanel().overrides).toEqual([])
    expect(useDocumentStore.getState().past).toEqual([])
  })

  it('⌥ 落在边框命中带上时轮换优先：不切刻度、不写 override', async () => {
    await mount()
    await press([0.9 - 4 / LAYOUT.width, 0.5], { altKey: true })
    await press([0.9 - 4 / LAYOUT.width, 0.5], { altKey: true })
    expect(livePanel().overrides).toEqual([])
    expect(selected()).toEqual(['axes_2'])
  })

  it('⇧⌥ 仍然是加选，不轮换（两个修饰键各管一件事）', async () => {
    await mount()
    await press(ON_LINE)
    await press(OVERLAP, { altKey: true, shiftKey: true })
    expect(selected()).toEqual(['axes_1.lines_0', 'axes_1'])
  })
})

/* ------------------------------ C. 键盘等价路径 ------------------------------ */

describe('键盘：命令面板的同一条动作', () => {
  it('图内编辑态下选中宿主 ⇒ 命令可用，跑一次换到孪生轴', async () => {
    await mount()
    useUiStore.getState().setSelectedGid('axes_1')
    expect(canCycleOverlapSelection()).toBe(true)
    expect(cycleOverlapSelection()).toBe(true)
    expect(selected()).toEqual(['axes_2'])
    expect(useUiStore.getState().status).toMatchObject({
      key: 'status.elementCycled',
      values: { label: '子图 2（右轴）' },
    })
  })

  it('没进图内编辑态 / 没选中 / 选中的是整图：命令不出现', async () => {
    await mount()
    useUiStore.getState().setSelectedGid('axes_1')
    useUiStore.setState({ elementPanelId: null })
    expect(canCycleOverlapSelection()).toBe(false)
    useUiStore.setState({ elementPanelId: 'p1', selectedGids: [] })
    expect(canCycleOverlapSelection()).toBe(false)
    useUiStore.getState().setSelectedGid('figure')
    expect(canCycleOverlapSelection()).toBe(false)
  })

  it('几何权威没就位：什么都不动（ADR 0017），由调用方去说「正在同步」', async () => {
    await mount()
    useUiStore.getState().setSelectedGid('axes_1')
    useRenderStore.getState().clear()
    expect(cycleOverlapSelection()).toBe(false)
    expect(selected()).toEqual(['axes_1'])
  })
})
