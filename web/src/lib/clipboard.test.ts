import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CLIPBOARD_FORMAT } from './brand'

/**
 * 粘贴读剪贴板失败的三种口径（审计 docs/audit/2026-08-17-ux-audit.md）：
 *
 * - 浏览器压根不给 readText（Firefox 默认如此）→ 说清楚，但每会话只说一次；
 * - 真的被拒（抛 NotAllowedError 且窗口有焦点）→ 与复制失败对称地报错；
 * - 其余（失焦、瞬态异常）→ 继续静默，那种粘贴多半根本不是冲我们来的。
 *
 * 「只提示一次」是模块级布尔，所以每个用例都 resetModules 后重新 import 拿
 * 一份干净的模块图；uiStore/documentStore 必须跟着同一次 import 拿，否则断言
 * 的是另一份 store 实例。
 */

/** 自动保存会 PUT 到后端；这里只要不抛就行 */
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const realClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')

const setClipboard = (value: unknown) =>
  Object.defineProperty(navigator, 'clipboard', { value, configurable: true })

const notAllowed = () => new DOMException('Document is not focused.', 'NotAllowedError')

const payloadText = () =>
  JSON.stringify({
    magic: CLIPBOARD_FORMAT,
    sourceDocId: 'd_elsewhere',
    objects: [
      { id: 'o1', type: 'text', x: 10, y: 10, w: 20, h: 8, text: 'hi',
        sizePt: 9, bold: false, color: '#000', align: 'left' },
    ],
    layoutGroups: [],
  })

/** 只为收尾清计时器：显式标类型，别让它参与 load() 的返回类型推导 */
let openedUi: (typeof import('@/store/uiStore'))['useUiStore'] | null = null

/** 干净的模块图 + 一张空画布 */
async function load() {
  vi.resetModules()
  const [clipboard, { useUiStore }, { useDocumentStore }, { useSelectionStore }, types] =
    await Promise.all([
      import('@/lib/clipboard'),
      import('@/store/uiStore'),
      import('@/store/documentStore'),
      import('@/store/selectionStore'),
      import('@/types/document'),
    ])
  useDocumentStore.setState({
    doc: types.canvasToDoc(types.emptyProject().canvases[0]),
    past: [],
    future: [],
    txn: null,
  })
  useSelectionStore.getState().set([])
  useUiStore.getState().setStatus('')
  openedUi = useUiStore
  return { clipboard, useUiStore, useDocumentStore, useSelectionStore }
}

type Loaded = Awaited<ReturnType<typeof load>>

const status = (l: Loaded) => l.useUiStore.getState().status

beforeEach(() => {
  vi.spyOn(document, 'hasFocus').mockReturnValue(true)
})

afterEach(() => {
  openedUi?.getState().setStatus('') // 顺手清掉 4.5s 的状态计时器
  openedUi = null
  vi.restoreAllMocks()
})

afterAll(() => {
  if (realClipboard) Object.defineProperty(navigator, 'clipboard', realClipboard)
  else Reflect.deleteProperty(navigator as object, 'clipboard')
})

describe('pasteObjects 读剪贴板失败', () => {
  it('浏览器不提供 readText：报一次「不支持」，之后不再重复轰炸', async () => {
    setClipboard({ writeText: vi.fn() }) // Firefox 默认形态：有 clipboard，没 readText
    const l = await load()

    expect(await l.clipboard.pasteObjects()).toBe(false)
    expect(status(l)).toContain('不支持')
    expect(l.useUiStore.getState().statusTone).toBe('error')

    // 同一会话里再按几次粘贴键：不再刷提示
    l.useUiStore.getState().setStatus('')
    expect(await l.clipboard.pasteObjects()).toBe(false)
    expect(await l.clipboard.pasteObjects()).toBe(false)
    expect(status(l)).toBe('')
  })

  it('navigator.clipboard 整个不存在也走同一条「不支持」分支', async () => {
    setClipboard(undefined)
    const l = await load()

    expect(await l.clipboard.pasteObjects()).toBe(false)
    expect(status(l)).toContain('不支持')
  })

  it('NotAllowedError + 窗口有焦点 = 真被拒：与复制失败对称地报错', async () => {
    setClipboard({ readText: vi.fn().mockRejectedValue(notAllowed()) })
    const l = await load()

    expect(await l.clipboard.pasteObjects()).toBe(false)
    expect(status(l)).toBe('无法读取剪贴板（浏览器权限被拒）')
    expect(l.useUiStore.getState().statusTone).toBe('error')
  })

  it('NotAllowedError + 窗口失焦：保持静默（那次粘贴多半不是给我们的）', async () => {
    vi.spyOn(document, 'hasFocus').mockReturnValue(false)
    setClipboard({ readText: vi.fn().mockRejectedValue(notAllowed()) })
    const l = await load()

    expect(await l.clipboard.pasteObjects()).toBe(false)
    expect(status(l)).toBe('')
  })

  it('其它异常（瞬态失败）同样静默', async () => {
    setClipboard({ readText: vi.fn().mockRejectedValue(new Error('boom')) })
    const l = await load()

    expect(await l.clipboard.pasteObjects()).toBe(false)
    expect(status(l)).toBe('')
  })
})

describe('pasteObjects 正常路径不受影响', () => {
  it('读到本工具的负载：照常落进文档并选中', async () => {
    setClipboard({ readText: vi.fn().mockResolvedValue(payloadText()) })
    const l = await load()

    expect(await l.clipboard.pasteObjects()).toBe(true)
    const objects = l.useDocumentStore.getState().doc.objects
    expect(objects).toHaveLength(1)
    expect(objects[0].type === 'text' && objects[0].text).toBe('hi')
    expect(l.useSelectionStore.getState().ids).toEqual([objects[0].id])
    expect(status(l)).toContain('已粘贴')
  })

  it('读到普通文本：不消费这次粘贴，也不弹任何提示', async () => {
    setClipboard({ readText: vi.fn().mockResolvedValue('随便一段文字') })
    const l = await load()

    expect(await l.clipboard.pasteObjects()).toBe(false)
    expect(status(l)).toBe('')
    expect(l.useDocumentStore.getState().doc.objects).toHaveLength(0)
  })
})
