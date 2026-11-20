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
})
