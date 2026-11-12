/**
 * 图内标注跟着面板走（ADR 0161 修订 2026-11-04）：多图排版里面板角缩放时，**整个落在**这张面板里的
 * 标注按同一固定点、同一比例缩放位置 / 尺寸 / 所有页面 pt 量，同一个事务（一条历史）；压在图边、伸到
 * 图外 / 锁定 / 隐藏的不动；非等比、单图编辑里都不跟。拖动 / 方向键移动面板时同一批标注一起平移。
 *
 * 版面：pa 在 (10,20) 100×75，拖东南角（固定点 = 左上角 (10,20)）。
 */
import { formatMessage, literal } from '@/i18n'
import { beforeEach, afterEach, describe, expect, it } from 'vitest'

import { moveLabel } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useProfileStore } from '@/store/profileStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import {
  emptyProject,
  type ArrowObject,
  type CanvasObject,
  type PanelObject,
  type ShapeObject,
  type TextObject,
} from '@/types/document'
import { startMoveDrag, startResizeDrag } from './interactions'
import { nudgeKeyDown, nudgeKeyUp, finishNudge } from './nudge'

const panel = (): PanelObject =>
  ({
    id: 'pa',
    type: 'panel',
    x: 10,
    y: 20,
    w: 100,
    h: 75,
    fileId: 'pa.pdf',
    fileKind: 'pdf',
    nativeW: 100,
    nativeH: 75,
    script: 'fig.py',
    overrides: [],
  }) as unknown as PanelObject

const text = (extra: Partial<TextObject> = {}): TextObject =>
  ({
    id: 't1',
    type: 'text',
    text: 'a',
    sizePt: 10,
    bold: false,
    color: '#000',
    align: 'left',
    x: 30,
    y: 30,
    w: 40,
    h: 8,
    borderPt: 0.5,
    padding: 1,
    ...extra,
  }) as TextObject

const arrow = (extra: Partial<ArrowObject> = {}): ArrowObject =>
  ({
    id: 'a1',
    type: 'arrow',
    x: 50,
    y: 50,
    w: 20,
    h: 10,
    start: { rx: 0, ry: 0 },
    end: { rx: 1, ry: 1 },
    strokePt: 1.2,
    color: '#000',
    head: 'end',
    ...extra,
  }) as ArrowObject

const shape = (extra: Partial<ShapeObject> = {}): ShapeObject =>
  ({
    id: 's1',
    type: 'shape',
    shape: 'rect',
    x: 100,
    y: 85,
    w: 30,
    h: 30,
    strokePt: 2,
    cornerRadius: 3,
    color: '#000',
    fill: null,
    ...extra,
  }) as ShapeObject

const get = <T extends CanvasObject>(id: string) =>
  useDocumentStore.getState().doc.objects.find((o) => o.id === id) as T

async function setup(extras: CanvasObject[] = [text(), arrow(), shape()]) {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  // 关吸附：这里只看"跟随"，不让缩放吸附把比例拽走
  useUiStore.setState({ tool: 'select', snapEnabled: false })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_corner_follow')
  useDocumentStore.getState().commit(literal('摆放'), (d) => {
    d.objects.push(panel(), ...extras)
  })
  useDocumentStore.setState({ past: [], future: [] })
}

