/**
 * 设置外壳（ADR 0038）。
 *
 * 钉四件事：① 十一个分区按固定顺序出现、当前项有 aria-current；② 旧分区 id
 * （profiles / canvas / sidebars / shortcuts）仍能深链到正确的新分区；③ 外框尺寸
 * 由 `SHELL_WIDTH` / `SHELL_HEIGHT` 固定，切分区不变；④ 键盘：↑ ↓ Home End 在导航里
 * 走并搬焦点；切分区内容区滚回顶部；从导出面板深链进来、关掉时回到导出面板。
 *
 * jsdom 没有布局引擎：「外框不跳」这里量的是 style 合同，真像素在
 * `e2e/settings-shell.spec.ts`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'
import {
  resolveSection,
  SECTIONS,
  SettingsDialog,
  SHELL_HEIGHT,
  SHELL_WIDTH,
} from '@/components/SettingsDialog'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}

const st = (key: string) => t(`settings.${key}`, { ns: 'dialogs' })

let root: Root
let host: HTMLDivElement

async function open(section: string | null = null) {
  useUiStore.setState({ settingsOpen: true, settingsSection: section })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<SettingsDialog />)
  })
  await act(async () => {})
}

const nav = () => document.querySelector('nav[aria-label]') as HTMLElement
const navButtons = () => [...nav().querySelectorAll('button')] as HTMLButtonElement[]
const current = () => navButtons().find((b) => b.getAttribute('aria-current') === 'true')
const dialog = () => document.querySelector('[role="dialog"]') as HTMLElement

beforeEach(() => {
  document.body.innerHTML = ''
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({ json: () => Promise.resolve({ checks: [] }), ok: true } as Response),
    ),
  )
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  vi.unstubAllGlobals()
  useUiStore.setState({ settingsOpen: false, settingsSection: null, settingsReturnTo: null, exportOpen: false })
  document.body.innerHTML = ''
})

describe('分区与深链', () => {
  it('十一个分区按固定顺序出现，默认落在「常规」', async () => {
    await open()
    expect(navButtons().map((b) => b.dataset.section)).toEqual(SECTIONS)
    expect(SECTIONS).toEqual([
      'general',
      'interface',
      'project',
      'style',
      'spec',
      'export',
      'ai',
      'packages',
      'diagnostics',
      'update',
      'about',
    ])
    expect(current()?.dataset.section).toBe('general')
    for (const id of SECTIONS) expect(navButtons().map((b) => b.textContent)).toContain(st(`section.${id}`))
  })

  it('旧分区 id 深链到正确的新分区；不认识的回到常规', () => {
    expect(resolveSection('profiles')).toBe('spec')
    expect(resolveSection('canvas')).toBe('interface')
    expect(resolveSection('sidebars')).toBe('interface')
    expect(resolveSection('shortcuts')).toBe('general')
    expect(resolveSection('ai')).toBe('ai')
    expect(resolveSection('nope')).toBeNull()
    expect(resolveSection(null)).toBeNull()
  })

  it('导出面板的「编辑」深链落在「规范」页，不是样式页', async () => {
    await open('profiles')
    expect(current()?.dataset.section).toBe('spec')
    expect(dialog().textContent).toContain(t('profiles.kind.specHint', { ns: 'dialogs' }))
    expect(dialog().textContent).not.toContain(t('profiles.kind.styleHint', { ns: 'dialogs' }))
  })

  it('「样式」与「规范」是两个分区，各自只有自己那类字段', async () => {
    await open('style')
    expect(dialog().textContent).toContain(t('profiles.kind.styleHint', { ns: 'dialogs' }))
    await act(async () => navButtons().find((b) => b.dataset.section === 'spec')!.click())
    expect(dialog().textContent).toContain(t('profiles.kind.specHint', { ns: 'dialogs' }))
    expect(dialog().textContent).not.toContain(t('profiles.kind.styleHint', { ns: 'dialogs' }))
  })
})

describe('尺寸与滚动合同', () => {
  it('外框宽高由常量固定，切分区不变', async () => {
    await open()
    const before = { w: dialog().style.width, h: dialog().style.height }
    expect(before).toEqual({ w: `${SHELL_WIDTH}px`, h: SHELL_HEIGHT })
    for (const id of ['packages', 'diagnostics', 'update', 'about', 'spec']) {
      await act(async () => navButtons().find((b) => b.dataset.section === id)!.click())
      expect({ w: dialog().style.width, h: dialog().style.height }).toEqual(before)
    }
  })

  it('内容区独立滚动：切分区后滚回顶部，导航不滚', async () => {
    await open('project')
    const content = dialog().querySelector('[data-settings-content]') as HTMLElement
    expect(content.className).toContain('overflow-y-auto') // jsdom 没有样式表，量 class 合同
    content.scrollTop = 120
    await act(async () => navButtons().find((b) => b.dataset.section === 'export')!.click())
    expect(content.scrollTop).toBe(0)
  })

  it('导航里的分区标签不换行、能横向滚（窄窗口时变成顶部一条）', async () => {
    await open()
    for (const b of navButtons()) expect(b.className).toContain('whitespace-nowrap')
    expect(nav().className).toContain('overflow-x-auto')
  })
})

describe('键盘', () => {
  it('↓ ↑ Home End 在导航里走，并把焦点搬到新的当前项', async () => {
    await open()
    const key = (k: string) =>
      act(async () => {
        nav().dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }))
      })
    // 事件从当前按钮冒泡到 nav；jsdom 里直接对 nav 发也走同一个处理器
    await key('ArrowDown')
    expect(current()?.dataset.section).toBe('interface')
    expect(document.activeElement).toBe(current())
    await key('End')
    expect(current()?.dataset.section).toBe('about')
    await key('ArrowDown')
    expect(current()?.dataset.section).toBe('general') // 循环
    await key('ArrowUp')
    expect(current()?.dataset.section).toBe('about')
    await key('Home')
    expect(current()?.dataset.section).toBe('general')
  })

  it('roving tabindex：只有当前项在 Tab 序里', async () => {
    await open('export')
    for (const b of navButtons()) {
      expect(b.tabIndex).toBe(b.dataset.section === 'export' ? 0 : -1)
    }
  })
})

describe('深链的返回', () => {
  it('从导出面板进来的，关掉设置回到导出面板', async () => {
    useUiStore.getState().setSettingsOpen(true, 'spec', { returnTo: 'export' })
    expect(useUiStore.getState().settingsReturnTo).toBe('export')
    useUiStore.getState().setSettingsOpen(false)
    expect(useUiStore.getState().exportOpen).toBe(true)
    expect(useUiStore.getState().settingsReturnTo).toBeNull()
  })

  it('普通打开再关掉，不会冒出导出面板', async () => {
    useUiStore.getState().setSettingsOpen(true, 'spec')
    useUiStore.getState().setSettingsOpen(false)
    expect(useUiStore.getState().exportOpen).toBe(false)
  })

  it('每次打开都重置返回目标：上一次的深链不会带到下一次', async () => {
    useUiStore.getState().setSettingsOpen(true, 'spec', { returnTo: 'export' })
    useUiStore.getState().setSettingsOpen(false)
    useUiStore.setState({ exportOpen: false })
    useUiStore.getState().setSettingsOpen(true)
    useUiStore.getState().setSettingsOpen(false)
    expect(useUiStore.getState().exportOpen).toBe(false)
  })
})
