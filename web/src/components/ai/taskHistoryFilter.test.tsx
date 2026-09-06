/**
 * AI 任务历史抽屉的搜索与状态筛选（#145，审计 T37）。
 *
 * 这一格原来是全仓库最后几个原生 `<select>` 之一——同一类操作在相邻界面用不同
 * 控件，视觉与键盘行为都不一致。迁到 `ui/Select` 之后要钉两件事：筛选**真的**
 * 传到后端，以及「全部状态」那一档不能因为 Radix 不接受空串就悄悄丢掉。
 *
 * 审计 T37 又加了一条：**一条记录都没有时不摆搜索与筛选**——搜一个空库、按状态
 * 筛一个空库，两个动作都不会有任何结果。判据的关键在「哪一刻」：不是「现在
 * 列表是空的」（筛出零条也是空的，那时它们必须留着，否则用户取消不掉筛选），
 * 而是「没有筛选条件、且库里本来就没有」。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchAiHistory, type AiHistoryEntry } from '@/lib/api'
import { t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
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

/** 一条真实形状的历史记录；筛选控件只在库里有东西时才出现 */
const entry = (over: Partial<AiHistoryEntry> = {}): AiHistoryEntry =>
  ({
    id: 1,
    prompt: '把图例移到左上角',
    target: '整张图',
    provider: 'codex',
    model: 'gpt-5.6-sol',
    status: 'done',
    changed: true,
    revert_available: true,
    pinned: false,
    started_ms: 1_756_000_000_000,
    script: '/tmp/figs/fig1.py',
    ...over,
  }) as unknown as AiHistoryEntry

const mount = async () => {
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
  // 首次查询排在一个 0ms 定时器之后。不等它，界面还停在「还不知道库里有没有
  // 记录」那一刻——那时它既不摆筛选也不写空状态，两种断言都会量到假象
  await act(async () => {
    await new Promise<void>((r) => setTimeout(r, 0))
  })
}

beforeEach(async () => {
  vi.mocked(fetchAiHistory).mockResolvedValue({ sessions: [entry()], total: 1 } as never)
  await mount()
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

describe('空历史不摆无效的搜索与筛选（审计 T37）', () => {
  /** 每条用例自己决定后端回什么，所以先把 beforeEach 挂上的那次卸掉 */
  const remount = async (res: { sessions: AiHistoryEntry[]; total: number }) => {
    await act(async () => root.unmount())
    document.body.innerHTML = ''
    vi.mocked(fetchAiHistory).mockResolvedValue(res as never)
    await mount()
  }

  const searchBox = () =>
    document.querySelector(`input[aria-label="${ai('history.searchAria')}"]`)
  const filterBox = () =>
    document.querySelector(`[role="combobox"][aria-label="${ai('history.filterAria')}"]`)

  it('一条记录都没有：搜索与筛选都不渲染，只留一句空状态', async () => {
    await remount({ sessions: [], total: 0 })
    expect(searchBox(), '空库里还摆着搜索框').toBeNull()
    expect(filterBox(), '空库里还摆着状态筛选').toBeNull()
    expect(document.body.textContent).toContain(ai('history.empty'))
  })

  it('空状态只有一句，不再跟一段「怎么开始」——那是输入框自己的事', async () => {
    await remount({ sessions: [], total: 0 })
    // 原来的第二段引导以「在下方输入需求」起头
    expect(document.body.textContent).not.toContain('在下方输入需求')
  })

  it('有记录时它们回来', async () => {
    await remount({ sessions: [entry()], total: 1 })
    expect(searchBox()).toBeTruthy()
    expect(filterBox()).toBeTruthy()
  })

  it('筛出零条时搜索与筛选必须留着，否则用户取消不掉筛选', async () => {
    // 先有记录 → 控件在 → 输入一个搜不到的词 → 后端回零条
    await remount({ sessions: [entry()], total: 1 })
    vi.mocked(fetchAiHistory).mockResolvedValue({ sessions: [], total: 0 } as never)
    const box = searchBox() as HTMLInputElement
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        'value',
      )!.set!
      setter.call(box, '搜不到的词')
      box.dispatchEvent(new Event('input', { bubbles: true }))
    })
    // 防抖 250ms
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 400))
    })
    expect(searchBox(), '筛出零条之后搜索框没了，用户没法把词删掉').toBeTruthy()
    expect(filterBox()).toBeTruthy()
    expect(document.body.textContent).toContain(ai('history.noMatch'))
  })
})
