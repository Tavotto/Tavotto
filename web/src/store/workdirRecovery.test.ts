import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: vi.fn(),
  setProjectWorkdir: vi.fn(),
  fetchEngineEnvironment: vi.fn(),
}))

import { engineRender, fetchEngineEnvironment, setProjectWorkdir, type EngineEnvironment, type WorkdirConfirmation } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useEnvStore } from './envStore'
import { renderKey, useRenderStore } from './renderStore'
import { useRuntimeAssetStore } from './runtimeAssetStore'
import { useScriptRunStore } from './scriptRunStore'

const environment = (mode: 'sandbox' | 'project'): EngineEnvironment => ({
  ok: true,
  project: { open: true, workdir: { mode, modes: ['sandbox', 'project'], decided: true } },
}) as EngineEnvironment
const hint: WorkdirConfirmation = {
  kind: 'workdir', code: 'workdir_confirmation_required', script: 'plot.py',
  reason: 'script_dir_evidence', recommended: 'project', options: [], conflicts: [], reads: [],
  probes: ['data/run_*.csv'], source: 'runtime',
}
const response = (points: number[], extra = {}) => ({
  rev: 1, manifest: { stem: 'plot', size_mm: [80, 60], elements: [], points },
  svg: `<svg data-points="${points.length}"/>`, warnings: [], ...extra,
})

beforeEach(() => {
  vi.mocked(engineRender).mockReset()
  vi.mocked(setProjectWorkdir).mockReset()
  vi.mocked(fetchEngineEnvironment).mockResolvedValue(environment('sandbox'))
  setCurrentProjectId('glob-project')
  useRenderStore.getState().clear()
  useRuntimeAssetStore.getState().clear()
  useScriptRunStore.getState().clear()
  useEnvStore.setState({ env: environment('sandbox'), workdirConfirmation: null })
  vi.mocked(setProjectWorkdir).mockResolvedValue({
    ok: true, project: environment('project').project, workdir: environment('project').project!.workdir,
  } as never)
})
afterEach(() => {
  useRenderStore.getState().clear()
  useScriptRunStore.getState().clear()
  setCurrentProjectId(null)
  vi.restoreAllMocks()
})

describe('选择正确运行目录后的图像恢复（#1254）', () => {
  it('成功但缺数据的图及其它旧目录变体都作废，编辑保留并能重画出数据', async () => {
    const patches = [{ gid: 'axes:0', prop: 'title', value: 'My title' }]
    vi.mocked(engineRender).mockResolvedValue(response([], { workdir_hint: hint }) as never)
    await useRenderStore.getState().render('plot.pdf', patches)
    vi.mocked(engineRender).mockResolvedValue(response([8, 9]) as never)
    await useRenderStore.getState().render('other.pdf', [])
    const key = renderKey('plot.pdf', patches)
    expect(useRenderStore.getState().byKey[key].status).toBe('ready')

    expect(await useEnvStore.getState().setWorkdirMode('project', { confirmed: true })).toBeNull()
    expect(useRenderStore.getState().byKey[key].stale).toBe(true)
    expect(useRenderStore.getState().byKey[key].lastPatches).toBeNull()
    expect(useRenderStore.getState().byKey[renderKey('other.pdf', [])].stale).toBe(true)

    vi.mocked(engineRender).mockResolvedValue(response([1, 2, 3]) as never)
    await useRenderStore.getState().render('plot.pdf', patches)
    const recovered = useRenderStore.getState().byKey[key]
    expect(recovered.status).toBe('ready')
    expect(recovered.stale).toBe(false)
    expect(recovered.workdirHint).toBeNull()
    expect(recovered.svg).toContain('data-points="3"')
    expect(vi.mocked(engineRender).mock.lastCall?.[1]).toEqual(patches)
  })

  it('旧目录下尚未返回的空图不能在切换之后重新成为权威', async () => {
    let finish!: (value: never) => void
    vi.mocked(engineRender).mockReturnValue(new Promise((resolve) => { finish = resolve }))
    const pending = useRenderStore.getState().render('plot.pdf', [])
    await vi.waitFor(() => expect(engineRender).toHaveBeenCalledTimes(1))
    expect(await useEnvStore.getState().setWorkdirMode('project', { confirmed: true })).toBeNull()
    finish(response([], { workdir_hint: hint }) as never)
    await pending
    const old = useRenderStore.getState().byKey[renderKey('plot.pdf', [])]
    expect(old.stale).toBe(true)
    expect(old.lastPatches).toBeNull()
    expect(old.manifest).toBeNull()
  })

  it('旧目录下捕获的脚本结果不再作为添加到画布的依据', async () => {
    useScriptRunStore.setState({ byScript: {
      'plot.py': { phase: 'captured_one', descriptors: [], error: null, droppedFigures: 0, cancelRequested: false, gen: 1 },
    } })
    expect(await useEnvStore.getState().setWorkdirMode('project', { confirmed: true })).toBeNull()
    expect(useScriptRunStore.getState().byScript['plot.py']).toBeUndefined()
  })

  it('确认继续用同一目录只恢复门禁，不作废成功图', async () => {
    vi.mocked(engineRender).mockResolvedValue(response([1, 2, 3]) as never)
    await useRenderStore.getState().render('plot.pdf', [])
    vi.mocked(setProjectWorkdir).mockResolvedValue({ ok: true, project: environment('sandbox').project } as never)
    expect(await useEnvStore.getState().setWorkdirMode('sandbox', { confirmed: true })).toBeNull()
    expect(setProjectWorkdir).toHaveBeenCalledWith('sandbox')
    expect(useRenderStore.getState().byKey[renderKey('plot.pdf', [])].stale).toBe(false)
  })

  it('A → B → A 后的旧目录答复不能作废新一代图', async () => {
    let finish!: (value: never) => void
    vi.mocked(setProjectWorkdir).mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
    const changing = useEnvStore.getState().setWorkdirMode('project', { confirmed: true })
    setCurrentProjectId('other-project')
    useEnvStore.getState().resetProject()
    setCurrentProjectId('glob-project')
    useEnvStore.getState().resetProject()
    useEnvStore.setState({ env: environment('sandbox') })
    vi.mocked(engineRender).mockResolvedValue(response([7, 8, 9]) as never)
    await useRenderStore.getState().render('new.pdf', [])
    finish({ ok: true, project: environment('project').project } as never)
    await changing
    expect(useEnvStore.getState().env?.project?.workdir?.mode).toBe('sandbox')
    expect(useRenderStore.getState().byKey[renderKey('new.pdf', [])].stale).toBe(false)
  })

  it('切换失败时保留已经渲染好的图', async () => {
    vi.mocked(engineRender).mockResolvedValue(response([], { workdir_hint: hint }) as never)
    await useRenderStore.getState().render('plot.pdf', [])
    vi.mocked(setProjectWorkdir).mockRejectedValue(new Error('cannot change directory'))
    expect(await useEnvStore.getState().setWorkdirMode('project', { confirmed: true })).toBe('cannot change directory')
    const entry = useRenderStore.getState().byKey[renderKey('plot.pdf', [])]
    expect(entry.stale).toBe(false)
    expect(entry.status).toBe('ready')
  })
})
