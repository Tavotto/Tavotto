/**
 * 右栏标题的身份行（审计 T01）与图内修改数徽标（审计 T07）。
 *
 * T01 验收原话：**每次都能指出当前对象和修改范围**。此前标题只有一个名字，
 * 而名字是用户内容（文件名 / 那句文字），回答不了「我在改的是文字、面板还是
 * 标注」。类型与名字分两格写。
 *
 * T07 验收原话：**对象数、问题数和修改数不会混淆**。此前「22」孤零零挂在
 * 「编辑图内元素」按钮旁边，与左栏元素树标题上的数字长得一模一样。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchReadiness: vi.fn().mockResolvedValue(null),
}))

import { literal, t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { Inspector } from './Inspector'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type CanvasObject, type PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
globalThis.fetch = vi.fn(async () => new Response('{}', { status: 200 })) as typeof fetch

const panel: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1_kinetics.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 0,
  y: 0,
  w: 80,
  h: 60,
  script: 'fig1.py',
} as PanelObject

const textObj = {
  id: 't1',
  type: 'text',
  text: '图注：反应速率随温度上升',
  x: 5,
  y: 5,
  w: 40,
  h: 8,
  sizePt: 9,
  bold: false,
  color: '#000000',
  align: 'left',
} as unknown as CanvasObject

const arrowObj = {
  id: 'a1',
  type: 'arrow',
  x: 5,
  y: 30,
  w: 20,
  h: 5,
  start: { rx: 0, ry: 0.5 },
  end: { rx: 1, ry: 0.5 },
  strokePt: 1,
  color: '#111111',
  head: 'end',
} as unknown as CanvasObject

const manifest = {
  stem: 'Fig1_kinetics',
  size_mm: [80, 60],
  elements: [
    { gid: 'figure', role: 'figure', label: '整张图', bbox: [0, 0, 1, 1], draggable: false, editable: [] },
    {
      gid: 'axes_0.title',
      role: 'title',
      label: '标题',
      bbox: [0.1, 0.02, 0.8, 0.08],
      draggable: false,
      editable: [{ prop: 'fontsize', type: 'number', value: 9 }],
    },
    { gid: 'axes_0', role: 'axes', label: '子图 1', bbox: [0.1, 0.1, 0.8, 0.8], draggable: false, editable: [] },
    {
      gid: 'axes_0.xticks',
      role: 'ticks',
      label: 'X 刻度文字',
      bbox: [0.1, 0.9, 0.8, 0.05],
      draggable: false,
      editable: [{ prop: 'fontsize', type: 'number', value: 8 }],
    },
    {
      gid: 'axes_0.xticklabels_1',
      role: 'ticklabel',
      label: '刻度 “10”',
      bbox: [0.2, 0.9, 0.05, 0.05],
      draggable: false,
      editable: [{ prop: 'text', type: 'text', value: '10' }],
    },
    {
      gid: 'axes_0.legend',
      role: 'legend',
      label: '图例',
      bbox: [0.6, 0.6, 0.3, 0.2],
      draggable: false,
      editable: [{ prop: 'fontsize', type: 'number', value: 8 }],
    },
    {
      gid: 'axes_0.legend.texts_0',
      role: 'legend_text',
      label: '图例项 “Catalyst (k = 0.1…”',
      bbox: [0.6, 0.6, 0.3, 0.1],
      draggable: false,
      editable: [{ prop: 'text', type: 'text', value: 'Catalyst (k = 0.125 $\\mathrm{min^{-1}}$)' }],
    },
    {
      gid: 'axes_0.lines_0',
      role: 'line',
      label: '曲线 “Catalyst (k = 0.1…”',
      bbox: [0.1, 0.1, 0.8, 0.8],
      draggable: false,
      editable: [{ prop: 'label', type: 'text', value: 'Catalyst (k = 0.125 $\\mathrm{min^{-1}}$)' }],
    },
  ],
}

let host: HTMLDivElement
let root: Root

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <Inspector />
      </TooltipProvider>,
    )
  })
  await act(async () => {})
}

const kind = () => document.querySelector('[data-object-kind]')?.textContent ?? null
const title = () => document.querySelector('h2')?.textContent ?? null
const badge = () => document.querySelector('[data-override-badge]')?.textContent ?? null

async function seed(objects: CanvasObject[], select: string[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_identity')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 100, h: 80 }
    d.objects = objects
  })
  useAssetStore.setState({
    byId: { 'Fig1_kinetics.pdf': { id: 'Fig1_kinetics.pdf', mtime: 1 } },
  } as never)
  useSelectionStore.getState().set(select)
}

beforeEach(async () => {
  document.body.innerHTML = ''
  localStorage.clear()
  useUiStore.setState({ rightTab: 'properties', elementPanelId: null, selectedGids: [] })
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('标题说得出「我在改的是什么」（T01）', () => {
  it('画布文字：类型是文字，名字是那句话', async () => {
    await seed([textObj], ['t1'])
    await mount()
    expect(kind()).toBe(t('objectType.text', { ns: 'common' }))
    expect(title()).toContain('图注：反应速率随温度上升')
    // 类型不是从名字里读出来的：两格分开写
    expect(kind()).not.toBe(title())
  })

  it('面板：类型是图片 / 面板，名字是文件名', async () => {
    await seed([panel], ['p1'])
    await mount()
    expect(kind()).toBe(t('objectType.panel', { ns: 'common' }))
    expect(title()).toContain('Fig1_kinetics')
  })

  it('标注：类型跟着对象走，不是所有对象都写同一个词', async () => {
    await seed([arrowObj], ['a1'])
    await mount()
    expect(kind()).toBe(t('objectType.arrow', { ns: 'common' }))
  })

  it('图内编辑且没选元素时，标题标出「整张图」这一层', async () => {
    await seed([panel], ['p1'])
    seedExactRender(panel, manifest as never)
    useUiStore.getState().setElementPanel('p1')
    await mount()
    expect(kind()).toBe(t('role.figure', { ns: 'inspector' }))
  })

  it('多选时不硬套一个类型', async () => {
    await seed([textObj, arrowObj], ['t1', 'a1'])
    await mount()
    expect(document.querySelector('[data-object-kind]')).toBeNull()
  })
})

describe('右栏的壳（2026-09-15 全面打磨 S2 / S3 / S4）', () => {
  /**
   * 对象页与图内元素页是**两个**身份头（`Inspector.tsx` 里各写一份）：只量一个的话，
   * 另一个退回 11px 时判据照样绿。两个都要点名。
   */
  it('身份头的标题与分区标题同一档：不比它下面的组标题小一号（S2 · 对象页）', async () => {
    await seed([panel], ['p1'])
    await mount()
    const h2 = document.querySelector('h2')!
    // `type-section` = 12/500/ink，与「位置与尺寸」那一档同一个角色；
    // 此前它是 text-xs（11）+ font-medium，对象名比它下面的分区标题还小
    expect(h2.className).toContain('type-section')
    expect(h2.className).not.toContain('text-xs')
  })

  it('身份头的标题与分区标题同一档（S2 · 图内元素页）', async () => {
    await seed([panel], ['p1'])
    seedExactRender(panel, manifest as never)
    useUiStore.getState().setElementPanel('p1')
    useUiStore.setState({ selectedGids: ['axes_0.title'] })
    await mount()
    const h2 = document.querySelector('h2')!
    expect(h2.textContent).toBeTruthy()
    expect(h2.className).toContain('type-section')
    expect(h2.className).not.toContain('text-xs')
  })

  it('图钉：状态靠图形说，不靠常驻的灰块（S3）', async () => {
    await seed([panel], ['p1'])
    await mount()
    const pinOf = () => document.querySelector<HTMLButtonElement>('button[aria-pressed]')!
    const pin = pinOf()
    // 默认就是钉住的：此前 `active` 让它常驻一块 ink 10% 的底，
    // 是整栏唯一一块常亮的背景
    expect(pin.getAttribute('aria-pressed')).toBe('true')
    expect(pin.className).not.toContain('bg-selected')
    expect(pin.querySelector('svg')!.getAttribute('class')).toContain('icon-pin')
    // 钉住 = 图标集里图钉的实心孪生（ADR 0052）：钉身是一条 fill=currentColor 的实心路径
    expect(pin.querySelector('svg path[fill="currentColor"]')).not.toBeNull()
    await act(async () => pin.click())
    const after = pinOf()
    expect(after.getAttribute('aria-pressed')).toBe('false')
    // 取消钉住换的是**图形**（回到线框图钉），不是底色
    expect(after.querySelector('svg')!.getAttribute('class')).toContain('icon-pin')
    expect(after.querySelector('svg path[fill="currentColor"]')).toBeNull()
    expect(after.className).not.toContain('bg-selected')
  })

  it('页签一律纯文字：改图助手那一枚不再带 Sparkles（S4）', async () => {
    await seed([panel], ['p1'])
    await mount()
    const tabs = [...document.querySelectorAll('[data-inspector-tab]')]
    expect(tabs.length).toBeGreaterThan(1)
    for (const tab of tabs) expect(tab.querySelector('svg')).toBeNull()
  })
})

