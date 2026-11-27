/**
 * 改图助手改画布矢量对象的方案卡（`kind: 'vector'`，ADR 0212 第 6 期）。
 *
 * 判据的主语：
 * * 「一次提交」量的是**文档历史栈**在「应用」前后恰好多一条（`past.length + 1`），不是 commit 被调用了几次；
 * * 「⌘Z 一步撤回」量的是撤销之后画布上的对象与出发时逐字相同（`JSON.stringify` 相等）、卡片又是待确认；
 * * 「基线变了」量的是方案点名的对象在应用前被用户改过，应用后文档**一个字没动**；
 * * 「快照不出本机的东西」量的是**真发出去的请求体**里没有子图的脚本 / 数据文件名 / 覆盖项。
 * 后端用桩（只给 `/api/vector-edit/plan` 的回包）：后端的补丁算法有 `tests/test_vector_edit_plan.py` 看护，这里不重测。
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { VectorEditResult } from '@/lib/api'
import { EngineError } from '@/lib/api'
import { useChangePlanStore } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { emptyProject, type CanvasObject } from '@/types/document'

const createPlan = vi.fn()
const getPlan = vi.fn()
const previewPng = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createVectorEditPlan: (...a: unknown[]) => createPlan(...a),
  getVectorEditPlan: (...a: unknown[]) => getPlan(...a),
  fetchLayoutPreviewPng: (...a: unknown[]) => previewPng(...a),
}))
// 导出管线对最小的测试对象不挑：这里只要一个确定的分段，不测它
vi.mock('@/lib/exportRequest', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/exportRequest')>()),
  canvasSegmentOf: (doc: { page: { w: number; h: number }; objects: unknown[] }) => ({ page_w_mm: doc.page.w, page_h_mm: doc.page.h, objects: doc.objects }),
}))
vi.mock('@/hooks/useVariantPng', () => ({ useVariantPng: () => ({ url: null, loading: false, approximate: false }), variantNeedsRender: () => false }))
vi.mock('@/hooks/useRuntimePreview', () => ({ useRuntimePreview: () => ({ url: null, exact: false }) }))
const connectAi = vi.fn()
vi.mock('@/store/connectAiStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/connectAiStore')>()),
  requestConnectAi: (...a: unknown[]) => connectAi(...a),
}))
// 助手探测到的能力：方案卡开关开着（默认）
import { useAiStore } from '@/store/aiStore'
import { runVectorEdit } from '@/store/vectorPlanActions'
import { VECTOR_TYPES, baselineHolds, diffOf, snapshotOf, vectorSelectionOf } from '@/lib/vectorEdit'
import { TooltipProvider } from '../ui/Tooltip'
import { ChangePlanCard } from './ChangePlanCard'
import { ChangePlanDialog } from './ChangePlanDialog'

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
globalThis.URL.createObjectURL ??= () => 'blob:test'
globalThis.URL.revokeObjectURL ??= () => undefined

const obj = (o: Record<string, unknown>) => o as unknown as CanvasObject
const T1 = obj({ id: 't1', type: 'text', x: 10, y: 10, w: 20, h: 6, text: 'A', sizePt: 8, name: '标题' })
const A1 = obj({ id: 'a1', type: 'arrow', x: 30, y: 30, w: 20, h: 0, strokePt: 0.5, color: '#cc0000' })
const S1 = obj({ id: 's1', type: 'shape', x: 5, y: 40, w: 10, h: 10, strokePt: 1 })
const PANEL = obj({ id: 'p1', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'secret_file.py', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: '/Users/someone/fig.py', overrides: [{ gid: 'ax', prop: 'fontsize', value: 9 }] })
const PATHTEXT = obj({ id: 'pt1', type: 'pathtext', x: 0, y: 0, w: 5, h: 5, text: '不该发出去' })

/** 后端回的方案：改两个、删一个，三条复述 + 一条没能照做的 */
const RESULT: VectorEditResult = {
  ok: true,
  errors: [],
  objects: [obj({ ...T1, sizePt: 12 }), obj({ ...A1, strokePt: 0.75 })],
  deleted: ['s1'],
  order: ['p1', 't1', 'a1'],
  reordered: false,
  echo: ['标题文字：字号 8 → 12 pt', '红箭头：线宽 0.5 → 0.75 pt', '方框：删除'],
  changes: [
    { id: 't1', field: 'sizePt', before: 8, after: 12 },
    { id: 'a1', field: 'strokePt', before: 0.5, after: 0.75 },
    { id: 'a1', field: 'color', before: '#cc0000', after: '#00aa00' },
    { id: 'a1', field: 'x', before: 30, after: 31 },
    { id: 'a1', field: 'w', before: 20, after: 21 },
    { id: 's1', deleted: true },
  ],
  checks: [],
  layout_groups: null,
  ops: [],
  unhandled: [{ reason: '改图里曲线的颜色要改脚本' }],
}

