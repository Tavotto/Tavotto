/**
 * 写回历史的空状态（审计 T32 的第一条建议）。
 *
 * 原来这里是两段：一句「还没有写回原始文件的记录」，再跟一段「用『写回原始
 * 文件』把图内修改写回文件后，这里会留下可回溯的足迹」。第二段讲的是**另一颗
 * 按钮**怎么用——那颗按钮就在同一块面板上，它自己说得清；而这块空白是用户来
 * 找「有没有可以回去的版本」时打开的，答案只有一句：暂无写回记录。
 *
 * T32 的实现改动落在 `HistoryPanel.tsx`，但当时没有任何用例执行到这条分支。
 * 这里补上，顺带钉住另外两条不能一起被简化掉的：读取中与读取失败都得说话，
 * 而**有记录时列表要真的出来**——空状态判据写反了的话，界面永远说「暂无」，
 * 而那正是用户想找回原件时最不该看到的答案。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchHistory, type HistoryVersion } from '@/lib/api'
import { t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import type { PanelObject } from '@/types/document'
import { HistoryPanel } from './HistoryPanel'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchHistory: vi.fn(),
}))

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const vh = (k: string) => t(`versionHistory.${k}`, { ns: 'inspector' })

const panel = (): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 0, y: 0, w: 100, h: 80,
    fileId: 'A.pdf', fileKind: 'pdf', nativeW: 100, nativeH: 80, overrides: [],
  }) as unknown as PanelObject

const version = (n: number): HistoryVersion =>
  ({ n, ts: '2026-09-06 14:59:00', count: 3, patches: [] }) as unknown as HistoryVersion

let root: Root
let host: HTMLDivElement

/** 历史挂在一个 Popover 里：先渲染，再点开触发按钮，最后等那次拉取落地 */
async function openHistory() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <HistoryPanel panel={panel()} />
      </TooltipProvider>,
    )
  })
  const trigger = Array.from(host.querySelectorAll('button')).find((b) =>
    b.textContent?.includes(vh('trigger')),
  )
  expect(trigger, '找不到「历史」入口').toBeTruthy()
  await act(async () => trigger!.click())
  await act(async () => {
    await new Promise<void>((r) => setTimeout(r, 0))
  })
}

/** Popover 走 Portal，落在 document.body 上 */
const text = () => document.body.textContent ?? ''

beforeEach(() => {
  document.body.innerHTML = ''
  vi.mocked(fetchHistory).mockReset()
})

afterEach(async () => {
  await act(async () => root?.unmount())
  document.body.innerHTML = ''
})

describe('写回历史的空状态', () => {
  it('一条记录都没有时只说一句「暂无写回记录」', async () => {
    vi.mocked(fetchHistory).mockResolvedValue({ versions: [] } as never)
    await openHistory()
    expect(text()).toContain(vh('emptyTitle'))
    expect(vh('emptyTitle')).toBe('暂无写回记录')
  })

  it('空状态后面不再跟一段「写回按钮怎么用」——那颗按钮就在旁边', async () => {
    vi.mocked(fetchHistory).mockResolvedValue({ versions: [] } as never)
    await openHistory()
    // 原来那段以「用「写回原始文件」把图内修改写回文件后」起头
    expect(text()).not.toContain('可回溯的足迹')
    expect(text()).not.toContain('把图内修改写回文件后')
  })

  it('有记录时列表真的出来：起点 + 每个版本，不会被空状态吞掉', async () => {
    vi.mocked(fetchHistory).mockResolvedValue({ versions: [version(0), version(1)] } as never)
    await openHistory()
    expect(text()).not.toContain(vh('emptyTitle'))
    // 时间线起点「脚本原始」永远在，加上两个版本
    expect(text()).toContain(vh('origin'))
    expect(text()).toContain('09-06 14:59')
  })

  it('还在读的时候说「正在读取」，不先谎称「暂无」', async () => {
    // 永不落地的请求：停在读取中那一刻
    vi.mocked(fetchHistory).mockReturnValue(new Promise(() => {}) as never)
    await openHistory()
    expect(text()).toContain(vh('loading'))
    expect(text()).not.toContain(vh('emptyTitle'))
  })

  it('读不到历史时如实报错，不显示成「暂无写回记录」', async () => {
    vi.mocked(fetchHistory).mockRejectedValue(new Error('后端没起来'))
    await openHistory()
    expect(text()).toContain('后端没起来')
    expect(text()).not.toContain(vh('emptyTitle'))
  })
})
