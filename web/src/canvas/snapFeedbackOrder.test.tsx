/**
 * 吸附反馈的叠放顺序（Codex #1036）：选中元素的实线框与反馈的自身加粗边同坐标同色，反馈必须画在选中框
 * **之上**（否则虚线被盖住），又在手柄**之下**（手柄要看得见、点得着）。主语：渲染出来的 OverlaySvg DOM 顺序。
 */
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { literal } from '@/i18n'
import type { Manifest, ManifestElement } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { OverlaySvg } from './OverlaySvg'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const axes: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图 1',
  bbox: [0.15, 0.3, 0.6, 0.5],
  editable: [{ prop: 'position', type: 'rect', value: [0.15, 0.2, 0.6, 0.5] }],
  draggable: false,
  resizable: true,
}
const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [100, 80],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    axes,
  ],
}

let root: Root
let container: HTMLDivElement

beforeEach(async () => {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', elementPanelId: 'p1', selectedGids: [axes.gid] })
  useSelectionStore.getState().clear()
  useInteractionStore.getState().end()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_snap_fb_order')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push({
      id: 'p1', type: 'panel', x: 10, y: 20, w: 100, h: 80, fileId: 'Fig1.pdf', fileKind: 'pdf',
      nativeW: 100, nativeH: 80, script: 'fig.py', overrides: [],
    } as PanelObject)
  })
  seedExactRender(useDocumentStore.getState().doc.objects[0] as PanelObject, manifest)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(createElement(OverlaySvg)))
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  useInteractionStore.getState().end()
  useUiStore.setState({ elementPanelId: null, selectedGids: [] })
})

const follows = (a: Node, b: Node) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

describe('吸附反馈的叠放顺序', () => {
  it('图内编辑态：自身加粗边在选中框之后、在缩放手柄之前（只画一份）', async () => {
    await act(async () =>
      useInteractionStore
        .getState()
        .setSnap([40], [], [{ axis: 'x', v: 40, self: { x: 25, y: 44, w: 60, h: 40 } }]),
    )
    expect(container.querySelector('[data-snap-self]')).toBeNull() // 不再画自身虚线框
    expect(container.querySelectorAll('[data-snap-self-edge]')).toHaveLength(1)
    const selected = container.querySelector('[data-element-box="selected"]')!
    const self = container.querySelector('[data-snap-self-edge]')!
    const handle = container.querySelector('[data-element-handle]')
    expect(selected).not.toBeNull()
    expect(handle).not.toBeNull()
    expect(follows(selected, self)).toBe(true)
    expect(follows(self, handle!)).toBe(true)
  })
})
