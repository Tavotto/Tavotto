/**
 * 隐私摘要里「发送问题反馈」那一句（ADR 0118）：功能默认关闭，关着就不多说；维护者打开后才补上
 * 「逐次确认后才上传」。读的是 `diagSendStore` 的镜像——渲染这一页本身不联网（见 privacyNetworkCopy.test）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { initI18n, t } from '@/i18n'
import { PrivacyAboutSettings } from './PrivacyAboutSettings'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useUpdateStore } from '@/store/updateStore'
import { useTelemetryStore } from '@/store/telemetryStore'
import { useDiagSendStore } from '@/store/diagSendStore'

let host: HTMLDivElement
let root: Root
beforeEach(() => {
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
  initI18n('zh-CN')
  useDiagSendStore.setState({ capability: null })
  vi.unstubAllGlobals()
})

async function summary(locale: 'en-US' | 'zh-CN') {
  initI18n(locale)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <PrivacyAboutSettings />
      </TooltipProvider>,
    )
  })
  const control = host.querySelector('[data-privacy-disclosure]')!.querySelector('button')!
  if (control.getAttribute('aria-expanded') === 'false') act(() => control.click())
  return host.querySelector('[data-privacy-network-summary]')?.textContent ?? ''
}

it.each(['en-US', 'zh-CN'] as const)('默认关闭：摘要不提发送问题反馈（%s）', async (locale) => {
  const text = await summary(locale)
  expect(text).not.toContain(t('settings.about.privacySend', { ns: 'dialogs' }))
})

it.each(['en-US', 'zh-CN'] as const)('维护者打开后：摘要补一句「逐次确认才上传」（%s）', async (locale) => {
  useDiagSendStore.setState({ capability: { enabled: true } })
  const text = await summary(locale)
  expect(text).toContain(t('settings.about.privacySend', { ns: 'dialogs' }))
})
