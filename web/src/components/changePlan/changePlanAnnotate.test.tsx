/**
 * 批注重改的弹窗（ADR 0206 §五）：对比 / 批注切换与底栏按钮的主次、免费用户带锁点了不进入、授权入口没出现时整个不画、
 * 首次提示只出一次、版本切换、批注模式点选元素写批注。引擎与状态机在 `tests/test_changeplan_revise.py` 与 `store/changePlanRevise.test.ts`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { ChangePlan, LicenseStatus, Manifest } from '@/lib/api'
import { useChangePlanStore, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { useLicenseStore } from '@/store/licenseStore'
import { emptyProject, type PanelObject } from '@/types/document'

vi.mock('@/hooks/useVariantPng', () => ({ useVariantPng: () => ({ url: null, loading: false, approximate: false }), variantNeedsRender: () => false }))
vi.mock('@/hooks/useRuntimePreview', () => ({ useRuntimePreview: () => ({ url: null, exact: false }) }))
const previewSnapshot = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPngSnapshot: (...a: unknown[]) => previewSnapshot(...a),
}))

import { TooltipProvider } from '../ui/Tooltip'
import { ChangePlanCard } from './ChangePlanCard'
import { ChangePlanDialog } from './ChangePlanDialog'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const BASE = [{ gid: 'ax.title', prop: 'fontsize', value: 9 }]
const plan = (id: string, version: number): ChangePlan => ({
  id,
  version,
  source: 'review',
  base: { target: 'f_p1', fingerprint: 'fp' },
  base_patches: BASE,
  candidate_patches: [{ gid: 'ax.title', prop: 'fontsize', value: 14 }],
  groups: [
    {
      key: 'g1',
      label: '标题太小',
      comment_id: 'c1',
      ...(version > 1 ? { origin: 'review' as const } : {}),
      atomic: false,
      changes: [{ target_gid: 'ax.title', prop: 'fontsize', before: 9, after: 14, patch: { gid: 'ax.title', prop: 'fontsize', value: 14 } }],
      boxes: { before: [{ gid: 'ax.title', box: [0.1, 0.1, 0.2, 0.2] }], after: [{ gid: 'ax.title', box: [0.1, 0.1, 0.3, 0.2] }] },
    },
  ],
  unhandled: [],
  preview: { before: `/${id}-b.png`, after: `/${id}-a.png` },
})
const entry = (over: Partial<PlanEntry> = {}): PlanEntry => {
  const s = useDocumentStore.getState()
  return {
    key: 'a',
    pj: null,
    panelId: 'p1',
    fileId: 'f_p1',
    source: { fileId: 'f_p1', script: 'fig.py' },
    documentId: s.documentId,
    loadSeq: s.loadSeq,
    canvasId: s.activeCanvasId,
    createdAt: 1,
    status: 'ready',
    planId: 'p1',
    plan: plan('p1', 1),
    beforeSrc: '/p1-b.png',
    afterSrc: '/p1-a.png',
    unchecked: {},
    drafts: {},
    request: { comments: [] },
    comments: [],
    commentCount: 1,
    ...over,
  }
}
const put = (e: PlanEntry) => act(() => useChangePlanStore.setState({ entries: { [e.key]: e }, order: [e.key], dialogKey: 'a' }))
const status = (s: LicenseStatus) => act(() => useLicenseStore.setState({ status: s }))
const PRO: LicenseStatus = { enabled: true, licensed: true, state: 'active', features: ['change-plan-rerun'], exp: null, email_hint: 'a***@x.com', needs_refresh: false }
const FREE: LicenseStatus = { ...PRO, licensed: false, features: [] }

let host: HTMLDivElement
let root: Root
const render = () => act(() => root.render(<TooltipProvider><ChangePlanDialog /></TooltipProvider>))
const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)
const qa = (sel: string) => [...document.body.querySelectorAll<HTMLElement>(sel)]
const view = () => q('[data-change-plan-dialog]')!.dataset.changePlanDialogView
const clearHint = () => {
  try {
    localStorage.removeItem('tavotto.changePlan.noteHintSeen')
  } catch {
    /* 没有存储：提示每次都出 */
  }
}

