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
  it('四步不可交换：`₂` 在中日韩脸里也有，码位却在 CJK 段之外', () => {
    // 这一条钉的是覆盖表的裁剪条件。多减一个 `cjk` 会让这里判成 'cjk'、
    // 后端仍判 'primary'——一个只在下标字符上发作的两侧分歧。
    // U10（ADR 0072）起 `₂` 是 Liberation 自带的 primary（旧后端时代靠回退脸，D07 批准的迁移）。
    expect('₂'.codePointAt(0)).toBeLessThan(CJK_START)
    expect(layerOf(0x2082)).toBe('primary')
  })

  it('没有隐式回退层：表里的 fallback 恒空（ADR 0060 §1）', () => {
    // 阿拉伯 / 天城 / 数学字母 / emoji 在旧后端时代由 PyMuPDF 自己挑的脸画出，现在是 missing
    expect(layerOf(0x1d6fc)).toBe('missing') // 𝛼
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
    expect(planRuns('😀')).toEqual([{ text: '😀', layer: 'missing' }])
    expect(planRuns('😀')[0].text.length).toBe(2)
  })
})

describe('missing 与 substituted 是两句话', () => {
  it('主脸自己画得出的既不算 missing 也不算 substituted', () => {
    // `⁵` 是 Liberation 自带的（U10）；旧后端时代它是回退脸画的、在 substituted 那句里
    expect(missingChars('×10⁵')).toEqual([])
    expect(substitutedChars('×10⁵')).toEqual([])
  })

  it('画不出来的不算 substituted', () => {
    expect(substitutedChars('T؟')).toEqual([])
  })

  it('中日韩不算 substituted——它只有一张脸，说了用户也改不动', () => {
    // 这是能力限制，不是这一次编辑的结果。为一个恒定的、改不动的限制在每
    // 一条中文标注上挂一条建议，只会训练用户忽略整个问题面板。
    expect(layerOf('样'.codePointAt(0) as number)).toBe('cjk')
    expect(substitutedChars('样品 A')).toEqual([])
    expect(substitutedChars('样品 ×10⁵')).toEqual([])
  })
})

describe('textDiagnostics 量的是渲染表示', () => {
  it('合成救回方框——auto 只救画不出的，scientific 只合成主脸没有的', () => {
    // `⁻`（U+207B）哪张脸都没有：auto 把它折成上标 `-`，渲染表示里不再缺；原文判据仍报缺
    expect(missingChars('m⁻²')).toEqual(['⁻'])
    expect(textDiagnostics('m⁻²', 'auto').missing).toEqual([])
    // `⁵` 主脸自己有：两档都原样落笔，谁也不合成、谁也不换脸
    expect(textDiagnostics('×10⁵', 'auto').substituted).toEqual([])
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
    // U10（ADR 0072）起表由 RenderCore 的批准字体集合出：后端名 + 版本串（rendercore 0.1）
    expect(COVERAGE_BACKEND).toMatch(/^rendercore \d+\.\d+/)
  })
})
