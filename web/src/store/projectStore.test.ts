import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  EngineError,
  armNoProjectRecovery,
  engineRender,
  fetchLayoutNames,
  fetchPanels,
  putAutosave,
} from '@/lib/api'
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
import { emptyProject, type TextObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useProjectStore } from './projectStore'

/**
 * 后端重启后 PROJECTS 清空（或项目被别处关掉），而本标签页 sessionStorage 里
 * 还留着旧 pj——此后每个请求都是 409 `no_project`。回归重点有三条：
 *   1. 一屏十几个请求同时 409，恢复动作只能跑一次；
 *   2. 恢复 = 忘掉 pj + 回 Project Picker，**不自动挑别的项目**，且不清兜底副本；
 *   3. 只认 `no_project` 这一个码——同样是 409 的 stale_write / file_locked
 *      各有各的处理，误伤它们等于把用户的改动扔了。
 */

const text = (id: string, t: string): TextObject => ({
  id, type: 'text', text: t, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

interface Reply {
  status: number
  body: unknown
}

/** 每个用例自己决定后端怎么答；默认 200 空对象 */
let reply: (url: string, init?: RequestInit) => Reply
const calls: { url: string; method: string }[] = []

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  calls.push({ url: u, method: init?.method ?? 'GET' })
  const r = reply(u, init)
  return new Response(JSON.stringify(r.body), { status: r.status })
}) as typeof fetch

const GONE: Reply = { status: 409, body: { error: '尚未打开项目', code: 'no_project' } }
const okShapes = (u: string): Reply => {
  if (u.includes('/api/projects/recent')) return { status: 200, body: { recent: [] } }
  if (u.includes('/api/projects/open')) {
    return { status: 200, body: { open: true, id: 'p_new', figures_dir: '/figs/new' } }
  }
  if (u.includes('/api/projects')) return { status: 200, body: { projects: [], default: null } }
  if (u.includes('/api/panels')) return { status: 200, body: { figures_dir: '/figs', panels: [] } }
  return { status: 200, body: {} }
}

const tick = () => new Promise((r) => setTimeout(r, 10))
const recentCalls = () => calls.filter((c) => c.url.includes('/api/projects/recent')).length

describe('项目失效（409 no_project）的前端出口', () => {
  /** 真正的恢复动作；用 spy 包一层数触发次数，行为仍是它自己的 */
  const real = useProjectStore.getState().dropProject
  let drop: ReturnType<typeof vi.fn>

  beforeEach(() => {
    localStorage.clear()
    reply = okShapes
    calls.length = 0
    drop = vi.fn(real)
    useProjectStore.setState({
      phase: 'open',
      project: { open: true, id: 'p_dead', figures_dir: '/figs/dead' },
      recent: [],
      opened: [],
      dropProject: drop as unknown as () => void,
    })
    setCurrentProjectId('p_dead')
    armNoProjectRecovery()
  })

  afterEach(() => {
    useProjectStore.setState({ dropProject: real })
    setCurrentProjectId(null)
    reply = okShapes
  })

  it('一屏请求同时 409：恢复只跑一次，错误照常抛给调用方', async () => {
    reply = () => GONE

    const results = await Promise.allSettled([
      fetchPanels(),
      fetchLayoutNames(),
      fetchPanels(),
      engineRender('Fig1_kinetics.pdf', []),
    ])

    expect(drop).toHaveBeenCalledTimes(1)

    // 调用方的语义一点不变：该抛的还是抛，错误体也原样带着
    expect(results.map((r) => r.status)).toEqual(Array(4).fill('rejected'))
    const first = (results[0] as PromiseRejectedResult).reason
    expect(first).toBeInstanceOf(ApiError)
    expect((first as ApiError).status).toBe(409)
    expect((first as ApiError).body.code).toBe('no_project')
    // engine 那条路走 EngineError（不是 ApiError），同样必须抛出来
    expect((results[3] as PromiseRejectedResult).reason).toBeInstanceOf(EngineError)

    // 恢复语义：忘掉 pj、回选择器，绝不自动落到别的项目上
    expect(currentProjectId()).toBeNull()
    expect(useProjectStore.getState().phase).toBe('none')
    expect(useProjectStore.getState().project).toBeNull()
  })

  it('已经回到选择器之后再 409 不会重复触发', async () => {
    reply = () => GONE
    await fetchPanels().catch(() => {})
    expect(drop).toHaveBeenCalledTimes(1)

    await fetchPanels().catch(() => {})
    await fetchLayoutNames().catch(() => {})
    expect(drop).toHaveBeenCalledTimes(1)
  })

  it('恢复动作本身幂等：已在选择器上再调一次什么都不做', async () => {
    reply = () => GONE
    await fetchPanels().catch(() => {})
    await tick()
    const before = recentCalls()

    real() // 直接再调一次
    expect(recentCalls()).toBe(before) // 连列表都不重新拉
  })

  it('未落盘的改动留在本机兜底副本里，自动保存请求仍打向失效的那个项目', async () => {
    const s = () => useDocumentStore.getState()
    await s().switchDocument(emptyProject(), 'd_gone')
    await tick() // 切文档那次落盘走完，diskBusy 复位
    s().commit(literal('加一段字'), (d) => {
      d.objects.push(text('t1', '还没存的改动'))
    })
    calls.length = 0

    reply = () => GONE
    await fetchPanels().catch(() => {})

    // 兜底副本：磁盘那份此刻必然写不进去，本机这份绝不能跟着没
    expect(localStorage.getItem('magplot.autosave.d_gone')).not.toBeNull()
    // 先冲刷再忘掉 pj：这次 PUT 仍带着失效的 p_dead，不会落到后端默认项目上
    const put = calls.find((c) => c.method === 'PUT' && c.url.includes('/api/autosave/'))
    expect(put?.url).toContain('pj=p_dead')
    await tick()
  })

  it('对照组：409 stale_write / file_locked 不触发恢复', async () => {
    reply = () => ({ status: 409, body: { code: 'stale_write', theirs: 999 } })
    await expect(putAutosave('d_x', emptyProject(), 1)).rejects.toBeInstanceOf(ApiError)

    reply = () => ({ status: 409, body: { code: 'file_locked', locked: 'Fig1.pdf' } })
    await expect(fetchPanels()).rejects.toBeInstanceOf(ApiError)

    expect(drop).not.toHaveBeenCalled()
    expect(currentProjectId()).toBe('p_dead')
    expect(useProjectStore.getState().phase).toBe('open')
    expect(useProjectStore.getState().project?.id).toBe('p_dead')
  })

  it('重新打开项目后重新武装：下一次失效照样送回选择器', async () => {
    reply = () => GONE
    await fetchPanels().catch(() => {})
    expect(drop).toHaveBeenCalledTimes(1)

    reply = okShapes
    await useProjectStore.getState().open('/figs/new')
    expect(currentProjectId()).toBe('p_new')
    expect(useProjectStore.getState().phase).toBe('open')

    reply = () => GONE
    await fetchPanels().catch(() => {})
    expect(drop).toHaveBeenCalledTimes(2)
    expect(useProjectStore.getState().phase).toBe('none')
    await tick()
  })
})
