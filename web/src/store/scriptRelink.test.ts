/**
 * 接回挪走的脚本（ADR 0211 第一期 6）：先验证、跑成了才改登记。`relinkScript` 只做编排——跑选中的那份（现有试运行登记，
 * 失败时后端一个字节不动）、看这张图的归属是不是真的换到了它、换到了才按原来的图内修改重画。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/liveSync', () => ({ refreshAssetsAndSync: vi.fn(async () => null) }))
vi.mock('@/store/renderScheduler', () => ({ requestRender: vi.fn() }))
vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  relinkPrepare: vi.fn(async () => ({ runConfig: 'rc_000000000abc' })),
  relinkFinish: vi.fn(async () => undefined),
}))

import { relinkFinish, relinkPrepare } from '@/lib/api'
import { requestRender } from '@/store/renderScheduler'
import { useAssetStore } from '@/store/assetStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { relinkScript, relinkTarget } from './scriptRelink'

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
    expect(await relinkScript(panel, 'plot.py', 'scripts/plot.py')).toBe('linked')
    expect(requestRender).toHaveBeenCalledWith(panel, true)
    expect(vi.mocked(requestRender).mock.calls[0][0].overrides).toEqual(panel.overrides)
    // 参数随接回带走：试跑用后端带过来的默认配置；接上了让它们留下
    expect(relinkPrepare).toHaveBeenCalledWith('plot.py', 'scripts/plot.py', null)
    expect(vi.mocked(useScriptRunStore.getState().run).mock.calls[0]).toEqual(['scripts/plot.py', 'rc_000000000abc'])
    expect(relinkFinish).toHaveBeenCalledWith('plot.py', 'scripts/plot.py', true, null)
  })

  it('试运行失败：不重画、不算接上（后端没改登记，原来那张错误卡留着）', async () => {
    runResult(() =>
      useScriptRunStore.setState({
        byScript: { 'scripts/plot.py': { phase: 'failed', error: { code: 'script_error', message: 'boom' } } },
      } as never),
    )
    capability('source_missing', 'plot.py')
    expect(await relinkScript(panel, 'plot.py', 'scripts/plot.py')).toBe('not_linked')
    expect(requestRender).not.toHaveBeenCalled()
    // 没接上：后端撤销这次带过去的参数，新键下不留残留
    expect(relinkFinish).toHaveBeenCalledWith('plot.py', 'scripts/plot.py', false, null)
  })

  it('还差一个决定（运行目录 / 依赖）：不是失败，等用户在弹出的框里定', async () => {
    runResult(() =>
      useScriptRunStore.setState({
        byScript: { 'scripts/plot.py': { phase: 'needs_workdir', error: { code: 'workdir_confirmation_required' } } },
      } as never),
    )
    capability('source_missing', 'plot.py')
    expect(await relinkScript(panel, 'plot.py', 'scripts/plot.py')).toBe('gated')
    expect(requestRender).not.toHaveBeenCalled()
    // 门不是终局：不收尾（用户在弹框里定完再跑，后端下一次按注册表的事实收尾）
    expect(relinkFinish).not.toHaveBeenCalled()
  })

  it('跑成了但这张图没归到它（它画的是别的图）：不算接上，不重画', async () => {
    runResult(() => capability('source_missing', 'plot.py'))
    capability('source_missing', 'plot.py')
    expect(await relinkScript(panel, 'plot.py', 'other.py')).toBe('not_linked')
    expect(requestRender).not.toHaveBeenCalled()
  })

  it('跑成了但这张图没归到它：也收尾成「没接上」（撤销带过去的参数）', async () => {
    runResult(() => capability('source_missing', 'plot.py'))
    capability('source_missing', 'plot.py')
    await relinkScript(panel, 'plot.py', 'other.py')
    expect(relinkFinish).toHaveBeenCalledWith('plot.py', 'other.py', false, null)
  })
})

describe('relinkTarget：一键按钮上写什么（不截断）', () => {
  it.each([
    ['plot.py', 'scripts/plot.py', { kind: 'dir', text: 'scripts/' }],
    ['scripts/plot.py', 'plot.py', { kind: 'root', text: '' }],
    ['plot.py', 'new_name.py', { kind: 'file', text: 'new_name.py' }],
    ['plot.py', 'scripts/new_name.py', { kind: 'file', text: 'new_name.py' }],
    ['plot.py', 'analysis/2026-autumn/very/long/folder/plot.py', { kind: 'dir', text: 'folder/' }],
  ] as const)('%s → %s', (from, to, want) => {
    expect(relinkTarget(from, to)).toEqual(want)
  })
})
