/**
 * 画布文字的原图字体预览（ADR 0164）：本机服务供了字节、浏览器收下之后用同一张脸画；
 * 其它情形（还没问到 / 用不了 / 没加载成功）一律是 `fontFamily` 的批准族栈——**不换别的字体、不改文档**。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const cache = vi.hoisted(() => ({
  state: vi.fn((_f?: unknown): string => 'unknown'),
  family: vi.fn((_f?: unknown): string | undefined => undefined),
}))
vi.mock('@/lib/sourceFontCache', () => ({
  sourceFontState: cache.state,
  previewFamilyOf: cache.family,
  useSourceFonts: () => 0,
  subscribeSourceFonts: () => () => {},
}))

import { cssFamilyOf } from '@/lib/sourceFont'
import { canvasFontStack } from '@/lib/typography'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type TextObject } from '@/types/document'
import { TextView } from './TextView'

const SHA = '0f'.repeat(32)
const sf = { family: 'DejaVu Sans', sha256: SHA, face: 0 }
let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  )
  localStorage.clear()
  useUiStore.setState({ editingTextId: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_textview_sf')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  cache.state.mockReset()
  cache.family.mockReset()
  cache.state.mockReturnValue('unknown')
  cache.family.mockReturnValue(undefined)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
})

const render = (over: Partial<TextObject>) => {
  const obj: TextObject = {
    id: 't1', type: 'text', text: 'H2O', sizePt: 10, bold: false, color: '#111', align: 'left',
    x: 0, y: 0, w: 40, h: 6, ...over,
  }
  useDocumentStore.getState().silent((d) => void d.objects.push(obj))
  act(() => root.render(<TextView obj={useDocumentStore.getState().doc.objects[0] as TextObject} />))
  return container.querySelector('div')!
}

describe('TextView 原图字体预览', () => {
  it('可用且已加载：身份派生的族名在最前，批准族栈当回退', () => {
    cache.state.mockReturnValue('ok')
    cache.family.mockReturnValue(cssFamilyOf(sf))
    const el = render({ sourceFont: sf, fontFamily: 'sans-serif' })
    expect(el.style.fontFamily).toBe(`"${cssFamilyOf(sf)}", ${canvasFontStack('sans-serif')}`)
  })

  it.each(['unknown', 'not_found', 'embedding_restricted', 'unsupported'])(
    '状态 %s：用 fontFamily 的批准族栈',
    (state) => {
      cache.state.mockReturnValue(state)
      cache.family.mockReturnValue(cssFamilyOf(sf)) // 即使缓存里有，状态不是 ok 就不用
      expect(render({ sourceFont: sf, fontFamily: 'monospace' }).style.fontFamily).toBe(
        canvasFontStack('monospace'),
      )
    },
  )

  it('状态 ok 但字节没加载成功：仍是批准族栈', () => {
    cache.state.mockReturnValue('ok')
    expect(render({ sourceFont: sf }).style.fontFamily).toBe(canvasFontStack('serif'))
  })

  const cjk = { family: 'SimHei', sha256: '1e'.repeat(32), face: 0 }

  it('CJK 脸（sourceFont.cjk）也进预览栈：正文脸之后、批准族之前', () => {
    cache.state.mockReturnValue('ok')
    cache.family.mockImplementation((f: unknown) => cssFamilyOf(f as typeof sf))
    expect(render({ sourceFont: { ...sf, cjk }, fontFamily: 'sans-serif' }).style.fontFamily).toBe(
      `"${cssFamilyOf(sf)}", "${cssFamilyOf(cjk)}", ${canvasFontStack('sans-serif')}`,
    )
  })

  it('CJK 脸不可用：与导出一样只剩正文脸 + 批准族（汉字回批准的 Noto）', () => {
    cache.state.mockImplementation((f: unknown) => ((f as typeof sf).sha256 === cjk.sha256 ? 'not_found' : 'ok'))
    cache.family.mockImplementation((f: unknown) => cssFamilyOf(f as typeof sf))
    expect(render({ sourceFont: { ...sf, cjk }, fontFamily: 'sans-serif' }).style.fontFamily).toBe(
      `"${cssFamilyOf(sf)}", ${canvasFontStack('sans-serif')}`,
    )
  })

  it('没有 sourceFont 的标注：与从前逐字相同', () => {
    expect(render({}).style.fontFamily).toBe('var(--font-doc)')
    expect(cache.state).not.toHaveBeenCalled()
  })

  it('文档对象不被预览改动', () => {
    cache.state.mockReturnValue('not_found')
    render({ sourceFont: sf, fontFamily: 'sans-serif' })
    expect(useDocumentStore.getState().doc.objects[0]).toMatchObject({ sourceFont: sf, fontFamily: 'sans-serif' })
  })
})
