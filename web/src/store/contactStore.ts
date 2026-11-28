import { create } from 'zustand'
import { buildSendPayload, NO_DETAIL_OPTIONS, type DetailOptions } from '@/diagnostics/detail'
import { currentLocale } from '@/i18n'
import {
  ApiError,
  cancelContact,
  cancelDiagSend,
  discardDiagSend,
  dismissContact,
  fetchContactCapability,
  fetchContactMessages,
  fetchContactStatus,
  fetchDiagSendStatus,
  forgetContactMessage,
  isStalePrepare,
  markContactRead,
  prepareDiagSend,
  refreshContactMessages,
  sendContact,
  startDiagSend,
  type ContactCapability,
  type ContactJournalHead,
  type ContactMessage,
  type ContactStatus,
  type DiagSendPrepared,
} from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { journalCatalogUi } from '@/lib/journalUi'
import { journalTipKey, type JournalIssue, type JournalTipContext } from '@/lib/journalTip'

/**
 * 「联系作者」的前端状态（ADR 0145）：能力镜像、对话框开关、**草稿**、发送阶段、「我的来信」与未读数。
 *
 * - **草稿只在这里**（进程内、不落盘）：关窗不丢，失败不丢，发送成功才清空。附件是内存里的 `File`。
 * - **前端不碰远程**：发送 / 取回回信都是本机引擎做，这里只遥控。唯一会引出远程请求的是 `send()`（用户点「发送」）
 *   与 `checkReplies()`（用户点「检查回信」）。「附上诊断信息」走现有诊断发送的 prepare → 预览 → start（`lib/api` 的 `*DiagSend*`），
 *   拿到报告编号后才把信发出去。
 * - **没有邮件协议链接**，也不唤起系统邮件程序：失败时只有「复制内容 / 复制地址」（由组件做）。
 * - 邮箱只在本机 localStorage 记一份「上次填写」（可删）；不进引擎状态、不进诊断、不进遥测。
 * - 遥测这次不加事件（攒齐一次升同意版本）。
 */
export type ContactPhase = 'edit' | 'diag' | 'sending' | 'cancelling' | 'done' | 'failed'
export type ContactView = 'write' | 'letters'
export type DiagAttachState = 'off' | 'preparing' | 'ready' | 'failed' | 'tooLarge'

export interface ContactDraft {
  subject: string
  body: string
  replyEmail: string
  files: File[]
  attachDiag: boolean
  /** 诊断包的三项额外内容（与「发送问题反馈」确认框同一组）：默认全不勾；取消「附上诊断信息」即清零，不跨草稿记忆 */
  diagOptions: DetailOptions
  /** 期刊线索的「原文链接」（ADR 0213）；普通来信不用它 */
  url: string
  /** 期刊反馈窗的「问题」分段；空 = 没选，不发 */
  issue: JournalIssue | ''
  /** 「申请收录期刊」的期刊名称（预填搜索词，可改） */
  query: string
}

export interface ContactFailure {
  /** contact = 引擎发信的失败码；diag = 诊断包没发出去（诊断发送的失败码）；local = 本机输入校验 */
  source: 'contact' | 'diag' | 'local'
  code: string
  retryable: boolean
  retryAfter: number | null
}

const EMAIL_KEY = 'tavotto.contact.email'
const POLL_MS = 350
/** 未读数的本机刷新间隔（只读本机记录，不联网；引擎自己每 6 小时问一次服务器） */
const UNREAD_REFRESH_MS = 5 * 60 * 1000

export const EMPTY_DRAFT: ContactDraft = {
  subject: '',
  body: '',
  replyEmail: '',
  files: [],
  attachDiag: false,
  diagOptions: NO_DETAIL_OPTIONS,
  url: '',
  issue: '',
  query: '',
}
/** 勾选变化后等多久才重新备包：连点几个框只备一次（同 DiagnosticsSendDialog） */
const DIAG_REFRESH_MS = 300

