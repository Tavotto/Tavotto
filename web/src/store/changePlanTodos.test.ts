/**
 * 建议待办卡（ADR 0214 第 2 期，前端）：勾选映射到逐组勾选 / 脚本待办勾选、「改这 N 项」一次 commit 应用勾着的图内待办、
 * 只把勾着的脚本待办（意见 id）交给脚本方案、过检查自动应用、改数据要先问再应用、撤销、只有脚本待办的方案不走旧的落回路径、
 * 上一轮对话随请求发出。后端不在这里（`tests/test_changeplan_script.py`），只桩 api。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { EngineError, type ChangePlan, type ChangePlanView } from '@/lib/api'
import { emptyProject, type PanelObject } from '@/types/document'

const createChangePlan = vi.fn()
const getChangePlan = vi.fn()
const discardChangePlan = vi.fn(async () => ({ discarded: true }))
const applyChangePlanScript = vi.fn()
const undoChangePlanScript = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createChangePlan: (...a: unknown[]) => createChangePlan(...a),
  getChangePlan: (...a: unknown[]) => getChangePlan(...a),
  discardChangePlan: (...a: unknown[]) => discardChangePlan(...(a as [])),
  applyChangePlanScript: (...a: unknown[]) => applyChangePlanScript(...a),
  undoChangePlanScript: (...a: unknown[]) => undoChangePlanScript(...a),
}))
vi.mock('@/store/renderScheduler', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/renderScheduler')>()),
  requestRender: vi.fn(),
}))

import { canonicalPatches } from '@/lib/changePlanApply'
import { setCurrentProjectId } from '@/lib/session'
import { todoPhase } from '@/components/changePlan/changePlanModel'
import { previousTurnOf } from '@/lib/previousTurn'
import { useAiStore, type AiSession } from './aiStore'
import { useDocumentStore } from './documentStore'
import { setChangePlanUndoRunner, todoLocked, useChangePlanStore, type PlanEntry, type ScriptFallback } from './changePlanStore'

const ds = () => useDocumentStore.getState()
const cps = () => useChangePlanStore.getState()
const BASE = [{ gid: 'ax.title', prop: 'fontsize', value: 9 }]
const group = (key: string, cid: string, gid: string, prop: string, value: number) => ({
  key,
  label: key,
  comment_id: cid,
  atomic: false,
  changes: [{ target_gid: gid, prop, before: 1, after: value, patch: { gid, prop, value } }],
  boxes: { after: [{ gid, box: [0.1, 0.1, 0.2, 0.2] as [number, number, number, number] }] },
})
const G1 = group('g1', 'a1', 'ax.line', 'linewidth', 3)
const G2 = group('g2', 'a2', 'ax.xlabel', 'fontsize', 12)

const advice = (over: Partial<ChangePlan> = {}): ChangePlan => ({
  id: 'p1',
  version: 1,
  source: 'assistant',
  reply: '线偏细、标题偏小，还可以加误差线。',
  todos: [
    { key: 't1', label: '线条加粗', why: '远看不清', kind: 'figure', group_key: 'g1' },
    { key: 't2', label: '标题放大', kind: 'figure', group_key: 'g2' },
    { key: 't3', label: '加误差线（SD）', why: '要看离散度', kind: 'script', comment_id: 'a3' },
  ],
  base: { target: 'f_p1', fingerprint: 'fp' },
  base_patches: BASE,
  candidate_patches: [...BASE, G1.changes[0].patch, G2.changes[0].patch],
  groups: [G1, G2],
  unhandled: [{ comment_id: 'a3', reason: 'needs_script' }],
  preview: { before: '/b.png', after: '/a.png' },
  ...over,
})
const scriptPlan = (over: Partial<ChangePlan> = {}): ChangePlan => ({
  id: 'p2',
  version: 1,
  kind: 'script',
  source: 'review',
  base: { target: 'f_p1', fingerprint: 'fp', script_sha256: 'aa' },
  base_patches: [], // 创建时用此刻（图内已应用之后）的 override，下面按实际写入
  candidate_patches: [],
  applied: null,
  groups: [
    {
      key: 'script',
      kind: 'script',
      label: '加误差线（SD）',
      comment_id: 'a1',
      atomic: true,
      changes: [],
      boxes: { after: [] },
      script: { rel: 'fig.py', base_sha256: 'aa', new_sha256: 'bb', diff: '', added: 1, removed: 0 },
    },
  ],
  unhandled: [],
  preview: { before: '/b.png', after: '/a.png' },
  ...over,
})

const FALLBACK: ScriptFallback = {
  project: 'pj_a',
  generation: 0,
  prompt: '这张图有什么可以改进的',
  fileId: 'f_p1',
  panelId: 'p1',
  gid: null,
  label: null,
  scope: 'figure',
  target: '整张图',
  canvas: 'main',
  script: 'fig.py',
}
const seed = () => {
  const p = { id: 'p1', type: 'panel', x: 10, y: 10, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject
  ds().commit(literal('放入'), (d) => {
    d.objects.push(p)
  })
}
const overridesNow = () => (ds().doc.objects.find((o) => o.id === 'p1') as PanelObject).overrides
const tick = (ms = 1300) => vi.advanceTimersByTimeAsync(ms)
const ask = (extra: { previousTurn?: { user: string; assistant: string } } = {}) =>
  cps().requestFromAssistant('p1', { instruction: FALLBACK.prompt, ...extra }, { ...FALLBACK, canvas: ds().activeCanvasId, generation: useAiStore.getState().generation }) as Promise<string>
const ready = (p: ChangePlan, extra: Record<string, unknown> = {}): ChangePlanView => ({ plan_id: p.id, state: 'ready', plan: p, ...extra }) as ChangePlanView

/** 当前 override 就是 script 方案的基线（`start` 带的是此刻的 override） */
let scriptView: ChangePlanView
function servePlans(first: ChangePlanView, script?: () => ChangePlanView) {
  createChangePlan.mockImplementation(async (body: { mode?: string }) => ({ plan_id: body.mode === 'script' ? 'p2' : 'p1', state: 'running' }))
  getChangePlan.mockImplementation(async (id: string) => (id === 'p1' ? first : (script ?? (() => scriptView))()))
}

