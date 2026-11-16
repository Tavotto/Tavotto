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
import { usePathEditStore } from '@/store/pathEditStore'
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
  usePathEditStore.getState().exit()
  globalThis.fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (String(url).includes('/api/text/outline')) {
      calls.push(JSON.parse(String(init?.body)))
      return new Response(JSON.stringify({ ok: true, results: [{ paths: vectors.preview.paths, auto_offset_pt: 4.567 }] }), { status: 200 })
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

describe('离线', () => {
  const setInput = async (input: HTMLInputElement, v: string) => {
    await act(async () => {
      input.focus()
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      set.call(input, v)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
  }

  it('没写 = 自动：框里用占位样式显示后端算的数；填 0 落 0（贴线）；清空回到自动', async () => {
    await seed([obj({ text: 'Off' })])
    const o = () => objs()[0] as PathTextObject
    await mount(<PathTextSection objs={[o()]} />)
    await settle()
    const field = () => el.querySelector<HTMLInputElement>('input[data-inspector-prop="path-text-offset"]')!
    expect(field().value).toBe('')
    expect(field().placeholder).toMatch(/4\.6/)
    await setInput(field(), '0')
    expect(o().baselineOffsetPt).toBe(0)
    await act(async () => root.render(<TooltipProvider><PathTextSection objs={[o()]} /></TooltipProvider>))
    expect(field().value).toBe('0')
    await setInput(field(), '')
    expect(o().baselineOffsetPt).toBeUndefined()
  })

  it('画布发给后端的请求带上一起成组的那条线的线宽', async () => {
    const line = { id: 'L', type: 'path', x: 15, y: 12, w: 60, h: 20, subpaths: P.subpaths, strokePt: 2, color: '#000000', fill: null } as CanvasObject
    const o = obj({ text: 'Lw', lineId: 'L' })
    await seed([line, o])
    await mount(<ObjectView obj={o} />)
    await settle()
    const sent = (calls.at(-1) as { objects: Record<string, unknown>[] }).objects[0]
    expect(sent.line_width_pt).toBe(2)
    expect(sent.baseline_offset_pt).toBeUndefined()
  })
})


describe('调间隙', () => {
  const lineObj = () =>
    obj({ x: 10, y: 20, w: 40, h: 0.5, text: 'Gap', subpaths: [{ closed: false, nodes: [{ p: [0, 0.5] }, { p: [1, 0.5] }] }] })
  const o = () => objs()[0] as PathTextObject

  it('画布间隙柄：沿法线拖改离线，字头朝上时往上拖变大；拖动中浮动栏让开；一次拖动一条历史', async () => {
    await seed([lineObj()])
    useSelectionStore.getState().set(['pt'])
    await mount(<svg><PathTextHandles t={getTransform()} /></svg>)
    await settle()
    const h = el.querySelector<SVGCircleElement>('[data-path-text-gap-handle]')!
    expect(h.dataset.auto).toBe('true')
    const t = getTransform()
    const at = (y: number) => ({ clientX: mmToViewX(30, t), clientY: mmToViewY(y, t), bubbles: true })
    await act(async () => h.dispatchEvent(new PointerEvent('pointerdown', { ...at(19), button: 0 })))
    let kindWhileDragging = ''
    for (const y of [19, 18.5, 18.25 - 3 * 0.3528]) {
      await act(async () => window.dispatchEvent(new MouseEvent('pointermove', at(y))))
      kindWhileDragging = useInteractionStore.getState().kind
    }
    await act(async () => window.dispatchEvent(new MouseEvent('pointerup', at(18.25 - 3 * 0.3528))))
    expect(kindWhileDragging).toBe('pathTextGap')
    expect(useInteractionStore.getState().kind).toBe('none')
    // 线在 y = 20.25；往上 3 pt 再多 2 mm
    expect(o().baselineOffsetPt).toBeCloseTo(8.7, 1)
    expect(past()).toBe(1)
  })

  it('⇧ 拖：靠近 0 吸到 0（贴线），靠近自动值吸回自动（字段删掉）', async () => {
    await seed([lineObj()])
    useSelectionStore.getState().set(['pt'])
    await mount(<svg><PathTextHandles t={getTransform()} /></svg>)
    await settle() // 自动值 4.567 回来了
    const t = getTransform()
    const drag = async (toMm: number) => {
      const h = el.querySelector<SVGCircleElement>('[data-path-text-gap-handle]')!
      await act(async () => h.dispatchEvent(new PointerEvent('pointerdown', { clientX: mmToViewX(30, t), clientY: mmToViewY(19, t), bubbles: true, button: 0 })))
      for (const y of [19.5, toMm]) {
        await act(async () => window.dispatchEvent(new MouseEvent('pointermove', { clientX: mmToViewX(30, t), clientY: mmToViewY(y, t), bubbles: true, shiftKey: true })))
      }
      await act(async () => window.dispatchEvent(new MouseEvent('pointerup', { clientX: mmToViewX(30, t), clientY: mmToViewY(toMm, t), bubbles: true, shiftKey: true })))
    }
    await drag(20.25 - 0.3) // 0.85 pt：吸到 0
    expect(o().baselineOffsetPt).toBe(0)
    await drag(20.25 - 4.2 * 0.3528) // 4.2 pt：离自动 4.567 不到 1.5 pt
    expect(o().baselineOffsetPt).toBeUndefined()
  })

  it('⌥⇧↑ / ⌥⇧↓：选区全是路径文字时每次 0.5 pt（从自动值起步）；别的选区照旧是微调', async () => {
    const { useKeyboard } = await import('@/hooks/useKeyboard')
    function Host() {
      useKeyboard()
      return null
    }
    const shape = { id: 'r', type: 'shape', shape: 'rect', x: 0, y: 0, w: 5, h: 5, strokePt: 1, color: '#000', fill: null } as CanvasObject
    await seed([lineObj(), shape])
    await mount(<><Host /><ObjectView obj={lineObj()} /></>)
    await settle()
    useSelectionStore.getState().set(['pt'])
    const key = async (k: string) =>
      act(async () => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: k, code: k, altKey: true, shiftKey: true, bubbles: true, cancelable: true }))
      })
    await key('ArrowUp')
    expect(o().baselineOffsetPt).toBeCloseTo(5.1, 6) // 4.567 → 4.6 + 0.5（一位小数）
    await key('ArrowDown')
    await key('ArrowDown')
    expect(o().baselineOffsetPt).toBeCloseTo(4.1, 6)
    expect(o().x).toBe(10)
    // 选区不全是路径文字：这组键照旧是 ⇧ 大步微调，离线不动
    useSelectionStore.getState().set(['r'])
    await key('ArrowUp')
    expect((objs()[1] as CanvasObject).y).toBeLessThan(0)
    expect(o().baselineOffsetPt).toBeCloseTo(4.1, 6)
  })

  it('属性栏：↑ 0.5 pt、⇧↑ 2 pt；旁边的小钮回到自动', async () => {
    await seed([obj({ baselineOffsetPt: 1 })])
    await mount(<PathTextSection objs={[o()]} />)
    const field = () => el.querySelector<HTMLInputElement>('input[data-inspector-prop="path-text-offset"]')!
    const rerender = async () => act(async () => root.render(<TooltipProvider><PathTextSection objs={[o()]} /></TooltipProvider>))
    await act(async () => {
      field().focus()
      field().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }))
    })
    expect(o().baselineOffsetPt).toBeCloseTo(1.5, 6)
    await rerender()
    await act(async () => {
      field().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', shiftKey: true, bubbles: true, cancelable: true }))
    })
    expect(o().baselineOffsetPt).toBeCloseTo(3.5, 6)
    await rerender()
    const reset = el.querySelector<HTMLButtonElement>('[data-path-text-offset-auto]')!
    expect(reset.disabled).toBe(false)
    await act(async () => reset.click())
    expect(o().baselineOffsetPt).toBeUndefined()
    await rerender()
    expect(el.querySelector<HTMLButtonElement>('[data-path-text-offset-auto]')!.disabled).toBe(true)
  })

  it('按住「离线」标签左右拖：整轮一条历史', async () => {
    await seed([obj({ baselineOffsetPt: 1 })])
    await mount(<PathTextSection objs={[o()]} />)
    const label = [...el.querySelectorAll<HTMLElement>('[data-number-scrub]')].find((x) => x.textContent === '离线' || x.textContent === 'Offset')!
    expect(label).toBeTruthy()
    label.setPointerCapture = () => {}
    label.hasPointerCapture = () => false
    await act(async () => label.dispatchEvent(new PointerEvent('pointerdown', { clientX: 100, bubbles: true, pointerId: 1 })))
    for (const x of [104, 108, 112]) await act(async () => window.dispatchEvent(new PointerEvent('pointermove', { clientX: x, bubbles: true })))
    await act(async () => window.dispatchEvent(new PointerEvent('pointerup', { clientX: 112, bubbles: true })))
    expect(o().baselineOffsetPt).toBeCloseTo(7, 6) // 1 + 12 px × 0.5
    expect(past()).toBe(1)
  })
})

