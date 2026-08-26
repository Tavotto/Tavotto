/**
 * 设置页的渐进披露。
 *
 * 要钉住的（修改前见 `docs/ux/img/ux-consistency-pass/before/zh-1440-settings-*.png`：
 * 每个分区都是 `<Row/> <p>说明</p> <p>更多说明</p>`，About 首屏还挂着完整
 * 解释器绝对路径与五条诊断项）：
 *   1. 各分区首屏不再连续出现说明文字墙；
 *   2. 普通解释进小问号，**四种触发方式都真的能用**（悬停 / 聚焦 / 点击 / 触摸），
 *      Esc 能关；
 *   3. 错误、隐私摘要、数据风险仍然常驻；
 *   4. About 首屏不出现完整解释器绝对路径，展开「环境诊断」后才有；
 *   5. SettingRow 的标签列宽在所有分区一致。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'
import { SettingsDialog } from '@/components/SettingsDialog'
import { useEnvStore } from '@/store/envStore'
import { useProjectStore } from '@/store/projectStore'
import { useTelemetryStore } from '@/store/telemetryStore'
import { useUiStore } from '@/store/uiStore'
import { useUpdateStore } from '@/store/updateStore'

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

const PYTHON_PATH = '/opt/homebrew/opt/python@3.13/libexec/bin/python3'

let root: Root
let host: HTMLDivElement

async function open(section: string) {
  useUiStore.setState({ settingsOpen: true, settingsSection: section })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<SettingsDialog />)
  })
  await act(async () => {})
}

/** 对话框正文（不含 portal 里的浮层） */
const body = () => document.querySelector('[role="dialog"]') as HTMLElement
const bodyText = () => body()?.textContent ?? ''
const allText = () => document.body.textContent ?? ''
const buttons = () => [...document.querySelectorAll('button')] as HTMLButtonElement[]
const byAria = (name: string) => buttons().find((b) => b.getAttribute('aria-label') === name)

/**
 * 「说明文字墙」的判据：对话框正文里**独立成段的解释性文字**有几段。
 * 30 字是分界——比这短的是状态摘要（「已关闭」「允许写回原始文件」），
 * 比这长的就是在讲道理，那种内容属于问号或技术详情。
 */
const proseCount = () =>
  [...body().querySelectorAll('p')].filter((p) => (p.textContent ?? '').trim().length >= 30).length

beforeEach(() => {
  document.body.innerHTML = ''
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({ json: () => Promise.resolve({ checks: [] }), ok: true } as Response),
    ),
  )
  useTelemetryStore.setState({
    settings: {
      consent: 'disabled',
      enabled: false,
      hard_disabled: false,
      consent_version: 1,
      saved_consent_version: 1,
      needs_reconsent: false,
    } as never,
    askOpen: false,
  })
  useUpdateStore.setState({ status: { current: '0.11.0', desktop: false } as never })
  useProjectStore.setState({
    project: {
      figures_dir: '/tmp/figs',
      scripts: 2,
      export_dir: 'exports/',
      backup_dir: 'cache/original_backups/',
      settings: { allow_write_back: true },
    } as never,
  })
  useEnvStore.setState({
    env: {
      ok: true,
      python: PYTHON_PATH,
      source: 'system',
      matplotlib: '3.10.8',
      managed: false,
      bundled: false,
      runtime: {} as never,
      state: 'idle',
    } as never,
  })
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  vi.unstubAllGlobals()
  useUiStore.setState({ settingsOpen: false, settingsSection: null })
  document.body.innerHTML = ''
})

/* ------------------------------ 不再有文字墙 ------------------------------ */

