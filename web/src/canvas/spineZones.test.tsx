/**
 * 画布上的坐标轴边框命中区（Prompt 16）：`ElementHitLayer` 把指针落在边框内 /
 * 外侧的动作接成「切这一边的向内 / 向外刻度」。
 *
 * 要钉住的：
 *   1. 悬停出现高亮条 + 状态文字（哪边 · 向内 / 向外 · 开着 / 关着 · 点击会怎样），
 *      离开即消失，不常驻遮挡；
 *   2. 点击 = 一次 commit（方向 + 显隐同一条历史），选中落到那条边的子图；
 *   3. 中线（neutral）不切刻度，只选中；
 *   4. 文字 / 刻度文字优先——落在它们 bbox 里的点不给边框命中区；
 *   5. 带宽按屏幕像素稳定（zoom）；触控带更宽；
 *   6. 旋转过的面板：指针 → 内容分数的反旋转仍然正确；
 *   7. 偏出去的边框（pickElement 命中 figure）照样可点。
 *
 * jsdom 说明：`getBoundingClientRect` 恒为 0，这里给命中层桩一个与
 * layout × zoom 同口径的矩形；断言的是结构与写入，不依赖 CSS 命中测试。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { Manifest, ManifestElement } from '@/lib/api'
import { literal } from '@/i18n'
import { ZONE_PX } from '@/lib/tickSides'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, rotateVec, type PanelObject, type PanelRotation } from '@/types/document'
import { PanelView } from './PanelView'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/* ------------------------------ 测试用 manifest ------------------------------ */

const f = (prop: string, value: unknown, extra: Record<string, unknown> = {}) =>
  ({ prop, type: typeof value === 'boolean' ? 'bool' : 'enum', value, ...extra }) as never

const axesEl = (spines: ManifestElement['spines']): ManifestElement =>
  ({
    gid: 'axes_0',
    role: 'axes',
    label: '子图',
    bbox: [0.1, 0.1, 0.8, 0.8],
    draggable: false,
    resizable: true,
    editable: [
      f('ticks_bottom', true),
      f('ticks_top', false),
      f('ticks_left', true),
      f('ticks_right', false),
    ],
    spines,
  }) as unknown as ManifestElement

const SPINES: NonNullable<ManifestElement['spines']> = {
  bottom: { visible: true, ticks: true, from: [0.1, 0.9], to: [0.9, 0.9] },
  top: { visible: true, ticks: false, from: [0.1, 0.1], to: [0.9, 0.1] },
  left: { visible: true, ticks: true, from: [0.1, 0.9], to: [0.1, 0.1] },
  right: { visible: true, ticks: false, from: [0.9, 0.9], to: [0.9, 0.1] },
}

const ticksEl = (axis: 'x' | 'y', bbox: number[]): ManifestElement =>
  ({
    gid: `axes_0.${axis}ticks`,
    role: 'ticks',
    label: `${axis} 刻度文字`,
    bbox,
    draggable: false,
    editable: [f('direction', 'out', { options: ['out', 'in', 'inout'] })],
  }) as unknown as ManifestElement

/** 一段文字，压在下边框外侧命中带的右半段上 */
const textEl: ManifestElement = {
  gid: 'axes_0.texts_0',
  role: 'text',
  label: '文字',
  bbox: [0.6, 0.895, 0.2, 0.06],
  editable: [],
  draggable: true,
  anchor: [0.7, 0.92],
  drag_prop: 'pos_frac',
}

