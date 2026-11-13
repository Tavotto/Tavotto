import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { t } from '@/i18n'
import { type BrowseResult, type ProjectPackageInspection, type ProjectPackageInstallation } from '@/lib/api'
import { isDesktop, pickDirectory } from '@/lib/desktop'
import { setCurrentProjectId } from '@/lib/session'
import { useProjectPackageInstallStore } from '@/store/projectPackageInstallStore'
import { projectFileEditor } from '@/lib/projectFileSession'
import type { WorkingDocumentSaveReceipt } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'
import { ProjectPackageInstallDialog } from './ProjectPackageInstallDialog'
import { TooltipProvider } from './ui/Tooltip'

vi.mock('@/lib/desktop', async importOriginal => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  isDesktop: vi.fn(() => false),
  pickDirectory: vi.fn(),
}))

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const flow = () => useProjectPackageInstallStore.getState()
const originalOpen = flow().openInstalled
const originalCancel = flow().cancel
const originalContinue = flow().continueEditing
const dt = (key: string, values?: Record<string, unknown>) => t(`packageInstall.${key}`, { ns: 'dialogs', ...values })
const archive = (name = 'A.zip') => new File(['opaque archive, parsed by the controlled backend'], name)
const document = (name = 'scientific project') => {
  const value = emptyProject(); value.schema = 4; value.project.name = name
  return value
}
const savedReceipt = (projectId: string): WorkingDocumentSaveReceipt => {
  const address = { projectId, documentId: 'saved-working-document', format: 'v4' as const }
  return { address, document: document(), revision: 'a'.repeat(32), loadSeq: 1, capture: projectFileEditor.capture(address) }
}
const inspection = (name = 'scientific project'): ProjectPackageInspection => ({
  document_format: 'v4', doc: document(name), manifest: {}, resources: [{ target: 'data.csv', record: {} }],
  missing: [], warnings: [], figure_snapshots: 2, execution_coverage: null,
})
const installation = (path = '/new/中文项目'): ProjectPackageInstallation => ({
  state: 'resources_installed', path, document_format: 'v4', doc: document(), references: [], execution: null,
  remap_fingerprint: 'actual-backend-fingerprint', missing: [], warnings: [], execution_coverage: null,
  configuration: 'ready', configuration_review: { version: 1, existing_local_metadata: false, workdir: null, defaults: [], issues: [] },
  document_saved: false,
})
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  return { promise: new Promise<T>((yes, no) => { resolve = yes; reject = no }), resolve, reject }
}

let root: Root
let host: HTMLDivElement
let inspected: ProjectPackageInspection
let installed: ProjectPackageInstallation
let installStatus: number
let installWait: Promise<void> | undefined
let installError: unknown
let requests: { path: string; method: string; pj: string | null; body: RequestInit['body'] }[]
let openAction: ReturnType<typeof vi.fn<() => Promise<boolean>>>
let continueAction: ReturnType<typeof vi.fn<() => Promise<boolean>>>
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
function node<T extends Element>(selector: string): T {
  const nodes = window.document.querySelectorAll<T>(selector)
  expect(nodes.length, selector).toBe(1)
  return nodes.item(0)
}
const dialog = () => node<HTMLElement>('[data-dialog="project-package-install"]')
const control = (name: string) => node<HTMLButtonElement>(`[data-project-package-${name}]`)
const destination = () => node<HTMLInputElement>('[data-project-package-destination]')
const installRequests = () => requests.filter(row => row.path.endsWith('/package/install'))
const forbiddenRequests = () => requests.filter(row => !row.path.endsWith('/package/inspect')
  && !row.path.endsWith('/package/install') && row.path !== '/api/projects/browse')
