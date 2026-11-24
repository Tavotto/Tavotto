/**
 * 改图助手发送时「先出修改方案卡还是照旧改脚本」的接线（ADR 0206 PR 4）：判据本身在 `lib/assistantPlanRoute.test.ts`，
 * 方案状态机在 `store/changePlanAssistant.test.ts`；这里钉组件这一半——发送真的走了哪条路、输入框与后端草稿是否清掉、
 * 用户的原话是否出现在对话里、同步被拒时是否原样落回改脚本路径（一个字不丢）、方案在生成时发送被锁。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import '@/hooks/useServerEvents' // 注册会话收尾提示（setAiSettledNotifier）
import { aiRun, ApiError, createChangePlan, EngineError, fetchAiRequest, fetchAiSession, getChangePlan } from '@/lib/api'
import { __resetWriteStampForTests } from '@/lib/aiDraft'
import { createDraftBackend } from '@/test/aiDraftBackend'
import { setCurrentProjectId } from '@/lib/session'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, capsOf } from '@/components/settings/testCaps'
import { useAiStore } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useChangePlanStore } from '@/store/changePlanStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { AssistantPanel } from './AiPanel'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiRun: vi.fn(),
  fetchAiRequest: vi.fn(),
  fetchAiSession: vi.fn(),
  createChangePlan: vi.fn(),
  getChangePlan: vi.fn(),
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
const q = <T extends Element = HTMLElement>(sel: string) => host.querySelector<T>(sel)
const qa = (sel: string) => [...host.querySelectorAll<HTMLElement>(sel)]
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

let be: ReturnType<typeof createDraftBackend>

beforeEach(async () => {
  __resetWriteStampForTests() // 每条用例都是「新开的页面」：写入戳下限从 0 起步
  be = createDraftBackend()
  globalThis.fetch = (async (url: unknown, init?: RequestInit) =>
    be.handle(String(url), init) ?? new Response('{}')) as typeof fetch
  localStorage.clear()
  setCurrentProjectId('pa')
  vi.mocked(aiRun).mockReset()
  vi.mocked(fetchAiRequest).mockReset()
  vi.mocked(fetchAiRequest).mockRejectedValue(new TypeError('down')) // 默认读不到：还原出的未确认提交自动收尾时保持未确认（各用例按需改写）
  vi.mocked(createChangePlan).mockReset().mockResolvedValue({ plan_id: 'pl1', state: 'running' })
  vi.mocked(getChangePlan).mockReset().mockResolvedValue({ plan_id: 'pl1', state: 'running', phase: 'model' })
  useChangePlanStore.getState().clear()
  vi.mocked(fetchAiSession).mockReset()
  vi.mocked(fetchAiSession).mockRejectedValue(new ApiError('nf', 404, {})) // 后端明确没有详情：凭摘要往下走
  useUiStore.setState({ status: null, confirm: null, elementPanelId: null, selectedGids: [] } as never)
  useAiStore.setState({
    sessions: [], submissions: {}, scope: 'figure',
    caps: capsOf([agentCaps()], { change_plan_enabled: true }), agent: 'codex', models: {}, efforts: {},
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


const NEEDS_SCRIPT = {
  plan_id: 'pl1',
  state: 'ready',
  code: 'change_plan_empty',
  plan: {
    id: 'pl1', version: 1, source: 'assistant', base: { target: 'Fig1.pdf', fingerprint: 'x' }, base_patches: [], candidate_patches: [],
    groups: [], unhandled: [{ comment_id: 'a1', reason: 'needs_script' }], preview: { before: '/b.png', after: '/a.png' },
  },
} as never

const sendText = async (text: string) => {
  await mount()
  await typeText(text)
  await click(q('[data-ai-send="send"]'))
  await settle()
}

describe('发送路由', () => {
  it('默认（开关开）：走方案路径——带原话与图，不起改脚本会话；原话出现在对话里；卡片在生成；生成中草稿与输入框先留着（刷新不丢）', async () => {
    await sendText('把线条加粗')
    expect(aiRun).not.toHaveBeenCalled()
    expect(createChangePlan).toHaveBeenCalledOnce()
    expect(vi.mocked(createChangePlan).mock.calls[0][0]).toMatchObject({ source: 'assistant', id: 'Fig1.pdf', instruction: '把线条加粗', patches: [] })
    expect(input().value).toBe('把线条加粗')
    expect(be.draft('pa', 'Fig1.pdf')).toBe('把线条加粗')
    expect(q('[data-change-plan-user]')?.textContent).toBe('把线条加粗')
    expect(q('[data-change-plan-card="generating"]')).toBeTruthy()
  })

  it('方案出来（落定）：这时才清输入框与后端草稿', async () => {
    vi.mocked(getChangePlan).mockResolvedValue({
      plan_id: 'pl1', state: 'ready',
      plan: { id: 'pl1', version: 1, source: 'assistant', base: { target: 'Fig1.pdf', fingerprint: 'x' }, base_patches: [], candidate_patches: [],
        groups: [{ key: 'g1', label: 'x', comment_id: 'a1', atomic: false, changes: [], boxes: { after: [] } }], unhandled: [], preview: { before: '/b.png', after: '/a.png' } },
    } as never)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    try {
      await mount()
      await typeText('把线条加粗')
      await click(q('[data-ai-send="send"]'))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('把线条加粗')
      await act(async () => void vi.advanceTimersByTimeAsync(1500))
      await settle()
    } finally {
      vi.useRealTimers()
    }
    expect(input().value).toBe('')
    expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined()
  })

  it('方案在生成：发送被锁（同一张图一次一份），不会起第二个任务', async () => {
    await sendText('把线条加粗')
    await typeText('再加粗一点')
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
    expect(createChangePlan).toHaveBeenCalledOnce()
  })

  it('开关关着（ai.change_plan.enabled = false）：照旧改脚本，一次 run，没有方案请求', async () => {
    useAiStore.setState({ caps: capsOf([agentCaps()], { change_plan_enabled: false }) })
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    await sendText('把线条加粗')
    expect(createChangePlan).not.toHaveBeenCalled()
    expect(aiRun).toHaveBeenCalledOnce()
    expect(q('[data-change-plan-user]')).toBeNull()
  })

  it.each(['change_plan_no_connection', 'change_plan_native_unsupported', 'change_plan_not_editable'])(
    '同步被拒（%s）：落回改脚本路径，同一句话、草稿照旧由账本清，没有留下方案卡',
    async (code) => {
      vi.mocked(createChangePlan).mockRejectedValue(new EngineError('x', '', code, ''))
      vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
      await sendText('把线条加粗')
      expect(aiRun).toHaveBeenCalledOnce()
      expect((vi.mocked(aiRun).mock.calls[0][0] as { prompt: string }).prompt).toBe('把线条加粗')
      expect(q('[data-change-plan-turn]')).toBeNull()
    },
  )

  it('整条指令都要改脚本：方案出来后自动续走现有改脚本路径（同一句话），对话里只留一行灰字', async () => {
    vi.mocked(getChangePlan).mockResolvedValue(NEEDS_SCRIPT)
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    try {
      await mount()
      await typeText('把数据换成对数坐标')
      await click(q('[data-ai-send="send"]'))
      await settle()
      await act(async () => void vi.advanceTimersByTimeAsync(1500))
      await settle()
    } finally {
      vi.useRealTimers()
    }
    expect(aiRun).toHaveBeenCalledOnce()
    expect((vi.mocked(aiRun).mock.calls[0][0] as { prompt: string }).prompt).toBe('把数据换成对数坐标')
    expect(q('[data-change-plan-handoff]')).toBeTruthy()
    expect(q('[data-change-plan-user]')).toBeNull() // 气泡由改脚本那一轮自己画，不重复
    expect(qa('[data-ai-user]')).toHaveLength(1)
    expect(qa('[data-ai-user]')[0].textContent).toContain('把数据换成对数坐标')
  })

  it('附了参考图：照旧改脚本（结构化调用只有文字）', async () => {
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    await mount()
    await typeText('照这张图的风格')
    // 模拟输入框里有一张参考图（上传结果已落定）
    const { useAiAttachStore, attachTargetKey } = await import('@/store/aiAttachStore')
    useAiAttachStore.setState({
      byTarget: { [attachTargetKey('pa', 'Fig1.pdf')]: [{ key: 'k1', serverId: 'srv1', kind: 'image', name: 'a.png', ext: 'png', status: 'ready' } as never] },
    } as never)
    await settle()
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(createChangePlan).not.toHaveBeenCalled()
    expect(aiRun).toHaveBeenCalledOnce()
  })

  it('一键排版的弹窗不依赖 AI：没连任何 Agent、也没选图时，助手页签里照样弹出（否则「应用」点不到）', async () => {
    useAiStore.setState({ caps: capsOf([], { change_plan_enabled: true }) })
    useSelectionStore.setState({ ids: [] } as never)
    const apply = vi.fn(() => 'applied' as const)
    const doc = useDocumentStore.getState()
    useChangePlanStore.getState().addLayoutEntry({
      key: 'L1', from: 'quick_layout', kind: 'layout', pj: null, panelId: '', fileId: '', source: { fileId: '', script: null },
      documentId: doc.documentId, loadSeq: doc.loadSeq, canvasId: doc.activeCanvasId, createdAt: 1, status: 'ready',
      plan: {
        id: 'L1', version: 1, source: 'quick_layout', base: { target: '', fingerprint: '' }, base_patches: [], candidate_patches: [],
        groups: [{ key: 'layout', label: '排版', atomic: true, changes: [], boxes: { after: [] } }], unhandled: [], preview: { before: 'blob:a', after: 'blob:b' },
      },
      beforeSrc: 'blob:a', afterSrc: 'blob:b', unchecked: {}, request: {}, comments: [], commentCount: 0, drafts: {},
      layout: { apply, regenerate: vi.fn(), count: 2 },
    })
    await mount()
    expect(q('[data-change-plan-card="ready"]')).toBeTruthy() // 没选图也在对话里
    await act(async () => useChangePlanStore.getState().openDialog('L1'))
    expect(document.body.querySelector('[data-change-plan-dialog]')).toBeTruthy()
    await act(async () => document.body.querySelector<HTMLElement>('[data-change-plan-confirm]')!.click())
    expect(apply).toHaveBeenCalledOnce()
  })

  it('A 图发起、切到 B 图并在 B 的输入框里写了字：A 的方案落定时不清 B 的输入框', async () => {
    useChangePlanStore.getState().clear()
    vi.mocked(getChangePlan).mockResolvedValueOnce({ plan_id: 'pl1', state: 'running', phase: 'model' }).mockResolvedValue(NEEDS_SCRIPT)
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    try {
      await mount()
      await typeText('A 的指令')
      await click(q('[data-ai-send="send"]'))
      await settle()
      await act(async () => useSelectionStore.setState({ ids: ['p2'] } as never))
      await settle()
      await typeText('B 的草稿')
      await act(async () => void vi.advanceTimersByTimeAsync(2600)) // A 的方案落定（整条要改脚本，续走）
      await settle()
    } finally {
      vi.useRealTimers()
    }
    expect(input().value).toBe('B 的草稿')
  })

  it('A 发起 → 切 B → 切回 A 写新稿：编辑序号不复用，A 落定时不清这份没提交的新稿', async () => {
    useChangePlanStore.getState().clear()
    vi.mocked(getChangePlan).mockResolvedValueOnce({ plan_id: 'pl1', state: 'running', phase: 'model' }).mockResolvedValue(NEEDS_SCRIPT)
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    try {
      await mount()
      await typeText('A 的指令')
      await click(q('[data-ai-send="send"]'))
      await settle()
      await act(async () => useSelectionStore.setState({ ids: ['p2'] } as never))
      await settle()
      await act(async () => useSelectionStore.setState({ ids: ['p1'] } as never))
      await settle()
      await typeText('A 的新稿')
      await act(async () => void vi.advanceTimersByTimeAsync(2600))
      await settle()
    } finally {
      vi.useRealTimers()
    }
    expect(input().value).toBe('A 的新稿')
  })
})
