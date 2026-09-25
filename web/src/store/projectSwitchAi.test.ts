/**
 * 换项目时改图助手必须换代（#589）。
 *
 * 改造前 `resetForNewProject()` 不清 `aiStore`：A 的对话留在 B 的助手面板里，那条「撤销」带的是 A 的
 * 会话、请求却发往 B 当前的 pj；A 切走之前发出、切走之后才回来的发起 / 撤销照样落地，`ai.done`
 * 的收尾提示照样出现在 B 的状态栏上。
 *
 * 每道闸各一条判据，并且每条都配一个「不切项目」的对照——尺子先证明量得到，再证明闸挡住了：
 *   * 切项目清空会话并换代；
 *   * 发起在飞时切走：回来不落地（对照：不切则落地）；
 *   * 撤销在飞时切走：回来回 `false`、不写状态（对照：不切则回 `true`）；
 *   * 撤销 / 中止钉在会话自己的项目上：此刻认领的是 B，请求头仍是 A；
 *   * `ai.done` 带着 A 的 pj 到达时 B 不接（对照：B 自己的照常提示）。
 * 所有扣住的请求都在用例结束前放行并 await（Codex 在 #607 抓过悬着的 promise）。
 */
import { beforeEach, describe, expect, it } from 'vitest'

import { currentProjectId, setCurrentProjectId } from '@/lib/session'
import { handleServerEvent } from '@/hooks/useServerEvents'
import { useAiStore } from './aiStore'
import { useProjectStore } from './projectStore'
import { useUiStore } from './uiStore'

type Held = { url: string; pj: string | null; release: (body: unknown) => void }
const held: Held[] = []
/** 哪些路径扣住（其余立刻回答） */
let holdPaths: string[] = []
const calls: { url: string; pj: string | null }[] = []

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  const headers = (init?.headers ?? {}) as Record<string, string>
  const pj = headers['X-Tavotto-Project'] ?? null
  calls.push({ url: u, pj })
  const answer = (body: unknown) =>
    new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  if (holdPaths.some((p) => u.includes(p))) {
    return new Promise<Response>((resolve) => held.push({ url: u, pj, release: (b) => resolve(answer(b)) }))
  }
  if (u.includes('/api/ai/run')) return answer({ session: `s-${calls.length}`, script: 'fig1.py' })
  if (u.includes('/revert') || u.includes('/cancel')) return answer({ ok: true, script: 'fig1.py' })
  if (u.includes('/api/panels')) return answer({ figures_dir: '/b', panels: [] })
  if (u.includes('/api/projects/recent')) return answer({ recent: [] })
  if (u.includes('/api/projects/open')) return answer([])
  if (u.includes('/api/engine/environment'))
    return answer({ ok: true, python: '/p', source: 'system', project: { open: true } })
  return answer({})
}) as typeof fetch

const switchProject = (id: string) =>
  useProjectStore
    .getState()
    .adoptOpenedProject({ id, path: `/${id}`, name: id, writable: true, open: true } as never)

const startA = () =>
  useAiStore.getState().start({
    prompt: '把图例移到左上角',
    fileId: 'Fig1.pdf',
    panelId: 'p1',
    gid: null,
    label: null,
    scope: 'figure',
    target: '整张图',
    overrides: [],
  })

const releaseAll = async (body: unknown) => {
  for (const h of held.splice(0)) h.release(body)
  for (let i = 0; i < 10; i++) await Promise.resolve()
}

beforeEach(() => {
  held.length = 0
  calls.length = 0
  holdPaths = []
  setCurrentProjectId('pa')
  useAiStore.setState({
    sessions: [],
    agent: 'codex',
    caps: { agents: [{ id: 'codex', name: 'Codex', usable: true, models: [], efforts: [] }] } as never,
  })
  useUiStore.setState({ status: null })
})

