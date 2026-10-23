/**
 * 导出面板（ADR 0031）。四组纪律各一组用例：
 *
 * 1. **信息架构**：文件名在最上方；必须删掉的东西一个都不许回来
 *    （预设、期刊宽、facts 大方格、PyMuPDF/Codex 说明、profile id/版本、
 *    「打包项目」、「留档」这个词）；
 * 2. **输出范围**：默认跟工作流走、可切换、**不可用时说出原因而不是隐藏**；
 * 3. error 默认阻止导出，用户显式确认后才放行，且**确认要写进样式检查报告**；
 * 4. **PPI 只在位图输出时出现**；文件名非法时就地报错并挡住导出。
 */
import { act } from 'react'
import { i18n, initI18n, literal } from '@/i18n'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// 预检埋点走的是 `/api/telemetry/event`；这里只把那一个函数换掉，
// 其余 api 保持真实实现（本文件靠 stubFetch 打桩 /api/export）
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  postTelemetryEvent: vi.fn(() => Promise.resolve({ accepted: true })),
  // 后端导出默认值（#715 PR-B）：默认「后端没有这组端点」，个别用例改答
  fetchExportDefaultsRemote: vi.fn(() => Promise.resolve(undefined)),
}))

// 「在文件夹中显示」走桌面壳的 IPC；只有个别用例把 `__TAURI_INTERNALS__` 装上
const tauriInvoke = vi.hoisted(() => vi.fn(async (..._args: unknown[]) => undefined as unknown))
vi.mock('@tauri-apps/api/core', () => ({ invoke: tauriInvoke }))

import { ExportDialog } from '@/components/ExportDialog'
import { pixelPreview } from '@/lib/exportRequest'
import { hydrateExportDefaults, readExportDefaults, writeExportDefaults } from '@/lib/exportDefaults'
import { fetchExportDefaultsRemote, postTelemetryEvent } from '@/lib/api'
import { setTelemetryEnabled } from '@/lib/telemetry'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { renderKey, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { runValidation, useValidationStore } from '@/store/validationStore'
import { bindingFor } from '@/lib/specBinding'
import { DEFAULT_PROFILE_ID } from '@/lib/profile'
import { profileName } from '@/lib/profileText'
import { toCatalog, useProfileStore } from '@/store/profileStore'
import { applyExportJob, cancelCurrentExport, refreshExportStatus, resetExportState, runExport, useExportStore } from '@/store/exportStore'
import { currentFigureOf } from '@/lib/problemContext'
import { effectiveScope, issuesInScope } from '@/lib/problemList'
import { useSelectionStore } from '@/store/selectionStore'
import { useWorkspaceStore } from '@/store/workspace'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { seedExactRender } from '@/test/renderFixtures'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/** 一张 8pt 刻度的图：撞绝对下限 → error，默认阻止导出 */
const manifest = (tickPt: number) => ({
  stem: 'Fig1',
  size_mm: [80, 60],
  elements: [
    {
      gid: 'axes_0.xticks',
      role: 'ticks',
      label: 'x 刻度',
      bbox: [0.1, 0.9, 0.8, 0.05],
      draggable: false,
      editable: [
        { prop: 'fontsize', type: 'number', value: tickPt },
        { prop: 'direction', type: 'enum', value: 'in' },
      ],
    },
  ],
})

const panel: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 0,
  y: 0,
  w: 80,
  h: 60,
}

let container: HTMLDivElement
let root: Root
let exportBodies: Record<string, unknown>[]

/** 后端把作业**一次跑完**就回终局：用例不必等轮询 */
let jobStatus: 'done' | 'partial' | 'conflict' | 'running' = 'done'
/** 终局回执里的 outputs（不设 = 一件没有 manifest 的 PDF） */
let jobOutputs: Record<string, unknown>[] | null = null
let jobWarnings: string[] = []
/** 后端 validate 的 `route`（#1209）：按请求体现造；null = 旧后端 / 算不出（响应里没有 route） */
/** 逐个作业依次给的状态（一批作业里每张一个）；用完退回 `jobStatus` */
/** 每次 `buildBatchInput` 调用时的微任务纪元（见「冻结是一个同步块」） */
const batchBuildLog: number[] = []
let jobStatusSeq: string[] | null = null
/** `/api/export/validate` 回包前先等多久（模拟 route 慢：冻结段每张最多等它 1.5 s） */
let routeDelayMs = 0
let routeOf: ((req: Record<string, any>) => Record<string, unknown>) | null = null
/** `/api/runtime/status` 的回答（null = 走兜底）；`runtimeStatusGate` 非 null 时先等它再回（模拟 ensure() 往返） */
let runtimeStatus: Record<string, unknown> | null = null
let runtimeStatusGate: Promise<void> | null = null
/** 非 0 时 `/api/runtime/status` 回这个 HTTP 状态码（模拟服务端瞬时失败）；-1 = 抛网络错误 */
let runtimeStatusFail = 0

function stubFetch() {
  exportBodies = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/api/export/start') || url.endsWith('/api/export')) {
      exportBodies.push(JSON.parse(String(init?.body ?? '{}')))
      const status = jobStatusSeq?.length ? jobStatusSeq.shift()! : jobStatus
      return new Response(
        JSON.stringify({
          job_id: jobStatusSeq ? `j${exportBodies.length}` : 'j1',
          status,
          outputs:
            status === 'done' || status === 'partial'
              ? (jobOutputs ?? [
                  {
                    format: 'pdf',
                    name: 'a.pdf',
                    url: '/exports/a.pdf',
                    bytes: 1234,
                    dimensions: { px: null, mm: [80, 60] },
                    vector: true,
                    status: 'done',
                    replaced: false,
                    error: null,
                  },
                ])
              : [],
          warnings: jobWarnings,
          conflicts: status === 'conflict' ? ['a.pdf'] : [],
          export_dir: '/out',
          error: null,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    }
    if (url.includes('/api/runtime/status') && runtimeStatusFail) {
      if (runtimeStatusFail < 0) throw new TypeError('network down')
      return new Response(JSON.stringify({ error: { code: 'boom' } }), { status: runtimeStatusFail })
    }
    if (url.includes('/api/runtime/status') && runtimeStatus) {
      if (runtimeStatusGate) await runtimeStatusGate
      return new Response(JSON.stringify(runtimeStatus), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    if (url.includes('/api/export/validate') && routeOf) {
      if (routeDelayMs) await new Promise((r) => setTimeout(r, routeDelayMs))
      const req = JSON.parse(String(init?.body ?? '{}'))
      return new Response(JSON.stringify({ ok: true, conflicts: [], writable: true, route: routeOf(req) }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response(JSON.stringify({ figures_dir: '/figs', panels: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch
}

async function setup(tickPt: number, page = { w: 80, h: 60 }, panelH = 60) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_export')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = page
    // immer 会把 doc 冻起来，模板对象得整份拷贝而不是就地改
    d.objects = [{ ...panel, h: panelH }]
  })
  useAssetStore.setState({
    byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } },
  } as never)
  const key = renderKey('Fig1.pdf', [])
  useRenderStore.setState({
    byKey: {
      [key]: {
        fileId: 'Fig1.pdf',
        rev: 1,
        manifest: manifest(tickPt),
        svg: null,
        status: 'ready',
        error: null,
        code: '',
        module: '',
        traceback: '',
        warnings: [],
        timings: {},
        stale: false,
        lastPatches: '[]',
        wantPatches: '[]',
        previewDpi: null,
      },
    } as never,
    latest: { 'Fig1.pdf': key },
    tracked: {},
    building: {},
  })
  useUiStore.getState().setExportOpen(true)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ExportDialog />
      </TooltipProvider>,
    )
  })
}

const text = () => document.body.textContent ?? ''

/** 位图素材 + 快速编辑（原图范围）的夹具；`overrides` 决定走"照抄"还是"重画" */
async function setupRaster(
  overrides: { gid: string; prop: string; value: unknown }[],
  script?: string,
) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_raster')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 180, h: 120 }
    d.objects = [
      {
        ...panel,
        id: 'r1',
        fileId: 'r1.png',
        fileKind: 'raster',
        nativeW: 10.16,
        nativeH: 6.77,
        pxW: 120,
        pxH: 80,
        overrides,
      } as never,
    ]
  })
  useAssetStore.setState({
    byId: {
      'r1.png': {
        id: 'r1.png',
        mtime: 1,
        ...(script ? { script } : {}),
        original_spec: {
          source_kind: 'raster',
          logical_w_mm: 10.16,
          logical_h_mm: 6.77,
          px_w: 120,
          px_h: 80,
          dpi: 300,
          dpi_source: 'metadata',
          viewport_pt: null,
          transparent: false,
        },
      },
    },
  } as never)
  useRenderStore.setState({ byKey: {}, latest: {}, tracked: {}, building: {} })
  useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'r1' })
  useUiStore.getState().setExportOpen(false)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ExportDialog />
      </TooltipProvider>,
    )
  })
  await act(async () => {
    useUiStore.getState().setExportOpen(true)
  })
}

const button = (label: string) =>
  [...document.body.querySelectorAll('button')].find((b) => b.textContent?.includes(label))

/** 格式复选框（组件工作台批次把 aria-pressed 的按钮改成了普通复选框，`FormatCheck`） */
const formatBox = (title: string) =>
  document.body.querySelector<HTMLInputElement>(`[data-export-formats] [data-export-format="${title.toLowerCase()}"] input[type="checkbox"]`)!
/** 位图分辨率下拉：只在选了位图格式时出现（工作台批次起它坐在文件名那一行右侧，没有「分辨率」文字标签） */
const ppiSelect = () => document.body.querySelector('[role="combobox"][aria-label="位图分辨率"]')
/** 知情确认框：格式那五颗复选框排在它前面，**不能拿页面里的第一颗**——认产品给的锚点 */
const confirmBox = () =>
  document.body.querySelector<HTMLInputElement>('input[data-export-confirm]')

/**
 * 「导出内容」那一组单选（多图排版才有；单图编辑没有输出范围）。顺序固定为 [原图, 整版]：
 * 老用例按下标认，界面上的视觉顺序是「整版 / 每张图单独导出」，两者不是一回事，认 `data-value`。
 */
const scopeRadios = () =>
  ['original', 'canvas']
    .map((v) =>
      document.body.querySelector<HTMLButtonElement>(
        `[data-onboarding-anchor="export-scope"] [data-value="${v}"]`,
      ),
    )
    .filter((b): b is HTMLButtonElement => !!b)

/** 有阻断项的信号：「要改」计数芯片（不再有任何警告句 / 红字提示） */
const errorHint = () => document.body.querySelector('[data-count-chip="danger"]')
const anyHint = errorHint

/** 「每张图单独导出」是依次发多个作业（每张各一个）：等这一批收尾 */
const batchDone = async (ms = 10_000) => {
  const t0 = Date.now()
  while (Date.now() - t0 < ms && !useExportStore.getState().batch?.done) {
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 50))
    })
  }
}

/** 等某个条件成立（批里每张之间有 250 ms 的 route 防抖，固定睡眠在负载高时不稳） */
const until = async (cond: () => boolean, ms = 8000) => {
  const t0 = Date.now()
  while (Date.now() - t0 < ms && !cond()) {
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 50))
    })
  }
}

/** 结果块默认只有一句摘要；逐个文件 / 核验 / 警告 / 诊断在折叠的「详情」里，要看先展开 */
const openResultDetail = async () => {
  const toggle = document.body.querySelector('[data-export-result] [data-notice-detail-toggle]')
  if (toggle) await act(async () => { (toggle as HTMLElement).click() })
}
const click = async (el: Element) => {
  await act(async () => {
    ;(el as HTMLElement).click()
    await new Promise<void>((r) => setTimeout(r, 0))
  })
}

beforeEach(() => {
  localStorage.clear()
  jobStatus = 'done'
  jobStatusSeq = null
  routeDelayMs = 0
  jobOutputs = null
  jobWarnings = []
  resetExportState()
  useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
  useSelectionStore.getState().clear()
  stubFetch()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

/*
 * 模式：#1292 起 `mode` 由激活画布上的图数推出（`workspaceStore.settleMode` 是唯一写入点），而导出对话框只读 `mode`
 * （`fast_edit` = 单图档，其余 = 多图档）。本文件的夹具直接给 `mode` 赋值，是**有意的**：它把「对话框怎么读 mode」
 * 与「mode 怎么推出」拆开，且需要 #1292 之后到不了的组合（排版里只有一张图，才能测「整版 / 每张图」两档在单张图上的口径）。
 * 「模式随图数推出」本身由 `store/workspace.test.ts` 钉着，这里不重复。
 */
vi.mock('@/lib/exportBatchInput', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/exportBatchInput')>()
  let epoch = 0
  return {
    ...real,
    buildBatchInput: (...args: Parameters<typeof real.buildBatchInput>) => {
      batchBuildLog.push(epoch)
      // 如果两次调用之间让出过（任何 await / 微任务），这个纪元就会推进
      queueMicrotask(() => {
        epoch += 1
      })
      return real.buildBatchInput(...args)
    },
  }
})

describe('信息架构：删掉的东西不许回来', () => {
  it('文件名在最上方（第一个可输入的控件）', async () => {
    await setup(9)
    const inputs = [...document.body.querySelectorAll('input')].filter(
      (i) => i.type !== 'checkbox',
    )
    // 第一个可输入的控件就是文件名（`d_export` 文档名）。文件名下面那行
    // 「这次会写哪几个文件」的预览按 2026-09-11 组件工作台批次去掉了——扩展名
    // 由格式复选框直接说出来，不再复述一遍
    expect((inputs[0] as HTMLInputElement).value).toBe(useDocumentStore.getState().doc.name)
  })

  it('§五的删除清单逐条不在界面上', async () => {
    await setup(9)
    const body = text()
    for (const gone of [
      '预设',            // 无作用的预设整行
      '期刊宽',          // 与规范设置重复
      '栏位',            // facts 大方格
      '最小有效字号',
      'PyMuPDF',         // 内部实现名（已退役，仍不许出现）
      'PDFium',          // 内部实现名
      'pikepdf',
      'Codex',
      '打包项目',        // 搬到了文档菜单
      '留档',            // 含义不清的标签
      '_时间戳',
      'lab-publication-v1', // profile 内部 id
      'axes_0',          // 内部对象标签
    ]) {
      expect(body, `「${gone}」不该出现在导出面板上`).not.toContain(gone)
    }
    // 规范仍在，只是只出现自然名称
    expect(body).toContain('默认规范')
  })

  it('「高级选项」拆散进三组：报告与严格核验在「检查」组、透明背景在「格式」组，开关看得见（2026-10-07 设计审计 §10.2）', async () => {
    await setup(9)
    const group = (id: string) => document.body.querySelector(`[data-export-group="${id}"]`)!
    expect([...document.body.querySelectorAll('[data-export-group]')].map((g) => g.getAttribute('data-export-group'))).toEqual([
      'output',
      'format',
      'checks',
    ])
    expect(group('checks').textContent).toContain('样式检查报告')
    expect(group('checks').querySelector('[aria-label="严格核验产物"]')).not.toBeNull()
    // 没有一个收起的「高级」把它们藏起来
    for (const d of document.body.querySelectorAll('details')) expect(d.textContent).not.toContain('样式检查报告')
    // 输出组里导出之前就看得见文件落到哪，「更改…」去设置 › 导出
    expect(group('output').querySelector('[data-export-location]')).not.toBeNull()
    await click(group('output').querySelector('[data-export-location-change]')!)
    expect(useUiStore.getState().settingsOpen).toBe(true)
    expect(useUiStore.getState().settingsSection).toBe('export')
    // 设置压在导出上（dialogStack）：收起来，别把这一层栈带进后面的用例
    await act(async () => useUiStore.getState().setSettingsOpen(false))
  })
})

describe('输出范围', () => {
  it('画布模式默认按画布；两个选项都在，不隐藏', async () => {
    await setup(9)
    const radios = scopeRadios()
    // 多图排版才有「导出内容」；默认整版（= 画布范围），「每张图单独导出」= 原图范围
    expect(radios.map((r) => r.textContent)).toEqual(['每张图单独导出', '整版'])
    expect(radios[1].getAttribute('aria-checked')).toBe('true')
  })

  it('单图编辑固定按原图，没有「输出范围」；并说出这次忽略了画布上的哪些变换', async () => {
    await setup(9)
    // 面板在画布上被缩小过 → 原图导出不套用这个缩放，界面必须说出来
    useDocumentStore.getState().commit(literal('缩小'), (d) => {
      const p0 = d.objects[0] as PanelObject
      p0.w = 40
      p0.h = 30
    })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    // 关掉再打开：`scope` 的默认值在**打开那一刻**取，两次 act 才是两次渲染
    await act(async () => {
      useUiStore.getState().setExportOpen(false)
    })
    await act(async () => {
      useUiStore.getState().setExportOpen(true)
    })
    // 单图编辑隐藏范围选择，固定 original（请求体里的 scope 由下面「单图」组钉住）
    expect(scopeRadios()).toHaveLength(0)
    expect(text()).toContain('80 × 60 mm')
    expect(text()).toContain('缩放')
  })

  it('渲染回来之后尺寸跟着变：对话框开着时 manifest 到位，说的必须是渲染回来的图幅', async () => {
    await setup(9)
    // 文档里是上一次同步到的图幅（磁盘上那份 PDF 的页面：脚本存盘时裁到了内容范围）
    useDocumentStore.getState().commit(literal('图幅'), (d) => {
      const p0 = d.objects[0] as PanelObject
      p0.nativeW = 75.26
      p0.nativeH = 58.68
    })
    // 这一变体还没画出来
    useRenderStore.setState({ byKey: {}, latest: {}, tracked: {}, building: {} })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await act(async () => {
      useUiStore.getState().setExportOpen(false)
    })
    await act(async () => {
      useUiStore.getState().setExportOpen(true)
    })
    expect(text()).toContain('75.3 × 58.7 mm')
    // 渲染回来：第 ① 档（manifest size_mm）到位——**没有任何素材清单的变化**。
    // 以前对话框只在素材清单变化时重算规格，这一刻它继续说 75.3 × 58.7，
    // 而快速编辑条上已经是 80 × 57.6（审计 T33）
    await act(async () => {
      seedExactRender(panel, { ...manifest(9), size_mm: [80, 57.6] } as never)
    })
    expect(text()).toContain('80 × 57.6 mm')
    expect(text()).not.toContain('75.3 × 58.7 mm')
  })

  // 「没有当前图就禁用」那一条被 #295 改掉了：项目里只有一张图时就按它导。
  // 判据与文案都归它，这里不再留旧标题——同一件事上留两套说法，坏的那天两边
  // 都会被当成「另一条在管」。
  it('画布模式没选中、但项目里只有一张图：就按它导，不再要求「先选中一张图」', async () => {
    await setup(9)
    const original = scopeRadios()[0]
    expect(original.hasAttribute('disabled')).toBe(false)
    expect(text()).not.toContain('先选中一张图')
    expect(text()).not.toContain('在下方选一张')
  })

  it('源文件不见了：单图档静默改按画布导，按钮亮着、预览换成页面', async () => {
    await setup(9)
    // 面板还在，素材清单里没有了（掉线 / 被删）
    useAssetStore.setState({ byId: {} } as never)
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await act(async () => {
      useUiStore.getState().setExportOpen(false)
    })
    await act(async () => {
      useUiStore.getState().setExportOpen(true)
    })
    // 单图编辑没有范围按钮；原图口径不可用时静默改按画布导（不报错、不灰按钮），左栏预览如实显示导出的页面
    expect(scopeRadios()).toHaveLength(0)
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    // 没有范围选择的单图档里，EPS 灰着的原因不能指向不存在的「每张图单独导出」
    const epsBox = formatBox('EPS')
    expect(epsBox.disabled).toBe(true)
    expect(epsBox.closest('label')!.title).toContain('没有可重新运行的脚本')
    expect(epsBox.closest('label')!.title).not.toContain('每张图单独导出')
    expect(text()).not.toContain('先选中一张图')
    expect(document.body.querySelector('[data-export-preview-stage] [data-canvas-thumb]'), '预览显示页面').toBeTruthy()
    expect(document.body.querySelector('[data-export-preview-size]')?.textContent).toContain('80 × 60 mm')
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].scope, '内部改按画布导').toBe('canvas')
    // 退路按图框裁掉页面白边：请求的页面尺寸 = 预览显示的 80 × 60 mm
    expect((exportBodies[0].canvas as { page_w_mm: number }).page_w_mm).toBe(80)
    expect((exportBodies[0].canvas as { page_h_mm: number }).page_h_mm).toBe(60)
  })
})