async function click(element: HTMLElement) { await act(async () => { element.click() }); await settle() }
async function type(element: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function inspect(file = archive()) {
  await act(async () => { expect(await flow().inspect(file)).toBe(true) })
}
async function publish() {
  await inspect()
  await type(destination(), '/new/中文项目')
  await click(control('install'))
  expect(flow().phase).toBe('published')
}

beforeEach(async () => {
  requests = []; inspected = inspection(); installed = installation(); installStatus = 201; installWait = undefined; installError = null
  localStorage.clear(); setCurrentProjectId('original-project')
  flow().reset()
  openAction = vi.fn(async () => false)
  continueAction = vi.fn(async () => false)
  useProjectPackageInstallStore.setState({ openInstalled: openAction, continueEditing: continueAction, cancel: originalCancel })
  useUiStore.getState().setSettingsOpen(false)
  vi.mocked(isDesktop).mockReturnValue(false); vi.mocked(pickDirectory).mockReset()
  const browse: BrowseResult = { path: '/parent', parent: '/', dirs: [], roots: [], shortcuts: [], is_roots: false, writable: true }
  vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = new URL(String(input), 'http://test')
    requests.push({ path: url.pathname, method: init?.method ?? 'GET', pj: url.searchParams.get('pj'), body: init?.body })
    if (url.pathname.endsWith('/package/inspect')) return Response.json(inspected)
    if (url.pathname.endsWith('/package/install')) {
      await installWait
      if (installError) throw installError
      return Response.json(installed, { status: installStatus })
    }
    if (url.pathname === '/api/projects/browse') return Response.json(browse)
    throw new Error(`Unexpected API call: ${url.pathname}`)
  }))
  host = window.document.createElement('div'); window.document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => { root.render(<TooltipProvider><ProjectPackageInstallDialog /></TooltipProvider>) })
})
afterEach(async () => {
  await act(async () => { root.unmount(); flow().reset() })
  host.remove()
  useProjectPackageInstallStore.setState({ openInstalled: originalOpen, continueEditing: originalContinue, cancel: originalCancel })
  useUiStore.getState().setSettingsOpen(false)
  setCurrentProjectId(null); vi.restoreAllMocks(); vi.unstubAllGlobals()
})

