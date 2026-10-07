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
// 修复卡片的状态（缺包时脚本行上的那张卡，与画布上的是同一个 store）
import { __resetDepRepairParkingForTests, useDepRepairStore } from '@/store/depRepairStore'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchRegistry: vi.fn(),
  probeScript: vi.fn(),
  cancelProbe: vi.fn().mockResolvedValue({ cancelling: true }),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
  createDependencyPlan: vi.fn(),
  installDependencyPlan: vi.fn(),
  cancelDependencyPlan: vi.fn().mockResolvedValue({}),
  fetchEngineEnvironment: vi.fn().mockResolvedValue({}),
  setProjectEnvironment: vi.fn(),
  setEngineEnvironment: vi.fn(),
  createJointDependencyPlan: vi.fn(),
  prepareJointDependencies: vi.fn(),
  skipDependencyPreparation: vi.fn(),
  setProjectWorkdir: vi.fn(),
}))

import {
  cancelDependencyPlan,
  cancelProbe,
  createDependencyPlan,
  createJointDependencyPlan,
  DEPENDENCY_PREPARATION_CODE,
  fetchRegistry,
  installDependencyPlan,
  prepareJointDependencies,
  probeScript,
  setEngineEnvironment,
  setProjectEnvironment,
  setProjectWorkdir,
  skipDependencyPreparation,
  WORKDIR_CONFIRMATION_CODE,
  type DependencyPreparationOffer,
  type WorkdirConfirmation,
  type CapturedFigureDescriptor,
  type DependencyRepairOffer,
  type ProbeResult,
  type RegistryView,
  type ScriptInventoryEntry,
} from '@/lib/api'
import { i18n } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { DependencyPrepareDialog } from '@/components/DependencyPrepareDialog'
import { EngineEnvironmentDialog } from '@/components/EngineEnvironmentDialog'
import { WorkdirConfirmDialog } from '@/components/WorkdirConfirmDialog'
import { ScriptLibrary } from '@/components/left/ScriptLibrary'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useEnvStore } from '@/store/envStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'
import { visibleBlocks } from '@/test/visibleBlocks'

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
    // 运行 / 取消是 IconButton（自带气泡），与真实的 App 根一样要套 TooltipProvider
    root.render(
      <TooltipProvider>
        <ScriptLibrary query={query} />
        {/* 与 App 根一样挂着：「选择渲染环境」就地打开的就是它 */}
        <EngineEnvironmentDialog />
        {/* 同上：起会话之前的两道门弹的就是它们 */}
        <DependencyPrepareDialog />
        <WorkdirConfirmDialog />
      </TooltipProvider>,
    )
  })
  await flush()
}

// 按可见文字**或**可达名找：运行 / 取消是图标钮，名字在 aria-label 里
const buttonByText = (text: string): HTMLButtonElement => {
  const btn = [...host.querySelectorAll('button')].find(
    (b) => (b.textContent ?? '').includes(text) || (b.getAttribute('aria-label') ?? '').includes(text),
  )
  if (!btn) throw new Error(`没有找到按钮: ${text}`)
  return btn as HTMLButtonElement
}

/** 运行钮是图标钮：可达名是「运行 <脚本> 并发现图」，按后缀找（每个用例只有一个脚本） */
const runButton = (): HTMLButtonElement => {
  const btn = host.querySelector<HTMLButtonElement>('button[aria-label$="并发现图"]')
  if (!btn) throw new Error('没有找到运行钮')
  return btn
}

