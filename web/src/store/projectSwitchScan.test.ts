/**
 * 导入即扫描接在项目的**统一认领**与**启动恢复**上（T02）：不是只改一个 picker 按钮。
 *
 * * `adoptOpenedProject`（打开 / 切换 / 教程换画布都走它）认领完成后开始（或复用）扫描，且不阻塞认领；
 * * 启动恢复（`init()` 发现项目还开着）同一个入口；
 * * 教程副本不扫；
 * * A→B→A：旧项目的扫描响应晚到，绝不落进新项目的 store。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { setCurrentProjectId } from '@/lib/session'
import { resetProjectScanBookkeeping, useProjectScanStore } from './projectScanStore'
import { useProjectStore } from './projectStore'
import { useUiStore } from './uiStore'

interface Call {
  url: string
  method: string
  body: string
}
const calls: Call[] = []
let scanGate: Promise<void> = Promise.resolve()
let scanBody: (url: string) => unknown = () => ({})

const scanSnapshot = (pj: string, scanId: string, over: Record<string, unknown> = {}) => ({
  scan_version: 1,
  project_id: pj,
  scan_id: scanId,
  epoch: 1,
  observation_seq: 1,
  reason: 'claim',
  state: 'complete',
  phase: 'awaiting_confirmation',
  outcome: { kind: 'target_found' },
  budget: { entries: 1, scripts: 1, assets: 0, elapsed_s: 0 },
  issues: [],
  checks: [],
  actions: [],
  ...over,
})

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  calls.push({ url: u, method: init?.method ?? 'GET', body: String(init?.body ?? '') })
  if (u.includes('/api/project/scan')) {
    await scanGate
    return new Response(JSON.stringify(scanBody(u)), { status: 200 })
  }
  const body = u.includes('/api/engine/environment')
    ? { ok: true, python: '/p', source: 'system', project: { open: true, workdir: { mode: 'sandbox', modes: [] } } }
    : u.includes('/api/projects/recent')
      ? { recent: [] }
      : u.includes('/api/projects')
        ? { projects: [], default: null }
        : u.includes('/api/panels')
          ? { figures_dir: '/new', panels: [] }
          : u.includes('/api/project')
            ? { open: true, id: 'p1', name: 'one', path: '/one', writable: true }
            : {}
  return new Response(JSON.stringify(body), { status: 200 })
}) as typeof fetch

const scanCalls = () => calls.filter((c) => c.url.includes('/api/project/scan'))
const adopt = (id: string, extra: Record<string, unknown> = {}) =>
  useProjectStore
    .getState()
    .adoptOpenedProject({ id, path: `/${id}`, name: id, writable: true, open: true, ...extra } as never)

beforeEach(() => {
  calls.length = 0
  scanGate = Promise.resolve()
  scanBody = (u) => scanSnapshot(u.includes('pj=p2') ? 'p2' : 'p1', u.includes('pj=p2') ? 's-b' : 's-a')
  resetProjectScanBookkeeping()
  useProjectScanStore.getState().clear()
  setCurrentProjectId('p1')
})
afterEach(() => {
  useProjectScanStore.getState().clear()
  useUiStore.setState({ scanPanelOpen: false })
})

describe('统一认领完成后开始扫描', () => {
  it('认领新项目 → 向新项目 POST /api/project/scan（reason=claim），不阻塞认领', async () => {
    scanGate = new Promise(() => {}) // 扫描永远不返回：认领也必须完成
    await adopt('p2')
    const posts = scanCalls().filter((c) => c.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0].url).toContain('pj=p2')
    expect(JSON.parse(posts[0].body)).toEqual({ force: false, reason: 'claim' })
    expect(useProjectStore.getState().phase).toBe('open')
  })

  it('教程副本不扫', async () => {
    await adopt('t1', { tutorial: true })
    expect(scanCalls()).toHaveLength(0)
  })

  it('扫描结果落进 store，展开状态随项目换代复位', async () => {
    useUiStore.setState({ scanPanelOpen: true })
    await adopt('p2')
    await new Promise((r) => setTimeout(r, 0))
    expect(useProjectScanStore.getState().scan?.scan_id).toBe('s-b')
    expect(useUiStore.getState().scanPanelOpen).toBe(false)
  })
})

describe('A→B→A 晚到响应', () => {
  it('A 的扫描响应在切到 B 之后才回来：不落进 B', async () => {
    let release!: () => void
    scanGate = new Promise<void>((r) => (release = r))
    scanBody = () => scanSnapshot('p1', 's-a-late')
    await adopt('p1')
    await new Promise((r) => setTimeout(r, 0)) // A 的 POST 已发出，正挂在闸门上

    scanGate = Promise.resolve()
    scanBody = () => scanSnapshot('p2', 's-b')
    await adopt('p2')
    await new Promise((r) => setTimeout(r, 0))
    expect(useProjectScanStore.getState().scan?.scan_id).toBe('s-b')

    release() // A 的响应此刻才回来
    await new Promise((r) => setTimeout(r, 0))
    expect(useProjectScanStore.getState().scan?.scan_id).toBe('s-b')
  })
})

describe('启动恢复', () => {
  it('init() 发现项目还开着：同一个入口开始扫描（reason=restore）', async () => {
    await useProjectStore.getState().init()
    await new Promise((r) => setTimeout(r, 0))
    const posts = scanCalls().filter((c) => c.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(JSON.parse(posts[0].body)).toEqual({ force: false, reason: 'restore' })
  })
})
