/**
 * 「联系作者」（ADR 0145）：入口两处挂载、窄屏只留图标、对话框四个状态、失败只给复制（没有任何邮件协议链接）、
 * 未读小圆点与通知轨胶囊、草稿关窗不丢、附上诊断信息的 prepare → 预览 → start → 带编号发信。
 *
 * 判据的主语：**哪些本机 API 被调用了**——`sendContact` 是唯一由用户点「发送」引出远程请求的调用，
 * 所以「没点发送 = 零远程」在前端这一侧就是它的调用次数为 0；`startDiagSend` 同理。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchContactCapability: vi.fn(),
  fetchContactStatus: vi.fn(),
  sendContact: vi.fn(),
  cancelContact: vi.fn(),
  dismissContact: vi.fn(),
  fetchContactMessages: vi.fn(),
  refreshContactMessages: vi.fn(),
  markContactRead: vi.fn(),
  forgetContactMessage: vi.fn(),
  prepareDiagSend: vi.fn(),
  startDiagSend: vi.fn(),
  fetchDiagSendStatus: vi.fn(),
  cancelDiagSend: vi.fn(),
  discardDiagSend: vi.fn(),
}))

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
  markContactRead,
  prepareDiagSend,
  sendContact,
  startDiagSend,
  type ContactCapability,
  type ContactMessage,
  type ContactStatus,
  type DiagSendPrepared,
} from '@/lib/api'
import { FEEDBACK_EMAIL } from '@/lib/brand'
import { t } from '@/i18n'
import enDialogs from '@/i18n/locales/en-US/dialogs.json'
import zhDialogs from '@/i18n/locales/zh-CN/dialogs.json'
import { ContactButton } from '@/components/contact/ContactButton'
import { ContactHost } from '@/components/contact/ContactHost'
import { NotificationRail } from '@/components/StatusBar'
import { ProjectPicker } from '@/components/ProjectPicker'
import { TopBar } from '@/components/TopBar'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { EMPTY_DRAFT, useContactStore } from '@/store/contactStore'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject } from '@/types/document'

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

const CAP: ContactCapability = {
  enabled: true,
  recipient: FEEDBACK_EMAIL,
  subject_max: 120,
  body_max: 5000,
  email_max: 254,
  max_attachments: 5,
  max_total_bytes: 20 * 1024 * 1024,
  extensions: ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.pdf', '.docx', '.xlsx', '.pptx'],
  unread: 0,
  retention_days: 365,
}
const PREPARED: DiagSendPrepared = {
  id: 'pid-1',
  gen: 0,
  size: 20480,
  sha256: 'a'.repeat(64),
  schema: 8,
  max_bytes: 1e7,
  too_large: false,
  entries: [
    { name: 'report.json', kind: 'environment' },
    { name: 'app.log', kind: 'log' },
  ],
}
const st = (over: Partial<ContactStatus>): ContactStatus => ({ state: 'sending', stage: 'init', sent: 0, total: 0, gen: 1, ...over })
const MSG = 'TVM-ABCD-EFGH-JKMN-PQRS'
const message = (over: Partial<ContactMessage> = {}): ContactMessage => ({
  message_id: MSG,
  sent_at: 1_760_000_000,
  route: 'cn',
  state: 'replied',
  unread: 1,
  replies: [{ id: 'r_1', created_at: '2026-10-22T08:00:00Z', body: '收到，下周修复。', read: false }],
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
const click = async (el: Element | null) => {
  expect(el).not.toBeNull()
  await act(async () => (el as HTMLElement).click())
}
const typeInto = async (el: HTMLInputElement | HTMLTextAreaElement | null, value: string) => {
  expect(el).not.toBeNull()
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value)
    el!.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const png = (name = 'shot.png', bytes = 64) => new File([new Uint8Array(bytes).fill(1)], name, { type: 'image/png' })
/** 让轮询（350 ms）转几拍 */
const settle = async (ms = 800) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function resetStore(over: Partial<ReturnType<typeof useContactStore.getState>> = {}) {
  useContactStore.setState({
    capability: CAP,
    open: false,
    view: 'write',
    draft: { ...EMPTY_DRAFT },
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
    rememberedEmail: null,
    ...over,
  })
}

