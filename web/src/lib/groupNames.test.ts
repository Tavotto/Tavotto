/**
 * 组名（ADR 0191 修订 / 0208 前置 6.0）的读法与写路径：读法与后端 `agentobjects.group_name_of` 用同一份
 * `tests/golden/group_names_vectors.json` 对拍；`setGroupPath` / `remapGroupPath` / `inheritGroupNames` 守「名字只在自己组链上」。
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import type { CanvasObject } from '@/types/document'
import { groupNameMap, groupNameOf, groupPathOf, inheritGroupNames, layerTree, remapGroupPath, setGroupPath } from './groupTree'

const GOLDEN = JSON.parse(readFileSync(path.resolve(process.cwd(), '../tests/golden/group_names_vectors.json'), 'utf8')) as {
  cases: { name: string; objects: Record<string, unknown>[]; names: Record<string, string> }[]
}
const obj = (over: Record<string, unknown>): CanvasObject =>
  ({ type: 'shape', shape: 'rect', x: 0, y: 0, w: 1, h: 1, strokePt: 1, color: '#000', fill: null, ...over }) as unknown as CanvasObject

describe('读法：与后端共享样本逐例对拍', () => {
  it.each(GOLDEN.cases)('$name', ({ objects, names }) => {
    const objs = objects.map(obj)
    const gids = new Set(objs.flatMap((o) => groupPathOf(o)))
    const got = Object.fromEntries([...gids].map((g) => [g, groupNameOf(objs, g)]).filter(([, n]) => n))
    expect(got).toEqual(names)
    // 一趟扫完的版本与逐个读同一个结果（图层树 / 导出载荷用的是它）
    expect(Object.fromEntries(groupNameMap(objs))).toEqual(names)
  })

  it('读时不改写文档', () => {
    const objs = [obj({ id: 'a', groupId: 'g1', groupNames: { g1: '乙', g9: '旧' } })]
    const before = JSON.stringify(objs)
    groupNameOf(objs, 'g1')
    groupNameMap(objs)
    layerTree(objs)
    expect(JSON.stringify(objs)).toBe(before)
  })
})

describe('setGroupPath：名字跟着链走', () => {
  it('拆掉一层：那一层的键去掉，其余留着；全拆光 = 整个字段去掉', () => {
    const o = obj({ id: 'a', groupId: 'g2', groupPath: ['g1', 'g2'], groupNames: { g1: '外', g2: '内' } })
    setGroupPath(o, ['g1'])
    expect(o.groupNames).toEqual({ g1: '外' })
    setGroupPath(o, [])
    expect(o.groupNames).toBeUndefined()
    expect(o.groupId).toBeUndefined()
  })

  it('外面再包一层：已有的名字不动', () => {
    const o = obj({ id: 'a', groupId: 'g1', groupNames: { g1: '内' } })
    setGroupPath(o, ['gNew', 'g1'])
    expect(o.groupNames).toEqual({ g1: '内' })
    expect(o.groupPath).toEqual(['gNew', 'g1'])
  })

  it('旧构建改过 groupId 留下的错位键：下次写链时顺带清掉', () => {
    const o = obj({ id: 'a', groupId: 'g_new', groupNames: { g_old: '旧名' } })
    expect(groupNameOf([o], 'g_new')).toBe('')
    setGroupPath(o, ['g_new'])
    expect(o.groupNames).toBeUndefined()
  })
})

describe('remapGroupPath：复制 / 粘贴换 id 时名字的键跟着换', () => {
  it('一层、两层都换键；原件不动', () => {
    const a = obj({ id: 'a', groupId: 'g2', groupPath: ['g1', 'g2'], groupNames: { g1: '外', g2: '内' } })
    const copy = structuredClone(a)
    remapGroupPath(copy, (g) => `${g}'`)
    expect(copy.groupPath).toEqual(["g1'", "g2'"])
    expect(copy.groupNames).toEqual({ "g1'": '外', "g2'": '内' })
    expect(a.groupNames).toEqual({ g1: '外', g2: '内' })
  })

  it('组内编辑里复制：外层祖先原样保留（名字不动），只换子组', () => {
    const copy = obj({ id: 'a', groupId: 'g2', groupPath: ['g1', 'g2'], groupNames: { g1: '外', g2: '内' } })
    remapGroupPath(copy, (g, i) => (i < 1 ? g : `${g}'`))
    expect(copy.groupNames).toEqual({ g1: '外', "g2'": '内' })
  })

  it('没有名字 / 不在组里：什么都不加', () => {
    const c = obj({ id: 'a', groupId: 'g1' })
    remapGroupPath(c, (g) => `${g}'`)
    expect(c.groupNames).toBeUndefined()
    const lone = obj({ id: 'b' })
    remapGroupPath(lone, (g) => g)
    expect(lone.groupNames).toBeUndefined()
  })
})

describe('inheritGroupNames：派生出来的新对象接着带外层组的名字', () => {
  it('只带它自己链上的、且自己还没有的', () => {
    const src = obj({ id: 's', groupId: 'g1', groupNames: { g1: '外', gX: '无关' } })
    const out = obj({ id: 'n' })
    setGroupPath(out, ['g1', 'gNew'])
    out.groupNames = { gNew: '已有' }
    inheritGroupNames(src, out)
    expect(out.groupNames).toEqual({ gNew: '已有', g1: '外' })
    const none = obj({ id: 'm' })
    inheritGroupNames(obj({ id: 's2', groupId: 'g1' }), none)
    expect(none.groupNames).toBeUndefined()
  })
})

describe('layerTree：组节点带名字', () => {
  it('名字按「z 序第一个非空」读，没有 = 空串', () => {
    const tree = layerTree([
      obj({ id: 'a', groupId: 'g1' }),
      obj({ id: 'b', groupId: 'g1', groupNames: { g1: '机理图' } }),
      obj({ id: 'c', groupId: 'g2' }),
    ])
    const g = Object.fromEntries(tree.flatMap((n) => (n.kind === 'group' ? [[n.gid, n.name]] : [])))
    expect(g).toEqual({ g1: '机理图', g2: '' })
  })
})
