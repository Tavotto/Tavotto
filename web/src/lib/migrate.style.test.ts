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

describe('版本边界', () => {
  it('拒绝未来版本，不将其当作旧版空布局', () => {
    expect(() => normalizeLayout({ schema: 4, canvases: [{ id: 'future' }] }, 'future')).toThrow()
  })
  it('保留旧版与当前版本的读取', () => {
    expect(normalizeLayout({ schema: 1, objs: [] }, 'old').schema).toBe(2)
    expect(normalizeLayout({ schema: 2, objects: [] }, 'single').schema).toBe(2)
    expect(normalizeLayout({ schema: 3, project: { id:'p', name:'p' }, canvases: [{id:'c', objects:[]}], activeCanvasId:'c' }, 'multi').schema).toBe(3)
  })
})

describe('显式异常版本', () => {
  it.each(['4.0', '4.5', '3.5', '1.5', '"4"', '{}', 'true', 'null', '-1', '0'])('拒绝 JSON schema %s', (value) => {
    const raw = JSON.parse(`{"schema":${value},"objs":[]}`)
    expect(() => normalizeLayout(raw, 'odd')).toThrow()
  })
  it('无 schema 的 v1 仍可读取', () => {
    expect(normalizeLayout({objs: []}, 'old').schema).toBe(2)
  })
})

describe('受损与未声明版本的混合结构', () => {
  it.each([
    [{ type:'text', text:'DO-NOT-LOSE' }], null,
    {schema:3, canvases:[], future:'DO-NOT-LOSE'},
    {schema:2, objects:{first:'DO-NOT-LOSE'}},
    {project:{name:'DO-NOT-LOSE'}, canvases:[], activeCanvasId:'c'},
    {objects:[{type:'text',text:'DO-NOT-LOSE'}]},
    {objs:{first:'DO-NOT-LOSE'}},
    {schema:2,objects:[{type:'text',text:'kept'},{text:'DO-NOT-LOSE'}]},
    {objs:[{type:'text',text:'kept'},{type:'unknown',text:'DO-NOT-LOSE'}]},
  ].map((payload) => [payload]))('拒绝不能安全迁移的结构 %#', (payload) => {
    expect(() => normalizeLayout(payload, 'bad')).toThrow()
  })
  it('稀疏 schema 2 保留名称及样式；v1 正文保留', () => {
    const a = normalizeLayout({schema:2,name:'sparse',style:{id:'s',snapshot:{}}}, 'other')
    expect(a.schema).toBe(2)
    if (a.schema !== 2) throw new Error('wrong schema')
    expect(a.name).toBe('sparse')
    expect('style' in a && a.style).toEqual({id:'s',snapshot:{}})
    const b = normalizeLayout({objs:[{type:'text',text:'kept'}]}, 'old')
    expect(b.schema).toBe(2)
    if (b.schema !== 2) throw new Error('wrong schema')
    expect(b.objects).toHaveLength(1)
    expect(b.objects[0]).toMatchObject({type:'text',text:'kept'})
  })
})
