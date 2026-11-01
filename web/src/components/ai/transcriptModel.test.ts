/**
 * 「错误码 → 失败卡主按钮」的前后端同源对：读 `tests/golden/ai_error_action_vectors.json`，不读后端源码；
 * 后端那一侧是 `tests/test_ai_cli_error_classes.py`（逐条复算 CLI 事件 → 错误码，并要求表覆盖 RUNNER_ERRORS）。
 */
import { describe, expect, it } from 'vitest'
import golden from '../../../../tests/golden/ai_error_action_vectors.json'
import { AGENT_ERRORS, AGENT_ERROR_ACTION, fixedByConnection } from './transcriptModel'

describe('AGENT_ERROR_ACTION', () => {
  it('与 golden 的 actions 逐项相等（含键集合）', () => {
    expect(AGENT_ERROR_ACTION).toEqual(golden.actions)
  })
  it('覆盖前端认得的每个 agent 错误码', () => {
    expect(Object.keys(AGENT_ERROR_ACTION).sort()).toEqual([...AGENT_ERRORS].sort())
  })
  it('fixedByConnection 只由码表决定', () => {
    for (const [k, a] of Object.entries(golden.actions))
      expect(fixedByConnection(k as never), k).toBe(a === 'check_connection')
    expect(fixedByConnection(null)).toBe(false)
  })
})
