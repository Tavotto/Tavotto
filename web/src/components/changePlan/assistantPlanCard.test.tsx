/**
 * 助手来源与一键排版的方案卡画法（ADR 0206 PR 4）：用户的原话是一个气泡、没有审阅意见卡；一句话说「改了几处、剩下的要改脚本」；
 * 「改脚本做剩下的」只在有 needs_script 时出现；全部要改脚本时只剩一行灰字；失败卡也给「改脚本做」；一键排版的卡不挂在某张图上、没选图也看得到。
 * 图片在 jsdom 里不加载，只看结构与 `data-change-plan-*` 锚点。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { ChangePlan, ReviewComment } from '@/lib/api'
import { useChangePlanStore, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject, type PanelObject } from '@/types/document'

vi.mock('@/hooks/useVariantPng', () => ({ useVariantPng: () => ({ url: null, loading: false, approximate: false }), variantNeedsRender: () => false }))
vi.mock('@/hooks/useRuntimePreview', () => ({ useRuntimePreview: () => ({ url: null, exact: false }) }))
const previewSnapshot = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPngSnapshot: (...a: unknown[]) => previewSnapshot(...a),
}))
const connectAi = vi.fn()
vi.mock('@/store/connectAiStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/connectAiStore')>()),
  requestConnectAi: (...a: unknown[]) => connectAi(...a),
}))

import { TooltipProvider } from '../ui/Tooltip'
import { ChangePlanDialog } from './ChangePlanDialog'
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
const comment = (i: number, anchor: ReviewComment['anchor']): ReviewComment => ({
  id: `c${i}`,
  v: 1,
  anchor,
  author: i === 1 ? '林悦' : '王工',
  text: i === 1 ? '标题太小' : '图例挡住了曲线',
  resolved: false,
  created_at: '2026-11-23T10:00:00+08:00',
  replies: [],
})
const COMMENTS = [comment(1, { kind: 'region', box: [0.1, 0.1, 0.2, 0.2] }), comment(2, { kind: 'overall' }), comment(3, { kind: 'overall' })]
const PLAN: ChangePlan = {
  id: 'p1',
  version: 1,
  source: 'review',
  base: { target: 'f_p1', fingerprint: 'fp' },
  base_patches: BASE,
  candidate_patches: [{ gid: 'ax.title', prop: 'fontsize', value: 14 }],
  groups: [
    {
      key: 'g1',
      label: '标题太小',
      comment_id: 'c1',
      atomic: false,
      changes: [{ target_gid: 'ax.title', prop: 'fontsize', before: 9, after: 14, patch: { gid: 'ax.title', prop: 'fontsize', value: 14 } }],
      boxes: { before: [{ gid: 'ax.title', box: [0.1, 0.1, 0.2, 0.2] }], after: [{ gid: 'ax.title', box: [0.1, 0.1, 0.3, 0.2] }] },
    },
  ],
  unhandled: [
    { comment_id: 'c2', reason: 'needs_script' },
    { comment_id: 'c3', reason: 'unclear' },
  ],
  preview: { before: '/b.png', after: '/a.png' },
}

const entry = (key: string, over: Partial<PlanEntry> = {}): PlanEntry => {
  const s = useDocumentStore.getState()
  return {
    key,
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
    beforeSrc: '/b.png',
    afterSrc: '/a.png',
    unchecked: {},
    drafts: {},
    request: { comments: COMMENTS },
    comments: COMMENTS,
    commentCount: 3,
    ...over,
  }
}
const put = (...es: PlanEntry[]) =>
  act(() => useChangePlanStore.setState({ entries: Object.fromEntries(es.map((e) => [e.key, e])), order: es.map((e) => e.key), dialogKey: null }))

let host: HTMLDivElement
let root: Root
const render = (el: React.ReactNode) => act(() => root.render(<TooltipProvider>{el}</TooltipProvider>))
const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)


const run = vi.fn()
const PLAN_A: ChangePlan = { ...PLAN, source: 'assistant', unhandled: [{ comment_id: 'a1', reason: 'needs_script' }], groups: [{ ...PLAN.groups[0], comment_id: 'a1', label: '把线条加粗' }] }
const assistant = (key: string, over: Partial<PlanEntry> = {}): PlanEntry =>
  entry(key, { from: 'assistant', instruction: '把线条加粗，再换成对数坐标', plan: PLAN_A, request: { instruction: '把线条加粗，再换成对数坐标' }, comments: [], commentCount: 0, ...over })

beforeEach(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  run.mockReset().mockResolvedValue(true)
  previewSnapshot.mockReset()
  useChangePlanStore.setState({ entries: {}, order: [], dialogKey: null, runScript: run as never })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_assistant_cards')
  useDocumentStore.getState().commit(literal('放入'), (d) => {
    d.objects.push({ id: 'p1', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject)
  })
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

it('助手来源：一个用户气泡（原话）+ 方案卡；没有审阅意见卡；一句话说改了几处、剩下的要改脚本', async () => {
  await put(assistant('a'))
  await render(<ChangePlanTranscript panelId="p1" />)
  expect(q('[data-change-plan-user]')?.textContent).toBe('把线条加粗，再换成对数坐标')
  expect(q('[data-change-plan-note-card]')).toBeNull()
  const card = q('[data-change-plan-card="ready"]')!
  expect(card.textContent).toContain('改了 1 处，剩下的要改脚本，这次没动')
  expect(card.textContent).not.toContain('意见')
})

it('「改脚本做剩下的」：只在有 needs_script 时出现，点了走 runScript；用过一次就不再出现', async () => {
  await put(assistant('a'))
  await render(<ChangePlanTranscript panelId="p1" />)
  await act(async () => q('[data-change-plan-run-script]')!.click())
  expect(run).toHaveBeenCalledWith('a')
  await put(assistant('a', { scriptStarted: true }))
  expect(q('[data-change-plan-run-script]')).toBeNull()
  await put(assistant('b', { plan: { ...PLAN_A, unhandled: [] } }))
  expect(q('[data-change-plan-run-script]')).toBeNull()
})

it('全部要改脚本交出去之后：气泡与卡都收起，只剩一行灰字（用户的话由改脚本那一轮画）', async () => {
  await put(assistant('a', { status: 'handoff', scriptStarted: true }))
  await render(<ChangePlanTranscript panelId="p1" />)
  expect(q('[data-change-plan-handoff]')?.textContent).toContain('要改脚本')
  expect(q('[data-change-plan-user]')).toBeNull()
  expect(q('[data-change-plan-card]')).toBeNull()
})

it('失败卡：助手来源另给「改脚本做」；续走失败的卡主动作是重试（再交一次改脚本）', async () => {
  await put(assistant('a', { status: 'failed', failure: { code: 'change_plan_model_failed' } }))
  await render(<ChangePlanTranscript panelId="p1" />)
  expect(q('[data-change-plan-failed-action="retry"]')).toBeTruthy()
  await act(async () => q('[data-change-plan-run-script]')!.click())
  expect(run).toHaveBeenCalledWith('a')
  await put(assistant('b', { status: 'failed', failure: { code: 'change_plan_script_failed' } }))
  expect(q('[data-change-plan-failed-action="script"]')).toBeTruthy()
  expect(q('[data-change-plan-run-script]')).toBeNull() // 主动作已经是它自己
  await act(async () => q('[data-change-plan-failed-action="script"]')!.click())
  expect(run).toHaveBeenCalledWith('b')
})

it('失败卡：审阅来源没有「改脚本做」（对照）', async () => {
  await put(entry('r', { status: 'failed', failure: { code: 'change_plan_model_failed' } }))
  await render(<ChangePlanTranscript panelId="p1" />)
  expect(q('[data-change-plan-run-script]')).toBeNull()
})

it('空方案（不全是要改脚本）：助手来源的那句话不提「意见」', async () => {
  await put(assistant('a', { status: 'failed', failure: { code: 'change_plan_empty' }, plan: { ...PLAN_A, groups: [], unhandled: [{ comment_id: 'a1', reason: 'unclear' }] } }))
  await render(<ChangePlanTranscript panelId="p1" />)
  const card = q('[data-change-plan-card="failed"]')!
  expect(card.textContent).toContain('这条没法直接改图')
  expect(card.textContent).toContain('指令不够具体') // 原因行用的是指令原话 + 一句原因（不说「意见」）
  expect(card.textContent).not.toContain('意见')
  expect(card.textContent).toContain('把线条加粗')
})

const layoutEntry = (over: Partial<PlanEntry> = {}): PlanEntry => {
  const apply = vi.fn(() => 'applied' as const)
  return entry('L', {
    from: 'quick_layout',
    kind: 'layout',
    panelId: '',
    fileId: '',
    source: { fileId: '', script: null },
    plan: { ...PLAN, source: 'quick_layout', base: { target: '', fingerprint: '' }, base_patches: [], candidate_patches: [], groups: [{ key: 'layout', label: '排版', atomic: true, changes: [], boxes: { before: [{ gid: 'p1', box: [0, 0, 0.5, 0.5] }], after: [{ gid: 'p1', box: [0.1, 0.1, 0.5, 0.5] }] } }], unhandled: [] },
    request: {},
    comments: [],
    commentCount: 0,
    layout: { apply, regenerate: vi.fn(), count: 2 },
    ...over,
  })
}

it('一键排版的卡：没选图（没有面板）也在助手里；一句话、「查看」「直接应用」；没有用户气泡 / 审阅意见卡', async () => {
  await put(layoutEntry())
  await render(<ChangePlanTranscript panelId="" />)
  const card = q('[data-change-plan-card="ready"]')!
  expect(card.textContent).toContain('2 张图重新排了版')
  expect(q('[data-change-plan-view]')).toBeTruthy()
  expect(q('[data-change-plan-apply]')).toBeTruthy()
  expect(q('[data-change-plan-user]')).toBeNull()
  expect(q('[data-change-plan-note-card]')).toBeNull()
})

it('一键排版的卡：不属于当前画布时不出现；弹窗里「应用」走 layout.apply，成功后转「已应用」', async () => {
  const e = layoutEntry()
  await put(e)
  await render(<><ChangePlanTranscript panelId="" /><ChangePlanDialog /></>)
  await act(async () => useChangePlanStore.getState().openDialog('L'))
  expect(q('[data-change-plan-dialog]')).toBeTruthy()
  expect(q('[data-change-group="layout"]')?.textContent).toContain('排版')
  await act(async () => q('[data-change-plan-confirm]')!.click())
  expect(e.layout!.apply).toHaveBeenCalledOnce()
  expect(useChangePlanStore.getState().entries.L.status).toBe('applied')
  expect(q('[data-change-plan-dialog]')).toBeNull()
  expect(q('[data-change-plan-card="applied"]')?.textContent).toContain('已应用 · 2 张图重新排了版')
  // 换一份文档：上一份的排版卡不跟过来
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
  await render(<ChangePlanTranscript panelId="" />)
  expect(q('[data-change-plan-card]')).toBeNull()
})
