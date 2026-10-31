/**
 * 图标微动效（ADR 0154）的看护：
 *   1. 登记表 ↔ 关键帧同源：motion.ts 里每枚图标在 motion.css 里都有 `.icon-<名>[data-play]` 规则，
 *      motion.css 里出现的每个 `.icon-<名>` 都登记过（一侧加了另一侧忘了 = 不动 / 死规则）。
 *   2. 角色表与几何对得上：`parts` / `sel` 每一组的条数 = 那一形该组的路径条数（defs.ts 改了几何，
 *      角色就会错位到别的路径上）。
 *   3. 委托：点按钮播 press、指针进按钮播 hover；禁用的按钮、别的按钮里的图标、减少动态效果时不播。
 *   4. `playIn` 挂载即带 `data-play`；没登记动作的图标不带任何动效属性。
 */
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  Check,
  Download,
  ICON_DEFS,
  ICON_MOTION,
  LayoutGrid,
  Bold,
  SlidersHorizontal,
  Sparkles,
  X,
  type IconDef,
} from './index'
import { _resetIconMotionForTest, installIconMotion } from '../iconMotion'
import { Spinner } from '../Spinner'

// 直接读源文件：vitest 默认 css: false，任何 CSS import（含 ?raw）都是空串（同 lib/motion.test）
const CSS_PATH = ['src/components/ui/icons/motion.css', 'web/src/components/ui/icons/motion.css']
  .map((p) => resolve(process.cwd(), p))
  .find(existsSync)
if (!CSS_PATH) throw new Error('找不到 motion.css')
const css = readFileSync(CSS_PATH, 'utf8')

const kebab = (name: string) =>
  name
    .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .replace(/([A-Za-z])(\d)/g, '$1-$2')
    .toLowerCase()

describe('登记表 ↔ 关键帧同源', () => {
  const names = Object.keys(ICON_MOTION)
  const byKebab = new Map(Object.keys(ICON_DEFS).map((n) => [kebab(n), n]))

  it('登记的每枚图标都在图标集里，并在 motion.css 里有播放规则', () => {
    for (const name of names) {
      expect(ICON_DEFS, name).toHaveProperty(name)
      expect(css, name).toContain(`.icon-${kebab(name)}[data-play]`)
    }
  })

  it('motion.css 里出现的图标类都登记过', () => {
    const used = new Set([...css.matchAll(/\.icon-([a-z0-9-]+)/g)].map((m) => m[1]))
    for (const k of used) {
      const name = byKebab.get(k)
      expect(name, `.icon-${k} 不是图标集里的名字`).toBeTruthy()
      expect(ICON_MOTION, `.icon-${k} 没在 motion.ts 登记`).toHaveProperty(name!)
    }
  })

  it('角色条数与几何条数一致', () => {
    for (const [name, m] of Object.entries(ICON_MOTION)) {
      const def = (ICON_DEFS as Record<string, IconDef>)[name]
      const check = (parts: object | undefined, variant: IconDef | undefined, label: string) => {
        if (!parts || typeof parts !== 'object') return
        for (const [list, roles] of Object.entries(parts) as [keyof IconDef, unknown[]][]) {
          const paths = variant?.[list] as unknown[] | undefined
          expect(paths?.length, `${name} ${label}.${String(list)}`).toBe(roles.length)
        }
      }
      check(m!.parts, def, 'parts')
      if (m!.sel && m!.sel !== 'all') {
        expect(def.sel, `${name} 写了 sel 角色却没有选中态`).toBeTruthy()
        check(m!.sel, def.sel, 'sel')
      }
    }
  })

  it('时长是 300–720ms 的一次性动作', () => {
    for (const [name, m] of Object.entries(ICON_MOTION)) {
      expect(m!.ms, name).toBeGreaterThanOrEqual(300)
      expect(m!.ms, name).toBeLessThanOrEqual(720)
    }
    expect(css).not.toMatch(/infinite/)
  })

  it('减少动态效果时一律不播', () => {
    const reduced = css.slice(css.indexOf('@media (prefers-reduced-motion: reduce)'))
    expect(reduced).toContain('.icon[data-play] [data-m] { animation: none !important; }')
    expect(reduced).toContain('.icon[data-tap] { animation: none !important; }')
  })
})

