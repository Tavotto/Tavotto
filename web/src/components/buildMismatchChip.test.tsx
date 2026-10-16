/**
 * 构建版本不一致（2026-10-16 UI 2.0「不推挤」）：顶栏右侧的状态芯片，不是文档流里的提示条。
 * 主语：认 `data-build-mismatch`；「没有模态」= 文档里没有 `data-dialog`（共用对话框外壳的锚点）。
 * 「不推挤」的像素判据在 e2e（页签行与画布的 y 不变）；这里钉组件自己的契约。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const outdated = vi.hoisted(() => ({ value: true }))
vi.mock('@/hooks/useBuildVersion', () => ({ useBuildVersion: () => outdated.value }))

import { BuildMismatchChip } from './BuildMismatchChip'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement

const mount = () => act(async () => root.render(<BuildMismatchChip />))

beforeEach(() => {
  outdated.value = true
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
})

describe('BuildMismatchChip', () => {
  it('构建一致时什么都不画', async () => {
    outdated.value = false
    await mount()
    expect(host.querySelector('[data-build-mismatch]')).toBeNull()
  })

  it('是一颗 24 高的芯片按钮：圆点 + 一句短语；不是对话框，焦点不被抢走', async () => {
    await mount()
    const chip = host.querySelector<HTMLButtonElement>('[data-build-mismatch]')!
    expect(chip.tagName).toBe('BUTTON')
    expect(chip.className).toMatch(/\bh-6\b/)
    expect(chip.className).toMatch(/\brounded-full\b/)
    expect(chip.textContent).toBe('界面需要刷新')
    // 颜色只在 6px 圆点上，芯片的字不染色
    expect(chip.querySelector('[aria-hidden]')!.className).toMatch(/\bsize-1\.5\b/)
    expect(chip.className).not.toMatch(/text-(?:warn|danger|info)/)
    expect(document.querySelector('[data-dialog]')).toBeNull()
    expect(document.activeElement).toBe(document.body)
  })

  it('点开是一句话 + 一个动作：刷新页面', async () => {
    const reload = vi.fn()
    Object.defineProperty(window, 'location', { value: { ...window.location, reload }, configurable: true })
    await mount()
    await act(async () => host.querySelector<HTMLButtonElement>('[data-build-mismatch]')!.click())
    const pop = document.querySelector('[data-build-mismatch-popover]')!
    expect(pop.textContent).toContain('刷新后使用新版本')
    expect(pop.querySelectorAll('button')).toHaveLength(1)
    await act(async () => pop.querySelector<HTMLButtonElement>('[data-build-mismatch-refresh]')!.click())
    expect(reload).toHaveBeenCalledOnce()
  })
})