const makeManifest = (
  opts: {
    /** null = 引擎没给（极坐标 / 3D） */
    spines?: ManifestElement['spines'] | null
    yTicksBbox?: number[]
    extra?: ManifestElement[]
  } = {},
): Manifest =>
  ({
    size_mm: [100, 80],
    elements: [
      { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
      axesEl(opts.spines === null ? undefined : (opts.spines ?? SPINES)),
      // X 刻度文字只占下边框外侧的左半段（0.1..0.5），右半段留给文字元素；
      // 它从 0.93 起，离线 ≈ 9 屏幕像素——外侧带（2.5..10px）的大部分仍在它之外
      ticksEl('x', [0.1, 0.93, 0.4, 0.05]),
      ticksEl('y', opts.yTicksBbox ?? [0.03, 0.1, 0.05, 0.8]),
      textEl,
      ...(opts.extra ?? []),
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

const livePanel = () => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}
const overrideOf = (gid: string, prop: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value

/* --------------------------------- 挂载 ---------------------------------- */

let root: Root
let container: HTMLDivElement
let zoom = 1
let rot: PanelRotation = 0

const LAYOUT = { width: mmToWorld(100), height: mmToWorld(80) }

function Harness() {
  const p = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return <PanelView obj={p} />
}

async function mount() {
  await act(async () => {
    root.render(<Harness />)
  })
  const layer = hitLayer()
  // 命中层在屏幕上的矩形：内容 × zoom；旋转 90/270 时外框长宽互换
  const swap = rot === 90 || rot === 270
  const w = LAYOUT.width * zoom
  const h = LAYOUT.height * zoom
  layer.getBoundingClientRect = () =>
    ({
      left: 0,
      top: 0,
      width: swap ? h : w,
      height: swap ? w : h,
      right: swap ? h : w,
      bottom: swap ? w : h,
      x: 0,
      y: 0,
      toJSON() {},
    }) as DOMRect
  return layer
}

const hitLayer = () => container.querySelector('[data-authority="ready"]') as HTMLDivElement

/** 内容分数坐标 → 屏幕点（经旋转），与 ElementHitLayer.frac 互逆 */
function clientAt(fx: number, fy: number): { clientX: number; clientY: number } {
  const w = LAYOUT.width * zoom
  const h = LAYOUT.height * zoom
  const swap = rot === 90 || rot === 270
  const cx = (swap ? h : w) / 2
  const cy = (swap ? w : h) / 2
  const [u, v] = rotateVec((fx - 0.5) * w, (fy - 0.5) * h, rot)
  return { clientX: cx + u, clientY: cy + v }
}

/** 离某条边 n 个屏幕像素的内容分数偏移 */
const dy = (px: number) => px / (LAYOUT.height * zoom)
const dx = (px: number) => px / (LAYOUT.width * zoom)

const pointer = (type: string, fx: number, fy: number, init: Record<string, unknown> = {}) => {
  const ev = new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, ...clientAt(fx, fy) })
  Object.assign(ev, { pointerType: 'mouse', pointerId: 1, ...init })
  return ev
}

async function hover(fx: number, fy: number, init: Record<string, unknown> = {}) {
  await act(async () => {
    hitLayer().dispatchEvent(pointer('pointermove', fx, fy, init))
  })
}
async function press(fx: number, fy: number, init: Record<string, unknown> = {}) {
  await act(async () => {
    hitLayer().dispatchEvent(pointer('pointerdown', fx, fy, init))
  })
  // 没落进命中带的按下会开始一次拖动（trackPointer 挂在 window 上）：松手
  await act(async () => {
    window.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, ...clientAt(fx, fy) }))
  })
}

const strip = () => container.querySelector('[data-spine-zone]')
const strips = () => Array.from(container.querySelectorAll('[data-spine-zone]'))
const label = () => container.querySelector('[data-spine-zone-label]')

beforeEach(async () => {
  zoom = 1
  rot = 0
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', elementPanelId: 'p1', selectedGids: [] })
  useSelectionStore.getState().clear()
  useInteractionStore.getState().end()
  useRenderStore.getState().clear()
  useRenderStore.setState({ render: async () => {} })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_spine_zones')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panel())
  })
  seedExactRender(panel(), makeManifest())
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

/* --------------------------------- 用例 ---------------------------------- */

