/**
 * 统一检查服务的**判据**看护（ADR 0030）。
 *
 * 这一层不重复求值器的用例（那是 `preflight.golden.test.ts` 与
 * `tests/test_preflight.py` 的事）。这里盯的是求值结果**接成可定位问题**
 * 之后必须成立的性质：画布维度在不在、逐条命中说的是不是自己的数字、
 * 指纹稳不稳、导出上下文合不合得起来、检查有没有偷偷改文档。
 */
import { describe, expect, it } from 'vitest'
import goldenVectors from '../../../tests/golden/preflight_vectors.json'
import { loadProfile } from './profile'
import { buildSpec, runSpec, type PreflightSpec } from './preflight'
import {
  canvasInput,
  exportContextIssues,
  fingerprintOf,
  filterIssues,
  knownRuleCodes,
  mergeExportIssues,
  ruleEntry,
  summarizeIssues,
  summaryFor,
  validateCanvas,
  validateProject,
} from './validation'
import type { CanvasData, FigureDocument, PanelObject } from '@/types/document'

const profile = loadProfile()

const manifestWith = (fields: { gid: string; role: string; label: string; pt: number }[]) => ({
  stem: 'Fig1',
  size_mm: [80, 60] as [number, number],
  elements: fields.map((f) => ({
    gid: f.gid,
    role: f.role,
    label: f.label,
    bbox: [0.1, 0.1, 0.5, 0.1] as [number, number, number, number],
    draggable: false,
    editable: [{ prop: 'fontsize', type: 'number', value: f.pt }],
  })),
})

const panel = (over: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 0,
  y: 0,
  w: 80,
  h: 60,
  script: 'fig1.py',
  ...over,
})

const docWith = (objects: FigureDocument['objects']): FigureDocument => ({
  schema: 2,
  name: '画布 1',
  page: { w: 80, h: 60 },
  objects,
  guides: [],
})

/** 渲染态：把一份 manifest 直接挂到那个变体上（不经 store，纯计算） */
const renderFor = (p: PanelObject, manifest: unknown) => ({
  byKey: { [`${p.fileId} ${JSON.stringify(p.overrides)}`]: { manifest } as never },
  latest: { [p.fileId]: `${p.fileId} ${JSON.stringify(p.overrides)}` },
})

const assets = { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } as never }

function runOne(doc: FigureDocument, render: ReturnType<typeof renderFor>, canvasId = 'c1') {
  return validateCanvas(
    { canvasId, canvasName: '画布 1', doc, profile },
    'doc-1',
    assets,
    render,
  )
}

describe('规则目录与求值器不许分叉', () => {
  it('golden vectors 里出现过的每个 rule code 都在目录里登记了', () => {
    const cases = (goldenVectors as { cases: { expected: { id: string }[] }[] }).cases
    const emitted = new Set(cases.flatMap((c) => c.expected.map((e) => e.id)))
    expect(emitted.size).toBeGreaterThan(10)
    const known = new Set(knownRuleCodes())
    const missing = [...emitted].filter((id) => !known.has(id)).sort()
    expect(missing, `这些 code 求值器会发但目录里没有：${missing.join(', ')}`).toEqual([])
  })

  it('目录里没登记的 code 按 document / 不可修复处理，绝不猜', () => {
    expect(ruleEntry('zzz-brand-new-rule')).toEqual({ context: 'document', fix: 'none' })
  })

  it('聚合投影原样留着——proof report 与 MCP 认的是它', () => {
    const p = panel()
    const doc = docWith([p])
    const render = renderFor(
      p,
      manifestWith([{ gid: 'axes_0.xticks', role: 'ticks', label: 'X 刻度文字', pt: 6 }]),
    )
    const result = runOne(doc, render)
    // **同一份输入，同一个求值器**：这条比的不是判据对不对（那是 golden
    // vectors 的事），而是接管这一层有没有偷偷改写 / 丢弃聚合项
    const direct = runSpec(buildSpec(doc, assets, render) as PreflightSpec, profile)
    expect(result.raw).toEqual(direct)
    expect(result.raw.length).toBeGreaterThan(0)
  })
})

