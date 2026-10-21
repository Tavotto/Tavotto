/**
 * 三步原生新手教程（ADR 0136）在 jsdom 里能钉住的部分：什么时候自己出现、教程层的四条边界
 * （inert / 按键不漏 / 外部文件拒收 / 收起不留监听）、每一步只在真实交互成功后完成、
 * 「下一步」不替用户声称完成、完成页的前后对比来自两份真实状态。
 *
 * **这是演示层的测试，不是编辑器的测试**：图例 / 标题改的是 onboardingStore.demo，
 * 这里一条都不拿来证明图内编辑能用（那由编辑器自己的用例与 e2e 负责）。
 * jsdom 没有布局：舞台尺寸、投放框 / 卡片的屏幕框都由用例给假值。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { configureOnboardingPersistence, useOnboardingStore } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { TITLE_SIZE } from '@/lib/onboarding/demoFigure'
import { resetTutorialAutoOpenForTest } from './autoOpen'
import { NativeTutorial } from './NativeTutorial'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/* ------------------------------- jsdom 补丁 ------------------------------- */

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const restore: (() => void)[] = []
function patch<T extends object, K extends keyof T>(obj: T, key: K, value: T[K] | PropertyDescriptor, getter = false) {
  const before = Object.getOwnPropertyDescriptor(obj, key)
  Object.defineProperty(obj, key, getter ? { configurable: true, ...(value as PropertyDescriptor) } : { configurable: true, writable: true, value })
  restore.push(() => {
    if (before) Object.defineProperty(obj, key, before)
    else delete (obj as Record<PropertyKey, unknown>)[key as PropertyKey]
  })
}

/** 投放框与卡片的屏幕框：卡片按自己的 translate 走（拖到哪框就在哪） */
const ZONE = { left: 200, top: 300, right: 800, bottom: 600 }
const CARD = { left: 40, top: 60, width: 300, height: 100 }
function rectOf(el: Element): DOMRect {
  const r = (x: number, y: number, w: number, h: number) =>
    ({ x, y, left: x, top: y, width: w, height: h, right: x + w, bottom: y + h, toJSON: () => ({}) }) as DOMRect
  if (el.matches('[data-tutorial-drop-zone]')) return r(ZONE.left, ZONE.top, ZONE.right - ZONE.left, ZONE.bottom - ZONE.top)
  if (el.matches('[data-tutorial-file-card]')) {
    const m = /translate\(([-\d.]+)px, ([-\d.]+)px\)/.exec((el as HTMLElement).style.transform)
    const dx = m ? Number(m[1]) : 0
    const dy = m ? Number(m[2]) : 0
    return r(CARD.left + dx, CARD.top + dy, CARD.width, CARD.height)
  }
  return r(0, 0, 0, 0)
}

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  localStorage.clear()
  configureOnboardingPersistence(null)
  useOnboardingStore.getState().resetOnboarding()
  useProjectStore.setState({ phase: 'none', recent: [], pinned: [], project: null, listsLoaded: true } as never)
  resetTutorialAutoOpenForTest()
  vi.stubGlobal('ResizeObserver', NoopResizeObserver)
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => setTimeout(() => cb(performance.now()), 0))
  vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id))
  patch(HTMLElement.prototype, 'clientWidth', { get: () => 800 }, true)
  patch(HTMLElement.prototype, 'clientHeight', { get: () => 600 }, true)
  patch(HTMLElement.prototype, 'offsetWidth', { get: () => 800 }, true)
  patch(HTMLElement.prototype, 'offsetHeight', { get: () => 600 }, true)
  patch(Element.prototype, 'getBoundingClientRect', function (this: Element) {
    return rectOf(this)
  })
  patch(Element.prototype, 'setPointerCapture', () => {})
  patch(Element.prototype, 'releasePointerCapture', () => {})
  patch(Element.prototype, 'hasPointerCapture', () => true)
  host = document.createElement('div')
  host.id = 'root'
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  while (restore.length) restore.pop()!()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

async function mount() {
  await act(async () => {
    root.render(<NativeTutorial />)
  })
}

const layer = () => document.querySelector<HTMLElement>('[data-native-tutorial]')
const q = <T extends Element = HTMLElement>(sel: string) => document.querySelector<T>(sel)
const ob = () => useOnboardingStore.getState()
const flush = () => act(async () => new Promise((r) => setTimeout(r, 5)))

