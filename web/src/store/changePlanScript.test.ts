/**
 * 脚本修改出方案卡（ADR 0206 PR 6，前端）：整条指令 / 剩下的要改脚本时先出「脚本方案」卡（`mode: 'script'`），
 * 这条路不适用就落回现有的直接改脚本；应用 / 撤销是后端写脚本（不进文档历史、⌘Z 不撤脚本）；脚本在生成之后被改过 = 「图已改过」；
 * 越界失败卡一句话 + 文件名（≤ 5 个，其余「等 N 个」）+「再试一次」。后端不在这里（`tests/test_changeplan_script.py`），只桩 api。
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
const restoreChangePlanScript = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createChangePlan: (...a: unknown[]) => createChangePlan(...a),
  getChangePlan: (...a: unknown[]) => getChangePlan(...a),
  discardChangePlan: (...a: unknown[]) => discardChangePlan(...(a as [])),
  applyChangePlanScript: (...a: unknown[]) => applyChangePlanScript(...a),
  undoChangePlanScript: (...a: unknown[]) => undoChangePlanScript(...a),
  restoreChangePlanScript: (...a: unknown[]) => restoreChangePlanScript(...a),
}))
vi.mock('@/store/renderScheduler', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/renderScheduler')>()),
  requestRender: vi.fn(),
}))

import { setCurrentProjectId } from '@/lib/session'
import { effectiveStatus, failureAction, failureText, overreachFiles, summaryLine } from '@/components/changePlan/changePlanModel'
import { useAiStore } from './aiStore'
import { useDocumentStore } from './documentStore'
import { useChangePlanStore, type ScriptFallback } from './changePlanStore'

const ds = () => useDocumentStore.getState()
const cps = () => useChangePlanStore.getState()
const BASE = [{ gid: 'ax.title', prop: 'fontsize', value: 9 }]
const DIFF = '--- fig.py (修改前)\n+++ fig.py (修改后)\n@@ -1,3 +1,3 @@\n a\n-ax.set_title("old")\n+ax.set_title("new")\n c\n'

const scriptPlan = (over: Partial<ChangePlan> = {}): ChangePlan => ({
  id: 'p1',
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
      label: '改成对数坐标',
      comment_id: 'a1',
      atomic: true,
      changes: [],
      boxes: { after: [] },
      script: { rel: 'fig.py', base_sha256: 'aa', new_sha256: 'bb', diff: DIFF, added: 1, removed: 1 },
    },
  ],
  unhandled: [],
  preview: { before: '/b.png', after: '/a.png' },
  ...over,
})
const ready = (p: ChangePlan): ChangePlanView => ({ plan_id: 'p1', state: 'ready', plan: p }) as ChangePlanView

const FALLBACK: ScriptFallback = {
  project: 'pj_a',
  generation: 0,
  prompt: '把 y 轴改成对数坐标',
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
const tick = (ms = 1300) => vi.advanceTimersByTimeAsync(ms)
const start = vi.fn()
const onlyScriptPlan = {
  ...scriptPlan(),
  kind: undefined,
  groups: [],
  unhandled: [{ comment_id: 'a1', reason: 'needs_script' }],
} as unknown as ChangePlan

beforeEach(async () => {
  vi.useFakeTimers()
  createChangePlan.mockReset().mockResolvedValue({ plan_id: 'p1', state: 'running' })
  getChangePlan.mockReset()
  applyChangePlanScript.mockReset().mockResolvedValue({ backup: 'fig/1', after_sha256: 'bb' })
  undoChangePlanScript.mockReset().mockResolvedValue(undefined)
  restoreChangePlanScript.mockReset().mockResolvedValue(undefined)
  start.mockReset().mockResolvedValue('accepted')
  useAiStore.setState({ start: start as never })
  setCurrentProjectId('pj_a')
  cps().clear()
  await ds().switchDocument(emptyProject(), 'd_script_plan')
  seed()
})
afterEach(() => vi.useRealTimers())

/** 先有一张「整条都要改脚本」的图内方案卡，让它自动续走脚本方案 */
async function toScriptCard() {
  getChangePlan.mockResolvedValueOnce({ plan_id: 'p1', state: 'ready', plan: onlyScriptPlan, code: 'change_plan_empty' })
  const first = (await cps().requestFromAssistant('p1', { instruction: FALLBACK.prompt }, { ...FALLBACK, canvas: ds().activeCanvasId, generation: useAiStore.getState().generation })) as string
  getChangePlan.mockResolvedValue(ready(scriptPlan()))
  await tick()
  await tick()
  const key = cps().order.find((k) => k !== first) as string
  return { first, key }
}

