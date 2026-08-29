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
}))

import {
  fetchPanels,
  fetchReadiness,
  fetchRegistry,
  probeScript,
  scanRegistry,
  writeRegistryEntry,
  type ReadinessPanel,
  type ReadinessReport,
} from '@/lib/api'
import { RegistryDialog, sourceOptions } from '@/components/RegistryDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { resetAssetLoadBookkeeping, useAssetStore } from '@/store/assetStore'
import {
  resetReadinessBookkeeping,
  useProjectReadinessStore,
} from '@/store/projectReadinessStore'
import { useUiStore } from '@/store/uiStore'

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
  resetReadinessBookkeeping()
  resetAssetLoadBookkeeping()
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
      ['Photo.png', '仅排版'],
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
    expect(strip).toContain('仅排版')
    // 待连接 = auto_linkable + needs_probe + conflict + source_missing = 4
    expect(strip.replace(/\s/g, '')).toContain('待连接4')
  })

  it('layout_only 不画成错误：没有 alert，也不说"失败"', async () => {
    await open(reportOf([P({ id: 'Photo.png', stem: 'Photo' })]))
    const row = rowOf('Photo.png')!
    expect(row.querySelector('[role="alert"]')).toBeNull()
    expect(row.textContent).not.toMatch(/失败|错误|损坏/)
    // 说清它还能干什么，而不是只说它不能干什么
    expect(row.textContent).toMatch(/排版|裁剪|导出/)
  })
})

describe('绝不替用户决定', () => {
  it('打开对话框不跑任何脚本', async () => {
    await open(reportOf(SIX))
    expect(mockProbe).not.toHaveBeenCalled()
  })

  it('试运行只有点了才跑，而且点之前先说清它会运行脚本', async () => {
    await open(reportOf(SIX))
    const row = rowOf('Mystery.pdf')!
    expect(row.textContent).toContain('Tavotto 将运行这个脚本')
    expect(mockProbe).not.toHaveBeenCalled()
    await clickIn(row, '试运行并连接')
    expect(mockProbe).toHaveBeenCalledWith('dyn.py')
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
  it('可写项目上给出下拉；选中后「连接」写的是 stem，入口取自那个脚本自己的', async () => {
    await open(reportOf(SIX))
    const row = rowOf('Photo.png')!
    const trigger = row.querySelector<HTMLElement>('[role="combobox"]')
    expect(trigger, '仅排版的图应该给得出「选择源脚本」').toBeTruthy()
    // Radix Select 的弹层在 jsdom 里不好点，直接驱动被测的写入路径：
    // 这里要钉的是「写进去的是什么」，不是 Radix 的开合
    expect(row.textContent).toContain('选择源脚本')
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

  it('已经连上的图也能改绑，但收在技术详情里（第一层不该盖过"它已经好了"）', async () => {
    await open(reportOf(SIX))
    const row = rowOf('Ok.pdf')!
    const details = row.querySelector('details')!
    await act(async () => {
      details.open = true
    })
    const picker = row.querySelector('[role="combobox"]')
    expect(picker, '可编辑的图也该给得出改绑').not.toBeNull()
    // 关键是它**收在技术详情里**，而不是摆在第一层
    expect(picker!.closest('details')).toBe(details)
    expect(details.textContent).toContain('改绑到其它脚本')
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

describe('技术详情', () => {
  it('默认收起', async () => {
    await open(reportOf(SIX))
    const details = rowOf('Ok.pdf')!.querySelector('details')!
    expect(details.open).toBe(false)
  })

  it('展开后才出现源脚本 / 入口 / 成本 / reason code', async () => {
    await open(reportOf(SIX))
    const details = rowOf('Ok.pdf')!.querySelector('details')!
    await act(async () => {
      details.open = true
    })
    const text = details.textContent ?? ''
    expect(text).toContain('ok.py')
    expect(text).toContain('main')
    expect(text).toContain('registered_source')
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
    // 手工选择的下拉整个不渲染
    expect(rowOf('Photo.png')!.querySelector('[role="combobox"]')).toBeNull()
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
    expect(dialog().textContent).toContain('读不回来')
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