describe('悬停反馈', () => {
  it('落在下边框里那一带：高亮条 + 状态文字（下边 · 朝内 · 关着 · 点击显示）', async () => {
    await mount()
    expect(strip()).toBeNull()
    await hover(0.3, 0.9 - dy(5))
    const s = strip()!
    expect(s.getAttribute('data-spine-zone')).toBe('bottom')
    expect(s.getAttribute('data-spine-zone-kind')).toBe('inner')
    expect(s.getAttribute('data-spine-zone-strong')).toBe('true')
    const text = label()!.textContent ?? ''
    expect(text).toContain('下边')
    expect(text).toContain('朝内')
    expect(text).toContain('关着')
    expect(text).toContain('点击显示')
    expect(hitLayer().style.cursor).toBe('pointer')
    // 高亮条与命中带同一把尺：厚度 = band - neutral 屏幕像素（本层 1px = 1 世界像素 / zoom）
    expect(parseFloat((s as HTMLElement).style.height)).toBeCloseTo(ZONE_PX.band - ZONE_PX.neutral, 5)
  })

  it('外侧那一带说的是向外刻度，且它此刻开着', async () => {
    await mount()
    await hover(0.3, 0.9 + dy(5))
    expect(strip()?.getAttribute('data-spine-zone-kind')).toBe('outer')
    const text = label()!.textContent ?? ''
    expect(text).toContain('朝外')
    expect(text).toContain('开着')
  })

  it('离开命中带 / 离开面板即消失，不常驻', async () => {
    await mount()
    await hover(0.3, 0.9 - dy(5))
    expect(strip()).not.toBeNull()
    await hover(0.5, 0.5)
    expect(strip()).toBeNull()
    expect(hitLayer().style.cursor).not.toBe('pointer')
    await hover(0.3, 0.9 - dy(5))
    await act(async () => {
      // React 的 onPointerLeave 由 pointerout（relatedTarget 在层外）合成
      hitLayer().dispatchEvent(new MouseEvent('pointerout', { bubbles: true, relatedTarget: null }))
    })
    expect(strip()).toBeNull()
  })

  it('方向是整条轴的：另一边可见时连带的那条边也浅色一起亮，文字点名', async () => {
    useDocumentStore.getState().commit(literal('开上边'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1') as PanelObject
      p.overrides.push({ gid: 'axes_0', prop: 'ticks_top', value: true })
    })
    seedExactRender(livePanel(), makeManifest({ spines: { ...SPINES, top: { ...SPINES.top, ticks: true } } }))
    await mount()
    await hover(0.3, 0.9 - dy(5))
    const all = strips()
    expect(all.map((s) => s.getAttribute('data-spine-zone')).sort()).toEqual(['bottom', 'top'])
    const top = all.find((s) => s.getAttribute('data-spine-zone') === 'top')!
    expect(top.getAttribute('data-spine-zone-strong')).toBe('false')
    expect(label()!.textContent).toContain('上边')
  })

  it('中线（neutral）不出高亮条、光标不是 pointer', async () => {
    await mount()
    await hover(0.3, 0.9)
    expect(strip()).toBeNull()
    expect(hitLayer().style.cursor).not.toBe('pointer')
  })
})

describe('点击即切', () => {
  it('点框里那一带 = 加向内（inout），一条历史，选中落到子图', async () => {
    await mount()
    const before = useDocumentStore.getState().past.length
    await press(0.3, 0.9 - dy(5))
    expect(overrideOf('axes_0.xticks', 'direction')).toBe('inout')
    expect(overrideOf('axes_0', 'ticks_bottom')).toBeUndefined()
    expect(useDocumentStore.getState().past.length).toBe(before + 1)
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0'])
  })

  it('隐藏的上边：点框外那一带 = 打开这一边（方向已是朝外，不动）', async () => {
    await mount()
    await press(0.3, 0.1 - dy(5))
    expect(overrideOf('axes_0', 'ticks_top')).toBe(true)
    expect(overrideOf('axes_0.xticks', 'direction')).toBeUndefined()
  })

  it('隐藏的上边：点框里那一带 = 打开 + 方向 inout，两条 override 同一次 commit，撤销一起回', async () => {
    await mount()
    const before = useDocumentStore.getState().past.length
    await press(0.3, 0.1 + dy(5))
    expect(overrideOf('axes_0', 'ticks_top')).toBe(true)
    expect(overrideOf('axes_0.xticks', 'direction')).toBe('inout')
    expect(useDocumentStore.getState().past.length).toBe(before + 1)
    await act(async () => {
      useDocumentStore.getState().undo()
    })
    expect(overrideOf('axes_0', 'ticks_top')).toBeUndefined()
    expect(overrideOf('axes_0.xticks', 'direction')).toBeUndefined()
  })

  it('左边框：Y 轴的方向，与 X 互不牵连', async () => {
    await mount()
    await press(0.1 + dx(5), 0.5)
    expect(overrideOf('axes_0.yticks', 'direction')).toBe('inout')
    expect(overrideOf('axes_0.xticks', 'direction')).toBeUndefined()
  })

  it('已选着它的刻度组时不改选区', async () => {
    useUiStore.setState({ selectedGids: ['axes_0.xticks'] })
    await mount()
    await press(0.3, 0.9 - dy(5))
    expect(overrideOf('axes_0.xticks', 'direction')).toBe('inout')
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.xticks'])
  })

  it('中线：不写任何 override，只选中子图', async () => {
    await mount()
    await press(0.3, 0.9)
    expect(livePanel().overrides).toEqual([])
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0'])
  })
})

