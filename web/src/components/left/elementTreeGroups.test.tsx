/**
 * 元素树里的**真实组**（共享色条）与色条的结构归属。
 *
 * 引擎只按色条自己声明的宿主给显式父级（manifest 的 `parent_gid` / `groups`），
 * 这里量的是树把它们摆成什么样：
 *
 *   * 共享色条：整张图 → 组 → {子图 B, 子图 C, 色条轴}；组可选中；
 *   * 色条轴不进任何一个子图的抽屉（它挂在组下，组下不加抽屉）；
 *   * 单宿主色条：色条轴挂回宿主子图，按类别进「图例与色条」抽屉；
 *   * 画布上选中组里的深层元素：祖先链（整张图、组、子图、必要的抽屉）全部展开；
 *   * 老 manifest（没有 `groups` / `parent_gid`）的树一个字节不变。
 *
 * 断言的是行的**层级**（缩进档）与父子关系，不只是树上有哪几个字。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal, t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { TREE_INDENT } from '@/components/ui/TreeRow'
import type { Manifest } from '@/lib/api'
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
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver
globalThis.fetch = vi.fn(async () => new Response('{}', { status: 200 })) as typeof fetch

const panel = {
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

const el = (gid: string, role: string, label: string, extra: Record<string, unknown> = {}) => ({
  gid,
  role,
  label,
  bbox: [0.1, 0.1, 0.2, 0.2],
  draggable: false,
  editable: [{ prop: 'fontsize', type: 'number', value: 7 }],
  ...extra,
})

const GROUP = 'group:axes_3'

/** 子图 A 有 6 个直属元素（会分抽屉）；B、C 与色条轴由一条共享色条连成组 */
const shared = (): Manifest =>
  ({
    stem: 'Fig1',
    size_mm: [80, 60],
    elements: [
      el('figure', 'figure', '整张图'),
      el('axes_0', 'axes', '子图 1'),
      el('axes_0.title', 'title', '标题 “A”'),
      el('axes_0.xlabel', 'axis_label', 'X 轴标题'),
      el('axes_0.legend', 'legend', '图例'),
      el('axes_0.lines_0', 'line', '曲线 1'),
      el('axes_0.lines_1', 'line', '曲线 2'),
      el('axes_0.xticks', 'ticks', 'X 刻度文字'),
      el('axes_1', 'axes', '子图 2', { parent_gid: GROUP }),
      el('axes_1.images_0', 'image', '图像 1', { geom_gid: 'axes_1' }),
      el('axes_1.xticks', 'ticks', 'X 刻度文字'),
      el('axes_1.xticklabels_0', 'ticklabel', '刻度 “0”'),
      el('axes_2', 'axes', '子图 3', { parent_gid: GROUP }),
      el('axes_2.images_0', 'image', '图像 1', { geom_gid: 'axes_2' }),
      el('axes_3', 'axes', '色条轴', {
        parent_gid: GROUP,
        is_colorbar: true,
        colorbar_gid: 'axes_3.colorbar',
      }),
      el('axes_3.colorbar', 'colorbar', '色条', {
        geom_gid: 'axes_3',
        owner_gids: ['axes_1', 'axes_2'],
        mappable_gid: 'axes_1.images_0',
      }),
      el('axes_3.yticks', 'ticks', 'Y 刻度文字'),
    ],
    groups: [
      {
        gid: GROUP,
        kind: 'shared_colorbar',
        members: ['axes_1', 'axes_2', 'axes_3'],
        subplot_gids: ['axes_1', 'axes_2'],
        colorbar_gid: 'axes_3.colorbar',
        mappable_gid: 'axes_1.images_0',
        bbox: [0.4, 0.1, 0.5, 0.7],
        resizable: true,
      },
    ],
  }) as unknown as Manifest

/** 单宿主色条：色条轴挂回子图；子图直属 6 个 → 分抽屉，色条轴与图例同进「图例与色条」 */
const single = (withParent = true): Manifest =>
  ({
    stem: 'Fig1',
    size_mm: [80, 60],
    elements: [
      el('figure', 'figure', '整张图'),
      el('axes_0', 'axes', '子图 1', { follow_gids: ['axes_1'] }),
      el('axes_0.title', 'title', '标题 “A”'),
      el('axes_0.xlabel', 'axis_label', 'X 轴标题'),
      el('axes_0.legend', 'legend', '图例'),
      el('axes_0.images_0', 'image', '图像 1'),
      el('axes_0.lines_0', 'line', '曲线 1'),
      el('axes_1', 'axes', '色条轴', {
        ...(withParent ? { parent_gid: 'axes_0' } : {}),
        is_colorbar: true,
        colorbar_gid: 'axes_1.colorbar',
      }),
      el('axes_1.colorbar', 'colorbar', '色条', { geom_gid: 'axes_1' }),
    ],
  }) as unknown as Manifest

let host: HTMLDivElement
let root: Root

