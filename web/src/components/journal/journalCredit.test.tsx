/**
 * 署名（ADR 0213 三期）：反馈窗里默认不勾的「写上我的称呼」、发出去的 `journal.credit`、出处卡的「线索来自 <称呼>」。
 *
 * 判据的主语：带不带 `credit` = `sendContact` 收到的 meta（引擎再按契约校验一次，这里只看前端交了什么）；
 * 「线索来自」出不出现 = 出处卡里有没有 `[data-journal-thanks]`，且文字走文本节点（称呼里的标签原样显示、不成元素）。
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
  fetchContactCapability,
  fetchContactMessages,
  fetchContactStatus,
  markContactRead,
  sendContact,
  type ContactCapability,
  type ContactStatus,
} from '@/lib/api'
import { FEEDBACK_EMAIL } from '@/lib/brand'
import { setLocale, t } from '@/i18n'
import { journalSources, loadCatalog } from '@/lib/journals'
import { journalTipText } from '@/store/contactStore'
import { ContactHost } from '@/components/contact/ContactHost'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { EMPTY_DRAFT, useContactStore } from '@/store/contactStore'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { emptyProject } from '@/types/document'
import { JournalPicker } from './JournalPicker'
import { SourceButton } from './SourceButton'
import { useJournalV2Store } from '@/store/journalV2Store'

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
  vi.mocked(fetchContactCapability).mockResolvedValue(CAP)
  useJournalCatalogStore.setState({ catalog: loadCatalog(vectors.catalog) })
  setCap(CAP)
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_journal_tips')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  // 停掉本条用例发出的轮询（runToken++）：否则它会在后面的用例里落定、调 load() 把 capability 清成 null，
  // 线索窗随之不渲染——CI 慢机上恰好落在「什么都不写直接发送」那条（#2310 两次红）
  useContactStore.getState().reset()
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

const jt = (k: string) => t(`journalTip.${k}`, { ns: 'dialogs' })
const openWrong = async () => {
  await openCard()
  await click(q('[data-journal-report]'))
  await waitTip()
}
const tickCredit = () => click(q('[data-journal-tip-credit-on]'))
const sentJournal = () => vi.mocked(sendContact).mock.calls[0][0].journal!

describe('反馈窗里的署名', () => {
  it('wrong：默认不勾、没有称呼框；不勾直接发送不带 credit', async () => {
    await openWrong()
    const box = q('[data-journal-tip-credit-on]') as HTMLInputElement
    expect(box.checked).toBe(false)
    expect(q('[data-journal-tip-credit]')!.textContent).toBe(jt('credit') + jt('creditHint'))
    expect(q('[data-journal-tip-credit]')!.textContent).toContain('所有人可见')
    expect(q('[data-journal-tip-credit-name]')).toBeNull()
    await click(q('[data-contact-send]'))
    expect(sentJournal().credit).toBeUndefined()
  })

  it('勾了才出现单行称呼框（最多 32 字）；填了随信带净化后的称呼', async () => {
    await openWrong()
    await tickCredit()
    const input = q('[data-journal-tip-credit-name]') as HTMLInputElement
    expect(input.maxLength).toBe(32)
    await typeInto(input, '  李明  ')
    await click(q('[data-contact-send]'))
    expect(sentJournal().credit).toBe('李明')
  })

  it('勾了又取消勾：不带 credit，称呼框收起', async () => {
    await openWrong()
    await tickCredit()
    await typeInto(q('[data-journal-tip-credit-name]') as HTMLInputElement, 'Li')
    await tickCredit()
    expect(q('[data-journal-tip-credit-name]')).toBeNull()
    await click(q('[data-contact-send]'))
    expect(sentJournal().credit).toBeUndefined()
  })

  it.each(['', '   ', 'a@b.c', 'https://x.example', 'www.x.example', 'a‮b', 'x'.repeat(33)])(
    '勾了但称呼不合规（%j）：不发，原地提示',
    async (bad) => {
      await openWrong()
      await tickCredit()
      await typeInto(q('[data-journal-tip-credit-name]') as HTMLInputElement, bad)
      await click(q('[data-contact-send]'))
      expect(sendContact).not.toHaveBeenCalled()
      expect(q('[data-contact-form-error="creditInvalid"]')).not.toBeNull()
    },
  )

  it('missing 也有这一行，勾了带 credit；换一条线索窗时回到不勾', async () => {
    await mount(<JournalPicker />)
    await act(async () => useContactStore.getState().openJournalTip({ type: 'missing', query: 'Foo Journal' }))
    await tickCredit()
    await typeInto(q('[data-journal-tip-credit-name]') as HTMLInputElement, 'Wang')
    await click(q('[data-contact-send]'))
    expect(sentJournal()).toMatchObject({ type: 'missing', credit: 'Wang' })
    await act(async () => useContactStore.getState().reset())
    await act(async () => useContactStore.getState().openJournalTip({ type: 'missing', query: 'Bar' }))
    expect((q('[data-journal-tip-credit-on]') as HTMLInputElement).checked).toBe(false)
  })

  it('editor（编辑部原话）没有这一行，store 也绝不带 credit，即便草稿里残留', async () => {
    await mount(<div />)
    await act(async () => useContactStore.getState().openJournalTip({ type: 'editor', journalId: 'fx-alpha', journalLabel: 'Fixture Alpha' }))
    await waitTip()
    expect(q('[data-journal-tip-credit]')).toBeNull()
    await act(async () => useContactStore.setState((s) => ({ draft: { ...s.draft, body: '原话', creditOn: true, credit: 'Li' } })))
    await click(q('[data-contact-send]'))
    expect(sentJournal().credit).toBeUndefined()
  })

  it('复制兜底文本带上称呼（勾了且合规才带）', () => {
    const tip = { type: 'wrong', journalId: 'fx-alpha', journalLabel: 'Fixture Alpha' } as const
    const label = (k: string) => t(`journalTip.${k}`, { ns: 'dialogs' })
    const on = journalTipText(tip, { ...EMPTY_DRAFT, creditOn: true, credit: ' Li ' }, label)
    expect(on).toContain(`${jt('creditRow')}: Li`)
    expect(journalTipText(tip, { ...EMPTY_DRAFT, creditOn: false, credit: 'Li' }, label)).not.toContain(jt('creditRow'))
    expect(journalTipText(tip, { ...EMPTY_DRAFT, creditOn: true, credit: 'a@b' }, label)).not.toContain(jt('creditRow'))
  })

  it('英文界面有同义的一句', async () => {
    await setLocale('en-US')
    await openWrong()
    expect(q('[data-journal-tip-credit]')!.textContent).toBe('Credit me in the catalog (visible to everyone)')
  })
})

describe('出处卡的致谢', () => {
  const v2entries = () => {
    const [path, s] = entries('fx-alpha', 'min_raster_dpi')[0]
    return [[path, { ...s, src: 'v2:s:journal', fact_id: 'line' }]] as [string, ReturnType<typeof journalSources>[string]][]
  }
  const patch = (thanks?: Record<string, string>) => ({
    catalog_rev: 1,
    rules: {},
    sources: {},
    confirm: [],
    ...(thanks ? { thanks } : {}),
  })
  const open = async (list = v2entries()) => {
    await mount(<SourceButton journal="Fixture Press" journalId="fx-alpha" entries={list} name="位图分辨率" />)
    await click(q('[data-journal-source]'))
  }

  it('v2 事实有署名：一行灰字「感谢 <称呼> 提示」', async () => {
    useJournalV2Store.setState({ byJournal: { 'fx-alpha': patch({ line: '李明' }) } })
    await open()
    expect(q('[data-journal-thanks]')!.textContent).toBe('感谢 李明 提示')
    expect(q('[data-journal-thanks]')!.getAttribute('title')).toBe('李明')
    expect(q('[data-journal-thanks]')!.className).toContain('truncate')
  })

  it('英文界面：Tip from <name>', async () => {
    await setLocale('en-US')
    useJournalV2Store.setState({ byJournal: { 'fx-alpha': patch({ line: 'Li Ming' }) } })
    await open()
    expect(q('[data-journal-thanks]')!.textContent).toBe('Thanks to Li Ming')
  })

  it('称呼里的标签只是文字：不成元素、不执行', async () => {
    const evil = '<img src=x onerror=alert(1)>'
    useJournalV2Store.setState({ byJournal: { 'fx-alpha': patch({ line: evil }) } })
    await open()
    // 称呼里的 `<` `>` 是合规字符（规则不禁），但无论如何它只会是文本节点
    const el = q('[data-journal-thanks]')
    if (el) {
      expect(el.querySelector('img')).toBeNull()
      expect(el.textContent).toBe(`感谢 ${evil} 提示`)
    }
    expect(document.body.querySelector('img[src="x"]')).toBeNull()
  })

  it.each([
    ['没有署名', patch()],
    ['署名属于别的事实', patch({ other: '李明' })],
  ])('不画：%s', async (_n, p) => {
    useJournalV2Store.setState({ byJournal: { 'fx-alpha': p } })
    await open()
    expect(q('[data-journal-thanks]')).toBeNull()
  })

  it('v1 条目（没有 v2 前缀）即使 fact_id 凑巧对上也不画', async () => {
    useJournalV2Store.setState({ byJournal: { 'fx-alpha': patch({ line: '李明' }) } })
    const [[path, s]] = v2entries()
    await open([[path, { ...s, src: 's2' }]])
    expect(q('[data-journal-thanks]')).toBeNull()
  })
})
