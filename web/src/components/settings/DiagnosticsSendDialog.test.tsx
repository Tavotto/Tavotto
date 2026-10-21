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
  ApiError,
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
import { setCurrentProjectId } from '@/lib/session'
import { useProjectStore } from '@/store/projectStore'
import { clearDiagnosticTrace, recordDiagnosticEvent, type DiagnosticEvent } from '@/diagnostics'
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
  max_bytes: 64 * 1024 * 1024,
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
  vi.mocked(saveDiagnosticsZip).mockClear()
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
    expect(discardMock).toHaveBeenCalledWith('pid-1', null)
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
    expect(discardMock).toHaveBeenCalledWith('pid-1', null)
    expect(startMock).not.toHaveBeenCalled()
    root = createRoot(host)
  })
})

describe('先看再发', () => {
  it('说明框在前（2026-10-19）：默认可见 = 副标题那一句 + 说明（标签在上、提示与字数一行）+ 问题类型 + 一行附件 + 「发送」「关闭」；清单 / 保存 / 额外项 / 政策都折在「查看内容」里', async () => {
    await mount()
    expect(q('[data-diag-send-sentence]')!.textContent).toBe(ds('sentence'))
    expect(q('[data-diag-send-sentence]')!.closest('details'), '如实说明的那一句常显，不在折叠里').toBeNull()
    const note = q<HTMLTextAreaElement>('[data-diag-send-note]')!
    expect(note.closest('details'), '说明框不折叠').toBeNull()
    expect(document.body.querySelector(`label[for="${note.id}"]`)?.textContent, '标签在字段上方').toBe(ds('noteLabel'))
    const category = q('[data-diag-send-category]')!
    expect(category.closest('details'), '问题类型不折叠').toBeNull()
    expect(category.querySelector(`[aria-label="${ds('categoryLabel')}"]`)).not.toBeNull()
    const det = q<HTMLDetailsElement>('[data-diag-send-details]')!
    expect(det.open).toBe(false)
    const folded = ['[data-diag-send-kinds]', '[data-diag-send-save]', '[data-diag-send-policy]', '[data-diag-send-error-summary-note]']
    for (const sel of folded) expect(q(sel)?.closest('details')).toBe(det)
    const footer = Array.from(document.body.querySelectorAll<HTMLButtonElement>('[data-diag-send-confirm],[data-diag-send-dismiss]'))
    expect(footer.map((b) => b.textContent)).toEqual([ds('close'), ds('send')])
    expect(document.body.querySelectorAll('[data-dialog-footer] [data-variant="primary"]'), '一颗主按钮').toHaveLength(1)
    // 额外内容（2026-10-21）：三个框 + 一键全选不折叠，且排在说明框之前（紧跟副标题）
    const extras = q('[data-diag-send-extras]')!
    expect(extras.closest('details'), '额外内容不折叠').toBeNull()
    expect(extras.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(extras.querySelectorAll('input[type=checkbox]')).toHaveLength(3)
    expect(q('[data-diag-send-extras-all]')!.textContent).toBe(ds('extrasSelectAll'))
    expect(det.textContent).toContain(ds('bestEffort'))
    expect(document.body.textContent).toContain(ds('noteAttached'))
    // 错误摘要的披露只在折叠里（不新增可见段落）；不暗示同意今后自动上报
    expect(det.textContent).toContain(ds('errorSummaryNote'))
    expect(q('[data-diag-send-sentence]')!.textContent).not.toContain(ds('errorSummaryNote'))
  })

  it('展开「查看详情」后可保存同一份 ZIP（展开不触发任何发送）', async () => {
    bundleMock.mockResolvedValue(new Blob(['zip']))
    await mount()
    const det = q<HTMLDetailsElement>('[data-diag-send-details]')!
    await act(async () => {
      det.open = true
    })
    await click(q('[data-diag-send-save]'))
    expect(bundleMock).toHaveBeenCalledWith('pid-1')
    expect(startMock).not.toHaveBeenCalled()
  })

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
    expect(document.body.textContent).toContain(ds('tooLarge', { size: '11.0 MiB', max: '64.0 MiB' }))
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

  it('备好的包在引擎里已被清掉（404）：说「关闭重开」，不给重试', async () => {
    startMock.mockRejectedValue(new ApiError('gone', 404, { code: 'diag_send_not_found' }))
    await mount()
    await click(q('[data-diag-send-confirm]'))
    await tick()
    expect(q('[data-diag-send-status]')!.textContent).toContain(ds('packageGone'))
    expect(q('[data-diag-send-confirm]')).toBeNull()
    // 全局保管预算回收了这份包时同一条路径：给「重新准备」
    expect(q('[data-diag-send-reprepare]')).not.toBeNull()
  })

  it('取消时报告其实已送达（cancel_raced）：按成功展示报告编号并如实说明', async () => {
    startMock.mockResolvedValue(status({}))
    statusMock.mockResolvedValue(
      status({ state: 'done', stage: null, report_id: 'TVD-ABCD-EFGH-JKMN-PQRS', cancel_raced: true }),
    )
    await mount()
    await click(q('[data-diag-send-confirm]'))
    await tick()
    expect(q('[data-diag-report-id]')!.textContent).toBe('TVD-ABCD-EFGH-JKMN-PQRS')
    expect(q('[data-diag-send-status]')!.textContent).toContain(ds('doneAfterCancel'))
    expect(q('[data-diag-send-status]')!.textContent).not.toContain(ds('cancelled'))
  })

  it('取消结果无法确认（complete 已发出）：结果未知——如实说、保留编号、只剩「关闭」，不说「没有发送」', async () => {
    startMock.mockResolvedValue(status({}))
    statusMock.mockResolvedValue(status({ state: 'unknown', stage: null, report_id: 'TVD-ABCD-EFGH-JKMN-PQRS' }))
    await mount()
    await click(q('[data-diag-send-confirm]'))
    await tick()
    expect(q('[data-diag-report-id]')!.textContent).toBe('TVD-ABCD-EFGH-JKMN-PQRS')
    expect(q('[data-diag-send-status]')!.textContent).toContain(ds('unknownOutcome'))
    expect(q('[data-diag-send-status]')!.textContent).not.toContain(ds('cancelled'))
    expect(q('[data-diag-send-confirm]')).toBeNull()
    expect(q('[data-diag-send-close]')).not.toBeNull()
  })

  it('send 带着引擎给的动作代次；取消后会话作废——自动重新备包，再发用新会话', async () => {
    prepareMock
      .mockResolvedValueOnce({ ...PREPARED, id: 'pid-1', gen: 3 })
      .mockResolvedValueOnce({ ...PREPARED, id: 'pid-2', gen: 0 })
    startMock.mockResolvedValue(status({ id: 'pid-1' }))
    statusMock.mockResolvedValueOnce(status({ id: 'pid-1', stage: 'upload', sent: 1, total: 9, gen: 3 }))
    await mount()
    await click(q('[data-diag-send-confirm]'))
    expect(startMock).toHaveBeenCalledWith('pid-1', expect.objectContaining({ gen: 3 }))
    await click(q('[data-diag-send-cancel]'))
    statusMock.mockResolvedValue(status({ id: 'pid-1', state: 'cancelled', stage: null, gen: 4 }))
    await tick()
    await tick()
    expect(prepareMock).toHaveBeenCalledTimes(2) // 重新备包
    expect(discardMock).toHaveBeenCalledWith('pid-1', null)
    expect(q('[data-diag-send-status]')!.textContent).toContain(ds('cancelled'))
    startMock.mockResolvedValue(status({ id: 'pid-2' }))
    statusMock.mockResolvedValue(status({ id: 'pid-2' }))
    await click(q('[data-diag-send-confirm]'))
    expect(startMock).toHaveBeenLastCalledWith('pid-2', expect.objectContaining({ gen: 0 }))
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

  it.each(['diag_stale_action', 'diag_session_cancelled'])(
    '迟到的 start 被引擎拒（409 %s）：属于取消流程，不是失败——继续轮询到 cancelled 并重新备包（#928）',
    async (code) => {
      prepareMock
        .mockResolvedValueOnce({ ...PREPARED, id: 'pid-1', gen: 0 })
        .mockResolvedValueOnce({ ...PREPARED, id: 'pid-2', gen: 0 })
      let rejectStart: (e: unknown) => void = () => {}
      startMock.mockReturnValue(new Promise((_, rej) => (rejectStart = rej)))
      statusMock.mockResolvedValue(status({ id: 'pid-1', state: 'cancelling', stage: null, gen: 1 }))
      await mount()
      await click(q('[data-diag-send-confirm]'))
      await click(q('[data-diag-send-cancel]'))
      // 取消先被引擎处理，迟到的 start 才回来 409
      await act(async () => rejectStart(new ApiError(code, 409, { code })))
      await tick()
      expect(q('[data-diag-send-status]')!.dataset.diagSendStatus).toBe('cancelling')
      expect(q('[data-diag-send-failure]')).toBeNull()
      statusMock.mockResolvedValue(status({ id: 'pid-1', state: 'cancelled', stage: null, gen: 1 }))
      await tick()
      await tick()
      expect(prepareMock).toHaveBeenCalledTimes(2)
      expect(q('[data-diag-send-status]')!.textContent).toContain(ds('cancelled'))
      expect(q('[data-diag-send-confirm]')).not.toBeNull()
    },
  )

  it('迟到的 start 被拒时用户还没点取消（别处取消了会话）：也进入取消流程，不显示失败', async () => {
    prepareMock.mockResolvedValueOnce({ ...PREPARED, id: 'pid-1' }).mockResolvedValueOnce({ ...PREPARED, id: 'pid-2' })
    startMock.mockRejectedValue(new ApiError('diag_stale_action', 409, { code: 'diag_stale_action' }))
    statusMock.mockResolvedValue(status({ id: 'pid-1', state: 'cancelled', stage: null, gen: 1 }))
    await mount()
    await click(q('[data-diag-send-confirm]'))
    await tick()
    await tick()
    expect(q('[data-diag-send-failure]')).toBeNull()
    expect(prepareMock).toHaveBeenCalledTimes(2)
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
    expect(discardMock).toHaveBeenCalledWith('pid-1', null)
    root = createRoot(host)
  })
})

describe('项目代次（Codex #923 P1）：对话框开着时项目被换掉，绝不发旧项目的包', () => {
  afterEach(() => {
    setCurrentProjectId(null)
    useProjectStore.setState({ switching: false })
    clearDiagnosticTrace()
  })

  it('备包完成后切项目：旧包被丢弃并重新备包，发送用的是新 id', async () => {
    setCurrentProjectId('A')
    prepareMock.mockResolvedValueOnce({ ...PREPARED, id: 'pid-A' }).mockResolvedValueOnce({ ...PREPARED, id: 'pid-B' })
    startMock.mockResolvedValue(status({ id: 'pid-B' }))
    statusMock.mockResolvedValue(status({ id: 'pid-B' }))
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
    expect(prepareMock).toHaveBeenCalledTimes(1)
    await act(async () => setCurrentProjectId('B'))
    await act(async () => {})
    // 端点按项目分键：丢弃必须带备包时的项目 A，而不是此刻（已是 B）的当前项目（Codex #955）
    expect(discardMock).toHaveBeenCalledWith('pid-A', 'A')
    expect(prepareMock).toHaveBeenCalledTimes(2)
    await click(q('[data-diag-send-confirm]'))
    expect(startMock).toHaveBeenCalledTimes(1)
    expect(startMock.mock.calls[0][0]).toBe('pid-B')
  })

  it('备包还在路上时切项目：晚到的旧响应被丢弃（并通知引擎释放），不会被拿去发送', async () => {
    setCurrentProjectId('A')
    let resolveOld!: (p: DiagSendPrepared) => void
    prepareMock
      .mockReturnValueOnce(new Promise((r) => (resolveOld = r)))
      .mockResolvedValueOnce({ ...PREPARED, id: 'pid-new' })
    startMock.mockResolvedValue(status({ id: 'pid-new' }))
    statusMock.mockResolvedValue(status({ id: 'pid-new' }))
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
    await act(async () => setCurrentProjectId('B'))
    await act(async () => {})
    await act(async () => resolveOld({ ...PREPARED, id: 'pid-old' }))
    expect(discardMock).toHaveBeenCalledWith('pid-old', 'A')
    await click(q('[data-diag-send-confirm]'))
    expect(startMock.mock.calls.map((c) => c[0])).toEqual(['pid-new'])
  })

  it('延迟的备包绑定调度时的项目（#1012 7）：载荷还在采集时切了项目，旧项目的备包请求被丢弃，之后的备包只属于新项目', async () => {
    setCurrentProjectId('A')
    prepareMock.mockResolvedValue({ ...PREPARED, id: 'pid-B' })
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    // 同步渲染：effect 已跑、buildSendPayload 的 promise 还没落地，此刻项目换了
    act(() => {
      root.render(
        <TooltipProvider>
          <DiagnosticsSendDialog open onOpenChange={() => {}} capability={CAP} />
        </TooltipProvider>,
      )
    })
    setCurrentProjectId('B')
    await act(async () => {})
    await act(async () => {})
    expect(prepareMock.mock.calls.length).toBeGreaterThan(0)
    expect(prepareMock.mock.calls.map((c) => c[2])).not.toContain('A')
    expect(prepareMock.mock.calls.every((c) => c[2] === 'B')).toBe(true)
  })

  it('真实的切换顺序：先认领新 pj、之后才换代（switching 期间）——这期间不备包，换代完成后备的包只含 B 的轨迹', async () => {
    // 与 projectStore.adoptSteps 同一个顺序：switching 亮 → setCurrentProjectId(B) → （await 加载）→ 清轨迹等换代 → switching 灭
    setCurrentProjectId('A')
    clearDiagnosticTrace()
    const aEvent = {
      type: 'align.blocked',
      mode: 'left',
      panel: 'panel:aaaaaaaaaaaa',
      reason: 'x',
      document_variant: 'var:111111111111',
      display_variant: null,
      authority_variant: null,
    } as unknown as DiagnosticEvent
    recordDiagnosticEvent(aEvent)
    const payloads: string[] = []
    prepareMock.mockImplementation(async (payload: unknown) => {
      payloads.push(JSON.stringify(payload))
      return { ...PREPARED, id: `pid-${payloads.length}` }
    })
    startMock.mockResolvedValue(status({}))
    statusMock.mockResolvedValue(status({}))
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
    expect(payloads).toHaveLength(1)
    expect(payloads[0]).toContain('panel:aaaaaaaaaaaa') // A 的包带着 A 的轨迹（判据的前提）

    // 切换开始 + 认领新 pj：内存里还是 A 的状态
    await act(async () => useProjectStore.setState({ switching: true }))
    await act(async () => setCurrentProjectId('B'))
    await act(async () => {})
    expect(discardMock).toHaveBeenCalledWith('pid-1', 'A')
    expect(payloads, '切换进行中不许备包（会是 A/B 混合包）').toHaveLength(1)
    expect(q('[data-diag-send-confirm]')!.hasAttribute('disabled')).toBe(true)
    await click(q('[data-diag-send-confirm]'))
    expect(startMock).not.toHaveBeenCalled()

    // 换代落地（轨迹清掉）→ switching 灭：这时才备包
    clearDiagnosticTrace()
    await act(async () => useProjectStore.setState({ switching: false }))
    await act(async () => {})
    expect(payloads).toHaveLength(2)
    expect(payloads[1]).not.toContain('panel:aaaaaaaaaaaa')
    await click(q('[data-diag-send-confirm]'))
    expect(startMock.mock.calls.map((c) => c[0])).toEqual(['pid-2'])
  })

  it.each([
    ['普通失败', new Error('boom')],
    ['404（引擎已丢会话）', new ApiError('gone', 404, { code: 'diag_send_not_found' })],
  ])('A 的 start 挂起 → 切到 B → B 备包完成 → A 的 start 才失败（%s）：不改 B 的对话框', async (_n, err) => {
    setCurrentProjectId('A')
    prepareMock.mockResolvedValueOnce({ ...PREPARED, id: 'pid-A' }).mockResolvedValueOnce({ ...PREPARED, id: 'pid-B' })
    let rejectA!: (e: unknown) => void
    startMock.mockReturnValueOnce(new Promise((_r, rej) => (rejectA = rej)))
    statusMock.mockResolvedValue(status({ id: 'pid-A' }))
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
    await click(q('[data-diag-send-confirm]')) // A 的 start 挂着
    await act(async () => setCurrentProjectId('B'))
    await act(async () => {})
    expect(prepareMock).toHaveBeenCalledTimes(2)
    expect(q('[data-diag-send-status]')!.getAttribute('data-diag-send-status')).toBe('ready')
    await act(async () => rejectA(err))
    await act(async () => {})
    // B 的对话框不受影响：仍是可发送的 ready，没有失败说明、没有 packageGone
    expect(q('[data-diag-send-status]')!.getAttribute('data-diag-send-status')).toBe('ready')
    expect(q('[data-diag-send-confirm]')).not.toBeNull()
    expect(q('[data-diag-send-reprepare]')).toBeNull()
    expect(document.body.textContent).not.toContain(ds('packageGone'))
  })

  it('发送中切项目：会话被取消（丢弃）并重新备包', async () => {
    setCurrentProjectId('A')
    prepareMock.mockResolvedValueOnce({ ...PREPARED, id: 'pid-A' }).mockResolvedValueOnce({ ...PREPARED, id: 'pid-B' })
    startMock.mockResolvedValue(status({ id: 'pid-A' }))
    statusMock.mockResolvedValue(status({ id: 'pid-A', stage: 'upload', sent: 1, total: 10 }))
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
    await click(q('[data-diag-send-confirm]'))
    await act(async () => setCurrentProjectId('B'))
    await act(async () => {})
    expect(discardMock).toHaveBeenCalledWith('pid-A', 'A')
    expect(prepareMock).toHaveBeenCalledTimes(2)
    expect(q('[data-diag-send-confirm]')).not.toBeNull()
  })
})

describe('引擎丢了会话（Codex #923 P2）：状态 404 是终态，不无限轮询、不困住用户', () => {
  it.each(['sending', 'cancelling'] as const)('%s 时 404：停止轮询、解除封锁、给「重新准备」', async (phase) => {
    startMock.mockResolvedValue(status({}))
    statusMock.mockRejectedValue(new ApiError('gone', 404, { code: 'diag_send_not_found' }))
    await mount()
    await click(q('[data-diag-send-confirm]'))
    if (phase === 'cancelling') await click(q('[data-diag-send-cancel]'))
    await tick()
    const calls = statusMock.mock.calls.length
    expect(calls).toBeGreaterThan(0)
    expect(q('[data-diag-send-status]')!.textContent).toContain(ds('packageGone'))
    expect(q('[data-diag-send-reprepare]')).not.toBeNull()
    expect(q('[data-diag-send-dismiss]')).not.toBeNull()
    await tick(3000)
    expect(statusMock.mock.calls.length).toBe(calls)
    // 不再 blockDismiss：「关闭」真的能关
    await click(q('[data-diag-send-dismiss]'))
    expect(closed).toEqual([false])
    // 重新准备：再备一次包
    prepareMock.mockResolvedValue({ ...PREPARED, id: 'pid-2' })
    await click(q('[data-diag-send-reprepare]'))
    await act(async () => {})
    expect(prepareMock).toHaveBeenCalledTimes(2)
    expect(q('[data-diag-send-confirm]')).not.toBeNull()
  })
})

describe('换包后复位旧包状态（Codex #1263 P2）', () => {
  it('404 置 packageGone → 改勾选换包成功 → 新包可重试失败显示普通重试而非「包已不在」', async () => {
    startMock.mockResolvedValue(status({}))
    statusMock.mockRejectedValue(new ApiError('gone', 404, { code: 'diag_send_not_found' }))
    await mount()
    await click(q('[data-diag-send-confirm]'))
    await tick()
    expect(q('[data-diag-send-status]')!.textContent).toContain(ds('packageGone'))
    prepareMock.mockResolvedValue({ ...PREPARED, id: 'pid-2', gen: 0 })
    await click(q('[data-diag-send-extra="source"]'))
    await tick(350)
    expect(prepareMock).toHaveBeenCalledTimes(2)
    expect(q('[data-diag-send-status]')!.textContent).not.toContain(ds('packageGone'))
    startMock.mockResolvedValue(status({ id: 'pid-2' }))
    statusMock.mockReset()
    statusMock.mockResolvedValue(status({ id: 'pid-2', state: 'failed', stage: null, code: 'rate_limited', retryable: true }))
    await click(q('[data-diag-send-confirm]'))
    await tick()
    const body = q('[data-diag-send-status]')!.textContent
    expect(body).toContain(ds('failure.rate_limited'))
    expect(body).not.toContain(ds('packageGone'))
    expect(q('[data-diag-send-confirm]')!.textContent).toBe(ds('retry'))
    expect(q('[data-diag-send-reprepare]')).toBeNull()
  })
})

describe('诊断包扩充（detail-…、勾选项）', () => {
  const lastPayload = () => prepareMock.mock.calls[prepareMock.mock.calls.length - 1][0] as Record<string, any>
  const box = (key: string) => q<HTMLInputElement>(`[data-diag-send-extra="${key}"]`)!
  const DETAIL_PREPARED: DiagSendPrepared = {
    ...PREPARED,
    entries: [
      { name: 'report.json', kind: 'environment', bytes: 1700 },
      { name: 'detail-errors.json', kind: 'detail_errors', bytes: 4096 },
      { name: 'detail-app-log.txt', kind: 'detail_logs', bytes: 2 * 1024 * 1024 },
    ],
    options: { source: false, crash_reports: false, corrupt_sessions: false },
    truncated: [{ kind: 'source', original_bytes: 900000, kept_bytes: 1000 }],
  }

  it('三个复选框默认不勾、不折叠；首次备包的载荷带 detail_options 全 false 与 detail', async () => {
    await mount(DETAIL_PREPARED)
    const det = q<HTMLDetailsElement>('[data-diag-send-details]')!
    expect(det.open).toBe(false)
    for (const key of ['source', 'crash_reports', 'corrupt_sessions']) {
      expect(box(key).checked).toBe(false)
      expect(box(key).closest('details')).toBeNull()
    }
    expect(lastPayload().detail_options).toEqual({ source: false, crash_reports: false, corrupt_sessions: false })
    expect(lastPayload().detail).toBeTypeOf('object')
    expect(lastPayload().detail.display.device_pixel_ratio).toBeTypeOf('number')
    const visibleBoxes = Array.from(document.body.querySelectorAll('input[type=checkbox]')).filter((el) => !el.closest('details'))
    expect(visibleBoxes).toHaveLength(3)
  })

  it('文件清单（路径 + 大小）和截断说明都在折叠区里', async () => {
    await mount(DETAIL_PREPARED)
    const det = q<HTMLDetailsElement>('[data-diag-send-details]')!
    const list = q('[data-diag-send-file-list]')!
    expect(list.closest('details[data-diag-send-details]')).toBe(det)
    expect(list.closest('details[data-diag-send-files]')).not.toBeNull()
    const row = q('[data-diag-file="detail-app-log.txt"]')!
    expect(row.textContent).toContain('detail-app-log.txt')
    expect(row.textContent).toContain('2.0 MiB')
    expect(q('[data-diag-file="detail-errors.json"]')!.textContent).toContain('4 KiB')
    expect(q('[data-diag-send-truncated]')!.textContent).toBe(ds('truncated', { kinds: ds('short.source') }))
    expect(q('[data-diag-send-truncated]')!.closest('details[data-diag-send-details]')).toBe(det)
  })

  it('没有被截断时不出现截断说明', async () => {
    await mount({ ...DETAIL_PREPARED, truncated: [] })
    expect(q('[data-diag-send-truncated]')).toBeNull()
  })

  it('勾选变化 = 重新备包（载荷带新选项、旧包被丢弃），已填的说明与类型不丢', async () => {
    await mount(DETAIL_PREPARED)
    const note = q<HTMLTextAreaElement>('[data-diag-send-note]')!
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
      setter.call(note, 'keep me')
      note.dispatchEvent(new Event('input', { bubbles: true }))
    })
    prepareMock.mockResolvedValue({ ...DETAIL_PREPARED, id: 'pid-2', gen: 0, options: { source: true, crash_reports: false, corrupt_sessions: false } })
    await click(box('source'))
    await tick(350)
    expect(prepareMock).toHaveBeenCalledTimes(2)
    expect(lastPayload().detail_options).toEqual({ source: true, crash_reports: false, corrupt_sessions: false })
    expect(discardMock).toHaveBeenCalledWith('pid-1', null)
    expect(box('source').checked).toBe(true)
    expect(q<HTMLTextAreaElement>('[data-diag-send-note]')!.value).toBe('keep me')
    expect(startMock).not.toHaveBeenCalled()
    // 再勾第二项：两项都在载荷里
    prepareMock.mockResolvedValue({ ...DETAIL_PREPARED, id: 'pid-3', options: { source: true, crash_reports: true, corrupt_sessions: false } })
    await click(box('crash_reports'))
    await tick(350)
    expect(lastPayload().detail_options).toEqual({ source: true, crash_reports: true, corrupt_sessions: false })
    // 发送用的是最新那份包
    startMock.mockResolvedValue(status({ state: 'sending' }))
    await click(q('[data-diag-send-confirm]'))
    expect(startMock.mock.calls[0][0]).toBe('pid-3')
  })

  it('勾选触发的刷新失败：旧包作废——保存与发送都不可用，不会拿到仍含已取消内容的旧包（隐私）', async () => {
    await mount({ ...DETAIL_PREPARED, options: { source: true, crash_reports: false, corrupt_sessions: false } })
    prepareMock.mockRejectedValue(new Error('boom'))
    await click(box('source')) // 取消勾选源码
    await tick(350)
    expect(prepareMock).toHaveBeenCalledTimes(2)
    expect(document.body.textContent).toContain(ds('prepareFailed'))
    // 引擎里的旧包被丢弃
    expect(discardMock).toHaveBeenCalledWith('pid-1', null)
    // 发送不可用；保存若还在也必须是禁用的，点了也取不到旧包
    expect(q('[data-diag-send-confirm]')).toBeNull()
    const save = q<HTMLButtonElement>('[data-diag-send-save]')
    if (save) {
      expect(save.disabled).toBe(true)
      await click(save)
    }
    expect(bundleMock).not.toHaveBeenCalled()
    expect(startMock).not.toHaveBeenCalled()
    expect(saveDiagnosticsZip).not.toHaveBeenCalled()
  })

  it('保存取包在途时取消勾选：响应回来也不写盘（旧包可能含已取消的内容，Codex #1263 P2）', async () => {
    await mount({ ...DETAIL_PREPARED, options: { source: true, crash_reports: false, corrupt_sessions: false } })
    let resolveBundle!: (b: Blob) => void
    bundleMock.mockReturnValue(new Promise<Blob>((r) => (resolveBundle = r)))
    prepareMock.mockResolvedValue({ ...DETAIL_PREPARED, id: 'pid-2' })
    await click(q('[data-diag-send-save]'))
    expect(bundleMock).toHaveBeenCalledWith('pid-1')
    await click(box('source')) // 在途取消勾选源码
    await act(async () => {
      resolveBundle(new Blob(['old']))
    })
    expect(saveDiagnosticsZip).not.toHaveBeenCalled()
  })

  it('保存成功后改勾选：换包期间与换包后都不再显示「已保存」（磁盘上只有旧包，Codex #1263 P2）', async () => {
    await mount(DETAIL_PREPARED)
    bundleMock.mockResolvedValue(new Blob(['old']))
    await click(q('[data-diag-send-save]'))
    await act(async () => {})
    expect(document.body.textContent).toContain(ds('saved'))
    prepareMock.mockResolvedValue({ ...DETAIL_PREPARED, id: 'pid-2', options: { source: true, crash_reports: false, corrupt_sessions: false } })
    await click(box('source'))
    expect(document.body.textContent).not.toContain(ds('saved'))
    await tick(350)
    await act(async () => {})
    expect(document.body.textContent).not.toContain(ds('saved'))
  })

  it('最新一次刷新收到 diag_stale_prepare：重试一次即恢复，不卡在刷新态（Codex #1263 P2）', async () => {
    await mount(DETAIL_PREPARED)
    prepareMock
      .mockRejectedValueOnce(new ApiError('stale', 409, { code: 'diag_stale_prepare' }))
      .mockResolvedValueOnce({ ...DETAIL_PREPARED, id: 'pid-2', options: { source: true, crash_reports: false, corrupt_sessions: false } })
    await click(box('source'))
    await tick(350)
    await act(async () => {})
    expect(prepareMock).toHaveBeenCalledTimes(3)
    expect(lastPayload().detail_options).toEqual({ source: true, crash_reports: false, corrupt_sessions: false })
    expect(q<HTMLButtonElement>('[data-diag-send-confirm]')!.disabled).toBe(false)
    expect(q<HTMLButtonElement>('[data-diag-send-save]')!.disabled).toBe(false)
  })

  it('重试后仍 stale：按失败作废（进 prepareFailed，不再无限重试，旧包不可保存 / 发送）', async () => {
    await mount(DETAIL_PREPARED)
    prepareMock.mockRejectedValue(new ApiError('stale', 409, { code: 'diag_stale_prepare' }))
    await click(box('source'))
    await tick(350)
    await act(async () => {})
    expect(prepareMock).toHaveBeenCalledTimes(3) // 挂载 1 次 + 刷新 1 次 + 重试 1 次
    expect(document.body.textContent).toContain(ds('prepareFailed'))
    await tick(2000)
    expect(prepareMock).toHaveBeenCalledTimes(3)
  })

  it('点击即时响应（卡顿回归）：勾选当场生效，不等后端、不回到「正在准备」、不禁用表单；连点只备一次', async () => {
    await mount(DETAIL_PREPARED)
    let release!: (p: DiagSendPrepared) => void
    prepareMock.mockImplementation(() => new Promise<DiagSendPrepared>((r) => (release = r)))
    await click(box('source'))
    // 备包请求还没发出（防抖中）、更没有回来：框已经勾上，且三个框与说明框都没被禁用
    expect(prepareMock).toHaveBeenCalledTimes(1)
    expect(box('source').checked).toBe(true)
    for (const k of ['source', 'crash_reports', 'corrupt_sessions']) expect(box(k).disabled, k).toBe(false)
    expect(q<HTMLTextAreaElement>('[data-diag-send-note]')!.disabled).toBe(false)
    expect(q('[data-diag-send-status]')!.getAttribute('data-diag-send-status')).toBe('ready')
    // 防抖中：状态区不出「正在准备」字样（静默）
    expect(q('[data-diag-send-status]')!.textContent).not.toContain(ds('preparing'))
    await click(box('crash_reports'))
    await click(box('corrupt_sessions'))
    expect(prepareMock).toHaveBeenCalledTimes(1)
    await tick(350)
    // 请求在途（后端没回）：框照样可点，发送在新包到之前不可用
    expect(prepareMock).toHaveBeenCalledTimes(2)
    expect(lastPayload().detail_options).toEqual({ source: true, crash_reports: true, corrupt_sessions: true })
    expect(box('source').disabled).toBe(false)
    expect(q<HTMLButtonElement>('[data-diag-send-confirm]')!.disabled).toBe(true)
    // 请求在途（refreshing=true）：状态区仍不出「正在准备」，发送按钮禁用即不会把旧包发出去
    expect(q('[data-diag-send-status]')!.textContent).not.toContain(ds('preparing'))
    await click(box('source'))
    expect(box('source').checked).toBe(false)
    release({ ...DETAIL_PREPARED, id: 'pid-late', options: { source: true, crash_reports: true, corrupt_sessions: true } })
    await act(async () => {})
    // 旧请求的结果不覆盖新勾选：不采用 pid-late、框仍是用户最后的选择，并通知引擎释放
    expect(box('source').checked).toBe(false)
    expect(discardMock).toHaveBeenCalledWith('pid-late', null)
    expect(q<HTMLButtonElement>('[data-diag-send-confirm]')!.disabled).toBe(true)
  })

  it('一键全选 / 全不选：一次点击三个框全勾（只换一次包），再点全清；默认仍全不勾', async () => {
    await mount(DETAIL_PREPARED)
    const all = () => q<HTMLButtonElement>('[data-diag-send-extras-all]')!
    for (const k of ['source', 'crash_reports', 'corrupt_sessions']) expect(box(k).checked).toBe(false)
    prepareMock.mockResolvedValue({ ...DETAIL_PREPARED, id: 'pid-all', options: { source: true, crash_reports: true, corrupt_sessions: true } })
    await click(all())
    for (const k of ['source', 'crash_reports', 'corrupt_sessions']) expect(box(k).checked, k).toBe(true)
    expect(all().textContent).toBe(ds('extrasSelectNone'))
    await tick(350)
    expect(prepareMock).toHaveBeenCalledTimes(2)
    expect(lastPayload().detail_options).toEqual({ source: true, crash_reports: true, corrupt_sessions: true })
    prepareMock.mockResolvedValue({ ...DETAIL_PREPARED, id: 'pid-none', options: { source: false, crash_reports: false, corrupt_sessions: false } })
    await click(all())
    for (const k of ['source', 'crash_reports', 'corrupt_sessions']) expect(box(k).checked, k).toBe(false)
    await tick(350)
    expect(lastPayload().detail_options).toEqual({ source: false, crash_reports: false, corrupt_sessions: false })
    // 勾着其中一项时再点 = 补全，而不是清空
    await click(box('source'))
    expect(all().textContent).toBe(ds('extrasSelectAll'))
    await click(all())
    for (const k of ['source', 'crash_reports', 'corrupt_sessions']) expect(box(k).checked, k).toBe(true)
  })

  it('引擎没把勾选项附上（载荷被丢）：复选框以引擎回的 options 为准，并说明「太大没能附上」', async () => {
    await mount(DETAIL_PREPARED)
    prepareMock.mockResolvedValue({ ...DETAIL_PREPARED, id: 'pid-2', options: { source: false, crash_reports: false, corrupt_sessions: false } })
    await click(box('source'))
    await tick(350)
    expect(prepareMock.mock.calls[1][1]).toEqual({ source: true, crash_reports: false, corrupt_sessions: false }) // 选项也走查询串
    expect(box('source').checked).toBe(false)
    expect(q('[data-diag-send-options-lost]')!.textContent).toBe(ds('optionsLost'))
    expect(q('[data-diag-send-options-lost]')!.closest('details')).toBeNull()
    // 引擎照实采用时没有这句话
    prepareMock.mockResolvedValue({ ...DETAIL_PREPARED, id: 'pid-3', options: { source: true, crash_reports: false, corrupt_sessions: false } })
    await click(box('source'))
    await tick(350)
    expect(box('source').checked).toBe(true)
    expect(q('[data-diag-send-options-lost]')).toBeNull()
  })

  it('常显的那一句按引擎确认的 options 选句：都没勾 = 原句；勾了就如实说附带了什么（仍只有一句）', async () => {
    await mount(DETAIL_PREPARED)
    const sentence = () => q('[data-diag-send-sentence]')!.textContent
    expect(sentence()).toBe(ds('sentence'))
    const cases: [Partial<Record<'source' | 'crash_reports' | 'corrupt_sessions', boolean>>, string[]][] = [
      [{ source: true }, ['source']],
      [{ crash_reports: true }, ['crash_reports']],
      [{ corrupt_sessions: true }, ['corrupt_sessions']],
      [{ source: true, crash_reports: true, corrupt_sessions: true }, ['source', 'crash_reports', 'corrupt_sessions']],
    ]
    let n = 0
    for (const [opts, keys] of cases) {
      const full = { source: false, crash_reports: false, corrupt_sessions: false, ...opts }
      prepareMock.mockResolvedValue({ ...DETAIL_PREPARED, id: `pid-s${n++}`, options: full })
      // 点任意一个框触发重新备包；显示以引擎回的 options 为准
      await click(box('source'))
      await tick(350)
      const want = ds('sentenceExtras', {
        items: keys.map((k) => ds(`sentenceItem.${k}`)).join(ds('listSeparator')),
      })
      expect(sentence()).toBe(want)
      expect(document.body.querySelectorAll('[data-diag-send-sentence]')).toHaveLength(1)
      expect(sentence()).not.toBe(ds('sentence'))
    }
    // 勾了但引擎没附上（options 全 false）：按引擎的来，回到原句
    prepareMock.mockResolvedValue({ ...DETAIL_PREPARED, id: 'pid-s9', options: { source: false, crash_reports: false, corrupt_sessions: false } })
    await click(box('crash_reports'))
    await tick(350)
    expect(sentence()).toBe(ds('sentence'))
  })

  it('不记忆：关掉对话框再打开，三个框都回到不勾，载荷也回到全 false', async () => {
    await mount(DETAIL_PREPARED)
    prepareMock.mockResolvedValue({ ...DETAIL_PREPARED, id: 'pid-2', options: { source: false, crash_reports: false, corrupt_sessions: true } })
    await click(box('corrupt_sessions'))
    await tick(350)
    expect(box('corrupt_sessions').checked).toBe(true)
    await act(async () => root.unmount())
    root = createRoot(host)
    prepareMock.mockResolvedValue({ ...DETAIL_PREPARED, id: 'pid-9' })
    await act(async () => {
      root.render(
        <TooltipProvider>
          <DiagnosticsSendDialog open onOpenChange={() => {}} capability={CAP} />
        </TooltipProvider>,
      )
    })
    await act(async () => {})
    expect(box('corrupt_sessions').checked).toBe(false)
    expect(lastPayload().detail_options).toEqual({ source: false, crash_reports: false, corrupt_sessions: false })
  })

  it('备包中 / 发送中复选框不可点（避免发送途中换内容）', async () => {
    await mount(DETAIL_PREPARED)
    startMock.mockResolvedValue(status({ state: 'sending' }))
    statusMock.mockResolvedValue(status({ state: 'sending' }))
    await click(q('[data-diag-send-confirm]'))
    for (const key of ['source', 'crash_reports', 'corrupt_sessions']) expect(box(key).disabled).toBe(true)
  })

  it('文案：PRIORITY 里每一类都有 short 名，三个选项都有名字与说明（中英）', () => {
    const py = readFileSync(resolve(__dirname, '../../../../src/tavotto/engine/diagdetail.py'), 'utf-8')
    const block = py.slice(py.indexOf('PRIORITY: dict[str, int] = {'), py.indexOf('#: 每类的原始字节份额'))
    const kinds = [...block.matchAll(/^\s+"([a-z_]+)": \d+,/gm)].map((m) => m[1])
    expect(kinds).toHaveLength(15)
    const opts = [...py.slice(py.indexOf('OPTION_KEYS = ('), py.indexOf('OPTION_KEYS = (') + 120).matchAll(/"([a-z_]+)"/g)].map((m) => m[1])
    expect(opts).toEqual(['source', 'crash_reports', 'corrupt_sessions'])
    for (const d of [zhDialogs, enDialogs]) {
      const send = (d as any).settings.diagnostics.send
      for (const k of kinds) expect(Object.keys(send.short), k).toContain(k)
      for (const k of opts) {
        expect(send.extra[k]).toBeTruthy()
        expect(send.extraHint[k]).toBeTruthy()
      }
    }
  })
})

describe('摘要句如实说明日志里可能有脚本打印的内容（#1012 4）', () => {
  const sends = [
    ['zh-CN', zhDialogs, '日志里可能有脚本打印出的内容'],
    ['en-US', enDialogs, 'may contain output printed by your script'],
  ] as const

  it('常显摘要句、带勾选项的摘要句、「默认不含」都提醒日志可能含脚本输出，且不再声称不含脚本源码', () => {
    for (const [lang, d, phrase] of sends) {
      const send = (d as any).settings.diagnostics.send
      expect(send.sentence, lang).toContain(phrase)
      expect(send.sentenceExtras, lang).toContain(phrase)
      expect(send.notIncluded, lang).toContain(
        lang === 'zh-CN' ? '脚本打印出的内容' : 'output printed by your script',
      )
      expect(send.sentence, lang).not.toMatch(/不含你的脚本源码|without your script source/)
    }
  })

  it('界面上显示的就是这句', async () => {
    await mount()
    const text = q('[data-diag-send-sentence]')!.textContent!
    expect(text).toContain(t('settings.diagnostics.send.sentence', { ns: 'dialogs' }))
    expect(text).toMatch(/脚本打印出的内容|output printed by your script/)
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
