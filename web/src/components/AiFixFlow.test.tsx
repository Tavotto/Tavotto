/**
 * AI 修复卡片（ADR 0149「问与报」）。判据的主语：**渲染出来的 DOM**——「卡片一句话读懂」那把尺子（默认可见区至多一句话、
 * 至多一个主按钮）、点击发出的回答、详情默认折叠；中英文各跑一遍。后端 / store 的网络面用假的。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  isDesktop: vi.fn(() => false),
  pickAnyFile: vi.fn(),
  pickDirectory: vi.fn(),
}))

import { setLocale } from '@/i18n'
import { isDesktop, pickAnyFile, pickDirectory } from '@/lib/desktop'
import { aiFixFlow } from '@/components/AiFixFlow'
import { useAiFixStore, type AiFixEntry } from '@/store/aiFixStore'
import { useDiagSendStore } from '@/store/diagSendStore'
import { visibleBlocks, visiblePrimaryButtons, visibleSentenceCount } from '@/test/visibleBlocks'

let host: HTMLDivElement
let root: Root

const REF = { session_id: 's', script: 'plot.py', attemptId: null }
const entry = (patch: Partial<AiFixEntry>): AiFixEntry => ({ phase: 'waiting', ref: REF, fixId: 'fx_1', ...patch })

function Harness({ e }: { e: AiFixEntry }) {
  const ui = aiFixFlow('script:plot.py', e)
  return (
    <section data-card>
      <p data-title>{ui?.title}</p>
      {ui?.slot}
      {ui?.details ? <details data-details>{ui.details}</details> : null}
      {ui?.primary}
      {ui?.ghost}
    </section>
  )
}

const render = async (e: AiFixEntry) => {
  await act(async () => root.render(<Harness e={e} />))
  return host.querySelector('[data-card]')!
}
const click = async (sel: string) => {
  await act(async () => (host.querySelector(sel) as HTMLElement).click())
}

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  useAiFixStore.setState({ entries: { 'script:plot.py': entry({}) } })
  vi.mocked(isDesktop).mockReturnValue(false)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  useAiFixStore.getState().reset()
  useDiagSendStore.setState({ open: false, presetNote: '', sentCount: 0 })
  await setLocale('zh-CN')
})

const CARDS: Record<string, Partial<AiFixEntry>> = {
  running: { phase: 'running' },
  activity: { phase: 'running', activity: { tool: 'install_packages', phase: 'start', detail: 'colorcet', at_ms: 1 } },
  stalledApproval: { phase: 'running', stalled: { reason: 'cli_waiting_approval', detail: 'change_python' } },
  stalledDelivery: { phase: 'running', stalled: { reason: 'cli_tool_not_delivered', detail: 'install_packages' } },
  stalledIdle: { phase: 'running', stalled: { reason: 'stalled', detail: '' } },
  autoStopped: { phase: 'done', outcome: 'failed', reason: 'cli_waiting_approval' },
  approve: { card: { id: 'c1', kind: 'approve', summary: '把 numpy 降到 1.26。', details: '因为 ABI', reads: ['~/Desktop/data.csv'], not_read: 'x' } },
  approvePkgs: { card: { id: 'c1', kind: 'approve', summary: '建一个环境并装上 colorcet。', packages: ['colorcet'] } },
  choice: { card: { id: 'c1', kind: 'choice', summary: 'data.csv 在哪个文件夹？', options: [{ id: 'a', label: '桌面' }, { id: 'b', label: '文档' }], allow_other: true } },
  pick: { card: { id: 'c1', kind: 'pick', summary: '请选一下数据文件。', pick: 'file' } },
  send: { card: { id: 'c1', kind: 'send', summary: 'AI 写的说明', wants_source: true } },
  done: { phase: 'done', outcome: 'not_fixed', summary: '装不上这个包。' },
  failed: { phase: 'done', outcome: 'failed', reason: 'quota' },
}

describe.each(['zh-CN', 'en-US'] as const)('一句话读懂（%s）', (lang) => {
  it.each(Object.keys(CARDS))('%s：可见区至多一句话、至多一个主按钮，详情默认折叠', async (name) => {
    await setLocale(lang)
    const e = entry(CARDS[name])
    useAiFixStore.setState({ entries: { 'script:plot.py': e } })
    const card = await render(e)
    expect(visibleSentenceCount(card, lang), name).toBeLessThanOrEqual(1)
    expect(visiblePrimaryButtons(card), name).toBeLessThanOrEqual(1)
    const details = card.querySelector('details')
    if (details) expect(details.open).toBe(false)
  })
})

describe('回答', () => {
  it('确认修复 / 不了：回 yes / no；详情里有读过哪些文件与「没读」', async () => {
    const answer = vi.fn(async () => {})
    useAiFixStore.setState({ answer } as never)
    const card = await render(entry(CARDS.approve))
    expect(card.querySelector('[data-ai-fix-confirm]')?.textContent).toBe('确认修复')
    expect(card.querySelector('[data-ai-fix-reads]')?.textContent).toContain('~/Desktop/data.csv')
    expect(card.querySelector('[data-ai-fix-reads]')?.textContent).toContain('没读')
    await click('[data-ai-fix-confirm]')
    expect(answer).toHaveBeenCalledWith('script:plot.py', 'yes')
    await click('[data-ai-fix-skip]')
    expect(answer).toHaveBeenLastCalledWith('script:plot.py', 'no')
  })

  it('选择题：点选项就答；「我自己填」带文字；没有主按钮', async () => {
    const answer = vi.fn(async () => {})
    useAiFixStore.setState({ answer } as never)
    const card = await render(entry(CARDS.choice))
    expect(visiblePrimaryButtons(card)).toBe(0)
    await click('[data-ai-fix-option="b"]')
    expect(answer).toHaveBeenCalledWith('script:plot.py', 'b')
    const input = card.querySelector('input') as HTMLInputElement
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '~/lab')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click('[data-ai-fix-other]')
    expect(answer).toHaveBeenLastCalledWith('script:plot.py', 'other', '~/lab')
  })

  it('选文件：桌面走原生选择器，选了才回答（取消不回答）；浏览器粘贴路径', async () => {
    const answer = vi.fn(async () => {})
    useAiFixStore.setState({ answer } as never)
    vi.mocked(isDesktop).mockReturnValue(true)
    vi.mocked(pickAnyFile).mockResolvedValueOnce(null).mockResolvedValueOnce('/Users/x/lab/d.csv')
    await render(entry(CARDS.pick))
    await click('[data-ai-fix-pick]')
    expect(answer).not.toHaveBeenCalled()
    await click('[data-ai-fix-pick]')
    expect(answer).toHaveBeenCalledWith('script:plot.py', '/Users/x/lab/d.csv')
    expect(pickDirectory).not.toHaveBeenCalled()
    vi.mocked(isDesktop).mockReturnValue(false)
    const card = await render(entry(CARDS.pick))
    expect(card.querySelector('[data-ai-fix-path]')).not.toBeNull()
  })

  it('发送卡：主按钮只打开发送对话框（预填说明），不自己发送；「不发送」回 no', async () => {
    const answer = vi.fn(async () => {})
    const openSend = vi.fn()
    useAiFixStore.setState({ answer, openSend } as never)
    const card = await render(entry(CARDS.send))
    expect(card.querySelector('[data-ai-fix-send]')?.textContent).toBe('查看并发送')
    await click('[data-ai-fix-send]')
    expect(openSend).toHaveBeenCalledWith('script:plot.py')
    expect(answer).not.toHaveBeenCalled()
    await click('[data-ai-fix-skip]')
    expect(answer).toHaveBeenCalledWith('script:plot.py', 'no')
  })

  it('运行中可取消；done 的主按钮「知道了」清掉条目', async () => {
    const cancel = vi.fn(async () => {})
    useAiFixStore.setState({ cancel } as never)
    await render(entry(CARDS.running))
    await click('[data-ai-fix-cancel]')
    expect(cancel).toHaveBeenCalledWith('script:plot.py')
    const card = await render(entry(CARDS.done))
    expect(visibleBlocks(card).map((b) => b.text)).toContain('装不上这个包。')
    await click('[data-ai-fix-dismiss]')
    expect(useAiFixStore.getState().entries['script:plot.py']).toBeUndefined()
  })
})

describe('不出现「执行器」概念', () => {
  it.each(['zh-CN', 'en-US'] as const)('%s：所有卡的文字里没有「执行器」/ executor', async (lang) => {
    await setLocale(lang)
    for (const name of Object.keys(CARDS)) {
      const card = await render(entry(CARDS[name]))
      expect(card.textContent ?? '', name).not.toMatch(/执行器|executor/i)
    }
  })
})


describe('进度与卡住（D3）', () => {
  it('运行中：显示后端给的那一句活动，包名带进去；没有活动时回到通用一句', async () => {
    const card = await render(entry(CARDS.activity))
    expect(card.querySelector('[data-title]')?.textContent).toBe('正在安装 colorcet')
    expect((await render(entry({ phase: 'running' }))).querySelector('[data-title]')?.textContent).toBe('AI 正在查找原因…')
    await setLocale('en-US')
    expect((await render(entry(CARDS.activity))).querySelector('[data-title]')?.textContent).toBe('Installing colorcet')
  })

  it('卡住：一句话原因（点名工具）+ 一个「停止」主按钮；点它取消，详情默认折叠', async () => {
    const cancel = vi.fn(async () => {})
    useAiFixStore.setState({ cancel } as never)
    const card = await render(entry(CARDS.stalledApproval))
    expect(card.querySelector('[data-title]')?.textContent).toBe('AI 在等它自己的审批（工具：change_python），Tavotto 没法替你批准')
    expect(card.querySelector('[data-ai-fix-stop]')?.textContent).toBe('停止')
    expect(card.querySelector('details')?.open).toBe(false)
    await click('[data-ai-fix-stop]')
    expect(cancel).toHaveBeenCalledWith('script:plot.py')
  })

  it('三种卡住原因各有一句话；自动停止后落成失败的一句话', async () => {
    for (const k of ['stalledApproval', 'stalledDelivery', 'stalledIdle'] as const) {
      const t = (await render(entry(CARDS[k]))).querySelector('[data-title]')?.textContent
      expect(t, k).toBeTruthy()
      expect(t).not.toContain('prep.aiFix')
    }
    expect((await render(entry(CARDS.autoStopped))).querySelector('[data-title]')?.textContent).toBe('AI 卡在它自己的审批上，已自动停止')
  })
})

it('确认卡的详情里列出将要安装的包（默认折叠）', async () => {
  const card = await render(entry(CARDS.approvePkgs))
  expect(card.querySelector('[data-ai-fix-packages]')?.textContent).toBe('将要安装：colorcet')
  expect(card.querySelector('details')?.open).toBe(false)
})
