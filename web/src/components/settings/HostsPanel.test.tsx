/**
 * 设置 →「接入 AI 编辑器」（用户 2026-10-27）。
 *
 * 主语是**界面上真的出现了什么、按钮真的调了什么**：
 * - 没装的宿主只有「未检测到」，没有按钮；装了的一个「接入」；接好的「已接入」。
 * - 第一次接入先选文件夹，选的路径原样交给 `runHostIntegration('connect', host, 路径)`；取消选择就什么都不调。
 * - 失败显示翻译过的一句原因，**不是** `config_unreadable` 这串下划线英文。
 * - 浏览器模式整组不显示（不画按不动的按钮）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  runHostIntegration: vi.fn(),
  pickDirectory: vi.fn(),
}))

import { pickDirectory, runHostIntegration } from '@/lib/desktop'
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
const tx = (key: string, values?: Record<string, unknown>) =>
  t(`settings.hosts.${key}`, { ns: 'dialogs', ...(values ?? {}) })

const LIST = (overrides: Record<string, unknown> = {}) =>
  JSON.stringify({
    ok: true,
    action: 'list',
    server: true,
    project_root: null,
    hosts: [
      { id: 'codex', label: 'Codex', installed: true, connected: true },
      { id: 'cursor', label: 'Cursor', installed: true, connected: false },
      { id: 'zcode', label: 'ZCode', installed: false, connected: false },
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

  it('asks for the folder on the first connect and passes it through unchanged', async () => {
    runMock.mockResolvedValueOnce(LIST())
    pickMock.mockResolvedValue('/Users/me/论文/figs')
    runMock.mockResolvedValueOnce(
      JSON.stringify({ ok: true, host: 'cursor', action: 'connect', state: 'needs_new_session', steps: [] }),
    )
    runMock.mockResolvedValue(LIST({ project_root: '/Users/me/论文/figs' }))
    await mount()
    await act(async () => connectButton('cursor')!.click())
    expect(pickMock).toHaveBeenCalledTimes(1)
    expect(runMock).toHaveBeenCalledWith('connect', 'cursor', '/Users/me/论文/figs')
    expect(row('cursor')?.textContent).toContain(tx('doneNewSession', { host: 'Cursor' }))
  })

  it('does nothing when the folder dialog is cancelled', async () => {
    runMock.mockResolvedValue(LIST())
    pickMock.mockResolvedValue(null)
    await mount()
    await act(async () => connectButton('cursor')!.click())
    expect(runMock).toHaveBeenCalledTimes(1) // 只有最初那次 list
  })

  it('does not ask again once a folder is remembered', async () => {
    runMock.mockResolvedValueOnce(LIST({ project_root: '/p' }))
    runMock.mockResolvedValueOnce(
      JSON.stringify({ ok: true, host: 'cursor', action: 'connect', state: 'connected', steps: [] }),
    )
    runMock.mockResolvedValue(LIST({ project_root: '/p' }))
    await mount()
    await act(async () => connectButton('cursor')!.click())
    expect(pickMock).not.toHaveBeenCalled()
    expect(runMock).toHaveBeenCalledWith('connect', 'cursor', undefined)
  })

  it('shows a translated reason, never the raw code', async () => {
    runMock.mockResolvedValueOnce(LIST({ project_root: '/p' }))
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
    runMock.mockResolvedValue(LIST({ project_root: '/p' }))
    await mount()
    await act(async () => connectButton('cursor')!.click())
    const reason = tx('error.config_unreadable', { host: 'Cursor' })
    expect(row('cursor')?.textContent).toContain(reason)
    expect(row('cursor')?.querySelector('[role="alert"], .text-danger-content')?.textContent ?? reason).not.toContain(
      'config_unreadable',
    )
    expect(connectButton('cursor')?.textContent).toBe(tx('retry'))
  })

  it('lets the user pick another folder when the remembered one is rejected', async () => {
    runMock.mockResolvedValueOnce(LIST({ project_root: '/gone' }))
    runMock.mockResolvedValueOnce(
      JSON.stringify({
        ok: false,
        host: 'cursor',
        action: 'connect',
        state: 'failed',
        error_code: 'project_root_rejected',
        steps: [{ step: 'project_root', ok: false, skipped: false, error_code: 'project_root_rejected' }],
      }),
    )
    runMock.mockResolvedValueOnce(LIST({ project_root: '/gone' }))
    pickMock.mockResolvedValue('/new/figs')
    runMock.mockResolvedValueOnce(
      JSON.stringify({ ok: true, host: 'cursor', action: 'connect', state: 'needs_new_session', steps: [] }),
    )
    runMock.mockResolvedValue(LIST({ project_root: '/new/figs' }))
    await mount()
    await act(async () => connectButton('cursor')!.click()) // 第一次：用记住的那个，被拒
    expect(pickMock).not.toHaveBeenCalled()
    expect(connectButton('cursor')?.textContent).toBe(tx('chooseFolder'))
    await act(async () => connectButton('cursor')!.click()) // 第二次：先换文件夹
    expect(pickMock).toHaveBeenCalledTimes(1)
    expect(runMock).toHaveBeenCalledWith('connect', 'cursor', '/new/figs')
  })

  it('renders nothing in the browser', async () => {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
    await mount()
    expect(document.querySelector('[data-hosts-section]')).toBeNull()
    expect(runMock).not.toHaveBeenCalled()
  })
})
