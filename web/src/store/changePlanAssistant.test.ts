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
import { connection, connectCapsOf } from '@/components/settings/testCaps'
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
const overridesNow = () => (ds().doc.objects.find((o) => o.id === 'p1') as PanelObject).overrides
const tick = (ms = 1300) => vi.advanceTimersByTimeAsync(ms)
const start = vi.fn()
const ask = () => cps().requestFromAssistant('p1', { instruction: FALLBACK.prompt, target: 'ax.line' }, { ...FALLBACK, canvas: ds().activeCanvasId, generation: useAiStore.getState().generation })

beforeEach(async () => {
  vi.useFakeTimers()
  // 脚本修改方案（PR 6）这条路在这里不适用：落回现有的直接改脚本（脚本方案本身见 changePlanScript.test.ts）
  createChangePlan.mockReset().mockImplementation(async (body: { mode?: string }) => {
    if (body.mode === 'script') throw new EngineError('x', '', 'change_plan_script_unsupported', '')
    return { plan_id: 'p1', state: 'running' }
  })
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
    expect(await ask()).toMatchObject({ kind: 'fallthrough' })
    expect(cps().order).toEqual([])
    expect(start).not.toHaveBeenCalled() // 续走由调用方（发送路径）做，store 不替它发
  })

  it.each(['change_plan_unsupported_connection', 'change_plan_no_connection', 'change_plan_native_unsupported'])(
    '线程里才判出不适用（轮询得到 failed / %s）：同样接着走改脚本路径，不留失败卡',
    async (code) => {
      getChangePlan.mockResolvedValue({ plan_id: 'p1', state: 'failed', code })
      const key = (await ask()) as string
      await tick()
      expect(start).toHaveBeenCalledOnce()
      expect(start.mock.calls[0][0].prompt).toBe(FALLBACK.prompt)
      expect(cps().entries[key].status).toBe('handoff')
    },
  )

  it('线程里的真失败（模型超时）仍是失败卡，不自动改脚本', async () => {
    getChangePlan.mockResolvedValue({ plan_id: 'p1', state: 'failed', code: 'change_plan_model_timeout' })
    const key = (await ask()) as string
    await tick()
    expect(start).not.toHaveBeenCalled()
    expect(cps().entries[key]).toMatchObject({ status: 'failed', failure: { code: 'change_plan_model_timeout' } })
  })

  it('其余失败（比如同图忙）是一张带「改脚本做」的失败卡，指令不丢', async () => {
    createChangePlan.mockRejectedValue(new EngineError('x', '', 'change_plan_busy', ''))
    const key = (await ask()) as string
    expect(cps().entries[key]).toMatchObject({ status: 'failed', failure: { code: 'change_plan_busy' } })
    expect(cps().entries[key].instruction).toBe(FALLBACK.prompt)
    createChangePlan.mockRejectedValue(new EngineError('x', '', 'change_plan_script_unsupported', '')) // 脚本方案这条路不适用 → 落回直接改脚本
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

describe('连接用不了：脚本方案同样走不通，直接落回旧路径', () => {
  it.each(['change_plan_no_connection', 'change_plan_unsupported_connection'])('同步被拒（%s）：只发一次方案请求，不再发脚本方案', async (code) => {
    createChangePlan.mockRejectedValue(new EngineError('x', '', code, ''))
    expect(await ask()).toMatchObject({ kind: 'fallthrough' })
    expect(createChangePlan).toHaveBeenCalledOnce()
    expect(createChangePlan.mock.calls[0][0].mode).toBeUndefined()
    expect(cps().order).toEqual([])
  })

  it('其它不适用码（太长）：才再试一次脚本方案', async () => {
    createChangePlan.mockRejectedValueOnce(new EngineError('x', '', 'change_plan_too_large', '')).mockResolvedValue({ plan_id: 'p2', state: 'running' })
    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'running', phase: 'model' })
    const key = await ask()
    expect(typeof key).toBe('string')
    expect(createChangePlan).toHaveBeenCalledTimes(2)
    expect(createChangePlan.mock.calls[1][0]).toMatchObject({ mode: 'script' })
  })

  it('线程里才判出连接不支持（轮询 failed）：直接改脚本，不发脚本方案请求', async () => {
    getChangePlan.mockResolvedValue({ plan_id: 'p1', state: 'failed', code: 'change_plan_unsupported_connection' })
    const key = (await ask()) as string
    await tick()
    expect(createChangePlan).toHaveBeenCalledOnce()
    expect(start).toHaveBeenCalledOnce()
    expect(cps().entries[key].status).toBe('handoff')
  })

  it('脚本方案自己在线程里被拒：直接改脚本，不循环再发脚本方案', async () => {
    createChangePlan.mockRejectedValueOnce(new EngineError('x', '', 'change_plan_too_large', '')).mockResolvedValue({ plan_id: 'p2', state: 'running' })
    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'failed', code: 'change_plan_native_unsupported' })
    await ask()
    await tick()
    expect(createChangePlan).toHaveBeenCalledTimes(2)
    expect(start).toHaveBeenCalledOnce()
  })
})

