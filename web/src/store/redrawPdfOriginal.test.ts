// T-005 补充：PDF 原件用了 Type 3 / 有缺字时的「用脚本重画」——一条 `figure.frame = "savefig"`，画布 == 导出
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { emptyProject, type PanelObject } from '@/types/document'
import type { PanelFontFacts, PanelInfo } from '@/lib/api'
import { useDocumentStore } from './documentStore'
import { useAssetStore } from './assetStore'
import { pdfFontFactsOf, usePdfFontFacts } from './pdfFontFacts'
import { redrawPdfOriginalsWithScript, redrawWithScript } from './actions'
import { renderTargets } from '@/hooks/useEngineSync'
import { setCurrentProjectId } from '@/lib/session'
import { pdfFontProblem, REDRAW_FRAME_OVERRIDE, redrawOffered } from '@/lib/scriptedBitmaps'

const fetchFacts = vi.hoisted(() => vi.fn())
const render = vi.hoisted(() => vi.fn(async () => ({ rev: 1, svg: '<svg/>', manifest: { size_mm: [120, 80], elements: [] } })))
vi.mock('@/lib/api', async (original) => ({
  ...(await original<typeof import('@/lib/api')>()),
  fetchPanelFontFacts: (...a: unknown[]) => fetchFacts(...a),
  engineRender: render,
}))
vi.mock('./styleWork', () => ({ hasPendingStyleWork: () => false }))

const facts = (extra: Partial<PanelFontFacts> = {}): PanelFontFacts =>
  ({ id: 'plot.pdf', readable: true, type3: false, missing_glyphs: false, ...extra })
const pdf = (extra: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p', type: 'panel', fileId: 'plot.pdf', fileKind: 'pdf', script: 'figure.py', figureFrame: 1,
  nativeW: 120, nativeH: 80, x: 10, y: 10, w: 60, h: 40, overrides: [], ...extra,
})
const S = () => useDocumentStore.getState()
const P = () => S().doc.objects[0] as PanelObject
const asset = (mtime: number) => ({ id: 'plot.pdf', kind: 'pdf', mtime, script: 'figure.py' }) as unknown as PanelInfo
const flush = () => new Promise((r) => setTimeout(r, 0))

async function seed(p = pdf()) {
  await S().switchDocument(emptyProject(), 'redraw-pdf-original-test')
  S().commit(literal('seed'), (d) => { d.objects.push(p) })
  useDocumentStore.setState({ past: [], future: [] })
}

beforeEach(async () => {
  fetchFacts.mockReset().mockResolvedValue(facts({ type3: true }))
  render.mockClear()
  setCurrentProjectId('A')
  useAssetStore.setState({ byId: { 'plot.pdf': asset(1) } })
  usePdfFontFacts.getState().clear()
  await seed()
})

describe('pdfFontProblem / redrawOffered（PDF）', () => {
  it('缺字压过 Type 3；读不出 / 没问题 / 还没问都不给', () => {
    expect(pdfFontProblem(facts({ type3: true, missing_glyphs: true }))).toBe('pdfMissingGlyphs')
    expect(pdfFontProblem(facts({ type3: true }))).toBe('pdfType3')
    expect(pdfFontProblem(facts())).toBeNull()
    expect(pdfFontProblem(facts({ readable: false, type3: true }))).toBeNull()
    expect(pdfFontProblem(undefined)).toBeNull()
    expect(redrawOffered(pdf())).toBe(false)
    expect(redrawOffered(pdf(), facts({ type3: true }))).toBe(true)
  })

  it.each<[string, Partial<PanelObject>]>([
    ['没脚本', { script: null }],
    ['带编辑', { overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'x' }] }],
    ['隐藏', { hidden: true }],
  ])('%s：有问题也不给', (_name, extra) => {
    expect(redrawOffered(pdf(extra), facts({ type3: true }))).toBe(false)
  })
})