describe('单图退路裁白边：检查 / proof report / 请求吃同一份裁后文档', () => {
  it('页面 180 × 120、图 80 × 60 摆在 (30, 20)：请求与报告的页面都是 80 × 60，对象坐标平移到 (0, 0)', async () => {
    await setup(8, { w: 180, h: 120 }) // 8pt → error，报告必出
    useDocumentStore.getState().commit(literal('挪'), (d) => {
      d.objects = d.objects.map((o) => ({ ...o, x: 30, y: 20 }))
    })
    useAssetStore.setState({ byId: {} } as never)
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await act(async () => {
      useUiStore.getState().setExportOpen(false)
    })
    await act(async () => {
      useUiStore.getState().setExportOpen(true)
    })
    expect(document.body.querySelector('[data-export-preview-size]')?.textContent).toContain('80 × 60 mm')
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    const canvas = exportBodies[0].canvas as {
      page_w_mm: number
      page_h_mm: number
      objects: { x_mm: number; y_mm: number }[]
    }
    expect([canvas.page_w_mm, canvas.page_h_mm]).toEqual([80, 60])
    expect([canvas.objects[0].x_mm, canvas.objects[0].y_mm]).toEqual([0, 0])
    const report = exportBodies[0].style_check_report as { page_mm: { w: number; h: number } }
    expect(report.page_mm.w).toBe(80)
    expect(report.page_mm.h).toBe(60)
  })
})

describe('像素预览按范围算', () => {
  it('画布范围按页面尺寸；原图范围按那张图自己的（位图直接报源像素网格）', async () => {
    const page = { w: 180, h: 120 }
    // 画布：180mm @ 600ppi ≈ 4252px
    expect(pixelPreview('canvas', 600, page, null)).toContain('4252')
    // 原图 + 矢量源：图幅 70.6mm @ 600ppi ≈ 1668px —— **不是** 4252
    const vector = {
      widthMm: 70.6,
      heightMm: 52.9,
      pixelWidth: null,
      pixelHeight: null,
      sourceKind: 'vector',
    } as never
    const shown = pixelPreview('original', 600, page, vector)
    expect(shown).toContain('1668')
    expect(shown, '原图范围下拿画布页面尺寸算 = 报另一张图的数字').not.toContain('4252')
    // 原图 + **照抄的**位图源：源像素网格，与 ppi 无关
    // （「带 override 会被重画」那一支在 lib/exportRequest.test.ts）
    const raster = {
      widthMm: 10.16,
      heightMm: 6.77,
      pixelWidth: 120,
      pixelHeight: 80,
      sourceKind: 'raster',
    } as never
    expect(pixelPreview('original', 600, page, raster, true)).toContain('120')
    expect(pixelPreview('original', 300, page, raster, true)).toContain('120')
    // 规格还没解析出来时不报一个编出来的数
    expect(pixelPreview('original', 600, page, null)).toBe('')
  })
})

describe('对话框开着时素材没了', () => {
  it('可用性跟着素材清单变，不是只挂在 figureId 上：单图档跟着改按画布', async () => {
    await setup(9)
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await act(async () => {
      useUiStore.getState().setExportOpen(false)
    })
    await act(async () => {
      useUiStore.getState().setExportOpen(true)
    })
    expect(document.body.querySelector('[data-export-preview-stage] [data-canvas-thumb]'), '原图口径：预览是图').toBeNull()

    // 对话框开着，素材被删 / 掉线：单图档当场改按画布导（预览换成页面）；memo 只挂 figureId 的话这里还是原图
    await act(async () => {
      useAssetStore.setState({ byId: {} } as never)
    })
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    expect(document.body.querySelector('[data-export-preview-stage] [data-canvas-thumb]'), '预览跟着换成页面').toBeTruthy()
    await click(button('开始导出')!)
    expect(exportBodies[0].scope).toBe('canvas')
  })
})

describe('阻断不拦导出：无警告句 + 自动记录未处理的项', () => {
  it('有 error 时导出照样可点，没有确认勾选框也没有警告句；未处理的项自动写进样式检查报告', async () => {
    await setup(8) // 8pt = 撞绝对下限 → error
    expect(text()).toContain('要改')

    // 知情确认框整个删掉了；导出永远可点
    expect(confirmBox(), '确认勾选框已删除').toBeNull()
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    expect(text(), '不出现任何警告句').not.toContain('投稿可能被退回')
    expect(document.body.querySelector('[data-export-fix]')).toBeNull()
    expect(document.body.querySelector('[data-export-hint]')).toBeNull()
    expect(errorHint()?.textContent).toBe('1 项要改')

    await click(button('开始导出')!)

    expect(exportBodies).toHaveLength(1)
    const report = exportBodies[0].style_check_report as Record<string, unknown>
    // 有未处理项就**必须**留档：报告是「导出时未处理的项」唯一的落点，
    // 报告开关是个记住的偏好，用户可能早就关掉了
    expect(report, '有未处理项必须生成样式检查报告').toBeTruthy()
    expect((report.profile as Record<string, string>).profile_id).toBe('lab-publication-v1')
    expect(report.forced).toBe(true)
    expect(report.acknowledged).toContain('font-below-absolute-floor')
    // 请求里的检查策略也自动是 acknowledged（后端据此放行阻断项，同时把规则码记进回执）
    expect((exportBodies[0].validation as { policy: string }).policy).toBe('acknowledged')
    const checks = report.checks as { id: string; severity: string }[]
    expect(checks.some((c) => c.id === 'font-below-absolute-floor' && c.severity === 'error')).toBe(
      true,
    )
  })

  it('没有阻断项时直接可导出，默认不生成报告', async () => {
    await setup(9)
    const go = button('开始导出')!
    expect(go.hasAttribute('disabled')).toBe(false)
    await click(go)
    expect(exportBodies[0].include_style_check_report).toBe(false)
    expect(exportBodies[0].style_check_report).toBeUndefined()
  })
})

describe('未处理的项随每次导出自动记录', () => {
  it('问题集合变了，下一次导出的报告记的是现在这一批（没有确认状态可以过期）', async () => {
    await setup(8) // 8pt 刻度 → 一条阻断项
    await click(button('开始导出')!)
    const first = exportBodies[0].style_check_report as { checks: unknown[] }
    expect(first.checks).toHaveLength(1)
    await click(button('再次导出')!)
    expect(exportBodies).toHaveLength(2)

    // 文档被编辑，冒出**第二条**阻断项
    await act(async () => {
      useDocumentStore.getState().commit(literal('加一段小字'), (d) => {
        d.objects = [
          ...d.objects,
          {
            id: 't-small',
            type: 'text',
            text: '太小的说明文字',
            sizePt: 5,
            bold: false,
            italic: false,
            color: '#000000',
            align: 'left',
            x: 0,
            y: 70,
            w: 60,
            h: 6,
          } as never,
        ]
      })
      // 真实应用里这一步由 validationStore 的订阅在 250ms 防抖后跑；
      // 用例不等那 250ms，直接触发同一个入口
      runValidation()
      await new Promise<void>((r) => setTimeout(r, 0))
    })
    expect(errorHint()?.textContent).toMatch(/^[2-9] 项要改/)
    await click(button('再次导出')!)
    expect(exportBodies).toHaveLength(3)
    const second = exportBodies[2].style_check_report as { checks: unknown[]; forced: boolean }
    expect(second.forced).toBe(true)
    expect(second.checks.length, '报告里是现在这一批，不是上一次的').toBeGreaterThan(first.checks.length)
  })

  it('导出之后下一次仍然带着未处理的项（不靠任何点头状态）', async () => {
    await setup(8)
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    await click(button('再次导出')!)
    expect(exportBodies).toHaveLength(2)
    expect((exportBodies[1].style_check_report as { forced: boolean }).forced).toBe(true)
    expect((exportBodies[1].validation as { policy: string }).policy).toBe('acknowledged')
  })
})

describe('统一 ExportRequest', () => {
  it('画布导出发的是 canvas 段，没有 original 段', async () => {
    await setup(9)
    await click(button('开始导出')!)
    const body = exportBodies[0]
    expect(body.scope).toBe('canvas')
    expect(body.canvas).toBeTruthy()
    expect(body.original).toBeUndefined()
    expect(body.filename).toBe(useDocumentStore.getState().doc.name)
  })

  it('只出 PDF 时 ppi 是 null，分辨率那一行**不出现**', async () => {
    await setup(9)
    await click(formatBox('PNG'))          // 取消 PNG，只剩 PDF
    expect(ppiSelect()).toBeNull()
    await click(button('开始导出')!)
    expect(exportBodies[0].ppi).toBeNull()
    expect(exportBodies[0].formats).toEqual(['pdf'])
  })

  it('选了位图才出现分辨率，且发的是数字', async () => {
    await setup(9)
    expect(ppiSelect()).toBeTruthy()
    await click(button('开始导出')!)
    expect(exportBodies[0].ppi).toBe(600)
  })
})

describe('SVG 导出（X03）', () => {
  it('SVG-only keeps its selection (and stays exportable) after following an error count chip', async () => {
    await setup(8)
    await click(formatBox('SVG'))
    await click(formatBox('PDF'))
    await click(formatBox('PNG'))
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    const chip = document.body.querySelector<HTMLElement>('[data-export-group="checks"] [data-count-chip="danger"]')!
    expect(chip, 'the real font-floor issue must produce a blocking count chip').not.toBeNull()
    await click(chip)
    expect(useUiStore.getState().leftTab).toBe('problems')
    expect(useUiStore.getState().problemFilter).toEqual(['error'])
    expect(useUiStore.getState().exportOpen).toBe(false)
    await act(async () => { useUiStore.getState().setExportOpen(true) })
    expect(formatBox('SVG').checked).toBe(true)
    expect(formatBox('PDF').checked).toBe(false)
    expect(formatBox('PNG').checked).toBe(false)
    expect(ppiSelect()).toBeNull()
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    expect(exportBodies).toHaveLength(0)
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].formats).toEqual(['svg'])
    expect(exportBodies[0].ppi).toBeNull()
    expect((exportBodies[0].style_check_report as { acknowledged: string[] }).acknowledged).toContain('font-below-absolute-floor')
  })

  it('SVG follows the existing formats and sends no PPI', async () => {
    await setup(9)
    expect([...document.body.querySelectorAll('[data-export-formats] [data-export-format]')].map((el) => el.getAttribute('data-export-format'))).toEqual(['pdf', 'png', 'eps', 'tiff', 'svg'])
    expect(formatBox('SVG').checked).toBe(false)
    await click(formatBox('SVG'))
    await click(formatBox('PDF'))
    await click(formatBox('PNG'))
    expect(document.body.querySelector('[data-export-svg-note]')).toBeNull()
    expect(ppiSelect()).toBeNull()
    await click(button('开始导出')!)
    expect(exportBodies[0].formats).toEqual(['svg'])
    expect(exportBodies[0].ppi).toBeNull()
    expect(readExportDefaults().formats).toEqual(['svg'])
  })

  it('restores SVG-only defaults from the backend for the next opening', async () => {
    await setup(9)
    await act(async () => { useUiStore.getState().setExportOpen(false) })
    vi.mocked(fetchExportDefaultsRemote).mockResolvedValueOnce({
      defaults: { dpi: '1200', formats: ['svg'], withProof: false, strictInspection: false },
    })
    await act(async () => { await hydrateExportDefaults() })
    await act(async () => { useUiStore.getState().setExportOpen(true) })
    expect(formatBox('SVG').checked).toBe(true)
    expect(formatBox('PDF').checked).toBe(false)
    expect(ppiSelect()).toBeNull()
    await click(button('开始导出')!)
    expect(exportBodies[0].formats).toEqual(['svg'])
    expect(exportBodies[0].ppi).toBeNull()
  })
})

describe('导出默认值以后端为准（#715 PR-B）', () => {
  it('对话框常驻挂载之后才取回的后端默认值：下次打开用取回的值（换 origin 首启不退回 600 ppi）', async () => {
    await setup(9) // 本机缓存是空的：挂载时读到 600
    await act(async () => {
      useUiStore.getState().setExportOpen(false)
    })
    vi.mocked(fetchExportDefaultsRemote).mockResolvedValueOnce({
      defaults: { dpi: '1200', formats: ['pdf', 'png'], withProof: false, strictInspection: false },
    })
    await act(async () => {
      await hydrateExportDefaults()
    })
    expect(readExportDefaults().dpi).toBe('1200') // 前提：缓存已被后端那份覆盖
    await act(async () => {
      useUiStore.getState().setExportOpen(true)
    })
    await click(button('开始导出')!)
    expect(exportBodies.at(-1)!.ppi).toBe(1200)
  })
})

describe('导出默认值：对话框开着时取回的后端值', () => {
  it('开着时不当面换掉；关上之后重读，再打开用取回的值', async () => {
    await setup(9) // 开着，缓存空：600
    vi.mocked(fetchExportDefaultsRemote).mockResolvedValueOnce({
      defaults: { dpi: '1200', formats: ['pdf', 'png'], withProof: false, strictInspection: false },
    })
    await act(async () => {
      await hydrateExportDefaults()
    })
    await act(async () => {
      useUiStore.getState().setExportOpen(false)
    })
    await act(async () => {
      useUiStore.getState().setExportOpen(true)
    })
    await click(button('开始导出')!)
    expect(exportBodies.at(-1)!.ppi).toBe(1200)
  })
})

describe('文件名的跨平台校验', () => {
  it('非法字符就地报错并挡住导出，不等一次网络往返', async () => {
    await setup(9)
    const input = [...document.body.querySelectorAll('input')].find(
      (i) => i.type !== 'checkbox',
    ) as HTMLInputElement
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )!.set!
      setter.call(input, 'Fig?1')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(text()).toContain('< > : " / \\ | ? *')
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(true)
    expect(exportBodies).toHaveLength(0)
  })

  it('顺手打上的扩展名被剥掉，不会出 `.pdf.pdf`', async () => {
    await setup(9)
    const input = [...document.body.querySelectorAll('input')].find(
      (i) => i.type !== 'checkbox',
    ) as HTMLInputElement
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )!.set!
      setter.call(input, 'Fig 1.pdf')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click(button('开始导出')!)
    expect(exportBodies[0].filename).toBe('Fig 1')
  })
})

describe('撞名之后「覆盖」带着的未处理项与第一次一致', () => {
  it('撞名之后点「覆盖」：同一批阻断项照样自动记录，不会因为没有确认状态而丢', async () => {
    jobStatus = 'conflict'
    await setup(8) // 一条阻断项
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].validation).toMatchObject({ policy: 'acknowledged' })

    // 撞名那一次**什么都没写**，界面问的是同一次导出的另一个问题
    await click(button('覆盖')!)
    expect(exportBodies).toHaveLength(2)
    expect(
      (exportBodies[1].validation as Record<string, unknown>).acknowledged,
      '第二次带着空的 acknowledged = 报告里丢了这批未处理项',
    ).toEqual((exportBodies[0].validation as Record<string, unknown>).acknowledged)
    expect(exportBodies[1].include_style_check_report).toBe(true)
  })

  it('撞名之后问题集合变了：「覆盖」带的是现在这一批（报告随之变）', async () => {
    jobStatus = 'conflict'
    await setup(8)
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(button('覆盖'), '这一步得真的停在冲突条上').toBeTruthy()
    const first = (exportBodies[0].style_check_report as { checks: unknown[] }).checks.length

    await act(async () => {
      useDocumentStore.getState().commit(literal('加一段小字'), (d) => {
        d.objects = [
          ...d.objects,
          {
            id: 't-small',
            type: 'text',
            text: '太小的说明文字',
            sizePt: 5,
            bold: false,
            italic: false,
            color: '#000000',
            align: 'left',
            x: 0,
            y: 70,
            w: 60,
            h: 6,
          } as never,
        ]
      })
      runValidation()
      await new Promise<void>((r) => setTimeout(r, 0))
    })

    await click(button('覆盖')!)
    expect(exportBodies).toHaveLength(2)
    expect(
      (exportBodies[1].style_check_report as { checks: unknown[] }).checks.length,
      '「覆盖」走的是 start()，报告取的是现在这一批',
    ).toBeGreaterThan(first)
  })
})

/**
 * QA 2026-09-24 FLAG-B1：一张比画布页还宽的图（x < 0）按「原图尺寸」导出，
 * 检查范围写着「仅此图」，却被「超出页面范围」阻断、报告里写着画布的 page_mm。
 * 原图范围的产物页面 = 图幅，画布怎么摆不进这次导出（SCI-06）。
 * 画布范围那半边是**前提**：同一份文档按画布导出时这条阻断必须还在。
 */
