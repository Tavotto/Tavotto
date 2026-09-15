/**
 * 改图助手对话区的质感（2026-09-15）：这些行为在 jsdom 里量得到的部分。
 *
 * - **状态行是唯一的「进行中」信号**：running 时一道亮带扫过文字（`text-shimmer`），
 *   完成即停；不再有第三方 loader / 骨架。
 * - **发送 ↔ 中止同一颗按钮**：正在跑时它是「中止」（可点、不看输入框空不空），
 *   点下去调 cancel；跑完回到「发送」。会话行上不再复制一颗中止钮。
 * - **贴底跟随只在看着底部时才跟**：往上翻着看旧回答时，新 delta 不把视口拽回底部，
 *   而是给一颗「回到底部」；到底了它自己消失。
 * - **过程步骤的展开是 Reveal**（跟着内容长高），按钮带 aria-expanded。
 * - **流式正文按 markdown 渲染、逐词 span**；终稿到达 span 消失。
 *
 * 动画本身（淡入、弹簧）jsdom 播不出来——只钉类名与 token（motion.test）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { aiCancel } from '@/lib/api'
import { t } from '@/i18n'
import { STREAM_WORD_CLASS } from '@/lib/streamMarkdown'
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

const sendButton = () => host.querySelector<HTMLButtonElement>('button[data-ai-send]')!
const statusLine = () => host.querySelector<HTMLElement>('[data-ai-status]')!
const scroller = () => host.querySelector<HTMLDivElement>('.overflow-y-auto')!
const scrollPill = () =>
  Array.from(host.querySelectorAll('button')).find(
    (b) => b.getAttribute('aria-label') === ai('panel.scrollToBottom'),
  )

/** jsdom 没有布局：把滚动几何装上去，scrollTop 才有「在不在底部」可言 */
function fakeGeometry(el: HTMLElement, { scrollHeight, clientHeight }: { scrollHeight: number; clientHeight: number }) {
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => scrollHeight })
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => clientHeight })
  let top = 0
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => top,
    set: (v: number) => {
      top = Math.max(0, Math.min(v, scrollHeight - clientHeight))
    },
  })
}

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
  vi.mocked(aiCancel).mockClear()
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
  await act(async () => root?.unmount())
})

describe('进行中的信号', () => {
  it('running：状态行亮带扫过；完成后停、且没有别的 loader', async () => {
    await mount([session()])
    expect(statusLine().dataset.aiStatus).toBe('running')
    expect(statusLine().classList.contains('text-shimmer')).toBe(true)
    expect(statusLine().textContent).toBe(ai('session.running'))

    await act(async () => {
      useAiStore.getState().finish({ session: 's1', status: 'done', changed: false, diff: '' })
    })
    expect(statusLine().dataset.aiStatus).toBe('done')
    expect(statusLine().classList.contains('text-shimmer')).toBe(false)
    expect(statusLine().textContent).toBe(ai('session.doneNoChange'))
    // running → done 那一下原位换（宪法第二十三节）：旧句留在 data-ghost 里退场，DOM 文本已是新句
    expect(statusLine().closest<HTMLElement>('.swap-text')!.dataset.ghost).toBe(ai('session.running'))
    // 整个面板里没有 aria-busy 之外的忙碌指示（旧版在这里摆过 loader + 骨架）
    expect(host.querySelectorAll('.animate-spin')).toHaveLength(0)
  })

  it('每一轮对话落位带 settle-in；会话行上不再有第二颗中止钮', async () => {
    await mount([session()])
    const block = host.querySelector<HTMLElement>('[data-ai-session]')!
    expect(block.classList.contains('animate-settle-in')).toBe(true)
    const stops = Array.from(host.querySelectorAll('button')).filter(
      (b) => b.getAttribute('aria-label') === ai('panel.abort'),
    )
    expect(stops).toHaveLength(1)
    expect(stops[0]).toBe(sendButton())
  })
})

