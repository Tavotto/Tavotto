/**
 * 修改方案的前端状态（ADR 0206）：发起 → 轮询 → 卡片状态、失败码映射、应用是**一次** commit（⌘Z 一步回）、
 * 基线对不上转「图已改过」不写、撤销只撤自己写的那一份、`replay_required` 触发重放、换项目作废在途轮询。
 * 后端求解 / 渲染不在这里（`tests/test_changeplan*.py`），这里只桩 `createChangePlan` / `getChangePlan` / `discardChangePlan`。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { EngineError, type ChangePlan, type ChangePlanGroup, type ChangePlanView } from '@/lib/api'
import { emptyProject, type PanelObject } from '@/types/document'

const createChangePlan = vi.fn()
const getChangePlan = vi.fn()
const discardChangePlan = vi.fn(async () => ({ discarded: true }))
const requestRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createChangePlan: (...a: unknown[]) => createChangePlan(...a),
  getChangePlan: (...a: unknown[]) => getChangePlan(...a),
  discardChangePlan: (...a: unknown[]) => discardChangePlan(...(a as [])),
}))
vi.mock('@/store/renderScheduler', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/renderScheduler')>()),
  requestRender: (...a: unknown[]) => requestRender(...a),
}))

import { setCurrentProjectId } from '@/lib/session'
import { useDocumentStore } from './documentStore'
import { useReviewStore } from './reviewStore'
import { POLL_TIMEOUT, setChangePlanUndoRunner, useChangePlanStore } from './changePlanStore'
import { registerOverrideStamper } from './documentStore'
import { effectiveStatus, failureAction } from '@/components/changePlan/changePlanModel'

const ds = () => useDocumentStore.getState()
const cps = () => useChangePlanStore.getState()

const BASE = [{ gid: 'ax.title', prop: 'fontsize', value: 9 }]
const g = (key: string, patch: { gid: string; prop: string; value: unknown }, commentId = key): ChangePlanGroup => ({
  key,
  label: `意见 ${key}`,
  comment_id: commentId,
  atomic: false,
  changes: [{ target_gid: patch.gid, prop: patch.prop, before: 1, after: 2, patch }],
  boxes: { after: [{ gid: patch.gid, box: [0.1, 0.1, 0.2, 0.2] }] },
})
const G1 = g('g1', { gid: 'ax.title', prop: 'fontsize', value: 14 })
const G2 = g('g2', { gid: 'ax.legend', prop: 'loc', value: 'upper left' })

const plan = (over: Partial<ChangePlan> = {}): ChangePlan => ({
  id: 'p1',
  version: 1,
  source: 'review',
  base: { target: 'f_p1', fingerprint: 'fp' },
  base_patches: BASE,
  candidate_patches: [
    { gid: 'ax.legend', prop: 'loc', value: 'upper left' },
    { gid: 'ax.title', prop: 'fontsize', value: 14 },
  ],
  groups: [G1, G2],
  unhandled: [{ comment_id: 'g3', reason: 'needs_script' }],
  preview: { before: '/api/change-plans/p1/before.png', after: '/api/change-plans/p1/after.png' },
  ...over,
})
const ready = (p: ChangePlan = plan(), extra: Record<string, unknown> = {}): ChangePlanView =>
  ({ plan_id: 'p1', state: 'ready', plan: p, ...extra }) as ChangePlanView

const seed = (overrides = BASE): PanelObject => {
  const p = {
    id: 'p1',
    type: 'panel',
    x: 10,
    y: 10,
    w: 80,
    h: 60,
    fileId: 'f_p1',
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 60,
    script: 'fig.py',
    overrides,
  } as PanelObject
  ds().commit(literal('放入'), (d) => {
    d.objects.push(p)
  })
  return p
}
const overridesNow = () => (ds().doc.objects.find((o) => o.id === 'p1') as PanelObject).overrides

/** 让轮询走一步（POLL_MS 之后微任务排空） */
const tick = (ms = 1300) => vi.advanceTimersByTimeAsync(ms)

const request = async () => {
  const key = await cps().requestFromReview('p1', { comments: [{ id: 'g1', text: '标题太小', author: '林悦', anchor: { kind: 'overall' } } as never] })
  return key!
}
const readyEntry = async () => {
  getChangePlan.mockResolvedValue(ready())
  const key = await request()
  await tick()
  return key
}

