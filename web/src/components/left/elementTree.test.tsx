/**
 * 图内元素树（审计 T08）。验收原话：**从图上点击对象后，树中对应行可见并
 * 高亮**。
 *
 * 树默认展开到语义聚类的成员为止，刻度组（与柱形系列、图例这类成员自带的
 * 下一层）是收起的（2026-09-13 审计 B44）——审计里选中的刻度就藏在里面，
 * 用户得自己一层层翻。这里量：选中一个深层 gid 之后，那一行
 * 出现在扁平化后的行里，并且带着选中标记。
 *
 * 另外两条：搜索占位语不再写内部标识（gid 仍然搜得到，只是不摆进默认文案）、
 * 标题上的计数带单位（审计 T07 / T08：光一个数字分不清是对象还是元素）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal, t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { LeftPanel } from './LeftPanel'
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
// 「打开素材」会把抽屉切到素材库，它量列宽用 ResizeObserver（jsdom 没有）
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver
globalThis.fetch = vi.fn(async () => new Response('{}', { status: 200 })) as typeof fetch

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
  script: 'fig1.py',
} as PanelObject

const el = (gid: string, role: string, label: string) => ({
  gid,
  role,
  label,
  bbox: [0.1, 0.1, 0.2, 0.2],
  draggable: false,
  editable: [{ prop: 'fontsize', type: 'number', value: 7 }],
})

/**
 * 刻度文字挂在刻度组下，刻度组挂在子图下——默认那两层是收起的。
 * 子图给 7 个直属元素、三条曲线，`buildTree` 才会加语义聚类那一层
 * （>4 个直属 + 同类 ≥2）——**聚类行与元素行是两套 onKeyDown**，
 * 只摆一条曲线的话聚类那一套一次都执行不到（变异反证时发现的）。
 */
const manifest = {
  stem: 'Fig1',
  size_mm: [80, 60],
  elements: [
    el('figure', 'figure', '整张图'),
    el('axes_0', 'axes', '子图 1'),
    el('axes_0.title', 'title', '标题'),
    // 引擎把引号里的文字截到 18 字：树行要用 `text` 字段补回全文（2026-09-14 审计 S15）
    {
      ...el('axes_0.xlabel', 'axis_label', 'X 轴 “Reaction time (mi…”'),
      editable: [
        { prop: 'text', type: 'text', value: 'Reaction time (min)' },
        { prop: 'fontsize', type: 'number', value: 7 },
      ],
    },
    el('axes_0.ylabel', 'axis_label', 'Y 轴标题'),
    el('axes_0.yticks', 'ticks', 'Y 刻度'),
    el('axes_0.yticks.label_3', 'ticklabel', '刻度文字 0.75'),
    el('axes_0.lines_0', 'line', '曲线 1'),
    el('axes_0.lines_1', 'line', '曲线 2'),
    el('axes_0.lines_2', 'line', '曲线 3'),
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
        <LeftPanel />
      </TooltipProvider>,
    )
  })
  await act(async () => {})
}

const rowGids = () =>
  [...host.querySelectorAll('[data-el]')].map((n) => (n as HTMLElement).dataset.el)
const search = () => host.querySelector('input') as HTMLInputElement
const heading = () => host.querySelector('h2')?.parentElement?.textContent ?? ''

async function type(v: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(search(), v)
    search().dispatchEvent(new Event('input', { bubbles: true }))
  })
}

async function seed() {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_eltree')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.objects = [panel as CanvasObject]
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
  seedExactRender(panel, manifest as never)
  useSelectionStore.getState().set(['p1'])
  useUiStore.getState().setElementPanel('p1')
}

