/**
 * 模型服务区每条接口行前的厂商图标（后端 `public()` 给的 `icon_key`；自定义 / 老后端 = 通用图标）。
 * 与 `AgentDetailView.test.tsx` 分开放，只借它的挂载方式。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAiCapabilities: vi.fn(),
}))

import { fetchAiCapabilities, type AiCapabilities, type AiEndpoint } from '@/lib/api'
import { SettingsDialog } from '@/components/SettingsDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { t } from '@/i18n'
import { useAiStore } from '@/store/aiStore'
import { useUiStore } from '@/store/uiStore'
import { agentCaps, capsOf, claudeCaps } from './testCaps'

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLDivElement
let root: Root

const ep = (id: string, over: Partial<AiEndpoint>): AiEndpoint => ({
  id,
  label: id,
  agent: 'claude',
  base_url: 'https://x.example/anthropic',
  models: [],
  default_model: null,
  wire_api: 'responses',
  has_key: true,
  key_hint: '…1234',
  incompatible: null,
  migratable: null,
  ...over,
})

async function openFirstAgentDetail(caps: AiCapabilities) {
  vi.mocked(fetchAiCapabilities).mockResolvedValue(caps)
  useAiStore.setState({ caps: null })
  useUiStore.setState({ settingsOpen: true, settingsSection: 'ai' })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <SettingsDialog />
      </TooltipProvider>,
    )
  })
  await act(async () => {})
  const name = caps.agents[0].display_name
  const row = [...document.querySelectorAll('button')].find((b) =>
    (b.getAttribute('aria-label') ?? b.textContent ?? '').includes(
      t('settings.agents.rowAria', { ns: 'dialogs', name }),
    ),
  )!
  await act(async () => row.click())
}

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  useAiStore.setState({ caps: null, agent: null })
  useUiStore.setState({ settingsOpen: false, settingsSection: null })
})

describe('接口行前的厂商图标', () => {
  it('对得上厂商的接口显示那家的图标，没有 icon_key 的显示通用图标', async () => {
    await openFirstAgentDetail(
      capsOf([claudeCaps(), agentCaps()], {
        endpoints: [ep('ds', { icon_key: 'deepseek' }), ep('mine', {})],
      }),
    )
    const ds = document.querySelector('[data-endpoint-row="ds"]')!
    const mine = document.querySelector('[data-endpoint-row="mine"]')!
    expect(ds.querySelector('[data-vendor-icon="deepseek"]')).toBeTruthy()
    expect(mine.querySelector('[data-vendor-icon="generic"]')).toBeTruthy()
  })
})
