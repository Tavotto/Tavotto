/**
 * `runNotebook`（ADR 0179）：所有入口共用的「转成脚本并运行」。判据的主语：发给后端的是路径抽取还是内容上传、
 * 发给准备会话 store 的 `open` 入参、留给用户的那一句（成功说一句、失败带「发送问题反馈」）、换项目后的静默。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { formatMessage } from '@/i18n'
import { ApiError } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useUiStore } from '@/store/uiStore'
import { runNotebook } from './notebookRun'

const mocks = vi.hoisted(() => ({ extractNotebook: vi.fn(), importNotebook: vi.fn() }))
vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  extractNotebook: mocks.extractNotebook,
  importNotebook: mocks.importNotebook,
}))

const open = vi.fn(async (..._a: unknown[]) => {})
const load = vi.fn(async () => {})
const said = () => formatMessage(useUiStore.getState().status)
const ok = (script: string, status = 'created') => ({ notebook: 'x.ipynb', script, status })

beforeEach(() => {
  vi.clearAllMocks()
  setCurrentProjectId('pj_a')
  useProjectPreparationStore.setState({ open } as never)
  useScriptLibraryStore.setState({ load } as never)
  useDiagSendStore.setState({ capability: { enabled: true } as never, open: false })
  useUiStore.setState({ status: null })
})

describe('runNotebook', () => {
  it('路径：后端抽取，运行抽出来的 .py（项目相对路径），刷新脚本库，说「已从…生成…」', async () => {
    mocks.extractNotebook.mockResolvedValue(ok('sub/x.py'))
    expect(await runNotebook({ path: 'sub/x.ipynb' })).toBe(true)
    expect(mocks.extractNotebook).toHaveBeenCalledExactlyOnceWith('sub/x.ipynb', 'pj_a')
    expect(open).toHaveBeenCalledExactlyOnceWith({ script: 'sub/x.py', reuse_last_success: true }, { run: true, placeIn: undefined })
    expect(load).toHaveBeenCalled()
    expect(said()).toBe('已从「x.ipynb」生成「x.py」，正在运行它')
    expect(useUiStore.getState().statusAction).toBeNull()
  })

  it('后端给了改过的名字（用户自己有同名脚本）：运行的、说的都是后端回的那个名字', async () => {
    mocks.extractNotebook.mockResolvedValue(ok('x (notebook).py', 'renamed'))
    await runNotebook({ path: 'x.ipynb' })
    expect(open).toHaveBeenCalledWith({ script: 'x (notebook).py', reuse_last_success: true }, expect.anything())
    expect(said()).toContain('x (notebook).py')
  })

  it('内容：File 交给后端上传抽取，不发路径', async () => {
    mocks.importNotebook.mockResolvedValue(ok('x.py'))
    const file = new File(['{}'], 'x.ipynb')
    expect(await runNotebook({ file }, { place: { canvasId: 'c1', at: { x: 1, y: 2 } } })).toBe(true)
    expect(mocks.importNotebook).toHaveBeenCalledExactlyOnceWith(file, 'x.ipynb', 'pj_a')
    expect(mocks.extractNotebook).not.toHaveBeenCalled()
    expect(open).toHaveBeenCalledWith(expect.anything(), { run: true, placeIn: { canvasId: 'c1', at: { x: 1, y: 2 } } })
  })

  it('同一次放下里没处理的文件数进那一句', async () => {
    mocks.extractNotebook.mockResolvedValue(ok('x.py'))
    await runNotebook({ path: 'x.ipynb' }, { skipped: 2 })
    expect(said()).toContain('其余 2 个文件这次没有处理')
  })

  it.each([
    ['notebook_unreadable', '不是能打开的笔记本'],
    ['notebook_unsupported_version', '很旧版本'],
    ['notebook_no_code', '没有可以运行的代码'],
    ['notebook_syntax_error', '第 3 段代码'],
    ['asset_import_not_writable', '不允许写入'],
    ['asset_import_disk_full', '磁盘空间'],
  ])('失败 %s：一句话说原因 + 发送问题反馈，不运行', async (code, phrase) => {
    mocks.extractNotebook.mockRejectedValue(new ApiError('x', 422, { code, params: { cell: 3, line: 1 } }))
    expect(await runNotebook({ path: 'x.ipynb' })).toBe(false)
    expect(open).not.toHaveBeenCalled()
    expect(said()).toContain('「x.ipynb」没能转成脚本')
    expect(said()).toContain(phrase)
    expect(useUiStore.getState().statusTone).toBe('error')
    expect(formatMessage(useUiStore.getState().statusAction!.label)).toBe('发送问题反馈')
  })

  it('反馈入口被关：句子照说，不画按钮', async () => {
    useDiagSendStore.setState({ capability: null, open: false })
    mocks.extractNotebook.mockRejectedValue(new ApiError('x', 422, { code: 'notebook_no_code', params: {} }))
    await runNotebook({ path: 'x.ipynb' })
    expect(said()).toContain('没能转成脚本')
    expect(useUiStore.getState().statusAction).toBeNull()
  })

  it('没有项目：不发请求，说这里没有项目文件夹', async () => {
    setCurrentProjectId(null)
    expect(await runNotebook({ file: new File(['{}'], 'x.ipynb') })).toBe(false)
    expect(mocks.importNotebook).not.toHaveBeenCalled()
    expect(said()).toContain('没有项目文件夹')
  })

  it('请求在途时换了项目：不运行、不说', async () => {
    mocks.extractNotebook.mockImplementation(async () => {
      setCurrentProjectId('pj_b')
      return ok('x.py')
    })
    expect(await runNotebook({ path: 'x.ipynb' })).toBe(false)
    expect(open).not.toHaveBeenCalled()
    expect(useUiStore.getState().status).toBeNull()
  })

  it('请求在途时换了项目、又失败：同样不说', async () => {
    mocks.extractNotebook.mockImplementation(async () => {
      setCurrentProjectId('pj_b')
      throw new ApiError('x', 422, { code: 'notebook_no_code', params: {} })
    })
    await runNotebook({ path: 'x.ipynb' })
    expect(useUiStore.getState().status).toBeNull()
  })

  it('返回的脚本路径里有首尾带空白的段：不运行（与主页同一道闸）', async () => {
    mocks.extractNotebook.mockResolvedValue(ok(' sub/x.py'))
    expect(await runNotebook({ path: ' sub/x.ipynb' })).toBe(false)
    expect(open).not.toHaveBeenCalled()
    expect(useUiStore.getState().statusTone).toBe('error')
  })
})