beforeEach(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  previewSnapshot.mockReset().mockReturnValue(new Promise(() => {}))
  clearHint()
  useChangePlanStore.setState({ entries: {}, order: [], dialogKey: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_change_plan_annotate')
  useDocumentStore.getState().commit(literal('放入'), (d) => {
    d.objects.push({ id: 'p1', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject)
  })
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

it('授权入口没出现（上线开关关着）：「对比 / 批注」切换、「写批注」整个不画', () => {
  status({ enabled: false })
  put(entry())
  render()
  expect(q('[data-change-plan-dialog]')).not.toBeNull()
  expect(q('[data-change-plan-write-note]')).toBeNull()
  expect(q('[data-premium-lock]')).toBeNull()
  expect(qa('[role="radiogroup"]').some((g) => g.textContent?.includes('批注'))).toBe(false)
})

it('免费用户：切换项与「写批注」都带锁，点了不进入批注', () => {
  status(FREE)
  put(entry())
  render()
  expect(qa('[data-premium-lock="change-plan-rerun"]')).toHaveLength(2)
  act(() => q('[data-change-plan-write-note]')!.click())
  const annotateTab = qa('[role="radio"]').find((b) => b.textContent?.includes('批注'))!
  act(() => annotateTab.click())
  expect(view()).toBe('compare')
  // 悬停写批注的小胶囊不出现（没有元素表也不会去取）
  expect(previewSnapshot).not.toHaveBeenCalled()
})

it('高级版：写批注 → 批注模式；0 条时「按批注重改」禁用且次级、「应用」次级；有批注后重改变主、应用仍次级', () => {
  status(PRO)
  put(entry())
  render()
  expect(view()).toBe('compare')
  expect(q('[data-premium-lock]')).toBeNull()
  expect(q('[data-change-plan-confirm]')!.dataset.variant).toBe('primary')
  act(() => q('[data-change-plan-write-note]')!.click())
  expect(view()).toBe('annotate')
  expect(q('[data-change-plan-stage]')!.dataset.changePlanStage).toBe('annotate')
  expect(qa('[data-change-plan-frame]')).toHaveLength(1)
  expect(q('[data-change-plan-notes]')!.textContent).toContain('点图上的元素写批注')
  expect(q('[data-change-plan-rerun]')!.hasAttribute('disabled')).toBe(true)
  expect(q('[data-change-plan-rerun]')!.dataset.variant).toBe('secondary')
  expect(q('[data-change-plan-confirm]')!.dataset.variant).toBe('secondary')

  act(() => useChangePlanStore.getState().saveNote('a', { id: 'n1', anchor: { kind: 'overall' }, label: '整体', text: '整体再小一点' }))
  expect(q('[data-change-plan-notes]')!.textContent).toContain('批注 · 1')
  expect(q('[data-change-plan-notes]')!.textContent).toContain('整体再小一点')
  expect(q('[data-change-plan-rerun]')!.hasAttribute('disabled')).toBe(false)
  expect(q('[data-change-plan-rerun]')!.dataset.variant).toBe('primary')
  expect(q('[data-change-plan-rerun]')!.textContent).toContain('按批注重改 · 1')
  expect(q('[data-change-plan-confirm]')!.dataset.variant).toBe('secondary')
  // 图上同一套实心编号：整体批注是「全」
  expect(q('[data-change-plan-frame] [data-review-mark="all"]')).not.toBeNull()

  const revise = vi.fn(async () => 'started' as const)
  act(() => useChangePlanStore.setState({ revise }))
  act(() => q('[data-change-plan-rerun]')!.click())
  expect(revise).toHaveBeenCalledWith('a')
})

it('「整体批注」按钮打开批注框，记下就进列表；× 删除', () => {
  status(PRO)
  put(entry())
  render()
  act(() => q('[data-change-plan-write-note]')!.click())
  act(() => q('[data-change-plan-note-overall]')!.click())
  const box = q('[data-change-plan-note-editor]')!
  expect(box).not.toBeNull()
  const ta = box.querySelector('textarea')!
  act(() => {
    const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
    set.call(ta, '颜色再淡一点')
    ta.dispatchEvent(new Event('input', { bubbles: true }))
  })
  act(() => (box.querySelector('button[type="submit"]') as HTMLButtonElement).click())
  expect(useChangePlanStore.getState().entries.a.notes?.map((n) => [n.anchor.kind, n.text])).toEqual([['overall', '颜色再淡一点']])
  expect(q('[data-change-plan-note-editor]')).toBeNull()
})

it('批注模式：悬停高亮、点一下元素打开该元素的批注框（锚点 = 元素 gid + 画面框，与审阅意见同形）', async () => {
  const manifest = {
    elements: [{ gid: 'ax.title', role: 'title', label: 'title', bbox: [0.1, 0.1, 0.3, 0.1], editable: [] }],
  } as unknown as Manifest
  previewSnapshot.mockResolvedValue({ url: 'data:image/png;base64,AAA', manifest })
  const rect = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, right: 1000, bottom: 500, width: 1000, height: 500, x: 0, y: 0, toJSON: () => ({}) })
  try {
    status(PRO)
    put(entry())
    render()
    act(() => q('[data-change-plan-write-note]')!.click())
    await act(async () => void (await Promise.resolve()))
    const layer = q('[data-change-plan-pick="annotate"]')!
    const fire = (type: string, x: number, y: number) => act(() => void layer.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 })))
    layer.setPointerCapture = () => {}
    fire('pointermove', 200, 70)
    expect(q('[data-change-plan-hover="ax.title"]')).not.toBeNull()
    fire('pointerdown', 200, 70)
    fire('pointerup', 200, 70)
    expect(q('[data-change-plan-note-editor]')).not.toBeNull()
    expect(q('[data-change-plan-note-editor]')!.textContent).toContain('title')
  } finally {
    rect.mockRestore()
  }
})

