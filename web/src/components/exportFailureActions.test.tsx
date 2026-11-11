/**
 * 导出失败的出口：目录写不了 → 「换个位置导出」（选完直接重新导出）；位图太大 → 「改用 N dpi 导出」，
 * 算不出档位退回「发送问题反馈」。同目录重试必败，所以这两类失败不再给「重试」。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const picker = vi.hoisted(() => ({ pick: vi.fn(async (_t?: string): Promise<string | null> => null) }))
vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  isDesktop: () => true,
  pickDirectory: picker.pick,
}))
const patch = vi.hoisted(() => ({ apply: vi.fn(async (_p: unknown) => undefined) }))
vi.mock('@/lib/projectSettingsPatch', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/projectSettingsPatch')>()),
  applyProjectSettingsPatch: patch.apply,
}))

import { ResultBlock } from '@/components/ExportDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useDocumentStore } from '@/store/documentStore'
import { useClipboardStore } from '@/lib/clipboard'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLDivElement
let root: Root
const failed = (code: string, params: Record<string, unknown> = {}, ppi = 600) =>
  ({
    job_id: 'j1',
    status: 'failed',
    request: { ppi },
    outputs: [],
    warnings: [],
    conflicts: [],
    error: { code, params, recoverable: true },
  }) as never
const BIG = { width: 20000, height: 20000, max_pixels: 100_000_000 }

async function render(job: unknown, extra: { onRetry?: () => void; onLowerDpi?: (d: number) => void } = {}) {
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ResultBlock job={job as never} edited={false} onRetry={extra.onRetry ?? (() => {})} currentPpi={600} onLowerDpi={extra.onLowerDpi} />
      </TooltipProvider>,
    )
  })
}
const q = (sel: string) => document.body.querySelector<HTMLButtonElement>(sel)

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  picker.pick.mockReset().mockResolvedValue(null)
  patch.apply.mockReset().mockResolvedValue(undefined)
  useDiagSendStore.setState({ capability: null })
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe.each(['export_dir_unwritable', 'tmp_dir_failed'])('%s', (code) => {
  it('主按钮是「换个位置导出」，不再给同目录「重试」', async () => {
    await render(failed(code, { error: 'x' }))
    const btn = q('[data-export-change-location]')!
    expect(btn.dataset.variant).toBe('primary')
    const direct = Array.from(document.body.querySelectorAll('[data-export-failed] button')).filter((b) => !b.closest('details'))
    expect(direct).toEqual([btn])
  })

  it('选了新文件夹：先写进项目的导出位置，再重新导出', async () => {
    const onRetry = vi.fn()
    picker.pick.mockResolvedValue('/new/place')
    await render(failed(code), { onRetry })
    await act(async () => { q('[data-export-change-location]')!.click() })
    expect(patch.apply).toHaveBeenCalledWith({ export_dir: '/new/place' })
    expect(onRetry).toHaveBeenCalledTimes(1)
    expect(patch.apply.mock.invocationCallOrder[0]).toBeLessThan(onRetry.mock.invocationCallOrder[0])
  })

  it('取消选择：什么都不变、不导出', async () => {
    const onRetry = vi.fn()
    await render(failed(code), { onRetry })
    await act(async () => { q('[data-export-change-location]')!.click() })
    expect(patch.apply).not.toHaveBeenCalled()
    expect(onRetry).not.toHaveBeenCalled()
  })

  it('后端拒绝新位置：一句话说原因，不导出', async () => {
    const onRetry = vi.fn()
    picker.pick.mockResolvedValue('/ro')
    patch.apply.mockRejectedValue(new Error('不可用'))
    await render(failed(code), { onRetry })
    await act(async () => { q('[data-export-change-location]')!.click() })
    expect(onRetry).not.toHaveBeenCalled()
    expect(document.body.querySelector('[data-export-location-error]')).not.toBeNull()
  })
})

describe('export_raster_too_large', () => {
  it('主按钮「改用 300 dpi 导出」，点下去带着 300 回调；没有「重试」', async () => {
    const onLowerDpi = vi.fn()
    await render(failed('export_raster_too_large', BIG), { onLowerDpi })
    const btn = q('[data-export-lower-dpi]')!
    expect(btn.dataset.exportLowerDpi).toBe('300')
    expect(btn.textContent).toContain('300 dpi')
    expect(btn.dataset.variant).toBe('primary')
    await act(async () => { btn.click() })
    expect(onLowerDpi).toHaveBeenCalledWith(300)
    const direct = Array.from(document.body.querySelectorAll('[data-export-failed] button')).filter((b) => !b.closest('details'))
    expect(direct).toEqual([btn])
  })

  it('以作业请求里的 dpi 为准（不是对话框此刻的值）', async () => {
    const onLowerDpi = vi.fn()
    // 1200 dpi 时 24000×24000（5.76 亿）→ 上限 1 亿：900 → 18000²=3.24 亿 仍超；600 → 12000² = 1.44 亿 超；300 → 6000² 够
    await render(failed('export_raster_too_large', { width: 24000, height: 24000, max_pixels: 100_000_000 }, 1200), { onLowerDpi })
    expect(q('[data-export-lower-dpi]')!.dataset.exportLowerDpi).toBe('300')
  })

  it('没有合法的更低档：退回「发送问题反馈」，不摆 dpi 按钮', async () => {
    useDiagSendStore.setState({ capability: { enabled: true, note_max_chars: 1000, retention_days: 30, categories: ['other'] } } as never)
    await render(failed('export_raster_too_large', { width: 90000, height: 90000, max_pixels: 100_000_000 }), { onLowerDpi: vi.fn() })
    expect(q('[data-export-lower-dpi]')).toBeNull()
    expect(q('[data-send-report]')).not.toBeNull()
  })

  it('逐项失败（一件都没写出来）时摘要行同样带这个出口', async () => {
    const onLowerDpi = vi.fn()
    const job = {
      job_id: 'j2', status: 'failed', request: { ppi: 600 }, warnings: [], conflicts: [], export_dir: '/out', error: null,
      outputs: [{ format: 'png', name: null, url: null, bytes: null, dimensions: null, vector: false, status: 'failed', replaced: false,
        error: { code: 'export_raster_too_large', params: BIG } }],
    }
    await render(job, { onLowerDpi })
    await act(async () => { q('[data-export-lower-dpi]')!.click() })
    expect(onLowerDpi).toHaveBeenCalledWith(300)
  })
})

describe('别的失败不变', () => {
  it('format_failed 仍是「重试」', async () => {
    const onRetry = vi.fn()
    await render(failed('format_failed'), { onRetry })
    expect(q('[data-export-change-location]')).toBeNull()
    expect(q('[data-export-lower-dpi]')).toBeNull()
    const direct = Array.from(document.body.querySelectorAll<HTMLButtonElement>('[data-export-failed] button')).filter((b) => !b.closest('details'))
    expect(direct).toHaveLength(1)
    await act(async () => { direct[0].click() })
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})

describe('缺图（面板指向的图不在了）', () => {
  const FIG = 'results\\case6.png'
  const missing = () => failed('export_render_failed', { id: FIG, figure: FIG, source_code: 'source_missing', reason: 'source:source_missing: x' })
  const setPanels = (fileIds: string[]) => {
    const doc = useDocumentStore.getState().doc
    useDocumentStore.setState({
      doc: { ...doc, objects: fileIds.map((fileId, i) => ({ id: `p${i}`, type: 'panel', fileId, name: 'case6' })) as never },
    })
  }
  beforeEach(() => {
    setPanels([FIG])
    useClipboardStore.setState({ pending: null })
  })

  it('一句人话 + 主按钮「重新找到这张图」，没有必败的「重试」', async () => {
    await render(missing())
    const card = document.body.querySelector('[data-export-failed]')!
    expect(card.textContent).toContain('case6.png')
    expect(card.textContent).not.toContain('source_missing')
    const btn = q('[data-export-find-source]')!
    expect(btn.dataset.variant).toBe('primary')
    const direct = Array.from(card.querySelectorAll('button')).filter((b) => !b.closest('details'))
    expect(direct).toEqual([btn])
  })

  it('点下去只把这一张交给重链接；链上了就直接重新导出', async () => {
    const onRetry = vi.fn()
    await render(missing(), { onRetry })
    await act(async () => { q('[data-export-find-source]')!.click() })
    const pending = useClipboardStore.getState().pending
    expect(pending).toMatchObject({ mode: 'relink', missing: [{ fileId: FIG, name: 'case6', count: 1 }] })
    await act(async () => {
      setPanels(['moved\\case6.png'])
      useClipboardStore.setState({ pending: null })
    })
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('取消 / 保持缺失：不导出（再导一次必败）', async () => {
    const onRetry = vi.fn()
    await render(missing(), { onRetry })
    await act(async () => { q('[data-export-find-source]')!.click() })
    await act(async () => { useClipboardStore.setState({ pending: null }) })
    expect(onRetry).not.toHaveBeenCalled()
  })

  it('单图导出的 source_missing 同一个出口', async () => {
    await render(failed('source_missing', { figure: FIG }))
    expect(q('[data-export-find-source]')).not.toBeNull()
  })

  it('排版里已经没有引用它的面板：退回「发送问题反馈」，不给点了没反应的按钮', async () => {
    setPanels([])
    await render(missing())
    expect(q('[data-export-find-source]')).toBeNull()
  })
})
