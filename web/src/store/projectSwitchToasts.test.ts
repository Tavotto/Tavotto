/**
 * 换项目时通知轨上说旧项目的事的 toast 要撤掉。
 *
 * 错误 toast 不会自己消失；`render.failed` / `project.error` 的撤销事件（`render.done` / `registry.changed`）
 * 按 pj 过滤，新项目收不到旧项目的——不在切换时撤，A 的「无法渲染 Fig1」会一直挂在 B 上，指着 A 的文件。
 */
import { beforeEach, describe, expect, it } from 'vitest'

import { literal } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { renderFailureOwner } from './renderStore'
import { useProjectStore } from './projectStore'
import { useUiStore } from './uiStore'

const respond = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
globalThis.fetch = (async (url: unknown) => {
  const u = String(url)
  return respond(
    u.includes('/api/projects/recent') ? { recent: [] } : u.includes('/api/projects/open') ? [] : u.includes('/api/panels') ? { figures_dir: '/new', panels: [] } : {},
  )
}) as typeof fetch

const switchTo = (id: string) =>
  useProjectStore.getState().adoptOpenedProject({ id, path: `/${id}`, name: id, writable: true, open: true } as never)

beforeEach(() => {
  setCurrentProjectId('p1')
  useUiStore.getState().setStatus(null)
})

describe('换项目撤掉旧项目的 toast', () => {
  it.each([
    ['render.failed', renderFailureOwner('p1', 'Fig1.pdf')],
    ['project.error', 'project.error:p1'],
    ['export.result', 'export.result'],
  ])('%s 主人的 toast：切到 B 之后不在', async (_n, owner) => {
    useUiStore.getState().setStatus(literal('A 的问题'), 'error', { owner })
    expect(useUiStore.getState().status).not.toBeNull() // 尺子是活的
    await switchTo('p2')
    expect(useUiStore.getState().status).toBeNull()
  })

  it('对照：别的主人 / 没有主人的 toast 不被连带撤掉', async () => {
    useUiStore.getState().setStatus(literal('别的事'), 'error', { owner: 'something.else' })
    await switchTo('p2')
    expect(useUiStore.getState().status).not.toBeNull()
    useUiStore.getState().setStatus(literal('没有主人'), 'error')
    await switchTo('p3')
    expect(useUiStore.getState().status).not.toBeNull()
  })
})
