/**
 * 批注重改的弹窗（ADR 0206 §五）：对比 / 批注切换与底栏按钮的主次、免费用户带锁点了不进入、授权入口没出现时整个不画、
 * 首次提示只出一次、版本切换、批注模式点选元素写批注。引擎与状态机在 `tests/test_changeplan_revise.py` 与 `store/changePlanRevise.test.ts`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { ChangePlan, LicenseStatus, Manifest } from '@/lib/api'
import { setChangePlanUndoRunner, useChangePlanStore, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { useLicenseStore } from '@/store/licenseStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'

vi.mock('@/hooks/useVariantPng', () => ({ useVariantPng: () => ({ url: null, loading: false, approximate: false }), variantNeedsRender: () => false }))
vi.mock('@/hooks/useRuntimePreview', () => ({ useRuntimePreview: () => ({ url: null, exact: false }) }))
const previewSnapshot = vi.fn()
const reviseApi = vi.fn()
const planManifest = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPngSnapshot: (...a: unknown[]) => previewSnapshot(...a),
  reviseChangePlan: (...a: unknown[]) => reviseApi(...a),
  fetchChangePlanManifest: (...a: unknown[]) => planManifest(...a),
  // 重改轮询永远在途：这里只看「发请求之前」的事
  getChangePlan: () => new Promise(() => {}),
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

const realActions = { revise: useChangePlanStore.getState().revise, undo: useChangePlanStore.getState().undo }
// 真应用里由 `useKeyboard` 交进来的「撤销」：测试里直接用文档的 undo
setChangePlanUndoRunner(() => useDocumentStore.getState().undo())
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
  planManifest.mockReset().mockReturnValue(new Promise(() => {}))
  reviseApi.mockReset().mockResolvedValue({ plan_id: 'p2' })
  clearHint()
  useChangePlanStore.setState({ ...realActions, entries: {}, order: [], dialogKey: null })
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

it('免费用户：切换项与「写批注」都带锁，点了不进入批注、不跳设置，只在原地弹一句话 +「了解高级版 ›」', () => {
  status(FREE)
  put(entry())
  render()
  expect(qa('[data-premium-lock="change-plan-rerun"]')).toHaveLength(2)
  act(() => qa('[data-premium-lock="change-plan-rerun"]')[1].click())
  expect(useUiStore.getState().settingsOpen).toBe(false)
  const ask = q('[data-premium-lock-ask]')!
  expect(ask.textContent).toContain('在改好的图上点元素写批注，再改一版')
  expect(ask.textContent).toContain('了解高级版')
  expect(view()).toBe('compare')
  // 点「了解高级版 ›」才去「关于与更新」，浮层收起
  act(() => q('[data-premium-lock-learn]')!.click())
  expect(useUiStore.getState().settingsOpen).toBe(true)
  expect(q('[data-premium-lock-ask]')).toBeNull()
  act(() => useUiStore.getState().setSettingsOpen(false))
  act(() => q('[data-change-plan-write-note]')!.click())
  const annotateTab = qa('[role="radio"]').find((b) => b.textContent?.includes('批注'))!
  act(() => annotateTab.click())
  expect(view()).toBe('compare')
  // 悬停写批注的小胶囊不出现（没有元素表也不会去取）
  expect(previewSnapshot).not.toHaveBeenCalled()
})

it('高级版：写批注 → 批注模式；0 条时「按批注重改」禁用且次级；有批注后重改变主；批注视图里没有「应用」（「放弃」仍在）', () => {
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
  expect(q('[data-change-plan-confirm]')).toBeNull()
  expect(q('[data-change-plan-discard]')).not.toBeNull()

  act(() => useChangePlanStore.getState().saveNote('a', { id: 'n1', anchor: { kind: 'overall' }, label: '整体', text: '整体再小一点' }))
  expect(q('[data-change-plan-notes]')!.textContent).toContain('批注 · 1')
  expect(q('[data-change-plan-notes]')!.textContent).toContain('整体再小一点')
  expect(q('[data-change-plan-rerun]')!.hasAttribute('disabled')).toBe(false)
  expect(q('[data-change-plan-rerun]')!.dataset.variant).toBe('primary')
  expect(q('[data-change-plan-rerun]')!.textContent).toContain('按批注重改 · 1')
  expect(q('[data-change-plan-confirm]')).toBeNull()
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

it('批注模式：曲线悬停沿真实路径描边（不画大半是空白的包围盒）；文字仍画框', async () => {
  // 一条从左下到右上的折线（分数坐标、y 向下），包围盒占了大半张图
  const line = {
    gid: 'ax.lines_0', role: 'line', label: 'k = 0.1', bbox: [0.1, 0.1, 0.8, 0.8], editable: [],
    geometry: { kind: 'polyline', fill: false, stroke: true, stroke_pt: 1.5, paint_order: 3, clip: [0.1, 0.1, 0.8, 0.8], paths: [{ closed: false, points: [[0.1, 0.9], [0.5, 0.5], [0.9, 0.1]] }] },
  }
  const manifest = { size_mm: [80, 60], elements: [line, { gid: 'ax.title', role: 'title', label: 'title', bbox: [0.4, 0.0, 0.2, 0.05], editable: [] }] } as unknown as Manifest
  previewSnapshot.mockResolvedValue({ url: 'data:image/png;base64,AAA', manifest })
  const rect = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, right: 1000, bottom: 500, width: 1000, height: 500, x: 0, y: 0, toJSON: () => ({}) })
  try {
    status(PRO)
    // 元素表按方案 id 缓存：换一个 id，免得拿到上一条用例缓存的那份
    put(entry({ planId: 'p_geom', plan: plan('p_geom', 1) }))
    render()
    act(() => q('[data-change-plan-write-note]')!.click())
    await act(async () => void (await Promise.resolve()))
    const layer = q('[data-change-plan-pick="annotate"]')!
    const fire = (type: string, x: number, y: number) => act(() => void layer.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 })))
    // 正落在线上（0.5, 0.5）
    fire('pointermove', 500, 250)
    expect(q('[data-change-plan-hover-path="ax.lines_0"]')).not.toBeNull()
    // 坐标不量化到 1%：分数坐标乘到 0–1000 再交给 geomPathD（它保留两位小数）
    expect(q('[data-change-plan-hover-path="ax.lines_0"] path')!.getAttribute('d')).toContain('M100.00,900.00')
    expect(q('[data-change-plan-hover-path="ax.lines_0"]')!.getAttribute('viewBox')).toBe('0 0 1000 1000')
    // 曲线不画着色的包围盒
    expect(q('[data-change-plan-hover="ax.lines_0"]')!.className).not.toContain('bg-sel')
    // 包围盒里离线很远的空白处（右下角）不命中这条线
    fire('pointermove', 850, 420)
    expect(q('[data-change-plan-hover-path="ax.lines_0"]')).toBeNull()
    // 文字没有路径：仍画框
    fire('pointermove', 500, 12)
    expect(q('[data-change-plan-hover="ax.title"]')!.className).toContain('bg-sel')
    expect(q('[data-change-plan-hover-path]')).toBeNull()
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

// ---- 一键排版不能重改（canRevise）与排版卡清单头 ----
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

it('批注的元素名走元素树同一个显示名：mathtext 读作 min⁻¹（含引擎截断、`$` 不成对的），普通名照旧，没有名字退回「这一处」', async () => {
  const { noteLabelOf } = await import('./noteTarget')
  expect(noteLabelOf('曲线 "k = 0.05 min$^{-1}$"')).toBe('曲线 "k = 0.05 min⁻¹"')
  const cut = noteLabelOf('曲线 "k = 0.05 min$^{-1…')
  expect(cut).toContain('min⁻¹')
  expect(cut).not.toContain('$')
  expect(noteLabelOf('图例')).toBe('图例')
  // 引擎截断后仍保留收尾引号（manifest.py）：在省略号前补齐，引号原位，结果里不露 `$` / 花括号
  const quoted = noteLabelOf('曲线 “k = 0.05 min$^{-1…”')
  expect(quoted).toContain('min⁻¹')
  expect(quoted).not.toMatch(/[$}]/)
  expect(quoted.endsWith('”')).toBe(true)
  // 没被截断的名字一个字不动：字面的 `$` 照旧显示
  expect(noteLabelOf('标题 “price $5”')).toContain('$5')
  expect(noteLabelOf('')).toBe('这一处')
  expect(noteLabelOf(undefined)).toBe('这一处')
})

// ---- 脚本方案卡（ADR 0206 PR 6；修订 2026-11-28 起也能批注重改） ----
const scriptPlan = (): ChangePlan => ({
  ...plan('S', 1),
  kind: 'script',
  source: 'assistant',
  groups: [
    {
      key: 'script',
      kind: 'script',
      label: '改标题',
      comment_id: 'a1',
      atomic: true,
      changes: [],
      boxes: { after: [] },
      script: { rel: 'fig.py', base_sha256: 'aa', new_sha256: 'bb', diff: '--- a\n+++ b\n@@ -1 +1 @@\n-a\n+b\n', added: 1, removed: 1 },
    },
  ],
  preview: { before: '/S-b.png', after: '/S-a.png', manifest: '/api/change-plans/S/manifest' },
})
const scriptEntry = (over: Partial<PlanEntry> = {}) => entry({ from: 'assistant', instruction: '改标题', plan: scriptPlan(), ...over })
const radios = () => qa('[role="radiogroup"]').find((g) => g.textContent?.includes('脚本改动'))!
const radio = (label: string) => [...radios().querySelectorAll<HTMLElement>('[role="radio"]')].find((b) => b.textContent === label)!

it('canRevise：脚本方案（助手 / 审阅转出的）也能重改；一键排版仍不能', async () => {
  const { canRevise } = await import('./changePlanModel')
  expect(canRevise(scriptEntry())).toBe(true)
  expect(canRevise(scriptEntry({ from: 'review' }))).toBe(true)
  expect(canRevise(entry({ from: 'assistant' }))).toBe(true)
  expect(canRevise(layoutEntry())).toBe(false)
})

it('脚本方案弹窗（高级版）：右上一排「对比图 / 脚本改动 / 批注」，底栏「写批注」+「应用」；批注视图只留「按批注重改」，元素表取方案自己存的那份', () => {
  status(PRO)
  put(scriptEntry())
  render()
  expect(q('[data-change-plan-dialog]')).not.toBeNull()
  expect([...radios().querySelectorAll('[role="radio"]')].map((b) => b.textContent)).toEqual(['对比图', '脚本改动', '批注'])
  expect(q('[data-change-plan-write-note]')).not.toBeNull()
  expect(q('[data-change-plan-confirm]')).not.toBeNull()
  expect(q('[data-change-plan-script-meta]')).not.toBeNull()
  act(() => radio('脚本改动').click())
  expect(q('[data-change-plan-script-diff]')).not.toBeNull()
  act(() => radio('批注').click())
  expect(view()).toBe('annotate')
  expect(q('[data-change-plan-script-diff]')).toBeNull()
  expect(q('[data-change-plan-confirm]')).toBeNull()
  expect(q('[data-change-plan-rerun]')).not.toBeNull()
  // 点选用的是脚本方案「修改后」那张图自己的元素表（后端存的），不是「当前文件 + 补丁」
  expect(planManifest).toHaveBeenCalledWith('/api/change-plans/S/manifest', null, expect.anything())
  expect(previewSnapshot).not.toHaveBeenCalled()
  act(() => radio('对比图').click())
  expect(view()).toBe('compare')
})

it('脚本方案弹窗（免费用户）：「批注」带锁，点了不进入', () => {
  status(FREE)
  put(scriptEntry())
  render()
  act(() => radio('批注').click())
  expect(view()).toBe('compare')
})

it('脚本方案弹窗：授权入口没出现时只有「对比图 / 脚本改动」两项、没有「写批注」', () => {
  status({ enabled: false })
  put(scriptEntry())
  render()
  expect([...radios().querySelectorAll('[role="radio"]')].map((b) => b.textContent)).toEqual(['对比图', '脚本改动'])
  expect(q('[data-change-plan-write-note]')).toBeNull()
})

it('旧脚本方案没存元素表：不去按补丁出图，批注照旧可框选 / 整体', () => {
  status(PRO)
  put(scriptEntry({ plan: { ...scriptPlan(), preview: { before: '/S-b.png', after: '/S-a.png' } } }))
  render()
  act(() => radio('批注').click())
  expect(view()).toBe('annotate')
  expect(planManifest).not.toHaveBeenCalled()
  expect(previewSnapshot).not.toHaveBeenCalled()
})

// ---- 卡片上的批注入口（ADR 0206 §五修订 2026-11-27）：待确认的铅笔钮 / 已应用的「再改一版」 ----
const putCard = (e: PlanEntry) => act(() => useChangePlanStore.setState({ entries: { [e.key]: e }, order: [e.key], dialogKey: null }))
const renderBoth = () =>
  act(() =>
    root.render(
      <TooltipProvider>
        <ChangePlanCard entry={useChangePlanStore.getState().entries.a} />
        <ChangePlanDialog />
      </TooltipProvider>,
    ),
  )
/** 把图改成方案那一版并记下这次 commit：卡片 = 已应用且能撤销 */
const appliedEntry = (over: Partial<PlanEntry> = {}) => {
  const candidate = plan('p1', 1).candidate_patches
  act(() =>
    useDocumentStore.getState().commit(literal('应用'), (d) => {
      ;(d.objects.find((x) => x.id === 'p1') as PanelObject).overrides = candidate
    }),
  )
  return entry({ status: 'applied', appliedPatches: candidate, appliedEntry: useDocumentStore.getState().past.at(-1), ...over })
}