/** 与引擎 `contactsend.valid_email` 同一条规则：可打印 ASCII、一个 @、域名含「.」、无引号 / 分号 / 逗号 / 括号 */
export function validEmail(v: string): boolean {
  if (v.length > 254 || v.split('@').length !== 2) return false
  if (!/^[\x21-\x7e]+$/.test(v) || /[,;<>()[\]"']/.test(v)) return false
  const [local, domain] = v.split('@')
  return !!local && domain.includes('.') && !domain.startsWith('.') && !domain.endsWith('.')
}

function readRemembered(): string | null {
  try {
    const v = window.localStorage.getItem(EMAIL_KEY)
    return v && validEmail(v) ? v : null
  } catch {
    return null
  }
}
function writeRemembered(v: string | null): void {
  try {
    if (v) window.localStorage.setItem(EMAIL_KEY, v)
    else window.localStorage.removeItem(EMAIL_KEY)
  } catch {
    /* 隐私窗口 / 被禁用：不记就是了，界面照常工作 */
  }
}

export function extOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i < 0 ? '' : name.slice(i).toLowerCase()
}

interface ContactState {
  capability: ContactCapability | null
  open: boolean
  view: ContactView
  draft: ContactDraft
  phase: ContactPhase
  status: ContactStatus | null
  failure: ContactFailure | null
  /** 表单校验没过时的一句话（`contact.error.*` 的键尾），不是引擎失败 */
  formError: string | null
  /** 附件被挡下的原因（`contact.fileError.*` 的键尾） */
  fileError: string | null
  messageId: string | null
  /** 信已送达但回信令牌没能存进本机（引擎 `inbox_saved: false`）：应用里收不到回信 */
  inboxMissing: boolean
  diag: { state: DiagAttachState; prepared: DiagSendPrepared | null }
  messages: ContactMessage[]
  messagesLoaded: boolean
  checking: boolean
  /** 这一趟「我的来信」里要标「新」的回信 id（看过即已读，但这一趟仍让用户看见哪几条是新的） */
  freshIds: string[]
  unread: number
  /** 通知轨胶囊：用户 × 掉时的未读数；未读数再增加才会重新出现 */
  capsuleDismissedAt: number
  rememberedEmail: string | null
  /** 此刻这封信是不是「期刊线索」（ADR 0213）；是就带着上下文，窗里多一个原文链接框、少诊断与主题 */
  journalTip: JournalTipContext | null
  /** 开期刊线索前被收起的普通来信草稿，线索窗结束后还给用户 */
  draftStash: ContactDraft | null

  load: () => Promise<void>
  /** 周期性刷新本机未读数；返回停止函数 */
  watch: () => () => void
  setOpen: (open: boolean, view?: ContactView) => void
  /** 从出处卡 / 选择器进来：同一个窗，带上线索上下文 */
  openJournalTip: (ctx: JournalTipContext) => void
  setView: (view: ContactView) => void
  setDraft: (patch: Partial<ContactDraft>) => void
  addFiles: (files: File[]) => void
  removeFile: (index: number) => void
  setAttachDiag: (on: boolean) => void
  setDiagOption: (key: keyof DetailOptions, on: boolean) => void
  send: () => Promise<void>
  cancel: () => Promise<void>
  /** 发送成功之后「再写一封」 */
  reset: () => void
  forgetEmail: () => void
  loadMessages: () => Promise<void>
  checkReplies: () => Promise<void>
  forget: (id: string) => Promise<void>
  dismissCapsule: () => void
}

let runToken = 0
let diagToken = 0
let diagPj: string | null = null
/** 诊断包已传完时记下 (备好的包 id → report_id)：之后信发失败再重试，不能再 `startDiagSend`（引擎对已完成的会话回 not_found） */
let diagDone: { id: string; reportId: string } | null = null
let diagRefresh: ReturnType<typeof setTimeout> | null = null
let unreadTimer: number | undefined

const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms))

