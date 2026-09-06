/**
 * 类型切换与导出载荷的**生产者那一侧**（cap-shape-switch）。
 *
 * 换类型改的是**字段集合**，而后端 `_draw_shape` / `_draw_arrow` 各自只读得懂
 * 一组字段——切完之后多一个它不读的、少一个它必须有的（箭头的 `start` /
 * `end`），前端一点异常都看不出来，导出那一刻才炸。
 *
 * 所以这一对由**共享向量**接住：`tests/golden/shape_switch_payloads.json` 是唯一
 * 出处，这里断言「前端确实产出它」，`tests/test_compose_switched_shapes.py`
 * 断言「后端确实画得出它」。**两侧都不重新实现对方那一半**——在 pytest 里手抄
 * 一份「切换之后大概长这样」的载荷，抄错了就是自己捏出来的假红/假绿，而它比
 * 没有用例更难发现。
 */
import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/shape_switch_payloads.json'
import { switchObject, type SwitchKind } from './shapeSwitch'
import { toExportObjects } from './exportPayload'
import type { CanvasObject } from '@/types/document'

/** 载荷经 `JSON.stringify` 才上线：`undefined` 的字段根本不会被发出去 */
const wire = (o: CanvasObject) => JSON.parse(JSON.stringify(toExportObjects([o])[0]))

describe.each(vectors.chains)('$name：$comment', (chain) => {
  it('每一步的导出载荷与向量逐字段相同', () => {
    let cur = chain.from as unknown as CanvasObject
    expect(wire(cur)).toEqual(chain.payloads[0])
    chain.steps.forEach((step, i) => {
      const next = switchObject(cur, step as SwitchKind)
      expect(next).not.toBeNull()
      cur = next!
      expect(wire(cur)).toEqual(chain.payloads[i + 1])
    })
  })

  it('向量的步数与载荷数对得上（少一条载荷 = 少验一步，用例会安静地缩水）', () => {
    expect(chain.payloads).toHaveLength(chain.steps.length + 1)
  })
})
