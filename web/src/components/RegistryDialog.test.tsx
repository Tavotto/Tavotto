/**
 * 项目接入状态（Prompt 08 §六）。
 *
 * 这个对话框是「不能编辑的图」唯一的出口，所以守的是四件事：
 *
 * 1. **六个状态各有自然文案与各自的下一步**，不合并、不压扁；
 * 2. **绝不替用户决定**——冲突不自动挑一个，试运行只有点了才跑；
 * 3. **`layout_only` 不是错误**：它只是没有源脚本，图照旧能排版导出；
 * 4. **每次动作之后走统一刷新那一条路径**，不手拼状态。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchReadiness: vi.fn(),
  fetchRegistry: vi.fn(),
  fetchPanels: vi.fn(),
  refreshProject: vi.fn(),
  probeScript: vi.fn(),
  scanRegistry: vi.fn(),
  writeRegistryEntry: vi.fn(),
  createPreparationSession: vi.fn(),
}))

import {
  ApiError,
  createPreparationSession,
  WORKDIR_CONFIRMATION_CODE,
  fetchPanels,
  fetchReadiness,
  fetchRegistry,
  probeScript,
  scanRegistry,
  writeRegistryEntry,
  type ReadinessPanel,
  type ReadinessReport,
} from '@/lib/api'
import {
  RegistryDialog,
  parseStems,
  pickableStems,
  sourceOptions,
} from '@/components/RegistryDialog'
import { WorkdirConfirmDialog } from '@/components/WorkdirConfirmDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { resetAssetLoadBookkeeping, useAssetStore } from '@/store/assetStore'
import {
  resetReadinessBookkeeping,
  useProjectReadinessStore,
} from '@/store/projectReadinessStore'
import { useUiStore } from '@/store/uiStore'
import { useEnvStore } from '@/store/envStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useScriptArgvStore } from '@/store/scriptArgvStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { PREPARATION_PANEL_KEY } from '@/lib/preparationFlag'
import { setCurrentProjectId } from '@/lib/session'
import { reasonText, statusLabel } from '@/lib/readinessText'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
// jsdom 没有布局引擎，也就没有 scrollIntoView。聚焦那条用例测的是"滚到它 +
// 焦点落上去"，滚动本身在这里量不到，焦点量得到。
Element.prototype.scrollIntoView ??= () => {}

const mockReadiness = vi.mocked(fetchReadiness)
const mockRegistry = vi.mocked(fetchRegistry)
const mockPanels = vi.mocked(fetchPanels)
const mockProbe = vi.mocked(probeScript)
const mockScan = vi.mocked(scanRegistry)
const mockWrite = vi.mocked(writeRegistryEntry)
const mockCreateSession = vi.mocked(createPreparationSession)
/** 本地开关关闭（`'off'`）：接入中心委派素材库那台状态机（`scriptRunStore`）——旧路径 */
const legacyPath = () => localStorage.setItem(PREPARATION_PANEL_KEY, 'off')
const settle = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })

const P = (over: Partial<ReadinessPanel>): ReadinessPanel => ({
  id: 'Fig.pdf',
  stem: 'Fig',
  status: 'layout_only',
  reason_code: 'no_source_candidate',
  script: null,
  candidates: [],
  can_probe: false,
  can_manual_link: true,
  details: {},
  ...over,
})

/** 六个状态各一张图，形状与后端判定表逐条对应 */
const SIX: ReadinessPanel[] = [
  P({
    id: 'Ok.pdf', stem: 'Ok', status: 'editable', reason_code: 'registered_source',
    script: 'ok.py', details: { entry: 'main', cost: 'light' },
  }),
  P({
    id: 'Auto.pdf', stem: 'Auto', status: 'auto_linkable',
    reason_code: 'static_unique_candidate', candidates: ['auto.py'],
    can_probe: true, details: { candidate_scope: 'panel' },
  }),
  P({
    id: 'Mystery.pdf', stem: 'Mystery', status: 'needs_probe',
    reason_code: 'runtime_output_unknown', candidates: ['dyn.py'],
    can_probe: true, details: { candidate_scope: 'project' },
  }),
  P({
    id: 'Dup.pdf', stem: 'Dup', status: 'conflict',
    reason_code: 'multiple_source_candidates', candidates: ['old.py', 'new.py'],
    can_probe: true, details: { candidate_scope: 'panel' },
  }),
  P({
    id: 'Gone.pdf', stem: 'Gone', status: 'source_missing',
    reason_code: 'registered_script_missing', script: 'gone.py',
    details: { entry: 'main', cost: 'light' },
  }),
  P({ id: 'Photo.png', stem: 'Photo' }),
]

function reportOf(panels: ReadinessPanel[], over: Partial<ReadinessReport> = {}): ReadinessReport {
  const summary = {
    total: panels.length,
    editable: 0, auto_linkable: 0, needs_probe: 0,
    conflict: 0, source_missing: 0, layout_only: 0,
  }
  for (const p of panels) summary[p.status] += 1
  const base = {
    project_id: 'pj-a',
    fingerprint: 'fp-1',
    generated_at: 1,
    summary,
    panels,
    conflicts: [],
    project: { writable: true, registry_valid: true, scan_ok: true, can_rescan: true },
    issues: [],
    ...over,
  }
  // `panels` / `summary` 由上面算好，别被 `over` 里的半份盖掉
  return { ...base, panels, summary }
}

/**
 * 入口函数名刻意用**三个互不相同、且都不是 `main`** 的值：
 * `ok.py` → `draw`（已登记那份）、`new.py` → `plot`（这一轮的候选）、
 * `dyn.py` → `render`（脚本清单解析出的）。`old.py` 三处都没有 → 应当**不传**，
 * 让后端用它自己的默认。全写成 `main` 的话，「取自哪一个出处」根本量不出来。
 *
 * `ok.py` 在**两个**出处里都有，而且值故意不同（登记的是 `draw`，静态解出来的
 * 是 `main`）——现实里这两个本来就会分开（用户手工改过 entry、或脚本后来变了）。
 * 写成一样的话，去掉第一个出处照样得到同一个答案，那条判据就永远量不到自己。
 */
const REGISTRY_VIEW = {
  source: 'tavotto_registry.json',
  scripts: { 'ok.py': { entry: 'draw', cost: 'light', notes: '', stems: ['Ok'] } },
  candidates: [
    { script: 'new.py', entry: 'plot', stems: ['Dup'], new_stems: ['Dup'], unresolved: [],
      dynamic_names: false, save_calls: 1, registered: false },
  ],
  conflicts: {},
  all_scripts: [
    { script: 'ok.py', registered: true, static_stems: ['Ok'], entry_candidates: ['main'], reason: 'registered' as const, can_probe: true },
    { script: 'auto.py', registered: false, static_stems: ['Auto'], entry_candidates: ['main'], reason: 'static_candidate' as const, can_probe: true },
    { script: 'dyn.py', registered: false, static_stems: [], entry_candidates: ['render'], reason: 'dynamic_stems' as const, can_probe: true },
  ],
}

