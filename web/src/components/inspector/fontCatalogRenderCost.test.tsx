/**
 * 本机字体清单（ADR 0169）的渲染开销：清单有几百个族，选中文字后的每次重渲染都不许再为它们
 * 逐项翻译 / 逐项造 JSX（#1762 上线后符号插入慢了一倍：每次渲染几百次 i18n 查表）。
 *   1. 本机字体名不走 i18n，直接用名字（i18n 里字体族只登记了 serif / sans-serif / monospace 三个通用族）。
 *   2. 适配器给出的 options / option_labels 在清单不变时是同一个引用，下拉模型的行才能复用。
 *   3. 同一份 options + labels + 语言，下拉模型的行是同一个对象，不重造。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { literal } from '@/i18n'
import { resetFontCatalogForTests, type FontCatalog } from '@/lib/fontCatalog'
import { useDocumentStore } from '@/store/documentStore'
import { resetGestureCoordinator } from '@/store/gestureCoordinator'
import { emptyProject, type TextObject } from '@/types/document'
import { fontFamilySelectModel } from './controls/fontFamilySelect'
import { fontFamilyOptionLabel } from './roles/registry'
import { useCanvasTypography, type TypographyAdapter } from './typographyAdapter'

const tCalls = vi.hoisted(() => ({ keys: [] as string[] }))
vi.mock('@/i18n', async (orig) => {
  const m = await orig<typeof import('@/i18n')>()
  return {
    ...m,
    t: ((key: string, ...rest: unknown[]) => {
      tCalls.keys.push(String(key))
      return (m.t as unknown as (...a: unknown[]) => unknown)(key, ...rest)
    }) as unknown as typeof m.t,
  }
})
vi.mock('@/lib/sourceFontCache', () => ({
  sourceFontState: () => 'unknown',
  useSourceFonts: () => 0,
  subscribeSourceFonts: () => () => {},
  previewFamilyOf: () => undefined,
}))
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const N = 600
const face = { sha256: 'a'.repeat(64), face: 0, bold: false, italic: false }
const bigCatalog = (): FontCatalog => ({
  families: Array.from({ length: N }, (_, i) => ({
    name: `Local Font ${i}`,
    zh: i % 50 === 0 ? `本机字体${i}` : null,
    cjk: i % 7 === 0,
    generic: null,
    faces: [face],
  })),
  builtin: {},
})
const fontKeys = () => tCalls.keys.filter((k) => k.startsWith('enum.fontfamily.'))

describe('本机字体不走 i18n', () => {
  beforeEach(() => {
    tCalls.keys = []
  })
  it('几百个本机族排成下拉：一次 enum.fontfamily 查表都没有；通用族照旧查', () => {
    const names = Array.from({ length: N }, (_, i) => `Local Font ${i}`)
    fontFamilySelectModel({ value: 'Local Font 3', options: names, labels: undefined, faces: undefined, onChange: () => {} })
    expect(fontKeys()).toEqual([])
    expect(fontFamilyOptionLabel('serif', undefined, ['serif'])).not.toBe('serif')
    expect(fontKeys()).toEqual(['enum.fontfamily.serif'])
  })
})

describe('适配器给出的字体选项是稳定引用', () => {
  let root: Root
  let container: HTMLDivElement
  let adapter: TypographyAdapter
  const obj = (): TextObject => ({
    id: 't1', type: 'text', text: 'H2O', sizePt: 9, bold: false, color: '#000000', align: 'left', x: 0, y: 0, w: 20, h: 8,
  })
  function Harness() {
    const objs = useDocumentStore((st) => st.doc.objects).filter((o): o is TextObject => o.type === 'text')
    adapter = useCanvasTypography(objs)
    return null
  }
  beforeEach(async () => {
    resetFontCatalogForTests(bigCatalog())
    resetGestureCoordinator()
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_fontcost')
    useDocumentStore.getState().commit(literal('放入对象'), (d) => void d.objects.push(obj()))
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root.render(<Harness />))
  })
  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    resetFontCatalogForTests()
    resetGestureCoordinator()
  })

  it('清单不变时，重渲染前后 options / option_labels 是同一个引用', () => {
    const a = adapter.fieldOf('fontFamily')!
    act(() => root.render(<Harness />))
    const b = adapter.fieldOf('fontFamily')!
    expect(a.options!.length).toBeGreaterThan(N)
    expect(b.options).toBe(a.options)
    expect(b.option_labels).toBe(a.option_labels)
    const c1 = adapter.fieldOf('fontFallback')!
    expect(adapter.fieldOf('fontFallback')!.options).toBe(c1.options)
  })

  it('同一份 options / labels，第二次建下拉模型不再造行，也不再查 i18n', () => {
    const f = adapter.fieldOf('fontFamily')!
    const mk = () => fontFamilySelectModel({ value: 'serif', options: f.options!, labels: f.option_labels, faces: undefined, onChange: () => {} })
    const first = mk()
    tCalls.keys = []
    const second = mk()
    expect(fontKeys()).toEqual([])
    expect(second.options[10]).toBe(first.options[10])
    expect(second.options[N]).toBe(first.options[N])
  })
})
