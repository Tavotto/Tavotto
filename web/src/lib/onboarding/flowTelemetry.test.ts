/**
 * 教程步骤的遥测（ADR 0041 §4）：只在**真的完成**时记 step_id + 流程版本；跳过不记；
 * 最后一步之后另记一条 tutorial_completed；没同意一个字节都不发。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  postTelemetryEvent: vi.fn().mockResolvedValue(undefined),
}))

import { postTelemetryEvent } from '@/lib/api'
import { setTelemetryEnabled } from '@/lib/telemetry'
import { configureOnboardingPersistence, ONBOARDING_FLOW_VERSION, useOnboardingStore } from '@/store/onboardingStore'
import { completeStep, skipStep } from './flow'
import { STEP_IDS } from './stepIds'

const post = vi.mocked(postTelemetryEvent)

beforeEach(() => {
  configureOnboardingPersistence(null)
  useOnboardingStore.getState().resetOnboarding()
  useOnboardingStore.getState().start({ projectId: 'p_tut', documentId: 'tavotto-tutorial' })
  post.mockClear()
  setTelemetryEnabled(true)
})

describe('tutorial_step_completed', () => {
  it('完成一步 = 一条，只带 step_id 与 tutorial_version', () => {
    completeStep('welcome')
    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('tutorial_step_completed', {
      step_id: 'welcome',
      tutorial_version: ONBOARDING_FLOW_VERSION,
    })
    expect(JSON.stringify(post.mock.calls)).not.toContain('p_tut')
  })

  it('跳过一步：状态机照样前进，遥测不记', () => {
    skipStep()
    expect(useOnboardingStore.getState().completedSteps).toContain('welcome')
    expect(useOnboardingStore.getState().currentStep).toBe('open_fast_edit')
    expect(post).not.toHaveBeenCalled()
  })

  it('走完最后一步：最后一条 step 之后再一条 tutorial_completed', () => {
    for (const id of STEP_IDS) completeStep(id)
    expect(useOnboardingStore.getState().status).toBe('completed')
    const events = post.mock.calls.map(([e]) => e)
    expect(events.filter((e) => e === 'tutorial_step_completed')).toHaveLength(STEP_IDS.length)
    expect(events.at(-1)).toBe('tutorial_completed')
    expect(post).toHaveBeenLastCalledWith('tutorial_completed', {
      tutorial_version: ONBOARDING_FLOW_VERSION,
    })
  })

  it('每个 step_id 都是后端白名单认识的那十个之一（闭集同源）', () => {
    // 后端枚举在 tests/test_telemetry_integrations.py 里对着这份文件比；这里守前端那半
    expect([...STEP_IDS]).toEqual([
      'welcome',
      'open_fast_edit',
      'select_text',
      'change_typography',
      'locate_problem',
      'export_original',
      'add_to_layout',
      'multi_select_align',
      'export_canvas',
      'done',
    ])
  })

  it('没同意：完成再多步也一个字节不发', () => {
    setTelemetryEnabled(false)
    completeStep('welcome')
    completeStep('open_fast_edit')
    expect(post).not.toHaveBeenCalled()
  })
})
