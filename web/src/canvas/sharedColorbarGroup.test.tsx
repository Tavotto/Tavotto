/**
 * 共享色条的**真实组**在画布上的几何行为（`Manifest.groups`）。
 *
 * 组是结构节点、不是图内元素：画布上点不中它，只能从元素树 / 面包屑选中。选中之后：
 *
 *   1. 拖组里任一成员 = 整组平移：子图 B、C 与色条轴各写一条 position、同一个位移，
 *      一条撤销、撤销 / 重做都回得去；组外的子图 A 不动；
 *   2. 只点不拖 = 钻进去选中那个成员（选中组不会吞掉「想改 B」的那一下）；
 *   3. 组框的手柄 = 整组缩放，参照框是成员 position 的并集，成员线性重映射进新框
 *      （相对位置不变，色条仍贴着组的右边）；
 *   4. 单独选中 B 拖动：只写 B，C 与共享色条都不跟（色条不是 B 的随行元素）；
 *   5. 有成员落位不归 Tavotto 管的组不展开——只挪一部分会拆散它。
 *
 * jsdom 说明：命中层的 `getBoundingClientRect` 桩成 layout × zoom 同口径（与
 * twinAxesPick.test 同一套），断言的是写进文档的 override 与选区，不依赖 CSS 命中。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { Manifest, ManifestElement, ManifestGroup } from '@/lib/api'
import { literal } from '@/i18n'
import { alignEntries, expandGroups, resolveGroup } from '@/lib/elementGeom'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { startGroupResize } from './interactions'
import { PanelView } from './PanelView'
import { useQuickEdit } from './quickEditStore'

/* -------------------------------- 测试数据 -------------------------------- */

type R4 = [number, number, number, number]
/** position 是 bottom-origin；这里全部上下对称（y + h = 0.85），bbox 与之同值 */
const POS: Record<string, R4> = {
  axes_0: [0.05, 0.15, 0.25, 0.7],
  axes_1: [0.38, 0.15, 0.2, 0.7],
  axes_2: [0.62, 0.15, 0.2, 0.7],
  axes_3: [0.85, 0.15, 0.03, 0.7],
}
const GROUP = 'group:axes_3'

const axes = (gid: string, label: string, extra: Partial<ManifestElement> = {}): ManifestElement => ({
  gid,
  role: 'axes',
  label,
  bbox: POS[gid],
  editable: [{ prop: 'position', type: 'rect', value: POS[gid] } as never],
  draggable: false,
  resizable: true,
  ...extra,
})

const manifest = (group: Partial<ManifestGroup> = {}): Manifest => ({
  stem: 'Fig1',
  size_mm: [100, 80],
  elements: [
    { gid: 'figure', role: 'figure', label: '整张图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    axes('axes_0', '子图 1'),
    axes('axes_1', '子图 2', { parent_gid: GROUP }),
    {
      gid: 'axes_1.images_0',
      role: 'image',
      label: '图像 1',
      bbox: POS.axes_1,
      editable: [],
      draggable: false,
      resizable: true,
      geom_gid: 'axes_1',
    },
    axes('axes_2', '子图 3', { parent_gid: GROUP }),
    axes('axes_3', '色条轴', {
      parent_gid: GROUP,
      is_colorbar: true,
      colorbar_gid: 'axes_3.colorbar',
    }),
    {
      gid: 'axes_3.colorbar',
      role: 'colorbar',
      label: '色条',
      bbox: POS.axes_3,
      editable: [],
      draggable: false,
      resizable: true,
      geom_gid: 'axes_3',
      host_gid: 'axes_1',
      owner_gids: ['axes_1', 'axes_2'],
      mappable_gid: 'axes_1.images_0',
    },
  ],
  groups: [
    {
      gid: GROUP,
      kind: 'shared_colorbar',
      members: ['axes_1', 'axes_2', 'axes_3'],
      subplot_gids: ['axes_1', 'axes_2'],
      colorbar_gid: 'axes_3.colorbar',
      mappable_gid: 'axes_1.images_0',
      bbox: [0.38, 0.15, 0.5, 0.7],
      resizable: true,
      ...group,
    },
  ],
})

const panel = (): PanelObject =>
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
  }) as unknown as PanelObject

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
  hitLayer().getBoundingClientRect = () =>
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
}

const at = (fx: number, fy: number) => ({ clientX: fx * LAYOUT.width, clientY: fy * LAYOUT.height })

/** 在 (fx, fy) 按下、横向拖 dxPx 屏幕像素、松手（dxPx = 0 就是点一下） */
async function drag([fx, fy]: [number, number], dxPx: number) {
  const start = at(fx, fy)
  const ev = new MouseEvent('pointerdown', { bubbles: true, cancelable: true, button: 0, ...start })
  Object.assign(ev, { pointerType: 'mouse', pointerId: 1 })
  await act(async () => {
    hitLayer().dispatchEvent(ev)
  })
  await act(async () => {
    for (let i = 1; dxPx && i <= 10; i++) {
      window.dispatchEvent(
        new MouseEvent('pointermove', {
          bubbles: true,
          clientX: start.clientX + (dxPx * i) / 10,
          clientY: start.clientY,
        }),
      )
    }
    window.dispatchEvent(
      new MouseEvent('pointerup', { bubbles: true, clientX: start.clientX + dxPx, clientY: start.clientY }),
    )
  })
}

const livePanel = () => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}
const positionOf = (gid: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === 'position')?.value as R4 | undefined

/** B 的图像上、C 上：离色条与组框边都远的点 */
const ON_B: [number, number] = [0.48, 0.5]
const ON_C: [number, number] = [0.72, 0.5]

