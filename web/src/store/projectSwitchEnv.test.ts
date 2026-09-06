/**
 * 换项目必须把项目级的渲染环境状态（项目环境 / 工作目录模式，ADR 0018 / 0045）
 * 清掉再按新项目重取——否则设置里的开关与错误块的建议说的是上一个项目的模式，
 * 而请求打到的是新项目（Codex 评审 P1）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setCurrentProjectId } from '@/lib/session'
import { useEnvStore } from './envStore'
import { useProjectStore } from './projectStore'

const calls: string[] = []
globalThis.fetch = (async (url: unknown) => {
  const u = String(url)
  calls.push(u)
  const body = u.includes('/api/engine/environment')
    ? { ok: true, python: '/p', source: 'system', project: { open: true, workdir: { mode: 'project', modes: [] } } }
    : u.includes('/api/projects/recent')
      ? { recent: [] }
      : u.includes('/api/projects/open')
        ? []
        : u.includes('/api/panels')
          ? { figures_dir: '/new', panels: [] }
          : {}
  return new Response(JSON.stringify(body), { status: 200 })
}) as typeof fetch

beforeEach(() => {
  calls.length = 0
  setCurrentProjectId('p1')
  useEnvStore.setState({
    env: { ok: true, project: { open: true, workdir: { mode: 'sandbox', modes: [] } } } as never,
  })
})
afterEach(() => vi.restoreAllMocks())

describe('换项目时的渲染环境状态', () => {
  it('adoptOpenedProject 清掉旧项目的 env.project 并重取', async () => {
    const seen: Array<string | undefined> = []
    const unsub = useEnvStore.subscribe((s) => seen.push(s.env?.project?.open ? s.env.project.workdir?.mode : 'closed'))
    await useProjectStore
      .getState()
      .adoptOpenedProject({ id: 'p2', path: '/new', name: 'new', writable: true, open: true } as never)
    unsub()
    // 先清（closed），后按新项目取回（project）
    expect(seen[0]).toBe('closed')
    expect(calls.some((u) => u.includes('/api/engine/environment'))).toBe(true)
    await new Promise((r) => setTimeout(r, 0))
    expect(useEnvStore.getState().env?.project?.workdir?.mode).toBe('project')
  })
})
