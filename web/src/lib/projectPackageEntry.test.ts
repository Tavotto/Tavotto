import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PACKAGE_EXT } from './brand'
import { chooseProjectPackage } from './projectPackageEntry'
import { useProjectPackageInstallStore } from '@/store/projectPackageInstallStore'
import { emptyProject } from '@/types/document'

const flow = () => useProjectPackageInstallStore.getState()
const actual = { inspect: flow().inspect, reopen: flow().reopen, install: flow().install, openInstalled: flow().openInstalled }
let pickers: HTMLInputElement[]
const picker = () => pickers.at(-1) ?? null
let inspect: ReturnType<typeof vi.fn<(file: File) => Promise<boolean>>>
let reopen: ReturnType<typeof vi.fn<(attemptId?: number) => void>>
const history = () => {
  const doc = emptyProject(); doc.schema = 4
  return { attemptId: 7, saved: null, installation: {
    state: 'resources_installed' as const, path: '/published/中文项目', document_format: 'v4' as const,
    doc, references: [], execution: null, remap_fingerprint: 'fp', missing: [], warnings: [], execution_coverage: null,
    configuration: 'ready' as const, configuration_review: {
      version: 1 as const, existing_local_metadata: false, workdir: null, defaults: [], issues: [],
    }, document_saved: false as const,
  } }
}

beforeEach(() => {
  flow().reset(); pickers = []
  inspect = vi.fn(async () => true); reopen = vi.fn()
  useProjectPackageInstallStore.setState({ inspect, reopen })
  vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(function (this: HTMLInputElement) { pickers.push(this) })
})
afterEach(() => { flow().reset(); useProjectPackageInstallStore.setState(actual); vi.restoreAllMocks() })

describe('显式项目包入口', () => {
  it('默认零历史时只选择包字节，文件交给检视，不提前安装或打开', () => {
    const install = vi.spyOn(flow(), 'install'), open = vi.spyOn(flow(), 'openInstalled')
    chooseProjectPackage()
    expect(picker()?.type).toBe('file')
    expect(picker()?.accept).toBe(PACKAGE_EXT + ',.zip,application/zip')
    const file = new File(['opaque archive'], '科学项目.zip')
    Object.defineProperty(picker(), 'files', { value: [file] })
    picker()!.dispatchEvent(new Event('change'))
    expect(inspect).toHaveBeenCalledExactlyOnceWith(file)
    expect(install).not.toHaveBeenCalled(); expect(open).not.toHaveBeenCalled()
  })

  it('文件选择取消不做任何检视', () => {
    chooseProjectPackage()
    picker()!.dispatchEvent(new Event('change'))
    expect(inspect).not.toHaveBeenCalled(); expect(reopen).not.toHaveBeenCalled()
  })

  it('已有真实receipt默认恢复窗口，不再次弹出文件选择器', () => {
    useProjectPackageInstallStore.setState({ receipts: [history()] })
    chooseProjectPackage()
    expect(reopen).toHaveBeenCalledExactlyOnceWith()
    expect(picker()).toBeNull(); expect(inspect).not.toHaveBeenCalled()
  })

  it.each(['installing', 'completion_unknown'] as const)('%s请求路径也恢复窗口，不误要求重新选包', state => {
    useProjectPackageInstallStore.setState({ attempts: [{ attemptId: 9, destination: '/requested/中文项目', state }] })
    chooseProjectPackage()
    expect(reopen).toHaveBeenCalledExactlyOnceWith()
    expect(picker()).toBeNull()
    expect(inspect).not.toHaveBeenCalled()
  })

  it('明确选择另一包不被历史劫持，仍只进入检视', () => {
    useProjectPackageInstallStore.setState({ receipts: [history()] })
    chooseProjectPackage(true)
    expect(picker()?.type).toBe('file'); expect(reopen).not.toHaveBeenCalled()
    const file = new File(['B'], 'B.zip')
    Object.defineProperty(picker(), 'files', { value: [file] })
    picker()!.dispatchEvent(new Event('change'))
    expect(inspect).toHaveBeenCalledExactlyOnceWith(file)
  })
})