describe('放不下的字', () => {
  const reply = (overflow: boolean) => {
    globalThis.fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (String(url).includes('/api/text/outline')) {
        calls.push(JSON.parse(String(init?.body)))
        return new Response(JSON.stringify({ ok: true, results: [{ paths: vectors.preview.paths, auto_offset_pt: 4.5, overflow }] }), { status: 200 })
      }
      return new Response('{}', { status: 200 })
    }) as typeof fetch
  }

  it('后端说放不下：选中或悬停时路径末端画红「+」，没选中也没悬停不画', async () => {
    reply(true)
    const o = obj({ text: 'Overflowing words' })
    await seed([o])
    await mount(<ObjectView obj={o} />)
    await settle()
    expect(el.querySelector('[data-path-text-overflow]')).toBeNull()
    await act(async () => useSelectionStore.getState().set(['pt']))
    const mark = el.querySelector('[data-path-text-overflow]')
    expect(mark).not.toBeNull()
    expect(mark!.querySelector('rect')).not.toBeNull()
    await act(async () => useSelectionStore.getState().clear())
    expect(el.querySelector('[data-path-text-overflow]')).toBeNull()
    await act(async () => useInteractionStore.getState().setHover('pt'))
    expect(el.querySelector('[data-path-text-overflow]')).not.toBeNull()
    await act(async () => useInteractionStore.getState().setHover(null))
  })

  it('放得下：选中也不画', async () => {
    reply(false)
    const o = obj({ text: 'Fits' })
    await seed([o])
    useSelectionStore.getState().set(['pt'])
    await mount(<ObjectView obj={o} />)
    await settle()
    expect(el.querySelector('[data-path-text-overflow]')).toBeNull()
  })
})

