import { beforeEach, describe, expect, it, vi } from 'vitest'
import { EngineError } from '@/lib/api'
import { literal } from '@/i18n'
import { emptyProject, type PanelObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useUiStore } from './uiStore'
import { useAssetStore } from './assetStore'
import { useSelectionStore } from './selectionStore'
import { useWorkspaceStore } from './workspace'
import { useMountedPngStore } from './mountedPngStore'
import { useEnvStore } from './envStore'
import { usePngAdmissionMemory } from './pngAdmissionMemory'
import { useSourceBackedReview } from './sourceBackedReview'
import { useRenderStore, renderKeyOf, rejectArtifactRenders } from './renderStore'
import { enterElementEdit, rebuildPanel, resetOverrides, restorePanelOverrides, reviewSourceBackedPanel } from './actions'
import { renderTargets } from '@/hooks/useEngineSync'
import { setCurrentProjectId } from '@/lib/session'
import { adoptionGuardOf, ArtifactValidationError, FIGSIZE_SOURCE_POLICY, SCRIPT_BACKED_SOURCE_POLICY, refuseArtifactOperation } from '@/lib/artifactValidation'

const world = vi.hoisted(() => ({ strictReason: 'artist_profile', flagOn: true, status: 'exact' as 'exact' | 'needs_confirmation', sha: '1'.repeat(64) }))
const render = vi.fn()
const preview = vi.fn()
const invalidate = vi.fn()
vi.mock('@/lib/api', async (original) => ({
  ...(await original<typeof import('@/lib/api')>()),
  engineRender: (...a: unknown[]) => render(...a),
  fetchScriptBackedEnabled: async () => world.flagOn,
  enginePreviewPngSnapshot: (...a: unknown[]) => preview(...a),
  engineInvalidate: (...a: unknown[]) => invalidate(...a),
}))
vi.mock('./styleWork', () => ({ hasPendingStyleWork: () => false }))

const FRAME = { gid: 'figure', prop: 'frame', value: 'figsize' }
const TITLE = { gid: 'axes_0.title', prop: 'fontsize', value: 14 }
const BYTES = 'a'.repeat(64)
const SHA1 = '1'.repeat(64)
const SHA2 = '2'.repeat(64)
const refused = (reason: string) =>
  new EngineError('refused', '', 'artifact_source_unavailable', '', undefined, undefined, { params: { reason } })

/** 假后端：严格策略按 `strictReason` 拒；来源驱动策略按 `flagOn` / 当前重画摘要答 */
const verdictBody = () => ({
  status: world.status,
  reconstruction_sha256: world.sha,
  metrics: world.status === 'exact'
    ? { differing_pixels: 0, differing_ratio: 0, total_pixels: 1000, bbox: null }
    : { differing_pixels: 30, differing_ratio: 0.03, total_pixels: 1000, bbox: [2, 3, 12, 9] },
})
const sourceEcho = (policy: string) => ({ render_policy: policy, source_id: 'plot.png', origin: 'static', kind: 'png', bytes_sha256: BYTES, size_bytes: 10 })
const base = () => ({ rev: 1, svg: '<svg/>', manifest: { size_mm: [127, 81.28], elements: [] } })

const panel = (): PanelObject => ({
  id: 'p', type: 'panel', fileId: 'plot.png', fileKind: 'raster', script: 'figure.py', figureFrame: 1,
  nativeW: 126.99, nativeH: 81.13, pxW: 360, pxH: 230, x: 17, y: 21, w: 63, h: 47,
  crop: { x: .11, y: .14, w: .62, h: .57 }, rotation: 90, flipH: true, flipV: true, opacity: .8, overrides: [],
})
const S = () => useDocumentStore.getState()
const P = () => S().doc.objects[0] as PanelObject
const ui = () => useUiStore.getState()
const review = () => useSourceBackedReview.getState()
const visual = (p: PanelObject) => ({ x: p.x, y: p.y, w: p.w, h: p.h, crop: p.crop, rotation: p.rotation, flipH: p.flipH, flipV: p.flipV, opacity: p.opacity, pxW: p.pxW, pxH: p.pxH })
const policies = () => render.mock.calls.map((c) => (c[2] as { source_policy?: string } | undefined)?.source_policy)

