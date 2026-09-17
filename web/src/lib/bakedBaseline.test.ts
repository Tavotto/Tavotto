/**
 * `isJustBakedBaselineOf` 的三档语义：`baked_current` 的 `false` / `undefined` / `true`
 * 不许压成两档。通过 `renderTargets` 的那组用例（useEngineSync.test.ts）量的是消费点；
 * 这里直接量判据本体，免得哪天消费点换了路，判据被顺手「简化」成 `!baked_current`。
 */
import { describe, expect, it } from 'vitest'
import { isJustBakedBaselineOf } from './bakedBaseline'

const baked = [{ gid: 'g0', prop: 'color', value: '#000' }]
const same = [{ gid: 'g0', prop: 'color', value: '#000' }]
const other = [{ gid: 'g0', prop: 'color', value: '#fff' }]

describe('isJustBakedBaselineOf', () => {
  it('没有基线（缺席 / 空数组 / null）→ false', () => {
    expect(isJustBakedBaselineOf(same, undefined)).toBe(false)
    expect(isJustBakedBaselineOf(same, {})).toBe(false)
    expect(isJustBakedBaselineOf(same, { baked_overrides: [] })).toBe(false)
    expect(isJustBakedBaselineOf(same, { baked_overrides: null })).toBe(false)
  })

  it('逐字相等 + 基线有效 → true；逐字相等 + 老后端没发 baked_current → 也 true（维持旧行为）', () => {
    expect(isJustBakedBaselineOf(same, { baked_overrides: baked, baked_current: true })).toBe(true)
    expect(isJustBakedBaselineOf(same, { baked_overrides: baked })).toBe(true)
  })

  it('基线已失效（baked_current === false）→ false，哪怕逐字相等', () => {
    expect(isJustBakedBaselineOf(same, { baked_overrides: baked, baked_current: false })).toBe(false)
  })

  it('overrides 与基线不同 → false，与基线有效与否无关', () => {
    expect(isJustBakedBaselineOf(other, { baked_overrides: baked, baked_current: true })).toBe(false)
    expect(isJustBakedBaselineOf(other, { baked_overrides: baked, baked_current: false })).toBe(false)
    expect(isJustBakedBaselineOf([], { baked_overrides: baked, baked_current: true })).toBe(false)
  })

  it('「相等」是逐字的：顺序不同就不算（override 数组的 JSON 就是变体键）', () => {
    const two = [
      { gid: 'g0', prop: 'color', value: '#000' },
      { gid: 'g1', prop: 'lw', value: 2 },
    ]
    const swapped = [two[1], two[0]]
    expect(isJustBakedBaselineOf(two, { baked_overrides: two, baked_current: true })).toBe(true)
    expect(isJustBakedBaselineOf(swapped, { baked_overrides: two, baked_current: true })).toBe(false)
  })
})
