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
import { __resetDepRepairParkingForTests, useDepRepairStore } from './depRepairStore'
import { useEnvStore } from './envStore'
import { useProjectStore } from './projectStore'
import { useRenderStore } from './renderStore'
import { useScriptRunStore, type ScriptRunState } from './scriptRunStore'

/** 被扣住的请求：url 片段 → 放行函数（由用例决定何时、回什么） */
const held = new Map<string, (body: unknown, status?: number) => void>()
const holding = new Set<string>()
const calls: { url: string; body: unknown }[] = []
/** `GET /api/engine/dependency/state` 的回答（`'network'` = 连实况都问不到） */
let stateAnswer: unknown = { state: 'idle', plan_id: '', log: '', error: null, code: '' }
/** 重建的 POST 在网络层失败（后端可能已经起了） */
let rebuildNetworkError = false
/** 后端此刻的全局解释器（`GET /api/engine/environment` 回它） */
let globalPython = '/p'

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
  if (u.includes('/api/engine/dependency/state')) {
    if (stateAnswer === 'network') throw new TypeError('Failed to fetch')
    return respond(stateAnswer)
  }
  if (u.includes('/api/engine/dependency/plan')) return respond({ plan: PLAN_A })
  if (u.includes('/api/engine/dependency/install'))
    return respond({ started: true, plan_id: 'plan-a', state: 'installing', log: '', error: null, code: '' })
  if (u.includes('/managed/rebuild') && rebuildNetworkError) throw new TypeError('Failed to fetch')
  const body = u.includes('/api/engine/environment')
    ? { ok: true, python: globalPython, source: 'system', project: { open: true } }
    : u.includes('/api/projects/recent')
      ? { recent: [] }
      : u.includes('/api/projects/open')
        ? []
        : u.includes('/api/panels')
          ? { figures_dir: '/new', panels: [] }
          : {}
  return respond(body)
}) as typeof fetch

