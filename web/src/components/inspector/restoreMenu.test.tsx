/**
 * 属性栏身份头的「n 项已修改 ↺」（2026-09-12 critique P1「恢复入口太深」的第二版）。
 *
 * 第一版在头部另起了一行「脚本名 · 脚本未改动 · ↺ · ?」，用户的回退意见：占地且啰嗦，
 * 脚本名多余、「脚本未改动」不必常驻、类名徽标与标题重复。现在计数徽标本身就是
 * 恢复菜单的触发器，头部不多一行、不多一颗钮。要钉住的：
 *   1. 徽标写的是修改数，点开是「恢复此元素 · n 项」「恢复整张图 · m 项」——各说各的
 *      对象与数量，数字来自同一份 overrides，按下去清掉的正是标签上写的那批（审计 T32）；
 *   2. 一条修改都没有时整颗徽标不出现（没有「禁用的恢复钮」）；
 *   3. 「源文件与高级」折叠区里**不再有**恢复按钮——只剩会动磁盘的那一组；
 *   4. 头部没有脚本行、没有 matplotlib 类名徽标（回退第一版）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchReadiness: vi.fn().mockResolvedValue(null),
  engineRender: vi.fn(),
}))

import { literal } from '@/i18n'
import { engineRender } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { Inspector } from './Inspector'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
globalThis.fetch = vi.fn(async () => new Response('{}', { status: 200 })) as typeof fetch

const manifest = {
  stem: 'Fig1_kinetics',
  size_mm: [80, 60],
  elements: [
    { gid: 'figure', role: 'figure', label: '整张图', bbox: [0, 0, 1, 1], draggable: false, editable: [] },
    {
      gid: 'axes_0.title',
      role: 'title',
      label: '标题 “A”',
      bbox: [0.1, 0.02, 0.8, 0.08],
      draggable: false,
      editable: [
        { prop: 'text', type: 'text', value: 'A' },
        { prop: 'fontsize', type: 'number', value: 9, min: 4, max: 40, step: 0.5 },
        { prop: 'color', type: 'color', value: '#000000' },
      ],
    },
    {
      gid: 'axes_0.xlabel',
      role: 'axis_label',
      label: 'X 轴 “t”',
      bbox: [0.1, 0.9, 0.8, 0.08],
      draggable: false,
      editable: [{ prop: 'fontsize', type: 'number', value: 8, min: 4, max: 40, step: 0.5 }],
    },
  ],
}

const OVERRIDES: PanelObject['overrides'] = [
  { gid: 'axes_0.title', prop: 'fontsize', value: 11 },
  { gid: 'axes_0.title', prop: 'color', value: '#ff0000' },
  { gid: 'axes_0.xlabel', prop: 'fontsize', value: 8 },
]

const panelOf = (extra: Partial<PanelObject> = {}): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    fileId: 'Fig1_kinetics.pdf',
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 60,
    x: 0,
    y: 0,
    w: 80,
    h: 60,
    script: 'figs/fig1_kinetics.py',
    overrides: OVERRIDES.map((o) => ({ ...o })),
    ...extra,
  }) as PanelObject

let host: HTMLDivElement
let root: Root

async function seed(p: PanelObject, gid: string | null) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_restore_menu')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 100, h: 80 }
    d.objects = [p]
  })
  useAssetStore.setState({
    byId: { 'Fig1_kinetics.pdf': { id: 'Fig1_kinetics.pdf', mtime: 1 } },
  } as never)
  // 现在这组 overrides、清掉元素那组、全清那组：三个变体都算「已渲染」，
  // 免得清完之后检查器因为「等引擎」整屏换成骨架
  const variants = [
    p,
    { ...p, overrides: p.overrides.filter((o) => o.gid !== 'axes_0.title') },
    { ...p, overrides: [] },
  ]
  for (const v of variants) {
    const key = renderKeyOf(v)
    useRenderStore.getState().patch(key, {
      fileId: p.fileId,
      manifest,
      svg: '<svg/>',
      rev: 1,
      status: 'ready',
      lastPatches: '[]',
    } as never)
    useRenderStore.setState((s) => ({ latest: { ...s.latest, [p.fileId]: key } }))
  }
  useSelectionStore.getState().set(['p1'])
  useUiStore.setState({ rightTab: 'properties', elementPanelId: 'p1', selectedGids: gid ? [gid] : [] })
  useDocumentStore.setState({ past: [], future: [] })
}

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

const restoreTrigger = () => host.querySelector<HTMLButtonElement>('[data-restore-menu]')
const menuItems = () => Array.from(document.querySelectorAll<HTMLElement>('[role="menuitem"]'))
const overrides = () =>
  (useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1') as PanelObject).overrides
const buttons = () => Array.from(host.querySelectorAll('button'))
const buttonByText = (text: string) => buttons().find((b) => b.textContent?.trim().startsWith(text))

/** Radix 的触发器认 pointerdown，`click()` 不开菜单（见 objectKindSwitch.test） */
async function openRestore() {
  await act(async () => {
    restoreTrigger()!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }))
  })
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
}

