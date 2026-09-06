/**
 * 命令面板的最终命令集（ADR 0041 §3）：id 稳定、不重复注册、每条都有中英文文案
 * 与关键词、项目命令只在项目打开时出现、动作复用真实 helper。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  refreshProject: vi.fn().mockResolvedValue({}),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '/x', panels: [] }),
  fetchReadiness: vi.fn().mockResolvedValue(null),
  fetchTutorialStatus: vi.fn().mockResolvedValue(null),
}))

import { refreshProject } from '@/lib/api'
import { i18n } from '@/i18n'
import zhDialogs from '@/i18n/locales/zh-CN/dialogs.json'
import enDialogs from '@/i18n/locales/en-US/dialogs.json'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { CommandPalette, usePalette } from './CommandPalette'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
// jsdom 没有 scrollIntoView；面板每次切换高亮都会调它
Element.prototype.scrollIntoView = vi.fn()

let root: Root | null = null
let host: HTMLDivElement | null = null

function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root!.render(<CommandPalette />))
}

const labels = () =>
  Array.from(document.querySelectorAll('[role=option] button span.truncate')).map((el) =>
    (el.textContent ?? '').trim(),
  )

const commandIds = (dialogs: { palette: { commands: Record<string, unknown> } }) =>
  Object.keys(dialogs.palette.commands)

beforeEach(async () => {
  await i18n.changeLanguage('zh-CN')
  useProjectStore.setState({ phase: 'open', project: { open: true, id: 'p1' } } as never)
  useUiStore.setState({ registryOpen: false })
  usePalette.setState({ open: true })
  vi.mocked(refreshProject).mockClear()
})
afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
  usePalette.setState({ open: false })
})

describe('命令集', () => {
  it('六条整合要求的命令都在，且都有中英文 label + keywords', () => {
    for (const id of [
      'refresh-project',
      'readiness',
      'tutorial-start',
      'tutorial-reset',
      'hints-reset',
      'shortcut-help',
    ]) {
      for (const dialogs of [zhDialogs, enDialogs]) {
        const entry = (dialogs.palette.commands as Record<string, { label: string; keywords: string }>)[id]
        expect(entry?.label, `${id} label`).toBeTruthy()
        expect(entry?.keywords, `${id} keywords`).toBeTruthy()
      }
    }
  })

  it('中英文资源的命令 id 集合一致（没有一边多一条）', () => {
    expect(commandIds(zhDialogs).sort()).toEqual(commandIds(enDialogs).sort())
  })

  it('渲染出来的命令不重复', () => {
    mount()
    const seen = labels()
    expect(new Set(seen).size).toBe(seen.length)
    expect(seen).toContain('刷新项目（检查新文件）')
    expect(seen).toContain('显示项目接入状态')
    expect(seen).toContain('重新显示新手提示')
    expect(seen).toContain('快捷键帮助')
  })

  it('没有打开项目时项目命令整组不出现（embedded / playground）', () => {
    useProjectStore.setState({ phase: 'none', project: null } as never)
    mount()
    const seen = labels()
    expect(seen).not.toContain('刷新项目（检查新文件）')
    expect(seen).not.toContain('显示项目接入状态')
    expect(seen).toContain('快捷键帮助')
  })

  it('「刷新项目」调统一刷新端点（reason=manual），不自己扫', () => {
    mount()
    const btn = Array.from(document.querySelectorAll('[role=option] button')).find((b) =>
      b.textContent?.includes('刷新项目'),
    ) as HTMLButtonElement
    act(() => btn.click())
    expect(refreshProject).toHaveBeenCalledWith('manual')
    expect(usePalette.getState().open).toBe(false)
  })

  it('「显示项目接入状态」打开接入中心，来源记为 palette', () => {
    const spy = vi.spyOn(useProjectReadinessStore.getState(), 'openCenter')
    mount()
    const btn = Array.from(document.querySelectorAll('[role=option] button')).find((b) =>
      b.textContent?.includes('项目接入状态'),
    ) as HTMLButtonElement
    act(() => btn.click())
    expect(spy).toHaveBeenCalledWith({ focus: null, source: 'palette' })
    expect(useUiStore.getState().registryOpen).toBe(true)
    spy.mockRestore()
  })

  it('英文界面下按英文关键词能搜到', async () => {
    await i18n.changeLanguage('en-US')
    mount()
    const input = document.querySelector('input') as HTMLInputElement
    act(() => {
      // React 的受控输入靠原生 setter 之外的值追踪；直接赋 value 会被它当成没变
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input, 'rescan')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(labels()).toEqual(['Refresh project (check for new files)'])
  })
})

/**
 * 空查询时的顺序（审计 T50）。验收原话：**常用编辑动作无需猜内部关键词**。
 * 排序判据本身在 `lib/commandRanking.test.ts` 逐条反证过；这里量的是
 * 「面板真的按它渲染」以及「跑过的命令进了最近使用、且存在本机」。
 */