export const useContactStore = create<ContactState>((set, get) => {
  /** 丢弃引擎里备好的诊断包（关窗 / 取消勾选 / 发完）。 */
  const discardDiag = () => {
    if (diagRefresh) clearTimeout(diagRefresh)
    diagRefresh = null
    diagToken++
    diagDone = null
    const id = get().diag.prepared?.id
    if (id) void discardDiagSend(id, diagPj).catch(() => {})
    set({ diag: { state: 'off', prepared: null } })
  }

  const prepareDiag = async () => {
    const mine = ++diagToken
    diagDone = null
    const old = get().diag.prepared?.id
    if (old) void discardDiagSend(old, diagPj).catch(() => {}) // 换包（勾选变化）：旧的那份不留在引擎里
    const pj = currentProjectId()
    diagPj = pj
    const opts = get().draft.diagOptions
    set({ diag: { state: 'preparing', prepared: null } })
    try {
      const payload = await buildSendPayload(opts)
      if (diagToken !== mine) return
      const p = await prepareDiagSend(payload, opts, pj)
      if (diagToken !== mine) {
        if (p.id) void discardDiagSend(p.id, pj).catch(() => {})
        return
      }
      set({ diag: { state: p.too_large ? 'tooLarge' : 'ready', prepared: p } })
    } catch (e) {
      if (isStalePrepare(e) || diagToken !== mine) return
      set({ diag: { state: 'failed', prepared: null } })
    }
  }

  /** 结束期刊线索窗：线索上下文清掉，收起的普通草稿还回来（没有就是空白草稿）。 */
  const endJournalTip = () => {
    const stash = get().draftStash
    set({
      journalTip: null,
      draftStash: null,
      draft: stash ?? { ...EMPTY_DRAFT, replyEmail: get().rememberedEmail ?? '' },
      phase: 'edit',
      failure: null,
      status: null,
      formError: null,
    })
  }

  /** 轮询引擎发信状态直到终态；`mine` 变了（取消 / 重发 / 关闭）就停。 */
  const pollContact = async (mine: number): Promise<ContactStatus | null> => {
    for (;;) {
      await sleep(POLL_MS)
      if (runToken !== mine) return null
      try {
        const s = await fetchContactStatus()
        if (runToken !== mine) return null
        set({ status: s })
        if (s.state === 'done' || s.state === 'failed' || s.state === 'cancelled' || s.state === 'idle') return s
      } catch {
        /* 单次读失败不改变结论，下一拍再读 */
      }
    }
  }

  const fail = (f: ContactFailure) => set({ phase: 'failed', failure: f })

  /** 诊断包：启动 → 轮询到送达，返回报告编号；失败 / 取消返回 null（状态已置好）。 */
  const sendDiag = async (mine: number): Promise<string | null> => {
    let prepared = get().diag.prepared
    if (!prepared?.id || diagPj !== currentProjectId()) {
      await prepareDiag()
      prepared = get().diag.prepared
    }
    const id = prepared?.id
    if (!id || runToken !== mine) {
      if (runToken === mine) fail({ source: 'diag', code: 'prepare_failed', retryable: true, retryAfter: null })
      return null
    }
    // 诊断包上一趟已经传完（信发失败后重试）：直接带原编号去发信
    if (diagDone && diagDone.id === id) return diagDone.reportId
    try {
      await startDiagSend(id, { category: 'other', note: '', gen: prepared?.gen, reply: false })
    } catch (e) {
      if (runToken !== mine) return null
      const code = e instanceof ApiError ? String(e.body?.code ?? '').replace(/^diag_/, '') : ''
      // 引擎里这份包没了（过期 / 引擎重启）：重新备包再试一次；其余按失败说
      fail({ source: 'diag', code: code || 'unexpected_response', retryable: true, retryAfter: null })
      return null
    }
    for (;;) {
      await sleep(POLL_MS)
      if (runToken !== mine) return null
      try {
        const s = await fetchDiagSendStatus(id)
        if (runToken !== mine) return null
        if (s.state === 'done' && s.report_id) {
          diagDone = { id, reportId: s.report_id }
          return s.report_id
        }
        if (s.state === 'unknown') {
          fail({ source: 'diag', code: 'unknown', retryable: false, retryAfter: null })
          return null
        }
        if (s.state === 'failed') {
          fail({
            source: 'diag',
            code: s.code ?? 'unexpected_response',
            retryable: s.retryable !== false,
            retryAfter: s.retry_after ?? null,
          })
          return null
        }
        if (s.state === 'cancelled') return null
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          fail({ source: 'diag', code: 'not_found', retryable: true, retryAfter: null })
          return null
        }
      }
    }
  }

  return {
    capability: null,
    open: false,
    view: 'write',
    draft: { ...EMPTY_DRAFT, replyEmail: readRemembered() ?? '' },
    phase: 'edit',
    status: null,
    failure: null,
    formError: null,
    fileError: null,
    messageId: null,
    inboxMissing: false,
    diag: { state: 'off', prepared: null },
    messages: [],
    messagesLoaded: false,
    checking: false,
    freshIds: [],
    unread: 0,
    capsuleDismissedAt: 0,
    rememberedEmail: readRemembered(),
    journalTip: null,
    draftStash: null,

    load: async () => {
      try {
        const c = await fetchContactCapability()
        set({ capability: c?.enabled === true ? c : null, unread: c?.enabled === true ? (c.unread ?? 0) : 0 })
      } catch {
        set({ capability: null, unread: 0 })
      }
    },

    watch: () => {
      window.clearInterval(unreadTimer)
      unreadTimer = window.setInterval(() => {
        if (get().capability) void get().load()
      }, UNREAD_REFRESH_MS)
      return () => window.clearInterval(unreadTimer)
    },

    setOpen: (open, view) => {
      if (!open) {
        const { phase } = get()
        if (phase === 'sending' || phase === 'cancelling' || phase === 'diag') return // 发送中不许随手关：先取消
        discardDiag()
        runToken++
        if (phase === 'done') get().reset()
        void dismissContact().catch(() => {})
        set({ open: false, formError: null, fileError: null, freshIds: [] })
        return
      }
      // 从普通入口（顶栏按钮、未读回信胶囊）打开：线索窗到此结束，普通草稿还给用户。「我的来信」也一样——
      // 线索窗是单独的窗、没有来信页，留着线索上下文会把它重新弹出来，而来信页照样把回信标成已读（Codex #2310）。
      // 只有线索正在发送时不动它（发送中的状态归线索窗）
      const busy = ['sending', 'cancelling', 'diag'].includes(get().phase)
      if (get().journalTip && !busy) endJournalTip()
      set({ open: true, view: view ?? 'write', formError: null })
      if (view === 'letters') void get().loadMessages()
      if (get().draft.attachDiag && get().diag.state === 'off') void prepareDiag()
    },

    openJournalTip: (ctx) => {
      const { phase, journalTip } = get()
      if (phase === 'sending' || phase === 'cancelling' || phase === 'diag') {
        set({ open: true, view: 'write' })
        return
      }
      if (phase === 'done') get().reset()
      if (!journalTip || journalTipKey(journalTip) !== journalTipKey(ctx)) {
        const cur = get().draft
        const blank = !cur.body.trim() && !cur.subject.trim() && cur.files.length === 0 && !cur.attachDiag
        // 手上有写到一半的普通来信：先收起来，线索窗结束再还
        if (!journalTip && !blank) set({ draftStash: cur })
        if (get().draft.attachDiag) discardDiag()
        set({
          draft: { ...EMPTY_DRAFT, replyEmail: get().rememberedEmail ?? '', query: ctx.query ?? '' },
          phase: 'edit',
          failure: null,
          status: null,
        })
      }
      set({ journalTip: ctx, open: true, view: 'write', formError: null })
    },

    setView: (view) => {
      set({ view })
      if (view === 'letters') void get().loadMessages()
      else set({ freshIds: [] })
    },

    setDraft: (patch) => set((s) => ({ draft: { ...s.draft, ...patch }, formError: null })),

    addFiles: (incoming) => {
      const cap = get().capability
      const maxCount = cap?.max_attachments ?? 5
      const maxTotal = cap?.max_total_bytes ?? 50 * 1024 * 1024
      const files = [...get().draft.files]
      let error: string | null = null
      for (const f of incoming) {
        // 不限格式（2026-11-04 用户裁决）：引擎对不认识的扩展名按 application/octet-stream 发
        if (f.size === 0) error = 'empty'
        else if (files.length >= maxCount) error = 'count'
        else if (files.reduce((n, x) => n + x.size, 0) + f.size > maxTotal) error = 'total'
        else if (files.some((x) => x.name === f.name && x.size === f.size)) continue
        else files.push(f)
      }
      set((s) => ({ draft: { ...s.draft, files }, fileError: error }))
    },

    removeFile: (index) =>
      set((s) => ({ draft: { ...s.draft, files: s.draft.files.filter((_, i) => i !== index) }, fileError: null })),

    setAttachDiag: (on) => {
      set((s) => ({ draft: { ...s.draft, attachDiag: on, diagOptions: NO_DETAIL_OPTIONS } }))
      if (on) void prepareDiag()
      else discardDiag()
    },

    setDiagOption: (key, on) => {
      set((s) => ({ draft: { ...s.draft, diagOptions: { ...s.draft.diagOptions, [key]: on } } }))
      if (!get().draft.attachDiag) return
      // 点击即时生效，备包防抖；备好之前「发送」是灰的（diag.state === 'preparing'）
      diagToken++
      set((s) => ({ diag: { state: 'preparing', prepared: s.diag.prepared } }))
      if (diagRefresh) clearTimeout(diagRefresh)
      diagRefresh = setTimeout(() => {
        diagRefresh = null
        void prepareDiag()
      }, DIAG_REFRESH_MS)
    },

    send: async () => {
      const { draft, phase, capability } = get()
      if (phase === 'sending' || phase === 'cancelling' || phase === 'diag') return
      const body = draft.body.trim()
      const email = draft.replyEmail.trim()
      const tip = get().journalTip
      let journal: ContactJournalHead | undefined
      if (tip) {
        // 用户不写任何话、只点发送也算有效线索；但「没有这本刊」至少要有搜索词、链接、附件之一
        const url = tip.type === 'editor' ? '' : normalizeTipUrl(draft.url)
        if (url === null) return set({ formError: 'urlInvalid' })
        const query = draft.query.trim()
        if (tip.type === 'editor' && !body && draft.files.length === 0) return
        if (tip.type === 'missing' && !query && !url && draft.files.length === 0) {
          return set({ formError: 'journalNeedsClue' })
        }
        journal = tipHead(tip, url, { issue: draft.issue, query })
      } else if (!body) return set({ formError: 'bodyRequired' })
      if (body.length > (capability?.body_max ?? 5000)) return set({ formError: 'bodyTooLong' })
      if (draft.subject.trim().length > (capability?.subject_max ?? 120)) return set({ formError: 'subjectTooLong' })
      if (email && !validEmail(email)) return set({ formError: 'emailInvalid' })
      if (!email && get().rememberedEmail) get().forgetEmail() // 清空邮箱框再发 = 忘记已记住的邮箱
      const mine = ++runToken
      set({ formError: null, failure: null, status: null, phase: draft.attachDiag && !tip ? 'diag' : 'sending' })
      let reportId: string | undefined
      if (draft.attachDiag && !tip) {
        const id = await sendDiag(mine)
        if (runToken !== mine) return
        if (!id) {
          // 诊断没发出去：用户取消 → 回到编辑；失败 → `fail` 已置好
          if (get().phase === 'diag') set({ phase: 'edit' })
          return
        }
        reportId = id
        set({ phase: 'sending' })
      }
      try {
        await sendContact(
          {
            locale: currentLocale(),
            subject: tip ? '' : draft.subject.trim(),
            body,
            reply_email: email,
            ...(reportId ? { report_id: reportId } : {}),
            ...(journal ? { journal } : {}),
          },
          draft.files,
        )
      } catch (e) {
        if (runToken !== mine) return
        if (e instanceof ApiError && e.status === 409 && e.body?.code === 'contact_send_in_progress') {
          /* 已在发送：接着轮询 */
        } else {
          const raw = e instanceof ApiError ? String(e.body?.code ?? '') : ''
          const local = raw.startsWith('contact_') ? raw.slice('contact_'.length) : ''
          return fail({
            source: local ? 'local' : 'contact',
            code: local || 'network',
            retryable: !local,
            retryAfter: null,
          })
        }
      }
      const final = await pollContact(mine)
      if (runToken !== mine || !final) return
      if (final.state === 'done') {
        // 邮箱：发送成功才在本机记一份「上次填写」（可在对话框里删）
        if (email) {
          writeRemembered(email)
          set({ rememberedEmail: email })
        }
        discardDiag()
        set({ phase: 'done', messageId: final.message_id ?? null, inboxMissing: final.inbox_saved === false, status: final })
        void get().load()
        void get().loadMessages()
      } else if (final.state === 'failed') {
        fail({
          source: 'contact',
          code: final.code ?? 'unexpected_response',
          retryable: final.retryable !== false,
          retryAfter: final.retry_after ?? null,
        })
      } else {
        set({ phase: 'edit', status: final }) // 用户取消：回到可编辑的草稿，没有发送
      }
    },

    cancel: async () => {
      const { phase } = get()
      if (phase === 'diag') {
        const id = get().diag.prepared?.id
        runToken++
        set({ phase: 'edit' })
        if (id) void cancelDiagSend(id).catch(() => {})
        return
      }
      if (phase !== 'sending') return
      set({ phase: 'cancelling' })
      try {
        await cancelContact()
      } catch {
        /* 引擎自己会收尾；下面的轮询读到终态 */
      }
    },

    reset: () => {
      runToken++
      discardDiag()
      const stash = get().draftStash
      set({
        journalTip: null,
        draftStash: null,
        draft: stash ?? { ...EMPTY_DRAFT, replyEmail: get().rememberedEmail ?? '' },
        phase: 'edit',
        status: null,
        failure: null,
        formError: null,
        fileError: null,
        messageId: null,
        inboxMissing: false,
      })
      void dismissContact().catch(() => {})
    },

    forgetEmail: () => {
      writeRemembered(null)
      set((s) => ({ rememberedEmail: null, draft: { ...s.draft, replyEmail: '' } }))
    },

    loadMessages: async () => {
      try {
        const { messages, unread } = await fetchContactMessages()
        const fresh = messages.flatMap((m) => m.replies.filter((r) => !r.read).map((r) => r.id))
        set({ messages, unread, messagesLoaded: true, freshIds: get().view === 'letters' ? fresh : get().freshIds })
        // 看过即已读：列表此刻就在用户眼前
        if (get().open && get().view === 'letters') {
          let left = unread
          for (const m of messages.filter((x) => x.unread > 0)) {
            const r = await markContactRead(m.message_id).catch(() => null)
            if (r) left = r.unread
          }
          set({ unread: left })
        }
      } catch {
        set({ messagesLoaded: true })
      }
    },

    checkReplies: async () => {
      if (get().checking) return
      set({ checking: true })
      try {
        await refreshContactMessages()
      } catch {
        /* 本轮没问成（线路不通 / 节流）：列表照旧 */
      }
      set({ checking: false })
      await get().loadMessages()
    },

    forget: async (id) => {
      try {
        const r = await forgetContactMessage(id)
        set({ unread: r.unread })
      } catch {
        /* 删不掉就留着 */
      }
      await get().loadMessages()
    },

    dismissCapsule: () => set((s) => ({ capsuleDismissedAt: s.unread })),
  }
})

