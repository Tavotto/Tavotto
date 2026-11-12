/**
 * ⌘V 粘贴图片（截图、从文件管理器复制的图片文件）：不是本工具的对象负载、也没有文字时，走与拖入同一条导入路径（ADR 0176）。
 * 判据的主语：这次粘贴**谁接管**（`handlePasteEvent` 的返回值与 `preventDefault`）、有没有发上传、放没放上画布；
 * 输入框里 / 有文字 / 没有图片时一概不拦，外部纯文字粘贴保持原样。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { formatMessage } from '@/i18n'
import type { ImportedAsset, PanelInfo } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { canvasToDoc, emptyProject } from '@/types/document'

const mocks = vi.hoisted(() => ({ importAsset: vi.fn() }))
vi.mock('@/lib/api', async (orig) => ({ ...(await orig<typeof import('@/lib/api')>()), importAsset: mocks.importAsset }))

import { handlePasteEvent } from './clipboard'

const png = (name = 'image.png', type = 'image/png') => new File([new Uint8Array([1])], name, { type })
const asset = (name: string): ImportedAsset => ({ id: name, name: name.replace(/\.\w+$/, ''), file: name, kind: 'raster', reused: false, renamed: false, bytes: 1 })
const info = (id: string) => ({ id, name: id.replace(/\.\w+$/, ''), folder: '.', mtime: 1, kind: 'raster', native_w_mm: 40, native_h_mm: 30 }) as PanelInfo

function pasteEvent(opts: { files?: File[]; text?: string; target?: EventTarget }) {
  const preventDefault = vi.fn()
  const e = {
    target: opts.target ?? document.body,
    clipboardData: { getData: (t: string) => (t === 'text/plain' ? (opts.text ?? '') : ''), files: opts.files ?? [], items: [] },
    preventDefault,
  } as unknown as ClipboardEvent
  return { e, preventDefault }
}

const settle = () => new Promise((r) => setTimeout(r, 0))

beforeEach(() => {
  vi.clearAllMocks()
  setCurrentProjectId('pj_a')
  const base = canvasToDoc(emptyProject().canvases[0])
  useDocumentStore.setState({ doc: { ...base, objects: [] }, past: [], future: [], txn: null })
  useUiStore.getState().setStatus(null)
  useAssetStore.setState({ panels: [], byId: {}, unsupported: [], loaded: true } as never)
  mocks.importAsset.mockImplementation(async (_f: Blob, name: string) => asset(name))
  vi.spyOn(useAssetStore.getState(), 'load').mockImplementation(async () => {
    const got = await Promise.all(mocks.importAsset.mock.results.map((r) => r.value as Promise<ImportedAsset>))
    const panels = got.map((a) => info(a.id))
    useAssetStore.setState({ panels, byId: Object.fromEntries(panels.map((p) => [p.id, p])) } as never)
    return { panels } as never
  })
})

describe('粘贴图片', () => {
  it('截图：接管事件，上传（自动起名 pasted-时间戳.png）并放进画布', async () => {
    const { e, preventDefault } = pasteEvent({ files: [png()] })
    expect(handlePasteEvent(e)).toBe(true)
    expect(preventDefault).toHaveBeenCalled()
    await settle()
    await settle()
    expect(mocks.importAsset).toHaveBeenCalledOnce()
    expect(mocks.importAsset.mock.calls[0][1]).toMatch(/^pasted-\d{8}-\d{6}\.png$/)
    expect(useDocumentStore.getState().doc.objects).toHaveLength(1)
    expect(formatMessage(useUiStore.getState().status)).toContain('pasted-')
  })

  it('目标在输入框里：让位，不上传', async () => {
    const { e, preventDefault } = pasteEvent({ files: [png()], target: document.createElement('input') })
    expect(handlePasteEvent(e)).toBe(false)
    expect(preventDefault).not.toHaveBeenCalled()
    await settle()
    expect(mocks.importAsset).not.toHaveBeenCalled()
  })

  it('同时有文字（电子表格 / 网页的复制常带预览图）：是在粘文字，不拦', async () => {
    const { e, preventDefault } = pasteEvent({ files: [png()], text: 'a\tb\n1\t2' })
    expect(handlePasteEvent(e)).toBe(false)
    expect(preventDefault).not.toHaveBeenCalled()
    expect(mocks.importAsset).not.toHaveBeenCalled()
  })

  it('没有图片（外部纯文字、别的文件）：保持原样，不拦不说', async () => {
    for (const opts of [{ text: '随便一段文字' }, { files: [new File([new Uint8Array([1])], 'a.csv')] }, {}]) {
      const { e, preventDefault } = pasteEvent(opts)
      expect(handlePasteEvent(e)).toBe(false)
      expect(preventDefault).not.toHaveBeenCalled()
    }
    expect(useUiStore.getState().status).toBeNull()
    expect(mocks.importAsset).not.toHaveBeenCalled()
  })

  it('从文件管理器复制的图片文件：沿用它自己的文件名', async () => {
    const { e } = pasteEvent({ files: [png('Fig 3.png')] })
    expect(handlePasteEvent(e)).toBe(true)
    await settle()
    expect(mocks.importAsset.mock.calls[0][1]).toBe('Fig 3.png')
  })

  it('上传失败：一句话 + 发送问题反馈，不静默', async () => {
    mocks.importAsset.mockRejectedValue(new Error('offline'))
    const { e } = pasteEvent({ files: [png()] })
    expect(handlePasteEvent(e)).toBe(true)
    await settle()
    await settle()
    expect(useUiStore.getState().statusTone).toBe('error')
    expect(useDocumentStore.getState().doc.objects).toHaveLength(0)
  })
})
