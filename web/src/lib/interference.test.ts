import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/interference_vectors.json'
import {
  BBOX_BASIS_ROLES,
  DATA_ROLES,
  DECORATION_ROLES,
  INTERFERENCE_CHECK_IDS,
  RECT_BASIS_ROLES,
  TEXT_ROLES,
  MIN_OVERLAP_MM,
  detectInterference,
  pyFixed,
  pyRound,
} from './interference'
import { loadProfile } from './profile'
import type { Manifest } from './api'

/**
 * 干涉检测的**跨语言等价性**看护（V-011）。判据只有 `engine/interference.py` 一份，
 * `lib/interference.ts` 是逐行移植；两边跑同一份 `tests/golden/interference_vectors.json`
 * （`python scripts/gen_interference_vectors.py --write` 按 Python 侧生成，
 * `tests/test_preflight_interference.py` 断言同一份）。比 detect 的逐对输出，不比成文 text。
 */

interface Case {
  name: string
  profile_id: string | null
  panel_id: string
  manifest: unknown
  expected: unknown[]
}

const data = vectors as unknown as { constants: Record<string, unknown>; cases: Case[] }

describe('interference golden vectors（与 engine/interference.py 逐对对齐）', () => {
  it('向量文件非空，三条检查都出现过', () => {
    expect(data.cases.length).toBeGreaterThan(15)
    const ids = new Set(data.cases.flatMap((c) => (c.expected as { id: string }[]).map((e) => e.id)))
    expect([...ids].sort()).toEqual([...INTERFERENCE_CHECK_IDS].sort())
  })

  it('常量与 Python 同值', () => {
    expect(MIN_OVERLAP_MM).toBe(data.constants.MIN_OVERLAP_MM)
    expect(INTERFERENCE_CHECK_IDS).toEqual(data.constants.CHECK_IDS)
    expect(DECORATION_ROLES).toEqual(data.constants.DECORATION_ROLES)
    expect(TEXT_ROLES).toEqual(data.constants.TEXT_ROLES)
    expect(DATA_ROLES).toEqual(data.constants.DATA_ROLES)
    expect(BBOX_BASIS_ROLES).toEqual(data.constants.BBOX_BASIS_ROLES)
    expect(RECT_BASIS_ROLES).toEqual(data.constants.RECT_BASIS_ROLES)
  })

  for (const c of data.cases) {
    it(c.name, () => {
      const profile = c.profile_id ? loadProfile(c.profile_id) : null
      expect(detectInterference(c.manifest as Manifest, profile, c.panel_id)).toEqual(c.expected)
    })
  }
})

describe('pyFixed / pyRound 与 Python 的取整一致', () => {
  it.each([
    [0.25, 1, '0.2'],
    [0.35, 1, '0.3'], // 0.35 的二进制值略小于 0.35
    [1.25, 1, '1.2'],
    [0.75, 1, '0.8'],
    [0.05, 1, '0.1'], // 0.05 的二进制值略大于 0.05
    [2.675, 2, '2.67'],
    [9.96, 1, '10.0'],
    [-0.0625, 3, '-0.062'],
    [-0.0001, 3, '-0.000'],
    [16, 1, '16.0'],
  ])('format(%s, .%sf) = %s', (x, nd, want) => {
    expect(pyFixed(x, nd)).toBe(want)
  })

  it('round(-0.0001, 3) 是 -0.0（JSON 往返后与 Python 相同）', () => {
    expect(Object.is(pyRound(-0.0001, 3), -0)).toBe(true)
    expect(pyRound(4.6875, 3)).toBe(4.688)
    expect(pyRound(0.9375, 3)).toBe(0.938)
  })
})
