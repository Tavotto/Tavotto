/**
 * 问题面板呈现层的看护（审计 T09）：范围、聚合、游标——三件事全是纯函数。
 */
import { describe, expect, it } from 'vitest'
import { msg } from '@/i18n'
import {
  cursorFor,
  cursorView,
  effectiveScope,
  flattenGroups,
  groupIssues,
  issuesInScope,
} from './problemList'
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
  it('没选过 + 有当前图 = 看当前图；没有当前图 = 整个文档，选过也一样', () => {
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
