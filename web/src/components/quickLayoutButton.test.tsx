/**
 * 工具条上的「一键排版」（ADR 0199）：授权入口没出现 = 整个不画；出现但没解锁 = PremiumLock 置灰（不吃点击）；
 * 解锁 = 可点，点了按小下拉里记住的选项发起；选项记在本机。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const runQuickLayout = vi.fn(async () => 'applied')
vi.mock('@/store/quickLayoutActions', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/quickLayoutActions')>()),
  runQuickLayout: (...a: unknown[]) => runQuickLayout(...(a as [])),
}))

import { CanvasToolbar } from '@/components/CanvasToolbar'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { readQuickLayoutPrefs } from '@/lib/quickLayoutPrefs'
import { useDocumentStore } from '@/store/documentStore'
import { useLicenseStore } from '@/store/licenseStore'
import { useQuickLayoutStore } from '@/store/quickLayoutActions'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
import { emptyProject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

let root: Root
let host: HTMLDivElement

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <CanvasToolbar />
      </TooltipProvider>,
    )
  })
}
const q = <T extends HTMLElement = HTMLElement>(sel: string) => host.querySelector<T>(sel)

const licensed = (features: string[]) => ({
  enabled: true as const,
  licensed: features.length > 0,
  state: 'active' as const,
  features,
  exp: null,
  email_hint: '',
  needs_refresh: false,
})

beforeEach(async () => {
  localStorage.clear()
  runQuickLayout.mockClear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ql_toolbar')
  useUiStore.setState({ tool: 'select' })
  useWorkspaceStore.setState({ mode: 'layout' })
  useQuickLayoutStore.setState({ busy: false })
})

afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
})

describe('一键排版按钮', () => {
  it('授权入口没出现（enabled: false）：整个不画', async () => {
    useLicenseStore.setState({ status: { enabled: false } })
    await mount()
    expect(q('[data-quick-layout]')).toBeNull()
    expect(q('[data-premium-lock]')).toBeNull()
  })

  it('入口出现但没解锁：置灰（PremiumLock），里面的按钮 inert，点外层去「关于与更新」、不发起', async () => {
    useLicenseStore.setState({ status: licensed([]) })
    await mount()
    const lock = q('[data-premium-lock="quick-layout"]')
    expect(lock, '没解锁要画锁').toBeTruthy()
    expect(lock!.getAttribute('aria-disabled')).toBe('true')
    // 真浏览器里 inert 挡掉指针与焦点（jsdom 不实现 inert，所以只钉属性）
    expect(q('[data-quick-layout-run]')!.closest('[inert]')).toBeTruthy()
    await act(async () => lock!.click())
    expect(runQuickLayout).not.toHaveBeenCalled()
    expect(useUiStore.getState().settingsOpen).toBe(true)
  })

  it('解锁：紧跟在「序号」之后，点了按默认「双栏 / 3 mm」发起', async () => {
    useLicenseStore.setState({ status: licensed(['quick-layout']) })
    await mount()
    expect(q('[data-premium-lock]')).toBeNull()
    const items = [...host.querySelectorAll('[data-toolbar-item]')]
    const run = q('[data-quick-layout-run]')!
    // 序号钮（带「序号」字）在一键排版钮正前面，适应钮在后面
    expect(items[items.indexOf(run) - 1].textContent).toBe('序号')
    expect(items.at(-1)!.hasAttribute('data-fit-canvas')).toBe(true)
    await act(async () => run.click())
    expect(runQuickLayout).toHaveBeenCalledWith('double', 3)
  })

  it('忙碌时按钮是忙碌态且不可再点', async () => {
    useLicenseStore.setState({ status: licensed(['quick-layout']) })
    useQuickLayoutStore.setState({ busy: true })
    await mount()
    const run = q<HTMLButtonElement>('[data-quick-layout-run]')!
    expect(run.disabled).toBe(true)
  })

  it('下拉里换成单栏 / 2 mm：之后用新选项发起，并记在本机', async () => {
    useLicenseStore.setState({ status: licensed(['quick-layout']) })
    await mount()
    await act(async () => {
      q('[data-quick-layout-options]')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }))
    })
    const pick = async (sel: string) => {
      const el = document.querySelector<HTMLElement>(sel)
      expect(el, `下拉里没有 ${sel}`).toBeTruthy()
      await act(async () => {
        el!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }))
        el!.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, button: 0, pointerType: 'mouse' }))
        el!.click()
      })
    }
    await pick('[data-quick-layout-target="single"]')
    expect(readQuickLayoutPrefs()).toEqual({ target: 'single', gapMm: 3, fixSpec: true })
    await act(async () => {
      q('[data-quick-layout-options]')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }))
    })
    await pick('[data-quick-layout-gap="2"]')
    expect(readQuickLayoutPrefs()).toEqual({ target: 'single', gapMm: 2, fixSpec: true })
    await act(async () => q('[data-quick-layout-run]')!.click())
    expect(runQuickLayout).toHaveBeenCalledWith('single', 2)
  })

  it('下拉里的「同时按规范修…」开关：默认勾上，点一下关掉并记在本机，再点开回来', async () => {
    useLicenseStore.setState({ status: licensed(['quick-layout']) })
    await mount()
    const open = () =>
      act(async () => {
        q('[data-quick-layout-options]')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }))
      })
    const toggle = async () => {
      const el = document.querySelector<HTMLElement>('[data-quick-layout-fix-spec]')
      expect(el).toBeTruthy()
      expect(el!.textContent).toBe('同时按规范修文字和线条')
      await act(async () => {
        el!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }))
        el!.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, button: 0, pointerType: 'mouse' }))
        el!.click()
      })
    }
    await open()
    const box = () => document.querySelector('[data-quick-layout-fix-spec]')!
    expect(box().getAttribute('aria-checked')).toBe('true')
    // 开关不关菜单：连点两下，勾跟着变
    await toggle()
    expect(readQuickLayoutPrefs().fixSpec).toBe(false)
    expect(box().getAttribute('aria-checked')).toBe('false')
    await toggle()
    expect(readQuickLayoutPrefs().fixSpec).toBe(true)
  })

  it('本机存储坏了 / 不可用：退回默认值，不抛', () => {
    localStorage.setItem('tavotto.quickLayout', '{坏的')
    expect(readQuickLayoutPrefs()).toEqual({ target: 'double', gapMm: 3, fixSpec: true })
    localStorage.setItem('tavotto.quickLayout', JSON.stringify({ target: 'x', gapMm: 9 }))
    expect(readQuickLayoutPrefs()).toEqual({ target: 'double', gapMm: 3, fixSpec: true })
    localStorage.setItem('tavotto.quickLayout', JSON.stringify({ target: 'single', gapMm: 2, fixSpec: 'no' }))
    expect(readQuickLayoutPrefs()).toEqual({ target: 'single', gapMm: 2, fixSpec: true })
    localStorage.setItem('tavotto.quickLayout', JSON.stringify({ target: 'single', gapMm: 2, fixSpec: false }))
    expect(readQuickLayoutPrefs().fixSpec).toBe(false)
  })
})
