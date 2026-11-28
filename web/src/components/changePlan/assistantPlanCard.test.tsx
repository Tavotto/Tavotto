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
import { useLicenseStore } from '@/store/licenseStore'
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
import { pendingSync } from './reviewSync'
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

/** 已应用（文档就是方案写进去的那份）：这里图内修改已经是 BASE，直接记成写进去的那份 */
const applied = { status: 'applied' as const, appliedPatches: BASE }

it('「改脚本做剩下的」：待确认卡上不出现（不替用户偷偷应用）；已应用后并进摘要句做行内链接，点了走 runScript；用过一次就不再出现', async () => {
  await put(assistant('a'))
  await render(<ChangePlanTranscript panelId="p1" />)
  expect(q('[data-change-plan-card="ready"]')).toBeTruthy()
  expect(q('[data-change-plan-run-script]')).toBeNull()
  await put(assistant('a', applied))
  const link = q('[data-change-plan-card="applied"] [data-change-plan-run-script]')!
  expect(link.textContent).toBe('剩下的改脚本来做 ›')
  // 行内：在摘要那句话里，不在按钮行
  expect(link.closest('p')!.textContent).toBe('已应用 · 改了 1 处，剩下的改脚本来做 ›')
  await act(async () => link.click())
  expect(run).toHaveBeenCalledWith('a')
  await put(assistant('a', { ...applied, scriptStarted: true }))
  expect(q('[data-change-plan-run-script]')).toBeNull()
  await put(assistant('b', { ...applied, plan: { ...PLAN_A, unhandled: [] } }))
  expect(q('[data-change-plan-run-script]')).toBeNull()
})

it('按钮行最多三颗：待确认「查看 / 直接应用 / 批注」，已应用「撤销 / 再改一版」（改脚本不占按钮位）', async () => {
  useLicenseStore.setState({ status: { enabled: true, licensed: true, state: 'active', features: ['change-plan-rerun'], exp: null, email_hint: '', needs_refresh: false } })
  await put(assistant('a'))
  await render(<ChangePlanTranscript panelId="p1" />)
  const buttons = (sel: string) => [...q(sel)!.querySelectorAll('[data-change-plan-body] > div button')]
  expect(buttons('[data-change-plan-card="ready"]').map((b) => b.textContent)).toEqual(['查看', '直接应用', '批注'])
  useDocumentStore.getState().commit(literal('应用'), (d) => {
    ;(d.objects[0] as PanelObject).overrides = [...BASE]
  })
  await put(assistant('a', { ...applied, appliedEntry: useDocumentStore.getState().past.at(-1) }))
  const row = buttons('[data-change-plan-card="applied"]')
  expect(row.map((b) => b.textContent)).toEqual(['撤销', '再改一版'])
  expect(row.length).toBeLessThanOrEqual(3)
  useLicenseStore.setState({ status: null })
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

it('不牵连审阅页签：助手 / 一键排版的卡应用后不会去标「已处理」意见（没有分享记录 id）', () => {
  const detail = { share_id: 's1', comments: COMMENTS.map((c) => ({ ...c, resolved: false })) } as never
  const overrides = [...BASE, { gid: 'ax.title', prop: 'fontsize', value: 14 }]
  const applied = assistant('a', { status: 'applied', appliedPatches: overrides })
  expect(pendingSync(applied, overrides, detail, null)).toBeNull()
  expect(pendingSync(layoutEntry({ status: 'applied' }), overrides, detail, null)).toBeNull()
})

it('弹窗清单：图内方案每组下面逐行列出改了什么（属性 前 → 后）；「顺带留出边距」整份只说一次，留边距那一处悬停不画整块框', async () => {
  const room = { target_gid: 'ax', prop: 'position', before: [0.1, 0.1, 0.8, 0.8], after: [0.12, 0.1, 0.78, 0.8], auto: 'room' as const, patch: { gid: 'ax', prop: 'position', value: [0.12, 0.1, 0.78, 0.8] } }
  const g1 = { ...PLAN.groups[0], changes: [...PLAN.groups[0].changes, room], boxes: { before: [...PLAN.groups[0].boxes.before!, { gid: 'ax', box: [0.1, 0.1, 0.8, 0.8] as [number, number, number, number] }], after: [...PLAN.groups[0].boxes.after, { gid: 'ax', box: [0.12, 0.1, 0.78, 0.8] as [number, number, number, number] }] } }
  const g2 = { ...g1, key: 'g2', label: '图例', comment_id: 'c2' }
  await put(assistant('a', { plan: { ...PLAN_A, groups: [g1, g2] } }))
  act(() => useChangePlanStore.getState().openDialog('a'))
  await render(<ChangePlanDialog />)
  const rows = [...document.body.querySelectorAll<HTMLElement>('[data-change-group]')]
  expect(rows).toHaveLength(2)
  const lines = [...rows[0].querySelectorAll<HTMLElement>('[data-change-line]')].map((l) => l.textContent)
  // 留边距那条不进逐行清单；字号那条带前后值
  expect(lines).toHaveLength(1)
  expect(lines[0]).toContain('9')
  expect(lines[0]).toContain('14')
  expect(lines[0]).toContain('→')
  const metas = rows.map((r) => r.querySelector('[data-change-plan-group-meta]')?.textContent ?? '')
  expect(metas.filter((m) => m.includes('顺带留出边距'))).toHaveLength(1)
  expect(metas[0]).toContain('顺带留出边距')
  // 列了改动、又没有审阅者：不再重复「N 处改动」
  expect(metas[1]).toBe('')
  // 悬停这一组：只画字号那一处的框，不画留边距挪了的整块子图
  const { activeBoxes } = { activeBoxes: () => [...document.body.querySelectorAll('[data-change-plan-box="g1"]')] }
  expect(activeBoxes().every((b) => (b as HTMLElement).style.width !== '78%' && (b as HTMLElement).style.width !== '80%')).toBe(true)
})


it('弹窗清单：一组超过 4 行时折成「还有 N 处」，点了展开', async () => {
  const mk = (i: number) => ({ target_gid: `ax.t${i}`, prop: 'linewidth', before: 1, after: 2 + i, patch: { gid: `ax.t${i}`, prop: 'linewidth', value: 2 + i } })
  const g1 = { ...PLAN.groups[0], changes: [0, 1, 2, 3, 4, 5].map(mk) }
  await put(assistant('a', { plan: { ...PLAN_A, groups: [g1] } }))
  act(() => useChangePlanStore.getState().openDialog('a'))
  await render(<ChangePlanDialog />)
  const count = () => document.body.querySelectorAll('[data-change-group] [data-change-line]').length
  expect(count()).toBe(4)
  const more = document.body.querySelector<HTMLElement>('[data-change-more]')!
  expect(more.textContent).toBe('还有 2 处')
  await act(async () => more.click())
  expect(count()).toBe(6)
  expect(document.body.querySelector('[data-change-more]')).toBeNull()
})