beforeEach(async () => {
  document.body.innerHTML = ''
  localStorage.clear()
  vi.mocked(engineRender).mockResolvedValue({ rev: 2, manifest, svg: '<svg/>', warnings: [] } as never)
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {} })
  useRenderStore.getState().clear()
})

afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
  useSelectionStore.getState().clear()
})

describe('头部只有计数徽标：没有脚本行、没有类名徽标', () => {
  it('徽标写的是「2 项已修改」，头部里没有 .py、没有「脚本未改动」', async () => {
    await seed(panelOf(), 'axes_0.title')
    await mount()
    const header = host.querySelector('header')!
    expect(restoreTrigger()!.textContent).toBe('2 项已修改')
    expect(header.textContent).not.toMatch(/\.py|脚本未改动/)
    // 头部只有两行：标题行 + 面包屑行；面包屑里没有类名徽标那个 <code>
    expect(header.children.length).toBe(2)
    expect(header.querySelector('code')).toBeNull()
  })

  it('没选元素（整张图）时徽标是面板总数 3', async () => {
    await seed(panelOf(), null)
    await mount()
    expect(restoreTrigger()!.textContent).toBe('3 项已修改')
  })
})

describe('计数徽标就是恢复菜单', () => {
  it('一条修改都没有时整颗徽标不出现', async () => {
    await seed(panelOf({ overrides: [] }), 'axes_0.title')
    await mount()
    expect(restoreTrigger()).toBeNull()
  })

  it('菜单两项各说各的对象与数量：「恢复此元素 · 2 项」「恢复整张图 · 3 项」', async () => {
    await seed(panelOf(), 'axes_0.title')
    await mount()
    await openRestore()
    const texts = menuItems().map((m) => m.textContent?.trim())
    expect(texts).toEqual(['恢复此元素 · 2 项', '恢复整张图 · 3 项'])
  })

  it('「恢复此元素」只清这个元素的两项，别的元素那项留着', async () => {
    await seed(panelOf(), 'axes_0.title')
    await mount()
    await openRestore()
    const item = menuItems().find((m) => m.textContent?.includes('恢复此元素'))!
    await act(async () => {
      item.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    expect(overrides()).toEqual([{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 8 }])
  })

  it('「恢复整张图」清掉全部，之后徽标消失', async () => {
    await seed(panelOf(), 'axes_0.title')
    await mount()
    await openRestore()
    const item = menuItems().find((m) => m.textContent?.includes('恢复整张图'))!
    await act(async () => {
      item.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    expect(overrides()).toEqual([])
    await act(async () => {})
    expect(restoreTrigger()).toBeNull()
  })

  it('没选元素时菜单只有「恢复整张图」', async () => {
    await seed(panelOf(), null)
    await mount()
    await openRestore()
    expect(menuItems().map((m) => m.textContent?.trim())).toEqual(['恢复整张图 · 3 项'])
  })

  it('「源文件与高级」里不再有恢复按钮，只剩「原始文件」那一组', async () => {
    await seed(panelOf(), 'axes_0.title')
    await mount()
    await act(async () => {
      buttonByText('源文件与高级')!.click()
    })
    const fold = host.querySelector('[data-source-advanced]')!
    const foldButtons = Array.from(fold.querySelectorAll('button')).map((b) => b.textContent?.trim() ?? '')
    expect(foldButtons.some((t) => t.startsWith('恢复'))).toBe(false)
    expect(foldButtons.some((t) => t.startsWith('写回原始文件'))).toBe(true)
    const heads = Array.from(fold.querySelectorAll('p')).map((p) => p.textContent?.trim())
    expect(heads).toContain('原始文件')
    expect(heads).not.toContain('恢复')
  })
})
