/**
 * 「发送问题反馈」对话框（ADR 0118）：逐次确认、先看再发、取消 / 关窗不再继续。
 *
 * 判据的主语：**哪些本机 API 被调用了**——`startDiagSend` 是唯一会让引擎去连诊断服务的调用，所以
 * 「未确认 / 关窗 / 取消 → 零远程请求」在前端这一侧就是「`startDiagSend` 调用次数为 0」；备包、查看、
 * 取消、丢弃都是本机调用，不出网。引擎侧「零 init」另在 `tests/test_diag_send.py` 用对端那一侧的请求记录钉死。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  prepareDiagSend: vi.fn(),
  startDiagSend: vi.fn(),
  fetchDiagSendStatus: vi.fn(),
  cancelDiagSend: vi.fn(),
  discardDiagSend: vi.fn(),
  fetchDiagSendBundle: vi.fn(),
  fetchDiagSendCapability: vi.fn(),
}))
vi.mock('./diagnosticsSave', () => ({ saveDiagnosticsZip: vi.fn(), stampForFilename: () => '20260101-000000' }))

import {
  cancelDiagSend,
  discardDiagSend,
  fetchDiagSendBundle,
  fetchDiagSendStatus,
  prepareDiagSend,
  startDiagSend,
  type DiagSendCapability,
  type DiagSendPrepared,
  type DiagSendStatus,
} from '@/lib/api'
import { t } from '@/i18n'
import enDialogs from '@/i18n/locales/en-US/dialogs.json'
import zhDialogs from '@/i18n/locales/zh-CN/dialogs.json'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { DiagnosticsSendDialog } from './DiagnosticsSendDialog'
import { saveDiagnosticsZip } from './diagnosticsSave'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
Element.prototype.hasPointerCapture ??= () => false
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as never

const prepareMock = vi.mocked(prepareDiagSend)
const startMock = vi.mocked(startDiagSend)
const statusMock = vi.mocked(fetchDiagSendStatus)
const cancelMock = vi.mocked(cancelDiagSend)
const discardMock = vi.mocked(discardDiagSend)
const bundleMock = vi.mocked(fetchDiagSendBundle)
const ds = (key: string, v?: Record<string, unknown>) =>
  t(`settings.diagnostics.send.${key}`, { ns: 'dialogs', ...(v ?? {}) })

const CAP: DiagSendCapability = {
  enabled: true,
  max_bytes: 10 * 1024 * 1024,
  note_max_chars: 1000,
  retention_days: 30,
  categories: [
    'render_failure',
    'export_failure',
    'environment',
    'editing',
    'install_update',
    'performance',
    'crash',
    'other',
  ],
}
const PREPARED: DiagSendPrepared = {
  id: 'pid-1',
  size: 20480,
  sha256: 'a'.repeat(64),
  schema: 6,
  max_bytes: CAP.max_bytes!,
  too_large: false,
  entries: [
    { name: 'report.json', kind: 'environment' },
    { name: 'app.log', kind: 'log' },
    { name: 'manifest.json', kind: 'manifest' },
  ],
}
const status = (over: Partial<DiagSendStatus>): DiagSendStatus => ({
  id: 'pid-1',
  state: 'sending',
  stage: 'init',
  size: PREPARED.size,
  sha256: PREPARED.sha256,
  sent: 0,
  total: 0,
  ...over,
})

let host: HTMLDivElement
let root: Root
let closed: boolean[]

async function mount(prepared: DiagSendPrepared = PREPARED) {
  prepareMock.mockResolvedValue(prepared)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  closed = []
  await act(async () => {
    root.render(
      <TooltipProvider>
        <DiagnosticsSendDialog open onOpenChange={(v) => closed.push(v)} capability={CAP} />
      </TooltipProvider>,
    )
  })
  await act(async () => {})
}

const q = <T extends Element = HTMLElement>(sel: string) => document.body.querySelector<T>(sel)
const click = async (el: Element | null) => {
  expect(el).not.toBeNull()
  await act(async () => {
    ;(el as HTMLElement).click()
  })
}
const tick = async (ms = 400) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  for (const m of [prepareMock, startMock, statusMock, cancelMock, discardMock, bundleMock]) m.mockReset()
  discardMock.mockResolvedValue({ ok: true })
  cancelMock.mockResolvedValue(status({ state: 'cancelling' }))
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.useRealTimers()
})

describe('未确认 → 零远程请求', () => {
  it('打开只备包（本机），没有点发送就没有 startDiagSend', async () => {
    await mount()
    expect(prepareMock).toHaveBeenCalledTimes(1)
    expect(startMock).not.toHaveBeenCalled()
    expect(q('[data-diag-send-confirm]')).not.toBeNull()
  })

  it('点「取消」关窗：丢弃备好的包，startDiagSend 为 0 次', async () => {
    await mount()
    await click(q('[data-diag-send-dismiss]'))
    expect(closed).toEqual([false])
    await act(async () => root.unmount())
    expect(discardMock).toHaveBeenCalledWith('pid-1')
    expect(startMock).not.toHaveBeenCalled()
    root = createRoot(host) // afterEach 再 unmount 一次也无害
  })

  it('写了说明、选了类型但没点发送就关窗：什么都没发', async () => {
    await mount()
    const note = q<HTMLTextAreaElement>('[data-diag-send-note]')!
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
      setter.call(note, 'something')
      note.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => root.unmount())
    expect(startMock).not.toHaveBeenCalled()
    expect(discardMock).toHaveBeenCalled()
    root = createRoot(host)
  })

  it('备包还没回来就关窗：晚到的包也被丢弃，不留在引擎里', async () => {
    let resolve!: (p: DiagSendPrepared) => void
    prepareMock.mockReturnValue(new Promise((r) => (resolve = r)))
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(
        <TooltipProvider>
          <DiagnosticsSendDialog open onOpenChange={() => {}} capability={CAP} />
        </TooltipProvider>,
      )
    })
    await act(async () => root.unmount())
    await act(async () => resolve(PREPARED))
    expect(discardMock).toHaveBeenCalledWith('pid-1')
    expect(startMock).not.toHaveBeenCalled()
    root = createRoot(host)
  })
})

describe('先看再发', () => {
  it('列出将发送的内容类别、大小、保留期，并提示说明里别写隐私', async () => {
    await mount()
    const kinds = Array.from(document.body.querySelectorAll('[data-diag-kind]')).map((e) => e.getAttribute('data-diag-kind'))
    expect(kinds).toEqual(['environment', 'log', 'manifest'])
    expect(document.body.textContent).toContain(ds('size', { size: '20 KiB' }))
    expect(document.body.textContent).toContain(ds('retention', { days: 30 }))
    expect(document.body.textContent).toContain(ds('noteHint'))
    expect(document.body.textContent).toContain(ds('notIncluded'))
    expect(q<HTMLTextAreaElement>('[data-diag-send-note]')!.maxLength).toBe(1000)
  })

  it('「保存这份诊断包」取的是备好的那一份（按 id），不是重新生成', async () => {
    bundleMock.mockResolvedValue(new Blob(['zip']))
    await mount()
    await click(q('[data-diag-send-save]'))
    expect(bundleMock).toHaveBeenCalledWith('pid-1')
    expect(vi.mocked(saveDiagnosticsZip)).toHaveBeenCalledTimes(1)
    expect(prepareMock).toHaveBeenCalledTimes(1)
  })

  it('包太大：不给发送按钮、如实说明、不截取', async () => {
    await mount({ ...PREPARED, id: null, too_large: true, size: 11 * 1024 * 1024 })
    expect(q('[data-diag-send-confirm]')).toBeNull()
    expect(document.body.textContent).toContain(ds('tooLarge', { size: '11.0 MiB', max: '10.0 MiB' }))
    expect(startMock).not.toHaveBeenCalled()
  })

  it('备包失败：给出路，不能发', async () => {
    prepareMock.mockRejectedValue(new Error('boom'))
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(
        <TooltipProvider>
          <DiagnosticsSendDialog open onOpenChange={() => {}} capability={CAP} />
        </TooltipProvider>,
      )
    })
    await act(async () => {})
    expect(document.body.textContent).toContain(ds('prepareFailed'))
    expect(q('[data-diag-send-confirm]')).toBeNull()
  })
})

describe('发送', () => {
  it('点发送 → startDiagSend 带 category 与 note；双击只发一次；状态区报阶段与进度', async () => {
    startMock.mockResolvedValue(status({}))
    statusMock.mockResolvedValue(status({ stage: 'upload', sent: 50, total: 100 }))
    await mount()
    const btn = q('[data-diag-send-confirm]')
    await click(btn)
    await click(btn)
    expect(startMock).toHaveBeenCalledTimes(1)
    expect(startMock).toHaveBeenCalledWith('pid-1', { category: 'other', note: '' })
    await tick()
    expect(q('[data-diag-send-status]')!.textContent).toContain(ds('progress.upload', { percent: 50 }))
  })

  it('成功：报告编号可复制，主按钮变「完成」', async () => {
    startMock.mockResolvedValue(status({}))
    statusMock.mockResolvedValue(status({ state: 'done', stage: null, report_id: 'TVD-ABCD-EFGH-JKMN-PQRS' }))
    await mount()
    await click(q('[data-diag-send-confirm]'))
    await tick()
    expect(q('[data-diag-report-id]')!.textContent).toBe('TVD-ABCD-EFGH-JKMN-PQRS')
    expect(q('[data-diag-send-close]')!.textContent).toBe(ds('done'))
    expect(q('[data-diag-send-confirm]')).toBeNull()
  })

  it('可重试的失败：说人话 + 退避时间 + 保存退路，主按钮变「重试」且再点会再发一次', async () => {
    startMock.mockResolvedValue(status({}))
    statusMock.mockResolvedValue(
      status({ state: 'failed', stage: null, code: 'rate_limited', retryable: true, retry_after: 600 }),
    )
    await mount()
    await click(q('[data-diag-send-confirm]'))
    await tick()
    const body = q('[data-diag-send-status]')!.textContent
    expect(body).toContain(ds('failure.rate_limited'))
    expect(body).toContain(ds('retryInMinutes', { count: 10 }))
    expect(body).toContain(ds('failureHint'))
    expect(q('[data-diag-send-confirm]')!.textContent).toBe(ds('retry'))
    startMock.mockResolvedValue(status({}))
    await click(q('[data-diag-send-confirm]'))
    expect(startMock).toHaveBeenCalledTimes(2)
  })

  it('不可重试的失败：只剩「关闭」，不给重试', async () => {
    startMock.mockResolvedValue(status({}))
    statusMock.mockResolvedValue(status({ state: 'failed', stage: null, code: 'too_large', retryable: false }))
    await mount()
    await click(q('[data-diag-send-confirm]'))
    await tick()
    expect(q('[data-diag-send-confirm]')).toBeNull()
    expect(q('[data-diag-send-close]')).not.toBeNull()
  })

  it('引擎比界面新：不认识的失败码按「意外结果」说，不空白', async () => {
    startMock.mockResolvedValue(status({}))
    statusMock.mockResolvedValue(status({ state: 'failed', stage: null, code: 'from_the_future', retryable: true }))
    await mount()
    await click(q('[data-diag-send-confirm]'))
    await tick()
    expect(q('[data-diag-send-status]')!.textContent).toContain(ds('failure.unexpected_response'))
  })

  it('发送中取消：调 cancelDiagSend，回到可编辑的表单，并说明什么都没发', async () => {
    startMock.mockResolvedValue(status({}))
    statusMock.mockResolvedValueOnce(status({ stage: 'upload', sent: 1, total: 100 }))
    await mount()
    await click(q('[data-diag-send-confirm]'))
    await click(q('[data-diag-send-cancel]'))
    expect(cancelMock).toHaveBeenCalledWith('pid-1')
    statusMock.mockResolvedValue(status({ state: 'cancelled', stage: null }))
    await tick()
    await tick()
    expect(q('[data-diag-send-status]')!.textContent).toContain(ds('cancelled'))
    expect(q('[data-diag-send-confirm]')).not.toBeNull()
    expect(startMock).toHaveBeenCalledTimes(1)
  })

  it('发送中卸载（关窗）：丢弃 = 引擎侧取消，之后不再轮询', async () => {
    startMock.mockResolvedValue(status({}))
    statusMock.mockResolvedValue(status({ stage: 'upload', sent: 1, total: 100 }))
    await mount()
    await click(q('[data-diag-send-confirm]'))
    await act(async () => root.unmount())
    const calls = statusMock.mock.calls.length
    await tick(2000)
    expect(statusMock.mock.calls.length).toBe(calls)
    expect(discardMock).toHaveBeenCalledWith('pid-1')
    root = createRoot(host)
  })
})

describe('文案与引擎同源', () => {
  const py = readFileSync(resolve(__dirname, '../../../../src/tavotto/engine/diagsend.py'), 'utf-8')

  it('每个引擎失败码都有中英文译文（FAILURES ↔ settings.diagnostics.send.failure）', () => {
    const block = py.slice(py.indexOf('FAILURES: dict[str, bool] = {'), py.indexOf('LOG_RESULTS'))
    const codes = [...block.matchAll(/^\s+"([a-z_]+)": (?:True|False),/gm)].map((m) => m[1])
    expect(codes.length).toBeGreaterThanOrEqual(20)
    for (const [lang, d] of [
      ['zh-CN', zhDialogs],
      ['en-US', enDialogs],
    ] as const) {
      const have = Object.keys((d as any).settings.diagnostics.send.failure).sort()
      expect(have, lang).toEqual([...codes].sort())
    }
  })

  it('问题类型与服务端契约的闭集逐项一致（CATEGORIES ↔ category）', () => {
    const m = py.match(/CATEGORIES = \(([^)]*)\)/s)!
    const cats = [...m[1].matchAll(/"([a-z_]+)"/g)].map((x) => x[1])
    expect(cats).toHaveLength(8)
    expect(CAP.categories).toEqual(cats)
    for (const d of [zhDialogs, enDialogs]) {
      expect(Object.keys((d as any).settings.diagnostics.send.category)).toEqual(cats)
    }
  })

  it('每类包内文件都有说明（ENTRY_KINDS 的值 ⊆ kind）', () => {
    const block = py.slice(py.indexOf('ENTRY_KINDS = {'), py.indexOf('MAX_BUNDLE_ENTRIES'))
    const kinds = new Set([...block.matchAll(/: "([a-z_]+)",/g)].map((m) => m[1]))
    kinds.add('other')
    for (const d of [zhDialogs, enDialogs]) {
      const have = Object.keys((d as any).settings.diagnostics.send.kind)
      for (const k of kinds) expect(have).toContain(k)
    }
  })

  it('说明字数上限与引擎一致（NOTE_MAX_CHARS）', () => {
    expect(py).toMatch(/NOTE_MAX_CHARS = 1000/)
    expect(CAP.note_max_chars).toBe(1000)
  })
})
