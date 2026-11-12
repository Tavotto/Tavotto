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
import { approvedCjkBoldFor, previewStackOf } from './sourceFontPreview'
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

describe('行内粗斜与中文加粗（ADR 0169 修订）', () => {
  const REG = 'a1'.repeat(32)
  const BOLD = 'c3'.repeat(32)
  const ZH = 'b2'.repeat(32)
  const ZH_BOLD = 'd4'.repeat(32)
  const family = (sha: string) => cssFamilyOf({ sha256: sha, face: 0 })
  const fam: SourceFont = {
    family: 'Lab',
    sha256: REG,
    face: 0,
    siblings: [{ sha256: BOLD, face: 0, bold: true }],
    cjk: { family: 'Zh', sha256: ZH, face: 0, siblings: [{ sha256: ZH_BOLD, face: 0, bold: true }] },
  }
  const OWN = { bold: false, italic: false }
  const BOLD_RUN = { bold: true, italic: false }
  const load = async (run: typeof OWN) => {
    previewStackOf(fam, APPROVED, OWN, run)
    await settle()
    await settle()
    return previewStackOf(fam, APPROVED, OWN, run)
  }

  it('粗体片段用同族粗体脸和中文粗体脸，常规片段不取它们的字节', async () => {
    answer({ [REG]: 'ok', [BOLD]: 'ok', [ZH]: 'ok', [ZH_BOLD]: 'ok' })
    expect(await load(OWN)).toBe(`"${family(REG)}", "${family(ZH)}", ${APPROVED}`)
    expect(api.fetchSourceFontBytes).toHaveBeenCalledTimes(2)
    expect(await load(BOLD_RUN)).toBe(`"${family(BOLD)}", "${family(ZH_BOLD)}", ${APPROVED}`)
  })

  it('同族没有这张脸：留在常规脸上，由浏览器合成（导出同样模拟）', async () => {
    answer({ [REG]: 'ok', [ZH]: 'ok', [ZH_BOLD]: 'ok', [BOLD]: 'ok' })
    expect(await load({ bold: false, italic: true })).toBe(`"${family(REG)}", "${family(ZH)}", ${APPROVED}`)
  })

  it('不用粗体顶常规：常规脸用不了时正文回批准字体', async () => {
    answer({ [REG]: 'not_found', [BOLD]: 'ok', [ZH]: 'ok' })
    expect(await load(OWN)).toBe(`"${family(ZH)}", ${APPROVED}`)
  })

  it('粗体请求只问、只取粗体那张，不碰常规脸', async () => {
    answer({ [REG]: 'ok', [BOLD]: 'ok' })
    const src: SourceFont = { family: 'Lab', sha256: REG, face: 0, siblings: [{ sha256: BOLD, face: 0, bold: true }] }
    for (let i = 0; i < 3; i++) {
      previewStackOf(src, APPROVED, OWN, BOLD_RUN)
      await settle()
      await settle()
    }
    expect(previewStackOf(src, APPROVED, OWN, BOLD_RUN)).toBe(`"${family(BOLD)}", ${APPROVED}`)
    const asked = api.fetchSourceFontStatus.mock.calls.flatMap((c) => c[0] as { sha256: string }[])
    expect(asked.map((i) => i.sha256)).not.toContain(REG)
    expect(api.fetchSourceFontBytes).toHaveBeenCalledTimes(1)
  })

  it('粗体 sibling 确实用不了才退到常规脸（状态一拍一拍问到）', async () => {
    answer({ [REG]: 'ok', [BOLD]: 'not_found' })
    const src: SourceFont = { family: 'Lab', sha256: REG, face: 0, siblings: [{ sha256: BOLD, face: 0, bold: true }] }
    for (let i = 0; i < 4; i++) {
      previewStackOf(src, APPROVED, OWN, BOLD_RUN)
      await settle()
      await settle()
    }
    expect(previewStackOf(src, APPROVED, OWN, BOLD_RUN)).toBe(`"${family(REG)}", ${APPROVED}`)
  })

  it('中文粗体在本机用不了：退回常规中文', async () => {
    answer({ [REG]: 'ok', [BOLD]: 'ok', [ZH]: 'ok', [ZH_BOLD]: 'not_found' })
    expect(await load(BOLD_RUN)).toBe(`"${family(BOLD)}", "${family(ZH)}", ${APPROVED}`)
  })
})