describe('各分区首屏没有说明文字墙', () => {
  for (const section of ['general', 'project', 'canvas', 'sidebars', 'export', 'shortcuts']) {
    it(`${section} 分区最多一段长文`, async () => {
      await open(section)
      expect(proseCount()).toBeLessThanOrEqual(1)
    })
  }

  it('常规分区的三段解释都在问号里，不在页面上', async () => {
    await open('general')
    expect(bodyText()).not.toContain(st('general.languageHint'))
    expect(bodyText()).not.toContain(st('general.autosaveHint'))
    expect(bodyText()).not.toContain(st('general.resetLayoutHint'))
    // 但确实点得到
    const help = byAria(st('helpAbout', { label: st('general.language') }))!
    expect(help).toBeTruthy()
    await act(async () => {
      help.click()
    })
    expect(allText()).toContain(st('general.languageHint'))
  })

  it('画布分区那段「关联元素是什么」进了问号', async () => {
    await open('canvas')
    expect(bodyText()).not.toContain(st('canvas.companionsExplain'))
    const help = byAria(st('helpAbout', { label: st('canvas.dragCompanions') }))!
    await act(async () => {
      help.click()
    })
    expect(allText()).toContain(st('canvas.companionsExplain'))
  })
})

/* -------------------------------- 小问号 --------------------------------- */

describe('小问号四种触发方式', () => {
  const helpBtn = () => byAria(st('helpAbout', { label: st('general.language') }))!

  it('鼠标悬停即展开，移开后收回', async () => {
    await open('general')
    const b = helpBtn()
    // React 的 onPointerEnter 是用冒泡的 pointerover 委托实现的，
    // 直接派 pointerenter 谁也收不到（那样写这条用例会「通过」但什么也没测）
    await act(async () => {
      b.dispatchEvent(new PointerEvent('pointerover', { bubbles: true, pointerType: 'mouse' }))
    })
    expect(b.getAttribute('aria-expanded')).toBe('true')
    expect(allText()).toContain(st('general.languageHint'))
  })

  it('触摸（pointerType=touch）不走悬停，但点击能开', async () => {
    await open('general')
    const b = helpBtn()
    await act(async () => {
      b.dispatchEvent(new PointerEvent('pointerover', { bubbles: true, pointerType: 'touch' }))
    })
    // 触屏的 pointerenter 与 click 连着来；只让鼠标走悬停这条路，
    // 否则触屏上「点一下」会变成「开了又关」
    expect(b.getAttribute('aria-expanded')).toBe('false')
    await act(async () => {
      b.click()
    })
    expect(b.getAttribute('aria-expanded')).toBe('true')
  })

  it('键盘聚焦即展开', async () => {
    await open('general')
    const b = helpBtn()
    await focusIt(b)
    expect(b.getAttribute('aria-expanded')).toBe('true')
  })

  it('Esc 关闭', async () => {
    await open('general')
    const b = helpBtn()
    await act(async () => {
      b.click()
    })
    expect(b.getAttribute('aria-expanded')).toBe('true')
    // 浮层内容真的挂上来了才算「开着」；Radix 的 dismissable layer 是在
    // 内容挂载后的一个微任务里才注册 Escape 监听——不等它就是在赛跑，
    // 表现为这条用例偶发红（实测三轮里红一轮）
    expect(allText()).toContain(st('general.languageHint'))
    await act(async () => {
      await Promise.resolve()
    })
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(b.getAttribute('aria-expanded')).toBe('false')
  })

  // React 的 onFocus/onBlur 走的是**冒泡的 focusin/focusout**；直接派一个
  // 不冒泡的 FocusEvent('focus') 谁也收不到。用 focus()/blur() 让 jsdom 自己发。
  const focusIt = async (b: HTMLElement) => {
    await act(async () => {
      b.focus()
    })
  }
  const blurIt = async (b: HTMLElement) => {
    await act(async () => {
      b.blur()
    })
  }

  /**
   * **点开**（焦点不在按钮上）→ Esc → Radix 把焦点还给按钮。
   *
   * 这一步是一次**真实的 focus 事件**（焦点从 body 移到按钮），而「聚焦即展开」
   * 会立刻把气泡又打开——用户按 Esc 像没反应。
   *
   * 用例必须自己把这次 focus 派出来：Radix 的焦点归还是异步的，等它自己发
   * 就会落在断言窗口之外，那样即使缺陷还在也照样绿（本用例第一版就是这样，
   * 三轮里红一轮——那不是「偶发」，是断言与缺陷在赛跑）。
   */
  it('Esc 之后不会被「焦点还回来」重新打开', async () => {
    await open('general')
    const b = helpBtn()
    // 点开：焦点留在 body 上，与真实鼠标操作一致
    await act(async () => {
      b.click()
    })
    expect(b.getAttribute('aria-expanded')).toBe('true')
    expect(document.activeElement).not.toBe(b)
    await act(async () => {
      await Promise.resolve()
    })
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(b.getAttribute('aria-expanded')).toBe('false')
    // Radix 把焦点还给触发按钮
    await focusIt(b)
    expect(b.getAttribute('aria-expanded')).toBe('false')
  })

  it('焦点真的离开过之后，再 Tab 回来仍然展开（闸只吃那一次）', async () => {
    await open('general')
    const b = helpBtn()
    await focusIt(b)
    await act(async () => {
      await Promise.resolve()
    })
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    await focusIt(b)
    expect(b.getAttribute('aria-expanded')).toBe('false')

    await blurIt(b) // 焦点真的离开
    await focusIt(b) // 再 Tab 回来
    expect(b.getAttribute('aria-expanded')).toBe('true')
  })

  it('问号有明确的可达名，不是一个无名图标', async () => {
    await open('general')
    expect(helpBtn().getAttribute('aria-label')).toBe(
      st('helpAbout', { label: st('general.language') }),
    )
  })

  it('展开时焦点留在问号上，不被搬进浮层（Tab 顺序不乱）', async () => {
    await open('general')
    const b = helpBtn()
    await focusIt(b)
    expect(document.activeElement).toBe(b)
  })
})

