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

/**
 * zustand 的 `set` 会把被 spy 的函数抄进新 state，`restoreAllMocks` 只还原旧对象上的那个——spy 会跨用例
 * 留下来（一个用例把 `setProjectPython` 换成永不回答的替身，后面的用例就永远发不出请求）。每个用例前把
 * 原函数放回去
 */
const ENV_ORIGINAL = { ...useEnvStore.getState() }
const RENDER_ORIGINAL = { ...useRenderStore.getState() }

beforeEach(async () => {
  useEnvStore.setState({
    setProjectPython: ENV_ORIGINAL.setProjectPython,
    refresh: ENV_ORIGINAL.refresh,
  })
  useRenderStore.setState({
    retryEnvironmentFailures: RENDER_ORIGINAL.retryEnvironmentFailures,
    markStale: RENDER_ORIGINAL.markStale,
  })
  held.clear()
  holding.clear()
  calls.length = 0
  setCurrentProjectId('p1')
  // 上一个用例可能把作业收进了某个项目那格：先整份换干净，再回到 p1
  useDepRepairStore.setState({ parked: {}, rebuildRunning: false })
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

  it('A 的联合计划绑定在途时切到 B：绑定回来不在 B 上执行，也不显示', async () => {
    useEnvStore.getState().requestDependencyPreparation(offer('a.py'))
    holding.add('/api/engine/dependencies/plan')
    const pending = useDepRepairStore.getState().prepare('tavotto_managed')
    await vi.waitFor(() => expect(held.has('/api/engine/dependencies/plan')).toBe(true))
    await switchTo('p2')
    held.get('/api/engine/dependencies/plan')?.({ plan: { plan_id: 'jp-a', requirements: ['lmfit'] } })
    await pending
    expect(useDepRepairStore.getState().jointPlan).toBeNull()
    expect(useDepRepairStore.getState().progress).toBeNull()
    expect(calls.some((c) => c.url.includes('/api/engine/dependencies/prepare'))).toBe(false)
  })

  it('A 的「不准备直接运行」在途时切到 B：不关 B 的框、不重排 B 的渲染', async () => {
    useEnvStore.getState().requestDependencyPreparation(offer('a.py'))
    holding.add('/api/engine/dependencies/skip')
    const pending = useDepRepairStore.getState().skipPreparation()
    await vi.waitFor(() => expect(held.has('/api/engine/dependencies/skip')).toBe(true))
    await switchTo('p2')
    useEnvStore.getState().requestDependencyPreparation(offer('b.py'))
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    held.get('/api/engine/dependencies/skip')?.({ ok: true, script: 'a.py', skipped: true })
    await pending
    expect(useEnvStore.getState().dependencyPreparation?.script).toBe('b.py')
    expect(retry).not.toHaveBeenCalled()
  })

  it('A 的重建在途时切到 B：B 不显示重建进度，切回 A 接得上', async () => {
    holding.add('/api/engine/environment/managed/rebuild')
    const pending = useDepRepairStore.getState().rebuildManaged()
    await vi.waitFor(() => expect(held.has('/api/engine/environment/managed/rebuild')).toBe(true))
    holding.clear()
    await switchTo('p2')
    held.get('/api/engine/environment/managed/rebuild')?.({ started: true, requirements: [] })
    await pending
    expect(useDepRepairStore.getState().progress).toBeNull()
    await switchTo('p1')
    expect(useDepRepairStore.getState().progress?.plan_id).toBe('managed-rebuild')
  })

  it('A 的「改用系统解释器」在途时切到 B：不重排 B 的渲染', async () => {
    let release: (v: string | null) => void = () => {}
    vi.spyOn(useEnvStore.getState(), 'setProjectPython').mockReturnValue(
      new Promise<string | null>((r) => (release = r)),
    )
    const pending = useDepRepairStore.getState().adoptSystemPython('/usr/bin/python3', 'lmfit')
    await switchTo('p2')
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    release(null)
    await pending
    expect(retry).not.toHaveBeenCalled()
    expect(useDepRepairStore.getState().busy).toBe(false)
  })

})

// ---------------------------------------------------------------- #605 评审第一轮

/**
 * 扣住**下一次**匹配的请求：先布置（发请求之前调），拿到它之后立刻停止扣——之后同一路径的请求（切项目时
 * 新项目的那次 refresh）照常回答
 */
function holdOnce(part: string) {
  holding.add(part)
  return vi
    .waitFor(() => expect(held.has(part)).toBe(true))
    .then(() => {
      holding.delete(part)
      return held.get(part)!
    })
}

const A_PROJECT_ENV = { open: true, python: '/envs/a/bin/python', source: 'project' }