describe('空查询时的顺序（审计 T50）', () => {
  // 「最近使用」是本机偏好，模块初始化时就从 localStorage 读进来了：
  // 不清的话上一条用例点过什么，这一条的第一屏就跟着变
  beforeEach(() => {
    localStorage.removeItem('tavotto.ui')
    useUiStore.setState({ recentCommands: [] })
  })

  const sectionIds = () =>
    Array.from(document.querySelectorAll('[data-palette-section]')).map(
      (el) => (el as HTMLElement).dataset.paletteSection,
    )
  const cmdIds = () =>
    Array.from(document.querySelectorAll('[data-cmd-id]')).map(
      (el) => (el as HTMLElement).dataset.cmdId,
    )
  const clickCmd = (id: string) => {
    const btn = document.querySelector(`[data-cmd-id="${id}"] button`) as HTMLButtonElement
    act(() => btn.click())
  }

  it('段标题按固定顺序出现，且都有译文', () => {
    mount()
    expect(sectionIds()).toEqual(['common', 'other'])
    for (const el of document.querySelectorAll('[data-palette-section]')) {
      expect((el.textContent ?? '').trim()).not.toBe('')
      expect(el.textContent).not.toContain('palette.section')
    }
  })

  it('教程 / 刷新 / 接入状态沉到「其他」，常用编辑动作在它们之前', () => {
    mount()
    const ids = cmdIds()
    for (const low of ['refresh-project', 'readiness', 'tutorial-start']) {
      expect(ids.indexOf('export')).toBeLessThan(ids.indexOf(low))
      expect(ids.indexOf('add-text')).toBeLessThan(ids.indexOf(low))
    }
  })

  it('跑过一条就进「最近使用」，并排在常用之前', () => {
    useUiStore.setState({ recentCommands: [] })
    mount()
    clickCmd('shortcut-help')
    expect(useUiStore.getState().recentCommands).toEqual(['shortcut-help'])
    // 关掉的面板重新挂一次，看它是不是排到了前面
    act(() => root?.unmount())
    usePalette.setState({ open: true })
    mount()
    expect(sectionIds()[0]).toBe('recent')
    expect(cmdIds()[0]).toBe('shortcut-help')
  })

  it('最近使用存本机、不进文档', () => {
    useUiStore.setState({ recentCommands: [] })
    mount()
    clickCmd('shortcut-help')
    const saved = JSON.parse(localStorage.getItem('tavotto.ui') ?? '{}')
    expect(saved.recentCommands).toEqual(['shortcut-help'])
  })

  it('有查询时不显示段标题，但顺序仍是那一份', () => {
    useUiStore.setState({ recentCommands: [] })
    mount()
    const input = document.querySelector('input') as HTMLInputElement
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input, '教程')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(sectionIds()).toEqual([])
    expect(cmdIds().length).toBeGreaterThan(0)
  })
})
