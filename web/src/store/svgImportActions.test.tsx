/**
 * SVG 导入的两个入口（ADR 0189）：粘贴 SVG 标记 → 可编辑对象（一条历史、选中）；做不准 / 读不了 → 整个当图片放进来并说原因；
 * 「拆成可编辑对象」→ 按图在画布上的大小缩放、原位替换、接着待在图原来的组里；做不准 → 图不动。粘贴事件认出 SVG 标记。
 * 后端用快照代答（`tests/golden/svgimport`，真的后端在 `tests/test_svgimport.py` 里测）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import illustrator from '../../../tests/golden/svgimport/illustrator.json'
import { literal } from '@/i18n'
import { groupPathOf } from '@/lib/groupTree'
import { emptyProject, type CanvasObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'
import { canExplodeSvg, explodeSvgPanel, pasteSvgText } from './svgImportActions'
import { useUiStore } from './uiStore'

const intake = vi.hoisted(() => vi.fn(async () => ({ placed: ['pasted.svg'], problems: [], aborted: false })))
const report = vi.hoisted(() => vi.fn())
const raster = vi.hoisted(() => vi.fn(async (): Promise<Blob | null> => null))
vi.mock('@/lib/svgRaster', async (orig) => ({ ...(await orig<typeof import('@/lib/svgRaster')>()), rasterizeSvg: raster }))
vi.mock('@/lib/canvasFileIntake', async (orig) => ({
  ...(await orig<typeof import('@/lib/canvasFileIntake')>()),
  importAndPlace: intake,
  reportImageResult: report,
}))

const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="1" height="1"/></svg>'
let reply: unknown = { ok: true, ...illustrator }
let fileText = SVG
const calls: string[] = []
beforeEach(() => {
  calls.length = 0
  intake.mockClear()
  report.mockClear()
  raster.mockClear()
  reply = { ok: true, ...illustrator }
  fileText = SVG
  globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
    const u = String(url)
    calls.push(u)
    if (u.includes('/api/svg/import')) return new Response(JSON.stringify(reply), { status: 200 })
    if (u.includes('/api/file')) return new Response(fileText, { status: 200 })
    return new Response('{}', { status: 200 })
  }) as typeof fetch
})
afterEach(() => {
  useUiStore.setState({ status: null })
})

const objs = () => useDocumentStore.getState().doc.objects
const past = () => useDocumentStore.getState().past.length
async function seed(items: CanvasObject[] = []) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_svg_' + Math.random())
  if (items.length) {
    useDocumentStore.getState().commit(literal('放'), (d) => {
      d.objects.push(...items)
    })
  }
  useDocumentStore.setState({ past: [], future: [] })
}

describe('粘贴 SVG 标记', () => {
  it('落成可编辑对象：一条历史、全部选中、剪切组带过来', async () => {
    await seed()
    expect(await pasteSvgText(SVG)).toBe('objects')
    expect(objs()).toHaveLength(illustrator.objects.length)
    expect(past()).toBe(1)
    expect(useSelectionStore.getState().ids).toHaveLength(objs().length)
    expect(objs().some((o) => o.clipMask)).toBe(true)
    expect(intake).not.toHaveBeenCalled()
  })

  it('有做不准的东西：整个当图片放进来，说原因，文档里不多出对象', async () => {
    await seed()
    // 后端给了对象但同时报了做不准：仍然整个当图片，不出半张图
    reply = { ok: true, ...illustrator, unsupported: { filter: 1 } }
    expect(await pasteSvgText(SVG)).toBe('image')
    expect(intake).toHaveBeenCalledTimes(1)
    const file = (intake.mock.calls[0] as unknown as [File[]])[0][0]
    expect(file.type).toBe('image/svg+xml')
    expect(objs()).toHaveLength(0)
    expect(useUiStore.getState().status).toBeTruthy()
    expect(report).not.toHaveBeenCalled()
  })

  it('SVG 素材检查不收：栅格化成 PNG 再放，说「当位图放进来」', async () => {
    await seed()
    reply = { ok: true, objects: [], size_mm: [20, 10], unsupported: { filter: 1 } }
    intake.mockResolvedValueOnce({ placed: [], problems: [{ name: 'pasted.svg', reason: literal('不收') }], aborted: false } as never)
    raster.mockResolvedValueOnce(new Blob(['png'], { type: 'image/png' }))
    expect(await pasteSvgText(SVG)).toBe('image')
    expect(raster).toHaveBeenCalledWith(SVG, [20, 10])
    expect(intake).toHaveBeenCalledTimes(2)
    const second = (intake.mock.calls[1] as unknown as [File[]])[0][0]
    expect(second.type).toBe('image/png')
    expect(report).not.toHaveBeenCalled()
    expect(useUiStore.getState().status).toBeTruthy()
  })

  it.each(['too_large', 'timeout', 'reference_cycle'])('被当成资源滥用拒掉的（%s）：不交给浏览器栅格化', async (code) => {
    await seed()
    reply = { ok: false, error: code }
    intake.mockResolvedValueOnce({ placed: [], problems: [{ name: 'pasted.svg', reason: literal('不收') }], aborted: false } as never)
    raster.mockResolvedValue(new Blob(['png'], { type: 'image/png' }))
    expect(await pasteSvgText(SVG)).toBe('image')
    expect(raster).not.toHaveBeenCalled()
    expect(report).toHaveBeenCalledTimes(1)
    raster.mockReset()
    raster.mockResolvedValue(null)
  })

  it('脚本 / 事件属性（script）：同样不栅格化', async () => {
    await seed()
    reply = { ok: true, objects: [], size_mm: [1, 1], unsupported: { script: 1 } }
    intake.mockResolvedValueOnce({ placed: [], problems: [{ name: 'pasted.svg', reason: literal('不收') }], aborted: false } as never)
    expect(await pasteSvgText(SVG)).toBe('image')
    expect(raster).not.toHaveBeenCalled()
  })

  it('当图片也没放成（素材检查不收、也栅格化不了）：由那条路说它的原因，不说「已当图片放进来」', async () => {
    await seed()
    reply = { ok: true, objects: [], size_mm: [1, 1], unsupported: { filter: 1 } }
    intake.mockResolvedValueOnce({ placed: [], problems: [{ name: 'pasted.svg', reason: literal('不行') }], aborted: false } as never)
    useUiStore.setState({ status: null })
    expect(await pasteSvgText(SVG)).toBe('image')
    expect(report).toHaveBeenCalledTimes(1)
    expect(useUiStore.getState().status).toBeNull()
  })

  it('后端读不了（坏 XML）：同样当图片放', async () => {
    await seed()
    reply = { ok: false, error: 'bad_xml' }
    expect(await pasteSvgText(SVG)).toBe('image')
    expect(intake).toHaveBeenCalledTimes(1)
  })
})

const svgPanel = (extra: Partial<CanvasObject> = {}): CanvasObject =>
  ({
    id: 'p', type: 'panel', fileId: 'art.svg', fileKind: 'svg', nativeW: 52.9, nativeH: 26.5, overrides: [],
    x: 100, y: 50, w: illustrator.size_mm[0] * 2, h: illustrator.size_mm[1] * 2, ...extra,
  }) as CanvasObject

describe('拆成可编辑对象', () => {
  it('静态 SVG 图：按画布上的大小缩放、原位替换、接着待在原来的组里；一条历史', async () => {
    await seed([{ id: 'a', type: 'shape', shape: 'rect', x: 0, y: 0, w: 1, h: 1, strokePt: 1, color: '#000', fill: null, groupId: 'G' } as CanvasObject, svgPanel({ groupId: 'G' })])
    expect(canExplodeSvg(objs()[1])).toBe(true)
    expect(await explodeSvgPanel('p')).toBe(true)
    expect(objs().find((o) => o.id === 'p')).toBeUndefined()
    const made = objs().slice(1)
    expect(made).toHaveLength(illustrator.objects.length)
    expect(made.every((o) => groupPathOf(o)[0] === 'G')).toBe(true)
    // 第一个矩形：快照里在 (x, y)，画布上图放大两倍、左上角在 (100, 50)
    const src = illustrator.objects[0] as { x: number; y: number; w: number; strokePt: number }
    expect(made[0].x).toBeCloseTo(100 + src.x * 2, 4)
    expect(made[0].y).toBeCloseTo(50 + src.y * 2, 4)
    expect(made[0].w).toBeCloseTo(src.w * 2, 4)
    expect((made[0] as { strokePt: number }).strokePt).toBeCloseTo(src.strokePt * 2, 4)
    expect(past()).toBe(1)
  })

  it('做不准：图不动、不进历史、说原因', async () => {
    await seed([svgPanel()])
    reply = { ok: true, objects: [], size_mm: [1, 1], unsupported: { image: 2 } }
    expect(await explodeSvgPanel('p')).toBe(false)
    expect(objs().map((o) => o.id)).toEqual(['p'])
    expect(past()).toBe(0)
    expect(useUiStore.getState().status).toBeTruthy()
  })

  it('有脚本的图、裁剪过的、旋转过的、锁定的：不能拆', () => {
    expect(canExplodeSvg(svgPanel({ script: 'fig.py' } as Partial<CanvasObject>))).toBe(false)
    expect(canExplodeSvg(svgPanel({ crop: { x: 0, y: 0, w: 0.5, h: 1 } } as Partial<CanvasObject>))).toBe(false)
    expect(canExplodeSvg(svgPanel({ rotation: 90 } as Partial<CanvasObject>))).toBe(false)
    expect(canExplodeSvg(svgPanel({ locked: true }))).toBe(false)
    expect(canExplodeSvg(svgPanel({ fileKind: 'pdf' } as Partial<CanvasObject>))).toBe(false)
  })
})

describe('粘贴事件', () => {
  it('剪贴板里是 SVG 标记：认出来、拦下、走导入', async () => {
    await seed()
    const { handlePasteEvent } = await import('@/lib/clipboard')
    // jsdom 没有 DataTransfer：给一个只会 getData 的替身
    const data = { getData: (t: string) => (t === 'text/plain' ? SVG : ''), files: [], items: [] }
    const ev = new Event('paste', { cancelable: true }) as ClipboardEvent
    Object.defineProperty(ev, 'clipboardData', { value: data })
    Object.defineProperty(ev, 'target', { value: document.body })
    expect(handlePasteEvent(ev)).toBe(true)
    expect(ev.defaultPrevented).toBe(true)
    await vi.waitFor(() => expect(calls.some((u) => u.includes('/api/svg/import'))).toBe(true))
  })
})