describe('发送 ↔ 中止同一颗按钮', () => {
  it('running 时是「中止」、输入框空着也能点、点了调 cancel、跑完回到「发送」', async () => {
    await mount([session()])
    const btn = sendButton()
    expect(btn.dataset.aiSend).toBe('stop')
    expect(btn.getAttribute('aria-label')).toBe(ai('panel.abort'))
    expect(btn.disabled).toBe(false)

    await act(async () => btn.click())
    expect(aiCancel).toHaveBeenCalledWith('s1')
    // cancel 之后会话不再 running → 同一颗按钮回到发送（输入框空着，所以禁用）
    expect(sendButton().dataset.aiSend).toBe('send')
    expect(sendButton().getAttribute('aria-label')).toBe(ai('panel.sendAria'))
    expect(sendButton().disabled).toBe(true)
  })

  it('没在跑：是「发送」，输入框空着时禁用', async () => {
    await mount([])
    expect(sendButton().dataset.aiSend).toBe('send')
    expect(sendButton().disabled).toBe(true)
  })
})

describe('贴底跟随', () => {
  it('看着底部时新 delta 跟着滚；翻上去了就不拽，给一颗「回到底部」，点了回去', async () => {
    await mount([session({ entries: [{ kind: 'message', text: 'first', streaming: true }] })])
    const el = scroller()
    fakeGeometry(el, { scrollHeight: 1000, clientHeight: 300 })

    // 贴着底：一条新 delta 之后 scrollTop 到底
    el.scrollTop = 700
    await act(async () => el.dispatchEvent(new Event('scroll')))
    await act(async () => useAiStore.getState().appendDelta('s1', 'delta', ' more'))
    expect(el.scrollTop).toBe(700)
    expect(scrollPill()).toBeUndefined()

    // 翻上去看旧回答：新 delta 不动视口，出现回到底部
    el.scrollTop = 100
    await act(async () => el.dispatchEvent(new Event('scroll')))
    await act(async () => useAiStore.getState().appendDelta('s1', 'delta', ' and more'))
    expect(el.scrollTop).toBe(100)
    expect(scrollPill(), '翻上去之后该有「回到底部」').toBeTruthy()

    await act(async () => scrollPill()!.click())
    expect(el.scrollTop).toBe(700)
    expect(scrollPill()).toBeUndefined()
  })

  it('跑完了就不摆「回到底部」：那颗钮只为「新内容还在来」服务', async () => {
    await mount([session({ status: 'done' })])
    const el = scroller()
    fakeGeometry(el, { scrollHeight: 1000, clientHeight: 300 })
    el.scrollTop = 0
    await act(async () => el.dispatchEvent(new Event('scroll')))
    expect(scrollPill()).toBeUndefined()
  })
})

describe('过程与正文', () => {
  it('过程步骤：按钮带 aria-expanded，展开是 Reveal', async () => {
    await mount([session({ entries: [{ kind: 'thinking', text: '先看一眼脚本' }] })])
    const toggle = Array.from(host.querySelectorAll('button')).find((b) =>
      b.textContent?.includes(ai('panel.processSteps', { count: 1 })),
    )!
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(host.querySelector('[data-reveal]')).toBeNull()
    await act(async () => toggle.click())
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    const reveal = host.querySelector<HTMLElement>('[data-reveal]')!
    expect(reveal.dataset.state).toBe('open')
    expect(reveal.textContent).toContain('先看一眼脚本')
  })

  it('流式正文按 markdown 渲染、逐词 span；终稿到达后 span 消失', async () => {
    await mount([session({ entries: [{ kind: 'message', text: 'move **legend** now', streaming: true }] })])
    expect(host.querySelector('strong')?.textContent).toBe('legend')
    expect(host.querySelectorAll(`.${STREAM_WORD_CLASS}`)).toHaveLength(3)
    await act(async () => useAiStore.getState().appendDelta('s1', 'message', 'move **legend** now'))
    expect(host.querySelector('strong')?.textContent).toBe('legend')
    expect(host.querySelectorAll(`.${STREAM_WORD_CLASS}`)).toHaveLength(0)
  })
})
