/**
 * 版面文件（schema 2 形状）里的样式绑定（ADR 0081）要带过 `normalizeLayout`：
 * 打开一份绑定的版面再存一次，绑定不能静默没了（Codex #547 P1）。
 */
import { describe, expect, it } from 'vitest'
import { normalizeLayout } from './migrate'

describe('normalizeLayout：schema 2 的样式绑定', () => {
  it('形状对的绑定原样带过来', () => {
    const doc = normalizeLayout(
      {
        schema: 2,
        name: '版面',
        page: { w: 80, h: 60 },
        objects: [],
        guides: [],
        style: { id: 's1', snapshot: { element: { title: { fontsize: 9 } }, pt_basis: 'page' } },
      },
      '版面',
    )
    expect('style' in doc && doc.style).toEqual({
      id: 's1',
      snapshot: { element: { title: { fontsize: 9 } }, pt_basis: 'page' },
    })
  })

  it('「已脱离」标记跟着带过来；不是 true 的不收', () => {
    const load = (detached: unknown) =>
      normalizeLayout(
        { schema: 2, name: '版面', page: { w: 80, h: 60 }, objects: [], guides: [], style: { id: 's1', snapshot: {}, detached } },
        '版面',
      )
    expect((load(true) as { style?: unknown }).style).toEqual({ id: 's1', snapshot: {}, detached: true })
    expect((load('yes') as { style?: unknown }).style).toEqual({ id: 's1', snapshot: {} })
  })

  it('样式写的 override 登记（owned，ADR 0081 §十三）跟着带过来；不是对象的不收', () => {
    const owned = { 'p1@f1': { 'axes_0.title': { fontsize: { value: 8, base: 10 } } } }
    const load = (o: unknown) =>
      normalizeLayout(
        { schema: 2, name: '版面', page: { w: 80, h: 60 }, objects: [], guides: [], style: { id: 's1', snapshot: {}, owned: o } },
        '版面',
      ) as { style?: unknown }
    expect(load(owned).style).toEqual({ id: 's1', snapshot: {}, owned })
    expect(load([1]).style).toEqual({ id: 's1', snapshot: {} })
  })

  it('形状不对的不收（没有 id / snapshot 不是对象）', () => {
    const doc = normalizeLayout({ schema: 2, page: { w: 80, h: 60 }, objects: [], style: { snapshot: 3 } }, 'x')
    expect('style' in doc ? doc.style : undefined).toBeUndefined()
  })
})