let root: Root

async function open(report: ReadinessReport) {
  mockReadiness.mockResolvedValue(report)
  useProjectReadinessStore.setState({ report })
  useUiStore.setState({ registryOpen: true })
  const mountEl = document.createElement('div')
  document.body.appendChild(mountEl)
  root = createRoot(mountEl)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <RegistryDialog />
      </TooltipProvider>,
    )
  })
  // Dialog 走 Radix portal：节点落在 document.body 上，不在挂载点里
  await act(async () => {
    await Promise.resolve()
  })
}

const dialog = () => document.querySelector('[role="dialog"]')!
const rowOf = (id: string) =>
  dialog().querySelector<HTMLElement>(`[data-panel-row="${CSS.escape(id)}"]`)
const buttonsIn = (el: Element | null) =>
  [...(el?.querySelectorAll('button') ?? [])].map((b) => b.textContent?.trim() ?? '')
const clickIn = async (el: Element | null, label: string) => {
  const btn = [...(el?.querySelectorAll('button') ?? [])].find((b) =>
    b.textContent?.includes(label),
  )
  expect(btn, `找不到按钮「${label}」`).toBeTruthy()
  await act(async () => {
    btn!.click()
    await Promise.resolve()
  })
}
/** 行尾的 ⋯（2026-09-11 Session 4）：可达名是「<文件名> 的更多操作」 */
const moreButton = (row: Element) =>
  [...row.querySelectorAll<HTMLButtonElement>('button')].find((b) =>
    (b.getAttribute('aria-label') ?? '').endsWith('的更多操作'),
  ) ?? null
/**
 * 打开一行的 ⋯ 菜单并返回菜单节点（Radix 把它 portal 到 body 上，不在行里）。
 * Radix 的触发器认 pointerdown（button 0），jsdom 没有 PointerEvent 构造器——
 * 同名的 MouseEvent 照样按事件名派发。
 */
const openMore = async (row: Element) => {
  const btn = moreButton(row)
  expect(btn, '这一行没有 ⋯').toBeTruthy()
  await act(async () => {
    btn!.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }))
    await Promise.resolve()
  })
  const menu = document.querySelector('[role="menu"]')
  expect(menu, '⋯ 没有打开菜单').toBeTruthy()
  return menu!
}

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
  vi.clearAllMocks()
  mockRegistry.mockResolvedValue(REGISTRY_VIEW)
  mockPanels.mockResolvedValue({ figures_dir: '/p', panels: [] })
  mockScan.mockResolvedValue({ changes: { added_scripts: [], added_stems: {} }, conflicts: {}, scripts: {} })
  mockWrite.mockResolvedValue({ scripts: {} })
  mockProbe.mockResolvedValue({
    script: 'dyn.py', entry: 'main', stems: ['Mystery'], descriptors: [], error: null, tried: [],
  })
  mockCreateSession.mockReturnValue(new Promise(() => {}))
  resetReadinessBookkeeping()
  resetAssetLoadBookkeeping()
  useScriptRunStore.getState().clear()
  useProjectPreparationStore.getState().clear()
  useProjectReadinessStore.getState().clear()
  useAssetStore.setState({ panels: [], byId: {}, loaded: true, loading: false, error: null })
  useUiStore.setState({ registryOpen: false, status: null })
})

afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
})

describe('六个状态', () => {
  it('每一张图都有状态名与一句自然话，一个都不缺', async () => {
    await open(reportOf(SIX))
    const expected: [string, string][] = [
      ['Ok.pdf', '可编辑'],
      ['Auto.pdf', '待连接'],
      ['Mystery.pdf', '需试运行'],
      ['Dup.pdf', '有冲突'],
      ['Gone.pdf', '源脚本丢失'],
      ['Photo.png', '仅版面'],
    ]
    for (const [id, label] of expected) {
      const row = rowOf(id)
      expect(row, `${id} 没有自己的一行`).toBeTruthy()
      expect(row!.textContent).toContain(label)
    }
  })

  it('普通用户看到的部分不出现实现术语（技术详情里才允许）', async () => {
    await open(reportOf(SIX))
    for (const id of SIX.map((p) => p.id)) {
      const row = rowOf(id)!
      // 一句话原因那一段：技术详情是 <details>，单独取上面的说明段
      const sentences = [...row.querySelectorAll('p')].map((p) => p.textContent ?? '').join(' ')
      expect(sentences, id).not.toMatch(/registry|注册表|\bstem\b|manifest|AST/i)
    }
  })

  it('顶部给出总计 / 可编辑 / 待连接 / 仅排版四个数', async () => {
    await open(reportOf(SIX))
    const strip = dialog().querySelector('dl')!.textContent ?? ''
    expect(strip).toContain('总计')
    expect(strip).toContain('可编辑')
    expect(strip).toContain('待连接')
    expect(strip).toContain('仅版面')
    // 待连接 = auto_linkable + needs_probe + conflict + source_missing = 4
    expect(strip.replace(/\s/g, '')).toContain('待连接4')
  })

  it('layout_only 不画成错误：没有 alert，也不说"失败"', async () => {
    await open(reportOf([P({ id: 'Photo.png', stem: 'Photo' })]))
    const row = rowOf('Photo.png')!
    expect(row.querySelector('[role="alert"]')).toBeNull()
    expect(row.textContent).not.toMatch(/失败|错误|损坏/)
    // 「说清它还能干什么」原先也断言在这一行上，但 2026-09-11 设计包之后行里不再列原因句，
    // 那条断言只是被状态名「仅排版」里的「排版」二字碰巧满足（2026-09-26 改名「仅版面」后现形）。
    // 那句话现在由素材卡与图内能力说明负责，看护在 AssetBrowser.readiness.test.tsx /
    // panelCapabilityNote.test.tsx；这里只守「不画成错误」。
    expect(row.textContent).toContain('仅版面')
  })
})

describe('嵌套对话框（2026-10-07 设计审计 §10.2）', () => {
  it('接入中心上再弹运行目录确认：只有栈底那一层画遮罩，不叠成两层暗', async () => {
    await open(reportOf(SIX))
    const extra = document.createElement('div')
    document.body.appendChild(extra)
    const second = createRoot(extra)
    await act(async () => {
      second.render(<WorkdirConfirmDialog />)
    })
    await act(async () => {
      useEnvStore.getState().requestWorkdirConfirmation({
        kind: 'workdir',
        code: WORKDIR_CONFIRMATION_CODE,
        script: 'dyn.py',
        reason: 'project_root_evidence',
        recommended: 'project_root',
        options: [],
        conflicts: [],
        reads: [],
      } as never)
    })
    expect(document.querySelectorAll('[data-dialog]').length).toBe(2)
    // 两层都有遮罩元素（点外面的判定仍落在它上面），但只有一层是有颜色的那一个
    expect(document.querySelectorAll('[data-dialog-scrim]').length).toBe(1)
    await act(async () => useEnvStore.getState().dismissWorkdirConfirmation())
    await act(async () => second.unmount())
    extra.remove()
  })
})