/** 最近一次重建请求交上去的进度 id（前端在发请求之前生成的那个） */
const lastRebuildId = (): string => {
  const post = [...calls].reverse().find((c) => c.url.includes('/managed/rebuild'))
  return (post?.body as { progress_id?: string } | null)?.progress_id ?? ''
}

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
    python_supported: { min: '3.10', max: '3.14' },
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
  // 模块级的停放槽活得比 zustand reset 长：每条用例从空的开始（互不串）
  __resetDepRepairParkingForTests()
  stateAnswer = { state: 'idle', plan_id: '', log: '', error: null, code: '' }
  rebuildNetworkError = false
  globalPython = '/p'
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
  useDepRepairStore.setState({ parked: {}, rebuilding: {} })
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

  it('切走期间 A 的作业失败了：切回 A 时「就地重试」的上下文一起回来（B 上没有它）', async () => {
    // Codex #709：`clear()` 只收进度、丢了 request / authorized，切回来看得到失败却没有「重试」
    await startInstallOnA()
    await switchTo('p2')
    expect(useDepRepairStore.getState().request).toBeNull()
    useDepRepairStore.getState().onProgress(progress('failed', { code: 'private_python_offline', error: '断网' }))
    await switchTo('p1')
    const s = useDepRepairStore.getState()
    expect(s.progress?.state).toBe('failed')
    expect(s.request).toMatchObject({ module: 'lmfit', script: 'fig.py', target: 'tavotto_managed' })
  })

  it('脚本行发起的修复：它那份 offer 随作业收放——B 上没有，切回 A 回来（#729）', async () => {
    // 脚本行卡片的前提住在切项目会被清空的 scriptRunStore 里；不随作业收放，切回来那一行挂不出卡片
    const rowOffer = { offer: { import_name: 'lmfit', script: 'fig.py' }, module: 'lmfit' } as never
    await useDepRepairStore
      .getState()
      .makePlan({ module: 'lmfit', script: 'fig.py', target: 'tavotto_managed' }, rowOffer)
    await useDepRepairStore.getState().install()
    useDepRepairStore.getState().onProgress(progress('installing'))
    // 发起时补上了同样缺这个包的其它行（`peers`，此处没有）：内容是那份 offer，收放前后是同一个对象
    const held = useDepRepairStore.getState().scriptOffer
    expect(held).toEqual({ ...(rowOffer as object), peers: [] })
    await switchTo('p2')
    expect(useDepRepairStore.getState().scriptOffer).toBeNull()
    await switchTo('p1')
    expect(useDepRepairStore.getState().scriptOffer).toBe(held)
    expect(useDepRepairStore.getState().progress?.state).toBe('installing')
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
    expect(useDepRepairStore.getState().progress?.plan_id).toBe(lastRebuildId())
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

describe('重建：进度 id 每次一个，单飞只在同一项目里（#606 第 3 条）', () => {
  const rebuildPosts = () => calls.filter((c) => c.url.includes('/managed/rebuild')).length

  it('A 的重建没结束：A 起不了第二次，B 可以起自己的；两边的进度各归各的', async () => {
    await useDepRepairStore.getState().rebuildManaged()
    const idA = lastRebuildId()
    expect(idA).toMatch(/^[0-9a-f]{32}$/)
    expect(rebuildPosts()).toBe(1) // 尺子是活的：A 确实起了一次
    await useDepRepairStore.getState().rebuildManaged()
    expect(rebuildPosts()).toBe(1) // 同一项目单飞

    await switchTo('p2')
    expect(useDepRepairStore.getState().rebuildRunningFor('p2')).toBe(false)
    await useDepRepairStore.getState().rebuildManaged()
    const idB = lastRebuildId()
    expect(rebuildPosts()).toBe(2)
    expect(idB).not.toBe(idA)

    // A 的进度：B 上不显示、不在 B 上派发；B 的进度：落在 B 上
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    useDepRepairStore.getState().onProgress(progress('installing', { plan_id: idA }))
    expect(useDepRepairStore.getState().progress?.plan_id).toBe(idB)
    useDepRepairStore.getState().onProgress(progress('done', { plan_id: idA }))
    expect(retry).not.toHaveBeenCalled()
    expect(useDepRepairStore.getState().rebuildRunningFor('p1')).toBe(false) // A 的单飞放开
    expect(useDepRepairStore.getState().rebuildRunningFor('p2')).toBe(true) // B 的不受影响
    useDepRepairStore.getState().onProgress(progress('installing', { plan_id: idB }))
    expect(useDepRepairStore.getState().progress?.state).toBe('installing')

    // 切回 A：看到的是 A 自己的结局，不是 B 的进度
    await switchTo('p1')
    expect(useDepRepairStore.getState().progress?.plan_id).toBe(idA)
    expect(useDepRepairStore.getState().progress?.state).toBe('done')
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

describe('重建的所属与单飞在发请求之前落定（#605 评审第二轮 P1）', () => {
  const rebuild = (state: DependencyProgress['state'], over: Partial<DependencyProgress> = {}) =>
    progress(state, { plan_id: lastRebuildId(), ...over })

  it('POST 回来之前进度就到了、而且立刻失败：结局留在卡片上，不被乐观的 creating_env 盖回去', async () => {
    const armed = holdOnce('/api/engine/environment/managed/rebuild')
    const pending = useDepRepairStore.getState().rebuildManaged()
    const release = await armed
    useDepRepairStore.getState().onProgress(rebuild('installing'))
    expect(useDepRepairStore.getState().progress?.state).toBe('installing') // 尺子是活的：早到的进度认得出是自己的
    useDepRepairStore.getState().onProgress(rebuild('failed', { code: 'managed_rebuild_failed', error: '建不起来' }))
    expect(useDepRepairStore.getState().rebuildRunningFor('p1')).toBe(false)
    release({ started: true, progress_id: lastRebuildId() })
    await pending
    const s = useDepRepairStore.getState()
    expect(s.progress?.state).toBe('failed')
    expect(s.errorCode).toBe('managed_rebuild_failed')
    expect(s.busy).toBe(false)
  })

  it('请求本身失败：所属与单飞都撤掉，之后的同 id 进度不再认领，可以再起一次', async () => {
    const armed = holdOnce('/api/engine/environment/managed/rebuild')
    const pending = useDepRepairStore.getState().rebuildManaged()
    const release = await armed
    release({ error: '忙', code: 'environment_mutating' }, 409)
    await pending
    let s = useDepRepairStore.getState()
    const failedId = lastRebuildId()
    expect(s.rebuildRunningFor('p1')).toBe(false)
    expect(s.progress).toBeNull()
    expect(s.errorCode).toBe('environment_mutating')
    useDepRepairStore.getState().onProgress(rebuild('installing'))
    expect(useDepRepairStore.getState().progress).toBeNull()
    await useDepRepairStore.getState().rebuildManaged()
    s = useDepRepairStore.getState()
    expect(s.progress?.plan_id).toBe(lastRebuildId())
    expect(lastRebuildId()).not.toBe(failedId)
  })

  it('请求在切项目之后才失败：A 那格记成失败、单飞放开，B 不动', async () => {
    const armed = holdOnce('/api/engine/environment/managed/rebuild')
    const pending = useDepRepairStore.getState().rebuildManaged()
    const release = await armed
    await switchTo('p2')
    release({ error: '忙', code: 'environment_mutating' }, 409)
    await pending
    expect(useDepRepairStore.getState().rebuildRunningFor('p1')).toBe(false)
    expect(useDepRepairStore.getState().errorCode).toBe('')
    await switchTo('p1')
    expect(useDepRepairStore.getState().progress?.state).toBe('failed')
    expect(useDepRepairStore.getState().errorCode).toBe('environment_mutating')
  })
})

describe('重建请求失败先问实况（#606 第 5 条）', () => {
  it('POST 在网络层失败、后端其实在建：进度照样显示，本项目的重建按钮保持占用', async () => {
    rebuildNetworkError = true
    stateAnswer = 'pending-fill'
    const pending = useDepRepairStore.getState().rebuildManaged()
    const id = lastRebuildId()
    stateAnswer = { plan_id: id, state: 'installing', log: 'Collecting lmfit', error: null, code: '' }
    await pending
    let s = useDepRepairStore.getState()
    expect(calls.some((c) => c.url.includes(`/api/engine/dependency/state?plan_id=${id}`))).toBe(true)
    expect(s.progress?.plan_id).toBe(id)
    expect(s.progress?.state).toBe('installing')
    expect(s.rebuildRunningFor('p1')).toBe(true)
    expect(s.errorCode).toBe('')
    expect(s.busy).toBe(false)
    // 之后的 SSE 照常接着走，终局放开单飞
    useDepRepairStore.getState().onProgress(progress('done', { plan_id: id }))
    s = useDepRepairStore.getState()
    expect(s.progress?.state).toBe('done')
    expect(s.rebuildRunningFor('p1')).toBe(false)
  })

  it('对照：POST 失败、实况说没到过后端（idle）——当作没起来，报错并放开', async () => {
    rebuildNetworkError = true
    await useDepRepairStore.getState().rebuildManaged()
    const s = useDepRepairStore.getState()
    expect(calls.some((c) => c.url.includes('/api/engine/dependency/state'))).toBe(true)
    expect(s.progress).toBeNull()
    expect(s.rebuildRunningFor('p1')).toBe(false)
    expect(s.errorText).not.toBe('')
  })

  it('POST 失败、实况已经是失败结局：结局与错误交出来，单飞放开', async () => {
    rebuildNetworkError = true
    const pending = useDepRepairStore.getState().rebuildManaged()
    stateAnswer = { plan_id: lastRebuildId(), state: 'failed', log: '', error: '建不起来', code: 'managed_rebuild_failed' }
    await pending
    const s = useDepRepairStore.getState()
    expect(s.progress?.state).toBe('failed')
    expect(s.errorCode).toBe('managed_rebuild_failed')
    expect(s.rebuildRunningFor('p1')).toBe(false)
  })
})

describe('全局解释器改动在切项目之后才回来（#606 第 4 条）', () => {
  it('B 按此刻重新问一次：显示新的全局解释器，不带 A 的 project', async () => {
    const armed = holdOnce('/api/engine/environment')
    const pending = useEnvStore.getState().setPython('/usr/bin/python3.12')
    const release = await armed
    await switchTo('p2')
    await vi.waitFor(() => expect(useEnvStore.getState().env?.python).toBe('/p'))
    // 尺子是活的：B 切换时那次 refresh 先回来了，此刻显示的还是旧的全局解释器
    globalPython = '/usr/bin/python3.12' // 后端已经全局换了
    release({ ok: true, python: '/usr/bin/python3.12', source: 'configured', project: A_PROJECT_ENV })
    expect(await pending).toBeNull()
    const env = useEnvStore.getState().env
    expect(env?.python).toBe('/usr/bin/python3.12')
    expect(JSON.stringify(env ?? {})).not.toContain('/envs/a')
  })
})

// ---------------------------------------------------------------- Codex #742：await 之后已切项目

/** 一行停在缺 `module` 上的脚本（与试运行以 missing_dependency 收场时 scriptRunStore 里的那份同形） */
const stuck = (module: string): ScriptRunState => ({
  phase: 'missing_dependency',
  descriptors: [],
  droppedFigures: 0,
  error: { code: 'missing_dependency', message: '', params: { module } },
  cancelRequested: false,
  gen: 0,
})
const probed = () =>
  calls.filter((c) => c.url.includes('/api/registry/probe')).map((c) => (c.body as { script: string }).script).sort()

describe('环境改动的回调按项目代际判（Codex #742）', () => {
  beforeEach(() => {
    useScriptRunStore.getState().clear()
    useScriptRunStore.setState({ byScript: { 'fig.py': stuck('lmfit'), 'peer.py': stuck('lmfit') } })
    useDepRepairStore.setState({ managedPreviews: {} })
  })

  it('P1：A 上「恢复自动检测」在途时切到 B——回来的回调不作用于 B（不 reset、不重排、不跑 B 的脚本），切回 A 续跑', async () => {
    let release: (v: string | null) => void = () => {}
    vi.spyOn(useEnvStore.getState(), 'setPython').mockReturnValue(new Promise<string | null>((r) => (release = r)))
    const pending = useDepRepairStore.getState().clearPinnedInterpreter('lmfit', 'fig.py')
    await switchTo('p2')
    // B 上也有一行缺同一个包、B 自己的修复状态也在：A 的回调一样都不许碰
    useScriptRunStore.setState({ byScript: { 'b.py': stuck('lmfit') } })
    useDepRepairStore.setState({ errorCode: 'b_own_error', pinned: { python: '/b', source: 'configured' } })
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    calls.length = 0
    release(null)
    expect(await pending).toBeNull()
    expect(probed(), 'A 的回调在 B 上跑了脚本').toEqual([])
    expect(retry).not.toHaveBeenCalled()
    expect(useDepRepairStore.getState().errorCode, 'A 的回调把 B 的修复状态 reset 了').toBe('b_own_error')
    expect(useDepRepairStore.getState().pinned?.python).toBe('/b')
    // 切回 A：与「切走期间装好」同一条路，补跑那一行与同样缺它的那一行
    await switchTo('p1')
    await vi.waitFor(() => expect(probed()).toEqual(['fig.py', 'peer.py']))
    // 再切走切回不重复
    await switchTo('p2')
    await switchTo('p1')
    expect(probed()).toEqual(['fig.py', 'peer.py'])
  })

  it('对照：没切项目时「恢复自动检测」立刻收卡、重排、重跑（尺子是活的）', async () => {
    vi.spyOn(useEnvStore.getState(), 'setPython').mockResolvedValue(null)
    useDepRepairStore.setState({ pinned: { python: '/a', source: 'configured' } })
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    calls.length = 0
    expect(await useDepRepairStore.getState().clearPinnedInterpreter('lmfit', 'fig.py')).toBeNull()
    expect(useDepRepairStore.getState().pinned).toBeNull()
    expect(retry).toHaveBeenCalled()
    await vi.waitFor(() => expect(probed()).toEqual(['fig.py', 'peer.py']))
  })

  it('P2：A 上「改用已有环境」在途时切到 B——B 不跑；切回 A，那一行与同样缺它的行续跑', async () => {
    let release: (v: string | null) => void = () => {}
    vi.spyOn(useEnvStore.getState(), 'setProjectPython').mockReturnValue(new Promise<string | null>((r) => (release = r)))
    const pending = useDepRepairStore.getState().adoptSystemPython('/usr/bin/python3', 'lmfit', 'fig.py')
    await switchTo('p2')
    useScriptRunStore.setState({ byScript: { 'b.py': stuck('lmfit') } })
    calls.length = 0
    release(null)
    await pending
    expect(probed()).toEqual([])
    await switchTo('p1')
    await vi.waitFor(() => expect(probed()).toEqual(['fig.py', 'peer.py']))
  })

  it('预读计划在途时切到 B：回来的计划不落进 B（B 上没有这一格）', async () => {
    const armed = holdOnce('/api/engine/dependency/plan')
    const pending = useDepRepairStore.getState().previewManaged({ module: 'lmfit', script: 'fig.py', target: 'tavotto_managed' })
    const release = await armed
    await switchTo('p2')
    release({ plan: PLAN_A })
    await pending
    expect(useDepRepairStore.getState().managedPreviews).toEqual({})
  })

  it('一键修复（installNow）的计划请求在途时切到 B：不在 B 上执行、不落计划', async () => {
    const armed = holdOnce('/api/engine/dependency/plan')
    const pending = useDepRepairStore.getState().installNow(
      { module: 'lmfit', script: 'fig.py', target: 'tavotto_managed' },
      { requirement: PLAN_A.requirement, target_kind: 'tavotto_managed', private_python: null },
    )
    const release = await armed
    await switchTo('p2')
    calls.length = 0
    release({ plan: { ...PLAN_A, target_kind: 'tavotto_managed', modifies_user_environment: false } })
    await pending
    expect(calls.some((c) => c.url.includes('/api/engine/dependency/install'))).toBe(false)
    expect(useDepRepairStore.getState().plan).toBeNull()
    expect(useDepRepairStore.getState().progress).toBeNull()
  })
})

/**
 * 统一规则：结果回来时所属项目就是当前项目，立即执行；不是就停放、回到它时 drain（`deliverToOwner`，Codex #742）。
 * 这里每一条都是 A → B → A **在结果回来之前**已经切回 A：代际变了、所属项目却正开着——`clear()` 那次 drain 已经
 * 过去，停放下去就没人再取。审计表里用到停放的每一行各一条
 */
describe('A → B → A 在结果回来之前已切回：结果立即作用于 A（Codex #742）', () => {
  beforeEach(() => {
    useScriptRunStore.getState().clear()
    useScriptRunStore.setState({ byScript: { 'fig.py': stuck('lmfit'), 'peer.py': stuck('lmfit') } })
    useDepRepairStore.setState({ managedPreviews: {} })
  })
  const bounce = async () => {
    await switchTo('p2')
    await switchTo('p1')
  }

  it('改用已有环境：切回之后才回来，立即重排、重跑那两行（不停放）', async () => {
    let release: (v: string | null) => void = () => {}
    vi.spyOn(useEnvStore.getState(), 'setProjectPython').mockReturnValue(new Promise<string | null>((r) => (release = r)))
    const pending = useDepRepairStore.getState().adoptSystemPython('/usr/bin/python3', 'lmfit', 'fig.py')
    await bounce()
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    calls.length = 0
    release(null)
    await pending
    expect(retry).toHaveBeenCalled()
    await vi.waitFor(() => expect(probed()).toEqual(['fig.py', 'peer.py']))
    // 没有停放下去：再切走切回不会再跑一遍
    await bounce()
    expect(probed()).toEqual(['fig.py', 'peer.py'])
  })

  it('恢复自动检测：切回之后才回来，立即收卡（清掉 A 的固定）、重排、重跑那两行', async () => {
    let release: (v: string | null) => void = () => {}
    vi.spyOn(useEnvStore.getState(), 'setPython').mockReturnValue(new Promise<string | null>((r) => (release = r)))
    const pending = useDepRepairStore.getState().clearPinnedInterpreter('lmfit', 'fig.py')
    await bounce()
    // 切回 A 之后卡片又拿到了那条固定（比如 A 的 offer 带着它）：回调要把它收掉
    useDepRepairStore.setState({ pinned: { python: '/a', source: 'configured' } })
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    calls.length = 0
    release(null)
    expect(await pending).toBeNull()
    expect(useDepRepairStore.getState().pinned).toBeNull()
    expect(retry).toHaveBeenCalled()
    await vi.waitFor(() => expect(probed()).toEqual(['fig.py', 'peer.py']))
    await bounce()
    expect(probed()).toEqual(['fig.py', 'peer.py'])
  })

  it('安装完成：A → B → A 之后才装好，立即重排、重跑发起行与同包的其它行', async () => {
    const rowOffer = { offer: { import_name: 'lmfit', script: 'fig.py' }, module: 'lmfit' } as never
    await useDepRepairStore.getState().makePlan({ module: 'lmfit', script: 'fig.py', target: 'tavotto_managed' }, rowOffer)
    await useDepRepairStore.getState().install()
    useDepRepairStore.getState().onProgress(progress('installing'))
    await bounce()
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    calls.length = 0
    useDepRepairStore.getState().onProgress(progress('done', { script: 'fig.py', import_name: 'lmfit' }))
    expect(retry).toHaveBeenCalled()
    await vi.waitFor(() => expect(probed()).toEqual(['fig.py', 'peer.py']))
  })

  it('失败后重试：A → B → A 之后才失败，结局与「就地重试」的上下文都在 A 的卡片上', async () => {
    await startInstallOnA()
    await bounce()
    useDepRepairStore.getState().onProgress(progress('failed', { code: 'dependency_network_unavailable', error: '断网' }))
    const s = useDepRepairStore.getState()
    expect(s.progress?.state).toBe('failed')
    expect(s.errorCode).toBe('dependency_network_unavailable')
    expect(s.request).toMatchObject({ module: 'lmfit', script: 'fig.py' })
    expect(s.parked).toEqual({})
  })

  it('跑前弹窗准备：A → B → A 之后才装好，立即重排 A 的渲染', async () => {
    useEnvStore.getState().requestDependencyPreparation(offer('fig.py'))
    const armed = holdOnce('/api/engine/dependencies/plan')
    const pending = useDepRepairStore.getState().prepare('tavotto_managed')
    const release = await armed
    release({ plan: { plan_id: 'jp-a', script: 'fig.py', target_kind: 'tavotto_managed', requirements: ['lmfit'] } })
    await pending
    expect(useDepRepairStore.getState().progress?.plan_id).toBe('jp-a')
    await bounce()
    expect(useDepRepairStore.getState().progress?.plan_id, '切回 A 没接上联合准备的作业').toBe('jp-a')
    const retry = vi.spyOn(useRenderStore.getState(), 'retryEnvironmentFailures')
    useDepRepairStore.getState().onProgress(progress('done', { plan_id: 'jp-a', flow: 'joint' }))
    expect(retry).toHaveBeenCalled()
    expect(useDepRepairStore.getState().progress?.state).toBe('done')
  })

  it('重建：POST 失败、实况在建，而 A → B → A 已经切回——实况接到 A 的卡片上（不停放）', async () => {
    const armed = holdOnce('/managed/rebuild')
    const pending = useDepRepairStore.getState().rebuildManaged()
    const release = await armed
    const id = lastRebuildId()
    await bounce()
    stateAnswer = { plan_id: id, state: 'installing', log: '', error: null, code: '' }
    release({ error: '网关超时' }, 504)
    await pending
    const s = useDepRepairStore.getState()
    expect(s.progress?.plan_id, '实况被停放进了 A 那格，而 A 正开着——没人再取').toBe(id)
    expect(s.progress?.state).toBe('installing')
    expect(s.parked).toEqual({})
    expect(s.rebuildRunningFor('p1')).toBe(true)
  })
})
