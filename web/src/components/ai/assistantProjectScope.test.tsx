/**
 * 改图助手的**组件侧**跟着项目换代（#589）。store 侧（会话、在飞请求、SSE）在
 * `store/projectSwitchAi.test.ts`；这里钉三处组件自己持有、store 清不到的东西：
 *
 *   * 助手面板以 `generation` 为 key 重挂：A 的草稿 / 错误提示不留在 B；
 *   * 「撤销这次修改」的后果（标脏渲染、状态提示）在 `revert()` 回 `false` 时一个都不写；
 *   * 任务历史视图钉在打开它那一刻的项目上：列表、回滚都发往那个项目，回来时换了项目就不提示。
 *
 * 每条都有「不换代 / 不换项目」的对照；扣住的请求都在用例结束前放行并 await。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { aiRevert, fetchAiHistory, type AiHistoryEntry } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, capsOf } from '@/components/settings/testCaps'
import { useAiStore, type AiSession } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { AssistantPanel, TaskHistory } from './AiPanel'
import { revertSession } from './revertSession'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiRevert: vi.fn(),
  fetchAiHistory: vi.fn(),
}))

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const ai = (k: string) => t(k, { ns: 'ai' })

let root: Root | null = null
let host: HTMLDivElement

const render = async (node: React.ReactNode) => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root!.render(<TooltipProvider>{node}</TooltipProvider>)
  })
}

/** 扣住下一次 `aiRevert`，由用例决定何时回答 */
const holdRevert = () => {
  let release!: () => void
  vi.mocked(aiRevert).mockImplementationOnce(
    () => new Promise((r) => (release = () => r({ ok: true, script: 'fig1.py' }))),
  )
  return () => release()
}

const session = (over: Partial<AiSession> = {}): AiSession => ({
  id: 's1',
  project: 'pa',
  agent: 'codex',
  agentLabel: 'Codex',
  prompt: 'p',
  script: 'fig1.py',
  panelId: 'p1',
  fileId: 'Fig1.pdf',
  gid: null,
  scope: 'figure',
  target: '整张图',
  entries: [],
  status: 'done',
  changed: true,
  diff: '',
  startedAt: 1,
  ...over,
})

const markStale = vi.fn()

beforeEach(() => {
  setCurrentProjectId('pa')
  markStale.mockClear()
  vi.mocked(aiRevert).mockReset()
  vi.mocked(aiRevert).mockResolvedValue({ ok: true, script: 'fig1.py' })
  vi.mocked(fetchAiHistory).mockReset()
  useRenderStore.setState({ markStale } as never)
  useUiStore.setState({ status: null, elementPanelId: null, selectedGids: [] } as never)
  useAiStore.setState({
    sessions: [],
    scope: 'figure',
    caps: capsOf([agentCaps()]),
    agent: 'codex',
    models: {},
    efforts: {},
  })
})

afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  root = null
  document.body.innerHTML = ''
})

describe('助手面板随项目代际重挂', () => {
  const typeDraft = async (text: string) => {
    const box = host.querySelector('textarea')!
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(box, text)
      box.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  const draft = () => host.querySelector('textarea')!.value

  beforeEach(async () => {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ai_scope')
    const panel = {
      id: 'p1', type: 'panel', x: 0, y: 0, w: 100, h: 75,
      fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 100, nativeH: 75,
      name: 'Fig1', script: 'fig1.py', overrides: [],
    } as unknown as PanelObject
    useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panel] } }) as never)
    useSelectionStore.setState({ ids: ['p1'] } as never)
    await render(<AssistantPanel />)
  })

  it('对照：会话变化不动草稿', async () => {
    await typeDraft('A 项目里写了一半')
    await act(async () => useAiStore.setState({ sessions: [] }))
    expect(draft()).toBe('A 项目里写了一半')
  })

  it('换代（切项目的 clear）：A 的草稿不留在 B', async () => {
    await typeDraft('A 项目里写了一半')
    await act(async () => useAiStore.getState().clear())
    expect(draft()).toBe('')
  })
})

describe('撤销的后果只写进发起它的那个项目', () => {
  it('对照：没换项目，撤销完标脏并提示', async () => {
    useAiStore.setState({ sessions: [session()] })
    const release = holdRevert()
    const done = revertSession(session())
    release()
    await done
    expect(markStale).toHaveBeenCalledWith(['Fig1.pdf'])
    expect(useUiStore.getState().status).not.toBeNull()
  })

  it('撤销在飞时换代：回来不标脏、不提示', async () => {
    useAiStore.setState({ sessions: [session()] })
    const release = holdRevert()
    const done = revertSession(session())
    useAiStore.getState().clear()
    release()
    await done
    expect(markStale).not.toHaveBeenCalled()
    expect(useUiStore.getState().status).toBeNull()
  })
})

describe('任务历史钉在打开它的项目上', () => {
  const entry = {
    id: 'h1',
    prompt: 'p',
    target: '整张图',
    provider: 'codex',
    model: null,
    status: 'done',
    changed: true,
    revert_available: true,
    pinned: false,
    started_ms: 1,
    script: 'fig1.py',
  } as unknown as AiHistoryEntry

  const mountHistory = async () => {
    vi.mocked(fetchAiHistory).mockResolvedValue({ sessions: [entry], total: 1 })
    await render(<TaskHistory onClose={() => {}} />)
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 0))
    })
  }
  const revertButton = () =>
    [...host.querySelectorAll<HTMLButtonElement>('button')].find(
      (b) => b.getAttribute('aria-label') === ai('history.revert'),
    )!

  it('列表按打开时的项目查', async () => {
    await mountHistory()
    expect(vi.mocked(fetchAiHistory).mock.calls[0][1]).toBe('pa')
  })

  it('对照：没换项目，回滚发往本项目并提示', async () => {
    await mountHistory()
    await act(async () => revertButton().click())
    expect(vi.mocked(aiRevert)).toHaveBeenCalledWith('h1', 'pa')
    expect(useUiStore.getState().status).not.toBeNull()
  })

  it('认领换成 B 之后点回滚：请求仍发往 A；回来时不在 B 上提示', async () => {
    await mountHistory()
    setCurrentProjectId('pb')
    await act(async () => revertButton().click())
    expect(vi.mocked(aiRevert)).toHaveBeenCalledWith('h1', 'pa')
    expect(useUiStore.getState().status).toBeNull()
  })
})
