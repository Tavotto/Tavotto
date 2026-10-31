/**
 * 真实定位的看护（ADR 0030）。
 *
 * 「点了一下什么都没发生」是这条功能最容易滑进去的失败，所以每条用例都
 * **量到落点**：模式、选中、视口、Inspector、属性字段；失败的四条各自
 * 量到**结构化原因**，不接受一个笼统的 false。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { FIELD_FLASH_MS, focusObject, HIGHLIGHT_MS, openProblems } from './issueFocus'
import { currentFigureOf } from './problemContext'
import { effectiveScope, issuesInScope } from './problemList'
import type { ObjectRef, ValidationIssue } from './validation'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { settleMode, useWorkspaceStore } from '@/store/workspace'
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
  // 模式由图数推出（本文件没挂模式订阅，换完文档自己对一次，等于订阅在场时的状态）
  settleMode()
}

beforeEach(() => {
  useUiStore.setState({ issueHighlight: null, fieldLocate: null, problemFilter: null, elementPanelId: null })
  useWorkspaceStore.getState().clear()
})

afterEach(() => {
  vi.useRealTimers()
  document.body.innerHTML = ''
})

describe('画布对象：排版模式', () => {
  it('选中、滚进视野、开属性页；画布上有两张图 = 排版，定位不改模式', async () => {
    await seed([panel(), panel({ id: 'p2', fileId: 'Fig2.pdf', y: 50 }), text])
    expect(useWorkspaceStore.getState().mode).toBe('layout')
    const reveal = vi.spyOn(useViewportStore.getState(), 'revealRect')
    const out = focusObject(refFor({ objectId: 't1' }))
    expect(out).toEqual({ ok: true, mode: 'layout', field: 'none' })
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

describe('图内元素：进图内编辑', () => {
  it('画布上只有这一张图 = 单图编辑；图内元素编辑 + 选中那个 gid', async () => {
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
    // 失败时不许留下半个状态（模式本来就只由图数决定，定位碰不到它）
    expect(useUiStore.getState().elementPanelId).toBeNull()
  })

  /** 属性页里的一个字段锚点：`gid` 挂在锚点自己身上（FieldBlock），或挂在外层页上（`data-inspector-gid`） */
  const anchor = (prop: string, gid: string | null, where: 'self' | 'page' = 'self') => {
    const host = document.createElement('div')
    host.setAttribute('data-prop', prop)
    const input = document.createElement('input')
    host.appendChild(input)
    if (where === 'self' && gid) host.setAttribute('data-gid', gid)
    if (where === 'page') {
      const page = document.createElement('div')
      if (gid) page.setAttribute('data-inspector-gid', gid)
      page.appendChild(host)
      document.body.appendChild(page)
    } else {
      document.body.appendChild(host)
    }
    return { host, input }
  }

  /** 手动推帧：`focusField` 的重试排在 rAF 里 */
  const frames = () => {
    const queue: FrameRequestCallback[] = []
    const raf = vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation(((cb: FrameRequestCallback) => {
      queue.push(cb)
      return queue.length
    }) as never)
    return {
      step: (n = 1) => {
        for (let i = 0; i < n; i++) queue.splice(0).forEach((cb) => cb(0))
      },
      restore: () => raf.mockRestore(),
    }
  }

  it('属性字段真的被聚焦（选择器用 data-prop，不用本地化文案），那一行亮一下', async () => {
    await seed()
    const { host, input } = anchor('fontsize', 'axes_0.xticks')
    const out = focusObject(refFor({ gid: 'axes_0.xticks' }), 'fontsize')
    expect(out).toMatchObject({ field: 'focused' })
    expect(document.activeElement).toBe(input)
    expect(host.hasAttribute('data-locate-flash')).toBe(true)
    expect(useUiStore.getState().fieldLocate).toBeNull()
  })

  it('同名字段认主人：上一个元素的属性页还挂着时，不落到它的「字号」上（2026-10-23 用户反馈）', async () => {
    await seed()
    // 从「图例」跳到「图例标题」：旧页（图例）还在，新页（标题）下一帧才挂上
    const stale = anchor('fontsize', 'axes_0.legend')
    const f = frames()
    try {
      const out = focusObject(refFor({ gid: 'axes_0.legend.title' }), 'fontsize')
      expect(out).toMatchObject({ field: 'requested' })
      expect(document.activeElement).not.toBe(stale.input)
      // 请求挂着：属性页据此展开字段所在的那一折
      expect(useUiStore.getState().fieldLocate).toMatchObject({ gid: 'axes_0.legend.title', prop: 'fontsize' })
      f.step(3)
      const fresh = anchor('fontsize', 'axes_0.legend.title', 'page')
      f.step()
      expect(document.activeElement).toBe(fresh.input)
      expect(fresh.host.hasAttribute('data-locate-flash')).toBe(true)
      expect(stale.host.hasAttribute('data-locate-flash')).toBe(false)
      expect(useUiStore.getState().fieldLocate).toBeNull()
    } finally {
      f.restore()
    }
  })

  it('等不到落点就放下请求（折不会被一张过期的请求一直撑开），也不宣称已聚焦', async () => {
    await seed()
    const f = frames()
    try {
      const out = focusObject(refFor({ gid: 'axes_0.xticks' }), 'fontsize')
      expect(out).toMatchObject({ field: 'requested' })
      f.step(100)
      expect(useUiStore.getState().fieldLocate).toBeNull()
    } finally {
      f.restore()
    }
  })

  it('还在重试时又定位了一条没有字段的问题：旧的重试作废，之后挂上的同名字段不被抢焦点（Codex #1365 P2）', async () => {
    await seed()
    const f = frames()
    try {
      focusObject(refFor({ gid: 'axes_0.legend' }), 'title_fontsize')
      expect(useUiStore.getState().fieldLocate).not.toBeNull()
      // 紧接着定位到一张图本身（页面级 / 无字段）
      focusObject(refFor({}))
      expect(useUiStore.getState().fieldLocate).toBeNull()
      const late = anchor('title_fontsize', 'axes_0.legend', 'page')
      f.step(5)
      expect(document.activeElement).not.toBe(late.input)
      expect(late.host.hasAttribute('data-locate-flash')).toBe(false)
    } finally {
      f.restore()
    }
  })

  it('还在重试时切了文档：重试停下，不把焦点落进另一份文档（Codex #1365 P2）', async () => {
    await seed()
    const f = frames()
    try {
      focusObject(refFor({ gid: 'axes_0.legend' }), 'title_fontsize')
      await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
      const late = anchor('title_fontsize', 'axes_0.legend', 'page')
      f.step(5)
      expect(document.activeElement).not.toBe(late.input)
      expect(useUiStore.getState().fieldLocate).toBeNull()
    } finally {
      f.restore()
    }
  })

  it('落点那一行的亮框到点撤掉', async () => {
    vi.useFakeTimers()
    await seed()
    const { host } = anchor('fontsize', 'axes_0.xticks')
    focusObject(refFor({ gid: 'axes_0.xticks' }), 'fontsize')
    expect(host.hasAttribute('data-locate-flash')).toBe(true)
    vi.advanceTimersByTime(FIELD_FLASH_MS + 1)
    expect(host.hasAttribute('data-locate-flash')).toBe(false)
  })

  it('画布对象（没有 gid）不认图内元素属性页里的同名字段', async () => {
    await seed()
    const inFigure = anchor('sizePt', 'axes_0.title', 'page')
    const own = anchor('sizePt', null)
    focusObject(refFor({ objectId: 't1' }), 'sizePt')
    expect(document.activeElement).toBe(own.input)
    expect(document.activeElement).not.toBe(inFigure.input)
  })

  it('没有 propertyPath 时回 none，不宣称"已定位到字段"', async () => {
    await seed()
    const out = focusObject(refFor({ gid: 'axes_0.xticks' }))
    expect(out).toMatchObject({ field: 'none' })
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

  it('多项目隔离：属于另一份文档的问题不许照着 id 在这个项目里乱选', async () => {
    await seed()
    useSelectionStore.getState().clear()
    const out = focusObject(refFor({ documentId: 'd_another_project' }))
    expect(out).toEqual({ ok: false, reason: 'document_not_loaded' })
    // 对象 id 在两个项目里可以相同——没有这道闸的话它会选中一个毫不相干的对象
    expect(useSelectionStore.getState().ids).toEqual([])
  })

  it('文档还没载入', async () => {
    await seed()
    useDocumentStore.setState({ documentId: '' })
    expect(focusObject(refFor())).toEqual({ ok: false, reason: 'document_not_loaded' })
  })
})

