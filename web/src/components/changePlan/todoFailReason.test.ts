import { describe, expect, it } from 'vitest'
import type { PlanEntry } from '@/store/changePlanStore'
import { todoFailReason } from './changePlanModel'

const failed = (agent_reply?: string) =>
  ({ status: 'failed', failure: { code: 'change_plan_script_no_change', ...(agent_reply ? { params: { agent_reply } } : {}) } }) as unknown as Pick<PlanEntry, 'status' | 'failure' | 'notice'>

describe('并进建议卡的「为什么没做成」', () => {
  it('第一段是过场话时取「原因：」那段，去掉 Markdown 记号与「原因：」前缀（真机 2026-11-27）', () => {
    const said = '我读完了 `fig1_kinetics.py`，这里需要先停下确认，不能直接改。\n\n原因：脚本里**没有任何实测数据**。\n\n如果你给我数据……'
    expect(todoFailReason(failed(said))).toBe('脚本里没有任何实测数据。')
  })
  it('Agent 照要求第一句就说原因：取第一段', () => {
    expect(todoFailReason(failed('脚本里没有实测数据，没法画误差棒。\n\n给我数据我再改。'))).toBe('脚本里没有实测数据，没法画误差棒。')
  })
  it('去掉 Agent 自己写的「未做改动：」前缀', () => {
    expect(todoFailReason(failed('未做改动：脚本里没有误差数据。'))).toBe('脚本里没有误差数据。')
  })
  it('太长截断加省略号；没回话用卡片本来那句失败文案', () => {
    expect(todoFailReason(failed('长'.repeat(300))).endsWith('…')).toBe(true)
    expect(todoFailReason(failed()).length).toBeGreaterThan(0)
  })
})
