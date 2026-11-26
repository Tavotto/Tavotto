// T-005：画布导出前，把有脚本、没动过的 PNG 面板转成能由脚本重画的图——只做不需要用户对照的两件事
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { EngineError, type PanelInfo } from '@/lib/api'
import { emptyProject, type PanelObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useAssetStore } from './assetStore'
import { useUiStore } from './uiStore'
import { useEnvStore } from './envStore'
import { useRenderStore } from './renderStore'
import { useSelectionStore } from './selectionStore'
import { pngRefusalOf, usePngAdmissionMemory } from './pngAdmissionMemory'
import { prepareScriptedBitmapsForRedraw, redrawWithScript } from './actions'
import { FIGSIZE_SOURCE_POLICY, SCRIPT_BACKED_SOURCE_POLICY } from '@/lib/artifactValidation'
import { setCurrentProjectId } from '@/lib/session'

const render = vi.fn()
const scriptBacked = vi.fn(async () => true)
vi.mock('@/lib/api', async (original) => ({
  ...(await original<typeof import('@/lib/api')>()),
  engineRender: (...args: unknown[]) => render(...args),
  // 开关开着也不该试来源驱动：差异要用户亲眼对照（ADR 0139 §二）
  fetchScriptBackedEnabled: () => scriptBacked(),
}))

const FRAME = { gid: 'figure', prop: 'frame', value: 'figsize' }
const sha = 'a'.repeat(64)
const source = { render_policy: FIGSIZE_SOURCE_POLICY, source_id: 'plot.png', origin: 'static', kind: 'png', bytes_sha256: sha, size_bytes: 10 }
const ok = () => ({ rev: 1, svg: '<svg/>', manifest: { size_mm: [127, 81.28], elements: [] }, artifact_source: source })
const refused = (reason: string) =>
  new EngineError('refused', '', 'artifact_source_unavailable', '', undefined, undefined, { params: { reason } })
const panel = (extra: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p', type: 'panel', fileId: 'plot.png', fileKind: 'raster', script: 'figure.py', figureFrame: 1,
  nativeW: 126.99, nativeH: 81.13, pxW: 360, pxH: 230, x: 17, y: 21, w: 63, h: 47, overrides: [], ...extra,
})
const info = (id: string, extra: Partial<PanelInfo> = {}): PanelInfo => ({
  id, name: 'plot', folder: '.', kind: 'raster', native_w_mm: 127, native_h_mm: 81.28, mtime: 0, script: 'figure.py', ...extra,
})
const S = () => useDocumentStore.getState()
const P = () => S().doc.objects[0] as PanelObject

async function seed(p: PanelObject = panel(), panels: PanelInfo[] = [info('plot.png')]) {
  await S().switchDocument(emptyProject(), 'bitmap-redraw-test')
  S().commit(literal('seed'), (d) => { d.objects.push(p) })
  useDocumentStore.setState({ past: [], future: [] })
  useAssetStore.setState({ panels, byId: Object.fromEntries(panels.map((x) => [x.id, x])) })
}

beforeEach(async () => {
  render.mockReset().mockResolvedValue(ok())
  scriptBacked.mockClear()
  useRenderStore.getState().clear()
  useUiStore.getState().setElementPanel(null)
  useSelectionStore.getState().clear()
  usePngAdmissionMemory.getState().clear()
  useEnvStore.getState().resetProject()
  setCurrentProjectId('A')
  await seed()
})

