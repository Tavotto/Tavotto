/**
 * 工作区上下文栏（审计 T01）。验收原话：**每次都能指出当前对象和修改范围；
 * 切换模式时画布不意外移动**。
 *
 * 「我在改哪一层」此前散在三处：顶栏一个「快速编辑」标签兼返回按钮、画布上方
 * 一条「图内编辑：Fig2 / 返回画布 Esc」、快速编辑另有一条带「画布排版」出口。
 * 这里量的是**只剩一个主返回入口**、面包屑从图到对象、以及教程锚点还挂在真
 * 按钮上（`docs/adr/0040` 明说锚点是稳定的 `data-*`，改名就把教程打断）。
 *
 * 视口那一条在 `store/workspace.test.ts`——它是 store 层的事实，不是渲染。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal, t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { WorkspaceContextBar } from './WorkspaceContextBar'
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
        <WorkspaceContextBar />
      </TooltipProvider>,
    )
  })
  await act(async () => {})
}

const bar = () => host.querySelector('[data-workspace-context-bar]') as HTMLElement | null
const backBtns = () => [...host.querySelectorAll('[data-context-back]')] as HTMLElement[]
const objectCrumb = () => host.querySelector('[data-context-object]')?.textContent ?? null
const anchors = () =>
  [...host.querySelectorAll('[data-onboarding-anchor]')].map(
    (n) => (n as HTMLElement).dataset.onboardingAnchor,
  )

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

describe('什么时候出现', () => {
  it('画布排版且没进图内编辑：不出现（顶栏已经说了在哪份文档）', async () => {
    await mount()
    expect(bar()).toBeNull()
  })

  it('图内编辑：出现，模式记为 layout（还在画布这一层）', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    expect(bar()?.dataset.workspaceMode).toBe('layout')
  })

  it('快速编辑：出现，模式记为 fast_edit', async () => {
    useWorkspaceStore.getState().enterFastEdit('p1')
    await mount()
    expect(bar()?.dataset.workspaceMode).toBe('fast_edit')
  })
})

describe('返回入口只有一个', () => {
  it('图内编辑态里整条栏上只有一颗返回按钮', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    expect(backBtns()).toHaveLength(1)
    expect(backBtns()[0].textContent).toContain(t('stage.backToCanvas', { ns: 'workspace' }))
  })

  it('快速编辑态里也只有一颗（此前这里有「画布排版」+ 顶栏徽章两个）', async () => {
    useWorkspaceStore.getState().enterFastEdit('p1')
    await mount()
    expect(backBtns()).toHaveLength(1)
  })

  it('图内编辑的返回：退出图内编辑并选中那个面板，属性页落在面板上', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    await act(async () => (backBtns()[0] as HTMLButtonElement).click())
    expect(useUiStore.getState().elementPanelId).toBeNull()
    expect(useSelectionStore.getState().ids).toEqual(['p1'])
  })

  it('快速编辑的返回：回画布排版', async () => {
    useWorkspaceStore.getState().enterFastEdit('p1')
    await mount()
    await act(async () => (backBtns()[0] as HTMLButtonElement).click())
    expect(useWorkspaceStore.getState().mode).toBe('layout')
  })

  it('图内编辑时返回按钮把 Esc 写在脸上（键盘那条路还在）', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    expect(backBtns()[0].textContent).toContain(t('keycap.esc', { ns: 'common' }))
  })
})

describe('面包屑：图 / 当前对象', () => {
  it('图名始终在；没选元素时没有第二级', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    expect(bar()?.textContent).toContain('Fig2_correlation')
    expect(objectCrumb()).toBeNull()
  })

  it('选中图内元素后第二级说的是那个对象', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    await act(async () => useUiStore.getState().setSelectedGid('axes_0.yticks'))
    expect(objectCrumb()).toBe('Y 刻度')
  })

  it('多选时说数量，不挑一个冒充全部', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    await act(async () => useUiStore.setState({ selectedGids: ['figure', 'axes_0.yticks'] }))
    expect(objectCrumb()).toBe(t('elementsSelected', { ns: 'inspector', count: 2 }))
  })

  it('没进图内编辑时不显示对象那一级（快速编辑但只排版）', async () => {
    useWorkspaceStore.getState().enterFastEdit('p1')
    useUiStore.setState({ elementPanelId: null, selectedGids: ['axes_0.yticks'] })
    await mount()
    expect(objectCrumb()).toBeNull()
  })

  it('界面上不出现 gid（ADR 0030：普通界面不摆内部标识）', async () => {
    useWorkspaceStore.getState().enterFastEdit('p1')
    useUiStore.getState().setElementPanel('p1')
    await mount()
    await act(async () => useUiStore.getState().setSelectedGid('axes_0.yticks'))
    expect(bar()?.textContent).not.toContain('axes_0')
  })
})

describe('教程锚点还挂在真按钮上（ADR 0040）', () => {
  it('快速编辑态两个锚点都在，且各挂一颗按钮', async () => {
    useWorkspaceStore.getState().enterFastEdit('p1')
    await mount()
    expect(anchors().sort()).toEqual(['add-to-layout', 'to-layout'])
    for (const a of ['add-to-layout', 'to-layout']) {
      const node = host.querySelector(`[data-onboarding-anchor="${a}"]`)
      expect(node?.tagName).toBe('BUTTON')
      expect((node as HTMLButtonElement).disabled).toBe(false)
    }
  })

  it('画布排版态不摆这两个锚点（那一屏没有这两个动作）', async () => {
    useUiStore.getState().setElementPanel('p1')
    await mount()
    expect(anchors()).toEqual([])
  })
})
