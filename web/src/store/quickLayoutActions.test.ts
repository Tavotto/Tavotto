/**
 * 一键排版的落地（ADR 0199）：收集面板 / 固定盒子、一次 commit、标签沿用 addSubLabels 的约定、
 * 等待期间文档变了就丢弃、失败不改任何东西。后端求解不在这里（`tests/test_quicklayout*.py`），这里只桩 `quickLayoutPlan`。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { formatMessage } from '@/i18n'
import type { QuickLayoutResponse } from '@/lib/api'
import { emptyProject, type PanelObject, type TextObject } from '@/types/document'

const quickLayoutPlan = vi.fn()
const requestRender = vi.fn()
const takeCheckpoint = vi.fn(async () => null)
// 面板有没有精确 manifest：用例按 id 控制
const exact = new Set<string>()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  quickLayoutPlan: (...a: unknown[]) => quickLayoutPlan(...a),
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

import { addSubLabels, makeSubLabel } from './actions'
import { useDocumentStore } from './documentStore'
import { useRenderStore } from './renderStore'
import { runUndoRedo } from '@/hooks/useKeyboard' // 加载它 = 把「撤销」交给一键排版的胶囊
import { runQuickLayout, runQuickLayoutRatios, useQuickLayoutStore } from './quickLayoutActions'
import { setPageSize, startLayoutAutoReflow } from './actions'
import { currentSpec } from './issueFixActions'

void runUndoRedo
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'

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
const lastRequest = () => quickLayoutPlan.mock.calls.at(-1)![0]

beforeEach(async () => {
  quickLayoutPlan.mockReset()
  requestRender.mockReset()
  takeCheckpoint.mockClear()
  exact.clear()
  localStorage.clear()
  useSelectionStore.getState().clear()
  useUiStore.getState().setStatus(null)
  await s().switchDocument(emptyProject(), 'd_quick_layout')
})

describe('收集', () => {
  it('没选中 = 当前画布全部面板；有图内编辑入口（script）的进 panels，位图 / 旋转 / 翻转 / 没脚本的进 fixed', async () => {
    seed(
      panel('p1'),
      panel('p2'),
      panel('p3', { fileKind: 'raster' }),
      panel('p4', { rotation: 90 }),
      panel('p5', { flipH: true }),
      panel('p6', { script: undefined }),
    )
    exact.add('p3') // 位图即使有 manifest 也只当固定盒子
    quickLayoutPlan.mockResolvedValue({ ok: false, code: 'not_converged' })
    await runQuickLayout('double', 3)
    const body = lastRequest()
    expect(body.panels.map((p: { key: string }) => p.key)).toEqual(['p1', 'p2'])
    expect(body.fixed.map((p: { key: string }) => p.key).sort()).toEqual(['p3', 'p4', 'p5', 'p6'])
    expect(body.panels[0]).toMatchObject({
      key: 'p1',
      id: 'f_p1',
      box: [10, 10, 80, 60],
      patches: [{ gid: 'axes_0.title', prop: 'fontsize', value: 9 }],
    })
    expect(body).toMatchObject({ target: 'double', gap_mm: 3, label_pt: 10 })
    expect(body.page).toMatchObject({ w: s().doc.page.w, h: s().doc.page.h })
    expect(body.profile).toBeTruthy()
  })

  it('没有多选时画布上有锁定的图：按固定处理，不进 panels、位置 / 尺寸 / overrides / 标签都不动、仍锁定', async () => {
    seed(panel('p1'), panel('p2'), panel('p3', { x: 200, locked: true }))
    const before = JSON.stringify(s().doc.objects.find((o) => o.id === 'p3'))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await runQuickLayout('double', 3)
    const body = lastRequest()
    expect(body.panels.map((p: { key: string }) => p.key)).toEqual(['p1', 'p2'])
    expect(body.fixed.map((p: { key: string }) => p.key)).toEqual(['p3'])
    expect(JSON.stringify(s().doc.objects.find((o) => o.id === 'p3'))).toBe(before)
    expect((s().doc.objects.find((o) => o.id === 'p3') as PanelObject).locked).toBe(true)
  })

  it('选中 ≥ 2 张就只排选中的；只选了 1 张还是排全部', async () => {
    seed(panel('p1'), panel('p2'), panel('p3'))
    quickLayoutPlan.mockResolvedValue({ ok: false, code: 'not_converged' })
    useSelectionStore.getState().set(['p1', 'p3'])
    await runQuickLayout('single', 2)
    expect(lastRequest().panels.map((p: { key: string }) => p.key)).toEqual(['p1', 'p3'])
    useSelectionStore.getState().set(['p2'])
    await runQuickLayout('single', 2)
    expect(lastRequest().panels.map((p: { key: string }) => p.key)).toEqual(['p1', 'p2', 'p3'])
  })

  it('不足两张可排的图：不发请求，胶囊说「至少要两张图」', async () => {
    seed(panel('p1'), panel('p2', { fileKind: 'raster' }))
    expect(await runQuickLayout('double', 3)).toBe('too_few')
    expect(quickLayoutPlan).not.toHaveBeenCalled()
    expect(statusText()).toBe('至少要两张图')
    expect(useUiStore.getState().statusTone).toBe('info')
  })
})

describe('应用', () => {
  it('一次 commit = 一条历史；覆盖整张换上、摆放落到 box、标签按返回顺序 (a)(b)', async () => {
    // 原生图幅已经是 85×60（没有「渲染回来再乘一遍」的换算），w / h 直接 = box
    seed(panel('p1', { nativeW: 85 }), panel('p2', { nativeW: 85 }))
    const depth = s().past.length
    quickLayoutPlan.mockResolvedValue(okPlan())
    expect(await runQuickLayout('double', 3)).toBe('applied')
    expect(s().past.length).toBe(depth + 1)
    const [p1, p2] = ['p1', 'p2'].map((id) => s().doc.objects.find((o) => o.id === id) as PanelObject)
    expect(p1.overrides).toEqual([{ gid: 'figure', prop: 'size_mm', value: [85, 60] }])
    expect([p1.x, p1.y, p1.w, p1.h]).toEqual([10, 10, 85, 60])
    expect([p2.x, p2.y]).toEqual([98, 10])
    const labels = texts()
    expect(labels.map((l) => l.text)).toEqual(['(a)', '(b)'])
    expect(labels.map((l) => [l.x, l.y])).toEqual([[11, 11], [99, 11]])
    expect(labels.every((l) => l.bold && l.w === 10 && l.h === 5)).toBe(true)
    // 页面跟着内容收紧，在同一条历史里
    expect([s().doc.page.w, s().doc.page.h]).toEqual([193, 80])
    // 每张返回的图都重渲染
    expect(requestRender.mock.calls.map((c) => (c[0] as PanelObject).id).sort()).toEqual(['p1', 'p2'])
    expect(takeCheckpoint).toHaveBeenCalledTimes(1)
    expect(statusText()).toBe('已排好')
    expect(useUiStore.getState().statusAction).toBeTruthy()
  })

  it('刚放上、还没出过图的图：先等它出第一张图（画布样式随之落上）再发请求', async () => {
    seed(panel('p1'), panel('p2'))
    exact.delete('p2')
    quickLayoutPlan.mockResolvedValue(okPlan())
    const run = runQuickLayout('double', 3)
    await Promise.resolve()
    expect(requestRender.mock.calls.map((c) => (c[0] as PanelObject).id)).toEqual(['p2'])
    expect(quickLayoutPlan).not.toHaveBeenCalled()
    exact.add('p2')
    useRenderStore.setState({ byKey: { ...useRenderStore.getState().byKey } })
    expect(await run).toBe('applied')
    expect(quickLayoutPlan).toHaveBeenCalledTimes(1)
  })

  it('再点一次不叠标签：参与排版的图原来范围里的旧子图标签换成新的，远处的文字不动', async () => {
    seed(panel('p1'), panel('p2', { x: 100 }))
    s().commit(literal('旧标签'), (d) => {
      d.objects.push(makeSubLabel(0, 11.5, 11), makeSubLabel(1, 101.5, 11))
      d.objects.push({ ...makeSubLabel(2, 300, 300) }, { ...makeSubLabel(0, 12, 40), text: 'note' })
    })
    quickLayoutPlan.mockResolvedValue(okPlan())
    expect(await runQuickLayout('double', 3)).toBe('applied')
    const all = texts().map((l) => [l.text, l.x, l.y])
    expect(all).toEqual([
      ['(c)', 300, 300],
      ['note', 12, 40],
      ['(a)', 11, 11],
      ['(b)', 99, 11],
    ])
  })

  it('参与排版的图原先在布局组里：排版后自动重排不会盖掉方案，撤销一次分组与位置一起回去', async () => {
    seed(panel('p1', { groupId: 'g1', nativeW: 85 }), panel('p2', { x: 10, y: 74, groupId: 'g1', nativeW: 85 }))
    s().commit(literal('布局组'), (d) => {
      d.layoutGroups = [{ id: 'g1', kind: 'col', order: ['p1', 'p2'], gap: 4, align: 'start', uniform: null }]
    })
    const snap = () => JSON.stringify([s().doc.objects, s().doc.layoutGroups ?? null])
    const before = snap()
    vi.useFakeTimers()
    const stop = startLayoutAutoReflow()
    try {
      quickLayoutPlan.mockResolvedValue(okPlan())
      expect(await runQuickLayout('double', 3)).toBe('applied')
      vi.advanceTimersByTime(500)
      const p2 = s().doc.objects.find((o) => o.id === 'p2') as PanelObject
      expect([p2.x, p2.y]).toEqual([98, 10])
      expect(s().doc.layoutGroups ?? []).toEqual([])
      expect(s().doc.objects.every((o) => !o.groupId)).toBe(true)
      s().undo()
      expect(snap()).toBe(before)
    } finally {
      stop()
      vi.useRealTimers()
    }
  })

  it('左侧半宽裁剪的两张图连续排两次：标签不叠，只有一个 (a)、一个 (b)', async () => {
    const crop = { x: 0.5, y: 0, w: 0.5, h: 1 }
    seed(panel('p1', { x: 50, w: 40, crop }), panel('p2', { x: 140, w: 40, crop }))
    quickLayoutPlan.mockResolvedValue({
      ...okPlan(),
      panels: [
        { key: 'p1', patches: [], box: [10, 10, 80, 60] },
        { key: 'p2', patches: [], box: [98, 10, 80, 60] },
      ],
    })
    expect(await runQuickLayout('double', 3)).toBe('applied')
    expect(await runQuickLayout('double', 3)).toBe('applied')
    expect(texts().map((l) => l.text)).toEqual(['(a)', '(b)'])
  })

  it('混选里有固定的图（位图）：只换「真拿到新标签」的图的旧标签，位图自己的 (c) 还在', async () => {
    seed(panel('p1'), panel('p2', { x: 100 }), panel('p3', { fileKind: 'raster', x: 10, y: 100 }))
    s().commit(literal('旧标签'), (d) => {
      d.objects.push(makeSubLabel(2, 11, 101))
    })
    quickLayoutPlan.mockResolvedValue(okPlan())
    expect(await runQuickLayout('double', 3)).toBe('applied')
    expect(texts().map((l) => l.text)).toEqual(['(c)', '(a)', '(b)'])
  })

  it('只选了画布上 4 张里的 2 张、页面收紧：没参与的图原来在页面里的，排完仍在页面里', async () => {
    seed(panel('p1'), panel('p2', { x: 100 }), panel('p3', { x: 300, y: 200 }), panel('p4', { x: 200, y: 260 }))
    useSelectionStore.getState().set(['p1', 'p2'])
    quickLayoutPlan.mockResolvedValue(okPlan())
    expect(await runQuickLayout('double', 3)).toBe('applied')
    const { w, h } = s().doc.page
    for (const o of s().doc.objects) {
      if (o.type !== 'panel' || ['p1', 'p2'].includes(o.id)) continue
      expect(o.x + o.w).toBeLessThanOrEqual(w)
      expect(o.y + o.h).toBeLessThanOrEqual(h)
    }
  })

  it('图幅变了：页面上的框按「渲染回来会再乘一遍」的比例预除，渲染后正好是 box', async () => {
    seed(panel('p1'), panel('p2', { nativeW: 40, nativeH: 30 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await runQuickLayout('double', 3)
    const p2 = s().doc.objects.find((o) => o.id === 'p2') as PanelObject
    // 原生 40×30 → 图幅 85×60：渲染回来 w *= 85/40，所以此刻存 85*40/85 = 40
    expect([p2.w, p2.h]).toEqual([40, 30])
  })

  it('被裁剪的图：规划发整图框，落位的可见框 = 整图框 × crop，显示比例与后端校验的一致；没裁剪的不变', async () => {
    seed(panel('p1', { x: 10, y: 10, w: 40, h: 60, crop: { x: 0.25, y: 0, w: 0.5, h: 1 } }), panel('p2'))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await runQuickLayout('double', 3)
    // 规划：整图框（可见框 40 宽是半幅 → 整图 80 宽，原点往左退 0.25 × 80）
    expect(lastRequest().panels[0].box).toEqual([-10, 10, 80, 60])
    const p1 = s().doc.objects.find((o) => o.id === 'p1') as PanelObject
    const p2 = s().doc.objects.find((o) => o.id === 'p2') as PanelObject
    // 求解整图 85×60 @ (10,10)：可见宽 = 85 × 0.5，位置 = 原点 + crop.x × 整图宽，crop 不动
    expect(p1.crop).toEqual({ x: 0.25, y: 0, w: 0.5, h: 1 })
    expect(p1.x).toBeCloseTo(10 + 0.25 * 85)
    expect(p1.y).toBeCloseTo(10)
    expect(p1.h).toBeCloseTo(60)
    // 渲染回来 native 同步再乘 85 / 80：可见宽正好是 42.5
    const synced = (p1.w * 85) / p1.nativeW
    expect(synced).toBeCloseTo(85 * 0.5)
    // 显示比例（可见框 / crop.w）= 求解整图宽 / 原生宽
    expect(synced / (p1.crop?.w ?? 1) / p1.nativeW).toBeCloseTo(85 / 80)
    // 没裁剪的图：框 = 求解 box
    expect([p2.x, p2.y, p2.w, p2.h]).toEqual([98, 10, (85 * 80) / 85, 60])
    // 网格记忆按页面上的框记（quickLayoutValid 拿它跟图比）
    expect(s().doc.quickLayout?.boxes.p1[2]).toBeCloseTo(42.5)
  })

  it('备注合成一行灰字，进同一枚胶囊', async () => {
    seed(panel('p1'), panel('p2'))
    quickLayoutPlan.mockResolvedValue({
      ...okPlan(),
      notes: [
        { code: 'equal_aspect', ref: 'p2' },
        { code: 'spec_not_fixed', ref: 'p1' },
      ],
    })
    await runQuickLayout('double', 3)
    const sub = formatMessage(useUiStore.getState().statusSubline)
    expect(sub).toBe('图 b 是等比例坐标，没参与轴线对齐；图 a 还有几处字号没修到规范，可在「问题」里看')
  })

  it('规范改动：排在备注前，类别按固定顺序、用「、」连、带规范名', async () => {
    seed(panel('p1'), panel('p2'))
    quickLayoutPlan.mockResolvedValue({
      ...okPlan(),
      spec_changes: ['other', 'font_size', 'font_family', 'legend_frame'],
      notes: [{ code: 'equal_aspect', ref: 'p2' }],
    })
    await runQuickLayout('double', 3)
    const sub = formatMessage(useUiStore.getState().statusSubline)
    const label = currentSpec().label
    expect(label).toBeTruthy()
    expect(sub).toBe(`已按「${label}」改了字体、字号、图例边框、其他样式；图 b 是等比例坐标，没参与轴线对齐`)
  })

  it('spec_changes 为空：没有规范那一项', async () => {
    seed(panel('p1'), panel('p2'))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await runQuickLayout('double', 3)
    expect(useUiStore.getState().statusSubline ?? null).toBeNull()
  })

  it('请求带上偏好里的 fix_spec（默认 true，关了是 false）', async () => {
    seed(panel('p1'), panel('p2'))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await runQuickLayout('double', 3)
    expect(lastRequest().fix_spec).toBe(true)
    localStorage.setItem('tavotto.quickLayout', JSON.stringify({ target: 'double', gapMm: 3, fixSpec: false }))
    useUiStore.getState().setStatus(null)
    await runQuickLayout('double', 3)
    expect(lastRequest().fix_spec).toBe(false)
  })

  it('点「撤销」一步撤回整次排版', async () => {
    seed(panel('p1'), panel('p2'))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await runQuickLayout('double', 3)
    useUiStore.getState().statusAction!.run()
    expect(texts()).toHaveLength(0)
    expect((s().doc.objects.find((o) => o.id === 'p1') as PanelObject).overrides[0].prop).toBe('fontsize')
  })

  it('A 画布排完、切到同样历史深度的 B 画布再点胶囊撤销：B 的最近一次编辑不被撤', async () => {
    seed(panel('p1'), panel('p2'))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await runQuickLayout('double', 3)
    const depthA = s().past.length
    const action = useUiStore.getState().statusAction!
    s().addCanvas('B')
    for (let i = 0; i < depthA; i++) {
      s().commit(literal(`B 的编辑 ${i}`), (d) => {
        d.objects.push({ ...makeSubLabel(i, 1, 1), id: `b_${i}` })
      })
    }
    const docB = s().doc
    expect(s().past.length).toBe(depthA)
    action.run()
    expect(s().past.length).toBe(depthA)
    expect(s().doc).toBe(docB)
  })

  it('replay_required：没返回的发出去的图也重放', async () => {
    seed(panel('p1'), panel('p2'), panel('p3'))
    quickLayoutPlan.mockResolvedValue({ ...okPlan(), replay_required: true })
    await runQuickLayout('double', 3)
    expect(new Set(requestRender.mock.calls.map((c) => (c[0] as PanelObject).id))).toEqual(new Set(['p1', 'p2', 'p3']))
  })

  it('返回了没发过的图 = 方案作废，不 commit', async () => {
    seed(panel('p1'), panel('p2'))
    const bad = okPlan()
    if (bad.ok) bad.panels[0].key = 'zzz'
    quickLayoutPlan.mockResolvedValue(bad)
    const depth = s().past.length
    expect(await runQuickLayout('double', 3)).toBe('failed')
    expect(s().past.length).toBe(depth)
  })
})

describe('等待期间与失败', () => {
  it('等待期间有一张图被改了：丢弃方案、不 commit、按此刻的列表重放、胶囊提示再点一次', async () => {
    seed(panel('p1'), panel('p2'))
    quickLayoutPlan.mockImplementation(async () => {
      s().commit(literal('用户改了'), (d) => {
        const p = d.objects.find((o) => o.id === 'p1') as PanelObject
        p.overrides.push({ gid: 'axes_0.title', prop: 'color', value: '#f00' })
      })
      return okPlan()
    })
    const depth = s().past.length
    expect(await runQuickLayout('double', 3)).toBe('stale')
    expect(s().past.length).toBe(depth + 1) // 只有用户自己那一条
    expect(texts()).toHaveLength(0)
    expect(statusText()).toBe('图刚刚变了，再点一次一键排版')
    expect(requestRender).toHaveBeenCalled()
  })

  it('等首次渲染期间切换了文档：整条链路放弃，不是 applied，目标文档一个字没动', async () => {
    seed(panel('p1'), panel('p2'))
    exact.delete('p2')
    quickLayoutPlan.mockResolvedValue(okPlan())
    const run = runQuickLayout('double', 3)
    await Promise.resolve()
    await s().switchDocument(emptyProject(), 'd_other')
    seed(panel('p1'), panel('p2'))
    const dest = s().doc
    const depth = s().past.length
    exact.add('p2')
    useRenderStore.setState({ byKey: { ...useRenderStore.getState().byKey } })
    expect(await run).not.toBe('applied')
    expect(quickLayoutPlan).not.toHaveBeenCalled()
    expect(s().doc).toBe(dest)
    expect(s().past.length).toBe(depth)
    expect(useQuickLayoutStore.getState().busy).toBe(false)
  })

  it('规划请求挂起期间开始了一次拖动事务：不把排版并进那次事务，取消拖动后与拖动前一致', async () => {
    seed(panel('p1'), panel('p2'))
    s().commit(literal('加标注'), (d) => {
      d.objects.push({ ...makeSubLabel(0, 150, 100), id: 'note1' })
    })
    const before = s().doc
    const depth = s().past.length
    quickLayoutPlan.mockImplementation(async () => {
      s().beginTxn(literal('拖动'))
      s().txnUpdate((d) => {
        d.objects.find((o) => o.id === 'note1')!.x += 5 // 拖的是不相干的标注：面板与页面都没变
      })
      return okPlan()
    })
    expect(await runQuickLayout('double', 3)).not.toBe('applied')
    s().endTxn({ discard: true })
    expect(s().doc).toEqual(before)
    expect(s().past.length).toBe(depth)
    expect(s().past.some((e) => formatMessage(e.label) === '一键排版')).toBe(false)
  })

  it('后端回失败：什么都不改，一句话，不是红色', async () => {
    seed(panel('p1'), panel('p2'))
    const before = JSON.stringify(s().doc)
    const depth = s().past.length
    for (const code of ['unavailable', 'invalid_plan', 'not_converged', 'render_failed'] as const) {
      quickLayoutPlan.mockResolvedValueOnce({ ok: false, code })
      expect(await runQuickLayout('double', 3)).toBe('failed')
      expect(statusText()).toBe('这次没排好，再试一次')
      expect(useUiStore.getState().statusTone).toBe('info')
    }
    expect(JSON.stringify(s().doc)).toBe(before)
    expect(s().past.length).toBe(depth)
  })

  it('请求抛了（不知道服务端走到哪）：不 commit，所有发出去的图重放', async () => {
    seed(panel('p1'), panel('p2'))
    quickLayoutPlan.mockRejectedValue(new Error('boom'))
    const depth = s().past.length
    expect(await runQuickLayout('double', 3)).toBe('failed')
    expect(s().past.length).toBe(depth)
    expect(requestRender).toHaveBeenCalledTimes(2)
    expect(statusText()).toBe('这次没排好，再试一次')
  })

  it('not_licensed：不说话（交给 PremiumLock），不 commit', async () => {
    seed(panel('p1'), panel('p2'))
    quickLayoutPlan.mockResolvedValue({ ok: false, code: 'not_licensed' })
    expect(await runQuickLayout('double', 3)).toBe('not_licensed')
    expect(useUiStore.getState().status).toBeNull()
  })

  it('请求期间 busy 为真，再点是 no-op；结束后复位', async () => {
    seed(panel('p1'), panel('p2'))
    let release!: (r: QuickLayoutResponse) => void
    quickLayoutPlan.mockImplementation(() => new Promise((r) => (release = r)))
    const first = runQuickLayout('double', 3)
    expect(useQuickLayoutStore.getState().busy).toBe(true)
    expect(await runQuickLayout('double', 3)).toBe('busy')
    release({ ok: false, code: 'not_converged' })
    await first
    expect(useQuickLayoutStore.getState().busy).toBe(false)
    expect(quickLayoutPlan).toHaveBeenCalledTimes(1)
  })
})

describe('addSubLabels 没变', () => {
  it('位置是 x+1.5 / y+1、(a)(b) 按阅读顺序、加粗、10×5，选中新标签', async () => {
    seed(panel('p1', { x: 100, y: 10 }), panel('p2', { x: 10, y: 10 }))
    addSubLabels()
    const labels = texts()
    expect(labels.map((l) => ({ ...l, id: '' }))).toEqual([
      { id: '', type: 'text', text: '(a)', x: 11.5, y: 11, w: 10, h: 5, sizePt: 10, color: '#000000', align: 'left', bold: true },
      { id: '', type: 'text', text: '(b)', x: 101.5, y: 11, w: 10, h: 5, sizePt: 10, color: '#000000', align: 'left', bold: true },
    ])
    expect(useSelectionStore.getState().ids).toEqual(labels.map((l) => l.id))
  })

  it('共用构造：一键排版的标签与它是同一个形状，只有位置不同', () => {
    const a = makeSubLabel(2, 5, 6)
    expect({ ...a, id: '' }).toEqual({
      id: '', type: 'text', text: '(c)', x: 5, y: 6, w: 10, h: 5, sizePt: 10, color: '#000000', align: 'left', bold: true,
    })
  })
})

describe('排列 / 画布 / 网格记忆（ADR 0199 修订）', () => {
  const prefs = (p: Record<string, unknown>) =>
    localStorage.setItem('tavotto.quickLayout', JSON.stringify({ target: 'double', gapMm: 3, fixSpec: true, ...p }))
  const run = async () => {
    useUiStore.getState().setStatus(null)
    return runQuickLayout('double', 3)
  }

  it('请求带 columns（默认 null）与解析后的 canvas（没定过画布大小 = fit）', async () => {
    seed(panel('p1'), panel('p2'))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await run()
    expect(lastRequest().columns).toBeNull()
    expect(lastRequest().canvas).toBe('fit')
    expect(lastRequest().ratios).toBeUndefined()
    prefs({ columns: 2 })
    await run()
    expect(lastRequest().columns).toBe(2)
  })

  it('画布「自动」：用户定过画布大小 = keep；没定过 / 老文档没有这个字段 = fit；显式选择压过自动', async () => {
    seed(panel('p1'), panel('p2'))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await run()
    expect(lastRequest().canvas).toBe('fit')
    s().commit(literal('定大小'), (d) => {
      d.page.userSized = true
    })
    await run()
    expect(lastRequest().canvas).toBe('keep')
    prefs({ canvas: 'fit' })
    await run()
    expect(lastRequest().canvas).toBe('fit')
    prefs({ canvas: 'keep' })
    s().commit(literal('取消'), (d) => {
      delete d.page.userSized
    })
    await run()
    expect(lastRequest().canvas).toBe('keep')
  })

  it('用户改页面尺寸（setPageSize）置 userSized；一键排版自己写页面尺寸不置', async () => {
    seed(panel('p1'), panel('p2'))
    expect(s().doc.page.userSized).toBeUndefined()
    quickLayoutPlan.mockResolvedValue(okPlan())
    await run()
    expect([s().doc.page.w, s().doc.page.h]).toEqual([193, 80])
    expect(s().doc.page.userSized).toBeUndefined()
    setPageSize(200, 100)
    expect(s().doc.page.userSized).toBe(true)
    // 已经是 true 的画布，排版之后仍是 true（不改它）
    await run()
    expect(s().doc.page.userSized).toBe(true)
  })

  it('page_too_small：一句话（info 色），图一个字没动，不留记忆', async () => {
    seed(panel('p1', { nativeW: 85 }), panel('p2', { nativeW: 85 }))
    const depth = s().past.length
    quickLayoutPlan.mockResolvedValue({ ok: false, code: 'page_too_small' })
    expect(await run()).toBe('failed')
    expect(statusText()).toBe('画布放不下这几张图，可以减少列数或调大画布')
    expect(useUiStore.getState().statusTone).toBe('info')
    expect(s().past.length).toBe(depth)
    expect(s().doc.quickLayout).toBeUndefined()
  })

  it('排完同一次 commit 里存网格记忆（含 boxes、目标、间距、解析后的画布、fixSpec）；⌘Z 一起回去', async () => {
    seed(panel('p1', { nativeW: 85 }), panel('p2', { nativeW: 85 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    prefs({ canvas: 'keep', fixSpec: false })
    const depth = s().past.length
    await run()
    expect(s().past.length).toBe(depth + 1)
    expect(s().doc.quickLayout).toEqual({
      rows: 1,
      cols: 2,
      cells: { p1: [0, 0], p2: [0, 1] },
      colRatios: [1, 1],
      rowRatios: [1],
      boxes: { p1: [10, 10, 85, 60], p2: [98, 10, 85, 60] },
      target: 'double',
      gapMm: 3,
      canvas: 'keep',
      fixSpec: false,
    })
    s().undo()
    expect(s().doc.quickLayout).toBeUndefined()
  })

  it('后端没回网格（grid: null）/ 网格里有没发过的图：不留记忆，旧的也清掉', async () => {
    seed(panel('p1', { nativeW: 85 }), panel('p2', { nativeW: 85 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await run()
    expect(s().doc.quickLayout).toBeTruthy()
    quickLayoutPlan.mockResolvedValue({ ...okPlan(), grid: null })
    await run()
    expect(s().doc.quickLayout).toBeUndefined()
    quickLayoutPlan.mockResolvedValue({
      ...okPlan(),
      grid: { rows: 1, cols: 2, cells: { p1: [0, 0], zz: [0, 1] }, col_ratios: [1, 1], row_ratios: [1] },
    })
    await run()
    expect(s().doc.quickLayout).toBeUndefined()
  })

  it('图幅变了（nativeW ≠ size_mm）：commit 之后页面上的框是「渲染回来再乘一遍」前的预除值，记忆暂时无效；渲染同步到 box 后有效', async () => {
    const { quickLayoutValid } = await import('@/lib/quickLayoutGrid')
    // 原生 80 宽，求解器要 85 宽：obj.w = 85 * 80 / 85 = 80，与 box 差 5 mm
    seed(panel('p1', { nativeW: 80 }), panel('p2', { nativeW: 80 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await run()
    const memo = s().doc.quickLayout!
    expect(quickLayoutValid(memo, s().doc.objects)).toBe(false)
    // 渲染回来补图幅（syncPanelNativeSize 的结果）：w / h 乘回 box
    s().commit(literal('渲染同步'), (d) => {
      for (const o of d.objects) if (o.type === 'panel') [o.w, o.h] = memo.boxes[o.id].slice(2) as [number, number]
    })
    expect(quickLayoutValid(memo, s().doc.objects)).toBe(true)
  })

  it('记忆随画布存取：画布换算互逆，且不进 schema 升版', async () => {
    seed(panel('p1', { nativeW: 85 }), panel('p2', { nativeW: 85 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await run()
    const saved = JSON.parse(JSON.stringify(s().doc))
    expect(saved.quickLayout.cols).toBe(2)
    expect(saved.schema).toBe(2)
  })
})

describe('拖分隔线重排（runQuickLayoutRatios）', () => {
  /** 排好一版再说：两张 85×60 并排，记忆有效 */
  const laidOut = async () => {
    seed(panel('p1', { nativeW: 85 }), panel('p2', { nativeW: 85 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await runQuickLayout('double', 3)
    quickLayoutPlan.mockClear()
    requestRender.mockClear()
    useUiStore.getState().setStatus(null)
  }

  it('一次请求：同样的图 / 目标 / 间距 / 画布，columns = 记忆的列数，fix_spec false，带 ratios；一次 commit、历史名「调整排版比例」', async () => {
    await laidOut()
    const depth = s().past.length
    quickLayoutPlan.mockResolvedValue({
      ...okPlan(),
      panels: [
        { key: 'p1', patches: [{ gid: 'figure', prop: 'size_mm', value: [60, 60] }], box: [10, 10, 60, 60] },
        { key: 'p2', patches: [{ gid: 'figure', prop: 'size_mm', value: [110, 60] }], box: [73, 10, 110, 60] },
      ],
      grid: { rows: 1, cols: 2, cells: { p1: [0, 0], p2: [0, 1] }, col_ratios: [1, 2], row_ratios: [1] },
    })
    expect(await runQuickLayoutRatios({ cols: [1, 2], rows: [1] })).toBe('applied')
    expect(quickLayoutPlan).toHaveBeenCalledTimes(1)
    const req = lastRequest()
    expect(req.fix_spec).toBe(false)
    expect(req.columns).toBe(2)
    expect(req.ratios).toEqual({ cols: [1, 2], rows: [1] })
    expect(req.target).toBe('double')
    expect(req.gap_mm).toBe(3)
    expect(req.canvas).toBe('fit')
    expect(req.panels.map((p: { key: string }) => p.key)).toEqual(['p1', 'p2'])
    expect(s().past.length).toBe(depth + 1)
    expect(formatMessage(s().past.at(-1)!.label)).toBe('调整排版比例')
    expect(s().doc.quickLayout?.colRatios).toEqual([1, 2])
    expect(s().doc.quickLayout?.boxes.p2).toEqual([73, 10, 110, 60])
    // 记忆里仍是那一次排版的 fixSpec 选择
    expect(s().doc.quickLayout?.fixSpec).toBe(true)
    // 静默：不说「已排好」
    expect(useUiStore.getState().status).toBeNull()
    // 标签仍是 (a)(b)，没叠
    expect(texts().map((l) => l.text)).toEqual(['(a)', '(b)'])
  })

  it('记忆已失效（用户挪过一张）：不发请求', async () => {
    await laidOut()
    s().commit(literal('挪'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1')!
      p.x += 5
    })
    expect(await runQuickLayoutRatios({ cols: [1, 2], rows: [1] })).toBe('stale')
    expect(quickLayoutPlan).not.toHaveBeenCalled()
  })

  it('请求失败：不 commit、不留预览，一句话', async () => {
    await laidOut()
    const depth = s().past.length
    useQuickLayoutStore.setState({ preview: { p1: [10, 10, 60, 60] } })
    quickLayoutPlan.mockResolvedValue({ ok: false, code: 'not_converged' })
    expect(await runQuickLayoutRatios({ cols: [1, 2], rows: [1] })).toBe('failed')
    expect(s().past.length).toBe(depth)
    expect(useQuickLayoutStore.getState().preview).toBeNull()
    expect(statusText()).toBe('这次没排好，再试一次')
  })

  it('page_too_small 同样一句话', async () => {
    await laidOut()
    quickLayoutPlan.mockResolvedValue({ ok: false, code: 'page_too_small' })
    await runQuickLayoutRatios({ cols: [1, 2], rows: [1] })
    expect(statusText()).toBe('画布放不下这几张图，可以减少列数或调大画布')
  })

  it('等待期间文档变了：丢弃、不 commit', async () => {
    await laidOut()
    const depth = s().past.length
    quickLayoutPlan.mockImplementation(async () => {
      s().commit(literal('手改'), (d) => {
        d.objects.find((o) => o.id === 'p2')!.x += 1
      })
      return okPlan()
    })
    expect(await runQuickLayoutRatios({ cols: [1, 2], rows: [1] })).toBe('stale')
    expect(s().past.length).toBe(depth + 1) // 只有手改那一条
  })
})
