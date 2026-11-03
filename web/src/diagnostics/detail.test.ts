/**
 * 发送包专用的 detail 载荷（ADR 0118 修订 2026-10-13）：console 环、未捕获错误环、显示环境、更新结果、样式改动。
 *
 * 判据的主语：**导出路径与发送路径各自出的载荷**——导出（`buildDiagnosticPayload`）一个 detail 字段都不能有；
 * 发送（`buildSendPayload`）带 detail 与勾选项（默认全 false）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { buildDiagnosticPayload } from './index'
import {
  buildSendPayload,
  fitDetailToBudget,
  SEND_PAYLOAD_BUDGET,
  type SendPayload,
  collectDisplayEnv,
  collectFocusedOverrides,
  overridesOmitted,
  markPlainData,
  CONSOLE_RING_CAPACITY,
  ERROR_RING_CAPACITY,
  installConsoleCapture,
  NO_DETAIL_OPTIONS,
  readConsoleRing,
  readErrorRing,
  __resetDetailRings,
} from './detail'
import { __resetDesktopUpdate, getUpdateOutcome } from '@/lib/desktop'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'

let uninstall: () => void
let origError: typeof console.error
let origWarn: typeof console.warn

beforeEach(() => {
  __resetDetailRings()
  origError = console.error
  origWarn = console.warn
  // 底层 console 先换成静默桩，免得测试输出被刷屏；装捕获时包装的是这个桩
  console.error = vi.fn()
  console.warn = vi.fn()
})
afterEach(() => {
  uninstall?.()
  console.error = origError
  console.warn = origWarn
})

describe('console 环', () => {
  it('记录 console.error / warn 的原文与调用栈，原方法照常被调用', () => {
    const base = console.error as ReturnType<typeof vi.fn>
    uninstall = installConsoleCapture()
    const err = new Error('boom 原文')
    const data = markPlainData({ a: 1 })
    console.error('render failed:', err, data)
    console.warn('careful', 42)
    const ring = readConsoleRing()
    expect(ring).toHaveLength(2)
    expect(ring[0].level).toBe('error')
    expect(ring[0].message).toContain('render failed:')
    expect(ring[0].message).toContain('Error: boom 原文')
    expect(ring[0].message).toContain('{"a":1}')
    expect(ring[0].stack).toContain('Error: boom 原文') // Error 自带的完整栈
    expect(ring[1]).toMatchObject({ level: 'warn', message: 'careful 42' })
    expect(ring[1].stack).toBeTypeOf('string') // 字符串消息记调用点的栈
    expect(base).toHaveBeenCalledWith('render failed:', err, data)
  })

  it('定长环：只留最近 40 条，更早的被挤掉', () => {
    uninstall = installConsoleCapture()
    for (let i = 0; i < CONSOLE_RING_CAPACITY + 15; i++) console.warn(`w${i}`)
    const ring = readConsoleRing()
    expect(ring).toHaveLength(CONSOLE_RING_CAPACITY)
    expect(ring[0].message).toBe('w15')
    expect(ring[ring.length - 1].message).toBe(`w${CONSOLE_RING_CAPACITY + 14}`)
  })

  it('自身出错被吞掉：循环引用 / 抛 getter 的参数不会让 console.error 抛', () => {
    uninstall = installConsoleCapture()
    const circular: Record<string, unknown> = {}
    circular.self = circular
    const evil = {
      get boom(): never {
        throw new Error('getter exploded')
      },
    }
    expect(() => console.error(circular, evil)).not.toThrow()
    expect(readConsoleRing()).toHaveLength(1)
  })

  it('有界序列化：10 万元素数组 / 深嵌套 / 10 万键对象，序列化器触碰的元素、层、键有界，message ≤ MAX_MESSAGE', () => {
    // 判据的主语：序列化器**实际触碰**了多少元素 / 层 / 键（Proxy trap 计数），不是这台机器忙不忙（不判墙钟，L-100）。
    // 上限的由来（detail.ts）：SHOW_MAX_BREADTH=50 / SHOW_MAX_DEPTH=4。每个被写出的键在对象分支上最多触发 3 次
    // getOwnPropertyDescriptor（for...in 的可枚举检查 + hasOwnProperty + 取 desc），数组分支 1 次；故触碰量 ≤ 3×(50+1)，
    // 远小于 n=10 万。已知盲点：对象分支的 `for...in` 会让引擎一次性取 ownKeys（O(n)，引擎内部、只 1 次），
    // 实现并不保证避开它，所以只断言 ownKeys ≤ 1 次，不断言它不是 O(n)。
    uninstall = installConsoleCapture()
    const BREADTH = 50
    const DEPTH = 4
    const touches = { gopd: 0, get: 0, ownKeys: 0 }
    const counted = <T extends object>(target: T, onTouch?: () => void): T =>
      new Proxy(target, {
        get(t, k, r) {
          touches.get++
          onTouch?.()
          return Reflect.get(t, k, r)
        },
        getOwnPropertyDescriptor(t, k) {
          touches.gopd++
          onTouch?.()
          return Reflect.getOwnPropertyDescriptor(t, k)
        },
        ownKeys(t) {
          touches.ownKeys++
          onTouch?.()
          return Reflect.ownKeys(t)
        },
      })
    const reset = () => {
      touches.gopd = touches.get = touches.ownKeys = 0
    }
    const N = 100_000
    const big = markPlainData(counted(Array.from({ length: N }, (_, i) => ({ i, text: 'x'.repeat(50) }))))
    // 深嵌套：每层都是 Proxy，记下被碰过的最深一层
    let deepestTouched = -1
    let deep: Record<string, unknown> = counted({ leaf: 1 }, () => (deepestTouched = Math.max(deepestTouched, 0)))
    const LEVELS = 5000
    for (let i = 1; i <= LEVELS; i++) {
      const level = LEVELS - i // 越靠外 level 越小：最外层为 0
      deep = counted({ child: deep }, () => (deepestTouched = Math.max(deepestTouched, level)))
    }
    const wideTarget: Record<string, number> = {}
    for (let i = 0; i < N; i++) wideTarget[`k${i}`] = i
    const wide = markPlainData(counted(wideTarget))

    console.error(big)
    expect(touches.gopd).toBeLessThanOrEqual(BREADTH + 1) // 元素：只碰前 50 个，不是 10 万
    reset()
    console.warn(markPlainData(deep))
    // 最外层 level 0 → 只允许碰 0..DEPTH-1 这几层；碰到 level ≥ DEPTH 说明深度上限失效
    expect(deepestTouched).toBeLessThan(DEPTH)
    expect(touches.gopd).toBeLessThanOrEqual(3 * DEPTH)
    reset()
    console.error(wide)
    expect(touches.gopd).toBeLessThanOrEqual(3 * (BREADTH + 1)) // 键：只碰前 50 个，不是 10 万
    expect(touches.ownKeys).toBeLessThanOrEqual(1) // 引擎一次性取键，见上方盲点说明
    reset()
    console.error('prefix', big, markPlainData(deep))

    for (const r of readConsoleRing()) expect(r.message.length).toBeLessThanOrEqual(4000)
    const [a, b, c] = readConsoleRing()
    expect(a.message.startsWith('[{"i":0')).toBe(true)
    expect(b.message).toContain('…') // 深度超界写省略号
    expect(c.message).toContain(',…') // 键数超界
  })

  it('幂等安装，卸载后还原且不再记录', () => {
    const first = installConsoleCapture()
    const second = installConsoleCapture()
    expect(second).toBe(first)
    console.warn('once')
    expect(readConsoleRing()).toHaveLength(1)
    first()
    console.warn('after')
    expect(readConsoleRing()).toHaveLength(1)
  })

  it('未捕获错误与 unhandledrejection 进另一个环（全局，不随切项目清）', () => {
    uninstall = installConsoleCapture()
    const boom = new Error('uncaught!')
    window.dispatchEvent(new ErrorEvent('error', { error: boom, message: 'uncaught!' }))
    const rej = new Event('unhandledrejection') as Event & { reason: unknown }
    rej.reason = new Error('rejected!')
    window.dispatchEvent(rej)
    const ring = readErrorRing()
    expect(ring.map((r) => r.level)).toEqual(['error', 'unhandledrejection'])
    expect(ring[0].message).toBe('Error: uncaught!')
    expect(ring[0].stack).toContain('uncaught!')
    expect(ring[1].message).toContain('rejected!')
    expect(readConsoleRing()).toHaveLength(0)
    for (let i = 0; i < ERROR_RING_CAPACITY + 5; i++) {
      window.dispatchEvent(new ErrorEvent('error', { error: new Error(`e${i}`) }))
    }
    expect(readErrorRing()).toHaveLength(ERROR_RING_CAPACITY)
  })
})

describe('显示环境', () => {
  it('取到缩放、窗口、屏幕、语言与主题', () => {
    document.documentElement.setAttribute('data-theme', 'dark')
    const env = collectDisplayEnv()
    expect(env.device_pixel_ratio).toBeTypeOf('number')
    expect(env.window_width).toBe(window.innerWidth)
    expect(env.screen_width).toBe(screen.width)
    expect(env.language).toBeTypeOf('string')
    expect(env.theme).toBe('dark')
    expect(env.resolved_theme).toBe('dark')
    document.documentElement.removeAttribute('data-theme')
    expect(collectDisplayEnv().theme).toBe('system')
  })
})

describe('更新结果', () => {
  it('没发生过应用内更新 = null（引擎侧如实写 unknown）', () => {
    __resetDesktopUpdate()
    expect(getUpdateOutcome()).toBeNull()
  })
})

describe('样式改动', () => {
  it('取正在编辑的面板的 overrides；没有面板就是空', () => {
    expect(collectFocusedOverrides()).toEqual([])
    const doc = useDocumentStore.getState().doc
    const panel = {
      id: 'p1',
      type: 'panel',
      fileId: 'Fig1.pdf',
      fileKind: 'pdf',
      script: 'fig1.py',
      overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'T' }],
    }
    useDocumentStore.setState({ doc: { ...doc, objects: [panel as never] } })
    useUiStore.setState({ elementPanelId: 'p1' })
    try {
      expect(collectFocusedOverrides()).toEqual([
        { panel: 'Fig1.pdf', kind: 'pdf', script: 'fig1.py', overrides: panel.overrides },
      ])
    } finally {
      useUiStore.setState({ elementPanelId: null })
      useDocumentStore.setState({ doc })
    }
  })
})

describe('多选时主选面板排第一（#1012 1）', () => {
  const mk = (id: string) => ({ id, type: 'panel', fileId: `${id}.pdf`, fileKind: 'pdf', script: null, overrides: [] })

  it('主选（最后选中的）不是文档里第一张时，overrides[0] 是主选，其余按文档顺序', () => {
    const doc = useDocumentStore.getState().doc
    useDocumentStore.setState({ doc: { ...doc, objects: [mk('p1'), mk('p2'), mk('p3'), mk('p4')] as never[] } })
    useUiStore.setState({ elementPanelId: null })
    useSelectionStore.setState({ ids: ['p1', 'p4', 'p3'] }) // p3 是最后选中的 = 主选
    try {
      expect(collectFocusedOverrides().map((p) => p.panel)).toEqual(['p3.pdf', 'p1.pdf', 'p4.pdf'])
      useSelectionStore.setState({ ids: ['p1', 'p2', 'p3', 'p4'] })
      expect(collectFocusedOverrides().map((p) => p.panel)).toEqual(['p4.pdf', 'p1.pdf', 'p2.pdf'])
      expect(overridesOmitted()).toBe(1)
    } finally {
      useSelectionStore.setState({ ids: [] })
      useDocumentStore.setState({ doc })
    }
  })
})

describe('两条路径的载荷', () => {
  it('导出路径的载荷没有 detail / detail_options', () => {
    const p = buildDiagnosticPayload() as unknown as Record<string, unknown>
    expect(Object.keys(p).sort()).toEqual(['frontend_state', 'interaction_trace'])
  })

  it('detail 带 overrides_omitted（数量上限之外没发的面板数）', async () => {
    const p = await buildSendPayload(NO_DETAIL_OPTIONS)
    expect(p.detail.overrides_omitted).toBe(0)
  })

  it('发送路径 = 老载荷 + detail_options（默认全 false）+ detail', async () => {
    uninstall = installConsoleCapture()
    console.error('for the maintainers')
    const p = await buildSendPayload(NO_DETAIL_OPTIONS)
    expect(p.detail_options).toEqual({ source: false, crash_reports: false, corrupt_sessions: false })
    expect(Object.keys(p.detail).sort()).toEqual(['console', 'display', 'errors', 'overrides', 'overrides_omitted', 'shell'])
    expect((p.detail.console as { message: string }[])[0].message).toBe('for the maintainers')
    expect(p.frontend_state).toBeTruthy()
    const on = await buildSendPayload({ ...NO_DETAIL_OPTIONS, source: true })
    expect(on.detail_options.source).toBe(true)
  })
})

describe('有界序列化不触发 getter', () => {
  it('带副作用 getter 的对象 / 数组被 console.error 时 getter 调用 0 次，写成 [getter]', () => {
    uninstall = installConsoleCapture()
    let calls = 0
    const obj = {
      plain: 1,
      get boom() {
        calls++
        return 'x'
      },
      nested: {
        get deep() {
          calls++
          return 1
        },
      },
    }
    const arr: unknown[] = [1]
    Object.defineProperty(arr, 1, {
      enumerable: true,
      get() {
        calls++
        return 2
      },
    })
    console.error(markPlainData(obj), markPlainData(arr))
    expect(calls).toBe(0)
    const msg = readConsoleRing()[0].message
    expect(msg).toContain('"plain":1')
    expect(msg).toContain('"boom":[getter]')
    expect(msg).toContain('"deep":[getter]')
    expect(msg).toContain('[1,[getter]]')
  })
})

describe('Proxy 实参不被反射遍历（#1012 2）', () => {
  /** 记录每一次 trap 调用（含 getPrototypeOf / has / ownKeys / get / getOwnPropertyDescriptor……）。 */
  const countingProxy = (target: object) => {
    const traps: string[] = []
    const handler = new Proxy(
      {},
      {
        get: (_t, name) => (...args: unknown[]) => {
          traps.push(String(name))
          return (Reflect as never as Record<string, (...a: unknown[]) => unknown>)[name as string](...args)
        },
      },
    )
    return { proxy: new Proxy(target, handler as ProxyHandler<object>), traps }
  }

  it('Proxy 对象 / 数组 / 无原型对象被 console.error 时 trap 调用 0 次，写 [object]', () => {
    uninstall = installConsoleCapture()
    const cases = [countingProxy({ a: 1 }), countingProxy([1, 2]), countingProxy(Object.create(null))]
    for (const c of cases) console.error('x', c.proxy)
    for (const c of cases) {
      // 老 WebView 没有 Error.isError 时退回 instanceof，会有一次 getPrototypeOf；其余 trap 任何时候都不许有
      const allowed = typeof (Error as unknown as { isError?: unknown }).isError === 'function' ? [] : ['getPrototypeOf']
      expect(c.traps.filter((t) => !allowed.includes(t))).toEqual([])
      expect(c.traps.length).toBe(allowed.length ? c.traps.length : 0)
    }
    expect(readConsoleRing().map((r) => r.message)).toEqual(['x [object]', 'x [object]', 'x [object]'])
  })

  it('原始值与 Error 照常记；未声明为纯数据的普通对象写 [object]', () => {
    uninstall = installConsoleCapture()
    console.error('n', 1, true, null, new Error('e1'), { a: 1 })
    expect(readConsoleRing()[0].message).toBe('n 1 true null Error: e1 [object]')
  })
})