async function seed(p = panel()) {
  await S().switchDocument(emptyProject(), 'source-backed-test')
  S().commit(literal('seed'), (d) => { d.objects.push(p) })
  useDocumentStore.setState({ past: [], future: [] })
}

beforeEach(async () => {
  Object.assign(world, { strictReason: 'artist_profile', flagOn: true, status: 'exact', sha: SHA1 })
  render.mockReset().mockImplementation(async (_id: unknown, _patches: unknown, opts?: Record<string, unknown>) => {
    const policy = opts?.source_policy as string | undefined
    if (policy === FIGSIZE_SOURCE_POLICY) throw refused(world.strictReason)
    if (policy === SCRIPT_BACKED_SOURCE_POLICY) {
      if (!world.flagOn) throw refused('policy_disabled')
      const confirmed = opts?.confirmed_reconstruction_sha256
      if (confirmed !== undefined && confirmed !== world.sha) throw refused('reconstruction_changed')
      return { ...base(), artifact_source: sourceEcho(policy), source_backed: verdictBody() }
    }
    return base()
  })
  // 对照图来自第二次请求（preview_png），带它自己的准入结论
  preview.mockReset().mockImplementation(async () => ({ url: 'data:image/png;base64,RECON', manifest: base().manifest, sourceBacked: verdictBody() }))
  invalidate.mockReset().mockResolvedValue({ invalidated: true })
  useRenderStore.getState().clear(); ui().setElementPanel(null); ui().setStatus(null)
  useSelectionStore.getState().clear(); useWorkspaceStore.getState().clear(); useMountedPngStore.setState({ byPanel: {} })
  useAssetStore.setState({ byId: {} }); setCurrentProjectId('A'); await seed()
  useEnvStore.getState().resetProject(); usePngAdmissionMemory.getState().clear(); review().clear()
})

