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
  collectDisplayEnv,
  collectFocusedOverrides,
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
    console.error('render failed:', err, { a: 1 })
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
    expect(base).toHaveBeenCalledWith('render failed:', err, { a: 1 })
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

describe('两条路径的载荷', () => {
  it('导出路径的载荷没有 detail / detail_options', () => {
    const p = buildDiagnosticPayload() as unknown as Record<string, unknown>
    expect(Object.keys(p).sort()).toEqual(['frontend_state', 'interaction_trace'])
  })

  it('发送路径 = 老载荷 + detail_options（默认全 false）+ detail', async () => {
    uninstall = installConsoleCapture()
    console.error('for the maintainers')
    const p = await buildSendPayload(NO_DETAIL_OPTIONS)
    expect(p.detail_options).toEqual({ source: false, crash_reports: false, corrupt_sessions: false })
    expect(Object.keys(p.detail).sort()).toEqual(['console', 'display', 'errors', 'overrides', 'shell'])
    expect((p.detail.console as { message: string }[])[0].message).toBe('for the maintainers')
    expect(p.frontend_state).toBeTruthy()
    const on = await buildSendPayload({ ...NO_DETAIL_OPTIONS, source: true })
    expect(on.detail_options.source).toBe(true)
  })
})