describe('Error 子类的 accessor 不被触发', () => {
  const makeEvil = () => {
    const calls = { name: 0, message: 0, stack: 0 }
    class Evil extends Error {
      get message(): string {
        calls.message++
        return 'evil-message'
      }
      get name(): string {
        calls.name++
        return 'EvilError'
      }
    }
    const e = new Evil('x')
    Object.defineProperty(e, 'stack', {
      get() {
        calls.stack++
        return 'evil-stack'
      },
    })
    return { e, calls }
  }

  it('console.error / 未捕获错误 / unhandledrejection：name、message、stack 的 getter 调用 0 次', () => {
    uninstall = installConsoleCapture()
    const a = makeEvil()
    console.error('boom', a.e)
    const b = makeEvil()
    window.dispatchEvent(new ErrorEvent('error', { error: b.e, message: 'm' }))
    const c = makeEvil()
    const rej = new Event('unhandledrejection') as Event & { reason: unknown }
    rej.reason = c.e
    window.dispatchEvent(rej)
    for (const { calls } of [a, b, c]) expect(calls).toEqual({ name: 0, message: 0, stack: 0 })
    expect(readConsoleRing()[0].message).toContain('[getter]')
    expect(readErrorRing().every((r) => r.message.includes('[getter]') || r.message === '')).toBe(true)
  })

  it('bound / Proxy 包装的用户 getter 也不被调用（身份白名单，不靠 toString 判）', () => {
    uninstall = installConsoleCapture()
    let calls = 0
    const userGetter = function (this: unknown) {
      calls++
      return 'evil-stack'
    }
    const bound = userGetter.bind(null) // toString 是 [native code]
    const proxied = new Proxy(userGetter, {})
    for (const get of [bound, proxied, userGetter]) {
      const e = new Error('x')
      Object.defineProperty(e, 'stack', { get, configurable: true })
      console.error('boom', e)
    }
    expect(calls).toBe(0)
    expect(readConsoleRing().every((r) => r.stack === null || !r.stack.includes('evil-stack'))).toBe(true)
  })

  it('普通 Error 仍取到名字、消息与栈', () => {
    uninstall = installConsoleCapture()
    class Custom extends Error {
      constructor(m: string) {
        super(m)
        this.name = 'CustomError'
      }
    }
    console.error(new Custom('plain'))
    const r = readConsoleRing()[0]
    expect(r.message).toBe('CustomError: plain')
    expect(r.stack).toContain('plain')
  })
})