describe('strict policy first, source-backed second (ADR 0139 §四)', () => {
  it.each(['artist_profile', 'source_mismatch'])('%s + experiment on + exact: one commit shaped like the strict one, page geometry untouched', async (reason) => {
    world.strictReason = reason
    const before = structuredClone(P())
    expect(await enterElementEdit('p')).toBe(true)
    expect(policies().slice(0, 2)).toEqual([FIGSIZE_SOURCE_POLICY, SCRIPT_BACKED_SOURCE_POLICY])
    expect(review().request).toBeNull()
    expect(P().overrides).toEqual([FRAME])
    expect(P().sourceBackedFigure).toEqual({
      version: 1, policy: SCRIPT_BACKED_SOURCE_POLICY, sourceId: 'plot.png', bytesSha256: BYTES, sizeBytes: 10,
      status: 'exact', reconstructionSha256: SHA1,
    })
    expect(Object.hasOwn(P(), 'artifactValidation')).toBe(false)
    expect(P().nativeW).toBe(127); expect(P().nativeH).toBe(81.28)
    expect(visual(P())).toEqual(visual(before))
    expect(S().past).toHaveLength(1)
    expect(ui().elementPanelId).toBe('p')
  })
  it('needs_confirmation opens the comparison and writes nothing until the user confirms', async () => {
    world.status = 'needs_confirmation'
    const before = structuredClone(P())
    const entering = enterElementEdit('p')
    await vi.waitFor(() => expect(review().request).not.toBeNull())
    const r = review().request!
    expect(r.mode).toBe('adopt')
    expect(r.originalSrc).toMatch(/\/api\/file\?.*plot\.png/)
    expect(r.reconstructionSrc).toBe('data:image/png;base64,RECON')
    expect(r.verdict.metrics).toMatchObject({ differingPixels: 30, differingRatio: 0.03, bbox: [2, 3, 12, 9] })
    expect(P()).toEqual(before); expect(S().past).toHaveLength(0); expect(ui().elementPanelId).toBeNull()
    // 对照看的是同一份零编辑重画：预览请求带来源驱动策略与来源身份、不带编辑
    expect(preview.mock.calls[0].slice(0, 2)).toEqual(['plot.png', []])
    expect(preview.mock.calls[0][4]).toEqual({ source_policy: SCRIPT_BACKED_SOURCE_POLICY, expected_source: { bytes_sha256: BYTES, size_bytes: 10 } })
    review().answer(true)
    expect(await entering).toBe(true)
    expect(P().sourceBackedFigure).toMatchObject({ status: 'reconstructed', reconstructionSha256: SHA1 })
    expect(P().sourceBackedFigure!.confirmedAt).toMatch(/^\d{4}-\d\d-\d\dT/)
    expect(P().overrides).toEqual([FRAME])
    expect(visual(P())).toEqual(visual(before))
    expect(S().past).toHaveLength(1)
  })
  it('cancelling the comparison writes nothing and falls back to the PR1 way out', async () => {
    world.status = 'needs_confirmation'
    const before = structuredClone(P())
    const entering = enterElementEdit('p')
    await vi.waitFor(() => expect(review().request).not.toBeNull())
    review().answer(false)
    expect(await entering).toBe(false)
    expect(P()).toEqual(before); expect(Object.hasOwn(P(), 'sourceBackedFigure')).toBe(false)
    expect(S().past).toHaveLength(0); expect(ui().elementPanelId).toBeNull()
    expect(ui().status).toEqual({ key: 'artifact.artistProfile', ns: 'errors' })
    expect(ui().statusAction?.label).toEqual({ key: 'artifact.rebuildEditable', ns: 'errors' })
    expect(useRenderStore.getState().byKey[renderKeyOf(P())]).toBeUndefined()
  })
  it('experiment off: same as PR1 — the new policy is never even tried; the refusal sentence is remembered, no comparison, no guard', async () => {
    world.flagOn = false
    expect(await enterElementEdit('p')).toBe(false)
    expect(policies()).toEqual([FIGSIZE_SOURCE_POLICY])
    expect(review().request).toBeNull(); expect(Object.hasOwn(P(), 'sourceBackedFigure')).toBe(false)
    expect(ui().status).toEqual({ key: 'artifact.artistProfile', ns: 'errors' })
    ui().setStatus(null)
    const calls = render.mock.calls.length
    expect(await enterElementEdit('p')).toBe(false)
    expect(render.mock.calls.length).toBe(calls) // remembered: the script is not run again
  })
  it.each(['save_ambiguous', 'unsupported_render_state', 'capture_incomplete', 'destination_unresolved'])('%s never tries the source-backed policy', async (reason) => {
    world.strictReason = reason
    expect(await enterElementEdit('p')).toBe(false)
    expect(policies()).toEqual([FIGSIZE_SOURCE_POLICY])
  })
  it('the switch flipping off between the question and the request still ends in the PR1 way out', async () => {
    world.flagOn = true
    render.mockImplementation(async (_i: unknown, _p: unknown, opts?: { source_policy?: string }) => {
      throw refused(opts?.source_policy === FIGSIZE_SOURCE_POLICY ? 'artist_profile' : 'policy_disabled')
    })
    expect(await enterElementEdit('p')).toBe(false)
    expect(policies()).toEqual([FIGSIZE_SOURCE_POLICY, SCRIPT_BACKED_SOURCE_POLICY])
    expect(ui().status).toEqual({ key: 'artifact.artistProfile', ns: 'errors' })
  })
  it('the source-backed attempt failing for its own reason reports that reason', async () => {
    render.mockImplementation(async (_i: unknown, _p: unknown, opts?: { source_policy?: string }) => {
      if (opts?.source_policy === FIGSIZE_SOURCE_POLICY) throw refused('source_mismatch')
      throw refused('unsupported_render_state')
    })
    expect(await enterElementEdit('p')).toBe(false)
    expect(ui().status).toEqual({ key: 'artifact.unsupportedRenderState', ns: 'errors' })
    expect(Object.hasOwn(P(), 'sourceBackedFigure')).toBe(false)
  })
  it('strict success never touches the new policy', async () => {
    render.mockImplementation(async (_i: unknown, _p: unknown, opts?: { source_policy?: string }) => ({ ...base(), artifact_source: sourceEcho(opts?.source_policy ?? '') }))
    expect(await enterElementEdit('p')).toBe(true)
    expect(policies()[0]).toBe(FIGSIZE_SOURCE_POLICY)
    expect(policies()).not.toContain(SCRIPT_BACKED_SOURCE_POLICY)
    expect(Object.hasOwn(P(), 'sourceBackedFigure')).toBe(false)
    expect(P().artifactValidation).toBeTruthy()
  })
})

