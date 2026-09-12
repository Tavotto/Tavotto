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

describe('图内元素的头部图标按角色（2026-09-12 critique P3）', () => {
  it('选中标题时是文字图标，不是面板那个图片图标；整张图是 Frame', async () => {
    await seed([panel], ['p1'])
    seedExactRender(panel, manifest as never)
    useUiStore.getState().setElementPanel('p1')
    useUiStore.setState({ selectedGids: ['axes_0.title'] })
    await mount()
    const icon = document.querySelector('header svg')!
    expect(icon.getAttribute('class')).toContain('lucide-type')
    expect(icon.getAttribute('class')).not.toContain('lucide-image')
    // 与元素树同一张表：树里标题也是 Type
    useUiStore.setState({ selectedGids: [] })
    await act(async () => {})
    expect(document.querySelector('header svg')!.getAttribute('class')).toContain('lucide-frame')
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