/**
 * 「原文链接」框的内容 → 线缆上的 url。空 = 不带（`''`）；没写协议就补 `https://`；
 * 只收 https、不带账号密码（引擎同一条规则再校验）；不合规返回 null（界面说一句，不静默丢掉用户写的东西）。
 */
export function normalizeTipUrl(raw: string): string | null {
  const v = raw.trim()
  if (!v) return ''
  const full = /^[a-z][a-z0-9+.-]*:\/\//i.test(v) ? v : `https://${v}`
  if (!/^https:\/\//i.test(full) || /\s/.test(full) || full.length > 2000) return null
  try {
    const u = new URL(full)
    if (u.username || u.password || !u.hostname.includes('.')) return null
    return full
  } catch {
    return null
  }
}

/** 上下文 + 链接 → 线缆上的 `journal` 头（只带有值的键） */
export function tipHead(
  tip: JournalTipContext,
  url: string,
  edited?: { issue?: JournalIssue | ''; query?: string },
): ContactJournalHead {
  const query = (edited?.query ?? tip.query ?? '').slice(0, 120)
  return {
    type: tip.type,
    ...(tip.type === 'wrong' && edited?.issue ? { issue: edited.issue } : {}),
    ...(tip.journalId ? { journal_id: tip.journalId } : {}),
    ...(tip.journalLabel ? { journal_label: tip.journalLabel } : {}),
    ...(tip.type === 'missing' && query ? { query } : {}),
    ...(tip.ruleKey ? { rule_key: tip.ruleKey } : {}),
    ...(tip.current ? { current: tip.current } : {}),
    ...(tip.sourceId ? { source_id: tip.sourceId } : {}),
    ...(url ? { url } : {}),
    ...(tip.catalog ? { catalog: tip.catalog } : {}),
  }
}

/** 期刊线索入口此刻出不出现（ADR 0213 §五）：期刊库入口开着、「联系作者」可用、引擎说没关期刊联网。三处入口共用这一个判断。 */
export function journalTipsAvailable(capability: ContactCapability | null): boolean {
  return journalCatalogUi() && capability?.journal_tips === true
}

/** 「复制内容」的文本：主题（有就带）+ 正文，别的都不带。 */
export function draftText(d: ContactDraft): string {
  const subject = d.subject.trim()
  return `${subject ? `${subject}\n\n` : ''}${d.body.trim()}`
}

/** 期刊线索发送失败时「复制内容」的文本：线索窗没有主题、正文可空，只复制 `draftText` 会是空的（Codex #2310）。
 *  带上刊名 / 条目 / 目录现值 / 问题类型或期刊名称、正文、链接；`label(key)` 取 `journalTip.*` 的界面文案。 */
export function journalTipText(
  tip: JournalTipContext,
  d: ContactDraft,
  label: (key: string) => string,
): string {
  const lines: string[] = []
  if (tip.type === 'missing') {
    lines.push(label('title.missing'))
    const name = d.query.trim()
    if (name) lines.push(`${label('nameLabel')}: ${name}`)
  } else if (tip.type === 'editor') {
    lines.push([label('title.wrong'), tip.journalLabel, label('editorTag')].filter(Boolean).join(' · '))
  } else {
    lines.push([label('title.wrong'), tip.journalLabel].filter(Boolean).join(' · '))
    if (tip.ruleLabel) lines.push(`${label('rowRule')}: ${tip.ruleLabel}`)
    if (tip.current) lines.push(`${label('rowCurrent')}: ${tip.current}`)
    if (d.issue) lines.push(`${label('issueLabel')}: ${label(`issue.${d.issue}`)}`)
  }
  const body = d.body.trim()
  if (body) lines.push('', body)
  const url = d.url.trim()
  if (url) lines.push('', url)
  return lines.join('\n')
}
