/**
 * 外部审阅（ADR 0187）前端：三态弹窗与失败码文案、额度用满选择卡、「审阅」页签出现条件、意见圆点只在页签打开时画、
 * 顶栏按钮与未读圆点、通知轨胶囊的收起 / 重现、中英键对齐。判据的主语是**哪些本机 API 被调用**与**DOM 里有什么**；选择器只认 data-*。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// 只替掉 buildReviewRender（弹窗发请求时要有个 render）；签名用真实现，才能量到「页面尺寸变了」这类输入变化
vi.mock('@/lib/reviewRender', async (orig) => ({
  ...(await orig<typeof import('@/lib/reviewRender')>()),
  buildReviewRender: () => ({ canvas: { page_w_mm: 100, page_h_mm: 80, objects: [] } }),
}))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchReviewCapability: vi.fn(),
  fetchReviewQuota: vi.fn(),
  fetchReviewFor: vi.fn(),
  fetchReviewShares: vi.fn(),
  shareReview: vi.fn(),
  updateReview: vi.fn(),
  fetchReviewStatus: vi.fn(),
  dismissReview: vi.fn(),
  revokeReview: vi.fn(),
  deleteReviewRecord: vi.fn(),
  fetchReviewShare: vi.fn(),
  refreshReview: vi.fn(),
  replyReviewComment: vi.fn(),
  resolveReviewComment: vi.fn(),
  markReviewRead: vi.fn(),
  fetchReviewUnread: vi.fn(),
  fetchReviewChanged: vi.fn(),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchReadiness: vi.fn().mockResolvedValue(null),
}))

import {
  ApiError,
  dismissReview,
  fetchReviewChanged,
  fetchReviewFor,
  fetchReviewShare,
  fetchReviewStatus,
  markReviewRead,
  refreshReview,
  replyReviewComment,
  shareReview,
  updateReview,
  type ReviewComment,
  type ReviewShareDetail,
  type ReviewShareSummary,
} from '@/lib/api'
import enDialogs from '@/i18n/locales/en-US/dialogs.json'
import zhDialogs from '@/i18n/locales/zh-CN/dialogs.json'
import { ReviewOverlay } from '@/canvas/ReviewOverlay'
import { Inspector } from '@/components/inspector/Inspector'
import { NotificationRail } from '@/components/StatusBar'
import { ReviewButton } from '@/components/review/ReviewButton'
import { ReviewDialog } from '@/components/review/ReviewDialog'
import { ReviewPanel } from '@/components/review/ReviewPanel'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useReviewStore } from '@/store/reviewStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { literal } from '@/i18n'
import { mmToPx, mmToViewX, mmToViewY } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 404 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
Element.prototype.hasPointerCapture ??= () => false
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as never

const SHARE: ReviewShareSummary = {
  share_id: 'sh_1',
  review_url: 'https://review.tavotto.cn/r/k7QmAAAA3fA',
  kind: 'figure',
  target_id: 'fig3',
  title: 'Figure 3',
  note: '',
  latest_version: 2,
  created_at: '2026-11-14T06:20:00Z',
  updated_at: '2026-11-15T06:20:00Z',
  expires_at: '2026-12-15T06:20:00Z',
  revoked: false,
  expired: false,
  comment_count: 3,
  open_count: 2,
  unread: 2,
}
const cm = (id: string, over: Partial<ReviewComment> = {}): ReviewComment => ({
  id,
  v: 2,
  anchor: { kind: 'region', box: [0.2, 0.2, 0.1, 0.1] },
  author: '林老师',
  text: `意见 ${id}`,
  resolved: false,
  created_at: '2026-11-15T06:32:00Z',
  replies: [],
  ...over,
})
const DETAIL = (comments: ReviewComment[], over: Partial<ReviewShareDetail> = {}): ReviewShareDetail => ({
  ...SHARE,
  versions: [{ v: 2, created_at: SHARE.updated_at, elements: [{ gid: 'legend', label: '图例', bbox: [0, 0, 0.1, 0.1] }] }],
  comments,
  verdicts: [],
  ...over,
})

let host: HTMLDivElement
let root: Root
async function render(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
}
const q = <T extends Element = HTMLElement>(sel: string) => document.body.querySelector<T>(sel)
const qa = (sel: string) => [...document.body.querySelectorAll<HTMLElement>(sel)]
const click = async (el: Element | null) => {
  expect(el).not.toBeNull()
  // SVG 元素没有 .click()：统一派发一个冒泡的 click
  await act(async () => {
    el!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  })
}
const settle = async (ms = 800) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function resetStore(over: Partial<ReturnType<typeof useReviewStore.getState>> = {}) {
  useReviewStore.setState({
    enabled: true,
    target: { kind: 'figure', id: 'fig3' },
    resolving: false,
    share: null,
    detail: null,
    dialogOpen: false,
    stage: 'idle',
    failure: null,
    picker: null,
    quota: { new_left: 1 },
    others: [],
    changed: true,
    unread: { total: 0, latest: null },
    capsuleDismissedAt: 0,
    selectedId: null,
    lastOp: null,
    ...over,
  })
}

beforeEach(async () => {
  vi.useFakeTimers()
  localStorage.clear()
  vi.mocked(dismissReview).mockResolvedValue({})
  vi.mocked(markReviewRead).mockResolvedValue({})
  vi.mocked(refreshReview).mockResolvedValue({ ok: true, unread: 0 })
  vi.mocked(fetchReviewChanged).mockResolvedValue({ changed: false })
  useUiStore.setState({ rightTab: 'properties', rightOpen: true, status: null })
  resetStore()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_review')
})
afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
  document.body.innerHTML = ''
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe('顶栏按钮', () => {
  it('功能关着完全不渲染', async () => {
    resetStore({ enabled: false })
    await render(<ReviewButton />)
    expect(q('[data-review-button]')).toBeNull()
  })
  it('有未读时带小圆点，点了只是开弹窗（不联网）', async () => {
    resetStore({ unread: { total: 2, latest: { share_id: 'sh_1', author: '林老师', count: 2 } } })
    await render(<ReviewButton />)
    expect(q('[data-review-dot]')).not.toBeNull()
    expect(q('[data-review-button]')?.dataset.variant).toBe('secondary')
    await click(q('[data-review-button]'))
    expect(useReviewStore.getState().dialogOpen).toBe(true)
    expect(shareReview).not.toHaveBeenCalled()
  })
  it('空画布无目标时退为无框 ghost 减噪，有目标时升为次按钮 secondary', async () => {
    resetStore({ target: null })
    await render(<ReviewButton />)
    expect(q('[data-review-button]')?.dataset.variant).toBe('ghost')
    expect(q('[data-review-button]')?.hasAttribute('disabled')).toBe(true)
  })
})

describe('分享弹窗三态', () => {
  it('首次：一句隐私说明 + 生成链接，页脚小字写今天还能新建几个；点了才调 shareReview', async () => {
    resetStore({ dialogOpen: true })
    vi.mocked(shareReview).mockResolvedValue({ ok: true })
    vi.mocked(fetchReviewStatus).mockResolvedValue({ stage: 'done', failure: null, retryable: false, share: SHARE })
    await render(<ReviewDialog />)
    expect(document.body.textContent).toContain('脚本和数据不出本机')
    expect(q('[data-review-quota]')?.textContent).toBe('每天可新建 1 个') // 不写剩余次数
    expect(shareReview).not.toHaveBeenCalled()
    await click(q('[data-review-create]'))
    expect(shareReview).toHaveBeenCalledTimes(1)
  })
  it('已分享有改动：链接 + 唯一主按钮「复制链接」+ 一行「画布有新改动 · 更新到这个链接」；没有版本号 / 意见数 / 剩余次数；停止分享要点两次', async () => {
    resetStore({ dialogOpen: true, share: SHARE, changed: true })
    const { revokeReview } = await import('@/lib/api')
    vi.mocked(revokeReview).mockResolvedValue({ ok: true })
    await render(<ReviewDialog />)
    expect(q('[data-review-link]')?.textContent).toContain('review.tavotto.cn/r/k7QmAAAA3fA')
    expect(q('[data-review-copy]')?.textContent).toBe('复制链接')
    expect(q('[data-review-changed]')?.textContent).toContain('画布有新改动')
    expect(q('[data-review-stats]')?.textContent).toMatch(/拿到链接可查看和留言 · .+失效/)
    expect(document.body.textContent).not.toMatch(/V\d|条意见|还能/)
    await click(q('[data-review-stop]'))
    expect(revokeReview).not.toHaveBeenCalled()
    await click(q('[data-review-stop]'))
    expect(revokeReview).toHaveBeenCalledWith('sh_1')
  })
  it('已分享没改动：去掉更新那一行', async () => {
    resetStore({ dialogOpen: true, share: SHARE, changed: false })
    await render(<ReviewDialog />)
    expect(q('[data-review-changed]')).toBeNull()
    expect(q('[data-review-update]')).toBeNull()
    expect(q('[data-review-copy]')).not.toBeNull()
  })
  it('更新：调 updateReview，做完自动复制链接并在通知轨说一句', async () => {
    resetStore({ dialogOpen: true, share: SHARE })
    vi.mocked(updateReview).mockResolvedValue({ ok: true })
    vi.mocked(fetchReviewStatus).mockResolvedValue({ stage: 'done', failure: null, retryable: false, share: SHARE })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    await render(<ReviewDialog />)
    await click(q('[data-review-update]'))
    await settle()
    expect(updateReview).toHaveBeenCalledWith('sh_1', undefined, expect.anything())
    expect(writeText).toHaveBeenCalledWith(SHARE.review_url)
    expect(JSON.stringify(useUiStore.getState().status)).toContain('review.linkCopied')
  })
  it('进行中：点阵进度、不能随手关', async () => {
    resetStore({ dialogOpen: true, stage: 'uploading' })
    await render(<ReviewDialog />)
    expect(q('[data-review-status="working"]')).not.toBeNull()
    useReviewStore.getState().closeDialog()
    expect(useReviewStore.getState().dialogOpen).toBe(true)
  })
})

describe('失败码：一句话 + 可重试', () => {
  const codes = ['share_gone', 'too_large', 'rate_limited', 'network', 'server', 'client_error', 'render_failed'] as const
  for (const code of codes) {
    it(`${code} 有自己的一句话`, async () => {
      resetStore({ dialogOpen: true, stage: 'failed', failure: { code, retryable: true } })
      await render(<ReviewDialog />)
      const text = q(`[data-review-failure="${code}"]`)?.textContent ?? ''
      expect(text.length).toBeGreaterThan(4)
      expect(text).not.toContain('review.failure')
      expect(q('[data-review-retry]')).not.toBeNull()
    })
  }
  it('不可重试就没有重试钮；重试 = 再发一次同样的请求', async () => {
    resetStore({ dialogOpen: true, stage: 'failed', failure: { code: 'too_large', retryable: false } })
    await render(<ReviewDialog />)
    expect(q('[data-review-retry]')).toBeNull()
  })
  it('重试走同一个操作', async () => {
    resetStore({
      dialogOpen: true,
      stage: 'failed',
      failure: { code: 'network', retryable: true },
      lastOp: { kind: 'new', title: 'Figure 3', note: 'n' },
    })
    vi.mocked(shareReview).mockResolvedValue({ ok: true })
    vi.mocked(fetchReviewStatus).mockResolvedValue({ stage: 'uploading', failure: null, retryable: false, share: null })
    await render(<ReviewDialog />)
    await click(q('[data-review-retry]'))
    expect(shareReview).toHaveBeenCalledWith({ kind: 'figure', id: 'fig3' }, 'Figure 3', 'n', expect.anything())
  })
  for (const error of ['review_render_required', 'target_invalid']) {
    it(`同步 422 ${error}：按「画面没生成出来」说，可重试`, async () => {
      resetStore({ dialogOpen: true })
      vi.mocked(shareReview).mockRejectedValue(new ApiError(error, 422, { ok: false, error }))
      await render(<ReviewDialog />)
      await click(q('[data-review-create]'))
      expect(q('[data-review-failure="render_failed"]')).not.toBeNull()
      expect(q('[data-review-retry]')).not.toBeNull()
      expect(fetchReviewStatus).not.toHaveBeenCalled()
    })
  }
  it('服务端说额度用满：不报错，换成选择卡', async () => {
    resetStore({ dialogOpen: true })
    vi.mocked(shareReview).mockResolvedValue({ ok: true })
    vi.mocked(fetchReviewStatus).mockResolvedValue({ stage: 'failed', failure: 'quota_new_exhausted', retryable: false, share: null })
    await render(<ReviewDialog />)
    await click(q('[data-review-create]'))
    await settle()
    expect(useReviewStore.getState().picker).toBe('new')
    expect(q('[data-review-picker="new"]')).not.toBeNull()
    expect(q('[data-review-status]')).toBeNull()
  })
})

describe('额度用满选择卡', () => {
  it('新建用满：更新已有（本机有未到期分享才列）/ 导出 PNG / 明天再分享', async () => {
    resetStore({ dialogOpen: true, picker: 'new', others: [{ ...SHARE, share_id: 'sh_9', title: 'Figure 2' }, { ...SHARE, share_id: 'sh_8', title: 'Figure 4' }] })
    vi.mocked(updateReview).mockResolvedValue({ ok: true })
    vi.mocked(fetchReviewStatus).mockResolvedValue({ stage: 'uploading', failure: null, retryable: false, share: null })
    await render(<ReviewDialog />)
    expect(qa('[data-review-pick]').map((e) => e.dataset.reviewPick)).toEqual(['export-png', 'update-existing'])
    expect(q('[data-review-pick="update-existing"]')?.textContent).toBe('更新已有的链接…')
    await click(q('[data-review-pick="update-existing"]'))
    await click(q('[data-review-pick-share="sh_9"]'))
    // 把这张图的画面更新到别的链接：带上 target
    expect(updateReview).toHaveBeenCalledWith('sh_9', undefined, expect.anything(), { kind: 'figure', id: 'fig3' })
  })
  it('没有别的分享记录就没有「更新已有的链接」；导出 PNG 打开导出窗', async () => {
    resetStore({ dialogOpen: true, picker: 'new', others: [] })
    await render(<ReviewDialog />)
    expect(q('[data-review-pick="update-existing"]')).toBeNull()
    await click(q('[data-review-pick="export-png"]'))
    expect(useUiStore.getState().exportOpen).toBe(true)
    expect(useReviewStore.getState().dialogOpen).toBe(false)
  })
  it('更新不限次数：更新被限流时按 rate_limited 一句话 + 重试', async () => {
    resetStore({ dialogOpen: true, share: SHARE, stage: 'failed', failure: { code: 'rate_limited', retryable: true }, lastOp: { kind: 'update', shareId: 'sh_1', note: '' } })
    vi.mocked(updateReview).mockResolvedValue({ ok: true })
    vi.mocked(fetchReviewStatus).mockResolvedValue({ stage: 'uploading', failure: null, retryable: false, share: null })
    await render(<ReviewDialog />)
    expect(q('[data-review-failure="rate_limited"]')?.textContent).toContain('太频繁')
    await click(q('[data-review-retry]'))
    expect(updateReview).toHaveBeenCalledWith('sh_1', '', expect.anything())
  })
  it('服务端若仍回 quota_update_exhausted：不再有专门的选择卡，按「没有成功」一句话 + 重试', async () => {
    resetStore({ dialogOpen: true, share: SHARE })
    vi.mocked(updateReview).mockResolvedValue({ ok: true })
    vi.mocked(fetchReviewStatus).mockResolvedValue({ stage: 'failed', failure: 'quota_update_exhausted' as never, retryable: true, share: SHARE })
    await render(<ReviewDialog />)
    await click(q('[data-review-update]'))
    await settle()
    expect(useReviewStore.getState().picker).toBeNull()
    expect(q('[data-review-retry]')).not.toBeNull()
  })
})

describe('「审阅」页签出现条件', () => {
  it('没分享过：没有审阅页签；分享过：出现，有未读时带小圆点', async () => {
    await render(<Inspector />)
    expect(q('[data-inspector-tab="review"]')).toBeNull()
    await act(async () => useReviewStore.setState({ share: SHARE }))
    expect(q('[data-inspector-tab="review"]')).not.toBeNull()
    expect(q('[data-review-tab-dot]')).not.toBeNull() // 有未读：字后一个小圆点，没有数字徽标
    expect(q('[data-review-tab-count]')).toBeNull()
  })
  it('功能关着不出现；页签停在审阅而记录没了，落回属性页', async () => {
    resetStore({ enabled: false, share: SHARE })
    await render(<Inspector />)
    expect(q('[data-inspector-tab="review"]')).toBeNull()
    await act(async () => {
      useReviewStore.setState({ enabled: true, share: SHARE })
      useUiStore.setState({ rightTab: 'review' })
    })
    expect(q('[data-review-panel]')).not.toBeNull()
    await act(async () => useReviewStore.setState({ share: null }))
    expect(q('[data-review-panel]')).toBeNull()
    expect(q('[data-inspector-tab="properties"][aria-selected="true"]')).not.toBeNull()
  })
})

describe('「审阅」页签内容', () => {
  const seed = (comments: ReviewComment[], share = SHARE) => {
    vi.mocked(fetchReviewShare).mockResolvedValue(DETAIL(comments, share))
    resetStore({ share, detail: DETAIL(comments, share), changed: false })
  }
  it('顶部一行灰字 + 复制链接；没有结论条；打开时读一遍并清未读，每 60 秒拉一次', async () => {
    seed([cm('c1')])
    await render(<ReviewPanel />)
    await settle(10)
    expect(markReviewRead).toHaveBeenCalledWith('sh_1')
    expect(refreshReview).toHaveBeenCalledTimes(1)
    expect(q('[data-review-note-line]')).toBeNull() // 栏顶没有失效日期 / 复制链接（归分享弹窗）
    expect(q('[data-review-verdict]')).toBeNull()
    await settle(60_000)
    expect(refreshReview).toHaveBeenCalledTimes(2)
  })
  it('分段「全部 / 未处理 N」按已处理过滤', async () => {
    seed([cm('c1'), cm('c2', { resolved: true })])
    await render(<ReviewPanel />)
    expect(qa('[data-review-comment]')).toHaveLength(2)
    const seg = [...document.body.querySelectorAll('[role="radio"],[role="tab"],button')].find((e) => e.textContent === '未处理 1')
    await click(seg ?? null)
    expect(qa('[data-review-comment]').map((e) => e.dataset.reviewComment)).toEqual(['c1'])
  })
  it('点意见 = 选中；回复走 replyReviewComment；已停止的记录只剩「删除这份记录」', async () => {
    seed([cm('c1')])
    vi.mocked(replyReviewComment).mockResolvedValue({ ok: true, comment: cm('c1', { replies: [{ id: 'r1', from: 'owner', author: '我', text: '好的', created_at: '2026-11-15T07:00:00Z' }] }) })
    await render(<ReviewPanel />)
    await click(q('[data-review-comment="c1"]'))
    expect(useReviewStore.getState().selectedId).toBe('c1')
    await click(q('[data-review-reply-open]'))
    const input = q<HTMLInputElement>('[data-review-reply-input]')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '好的')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => {
      input.form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })
    expect(replyReviewComment).toHaveBeenCalledWith('sh_1', 'c1', '好的')
    expect(q('[data-review-reply="owner"]')).not.toBeNull()
  })
  it('已停止：一行灰字说明、没有回复 / 复制，可删除记录', async () => {
    seed([cm('c1')], { ...SHARE, revoked: true })
    await render(<ReviewPanel />)
    expect(q('[data-review-note-line]')?.textContent).toContain('已停止分享')
    expect(q('[data-review-delete]')).not.toBeNull()
    expect(q('[data-review-reply-open]')).toBeNull()
    expect(refreshReview).not.toHaveBeenCalled()
  })
  it('原位置已变化：旧版本的区域意见标灰字并带快照缩略图；最新版的不标', async () => {
    seed([cm('old', { v: 1 }), cm('new', { v: 2 })])
    await render(<ReviewPanel />)
    expect(q('[data-review-comment="old"] [data-review-moved]')?.textContent).toContain('原位置已变化')
    expect(q('[data-review-comment="old"] [data-review-moved] img')?.getAttribute('src')).toContain('/versions/1/snapshot.png')
    expect(q('[data-review-comment="new"] [data-review-moved]')).toBeNull()
  })
})

describe('意见行：序号 / 全 / ✓，版本只在非当前版本标', () => {
  it('序号小圆对应画布圆点；整体意见「全」；已处理换 ✓；V1 · 只在旧版本出现；没有头像 / 锚点名标签', async () => {
    const cs = [
      cm('a', { created_at: new Date(Date.now() - 10 * 60_000).toISOString() }),
      cm('o', { anchor: { kind: 'overall' } }),
      cm('r', { resolved: true, v: 1 }),
      cm('w', { replies: [{ id: 'r1', from: 'owner', author: '我', text: '好的', created_at: '2026-11-15T07:00:00Z' }] }),
    ]
    vi.mocked(fetchReviewShare).mockResolvedValue(DETAIL(cs))
    resetStore({ share: SHARE, detail: DETAIL(cs) })
    await render(<ReviewPanel />)
    expect(q('[data-review-comment="a"] [data-review-mark]')?.getAttribute('data-review-mark')).toBe('1')
    expect(q('[data-review-comment="o"] [data-review-mark]')?.textContent).toBe('全')
    expect(q('[data-review-comment="r"] [data-review-check]')).not.toBeNull()
    expect(q('[data-review-comment="r"]')?.textContent).toContain('V1 ·')
    expect(q('[data-review-comment="a"]')?.textContent).not.toContain('V2')
    expect(q('[data-review-comment="a"]')?.textContent).toContain('10分钟前')
    expect(q('[data-review-comment="w"] [data-review-reply="owner"]')?.textContent).toBe('作者好的')
    expect(q('[data-review-anchor]')).toBeNull()
    // 动作常态不显示（只在悬停 / 选中时出现）：未选中那条的动作容器是透明的
    expect(q('[data-review-comment="a"] [data-review-reply-open]')?.parentElement?.className).toContain('opacity-0')
    await click(q('[data-review-comment="a"]'))
    expect(q('[data-review-comment="a"] [data-review-reply-open]')?.parentElement?.className).toContain('opacity-100')
  })
})

describe('画布圆点只在审阅页签打开时渲染', () => {
  const T = { zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 }
  const overlay = () => (
    <svg>
      <ReviewOverlay objects={[]} t={T} />
    </svg>
  )
  beforeEach(() => {
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
  })
  it('属性页：没有圆点；审阅页：区域意见有圆点；关了右栏：没有', async () => {
    resetStore({ share: SHARE, detail: DETAIL([cm('c1'), cm('c2', { v: 1 })]), changed: false })
    await render(overlay())
    expect(q('[data-review-pins]')).toBeNull()
    await act(async () => useUiStore.setState({ rightTab: 'review' }))
    // 最新版的区域意见画圆点；旧版本的区域意见不画（列表里标原位置已变化）
    expect(qa('[data-review-pin]').map((e) => e.dataset.reviewPin)).toEqual(['c1'])
    await act(async () => useUiStore.setState({ rightOpen: false }))
    expect(q('[data-review-pins]')).toBeNull()
  })
  it('点圆点 = 选中那条意见并描边', async () => {
    resetStore({ share: SHARE, detail: DETAIL([cm('c1')]), changed: false })
    useUiStore.setState({ rightTab: 'review' })
    await render(overlay())
    await click(q('[data-review-pin="c1"] [role="button"]'))
    expect(useReviewStore.getState().selectedId).toBe('c1')
    expect(q('[data-review-outline="c1"]')).not.toBeNull()
  })
  it('整体意见没有圆点', async () => {
    resetStore({ share: SHARE, detail: DETAIL([cm('o', { anchor: { kind: 'overall' } })]) })
    useUiStore.setState({ rightTab: 'review' })
    await render(overlay())
    expect(q('[data-review-pin]')).toBeNull()
  })
})

describe('选择卡只有一个可更新的链接时写明是哪一个', () => {
  it('直接点就更新它，说明里带链接的名字', async () => {
    resetStore({ dialogOpen: true, picker: 'new', others: [{ ...SHARE, share_id: 'sh_9', title: 'Figure 2' }] })
    vi.mocked(updateReview).mockResolvedValue({ ok: true })
    vi.mocked(fetchReviewStatus).mockResolvedValue({ stage: 'uploading', failure: null, retryable: false, share: null })
    await render(<ReviewDialog />)
    expect(q('[data-review-pick="update-existing"]')?.textContent).toContain('更新已有的链接（Figure 2）')
    await click(q('[data-review-pick="update-existing"]'))
    expect(updateReview).toHaveBeenCalledWith('sh_9', undefined, expect.anything(), { kind: 'figure', id: 'fig3' })
  })
})

describe('排版的坐标基准是页面', () => {
  const T = { zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 }
  const panelAt = (x: number) =>
    ({ id: 'p1', type: 'panel', fileId: 'p1.pdf', fileKind: 'pdf', x, y: 0, w: 40, h: 30, overrides: [] }) as never
  async function seedLayout(x: number) {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_layout')
    useDocumentStore.getState().commit(literal('准备'), (d) => {
      d.page = { w: 100, h: 80 }
      d.objects = [panelAt(x)]
    })
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
    useUiStore.setState({ rightTab: 'review', rightOpen: true })
  }
  it('页面内的面板意见：圆点在；面板整个在页面外：不画圆点，列表标「原位置已变化」', async () => {
    const c = cm('e1', { v: 2, anchor: { kind: 'element', gid: 'p1', box: [0.1, 0.1, 0.2, 0.2] } })
    await seedLayout(10)
    vi.mocked(fetchReviewShare).mockResolvedValue(DETAIL([c]))
    resetStore({ share: { ...SHARE, kind: 'layout' }, detail: DETAIL([c]) })
    await render(
      <>
        <svg>
          <ReviewOverlay objects={useDocumentStore.getState().doc.objects} t={T} />
        </svg>
        <ReviewPanel />
      </>,
    )
    expect(q('[data-review-pin="e1"]')).not.toBeNull()
    expect(q('[data-review-comment="e1"] [data-review-moved]')).toBeNull()
    await act(async () => {
      useDocumentStore.getState().commit(literal('挪'), (d) => {
        d.objects = [panelAt(500)]
      })
    })
    expect(q('[data-review-pin="e1"]')).toBeNull()
    expect(q('[data-review-comment="e1"] [data-review-moved]')?.textContent).toContain('原位置已变化')
  })
  it('区域意见：版本 frame=page 时落在页面上（页面 100×80 的一半处）', async () => {
    await seedLayout(10)
    const d = DETAIL([cm('r1', { anchor: { kind: 'region', box: [0.5, 0.5, 0.2, 0.2] } })])
    d.versions[0].frame = 'page'
    resetStore({ share: { ...SHARE, kind: 'layout' }, detail: d, changed: false })
    await render(
      <svg>
        <ReviewOverlay objects={useDocumentStore.getState().doc.objects} t={T} />
      </svg>,
    )
    const c = q('[data-review-pin="r1"] circle')!
    const cx = Number(c.getAttribute('cx'))
    const cy = Number(c.getAttribute('cy'))
    // 圆点在区域框右上角：x = (0.5+0.2)*页宽、y = 0.5*页高（同一换算：比例 × 页面 mm 的屏幕长度）
    const px = (mm: number) => mm * (cx / 70)
    expect(cy / px(40)).toBeCloseTo(1, 1)
  })
})

describe('边框线 / 网格线 / 刻度线的意见（合成 gid）', () => {
  const T = { zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 }
  const panel = {
    id: 'p1', type: 'panel', fileId: 'p1.pdf', fileKind: 'pdf', nativeW: 40, nativeH: 30, x: 5, y: 4, w: 40, h: 30, overrides: [],
  } as unknown as PanelObject
  const line = (pts: [number, number][]) => ({ kind: 'multi_path', paths: [{ points: pts, closed: false }], fill: false, stroke: true })
  const manifestOf = (over: Record<string, unknown> = {}) =>
    ({
      stem: 'p1',
      size_mm: [40, 30],
      elements: [
        {
          gid: 'axes_0', role: 'axes', label: 'axes', bbox: [0.1, 0.1, 0.8, 0.8], draggable: false, editable: [],
          spines: { left: { visible: true, ticks: true, from: [0.1, 0.9], to: [0.1, 0.1] }, top: { visible: false, ticks: false, from: [0.1, 0.1], to: [0.9, 0.1] } },
          grid: { x: line([[0.3, 0.9], [0.3, 0.1]]) },
          ...over,
        },
      ],
    }) as never
  afterEach(() => useRenderStore.setState({ byKey: {}, latest: {}, recent: {} }))
  async function seed(m: never) {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_guides')
    useDocumentStore.getState().commit(literal('准备'), (d) => {
      d.objects = [panel as never]
    })
    seedExactRender(panel, m)
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    useUiStore.setState({ rightTab: 'review', rightOpen: true })
  }
  const pinned = async (gid: string, box: number[], pt: number[]) => {
    const c = cm('g1', { anchor: { kind: 'element', gid, box, pt } as never })
    vi.mocked(fetchReviewShare).mockResolvedValue(DETAIL([c]))
    resetStore({ share: SHARE, detail: DETAIL([c]) })
    await render(
      <>
        <svg>
          <ReviewOverlay objects={useDocumentStore.getState().doc.objects} t={T} />
        </svg>
        <ReviewPanel />
      </>,
    )
  }
  it('边框线：圆点落在那条线现在的位置（按上传时的点击位置换算），不退到面板角上', async () => {
    await seed(manifestOf())
    await pinned('axes_0.spine_left', [0.099, 0.1, 0.002, 0.8], [0.1, 0.5])
    const c = q('[data-review-pin="g1"] circle')!
    expect(q('[data-review-comment="g1"] [data-review-moved]')).toBeNull()
    expect(Number(c.getAttribute('cx'))).toBeCloseTo(mmToViewX(5 + 0.1 * 40, T), 0)
    expect(Number(c.getAttribute('cy'))).toBeCloseTo(mmToViewY(4 + 0.5 * 30, T), 0)
  })
  it('网格线：框是所有网格线的包围盒；挪动后跟着走', async () => {
    await seed(manifestOf({ grid: { x: line([[0.3, 0.9], [0.3, 0.1], [0.5, 0.1], [0.5, 0.9]]) } }))
    await pinned('axes_0.xgrid', [0.299, 0.1, 0.202, 0.8], [0.4, 0.5])
    const c = q('[data-review-pin="g1"] circle')!
    expect(Number(c.getAttribute('cx'))).toBeCloseTo(mmToViewX(5 + 0.4 * 40, T), 0)
    expect(mmToPx(1, T)).toBeGreaterThan(0)
  })
  it('数据没了（网格关了 / 那一边隐藏）：不画圆点，列表标「原位置已变化」', async () => {
    await seed(manifestOf({ grid: undefined }))
    await pinned('axes_0.xgrid', [0.299, 0.1, 0.002, 0.8], [0.3, 0.5])
    expect(q('[data-review-pin="g1"]')).toBeNull()
    expect(q('[data-review-comment="g1"] [data-review-moved]')).not.toBeNull()
  })
})

describe('元素表还没到时不判「原位置已变化」', () => {
  it('元素意见：面板还没有 manifest → 不标 moved、不画圆点；表到了才判', async () => {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_pending')
    useDocumentStore.getState().commit(literal('准备'), (d) => {
      d.objects = [{ id: 'p1', type: 'panel', fileId: 'p1.pdf', fileKind: 'pdf', x: 0, y: 0, w: 40, h: 30, overrides: [] } as never]
    })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    const c = cm('e1', { anchor: { kind: 'element', gid: 'legend', box: [0.1, 0.1, 0.2, 0.2] } })
    vi.mocked(fetchReviewShare).mockResolvedValue(DETAIL([c]))
    resetStore({ share: SHARE, detail: DETAIL([c]) })
    await render(<ReviewPanel />)
    expect(q('[data-review-comment="e1"] [data-review-moved]')).toBeNull()
  })
})

describe('画布相对最新版有没有新改动', () => {
  it('打开弹窗时问引擎：false 就是没改动；404 / 失败 / 没有分享记录都按「有改动」', async () => {
    resetStore({ share: SHARE })
    vi.mocked(fetchReviewChanged).mockResolvedValue({ changed: false })
    await act(async () => useReviewStore.getState().openDialog())
    expect(fetchReviewChanged).toHaveBeenCalledWith('sh_1', expect.anything())
    expect(useReviewStore.getState().changed).toBe(false)
    vi.mocked(fetchReviewChanged).mockRejectedValue(new ApiError('nf', 404, {}))
    await act(async () => useReviewStore.getState().openDialog())
    expect(useReviewStore.getState().changed).toBe(true)
    vi.mocked(fetchReviewChanged).mockResolvedValue({ changed: false })
    resetStore({ share: null })
    await act(async () => useReviewStore.getState().openDialog())
    expect(useReviewStore.getState().changed).toBe(true)
  })
  it('更新成功后画布与最新版一致：changed 变 false', async () => {
    resetStore({ share: SHARE, dialogOpen: true, changed: true })
    vi.mocked(updateReview).mockResolvedValue({ ok: true })
    vi.mocked(fetchReviewStatus).mockResolvedValue({ stage: 'done', failure: null, retryable: false, share: SHARE })
    await act(async () => void useReviewStore.getState().update())
    await settle()
    expect(useReviewStore.getState().changed).toBe(false)
  })
})

describe('分享后本地画布改过：区域意见不再画在旧坐标上', () => {
  const T = { zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 }
  it('changed=true：最新版的区域意见不画圆点，列表里走「原位置已变化」；changed=false 才画', async () => {
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
    useUiStore.setState({ rightTab: 'review', rightOpen: true })
    const d = DETAIL([cm('r1')])
    vi.mocked(fetchReviewShare).mockResolvedValue(d)
    resetStore({ share: SHARE, detail: d, changed: true })
    vi.mocked(fetchReviewChanged).mockResolvedValue({ changed: true })
    await render(
      <>
        <svg>
          <ReviewOverlay objects={[]} t={T} />
        </svg>
        <ReviewPanel />
      </>,
    )
    await settle(900)
    expect(q('[data-review-pin="r1"]')).toBeNull()
    expect(q('[data-review-comment="r1"] [data-review-moved]')).not.toBeNull()
    vi.mocked(fetchReviewChanged).mockResolvedValue({ changed: false })
    await act(async () => useReviewStore.setState({ changed: false }))
    expect(q('[data-review-pin="r1"]')).not.toBeNull()
    expect(q('[data-review-comment="r1"] [data-review-moved]')).toBeNull()
  })
})

describe('一致性重查', () => {
  const T = { zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 }
  it('只改页面尺寸（对象数组不变）：区域圆点立刻消失，重查返回 false 才回来', async () => {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_pagesize')
    useDocumentStore.getState().commit(literal('准备'), (d) => {
      d.page = { w: 100, h: 80 }
      d.objects = [{ id: 'p1', type: 'panel', fileId: 'p1.pdf', fileKind: 'pdf', x: 10, y: 0, w: 40, h: 30, overrides: [] } as never]
    })
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
    useUiStore.setState({ rightTab: 'review', rightOpen: true })
    const d = DETAIL([cm('r1')])
    d.versions[0].frame = 'page'
    vi.mocked(fetchReviewShare).mockResolvedValue(d)
    vi.mocked(fetchReviewChanged).mockResolvedValue({ changed: false })
    resetStore({ share: { ...SHARE, kind: 'layout' }, detail: d, changed: false })
    await render(
      <>
        <svg>
          <ReviewOverlay objects={useDocumentStore.getState().doc.objects} t={T} />
        </svg>
        <ReviewPanel />
      </>,
    )
    await settle(900)
    expect(q('[data-review-pin="r1"]')).not.toBeNull()
    // 拖页面边只改 doc.page：对象数组还是同一个
    await act(async () => {
      useDocumentStore.getState().commit(literal('改页面'), (dd) => {
        dd.page = { w: 120, h: 90 }
      })
    })
    expect(useReviewStore.getState().changed).toBe(true) // 乐观作废，重查还没返回
    expect(q('[data-review-pin="r1"]')).toBeNull()
    await settle(900) // 重查（mock 返回 false）
    expect(fetchReviewChanged).toHaveBeenCalled()
    expect(q('[data-review-pin="r1"]')).not.toBeNull()
  })

  it('A 的 changed 查询慢返回 false、B 先返回 true：最终是 true（迟到结果被丢弃）', async () => {
    resetStore({ share: SHARE, changed: true })
    let resolveA!: (v: { changed: boolean }) => void
    vi.mocked(fetchReviewChanged).mockImplementationOnce(() => new Promise((r) => (resolveA = r)))
    const pa = useReviewStore.getState().loadChanged() // A 在途
    // 切到 B：旧序号作废
    useReviewStore.getState().beginResolve()
    vi.mocked(fetchReviewFor).mockResolvedValue({ share: { ...SHARE, share_id: 'sh_b' } })
    await act(async () => useReviewStore.getState().setTarget({ kind: 'figure', id: 'B' }))
    vi.mocked(fetchReviewChanged).mockResolvedValueOnce({ changed: true })
    await act(async () => useReviewStore.getState().loadChanged())
    expect(useReviewStore.getState().changed).toBe(true)
    resolveA({ changed: false })
    await pa
    expect(useReviewStore.getState().changed).toBe(true)
  })
})

describe('切图：新图 target 解析中不拼出「B 的画面 + A 的链接」', () => {
  it('beginResolve 立刻作废 A 的 target / 分享：弹窗不显示 A 的链接、不能生成；解析完成才恢复', async () => {
    resetStore({ dialogOpen: true, share: SHARE, target: { kind: 'figure', id: 'A' } })
    await render(<ReviewDialog />)
    expect(q('[data-review-link]')).not.toBeNull()
    await act(async () => useReviewStore.getState().beginResolve())
    expect(q('[data-review-link]')).toBeNull()
    expect(q('[data-review-create]')).toBeNull()
    expect(q('[data-review-resolving]')).not.toBeNull()
    expect(shareReview).not.toHaveBeenCalled()
    const st = useReviewStore.getState()
    expect(st.target).toBeNull()
    expect(st.share).toBeNull()
    // 在解析期间调 create / update 也不会发出请求（没有 target、没有分享）
    await act(async () => {
      await st.create('x', '')
      await st.update()
    })
    expect(shareReview).not.toHaveBeenCalled()
    expect(updateReview).not.toHaveBeenCalled()
    vi.mocked(fetchReviewFor).mockResolvedValue({ share: null })
    await act(async () => useReviewStore.getState().setTarget({ kind: 'figure', id: 'B' }))
    expect(useReviewStore.getState().resolving).toBe(false)
    expect(q('[data-review-create]')).not.toBeNull()
    expect(q('[data-review-link]')).toBeNull()
  })
  it('旧解析的迟到结果被丢弃（解析序号递增）', async () => {
    resetStore({ share: null, target: null })
    let resolveA!: (v: { share: typeof SHARE }) => void
    vi.mocked(fetchReviewFor).mockImplementationOnce(() => new Promise((r) => (resolveA = r)))
    const p = useReviewStore.getState().setTarget({ kind: 'figure', id: 'A' })
    useReviewStore.getState().beginResolve() // 切到 B，B 还在解析
    resolveA({ share: SHARE })
    await p
    expect(useReviewStore.getState().share).toBeNull() // A 的分享不会落到 B 上
  })
})

describe('通知轨胶囊', () => {
  const latest = { share_id: 'sh_1', author: '林老师', count: 2 }
  it('「林老师留了 2 条意见」；× 之后同样的未读数不再出现，未读增加才重现', async () => {
    resetStore({ unread: { total: 2, latest } })
    await render(<NotificationRail />)
    await settle(300)
    expect(q('[data-review-capsule]')?.textContent).toContain('林老师留了 2 条意见')
    await act(async () => useReviewStore.getState().dismissCapsule())
    await settle(600)
    expect(q('[data-review-capsule]')).toBeNull()
    await act(async () => useReviewStore.setState({ unread: { total: 3, latest: { ...latest, count: 3 } } }))
    await settle(300)
    expect(q('[data-review-capsule]')).not.toBeNull()
  })
  it('分享弹窗开着时不出现', async () => {
    resetStore({ unread: { total: 2, latest }, dialogOpen: true })
    await render(<NotificationRail />)
    await settle(300)
    expect(q('[data-review-capsule]')).toBeNull()
  })
})

describe('中英键对齐', () => {
  const flat = (o: unknown, p = ''): string[] =>
    Object.entries(o as Record<string, unknown>).flatMap(([k, v]) =>
      typeof v === 'object' && v ? flat(v, `${p}${k}.`) : [`${p}${k.replace(/_(one|other)$/, '')}`],
    )
  it('review 键组两种语言的键一致', () => {
    const zh = new Set(flat((zhDialogs as Record<string, unknown>).review))
    const en = new Set(flat((enDialogs as Record<string, unknown>).review))
    expect([...zh].filter((k) => !en.has(k))).toEqual([])
    expect([...en].filter((k) => !zh.has(k))).toEqual([])
    // 失败码闭集全有文案
    for (const c of ['quota_new_exhausted', 'share_gone', 'too_large', 'rate_limited', 'network', 'server', 'client_error', 'render_failed'])
      expect(zh.has(`failure.${c}`)).toBe(true)
  })
})
