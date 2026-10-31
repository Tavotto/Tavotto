/**
 * 崩溃页（2026-10-07 设计审计 §10.2）：此前没有主按钮、「从空白开始」与「重新加载」同权重。
 * 现在一颗主按钮「重新加载」，「打开空白排版」在左边、危险浅底胶囊；报错原文收在「详情」里、能复制。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { readDiagnosticTrace } from '@/diagnostics'
import { __resetDiagnosticsForTests } from '@/diagnostics/store'
import { __resetDetailRings, readErrorRing } from '@/diagnostics/detail'
import { useDiagSendStore } from '@/store/diagSendStore'

// 崩溃页自己渲染的发送对话框：这里换成可控的替身（真对话框有自己的测试）
const dialogMock = vi.hoisted(() => ({ throws: false }))
vi.mock('@/components/settings/DiagnosticsSendDialog', () => ({
  DiagnosticsSendDialog: ({ open }: { open: boolean }) => {
    if (dialogMock.throws) throw new Error('dialog boom')
    return open ? <div data-fake-send-dialog /> : null
  },
}))

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

function Boom(): never {
  throw new Error('kaboom')
}

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  dialogMock.throws = false
  useDiagSendStore.setState({ capability: null, open: false })
  __resetDiagnosticsForTests()
  __resetDetailRings()
  vi.spyOn(console, 'error').mockImplementation(() => {})
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.restoreAllMocks()
})

const BASELINE_SHAPE = 'div div h1 p details summary svg g path div pre button div button span button'

describe('ErrorBoundary 崩溃页', () => {
  it('一颗主按钮「重新加载」；「打开空白排版」是左边的危险浅底胶囊；原文在折叠的详情里、可复制', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    act(() =>
      root.render(
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>,
      ),
    )
    const screen = document.querySelector('[data-crash-screen]')!
    expect(screen).not.toBeNull()
    const primaries = screen.querySelectorAll('[data-variant="primary"]')
    expect(primaries).toHaveLength(1)
    expect(primaries[0].hasAttribute('data-crash-reload')).toBe(true)
    expect(screen.querySelector('[data-crash-blank]')!.getAttribute('data-variant')).toBe('danger-tinted')
    const details = screen.querySelector('details[data-crash-details]') as HTMLDetailsElement
    expect(details.open).toBe(false)
    expect(details.textContent).toContain('kaboom')
    await act(async () => (screen.querySelector('[data-crash-copy]') as HTMLButtonElement).click())
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('kaboom'))
  })
})

describe('崩溃页也是窗框的一部分（Codex #994）', () => {
  const w = window as unknown as Record<string, unknown>
  afterEach(() => {
    delete w.__TAURI_INTERNALS__
    delete w.__TAVOTTO_WINDOW_CHROME__
  })
  const crashRoot = () => document.querySelector('[data-crash-screen]')!.parentElement!

  it('macOS（标题栏叠在页面上）：崩溃页取代了整棵树，空白处是拖动区，窗口仍拖得动', () => {
    w.__TAURI_INTERNALS__ = {}
    w.__TAVOTTO_WINDOW_CHROME__ = 'overlay'
    act(() => root.render(<ErrorBoundary><Boom /></ErrorBoundary>))
    expect(crashRoot().getAttribute('data-tauri-drag-region')).toBe('')
  })

  it('浏览器里不挂拖动区', () => {
    act(() => root.render(<ErrorBoundary><Boom /></ErrorBoundary>))
    expect(crashRoot().hasAttribute('data-tauri-drag-region')).toBe(false)
  })
})

describe('崩溃页的「发送问题反馈」入口（PR A）', () => {
  const crash = () =>
    act(() =>
      root.render(
        <ErrorBoundary diagnostics>
          <Boom />
        </ErrorBoundary>,
      ),
    )
  const shape = () => Array.from(host.querySelectorAll('*')).map((e) => e.tagName.toLowerCase())

  it('关着：没有入口、没有对话框，元素序列与改动前逐元素一致', () => {
    crash()
    expect(host.querySelector('[data-send-report]')).toBeNull()
    expect(host.querySelector('[data-fake-send-dialog]')).toBeNull()
    expect(shape().join(' ')).toBe(BASELINE_SHAPE)
  })

  it('开着：入口在折叠的「详情」里、是次要钮；主按钮仍只有「重新加载」那一颗', () => {
    useDiagSendStore.setState({ capability: { enabled: true } })
    crash()
    const screen = host.querySelector('[data-crash-screen]')!
    const btn = screen.querySelector<HTMLElement>('[data-send-report]')!
    expect(btn).not.toBeNull()
    expect(btn.closest('details[data-crash-details]')).not.toBeNull()
    expect(btn.getAttribute('data-variant')).toBe('secondary')
    expect(screen.querySelectorAll('[data-variant="primary"]')).toHaveLength(1)
    expect(host.querySelector('[data-fake-send-dialog]')).toBeNull()
  })

  it('点入口只 setOpen(true)，崩溃页自己的对话框随之打开', () => {
    useDiagSendStore.setState({ capability: { enabled: true } })
    crash()
    act(() => host.querySelector<HTMLElement>('[data-send-report]')!.click())
    expect(useDiagSendStore.getState().open).toBe(true)
    expect(host.querySelector('[data-fake-send-dialog]')).not.toBeNull()
    expect(host.querySelector('[data-crash-screen]')).not.toBeNull()
  })

  it('对话框自己抛错：退回空，崩溃页还在，不二次崩', () => {
    useDiagSendStore.setState({ capability: { enabled: true }, open: true })
    dialogMock.throws = true
    crash()
    expect(host.querySelector('[data-crash-screen]')).not.toBeNull()
    expect(host.querySelector('[data-crash-reload]')).not.toBeNull()
    expect(host.querySelector('[data-fake-send-dialog]')).toBeNull()
  })

  it('首次渲染就崩（App 的 effect 没机会取能力说明）：崩溃页自己取一次，能力开着就出现发送入口', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ enabled: true }), headers: new Headers() })),
    )
    try {
      expect(useDiagSendStore.getState().capability).toBeNull()
      crash()
      await act(async () => {})
      expect(useDiagSendStore.getState().capability).toMatchObject({ enabled: true })
      expect(host.querySelector('[data-send-report]')).not.toBeNull()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('componentDidCatch：trace 记 source=boundary（无原文）；原文与 componentStack 进未捕获错误环', () => {
    crash()
    const ev = readDiagnosticTrace().find((e) => e.type === 'ui.unexpected_error')!
    expect(ev).toMatchObject({ source: 'boundary', error_name: 'Error' })
    expect(JSON.stringify(readDiagnosticTrace())).not.toContain('kaboom')
    const ring = readErrorRing()
    expect(ring).toHaveLength(1)
    expect(ring[0].level).toBe('boundary')
    expect(ring[0].message).toContain('kaboom')
    expect(ring[0].stack).toContain('componentStack:')
  })
})

describe('playground / mcp 形态（不传 diagnostics）：没有 Flask 诊断 API，不发这个请求', () => {
  const crashPlain = () => act(() => root.render(<ErrorBoundary><Boom /></ErrorBoundary>))

  it('崩溃时不取 /api/diagnostics/send 的能力说明，崩溃页与接入发送功能之前逐元素一致', async () => {
    const fetchSpy = vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ enabled: true }), headers: new Headers() }),
    )
    vi.stubGlobal('fetch', fetchSpy)
    const loadSpy = vi.spyOn(useDiagSendStore.getState(), 'load')
    try {
      crashPlain()
      await act(async () => {})
      expect(fetchSpy).not.toHaveBeenCalled()
      expect(loadSpy).not.toHaveBeenCalled()
      expect(useDiagSendStore.getState().capability).toBeNull()
      expect(Array.from(host.querySelectorAll('*')).map((e) => e.tagName.toLowerCase()).join(' ')).toBe(BASELINE_SHAPE)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('即便 store 里碰巧有能力说明，也不渲染入口与对话框', () => {
    useDiagSendStore.setState({ capability: { enabled: true }, open: true })
    crashPlain()
    expect(host.querySelector('[data-crash-screen]')).not.toBeNull()
    expect(host.querySelector('[data-send-report]')).toBeNull()
    expect(host.querySelector('[data-fake-send-dialog]')).toBeNull()
  })

  it('主应用形态（diagnostics）仍会取一次', async () => {
    const fetchSpy = vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ enabled: true }), headers: new Headers() }),
    )
    vi.stubGlobal('fetch', fetchSpy)
    try {
      act(() => root.render(<ErrorBoundary diagnostics><Boom /></ErrorBoundary>))
      await act(async () => {})
      expect(fetchSpy).toHaveBeenCalled()
      expect(String((fetchSpy.mock.calls[0] as unknown[])[0])).toContain('/api/diagnostics/send')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