const down = () =>
  ({ clientX: 0, clientY: 0, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
const fire = (
  type: 'pointermove' | 'pointerup',
  mm: [number, number],
  mods: { altKey?: boolean } = {},
) =>
  window.dispatchEvent(
    new MouseEvent(type, { clientX: mmToWorld(mm[0]), clientY: mmToWorld(mm[1]), bubbles: true, ...mods }),
  )

/** 拖东南角，返回拖动中最后一帧的读数 */
function dragCorner(to: [number, number], mods: { altKey?: boolean } = {}) {
  startResizeDrag(down(), 'pa', 'se')
  fire('pointermove', [to[0] / 2, to[1] / 2], mods)
  fire('pointermove', to, mods)
  const st = useInteractionStore.getState().dragStatus
  fire('pointerup', to, mods)
  return st && {
    main: formatMessage(st.main),
    detail: formatMessage(st.detail),
    tone: st.tone,
    box: st.box,
  }
}

/** 给 pa 塞一份「这一版」的 manifest：图里画出来的文字字号（脚本 pt）逐个给 */
function seedFonts(...sizes: number[]) {
  const p = get<PanelObject>('pa')
  const manifest = {
    elements: sizes.map((v, i) => ({
      gid: `axes_0.text_${i}`,
      role: 'text',
      editable: [{ prop: 'fontsize', value: v }],
    })),
  }
  useRenderStore.setState({
    byKey: {
      [renderKeyOf(p)]: { manifest, lastPatches: JSON.stringify(p.overrides), stale: false },
    },
  } as never)
}

beforeEach(async () => {
  await setup()
  seedFonts(9)
})
afterEach(() => {
  useInteractionStore.getState().end()
})

describe('拖角缩放：图内标注一起缩', () => {
  it('位置、尺寸、pt 量按同一个固定点、同一个比例缩放，一条历史', () => {
    dragCorner([-50, -37.5])
    const p = get<PanelObject>('pa')
    const s = p.w / 100
    expect(s).toBeGreaterThan(0.3)
    expect(s).toBeLessThan(0.7)
    expect(p.h / 75).toBeCloseTo(s, 9)

    // 固定点 = 左上角 (10, 20)；位置按框中心位似
    const t = get<TextObject>('t1')
    expect(t.w).toBeCloseTo(40 * s, 9)
    expect(t.h).toBeCloseTo(8 * s, 9)
    expect(t.x + t.w / 2).toBeCloseTo(10 + (50 - 10) * s, 9)
    expect(t.y + t.h / 2).toBeCloseTo(20 + (34 - 20) * s, 9)
    expect(t.sizePt).toBeCloseTo(10 * s, 9)
    expect(t.borderPt).toBeCloseTo(0.5 * s, 9)
    expect(t.padding).toBeCloseTo(1 * s, 9)

    const a = get<ArrowObject>('a1')
    expect(a.strokePt).toBeCloseTo(1.2 * s, 9)
    expect(a.w).toBeCloseTo(20 * s, 9)
    expect(a.start).toEqual({ rx: 0, ry: 0 }) // 端点是相对框的比例，不变
    expect(a.end).toEqual({ rx: 1, ry: 1 })

    // 压在图边上、伸到图外的形状是独立的，不跟
    expect(get<ShapeObject>('s1')).toMatchObject({ x: 100, y: 85, w: 30, strokePt: 2, cornerRadius: 3 })

    // 面板与标注在同一个事务里：整手势一条历史，撤销一步全部回来
    expect(useDocumentStore.getState().past).toHaveLength(1)
    useDocumentStore.getState().undo()
    expect(get<PanelObject>('pa').w).toBe(100)
    expect(get<TextObject>('t1')).toMatchObject({ x: 30, y: 30, w: 40, sizePt: 10, borderPt: 0.5, padding: 1 })
    expect(get<ArrowObject>('a1').strokePt).toBe(1.2)
  })

  it('图外 / 压边 / 锁定 / 隐藏的标注不动', async () => {
    await setup([
      text({ id: 'far', x: 300, y: 300 }),
      text({ id: 'edge', x: 90, y: 30 }),
      text({ id: 'locked', locked: true }),
      text({ id: 'hidden', hidden: true }),
      text({ id: 'in' }),
    ])
    dragCorner([-50, -37.5])
    for (const id of ['far', 'edge', 'locked', 'hidden']) {
      const o = get<TextObject>(id)
      expect(o.sizePt).toBe(10)
      expect(o.w).toBe(40)
    }
    expect(get<TextObject>('far').x).toBe(300)
    expect(get<TextObject>('in').sizePt).toBeLessThan(10)
  })

  it('放大也一样（比例 > 1）', () => {
    dragCorner([50, 37.5])
    const s = get<PanelObject>('pa').w / 100
    expect(s).toBeGreaterThan(1.3)
    expect(get<TextObject>('t1').sizePt).toBeCloseTo(10 * s, 9)
  })

  it('成组的标注要整组都在图里才跟（不把组拆开）', async () => {
    await setup([
      text({ id: 'g-in', groupId: 'g1' }),
      text({ id: 'g-out', x: 300, y: 300, groupId: 'g1' }),
      text({ id: 'h-1', groupId: 'g2' }),
      arrow({ id: 'h-2', groupId: 'g2' }),
    ])
    dragCorner([-50, -37.5])
    expect(get<TextObject>('g-in')).toMatchObject({ x: 30, sizePt: 10 })
    expect(get<TextObject>('h-1').sizePt).toBeLessThan(10)
    expect(get<ArrowObject>('h-2').strokePt).toBeLessThan(1.2)
  })

  it('图里没有标注：读数不带后缀', async () => {
    await setup([text({ id: 'far', x: 300, y: 300 })])
    const readout = dragCorner([-50, -37.5])
    expect(readout!.detail).toBe('')
  })

  it('⌥ 反转比例锁定 = 非等比：没有"一个比例"，标注留在原处', () => {
    const readout = dragCorner([-50, -10], { altKey: true })
    const p = get<PanelObject>('pa')
    expect(p.w / 100).not.toBeCloseTo(p.h / 75, 3)
    expect(get<TextObject>('t1')).toMatchObject({ x: 30, y: 30, w: 40, sizePt: 10 })
    expect(readout).toMatchObject({ detail: '标注不动' })
  })

  it('单图编辑（fast_edit）里不跟，也没有读数', () => {
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'pa' })
    const readout = dragCorner([-50, -37.5])
    expect(get<TextObject>('t1')).toMatchObject({ x: 30, sizePt: 10 })
    expect(readout).toBeNull()
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
  })

})