beforeEach(async () => {
  vi.useFakeTimers()
  // 生产里由 `hooks/useKeyboard` 交进来（`runUndoRedo`）；这里直接走文档历史的 undo
  setChangePlanUndoRunner(() => ds().undo())
  createChangePlan.mockReset().mockResolvedValue({ plan_id: 'p1', state: 'running' })
  getChangePlan.mockReset()
  discardChangePlan.mockClear()
  requestRender.mockReset()
  setCurrentProjectId('pj_a')
  cps().clear()
  await ds().switchDocument(emptyProject(), 'd_change_plan')
  seed()
})
afterEach(() => {
  vi.useRealTimers()
})

describe('发起与轮询', () => {
  it('带全量 override 与 client_request_id 发起；running → ready 后卡片待确认、两张图带项目参数', async () => {
    getChangePlan.mockResolvedValueOnce({ plan_id: 'p1', state: 'running', phase: 'render_before' }).mockResolvedValueOnce(ready())
    const key = await request()
    expect(createChangePlan).toHaveBeenCalledTimes(1)
    const body = createChangePlan.mock.calls[0][0]
    expect(body).toMatchObject({ id: 'f_p1', patches: BASE, source: 'review', client_request_id: key })
    expect(cps().entries[key].status).toBe('generating')
    await tick()
    expect(cps().entries[key].status).toBe('generating')
    await tick()
    const e = cps().entries[key]
    expect(e.status).toBe('ready')
    expect(e.afterSrc).toContain('/api/change-plans/p1/after.png')
    expect(e.afterSrc).toContain('pj=pj_a')
  })

  it('同一张图同一批意见在途 / 待确认时再点 = 聚焦已有的卡，不发第二个请求', async () => {
    getChangePlan.mockResolvedValue({ plan_id: 'p1', state: 'running', phase: 'x' })
    const a = await request()
    const b = await request()
    expect(b).toBe(a)
    expect(createChangePlan).toHaveBeenCalledTimes(1)
  })

  it('同步失败（没连 AI）落成卡片上的失败码，下一步是「连接 AI」', async () => {
    createChangePlan.mockRejectedValueOnce(new EngineError('x', '', 'change_plan_no_connection', ''))
    const key = await request()
    expect(cps().entries[key]).toMatchObject({ status: 'failed', failure: { code: 'change_plan_no_connection' } })
    expect(failureAction('change_plan_no_connection')).toBe('connect')
  })

  it('每个稳定码都有下一步（重试 / 连接 AI / 重新生成 / 收起），没有死胡同', () => {
    const codes = [
      'invalid_change_plan', 'change_plan_no_comments', 'change_plan_native_unsupported', 'change_plan_not_editable', 'change_plan_busy',
      'change_plan_unknown', 'change_plan_no_connection', 'change_plan_unsupported_connection', 'change_plan_model_auth', 'change_plan_model_quota',
      'change_plan_model_timeout', 'change_plan_model_failed', 'change_plan_too_large', 'change_plan_render_failed', 'change_plan_figure_changed',
      'change_plan_empty', 'change_plan_failed', POLL_TIMEOUT, 'something_new',
    ]
    for (const c of codes) expect(['connect', 'retry', 'regenerate', 'dismiss'], c).toContain(failureAction(c))
    expect(failureAction('change_plan_model_quota')).toBe('retry')
    expect(failureAction('change_plan_figure_changed')).toBe('regenerate')
    expect(failureAction('change_plan_too_large')).toBe('dismiss')
  })

  it('任务失败（终态 failed + 稳定码）；失败里带 replay_required 就重放这张图', async () => {
    getChangePlan.mockResolvedValue({ plan_id: 'p1', state: 'failed', code: 'change_plan_model_quota', error: 'x', replay_required: true })
    const key = await request()
    await tick()
    expect(cps().entries[key]).toMatchObject({ status: 'failed', failure: { code: 'change_plan_model_quota' } })
    expect(requestRender).toHaveBeenCalledTimes(1)
    expect(requestRender.mock.calls[0][0].id).toBe('p1')
  })

  it('没有一条意见能落成修改（change_plan_empty）：卡片按失败画，方案里的未处理还在', async () => {
    getChangePlan.mockResolvedValue(ready(plan({ groups: [], candidate_patches: BASE }), { code: 'change_plan_empty' }))
    const key = await request()
    await tick()
    expect(cps().entries[key]).toMatchObject({ status: 'failed', failure: { code: 'change_plan_empty' } })
    expect(cps().entries[key].plan?.unhandled).toHaveLength(1)
  })

  it('ready 且 replay_required：重放一次', async () => {
    getChangePlan.mockResolvedValue(ready(plan(), { replay_required: true }))
    await request()
    await tick()
    expect(requestRender).toHaveBeenCalledTimes(1)
  })

  it('轮询有上限：太久没结果落成本地超时码，不挂死', async () => {
    getChangePlan.mockResolvedValue({ plan_id: 'p1', state: 'running', phase: 'x' })
    const key = await request()
    await tick(5 * 60 * 1000)
    expect(cps().entries[key]).toMatchObject({ status: 'failed', failure: { code: POLL_TIMEOUT } })
  })

  it('轮询里连续断线才算失败；方案过期（change_plan_unknown）是终态', async () => {
    getChangePlan.mockRejectedValue(new EngineError('x', '', 'change_plan_unknown', ''))
    const key = await request()
    await tick()
    expect(cps().entries[key].failure?.code).toBe('change_plan_unknown')
  })

  it('换项目：卡片清空，在途轮询不再落进新项目', async () => {
    getChangePlan.mockResolvedValue(ready())
    await request()
    setCurrentProjectId('pj_b')
    expect(cps().order).toEqual([])
    await tick()
    expect(cps().entries).toEqual({})
  })

  it('重新生成：旧卡换成同一份请求的新卡，旧方案在后端放弃', async () => {
    getChangePlan.mockResolvedValue(ready())
    const key = await request()
    await tick()
    createChangePlan.mockResolvedValueOnce({ plan_id: 'p2', state: 'running' })
    const next = await cps().regenerate(key)
    expect(next).not.toBe(key)
    expect(cps().entries[key]).toBeUndefined()
    expect(discardChangePlan).toHaveBeenCalledTimes(1)
    expect(createChangePlan.mock.calls[1][0].comments).toEqual(createChangePlan.mock.calls[0][0].comments)
  })
})

