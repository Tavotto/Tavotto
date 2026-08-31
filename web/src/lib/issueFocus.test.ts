/**
 * 真实定位的看护（ADR 0030）。
 *
 * 「点了一下什么都没发生」是这条功能最容易滑进去的失败，所以每条用例都
 * **量到落点**：模式、选中、视口、Inspector、属性字段；失败的四条各自
 * 量到**结构化原因**，不接受一个笼统的 false。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { focusObject, HIGHLIGHT_MS, openProblems } from './issueFocus'
import type { ObjectRef } from './validation'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { emptyProject, type PanelObject, type TextObject } from '@/types/document'

const panel = (over: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 10,
  y: 10,
  w: 60,
  h: 40,
  script: 'fig1.py',
  ...over,
})

const text: TextObject = {
  id: 't1',
  type: 'text',
  text: '图注',
  sizePt: 6,
  bold: false,
  color: '#000000',
  align: 'left',
  x: 5,
  y: 5,
  w: 20,
  h: 6,
}

const refFor = (over: Partial<ObjectRef> = {}): ObjectRef => ({
  documentId: 'd_focus',
  canvasId: useDocumentStore.getState().activeCanvasId,
  objectId: 'p1',
  gid: null,
  ...over,
})

async function seed(objects = [panel(), text]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_focus')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 80, h: 60 }
    d.objects = objects
  })
  // 视口要有尺寸，`revealRect` 才会真的动（没尺寸时它按设计什么都不做）
  useViewportStore.setState({ viewW: 800, viewH: 600 })
}

beforeEach(() => {
  useUiStore.setState({ issueHighlight: null, problemFilter: null, elementPanelId: null })
  useWorkspaceStore.getState().clear()
})

afterEach(() => {
  vi.useRealTimers()
  document.body.innerHTML = ''
})

describe('画布对象：排版模式', () => {
  it('切回排版、选中、滚进视野、开属性页', async () => {
    await seed()
    useWorkspaceStore.getState().enterFastEdit('p1')
    const reveal = vi.spyOn(useViewportStore.getState(), 'revealRect')
    const out = focusObject(refFor({ objectId: 't1' }))
    expect(out).toEqual({ ok: true, mode: 'layout', focusedField: false })
    expect(useWorkspaceStore.getState().mode).toBe('layout')
    expect(useSelectionStore.getState().ids).toEqual(['t1'])
    expect(reveal).toHaveBeenCalledWith({ x: 5, y: 5, w: 20, h: 6 })
    expect(useUiStore.getState().rightTab).toBe('properties')
  })

  it('高亮是短暂的，且与选中态分开——到点自己撤掉', async () => {
    vi.useFakeTimers()
    await seed()
    focusObject(refFor())
    expect(useUiStore.getState().issueHighlight?.objectId).toBe('p1')
    vi.advanceTimersByTime(HIGHLIGHT_MS + 10)
    expect(useUiStore.getState().issueHighlight).toBeNull()
    // 选中态不受影响：高亮撤了不等于取消选择
    expect(useSelectionStore.getState().ids).toEqual(['p1'])
  })

  it('连着定位同一个对象两次会重新播一遍（token 变了）', async () => {
    await seed()
    focusObject(refFor())
    const first = useUiStore.getState().issueHighlight!.token
    focusObject(refFor())
    expect(useUiStore.getState().issueHighlight!.token).toBeGreaterThan(first)
  })
})

describe('图内元素：进快速编辑', () => {
  it('切进快速编辑 + 图内元素编辑 + 选中那个 gid', async () => {
    await seed()
    const out = focusObject(refFor({ gid: 'axes_0.xticks' }), 'fontsize')
    expect(out.ok).toBe(true)
    expect(out).toMatchObject({ mode: 'fast_edit' })
    expect(useWorkspaceStore.getState().mode).toBe('fast_edit')
    expect(useWorkspaceStore.getState().activePanelId).toBe('p1')
    expect(useUiStore.getState().elementPanelId).toBe('p1')
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.xticks'])
  })

  it('没有源脚本进不去图内编辑——是一个说得出原因的失败，不是崩溃', async () => {
    await seed([panel({ script: undefined })])
    const out = focusObject(refFor({ gid: 'axes_0.xticks' }))
    expect(out).toEqual({ ok: false, reason: 'not_editable' })
    // 失败时不许留下半个状态
    expect(useWorkspaceStore.getState().mode).toBe('layout')
    expect(useUiStore.getState().elementPanelId).toBeNull()
  })

  it('属性字段真的被聚焦（选择器用 data-prop，不用本地化文案）', async () => {
    await seed()
    const host = document.createElement('div')
    host.setAttribute('data-prop', 'fontsize')
    const input = document.createElement('input')
    host.appendChild(input)
    document.body.appendChild(host)
    const raf = vi
      .spyOn(globalThis, 'requestAnimationFrame')
      .mockImplementation(((cb: FrameRequestCallback) => {
        cb(0)
        return 1
      }) as never)
    const out = focusObject(refFor({ gid: 'axes_0.xticks' }), 'fontsize')
    expect(out).toMatchObject({ focusedField: true })
    expect(document.activeElement).toBe(input)
    raf.mockRestore()
  })

  it('没有 propertyPath 时如实回 false，不宣称"已定位到字段"', async () => {
    await seed()
    const out = focusObject(refFor({ gid: 'axes_0.xticks' }))
    expect(out).toMatchObject({ focusedField: false })
  })
})

describe('跨画布', () => {
  it('对象在另一张画布上时先切过去', async () => {
    await seed()
    const first = useDocumentStore.getState().activeCanvasId
    const second = useDocumentStore.getState().addCanvas('画布 2')
    expect(useDocumentStore.getState().activeCanvasId).toBe(second)
    const out = focusObject({ documentId: 'd_focus', canvasId: first, objectId: 'p1', gid: null })
    expect(out.ok).toBe(true)
    expect(useDocumentStore.getState().activeCanvasId).toBe(first)
    expect(useSelectionStore.getState().ids).toEqual(['p1'])
  })

  it('画布已经不在项目里 = canvas_missing（不是静默不动）', async () => {
    await seed()
    const out = focusObject(refFor({ canvasId: 'c_gone' }))
    expect(out).toEqual({ ok: false, reason: 'canvas_missing' })
  })
})

describe('失败的四条各有各的原因', () => {
  it('对象已删', async () => {
    await seed()
    useDocumentStore.getState().commit(literal('删'), (d) => {
      d.objects = d.objects.filter((o) => o.id !== 'p1')
    })
    expect(focusObject(refFor())).toEqual({ ok: false, reason: 'object_deleted' })
  })

  it('文档还没载入', async () => {
    await seed()
    useDocumentStore.setState({ documentId: '' })
    expect(focusObject(refFor())).toEqual({ ok: false, reason: 'document_not_loaded' })
  })
})

describe('页面级问题', () => {
  it('没有对象可选时切到「画布」那一栏，而不是随便选一个对象', async () => {
    await seed()
    useSelectionStore.getState().set(['p1'])
    const out = focusObject(refFor({ objectId: null }))
    expect(out).toEqual({ ok: true, mode: 'layout', focusedField: false })
    expect(useUiStore.getState().rightTab).toBe('canvas')
    expect(useSelectionStore.getState().ids).toEqual(['p1'])
  })
})

describe('openProblems', () => {
  it('打开左侧问题面板并带上筛选', () => {
    openProblems({ severities: ['error'] })
    expect(useUiStore.getState().leftTab).toBe('problems')
    expect(useUiStore.getState().leftOpen).toBe(true)
    expect(useUiStore.getState().problemFilter).toEqual(['error'])
  })

  it('不带筛选时把上一次的筛选清掉——否则用户会看到一个空面板', () => {
    useUiStore.getState().setProblemFilter(['suggestion'])
    openProblems()
    expect(useUiStore.getState().problemFilter).toBeNull()
  })
})
