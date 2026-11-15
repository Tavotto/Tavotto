import { create } from 'zustand'
import {
  ApiError,
  deleteReviewRecord,
  dismissReview,
  fetchReviewCapability,
  fetchReviewChanged,
  fetchReviewFor,
  fetchReviewQuota,
  fetchReviewShare,
  fetchReviewShares,
  fetchReviewStatus,
  fetchReviewUnread,
  markReviewRead,
  refreshReview,
  replyReviewComment,
  resolveReviewComment,
  revokeReview,
  shareReview,
  updateReview,
  type ReviewComment,
  type ReviewFailureCode,
  type ReviewQuota,
  type ReviewRender,
  type ReviewShareDetail,
  type ReviewShareSummary,
  type ReviewStage,
  type ReviewTarget,
  type ReviewUnread,
} from '@/lib/api'
import { buildReviewRender } from '@/lib/reviewRender'
import { sameTarget } from '@/lib/reviewTarget'

/**
 * 「外部审阅」的前端状态（ADR 0187）：能力镜像、当前 target 的分享记录（决定「审阅」页签出不出现）、分享弹窗、
 * 进度、剩余次数、意见详情与未读。**前端不碰远程**：生成 / 更新 / 停止 / 拉意见都是本机引擎做，这里只遥控；
 * 会引出远程请求的只有用户点的那几下与「审阅」页签开着时每 60 秒一次的 `refresh`。
 */
/** 额度用满的选择卡只有「今天新建用完」这一种（更新同一链接不限次数） */
export type ReviewPicker = 'new' | null
export interface ReviewFailure {
  code: ReviewFailureCode
  retryable: boolean
}

const POLL_MS = 350
const UNREAD_REFRESH_MS = 5 * 60 * 1000
export const TAB_REFRESH_MS = 60 * 1000

interface ReviewState {
  enabled: boolean
  target: ReviewTarget | null
  /** 新图的 target 正在解析：此时 `target` / `share` 一律是空的，弹窗显示加载态 */
  resolving: boolean
  /** 当前 target 的未删除分享记录；null = 没分享过（页签不出现） */
  share: ReviewShareSummary | null
  detail: ReviewShareDetail | null
  dialogOpen: boolean
  stage: ReviewStage
  failure: ReviewFailure | null
  /** 额度用满的选择卡：new = 今天新建用完；update = 这个链接今天更新用完 */
  picker: ReviewPicker
  quota: ReviewQuota
  /** 选择卡里「更新已有的链接」可选的本机未到期分享 */
  others: ReviewShareSummary[]
  /** 画布相对最新版本有没有新改动（弹窗打开时问引擎，决定「更新」那一行出不出现）；没问到 / 失败按 true（宁可多给更新入口） */
  changed: boolean
  unread: ReviewUnread
  /** 通知轨胶囊：用户 × 掉时的未读总数；未读数再增加才会重新出现 */
  capsuleDismissedAt: number
  selectedId: string | null
  /** 最近一次发起的是更新还是新建（失败重试用） */
  lastOp: { kind: 'new'; title: string; note: string } | { kind: 'update'; shareId: string; note: string; other?: ReviewTarget } | null

  loadCapability: () => Promise<void>
  setTarget: (t: ReviewTarget | null) => Promise<void>
  /** 切图后、新图的 target 还在解析（要问引擎 stem）：立刻作废旧 target / 分享，分享审阅在解析完成前不可用 */
  beginResolve: () => void
  reloadShare: () => Promise<void>
  /** 剩余次数 + 选择卡的候选；联网不通时数字是 null，界面就不显示 */
  loadQuota: () => Promise<void>
  loadChanged: () => Promise<void>
  /** 本地画布输入变了：先把 changed 置 true 并作废在途的 changed 查询（乐观作废，避免重查返回前画错） */
  invalidateChanged: () => void
  loadDetail: () => Promise<void>
  openDialog: () => void
  closeDialog: () => void
  create: (title: string, note: string) => Promise<void>
  update: (note?: string) => Promise<void>
  updateOther: (shareId: string, note?: string) => Promise<void>
  retry: () => Promise<void>
  revoke: () => Promise<void>
  removeRecord: () => Promise<void>
  select: (id: string | null) => void
  reply: (commentId: string, text: string) => Promise<boolean>
  setResolved: (commentId: string, resolved: boolean) => Promise<void>
  /** 「审阅」页签打开 / 每 60 秒：拉一次 feed，再读详情；打开那次顺带清未读 */
  refreshTab: (opts?: { markRead?: boolean }) => Promise<void>
  loadUnread: () => Promise<void>
  watch: () => () => void
  dismissCapsule: () => void
}

