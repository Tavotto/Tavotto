/**
 * 属性页按**页面上的实际大小**显示字号与线宽（2026-09-24 用户拍板）。
 *
 * 用户看到的问题：图摆成原生宽度的 60% 之后，同一个标题字号，属性页写 10（脚本里的原始值），
 * 左栏样式面板写 6（页面上读者量到的），问题面板说「当前 6.00 pt」。这里把三处同时挂起来，
 * 对同一个元素量同一个数；再把属性页上几种输入入口（排版控件、通用数字行、批量行、边框卡）
 * 各量一遍：显示 = 脚本值 × 缩放比、输入按页面值、写进 override 的是换回去的脚本值、
 * 局部预览拿到的与 override 是同一个数。
 *
 * 夹具：原生 80 mm 宽的图在页面上 48 mm 宽（缩放比 0.6）；标题脚本值 10 pt → 页面 6 pt，
 * 低于默认规范的绝对下限 8 pt，预检报一条。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { EditableField } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { ProblemPanel } from '@/components/left/ProblemPanel'
import { StylePanel } from '@/components/left/StylePanel'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProfileStore } from '@/store/profileStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { resetPreview, setHistoryMode } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { runValidation, useValidationStore } from '@/store/validationStore'
import { useWorkspaceStore } from '@/store/workspace'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { ElementInspector } from './ElementInspector'

/** 局部预览收到的值：必须与写进 override 的是同一个脚本值（预览贴在按脚本坐标系画的 SVG 上） */
const previewed: { gid: string; prop: string; value: unknown }[] = []
vi.mock('@/store/svgPreviewStore', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/store/svgPreviewStore')>()
  return {
    ...real,
    previewStyle: (gid: string, role: string, prop: string, value: unknown) => {
      previewed.push({ gid, prop, value })
      return real.previewStyle(gid, role, prop, value)
    },
  }
})

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}

const panelAt = (k: number): PanelObject => ({
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  script: 'fig1.py',
  name: 'Fig1',
  nativeW: 80,
  nativeH: 60,
  x: 0,
  y: 0,
  w: 80 * k,
  h: 60 * k,
  overrides: [],
})

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField
const size = (value: number) => f('fontsize', 'number', value, { min: 3, max: 36, step: 0.5, unit: 'pt' })
const text = (value: number, content = 'Title') => [
  f('text', 'text', content),
  size(value),
  f('color', 'color', '#000000'),
  f('weight', 'enum', 'normal', { options: ['normal', 'bold'] }),
  f('style', 'enum', 'normal', { options: ['normal', 'italic'] }),
  f('fontfamily', 'enum', 'serif', { options: ['serif', 'sans-serif'] }),
]
const el = (gid: string, role: string, editable: EditableField[], label = gid) => ({
  gid,
  role,
  label,
  bbox: [0.1, 0.1, 0.2, 0.05],
  draggable: false,
  editable,
})

const manifest = (o: { title?: number; ylabel?: number; tick?: number; spine?: number } = {}) => ({
  stem: 'Fig1',
  size_mm: [80, 60],
  elements: [
    el('axes_0', 'axes', [
      f('spine_linewidth', 'number', o.spine ?? 1.25, { min: 0.1, max: 3, step: 0.1, unit: 'pt' }),
      ...['top', 'right', 'bottom', 'left'].flatMap((s) => [
        f(`spine_${s}_color`, 'color', '#000000'),
        f(`spine_${s}_linewidth`, 'number', o.spine ?? 1.25, { min: 0.1, max: 3, step: 0.1, unit: 'pt' }),
      ]),
    ]),
    el('axes_0.title', 'title', text(o.title ?? 10), '标题'),
    el('axes_0.xlabel', 'axis_label', text(10, 'Time'), 'X 轴标题'),
    el('axes_0.ylabel', 'axis_label', text(o.ylabel ?? 10, 'Y'), 'Y 轴标题'),
    el('axes_0.lines_0', 'line', [
      f('color', 'color', '#1f77b4'),
      f('linewidth', 'number', 2, { min: 0.1, max: 5, step: 0.1, unit: 'pt' }),
    ]),
    el('axes_0.lines_1', 'line', [
      f('color', 'color', '#ff7f0e'),
      f('linewidth', 'number', 2, { min: 0.1, max: 5, step: 0.1, unit: 'pt' }),
    ]),
    el('axes_0.colorbar_0', 'colorbar', [
      f('tick_fontsize', 'number', o.tick ?? 15, { min: 3, max: 36, step: 0.5, unit: 'pt' }),
    ]),
  ],
})

const s = () => useDocumentStore.getState()
const current = () => s().doc.objects.find((o) => o.id === 'p1') as PanelObject
const overrideOf = (gid: string, prop: string) =>
  current().overrides.find((o) => o.gid === gid && o.prop === prop)?.value

