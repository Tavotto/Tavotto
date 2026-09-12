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
import { literal } from '@/i18n'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// 预检埋点走的是 `/api/telemetry/event`；这里只把那一个函数换掉，
// 其余 api 保持真实实现（本文件靠 stubFetch 打桩 /api/export）
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  postTelemetryEvent: vi.fn(() => Promise.resolve({ accepted: true })),
}))

import { ExportDialog } from '@/components/ExportDialog'
import { pixelPreview } from '@/lib/exportRequest'
import { postTelemetryEvent } from '@/lib/api'
import { setTelemetryEnabled } from '@/lib/telemetry'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { renderKey, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { runValidation, useValidationStore } from '@/store/validationStore'
import { bindingFor } from '@/lib/specBinding'
import { toCatalog, useProfileStore } from '@/store/profileStore'
import { resetExportState } from '@/store/exportStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useWorkspaceStore } from '@/store/workspace'
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
let jobStatus: 'done' | 'conflict' = 'done'

function stubFetch() {
  exportBodies = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/api/export/start') || url.endsWith('/api/export')) {
      exportBodies.push(JSON.parse(String(init?.body ?? '{}')))
      return new Response(
        JSON.stringify({
          job_id: 'j1',
          status: jobStatus,
          outputs:
            jobStatus === 'done'
              ? [
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
                ]
              : [],
          warnings: [],
          conflicts: jobStatus === 'conflict' ? ['a.pdf'] : [],
          export_dir: '/out',
          error: null,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
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
async function setupRaster(overrides: { gid: string; prop: string; value: unknown }[]) {
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
  [...document.body.querySelectorAll('label')]
    .find((l) => l.textContent?.trim() === title)
    ?.querySelector('input[type="checkbox"]') as HTMLInputElement
/** 位图分辨率下拉：只在选了位图格式时出现（工作台批次起它坐在文件名那一行右侧，没有「分辨率」文字标签） */
const ppiSelect = () => document.body.querySelector('[role="combobox"][aria-label="位图分辨率"]')
/** 知情确认框：格式那四颗复选框排在它前面，**不能拿页面里的第一颗**——认产品给的锚点 */
const confirmBox = () =>
  document.body.querySelector<HTMLInputElement>('input[data-export-confirm]')

const click = async (el: Element) => {
  await act(async () => {
    ;(el as HTMLElement).click()
    await new Promise<void>((r) => setTimeout(r, 0))
  })
}

beforeEach(() => {
  localStorage.clear()
  jobStatus = 'done'
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
      'PyMuPDF',         // 内部实现名
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

  it('样式检查报告在「高级选项」里，默认收起', async () => {
    await setup(9)
    const details = document.body.querySelector('details') as HTMLDetailsElement
    expect(details, '缺少高级选项').toBeTruthy()
    expect(details.open).toBe(false)
    expect(text()).toContain('样式检查报告')
  })
})

describe('输出范围', () => {
  it('画布模式默认按画布；两个选项都在，不隐藏', async () => {
    await setup(9)
    const radios = [...document.body.querySelectorAll('[role="radio"]')]
    expect(radios.map((r) => r.textContent)).toEqual(['原图尺寸', '当前画布'])
    expect(radios[1].getAttribute('aria-checked')).toBe('true')
  })

  it('快速编辑默认按原图，并说出这次忽略了画布上的哪些变换', async () => {
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
    const radios = [...document.body.querySelectorAll('[role="radio"]')]
    expect(radios[0].getAttribute('aria-checked')).toBe('true')
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
    const original = document.body.querySelector('[role="radio"]') as HTMLButtonElement
    expect(original.hasAttribute('disabled')).toBe(false)
    expect(text()).not.toContain('先选中一张图')
    expect(text()).not.toContain('点选下面的一张图')
  })

  it('源文件不见了：禁用 + **说的是源文件不见了**，不是"先选中一张图"', async () => {
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
    const original = document.body.querySelector('[role="radio"]') as HTMLButtonElement
    expect(original.hasAttribute('disabled')).toBe(true)
    expect(text()).toContain('源文件现在找不到了')
    // 三个原因折成两句的话会说成这一句，用户照做之后按钮还是灰的
    expect(text()).not.toContain('先选中一张图')
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
  it('可用性跟着素材清单变，不是只挂在 figureId 上', async () => {
    await setup(9)
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await act(async () => {
      useUiStore.getState().setExportOpen(false)
    })
    await act(async () => {
      useUiStore.getState().setExportOpen(true)
    })
    const radio = () => document.body.querySelector('[role="radio"]') as HTMLButtonElement
    expect(radio().hasAttribute('disabled')).toBe(false)

    // 对话框开着，素材被删 / 掉线：那颗按钮必须当场灰掉
    await act(async () => {
      useAssetStore.setState({ byId: {} } as never)
    })
    expect(radio().hasAttribute('disabled'), 'memo 只挂 figureId 的话这里还是亮的').toBe(true)
    expect(text()).toContain('源文件现在找不到了')
  })
})

describe('阻断与确认', () => {
  it('有 error 时默认拦住导出，勾选确认后才放行，并写进样式检查报告', async () => {
    await setup(8) // 8pt = 撞绝对下限 → error
    expect(text()).toContain('阻断')

    const go = button('开始导出')!
    expect(go.hasAttribute('disabled')).toBe(true)

    const check = confirmBox()!
    expect(check, '缺少显式确认勾选框').toBeTruthy()
    await act(async () => {
      check.click()
    })

    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    await click(button('开始导出')!)

    expect(exportBodies).toHaveLength(1)
    const report = exportBodies[0].style_check_report as Record<string, unknown>
    // 勾了确认就**必须**留档：确认框上写着这次确认会被记录，
    // 而报告开关是个记住的偏好，用户可能早就关掉了
    expect(report, '确认之后必须生成样式检查报告').toBeTruthy()
    expect((report.profile as Record<string, string>).profile_id).toBe('lab-publication-v1')
    expect(report.forced).toBe(true)
    expect(report.acknowledged).toContain('font-below-absolute-floor')
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

describe('确认只对"这一批"问题有效', () => {
  it('问题集合变了，那个勾必须掉（否则新问题会不经确认被导出）', async () => {
    await setup(8) // 8pt 刻度 → 一条阻断项
    const check = () => confirmBox()!
    await act(async () => {
      check().click()
    })
    expect(check().checked).toBe(true)
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)

    const before = text()

    // 文档被编辑，冒出**第二条**阻断项：确认过的那一批已经不是现在这一批
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
    expect(text(), '这一步得真的改变问题集合，不然这条用例是空的').not.toBe(before)
    const after = confirmBox()
    expect(after, '还应该要确认').toBeTruthy()
    expect(after!.checked, '问题集合变了，勾还留着 = 新问题不经确认就放行').toBe(false)
  })

  it('导出一次之后要重新确认（一次点头只对那一次有效）', async () => {
    await setup(8)
    const check = () => confirmBox()!
    await act(async () => {
      check().click()
    })
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(check().checked, '导出之后那个勾还留着 = 下一次不经确认就放行').toBe(false)
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

describe('阻断闸没有第二条路绕过去', () => {
  it('撞名之后点「覆盖」，不许把同一批阻断项不经确认再导一次', async () => {
    jobStatus = 'conflict'
    await setup(8) // 一条阻断项
    const check = () => confirmBox()!
    await act(async () => {
      check().click()
    })
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].validation).toMatchObject({ policy: 'acknowledged' })

    // 撞名那一次**什么都没写**，界面问的是同一次导出的另一个问题 ——
    // 确认不该被清掉，「覆盖」也就照常可用
    await click(button('覆盖')!)
    expect(exportBodies).toHaveLength(2)
    expect(
      (exportBodies[1].validation as Record<string, unknown>).acknowledged,
      '第二次带着空的 acknowledged = 替用户签了一个他没签过的字',
    ).toEqual((exportBodies[0].validation as Record<string, unknown>).acknowledged)
    expect(exportBodies[1].include_style_check_report).toBe(true)
  })

  it('撞名之后问题集合变了 → 「覆盖」这条路也必须被闸挡住', async () => {
    jobStatus = 'conflict'
    await setup(8)
    const check = () => confirmBox()!
    await act(async () => {
      check().click()
    })
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(button('覆盖'), '这一步得真的停在冲突条上').toBeTruthy()

    // 文档被编辑，冒出**另一条**阻断项：确认过的那一批已经不是现在这一批，
    // 那个勾会被撤销 —— 而「覆盖」按钮没有经过主按钮的 disabled
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
    expect(check().checked, '问题集合变了，勾应该已经掉了').toBe(false)

    await click(button('覆盖')!)
    expect(
      exportBodies,
      '「覆盖」绕过了主按钮的 disabled —— 闸必须在 start() 里',
    ).toHaveLength(1)
  })
})

describe('「能不能导」只有一份判断', () => {
  it('原图不可用时，「重试」这条路也发不出请求', async () => {
    jobStatus = 'conflict'
    await setup(9)
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await act(async () => {
      useUiStore.getState().setExportOpen(false)
    })
    await act(async () => {
      useUiStore.getState().setExportOpen(true)
    })
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].scope).toBe('original')

    // 停在冲突条上时源没了：主按钮会变灰，而「覆盖」不经过它
    await act(async () => {
      useAssetStore.setState({ byId: {} } as never)
    })
    expect(text()).toContain('源文件现在找不到了')
    await click(button('覆盖')!)
    expect(
      exportBodies,
      '咽喉闸少了「原图可不可用」这一条 = 起一个界面刚说不可用的导出',
    ).toHaveLength(1)
  })
})

describe('照抄源位图 vs 引擎重画', () => {
  it('带 override 的位图面板不许报源像素网格（它会被重画）', async () => {
    await setupRaster([])
    expect(text(), '照抄源文件时报的就是源像素网格').toContain('120 × 80')

    await setupRaster([{ gid: 'axes_0', prop: 'fontsize', value: 9 }])
    expect(
      text(),
      '带 override = 引擎重画，拿到的是 PDF；再报源像素网格就是界面与文件各说各的',
    ).not.toContain('120 × 80')
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
    expect(text()).toContain('1 警告')
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

const radios = () => [...document.body.querySelectorAll('[role="radio"]')] as HTMLButtonElement[]

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
  it('快速编辑里默认是那张图的名字，不是画布名；切到画布再切回来各归各', async () => {
    await stage({ panels: [panel], renders: { 'Fig1.pdf': manifestWithTicks(1, 9) } })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await mountDialogs()
    await openDialog()
    expect(filenameInput().value, '面板没起名就用文件 stem').toBe('Fig1')
    await click(radios()[1])
    expect(filenameInput().value).toBe(useDocumentStore.getState().doc.name)
    await click(radios()[0])
    expect(filenameInput().value).toBe('Fig1')
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
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await mountDialogs()
    await openDialog()
    await typeFilename('mine')
    await click(radios()[1])
    expect(filenameInput().value).toBe('mine')
    // 恰好敲回默认值也算改过：切范围仍然不动它
    await typeFilename('Fig1')
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
    expect(header()).toContain('原图尺寸')
    expect(header()).toContain('80 × 60 mm')
    expect(text()).not.toContain('磁盘上的原件')
    expect(document.body.querySelector('[data-export-target] img')).toBeTruthy()
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
    expect(header()).toContain('磁盘上的原件是 75.3 × 58.7 mm')
    expect(header()).toContain('按图幅 80 × 57.6 mm 出图')
    // 权威只有一个：头部之外不再报第二个尺寸
    expect(text().split('80 × 57.6 mm').length - 1).toBe(2)
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

  it('画布：画布名 + 页面尺寸 + 对象数，缩略图是示意不是渲染', async () => {
    await stage({ panels: [panel], renders: { 'Fig1.pdf': manifestWithTicks(1, 9) }, page: { w: 180, h: 120 } })
    await mountDialogs()
    await openDialog()
    expect(header()).toContain(useDocumentStore.getState().doc.name)
    expect(header()).toContain('当前画布')
    expect(header()).toContain('180 × 120 mm')
    expect(header()).toContain('1 个对象')
    expect(document.body.querySelector('[data-export-target] svg')).toBeTruthy()
    expect(document.body.querySelector('[data-export-target] img')).toBeNull()
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
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await mountDialogs()
    await openDialog()
    expect(radios()[0].getAttribute('aria-checked')).toBe('true')
    expect(text()).toContain('导出前检查通过')
    expect(text()).toContain('只算这张图')
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)

    await click(radios()[1])
    expect(text()).toMatch(/\d+ 阻断/)
    expect(text()).toContain('1 警告')
    expect(text()).toContain('整个画布')
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(true)
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
    expect(text()).toContain(`${errorsOn('p1')} 阻断`)
    expect(text()).not.toContain(`${errorsOn('p1') + errorsOn('p2')} 阻断`)
    expect(button('开始导出')!.hasAttribute('disabled')).toBe(true)
    const check = confirmBox()!
    await act(async () => {
      check.click()
    })
    await click(button('开始导出')!)
    const report = exportBodies[0].style_check_report as Record<string, unknown>
    const checks = report.checks as { id: string; object_ids?: string[]; objectIds?: string[] }[]
    const floor = checks.filter((c) => c.id === 'font-below-absolute-floor')
    expect(floor.length, '报告里的阻断项只有这张图的那一条').toBe(1)
    expect(floor[0].object_ids, '别的图的对象不进这份报告').toEqual(['p1'])
  })
})

describe('T33 · 阻断项逐条列出，紧挨着知情确认，每条可定位', () => {
  const list = () => document.body.querySelector('[aria-label="阻断性问题"]')
  const rows = () => [...(list()?.querySelectorAll('[data-blocking-issue]') ?? [])]
  const locateButtons = () => [...document.body.querySelectorAll('button')].filter((b) => b.textContent === '定位')

  it('说清是什么、在哪、当前值 → 要求；警告仍只给数量', async () => {
    await stage({
      panels: [panel],
      renders: { 'Fig1.pdf': manifestWithTicks(1, 8) },
      page: { w: 80, h: 40 },
    })
    await mountDialogs()
    await openDialog()
    expect(list()).toBeTruthy()
    // 8pt 撞两条字号规则：一条元素两行，各说各的规则，各有各的「定位」
    const errors = useValidationStore.getState().issues.filter((i) => i.severity === 'error')
    expect(errors.length).toBeGreaterThan(0)
    expect(rows()).toHaveLength(errors.length)
    const row = rows().find((r) => r.getAttribute('data-blocking-issue') === 'font-below-absolute-floor')!
    expect(row, '每一行带着稳定的规则码').toBeTruthy()
    expect(row.textContent).toContain('字号低于绝对下限')
    expect(row.textContent).toContain('x 刻度 1')
    expect(row.textContent).toContain('8.00pt')
    expect(locateButtons()).toHaveLength(errors.length)
    // 列表紧挨着确认框：确认框是它的下一个兄弟
    expect(list()!.nextElementSibling?.querySelector('input[type="checkbox"]')).toBeTruthy()
    // 警告（page-aspect）不逐条列
    expect(text()).toContain('1 警告')
    expect(text()).not.toContain('页面比例')
  })

  it('超过 5 条只列 5 条，其余交给问题面板', async () => {
    await stage({ panels: [panel], renders: { 'Fig1.pdf': manifestWithTicks(7, 8) } })
    await mountDialogs()
    await openDialog()
    const errors = useValidationStore.getState().issues.filter((i) => i.severity === 'error').length
    expect(errors).toBeGreaterThan(5)
    expect(rows()).toHaveLength(5)
    expect(text()).toContain(`还有 ${errors - 5} 条`)
  })

  it('进不了图内编辑（没有源脚本）：对话框留在原地并说出原因', async () => {
    await stage({ panels: [panel], renders: { 'Fig1.pdf': manifestWithTicks(1, 8) } })
    await mountDialogs()
    await openDialog()
    await click(locateButtons()[0])
    expect(useUiStore.getState().exportOpen).toBe(true)
    expect(useUiStore.getState().statusTone).toBe('error')
  })

  it('定位成功：对话框让开、真的到了那个元素；再点「导出」填过的东西原样回来', async () => {
    await stage({
      panels: [{ ...panel, script: 'fig1.py' }],
      renders: { 'Fig1.pdf': manifestWithTicks(1, 8) },
    })
    await mountDialogs()
    await openDialog()
    // 用户已经填了一些东西
    await typeFilename('mine')
    await click(formatBox('PNG')) // 关掉 PNG
    expect(useUiStore.getState().exportOpen).toBe(true)

    await click(locateButtons()[0])
    expect(useUiStore.getState().exportOpen).toBe(false)
    expect(useWorkspaceStore.getState().mode).toBe('fast_edit')
    expect(useUiStore.getState().elementPanelId).toBe('p1')
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.xticks'])
    expect(useUiStore.getState().statusTone).toBe('info')

    await openDialog()
    expect(filenameInput().value, '让开再回来，文件名还在').toBe('mine')
    expect(formatBox('PNG').checked).toBe(false)
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
    await click(radios()[0].parentElement!.querySelectorAll('[role="radio"]')[1])

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

const originalRadio = () => document.body.querySelector('[role="radio"]') as HTMLButtonElement
const figureOptions = () => [...document.body.querySelectorAll('[role="option"]')] as HTMLElement[]

describe('画布模式下选中的图就是要导的那张（用户反馈 06）', () => {
  it('画布上选中了一个面板：原图尺寸可用，导的就是它', async () => {
    await setupTwo({ select: ['p2'] })
    expect(originalRadio().hasAttribute('disabled'), '选中了面板还说「先选中一张图」').toBe(false)
    expect(text()).not.toContain('先选中一张图')
    await click(originalRadio())
    expect(text()).toContain('65 × 50 mm')
    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].scope).toBe('original')
    expect((exportBodies[0].original as { figure_id: string }).figure_id).toBe('Fig2.pdf')
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

  it('什么都没选、项目里有两张图：列表列出两张，点一张就按它导（不用回画布选）', async () => {
    await setupTwo({ select: [] })
    expect(originalRadio().hasAttribute('disabled')).toBe(true)
    expect(text()).toContain('点选下面的一张图')
    const options = figureOptions()
    expect(options.map((o) => o.textContent)).toEqual(['Fig1', 'Fig2'])
    expect(options.every((o) => o.getAttribute('aria-selected') === 'false')).toBe(true)

    await click(options[1])
    // 点了就是选了：范围切到原图、按钮亮、说的是这张图的尺寸
    const radios = [...document.body.querySelectorAll('[role="radio"]')]
    expect(radios[0].getAttribute('aria-checked')).toBe('true')
    expect(originalRadio().hasAttribute('disabled')).toBe(false)
    expect(figureOptions()[1].getAttribute('aria-selected')).toBe('true')
    expect(text()).toContain('65 × 50 mm')
    expect(text()).not.toContain('点选下面的一张图')

    await click(button('开始导出')!)
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].scope).toBe('original')
    expect((exportBodies[0].original as { figure_id: string }).figure_id).toBe('Fig2.pdf')
    // 点缩略图是这一次导出的选择，不改画布选区
    expect(useSelectionStore.getState().ids).toEqual([])
  })

  it('画布上选中了 p2，列表里再点 Fig1：以点的为准', async () => {
    await setupTwo({ select: ['p2'] })
    await click(originalRadio())
    await click(figureOptions()[0])
    expect(text()).toContain('80 × 60 mm')
    await click(button('开始导出')!)
    expect((exportBodies[0].original as { figure_id: string }).figure_id).toBe('Fig1.pdf')
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

  it('按画布导且当前图可用时列表收起；原图不可用时列表仍在（那句提示得指得到东西）', async () => {
    await setupTwo({ select: ['p2'] })
    // 默认按画布，图又有着落：不摆列表
    expect(figureOptions()).toHaveLength(0)
    await click(originalRadio())
    expect(figureOptions()).toHaveLength(2)
    // 切回画布：收起
    await click([...document.body.querySelectorAll('[role="radio"]')][1])
    expect(figureOptions()).toHaveLength(0)
    // 选中的那张源文件没了：按画布也得把列表摆出来，让用户换一张
    await act(async () => {
      useAssetStore.setState({
        byId: { 'Fig1.pdf': { id: 'Fig1.pdf', kind: 'pdf', mtime: 1 } },
        panels: [{ id: 'Fig1.pdf', kind: 'pdf', mtime: 1 }],
      } as never)
    })
    expect(text()).toContain('源文件现在找不到了')
    expect(figureOptions()).toHaveLength(2)
    await click(figureOptions()[0])
    expect(originalRadio().hasAttribute('disabled')).toBe(false)
    expect(text()).toContain('80 × 60 mm')
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
  })

  it('缩略图：有图内修改的面板挂 store 里那份 SVG，没有的走素材缩略图地址，不发渲染请求', async () => {
    await setupTwo({ select: [] })
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
    expect(eps.closest('label')!.title).toContain('EPS 只能按「原图尺寸」导出单张图')
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
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    await setup(9)
    await act(async () => {
      useAssetStore.setState({
        byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1, script: 'fig1.py' } },
      } as never)
    })
    await click(formatBox('EPS'))
    await click(button('当前画布')!)
    expect(text()).toContain('EPS 只能按「原图尺寸」导出单张图')
    await click(button('开始导出')!)
    expect(exportBodies[0].scope).toBe('canvas')
    expect(exportBodies[0].formats).toEqual(['pdf', 'png'])
    // 再把 PDF / PNG 都取消：只剩一个发不出去的 EPS，主按钮必须灰
    await click(formatBox('PDF'))
    await click(formatBox('PNG'))
    expect((button('开始导出') as HTMLButtonElement).disabled).toBe(true)
  })
})
