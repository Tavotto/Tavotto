/**
 * 导出 / 打开项目包失败的红色提示带出口（docs/rules/frontend/ui-visual-discipline.md「失败出口三种结局」）：
 * 提示带「再试一次」，点下去重做**同一次**原动作；对象变了（换了排版 / 项目）只收提示、不重放；
 * 重试又失败换成「发给我们看看」。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const createPackage = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createPackage: (...a: unknown[]) => createPackage(...a),
}))
const openPackageFile = vi.fn()
vi.mock('@/lib/packageImport', () => ({ openPackageFile: (...a: unknown[]) => openPackageFile(...a) }))

import { formatMessage } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'
import { exportPackage, openPackageWithRetry } from '@/store/packageActions'

const ui = () => useUiStore.getState()
const label = () => formatMessage(ui().statusAction?.label ?? null)
const statusKey = () => ui().status?.key

beforeEach(async () => {
  createPackage.mockReset()
  openPackageFile.mockReset()
  setCurrentProjectId('A')
  useUiStore.setState({ status: null, statusAction: null, statusOwner: null })
  useDiagSendStore.setState({ capability: { enabled: true } as never, open: false })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'pkg-doc')
})

describe('导出项目包失败', () => {
  it('提示带「再试一次」，点下去重做同一次导出，成功后收掉失败提示', async () => {
    createPackage.mockRejectedValueOnce(new Error('磁盘满了'))
    await exportPackage()
    expect(statusKey()).toBe('status.packageFailed')
    expect(label()).toBe('再试一次')
    createPackage.mockResolvedValueOnce({ name: 'x.tavotto', assets: 2 })
    ui().statusAction!.run()
    await vi.waitFor(() => expect(statusKey()).toBe('status.packaged'))
    expect(createPackage).toHaveBeenCalledTimes(2)
  })

  it('点的时候已换了排版：只收提示，不把别的排版打成包', async () => {
    createPackage.mockRejectedValueOnce(new Error('x'))
    await exportPackage()
    const retry = ui().statusAction!
    await useDocumentStore.getState().switchDocument(emptyProject(), 'other-doc')
    useUiStore.setState({ status: null, statusAction: null }) // 换文档本来就会清；这里钉的是旧按钮点下去也不重放
    retry.run()
    await Promise.resolve()
    expect(createPackage).toHaveBeenCalledTimes(1)
  })

  it('点的时候已换了项目：只收提示', async () => {
    createPackage.mockRejectedValueOnce(new Error('x'))
    await exportPackage()
    const retry = ui().statusAction!
    setCurrentProjectId('B')
    retry.run()
    await Promise.resolve()
    expect(createPackage).toHaveBeenCalledTimes(1)
  })

  it('重试又失败：换成「发给我们看看」', async () => {
    createPackage.mockRejectedValue(new Error('还是不行'))
    await exportPackage()
    ui().statusAction!.run()
    await vi.waitFor(() => expect(createPackage).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(label()).toBe('发送问题反馈'))
    ui().statusAction!.run()
    expect(useDiagSendStore.getState().open).toBe(true)
  })
})

describe('打开项目包失败', () => {
  const file = new File(['x'], 'a.tavotto')

  it('提示带「再试一次」，点下去重开同一个文件', async () => {
    openPackageFile.mockRejectedValueOnce(new Error('不是项目包'))
    await openPackageWithRetry(file, 0)
    expect(statusKey()).toBe('status.packageOpenFailed')
    expect(label()).toBe('再试一次')
    openPackageFile.mockResolvedValueOnce(undefined)
    ui().statusAction!.run()
    await vi.waitFor(() => expect(openPackageFile).toHaveBeenCalledTimes(2))
    expect(openPackageFile).toHaveBeenLastCalledWith(file)
    expect(ui().statusAction).toBeNull()
  })

  it('点的时候已换了项目：不在别的项目里冒出一份新排版', async () => {
    openPackageFile.mockRejectedValueOnce(new Error('x'))
    await openPackageWithRetry(file, 0)
    const retry = ui().statusAction!
    setCurrentProjectId('B')
    retry.run()
    await Promise.resolve()
    expect(openPackageFile).toHaveBeenCalledTimes(1)
  })

  it('重试又失败：换成「发给我们看看」', async () => {
    openPackageFile.mockRejectedValue(new Error('坏包'))
    await openPackageWithRetry(file, 0)
    ui().statusAction!.run()
    await vi.waitFor(() => expect(openPackageFile).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(label()).toBe('发送问题反馈'))
  })
})
