/**
 * 编辑器里把 .py 拖到画布上（2026-10-27 用户拍板）。判据的主语：**发给准备会话 store 的 `open` 入参**（哪个项目相对脚本、
 * 带不带 `run`、要放进哪张画布）与说给用户的那一句；项目外 / 不是脚本 / 教程层盖着时一个 `open` 都不发。
 */
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { formatMessage } from '@/i18n'
import { ApiError, type ImportedAsset, type PanelInfo } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useAssetStore } from '@/store/assetStore'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useDocumentStore } from '@/store/documentStore'
import { useForeignScriptStore } from '@/store/foreignScriptStore'
import { canvasToDoc, emptyProject } from '@/types/document'
import { acceptsScriptDrag, runDroppedScript, useCanvasScriptDrop } from './useCanvasScriptDrop'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useProjectStore } from '@/store/projectStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useUiStore } from '@/store/uiStore'

const mocks = vi.hoisted(() => ({
  importAsset: vi.fn(),
  extractNotebook: vi.fn(),
  importNotebook: vi.fn(),
  openPackageFile: vi.fn(),
  isDesktop: vi.fn(() => false),
  pickScriptFile: vi.fn(async () => null as string | null),
  nativeAvailable: false,
  nativeHandler: null as null | ((d: unknown) => void),
}))
vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  importAsset: mocks.importAsset,
  extractNotebook: mocks.extractNotebook,
  importNotebook: mocks.importNotebook,
}))
vi.mock('@/lib/packageImport', () => ({ openPackageFile: mocks.openPackageFile }))
vi.mock('@/lib/desktop', async (orig) => ({
  ...(await orig<typeof import('@/lib/desktop')>()),
  isDesktop: mocks.isDesktop,
  nativeFileDropAvailable: async () => mocks.nativeAvailable,
  onNativeFileDrop: async (h: (d: unknown) => void) => {
    mocks.nativeHandler = h
    return () => {}
  },
  pickScriptFile: mocks.pickScriptFile,
}))

const open = vi.fn(async () => {})
const place = { canvasId: 'c_paper', at: { x: 40, y: 30 } }

beforeEach(() => {
  vi.clearAllMocks()
  open.mockClear()
  useProjectPreparationStore.setState({ open } as never)
  useProjectStore.setState({ project: { open: true, figures_dir: '/Users/me/paper' } as never, switching: false })
  useOnboardingStore.setState({ surface: 'none' } as never)
  useUiStore.setState({ status: null })
})

describe('runDroppedScript', () => {
  it('项目里的脚本：「打开并运行」它，并记下要放进的画布与落点', () => {
    runDroppedScript('/Users/me/paper/plots/Fig5_spectra.py', place)
    expect(open).toHaveBeenCalledExactlyOnceWith({ script: 'plots/Fig5_spectra.py', reuse_last_success: true }, { run: true, placeIn: place })
  })

  it('项目外的脚本：不运行、不切项目，说清楚', () => {
    runDroppedScript('/Users/me/other/fig.py', place)
    expect(open).not.toHaveBeenCalled()
    expect(JSON.stringify(useUiStore.getState().status)).toContain('outsideProject')
  })

  it('文件名首尾带空格：与主页同一道闸，不运行', () => {
    runDroppedScript('/Users/me/paper/ fig.py', place)
    expect(open).not.toHaveBeenCalled()
  })
})

describe('acceptsScriptDrag', () => {
  it('系统拖来的文件才接（素材卡的内部拖动不归它管）', () => {
    expect(acceptsScriptDrag(['Files'])).toBe(true)
    expect(acceptsScriptDrag(['application/x-panel-id', 'text/plain'])).toBe(false)
    expect(acceptsScriptDrag(['text/plain'])).toBe(false)
  })

  it('教程层盖着 / 正在切项目：不接', () => {
    useOnboardingStore.setState({ surface: 'tutorial' } as never)
    expect(acceptsScriptDrag(['Files'])).toBe(false)
    useOnboardingStore.setState({ surface: 'none' } as never)
    useProjectStore.setState({ switching: true })
    expect(acceptsScriptDrag(['Files'])).toBe(false)
  })
})

/* ------------------------------------------------------------------------------------------------
 * 画布上的整次放下（ADR 0176）。判据的主语：每个文件落到哪个出口（上传并放置 / 运行 / 项目包 / 说一句 + 按钮）、
 * 最终留给用户的那一句话（有问题时是否带「发送问题反馈」主按钮）。壳不可用（`nativeFileDropAvailable` = false）。
 * ---------------------------------------------------------------------------------------------- */

