/**
 * 厂商图标：表里有图形的画图形（单色，跟随 currentColor）；表外 key 落到通用图标而不是空白。
 * 以及预设下拉的每一行。（接口行见 AgentDetailView.vendorIcon.test.tsx，选择器见 ModelPicker.vendorIcon.test.tsx。）
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { EndpointDialog } from './EndpointDialog'
import { VENDOR_GLYPHS } from './vendorIcons'
import { VendorIcon } from './VendorIcon'
import type { AiEndpointPreset } from '@/lib/api'

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
  document.body.innerHTML = ''
})

const render = (node: React.ReactNode) => act(async () => root.render(node))

describe('VendorIcon', () => {
  it('表里的 key 画本地内联矢量：currentColor、24 网格、没有远程图片', async () => {
    await render(<VendorIcon iconKey="deepseek" size={20} glyphSize={16} />)
    const svg = host.querySelector('svg[data-vendor-icon="deepseek"]')!
    expect(svg.getAttribute('fill')).toBe('currentColor')
    expect(svg.getAttribute('viewBox')).toBe('0 0 24 24')
    expect(host.querySelector('img, image, use')).toBeNull()
    expect(svg.querySelectorAll('path').length).toBe(VENDOR_GLYPHS.deepseek.length)
  })

  it.each([undefined, null, '', 'custom', 'synthetic', 'no-such-vendor'])(
    '表外 key %s → 通用图标，外框仍在',
    async (key) => {
      await render(<VendorIcon iconKey={key} />)
      expect(host.querySelector('[data-vendor-icon="generic"]')).toBeTruthy()
      expect(host.querySelector('svg')).toBeTruthy()
    },
  )

  it('图形表里没有空条目', () => {
    for (const [key, paths] of Object.entries(VENDOR_GLYPHS)) {
      expect(paths.length, key).toBeGreaterThan(0)
      for (const p of paths) expect(p.d.length, key).toBeGreaterThan(20)
    }
  })
})

describe('预设下拉每一行前的图标', () => {
  const PRESETS: AiEndpointPreset[] = [
    { id: 'deepseek', label: 'DeepSeek', icon_key: 'deepseek', region: 'cn', agent: 'claude', base_url: 'https://api.deepseek.com/anthropic', models: [] },
    { id: 'custom-claude', label: 'Custom', icon_key: 'custom', region: 'custom', agent: 'claude', base_url: '', models: [] },
  ]
  it('每个选项带自己的厂商图标（自定义 = 通用）', async () => {
    await render(
      <EndpointDialog agent="claude" agentLabel="Claude Code" wireApi={false} existing={null} presets={PRESETS} onClose={() => {}} onSave={() => {}} />,
    )
    const trigger = document.body.querySelector<HTMLElement>('[role="combobox"]')!
    await act(async () => trigger.click())
    const options = [...document.body.querySelectorAll('[role="option"]')]
    expect(options).toHaveLength(2)
    expect(options[0].querySelector('[data-vendor-icon="deepseek"]')).toBeTruthy()
    expect(options[1].querySelector('[data-vendor-icon="generic"]')).toBeTruthy()
  })
})
