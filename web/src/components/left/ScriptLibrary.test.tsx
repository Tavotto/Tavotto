/**
 * 素材库「脚本」区（Session 5）：所有合理脚本可见、文案按状态区分、
 * 「运行并发现图」“取消”与多 Figure 结果、safe 失败的恢复路径。
 *
 * 负向反证的看护点：
 *   #1 show-only（no_static_output）脚本必须出现在列表里且可运行；
 *   #4 多 Figure 的结果弹层必须列出**每一张**；
 *   #3 的前端半边：取消按钮真的调 cancelProbe（后端半边在
 *      tests/test_asset_library.py 的 sentinel）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchRegistry: vi.fn(),
  probeScript: vi.fn(),
  cancelProbe: vi.fn().mockResolvedValue({ cancelling: true }),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
}))

import {
  cancelProbe,
  fetchRegistry,
  probeScript,
  type CapturedFigureDescriptor,
  type ProbeResult,
  type RegistryView,
  type ScriptInventoryEntry,
} from '@/lib/api'
import { i18n } from '@/i18n'
import { ScriptLibrary } from '@/components/left/ScriptLibrary'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useScriptRunStore } from '@/store/scriptRunStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockRegistry = vi.mocked(fetchRegistry)
const mockProbe = vi.mocked(probeScript)
const mockCancel = vi.mocked(cancelProbe)

const entry = (over: Partial<ScriptInventoryEntry>): ScriptInventoryEntry => ({
  script: 'a.py',
  registered: false,
  static_stems: [],
  entry_candidates: ['__main__'],
  reason: 'no_static_output',
  can_probe: true,
  ...over,
})

const view = (all: ScriptInventoryEntry[], scripts: RegistryView['scripts'] = {}): RegistryView => ({
  source: 'tavotto_registry.json',
  scripts,
  candidates: [],
  conflicts: {},
  all_scripts: all,
})

const desc = (stem: string): CapturedFigureDescriptor => ({
  asset_id: `runtime:show.py#${stem}`,
  script: 'show.py',
  entry: '__main__',
  stem,
  capture_source: 'pyplot',
  execution_profile: 'safe',
  original_artifact: null,
  size_mm: [100, 80],
  source_fingerprint: 'sha256:x',
  can_writeback_artifact: false,
  can_writeback_source: false,
})

const ok = (descriptors: CapturedFigureDescriptor[], dropped = 0): ProbeResult => ({
  script: 'show.py',
  entry: '__main__',
  stems: descriptors.map((d) => d.stem),
  descriptors,
  error: null,
  tried: ['__main__'],
  registered: true,
  dropped_figures: dropped,
})

let host: HTMLElement
let root: Root

const flush = async () => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
}

async function mount(query = '') {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<ScriptLibrary query={query} />)
  })
  await flush()
}

const buttonByText = (text: string): HTMLButtonElement => {
  const btn = [...host.querySelectorAll('button')].find((b) =>
    (b.textContent ?? '').includes(text),
  )
  if (!btn) throw new Error(`没有找到按钮: ${text}`)
  return btn as HTMLButtonElement
}

beforeEach(() => {
  localStorage.clear()
  useScriptLibraryStore.getState().clear()
  useScriptRunStore.getState().clear()
  mockRegistry.mockReset()
  mockProbe.mockReset()
  mockCancel.mockClear()
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('脚本区列表', () => {
  it('所有合理脚本可见：show-only / 动态命名 / 已登记 / 工具脚本（反证 #1）', async () => {
    mockRegistry.mockResolvedValue(
      view(
        [
          entry({ script: 'show.py', reason: 'no_static_output' }),
          entry({ script: 'dyn.py', reason: 'dynamic_stems' }),
          entry({ script: 'linked.py', reason: 'registered', registered: true }),
          entry({ script: 'conftest.py', reason: 'infrastructure' }),
        ],
        { 'linked.py': { entry: 'main', cost: 'medium', notes: '', stems: ['f1', 'f2'] } },
      ),
    )
    await mount()
    const text = host.textContent ?? ''
    expect(text).toContain('show.py')
    expect(text).toContain('dyn.py')
    expect(text).toContain('linked.py')
    expect(text).toContain('conftest.py')
    // 分组文案：尚未运行 vs 输出名称只能在运行后确定 vs 已关联数
    expect(text).toContain('这个脚本尚未运行')
    expect(text).toContain('输出名称只能在运行后确定')
    expect(text).toContain('已关联 2 张图')
  })

  it('safe 模式首次说明可见、关掉后不再出现', async () => {
    mockRegistry.mockResolvedValue(view([entry({})]))
    await mount()
    expect(host.textContent).toContain('安全导入会隔离脚本写入')
    await act(async () => buttonByText('知道了').click())
    expect(host.textContent).not.toContain('安全导入会隔离脚本写入')
    await act(async () => root.unmount())
    await mount()
    expect(host.textContent).not.toContain('安全导入会隔离脚本写入')
  })

  it('英文主路径不得泄漏中文', async () => {
    mockRegistry.mockResolvedValue(
      view([
        entry({ script: 'show.py', reason: 'no_static_output' }),
        entry({ script: 'dyn.py', reason: 'dynamic_stems' }),
      ]),
    )
    await i18n.changeLanguage('en-US')
    await mount()
    expect(host.textContent ?? '').not.toMatch(/[一-鿿]/)
  })
})

describe('运行 / 取消 / 结果', () => {
  it('点击「运行并发现图」→ 调 probe、显示 loading、可取消；取消后焦点留在原按钮', async () => {
    mockRegistry.mockResolvedValue(view([entry({ script: 'show.py' })]))
    let resolveProbe!: (r: ProbeResult) => void
    mockProbe.mockImplementation(() => new Promise((r) => (resolveProbe = r)))
    await mount()

    const btn = buttonByText('运行并发现图')
    await act(async () => {
      btn.focus()
      btn.click()
    })
    expect(mockProbe).toHaveBeenCalledWith('show.py')
    expect(host.textContent).toContain('正在启动渲染环境')
    // busy 态同一个按钮翻转成「取消」——focus 不搬家
    expect(btn.textContent).toContain('取消')
    expect(document.activeElement).toBe(btn)

    await act(async () => btn.click()) // 取消
    expect(mockCancel).toHaveBeenCalledWith('show.py')
    await act(async () => {
      resolveProbe({
        ...ok([]),
        registered: false,
        error: { code: 'execution_cancelled', message: '已中断' },
      })
    })
    await flush()
    expect(host.textContent).toContain('已取消')
    expect(document.activeElement).toBe(btn)
  })

  it('SSE probe.started 把「正在启动」推进到「正在运行」', async () => {
    mockRegistry.mockResolvedValue(view([entry({ script: 'show.py' })]))
    mockProbe.mockImplementation(() => new Promise(() => {}))
    await mount()
    await act(async () => buttonByText('运行并发现图').click())
    expect(host.textContent).toContain('正在启动渲染环境')
    await act(async () => useScriptRunStore.getState().markRunning('show.py'))
    expect(host.textContent).toContain('正在运行脚本')
  })

  it('多 Figure：结果弹层列出每一张，全部可添加（反证 #4：只显示第一张这里红）', async () => {
    mockRegistry.mockResolvedValue(view([entry({ script: 'show.py' })]))
    mockProbe.mockResolvedValue(ok([desc('a'), desc('b'), desc('c')], 1))
    await mount()
    await act(async () => buttonByText('运行并发现图').click())
    await flush()
    expect(host.textContent).toContain('已发现 3 张图')
    await act(async () => buttonByText('查看捕获结果').click())
    const dialog = document.querySelector('[role="dialog"]')
    expect(dialog?.textContent).toContain('a')
    expect(dialog?.textContent).toContain('b')
    expect(dialog?.textContent).toContain('c')
    const addButtons = [...(dialog?.querySelectorAll('button') ?? [])].filter((b) =>
      (b.textContent ?? '').includes('添加到画布'),
    )
    expect(addButtons).toHaveLength(3)
    // 超上限被丢弃的张数如实显示
    expect(dialog?.textContent).toContain('还有 1 张未捕获')
  })

  it('缺包失败：错误按 code 翻译，出现恢复路径，且没有可点的 native 按钮', async () => {
    mockRegistry.mockResolvedValue(view([entry({ script: 'show.py' })]))
    mockProbe.mockResolvedValue({
      ...ok([]),
      registered: false,
      error: {
        code: 'missing_dependency',
        message: '缺少依赖包：pandas（当前渲染环境里没有它）',
        params: { module: 'pandas' },
        traceback: 'ModuleNotFoundError: pandas',
      },
    })
    await mount()
    await act(async () => buttonByText('运行并发现图').click())
    await flush()
    expect(host.textContent).toContain('pandas')
    expect(host.textContent).toContain('可能依赖原来的 Python 环境')
    // 真实入口只有「选择渲染环境」与「复制诊断」；「按项目原方式运行」只在
    // 文案里（PR 2 未落地，不给可点但无功能的按钮）
    expect(buttonByText('选择渲染环境')).toBeTruthy()
    expect(buttonByText('复制诊断')).toBeTruthy()
    expect(
      [...host.querySelectorAll('button')].some((b) =>
        (b.textContent ?? '').includes('按项目原方式运行'),
      ),
    ).toBe(false)
  })

  it('没出图（script_no_figure）不进「可能需要原环境」组', async () => {
    mockRegistry.mockResolvedValue(view([entry({ script: 'show.py' })]))
    mockProbe.mockResolvedValue({
      ...ok([]),
      registered: false,
      error: { code: 'script_no_figure', message: '没有捕获到任何 Figure', params: { entry: '__main__' } },
    })
    await mount()
    await act(async () => buttonByText('运行并发现图').click())
    await flush()
    expect(host.textContent).not.toContain('可能需要原环境')
    expect(host.textContent).toContain('没有捕获到任何 Figure')
  })
})