/** 第一步放下之后的演示渲染：把时钟拨过它的时长，让 tween 走到终点 */
async function finishRender() {
  const later = performance.now() + 10_000
  const now = vi.spyOn(performance, 'now').mockReturnValue(later)
  await flush()
  await flush()
  now.mockRestore()
}

async function click(el: Element | null) {
  expect(el).not.toBeNull()
  await act(async () => {
    ;(el as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }))
  })
}

async function key(el: Element | null, k: string, init: KeyboardEventInit = {}) {
  expect(el).not.toBeNull()
  await act(async () => {
    el!.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init }))
  })
}

async function pointer(el: Element, type: string, x: number, y: number, id = 1) {
  await act(async () => {
    const ev = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 })
    Object.defineProperty(ev, 'pointerId', { value: id })
    Object.defineProperty(ev, 'isPrimary', { value: true })
    el.dispatchEvent(ev)
  })
}

/** 进教程并走到某一步（前面的步骤按完成记） */
async function openAt(step: 'demo_import' | 'demo_move_legend' | 'demo_change_title' | 'done') {
  await mount()
  await act(async () => {
    ob().openTutorial()
    const order = ['demo_import', 'demo_move_legend', 'demo_change_title', 'done'] as const
    for (const id of order) {
      if (id === step) break
      ob().markStep(id)
      ob().next()
    }
  })
  await flush()
}

/* --------------------------------- 自动出现 -------------------------------- */

describe('什么时候自己出现', () => {
  it('第一次打开（没开始过、主页、没有自己的项目）：欢迎页，只这一次', async () => {
    await mount()
    expect(layer()?.dataset.surface).toBe('welcome')
    expect(q('[data-tutorial-view="welcome"]')).not.toBeNull()
    expect(ob().welcomeSeenAt).toBeTypeOf('number')
    // 收起之后同一会话不再弹；下次启动也不弹（welcomeSeenAt 记下了）
    await click(q('[data-tutorial-open-workspace]'))
    expect(layer()).toBeNull()
    resetTutorialAutoOpenForTest()
    act(() => root.unmount())
    root = createRoot(host)
    await mount()
    expect(layer()).toBeNull()
    expect(ob().status).toBe('not_started')
  })

  it('已经在用的人不打扰：最近列表里有自己的项目，或启动时项目就开着', async () => {
    useProjectStore.setState({ recent: [{ path: '/me/paper', name: 'paper' }] } as never)
    await mount()
    expect(layer()).toBeNull()
    act(() => root.unmount())
    root = createRoot(host)
    resetTutorialAutoOpenForTest()
    useProjectStore.setState({ recent: [], phase: 'open' } as never)
    await mount()
    expect(layer()).toBeNull()
  })

  it('启动时项目列表没取到（后端暂时不可达，列表也是空的）：先不判、不记 welcomeSeenAt；取到了再判', async () => {
    useProjectStore.setState({ listsLoaded: false } as never)
    await mount()
    expect(layer()).toBeNull()
    expect(ob().welcomeSeenAt).toBeNull()
    // 后端回来、列表取到：有自己的项目 → 照样不打扰
    act(() => useProjectStore.setState({ recent: [{ path: '/me/paper', name: 'paper' }], listsLoaded: true } as never))
    await flush()
    expect(layer()).toBeNull()
    expect(ob().welcomeSeenAt).toBeNull()
  })

  it('列表取到之后确实没有自己的项目：这时才出欢迎页', async () => {
    useProjectStore.setState({ listsLoaded: false } as never)
    await mount()
    expect(layer()).toBeNull()
    act(() => useProjectStore.setState({ listsLoaded: true } as never))
    await flush()
    expect(layer()?.dataset.surface).toBe('welcome')
  })

  it('示例项目不算「自己的项目」', async () => {
    useProjectStore.setState({ recent: [{ path: '/data/tutorial/Tutorial', name: 'Tutorial', tutorial: true }] } as never)
    await mount()
    expect(layer()?.dataset.surface).toBe('welcome')
  })

  it('上次关应用时教程正进行到一半：接着那一步；暂停 / 完成 / 跳过的不自己出现', async () => {
    useOnboardingStore.setState({ status: 'active', currentStep: 'demo_change_title', completedSteps: ['demo_import', 'demo_move_legend'] })
    await mount()
    expect(q('[data-tutorial-step]')?.dataset.tutorialStep).toBe('demo_change_title')
    for (const status of ['paused', 'completed', 'skipped'] as const) {
      act(() => root.unmount())
      root = createRoot(host)
      resetTutorialAutoOpenForTest()
      useOnboardingStore.setState({ status, surface: 'none', welcomeSeenAt: null })
      await mount()
      expect(layer(), status).toBeNull()
    }
  })
})