describe('原图范围不被画布摆放阻断（FLAG-B1）', () => {
  const reopen = async () => {
    await act(async () => {
      useUiStore.getState().setExportOpen(false)
    })
    await act(async () => {
      useUiStore.getState().setExportOpen(true)
    })
  }
  const offPage = async (tickPt: number) => {
    await setup(tickPt, { w: 150, h: 100 })
    useDocumentStore.getState().commit(literal('摆到页外'), (d) => {
      ;(d.objects[0] as PanelObject).x = -20
    })
  }

  it('画布范围仍是阻断；原图范围没有阻断，直接可导', async () => {
    await offPage(9)
    await reopen()
    expect(errorHint(), '前提：画布范围下它是阻断').toBeTruthy()

    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await reopen()
    expect(scopeRadios(), '单图编辑固定按原图，没有范围可选').toHaveLength(0)
    expect(anyHint()).toBeNull()
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].scope).toBe('original')
  })

  it('样式检查报告：画布范围认画布页与 out-of-page；原图范围只认这张图的问题与图幅', async () => {
    await offPage(8) // 8 pt 撞绝对下限：让原图范围也有一条要确认的阻断，报告才会生成
    await reopen()
    await click(button('开始导出')!)
    const canvasReport = exportBodies[0].style_check_report as Record<string, unknown>
    expect(canvasReport.acknowledged).toContain('out-of-page')
    expect(canvasReport.page_mm).toEqual({ w: 150, h: 100, margin: 0 })

    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await reopen()
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(2)
    expect(exportBodies[1].scope).toBe('original')
    const report = exportBodies[1].style_check_report as Record<string, unknown>
    expect(report.acknowledged).toEqual(['font-below-absolute-floor'])
    expect((report.checks as { id: string }[]).map((c) => c.id)).not.toContain('out-of-page')
    expect(report.page_mm).toEqual({ w: 80, h: 60, margin: 0 })
    expect((report.objects as { rect_mm: number[] }[]).map((o) => o.rect_mm)).toEqual([
      [0, 0, 80, 60],
    ])
  })

  /**
   * 画布上的**缩放**同样不进原图导出（ADR 0028）：9 pt 的图在画布上缩到一半，读者在那页上
   * 量到 4.5 pt——按画布导出这是阻断；按原图导出，文件里仍是 9 pt，不许被这次缩放阻断，
   * 报告里也不许出现这条。画布范围那半边是前提（否则「不阻断」恒成立）。
   */
  it('画布上缩小摆放的图：画布范围按缩放后的字号阻断，原图范围按原图字号放行', async () => {
    writeExportDefaults({ withProof: true })
    await setup(9, { w: 80, h: 60 })
    useDocumentStore.getState().commit(literal('缩到一半'), (d) => {
      ;(d.objects[0] as PanelObject).w = 40
      ;(d.objects[0] as PanelObject).h = 30
    })
    await reopen()
    expect(errorHint(), '前提：画布范围下 4.5 pt 是阻断').toBeTruthy()
    expect(errorHint()).toBeTruthy()

    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await reopen()
    expect(scopeRadios(), '单图编辑固定按原图，没有范围可选').toHaveLength(0)
    expect(anyHint(), '原图里是 9 pt，没有要确认的阻断').toBeNull()
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].scope).toBe('original')
    const report = exportBodies[0].style_check_report as Record<string, unknown>
    expect(report, '夹具开了报告').toBeTruthy()
    expect((report.checks as { id: string }[]).map((c) => c.id).filter((id) => id.startsWith('font-'))).toEqual([])
    expect(report.page_mm).toEqual({ w: 80, h: 60, margin: 0 })
  })

  /**
   * 那张图**不在当前画布上**（Codex 评审 #596 P2）：`findFigurePanel()` 会跨画布找到它，
   * 检查与报告就都得按**它所在的那张画布**算——拿当前画布去裁，摘要里一条都剩不下
   * （8 pt 的阻断被说成没问题），报告的 `objects` 也是空的。
   */
  it('图在另一张画布上：检查按它所在的画布算，报告里有这张图', async () => {
    await offPage(8)
    await act(async () => {
      useDocumentStore.getState().addCanvas('空白')
    })
    const home = useDocumentStore.getState().canvases.find((c) =>
      c.objects.some((o) => o.id === 'p1'),
    )
    expect(home, '前提：p1 在另一张画布上').toBeTruthy()
    expect(useDocumentStore.getState().activeCanvasId).not.toBe(home!.id)
    // 别的画布上的图不在多图排版的「每张图」里（只认当前画布）；这条检查口径只剩单图编辑一条路到得了
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await reopen()
    expect(scopeRadios(), '单图编辑固定原图').toHaveLength(0)
    // 这张图自己的阻断（8 pt）必须在；画布摆放那条不算
    expect(errorHint(), '8 pt 的阻断被裁没了').toBeTruthy()
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    const report = exportBodies[0].style_check_report as Record<string, unknown>
    expect(report.acknowledged).toEqual(['font-below-absolute-floor'])
    expect((report.objects as { name: string; rect_mm: number[] }[])).toEqual([
      expect.objectContaining({ name: 'Fig1.pdf', rect_mm: [0, 0, 80, 60] }),
    ])
  })

  /**
   * 两张画布绑的规范不同（Codex 评审 #596 P1）：图所在的画布是宽松的 free-form
   * （8 pt 合规），当前画布是 lab-publication（8 pt 是阻断）。摘要按图所在画布的规范算
   * 出「干净」，那么报告的规范戳、严格核验的 `profile_id` 也必须是那一套——各取各的话，
   * 报告会声称这张 8 pt 的图是按当前画布那套严规范查过并通过的。
   * 对照：切回画布范围，四处一起换成当前画布的规范（判据不是恒等成立）。
   */
  it('图在另一张画布上且两张画布规范不同：检查、报告、严格核验都按图所在画布的规范', async () => {
    const entry = (id: string) => {
      const e = toCatalog(useProfileStore.getState().specs).find((x) => x.id === id)
      expect(e, `夹具里没有 ${id}`).toBeTruthy()
      return e!
    }
    writeExportDefaults({ withProof: true, strictInspection: true })
    await setup(8)
    useDocumentStore.getState().commit(literal('图所在画布：宽松规范'), (d) => {
      d.profile = bindingFor(entry('free-form-v1'))
    })
    await act(async () => {
      useDocumentStore.getState().addCanvas('严规范')
    })
    useDocumentStore.getState().commit(literal('当前画布：严规范'), (d) => {
      d.profile = bindingFor(entry('lab-publication-v1'))
    })
    const s = useDocumentStore.getState()
    const home = s.canvases.find((c) => c.objects.some((o) => o.id === 'p1'))
    expect(home?.profile?.id, '前提：p1 所在画布绑的是 free-form').toBe('free-form-v1')
    expect(s.activeCanvasId, '前提：p1 不在当前画布上').not.toBe(home!.id)
    expect(s.doc.profile?.id, '前提：当前画布绑的是 lab-publication').toBe('lab-publication-v1')

    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await reopen()
    expect(scopeRadios(), '单图编辑固定原图').toHaveLength(0)
    // 按 free-form 算：8 pt 合规，没有要确认的东西
    expect(anyHint(), '8 pt 在图所在画布的规范下不是阻断').toBeNull()
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    expect(
      document.body.querySelector('[data-export-profile-canvas]'),
      '规范下拉显示的是别的画布的规范，得说出来',
    ).toBeTruthy()
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].scope).toBe('original')
    const report = exportBodies[0].style_check_report as Record<string, unknown>
    expect(report, '夹具开了报告').toBeTruthy()
    expect((report.profile as Record<string, string>).profile_id).toBe('free-form-v1')
    expect(report.checks).toEqual([])
    expect(exportBodies[0].inspection).toEqual({ mode: 'strict', profile_id: 'free-form-v1' })

    // 对照：回到多图排版（默认整版 = 当前画布），规范跟着换成 lab-publication
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
    await reopen()
    expect(document.body.querySelector('[data-export-profile-canvas]')).toBeNull()
    // 上一次已经导完：新的一次是显式的「再次导出」
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(2)
    expect(exportBodies[1].scope).toBe('canvas')
    const canvasReport = exportBodies[1].style_check_report as Record<string, unknown>
    expect((canvasReport.profile as Record<string, string>).profile_id).toBe('lab-publication-v1')
    expect(exportBodies[1].inspection).toEqual({ mode: 'strict', profile_id: 'lab-publication-v1' })
  })
})

describe('「能不能导」只有一份判断', () => {
  it('原图不可用时，「每张图单独导出」那一张发不出请求，如实记失败（单图档则改按画布导，见上）', async () => {
    await setup(9)
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
    await act(async () => useUiStore.getState().setExportOpen(false))
    await act(async () => useUiStore.getState().setExportOpen(true))
    await click(originalRadio())
    useAssetStore.setState({ byId: {} } as never)
    await click(button('开始导出')!)
    await batchDone()
    expect(
      exportBodies,
      '咽喉闸少了「原图可不可用」这一条 = 起一个界面刚说不可用的导出',
    ).toHaveLength(0)
    const b = useExportStore.getState().batch!
    expect(b.failedStarts).toHaveLength(1)
    expect(b.failedStarts[0].reason).not.toBe('')
  })
})

describe('照抄源位图 vs 引擎重画', () => {
  it('带 override 的位图面板不许报源像素网格（它会被重画）', async () => {
    await setupRaster([])
    expect(text(), '照抄源文件时报的就是源像素网格').toContain('120 × 80')

    await setupRaster([{ gid: 'axes_0', prop: 'fontsize', value: 9 }], 's.py')
    expect(
      text(),
      '带 override 且有脚本 = 引擎重画，拿到的是 PDF；再报源像素网格就是界面与文件各说各的',
    ).not.toContain('120 × 80')

    // 没有脚本就画不出：后端 `_resolve_panel_source` 退回磁盘文件，文件仍是源像素网格
    await setupRaster([{ gid: 'axes_0', prop: 'fontsize', value: 9 }])
    expect(text(), '带 override 但没有脚本：后端退回磁盘原件').toContain('120 × 80')
  })
})

describe('来源读后端 route（#1209）', () => {
  const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 400)) })
  const panelRoute = (over: Record<string, unknown>) => ({
    source: 'disk',
    embeds_disk_bitmap: true,
    formats: {},
    offers: { eps: { how: 'unavailable', reason: 'eps_needs_script' } },
    ...over,
  })
  afterEach(() => { routeOf = null })

  it('route 说这次会被重画：像素预览不再报源像素网格（即使前端旧判据会说照抄）', async () => {
    routeOf = (req) => ({
      scope: req.scope,
      formats: req.formats,
      panels: { 'r1.png': panelRoute({ source: 'redraw', embeds_disk_bitmap: false }) },
    })
    await setupRaster([])
    expect(text(), '还没拿到 route：旧判据（无编辑 = 照抄）').toContain('120 × 80')
    await settle()
    expect(text(), 'route 说重画，前端不再自己推算').not.toContain('120 × 80')
  })

  it('EPS 可不可用读 route.offers，不看前端素材表里有没有 script', async () => {
    routeOf = (req) => ({
      scope: req.scope,
      formats: req.formats,
      panels: { 'r1.png': panelRoute({ offers: { eps: { how: 'redraw' } } }) },
    })
    await setupRaster([]) // 素材表里没有 script：旧判据 = 不可用
    const epsDisabled = () =>
      document.body.querySelector('[data-export-format="eps"]')?.getAttribute('aria-disabled')
    expect(epsDisabled(), '还没拿到 route：旧判据').toBe('true')
    await settle()
    expect(epsDisabled(), 'route.offers.eps 可用').toBeNull()
  })

  it('画布范围：route 说某个带编辑的图画不出来，点导出之前就说', async () => {
    routeOf = (req) => ({
      scope: req.scope,
      formats: req.formats,
      panels: {
        'Fig1.pdf': panelRoute({
          source: 'unavailable',
          embeds_disk_bitmap: false,
          formats: { pdf: { how: 'unavailable', reason: 'source_needs_execution' } },
        }),
      },
    })
    await setup(9)
    await settle()
    expect(document.body.querySelector('[data-export-route-unavailable]')?.textContent).toContain('Fig1.pdf')
  })

  it('重开对话框：新 route 到之前读到的是「没有 route」，不拿旧 offers 禁用 / 放开 EPS', async () => {
    routeOf = (req) => ({
      scope: req.scope,
      formats: req.formats,
      panels: { 'r1.png': panelRoute({ offers: { eps: { how: 'redraw' } } }) },
    })
    await setupRaster([]) // 素材表里没有 script：退路 = EPS 不可用
    const epsDisabled = () =>
      document.body.querySelector('[data-export-format="eps"]')?.getAttribute('aria-disabled')
    await settle()
    expect(epsDisabled(), 'route 到了：可用').toBeNull()
    await act(async () => { useUiStore.getState().setExportOpen(false) })
    await act(async () => { useUiStore.getState().setExportOpen(true) })
    expect(epsDisabled(), '重开后、响应到达前：退路，而不是关着时的旧答案').toBe('true')
    await settle()
    expect(epsDisabled(), '新响应到达').toBeNull()
  })

  it('对话框关着不取 route；每次重新打开都重取，哪怕输入没变', async () => {
    let validates = 0
    routeOf = (req) => {
      validates += 1
      return { scope: req.scope, formats: req.formats, panels: {} }
    }
    await setup(9)
    await settle()
    const opened = validates
    expect(opened, '打开后取了 route').toBeGreaterThan(0)

    await act(async () => { useUiStore.getState().setExportOpen(false) })
    // 关着时编辑文档：不发 validate
    await act(async () => {
      useDocumentStore.getState().commit(literal('编辑'), (d) => { d.objects = [{ ...d.objects[0], h: 33 } as never] })
    })
    await settle()
    expect(validates, '关着不发请求').toBe(opened)

    await act(async () => { useUiStore.getState().setExportOpen(true) })
    await settle()
    expect(validates, '重新打开必须重取').toBeGreaterThan(opened)

    const again = validates
    await act(async () => { useUiStore.getState().setExportOpen(false) })
    await act(async () => { useUiStore.getState().setExportOpen(true) })
    await settle()
    expect(validates, '输入没变的重开也重取（脚本登记可能变了）').toBeGreaterThan(again)
  })

  it('route 缺席（旧后端）：没有那条提示，行为与引入 route 前一致', async () => {
    routeOf = null
    await setup(9)
    await settle()
    expect(document.body.querySelector('[data-export-route-unavailable]')).toBeNull()
  })
})

describe('产物核验（ADR 0068）：未核验不显示绿', () => {
  const output = (manifest: Record<string, unknown> | null | undefined) => ({
    format: 'pdf',
    name: 'a.pdf',
    url: '/exports/a.pdf',
    bytes: 1234,
    dimensions: { px: null, mm: [80, 60] },
    vector: true,
    status: 'done',
    replaced: false,
    error: null,
    ...(manifest === undefined ? {} : { manifest }),
  })
  const manifest = (checks: Record<string, string>) => ({
    manifest_version: 1,
    format: 'pdf',
    sha256: 'x',
    bytes: 1234,
    policy: 'standard',
    verdict: 'accepted',
    required: ['integrity', 'size'],
    checks,
    notes: [],
  })
  const badge = () => document.body.querySelector<HTMLElement>('[data-inspection]')

  it('回执里没有 manifest（老服务端 / 检查器没跑）：画「未核验」，没有绿勾', async () => {
    await setup(9)
    await click(button('开始导出')!)
    await openResultDetail()
    expect(badge()?.dataset.inspection).toBe('unknown')
    expect(badge()?.textContent).toBe('未核验')
    expect(document.body.querySelector('[data-inspection="verified"]')).toBeNull()
  })

  it('全部可判项 verified 才画绿；not_applicable 不算', async () => {
    jobOutputs = [
      output(manifest({ integrity: 'verified', size: 'verified', text_layer: 'not_applicable' })),
    ]
    await setup(9)
    await click(button('开始导出')!)
    await openResultDetail()
    expect(badge()?.dataset.inspection).toBe('verified')
    expect(badge()?.textContent).toContain('已核验')
    expect(badge()?.className).toContain('text-ok')
  })

  it('有一项 unknown 就不画绿，按名字列出未核验的项', async () => {
    jobOutputs = [
      output(manifest({ integrity: 'verified', size: 'verified', clipping: 'unknown' })),
    ]
    await setup(9)
    await click(button('开始导出')!)
    await openResultDetail()
    expect(badge()?.dataset.inspection).toBe('unknown')
    expect(badge()?.textContent).toBe('未核验：裁切')
    expect(badge()?.className).not.toContain('text-ok')
  })

  it('可选项 failed：文件已交付，但红着说出是哪一项', async () => {
    jobOutputs = [
      output(manifest({ integrity: 'verified', size: 'verified', dpi_tag: 'failed', raster_density: 'unknown' })),
    ]
    await setup(9)
    await click(button('开始导出')!)
    await openResultDetail()
    expect(badge()?.dataset.inspection).toBe('failed')
    expect(badge()?.textContent).toBe('核验未通过：分辨率标签')
    expect(badge()?.className).toContain('text-danger')
  })

  it('严格核验默认不勾（请求里没有 inspection 键）；勾上后带 strict 与当前规范', async () => {
    await setup(9)
    await click(button('开始导出')!)
    expect('inspection' in exportBodies[0]).toBe(false)
    const toggle = document.body.querySelector<HTMLElement>('[aria-label="严格核验产物"]')
    expect(toggle, '高级选项里缺少严格核验开关').toBeTruthy()
    await click(toggle!)
    await click(document.body.querySelector<HTMLButtonElement>('[data-export-again]')!)
    expect(exportBodies[1].inspection).toEqual({
      mode: 'strict',
      profile_id: useDocumentStore.getState().doc.profile?.id ?? readExportDefaults().profileId,
    })
  })
})

describe('已有同名文件', () => {
  it('默认先问一句，给出「覆盖」与「另存一份」两条明确出路', async () => {
    jobStatus = 'conflict'
    await setup(9)
    await click(button('开始导出')!)
    expect(exportBodies[0].overwrite).toBe('ask')
    expect(text()).toContain('已经有 a.pdf')
    await click(button('覆盖')!)
    expect(exportBodies[1].overwrite).toBe('replace')
    await click(button('另存一份')!)
    expect(exportBodies[2].overwrite).toBe('rename')
  })
})

describe('检查摘要只给数量，完整清单在问题面板', () => {
  it('摘要里有计数与入口，不列第二套清单', async () => {
    // 80×40 的页面 + 同尺寸面板：只有 page-aspect 这一条 warn
    await setup(9, { w: 80, h: 40 }, 40)
    expect(text()).toContain('1 项留意')
    expect(text()).not.toContain('页面比例')   // 明细归问题面板
    const open = button('在问题面板中查看')!
    await click(open)
    expect(useUiStore.getState().leftTab).toBe('problems')
    expect(useUiStore.getState().exportOpen).toBe(false)
  })
})

describe('预检的匿名用量统计', () => {
  it('只发计数，不发任何一条检查项的文字 / 字体名 / 对象 id', async () => {
    const posted = vi.mocked(postTelemetryEvent)
    posted.mockClear()
    setTelemetryEnabled(true)
    try {
      await setup(8)                       // 8pt 刻度：撞绝对下限 → 1 条 error
      const calls = posted.mock.calls.filter(([event]) => event === 'preflight_completed')
      expect(calls).toHaveLength(1)
      const props = calls[0][1] as Record<string, unknown>
      expect(Object.keys(props).sort()).toEqual([
        'errors', 'not_verifiable', 'passed', 'suggestions', 'warnings',
      ])
      expect(props.errors).toBeGreaterThan(0)
      expect(props.passed).toBe(false)
      for (const v of Object.values(props)) {
        expect(typeof v === 'number' || typeof v === 'boolean').toBe(true)
      }
      // 面板里真实存在的那些文字一个都不能出现在载荷里
      const blob = JSON.stringify(props)
      for (const leaked of ['Fig1', '.pdf', 'axes_0', 'x 刻度', '字号']) {
        expect(blob).not.toContain(leaked)
      }
    } finally {
      setTelemetryEnabled(false)
    }
  })

  it('没同意时一条都不发', async () => {
    const posted = vi.mocked(postTelemetryEvent)
    posted.mockClear()
    setTelemetryEnabled(false)
    await setup(9)
    expect(posted).not.toHaveBeenCalled()
  })
})