async function toCard(p: ChangePlan = advice(), code?: string) {
  servePlans(ready(p, code ? { code } : {}))
  const key = await ask()
  await tick()
  return key
}

beforeEach(async () => {
  vi.useFakeTimers()
  createChangePlan.mockReset()
  getChangePlan.mockReset()
  applyChangePlanScript.mockReset().mockResolvedValue({ backup: 'fig/1', after_sha256: 'bb' })
  undoChangePlanScript.mockReset().mockResolvedValue(undefined)
  discardChangePlan.mockClear()
  setCurrentProjectId('pj_a')
  cps().clear()
  await ds().switchDocument(emptyProject(), 'd_todos')
  seed()
  setChangePlanUndoRunner(() => ds().undo())
  scriptView = ready(scriptPlan())
})
afterEach(() => vi.useRealTimers())

describe('出卡', () => {
  it('有待办的方案是一张待办卡：就绪、保留回答；只有脚本待办（change_plan_empty）也不落回改脚本、不是失败卡', async () => {
    const key = await toCard()
    expect(cps().entries[key]).toMatchObject({ status: 'ready', failure: undefined })
    expect(cps().entries[key].plan?.reply).toContain('误差线')

    cps().clear()
    createChangePlan.mockClear()
    const onlyScript = advice({ groups: [], candidate_patches: BASE, todos: [advice().todos![2]] })
    const key2 = await toCard(onlyScript, 'change_plan_empty')
    expect(cps().entries[key2]).toMatchObject({ status: 'ready', failure: undefined })
    expect(createChangePlan).toHaveBeenCalledOnce() // 没有自动续出脚本方案
  })

  it('只有回答、没有待办：落成 answered（画成助手正文，不出卡、不落回聊天 / 脚本）', async () => {
    const key = await toCard(advice({ todos: undefined, groups: [], candidate_patches: BASE, unhandled: [] }), 'change_plan_empty')
    expect(cps().entries[key].status).toBe('answered')
    expect(createChangePlan).toHaveBeenCalledOnce()
  })
})

describe('勾选', () => {
  it('默认全勾；图内待办的勾选就是逐组勾选，脚本待办记在 todoOff', async () => {
    const key = await toCard()
    cps().toggleTodo(key, 't1')
    cps().toggleTodo(key, 't3')
    const e = cps().entries[key]
    expect(e.unchecked).toEqual({ g1: true })
    expect(e.todoOff).toEqual({ t3: true })
    cps().toggleTodo(key, 't1')
    expect(cps().entries[key].unchecked.g1).toBe(false)
  })
})

