/**
 * 窗口里的 Agent 待办卡（ADR 0217 §五）：发现（对得上图与基线才出卡、不重复、收起后不再出）与应用
 * （图内待办写进文档并告诉后端勾了哪几项、脚本待办只交回 Agent、插件先落定则撤回）。后端不在这里（`tests/test_changeplan_agent_card.py`），只桩 api。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { EngineError, type AgentPlanRef, type ChangePlan } from '@/lib/api'
import { emptyProject, type PanelObject } from '@/types/document'

const listAgentChangePlans = vi.fn()
const getChangePlan = vi.fn()
const reportAgentPlanApplied = vi.fn()
const discardChangePlan = vi.fn(async () => ({ discarded: true }))

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  listAgentChangePlans: (...a: unknown[]) => listAgentChangePlans(...a),
  getChangePlan: (...a: unknown[]) => getChangePlan(...a),
  reportAgentPlanApplied: (...a: unknown[]) => reportAgentPlanApplied(...a),
  discardChangePlan: (...a: unknown[]) => discardChangePlan(...(a as [])),
}))
vi.mock('@/store/renderScheduler', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/renderScheduler')>()),
  requestRender: vi.fn(),
}))

import { setCurrentProjectId } from '@/lib/session'
import { discoverAgentPlans, resetAgentPlanCache } from './agentPlanDiscovery'
import { resetHiddenAgentPlans, setChangePlanUndoRunner, useChangePlanStore } from './changePlanStore'
import { setProjectFile, useDocumentStore } from './documentStore'

const ds = () => useDocumentStore.getState()
const cps = () => useChangePlanStore.getState()
const BASE = [{ gid: 'ax.title', prop: 'fontsize', value: 9 }]
const G1 = {
  key: 'g1',
  label: 'g1',
  comment_id: 'a1',
  atomic: false,
  changes: [{ target_gid: 'ax.line', prop: 'linewidth', before: 1, after: 3, patch: { gid: 'ax.line', prop: 'linewidth', value: 3 } }],
  boxes: { after: [{ gid: 'ax.line', box: [0.1, 0.1, 0.2, 0.2] as [number, number, number, number] }] },
}
const plan = (over: Partial<ChangePlan> = {}): ChangePlan => ({
  id: 'p1',
  version: 1,
  source: 'assistant',
  agent: { session: 's1', panel: 'p1', layout: '组图', stem: 'Fig1', at: 100 },
  todos: [
    { key: 't1', label: '线条加粗', why: '远看不清', kind: 'figure', group_key: 'g1' },
    { key: 't2', label: '加误差线（SD）', kind: 'script' },
  ],
  base: { target: 'f_p1', fingerprint: 'fp' },
  base_patches: BASE,
  candidate_patches: [...BASE, G1.changes[0].patch],
  groups: [G1],
  unhandled: [],
  preview: { before: '/b.png', after: '/a.png' },
  ...over,
})
const REF: AgentPlanRef = { id: 'p1', target: 'f_p1', panel: 'p1', layout: '组图', created: 100, todos: 2 }
const panel = (id: string, fileId: string, overrides = BASE) =>
  ({ id, type: 'panel', x: 10, y: 10, w: 80, h: 60, fileId, fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides }) as PanelObject
const put = (...ps: PanelObject[]) =>
  ds().commit(literal('放入'), (d) => {
    d.objects.push(...ps)
  })
const overridesNow = (id = 'p1') => (ds().doc.objects.find((o) => o.id === id) as PanelObject).overrides
const serve = (refs: AgentPlanRef[], p: ChangePlan = plan()) => {
  listAgentChangePlans.mockResolvedValue(refs)
  getChangePlan.mockImplementation(async (id: string) => ({ plan_id: id, state: 'ready', plan: { ...p, id } }))
}
const binding = { projectId: 'pj_a', name: '组图', file: 'tavottofile/组图.json', revision: 'r', dirty: false }
const entries = () => Object.values(cps().entries)

beforeEach(async () => {
  listAgentChangePlans.mockReset()
  getChangePlan.mockReset()
  reportAgentPlanApplied.mockReset().mockResolvedValue(undefined)
  discardChangePlan.mockClear()
  setCurrentProjectId('pj_a')
  resetHiddenAgentPlans()
  resetAgentPlanCache()
  cps().clear()
  await ds().switchDocument(emptyProject(), 'd_agent_plans')
  setProjectFile(null)
  setChangePlanUndoRunner(() => ds().undo())
})

describe('发现', () => {
  it('图与基线都对得上：出一张 Agent 来源的建议卡', async () => {
    put(panel('p1', 'f_p1'))
    serve([REF])
    expect(await discoverAgentPlans()).toBe(1)
    const [e] = entries()
    expect(e).toMatchObject({ from: 'assistant', status: 'ready', panelId: 'p1', planId: 'p1', agent: {} })
    expect(e.plan?.todos).toHaveLength(2)
  })

  it('文档里没有这张图：不出卡，也不去读方案', async () => {
    put(panel('p9', 'other.pdf'))
    serve([REF])
    expect(await discoverAgentPlans()).toBe(0)
    expect(entries()).toHaveLength(0)
    expect(getChangePlan).not.toHaveBeenCalled()
  })

  it('这张图此刻的修改与方案基线对不上（指纹不一致）：不出卡', async () => {
    put(panel('p1', 'f_p1', [{ gid: 'ax.title', prop: 'fontsize', value: 12 }]))
    serve([REF])
    expect(await discoverAgentPlans()).toBe(0)
    expect(entries()).toHaveLength(0)
  })

  it('同一张图有几份：只认方案记的那张子图；认不出就不出卡', async () => {
    put(panel('pa', 'f_p1'), panel('p1', 'f_p1'))
    serve([REF])
    await discoverAgentPlans()
    expect(entries().map((e) => e.panelId)).toEqual(['p1'])
    cps().clear()
    resetHiddenAgentPlans()
    serve([{ ...REF, panel: 'gone' }])
    expect(await discoverAgentPlans()).toBe(0)
  })

  it('同一张图开在另一份排版里：不出卡；排版名一致或认不出才出', async () => {
    put(panel('p1', 'f_p1'))
    serve([REF])
    expect(await discoverAgentPlans()).toBe(1) // 没绑定排版：只比子图 id
    cps().clear()
    resetHiddenAgentPlans()
    setProjectFile({ ...binding, name: '另一份' })
    expect(await discoverAgentPlans()).toBe(0)
    setProjectFile({ ...binding, name: '组图' })
    expect(await discoverAgentPlans()).toBe(1)
  })

  it('宿主记进卡里', async () => {
    put(panel('p1', 'f_p1'))
    serve([{ ...REF, host: 'claude_code' }])
    await discoverAgentPlans()
    expect(entries()[0].agent?.host).toBe('claude_code')
  })

  it('同一份方案不重复出；方案读过一次就不再读', async () => {
    put(panel('p1', 'f_p1'))
    serve([REF])
    await discoverAgentPlans()
    expect(await discoverAgentPlans()).toBe(0)
    expect(entries()).toHaveLength(1)
    expect(getChangePlan).toHaveBeenCalledTimes(1)
  })

  it('收起之后本次运行不再出现，也不去后端放弃那份方案', async () => {
    put(panel('p1', 'f_p1'))
    serve([REF])
    await discoverAgentPlans()
    cps().dismiss(entries()[0].key)
    expect(entries()).toHaveLength(0)
    expect(discardChangePlan).not.toHaveBeenCalled()
    expect(await discoverAgentPlans()).toBe(0)
    expect(entries()).toHaveLength(0)
  })

  it('列表接口出错：静默当没有', async () => {
    put(panel('p1', 'f_p1'))
    listAgentChangePlans.mockRejectedValue(new EngineError('x', '', 'boom', ''))
    expect(await discoverAgentPlans()).toBe(0)
  })
})

describe('应用', () => {
  const found = async () => {
    put(panel('p1', 'f_p1'))
    serve([REF])
    await discoverAgentPlans()
    return entries()[0].key
  }

  it('图内待办写进文档（一次 commit），勾着的全部 key（含脚本）告诉后端，脚本待办不起脚本方案', async () => {
    const key = await found()
    const before = ds().past.length
    expect(await cps().applyTodos(key)).toBe('applied')
    expect(overridesNow()).toEqual([...BASE, G1.changes[0].patch])
    expect(ds().past.length).toBe(before + 1)
    expect(reportAgentPlanApplied).toHaveBeenCalledWith('p1', ['t1', 't2'])
    const e = cps().entries[key]
    expect(e.agent).toEqual({ reported: ['t1', 't2'], handed: ['t2'] })
    expect(e.todoRun?.scripts).toEqual([])
    expect(entries()).toHaveLength(1) // 没有续出脚本方案卡
  })

  it('取消勾选的待办不告诉后端', async () => {
    const key = await found()
    cps().toggleTodo(key, 't2')
    await cps().applyTodos(key)
    expect(reportAgentPlanApplied).toHaveBeenCalledWith('p1', ['t1'])
    expect(cps().entries[key].agent?.handed).toEqual([])
  })

  it('只勾了脚本待办：文档不动，只告诉后端交回 Agent', async () => {
    const key = await found()
    cps().toggleTodo(key, 't1')
    expect(await cps().applyTodos(key)).toBe('nothing')
    expect(overridesNow()).toEqual(BASE)
    expect(reportAgentPlanApplied).toHaveBeenCalledWith('p1', ['t2'])
    expect(cps().entries[key].agent?.reported).toEqual(['t2'])
  })

  it('点过之后不会再告诉后端第二次', async () => {
    const key = await found()
    await cps().applyTodos(key)
    expect(await cps().applyTodos(key)).toBe('nothing')
    expect(reportAgentPlanApplied).toHaveBeenCalledTimes(1)
  })

  it('插件那边先应用了：窗口里刚写的撤回、卡收起、不再出现', async () => {
    reportAgentPlanApplied.mockRejectedValue(new EngineError('x', '', 'change_plan_agent_settled', ''))
    const key = await found()
    await cps().applyTodos(key)
    await vi.waitFor(() => expect(cps().entries[key]).toBeUndefined())
    expect(overridesNow()).toEqual(BASE)
    expect(await discoverAgentPlans()).toBe(0)
  })

  it('撤销：图内那次撤回，卡收起（后端已记了这次应用）', async () => {
    const key = await found()
    await cps().applyTodos(key)
    expect(await cps().undoTodos(key)).toBe(true)
    expect(overridesNow()).toEqual(BASE)
    expect(cps().entries[key]).toBeUndefined()
  })
})