describe('编辑路径', () => {
  it('⌘ 双击进它自带那份路径的锚点编辑；拖两下锚点、退出编辑后只剩一步撤销，撤销回到原来的路径', async () => {
    const { activePathEdit } = await import('@/store/pathEditStore')
    const { startAnchorDrag } = await import('./pathEditDrag')
    const { usePathEditStore } = await import('@/store/pathEditStore')
    const o = obj({ text: 'Edit me' })
    await seed([o])
    await mount(<ObjectView obj={o} />)
    // 不按 ⌘：改字
    await act(async () => {
      el.querySelector('[data-object-id="pt"]')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    })
    expect(useUiStore.getState().editingTextId).toBe('pt')
    expect(activePathEdit()).toBeNull()
    await act(async () => useUiStore.getState().setEditingText(null))
    await act(async () => {
      el.querySelector('[data-object-id="pt"]')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, metaKey: true }))
    })
    expect(activePathEdit()?.path.id).toBe('pt')
    expect(useUiStore.getState().editingTextId).toBeNull()
    const before = structuredClone((objs()[0] as PathTextObject).subpaths)
    const drag = async (i: number, dx: number) => {
      const path = activePathEdit()!.path
      startAnchorDrag({ clientX: 0, clientY: 0, button: 0, stopPropagation() {}, preventDefault() {} } as unknown as React.PointerEvent, path, { sp: 0, i })
      await act(async () => window.dispatchEvent(new MouseEvent('pointermove', { clientX: dx, clientY: 0, bubbles: true })))
      await act(async () => window.dispatchEvent(new MouseEvent('pointermove', { clientX: dx * 2, clientY: 0, bubbles: true })))
      await act(async () => window.dispatchEvent(new MouseEvent('pointerup', { clientX: dx * 2, clientY: 0, bubbles: true })))
    }
    await drag(0, 10)
    await drag(1, -10)
    expect(past()).toBe(2)
    const after = (objs()[0] as PathTextObject)
    expect(after.type).toBe('pathText')
    expect(after.text).toBe('Edit me')
    expect(after.subpaths).not.toEqual(before)
    await act(async () => usePathEditStore.getState().exit())
    expect(past()).toBe(1)
    expect(useDocumentStore.getState().past.at(-1)!.label.key).toBe('history.editPathTextPath')
    await act(async () => void useDocumentStore.getState().undo())
    expect((objs()[0] as PathTextObject).subpaths).toEqual(before)
  })
})

