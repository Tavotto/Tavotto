/**
 * 「记住修好的做法」（ADR 0197 第 3 层·在线）：
 * ① 老后端（没有 /api/ai/fix-memory 或回包形状不对）→ 整行不存在；
 * ② 有记录 → 一行：开关 + 「已记住 N 条」+「查看」；没有记录 → 只有开关；
 * ③「查看」开列表：每条一句「哪类问题 → 怎么修好的」，路径 / 参数用等宽；逐条删调 DELETE /:id；
 * ④「全部删除」先在弹窗里原地确认，取消就不删（不弹全局确认框：会把列表弹窗当成点了外面关掉）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchFixMemory: vi.fn(),
  setFixMemoryEnabled: vi.fn(),
  deleteFixMemory: vi.fn(),
}))

import { deleteFixMemory, fetchFixMemory, type FixMemoryEntry, type FixMemoryState } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { FixMemoryRow, fixMemoryAction } from './FixMemoryRow'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const entry = (over: Partial<FixMemoryEntry> = {}): FixMemoryEntry => ({
  id: 'aaaaaaaaaaaa',
  shape: 'script_needs_arguments',
  kind: 'run_args',
  fields: { args: ['--data', 'results/run3.csv'] },
  script: 'make_fig3.py',
  created: 1_795_000_000,
  last_used: 1_795_000_000,
  times: 2,
  ...over,
})

let host: HTMLDivElement
let root: Root

async function mount(state: unknown) {
  vi.mocked(fetchFixMemory).mockResolvedValue(state as FixMemoryState)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <FixMemoryRow />
      </TooltipProvider>,
    )
  })
}

const q = (sel: string) => document.querySelector(sel) as HTMLElement | null

beforeEach(() => {
  vi.mocked(fetchFixMemory).mockReset()
  vi.mocked(deleteFixMemory).mockReset()
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

describe('FixMemoryRow', () => {
  it('老后端或回包形状不对：整行不存在', async () => {
    await mount({ checks: [] })
    expect(q('[data-fix-memory]')).toBeNull()
  })

  it('没有记录：只有开关，没有「查看」', async () => {
    await mount({ enabled: true, entries: [] })
    expect(q('[data-fix-memory] [role="switch"]')).toBeTruthy()
    expect(q('[data-fix-memory-view]')).toBeNull()
  })

  it('查看列表、逐条删除', async () => {
    await mount({
      enabled: true,
      entries: [
        entry(),
        entry({
          id: 'bbbbbbbbbbbb',
          kind: 'execution',
          shape: 'safe_file_operation_blocked',
          fields: { mode: 'native' },
        }),
      ],
    })
    await act(async () => q('[data-fix-memory-view]')!.click())
    const first = q('[data-fix-memory-entry="aaaaaaaaaaaa"]')!
    expect(first.querySelector('code')?.textContent).toBe('--data results/run3.csv')
    vi.mocked(deleteFixMemory).mockResolvedValue({
      enabled: true,
      entries: [],
    })
    await act(async () => q('[data-fix-memory-delete="aaaaaaaaaaaa"]')!.click())
    expect(deleteFixMemory).toHaveBeenCalledWith('aaaaaaaaaaaa')
    expect(q('[data-fix-memory-confirm]')).toBeNull() // 逐条删不问
  })

  it('全部删除先在弹窗里原地确认，取消就不删', async () => {
    await mount({ enabled: true, entries: [entry()] })
    await act(async () => q('[data-fix-memory-view]')!.click())
    await act(async () => q('[data-fix-memory-clear]')!.click())
    expect(q('[data-fix-memory-confirm]')).toBeTruthy()
    expect(deleteFixMemory).not.toHaveBeenCalled()
    await act(async () => q('[data-confirm-cancel]')!.click())
    expect(q('[data-fix-memory-confirm]')).toBeNull()
    expect(q('[data-fix-memory-list]')).toBeTruthy() // 列表弹窗还开着
    vi.mocked(deleteFixMemory).mockResolvedValue({ enabled: true, entries: [] })
    await act(async () => q('[data-fix-memory-clear]')!.click())
    await act(async () => q('[data-confirm-ok]')!.click())
    expect(deleteFixMemory).toHaveBeenCalledWith(undefined)
    expect(q('[data-fix-memory-empty]')).toBeTruthy()
  })

  it('像凭据的变量只显示名字', () => {
    const text = fixMemoryAction(
      entry({
        kind: 'launcher',
        fields: {
          env: [{ name: 'LAB_DATA_DIR', value: '~/lab' }, { name: 'API_TOKEN' }],
        },
      }),
    )
    expect(text).toContain('LAB_DATA_DIR=~/lab')
    expect(text).toContain('API_TOKEN')
    expect(text).not.toContain('API_TOKEN=')
  })
})
