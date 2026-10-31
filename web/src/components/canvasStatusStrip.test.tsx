/**
 * 画布状态条（UI 审计 B2 / B3 / A1）。验收：**每次都能指出当前状态、当前对象和退出路径**；
 * 图内编辑的出口只有一颗、Esc 同一个动作；教程与 e2e 认的 `data-*` 锚点原样保留。
 *
 * 此前这些话分在三处：浮在图上的「图名 / 退出图内编辑」胶囊（盖住图标题与控制点）、右栏「画布」页、
 * 提示条。视口那一条在 `store/workspace.test.ts`——它是 store 层的事实，不是渲染。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal, t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { CanvasStatusStrip } from './CanvasStatusStrip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
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
  name: 'Fig2_correlation',
  fileId: 'Fig2_correlation.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 10,
  y: 10,
  w: 80,
  h: 60,
  script: 'fig2.py',
} as PanelObject

const manifest = {
  stem: 'Fig2_correlation',
  size_mm: [80, 60],
  elements: [
    { gid: 'figure', role: 'figure', label: '整张图', bbox: [0, 0, 1, 1], draggable: false, editable: [] },
    {
      gid: 'axes_0.yticks',
      role: 'ticks',
      label: 'Y 刻度',
      bbox: [0.05, 0.1, 0.05, 0.8],
      draggable: false,
      editable: [{ prop: 'fontsize', type: 'number', value: 7 }],
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
        <CanvasStatusStrip />
      </TooltipProvider>,
    )
  })
  await act(async () => {})
}

const bar = () => host.querySelector('[data-workspace-context-bar]') as HTMLElement | null
const backBtns = () => [...host.querySelectorAll('[data-context-back]')] as HTMLElement[]
const objectCrumb = () => host.querySelector('[data-context-object]')?.textContent ?? null
const crumbs = () =>
  [...host.querySelectorAll('ol > li:not([aria-hidden])')].map((n) => n.textContent)
const anchors = () =>
  [...host.querySelectorAll('[data-onboarding-anchor]')].map(
    (n) => (n as HTMLElement).dataset.onboardingAnchor,
  )
const w = (k: string, v?: Record<string, unknown>) => t(k, { ns: 'workspace', ...(v ?? {}) })

const panel2: PanelObject = { ...panel, id: 'p2', name: 'Fig2_yield', fileId: 'Fig2_yield.pdf', x: 100, y: 10, script: undefined } as PanelObject

async function seed() {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ctxbar')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.objects = [panel as CanvasObject]
  })
  useAssetStore.setState({
    byId: { 'Fig2_correlation.pdf': { id: 'Fig2_correlation.pdf', mtime: 1 } },
  } as never)
  seedExactRender(panel, manifest as never)
}

async function setObjects(objs: CanvasObject[]) {
  await act(async () =>
    useDocumentStore.getState().commit(literal('换'), (d) => {
      d.objects = objs
    }),
  )
}

beforeEach(async () => {
  document.body.innerHTML = ''
  localStorage.clear()
  useWorkspaceStore.getState().clear()
  useUiStore.setState({ elementPanelId: null, selectedGids: [] })
  useSelectionStore.getState().clear()
  await seed()
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('状态条常驻，说当前状态（B2）', () => {
  it('空画布：常驻，说空画布与页面尺寸', async () => {
    await setObjects([])
    await mount()
    expect(bar()?.dataset.workspaceMode).toBe('layout')
    expect(bar()!.textContent).toContain(w('stageStrip.empty'))
    expect(bar()!.textContent).toContain(w('stageStrip.page', { w: '150.0', h: '100.0' }))
  })

  it('单图编辑：说「按原图尺寸 W × H mm 导出」，模式记为 fast_edit', async () => {
    useWorkspaceStore.getState().enterFastEdit('p1')
    await mount()
    expect(bar()?.dataset.workspaceMode).toBe('fast_edit')
    expect(bar()!.textContent).toContain(w('stageStrip.single'))
    expect(bar()!.textContent).toContain(w('stageStrip.exportAtOriginal', { w: '80.0', h: '60.0' }))
  })

  it('多图排版：说页面尺寸与图数', async () => {
    await setObjects([panel as CanvasObject, panel2 as CanvasObject])
    await mount()
    expect(bar()!.textContent).toContain(w('stageStrip.multi'))
    expect(bar()!.textContent).toContain(w('stageStrip.count', { n: 2 }))
  })

  it('图内编辑：模式记为 layout（还在画布这一层）；条是固定高度灰条，不是浮在图上的胶囊', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    expect(bar()?.dataset.workspaceMode).toBe('layout')
    expect(bar()!.className).toContain('h-7')
    expect(bar()!.className).not.toContain('absolute')
    expect(bar()!.className).not.toContain('rounded-full')
  })

  it('单图编辑进不了图内编辑：说原因并给「连接源脚本」', async () => {
    await setObjects([{ ...panel, script: undefined } as CanvasObject])
    useWorkspaceStore.getState().enterFastEdit('p1')
    await mount()
    expect(bar()!.textContent).toContain(w('fastEdit.layoutOnly'))
    expect(bar()!.textContent).toContain(w('fastEdit.connectSource'))
  })
})

describe('返回入口只有一个（B3）', () => {
  it('图内编辑态里整条栏上只有一颗返回按钮，Esc 写在脸上', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    expect(backBtns()).toHaveLength(1)
    expect(backBtns()[0].getAttribute('aria-label')).toBe(w('stage.backToCanvas'))
    expect(bar()!.textContent).toContain(t('keycap.esc', { ns: 'common' }))
  })

  it('单图编辑态里没在图内编辑：没有出口按钮（没有「排版」可回）', async () => {
    useWorkspaceStore.getState().enterFastEdit('p1')
    await mount()
    expect(bar()).not.toBeNull()
    expect(backBtns()).toHaveLength(0)
  })

  it('单图编辑态里在图内编辑：整条栏上只有一颗，说「退出图内编辑」', async () => {
    useWorkspaceStore.getState().enterFastEdit('p1')
    useUiStore.getState().setElementPanel('p1')
    await mount()
    expect(backBtns()).toHaveLength(1)
    expect(backBtns()[0].getAttribute('aria-label')).toBe(w('stage.exitElementEdit'))
    expect(backBtns()[0].hasAttribute('data-exit-element-edit')).toBe(true)
  })

  it('两种模式点击都是：退出图内编辑、选中该面板，模式不变', async () => {
    for (const fast of [true, false]) {
      useWorkspaceStore.getState().clear()
      useSelectionStore.getState().clear()
      if (fast) useWorkspaceStore.getState().enterFastEdit('p1')
      useUiStore.getState().setElementPanel('p1')
      await mount()
      await act(async () => (backBtns()[0] as HTMLButtonElement).click())
      expect(useUiStore.getState().elementPanelId).toBeNull()
      expect(useSelectionStore.getState().ids).toEqual(['p1'])
      expect(useWorkspaceStore.getState().mode).toBe(fast ? 'fast_edit' : 'layout')
      await act(async () => root.unmount())
      host.remove()
    }
    await mount() // 交给 afterEach 卸载
  })
})

describe('面包屑只跟当前焦点走（A1）', () => {
  it('没选中：停在画布', async () => {
    await setObjects([panel as CanvasObject, panel2 as CanvasObject])
    await mount()
    expect(crumbs()).toEqual([useDocumentStore.getState().doc.name])
  })

  it('选中一张：延伸到这张图，多图时带序号', async () => {
    await setObjects([panel as CanvasObject, panel2 as CanvasObject])
    await mount()
    await act(async () => useSelectionStore.getState().set(['p2']))
    expect(crumbs().at(-1)).toBe('(b) Fig2_yield')
  })

  it('只有一张图时图名不带序号', async () => {
    await mount()
    await act(async () => useSelectionStore.getState().set(['p1']))
    expect(crumbs().at(-1)).toBe('Fig2_correlation')
  })

  it('多选：说「已选 N 张」，不挑一个冒充全部', async () => {
    await setObjects([panel as CanvasObject, panel2 as CanvasObject])
    await mount()
    await act(async () => useSelectionStore.getState().set(['p1', 'p2']))
    expect(crumbs().at(-1)).toBe(w('stageStrip.selected', { n: 2, names: '(a) (b)' }))
  })

  it('每一段能点回那一层：点画布 = 退出图内编辑并清选区', async () => {
    useUiStore.getState().setElementPanel('p1')
    useSelectionStore.getState().set(['p1'])
    await mount()
    await act(async () => (host.querySelector('[data-crumb="canvas"]') as HTMLButtonElement).click())
    expect(useUiStore.getState().elementPanelId).toBeNull()
    expect(useSelectionStore.getState().ids).toEqual([])
  })

  it('图内编辑：点图那一段 = 退出图内编辑并选中该图', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    await act(async () => (host.querySelector('[data-crumb="figure"]') as HTMLButtonElement).click())
    expect(useUiStore.getState().elementPanelId).toBeNull()
    expect(useSelectionStore.getState().ids).toEqual(['p1'])
  })

  it('图内编辑：图名 / 当前对象；没选元素时是「整张图」（与右栏头部同一个词）', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    expect(bar()?.textContent).toContain('Fig2_correlation')
    expect(objectCrumb()).toBe(t('role.figure', { ns: 'inspector' }))
  })

  it('选中图内元素后第二级说的是那个对象', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    await act(async () => useUiStore.getState().setSelectedGid('axes_0.yticks'))
    expect(objectCrumb()).toBe('Y 刻度')
  })

  it('元素多选时说数量', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    await act(async () => useUiStore.setState({ selectedGids: ['figure', 'axes_0.yticks'] }))
    expect(objectCrumb()).toBe(t('elementsSelected', { ns: 'inspector', count: 2 }))
  })

  it('没进图内编辑时不显示对象那一级', async () => {
    useWorkspaceStore.getState().enterFastEdit('p1')
    useUiStore.setState({ elementPanelId: null, selectedGids: ['axes_0.yticks'] })
    await mount()
    expect(objectCrumb()).toBeNull()
  })

  it('界面上不出现 gid，也不出现「坐标区」（ADR 0030 / 用户裁定：子图的叫法不改）', async () => {
    useWorkspaceStore.getState().enterFastEdit('p1')
    useUiStore.getState().setElementPanel('p1')
    await mount()
    await act(async () => useUiStore.getState().setSelectedGid('axes_0.yticks'))
    expect(bar()?.textContent).not.toContain('axes_0')
    expect(bar()?.textContent).not.toContain('坐标区')
  })
})

describe('教程锚点（ADR 0040）', () => {
  it('单图编辑态不摆锚点：`to-layout` 随「回排版」出口一起删了（没有排版可回）', async () => {
    useWorkspaceStore.getState().enterFastEdit('p1')
    useUiStore.getState().setElementPanel('p1')
    await mount()
    expect(anchors()).toEqual([])
  })

  it('画布排版态不摆锚点（那一屏没有这个动作）', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    expect(anchors()).toEqual([])
  })
})
