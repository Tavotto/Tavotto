/**
 * 拖动状态胶囊（`DragStatusBar`，读 `interaction.dragStatus`，ADR 0161）：有状态就显示、不受
 * 「拖动即藏」影响；没有状态时一切照旧（移动、框选、普通缩放……仍然拖动即藏）；不拖时选中面板，胶囊里有
 * 「贴合内容 / 取消裁剪」。
 */
import { formatMessage, literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore, type DragStatus } from '@/store/interactionStore'
import { cropDragStatus, scaleDragStatus } from '@/lib/dragStatus'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ContextBar } from './ContextBar'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panel = (extra: Partial<PanelObject> = {}): PanelObject =>
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
    overrides: [],
    ...extra,
  }) as unknown as PanelObject

const BOX = { x: 10, y: 20, w: 100, h: 75 }
const scale = (extra: Partial<Parameters<typeof scaleDragStatus>[0]> = {}): DragStatus =>
  scaleDragStatus({
    fromPct: 92,
    toPct: 75,
    fromPt: 7.4,
    toPt: 6.0,
    low: false,
    follow: { on: true, count: 2 },
    box: BOX,
    ...extra,
  })

let root: Root

async function mountBar(opts: { ink?: boolean; extra?: Partial<PanelObject> } = {}) {
  localStorage.clear()
  document.body.innerHTML = ''
  useUiStore.setState({
    elementPanelId: null,
    selectedGids: [],
    editingTextId: null,
    cropTargetId: null,
    tool: 'select',
    layout: 'wide',
    leftOpen: false,
    rightOpen: false,
  })
  useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_layout_bar')
  useDocumentStore.getState().commit(literal('放面板'), (d) => {
    d.objects.push(panel(opts.extra))
  })
  useDocumentStore.setState({ past: [], future: [] })
  const p = useDocumentStore.getState().doc.objects[0] as PanelObject
  useRenderStore.getState().patch(renderKeyOf(p), {
    fileId: p.fileId,
    manifest: {
      stem: 'Fig',
      size_mm: [100, 75],
      elements: [
        {
          gid: 'figure',
          role: 'figure',
          label: '整图',
          bbox: [0, 0, 1, 1],
          editable: [],
          draggable: false,
          ...(opts.ink ? { ink_bbox: [0.1, 0.1, 0.9, 0.9] as [number, number, number, number] } : {}),
        },
      ],
    },
    svg: '<svg xmlns="http://www.w3.org/2000/svg"/>',
    rev: 1,
    status: 'ready',
    lastPatches: JSON.stringify(p.overrides),
  })
  useRenderStore.setState((s) => ({ latest: { ...s.latest, [p.fileId]: renderKeyOf(p) } }))
  const anchor = document.createElement('div')
  anchor.setAttribute('data-object-id', 'pa')
  document.body.appendChild(anchor)
  const mountEl = document.createElement('div')
  document.body.appendChild(mountEl)
  root = createRoot(mountEl)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ContextBar />
      </TooltipProvider>,
    )
  })
  await act(async () => {
    useSelectionStore.getState().set(['pa'])
  })
}

afterEach(async () => {
  await act(async () => {
    root.unmount()
  })
  useInteractionStore.getState().end()
  useSelectionStore.getState().clear()
  document.body.innerHTML = ''
})

const bar = () => document.querySelector('[data-context-bar]')
const statusEl = () => document.querySelector('[data-drag-status]')
const begin = async (kind: Parameters<ReturnType<typeof useInteractionStore.getState>['begin']>[0], st: DragStatus | null) =>
  act(async () => {
    useInteractionStore.getState().begin(kind)
    useInteractionStore.getState().setDragStatus(st)
    // 画布上的 pointerdown（任意位置）本来会把快捷胶囊藏起来；状态胶囊不受影响
    window.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
  })

describe('拖动状态胶囊', () => {
  beforeEach(async () => {
    await mountBar({ ink: true })
  })

  it('有 dragStatus：显示主句 + 淡色后缀，带语气；快捷胶囊（拖动即藏）不出现', async () => {
    expect(bar()).not.toBeNull()
    await begin('resize', scale())
    const el = statusEl()!
    expect(el.getAttribute('data-drag-tone')).toBe('normal')
    expect(el.textContent).toBe('缩放 92% → 75% · 图内最小字号 7.4 → 6.0 pt· 标注 2 个一起缩放')
    expect(bar()).toBeNull()
  })

  it('不依赖手势种类或选区：kind 为 none 或 move 照样显示（单图编辑复用同一个形状）', async () => {
    await act(async () => {
      useInteractionStore.getState().setDragStatus(scale({ low: true }))
    })
    expect(statusEl()!.getAttribute('data-drag-tone')).toBe('warn')
    await begin('move', scale())
    expect(statusEl()).not.toBeNull()
  })

  it('end() 清掉：状态胶囊消失，快捷胶囊回来', async () => {
    await begin('crop', cropDragStatus({ side: 'l', state: 'trim', mm: 3.14, box: BOX }))
    expect(statusEl()!.textContent).toContain('裁掉留白 · 左 3.1 mm')
    await act(async () => {
      useInteractionStore.getState().end()
      window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }))
    })
    expect(statusEl()).toBeNull()
    expect(bar()!.querySelector('button')).not.toBeNull()
  })

  it('没有 dragStatus 时一切照旧：移动 / 框选 / 平移 / 普通缩放仍拖动即藏', async () => {
    for (const kind of ['move', 'marquee', 'pan', 'resize', 'endpoint'] as const) {
      await begin(kind, null)
      expect(bar(), kind).toBeNull()
      expect(statusEl(), kind).toBeNull()
      await act(async () => {
        useInteractionStore.getState().end()
        window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }))
      })
      expect(bar(), `${kind} 松手`).not.toBeNull()
    }
  })
})

