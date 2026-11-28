/**
 * 关于页的配色来源致谢（ADR 0210 §三）：跟着推荐配色的入口开关走——入口关着，关于页与之前一字不差；
 * 打开后多一句来源说明 + Crameri 要求的引用（引文是出版物原文，不翻译）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { initI18n } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { paletteSource } from '@/lib/palette/catalog'
import { PALETTE_UI_DEV_KEY } from '@/lib/paletteUi'
import { useTelemetryStore } from '@/store/telemetryStore'
import { useUpdateStore } from '@/store/updateStore'
import { PrivacyAboutSettings } from './PrivacyAboutSettings'

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  initI18n('zh-CN')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  useTelemetryStore.setState({
    settings: { consent: 'disabled', enabled: false, hard_disabled: false, needs_reconsent: false } as never,
  })
  useUpdateStore.setState({
    status: { current: '0.17.0', auto_check: true, desktop: false, repo_url: '', releases_url: '' } as never,
    desktopChecked: true,
    desktopPhase: 'idle',
    desktopUpdate: null,
  })
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('no network') }))
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  localStorage.removeItem(PALETTE_UI_DEV_KEY)
  vi.unstubAllGlobals()
})

const render = () =>
  act(async () => {
    root.render(
      <TooltipProvider>
        <PrivacyAboutSettings />
      </TooltipProvider>,
    )
  })

it('入口关着：关于页没有配色致谢', async () => {
  await render()
  expect(host.querySelector('[data-about-palette-credits]')).toBeNull()
})

it('入口打开：一句来源说明 + Crameri 的引用与 doi', async () => {
  localStorage.setItem(PALETTE_UI_DEV_KEY, '1')
  await render()
  const credits = host.querySelector('[data-about-palette-credits]')
  expect(credits).not.toBeNull()
  const cite = host.querySelector('[data-about-palette-cite]')!.textContent!
  const crameri = paletteSource('crameri')!
  expect(cite).toContain(crameri.cite!)
  expect(cite).toContain(`doi:${crameri.doi}`)
})