describe('出脚本方案卡', () => {
  it('整条指令都要改脚本：先出脚本方案卡（mode=script），不直接改脚本；原卡只剩一行灰字', async () => {
    const { first, key } = await toScriptCard()
    expect(start).not.toHaveBeenCalled()
    const body = createChangePlan.mock.calls[1][0]
    expect(body).toMatchObject({ mode: 'script', source: 'assistant', instruction: FALLBACK.prompt, patches: BASE })
    expect(cps().entries[first].status).toBe('handoff')
    expect(cps().entries[key]).toMatchObject({ status: 'ready', from: 'assistant', instruction: FALLBACK.prompt })
    expect(summaryLine(cps().entries[key])).toContain('+1 −1')
  })

  it('这条路不适用（没连上能用的模型等）：悄悄落回现有的直接改脚本，指令一个字不丢', async () => {
    createChangePlan.mockImplementation(async (b: { mode?: string }) => {
      if (b.mode === 'script') throw new EngineError('x', '', 'change_plan_script_unsupported', '')
      return { plan_id: 'p1', state: 'running' }
    })
    getChangePlan.mockResolvedValue({ plan_id: 'p1', state: 'ready', plan: onlyScriptPlan, code: 'change_plan_empty' })
    await cps().requestFromAssistant('p1', { instruction: FALLBACK.prompt }, { ...FALLBACK, canvas: ds().activeCanvasId, generation: useAiStore.getState().generation })
    await tick()
    expect(start).toHaveBeenCalledOnce()
    expect(start.mock.calls[0][0]).toMatchObject({ prompt: FALLBACK.prompt, script: 'fig.py' })
  })

  it('脚本方案没有逐组勾选：整组要或不要；待确认状态与图内 override 一致才算「待确认」', async () => {
    const { key } = await toScriptCard()
    const e = cps().entries[key]
    expect(effectiveStatus(e, BASE)).toBe('ready')
    expect(effectiveStatus(e, [])).toBe('stale') // 图上的 override 变了：「改后」预览对不上
  })
})

it('「改脚本做剩下的」：脚本方案卡记着它接在哪张卡之后（只画一次用户气泡）', async () => {
  const { first, key } = await toScriptCard()
  expect(cps().entries[key].followsKey).toBe(first)
})

