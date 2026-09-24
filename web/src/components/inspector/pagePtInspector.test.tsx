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
import { ElementQuickActions } from '@/canvas/context-bar/ElementBar'
import { QuickEdit } from '@/canvas/QuickEdit'
import { useQuickEdit } from '@/canvas/quickEditStore'
import { useEngineSync } from '@/hooks/useEngineSync'
import { panelScale } from '@/lib/preflight'
import { finishActiveGesture } from '@/store/gestureCoordinator'
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

const manifest = (
  o: { title?: number; ylabel?: number; tick?: number; spine?: number; legend?: boolean } = {},
) => ({
  stem: 'Fig1',
  size_mm: [80, 60],
  elements: [
    el('axes_0', 'axes', [
      ...['bottom', 'top', 'left', 'right'].map((side) => f(`ticks_${side}`, 'bool', side === 'bottom' || side === 'left')),
      f('spine_linewidth', 'number', o.spine ?? 1.25, { min: 0.1, max: 3, step: 0.1, unit: 'pt' }),
      ...['top', 'right', 'bottom', 'left'].flatMap((s) => [
        f(`spine_${s}_color`, 'color', '#000000'),
        f(`spine_${s}_linewidth`, 'number', o.spine ?? 1.25, { min: 0.1, max: 3, step: 0.1, unit: 'pt' }),
      ]),
    ]),
    ...['x', 'y'].map((axis) =>
      el(`axes_0.${axis}ticks`, 'ticks', [
        size(10),
        f('direction', 'enum', 'out', { options: ['out', 'in', 'inout'], group: '刻度线' }),
        f('length', 'number', 5, { min: 0, max: 12, step: 0.5, unit: 'pt', group: '刻度线' }),
        f('width', 'number', 1, { min: 0.1, max: 3, step: 0.1, unit: 'pt', group: '刻度线' }),
      ]),
    ),
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
    ...(o.legend
      ? [
          el('axes_0.legend', 'legend', [
            f('loc', 'enum', 'best', { options: ['best', 'upper right', 'lower left'] }),
            size(10),
          ]),
        ]
      : []),
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

  it('刻度卡（子图页）：刻度线长度显示 5 × 0.6 = 3，输入 4.5 写回 7.5', async () => {
    await seed(0.6)
    await mount(['axes_0'])
    const len = () => region('inspector').querySelector<HTMLInputElement>('input[data-inspector-prop="length"]')!
    expect(len().value).toBe('3')
    await type(len(), '4.5')
    expect(overrideOf('axes_0.xticks', 'length')).toBe(7.5)
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

describe('画布上的两个快捷入口也按页面值进出', () => {
  async function mountAlone(node: React.ReactNode) {
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(<TooltipProvider>{node}</TooltipProvider>)
    })
  }
  /** 这几个入口里唯一的非取色数字框 */
  const numberInput = () =>
    [...document.querySelectorAll<HTMLInputElement>('input')].find((i) => i.type !== 'color')!

  it('浮动工具条（曲线线宽）：显示 1.2，输入 0.9 → 1.5；写死的下界 0.1 也换到页面上', async () => {
    await seed(0.6)
    useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.lines_0'] })
    function Bar() {
      const panel = useDocumentStore((st) => st.doc.objects.find((o) => o.id === 'p1')) as PanelObject
      return <ElementQuickActions panel={panel} gid="axes_0.lines_0" />
    }
    await mountAlone(<Bar />)
    expect(numberInput().value).toBe('1.2')
    await type(numberInput(), '0.9')
    expect(overrideOf('axes_0.lines_0', 'linewidth')).toBe(1.5)
    await type(numberInput(), '0.01')
    expect(overrideOf('axes_0.lines_0', 'linewidth'), '钳到页面下界 0.06 = 脚本 0.1').toBe(0.1)
  })

  it('右键快捷编辑（图例字号）：显示 10 × 0.6 = 6，输入 7.5 → 12.5', async () => {
    await seed(0.6, manifest({ legend: true }))
    await mountAlone(<QuickEdit />)
    await act(async () => {
      useQuickEdit.getState().open({ kind: 'element', panelId: 'p1', gid: 'axes_0.legend' }, 120, 80)
    })
    expect(numberInput().value).toBe('6')
    await type(numberInput(), '7.5')
    expect(overrideOf('axes_0.legend', 'fontsize')).toBe(12.5)
    await act(async () => useQuickEdit.getState().close())
  })
})

/**
 * 事务里的连续写入 × 事务收尾修正（#543 `registerTxnFinalizer`）。
 *
 * 能局部预览的字段（曲线线宽）连续改是一轮手势 = 一次事务（`useFieldGesture`，安静 450 ms 收尾）。
 * 事务进行中渲染回来的图幅变了（磁盘 PDF 73.3 mm → 脚本 figsize 80 mm）时，`nativeW` 与 `w` 的
 * 同步推迟到收尾、并进同一条历史。问题是：换算用的缩放比取写入那一刻的，还是收尾修正之后的？
 *
 * **两者相等，取写入那一刻的就对**：收尾修正按同一比例同时改 `w` 与 `nativeW`（「缩放比不变」，
 * `useEngineSync.applyNativeSizeFixes`），所以写入器每次写入时读当下文档的缩放比即可，不需要
 * 另记一份「事务开始时的缩放比」，也不需要收尾后回头重算 override。这条用例钉住这个前提：
 * 收尾前后缩放比不变、override 按它换出来、松手后读回来就是输入的页面值、整轮一条历史，
 * 撤销 / 重做不改变这个数。收尾修正哪天不再保持缩放比，这里就红。
 */
describe('事务里连续写线宽，收尾修正改了图幅：缩放比前后一致，读回来仍是输入的页面值', () => {
  it('73.3 mm 的图摆成 60%，改线宽的手势中途渲染回来 80 mm 图幅：override 按 0.6 换算，收尾后缩放比仍 0.6，一条历史', async () => {
    const p: PanelObject = { ...panelAt(0.6), nativeW: 73.3, nativeH: 55, w: 73.3 * 0.6, h: 55 * 0.6 }
    const disk = { ...manifest(), size_mm: [73.3, 55] }
    await s().switchDocument(emptyProject(), 'd_page_pt_txn')
    s().commit(literal('准备'), (d) => {
      d.page = { w: 80, h: 60 }
      d.objects = [{ ...p }]
    })
    useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
    seedExactRender(p, disk as never)
    useSelectionStore.getState().set(['p1'])
    useDocumentStore.setState({ past: [], future: [] })

    function Probe() {
      useEngineSync()
      return null
    }
    useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.lines_0'] })
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(
        <TooltipProvider>
          <Probe />
          <div data-test="inspector">
            <Inspector />
          </div>
        </TooltipProvider>,
      )
    })
    const before = s().past.length
    const k0 = panelScale(current())
    expect(k0).toBeCloseTo(0.6, 9)
    const lw = () => fieldInput('linewidth')
    expect(lw().value).toBe('1.2')

    // 手势开始：第一次写入当场开一轮事务（能预览的字段）
    await type(lw(), '0.9')
    expect(s().txn, '手势这一轮还开着').not.toBeNull()
    expect(overrideOf('axes_0.lines_0', 'linewidth')).toBe(1.5)

    // 事务进行中渲染回来：图幅 80 × 60（脚本 figsize），同步推迟到收尾
    const scriptSize = { ...manifest(), size_mm: [80, 60] }
    await act(async () => {
      seedExactRender(current(), scriptSize as never)
    })
    expect(current().nativeW, '事务开着时不改图幅').toBe(73.3)

    // 同一轮里再改一次（键盘步进，焦点还在框里）
    await act(async () => {
      lw().focus()
      lw().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }))
    })
    expect(s().txn, '仍是同一轮').not.toBeNull()
    const wrote = overrideOf('axes_0.lines_0', 'linewidth') as number
    // 这一版（第二次写入之后的变体）也在事务里画回来了：收尾修正按它换图幅
    const drawn = {
      ...scriptSize,
      elements: scriptSize.elements.map((e) =>
        e.gid === 'axes_0.lines_0'
          ? { ...e, editable: e.editable.map((f) => (f.prop === 'linewidth' ? { ...f, value: wrote } : f)) }
          : e,
      ),
    }
    await act(async () => {
      seedExactRender(current(), drawn as never)
    })
    expect(current().nativeW, '仍然推迟').toBe(73.3)

    await act(async () => {
      finishActiveGesture()
    })
    expect(s().txn).toBeNull()
    // 收尾修正：原生图幅换成 80，页面尺寸按同一比例跟着走，缩放比不变
    expect(current().nativeW).toBe(80)
    expect(current().w).toBeCloseTo(48, 9)
    expect(panelScale(current())).toBeCloseTo(k0, 9)
    expect(s().past.length - before, '两次写入与图幅同步是同一条历史').toBe(1)
    expect(overrideOf('axes_0.lines_0', 'linewidth')).toBe(wrote)

    // 读回来的页面值 = 写入那一刻输入的值
    const shown = Number(lw().value)
    expect(shown).toBe(Number((wrote * 0.6).toFixed(2)))
    expect(shown, '0.9 再步进一格').toBe(1)

    await act(async () => {
      expect(s().undo()).not.toBeNull()
    })
    expect(overrideOf('axes_0.lines_0', 'linewidth')).toBeUndefined()
    expect(panelScale(current())).toBeCloseTo(k0, 9)
    await act(async () => {
      expect(s().redo()).not.toBeNull()
    })
    expect(overrideOf('axes_0.lines_0', 'linewidth')).toBe(wrote)
    expect(panelScale(current())).toBeCloseTo(k0, 9)
  })
})
