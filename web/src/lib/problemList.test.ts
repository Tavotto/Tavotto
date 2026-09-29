/**
 * 问题面板呈现层的看护（审计 T09）：范围、聚合、游标——三件事全是纯函数。
 */
import { describe, expect, it } from 'vitest'
import { msg } from '@/i18n'
import {
  bucketsByCategory,
  bucketsByFigure,
  cursorFor,
  cursorView,
  drillIssues,
  drillKey,
  drillKeysOf,
  drillOf,
  effectiveScope,
  flattenGroups,
  groupIssues,
  issuesInScope,
  isSplit,
  sameDrill,
  WHOLE,
} from './problemList'
import type { SubplotPart } from './subplotParts'
import type { Severity } from './profile'
import type { ValidationIssue } from './validation'

const issue = (
  id: string,
  rule: string,
  severity: Severity,
  objectId: string | null,
  gid: string | null = null,
): ValidationIssue => ({
  issueId: id,
  ruleCode: rule,
  severity,
  context: 'document',
  objectRef: { documentId: 'd', canvasId: 'c1', objectId, gid },
  subject: { kind: gid ? 'element' : objectId ? 'object' : 'page' },
  propertyPath: null,
  message: msg('x'),
  technicalDetails: {},
  fixKind: 'none',
})

const A1 = issue('a1', 'font-too-small', 'warn', 'p1', 'g1')
const A2 = issue('a2', 'font-too-small', 'warn', 'p1', 'g2')
const A3 = issue('a3', 'font-too-small', 'warn', 'p2', 'g1')
const B1 = issue('b1', 'font-below-absolute-floor', 'error', 'p2', 'g3')
const PAGE = issue('pg', 'page-width', 'warn', null)
const SUGG = issue('s1', 'palette-line-markers', 'suggestion', 'p1', 'g9')

describe('范围', () => {
  it('没选过 + 有当前图 = 看当前图；没有当前图 = 整份排版，选过也一样', () => {
    expect(effectiveScope(null, 'p1')).toBe('figure')
    expect(effectiveScope(null, null)).toBe('document')
    expect(effectiveScope('figure', null)).toBe('document')
    expect(effectiveScope('document', 'p1')).toBe('document')
  })

  it('「当前图」= 主语是这个面板的那些；页面级问题不属于任何一张图', () => {
    const all = [A1, A2, A3, B1, PAGE]
    expect(issuesInScope(all, 'figure', 'p1').map((i) => i.issueId)).toEqual(['a1', 'a2'])
    expect(issuesInScope(all, 'figure', 'p2').map((i) => i.issueId)).toEqual(['a3', 'b1'])
    expect(issuesInScope(all, 'document', 'p1')).toHaveLength(5)
  })
})

describe('聚合', () => {
  it('同一条规则合成一组，组按最高等级排（阻断在前），受影响对象按（对象, 元素）数', () => {
    const groups = groupIssues([A1, A2, A3, SUGG, B1])
    expect(groups.map((g) => g.ruleCode)).toEqual([
      'font-below-absolute-floor',
      'font-too-small',
      'palette-line-markers',
    ])
    const a = groups[1]
    expect(a.issues.map((i) => i.issueId)).toEqual(['a1', 'a2', 'a3'])
    expect(a.objects).toBe(3)
    expect(a.severity).toBe('warn')
  })

  it('同一个对象同一个元素被同一条规则报两次，只算一个对象', () => {
    const dup = { ...A1, issueId: 'a1-dup' }
    expect(groupIssues([A1, dup])[0].objects).toBe(1)
  })

  it('展开顺序 = 组顺序 × 组内顺序', () => {
    expect(flattenGroups(groupIssues([A1, B1, A2])).map((i) => i.issueId)).toEqual(['b1', 'a1', 'a2'])
  })
})

