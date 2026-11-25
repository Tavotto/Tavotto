/**
 * 修改方案应用的同源对（ADR 0206）：前端 `applyGroups` / `canonicalPatches` ↔ 后端 `changeplan.apply_groups`。
 * 两侧各读 `tests/golden/change_plan_apply_vectors.json`，后端那一侧是 `tests/test_changeplan_golden.py`。
 */
import { describe, expect, it } from 'vitest'

import golden from '../../../tests/golden/change_plan_apply_vectors.json'
import { applyGroups, canonicalPatches, samePatches } from './changePlanApply'

describe('applyGroups（同源对）', () => {
  it('每条向量与后端期望逐条一致', () => {
    expect(golden.cases.length).toBeGreaterThanOrEqual(10)
    for (const c of golden.cases) {
      expect(applyGroups(c.base, c.groups as never, c.keys), c.name).toEqual(c.expected)
    }
  })

  it('期望值本身是规范形', () => {
    for (const c of golden.cases) expect(canonicalPatches(c.expected), c.name).toEqual(c.expected)
  })
})

describe('samePatches', () => {
  it('顺序、重复、嵌套键序不同的等价写法是同一件事', () => {
    const a = [
      { gid: 'b', prop: 'p', value: { x: 1, y: [1, 2] } },
      { gid: 'a', prop: 'p', value: 1 },
      { gid: 'a', prop: 'p', value: 2 },
    ]
    const b = [
      { gid: 'a', prop: 'p', value: 2 },
      { gid: 'b', prop: 'p', value: { y: [1, 2], x: 1 } },
    ]
    expect(samePatches(a, b)).toBe(true)
    expect(samePatches(a, [{ gid: 'a', prop: 'p', value: 3 }])).toBe(false)
  })
})
