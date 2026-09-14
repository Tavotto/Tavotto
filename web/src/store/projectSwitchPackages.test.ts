/**
 * 换项目时包管理的状态必须换代（ADR 0038，本轮评审 P2）。
 *
 * 「在 PyPI 查找」的结果是**全局的、不带项目身份**的一份状态，而它答的每一句
 * 话都属于某一个项目：`installed` 说的是那个项目的受管环境里装了哪一版，
 * `source` 说的是那个项目解释器读到的索引源配置。改造前 `resetForNewProject()`
 * 既不清它、也不作废在途的那一次——于是 A 项目的查找结果会一直开在 B 的包页面
 * 上，而那一页的安装按钮作用在 B 上。
 *
 * 两条判据分别看两件不同的事，**各自可被单独拆掉**（同一条保证做两遍的话，
 * 拆掉其中一遍会照样全绿）：
 *
 *   * `clear()` 里的 `set(IDLE_LOOKUP)` 管**已经落地**的那份；
 *   * `clear()` 里的换代管**还在飞**的那些。
 *
 * 作业是另一回事（issue #309）：它改的是**起它那个项目**的环境、还在后端跑，
 * `job_id` 是前端唯一的把手，所以 `clear()` **不清**它；「B 的页面上不该出现
 * A 的作业」靠作业自带项目字段 + `progressFor(project)` 解决。下面第二组用例
 * 钉的就是这两件事各自成立：切走看不见、切回接得上。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setCurrentProjectId } from '@/lib/session'
import type { PackageLookup, PackageProgress } from '@/lib/api'
import { useEnvStore } from './envStore'
import { isPackageJobRunning, usePackageStore } from './packageStore'
import { useProjectStore } from './projectStore'
import { useRenderStore } from './renderStore'

/** A 项目那次查找的答案——`installed` / `source` 是它属于哪个项目的证据 */
const A_ANSWER: PackageLookup = {
  name: 'lmfit',
  versions: ['1.3.4', '1.3.2'],
  latest: '1.3.4',
  installed: '1.3.2',
  source: 'custom_index',
}

/** 把 `/api/engine/packages/lookup` 那一次请求**扣在手里**，由用例决定何时回答 */
let releaseLookup: ((body: unknown, status?: number) => void) | null = null

/** 每次 fetch 的 url 与 JSON 请求体（作业那组用例数「谁被刷了」「取消发的是谁」） */
const calls: { url: string; body: unknown }[] = []

