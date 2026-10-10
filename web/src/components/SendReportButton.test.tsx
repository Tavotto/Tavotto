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
    expect(discardDiagSend).toHaveBeenCalledWith('pid-1')
    expect(startDiagSend).not.toHaveBeenCalled()
  })
})
