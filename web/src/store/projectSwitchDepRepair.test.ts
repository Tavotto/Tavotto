/**
 * 换项目时依赖修复的状态必须换代（issue #590）。
 *
 * 改造前 `resetForNewProject()` 不碰 `depRepairStore`：A 项目的缺包计划、装包进度、联合计划、钉住的
 * 解释器会原样开在 B 上，B 上点「安装」拿的是 A 的计划；A 那边在途的请求 / SSE 进度也照样落进 B。
 *
 * 分三件事，各由一条判据负责（拆掉任何一条都有用例红）：
 *
 *   * **已经落地的**那份：`clear()` 把计划 / 错误 / 钉住的解释器 / 此刻显示的进度清掉；
 *   * **还在飞的**请求：`clear()` 换代，A 的响应回来时作废；
 *   * **作业本身不取消**：后端 `close_project` 不碰装包线程、结果按计划自己的项目记账，所以前端也不丢
 *     作业——进度按所属项目分格，B 上看不见、终态副作用不在 B 上派发，切回 A 接得上（与
 *     `projectSwitchPackages.test.ts` 的作业那组同一形状）。
 *
 * 每条都配「同样的事发生在 A 还开着时照常落地」的对照：否则「B 上什么都没有」会因为从没人触发而恒绿。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setCurrentProjectId } from '@/lib/session'
import type { DependencyPreparationOffer, DependencyProgress, DependencyRepairPlan } from '@/lib/api'
import { useDepRepairStore } from './depRepairStore'
import { useEnvStore } from './envStore'
import { useProjectStore } from './projectStore'
import { useRenderStore } from './renderStore'

/** 被扣住的请求：url 片段 → 放行函数（由用例决定何时、回什么） */
const held = new Map<string, (body: unknown, status?: number) => void>()
const holding = new Set<string>()
const calls: { url: string; body: unknown }[] = []

const PLAN_A: DependencyRepairPlan = {
  plan_id: 'plan-a',
  module: 'lmfit',
  distribution: 'lmfit',
  target_kind: 'tavotto_managed',
} as unknown as DependencyRepairPlan

const respond = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  calls.push({ url: u, body: typeof init?.body === 'string' ? JSON.parse(init.body) : null })
  for (const part of holding) {
    if (u.includes(part)) {
      return new Promise<Response>((resolve) => {
        held.set(part, (body, status = 200) => resolve(respond(body, status)))
      })
    }
  }
  if (u.includes('/api/engine/dependency/plan')) return respond({ plan: PLAN_A })
  if (u.includes('/api/engine/dependency/install'))
    return respond({ started: true, plan_id: 'plan-a', state: 'installing', log: '', error: null, code: '' })
  const body = u.includes('/api/engine/environment')
    ? { ok: true, python: '/p', source: 'system', project: { open: true } }
    : u.includes('/api/projects/recent')
      ? { recent: [] }
      : u.includes('/api/projects/open')
        ? []
        : u.includes('/api/panels')
          ? { figures_dir: '/new', panels: [] }
          : {}
  return respond(body)
}) as typeof fetch

const switchTo = (id: string) =>
  useProjectStore.getState().adoptOpenedProject({ id, path: `/${id}`, name: id, writable: true, open: true } as never)

const progress = (state: DependencyProgress['state'], over: Partial<DependencyProgress> = {}): DependencyProgress => ({
  plan_id: 'plan-a',
  state,
  log: '',
  error: null,
  code: '',
  ...over,
})

const offer = (script: string): DependencyPreparationOffer =>
  ({
    code: 'dependency_preparation_required',
    script,
    plan: { status: 'ready', missing: [], requirements: ['lmfit'] },
    target_kind: 'tavotto_managed',
    targets: [],
    rounds_remaining: 3,
    skipped: false,
    user_environments: [],
  }) as unknown as DependencyPreparationOffer

/** A 上：形成计划、点安装、SSE 推到 installing——此刻 A 的界面上有计划与进度 */
async function startInstallOnA() {
  await useDepRepairStore.getState().makePlan({ module: 'lmfit', script: 'fig.py', target: 'tavotto_managed' })
  await useDepRepairStore.getState().install()
  useDepRepairStore.getState().onProgress(progress('installing', { log: 'Collecting lmfit' }))
}

beforeEach(async () => {
  held.clear()
  holding.clear()
  calls.length = 0
  setCurrentProjectId('p1')
  // 上一个用例可能把作业收进了某个项目那格：先整份换干净，再回到 p1
  useDepRepairStore.setState({ parked: {} })
  useDepRepairStore.getState().reset()
  useEnvStore.setState({ dependencyPreparation: null })
})
afterEach(() => vi.restoreAllMocks())

