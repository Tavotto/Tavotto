/**
 * 故障卡里的「发送问题反馈」入口（ADR 0118）：
 *   * 开关关着（`diagSendStore.capability === null`，与设置页同一个判据）→ 完全不渲染；
 *   * 卡片**没有**可执行的修复动作 → 它是主按钮；已有修复类主按钮 → 它住在折叠详情里、不抢主按钮；
 *   * 点它只是打开对话框（本机备包）：不发送，`startDiagSend` 为 0 次。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  startDiagSend: vi.fn(),
  prepareDiagSend: vi.fn(),
  discardDiagSend: vi.fn(),
  fetchDiagSendStatus: vi.fn(),
}))

import { prepareDiagSend, discardDiagSend, startDiagSend, type DiagSendPrepared } from '@/lib/api'
import { ApiError } from '@/lib/api'
import { t } from '@/i18n'
import { DiagnosticsSendHost } from '@/components/DiagnosticsSendHost'
import { ResultBlock } from '@/components/ExportDialog'
import { SendReportButton } from '@/components/SendReportButton'
import { TaskDiagnostic } from '@/components/TaskDiagnostic'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDiagSendStore } from '@/store/diagSendStore'

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

const label = t('settings.diagnostics.send.cardButton', { ns: 'dialogs' })
const CAP = { enabled: true, note_max_chars: 1000, retention_days: 30, categories: ['other'] }
const PREPARED: DiagSendPrepared = {
  id: 'pid-1', size: 100, sha256: 'a'.repeat(64), schema: 6, max_bytes: 1e7, too_large: false,
  entries: [{ name: 'report.json', kind: 'environment' }],
}

let host: HTMLDivElement
let root: Root
async function render(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        {node}
        <DiagnosticsSendHost />
      </TooltipProvider>,
    )
  })
  await act(async () => {})
}
const buttons = () => Array.from(document.body.querySelectorAll<HTMLButtonElement>('[data-send-report]'))
const job = (recoverable: boolean) =>
  ({
    job_id: 'job-1',
    status: 'failed',
    outputs: [],
    warnings: [],
    conflicts: [],
    error: { code: 'export_failed', params: {}, recoverable },
  }) as never

beforeEach(() => {
  vi.mocked(startDiagSend).mockReset()
  vi.mocked(prepareDiagSend).mockReset().mockResolvedValue(PREPARED)
  vi.mocked(discardDiagSend).mockReset().mockResolvedValue({ ok: true })
  useDiagSendStore.setState({ capability: null, open: false })
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('开关关着：完全不出现', () => {
  it('SendReportButton / 导出失败卡 / TaskDiagnostic 里都没有', async () => {
    await render(
      <>
        <ResultBlock job={job(false)} edited={false} onRetry={() => {}} />
        <ResultBlock job={job(true)} edited={false} onRetry={() => {}} />
        <TaskDiagnostic kind="preparation" refId="a" folded={false} />
      </>,
    )
    expect(buttons()).toHaveLength(0)
    expect(document.body.textContent).not.toContain(label)
  })
})

describe('开关开着：主 / 次位置', () => {
  beforeEach(() => useDiagSendStore.setState({ capability: CAP }))

  it('导出失败且不可恢复（卡片没有修复动作）：它就是主按钮', async () => {
    await render(<ResultBlock job={job(false)} edited={false} onRetry={() => {}} />)
    const notice = document.body.querySelector('[data-export-failed]')!
    const direct = Array.from(notice.querySelectorAll<HTMLButtonElement>('button')).filter(
      (b) => !b.closest('details'),
    )
    expect(direct.map((b) => b.textContent)).toEqual([label])
    expect(direct[0].hasAttribute('data-send-report')).toBe(true)
  })

  it('导出失败但可重试：主按钮仍是「重试」，发送反馈在折叠详情里', async () => {
    await render(<ResultBlock job={job(true)} edited={false} onRetry={() => {}} />)
    const notice = document.body.querySelector('[data-export-failed]')!
    const direct = Array.from(notice.querySelectorAll<HTMLButtonElement>('button')).filter(
      (b) => !b.closest('details'),
    )
    expect(direct).toHaveLength(1)
    expect(direct[0].hasAttribute('data-send-report')).toBe(false)
    const inFold = notice.querySelector('details [data-send-report]')
    expect(inFold).not.toBeNull()
    expect((notice.querySelector('details') as HTMLDetailsElement).open).toBe(false)
  })

  it('TaskDiagnostic（已住在折叠详情里的调用方）：与「下载诊断」并排，次要样式', async () => {
    await render(<TaskDiagnostic kind="script_run" refId="r1" folded={false} />)
    const body = document.body.querySelector('[data-task-diagnostic-body]')!
    expect(body.querySelectorAll('[data-send-report]')).toHaveLength(1)
  })
})

describe('点击只是打开对话框', () => {
  it('打开 = 本机备包；没有点「发送」之前 startDiagSend 为 0；关掉后丢弃', async () => {
    useDiagSendStore.setState({ capability: CAP })
    await render(<SendReportButton variant="primary" />)
    expect(prepareDiagSend).not.toHaveBeenCalled()
    await act(async () => buttons()[0].click())
    await act(async () => {})
    expect(useDiagSendStore.getState().open).toBe(true)
    expect(prepareDiagSend).toHaveBeenCalledTimes(1)
    expect(startDiagSend).not.toHaveBeenCalled()
    await act(async () => useDiagSendStore.getState().setOpen(false))
    expect(discardDiagSend).toHaveBeenCalledWith('pid-1', null)
    expect(startDiagSend).not.toHaveBeenCalled()
  })
})

describe('迟到的旧备包被引擎取代（409 stale_prepare）', () => {
  it('关了再开：旧请求晚到的 stale_prepare 不改界面、不丢弃新会话、不显示失败', async () => {
    useDiagSendStore.setState({ capability: CAP })
    let rejectA!: (e: unknown) => void
    const pending = new Promise<DiagSendPrepared>((_res, rej) => (rejectA = rej))
    vi.mocked(prepareDiagSend)
      .mockReset()
      .mockReturnValueOnce(pending)
      .mockResolvedValueOnce({ ...PREPARED, id: 'pid-B' })
    await render(<SendReportButton variant="primary" />)
    await act(async () => buttons()[0].click())
    await act(async () => {})
    await act(async () => useDiagSendStore.getState().setOpen(false))
    await act(async () => buttons()[0].click())
    await act(async () => {})
    expect(prepareDiagSend).toHaveBeenCalledTimes(2)
    const failed = t('settings.diagnostics.send.prepareFailed', { ns: 'dialogs' })
    expect(document.body.textContent).not.toContain(failed)
    vi.mocked(discardDiagSend).mockClear()
    await act(async () => rejectA(new ApiError('diag_stale_prepare', 409, { code: 'diag_stale_prepare' })))
    await act(async () => {})
    expect(document.body.textContent).not.toContain(failed)
    expect(discardDiagSend).not.toHaveBeenCalledWith('pid-B', expect.anything())
    await act(async () => useDiagSendStore.getState().setOpen(false))
    expect(discardDiagSend).toHaveBeenCalledWith('pid-B', null) // 新会话仍是界面持有的那份
  })

  it('被另一个窗口的更新请求取代（同一次打开里收到 stale_prepare）：不显示「备包失败」，其他错误才显示', async () => {
    useDiagSendStore.setState({ capability: CAP })
    const failed = t('settings.diagnostics.send.prepareFailed', { ns: 'dialogs' })
    vi.mocked(prepareDiagSend)
      .mockReset()
      .mockRejectedValueOnce(new ApiError('diag_stale_prepare', 409, { code: 'diag_stale_prepare' }))
    await render(<SendReportButton variant="primary" />)
    await act(async () => buttons()[0].click())
    await act(async () => {})
    expect(document.body.textContent).not.toContain(failed)
    await act(async () => useDiagSendStore.getState().setOpen(false))
    vi.mocked(prepareDiagSend).mockRejectedValueOnce(new ApiError('boom', 500, {}))
    await act(async () => buttons()[0].click())
    await act(async () => {})
    expect(document.body.textContent).toContain(failed)
  })
})
