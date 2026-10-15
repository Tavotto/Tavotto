/**
 * 助手输入框的草稿与「提交可靠性」界面（ADR 0132）。store 侧的状态机在 `store/aiSubmission.test.ts`；
 * 这里钉组件持有的那一半：
 *   * 右栏切页签会卸载助手面板——草稿落盘，重挂读回；刷新同理；
 *   * 草稿按「项目 × 面板文件」分键：切项目 / 切面板不串稿；
 *   * 「已发出未确认」：普通发送 / 重试一律禁用，只给「查询结果」；查到没收到才给「重新发送」（同一个 rid）；
 *   * 失败会话的「重试」遇到 `changed` 先二次确认。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { aiRun, ApiError, fetchAiRequest, fetchAiSession } from '@/lib/api'
import { DRAFT_MAX_BYTES } from '@/lib/aiDraft'
import { createDraftBackend } from '@/test/aiDraftBackend'
import { RETRY_MS, WRITE_DEBOUNCE_MS } from './useAiDraft'
import { setCurrentProjectId } from '@/lib/session'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, capsOf } from '@/components/settings/testCaps'
import { useAiStore, type AiSession } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { AssistantPanel } from './AiPanel'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiRun: vi.fn(),
  fetchAiRequest: vi.fn(),
  fetchAiSession: vi.fn(),
}))

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let host: HTMLDivElement

const mount = async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root!.render(
      <TooltipProvider>
        <AssistantPanel />
      </TooltipProvider>,
    )
  })
  await settle() // 草稿是异步从后端读回来的
}
/** 右栏切页签 = 卸载助手面板 */
const unmount = async () => {
  await act(async () => root!.unmount())
  root = null
  host.remove()
}

const q = <T extends Element = HTMLElement>(sel: string) => host.querySelector<T>(sel)
const input = () => q<HTMLTextAreaElement>('[data-ai-input]')!
const typeText = async (text: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(input(), text)
    input().dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const click = async (el: Element | null) => {
  expect(el, '要点的按钮不在').toBeTruthy()
  await act(async () => (el as HTMLElement).click())
}
const settle = async () => {
  for (let i = 0; i < 20; i++) await act(async () => Promise.resolve())
}

const panelOf = (fileId: string, id: string): PanelObject =>
  ({
    id, type: 'panel', x: 0, y: 0, w: 100, h: 75,
    fileId, fileKind: 'pdf', nativeW: 100, nativeH: 75,
    name: fileId, script: `${id}.py`, overrides: [],
  }) as unknown as PanelObject

const select = async (id: string) => {
  await act(async () => useSelectionStore.setState({ ids: [id] } as never))
}

const failedSession = (over: Partial<AiSession> = {}): AiSession => ({
  id: 's1', project: 'pa', agent: 'codex', agentLabel: 'Codex', prompt: '加粗线条',
  script: 'fig1.py', panelId: 'p1', fileId: 'Fig1.pdf', gid: null, scope: 'figure',
  target: '整张图', entries: [], status: 'failed', changed: false, diff: '', startedAt: 1,
  finishedAt: 2, finalized: true, ...over,
})

let be: ReturnType<typeof createDraftBackend>
const SENT = { scope: 'figure', gid: null, label: null, target: '整张图' }

beforeEach(async () => {
  be = createDraftBackend()
  globalThis.fetch = (async (url: unknown, init?: RequestInit) =>
    be.handle(String(url), init) ?? new Response('{}')) as typeof fetch
  localStorage.clear()
  setCurrentProjectId('pa')
  vi.mocked(aiRun).mockReset()
  vi.mocked(fetchAiRequest).mockReset()
  vi.mocked(fetchAiSession).mockReset()
  vi.mocked(fetchAiSession).mockRejectedValue(new Error('no detail'))
  useUiStore.setState({ status: null, confirm: null, elementPanelId: null, selectedGids: [] } as never)
  useAiStore.setState({
    sessions: [], submissions: {}, scope: 'figure',
    caps: capsOf([agentCaps()]), agent: 'codex', models: {}, efforts: {},
  })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ai_draft')
  useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panelOf('Fig1.pdf', 'p1'), panelOf('Fig2.pdf', 'p2'), { ...panelOf('Fig3.pdf', 'p3'), script: 'p1.py' } as PanelObject] } }) as never)
  useSelectionStore.setState({ ids: ['p1'] } as never)
})
afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  root = null
  document.body.innerHTML = ''
})