describe('edits carry the confirmation; a changed reconstruction never swaps the basis silently', () => {
  async function adopted(status: 'exact' | 'needs_confirmation') {
    world.status = status
    const entering = enterElementEdit('p')
    if (status === 'needs_confirmation') {
      await vi.waitFor(() => expect(review().request).not.toBeNull())
      review().answer(true)
    }
    expect(await entering).toBe(true)
    render.mockClear()
  }
  it.each(['exact', 'needs_confirmation'] as const)('%s: zero edits send no digest, a real edit sends the admitted one', async (status) => {
    await adopted(status)
    const g = adoptionGuardOf(P())!
    await useRenderStore.getState().render('plot.png', [FRAME], undefined, undefined, undefined, g)
    expect(render.mock.calls.at(-1)![2]).not.toHaveProperty('confirmed_reconstruction_sha256')
    await useRenderStore.getState().render('plot.png', [FRAME, TITLE], undefined, undefined, undefined, g)
    expect(render.mock.calls.at(-1)![1]).toEqual([FRAME, TITLE])
    expect(render.mock.calls.at(-1)![2]).toMatchObject({ source_policy: SCRIPT_BACKED_SOURCE_POLICY, confirmed_reconstruction_sha256: SHA1 })
  })
  it('reconstruction_changed keeps the edits, offers one button, and re-confirming updates only the digest', async () => {
    await adopted('exact')
    S().commit(literal('edit'), (d) => { (d.objects[0] as PanelObject).overrides.push({ ...TITLE }) })
    const edited = structuredClone(P())
    world.sha = SHA2; world.status = 'needs_confirmation' // 脚本 / 数据 / 环境变了
    await useRenderStore.getState().render('plot.png', P().overrides, undefined, undefined, undefined, adoptionGuardOf(P()))
    expect(P()).toEqual(edited) // 编辑与 guard 都原样
    expect(ui().status).toEqual({ key: 'artifact.reconstructionChanged', ns: 'errors' })
    expect(ui().statusAction?.label).toEqual({ key: 'artifact.reviewReconstruction', ns: 'errors' })
    const past = S().past.length
    ui().statusAction!.run()
    await vi.waitFor(() => expect(review().request).not.toBeNull())
    expect(review().request!.mode).toBe('changed')
    expect(P()).toEqual(edited); expect(S().past).toHaveLength(past)
    review().answer(true)
    await vi.waitFor(() => expect(P().sourceBackedFigure!.reconstructionSha256).toBe(SHA2))
    expect(P().sourceBackedFigure).toMatchObject({ status: 'reconstructed' })
    expect(P().overrides).toEqual(edited.overrides)
    expect(S().past).toHaveLength(past + 1)
    // 新摘要上去之后真实编辑的渲染带的是新摘要，不再被拒
    await vi.waitFor(() => expect(render.mock.calls.some((c) => (c[2] as Record<string, unknown>).confirmed_reconstruction_sha256 === SHA2)).toBe(true))
  })
  it('cancelling the re-comparison writes nothing', async () => {
    await adopted('exact')
    S().commit(literal('edit'), (d) => { (d.objects[0] as PanelObject).overrides.push({ ...TITLE }) })
    const edited = structuredClone(P()); const past = S().past.length
    world.sha = SHA2; world.status = 'needs_confirmation'
    await useRenderStore.getState().render('plot.png', P().overrides, undefined, undefined, undefined, adoptionGuardOf(P()))
    ui().statusAction!.run()
    await vi.waitFor(() => expect(review().request).not.toBeNull())
    review().answer(false)
    await vi.waitFor(() => expect(review().request).toBeNull())
    expect(P()).toEqual(edited); expect(S().past).toHaveLength(past)
  })
  it('re-entering with the same reconstruction is silent; exact→exact with a new digest refreshes without a dialog', async () => {
    await adopted('exact')
    ui().setElementPanel(null)
    const past = S().past.length
    expect(await enterElementEdit('p')).toBe(true)
    expect(review().request).toBeNull(); expect(S().past).toHaveLength(past)
    ui().setElementPanel(null)
    world.sha = SHA2 // 字节摘要变了（元数据），逐像素仍与原件相同
    expect(await enterElementEdit('p')).toBe(true)
    expect(review().request).toBeNull()
    expect(P().sourceBackedFigure).toMatchObject({ status: 'exact', reconstructionSha256: SHA2 })
    expect(S().past).toHaveLength(past + 1)
  })
  it('re-entering after the redraw changed asks again instead of swapping the basis', async () => {
    await adopted('needs_confirmation')
    ui().setElementPanel(null)
    const guard = structuredClone(P().sourceBackedFigure)
    world.sha = SHA2
    const entering = enterElementEdit('p')
    await vi.waitFor(() => expect(review().request?.mode).toBe('changed'))
    expect(P().sourceBackedFigure).toEqual(guard)
    review().answer(false)
    expect(await entering).toBe(false)
    expect(P().sourceBackedFigure).toEqual(guard)
    expect(ui().elementPanelId).toBeNull()
  })
})