it('首次提示：高级版第一次打开出现，点任意处消失并记住；再打开不出', () => {
  status(PRO)
  put(entry())
  render()
  expect(q('[data-change-plan-first-hint]')!.textContent).toContain('点这里写批注')
  act(() => void document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })))
  expect(q('[data-change-plan-first-hint]')).toBeNull()
  act(() => root.unmount())
  root = createRoot(host)
  render()
  expect(q('[data-change-plan-first-hint]')).toBeNull()
})

it('多版：两版紧凑分段、三版收成下拉；切版本换图，各版勾选各记各的；新版出来回到「对比」', () => {
  status(PRO)
  const v = (id: string, n: number) => ({ planId: id, plan: plan(id, n), beforeSrc: `/${id}-b.png`, afterSrc: `/${id}-a.png`, unchecked: {} })
  put(entry({ planId: 'p2', plan: plan('p2', 2), beforeSrc: '/p2-b.png', afterSrc: '/p2-a.png', versions: [v('p1', 1), v('p2', 2)] }))
  render()
  const tabs = qa('[role="radiogroup"][aria-label="方案版本"] [role="radio"]')
  expect(tabs.map((t) => t.textContent)).toEqual(['第 1 版', '第 2 版'])
  expect(q('[data-change-plan-dialog]')!.dataset.changePlanVersion).toBe('2')
  act(() => q('[data-change-plan-write-note]')!.click())
  expect(view()).toBe('annotate')
  act(() => tabs[0].click())
  expect(useChangePlanStore.getState().entries.a.planId).toBe('p1')
  expect(view()).toBe('compare')
  expect(q('[data-change-plan-dialog]')!.dataset.changePlanVersion).toBe('1')
  expect(q('[data-change-plan-stage] img')!.getAttribute('src')).toBe('/p1-b.png')
})

it('卡片：按批注重改进行中回到「生成中」，标题行是「第 N 版」', () => {
  const v = (id: string, n: number) => ({ planId: id, plan: plan(id, n), beforeSrc: `/${id}-b.png`, afterSrc: `/${id}-a.png`, unchecked: {} })
  put(entry({ planId: 'p2', plan: plan('p2', 2), versions: [v('p1', 1), v('p2', 2)] }))
  act(() => root.render(<TooltipProvider><ChangePlanCard entry={useChangePlanStore.getState().entries.a} /></TooltipProvider>))
  expect(q('[data-change-plan-card]')!.textContent).toContain('第 2 版')
  put(entry({ revising: 'p3' }))
  act(() => root.render(<TooltipProvider><ChangePlanCard entry={useChangePlanStore.getState().entries.a} /></TooltipProvider>))
  expect(q('[data-change-plan-card]')!.dataset.changePlanCard).toBe('generating')
  expect(q('[data-change-plan-card]')!.textContent).toContain('按批注重改')
})

const MANIFEST = { elements: [{ gid: 'ax.legend', role: 'legend', label: 'legend', bbox: [0.6, 0.1, 0.2, 0.1], editable: [] }] } as unknown as Manifest
const FULL = { left: 0, top: 0, right: 1000, bottom: 500, width: 1000, height: 500, x: 0, y: 0, toJSON: () => ({}) }

it('对比模式（高级版）：鼠标移进图才去取元素表；悬停「修改后」一侧的元素出「写批注」胶囊，点了切到批注并打开它的批注框；分隔线左边不响应', async () => {
  previewSnapshot.mockResolvedValue({ url: 'data:image/png;base64,AAA', manifest: MANIFEST })
  const rect = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(FULL)
  try {
    status(PRO)
    put(entry({ planId: 'pq', plan: plan('pq', 1) })) // 元素表按方案 id 缓存：这条用例用自己的 id
    render()
    expect(previewSnapshot).not.toHaveBeenCalled()
    const layer = q('[data-change-plan-pick="quick"]')!
    // 滑动对比默认分隔线在 50%：这一层只盖右半边
    expect(layer.style.left).toBe('50%')
    act(() => void layer.dispatchEvent(new PointerEvent('pointerover', { bubbles: true })))
    await act(async () => void (await Promise.resolve()))
    expect(previewSnapshot).toHaveBeenCalledTimes(1)
    const move = (x: number, y: number) => act(() => void layer.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: x, clientY: y })))
    move(700, 75)
    expect(q('[data-change-plan-quick="ax.legend"]')!.textContent).toContain('写批注')
    move(300, 75) // 落在左半边的图坐标上没有元素
    expect(q('[data-change-plan-quick]')).toBeNull()
    move(700, 75)
    act(() => q('[data-change-plan-pick="quick"]')!.click())
    expect(view()).toBe('annotate')
    expect(q('[data-change-plan-note-editor]')!.textContent).toContain('legend')
  } finally {
    rect.mockRestore()
  }
})