const png = (name: string) => new File([new Uint8Array([1])], name, { type: 'image/png' })
const named = (name: string) => new File([new Uint8Array([1])], name)
const dropOf = (files: File[], uris: string[] = []) => ({
  types: uris.length ? ['Files', 'text/uri-list'] : ['Files'],
  getData: (f: string) => (f === 'text/uri-list' ? uris.join('\r\n') : ''),
  files,
})
const uri = (p: string) => `file://${p}`
const asset = (name: string): ImportedAsset => ({ id: name, name: name.replace(/\.\w+$/, ''), file: name, kind: 'raster', reused: false, renamed: false, bytes: 1 })
const info = (id: string) => ({ id, name: id.replace(/\.\w+$/, ''), folder: '.', mtime: 1, kind: 'raster', native_w_mm: 40, native_h_mm: 30, px_w: 4, px_h: 3 }) as PanelInfo
const status = () => useUiStore.getState()
const text = () => formatMessage(status().status)

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

async function drop(files: File[], uris: string[] = [], native?: unknown) {
  let handler!: ReturnType<typeof useCanvasScriptDrop>
  const Probe = () => {
    handler = useCanvasScriptDrop()
    return null
  }
  const host = document.createElement('div')
  const root = createRoot(host)
  await act(async () => root.render(createElement(Probe))) // 壳探测落定（false）
  const canvasId = useDocumentStore.getState().activeCanvasId
  await act(async () => {
    handler(dropOf(files, uris), { canvasId, at: { x: 50, y: 40 } })
    if (native) mocks.nativeHandler!(native)
    await new Promise((r) => setTimeout(r, 0))
    await new Promise((r) => setTimeout(r, 0))
  })
  act(() => root.unmount())
}