describe('the original is never written through a source-backed panel', () => {
  async function adoptedPanel() {
    world.status = 'exact'
    expect(await enterElementEdit('p')).toBe(true)
  }
  it('write-back, rebuild, restore and sync all refuse', async () => {
    await adoptedPanel()
    expect(() => refuseArtifactOperation(P())).toThrowError(expect.objectContaining({ reason: 'writeback_not_supported' }))
    expect(await rebuildPanel('p')).toBe('failed')
    expect(invalidate).not.toHaveBeenCalled()
    expect(() => restorePanelOverrides('p', literal('restore'), [FRAME, TITLE])).toThrow()
  })
  it('reset returns the panel to a plain PNG: both guards are gone', async () => {
    await adoptedPanel()
    resetOverrides('p')
    expect(Object.hasOwn(P(), 'sourceBackedFigure')).toBe(false)
    expect(Object.hasOwn(P(), 'artifactValidation')).toBe(false)
    expect(P().overrides).toEqual([])
  })
})

describe('zero edits do not run the script on reopen', () => {
  it('a zero-edit source-backed panel renders only while it is being edited; a real edit renders as usual', async () => {
    world.status = 'exact'
    await enterElementEdit('p')
    const zero = P()
    expect(renderTargets([zero], null, {}).map((o) => o.id)).toEqual([])
    expect(renderTargets([zero], null, { 'plot.png': true }).map((o) => o.id)).toEqual([]) // 脚本被 AI 改过也不自动重跑
    expect(renderTargets([zero], 'p', {}).map((o) => o.id)).toEqual(['p'])
    const edited = { ...zero, overrides: [...zero.overrides, TITLE] }
    expect(renderTargets([edited], null, {}).map((o) => o.id)).toEqual(['p'])
  })
})

