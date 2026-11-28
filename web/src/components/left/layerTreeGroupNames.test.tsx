/**
 * 图层树里的组名（ADR 0191 修订 / 0208 前置 6.0）：
 *   - 有名字：标题写名字，个数挪进悬停提示；没名字：照旧「组 · N 个对象」；
 *   - 双击标题 / F2 进入行内改名，Enter 提交、Esc 取消、清空 = 去掉名字；
 *   - 提交 = 同组全员一次写入，⌘Z 一步撤回。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LayerTree } from '@/components/left/LayerTree'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { t } from '@/i18n'
import { groupNameOf } from '@/lib/groupTree'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { emptyProject, type CanvasObject } from '@/types/document'

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

const lt = (key: string, values?: Record<string, unknown>) =>
  t(`layerTree.${key}`, { ns: 'workspace', ...(values ?? {}) })

const rect = (id: string, over: Record<string, unknown> = {}): CanvasObject =>
  ({ id, type: 'shape', shape: 'rect', x: 0, y: 0, w: 10, h: 10, strokePt: 1, color: '#000000', fill: null, ...over }) as unknown as CanvasObject

let root: Root
async function mount(objects: CanvasObject[]) {
  useSelectionStore.getState().set([])
  useDocumentStore.setState({ doc: { ...emptyProject(), objects }, past: [], future: [], txn: null } as never)
  const host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <LayerTree />
      </TooltipProvider>,
    )
  })
}
afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
})
beforeEach(() => {
  document.body.innerHTML = ''
})

const groupRow = (gid: string) => document.querySelector<HTMLElement>(`li[data-layer="g:${gid}"]`)!
const labelOf = (gid: string) => groupRow(gid).querySelector<HTMLElement>('[data-tree-label]')!
const box = (gid: string) => groupRow(gid).querySelector<HTMLInputElement>('input')
const settle = () => act(async () => new Promise<void>((r) => setTimeout(r, 10)))
const names = () => useDocumentStore.getState().doc.objects.map((o) => o.groupNames ?? null)

const dbl = (el: Element) => act(async () => void el.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })))
const key = (el: Element, k: string) =>
  act(async () => void el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true })))
async function typeAndEnter(gid: string, text: string) {
  const input = box(gid)!
  input.value = text
  await key(input, 'Enter')
  await settle()
}

const doc2 = () => [
  rect('a', { groupId: 'g1', groupNames: { g1: '机理图' } }),
  rect('b', { groupId: 'g1', groupNames: { g1: '机理图' } }),
  rect('c', { groupId: 'g2' }),
  rect('d', { groupId: 'g2' }),
]

describe('组行的标题', () => {
  it('有名字写名字，个数进悬停提示；没名字照旧', async () => {
    await mount(doc2())
    expect(labelOf('g1').textContent).toBe('机理图')
    expect(labelOf('g1').getAttribute('title')).toBe(lt('groupLabel', { count: 2 }))
    expect(groupRow('g1').getAttribute('aria-label')).toBe(lt('groupNamedAria', { name: '机理图', count: 2 }))
    expect(labelOf('g2').textContent).toBe(lt('groupLabel', { count: 2 }))
    expect(labelOf('g2').getAttribute('title')).toBeNull()
  })

  it('旧文档（没有 groupNames）：标题与以前一样', async () => {
    await mount([rect('a', { groupId: 'g1' }), rect('b', { groupId: 'g1' })])
    expect(labelOf('g1').textContent).toBe(lt('groupLabel', { count: 2 }))
  })

  it('同组名字不一致：显示 z 序第一个非空的（树里自底向上第一个）', async () => {
    await mount([
      rect('a', { groupId: 'g1' }),
      rect('b', { groupId: 'g1', groupNames: { g1: '乙' } }),
      rect('c', { groupId: 'g1', groupNames: { g1: '丙' } }),
    ])
    expect(labelOf('g1').textContent).toBe('乙')
  })
})

describe('双击改名', () => {
  it('双击标题 → 输入框（带着现名）→ Enter：同组全员一次写入，⌘Z 一步撤回', async () => {
    await mount(doc2())
    await dbl(labelOf('g1'))
    expect(box('g1')!.value).toBe('机理图')
    await typeAndEnter('g1', '受体通路')
    expect(box('g1')).toBeNull()
    expect(labelOf('g1').textContent).toBe('受体通路')
    expect(names()).toEqual([{ g1: '受体通路' }, { g1: '受体通路' }, null, null])
    expect(useDocumentStore.getState().past).toHaveLength(1) // 一次 commit
    await act(async () => useDocumentStore.getState().undo())
    expect(names()).toEqual([{ g1: '机理图' }, { g1: '机理图' }, null, null])
    expect(labelOf('g1').textContent).toBe('机理图')
  })

  it('没名字的组：输入框是空的，占位是原来的标题；提交后得到名字', async () => {
    await mount(doc2())
    await dbl(labelOf('g2'))
    expect(box('g2')!.value).toBe('')
    expect(box('g2')!.placeholder).toBe(lt('groupLabel', { count: 2 }))
    await typeAndEnter('g2', '  对照组  ')
    expect(labelOf('g2').textContent).toBe('对照组') // 去首尾空白
    expect(names()).toEqual([{ g1: '机理图' }, { g1: '机理图' }, { g2: '对照组' }, { g2: '对照组' }])
  })

  it('清空 = 去掉名字，回到「组 · N 个对象」，键也拿掉', async () => {
    await mount(doc2())
    await dbl(labelOf('g1'))
    await typeAndEnter('g1', '')
    expect(labelOf('g1').textContent).toBe(lt('groupLabel', { count: 2 }))
    expect(names()).toEqual([null, null, null, null])
  })

  it('Esc 取消：不写文档、不进历史，焦点回到这一行', async () => {
    await mount(doc2())
    groupRow('g1').focus()
    await key(groupRow('g1'), 'F2') // 键盘也能进
    expect(box('g1')).not.toBeNull()
    box('g1')!.value = '不该写进去'
    await key(box('g1')!, 'Escape')
    await settle()
    expect(box('g1')).toBeNull()
    expect(names()[0]).toEqual({ g1: '机理图' })
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(document.activeElement).toBe(groupRow('g1'))
  })

  it('名字没变：不留空历史', async () => {
    await mount(doc2())
    await dbl(labelOf('g1'))
    await typeAndEnter('g1', '机理图')
    expect(useDocumentStore.getState().past).toHaveLength(0)
  })

  it('同组名字不一致时，改名把全员统一', async () => {
    await mount([
      rect('a', { groupId: 'g1', groupNames: { g1: '甲' } }),
      rect('b', { groupId: 'g1', groupNames: { g1: '乙' } }),
      rect('c', { groupId: 'g1' }),
    ])
    await dbl(labelOf('g1'))
    await typeAndEnter('g1', '统一名')
    expect(names()).toEqual([{ g1: '统一名' }, { g1: '统一名' }, { g1: '统一名' }])
    expect(groupNameOf(useDocumentStore.getState().doc.objects, 'g1')).toBe('统一名')
  })

  it('嵌套组：改内层不动外层的名字', async () => {
    await mount([
      rect('a', { groupId: 'g2', groupPath: ['g1', 'g2'], groupNames: { g1: '外', g2: '内' } }),
      rect('b', { groupId: 'g1', groupNames: { g1: '外' } }),
    ])
    await dbl(labelOf('g2'))
    await typeAndEnter('g2', '里')
    expect(names()).toEqual([{ g1: '外', g2: '里' }, { g1: '外' }])
  })
})

describe('改名框的样子', () => {
  it('双击有名字的组：原名全选；框与静态标题左缘对齐（负外边距抵掉内边距 + 边线）', async () => {
    await mount(doc2())
    await dbl(labelOf('g1'))
    const input = box('g1')!
    expect(input.selectionStart).toBe(0)
    expect(input.selectionEnd).toBe('机理图'.length)
    expect(input.className).toContain('-ml-[7px]')
    expect(input.className).toContain('placeholder:text-ink-3')
  })
})
