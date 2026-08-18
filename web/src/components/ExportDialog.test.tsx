/**
 * 导出对话框：**规范从 profile 读，阻断项默认拦住导出**。
 *
 * 三条纪律各一组用例：
 *
 * 1. 页宽 / 字号 / DPI 的口径全部来自 profile —— 以前 85/150/180mm 是写死的，
 *    规范一改那三个数字就开始撒谎；
 * 2. error 默认阻止导出，用户显式确认后才放行，且**确认要写进 proof**；
 * 3. warn 与 not_verifiable 必须看得见（不是折在一句「有几个问题」后面）。
 */
import { act } from 'react'
import { literal } from '@/i18n'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExportDialog } from '@/components/ExportDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { renderKey, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
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

function stubFetch() {
  exportBodies = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/api/export')) {
      exportBodies.push(JSON.parse(String(init?.body ?? '{}')))
      return new Response(
        JSON.stringify({ files: [{ name: 'a.pdf', url: '/exports/a.pdf' }],
                         export_dir: '/out', warnings: [] }),
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

describe('规范信息来自 profile', () => {
  it('页宽判定与字号下限都按 profile 显示，不是写死的 85/150', async () => {
    await setup(9)
    expect(text()).toContain('单栏 80mm')
    expect(text()).toContain('lab-publication-v1')
    // 预设的页宽也来自 profile（提示文字在 title 上）
    const hints = [...document.body.querySelectorAll('button')]
      .map((b) => b.getAttribute('title') ?? '')
      .join('|')
    expect(hints).toContain('80mm 单栏')
    expect(hints).toContain('150mm 通栏')
    expect(hints).not.toContain('85mm')
  })

  it('页宽不符时如实说不符，并列出规范里的两个宽度', async () => {
    await setup(9, { w: 123, h: 92 })
    expect(text()).toContain('不符（规范 80/150mm）')
  })

  it('最小有效字号直接摆在面板上', async () => {
    await setup(8.2)
    expect(text()).toContain('最小有效字号')
    expect(text()).toContain('8.2pt')
  })
})

describe('阻断与确认', () => {
  it('有 error 时默认拦住导出，勾选确认后才放行，并写进 proof', async () => {
    await setup(8) // 8pt = 撞绝对下限 → error
    expect(text()).toContain('阻断')

    const go = button('开始导出')!
    expect(go.hasAttribute('disabled')).toBe(true)

    // 打开 proof 留档 + 勾确认
    const proofToggle = document.body.querySelector('[role="switch"]')
    if (proofToggle) await click(proofToggle)
    const check = document.body.querySelector('input[type="checkbox"]') as HTMLInputElement
    expect(check, '缺少显式确认勾选框').toBeTruthy()
    await act(async () => {
      check.click()
    })

    expect(button('开始导出')!.hasAttribute('disabled')).toBe(false)
    await click(button('开始导出')!)

    expect(exportBodies).toHaveLength(1)
    const proof = exportBodies[0].proof as Record<string, unknown>
    expect(proof).toBeTruthy()
    expect((proof.profile as Record<string, string>).profile_id).toBe('lab-publication-v1')
    expect((proof.profile as Record<string, string>).profile_version).toBeTruthy()
    expect(proof.forced).toBe(true)
    expect(proof.acknowledged).toContain('font-below-absolute-floor')
    const checks = proof.checks as { id: string; severity: string }[]
    expect(checks.some((c) => c.id === 'font-below-absolute-floor' && c.severity === 'error')).toBe(
      true,
    )
  })

  it('没有阻断项时直接可导出，proof 里 forced 为 false', async () => {
    await setup(9)
    const proofToggle = document.body.querySelector('[role="switch"]')
    if (proofToggle) await click(proofToggle)
    const go = button('开始导出')!
    expect(go.hasAttribute('disabled')).toBe(false)
    await click(go)
    const proof = exportBodies[0].proof as Record<string, unknown>
    expect(proof.forced).toBe(false)
    expect(proof.acknowledged).toEqual([])
  })
})

describe('warn 与 not_verifiable 必须看得见', () => {
  it('展开后逐条列出，并标出等级', async () => {
    // 80×40 的页面 + 同尺寸面板：只有 page-aspect 这一条 warn（没有阻断项，
    // 所以摘要默认是收起的，点开才看得到明细——这正是要验的那一下）
    await setup(9, { w: 80, h: 40 }, 40)
    const toggle = [...document.body.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('预检：'),
    )
    expect(toggle, '缺少预检摘要').toBeTruthy()
    expect(text()).toContain('1 警告')
    await click(toggle!)
    expect(text()).toContain('页面比例')
    expect(text()).toContain('警告')
  })
})