async function seed(k = 0.6, m = manifest()) {
  await s().switchDocument(emptyProject(), 'd_page_pt')
  s().commit(literal('准备'), (d) => {
    d.page = { w: 80, h: 60 }
    d.objects = [panelAt(k)]
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
  seedExactRender(panelAt(k), m as never)
  useSelectionStore.getState().set(['p1'])
  useDocumentStore.setState({ past: [], future: [] })
  runValidation()
}

/** 引擎按当前 override 画完回来（真引擎回报的 manifest 就是这些值），再跑一遍检查 */
async function engineReturns(m: ReturnType<typeof manifest>) {
  await act(async () => {
    seedExactRender(current(), m as never)
    runValidation()
  })
}

let root: Root
let host: HTMLDivElement

function Inspector() {
  const panel = useDocumentStore((st) => st.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return <ElementInspector panel={panel} />
}

async function mount(gids: string[]) {
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: gids })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <div data-test="inspector">
          <Inspector />
        </div>
        <div data-test="style">
          <StylePanel />
        </div>
        <div data-test="problems">
          <ProblemPanel />
        </div>
      </TooltipProvider>,
    )
  })
}

const region = (name: string) => host.querySelector(`[data-test="${name}"]`)!
const inspectorSize = () =>
  region('inspector').querySelector<HTMLInputElement>('input[data-inspector-prop="fontsize"]')!
const fieldInput = (prop: string) =>
  region('inspector').querySelector<HTMLInputElement>(`[data-prop="${prop}"] input`)!
const styleInput = (label: string) =>
  region('style').querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!
/** 问题面板这一行说的「当前 X pt」 */
const problemCurrent = () => {
  const row = [...region('problems').querySelectorAll<HTMLElement>('[data-issue-row]')].find(
    (r) => r.getAttribute('data-issue-object') === 'p1' && (r.textContent ?? '').includes('pt'),
  )
  return row ? Number(/([\d.]+) pt/.exec(row.textContent ?? '')?.[1]) : null
}
const titleIssue = () =>
  useValidationStore
    .getState()
    .issues.find((i) => i.objectRef.gid === 'axes_0.title' && i.propertyPath === 'fontsize')

async function type(input: HTMLInputElement, value: string) {
  await act(async () => {
    input.focus()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => {
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
  })
}

beforeEach(() => {
  previewed.length = 0
  resetPreview()
  setHistoryMode('gesture')
  document.body.innerHTML = ''
  useRenderStore.getState().clear()
  useUiStore.setState({
    problemFilter: null,
    problemScope: 'document',
    problemCursor: null,
    leftTab: 'style',
    leftOpen: true,
    layout: 'wide',
  })
  useWorkspaceStore.getState().clear()
  useSelectionStore.getState().clear()
  useValidationStore.setState({ results: [], issues: [], ready: false, failed: false, running: false })
  useProfileStore.setState({ styles: [], loaded: true, error: null })
})

afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
  useUiStore.setState({ selectedGids: [] })
})

describe('三处对同一个元素显示同一个数（属性页 / 样式面板 / 问题面板）', () => {
  it('缩放比 0.6：标题脚本 10 pt → 三处都是 6；属性页输入 8.5 → override 14.17，三处都是 8.5、问题清零', async () => {
    await seed(0.6)
    await mount(['axes_0.title'])
    expect(titleIssue(), '夹具该让预检报出这一条').toBeTruthy()
    expect(inspectorSize().value).toBe('6')
    expect(styleInput('标题字号').value).toBe('6')
    expect(problemCurrent()).toBe(6)

    const before = s().past.length
    await type(inspectorSize(), '8.5')
    expect(overrideOf('axes_0.title', 'fontsize')).toBe(14.17)
    expect(s().past.length - before, '一次输入一条历史').toBe(1)

    await engineReturns(manifest({ title: 14.17 }))
    expect(inspectorSize().value).toBe('8.5')
    expect(styleInput('标题字号').value).toBe('8.5')
    expect(titleIssue(), '8.5 pt 已经合规').toBeUndefined()

    // ⌘Z 一次回到原样，显示跟着回去
    await act(async () => {
      expect(s().undo()).not.toBeNull()
    })
    expect(overrideOf('axes_0.title', 'fontsize')).toBeUndefined()
    await engineReturns(manifest({ title: 10 }))
    expect(inspectorSize().value).toBe('6')
    await act(async () => {
      expect(s().redo()).not.toBeNull()
    })
    expect(overrideOf('axes_0.title', 'fontsize')).toBe(14.17)
  })

  it('样式面板里改，属性页读到的是同一个页面值（两边共用一个写入器）', async () => {
    await seed(0.6)
    await mount(['axes_0.title'])
    await type(styleInput('标题字号'), '9')
    expect(overrideOf('axes_0.title', 'fontsize')).toBe(15)
    await engineReturns(manifest({ title: 15 }))
    expect(inspectorSize().value).toBe('9')
  })

  it('缩放比 1.33 的两个关键值：输入 8.5 → 三处 8.5、不报；输入 8 → 三处 8.01、不报（真实渲染 8.0066 pt，严格高于 8）', async () => {
    await seed(1.33, manifest({ title: 5 }))
    await mount(['axes_0.title'])
    expect(inspectorSize().value).toBe('6.65')
    expect(problemCurrent()).toBe(6.65)

    await type(inspectorSize(), '8.5')
    expect(overrideOf('axes_0.title', 'fontsize')).toBe(6.39)
    await engineReturns(manifest({ title: 6.39 }))
    expect(inspectorSize().value).toBe('8.5')
    expect(styleInput('标题字号').value).toBe('8.5')
    expect(titleIssue()).toBeUndefined()

    await type(inspectorSize(), '8')
    expect(overrideOf('axes_0.title', 'fontsize')).toBe(6.02)
    await engineReturns(manifest({ title: 6.02 }))
    expect(inspectorSize().value).toBe('8.01')
    expect(styleInput('标题字号').value).toBe('8.01')
    expect(titleIssue(), '8.0066 pt 严格高于绝对下限 8 pt').toBeUndefined()
  })
})

