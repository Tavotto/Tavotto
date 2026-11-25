/**
 * 接回挪走的脚本（ADR 0211 第一期 6）：先验证、跑成了才改登记。`relinkScript` 只做编排——跑选中的那份（现有试运行登记，
 * 失败时后端一个字节不动）、看这张图的归属是不是真的换到了它、换到了才按原来的图内修改重画。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/liveSync', () => ({ refreshAssetsAndSync: vi.fn(async () => null) }))
vi.mock('@/store/renderScheduler', () => ({ requestRender: vi.fn() }))

import { requestRender } from '@/store/renderScheduler'
import { useAssetStore } from '@/store/assetStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { relinkScript } from './scriptRelink'

const panel: PanelObject = {
  id: 'p1', type: 'panel', fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 80, nativeH: 60,
  x: 0, y: 0, w: 80, h: 60, script: 'plot.py',
  overrides: [{ gid: 'axes_0.title', prop: 'text', value: '改过的标题' }],
}

function capability(status: string, script: string | null) {
  useAssetStore.setState({
    byId: {
      'Fig1.pdf': {
        id: 'Fig1.pdf',
        capability: { status, reason_code: '', script, candidates: [], can_probe: false, can_manual_link: true },
      },
    } as never,
  })
}

/** 试运行的结局：用 setState 注入（不对 getState() spyOn，L-086） */
function runResult(after: () => void) {
  useScriptRunStore.setState({
    run: vi.fn(async () => after()),
  } as never)
}

beforeEach(() => {
  useScriptRunStore.setState({ byScript: {} } as never)
  vi.mocked(requestRender).mockClear()
  useUiStore.getState().setStatus(null as never, 'info')
})
afterEach(() => vi.clearAllMocks())

describe('relinkScript', () => {
  it('跑成了、图归到选中的那份：重画（面板与图内修改原样），说一句已接到', async () => {
    runResult(() => capability('editable', 'scripts/plot.py'))
    capability('source_missing', 'plot.py')
    expect(await relinkScript(panel, 'scripts/plot.py')).toBe('linked')
    expect(requestRender).toHaveBeenCalledWith(panel, true)
    expect(vi.mocked(requestRender).mock.calls[0][0].overrides).toEqual(panel.overrides)
  })

  it('试运行失败：不重画、不算接上（后端没改登记，原来那张错误卡留着）', async () => {
    runResult(() =>
      useScriptRunStore.setState({
        byScript: { 'scripts/plot.py': { phase: 'failed', error: { code: 'script_error', message: 'boom' } } },
      } as never),
    )
    capability('source_missing', 'plot.py')
    expect(await relinkScript(panel, 'scripts/plot.py')).toBe('not_linked')
    expect(requestRender).not.toHaveBeenCalled()
  })

  it('还差一个决定（运行目录 / 依赖）：不是失败，等用户在弹出的框里定', async () => {
    runResult(() =>
      useScriptRunStore.setState({
        byScript: { 'scripts/plot.py': { phase: 'needs_workdir', error: { code: 'workdir_confirmation_required' } } },
      } as never),
    )
    capability('source_missing', 'plot.py')
    expect(await relinkScript(panel, 'scripts/plot.py')).toBe('gated')
    expect(requestRender).not.toHaveBeenCalled()
  })

  it('跑成了但这张图没归到它（它画的是别的图）：不算接上，不重画', async () => {
    runResult(() => capability('source_missing', 'plot.py'))
    capability('source_missing', 'plot.py')
    expect(await relinkScript(panel, 'other.py')).toBe('not_linked')
    expect(requestRender).not.toHaveBeenCalled()
  })
})
