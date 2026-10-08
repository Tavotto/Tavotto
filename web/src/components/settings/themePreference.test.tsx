/**
 * 外观偏好（设置 › 通用 › 外观：跟随系统 / 浅色 / 深色；2026-10-07 暗色主题，宪法第二十八节）。
 *
 * 主语三层，各钉一条：
 *   1. `applyTheme`：`<html>` 上的 `data-theme`——system 不挂（让 `prefers-color-scheme` 那段媒体查询自己生效），
 *      light / dark 挂上（light 挡住媒体查询，dark 直接套深色表）；
 *   2. `uiStore`：偏好写进本机 `tavotto.ui`、下次启动读回来；未知值当 system；「界面看起来不对？」的重置不动它；
 *   3. 设置页那一行：点哪一档，`<html>` 当场就是哪一套（不用刷新）。
 * 颜色本身（两张值表、媒体查询那一段在不在、两段是否同一张表）由 `tokenContrast.test.ts` 量；
 * 真浏览器里「切了主题、计算出来的颜色真的变了」由 `e2e/theme.spec.ts` 量（jsdom 不算 CSS）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { applyTheme } from '@/lib/theme'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as never

const LS_KEY = 'tavotto.ui'
const html = () => document.documentElement

async function freshStore() {
  vi.resetModules()
  return (await import('@/store/uiStore')).useUiStore
}

beforeEach(() => {
  localStorage.clear()
  html().removeAttribute('data-theme')
})

describe('applyTheme：<html data-theme>', () => {
  it('dark / light 挂上对应的值，system 摘掉（交给 prefers-color-scheme）', () => {
    applyTheme('dark')
    expect(html().getAttribute('data-theme')).toBe('dark')
    applyTheme('light')
    expect(html().getAttribute('data-theme')).toBe('light')
    applyTheme('system')
    expect(html().hasAttribute('data-theme')).toBe(false)
  })
})

describe('uiStore.theme：本机偏好', () => {
  it('全新安装是「跟随系统」', async () => {
    const store = await freshStore()
    expect(store.getState().theme).toBe('system')
  })

  it('setTheme 当场落到 <html> 上、写进 tavotto.ui；下次启动读回来', async () => {
    let store = await freshStore()
    store.getState().setTheme('dark')
    expect(html().getAttribute('data-theme')).toBe('dark')
    expect(JSON.parse(localStorage.getItem(LS_KEY) ?? '{}').theme).toBe('dark')
    store = await freshStore()
    expect(store.getState().theme).toBe('dark')
  })

  it('本机存的是认不得的值：当「跟随系统」', async () => {
    localStorage.setItem(LS_KEY, JSON.stringify({ prefsVersion: 2, theme: 'sepia' }))
    const store = await freshStore()
    expect(store.getState().theme).toBe('system')
  })

  it('「界面看起来不对？」重置排布，不动外观', async () => {
    const store = await freshStore()
    store.getState().setTheme('dark')
    store.getState().resetLayoutPrefs()
    expect(store.getState().theme).toBe('dark')
    expect(JSON.parse(localStorage.getItem(LS_KEY) ?? '{}').theme).toBe('dark')
  })
})

describe('设置 › 通用 › 外观', () => {
  let root: Root | null = null
  let host: HTMLDivElement | null = null
  afterEach(async () => {
    await act(async () => root?.unmount())
    host?.remove()
    root = null
    host = null
  })

  it('三档：点「深色」<html> 当场是 dark，点「浅色」是 light，点「跟随系统」摘掉 data-theme', async () => {
    const store = await freshStore()
    const { GeneralSettings } = await import('./GeneralSettings')
    const { TooltipProvider } = await import('@/components/ui/Tooltip')
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root!.render(
        <TooltipProvider>
          <GeneralSettings close={() => {}} />
        </TooltipProvider>,
      )
    })
    const group = host.querySelector('[data-testid="settings-theme"]')
    expect(group, '外观那一行').toBeTruthy()
    const option = (v: string) => group!.querySelector<HTMLButtonElement>(`[data-value="${v}"]`)!
    expect([...group!.querySelectorAll('[data-value]')].map((b) => b.getAttribute('data-value'))).toEqual([
      'system',
      'light',
      'dark',
    ])
    expect(option('system').getAttribute('aria-checked')).toBe('true')

    await act(async () => option('dark').click())
    expect(html().getAttribute('data-theme')).toBe('dark')
    expect(store.getState().theme).toBe('dark')
    expect(option('dark').getAttribute('aria-checked')).toBe('true')

    await act(async () => option('light').click())
    expect(html().getAttribute('data-theme')).toBe('light')

    await act(async () => option('system').click())
    expect(html().hasAttribute('data-theme')).toBe(false)
    expect(store.getState().theme).toBe('system')
  })
})