/* -------------------------------- 教程层边界 ------------------------------- */

describe('教程层的边界', () => {
  it('从步骤页打开（不经欢迎页）：焦点进到教程层里，不留在底下 inert 的界面上', async () => {
    const outside = document.createElement('button')
    host.appendChild(outside)
    outside.focus()
    await openAt('demo_import')
    await flush()
    expect(layer()!.contains(document.activeElement)).toBe(true)
  })

  it('开着时应用根 inert；收起后还原', async () => {
    await openAt('demo_import')
    expect(host.hasAttribute('inert')).toBe(true)
    await click(q('[data-tutorial-skip]'))
    expect(layer()).toBeNull()
    expect(host.hasAttribute('inert')).toBe(false)
    expect(ob().status).toBe('skipped')
  })

  it('教程层里的按键不冒泡到 window（工作台快捷键不会在看不见的画布上生效）', async () => {
    await openAt('demo_import')
    const onWindow = vi.fn()
    window.addEventListener('keydown', onWindow)
    await key(q('[data-tutorial-next]'), 'Escape')
    await key(q('[data-tutorial-file-card]'), 'z', { metaKey: true })
    window.removeEventListener('keydown', onWindow)
    expect(onWindow).not.toHaveBeenCalled()
  })

  it('系统拖进来的文件：拒收（dropEffect none），不读文件内容，只说一句', async () => {
    await openAt('demo_import')
    const filesRead = vi.fn(() => [])
    const dt = { types: ['Files'], dropEffect: 'copy' }
    Object.defineProperty(dt, 'files', { get: filesRead })
    const ev = new Event('dragover', { bubbles: true, cancelable: true })
    Object.defineProperty(ev, 'dataTransfer', { value: dt })
    await act(async () => {
      q('[data-tutorial-drop-zone]')!.dispatchEvent(ev)
    })
    expect(ev.defaultPrevented).toBe(true)
    expect(dt.dropEffect).toBe('none')
    expect(filesRead).not.toHaveBeenCalled()
    expect(q('[data-tutorial-external-notice]')!.textContent).toContain('完成后可以导入自己的脚本')
    // 第一步并没有因此完成
    expect(ob().completedSteps).not.toContain('demo_import')
    // 页面内部的拖动（没有 Files）不受影响
    const inner = new Event('dragover', { bubbles: true, cancelable: true })
    Object.defineProperty(inner, 'dataTransfer', { value: { types: ['text/plain'], dropEffect: 'move' } })
    await act(async () => {
      q('[data-tutorial-drop-zone]')!.dispatchEvent(inner)
    })
    expect(inner.defaultPrevented).toBe(false)
  })

  it('拖动中卸载（跳过教程）：拖动期间挂的窗口监听全部摘掉', async () => {
    await openAt('demo_import')
    const add = vi.spyOn(window, 'addEventListener')
    const remove = vi.spyOn(window, 'removeEventListener')
    const card = q('[data-tutorial-file-card]')!
    await pointer(card, 'pointerdown', 100, 100)
    const added = add.mock.calls.filter(([t]) => String(t) === 'blur' || String(t) === 'resize')
    expect(added.map(([t]) => t).sort()).toEqual(['blur', 'resize'])
    await click(q('[data-tutorial-skip]'))
    // 拖动期间挂上的每一个（按函数身份）都被摘掉了
    for (const [type, fn] of added) {
      expect(remove.mock.calls.some(([t, f]) => String(t) === String(type) && f === fn), String(type)).toBe(true)
    }
  })
})

/* ---------------------------------- 第一步 --------------------------------- */