describe('图内元素的头部图标按角色（2026-09-12 critique P3）', () => {
  it('选中标题时是文字图标，不是面板那个图片图标；整张图是 Fullscreen（外框含内容区）', async () => {
    await seed([panel], ['p1'])
    seedExactRender(panel, manifest as never)
    useUiStore.getState().setElementPanel('p1')
    useUiStore.setState({ selectedGids: ['axes_0.title'] })
    await mount()
    const icon = document.querySelector('header svg')!
    expect(icon.getAttribute('class')).toContain('icon-type')
    expect(icon.getAttribute('class')).not.toContain('icon-image')
    // 与元素树同一张表：树里标题也是 Type
    useUiStore.setState({ selectedGids: [] })
    await act(async () => {})
    expect(document.querySelector('header svg')!.getAttribute('class')).toContain('icon-fullscreen')
  })
})

describe('图内修改数说清是修改数（T07）', () => {
  const withOverrides = (n: number): PanelObject => ({
    ...panel,
    overrides: Array.from({ length: n }, (_, i) => ({
      gid: `axes_0.e${i}`,
      prop: 'fontsize',
      value: 7,
    })) as PanelObject['overrides'],
  })

  it('徽标带单位，不是一个孤零零的数字', async () => {
    const p = withOverrides(22)
    await seed([p], ['p1'])
    seedExactRender(p, manifest as never)
    await mount()
    expect(badge()).toBe(t('element.modifiedCount', { ns: 'inspector', count: 22 }))
    expect(badge()).not.toBe('22')
  })

  it('一条修改都没有时不摆这个徽标', async () => {
    await seed([panel], ['p1'])
    seedExactRender(panel, manifest as never)
    await mount()
    expect(document.querySelector('[data-override-badge]')).toBeNull()
  })
})

