import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/source_font_vectors.json'
import type { ManifestFaceSource } from './api'
import {
  SOURCE_FONT_REASONS,
  cssFamilyOf,
  faceKey,
  isSourceFont,
  sameFace,
  sourceFacesOf,
  sourceFontFromElement,
} from './sourceFont'
import type { SourceFont } from '@/types/document'

const SHA = '0f'.repeat(32)
const src = (over: Partial<ManifestFaceSource> = {}): ManifestFaceSource => ({
  family: 'DejaVu Sans',
  sha256: SHA,
  face: 0,
  bold: false,
  italic: false,
  ...over,
})

describe('sourceFont：形状向量与后端 parse_source_font 同源', () => {
  it.each(vectors.cases)('$name', (c) => {
    expect(isSourceFont(c.value)).toBe(c.valid)
  })
})

describe('sourceFontFromElement（复制为标注取值的唯一入口）', () => {
  it('manifest 的文件级身份 → sourceFont；粗斜只在为真时写，没有路径', () => {
    const sf = sourceFontFromElement({ face_src: src({ bold: true }) })
    expect(sf).toEqual({ family: 'DejaVu Sans', sha256: SHA, face: 0, bold: true })
    expect('path' in (sf as object)).toBe(false)
    expect(isSourceFont(sf)).toBe(true)
  })

  it('带中日韩脸时写 cjk；缺席就不写（汉字由批准的 Noto 画）', () => {
    const sf = sourceFontFromElement({
      face_src: src(),
      cjk_src: src({ family: 'SimSun', face: 2, sha256: 'ab'.repeat(32) }),
    })
    expect(sf?.cjk).toEqual({ family: 'SimSun', sha256: 'ab'.repeat(32), face: 2 })
    expect(sourceFontFromElement({ face_src: src() })?.cjk).toBeUndefined()
  })

  it('没有 face_src（解析不出脸 / 老引擎）→ undefined，不编造身份', () => {
    expect(sourceFontFromElement({})).toBeUndefined()
    expect(sourceFontFromElement({ face_src: src({ sha256: 'nope' }) })).toBeUndefined()
  })
})

describe('身份工具', () => {
  it('只按 sha256 + 面序号认脸，名字不参与', () => {
    expect(sameFace({ family: 'A', sha256: SHA, face: 0 }, { family: 'B', sha256: SHA, face: 0 })).toBe(true)
    expect(sameFace({ family: 'A', sha256: SHA, face: 0 }, { family: 'A', sha256: SHA, face: 1 })).toBe(false)
    expect(faceKey({ sha256: SHA, face: 3 })).toBe(`${SHA}:3`)
  })

  it('预览族名由身份派生，不会撞真实字体名', () => {
    expect(cssFamilyOf({ sha256: SHA, face: 1 })).toBe(`tvsrc-${SHA.slice(0, 16)}-1`)
  })

  it('一批对象里要问本机状态的脸去重，形状不对的不问', () => {
    const a: SourceFont = { family: 'A', sha256: SHA, face: 0, cjk: { family: 'C', sha256: 'ab'.repeat(32), face: 0 } }
    const faces = sourceFacesOf([{ sourceFont: a }, { sourceFont: a }, { sourceFont: undefined }, { sourceFont: { family: '' } as never }])
    expect(faces.map(faceKey)).toEqual([`${SHA}:0`, `${'ab'.repeat(32)}:0`])
  })

  it('原因闭集与后端 REASONS 逐项相同', () => {
    expect([...SOURCE_FONT_REASONS]).toEqual([
      'not_found',
      'embedding_restricted',
      'too_large',
      'unsupported_format',
      'unreadable',
      'invalid',
    ])
  })
})
