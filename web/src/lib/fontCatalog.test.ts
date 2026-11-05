import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const fetchMock = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ fetchSourceFontCatalog: fetchMock }))

import {
  isBuiltinFace,
  refreshFontCatalogIfStale,
  resetFontCatalogForTests,
  resetLocalFamily,
  restyleLocalFace,
  setBuiltinFamily,
  setLocalCjkFamily,
  setLocalFamily,
  type FontCatalog,
} from './fontCatalog'
import type { TextObject } from '@/types/document'

const sha = (c: string) => c.repeat(64)
const face = (c: string, bold = false, italic = false, n = 0) => ({ sha256: sha(c), face: n, bold, italic })

const catalog: FontCatalog = {
  families: [
    {
      name: 'Times New Roman',
      zh: null,
      cjk: false,
      generic: 'serif',
      faces: [face('a'), face('b', true), face('c', false, true), face('d', true, true)],
    },
    { name: 'SimSun', zh: '宋体', cjk: true, generic: null, faces: [face('e')] },
  ],
  builtin: {
    serif: { name: 'Liberation Serif', faces: [face('1'), face('2', true)] },
    'sans-serif': { name: 'Liberation Sans', faces: [face('3'), face('4', true)] },
  },
}

const text = (over: Partial<TextObject> = {}) => ({ id: 't', type: 'text', bold: false, ...over }) as TextObject

describe('同族的其它脸', () => {
  it('记下另外三张粗斜，不含自己', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    expect(o.sourceFont?.siblings).toEqual([
      { sha256: sha('b'), face: 0, bold: true },
      { sha256: sha('c'), face: 0, italic: true },
      { sha256: sha('d'), face: 0, bold: true, italic: true },
    ])
  })

  it('按 B 换脸后，常规脸进了 siblings，粗体脸出来', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    restyleLocalFace(o, catalog, true, false)
    expect(o.sourceFont?.sha256).toBe(sha('b'))
    expect(o.sourceFont?.siblings?.map((s) => s.sha256)).toEqual([sha('a'), sha('c'), sha('d')])
  })

  it('中文脸也带同族粗体，脸本身留常规', () => {
    const c: FontCatalog = {
      ...catalog,
      families: [
        ...catalog.families,
        { name: 'Microsoft YaHei', zh: '微软雅黑', cjk: true, generic: null, faces: [face('f'), face('g', true)] },
      ],
    }
    const o = text({ bold: true })
    setLocalCjkFamily(o, c, 'Microsoft YaHei')
    expect(o.sourceFont?.cjk).toEqual({
      family: 'Microsoft YaHei',
      sha256: sha('f'),
      face: 0,
      siblings: [{ sha256: sha('g'), face: 0, bold: true }],
    })
  })

  it('只有一张脸的字体不写 siblings 字段', () => {
    const o = text()
    setLocalFamily(o, catalog, 'SimSun')
    expect(o.sourceFont).not.toHaveProperty('siblings')
  })
})

describe('选本机字体', () => {
  it('英文选本机字体：写身份和回退族，不带文件路径', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    expect(o.sourceFont).toMatchObject({ family: 'Times New Roman', sha256: sha('a'), face: 0 })
    expect(o.fontFamily).toBe('serif')
  })

  it('已是粗体时选到这一族的粗体那张脸', () => {
    const o = text({ bold: true })
    setLocalFamily(o, catalog, 'Times New Roman')
    expect(o.sourceFont).toMatchObject({ sha256: sha('b'), bold: true })
  })

  it('这一族没有粗体：选常规脸，对象的粗体留着，由画布和导出模拟', () => {
    const o = text({ bold: true })
    setLocalFamily(o, catalog, 'SimSun')
    expect(o.sourceFont?.sha256).toBe(sha('e'))
    expect(o.sourceFont).not.toHaveProperty('bold')
    expect(o.bold).toBe(true)
  })

  it('换成自带中文字形的字体：之前单独选的中文脸清掉', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    setLocalCjkFamily(o, catalog, 'SimSun')
    setLocalFamily(o, catalog, 'SimSun')
    expect(o.sourceFont?.cjk).toBeUndefined()
  })
})