beforeEach(() => {
  // 模块级的停放槽活得比 zustand reset 长：每条用例从空的开始（互不串）
  __resetDepRepairParkingForTests()
  localStorage.clear()
  useScriptLibraryStore.getState().clear()
  useScriptRunStore.getState().clear()
  useUiStore.setState({ engineEnvOpen: false, settingsOpen: false, settingsSection: null, dialogStack: [] })
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
    expect(text).toContain('脚本尚未运行')
    expect(text).toContain('输出名称只能在运行后确定')
    expect(text).toContain('已关联 2 张图')
  })

  it('没有常驻的安全导入说明（2026-09-15 打磨批次 G：说明与术语提示不常驻）', async () => {
    mockRegistry.mockResolvedValue(view([entry({})]))
    await mount()
    expect(host.textContent).not.toContain('安全导入会隔离脚本写入')
    expect(Array.from(host.querySelectorAll('button')).find((b) => b.textContent === '知道了')).toBeUndefined()
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

    const btn = runButton()
    await act(async () => {
      btn.focus()
      btn.click()
    })
    expect(mockProbe).toHaveBeenCalledWith('show.py')
    expect(host.textContent).toContain('正在启动渲染环境')
    // busy 态同一个按钮翻转成「取消」——focus 不搬家
    expect(btn.getAttribute('aria-label')).toContain('取消')
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
    await act(async () => runButton().click())
    expect(host.textContent).toContain('正在启动渲染环境')
    await act(async () => useScriptRunStore.getState().markRunning('show.py'))
    expect(host.textContent).toContain('正在运行脚本')
  })

  it('多 Figure：结果弹层列出每一张，全部可添加（反证 #4：只显示第一张这里红）', async () => {
    mockRegistry.mockResolvedValue(view([entry({ script: 'show.py' })]))
    mockProbe.mockResolvedValue(ok([desc('a'), desc('b'), desc('c')], 1))
    await mount()
    await act(async () => runButton().click())
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
    await act(async () => runButton().click())
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

  it('「选择渲染环境」就地打开渲染环境对话框（卡片在里面），不跳设置页', async () => {
    // 环境状态先备好：卡片挂上就有东西可画，不去真的请求 /api/engine
    useEnvStore.setState({
      env: {
        ok: true,
        python: '/usr/bin/python3',
        source: 'system',
        matplotlib: '3.10.8',
        managed: false,
        bundled: false,
        state: 'idle',
      } as never,
    })
    mockRegistry.mockResolvedValue(view([entry({ script: 'show.py' })]))
    mockProbe.mockResolvedValue({
      ...ok([]),
      registered: false,
      error: { code: 'missing_dependency', message: '缺少依赖包：pandas', params: { module: 'pandas' } },
    })
    await mount()
    await act(async () => runButton().click())
    await flush()
    await act(async () => buttonByText('选择渲染环境').click())
    await flush()
    const ui = useUiStore.getState()
    expect(ui.engineEnvOpen).toBe(true)
    // 旧缺陷：深链到设置的「关于与隐私」页，那里没有任何渲染环境内容
    expect(ui.settingsOpen).toBe(false)
    const dialog = document.querySelector('[data-dialog="engine-environment"]')
    expect(dialog).not.toBeNull()
    // 对话框的正文就是那一份渲染环境（一组行）；解释器那一行的「更换…」就是换 Python 环境的出口
    expect(dialog!.querySelector('[data-engine-env-card]')).not.toBeNull()
    expect(dialog!.querySelector('[data-engine-interpreter] button')?.textContent).toBe('更换…')
    expect(dialog!.textContent).toContain('/usr/bin/python3')
  })

  it('多个脚本同样失败：每一行默认只多一个「详情」折叠标题，解释与出口都收在里面', async () => {
    mockRegistry.mockResolvedValue(view([entry({ script: 'a.py' }), entry({ script: 'b.py' }), entry({ script: 'c.py' })]))
    mockProbe.mockImplementation(async (script: string) => ({
      ...ok([]),
      script,
      registered: false,
      error: { code: 'missing_dependency', message: '缺少依赖包：pandas', params: { module: 'pandas' } },
    }))
    await mount()
    for (const btn of [...host.querySelectorAll<HTMLButtonElement>('button[aria-label$="并发现图"]')]) {
      await act(async () => btn.click())
      await flush()
    }
    const recoveries = [...host.querySelectorAll('[data-script-recovery]')]
    expect(recoveries).toHaveLength(3)
    for (const r of recoveries) {
      expect(visibleBlocks(r)).toEqual([{ tag: 'summary', text: '详情' }])
    }
    // 整个列表里默认看得到的文字没有那段解释
    expect(visibleBlocks(host).some((b) => b.text.includes('可能依赖原来的 Python 环境'))).toBe(false)
  })

  it('同一个包缺在几个脚本上：只挂一张修复卡；装好后同样缺它的几行一起重跑', async () => {
    const offerFor = (script: string): DependencyRepairOffer => ({
      import_name: 'openpyxl',
      script,
      requirement: {
        import_name: 'openpyxl', distribution: 'openpyxl', specifier: '', requirement: 'openpyxl',
        resolution_source: 'curated', confidence: 'high', installable: true,
      },
      targets: [{
        kind: 'tavotto_managed', venv: '', python: '', modifies_user_environment: false,
        creates_environment: true, available: true, reason: '',
      }],
      rounds_remaining: 3,
      python_supported: { min: '3.10', max: '3.14' },
    })
    useDepRepairStore.getState().reset()
    mockRegistry.mockResolvedValue(view([entry({ script: 'a.py' }), entry({ script: 'b.py' })]))
    mockProbe.mockImplementation(async (script: string) => ({
      ...ok([]),
      script,
      registered: false,
      error: {
        code: 'missing_dependency', message: '缺少依赖包：openpyxl', params: { module: 'openpyxl' },
        dependency_repair: offerFor(script),
      },
    }))
    vi.mocked(createDependencyPlan).mockResolvedValue({
      plan: {
        plan_id: 'plan-2', target_kind: 'tavotto_managed', python: '', creates_environment: true,
        modifies_user_environment: false, network_required: true, expires_at: 0,
        ...offerFor('a.py').requirement!,
      },
    })
    vi.mocked(installDependencyPlan).mockResolvedValue({ started: true } as never)
    await mount()
    for (const btn of [...host.querySelectorAll<HTMLButtonElement>('button[aria-label$="并发现图"]')]) {
      await act(async () => btn.click())
      await flush()
    }
    expect(host.querySelectorAll('[data-script-dependency-repair]')).toHaveLength(1)
    // 另一行也不再叠恢复入口：修复卡（在第一行上）就是它的下一步
    expect(host.querySelectorAll('[data-script-recovery]')).toHaveLength(0)
    await act(async () => buttonByText('一键修复').click())
    await flush()
    mockProbe.mockClear()
    mockProbe.mockImplementation(async (script: string) => ({ ...ok([desc('F')]), script }))
    await act(async () => {
      useDepRepairStore.getState().onProgress({
        plan_id: 'plan-2', state: 'done', log: '', error: null, code: '',
        script: 'a.py', import_name: 'openpyxl', distribution: 'openpyxl',
      } as never)
    })
    await flush()
    expect(mockProbe.mock.calls.map((c) => c[0]).sort()).toEqual(['a.py', 'b.py'])
    useDepRepairStore.getState().reset()
  })

  it('缺包的脚本单独归「需要修复」组（排最前）；超时与一般失败仍在「可能需要原环境」', async () => {
    mockRegistry.mockResolvedValue(view([entry({ script: 'a.py' }), entry({ script: 'b.py' }), entry({ script: 'c.py' })]))
    mockProbe.mockImplementation(async (script: string) => ({
      ...ok([]),
      script,
      registered: false,
      error:
        script === 'a.py'
          ? { code: 'missing_dependency', message: '缺少依赖包：pandas', params: { module: 'pandas' } }
          : script === 'b.py'
            ? { code: 'execution_timeout', message: '超时' }
            : { code: 'script_failed', message: '脚本出错' },
    }))
    await mount()
    for (const btn of [...host.querySelectorAll<HTMLButtonElement>('button[aria-label$="并发现图"]')]) {
      await act(async () => btn.click())
      await flush()
    }
    const groups = [...host.querySelectorAll('section ul[aria-label]')].map((ul) => ({
      name: ul.getAttribute('aria-label'),
      scripts: [...ul.querySelectorAll('li > div span.font-mono')].map((s) => s.getAttribute('title')),
    }))
    expect(groups[0]).toEqual({ name: '需要修复', scripts: ['a.py'] })
    expect(groups.find((g) => g.name === '可能需要原环境')?.scripts).toEqual(['b.py', 'c.py'])
  })

  /** 两行脚本都缺 openpyxl，后端给同一种 offer（`over` 改它）：跑一遍，返回卡片 */
  async function twoRowsMissing(over: Partial<DependencyRepairOffer>) {
    const base: DependencyRepairOffer = {
      import_name: 'openpyxl',
      script: 'a.py',
      requirement: {
        import_name: 'openpyxl', distribution: 'openpyxl', specifier: '', requirement: 'openpyxl',
        resolution_source: 'curated', confidence: 'high', installable: true,
      },
      targets: [],
      rounds_remaining: 3,
      python_supported: { min: '3.10', max: '3.14' },
      ...over,
    }
    useDepRepairStore.getState().reset()
    mockRegistry.mockResolvedValue(view([entry({ script: 'a.py' }), entry({ script: 'b.py' })]))
    mockProbe.mockImplementation(async (script: string) => ({
      ...ok([]),
      script,
      registered: false,
      error: {
        code: 'missing_dependency', message: '缺少依赖包：openpyxl', params: { module: 'openpyxl' },
        dependency_repair: { ...base, script },
      },
    }))
    await mount()
    for (const btn of [...host.querySelectorAll<HTMLButtonElement>('button[aria-label$="并发现图"]')]) {
      await act(async () => btn.click())
      await flush()
    }
    expect(host.querySelectorAll('[data-script-dependency-repair]')).toHaveLength(1)
    mockProbe.mockReset()
    mockProbe.mockImplementation(async (script: string) => ({ ...ok([desc('F')]), script }))
  }

  it('一键修复改用了电脑上已有的环境：那一行与同样缺这个包的行立刻重跑、卡片收起（Codex #742）', async () => {
    await twoRowsMissing({
      targets: [{
        kind: 'system_interpreter', venv: '', python: '/usr/local/bin/python3', modifies_user_environment: false,
        creates_environment: false, available: true, reason: '', python_version: '3.12.4', support: 'verified',
      }],
    })
    vi.mocked(setProjectEnvironment).mockResolvedValue({ ok: true, project: { open: true } } as never)
    await act(async () => buttonByText('一键修复').click())
    await flush()
    expect(setProjectEnvironment).toHaveBeenCalledWith('/usr/local/bin/python3', 'openpyxl')
    expect(mockProbe.mock.calls.map((c) => c[0]).sort()).toEqual(['a.py', 'b.py'])
    expect(host.querySelector('[data-script-dependency-repair]')).toBeNull()
  })

  it('改用失败：不重跑，卡片留着并说出原因', async () => {
    await twoRowsMissing({
      targets: [{
        kind: 'system_interpreter', venv: '', python: '/usr/local/bin/python3', modifies_user_environment: false,
        creates_environment: false, available: true, reason: '', python_version: '3.12.4', support: 'verified',
      }],
    })
    vi.mocked(setProjectEnvironment).mockRejectedValue(new Error('这个环境里也没有 openpyxl'))
    await act(async () => buttonByText('一键修复').click())
    await flush()
    expect(mockProbe).not.toHaveBeenCalled()
    expect(host.querySelector('[data-script-dependency-repair]')!.textContent).toContain('这个环境里也没有 openpyxl')
  })

  it('全局固定清掉之后：停在缺包上的行重跑，不再停在「恢复自动检测」（Codex #742 同一类）', async () => {
    await twoRowsMissing({ code: 'dependency_interpreter_pinned', pinned: { python: '/opt/venv/bin/python', source: 'configured' } })
    vi.mocked(setEngineEnvironment).mockResolvedValue({ ok: true } as never)
    await act(async () => buttonByText('恢复自动检测').click())
    await flush()
    expect(setEngineEnvironment).toHaveBeenCalledWith(null)
    expect(mockProbe.mock.calls.map((c) => c[0]).sort()).toEqual(['a.py', 'b.py'])
    expect(host.querySelector('[data-dependency-repair-pinned]')).toBeNull()
  })

  it('没出图（script_no_figure）不进「可能需要原环境」组', async () => {
    mockRegistry.mockResolvedValue(view([entry({ script: 'show.py' })]))
    mockProbe.mockResolvedValue({
      ...ok([]),
      registered: false,
      error: { code: 'script_no_figure', message: '没有捕获到任何 Figure', params: { entry: '__main__' } },
    })
    await mount()
    await act(async () => runButton().click())
    await flush()
    expect(host.textContent).not.toContain('可能需要原环境')
    expect(host.textContent).toContain('没有捕获到任何 Figure')
  })

  it('缺包且后端给了修复 offer：脚本行上就有「安装到 Tavotto 环境」，点一次安装，装好后自动重跑', async () => {
    // 2026-09-28 实测：新脚本的图还没上画布，右栏修复卡片不出现，脚本行上只有「选择渲染环境」
    // 「复制诊断」——新用户走不到安装。offer 是后端试运行失败时挂上的同一份 `deprepair.offer()`
    const privatePython = {
      id: 'pbs', version: '3.13.15', target: 'windows-x86_64', source_host: 'github.com',
      download_bytes: 47131996, required: true, cached: false, network_required: true,
    }
    const offer: DependencyRepairOffer = {
      import_name: 'adjustText',
      script: 'fig_labels.py',
      requirement: {
        import_name: 'adjustText', distribution: 'adjustText', specifier: '', requirement: 'adjustText',
        resolution_source: 'curated', confidence: 'high', installable: true,
      },
      targets: [{
        kind: 'tavotto_managed', venv: '', python: '', modifies_user_environment: false,
        creates_environment: true, available: true, reason: '', private_python: privatePython,
      }],
      rounds_remaining: 3,
      python_supported: { min: '3.10', max: '3.14' },
    }
    useDepRepairStore.getState().reset()
    mockRegistry.mockResolvedValue(view([entry({ script: 'fig_labels.py' })]))
    mockProbe.mockResolvedValue({
      ...ok([]),
      script: 'fig_labels.py',
      registered: false,
      error: {
        code: 'missing_dependency',
        message: '缺少依赖包：adjustText（当前渲染环境里没有它）',
        params: { module: 'adjustText' },
        dependency_repair: offer,
      },
    })
    vi.mocked(createDependencyPlan).mockResolvedValue({
      plan: {
        plan_id: 'plan-row', target_kind: 'tavotto_managed', python: '', creates_environment: true,
        modifies_user_environment: false, network_required: true, expires_at: 0,
        private_python: privatePython, ...offer.requirement!,
      },
    })
    vi.mocked(installDependencyPlan).mockResolvedValue({ started: true } as never)
    await mount()
    await act(async () => runButton().click())
    await flush()
    expect(host.querySelector('[data-script-dependency-repair]'), '脚本行上没有修复卡片').toBeTruthy()
    expect(host.querySelector('[data-dependency-disclosure]')).toBeTruthy()
    // 卡片就是这一行的下一步：「选择渲染环境」收进卡片的「高级」，恢复说明那一段不再叠在卡片下面（2026-09-29）
    expect(buttonByText('选择渲染环境').closest('[data-repair-advanced]')).toBeTruthy()
    expect([...host.querySelectorAll('button')].some((b) => b.textContent?.includes('复制诊断'))).toBe(false)
    await act(async () => buttonByText('一键修复').click())
    await flush()
    expect(createDependencyPlan).toHaveBeenCalledWith({
      module: 'adjustText', script: 'fig_labels.py', target: 'tavotto_managed',
    })
    expect(installDependencyPlan).toHaveBeenCalledWith('plan-row')
    // 装好：后端的进度带着计划所属的脚本 → 这一行自动重跑
    mockProbe.mockClear()
    mockProbe.mockResolvedValue({ ...ok([desc('Fig1')]), script: 'fig_labels.py' })
    await act(async () => {
      useDepRepairStore.getState().onProgress({
        plan_id: 'plan-row', state: 'done', log: '', error: null, code: '',
        script: 'fig_labels.py', distribution: 'adjustText',
      } as never)
    })
    await flush()
    expect(mockProbe).toHaveBeenCalledTimes(1)
    expect(mockProbe.mock.calls[0][0]).toBe('fig_labels.py')
    expect(host.querySelector('[data-script-dependency-repair]')).toBeNull()
    useDepRepairStore.getState().reset()
  })
})