let host: HTMLDivElement
let root: Root
/** 卡片跟着 store 里的条目走（真界面也是这样订阅的） */
function Live({ k }: { k: string }) {
  const e = useChangePlanStore((st) => st.entries[k])
  return e ? <ChangePlanCard entry={e} /> : null
}
const render = (el: React.ReactNode) => act(() => root.render(<TooltipProvider>{el}</TooltipProvider>))
const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)
const qa = (sel: string) => [...document.body.querySelectorAll<HTMLElement>(sel)]
const doc = () => useDocumentStore.getState()
const ids = () => doc().doc.objects.map((o) => o.id)
const entryOf = (key: string) => useChangePlanStore.getState().entries[key]

/** 发起一次并推进到方案出卡 */
async function runToReady(result: VectorEditResult = RESULT): Promise<string> {
  getPlan.mockResolvedValue({ plan_id: 'vp1', state: 'ready', plan: result })
  const key = runVectorEdit('把标题放大、箭头加粗、删掉方框', ['t1', 'a1'])!
  expect(key).toBeTruthy()
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1500)
  })
  return key
}

beforeEach(async () => {
  vi.useFakeTimers()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  createPlan.mockReset().mockResolvedValue({ plan_id: 'vp1', state: 'running' })
  getPlan.mockReset()
  previewPng.mockReset().mockResolvedValue(new Blob(['x'], { type: 'image/png' }))
  connectAi.mockReset()
  useChangePlanStore.setState({ entries: {}, order: [], dialogKey: null })
  useAiStore.setState({ caps: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_vector_plan')
  useDocumentStore.getState().commit(literal('放入'), (d) => {
    d.objects.push(structuredClone(PANEL), structuredClone(T1), structuredClone(A1), structuredClone(S1), structuredClone(PATHTEXT))
  })
  useSelectionStore.getState().set(['t1', 'a1'])
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.useRealTimers()
})

it('选区判据：只认文字 / 箭头 / 形状 / 路径，子图与别的类型不算', () => {
  expect(vectorSelectionOf(doc().doc.objects, ['p1', 't1', 'pt1', 'zz', 's1'])).toEqual(['t1', 's1'])
  expect(vectorSelectionOf(doc().doc.objects, ['p1'])).toEqual([])
})

it('可编辑的类型与后端 `vectoredit.EDITABLE_TYPES` 同一份（严格同源对：改一侧要改另一侧）', () => {
  const py = readFileSync(path.resolve(__dirname, '../../../../src/tavotto/engine/vectoredit.py'), 'utf-8')
  const m = /^EDITABLE_TYPES = \(([^)]*)\)/m.exec(py)
  expect(m).not.toBeNull()
  const backend = [...m![1].matchAll(/"([a-z]+)"/g)].map((x) => x[1])
  expect([...VECTOR_TYPES]).toEqual(backend)
})

it('快照：矢量对象原样、子图只留外框；脚本、文件名、覆盖项与别的类型都不发', () => {
  const snap = snapshotOf(doc().doc, 'c1')
  const text = JSON.stringify(snap.canvas)
  expect(snap.canvas.objects.map((o) => o.id)).toEqual(['p1', 't1', 'a1', 's1'])
  for (const secret of ['secret_file.py', '/Users/someone', 'fontsize', '不该发出去']) expect(text).not.toContain(secret)
  const panel = snap.canvas.objects[0] as unknown as Record<string, unknown>
  expect(Object.keys(panel).sort()).toEqual(['h', 'id', 'type', 'w', 'x', 'y'])
})

it('差异：快照里没有的是新增、有的是改、删的取快照原样；布局组没变就不带', () => {
  const snap = snapshotOf(doc().doc, 'c1')
  const d = diffOf(snap, { ...RESULT, objects: [...RESULT.objects!, obj({ id: 'new1', type: 'path', x: 1, y: 1, w: 1, h: 1 })] })
  expect(d.added.map((o) => o.id)).toEqual(['new1'])
  expect(d.changed.map((c) => c.after.id)).toEqual(['t1', 'a1'])
  expect(d.deleted.map((o) => o.id)).toEqual(['s1'])
  expect(d.layout_groups).toBeUndefined()
})

it('基线：点名的对象和出发时不同才算变；没点名的对象变了不碍事', () => {
  const snap = snapshotOf(doc().doc, 'c1')
  expect(baselineHolds(snap, RESULT, doc().doc.objects)).toBe(true)
  const other = doc().doc.objects.map((o) => (o.id === 'p1' ? ({ ...o, x: 99 } as CanvasObject) : o))
  expect(baselineHolds(snap, RESULT, other)).toBe(true) // 子图没被点名
  const named = doc().doc.objects.map((o) => (o.id === 't1' ? ({ ...o, text: 'B' } as CanvasObject) : o))
  expect(baselineHolds(snap, RESULT, named)).toBe(false)
})

it('发请求：带内存快照与选区；生成中卡片在对话里，没有可点的按钮', async () => {
  const key = runVectorEdit('把标题放大', ['t1'])!
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10)
  })
  const body = createPlan.mock.calls[0][0] as { canvas: { objects: { id: string }[] }; selection: string[]; instruction: string; client_request_id: string }
  expect(body.selection).toEqual(['t1'])
  expect(body.instruction).toBe('把标题放大')
  expect(body.client_request_id).toBe(key)
  expect(body.canvas.objects.map((o) => o.id)).toEqual(['p1', 't1', 'a1', 's1'])
  render(<Live k={key} />)
  expect(q('[data-change-plan-card]')!.dataset.changePlanCard).toBe('generating')
  expect(q('[data-change-plan-generating]')!.textContent).toContain('正在改选中的对象')
  // 一次一份：这张画布上已有一份在生成，再发不出第二份
  expect(runVectorEdit('再来一份', ['t1'])).toBeNull()
})