beforeEach(async () => {
  vi.useFakeTimers()
  localStorage.clear()
  vi.mocked(dismissContact).mockResolvedValue(st({ state: 'idle' }))
  vi.mocked(fetchContactCapability).mockResolvedValue(CAP)
  vi.mocked(fetchContactMessages).mockResolvedValue({ messages: [], unread: 0 })
  vi.mocked(markContactRead).mockResolvedValue({ unread: 0 })
  vi.mocked(prepareDiagSend).mockResolvedValue(PREPARED)
  vi.mocked(discardDiagSend).mockResolvedValue({ ok: true })
  vi.mocked(cancelDiagSend).mockResolvedValue({} as never)
  resetStore()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_contact')
})
afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
  document.body.innerHTML = ''
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe('入口两处挂载', () => {
  it('引擎说关着（capability = null）：顶栏与主页都完全不渲染', async () => {
    resetStore({ capability: null })
    await render(
      <>
        <TopBar />
        <ContactHost />
      </>,
    )
    expect(q('[data-contact-button]')).toBeNull()
    expect(q('[data-dialog="contact"]')).toBeNull()
  })

  it('编辑器顶栏：在撤销重做与导出之间，次级样式、不是主按钮', async () => {
    await render(<TopBar />)
    const btn = q('[data-contact-button="topbar"]')!
    expect(btn).not.toBeNull()
    expect(btn.textContent).toContain(t('contact.buttonLabel', { ns: 'dialogs' }))
    expect(btn.getAttribute('data-variant')).toBe('secondary')
    const exportBtn = q('[data-onboarding-anchor="export"]')!
    const undo = q('[data-topbar] button[aria-label]')! // 顶栏里第一颗带名字的按钮不是它；只比与导出的先后
    expect(btn.compareDocumentPosition(exportBtn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(exportBtn.getAttribute('data-variant')).toBe('primary')
    expect(undo).not.toBeNull()
    // 撤销 / 重做在它前面：同一个右侧组里，它的前一个兄弟里含撤销重做
    const group = btn.parentElement!
    const kids = [...group.children]
    const idx = kids.indexOf(btn.closest('[data-contact-button]')!.parentElement === group ? btn : btn.parentElement!)
    expect(idx).toBeGreaterThan(0)
    expect(kids[idx + 1]?.contains(exportBtn) || kids[idx + 1] === exportBtn).toBe(true)
  })

  it('窄于 900 只留图标：文字走 sr-only 进可达名，按钮自己有 aria-label', async () => {
    await render(<TopBar />)
    const btn = q('[data-contact-button="topbar"]')!
    const label = t('contact.buttonLabel', { ns: 'dialogs' })
    expect(btn.getAttribute('aria-label')).toBe(label)
    const text = [...btn.querySelectorAll('span')].find((s) => s.textContent === label)!
    expect(text.className).toContain('max-[899px]:sr-only')
    expect(btn.querySelector('svg')).not.toBeNull()
  })

  it('主页（新手版 / 老手版共用的 HomeView）与「全部项目」视图都有它', async () => {
    await render(<ProjectPicker />)
    expect(q('[data-contact-button="home"]')).not.toBeNull()
    await click(q('[data-home-all]'))
    expect(q('[data-contact-button="home"]')).not.toBeNull()
  })

  it('点它只是打开写信窗口：不联网（sendContact / startDiagSend 为 0 次）', async () => {
    await render(
      <>
        <ContactButton placement="topbar" />
        <ContactHost />
      </>,
    )
    await click(q('[data-contact-button]'))
    expect(q('[data-dialog="contact"]')).not.toBeNull()
    expect(q('[data-contact-recipient]')!.textContent).toBe(FEEDBACK_EMAIL)
    expect(sendContact).not.toHaveBeenCalled()
    expect(startDiagSend).not.toHaveBeenCalled()
  })
})

describe('没有打开项目时（不在工作台的 TooltipProvider 里）', () => {
  it('ContactHost 自带 provider：加附件后带气泡的移除钮能渲染，不崩', async () => {
    resetStore()
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => root.render(<ContactHost />)) // 刻意不包 TooltipProvider
    await act(async () => useContactStore.getState().setOpen(true))
    await act(async () => useContactStore.getState().addFiles([png()]))
    expect(q('[data-contact-file="shot.png"] [data-contact-file-remove]')).not.toBeNull()
  })
})

describe('对话框的四个状态', () => {
  async function openDialog(over: Partial<ReturnType<typeof useContactStore.getState>> = {}) {
    resetStore(over)
    await render(<ContactHost />)
    await act(async () => useContactStore.getState().setOpen(true))
  }

  it('编辑：收件人只展示、主题 / 正文 / 附件 / 诊断 / 回信邮箱与那句说明都在', async () => {
    await openDialog()
    expect(q('[data-contact-recipient]')!.textContent).toBe(FEEDBACK_EMAIL)
    for (const sel of ['subject', 'text', 'pick', 'diag', 'email', 'email-note', 'drop']) {
      expect(q(`[data-contact-${sel}]`), sel).not.toBeNull()
    }
    expect(q('[data-contact-email-note]')!.textContent).toBe(t('contact.emailNote', { ns: 'dialogs' }))
    expect(q('[data-contact-email-note]')!.textContent).toContain('不填也能在 Tavotto 里收到回信')
    // 收件人是文字不是链接，也没有输入框
    expect(q('[data-contact-recipient]')!.closest('a')).toBeNull()
    expect(q('[data-contact-send]')!.hasAttribute('disabled')).toBe(false)
  })

  it('正文为空：点发送只提示，不发（零远程）', async () => {
    await openDialog()
    await click(q('[data-contact-send]'))
    expect(q('[data-contact-form-error="bodyRequired"]')).not.toBeNull()
    expect(sendContact).not.toHaveBeenCalled()
  })

  it('回信邮箱输入时就在框下给一句提示（与服务端同规则：域名含「.」、无引号 / 分号 / 括号）', async () => {
    await openDialog()
    for (const bad of ['a@b', 'a;b@c.com', 'a(b)@c.com', 'a"b@c.com']) {
      await typeInto(q<HTMLInputElement>('[data-contact-email]'), bad)
      expect(q('[data-contact-email-hint]'), bad).not.toBeNull()
    }
    await typeInto(q<HTMLInputElement>('[data-contact-email]'), 'me@example.org')
    expect(q('[data-contact-email-hint]')).toBeNull()
  })

  it('回信邮箱格式不对：本机先挡，不发', async () => {
    await openDialog()
    await typeInto(q<HTMLTextAreaElement>('[data-contact-text]'), 'hello')
    await typeInto(q<HTMLInputElement>('[data-contact-email]'), 'a@@b')
    await click(q('[data-contact-send]'))
    expect(q('[data-contact-form-error="emailInvalid"]')).not.toBeNull()
    expect(sendContact).not.toHaveBeenCalled()
  })

  it('发送中（可取消、显示上传进度）→ 已发送（编号 + 回信去向）；发出的内容与附件如实', async () => {
    vi.mocked(sendContact).mockResolvedValue(st({}))
    vi.mocked(fetchContactStatus)
      .mockResolvedValueOnce(st({ stage: 'upload', sent: 50, total: 100 }))
      .mockResolvedValue(st({ state: 'done', stage: null, message_id: MSG, route: 'cn' }))
    await openDialog()
    await typeInto(q<HTMLInputElement>('[data-contact-subject]'), '建议')
    await typeInto(q<HTMLTextAreaElement>('[data-contact-text]'), '想要一个新功能')
    await typeInto(q<HTMLInputElement>('[data-contact-email]'), 'me@example.org')
    await act(async () => useContactStore.getState().addFiles([png()]))
    await click(q('[data-contact-send]'))

    expect(sendContact).toHaveBeenCalledTimes(1)
    const [meta, files] = vi.mocked(sendContact).mock.calls[0]
    expect(meta).toMatchObject({ subject: '建议', body: '想要一个新功能', reply_email: 'me@example.org' })
    expect('report_id' in meta).toBe(false) // 没勾诊断就不带编号
    expect(files.map((f) => f.name)).toEqual(['shot.png'])

    // 发送中：有取消、状态区在、不能随手关（Esc / 关闭钮被 blockDismiss 挡住）
    expect(q('[data-contact-cancel]')).not.toBeNull()
    expect(q('[data-contact-status="sending"]')).not.toBeNull()
    expect(q('[data-contact-send]')!.hasAttribute('disabled')).toBe(true)
    await settle(400)
    const bar = q('[role="progressbar"]')
    expect(bar?.getAttribute('aria-valuenow')).toBe('50')

    await settle(800)
    expect(q('[data-contact-status="done"]')).not.toBeNull()
    expect(q('[data-contact-message-id]')!.textContent).toContain(MSG)
    expect(q('[data-contact-status="done"]')!.textContent).toContain(t('contact.sentReplyHere', { ns: 'dialogs' }))
    // 成功后邮箱在本机记住（可删）
    expect(localStorage.getItem('tavotto.contact.email')).toBe('me@example.org')
  })

  async function noInboxCase(email: string, key: string) {
    vi.mocked(sendContact).mockResolvedValue(st({}))
    vi.mocked(fetchContactStatus).mockResolvedValue(
      st({ state: 'done', stage: null, message_id: MSG, route: 'cn', inbox_saved: false }),
    )
    localStorage.clear()
    await openDialog()
    await typeInto(q<HTMLTextAreaElement>('[data-contact-text]'), 'hi')
    if (email) await typeInto(q<HTMLInputElement>('[data-contact-email]'), email)
    await click(q('[data-contact-send]'))
    await settle(800)
    const done = q('[data-contact-status="done"]')!
    expect(done.textContent).toContain(t(key, { ns: 'dialogs' }))
    expect(done.textContent).not.toContain(t('contact.sentReplyHere', { ns: 'dialogs' }))
    expect(done.textContent).toContain(MSG)
    expect(q('[data-contact-inbox-missing]')).not.toBeNull()
  }

  it('已送达但本机存不下回信（inbox_saved=false），留了邮箱：说作者会回到邮箱', async () => {
    await noInboxCase('me@example.org', 'contact.sentNoInboxEmail')
  })

  it('已送达但本机存不下回信（inbox_saved=false），没留邮箱：说收不到回复、请留邮箱再发', async () => {
    await noInboxCase('', 'contact.sentNoInboxNoEmail')
  })

  it('发送中点取消：调用引擎取消，回到可编辑的草稿并说「没有发送」，内容还在', async () => {
    vi.mocked(sendContact).mockResolvedValue(st({}))
    vi.mocked(cancelContact).mockResolvedValue(st({ state: 'cancelling' }))
    let polls = 0
    vi.mocked(fetchContactStatus).mockImplementation(async () =>
      ++polls < 3 ? st({ stage: 'upload', sent: 1, total: 10 }) : st({ state: 'cancelled', stage: null }),
    )
    await openDialog()
    await typeInto(q<HTMLTextAreaElement>('[data-contact-text]'), '别发')
    await click(q('[data-contact-send]'))
    await click(q('[data-contact-cancel]'))
    expect(cancelContact).toHaveBeenCalledTimes(1)
    await settle(1500)
    expect(useContactStore.getState().phase).toBe('edit')
    expect(q<HTMLTextAreaElement>('[data-contact-text]')!.value).toBe('别发')
    expect(q('[data-contact-status="done"]')).toBeNull()
  })

  it('失败：草稿保留；只有「再试一次 / 复制内容 / 复制地址」，页面里没有任何邮件协议链接', async () => {
    vi.mocked(sendContact).mockResolvedValue(st({}))
    vi.mocked(fetchContactStatus).mockResolvedValue(
      st({ state: 'failed', stage: null, code: 'network', retryable: true, retry_after: null }),
    )
    await openDialog()
    await typeInto(q<HTMLTextAreaElement>('[data-contact-text]'), '很重要的一段话')
    await click(q('[data-contact-send]'))
    await settle(800)

    expect(q('[data-contact-status="failed"]')).not.toBeNull()
    expect(q('[data-contact-failure="network"]')!.textContent).toContain(t('contact.failure.network', { ns: 'dialogs' }))
    expect(q<HTMLTextAreaElement>('[data-contact-text]')!.value).toBe('很重要的一段话')
    expect(q('[data-contact-send]')!.textContent).toBe(t('contact.retry', { ns: 'dialogs' }))
    const fallback = q('[data-contact-fallback]')!
    const names = [...fallback.querySelectorAll('button')].map((b) => b.textContent)
    expect(names).toEqual([t('contact.copyContent', { ns: 'dialogs' }), t('contact.copyAddress', { ns: 'dialogs' })])
    // 全页没有任何 mailto 链接，也没有指向邮件的 <a>
    expect(document.body.innerHTML.toLowerCase()).not.toContain('mailto')
    expect(document.body.querySelectorAll('a[href^="mailto"]').length).toBe(0)

    // 复制内容 / 复制地址：写进剪贴板的就是用户的正文与地址，没有别的
    const writeText = vi.fn(async (_text: string) => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const [copyContent, copyAddress] = [...fallback.querySelectorAll('button')]
    await click(copyContent)
    await click(copyAddress)
    expect(writeText.mock.calls.map((c) => c[0])).toEqual(['很重要的一段话', FEEDBACK_EMAIL])
    expect(copyContent.textContent).toBe(t('contact.copied', { ns: 'dialogs' })) // 复制后自己说「已复制」

    // 再试一次 = 又一次发送，带的还是那份草稿
    vi.mocked(fetchContactStatus).mockResolvedValue(st({ state: 'done', stage: null, message_id: MSG, route: 'cn' }))
    await click(q('[data-contact-send]'))
    await settle(800)
    expect(sendContact).toHaveBeenCalledTimes(2)
    expect(vi.mocked(sendContact).mock.calls[1][0].body).toBe('很重要的一段话')
    expect(q('[data-contact-status="done"]')).not.toBeNull()
  })

  it('本机输入校验码（引擎 422）：说人话、不给「再试」的空话、草稿不丢', async () => {
    vi.mocked(sendContact).mockRejectedValue(new ApiError('x', 422, { code: 'contact_attachment_type' }))
    await openDialog()
    await typeInto(q<HTMLTextAreaElement>('[data-contact-text]'), 'hi')
    await click(q('[data-contact-send]'))
    expect(q('[data-contact-failure="attachment_type"]')!.textContent).toContain(
      t('contact.error.attachment_type', { ns: 'dialogs' }),
    )
    expect(q<HTMLTextAreaElement>('[data-contact-text]')!.value).toBe('hi')
  })

  it('草稿关窗不丢：关掉再打开，主题 / 正文 / 附件都还在；发送成功后再关才清空', async () => {
    await openDialog()
    await typeInto(q<HTMLInputElement>('[data-contact-subject]'), '主题')
    await typeInto(q<HTMLTextAreaElement>('[data-contact-text]'), '没写完')
    await act(async () => useContactStore.getState().addFiles([png()]))
    await click(q('[data-contact-dismiss]'))
    expect(q('[data-dialog="contact"]')).toBeNull()
    await act(async () => useContactStore.getState().setOpen(true))
    expect(q<HTMLInputElement>('[data-contact-subject]')!.value).toBe('主题')
    expect(q<HTMLTextAreaElement>('[data-contact-text]')!.value).toBe('没写完')
    expect(q('[data-contact-file="shot.png"]')).not.toBeNull()

    vi.mocked(sendContact).mockResolvedValue(st({}))
    vi.mocked(fetchContactStatus).mockResolvedValue(st({ state: 'done', stage: null, message_id: MSG, route: 'cn' }))
    await click(q('[data-contact-send]'))
    await settle(800)
    await click(q('[data-contact-close]'))
    await act(async () => useContactStore.getState().setOpen(true))
    expect(q<HTMLTextAreaElement>('[data-contact-text]')!.value).toBe('')
  })

  it('附件：类型 / 数量 / 合计大小在本机先挡', async () => {
    await openDialog()
    await act(async () => useContactStore.getState().addFiles([new File(['x'], 'a.exe')]))
    expect(q('[data-contact-file-error="type"]')).not.toBeNull()
    await act(async () => useContactStore.getState().addFiles(Array.from({ length: 6 }, (_, i) => png(`p${i}.png`, 10 + i))))
    expect(useContactStore.getState().draft.files).toHaveLength(5)
    expect(q('[data-contact-file-error="count"]')).not.toBeNull()
    await act(async () => useContactStore.getState().removeFile(0))
    await act(async () => useContactStore.getState().addFiles([png('big.png', 20 * 1024 * 1024)]))
    expect(q('[data-contact-file-error="total"]')).not.toBeNull()
  })

  it('记住的邮箱可以删', async () => {
    localStorage.setItem('tavotto.contact.email', 'me@example.org')
    resetStore({ rememberedEmail: 'me@example.org', draft: { ...EMPTY_DRAFT, replyEmail: 'me@example.org' } })
    await render(<ContactHost />)
    await act(async () => useContactStore.getState().setOpen(true))
    expect(q<HTMLInputElement>('[data-contact-email]')!.value).toBe('me@example.org')
    await click(q('[data-contact-email-forget]'))
    expect(localStorage.getItem('tavotto.contact.email')).toBeNull()
    expect(q<HTMLInputElement>('[data-contact-email]')!.value).toBe('')
    expect(q('[data-contact-email-remembered]')).toBeNull()
  })
})

describe('附上诊断信息：prepare → 预览 → start → 带编号发信', () => {
  it('勾选只备包（本机，零远程）并列出内容；点发送才 start，拿到报告编号后才发信', async () => {
    resetStore()
    await render(<ContactHost />)
    await act(async () => useContactStore.getState().setOpen(true))
    await typeInto(q<HTMLTextAreaElement>('[data-contact-text]'), '带诊断')
    await click(q('input[data-contact-diag]'))
    await settle(10)
    expect(prepareDiagSend).toHaveBeenCalledTimes(1)
    expect(startDiagSend).not.toHaveBeenCalled()
    expect(sendContact).not.toHaveBeenCalled()
    expect(q('[data-contact-diag-state="ready"]')).not.toBeNull()
    expect(q('[data-contact-diag-kinds] [data-diag-kind="environment"]')).not.toBeNull()
    expect(q('[data-contact-diag-kinds] [data-diag-kind="log"]')).not.toBeNull()

    vi.mocked(startDiagSend).mockResolvedValue({} as never)
    vi.mocked(fetchDiagSendStatus).mockResolvedValue({
      id: 'pid-1', state: 'done', stage: null, size: 1, sha256: 'a', sent: 1, total: 1,
      report_id: 'TVD-AAAA-BBBB-CCCC-DDDD',
    })
    vi.mocked(sendContact).mockResolvedValue(st({}))
    vi.mocked(fetchContactStatus).mockResolvedValue(st({ state: 'done', stage: null, message_id: MSG, route: 'cn' }))
    await click(q('[data-contact-send]'))
    await settle(1500)

    expect(startDiagSend).toHaveBeenCalledTimes(1)
    expect(vi.mocked(startDiagSend).mock.calls[0][0]).toBe('pid-1')
    expect(sendContact).toHaveBeenCalledTimes(1)
    expect(vi.mocked(sendContact).mock.calls[0][0].report_id).toBe('TVD-AAAA-BBBB-CCCC-DDDD')
    expect(q('[data-contact-status="done"]')).not.toBeNull()
  })

  it('诊断包发不出去：信不发，失败说清是诊断包的事，草稿与勾选都在', async () => {
    resetStore()
    await render(<ContactHost />)
    await act(async () => useContactStore.getState().setOpen(true))
    await typeInto(q<HTMLTextAreaElement>('[data-contact-text]'), 'x')
    await click(q('input[data-contact-diag]'))
    await settle(10)
    vi.mocked(startDiagSend).mockResolvedValue({} as never)
    vi.mocked(fetchDiagSendStatus).mockResolvedValue({
      id: 'pid-1', state: 'failed', stage: null, size: 1, sha256: 'a', sent: 0, total: 0, code: 'network', retryable: true,
    })
    await click(q('[data-contact-send]'))
    await settle(1000)
    expect(sendContact).not.toHaveBeenCalled()
    expect(q('[data-contact-status="failed"]')).not.toBeNull()
    expect(q('[data-contact-failure="network"]')!.textContent).toContain(
      t('contact.diagFailure', { ns: 'dialogs', reason: t('settings.diagnostics.send.failure.network', { ns: 'dialogs' }) }),
    )
    expect(useContactStore.getState().draft.attachDiag).toBe(true)
  })

  it('诊断包已传完、信发失败：重试不再 start 诊断，带原编号直接发信', async () => {
    resetStore()
    await render(<ContactHost />)
    await act(async () => useContactStore.getState().setOpen(true))
    await typeInto(q<HTMLTextAreaElement>('[data-contact-text]'), '重试')
    await click(q('input[data-contact-diag]'))
    await settle(10)
    vi.mocked(startDiagSend).mockResolvedValue({} as never)
    vi.mocked(fetchDiagSendStatus).mockResolvedValue({
      id: 'pid-1', state: 'done', stage: null, size: 1, sha256: 'a', sent: 1, total: 1,
      report_id: 'TVD-AAAA-BBBB-CCCC-DDDD',
    })
    vi.mocked(sendContact).mockResolvedValue(st({}))
    vi.mocked(fetchContactStatus).mockResolvedValue(
      st({ state: 'failed', stage: null, code: 'network', retryable: true, retry_after: null }),
    )
    await click(q('[data-contact-send]'))
    await settle(1500)
    expect(q('[data-contact-status="failed"]')).not.toBeNull()
    expect(startDiagSend).toHaveBeenCalledTimes(1)

    vi.mocked(fetchContactStatus).mockResolvedValue(st({ state: 'done', stage: null, message_id: MSG, route: 'cn' }))
    await click(q('[data-contact-send]'))
    await settle(1500)
    expect(startDiagSend).toHaveBeenCalledTimes(1)
    expect(sendContact).toHaveBeenCalledTimes(2)
    expect(vi.mocked(sendContact).mock.calls[1][0].report_id).toBe('TVD-AAAA-BBBB-CCCC-DDDD')
    expect(q('[data-contact-status="done"]')).not.toBeNull()
  })

  it('取消勾选就丢掉备好的包', async () => {
    resetStore()
    await render(<ContactHost />)
    await act(async () => useContactStore.getState().setOpen(true))
    await click(q('input[data-contact-diag]'))
    await settle(10)
    await click(q('input[data-contact-diag]'))
    expect(useContactStore.getState().diag).toEqual({ state: 'off', prepared: null })
    expect(q('[data-contact-diag-details]')).toBeNull()
  })
})

describe('未读回信：小圆点、通知轨胶囊、我的来信', () => {
  it('有未读：按钮上一个小圆点；没有就没有', async () => {
    await render(<ContactButton placement="topbar" />)
    expect(q('[data-contact-dot]')).toBeNull()
    await act(async () => useContactStore.setState({ unread: 2 }))
    expect(q('[data-contact-dot]')).not.toBeNull()
    expect(q('[data-contact-button]')!.getAttribute('data-contact-unread')).toBe('2')
  })

  it('通知轨里一枚胶囊（不是横条）：点「查看」打开我的来信；× 收起，未读数增加才重新出现', async () => {
    await render(
      <>
        <NotificationRail />
        <ContactHost />
      </>,
    )
    expect(q('[data-contact-capsule]')).toBeNull()
    await act(async () => useContactStore.setState({ unread: 1 }))
    const cap = q('[data-contact-capsule]')!
    expect(cap).not.toBeNull()
    expect(cap.getAttribute('data-toast-layout')).toBe('pill') // 通知轨的胶囊形式，不进文档流
    expect(cap.textContent).toContain(t('contact.capsule', { ns: 'dialogs' }))

    const closeLabel = t('contact.capsuleClose', { ns: 'dialogs' })
    await click(cap.querySelector(`button[aria-label="${closeLabel}"]`))
    await settle(200)
    expect(q('[data-contact-capsule]')).toBeNull()
    await act(async () => useContactStore.setState({ unread: 1 }))
    expect(q('[data-contact-capsule]')).toBeNull() // 同一个数不重复打扰
    await act(async () => useContactStore.setState({ unread: 2 }))
    expect(q('[data-contact-capsule]')).not.toBeNull()

    vi.mocked(fetchContactMessages).mockResolvedValue({ messages: [message({ unread: 2 })], unread: 2 })
    const open = [...q('[data-contact-capsule]')!.querySelectorAll('button')].find(
      (b) => b.textContent === t('contact.capsuleOpen', { ns: 'dialogs' }),
    )!
    await click(open)
    await settle(200)
    expect(useContactStore.getState().open).toBe(true)
    expect(useContactStore.getState().view).toBe('letters')
    expect(q('[data-contact-letters]')).not.toBeNull()
    expect(q('[data-contact-capsule]')).toBeNull() // 窗口开着时胶囊让位（退场动画走完后卸载）
  })

  it('我的来信：列出回信并标「新」；看过即已读（调用 markContactRead），未读数归零', async () => {
    vi.mocked(fetchContactMessages).mockResolvedValue({ messages: [message()], unread: 1 })
    vi.mocked(markContactRead).mockResolvedValue({ unread: 0 })
    resetStore({ unread: 1 })
    await render(<ContactHost />)
    await act(async () => useContactStore.getState().setOpen(true, 'letters'))
    await settle(10)
    expect(q(`[data-contact-letter="${MSG}"]`)).not.toBeNull()
    expect(q('[data-contact-reply="r_1"]')!.textContent).toContain('收到，下周修复。')
    expect(q('[data-contact-reply-new]')).not.toBeNull()
    expect(markContactRead).toHaveBeenCalledWith(MSG)
    expect(useContactStore.getState().unread).toBe(0)
  })

  it('没有信时说明去向，而不是空白', async () => {
    resetStore()
    await render(<ContactHost />)
    await act(async () => useContactStore.getState().setOpen(true, 'letters'))
    await settle(10)
    expect(q('[data-contact-letters-empty]')).not.toBeNull()
  })
})

describe('文案与引擎同源', () => {
  const py = readFileSync(resolve(__dirname, '../../../../src/tavotto/engine/contactsend.py'), 'utf-8')

  it('每个引擎失败码都有中英文译文（FAILURES ↔ contact.failure）', () => {
    const block = py.slice(py.indexOf('FAILURES: dict[str, bool] = {'), py.indexOf('LOG_RESULTS'))
    const codes = [...block.matchAll(/^\s+"([a-z_]+)": (?:True|False),/gm)].map((m) => m[1])
    expect(codes.length).toBeGreaterThanOrEqual(20)
    for (const [lang, d] of [
      ['zh-CN', zhDialogs],
      ['en-US', enDialogs],
    ] as const) {
      expect(Object.keys((d as any).contact.failure).sort(), lang).toEqual([...codes].sort())
    }
  })

  it('本机输入校验码（ContactError）也都有译文', () => {
    const codes = new Set([...py.matchAll(/ContactError\("([a-z_]+)"/g)].map((m) => m[1]))
    expect(codes.size).toBeGreaterThanOrEqual(10)
    for (const d of [zhDialogs, enDialogs]) {
      const have = Object.keys((d as any).contact.error)
      for (const c of codes) expect(have, c).toContain(c)
    }
  })

  it('附件类型与上限取自引擎能力说明；前端的扩展名表与契约白名单同源', () => {
    const exts = [...py.slice(py.indexOf('TYPE_BY_EXT'), py.indexOf('ALLOWED_TYPES')).matchAll(/"(\.[a-z]+)":/g)].map(
      (m) => m[1],
    )
    expect(exts.sort()).toEqual([...CAP.extensions!].sort())
    expect(py).toContain('MAX_ATTACHMENTS = 5')
    expect(py).toContain('MAX_TOTAL_BYTES = 20 * 1024 * 1024')
  })
})
