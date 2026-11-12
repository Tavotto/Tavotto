/** 打开 / 新建项目失败的出口：找不到 →「重新选择文件夹」（不动最近列表）；其他 →「再试一次」，再失败 → 反馈 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { formatMessage } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { useDiagSendStore } from './diagSendStore'
import { reportProjectOpenFailure } from './projectOpenFailure'
import { useProjectStore } from './projectStore'
import { useUiStore } from './uiStore'

const ui = () => useUiStore.getState()
const label = () => formatMessage(ui().statusAction?.label ?? null)
const recent = (exists: boolean) => [{ path: '/Volumes/disk/figs', name: 'figs', exists, last_opened: 1, current: false }]
const refreshRecent = vi.fn()
const retry = vi.fn()
const reselect = vi.fn()
const report = (o: Partial<Parameters<typeof reportProjectOpenFailure>[0]> = {}) =>
  reportProjectOpenFailure({ path: '/Volumes/disk/figs', create: false, attempt: 0, retry, reselect, ...o })

beforeEach(() => {
  retry.mockReset(); reselect.mockReset(); refreshRecent.mockReset().mockResolvedValue(undefined)
  setCurrentProjectId('A')
  useUiStore.setState({ status: null, statusAction: null, statusOwner: null })
  useDiagSendStore.setState({ capability: { enabled: true } as never, open: false })
  useProjectStore.setState({ recent: recent(true), refreshRecent } as never)
})

describe('文件夹找不到（后端的 exists=false）', () => {
  beforeEach(() => { useProjectStore.setState({ recent: recent(false) } as never) })

  it('一句话 +「重新选择文件夹」，点下去打开文件夹选择入口，提示收掉', async () => {
    await report()
    expect(ui().status).toMatchObject({ key: 'workspace.openMissing', values: { name: 'figs' } })
    expect(ui().statusTone).toBe('error')
    expect(label()).toBe('重新选择文件夹')
    ui().statusAction!.run()
    expect(reselect).toHaveBeenCalledTimes(1)
    expect(ui().status).toBeNull()
    expect(retry).not.toHaveBeenCalled()
  })

  it('不把它从最近列表去掉（移动硬盘没插也会找不到），先重读一次列表拿 exists', async () => {
    await report()
    expect(refreshRecent).toHaveBeenCalledTimes(1)
    expect(useProjectStore.getState().recent).toHaveLength(1)
  })

  it('新建项目失败不算「找不到」：走「再试一次」', async () => {
    await report({ create: true })
    expect(ui().status).toMatchObject({ key: 'workspace.createFailed' })
    expect(label()).toBe('再试一次')
  })
})

describe('其他失败', () => {
  it('一句话 +「再试一次」，点下去重做同一次打开（序号 +1）', async () => {
    await report()
    expect(ui().status).toMatchObject({ key: 'workspace.openFailed', values: { name: 'figs' } })
    expect(label()).toBe('再试一次')
    ui().statusAction!.run()
    expect(retry).toHaveBeenCalledWith(1)
    expect(reselect).not.toHaveBeenCalled()
  })

  it('点的时候已打开了别的项目：只收提示，不重放', async () => {
    await report()
    const click = ui().statusAction!
    setCurrentProjectId('B')
    click.run()
    expect(retry).not.toHaveBeenCalled()
    expect(ui().status).toBeNull()
  })

  it('重试又失败：换成「发送问题反馈」', async () => {
    await report({ attempt: 1 })
    expect(label()).toBe('发送问题反馈')
    ui().statusAction!.run()
    expect(useDiagSendStore.getState().open).toBe(true)
  })

  it('最近列表里没有这条路径：名字取路径末段，照样给「再试一次」', async () => {
    useProjectStore.setState({ recent: [] } as never)
    await report({ path: 'D:\\data\\runs\\' })
    expect(ui().status).toMatchObject({ key: 'workspace.openFailed', values: { name: 'runs' } })
    expect(label()).toBe('再试一次')
  })
})

describe('后端原话只进「复制」，不进界面句子', () => {
  it('其他失败：copyDetail = 后端原话，界面句子仍是人话', async () => {
    await report({ error: new Error('PermissionError: [Errno 13] /Volumes/disk/figs') })
    expect(ui().status).toMatchObject({ key: 'workspace.openFailed' })
    expect(ui().statusCopyDetail).toBe('PermissionError: [Errno 13] /Volumes/disk/figs')
  })

  it('文件夹找不到、新建失败同样带原话；没给 error 就没有', async () => {
    useProjectStore.setState({ recent: recent(false) } as never)
    await report({ error: new Error('目录不存在') })
    expect(ui().statusCopyDetail).toBe('目录不存在')
    await report({ create: true, error: new Error('exists') })
    expect(ui().statusCopyDetail).toBe('exists')
    await report()
    expect(ui().statusCopyDetail).toBeNull()
  })
})
