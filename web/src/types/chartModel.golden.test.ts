// golden vectors 腿：与 pytest（tests/test_chart_model_vectors.py）共跑同一份
// tests/golden/chart_model_vectors.json，断言两侧同一「接受 / 拒绝 + 同码」。
import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/chart_model_vectors.json'
import { validateChartBinding, validateChartModel } from './chartModel'

const caseList = vectors.vectors as {
  name: string
  model: unknown
  binding: unknown
  expect: string
}[]

describe('chart model golden vectors', () => {
  for (const v of caseList) {
    it(v.name, () => {
      const modelVerdict = validateChartModel(v.model)
      if (v.expect.startsWith('binding:')) {
        // N4 legend 合同等向量：模型本身合法，判据落在 validateChartBinding。
        expect(modelVerdict).toEqual({ ok: true })
        expect(validateChartBinding(v.binding, v.model as never)).toEqual({
          ok: false,
          code: v.expect.slice('binding:'.length),
        })
        return
      }
      if (v.expect === 'ok') {
        expect(modelVerdict).toEqual({ ok: true })
        const bindingVerdict = validateChartBinding(v.binding, v.model as never)
        expect(bindingVerdict).toEqual({ ok: true })
        return
      }
      if (v.expect === 'chart_binding_stale' || v.expect === 'chart_source_mismatch') {
        expect(modelVerdict).toEqual({ ok: true })
        expect(validateChartBinding(v.binding, v.model as never)).toEqual({
          ok: false,
          code: v.expect,
        })
        return
      }
      expect(modelVerdict).toEqual({ ok: false, code: v.expect })
    })
  }

  it('vectors file is the shared one and non-empty', () => {
    expect(vectors.schema).toBe(1)
    expect(caseList.length).toBeGreaterThan(5)
  })
})
