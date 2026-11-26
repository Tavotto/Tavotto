// T-005：画布角标「用脚本重画」——用户点了才重画；与进图内编辑同一套准入与对照确认，成功后画布 == 导出
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { EngineError } from '@/lib/api'
import { emptyProject, type PanelObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useUiStore } from './uiStore'
import { useAssetStore } from './assetStore'
import { useSelectionStore } from './selectionStore'
import { useEnvStore } from './envStore'
import { usePngAdmissionMemory } from './pngAdmissionMemory'
import { useSourceBackedReview } from './sourceBackedReview'
import { useRenderStore } from './renderStore'
import { enterElementEdit, redrawWithScript } from './actions'
import { renderTargets } from '@/hooks/useEngineSync'
import { setCurrentProjectId } from '@/lib/session'
import {
  clearAdoption,
  FIGSIZE_SOURCE_POLICY,
  isSourceBackedZeroEdit,
  originalArtifactOverrides,
  SCRIPT_BACKED_SOURCE_POLICY,
} from '@/lib/artifactValidation'
import { redrawOffered } from '@/lib/scriptedBitmaps'

const world = vi.hoisted(() => ({ strictOk: false, status: 'needs_confirmation' as 'exact' | 'needs_confirmation', sha: '1'.repeat(64) }))
const render = vi.fn()
const preview = vi.fn()
vi.mock('@/lib/api', async (original) => ({
  ...(await original<typeof import('@/lib/api')>()),
  engineRender: (...a: unknown[]) => render(...a),
  fetchScriptBackedEnabled: async () => true,
  enginePreviewPngSnapshot: (...a: unknown[]) => preview(...a),
}))
vi.mock('./styleWork', () => ({ hasPendingStyleWork: () => false }))

const FRAME = { gid: 'figure', prop: 'frame', value: 'figsize' }
const BYTES = 'a'.repeat(64)
const refused = (reason: string) =>
  new EngineError('refused', '', 'artifact_source_unavailable', '', undefined, undefined, { params: { reason } })
const verdictBody = () => ({
  status: world.status,
  reconstruction_sha256: world.sha,
  metrics: world.status === 'exact'
    ? { differing_pixels: 0, differing_ratio: 0, total_pixels: 1000, bbox: null }
    : { differing_pixels: 30, differing_ratio: 0.03, total_pixels: 1000, bbox: [2, 3, 12, 9] },
})
const sourceEcho = (policy: string) => ({ render_policy: policy, source_id: 'plot.png', origin: 'static', kind: 'png', bytes_sha256: BYTES, size_bytes: 10 })
const base = () => ({ rev: 1, svg: '<svg/>', manifest: { size_mm: [127, 81.28], elements: [] } })

const panel = (extra: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p', type: 'panel', fileId: 'plot.png', fileKind: 'raster', script: 'figure.py', figureFrame: 1,
  nativeW: 126.99, nativeH: 81.13, pxW: 360, pxH: 230, x: 17, y: 21, w: 63, h: 47, overrides: [], ...extra,
})
const S = () => useDocumentStore.getState()
const P = () => S().doc.objects[0] as PanelObject
const review = () => useSourceBackedReview.getState()
const policies = () => render.mock.calls.map((c) => (c[2] as { source_policy?: string } | undefined)?.source_policy)

async function seed(p = panel()) {
  await S().switchDocument(emptyProject(), 'redraw-with-script-test')
  S().commit(literal('seed'), (d) => { d.objects.push(p) })
  useDocumentStore.setState({ past: [], future: [] })
}

beforeEach(async () => {
  Object.assign(world, { strictOk: false, status: 'needs_confirmation', sha: '1'.repeat(64) })
  render.mockReset().mockImplementation(async (_id: unknown, _patches: unknown, opts?: Record<string, unknown>) => {
    const policy = opts?.source_policy as string | undefined
    if (policy === FIGSIZE_SOURCE_POLICY) {
      if (!world.strictOk) throw refused('source_mismatch')
      return { ...base(), artifact_source: sourceEcho(policy) }
    }
    if (policy === SCRIPT_BACKED_SOURCE_POLICY) {
      return { ...base(), artifact_source: sourceEcho(policy), source_backed: verdictBody() }
    }
    return base()
  })
  preview.mockReset().mockImplementation(async () => ({ url: 'data:image/png;base64,RECON', manifest: base().manifest, sourceBacked: verdictBody() }))
  useRenderStore.getState().clear(); useUiStore.getState().setElementPanel(null); useUiStore.getState().setStatus(null)
  useSelectionStore.getState().clear(); useAssetStore.setState({ byId: {} }); setCurrentProjectId('A')
  await seed()
  useEnvStore.getState().resetProject(); usePngAdmissionMemory.getState().clear(); review().clear()
})