describe('中文那一格', () => {
  it('英文是本机字体：只加 cjk', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    setLocalCjkFamily(o, catalog, 'SimSun')
    expect(o.sourceFont).toMatchObject({
      family: 'Times New Roman',
      cjk: { family: 'SimSun', sha256: sha('e'), face: 0 },
    })
  })

  it('英文留在内置字体：正文脸点名批准字体，界面仍是内置族', () => {
    const o = text({ fontFamily: 'serif' })
    setLocalCjkFamily(o, catalog, 'SimSun')
    expect(o.sourceFont).toMatchObject({ family: 'Liberation Serif', sha256: sha('1') })
    expect(isBuiltinFace(catalog, o.sourceFont!)).toBe(true)
    expect(o.fontFamily).toBe('serif')
  })

  it('回到自动：内置英文 + 自动 = 整条 sourceFont 清掉，文档回到没选过的样子', () => {
    const o = text({ fontFamily: 'serif' })
    setLocalCjkFamily(o, catalog, 'SimSun')
    setLocalCjkFamily(o, catalog, null)
    expect(o.sourceFont).toBeUndefined()
  })

  it('回到自动：本机英文字体留着', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    setLocalCjkFamily(o, catalog, 'SimSun')
    setLocalCjkFamily(o, catalog, null)
    expect(o.sourceFont?.family).toBe('Times New Roman')
    expect(o.sourceFont?.cjk).toBeUndefined()
  })

  it('英文选回内置：中文脸保留', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    setLocalCjkFamily(o, catalog, 'SimSun')
    expect(setBuiltinFamily(o, catalog, 'sans-serif')).toBe(true)
    expect(o.sourceFont).toMatchObject({ family: 'Liberation Sans', cjk: { family: 'SimSun' } })
    expect(o.fontFamily).toBe('sans-serif')
  })

  it('没有中文脸时英文选回内置不走这里（调用方清掉 sourceFont）', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    expect(setBuiltinFamily(o, catalog, 'sans-serif')).toBe(false)
  })
})

describe('粗体 / 斜体', () => {
  it('按 B 换成同族的粗体文件，中文脸不动', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    setLocalCjkFamily(o, catalog, 'SimSun')
    expect(restyleLocalFace(o, catalog, true, false)).toBe(true)
    expect(o.sourceFont).toMatchObject({ sha256: sha('b'), bold: true, cjk: { family: 'SimSun' } })
    expect(o.bold).toBe(true)
  })

  it('这一族没有那张脸：脸不换，对象记下粗斜，由画布和导出模拟', () => {
    const o = text()
    setLocalFamily(o, catalog, 'SimSun')
    expect(restyleLocalFace(o, catalog, true, true)).toBe(true)
    expect(o.bold).toBe(true)
    expect(o.italic).toBe(true)
    expect(o.sourceFont?.sha256).toBe(sha('e'))
    expect(o.sourceFont).not.toHaveProperty('bold')
    expect(restyleLocalFace(o, catalog, false, false)).toBe(true)
    expect(o.bold).toBe(false)
    expect(o).not.toHaveProperty('italic')
  })

  it('清单不认识的脸（复制来的原图字体）走 ADR 0164 的失效规则', () => {
    const o = text({ sourceFont: { family: 'DejaVu Sans', sha256: sha('f'), face: 0 } })
    expect(restyleLocalFace(o, catalog, true, false)).toBe(false)
  })
})

describe('单项恢复英文', () => {
  it('中文脸保住，正文脸换回默认族的批准字体，fontFamily 删掉', () => {
    const o = text({ fontFamily: 'sans-serif' })
    setLocalFamily(o, catalog, 'Times New Roman')
    setLocalCjkFamily(o, catalog, 'SimSun')
    expect(resetLocalFamily(o, catalog)).toBe(true)
    expect(o.sourceFont).toMatchObject({ family: 'Liberation Serif', sha256: sha('1'), cjk: { family: 'SimSun' } })
    expect(o.fontFamily).toBeUndefined()
  })

  it('已是粗体时取默认族里的粗体那张', () => {
    const o = text({ bold: true })
    setLocalFamily(o, catalog, 'Times New Roman')
    setLocalCjkFamily(o, catalog, 'SimSun')
    resetLocalFamily(o, catalog)
    expect(o.sourceFont).toMatchObject({ sha256: sha('2'), bold: true })
  })

  it('没有中文脸：返回 false，调用方整条清掉', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    expect(resetLocalFamily(o, catalog)).toBe(false)
  })
})

describe('清单刷新', () => {
  const ok = (state: 'ready' | 'scanning' = 'ready') =>
    ({ ok: true, state, families: [], builtin: {} }) as never

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-05T00:00:00Z'))
    resetFontCatalogForTests()
    fetchMock.mockReset()
  })
  afterEach(() => {
    vi.useRealTimers()
    resetFontCatalogForTests()
  })

  it('没问过就问；60 秒内再挂载不问，满 60 秒再问', async () => {
    fetchMock.mockResolvedValue(ok())
    refreshFontCatalogIfStale()
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(59_000)
    refreshFontCatalogIfStale()
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(1_000)
    refreshFontCatalogIfStale()
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('同一时刻只有一个请求在飞', async () => {
    let done!: (v: never) => void
    fetchMock.mockReturnValue(new Promise((r) => (done = r)))
    refreshFontCatalogIfStale()
    vi.advanceTimersByTime(120_000)
    refreshFontCatalogIfStale()
    refreshFontCatalogIfStale()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    done(ok())
    await vi.advanceTimersByTimeAsync(0)
  })

  it('失败不重试轰炸，下次挂载满 60 秒再试', async () => {
    fetchMock.mockRejectedValue(new Error('404'))
    refreshFontCatalogIfStale()
    await vi.advanceTimersByTimeAsync(0)
    refreshFontCatalogIfStale()
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(61_000)
    refreshFontCatalogIfStale()
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