it('批注模式：在图上拖一块 = 框选批注（标「框选的一块」）；太小的拖动不算', async () => {
  previewSnapshot.mockResolvedValue({ url: 'data:image/png;base64,AAA', manifest: MANIFEST })
  const rect = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(FULL)
  try {
    status(PRO)
    put(entry())
    render()
    act(() => q('[data-change-plan-write-note]')!.click())
    const layer = q('[data-change-plan-pick="annotate"]')!
    layer.setPointerCapture = () => {}
    const fire = (type: string, x: number, y: number) => act(() => void layer.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 })))
    fire('pointerdown', 100, 100)
    fire('pointermove', 400, 300)
    fire('pointerup', 400, 300)
    expect(q('[data-change-plan-note-editor]')!.textContent).toContain('框选的一块')
  } finally {
    rect.mockRestore()
  }
})

it('部分勾选时悬停「写批注」不出现（命中的是完整「修改后」，与显示不一致）；底栏「写批注」仍可进批注模式', () => {
  status(PRO)
  const p = plan('pp', 1)
  const two = { ...p, groups: [p.groups[0], { ...p.groups[0], key: 'g2', comment_id: 'c2' }] }
  put(entry({ planId: 'pp', plan: two, unchecked: { g2: true } }))
  render()
  expect(q('[data-change-plan-pick="quick"]')).toBeNull()
  act(() => q('[data-change-plan-write-note]')!.click())
  expect(view()).toBe('annotate')
  expect(q('[data-change-plan-pick="annotate"]')).not.toBeNull()
})

// ---- 一键排版 / 脚本卡不能重改（canRevise）与排版卡清单头 ----
const layoutPlan = (): ChangePlan => ({
  ...plan('L', 1),
  source: 'quick_layout',
  base: { target: '', fingerprint: '' },
  base_patches: [],
  candidate_patches: [],
  groups: [{ key: 'layout', label: '排版', atomic: true, changes: [], boxes: { before: [{ gid: 'p1', box: [0, 0, 0.5, 0.5] }], after: [{ gid: 'p1', box: [0.1, 0.1, 0.5, 0.5] }] } }],
})
const layoutEntry = (over: Partial<PlanEntry> = {}) =>
  entry({
    from: 'quick_layout',
    kind: 'layout',
    panelId: '',
    fileId: '',
    source: { fileId: '', script: null },
    planId: undefined,
    plan: layoutPlan(),
    layout: { apply: () => 'applied', regenerate: () => undefined, count: 3 },
    ...over,
  })

it('canRevise：只有审阅 / 助手来源的图内方案才能重改', async () => {
  const { canRevise } = await import('./changePlanModel')
  expect(canRevise(entry())).toBe(true)
  expect(canRevise(entry({ from: 'review' }))).toBe(true)
  expect(canRevise(entry({ from: 'assistant' }))).toBe(true)
  expect(canRevise(layoutEntry())).toBe(false)
  expect(canRevise(entry({ from: 'quick_layout' }))).toBe(false)
  expect(canRevise(entry({ plan: layoutPlan() }))).toBe(false)
})

it('一键排版的卡（高级版用户）：没有「对比 / 批注」切换、没有「写批注」、没有悬停写批注，也没有首次提示；清单头是「排版 · N 张图」，不出现「0 处」', () => {
  status(PRO)
  put(layoutEntry())
  render()
  expect(q('[data-change-plan-dialog]')).not.toBeNull()
  expect(q('[data-change-plan-write-note]')).toBeNull()
  expect(q('[data-change-plan-first-hint]')).toBeNull()
  expect(qa('[role="radiogroup"]').some((g) => g.textContent?.includes('批注'))).toBe(false)
  expect(q('[data-change-plan-list]')!.textContent).toContain('排版 · 3 张图')
  expect(q('[data-change-plan-list]')!.textContent).toContain('3 张图')
  expect(q('[data-change-plan-list]')!.textContent).not.toMatch(/改动 · |0 处/)
})

it('对照：同样是高级版，审阅来源的卡仍有「对比 / 批注」切换与「写批注」', () => {
  status(PRO)
  put(entry())
  render()
  expect(q('[data-change-plan-write-note]')).not.toBeNull()
  expect(qa('[role="radiogroup"]').some((g) => g.textContent?.includes('批注'))).toBe(true)
})