/**
 * 2026-09-13 审计 B48 / B50 / B51：归属进面包屑（图例项 → 图例、刻度文字 → X 轴刻度），
 * 标题只显示可读文本（mathtext 源码留在名称框里）。
 */
describe('归属与可读标题', () => {
  const crumbs = () => document.querySelector('header p span[title]')?.getAttribute('title') ?? ''

  it('刻度文字：面包屑是「图 / 子图 / X 轴刻度」，标题是那一个刻度', async () => {
    await seed([panel], ['p1'])
    seedExactRender(panel, manifest as never)
    useUiStore.getState().setElementPanel('p1')
    useUiStore.setState({ selectedGids: ['axes_0.xticklabels_1'] })
    await mount()
    expect(crumbs()).toBe('Fig1_kinetics.pdf / 子图 1 / X 轴刻度 / 刻度 “10”')
    expect(title()).toBe('刻度 “10”')
  })

  it('图例项：面包屑里有「图例」这一级，标题把 mathtext 换成可读文本', async () => {
    await seed([panel], ['p1'])
    seedExactRender(panel, manifest as never)
    useUiStore.getState().setElementPanel('p1')
    useUiStore.setState({ selectedGids: ['axes_0.legend.texts_0'] })
    await mount()
    expect(crumbs()).toContain('/ 图例 /')
    expect(title()).toBe('图例项 “Catalyst (k = 0.125 min⁻¹)”')
    expect(title()).not.toContain('$')
  })

  it('曲线：标题按 `label` 字段补全被引擎截断的名字，并去掉数学源码', async () => {
    await seed([panel], ['p1'])
    seedExactRender(panel, manifest as never)
    useUiStore.getState().setElementPanel('p1')
    useUiStore.setState({ selectedGids: ['axes_0.lines_0'] })
    await mount()
    expect(title()).toBe('曲线 “Catalyst (k = 0.125 min⁻¹)”')
    // 子图直属的元素没有中间那一级
    expect(crumbs()).toBe('Fig1_kinetics.pdf / 子图 1 / 曲线 “Catalyst (k = 0.125 min⁻¹)”')
  })
})

/**
 * 2026-09-14 审计 A4：面包屑每一级祖先都能点，往上走的路就是它本身——
 * 「所属子图」「所属系列」那种再写一行的链接删掉（同一枚回转箭头曾同时表示上到子图、
 * 下到 X / Y 刻度）。
 */
describe('面包屑可点', () => {
  const crumbButtons = () =>
    [...document.querySelectorAll<HTMLButtonElement>('header p button[data-crumb]')]

  it('刻度文字：三级祖先各是一颗按钮，点「子图 1」选中它；「所属子图」那一行不再出现', async () => {
    await seed([panel], ['p1'])
    seedExactRender(panel, manifest as never)
    useUiStore.getState().setElementPanel('p1')
    useUiStore.setState({ selectedGids: ['axes_0.xticklabels_1'] })
    await mount()
    expect(crumbButtons().map((b) => b.getAttribute('data-crumb'))).toEqual([
      'figure',
      'axes_0',
      'axes_0.xticks',
    ])
    expect(document.body.textContent).not.toContain('所属子图')
    await act(async () => crumbButtons()[1].click())
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0'])
  })
})