describe('绝不替用户决定', () => {
  it('打开对话框不跑任何脚本', async () => {
    await open(reportOf(SIX))
    expect(mockProbe).not.toHaveBeenCalled()
  })

  it('试运行只有点了才跑，而且点之前先说清它会运行脚本；默认打开准备面板（T09b），本组件一个执行请求都不发', async () => {
    useScriptArgvStore.getState().setTokens('dyn.py', ['--n', '3'])
    await open(reportOf(SIX))
    const row = rowOf('Mystery.pdf')!
    expect(row.textContent).toContain('Tavotto 会运行这个脚本')
    expect(mockCreateSession).not.toHaveBeenCalled()
    await clickIn(row, '试运行并连接')
    // 对话框让开、同一个准备面板打开：只读检查，参数草稿此刻冻结进会话
    expect(useUiStore.getState().registryOpen).toBe(false)
    expect(useUiStore.getState().guideCard).toBe('card')
    expect(mockCreateSession).toHaveBeenCalledTimes(1)
    expect(mockCreateSession.mock.calls[0][0]).toEqual({ script: 'dyn.py', argv: ['--n', '3'], argv_sensitive: false })
    expect(mockProbe, '接入中心不该再自己跑试运行').not.toHaveBeenCalled()
    useScriptArgvStore.getState().clear()
  })

  it('开关关闭：委派素材库那台状态机，参数草稿照样带上（T03：off 之后不退化成空 argv）', async () => {
    legacyPath()
    useScriptArgvStore.getState().setTokens('dyn.py', ['--n', '3'])
    await open(reportOf(SIX))
    await clickIn(rowOf('Mystery.pdf')!, '试运行并连接')
    await settle()
    expect(mockCreateSession).not.toHaveBeenCalled()
    expect(mockProbe).toHaveBeenCalledTimes(1)
    expect(mockProbe).toHaveBeenCalledWith('dyn.py', undefined, { argv: ['--n', '3'], sensitive: false })
    // 状态记在素材库那台状态机里：同一个脚本只有一份运行状态
    expect(useScriptRunStore.getState().byScript['dyn.py']?.phase).toBe('captured_one')
    expect(rowOf('Mystery.pdf')!.textContent).toContain('已连接 Mystery')
    useScriptArgvStore.getState().clear()
  })

  it('开关关闭：无参数运行替换掉的旧图名在那一行说清怎么恢复（T09b）', async () => {
    legacyPath()
    mockProbe.mockResolvedValue({
      script: 'dyn.py', entry: 'main', stems: ['Mystery'], descriptors: [], error: null, tried: [],
      unlinked_stems: ['Mystery_scaled'],
    })
    await open(reportOf(SIX))
    await clickIn(rowOf('Mystery.pdf')!, '试运行并连接')
    await settle()
    expect(rowOf('Mystery.pdf')!.textContent).toContain('此前带其他参数生成的 Mystery_scaled 已不再关联，用原参数再运行一次即可恢复')
  })

  it('试运行撞上起会话之前的依赖门：弹同一个授权框（载荷交给 envStore），不报「试运行失败」', async () => {
    // Windows 真机验收（main 493a1310）：门的载荷在试运行这条路上被当成失败吞掉，授权框从不弹出
    legacyPath()
    setCurrentProjectId('p1')
    useEnvStore.setState({ dependencyPreparation: null })
    const offer = { code: 'dependency_preparation_required', script: 'dyn.py', plan: {}, target_kind: 'tavotto_managed', targets: [], rounds_remaining: 3, skipped: false }
    mockProbe.mockResolvedValue({
      script: 'dyn.py', entry: null, stems: [], descriptors: [], tried: [],
      error: { code: 'dependency_preparation_required', message: '要先准备依赖', dependency_preparation: offer as never },
    })
    await open(reportOf(SIX))
    await clickIn(rowOf('Mystery.pdf')!, '试运行并连接')
    await settle()
    expect(useEnvStore.getState().dependencyPreparation, '载荷没交给 envStore').toEqual(offer)
    expect(dialog().textContent).not.toContain('试运行失败')
    // 门有了答案（授权准备成功）：重跑的是**那一台**状态机里停在门上的那一行，一次
    mockProbe.mockClear()
    mockProbe.mockResolvedValue({ script: 'dyn.py', entry: null, stems: ['Mystery'], descriptors: [], tried: [] } as never)
    await act(async () => {
      useScriptRunStore.getState().rerunGated('needs_preparation', 'dyn.py')
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(mockProbe, '门放行之后那一行没有重跑').toHaveBeenCalledTimes(1)
    // 别的脚本的答案不重跑这一行
    mockProbe.mockClear()
    await act(async () => {
      useScriptRunStore.getState().rerunGated('needs_preparation', 'other.py')
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(mockProbe).not.toHaveBeenCalled()
    useEnvStore.setState({ dependencyPreparation: null })
    setCurrentProjectId(null)
  })

  it('门以非 2xx 回来（请求直接抛 409）：同样交给授权框，不报「试运行失败」（#740 Codex P2）', async () => {
    legacyPath()
    setCurrentProjectId('p1')
    useEnvStore.setState({ dependencyPreparation: null })
    const offer = { code: 'dependency_preparation_required', script: 'dyn.py', plan: {}, target_kind: 'tavotto_managed', targets: [], rounds_remaining: 3, skipped: false }
    mockProbe.mockRejectedValue(
      new ApiError('要先准备依赖', 409, { code: 'dependency_preparation_required', dependency_preparation: offer }),
    )
    await open(reportOf(SIX))
    await clickIn(rowOf('Mystery.pdf')!, '试运行并连接')
    await settle()
    expect(useEnvStore.getState().dependencyPreparation, '抛出来的门没交给 envStore').toEqual(offer)
    expect(dialog().textContent).not.toContain('试运行失败')
    useEnvStore.setState({ dependencyPreparation: null })
    setCurrentProjectId(null)
  })

  it('接入中心与素材库是同一台状态机：门放行只重跑一次，在跑时再点不并发（#740 那组补丁的结构性替代）', async () => {
    legacyPath()
    setCurrentProjectId('p1')
    const offer = { code: 'dependency_preparation_required', script: 'dyn.py', plan: {}, target_kind: 'tavotto_managed', targets: [], rounds_remaining: 3, skipped: false }
    mockProbe.mockResolvedValue({
      script: 'dyn.py', entry: null, stems: [], descriptors: [], tried: [],
      error: { code: 'dependency_preparation_required', message: '要先准备依赖', dependency_preparation: offer as never },
    })
    await open(reportOf(SIX))
    await clickIn(rowOf('Mystery.pdf')!, '试运行并连接')
    await settle()
    expect(useScriptRunStore.getState().byScript['dyn.py']?.phase).toBe('needs_preparation')
    mockProbe.mockClear()
    let finish!: () => void
    const held = new Promise<void>((r) => (finish = r))
    let inFlight = 0
    let maxInFlight = 0
    mockProbe.mockImplementation(async () => {
      inFlight += 1
      maxInFlight = Math.max(maxInFlight, inFlight)
      await held
      inFlight -= 1
      return { script: 'dyn.py', entry: null, stems: ['Mystery'], descriptors: [], tried: [] } as never
    })
    await act(async () => {
      useScriptRunStore.getState().rerunGated('needs_preparation', 'dyn.py')
      await new Promise((r) => setTimeout(r, 0))
    })
    // 素材库那一行 = 接入中心这一行：再点一次（或另一处再触发）是同一台状态机里的 busy，不发第二个请求
    await act(async () => {
      void useScriptRunStore.getState().run('dyn.py')
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(mockProbe).toHaveBeenCalledTimes(1)
    await act(async () => {
      finish()
      await new Promise((r) => setTimeout(r, 10))
    })
    expect(mockProbe).toHaveBeenCalledTimes(1)
    expect(maxInFlight).toBe(1)
    useEnvStore.setState({ dependencyPreparation: null })
    setCurrentProjectId(null)
  })

  it('A → B → A：上一代停在门上的那一行随换代清空，这一代的放行不重跑它，也不留再打开的按钮（#740 Codex P2）', async () => {
    legacyPath()
    setCurrentProjectId('p1')
    const other = { code: 'dependency_preparation_required', script: 'other.py', plan: {}, target_kind: 'tavotto_managed', targets: [], rounds_remaining: 3, skipped: false }
    const offer = { ...other, script: 'dyn.py' }
    useEnvStore.setState({ dependencyPreparation: other as never })
    mockProbe.mockResolvedValue({
      script: 'dyn.py', entry: null, stems: [], descriptors: [], tried: [],
      error: { code: 'dependency_preparation_required', message: '要先准备依赖', dependency_preparation: offer as never },
    })
    await open(reportOf(SIX))
    await clickIn(rowOf('Mystery.pdf')!, '试运行并连接')
    await settle()
    // 别的脚本的授权框开着时撞上的门：那一份作答完关掉之后，这一行能把自己的那份再打开
    expect(useEnvStore.getState().dependencyPreparation, '前提：开着的是别的脚本那一份').toEqual(other)
    await act(async () => useEnvStore.setState({ dependencyPreparation: null }))
    await clickIn(rowOf('Mystery.pdf')!, '准备依赖…')
    expect(useEnvStore.getState().dependencyPreparation, '行上没有再打开的入口').toEqual(offer)
    // 换到 p2 再回 p1：项目 id 相同、代际已变（projectStore 每次换代都清 scriptRunStore）
    await act(async () => {
      useEnvStore.setState({ dependencyPreparation: null })
      setCurrentProjectId('p2')
      useScriptRunStore.getState().clear()
      setCurrentProjectId('p1')
      useScriptRunStore.getState().clear()
    })
    const stale = [...(rowOf('Mystery.pdf')?.querySelectorAll('button') ?? [])].find((b) =>
      b.textContent?.includes('准备依赖…'),
    )
    expect(stale, '换过项目之后还留着上一代的「准备依赖…」').toBeUndefined()
    mockProbe.mockClear()
    await act(async () => {
      useScriptRunStore.getState().rerunGated('needs_preparation', 'dyn.py')
      await new Promise((r) => setTimeout(r, 10))
    })
    expect(mockProbe, '上一代的待重跑在这一代被放行了').not.toHaveBeenCalled()
    setCurrentProjectId(null)
  })

  it('冲突：两个候选都列出来，一个都不预选、也不自动写', async () => {
    await open(reportOf(SIX))
    const row = rowOf('Dup.pdf')!
    const labels = buttonsIn(row)
    expect(labels.some((l) => l.includes('old.py'))).toBe(true)
    expect(labels.some((l) => l.includes('new.py'))).toBe(true)
    expect(mockWrite).not.toHaveBeenCalled()
  })

  it('冲突：点了哪个就写哪个（写的对象是 stem，不是这张图的文件名）', async () => {
    await open(reportOf(SIX))
    await clickIn(rowOf('Dup.pdf'), 'new.py')
    // 入口取自这一轮扫出来的候选（`plot`），不是写死的 `main`。
    // `append: true` 是这条路的必需项——一个脚本产出多张图是常态，整条替换
    // 会让 new.py 已经认领的其它图当场失去编辑入口，而用户只点了这一张。
    // `cost` / `notes` 一个字都不传：不提 = 保留磁盘上原来那个值。
    expect(mockWrite).toHaveBeenCalledWith({
      script: 'new.py', entry: 'plot', stems: ['Dup'], append: true,
    })
  })

  it('三处都不知道这个脚本的入口时**不传**，让后端用它自己的默认', async () => {
    await open(reportOf(SIX))
    await clickIn(rowOf('Dup.pdf'), 'old.py')
    expect(mockWrite).toHaveBeenCalledWith({
      script: 'old.py', entry: undefined, stems: ['Dup'], append: true,
    })
  })
})

describe('手工选择源脚本', () => {
  it('可写项目上 ⋯ 菜单里给得出「选择源脚本」；选中后写的是 stem，入口取自那个脚本自己的', async () => {
    await open(reportOf(SIX))
    const row = rowOf('Photo.png')!
    // 2026-09-11 Session 4：手工关联收进行尾的 ⋯ 菜单（低频动作不占第一层）
    const menu = await openMore(row)
    const sub = [...menu.querySelectorAll('[role="menuitem"]')].find((m) =>
      m.textContent?.includes('选择源脚本'),
    )
    expect(sub, '仅排版的图应该给得出「选择源脚本」').toBeTruthy()
    // 这里要钉的是「写进去的是什么」，不是 Radix 子菜单的开合：
    // 候选顺序与「不列当前脚本」由下面那条纯函数用例看住
  })

  /** 「全部脚本」段里第 n 行的手工填名 → 写入，走的是同一条写入路径 */
  const writeStemsInAdvanced = async (rowIndex: number, stem: string) => {
    const advanced = [...dialog().querySelectorAll('details')].find((d) =>
      d.querySelector('summary')?.textContent?.includes('全部脚本'),
    )!
    await act(async () => {
      advanced.open = true
    })
    const row = advanced.querySelectorAll('li')[rowIndex]
    const input = row.querySelector('input')!
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value',
      )!.set!
      setter.call(input, stem)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await clickIn(row, '写入')
  }

  it('已经连上的图也能改绑；技术详情段已去掉，改绑入口在行尾的 ⋯ 菜单里', async () => {
    await open(reportOf(SIX))
    const row = rowOf('Ok.pdf')!
    // 2026-09-11 设计包去掉了「技术详情」折叠段：行里不再有 <details>
    expect(row.querySelector('details')).toBeNull()
    const menu = await openMore(row)
    const sub = [...menu.querySelectorAll('[role="menuitem"]')].find((m) =>
      m.textContent?.includes('改绑到其它脚本'),
    )
    expect(sub, '可编辑的图也该给得出改绑').toBeTruthy()
  })

  // 选项住在 Radix 的弹层里，从 DOM 上量不到——所以判据打在那个纯函数上
  it('改绑的候选里**不含它现在连着的那一个**（选了等于什么都没做）', () => {
    const editable = SIX.find((p) => p.id === 'Ok.pdf')!
    expect(sourceOptions(editable, ['ok.py', 'auto.py', 'dyn.py'])).toEqual([
      'auto.py',
      'dyn.py',
    ])
  })

  it('候选排在前面（这一轮真的解出来的最可能对），其余按名字排', () => {
    const conflict = SIX.find((p) => p.id === 'Dup.pdf')! // candidates: old.py / new.py
    expect(sourceOptions(conflict, ['zzz.py', 'new.py', 'aaa.py', 'old.py'])).toEqual([
      'old.py',
      'new.py',
      'aaa.py',
      'zzz.py',
    ])
  })

  it('入口取自**已登记的那份**（第一个出处）', async () => {
    await open(reportOf(SIX))
    await writeStemsInAdvanced(0, 'Extra') // ok.py，注册表里记着 entry=draw
    expect(mockWrite).toHaveBeenCalledWith({
      script: 'ok.py', entry: 'draw', stems: ['Extra'],
    })
  })

  it('入口取自脚本清单解析出的那个（第三个出处），而不是一律写死 main', async () => {
    await open(reportOf(SIX))
    await writeStemsInAdvanced(2, 'Mystery') // dyn.py，清单里解析出 entry=render
    expect(mockWrite).toHaveBeenCalledWith({
      script: 'dyn.py', entry: 'render', stems: ['Mystery'],
    })
  })
})

describe('技术详情（2026-09-11 设计包已去掉）', () => {
  it('行里没有折叠段，也不出现 stem / 入口 / reason code 这些实现词', async () => {
    await open(reportOf(SIX))
    const row = rowOf('Ok.pdf')!
    expect(row.querySelector('details')).toBeNull()
    const text = row.textContent ?? ''
    expect(text).not.toContain('registered_source')
    expect(text).not.toContain('技术详情')
  })
})

describe('聚焦到指定的一张图', () => {
  it('焦点落到那一行上（「为什么不能编辑？」的落点）', async () => {
    useProjectReadinessStore.setState({ focusId: 'Gone.pdf' })
    await open(reportOf(SIX))
    expect(document.activeElement).toBe(rowOf('Gone.pdf'))
  })

  it('聚焦标记当场清掉：下次打开不该再高亮同一行', async () => {
    useProjectReadinessStore.setState({ focusId: 'Gone.pdf' })
    await open(reportOf(SIX))
    expect(useProjectReadinessStore.getState().focusId).toBeNull()
  })
})

describe('动作之后的刷新', () => {
  it('写完之后走统一刷新：就绪度与素材清单都重取，且都是 force', async () => {
    await open(reportOf(SIX))
    mockReadiness.mockClear()
    mockPanels.mockClear()
    await clickIn(rowOf('Dup.pdf'), 'new.py')
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(mockPanels).toHaveBeenCalled()
    expect(mockReadiness).toHaveBeenCalled()
  })

  it('重新扫描调的是既有端点，不是自己再判一遍', async () => {
    await open(reportOf(SIX))
    await clickIn(dialog(), '重新扫描')
    expect(mockScan).toHaveBeenCalled()
  })
})

describe('项目级状态', () => {
  it('只读项目：说明原因，且不渲染一个按了才发现存不下的关联控件', async () => {
    await open(
      reportOf(
        SIX.map((p) => ({ ...p, can_manual_link: false })),
        { project: { writable: false, registry_valid: true, scan_ok: true, can_rescan: true } },
      ),
    )
    expect(dialog().textContent).toContain('只读')
    // 冲突行上「用 old.py / 用 new.py」还在，但一律禁用（那是唯一的裁决入口，
    // 藏掉的话用户连"为什么不行"都看不到）
    const conflictButtons = [...rowOf('Dup.pdf')!.querySelectorAll('button')].filter((b) =>
      b.textContent?.includes('.py'),
    )
    expect(conflictButtons.length).toBeGreaterThan(0)
    expect(conflictButtons.every((b) => b.disabled)).toBe(true)
    // 手工选择的入口整个不渲染：只读项目上仅排版的图连 ⋯ 都没有
    expect(moreButton(rowOf('Photo.png')!)).toBeNull()
  })

  it('这一轮没扫成：说「可能不完整」，不冒充"没有候选"', async () => {
    await open(
      reportOf([P({ id: 'Photo.png', stem: 'Photo', reason_code: 'source_scan_unavailable' })], {
        conflicts: null,
        project: { writable: true, registry_valid: true, scan_ok: false, can_rescan: true },
      }),
    )
    expect(dialog().textContent).toContain('不完整')
  })

  it('记录文件读不回来：单独说一句，与"只读"分开', async () => {
    await open(
      reportOf(SIX, {
        project: { writable: true, registry_valid: false, scan_ok: true, can_rescan: true },
      }),
    )
    expect(dialog().textContent).toContain('无法读取')
    expect(dialog().textContent).not.toContain('只读')
  })
})

describe('取不到就绪度', () => {
  it('首次失败：给出可重试的错误态，而不是一个空白对话框', async () => {
    mockReadiness.mockRejectedValue(new Error('后端没起来'))
    useProjectReadinessStore.setState({ report: null, error: '后端没起来' })
    useUiStore.setState({ registryOpen: true })
    const mountEl = document.createElement('div')
    document.body.appendChild(mountEl)
    root = createRoot(mountEl)
    await act(async () => {
      root.render(
        <TooltipProvider>
          <RegistryDialog />
        </TooltipProvider>,
      )
    })
    expect(dialog().textContent).toContain('后端没起来')
    expect(buttonsIn(dialog()).some((l) => l.includes('重试'))).toBe(true)
  })
})

/**
 * 正常项目不该长得像故障排查页（审计 T10）。
 *
 * 全部可编辑时，四个计数格里有两个恒为零——「待连接 0 · 仅排版 0」不是信息，
 * 它把一个没有问题的项目画成一张需要排查的表。而每张可编辑图下面那句解释
 * 对每一张都一模一样，说 N 遍不比说一遍多告诉用户任何事。
 */
describe('正常状态', () => {
  const ALL_OK: ReadinessPanel[] = [
    P({ id: 'Fig1_kinetics.pdf', stem: 'Fig1_kinetics', status: 'editable',
      reason_code: 'registered_source', script: 'fig1_kinetics.py', details: { entry: 'main' } }),
    P({ id: 'Fig2_correlation.pdf', stem: 'Fig2_correlation', status: 'editable',
      reason_code: 'registered_source', script: 'fig2_correlation.py', details: { entry: 'main' } }),
  ]

  it('全都能编辑：一句话说完，不摆四个格子', async () => {
    await open(reportOf(ALL_OK))
    const text = dialog().textContent ?? ''
    expect(text).toContain('2 张图已就绪')
    expect(text).not.toContain('待连接')
    expect(text).not.toContain('仅版面')
  })

  it('有待连接项时照旧摆四个格子（那时零和非零都要看得见）', async () => {
    await open(reportOf(SIX))
    const text = dialog().textContent ?? ''
    expect(text).toContain('待连接')
    expect(text).toContain('仅版面')
    expect(text).not.toContain('已就绪')
  })

  it('可编辑那些不再逐张重复同一句解释；分组标题已经说了几张', async () => {
    await open(reportOf(ALL_OK))
    const text = dialog().textContent ?? ''
    // 计数是组名后面一个 meta 数字，不是「名字（N）」（全面打磨 D15）
    expect(text).toContain('可编辑2')
    // 那句话（`readinessText.reasonText` 的 registered_source）一次都不出现
    expect(text).not.toContain('可以直接改图里的内容')
  })

  it('不能编辑的那些不再逐条印原因句（2026-09-11 设计包）：状态只在角标与分组标题里', async () => {
    await open(reportOf(SIX))
    for (const p of SIX.filter((p) => p.status !== 'editable')) {
      expect(rowOf(p.id)?.textContent ?? '', p.id).not.toContain(reasonText(p))
      expect(rowOf(p.id)?.textContent ?? '', p.id).toContain(statusLabel(p.status))
    }
  })
})

describe('图卡的缩略图', () => {
  it('素材清单里有它就画缩略图，走的是既有的渲染地址', async () => {
    useAssetStore.setState({
      panels: [],
      byId: {
        'Ok.pdf': {
          id: 'Ok.pdf', name: 'Ok', folder: '.', kind: 'pdf',
          native_w_mm: 80, native_h_mm: 60, mtime: 42,
        },
      },
      loaded: true,
    })
    await open(reportOf(SIX))
    const img = rowOf('Ok.pdf')!.querySelector('img')!
    expect(img).not.toBeNull()
    expect(img.getAttribute('src')).toContain('Ok.pdf')
    expect(img.getAttribute('src')).toContain('42') // mtime 进 URL：文件变了就换一张
    expect(img.getAttribute('alt')).toBe('') // 装饰性，读屏不念
  })

  it('素材清单里没有它就画占位方块，不猜一个会 404 的地址', async () => {
    useAssetStore.setState({ panels: [], byId: {}, loaded: true })
    await open(reportOf(SIX))
    expect(rowOf('Ok.pdf')!.querySelector('img')).toBeNull()
  })
})

/**
 * 高级段的手工映射（审计 T10：手写逗号列表进了配置界面）。
 *
 * 这条兜底路径不能去掉——「产物名要跑起来才知道」的脚本，它的图名此刻不在
 * 任何清单里。能做的是：给输入框一个真正的标签（而不是拿 placeholder 当
 * 标签），并且把**项目里已有的图名**做成可选项，常见情形不必手打。
 */
describe('手工映射：图名列表', () => {
  const writable = () => reportOf(SIX)

  it('解析：中英文逗号与空白都算分隔，去重，空段丢掉', () => {
    expect(parseStems('a, b')).toEqual(['a', 'b'])
    expect(parseStems('a，b  c')).toEqual(['a', 'b', 'c'])
    expect(parseStems('  ')).toEqual([])
    expect(parseStems('a, a, b')).toEqual(['a', 'b'])
  })

  it('输入框有真正的标签，不是拿 placeholder 顶替', async () => {
    await open(writable())
    const input = dialog().querySelector<HTMLInputElement>('#stems-ok\\.py')!
    expect(input).not.toBeNull()
    const label = dialog().querySelector(`label[for="stems-ok.py"]`)
    expect(label?.textContent).toBe('此脚本画出的图')
  })

  it('写入的是解析后的那几个名字', async () => {
    await open(writable())
    const input = dialog().querySelector<HTMLInputElement>('#stems-ok\\.py')!
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, 'Alpha，Beta  Alpha')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const row = input.closest('li')!
    await clickIn(row, '写入')
    expect(mockWrite).toHaveBeenCalledWith(
      expect.objectContaining({ script: 'ok.py', stems: ['Alpha', 'Beta'] }),
    )
  })

  it('只读项目上根本不给这个控件（按了才发现存不下更糟）', async () => {
    await open(
      reportOf(SIX, {
        project: { writable: false, registry_valid: true, scan_ok: true, can_rescan: true },
      }),
    )
    expect(dialog().querySelector('#stems-ok\\.py')).toBeNull()
  })
})

describe('手工映射：从项目已有的图名里挑', () => {
  const pickerIn = (row: Element | null) =>
    row?.querySelector('[aria-label="从项目已有的图名里挑一个给 ok.py"]') ?? null

  const typeStems = async (input: HTMLInputElement, value: string) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, value)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }

  it('项目里还有没被挑过的图名时给挑选入口', async () => {
    await open(reportOf(SIX))
    const input = dialog().querySelector<HTMLInputElement>('#stems-ok\\.py')!
    expect(pickerIn(input.closest('li'))).not.toBeNull()
  })

  it('已经填进去的名字不再出现在可选项里；全填完了入口就收起', async () => {
    await open(reportOf(SIX))
    const input = dialog().querySelector<HTMLInputElement>('#stems-ok\\.py')!
    // SIX 的六个 stem 全填进去 → 一个可挑的都不剩
    await typeStems(input, SIX.map((p) => p.stem).join(', '))
    expect(pickerIn(input.closest('li'))).toBeNull()
    // 去掉一个 → 它又可以挑了
    await typeStems(input, SIX.slice(1).map((p) => p.stem).join(', '))
    expect(pickerIn(input.closest('li'))).not.toBeNull()
  })

  it('判据用解析后的名字：敲到一半的分隔符不会让已填的名字又冒出来', async () => {
    await open(reportOf(SIX))
    const input = dialog().querySelector<HTMLInputElement>('#stems-ok\\.py')!
    await typeStems(input, SIX.map((p) => p.stem).join(', ') + '，')
    expect(pickerIn(input.closest('li'))).toBeNull()
  })
})

/**
 * 可挑的图名（纯函数）。
 *
 * 用 DOM 量不到这一维：选项住在 Radix 的弹层里，触发器上看不见。实测过
 * ——把判据换成「是那串文本的子串」之后，上面那几条 DOM 用例全绿。
 */
describe('可挑的图名', () => {
  it('已经填进去的那些不再出现', () => {
    expect(pickableStems(['Ok', 'Auto'], ['Ok'])).toEqual(['Auto'])
    expect(pickableStems(['Ok', 'Auto'], ['Ok', 'Auto'])).toEqual([])
    expect(pickableStems(['Ok', 'Auto'], [])).toEqual(['Ok', 'Auto'])
  })

  it('判「等于」不判「子串」：填了 Ok_v2 之后 Ok 仍然可挑', () => {
    expect(pickableStems(['Ok', 'Ok_v2'], ['Ok_v2'])).toEqual(['Ok'])
    expect(pickableStems(['Fig1'], ['Fig1_kinetics'])).toEqual(['Fig1'])
  })
})

/**
 * 「仅待连接项展示下一步」（审计 T10）。
 *
 * 一张**已经能编辑**的图第一层只该有一个动作：把它放上画布。「重新试运行」
 * 是排障动作，摆在第一层会让一张已经好了的图看起来还有事要做。它和「改绑」
 * 一起收进技术详情——那一段明确是给排障用的。
 */
describe('可编辑图的动作层级', () => {
  /** `<details>` 里的内容照样在 DOM 里，所以判据必须问「它在不在那一层」 */
  const firstLevelButtons = (id: string) => {
    const row = rowOf(id)!
    return [...row.querySelectorAll('button')]
      .filter((b) => !b.closest('details'))
      .map((b) => b.textContent?.trim() ?? '')
  }

  it('第一层第一颗是「添加到画布」', async () => {
    useAssetStore.setState({
      panels: [],
      byId: {
        'Ok.pdf': {
          id: 'Ok.pdf', name: 'Ok', folder: '.', kind: 'pdf',
          native_w_mm: 80, native_h_mm: 60, mtime: 1,
        },
      },
      loaded: true,
    })
    await open(reportOf(SIX))
    expect(firstLevelButtons('Ok.pdf')[0]).toBe('添加到画布')
  })

  it('「重新试运行」还在：收进行尾的 ⋯ 菜单；默认打开准备面板，开关关闭时跑既有的试运行端点', async () => {
    await open(reportOf(SIX))
    const row = rowOf('Ok.pdf')!
    // 第一层不再有它——一张已经好了的图不该看起来还有事要做
    expect(firstLevelButtons('Ok.pdf').join(' ')).not.toContain('重新试运行')
    const menu = await openMore(row)
    const reprobe = [...menu.querySelectorAll('[role="menuitem"]')].find((m) =>
      m.textContent?.includes('重新试运行'),
    )
    expect(reprobe, '排障动作不该被删掉').toBeTruthy()
    await act(async () => {
      reprobe!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
    })
    expect(mockCreateSession.mock.calls.map((c) => c[0])).toEqual([{ script: 'ok.py' }])
    expect(mockProbe).not.toHaveBeenCalled()
    // 开关关闭：同一个菜单项走素材库那台状态机
    await act(async () => root.unmount())
    document.body.innerHTML = ''
    legacyPath()
    await open(reportOf(SIX))
    const again = [...(await openMore(rowOf('Ok.pdf')!)).querySelectorAll('[role="menuitem"]')].find((m) =>
      m.textContent?.includes('重新试运行'),
    )
    await act(async () => {
      again!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
    })
    expect(mockProbe).toHaveBeenCalledWith('ok.py')
  })

  it('没有任何低频动作可放时（只读项目上的仅排版图）就没有 ⋯', async () => {
    await open(
      reportOf([P({ id: 'Photo.png', stem: 'Photo' })], {
        project: { writable: false, registry_valid: true, scan_ok: true, can_rescan: true },
      }),
    )
    expect(moreButton(rowOf('Photo.png')!)).toBeNull()
  })

  it('待连接的那些第一层照旧有下一步', async () => {
    await open(reportOf(SIX))
    expect(firstLevelButtons('Auto.pdf')).toContain('自动连接')
    expect(firstLevelButtons('Mystery.pdf').join(' ')).toContain('试运行并连接')
    expect(firstLevelButtons('Dup.pdf').join(' ')).toContain('用 old.py')
  })
})

describe('换项目 / 重新打开（#831 Codex P1 + 维护者复审：退场动画里留着的那份正文）', () => {
  const RT_A = { asset_id: 'rt-a', stem: 'Mystery', script: 'dyn.py' } as never
  const VIEW_B = {
    ...REGISTRY_VIEW,
    scripts: {},
    candidates: [],
    all_scripts: [
      { script: 'b_only.py', registered: false, static_stems: ['Bee'], entry_candidates: ['main'], reason: 'static_candidate' as const, can_probe: true },
      // 同名脚本：B 里也有一个 dyn.py——A 的试运行结果按脚本名挂上去就会冒在 B 的这一行上
      { script: 'dyn.py', registered: false, static_stems: [], entry_candidates: ['main'], reason: 'dynamic_stems' as const, can_probe: true },
    ],
  }
  const REPORT_B = reportOf([P({ id: 'Bee.pdf', stem: 'Bee' })], { project_id: 'pj-b' })
  const PROBED_A = { script: 'dyn.py', entry: 'main', stems: ['Mystery'], descriptors: [RT_A], error: null, tried: [] }

  /** 与 projectStore 换项目同序的那三步：会话认领 B、脚本运行换代、就绪度清掉；然后 B 的报告落地 */
  const switchToB = async () => {
    mockReadiness.mockResolvedValue(REPORT_B)
    mockRegistry.mockResolvedValue(VIEW_B)
    await act(async () => {
      setCurrentProjectId('p2')
      useScriptRunStore.getState().clear()
      useProjectReadinessStore.getState().clear()
    })
    await act(async () => {
      useProjectReadinessStore.setState({ report: REPORT_B })
      await new Promise((r) => setTimeout(r, 0))
    })
  }
  const settle = () =>
    act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
  const reopen = async () => {
    await act(async () => {
      useUiStore.setState({ registryOpen: true })
    })
    await settle()
  }
  /** A 的东西：A 才有的脚本（auto.py 只在 A 的注册表视图里）、A 的试运行结果与它的「添加到画布」 */
  const showsA = () => {
    const text = dialog().textContent ?? ''
    return text.includes('auto.py') || text.includes('添加到画布') || text.includes('已连接')
  }
  const addButtons = () =>
    [...dialog().querySelectorAll('button')].filter((b) => (b.textContent ?? '').includes('添加到画布'))

  /**
   * 退场动画（生产代码一行不改）：jsdom 不算样式，`animationName` 永远是空、Radix Presence 当场卸载。
   * 这里让 `getComputedStyle` 按节点此刻的 `data-state` 报动画名（开 = fade-in、关 = fade-out）——
   * Presence 拿到的是同一个样式对象、关的那一刻读到名字变了，就把内容留到 `animationend`，与浏览器一致
   */
  let styleSpy: { mockRestore: () => void } | null = null
  const simulateExitAnimation = () => {
    const real = window.getComputedStyle.bind(window)
    styleSpy = vi.spyOn(window, 'getComputedStyle').mockImplementation((el, pseudo) => {
      const base = real(el, pseudo)
      if (!(el instanceof HTMLElement) || !el.hasAttribute('data-state')) return base
      return new Proxy(base, {
        get(t, k) {
          if (k === 'animationName') return el.getAttribute('data-state') === 'closed' ? 'fade-out' : 'fade-in'
          const v = Reflect.get(t, k, t)
          return typeof v === 'function' ? v.bind(t) : v
        },
      })
    })
  }
  /** 退场动画放完：给留着的节点发 animationend（jsdom 没有 AnimationEvent，名字挂在普通 Event 上） */
  const finishExit = () =>
    act(async () => {
      for (const el of document.querySelectorAll('[data-state="closed"]')) {
        const e = new Event('animationend')
        Object.defineProperty(e, 'animationName', { value: 'fade-out' })
        el.dispatchEvent(e)
      }
    })
  /** 关掉：退场动画里同一份正文还挂着（data-state=closed）——这条前提不成立的话下面的用例量不到东西 */
  const closeRetained = async () => {
    await act(async () => useProjectReadinessStore.getState().closeCenter())
    const retained = document.querySelector('[role="dialog"]')
    expect(retained, '退场动画没有留住正文：模拟失效').not.toBeNull()
    expect(retained!.getAttribute('data-state')).toBe('closed')
  }

  afterEach(() => {
    styleSpy?.mockRestore()
    styleSpy = null
    setCurrentProjectId(null)
  })

  it('A 里试运行过、对话框开着换到 B：高级段与试运行结果都是 B 的，没有 A 的「添加到画布」', async () => {
    legacyPath() // 这条保护的是试运行结果不跨项目：默认走准备面板时接入中心不发试运行，只有旧路径（开关关闭）会落描述符
    setCurrentProjectId('p1')
    mockProbe.mockResolvedValue(PROBED_A)
    await open(reportOf(SIX))
    await clickIn(rowOf('Mystery.pdf')!, '试运行并连接')
    await settle()
    expect(dialog().textContent).toContain('把 Mystery 添加到画布')
    const fetches = mockRegistry.mock.calls.length
    await switchToB()
    expect(mockRegistry.mock.calls.length, 'B 的注册表视图没有重取').toBeGreaterThan(fetches)
    expect(dialog().textContent).toContain('b_only.py')
    expect(showsA(), 'B 里还显示着 A 的脚本 / 试运行结果').toBe(false)
  })

  it('A 里试运行 → 关 → 退场动画没放完就换到 B 并重新打开：重取 B 的视图，没有 A 的描述符与「添加到画布」', async () => {
    legacyPath() // 这条保护的是试运行结果不跨项目：默认走准备面板时接入中心不发试运行，只有旧路径（开关关闭）会落描述符
    simulateExitAnimation()
    setCurrentProjectId('p1')
    mockProbe.mockResolvedValue(PROBED_A)
    await open(reportOf(SIX))
    await clickIn(rowOf('Mystery.pdf')!, '试运行并连接')
    await settle()
    expect(addButtons().length, "A 的试运行结果应先出现").toBeGreaterThan(0)
    await closeRetained()
    const fetches = mockRegistry.mock.calls.length
    await switchToB()
    await reopen()
    expect(dialog().getAttribute('data-state')).toBe('open')
    expect(mockRegistry.mock.calls.length, '重新打开没有重取注册表视图').toBeGreaterThan(fetches)
    expect(dialog().textContent).toContain('b_only.py')
    expect(addButtons(), 'A 的 runtime 描述符留在了 B 的对话框里').toHaveLength(0)
    expect(showsA()).toBe(false)
  })

  it('对照：退场动画先放完（正文已卸）再换到 B、重新打开——同样只有 B 的', async () => {
    simulateExitAnimation()
    setCurrentProjectId('p1')
    mockProbe.mockResolvedValue(PROBED_A)
    await open(reportOf(SIX))
    await clickIn(rowOf('Mystery.pdf')!, '试运行并连接')
    await settle()
    await closeRetained()
    await finishExit()
    expect(document.querySelector('[role="dialog"]'), 'animationend 之后正文应已卸载').toBeNull()
    const fetches = mockRegistry.mock.calls.length
    await switchToB()
    await reopen()
    expect(mockRegistry.mock.calls.length).toBeGreaterThan(fetches)
    expect(dialog().textContent).toContain('b_only.py')
    expect(showsA()).toBe(false)
  })

  it('同一个项目里关掉、退场动画没放完又打开：也是新的一次打开——重取视图，上一次的试运行结果不带过来', async () => {
    legacyPath() // 这条保护的是试运行结果不跨项目：默认走准备面板时接入中心不发试运行，只有旧路径（开关关闭）会落描述符
    simulateExitAnimation()
    setCurrentProjectId('p1')
    mockProbe.mockResolvedValue(PROBED_A)
    await open(reportOf(SIX))
    await clickIn(rowOf('Mystery.pdf')!, '试运行并连接')
    await settle()
    expect(addButtons().length, "A 的试运行结果应先出现").toBeGreaterThan(0)
    await closeRetained()
    const fetches = mockRegistry.mock.calls.length
    await reopen()
    expect(mockRegistry.mock.calls.length, '重新打开没有重取注册表视图').toBeGreaterThan(fetches)
    // T09b 起试运行状态归素材库那台状态机（`scriptRunStore`，按项目代际清空），不再是本对话框的局部状态：
    // 同一个项目里重新打开，同一个脚本只有一份运行状态——结果仍在，且不是上一次正文的残留（上面已重取视图）。
    // 跨项目的不带过来由上面几条（换到 B）钉住
    expect(addButtons().length, '同项目里重开：运行状态应仍是那台状态机里的那一份').toBeGreaterThan(0)
  })

  it('A 的试运行在飞时换到 B（对话框开着）：迟到的结果不落进 B 的对话框', async () => {
    legacyPath() // 这条保护的是试运行结果不跨项目：默认走准备面板时接入中心不发试运行，只有旧路径（开关关闭）会落描述符
    setCurrentProjectId('p1')
    let finish!: (v: Awaited<ReturnType<typeof probeScript>>) => void
    mockProbe.mockReturnValueOnce(new Promise((r) => (finish = r)))
    await open(reportOf(SIX))
    await clickIn(rowOf('Mystery.pdf')!, '试运行并连接')
    await switchToB()
    await act(async () => {
      finish(PROBED_A as never)
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(dialog().textContent).toContain('b_only.py')
    expect(showsA(), 'A 迟到的试运行结果落进了 B').toBe(false)
  })

  it('A 的试运行在飞 → 关 → 退场动画里换到 B 并重新打开 → A 的结果才回来：不落地', async () => {
    simulateExitAnimation()
    setCurrentProjectId('p1')
    let finish!: (v: Awaited<ReturnType<typeof probeScript>>) => void
    mockProbe.mockReturnValueOnce(new Promise((r) => (finish = r)))
    await open(reportOf(SIX))
    await clickIn(rowOf('Mystery.pdf')!, '试运行并连接')
    await closeRetained()
    await switchToB()
    await reopen()
    await act(async () => {
      finish(PROBED_A as never)
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(dialog().textContent).toContain('b_only.py')
    expect(addButtons(), 'A 迟到的描述符落进了 B').toHaveLength(0)
    expect(showsA()).toBe(false)
  })
})
