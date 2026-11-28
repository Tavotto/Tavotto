/**
 * 批注重改的前端状态（ADR 0206 §五）：批注增删与上限、`revise` 带批注发起 → 轮询 → 新一版入列并切过去（批注清空、各版勾选各记各的）、
 * 没解锁 / 失败 / 超时只落一句灰字、旧版原样留着；放弃一并收掉各版。后端见 `tests/test_changeplan_revise.py`。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { EngineError, type ChangePlan, type ChangePlanGroup, type ChangePlanView } from '@/lib/api'
import { emptyProject, type PanelObject } from '@/types/document'

const createChangePlan = vi.fn()
const getChangePlan = vi.fn()
const reviseChangePlan = vi.fn()
const discardChangePlan = vi.fn(async () => ({ discarded: true }))

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createChangePlan: (...a: unknown[]) => createChangePlan(...a),
  getChangePlan: (...a: unknown[]) => getChangePlan(...a),
  reviseChangePlan: (...a: unknown[]) => reviseChangePlan(...a),
  discardChangePlan: (...a: unknown[]) => discardChangePlan(...(a as [])),
}))
vi.mock('@/store/renderScheduler', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/renderScheduler')>()),
  requestRender: vi.fn(),
}))

import { setCurrentProjectId } from '@/lib/session'
import { useDocumentStore } from './documentStore'
import { MAX_NOTES, notesOf, panelOf, POLL_TIMEOUT, REVISE_UNDO_BLOCKED, setChangePlanUndoRunner, useChangePlanStore, versionNumber, versionsOf, type PlanNote } from './changePlanStore'
import { effectiveStatus, groupViews, noteResults, summaryLine, unhandledViews } from '@/components/changePlan/changePlanModel'

const ds = () => useDocumentStore.getState()
const cps = () => useChangePlanStore.getState()
const BASE = [{ gid: 'ax.title', prop: 'fontsize', value: 9 }]

const g = (key: string, patch: { gid: string; prop: string; value: unknown }, commentId: string, origin?: 'review' | 'note'): ChangePlanGroup => ({
  key,
  label: `意见 ${key}`,
  comment_id: commentId,
  ...(origin ? { origin } : {}),
  atomic: false,
  changes: [{ target_gid: patch.gid, prop: patch.prop, before: 1, after: 2, patch }],
  boxes: { after: [{ gid: patch.gid, box: [0.1, 0.1, 0.2, 0.2] }] },
})
const plan = (id: string, over: Partial<ChangePlan> = {}): ChangePlan => ({
  id,
  version: 1,
  source: 'review',
  base: { target: 'f_p1', fingerprint: 'fp' },
  base_patches: BASE,
  candidate_patches: [{ gid: 'ax.title', prop: 'fontsize', value: 14 }],
  groups: [g('g1', { gid: 'ax.title', prop: 'fontsize', value: 14 }, 'c1', undefined)],
  unhandled: [],
  preview: { before: `/api/change-plans/${id}/before.png`, after: `/api/change-plans/${id}/after.png` },
  ...over,
})
const ready = (p: ChangePlan): ChangePlanView => ({ plan_id: p.id, state: 'ready', plan: p }) as ChangePlanView
const V2 = plan('p2', {
  version: 2,
  parent: 'p1',
  candidate_patches: [{ gid: 'ax.legend', prop: 'loc', value: 'upper left' }, { gid: 'ax.title', prop: 'fontsize', value: 14 }],
  groups: [
    g('g1', { gid: 'ax.legend', prop: 'loc', value: 'upper left' }, 'n1', 'note'),
    g('g2', { gid: 'ax.title', prop: 'fontsize', value: 14 }, 'c1', 'review'),
  ],
  unhandled: [{ comment_id: 'n2', reason: 'no_effect' }],
})

const note = (id: string, text = '图例挪到左上'): PlanNote => ({ id, anchor: { kind: 'element', gid: 'ax.legend', box: [0.6, 0.1, 0.2, 0.1] }, label: '图例', text })
const tick = (ms = 1300) => vi.advanceTimersByTimeAsync(ms)

beforeEach(async () => {
  vi.useFakeTimers()
  createChangePlan.mockReset().mockResolvedValue({ plan_id: 'p1', state: 'running' })
  getChangePlan.mockReset()
  reviseChangePlan.mockReset().mockResolvedValue({ plan_id: 'p2', state: 'running' })
  discardChangePlan.mockClear()
  setCurrentProjectId('pj_a')
  cps().clear()
  await ds().switchDocument(emptyProject(), 'd_change_plan_revise')
  ds().commit(literal('放入'), (d) => {
    d.objects.push({ id: 'p1', type: 'panel', x: 10, y: 10, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject)
  })
})
afterEach(() => vi.useRealTimers())

const readyEntry = async () => {
  getChangePlan.mockResolvedValue(ready(plan('p1')))
  const key = (await cps().requestFromReview('p1', { comments: [{ id: 'c1', text: '标题太小', author: '林悦', anchor: { kind: 'overall' } } as never] }))!
  await tick()
  return key
}

describe('批注', () => {
  it('增改删与条数上限；第一版入列', async () => {
    const key = await readyEntry()
    expect(versionsOf(cps().entries[key]).map((v) => v.planId)).toEqual(['p1'])
    expect(versionNumber(cps().entries[key])).toBe(1)
    cps().saveNote(key, note('n1'))
    cps().saveNote(key, note('n1', '改成右上'))
    expect(notesOf(cps().entries[key]).map((n) => n.text)).toEqual(['改成右上'])
    for (let i = 2; i <= MAX_NOTES + 5; i++) cps().saveNote(key, note(`n${i}`))
    expect(notesOf(cps().entries[key])).toHaveLength(MAX_NOTES)
    cps().removeNote(key, 'n1')
    expect(notesOf(cps().entries[key])).toHaveLength(MAX_NOTES - 1)
  })
})

describe('重改期间的批注', () => {
  it('回包只清掉仍与提交快照一致的批注；进行中新增的、提交后又改过的保留', async () => {
    const key = await readyEntry()
    cps().saveNote(key, note('n1', '甲'))
    cps().saveNote(key, note('n2', '乙'))
    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'running', phase: 'model' })
    await cps().revise(key)
    cps().saveNote(key, note('n3', '新增'))
    cps().saveNote(key, note('n2', '乙改过'))
    getChangePlan.mockResolvedValue(ready(V2))
    await tick()
    expect(notesOf(cps().entries[key]).map((n) => [n.id, n.text])).toEqual([['n2', '乙改过'], ['n3', '新增']])
  })
})

describe('按批注重改', () => {
  it('带批注发起（id / 锚点 / 文字）；出好后新一版入列并切过去、批注清空，第 1 版的勾选留着', async () => {
    const key = await readyEntry()
    cps().toggleGroup(key, 'g1')
    cps().saveNote(key, note('n1'))
    getChangePlan.mockResolvedValue(ready(V2))
    expect(await cps().revise(key)).toBe('started')
    const [planId, body] = reviseChangePlan.mock.calls[0]
    expect(planId).toBe('p1')
    expect(body.notes).toEqual([{ id: 'n1', anchor: note('n1').anchor, text: '图例挪到左上' }])
    // 出版规范与新建助手方案同一份（currentStandard）：批注里写「按期刊标准」时模型才有数可依
    expect(body.standard).toEqual(expect.objectContaining({ name: expect.any(String), rules: expect.any(Object) }))
    expect(cps().entries[key].revising).toBe('p2')
    await tick()
    const e = cps().entries[key]
    expect(e.revising).toBeUndefined()
    expect(e.status).toBe('ready')
    expect(e.planId).toBe('p2')
    expect(e.afterSrc).toContain('/api/change-plans/p2/after.png')
    expect(notesOf(e)).toEqual([])
    expect(e.unchecked).toEqual({})
    expect(versionNumber(e)).toBe(2)
    // 切回第 1 版：它自己的勾选还在；再切回第 2 版
    cps().setViewVersion(key, 'p1')
    expect(cps().entries[key].planId).toBe('p1')
    expect(cps().entries[key].unchecked).toEqual({ g1: true })
    expect(versionNumber(cps().entries[key])).toBe(1)
    cps().setViewVersion(key, 'p2')
    expect(cps().entries[key].plan?.id).toBe('p2')
  })

  it('第 2 版：按批注改的组标 fromNote、卡上一句说按几条批注改了几处、没处理的批注按原话显示', async () => {
    const key = await readyEntry()
    cps().saveNote(key, note('n1'))
    cps().saveNote(key, note('n2', '把线加粗'))
    getChangePlan.mockResolvedValue(ready(V2))
    await cps().revise(key)
    await tick()
    const e = cps().entries[key]
    expect(groupViews(e).map((x) => x.fromNote)).toEqual([true, false])
    expect(summaryLine(e)).toBe('按 1 条批注又改了 1 处')
    expect(unhandledViews(e)[0].text).toBe('把线加粗')
  })

  it('重改进行中记下发出的批注（舞台标记 / 右栏按它画）；新版到手挂到那一版上，逐条结果按批注 id 对上：改成了 = 它那一组，没改成 = 原因；失败不留快照', async () => {
    const key = await readyEntry()
    cps().saveNote(key, note('n1'))
    cps().saveNote(key, note('n2', '把线加粗'))
    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'running', phase: 'model' })
    await cps().revise(key)
    expect(cps().entries[key].revisingNotes?.map((n) => n.id)).toEqual(['n1', 'n2'])
    // 进行中没有结果（后端只报整体阶段）
    expect(noteResults(cps().entries[key])).toBeNull()
    getChangePlan.mockResolvedValue(ready(V2))
    await tick()
    const e = cps().entries[key]
    expect(e.revisingNotes).toBeUndefined()
    expect(versionsOf(e).at(-1)?.fromNotes?.map((n) => n.id)).toEqual(['n1', 'n2'])
    expect(noteResults(e)?.rows).toEqual([
      { id: 'n1', n: 1, text: '图例挪到左上', done: true, groupKey: 'g1' },
      { id: 'n2', n: 2, text: '把线加粗', done: false, reason: '改了也看不出变化，这次没动' },
    ])
    // 切回第 1 版：不是重改出的，没有逐条结果；再切回来还在
    cps().setViewVersion(key, 'p1')
    expect(noteResults(cps().entries[key])).toBeNull()
    cps().setViewVersion(key, 'p2')
    expect(noteResults(cps().entries[key])?.rows).toHaveLength(2)
    // 再重改一次失败：快照清掉，第 2 版的结果不动
    cps().saveNote(key, note('n3', '再大一点'))
    getChangePlan.mockResolvedValue({ plan_id: 'p3', state: 'failed', code: 'change_plan_model_quota', error: 'x' })
    reviseChangePlan.mockResolvedValue({ plan_id: 'p3', state: 'running' })
    await cps().revise(key)
    await tick()
    expect(cps().entries[key].revisingNotes).toBeUndefined()
    expect(cps().entries[key].planId).toBe('p2')
    expect(noteResults(cps().entries[key])?.rows).toHaveLength(2)
  })

  it('逐条结果：答了「不改」的按批注组算没改成；脚本方案对不上逐条（rows = null）', async () => {
    const key = await readyEntry()
    cps().saveNote(key, note('n1'))
    getChangePlan.mockResolvedValue(ready(V2))
    await cps().revise(key)
    await tick()
    const e = cps().entries[key]
    expect(noteResults({ ...e, answers: { g1: 'no' } })?.rows?.[0]).toMatchObject({ id: 'n1', done: false })
    const script = { ...e, plan: { ...e.plan!, kind: 'script' as const } }
    expect(noteResults(script)).toEqual({ notes: versionsOf(e).at(-1)!.fromNotes, rows: null })
  })

  it('没有批注不发；没解锁回 not_licensed、只落一句灰字；发起失败 / 轮询失败 / 超时都不动旧版', async () => {
    const key = await readyEntry()
    expect(await cps().revise(key)).toBe('nothing')
    expect(reviseChangePlan).not.toHaveBeenCalled()
    cps().saveNote(key, note('n1'))

    reviseChangePlan.mockResolvedValueOnce({ ok: false, code: 'not_licensed' })
    expect(await cps().revise(key)).toBe('not_licensed')
    expect(cps().entries[key].reviseError?.code).toBe('not_licensed')
    expect(cps().entries[key].revising).toBeUndefined()

    reviseChangePlan.mockRejectedValueOnce(new EngineError('x', '', 'change_plan_busy', ''))
    expect(await cps().revise(key)).toBe('failed')
    expect(cps().entries[key].reviseError?.code).toBe('change_plan_busy')

    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'failed', code: 'change_plan_model_quota', error: 'x' })
    expect(await cps().revise(key)).toBe('started')
    await tick()
    expect(cps().entries[key].reviseError?.code).toBe('change_plan_model_quota')
    expect(cps().entries[key].planId).toBe('p1')
    expect(notesOf(cps().entries[key])).toHaveLength(1)
    expect(versionsOf(cps().entries[key])).toHaveLength(1)

    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'running', phase: 'model' })
    await cps().revise(key)
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000)
    expect(cps().entries[key].reviseError?.code).toBe(POLL_TIMEOUT)
    expect(cps().entries[key].revising).toBeUndefined()
  })

  it('在途时不重复发；放弃一并收掉各版（含在途的那一版）', async () => {
    const key = await readyEntry()
    cps().saveNote(key, note('n1'))
    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'running', phase: 'model' })
    expect(await cps().revise(key)).toBe('started')
    expect(await cps().revise(key)).toBe('busy')
    cps().discard(key)
    expect(discardChangePlan.mock.calls.map((c) => (c as unknown[])[0]).sort()).toEqual(['p1', 'p2'])
  })
})

// ---- 「再改一版」（已应用的卡按批注重改，ADR 0206 修订 2026-11-28）：拿到新版才撤回，失败不丢已应用的修改 ----
describe('已应用的卡按批注重改', () => {
  const CAND = [{ gid: 'ax.title', prop: 'fontsize', value: 14 }]
  const overridesNow = () => (ds().doc.objects.find((x) => x.id === 'p1') as PanelObject).overrides
  const statusNow = (key: string) => {
    const e = cps().entries[key]
    return effectiveStatus(e, panelOf(e)?.overrides ?? null)
  }
  const appliedWithNote = async () => {
    setChangePlanUndoRunner(() => ds().undo())
    const key = await readyEntry()
    expect(cps().apply(key)).toBe('applied')
    expect(overridesNow()).toEqual(CAND)
    cps().saveNote(key, note('n1'))
    return key
  }

  it('请求期间卡片仍是已应用、图不动；新版拿到时才撤回上一版、切到新版待确认', async () => {
    const key = await appliedWithNote()
    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'running', phase: 'model' })
    expect(await cps().revise(key)).toBe('started')
    await tick()
    expect(cps().entries[key].status).toBe('applied')
    expect(cps().entries[key].revising).toBe('p2')
    expect(overridesNow()).toEqual(CAND)
    getChangePlan.mockResolvedValue(ready(V2))
    await tick()
    const e = cps().entries[key]
    expect(overridesNow()).toEqual(BASE)
    expect(e.planId).toBe('p2')
    expect(e.status).toBe('ready')
    expect(e.notice).toBeUndefined()
    expect(statusNow(key)).toBe('ready')
  })

  it('新版没出来（轮询失败）：什么都不撤，卡片仍已应用，只落一句灰字', async () => {
    const key = await appliedWithNote()
    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'failed', code: 'change_plan_model_quota', error: 'x' })
    expect(await cps().revise(key)).toBe('started')
    await tick()
    const e = cps().entries[key]
    expect(overridesNow()).toEqual(CAND)
    expect(e.status).toBe('applied')
    expect(e.planId).toBe('p1')
    expect(e.reviseError?.code).toBe('change_plan_model_quota')
    expect(statusNow(key)).toBe('applied')
  })

  it('发请求就失败：同样什么都不撤', async () => {
    const key = await appliedWithNote()
    reviseChangePlan.mockRejectedValueOnce(new EngineError('x', '', 'change_plan_busy', ''))
    expect(await cps().revise(key)).toBe('failed')
    expect(overridesNow()).toEqual(CAND)
    expect(cps().entries[key].status).toBe('applied')
  })

  it('拿到新版时已撤不回（期间又改过图）：新版照样入列，文档不动，卡片派生成「原图改过了」并说一句', async () => {
    const key = await appliedWithNote()
    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'running', phase: 'model' })
    await cps().revise(key)
    ds().commit(literal('别的修改'), (d) => {
      d.objects[0].x = 3
    })
    getChangePlan.mockResolvedValue(ready(V2))
    await tick()
    const e = cps().entries[key]
    expect(overridesNow()).toEqual(CAND)
    expect((ds().doc.objects[0] as PanelObject).x).toBe(3)
    expect(e.planId).toBe('p2')
    expect(versionsOf(e)).toHaveLength(2)
    expect(e.notice).toBe('revise_stale')
    expect(statusNow(key)).toBe('stale')
  })

  it('等的这段时间里用户自己点了「撤销」：新版到手直接是待确认，不说「撤不回」', async () => {
    const key = await appliedWithNote()
    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'running', phase: 'model' })
    await cps().revise(key)
    expect(cps().undo(key)).toBe(true)
    expect(overridesNow()).toEqual(BASE)
    getChangePlan.mockResolvedValue(ready(V2))
    await tick()
    expect(cps().entries[key].notice).toBeUndefined()
    expect(statusNow(key)).toBe('ready')
  })

  it('用户正拖到一半（文档事务开着）：不撤（撤回会先收掉那一笔手势），按撤不回处理', async () => {
    const key = await appliedWithNote()
    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'running', phase: 'model' })
    await cps().revise(key)
    ds().beginTxn(literal('拖动'))
    getChangePlan.mockResolvedValue(ready(V2))
    await tick()
    expect(overridesNow()).toEqual(CAND)
    expect(cps().entries[key].notice).toBe('revise_stale')
    ds().endTxn()
  })

  it('用户先自己撤销了（存着的状态仍是已应用）再点「按批注重改」：照常发，新版到手直接是待确认', async () => {
    const key = await appliedWithNote()
    expect(cps().undo(key)).toBe(true)
    ds().commit(literal('别的修改'), (d) => {
      d.objects[0].x = 3
    })
    getChangePlan.mockResolvedValue(ready(V2))
    expect(await cps().revise(key)).toBe('started')
    await tick()
    expect(cps().entries[key].planId).toBe('p2')
    expect(cps().entries[key].notice).toBeUndefined()
    expect(statusNow(key)).toBe('ready')
  })

  it('点按钮那一刻就撤不回：不发请求、什么都不动，一句「没法替你撤回」', async () => {
    const key = await appliedWithNote()
    ds().commit(literal('别的修改'), (d) => {
      d.objects[0].x = 3
    })
    expect(await cps().revise(key)).toBe('failed')
    expect(reviseChangePlan).not.toHaveBeenCalled()
    expect(cps().entries[key].reviseError?.code).toBe(REVISE_UNDO_BLOCKED)
    expect(overridesNow()).toEqual(CAND)
  })
})

