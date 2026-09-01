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
import { runValidation } from '@/store/validationStore'
import { bindingFor } from '@/lib/specBinding'
import { toCatalog, useProfileStore } from '@/store/profileStore'
import { resetExportState } from '@/store/exportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { emptyProject, type PanelObject } from '@/types/document'

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
  it('文件名在最上方，且预览出这次会写哪几个文件', async () => {
    await setup(9)
    const inputs = [...document.body.querySelectorAll('input')].filter(
      (i) => i.type !== 'checkbox',
    )
    // 第一个可输入的控件就是文件名（`d_export` 文档名）
    expect((inputs[0] as HTMLInputElement).value).toBe(useDocumentStore.getState().doc.name)
    expect(text()).toContain('.pdf')
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

  it('没有当前图时「原图尺寸」禁用，并**说出原因**（不隐藏、不静默改画布）', async () => {
    await setup(9)
    const original = document.body.querySelector('[role="radio"]') as HTMLButtonElement
    expect(original.hasAttribute('disabled')).toBe(true)
    expect(text()).toContain('先选中一张图')
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

    const check = document.body.querySelector('input[type="checkbox"]') as HTMLInputElement
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
    const check = () => document.body.querySelector('input[type="checkbox"]') as HTMLInputElement
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
    const after = document.body.querySelector('input[type="checkbox"]') as HTMLInputElement | null
    expect(after, '还应该要确认').toBeTruthy()
    expect(after!.checked, '问题集合变了，勾还留着 = 新问题不经确认就放行').toBe(false)
  })

  it('导出一次之后要重新确认（一次点头只对那一次有效）', async () => {
    await setup(8)
    const check = () => document.body.querySelector('input[type="checkbox"]') as HTMLInputElement
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
    await click(button('PNG')!)          // 取消 PNG，只剩 PDF
    expect(text()).not.toContain('分辨率')
    await click(button('开始导出')!)
    expect(exportBodies[0].ppi).toBeNull()
    expect(exportBodies[0].formats).toEqual(['pdf'])
  })

  it('选了位图才出现分辨率，且发的是数字', async () => {
    await setup(9)
    expect(text()).toContain('分辨率')
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
    const check = () => document.body.querySelector('input[type="checkbox"]') as HTMLInputElement
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
    const check = () => document.body.querySelector('input[type="checkbox"]') as HTMLInputElement
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