describe('「改这 N 项」', () => {
  it('只应用勾着的图内待办：一次 commit，⌘Z 一步回；没有脚本待办就不出脚本方案', async () => {
    const key = await toCard()
    cps().toggleTodo(key, 't2')
    cps().toggleTodo(key, 't3')
    const before = ds().past.length
    expect(await cps().applyTodos(key)).toBe('applied')
    expect(ds().past.length).toBe(before + 1)
    expect(canonicalPatches(overridesNow())).toEqual(canonicalPatches([...BASE, G1.changes[0].patch]))
    expect(createChangePlan).toHaveBeenCalledOnce()
    expect(cps().entries[key].todoRun).toEqual({ figure: ['t1'], script: [], scripts: [] })
    ds().undo()
    expect(overridesNow()).toEqual(BASE)
  })

  it('勾着的脚本待办：只带它的意见 id（来源方案 + comment_ids），过检查自动应用，不要第二次点「应用」', async () => {
    const key = await toCard()
    cps().toggleTodo(key, 't1') // 图内只留 t2
    scriptView = ready(scriptPlan({ base_patches: [...BASE, G2.changes[0].patch] }))
    await cps().applyTodos(key)
    expect(canonicalPatches(overridesNow())).toEqual(canonicalPatches([...BASE, G2.changes[0].patch]))
    const body = createChangePlan.mock.calls[1][0]
    expect(body).toMatchObject({ source: 'review', mode: 'script', from_plan: 'p1', comment_ids: ['a3'] })
    expect('comments' in body || 'instruction' in body).toBe(false)
    const sk = cps().entries[key].todoRun?.scripts[0]?.key as string
    expect(cps().entries[sk]).toMatchObject({ todoOf: key, followsKey: key, status: 'generating' })
    expect(todoPhase(cps().entries[key], [cps().entries[sk]], true)).toBe('running')
    await tick()
    await tick(0)
    expect(applyChangePlanScript).toHaveBeenCalledWith('p2')
    expect(cps().entries[sk].status).toBe('applied')
    expect(todoPhase(cps().entries[key], [cps().entries[sk]], true)).toBe('done')
  })

  it('脚本待办全取消勾选：不出脚本方案；只剩脚本待办（没有图内）也能点，图内一步不动', async () => {
    const key = await toCard()
    cps().toggleTodo(key, 't1')
    cps().toggleTodo(key, 't2')
    scriptView = ready(scriptPlan({ base_patches: BASE }))
    await cps().applyTodos(key)
    expect(overridesNow()).toEqual(BASE)
    expect(createChangePlan.mock.calls[1][0]).toMatchObject({ comment_ids: ['a3'] })
  })

  it('一项都没勾：什么都不发', async () => {
    const key = await toCard()
    for (const t of ['t1', 't2', 't3']) cps().toggleTodo(key, t)
    expect(await cps().applyTodos(key)).toBe('nothing')
    expect(createChangePlan).toHaveBeenCalledOnce()
    expect(overridesNow()).toEqual(BASE)
  })

  it('改了画出来的数据：先问，回答「就这样改」才应用；不改 = 脚本没动', async () => {
    const confirm = { kind: 'script_data_changed' as const, parts: [] }
    const dataPlan = (): ChangePlan => {
      const p = scriptPlan({ base_patches: [...BASE, G1.changes[0].patch, G2.changes[0].patch] })
      p.groups[0] = { ...p.groups[0], confirm } as never
      return p
    }
    const key = await toCard()
    scriptView = ready(dataPlan())
    await cps().applyTodos(key)
    await tick()
    await tick(0)
    const sk = cps().entries[key].todoRun?.scripts[0]?.key as string
    expect(applyChangePlanScript).not.toHaveBeenCalled()
    expect(todoPhase(cps().entries[key], [cps().entries[sk]], true)).toBe('asking')
    cps().answerConfirm(sk, 'script', 'yes')
    await tick(0)
    expect(applyChangePlanScript).toHaveBeenCalledWith('p2', { confirmDataChange: true })
    expect(cps().entries[sk].status).toBe('applied')
  })

  it('改数据先问，答「先不改」：脚本没动，那一项算没改（卡回到可勾选；图内已改的照算）', async () => {
    const key = await toCard()
    cps().toggleTodo(key, 't1')
    cps().toggleTodo(key, 't2')
    const p = scriptPlan({ base_patches: BASE })
    p.groups[0] = { ...p.groups[0], confirm: { kind: 'script_data_changed', parts: [] } } as never
    scriptView = ready(p)
    await cps().applyTodos(key)
    await tick()
    const sk = cps().entries[key].todoRun?.scripts[0]?.key as string
    cps().answerConfirm(sk, 'script', 'no')
    await tick(0)
    expect(applyChangePlanScript).not.toHaveBeenCalled()
    expect(cps().entries[sk].failure?.code).toBe('change_plan_script_data_declined')
    expect(todoPhase(cps().entries[key], [cps().entries[sk]], false)).toBe('choose')
  })

  it('脚本方案失败：图内那一半留着，不自动再试；什么都没改成时卡回到可勾选，可再点一次', async () => {
    const key = await toCard()
    cps().toggleTodo(key, 't1')
    cps().toggleTodo(key, 't2')
    scriptView = { plan_id: 'p2', state: 'failed', code: 'change_plan_script_no_change' } as ChangePlanView
    await cps().applyTodos(key)
    await tick()
    const sk = cps().entries[key].todoRun?.scripts[0]?.key as string
    expect(cps().entries[sk].status).toBe('failed')
    expect(todoLocked(cps().entries[key], cps().entries)).toBe(false)
    expect(todoPhase(cps().entries[key], [cps().entries[sk]], false)).toBe('choose')
    // 再点一次：旧的失败卡收掉，换一张新的
    scriptView = ready(scriptPlan({ base_patches: BASE }))
    await cps().applyTodos(key)
    expect(cps().entries[sk]).toBeUndefined()
    expect(createChangePlan).toHaveBeenCalledTimes(3)
  })
})

