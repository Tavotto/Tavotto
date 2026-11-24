/**
 * 审阅页签入口与收尾（ADR 0206 PR 3）：悬浮胶囊出现条件、点击交给助手、没连 AI 的引导、应用后标已处理 / 撤销还原、
 * 回复草稿模板与发送、更新到同一链接。判据的主语是**哪些接口被调用**与**DOM 里有什么**；选择器只认 data-*。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { AiCapabilities, ChangePlan, ChangePlanChange, ReviewComment, ReviewShareDetail, ReviewShareSummary } from '@/lib/api'

vi.mock('@/hooks/useVariantPng', () => ({ useVariantPng: () => ({ url: null, loading: false, approximate: false }), variantNeedsRender: () => false }))
vi.mock('@/lib/reviewRender', async (orig) => ({
  ...(await orig<typeof import('@/lib/reviewRender')>()),
  buildReviewRender: () => ({ canvas: { page_w_mm: 100, page_h_mm: 80, objects: [] } }),
}))
const createChangePlan = vi.fn()
const getChangePlan = vi.fn()
const resolveReviewComment = vi.fn()
const replyReviewComment = vi.fn()
const updateReview = vi.fn()
const fetchReviewStatus = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createChangePlan: (...a: unknown[]) => createChangePlan(...a),
  getChangePlan: (...a: unknown[]) => getChangePlan(...a),
  discardChangePlan: async () => ({ discarded: true }),
  resolveReviewComment: (...a: unknown[]) => resolveReviewComment(...a),
  replyReviewComment: (...a: unknown[]) => replyReviewComment(...a),
  updateReview: (...a: unknown[]) => updateReview(...a),
  fetchReviewStatus: (...a: unknown[]) => fetchReviewStatus(...a),
  dismissReview: async () => ({}),
  fetchReviewChanged: async () => ({ changed: !linkCurrent }),
  fetchReviewFor: async () => ({ share: SHARE }),
  fetchReviewShare: async () => detailOf(server),
  refreshReview: async () => ({ ok: true, unread: 0 }),
  markReviewRead: async () => ({}),
  fetchReviewUnread: async () => ({ total: 0, latest: null }),
  fetchReviewQuota: async () => ({ new_left: 1 }),
  fetchReviewShares: async () => ({ shares: [] }),
}))
const connectAi = vi.fn()
vi.mock('@/store/connectAiStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/connectAiStore')>()),
  requestConnectAi: (...a: unknown[]) => connectAi(...a),
}))

import { ReviewPanel } from '@/components/review/ReviewPanel'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { draftReply } from '@/lib/changePlanReplyDraft'
import { useAiStore } from '@/store/aiStore'
import { useChangePlanStore, type PlanEntry } from '@/store/changePlanStore'
import { setChangePlanUndoRunner } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { useReviewStore } from '@/store/reviewStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { groupViews } from './changePlanModel'
import { elementLabelOf, useReviewResolveSync } from './reviewSync'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 404 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as never

const SHARE: ReviewShareSummary = {
  share_id: 'sh_1',
  review_url: 'https://review.tavotto.cn/r/x',
  kind: 'figure',
  target_id: 'fig3',
  title: 'Figure 3',
  note: '',
  latest_version: 1,
  created_at: '2026-11-14T06:20:00Z',
  updated_at: '2026-11-15T06:20:00Z',
  expires_at: '2099-12-15T06:20:00Z',
  revoked: false,
  expired: false,
  comment_count: 3,
  open_count: 2,
  unread: 0,
}
const cm = (id: string, over: Partial<ReviewComment> = {}): ReviewComment => ({
  id,
  v: 1,
  anchor: { kind: 'overall' },
  author: '林老师',
  text: `意见 ${id}`,
  resolved: false,
  created_at: '2026-11-15T06:32:00Z',
  replies: [],
  ...over,
})
let server: ReviewComment[]
/** 审阅链接上的版本是不是最新（引擎按画布比对的结果）：更新成功之后才是 */
let linkCurrent = false
const detailOf = (comments: ReviewComment[]): ReviewShareDetail => ({
  ...SHARE,
  versions: [
    {
      v: 1,
      created_at: SHARE.updated_at,
      elements: [
        { gid: 'ax.title', label: '标题', bbox: [0, 0, 0.1, 0.1] },
        { gid: 'ax.legend', label: '图例', bbox: [0, 0, 0.1, 0.1] },
      ],
    },
  ],
  comments,
  verdicts: [],
})

