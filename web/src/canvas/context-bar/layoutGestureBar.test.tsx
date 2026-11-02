/**
 * 胶囊在多图排版的「面板角缩放 / 边裁剪」拖动期间不隐藏、显示这一下拖动的含义（ADR 0161）；
 * 其余手势（移动、普通对象缩放、框选……）仍然拖动即藏；不拖时选中面板，胶囊里有
 * 「贴合内容 / 取消裁剪 / 连同标注」。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore, type LayoutReadout } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ContextBar } from './ContextBar'
import { readoutSentence } from './LayoutGestureBar'

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

const scale = (extra: Partial<Extract<LayoutReadout, { kind: 'scale' }>> = {}): LayoutReadout => ({
  kind: 'scale',
  fromPct: 92,
  toPct: 75,
  fromPt: 7.4,
  toPt: 6.0,
  low: false,
  follow: { on: true, count: 2 },
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
    scaleWithAnnotations: true,
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
const readoutEl = () => document.querySelector('[data-layout-readout]')
const begin = async (kind: Parameters<ReturnType<typeof useInteractionStore.getState>['begin']>[0], r: LayoutReadout | null) =>
  act(async () => {
    useInteractionStore.getState().begin(kind)
    useInteractionStore.getState().setLayoutReadout(r)
    // 画布上的 pointerdown（任意位置）本来会把胶囊藏起来；这两种手势要不藏
    window.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
  })

describe('拖动期间的胶囊', () => {
  beforeEach(async () => {
    await mountBar({ ink: true })
  })

  it('面板角缩放（resize + 缩放读数）：不藏，显示状态句', async () => {
    expect(bar()).not.toBeNull()
    await begin('resize', scale())
    expect(bar()).not.toBeNull()
    const el = readoutEl()!
    expect(el.getAttribute('data-layout-readout')).toBe('scale')
    expect(el.textContent).toBe('缩放 92% → 75% · 图内文字 7.4 → 6.0 pt · 标注 2 个一起缩放')
    // 状态句替换了快捷动作，拖动中不出现按钮
    expect(bar()!.querySelector('button')).toBeNull()
  })

  it('面板边裁剪（crop + 裁剪读数）：不藏', async () => {
    await begin('crop', { kind: 'crop', side: 'l', state: 'trim', mm: 3.14 })
    expect(bar()).not.toBeNull()
    expect(readoutEl()!.textContent).toBe('裁掉留白 · 左 3.1 mm')
  })

  it('其余手势照旧拖动即藏：移动 / 框选 / 平移（无读数）与普通 resize（无读数）', async () => {
    for (const kind of ['move', 'marquee', 'pan', 'resize', 'endpoint'] as const) {
      await begin(kind, null)
      expect(bar(), kind).toBeNull()
      await act(async () => {
        useInteractionStore.getState().end()
        window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }))
      })
      expect(bar(), `${kind} 松手`).not.toBeNull()
    }
  })

  it('读数与手势种类对不上（move + 缩放读数）不算：仍然藏', async () => {
    await begin('move', scale())
    expect(bar()).toBeNull()
  })

  it('松手：状态句消失，快捷动作回来', async () => {
    await begin('resize', scale())
    await act(async () => {
      useInteractionStore.getState().end()
      window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }))
    })
    expect(readoutEl()).toBeNull()
    expect(bar()!.querySelector('button')).not.toBeNull()
  })
})

describe('状态句（读数 → 文字与语气，只翻译不判断）', () => {
  it('缩放：低于下限 warn；不带 pt（位图）、标注不动 / 无标注', () => {
    expect(readoutSentence(scale({ low: true })).tone).toBe('warn')
    expect(readoutSentence(scale()).tone).toBe('neutral')
    expect(readoutSentence(scale({ fromPt: null, toPt: null, follow: { on: false, count: 2 } })).text).toBe(
      '缩放 92% → 75% · 标注不动',
    )
    expect(readoutSentence(scale({ follow: { on: true, count: 0 } })).text).toBe(
      '缩放 92% → 75% · 图内文字 7.4 → 6.0 pt',
    )
  })

  it('裁剪五态：full / trim / tight / blocked(warn) / content(danger)', () => {
    const s = (state: 'full' | 'trim' | 'tight' | 'blocked' | 'content', side: 'l' | 'r' | 't' | 'b' = 'l', mm = 4.2) =>
      readoutSentence({ kind: 'crop', side, state, mm })
    expect(s('full')).toEqual({ text: '到图边 · 左未裁剪', tone: 'neutral' })
    expect(s('trim', 'l', 3.1)).toEqual({ text: '裁掉留白 · 左 3.1 mm', tone: 'neutral' })
    expect(s('tight')).toEqual({ text: '紧凑 · 左贴合内容', tone: 'neutral' })
    expect(s('blocked').tone).toBe('warn')
    expect(s('blocked').text).toMatch(/^紧凑 · 已到内容边缘，按住 .+ 继续会裁掉内容$/)
    expect(s('content', 'r')).toEqual({ text: '裁剪 · 右 4.2 mm，将裁掉内容', tone: 'danger' })
  })
})

describe('不拖时选中面板：贴合内容 / 取消裁剪 / 连同标注', () => {
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

  it('连同标注开关：默认开，点一下关，状态在 uiStore', async () => {
    await mountBar({ ink: true })
    const btn = action('follow-annotations')!
    expect(btn.getAttribute('aria-pressed')).toBe('true')
    await act(async () => {
      btn.click()
    })
    expect(useUiStore.getState().scaleWithAnnotations).toBe(false)
    expect(action('follow-annotations')!.getAttribute('aria-pressed')).toBe('false')
  })

  it('单图编辑里不出这三个', async () => {
    await mountBar({ ink: true })
    await act(async () => {
      useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'pa' })
    })
    expect(action('fit-content')).toBeNull()
    expect(action('follow-annotations')).toBeNull()
  })
})