describe('the confirmation binds to what the user was shown', () => {
  it('the preview response differs from the first render: the guard and the dialog use the preview one', async () => {
    world.status = 'needs_confirmation' // 第一次 render：摘要 SHA1
    preview.mockResolvedValueOnce({
      url: 'data:image/png;base64,SHOWN', manifest: base().manifest,
      sourceBacked: { ...verdictBody(), reconstruction_sha256: SHA2, metrics: { differing_pixels: 77, differing_ratio: 0.07, total_pixels: 1000, bbox: [0, 0, 5, 5] } },
    })
    const entering = enterElementEdit('p')
    await vi.waitFor(() => expect(review().request).not.toBeNull())
    expect(review().request!.verdict.reconstructionSha256).toBe(SHA2)
    expect(review().request!.verdict.metrics.differingPixels).toBe(77)
    review().answer(true)
    await entering
    expect(P().sourceBackedFigure).toMatchObject({ status: 'reconstructed', reconstructionSha256: SHA2 })
  })
  it('a preview without a usable verdict is not confirmable: no dialog, nothing written', async () => {
    world.status = 'needs_confirmation'
    preview.mockResolvedValueOnce({ url: 'data:image/png;base64,X', manifest: base().manifest })
    expect(await enterElementEdit('p')).toBe(false)
    expect(review().request).toBeNull(); expect(Object.hasOwn(P(), 'sourceBackedFigure')).toBe(false)
  })
})

describe('re-confirming targets the instance that is really stale', () => {
  const guardOf = (sha: string) => ({
    version: 1 as const, policy: SCRIPT_BACKED_SOURCE_POLICY, sourceId: 'plot.png', bytesSha256: BYTES, sizeBytes: 10,
    status: 'reconstructed' as const, reconstructionSha256: sha, confirmedAt: '2026-10-20T10:00:00.000Z',
  })
  async function twoInstances() {
    const make = (id: string, sha: string): PanelObject => ({ ...panel(), id, overrides: [FRAME, TITLE], sourceBackedFigure: guardOf(sha) })
    await seed(make('fresh', SHA1))
    S().commit(literal('second'), (d) => { d.objects.push(make('old', '9'.repeat(64))) })
    useDocumentStore.setState({ past: [], future: [] })
  }
  it('the failing render’s own guard picks the toast owner', async () => {
    await twoInstances()
    rejectArtifactRenders('plot.png', guardOf('9'.repeat(64)), new ArtifactValidationError('reconstruction_changed'))
    expect(ui().statusOwner).toBe('source.review:old')
  })
  it('clicking the first (already current) instance re-confirms the stale sibling, not itself', async () => {
    await twoInstances()
    world.status = 'needs_confirmation'; world.sha = SHA1
    void reviewSourceBackedPanel('fresh')
    await vi.waitFor(() => expect(review().request).not.toBeNull())
    expect(review().request!.panelId).toBe('old')
    review().answer(true)
    await vi.waitFor(() => expect((S().doc.objects.find((o) => o.id === 'old') as PanelObject).sourceBackedFigure!.reconstructionSha256).toBe(SHA1))
    expect((S().doc.objects.find((o) => o.id === 'fresh') as PanelObject).sourceBackedFigure).toEqual(guardOf(SHA1))
  })
  it('a rejection of one instance leaves the other instance’s renders alone', async () => {
    await twoInstances()
    const fresh = S().doc.objects.find((o) => o.id === 'fresh') as PanelObject
    const old = S().doc.objects.find((o) => o.id === 'old') as PanelObject
    for (const p of [fresh, old]) useRenderStore.getState().patch(renderKeyOf(p), { fileId: p.fileId, artifactValidation: p.sourceBackedFigure, status: 'ready', stale: false })
    rejectArtifactRenders('plot.png', old.sourceBackedFigure!, new ArtifactValidationError('reconstruction_changed'))
    expect(useRenderStore.getState().byKey[renderKeyOf(old)].status).toBe('error')
    expect(useRenderStore.getState().byKey[renderKeyOf(fresh)].status).toBe('ready')
  })
})