const BASE = [{ gid: 'ax.title', prop: 'fontsize', value: 9 }]
const ch = (gid: string, prop: string, before: unknown, after: unknown): ChangePlanChange => ({
  target_gid: gid,
  prop,
  before,
  after,
  patch: { gid, prop, value: after },
})
const PLAN: ChangePlan = {
  id: 'p1',
  version: 1,
  source: 'review',
  base: { target: 'f_p1', fingerprint: 'fp' },
  base_patches: BASE,
  candidate_patches: [
    { gid: 'ax.legend', prop: 'loc', value: 'lower right' },
    { gid: 'ax.title', prop: 'fontsize', value: 11 },
  ],
  groups: [
    { key: 'g1', label: 'x', comment_id: 'c2', atomic: false, changes: [ch('ax.title', 'fontsize', 9, 11)], boxes: { after: [] } },
    { key: 'g2', label: 'y', comment_id: 'c3', atomic: false, changes: [ch('ax.legend', 'loc', 'upper right', 'lower right')], boxes: { after: [] } },
  ],
  unhandled: [],
  preview: { before: '/b.png', after: '/a.png' },
}

const ds = () => useDocumentStore.getState()
const cps = () => useChangePlanStore.getState()
let host: HTMLDivElement
let root: Root
function Harness() {
  useReviewResolveSync()
  return <ReviewPanel />
}
const mount = async () => {
  await act(async () => root.render(<TooltipProvider><Harness /></TooltipProvider>))
}
const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)
const click = async (el: Element | null) => {
  expect(el).not.toBeNull()
  await act(async () => {
    el!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  })
}
const flush = async (ms = 50) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)))

function setReview(comments: ReviewComment[], over: Partial<ReturnType<typeof useReviewStore.getState>> = {}) {
  server = comments
  useReviewStore.setState({
    enabled: true,
    target: { kind: 'figure', id: 'fig3' },
    share: SHARE,
    detail: detailOf(comments),
    stage: 'idle',
    failure: null,
    changed: false,
    selectedId: null,
    ...over,
  })
}
const entry = (over: Partial<PlanEntry> = {}): PlanEntry => ({
  key: 'k1',
  pj: null,
  panelId: 'p1',
  fileId: 'f_p1',
  documentId: ds().documentId,
  loadSeq: ds().loadSeq,
  canvasId: ds().activeCanvasId,
  createdAt: 1,
  status: 'ready',
  source: { fileId: 'f_p1', script: 'fig.py' },
  plan: PLAN,
  unchecked: {},
  drafts: {},
  request: { share_id: 'sh_1', numbers: { c2: 2, c3: 3 } },
  comments: [],
  commentCount: 2,
  ...over,
})
const putEntry = (e: PlanEntry) => act(() => useChangePlanStore.setState({ entries: { [e.key]: e }, order: [e.key], dialogKey: null }))
const overridesNow = () => (ds().doc.objects.find((o) => o.id === 'p1') as PanelObject).overrides

