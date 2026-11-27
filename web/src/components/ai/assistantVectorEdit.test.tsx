/**
 * 改图助手的矢量编辑入口（ADR 0212 第 6 期）：没有目标图、选区里有矢量对象时，输入框可用、发出去的是矢量编辑请求；
 * 有目标图时照旧（图内修改方案 / 改脚本）。方案卡本身的行为在 `changePlan/vectorPlan.test.tsx`。
 *
 * 判据的主语：路由量的是**发出去的是哪个端点**（`createVectorEditPlan` 还是 `createChangePlan`），不是界面上出现了什么字。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import '@/hooks/useServerEvents'
import { ApiError, createChangePlan, createVectorEditPlan, fetchAiRequest, fetchAiSession, getChangePlan, getVectorEditPlan } from '@/lib/api'
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
import { emptyProject, type CanvasObject } from '@/types/document'
import { AssistantPanel } from './AiPanel'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAiRequest: vi.fn(),
  fetchAiSession: vi.fn(),
  createChangePlan: vi.fn(),
  getChangePlan: vi.fn(),
  createVectorEditPlan: vi.fn(),
  getVectorEditPlan: vi.fn(),
}))

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let host: HTMLDivElement
const settle = async () => {
  for (let i = 0; i < 20; i++) await act(async () => Promise.resolve())
}
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
  await settle()
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
const send = async () => {
  const btn = q<HTMLButtonElement>('[data-ai-send]')!
  await act(async () => btn.click())
  await settle()
}

const panelOf = (fileId: string, id: string) =>
  ({ id, type: 'panel', x: 0, y: 0, w: 100, h: 75, fileId, fileKind: 'pdf', nativeW: 100, nativeH: 75, name: fileId, script: `${id}.py`, overrides: [] }) as unknown as CanvasObject
const textOf = (id: string) => ({ id, type: 'text', x: 10, y: 80, w: 20, h: 6, text: 'A', sizePt: 8 }) as unknown as CanvasObject

let be: ReturnType<typeof createDraftBackend>

beforeEach(async () => {
  __resetWriteStampForTests()
  be = createDraftBackend()
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => be.handle(String(url), init) ?? new Response('{}')) as typeof fetch
  localStorage.clear()
  setCurrentProjectId('pa')
  vi.mocked(fetchAiRequest).mockReset().mockRejectedValue(new TypeError('down'))
  vi.mocked(fetchAiSession).mockReset().mockRejectedValue(new ApiError('nf', 404, {}))
  vi.mocked(createChangePlan).mockReset().mockResolvedValue({ plan_id: 'pl1', state: 'running' })
  vi.mocked(getChangePlan).mockReset().mockResolvedValue({ plan_id: 'pl1', state: 'running', phase: 'model' })
  vi.mocked(createVectorEditPlan).mockReset().mockResolvedValue({ plan_id: 'vp1', state: 'running' })
  vi.mocked(getVectorEditPlan).mockReset().mockResolvedValue({ plan_id: 'vp1', state: 'running', phase: 'model' })
  useChangePlanStore.getState().clear()
  useUiStore.setState({ status: null, confirm: null, elementPanelId: null, selectedGids: [] } as never)
  useAiStore.setState({ sessions: [], submissions: {}, scope: 'figure', caps: capsOf([agentCaps()], { change_plan_enabled: true }), agent: 'codex', models: {}, efforts: {} })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ai_vector')
  useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panelOf('Fig1.pdf', 'p1'), panelOf('Fig2.pdf', 'p2'), textOf('t1'), textOf('t2')] } }) as never)
  useSelectionStore.setState({ ids: ['t1'] } as never)
})
afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  root = null
  document.body.innerHTML = ''
})

describe('改图助手的矢量编辑入口', () => {
  it('选中的是矢量对象、没有目标图：输入框可用，发出去的是矢量编辑请求（带选区），原话出现在对话里，卡片在生成中', async () => {
    await mount()
    expect(input().disabled).toBe(false)
    expect(input().placeholder).toContain('选中的 1 个对象')
    expect(q('[data-ai-empty="vector"]')).not.toBeNull()
    await typeText('把字号加倍')
    await send()
    expect(createVectorEditPlan).toHaveBeenCalledTimes(1)
    expect(createChangePlan).not.toHaveBeenCalled()
    const body = vi.mocked(createVectorEditPlan).mock.calls[0][0] as { instruction: string; selection: string[] }
    expect(body.instruction).toBe('把字号加倍')
    expect(body.selection).toEqual(['t1'])
    expect(q('[data-change-plan-user]')!.textContent).toBe('把字号加倍')
    expect(q('[data-change-plan-card]')!.dataset.changePlanCard).toBe('generating')
    expect(input().value).toBe('') // 卡片已经在对话里，输入框清空
    // 在生成时再发不出第二份：发送钮锁住
    await typeText('再来')
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
  })

  it('选区里有子图又有矢量对象（没有单一目标图）：照样走矢量编辑，选区只带矢量对象', async () => {
    useSelectionStore.setState({ ids: ['p1', 't1', 't2'] } as never)
    await mount()
    await typeText('左对齐')
    await send()
    expect((vi.mocked(createVectorEditPlan).mock.calls[0][0] as { selection: string[] }).selection).toEqual(['t1', 't2'])
  })

  it('选中的是一张可编辑的图：照旧先出图内修改方案，不走矢量编辑', async () => {
    useSelectionStore.setState({ ids: ['p1'] } as never)
    await mount()
    expect(q('[data-ai-empty="vector"]')).toBeNull()
    await typeText('把图例移到左上角')
    await send()
    expect(createChangePlan).toHaveBeenCalledTimes(1)
    expect(createVectorEditPlan).not.toHaveBeenCalled()
  })

  it('方案卡开关关着：这条路不开，输入框照旧禁用（没有目标图）', async () => {
    useAiStore.setState({ caps: capsOf([agentCaps()], { change_plan_enabled: false }) })
    await mount()
    expect(input().disabled).toBe(true)
    expect(q('[data-ai-empty="vector"]')).toBeNull()
  })

  it('什么都没选：照旧是「选一张图」的空态，输入框禁用', async () => {
    useSelectionStore.setState({ ids: [] } as never)
    await mount()
    // 画布上有两张可编辑的图，没有单一目标
    expect(input().disabled).toBe(true)
  })
})
