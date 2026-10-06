import { beforeEach, describe, expect, it } from 'vitest'
import { useEnvStore } from './envStore'

/**
 * 装载期的确认不被更早发出的环境读取盖掉（T12，T11 遗留的「页面装载完成前点下的确认丢了」）。
 *
 * 页面一挂上就发一次 `GET /api/engine/environment`（App 挂载）；它在项目记住了解释器时要起一次解释器，可能比
 * 用户紧接着点下的确认（`PATCH /api/engine/workdir`）回来得**晚**。晚到的那份是确认之前的快照：直接整份写进
 * store，界面就回到「还没决定运行目录」，看上去像确认被吞了，而后端其实已经记下了。
 */

const before = {
  ok: true,
  python: '/x/python',
  source: 'bundled',
  project: { open: true, workdir: { mode: 'sandbox', modes: ['sandbox', 'project', 'project_root'], decided: false } },
}
const after = { open: true, workdir: { mode: 'project_root', modes: ['sandbox', 'project', 'project_root'], decided: true } }

let releaseGet: (() => void) | null = null
let gets = 0

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  if (u.includes('/api/engine/workdir') && init?.method === 'PATCH') {
    return new Response(JSON.stringify({ ok: true, workdir: after.workdir, project: after }), { status: 200 })
  }
  if (u.includes('/api/engine/environment')) {
    gets += 1
    // 第一次读取（装载期）卡住，等确认先回来；之后的读取立即回后端此刻的事实
    if (gets === 1) {
      await new Promise<void>((resolve) => {
        releaseGet = resolve
      })
      return new Response(JSON.stringify(before), { status: 200 })
    }
    return new Response(JSON.stringify({ ...before, project: after }), { status: 200 })
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

beforeEach(() => {
  gets = 0
  releaseGet = null
  useEnvStore.setState({ env: null, workdirConfirmation: null })
})

describe('envStore 装载期的确认', () => {
  it('确认先回、装载期那次读取后回：确认不被旧快照盖掉', async () => {
    const loading = useEnvStore.getState().refresh()
    await Promise.resolve()
    expect(releaseGet).not.toBeNull()
    const err = await useEnvStore.getState().setWorkdirMode('project_root', { confirmed: true })
    expect(err).toBeNull()
    expect(useEnvStore.getState().env?.project?.workdir?.mode).toBe('project_root')
    releaseGet!()
    await loading
    const workdir = useEnvStore.getState().env?.project?.workdir
    expect(workdir?.mode).toBe('project_root')
    expect(workdir?.decided).toBe(true)
  })

  it('没有写入插队时，读取照常落地', async () => {
    const loading = useEnvStore.getState().refresh()
    await Promise.resolve()
    releaseGet!()
    await loading
    expect(useEnvStore.getState().env?.project?.workdir?.mode).toBe('sandbox')
  })
})