describe('对话框里改规范，不许连带重置用户填过的东西', () => {
  const filenameInput = () =>
    [...document.body.querySelectorAll('input')].find(
      (i) => i.type !== 'checkbox',
    ) as HTMLInputElement

  const type = async (value: string) => {
    const input = filenameInput()
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )!.set!
      setter.call(input, value)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }

  /** 选一套规范 = `applyProfile()` 提交一个新的 `d.profile`（对象身份变了） */
  const pickProfile = async () => {
    const entry = toCatalog(useProfileStore.getState().specs)[0]
    expect(entry, '夹具里没有可选的规范，这条用例什么都量不到').toBeTruthy()
    await act(async () => {
      useDocumentStore.getState().commit(literal('换规范'), (d) => {
        d.profile = bindingFor(entry)
      })
    })
  }

  it('挑一套规范之后，用户敲进去的导出名还在', async () => {
    await setup(9)
    await type('我的图名')
    expect(filenameInput().value).toBe('我的图名')
    await pickProfile()
    expect(filenameInput().value, '选规范把导出名冲回了文档名').toBe('我的图名')
  })

  it('**换文档仍然重置**——修的是"选规范时别重跑"，不是"再也不重置"', async () => {
    await setup(9)
    await type('我的图名')
    await act(async () => {
      await useDocumentStore.getState().switchDocument(emptyProject(), 'd_another')
    })
    expect(filenameInput().value, '换了文档还留着上一份的导出名').not.toBe('我的图名')
  })
})

/* ======================= 审计 T33 / T35（2026-09-06） ======================= */

import { SettingsDialog } from '@/components/SettingsDialog'
import { dialogCovered } from '@/store/uiStore'

/** 几条 8pt 刻度：每条一个阻断项（`font-below-absolute-floor`） */
const manifestWithTicks = (n: number, pt = 8, sizeMm: [number, number] = [80, 60]) => ({
  stem: 'Fig1',
  size_mm: sizeMm,
  elements: Array.from({ length: n }, (_, i) => ({
    gid: `axes_${i}.xticks`,
    role: 'ticks',
    label: `x 刻度 ${i + 1}`,
    bbox: [0.1, 0.9, 0.8, 0.05],
    draggable: false,
    editable: [
      { prop: 'fontsize', type: 'number', value: pt },
      { prop: 'direction', type: 'enum', value: 'in' },
    ],
  })),
})

/** 摆好文档 + 渲染态 + 素材清单，**不**打开对话框（各用例自己决定何时开、以什么模式开） */
async function stage(opts: {
  panels: PanelObject[]
  renders: Record<string, unknown>
  page?: { w: number; h: number }
  docId?: string
  /** 素材清单里的磁盘事实（矢量源的页面尺寸等）；按 fileId 给 */
  assetSpecs?: Record<string, Record<string, unknown>>
}) {
  await useDocumentStore.getState().switchDocument(emptyProject(), opts.docId ?? 'd_audit')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = opts.page ?? { w: 80, h: 60 }
    d.objects = opts.panels.map((p) => ({ ...p }))
  })
  useAssetStore.setState({
    byId: Object.fromEntries(
      opts.panels.map((p) => [
        p.fileId,
        { id: p.fileId, mtime: 1, ...(opts.assetSpecs?.[p.fileId] ? { original_spec: opts.assetSpecs[p.fileId] } : {}) },
      ]),
    ),
  } as never)
  const byKey: Record<string, unknown> = {}
  const latest: Record<string, string> = {}
  for (const p of opts.panels) {
    const key = renderKey(p.fileId, p.overrides)
    latest[p.fileId] = key
    byKey[key] = {
      fileId: p.fileId,
      rev: 1,
      manifest: opts.renders[p.fileId],
      svg: null,
      status: 'ready',
      error: null,
      code: '',
      module: '',
      traceback: '',
      warnings: [],
      timings: {},
      stale: false,
      lastPatches: '[]',
      wantPatches: '[]',
      previewDpi: null,
    }
  }
  useRenderStore.setState({ byKey, latest, tracked: {}, building: {} } as never)
}

/* ------------------------- 用户反馈 06：原图导出的对象 ------------------------- */

/** 两张图都在画布上（p1 → Fig1.pdf，p2 → Fig2.pdf），素材清单都认识它们 */
async function setupTwo(opts: { select?: string[]; open?: boolean } = {}) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_two')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    // 页面按默认规范的双栏宽 150 mm、4:3——夹具本身不能带阻断项，否则每条
    // 用例都得先去点确认框，测的就不再是"选对了图"这一件事
    d.page = { w: 150, h: 112.5 }
    d.objects = [
      { ...panel },
      { ...panel, id: 'p2', fileId: 'Fig2.pdf', nativeW: 65, nativeH: 50, w: 65, h: 50, x: 82 },
    ]
  })
  useAssetStore.setState({
    byId: {
      'Fig1.pdf': { id: 'Fig1.pdf', kind: 'pdf', mtime: 1 },
      'Fig2.pdf': { id: 'Fig2.pdf', kind: 'pdf', mtime: 1 },
    },
    panels: [
      { id: 'Fig1.pdf', kind: 'pdf', mtime: 1 },
      { id: 'Fig2.pdf', kind: 'pdf', mtime: 1 },
    ],
  } as never)
  const k1 = renderKey('Fig1.pdf', [])
  const k2 = renderKey('Fig2.pdf', [])
  const entry = (fileId: string, size: [number, number]) => ({
    fileId,
    rev: 1,
    manifest: { ...manifest(9), size_mm: size },
    svg: null,
    status: 'ready',
    error: null,
    code: '',
    module: '',
    traceback: '',
    warnings: [],
    timings: {},
    stale: false,
    lastPatches: '[]',
    wantPatches: '[]',
    previewDpi: null,
  })
  useRenderStore.setState({
    byKey: { [k1]: entry('Fig1.pdf', [80, 60]), [k2]: entry('Fig2.pdf', [65, 50]) } as never,
    latest: { 'Fig1.pdf': k1, 'Fig2.pdf': k2 },
    tracked: {},
    building: {},
  })
  useSelectionStore.getState().set(opts.select ?? [])
  useUiStore.getState().setExportOpen(opts.open ?? true)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ExportDialog />
      </TooltipProvider>,
    )
  })
}

/** 只挂导出对话框；`withSettings` 时把设置对话框一起挂上（审计 T35 的遮挡判据） */
async function mountDialogs(withSettings = false) {
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ExportDialog />
        {withSettings && <SettingsDialog />}
      </TooltipProvider>,
    )
  })
}

const openDialog = async () => {
  await act(async () => {
    useUiStore.getState().setExportOpen(true)
  })
}

const filenameInput = () =>
  [...document.body.querySelectorAll('input')].find((i) => i.type !== 'checkbox') as HTMLInputElement

const radios = () => scopeRadios()

const typeFilename = async (value: string) => {
  const input = filenameInput()
  await act(async () => {
    // React 受控输入：走原生 setter 再派发 input，onChange 才会跑
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

describe('T33 · 默认文件名跟着导出对象走', () => {
  it('单图编辑里默认是那张图的名字，不是画布名；多图排版里整版 / 每张图各归各', async () => {
    await stage({ panels: [panel], renders: { 'Fig1.pdf': manifestWithTicks(1, 9) } })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await mountDialogs()
    await openDialog()
    expect(filenameInput().value, '面板没起名就用文件 stem').toBe('Fig1')
    expect(radios(), '单图编辑没有范围可切').toHaveLength(0)
    // 回到多图排版：默认整版，名字是画布名；切到「每张图单独导出」换成图名，再切回来各归各
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
    await act(async () => useUiStore.getState().setExportOpen(false))
    await openDialog()
    expect(filenameInput().value).toBe(useDocumentStore.getState().doc.name)
    await click(radios()[0])
    // 「每张图单独导出」每张用各自的名字：文件名输入隐藏，换成一句说明；对象头报的是这张图
    expect(filenameInput(), '每张图用各自的名字，没有统一文件名输入').toBeUndefined()
    expect(document.body.querySelector('[data-export-batch-names]')).toBeTruthy()
    await click(radios()[1])
    expect(filenameInput().value).toBe(useDocumentStore.getState().doc.name)
  })

  it('面板起过名就用面板名', async () => {
    await stage({
      panels: [{ ...panel, name: 'Fig1_kinetics' }],
      renders: { 'Fig1.pdf': manifestWithTicks(1, 9) },
    })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await mountDialogs()
    await openDialog()
    expect(filenameInput().value).toBe('Fig1_kinetics')
  })

  it('用户改过名字之后切范围**不再替他换**（按标志判，不比字符串）', async () => {
    await stage({ panels: [panel], renders: { 'Fig1.pdf': manifestWithTicks(1, 9) } })
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
    await mountDialogs()
    await openDialog()
    await typeFilename('mine')
    await click(radios()[0])
    await click(radios()[1])
    expect(filenameInput().value).toBe('mine')
    // 恰好敲回默认值也算改过：切范围仍然不动它
    await typeFilename('Fig1')
    await click(radios()[0])
    await click(radios()[1])
    expect(filenameInput().value).toBe('Fig1')
  })
})

describe('T33 · 导出对象头部：名字 · 范围 · 尺寸只出现一次', () => {
  const header = () => document.body.querySelector('[data-export-target]')?.textContent ?? ''

  const vectorOnDisk = (w: number, h: number) => ({
    source_kind: 'vector',
    logical_w_mm: w,
    logical_h_mm: h,
    px_w: null,
    px_h: null,
    dpi: null,
    dpi_source: 'unknown',
    viewport_pt: [(w / 25.4) * 72, (h / 25.4) * 72],
    transparent: false,
  })

  it('原图：图名 + 图幅；磁盘原件与图幅一致时不多说', async () => {
    await stage({
      panels: [panel],
      renders: { 'Fig1.pdf': manifestWithTicks(1, 9, [80, 60]) },
      assetSpecs: { 'Fig1.pdf': vectorOnDisk(80, 60) },
    })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await mountDialogs()
    await openDialog()
    expect(header()).toContain('Fig1')
    expect(header(), '范围词不在一行头部里复述').not.toContain('原图尺寸')
    expect(header()).toContain('80 × 60 mm')
    expect(text()).not.toContain('磁盘上的原件')
    // 缩略图只在左栏大预览里（对象头不再重复一份小的）
    expect(document.body.querySelector('[data-export-target] img')).toBeNull()
    expect(document.body.querySelector('[data-export-preview-stage] img')).toBeTruthy()
  })

  it('磁盘原件被脚本裁过（bbox_inches=tight）：按图幅出图，并说出磁盘那份是多少（审计里 80×57.6 vs 75.3×58.7）', async () => {
    // 教程 Fig1_kinetics 的真实数字：磁盘 PDF 75.26 × 58.68，figsize 80 × 57.6
    await stage({
      panels: [{ ...panel, nativeW: 80, nativeH: 57.6, h: 57.6 }],
      renders: { 'Fig1.pdf': manifestWithTicks(1, 9, [80, 57.6]) },
      assetSpecs: { 'Fig1.pdf': vectorOnDisk(75.26, 58.68) },
    })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await mountDialogs()
    await openDialog()
    expect(header()).toContain('80 × 57.6 mm')
    expect(header()).toContain('磁盘上的原文件为 75.3 × 58.7 mm')
    expect(header()).toContain('将按图幅 80 × 57.6 mm 导出')
    // 同一份 spec 只在三处出现：对象头、左栏预览、磁盘原件那句「将按图幅 … 导出」
    expect(text().split('80 × 57.6 mm').length - 1).toBe(3)
  })

  it('位图源没有「裁过」这回事：尺寸不一致也不说那句', async () => {
    await stage({
      panels: [{ ...panel, nativeW: 80, nativeH: 57.6, h: 57.6 }],
      renders: { 'Fig1.pdf': manifestWithTicks(1, 9, [80, 57.6]) },
      assetSpecs: { 'Fig1.pdf': { ...vectorOnDisk(75.26, 58.68), source_kind: 'raster', px_w: 890, px_h: 693 } },
    })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await mountDialogs()
    await openDialog()
    expect(header()).toContain('80 × 57.6 mm')
    expect(text()).not.toContain('磁盘上的原件')
  })

  it('画布：画布名 + 页面尺寸，缩略图是画布列表同一张（真实内容，不发渲染）', async () => {
    await stage({ panels: [panel], renders: { 'Fig1.pdf': manifestWithTicks(1, 9) }, page: { w: 180, h: 120 } })
    await mountDialogs()
    await openDialog()
    expect(header()).toContain(useDocumentStore.getState().doc.name)
    expect(header()).toContain('180 × 120 mm')
    expect(header(), '精简成一行：名字 · 尺寸').not.toContain('1 个对象')
    // 与画布列表 / 版本列表同一个组件（在左栏大预览里）：面板落位处挂的是素材预览图，不是灰方块
    const thumb = document.body.querySelector('[data-export-preview-stage] [data-canvas-thumb]')
    expect(thumb).toBeTruthy()
    expect(thumb!.querySelector('[data-thumb-panel="Fig1.pdf"]')).toBeTruthy()
    expect(document.body.querySelector('[data-export-target] [data-canvas-thumb]')).toBeNull()
  })
})

describe('P02 · 检查的是哪份产物（对象头不再复述文件名清单）', () => {
  const checkScope = () => document.body.querySelector('[data-export-check-scope]')?.textContent ?? ''

  async function openFast() {
    await stage({ panels: [panel], renders: { 'Fig1.pdf': manifestWithTicks(1, 9) } })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await mountDialogs()
    await openDialog()
  }

  it('对象头没有文件名清单（右栏文件名输入框 + 格式勾选已经说了）', async () => {
    await openFast()
    expect(document.body.querySelector('[data-export-target-files]')).toBeNull()
    expect(document.body.querySelector('[data-export-target]')?.textContent).not.toContain('.png')
  })

  it('仅 PDF：没有 DPI 一行；加上位图才出现', async () => {
    await openFast()
    await click(formatBox('PNG'))
    expect(ppiSelect()).toBeNull()
    await click(formatBox('TIFF'))
    expect(ppiSelect()).toBeTruthy()
  })

  it('原图 ↔ 画布：检查对象各归各，不串', async () => {
    await openFast()
    // 多图排版里切「每张图单独导出」才是原图范围（单图编辑固定原图、没有画布可切）
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
    await act(async () => useUiStore.getState().setExportOpen(false))
    await openDialog()
    await click(radios()[0])
    const canvasName = useDocumentStore.getState().doc.name
    expect(checkScope()).toContain('仅查原图「Fig1」')
    expect(checkScope()).not.toContain(canvasName)
    await click(radios()[1])
    expect(checkScope()).toContain(`查整个画布「${canvasName}」`)
    // 尺寸仍只在头部报一次，检查行不报数
    expect(checkScope()).not.toMatch(/mm/)
  })

  it('原图不可用（源文件不见了）：单图档静默改按画布导（头部是画布名 · 尺寸）', async () => {
    await stage({ panels: [panel], renders: { 'Fig1.pdf': manifestWithTicks(1, 9) } })
    useAssetStore.setState({ byId: {} } as never)
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await mountDialogs()
    await openDialog()
    expect(radios(), '单图编辑没有范围可选').toHaveLength(0)
    // 原图口径不可用：静默改按画布导；头部仍点名将写出的文件（默认名是这张图的名字，如「spectrum」，不是画布名），
    // 范围句如实说的是画布（不再是「没有可导出的文件名」）
    expect(document.body.querySelector('[data-export-target]')?.textContent).toContain(useDocumentStore.getState().doc.name)
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
  })
})

describe('T33 · 检查摘要按导出目标取范围', () => {
  const second: PanelObject = { ...panel, id: 'p2', fileId: 'Fig2.pdf', x: 0, y: 60, script: 'fig2.py' }

  it('按原图：别的图的阻断项与页面级警告都不算进来；按画布：都算', async () => {
    // 80×40 的页面：page-aspect 一条 warn（页面级）；Fig2 上 8pt：一条 error（别的图）
    await stage({
      panels: [{ ...panel, h: 40 }, second],
      renders: { 'Fig1.pdf': manifestWithTicks(1, 9), 'Fig2.pdf': manifestWithTicks(1, 8) },
      page: { w: 80, h: 40 },
    })
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
    await mountDialogs()
    await openDialog()
    await click(radios()[0]) // 多图排版 → 每张图单独导出（原图范围）
    await click(figureOptions()[0]) // 在左栏缩略图条里点 Fig1：就是这次要导的那张
    expect(radios()[0].getAttribute('aria-checked')).toBe('true')
    // 没查出问题时说的是「按哪套规范、查的哪个范围没查出」，不是一句「检查通过」
    const ok = document.body.querySelector('[data-export-check-ok]')
    expect(ok, '没查出问题时给出结论行').toBeTruthy()
    const record = useProfileStore.getState().specs.find((r) => r.id === DEFAULT_PROFILE_ID)
    expect(record, '夹具里有默认规范').toBeTruthy()
    const specName = profileName(record!)
    expect(ok!.textContent).toContain(`按「${specName}」未发现问题`)
    expect(ok!.textContent).toContain('仅查原图「Fig1」')
    expect(ok!.textContent).not.toContain('检查通过')
    // 覆盖范围说在明处：识别不出的内容不在检查之内
    expect(ok!.querySelector('[data-export-check-coverage]')?.textContent).toContain('不在检查之内')
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)

    await click(radios()[1])
    expect(text()).toMatch(/\d+ 项要改/)
    expect(text()).toContain('1 项留意')
    expect(text()).toContain('查整个画布「')
    expect(errorHint(), '画布范围有阻断：计数芯片').toBeTruthy()
  })

  it('换界面语言后结论行里的规范名跟着换（#1194 评审 P2）', async () => {
    await stage({
      panels: [{ ...panel, h: 40 }],
      renders: { 'Fig1.pdf': manifestWithTicks(1, 9) },
      page: { w: 80, h: 40 },
    })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await mountDialogs()
    await openDialog()
    const record = useProfileStore.getState().specs.find((r) => r.id === DEFAULT_PROFILE_ID)!
    const ok = () => document.body.querySelector('[data-export-check-ok]')
    expect(ok(), '前提：没查出问题，有结论行').toBeTruthy()
    const zh = profileName(record)
    expect(ok()!.textContent).toContain(zh)
    try {
      await act(async () => { await i18n.changeLanguage('en-US') })
      const en = profileName(record)
      expect(en, '前提：内置规范的名字两种语言不同').not.toBe(zh)
      expect(ok()!.textContent).toContain(en)
      expect(ok()!.textContent).not.toContain(zh)
    } finally {
      await act(async () => { await i18n.changeLanguage('zh-CN') })
    }
  })

  it('按原图时这张图自己的阻断项照样拦住，并且报告里只有它的条目', async () => {
    await stage({
      panels: [panel, second],
      renders: { 'Fig1.pdf': manifestWithTicks(1, 8), 'Fig2.pdf': manifestWithTicks(1, 8) },
    })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await mountDialogs()
    await openDialog()
    // 按原图只算这一张图上的阻断项，别的图的一条都不算
    const errorsOn = (id: string) =>
      useValidationStore
        .getState()
        .issues.filter((i) => i.severity === 'error' && i.objectRef.objectId === id).length
    expect(errorsOn('p1')).toBeGreaterThan(0)
    expect(errorsOn('p2'), '夹具里另一张图也得有阻断项，否则这条什么都没证明').toBeGreaterThan(0)
    expect(text()).toContain(`${errorsOn('p1')} 项要改`)
    expect(text()).not.toContain(`${errorsOn('p1') + errorsOn('p2')} 项要改`)
    expect(errorHint(), '计数芯片在，但不拦导出').toBeTruthy()
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    await click(button('开始导出')!)
    const report = exportBodies[0].style_check_report as Record<string, unknown>
    const checks = report.checks as { id: string; object_ids?: string[]; objectIds?: string[] }[]
    const floor = checks.filter((c) => c.id === 'font-below-absolute-floor')
    expect(floor.length, '报告里的阻断项只有这张图的那一条').toBe(1)
    expect(floor[0].object_ids, '别的图的对象不进这份报告').toEqual(['p1'])
  })
})

describe('#1228 评审 P2 · 不在画布上的原图不借画布的检查结论', () => {
  const checkScope = () => document.body.querySelector('[data-export-check-scope]')?.textContent ?? ''
  const offCanvas = {
    id: 'Fig9.pdf',
    kind: 'pdf',
    mtime: 1,
  }

  /**
   * 画布上只有一条 5 pt 的文字（一条阻断项），项目里唯一的「图」是素材库里还没上画布的 Fig9。
   * 多图排版的「每张图」只认画布上的图，所以它只在单图编辑里到得了（项目里只有一张图时就是它）。
   */
  async function setupOff(opts: { withCanvasPanel?: boolean } = {}) {
    await stage({ panels: [panel], renders: { 'Fig1.pdf': manifestWithTicks(1, 8) } })
    if (!opts.withCanvasPanel) {
      useDocumentStore.getState().commit(literal('只留一段小字'), (d) => {
        d.objects = [
          { id: 't-small', type: 'text', text: '太小的说明文字', sizePt: 5, bold: false, italic: false, color: '#000000', align: 'left', x: 0, y: 70, w: 60, h: 6 } as never,
        ]
      })
    }
    useAssetStore.setState((st) => ({
      byId: opts.withCanvasPanel ? { ...st.byId, 'Fig9.pdf': offCanvas } : { 'Fig9.pdf': offCanvas },
      panels: opts.withCanvasPanel
        ? [...(st.panels ?? []), { id: 'Fig1.pdf', kind: 'pdf', mtime: 1 }, { ...offCanvas, native_w_mm: 70, native_h_mm: 50 }]
        : [{ ...offCanvas, native_w_mm: 70, native_h_mm: 50 }],
    }) as never)
    await act(async () => {
      runValidation()
    })
    useWorkspaceStore.setState({ mode: opts.withCanvasPanel ? 'layout' : 'fast_edit', activePanelId: null })
    await mountDialogs()
    await openDialog()
  }

  it('范围句如实说没检查；画布上的阻断项不进这次导出；摘要是「没查成」，灰色提示', async () => {
    await setupOff()
    const canvasErrors = useValidationStore.getState().issues.filter((i) => i.severity === 'error').length
    expect(canvasErrors, '前提：当前画布上有阻断项').toBeGreaterThan(0)
    expect(document.body.querySelector('[data-export-target]')!.textContent).toContain('Fig9')
    expect(checkScope()).toBe('「Fig9」不在画布上，这次导出不做规范检查')
    expect(document.body.querySelector('[data-export-check-ok]')).toBeNull()
    expect(text()).not.toMatch(/\d+ 项要改/)
    // 没查成：不拦导出、也没有警告句，仍记进报告的 checkFailed 档
    expect(document.body.querySelector('[data-export-hint]')).toBeNull()
    expect(errorHint()).toBeNull()
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect((exportBodies[0].original as { figure_id?: string }).figure_id).toBe('Fig9.pdf')
    const report = exportBodies[0].style_check_report as { checks?: { id: string }[] } | undefined
    expect(
      (report?.checks ?? []).filter((c) => c.id.startsWith('font-')),
      '画布上的条目不写进这张图的报告',
    ).toHaveLength(0)
  })

  it('对照：图在画布上时仍是「仅查原图」、画布阻断项照常给计数芯片', async () => {
    await setupOff({ withCanvasPanel: true })
    await click(originalRadio())
    const opt = figureOptions().find((o) => o.textContent?.includes('Fig1'))
    if (opt) await click(opt)
    // 素材库里没上画布的 Fig9 不在缩略图条里
    expect(figureOptions().map((o) => o.textContent)).toEqual(['Fig1'])
    expect(checkScope()).not.toContain('不在画布上')
    expect(text()).toMatch(/\d+ 项要改/)
    expect(errorHint(), '画布阻断项照常给计数芯片，但不拦').toBeTruthy()
  })
})

describe('T33 · 检查区只有计数芯片与问题面板入口', () => {
  const list = () => document.body.querySelector('[aria-label="阻断性问题"]')
  const locateButtons = () => [...document.body.querySelectorAll('button')].filter((b) => b.textContent === '定位')

  it('检查区不逐条列阻断项、没有「定位」入口和任何警告句：只有计数芯片 +「在问题面板中查看」', async () => {
    await stage({
      panels: [panel],
      renders: { 'Fig1.pdf': manifestWithTicks(1, 8) },
      page: { w: 80, h: 40 },
    })
    await mountDialogs()
    await openDialog()
    expect(errorHint(), '有阻断项：计数芯片在').toBeTruthy()
    expect(list(), '逐条清单已删').toBeNull()
    expect(document.body.querySelector('[data-blocking-issue]')).toBeNull()
    expect(locateButtons()).toHaveLength(0)
    expect(text()).not.toContain('投稿可能被退回')
    expect(button('在问题面板中查看'), '问题面板入口仍在').toBeTruthy()
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
  })

  it('计数芯片来自真实检查结果：要改 / 留意带色点，建议与无法核验永远是灰；点芯片带着这一档去问题面板', async () => {
    await stage({
      panels: [panel],
      renders: { 'Fig1.pdf': manifestWithTicks(1, 8) },
      page: { w: 80, h: 40 },
    })
    await mountDialogs()
    await openDialog()
    // 夹具本身不产出「建议」：补一条（同一份清单里改等级），才能证明「建议」那颗芯片确实是灰的
    await act(async () => {
      const cur = useValidationStore.getState().issues
      useValidationStore.setState({ issues: [...cur, { ...cur[0], issueId: 'extra-suggestion', severity: 'suggestion' }] })
    })
    const counts = useValidationStore.getState().issues.reduce<Record<string, number>>((m, i) => {
      m[i.severity] = (m[i.severity] ?? 0) + 1
      return m
    }, {})
    const chip = (tone: string) => document.body.querySelector<HTMLElement>(`[data-count-chip="${tone}"]`)
    expect(chip('danger')!.textContent).toBe(`${counts.error} 项要改`)
    expect(chip('warn')!.textContent).toBe(`${counts.warn} 项留意`)
    // 建议 / 无法核验只会出现在 quiet 档（有才出现），数字与检查结果一致
    for (const el of document.body.querySelectorAll<HTMLElement>('[data-count-chip="quiet"]')) {
      const n = Number(el.textContent!.match(/\d+/)![0])
      expect([counts.suggestion, counts.not_verifiable]).toContain(n)
    }
    expect(document.body.querySelectorAll('[data-count-chip="danger"]')).toHaveLength(1)
    // 每颗芯片的档由它的名字决定：只有「项要改」是 danger、「项留意」是 warn，其余（建议 / 无法核验）一律 quiet
    const toneOf: Record<string, string> = { 项要改: 'danger', 项留意: 'warn', 条建议: 'quiet', 项无法核验: 'quiet' }
    const all = [...document.body.querySelectorAll<HTMLElement>('[data-count-chip]')]
    expect(all.length).toBeGreaterThanOrEqual(2)
    for (const el of all) {
      const word = Object.keys(toneOf).find((w) => el.textContent!.includes(w))!
      expect(word, el.textContent!).toBeTruthy()
      expect(el.getAttribute('data-count-chip'), el.textContent!).toBe(toneOf[word])
    }
    // 圆点之外芯片不染色：没有任何 danger / warn 的字或底
    expect(chip('danger')!.className).not.toMatch(/text-danger|bg-danger|text-warn|bg-warn/)
    await click(chip('warn')!)
    expect(useUiStore.getState().leftTab).toBe('problems')
    expect(useUiStore.getState().problemFilter).toEqual(['warn'])
    expect(useUiStore.getState().exportOpen).toBe(false)
  })
  it('「在问题面板中查看」同样让开而不丢状态；换了文档才重置', async () => {
    await stage({ panels: [panel], renders: { 'Fig1.pdf': manifestWithTicks(1, 8) } })
    await mountDialogs()
    await openDialog()
    await typeFilename('kept')
    await click(button('在问题面板中查看')!)
    expect(useUiStore.getState().exportOpen).toBe(false)
    expect(useUiStore.getState().leftTab).toBe('problems')
    await openDialog()
    expect(filenameInput().value).toBe('kept')

    await typeFilename('x')
    await click(button('在问题面板中查看')!)
    expect(useUiStore.getState().exportOpen).toBe(false)
    await act(async () => {
      await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
    })
    await openDialog()
    expect(filenameInput().value, '换了文档，上一份文档的名字不许带过来').toBe(
      useDocumentStore.getState().doc.name,
    )
  })
})

describe('T35 · 设置压在导出之上：一次只显示一个主对话框', () => {
  const dialogs = () => [...document.body.querySelectorAll('[role="dialog"]')] as HTMLElement[]
  const covered = (el: HTMLElement) => el.hasAttribute('data-covered')

  it('「编辑规范」不关导出面板：设置压在上面，Esc 只退设置，回来时填过的东西与焦点都在', async () => {
    await stage({ panels: [panel], renders: { 'Fig1.pdf': manifestWithTicks(1, 9) } })
    await mountDialogs(true)
    await openDialog()
    await typeFilename('mine')
    await click(radios()[1]) // 整版

    const edit = document.body.querySelector('button[aria-label="编辑规范"]') as HTMLButtonElement
    // 真浏览器里 mousedown 会先把焦点给按钮；jsdom 的 click() 不会，这里补上
    edit.focus()
    await click(edit)
    await act(async () => {})

    const s = useUiStore.getState()
    expect(s.exportOpen, '深链不许先关导出').toBe(true)
    expect(s.settingsOpen).toBe(true)
    expect(s.dialogStack).toEqual(['export', 'settings'])
    const [exportDialog, settingsDialog] = dialogs()
    expect(dialogs()).toHaveLength(2)
    expect(covered(exportDialog), '导出面板被盖住：不可见但没卸载').toBe(true)
    expect(exportDialog.classList.contains('invisible'), '被盖住 = 真的看不见，不只是打个标').toBe(true)
    expect(covered(settingsDialog)).toBe(false)
    expect(settingsDialog.classList.contains('invisible')).toBe(false)
    // 只有一层遮罩看得见
    const overlays = [...document.body.querySelectorAll('.fixed.inset-0')]
    expect(overlays.filter((o) => !o.classList.contains('invisible'))).toHaveLength(1)

    // Esc 只关栈顶那一层
    await act(async () => {
      settingsDialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    // Radix FocusScope 在卸载后的 **setTimeout(0)** 里才把焦点还回去：只 flush
    // 微任务的话这一步有时还没跑到（六次里红两次的那种"偶发"）——等一个宏任务
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 0))
    })
    expect(useUiStore.getState().settingsOpen).toBe(false)
    expect(useUiStore.getState().exportOpen).toBe(true)
    expect(dialogCovered(useUiStore.getState().dialogStack, 'export')).toBe(false)
    expect(dialogs()).toHaveLength(1)
    expect(covered(dialogs()[0])).toBe(false)
    expect(filenameInput().value, '被盖住期间状态一个字没丢').toBe('mine')
    expect(document.activeElement, '焦点回到打开子步骤的那颗按钮').toBe(
      document.body.querySelector('button[aria-label="编辑规范"]'),
    )
  })
})