beforeEach(async () => {
  document.body.innerHTML = ''
  localStorage.clear()
  useUiStore.setState({ leftTab: 'elements', selectedGids: [] })
  await seed()
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('选中的元素自己浮出来', () => {
  it('深层折叠组里的元素被选中后，那一行出现在树里', async () => {
    await mount()
    // 默认只展开 Figure 与子图：刻度文字所在的刻度组是收起的
    expect(rowGids()).not.toContain('axes_0.yticks.label_3')

    await act(async () => {
      useUiStore.getState().setSelectedGid('axes_0.yticks.label_3')
    })
    await act(async () => {})
    expect(rowGids()).toContain('axes_0.yticks.label_3')
  })

  it('浮出来的那一行是选中态，不是只把组打开了事', async () => {
    await mount()
    await act(async () => {
      useUiStore.getState().setSelectedGid('axes_0.yticks.label_3')
    })
    await act(async () => {})
    const row = host.querySelector('[data-el="axes_0.yticks.label_3"]') as HTMLElement
    expect(row.getAttribute('aria-selected')).toBe('true')
  })

  it('已经可见的元素不受影响：本来展开的照旧展开', async () => {
    await mount()
    await act(async () => {
      useUiStore.getState().setSelectedGid('axes_0.title')
    })
    await act(async () => {})
    expect(rowGids()).toContain('axes_0.title')
    // 顺带：把祖先展开不该把**别的**折叠组一起打开
    expect(rowGids()).not.toContain('axes_0.yticks.label_3')
  })

  it('树里根本没有的 gid 不会把什么都展开', async () => {
    await mount()
    const before = rowGids()
    await act(async () => {
      useUiStore.getState().setSelectedGid('axes_9.nothing')
    })
    await act(async () => {})
    expect(rowGids()).toEqual(before)
  })
})

describe('文案与计数', () => {
  it('搜索占位语不摆内部标识', async () => {
    await mount()
    expect(search().placeholder).toBe(t('elementTree.search', { ns: 'workspace' }))
    expect(search().placeholder).not.toContain('gid')
  })

  it('gid 仍然搜得到，只是不写进默认文案', async () => {
    await mount()
    await type('yticks')
    expect(rowGids()).toContain('axes_0.yticks')
  })

  it('树行用完整名字（按可用宽度 CSS 截断），不用引擎按字数截的那份（S15）', async () => {
    await mount()
    const row = host.querySelector('[data-el="axes_0.xlabel"]')!
    expect(row.textContent).toContain('Reaction time (min)')
    expect(row.textContent).not.toContain('(mi…')
  })

  it('标题上的计数带单位', async () => {
    await mount()
    // manifest 有 10 条，figure 那条不算
    expect(heading()).toContain(t('elementTree.count', { ns: 'workspace', count: 9 }))
    expect(heading()).not.toMatch(/\s9\s*$/)
  })
})

describe('结构列表的计数也带单位（T07）', () => {
  it('对象数说的是「对象」，与元素数不会看混', async () => {
    useUiStore.setState({ leftTab: 'layers' })
    await mount()
    expect(heading()).toContain(t('layerTree.count', { ns: 'workspace', count: 1 }))
    expect(t('layerTree.count', { ns: 'workspace', count: 1 })).not.toBe(
      t('elementTree.count', { ns: 'workspace', count: 1 }),
    )
  })
})

/**
 * 键盘可连续浏览父子层级（审计 T08 验收后半句）。树是 `role="tree"` +
 * `role="treeitem"`，焦点靠 roving tabindex 走，不靠 Tab 一格一格穿。
 */
describe('键盘', () => {
  const row = (gid: string) => host.querySelector(`[data-el="${gid}"]`) as HTMLElement
  const press = async (el: HTMLElement, key: string) => {
    await act(async () => {
      el.focus()
      el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
    })
    await act(async () => {})
  }

  it('↓ 走到下一行，↑ 走回来', async () => {
    await mount()
    const gids = rowGids()
    await press(row(gids[0]!), 'ArrowDown')
    expect((document.activeElement as HTMLElement).dataset.el).toBe(gids[1])
    await press(document.activeElement as HTMLElement, 'ArrowUp')
    expect((document.activeElement as HTMLElement).dataset.el).toBe(gids[0])
  })

  it('→ 展开收起的层级，← 收回去', async () => {
    await mount()
    const ticks = row('axes_0.yticks')
    expect(ticks.getAttribute('aria-expanded')).toBe('false')
    await press(ticks, 'ArrowRight')
    expect(rowGids()).toContain('axes_0.yticks.label_3')
    await press(row('axes_0.yticks'), 'ArrowLeft')
    expect(rowGids()).not.toContain('axes_0.yticks.label_3')
  })

  it('展开之后 ↓ 就能走进子层：父子是连着的一条路', async () => {
    await mount()
    await press(row('axes_0.yticks'), 'ArrowRight')
    await press(row('axes_0.yticks'), 'ArrowDown')
    expect((document.activeElement as HTMLElement).dataset.el).toBe('axes_0.yticks.label_3')
  })

  it('没有选中时 Tab 停在第一行，不是第一个聚类行', async () => {
    await mount()
    const tabbable = [...host.querySelectorAll('[data-el]')].filter(
      (n) => (n as HTMLElement).tabIndex === 0,
    ) as HTMLElement[]
    // 反证：这棵树里真有聚类行，而且它不是第一行——否则两种实现停在同一处
    const first = rowGids()[0]!
    expect(rowGids().some((g) => g!.includes('#'))).toBe(true)
    expect(first.includes('#')).toBe(false)
    expect(tabbable.map((n) => n.dataset.el)).toEqual([first])
  })

  it('语义聚类那一行也走同一套键：← 收起、→ 展开', async () => {
    await mount()
    // 聚类行的 key 是「父 key#聚类名」，不是 gid。聚类默认是**展开**的（审计 B44：
    // 文字 / 数据系列 / 图例 / 坐标轴的成员一进来就看得见），所以先 ← 再 →
    const cluster = [...host.querySelectorAll('[data-el]')].find((n) =>
      (n as HTMLElement).dataset.el!.includes('#'),
    ) as HTMLElement
    expect(cluster).toBeTruthy()
    expect(cluster.getAttribute('aria-expanded')).toBe('true')
    const expanded = rowGids().length
    await press(cluster, 'ArrowLeft')
    expect(rowGids().length).toBeLessThan(expanded)
    await press(row(cluster.dataset.el!), 'ArrowRight')
    expect(rowGids().length).toBe(expanded)
  })
})

/**
 * 空态分两句话（2026-09-13 审计 B05）：「画布上一张可编辑的图都没有」与「有、但没
 * 选中」是两种状态，各给各的下一步；两句都不许出现「参数化」这个实现词。
 */
describe('空态', () => {
  const et = (key: string) => t(`elementTree.${key}`, { ns: 'workspace' })

  it('一张可编辑的图都没有：说清原因，主动作是打开素材', async () => {
    useUiStore.getState().setElementPanel(null)
    useSelectionStore.getState().set([])
    useDocumentStore.getState().commit(literal('清空'), (d) => {
      d.objects = [{ ...panel, script: undefined } as CanvasObject]
    })
    await mount()
    expect(host.textContent).toContain(et('noEditableTitle'))
    expect(host.textContent).not.toContain('参数化')
    const btn = [...host.querySelectorAll('button')].find((b) => b.textContent === et('openAssets'))!
    expect(btn).toBeTruthy()
    await act(async () => btn.click())
    expect(useUiStore.getState().leftTab).toBe('assets')
  })

  it('有可编辑的图但没选中：主动作选中它，树随即出现', async () => {
    useUiStore.getState().setElementPanel(null)
    useSelectionStore.getState().set([])
    await mount()
    expect(host.textContent).toContain(et('noPanelTitle'))
    expect(host.textContent).not.toContain(et('noEditableTitle'))
    const btn = [...host.querySelectorAll('button')].find((b) => b.textContent === et('locateEditable'))!
    expect(btn).toBeTruthy()
    await act(async () => btn.click())
    await act(async () => {})
    expect(useSelectionStore.getState().ids).toEqual(['p1'])
    expect(rowGids()).toContain('figure')
  })

  it('刻度文字行只显示值：组名已经说了它们是刻度', async () => {
    await mount()
    const row = (gid: string) => host.querySelector(`[data-el="${gid}"]`) as HTMLElement
    await act(async () => {
      row('axes_0.yticks').focus()
      row('axes_0.yticks').dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }),
      )
    })
    await act(async () => {})
    const tick = row('axes_0.yticks.label_3')
    expect(tick).toBeTruthy()
    // 夹具的刻度文字没有 text 字段：退回完整名字；有 text 的那条只显示值
    expect(tick.textContent).toContain('刻度文字 0.75')
  })
})