describe('脚本卡被收起（真机 2026-11-27：失败卡点「收起」后建议卡又转圈、停不下）', () => {
  it('图内改好了、脚本那条失败：收起失败卡后建议卡仍是落定（不再转圈），脚本那条算没改', async () => {
    const key = await toCard()
    cps().toggleTodo(key, 't2') // 图内留 t1，脚本 t3
    scriptView = { plan_id: 'p2', state: 'failed', code: 'change_plan_script_no_change' } as ChangePlanView
    await cps().applyTodos(key)
    await tick()
    const sk = cps().entries[key].todoRun?.scripts[0]?.key as string
    expect(todoPhase(cps().entries[key], [cps().entries[sk]], true)).toBe('done')
    cps().dismiss(sk)
    expect(cps().entries[sk]).toBeUndefined()
    // 卡片拿到的脚本条目列表里已经没有它
    expect(todoPhase(cps().entries[key], [], true)).toBe('done')
    expect(todoLocked(cps().entries[key], cps().entries)).toBe(true) // 图内那次 commit 还在
  })

  it('什么都没改成、失败卡收起：回到可勾选，不转圈', async () => {
    const key = await toCard()
    cps().toggleTodo(key, 't1')
    cps().toggleTodo(key, 't2')
    scriptView = { plan_id: 'p2', state: 'failed', code: 'change_plan_script_no_change' } as ChangePlanView
    await cps().applyTodos(key)
    await tick()
    const sk = cps().entries[key].todoRun?.scripts[0]?.key as string
    cps().dismiss(sk)
    expect(todoPhase(cps().entries[key], [], false)).toBe('choose')
    expect(todoLocked(cps().entries[key], cps().entries)).toBe(false)
  })
})

describe('两条脚本待办之间又发了一句（图正忙）', () => {
  it('下一条不被悄悄丢掉：先排着，那份落定后接着做', async () => {
    const key = await toCard()
    cps().toggleTodo(key, 't1')
    cps().toggleTodo(key, 't2') // 只剩脚本 t3
    // 同一张图上另有一份方案正在生成
    const other = cps().entries[key]
    useChangePlanStore.setState((st) => ({ order: [...st.order, 'busy'], entries: { ...st.entries, busy: { ...other, key: 'busy', status: 'generating', todoRun: undefined } } }))
    scriptView = ready(scriptPlan({ base_patches: BASE }))
    await cps().applyTodos(key)
    await tick()
    expect(cps().entries[key].todoRun?.script).toEqual(['t3'])
    expect(cps().entries[key].todoRun?.scripts).toEqual([])
    expect(createChangePlan).toHaveBeenCalledOnce() // 只有出待办卡那一次，脚本方案还没发
    // 那份落定：接着做
    useChangePlanStore.setState((st) => ({ entries: { ...st.entries, busy: { ...st.entries.busy, status: 'discarded' } } }))
    await tick()
    expect(cps().entries[key].todoRun?.scripts.map((x) => x.todo)).toEqual(['t3'])
    expect(createChangePlan).toHaveBeenCalledTimes(2)
  })
})