const originalRadio = () => scopeRadios()[0]
/** 多图排版左栏缩略图条里的「图」（不含「整版」那一项）；点它 = 换预览，「每张图单独导出」下同时选中要导的那张 */
const figureOptions = () =>
  [...document.body.querySelectorAll('[data-export-preview-tab]:not([data-export-preview-tab="page"])')] as HTMLElement[]

describe('检查芯片 / 「在问题面板中查看」带着导出目标去（#1106 P2）', () => {
  /** 面板此刻会当作「当前图」的那张（与 `useCurrentFigure` 同一个判据） */
  const panelCurrentFigure = () =>
    currentFigureOf(
      [useWorkspaceStore.getState().activePanelId, useUiStore.getState().elementPanelId, useSelectionStore.getState().ids.at(-1) ?? null],
      useDocumentStore.getState().doc.objects,
    ).id

  async function twoWithErrorOnP2() {
    await setupTwo({ select: ['p1'] })
    // Fig2 上 5 组 8 pt 刻度（多于先露的 3 条）：p2 有阻断项、p1 干净
    const k2 = renderKey('Fig2.pdf', [])
    await act(async () => {
      const cur = useRenderStore.getState().byKey[k2]
      useRenderStore.setState({
        byKey: { ...useRenderStore.getState().byKey, [k2]: { ...cur, manifest: manifestWithTicks(5, 8, [65, 50]) } },
      } as never)
      runValidation()
    })
    await click(originalRadio())
    await click(figureOptions()[1]) // 导出目标 = p2；画布上选中的仍是 p1
    expect(useSelectionStore.getState().ids).toEqual(['p1'])
    expect(panelCurrentFigure()).toBe('p1')
  }
  const danger = () => document.body.querySelector<HTMLElement>('[data-count-chip="danger"]')

  it('点「要改」芯片：面板的当前图 = 导出的那张，范围「当前图」，筛选只留阻断', async () => {
    await twoWithErrorOnP2()
    expect(danger(), 'p2 有阻断项，应有芯片').not.toBeNull()
    await click(danger()!)
    expect(panelCurrentFigure(), '芯片只带了等级，面板仍看工作区原来的图').toBe('p2')
    expect(useUiStore.getState().problemScope).toBe('figure')
    expect(useUiStore.getState().problemFilter).toEqual(['error'])
  })

  it('按画布导出：从画布 A 的芯片进去只列 A 的问题，B 画布的不跟进来（#1106 P2）', async () => {
    await stage({
      panels: [panel, { ...panel, id: 'p2', fileId: 'Fig2.pdf', x: 0, y: 60, script: 'fig2.py' }],
      renders: { 'Fig1.pdf': manifestWithTicks(1, 8), 'Fig2.pdf': manifestWithTicks(1, 9) },
    })
    await mountDialogs()
    await openDialog()
    const active = useDocumentStore.getState().activeCanvasId
    expect(text()).toMatch(/\d+ 项要改/)
    // 另一张画布上也有一条阻断项（克隆 A 的一条、换画布 id）：整份排版里是两张画布的问题
    await act(async () => {
      const cur = useValidationStore.getState().issues
      const err = cur.find((i) => i.severity === 'error')!
      useValidationStore.setState({
        issues: [...cur, { ...err, issueId: 'other-canvas', objectRef: { ...err.objectRef, canvasId: 'c_other' } }],
      })
    })
    await click(danger()!)
    const all = useValidationStore.getState().issues
    const scope = effectiveScope(useUiStore.getState().problemScope, null)
    expect(scope, '导出整块画布落在「当前画布」那档，不是整份排版').toBe('canvas')
    const listed = issuesInScope(all, scope, null, useDocumentStore.getState().activeCanvasId)
    expect(listed.some((i) => i.issueId === 'other-canvas'), 'B 画布的问题不该列进来').toBe(false)
    expect(listed.length).toBeGreaterThan(0)
    expect(listed.every((i) => i.objectRef.canvasId === active)).toBe(true)
    expect(all.length, '前提：整份排版里确实比 A 多').toBeGreaterThan(listed.length)
  })

  it('校验失败、保留上一轮结果的分支：「在问题面板中查看」打开面板，problemFilter 是 null 而不是 click event', async () => {
    await twoWithErrorOnP2()
    await act(async () => {
      useValidationStore.setState({ failed: true })
    })
    expect(text()).toContain('来自更早一次')
    useUiStore.getState().setProblemFilter(['warn'])
    await click(button('在问题面板中查看')!)
    expect(useUiStore.getState().leftTab).toBe('problems')
    // 筛选必须是 null 或等级数组——click event 被当成 severities 存进去，面板一打开 `.includes` 就崩
    const f = useUiStore.getState().problemFilter
    expect(f === null || (Array.isArray(f) && f.every((x) => typeof x === 'string'))).toBe(true)
    expect(f).toBeNull()
  })

  it('「在问题面板中查看」同样带目标', async () => {
    await twoWithErrorOnP2()
    await click(button('在问题面板中查看')!)
    expect(panelCurrentFigure()).toBe('p2')
    expect(useUiStore.getState().problemScope).toBe('figure')
  })
})

