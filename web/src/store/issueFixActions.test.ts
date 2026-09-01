/**
 * 同一个属性上的多条修复计划**必须合并成一条**（PR #214 第三轮评审）。
 *
 * 挨个写的话后写的赢，而它可能违反前一条：默认规范上一条 6pt 图例文字同时
 * 命中 `font-below-absolute-floor`（算出 8.5）与 `legend-font-size`（算出
 * 8.0），8.0 后写、把 8.5 盖掉，而 8.0 仍然过不了绝对下限（判据是
 * `eff <= floor`）——「全部修复」报了两条修好，那条 error 还在。
 */
import { describe, expect, it } from 'vitest'
import type { FixPlan } from '@/lib/issueFix'
import { mergePlans } from './issueFixActions'

const override = (value: number, bound?: { min?: number; max?: number }): FixPlan => ({
  kind: 'override',
  objectId: 'p1',
  patches: [{ gid: 'legend_0', prop: 'fontsize', value }],
  bound,
})

const valueOf = (plan: FixPlan): unknown =>
  plan.kind === 'override' ? plan.patches[0].value : plan.kind === 'textSize' ? plan.sizePt : null

describe('同属性的计划合并', () => {
  it('取区间交集，结果同时满足两条规则（不是后写的赢）', () => {
    // 绝对下限：必须 > 8.0，所以最小合法档是 8.5；图例：[8.0, 9.0]
    const { plans, skipped } = mergePlans([
      override(8.5, { min: 8.5, max: 12 }),
      override(8.0, { min: 8.0, max: 9.0 }),
    ])
    expect(plans).toHaveLength(1)
    expect(skipped).toBe(0)
    const v = valueOf(plans[0]) as number
    expect(v, '后写的 8.0 赢了 = 绝对下限那条根本没修好').toBe(8.5)
    // 由构造保证：交集里的任何值都同时满足两条
    expect(v).toBeGreaterThanOrEqual(8.5)
    expect(v).toBeLessThanOrEqual(9.0)
  })

  it('降字号方向：夹进交集之后取到的是"改动最小"的那一端', () => {
    const { plans } = mergePlans([
      override(11, { min: 8.5, max: 11 }),
      override(9, { min: 8.0, max: 9 }),
    ])
    expect(valueOf(plans[0])).toBe(9)
  })

  it('两条规则互相矛盾时**整组不修**，并如实报出来', () => {
    const { plans, skipped } = mergePlans([
      override(12, { min: 12, max: 20 }),
      override(9, { min: 8, max: 9 }),
    ])
    expect(plans).toHaveLength(0)
    expect(skipped, '报"修好了"而它没好，比报一个修不了更坏').toBe(2)
  })

  it('给不出区间的也不合并（不假装知道怎么调和）', () => {
    const { plans, skipped } = mergePlans([override(8.5, { min: 8.5 }), override(8.0)])
    expect(plans).toHaveLength(0)
    expect(skipped).toBe(2)
  })

  it('不同属性 / 不同对象各走各的，一条都不许被合并掉', () => {
    const other: FixPlan = {
      kind: 'override',
      objectId: 'p1',
      patches: [{ gid: 'legend_0', prop: 'frameon', value: false }],
    }
    const another: FixPlan = {
      kind: 'override',
      objectId: 'p2',
      patches: [{ gid: 'legend_0', prop: 'fontsize', value: 8.5 }],
      bound: { min: 8.5 },
    }
    const { plans, skipped } = mergePlans([override(8.5, { min: 8.5 }), other, another])
    expect(plans).toHaveLength(3)
    expect(skipped).toBe(0)
  })

  it('页宽这类没有目标键的计划原样通过', () => {
    const page: FixPlan = { kind: 'pageWidth', widthMm: 85 }
    const { plans } = mergePlans([page])
    expect(plans).toEqual([page])
  })
})