describe('载荷预算', () => {
  const rec = (n: number) => ({ t: n, level: 'error', message: 'm'.repeat(4000), stack: 's'.repeat(8000) })
  const worst = (): SendPayload =>
    ({
      frontend_state: {},
      interaction_trace: [],
      detail_options: NO_DETAIL_OPTIONS,
      detail: {
        console: Array.from({ length: 40 }, (_, i) => rec(i)),
        errors: Array.from({ length: 30 }, (_, i) => rec(i)),
        display: {},
        shell: {},
        overrides: Array.from({ length: 3 }, () => ({
          panel: 'p',
          overrides: Array.from({ length: 400 }, (_, i) => ({ gid: `g${i}`, prop: 'text', value: 'v'.repeat(900) })),
        })),
      },
    }) as unknown as SendPayload
  const size = (p: unknown) => new TextEncoder().encode(JSON.stringify(p)).length

  it('最坏情形的载荷被截进预算，截过什么记进 client_truncated', () => {
    const p = worst()
    expect(size(p)).toBeGreaterThan(SEND_PAYLOAD_BUDGET)
    const fitted = fitDetailToBudget(p)
    expect(size(fitted)).toBeLessThanOrEqual(SEND_PAYLOAD_BUDGET)
    expect((fitted.detail.client_truncated as string[]).length).toBeGreaterThan(0)
    expect(SEND_PAYLOAD_BUDGET).toBeLessThan(1024 * 1024) // 引擎的整份丢弃上限
  })

  it('fit 是纯函数：全局环里的记录与 overrides 数组不会被原地截短', () => {
    uninstall = installConsoleCapture()
    for (let i = 0; i < 40; i++) {
      console.error('m'.repeat(4000), Object.assign(new Error('e'), { stack: 's'.repeat(8000) }))
    }
    const before = JSON.stringify([readConsoleRing(), readErrorRing()])
    const overrides = Array.from({ length: 400 }, (_, i) => ({ gid: `g${i}`, prop: 'text', value: 'v'.repeat(900) }))
    const p = worst()
    ;(p.detail as Record<string, unknown>).console = readConsoleRing()
    ;(p.detail as Record<string, unknown>).errors = readErrorRing()
    ;(p.detail as Record<string, unknown>).overrides = Array.from({ length: 3 }, () => ({ panel: 'p', overrides }))
    const fitted = fitDetailToBudget(p)
    expect((fitted.detail.client_truncated as string[]).length).toBeGreaterThan(0)
    expect(JSON.stringify([readConsoleRing(), readErrorRing()])).toBe(before)
    expect(overrides).toHaveLength(400)
    expect((p.detail.overrides as { overrides: unknown[] }[])[0].overrides).toHaveLength(400)
    expect(p.detail.client_truncated).toBeUndefined() // 入参也不被改
  })

  it('没超预算时原样、不写 client_truncated', () => {
    const p = worst()
    ;(p.detail as { console: unknown[]; errors: unknown[]; overrides: unknown[] }).console = []
    ;(p.detail as { errors: unknown[] }).errors = []
    ;(p.detail as { overrides: unknown[] }).overrides = []
    expect(fitDetailToBudget(p).detail.client_truncated).toBeUndefined()
  })
})
