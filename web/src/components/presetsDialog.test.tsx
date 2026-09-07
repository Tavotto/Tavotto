/**
 * 科研预设对话框（审计 T30）：九种预设各有一格结构预览，预览画的**就是**插入
 * 时落到画布上的那一组对象——同一个 `buildPreset()`，不是另画的图标。
 *
 * jsdom 没有布局，量不出缩放后的像素；这里钉的是「预览里的对象与插入的对象
 * 同源」：每格预览里的画布对象节点数、类型与形状序列，与 `insertPreset` 真的
 * 放进文档的那一批逐项相等。把预览换成任何一份手写图标，这条就红。
 *
 * 光比「节点数」与「插入的那批签名」还不够——那两条都不看**预览画出来的是
 * 什么**：把预览喂进一份改过的定义（比如整组转 33°），节点数一样、插入那侧
 * 也没动，两条照样绿（实测变异存活）。所以再钉一条：把预览渲染出来的每个对象
 * 节点，与「拿 `buildPreset` 同一份定义直接喂 `ObjectView`」渲出来的节点做
 * 标记级比对（去掉随机 id）。两侧的渲染器同一个，**喂进去的定义不同就会分叉**。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ObjectView } from '@/canvas/ObjectView'
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
/** 参照渲染用的第二个 root：拿定义直接喂 ObjectView，与预览比标记 */
let refContainer: HTMLDivElement
let refRoot: Root

/** 去掉每次 `buildPreset` 都换的随机 id / key，只留结构与样式 */
const normalize = (el: Element) =>
  el.outerHTML.replace(/data-object-id="[^"]*"/g, '').replace(/\bid="[^"]*"/g, '')

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
  refContainer = document.createElement('div')
  document.body.appendChild(refContainer)
  refRoot = createRoot(refContainer)
})

afterEach(() => {
  act(() => root.unmount())
  act(() => refRoot.unmount())
  container.remove()
  refContainer.remove()
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

  it('预览画出来的就是那份定义：与直接喂 ObjectView 的渲染逐个节点相同', () => {
    open()
    for (const id of PRESET_IDS) {
      const objs = buildPreset(id, { x: 0, y: 0 })
      act(() =>
        refRoot.render(
          <TooltipProvider>
            {objs.map((o) => (
              <ObjectView key={o.id} obj={o} />
            ))}
          </TooltipProvider>,
        ),
      )
      const shown = [
        ...document
          .querySelector(`[data-preset-preview="${id}"]`)!
          .querySelectorAll('[data-object-id]'),
      ].map(normalize)
      const expected = [...refContainer.querySelectorAll('[data-object-id]')].map(normalize)
      expect(expected.length, id).toBe(objs.length)
      expect(shown, id).toEqual(expected)
    }
  })

  it('预览不吃指针与焦点（inert）', () => {
    open()
    const preview = document.querySelector('[data-preset-preview="dimension"]')!
    expect(preview.hasAttribute('inert')).toBe(true)
    expect(preview.getAttribute('aria-hidden')).toBe('true')
  })

  /**
   * 九格的可访问 role 必须是 button（评审 #299-3）。
   *
   * 之前每个 `<button>` 上直接写了 `role="listitem"`——显式 role **替换**
   * 掉原生语义，于是辅助技术树里那九格成了可聚焦的列表项，而不是能激活的
   * 控件；读屏用户听不到「按钮」，也拿不到「按回车会发生什么」这条信息。
   *
   * 判据量的是**可访问 role**（显式 role 赢，没有才用标签的隐式 role），
   * 不是「有没有写 role 属性」：写成 `role="option"` / `role="link"` 同样
   * 是这个缺陷，而「没写 role 属性」那条判据看不见它们。
   */
  it('九格的可访问 role 是 button，外层的列表语义还在', () => {
    open()
    /** 标签的隐式 role（只列这几格用到的） */
    const IMPLICIT: Record<string, string> = { BUTTON: 'button', LI: 'listitem', UL: 'list' }
    const roleOf = (el: Element) => el.getAttribute('role') ?? IMPLICIT[el.tagName] ?? null

    const cells = [...document.querySelectorAll('[data-preset]')]
    expect(cells.length).toBe(PRESET_IDS.length)
    for (const cell of cells) {
      expect(roleOf(cell), cell.getAttribute('data-preset') ?? '').toBe('button')
      // 列表语义没有因此丢掉：每格被一个 listitem 包着，外层是 list
      expect(roleOf(cell.parentElement!)).toBe('listitem')
      expect(roleOf(cell.parentElement!.parentElement!)).toBe('list')
    }
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
