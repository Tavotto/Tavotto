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
import { postTelemetryEvent } from '@/lib/api'
import { setTelemetryEnabled } from '@/lib/telemetry'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { renderKey, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
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