describe('落回改脚本的原因码是这次调用自己的返回值', () => {
  it('两个面板几乎同时发起：各自拿到自己的码（可控 Promise 制造交错），互不串值', async () => {
    ds().commit(literal('放入第二张'), (d) => {
      d.objects.push({ id: 'p2', type: 'panel', x: 100, y: 10, w: 80, h: 60, fileId: 'f_p2', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig2.py', overrides: [] } as PanelObject)
    })
    const gates: Record<string, (e: Error) => void> = {}
    createChangePlan.mockImplementation(
      (b: { id: string }) => new Promise((_resolve, reject) => { gates[b.id] = reject }),
    )
    const fb2 = { ...FALLBACK, fileId: 'f_p2', panelId: 'p2', script: 'fig2.py' }
    const a = cps().requestFromAssistant('p1', { instruction: 'A' }, FALLBACK)
    const b = cps().requestFromAssistant('p2', { instruction: 'B' }, fb2)
    await tick(10)
    expect(Object.keys(gates).sort()).toEqual(['f_p1', 'f_p2'])
    // 后发起的先落定、码不同；先发起的后落定
    gates.f_p2(new EngineError('x', '', 'change_plan_no_connection', ''))
    const rb = await b
    gates.f_p1(new EngineError('x', '', 'change_plan_unsupported_connection', ''))
    const ra = await a
    expect(ra).toEqual({ kind: 'fallthrough', code: 'change_plan_unsupported_connection' })
    expect(rb).toEqual({ kind: 'fallthrough', code: 'change_plan_no_connection' })
  })

  it('旧值不残留：上一次带连接码落回，这一次是别的码——各读各的，连接码也不会让下一次跳过脚本方案', async () => {
    createChangePlan.mockRejectedValueOnce(new EngineError('x', '', 'change_plan_no_connection', ''))
    expect(await ask()).toEqual({ kind: 'fallthrough', code: 'change_plan_no_connection' })
    expect(createChangePlan).toHaveBeenCalledOnce() // 连接码：没发脚本方案
    createChangePlan.mockReset().mockRejectedValueOnce(new EngineError('x', '', 'change_plan_too_large', '')).mockRejectedValueOnce(new EngineError('x', '', 'change_plan_not_editable', ''))
    // 这次是别的码：不被上一次的连接码带偏，会再试脚本方案；脚本方案自己的码是最终返回
    expect(await ask()).toEqual({ kind: 'fallthrough', code: 'change_plan_not_editable' })
    expect(createChangePlan).toHaveBeenCalledTimes(2)
  })

  it('不适用来自 legacyOnly 的路径（没有后端码）：返回自带的标记码，不会读到旧值', async () => {
    createChangePlan.mockRejectedValueOnce(new EngineError('x', '', 'change_plan_no_connection', ''))
    await ask()
    getChangePlan.mockResolvedValue({ plan_id: 'p1', state: 'failed', code: 'change_plan_unsupported_connection' })
    createChangePlan.mockReset().mockResolvedValue({ plan_id: 'p1', state: 'running' })
    const key = (await ask()) as string
    await tick()
    expect(start).toHaveBeenCalledOnce()
    expect(cps().entries[key].status).toBe('handoff')
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

describe('聊天 / 提问（整条都是 out_of_scope）', () => {
  const chat = plan({ groups: [], candidate_patches: BASE, unhandled: [{ comment_id: 'a1', reason: 'out_of_scope' }] })

  it('不出失败卡：原话交给普通助手对话（不走脚本方案、不加交接句），卡撤掉、输入框草稿放掉', async () => {
    getChangePlan.mockResolvedValue(ready(chat, { code: 'change_plan_empty' }))
    const settled = vi.fn()
    const key = (await cps().requestFromAssistant('p1', { instruction: FALLBACK.prompt }, { ...FALLBACK, canvas: ds().activeCanvasId, generation: useAiStore.getState().generation }, settled)) as string
    await tick()
    expect(start).toHaveBeenCalledOnce()
    expect(start.mock.calls[0][0]).toMatchObject({ prompt: FALLBACK.prompt, fileId: 'f_p1', panelId: 'p1', script: 'fig.py' })
    expect(createChangePlan.mock.calls.filter(([b]) => (b as { mode?: string }).mode === 'script')).toHaveLength(0)
    expect(cps().entries[key]).toBeUndefined()
    expect(cps().order).not.toContain(key)
    expect(settled).toHaveBeenCalledOnce()
  })

  it('发不出去（没有可用 Agent）：留失败卡，指令不丢', async () => {
    start.mockRejectedValueOnce(new Error('no-usable-agent'))
    getChangePlan.mockResolvedValue(ready(chat, { code: 'change_plan_empty' }))
    const key = (await ask()) as string
    await tick()
    expect(cps().entries[key]).toMatchObject({ status: 'failed', failure: { code: 'change_plan_script_failed' } })
  })

  it('混合（有 out_of_scope 也有 needs_script / unclear）：照旧出失败卡，不落回', async () => {
    const mixed = plan({ groups: [], unhandled: [{ comment_id: 'a1', reason: 'out_of_scope' }, { comment_id: 'a1', reason: 'unclear' }] })
    getChangePlan.mockResolvedValue(ready(mixed, { code: 'change_plan_empty' }))
    const key = (await ask()) as string
    await tick()
    expect(start).not.toHaveBeenCalled()
    expect(cps().entries[key]).toMatchObject({ status: 'failed', failure: { code: 'change_plan_empty' } })
  })

  it('有能改的组（另一部分 out_of_scope）：照旧出方案卡', async () => {
    getChangePlan.mockResolvedValue(ready(plan({ unhandled: [{ comment_id: 'a1', reason: 'out_of_scope' }] })))
    const key = (await ask()) as string
    await tick()
    expect(start).not.toHaveBeenCalled()
    expect(cps().entries[key].status).toBe('ready')
  })

  it('unclear / no_change_needed 单独出现：照旧出失败卡', async () => {
    for (const reason of ['unclear', 'no_change_needed'] as const) {
      cps().clear()
      getChangePlan.mockResolvedValue(ready(plan({ groups: [], unhandled: [{ comment_id: 'a1', reason }] }), { code: 'change_plan_empty' }))
      const key = (await ask()) as string
      await tick()
      expect(start).not.toHaveBeenCalled()
      expect(cps().entries[key]).toMatchObject({ status: 'failed', failure: { code: 'change_plan_empty' } })
    }
  })

  it('审阅来源全 out_of_scope：照旧失败卡，不落回助手对话', async () => {
    const review = plan({ source: 'review', groups: [], unhandled: [{ comment_id: 'c1', reason: 'out_of_scope' }] })
    getChangePlan.mockResolvedValue(ready(review, { code: 'change_plan_empty' }))
    const key = (await cps().requestFromReview('p1', { share_id: 's1', comments: [{ id: 'c1', text: '你好' }] } as never)) as string
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
    expect(cps().entries[key]).toMatchObject({ status: 'applied', scriptStarted: true }) // 点之前先应用了图内修改
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


describe('用输入框选的那条连接与型号', () => {
  it('助手与审阅两种来源的请求都带 connection / model / effort（没有连接视图 = 不带，后端按旧的挑法）', async () => {
    await ask()
    expect('connection' in createChangePlan.mock.calls[0][0]).toBe(false)
    cps().clear()
    useAiStore.setState({
      caps: connectCapsOf([connection('sub:codex', { route: 'cli:codex', models: ['gpt-x'], default_model: 'gpt-x' }), connection('prov2', { models: ['m-2'], default_model: 'm-2' })]),
      connection: 'prov2',
      models: { prov2: 'm-2' },
    })
    await ask()
    expect(createChangePlan.mock.calls[1][0]).toMatchObject({ source: 'assistant', connection: 'prov2', model: 'm-2' })
    cps().clear()
    await cps().requestFromReview('p1', { comments: [{ id: 'c1', text: 't', author: 'a', anchor: { kind: 'overall' } } as never] })
    expect(createChangePlan.mock.calls[2][0]).toMatchObject({ source: 'review', connection: 'prov2', model: 'm-2' })
    useAiStore.setState({ caps: null, connection: null, models: {} })
  })

  it('选中的连接不支持结构化调用（后端 unsupported_connection）：落回改脚本路径，不留卡', async () => {
    createChangePlan.mockRejectedValue(new EngineError('x', '', 'change_plan_unsupported_connection', ''))
    expect(await ask()).toMatchObject({ kind: 'fallthrough' })
  })
})

describe('改脚本做剩下的：把图内已改的部分写进指令', () => {
  it('未应用时点：先把勾选的图内修改应用（一次 commit 进时间线）再续走，上下文写「已在图内改好」；原话仍在最前', async () => {
    getChangePlan.mockResolvedValue(ready(partial))
    const key = (await ask()) as string
    await tick()
    const depth = ds().past.length
    start.mockImplementation(async () => {
      // 续走发出时图内修改已经落到文档里
      expect(overridesNow()).toEqual(partial.candidate_patches)
      return 'accepted'
    })
    expect(await cps().runScript(key)).toBe(true)
    expect(ds().past.length).toBe(depth + 1)
    const prompt = start.mock.calls[0][0].prompt as string
    expect(prompt.startsWith(FALLBACK.prompt)).toBe(true)
    expect(prompt).toContain('已经在图内改好（不必再改）')
    expect(prompt).toContain('ax.line · linewidth → 3')
    expect(start.mock.calls[0][0].overrides).toEqual(partial.candidate_patches)
    expect(cps().entries[key].status).toBe('applied')
  })

  it('应用没成（图在生成之后被改过）：不续走改脚本，不写', async () => {
    getChangePlan.mockResolvedValue(ready(partial))
    const key = (await ask()) as string
    await tick()
    ds().commit(literal('手改'), (d) => {
      ;(d.objects.find((o) => o.id === 'p1') as PanelObject).overrides = [{ gid: 'ax.title', prop: 'fontsize', value: 12 }]
    })
    expect(await cps().runScript(key)).toBe(false)
    expect(start).not.toHaveBeenCalled()
    expect(cps().entries[key].status).toBe('stale')
  })

  it('已应用时点：不再 commit，上下文写「已经改好」', async () => {
    getChangePlan.mockResolvedValue(ready(partial))
    const key = (await ask()) as string
    await tick()
    cps().apply(key)
    const depth = ds().past.length
    await cps().runScript(key)
    expect(ds().past.length).toBe(depth)
    expect(start.mock.calls[0][0].prompt).toContain('已经在图内改好（不必再改）')
  })

  it('应用后 ⌘Z 撤回（卡片显示待确认）再点：按有效状态判，先重新 commit 再 start，上下文是「已改好」', async () => {
    getChangePlan.mockResolvedValue(ready(partial))
    const key = (await ask()) as string
    await tick()
    expect(cps().apply(key)).toBe('applied')
    ds().undo()
    expect(overridesNow()).toEqual(BASE)
    expect(cps().entries[key].status).toBe('applied') // 存着的状态是旧的，有效状态是待确认
    const depth = ds().past.length
    start.mockImplementation(async () => {
      expect(overridesNow()).toEqual(partial.candidate_patches)
      return 'accepted'
    })
    expect(await cps().runScript(key)).toBe(true)
    expect(ds().past.length).toBe(depth + 1)
    expect(start.mock.calls[0][0].prompt).toContain('已经在图内改好（不必再改）')
    expect(start.mock.calls[0][0].overrides).toEqual(partial.candidate_patches)
  })

  it('应用后 ⌘Z 撤回、图又被改过：不续走，不写', async () => {
    getChangePlan.mockResolvedValue(ready(partial))
    const key = (await ask()) as string
    await tick()
    cps().apply(key)
    ds().undo()
    ds().commit(literal('手改'), (d) => {
      ;(d.objects.find((o) => o.id === 'p1') as PanelObject).overrides = [{ gid: 'ax.title', prop: 'fontsize', value: 12 }]
    })
    const depth = ds().past.length
    expect(await cps().runScript(key)).toBe(false)
    expect(start).not.toHaveBeenCalled()
    expect(ds().past.length).toBe(depth)
    expect(cps().entries[key].status).toBe('stale')
  })

  it('取消勾选的组不应用也不写进上下文', async () => {
    getChangePlan.mockResolvedValue(ready(partial))
    const key = (await ask()) as string
    await tick()
    cps().toggleGroup(key, 'g1')
    const depth = ds().past.length
    await cps().runScript(key)
    expect(ds().past.length).toBe(depth)
    expect(start.mock.calls[0][0].prompt).toBe(FALLBACK.prompt)
  })

  it('整条都要改脚本：指令原样，一个字不加', async () => {
    getChangePlan.mockResolvedValue(ready(onlyScript, { code: 'change_plan_empty' }))
    await ask()
    await tick()
    expect(start.mock.calls[0][0].prompt).toBe(FALLBACK.prompt)
  })
})

describe('草稿与输入框留到方案落定', () => {
  it('onSettle：生成中不调；落定（出卡）时调一次且只一次', async () => {
    const settle = vi.fn()
    getChangePlan.mockResolvedValueOnce({ plan_id: 'p1', state: 'running', phase: 'model' }).mockResolvedValue(ready(plan()))
    await cps().requestFromAssistant('p1', { instruction: FALLBACK.prompt }, FALLBACK, settle)
    expect(settle).not.toHaveBeenCalled()
    await tick(1300)
    expect(settle).not.toHaveBeenCalled()
    await tick(1300)
    expect(settle).toHaveBeenCalledOnce()
    await tick(3000)
    expect(settle).toHaveBeenCalledOnce()
  })

  it('失败也算落定；同步被拒落回旧路径时不调（旧路径的账本自己清草稿）', async () => {
    const settle = vi.fn()
    getChangePlan.mockResolvedValue({ plan_id: 'p1', state: 'failed', code: 'change_plan_model_failed' })
    await cps().requestFromAssistant('p1', { instruction: 'x' }, FALLBACK, settle)
    await tick()
    expect(settle).toHaveBeenCalledOnce()
    cps().clear()
    const settle2 = vi.fn()
    createChangePlan.mockRejectedValue(new EngineError('x', '', 'change_plan_no_connection', ''))
    expect(await cps().requestFromAssistant('p1', { instruction: 'x' }, FALLBACK, settle2)).toMatchObject({ kind: 'fallthrough' })
    expect(settle2).not.toHaveBeenCalled()
  })
})