let genToken = 0
/** changed 查询的序号：切图 / 作废 / 新查询都会递增，旧查询的迟到结果不写回 */
let changedToken = 0
let targetToken = 0
let unreadTimer: number | undefined
const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms))

export const useReviewStore = create<ReviewState>((set, get) => {
  const patchComment = (c: ReviewComment) =>
    set((s) =>
      s.detail
        ? { detail: { ...s.detail, comments: s.detail.comments.map((x) => (x.id === c.id ? c : x)) } }
        : {},
    )

  /** 发起一次生成 / 更新，然后盯 status 到终态（一次发起一个 token，关窗 / 换发起即作废旧的盯梢） */
  const run = async (start: (render: ReviewRender) => Promise<unknown>) => {
    const mine = ++genToken
    const render = buildReviewRender()
    if (!render) {
      // 画面取不出来（源文件不在等）：不发请求，按「画面没生成出来」说
      set({ stage: 'failed', failure: { code: 'render_failed', retryable: true }, picker: null })
      return
    }
    set({ stage: 'rendering', failure: null, picker: null })
    try {
      await start(render)
    } catch (e) {
      // 同步 422（`review_render_required` / `target_invalid`）= 这次的画面 / 对象不成立：按「画面没生成出来」说，可重试
      const code: ReviewFailureCode = e instanceof ApiError && e.status === 422 ? 'render_failed' : 'client_error'
      if (mine === genToken) set({ stage: 'failed', failure: { code, retryable: true } })
      return
    }
    for (;;) {
      await sleep(POLL_MS)
      if (mine !== genToken) return
      let st
      try {
        st = await fetchReviewStatus()
      } catch {
        set({ stage: 'failed', failure: { code: 'client_error', retryable: true } })
        return
      }
      if (mine !== genToken) return
      if (st.stage === 'done') {
        set({ stage: 'done', failure: null, changed: false })
        void dismissReview().catch(() => {})
        await get().reloadShare()
        void get().loadQuota()
        return
      }
      if (st.stage === 'failed') {
        const code = st.failure ?? 'client_error'
        void dismissReview().catch(() => {})
        if (code === 'quota_new_exhausted') {
          set({ stage: 'idle', failure: null, picker: 'new' })
          await get().loadQuota()
        } else {
          set({ stage: 'failed', failure: { code, retryable: st.retryable } })
        }
        return
      }
      set({ stage: st.stage })
    }
  }

  return {
    enabled: false,
    target: null,
    resolving: false,
    share: null,
    detail: null,
    dialogOpen: false,
    stage: 'idle',
    failure: null,
    picker: null,
    quota: { new_left: null },
    others: [],
    changed: true,
    unread: { total: 0, latest: null },
    capsuleDismissedAt: 0,
    selectedId: null,
    lastOp: null,

    loadCapability: async () => {
      try {
        set({ enabled: (await fetchReviewCapability())?.enabled === true })
      } catch {
        set({ enabled: false })
      }
      if (get().enabled) void get().loadUnread()
    },

    beginResolve: () => {
      targetToken++
      genToken++
      changedToken++
      set({ target: null, resolving: true, share: null, detail: null, selectedId: null, picker: null, failure: null, stage: 'idle', changed: true })
    },

    setTarget: async (t) => {
      if (get().resolving) set({ resolving: false })
      if (sameTarget(t, get().target)) return
      const mine = ++targetToken
      changedToken++
      set({ target: t, share: null, detail: null, selectedId: null, picker: null, failure: null, stage: 'idle' })
      genToken++
      if (!t || !get().enabled) return
      try {
        const r = await fetchReviewFor(t)
        if (mine === targetToken) set({ share: r.share ?? null })
      } catch {
        /* 问不到就当没有：页签不出现，不报错 */
      }
    },

    reloadShare: async () => {
      const t = get().target
      if (!t || !get().enabled) return
      const mine = targetToken
      try {
        const r = await fetchReviewFor(t)
        if (mine === targetToken) set({ share: r.share ?? null })
      } catch {
        /* 同上 */
      }
    },

    loadQuota: async () => {
      try {
        set({ quota: (await fetchReviewQuota()) ?? { new_left: null } })
      } catch {
        set({ quota: { new_left: null } })
      }
      try {
        const all = (await fetchReviewShares())?.shares ?? []
        set({ others: all.filter((x) => !x.revoked && !x.expired) })
      } catch {
        set({ others: [] })
      }
    },

    loadChanged: async () => {
      const id = get().share?.share_id
      const render = buildReviewRender()
      const mine = ++changedToken
      if (!id || !render) return set({ changed: true })
      // 只在序号、分享都还是发起时的那一个才写回：A 的迟到结果不能盖掉 B 的
      const apply = (v: boolean) => {
        if (mine === changedToken && get().share?.share_id === id) set({ changed: v })
      }
      try {
        const r = await fetchReviewChanged(id, render)
        apply(r?.changed !== false)
      } catch {
        apply(true)
      }
    },

    invalidateChanged: () => {
      changedToken++
      set({ changed: true })
    },

    loadDetail: async () => {
      const id = get().share?.share_id
      if (!id) return set({ detail: null })
      const mine = targetToken
      try {
        const d = await fetchReviewShare(id)
        if (d && mine === targetToken && get().share?.share_id === id) set({ detail: d })
      } catch {
        /* 留着上一份 */
      }
    },

    openDialog: () => {
      set({ dialogOpen: true, picker: null, failure: null, stage: 'idle' })
      void get().loadQuota()
      void get().loadChanged()
    },
    closeDialog: () => {
      const { stage } = get()
      if (stage === 'rendering' || stage === 'uploading') return // 进行中不许随手关
      genToken++
      void dismissReview().catch(() => {})
      set({ dialogOpen: false, stage: 'idle', failure: null, picker: null })
    },

    create: async (title, note) => {
      const t = get().target
      if (!t) return
      set({ lastOp: { kind: 'new', title, note } })
      await run((render) => shareReview(t, title, note, render))
    },
    update: async (note) => {
      const id = get().share?.share_id
      if (!id) return
      set({ lastOp: { kind: 'update', shareId: id, note: note ?? '' } })
      await run((render) => updateReview(id, note, render))
    },
    updateOther: async (shareId, note) => {
      const t = get().target
      set({ lastOp: { kind: 'update', shareId, note: note ?? '', other: t ?? undefined } })
      await run((render) => updateReview(shareId, note, render, t ?? undefined))
    },
    retry: async () => {
      const op = get().lastOp
      if (!op) return
      if (op.kind === 'new') await get().create(op.title, op.note)
      else if (op.other) await get().updateOther(op.shareId, op.note)
      else await get().update(op.note)
    },

    revoke: async () => {
      const id = get().share?.share_id
      if (!id) return
      try {
        const r = await revokeReview(id)
        if (r.ok === false) {
          set({ stage: 'failed', failure: { code: r.failure ?? 'client_error', retryable: true } })
          return
        }
      } catch {
        set({ stage: 'failed', failure: { code: 'client_error', retryable: true } })
        return
      }
      await get().reloadShare()
      set({ dialogOpen: false })
    },

    removeRecord: async () => {
      const id = get().share?.share_id
      if (!id) return
      try {
        await deleteReviewRecord(id)
      } catch {
        return
      }
      set({ share: null, detail: null, selectedId: null })
      void get().loadUnread()
    },

    select: (id) => set({ selectedId: id }),

    reply: async (commentId, text) => {
      const id = get().share?.share_id
      if (!id || !text.trim()) return false
      try {
        const r = await replyReviewComment(id, commentId, text.trim())
        if (r.ok) patchComment(r.comment)
        return !!r.ok
      } catch {
        return false
      }
    },
    setResolved: async (commentId, resolved) => {
      const id = get().share?.share_id
      if (!id) return
      try {
        const r = await resolveReviewComment(id, commentId, resolved)
        if (r.ok) {
          patchComment(r.comment)
          void get().reloadShare()
        }
      } catch {
        /* 没成就保持原样 */
      }
    },

    refreshTab: async (opts) => {
      const id = get().share?.share_id
      if (!id) return
      if (opts?.markRead) void markReviewRead(id).catch(() => {})
      if (get().share && !get().share!.revoked && !get().share!.expired) await refreshReview(id).catch(() => null)
      await get().reloadShare()
      await get().loadDetail()
      void get().loadChanged()
      void get().loadUnread()
    },

    loadUnread: async () => {
      if (!get().enabled) return
      try {
        set({ unread: await fetchReviewUnread() })
      } catch {
        /* 本机读不到就不动 */
      }
    },

    watch: () => {
      window.clearInterval(unreadTimer)
      unreadTimer = window.setInterval(() => void get().loadUnread(), UNREAD_REFRESH_MS)
      return () => window.clearInterval(unreadTimer)
    },

    dismissCapsule: () => set((s) => ({ capsuleDismissedAt: s.unread.total })),
  }
})