describe('应用与撤销（后端写脚本，不进文档历史）', () => {
  it('应用成功：卡片已应用、方案里记着备份；文档历史一步没动（⌘Z 不撤脚本）', async () => {
    const { key } = await toScriptCard()
    const before = ds().past.length
    expect(await cps().applyScript(key)).toBe('applied')
    expect(applyChangePlanScript).toHaveBeenCalledWith('p1')
    const e = cps().entries[key]
    expect(e.status).toBe('applied')
    expect(e.plan?.applied).toEqual({ backup: 'fig/1', after_sha256: 'bb' })
    expect(ds().past.length).toBe(before)
    // 脚本一改，图源修订变了：已应用的卡不会因此变成「图已改过」
    expect(effectiveStatus(e, BASE, { fileId: 'f_p1', script: 'fig.py', mtime: 999 })).toBe('applied')
  })

  it('store.apply 对脚本方案同样走脚本应用（不写文档）', async () => {
    const { key } = await toScriptCard()
    cps().apply(key)
    await tick(0)
    expect(applyChangePlanScript).toHaveBeenCalledOnce()
    expect(overridesOf()).toEqual(BASE)
  })

  it('脚本在生成之后被改过：不写，卡片转「图已改过」', async () => {
    const { key } = await toScriptCard()
    applyChangePlanScript.mockRejectedValue(new EngineError('x', '', 'change_plan_figure_changed', ''))
    expect(await cps().applyScript(key)).toBe('stale')
    expect(cps().entries[key].status).toBe('stale')
  })

  it('写入失败：一句灰字，脚本没动，状态仍待确认', async () => {
    const { key } = await toScriptCard()
    applyChangePlanScript.mockRejectedValue(new EngineError('x', '', 'change_plan_script_apply_failed', ''))
    expect(await cps().applyScript(key)).toBe('failed')
    expect(cps().entries[key]).toMatchObject({ status: 'ready', notice: 'apply_failed', scriptBusy: false })
  })

  it('撤销：按备份还原，同一份方案又是待确认；之后脚本又被改过 = 没替你撤销', async () => {
    const { key } = await toScriptCard()
    await cps().applyScript(key)
    expect(await cps().undoScript(key)).toBe(true)
    expect(cps().entries[key]).toMatchObject({ status: 'ready' })
    expect(cps().entries[key].plan?.applied).toBeNull()
    await cps().applyScript(key)
    undoChangePlanScript.mockRejectedValue(new EngineError('x', '', 'change_plan_script_undo_conflict', ''))
    expect(await cps().undoScript(key)).toBe(false)
    expect(cps().entries[key]).toMatchObject({ status: 'applied', notice: 'undo_failed' })
  })

  it('连点只发一次', async () => {
    const { key } = await toScriptCard()
    let release!: () => void
    applyChangePlanScript.mockReturnValue(new Promise((r) => (release = () => r({ backup: 'x', after_sha256: 'bb' }))))
    const a = cps().applyScript(key)
    expect(await cps().applyScript(key)).toBe('failed')
    release()
    await a
    expect(applyChangePlanScript).toHaveBeenCalledOnce()
  })
})

const overridesOf = () => (ds().doc.objects.find((o) => o.id === 'p1') as PanelObject).overrides

describe('失败终态：一句话 + 下一步', () => {
  it.each([
    'change_plan_script_check_failed',
    'change_plan_script_no_change',
    'change_plan_script_agent_failed',
    'change_plan_script_data_changed',
    'change_plan_script_no_preview',
    'change_plan_script_overreach',
  ])('%s → 再试一次（重新生成）', (code) => {
    expect(failureAction(code)).toBe('retry')
    expect(failureText(code, undefined, true)).not.toBe('')
  })

  it('越界：脚本被还原 / 没替你还原 / 只动了别的文件，各说清楚；文件名最多列 5 个，其余「等 N 个」', () => {
    expect(failureText('change_plan_script_overreach', { target_changed: true })).toContain('没有自动还原')
    expect(failureText('change_plan_script_overreach', { target_changed: true })).toContain('可能是你保存的')
    expect(failureText('change_plan_script_overreach', { files: ['a.txt'], total: 1 })).toContain('你的脚本没有被改动')
    expect(overreachFiles({ files: ['a.txt', 'b.txt'], total: 2 })).toBe('a.txt 和 b.txt')
    expect(overreachFiles({ files: ['a', 'b', 'c', 'd', 'e'], total: 7 })).toContain('等 7 个')
    expect(overreachFiles({ files: [], total: 0 })).toBe('')
  })

  it('失败后「再试一次」用同一份请求再来一份脚本方案', async () => {
    const { key } = await toScriptCard()
    getChangePlan.mockResolvedValue({ plan_id: 'p2', state: 'failed', code: 'change_plan_script_overreach', params: { files: ['x.txt'], total: 1, target_changed: false } })
    cps().patchEntry(key, { status: 'failed', failure: { code: 'change_plan_script_overreach' } })
    createChangePlan.mockClear()
    await cps().regenerate(key)
    expect(createChangePlan.mock.calls[0][0]).toMatchObject({ mode: 'script' })
  })
})