it('卡片入口：高级版待确认卡点铅笔钮 → 弹窗直接以批注模式打开；不传 view 的「查看」仍先看对比', () => {
  status(PRO)
  putCard(entry())
  renderBoth()
  const mark = q('[data-change-plan-mark]')!
  expect(mark.getAttribute('title')).toBe('在改好的图上标几处，再改一版')
  expect(mark.textContent).toBe('批注')
  expect(q('[data-change-plan-dialog]')).toBeNull()
  act(() => mark.click())
  expect(view()).toBe('annotate')
  act(() => useChangePlanStore.getState().closeDialog())
  act(() => q('[data-change-plan-view]')!.click())
  expect(view()).toBe('compare')
})

it('卡片入口：免费用户铅笔钮带锁，点了不开弹窗、不跳设置，只弹一句话 +「了解高级版 ›」', () => {
  status(FREE)
  putCard(entry())
  renderBoth()
  const lock = q('[data-premium-lock]')
  expect(lock).not.toBeNull()
  act(() => lock!.click())
  expect(q('[data-change-plan-dialog]')).toBeNull()
  expect(useChangePlanStore.getState().dialogKey).toBeNull()
  expect(useUiStore.getState().settingsOpen).toBe(false)
  expect(q('[data-premium-lock-ask]')!.textContent).toContain('了解高级版')
})