describe('逐条命中：一行一个真实对象，各说自己的数字', () => {
  const p = panel()
  const doc = docWith([p])
  const render = renderFor(
    p,
    manifestWith([
      { gid: 'axes_0.xticks', role: 'ticks', label: 'X 刻度文字', pt: 6 },
      { gid: 'axes_0.yticks', role: 'ticks', label: 'Y 刻度文字', pt: 7 },
      { gid: 'axes_0.title', role: 'title', label: '标题 “甲”', pt: 7.5 },
    ]),
  )

  it('三个过小的元素摊成三行，而不是一行说三遍', () => {
    const small = runOne(doc, render).issues.filter((i) => i.ruleCode === 'font-below-absolute-floor')
    expect(small).toHaveLength(3)
    expect(small.map((i) => i.objectRef.gid).sort()).toEqual([
      'axes_0.title',
      'axes_0.xticks',
      'axes_0.yticks',
    ])
  })

  it('每一行带的是**它自己**的当前值，不是最糟那一次的', () => {
    const byGid = new Map(
      runOne(doc, render)
        .issues.filter((i) => i.ruleCode === 'font-below-absolute-floor')
        .map((i) => [i.objectRef.gid, i.technicalDetails.effective_pt]),
    )
    expect(byGid.get('axes_0.xticks')).toBe(6)
    expect(byGid.get('axes_0.yticks')).toBe(7)
    expect(byGid.get('axes_0.title')).toBe(7.5)
  })

  it('主语带着引擎给的可读标签与角色，不带 gid', () => {
    const one = runOne(doc, render).issues.find((i) => i.objectRef.gid === 'axes_0.xticks')!
    expect(one.subject.kind).toBe('element')
    expect(one.subject.elementLabel).toBe('X 刻度文字')
    expect(one.subject.elementRole).toBe('ticks')
    // 文案里的参数不许夹带 gid（技术详情另说）
    expect(JSON.stringify(one.message.values ?? {})).not.toContain('axes_0')
  })

  it('gid 在 manifest 里查不到时**不拿 gid 顶替可读标签**', () => {
    // 真实存在的形状：`tick-label-count` 报的 gid 是**轴前缀**（`axes_0.xaxis`），
    // 它根本不是一个元素。拿它当"人话主语"就等于把内部标识说给用户听
    const many = manifestWith(
      Array.from({ length: 40 }, (_, i) => ({
        gid: `axes_0.xaxis_${i}`,
        role: 'ticklabel',
        label: `刻度 ${i}`,
        pt: 10,
      })),
    )
    const one = runOne(doc, renderFor(p, many)).issues.find(
      (i) => i.ruleCode === 'tick-label-count',
    )!
    expect(one.objectRef.gid).toContain('axes_0.xaxis')
    expect(one.subject.elementLabel).toBeUndefined()
    expect(one.subject.elementRole).toBeUndefined()
  })

  it('命中的属性名进 propertyPath——定位靠它落到字段上', () => {
    const one = runOne(doc, render).issues.find((i) => i.objectRef.gid === 'axes_0.title')!
    expect(one.propertyPath).toBe('fontsize')
  })

  it('聚合项与逐条命中说的是同一批对象（两者不许分叉）', () => {
    const result = runOne(doc, render)
    for (const item of result.raw) {
      const mine = result.issues.filter((i) => i.ruleCode === item.id)
      const gids = new Set(mine.map((i) => i.objectRef.gid).filter(Boolean))
      const objs = new Set(mine.map((i) => i.objectRef.objectId).filter(Boolean))
      expect([...gids].sort()).toEqual([...item.gids].sort())
      if (item.objectIds.length) expect([...objs].sort()).toEqual([...item.objectIds].sort())
    }
  })
})

describe('一次交上来多个对象时逐个入账', () => {
  it('两个对象同时越界 = 两条问题，各指一个对象', () => {
    const a = panel({ id: 'p1', x: -50 })
    const b = panel({ id: 'p2', x: 200 })
    const doc = docWith([a, b])
    const out = runOne(doc, renderFor(a, manifestWith([]))).issues.filter(
      (i) => i.ruleCode === 'out-of-page',
    )
    expect(out).toHaveLength(2)
    expect(out.map((i) => i.objectRef.objectId).sort()).toEqual(['p1', 'p2'])
  })
})

describe('画布维度（改造前缺的那一维）', () => {
  it('每条问题都说得出自己在哪张画布上', () => {
    const p = panel()
    const render = renderFor(p, manifestWith([
      { gid: 'axes_0.xticks', role: 'ticks', label: 'X 刻度文字', pt: 6 },
    ]))
    const canvases: CanvasData[] = [
      { id: 'c1', name: '画布 1', page: { w: 80, h: 60 }, objects: [p], guides: [] },
      { id: 'c2', name: '画布 2', page: { w: 80, h: 60 }, objects: [p], guides: [] },
    ]
    const results = validateProject({
      documentId: 'doc-1',
      canvases: canvases.map((c) => canvasInput(c, docWith(c.objects), profile)),
      assets,
      render,
    })
    expect(results.map((r) => r.canvasId)).toEqual(['c1', 'c2'])
    for (const r of results) {
      expect(r.issues.length).toBeGreaterThan(0)
      for (const i of r.issues) expect(i.objectRef.canvasId).toBe(r.canvasId)
    }
    // 同一个对象在两张画布上 = 两条不同的问题，指纹不能撞
    const a = results[0].issues[0].issueId
    const b = results[1].issues[0].issueId
    expect(a).not.toBe(b)
  })
})