describe('prepareScriptedBitmapsForRedraw', () => {
  it('精确采用通过：写 FRAME + 守卫，画布导出由脚本重画；不进图内编辑；一次可撤销', async () => {
    expect(await prepareScriptedBitmapsForRedraw(['p'])).toEqual({ p: 'adopted' })
    expect(render).toHaveBeenCalledTimes(1)
    expect(render.mock.calls[0]).toEqual(['plot.png', [], { source_policy: FIGSIZE_SOURCE_POLICY }])
    expect(P().overrides).toEqual([FRAME])
    expect(P().artifactValidation).toMatchObject({ version: 1, requiredFrame: 'figsize', sourceId: 'plot.png', bytesSha256: sha })
    expect([P().nativeW, P().nativeH]).toEqual([127, 81.28])
    expect([P().x, P().y, P().w, P().h]).toEqual([17, 21, 63, 47])
    expect(useUiStore.getState().elementPanelId).toBeNull()
    S().undo()
    expect(P().overrides).toEqual([])
    expect(P().artifactValidation).toBeUndefined()
  })

  it('精确策略被拒（像素对不上）：保留原件、不试来源驱动、记住拒绝，同一张图不再重跑脚本', async () => {
    render.mockRejectedValue(refused('source_mismatch'))
    const before = structuredClone(P())
    expect(await prepareScriptedBitmapsForRedraw(['p'])).toEqual({ p: 'kept' })
    expect(render).toHaveBeenCalledTimes(1)
    for (const call of render.mock.calls) expect(JSON.stringify(call)).not.toContain(SCRIPT_BACKED_SOURCE_POLICY)
    expect(scriptBacked).not.toHaveBeenCalled()
    expect(P()).toEqual(before)
    expect(S().past).toHaveLength(0)
    expect(await prepareScriptedBitmapsForRedraw(['p'])).toEqual({ p: 'kept' })
    expect(render).toHaveBeenCalledTimes(1)
    // 这条拒绝只挡精确：不进共享的采用记忆，角标与进图内编辑仍能走到来源驱动的对照
    expect(pngRefusalOf('plot.png')).toBeNull()
  })

  it('只挡精确的拒绝之后，「用脚本重画」照样走到来源驱动的对照', async () => {
    render.mockImplementation(async (_id: unknown, _p: unknown, opts?: { source_policy?: string }) => {
      if (opts?.source_policy === FIGSIZE_SOURCE_POLICY) throw refused('source_mismatch')
      throw refused('unsupported_artist')
    })
    expect(await prepareScriptedBitmapsForRedraw(['p'])).toEqual({ p: 'kept' })
    render.mockClear()
    await redrawWithScript('p')
    const policies = render.mock.calls.map((c) => (c[2] as { source_policy?: string } | undefined)?.source_policy)
    expect(policies).toEqual([FIGSIZE_SOURCE_POLICY, SCRIPT_BACKED_SOURCE_POLICY])
  })

  it('换了项目代际：导出前那条只挡精确的记录作废，再导出会重试', async () => {
    render.mockRejectedValue(refused('source_mismatch'))
    await prepareScriptedBitmapsForRedraw(['p'])
    usePngAdmissionMemory.getState().clear()
    await prepareScriptedBitmapsForRedraw(['p'])
    expect(render).toHaveBeenCalledTimes(2)
  })

  it('要先回答环境问题的：保留原件，不在导出途中弹出工作目录 / 依赖对话框', async () => {
    const confirmation = { kind: 'workdir' as const, code: 'workdir_confirmation_required', script: 'figure.py', reason: 'project_root_evidence', recommended: 'project_root', options: [], conflicts: [], reads: [] }
    render.mockRejectedValue(new EngineError('choose', '', 'workdir_confirmation_required', '', undefined, undefined, { confirmation } as never))
    expect(await prepareScriptedBitmapsForRedraw(['p'])).toEqual({ p: 'kept' })
    expect(useEnvStore.getState().workdirConfirmation).toBeNull()
    expect(P().overrides).toEqual([])
  })

  it('素材清单里没有脚本 / 已经有编辑 / 已采用：不跑脚本', async () => {
    await seed(panel(), [info('plot.png', { script: undefined })])
    expect(await prepareScriptedBitmapsForRedraw(['p'])).toEqual({ p: 'kept' })
    await seed(panel({ overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'x' }] }))
    expect(await prepareScriptedBitmapsForRedraw(['p'])).toEqual({ p: 'kept' })
    expect(render).not.toHaveBeenCalled()
  })

  it('有矢量主文件：换成它，不发位图采用请求', async () => {
    await seed(panel({ fileId: 'fig1.png' }), [
      info('pdf/fig1.pdf', { kind: 'pdf', native_w_mm: 127, native_h_mm: 81.28, figure_key: 'script:fig1', primary: true }),
      info('fig1.png', { px_w: 360, px_h: 230, figure_key: 'script:fig1', primary: false }),
    ])
    expect(await prepareScriptedBitmapsForRedraw(['p'])).toEqual({ p: 'vector' })
    expect(P().fileId).toBe('pdf/fig1.pdf')
    expect(render).not.toHaveBeenCalled()
  })

  it('等回应期间文档变了：这次结果不提交', async () => {
    let resolve!: (v: unknown) => void
    render.mockReturnValue(new Promise((r) => { resolve = r }))
    const pending = prepareScriptedBitmapsForRedraw(['p'])
    await vi.waitFor(() => expect(render).toHaveBeenCalled())
    S().commit(literal('move'), (d) => { (d.objects[0] as PanelObject).x = 30 })
    resolve(ok())
    expect(await pending).toEqual({ p: 'kept' })
    expect(P().overrides).toEqual([])
  })
})
