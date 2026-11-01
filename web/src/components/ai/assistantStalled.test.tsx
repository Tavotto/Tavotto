/**
 * 「AI 没有动静」（`ai.stalled`）：后端只提示、不杀进程；界面把状态行换成「已经 N 秒没有动静」，并给「取消 / 重试」。
 * 又有输出（delta）或收尾，提示立刻撤掉。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { aiCancel } from '@/lib/api'
import { t } from '@/i18n'
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
  aiCancel: vi.fn(async () => ({ ok: true })),
}))

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const ai = (k: string, v?: Record<string, unknown>) => t(k, { ns: 'ai', ...(v ?? {}) })

const panel = (): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 0, y: 0, w: 100, h: 75,
    fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 100, nativeH: 75,
    name: 'Fig1', script: '/tmp/figs/fig1.py', overrides: [],
  }) as unknown as PanelObject

const session = (over: Partial<AiSession> = {}): AiSession => ({
  id: 's1',
  project: null,
  agent: 'codex',
  agentLabel: 'Codex',
  prompt: '把图例移到左上角',
  script: '/tmp/figs/fig1.py',
  panelId: 'p1',
  fileId: 'Fig1.pdf',
  gid: null,
  scope: 'figure',
  target: '整张图',
  entries: [],
  status: 'running',
  changed: false,
  diff: '',
  startedAt: 1_756_000_000_000,
  ...over,
})

let root: Root
let host: HTMLDivElement

async function mount(sessions: AiSession[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_assistant_motion')
  useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panel()] } }) as never)
  useSelectionStore.setState({ ids: ['p1'] } as never)
  useUiStore.setState({ elementPanelId: null, selectedGids: [] } as never)
  useAiStore.setState({ sessions })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <AssistantPanel />
      </TooltipProvider>,
    )
  })
}


const status = () => host.querySelector<HTMLElement>('[data-ai-status]')!
const q = (sel: string) => host.querySelector<HTMLButtonElement>(sel)

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
  vi.mocked(aiCancel).mockClear()
  useAiStore.setState({ sessions: [], scope: 'figure', caps: capsOf([agentCaps()]), agent: 'codex', models: {}, efforts: {} })
})
afterEach(async () => {
  await act(async () => root?.unmount())
})

describe('ai.stalled', () => {
  it('没动静时：状态行说明秒数，出现取消 / 重试；点取消调 cancel', async () => {
    await mount([session()])
    expect(q('[data-ai-stalled-actions]')).toBeNull()
    await act(async () => useAiStore.getState().setStalled('s1', 120))
    expect(status().textContent).toBe(ai('session.stalled', { seconds: 120 }))
    expect(status().textContent).toContain('120')
    expect(q('[data-ai-stalled-retry]')).not.toBeNull()
    await act(async () => q('[data-ai-stalled-cancel]')!.click())
    expect(aiCancel).toHaveBeenCalledWith('s1', null)
  })

  it('又有输出：提示撤掉；收尾后即使没复位也不再显示', async () => {
    await mount([session()])
    await act(async () => useAiStore.getState().setStalled('s1', 130))
    await act(async () => useAiStore.getState().appendDelta('s1', 'message', 'hi'))
    expect(q('[data-ai-stalled-actions]')).toBeNull()
    expect(status().textContent).toBe(ai('session.running'))
    await act(async () => useAiStore.getState().setStalled('s1', 140))
    await act(async () => useAiStore.getState().finish({ session: 's1', status: 'done', changed: false, diff: '' }))
    expect(q('[data-ai-stalled-actions]')).toBeNull()
  })

  it('已收尾的会话不吃迟到的 stalled', async () => {
    await mount([session({ status: 'done', finalized: true })])
    await act(async () => useAiStore.getState().setStalled('s1', 150))
    expect(useAiStore.getState().sessions[0].stalledS).toBeUndefined()
  })
})