describe('渲染', () => {
  let host: HTMLDivElement
  let root: Root
  beforeEach(() => {
    host = document.createElement('div')
    document.body.append(host)
    root = createRoot(host)
  })
  afterEach(() => {
    act(() => root.unmount())
    host.remove()
  })
  const render = (el: React.ReactNode) => act(() => root.render(el))

  it('登记的图标带触发、时长与路径角色；没登记的什么都不带', () => {
    render(
      <>
        <Download />
        <Bold />
      </>,
    )
    const dl = host.querySelector('svg.icon-download')!
    expect(dl.getAttribute('data-motion')).toBe('press')
    expect((dl as SVGElement).style.getPropertyValue('--icon-ms')).toBe('520ms')
    expect([...dl.querySelectorAll('path')].map((p) => p.getAttribute('data-m'))).toEqual(['q', 'p', 'p'])
    const ty = host.querySelector('svg.icon-bold')!
    expect(ty.hasAttribute('data-motion')).toBe(false)
    expect(ty.querySelector('[data-m]')).toBeNull()
  })

  it('一笔画的路径带 pathLength=1，依次动的带错开下标', () => {
    render(
      <>
        <Check />
        <LayoutGrid />
      </>,
    )
    expect(host.querySelector('svg.icon-check path')!.getAttribute('pathLength')).toBe('1')
    const tiles = [...host.querySelectorAll('svg.icon-layout-grid path')] as SVGPathElement[]
    expect(tiles.map((t) => t.style.getPropertyValue('--k'))).toEqual(['0', '1', '2', '3'])
  })

  it('选中态写了角色才动，角色按选中形的路径贴', () => {
    render(<Sparkles filled />)
    const sp = host.querySelector('svg.icon-sparkles')!
    expect(sp.getAttribute('data-motion')).toBe('press')
    expect([...sp.querySelectorAll('path')].map((p) => p.getAttribute('data-m'))).toEqual(['p', 'q'])
  })

  it('属性旋钮：轨道、延长线与两颗旋钮各有角色，不靠蒙版（WebKit 的蒙版抗锯齿与普通描边不同，静止形会变）', () => {
    render(<SlidersHorizontal />)
    const sl = host.querySelector('svg.icon-sliders-horizontal')!
    expect(sl.querySelector('mask')).toBeNull()
    // 延长线静止不画
    expect([...sl.querySelectorAll('[data-m="v"], [data-m="w"]')].map((p) => p.getAttribute('stroke-opacity'))).toEqual(['0', '0'])
    expect([...sl.querySelectorAll('path')].map((p) => p.getAttribute('data-m'))).toEqual(['u', null, 'p', null, 'x', 'q', 'v', 'w'])
  })

  it('playIn 挂载即播；调用方的 style 不被吞', () => {
    render(<X playIn style={{ color: 'red' }} />)
    const x = host.querySelector('svg.icon-x') as SVGElement
    expect(x.hasAttribute('data-play')).toBe(true)
    expect(x.style.color).toBe('red')
    expect(x.style.getPropertyValue('--icon-ms')).toBe('360ms')
  })
})