describe('画布模式下选中的图就是要导的那张（用户反馈 06）', () => {
  it('画布上选中了一个面板：原图尺寸可用，导的就是它', async () => {
    await setupTwo({ select: ['p2'] })
    expect(originalRadio().hasAttribute('disabled'), '选中了面板还说「先选中一张图」').toBe(false)
    expect(text()).not.toContain('先选中一张图')
    await click(originalRadio())
    expect(text()).toContain('65 × 50 mm') // 左栏 / 对象头报的是选中的那张
    // 「每张图单独导出」= 一次点击把每张图各导一份（依次发多个作业）
    await click(button('开始导出')!)
    await batchDone()
    expect(exportBodies).toHaveLength(2)
    expect(exportBodies.every((b) => b.scope === 'original')).toBe(true)
    expect(exportBodies.map((b) => (b.original as { figure_id: string }).figure_id).sort()).toEqual([
      'Fig1.pdf',
      'Fig2.pdf',
    ])
  })

  it('主选是文字、多选里混着一张图：导的是那张图', async () => {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_mixed')
    await setupTwo({ select: [] })
    useDocumentStore.getState().commit(literal('加文字'), (d) => {
      d.objects.push({ id: 't1', type: 'text', text: '标题', sizePt: 10, x: 0, y: 0, w: 30, h: 8 } as never)
    })
    await act(async () => {
      useSelectionStore.getState().set(['p1', 't1'])
    })
    expect(originalRadio().hasAttribute('disabled')).toBe(false)
    await click(originalRadio())
    expect(text()).toContain('80 × 60 mm')
  })

  it('什么都没选、项目里有两张图：缩略图条永远在；切到「每张图单独导出」不用先选一张，点哪张只换预览 / 检查摘要', async () => {
    await setupTwo({ select: [] })
    expect(text()).not.toContain('在下方选一张')
    expect(figureOptions()).toHaveLength(2)
    expect(scopeRadios()[1].getAttribute('aria-checked'), '默认整版').toBe('true')
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    expect(originalRadio().hasAttribute('disabled')).toBe(false)
    await click(originalRadio())
    // 每张图都导，没有「还没定要导哪一张」这回事；主按钮亮着
    expect(text()).not.toContain('在下方选一张')
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    const options = figureOptions()
    expect(options.map((o) => o.textContent)).toEqual(['Fig1', 'Fig2'])
    await click(options[1])
    expect(figureOptions()[1].getAttribute('aria-selected')).toBe('true')
    expect(text()).toContain('65 × 50 mm')

    await click(button('开始导出')!)
    await batchDone()
    expect(exportBodies).toHaveLength(2)
    expect(exportBodies.map((b) => (b.original as { figure_id: string }).figure_id).sort()).toEqual([
      'Fig1.pdf',
      'Fig2.pdf',
    ])
    // 点缩略图只是换预览，不改画布选区
    expect(useSelectionStore.getState().ids).toEqual([])
  })

  it('画布上选中了 p2，缩略图条里再点 Fig1：预览 / 检查摘要以点的为准，导出仍是每张各一份', async () => {
    await setupTwo({ select: ['p2'] })
    await click(originalRadio())
    await click(figureOptions()[0])
    expect(text()).toContain('80 × 60 mm')
    await click(button('开始导出')!)
    await batchDone()
    expect(exportBodies).toHaveLength(2)
  })

  it('关掉再打开：上一次点的那张不带过来，重新按上下文', async () => {
    await setupTwo({ select: ['p2'] })
    await click(originalRadio())
    await click(figureOptions()[0])
    expect(text()).toContain('80 × 60 mm')
    await act(async () => {
      useUiStore.getState().setExportOpen(false)
    })
    await act(async () => {
      useUiStore.getState().setExportOpen(true)
    })
    await click(originalRadio())
    expect(text()).toContain('65 × 50 mm')
  })

  it('缩略图条永远在；选中的那张源文件没了：整版下一个字不说，每张图单独导出才说', async () => {
    await setupTwo({ select: ['p2'] })
    // 缩略图条永远在；整版时点图只换预览，导出内容仍是整版
    expect(figureOptions()).toHaveLength(2)
    await click(figureOptions()[0])
    expect(figureOptions()[0].getAttribute('aria-selected')).toBe('true')
    expect(scopeRadios()[1].getAttribute('aria-checked')).toBe('true')
    await click(originalRadio())
    expect(figureOptions()).toHaveLength(2)
    await click(scopeRadios()[1])
    expect(figureOptions()).toHaveLength(2)
    // 选中的那张源文件没了：画布范围下一个字不说（导画布什么都没挡着）……
    await act(async () => {
      useAssetStore.setState({
        byId: { 'Fig1.pdf': { id: 'Fig1.pdf', kind: 'pdf', mtime: 1 } },
        panels: [{ id: 'Fig1.pdf', kind: 'pdf', mtime: 1 }],
      } as never)
    })
    expect(text()).not.toContain('源文件现在找不到了')
    // ……切到原图才说（选中的那张此刻导不出）；批量导出仍可点，导不出的那张在结果里如实记失败
    await click(originalRadio())
    expect(text()).toContain('源文件现在找不到了')
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    expect(figureOptions()).toHaveLength(2)
    await click(figureOptions()[0])
    expect(text()).not.toContain('源文件现在找不到了')
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    expect(text()).toContain('80 × 60 mm')
  })

  /**
   * 素材库里还没上画布的图（`listExportableFigures()` 里 `panel === null` 的候选）：选中后
   * `figureId` 与规格都有效、能导，对象头却曾按 `panel` 判而被压掉——项目里只有这一张时
   * 清单也收起，界面上没有一处写着对象名与最终尺寸（Codex 对 #337 的评审）。
   */
  it('素材里还没上画布的图：选中后对象头照样摆出名字与最终尺寸', async () => {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_offcanvas')
    const onDisk = {
      source_kind: 'vector',
      logical_w_mm: 70,
      logical_h_mm: 50,
      px_w: null,
      px_h: null,
      dpi: null,
      dpi_source: 'unknown',
      viewport_pt: [(70 / 25.4) * 72, (50 / 25.4) * 72],
      transparent: false,
    }
    useAssetStore.setState({
      byId: { 'Fig9.pdf': { id: 'Fig9.pdf', kind: 'pdf', mtime: 1, original_spec: onDisk } },
      panels: [{ id: 'Fig9.pdf', kind: 'pdf', mtime: 1, native_w_mm: 70, native_h_mm: 50, original_spec: onDisk }],
    } as never)
    useRenderStore.setState({ byKey: {}, latest: {}, tracked: {}, building: {} })
    // 单图编辑（多图排版的「每张图」不含没上画布的图）：项目里只有这一张，它就是要导的
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: null })
    await mountDialogs()
    await openDialog()
    // 项目里只有这一张：切到原图就是它，清单不出现——对象头是唯一写着对象名与尺寸的地方
    // 不在画布上 = 这次导出没有规范检查（#1228 P2）：灰色提示，不拦导出
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    expect(figureOptions(), '单图编辑没有缩略图条').toHaveLength(0)
    const header = document.body.querySelector('[data-export-target]')
    expect(header, '选中了图就该有对象头').toBeTruthy()
    expect(header!.textContent).toContain('Fig9')
    expect(header!.textContent).toContain('70 × 50 mm')
    expect(header!.textContent).not.toContain('没有当前图')
    const img = document.body.querySelector('[data-export-preview-stage] img') as HTMLImageElement | null
    expect(img?.getAttribute('src'), '左栏大预览走素材同一条地址').toContain('/api/render?id=Fig9.pdf')
    await click(button('开始导出')!)
    expect((exportBodies[0].original as { figure_id: string }).figure_id).toBe('Fig9.pdf')
  })

  it('素材库里另有一张还没上画布的图：不在缩略图条里，「每张图单独导出」也不导它（Codex / 用户 10-21 裁决）', async () => {
    await setupTwo({ select: [] })
    useAssetStore.setState((st) => ({
      byId: { ...st.byId, 'Fig3.pdf': { id: 'Fig3.pdf', kind: 'pdf', mtime: 1 } },
      panels: [...st.panels, { id: 'Fig3.pdf', kind: 'pdf', mtime: 1, native_w_mm: 42, native_h_mm: 30 }],
    }) as never)
    await click(originalRadio())
    expect(figureOptions().map((o) => o.textContent)).toEqual(['Fig1', 'Fig2'])
    await click(button('开始导出')!)
    await batchDone()
    expect(exportBodies.map((b) => (b.original as { figure_id: string }).figure_id).sort()).toEqual([
      'Fig1.pdf',
      'Fig2.pdf',
    ])
  })

  it('项目里一张图都没有：说「还没有可以导的图」，不摆空列表、不说"点选下面"', async () => {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_empty')
    useAssetStore.setState({ byId: {}, panels: [] } as never)
    useRenderStore.setState({ byKey: {}, latest: {}, tracked: {}, building: {} })
    useUiStore.getState().setExportOpen(true)
    await act(async () => {
      root.render(
        <TooltipProvider>
          <ExportDialog />
        </TooltipProvider>,
      )
    })
    expect(originalRadio().hasAttribute('disabled')).toBe(true)
    expect(text()).toContain('项目里还没有可以按原图尺寸导出的图')
    expect(text()).not.toContain('点选下面')
    expect(document.body.querySelector('[role="listbox"]')).toBeNull()
    // 这是一句说明，不是错误：画布照常能导
    expect(document.body.querySelector('.text-danger')).toBeNull()
  })

  it('缩略图：有图内修改的面板挂 store 里那份 SVG，没有的走素材缩略图地址，不发渲染请求', async () => {
    await setupTwo({ select: [] })
    // 清单只在原图范围下出现
    await click(originalRadio())
    const fetches: string[] = []
    const realFetch = globalThis.fetch
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      fetches.push(String(input))
      return realFetch(input, init)
    }) as typeof fetch
    // p1 改过一个值，且这一版已经画好在 store 里
    const patched = [{ gid: 'axes_0.xticks', prop: 'fontsize', value: 9 }]
    const key = renderKey('Fig1.pdf', patched)
    useRenderStore.setState((s) => ({
      byKey: {
        ...s.byKey,
        [key]: {
          ...s.byKey[renderKey('Fig1.pdf', [])],
          svg: '<svg viewBox="0 0 80 60" preserveAspectRatio="none" style="width:100%;height:100%"><rect id="mine" width="80" height="60"/></svg>',
          lastPatches: JSON.stringify(patched),
          wantPatches: JSON.stringify(patched),
        },
      } as never,
      latest: { ...s.latest, 'Fig1.pdf': key },
    }))
    await act(async () => {
      useDocumentStore.getState().commit(literal('改字号'), (d) => {
        ;(d.objects[0] as PanelObject).overrides = patched
      })
    })
    const [o1, o2] = figureOptions()
    const svgThumb = o1.querySelector('[data-export-thumb="svg"]')
    expect(svgThumb, '有修改的面板该挂引擎 SVG').toBeTruthy()
    expect(svgThumb!.querySelector('#mine')).toBeTruthy()
    const img = o2.querySelector('img[data-export-thumb="file"]') as HTMLImageElement
    expect(img, '没修改的面板走素材缩略图').toBeTruthy()
    expect(img.getAttribute('src')).toContain('/api/render?id=Fig2.pdf')
    // 打开列表这一路没有发过任何渲染请求
    expect(fetches.filter((u) => u.includes('/api/engine/'))).toEqual([])
  })
})

describe('EPS 与 TIFF（ADR 0046）', () => {
  it('画布范围：EPS 禁用并说出原因，TIFF 是位图（分辨率行随它出现）', async () => {
    await setup(9)
    await click(formatBox('PNG')) // 只剩 PDF
    expect(ppiSelect()).toBeNull()
    const eps = formatBox('EPS')
    expect(eps.disabled).toBe(true)
    expect(eps.closest('label')!.title).toContain('EPS 只支持导出单张图')
    await click(formatBox('TIFF'))
    expect(ppiSelect(), 'TIFF 是位图，分辨率那一行要出现').toBeTruthy()
    // 「这次会写哪几个文件」的预览行已按工作台批次去掉；发出去的格式清单在请求体里判
    await click(button('开始导出')!)
    expect(exportBodies[0].formats).toEqual(['pdf', 'tiff'])
    expect(exportBodies[0].ppi).toBe(600)
  })

  it('原图范围 + 有脚本的图：EPS 可用；只出 EPS 时 ppi 是 null', async () => {
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await setup(9)
    await act(async () => {
      useAssetStore.setState({
        byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1, script: 'fig1.py' } },
      } as never)
    })
    const eps = formatBox('EPS')
    expect(eps.disabled).toBe(false)
    await click(eps)
    await click(formatBox('PNG'))
    await click(formatBox('PDF'))
    expect(ppiSelect(), '只剩矢量格式，分辨率那一行不该出现').toBeNull()
    await click(button('开始导出')!)
    expect(exportBodies[0].scope).toBe('original')
    expect(exportBodies[0].formats).toEqual(['eps'])
    expect(exportBodies[0].ppi).toBeNull()
  })

  it('原图范围 + 没有脚本的图：EPS 禁用，说的是「没有脚本」而不是「只能按原图」', async () => {
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await setup(9)
    const eps = formatBox('EPS')
    expect(eps.disabled).toBe(true)
    expect(eps.closest('label')!.title).toContain('没有可重新运行的脚本')
  })

  it('勾着 EPS 切回画布：请求里自动不带它，界面把原因摆出来；只勾 EPS 时按钮变灰', async () => {
    await setup(9)
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
    await act(async () => {
      useAssetStore.setState({
        byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1, script: 'fig1.py' } },
      } as never)
    })
    // 素材表是 setup 之后才补的脚本：重开一次让对话框按它重算
    await act(async () => { useUiStore.getState().setExportOpen(false) })
    await act(async () => { useUiStore.getState().setExportOpen(true) })
    await click(originalRadio())
    await click(figureOptions().find((o) => o.textContent?.includes('Fig1'))!) // 选定要导的那张
    // EPS 能不能勾读 route（250 ms 防抖后到）；等它到，否则点的是一颗灰着的框
    await act(async () => { await new Promise((r) => setTimeout(r, 400)) })
    await click(formatBox('EPS'))
    expect(formatBox('EPS').checked, '前提：原图范围下 EPS 勾得上').toBe(true)
    await click(scopeRadios()[1])
    expect(text()).toContain('EPS 只支持导出单张图')
    await click(button('开始导出')!)
    expect(exportBodies[0].scope).toBe('canvas')
    expect(exportBodies[0].formats).toEqual(['pdf', 'png'])
    // 再把 PDF / PNG 都取消：只剩一个发不出去的 EPS，主按钮必须灰
    await click(formatBox('PDF'))
    await click(formatBox('PNG'))
    // 刚导完一次：此刻的「开始」是完成态脚部的「再次导出」，同一个闸
    expect(document.body.querySelector<HTMLButtonElement>('[data-export-again]')!.disabled).toBe(true)
  })
})


describe('每张图单独导出：依次发多个作业（后端协议不变）', () => {
  const start = () => click(button('开始导出')!)
  const bodiesFigureIds = () => exportBodies.map((b) => (b.original as { figure_id: string }).figure_id)

  it('一次点击，每张图各发一个作业；进度说第几张；全部成功是一份汇总，脚部换成「再次导出 · 完成」', async () => {
    jobStatusSeq = ['running', 'done']
    await setupTwo({ select: [] })
    await click(originalRadio())
    await start()
    // 第一张在跑：进度句点名第几张 / 共几张
    await until(() => exportBodies.length === 1)
    expect(exportBodies).toHaveLength(1)
    expect(document.body.querySelector('[data-export-progress]')?.textContent).toContain('第 1/2 张')
    await act(async () => {
      applyExportJob({ job_id: 'j1', status: 'done', outputs: [], warnings: [], conflicts: [], error: null } as never)
    })
    await batchDone()
    expect(bodiesFigureIds()).toEqual(['Fig1.pdf', 'Fig2.pdf'])
    expect(useExportStore.getState().batch?.aggregate?.status).toBe('done')
    expect(document.body.querySelector('[data-export-again]')).toBeTruthy()
    expect(document.body.querySelector('[data-export-finish]')).toBeTruthy()
  })

  it('每张各自按它自己的原图口径检查与留档：两份请求的 original 各是各的图', async () => {
    await setupTwo({ select: [] })
    await click(originalRadio())
    await start()
    await batchDone()
    expect(exportBodies.map((b) => (b.original as { figure_id: string; width_mm?: number }).figure_id)).toEqual([
      'Fig1.pdf',
      'Fig2.pdf',
    ])
    expect(exportBodies.map((b) => b.filename)).toEqual(['Fig1', 'Fig2'])
  })

  it('部分失败：汇总走既有 partialSummary，失败的那张点得出名字', async () => {
    jobStatusSeq = ['done', 'failed']
    await setupTwo({ select: [] })
    await click(originalRadio())
    await start()
    await batchDone()
    const agg = useExportStore.getState().batch!.aggregate!
    expect(agg.status).toBe('partial')
    expect(document.body.querySelector('[data-export-partial-summary]')?.textContent).toMatch(/1.*1/)
    expect(text()).toContain('Fig2')
  })

  it('撞名逐张走既有冲突流程：停在冲突条上，选「覆盖」重发这一张，再继续下一张', async () => {
    jobStatusSeq = ['conflict', 'done', 'done']
    await setupTwo({ select: [] })
    await click(originalRadio())
    await start()
    await until(() => !!button('覆盖'))
    expect(exportBodies).toHaveLength(1)
    expect(button('覆盖'), '停在第一张的冲突条上').toBeTruthy()
    expect(useExportStore.getState().batch?.done).toBe(false)
    await click(button('覆盖')!)
    await batchDone()
    expect(bodiesFigureIds()).toEqual(['Fig1.pdf', 'Fig1.pdf', 'Fig2.pdf'])
    expect(exportBodies[1].overwrite).toBe('replace')
    expect(useExportStore.getState().batch?.aggregate?.status).toBe('done')
  })

  it('批开始后改格式 / PPI / 排版、甚至关掉对话框：后面每张的请求仍是点击那一刻冻结的（Codex #1295 P1）', async () => {
    jobStatusSeq = ['running', 'done']
    await setupTwo({ select: [] })
    await click(originalRadio())
    const before = { formats: ['pdf', 'png'], ppi: 600 }
    await start()
    await until(() => exportBodies.length === 1)
    expect(exportBodies[0].formats).toEqual(before.formats)
    expect(exportBodies[0].ppi).toBe(before.ppi)
    // 第一张在跑：用户改格式（去掉 PNG）、改 PPI、改排版，再关掉对话框
    await click(formatBox('PNG'))
    await act(async () => {
      useDocumentStore.getState().commit(literal('批进行中改排版'), (d) => {
        d.page = { w: 99, h: 77 }
      })
      useUiStore.getState().setExportOpen(false)
    })
    await act(async () => {
      applyExportJob({ job_id: 'j1', status: 'done', outputs: [], warnings: [], conflicts: [], error: null } as never)
    })
    await batchDone()
    expect(exportBodies).toHaveLength(2)
    expect(exportBodies[1].formats, '第二张用点击那一刻的格式，不是后来改的').toEqual(before.formats)
    expect(exportBodies[1].ppi).toBe(before.ppi)
    expect(exportBodies.map((b) => b.filename)).toEqual(['Fig1', 'Fig2'])
  })

  it('冻结段 route 等待期间关掉对话框去改排版：整批一致地用改后的那一份文档，请求全部发出（不混版本、不作废）', async () => {
    routeOf = (req) => ({ scope: req.scope, formats: req.formats, panels: {} })
    routeDelayMs = 500
    await setupTwo({ select: [] })
    await click(originalRadio())
    await start()
    expect(useExportStore.getState().batch?.phase).toBe('prepare')
    // 第一张正在等 route：用户关掉对话框，去改排版
    await act(async () => {
      useUiStore.getState().setExportOpen(false)
      useDocumentStore.getState().commit(literal('冻结段里改排版'), (d) => {
        d.page = { w: 99, h: 77 }
      })
    })
    await batchDone(20_000)
    const b = useExportStore.getState().batch!
    expect(exportBodies).toHaveLength(2) // 全部发出，没有作废
    const docs = b.prepared.map((p) => p.input.doc)
    expect(docs).toHaveLength(2)
    expect(docs[0], '两张读的是同一份文档').toBe(docs[1])
    expect(docs[0], '且是改后的那一份（route 齐了那一刻）').toBe(useDocumentStore.getState().doc)
    expect(docs[0].page).toEqual({ w: 99, h: 77 })
  })

  it('冻结是一个同步块：整批的 buildBatchInput 调用之间没有任何 await / 微任务间隔', async () => {
    routeOf = (req) => ({ scope: req.scope, formats: req.formats, panels: {} })
    routeDelayMs = 100
    await setupTwo({ select: [] })
    await click(originalRadio())
    batchBuildLog.length = 0
    await start()
    await batchDone(20_000)
    // 前面是逐张取 route 前的探针调用（中间隔着 await）；最后 N=2 次是冻结块里的真调用，必须在同一个微任务纪元里
    expect(batchBuildLog.length).toBeGreaterThanOrEqual(4)
    const last2 = batchBuildLog.slice(-2)
    expect(last2[0]).toBe(last2[1])
    const probes = batchBuildLog.slice(0, -2)
    expect(probes[0], '探针之间隔着 await（纪元不同），证明这个断言能看出间隔').not.toBe(last2[0])
  })

  it('批里某张的请求体 == 同一张单张导出的请求体（冻结用的是同一条检查 / 留档链）', async () => {
    await setupTwo({ select: [] })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await act(async () => useUiStore.getState().setExportOpen(false))
    await act(async () => useUiStore.getState().setExportOpen(true))
    await click(button('开始导出')!)
    const single = exportBodies[0]
    expect((single.original as { figure_id: string }).figure_id).toBe('Fig1.pdf')
    exportBodies.length = 0
    resetExportState()
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
    await act(async () => useUiStore.getState().setExportOpen(false))
    await act(async () => useUiStore.getState().setExportOpen(true))
    await click(originalRadio())
    await click(button('开始导出')!)
    await batchDone(20_000)
    const inBatch = exportBodies.find((b) => (b.original as { figure_id: string }).figure_id === 'Fig1.pdf')
    expect(inBatch).toEqual(single)
  })

  it('冻结段里什么都没动：route 慢也照常整批发出，且每张用同一个文档版本', async () => {
    routeOf = (req) => ({ scope: req.scope, formats: req.formats, panels: {} })
    routeDelayMs = 300
    await setupTwo({ select: [] })
    await click(originalRadio())
    await start()
    await batchDone(20_000)
    expect(exportBodies).toHaveLength(2)
  })

  it('取消 = 停掉剩余：第二张不再发，已写出的如实报', async () => {
    jobStatusSeq = ['done', 'running']
    await setupTwo({ select: [] })
    await click(originalRadio())
    await start()
    await until(() => exportBodies.length === 2)
    expect(exportBodies).toHaveLength(2) // 第一张写出了，第二张在跑
    await click(button('取消导出')!)
    await act(async () => {
      applyExportJob({ job_id: 'j2', status: 'cancelled', outputs: [], warnings: [], conflicts: [], error: null } as never)
    })
    await batchDone()
    const agg = useExportStore.getState().batch!.aggregate!
    expect(agg.status, '写出过一张 → 如实报部分完成，不是「已取消」一句话盖过').toBe('partial')
    expect(agg.outputs.filter((o) => o.status === 'done')).toHaveLength(1)
    expect(exportBodies).toHaveLength(2)
  })

  it('导不出的那张（源文件没了）记一笔失败，其余照常导', async () => {
    await setupTwo({ select: [] })
    useAssetStore.setState({
      byId: { 'Fig1.pdf': { id: 'Fig1.pdf', kind: 'pdf', mtime: 1 } },
      panels: [{ id: 'Fig1.pdf', kind: 'pdf', mtime: 1 }],
    } as never)
    await click(originalRadio())
    await start()
    await batchDone()
    expect(bodiesFigureIds()).toEqual(['Fig1.pdf'])
    const b = useExportStore.getState().batch!
    expect(b.failedStarts.map((f) => f.name)).toEqual(['Fig2'])
    expect(b.failedStarts[0].reason, '那一张没起来的原因随汇总保留').not.toBe('')
    expect(b.aggregate?.status).toBe('partial')
    // 批结束后仍可见：摘要说有失败，详情里点名哪张 + 那条原因
    await openResultDetail()
    expect(document.body.querySelector('[data-export-result]')?.textContent).toContain('Fig2')
    expect(document.body.querySelector('[data-export-result]')?.textContent).toContain(b.failedStarts[0].reason)
  })
})

