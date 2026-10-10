/**
 * 构建版本不一致（2026-10-07 设计审计 §10.1）：此前叫 UpdateBanner、实际是启动时自己弹出的模态对话框。
 * 现在是一条**非模态**的内嵌说明条——不挡界面、不抢焦点，「稍后」收起。
 * 主语：认 `data-build-mismatch`；「没有模态」= 文档里没有 `data-dialog`（共用对话框外壳的锚点）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { BuildMismatchNotice } from './BuildMismatchNotice'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement

beforeEach(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<BuildMismatchNotice />))
})
afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
})

describe('BuildMismatchNotice', () => {
  it('是一条内嵌说明条，不是对话框；焦点不被抢走', () => {
    expect(host.querySelector('[data-build-mismatch]')).not.toBeNull()
    expect(document.querySelector('[data-dialog]')).toBeNull()
    expect(document.activeElement).toBe(document.body)
  })

  it('「稍后」收起，这一次不再出现', async () => {
    const buttons = host.querySelectorAll<HTMLButtonElement>('[data-build-mismatch] button')
    await act(async () => buttons[buttons.length - 1].click())
    expect(host.querySelector('[data-build-mismatch]')).toBeNull()
  })
})
