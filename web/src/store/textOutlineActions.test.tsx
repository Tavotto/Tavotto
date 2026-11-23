/**
 * 文字转轮廓（⇧⌘O）与复制为 SVG（ADR 0189）走真的 store 与真的快捷键；后端用快照代答
 * （`tests/golden/text_outline.json`，后端那一侧在 `tests/test_text_outline.py` 里按像素核与导出逐点相同）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import golden from '../../../tests/golden/text_outline.json'
import { literal } from '@/i18n'
import { useKeyboard } from '@/hooks/useKeyboard'
import { groupPathOf } from '@/lib/groupTree'
import { emptyProject, type CanvasObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'
import { canCopyAsSvg, copySelectionAsSvg } from './svgCopyActions'
import { canOutlineText, outlineSelectedText } from './textOutlineActions'
import { useUiStore } from './uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

function Host() {
  useKeyboard()
  return null
}
let root: Root
let host: HTMLDivElement
beforeAll(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<Host />))
})
afterAll(async () => {
  await act(async () => root.unmount())
  host.remove()
})

let outlineReply: unknown
let svgReply: unknown
const bodies: Record<string, unknown> = {}
beforeEach(() => {
  outlineReply = { ok: true, results: golden.cases.map((c) => ({ paths: c.paths })) }
  svgReply = { ok: true, svg: '<svg xmlns="http://www.w3.org/2000/svg"/>' }
  globalThis.fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url)
    bodies[u.split('?')[0]] = init?.body ? JSON.parse(String(init.body)) : null
    if (u.includes('/api/text/outline')) return new Response(JSON.stringify(outlineReply), { status: 200 })
    if (u.includes('/api/svg/copy')) return new Response(JSON.stringify(svgReply), { status: 200 })
    return new Response('{}', { status: 200 })
  }) as typeof fetch
  useUiStore.setState({ status: null, tool: 'select' })
})

const text = (id: string, x: number, extra: Partial<CanvasObject> = {}): CanvasObject =>
  ({ id, type: 'text', x, y: 10, w: 20, h: 6, text: `T${id}`, sizePt: 12, bold: false, color: '#1f4e9a', align: 'left', ...extra }) as CanvasObject
const objs = () => useDocumentStore.getState().doc.objects
const past = () => useDocumentStore.getState().past.length
async function seed(items: CanvasObject[], select: string[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ol_' + Math.random())
  useDocumentStore.getState().commit(literal('放'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
  useSelectionStore.getState().set(select)
}

describe('文字转轮廓', () => {
  it('原位替换、一条历史、选中结果；一条路径不另成组，多条路径成一个新组且留在原来的组里', async () => {
    await seed(
      [{ id: 'below', type: 'shape', shape: 'rect', x: 0, y: 0, w: 1, h: 1, strokePt: 1, color: '#000', fill: null } as CanvasObject,
        text('a', 0), text('b', 30, { groupId: 'G' }), { id: 'top', type: 'shape', shape: 'rect', x: 0, y: 0, w: 1, h: 1, strokePt: 1, color: '#000', fill: null } as CanvasObject],
      ['a', 'b'],
    )
    expect(canOutlineText()).toBe(true)
    expect(await outlineSelectedText()).toBe(true)
    const ids = objs().map((o) => o.id)
    expect(ids[0]).toBe('below')
    expect(ids.at(-1)).toBe('top')
    const made = objs().slice(1, -1)
    expect(made).toHaveLength(golden.cases[0].paths.length + golden.cases[1].paths.length)
    expect(made.every((o) => o.type === 'path')).toBe(true)
    // 第一个文字：一条路径，不在任何组里，名字是原文
    expect(groupPathOf(made[0])).toEqual([])
    expect(made[0].name).toBe('Ta')
    // 第二个文字：三条路径成一个新组，挂在原来的组 G 下面
    const tail = made.slice(1)
    const chain = groupPathOf(tail[0])
    expect(chain[0]).toBe('G')
    expect(chain).toHaveLength(2)
    expect(tail.every((o) => groupPathOf(o).join() === chain.join())).toBe(true)
    // 几何原样来自后端（页面 mm）
    expect(made[0].x).toBeCloseTo((golden.cases[0].paths[0] as { x: number }).x, 9)
    expect(past()).toBe(1)
    expect(useSelectionStore.getState().ids).toEqual(made.map((o) => o.id))
    // 发给后端的是导出载荷的形状
    const sent = bodies['/api/text/outline'] as { objects: { type: string; size_pt: number }[] }
    expect(sent.objects.map((o) => o.type)).toEqual(['text', 'text'])
    expect(sent.objects[0].size_pt).toBe(12)
  })

  it('有一个转不成（字体不可用）：其余照转，那个不动，说一句', async () => {
    await seed([text('a', 0), text('b', 30)], ['a', 'b'])
    outlineReply = { ok: true, results: [{ paths: golden.cases[0].paths }, { error: 'fonts' }] }
    expect(await outlineSelectedText()).toBe(true)
    expect(objs().map((o) => o.type)).toEqual(['path', 'text'])
    expect(useUiStore.getState().status).toBeTruthy()
  })

  it('全都转不成：文档不动、不进历史', async () => {
    await seed([text('a', 0)], ['a'])
    outlineReply = { ok: true, results: [{ error: 'fonts' }] }
    expect(await outlineSelectedText()).toBe(false)
    expect(objs().map((o) => o.type)).toEqual(['text'])
    expect(past()).toBe(0)
  })

  it('⇧⌘O：选着文字才消费；锁定的、隐藏的不转', async () => {
    await seed([text('a', 0, { locked: true }), text('b', 30, { hidden: true })], ['a', 'b'])
    expect(canOutlineText()).toBe(false)
    const ev = (o: KeyboardEventInit) => new KeyboardEvent('keydown', { key: 'O', code: 'KeyO', ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true, ...o })
    let e = ev({})
    act(() => {
      window.dispatchEvent(e)
    })
    expect(e.defaultPrevented).toBe(false)
    await seed([text('c', 0)], ['c'])
    e = ev({})
    act(() => {
      window.dispatchEvent(e)
    })
    expect(e.defaultPrevented).toBe(true)
    await vi.waitFor(() => expect(objs()[0].type).toBe('path'))
  })
})

describe('复制为 SVG', () => {
  it('页面 = 选区看得见的范围、坐标平移到左上为原点；剪贴板拿到 SVG（环境认 image/svg+xml 就两种都放）', async () => {
    await seed([text('a', 12), text('b', 40)], ['a', 'b'])
    const written: Record<string, string> = {}
    class FakeItem {
      static supports = (t: string) => t === 'image/svg+xml' || t === 'text/plain'
      items: Record<string, Promise<Blob>>
      constructor(items: Record<string, Promise<Blob>>) {
        this.items = items
      }
    }
    Object.assign(globalThis, { ClipboardItem: FakeItem })
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        write: async (items: FakeItem[]) => {
          for (const [k, v] of Object.entries(items[0].items)) written[k] = await (await v).text()
        },
      },
    })
    expect(canCopyAsSvg()).toBe(true)
    expect(await copySelectionAsSvg()).toBe(true)
    expect(Object.keys(written).sort()).toEqual(['image/svg+xml', 'text/plain'])
    expect(written['text/plain']).toContain('<svg')
    const sent = bodies['/api/svg/copy'] as { objects: { x_mm: number }[]; w_mm: number; h_mm: number }
    expect(sent.objects.map((o) => o.x_mm)).toEqual([0, 28])
    expect(sent.w_mm).toBeCloseTo(48)
    expect(sent.h_mm).toBeCloseTo(6)
  })

  it('write 不让写：退到 writeText；都不行就说没复制成', async () => {
    await seed([text('a', 0)], ['a'])
    let plain = ''
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        write: async () => {
          throw new Error('denied')
        },
        writeText: async (s: string) => {
          plain = s
        },
      },
    })
    expect(await copySelectionAsSvg()).toBe(true)
    expect(plain).toContain('<svg')
    svgReply = { ok: false, error: 'fonts' }
    expect(await copySelectionAsSvg()).toBe(false)
    expect(useUiStore.getState().status).toBeTruthy()
  })

  it('选区里有图：不能复制为 SVG（图的 SVG 走导出）', async () => {
    await seed(
      [text('a', 0), { id: 'p', type: 'panel', fileId: 'f.pdf', fileKind: 'pdf', nativeW: 10, nativeH: 10, overrides: [], x: 0, y: 0, w: 10, h: 10 } as CanvasObject],
      ['a', 'p'],
    )
    expect(canCopyAsSvg()).toBe(false)
  })
})