describe('游标', () => {
  const groups = groupIssues([A1, A2, A3, B1])
  // 展开顺序：b1 | a1 a2 a3

  it('给一条问题造游标：记同组下标与展开下标；不在清单里回 null', () => {
    expect(cursorFor(groups, 'a2')).toEqual({
      issueId: 'a2',
      ruleCode: 'font-too-small',
      index: 1,
      flatIndex: 2,
    })
    expect(cursorFor(groups, 'nope')).toBeNull()
  })

  it('那条还在：位置是 1-based，上一条 / 下一条是展开顺序里的邻居', () => {
    const v = cursorView(groups, cursorFor(groups, 'a1'))
    expect(v.current?.issueId).toBe('a1')
    expect(v.position).toBe(2)
    expect(v.total).toBe(4)
    expect(v.prev?.issueId).toBe('b1')
    expect(v.next?.issueId).toBe('a2')
  })

  it('末尾那条没有下一项，开头那条没有上一项', () => {
    expect(cursorView(groups, cursorFor(groups, 'a3')).next).toBeNull()
    expect(cursorView(groups, cursorFor(groups, 'b1')).prev).toBeNull()
  })

  it('那条修好消失了：「下一项」= 同组同位置顶上来的那条，不跳回开头', () => {
    const cursor = cursorFor(groups, 'a1')!
    const after = groupIssues([A2, A3, B1]) // a1 修好了
    const v = cursorView(after, cursor)
    expect(v.current).toBeNull()
    expect(v.position).toBe(0)
    expect(v.total).toBe(3)
    expect(v.next?.issueId).toBe('a2')
    expect(v.prev?.issueId).toBe('b1')
  })

  it('那条消失且同组也空了：「下一项」= 展开顺序里原位置上的那条', () => {
    const cursor = cursorFor(groups, 'b1')! // flatIndex 0
    const after = groupIssues([A1, A2, A3])
    const v = cursorView(after, cursor)
    expect(v.next?.issueId).toBe('a1')
    expect(v.prev).toBeNull()
  })

  it('最后一条消失、同组也空了：夹到新清单的末尾，不越界', () => {
    const cursor = cursorFor(groups, 'a3')! // flatIndex 3
    const after = groupIssues([B1])
    const v = cursorView(after, cursor)
    expect(v.next?.issueId).toBe('b1')
    expect(v.prev).toBeNull()
  })

  it('没有游标 / 清单空了：什么都不指', () => {
    expect(cursorView(groups, null)).toMatchObject({ current: null, next: null, prev: null, total: 4 })
    expect(cursorView([], cursorFor(groups, 'a1'))).toMatchObject({ current: null, next: null, total: 0 })
  })
})

/* --------------------------- 卡片（2026-09-28） --------------------------- */

const part = (key: string, order: number, tag: string | null): SubplotPart => ({
  key,
  order,
  tag,
  label: `子图 ${order + 1}`,
  bbox: [0, 0, 1, 1],
})
const inPart = (i: ValidationIssue, p: SubplotPart): ValidationIssue => ({
  ...i,
  subject: { ...i.subject, part: p },
})
const PA = part('axes_0', 0, '(a)')
const PB = part('axes_2', 1, '(b)')
// p1 是一张组图：两条在 (b)、一条在 (a)、一条整图级（位图分辨率这类，不落在任何子图里）
const G1 = inPart(issue('g1', 'font-too-small', 'warn', 'p1', 'axes_2.t0'), PB)
const G2 = inPart(issue('g2', 'font-below-absolute-floor', 'error', 'p1', 'axes_2.t1'), PB)
const G3 = inPart(issue('g3', 'line-width-off-preset', 'warn', 'p1', 'axes_0'), PA)
const G4 = issue('g4', 'raster-dpi', 'warn', 'p1')
const NV = issue('nv', 'panel-text-not-verifiable', 'not_verifiable', 'p3')