describe('项目包安装对话框（真实store及HTTP包装，打开动作单独监测）', () => {
  it('取消后丢响应仍可回看requested路径，未收到receipt不显示已安装/已保存或授打开', async () => {
    await inspect()
    const held = deferred<void>(); installWait = held.promise
    await type(destination(), installed.path)
    await click(control('install'))
    await click(control('close'))
    installError = new TypeError('The installation response was lost')
    await act(async () => { held.resolve(); await held.promise }); await settle()
    expect(flow().receipts).toEqual([])
    await act(async () => { flow().reopen() })
    expect(flow().phase).toBe('unconfirmed')
    expect(node('[data-project-package-unconfirmed]').textContent).toContain(installed.path)
    expect(dialog().textContent).toContain(dt('completionUnknown'))
    expect(dialog().textContent).not.toContain(dt('saved'))
    expect(dialog().textContent).not.toContain(dt('installedAt'))
    expect(window.document.querySelector('[data-project-package-open]')).toBeNull()
    expect(window.document.querySelector('[data-project-package-install]')).toBeNull()
    expect(installRequests()).toHaveLength(1)
    expect(openAction).not.toHaveBeenCalled()
  })

  it('重开在途路径后合法receipt迟到仅提供明确查看，不自动采用或重复安装', async () => {
    await inspect()
    const held = deferred<void>(); installWait = held.promise
    await type(destination(), installed.path)
    await click(control('install')); await click(control('close'))
    await act(async () => { flow().reopen() })
    expect(flow().phase).toBe('unconfirmed')
    await act(async () => { held.resolve(); await held.promise }); await settle()
    expect(flow().receipts).toHaveLength(1)
    expect(flow().phase).toBe('unconfirmed')
    expect(dialog().textContent).toContain(dt('receivedResult'))
    expect(openAction).not.toHaveBeenCalled()
    await click(control('view-result'))
    expect(flow().phase).toBe('published')
    expect(control('open')).toBeTruthy()
    expect(installRequests()).toHaveLength(1)
    expect(openAction).not.toHaveBeenCalled()
  })

  it('历史已保存回执的继续动作使用原项目恢复，不误当初次采用或直接关窗', async () => {
    await publish()
    const cancel = vi.spyOn(flow(), 'cancel')
    await act(async () => {
      useProjectPackageInstallStore.setState({ phase: 'saved', saved: savedReceipt('another-project') })
    })
    await click(control('continue'))
    expect(continueAction).toHaveBeenCalledTimes(1)
    expect(openAction).not.toHaveBeenCalled()
    expect(cancel).not.toHaveBeenCalled()
    expect(installRequests()).toHaveLength(1)
  })

  it('配置检查只在已保存项目当前认领后指向原project设置，切项目即撤动作', async () => {
    installed.configuration = 'needs_review'
    await publish()
    await act(async () => {
      useProjectPackageInstallStore.setState({ phase: 'saved', saved: savedReceipt('installed-project') })
    })
    expect(window.document.querySelector('[data-project-package-review-configuration]')).toBeNull()
    await act(async () => { setCurrentProjectId('installed-project') })
    expect(control('review-configuration')).toBeTruthy()
    await act(async () => { setCurrentProjectId('other-project') })
    expect(window.document.querySelector('[data-project-package-review-configuration]')).toBeNull()
    expect(useUiStore.getState().settingsOpen).toBe(false)
    await act(async () => { setCurrentProjectId('installed-project') })
    await click(control('review-configuration'))
    expect(useUiStore.getState().settingsOpen).toBe(true)
    expect(useUiStore.getState().settingsSection).toBe('project')
    expect(flow().phase).toBe('idle')
    expect(forbiddenRequests()).toEqual([])
    expect(openAction).not.toHaveBeenCalled()
  })

  it('检视只展示原包事实，输入新目录后仅发送原install options，不提前采用或保存正文', async () => {
    const file = archive()
    await inspect(file)
    expect(dialog().textContent).toContain(inspected.doc.project.name)
    expect(dialog().textContent).toContain(dt('contents', { canvases: 1, resources: 1, snapshots: 2 }))
    expect(control('install').disabled).toBe(true)
    expect(installRequests()).toHaveLength(0)
    await type(destination(), '  /new/中文项目  ')
    await click(control('install'))
    expect(installRequests()).toHaveLength(1)
    const request = installRequests()[0], form = request.body as FormData
    expect(form.get('package')).toBe(file)
    expect(form.get('options')).toBe(JSON.stringify({ destination: '/new/中文项目' }))
    expect(request.pj).toBe('original-project')
    expect(flow().installation?.document_saved).toBe(false)
    expect(dialog().textContent).toContain(dt('documentPending'))
    expect(openAction).not.toHaveBeenCalled()
    expect(forbiddenRequests()).toEqual([])
    await click(control('open'))
    expect(openAction).toHaveBeenCalledTimes(1)
  })

  it.each(['publication_unconfirmed', 'installation_unconfirmed'] as const)('真实503的%s保留已发布路径及配置待复核，不宣称正文已保存', async state => {
    installed.state = state; installed.configuration = 'needs_review'; installStatus = 503
    await publish()
    expect(dialog().textContent).toContain(installed.path)
    expect(dialog().textContent).toContain(dt('publicationUnconfirmed'))
    expect(dialog().textContent).toContain(dt('configurationReview'))
    expect(dialog().textContent).toContain(dt('documentPending'))
    expect(dialog().textContent).not.toContain(dt('saved'))
    expect(window.document.querySelector('[data-project-package-install]')).toBeNull()
    expect(openAction).not.toHaveBeenCalled()
    await click(control('open'))
    expect(openAction).toHaveBeenCalledTimes(1)
    expect(installRequests()).toHaveLength(1)
  })

  it('安装在途仍可关闭；迟到发布仅记receipt，重新打开不重新安装', async () => {
    await inspect()
    const held = deferred<void>(); installWait = held.promise
    await type(destination(), installed.path)
    const cancel = vi.spyOn(flow(), 'cancel')
    await click(control('install'))
    expect(flow().phase).toBe('installing')
    expect(control('close').disabled).toBe(false)
    await click(control('close'))
    expect(cancel).toHaveBeenCalledTimes(1)
    expect(flow().phase).toBe('idle')
    await act(async () => { held.resolve(); await held.promise }); await settle()
    expect(flow().phase).toBe('idle')
    expect(window.document.querySelector('[data-dialog="project-package-install"]')).toBeNull()
    expect(flow().receipts).toHaveLength(1)
    await act(async () => { flow().reopen() })
    expect(dialog().textContent).toContain(installed.path)
    expect(dialog().textContent).toContain(dt('documentPending'))
    expect(installRequests()).toHaveLength(1)
    expect(openAction).not.toHaveBeenCalled()
  })

  it('明确打开动作在途仍能关闭并调用cancel，不用加载态锁住原回执', async () => {
    await publish()
    const held = deferred<boolean>()
    openAction.mockImplementation(() => {
      useProjectPackageInstallStore.setState({ phase: 'opening' })
      return held.promise
    })
    const cancel = vi.spyOn(flow(), 'cancel')
    await click(control('open'))
    expect(flow().phase).toBe('opening')
    expect(control('close').disabled).toBe(false)
    await click(control('close'))
    expect(cancel).toHaveBeenCalledTimes(1)
    expect(flow().phase).toBe('idle')
    expect(flow().receipts).toHaveLength(1)
    await act(async () => { held.resolve(false); await held.promise })
  })

  it('历史行按attempt恢复对应路径，不误取另一份回执且不触发打开', async () => {
    await publish()
    const first = flow().receipts[0]
    installed = installation('/new/第二项目'); await inspect(archive('B.zip'))
    await type(destination(), installed.path); await click(control('install'))
    await inspect(archive('C.zip'))
    expect(flow().receipts).toHaveLength(2)
    await click(node<HTMLButtonElement>(`[data-project-package-reopen="${first.attemptId}"]`))
    expect(flow().installation?.path).toBe(first.installation.path)
    expect(flow().phase).toBe('published')
    expect(openAction).not.toHaveBeenCalled()
    expect(installRequests()).toHaveLength(2)
  })

  it('浏览器DirBrowser只生成新目录路径，确认选择不创建目录或打开项目', async () => {
    await inspect()
    await click(control('choose-destination'))
    expect(node('[data-dialog="dir-browser"]')).toBeTruthy()
    await type(node<HTMLInputElement>('[data-dir-browser-name]'), '中文新项目')
    await click(node<HTMLButtonElement>('[data-dir-browser-confirm]'))
    expect(destination().value).toBe('/parent/中文新项目')
    expect(window.document.querySelector('[data-dialog="dir-browser"]')).toBeNull()
    expect(installRequests()).toHaveLength(0)
    expect(forbiddenRequests()).toEqual([])
    expect(openAction).not.toHaveBeenCalled()
    expect(pickDirectory).not.toHaveBeenCalled()
  })

  it('桌面选择取消保留手工目录，零安装、零打开', async () => {
    vi.mocked(isDesktop).mockReturnValue(true); vi.mocked(pickDirectory).mockResolvedValue(null)
    await inspect(); await type(destination(), '/manual/new')
    await click(control('choose-destination'))
    expect(pickDirectory).toHaveBeenCalledTimes(1)
    expect(destination().value).toBe('/manual/new')
    expect(window.document.querySelector('[data-dialog="new-project-name"]')).toBeNull()
    expect(installRequests()).toHaveLength(0); expect(openAction).not.toHaveBeenCalled()
    expect(forbiddenRequests()).toEqual([])
  })

  it.each(['new inspection', 'dismissal'] as const)('桌面迟到成功选择不覆盖%s的新状态', async change => {
    vi.mocked(isDesktop).mockReturnValue(true)
    const held = deferred<string | null>(); vi.mocked(pickDirectory).mockReturnValue(held.promise)
    await inspect(); await click(control('choose-destination'))
    if (change === 'dismissal') await click(control('close'))
    inspected = inspection('B'); await inspect(archive('B.zip'))
    await type(destination(), '/B/own-draft')
    await act(async () => { held.resolve('/late/A'); await held.promise }); await settle()
    expect(destination().value).toBe('/B/own-draft')
    expect(window.document.querySelector('[data-dialog="new-project-name"]')).toBeNull()
    expect(dialog().textContent).toContain('B')
    expect(installRequests()).toHaveLength(0)
  })

  it('桌面迟到错误不能写进新inspection', async () => {
    vi.mocked(isDesktop).mockReturnValue(true)
    const held = deferred<string | null>(); vi.mocked(pickDirectory).mockReturnValue(held.promise)
    await inspect(); await click(control('choose-destination'))
    inspected = inspection('B'); await inspect(archive('B.zip'))
    await type(destination(), '/B/own-draft')
    await act(async () => { held.reject(new Error('late native chooser failure')); await held.promise.catch(() => {}) }); await settle()
    expect(destination().value).toBe('/B/own-draft')
    expect(dialog().textContent).not.toContain('late native chooser failure')
    expect(dialog().querySelector('[data-notice="danger"]')).toBeNull()
  })

  it('缺资源与覆盖不完整如实显示，只消费后端事实，不自动Run或修复', async () => {
    inspected.missing = ['missing/data.csv']
    inspected.execution_coverage = { state: 'incomplete', issues: [{ script: 'plot.py', reason: 'unknown_input_channels' }] }
    await inspect()
    expect(dialog().textContent).toContain(dt('missing', { count: 1 }))
    expect(dialog().textContent).toContain(dt('dependencies'))
    expect(dialog().querySelector('[data-notice="warn"]')).not.toBeNull()
    expect(installRequests()).toHaveLength(0); expect(forbiddenRequests()).toEqual([])
  })
})
