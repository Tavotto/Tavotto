/**
 * 预览字体栈（ADR 0164）：`[正文脸, CJK 脸, 批准族栈]`，与导出（`plan._user_faces`）同序、同判据；
 * 两张脸各自独立回退。用真的 `sourceFontCache`（只替换网络那一层）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  fetchSourceFontStatus: vi.fn(),
  fetchSourceFontBytes: vi.fn(),
}))
vi.mock('./api', async (orig) => ({ ...(await orig<typeof import('./api')>()), ...api }))

import { cssFamilyOf } from './sourceFont'
import { resetSourceFontCache } from './sourceFontCache'
import { previewStackOf } from './sourceFontPreview'
import type { SourceFont } from '@/types/document'

const latin = { family: 'DejaVu Sans', sha256: 'a1'.repeat(32), face: 0 }
const cjk = { family: 'SimHei', sha256: 'b2'.repeat(32), face: 0 }
const source: SourceFont = { ...latin, cjk }
const APPROVED = 'Liberation Serif, serif'
const settle = () => new Promise((r) => setTimeout(r, 0))

const answer = (states: Record<string, string>) =>
  api.fetchSourceFontStatus.mockImplementation(async (items: { sha256: string; face: number }[]) => ({
    ok: true,
    results: items.map((i) => ({ ...i, state: states[i.sha256] ?? 'not_found' })),
  }))

beforeEach(() => {
  resetSourceFontCache()
  api.fetchSourceFontStatus.mockReset()
  api.fetchSourceFontBytes.mockReset()
  api.fetchSourceFontBytes.mockResolvedValue(new ArrayBuffer(8))
  class FakeFontFace {
    async load() {
      return this
    }
  }
  vi.stubGlobal('FontFace', FakeFontFace)
  Object.defineProperty(document, 'fonts', { configurable: true, value: { add: () => {} } })
})
afterEach(() => vi.unstubAllGlobals())

const stackAfterLoad = async (src: SourceFont | undefined) => {
  previewStackOf(src, APPROVED) // 第一次：排请求
  await settle()
  await settle()
  return previewStackOf(src, APPROVED)
}

describe('previewStackOf', () => {
  it('CJK 脸与正文脸一起问、一起加载，排在正文脸之后、批准族之前', async () => {
    answer({ [latin.sha256]: 'ok', [cjk.sha256]: 'ok' })
    expect(await stackAfterLoad(source)).toBe(
      `"${cssFamilyOf(latin)}", "${cssFamilyOf(cjk)}", ${APPROVED}`,
    )
    // 同一拍一次请求，两张脸都在里面
    expect(api.fetchSourceFontStatus).toHaveBeenCalledTimes(1)
    expect(api.fetchSourceFontStatus.mock.calls[0][0]).toEqual([
      { sha256: latin.sha256, face: 0 },
      { sha256: cjk.sha256, face: 0 },
    ])
    expect(api.fetchSourceFontBytes).toHaveBeenCalledTimes(2)
  })

  it('CJK 脸不可用：栈里没有它，汉字走批准族（导出回批准的 Noto）；正文脸照用', async () => {
    answer({ [latin.sha256]: 'ok', [cjk.sha256]: 'embedding_restricted' })
    expect(await stackAfterLoad(source)).toBe(`"${cssFamilyOf(latin)}", ${APPROVED}`)
  })

  it('正文脸不可用而 CJK 脸可用：两张脸各自独立（与导出的 user_primary / user_cjk 一致）', async () => {
    answer({ [latin.sha256]: 'not_found', [cjk.sha256]: 'ok' })
    expect(await stackAfterLoad(source)).toBe(`"${cssFamilyOf(cjk)}", ${APPROVED}`)
  })

  it('两张都不可用 / 没有 sourceFont：就是批准族栈', async () => {
    answer({})
    expect(await stackAfterLoad(source)).toBe(APPROVED)
    expect(previewStackOf(undefined, APPROVED)).toBe(APPROVED)
  })

  it('CJK 脸字节没取到：状态 ok 也不进栈', async () => {
    answer({ [latin.sha256]: 'ok', [cjk.sha256]: 'ok' })
    api.fetchSourceFontBytes.mockImplementation(async (sha: string) => {
      if (sha === cjk.sha256) throw new Error('HTTP 500')
      return new ArrayBuffer(8)
    })
    expect(await stackAfterLoad(source)).toBe(`"${cssFamilyOf(latin)}", ${APPROVED}`)
  })

  it('CJK 脸与正文脸是同一张时不重复', async () => {
    answer({ [latin.sha256]: 'ok' })
    expect(await stackAfterLoad({ ...latin, cjk: latin })).toBe(`"${cssFamilyOf(latin)}", ${APPROVED}`)
  })
})