beforeEach(async () => {
  vi.useFakeTimers()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  connectAi.mockReset()
  createChangePlan.mockReset().mockResolvedValue({ plan_id: 'p1', state: 'running' })
  getChangePlan.mockReset().mockResolvedValue({ plan_id: 'p1', state: 'running', phase: 'x' })
  replyReviewComment.mockReset().mockImplementation(async (_s: string, id: string, text: string) => {
    const c = server.find((x) => x.id === id)!
    return { ok: true, comment: { ...c, replies: [...c.replies, { id: 'r1', from: 'owner', author: 'me', text, created_at: '' }] } }
  })
  resolveReviewComment.mockReset().mockImplementation(async (_s: string, id: string, resolved: boolean) => {
    server = server.map((x) => (x.id === id ? { ...x, resolved } : x))
    return { ok: true, comment: server.find((x) => x.id === id) }
  })
  linkCurrent = false
  updateReview.mockReset().mockImplementation(async () => {
    linkCurrent = true
    return { ok: true }
  })
  fetchReviewStatus.mockReset().mockResolvedValue({ stage: 'done', failure: null, retryable: false, share: SHARE })
  setChangePlanUndoRunner(() => ds().undo())
  useChangePlanStore.setState({ entries: {}, order: [], dialogKey: null })
  useUiStore.setState({ rightTab: 'review', rightOpen: true, status: null })
  // 连着 AI（这个文件的多数用例不关心）
  useAiStore.setState({ caps: { vendors: [], connections: [{ id: 'c', state: 'ready' }], agents: [] } as unknown as AiCapabilities })
  await ds().switchDocument(emptyProject(), 'd_handoff')
  ds().commit(literal('放入'), (d) => {
    d.objects.push({ id: 'p1', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject)
  })
  useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
  vi.useRealTimers()
})

describe('悬浮胶囊', () => {
  it('有未处理意见才出现；全处理完 / 排版（多张图）里不出现', async () => {
    setReview([cm('c1', { resolved: true }), cm('c2')])
    await mount()
    expect(q('[data-change-plan-pill]')?.dataset.changePlanPill).toBe('run')
    expect(q('[data-change-plan-pill]')?.textContent).toBe('交给 Agent 修改')
    expect(q('[data-review-floating]')).not.toBeNull()

    act(() => setReview([cm('c1', { resolved: true })]))
    await flush()
    expect(q('[data-change-plan-pill]')).toBeNull()
    expect(q('[data-review-floating]')).toBeNull()

    act(() => {
      setReview([cm('c1'), cm('c2')])
      useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
    })
    await flush()
    expect(q('[data-change-plan-pill]')).toBeNull()
  })

  it('点了：切到助手页签，带分享记录与审阅页签上的编号（已处理的占着号）发起方案', async () => {
    setReview([cm('c1', { resolved: true }), cm('c2'), cm('c3')])
    await mount()
    await click(q('[data-change-plan-pill]'))
    await flush()
    expect(useUiStore.getState().rightTab).toBe('assistant')
    expect(createChangePlan).toHaveBeenCalledTimes(1)
    expect(createChangePlan.mock.calls[0][0]).toMatchObject({ share_id: 'sh_1', source: 'review' })
    const e = Object.values(cps().entries)[0]
    expect(e.request.numbers).toEqual({ c2: 2, c3: 3 })
    expect(e.comments.map((c) => c.id)).toEqual(['c2', 'c3'])
  })

  it('同一批意见重复点 = 聚焦已有的卡，不发第二个请求', async () => {
    setReview([cm('c1'), cm('c2')])
    await mount()
    await click(q('[data-change-plan-pill]'))
    await flush()
    act(() => useUiStore.getState().setRightTab('review'))
    await click(q('[data-change-plan-pill]'))
    await flush()
    expect(createChangePlan).toHaveBeenCalledTimes(1)
    expect(useUiStore.getState().rightTab).toBe('assistant')
  })

  it('没连 AI：走「连接 AI」引导、不发请求；连好后接着做', async () => {
    useAiStore.setState({ caps: { vendors: [], connections: [], agents: [] } as unknown as AiCapabilities })
    setReview([cm('c1')])
    await mount()
    await click(q('[data-change-plan-pill]'))
    await flush()
    expect(connectAi).toHaveBeenCalledTimes(1)
    expect(createChangePlan).not.toHaveBeenCalled()
    await act(async () => connectAi.mock.calls[0][0].onConnected())
    await flush()
    expect(createChangePlan).toHaveBeenCalledTimes(1)
    expect(useUiStore.getState().rightTab).toBe('assistant')
  })
})

describe('应用后收尾', () => {
  const applyIt = async () => {
    putEntry(entry())
    await act(async () => void cps().apply('k1'))
    await flush()
  }

  it('应用 → 对应意见自动标已处理（只标有生效改动的组），计数同步；撤销 → 只还原这次标的', async () => {
    // c1 是审阅者 / 用户早先自己标的，不在这次方案里
    setReview([cm('c1', { resolved: true }), cm('c2'), cm('c3'), cm('c4')])
    await mount()
    await applyIt()
    expect(overridesNow()).toEqual(PLAN.candidate_patches)
    expect(resolveReviewComment.mock.calls.map((c) => [c[1], c[2]])).toEqual([['c2', true], ['c3', true]])
    expect(cps().entries.k1.resolvedIds).toEqual(['c2', 'c3'])
    expect(q('[data-review-comment="c2"]')?.hasAttribute('data-review-resolved')).toBe(true)
    expect(q('[data-review-comment="c4"]')?.hasAttribute('data-review-resolved')).toBe(false)
    // 胶囊：新版还没发到链接（画布有改动）→ 更新到同一链接
    act(() => useReviewStore.setState({ changed: true }))
    await flush()
    expect(q('[data-change-plan-pill]')?.dataset.changePlanPill).toBe('update')

    resolveReviewComment.mockClear()
    await act(async () => void cps().undo('k1'))
    await flush()
    expect(overridesNow()).toEqual(BASE)
    expect(resolveReviewComment.mock.calls.map((c) => [c[1], c[2]])).toEqual([['c2', false], ['c3', false]])
    expect(server.find((c) => c.id === 'c1')?.resolved).toBe(true)
    expect(server.find((c) => c.id === 'c2')?.resolved).toBe(false)
    expect(cps().entries.k1.resolvedIds).toEqual([])
  })

  it('标已处理失败：一句灰字 + 重试，不自动反复请求；重试成功后收起', async () => {
    setReview([cm('c2'), cm('c3')])
    resolveReviewComment.mockRejectedValue(new Error('net'))
    await mount()
    await applyIt()
    await flush(500)
    expect(resolveReviewComment).toHaveBeenCalledTimes(2) // 每条各试一次，之后不再自己试
    expect(q('[data-change-plan-resolve-failed]')).not.toBeNull()
    expect(cps().entries.k1.resolveFailed).toBe(true)
    resolveReviewComment.mockImplementation(async (_s: string, id: string, resolved: boolean) => {
      server = server.map((x) => (x.id === id ? { ...x, resolved } : x))
      return { ok: true, comment: server.find((x) => x.id === id) }
    })
    await click(q('[data-change-plan-resolve-retry]'))
    await flush()
    expect(q('[data-change-plan-resolve-failed]')).toBeNull()
    expect(cps().entries.k1.resolvedIds).toEqual(['c2', 'c3'])
  })

  it('等待中切图：请求仍发往原分享、resolvedIds 照记，撤销也还原到原分享', async () => {
    setReview([cm('c2'), cm('c3')])
    await mount()
    putEntry(entry())
    let release: () => void = () => {}
    const gate = new Promise<void>((r) => (release = r))
    resolveReviewComment.mockImplementation(async (_s: string, id: string, resolved: boolean) => {
      await gate
      server = server.map((x) => (x.id === id ? { ...x, resolved } : x))
      return { ok: true, comment: server.find((x) => x.id === id) }
    })
    await act(async () => void cps().apply('k1'))
    await flush()
    // 此刻用户切到了另一张图 / 另一份分享
    act(() => useReviewStore.setState({ share: { ...SHARE, share_id: 'sh_other' }, detail: { ...detailOf([cm('x1')]), share_id: 'sh_other' } }))
    release()
    await flush(200)
    expect(resolveReviewComment.mock.calls.map((c) => [c[0], c[1], c[2]])).toEqual([['sh_1', 'c2', true], ['sh_1', 'c3', true]])
    expect(cps().entries.k1.resolvedIds).toEqual(['c2', 'c3'])
    // 切回来，撤销：发往原分享
    act(() => setReview(server))
    resolveReviewComment.mockClear()
    await act(async () => void cps().undo('k1'))
    await flush(200)
    expect(resolveReviewComment.mock.calls.map((c) => [c[0], c[1], c[2]])).toEqual([['sh_1', 'c2', false], ['sh_1', 'c3', false]])
    expect(cps().entries.k1.resolvedIds).toEqual([])
  })

  it('取消勾选的组不标已处理', async () => {
    setReview([cm('c2'), cm('c3')])
    await mount()
    putEntry(entry({ unchecked: { g2: true } }))
    await act(async () => void cps().apply('k1'))
    await flush()
    expect(resolveReviewComment.mock.calls.map((c) => c[1])).toEqual(['c2'])
  })
})

describe('回复草稿', () => {
  it('被处理的意见下出现收拢的草稿框，内容由改动现算；改了再点「发送」才调回复端点', async () => {
    setReview([cm('c2'), cm('c3')])
    await mount()
    putEntry(entry())
    await act(async () => void cps().apply('k1'))
    await flush()
    const box = (id: string) => q(`[data-review-comment="${id}"] [data-review-reply-draft-input]`) as HTMLTextAreaElement
    expect(box('c2').value).toBe('已把标题的字号从 9 pt 改到 11 pt')
    expect(box('c3').value).toBe('已把图例挪到右下角')
    expect(q('[data-review-reply-draft-send]')).toBeNull() // 没获焦 = 收拢，没有发送
    expect(replyReviewComment).not.toHaveBeenCalled()

    await act(async () => box('c2').focus())
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
      set.call(box('c2'), '已改到 11 pt，谢谢')
      box('c2').dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click(q('[data-review-reply-draft-send]'))
    await flush()
    expect(replyReviewComment).toHaveBeenCalledWith('sh_1', 'c2', '已改到 11 pt，谢谢')
    // 发过的不再出草稿框，另一条不受影响
    expect(q('[data-review-comment="c2"] [data-review-reply-draft]')).toBeNull()
    expect(box('c3')).not.toBeNull()
  })

  it('模板：字号 / 线宽 / 颜色 / 显隐 / 图例位置 / 文字 / 加粗 / 认不出的 → 兜底', () => {
    const label = (g: string) => ({ 'ax.y': 'y 轴标签', 'ax.legend': '图例', 'ax.c': '曲线' })[g]
    const d = (...c: ChangePlanChange[]) => draftReply(c, label)
    expect(d(ch('ax.y', 'fontsize', 9, 11))).toBe('已把y 轴标签的字号从 9 pt 改到 11 pt')
    expect(d(ch('ax.y', 'fontsize', undefined, 11))).toBe('已把y 轴标签的字号改到 11 pt')
    expect(d(ch('ax.c', 'linewidth', 1, 2))).toBe('已把曲线的线宽从 1 改到 2')
    expect(d(ch('ax.c', 'color', '#000', '#f00'))).toBe('已改了曲线的颜色')
    expect(d(ch('ax.legend', 'visible', true, false))).toBe('已隐藏图例')
    expect(d(ch('ax.legend', 'frameon', true, false))).toBe('已去掉图例的边框')
    expect(d(ch('ax.legend', 'loc', 'upper right', 'lower right'))).toBe('已把图例挪到右下角')
    expect(d(ch('ax.legend', 'loc', 1, 4))).toBe('已把图例挪到右下角')
    expect(d(ch('ax.y', 'text', 'a', 'Stress (MPa)'))).toBe('已把y 轴标签的文字改成「Stress (MPa)」')
    expect(d(ch('ax.y', 'weight', 'normal', 'bold'))).toBe('已把y 轴标签加粗')
    expect(d(ch('ax.y', 'weird_prop', 1, 2))).toBe('已按意见调整')
    expect(d(ch('nope', 'visible', false, true))).toBe('已显示这一处')
    // 多处：最多两句，其余并成一个数
    expect(d(ch('ax.y', 'fontsize', 9, 11), ch('ax.legend', 'visible', true, false), ch('ax.c', 'linewidth', 1, 2))).toBe(
      '已把y 轴标签的字号从 9 pt 改到 11 pt；已隐藏图例（另有 1 处）',
    )
    // 认不出的夹在里面：只说认得出的
    expect(d(ch('ax.c', 'weird_prop', 1, 2), ch('ax.legend', 'visible', true, false))).toBe('已隐藏图例')
  })
})

describe('更新到同一链接', () => {
  const appliedSetup = async () => {
    setReview([cm('c2'), cm('c3')])
    await mount()
    putEntry(entry())
    await act(async () => void cps().apply('k1'))
    await flush()
    act(() => useReviewStore.setState({ changed: true }))
    await flush()
  }

  it('应用后胶囊换成「更新到同一链接」，只有点了才调 update；更新完变灰「链接已更新」', async () => {
    await appliedSetup()
    const pill = () => q('[data-change-plan-pill]')
    expect(pill()?.dataset.changePlanPill).toBe('update')
    expect(pill()?.textContent).toBe('更新到同一链接')
    expect(updateReview).not.toHaveBeenCalled()
    await click(pill())
    await flush(800)
    expect(updateReview).toHaveBeenCalledTimes(1)
    expect(updateReview.mock.calls[0][0]).toBe('sh_1')
    expect(pill()?.dataset.changePlanPill).toBe('updated')
    expect(pill()?.textContent).toBe('链接已更新')
    expect(pill()?.hasAttribute('disabled')).toBe(true)
  })

  it('失败：一句灰字，胶囊还在、可再点', async () => {
    await appliedSetup()
    updateReview.mockImplementation(async () => ({ ok: true }))
    fetchReviewStatus.mockResolvedValue({ stage: 'failed', failure: 'network', retryable: true, share: SHARE })
    await click(q('[data-change-plan-pill]'))
    await flush(800)
    expect(q('[data-change-plan-pill-failure]')).not.toBeNull()
    expect(q('[data-change-plan-pill]')?.dataset.changePlanPill).toBe('update')
    expect(q('[data-change-plan-pill]')?.hasAttribute('disabled')).toBe(false)
  })
})

it('卡上的意见编号取审阅页签给的（没有才按这批里的位置）', () => {
  const e = entry({ comments: [cm('c2'), cm('c3')] })
  expect(groupViews(e).map((g) => g.commentNumbers)).toEqual([[2], [3]])
  expect(groupViews({ ...e, request: { share_id: 'sh_1' } }).map((g) => g.commentNumbers)).toEqual([[1], [2]])
})

it('回复草稿里的元素名走元素树同一个显示名：mathtext 读作 min⁻¹，普通标签原样，没有名字退回「这一处」', () => {
  const detail = detailOf([])
  detail.versions[0].elements.push(
    { gid: 'ax.k', label: '曲线 “k = 0.1 min$^{-1}$”', bbox: [0, 0, 0.1, 0.1] },
    { gid: 'ax.blank', label: '', bbox: [0, 0, 0.1, 0.1] },
  )
  const labelOf = elementLabelOf(detail)
  const text = draftReply([ch('ax.k', 'color', '#000', '#f00')], labelOf)
  expect(text).toContain('min⁻¹')
  expect(text).not.toContain('$')
  expect(draftReply([ch('ax.legend', 'color', '#000', '#f00')], labelOf)).toBe('已改了图例的颜色')
  expect(draftReply([ch('ax.blank', 'color', '#000', '#f00')], labelOf)).toBe('已改了这一处的颜色')
})