/**
 * #729：从脚本行发起的修复，A → B → A 之后仍挂在那一行上。
 *
 * 脚本行的卡片以前只读 `scriptRunStore` 里那次运行的 `missing_dependency` offer，而换项目时
 * `resetForNewProject()` 刻意清空 `scriptRunStore`——作业进度与重试上下文由 `depRepairStore.clear()` 放回来了，
 * 那一行却因为没有 offer 不渲染卡片：进度、取消、重试都够不着。修法是发起时把 offer 随作业一起收放
 * （`depRepairStore.scriptOffer`）。这里按 `resetForNewProject()` 里的同两步（`scriptRunStore.clear()` →
 * `depRepairStore.clear()`，此前 `setCurrentProjectId` 已经换成新项目）模拟切项目，三种终局各一条。
 */
describe('脚本行发起的修复切项目再切回（#729）', () => {
  const privatePython = {
    id: 'pbs', version: '3.13.15', target: 'windows-x86_64', source_host: 'github.com',
    download_bytes: 47131996, required: true, cached: false, network_required: true,
  }
  const offer: DependencyRepairOffer = {
    import_name: 'adjustText',
    script: 'fig_labels.py',
    requirement: {
      import_name: 'adjustText', distribution: 'adjustText', specifier: '', requirement: 'adjustText',
      resolution_source: 'curated', confidence: 'high', installable: true,
    },
    targets: [{
      kind: 'tavotto_managed', venv: '', python: '', modifies_user_environment: false,
      creates_environment: true, available: true, reason: '', private_python: privatePython,
    }],
    rounds_remaining: 3,
    python_supported: { min: '3.10', max: '3.14' },
  }
  const card = () => host.querySelector('[data-script-dependency-repair]')
  const progress = (state: string, over: Record<string, unknown> = {}) =>
    ({
      plan_id: 'plan-row', state, log: '', error: null, code: '', target_kind: 'tavotto_managed',
      script: 'fig_labels.py', distribution: 'adjustText', ...over,
    }) as never
  /** 与 `resetForNewProject()` 同样的两步（顺序也相同） */
  const switchTo = async (id: string) => {
    await act(async () => {
      setCurrentProjectId(id)
      useScriptRunStore.getState().clear()
      useDepRepairStore.getState().clear()
    })
    await flush()
  }

  beforeEach(() => {
    setCurrentProjectId('pA')
    useDepRepairStore.setState({ parked: {} })
    useDepRepairStore.getState().reset()
    vi.mocked(createDependencyPlan).mockReset()
    vi.mocked(installDependencyPlan).mockReset()
    vi.mocked(cancelDependencyPlan).mockClear()
    mockRegistry.mockResolvedValue(view([entry({ script: 'fig_labels.py' })]))
    mockProbe.mockResolvedValue({
      ...ok([]),
      script: 'fig_labels.py',
      registered: false,
      error: {
        code: 'missing_dependency',
        message: '缺少依赖包：adjustText（当前渲染环境里没有它）',
        params: { module: 'adjustText' },
        dependency_repair: offer,
      },
    })
    vi.mocked(createDependencyPlan).mockResolvedValue({
      plan: {
        plan_id: 'plan-row', target_kind: 'tavotto_managed', python: '', creates_environment: true,
        modifies_user_environment: false, network_required: true, expires_at: 0,
        private_python: privatePython, ...offer.requirement!,
      },
    })
    vi.mocked(installDependencyPlan).mockResolvedValue({ started: true } as never)
  })
  afterEach(() => {
    useDepRepairStore.setState({ parked: {} })
    useDepRepairStore.getState().reset()
    setCurrentProjectId(null)
  })

  /** A 上：脚本行跑出缺包 → 在那一行点一次安装 → SSE 推到 installing，然后切到 B */
  async function installFromRowThenLeave() {
    await mount()
    await act(async () => runButton().click())
    await flush()
    await act(async () => buttonByText('一键修复').click())
    await flush()
    expect(installDependencyPlan).toHaveBeenCalledWith('plan-row')
    await act(async () => useDepRepairStore.getState().onProgress(progress('installing')))
    expect(buttonByText('取消'), 'A 上安装中应有「取消」').toBeTruthy()
    await switchTo('pB')
    // B 上同名的脚本行不显示 A 的修复（scriptRunStore 已清空，offer 也不属于 B）
    expect(card(), 'B 上不该显示 A 的修复卡片').toBeNull()
    expect(host.textContent).not.toContain('正在安装')
  }

  it('运行中：切回 A，脚本行上仍是进度，「取消」可用', async () => {
    await installFromRowThenLeave()
    await switchTo('pA')
    expect(useScriptRunStore.getState().byScript['fig_labels.py'], '前提：scriptRunStore 确实被清空').toBeUndefined()
    expect(card(), '切回 A 后脚本行上看不到修复进度').toBeTruthy()
    expect(card()!.textContent).toContain('正在安装')
    await act(async () => buttonByText('取消').click())
    expect(cancelDependencyPlan).toHaveBeenCalledWith('plan-row')
  })

  it('失败：切走期间失败，切回 A，脚本行上有失败结局与「重试」，重试按原授权再装一次', async () => {
    await installFromRowThenLeave()
    await act(async () =>
      useDepRepairStore.getState().onProgress(
        progress('failed', { code: 'dependency_network_unavailable', error: '断网' }),
      ),
    )
    expect(card(), '失败的结局不该落到 B 的脚本行上').toBeNull()
    await switchTo('pA')
    expect(card(), '切回 A 后脚本行上看不到失败结局').toBeTruthy()
    expect(card()!.querySelector('[data-repair-failure]')!.textContent).toBe('下载没成功，检查网络后点重试。')
    const retry = card()!.querySelector<HTMLButtonElement>('[data-dependency-repair-retry]')
    expect(retry, '失败结局上没有「重试」').toBeTruthy()
    vi.mocked(createDependencyPlan).mockClear()
    vi.mocked(installDependencyPlan).mockClear()
    await act(async () => retry!.click())
    await flush()
    expect(createDependencyPlan).toHaveBeenCalledWith({
      module: 'adjustText', script: 'fig_labels.py', target: 'tavotto_managed',
    })
    expect(installDependencyPlan).toHaveBeenCalledWith('plan-row')
    expect(card(), '重试之后卡片仍在脚本行上').toBeTruthy()
  })

  it('成功：切走期间装好了，切回 A 自动重跑那一行一次（与没切走时同一条路），B 上不触发、再切回不重复', async () => {
    await installFromRowThenLeave()
    mockProbe.mockClear()
    mockProbe.mockResolvedValue({ ...ok([desc('Fig1')]), script: 'fig_labels.py' })
    await act(async () => useDepRepairStore.getState().onProgress(progress('done')))
    await flush()
    expect(mockProbe, 'B 上不该重跑 A 的脚本').not.toHaveBeenCalled()
    await switchTo('pA')
    expect(mockProbe, '切回 A 后没有自动重跑').toHaveBeenCalledTimes(1)
    expect(mockProbe.mock.calls[0][0]).toBe('fig_labels.py')
    expect(card(), '重跑后卡片应随之收起').toBeNull()
    // 重跑出了图：这一行回到「发现了图」
    expect(host.textContent).toContain('已发现 1 张图')
    // 再切走、切回：不重复触发
    await switchTo('pB')
    await switchTo('pA')
    expect(mockProbe).toHaveBeenCalledTimes(1)
  })

  /** A 上两个脚本缺同一个包：只挂一张卡（fig_labels.py 那行），在它上面装、然后切到 B */
  async function installForTwoThenLeave() {
    mockRegistry.mockResolvedValue(view([entry({ script: 'fig_labels.py' }), entry({ script: 'other.py' })]))
    mockProbe.mockImplementation(async (script: string) => ({
      ...ok([]),
      script,
      registered: false,
      error: {
        code: 'missing_dependency',
        message: '缺少依赖包：adjustText',
        params: { module: 'adjustText' },
        dependency_repair: { ...offer, script },
      },
    }))
    await mount()
    for (const btn of [...host.querySelectorAll<HTMLButtonElement>('button[aria-label$="并发现图"]')]) {
      await act(async () => btn.click())
      await flush()
    }
    expect(host.querySelectorAll('[data-script-dependency-repair]')).toHaveLength(1)
    await act(async () => buttonByText('一键修复').click())
    await flush()
    await act(async () => useDepRepairStore.getState().onProgress(progress('installing')))
    await switchTo('pB')
    mockProbe.mockReset()
    mockProbe.mockImplementation(async (script: string) => ({ ...ok([desc('Fig1')]), script }))
  }

  it('同一个包缺在两行上：切走期间装好，切回 A 两行都重跑（Codex #742：运行记录已随切项目清空）', async () => {
    await installForTwoThenLeave()
    await act(async () => useDepRepairStore.getState().onProgress(progress('done', { import_name: 'adjustText' })))
    await flush()
    expect(mockProbe, 'B 上不该重跑 A 的脚本').not.toHaveBeenCalled()
    await switchTo('pA')
    expect(mockProbe.mock.calls.map((c) => c[0]).sort()).toEqual(['fig_labels.py', 'other.py'])
    // 再切走切回不重复
    await switchTo('pB')
    await switchTo('pA')
    expect(mockProbe).toHaveBeenCalledTimes(2)
  })

  it('同一个包缺在两行上：切回 A 时还在装，之后在 A 上装好——两行都重跑', async () => {
    await installForTwoThenLeave()
    await switchTo('pA')
    await act(async () => useDepRepairStore.getState().onProgress(progress('done', { import_name: 'adjustText' })))
    await flush()
    expect(mockProbe.mock.calls.map((c) => c[0]).sort()).toEqual(['fig_labels.py', 'other.py'])
  })

  it('成功：切回 A 时还在装，之后在 A 上装好——同样自动重跑那一行一次', async () => {
    await installFromRowThenLeave()
    await switchTo('pA')
    mockProbe.mockClear()
    mockProbe.mockResolvedValue({ ...ok([desc('Fig1')]), script: 'fig_labels.py' })
    await act(async () => useDepRepairStore.getState().onProgress(progress('done')))
    await flush()
    expect(mockProbe, '切回后装好没有自动重跑').toHaveBeenCalledTimes(1)
    expect(card()).toBeNull()
  })

  it('取消：切走期间取消了，切回 A，脚本行上有取消结局与「重试」', async () => {
    await installFromRowThenLeave()
    await act(async () =>
      useDepRepairStore.getState().onProgress(progress('cancelled', { code: 'dependency_install_cancelled' })),
    )
    await switchTo('pA')
    expect(card(), '切回 A 后脚本行上看不到取消结局').toBeTruthy()
    expect(card()!.textContent).toContain('安装已取消')
    const retry = card()!.querySelector<HTMLButtonElement>('[data-dependency-repair-retry]')
    expect(retry, '取消结局上没有「重试」').toBeTruthy()
    vi.mocked(createDependencyPlan).mockClear()
    await act(async () => retry!.click())
    await flush()
    expect(createDependencyPlan).toHaveBeenCalledTimes(1)
    // 「知道了」收起：offer 随之放掉，这一行回到没有卡片（scriptRunStore 里也没有那次运行了）
    await act(async () => useDepRepairStore.getState().reset())
    expect(card()).toBeNull()
  })
})