describe('切项目清掉 A 的对话', () => {
  it('A 有一段对话 → 切到 B：会话列表空、代际前进', async () => {
    await startA()
    expect(useAiStore.getState().sessions).toHaveLength(1) // 对照：尺子量得到
    const before = useAiStore.getState().generation
    await switchProject('pb')
    expect(useAiStore.getState().sessions).toEqual([])
    expect(useAiStore.getState().generation).toBe(before + 1)
  })
})

describe('A 在飞的请求切走之后才回来', () => {
  it('对照：不切项目，发起回来就落地', async () => {
    holdPaths = ['/api/ai/run']
    const run = startA()
    await releaseAll({ session: 's-late', script: 'fig1.py' })
    await run
    expect(useAiStore.getState().sessions.map((s) => s.id)).toEqual(['s-late'])
  })

  it('发起在飞时切到 B：回来不落进 B 的会话列表', async () => {
    holdPaths = ['/api/ai/run']
    const run = startA()
    await switchProject('pb')
    await releaseAll({ session: 's-late', script: 'fig1.py' })
    await run
    expect(useAiStore.getState().sessions).toEqual([])
  })

  it('对照：不切项目，撤销回来回 true 并记成已撤销', async () => {
    await startA()
    const sid = useAiStore.getState().sessions[0].id
    holdPaths = ['/revert']
    const undo = useAiStore.getState().revert(sid)
    await releaseAll({ ok: true, script: 'fig1.py' })
    expect(await undo).toBe(true)
    expect(useAiStore.getState().sessions[0].status).toBe('reverted')
  })

  it('撤销在飞时切到 B：回来回 false（调用方不再标脏、不再提示），B 里没有它', async () => {
    await startA()
    const sid = useAiStore.getState().sessions[0].id
    holdPaths = ['/revert']
    const undo = useAiStore.getState().revert(sid)
    await switchProject('pb')
    await releaseAll({ ok: true, script: 'fig1.py' })
    expect(await undo).toBe(false)
    expect(useAiStore.getState().sessions).toEqual([])
  })
})

describe('撤销 / 中止钉在会话自己的项目上', () => {
  it('此刻认领的是 B，A 的会话的撤销与中止请求仍发往 A', async () => {
    await startA()
    const sid = useAiStore.getState().sessions[0].id
    expect(useAiStore.getState().sessions[0].project).toBe('pa')
    // 只换认领、不换代（模拟切换进行到一半、会话还在的那一刻）
    setCurrentProjectId('pb')
    expect(currentProjectId()).toBe('pb')
    await useAiStore.getState().revert(sid)
    await useAiStore.getState().cancel(sid)
    const sent = calls.filter((c) => c.url.includes(`/api/ai/sessions/${sid}/`))
    expect(sent.map((c) => c.pj)).toEqual(['pa', 'pa'])
    expect(sent.every((c) => !c.url.includes('pj=pb'))).toBe(true)
  })
})

describe('ai.done 带着别的项目的 pj', () => {
  const done = (pj: string) =>
    handleServerEvent({
      kind: 'ai.done',
      pj,
      session: 'sx',
      status: 'done',
      changed: false,
      diff: '',
      script: 'fig1.py',
    } as never)

  it('对照：本项目的收尾照常提示', () => {
    setCurrentProjectId('pb')
    useProjectStore.setState({ project: { id: 'pb' } as never })
    done('pb')
    expect(useUiStore.getState().status).not.toBeNull()
  })

  it('A 的收尾在换代窗口里到达：不提示、不碰会话', () => {
    // 切换进行到一半：全局 pj 已经是 B，`project` 还没发布、仍是 A——通用的 `project?.id`
    // 那道判据此刻放行 A 的事件，只有按「此刻认领的 pj」判的那一道挡得住
    setCurrentProjectId('pb')
    useProjectStore.setState({ project: { id: 'pa' } as never })
    done('pa')
    expect(useUiStore.getState().status).toBeNull()
  })
})