describe('应用 / 撤销', () => {
  it('全部勾选：一次 commit 写 candidate_patches，⌘Z（undo）一步回到改前；写完重放', async () => {
    const key = await readyEntry()
    const depth = ds().past?.length ?? 0
    expect(cps().apply(key)).toBe('applied')
    expect(overridesNow()).toEqual(cps().entries[key].plan!.candidate_patches)
    expect(ds().past?.length ?? 0).toBe(depth + 1)
    expect(cps().entries[key].status).toBe('applied')
    expect(requestRender).toHaveBeenCalledTimes(1)
    ds().undo()
    expect(overridesNow()).toEqual(BASE)
  })

  it('取消勾选一组：写 applyGroups(base, groups, 勾选的)，不是 candidate_patches', async () => {
    const key = await readyEntry()
    cps().toggleGroup(key, 'g2')
    expect(cps().apply(key)).toBe('applied')
    expect(overridesNow()).toEqual([{ gid: 'ax.title', prop: 'fontsize', value: 14 }])
  })

  it('一组都没勾：什么都不写', async () => {
    const key = await readyEntry()
    cps().toggleGroup(key, 'g1')
    cps().toggleGroup(key, 'g2')
    const before = overridesNow()
    expect(cps().apply(key)).toBe('nothing')
    expect(overridesNow()).toBe(before)
    expect(cps().entries[key].status).toBe('ready')
  })

  it('基线对不上（生成之后这张图被改过）：转「图已改过」，不写', async () => {
    const key = await readyEntry()
    ds().commit(literal('手改'), (d) => {
      const o = d.objects.find((x) => x.id === 'p1') as PanelObject
      o.overrides = [{ gid: 'ax.title', prop: 'fontsize', value: 20 }]
    })
    expect(effectiveStatus(cps().entries[key], overridesNow())).toBe('stale')
    const depth = ds().past?.length ?? 0
    expect(cps().apply(key)).toBe('stale')
    expect(cps().entries[key].status).toBe('stale')
    expect(overridesNow()).toEqual([{ gid: 'ax.title', prop: 'fontsize', value: 20 }])
    expect(ds().past?.length ?? 0).toBe(depth)
  })

  it('撤销走历史：栈顶还是那次 commit 才撤，一步回到改前；用户之后又改了别的就不替他撤', async () => {
    const key = await readyEntry()
    cps().apply(key)
    expect(cps().undo(key)).toBe(true)
    expect(overridesNow()).toEqual(BASE)
    expect(requestRender).toHaveBeenCalled()
    // 卡片以文档为准：撤回之后又是待确认，可以再应用
    expect(effectiveStatus(cps().entries[key], overridesNow())).toBe('ready')
    expect(cps().apply(key)).toBe('applied')

    ds().commit(literal('手改'), (d) => {
      const o = d.objects.find((x) => x.id === 'p1') as PanelObject
      o.overrides = [{ gid: 'ax.title', prop: 'fontsize', value: 31 }]
    })
    expect(cps().undo(key)).toBe(false)
    expect(overridesNow()).toEqual([{ gid: 'ax.title', prop: 'fontsize', value: 31 }])
    expect(cps().entries[key].notice).toBe('undo_failed')
  })

  it('⌘Z / ⌘⇧Z 之后卡片跟着文档走：已应用 → 撤回 = 待确认 → 重做 = 已应用；之后手改 = 图已改过', async () => {
    const key = await readyEntry()
    const st = () => effectiveStatus(cps().entries[key], overridesNow())
    cps().apply(key)
    expect(st()).toBe('applied')
    ds().undo()
    expect(st()).toBe('ready')
    ds().redo()
    expect(st()).toBe('applied')
    ds().undo()
    ds().commit(literal('手改'), (d) => {
      ;(d.objects.find((x) => x.id === 'p1') as PanelObject).overrides = [{ gid: 'ax.title', prop: 'fontsize', value: 40 }]
    })
    expect(st()).toBe('stale')
  })

  it('提交时的身份抄写不影响应用：方案 patch 的 identity 原样落下，不会误报「没能应用」', async () => {
    const withIdentity = plan({
      candidate_patches: [{ gid: 'ax.title', prop: 'fontsize', value: 14, identity: 'l1:abc' }],
      groups: [g('g1', { gid: 'ax.title', prop: 'fontsize', value: 14, identity: 'l1:abc' } as never)],
    })
    getChangePlan.mockResolvedValue(ready(withIdentity))
    const key = await request()
    await tick()
    // 一个会把改过值的条目身份重抄成别的东西的抄写器（生产里按此刻 manifest 抄）
    const off = registerOverrideStamper((d) => {
      for (const o of d.objects) if (o.type === 'panel') for (const ov of o.overrides) ov.identity = 'l1:rewritten'
    })
    try {
      expect(cps().apply(key)).toBe('applied')
      expect(overridesNow()).toEqual([{ gid: 'ax.title', prop: 'fontsize', value: 14, identity: 'l1:abc' }])
    } finally {
      off()
    }
  })

  it('只给分享记录 id 时，意见快照取审阅页签里同一份记录的未处理意见（卡片有条数、编号与称呼）', async () => {
    useReviewStore.setState({
      detail: {
        share_id: 's1',
        comments: [
          { id: 'c1', author: '林悦', text: 'a', resolved: false, anchor: { kind: 'overall' } },
          { id: 'c2', author: '王工', text: 'b', resolved: true, anchor: { kind: 'overall' } },
        ],
      } as never,
    })
    const key = (await cps().requestFromReview('p1', { share_id: 's1' }))!
    expect(createChangePlan.mock.calls[0][0]).toMatchObject({ share_id: 's1' })
    expect(createChangePlan.mock.calls[0][0].comments).toBeUndefined()
    expect(cps().entries[key].commentCount).toBe(1)
    useReviewStore.setState({ detail: null })
  })

  it('放弃：后端那份收掉，卡片是已放弃，之后不能再应用', async () => {
    const key = await readyEntry()
    cps().openDialog(key)
    cps().discard(key)
    expect(cps().entries[key].status).toBe('discarded')
    expect(cps().dialogKey).toBeNull()
    expect(discardChangePlan).toHaveBeenCalledTimes(1)
    expect(cps().apply(key)).toBe('failed')
  })
})
