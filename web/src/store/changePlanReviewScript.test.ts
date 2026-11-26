/**
 * 审阅来源的「改脚本做剩下的」（ADR 0206 修订 2026-11-26）：只由用户点按钮发起——请求只带来源方案 id 与意见 id（不带意见文字）、
 * 出一张新的脚本方案卡挂在原卡后面；审阅来源全是 needs_script 时仍不自动续走。后端核验在 `tests/test_changeplan_script.py`。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { ChangePlan, ChangePlanView, ReviewComment } from '@/lib/api'
import { emptyProject, type PanelObject } from '@/types/document'

const createChangePlan = vi.fn()
const getChangePlan = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createChangePlan: (...a: unknown[]) => createChangePlan(...a),
  getChangePlan: (...a: unknown[]) => getChangePlan(...a),
  discardChangePlan: vi.fn(async () => ({ discarded: true })),
}))
vi.mock('@/store/renderScheduler', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/renderScheduler')>()),
  requestRender: vi.fn(),
}))

import { setCurrentProjectId } from '@/lib/session'
import { useAiStore } from './aiStore'
import { useDocumentStore } from './documentStore'
import { reviewScriptIds, useChangePlanStore } from './changePlanStore'

const ds = () => useDocumentStore.getState()
const cps = () => useChangePlanStore.getState()
const tick = (ms = 1300) => vi.advanceTimersByTimeAsync(ms)

const BASE = [{ gid: 'ax.title', prop: 'fontsize', value: 9 }]
const cm = (id: string, text: string): ReviewComment => ({ id, v: 1, anchor: { kind: 'overall' }, author: '林悦', text, resolved: false, created_at: '2026-11-26T10:00:00+08:00', replies: [] })
const COMMENTS = [cm('c1', '标题太小'), cm('c2', '把数据换成对数坐标'), cm('c3', '说不清')]
const G1 = {
  key: 'g1',
  label: '标题太小',
  comment_id: 'c1',
  atomic: false,
  changes: [{ target_gid: 'ax.title', prop: 'fontsize', before: 9, after: 14, patch: { gid: 'ax.title', prop: 'fontsize', value: 14 } }],
  boxes: { after: [{ gid: 'ax.title', box: [0.1, 0.1, 0.2, 0.2] as [number, number, number, number] }] },
}
const stored = (ids: string[]) => ids.map((id) => ({ id, text: COMMENTS.find((c) => c.id === id)!.text }))
const plan = (over: Partial<ChangePlan> = {}): ChangePlan => ({
  id: 'p1',
  version: 1,
  source: 'review',
  base: { target: 'f_p1', fingerprint: 'fp' },
  base_patches: BASE,
  candidate_patches: [{ gid: 'ax.title', prop: 'fontsize', value: 14 }],
  groups: [G1],
  unhandled: [
    { comment_id: 'c2', reason: 'needs_script' },
    { comment_id: 'c3', reason: 'unclear' },
  ],
  comments: stored(['c1', 'c2', 'c3']),
  preview: { before: '/b.png', after: '/a.png' },
  ...over,
})
const ready = (p: ChangePlan, id = 'p1', extra: Record<string, unknown> = {}): ChangePlanView => ({ plan_id: id, state: 'ready', plan: p, ...extra }) as ChangePlanView
const scriptPlan = (): ChangePlan => plan({ id: 'p2', kind: 'script', groups: [], unhandled: [], comments: undefined, candidate_patches: BASE })

const seed = () => {
  ds().commit(literal('放入'), (d) => {
    d.objects.push({ id: 'p1', type: 'panel', x: 10, y: 10, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject)
  })
}
const fromReview = async (p: ChangePlan) => {
  getChangePlan.mockResolvedValueOnce(ready(p))
  createChangePlan.mockResolvedValueOnce({ plan_id: 'p1', state: 'running' })
  const key = (await cps().requestFromReview('p1', { comments: COMMENTS }))!
  await tick()
  return key
}

beforeEach(async () => {
  vi.useFakeTimers()
  createChangePlan.mockReset()
  getChangePlan.mockReset()
  useAiStore.setState({ start: vi.fn() as never })
  setCurrentProjectId('pj_a')
  cps().clear()
  await ds().switchDocument(emptyProject(), 'd_review_script')
  seed()
})
afterEach(() => vi.useRealTimers())

describe('reviewScriptIds', () => {
  it('只取没处理里原因是「要改脚本」、且后端存着原文的意见', () => {
    expect(reviewScriptIds({ plan: plan() })).toEqual(['c2'])
    expect(reviewScriptIds({ plan: plan({ comments: stored(['c1']) }) })).toEqual([])
    expect(reviewScriptIds({ plan: plan({ comments: undefined }) })).toEqual([])
  })
  it('助手来源 / 一键排版 / 脚本方案卡不走这条路', () => {
    expect(reviewScriptIds({ from: 'assistant', plan: plan() })).toEqual([])
    expect(reviewScriptIds({ kind: 'layout', plan: plan() })).toEqual([])
    expect(reviewScriptIds({ plan: { ...plan(), kind: 'script' } })).toEqual([])
  })
})

describe('点「改脚本做剩下的」', () => {
  it('只带来源方案 id 与意见 id、source 是 review、mode 是 script；不带任何意见文字；新卡挂在原卡后面', async () => {
    const key = await fromReview(plan())
    createChangePlan.mockClear()
    createChangePlan.mockResolvedValueOnce({ plan_id: 'p2', state: 'running' })
    getChangePlan.mockResolvedValueOnce(ready(scriptPlan(), 'p2'))
    expect(await cps().runScript(key)).toBe(true)
    expect(createChangePlan).toHaveBeenCalledOnce()
    const body = createChangePlan.mock.calls[0][0]
    expect(body).toMatchObject({ source: 'review', mode: 'script', from_plan: 'p1', comment_ids: ['c2'], id: 'f_p1' })
    expect(['comments', 'share_id', 'instruction', 'target'].some((k) => k in body)).toBe(false)
    expect(JSON.stringify(body)).not.toContain('对数坐标')
    const other = Object.values(cps().entries).find((e) => e.key !== key)!
    expect(other).toMatchObject({ followsKey: key, request: { mode: 'script', scriptFrom: { plan_id: 'p1', comment_ids: ['c2'] } } })
    expect(other.from).toBeUndefined()
    expect(other.comments.map((c) => c.id)).toEqual(['c2'])
    expect(cps().entries[key].scriptStarted).toBe(true)
    await tick()
    expect(cps().entries[other.key]).toMatchObject({ status: 'ready' })
  })

  it('来源方案带着分享记录 id：脚本卡的请求记着同一个 id（应用后标已处理用），但不发给后端', async () => {
    getChangePlan.mockResolvedValueOnce(ready(plan()))
    createChangePlan.mockResolvedValueOnce({ plan_id: 'p1', state: 'running' })
    const key = (await cps().requestFromReview('p1', { comments: COMMENTS, share_id: 's1' }))!
    await tick()
    createChangePlan.mockClear()
    createChangePlan.mockResolvedValueOnce({ plan_id: 'p2', state: 'running' })
    getChangePlan.mockResolvedValueOnce(ready(scriptPlan(), 'p2'))
    expect(await cps().runScript(key)).toBe(true)
    expect('share_id' in createChangePlan.mock.calls[0][0]).toBe(false)
    const other = Object.values(cps().entries).find((e) => e.key !== key)!
    expect(other.request.share_id).toBe('s1')
  })

  it('没有 needs_script 的意见 / 已经交过一次：什么都不发', async () => {
    const key = await fromReview(plan({ unhandled: [{ comment_id: 'c3', reason: 'unclear' }] }))
    createChangePlan.mockClear()
    expect(await cps().runScript(key)).toBe(false)
    expect(createChangePlan).not.toHaveBeenCalled()
    const key2 = await fromReview(plan())
    cps().patchEntry(key2, { scriptStarted: true })
    const calls = createChangePlan.mock.calls.length
    expect(await cps().runScript(key2)).toBe(false)
    expect(createChangePlan).toHaveBeenCalledTimes(calls)
  })

  it('审阅来源整份都要改脚本：不自动续走，卡变成「没有一条能落成图内修改」的失败卡，等用户点', async () => {
    const onlyScript = plan({ groups: [], candidate_patches: BASE, unhandled: [{ comment_id: 'c2', reason: 'needs_script' }], comments: stored(['c2']) })
    createChangePlan.mockResolvedValueOnce({ plan_id: 'p1', state: 'running' })
    getChangePlan.mockResolvedValueOnce(ready(onlyScript, 'p1', { code: 'change_plan_empty' }))
    const key = (await cps().requestFromReview('p1', { comments: COMMENTS }))!
    await tick()
    expect(createChangePlan).toHaveBeenCalledTimes(1)
    expect(cps().entries[key]).toMatchObject({ status: 'failed', failure: { code: 'change_plan_empty' } })
    expect(Object.keys(cps().entries)).toEqual([key])
    expect(cps().entries[key].scriptStarted).toBeFalsy()
  })

  it('图内那部分还没应用：先应用再改脚本（与助手来源同一个做法）；脚本方案被拒时留一张失败卡、不碰 aiStore.start', async () => {
    const key = await fromReview(plan())
    createChangePlan.mockClear()
    createChangePlan.mockRejectedValueOnce(Object.assign(new (await import('@/lib/api')).EngineError('x', '', 'change_plan_script_unsupported', ''), {}))
    await cps().runScript(key)
    expect((ds().doc.objects.find((o) => o.id === 'p1') as PanelObject).overrides).toEqual([{ gid: 'ax.title', prop: 'fontsize', value: 14 }])
    const other = Object.values(cps().entries).find((e) => e.key !== key)!
    expect(other).toMatchObject({ status: 'failed', failure: { code: 'change_plan_script_unsupported' } })
    expect(useAiStore.getState().start).not.toHaveBeenCalled()
  })
})