describe('写状态的那一侧按代际判（#605 评审 P1：envStore 的在途响应）', () => {
  it('对照：A 还开着时「改用系统解释器」的响应照常写进 env（尺子是活的）', async () => {
    useEnvStore.setState({ env: { ok: true, python: '/p', source: 'system', project: { open: true } } as never })
    const armed = holdOnce('/api/engine/environment')
    const pending = useDepRepairStore.getState().adoptSystemPython('/usr/bin/python3', 'lmfit')
    const release = await armed
    release({ ok: true, project: A_PROJECT_ENV })
    await pending
    expect(JSON.stringify(useEnvStore.getState().env)).toContain('/envs/a')
  })

  it('「改用系统解释器」切项目之后才回来：A 的项目环境不写进 B', async () => {
    const armed = holdOnce('/api/engine/environment')
    const pending = useDepRepairStore.getState().adoptSystemPython('/usr/bin/python3', 'lmfit')
    const release = await armed
    await switchTo('p2')
    release({ ok: true, project: A_PROJECT_ENV })
    await pending
    expect(JSON.stringify(useEnvStore.getState().env ?? {})).not.toContain('/envs/a')
  })

  it('envStore.refresh() 切项目之后才回来：A 的 env 不覆盖 B 的', async () => {
    const armed = holdOnce('/api/engine/environment')
    const pending = useEnvStore.getState().refresh()
    const release = await armed
    await switchTo('p2')
    await vi.waitFor(() => expect(useEnvStore.getState().env?.project?.open).toBe(true))
    release({ ok: true, python: '/p', source: 'system', project: A_PROJECT_ENV })
    await pending
    expect(JSON.stringify(useEnvStore.getState().env ?? {})).not.toContain('/envs/a')
  })

  it('全局 setPython 切项目之后才回来：响应里 A 的 project 不写进 B', async () => {
    const armed = holdOnce('/api/engine/environment')
    const pending = useEnvStore.getState().setPython('/usr/bin/python3')
    const release = await armed
    await switchTo('p2')
    release({ ok: true, python: '/usr/bin/python3', source: 'configured', project: A_PROJECT_ENV })
    expect(await pending).toBeNull()
    expect(JSON.stringify(useEnvStore.getState().env ?? {})).not.toContain('/envs/a')
  })

  it('setWorkdirMode 切项目之后才回来：B 的 env / 确认框 / 渲染都不动', async () => {
    const armed = holdOnce('/api/engine/workdir')
    const pending = useEnvStore.getState().setWorkdirMode('sandbox', { confirmed: true })
    const release = await armed
    await switchTo('p2')
    useEnvStore.setState({ workdirConfirmation: { script: 'b.py' } as never })
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    release({ ok: true, project: A_PROJECT_ENV })
    expect(await pending).toBeNull()
    expect(JSON.stringify(useEnvStore.getState().env ?? {})).not.toContain('/envs/a')
    expect(useEnvStore.getState().workdirConfirmation).not.toBeNull()
    expect(retry).not.toHaveBeenCalled()
  })

  it('「改回」切项目之后才回来：B 的「已改用」提示与面板都不动', async () => {
    const armed = holdOnce('/api/engine/environment')
    const pending = useEnvStore.getState().revertAdoptedEnvironment()
    const release = await armed
    await switchTo('p2')
    useEnvStore.getState().noteEnvironmentAdopted({ source: 'conda', label: 'b', python_version: '3.12' } as never)
    const stale = vi.spyOn(useRenderStore.getState(), 'markStale')
    release({ ok: true, project: A_PROJECT_ENV })
    expect(await pending).toBeNull()
    expect(useEnvStore.getState().adoptedEnvironment?.label).toBe('b')
    expect(stale).not.toHaveBeenCalled()
    useEnvStore.getState().dismissAdoptedEnvironment()
  })
})

describe('重建跨项目单飞（#605 评审 P1）', () => {
  const rebuildPosts = () => calls.filter((c) => c.url.includes('/managed/rebuild')).length

  it('A 的重建没结束：切到 B 起不了第二次，A 的进度仍归 A；终局之后才放开', async () => {
    await useDepRepairStore.getState().rebuildManaged()
    expect(rebuildPosts()).toBe(1) // 尺子是活的：A 确实起了一次
    await switchTo('p2')
    expect(useDepRepairStore.getState().rebuildRunning).toBe(true)
    await useDepRepairStore.getState().rebuildManaged()
    expect(rebuildPosts()).toBe(1)

    // A 的进度到了：B 上不显示、不在 B 上派发
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    useDepRepairStore.getState().onProgress(progress('installing', { plan_id: 'managed-rebuild' }))
    expect(useDepRepairStore.getState().progress).toBeNull()
    useDepRepairStore.getState().onProgress(progress('done', { plan_id: 'managed-rebuild' }))
    expect(retry).not.toHaveBeenCalled()
    expect(useDepRepairStore.getState().rebuildRunning).toBe(false)

    // 放开之后 B 可以起自己的
    await useDepRepairStore.getState().rebuildManaged()
    expect(rebuildPosts()).toBe(2)
  })
})

describe('A → B → A 之后才被拒（#605 评审 P2）', () => {
  it('作业已经放回当前卡片：结局落在当前卡片上，不停在 preparing', async () => {
    await useDepRepairStore.getState().makePlan({ module: 'lmfit', script: 'fig.py', target: 'tavotto_managed' })
    const armed = holdOnce('/api/engine/dependency/install')
    const pending = useDepRepairStore.getState().install()
    const release = await armed
    await switchTo('p2')
    await switchTo('p1')
    expect(useDepRepairStore.getState().progress?.state).toBe('preparing') // 尺子是活的：确实放回来了
    release({ error: '拒绝', code: 'dependency_install_not_allowed' }, 409)
    await pending
    const s = useDepRepairStore.getState()
    expect(s.progress?.state).toBe('failed')
    expect(s.errorCode).toBe('dependency_install_not_allowed')
  })
})
