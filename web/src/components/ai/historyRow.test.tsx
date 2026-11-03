/**
 * 任务历史的一行（2026-10-28 重做）：每一行同一个版式，失败只差红点 + 「失败」，原因只在详情卡里；
 * 详情卡点元信息行打开（钉住）；动作藏在行内但键盘仍够得着；「技术详情」折叠已去掉。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchAiHistory, type AiHistoryEntry } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { TaskHistory, TaskHistoryPopover } from './TaskHistory'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAiHistory: vi.fn(),
  fetchExecTrace: vi.fn().mockResolvedValue({ available: false, reason: 'not_found' }),
}))

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const base = {
  id: 'abc123',
  prompt: '把线改成红色',
  target: null,
  scope: 'figure',
  provider: 'codex',
  model: null,
  effort: 'low',
  status: 'done',
  changed: true,
  error: null,
  revert_available: true,
  pinned: false,
  started_ms: 1_756_000_000_000,
  script: 'sub/fig1.py',
} as unknown as AiHistoryEntry

let root: Root
let host: HTMLDivElement
const mount = async (entries: Partial<AiHistoryEntry>[]) => {
  vi.mocked(fetchAiHistory).mockResolvedValue({ sessions: entries.map((e) => ({ ...base, ...e })), total: entries.length } as never)
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
}
afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
  vi.clearAllMocks()
})
const q = (sel: string, el: ParentNode = document) => el.querySelector<HTMLElement>(sel)

describe('任务历史行', () => {
  beforeEach(() => {
    vi.mocked(fetchAiHistory).mockReset()
  })

  it('一行元信息：状态点 + 词；整张图不写范围；子图写出来；不再有「技术详情」', async () => {
    await mount([{ id: 'a' }, { id: 'b', scope: 'axes', target: '子图 b', changed: false }])
    const rows = [...document.querySelectorAll('[data-ai-history-row]')]
    expect(q('[data-ai-history-state]', rows[0])!.textContent).toBe('有改动')
    expect(q('[data-ai-history-dot]', rows[0])!.getAttribute('data-ai-history-dot')).toBe('ok')
    expect(q('[data-ai-history-scope]', rows[0])).toBeNull()
    expect(q('[data-ai-history-scope]', rows[1])!.textContent).toBe('子图 b')
    expect(q('[data-ai-history-state]', rows[1])!.textContent).toBe('没有改动')
    expect(document.body.textContent).not.toContain('技术详情')
  })

  it('失败的行版式不变：红点 + 失败，原因不在行内，点元信息行才在详情卡第一行', async () => {
    await mount([{ id: 'f', status: 'failed', changed: false, error: '找不到包 zz9' }])
    const row = q('[data-ai-history-row]')!
    expect(q('[data-ai-history-dot]', row)!.getAttribute('data-ai-history-dot')).toBe('danger')
    expect(q('[data-ai-history-state]', row)!.textContent).toBe('失败')
    expect(row.textContent).not.toContain('找不到包')
    await act(async () => q('[data-ai-history-details]', row)!.click())
    const card = document.querySelector('[data-ai-history-card="details"]')!
    expect(card.firstElementChild!.hasAttribute('data-ai-history-error')).toBe(true)
    // 认不出的错误串不原样露出，换成通用句
    expect(card.textContent).not.toContain('找不到包 zz9')
    expect(card.textContent).toContain('AI 这次运行出错了。')
    expect(card.textContent).toContain('fig1.py')
    expect(q('[data-ai-history-trace]', card as HTMLElement)).not.toBeNull()
    // 再点一次收起
    await act(async () => q('[data-ai-history-details]', row)!.click())
    expect(document.querySelector('[data-ai-history-card="details"]')).toBeNull()
  })

  it('失败原因是闭集码：详情卡显示翻译后的句子，不露原始码', async () => {
    await mount([{ id: 'q', status: 'failed', changed: false, error: 'agent_quota' }])
    await act(async () => q('[data-ai-history-details]')!.click())
    const err = q('[data-ai-history-error]')!
    expect(err.textContent).toBe('模型服务额度不足或正在限流，稍后再试。')
    expect(err.textContent).not.toContain('agent_quota')
  })

  it('置顶 / 回滚 / 删除仍在行内（悬停或聚焦才显形，键盘够得着）', async () => {
    await mount([{ id: 'a', pinned: true }])
    const actions = q('[data-ai-history-actions]')!
    expect(actions.className).toContain('group-focus-within/hrow:opacity-100')
    for (const s of ['pin', 'revert', 'delete']) expect(q(`[data-ai-history-${s}]`, actions)).not.toBeNull()
    expect(q('[data-ai-history-pinned]')).not.toBeNull()
  })

  it('Agent 标无框；点它开模型卡：型号没记就写「跟随 CLI 默认」', async () => {
    await mount([{ id: 'a' }])
    const badge = q('[data-ai-history-agent]')!
    expect(badge.className).not.toMatch(/border|bg-/)
    await act(async () => badge.click())
    const card = document.querySelector('[data-ai-history-card="model"]')!
    expect(q('[data-ai-history-model]', card as HTMLElement)!.textContent).toBe('跟随 CLI 默认')
  })

  it('点「查看执行记录」：详情卡和任务历史弹层先收起，宽卡由常驻宿主画（不被浮层压住、不随那一行消失）', async () => {
    vi.mocked(fetchAiHistory).mockResolvedValue({ sessions: [base], total: 1 } as never)
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(
        <TooltipProvider>
          <TaskHistoryPopover />
        </TooltipProvider>,
      )
    })
    await act(async () => q('[data-ai-history-trigger]')!.click())
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 0))
    })
    expect(q('[data-ai-history]')).not.toBeNull()
    await act(async () => q('[data-ai-history-details]')!.click())
    expect(q('[data-ai-history-card="details"]')).not.toBeNull()
    await act(async () => q('[data-ai-history-trace]')!.click())
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 0))
    })
    expect(q('[data-dialog="exec-trace"]')).not.toBeNull()
    expect(q('[data-ai-history]')).toBeNull()
    expect(q('[data-ai-history-card]')).toBeNull()
    // 关掉宽卡：宿主退场，不留残余
    await act(async () => q('[data-ai-trace-close]')!.click())
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 0))
    })
    expect(q('[data-dialog="exec-trace"]')).toBeNull()
  })
})
