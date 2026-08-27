/**
 * 首次引导的显隐必须跟着属性页走。
 *
 * `<md` 视口下内置案例的首次引导照常出现，用户能完成第 1 步（点中标题），但第 2
 * 步指向的字号控件在 `ElementInspector` 里，而它整个被收起了（`hidden … md:flex`，
 * ADR 0007 的刻意受限形态）——引导让用户去改一个**不存在于可见 UI** 的控件。
 *
 * 这里两件事各钉一条：断点判据自己的行为，以及它与那条 CSS 是不是还在说同一件事。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { INSPECTOR_BREAKPOINT, inspectorVisible } from './inspectorViewport'

const stubMatchMedia = (matches: boolean, seen: string[] = []) =>
  vi.stubGlobal('matchMedia', (q: string) => {
    seen.push(q)
    return { matches, media: q, addEventListener: () => {}, removeEventListener: () => {} }
  })

afterEach(() => vi.unstubAllGlobals())

describe('属性页可见性判据', () => {
  it('宽屏（查询命中）→ 可见', () => {
    stubMatchMedia(true)
    expect(inspectorVisible()).toBe(true)
  })

  it('窄屏（查询不命中）→ 不可见，引导因此不出现', () => {
    stubMatchMedia(false)
    expect(inspectorVisible()).toBe(false)
  })

  it('问的是属性页那条断点，不是随手写的一个宽度', () => {
    const seen: string[] = []
    stubMatchMedia(true, seen)
    inspectorVisible()
    expect(seen).toEqual([INSPECTOR_BREAKPOINT])
  })

  it('没有 matchMedia 的环境当作宽屏，而不是把引导整个关掉', () => {
    vi.stubGlobal('matchMedia', undefined)
    expect(inspectorVisible()).toBe(true)
  })
})

describe('与属性页那条 CSS 的同源对', () => {
  // 读源码走 import.meta.glob('?raw')，与 modKey.test.ts 同一手法：src 归
  // tsconfig.app.json 管，那儿不该有 node 类型。
  const sources = import.meta.glob('./PlaygroundApp.tsx', {
    eager: true, query: '?raw', import: 'default',
  }) as Record<string, string>
  const app = Object.values(sources)[0]

  it('属性页的 aside 仍然用 md 断点收起', () => {
    expect(app).toContain('md:flex')
    const aside = app.split('\n').find((l) => l.includes('<aside') && l.includes('md:flex'))
    expect(aside, '属性页那个 aside 不见了或换了断点写法').toBeTruthy()
  })

  it('断点常量就是 Tailwind 默认的 md（48rem = 768px）', () => {
    // 换了 Tailwind 的 screens 配置就得同时改这里——两侧只有一条判据。
    expect(INSPECTOR_BREAKPOINT).toBe('(min-width: 48rem)')
  })

  it('引导的渲染条件确实带上了这个判据', () => {
    expect(app).toContain('inspectorShown && (')
  })
})
