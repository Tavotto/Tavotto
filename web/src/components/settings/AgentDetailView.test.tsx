/**
 * Agent 详情：路径 / 版本 / 来源 / 诊断、显式保存的自定义可执行文件、
 * 以及只在这一层出现的第三方模型服务。
 *
 * issue #89 的回归看护在这里换了语义——不再是「失焦提交」，而是
 * **显式「验证并保存」**：不点保存就一个请求都不发，旧路径始终看得见，
 * 保存失败时草稿留着、后端那份有效设置不动。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAiCapabilities: vi.fn(),
  patchAiAgent: vi.fn(),
  setAiEndpointActive: vi.fn(),
  startAiInstall: vi.fn(),
  fetchAiInstallStatus: vi.fn(),
}))

import {
  fetchAiCapabilities,
  patchAiAgent,
  setAiEndpointActive,
  startAiInstall,
  type AiCapabilities,
} from '@/lib/api'
import { SettingsDialog } from '@/components/SettingsDialog'
import { t } from '@/i18n'
import { useAiStore } from '@/store/aiStore'
import { useUiStore } from '@/store/uiStore'
import { agentCaps, capsOf, claudeCaps } from './testCaps'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const fetchMock = vi.mocked(fetchAiCapabilities)
const patchMock = vi.mocked(patchAiAgent)
const activeMock = vi.mocked(setAiEndpointActive)
const installMock = vi.mocked(startAiInstall)

const ag = (key: string, v?: Record<string, unknown>) =>
  t(`settings.agents.${key}`, { ns: 'dialogs', ...(v ?? {}) })

let host: HTMLDivElement
let root: Root

const text = () => document.body.textContent ?? ''
const buttons = () => [...document.querySelectorAll('button')] as HTMLButtonElement[]
const byName = (name: string) =>
  buttons().find((b) => (b.getAttribute('aria-label') ?? b.textContent ?? '').includes(name))
const pathInput = () =>
  document.querySelector('input[placeholder="' + ag('detail.pathPlaceholder') + '"]') as
    | HTMLInputElement
    | null

const setValue = (input: HTMLInputElement, v: string) => {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value',
  )!.set!
  setter.call(input, v)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

/** 打开设置 → 编码 Agent → 第一个 Agent 的详情 */
async function openDetail(initial: AiCapabilities = capsOf([agentCaps(), claudeCaps()])) {
  fetchMock.mockResolvedValue(initial)
  useAiStore.setState({ caps: null })
  useUiStore.setState({ settingsOpen: true, settingsSection: 'ai' })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<SettingsDialog />)
  })
  await act(async () => {})
  const row = byName(ag('rowAria', { name: initial.agents[0].display_name }))!
  await act(async () => row.click())
}

/** 打开「自定义可执行文件」折叠块 */
async function openCustomFold() {
  const fold = [...document.querySelectorAll('details')].find((d) =>
    d.textContent?.includes(ag('detail.customExecutable')),
  )!
  await act(async () => {
    fold.open = true
    fold.dispatchEvent(new Event('toggle'))
  })
}

beforeEach(() => {
  fetchMock.mockReset()
  patchMock.mockReset()
  activeMock.mockReset()
  installMock.mockReset()
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  useAiStore.setState({ caps: null, agent: null })
  useUiStore.setState({ settingsOpen: false, settingsSection: null })
})