it('出卡：一句话说清改了几个对象、另有几条没改；清单逐条是后端的复述，没能照做的一句灰字', async () => {
  const key = await runToReady()
  expect(entryOf(key).status).toBe('ready')
  render(
    <>
      <Live k={key} />
      <ChangePlanDialog />
    </>,
  )
  const card = q('[data-change-plan-card]')!
  expect(card.dataset.changePlanCard).toBe('ready')
  expect(card.textContent).toContain('改了 3 个对象，另有 1 条没改')
  act(() => useChangePlanStore.getState().openDialog(key))
  const rows = qa('[data-change-group]')
  // 按对象分组、翻成界面语言：对象叫法 = 类型 +「名字」，字段叫属性面板的名字，数值带单位，不出现 id 与内部键名，也不是后端的复述原文
  expect(rows.map((r) => r.querySelector('span.text-sm')!.textContent)).toEqual(['文字「标题」', '箭头', '删除形状'])
  const lines = (i: number) => [...rows[i].querySelectorAll('[data-change-line]')].map((l) => l.textContent)
  expect(lines(0)).toEqual(['字号8 pt→12 pt'])
    expect(lines(1)).toContain('线宽0.5 pt→0.75 pt')
  expect(lines(1)).toContain('位置和大小')
  expect(rows[1].querySelectorAll('span[style*="background-color"]')).toHaveLength(2) // 颜色带色块
  const all = rows.map((r) => r.textContent).join('')
  for (const raw of ['strokePt', 'sizePt', 'a1', 't1', 's1']) expect(all).not.toContain(raw)
  expect(rows[2].title).toContain('方框')
  // 整批要或不要：清单里没有勾选框
  expect(q('[data-change-plan-list] input[type="checkbox"]')).toBeNull()
  const un = q('[data-change-plan-unhandled]')!
  expect(un.textContent).toContain('改图里曲线的颜色要改脚本')
})

