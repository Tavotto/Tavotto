import { describe, expect, it } from 'vitest'
import {
  CJK_START,
  COVERAGE_BACKEND,
  layerOf,
  missingChars,
  planRuns,
  substitutedChars,
  textDiagnostics,
} from './glyphPlan'

describe('分层顺序', () => {
  it('四步不可交换：`₂` 在中日韩脸里有，码位却在 CJK 段之外', () => {
    // 这一条钉的是覆盖表的裁剪条件。多减一个 `cjk` 会让这里判成 'cjk'、
    // 后端仍判 'fallback'——一个只在下标字符上发作的两侧分歧。
    expect('₂'.codePointAt(0)).toBeLessThan(CJK_START)
    expect(layerOf(0x2082)).toBe('fallback')
  })

  it('第 4 步把拉丁段里只有中日韩脸画得出的字符救回来', () => {
    expect(layerOf(0x2501)).toBe('cjk') // ━
  })

  it('谁都画不出的字符是 missing，不是安静地当成画得出', () => {
    expect(layerOf(0x061f)).toBe('missing') // ؟
    expect(missingChars('T؟ = 5')).toEqual(['؟'])
  })

  it('相邻同层合并成一段', () => {
    expect(planRuns('AB')).toEqual([{ text: 'AB', layer: 'primary' }])
  })

  it('空串回空表', () => {
    expect(planRuns('')).toEqual([])
  })
})

describe('按码位遍历，不按 UTF-16 码元', () => {
  it('代理对算一个字符', () => {
    // 按码元遍历的话 😀 会被拆成两个「画不出来的字」，用户看到的是
    // 「有 2 个字符画不出来」而屏幕上只有一个表情。
    expect(planRuns('😀')).toEqual([{ text: '😀', layer: 'fallback' }])
    expect(planRuns('😀')[0].text.length).toBe(2)
  })
})

describe('missing 与 substituted 是两句话', () => {
  it('画得出但换了脸的不算 missing', () => {
    expect(missingChars('×10⁵')).toEqual([])
    expect(substitutedChars('×10⁵')).toEqual(['⁵'])
  })

  it('画不出来的不算 substituted', () => {
    expect(substitutedChars('T؟')).toEqual([])
  })
})

describe('textDiagnostics 量的是渲染表示', () => {
  it('合成之后那几个字符不再换脸——auto 与 scientific 给出不同的答案', () => {
    expect(textDiagnostics('×10⁵', 'auto').substituted).toEqual(['⁵'])
    expect(textDiagnostics('×10⁵', 'scientific').substituted).toEqual([])
  })

  it('行内标记不算进去（`^{}` 本身不落到纸上）', () => {
    expect(textDiagnostics('cm^{-1}').substituted).toEqual([])
    expect(textDiagnostics('cm^{-1}').missing).toEqual([])
  })

  it('缺字形与解释档无关：合成不出来的还是缺', () => {
    expect(textDiagnostics('T؟', 'scientific').missing).toEqual(['؟'])
  })
})

describe('覆盖表的身份', () => {
  it('说得出自己是哪一版后端出的', () => {
    // 「表是哪来的」必须问得出来：诊断里没有这一句时，覆盖漂移的表现是
    // 一堆对不上的字符，而没人知道该去比哪两个版本。
    expect(COVERAGE_BACKEND).toMatch(/^pymupdf \d+\.\d+/)
  })
})
