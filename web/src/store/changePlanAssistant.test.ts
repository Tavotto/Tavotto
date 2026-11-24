/**
 * 改图助手先出修改方案卡（ADR 0206 PR 4）：发起带的是用户的原话与选中元素、同步被拒的「这条路不适用」落回旧路径不留卡、
 * 整条指令都要改脚本 = 自动续走现有改脚本路径（`aiStore.start`，用发送那一刻的快照）、部分能改部分要改脚本 = 出卡 + 「改脚本做剩下的」、
 * 失败的卡也给「改脚本做」、续走失败不丢指令。后端不在这里（`tests/test_changeplan_endpoints.py`），只桩 api 与 `aiStore.start`。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { EngineError, type ChangePlan, type ChangePlanView } from '@/lib/api'
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
import { useAiStore } from './aiStore'
import { useDocumentStore } from './documentStore'
import { useChangePlanStore, type ScriptFallback } from './changePlanStore'
import { summaryLine, failureAction } from '@/components/changePlan/changePlanModel'

const ds = () => useDocumentStore.getState()
const cps = () => useChangePlanStore.getState()

const BASE = [{ gid: 'ax.title', prop: 'fontsize', value: 9 }]
const G1 = {
  key: 'g1',
  label: '把线条加粗',
  comment_id: 'a1',
  atomic: false,
  changes: [{ target_gid: 'ax.line', prop: 'linewidth', before: 1, after: 3, patch: { gid: 'ax.line', prop: 'linewidth', value: 3 } }],
  boxes: { after: [{ gid: 'ax.line', box: [0.1, 0.1, 0.2, 0.2] as [number, number, number, number] }] },
}
const plan = (over: Partial<ChangePlan> = {}): ChangePlan => ({
  id: 'p1',
  version: 1,
  source: 'assistant',
  base: { target: 'f_p1', fingerprint: 'fp' },
  base_patches: BASE,
  candidate_patches: [...BASE, { gid: 'ax.line', prop: 'linewidth', value: 3 }],
  groups: [G1],
  unhandled: [],
  preview: { before: '/b.png', after: '/a.png' },
  ...over,
})
const ready = (p: ChangePlan, extra: Record<string, unknown> = {}): ChangePlanView => ({ plan_id: 'p1', state: 'ready', plan: p, ...extra }) as ChangePlanView
const onlyScript = plan({ groups: [], candidate_patches: BASE, unhandled: [{ comment_id: 'a1', reason: 'needs_script' }] })
const partial = plan({ unhandled: [{ comment_id: 'a1', reason: 'needs_script' }] })

const FALLBACK: ScriptFallback = {
  project: 'pj_a',
  generation: 0,
  prompt: '把线条加粗，再把数据换成对数坐标',
  fileId: 'f_p1',
  panelId: 'p1',
  gid: 'ax.line',
  label: '线条',
  scope: 'element',
  target: '线条',
  canvas: 'main',
  script: 'fig.py',
}

const seed = (): PanelObject => {
  const p = { id: 'p1', type: 'panel', x: 10, y: 10, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject
  ds().commit(literal('放入'), (d) => {
    d.objects.push(p)
  })
  return p
}
const tick = (ms = 1300) => vi.advanceTimersByTimeAsync(ms)
const start = vi.fn()
const ask = () => cps().requestFromAssistant('p1', { instruction: FALLBACK.prompt, target: 'ax.line' }, { ...FALLBACK, canvas: ds().activeCanvasId, generation: useAiStore.getState().generation })

beforeEach(async () => {
  vi.useFakeTimers()
  createChangePlan.mockReset().mockResolvedValue({ plan_id: 'p1', state: 'running' })
  getChangePlan.mockReset()
  discardChangePlan.mockClear()
  requestRender.mockReset()
  start.mockReset().mockResolvedValue('accepted')
  useAiStore.setState({ start: start as never })
  setCurrentProjectId('pj_a')
  cps().clear()
  await ds().switchDocument(emptyProject(), 'd_assistant_plan')
  seed()
})
afterEach(() => vi.useRealTimers())

describe('发起', () => {
  it('带用户的原话与选中的元素，来源是 assistant（不带意见 / 分享记录）', async () => {
    getChangePlan.mockResolvedValue({ plan_id: 'p1', state: 'running', phase: 'model' })
    const key = await ask()
    expect(typeof key).toBe('string')
    expect(createChangePlan).toHaveBeenCalledOnce()
    const body = createChangePlan.mock.calls[0][0]
    expect(body).toMatchObject({ source: 'assistant', id: 'f_p1', instruction: FALLBACK.prompt, target: 'ax.line', patches: BASE })
    expect('comments' in body || 'share_id' in body).toBe(false)
    const e = cps().entries[key as string]
    expect(e).toMatchObject({ from: 'assistant', status: 'generating', instruction: FALLBACK.prompt })
  })

  it('整张图范围（没有选中元素）不带 target', async () => {
    await cps().requestFromAssistant('p1', { instruction: '全部字号改 10' }, FALLBACK)
    expect('target' in createChangePlan.mock.calls[0][0]).toBe(false)
  })

  it('同一张图已有方案在生成：不再发第二份', async () => {
    getChangePlan.mockResolvedValue({ plan_id: 'p1', state: 'running', phase: 'model' })
    await ask()
    expect(await ask()).toBeNull()
    expect(createChangePlan).toHaveBeenCalledOnce()
  })
})

describe('这条路不适用：落回旧路径，不留卡', () => {
  it.each([
    'change_plan_no_connection',
    'change_plan_unsupported_connection',
    'change_plan_native_unsupported',
    'change_plan_not_editable',
    'invalid_change_plan',
    'change_plan_too_large',
  ])('同步被拒 %s', async (code) => {
    createChangePlan.mockRejectedValue(new EngineError('x', '', code, ''))
    expect(await ask()).toBe('fallthrough')
    expect(cps().order).toEqual([])
    expect(start).not.toHaveBeenCalled() // 续走由调用方（发送路径）做，store 不替它发
  })

  it('其余失败（比如同图忙）是一张带「改脚本做」的失败卡，指令不丢', async () => {
    createChangePlan.mockRejectedValue(new EngineError('x', '', 'change_plan_busy', ''))
    const key = (await ask()) as string
    expect(cps().entries[key]).toMatchObject({ status: 'failed', failure: { code: 'change_plan_busy' } })
    expect(cps().entries[key].instruction).toBe(FALLBACK.prompt)
    expect(await cps().runScript(key)).toBe(true)
    expect(start).toHaveBeenCalledOnce()
  })

  it('重新生成遇到这类码也不绕路：留一张失败卡', async () => {
    getChangePlan.mockResolvedValue({ plan_id: 'p1', state: 'failed', code: 'change_plan_model_failed' })
    const key = (await ask()) as string
    await tick()
    createChangePlan.mockRejectedValue(new EngineError('x', '', 'change_plan_no_connection', ''))
    await cps().regenerate(key)
    const e = Object.values(cps().entries)[0]
    expect(e).toMatchObject({ status: 'failed', failure: { code: 'change_plan_no_connection' } })
  })
})

describe('整条指令都要改脚本', () => {
  it('自动续走现有改脚本路径：用发送那一刻的快照，卡只剩一行灰字', async () => {
    getChangePlan.mockResolvedValue(ready(onlyScript, { code: 'change_plan_empty' }))
    const key = (await ask()) as string
    await tick()
    expect(start).toHaveBeenCalledOnce()
    expect(start.mock.calls[0][0]).toMatchObject({
      prompt: FALLBACK.prompt,
      fileId: 'f_p1',
      panelId: 'p1',
      gid: 'ax.line',
      scope: 'element',
      script: 'fig.py',
      overrides: BASE,
      project: 'pj_a',
    })
    expect('draftStamp' in start.mock.calls[0][0]).toBe(false) // 草稿在发送时已清
    expect(cps().entries[key].status).toBe('handoff')
  })

  it('续走时那张图已经不在了：不发，失败卡带重试', async () => {
    getChangePlan.mockResolvedValue(ready(onlyScript, { code: 'change_plan_empty' }))
    const key = (await ask()) as string
    ds().commit(literal('删'), (d) => {
      d.objects = d.objects.filter((o) => o.id !== 'p1')
    })
    await tick()
    expect(start).not.toHaveBeenCalled()
    expect(cps().entries[key]).toMatchObject({ status: 'failed', failure: { code: 'change_plan_script_failed' } })
    expect(failureAction('change_plan_script_failed')).toBe('script')
  })

  it('续走发不出去（没有可用 Agent 等）：失败卡，点重试再发', async () => {
    start.mockRejectedValueOnce(new Error('no-usable-agent'))
    getChangePlan.mockResolvedValue(ready(onlyScript, { code: 'change_plan_empty' }))
    const key = (await ask()) as string
    await tick()
    expect(cps().entries[key]).toMatchObject({ status: 'failed', failure: { code: 'change_plan_script_failed' } })
    expect(await cps().runScript(key)).toBe(true)
    expect(start).toHaveBeenCalledTimes(2)
    expect(cps().entries[key].status).toBe('handoff')
  })

  it('不全是 needs_script（还有别的原因）的空方案：不自动续走，失败卡里另给「改脚本做」', async () => {
    const mixed = plan({ groups: [], unhandled: [{ comment_id: 'a1', reason: 'needs_script' }, { comment_id: 'a1', reason: 'unclear' }] })
    getChangePlan.mockResolvedValue(ready(mixed, { code: 'change_plan_empty' }))
    const key = (await ask()) as string
    await tick()
    expect(start).not.toHaveBeenCalled()
    expect(cps().entries[key]).toMatchObject({ status: 'failed', failure: { code: 'change_plan_empty' } })
  })
})

describe('部分能改、部分要改脚本', () => {
  it('出卡；「改脚本做剩下的」点了才走改脚本路径，卡留着', async () => {
    getChangePlan.mockResolvedValue(ready(partial))
    const key = (await ask()) as string
    await tick()
    expect(start).not.toHaveBeenCalled()
    expect(cps().entries[key].status).toBe('ready')
    expect(summaryLine(cps().entries[key])).toContain('剩下的要改脚本')
    expect(await cps().runScript(key)).toBe(true)
    expect(start).toHaveBeenCalledOnce()
    expect(cps().entries[key]).toMatchObject({ status: 'ready', scriptStarted: true })
  })

  it('应用 = 一次 commit，⌘Z 一步回（与审阅来源同一条）', async () => {
    getChangePlan.mockResolvedValue(ready(partial))
    const key = (await ask()) as string
    await tick()
    const depth = ds().past.length
    expect(cps().apply(key)).toBe('applied')
    expect(ds().past.length).toBe(depth + 1)
    expect((ds().doc.objects.find((o) => o.id === 'p1') as PanelObject).overrides).toEqual(partial.candidate_patches)
  })

  it('全部能改：没有「改脚本」的出口', async () => {
    getChangePlan.mockResolvedValue(ready(plan()))
    const key = (await ask()) as string
    await tick()
    expect(summaryLine(cps().entries[key])).toBe('改了 1 处')
  })
})