describe('没选本机中文字体时的中文粗体', () => {
  const OWN = { bold: false, italic: false }
  const BOLD = { bold: true, italic: false }
  const load = async (run: typeof OWN, boldCjk = approvedCjkBoldFor('serif', '中文')) => {
    previewStackOf(undefined, APPROVED, OWN, run, boldCjk)
    await settle()
    await settle()
    return previewStackOf(undefined, APPROVED, OWN, run, boldCjk)
  }

  it('随族选 Noto：衬线用 Serif，其余用 Sans', () => {
    expect(approvedCjkBoldFor('serif', '中文')?.family).toBe('Noto Serif SC')
    expect(approvedCjkBoldFor('sans-serif', '中文')?.family).toBe('Noto Sans SC')
    expect(approvedCjkBoldFor('monospace', '中文')?.family).toBe('Noto Sans SC')
  })

  it('没有中文：不取字节', () => {
    expect(approvedCjkBoldFor('serif', 'Bold 123')).toBeUndefined()
  })

  it('粗体片段栈里有 Noto 粗体，常规片段不问它', async () => {
    const face = approvedCjkBoldFor('serif', '中文')!
    answer({ [face.sha256]: 'ok' })
    expect(await load(OWN)).toBe(APPROVED)
    expect(api.fetchSourceFontStatus).not.toHaveBeenCalled()
    expect(await load(BOLD)).toBe(`"${cssFamilyOf(face)}", ${APPROVED}`)
  })

  it('本机服务给不了：退回系统字体栈', async () => {
    answer({})
    expect(await load(BOLD)).toBe(APPROVED)
  })

  it('选了本机中文字体但它不可用：粗体栈用批准粗体（与导出 cjk_for 回退一致）', async () => {
    const face = approvedCjkBoldFor('serif', '中文')!
    answer({ [face.sha256]: 'ok', [latin.sha256]: 'ok', [cjk.sha256]: 'not_found' })
    // 第一拍问本机字体，知道它不可用后第二拍才问批准粗体（真实界面由状态通知触发重渲染）
    for (let i = 0; i < 2; i++) {
      previewStackOf(source, APPROVED, OWN, BOLD, face)
      await settle()
      await settle()
    }
    expect(previewStackOf(source, APPROVED, OWN, BOLD, face)).toContain(cssFamilyOf(face))
  })

  it('本机中文字体还在加载：不取批准粗体的字节', async () => {
    const face = approvedCjkBoldFor('serif', '中文')!
    let release = (_: ArrayBuffer) => {}
    api.fetchSourceFontBytes.mockImplementation(
      (sha: string) => (sha === cjk.sha256 ? new Promise((r) => (release = r)) : Promise.resolve(new ArrayBuffer(8))),
    )
    answer({ [face.sha256]: 'ok', [latin.sha256]: 'ok', [cjk.sha256]: 'ok' })
    previewStackOf(source, APPROVED, OWN, BOLD, face)
    await settle()
    await settle()
    previewStackOf(source, APPROVED, OWN, BOLD, face)
    await settle()
    expect(api.fetchSourceFontBytes).not.toHaveBeenCalledWith(face.sha256, 0)
    release(new ArrayBuffer(8))
  })

  it('选了本机中文字体时不用批准粗体', async () => {
    const face = approvedCjkBoldFor('serif', '中文')!
    answer({ [face.sha256]: 'ok', [latin.sha256]: 'ok', [cjk.sha256]: 'ok' })
    previewStackOf(source, APPROVED, OWN, BOLD, face)
    await settle()
    await settle()
    expect(previewStackOf(source, APPROVED, OWN, BOLD, face)).not.toContain(cssFamilyOf(face))
  })
})