/**
 * 起会话之前的门（Windows 真机验收，main 493a1310）：项目里只有一个 `from adjustText import adjust_text` 的脚本、
 * 还没有任何图。试运行 200 回来、`error.code == dependency_preparation_required` 带整份载荷——以前前端把它当成
 * 一次失败塞进「可能需要原环境」，出口只有「选择渲染环境 / 复制诊断」，新用户走不到安装。
 * 判据的主语：素材库脚本行（`scriptRunStore.run`）拿到这份载荷后，① 授权框（同一个 `DependencyPrepareDialog`）
 * 弹出；② 这一行不进「可能需要原环境」；③ 授权并准备成功后自动再试运行一次、出图；④ 「稍后」之后行上有再打开的
 * 入口、「不准备，直接运行」之后同样重跑；⑤ 运行目录那道门同形。
 */
describe('试运行撞上起会话之前的门', () => {
  const prepOffer: DependencyPreparationOffer = {
    code: DEPENDENCY_PREPARATION_CODE,
    script: 'fig_labels.py',
    plan: {
      plan_version: 1, status: 'ready', target_kind: 'tavotto_managed', script: 'fig_labels.py', needed: [],
      missing: [{ import_name: 'adjustText', distribution: 'adjusttext', resolution_source: 'curated', declared: false, specifiers: [], via: [] }],
      satisfied: [], unknown: [], possible: [], requirements: ['adjusttext'], constraints: [], require_hashes: false,
      adapter: [], blocked: [],
      selection: { selected_groups: [], available_groups: [], unselected_groups: [], skipped_marker: [] },
      identity: 'id',
    },
    target_kind: 'tavotto_managed',
    targets: [
      { kind: 'tavotto_managed', venv: '', python: '', modifies_user_environment: false, creates_environment: true, available: true, reason: '' },
    ],
    rounds_remaining: 3,
    skipped: false,
  }
  const gateProbe = (): ProbeResult => ({
    ...ok([]),
    script: 'fig_labels.py',
    registered: false,
    error: {
      code: DEPENDENCY_PREPARATION_CODE,
      message: '脚本开跑就需要的包渲染环境里没有，要先准备依赖',
      dependency_preparation: prepOffer,
    },
  })
  const prepDialog = () => document.querySelector('[data-dialog="dependency-prepare"]')
  const docButton = (label: string) =>
    [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === label) as HTMLButtonElement | undefined

  beforeEach(() => {
    setCurrentProjectId('p1')
    useEnvStore.setState({ dependencyPreparation: null, workdirConfirmation: null })
    useDepRepairStore.getState().reset()
    vi.mocked(createJointDependencyPlan).mockReset()
    vi.mocked(prepareJointDependencies).mockReset()
    vi.mocked(skipDependencyPreparation).mockReset()
    vi.mocked(setProjectWorkdir).mockReset()
    mockRegistry.mockResolvedValue(view([entry({ script: 'fig_labels.py' })]))
  })
  afterEach(() => {
    useEnvStore.setState({ dependencyPreparation: null, workdirConfirmation: null })
    useDepRepairStore.getState().reset()
    document.body.innerHTML = ''
    setCurrentProjectId(null)
  })

  it('依赖门（脚本行 ▶）：直接弹一键修复框、不进「可能需要原环境」；点一次走联合准备，装好后自动再试运行、出图', async () => {
    mockProbe.mockResolvedValueOnce(gateProbe())
    vi.mocked(createJointDependencyPlan).mockResolvedValue({
      plan: {
        plan_id: 'jp-row', script: 'fig_labels.py', target_kind: 'tavotto_managed', python: '', requirements: ['adjusttext'],
        constraints: [], require_hashes: false, adapter: [], identity: 'id', needed_imports: ['adjustText'], groups: [],
        modifies_user_environment: false, creates_environment: true, network_required: true, expires_at: 0, joint: prepOffer.plan,
      },
    } as never)
    vi.mocked(prepareJointDependencies).mockResolvedValue({ started: true } as never)
    await mount()
    await act(async () => runButton().click())
    await flush()
    // ① 不用找素材库的修复按钮：缺依赖的响应直接弹框。
    expect(prepDialog(), '缺依赖后没有弹授权框').toBeTruthy()
    expect(useEnvStore.getState().dependencyPreparation).toEqual(prepOffer)
    expect(prepDialog()!.textContent).toContain('这个脚本还缺 adjusttext，点一下自动装好。')
    expect(host.querySelector('[data-script-preparation-sentence]')).toBeNull()
    // ② 不是失败：不进「可能需要原环境」，没有那两颗无关的出口
    expect(host.textContent).not.toContain('可能需要原环境')
    expect([...host.querySelectorAll('[data-script-preparation] button')].filter((b) => !b.closest('details')).map((b) => b.textContent)).toEqual(['一键修复'])
    // ③ 点一次：先绑定计划再只发 plan_id
    await act(async () => prepDialog()!.querySelector<HTMLButtonElement>('[data-dependency-prepare-start]')!.click())
    await flush()
    expect(createJointDependencyPlan).toHaveBeenCalledWith({ script: 'fig_labels.py', target: 'tavotto_managed' })
    expect(prepareJointDependencies).toHaveBeenCalledWith('jp-row')
    // ④ 准备成功（SSE 带着计划所属的脚本）→ #740 的 `rerunGated`：这一行自动再试运行一次、出图
    mockProbe.mockClear()
    mockProbe.mockResolvedValue({ ...ok([desc('Fig1')]), script: 'fig_labels.py' })
    await act(async () =>
      useDepRepairStore.getState().onProgress({
        plan_id: 'jp-row', state: 'done', log: '', error: null, code: '', flow: 'joint', script: 'fig_labels.py',
      }),
    )
    await flush()
    expect(mockProbe, '准备成功后没有自动重跑试运行').toHaveBeenCalledTimes(1)
    expect(mockProbe.mock.calls[0][0]).toBe('fig_labels.py')
    expect(useScriptRunStore.getState().byScript['fig_labels.py']?.phase).toBe('captured_one')
  })

  it('其他入口撞上依赖门同样弹授权框；「不准备，直接运行」之后重跑', async () => {
    mockProbe.mockResolvedValueOnce(gateProbe())
    vi.mocked(skipDependencyPreparation).mockResolvedValue({ skipped: true } as never)
    await mount()
    await act(async () => void useScriptRunStore.getState().run('fig_labels.py')) // 不是脚本行 ▶
    await flush()
    expect(useEnvStore.getState().dependencyPreparation, '载荷没交给 envStore').toEqual(prepOffer)
    expect(prepDialog(), '授权框没弹').toBeTruthy()
    mockProbe.mockClear()
    mockProbe.mockResolvedValue({ ...ok([desc('Fig1')]), script: 'fig_labels.py' })
    await act(async () => docButton('不准备，直接运行')!.click())
    await flush()
    expect(skipDependencyPreparation).toHaveBeenCalledWith('fig_labels.py')
    expect(mockProbe, '明确跳过之后没有重跑试运行').toHaveBeenCalledTimes(1)
  })

  it('运行目录门同形：载荷交给 envStore、行上有「选择运行目录…」、选定后重跑', async () => {
    const confirmation: WorkdirConfirmation = {
      kind: 'workdir', code: WORKDIR_CONFIRMATION_CODE, script: 'fig_labels.py', reason: 'script_dir_evidence',
      recommended: 'project', options: [], conflicts: [], reads: [],
    }
    mockProbe.mockResolvedValueOnce({
      ...ok([]),
      script: 'fig_labels.py',
      registered: false,
      error: { code: WORKDIR_CONFIRMATION_CODE, message: '要先选择脚本的运行目录', confirmation },
    })
    vi.mocked(setProjectWorkdir).mockResolvedValue({ project: { open: true } } as never)
    await mount()
    await act(async () => runButton().click())
    await flush()
    expect(useEnvStore.getState().workdirConfirmation, '载荷没交给 envStore').toEqual(confirmation)
    expect(host.textContent).not.toContain('可能需要原环境')
    const reopenWorkdir = () => host.querySelector('[data-script-workdir-choose] button') as HTMLButtonElement | null
    // 「稍后」之后行上有再打开的入口
    await act(async () => docButton('稍后')!.click())
    await flush()
    expect(useEnvStore.getState().workdirConfirmation).toBeNull()
    expect(reopenWorkdir(), '行上没有再打开的入口').toBeTruthy()
    await act(async () => reopenWorkdir()!.click())
    await flush()
    expect(useEnvStore.getState().workdirConfirmation, '再打开没有把载荷交回 envStore').toEqual(confirmation)
    mockProbe.mockClear()
    mockProbe.mockResolvedValue({ ...ok([desc('Fig1')]), script: 'fig_labels.py' })
    // 在确认框里选定（推荐项已预选）→ 这一行自动再试运行
    await act(async () => docButton('运行')!.click())
    await flush()
    expect(setProjectWorkdir).toHaveBeenCalledWith('project')
    expect(mockProbe, '选定运行目录后没有重跑试运行').toHaveBeenCalledTimes(1)
    expect(useScriptRunStore.getState().byScript['fig_labels.py']?.phase).toBe('captured_one')
  })
})

