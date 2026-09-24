/**
 * 报告保存失败时说实话（ADR 0075；#504 评审）。
 *
 * 主语：**桌面版**、`POST /api/perf/report` 没接住的那一刻，界面上说的是什么、
 * 报告还在不在。桌面壳的 WKWebView 会静默取消 `<a download>`，那条退路在桌面上
 * 等于什么都没存——回一个文件名就是谎称「已保存」。
 *
 * 反证：把 `saveReport` 里 `if (isDesktop()) return null` 删掉（修复前的写法）→
 * 「桌面版后端失败」两条必红；删掉 HUD 上的 `data-perf-notice` → 面板那条红；
 * 去掉 probeStore 的保存代数判断 → 「过时的结果」三条红（提交前手工跑过）。
 */
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PerfProbeHud } from '@/components/PerfProbeHud'
import { t as translate } from '@/i18n'
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
    // 页面上另有一个 alert（排在前面）：判据必须指向保存失败那一句，而不是「第一个 alert」
    const decoy = document.createElement('p')
    decoy.setAttribute('role', 'alert')
    decoy.textContent = '别处的提示'
    document.body.prepend(decoy)
    await act(async () => root.render(<PerfProbeHud />))
    expect(document.querySelector('[role="alert"]')).toBe(decoy) // 按 role 找会找错——这正是要防的
    const notice = document.querySelector('[data-perf-notice="save-failed"]')
    expect(notice?.textContent).toBe(translate('perfProbe.saveFailed', { ns: 'dialogs' }))
    expect(notice?.getAttribute('role')).toBe('alert')
    decoy.remove()
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

/** 手动放行的一次报告请求 */
function deferReply(): { resolve: (r: Response) => void; reject: () => void } {
  let resolve!: (r: Response) => void
  let reject!: () => void
  const p = new Promise<Response>((res, rej) => {
    resolve = res
    reject = () => rej(new TypeError('offline'))
  })
  reportReply = () => p
  return { resolve, reject }
}

const ok = (name: string) => Response.json({ dir: '/data/perf-reports', name })

/** 桌面版，录了一段、第一次保存失败，停在「没保存 + 重试」 */
async function failedOnce() {
  asDesktop()
  const probe = usePerfProbeStore.getState
  expect(probe().start()).toBe(true)
  useInteractionStore.getState().begin('move')
  useInteractionStore.getState().end()
  await probe().finish()
  expect(probe().notice).toBe('save_failed')
  return probe
}

describe('探针面板：过时的保存结果不许落到界面', () => {
  it('点了重试、请求还没回来就关掉面板：回来的结果不许把面板重新打开', async () => {
    const probe = await failedOnce()
    const req = deferReply()
    const pending = probe().retrySave()
    probe().close()
    req.resolve(ok('tavotto-perf-late.json'))
    await pending
    expect(probe().phase).toBe('off')
    expect(probe().result).toBeNull()
  })

  it('完成并保存还在路上就放弃：回来的失败不许把面板重新打开', async () => {
    asDesktop()
    const probe = usePerfProbeStore.getState
    expect(probe().start()).toBe(true)
    useInteractionStore.getState().begin('move')
    useInteractionStore.getState().end()
    const req = deferReply()
    const pending = probe().finish()
    // finishProbe 本身要等系统事实：放一拍让它走到保存请求上
    await new Promise((r) => setTimeout(r, 0))
    probe().discard()
    req.reject()
    await pending
    expect(probe().phase).toBe('off')
    expect(probe().notice).toBeNull()
    expect(probe().unsaved).toBeNull()
  })

  it('连点两次重试：先发的那次晚回来的失败，不许盖掉后发那次的成功', async () => {
    const probe = await failedOnce()
    const first = deferReply()
    const p1 = probe().retrySave()
    const second = deferReply()
    const p2 = probe().retrySave()
    second.resolve(ok('tavotto-perf-ok.json'))
    await p2
    first.reject()
    await p1
    expect(probe().phase).toBe('done')
    expect(probe().notice).toBeNull()
    expect(probe().result?.file).toBe('tavotto-perf-ok.json')
  })
})