describe('指纹', () => {
  it('值变了指纹不变——UI 拿它当 key，不该重建整行', () => {
    const p = panel()
    const doc = docWith([p])
    const before = runOne(doc, renderFor(p, manifestWith([
      { gid: 'axes_0.xticks', role: 'ticks', label: 'X 刻度文字', pt: 6 },
    ])))
    const after = runOne(doc, renderFor(p, manifestWith([
      { gid: 'axes_0.xticks', role: 'ticks', label: 'X 刻度文字', pt: 7 },
    ])))
    const pick = (r: typeof before) =>
      r.issues.find((i) => i.ruleCode === 'font-below-absolute-floor')!
    expect(pick(after).issueId).toBe(pick(before).issueId)
    expect(pick(after).technicalDetails.effective_pt).not.toBe(
      pick(before).technicalDetails.effective_pt,
    )
  })

  it('规则 / 画布 / 对象 / 元素 / 属性五维各自参与', () => {
    const base = { documentId: 'd', canvasId: 'c', objectId: 'o', gid: 'g' }
    const f = fingerprintOf('rule-a', base, 'p')
    expect(f).not.toBe(fingerprintOf('rule-b', base, 'p'))
    expect(f).not.toBe(fingerprintOf('rule-a', { ...base, canvasId: 'c2' }, 'p'))
    expect(f).not.toBe(fingerprintOf('rule-a', { ...base, objectId: 'o2' }, 'p'))
    expect(f).not.toBe(fingerprintOf('rule-a', { ...base, gid: 'g2' }, 'p'))
    expect(f).not.toBe(fingerprintOf('rule-a', base, 'p2'))
  })
})

describe('检查不修改文档', () => {
  it('冻起来的文档照样查得动（一个字节都没写）', () => {
    const p = Object.freeze(panel()) as PanelObject
    const doc = Object.freeze(docWith(Object.freeze([p]) as never)) as FigureDocument
    Object.freeze(doc.page)
    const result = runOne(doc, renderFor(p, manifestWith([
      { gid: 'axes_0.xticks', role: 'ticks', label: 'X 刻度文字', pt: 6 },
    ])))
    expect(result.issues.length).toBeGreaterThan(0)
  })
})

describe('导出上下文', () => {
  const ref = { documentId: 'd', canvasId: 'c1' }

  it('PPI 够就一条都不发', () => {
    expect(
      exportContextIssues({ formats: ['png'], dpi: profile.min_raster_dpi }, profile, ref),
    ).toEqual([])
  })

  it('PPI 不够时发一条，主语是这次导出请求（没有对象）', () => {
    const [issue] = exportContextIssues(
      { formats: ['png'], dpi: profile.min_raster_dpi - 1 },
      profile,
      ref,
    )
    expect(issue.ruleCode).toBe('raster-dpi')
    expect(issue.context).toBe('export')
    expect(issue.objectRef.objectId).toBeNull()
    expect(issue.message.key).toBe('preflight.exportRasterDpi')
    // 与 MCP 那条同源：同一个 rule code 才能共用规范里的 severity 表
    expect(issue.severity).toBe(profile.severity['raster-dpi'] ?? 'warn')
  })

  it('只有位图格式才判——纯 PDF 导出不该被 PPI 拦住', () => {
    expect(exportContextIssues({ formats: ['pdf'], dpi: 1 }, profile, ref)).toEqual([])
  })

  it('与文档问题按指纹去重，且文档那条说了算', () => {
    const docIssue = exportContextIssues({ formats: ['png'], dpi: 1 }, profile, ref)
    const merged = mergeExportIssues(docIssue, docIssue)
    expect(merged).toHaveLength(1)
    expect(merged[0]).toBe(docIssue[0])
  })
})

describe('汇总与筛选', () => {
  it('「还没查」不许当成「没问题」', () => {
    const notYet = summarizeIssues([], { ready: false, failed: false })
    expect(notYet.total).toBe(0)
    expect(notYet.ready).toBe(false)
    const passed = summarizeIssues([], { ready: true, failed: false })
    expect(passed.ready).toBe(true)
  })

  it('组装摘要时 ready / failed 原样透传，不许在路上被写死', () => {
    // 导出对话框读的就是这两位：写死成 true 的话，防抖那 250ms 里它会先说
    // 一句「检查通过」——那是这套服务能犯的最坏的错
    expect(summaryFor([], { ready: false, failed: false }).ready).toBe(false)
    expect(summaryFor([], { ready: true, failed: true }).failed).toBe(true)
    expect(summaryFor([], { ready: true, failed: false }).ready).toBe(true)
  })

  it('筛选按等级 / 画布 / 规则各自生效', () => {
    const p = panel()
    const doc = docWith([p])
    const all = runOne(doc, renderFor(p, manifestWith([
      { gid: 'axes_0.xticks', role: 'ticks', label: 'X 刻度文字', pt: 6 },
    ]))).issues
    expect(filterIssues(all, { canvasId: 'nope' })).toEqual([])
    expect(filterIssues(all, { canvasId: 'c1' })).toEqual(all)
    expect(filterIssues(all, { ruleCode: 'font-below-absolute-floor' }).length).toBeGreaterThan(0)
    expect(filterIssues(all, { severities: [] })).toEqual(all)
  })
})
