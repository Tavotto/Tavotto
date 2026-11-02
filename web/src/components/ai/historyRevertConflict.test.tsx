/**
 * 历史条目上的「回滚」遇到过期写入（QA STATE-09）：后端以 409 `ai_revert_conflict`
 * 拒绝时，界面要**说出口**，不许装作已回滚。
 *
 * 主语是状态轨（`uiStore.status`）在点击之后那一刻的内容：原来的 `.then` 只接成功，
 * 失败被吞成一次未处理的 rejection——状态轨上什么都没有，用户以为点了没反应或已经好了。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { aiRevert, ApiError, fetchAiHistory, type AiHistoryEntry } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useRevertDiffStore } from '@/store/revertDiffStore'
import { useUiStore } from '@/store/uiStore'
import { TaskHistory } from './AiPanel'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAiHistory: vi.fn(),
  aiRevert: vi.fn(),
}))

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const entry = {
  id: 'abc123',
  prompt: '把线改成红色',
  target: '整张图',
  provider: 'codex',
  model: 'm',
  status: 'done',
  changed: true,
  revert_available: true,
  pinned: false,
  started_ms: 1_756_000_000_000,
  script: 'fig1.py',
  diff: '--- a/fig1.py\n+++ b/fig1.py\n@@ -1 +1 @@\n-a\n+b\n',
} as unknown as AiHistoryEntry

let root: Root
let host: HTMLDivElement

beforeEach(async () => {
  useUiStore.getState().setStatus(null)
  useRevertDiffStore.getState().close()
  vi.mocked(fetchAiHistory).mockResolvedValue({ sessions: [entry], total: 1 } as never)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <TaskHistory onClose={() => {}} />
      </TooltipProvider>,
    )
  })
  await act(async () => {
    await new Promise<void>((r) => setTimeout(r, 0))
  })
})

afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
  vi.clearAllMocks()
  useUiStore.getState().setStatus(null)
})

const clickRevert = async () => {
  const btn = document.querySelector('button[data-ai-history-revert]') as HTMLButtonElement | null
  expect(btn, '历史条目上没有回滚按钮').toBeTruthy()
  await act(async () => {
    btn!.click()
    await new Promise<void>((r) => setTimeout(r, 0))
  })
}

describe('历史条目回滚：过期写入被拒时说出口', () => {
  it('409 ai_revert_conflict → 这一条下面出现冲突句 + 「查看差异」，点它打开这次修改的差异；状态轨不再重复', async () => {
    vi.mocked(aiRevert).mockRejectedValue(
      new ApiError('conflict', 409, {
        code: 'ai_revert_conflict',
        params: { script: 'fig1.py' },
      }),
    )
    await clickRevert()
    const card = document.querySelector('[data-ai-history-conflict]')
    expect(card, '冲突后这一条下面没有出路卡').toBeTruthy()
    expect(card!.textContent).toContain('fig1.py')
    expect(useUiStore.getState().status).toBeNull()
    const btn = card!.querySelector('button[data-ai-history-conflict-diff]') as HTMLButtonElement
    expect(btn.textContent).toBe('查看差异')
    await act(async () => btn.click())
    expect(useRevertDiffStore.getState().target).toEqual({ diff: entry.diff, script: 'fig1.py' })
  })

  it('别的回滚失败仍走状态轨（error 档），不出现冲突卡', async () => {
    vi.mocked(aiRevert).mockRejectedValue(new ApiError('boom', 500, { code: 'internal_error' }))
    await clickRevert()
    expect(document.querySelector('[data-ai-history-conflict]')).toBeNull()
    expect(useUiStore.getState().statusTone).toBe('error')
  })

  it('对照：回滚成功时照旧报「已回滚」', async () => {
    vi.mocked(aiRevert).mockResolvedValue({ ok: true, script: 'fig1.py' })
    await clickRevert()
    const ui = useUiStore.getState()
    // 成功是完成态（✓），不是中性提示（Codex #821 P2：成功类提示都显式标 done）
    expect(ui.statusTone).toBe('done')
    expect(ui.status?.key).toBe('history.reverted')
  })
})