describe('草稿存后端：落盘与恢复', () => {
  it('打字 → 切页签（卸载）→ 切回（重挂）：草稿还在，并说一句「已恢复」', async () => {
    await mount()
    await typeText('把图例移到左上角')
    await unmount() // 卸载时 keepalive 冲刷
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBe('把图例移到左上角')
    await mount()
    expect(input().value).toBe('把图例移到左上角')
    expect(q('[data-ai-draft-note="restored"]')).toBeTruthy()
    await typeText('把图例移到左上角，再大一点')
    expect(q('[data-ai-draft-note]')).toBeNull() // 一动手就收
  })

  it('换端口（换 origin）也不丢：浏览器存储清空后重挂，草稿仍从后端读回', async () => {
    await mount()
    await typeText('写了一半的指令')
    await unmount()
    await settle()
    localStorage.clear()
    sessionStorage.clear() // 端口一变，这些全是空的新 origin
    await mount()
    expect(input().value).toBe('写了一半的指令')
    // 整个过程没往浏览器存储里写过任何东西
    expect(localStorage.length).toBe(0)
  })

  it('防抖 600 ms：停手前不发请求；到点只发一次 PUT；pagehide 立刻 keepalive 冲刷', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      const puts = () => be.calls.filter((c) => c.method === 'PUT' && c.path === '/api/ai/drafts')
      await typeText('第一')
      await typeText('第一句')
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS - 50))
      expect(puts()).toHaveLength(0) // 对照：防抖期内没写
      await act(async () => void vi.advanceTimersByTime(100))
      await settle()
      expect(puts()).toHaveLength(1) // 两次输入合并成一次
      expect(be.draft('pa', 'Fig1.pdf')).toBe('第一句')
      expect(puts()[0].keepalive).toBe(false)
      await typeText('第二句')
      await act(async () => window.dispatchEvent(new Event('pagehide')))
      await settle()
      expect(puts()).toHaveLength(2)
      expect(puts()[1].keepalive).toBe(true) // 页面正在关：keepalive
      expect(be.draft('pa', 'Fig1.pdf')).toBe('第二句')
    } finally {
      vi.useRealTimers()
    }
  })

  it('后端暂时不可用：文本留在内存里，3 秒后重试，恢复后写成；期间不丢', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      be.setDown(true)
      await typeText('断网时写的话')
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 10))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined() // 没写成
      expect(input().value).toBe('断网时写的话') // 内存里还在
      await act(async () => void vi.advanceTimersByTime(RETRY_MS + 10))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined() // 重试仍失败，不抛、继续留着
      be.setDown(false)
      await act(async () => void vi.advanceTimersByTime(RETRY_MS + 10))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('断网时写的话')
    } finally {
      vi.useRealTimers()
    }
  })

  it('切面板：旧面板的稿存下、新面板读它自己的；切回来找得到，不串稿', async () => {
    await mount()
    await typeText('给 Fig1 的话')
    await select('p2')
    await settle()
    expect(input().value).toBe('') // Fig2 没有稿
    await typeText('给 Fig2 的话')
    await select('p1')
    await settle()
    expect(input().value).toBe('给 Fig1 的话')
    await select('p2')
    await settle()
    expect(input().value).toBe('给 Fig2 的话')
  })

  it('切项目：B 看不到 A 的稿（对照：切回 A 找得回）', async () => {
    await mount()
    await typeText('A 项目的话')
    setCurrentProjectId('pb')
    await act(async () => useAiStore.getState().clear()) // 面板按代际重挂
    await settle()
    expect(input().value).toBe('')
    expect(be.draft('pb', 'Fig1.pdf')).toBeUndefined()
    setCurrentProjectId('pa')
    await act(async () => useAiStore.getState().clear())
    await settle()
    expect(input().value).toBe('A 项目的话')
  })

  it('刷新后挂载那一刻项目认领还没到（null）：不读不写，认领到了才读', async () => {
    be.put('pa', 'Fig1.pdf', '上次没发出的话')
    setCurrentProjectId(null)
    await mount()
    expect(input().value).toBe('')
    expect(be.calls).toEqual([]) // 没认领 = 一个请求都不发（不会落进后端的默认项目）
    setCurrentProjectId('pa')
    await settle()
    expect(input().value).toBe('上次没发出的话')
  })

  it('多标签页：窗口重新获得焦点时以后端为准再读一次；本地有没写出去的改动时不覆盖', async () => {
    await mount()
    await typeText('这个标签页的话')
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    await settle()
    be.put('pa', 'Fig1.pdf', '另一个标签页后来写的话') // 另一个标签页改了
    await act(async () => window.dispatchEvent(new Event('focus')))
    await settle()
    expect(input().value).toBe('另一个标签页后来写的话') // 对照：没有本地改动时后端为准
    await typeText('我又接着写') // 本地有 dirty
    be.put('pa', 'Fig1.pdf', '另一个标签页又写了')
    await act(async () => window.dispatchEvent(new Event('focus')))
    await settle()
    expect(input().value).toBe('我又接着写') // 最后写入为准：不被覆盖
  })

  it('超过 20 KB：给「过长」提示；后端 413 后不存、不截断', async () => {
    await mount()
    await typeText('图'.repeat(Math.ceil(DRAFT_MAX_BYTES / 3) + 1))
    expect(q('[data-ai-draft-note="too-large"]')).toBeTruthy()
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined()
  })
})

