/**
 * 恢复卡「选一份」名单上同名候选的区分（ADR 0211 第一期，维护者 2026-11-25 同意）：每个候选名字下面一行灰色小字
 * 「最后修改时间 · 几张图」，时间写法与时间线节点默认名相同（本地时区 `MM-DD HH:mm`），图数按面板数、英文分单复数。
 * 拿不到的那一半不写，两半都没有就没有这一行。主语：名单项认 `data-restore-action="open-alt"`，不认文案顺序。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/documentRestore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/documentRestore')>()),
  openAlternative: vi.fn(async () => ({ kind: 'superseded' })),
}))

import { i18n } from '@/i18n'
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

describe('恢复卡：同名候选下面一行「时间 · 几张图」', () => {
  it('两份同名候选各自带上最后修改时间与图数，分得开；点哪份开哪份', async () => {
    const items = await render([
      alt({ id: 'd_a', at: AT_A, figures: 2 }),
      alt({ id: 'd_b', at: AT_B, figures: 1 }),
    ])
    expect(items).toHaveLength(2)
    expect(items[0].textContent).toContain('排版一')
    expect(items[0].textContent).toContain('11-25 13:47 · 2 张图')
    expect(items[1].textContent).toContain('11-24 09:05 · 1 张图')
    expect(items[0].textContent).not.toBe(items[1].textContent)
    await act(async () => items[1].click())
    expect(openAlternative).toHaveBeenCalledWith('d_b')
  })

  it('时间按本地时区、与时间线默认名同一写法（补零、24 小时制）', () => {
    expect(formatStamp(new Date(2026, 0, 3, 7, 4))).toBe('01-03 07:04')
    expect(formatStamp(new Date(2026, 11, 31, 23, 59))).toBe('12-31 23:59')
  })

  it('英文：单复数分开（1 figure / 2 figures）', async () => {
    await act(async () => {
      await i18n.changeLanguage('en-US')
    })
    const items = await render([alt({ id: 'd_a', at: AT_A, figures: 1 }), alt({ id: 'd_b', at: AT_B, figures: 2 })])
    expect(items[0].textContent).toContain('11-25 13:47 · 1 figure')
    expect(items[0].textContent).not.toContain('figures')
    expect(items[1].textContent).toContain('11-24 09:05 · 2 figures')
  })

  it('拿不到的那一半不写；两半都没有就没有这一行', async () => {
    const items = await render([
      alt({ id: 'd_t', at: AT_A }),
      alt({ id: 'd_f', figures: 3 }),
      alt({ id: 'd_n' }),
    ])
    expect(items[0].textContent).toBe('打开“排版一”11-25 13:47')
    expect(items[1].textContent).toBe('打开“排版一”3 张图')
    expect(items[2].textContent).toBe('打开“排版一”')
  })
})
