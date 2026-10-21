import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { ONBOARDING_FLOW_VERSION } from '@/store/onboardingStore'

/**
 * e2e 预置的 onboarding 状态必须跟着当前教程纪元走：低于它的 blob 会被迁回新手态，
 * 教程层挡住「老手版主页」类用例（#1269 CI 大面积红）。e2e 的 Node 侧不引应用源码，
 * 所以 fixtures 里抄了一份常量——这里看护两者相等，并禁止别处再手写字面版本号。
 */
const E2E_DIR = path.resolve(__dirname, '../../../e2e')

describe('e2e 的 onboarding flowVersion', () => {
  it('fixtures.ts 的常量等于应用源头的 ONBOARDING_FLOW_VERSION', () => {
    const src = readFileSync(path.join(E2E_DIR, 'fixtures.ts'), 'utf-8')
    const m = /export const ONBOARDING_FLOW_VERSION = (\d+)/.exec(src)
    expect(m, 'fixtures.ts 缺 ONBOARDING_FLOW_VERSION').not.toBeNull()
    expect(Number(m![1])).toBe(ONBOARDING_FLOW_VERSION)
  })

  it('除 tutorial.spec.ts（有意测旧版迁移）外，e2e 不手写 flowVersion 数字字面量', () => {
    const offenders: string[] = []
    for (const f of readdirSync(E2E_DIR)) {
      if (!/\.ts$/.test(f) || f === 'tutorial.spec.ts') continue
      const lines = readFileSync(path.join(E2E_DIR, f), 'utf-8').split('\n')
      lines.forEach((l, i) => {
        if (/flowVersion:\s*\d+/.test(l)) offenders.push(`${f}:${i + 1}`)
      })
    }
    expect(offenders).toEqual([])
  })
})
