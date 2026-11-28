/**
 * 脚本方案的批注重改（ADR 0206 修订 2026-11-28，前端状态）：`revise` 对脚本卡带上输入框选的连接；已应用的卡「再改一版」时请求期间脚本保持已应用，
 * **新版到手那一刻**才按备份撤回上一版（`undoChangePlanScript`）；撤不回（之后又改过脚本）= 新版照样入列、标「图已改过」+ 一句灰字、不动文件；
 * 新版失败 = 什么都不撤。后端见 `tests/test_changeplan_script_revise.py`。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { EngineError, type ChangePlan, type ChangePlanView } from '@/lib/api'
import { emptyProject, type PanelObject } from '@/types/document'

const getChangePlan = vi.fn()
const reviseChangePlan = vi.fn()
const undoChangePlanScript = vi.fn()
const applyChangePlanScript = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  getChangePlan: (...a: unknown[]) => getChangePlan(...a),
  reviseChangePlan: (...a: unknown[]) => reviseChangePlan(...a),
  undoChangePlanScript: (...a: unknown[]) => undoChangePlanScript(...a),
  applyChangePlanScript: (...a: unknown[]) => applyChangePlanScript(...a),
  discardChangePlan: async () => ({ discarded: true }),
}))
vi.mock('./aiStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./aiStore')>()),
  currentRunSelection: () => ({ connection: 'conn1', model: 'm1' }),
}))
vi.mock('@/store/renderScheduler', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/renderScheduler')>()),
  requestRender: vi.fn(),
}))

import { setCurrentProjectId } from '@/lib/session'
import { canRevise } from '@/components/changePlan/changePlanModel'
import { useDocumentStore } from './documentStore'
import { REVISE_UNDO_BLOCKED, useChangePlanStore, versionsOf, type PlanEntry } from './changePlanStore'

const ds = () => useDocumentStore.getState()
const cps = () => useChangePlanStore.getState()
const BASE = [{ gid: 'ax.title', prop: 'fontsize', value: 9 }]
const APPLIED = { backup: 'fig/1', after_sha256: 'bb' }

const scriptPlan = (id: string, over: Partial<ChangePlan> = {}): ChangePlan => ({
  id,
  version: 1,
  kind: 'script',
  source: 'assistant',
  base: { target: 'f_p1', fingerprint: 'fp', script_sha256: 'aa' },
  base_patches: BASE,
  candidate_patches: BASE,
  applied: null,
  groups: [
    {
      key: 'script',
      kind: 'script',
      label: '横轴改成秒',
      comment_id: 'a1',
      atomic: true,
      changes: [],
      boxes: { after: [] },
      script: { rel: 'fig.py', base_sha256: 'aa', new_sha256: id === 'p1' ? 'bb' : 'cc', diff: '', added: 1, removed: 1 },
    },
  ],
  unhandled: [],
  preview: { before: `/api/change-plans/${id}/before.png`, after: `/api/change-plans/${id}/after.png`, manifest: `/api/change-plans/${id}/manifest` },
  ...over,
})
const V2 = scriptPlan('p2', { version: 2, parent: 'p1' })
const ready = (p: ChangePlan): ChangePlanView => ({ plan_id: p.id, state: 'ready', plan: p }) as ChangePlanView
const tick = (ms = 1300) => vi.advanceTimersByTimeAsync(ms)

const put = (over: Partial<PlanEntry> = {}) => {
  const s = ds()
  const p = scriptPlan('p1', over.status === 'applied' ? { applied: APPLIED } : {})
  const e: PlanEntry = {
    key: 'k',
    pj: 'pj_a',
    from: 'assistant',
    panelId: 'p1',
    fileId: 'f_p1',
    source: { fileId: 'f_p1', script: 'fig.py' },
    documentId: s.documentId,
    loadSeq: s.loadSeq,
    canvasId: s.activeCanvasId,
    createdAt: 1,
    status: 'ready',
    planId: 'p1',
    plan: p,
    versions: [{ planId: 'p1', plan: p, beforeSrc: '/b1', afterSrc: '/a1', unchecked: {} }],
    beforeSrc: '/b1',
    afterSrc: '/a1',
    unchecked: {},
    drafts: {},
    request: { instruction: '横轴改成秒', mode: 'script' },
    comments: [],
    commentCount: 0,
    ...over,
  }
  useChangePlanStore.setState({ entries: { k: e }, order: ['k'] })
  cps().saveNote('k', { id: 'n1', anchor: { kind: 'element', gid: 'ax.xlabel', box: [0.4, 0.9, 0.2, 0.05] }, label: '横轴标题', text: '单位写成 s' })
}
const e = () => cps().entries.k

beforeEach(async () => {
  vi.useFakeTimers()
  getChangePlan.mockReset()
  reviseChangePlan.mockReset().mockResolvedValue({ plan_id: 'p2', state: 'running' })
  undoChangePlanScript.mockReset().mockResolvedValue(undefined)
  applyChangePlanScript.mockReset().mockResolvedValue({ backup: 'fig/2', after_sha256: 'cc' })
  setCurrentProjectId('pj_a')
  cps().clear()
  await ds().switchDocument(emptyProject(), 'd_script_revise')
  ds().commit(literal('放入'), (d) => {
    d.objects.push({ id: 'p1', type: 'panel', x: 10, y: 10, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject)
  })
})
afterEach(() => vi.useRealTimers())

describe('脚本方案的批注重改', () => {
  it('脚本卡能重改（审阅转出的与助手来源的都算）', () => {
    put()
    expect(canRevise(e())).toBe(true)
    expect(canRevise({ ...e(), from: 'review' })).toBe(true)
  })

  it('待确认的脚本卡：带批注与输入框选的连接发起；新一版入列并切过去，不撤任何东西', async () => {
    put()
    getChangePlan.mockResolvedValue(ready(V2))
    expect(await cps().revise('k')).toBe('started')
    const [planId, body] = reviseChangePlan.mock.calls[0]
    expect(planId).toBe('p1')
    expect(body.connection).toBe('conn1')
    expect(body.notes).toEqual([{ id: 'n1', anchor: { kind: 'element', gid: 'ax.xlabel', box: [0.4, 0.9, 0.2, 0.05] }, text: '单位写成 s' }])
    await tick()
    expect(e().planId).toBe('p2')
    expect(e().status).toBe('ready')
    expect(versionsOf(e()).map((v) => v.planId)).toEqual(['p1', 'p2'])
    expect(undoChangePlanScript).not.toHaveBeenCalled()
  })

  it('已应用的卡「再改一版」：请求期间脚本保持已应用；新版到手才按备份撤回上一版，再是待确认，可应用新版', async () => {
    put({ status: 'applied' })
    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'running', phase: 'agent' })
    expect(await cps().revise('k')).toBe('started')
    await tick()
    expect(e().status).toBe('applied')
    expect(undoChangePlanScript).not.toHaveBeenCalled()
    getChangePlan.mockResolvedValue(ready(V2))
    await tick()
    expect(undoChangePlanScript).toHaveBeenCalledWith('p1')
    expect(e().planId).toBe('p2')
    expect(e().status).toBe('ready')
    expect(e().notice).toBeUndefined()
    expect(versionsOf(e())[0].plan.applied).toBeNull()
    expect(await cps().applyScript('k')).toBe('applied')
    expect(applyChangePlanScript).toHaveBeenCalledWith('p2')
  })

  it('新版到手时撤不回（之后又改过脚本）：新版照样入列、标「图已改过」+ 一句灰字，不再动文件', async () => {
    put({ status: 'applied' })
    undoChangePlanScript.mockRejectedValue(new EngineError('changed', '', 'change_plan_script_undo_conflict'))
    getChangePlan.mockResolvedValue(ready(V2))
    await cps().revise('k')
    await tick()
    expect(undoChangePlanScript).toHaveBeenCalledTimes(1)
    expect(e().planId).toBe('p2')
    expect(e().status).toBe('stale')
    expect(e().notice).toBe('revise_stale')
    expect(e().scriptBusy).toBe(false)
  })

  it('新版没出来（失败）：什么都不撤，卡片仍是已应用，弹窗里一句灰字', async () => {
    put({ status: 'applied' })
    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'failed', code: 'change_plan_script_agent_failed' })
    await cps().revise('k')
    await tick()
    expect(undoChangePlanScript).not.toHaveBeenCalled()
    expect(e().status).toBe('applied')
    expect(e().planId).toBe('p1')
    expect(e().reviseError?.code).toBe('change_plan_script_agent_failed')
  })

  it('等的时候用户已自己撤销（条目回到待确认）：新版到手不再撤一次', async () => {
    put({ status: 'applied' })
    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'running', phase: 'agent' })
    await cps().revise('k')
    await tick()
    useChangePlanStore.setState((s) => ({ entries: { ...s.entries, k: { ...s.entries.k, status: 'ready', plan: { ...s.entries.k.plan!, applied: null } } } }))
    getChangePlan.mockResolvedValue(ready(V2))
    await tick()
    expect(undoChangePlanScript).not.toHaveBeenCalled()
    expect(e().planId).toBe('p2')
    expect(e().status).toBe('ready')
  })

  it('已应用但后端说脚本之后又改过（409）：不发起、落成「没法替你撤回」那一句', async () => {
    put({ status: 'applied' })
    reviseChangePlan.mockRejectedValue(new EngineError('changed', '', 'change_plan_figure_changed'))
    expect(await cps().revise('k')).toBe('failed')
    expect(e().reviseError?.code).toBe(REVISE_UNDO_BLOCKED)
    expect(e().status).toBe('applied')
    expect(e().revising).toBeUndefined()
  })
})
