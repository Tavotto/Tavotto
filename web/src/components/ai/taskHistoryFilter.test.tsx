/**
 * AI 任务历史抽屉的状态筛选（#145）。
 *
 * 这一格原来是全仓库最后几个原生 `<select>` 之一——同一类操作在相邻界面用不同
 * 控件，视觉与键盘行为都不一致。迁到 `ui/Select` 之后要钉两件事：筛选**真的**
 * 传到后端，以及「全部状态」那一档不能因为 Radix 不接受空串就悄悄丢掉。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchAiHistory } from '@/lib/api'
import { t } from '@/i18n'
import { TaskHistory } from './AiPanel'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAiHistory: vi.fn(),
}))

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const ai = (k: string) => t(k, { ns: 'ai' })

let root: Root
let host: HTMLDivElement

beforeEach(async () => {
  vi.mocked(fetchAiHistory).mockResolvedValue({ sessions: [], total: 0 } as never)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<TaskHistory onClose={() => {}} />)
  })
})

afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

const openFilter = async (): Promise<HTMLElement[]> => {
  const trigger = document.querySelector(
    `[role="combobox"][aria-label="${ai('history.filterAria')}"]`,
  ) as HTMLElement
  expect(trigger, '状态筛选不见了（迁到 ui/Select 之后是 combobox）').toBeTruthy()
  await act(async () => {
    trigger.click()
  })
  return [...document.body.querySelectorAll('[role="option"]')] as HTMLElement[]
}

describe('任务历史的状态筛选', () => {
  it('选一个状态：真的作为 status 传给后端', async () => {
    const options = await openFilter()
    expect(options.length, '状态清单是空的').toBeGreaterThan(1)
    // 第 0 项是「全部状态」，第 1 项起是真实状态
    expect(options[0].textContent?.trim()).toBe(ai('history.allStatuses'))
    await act(async () => {
      options[1].click()
    })
    const arg = vi.mocked(fetchAiHistory).mock.calls.at(-1)![0] as { status: string }
    expect(arg.status, '选了一个具体状态，却没作为 status 发出去').toBeTruthy()
    expect(arg.status).not.toBe('__all__')
  })

  it('「全部状态」传的是空筛选，不是那个哨兵值', async () => {
    // Radix 的 Item 不接受空串（那是「未选中」的保留态），所以界面上用了
    // `__all__` 哨兵。它绝不能漏到后端去——那会变成一个查不到任何东西的筛选。
    const first = await openFilter()
    await act(async () => first[1].click())

    const again = await openFilter()
    expect(again[0].textContent?.trim()).toBe(ai('history.allStatuses'))
    await act(async () => {
      again[0].click()
    })
    const arg = vi.mocked(fetchAiHistory).mock.calls.at(-1)![0] as { status: string }
    expect(arg.status).toBe('')
  })
})
