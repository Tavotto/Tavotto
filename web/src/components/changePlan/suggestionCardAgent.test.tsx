/**
 * Agent 来源的建议待办卡（ADR 0217 §五）：标「来自…」；没有「再加一条」、脚本待办写「交给 Agent 改」；
 * 左边一颗「收起」；落定后图内改了几项 + 一行「N 项交回…改」。图片在 jsdom 里不加载，只看结构与 `data-*` 锚点。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { ChangePlan } from '@/lib/api'
import { useChangePlanStore, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject, type PanelObject } from '@/types/document'

const reportAgentPlanApplied = vi.fn(async () => undefined)
vi.mock('@/hooks/useVariantPng', () => ({ useVariantPng: () => ({ url: null, loading: false, approximate: false }), variantNeedsRender: () => false }))
vi.mock('@/hooks/useRuntimePreview', () => ({ useRuntimePreview: () => ({ url: null, exact: false }) }))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPngSnapshot: vi.fn(async () => new Promise(() => undefined)),
  reportAgentPlanApplied: (...a: unknown[]) => reportAgentPlanApplied(...(a as [])),
}))

import { TooltipProvider } from '../ui/Tooltip'
import { ChangePlanTranscript } from './ChangePlanTranscript'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const BASE = [{ gid: 'ax.title', prop: 'fontsize', value: 9 }]
const g = (key: string, cid: string, gid: string) => ({
  key,
  label: key,
  comment_id: cid,
  atomic: false,
  changes: [{ target_gid: gid, prop: 'linewidth', before: 1, after: 3, patch: { gid, prop: 'linewidth', value: 3 } }],
  boxes: { after: [{ gid, box: [0.1, 0.1, 0.2, 0.2] as [number, number, number, number] }] },
})
const PLAN: ChangePlan = {
  id: 'p1',
  version: 1,
  source: 'assistant',
  reply: '线偏细，还可以加误差线。',
  todos: [
    { key: 't1', label: '线条加粗', why: '远看不清', kind: 'figure', group_key: 'g1' },
    { key: 't2', label: '图例挪到左上角', kind: 'figure', group_key: 'g2' },
    { key: 't3', label: '加误差线（SD）', why: '要看离散度', kind: 'script', comment_id: 'a3' },
  ],
  base: { target: 'f_p1', fingerprint: 'fp' },
  base_patches: BASE,
  candidate_patches: BASE,
  groups: [g('g1', 'a1', 'ax.line'), g('g2', 'a2', 'ax.legend')],
  unhandled: [{ comment_id: 'a3', reason: 'needs_script' }],
  preview: { before: '/b.png', after: '/a.png' },
}
const entry = (key: string, over: Partial<PlanEntry> = {}): PlanEntry => {
  const s = useDocumentStore.getState()
  return {
    key,
    from: 'assistant',
    pj: null,
    panelId: 'p1',
    fileId: 'f_p1',
    source: { fileId: 'f_p1', script: 'fig.py' },
    documentId: s.documentId,
    loadSeq: s.loadSeq,
    canvasId: s.activeCanvasId,
    createdAt: 1,
    status: 'ready',
    plan: PLAN,
    planId: 'p1',
    beforeSrc: '/b.png',
    afterSrc: '/a.png',
    unchecked: {},
    drafts: {},
    agent: {},
    request: {},
    comments: [],
    commentCount: 0,
    ...over,
  }
}
const realApplyTodos = useChangePlanStore.getState().applyTodos
const apply = vi.fn()
const put = (...es: PlanEntry[]) =>
  act(() => useChangePlanStore.setState({ entries: Object.fromEntries(es.map((e) => [e.key, e])), order: es.map((e) => e.key), dialogKey: null, applyTodos: apply as never }))

let host: HTMLDivElement
let root: Root
const render = () => act(() => root.render(<TooltipProvider><ChangePlanTranscript panelId="p1" /></TooltipProvider>))
const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)

beforeEach(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  apply.mockReset().mockResolvedValue('applied')
  reportAgentPlanApplied.mockClear()
  useChangePlanStore.setState({ entries: {}, order: [], dialogKey: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_suggestion_card')
  useDocumentStore.getState().commit(literal('放入'), (d) => {
    d.objects.push({ id: 'p1', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject)
  })
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})


it('标「来自…」，没有「再加一条」，脚本待办写「交给 Agent 改」', async () => {
  put(entry('a'))
  await render()
  expect(q('[data-suggestion-from-agent]')?.textContent).toBe('来自插件')
  expect(q('[data-suggestion-add]')).toBeNull()
  const section = q('[data-suggestion-script-section]')!
  expect(section.textContent).toBe('改脚本 · 交回插件')
  expect(section.getAttribute('title')).toBe('这几项要改脚本，不在这里做；勾着的会交回插件')
  expect(q('[data-suggestion-apply]')?.textContent).toBe('改这 3 项')
})

it('窗口自己出的建议卡不带来源标签', async () => {
  put(entry('a', { agent: undefined }))
  await render()
  expect(q('[data-suggestion-from-agent]')).toBeNull()
  expect(q('[data-suggestion-dismiss]')).toBeNull()
  expect(q('[data-suggestion-script-section]')?.textContent).toBe('要改脚本')
})

it('「收起」只在本地收起、不去后端放弃那份方案', async () => {
  put(entry('a'))
  await render()
  await act(async () => q('[data-suggestion-dismiss]')!.click())
  expect(useChangePlanStore.getState().entries.a).toBeUndefined()
})

it('图已改过：只写「已过期」+「收起」，不给重新生成', async () => {
  put(entry('a', { source: { fileId: 'f_p1', script: 'other.py' } }))
  await render()
  expect(q('[data-change-plan-regenerate]')).toBeNull()
  expect(q('[data-suggestion-dismiss]')).toBeTruthy()
})

it('落定：改了图内几项 + 一行「N 项交回…改」，没改里不再列交回去的那条', async () => {
  const G = { gid: 'ax.line', prop: 'linewidth', value: 3 }
  const todos = [PLAN.todos![0], PLAN.todos![2]]
  put(entry('a', { plan: { ...PLAN, todos, groups: [PLAN.groups[0]], candidate_patches: [...BASE, G] } }))
  await act(async () => useChangePlanStore.setState({ applyTodos: realApplyTodos }))
  await render()
  await act(async () => q('[data-suggestion-apply]')!.click())
  expect(reportAgentPlanApplied).toHaveBeenCalledWith('p1', ['t1', 't3'])
  expect(q('[data-suggestion-done]')?.textContent).toBe('已按建议改了 1 项')
  expect(q('[data-suggestion-handed]')?.textContent).toBe('1 项交回插件改')
  expect(q('[data-suggestion-skipped]')).toBeNull()
  expect(q('[data-change-plan-undo]')).toBeTruthy()
})

it('只交回、窗口里一项没改：标题就是「N 项交回…改」', async () => {
  put(entry('a', { todoRun: { figure: [], script: [], scripts: [] }, agent: { reported: ['t3'], handed: ['t3'] } }))
  await render()
  expect(q('[data-suggestion-done]')?.textContent).toBe('1 项交回插件改')
  expect(q('[data-suggestion-handed]')).toBeNull()
})

it('Agent 来源的卡不能按批注重改（续改会换成插件读不到的另一份方案）', async () => {
  const { canRevise } = await import('./changePlanModel')
  expect(canRevise(entry('a'))).toBe(false)
  expect(canRevise(entry('b', { agent: undefined }))).toBe(true)
  put(entry('a'))
  expect(await useChangePlanStore.getState().revise('a')).toBe('failed')
})

it('窗口里改的被 ⌘Z 撤了、也没交回 Agent：卡不再出现', async () => {
  put(entry('a', { status: 'applied', todoRun: { figure: ['t1'], script: [], scripts: [] }, agent: { reported: ['t1'], handed: [] } }))
  await render()
  expect(q('[data-change-plan-suggestion]')).toBeNull()
})

it('按宿主写名字：Codex / Claude Code；「先不改」在底行最右', async () => {
  put(entry('a', { agent: { host: 'codex' } }))
  await render()
  expect(q('[data-suggestion-from-agent]')?.textContent).toBe('来自 Codex')
  expect(q('[data-suggestion-script-section]')?.textContent).toBe('改脚本 · 交回 Codex')
  const row = q('[data-suggestion-actions]')!
  expect(row.lastElementChild).toBe(q('[data-suggestion-dismiss]'))
  expect(q('[data-suggestion-dismiss]')?.textContent).toBe('先不改')
  put(entry('b', { agent: { host: 'claude_code' } }))
  await render()
  expect(q('[data-suggestion-from-agent]')?.textContent).toBe('来自 Claude Code')
})
