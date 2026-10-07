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
import { TooltipProvider } from '@/components/ui/Tooltip'

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

async function mount(checks = CHECKS, response?: Promise<Response>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => response ?? Promise.resolve({ json: () => Promise.resolve({ checks }), ok: true } as Response)),
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
    root.render(
      <TooltipProvider>
        <DiagnosticsSettings />
      </TooltipProvider>,
    )
  })
  await act(async () => {})
}

const text = () => document.body.textContent ?? ''
const buttons = () => [...document.querySelectorAll('button')] as HTMLButtonElement[]
const byName = (name: string) =>
  buttons().find(
    (b) =>
      (b.getAttribute('aria-label') ?? b.textContent ?? '').trim() === name ||
      // 折叠行的按钮里还有行尾的值（「3 项正常」）：认名字那一截
      (b.hasAttribute('aria-expanded') && b.querySelector('span')?.textContent?.trim() === name),
  )

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
  /**
   * 顺序是「健康 → 报告 → 开发者」（2026-10-07 设计审计 §9.1 P0），同时守住 #797「异步结果不挪动正在按的入口」：
   * 健康组只有一行结论，取数中与取数后是**同一行**（同一个元素，只换现状那句话、控件列多一枚胶囊）；会随结果
   * 长高的东西（异常项、各项检查结果、恢复入口）全在开发者组**之后**的「检查结果」组里。
   */
  it('健康结论在最前、且取数前后是同一行；随结果长高的都在开发者入口之后', async () => {
    let resolve!: (value: Response) => void
    const pending = new Promise<Response>((done) => { resolve = done })
    await mount(CHECKS, pending)
    const page = document.querySelector('[data-diagnostics-page]')!
    const dev = page.querySelector('[data-diagnostics-dev]')!
    const toggle = dev.querySelector('button')!
    const health = page.querySelector('[data-diagnostics-health]')!
    const report = page.querySelector('[data-settings-anchor="diagnostics.report"]')!
    const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING
    // 顺序：健康 → 报告 → 开发者
    expect(health.compareDocumentPosition(report) & FOLLOWING).toBeTruthy()
    expect(report.compareDocumentPosition(dev) & FOLLOWING).toBeTruthy()
    const loading = page.querySelector('[data-diagnostics-loading]')!
    expect(loading.textContent).toBe(st('about.detecting'))
    const verdictRow = loading.closest('[data-setting-row]')!
    expect(health.contains(verdictRow)).toBe(true)
    await act(async () => { toggle.click() })
    const start = page.querySelector('[data-perf-probe-start]')!
    expect(start).not.toBeNull()
    await act(async () => {
      resolve({ json: () => Promise.resolve({ checks: CHECKS }), ok: true } as Response)
    })
    expect(page.querySelector('[data-diagnostics-loading]')).toBeNull()
    const summary = page.querySelector('[data-diagnostics-summary]')!
    expect(summary.textContent).toContain(st('diagnostics.summaryFailing', { count: 1 }))
    // 结论在原来那一行里落地（同一个元素），健康组里还是只有这一行
    expect(summary.closest('[data-setting-row]')).toBe(verdictRow)
    expect(health.querySelectorAll('[data-setting-row]')).toHaveLength(1)
    const failures = page.querySelector('[data-diagnostics-failures]')!
    expect(failures.textContent).toContain(st('about.check.project_writable'))
    expect(dev.compareDocumentPosition(failures) & FOLLOWING).toBeTruthy()
    await act(async () => {
      useEnvStore.setState({ env: { ...useEnvStore.getState().env!, ok: false } })
    })
    const card = page.querySelector('[data-engine-env-card]')!
    expect(card).not.toBeNull()
    expect(dev.compareDocumentPosition(card) & FOLLOWING).toBeTruthy()
    expect(page.querySelector('[data-diagnostics-dev] > div > button')).toBe(toggle)
    expect(page.querySelector('[data-perf-probe-start]')).toBe(start)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(card.closest('[data-diagnostics-dev]')).toBeNull() // 错误与恢复入口不藏进技术详情
  })

  it('结论是一枚带字的状态胶囊，不是行标签（2026-10-07 设计审计 §9.1）', async () => {
    await mount()
    const summary = document.querySelector('[data-diagnostics-summary]')!
    expect(summary.getAttribute('data-status-pill')).toBe('danger')
    await act(async () => root.unmount())
    host.remove()
    await mount(CHECKS.filter((c) => c.ok))
    expect(document.querySelector('[data-diagnostics-summary]')!.getAttribute('data-status-pill')).toBe('ok')
  })

  it('异常项在首屏并说原因；正常项默认折叠（审计 T47）', async () => {
    await mount()
    expect(text()).toContain(st('diagnostics.summaryFailing', { count: 1 }))
    expect(text()).toContain(st('about.check.project_writable'))
    // 目录走 `PathValue`：默认只给末级目录，全路径展开可见（与项目设置同一份实现）
    expect(text()).toContain('figs')
    expect(text()).not.toContain('/tmp/figs')
    await act(async () =>
      document.body
        .querySelector<HTMLElement>(
          `[aria-label="${st('project.showFullPath', { name: st('about.check.project_writable') })}"]`,
        )!
        .click(),
    )
    expect(text()).toContain('/tmp/figs') // 全路径不许消失
    // 正常项不铺首屏——它们在「技术详情」里还有一份带取值的
    expect(text()).not.toContain(st('about.check.matplotlib'))
    await act(async () => byName(st('diagnostics.okDetails'))!.click())
    expect(text()).toContain(st('about.check.matplotlib'))
    expect(text()).not.toContain(PYTHON_PATH) // 好的不摆路径
  })

  it('结论那句话不说成「全部正常」（审计 T47）', async () => {
    await mount(CHECKS.filter((c) => c.ok))
    expect(text()).toContain(st('diagnostics.summaryOk'))
    // 结论那句话本身不许说成「全部正常」——它会被读成"图没问题"
    expect(st('diagnostics.summaryOk')).not.toBe('全部正常')
  })

  it('说清本页数据什么时候取的，并且重取一次真的再发一次请求（审计 T47）', async () => {
    await mount()
    expect(text()).toContain(st('diagnostics.fetchedAt', { time: '' }).replace(/\s*$/, ''))
    const calls = () => (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length
    const before = calls()
    await act(async () => byName(st('diagnostics.refetch'))!.click())
    await act(async () => {})
    expect(calls()).toBeGreaterThan(before)
  })

  it('两个环境不再同名，并且说清它们不是同一个（审计 T47）', async () => {
    await mount()
    await act(async () => byName(st('diagnostics.devTitle'))!.click())
    expect(text()).toContain(st('diagnostics.envNote', { product: 'Tavotto' }))
    // 「自带的」与「这个项目的」是两个不同的名字，不许有一个光叫「Tavotto 环境」
    const bundled = t('engine.sourceLabel.bundled', { ns: 'errors', product: 'Tavotto' })
    const managed = t('engine.managedEnvUsing', { ns: 'errors', product: 'Tavotto', version: '3.13' })
    expect(bundled).not.toBe('Tavotto 环境')
    expect(managed.startsWith('Tavotto 环境')).toBe(false)
    expect(bundled).not.toBe(managed)
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

  it('环境正常时渲染环境卡不在这一页（搬到了「项目」页）；给开发者里有检查明细；内置包版本清单不在这一页', async () => {
    await mount()
    // 按元素数，不按字符串出现次数——「渲染环境」四个字也出现在别的句子里
    expect(document.querySelectorAll('[data-engine-env-card]')).toHaveLength(0)
    await act(async () => byName(st('diagnostics.devTitle'))!.click())
    expect(document.querySelectorAll('[data-engine-env-card]')).toHaveLength(0)
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

describe('异常项给下一步（审计 T47）', () => {
  it('项目目录不可写：说清接下来做什么，不只是把路径摆出来', async () => {
    await mount(CHECKS)
    const line = [...document.querySelectorAll('[data-next-step]')].map((e) => e.textContent)
    expect(line.join('\n')).toContain(st('diagnostics.nextStep.project_writable'))
  })

  it('说不出真实动作的那几条不硬编一句（registry_conflicts 没有登记）', async () => {
    await mount([{ id: 'registry_conflicts', ok: false, label: '注册表 stem 归属', detail: '2 个冲突' }])
    expect(document.querySelectorAll('[data-next-step]')).toHaveLength(0)
    expect(text()).toContain('2 个冲突') // 原因照旧说
  })

  it('正常项不带下一步', async () => {
    // **挑一条登记过下一步的检查，让它是好的**：拿 worker_python 那种本来就
    // 没登记的来量，「没有下一步」在任何实现下都成立（判据恒真）
    await mount([{ id: 'project_writable', ok: true, label: '项目目录可写', detail: '/tmp/figs' }])
    await act(async () => byName(st('diagnostics.okDetails'))!.click())
    expect(text()).toContain(st('about.check.project_writable'))
    expect(document.querySelectorAll('[data-next-step]')).toHaveLength(0)
  })

  it('渲染引擎那条只在恢复卡片真的在这一屏上时才指着它说', async () => {
    // env.ok = true（默认 mount 给的就是好的）→ 卡片在「技术详情」里，首屏没有
    await mount([{ id: 'matplotlib', ok: false, label: 'matplotlib', detail: '无法导入' }])
    expect(document.querySelectorAll('[data-next-step]')).toHaveLength(0)

    // env 坏了 → 卡片常驻首屏，这时才说得出「下面那张卡片」。
    // **在挂载之后改 store**：`mount()` 自己会把 env 摆成好的那一份
    await act(async () => {
      useEnvStore.setState({ env: { ...useEnvStore.getState().env!, ok: false } as never })
    })
    expect(document.querySelectorAll('[data-engine-env-card]').length).toBeGreaterThan(0)
    expect(text()).toContain(st('diagnostics.nextStep.engine'))
  })
})