describe('撤销', () => {
  it('图内 commit 与脚本一起撤回，卡回到可勾选（勾选保留）', async () => {
    const key = await toCard()
    cps().toggleTodo(key, 't2')
    scriptView = ready(scriptPlan({ base_patches: [...BASE, G1.changes[0].patch] }))
    await cps().applyTodos(key)
    await tick()
    await tick(0)
    const sk = cps().entries[key].todoRun?.scripts[0]?.key as string
    expect(await cps().undoTodos(key)).toBe(true)
    expect(undoChangePlanScript).toHaveBeenCalledWith('p2')
    expect(overridesNow()).toEqual(BASE)
    expect(cps().entries[sk]).toBeUndefined()
    expect(cps().entries[key].todoRun).toBeUndefined()
    expect(cps().entries[key].unchecked).toEqual({ g2: true })
  })
})

describe('上一轮对话随请求发出', () => {
  it('previous_turn 进图内方案请求', async () => {
    servePlans(ready(advice()))
    await ask({ previousTurn: { user: '这张图有什么可以改进的', assistant: '线偏细。' } })
    expect(createChangePlan.mock.calls[0][0].previous_turn).toEqual({ user: '这张图有什么可以改进的', assistant: '线偏细。' })
  })

  it('没有上一轮就不带这个字段', async () => {
    servePlans(ready(advice()))
    await ask()
    expect('previous_turn' in createChangePlan.mock.calls[0][0]).toBe(false)
  })
})

describe('previousTurnOf', () => {
  const session = (over: Partial<AiSession>): AiSession =>
    ({ id: 's', prompt: '问', panelId: 'p1', status: 'done', startedAt: 10, entries: [], ...over }) as AiSession
  const plan = (createdAt: number): PlanEntry => ({ key: 'k', from: 'assistant', panelId: 'p1', instruction: '有什么可以改进的', createdAt, status: 'ready', plan: advice() }) as unknown as PlanEntry

  it('取最后一串助手正文（过程行之前的不要）', () => {
    const s = session({ entries: [{ kind: 'message', text: '先看看' }, { kind: 'action', text: '读脚本' }, { kind: 'message', text: '建议一' }, { kind: 'message', text: '建议二' }] })
    expect(previousTurnOf('p1', [s], [])).toEqual({ user: '问', assistant: '建议一\n\n建议二' })
  })

  it('会话与方案卡取更新的那个；方案卡 = 回答 + 待办标签一行一条', () => {
    const s = session({ startedAt: 10, entries: [{ kind: 'message', text: '旧回答' }] })
    expect(previousTurnOf('p1', [s], [plan(20)])).toEqual({ user: '有什么可以改进的', assistant: '线偏细、标题偏小，还可以加误差线。\n线条加粗\n标题放大\n加误差线（SD）' })
    expect(previousTurnOf('p1', [{ ...s, startedAt: 30 }], [plan(20)])?.assistant).toBe('旧回答')
  })

  it('别的图、没完成的、没有正文的都不算；一轮都没有 = undefined；总长封顶', () => {
    expect(previousTurnOf('p9', [session({ entries: [{ kind: 'message', text: 'x' }] })], [plan(1)])).toBeUndefined()
    expect(previousTurnOf('p1', [session({ status: 'running', entries: [{ kind: 'message', text: 'x' }] }), session({})], [])).toBeUndefined()
    const long = previousTurnOf('p1', [session({ prompt: 'u'.repeat(5000), entries: [{ kind: 'message', text: 'a'.repeat(9000) }] })], [])
    expect((long?.user.length ?? 0) + (long?.assistant.length ?? 0)).toBeLessThanOrEqual(4000)
  })
})