describe('整次放下', () => {
  beforeEach(() => {
    setCurrentProjectId('pj_a')
    const base = canvasToDoc(emptyProject().canvases[0])
    useDocumentStore.setState({ doc: { ...base, objects: [] }, past: [], future: [], txn: null })
    useDiagSendStore.setState({ capability: { enabled: true } as never, open: false })
    useAssetStore.setState({ panels: [], byId: {}, unsupported: [], loaded: true } as never)
    mocks.nativeAvailable = false
    mocks.importAsset.mockImplementation(async (_f: Blob, name: string) => asset(name))
    vi.spyOn(useAssetStore.getState(), 'load').mockImplementation(async () => {
      const got = await Promise.all(mocks.importAsset.mock.results.map((r) => (r.value as Promise<ImportedAsset>).catch(() => null)))
      const panels = got.filter((a): a is ImportedAsset => !!a).map((a) => info(a.id))
      useAssetStore.setState({ panels, byId: Object.fromEntries(panels.map((p) => [p.id, p])) } as never)
      return { panels } as never
    })
    useForeignScriptStore.setState({ offer: null })
  })

  it('PNG：上传并放进画布，说一句，不再说「不是 Python 脚本」', async () => {
    await drop([png('fig.png')])
    expect(mocks.importAsset).toHaveBeenCalledExactlyOnceWith(expect.any(File), 'fig.png', 'pj_a')
    expect(useDocumentStore.getState().doc.objects).toHaveLength(1)
    expect(text()).toContain('fig')
    expect(text()).not.toContain('Python')
    expect(status().statusAction).toBeNull()
    expect(open).not.toHaveBeenCalled()
  })

  it('多张图 + 一个不认识的文件：图全放上，最后一句说没成的（带主按钮），不静默丢', async () => {
    await drop([png('a.png'), named('data.csv'), png('b.png')])
    expect(useDocumentStore.getState().doc.objects).toHaveLength(2)
    expect(text()).toContain('data.csv')
    expect(status().statusTone).toBe('error')
    expect(formatMessage(status().statusAction!.label)).toBe('发送问题反馈')
  })

  it.each(['data.csv', 'photo.webp', 'mystery'])('只有「%s」：一句话 + 发送问题反馈，没有上传、没有「请拖入 .py」', async (name) => {
    await drop([named(name)])
    expect(mocks.importAsset).not.toHaveBeenCalled()
    expect(text()).toContain(name)
    expect(text()).not.toContain('.py')
    expect(status().statusAction).not.toBeNull()
  })

  it('后端不收（不是图片）：原因进那一句话，带反馈按钮，画布上不留空面板', async () => {
    mocks.importAsset.mockRejectedValue(new ApiError('x', 415, { code: 'asset_import_unsupported_type', params: {} }))
    await drop([png('fake.png')])
    expect(useDocumentStore.getState().doc.objects).toHaveLength(0)
    expect(text()).toContain('fake.png')
    expect(status().statusAction).not.toBeNull()
  })

  it('项目包：走导入项目包的同一条流程；同批别的文件说一声没处理', async () => {
    mocks.openPackageFile.mockResolvedValue(undefined)
    await drop([named('paper.tavotto'), png('a.png')])
    expect(mocks.openPackageFile).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ name: 'paper.tavotto' }))
    expect(mocks.importAsset).not.toHaveBeenCalled()
    expect(text()).toContain('paper.tavotto')
    expect(text()).toContain('1')
  })

  it('项目包打不开：一句话 + 发送问题反馈', async () => {
    mocks.openPackageFile.mockRejectedValue(new ApiError('x', 400, { code: 'package_invalid', params: { reason: 'bad zip' } }))
    await drop([named('x.zip')])
    expect(text()).toContain('x.zip')
    expect(status().statusTone).toBe('error')
    expect(status().statusAction).not.toBeNull()
  })

  it('项目里的 .py（壳没来，浏览器给了 file:// 路径）：打开并运行、放进这张画布', async () => {
    await drop([named('plot.py')], [uri('/Users/me/paper/plot.py')])
    expect(open).toHaveBeenCalledExactlyOnceWith(
      { script: 'plot.py', reuse_last_success: true },
      { run: true, placeIn: expect.objectContaining({ at: { x: 50, y: 40 } }) },
    )
    expect(text()).toContain('plot.py')
  })

  it('项目外的 .py：一句话 + 主按钮「打开它所在的项目」，按钮打开那个项目并运行它', async () => {
    const openProject = vi.fn(async () => ({}) as never)
    useProjectStore.setState({ open: openProject } as never)
    await drop([named('fig.py')], [uri('/Users/me/other/fig.py')])
    expect(open).not.toHaveBeenCalled()
    expect(text()).toContain('fig.py')
    expect(formatMessage(status().statusAction!.label)).toBe('打开它所在的项目')
    await act(async () => {
      status().statusAction!.run()
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(openProject).toHaveBeenCalledExactlyOnceWith('/Users/me/other', false, { entry: 'script', script: 'fig.py' })
    expect(open).toHaveBeenCalledExactlyOnceWith({ script: 'fig.py', reuse_last_success: true }, { run: true })
  })

  it('项目外的 .py，按钮点了项目却打不开：一句话 + 发送问题反馈', async () => {
    useProjectStore.setState({ open: vi.fn(async () => Promise.reject(new Error('nope'))) } as never)
    await drop([named('fig.py')], [uri('/Users/me/other/fig.py')])
    await act(async () => {
      status().statusAction!.run()
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(open).not.toHaveBeenCalled()
    expect(formatMessage(status().statusAction!.label)).toBe('发送问题反馈')
  })

  it('浏览器里拖 .py（拿不到路径）：不再叫用户去脚本库，给发送问题反馈', async () => {
    await drop([named('plot.py')])
    expect(open).not.toHaveBeenCalled()
    expect(text()).toContain('plot.py')
    expect(text()).not.toContain('脚本库')
    expect(formatMessage(status().statusAction!.label)).toBe('发送问题反馈')
  })

  it('桌面壳但没给路径：说清并弹选择器，选中后运行', async () => {
    mocks.isDesktop.mockReturnValue(true)
    mocks.pickScriptFile.mockResolvedValue('/Users/me/paper/plot.py')
    await drop([named('plot.py')])
    expect(mocks.pickScriptFile).toHaveBeenCalled()
    expect(open).toHaveBeenCalledExactlyOnceWith({ script: 'plot.py', reuse_last_success: true }, { run: true, placeIn: expect.anything() })
    mocks.isDesktop.mockReturnValue(false)
  })

  it('一次拖了几个 .py：运行第一个，说清其余没运行', async () => {
    await drop([named('a.py'), named('b.py'), named('c.py')], [uri('/Users/me/paper/a.py'), uri('/Users/me/paper/b.py'), uri('/Users/me/paper/c.py')])
    expect(open).toHaveBeenCalledOnce()
    expect(open).toHaveBeenCalledWith({ script: 'a.py', reuse_last_success: true }, expect.anything())
    expect(text()).toContain('a.py')
    expect(text()).toContain('2')
  })

  it('图 + 脚本 + 不认识的：图放上、脚本跑起来，没成的那句排在最后（有最后发言权）', async () => {
    await drop([png('a.png'), named('plot.py'), named('data.csv')], [uri('/Users/me/paper/plot.py')])
    expect(useDocumentStore.getState().doc.objects).toHaveLength(1)
    expect(open).toHaveBeenCalledOnce()
    expect(text()).toContain('data.csv')
    expect(status().statusAction).not.toBeNull()
  })

  it('R 脚本在项目里：出「生成可编辑版本」那张卡，不运行', async () => {
    await drop([named('plot.R')], [uri('/Users/me/paper/plot.R')])
    expect(open).not.toHaveBeenCalled()
    expect(useForeignScriptStore.getState().offer).toEqual({ script: 'plot.R', language: 'r' })
  })

  it('R 脚本在项目外：给「打开它所在的项目」，点了打开并出那张卡', async () => {
    const openProject = vi.fn(async () => ({}) as never)
    useProjectStore.setState({ open: openProject } as never)
    await drop([named('plot.m')], [uri('/Users/me/other/plot.m')])
    expect(formatMessage(status().statusAction!.label)).toBe('打开它所在的项目')
    await act(async () => {
      status().statusAction!.run()
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(openProject).toHaveBeenCalledWith('/Users/me/other', false, { entry: 'script', script: 'plot.m' })
    expect(useForeignScriptStore.getState().offer).toEqual({ script: 'plot.m', language: 'matlab' })
  })

  it('正在切项目：整次放下不收', async () => {
    useProjectStore.setState({ switching: true })
    await drop([png('a.png')])
    expect(mocks.importAsset).not.toHaveBeenCalled()
  })

  it('原生事件里 R 脚本的 `script` 已是绝对路径：项目根下的走转换提议，不是「不在项目里」', async () => {
    mocks.nativeAvailable = true
    await drop([named('plot.R')], [], { kind: 'foreign_script', folder: '/Users/me/paper', script: '/Users/me/paper/plot.R', name: 'plot.R', ignored: 0 })
    expect(useForeignScriptStore.getState().offer).toEqual({ script: 'plot.R', language: 'r' })
  })

  it('原生事件里项目外的 MATLAB 脚本：按钮打开的是它真正的父目录', async () => {
    mocks.nativeAvailable = true
    const openProject = vi.fn(async () => ({}) as never)
    useProjectStore.setState({ open: openProject } as never)
    await drop([named('plot.m')], [], { kind: 'foreign_script', folder: '/Users/me/other', script: '/Users/me/other/plot.m', name: 'plot.m', ignored: 0 })
    await act(async () => {
      status().statusAction!.run()
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(openProject).toHaveBeenCalledExactlyOnceWith('/Users/me/other', false, { entry: 'script', script: 'plot.m' })
  })

  it('原生事件里的 .py 路径同样直接用 `script`（项目根下的 .py 照常运行）', async () => {
    mocks.nativeAvailable = true
    await drop([named('plot.py')], [], { kind: 'script', folder: '/Users/me/paper', script: '/Users/me/paper/plot.py', name: 'plot.py', ignored: 0 })
    expect(open).toHaveBeenCalledWith({ script: 'plot.py', reuse_last_success: true }, expect.anything())
  })

  it('图片上传一直挂着：脚本仍按原生路径运行，不等图片', async () => {
    mocks.nativeAvailable = true
    mocks.importAsset.mockImplementation(() => new Promise(() => {}))
    await drop([png('a.png'), named('plot.py')], [], { kind: 'script', folder: '/Users/me/paper', script: '/Users/me/paper/plot.py', name: 'plot.py', ignored: 0 })
    expect(open).toHaveBeenCalledOnce()
  })
})

/* ------------------------------------------------------------------------------------------------
 * Jupyter 笔记本拖进画布（ADR 0179）。判据的主语：发给后端的是哪条（路径抽取 / 内容上传）、发给准备会话 store 的 `open`
 * 入参（抽出来的 .py、带不带落点）、留给用户的那一句话与主按钮。原笔记本不动由后端的用例钉，这里只钉前端的出口。
 * ---------------------------------------------------------------------------------------------- */
describe('笔记本拖进画布', () => {
  const converted = (script: string, notebook = script.replace(/\.py$/, '.ipynb')) => ({ notebook, script, status: 'created' as const })

  beforeEach(() => {
    setCurrentProjectId('pj_a')
    const base = canvasToDoc(emptyProject().canvases[0])
    useDocumentStore.setState({ doc: { ...base, objects: [] }, past: [], future: [], txn: null })
    useDiagSendStore.setState({ capability: { enabled: true } as never, open: false })
    mocks.nativeAvailable = false
    mocks.isDesktop.mockReturnValue(false)
    mocks.extractNotebook.mockImplementation(async (nb: string) => converted(nb.replace(/\.ipynb$/, '.py'), nb))
    mocks.importNotebook.mockImplementation(async (_f: Blob, name: string) => converted(name.replace(/\.ipynb$/, '.py'), name))
    vi.spyOn(useScriptLibraryStore.getState(), 'load').mockResolvedValue(undefined)
  })

  it('项目里的笔记本（拿到路径）：后端抽成同名 .py，「打开并运行」它、图放进这张画布，说一句', async () => {
    await drop([named('fig.ipynb')], [uri('/Users/me/paper/sub/fig.ipynb')])
    expect(mocks.extractNotebook).toHaveBeenCalledExactlyOnceWith('sub/fig.ipynb', 'pj_a')
    expect(mocks.importNotebook).not.toHaveBeenCalled()
    expect(open).toHaveBeenCalledExactlyOnceWith(
      { script: 'sub/fig.py', reuse_last_success: true },
      { run: true, placeIn: expect.objectContaining({ at: { x: 50, y: 40 } }) },
    )
    expect(text()).toContain('fig.ipynb')
    expect(text()).toContain('fig.py')
    expect(status().statusTone).toBe('done')
    expect(status().statusAction).toBeNull()
  })

  it('壳交来的原生事件：项目根下的笔记本同样走路径抽取', async () => {
    mocks.nativeAvailable = true
    await drop([named('fig.ipynb')], [], { kind: 'notebook', folder: '/Users/me/paper', notebook: '/Users/me/paper/fig.ipynb', name: 'fig.ipynb', ignored: 0 })
    expect(mocks.extractNotebook).toHaveBeenCalledExactlyOnceWith('fig.ipynb', 'pj_a')
    expect(open).toHaveBeenCalledWith({ script: 'fig.py', reuse_last_success: true }, expect.anything())
  })

  it('项目外的笔记本：一句话 + 主按钮「打开它所在的项目」，点了打开项目、再抽取并运行；一个字节都没有先写', async () => {
    const openProject = vi.fn(async () => ({}) as never)
    useProjectStore.setState({ open: openProject } as never)
    await drop([named('fig.ipynb')], [uri('/Users/me/other/fig.ipynb')])
    expect(mocks.extractNotebook).not.toHaveBeenCalled()
    expect(open).not.toHaveBeenCalled()
    expect(text()).toContain('fig.ipynb')
    expect(formatMessage(status().statusAction!.label)).toBe('打开它所在的项目')
    await act(async () => {
      status().statusAction!.run()
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(openProject).toHaveBeenCalledExactlyOnceWith('/Users/me/other', false, { entry: 'script', script: 'fig.ipynb' })
    expect(mocks.extractNotebook).toHaveBeenCalledExactlyOnceWith('fig.ipynb', 'pj_a')
    expect(open).toHaveBeenCalledExactlyOnceWith({ script: 'fig.py', reuse_last_success: true }, { run: true })
  })

  it('项目外的笔记本，按钮点了项目却打不开：一句话 + 发送问题反馈', async () => {
    useProjectStore.setState({ open: vi.fn(async () => Promise.reject(new Error('nope'))) } as never)
    await drop([named('fig.ipynb')], [uri('/Users/me/other/fig.ipynb')])
    await act(async () => {
      status().statusAction!.run()
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(mocks.extractNotebook).not.toHaveBeenCalled()
    expect(formatMessage(status().statusAction!.label)).toBe('发送问题反馈')
  })

  it('浏览器里拿不到路径：读到的内容交给后端抽成当前项目里的 .py，不弹选择器、不再说放不进画布', async () => {
    await drop([named('fig.ipynb')])
    expect(mocks.extractNotebook).not.toHaveBeenCalled()
    expect(mocks.importNotebook).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ name: 'fig.ipynb' }), 'fig.ipynb', 'pj_a')
    expect(mocks.pickScriptFile).not.toHaveBeenCalled()
    expect(open).toHaveBeenCalledWith({ script: 'fig.py', reuse_last_success: true }, { run: true, placeIn: expect.anything() })
    expect(text()).not.toContain('放不进画布')
  })

  it('桌面壳但这次没给路径：同样用内容上传（不弹选择器）', async () => {
    mocks.isDesktop.mockReturnValue(true)
    await drop([named('fig.ipynb')])
    expect(mocks.importNotebook).toHaveBeenCalledOnce()
    expect(mocks.pickScriptFile).not.toHaveBeenCalled()
  })

  it('抽取失败（版本太旧 / 没有代码 / 看不懂）：原因进那一句话 + 发送问题反馈，不运行', async () => {
    mocks.extractNotebook.mockRejectedValue(new ApiError('x', 422, { code: 'notebook_no_code', params: {} }))
    await drop([named('fig.ipynb')], [uri('/Users/me/paper/fig.ipynb')])
    expect(open).not.toHaveBeenCalled()
    expect(text()).toContain('fig.ipynb')
    expect(text()).toContain('没有可以运行的代码')
    expect(status().statusTone).toBe('error')
    expect(formatMessage(status().statusAction!.label)).toBe('发送问题反馈')
  })

  it('写不进去（只读 / 磁盘满）同样是一句话 + 发送问题反馈', async () => {
    mocks.extractNotebook.mockRejectedValue(new ApiError('x', 403, { code: 'asset_import_not_writable', params: {} }))
    await drop([named('fig.ipynb')], [uri('/Users/me/paper/fig.ipynb')])
    expect(open).not.toHaveBeenCalled()
    expect(formatMessage(status().statusAction!.label)).toBe('发送问题反馈')
  })

  it('没有项目（playground / MCP 画布）：不发请求，说清这里没有项目文件夹，不画按钮', async () => {
    setCurrentProjectId(null)
    useDiagSendStore.setState({ capability: null, open: false })
    await drop([named('fig.ipynb')])
    expect(mocks.importNotebook).not.toHaveBeenCalled()
    expect(mocks.extractNotebook).not.toHaveBeenCalled()
    expect(text()).toContain('fig.ipynb')
    expect(text()).toContain('没有项目文件夹')
    expect(status().statusAction).toBeNull()
  })

  it('一次拖了 .py 和笔记本：.py 赢，笔记本这次不处理并说一声', async () => {
    await drop([named('fig.ipynb'), named('plot.py')], [uri('/Users/me/paper/plot.py')])
    expect(mocks.extractNotebook).not.toHaveBeenCalled()
    expect(open).toHaveBeenCalledExactlyOnceWith({ script: 'plot.py', reuse_last_success: true }, expect.anything())
    expect(text()).toContain('plot.py')
    expect(text()).toContain('1')
  })

  it('一次拖了几个笔记本：处理第一个，说清其余没处理', async () => {
    await drop([named('a.ipynb'), named('b.ipynb')], [uri('/Users/me/paper/a.ipynb'), uri('/Users/me/paper/b.ipynb')])
    expect(mocks.extractNotebook).toHaveBeenCalledExactlyOnceWith('a.ipynb', 'pj_a')
    expect(text()).toContain('a.py')
    expect(text()).toContain('1')
  })

  it('抽取在途时换了项目：什么都不运行、不说（A 项目的响应绝不落进 B）', async () => {
    mocks.extractNotebook.mockImplementation(async (nb: string) => {
      setCurrentProjectId('pj_b')
      return converted(nb.replace(/\.ipynb$/, '.py'), nb)
    })
    await drop([named('fig.ipynb')], [uri('/Users/me/paper/fig.ipynb')])
    expect(open).not.toHaveBeenCalled()
    expect(status().status).toBeNull()
  })

  it('笔记本 + 不认识的：笔记本那句先说、没成的那句排在最后（有最后发言权）', async () => {
    await drop([named('fig.ipynb'), named('data.csv')], [uri('/Users/me/paper/fig.ipynb')])
    expect(open).toHaveBeenCalledOnce()
    expect(text()).toContain('data.csv')
    expect(status().statusAction).not.toBeNull()
  })
})