async function mount(m: Manifest) {
  seedExactRender(panel, m as never)
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
  [...host.querySelectorAll('[role="tree"] [data-el]')].map((n) => (n as HTMLElement).dataset.el!)
const row = (key: string) => host.querySelector(`[data-el="${CSS.escape(key)}"]`) as HTMLElement | null
/** 行的缩进档：treeIndent = 8 + depth × TREE_INDENT */
const depthOf = (key: string) => (parseFloat(row(key)!.style.paddingLeft) - 8) / TREE_INDENT
/** 某一行之后、缩进比它深的连续行 = 它（展开着的）子树 */
const subtreeOf = (key: string) => {
  const gids = rowGids()
  const i = gids.indexOf(key)
  const d = depthOf(key)
  const out: string[] = []
  for (const g of gids.slice(i + 1)) {
    if (depthOf(g) <= d) break
    out.push(g)
  }
  return out
}
const directChildren = (key: string) => subtreeOf(key).filter((g) => depthOf(g) === depthOf(key) + 1)

beforeEach(async () => {
  document.body.innerHTML = ''
  localStorage.clear()
  useUiStore.setState({ leftTab: 'elements', selectedGids: [] })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_eltree_groups')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.objects = [panel as CanvasObject]
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
  useSelectionStore.getState().set(['p1'])
  useUiStore.getState().setElementPanel('p1')
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('共享色条：整张图 → 组 → 子图 B、C 与色条', () => {
  it('组是整张图的直接子节点，B、C、色条轴是组的直接子节点；A 不进组', async () => {
    await mount(shared())
    expect(directChildren('figure')).toEqual(['axes_0', GROUP])
    expect(directChildren(GROUP)).toEqual(['axes_1', 'axes_2', 'axes_3'])
    // 色条元素仍在它的承载轴下
    expect(directChildren('axes_3')).toContain('axes_3.colorbar')
    expect(subtreeOf('axes_0')).not.toContain('axes_3')
  })

  it('组的行说得出它是谁：共享色条组（子图 2、子图 3）', async () => {
    await mount(shared())
    expect(row(GROUP)!.textContent).toContain(
      t('modelGroup.sharedColorbar', { ns: 'inspector', members: '子图 2、子图 3' }),
    )
  })

  it('组下不加抽屉，A 的抽屉照旧（>4 个直属才分，单个的不开抽屉）', async () => {
    await mount(shared())
    expect(directChildren(GROUP).some((g) => g.includes('#'))).toBe(false)
    const drawers = directChildren('axes_0').filter((g) => g.includes('#'))
    expect(drawers).toEqual(expect.arrayContaining(['axes_0#text', 'axes_0#series']))
    // A 的图例只有一个成员：不单独开「图例与色条」抽屉；共享色条不在 A 的任何抽屉里
    expect(drawers).not.toContain('axes_0#legend')
    expect(subtreeOf('axes_0')).not.toContain('axes_3')
  })

  it('点组的行 = 选中组（它是真实节点，不是抽屉）', async () => {
    await mount(shared())
    await act(async () => {
      row(GROUP)!.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }))
    })
    expect(useUiStore.getState().selectedGids).toEqual([GROUP])
    expect(row(GROUP)!.getAttribute('aria-selected')).toBe('true')
  })

  it('画布上选中组里的深层元素：整张图、组、子图、刻度组依次展开，选中行出现', async () => {
    await mount(shared())
    // 先把组收起来
    await act(async () => {
      row(GROUP)!.focus()
    })
    await act(async () => {
      row(GROUP)!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }))
    })
    expect(rowGids()).not.toContain('axes_1')
    await act(async () => {
      useUiStore.getState().setSelectedGid('axes_1.xticklabels_0')
    })
    await act(async () => {})
    expect(rowGids()).toEqual(expect.arrayContaining([GROUP, 'axes_1', 'axes_1.xticks']))
    expect(row('axes_1.xticklabels_0')!.getAttribute('aria-selected')).toBe('true')
    expect(depthOf('axes_1.xticklabels_0')).toBe(depthOf(GROUP) + 3)
  })

  it('搜组名能找到组，子树整棵留着', async () => {
    await mount(shared())
    const input = host.querySelector('input') as HTMLInputElement
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, '共享色条组')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(rowGids()).toEqual(expect.arrayContaining([GROUP, 'axes_1', 'axes_2', 'axes_3']))
    expect(rowGids()).not.toContain('axes_0')
  })
})

describe('单宿主色条挂回子图', () => {
  it('色条轴是子图的子节点，并按类别进「图例与色条」抽屉（与图例同一格）', async () => {
    await mount(single())
    expect(directChildren('figure')).toEqual(['axes_0'])
    expect(directChildren('axes_0#legend')).toEqual(['axes_0.legend', 'axes_1'])
  })

  it('老 manifest（没有 parent_gid）的树不变：色条轴仍在整张图下', async () => {
    await mount(single(false))
    expect(directChildren('figure')).toEqual(['axes_0', 'axes_1'])
    expect(rowGids().some((g) => g.startsWith('group:'))).toBe(false)
  })
})
