/**
 * 恢复卡「选一份」名单（ADR 0211 第一期，维护者 2026-11-25 定稿）：每个候选只占一行——左边只写排版名
 * （按钮已经写了「选一份打开」，不再每行「打开“…”」），右边灰字是最后修改时间，用来分开同名候选。
 * 时间写法与时间线节点默认名相同（本地时区 `MM-DD HH:mm`），右侧复用菜单项现成的右侧槽位（靠右、数字等宽）；
 * 拿不到时间右边留空。不写图数。主语：名单项认 `data-restore-action="open-alt"`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/documentRestore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/documentRestore')>()),
  openAlternative: vi.fn(async () => ({ kind: 'superseded' })),
}))

import { formatStamp } from '@/i18n/format'
import { openAlternative, type AltCandidate, type RestoreIssue } from '@/store/documentRestore'
import { RestoreIssueBody } from './RestoreIssueBody'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement

const AT_A = new Date(2026, 10, 25, 13, 47).getTime()
const AT_B = new Date(2026, 10, 24, 9, 5).getTime()

const alt = (over: Partial<AltCandidate>): AltCandidate => ({
  id: 'd_x', name: '排版一', sources: ['project_history'], ownership: 'this', ...over,
})

async function render(alternatives: AltCandidate[]) {
  const issue: RestoreIssue = { pj: 'p_a', gen: 1, kind: 'choose', ref: null, alternatives }
  await act(async () => root.render(<RestoreIssueBody issue={issue} close={() => {}} />))
  const trigger = host.querySelector<HTMLElement>('[data-restore-action="choose"]')!
  await act(async () => {
    trigger.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
  })
  return [...document.querySelectorAll<HTMLElement>('[data-restore-action="open-alt"]')]
}

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.clearAllMocks()
})

describe('恢复卡「选一份」：一行一份，名字在左、时间在右', () => {
  it('两份同名 + 一份不同名：每行只有排版名与右侧时间，同名的靠时间分开；点哪份开哪份', async () => {
    const items = await render([
      alt({ id: 'd_a', at: AT_A }),
      alt({ id: 'd_b', at: AT_B }),
      alt({ id: 'd_c', name: '投稿版', at: AT_B }),
    ])
    expect(items.map((i) => i.textContent)).toEqual(['排版一11-25 13:47', '排版一11-24 09:05', '投稿版11-24 09:05'])
    // 不再每行重复「打开“…”」，也没有图数 / 第二行
    for (const i of items) {
      expect(i.textContent).not.toMatch(/打开|“|张图/)
      expect(i.querySelectorAll('.flex-col > span')).toHaveLength(1)
    }
    // 右侧那段在名字后面、靠右、灰字、数字等宽、正文字体（不是等宽字）
    const right = items[0].lastElementChild as HTMLElement
    expect(right.textContent).toBe('11-25 13:47')
    expect(right.className).toMatch(/text-right/)
    expect(right.className).toMatch(/text-ink-3/)
    expect(right.className).toMatch(/tabular-nums/)
    expect(right.className).not.toMatch(/font-mono/)
    await act(async () => items[1].click())
    expect(openAlternative).toHaveBeenCalledWith('d_b')
  })

  it('拿不到时间：右边留空，只有名字', async () => {
    const [only] = await render([alt({ id: 'd_n' })])
    expect(only.textContent).toBe('排版一')
  })

  it('时间按本地时区、与时间线默认名同一写法（补零、24 小时制）', () => {
    expect(formatStamp(new Date(2026, 0, 3, 7, 4))).toBe('01-03 07:04')
    expect(formatStamp(new Date(2026, 11, 31, 23, 59))).toBe('12-31 23:59')
  })
})