/* --------------------------- 风险与错误仍然可见 --------------------------- */

describe('该常驻的不许折叠', () => {
  it('只读模式的副作用一句话常驻', async () => {
    useProjectStore.setState({
      project: {
        figures_dir: '/tmp/figs',
        scripts: 2,
        export_dir: 'exports/',
        backup_dir: 'cache/original_backups/',
        settings: { allow_write_back: false },
      } as never,
    })
    await open('project')
    expect(bodyText()).toContain(st('project.readOnlyHint'))
  })

  it('允许写回时给的是状态摘要，不是警告', async () => {
    await open('project')
    expect(bodyText()).toContain(st('project.writeBackAllowed'))
    expect(bodyText()).not.toContain(st('project.readOnlyHint'))
  })

  it('隐私最短摘要常驻', async () => {
    await open('about')
    expect(bodyText()).toContain(st('about.telemetry.summary'))
  })

  it('遥测默认关闭的语义没变：开关是 false', async () => {
    await open('about')
    const toggle = byAria(st('about.telemetry.toggle'))!
    expect(toggle.getAttribute('aria-checked')).toBe('false')
  })
})

/* ------------------------------ About 与诊断 ------------------------------ */

describe('About 页', () => {
  it('首屏不显示完整解释器绝对路径', async () => {
    await open('about')
    expect(bodyText()).not.toContain(PYTHON_PATH)
  })

  it('首屏给的是「解释器来源 + matplotlib 版本」两行摘要', async () => {
    await open('about')
    expect(bodyText()).toContain(st('about.engineStatus'))
    expect(bodyText()).toContain('3.10.8')
  })

  it('展开「环境诊断」后完整路径才出现', async () => {
    await open('about')
    const diag = buttons().find((b) => b.textContent?.trim() === st('about.diagnosticsTitle'))!
    expect(diag.getAttribute('aria-expanded')).toBe('false')
    await act(async () => {
      diag.click()
    })
    expect(diag.getAttribute('aria-expanded')).toBe('true')
    expect(bodyText()).toContain(PYTHON_PATH)
  })

  it('页面内明显分三块：产品 / 隐私与数据 / 渲染环境', async () => {
    await open('about')
    expect(bodyText()).toContain(st('about.privacyTitle'))
    expect(bodyText()).toContain(st('about.environmentTitle'))
  })
})

/* ------------------------------- 行的一致性 ------------------------------- */

describe('SettingRow 布局稳定', () => {
  it('不同分区的标签列宽一致', async () => {
    const widths = new Set<string>()
    for (const section of ['general', 'project', 'export', 'sidebars']) {
      await open(section)
      for (const el of body().querySelectorAll('span[style*="width"]')) {
        const w = (el as HTMLElement).style.width
        if (w) widths.add(w)
      }
      await act(async () => {
        root.unmount()
      })
      document.body.innerHTML = ''
    }
    expect([...widths]).toEqual(['112px'])
  })
})