describe('visible export recovery', () => {
  afterEach(() => { resetExportState(); initI18n('zh-CN') })
  const input = () => ({ scope: 'canvas' as const, formats: ['pdf', 'png', 'tiff'], filename: 'Keep name', ppi: 600, documentId: 'd_export', doc: useDocumentStore.getState().doc })
  const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  const runningJob = { job_id: 'feedback-job', status: 'running' as const, outputs: [], warnings: [], conflicts: [], error: null }

  it('renders expired-session recovery and retranslates it without replacing user settings', async () => {
    initI18n('en-US')
    await setup(9)
    const doc = useDocumentStore.getState().doc
    globalThis.fetch = vi.fn(async () => response({ code: 'session_auth_required', error: '会话未建立或已失效' }, 401)) as typeof fetch
    await act(async () => { await runExport(input()) })
    const error = () => document.querySelector('[data-export-start-error]')?.textContent
    expect(error()).toContain('Relaunch Tavotto')
    expect(error()).not.toContain('Error:')
    await act(async () => { initI18n('zh-CN') })
    expect(error()).toContain('会话')
    expect(useDocumentStore.getState().doc).toBe(doc)
  })

  it('启动失败有具体原因（源文件已变）：这一句就是正文，通用标题不顶替它；技术细节在详情', async () => {
    for (const [lng, reason, generic] of [['en-US', 'The source file changed. Your edits are preserved.', 'The export didn’t finish'], ['zh-CN', '源文件已变化，已保留你的修改。', '导出没有完成']] as const) {
      initI18n(lng)
      await setup(9)
      globalThis.fetch = vi.fn(async () => response({ error: 'Source changed', code: 'artifact_source_unavailable', params: { reason: 'source_changed' } }, 409)) as typeof fetch
      await act(async () => { await runExport(input()) })
      const notice = document.querySelector('[data-export-start-error]')!
      expect(notice.textContent).toContain(reason)
      expect(notice.textContent).not.toContain(generic)
      expect(notice.querySelector('[data-notice-detail-toggle]')).not.toBeNull()
      resetExportState()
    }
  })

  it('启动失败只有「HTTP 500」：标题是一句人话，状态码折进详情；状态区同时只有一条失败条', async () => {
    initI18n('en-US')
    await setup(9)
    globalThis.fetch = vi.fn(async () => response({}, 500)) as typeof fetch
    await act(async () => { await runExport(input()) })
    const notice = document.querySelector('[data-export-start-error]')!
    expect(notice.textContent).toContain('The export didn’t finish')
    expect(notice.textContent).not.toContain('HTTP 500')
    await act(async () => { (notice.querySelector('[data-notice-detail-toggle]') as HTMLButtonElement).click() })
    expect(notice.querySelector('[data-notice-detail]')?.textContent).toContain('HTTP 500')
    expect(document.querySelectorAll('[data-export-failure]')).toHaveLength(1)
  })

  it('失败条的先后与详情展开不依赖译文：先状态失败、再取消失败，切换界面语言后仍是取消那条', async () => {
    initI18n('en-US')
    await setup(9)
    globalThis.fetch = vi.fn(async () => response(runningJob)) as typeof fetch
    await act(async () => { await runExport(input()) })
    globalThis.fetch = vi.fn(async () => { throw new TypeError('Failed to fetch') }) as typeof fetch
    await act(async () => { await refreshExportStatus() })
    expect(document.querySelector('[data-export-poll-error]')).not.toBeNull()
    // 取消失败带一段较长的原文 → 有「详情」可展开
    globalThis.fetch = vi.fn(async () => { throw new TypeError(`Failed to fetch ${'upstream '.repeat(15)}`) }) as typeof fetch
    await act(async () => { await cancelCurrentExport() })
    expect(document.querySelectorAll('[data-export-failure]')).toHaveLength(1)
    expect(document.querySelector('[data-export-cancel-error]')).not.toBeNull()
    await act(async () => { await i18n.changeLanguage('zh-CN') })
    // 签名只认稳定的失败身份：换语言后所有译文都变了，也不会按固定键序重新盖戳把旧的状态失败顶上来
    expect(document.querySelector('[data-export-poll-error]')).toBeNull()
    expect(document.querySelector('[data-export-cancel-error]')).not.toBeNull()
    expect(document.querySelectorAll('[data-export-failure]')).toHaveLength(1)
  })

  it('shows cancel and status failures, keeps dialog/job on dismissal, and retries status', async () => {
    initI18n('en-US')
    await setup(9)
    globalThis.fetch = vi.fn(async () => response(runningJob)) as typeof fetch
    await act(async () => { await runExport(input()) })
    globalThis.fetch = vi.fn(async () => { throw new TypeError('Failed to fetch') }) as typeof fetch
    await act(async () => { await cancelCurrentExport(); await refreshExportStatus() })
    // 失败条同时最多一条：取消失败先出现、状态失败后出现 → 后来的替换前一条（不堆叠）
    expect(document.querySelectorAll('[data-export-failure]')).toHaveLength(1)
    expect(document.querySelector('[data-export-cancel-error]')).toBeNull()
    expect(document.querySelector('[data-export-poll-error]')?.textContent).toContain('job may still finish')
    // 原文较长：折在「详情」里，展开才见
    expect(document.querySelector('[data-export-poll-error]')?.textContent).not.toContain('Check the connection')
    await act(async () => { (document.querySelector('[data-export-poll-error] [data-notice-detail-toggle]') as HTMLButtonElement).click() })
    expect(document.querySelector('[data-export-poll-error] [data-notice-detail]')?.textContent).toContain('Check the connection')
    await act(async () => { useUiStore.getState().setExportOpen(false) })
    expect(useExportStore.getState().ownedJobId).toBe('feedback-job')
    await act(async () => { useUiStore.getState().setExportOpen(true) })
    expect(document.querySelector('[data-export-poll-error]')).toBeTruthy()
    globalThis.fetch = vi.fn(async () => response({ ...runningJob, status: 'done' })) as typeof fetch
    await act(async () => { (document.querySelector('[data-export-status-retry]') as HTMLButtonElement).click() })
    expect(document.querySelector('[data-export-poll-error]')).toBeNull()
    expect(document.querySelector('[data-export-cancel-error]')).toBeNull()
  })

  it('offers the task diagnostic when every requested output failed (failed with nonempty outputs)', async () => {
    initI18n('en-US')
    await setup(9)
    const failedOut = (format: string) => ({ format, name: null, url: null, bytes: null, dimensions: null, vector: false, status: 'failed', replaced: false, error: { code: 'format_failed', params: {}, recoverable: true } })
    globalThis.fetch = vi.fn(async () => response({ job_id: 'allfail1', status: 'failed', outputs: [failedOut('pdf'), failedOut('png')], warnings: [], conflicts: [], export_dir: '/out', error: { code: 'format_failed', params: {}, recoverable: true } })) as typeof fetch
    await act(async () => { await runExport(input()) })
    expect(document.querySelector('[data-export-failed]')).toBeNull() // 走的是逐项结果分支
    await openResultDetail()
    expect(document.querySelectorAll('[data-export-result] p.text-danger-content').length).toBe(2)
    expect(document.querySelector('[data-task-diagnostic][data-task-diagnostic-kind="export"]')).toBeTruthy()
  })

  it('disables repeated cancellation before and after acknowledgement without claiming completion', async () => {
    initI18n('en-US')
    await setup(9)
    globalThis.fetch = vi.fn(async () => response(runningJob)) as typeof fetch
    await act(async () => { await runExport(input()) })
    let resolve!: (r: Response) => void
    globalThis.fetch = vi.fn(() => new Promise<Response>((r) => { resolve = r })) as typeof fetch
    const button = () => document.querySelector('[data-export-cancel]') as HTMLButtonElement
    act(() => { button().click(); button().click() })
    expect(button().disabled).toBe(true)
    expect(button().textContent).toContain('Requesting cancellation')
    expect(fetch).toHaveBeenCalledTimes(1)
    await act(async () => { resolve(response({ cancelling: true })) })
    expect(button().disabled).toBe(true)
    expect(button().textContent).toContain('Cancellation requested')
    expect(useExportStore.getState().running).toBe(true)
    await act(async () => { applyExportJob({ ...runningJob, status: 'cancelled' }) })
    expect(document.querySelector('[data-export-cancel]')).toBeNull()
  })
})


describe('导出终局通知：关窗期间的结果不能无声', () => {
  afterEach(() => { resetExportState(); initI18n('zh-CN'); useUiStore.getState().setStatus(null) })
  const input = () => ({ scope: 'canvas' as const, formats: ['pdf', 'png'], filename: 'Keep name', ppi: 600, documentId: 'd_export', doc: useDocumentStore.getState().doc })
  const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  const base = { job_id: 'job-1', warnings: [], conflicts: [] as string[], error: null }
  const failedOut = { format: 'png', name: null, url: null, bytes: null, dimensions: null, vector: false, status: 'failed', replaced: false, error: { code: 'format_failed', params: {}, recoverable: true } }
  const doneOut = { format: 'pdf', name: 'a.pdf', url: '/exports/a.pdf', bytes: 1, dimensions: { px: null, mm: null }, vector: true, status: 'done', replaced: false, error: null }
  const status = () => useUiStore.getState()

  async function startThenClose(closeFirst = true) {
    await setup(9)
    globalThis.fetch = vi.fn(async () => response({ ...base, status: 'running', outputs: [] })) as typeof fetch
    await act(async () => { await runExport(input()) })
    if (closeFirst) await act(async () => { useUiStore.getState().setExportOpen(false) })
  }
  const finish = (job: Record<string, unknown>) => act(async () => { applyExportJob({ ...base, outputs: [], ...job } as never) })

  it.each([
    ['failed', { status: 'failed', outputs: [failedOut], error: { code: 'format_failed', params: {}, recoverable: true } }, 'error'],
    ['partial', { status: 'partial', outputs: [doneOut, failedOut] }, 'error'],
    ['conflict', { status: 'conflict', conflicts: ['a.pdf'] }, 'info'],
    ['unknown', { status: 'unknown' }, 'error'],
  ])('关着对话框时 %s：出一条带「查看」的通知，且不自己消失', async (_n, job, tone) => {
    await startThenClose()
    await finish(job)
    const s = status()
    expect(s.status).not.toBeNull()
    expect(s.statusTone).toBe(tone)
    expect(JSON.stringify(s.status)).toContain('export.toast.')
    expect(s.statusAction).not.toBeNull()
    expect(s.statusOwner).toBe('export.result')
  })

  it('「查看」重新打开对话框并撤掉这条通知', async () => {
    await startThenClose()
    await finish({ status: 'failed', outputs: [failedOut], error: { code: 'format_failed', params: {}, recoverable: true } })
    await act(async () => { status().statusAction!.run() })
    expect(status().exportOpen).toBe(true)
    expect(status().status).toBeNull()
  })

  it('对话框开着时不再弹第二份（结果就在结果块里）', async () => {
    await startThenClose(false)
    expect(status().exportOpen).toBe(true)
    await finish({ status: 'failed', outputs: [failedOut], error: { code: 'format_failed', params: {}, recoverable: true } })
    expect(status().status).toBeNull()
    // 成功也一样：开着就不弹
    resetExportState()
    await startThenClose(false)
    await finish({ job_id: 'job-1', status: 'done', outputs: [doneOut] })
    expect(status().status).toBeNull()
  })

  it('对照：关着对话框时成功 / 取消仍有一句话（不带动作、自己消失）', async () => {
    await startThenClose()
    await finish({ status: 'done', outputs: [doneOut] })
    expect(JSON.stringify(status().status)).toContain('export.exported')
    expect(status().statusAction).toBeNull()
  })

  it('重复的终态快照只通知一次：用户关掉后同一结果不会再冒出来', async () => {
    await startThenClose()
    const job = { status: 'failed', outputs: [failedOut], error: { code: 'format_failed', params: {}, recoverable: true } }
    await finish(job)
    expect(status().status).not.toBeNull()
    await act(async () => { useUiStore.getState().setStatus(null) })
    await finish(job)
    await act(async () => { await refreshExportStatus() })
    expect(status().status).toBeNull()
    // 开了又关一次对话框（可见性变化会让通知逻辑重跑）：同一个结果仍然不再冒出来
    await act(async () => { useUiStore.getState().setExportOpen(true) })
    await act(async () => { useUiStore.getState().setExportOpen(false) })
    expect(status().status).toBeNull()
  })

  it('重新打开对话框也撤掉关窗期间挂着的通知', async () => {
    await startThenClose()
    await finish({ status: 'conflict', conflicts: ['a.pdf'] })
    expect(status().status).not.toBeNull()
    await act(async () => { useUiStore.getState().setExportOpen(true) })
    expect(status().status).toBeNull()
  })
})


/**
 * 设计审计 §10.2 P0：进度 / 冲突 / 结果在可滚正文的最末尾（正文一长就在视口外），
 * 导完之后脚部还挂着「开始导出」——再点一下就撞上自己刚写出的同名文件。
 */
describe('状态区与完成态脚部', () => {
  const statusArea = () => document.body.querySelector<HTMLElement>('[data-dialog-status]')
  const q = (sel: string) => document.body.querySelector<HTMLButtonElement>(sel)
  const w = window as unknown as Record<string, unknown>
  afterEach(() => {
    delete w.__TAURI_INTERNALS__
    tauriInvoke.mockReset()
  })

  it('结果落在脚部正上方的状态区，不在可滚正文里', async () => {
    await setup(9)
    expect(statusArea(), '还没导：状态区不占位').toBeNull()
    await click(q('[data-export-start]')!)
    const area = statusArea()
    expect(area).toBeTruthy()
    expect(area!.textContent).toContain('已导出 1 个文件')
    await openResultDetail()
    expect(statusArea()!.textContent).toContain('a.pdf')
    // 状态区与可滚正文是兄弟，不是它的后代：正文怎么滚都不会把结果滚出视口
    const scrollBody = document.body.querySelector('[data-dialog="export"] .overflow-y-auto')
    expect(scrollBody?.contains(area!)).toBe(false)
    expect(scrollBody?.textContent).not.toContain('a.pdf')
  })

  it('撞名：说明在状态区，两条出路在脚部——「另存一份」(secondary) +「覆盖」(危险浅底胶囊)', async () => {
    jobStatus = 'conflict'
    await setup(9)
    await click(q('[data-export-start]')!)
    expect(statusArea()!.textContent).toContain('已经有 a.pdf')
    const footer = q('[data-dialog="export"] [data-dialog-footer]')!
    expect(footer.querySelector('[data-export-conflict-replace]')!.getAttribute('data-variant')).toBe('danger-tinted')
    expect(footer.querySelector('[data-export-conflict-rename]')!.getAttribute('data-variant')).toBe('secondary')
    // 撞名不是完成，也不是「再点一次开始导出」：脚部只给两条出路
    expect(q('[data-export-start]')).toBeNull()
    expect(q('[data-export-finish]')).toBeNull()
  })

  it('导完：脚部换成「再次导出 · 完成」，没有第二颗「开始导出」', async () => {
    await setup(9)
    await click(q('[data-export-start]')!)
    expect(q('[data-export-start]')).toBeNull()
    const again = q('[data-export-again]')!
    const finish = q('[data-export-finish]')!
    expect(again.textContent).toBe('再次导出')
    expect(finish.textContent).toBe('完成')
    // 一个上下文只有一个填色主动作：「完成」；「再次导出」是次要的
    expect(finish.className).toContain('bg-ink')
    expect(again.className).not.toContain('bg-ink')
    // 浏览器模式没有文件管理器：不摆一颗点了没用的按钮
    expect(q('[data-export-reveal]')).toBeNull()
  })

  it('键盘在「开始导出」上按下去：那颗按钮卸掉后焦点接到「完成」，不摔到 body', async () => {
    await setup(9)
    const start = q('[data-export-start]')!
    start.focus()
    expect(document.activeElement).toBe(start)
    await click(start)
    expect(document.activeElement).toBe(q('[data-export-finish]'))
  })

  it('「完成」关掉对话框，不发第二次导出', async () => {
    await setup(9)
    await click(q('[data-export-start]')!)
    await click(q('[data-export-finish]')!)
    expect(useUiStore.getState().exportOpen).toBe(false)
    expect(exportBodies).toHaveLength(1)
  })

  it('「再次导出」是显式的新一次导出（同名照样先问）', async () => {
    await setup(9)
    await click(q('[data-export-start]')!)
    jobStatus = 'conflict'
    await click(q('[data-export-again]')!)
    expect(exportBodies).toHaveLength(2)
    expect(exportBodies[1].overwrite).toBe('ask')
    expect(statusArea()!.textContent).toContain('已经有 a.pdf')
  })

  it('重新打开对话框：上一回的完成态不留在脚部', async () => {
    await setup(9)
    await click(q('[data-export-start]')!)
    expect(q('[data-export-finish]')).toBeTruthy()
    await act(async () => {
      useUiStore.getState().setExportOpen(false)
    })
    await act(async () => {
      useUiStore.getState().setExportOpen(true)
    })
    expect(q('[data-export-start]')).toBeTruthy()
    expect(q('[data-export-finish]')).toBeNull()
  })

  it('桌面里有「在文件夹中显示」，定位的是刚写出的那个文件；失败时说出完整路径', async () => {
    w.__TAURI_INTERNALS__ = {}
    await setup(9)
    await click(q('[data-export-start]')!)
    const reveal = q('[data-export-reveal]')!
    expect(reveal.textContent).toBe('在文件夹中显示')
    expect(reveal.className).not.toContain('bg-ink')
    await click(reveal)
    expect(tauriInvoke).toHaveBeenCalledWith('reveal_export', { dir: '/out', name: 'a.pdf' })
    expect(document.body.querySelector('[data-export-reveal-error]')).toBeNull()

    tauriInvoke.mockRejectedValueOnce(new Error('denied'))
    await click(reveal)
    expect(document.body.querySelector('[data-export-reveal-error]')?.textContent).toContain(
      '/out/a.pdf',
    )
  })
  it('连点两次「在文件夹中显示」：先发的那次迟到失败，不覆盖后一次的成功（Codex #821 P2）', async () => {
    w.__TAURI_INTERNALS__ = {}
    await setup(9)
    await click(q('[data-export-start]')!)
    const reveal = q('[data-export-reveal]')!
    let rejectFirst!: (e: Error) => void
    tauriInvoke.mockReturnValueOnce(new Promise((_, rej) => (rejectFirst = rej)))
    await click(reveal)
    await click(reveal)
    await act(async () => {
      rejectFirst(new Error('late'))
    })
    expect(document.body.querySelector('[data-export-reveal-error]')).toBeNull()
  })
})

