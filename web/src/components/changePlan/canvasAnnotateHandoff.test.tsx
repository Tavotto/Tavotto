/**
 * 图内修改卡的「改在画布上加」（ADR 0208 第 ③ 期 3c，照 #2261 审阅卡「要改脚本」经用户点击转脚本卡的做法）：
 * 助手方案里有 `needs_canvas_annotation` 的未处理 → 卡上给这颗按钮；**点了才**转给矢量编辑方案，选区里没有矢量对象也能走；
 * 没点就一个字不发；点过之后按钮收起、同一条指令只画一次用户气泡。
 *
 * 判据的主语：「发了没有」量的是 `createVectorEditPlan` 的调用（不是界面上出现了什么）；「选区空也能走」量的是**请求里的 `selection` 为空**。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { ChangePlan } from '@/lib/api'
import { useChangePlanStore, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { emptyProject, type PanelObject } from '@/types/document'

const createPlan = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createVectorEditPlan: (...a: unknown[]) => createPlan(...a),
  getVectorEditPlan: () => new Promise(() => undefined),
}))
vi.mock('@/hooks/useVariantPng', () => ({ useVariantPng: () => ({ url: null, loading: false, approximate: false }), variantNeedsRender: () => false }))
vi.mock('@/hooks/useRuntimePreview', () => ({ useRuntimePreview: () => ({ url: null, exact: false }) }))

import '@/store/vectorPlanActions' // 注册「改在画布上加」要起的矢量方案
import { TooltipProvider } from '../ui/Tooltip'
import { ChangePlanCard } from './ChangePlanCard'
import { ChangePlanTurn } from './ChangePlanTranscript'

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

const PLAN: ChangePlan = {
  id: 'p1',
  version: 1,
  source: 'assistant',
  base: { target: 'f_p1', fingerprint: 'fp' },
  base_patches: [],
  candidate_patches: [{ gid: 'ax.title', prop: 'fontsize', value: 14 }],
  groups: [{ key: 'g1', label: '标题', atomic: false, changes: [{ target_gid: 'ax.title', prop: 'fontsize', before: 9, after: 14, patch: { gid: 'ax.title', prop: 'fontsize', value: 14 } }], boxes: { after: [{ gid: 'ax.title', box: [0.1, 0.1, 0.2, 0.2] }] } }],
  unhandled: [{ comment_id: 'a1', reason: 'needs_canvas_annotation' }],
  preview: { before: '/b.png', after: '/a.png' },
}
const entry = (over: Partial<PlanEntry> = {}): PlanEntry => {
  const s = useDocumentStore.getState()
  return {
    key: 'a',
    from: 'assistant',
    instruction: '标题放大，再给图加个 (a) 编号',
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
    request: { instruction: '标题放大，再给图加 (a) 编号' },
    comments: [],
    commentCount: 0,
    ...over,
  }
}
const put = (...es: PlanEntry[]) => act(() => useChangePlanStore.setState({ entries: Object.fromEntries(es.map((e) => [e.key, e])), order: es.map((e) => e.key), dialogKey: null }))

let host: HTMLDivElement
let root: Root
const render = (el: React.ReactNode) => act(() => root.render(<TooltipProvider>{el}</TooltipProvider>))
const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)

beforeEach(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  createPlan.mockReset().mockResolvedValue({ plan_id: 'vp1', state: 'running' })
  useChangePlanStore.setState({ entries: {}, order: [], dialogKey: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_canvas_annotate')
  useDocumentStore.getState().commit(literal('放入'), (d) => {
    d.objects.push({ id: 'p1', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: [] } as PanelObject)
  })
  useSelectionStore.getState().set(['p1']) // 选中的是子图，不是矢量对象
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

it('图内方案里有「要在画布上加标注」的未处理：卡上给「改在画布上加」，没点就不发', () => {
  put(entry())
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(q('[data-change-plan-canvas-annotate]')!.textContent).toBe('改在画布上加')
  expect(createPlan).not.toHaveBeenCalled()
})

it('没有这条未处理 / 不是助手来源：没有这颗按钮', () => {
  put(entry({ plan: { ...PLAN, unhandled: [{ comment_id: 'a1', reason: 'needs_script' }] } }))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(q('[data-change-plan-canvas-annotate]')).toBeNull()
  put(entry({ from: 'review' }))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(q('[data-change-plan-canvas-annotate]')).toBeNull()
})

it('点了才转交：矢量方案请求带原话、选区为空；原卡收起按钮，新卡接在它后面（只一个用户气泡）', async () => {
  put(entry())
  render(
    <>
      {Object.keys(useChangePlanStore.getState().entries).map((k) => (
        <Live key={k} k={k} />
      ))}
    </>,
  )
  await act(async () => {
    q('[data-change-plan-canvas-annotate]')!.click()
    await Promise.resolve()
  })
  expect(createPlan).toHaveBeenCalledTimes(1)
  const body = createPlan.mock.calls[0][0] as { instruction: string; selection: string[] }
  expect(body.instruction).toBe('标题放大，再给图加个 (a) 编号') // entry.instruction（用户原话）
  expect(body.selection).toEqual([])
  const st = useChangePlanStore.getState()
  expect(st.entries.a.canvasStarted).toBe(true)
  const next = Object.values(st.entries).find((e) => e.kind === 'vector')!
  expect(next.followsKey).toBe('a')
  expect(next.status).toBe('generating')
  act(() => root.render(<TooltipProvider><Turns /></TooltipProvider>))
  expect(q('[data-change-plan-canvas-annotate]')).toBeNull() // 点过就收起
  expect(document.body.querySelectorAll('[data-change-plan-user]')).toHaveLength(1)
})

it('不是发起时的那张画布：不转交', () => {
  put(entry({ canvasId: 'another' }))
  expect(useChangePlanStore.getState().runCanvasAnnotate('a')).toBe(false)
  expect(createPlan).not.toHaveBeenCalled()
})

function Live({ k }: { k: string }) {
  const e = useChangePlanStore((s) => s.entries[k])
  return e ? <ChangePlanCard entry={e} /> : null
}
function Turns() {
  const order = useChangePlanStore((s) => s.order)
  const entries = useChangePlanStore((s) => s.entries)
  return (
    <>
      {order.map((k) => entries[k]).map((e) => (
        <ChangePlanTurn key={e.key} entry={e} latest />
      ))}
    </>
  )
}

it('整条都要在画布上加（图内一条都改不了）：失败卡里也有这颗按钮，没有「改脚本来做」', () => {
  put(entry({ status: 'failed', failure: { code: 'change_plan_empty' }, plan: { ...PLAN, groups: [] } }))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(q('[data-change-plan-canvas-annotate]')).not.toBeNull()
  expect(q('[data-change-plan-run-script]')).toBeNull()
})
