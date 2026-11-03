/**
 * 编辑器里把 .py 拖到画布上（2026-10-27 用户拍板）。判据的主语：**发给准备会话 store 的 `open` 入参**（哪个项目相对脚本、
 * 带不带 `run`、要放进哪张画布）与说给用户的那一句；项目外 / 不是脚本 / 教程层盖着时一个 `open` 都不发。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { acceptsScriptDrag, runDroppedScript } from './useCanvasScriptDrop'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'

const open = vi.fn(async () => {})
const place = { canvasId: 'c_paper', at: { x: 40, y: 30 } }

beforeEach(() => {
  open.mockClear()
  useProjectPreparationStore.setState({ open } as never)
  useProjectStore.setState({ project: { open: true, figures_dir: '/Users/me/paper' } as never, switching: false })
  useOnboardingStore.setState({ surface: 'none' } as never)
  useUiStore.setState({ status: null })
})

describe('runDroppedScript', () => {
  it('项目里的脚本：「打开并运行」它，并记下要放进的画布与落点', () => {
    runDroppedScript('/Users/me/paper/plots/Fig5_spectra.py', place)
    expect(open).toHaveBeenCalledExactlyOnceWith({ script: 'plots/Fig5_spectra.py', reuse_last_success: true }, { run: true, placeIn: place })
  })

  it('项目外的脚本：不运行、不切项目，说清楚', () => {
    runDroppedScript('/Users/me/other/fig.py', place)
    expect(open).not.toHaveBeenCalled()
    expect(JSON.stringify(useUiStore.getState().status)).toContain('outsideProject')
  })

  it('文件名首尾带空格：与主页同一道闸，不运行', () => {
    runDroppedScript('/Users/me/paper/ fig.py', place)
    expect(open).not.toHaveBeenCalled()
  })
})

describe('acceptsScriptDrag', () => {
  it('系统拖来的文件才接（素材卡的内部拖动不归它管）', () => {
    expect(acceptsScriptDrag(['Files'])).toBe(true)
    expect(acceptsScriptDrag(['application/x-panel-id', 'text/plain'])).toBe(false)
    expect(acceptsScriptDrag(['text/plain'])).toBe(false)
  })

  it('教程层盖着 / 正在切项目：不接', () => {
    useOnboardingStore.setState({ surface: 'tutorial' } as never)
    expect(acceptsScriptDrag(['Files'])).toBe(false)
    useOnboardingStore.setState({ surface: 'none' } as never)
    useProjectStore.setState({ switching: true })
    expect(acceptsScriptDrag(['Files'])).toBe(false)
  })
})
