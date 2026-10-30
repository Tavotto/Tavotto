/**
 * 顶栏「新手教程」状态胶囊（2026-10-30，ADR 0136 修订）：新手态且没做过选择才出现；
 * 点它进教程（记下选择）、点 × 关掉（记下选择），之后不再出现。认 `data-tutorial-chip*` 锚点。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import '@/i18n'
import { configureOnboardingPersistence, useOnboardingStore } from '@/store/onboardingStore'
import { TutorialChip } from './TutorialChip'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement

const ob = () => useOnboardingStore.getState()
const chip = () => host.querySelector<HTMLElement>('[data-tutorial-chip]')
const mount = () => act(async () => root.render(<TutorialChip />))
const click = (sel: string) =>
  act(async () => {
    host.querySelector<HTMLElement>(sel)!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })

beforeEach(() => {
  configureOnboardingPersistence(null)
  ob().resetOnboarding()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
})

describe('TutorialChip', () => {
  it('新手态且没做过选择：画出一颗胶囊，文字走 i18n（只有「新手教程」，没有说明文字）', async () => {
    await mount()
    expect(chip()).not.toBeNull()
    expect(chip()!.textContent).toBe('新手教程')
    expect(host.querySelector('[data-tutorial-chip-dismiss]')).not.toBeNull()
  })

  it('点胶囊：进教程并记下选择，胶囊不再出现', async () => {
    await mount()
    await click('[data-tutorial-chip-open]')
    expect(ob().status).toBe('active')
    expect(ob().surface).toBe('tutorial')
    expect(ob().welcomeSeenAt).toBeTypeOf('number')
    expect(chip()).toBeNull()
  })

  it('点 ×：只关胶囊并记下选择，不进教程', async () => {
    await mount()
    await click('[data-tutorial-chip-dismiss]')
    expect(ob().status).toBe('not_started')
    expect(ob().surface).toBe('none')
    expect(ob().welcomeSeenAt).toBeTypeOf('number')
    expect(chip()).toBeNull()
  })

  it('已选择过 / 不是新手态 / 教程层开着：不画', async () => {
    useOnboardingStore.setState({ welcomeSeenAt: 5 })
    await mount()
    expect(chip()).toBeNull()
    for (const status of ['active', 'paused', 'completed', 'skipped'] as const) {
      await act(async () => useOnboardingStore.setState({ welcomeSeenAt: null, status }))
      expect(chip(), status).toBeNull()
    }
    await act(async () => useOnboardingStore.setState({ status: 'not_started', surface: 'welcome' }))
    expect(chip()).toBeNull()
  })
})