it('老后端没有结构化改动：清单退回显示复述原文', async () => {
  const key = await runToReady({ ...RESULT, changes: [] })
  render(
    <>
      <Live k={key} />
      <ChangePlanDialog />
    </>,
  )
  act(() => useChangePlanStore.getState().openDialog(key))
  expect(qa('[data-change-group]').map((r) => r.textContent)).toEqual(RESULT.echo)
})

it('应用 = 一次提交，⌘Z 一步撤回；撤回后卡片又是待确认', async () => {
  const key = await runToReady()
  render(<Live k={key} />)
  const before = JSON.stringify(doc().doc.objects)
  const pastBefore = doc().past.length
  act(() => void useChangePlanStore.getState().apply(key))
  expect(doc().past.length).toBe(pastBefore + 1)
  expect(ids()).toEqual(['p1', 't1', 'a1', 'pt1']) // s1 删了，不在的类型原样留着
  expect((doc().doc.objects.find((o) => o.id === 't1') as unknown as { sizePt: number }).sizePt).toBe(12)
  expect((doc().doc.objects.find((o) => o.id === 'a1') as unknown as { strokePt: number }).strokePt).toBe(0.75)
  expect(q('[data-change-plan-card]')!.dataset.changePlanCard).toBe('applied')
  expect(q('[data-change-plan-card]')!.textContent).toContain('已应用 · 改了 3 个对象')
  act(() => doc().undo())
  expect(JSON.stringify(doc().doc.objects)).toBe(before)
  expect(q('[data-change-plan-card]')!.dataset.changePlanCard).toBe('ready')
})

it('应用之后别的写入把那条历史换成合并后的新条目（文字量好尺寸的回写）：卡片仍是「已应用」，撤销仍一步回到出发时', async () => {
  const key = await runToReady()
  render(<Live k={key} />)
  const before = JSON.stringify(doc().doc.objects)
  act(() => void useChangePlanStore.getState().apply(key))
  // 与 `measuredWriteback` 同一做法：`amendLast` 把改动并进栈顶那一条，条目换成新对象
  const top = doc().past.at(-1)!
  act(() => {
    doc().amendLast(top, (d) => {
      ;(d.objects.find((o) => o.id === 't1') as unknown as { w: number }).w = 25
    })
  })
  expect(doc().past.at(-1)).not.toBe(top)
  expect(q('[data-change-plan-card]')!.dataset.changePlanCard).toBe('applied')
  act(() => doc().undo())
  expect(JSON.stringify(doc().doc.objects)).toBe(before)
  expect(q('[data-change-plan-card]')!.dataset.changePlanCard).toBe('ready')
})

it('应用前点名的对象被改过：卡片是「图改过了」，应用不写任何东西', async () => {
  const key = await runToReady()
  act(() =>
    doc().commit(literal('改字'), (d) => {
      ;(d.objects.find((o) => o.id === 't1') as unknown as { text: string }).text = '别的'
    }),
  )
  render(<Live k={key} />)
  expect(q('[data-change-plan-card]')!.dataset.changePlanCard).toBe('stale')
  const snapshot = JSON.stringify(doc().doc.objects)
  const past = doc().past.length
  let out = ''
  act(() => {
    out = useChangePlanStore.getState().apply(key)
  })
  expect(out).toBe('stale')
  expect(JSON.stringify(doc().doc.objects)).toBe(snapshot)
  expect(doc().past.length).toBe(past)
})

it('应用后被删掉的对象不再留在选区里', async () => {
  const key = await runToReady({ ...RESULT, deleted: ['s1'] })
  useSelectionStore.getState().set(['t1', 's1'])
  act(() => void useChangePlanStore.getState().apply(key))
  expect(useSelectionStore.getState().ids).toEqual(['t1'])
})

it('手势进行中不应用：commit 会并进那次手势的事务', async () => {
  const key = await runToReady()
  act(() => doc().beginTxn(literal('拖')))
  const before = JSON.stringify(doc().doc.objects)
  let out = ''
  act(() => {
    out = useChangePlanStore.getState().apply(key)
  })
  expect(out).toBe('failed')
  expect(JSON.stringify(doc().doc.objects)).toBe(before)
  act(() => doc().endTxn())
})