// --------------------------------------------------------------------------- 三分意图 / 写成待办 / 再加一条 / 执行中跳过（ADR 0214 修订 2026-11-27）
const answeredPlan = (): ChangePlan => ({ ...advice({ todos: undefined, groups: [], candidate_patches: BASE, unhandled: [], reply: '1. 线偏细\n2. 标题偏小' }) })

describe('把这些建议写成待办', () => {
  it('同一个方案请求、意图 todos、不带用户的话，带这段回答当上一轮；待办卡出在回答下面，按钮不再出现', async () => {
    const key = await toCard(answeredPlan(), 'change_plan_empty')
    expect(cps().entries[key].status).toBe('answered')
    createChangePlan.mockClear()
    createChangePlan.mockResolvedValue({ plan_id: 'p9', state: 'running' })
    getChangePlan.mockResolvedValue(ready(advice({ id: 'p9' })))
    const todoKey = await cps().requestTodos(key)
    expect(typeof todoKey).toBe('string')
    const body = createChangePlan.mock.calls[0][0]
    expect(body).toMatchObject({ source: 'assistant', intent: 'todos', previous_turn: { assistant: '1. 线偏细\n2. 标题偏小' } })
    expect('instruction' in body).toBe(false)
    expect(cps().entries[key].todosKey).toBe(todoKey)
    expect(await cps().requestTodos(key)).toBeNull() // 点过一次就不再发
    await tick()
    expect(cps().entries[todoKey as string]).toMatchObject({ status: 'ready', from: 'assistant' })
    expect(cps().entries[todoKey as string].plan?.todos).toHaveLength(3)
  })

  it('这条请求被拒（没连上模型等）：留一张失败卡，不落回改脚本', async () => {
    const key = await toCard(answeredPlan(), 'change_plan_empty')
    createChangePlan.mockRejectedValue(new EngineError('x', '', 'change_plan_no_connection', ''))
    const todoKey = (await cps().requestTodos(key)) as string
    expect(cps().entries[todoKey]).toMatchObject({ status: 'failed', failure: { code: 'change_plan_no_connection' } })
  })
})

describe('再加一条', () => {
  const grown = (): ChangePlan =>
    advice({
      id: 'p3',
      version: 2,
      parent: 'p1',
      groups: [G1, G2, group('g3', 'a5', 'ax.legend', 'frameon', 0)],
      todos: [...advice().todos!, { key: 't4', label: '图例去掉边框', kind: 'figure', group_key: 'g3', added: true }],
    } as Partial<ChangePlan>)

  it('带来源方案与这句话；新一版并进同一张卡，已有的勾选与 key 不变，新条目默认勾上', async () => {
    const key = await toCard()
    cps().toggleTodo(key, 't1')
    createChangePlan.mockClear()
    createChangePlan.mockResolvedValue({ plan_id: 'p3', state: 'running' })
    getChangePlan.mockResolvedValue(ready(grown()))
    expect(await cps().addTodo(key, '  图例去掉边框 ')).toBe(true)
    expect(createChangePlan.mock.calls[0][0]).toMatchObject({ source: 'assistant', from_plan: 'p1', add_todo: '图例去掉边框', id: 'f_p1' })
    expect(cps().entries[key]).toMatchObject({ adding: 'p3', addingText: '图例去掉边框' })
    await tick()
    const e = cps().entries[key]
    expect(e.adding).toBeUndefined()
    expect(e.planId).toBe('p3')
    expect(e.plan?.todos?.map((t) => t.key)).toEqual(['t1', 't2', 't3', 't4'])
    expect(e.unchecked).toEqual({ g1: true }) // 老勾选原样；g3 没在里面 = 勾着
    expect(e.status).toBe('ready')
  })

  it('点过「改这 N 项」之后、满 12 条、空话、太长、在途时都不加', async () => {
    const key = await toCard()
    expect(await cps().addTodo(key, '   ')).toBe(false)
    expect(await cps().addTodo(key, 'x'.repeat(301))).toBe(false)
    createChangePlan.mockClear()
    getChangePlan.mockResolvedValue(ready(grown()))
    createChangePlan.mockResolvedValue({ plan_id: 'p3', state: 'running' })
    const first = cps().addTodo(key, 'a')
    expect(await cps().addTodo(key, 'b')).toBe(false) // 在途
    await first
    await tick()
    await cps().applyTodos(key)
    expect(await cps().addTodo(key, 'c')).toBe(false) // 已经点过
  })

  it('没加成：已有的卡原样，留一个稳定码给灰字，下一次可以再试', async () => {
    const key = await toCard()
    createChangePlan.mockRejectedValue(new EngineError('x', '', 'change_plan_busy', ''))
    expect(await cps().addTodo(key, '图例去掉边框')).toBe(false)
    expect(cps().entries[key]).toMatchObject({ adding: undefined, addError: { code: 'change_plan_busy' } })
    expect(cps().entries[key].plan?.todos).toHaveLength(3)
  })
})

