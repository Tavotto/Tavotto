/** Copy contract for the normal network actions reachable from Settings.
 * This renders both channels; it is not packet capture or an OS-level privacy audit.
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { initI18n } from '@/i18n'
import { PrivacyAboutSettings } from './PrivacyAboutSettings'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useUpdateStore } from '@/store/updateStore'
import { useTelemetryStore } from '@/store/telemetryStore'

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  useTelemetryStore.setState({ settings: { consent: 'disabled', enabled: false, hard_disabled: false, needs_reconsent: false } as never })
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('No network needed for copy test') }))
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  initI18n('zh-CN')
  vi.unstubAllGlobals()
})

it.each(['en-US', 'zh-CN'] as const)('discloses package and assistant networking and channel-specific controls in %s', async (locale) => {
  initI18n(locale)
  for (const desktop of [false, true]) {
    useUpdateStore.setState({
      status: { current: '0.17.0', auto_check: true, desktop, repo_url: '', releases_url: '' } as never,
      desktopChecked: true, desktopPhase: 'idle', desktopUpdate: null,
    })
    await act(async () => { root.render(<TooltipProvider><PrivacyAboutSettings /></TooltipProvider>) })
    const disclosure = () => host.querySelector('[data-privacy-disclosure]')!
    const control = disclosure().querySelector('button')!
    if (control.getAttribute('aria-expanded') === 'false') act(() => control.click())
    const text = host.querySelector('[data-privacy-network-summary]')?.textContent ?? ''
    if (locale === 'en-US') {
      expect(text).toContain('server when you connect to a remote instance')
      expect(text).toContain('Package lookups, environment preparation and dependency installation')
      expect(text).toContain('download packages')
      expect(text).toContain('Codex / Claude tools you invoke may contact their own services')
      expect(text).toContain('only after you opt in')
      expect(text).toContain('In browser mode, “Check daily”')
      expect(text).toContain('desktop app checks at startup and has no in-app switch')
    } else {
      expect(text).toContain('连接远程实例时则是对应的服务器')
      expect(text).toContain('查找软件包、准备环境和安装依赖')
      expect(text).toContain('下载软件包')
      expect(text).toContain('Codex / Claude 工具可能按其自身隐私设置连接各自的服务')
      expect(text).toContain('经你同意后才发送')
      expect(text).toContain('浏览器模式可用“每天自动检查”')
      expect(text).toContain('桌面版在启动时检查，目前没有应用内关闭开关')
    }
    // 自动检查那一行两条通道都在（2026-10-07 设计审计 §9.1 桌面 / 浏览器同形）；桌面版那颗开关开着、停用——
    // 与上面那句「桌面版在启动时检查，目前没有应用内关闭开关」说的是同一件事
    const auto = host.querySelector<HTMLButtonElement>('#setting-update-auto')!
    expect(auto).toBeTruthy()
    expect(auto.disabled).toBe(desktop)
    if (desktop) expect(auto.getAttribute('aria-checked')).toBe('true')
  }
  expect(fetch).not.toHaveBeenCalled()
})