describe('已发出未确认：不能盲目重发', () => {
  beforeEach(() => {
    vi.mocked(aiRun).mockRejectedValue(new TypeError('Failed to fetch'))
  })

  const sendIt = async (text = '把图例移到左上角') => {
    await typeText(text)
    await click(q('[data-ai-send="send"]'))
    await settle()
  }

  it('超时/断网后：出「查询结果」，没有普通重试；发送钮禁用、输入框只读、草稿保留', async () => {
    await mount()
    await sendIt()
    expect(q('[data-ai-submission="unconfirmed"]')).toBeTruthy()
    expect(q('[data-ai-check]')).toBeTruthy()
    expect(q('[data-ai-retry]')).toBeNull()
    expect(q('[data-ai-error="send"]')).toBeNull() // 不是「发送失败」
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
    expect(input().readOnly).toBe(true)
    expect(input().value).toBe('把图例移到左上角')
    expect(aiRun).toHaveBeenCalledTimes(1)
    // ⌘↵ 也发不出去
    await act(async () => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true }))
    })
    expect(aiRun).toHaveBeenCalledTimes(1)
  })

  it('刷新后（草稿里的 rid）恢复成未确认，不是一条可以普通重发的草稿', async () => {
    be.pendings.set('pa\u0000Fig1.pdf', [{ rid: 'rid-restore-0001', text: '加粗线条', sent: SENT }])
    await mount()
    expect(q('[data-ai-submission="unconfirmed"]')).toBeTruthy()
    expect(input().value).toBe('加粗线条')
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
  })

  it('查询：后端没收到 → 出「重新发送」，用【同一个】rid；成功后清输入框与草稿', async () => {
    await mount()
    await sendIt()
    const rid = (vi.mocked(aiRun).mock.calls[0][0] as { client_request_id: string }).client_request_id
    vi.mocked(fetchAiRequest).mockRejectedValue(new ApiError('nf', 404, { code: 'ai_request_not_found' }))
    await click(q('[data-ai-check]'))
    await settle()
    expect(q('[data-ai-submission="not_received"]')).toBeTruthy()
    expect(q('[data-ai-check]')).toBeNull()
    vi.mocked(aiRun).mockResolvedValue({ session: 's-9', script: 'fig1.py' })
    await click(q('[data-ai-resend-same]'))
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(2)
    expect((vi.mocked(aiRun).mock.calls[1][0] as { client_request_id: string }).client_request_id).toBe(rid)
    expect(input().value).toBe('')
    expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined()
    expect(be.pending('pa', 'Fig1.pdf')).toEqual([])
    expect(useAiStore.getState().sessions.map((s) => s.id)).toEqual(['s-9'])
  })

  it('查询：后端已登记 → 进会话、不再发；联系不上 → 说「稍后再查」且仍禁用', async () => {
    await mount()
    await sendIt()
    vi.mocked(fetchAiRequest).mockRejectedValue(new TypeError('down'))
    await click(q('[data-ai-check]'))
    await settle()
    expect(q('[data-ai-submission="unconfirmed"]')?.textContent).toContain('联系不上')
    expect(q('[data-ai-resend-same]')).toBeNull()

    vi.mocked(fetchAiRequest).mockResolvedValue({ session: 's-5', status: 'running', changed: false, script: 'fig1.py' })
    await click(q('[data-ai-check]'))
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(1)
    expect(useAiStore.getState().sessions.map((s) => s.id)).toEqual(['s-5'])
    expect(q('[data-ai-submission]')).toBeNull()
    expect(input().value).toBe('')
  })

  it('查询：后端说「已执行过、记录已删」→ 说一句、清输入框、不出现重新发送，也不再发', async () => {
    await mount()
    await sendIt()
    vi.mocked(fetchAiRequest).mockResolvedValue({ session: 's-5', status: 'history_deleted', changed: false, script: null as never })
    await click(q('[data-ai-check]'))
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(1)
    expect(q('[data-ai-submission]')).toBeNull()
    expect(q('[data-ai-resend-same]')).toBeNull()
    expect(input().value).toBe('')
    expect(useUiStore.getState().status).not.toBeNull()
  })

  it('同一脚本的另一个面板：A 面板未确认时切到 B，提示还在、发送仍锁住（按脚本判）', async () => {
    await mount()
    await sendIt()
    expect(aiRun).toHaveBeenCalledTimes(1)
    await select('p3') // Fig3 与 Fig1 共用脚本 p1.py
    await settle()
    expect(q('[data-ai-submission="unconfirmed"]')).toBeTruthy()
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
    await typeText('在 B 面板上再说一遍')
    await act(async () => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true }))
    })
    expect(aiRun).toHaveBeenCalledTimes(1)
    // 对照：别的脚本的面板（p2）不受影响
    await select('p2')
    await settle()
    expect(q('[data-ai-submission]')).toBeNull()
  })

  it('刷新后在兄弟面板上挂载：A 面板的待确认账本也还原，发送仍锁住', async () => {
    be.pendings.set('pa\u0000Fig1.pdf', [{ rid: 'rid-sibling-0001', text: 'A 面板发出的话', sent: SENT }])
    useSelectionStore.setState({ ids: ['p3'] } as never)
    await mount()
    expect(q('[data-ai-submission="unconfirmed"]')).toBeTruthy()
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
  })

  it('「改写后再发」：放弃这条等待、输入框可改，下一次发送是新 rid', async () => {
    await mount()
    await sendIt('旧指令')
    const rid1 = (vi.mocked(aiRun).mock.calls[0][0] as { client_request_id: string }).client_request_id
    vi.mocked(fetchAiRequest).mockRejectedValue(new ApiError('nf', 404, { code: 'ai_request_not_found' }))
    await click(q('[data-ai-check]'))
    await settle()
    await click(q('[data-ai-edit-resend]'))
    expect(input().readOnly).toBe(false)
    vi.mocked(aiRun).mockResolvedValue({ session: 's-3', script: 'fig1.py' })
    await typeText('新指令')
    await click(q('[data-ai-send="send"]'))
    await settle()
    const rid2 = (vi.mocked(aiRun).mock.calls[1][0] as { client_request_id: string }).client_request_id
    expect(rid2).not.toBe(rid1)
  })

  it('明确失败（409 busy）：是「没有发出去」+ 普通重试，不是未确认', async () => {
    vi.mocked(aiRun).mockRejectedValue(new ApiError('busy', 409, { code: 'ai_script_busy', params: { script: 'fig1.py' } }))
    await mount()
    await sendIt()
    expect(q('[data-ai-error="send"]')).toBeTruthy()
    expect(q('[data-ai-submission]')).toBeNull()
    expect(input().readOnly).toBe(false)
    expect(input().value).toBe('把图例移到左上角') // 文本留着
  })
})

