/** 失败出口的共用按钮（`failureExit.ts`）：第一次失败给重试，重试又失败 / 本来就没用 / 反馈入口被关 → 各自的结局 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { formatMessage } from '@/i18n'
import { useDiagSendStore } from './diagSendStore'
import { retryOrFeedback } from './failureExit'
import { useUiStore } from './uiStore'

const label = (a: { label: never } | undefined) => formatMessage(a?.label ?? null)

beforeEach(() => {
  useUiStore.setState({ status: null, statusAction: null, statusOwner: null })
  useDiagSendStore.setState({ capability: { enabled: true } as never, open: false })
})

describe('retryOrFeedback', () => {
  it('第一次失败：「再试一次」，点下去先收掉自己那一条、再带着下一次序号重放', () => {
    const run = vi.fn()
    useUiStore.getState().setStatus(null)
    useUiStore.getState().setStatus(formatMsg(), 'error', { owner: 'o' })
    const a = retryOrFeedback({ owner: 'o', attempt: 0, run })!
    expect(label(a as never)).toBe('再试一次')
    a.run()
    expect(useUiStore.getState().status).toBeNull()
    expect(run).toHaveBeenCalledWith(1)
  })

  it('已经重试过 / 明确不可重试：「发给我们看看」，点下去打开反馈对话框', () => {
    for (const a of [
      retryOrFeedback({ owner: 'o', attempt: 1, run: vi.fn() })!,
      retryOrFeedback({ owner: 'o', attempt: 0, retryable: false, run: vi.fn() })!,
    ]) {
      expect(label(a as never)).toBe('发送问题反馈')
      a.run()
      expect(useDiagSendStore.getState().open).toBe(true)
      useDiagSendStore.setState({ open: false })
    }
  })

  it('反馈入口被用户 / 部署关掉：不给按钮（那是人家自己关的）', () => {
    useDiagSendStore.setState({ capability: null })
    expect(retryOrFeedback({ owner: 'o', attempt: 1, run: vi.fn() })).toBeUndefined()
  })
})

function formatMsg() {
  return { key: 'status.packageFailed', ns: 'workspace', values: { error: 'x' } } as never
}
