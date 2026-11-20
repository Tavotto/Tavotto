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
import { runUndoRedo } from '@/hooks/useKeyboard' // 加载它 = 把「撤销」交给一键排版的胶囊
import { runQuickLayout, useQuickLayoutStore } from './quickLayoutActions'

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
  it('没选中 = 当前画布全部面板；有精确 manifest 的进 panels，位图 / 旋转 / 翻转 / 没 manifest 的进 fixed', async () => {
    seed(
      panel('p1'),
      panel('p2'),
      panel('p3', { fileKind: 'raster' }),
      panel('p4', { rotation: 90 }),
      panel('p5', { flipH: true }),
    )
    exact.add('p3') // 位图即使有 manifest 也只当固定盒子
    quickLayoutPlan.mockResolvedValue({ ok: false, code: 'not_converged' })
    await runQuickLayout('double', 3)
    const body = lastRequest()
    expect(body.panels.map((p: { key: string }) => p.key)).toEqual(['p1', 'p2'])
    expect(body.fixed.map((p: { key: string }) => p.key).sort()).toEqual(['p3', 'p4', 'p5'])
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
    // 每张返回的图都重渲染
    expect(requestRender.mock.calls.map((c) => (c[0] as PanelObject).id).sort()).toEqual(['p1', 'p2'])
    expect(takeCheckpoint).toHaveBeenCalledTimes(1)
    expect(statusText()).toBe('已排好')
    expect(useUiStore.getState().statusAction).toBeTruthy()
  })

  it('图幅变了：页面上的框按「渲染回来会再乘一遍」的比例预除，渲染后正好是 box', async () => {
    seed(panel('p1'), panel('p2', { nativeW: 40, nativeH: 30 }))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await runQuickLayout('double', 3)
    const p2 = s().doc.objects.find((o) => o.id === 'p2') as PanelObject
    // 原生 40×30 → 图幅 85×60：渲染回来 w *= 85/40，所以此刻存 85*40/85 = 40
    expect([p2.w, p2.h]).toEqual([40, 30])
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
    expect(sub).toBe('图 b 是等比例坐标，没参与轴线对齐；图 a 的字号没能修到规范（修了会压住数据）')
  })

  it('点「撤销」一步撤回整次排版', async () => {
    seed(panel('p1'), panel('p2'))
    quickLayoutPlan.mockResolvedValue(okPlan())
    await runQuickLayout('double', 3)
    useUiStore.getState().statusAction!.run()
    expect(texts()).toHaveLength(0)
    expect((s().doc.objects.find((o) => o.id === 'p1') as PanelObject).overrides[0].prop).toBe('fontsize')
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
