/**
 * `web/src` 的增量架构门禁：不新增 import 环、旧环不扩大、不新增跨层反向边、
 * 动态 import 逐条登记。图由 `scripts/import-graph.mjs` 用 TypeScript 编译器 API 建
 * （只读源码不执行），基线在 `web/import-architecture-baseline.json`。
 *
 * 判据的主语：**生产模块之间的运行时边**。`import type` 编译后不存在，不算；
 * `*.test.ts(x)` / `src/test/**` 不进图。环 = 强连通分量（≥2 节点）；「旧环扩大」
 * = 成员多了或分量内部的边多了——延后到函数里再 import 也是逻辑依赖，照样算边。
 *
 * 基线是登记表不是豁免表：每个环带 reason / since / remove_when；环没了要把它删掉
 * （旧豁免会安静地变成盲区），所以「基线里有、图里没有」同样红。
 */
import fs from 'node:fs'
import { describe, expect, it } from 'vitest'
import {
  BASELINE,
  LAYER_RULES,
  buildGraph,
  cycles,
  layerViolations,
  runtimeEdges,
} from '../scripts/import-graph.mjs'

type Cycle = { members: string[]; edges: string[][]; reason?: string }
type Baseline = {
  cycles: Cycle[]
  unknownDynamic: { from: string; call: string; reason: string }[]
  layerViolations: { from: string; to: string; reason: string }[]
}

const baseline = JSON.parse(fs.readFileSync(BASELINE, 'utf8')) as Baseline
const graph = buildGraph()
const found = cycles(graph)

const key = (members: string[]) => [...members].sort().join(' | ')
const edgeKey = (e: string[]) => `${e[0]} -> ${e[1]}`

describe('import 图本身立得住（判据的前提）', () => {
  it('真的扫到了一批生产模块与边', () => {
    expect(graph.nodes.size).toBeGreaterThan(200)
    expect(runtimeEdges(graph).length).toBeGreaterThan(1000)
    expect(graph.edges.some((e) => e.kind === 'type')).toBe(true)
  })

  it('没有解析不了的相对路径（解析不了 = 图上缺边，不是「没有依赖」）', () => {
    expect(graph.unresolved).toEqual([])
  })

  it('非字面量的 import() 逐条登记', () => {
    const registered = new Set(baseline.unknownDynamic.map((d) => `${d.from} :: ${d.call}`))
    const unknown = graph.unknownDynamic.map((d) => `${d.from} :: ${d.call}`)
    expect(unknown.filter((u) => !registered.has(u))).toEqual([])
    // 登记表里的也得还在——否则那条登记就是过期的
    expect([...registered].filter((r) => !unknown.includes(r))).toEqual([])
  })
})

describe('import 环只减不增', () => {
  const byKey = new Map(baseline.cycles.map((c) => [key(c.members), c]))

  it('没有基线之外的新环', () => {
    const novel = found.filter((c) => {
      // 与某个基线环有交集就归到它头上（下一条判扩大），完全不相干的才是新环
      return !baseline.cycles.some((b) => c.members.some((m) => b.members.includes(m)))
    })
    expect(
      novel.map((c) => c.members.join(' ↔ ')),
      '新出现的 import 环。拆掉它，或者在 import-architecture-baseline.json 里登记并写清 reason / remove_when',
    ).toEqual([])
  })

  it('旧环不扩大：成员与内部边都是基线的子集', () => {
    for (const c of found) {
      const b = baseline.cycles.find((x) => c.members.some((m) => x.members.includes(m)))
      if (!b) continue
      const extraMembers = c.members.filter((m) => !b.members.includes(m))
      const known = new Set(b.edges.map(edgeKey))
      const extraEdges = c.edges.map(edgeKey).filter((e) => !known.has(e))
      expect(extraMembers, `环 ${key(b.members)} 多出成员`).toEqual([])
      expect(extraEdges, `环 ${key(b.members)} 多出内部边`).toEqual([])
    }
  })

  it('基线里的环还在（没了就把它从基线删掉——过期的登记是盲区）', () => {
    const present = new Set(found.map((c) => key(c.members)))
    const stale = [...byKey.keys()].filter((k) => !present.has(k))
    expect(stale).toEqual([])
  })

  it('每个已知环都写了理由与删除条件', () => {
    for (const c of baseline.cycles) {
      expect(c.reason, key(c.members)).toBeTruthy()
      expect((c as { remove_when?: string }).remove_when, key(c.members)).toBeTruthy()
    }
  })
})

describe('跨层反向边', () => {
  it(`门禁规则（${LAYER_RULES.map(([a, b]) => `${a}→${b}`).join(', ')}）零违例，或已在基线登记`, () => {
    const known = new Set(baseline.layerViolations.map((v) => `${v.from} -> ${v.to}`))
    const novel = layerViolations(graph).filter((v) => !known.has(`${v.from} -> ${v.to}`))
    expect(novel.map((v) => `${v.from}:${v.line} → ${v.to}（${v.rule}）`)).toEqual([])
  })
})