it('卡片入口：授权入口没上线时不画铅笔钮', () => {
  status({ enabled: false })
  putCard(entry())
  renderBoth()
  expect(q('[data-change-plan-mark]')).toBeNull()
  expect(q('[data-premium-lock]')).toBeNull()
})

it('卡片入口：一键排版卡没有铅笔钮 / 「再改一版」；脚本卡有（待确认「批注」，已应用「再改一版」）', () => {
  status(PRO)
  putCard(layoutEntry())
  renderBoth()
  expect(q('[data-change-plan-mark]')).toBeNull()
  expect(q('[data-change-plan-revise-again]')).toBeNull()
  putCard(scriptEntry())
  renderBoth()
  expect(q('[data-change-plan-mark]')).not.toBeNull()
  act(() => q('[data-change-plan-mark]')!.click())
  expect(view()).toBe('annotate')
  act(() => useChangePlanStore.getState().closeDialog())
  putCard(scriptEntry({ status: 'applied', plan: { ...scriptPlan(), applied: { backup: 'fig/1', after_sha256: 'bb' } } }))
  renderBoth()
  expect(q('[data-change-plan-undo]')).not.toBeNull()
  const again = q('[data-change-plan-revise-again]')!
  expect(again.textContent).toBe('再改一版')
  act(() => again.click())
  expect(view()).toBe('annotate')
  expect(q('[data-change-plan-confirm]')).toBeNull()
  expect(q('[data-change-plan-rerun]')).not.toBeNull()
  // 重改进行中：改脚本 Agent 占着脚本，「撤销」先收起，「再改一版」仍在（看进度）
  act(() => useChangePlanStore.getState().closeDialog())
  putCard({ ...useChangePlanStore.getState().entries.a, revising: 'p9' })
  renderBoth()
  expect(q('[data-change-plan-undo]')).toBeNull()
  expect(q('[data-change-plan-revise-again]')).not.toBeNull()
})