describe('第一步：拖入示例脚本', () => {
  it('没做之前「下一步」不能点；卡片真的落进投放框才完成', async () => {
    await openAt('demo_import')
    expect(q<HTMLButtonElement>('[data-tutorial-next]')!.disabled).toBe(true)
    const card = q('[data-tutorial-file-card]')!
    await pointer(card, 'pointerdown', 100, 100)
    await pointer(card, 'pointermove', 400, 420)
    expect(q('[data-tutorial-drop-zone]')!.hasAttribute('data-over')).toBe(true)
    await pointer(card, 'pointerup', 400, 420)
    // 先演一段「渲染中」：这期间还没完成、「下一步」不亮、图还没出来
    expect(q('[data-tutorial-rendering]')).not.toBeNull()
    expect(q('[data-tutorial-imported]')).not.toBeNull()
    expect(q('[data-demo-figure]')).toBeNull()
    expect(ob().completedSteps).not.toContain('demo_import')
    expect(q<HTMLButtonElement>('[data-tutorial-next]')!.disabled).toBe(true)
    await finishRender()
    expect(q('[data-tutorial-rendering]')).toBeNull()
    expect(ob().completedSteps).toContain('demo_import')
    expect(q<HTMLButtonElement>('[data-tutorial-next]')!.disabled).toBe(false)
    expect(q('[data-tutorial-feedback]')!.dataset.tutorialFeedback).toBe('success')
    expect(q('[data-demo-figure]')).not.toBeNull()
    await click(q('[data-tutorial-next]'))
    expect(ob().currentStep).toBe('demo_move_legend')
  })

  it('放错地方：不完成、卡片回原位、说一句；悬停在框上不算完成', async () => {
    await openAt('demo_import')
    const card = q('[data-tutorial-file-card]')!
    await pointer(card, 'pointerdown', 100, 100)
    await pointer(card, 'pointermove', 400, 420) // 悬停在框上
    expect(ob().completedSteps).not.toContain('demo_import')
    await pointer(card, 'pointermove', 120, 140)
    await pointer(card, 'pointerup', 120, 140)
    expect(ob().completedSteps).not.toContain('demo_import')
    expect(card.hasAttribute('data-dragging')).toBe(false)
    expect(q('[data-tutorial-feedback]')!.textContent).toContain('没有放进投放框')
  })

  it('pointercancel / 窗口失焦：拖动作废，不能变成一次放下', async () => {
    await openAt('demo_import')
    const card = q('[data-tutorial-file-card]')!
    await pointer(card, 'pointerdown', 100, 100)
    await pointer(card, 'pointermove', 400, 420)
    await pointer(card, 'pointercancel', 400, 420)
    expect(card.hasAttribute('data-dragging')).toBe(false)
    await pointer(card, 'pointerup', 400, 420)
    expect(ob().completedSteps).not.toContain('demo_import')
    await pointer(card, 'pointerdown', 100, 100)
    await pointer(card, 'pointermove', 400, 420)
    await act(async () => {
      window.dispatchEvent(new Event('blur'))
    })
    await pointer(card, 'pointerup', 400, 420)
    expect(ob().completedSteps).not.toContain('demo_import')
  })

  it('键盘：空格拿起、方向键移进框、回车放下；Esc 取消', async () => {
    await openAt('demo_import')
    const card = q('[data-tutorial-file-card]')!
    await key(card, ' ')
    expect(card.getAttribute('aria-pressed')).toBe('true')
    await key(card, 'Escape')
    expect(card.getAttribute('aria-pressed')).toBe('false')
    await key(card, ' ')
    for (let i = 0; i < 10; i++) await key(card, 'ArrowDown')
    for (let i = 0; i < 10; i++) await key(card, 'ArrowRight')
    await flush()
    await key(card, 'Enter')
    expect(q('[data-tutorial-rendering]')).not.toBeNull()
    await finishRender()
    expect(ob().completedSteps).toContain('demo_import')
  })

  it('只教拖放：没有「直接使用示例」之类的捷径', async () => {
    await openAt('demo_import')
    expect(q('[data-tutorial-use-sample]')).toBeNull()
    expect(q('[data-tutorial-drop-zone] button')).toBeNull()
  })

  it('渲染途中返回欢迎页：计时停掉，不在背后补记完成（教程仍是进行中）', async () => {
    await openAt('demo_import')
    const card = q('[data-tutorial-file-card]')!
    await pointer(card, 'pointerdown', 100, 100)
    await pointer(card, 'pointermove', 400, 420)
    await pointer(card, 'pointerup', 400, 420)
    expect(q('[data-tutorial-rendering]')).not.toBeNull()
    await click(q('[data-tutorial-back]'))
    expect(layer()?.dataset.surface).toBe('welcome')
    await finishRender()
    expect(ob().status).toBe('active')
    expect(ob().completedSteps).not.toContain('demo_import')
  })

  it('减少动态效果：放下即完成，不演渲染', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('reduce'), addEventListener() {}, removeEventListener() {} }))
    await openAt('demo_import')
    const card = q('[data-tutorial-file-card]')!
    await pointer(card, 'pointerdown', 100, 100)
    await pointer(card, 'pointermove', 400, 420)
    await pointer(card, 'pointerup', 400, 420)
    expect(q('[data-tutorial-rendering]')).toBeNull()
    expect(ob().completedSteps).toContain('demo_import')
  })

  it('第一步「返回」回到欢迎页（教程状态不动）', async () => {
    await openAt('demo_import')
    await click(q('[data-tutorial-back]'))
    expect(layer()?.dataset.surface).toBe('welcome')
    expect(ob().status).toBe('active')
  })
})

