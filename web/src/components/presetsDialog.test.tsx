/**
 * 科研预设对话框（审计 T30）：九种预设各有一格结构预览，预览画的**就是**插入
 * 时落到画布上的那一组对象——同一个 `buildPreset()`，不是另画的图标。
 *
 * jsdom 没有布局，量不出缩放后的像素；这里钉的是「预览里的对象与插入的对象
 * 同源」：每格预览里的画布对象节点数、类型与形状序列，与 `insertPreset` 真的
 * 放进文档的那一批逐项相等。把预览换成任何一份手写图标，这条就红。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { buildPreset, insertPreset, PRESET_IDS } from '@/lib/presets'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject, type CanvasObject } from '@/types/document'
import { PresetsDialog } from './PresetsDialog'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/** 对象的「结构签名」：类型 + 形状 / 端型 / 文字——与 id、坐标无关 */
const signature = (o: CanvasObject): string => {
  switch (o.type) {
    case 'arrow':
      return `arrow:${o.headStart ?? ''}>${o.headEnd ?? ''}:${o.dash ?? 'solid'}`
    case 'shape':
      return `shape:${o.shape}:${o.dash ?? 'solid'}:${o.rotationDeg ?? 0}`
    case 'text':
      return `text:${o.text}:${o.align}`
    default:
      return o.type
  }
}

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  )
  localStorage.clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_presets')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
})

const open = () =>
  act(() =>
    root.render(
      <TooltipProvider>
        <PresetsDialog open onClose={() => {}} />
      </TooltipProvider>,
    ),
  )

describe('PresetsDialog', () => {
  it('九种预设各有一格预览，预览里画的是画布对象视图（不是图标）', () => {
    open()
    for (const id of PRESET_IDS) {
      const cell = document.querySelector(`[data-preset="${id}"]`)
      expect(cell, id).not.toBeNull()
      const preview = cell!.querySelector(`[data-preset-preview="${id}"]`)
      expect(preview, id).not.toBeNull()
      expect(preview!.querySelectorAll('[data-object-id]').length, id).toBe(
        buildPreset(id, { x: 0, y: 0 }).length,
      )
    }
  })

  it('预览与插入同一份定义：结构签名逐项相等', () => {
    open()
    for (const id of PRESET_IDS) {
      const previewed = buildPreset(id, { x: 0, y: 0 }).map(signature)
      const before = useDocumentStore.getState().doc.objects.length
      act(() => insertPreset(id))
      const inserted = useDocumentStore.getState().doc.objects.slice(before).map(signature)
      expect(inserted, id).toEqual(previewed)
      // 成组落地：同一 groupId
      const groups = new Set(useDocumentStore.getState().doc.objects.slice(before).map((o) => o.groupId))
      expect(groups.size, id).toBe(1)
    }
  })

  it('预览不吃指针与焦点（inert）', () => {
    open()
    const preview = document.querySelector('[data-preset-preview="dimension"]')!
    expect(preview.hasAttribute('inert')).toBe(true)
    expect(preview.getAttribute('aria-hidden')).toBe('true')
  })

  it('每格是带名字的可点按钮：点击插入并成组', () => {
    open()
    const cell = document.querySelector<HTMLButtonElement>('[data-preset="scalebar"]')!
    expect(cell.getAttribute('aria-label')).toBeTruthy()
    act(() => cell.click())
    const objs = useDocumentStore.getState().doc.objects
    expect(objs.map(signature)).toEqual(buildPreset('scalebar', { x: 0, y: 0 }).map(signature))
  })
})