describe('编辑路径（在组里）', () => {
  it('⌘ 双击组里的路径文字也直接进路径编辑，不先进组', async () => {
    const { activePathEdit } = await import('@/store/pathEditStore')
    const line = { id: 'L', type: 'path', groupId: 'G', x: 15, y: 12, w: 60, h: 20, subpaths: P.subpaths, strokePt: 1, color: '#000000', fill: null } as CanvasObject
    const o = obj({ text: 'Grouped', groupId: 'G', lineId: 'L' })
    await seed([line, o])
    await mount(<ObjectView obj={o} />)
    await act(async () => {
      el.querySelector('[data-object-id="pt"]')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, metaKey: true }))
    })
    expect(activePathEdit()?.path.id).toBe('pt')
  })
})


describe('选框与浮动栏让开字', () => {
  it('pathTextFrame：框并上字的范围；旋转时中心按对象的角度换到页面', async () => {
    const { pathTextFrame } = await import('@/store/pathTextGlyphStore')
    const o = obj({ x: 10, y: 20, w: 40, h: 10 })
    expect(pathTextFrame(o, undefined)).toBe(o)
    const f = pathTextFrame(o, { x: 5, y: -8, w: 20, h: 6 })
    expect([f.x, f.y, f.w, f.h]).toEqual([10, 12, 40, 18])
    const r = pathTextFrame({ ...o, rotationDeg: 90 }, { x: 5, y: -8, w: 20, h: 6 })
    // 局部并集中心 (20, 1) 比框中心 (20, 5) 高 4：转 90° 后落在框中心右边 4
    expect(r.x + r.w / 2).toBeCloseTo(30 + 4, 9)
    expect(r.y + r.h / 2).toBeCloseTo(25, 9)
    expect([r.w, r.h, r.rotationDeg]).toEqual([40, 18, 90])
  })

  it('画布画完字之后，选框（缩放手柄）把伸出路径框的字也包进去', async () => {
    const { OverlaySvg } = await import('./OverlaySvg')
    const { usePathTextGlyphStore } = await import('@/store/pathTextGlyphStore')
    const o = obj({ x: 15, y: 30 })
    await seed([o])
    useSelectionStore.getState().set(['pt'])
    await mount(<><ObjectView obj={o} /><OverlaySvg /></>)
    await settle()
    const g = usePathTextGlyphStore.getState().bounds.pt
    expect(g).toBeTruthy()
    const t = getTransform()
    const nw = el.querySelector<SVGRectElement>('[data-handle="nw"]')!
    const cx = Number(nw.getAttribute('x')) + Number(nw.getAttribute('width')) / 2
    const cy = Number(nw.getAttribute('y')) + Number(nw.getAttribute('height')) / 2
    expect(cx).toBeCloseTo(mmToViewX(o.x + Math.min(0, g.x), t), 3)
    expect(cy).toBeCloseTo(mmToViewY(o.y + Math.min(0, g.y), t), 3)
    // 预览里的字伸出了框（离线以后在线的上方），选框确实比路径的框大
    expect(Math.min(0, g.x) < 0 || Math.min(0, g.y) < 0).toBe(true)
  })
})