/* ---------------------------------- 第二步 --------------------------------- */

describe('第二步：拖动图例（演示层）', () => {
  const legend = () => q<SVGGElement>('[data-demo-legend]')!

  it('键盘拿起、上移、放下：位移按图的 pt 记进演示状态并完成；坐标轴与曲线不动', async () => {
    await openAt('demo_move_legend')
    const linesBefore = [...document.querySelectorAll('polyline')].map((p) => p.getAttribute('points'))
    await key(legend(), ' ')
    for (let i = 0; i < 3; i++) await key(legend(), 'ArrowUp')
    await key(legend(), 'Enter')
    expect(ob().demo.legendOffset).toEqual([0, -12])
    expect(ob().completedSteps).toContain('demo_move_legend')
    expect(legend().getAttribute('transform')).toMatch(/translate\(/)
    expect([...document.querySelectorAll('polyline')].map((p) => p.getAttribute('points'))).toEqual(linesBefore)
  })

  it('挪得太少不算换了位置；Esc 取消回到拿起前', async () => {
    await openAt('demo_move_legend')
    await key(legend(), ' ')
    await key(legend(), 'ArrowUp')
    await key(legend(), 'Enter')
    expect(ob().completedSteps).not.toContain('demo_move_legend')
    expect(q('[data-tutorial-feedback]')!.textContent).toContain('再拖远一点')
    const before = ob().demo.legendOffset
    await key(legend(), ' ')
    for (let i = 0; i < 5; i++) await key(legend(), 'ArrowUp')
    await key(legend(), 'Escape')
    expect(ob().demo.legendOffset).toEqual(before)
    expect(ob().completedSteps).not.toContain('demo_move_legend')
  })

  it('返回上一步再回来：图例位置还在，「下一步」仍可点', async () => {
    await openAt('demo_move_legend')
    await key(legend(), ' ')
    for (let i = 0; i < 4; i++) await key(legend(), 'ArrowUp')
    await key(legend(), 'Enter')
    await click(q('[data-tutorial-back]'))
    expect(ob().currentStep).toBe('demo_import')
    await click(q('[data-tutorial-next]'))
    expect(ob().currentStep).toBe('demo_move_legend')
    expect(ob().demo.legendOffset).toEqual([0, -16])
    expect(q<HTMLButtonElement>('[data-tutorial-next]')!.disabled).toBe(false)
  })
})

/* ---------------------------------- 第三步 --------------------------------- */

describe('第三步：标题字号（演示层）', () => {
  const title = () => q<SVGGElement>('[data-demo-title]')!

  it('只点标题不算完成；点了才出现字号控件；+ 一下（比进来时大）才完成', async () => {
    await openAt('demo_change_title')
    expect(title().getAttribute('font-size')).toBe(String(TITLE_SIZE.initial))
    expect(q('[data-tutorial-font-controls]')).toBeNull()
    await click(title())
    expect(q('[data-tutorial-font-controls]')).not.toBeNull()
    expect(ob().completedSteps).not.toContain('demo_change_title')
    await click(q('[data-tutorial-size-increase]'))
    expect(ob().demo.titleSizePt).toBe(TITLE_SIZE.initial + 1)
    expect(title().getAttribute('font-size')).toBe(String(TITLE_SIZE.initial + 1))
    expect(ob().completedSteps).toContain('demo_change_title')
  })

  it('改小不算；输入无效的字不改字号、不卡住', async () => {
    await openAt('demo_change_title')
    await click(title())
    await click(q('[data-tutorial-size-decrease]'))
    expect(ob().demo.titleSizePt).toBe(TITLE_SIZE.initial - 1)
    expect(ob().completedSteps).not.toContain('demo_change_title')
    const input = q<HTMLInputElement>('[data-tutorial-font-controls] input')!
    await act(async () => {
      input.focus()
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      set.call(input, 'abc')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await key(input, 'Enter')
    expect(ob().demo.titleSizePt).toBe(TITLE_SIZE.initial - 1)
    // 之后照常能完成
    await click(q('[data-tutorial-size-increase]'))
    await click(q('[data-tutorial-size-increase]'))
    expect(ob().completedSteps).toContain('demo_change_title')
  })

  it('到上限后 + 不可点（不会把标题顶出图幅）', async () => {
    await openAt('demo_change_title')
    await click(title())
    for (let i = 0; i < 10; i++) {
      const plus = q<HTMLButtonElement>('[data-tutorial-size-increase]')!
      if (plus.disabled) break
      await click(plus)
    }
    expect(ob().demo.titleSizePt).toBe(TITLE_SIZE.max)
    expect(q<HTMLButtonElement>('[data-tutorial-size-increase]')!.disabled).toBe(true)
  })

  it('带着上限字号进来（旧存档 / 手改）也能完成：进入基线封顶在上限再退一步，不卡死', async () => {
    await openAt('demo_move_legend')
    // 进第三步之前存档里就是上限（迁移之外的旁路 / 旧版本写入）
    await act(async () => {
      ob().setDemo({ titleSizePt: TITLE_SIZE.max })
      ob().markStep('demo_move_legend')
      ob().next()
    })
    await flush()
    await click(title())
    await click(q('[data-tutorial-size-decrease]'))
    await click(q('[data-tutorial-size-increase]'))
    expect(ob().demo.titleSizePt).toBe(TITLE_SIZE.max)
    expect(ob().completedSteps).toContain('demo_change_title')
  })

  it('字号框里按 Esc：收起选中、焦点回到标题（框自己吞掉了冒泡，靠 onEscape）', async () => {
    await openAt('demo_change_title')
    await click(title())
    const input = q<HTMLInputElement>('[data-tutorial-font-controls] input')!
    await act(async () => input.focus())
    expect(document.activeElement).toBe(input)
    await key(input, 'Escape')
    expect(q('[data-tutorial-font-controls]')).toBeNull()
    expect(document.activeElement).toBe(title())
    expect(layer()).not.toBeNull() // 只收起选中，不关教程
  })

  it('图例的改动带到这一步（同一张图）', async () => {
    await openAt('demo_change_title')
    const initial = q('[data-demo-legend]')!.getAttribute('transform')
    await act(async () => ob().setDemo({ legendOffset: [0, -20] }))
    const moved = q('[data-demo-legend]')!.getAttribute('transform')
    expect(moved).not.toBe(initial)
    const y = (tr: string | null) => Number(/translate\(([-\d.]+) ([-\d.]+)\)/.exec(tr ?? '')![2])
    expect(y(moved)).toBeCloseTo(y(initial) - 20, 5)
  })
})

/* ---------------------------------- 完成页 --------------------------------- */

describe('完成页', () => {
  it('前后对比：同一组曲线；不同的只有图例位置与标题字号', async () => {
    await openAt('done')
    await act(async () => ob().setDemo({ legendOffset: [0, -20], titleSizePt: 11 }))
    const before = q('[data-tutorial-compare-panel="before"] [data-demo-figure]')!
    const after = q('[data-tutorial-compare-panel="after"] [data-demo-figure]')!
    const pts = (svg: Element) => [...svg.querySelectorAll('polyline')].map((p) => p.getAttribute('points'))
    expect(pts(before)).toEqual(pts(after))
    expect(before.querySelector('[data-demo-title]')!.getAttribute('font-size')).toBe(String(TITLE_SIZE.initial))
    expect(after.querySelector('[data-demo-title]')!.getAttribute('font-size')).toBe('11')
    expect(before.querySelector('[data-demo-legend]')!.getAttribute('transform')).not.toBe(
      after.querySelector('[data-demo-legend]')!.getAttribute('transform'),
    )
    expect(after.getAttribute('aria-label')).toContain('11 pt')
  })

  it('「开始使用」记完成、收起教程层；「重新体验」回到第一步并清空改动', async () => {
    await openAt('done')
    await act(async () => ob().setDemo({ titleSizePt: 11 }))
    await click(q('[data-tutorial-replay]'))
    expect(ob().currentStep).toBe('demo_import')
    expect(ob().demo.titleSizePt).toBeNull()
    await act(async () => {
      for (const id of ['demo_import', 'demo_move_legend', 'demo_change_title'] as const) {
        ob().markStep(id)
        ob().next()
      }
    })
    await click(q('[data-tutorial-start-using]'))
    expect(ob().status).toBe('completed')
    expect(layer()).toBeNull()
  })
})