describe('属性页的各个入口都按页面值进出', () => {
  it('通用数字行（色条刻度字号）：显示 15 × 0.6 = 9；输入 7.5 → 12.5；界换到页面上（输入 1 钳到 3 × 0.6）', async () => {
    await seed(0.6)
    await mount(['axes_0.colorbar_0'])
    const input = () => fieldInput('tick_fontsize')
    expect(input().value).toBe('9')
    await type(input(), '7.5')
    expect(overrideOf('axes_0.colorbar_0', 'tick_fontsize')).toBe(12.5)
    await type(input(), '1')
    expect(overrideOf('axes_0.colorbar_0', 'tick_fontsize'), '钳到页面下界 1.8 = 脚本下界 3').toBe(3)
  })

  it('多选批量：两个轴标题一致时显示页面值、写回脚本值；不一致时是「多个值」', async () => {
    await seed(0.6)
    await mount(['axes_0.xlabel', 'axes_0.ylabel'])
    expect(inspectorSize().value).toBe('6')
    await type(inspectorSize(), '9')
    expect(overrideOf('axes_0.xlabel', 'fontsize')).toBe(15)
    expect(overrideOf('axes_0.ylabel', 'fontsize')).toBe(15)
    await act(async () => root.unmount())

    await seed(0.6, manifest({ ylabel: 12 }))
    await mount(['axes_0.xlabel', 'axes_0.ylabel'])
    expect(inspectorSize().value, '「多个值」不拿第一个冒充全部').toBe('')
  })

  it('多选批量的通用行（两条曲线的线宽）：显示页面值，写回脚本值', async () => {
    await seed(0.6)
    await mount(['axes_0.lines_0', 'axes_0.lines_1'])
    // 两条曲线的公共字段只有颜色与线宽：唯一一个非取色的输入框就是线宽
    const lw = () =>
      region('inspector').querySelector<HTMLInputElement>('input:not([type="color"])')!
    expect(lw().value).toBe('1.2')
    await type(lw(), '0.9')
    expect(overrideOf('axes_0.lines_0', 'linewidth')).toBe(1.5)
    expect(overrideOf('axes_0.lines_1', 'linewidth')).toBe(1.5)
  })

  it('边框卡：「全部」与逐边是同一种单位，四边一致时不报「多个值」，写回脚本值', async () => {
    await seed(0.5)
    await mount(['axes_0'])
    const linked = fieldInput('spine_linewidth')
    expect(linked.value).toBe('0.63')
    await type(linked, '1')
    expect(overrideOf('axes_0', 'spine_linewidth')).toBe(2)
  })

  it('局部预览拿到的与写进 override 的是同一个脚本值（曲线线宽：页面 1.2 = 脚本 2）', async () => {
    await seed(0.6)
    await mount(['axes_0.lines_0'])
    expect(fieldInput('linewidth').value).toBe('1.2')
    await type(fieldInput('linewidth'), '0.9')
    const wrote = overrideOf('axes_0.lines_0', 'linewidth')
    expect(wrote).toBe(1.5)
    const seen = previewed.filter((p) => p.gid === 'axes_0.lines_0' && p.prop === 'linewidth')
    expect(seen.length).toBeGreaterThan(0)
    expect(seen.every((p) => p.value === wrote)).toBe(true)
  })

  it('缩放比为 1：显示脚本值、原样写（不引入任何换算与取整）', async () => {
    await seed(1, manifest({ title: 8.33 }))
    await mount(['axes_0.title'])
    expect(inspectorSize().value).toBe('8.33')
    await type(inspectorSize(), '8.75')
    expect(overrideOf('axes_0.title', 'fontsize')).toBe(8.75)
  })
})