it('几何内核没装好：一条都做不了时卡上一句人话，图没有改动', async () => {
  const key = await runToReady({
    ok: true,
    errors: [],
    objects: [],
    deleted: [],
    echo: [],
    unhandled: [{ reason: '第 1 条（pathfinder）：这台机器上的几何内核没装上……', code: 'kernel_unavailable' }],
  })
  render(<Live k={key} />)
  const card = q('[data-change-plan-card]')!
  expect(card.dataset.changePlanCard).toBe('failed')
  expect(card.dataset.changePlanCode).toBe('vector_kernel_unavailable')
  expect(card.textContent).toContain('缺一个组件')
  expect(card.textContent).not.toContain('这台机器上的几何内核没装上') // 后端那句是写给模型的，不上界面
})

it('模型什么都改不了：空方案卡，没能照做的各一行，码翻成人话', async () => {
  const key = await runToReady({
    ok: true,
    errors: [],
    objects: [],
    deleted: [],
    echo: [],
    unhandled: [{ reason: '第 1 条（set）：点名的对象 / 组不在这张画布上', code: 'unknown_object' }, { reason: '这是曲线的颜色' }],
  })
  render(<Live k={key} />)
  const card = q('[data-change-plan-card]')!
  expect(card.dataset.changePlanCode).toBe('change_plan_empty')
  expect(qa('[data-change-plan-unhandled]').map((x) => x.textContent)).toEqual([expect.stringContaining('找不到对应的对象'), expect.stringContaining('这是曲线的颜色')])
  // 矢量编辑没有「改脚本」的后路
  expect(q('[data-change-plan-run-script]')).toBeNull()
})

it('核对没过的方案仍可应用，卡上一句话告诉用户', async () => {
  const key = await runToReady({ ...RESULT, ok: false, errors: [{ code: 'checks_failed' }], checks: [{ check: 'page', status: 'fail', item: 't1' }] })
  render(<Live k={key} />)
  expect(q('[data-change-plan-card]')!.textContent).toContain('1 处核对没过')
  expect(q('[data-change-plan-apply]')).not.toBeNull()
})

it('请求被拒（没连 AI）：失败卡，给「连接 AI」，且重试会重新发一份', async () => {
  createPlan.mockRejectedValueOnce(new EngineError('x', '', 'change_plan_no_connection', ''))
  const key = runVectorEdit('放大', ['t1'])!
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10)
  })
  render(<Live k={key} />)
  const btn = q('[data-change-plan-failed-action]')!
  expect(btn.dataset.changePlanFailedAction).toBe('connect')
  expect(q('[data-change-plan-run-script]')).toBeNull()
  // 失败卡的重来：按此刻的快照再发一份（同一选区）
  act(() => void useChangePlanStore.getState().regenerate(key))
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10)
  })
  expect(createPlan).toHaveBeenCalledTimes(2)
  expect((createPlan.mock.calls[1][0] as { selection: string[] }).selection).toEqual(['t1'])
})

it('对比图出不来：失败卡 + 重试，不让用户没看过就应用', async () => {
  previewPng.mockRejectedValue(new EngineError('x', '', 'change_plan_render_failed', ''))
  const key = await runToReady()
  render(<Live k={key} />)
  expect(q('[data-change-plan-card]')!.dataset.changePlanCode).toBe('change_plan_render_failed')
  expect(q('[data-change-plan-apply]')).toBeNull()
})

it('对比图取景：只画被改动的那一块（加留白），前后同一个框；改动占大半页就用整页', async () => {
  const { previewFrame, cropToFrame } = await import('@/lib/vectorEdit')
  const page = { w: 180, h: 100 }
  const before = doc().doc.objects
  const f = previewFrame(page, before, RESULT.objects!, RESULT)!
  expect(f).not.toBeNull()
  expect(f.x).toBeGreaterThanOrEqual(0)
  expect(f.w).toBeLessThan(page.w)
  const c = cropToFrame({ page, objects: before }, f)
  expect(c.page).toEqual({ w: f.w, h: f.h })
  expect(c.objects.find((o) => o.id === 't1')!.x).toBeCloseTo(10 - f.x)
  expect(previewFrame({ w: 30, h: 30 }, before, RESULT.objects!, RESULT)).toBeNull()
})
