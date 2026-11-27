/**
 * 期刊线索入口（ADR 0213 一期）：出处卡「这条不对」、选择器「没有这本刊」、同一个「联系作者」窗里的线索态、「我的来信」标签。
 *
 * 判据的主语：入口出不出现 = 页面里有没有那个元素（开关三个：期刊入口常量、联系作者、引擎说期刊联网没关）；
 * 发出去的是什么 = `sendContact` 收到的 meta（引擎再按契约校验一次，这里只看前端交了什么）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const uiOn = vi.hoisted(() => ({ value: true }))
vi.mock('@/lib/journalUi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/journalUi')>()),
  journalCatalogUi: () => uiOn.value,
}))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchContactCapability: vi.fn(),
  fetchContactStatus: vi.fn(),
  sendContact: vi.fn(),
  dismissContact: vi.fn(),
  fetchContactMessages: vi.fn(),
  markContactRead: vi.fn(),
  prepareDiagSend: vi.fn(),
  discardDiagSend: vi.fn(),
}))

import vectors from '../../../../tests/golden/journal_vectors.json'
import {
  dismissContact,
  fetchContactMessages,
  fetchContactStatus,
  markContactRead,
  sendContact,
  type ContactCapability,
  type ContactMessage,
  type ContactStatus,
} from '@/lib/api'
import { FEEDBACK_EMAIL } from '@/lib/brand'
import { setLocale, t } from '@/i18n'
import { journalSources, loadCatalog } from '@/lib/journals'
import { normalizeTipUrl } from '@/store/contactStore'
import { ContactHost } from '@/components/contact/ContactHost'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { EMPTY_DRAFT, useContactStore } from '@/store/contactStore'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { emptyProject } from '@/types/document'
import { JournalPicker } from './JournalPicker'
import { SourceButton } from './SourceButton'

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
  max_total_bytes: 50 * 1024 * 1024,
  unread: 0,
  retention_days: 365,
  journal_tips: true,
}
const DONE: ContactStatus = { state: 'done', gen: 1, message_id: 'TVM-ABCD-EFGH-JKMN-PQRS', inbox_saved: true }

let host: HTMLElement
let root: Root
const mount = async (node: React.ReactNode) => {
  await act(async () => {
    root.render(
      <TooltipProvider>
        {node}
        <ContactHost />
      </TooltipProvider>,
    )
  })
}
const click = async (el: Element | null) => {
  expect(el).not.toBeNull()
  await act(async () => {
    ;(el as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}
const q = (sel: string) => document.body.querySelector(sel)
/** 点开入口后反馈窗异步挂载（CI 慢机上一轮 act 未必刷到）：等窗出现再往下点 */
const waitTip = () => vi.waitFor(() => expect(q('[data-dialog="journal-tip"]')).not.toBeNull())
const typeInto = async (el: HTMLInputElement | HTMLTextAreaElement | null, value: string) => {
  expect(el).not.toBeNull()
  await act(async () => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value)
    el!.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const setCap = (cap: ContactCapability | null) =>
  useContactStore.setState({
    capability: cap,
    open: false,
    view: 'write',
    draft: { ...EMPTY_DRAFT },
    phase: 'edit',
    status: null,
    failure: null,
    formError: null,
    journalTip: null,
    draftStash: null,
    messages: [],
  })
const entries = (id: string, path: string) =>
  [[path, journalSources(id, loadCatalog(vectors.catalog))[path]]] as [string, ReturnType<typeof journalSources>[string]][]

beforeEach(async () => {
  await setLocale('zh-CN')
  localStorage.clear()
  uiOn.value = true
  vi.mocked(dismissContact).mockResolvedValue({ state: 'idle', gen: 0 })
  vi.mocked(fetchContactMessages).mockResolvedValue({ messages: [], unread: 0 })
  vi.mocked(markContactRead).mockResolvedValue({ unread: 0 })
  vi.mocked(sendContact).mockResolvedValue({ state: 'sending', gen: 1 })
  vi.mocked(fetchContactStatus).mockResolvedValue(DONE)
  useJournalCatalogStore.setState({ catalog: loadCatalog(vectors.catalog) })
  setCap(CAP)
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_journal_tips')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  vi.clearAllMocks()
  setCap(null)
})

const openCard = async () => {
  await mount(
    <SourceButton journal="Fixture Press" journalId="fx-alpha" entries={entries('fx-alpha', 'min_raster_dpi')} name="位图分辨率" />,
  )
  await click(q('[data-journal-source]'))
}

describe('出处卡「这条不对」', () => {
  it('和「打开原网页」同一行的文字链接；点开是专用的「期刊规范反馈」窗：灰字刊名、条目 / 现值 / 出处三行', async () => {
    await openCard()
    const link = q('[data-journal-report]')!
    expect(link.tagName).toBe('BUTTON')
    expect(link.textContent).toBe(t('journal.card.report', { ns: 'dialogs' }))
    expect(link.parentElement).toBe(q('[data-journal-open]')!.parentElement)
    await click(link)
    await waitTip()
    expect(q('[data-dialog="journal-tip"]')).not.toBeNull()
    expect(q('[data-dialog="contact"]')).toBeNull() // 不是「联系作者」窗
    expect(q('[data-dialog="journal-tip"]')!.textContent).toContain(t('journalTip.title.wrong', { ns: 'dialogs' }))
    expect(q('[data-journal-tip-about]')!.textContent).toBe('Fixture Press')
    expect(q('[data-journal-tip-row="rule"]')).not.toBeNull()
    expect(q('[data-journal-tip-row="current"]')).not.toBeNull()
    const src = q('[data-journal-tip-row="source"] dd')!.textContent!
    expect(src).toMatch(/·/) // 主机名 · 抓取日期
    expect(q('[data-contact-journal-url]')).not.toBeNull()
    expect(q('[data-contact-subject]')).toBeNull() // 没有主题框
    expect(q('[data-contact-diag]')).toBeNull() // 没有诊断信息
    expect(q('[data-contact-switch-view]')).toBeNull() // 没有「我的来信」
    expect(q('[data-contact-wecom-line]')).toBeNull()
    expect(q('[data-contact-pick]')!.textContent).toBe(t('journalTip.attach', { ns: 'dialogs' }))
    expect(q('[data-contact-send]')!.textContent).toBe(t('journalTip.submit', { ns: 'dialogs' }))
  })

  it('「问题」分段四项、默认不选；选了随信带 issue；原文进正文', async () => {
    await openCard()
    await click(q('[data-journal-report]'))
    await waitTip()
    const radios = [...document.body.querySelectorAll('[data-testid="journal-tip-issue"] [role="radio"]')]
    expect(radios.map((r) => r.textContent)).toEqual(
      ['wrong_value', 'updated', 'link_broken', 'other'].map((k) => t(`journalTip.issue.${k}`, { ns: 'dialogs' })),
    )
    expect(radios.some((r) => r.getAttribute('aria-checked') === 'true')).toBe(false)
    const again = [...document.body.querySelectorAll('[data-testid="journal-tip-issue"] [role="radio"]')]
    await click(again[1])
    await typeInto(q('[data-contact-text]') as HTMLTextAreaElement, '官网现在写 0.25 pt')
    await click(q('[data-contact-send]'))
    const [meta] = vi.mocked(sendContact).mock.calls[0]
    expect(meta.journal?.issue).toBe('updated')
    expect(meta.body).toBe('官网现在写 0.25 pt')
  })

  it('字段随「问题」联动：只换标签，已填内容不清；出处主机名是外链', async () => {
    await openCard()
    await click(q('[data-journal-report]'))
    await waitTip()
    const d = () => q('[data-dialog="journal-tip"]')!.textContent!
    const jt = (k: string) => t(`journalTip.${k}`, { ns: 'dialogs' })
    expect(d()).toContain(jt('quoteLabel'))
    expect(d()).toContain(jt('urlLabel'))
    const link = q('[data-journal-tip-source-link]') as HTMLAnchorElement
    expect(link.target).toBe('_blank')
    expect(link.href).toMatch(/^https?:/)
    expect(q('[data-journal-tip-entry]')!.previousElementSibling).toBeNull() // 没有小标题
    await typeInto(q('[data-contact-text]') as HTMLTextAreaElement, '原话')
    const radios = () => [...document.body.querySelectorAll('[data-testid="journal-tip-issue"] [role="radio"]')]
    await click(radios()[2])
    expect(d()).toContain(jt('urlLabelNew'))
    expect(d()).toContain(jt('noteLabel'))
    expect(d()).not.toContain(jt('quoteLabel'))
    await click(radios()[3])
    expect(d()).toContain(jt('descLabel'))
    expect((q('[data-contact-text]') as HTMLTextAreaElement).value).toBe('原话')
    expect((q('[data-contact-email]') as HTMLInputElement).placeholder).toBe(jt('emailWrong'))
  }, 20_000) // 连点多步：整机满载时默认 5 s 不够

  it('普通「联系作者」窗没有任何期刊字段', async () => {
    await mount(<div />)
    await act(async () => useContactStore.getState().setOpen(true))
    expect(q('[data-dialog="contact"]')).not.toBeNull()
    expect(q('[data-dialog="journal-tip"]')).toBeNull()
    expect(q('[data-contact-journal-url]')).toBeNull()
    expect(q('[data-contact-subject]')).not.toBeNull()
  })

  it('三个开关缺一就不出现：联系作者关着 / 引擎说期刊联网被关 / 旧引擎不带 journal_tips', async () => {
    for (const cap of [null, { ...CAP, journal_tips: false }, { ...CAP, journal_tips: undefined }]) {
      setCap(cap)
      await openCard()
      expect(q('[data-journal-open]')).not.toBeNull()
      expect(q('[data-journal-report]')).toBeNull()
      await act(async () => root.render(<div />))
    }
  })

  it('期刊入口常量关着：不出现', async () => {
    uiOn.value = false
    await openCard()
    expect(q('[data-journal-report]')).toBeNull()
  })

  it('什么都不写直接发送：带着刊 id / 规则键 / 当前值 / 出处 id / 目录版本，不带主题与诊断编号', async () => {
    await openCard()
    await click(q('[data-journal-report]'))
    await waitTip()
    await click(q('[data-contact-send]'))
    expect(sendContact).toHaveBeenCalledTimes(1)
    const [meta] = vi.mocked(sendContact).mock.calls[0]
    expect(meta.subject).toBe('')
    expect(meta.body).toBe('')
    expect(meta.report_id).toBeUndefined()
    expect(meta.journal).toMatchObject({
      type: 'wrong',
      journal_id: 'fx-alpha',
      journal_label: 'Fixture Press',
      rule_key: 'min_raster_dpi',
      catalog: { v1: vectors.catalog.catalog_rev },
    })
    expect(meta.journal?.current).toBeTruthy()
    expect(meta.journal?.url).toBeUndefined()
    expect(meta.journal?.issue).toBeUndefined() // 不选就不带
  })

  it('原文链接：没写协议补 https；http 不收，原样提示、不发', async () => {
    await openCard()
    await click(q('[data-journal-report]'))
    await waitTip()
    await typeInto(q('[data-contact-journal-url]') as HTMLInputElement, 'example.org/guide')
    await click(q('[data-contact-send]'))
    expect(vi.mocked(sendContact).mock.calls[0][0].journal?.url).toBe('https://example.org/guide')
    vi.mocked(sendContact).mockClear()
    await act(async () => useContactStore.getState().reset())
    await act(async () =>
      useContactStore.getState().openJournalTip({ type: 'wrong', journalId: 'fx-alpha', journalLabel: 'Fixture Press' }),
    )
    await typeInto(q('[data-contact-journal-url]') as HTMLInputElement, 'http://example.org/guide')
    await click(q('[data-contact-send]'))
    expect(sendContact).not.toHaveBeenCalled()
    expect(q('[data-contact-form-error="urlInvalid"]')).not.toBeNull()
  })

  it('线索窗关掉再从顶栏打开：写到一半的普通来信还在', async () => {
    useContactStore.setState({ draft: { ...EMPTY_DRAFT, body: '普通来信写了一半' } })
    await openCard()
    await click(q('[data-journal-report]'))
    await waitTip()
    expect((q('[data-contact-text]') as HTMLTextAreaElement).value).toBe('')
    await act(async () => useContactStore.getState().setOpen(false))
    await act(async () => useContactStore.getState().setOpen(true))
    expect(useContactStore.getState().journalTip).toBeNull()
    expect(useContactStore.getState().draft.body).toBe('普通来信写了一半')
  })
})

describe('选择器「没有这本刊」', () => {
  const search = async (v: string) => {
    await click(q('[data-journal-picker]'))
    await typeInto(q('[data-journal-search]') as HTMLInputElement, v)
  }

  it('搜不到那一行句尾多一个链接；点开带着搜索词，type=missing，有搜索词就能直接发', async () => {
    await mount(<JournalPicker />)
    await search('zzzz-no-such')
    const empty = q('[data-journal-empty]')!
    expect(empty.textContent).toContain(t('journal.noMatch', { ns: 'dialogs' }))
    const link = empty.querySelector('button')!
    expect(link.textContent).toBe(t('journal.noJournalListed', { ns: 'dialogs' }))
    await click(link)
    await waitTip()
    expect(q('[data-dialog="journal-tip"]')!.textContent).toContain(t('journalTip.title.missing', { ns: 'dialogs' }))
    expect(q('[data-journal-tip-about]')).toBeNull() // 申请收录没有灰字
    expect((q('[data-journal-tip-name]') as HTMLInputElement).value).toBe('zzzz-no-such')
    expect(q('[data-contact-send]')!.textContent).toBe(t('journalTip.submitMissing', { ns: 'dialogs' }))
    expect((q('[data-contact-email]') as HTMLInputElement).placeholder).toBe(t('journalTip.emailMissing', { ns: 'dialogs' }))
    expect(q('[data-journal-tip-entry]')).toBeNull() // 也没有「问题」分段
    await click(q('[data-contact-send]'))
    const [meta] = vi.mocked(sendContact).mock.calls[0]
    expect(meta.journal).toMatchObject({ type: 'missing', query: 'zzzz-no-such' })
    expect(meta.journal?.journal_id).toBeUndefined()
    expect(meta.journal?.issue).toBeUndefined()
  })

  it('期刊名称可以改：发出去的是改后的字；清空又没链接没附件就不发', async () => {
    await mount(<JournalPicker />)
    await act(async () => useContactStore.getState().openJournalTip({ type: 'missing', query: 'zzzz' }))
    await typeInto(q('[data-journal-tip-name]') as HTMLInputElement, 'Journal of Fixtures')
    await click(q('[data-contact-send]'))
    expect(vi.mocked(sendContact).mock.calls[0][0].journal?.query).toBe('Journal of Fixtures')
    vi.mocked(sendContact).mockClear()
    await act(async () => useContactStore.getState().reset())
    await act(async () => useContactStore.getState().openJournalTip({ type: 'missing', query: 'zzzz' }))
    await typeInto(q('[data-journal-tip-name]') as HTMLInputElement, '')
    await click(q('[data-contact-send]'))
    expect(sendContact).not.toHaveBeenCalled()
    expect(q('[data-contact-form-error="journalNeedsClue"]')).not.toBeNull()
  })

  it('开关不满足时只剩原来那一句，没有链接', async () => {
    setCap({ ...CAP, journal_tips: false })
    await mount(<JournalPicker />)
    await search('zzzz-no-such')
    expect(q('[data-journal-empty]')!.querySelector('button')).toBeNull()
  })

  it('搜索框是空的（没有可发的线索）又没链接没附件：不发，说一句', async () => {
    await mount(<JournalPicker />)
    await act(async () => useContactStore.getState().openJournalTip({ type: 'missing', query: '' }))
    await click(q('[data-contact-send]'))
    expect(sendContact).not.toHaveBeenCalled()
    expect(q('[data-contact-form-error="journalNeedsClue"]')).not.toBeNull()
  })
})

describe('我的来信', () => {
  it('kind=journal 的信显示「期刊线索」标签', async () => {
    const m: ContactMessage = {
      message_id: 'TVM-ABCD-EFGH-JKMN-PQRS',
      sent_at: 1_760_000_000,
      route: 'cn',
      kind: 'journal',
      state: 'new',
      unread: 0,
      subject: '期刊线索：Nature',
      body: '',
      attachment_names: [],
      replies: [],
    }
    vi.mocked(fetchContactMessages).mockResolvedValue({ messages: [m], unread: 0 })
    await mount(<div />)
    await act(async () => useContactStore.getState().setOpen(true, 'letters'))
    // 来信列表异步拉取：满载时一轮 act 未必刷到，等它出现
    await vi.waitFor(() => expect(q('[data-contact-letter-kind="journal"]')?.textContent).toBe('期刊线索'))
  })
})

describe('原文链接的整理', () => {
  it.each([
    ['', ''],
    ['  ', ''],
    ['example.org/a', 'https://example.org/a'],
    ['https://example.org/a', 'https://example.org/a'],
    ['http://example.org/a', null],
    ['ftp://example.org/a', null],
    ['https://u:p@example.org/a', null],
    ['https://example.org/a b', null],
    ['localhost', null],
  ])('%s -> %s', (raw, out) => {
    expect(normalizeTipUrl(raw)).toBe(out)
  })
})