describe('执行中跳过还没轮到的脚本待办', () => {
  const twoScripts = (): ChangePlan => {
    const p = advice()
    return { ...p, todos: [p.todos![2], { key: 't4', label: '换成对数坐标', kind: 'script', comment_id: 'a4' }], groups: [], candidate_patches: BASE, unhandled: [{ comment_id: 'a3', reason: 'needs_script' }, { comment_id: 'a4', reason: 'needs_script' }] }
  }
  const serve = (planId: string) => ready(scriptPlan({ id: planId, base_patches: BASE }))

  beforeEach(() => {
    let n = 1
    createChangePlan.mockReset().mockImplementation(async (body: { mode?: string }) => ({ plan_id: body.mode === 'script' ? `s${++n}` : 'p1', state: 'running' }))
    getChangePlan.mockReset().mockImplementation(async (id: string) => (id === 'p1' ? ready(twoScripts()) : serve(id)))
  })

  it('一条一条做：先只出第一条的脚本方案，应用之后才轮到下一条', async () => {
    const key = await ask()
    await tick()
    await cps().applyTodos(key)
    expect(createChangePlan.mock.calls.filter((c) => c[0].mode === 'script').map((c) => c[0].comment_ids)).toEqual([['a3']])
    await tick()
    await tick(0)
    await tick()
    await tick(0)
    expect(createChangePlan.mock.calls.filter((c) => c[0].mode === 'script').map((c) => c[0].comment_ids)).toEqual([['a3'], ['a4']])
    expect(applyChangePlanScript).toHaveBeenCalledTimes(2)
    expect(cps().entries[key].todoRun?.scripts.map((s) => s.todo)).toEqual(['t3', 't4'])
  })

  it('还没轮到的可以勾掉跳过（计数跟着变）；已经在做的锁着', async () => {
    const key = await ask()
    await tick()
    await cps().applyTodos(key)
    cps().toggleTodo(key, 't3') // 第一条已经开始：不能动
    expect(cps().entries[key].todoOff).toBeUndefined()
    cps().toggleTodo(key, 't4') // 还排着：可以跳过
    expect(cps().entries[key].todoOff).toEqual({ t4: true })
    await tick()
    await tick(0)
    await tick()
    await tick(0)
    expect(createChangePlan.mock.calls.filter((c) => c[0].mode === 'script')).toHaveLength(1)
    expect(applyChangePlanScript).toHaveBeenCalledTimes(1)
    const e = cps().entries[key]
    expect(todoPhase(e, [cps().entries[e.todoRun!.scripts[0].key]], false)).toBe('done')
  })

  it('第一条 Agent 决定不改（脚本没动）：后面排着的照做（真机：一条「没数据画不了」把后面几条全拖成没改）', async () => {
    const key = await ask()
    await tick()
    getChangePlan.mockImplementation(async (id: string) => (id === 'p1' ? ready(twoScripts()) : ({ plan_id: id, state: 'failed', code: 'change_plan_script_no_change' } as ChangePlanView)))
    await cps().applyTodos(key)
    await tick()
    await tick()
    await tick()
    expect(createChangePlan.mock.calls.filter((c) => c[0].mode === 'script')).toHaveLength(2)
  })

  it('第一条越界（动了别的文件）：停下，后面排着的不再做', async () => {
    const key = await ask()
    await tick()
    getChangePlan.mockImplementation(async (id: string) =>
      id === 'p1' ? ready(twoScripts()) : ({ plan_id: id, state: 'failed', code: 'change_plan_script_overreach', error: '', params: { files: ['x.py'], total: 1 } } as ChangePlanView),
    )
    await cps().applyTodos(key)
    await tick()
    await tick()
    await tick()
    expect(createChangePlan.mock.calls.filter((c) => c[0].mode === 'script')).toHaveLength(1)
    expect(todoLocked(cps().entries[key], cps().entries)).toBe(false)
  })
})