describe('委托', () => {
  let host: HTMLDivElement
  let root: Root
  const flushFrame = () => act(() => vi.runAllTimers())
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame'] })
    _resetIconMotionForTest()
    installIconMotion(document)
    host = document.createElement('div')
    document.body.append(host)
    root = createRoot(host)
  })
  afterEach(() => {
    act(() => root.unmount())
    host.remove()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('按钮里没有点按动作的图标点的时候轻点一下（悬停图标、没登记的图标都算）', () => {
    act(() =>
      root.render(
        <button type="button">
          <Bold data-plain />
          <LayoutGrid data-hover />
          <Download data-press />
        </button>,
      ),
    )
    act(() => host.querySelector('button')!.click())
    flushFrame()
    expect(host.querySelector('[data-plain]')!.hasAttribute('data-tap')).toBe(true)
    expect(host.querySelector('[data-hover]')!.hasAttribute('data-tap')).toBe(true)
    expect(host.querySelector('[data-press]')!.hasAttribute('data-tap')).toBe(false)
    expect(host.querySelector('[data-press]')!.hasAttribute('data-play')).toBe(true)
  })

  it('点按钮播按钮里的 press 图标，不播里面另一颗按钮的', () => {
    act(() =>
      root.render(
        <div data-icon-host data-outer>
          <Download data-a />
          <button type="button">
            <Download data-b />
          </button>
        </div>,
      ),
    )
    act(() => (host.querySelector('[data-outer]') as HTMLElement).click())
    flushFrame()
    expect(host.querySelector('[data-a]')!.hasAttribute('data-play')).toBe(true)
    expect(host.querySelector('[data-b]')!.hasAttribute('data-play')).toBe(false)
  })

  it('禁用的按钮不播', () => {
    act(() =>
      root.render(
        <button type="button" aria-disabled="true">
          <Download />
        </button>,
      ),
    )
    act(() => host.querySelector('button')!.click())
    flushFrame()
    expect(host.querySelector('svg')!.hasAttribute('data-play')).toBe(false)
  })

  it('指针进按钮播 hover 图标；在按钮里面移动不重播；press 图标不因悬停而播', () => {
    act(() =>
      root.render(
        <button type="button">
          <LayoutGrid data-h />
          <Download data-p />
        </button>,
      ),
    )
    const btn = host.querySelector('button')!
    const over = (target: Element, related: Element | null) =>
      target.dispatchEvent(
        new MouseEvent('pointerover', { bubbles: true, relatedTarget: related }) as unknown as PointerEvent,
      )
    act(() => over(btn, document.body))
    const grid = host.querySelector('[data-h]')!
    expect(grid.hasAttribute('data-play')).toBe(true)
    expect(host.querySelector('[data-p]')!.hasAttribute('data-play')).toBe(false)
    grid.removeAttribute('data-play')
    act(() => over(grid, btn))
    expect(grid.hasAttribute('data-play')).toBe(false)
  })

  it('列表项被高亮（data-active / data-highlighted 亮起，命令面板 ↑↓）时播它的图标，不分触发方式；熄灭不播', async () => {
    act(() =>
      root.render(
        <ul>
          <li data-row>
            <Download data-press />
            <LayoutGrid data-hover />
          </li>
        </ul>,
      ),
    )
    const row = host.querySelector('[data-row]')!
    await act(async () => {
      row.setAttribute('data-active', 'true')
      await Promise.resolve()
    })
    expect(host.querySelector('[data-press]')!.hasAttribute('data-play')).toBe(true)
    expect(host.querySelector('[data-hover]')!.hasAttribute('data-play')).toBe(true)
    host.querySelectorAll('svg').forEach((s) => s.removeAttribute('data-play'))
    await act(async () => {
      row.removeAttribute('data-active')
      await Promise.resolve()
    })
    expect(host.querySelector('[data-press]')!.hasAttribute('data-play')).toBe(false)
  })

  it('菜单项（Radix 的 data-highlighted）亮起时播；值变了不重播', async () => {
    act(() =>
      root.render(
        <div data-item>
          <LayoutGrid data-hover />
        </div>,
      ),
    )
    const item = host.querySelector('[data-item]')!
    await act(async () => {
      item.setAttribute('data-highlighted', '')
      await Promise.resolve()
    })
    const svg = host.querySelector('[data-hover]')!
    expect(svg.hasAttribute('data-play')).toBe(true)
    svg.removeAttribute('data-play')
    await act(async () => {
      item.setAttribute('data-highlighted', 'x')
      await Promise.resolve()
    })
    expect(svg.hasAttribute('data-play')).toBe(false)
  })

  it('通用轻点不碰图标集之外的 svg（运行中的 Spinner 点了展开钮也照转，Codex #1548）', () => {
    act(() =>
      root.render(
        <button type="button">
          <Spinner data-spin />
          <Bold data-plain />
        </button>,
      ),
    )
    act(() => host.querySelector('button')!.click())
    flushFrame()
    expect(host.querySelector('[data-spin]')!.hasAttribute('data-tap')).toBe(false)
    expect(host.querySelector('[data-plain]')!.hasAttribute('data-tap')).toBe(true)
  })

  it('减少动态效果时不播', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('reduce'), media: q }))
    act(() =>
      root.render(
        <button type="button">
          <Download />
        </button>,
      ),
    )
    act(() => host.querySelector('button')!.click())
    flushFrame()
    expect(host.querySelector('svg')!.hasAttribute('data-play')).toBe(false)
  })
})
