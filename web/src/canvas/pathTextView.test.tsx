/**
 * 路径文字（ADR 0190）在画布上：照后端「文字转轮廓」的结果画（代答用后端生成的 `tests/golden/path_text_vectors.json`
 * 里的 preview），等结果时浏览器 `<textPath>` 顶着；就地输入；起点柄一次拖动一条历史；属性页。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import vectors from '../../../tests/golden/path_text_vectors.json'
import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { PathTextSection } from '@/components/inspector/PathTextSection'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { getTransform, mmToViewX, mmToViewY, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type CanvasObject, type PathTextObject } from '@/types/document'
import { ObjectView } from './ObjectView'
import { PathTextHandles } from './PathTextHandles'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const P = vectors.preview.object
const obj = (over: Partial<PathTextObject> = {}): PathTextObject => ({
  id: 'pt',
  type: 'pathText',
  x: 15,
  y: 12,
  w: P.w_mm,
  h: P.h_mm,
  text: P.text,
  sizePt: P.size_pt,
  bold: false,
  color: P.color,
  subpaths: P.subpaths as PathTextObject['subpaths'],
  ...over,
})

let calls: unknown[] = []
let root: Root
let el: HTMLDivElement
const objs = () => useDocumentStore.getState().doc.objects
const past = () => useDocumentStore.getState().past.length

async function seed(items: CanvasObject[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ptv_' + Math.random())
  useDocumentStore.getState().commit(literal('放'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
}
async function mount(node: React.ReactNode) {
  el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
  await act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))
}
const settle = async (ms = 200) =>
  act(async () => {
    await new Promise((r) => setTimeout(r, ms))
  })

beforeEach(() => {
  localStorage.clear()
  calls = []
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 })
  useUiStore.setState({ tool: 'select', editingTextId: null, elementPanelId: null })
  useSelectionStore.getState().clear()
  globalThis.fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (String(url).includes('/api/text/outline')) {
      calls.push(JSON.parse(String(init?.body)))
      return new Response(JSON.stringify({ ok: true, results: [{ paths: vectors.preview.paths }] }), { status: 200 })
    }
    return new Response('{}', { status: 200 })
  }) as typeof fetch
})
afterEach(async () => {
  await act(async () => root?.unmount())
  el?.remove()
  useInteractionStore.getState().end()
})

describe('画布照后端的字形画', () => {
  it('先用 textPath 顶着，结果回来换成后端的轮廓；发的是挪到原点、不带旋转的对象', async () => {
    const o = obj({ rotationDeg: 20, text: 'Tavotto' })
    await seed([o])
    await mount(<ObjectView obj={o} />)
    expect(el.querySelector('[data-path-text-fallback]')?.textContent).toBe('Tavotto')
    expect(el.querySelector('[data-path-text-glyphs]')).toBeNull()
    await settle()
    expect(el.querySelector('[data-path-text-fallback]')).toBeNull()
    const glyphs = el.querySelector('[data-path-text-glyphs]')!
    expect(glyphs.querySelectorAll('path')).toHaveLength(vectors.preview.paths.length)
    // 轮廓按它自己的框落位（mm，外层 scale 换到世界坐标）
    const first = vectors.preview.paths[0]
    expect(glyphs.querySelector('g')!.getAttribute('transform')).toBe(`translate(${first.x} ${first.y})`)
    expect(glyphs.querySelector('path')!.getAttribute('fill')).toBe(P.color)
    expect(calls).toHaveLength(1)
    const sent = (calls[0] as { objects: Record<string, unknown>[] }).objects[0]
    expect([sent.type, sent.x_mm, sent.y_mm, sent.rotation_deg, sent.text]).toEqual(['pathText', 0, 0, undefined, 'Tavotto'])
    // 外层 div 带着旋转（与其它标注同一套），命中交给沿路径的透明带
    const div = el.querySelector<HTMLElement>('[data-object-id="pt"]')!
    expect(div.style.transform).toBe('rotate(20deg)')
    expect(div.style.pointerEvents).toBe('none')
    expect(el.querySelector('[data-hit-path]')).not.toBeNull()
  })

  it('同样的字和路径只问一次：挪位置不重新问', async () => {
    const o = obj({ text: 'Cached' })
    await seed([o])
    await mount(<ObjectView obj={o} />)
    await settle()
    await act(async () => root.render(<TooltipProvider><ObjectView obj={{ ...o, x: 40, y: 30 }} /></TooltipProvider>))
    await settle()
    expect(calls).toHaveLength(1)
    expect(el.querySelector('[data-path-text-glyphs]')).not.toBeNull()
  })
})

describe('就地输入', () => {
  it('双击进入输入；Enter 提交一条历史；Esc 放弃', async () => {
    const o = obj({ text: 'Old' })
    await seed([o])
    await mount(<ObjectView obj={o} />)
    await act(async () => {
      el.querySelector('[data-object-id="pt"]')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    })
    expect(useUiStore.getState().editingTextId).toBe('pt')
    const input = el.querySelector<HTMLInputElement>('[data-path-text-input]')!
    expect(document.activeElement).toBe(input)
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      set.call(input, 'New words')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    expect((objs()[0] as PathTextObject).text).toBe('New words')
    expect(past()).toBe(1)
    expect(useUiStore.getState().editingTextId).toBeNull()

    await act(async () => useUiStore.getState().setEditingText('pt'))
    const again = el.querySelector<HTMLInputElement>('[data-path-text-input]')!
    await act(async () => {
      again.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(past()).toBe(1)
  })
})

describe('起点柄', () => {
  it('选中时描出路径与起点柄；拖它沿路径挪起点，一次拖动一条历史', async () => {
    const line = obj({ x: 10, y: 20, w: 40, h: 0.5, subpaths: [{ closed: false, nodes: [{ p: [0, 0.5] }, { p: [1, 0.5] }] }] })
    await seed([line])
    useSelectionStore.getState().set(['pt'])
    await mount(
      <svg>
        <PathTextHandles t={getTransform()} />
      </svg>,
    )
    const handle = el.querySelector<SVGRectElement>('[data-path-text-start-handle]')!
    expect(handle).not.toBeNull()
    const t = getTransform()
    await act(async () => {
      handle.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, clientX: mmToViewX(10, t), clientY: mmToViewY(20, t) }))
    })
    for (const x of [15, 20, 30]) {
      await act(async () => {
        window.dispatchEvent(new MouseEvent('pointermove', { clientX: mmToViewX(x, t), clientY: mmToViewY(23, t), bubbles: true }))
      })
    }
    await act(async () => {
      window.dispatchEvent(new MouseEvent('pointerup', { clientX: mmToViewX(30, t), clientY: mmToViewY(23, t), bubbles: true }))
    })
    expect((objs()[0] as PathTextObject).startOffset).toBeCloseTo(0.5, 6)
    expect(past()).toBe(1)
  })

  it('编辑锚点、别的工具、多选时不出现', async () => {
    await seed([obj(), obj({ id: 'other' })])
    useSelectionStore.getState().set(['pt', 'other'])
    await mount(
      <svg>
        <PathTextHandles t={getTransform()} />
      </svg>,
    )
    expect(el.querySelector('[data-path-text-handles]')).toBeNull()
  })
})

describe('属性页', () => {
  it('翻转、对齐、起点各一条历史；起点按百分比', async () => {
    await seed([obj()])
    const o = () => objs()[0] as PathTextObject
    await mount(<PathTextSection objs={[o()]} />)
    await act(async () => el.querySelector<HTMLButtonElement>('[data-path-text-flip]')!.click())
    expect(o().flip).toBe(true)
    await act(async () => root.render(<TooltipProvider><PathTextSection objs={[o()]} /></TooltipProvider>))
    await act(async () => el.querySelector<HTMLButtonElement>('[data-path-text-flip]')!.click())
    expect(o().flip).toBeUndefined()
    const center = [...el.querySelectorAll<HTMLElement>('[role="radio"]')].find((b) => b.getAttribute('aria-label')?.includes('居中') || b.getAttribute('aria-label')?.toLowerCase().includes('center'))!
    await act(async () => center.click())
    expect(o().textAlign).toBe('center')
    expect(past()).toBe(3)
  })
})