const overridesNow = () => (useDocumentStore.getState().doc.objects.find((x) => x.id === 'p1') as PanelObject).overrides
const CANDIDATE = [{ gid: 'ax.title', prop: 'fontsize', value: 14 }]
const noteIt = () => act(() => useChangePlanStore.getState().saveNote('a', { id: 'n1', anchor: { kind: 'overall' }, label: '整体', text: '整体再小一点' }))

it('「再改一版」：已应用可撤销的卡点了 → 只开批注弹窗，不撤回，文档仍是已应用', () => {
  status(PRO)
  const undo = vi.fn(() => true)
  putCard(appliedEntry())
  act(() => useChangePlanStore.setState({ undo }))
  renderBoth()
  const btn = q('[data-change-plan-revise-again]')!
  expect(btn.textContent).toBe('再改一版')
  expect(btn.getAttribute('title')).toBe('在改好的图上标几处，再改一版')
  act(() => btn.click())
  expect(view()).toBe('annotate')
  expect(undo).not.toHaveBeenCalled()
  expect(overridesNow()).toEqual(CANDIDATE)
})

it('已应用条目的弹窗：没有「应用」「放弃」，只有「按批注重改」；关弹窗什么都不发生', () => {
  status(PRO)
  const undo = vi.fn(() => true)
  putCard(appliedEntry())
  act(() => useChangePlanStore.setState({ undo }))
  renderBoth()
  act(() => q('[data-change-plan-revise-again]')!.click())
  expect(q('[data-change-plan-confirm]')).toBeNull()
  expect(q('[data-change-plan-discard]')).toBeNull()
  expect(q('[data-change-plan-rerun]')!.hasAttribute('disabled')).toBe(true)
  noteIt()
  expect(q('[data-change-plan-rerun]')!.textContent).toContain('按批注重改 · 1')
  expect(q('[data-change-plan-rerun]')!.dataset.variant).toBe('primary')
  act(() => useChangePlanStore.getState().closeDialog())
  expect(q('[data-change-plan-dialog]')).toBeNull()
  expect(undo).not.toHaveBeenCalled()
  expect(reviseApi).not.toHaveBeenCalled()
  expect(overridesNow()).toEqual(CANDIDATE)
  expect(useChangePlanStore.getState().entries.a.status).toBe('applied')
})