describe('页面级问题', () => {
  it('没有对象可选时切到「画布」那一栏，而不是随便选一个对象', async () => {
    await seed([panel(), panel({ id: 'p2', fileId: 'Fig2.pdf', y: 50 })])
    useSelectionStore.getState().set(['p1'])
    const out = focusObject(refFor({ objectId: null }))
    expect(out).toEqual({ ok: true, mode: 'layout', field: 'none' })
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

  /** 面板此刻会当作「当前图」的那张（与 `useCurrentFigure` 同一个判据） */
  const panelCurrentFigure = () =>
    currentFigureOf(
      [useWorkspaceStore.getState().activePanelId, useUiStore.getState().elementPanelId, useSelectionStore.getState().ids.at(-1) ?? null],
      useDocumentStore.getState().doc.objects,
    ).id

  it('带导出目标（另一张图、另一张画布）：先切过去、当前图就是它、范围是「当前图」——不是工作区原来选的那张', async () => {
    await seed([panel(), panel({ id: 'p2', fileId: 'Fig2.pdf', y: 50 })])
    const first = useDocumentStore.getState().activeCanvasId
    // 用户原来在 p1 的图内编辑里（画布上有两张图：排版里的子状态）
    useUiStore.setState({ elementPanelId: 'p1', problemScope: 'document' })
    const second = useDocumentStore.getState().addCanvas('画布 2')
    expect(useDocumentStore.getState().activeCanvasId).toBe(second)
    openProblems({ severities: ['error'], target: { canvasId: first, figureId: 'p2' } })
    expect(useDocumentStore.getState().activeCanvasId).toBe(first)
    expect(panelCurrentFigure(), '面板的当前图仍是上一张').toBe('p2')
    expect(useUiStore.getState().problemScope).toBe('figure')
    expect(useUiStore.getState().problemFilter).toEqual(['error'])
    expect(useUiStore.getState().leftTab).toBe('problems')
  })

  it('目标是画布（没有图）：范围切到「当前画布」（只列这张画布的问题，不是整份排版）；不带 target 时不动现有范围', async () => {
    await seed()
    const first = useDocumentStore.getState().activeCanvasId
    const second = useDocumentStore.getState().addCanvas('画布 2')
    useUiStore.setState({ problemScope: 'figure' })
    openProblems({ target: { canvasId: first, figureId: null } })
    expect(useDocumentStore.getState().activeCanvasId).toBe(first)
    expect(useUiStore.getState().problemScope).toBe('canvas')
    // 面板用同一套函数裁清单：两张画布各一条问题，只剩 A 的那条
    const mk = (canvasId: string): ValidationIssue =>
      ({ issueId: `i-${canvasId}`, objectRef: { canvasId, objectId: 'p1', gid: null } }) as unknown as ValidationIssue
    const all = [mk(first), mk(second)]
    const scope = effectiveScope(useUiStore.getState().problemScope, null)
    expect(scope).toBe('canvas')
    expect(issuesInScope(all, scope, null, useDocumentStore.getState().activeCanvasId).map((i) => i.issueId)).toEqual([`i-${first}`])
    useUiStore.setState({ problemScope: 'figure' })
    useUiStore.setState({ problemScope: 'figure' })
    openProblems()
    expect(useUiStore.getState().problemScope).toBe('figure')
  })
})

describe('定位不抢左栏（审计 T09）', () => {
  it('从问题清单定位图内元素：左栏留在「问题」页，元素树不顶上来', async () => {
    await seed()
    useUiStore.setState({ layout: 'wide', leftOpen: true, leftTab: 'problems' })
    const out = focusObject(refFor({ gid: 'axes_0.xticks' }), 'fontsize')
    expect(out.ok).toBe(true)
    expect(useUiStore.getState().leftTab, '元素树把问题清单顶掉了').toBe('problems')
    expect(useUiStore.getState().leftOpen).toBe(true)
    expect(useUiStore.getState().elementPanelId).toBe('p1')
  })
})
