import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  fetchSourceFontStatus: vi.fn(),
  fetchSourceFontBytes: vi.fn(),
}))
vi.mock('./api', async (orig) => ({ ...(await orig<typeof import('./api')>()), ...api }))

import { sourceFontInUse, sourceFontMissingOf } from './preflight'
import { cssFamilyOf } from './sourceFont'
import {
  previewFamilyOf,
  resetSourceFontCache,
  sourceFontState,
  subscribeSourceFonts,
} from './sourceFontCache'
import type { TextObject } from '@/types/document'

const SHA = '0f'.repeat(32)
const face = { family: 'DejaVu Sans', sha256: SHA, face: 0 }
const obj = (over: Partial<TextObject> = {}): TextObject => ({
  id: 't',
  type: 'text',
  text: 'x',
  sizePt: 9,
  bold: false,
  color: '#000',
  align: 'left',
  x: 0,
  y: 0,
  w: 10,
  h: 5,
  sourceFont: face,
  ...over,
})
const settle = () => new Promise((r) => setTimeout(r, 0))

describe('原图字体的本机状态缓存（ADR 0164）', () => {
  const added: unknown[] = []
  beforeEach(() => {
    resetSourceFontCache()
    added.length = 0
    api.fetchSourceFontStatus.mockReset()
    api.fetchSourceFontBytes.mockReset()
    class FakeFontFace {
      family: string
      buf: ArrayBuffer
      desc: object
      constructor(family: string, buf: ArrayBuffer, desc: object) {
        this.family = family
        this.buf = buf
        this.desc = desc
      }
      async load() {
        return this
      }
    }
    vi.stubGlobal('FontFace', FakeFontFace)
    Object.defineProperty(document, 'fonts', { configurable: true, value: { add: (f: unknown) => added.push(f) } })
  })
  afterEach(() => vi.unstubAllGlobals())

  it('先回 unknown，同一拍的请求合成一次；ok 之后才取字节并以身份派生的族名注册，声明这张脸自己的粗斜', async () => {
    api.fetchSourceFontStatus.mockResolvedValue({ ok: true, results: [{ sha256: SHA, face: 0, state: 'ok' }] })
    api.fetchSourceFontBytes.mockResolvedValue(new ArrayBuffer(8))
    const seen = vi.fn()
    subscribeSourceFonts(seen)

    expect(sourceFontState(face, { bold: true })).toBe('unknown')
    expect(sourceFontState(face)).toBe('unknown')
    await settle()
    await settle()

    expect(api.fetchSourceFontStatus).toHaveBeenCalledTimes(1)
    expect(api.fetchSourceFontStatus).toHaveBeenCalledWith([{ sha256: SHA, face: 0 }])
    expect(sourceFontState(face)).toBe('ok')
    expect(previewFamilyOf(face)).toBe(cssFamilyOf(face))
    expect(added).toHaveLength(1)
    expect((added[0] as { desc: object }).desc).toEqual({ weight: '700', style: 'normal' })
    expect(seen).toHaveBeenCalled()
  })

  it('不能用的原因原样记下，不去取字节', async () => {
    api.fetchSourceFontStatus.mockResolvedValue({
      ok: true,
      results: [{ sha256: SHA, face: 0, state: 'embedding_restricted' }],
    })
    sourceFontState(face)
    await settle()
    expect(sourceFontState(face)).toBe('embedding_restricted')
    expect(api.fetchSourceFontBytes).not.toHaveBeenCalled()
    expect(previewFamilyOf(face)).toBeUndefined()
  })

  it('没有本机服务（playground / 旧后端）→ unsupported，不重复轰炸', async () => {
    api.fetchSourceFontStatus.mockRejectedValue(new Error('HTTP 404'))
    sourceFontState(face)
    await settle()
    expect(sourceFontState(face)).toBe('unsupported')
    sourceFontState(face)
    await settle()
    expect(api.fetchSourceFontStatus).toHaveBeenCalledTimes(1)
  })

  it('字节取不到：状态仍是 ok、没有预览族（画面退回批准族，导出不受影响）', async () => {
    api.fetchSourceFontStatus.mockResolvedValue({ ok: true, results: [{ sha256: SHA, face: 0, state: 'ok' }] })
    api.fetchSourceFontBytes.mockRejectedValue(new Error('HTTP 500'))
    sourceFontState(face)
    await settle()
    await settle()
    expect(sourceFontState(face)).toBe('ok')
    expect(previewFamilyOf(face)).toBeUndefined()
  })

  it('预检的事实：ok / 还没问到都不算缺；用不了才算，且保留名字', async () => {
    expect(sourceFontMissingOf(obj())).toBeUndefined() // unknown
    api.fetchSourceFontStatus.mockResolvedValue({ ok: true, results: [{ sha256: SHA, face: 0, state: 'not_found' }] })
    await settle()
    await settle()
    expect(sourceFontMissingOf(obj())).toEqual({ family: 'DejaVu Sans', reason: 'not_found' })
    expect(sourceFontInUse(obj())).toBeUndefined()
    expect(sourceFontMissingOf(obj({ hidden: true }))).toBeUndefined()
    expect(sourceFontMissingOf(obj({ sourceFont: undefined }))).toBeUndefined()
  })
})