describe('移动面板：图里的标注一起平移', () => {
  const select = (...ids: string[]) => useSelectionStore.getState().set(ids)

  it('拖动面板：图里的标注同一位移，图外 / 压边的不动；一条历史、标签按选中的个数说', () => {
    select('pa')
    startMoveDrag(down(), 'pa')
    fire('pointermove', [5, 3])
    fire('pointermove', [10, 6])
    fire('pointerup', [10, 6])
    expect(get<PanelObject>('pa')).toMatchObject({ x: 20, y: 26 })
    expect(get<TextObject>('t1')).toMatchObject({ x: 40, y: 36, w: 40, sizePt: 10 })
    expect(get<ArrowObject>('a1')).toMatchObject({ x: 60, y: 56 })
    expect(get<ShapeObject>('s1')).toMatchObject({ x: 100, y: 85 })
    const { past } = useDocumentStore.getState()
    expect(past).toHaveLength(1)
    expect(formatMessage(past[0].label)).toBe(formatMessage(moveLabel(1)))
    useDocumentStore.getState().undo()
    expect(get<TextObject>('t1')).toMatchObject({ x: 30, y: 30 })
  })

  it('方向键微调面板：同一批标注一起动', () => {
    select('pa')
    const key = (k: string) => new KeyboardEvent('keydown', { key: k })
    expect(nudgeKeyDown(key('ArrowRight'))).toBe(true)
    nudgeKeyUp(new KeyboardEvent('keyup', { key: 'ArrowRight' }))
    finishNudge()
    const p = get<PanelObject>('pa')
    expect(p.x).toBeGreaterThan(10)
    expect(get<TextObject>('t1').x).toBeCloseTo(30 + (p.x - 10), 9)
    expect(get<ShapeObject>('s1').x).toBe(100)
  })

  it('只拖标注本身：面板不跟', () => {
    select('t1')
    startMoveDrag(down(), 't1')
    fire('pointermove', [10, 6])
    fire('pointerup', [10, 6])
    expect(get<TextObject>('t1')).toMatchObject({ x: 40, y: 36 })
    expect(get<PanelObject>('pa')).toMatchObject({ x: 10, y: 20 })
  })
})

describe('缩放状态（interaction.dragStatus）', () => {
  it('主句 = 缩放百分比 + 图里真实最小字号 × 缩放比（panelScale / drawnMinFontPt，与预检同一来源），后缀 = 标注数', () => {
    const readout = dragCorner([-50, -37.5])
    const p = get<PanelObject>('pa')
    const s = p.w / 100
    expect(readout).toMatchObject({
      main: `缩放 100% → ${Math.round(s * 100)}% · 图内最小字号 9.0 → ${(Math.round(9 * s * 10) / 10).toFixed(1)} pt`,
      detail: '标注 2 个一起缩放', // 压在图边上的形状不算
    })
    // 胶囊贴着被拖面板此刻的框
    expect(readout!.box.w).toBeCloseTo(p.w, 9)
  })

  it('低于规范下限 → warn；规范库没载入读不到下限 → 不判（normal）', () => {
    useProfileStore.setState({ loaded: false })
    expect(dragCorner([-50, -37.5])).toMatchObject({ tone: 'normal' })
    useDocumentStore.getState().undo()
    useProfileStore.setState({ loaded: true })
    // 默认规范的字号下限（8pt 左右）：缩到约 50% 后图内文字 ≈ 4.5pt，低于它
    expect(dragCorner([-50, -37.5])).toMatchObject({ tone: 'warn' })
  })

  it('#1647 P2-2：图里文字是 12 pt，缩到 75% 显示 9.0 pt、不告警（不再按固定 9 pt 估成 6.8）', () => {
    seedFonts(12, 14)
    useProfileStore.setState({ loaded: true })
    const r = dragCorner([-25, -18.75])
    expect(r!.main).toBe('缩放 100% → 75% · 图内最小字号 12.0 → 9.0 pt')
    expect(r!.tone).toBe('normal')
  })

  it('#1647 P2-2：图里最小文字 6 pt，100% 起手就低于 8 pt 下限 → 告警', () => {
    seedFonts(6, 10)
    useProfileStore.setState({ loaded: true })
    const r = dragCorner([0.01, 0.0075])
    expect(r!.main).toMatch(/图内最小字号 6\.0 → 6\.0 pt$/)
    expect(r!.tone).toBe('warn')
  })

  it('#1647 P2-2：读不到 manifest = 不报 pt、不判下限（不拿估计值冒充事实）', () => {
    useRenderStore.setState({ byKey: {} } as never)
    useProfileStore.setState({ loaded: true })
    const r = dragCorner([-50, -37.5])
    expect(r!.main).toMatch(/^缩放 100% → \d+%$/)
    expect(r!.tone).toBe('normal')
  })

  it('放大不低于下限', () => {
    useProfileStore.setState({ loaded: true })
    expect(dragCorner([50, 37.5])).toMatchObject({ tone: 'normal' })
  })

  it('位图源不报 pt（按 dpi 评质量）', async () => {
    await setup()
    useDocumentStore.getState().commit(literal('改成位图'), (d) => {
      ;(d.objects[0] as PanelObject).fileKind = 'raster'
    })
    const r = dragCorner([-50, -37.5])
    expect(r!.main).toMatch(/^缩放 100% → \d+%$/)
  })

  it('松手后状态清掉', () => {
    dragCorner([-50, -37.5])
    expect(useInteractionStore.getState().dragStatus).toBeNull()
  })
})
