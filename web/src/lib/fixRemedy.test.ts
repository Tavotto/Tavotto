/**
 * 智能修复 remedy 的前后端同源对（ADR 0148）：读 `tests/golden/fix_remedy_vectors.json`，不读后端源码；
 * 后端那一侧是 `tests/test_fixrouter_pair.py`（用 `fixrouter.choose` 逐条复算同一份向量，并读本文件旁 `fixRemedy.ts` 的字段表）。
 */
import { describe, expect, it } from 'vitest'
import golden from '../../../tests/golden/fix_remedy_vectors.json'
import { isRemedy, REMEDY_FIELDS, REMEDY_KINDS } from './fixRemedy'

describe('remedy 的种类闭集与字段表', () => {
  it('种类 = golden 的 kinds（含顺序），字段 = golden 的 fields', () => {
    expect(REMEDY_KINDS).toEqual(golden.kinds)
    const sorted = (o: Record<string, readonly string[]>) =>
      Object.fromEntries(Object.entries(o).map(([k, v]) => [k, [...v].sort()]))
    expect(sorted(REMEDY_FIELDS)).toEqual(sorted(golden.fields))
  })

  it('golden 里每条后端产出的 remedy 前端都认得', () => {
    expect(golden.vectors.length).toBeGreaterThan(0)
    for (const v of golden.vectors) expect(isRemedy(v.remedy), v.name).toBe(true)
  })

  it('认不得的形状一律不是 remedy（旧前端读到新种类、缺字段、多字段都当没有）', () => {
    expect(isRemedy(null)).toBe(false)
    expect(isRemedy({ kind: 'teleport' })).toBe(false)
    expect(isRemedy({ kind: 'install_packages', packages: ['a'] })).toBe(false)
    expect(isRemedy({ kind: 'ai_fix', extra: 1 })).toBe(false)
  })
})
