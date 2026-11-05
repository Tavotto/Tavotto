/**
 * 设置 →「接入 AI 编辑器」（用户 2026-10-27）。
 *
 * 主语是**界面上真的出现了什么、按钮真的调了什么**：
 * - 没装的宿主只有「未检测到」，没有按钮；装了的一个「接入」；接好的「已接入」。
 * - 点「接入」就接，不弹选文件夹（用户 2026-11-04，ADR 0009 §1b）：只调 `runHostIntegration('connect', host)`。
 * - 失败显示翻译过的一句原因，**不是** `config_unreadable` 这串下划线英文。
 * - 浏览器模式整组不显示（不画按不动的按钮）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  runHostIntegration: vi.fn(),
  runCodexIntegration: vi.fn(),
  pickDirectory: vi.fn(),
}))

import { pickDirectory, runCodexIntegration, runHostIntegration } from '@/lib/desktop'
import { t } from '@/i18n'
import { HostsPanel } from './HostsPanel'
import { TooltipProvider } from '@/components/ui/Tooltip'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const runMock = vi.mocked(runHostIntegration)
const pickMock = vi.mocked(pickDirectory)
const codexMock = vi.mocked(runCodexIntegration)
const tx = (key: string, values?: Record<string, unknown>) =>
  t(`settings.hosts.${key}`, { ns: 'dialogs', ...(values ?? {}) })

const LIST = (overrides: Record<string, unknown> = {}) =>
  JSON.stringify({
    ok: true,
    action: 'list',
    server: true,
    hosts: [
      { id: 'codex', label: 'Codex', installed: true, connected: true },
      { id: 'cursor', label: 'Cursor', installed: true, connected: false },
      { id: 'zcode', label: 'ZCode', installed: false, connected: false },
      { id: 'antigravity', label: 'Antigravity', installed: true, connected: false },
    ],
    ...overrides,
  })

let host: HTMLDivElement
let root: Root

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <HostsPanel />
      </TooltipProvider>,
    )
  })
}

const row = (id: string) => document.querySelector(`[data-host-row="${id}"]`) as HTMLElement | null
const connectButton = (id: string) =>
  document.querySelector(`[data-host-connect="${id}"]`) as HTMLButtonElement | null

describe('HostsPanel', () => {
  beforeEach(() => {
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    runMock.mockReset()
    pickMock.mockReset()
    codexMock.mockReset()
  })
  afterEach(async () => {
    await act(async () => root?.unmount())
    host?.remove()
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
  })

  it('shows not-found without a button, connect for installed, connected for done', async () => {
    runMock.mockResolvedValue(LIST())
    await mount()
    expect(row('zcode')?.textContent).toContain(tx('notDetected'))
    expect(connectButton('zcode')).toBeNull()
    expect(connectButton('cursor')?.textContent).toBe(tx('connect'))
    expect(row('codex')?.querySelector('[data-host-connected]')?.textContent).toContain(tx('connected'))
    expect(connectButton('codex')).toBeNull()
  })

  it('draws Antigravity with its own logo, not the generic fallback', async () => {
    runMock.mockResolvedValue(LIST())
    await mount()
    expect(row('antigravity')?.querySelector('[data-brand-logo]')?.getAttribute('data-brand-logo')).toBe('antigravity')
    expect(connectButton('antigravity')?.textContent).toBe(tx('connect'))
  })

  it('connects with one click: no folder dialog, nothing but the host goes to the shell', async () => {
    runMock.mockResolvedValueOnce(LIST())
    runMock.mockResolvedValueOnce(
      JSON.stringify({ ok: true, host: 'cursor', action: 'connect', state: 'needs_new_session', steps: [] }),
    )
    runMock.mockResolvedValue(LIST())
    await mount()
    await act(async () => connectButton('cursor')!.click())
    expect(pickMock).not.toHaveBeenCalled()
    expect(runMock).toHaveBeenCalledWith('connect', 'cursor')
    expect(row('cursor')?.textContent).toContain(tx('doneNewSession', { host: 'Cursor' }))
  })

  it('shows a translated reason, never the raw code', async () => {
    runMock.mockResolvedValueOnce(LIST())
    runMock.mockResolvedValueOnce(
      JSON.stringify({
        ok: false,
        host: 'cursor',
        action: 'connect',
        state: 'failed',
        error_code: 'config_unreadable',
        steps: [{ step: 'config', ok: false, skipped: false, error_code: 'config_unreadable', detail: '/x/mcp.json' }],
      }),
    )
    runMock.mockResolvedValue(LIST())
    await mount()
    await act(async () => connectButton('cursor')!.click())
    const reason = tx('error.config_unreadable', { host: 'Cursor' })
    expect(row('cursor')?.textContent).toContain(reason)
    expect(row('cursor')?.querySelector('[role="alert"], .text-danger-content')?.textContent ?? reason).not.toContain(
      'config_unreadable',
    )
    expect(connectButton('cursor')?.textContent).toBe(tx('retry'))
  })

  it('renders nothing in the browser', async () => {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
    await mount()
    expect(document.querySelector('[data-hosts-section]')).toBeNull()
    expect(runMock).not.toHaveBeenCalled()
  })

  const stateList = (hosts: Record<string, unknown>[]) =>
    JSON.stringify({ ok: true, action: 'list', server: true, hosts })
  const stateOf = (id: string) => row(id)?.querySelector('[data-host-connected]')?.getAttribute('data-host-state')

  it('shows skill version, built-in notes, outdated plugin and kept skill', async () => {
    runMock.mockResolvedValue(
      stateList([
        { id: 'codex', label: 'Codex', installed: true, connected: true, skill: 'builtin', version: '0.21.0', update: { latest: '0.22.0', how: 'codex_plugin' } },
        { id: 'cursor', label: 'Cursor', installed: true, connected: true, skill: 'current', version: '0.22.0' },
        { id: 'claude-desktop', label: 'Claude', installed: true, connected: true, skill: 'builtin' },
        { id: 'vscode', label: 'VS Code', installed: true, connected: true, skill: 'modified', version: '0.21.0' },
      ]),
    )
    await mount()
    expect(stateOf('codex')).toBe('plugin-outdated')
    expect(row('codex')?.textContent).toContain(tx('pluginOutdated', { version: '0.21.0' }))
    expect(row('codex')?.querySelector('[data-host-update="codex"]')).not.toBeNull()
    expect(stateOf('cursor')).toBe('skill')
    expect(row('cursor')?.textContent).toContain(tx('connectedSkill', { version: '0.22.0' }))
    expect(row('cursor')?.querySelector('[data-host-update],[data-host-overwrite]')).toBeNull()
    expect(stateOf('claude-desktop')).toBe('builtin')
    expect(row('claude-desktop')?.textContent).toContain(tx('connectedBuiltin'))
    expect(stateOf('vscode')).toBe('skill-modified')
    expect(row('vscode')?.textContent).toContain(tx('skillKept'))
    // 改过 / 有新版不算一切正常：不给绿色对勾
    expect(row('vscode')?.querySelector('[data-host-connected] svg')).toBeNull()
    expect(row('vscode')?.querySelector('[data-host-overwrite="vscode"]')).not.toBeNull()
  })

  it('update button runs the codex upgrade channel and refreshes', async () => {
    runMock.mockResolvedValue(
      stateList([{ id: 'codex', label: 'Codex', installed: true, connected: true, skill: 'builtin', version: '0.21.0', update: { latest: '0.22.0', how: 'codex_plugin' } }]),
    )
    codexMock.mockResolvedValue(JSON.stringify({ ok: true, action: 'upgrade', steps: [] }))
    await mount()
    await act(async () => (row('codex')!.querySelector('[data-host-update="codex"]') as HTMLButtonElement).click())
    expect(codexMock).toHaveBeenCalledWith('upgrade')
    expect(runMock.mock.calls.filter((c) => c[0] === 'list').length).toBeGreaterThanOrEqual(2)
  })

  it('overwrite asks once, and only the confirm sends connect_force_skill', async () => {
    runMock.mockResolvedValue(
      stateList([{ id: 'vscode', label: 'VS Code', installed: true, connected: true, skill: 'modified', version: '0.21.0' }]),
    )
    await mount()
    await act(async () => (row('vscode')!.querySelector('[data-host-overwrite="vscode"]') as HTMLButtonElement).click())
    expect(runMock).not.toHaveBeenCalledWith('connect_force_skill', 'vscode')
    runMock.mockResolvedValueOnce(JSON.stringify({ ok: true, host: 'vscode', action: 'connect', state: 'connected', steps: [] }))
    await act(async () => (document.querySelector('[data-host-overwrite-confirm]') as HTMLButtonElement).click())
    expect(runMock).toHaveBeenCalledWith('connect_force_skill', 'vscode')
  })
})
