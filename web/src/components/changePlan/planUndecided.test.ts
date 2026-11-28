import { describe, expect, it } from 'vitest'
import { editSteps } from '@/components/ai/Transcript'
import type { AiSession } from '@/store/aiStore'
import { planUndecided } from './changePlanModel'

type E = Parameters<typeof planUndecided>[0]
const base = { from: 'assistant', kind: 'panel', status: 'generating', plan: undefined, revising: undefined, request: { mode: 'auto' }, phase: undefined } as unknown as E

describe('助手来源：模型回话之前不画方案卡（用户 2026-11-27：聊天会先闪一张卡）', () => {
  it('生成中、还没方案、没到出修改后的图：只画思考那一行', () => {
    expect(planUndecided(base)).toBe(true)
    expect(planUndecided({ ...base, phase: 'model' } as E)).toBe(true)
    // 方案回来是聊天 / 提问（整条 out_of_scope）、正在交给普通对话：还是那一行，不闪卡（真机：交接要等发送落定）
    expect(planUndecided({ ...base, plan: { groups: [], unhandled: [{ comment_id: 'a1', reason: 'out_of_scope' }] } } as unknown as E)).toBe(true)
  })
  it('方案回来开始出修改后的图 / 脚本方案 / 已有方案 / 审阅来源：照旧画卡', () => {
    expect(planUndecided({ ...base, phase: 'render_after' } as E)).toBe(false)
    expect(planUndecided({ ...base, phase: 'agent' } as E)).toBe(false)
    expect(planUndecided({ ...base, request: { mode: 'script' } } as unknown as E)).toBe(false)
    expect(planUndecided({ ...base, plan: { groups: [], unhandled: [{ comment_id: 'a1', reason: 'needs_script' }] } } as unknown as E)).toBe(false)
    expect(planUndecided({ ...base, from: 'review' } as unknown as E)).toBe(false)
    expect(planUndecided({ ...base, status: 'failed' } as E)).toBe(false)
    expect(planUndecided({ ...base, revising: '' } as E)).toBe(false)
  })
})

describe('聊天落回的一轮不画改脚本清单', () => {
  const s = (x: Partial<AiSession>) => ({ status: 'running', changed: false, ...x }) as AiSession
  it('只是回答（还在读）：不画；真动了脚本：照常画', () => {
    expect(editSteps(s({ chat: true }))).toBeNull()
    expect(editSteps(s({ chat: true, stage: { step: 'read', round: 1 } }))).toBeNull()
    // Agent 一退出后端就推到「出图核对」，没改脚本也一样：不画
    expect(editSteps(s({ chat: true, stage: { step: 'verify', round: 1 } }))).toBeNull()
    expect(editSteps(s({ chat: true, chatEdited: true, stage: { step: 'verify', round: 1 } }))).toHaveLength(3)
    expect(editSteps(s({ chat: true, stage: { step: 'edit', round: 1 } }))).toHaveLength(3)
    expect(editSteps(s({}))).toHaveLength(3)
  })
})