describe('卡片：按图', () => {
  it('一张图一桶（文档顺序），组图按子图再分：整图级在前、子图按阅读顺序；无法核验不进卡片', () => {
    const figs = bucketsByFigure([G1, G3, A3, G2, G4, PAGE, NV])
    expect(figs.map((f) => f.key)).toEqual(['p1', 'p2', 'page:c1'])
    const [p1, p2, page] = figs
    expect(isSplit(p1)).toBe(true)
    expect(p1.parts.map((p) => p.key)).toEqual([WHOLE, 'axes_0', 'axes_2'])
    expect(p1.parts.map((p) => p.issues.map((i) => i.issueId))).toEqual([['g4'], ['g3'], ['g1', 'g2']])
    expect(p1.parts[2].severity).toBe('error')
    expect(isSplit(p2)).toBe(false)
    expect(page.objectId).toBeNull()
  })

  it('只有一个子图有问题也拆：「子图 (c)」比笼统的整图卡说得多', () => {
    const [f] = bucketsByFigure([G1])
    expect(isSplit(f)).toBe(true)
    expect(drillOf(G1, 'figure', [f])).toEqual({ kind: 'part', figure: 'p1', key: 'axes_2' })
  })

  it('卡片里装的 = 点进去列出来的：同一个 drillIssues', () => {
    const all = [G1, G2, G3, G4, A3, NV]
    expect(drillIssues(all, { kind: 'part', figure: 'p1', key: 'axes_2' }).map((i) => i.issueId)).toEqual([
      'g1',
      'g2',
    ])
    expect(drillIssues(all, { kind: 'part', figure: 'p1', key: WHOLE }).map((i) => i.issueId)).toEqual(['g4'])
    expect(drillIssues(all, { kind: 'figure', key: 'p1' })).toHaveLength(4)
    expect(drillIssues(all, { kind: 'unverifiable' }).map((i) => i.issueId)).toEqual(['nv'])
  })
})

describe('卡片：按类别', () => {
  it('类别来自规则目录；阻断在前，同级项数多的在前', () => {
    const cats = bucketsByCategory([G1, G3, G4, A1, A2, NV])
    // 文字：g1 a1 a2（warn ×3）；线条：g3；文件：g4（raster-dpi）
    expect(cats.map((c) => [c.key, c.issues.length])).toEqual([
      ['text', 3],
      ['lines', 1],
      ['file', 1],
    ])
    expect(bucketsByCategory([G1, G2])[0].severity).toBe('error')
    expect(drillOf(G3, 'category', [])).toEqual({ kind: 'category', key: 'lines' })
  })

  it('目录里没有的规则落「其他」，不按名字猜', () => {
    const odd = issue('z', 'font-something-new', 'warn', 'p1')
    expect(bucketsByCategory([odd]).map((c) => c.key)).toEqual(['other'])
  })
})

describe('卡片的机器标识', () => {
  it('drillKeysOf 覆盖这条问题可能在的每一张卡片，其中就有 drillOf 给的那张', () => {
    const figs = bucketsByFigure([G1, A3])
    for (const i of [G1, A3, NV]) {
      for (const view of ['figure', 'category'] as const) {
        expect(drillKeysOf(i)).toContain(drillKey(drillOf(i, view, figs)))
      }
    }
  })

  it('sameDrill 按内容比，不按引用', () => {
    expect(sameDrill({ kind: 'part', figure: 'p1', key: 'a' }, { kind: 'part', figure: 'p1', key: 'a' })).toBe(true)
    expect(sameDrill({ kind: 'part', figure: 'p1', key: 'a' }, { kind: 'part', figure: 'p2', key: 'a' })).toBe(false)
    expect(sameDrill({ kind: 'figure', key: 'p1' }, { kind: 'category', key: 'text' } as never)).toBe(false)
    expect(sameDrill(null, null)).toBe(true)
    expect(sameDrill({ kind: 'unverifiable' }, null)).toBe(false)
  })
})