describe('行与状态的写法（2026-10-07 设计审计 §10.3）', () => {
  it('组头是 28px 的 type-section + type-meta 计数', async () => {
    mockRegistry.mockResolvedValue(view([entry({ script: 'show.py', reason: 'no_static_output' })]))
    await mount()
    const head = host.querySelector<HTMLElement>('[data-script-group]')!
    expect(head.className).toContain('h-7')
    expect(head.querySelector('.type-section')).toBeTruthy()
    expect(head.querySelector('.type-meta')?.textContent).toBe('1')
  })

  it('运行中：点是静止的，「在动」只由那句话的 shimmer 说（没有转圈 / 呼吸）', async () => {
    mockRegistry.mockResolvedValue(view([entry({ script: 'show.py' })]))
    mockProbe.mockImplementation(() => new Promise(() => {}))
    await mount()
    await act(async () => runButton().click())
    const row = host.querySelector<HTMLElement>('[data-script-row="show.py"]')!
    expect(row.querySelector('[data-script-dot="running"]')).toBeTruthy()
    expect(row.querySelector('.animate-spin')).toBeNull()
    expect(row.querySelector('[data-script-running]')!.className).toContain('text-shimmer')
  })

  it('筛不到时是 EmptyState，不是一行裸字', async () => {
    mockRegistry.mockResolvedValue(view([entry({ script: 'show.py' })]))
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(
        <TooltipProvider>
          <ScriptLibrary query="nothing-like-this" />
        </TooltipProvider>,
      )
    })
    await flush()
    expect(host.querySelector('[data-script-no-match][data-empty-state]')).toBeTruthy()
  })

  it('行菜单（⋯ / ⇧F10）：运行与复制路径', async () => {
    mockRegistry.mockResolvedValue(view([entry({ script: 'show.py' })]))
    await mount()
    const row = host.querySelector<HTMLElement>('[data-script-row="show.py"] > div')!
    await act(async () => {
      row.dispatchEvent(new KeyboardEvent('keydown', { key: 'F10', shiftKey: true, bubbles: true, cancelable: true }))
    })
    expect(document.querySelector('[data-script-copy-path]')).toBeTruthy()
  })
})