describe('状态构造（手势侧：语气与句子）', () => {
  const text = (s: DragStatus) => ({ main: formatMessage(s.main), detail: formatMessage(s.detail), tone: s.tone })

  it('缩放：低于下限 warn；位图不带 pt；标注不动 / 无标注', () => {
    expect(scale({ low: true }).tone).toBe('warn')
    expect(scale().tone).toBe('normal')
    expect(text(scale({ fromPt: null, toPt: null, follow: { on: false, count: 2 } }))).toEqual({
      main: '缩放 92% → 75%',
      detail: '标注不动',
      tone: 'normal',
    })
    expect(text(scale({ follow: { on: true, count: 0 } })).detail).toBe('')
  })

  it('裁剪五态：full / trim / tight / blocked(warn) / content(danger)', () => {
    const s = (state: 'full' | 'trim' | 'tight' | 'blocked' | 'content', side: 'l' | 'r' | 't' | 'b' = 'l', mm = 4.2) =>
      text(cropDragStatus({ side, state, mm, box: BOX }))
    expect(s('full')).toMatchObject({ main: '到图边 · 左未裁剪', tone: 'normal' })
    expect(s('trim', 'l', 3.1)).toMatchObject({ main: '裁掉留白 · 左 3.1 mm', tone: 'normal' })
    expect(s('tight')).toMatchObject({ main: '紧凑 · 左贴合内容', tone: 'normal' })
    expect(s('blocked').tone).toBe('warn')
    expect(s('blocked').main).toMatch(/^紧凑 · 已到内容边缘，按住 .+ 继续会裁掉内容$/)
    expect(s('content', 'r')).toMatchObject({ main: '裁剪 · 右 4.2 mm，将裁掉内容', tone: 'danger' })
  })
})

describe('不拖时选中面板：贴合内容 / 取消裁剪', () => {
  const action = (name: string) =>
    bar()!.querySelector(`[data-layout-action="${name}"]`) as HTMLButtonElement | null

  it('有 ink_bbox：贴合内容可点，点一下四边裁到内容边缘（一条历史）；之后置灰', async () => {
    await mountBar({ ink: true })
    expect(action('fit-content')!.disabled).toBe(false)
    expect(action('reset-crop')!.disabled).toBe(true) // 还没裁过
    await act(async () => {
      action('fit-content')!.click()
    })
    const p = useDocumentStore.getState().doc.objects[0] as PanelObject
    expect(p.crop!.x).toBeCloseTo(0.1, 9)
    expect(p.crop!.y).toBeCloseTo(0.1, 9)
    expect(p.crop!.w).toBeCloseTo(0.8, 9)
    expect(p.w).toBeCloseTo(80, 9)
    expect(p.x).toBeCloseTo(20, 9) // 整图不动：10 + 100 × 0.1
    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(action('fit-content')!.disabled).toBe(true)
    expect(action('reset-crop')!.disabled).toBe(false)
    await act(async () => {
      action('reset-crop')!.click()
    })
    const q = useDocumentStore.getState().doc.objects[0] as PanelObject
    expect(q.crop).toBeUndefined()
    expect(q.w).toBeCloseTo(100, 9)
  })

  it('缺 ink_bbox（旧后端）：贴合内容置灰', async () => {
    await mountBar({ ink: false })
    expect(action('fit-content')!.disabled).toBe(true)
  })

  it('没有「连同标注」开关：图里的标注跟不跟按位置自动定（ADR 0161 修订 2026-11-04）', async () => {
    await mountBar({ ink: true })
    expect(action('reset-crop')).not.toBeNull()
    expect(action('follow-annotations')).toBeNull()
  })

  it('单图编辑里不出这两个', async () => {
    await mountBar({ ink: true })
    await act(async () => {
      useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'pa' })
    })
    expect(action('fit-content')).toBeNull()
    expect(action('reset-crop')).toBeNull()
  })
})
