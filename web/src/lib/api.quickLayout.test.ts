/** 一键排版响应的形状核对：规范改动码是封闭集合，没见过的码 = 整份响应形状不对（与备注码同一风格） */
import { afterEach, describe, expect, it } from 'vitest'
import { quickLayoutPlan, type QuickLayoutRequest } from './api'

const realFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = realFetch
})

const req: QuickLayoutRequest = {
  panels: [],
  fixed: [],
  page: { w: 100, h: 100, margin: 0 },
  target: 'double',
  gap_mm: 3,
  profile: {},
  label_pt: 8,
  fix_spec: true,
  columns: null,
  canvas: 'fit',
}
const okBody = (extra: Record<string, unknown> = {}) => ({
  ok: true,
  panels: [],
  labels: [],
  notes: [],
  spec_changes: [],
  page: { w: 100, h: 50 },
  replay_required: false,
  ...extra,
})
const serve = (body: unknown) => {
  globalThis.fetch = (async () => new Response(JSON.stringify(body), { status: 200 })) as typeof fetch
}

describe('quickLayoutPlan 形状核对', () => {
  it('合法的规范改动码通过', async () => {
    serve(okBody({ spec_changes: ['font_family', 'font_size', 'line_width', 'tick_direction', 'legend_frame', 'text_weight', 'other'] }))
    const res = await quickLayoutPlan(req)
    expect(res.ok && res.spec_changes.length).toBe(7)
  })

  it('没见过的规范改动码：整份当形状不对', async () => {
    serve(okBody({ spec_changes: ['font_size', 'colour'] }))
    await expect(quickLayoutPlan(req)).rejects.toMatchObject({ code: 'bad_shape' })
  })

  it('缺 spec_changes / 不是数组：形状不对', async () => {
    const { spec_changes: _drop, ...rest } = okBody()
    serve(rest)
    await expect(quickLayoutPlan(req)).rejects.toMatchObject({ code: 'bad_shape' })
    serve(okBody({ spec_changes: 'font_size' }))
    await expect(quickLayoutPlan(req)).rejects.toMatchObject({ code: 'bad_shape' })
  })

  it('grid：缺席 / null 都通过（缺席归一成 null）；自洽的网格通过', async () => {
    serve(okBody())
    const a = await quickLayoutPlan(req)
    expect(a.ok && a.grid).toBeNull()
    serve(okBody({ grid: null }))
    expect((await quickLayoutPlan(req)) as { grid: unknown }).toMatchObject({ grid: null })
    const grid = { rows: 1, cols: 2, cells: { a: [0, 0], b: [0, 1] }, col_ratios: [1, 1], row_ratios: [1] }
    serve(okBody({ grid }))
    expect((await quickLayoutPlan(req)) as { grid: unknown }).toMatchObject({ grid })
  })

  it('grid 形状不对（比例数与行列数不符 / 格子越界 / 非正权重）：整份当形状不对', async () => {
    const bad = [
      { rows: 1, cols: 2, cells: { a: [0, 0] }, col_ratios: [1], row_ratios: [1] },
      { rows: 1, cols: 2, cells: { a: [0, 2] }, col_ratios: [1, 1], row_ratios: [1] },
      { rows: 1, cols: 2, cells: { a: [0, 0] }, col_ratios: [1, 0], row_ratios: [1] },
      { rows: 1, cols: 2, cells: 'x', col_ratios: [1, 1], row_ratios: [1] },
    ]
    for (const grid of bad) {
      serve(okBody({ grid }))
      await expect(quickLayoutPlan(req)).rejects.toMatchObject({ code: 'bad_shape' })
    }
  })

  it('失败码 page_too_small 在闭集里；没见过的码不在', async () => {
    serve({ ok: false, code: 'page_too_small' })
    expect(await quickLayoutPlan(req)).toEqual({ ok: false, code: 'page_too_small' })
    serve({ ok: false, code: 'nope' })
    await expect(quickLayoutPlan(req)).rejects.toMatchObject({ code: 'bad_shape' })
  })
})