describe('失败会话的「重试」遇到 changed', () => {
  const retryBtn = () => q('[data-ai-error="session"] [data-ai-retry]')

  it('changed=true：先弹确认；取消 → 不发；确认 → 才发（新任务）', async () => {
    useAiStore.setState({ sessions: [failedSession({ changed: true })] })
    vi.mocked(aiRun).mockResolvedValue({ session: 's-2', script: 'fig1.py' })
    await mount()
    await click(retryBtn())
    const req = useUiStore.getState().confirm
    expect(req, '应该弹确认').toBeTruthy()
    expect(aiRun).not.toHaveBeenCalled()
    await act(async () => {
      useUiStore.getState().setConfirm(null)
      req!.resolve(false)
    })
    await settle()
    expect(aiRun).not.toHaveBeenCalled()

    await click(retryBtn())
    const req2 = useUiStore.getState().confirm
    await act(async () => {
      useUiStore.getState().setConfirm(null)
      req2!.resolve(true)
    })
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(1)
  })

  it('对照 changed=false：照旧直接重试，不打扰', async () => {
    useAiStore.setState({ sessions: [failedSession({ changed: false })] })
    vi.mocked(aiRun).mockResolvedValue({ session: 's-2', script: 'fig1.py' })
    await mount()
    await click(retryBtn())
    await settle()
    expect(useUiStore.getState().confirm).toBeNull()
    expect(aiRun).toHaveBeenCalledTimes(1)
  })
})
