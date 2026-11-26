/**
 * 审阅来源的方案卡上「改脚本做剩下的」（ADR 0206 修订 2026-11-26）：有「要改脚本」的意见（且后端存着原文）才出现，点了走 `runScript`；
 * 没有就不出现；全是要改脚本的失败卡也给这条路；脚本方案卡接在后面时不再重画审阅意见卡。
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
const cm = (id: string, text: string): ReviewComment => ({ id, v: 1, anchor: { kind: 'overall' }, author: '林悦', text, resolved: false, created_at: '2026-11-26T10:00:00+08:00', replies: [] })
const COMMENTS = [cm('c1', '标题太小'), cm('c2', '把数据换成对数坐标')]
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
  unhandled: [{ comment_id: 'c2', reason: 'needs_script' }],
  comments: COMMENTS.map((c) => ({ id: c.id, text: c.text })),
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
    commentCount: 2,
    ...over,
  }
}
const put = (...es: PlanEntry[]) => act(() => useChangePlanStore.setState({ entries: Object.fromEntries(es.map((e) => [e.key, e])), order: es.map((e) => e.key), dialogKey: null }))

let host: HTMLDivElement
let root: Root
const run = vi.fn()
const render = (el: React.ReactNode) => act(() => root.render(<TooltipProvider>{el}</TooltipProvider>))
const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)
const qa = (sel: string) => document.body.querySelectorAll<HTMLElement>(sel)

beforeEach(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  run.mockReset().mockResolvedValue(true)
  useChangePlanStore.setState({ entries: {}, order: [], dialogKey: null, runScript: run as never })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_review_script_card')
  useDocumentStore.getState().commit(literal('放入'), (d) => {
    d.objects.push({ id: 'p1', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject)
  })
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

it('审阅来源有「要改脚本」的意见：显示「改脚本做剩下的」，点了走 runScript', async () => {
  await put(entry('r'))
  await render(<ChangePlanTranscript panelId="p1" />)
  const btn = q('[data-change-plan-run-script]')!
  expect(btn.textContent).toBe('改脚本做剩下的')
  await act(async () => btn.click())
  expect(run).toHaveBeenCalledWith('r')
})

it('没有「要改脚本」的意见、后端没存原文、或已经交过一次：不显示', async () => {
  await put(entry('a', { plan: { ...PLAN, unhandled: [{ comment_id: 'c2', reason: 'unclear' }] } }))
  await render(<ChangePlanTranscript panelId="p1" />)
  expect(q('[data-change-plan-run-script]')).toBeNull()
  await put(entry('b', { plan: { ...PLAN, comments: undefined } }))
  expect(q('[data-change-plan-run-script]')).toBeNull()
  await put(entry('c', { scriptStarted: true }))
  expect(q('[data-change-plan-run-script]')).toBeNull()
})

it('全是要改脚本的审阅意见（没有一条能落成图内修改）：失败卡里同样给这条路，由用户点', async () => {
  await put(entry('f', { status: 'failed', failure: { code: 'change_plan_empty' }, plan: { ...PLAN, groups: [] } }))
  await render(<ChangePlanTranscript panelId="p1" />)
  expect(q('[data-change-plan-card="failed"]')).toBeTruthy()
  await act(async () => q('[data-change-plan-run-script]')!.click())
  expect(run).toHaveBeenCalledWith('f')
})

it('脚本方案卡接在审阅卡后面：不再画第二张审阅意见卡', async () => {
  const script: ChangePlan = { ...PLAN, id: 'p2', kind: 'script', groups: [], unhandled: [], comments: undefined }
  await put(entry('r', { scriptStarted: true }), entry('s', { followsKey: 'r', plan: script, comments: [COMMENTS[1]], commentCount: 1 }))
  await render(<ChangePlanTranscript panelId="p1" />)
  expect(qa('[data-change-plan-note-card]').length).toBe(1)
  expect(qa('[data-change-plan-turn]').length).toBe(2)
})
