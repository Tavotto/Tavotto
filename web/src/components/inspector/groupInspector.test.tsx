/**
 * 右栏对真实组（共享色条，`Manifest.groups`）的两处呈现：
 *
 *   1. **选中组** → 组页：标题是组名、列出成员（色条轴直接换成它的色条）、说出颜色
 *      来源并给「选中它」的入口；不是退回「整张图」，也不挂恢复整张图的菜单；
 *   2. **面包屑走真实父级**：组里的子图 / 色条都带着组这一级，点它回到组；
 *      色条轴这一级（色条的承载轴）不单列——单宿主色条的上一级是宿主子图。
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
import type { Manifest } from '@/lib/api'
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

const panel = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 100,
  nativeH: 80,
  overrides: [{ gid: 'axes_0', prop: 'position', value: [0.1, 0.1, 0.2, 0.7] }],
  x: 0,
  y: 0,
  w: 100,
  h: 80,
  script: 'fig.py',
} as PanelObject

const GROUP = 'group:axes_3'
const pos = (v: number[]) => ({ prop: 'position', type: 'rect', value: v })
const el = (gid: string, role: string, label: string, extra: Record<string, unknown> = {}) => ({
  gid,
  role,
  label,
  bbox: [0.1, 0.1, 0.2, 0.2],
  draggable: false,
  editable: [] as unknown[],
  ...extra,
})

const manifest = {
  stem: 'Fig1',
  size_mm: [100, 80],
  elements: [
    el('figure', 'figure', '整张图'),
    el('axes_0', 'axes', '子图 1', { resizable: true, editable: [pos([0.05, 0.15, 0.25, 0.7])] }),
    el('axes_1', 'axes', '子图 2', {
      parent_gid: GROUP,
      resizable: true,
      editable: [pos([0.38, 0.15, 0.2, 0.7])],
    }),
    el('axes_1.images_0', 'image', '图像 1', { geom_gid: 'axes_1', resizable: true }),
    el('axes_2', 'axes', '子图 3', {
      parent_gid: GROUP,
      resizable: true,
      editable: [pos([0.62, 0.15, 0.2, 0.7])],
    }),
    el('axes_3', 'axes', '色条轴', {
      parent_gid: GROUP,
      resizable: true,
      is_colorbar: true,
      colorbar_gid: 'axes_3.colorbar',
      editable: [pos([0.85, 0.15, 0.03, 0.7])],
    }),
    el('axes_3.colorbar', 'colorbar', '色条', {
      geom_gid: 'axes_3',
      resizable: true,
      owner_gids: ['axes_1', 'axes_2'],
      mappable_gid: 'axes_1.images_0',
      editable: [{ prop: 'fontsize', type: 'number', value: 8 }],
    }),
    // 单宿主色条：色条轴挂回子图 1
    el('axes_4', 'axes', '色条轴', {
      parent_gid: 'axes_0',
      is_colorbar: true,
      colorbar_gid: 'axes_4.colorbar',
    }),
    el('axes_4.colorbar', 'colorbar', '色条', {
      geom_gid: 'axes_4',
      owner_gids: ['axes_0'],
      editable: [{ prop: 'fontsize', type: 'number', value: 8 }],
    }),
  ],
  groups: [
    {
      gid: GROUP,
      kind: 'shared_colorbar',
      members: ['axes_1', 'axes_2', 'axes_3'],
      subplot_gids: ['axes_1', 'axes_2'],
      colorbar_gid: 'axes_3.colorbar',
      mappable_gid: 'axes_1.images_0',
      bbox: [0.38, 0.15, 0.5, 0.7],
      resizable: true,
    },
  ],
} as unknown as Manifest

let host: HTMLDivElement
let root: Root

async function mount(select: string) {
  useUiStore.getState().setSelectedGid(select)
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

const title = () => document.querySelector('h2')?.textContent ?? ''
const crumbs = () => [...document.querySelectorAll('[data-crumb]')].map((b) => (b as HTMLElement).dataset.crumb)
const groupName = t('modelGroup.sharedColorbar', { ns: 'inspector', members: '子图 2、子图 3' })

beforeEach(async () => {
  document.body.innerHTML = ''
  localStorage.clear()
  useUiStore.setState({ rightTab: 'properties', elementPanelId: null, selectedGids: [] })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_group_inspector')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 120, h: 100 }
    d.objects = [panel as CanvasObject]
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
  useSelectionStore.getState().set(['p1'])
  seedExactRender(panel, manifest as never)
  useUiStore.getState().setElementPanel('p1')
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('选中组 → 组页', () => {
  it('标题是组名，不退回「整张图」；不挂恢复整张图的菜单', async () => {
    await mount(GROUP)
    expect(title()).toBe(groupName)
    expect(document.querySelector('[data-object-kind]')).toBeNull()
    expect(document.querySelector('[data-group-page]')).not.toBeNull()
    // 面板上有一条 override：「没选元素」的头会挂恢复整张图的菜单，组的头不挂
    expect(document.querySelectorAll('header p button:not([data-crumb])')).toHaveLength(0)
  })

  it('成员逐个列出（色条轴换成它的色条），点一下选中那个成员', async () => {
    await mount(GROUP)
    const members = [...document.querySelectorAll('[data-group-member]')].map(
      (b) => (b as HTMLElement).dataset.groupMember,
    )
    expect(members).toEqual(['axes_1', 'axes_2', 'axes_3.colorbar'])
    await act(async () => {
      ;(document.querySelector('[data-group-member="axes_2"]') as HTMLElement).click()
    })
    expect(useUiStore.getState().selectedGids).toEqual(['axes_2'])
  })

  it('颜色来源是色条的 mappable（不是组），给「选中它」的入口', async () => {
    await mount(GROUP)
    const btn = document.querySelector('[data-group-color-source]') as HTMLElement
    expect(btn.dataset.groupColorSource).toBe('axes_1.images_0')
    await act(async () => btn.click())
    expect(useUiStore.getState().selectedGids).toEqual(['axes_1.images_0'])
  })

  it('整组缩放用成组缩放那个控件（参照框 = 成员并集）', async () => {
    await mount(GROUP)
    expect(document.querySelector('[data-group-page] [data-scale-apply]')).not.toBeNull()
  })

  /** 组不能整体变换的几种来路（`groupTransformBlocked`）：manifest 变体 + 面板上锁住的元素 */
  const blockedCases = [
    ['锁住子图 3', 'locked', 'layoutMemberLocked', manifest, ['axes_2']],
    ['锁住共享的色条元素', 'locked', 'layoutMemberLocked', manifest, ['axes_3.colorbar']],
    [
      '成员落位不归 Tavotto 管（resizable: false）',
      'not_resizable',
      'layoutLocked',
      { ...manifest, groups: [{ ...manifest.groups![0], resizable: false }] },
      [],
    ],
    [
      '成员这一版没有 position',
      'incomplete',
      'layoutIncomplete',
      {
        ...manifest,
        elements: manifest.elements.map((e) => (e.gid === 'axes_2' ? { ...e, editable: [] } : e)),
      },
      [],
    ],
  ] as const

  it.each(blockedCases)(
    '%s：整组缩放不摆，按原因说清楚（与拖动 / 组框手柄 / 方向键同一个判据）',
    async (_name, reason, key, m, locked) => {
      useDocumentStore.getState().commit(literal('锁定'), (d) => {
        ;(d.objects[0] as PanelObject).lockedGids = [...locked]
      })
      seedExactRender(useDocumentStore.getState().doc.objects[0] as PanelObject, m as never)
      await mount(GROUP)
      expect(document.querySelector('[data-group-page] [data-scale-apply]')).toBeNull()
      const note = document.querySelector('[data-group-blocked]') as HTMLElement | null
      expect(note?.dataset.groupBlocked).toBe(reason)
      expect(note?.textContent).toBe(t(`modelGroup.${key}`, { ns: 'inspector' }))
    },
  )
})

describe('面包屑走真实父级', () => {
  it('组里的子图：整张图 / 组，点组回到组', async () => {
    await mount('axes_2')
    expect(crumbs()).toEqual(['figure', GROUP])
    await act(async () => {
      ;(document.querySelector(`[data-crumb="${GROUP}"]`) as HTMLElement).click()
    })
    expect(useUiStore.getState().selectedGids).toEqual([GROUP])
  })

  it('共享色条：整张图 / 组 / 色条——色条轴这一级不单列', async () => {
    await mount('axes_3.colorbar')
    expect(crumbs()).toEqual(['figure', GROUP])
    const text = document.querySelector('header p')?.textContent ?? ''
    expect(text).toContain(groupName)
    expect(text).not.toContain('色条轴')
  })

  it('单宿主色条：上一级是宿主子图，不是色条轴', async () => {
    await mount('axes_4.colorbar')
    expect(crumbs()).toEqual(['figure', 'axes_0'])
  })
})