describe('预览信息完善（PR3）：缩略图说真话', () => {
  const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)
  const target = () => q('[data-export-target]')!
  const stage = () => q('[data-export-preview-stage]')!

  it('左栏预览角上有「示意 · 非最终文件」的标注（两种范围都有），且不是警告色', async () => {
    await setup(9)
    expect(stage().querySelector('[data-export-thumb-note]')?.textContent).toBe(
      '缩略图仅为示意 · 非最终文件',
    )
    expect(stage().querySelector('[data-export-thumb-note]')?.className).not.toContain('warn')
    expect(target().querySelector('[data-export-thumb-note]'), '一行头部不再带这句').toBeNull()
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await act(async () => useUiStore.getState().setExportOpen(false))
    await act(async () => useUiStore.getState().setExportOpen(true))
    expect(stage().querySelector('[data-export-thumb-note]')).toBeTruthy()
  })

  it('勾了透明背景（PNG 画布范围）：左栏大预览的页面画成棋盘格；不勾就是纸白', async () => {
    await setup(9)
    expect(stage().querySelector('[data-thumb-transparent]')).toBeNull()
    expect(formatBox('PNG').checked, '默认就带 PNG').toBe(true)
    const toggle = document.body.querySelector<HTMLElement>('[aria-label="透明背景（仅 PNG / TIFF）"]')!
    await click(toggle)
    const page = stage().querySelector('[data-thumb-page]')!
    expect(page.hasAttribute('data-thumb-transparent')).toBe(true)
    expect(page.getAttribute('fill')).toMatch(/^url\(#thumb-checker-/)
    expect(stage().querySelector('pattern')).toBeTruthy()
    await click(toggle)
    expect(stage().querySelector('[data-thumb-transparent]')).toBeNull()
    expect(stage().querySelector('[data-thumb-page]')!.getAttribute('fill')).toBe('var(--color-paper)')
  })

  it('原图范围：左栏大预览取编辑后的那一份图——有图内修改就都是引擎 SVG（导出的正是重渲那一版）', async () => {
    await setup(9)
    useDocumentStore.getState().commit(literal('改'), (d) => {
      ;(d.objects[0] as PanelObject).overrides = [
        { gid: 'axes_0.xticks', prop: 'fontsize', value: 9 },
      ]
    })
    const edited = useDocumentStore.getState().doc.objects[0] as PanelObject
    seedExactRender(edited, manifest(9) as never, { svg: '<svg data-marker="edited"></svg>' })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await act(async () => useUiStore.getState().setExportOpen(false))
    await act(async () => useUiStore.getState().setExportOpen(true))
    // 左栏大预览读的是编辑后的引擎 SVG（导出的正是重渲那一版），不是磁盘原图；对象头不再重复一份缩略图
    const big = q('[data-export-preview-stage] [data-export-thumb="svg"]')
    expect(big, '大预览读的是编辑后的引擎 SVG，不是磁盘原图').toBeTruthy()
    expect(big!.innerHTML).toContain('data-marker="edited"')
    expect(q('[data-export-preview-stage] [data-export-thumb="file"]')).toBeNull()
    expect(target().querySelector('[data-export-thumb]')).toBeNull()
  })

  it('原图缩略图的盒按真实图幅比例（不是固定 60×40）：竖图贴高、宽图贴宽，外框尺寸不变', async () => {
    await setup(9)
    useDocumentStore.getState().commit(literal('改'), (d) => {
      ;(d.objects[0] as PanelObject).overrides = [
        { gid: 'axes_0.xticks', prop: 'fontsize', value: 9 },
      ]
    })
    const edited = useDocumentStore.getState().doc.objects[0] as PanelObject
    // 竖图 40×80：manifest 的图幅 → 比例 0.5
    seedExactRender(edited, { ...manifest(9), size_mm: [40, 80] } as never, { svg: '<svg></svg>' })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await act(async () => useUiStore.getState().setExportOpen(false))
    await act(async () => useUiStore.getState().setExportOpen(true))
    // 内容盒按真实图幅比例定（测不到舞台尺寸的 jsdom 里走 CSS 退路：aspect-ratio + 宽 100%），图本身填满盒子——不留边距
    const content = q('[data-export-preview-stage] [data-export-preview-content]') as HTMLElement
    expect(content.style.aspectRatio.replace(' / 1', '')).toBe('0.5')
    const box = content.querySelector('[data-export-thumb="svg"]') as HTMLElement
    expect(box.className).toContain('h-full')
    expect(box.className).toContain('w-full')
  })
})

describe('预览信息完善（PR3）：结果块', () => {
  const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)
  const out = (format: string, name: string | null, status: 'done' | 'failed') => ({
    format,
    name,
    url: name ? `/exports/${name}` : null,
    bytes: name ? 10 : null,
    dimensions: { px: null, mm: [80, 60] },
    vector: format === 'pdf',
    status,
    replaced: false,
    error: status === 'failed' ? { code: 'format_failed', params: {} } : null,
  })

  it('partial：一句汇总的数字取自后端 outputs；逐行不变', async () => {
    jobStatus = 'partial'
    jobOutputs = [
      out('pdf', 'a.pdf', 'done'),
      out('png', 'a.png', 'done'),
      out('tiff', null, 'failed'),
    ]
    await setup(9)
    await click(q('[data-export-start]')!)
    expect(q('[data-export-partial-summary]')?.textContent).toBe('2 个成功，1 个失败')
  })

  it('全部成功：没有汇总句', async () => {
    await setup(9)
    await click(q('[data-export-start]')!)
    expect(q('[data-export-result]')).toBeTruthy()
    expect(q('[data-export-partial-summary]')).toBeNull()
  })
})


describe('runtime 图：打开对话框主动确认注册状态（#1348 Codex）', () => {
  const ID = 'runtime:fig.py#F'
  async function openRuntime(assets: unknown[] = []) {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_rt')
    useDocumentStore.getState().commit(literal('准备'), (d) => {
      d.page = { w: 80, h: 60 }
      d.objects = [
        {
          ...panel,
          id: 'rp',
          fileId: ID,
          fileKind: 'runtime',
          source: { script: 'fig.py', stem: 'F' },
        } as never,
      ]
    })
    useAssetStore.setState({ byId: {} } as never)
    useRuntimeAssetStore.setState({ assets, byId: {} } as never)
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'rp' })
    useUiStore.getState().setExportOpen(false)
    await act(async () => {
      useUiStore.getState().setExportOpen(true)
    })
  }
  afterEach(() => {
    runtimeStatus = null
    runtimeStatusGate = null
    runtimeStatusFail = 0
  })

  it('清单缺 id、注册状态未知：先显示「正在确认」且不放行，ensure 回 registered 后可用（EPS 亮、按原图导）', async () => {
    let release!: () => void
    runtimeStatusGate = new Promise<void>((r) => (release = r))
    runtimeStatus = { status: 'fresh', cached: true, registered: true, execution_profile: 'safe' }
    await setup(9) // 先把组件挂上、store 铺好
    await openRuntime()
    expect(document.body.querySelector('[data-export-figure-checking]'), '确认中').toBeTruthy()
    expect(button('开始导出')!.hasAttribute('disabled'), '确认完之前不放行').toBe(true)
    await act(async () => release())
    await act(async () => {})
    expect(document.body.querySelector('[data-export-figure-checking]')).toBeNull()
    expect(formatBox('EPS').disabled, '单图原图口径可用 → EPS 亮').toBe(false)
  })

  it('旧的 registered:false（重跑注册之前问的）：打开对话框重新问，拿到 true 后可用', async () => {
    runtimeStatus = { status: 'fresh', cached: true, registered: true, execution_profile: 'safe' }
    await setup(9)
    await openRuntime()
    const settle = () => act(async () => void (await new Promise((r) => setTimeout(r, 30))))
    await settle()
    // 用户重跑脚本之前问过一次得到 false，之后重跑注册了——store 里还留着旧的 false
    useRuntimeAssetStore.setState({
      byId: { [ID]: { registered: false, checked: true, status: 'needs_rerun', cached: false, profile: 'safe' } },
    } as never)
    await act(async () => {
      useUiStore.getState().setExportOpen(false)
    })
    await act(async () => {
      useUiStore.getState().setExportOpen(true)
    })
    await settle()
    expect(useRuntimeAssetStore.getState().byId[ID]?.registered).toBe(true)
    expect(formatBox('EPS').disabled).toBe(false)
  })

  const settle = () => act(async () => void (await new Promise((r) => setTimeout(r, 30))))

  it.each([500, -1])('status 查询失败（%i）：注册状态仍未知，照常请求原图口径，不静默改走画布', async (fail) => {
    runtimeStatusFail = fail
    await setup(9)
    await openRuntime()
    await settle()
    expect(useRuntimeAssetStore.getState().byId[ID]?.registered, '传输失败不是「不认」').toBeNull()
    expect(document.body.querySelector('[data-export-figure-checking]'), '问过了，不再「确认中」').toBeNull()
    expect(document.body.querySelector('[data-export-runtime-refused]')).toBeNull()
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].scope, '按原图口径交给后端权威校验').toBe('original')
  })

  it('后端明确回 registered:false：不静默改走画布，给 note 与按钮，用户显式选了才按画布导', async () => {
    runtimeStatus = { status: 'needs_rerun', cached: false, registered: false, execution_profile: 'safe' }
    await setup(9)
    await openRuntime()
    await settle()
    expect(document.body.querySelector('[data-export-runtime-refused]'), '说清楚要重新运行').toBeTruthy()
    expect(button('开始导出')!.hasAttribute('disabled'), '不静默导出').toBe(true)
    await click(document.body.querySelector('[data-export-accept-canvas]') as HTMLElement)
    expect(document.body.querySelector('[data-export-runtime-refused]')).toBeNull()
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].scope, '用户显式选了才走画布').toBe('canvas')
  })

  it('缓存清单里还有旧条目、但刚问来的结果明确 false：显示 refused 提示、不发原图导出', async () => {
    runtimeStatus = { status: 'needs_rerun', cached: false, registered: false, execution_profile: 'safe' }
    await setup(9)
    await openRuntime([{ id: ID, script: 'fig.py', stem: 'F', cached: true, status: 'fresh', size_mm: [80, 60] }])
    await settle()
    expect(document.body.querySelector('[data-export-runtime-refused]'), '新鲜的「不认」优先于旧清单').toBeTruthy()
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(true)
    expect(exportBodies).toHaveLength(0)
  })
})

describe('导出页改版（beta 0.19.0b3 反馈）', () => {
  const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)
  const out = (format: string, name: string | null, status: 'done' | 'failed') => ({
    format,
    name,
    url: name ? `/exports/${name}` : null,
    bytes: name ? 10 : null,
    dimensions: { px: null, mm: [80, 60] },
    vector: format === 'pdf',
    status,
    replaced: false,
    error: status === 'failed' ? { code: 'format_failed', params: {} } : null,
  })

  it('勾 SVG 后格式下面没有那段「默认保留文字…」说明', async () => {
    await setup(9)
    await click(formatBox('SVG'))
    expect(q('[data-export-svg-note]')).toBeNull()
    expect(q('[data-export-group="format"]')!.textContent).not.toContain('转换为形状')
  })

  it('输出组：只有卡片本身——没有分隔线，单图档也没有空的第一行', async () => {
    await setup(9)
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await act(async () => useUiStore.getState().setExportOpen(false))
    await act(async () => useUiStore.getState().setExportOpen(true))
    const card = q('[data-export-group="output"] [data-ui-field-group]')!
    expect(card.hasAttribute('data-ui-field-group-plain')).toBe(true)
    // 范围那一节在单图档里什么都不渲染（没有 segmented、没有说明）：DOM 里它是空的，CSS 的 empty:hidden 让它不占行
    const scope = card.querySelector('section[aria-label]')!
    expect(scope.children).toHaveLength(0)
    expect(scope.className).toContain('empty:hidden')
    // 其余组仍有分隔线
    expect(q('[data-export-group="format"] [data-ui-field-group]')!.hasAttribute('data-ui-field-group-plain')).toBe(false)
  })

  it('预览区没有圆角框套圆角框：舞台不带内边距与边框，也不再有预览下方的名字 + 尺寸块', async () => {
    await setup(9)
    const stage = q('[data-export-preview-stage]')!
    expect(stage.className).not.toMatch(/\bp-3\b|border/)
    expect(q('[data-export-preview]')!.querySelector('p.font-medium')).toBeNull()
    expect(q('[data-export-preview-size]')!.textContent).toMatch(/mm/)
    // 图是直角：预览区里（容器、舞台、内容盒、图本身）没有任何圆角类，overflow-hidden 的容器也不带
    for (const el of [q('[data-export-preview]')!, stage, ...stage.querySelectorAll<HTMLElement>('*')]) {
      if (el.closest('[data-export-preview-tabs]') || el.closest('[data-export-preview-reset]')) continue
      if (el.className && typeof el.className === 'string' && !el.closest('.pointer-events-none')) {
        expect(el.className, '预览里的图不许有圆角').not.toMatch(/\brounded/)
      }
    }
    expect(q('[data-export-preview] > div')!.className).not.toMatch(/\brounded/)
  })

  it('全部成功：一句「已导出 N 个文件」+ 一个「在文件夹中显示」，逐个文件与核验都折在详情里', async () => {
    const w = window as unknown as Record<string, unknown>
    w.__TAURI_INTERNALS__ = { invoke: vi.fn() }
    try {
      jobOutputs = [out('pdf', 'a.pdf', 'done'), out('png', 'a.png', 'done')]
      await setup(9)
      await click(q('[data-export-start]')!)
      const result = q('[data-export-result]')!
      expect(result.textContent).toContain('已导出 2 个文件')
      expect(result.textContent).not.toContain('a.pdf')
      expect(result.textContent).not.toMatch(/核验/)
      expect(q('[data-dialog-status] [data-export-reveal]'), '动作在摘要行上').toBeTruthy()
      expect(q('[data-dialog-footer] [data-export-reveal]'), '脚部不再重复').toBeNull()
      await openResultDetail()
      expect(result.textContent).toContain('a.pdf')
    } finally {
      delete w.__TAURI_INTERNALS__
    }
  })

  it('有失败：摘要是「N 个成功，M 个失败」，失败原因在详情里', async () => {
    jobStatus = 'partial'
    jobOutputs = [out('pdf', 'a.pdf', 'done'), out('png', 'a.png', 'done'), out('tiff', null, 'failed')]
    await setup(9)
    await click(q('[data-export-start]')!)
    expect(q('[data-export-result]')!.textContent).toContain('2 个成功，1 个失败')
    expect(q('[data-export-result] p.text-danger-content')).toBeNull()
    await openResultDetail()
    expect(q('[data-export-result] p.text-danger-content')).toBeTruthy()
  })

  it('核验未通过的文件不能藏进详情：摘要行上点名数量；「未核验」常态则不上摘要', async () => {
    jobOutputs = [
      {
        ...out('pdf', 'a.pdf', 'done'),
        manifest: {
          manifest_version: 1, format: 'pdf', sha256: 'x', bytes: 10, policy: 'standard', verdict: 'accepted',
          required: ['integrity', 'size'], notes: [],
          checks: { integrity: 'verified', size: 'verified', dpi_tag: 'failed' },
        },
      } as never,
    ]
    await setup(9)
    await click(q('[data-export-start]')!)
    expect(q('[data-export-result]')!.textContent).toContain('1 个文件核验未通过')
    expect(q('[data-export-result]')!.getAttribute('data-notice')).toBe('warn')
  })

  it('核验未通过 + 警告 + 导出期间被编辑：摘要一句话里三样都在，不被优先级链吃掉', async () => {
    jobOutputs = [
      {
        ...out('pdf', 'a.pdf', 'done'),
        manifest: {
          manifest_version: 1, format: 'pdf', sha256: 'x', bytes: 10, policy: 'standard', verdict: 'accepted',
          required: ['integrity', 'size'], notes: [],
          checks: { integrity: 'verified', size: 'verified', dpi_tag: 'failed' },
        },
      } as never,
    ]
    jobWarnings = ['w1', 'w2']
    await setup(9)
    await click(q('[data-export-start]')!)
    // 导出期间文档被改：这个标志由 store 按指纹算，这里直接置位（算法本身另有用例）
    await act(async () => { useExportStore.setState({ editedDuringExport: true }) })
    const text = q('[data-export-result]')!.textContent!
    expect(text).toContain('1 个文件核验未通过')
    expect(text).toContain('2 条提示')
    expect(text).toContain('导出期间排版又改了')
    expect(q('[data-export-result]')!.getAttribute('data-notice')).toBe('warn')
  })
})