describe('生成期间脚本被改过（冲突）：不自动还原，用户点了才恢复', () => {
  const params = { target_changed: true, backup: 'fig/1', script: 'fig.py', current_sha256: 'cc', files: [], total: 0 }
  const conflictCard = async () => {
    getChangePlan.mockResolvedValue({ plan_id: 'p9', state: 'failed', code: 'change_plan_script_overreach', params })
    const key = (await cps().requestFromAssistant('p1', { instruction: FALLBACK.prompt }, { ...FALLBACK, canvas: ds().activeCanvasId, generation: useAiStore.getState().generation })) as string
    await tick()
    return key
  }

  it('主动作是「恢复到开始前的版本」，另给「保留现在的版本」；没有备份信息时才退回重试', async () => {
    expect(failureAction('change_plan_script_overreach', params)).toBe('restore')
    expect(failureAction('change_plan_script_overreach', { target_changed: true })).toBe('retry')
    expect(failureAction('change_plan_script_overreach', { target_changed: false, files: ['a'] })).toBe('retry')
  })

  it('点「恢复」才调后端，带冲突时看到的摘要；成功后收起这张卡', async () => {
    const key = await conflictCard()
    expect(restoreChangePlanScript).not.toHaveBeenCalled()
    expect(await cps().restoreScript(key)).toBe(true)
    expect(restoreChangePlanScript).toHaveBeenCalledWith({ backup: 'fig/1', script: 'fig.py', expect_sha256: 'cc' })
    expect(cps().entries[key]).toBeUndefined()
  })

  it('恢复被拒（脚本之后又变了）：一句灰字，卡片仍在，脚本没动', async () => {
    const key = await conflictCard()
    restoreChangePlanScript.mockRejectedValue(new EngineError('x', '', 'change_plan_script_restore_conflict', ''))
    expect(await cps().restoreScript(key)).toBe(false)
    expect(cps().entries[key]).toMatchObject({ status: 'failed', notice: 'restore_failed', scriptBusy: false })
  })
})

describe('图内方案这条路不适用：先试脚本方案，脚本方案也不适用才落回直接改脚本', () => {
  const ask = () => cps().requestFromAssistant('p1', { instruction: FALLBACK.prompt }, { ...FALLBACK, canvas: ds().activeCanvasId, generation: useAiStore.getState().generation })

  it('同步被拒（比如连接不支持结构化调用）→ 接着请求脚本方案（mode=script），出脚本方案卡', async () => {
    createChangePlan.mockImplementation(async (b: { mode?: string }) => {
      if (b.mode === 'script') return { plan_id: 'p1', state: 'running' }
      throw new EngineError('x', '', 'change_plan_unsupported_connection', '')
    })
    getChangePlan.mockResolvedValue(ready(scriptPlan()))
    const key = (await ask()) as string
    expect(createChangePlan.mock.calls.map((c) => c[0].mode)).toEqual([undefined, 'script'])
    await tick()
    expect(cps().entries[key]).toMatchObject({ status: 'ready', from: 'assistant' })
    expect(cps().entries[key].plan?.kind).toBe('script')
    expect(start).not.toHaveBeenCalled()
  })

  it('两条路都不适用 → fallthrough（调用方发现有的直接改脚本），不留卡', async () => {
    createChangePlan.mockRejectedValue(new EngineError('x', '', 'change_plan_unsupported_connection', ''))
    expect(await ask()).toBe('fallthrough')
    expect(createChangePlan).toHaveBeenCalledTimes(2)
    expect(cps().order).toEqual([])
  })

  it('线程里才判出的不适用码（轮询到 failed）→ 接着走 runScript：先脚本方案', async () => {
    getChangePlan.mockResolvedValueOnce({ plan_id: 'p1', state: 'failed', code: 'change_plan_unsupported_connection' })
    const first = (await ask()) as string
    getChangePlan.mockResolvedValue(ready(scriptPlan()))
    await tick()
    await tick()
    const key = cps().order.find((k) => k !== first) as string
    expect(createChangePlan.mock.calls[1][0]).toMatchObject({ mode: 'script' })
    expect(cps().entries[key].plan?.kind).toBe('script')
    expect(start).not.toHaveBeenCalled()
  })
})
