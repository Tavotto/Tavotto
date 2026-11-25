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
vi.mock('@/lib/packageImport', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/packageImport')>()),
  openPackageFile: (...a: unknown[]) => openPackageFile(...a),
}))
let desktop = false
vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  isDesktop: () => desktop,
}))

import { formatMessage } from '@/i18n'
import { ApiError } from '@/lib/api'
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
  desktop = false
  createPackage.mockReset()
  openPackageFile.mockReset()
  setCurrentProjectId('A')
  useUiStore.setState({ status: null, statusAction: null, statusOwner: null })
  useDiagSendStore.setState({ capability: { enabled: true } as never, open: false })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'pkg-doc')
})

describe('导出项目包失败', () => {
  it('打包整份普通排版时提交全部画布进入可安装导出链', async () => {
    const document = emptyProject()
    const first = document.canvases[0]
    document.canvases.push({ ...first, id: 'detail', name: 'Detail' })
    document.activeCanvasId = 'detail'
    await useDocumentStore.getState().switchDocument(document, 'mixed-native')
    createPackage.mockResolvedValueOnce({ name: 'mixed.tavotto', assets: 2, document_format: 'v4', missing: [] })

    await exportPackage()

    expect(createPackage).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ schema: 3, activeCanvasId: 'detail' }),
      {},
      { pj: 'A', canvasIds: [first.id, 'detail'] },
    )
    expect(useDocumentStore.getState().buildProject().schema).toBe(3)
  })

  it('有缺失图表附件的包不能报导出成功', async () => {
    createPackage.mockResolvedValueOnce({ name: 'x.tavotto', assets: 1, missing_chart_sources: ['bad'] })
    await exportPackage()
    expect(statusKey()).toBe('status.packageFailed')
    expect(formatMessage(ui().status)).toContain('图表来源缺失或损坏')
  })
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

  it('仅打包当前画布时，切到另一张画布后旧重试不再导出', async () => {
    const document = emptyProject()
    const first = document.canvases[0]
    document.canvases.push({ ...first, id: 'sibling', name: 'Sibling' })
    await useDocumentStore.getState().switchDocument(document, 'two-canvases')
    createPackage.mockRejectedValueOnce(new Error('x'))
    await exportPackage('canvas')
    expect(createPackage).toHaveBeenCalledWith(first.name, expect.objectContaining({
      activeCanvasId: first.id,
    }), {}, { pj: 'A', canvasId: first.id })
    const retry = ui().statusAction!
    useDocumentStore.getState().switchCanvas('sibling')
    expect(useDocumentStore.getState().activeCanvasId).toBe('sibling')
    retry.run()
    await Promise.resolve()
    expect(createPackage).toHaveBeenCalledTimes(1)
  })

  it('重试时仍打包用户选定的画布，即使当前画布已切换', async () => {
    const document = emptyProject()
    const first = document.canvases[0]
    document.canvases.push({ ...first, id: 'detail', name: 'Detail' }, { ...first, id: 'private', name: 'Private' })
    await useDocumentStore.getState().switchDocument(document, 'three-canvases')
    createPackage.mockRejectedValueOnce(new Error('x'))
    const scope = { canvasIds: [first.id, 'detail'] }
    await exportPackage(scope)
    const retry = ui().statusAction!
    useDocumentStore.getState().switchCanvas('private')
    createPackage.mockResolvedValueOnce({ name: 'x.tavotto', assets: 2 })
    retry.run()
    await vi.waitFor(() => expect(createPackage).toHaveBeenCalledTimes(2))
    expect(createPackage).toHaveBeenLastCalledWith(
      expect.any(String),
      expect.objectContaining({ activeCanvasId: 'private' }),
      {},
      { pj: 'A', canvasId: undefined, ...scope },
    )
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

describe('打开更新版本打出的项目包', () => {
  const tooNew = () =>
    new ApiError('这个项目包来自更新的 Tavotto', 400, { code: 'package_schema_too_new', params: { schema: 4 } })

  it('桌面版：说「来自更新的版本」，主按钮是「检查更新」，不给「再试一次」', async () => {
    desktop = true
    openPackageFile.mockRejectedValueOnce(tooNew())
    await openPackageWithRetry(new File(['x'], 'new.tavotto'), 0)
    expect(statusKey()).toBe('status.packageTooNew')
    expect(label()).toBe('检查更新')
  })

  it('浏览器版没有更新通道：换成「发送问题反馈」', async () => {
    openPackageFile.mockRejectedValueOnce(tooNew())
    await openPackageWithRetry(new File(['x'], 'new.tavotto'), 0)
    expect(statusKey()).toBe('status.packageTooNew')
    expect(label()).toBe('发送问题反馈')
  })
})