describe('usePdfFontFacts', () => {
  it('同一份文件只问一次；文件变了（mtime）再问；切项目作废在途的答复', async () => {
    usePdfFontFacts.getState().request('plot.pdf')
    usePdfFontFacts.getState().request('plot.pdf')
    await flush()
    expect(fetchFacts).toHaveBeenCalledTimes(1)
    expect(pdfFontFactsOf('plot.pdf')?.type3).toBe(true)
    useAssetStore.setState({ byId: { 'plot.pdf': asset(2) } })
    expect(pdfFontFactsOf('plot.pdf')).toBeUndefined()
    fetchFacts.mockResolvedValueOnce(facts())
    usePdfFontFacts.getState().request('plot.pdf')
    usePdfFontFacts.getState().clear()
    await flush()
    expect(fetchFacts).toHaveBeenCalledTimes(2)
    expect(pdfFontFactsOf('plot.pdf')).toBeUndefined()
  })

  it('请求失败 / 读不出：不说有问题，也不反复问', async () => {
    fetchFacts.mockRejectedValueOnce(new Error('down'))
    usePdfFontFacts.getState().request('plot.pdf')
    await flush()
    usePdfFontFacts.getState().request('plot.pdf')
    expect(fetchFacts).toHaveBeenCalledTimes(1)
    expect(pdfFontFactsOf('plot.pdf')).toBeUndefined()
  })

  it('清单里没有这份文件：不问', () => {
    useAssetStore.setState({ byId: {} })
    usePdfFontFacts.getState().request('plot.pdf')
    expect(fetchFacts).not.toHaveBeenCalled()
  })
})

describe('redrawWithScript（PDF 原件）', () => {
  it('一次可撤销的提交：只加 figure.frame = savefig，版面不动；画布由引擎重画', async () => {
    usePdfFontFacts.getState().request('plot.pdf')
    await flush()
    const before = structuredClone(P())
    expect(renderTargets([P()], null, {}).map((o) => o.id)).toEqual([])
    expect(redrawWithScript('p')).toBe(true)
    expect(P().overrides).toEqual([REDRAW_FRAME_OVERRIDE])
    expect({ ...P(), overrides: [] }).toEqual(before)
    expect(S().past).toHaveLength(1)
    expect(renderTargets([P()], null, {}).map((o) => o.id)).toEqual(['p'])
    expect(redrawOffered(P(), pdfFontFactsOf('plot.pdf'))).toBe(false)
    S().undo()
    expect(P()).toEqual(before)
  })

  it('原件没问题或还没问到：不动', async () => {
    expect(redrawWithScript('p')).toBe(false)
    fetchFacts.mockResolvedValueOnce(facts())
    usePdfFontFacts.getState().request('plot.pdf')
    await flush()
    expect(redrawWithScript('p')).toBe(false)
    expect(P().overrides).toEqual([])
    expect(S().past).toHaveLength(0)
  })
})

describe('redrawPdfOriginalsWithScript（导出结果里的「用脚本重画并重新导出」）', () => {
  it('几张一起换、一次提交；带编辑的不动；一次撤销全回原件', async () => {
    S().commit(literal('more'), (d) => {
      d.objects.push(pdf({ id: 'q', x: 80 }))
      d.objects.push(pdf({ id: 'e', x: 150, overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'x' }] }))
    })
    useDocumentStore.setState({ past: [], future: [] })
    usePdfFontFacts.getState().request('plot.pdf')
    await flush()
    expect(redrawPdfOriginalsWithScript(['p', 'q', 'e', 'missing'])).toEqual(['p', 'q'])
    const byId = (id: string) => S().doc.objects.find((o) => o.id === id) as PanelObject
    expect(byId('p').overrides).toEqual([REDRAW_FRAME_OVERRIDE])
    expect(byId('q').overrides).toEqual([REDRAW_FRAME_OVERRIDE])
    expect(byId('e').overrides).toHaveLength(1)
    expect(S().past).toHaveLength(1)
    S().undo()
    expect(byId('p').overrides).toEqual([])
    expect(byId('q').overrides).toEqual([])
  })

  it('一张都换不了（没问到 / 没问题）：不提交', () => {
    expect(redrawPdfOriginalsWithScript(['p'])).toEqual([])
    expect(S().past).toHaveLength(0)
  })
})
