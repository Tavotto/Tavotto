/**
 * 对数轴下限的确认（ADR 0206 修订 2026-11-26）：方案里带 `confirm` 的组，卡片先问「要一起改吗」，回答前没有「查看 / 应用」；
 * 「一起改」→ 与平常的待确认卡一样；「不改」→ 这一组整组不要、那条意见进没处理列表，没有组剩下就和空方案一样。
 * 另盯复数：英文「1 place / 2 places」「1 change / 3 changes」。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { literal, setLocale } from '@/i18n'
import type { ChangePlan, ChangePlanGroup } from '@/lib/api'
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

const BASE = [{ gid: 'ax0', prop: 'ylabel', value: 'Conversion' }]
const LOG: ChangePlanGroup = {
  key: 'g1',
  label: '纵轴改成对数坐标',
  comment_id: 'a1',
  atomic: false,
  changes: [
    { target_gid: 'ax0', prop: 'yscale', before: 'linear', after: 'log', patch: { gid: 'ax0', prop: 'yscale', value: 'log' } },
    { target_gid: 'ax0', prop: 'ylim', before: [0, 1.05], after: [0.01, 1.05], patch: { gid: 'ax0', prop: 'ylim', value: [0.01, 1.05] } },
  ],
  boxes: { after: [{ gid: 'ax0', box: [0.1, 0.1, 0.8, 0.8] }] },
  confirm: { kind: 'log_lower_limit', axis: 'y', gid: 'ax0', from: 0, to: 0.01 },
}
const TITLE: ChangePlanGroup = {
  key: 'g2',
  label: '标题大一点',
  comment_id: 'a1',
  atomic: false,
  changes: [{ target_gid: 'ax0.title', prop: 'fontsize', before: 9, after: 12, patch: { gid: 'ax0.title', prop: 'fontsize', value: 12 } }],
  boxes: { after: [{ gid: 'ax0.title', box: [0.1, 0.0, 0.3, 0.1] }] },
}
const plan = (groups: ChangePlanGroup[]): ChangePlan => ({
  id: 'p1',
  version: 1,
  source: 'assistant',
  base: { target: 'f_p1', fingerprint: 'fp' },
  base_patches: BASE,
  candidate_patches: [...BASE, ...groups.flatMap((g) => g.changes.map((c) => c.patch!))],
  groups,
  unhandled: [],
  preview: { before: '/b.png', after: '/a.png' },
})

const entry = (key: string, groups: ChangePlanGroup[], over: Partial<PlanEntry> = {}): PlanEntry => {
  const s = useDocumentStore.getState()
  return {
    key,
    from: 'assistant',
    instruction: '把纵轴改成对数坐标',
    pj: null,
    panelId: 'p1',
    fileId: 'f_p1',
    source: { fileId: 'f_p1', script: 'fig.py' },
    documentId: s.documentId,
    loadSeq: s.loadSeq,
    canvasId: s.activeCanvasId,
    createdAt: 1,
    status: 'ready',
    plan: plan(groups),
    beforeSrc: '/b.png',
    afterSrc: '/a.png',
    unchecked: {},
    drafts: {},
    request: { instruction: '把纵轴改成对数坐标' },
    comments: [],
    commentCount: 0,
    ...over,
  }
}
const put = (...es: PlanEntry[]) =>
  act(() => useChangePlanStore.setState({ entries: Object.fromEntries(es.map((e) => [e.key, e])), order: es.map((e) => e.key), dialogKey: null }))
const cps = () => useChangePlanStore.getState()

let host: HTMLDivElement
let root: Root
const render = (el: React.ReactNode) => act(() => root.render(<TooltipProvider>{el}</TooltipProvider>))
const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)

beforeEach(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  previewSnapshot.mockReset().mockResolvedValue({ url: 'blob:partial', manifest: { elements: [] } })
  useChangePlanStore.setState({ entries: {}, order: [], dialogKey: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_log_limit')
  useDocumentStore.getState().commit(literal('放入'), (d) => {
    d.objects.push({ id: 'p1', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject)
  })
})
afterEach(async () => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  await setLocale('zh-CN')
})

it('带确认的组：卡上只有一句问话 + 「一起改」「不改」，没有「查看 / 直接应用」', async () => {
  await put(entry('a', [LOG]))
  await render(<ChangePlanTranscript panelId="p1" />)
  const card = q('[data-change-plan-card="ready"]')!
  expect(card.textContent).toContain('对数轴的下限要大于 0，把纵轴下限一起改成 10⁻² 吗？')
  expect(q('[data-change-plan-confirm-log="yes"]')?.textContent).toBe('一起改')
  expect(q('[data-change-plan-confirm-log="no"]')?.textContent).toBe('不改')
  expect(q('[data-change-plan-view]')).toBeNull()
  expect(q('[data-change-plan-apply]')).toBeNull()
  // 回答之前，弹窗与应用都拒绝
  act(() => cps().openDialog('a'))
  expect(cps().dialogKey).toBeNull()
  expect(cps().apply('a')).toBe('nothing')
  expect(useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')).toMatchObject({ overrides: BASE })
})

it('「一起改」：变成平常的待确认卡（查看 / 应用都在），应用写进的是带下限的整组', async () => {
  await put(entry('a', [LOG]))
  await render(<ChangePlanTranscript panelId="p1" />)
  await act(async () => q('[data-change-plan-confirm-log="yes"]')!.click())
  expect(q('[data-change-plan-confirm-log]')).toBeNull()
  expect(q('[data-change-plan-view]')).toBeTruthy()
  expect(q('[data-change-plan-apply]')).toBeTruthy()
  expect(cps().entries.a.answers).toEqual({ g1: 'yes' })
  await act(async () => q('[data-change-plan-apply]')!.click())
  const panel = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1') as PanelObject
  expect(panel.overrides).toEqual(expect.arrayContaining([{ gid: 'ax0', prop: 'ylim', value: [0.01, 1.05] }, { gid: 'ax0', prop: 'yscale', value: 'log' }]))
})

it('「不改」而没有别的组：当作空方案，失败卡里写明这次没动', async () => {
  await put(entry('a', [LOG]))
  await render(<ChangePlanTranscript panelId="p1" />)
  await act(async () => q('[data-change-plan-confirm-log="no"]')!.click())
  expect(cps().entries.a).toMatchObject({ status: 'failed', failure: { code: 'change_plan_empty' } })
  const card = q('[data-change-plan-card="failed"]')!
  expect(card.textContent).toContain('对数轴下限没改，这次没动')
  expect(card.textContent).toContain('把纵轴改成对数坐标')
  expect(q('[data-change-plan-apply]')).toBeNull()
  expect(useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')).toMatchObject({ overrides: BASE })
})

it('「不改」而还有别的组：这一组整组不要（只应用剩下的），那条意见出现在没处理列表', async () => {
  await put(entry('a', [LOG, TITLE]))
  await render(<ChangePlanTranscript panelId="p1" />)
  await act(async () => q('[data-change-plan-confirm-log="no"]')!.click())
  const e = cps().entries.a
  expect(e.status).toBe('ready')
  expect(e.answers).toEqual({ g1: 'no' })
  expect(e.unchecked).toEqual({ g1: true })
  expect(q('[data-change-plan-view]')).toBeTruthy()
  // 弹窗清单：被拒的组不在清单里，意见在没处理列表里、原因是「对数轴下限没改」
  act(() => cps().openDialog('a'))
  await render(<ChangePlanDialog />)
  expect(document.body.querySelectorAll('[data-change-group]').length).toBe(1)
  expect(document.body.querySelector('[data-change-group="g2"]')).toBeTruthy()
  expect(q('[data-change-plan-unhandled="a1"]')?.textContent).toContain('对数轴下限没改，这次没动')
  await act(async () => {
    await new Promise((r) => setTimeout(r, 450)) // 出图防抖
  })
  expect(previewSnapshot).toHaveBeenCalled() // 弹窗里「修改后」按剩下的组出图，不带上被拒的那组
  const sent = previewSnapshot.mock.calls.at(-1)![1] as { gid: string; prop: string }[]
  expect(sent.some((p) => p.prop === 'yscale' || p.prop === 'ylim')).toBe(false)
  act(() => {
    cps().apply('a')
  })
  const panel = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1') as PanelObject
  expect(panel.overrides).toEqual([...BASE, { gid: 'ax0.title', prop: 'fontsize', value: 12 }].sort((a, b) => a.gid.localeCompare(b.gid)))
})

it('英文复数：1 place / 3 places，1 change / 3 changes，没有「1 places」', async () => {
  await setLocale('en-US')
  const one = (g: ChangePlanGroup) => ({ ...g, confirm: undefined })
  await put(entry('a', [one(TITLE)]), entry('b', [one({ ...TITLE, key: 'g9', boxes: { after: [TITLE.boxes.after[0], TITLE.boxes.after[0], TITLE.boxes.after[0]] } })]))
  await render(<ChangePlanTranscript panelId="p1" />)
  const text = document.body.textContent ?? ''
  expect(text).toContain('Changed 1 place')
  expect(text).toContain('Changed 3 places')
  expect(text).not.toMatch(/1 places/)
  act(() => cps().openDialog('a'))
  await render(<ChangePlanDialog />)
  expect(q('[data-change-group="g2"]')?.textContent).toContain('1 change')
  expect(q('[data-change-group="g2"]')?.textContent).not.toMatch(/^\s*1\s*$/)
})

it('带自动腾出的边距的组：清单行灰字里多一句「腾出边距，放下轴标签」；没有的组没有；英文同样', async () => {
  const room: ChangePlanGroup = {
    ...LOG,
    confirm: undefined,
    changes: [
      ...LOG.changes,
      { target_gid: 'ax0', prop: 'position', before: [0.1, 0.1, 0.8, 0.8], after: [0.14, 0.1, 0.76, 0.8], auto: 'room', patch: { gid: 'ax0', prop: 'position', value: [0.14, 0.1, 0.76, 0.8] } },
    ],
  }
  await put(entry('a', [room, TITLE]))
  act(() => cps().openDialog('a'))
  await render(<ChangePlanDialog />)
  expect(q('[data-change-group="g1"]')?.textContent).toContain('腾出边距，放下轴标签')
  expect(q('[data-change-group="g2"]')?.textContent).not.toContain('腾出边距')
  await setLocale('en-US')
  await render(<ChangePlanDialog />)
  expect(q('[data-change-group="g1"]')?.textContent).toContain('Made room for the axis label')
})
