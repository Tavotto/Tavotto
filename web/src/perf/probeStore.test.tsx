/**
 * 报告保存失败时说实话（ADR 0075；#504 评审）。
 *
 * 主语：**桌面版**、`POST /api/perf/report` 没接住的那一刻，界面上说的是什么、
 * 报告还在不在。桌面壳的 WKWebView 会静默取消 `<a download>`，那条退路在桌面上
 * 等于什么都没存——回一个文件名就是谎称「已保存」。
 *
 * 反证：把 `saveReport` 里 `if (isDesktop()) return null` 删掉（修复前的写法）→
 * 「桌面版后端失败」两条必红（提交前手工跑过）。
 */
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PerfProbeHud } from '@/components/PerfProbeHud'
import { useInteractionStore } from '@/store/interactionStore'
import { usePerfProbeStore } from './probeStore'
import { cancelProbe, saveReport, type PerfReport } from './session'

vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  revealExportedFile: vi.fn(async () => true),
}))

/** 报告端点的行为；系统事实端点一律回空（与本组无关） */
let reportReply: () => Promise<Response>
const clicks = vi.fn()

beforeEach(() => {
  reportReply = () => Promise.reject(new TypeError('offline'))
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) =>
      String(url).includes('/api/perf/report')
        ? reportReply()
        : Promise.resolve(new Response('{}', { status: 200 })),
    ),
  )
  clicks.mockClear()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(clicks)
  URL.createObjectURL = vi.fn(() => 'blob:mock/report')
  URL.revokeObjectURL = vi.fn()
})

afterEach(() => {
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
  usePerfProbeStore.getState().discard()
  cancelProbe()
  useInteractionStore.getState().end()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const asDesktop = () => {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
}
const REPORT = { schema: 'tavotto-perf-probe/1', segments: [] } as unknown as PerfReport

describe('saveReport', () => {
  it('桌面版后端失败：回 null，不走浏览器下载', async () => {
    asDesktop()
    expect(await saveReport(REPORT)).toBeNull()
    reportReply = () => Promise.resolve(new Response('disk full', { status: 507 }))
    expect(await saveReport(REPORT)).toBeNull()
    expect(clicks).not.toHaveBeenCalled()
  })

  it('浏览器模式后端失败：照旧给一份下载（那里下载是真的会落地的）', async () => {
    const saved = await saveReport(REPORT)
    expect(saved?.dir).toBeNull()
    expect(saved?.name).toMatch(/^tavotto-perf-.*\.json$/)
    expect(clicks).toHaveBeenCalledTimes(1)
  })

  it('桌面版后端写好了：回后端的名字与目录，不另下载', async () => {
    asDesktop()
    reportReply = () =>
      Promise.resolve(Response.json({ dir: '/data/perf-reports', name: 'tavotto-perf-x.json' }))
    expect(await saveReport(REPORT)).toEqual({ dir: '/data/perf-reports', name: 'tavotto-perf-x.json' })
    expect(clicks).not.toHaveBeenCalled()
  })
})

describe('探针面板：桌面版保存失败', () => {
  it('不说「已保存」，报告留着，重试成功后才给文件名', async () => {
    asDesktop()
    const probe = usePerfProbeStore.getState
    expect(probe().start()).toBe(true)
    useInteractionStore.getState().begin('move')
    useInteractionStore.getState().end()
    await probe().finish()

    expect(probe().phase).toBe('done')
    expect(probe().notice).toBe('save_failed')
    expect(probe().result).toBeNull()
    expect(probe().unsaved?.segments.length).toBe(1)

    // 面板上是失败 + 重试，不是「报告已保存」
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    await act(async () => root.render(<PerfProbeHud />))
    expect(host.querySelector('[role="alert"]')).not.toBeNull()
    expect(host.querySelector('[data-perf-action="retry-save"]')).not.toBeNull()
    expect(host.querySelector('[data-perf-action="reveal"]')).toBeNull()

    reportReply = () =>
      Promise.resolve(Response.json({ dir: '/data/perf-reports', name: 'tavotto-perf-y.json' }))
    await act(async () => {
      ;(host.querySelector('[data-perf-action="retry-save"]') as HTMLButtonElement).click()
    })
    expect(probe().notice).toBeNull()
    expect(probe().unsaved).toBeNull()
    expect(probe().result?.file).toBe('tavotto-perf-y.json')
    expect(host.querySelector('[data-perf-action="retry-save"]')).toBeNull()
    expect(host.querySelector('[data-perf-action="reveal"]')?.textContent).toBe('tavotto-perf-y.json')
    act(() => root.unmount())
    host.remove()
  })
})
