/**
 * 入口收敛（2026-10 界面审计 A2）：同一件事只在一个家。
 *
 *   - 更多菜单不再放「论文样式…」「画布设置」（样式在左栏「样式」、画布设置在右栏「画布」页签；命令面板的项保留）；
 *   - 排版菜单没有「排版时间线…」、顶栏没有时钟钮（时间线是右栏页签；⇧⌘H 与命令面板保留）；
 *   - 教程：顶栏胶囊在时更多菜单不放教程项，胶囊消失后才有（两个入口不同时出现）。
 *
 * 判据的主语：菜单里有没有这一项 = 打开菜单后 portal 里 `[role="menuitem"]` 的文字；胶囊在不在 = `[data-tutorial-chip]`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useOnboardingStore } from '@/store/onboardingStore'
import { MoreMenu } from './TopBar'
import { TutorialChip } from './onboarding/TutorialChip'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLElement
let root: Root

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <TutorialChip />
        <MoreMenu writeBackItem={null} />
      </TooltipProvider>,
    )
  })
}

async function openMenu(): Promise<string[]> {
  const trigger = host.querySelector<HTMLElement>('[data-more-menu]')!
  await act(async () => {
    trigger.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, ctrlKey: false }))
    trigger.click()
  })
  return [...document.body.querySelectorAll('[role="menuitem"]')].map((i) => i.textContent ?? '')
}

beforeEach(() => {
  localStorage.clear()
  useOnboardingStore.setState({ status: 'not_started', welcomeSeenAt: null, surface: 'none' } as never)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

describe('更多菜单', () => {
  it('没有「论文样式…」「画布设置」', async () => {
    await mount()
    const items = await openMenu()
    expect(items.length).toBeGreaterThan(0)
    expect(items.join('|')).not.toContain('论文样式')
    expect(items.join('|')).not.toContain('画布设置')
  })

  it('新手教程胶囊在时，菜单里不放教程项', async () => {
    await mount()
    expect(host.querySelector('[data-tutorial-chip]')).not.toBeNull()
    const items = await openMenu()
    expect(items.some((t) => t.includes('教程'))).toBe(false)
  })

  it('胶囊消失后，菜单里才有教程项', async () => {
    useOnboardingStore.setState({ status: 'not_started', welcomeSeenAt: 1, surface: 'none' } as never)
    await mount()
    expect(host.querySelector('[data-tutorial-chip]')).toBeNull()
    const items = await openMenu()
    expect(items.some((t) => t.includes('教程'))).toBe(true)
  })
})