describe('redrawOffered：画布上挂的是有脚本的位图原件', () => {
  it('没动过的 PNG 源图给；没脚本 / 带编辑 / 隐藏 / 历史版本不给', () => {
    expect(redrawOffered(panel())).toBe(true)
    expect(redrawOffered(panel({ script: null }))).toBe(false)
    expect(redrawOffered(panel({ overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'x' }] }))).toBe(false)
    expect(redrawOffered(panel({ hidden: true }))).toBe(false)
    expect(redrawOffered(panel({ fileId: 'plot.pdf', fileKind: 'pdf' }))).toBe(false)
  })

  it('只看文档、不执行：判一遍不发任何渲染', () => {
    redrawOffered(P())
    expect(render).not.toHaveBeenCalled()
    expect(renderTargets([P()], null, {}).map((o) => o.id)).toEqual([])
  })
})

describe('redrawWithScript', () => {
  it('重画与原件有差异：先对照，确认后一次提交——来源驱动采用 + showsRedraw，不进图内编辑', async () => {
    const pending = redrawWithScript('p')
    await vi.waitFor(() => expect(review().request).not.toBeNull())
    expect(review().request!.mode).toBe('adopt')
    expect(S().past).toHaveLength(0)
    review().answer(true)
    expect(await pending).toBe(true)
    expect(policies().slice(0, 2)).toEqual([FIGSIZE_SOURCE_POLICY, SCRIPT_BACKED_SOURCE_POLICY])
    expect(P().overrides).toEqual([FRAME])
    expect(P().sourceBackedFigure).toMatchObject({ status: 'reconstructed', reconstructionSha256: world.sha })
    expect(P().showsRedraw).toBe(true)
    expect(S().past).toHaveLength(1)
    expect(useUiStore.getState().elementPanelId).toBeNull()
    // 画布显示重画（不是原像素）、自动渲染接手它；原图导出也发 FRAME（同一份重画）
    expect(isSourceBackedZeroEdit(P())).toBe(false)
    expect(renderTargets([P()], null, {}).map((o) => o.id)).toEqual(['p'])
    expect(originalArtifactOverrides(P())).toEqual([FRAME])
    expect(redrawOffered(P())).toBe(false)
  })

  it('对照时取消：什么都不写，角标还在', async () => {
    const before = structuredClone(P())
    const pending = redrawWithScript('p')
    await vi.waitFor(() => expect(review().request).not.toBeNull())
    review().answer(false)
    expect(await pending).toBe(false)
    expect(P()).toEqual(before)
    expect(S().past).toHaveLength(0)
    expect(redrawOffered(P())).toBe(true)
  })

  it('精确策略通过：严格 guard，不记 showsRedraw（精确采用本来就显示引擎图）', async () => {
    world.strictOk = true
    expect(await redrawWithScript('p')).toBe(true)
    expect(policies()).toEqual([FIGSIZE_SOURCE_POLICY])
    expect(review().request).toBeNull()
    expect(Object.hasOwn(P(), 'artifactValidation')).toBe(true)
    expect(P().showsRedraw).toBeUndefined()
    expect(redrawOffered(P())).toBe(false)
    expect(useUiStore.getState().elementPanelId).toBeNull()
  })

  it('撤销回到原像素与角标', async () => {
    const pending = redrawWithScript('p')
    await vi.waitFor(() => expect(review().request).not.toBeNull())
    review().answer(true)
    await pending
    S().undo()
    expect(P().showsRedraw).toBeUndefined()
    expect(Object.hasOwn(P(), 'sourceBackedFigure')).toBe(false)
    expect(redrawOffered(P())).toBe(true)
  })

  it('之前进编辑时已对照确认过：重画摘要没变就不再对照，只记 showsRedraw', async () => {
    const entering = enterElementEdit('p')
    await vi.waitFor(() => expect(review().request).not.toBeNull())
    review().answer(true)
    expect(await entering).toBe(true)
    useUiStore.getState().setElementPanel(null)
    review().clear()
    expect(isSourceBackedZeroEdit(P())).toBe(true)
    expect(redrawOffered(P())).toBe(true)
    const past = S().past.length
    expect(await redrawWithScript('p')).toBe(true)
    expect(review().request).toBeNull()
    expect(P().showsRedraw).toBe(true)
    expect(S().past).toHaveLength(past + 1)
    expect(useUiStore.getState().elementPanelId).toBeNull()
  })

  it('不给角标的面板：不跑脚本', async () => {
    await seed(panel({ script: null }))
    expect(await redrawWithScript('p')).toBe(false)
    expect(render).not.toHaveBeenCalled()
  })

  it('清采用一并清 showsRedraw（换素材 / 重置后不能留半个状态）', () => {
    const p: Partial<PanelObject> = { sourceBackedFigure: undefined, showsRedraw: true }
    clearAdoption(p)
    expect(Object.hasOwn(p, 'showsRedraw')).toBe(false)
  })
})
