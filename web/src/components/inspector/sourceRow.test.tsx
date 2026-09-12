/**
 * 属性栏身份头的脚本行（2026-09-12 critique P1「脚本不在场」）。
 *
 * 要钉住的：
 *   1. 图内编辑时，头部**看得见脚本名**与「脚本未改动」——此前整张页面的文字里没有
 *      任何 `.py`（critique 实测），aha 时刻的后半句没人说；
 *   2. 恢复入口跟着脚本行走：一颗 ↺ 菜单，「恢复此元素 · n 项」「恢复整张图 · m 项」
 *      各说各的对象与数量，数字来自同一份 overrides，按下去清掉的正是标签上写的那批
 *      （审计 T32 的保证不因搬家而丢）；没有任何修改时这颗钮不出现；
 *   3. 「源文件与高级」折叠区里**不再有**恢复按钮——只剩会动磁盘的那一组；
 *   4. 身份头带 matplotlib 类名徽标（术语桥）：标题是 `Text`，整张图是 `Figure`；
 *   5. 「修改保存在哪里」跟着脚本行走，说明仍不用实现词；
 *   6. 没有脚本的素材不摆这一行。
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
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_source_row')
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

const row = () => host.querySelector<HTMLElement>('[data-source-row]')
const restoreTrigger = () => host.querySelector<HTMLButtonElement>('[data-source-restore]')
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

describe('脚本行：脚本在场', () => {
  it('图内编辑时头部写着脚本名与「脚本未改动」', async () => {
    await seed(panelOf(), 'axes_0.title')
    await mount()
    const r = row()!
    expect(r.textContent).toContain('fig1_kinetics.py')
    expect(r.textContent).toContain('脚本未改动')
    // 完整路径留在 title 里，不占版面
    expect(r.querySelector('[title="figs/fig1_kinetics.py"]')).toBeTruthy()
  })

  it('没有脚本的素材不摆这一行', async () => {
    await seed(panelOf({ script: null, overrides: [] }), 'axes_0.title')
    await mount()
    expect(row()).toBeNull()
  })

  it('没选元素（整张图）时也在，且徽标是 Figure', async () => {
    await seed(panelOf(), null)
    await mount()
    expect(row()!.textContent).toContain('fig1_kinetics.py')
    expect(host.querySelector('[data-mpl-class]')?.textContent).toBe('Figure')
  })

  it('选中标题时徽标是 Text', async () => {
    await seed(panelOf(), 'axes_0.title')
    await mount()
    expect(host.querySelector('[data-mpl-class]')?.textContent).toBe('Text')
  })
})

describe('恢复入口跟着脚本行走', () => {
  it('一条修改都没有时没有 ↺ 钮', async () => {
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

  it('「恢复整张图」清掉全部，之后 ↺ 钮消失', async () => {
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

describe('「修改保存在哪里」跟着脚本行走', () => {
  it('问号钮在脚本行里，说明不用实现词', async () => {
    await seed(panelOf(), 'axes_0.title')
    await mount()
    const btn = row()!.querySelector<HTMLButtonElement>('button[aria-label="修改保存在哪里？"]')
    expect(btn).toBeTruthy()
    await act(async () => btn!.click())
    const text = document.body.textContent ?? ''
    expect(text).toContain('图内修改')
    for (const jargon of ['override', '撤销栈', '引擎', '孤儿']) expect(text).not.toContain(jargon)
  })
})
