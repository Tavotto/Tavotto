/**
 * T04：失败提示处的「本次问题的诊断」。
 *
 * 看护四件事：默认只多一个折叠标题（卡片一句话，不堆新按钮）、请求绑定**组件出现时**的项目与那一次的 id、
 * 记录没有 / 过期时如实说没有（且不去下载别的东西冒充）、两个语种的文案都在。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TaskDiagnostic } from '@/components/TaskDiagnostic'
import { i18n, resources } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { visibleBlocks, visiblePrimaryButtons } from '@/test/visibleBlocks'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLDivElement
let root: Root
const fetchMock = vi.fn()
const clicks: string[] = []

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  fetchMock.mockReset()
  clicks.length = 0
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:x', revokeObjectURL: () => {} }))
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    clicks.push(this.download)
  })
  setCurrentProjectId('pj-open')
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  setCurrentProjectId(null)
})

const mount = (props: Partial<React.ComponentProps<typeof TaskDiagnostic>> = {}) =>
  act(() => root.render(<TaskDiagnostic kind="export" refId="job1234" {...props} />))

const clickDownload = async () => {
  const btn = host.querySelector('button') as HTMLButtonElement
  await act(async () => {
    btn.click()
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe('TaskDiagnostic', () => {
  it.each(Object.keys(resources))('adds exactly one collapsed heading and no primary button (%s)', async (lng) => {
    await i18n.changeLanguage(lng)
    mount()
    const details = host.querySelector('details') as HTMLDetailsElement
    expect(details.open).toBe(false)
    // 默认可见的只有折叠标题那一块文字，没有主按钮
    expect(visibleBlocks(host)).toHaveLength(1)
    expect(visiblePrimaryButtons(host)).toBe(0)
    expect(visibleBlocks(host)[0].text).toBe((resources as Record<string, any>)[lng].dialogs.taskDiagnostic.title)
  })

  it('asks for exactly that attempt, in the project the failure was shown in', async () => {
    fetchMock.mockResolvedValue(
      new Response('{}', {
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="tavotto-task-diagnostic-1.json"' },
      }),
    )
    mount()
    setCurrentProjectId('pj-other') // 用户在失败提示还挂着时切了项目
    await clickDownload()
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/api/diagnostics/task?kind=export&ref=job1234')
    expect(String(url)).toContain('pj=pj-open')
    expect(String(url)).not.toContain('pj-other')
    expect((init as RequestInit).headers).toMatchObject({ 'X-Tavotto-Project': 'pj-open' })
    expect(clicks).toEqual(['tavotto-task-diagnostic-1.json'])
  })

  it.each([
    ['expired', 'expired'],
    ['not_found', 'notFound'],
  ] as const)('says the record is gone (%s) and downloads nothing', async (reason, key) => {
    await i18n.changeLanguage('zh-CN')
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ available: false, reason }), { status: 404 }))
    mount()
    await clickDownload()
    expect(clicks).toEqual([])
    expect(fetchMock).toHaveBeenCalledTimes(1) // 没有退而去取别的诊断包
    const gone = host.querySelector('[data-task-diagnostic-gone]')
    expect(gone?.getAttribute('data-task-diagnostic-gone')).toBe(reason)
    expect(gone?.textContent).toBe((resources as Record<string, any>)['zh-CN'].dialogs.taskDiagnostic[key])
  })

  it('reports a server error as a failure, not as a missing record', async () => {
    fetchMock.mockResolvedValue(new Response('boom', { status: 500 }))
    mount()
    await clickDownload()
    expect(host.querySelector('[role="alert"]')).not.toBeNull()
    expect(host.querySelector('[data-task-diagnostic-gone]')).toBeNull()
    expect(clicks).toEqual([])
  })

  it('unfolded mode (inside an already folded area) renders only the button', () => {
    mount({ folded: false })
    expect(host.querySelector('details')).toBeNull()
    expect(host.querySelectorAll('button')).toHaveLength(1)
  })
})