it('已应用条目写批注、点「按批注重改」：先发请求，图仍是已应用（拿到新版才撤回，见 store/changePlanRevise.test.ts）', async () => {
  status(PRO)
  putCard(appliedEntry())
  renderBoth()
  act(() => q('[data-change-plan-revise-again]')!.click())
  noteIt()
  await act(async () => {
    q('[data-change-plan-rerun]')!.click()
  })
  expect(overridesNow()).toEqual(CANDIDATE)
  expect(useChangePlanStore.getState().entries.a.status).toBe('applied')
  expect(useChangePlanStore.getState().entries.a.revising).toBe('p2')
  expect(reviseApi).toHaveBeenCalledTimes(1)
  expect(reviseApi.mock.calls[0][0]).toBe('p1')
})

it('已应用但撤不回（之后又改过图）：点「按批注重改」不发请求、文档不变、弹窗一句灰字', async () => {
  status(PRO)
  putCard(appliedEntry())
  renderBoth()
  act(() => q('[data-change-plan-revise-again]')!.click())
  noteIt()
  // 之后又在同一张图上记了一次历史（override 不变）：栈顶已不是那次应用
  act(() => useDocumentStore.getState().commit(literal('别的修改'), (d) => {
      d.objects[0].x = 3
    }))
  const before = overridesNow()
  await act(async () => {
    q('[data-change-plan-rerun]')!.click()
  })
  expect(reviseApi).not.toHaveBeenCalled()
  expect(overridesNow()).toEqual(before)
  expect(overridesNow()).toEqual(CANDIDATE)
  expect(q('[data-change-plan-dialog]')!.textContent).toContain('没法替你撤回')
  expect(useChangePlanStore.getState().entries.a.status).toBe('applied')
})

it('「再改一版」：免费用户带锁不进入、不撤回；不可撤销时不出现', () => {
  status(FREE)
  const undo = vi.fn(() => true)
  putCard(appliedEntry())
  act(() => useChangePlanStore.setState({ undo }))
  renderBoth()
  act(() => q('[data-premium-lock]')!.click())
  expect(undo).not.toHaveBeenCalled()
  expect(q('[data-change-plan-dialog]')).toBeNull()

  // 栈顶不是应用那次 commit：没有「撤销」，也就没有「再改一版」
  status(PRO)
  putCard({ ...useChangePlanStore.getState().entries.a, appliedEntry: undefined })
  renderBoth()
  expect(q('[data-change-plan-undo]')).toBeNull()
  expect(q('[data-change-plan-revise-again]')).toBeNull()
})

// ---- 2026-11-28：重改过程看得见、第 2 版起对比上一版、首次提示进过批注即已读 ----
const ver = (id: string, n: number) => ({ planId: id, plan: plan(id, n), beforeSrc: `/${id}-b.png`, afterSrc: `/${id}-a.png`, unchecked: {} })

