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
import { DRAFT_MAX_BYTES, DRAFT_PREFIX, loadDraft, markSubmitted, saveDraft } from '@/lib/aiDraft'
import { setCurrentProjectId } from '@/lib/session'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, capsOf } from '@/components/settings/testCaps'
import { useAiStore, type AiSession } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { AssistantPanel } from './AiPanel'
import { __resetDraftSweepForTests } from './useAiDraft'

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
  for (let i = 0; i < 5; i++) await act(async () => Promise.resolve())
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

beforeEach(async () => {
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
  useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panelOf('Fig1.pdf', 'p1'), panelOf('Fig2.pdf', 'p2')] } }) as never)
  useSelectionStore.setState({ ids: ['p1'] } as never)
})
afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  root = null
  document.body.innerHTML = ''
})

describe('草稿落盘与恢复', () => {
  it('打字 → 切页签（卸载）→ 切回（重挂）：草稿还在，并说一句「已恢复」', async () => {
    await mount()
    await typeText('把图例移到左上角')
    await unmount() // 卸载时 flush
    expect(loadDraft('pa', 'Fig1.pdf')?.text).toBe('把图例移到左上角')
    await mount()
    expect(input().value).toBe('把图例移到左上角')
    expect(q('[data-ai-draft-note="restored"]')).toBeTruthy()
    await typeText('把图例移到左上角，再大一点')
    expect(q('[data-ai-draft-note]')).toBeNull() // 一动手就收
  })

  it('防抖：停手前不写盘；pagehide（刷新 / 关窗）立刻写盘', async () => {
    await mount()
    await typeText('还没停手')
    expect(loadDraft('pa', 'Fig1.pdf')).toBeNull() // 对照：防抖期内没写
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    expect(loadDraft('pa', 'Fig1.pdf')?.text).toBe('还没停手')
  })

  it('切面板：旧面板的稿存下、新面板读它自己的；切回来找得到，不串稿', async () => {
    await mount()
    await typeText('给 Fig1 的话')
    await select('p2')
    expect(input().value).toBe('') // Fig2 没有稿
    await typeText('给 Fig2 的话')
    await select('p1')
    expect(input().value).toBe('给 Fig1 的话')
    await select('p2')
    expect(input().value).toBe('给 Fig2 的话')
  })

  it('切项目：B 看不到 A 的稿（对照：切回 A 找得回）', async () => {
    await mount()
    await typeText('A 项目的话')
    setCurrentProjectId('pb')
    await act(async () => useAiStore.getState().clear()) // 面板按代际重挂
    expect(input().value).toBe('')
    expect(loadDraft('pb', 'Fig1.pdf')).toBeNull()
    setCurrentProjectId('pa')
    await act(async () => useAiStore.getState().clear())
    expect(input().value).toBe('A 项目的话')
  })

  it('刷新后文档还没恢复完（面板列表为空）时挂载：草稿不会被当成「目标已删」清掉，面板到了就恢复', async () => {
    saveDraft('pa', 'Fig1.pdf', '上次没发出的话')
    __resetDraftSweepForTests() // 让「首次挂载清扫」在这条用例里真的发生
    useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [] } }) as never)
    useSelectionStore.setState({ ids: [] } as never)
    await mount()
    expect(loadDraft('pa', 'Fig1.pdf')?.text).toBe('上次没发出的话') // 对照：空面板列表那一刻没把它清掉
    useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panelOf('Fig1.pdf', 'p1')] } }) as never)
    await select('p1')
    expect(input().value).toBe('上次没发出的话')
  })

  it('超过 20 KB：不落盘、不截断，并说「过长」', async () => {
    await mount()
    await typeText('图'.repeat(Math.ceil(DRAFT_MAX_BYTES / 3) + 1))
    expect(q('[data-ai-draft-note="too-large"]')).toBeTruthy()
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    expect(Object.keys(localStorage).filter((k) => k.startsWith(DRAFT_PREFIX))).toEqual([])
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
    markSubmitted('pa', 'Fig1.pdf', '加粗线条', 'rid-restore-0001', { scope: 'figure', gid: null, label: null, target: '整张图' })
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
    expect(loadDraft('pa', 'Fig1.pdf')).toBeNull()
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
