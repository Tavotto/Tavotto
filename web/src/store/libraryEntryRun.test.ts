import { afterEach, describe, expect, it, vi } from 'vitest'
import { runEntryScript } from '@/store/libraryEntryRun'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useProjectStore } from '@/store/projectStore'

describe('库模块卡的「运行它」（TVD-Y5JF）', () => {
  afterEach(() => vi.restoreAllMocks())

  const spies = () => {
    const prepOpen = vi.fn().mockResolvedValue(undefined)
    const projectOpen = vi.fn().mockResolvedValue({})
    vi.spyOn(useProjectPreparationStore, 'getState').mockReturnValue({
      ...useProjectPreparationStore.getState(),
      open: prepOpen,
    })
    vi.spyOn(useProjectStore, 'getState').mockReturnValue({ ...useProjectStore.getState(), open: projectOpen })
    return { prepOpen, projectOpen }
  }

  it('入口在项目里：不换项目，直接打开并运行它', async () => {
    const { prepOpen, projectOpen } = spies()
    const ok = await runEntryScript({ path: '/p/tools/draw.py', in_project: true, direct: true, open_root: '/p', open_script: 'tools/draw.py', script: 'tools/draw.py' })
    expect(ok).toBe(true)
    expect(projectOpen).not.toHaveBeenCalled()
    expect(prepOpen).toHaveBeenCalledWith(expect.objectContaining({ script: 'tools/draw.py' }), { run: true })
  })

  it('入口在项目外：先打开仓库文件夹（带着这个脚本进来），再打开并运行', async () => {
    const { prepOpen, projectOpen } = spies()
    const ok = await runEntryScript({ path: '/repo/draw.py', in_project: false, direct: true, open_root: '/repo', open_script: 'draw.py' })
    expect(ok).toBe(true)
    expect(projectOpen).toHaveBeenCalledWith('/repo', false, { entry: 'script', script: 'draw.py' })
    expect(prepOpen).toHaveBeenCalledWith(expect.objectContaining({ script: 'draw.py' }), { run: true })
    expect(projectOpen.mock.invocationCallOrder[0]).toBeLessThan(prepOpen.mock.invocationCallOrder[0])
  })

  it('文件夹没能打开：不运行，回 false', async () => {
    const { prepOpen, projectOpen } = spies()
    projectOpen.mockRejectedValueOnce(new Error('denied'))
    expect(await runEntryScript({ path: '/repo/draw.py', in_project: false, direct: true, open_root: '/repo', open_script: 'draw.py' })).toBe(false)
    expect(prepOpen).not.toHaveBeenCalled()
  })
})