async function setup(m: Manifest = manifest()) {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({
    tool: 'select',
    elementPanelId: 'p1',
    selectedGids: [],
    snapEnabled: false,
    dragAxesWithCompanions: true,
  })
  useQuickEdit.getState().close()
  useSelectionStore.getState().clear()
  useInteractionStore.getState().end()
  useRenderStore.getState().clear()
  useRenderStore.setState({ render: async () => {} })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_shared_cbar_group')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panel())
  })
  seedExactRender(panel(), m)
  useDocumentStore.setState({ past: [], future: [] })
}

beforeEach(async () => {
  await setup()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => {
    root.unmount()
  })
  container.remove()
  useInteractionStore.getState().end()
})

/* ------------------------------ 组 = 成员 ------------------------------ */

describe('组展开成成员：几何只写成员自己的 position', () => {
  it('选区里的组展开成 B、C 与色条轴（色条经几何代理归到色条轴，不重复）', () => {
    const keys = alignEntries(panel(), manifest(), [GROUP]).map((e) => e.key)
    expect(keys).toEqual(['axes_1', 'axes_2', 'axes_3'])
    // 组与它的成员同时在选区里：每个几何落点只出现一次
    expect(
      alignEntries(panel(), manifest(), [GROUP, 'axes_3.colorbar', 'axes_1']).map((e) => e.key),
    ).toEqual(['axes_1', 'axes_2', 'axes_3'])
  })

  it('有成员落位不归 Tavotto 管的组不展开（只挪一部分会拆散它）', () => {
    expect(expandGroups(manifest({ resizable: false }), [GROUP])).toEqual([])
    expect(resolveGroup(panel(), manifest({ resizable: false }), [GROUP])).toBeNull()
  })
})

describe('选中组之后在画布上拖', () => {
  it('拖组里任一成员 = 整组平移：B、C、色条同一位移，A 不动；一条撤销、可重做', async () => {
    await mount()
    act(() => useUiStore.getState().setSelectedGid(GROUP))
    await drag(ON_B, 40)

    const dfx = 40 / LAYOUT.width
    for (const g of ['axes_1', 'axes_2', 'axes_3']) {
      expect(positionOf(g)![0]).toBeCloseTo(POS[g][0] + dfx, 4)
      expect(positionOf(g)![1]).toBeCloseTo(POS[g][1], 4)
      expect(positionOf(g)!.slice(2)).toEqual(POS[g].slice(2))
    }
    expect(positionOf('axes_0')).toBeUndefined()
    // 选区没被改成点到的那张图：还是组
    expect(useUiStore.getState().selectedGids).toEqual([GROUP])
    expect(useDocumentStore.getState().past).toHaveLength(1)

    act(() => {
      useDocumentStore.getState().undo()
    })
    expect(livePanel().overrides).toEqual([])
    act(() => {
      useDocumentStore.getState().redo()
    })
    expect(positionOf('axes_3')![0]).toBeCloseTo(POS.axes_3[0] + dfx, 4)
  })

  it('只点不拖 = 钻进去选中点到的那个成员，文档一个字节不动', async () => {
    await mount()
    act(() => useUiStore.getState().setSelectedGid(GROUP))
    await drag(ON_C, 0)
    expect(useUiStore.getState().selectedGids).toEqual(['axes_2'])
    expect(livePanel().overrides).toEqual([])
    expect(useDocumentStore.getState().past).toHaveLength(0)
  })

  it('单独选中 B 拖动：只有 B 走，C 与共享色条都不跟', async () => {
    await mount()
    act(() => useUiStore.getState().setSelectedGid('axes_1'))
    await drag(ON_B, 40)
    expect(positionOf('axes_1')![0]).toBeCloseTo(POS.axes_1[0] + 40 / LAYOUT.width, 4)
    expect(positionOf('axes_2')).toBeUndefined()
    expect(positionOf('axes_3')).toBeUndefined()
  })
})

describe('组框手柄 = 整组缩放', () => {
  it('参照框是成员 position 的并集，成员线性重映射：相对位置不变、色条仍贴右边', async () => {
    const group = resolveGroup(panel(), manifest(), [GROUP])!
    // 组框 = B 左边到色条右边
    expect(group.box[0]).toBeCloseTo(0.38, 6)
    expect(group.box[0] + group.box[2]).toBeCloseTo(0.88, 6)

    const down = { clientX: 0, clientY: 0, button: 0, stopPropagation() {} }
    startGroupResize(down as never, livePanel(), group, LAYOUT, 'e')
    await act(async () => {
      window.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: -50, clientY: 0 }))
      window.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: -50, clientY: 0 }))
    })

    const x0 = 0.38
    const w0 = 0.5
    const w1 = w0 - 50 / LAYOUT.width
    const k = w1 / w0
    for (const g of ['axes_1', 'axes_2', 'axes_3']) {
      const [x, y, w, h] = positionOf(g)!
      // 西边钉住，东西向按同一个倍数重映射；南北向不变
      expect(x).toBeCloseTo(x0 + (POS[g][0] - x0) * k, 3)
      expect(w).toBeCloseTo(POS[g][2] * k, 3)
      expect(y).toBeCloseTo(POS[g][1], 4)
      expect(h).toBeCloseTo(POS[g][3], 4)
    }
    const cb = positionOf('axes_3')!
    expect(cb[0] + cb[2]).toBeCloseTo(x0 + w1, 3)
    expect(positionOf('axes_0')).toBeUndefined()
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })
})
