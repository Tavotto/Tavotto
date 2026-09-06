/**
 * 设置页的「说明文字专项补查」（UI 审计 T38 / T39 / T40 / T43 + 18 条说明弹层）。
 *
 * 这一组钉的是**措辞与控件的对应关系**，不是像素：
 *   1. 常规页不再用问号解释自己（`settingsDisclosure.test.tsx` 判「一个都没有」，
 *      这里判剩下的那几行说的是动作与结果）；
 *   2. 侧栏开关用结果式名称、标签指着开关、限制只在**真的受限时**出现；
 *   3. 路径可核实：默认末级目录、能展开成完整路径、默认值由控件表达；
 *   4. 「允许写回原始文件」这个反转必须与**真实保护**同步——设置页的开关与
 *      属性栏那个会碰磁盘的按钮读同一个字段（这条是 T40 的验收原文：
 *      「不能只改文案」）；
 *   5. 导出偏好与导出对话框是同一批名字、同一个单位。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'
import { SettingsDialog } from '@/components/SettingsDialog'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { useOnboardingStore } from '@/store/onboardingStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
Element.prototype.hasPointerCapture ??= () => false
Element.prototype.releasePointerCapture ??= () => {}
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as never

const st = (key: string, values?: Record<string, unknown>) =>
  t(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

const FIGURES = '/Users/me/Library/Application Support/Tavotto/tutorial/v1-a42973/Tutorial'
const EXPORTS = '/Users/me/Library/Application Support/Tavotto/tutorial/v1-a42973/exports'
const BACKUPS = '/Users/me/Library/Application Support/Tavotto/cache/original_backups'

let root: Root
let host: HTMLDivElement

async function render(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(node)
  })
  await act(async () => {})
}

async function open(section: string) {
  useUiStore.setState({ settingsOpen: true, settingsSection: section })
  await render(<SettingsDialog />)
}

const body = () => document.querySelector('[role="dialog"]') as HTMLElement
const bodyText = () => body()?.textContent ?? ''
const buttons = () => [...document.querySelectorAll('button')] as HTMLButtonElement[]
const byText = (s: string) => buttons().find((b) => b.textContent?.trim() === s)

function project(patch: Record<string, unknown> = {}) {
  useProjectStore.setState({
    project: {
      figures_dir: FIGURES,
      scripts: 2,
      export_dir: EXPORTS,
      backup_dir: BACKUPS,
      settings: { allow_write_back: true },
      ...patch,
    } as never,
  })
}

beforeEach(() => {
  document.body.innerHTML = ''
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            checks: [],
            settings: { allow_write_back: true },
            export_dir: EXPORTS,
            backup_dir: BACKUPS,
          }),
      } as Response),
    ),
  )
  project()
  useUiStore.setState({ layout: 'wide', leftPinned: false, rightPinned: true })
  useOnboardingStore.setState({ status: 'not_started', tutorialProjectId: null } as never)
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  useUiStore.setState({ settingsOpen: false, settingsSection: null })
  localStorage.removeItem('tavotto.export.defaults')
  document.body.innerHTML = ''
})

/* --------------------------------- T38 常规 -------------------------------- */

describe('T38 常规：说明改成动作与结果', () => {
  it('快捷键那一行直接给出键位，不用一段话介绍另一个帮助入口', async () => {
    await open('general')
    expect(bodyText()).toContain(st('shortcuts.label'))
    const kbd = body().querySelector('kbd')
    expect(kbd?.textContent?.trim()).toBe('?')
  })

  it('有教程项目时「重置」单独一行，标签底下写清重置的是哪个对象', async () => {
    useOnboardingStore.setState({ status: 'completed', tutorialProjectId: 'p1' } as never)
    await open('general')
    // 进入教程与重置是两件事，各自一行：改动前它们挤在同一行，
    // 「再看一遍教程」与「重置教程项目」的区别得点开问号才知道
    expect(byText(st('tutorial.restart'))).toBeTruthy()
    expect(bodyText()).toContain(st('tutorial.reset'))
    expect(bodyText()).toContain(st('tutorial.resetScope'))
  })

  it('没有教程项目时不出现重置行', async () => {
    await open('general')
    expect(bodyText()).not.toContain(st('tutorial.resetScope'))
  })

  it('提示按钮说的是点了会怎样', async () => {
    await open('general')
    expect(byText(st('tutorial.resetHints'))?.textContent).toBe('重新显示操作提示')
  })
})

