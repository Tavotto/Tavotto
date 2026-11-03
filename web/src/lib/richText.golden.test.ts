import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/rich_text_vectors.json'
import {
  GLANCE_ACCENTS,
  GLANCE_DROP,
  GLANCE_FUNCS,
  GLANCE_SPACES,
  GLANCE_SYMBOLS,
  GLANCE_UNWRAP,
  glanceText,
  legacyToLatex,
  parseRuns,
  plainText,
  serializeRuns,
  type TextFormat,
} from './richText'

/**
 * 标注两种写法（旧标记 / LaTeX 子集，ADR 0152）的**跨语言等价性**看护。
 *
 * 解析有两份（`src/tavotto/richtext.py` 给导出与预检，`./richText.ts` 给画布与编辑框）；
 * 分叉的症状是「编辑框里是斜体、导出上是正体」。向量由
 * `python scripts/gen_rich_text_vectors.py --write` 按 Python 侧生成，
 * pytest 的 `tests/test_rich_text_vectors.py` 跑的是同一份。
 */
describe('标注写法两侧一致', () => {
  for (const vec of vectors.vectors) {
    const format = (vec.format ?? undefined) as TextFormat | undefined
    it(vec.name, () => {
      const runs = parseRuns(vec.text, format)
      expect(runs).toEqual(vec.runs)
      expect(serializeRuns(runs, 'latex')).toBe(vec.latex)
      expect(plainText(vec.text, format)).toBe(vec.plain)
      expect(glanceText(vec.text, format)).toBe(vec.glance)
      // 规范形再读一遍，片段不变（编辑框写回后画出来的就是写回前看到的）
      expect(parseRuns(vec.latex, 'latex').map(({ raw: _raw, ...r }) => r)).toEqual(
        runs.map(({ raw: _raw, ...r }) => r),
      )
      if (!format) expect(legacyToLatex(vec.text)).toBe(vec.latex)
    })
  }
})

describe('小缩略图的读法两侧同表', () => {
  it('glance 的每张表与 Python 侧逐项相同', () => {
    expect(GLANCE_SYMBOLS).toEqual(vectors.glance.symbols)
    expect(GLANCE_FUNCS).toEqual(vectors.glance.funcs)
    expect(GLANCE_UNWRAP).toEqual(vectors.glance.unwrap)
    expect(GLANCE_ACCENTS).toEqual(vectors.glance.accents)
    expect(GLANCE_SPACES).toEqual(vectors.glance.spaces)
    expect(GLANCE_DROP).toEqual(vectors.glance.drop)
  })
})
