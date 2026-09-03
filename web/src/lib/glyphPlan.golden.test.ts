import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/glyph_plan_vectors.json'
import { missingChars, planRuns } from './glyphPlan'

/**
 * 字形归属计划的**跨语言等价性**看护。
 *
 * 算法有两份（`src/tavotto/glyphplan.py` 与 `./glyphPlan.ts`），而 oracle
 * 还**不同源**：Python 侧问真字体，这一侧读生成的覆盖表。所以这份向量同时
 * 在看两件事——算法一致，以及那张表还配得上真字体。分叉的症状是「预览说
 * 这个字画得出、导出上是个方框」，而它只在某些字符上发作。
 *
 * 向量由 `python scripts/gen_glyph_plan_vectors.py --write` 按 Python 侧生成，
 * pytest 的 `tests/test_glyph_plan.py` 跑的是同一份。
 */
describe('字形计划两侧一致', () => {
  // 计划与族无关（三个族共用一张 primary 覆盖表，pytest 那侧量过这条承诺），
  // 但向量仍逐族生成——族一旦变成一个变量，这里立刻就会红。
  for (const vec of vectors.vectors) {
    it(vec.name, () => {
      expect(planRuns(vec.text)).toEqual(vec.runs)
      expect(missingChars(vec.text)).toEqual(vec.missing)
    })
  }
})