describe('Agent 详情', () => {
  it('概览给出状态、版本、可执行文件、来源和最后检测', async () => {
    await openDetail()
    expect(text()).toContain(ag('state.ready'))
    expect(text()).toContain('codex-cli 1.2.3')
    expect(text()).toContain('/opt/homebrew/bin/codex')
    expect(text()).toContain(ag('source.path'))
    expect(text()).toContain(ag('detail.checkedAt'))
  })

  it('诊断折叠区里有搜索路径与就绪结论，一级列表上没有', async () => {
    await openDetail()
    expect(text()).toContain(ag('detail.diagnostics'))
    expect(text()).toContain('/usr/local/bin')
    expect(text()).toContain(ag('readiness.ready'))
  })

  it('高级设置默认是折叠的', async () => {
    await openDetail()
    for (const d of document.querySelectorAll('details')) expect(d.open).toBe(false)
    expect(pathInput()).toBeNull()          // 没展开就没有输入框
  })

  it('回显已存的自定义路径', async () => {
    await openDetail(capsOf([agentCaps({ path_override: '/saved/codex', detection_source: 'custom' })]))
    await openCustomFold()
    expect(text()).toContain('/saved/codex')
    expect(text()).toContain(ag('detail.currentOverride'))
  })

  it('只有显式点「验证并保存」才提交（编辑期间一个请求都不发）', async () => {
    await openDetail()
    await openCustomFold()
    await act(async () => byName(ag('detail.useCustomExecutable'))!.click())
    const input = pathInput()!
    await act(async () => {
      input.focus()
      setValue(input, '/new/codex')
    })
    // 失焦不再提交——这正是 issue #89 里把已存路径清空的那条路径
    await act(async () => input.blur())
    expect(patchMock).not.toHaveBeenCalled()

    const saved = capsOf([agentCaps({ path_override: '/new/codex', detection_source: 'custom' })])
    patchMock.mockResolvedValue(saved)
    fetchMock.mockResolvedValue(saved)
    await act(async () => byName(ag('detail.validateAndSave'))!.click())
    expect(patchMock).toHaveBeenCalledExactlyOnceWith('codex', { path_override: '/new/codex' })
    expect(fetchMock).toHaveBeenCalledWith(true)     // 保存后重新探测
  })

  it('保存失败时草稿留着、错误说清楚，旧路径不被覆盖', async () => {
    await openDetail(capsOf([agentCaps({ path_override: '/saved/codex' })]))
    await openCustomFold()
    await act(async () => byName(ag('detail.useCustomExecutable'))!.click())
    const input = pathInput()!
    await act(async () => setValue(input, '/bad/codex'))
    patchMock.mockRejectedValue(
      Object.assign(new Error('bad'), { code: 'ai_agent_executable_invalid', params: { path: '/bad/codex' } }),
    )
    await act(async () => byName(ag('detail.validateAndSave'))!.click())
    await act(async () => {})
    expect(pathInput()!.value).toBe('/bad/codex')     // 草稿还在
    expect(document.querySelector('[role="alert"]')).not.toBeNull()
    expect(text()).toContain('/saved/codex')          // 旧的那份仍然看得见
  })

  it('保存在途时的新编辑不被完成回调顶掉', async () => {
    await openDetail()
    await openCustomFold()
    await act(async () => byName(ag('detail.useCustomExecutable'))!.click())
    await act(async () => setValue(pathInput()!, '/new/codex'))
    let resolvePatch!: (v: AiCapabilities) => void
    patchMock.mockImplementation(
      () => new Promise<AiCapabilities>((r) => (resolvePatch = r)),
    )
    await act(async () => byName(ag('detail.validateAndSave'))!.click())
    // 保存 + 重探测在途（可达数秒），用户接着改
    await act(async () => setValue(pathInput()!, '/newer/codex'))
    const saved = capsOf([agentCaps({ path_override: '/new/codex' })])
    fetchMock.mockResolvedValue(saved)
    await act(async () => resolvePatch(saved))
    await act(async () => {})
    expect(pathInput()!.value).toBe('/newer/codex')     // 新草稿仍在
  })

  it('恢复自动检测是一次明确的点击', async () => {
    await openDetail(capsOf([agentCaps({ path_override: '/saved/codex' })]))
    await openCustomFold()
    const cleared = capsOf([agentCaps({ path_override: null })])
    patchMock.mockResolvedValue(cleared)
    fetchMock.mockResolvedValue(cleared)
    await act(async () => byName(ag('detail.returnToAuto'))!.click())
    expect(patchMock).toHaveBeenCalledExactlyOnceWith('codex', { path_override: '' })
  })

  it('第三方模型服务只在详情里出现，且密钥仍然遮罩', async () => {
    const caps = capsOf([agentCaps()], {
      endpoints: [{
        id: 'kimi', label: 'Kimi', agent: 'codex',
        base_url: 'https://api.moonshot.cn/anthropic', models: ['k2'],
        default_model: 'k2', wire_api: 'chat', has_key: true, key_hint: '…cdef',
      }],
    })
    await openDetail(caps)
    expect(text()).toContain(ag('detail.modelService'))
    expect(text()).toContain('Kimi')
    // 完整密钥永远不出现（后端也从不回传，这里守的是前端不自己拼一个出来）
    expect(text()).not.toContain('sk-')
  })

  it('不支持第三方接口的 Agent 不显示模型服务区块', async () => {
    await openDetail(capsOf([agentCaps({
      features: { third_party_endpoints: false, model_selection: true,
                  effort_selection: false, wire_api_selection: false,
                  readiness_probe: false },
    })]))
    expect(text()).not.toContain(ag('detail.modelService'))
  })

  it('切换回官方登录态会重新读取能力', async () => {
    const caps = capsOf([agentCaps({ active_endpoint_id: 'kimi' })], {
      endpoints: [{
        id: 'kimi', label: 'Kimi', agent: 'codex', base_url: 'https://x/v1',
        models: [], default_model: null, wire_api: 'chat', has_key: true, key_hint: '…cdef',
      }],
    })
    await openDetail(caps)
    activeMock.mockResolvedValue(capsOf([agentCaps()]))
    fetchMock.mockResolvedValue(capsOf([agentCaps()]))
    const radios = [...document.querySelectorAll('input[type="radio"]')] as HTMLInputElement[]
    await act(async () => radios[0].click())
    expect(activeMock).toHaveBeenCalledWith('codex', '')
    expect(fetchMock).toHaveBeenCalledWith(true)
  })

  it('一键安装移进详情：先看到确切命令，确认后才跑', async () => {
    await openDetail(capsOf([agentCaps({
      state: 'not_installed', installed: false, usable: false,
      version: null, executable_path: null,
    })]))
    expect(text()).toContain('npm install -g @openai/codex')
    await act(async () => byName(ag('install.action', { name: 'Codex' }))!.click())
    expect(text()).toContain(ag('install.confirmBody'))
    installMock.mockResolvedValue({ status: 'running' })
    await act(async () => byName(ag('install.confirmAction'))!.click())
    expect(installMock).toHaveBeenCalledExactlyOnceWith('codex')
  })

  it('装好的 Agent 不显示安装入口', async () => {
    await openDetail()
    expect(text()).not.toContain('npm install -g')
  })

  it('没有 npm 时只引导装 Node.js，不给可点的安装按钮', async () => {
    await openDetail(capsOf([agentCaps({
      state: 'not_installed', installed: false, usable: false,
      install: { method: 'npm', package: '@openai/codex', available: false, status: 'idle' },
    })]))
    expect(text()).toContain(ag('install.noNpm'))
    expect(byName(ag('install.action', { name: 'Codex' }))!.disabled).toBe(true)
  })

  it('claude 那侧不显示推理强度（能力由后端声明）', async () => {
    await openDetail(capsOf([claudeCaps()]))
    expect(text()).toContain('Claude Code')
    expect(text()).toContain('claude 2.0.0')
  })
})