/** `/api/engine/packages/run` 的回答：后端在 run 响应里就带一份进度快照 */
const RUN_ANSWER: { started: boolean } & PackageProgress = {
  started: true,
  job_id: 'job-a',
  state: 'installing',
  log: '',
  error: null,
  code: '',
  op: 'install',
  distribution: 'lmfit',
}

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  calls.push({ url: u, body: typeof init?.body === 'string' ? JSON.parse(init.body) : null })
  if (u.includes('/api/engine/packages/run')) {
    const body = init?.body ? (JSON.parse(String(init.body)) as { job_id: string }) : { job_id: '' }
    return new Response(JSON.stringify({ ...RUN_ANSWER, job_id: body.job_id }), { status: 200 })
  }
  if (u.includes('/api/engine/packages/cancel')) {
    return new Response(JSON.stringify({ cancelling: true }), { status: 200 })
  }
  if (u.includes('/api/engine/packages/lookup')) {
    return new Promise<Response>((resolve) => {
      releaseLookup = (body, status = 200) =>
        resolve(
          new Response(JSON.stringify(body), {
            status,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
    })
  }
  const body = u.includes('/api/engine/packages')
    ? { environment: null, capability: { available: true }, user: [], builtin: [] }
    : u.includes('/api/engine/environment')
      ? { ok: true, python: '/p', source: 'system', project: { open: true } }
      : u.includes('/api/projects/recent')
        ? { recent: [] }
        : u.includes('/api/projects/open')
          ? []
          : u.includes('/api/panels')
            ? { figures_dir: '/new', panels: [] }
            : {}
  return new Response(JSON.stringify(body), { status: 200 })
}) as typeof fetch

const switchProject = (id = 'p2') =>
  useProjectStore
    .getState()
    .adoptOpenedProject({ id, path: `/${id}`, name: id, writable: true, open: true } as never)

beforeEach(() => {
  releaseLookup = null
  calls.length = 0
  setCurrentProjectId('p1')
  usePackageStore.setState({
    data: null,
    loading: false,
    loadError: '',
    jobs: {},
    busy: false,
    errorCode: '',
    errorText: '',
    lookup: { query: '', status: 'idle', result: null, code: '', text: '' },
  })
})
afterEach(() => vi.restoreAllMocks())

describe('换项目时的包管理状态', () => {
  it('已经落地的查找结果被清掉——它说的 installed 是旧项目环境里的版本', async () => {
    const run = usePackageStore.getState().runLookup('lmfit')
    releaseLookup?.(A_ANSWER)
    await run
    expect(usePackageStore.getState().lookup.result).toEqual(A_ANSWER)

    await switchProject()

    const after = usePackageStore.getState().lookup
    expect(after.status).toBe('idle')
    expect(after.result).toBeNull()
    expect(after.query).toBe('')
  })

  it('A 的查找在切到 B 之后才回来：整份被丢弃，store 里查不到它', async () => {
    const run = usePackageStore.getState().runLookup('lmfit')
    expect(usePackageStore.getState().lookup.status).toBe('loading')

    await switchProject()
    // 切完项目**之后**答案才回来——这一步是这条用例的全部意义
    releaseLookup?.(A_ANSWER)
    await run
    await new Promise((r) => setTimeout(r, 0))

    const after = usePackageStore.getState().lookup
    // 断言的是「store 里没有它」，不是「界面上看不见」
    expect(after.result).toBeNull()
    expect(after.status).toBe('idle')
    expect(JSON.stringify(after)).not.toContain('custom_index')
    expect(JSON.stringify(after)).not.toContain('1.3.2')
  })

  it('查找失败的那一次迟到回来同样被丢弃（走的是 catch 那一支）', async () => {
    // 502 才真的进 `runLookup` 的 catch。回 200 带一个 code 字段的话
    // `jsonFetch` 会当成成功，这条用例就跑到了 try 那一支上——名字说的是失败
    // 路径，量到的却是成功路径。
    const run = usePackageStore.getState().runLookup('lmfit')
    await switchProject()
    releaseLookup?.({ code: 'package_lookup_offline', error: '连不上索引' }, 502)
    await run
    await new Promise((r) => setTimeout(r, 0))

    const after = usePackageStore.getState().lookup
    expect(after.status).toBe('idle')
    expect(after.code).toBe('')
    expect(after.text).toBe('')
  })

  it('先证明 502 真的进了 catch：不切项目时它会落成 error', async () => {
    // 上一条用例的前提。没有这一条，`jsonFetch` 哪天不再对 502 抛异常，那条
    // 「被丢弃」就会因为**根本没有答案落地**而恒绿。
    const run = usePackageStore.getState().runLookup('lmfit')
    releaseLookup?.({ code: 'package_lookup_offline', error: '连不上索引' }, 502)
    await run
    const after = usePackageStore.getState().lookup
    expect(after.status).toBe('error')
    expect(after.code).toBe('package_lookup_offline')
  })

  it('清单与上一次的错误也跟着丢：它们说的是旧项目那个受管环境', async () => {
    usePackageStore.setState({
      data: { environment: { python: '/a/bin/python' }, user: [{ distribution: 'lmfit' }] } as never,
      loadError: '旧项目的加载失败',
      errorCode: 'package_busy',
      errorText: '旧项目的错误',
    })
    await switchProject()
    const s = usePackageStore.getState()
    expect(s.data).toBeNull()
    expect(s.loadError).toBe('')
    expect(s.errorCode).toBe('')
    expect(s.errorText).toBe('')
  })

  it('换代之后再查一次，新项目自己的答案照常落地（不是把功能关掉了）', async () => {
    await switchProject()
    const run = usePackageStore.getState().runLookup('lmfit')
    releaseLookup?.({ ...A_ANSWER, installed: '', source: 'pypi' })
    await run
    expect(usePackageStore.getState().lookup.result?.source).toBe('pypi')
    expect(usePackageStore.getState().lookup.status).toBe('found')
  })
})

/* ------------------------- 作业跟着它所属的项目走（issue #309） ------------------------- */

const progressFor = (project: string) => usePackageStore.getState().progressFor(project)
/** 数「清单被刷了几次」：`/api/engine/packages` 本身，不含 run / cancel / lookup / job */
const listLoads = () => calls.filter((c) => /\/api\/engine\/packages(\?|$)/.test(c.url)).length
const done = (jobId: string): PackageProgress => ({
  job_id: jobId,
  state: 'done',
  log: 'Successfully installed lmfit\n',
  error: null,
  code: '',
  op: 'install',
  distribution: 'lmfit',
  result: { distribution: 'lmfit', version: '1.3.4' },
})

/**
 * 终态副作用的两个探针。**不用 `vi.spyOn(store.getState(), …)`**：zustand 每次 `set`
 * 都会把上一份 state 里的函数原样抄进新对象，spy 一旦被抄走，`restoreAllMocks`
 * 只还原旧对象上的那份，新对象里留下的仍是那个 spy——计数会跨用例累加（实测
 * 一次变异跑出「期望 1 次、实际 9 次」）。所以每条用例自己 `setState` 一个新的
 * `vi.fn()`，跑完把原函数放回去。
 */
const ORIGINAL_ENV_REFRESH = useEnvStore.getState().refresh
const ORIGINAL_RENDER_RETRY = useRenderStore.getState().retryEnvironmentFailures
function probes() {
  const refresh = vi.fn(async () => {})
  const retry = vi.fn()
  useEnvStore.setState({ refresh })
  useRenderStore.setState({ retryEnvironmentFailures: retry })
  return { refresh, retry }
}
afterEach(() => {
  useEnvStore.setState({ refresh: ORIGINAL_ENV_REFRESH })
  useRenderStore.setState({ retryEnvironmentFailures: ORIGINAL_RENDER_RETRY })
})

/** 起一个作业并证明它在起它的那个项目上**真的在**——没有这一步，后面的「看不见」恒真 */
async function startJobOn(project: string, jobId: string) {
  setCurrentProjectId(project)
  expect(await usePackageStore.getState().run(jobId)).toBe(true)
  const p = progressFor(project)
  expect(p?.job_id).toBe(jobId)
  expect(isPackageJobRunning(p)).toBe(true)
}

describe('安装作业跟着它所属的项目走', () => {
  it('A 起的作业切到 B：B 的页面上没有它，A 做完也不在 B 上刷清单 / 环境 / 渲染', async () => {
    await startJobOn('p1', 'job-a')

    await switchProject('p2')
    expect(progressFor('p2')).toBeNull()
    // 把手没丢：作业还在后端跑，A 那一格原样留着
    expect(progressFor('p1')?.job_id).toBe('job-a')

    // 探针装在切完项目**之后**：换项目那一路自己也会重置 store，装早了会被抄掉
    const { retry, refresh } = probes()
    calls.length = 0
    usePackageStore.getState().onProgress(done('job-a'))
    await new Promise((r) => setTimeout(r, 0))

    // 终态落进了 A 的格子（切回去能看到「已完成」），B 上仍然什么都没有
    expect(progressFor('p1')?.state).toBe('done')
    expect(progressFor('p2')).toBeNull()
    // B 上一个副作用都没派发：这些动作说的都是「当前项目」
    expect(retry).not.toHaveBeenCalled()
    expect(refresh).not.toHaveBeenCalled()
    expect(listLoads()).toBe(0)
  })

  it('A 的作业在 B 上失败：失败文案不写到 B 的页面上，只落进 A 的格子', async () => {
    await startJobOn('p1', 'job-a')
    await switchProject('p2')
    usePackageStore.getState().onProgress({
      ...done('job-a'),
      state: 'failed',
      code: 'package_install_failed',
      error: 'pip 退出码 1',
      result: null,
    })
    expect(usePackageStore.getState().errorCode).toBe('')
    expect(usePackageStore.getState().errorText).toBe('')
    expect(progressFor('p1')?.state).toBe('failed')
    expect(progressFor('p1')?.code).toBe('package_install_failed')
    expect(progressFor('p2')).toBeNull()
  })

  it('切回 A：进度接得上，取消发的仍是那个 job_id', async () => {
    await startJobOn('p1', 'job-a')
    await switchProject('p2')
    expect(progressFor('p2')).toBeNull()

    await switchProject('p1')
    const p = progressFor('p1')
    expect(p?.job_id).toBe('job-a')
    expect(isPackageJobRunning(p)).toBe(true)

    calls.length = 0
    await usePackageStore.getState().cancel()
    const cancels = calls.filter((c) => c.url.includes('/api/engine/packages/cancel'))
    expect(cancels).toHaveLength(1)
    expect(cancels[0]!.body).toEqual({ job_id: 'job-a' })
  })

  it('在 B 上取消：没有属于 B 的作业，一个取消请求都不发', async () => {
    await startJobOn('p1', 'job-a')
    await switchProject('p2')
    calls.length = 0
    await usePackageStore.getState().cancel()
    expect(calls.filter((c) => c.url.includes('/api/engine/packages/cancel'))).toHaveLength(0)
  })

  it('两个项目各起一个：各在各的页面上，谁也不盖谁', async () => {
    await startJobOn('p1', 'job-a')
    await switchProject('p2')
    await startJobOn('p2', 'job-b')
    expect(progressFor('p1')?.job_id).toBe('job-a')
    expect(progressFor('p2')?.job_id).toBe('job-b')
    // A 的终态回来：只动 A 的格子
    usePackageStore.getState().onProgress(done('job-a'))
    expect(progressFor('p1')?.state).toBe('done')
    expect(progressFor('p2')?.job_id).toBe('job-b')
    expect(isPackageJobRunning(progressFor('p2'))).toBe(true)
  })

  it('对照：作业属于此刻开着的项目时，终态照常刷清单 / 环境 / 渲染重排', async () => {
    // 上面那条「0 次」的前提：同一个事件在**作业所属项目**上确实会触发这些动作。
    // 没有这一条，spy 哪天没接上那条路径，「不触发」就会因为从没有人触发而恒绿。
    await startJobOn('p1', 'job-a')
    const { retry, refresh } = probes()
    calls.length = 0
    usePackageStore.getState().onProgress(done('job-a'))
    await new Promise((r) => setTimeout(r, 0))
    expect(retry).toHaveBeenCalledTimes(1)
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(listLoads()).toBe(1)
    expect(progressFor('p1')?.state).toBe('done')
  })
})
