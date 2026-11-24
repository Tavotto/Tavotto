/**
 * 一键排版先出修改方案卡（ADR 0206 §一，`source: quick_layout`）：求解回来后文档一个字不动，做成一张卡（一个 `atomic` 组「排版」）
 * + 前后两张整版预览；「应用」才走**现有**的那一次 commit（一步撤销不变，检查点此刻才取）；应用前图被改过 = 「图已改过」不写；
 * 预览出不来 = 不卡住、直接落地 + 一句灰字；拖分隔线调比例不出卡。落地本身的细节在 `quickLayoutActions.test.ts`（那份关掉出卡）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { formatMessage } from '@/i18n'
import type { QuickLayoutResponse } from '@/lib/api'
import { emptyProject, type PanelObject, type TextObject } from '@/types/document'

const quickLayoutPlan = vi.fn()
const fetchLayoutPreviewPng = vi.fn()
const requestRender = vi.fn()
const takeCheckpoint = vi.fn(async () => null)
// 面板有没有精确 manifest：用例按 id 控制
const exact = new Set<string>()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  quickLayoutPlan: (...a: unknown[]) => quickLayoutPlan(...a),
  fetchLayoutPreviewPng: (...a: unknown[]) => fetchLayoutPreviewPng(...a),
}))
vi.mock('@/store/renderScheduler', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/renderScheduler')>()),
  requestRender: (...a: unknown[]) => requestRender(...a),
}))
vi.mock('@/lib/timelineCheckpoint', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/timelineCheckpoint')>()),
  takeCheckpoint: (...a: unknown[]) => takeCheckpoint(...(a as [])),
}))
vi.mock('@/store/renderStore', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/store/renderStore')>()
  return {
    ...real,
    exactPanelManifest: (_s: unknown, p: PanelObject) => (exact.has(p.id) ? { stem: p.id, size_mm: [80, 60], elements: [] } : null),
  }
})

import { useDocumentStore } from './documentStore'
import { runUndoRedo } from '@/hooks/useKeyboard' // 加载它 = 把「撤销」交给一键排版的胶囊
import { runQuickLayout, runQuickLayoutRatios, useQuickLayoutStore } from './quickLayoutActions'

void runUndoRedo
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'
import { useAiStore } from './aiStore'
import { useChangePlanStore } from './changePlanStore'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const s = () => useDocumentStore.getState()

const panel = (id: string, partial: Partial<PanelObject> = {}): PanelObject =>
  ({
    id,
    type: 'panel',
    x: 10,
    y: 10,
    w: 80,
    h: 60,
    fileId: `f_${id}`,
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 60,
    script: 'fig.py',
    overrides: [{ gid: 'axes_0.title', prop: 'fontsize', value: 9 }],
    ...partial,
  }) as PanelObject

const seed = (...panels: PanelObject[]) => {
  s().commit(literal('放入'), (d) => {
    d.objects.push(...panels)
  })
  for (const p of panels) if (p.fileKind !== 'raster') exact.add(p.id)
}

const okPlan = (): QuickLayoutResponse => ({
  ok: true,
  panels: [
    { key: 'p1', patches: [{ gid: 'figure', prop: 'size_mm', value: [85, 60] }], box: [10, 10, 85, 60] },
    { key: 'p2', patches: [{ gid: 'figure', prop: 'size_mm', value: [85, 60] }], box: [98, 10, 85, 60] },
  ],
  labels: [
    { key: 'p1', x: 11, y: 11 },
    { key: 'p2', x: 99, y: 11 },
  ],
  notes: [],
  spec_changes: [],
  page: { w: 193, h: 80 },
  grid: { rows: 1, cols: 2, cells: { p1: [0, 0], p2: [0, 1] }, col_ratios: [1, 1], row_ratios: [1] },
  replay_required: false,
})

const statusText = () => formatMessage(useUiStore.getState().status)
const texts = () => s().doc.objects.filter((o): o is TextObject => o.type === 'text')

beforeEach(async () => {
  quickLayoutPlan.mockReset()
  fetchLayoutPreviewPng.mockReset().mockImplementation(async () => new Blob(['png'], { type: 'image/png' }))
  let n = 0
  URL.createObjectURL = vi.fn(() => `blob:preview-${++n}`)
  URL.revokeObjectURL = vi.fn()
  requestRender.mockReset()
  takeCheckpoint.mockClear()
  exact.clear()
  localStorage.clear()
  useAiStore.setState({ caps: { change_plan_enabled: true } as never })
  useChangePlanStore.getState().clear()
  useSelectionStore.getState().clear()
  useUiStore.getState().setStatus(null)
  await s().switchDocument(emptyProject(), 'd_quick_layout')
})


const entries = () => Object.values(useChangePlanStore.getState().entries)
const objectsJson = () => JSON.stringify(s().doc.objects) + JSON.stringify(s().doc.page)

describe('出卡：求解回来后文档一个字不动', () => {
  it('做成一张卡（一个 atomic 组「排版」，两张整版预览），弹出弹窗并切到助手页签；没有历史条目、没有检查点', async () => {
    seed(panel('p1'), panel('p2', { x: 120 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    const before = objectsJson()
    const depth = s().past.length
    expect(await runQuickLayout('double', 3)).toBe('planned')
    expect(objectsJson()).toBe(before)
    expect(s().past.length).toBe(depth)
    expect(takeCheckpoint).not.toHaveBeenCalled()
    const [e] = entries()
    expect(entries()).toHaveLength(1)
    expect(e).toMatchObject({ kind: 'layout', from: 'quick_layout', status: 'ready', panelId: '', beforeSrc: 'blob:preview-1', afterSrc: 'blob:preview-2' })
    expect(e.plan?.source).toBe('quick_layout')
    expect(e.plan?.groups).toHaveLength(1)
    expect(e.plan?.groups[0]).toMatchObject({ atomic: true, changes: [] })
    expect(e.plan?.groups[0].boxes.after.map((b) => b.gid)).toEqual(['p1', 'p2'])
    expect(useChangePlanStore.getState().dialogKey).toBe(e.key)
    expect(useUiStore.getState().rightTab).toBe('assistant')
    expect(useQuickLayoutStore.getState().busy).toBe(false)
    // 前后两份画布各出一次图；后一份是应用之后的页面（标签已加上），前一份是现在的
    expect(fetchLayoutPreviewPng).toHaveBeenCalledTimes(2)
    const [b, a] = fetchLayoutPreviewPng.mock.calls.map((c) => c[0] as { objects: { type: string }[] })
    expect(b.objects.filter((o) => o.type === 'text')).toHaveLength(0)
    expect(a.objects.filter((o) => o.type === 'text')).toHaveLength(2)
    // 求解让后端的 worker 停在候选上：推迟落地时先按此刻的列表放回
    expect(requestRender.mock.calls.map((c) => (c[0] as PanelObject).id).sort()).toEqual(['p1', 'p2'])
  })

  it('「应用」= 现有的那一次 commit：一条历史、检查点此刻才取、网格记忆一并写、⌘Z 一步回；卡转「已应用」', async () => {
    seed(panel('p1'), panel('p2', { x: 120 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    const before = objectsJson()
    await runQuickLayout('double', 3)
    const [e] = entries()
    const depth = s().past.length
    expect(useChangePlanStore.getState().apply(e.key)).toBe('applied')
    expect(s().past.length).toBe(depth + 1)
    expect(takeCheckpoint).toHaveBeenCalledOnce()
    expect(s().doc.quickLayout).toBeTruthy()
    expect(texts()).toHaveLength(2)
    expect(useChangePlanStore.getState().entries[e.key].status).toBe('applied')
    expect(useChangePlanStore.getState().dialogKey).toBeNull()
    expect(statusText()).toContain('排')
    s().undo()
    expect(objectsJson()).toBe(before)
  })

  it('应用前图被改过：不写、卡转「图已改过」，并说一句', async () => {
    seed(panel('p1'), panel('p2', { x: 120 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await runQuickLayout('double', 3)
    const [e] = entries()
    s().commit(literal('手改'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1')!
      p.x += 5
    })
    const depth = s().past.length
    expect(useChangePlanStore.getState().apply(e.key)).toBe('stale')
    expect(s().past.length).toBe(depth)
    expect(texts()).toHaveLength(0)
    expect(takeCheckpoint).not.toHaveBeenCalled()
    expect(useChangePlanStore.getState().entries[e.key].status).toBe('stale')
  })

  it('放弃：卡收掉、预览图地址放掉，文档不动', async () => {
    seed(panel('p1'), panel('p2', { x: 120 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await runQuickLayout('double', 3)
    const [e] = entries()
    useChangePlanStore.getState().discard(e.key)
    expect(entries()).toHaveLength(0)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview-1')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview-2')
    expect(texts()).toHaveLength(0)
  })

  it('「重新生成」（图已改过之后）= 按同样的选项再排一次，旧卡换成新卡', async () => {
    seed(panel('p1'), panel('p2', { x: 120 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await runQuickLayout('double', 3)
    const [first] = entries()
    await useChangePlanStore.getState().regenerate(first.key)
    await vi.waitFor(() => expect(entries()).toHaveLength(1))
    expect(entries()[0].key).not.toBe(first.key)
    expect(quickLayoutPlan).toHaveBeenCalledTimes(2)
  })
})

describe('预览出不来：不卡住', () => {
  it.each(['前一份', '后一份'])('%s出图失败：直接落地（一次 commit），一句灰字说明没有预览，没有卡', async (which) => {
    seed(panel('p1'), panel('p2', { x: 120 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    fetchLayoutPreviewPng.mockImplementation(async (c: { objects: { type: string }[] }) => {
      const hasLabels = c.objects.some((o) => o.type === 'text')
      if ((which === '前一份') !== hasLabels) throw new Error('渲染失败')
      return new Blob(['png'])
    })
    const depth = s().past.length
    expect(await runQuickLayout('double', 3)).toBe('applied')
    expect(entries()).toHaveLength(0)
    expect(s().past.length).toBe(depth + 1)
    expect(takeCheckpoint).toHaveBeenCalledOnce()
    expect(texts()).toHaveLength(2)
    expect(formatMessage(useUiStore.getState().statusSubline)).toContain('预览')
    expect(URL.createObjectURL).not.toHaveBeenCalled() // 两张都到了才建地址：半张不留
  })

  it('出图期间图被改过：不落地、不出卡（一句「图改过了」）', async () => {
    seed(panel('p1'), panel('p2', { x: 120 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    fetchLayoutPreviewPng.mockImplementation(async () => {
      s().commit(literal('手改'), (d) => {
        d.objects.find((o) => o.id === 'p1')!.x += 5
      })
      throw new Error('x')
    })
    expect(await runQuickLayout('double', 3)).toBe('stale')
    expect(entries()).toHaveLength(0)
    expect(texts()).toHaveLength(0)
  })
})

describe('不出卡的场合', () => {
  it('开关关着（ai.change_plan.enabled = false）：直接落地，没有预览请求', async () => {
    useAiStore.setState({ caps: { change_plan_enabled: false } as never })
    seed(panel('p1'), panel('p2', { x: 120 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    expect(await runQuickLayout('double', 3)).toBe('applied')
    expect(fetchLayoutPreviewPng).not.toHaveBeenCalled()
    expect(entries()).toHaveLength(0)
  })

  it('拖分隔线调比例：直接落地，不出卡', async () => {
    useAiStore.setState({ caps: { change_plan_enabled: false } as never })
    seed(panel('p1'), panel('p2', { x: 120 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await runQuickLayout('double', 3)
    useAiStore.setState({ caps: { change_plan_enabled: true } as never })
    quickLayoutPlan.mockResolvedValue(okPlan())
    const out = await runQuickLayoutRatios({ cols: [1, 2], rows: [1] })
    expect(['applied', 'stale']).toContain(out) // 记忆是否还有效不是这条要验的；要验的是不出卡
    expect(fetchLayoutPreviewPng).not.toHaveBeenCalled()
    expect(entries()).toHaveLength(0)
  })

  it('求解没过（太少 / 失败）：和以前一样一句话，没有卡', async () => {
    seed(panel('p1'), panel('p2', { x: 120 }))
    quickLayoutPlan.mockResolvedValue({ ok: false, code: 'not_converged' })
    expect(await runQuickLayout('double', 3)).toBe('failed')
    expect(entries()).toHaveLength(0)
  })
})
