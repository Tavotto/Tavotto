/**
 * 「最近的排版」菜单点一行读不了：失败出口（docs/rules/frontend/ui-visual-discipline.md「失败出口三种结局」）。
 *  - 副本已不在本机 → 结局①：把这一行从最近列表去掉并说一句（info，不是红字）；本机槽位里的数据不动；
 *  - 来自更新的版本读不了 →「检查更新」（桌面版，与顶栏说明块同一个动作）；浏览器版没有更新通道 →「发给我们看看」。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const shell = vi.hoisted(() => ({ desktop: false }))
vi.mock('@/lib/desktop', async (original) => ({
  ...(await original<typeof import('@/lib/desktop')>()),
  isDesktop: () => shell.desktop,
}))

import { formatMessage } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject } from '@/types/document'
import { openRecentDocument } from './actions'
import { useDiagSendStore } from './diagSendStore'
import { useDocumentStore } from './documentStore'
import { useUiStore } from './uiStore'
import { useUpdateStore } from './updateStore'

let serverDoc: unknown = null
globalThis.fetch = (async (url: unknown) => {
  const u = new URL(String(url), 'http://x')
  if (/^\/api\/autosave\/[^/]+$/.test(u.pathname)) {
    return serverDoc === null
      ? new Response('{}', { status: 404 })
      : new Response(JSON.stringify(serverDoc), { status: 200, headers: { 'X-Tavotto-Revision': 'r1' } })
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

const ui = () => useUiStore.getState()
const label = () => formatMessage(ui().statusAction?.label ?? null)
const recentIds = () => useDocumentStore.getState().recentDocs.map((e) => e.id)

beforeEach(() => {
  localStorage.clear()
  serverDoc = null
  shell.desktop = false
  setCurrentProjectId(null)
  useUiStore.setState({ status: null, statusAction: null, statusOwner: null })
  useDiagSendStore.setState({ capability: { enabled: true } as never, open: false })
  localStorage.setItem('tavotto.docIndex', JSON.stringify([
    { id: 'd_old', name: '旧排版', savedAt: 1, objects: 0 },
    { id: 'd_keep', name: '别的排版', savedAt: 2, objects: 0 },
  ]))
  useDocumentStore.setState({ recentDocs: [
    { id: 'd_old', name: '旧排版', savedAt: 1, objects: 0 },
    { id: 'd_keep', name: '别的排版', savedAt: 2, objects: 0 },
  ] as never })
})

describe('副本已不在', () => {
  it('这一行从最近列表去掉（别的行不动）并说一句；不是红字、没有多余按钮', async () => {
    await openRecentDocument('d_old')
    expect(ui().status).toMatchObject({ key: 'status.recentMissing' })
    expect(ui().statusTone).toBe('info')
    expect(ui().statusAction).toBeNull()
    expect(recentIds()).toEqual(['d_keep'])
  })
})

describe('来自更新版本、读不了', () => {
  beforeEach(() => { serverDoc = { ...emptyProject(), schema: 999 } })

  it('桌面版：红字带「检查更新」，点下去走与顶栏同一个检查', async () => {
    shell.desktop = true
    const checkDesktop = vi.fn().mockResolvedValue(undefined)
    useUpdateStore.setState({ checkDesktop, desktopUpdate: null, desktopError: null } as never)
    await openRecentDocument('d_old')
    expect(ui().status).toMatchObject({ key: 'status.recentTooNew' })
    expect(ui().statusTone).toBe('error')
    expect(label()).toBe('检查更新')
    expect(recentIds()).toEqual(['d_old', 'd_keep']) // 文件好好的，不从列表去掉
    ui().statusAction!.run()
    await vi.waitFor(() => expect(checkDesktop).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(ui().status).toMatchObject({ key: 'docBanner.updateNone' }))
  })

  it('浏览器版没有更新通道：红字带「发给我们看看」，点下去打开反馈对话框', async () => {
    await openRecentDocument('d_old')
    expect(ui().status).toMatchObject({ key: 'status.recentTooNew' })
    expect(label()).toBe('发送问题反馈')
    ui().statusAction!.run()
    expect(useDiagSendStore.getState().open).toBe(true)
  })
})