describe('优先级', () => {
  it('文字压在外侧命中带上：那一段不给边框命中（文字优先）', async () => {
    await mount()
    // 文字 bbox 覆盖 x 0.6..0.8、y 0.895..0.955 —— 下边框外侧带的右半段
    await hover(0.7, 0.9 + dy(5))
    expect(strip()).toBeNull()
    await press(0.7, 0.9 + dy(5))
    expect(livePanel().overrides).toEqual([])
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.texts_0'])
  })

  it('刻度文字压在外侧命中带上：同样让路', async () => {
    await mount()
    // X 刻度文字 bbox：x 0.1..0.5、y 0.93..0.98；外侧带 y 在 0.9 + 2.5px..10px（≈0.908..0.933）
    await hover(0.3, 0.932)
    expect(strip()).toBeNull()
  })

  it('外侧带上没被文字盖住的那段照样可点', async () => {
    await mount()
    await hover(0.55, 0.9 + dy(5))
    expect(strip()?.getAttribute('data-spine-zone-kind')).toBe('outer')
  })
})

describe('几何：zoom / 触控 / 旋转 / 偏出去的边框', () => {
  it.each([0.5, 3])('zoom=%f：离线 5 屏幕像素仍在带里、20 像素在带外', async (z) => {
    zoom = z
    useViewportStore.setState({ zoom: z })
    await mount()
    await hover(0.3, 0.9 - dy(5))
    expect(strip()?.getAttribute('data-spine-zone-kind')).toBe('inner')
    await hover(0.3, 0.9 - dy(20))
    expect(strip()).toBeNull()
  })

  it('触控：14 像素对鼠标在带外、对手指在带里', async () => {
    await mount()
    await hover(0.3, 0.9 - dy(14))
    expect(strip()).toBeNull()
    await hover(0.3, 0.9 - dy(14), { pointerType: 'touch' })
    expect(strip()?.getAttribute('data-spine-zone-kind')).toBe('inner')
    await press(0.3, 0.9 - dy(14), { pointerType: 'touch' })
    expect(overrideOf('axes_0.xticks', 'direction')).toBe('inout')
  })

  it.each([90, 180, 270] as PanelRotation[])('旋转 %d°：指针反旋转后仍落在下边框里那一带', async (r) => {
    rot = r
    useDocumentStore.getState().commit(literal('旋转'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1') as PanelObject
      ;(p as PanelObject & { rotation?: PanelRotation }).rotation = r
      if (r === 90 || r === 270) {
        p.w = 80
        p.h = 100
      }
    })
    seedExactRender(livePanel(), makeManifest())
    await mount()
    await hover(0.3, 0.9 - dy(5))
    expect(strip()?.getAttribute('data-spine-zone')).toBe('bottom')
    expect(strip()?.getAttribute('data-spine-zone-kind')).toBe('inner')
    await press(0.3, 0.9 - dy(5))
    expect(overrideOf('axes_0.xticks', 'direction')).toBe('inout')
  })

  it('偏出去的左边框（pickElement 命中 figure）：命中区跟着线走', async () => {
    const spines = { ...SPINES, left: { ...SPINES.left, from: [0.04, 0.9] as [number, number], to: [0.04, 0.1] as [number, number] } }
    // 刻度文字跟着偏出去的边框一起往外挪（matplotlib 就是这么排的）
    seedExactRender(livePanel(), makeManifest({ spines, yTicksBbox: [-0.03, 0.1, 0.05, 0.8] }))
    await mount()
    await hover(0.04 - dx(5), 0.5)
    expect(strip()?.getAttribute('data-spine-zone')).toBe('left')
    expect(strip()?.getAttribute('data-spine-zone-kind')).toBe('outer')
    // 框的左沿此刻是空白（离线 24px）：没有命中区
    await hover(0.1 + dx(1), 0.5)
    expect(strip()).toBeNull()
    // 点偏出去的线本身 = 选中它的子图
    await press(0.04, 0.5)
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0'])
  })

  it('没有 spines（极坐标 / 3D）：整层没有任何边框命中', async () => {
    seedExactRender(livePanel(), makeManifest({ spines: null }))
    await mount()
    await hover(0.3, 0.9 - dy(5))
    expect(strip()).toBeNull()
    await press(0.3, 0.9 - dy(5))
    expect(livePanel().overrides).toEqual([])
  })
})
