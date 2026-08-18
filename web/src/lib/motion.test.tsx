/// <reference types="node" />
/**
 * 动效地基。这里看护三件事：
 *
 * 1. **CSS 与 JS 的时长逐值同源**——直接读 index.css 比对，不是抄一份常量。
 *    两边错开的后果是「CSS 播 180ms、JS 等 120ms」这类只在真浏览器里
 *    偶发闪一下的问题，单测再多也照不到。
 * 2. **reduced-motion 下 tween 一帧都不放**（同步落终态）。CSS 有全局兜底，
 *    JS 动画没有——这条是那半边唯一的守卫。
 * 3. **usePresence 的退场保活会真的卸载**，不会把 DOM 永远留着。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  DURATION,
  EASE_POP,
  easeOutCubic,
  prefersReducedMotion,
  tween,
  usePresence,
} from './motion'

/**
 * 直接读源文件，不走 import —— vitest 默认 `css: false`，任何形式的 CSS
 * import（含 `?raw`）都会被替换成空串，比对就永远是假绿。
 * 兼容从 web/ 或从仓库根启动两种 cwd。
 */
const CSS_PATH = ['src/index.css', 'web/src/index.css']
  .map((p) => resolve(process.cwd(), p))
  .find(existsSync)
if (!CSS_PATH) throw new Error('找不到 index.css')
const CSS = readFileSync(CSS_PATH, 'utf8')

/** 读 index.css 的 @theme 里某个 token 的字面量 */
function cssToken(name: string): string {
  const m = CSS.match(new RegExp(`--${name}:\\s*([^;]+);`))
  if (!m) throw new Error(`index.css 里没有 --${name}`)
  return m[1].trim()
}

function setReducedMotion(reduce: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: reduce && q.includes('prefers-reduced-motion'),
    media: q,
    addEventListener() {},
    removeEventListener() {},
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('时长 token：CSS 与 JS 同源', () => {
  it.each(Object.entries(DURATION))('--duration-%s 与 DURATION.%s 相等', (name, ms) => {
    expect(cssToken(`duration-${name}`)).toBe(`${ms}ms`)
  })

  it('退场比进场短一档——否则退场会挡住用户的下一步动作', () => {
    expect(DURATION.exit).toBeLessThan(DURATION.fast)
  })

  it('index.css 里声明的每个 --duration-* 都在 DURATION 里有对应项', () => {
    const declared = [...CSS.matchAll(/--duration-([a-z]+):/g)].map((m) => m[1])
    expect(new Set(declared)).toEqual(new Set(Object.keys(DURATION)))
  })
})

describe('关键帧的形态约束', () => {
  it('关键帧里不许出现 translate(-50%…)', () => {
    // Tailwind v4 的 `-translate-x-1/2` 是**独立的 `translate` 属性**，与动画的
    // `transform` 各走各的、会叠加。给关键帧补一份居中位移 = 播放期间多偏半个
    // 身位（实测 250px）。居中的浮层直接套 pop-in 就是对的。
    expect(CSS).not.toMatch(/@keyframes[\s\S]*?translate\(-50%/)
  })

  it('EASE_POP 与 index.css 的 --ease-pop 逐字节相同', () => {
    // WAAPI 只认字符串缓动，JS 侧只能自己带一份；带了就必须钉住
    expect(cssToken('ease-pop')).toBe(EASE_POP)
  })

  it('交叉淡出是 linear + forwards', () => {
    // linear：两侧不透明度和恒为 1，中途不会出现一道暗带
    // forwards：动画结束到卸载之间不会有一帧回到 opacity:1 闪一下
    const v = cssToken('animate-crossfade-out')
    expect(v).toContain('linear')
    expect(v).toContain('forwards')
  })

  it('位移类关键帧的幅度都 ≤4px——动效是点缀，不是弹跳', () => {
    for (const px of [...CSS.matchAll(/transform:[^;]*translateY\((-?[\d.]+)px\)/g)]) {
      expect(Math.abs(Number(px[1]))).toBeLessThanOrEqual(4)
    }
  })
})

describe('tween', () => {
  it('reduced-motion：同步落终态，一帧都不放', () => {
    setReducedMotion(true)
    const raf = vi.spyOn(globalThis, 'requestAnimationFrame')
    const seen: number[] = []
    let done = false

    const cancel = tween({ duration: 1000, onUpdate: (p) => seen.push(p), onDone: () => (done = true) })

    expect(seen).toEqual([1])
    expect(done).toBe(true)
    expect(raf).not.toHaveBeenCalled()
    cancel()
  })

  it('正常情况：逐帧推进，最后一帧正好是 1', async () => {
    setReducedMotion(false)
    const seen: number[] = []
    await new Promise<void>((resolve) => {
      tween({ duration: 40, onUpdate: (p) => seen.push(p), onDone: resolve })
    })
    expect(seen.length).toBeGreaterThan(1)
    expect(seen.at(-1)).toBe(1)
    // 缓动是单调不减的
    expect([...seen].sort((a, b) => a - b)).toEqual(seen)
  })

  it('cancel 之后不再回调', async () => {
    setReducedMotion(false)
    const seen: number[] = []
    const cancel = tween({ duration: 500, onUpdate: (p) => seen.push(p) })
    cancel()
    const n = seen.length
    await new Promise((r) => setTimeout(r, 60))
    expect(seen.length).toBe(n)
  })

  it('easeOutCubic 首尾闭合', () => {
    expect(easeOutCubic(0)).toBe(0)
    expect(easeOutCubic(1)).toBe(1)
  })
})

describe('prefersReducedMotion', () => {
  it('没有 matchMedia 的环境（老 WebView / SSR）当作不减少动效，而不是崩', () => {
    vi.stubGlobal('matchMedia', undefined)
    expect(prefersReducedMotion()).toBe(false)
  })
})

describe('usePresence', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })
  afterEach(() => {
    act(() => root.unmount())
    container.remove()
  })

  function Probe({ open }: { open: boolean }) {
    const { mounted, state } = usePresence(open, 90)
    return mounted ? <div data-testid="box" data-state={state} /> : null
  }

  const box = () => container.querySelector('[data-testid="box"]')

  it('关闭后先保活播退场，再真的卸载', async () => {
    setReducedMotion(false)
    act(() => root.render(<Probe open />))
    expect(box()?.getAttribute('data-state')).toBe('open')

    act(() => root.render(<Probe open={false} />))
    // 还挂着，且已经切到 closed —— CSS 的 data-[state=closed]:animate-* 才有机会播
    expect(box()?.getAttribute('data-state')).toBe('closed')

    await act(async () => {
      await new Promise((r) => setTimeout(r, 140))
    })
    expect(box()).toBeNull()
  })

  it('reduced-motion：立刻卸载，不留空档期', () => {
    setReducedMotion(true)
    act(() => root.render(<Probe open />))
    act(() => root.render(<Probe open={false} />))
    expect(box()).toBeNull()
  })

  it('退场途中又打开：撤销卸载，不会播到一半消失', async () => {
    setReducedMotion(false)
    act(() => root.render(<Probe open />))
    act(() => root.render(<Probe open={false} />))
    act(() => root.render(<Probe open />))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 140))
    })
    expect(box()?.getAttribute('data-state')).toBe('open')
  })
})
