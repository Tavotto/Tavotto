import { create } from 'zustand'
import { buildSendPayload, NO_DETAIL_OPTIONS } from '@/diagnostics/detail'
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
  type ContactMessage,
  type ContactStatus,
  type DiagSendPrepared,
} from '@/lib/api'
import { currentProjectId } from '@/lib/session'

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

export const EMPTY_DRAFT: ContactDraft = { subject: '', body: '', replyEmail: '', files: [], attachDiag: false }

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

  load: () => Promise<void>
  /** 周期性刷新本机未读数；返回停止函数 */
  watch: () => () => void
  setOpen: (open: boolean, view?: ContactView) => void
  setView: (view: ContactView) => void
  setDraft: (patch: Partial<ContactDraft>) => void
  addFiles: (files: File[]) => void
  removeFile: (index: number) => void
  setAttachDiag: (on: boolean) => void
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
let unreadTimer: number | undefined

const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms))

export const useContactStore = create<ContactState>((set, get) => {
  /** 丢弃引擎里备好的诊断包（关窗 / 取消勾选 / 发完）。 */
  const discardDiag = () => {
    diagToken++
    diagDone = null
    const id = get().diag.prepared?.id
    if (id) void discardDiagSend(id, diagPj).catch(() => {})
    set({ diag: { state: 'off', prepared: null } })
  }

  const prepareDiag = async () => {
    const mine = ++diagToken
    diagDone = null
    const pj = currentProjectId()
    diagPj = pj
    set({ diag: { state: 'preparing', prepared: null } })
    try {
      const payload = await buildSendPayload(NO_DETAIL_OPTIONS)
      if (diagToken !== mine) return
      const p = await prepareDiagSend(payload, NO_DETAIL_OPTIONS, pj)
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
      await startDiagSend(id, { category: 'other', note: '', gen: prepared?.gen })
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
      set({ open: true, view: view ?? 'write', formError: null })
      if (view === 'letters') void get().loadMessages()
      if (get().draft.attachDiag && get().diag.state === 'off') void prepareDiag()
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
      const maxTotal = cap?.max_total_bytes ?? 20 * 1024 * 1024
      const exts = new Set(cap?.extensions ?? [])
      const files = [...get().draft.files]
      let error: string | null = null
      for (const f of incoming) {
        if (exts.size > 0 && !exts.has(extOf(f.name))) error = 'type'
        else if (f.size === 0) error = 'empty'
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
      set((s) => ({ draft: { ...s.draft, attachDiag: on } }))
      if (on) void prepareDiag()
      else discardDiag()
    },

    send: async () => {
      const { draft, phase, capability } = get()
      if (phase === 'sending' || phase === 'cancelling' || phase === 'diag') return
      const body = draft.body.trim()
      const email = draft.replyEmail.trim()
      if (!body) return set({ formError: 'bodyRequired' })
      if (body.length > (capability?.body_max ?? 5000)) return set({ formError: 'bodyTooLong' })
      if (draft.subject.trim().length > (capability?.subject_max ?? 120)) return set({ formError: 'subjectTooLong' })
      if (email && !validEmail(email)) return set({ formError: 'emailInvalid' })
      if (!email && get().rememberedEmail) get().forgetEmail() // 清空邮箱框再发 = 忘记已记住的邮箱
      const mine = ++runToken
      set({ formError: null, failure: null, status: null, phase: draft.attachDiag ? 'diag' : 'sending' })
      let reportId: string | undefined
      if (draft.attachDiag) {
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
            subject: draft.subject.trim(),
            body,
            reply_email: email,
            ...(reportId ? { report_id: reportId } : {}),
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
      set({
        draft: { ...EMPTY_DRAFT, replyEmail: get().rememberedEmail ?? '' },
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

/** 「复制内容」的文本：主题（有就带）+ 正文，别的都不带。 */
export function draftText(d: ContactDraft): string {
  const subject = d.subject.trim()
  return `${subject ? `${subject}\n\n` : ''}${d.body.trim()}`
}
