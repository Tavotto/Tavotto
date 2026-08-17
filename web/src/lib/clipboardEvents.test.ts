import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CLIPBOARD_FORMAT } from './brand'
import { handleCopyEvent, handlePasteEvent } from './clipboard'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { canvasToDoc, emptyProject, type TextObject } from '@/types/document'

/**
 * ⌘C/⌘V 的主路径是原生 ClipboardEvent（e.clipboardData 同步读写）：
 * WebKit（Safari / 桌面壳）不给非编辑区的异步 readText/writeText，
 * 「跨标签页粘贴」只有这条路在所有浏览器都通。这里看护事件层的让位规则。
 */

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const text = (id: string): TextObject => ({
  id, type: 'text', text: 'hi', sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 10, y: 10, w: 20, h: 8,
})

const payloadText = () =>
  JSON.stringify({
    magic: CLIPBOARD_FORMAT,
    sourceDocId: 'd_elsewhere',
    objects: [text('o1')],
    layoutGroups: [],
  })

function fakeEvent(opts: {
  target?: EventTarget
  getData?: (t: string) => string
  hasClipboardData?: boolean
}) {
  const setData = vi.fn()
  const preventDefault = vi.fn()
  const e = {
    target: opts.target ?? document.body,
    clipboardData:
      opts.hasClipboardData === false
        ? null
        : { getData: opts.getData ?? (() => ''), setData },
    preventDefault,
  } as unknown as ClipboardEvent
  return { e, setData, preventDefault }
}

beforeEach(() => {
  const base = canvasToDoc(emptyProject().canvases[0])
  useDocumentStore.setState({
    doc: { ...base, objects: [text('t1')] },
    past: [],
    future: [],
    txn: null,
  })
  useSelectionStore.getState().set([])
  useUiStore.getState().setStatus('')
})

afterEach(() => {
  useUiStore.getState().setStatus('')
  vi.restoreAllMocks()
})

describe('handleCopyEvent', () => {
  it('有选中对象：接管事件，负载同步写进 clipboardData', () => {
    useSelectionStore.getState().set(['t1'])
    const { e, setData, preventDefault } = fakeEvent({})
    expect(handleCopyEvent(e)).toBe(true)
    expect(preventDefault).toHaveBeenCalled()
    const [mime, data] = setData.mock.calls[0]
    expect(mime).toBe('text/plain')
    expect(JSON.parse(data).magic).toBe(CLIPBOARD_FORMAT)
    expect(useUiStore.getState().status).toContain('已复制')
  })

  it('无选中对象：不接管（普通文本复制照旧）', () => {
    const { e, preventDefault } = fakeEvent({})
    expect(handleCopyEvent(e)).toBe(false)
    expect(preventDefault).not.toHaveBeenCalled()
  })

  it('目标在输入框里：让位给原生复制', () => {
    useSelectionStore.getState().set(['t1'])
    const input = document.createElement('input')
    const { e, preventDefault } = fakeEvent({ target: input })
    expect(handleCopyEvent(e)).toBe(false)
    expect(preventDefault).not.toHaveBeenCalled()
  })
})

describe('handlePasteEvent', () => {
  it('本工具负载：消费事件并落进文档', () => {
    const { e, preventDefault } = fakeEvent({ getData: payloadText })
    expect(handlePasteEvent(e)).toBe(true)
    expect(preventDefault).toHaveBeenCalled()
    const objects = useDocumentStore.getState().doc.objects
    expect(objects).toHaveLength(2)
    expect(useUiStore.getState().status).toContain('已粘贴')
  })

  it('普通文本：不消费、不提示', () => {
    const { e, preventDefault } = fakeEvent({ getData: () => '随便一段文字' })
    expect(handlePasteEvent(e)).toBe(false)
    expect(preventDefault).not.toHaveBeenCalled()
    expect(useDocumentStore.getState().doc.objects).toHaveLength(1)
  })

  it('目标在输入框里：让位给原生粘贴', () => {
    const input = document.createElement('input')
    const { e, preventDefault } = fakeEvent({ target: input, getData: payloadText })
    expect(handlePasteEvent(e)).toBe(false)
    expect(preventDefault).not.toHaveBeenCalled()
  })
})
