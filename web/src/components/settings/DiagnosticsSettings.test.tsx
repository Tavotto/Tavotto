/**
 * 设置 → 诊断（ADR 0038）。
 *
 * ① 只显示健康状态、失败原因与两个动作；② Agent 页已有的 CLI 检查项不重复；
 * ③ 渲染环境卡只出现一次（技术详情里），且内置包清单不在这里；
 * ④ 「复制诊断」先预览脱敏后的文本再复制，文本来自后端同一份采集。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchDiagnosticsSummary: vi.fn(),
}))

import { fetchDiagnosticsSummary } from '@/lib/api'
import { t } from '@/i18n'
import { DiagnosticsSettings } from '@/components/settings/DiagnosticsSettings'
import { useEnvStore } from '@/store/envStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
Element.prototype.hasPointerCapture ??= () => false
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as never

const summaryMock = vi.mocked(fetchDiagnosticsSummary)
const st = (key: string, v?: Record<string, unknown>) =>
  t(`settings.${key}`, { ns: 'dialogs', ...(v ?? {}) })

const PYTHON_PATH = '/opt/homebrew/opt/python@3.13/libexec/bin/python3'
const CHECKS = [
  { id: 'worker_python', ok: true, label: '渲染引擎 Python', detail: `${PYTHON_PATH}（系统 Python）` },
  { id: 'matplotlib', ok: true, label: 'matplotlib', detail: '3.10.8' },
  { id: 'cli_codex', ok: true, label: 'Codex CLI', detail: 'codex-cli 1.2.3' },
  { id: 'cli_claude', ok: false, label: 'Claude CLI', detail: '未安装' },
  { id: 'project_writable', ok: false, label: '项目目录可写', detail: '/tmp/figs' },
]

let host: HTMLDivElement
let root: Root

async function mount(checks = CHECKS) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ json: () => Promise.resolve({ checks }), ok: true } as Response)),
  )
  useEnvStore.setState({
    env: {
      ok: true,
      python: PYTHON_PATH,
      source: 'system',
      matplotlib: '3.10.8',
      managed: false,
      bundled: true,
      runtime: { packages: { numpy: '2.1.0', matplotlib: '3.10.8' } } as never,
      state: 'idle',
    } as never,
  })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<DiagnosticsSettings />)
  })
  await act(async () => {})
}

const text = () => document.body.textContent ?? ''
const buttons = () => [...document.querySelectorAll('button')] as HTMLButtonElement[]
const byName = (name: string) =>
  buttons().find((b) => (b.getAttribute('aria-label') ?? b.textContent ?? '').trim() === name)

beforeEach(() => {
  summaryMock.mockReset()
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('首屏', () => {
  it('健康状态：异常数在前、异常项说原因、正常项只有名字', async () => {
    await mount()
    expect(text()).toContain(st('diagnostics.summaryFailing', { count: 1 }))
    expect(text()).toContain(st('about.check.project_writable'))
    expect(text()).toContain('/tmp/figs') // 坏的说原因
    expect(text()).toContain(st('about.check.matplotlib'))
    expect(text()).not.toContain(PYTHON_PATH) // 好的不摆路径
  })

  it('Agent 页已有的 CLI 检查项不在这里重复', async () => {
    await mount()
    expect(text()).not.toContain('Codex CLI')
    expect(text()).not.toContain('codex-cli 1.2.3')
    expect(text()).not.toContain(st('about.check.cli_claude'))
    // 上面 cli_claude 是坏的，但过滤在前：异常数是 1 不是 2
    expect(text()).toContain(st('diagnostics.summaryFailing', { count: 1 }))
  })

  it('全部正常时一句话', async () => {
    await mount(CHECKS.filter((c) => c.ok))
    expect(text()).toContain(st('diagnostics.summaryOk'))
  })

  it('渲染环境卡只在技术详情里、只有一张；内置包版本清单不在这一页', async () => {
    await mount()
    const okTitle = t('engine.okTitle', { ns: 'errors' })
    expect(text().split(okTitle).length - 1).toBe(0)
    await act(async () => byName(st('techDetails'))!.click())
    expect(text().split(okTitle).length - 1).toBe(1)
    expect(text()).toContain(PYTHON_PATH)
    expect(text()).not.toContain('2.1.0') // numpy 版本归包管理页
  })
})

describe('复制诊断', () => {
  it('先预览再复制：文本来自后端摘要，预览里有脱敏提示', async () => {
    summaryMock.mockResolvedValue({ text: 'tavotto.version: 0.12.0\npaths.data_dir: ~/Library/…\n', report: {} })
    const write = vi.fn(() => Promise.resolve())
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: write } })
    await mount()
    expect(document.querySelector('[data-diagnostics-preview]')).toBeNull()
    await act(async () => byName(st('diagnostics.copyReport'))!.click())
    await act(async () => {})
    expect(summaryMock).toHaveBeenCalled()
    const preview = document.querySelector('[data-diagnostics-preview]')!
    expect(preview.textContent).toContain(st('diagnostics.previewNote'))
    expect(preview.textContent).toContain('tavotto.version: 0.12.0')
    expect(write).not.toHaveBeenCalled() // 预览阶段一个字节都没进剪贴板
    await act(async () => byName(st('diagnostics.copyReport'))!.click())
    expect(write).toHaveBeenCalledWith('tavotto.version: 0.12.0\npaths.data_dir: ~/Library/…\n')
  })

  it('摘要拿不到时说清失败，不给一个空剪贴板', async () => {
    summaryMock.mockRejectedValue(new Error('500'))
    await mount()
    await act(async () => byName(st('diagnostics.copyReport'))!.click())
    await act(async () => {})
    expect(text()).toContain(st('diagnostics.prepareFailed'))
    expect(document.querySelector('[data-diagnostics-preview]')).toBeNull()
  })

  it('导出诊断包的按钮还在', async () => {
    await mount()
    expect(byName(st('about.exportBundle'))).toBeTruthy()
  })
})