describe('换项目时的依赖修复状态（issue #590）', () => {
  it('已经落地的计划 / 进度 / 错误 / 钉住的解释器：切到 B 全都不在', async () => {
    await startInstallOnA()
    useDepRepairStore.setState({
      pinned: { python: '/usr/bin/python3', source: 'configured', variable: '' } as never,
      jointBlocked: { status: 'blocked' } as never,
      errorCode: 'dependency_install_failed',
      errorText: 'A 的错误',
    })
    const before = useDepRepairStore.getState()
    // 尺子是活的：切之前 A 的东西确实都在
    expect(before.plan?.plan_id).toBe('plan-a')
    expect(before.progress?.state).toBe('installing')
    expect(before.pinned).not.toBeNull()

    await switchTo('p2')

    const s = useDepRepairStore.getState()
    expect(s.plan).toBeNull()
    expect(s.progress).toBeNull()
    expect(s.jointPlan).toBeNull()
    expect(s.jointBlocked).toBeNull()
    expect(s.pinned).toBeNull()
    expect(s.errorCode).toBe('')
    expect(s.errorText).toBe('')
    expect(s.busy).toBe(false)
  })

  it('A 的计划请求在途时切到 B：回来的计划不落进 B', async () => {
    holding.add('/api/engine/dependency/plan')
    const pending = useDepRepairStore.getState().makePlan({ module: 'lmfit', script: 'fig.py', target: 'tavotto_managed' })
    await vi.waitFor(() => expect(held.has('/api/engine/dependency/plan')).toBe(true))
    await switchTo('p2')
    held.get('/api/engine/dependency/plan')?.({ plan: PLAN_A })
    await pending
    expect(useDepRepairStore.getState().plan).toBeNull()
  })

  it('对照：A 还开着时同一次计划请求照常落地', async () => {
    holding.add('/api/engine/dependency/plan')
    const pending = useDepRepairStore.getState().makePlan({ module: 'lmfit', script: 'fig.py', target: 'tavotto_managed' })
    await vi.waitFor(() => expect(held.has('/api/engine/dependency/plan')).toBe(true))
    held.get('/api/engine/dependency/plan')?.({ plan: PLAN_A })
    await pending
    expect(useDepRepairStore.getState().plan?.plan_id).toBe('plan-a')
  })

  it('A 的进度在切到 B 之后晚到：B 上不显示、终态副作用不在 B 上派发', async () => {
    await startInstallOnA()
    await switchTo('p2')
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    const refresh = vi.spyOn(useEnvStore.getState(), 'refresh')
    useEnvStore.getState().requestDependencyPreparation(offer('b.py'))

    useDepRepairStore.getState().onProgress(progress('verifying'))
    expect(useDepRepairStore.getState().progress).toBeNull()
    useDepRepairStore.getState().onProgress(progress('done', { flow: 'joint' }))
    expect(useDepRepairStore.getState().progress).toBeNull()
    expect(retry).not.toHaveBeenCalled()
    expect(refresh).not.toHaveBeenCalled()
    expect(useEnvStore.getState().dependencyPreparation?.script).toBe('b.py')
  })

  it('对照：A 还开着时同一条终态照常派发（刷环境 + 重排渲染）', async () => {
    await startInstallOnA()
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    const refresh = vi.spyOn(useEnvStore.getState(), 'refresh')
    useDepRepairStore.getState().onProgress(progress('done'))
    expect(useDepRepairStore.getState().progress?.state).toBe('done')
    expect(retry).toHaveBeenCalledTimes(1)
    expect(refresh).toHaveBeenCalled()
  })

  it('A 的装包作业切项目不取消：不发取消请求，切回 A 接得上（带着切走期间的进度）', async () => {
    await startInstallOnA()
    await switchTo('p2')
    useDepRepairStore.getState().onProgress(progress('verifying', { log: 'import lmfit' }))
    expect(calls.some((c) => c.url.includes('/cancel'))).toBe(false)

    await switchTo('p1')
    const s = useDepRepairStore.getState()
    expect(s.progress?.plan_id).toBe('plan-a')
    expect(s.progress?.state).toBe('verifying')
    // 接回来之后照常收进度、终态照常派发
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    useDepRepairStore.getState().onProgress(progress('done'))
    expect(useDepRepairStore.getState().progress?.state).toBe('done')
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('切走期间 A 的作业失败了：切回 A 时结局与错误交出来，不静默丢', async () => {
    await startInstallOnA()
    await switchTo('p2')
    useDepRepairStore.getState().onProgress(progress('failed', { code: 'dependency_install_failed', error: 'pip 失败' }))
    expect(useDepRepairStore.getState().errorCode).toBe('')
    await switchTo('p1')
    const s = useDepRepairStore.getState()
    expect(s.progress?.state).toBe('failed')
    expect(s.errorCode).toBe('dependency_install_failed')
  })

  it('A 的安装请求在切项目之后才被拒：A 那格记成失败，B 不动', async () => {
    await useDepRepairStore.getState().makePlan({ module: 'lmfit', script: 'fig.py', target: 'tavotto_managed' })
    holding.add('/api/engine/dependency/install')
    const pending = useDepRepairStore.getState().install()
    await vi.waitFor(() => expect(held.has('/api/engine/dependency/install')).toBe(true))
    await switchTo('p2')
    held.get('/api/engine/dependency/install')?.({ error: '拒绝', code: 'dependency_install_not_allowed' }, 409)
    await pending
    expect(useDepRepairStore.getState().errorCode).toBe('')
    expect(useDepRepairStore.getState().progress).toBeNull()
    await switchTo('p1')
    const s = useDepRepairStore.getState()
    expect(s.progress?.state).toBe('failed')
    expect(s.errorCode).toBe('dependency_install_not_allowed')
  })

  it('A 的「改用这个环境」在途时切到 B：A 的环境状态不写进 B、不关 B 的框、不重排 B 的渲染', async () => {
    holding.add('/api/engine/environment')
    const pending = useDepRepairStore.getState().adoptUserEnvironment('env-a', 'a.py')
    await vi.waitFor(() => expect(held.has('/api/engine/environment')).toBe(true))
    holding.clear()
    await switchTo('p2')
    useEnvStore.getState().requestDependencyPreparation(offer('b.py'))
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    held.get('/api/engine/environment')?.({ ok: true, project: { open: true, python: '/envs/a/bin/python' } })
    await pending
    expect(useEnvStore.getState().dependencyPreparation?.script).toBe('b.py')
    expect(retry).not.toHaveBeenCalled()
    expect(JSON.stringify(useEnvStore.getState().env ?? {})).not.toContain('/envs/a')
  })
})
