/**
 * 执行器 / 型号选择器上的厂商图标：某执行器**当前走第三方接口**时，那一项显示该厂商的图标
 * （执行器名仍在文字里）；走 CLI 自己的登录态时显示执行器图标。
 * 与 `aiModelPicker.test.tsx` 分开放：那份在 #1316 的改动范围里。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { claudeCaps } from '@/components/settings/testCaps'
import type { AiAgentCaps, AiEndpoint } from '@/lib/api'
import { ModelPicker } from './ModelPicker'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}

const endpoint = (over: Partial<AiEndpoint> = {}): AiEndpoint => ({
  id: 'e1',
  label: 'DeepSeek',
  agent: 'claude',
  base_url: 'https://api.deepseek.com/anthropic',
  models: ['deepseek-v4-pro'],
  default_model: null,
  wire_api: 'responses',
  has_key: true,
  key_hint: '…1234',
  incompatible: null,
  migratable: null,
  ...over,
})

let root: Root
let host: HTMLDivElement
beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

async function mount(a: AiAgentCaps) {
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ModelPicker usable={[a]} active={a.id} model="" />
      </TooltipProvider>,
    )
  })
  return host.querySelector('[data-ai-model-picker="trigger"]') as HTMLElement
}

describe('选择器触发按钮上的图标', () => {
  it('走第三方接口：显示厂商图标，执行器名仍在文字里', async () => {
    const trigger = await mount(
      claudeCaps({ endpoint: endpoint({ icon_key: 'deepseek' }), active_endpoint_id: 'e1' }),
    )
    expect(trigger.querySelector('svg[data-vendor-icon="deepseek"]')).toBeTruthy()
    expect(trigger.textContent).toContain('Claude')
  })

  it('官方登录态（没有接管的接口）：显示执行器图标，没有厂商图标', async () => {
    const trigger = await mount(claudeCaps({ endpoint: null }))
    expect(trigger.querySelector('[data-vendor-icon]')).toBeNull()
    expect(trigger.querySelector('svg')).toBeTruthy()
  })

  it('接管的接口没有 icon_key（自定义 / 老后端）：通用图标，不是空白也不是执行器图标', async () => {
    const trigger = await mount(claudeCaps({ endpoint: endpoint(), active_endpoint_id: 'e1' }))
    expect(trigger.querySelector('[data-vendor-icon="generic"]')).toBeTruthy()
  })
})