it('首次提示：进过批注视图就记已读，回到对比（如重改完）不再弹', () => {
  status(PRO)
  put(entry())
  render()
  expect(q('[data-change-plan-first-hint]')).not.toBeNull()
  act(() => q('[data-change-plan-write-note]')!.click())
  expect(view()).toBe('annotate')
  act(() => useChangePlanStore.getState().closeDialog())
  clearHintOnlyState()
  put(entry({ planId: 'p2', plan: plan('p2', 2), beforeSrc: '/p2-b.png', afterSrc: '/p2-a.png', versions: [ver('p1', 1), ver('p2', 2)] }))
  render()
  expect(view()).toBe('compare')
  expect(q('[data-change-plan-first-hint]')).toBeNull()
})
/** 只换一份新弹窗（不清本机记的「已读」） */
function clearHintOnlyState() {
  act(() => root.unmount())
  root = createRoot(host)
}

it('重改进行中：舞台换成「正在作图」（坐标纸 + 小画 + 三步清单），不是一动不动的旧图', () => {
  status(PRO)
  put(entry({ revising: 'p2', phase: 'model' }))
  act(() => useChangePlanStore.getState().openDialog('a', { view: 'annotate' }))
  render()
  expect(q('[data-change-plan-stage]')!.dataset.changePlanStage).toBe('revising')
  expect(q('[data-change-plan-stage] [data-change-plan-frost]')).not.toBeNull()
  expect(q('[data-change-plan-stage] [data-change-plan-steps]')).not.toBeNull()
  expect(q('[data-change-plan-stage] img')).toBeNull()
  expect(q('[data-change-plan-rerun]')!.textContent).toContain('正在')
})

it('第 2 版起：左侧默认是上一版的「修改后」，两侧标「第 1 版 / 第 2 版」；切回第 1 版看的是原图', () => {
  status(PRO)
  put(entry({ planId: 'p2', plan: plan('p2', 2), beforeSrc: '/p2-b.png', afterSrc: '/p2-a.png', versions: [ver('p1', 1), ver('p2', 2)] }))
  render()
  const imgs = qa('[data-change-plan-stage] img').map((i) => i.getAttribute('src'))
  expect(imgs[0]).toBe('/p1-a.png')
  expect(imgs[1]).toBe('/p2-a.png')
  const stage = q('[data-change-plan-stage]')!.textContent!
  expect(stage).toContain('第 1 版')
  expect(stage).toContain('第 2 版')
  expect(stage).not.toContain('修改前')
  const tabs = qa('[role="radiogroup"][aria-label="方案版本"] [role="radio"]')
  act(() => tabs[0].click())
  expect(qa('[data-change-plan-stage] img')[0].getAttribute('src')).toBe('/p1-b.png')
  expect(q('[data-change-plan-stage]')!.textContent).toContain('修改前')
})

it('第 2 版起：左侧的改动框按上一版里的位置画（上一版元素表取到之前用上一版记的框）', () => {
  status(PRO)
  put(entry({ planId: 'p2', plan: plan('p2', 2), beforeSrc: '/p2-b.png', afterSrc: '/p2-a.png', versions: [ver('p1', 1), ver('p2', 2)] }))
  render()
  // 滑动对比（分隔线在中间，这一处整个在线左）：只画左侧那个框，是第 1 版「修改后」的位置 [0.1, 0.1, 0.3, 0.2]，不是原图的 [0.1, 0.1, 0.2, 0.2]
  const boxes = qa('[data-change-plan-box="g1"]')
  expect(boxes).toHaveLength(1)
  expect(boxes[0].style.width).toBe('30%')
})

it('第 2 版一组都没勾：「修改后」就是原图，左侧也回到原图、标「修改前 / 修改后」', () => {
  status(PRO)
  put(entry({ planId: 'p2', plan: plan('p2', 2), beforeSrc: '/p2-b.png', afterSrc: '/p2-a.png', unchecked: { g1: true }, versions: [ver('p1', 1), ver('p2', 2)] }))
  render()
  const imgs = qa('[data-change-plan-stage] img').map((i) => i.getAttribute('src'))
  expect(imgs).toEqual(['/p2-b.png', '/p2-b.png'])
  expect(q('[data-change-plan-stage]')!.textContent).toContain('修改前')
})

it('重改进行中：右栏「整体批注」先不接（舞台上没有图可锚批注框）', () => {
  status(PRO)
  put(entry({ revising: 'p2' }))
  act(() => useChangePlanStore.getState().openDialog('a', { view: 'annotate' }))
  render()
  expect(q('[data-change-plan-note-overall]')!.hasAttribute('disabled')).toBe(true)
})

