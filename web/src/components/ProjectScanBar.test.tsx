/**
 * 导入即扫描的轻量准备条（T02）：真的读后端快照、不阻塞、不抢焦点、不碰教程语义。
 *
 * 面板的每个断言都对着**后端给的快照形状**（不是手写一份「前端以为的」状态）：目标、部分检查、
 * 旧动作、先浏览（关闭只隐藏）、重新打开；以及两条边界——教程进行中不出现、从不夺焦点。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// 这组用例钉的是**旧**的同步试运行路径（T09 起它在本地开关关闭时才走；默认走准备面板，见
// `PreparationPanel.test.tsx` / `scriptLibraryPanel.test.tsx`）
vi.mock('@/lib/preparationFlag', () => ({ PREPARATION_PANEL_KEY: 'tavotto.preparationPanel', preparationPanelEnabled: () => false }))

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  startProjectScan: vi.fn(),
  fetchProjectScan: vi.fn(),
  cancelProjectScan: vi.fn(),
}))
vi.mock('@/store/scriptRunStore', () => ({
  useScriptRunStore: { getState: () => ({ run: runScript }) },
}))

import { ProjectScanBar } from '@/components/ProjectScanBar'
import { cancelProjectScan, fetchProjectScan, startProjectScan, type ProjectScan } from '@/lib/api'
import { ONBOARDING_DEFAULTS, useOnboardingStore } from '@/store/onboardingStore'
import { resetProjectScanBookkeeping, useProjectScanStore } from '@/store/projectScanStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'

const runScript = vi.fn()

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const snap = (over: Partial<ProjectScan> = {}): ProjectScan => ({
  scan_version: 1,
  project_id: 'pj-a',
  scan_id: 's1',
  epoch: 1,
  observation_seq: 1,
  reason: 'claim',
  state: 'complete',
  phase: 'awaiting_confirmation',
  outcome: { kind: 'target_found' },
  budget: { entries: 9, scripts: 2, assets: 0, elapsed_s: 0.02 },
  issues: [],
  assets: { count: 0, pdf: 0, raster: 0, browsable: false },
  scripts: [],
  targets: [
    {
      script: 'plot.py',
      role: 'plot',
      evidence: 'dynamic_stems',
      registered: false,
      entry: '__main__',
      scope: '.',
      checked: true,
      session_target: { script: 'plot.py', entry: '__main__' },
    },
  ],
  default_target: 'plot.py',
  target_choice: 'single',
  checks: [],
  environment: {
    verified: false,
    remembered: null,
    truncated: false,
    candidates: [
      { id: 'e1', source: 'project_venv', scope: 'project', label: '.venv', python_relative: '.venv/bin/python', status: 'unchecked' },
    ],
  },
  dependencies: { script: 'plot.py', files: ['requirements.txt'], requirements: 2, unsupported: [], evaluated: false },
  actions: [{ id: 'rescan', kind: 'rescan' }],
  ...over,
})

let host: HTMLElement
let root: Root

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<ProjectScanBar />)
  })
}
const bar = () => host.querySelector('[data-project-scan]')
const line = () => host.querySelector('[data-project-scan-line]')?.textContent ?? ''
const button = (text: string) =>
  [...host.querySelectorAll('button')].find((b) => b.textContent?.includes(text))

beforeEach(() => {
  resetProjectScanBookkeeping()
  useProjectScanStore.getState().clear()
  useUiStore.setState({ scanPanelOpen: false })
  useProjectStore.setState({ project: { open: true, id: 'pj-a' } } as never)
  useOnboardingStore.setState({ ...ONBOARDING_DEFAULTS })
  runScript.mockReset()
  vi.mocked(startProjectScan).mockReset()
  vi.mocked(cancelProjectScan).mockReset()
  vi.mocked(fetchProjectScan).mockReset()
  vi.mocked(fetchProjectScan).mockResolvedValue(snap())
})
afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
})

describe('该说的时候说清楚', () => {
  it('找到唯一的绘图脚本：点名它，并说明已有的图可以直接排版', async () => {
    useProjectScanStore.setState({ scan: snap() })
    await mount()
    expect(bar()).not.toBeNull()
    expect(line()).toContain('plot.py')
    expect(line()).toContain('直接排版')
    expect(bar()?.getAttribute('data-scan-phase')).toBe('awaiting_confirmation')
  })

  it('看不全：把「没检查完」说出来，详情里逐条列出账本', async () => {
    useProjectScanStore.setState({
      scan: snap({
        state: 'partial',
        issues: [{ code: 'unreadable_dir', severity: 'partial', scope: 'dir', path: 'locked', count: 1 }],
      }),
    })
    useUiStore.setState({ scanPanelOpen: true })
    await mount()
    expect(line()).toContain('没有检查完')
    expect(host.querySelector('[data-scan-issue="unreadable_dir"]')?.textContent).toContain('locked')
  })

  it('详情里环境永远是「未核验」，依赖只说声明了什么、没说装没装', async () => {
    useProjectScanStore.setState({ scan: snap() })
    useUiStore.setState({ scanPanelOpen: true })
    await mount()
    expect(host.querySelector('[data-scan-env]')?.textContent).toContain('都还没有核验')
    expect(host.querySelector('[data-scan-deps]')?.textContent).toContain('尚未检查是否已安装')
  })

  it('运行中只说「已发现」的计数，没有百分比与进度条', async () => {
    useProjectScanStore.setState({
      slow: true,
      scan: snap({
        state: 'running',
        phase: 'scanning',
        outcome: { kind: 'scanning' },
        found: { scripts: 4, assets: 9 },
        targets: undefined,
      }),
    })
    await mount()
    expect(line()).toMatch(/4.*9/)
    expect(host.querySelector('progress,[role="progressbar"]')).toBeNull()
    expect(button('取消检查')).toBeTruthy()
  })
})

describe('该安静的时候一个字不说', () => {
  it('静态项目（没有脚本）整条不出现', async () => {
    useProjectScanStore.setState({
      scan: snap({ outcome: { kind: 'static_source' }, phase: 'completed', targets: [], default_target: null }),
    })
    await mount()
    expect(bar()).toBeNull()
  })

  it('跑得快的扫描不闪：运行中但还没超过阈值时不出现', async () => {
    useProjectScanStore.setState({
      slow: false,
      scan: snap({ state: 'running', phase: 'scanning', outcome: { kind: 'scanning' } }),
    })
    await mount()
    expect(bar()).toBeNull()
  })

  it('教程进行中 / 当前就是教程副本：不出现（教程语义不被共用）', async () => {
    useProjectScanStore.setState({ scan: snap() })
    useOnboardingStore.setState({ status: 'active' })
    await mount()
    expect(bar()).toBeNull()
    act(() => root.unmount())
    host.remove()
    useOnboardingStore.setState({ status: 'not_started' })
    useProjectStore.setState({ project: { open: true, id: 'pj-a', tutorial: true } } as never)
    await mount()
    expect(bar()).toBeNull()
  })
})

describe('三个不同的动作', () => {
  it('关闭只隐藏这一轮，不取消扫描、不发任何请求；命令面板的「重新打开」能叫回来', async () => {
    useProjectScanStore.setState({ scan: snap() })
    await mount()
    await act(async () => button('关闭')!.click())
    expect(bar()).toBeNull()
    expect(cancelProjectScan).not.toHaveBeenCalled()
    expect(startProjectScan).not.toHaveBeenCalled()
    await act(async () => useProjectScanStore.getState().reopen())
    expect(bar()).not.toBeNull()
  })

  it('取消检查打的是取消扫描的端点，不是别的', async () => {
    vi.mocked(cancelProjectScan).mockResolvedValue(snap({ state: 'running', phase: 'scanning', outcome: { kind: 'scanning' } }))
    useProjectScanStore.setState({
      slow: true,
      scan: snap({ state: 'running', phase: 'scanning', outcome: { kind: 'scanning' }, found: { scripts: 0, assets: 0 } }),
    })
    await mount()
    await act(async () => button('取消检查')!.click())
    expect(cancelProjectScan).toHaveBeenCalledTimes(1)
  })

  it('重新检查 = force 的新一轮', async () => {
    vi.mocked(startProjectScan).mockResolvedValue(snap({ scan_id: 's2' }))
    useProjectScanStore.setState({ scan: snap() })
    await mount()
    await act(async () => button('重新检查')!.click())
    expect(startProjectScan).toHaveBeenCalledWith({ force: true, reason: 'manual' })
  })

  it('旧动作「试运行」是用户显式点击才触发的既有素材库动作', async () => {
    useProjectScanStore.setState({ scan: snap() })
    useUiStore.setState({ scanPanelOpen: true })
    await mount()
    expect(runScript).not.toHaveBeenCalled() // 渲染本身不执行任何东西
    await act(async () => button('试运行')!.click())
    expect(runScript).toHaveBeenCalledWith('plot.py')
  })
})

describe('不阻塞、不抢焦点', () => {
  it('挂载 / 更新时从不夺焦点，也不是对话框', async () => {
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    outside.focus()
    useProjectScanStore.setState({ scan: snap() })
    await mount()
    expect(document.activeElement).toBe(outside)
    await act(async () => useProjectScanStore.setState({ scan: snap({ observation_seq: 2 }) }))
    expect(document.activeElement).toBe(outside)
    expect(host.querySelector('[role="dialog"],[aria-modal="true"]')).toBeNull()
    outside.remove()
  })
})
