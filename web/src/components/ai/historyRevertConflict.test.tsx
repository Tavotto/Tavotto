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
import { t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
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
} as unknown as AiHistoryEntry

let root: Root
let host: HTMLDivElement

beforeEach(async () => {
  useUiStore.getState().setStatus(null)
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
  const btn = document.querySelector(
    `button[aria-label="${t('history.revert', { ns: 'ai' })}"]`,
  ) as HTMLButtonElement | null
  expect(btn, '历史条目上没有回滚按钮').toBeTruthy()
  await act(async () => {
    btn!.click()
    await new Promise<void>((r) => setTimeout(r, 0))
  })
}

describe('历史条目回滚：过期写入被拒时说出口', () => {
  it('409 ai_revert_conflict → 状态轨上是冲突文案（error 档），不是「已回滚」', async () => {
    vi.mocked(aiRevert).mockRejectedValue(
      new ApiError('conflict', 409, {
        code: 'ai_revert_conflict',
        params: { script: 'fig1.py' },
      }),
    )
    await clickRevert()
    const ui = useUiStore.getState()
    expect(ui.statusTone).toBe('error')
    expect(ui.status?.key).toBe('backend.ai_revert_conflict')
    expect(ui.status?.values).toEqual({ script: 'fig1.py' })
  })

  it('对照：回滚成功时照旧报「已回滚」', async () => {
    vi.mocked(aiRevert).mockResolvedValue({ ok: true, script: 'fig1.py' })
    await clickRevert()
    const ui = useUiStore.getState()
    expect(ui.statusTone).toBe('info')
    expect(ui.status?.key).toBe('history.reverted')
  })
})
