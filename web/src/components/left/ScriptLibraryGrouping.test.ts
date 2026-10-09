/**
 * 脚本库分组：准备结局只管「这次的」。比最近一次注册表刷新旧的结局不压过刷新后权威的 `linked`
 * （Codex #820 r4234219380）：两个方向——失败后脚本被别处跑通（该进已关联），旧成功被后端撤销（不该留在已关联）。
 */
import { describe, expect, it } from 'vitest'

import type { ScriptInventoryEntry } from '@/lib/api'
import type { PrepEntry } from '@/store/projectPreparationStore'

import { groupOf } from './ScriptLibrary'

const entry = (over: Partial<ScriptInventoryEntry>): ScriptInventoryEntry => ({
  script: 'a.py',
  registered: true,
  linked: true,
  static_stems: [],
  entry_candidates: ['__main__'],
  reason: 'no_static_output',
  can_probe: true,
  ...over,
})

const prep = (outcome: { kind: string }, reportAt?: number, captured = 1): PrepEntry =>
  ({
    report: { outcome, captured: Array.from({ length: captured }, () => ({})) },
    reportAt,
  }) as unknown as PrepEntry

describe('脚本库分组：准备结局 vs 刷新后的注册表', () => {
  it('结局比刷新新：照旧用结局（失败 -> 需要处理；成功 -> 已关联）', () => {
    expect(groupOf(entry({}), undefined, prep({ kind: 'failed' }, 2000), 1000)).toBe('needsFix')
    expect(groupOf(entry({ linked: false }), undefined, prep({ kind: 'succeeded' }, 2000), 1000)).toBe('linked')
  })

  it('旧的失败结局 + 刷新后后端说已关联（脚本后来被 MCP / CLI 跑通）：进已关联，不留在需要处理', () => {
    expect(groupOf(entry({ linked: true }), undefined, prep({ kind: 'failed' }, 1000), 2000)).toBe('linked')
  })

  it('旧的成功结局 + 刷新后后端说没关联：不再算已关联', () => {
    expect(groupOf(entry({ registered: true, linked: false }), undefined, prep({ kind: 'succeeded' }, 1000), 2000)).toBe(
      'notRun',
    )
  })

  it('没有落地时刻的老条目 / 还没刷新过：结局照旧生效', () => {
    expect(groupOf(entry({}), undefined, prep({ kind: 'failed' }, undefined), 2000)).toBe('needsFix')
    expect(groupOf(entry({}), undefined, prep({ kind: 'failed' }, 500), 0)).toBe('needsFix')
  })
})
