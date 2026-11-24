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
import { MAX_NOTES, notesOf, POLL_TIMEOUT, useChangePlanStore, versionNumber, versionsOf, type PlanNote } from './changePlanStore'
import { groupViews, summaryLine, unhandledViews } from '@/components/changePlan/changePlanModel'

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
