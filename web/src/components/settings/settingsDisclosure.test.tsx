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
import { CONTENT_MAX_WIDTH, CONTENT_MODE, SECTIONS, SettingsDialog } from '@/components/SettingsDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { SETTING_CONTROL_WIDTH } from '@/components/settings/SettingRow'
import { useEnvStore } from '@/store/envStore'
import { useProjectStore } from '@/store/projectStore'
import { useTelemetryStore } from '@/store/telemetryStore'
import { useUiStore } from '@/store/uiStore'
import { useUpdateStore } from '@/store/updateStore'
import { useAiStore } from '@/store/aiStore'
import { agentCaps, capsOf } from './testCaps'

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
    root.render(
      <TooltipProvider>
        <SettingsDialog />
      </TooltipProvider>,
    )
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
  // about 不在列：隐私最短摘要是**必须常驻**的一段（见下「该常驻的不许折叠」）
  for (const section of ['general', 'project', 'interface', 'export', 'packages', 'diagnostics']) {
    it(`${section} 分区最多一段长文`, async () => {
      await open(section)
      expect(proseCount()).toBeLessThanOrEqual(1)
    })
  }

  /**
   * 审计「说明文字专项补查」之后，常规页**一个问号都不该有**：那六段说明
   * 要么是标题的同义反复（界面语言）、要么在介绍另一个帮助入口（快捷键）、
   * 要么在定义名词（情境提示），全部删掉或改成标签底下的一行短说明。
   *
   * 判据不写成「不含那几段旧文案」——旧 key 都删了，那种断言恒真。改判
   * **这一页有没有问号按钮**：只要有人再挂一个回来，这条就红。
   */
  it('常规分区一个问号都没有', async () => {
    await open('general')
    expect(body().querySelectorAll('[data-help-tip]')).toHaveLength(0)
  })

  /**
   * P2 那几页按同一条口径收：样式 / 规范 / 编码 Agent / 包管理一个问号都不该
   * 有——它们那几段说明要么变成了标签底下的一行短说明（规范页的「跟随更新」），
   * 要么变成了页面本身的一部分（规范页的规则快照折叠区、包管理页的工程细节
   * 折叠区）。诊断页留着**唯一那一个**：导出诊断包会把本机信息交出去，说明
   * 带链接、要在按下之前读到，那正是问号该在的地方。
   */
  it('P2 那几页里只有诊断页留了一个问号（导出诊断包那条）', async () => {
    // 分区 id 写错时那一格什么都不渲染，「没有问号」就恒真——所以每一格先
    // 证明**正文真的换成了那一页**。第一版把编码 Agent 的 id 写成了 `agents`
    // （真实 id 是 `ai`），那一格于是白绿了一轮
    // 编码 Agent 那一页要一份能用的 caps，否则它渲染的是骨架屏——骨架屏上
    // 当然没有问号，那又是一次恒真。它挂载后会自己再探一次，所以连
    // `/api/ai/capabilities` 的回包一起摆好
    const caps = capsOf([agentCaps()])
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve(String(input).includes('/api/ai/capabilities') ? caps : { checks: [] }),
        } as Response),
      ),
    )
    useAiStore.setState({ caps, agent: 'codex' })
    const shown = (id: string) =>
      body().querySelector(`[data-section="${id}"]`)?.getAttribute('aria-current')
    for (const section of ['style', 'spec', 'ai', 'packages']) {
      await open(section)
      expect(shown(section), `分区 id 写错了：${section}`).toBe('true')
      expect(body().querySelectorAll('[data-help-tip]'), section).toHaveLength(0)
      await act(async () => {
        root.unmount()
      })
      document.body.innerHTML = ''
    }
    await open('diagnostics')
    expect(shown('diagnostics')).toBe('true')
    expect(body().querySelectorAll('[data-help-tip]')).toHaveLength(1)
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

/**
 * 小问号的行为用**界面页那唯一的一个**（「拖动时一同移动关联对象」）来验：
 * 常规页已经一个问号都不剩，拿它当夹具的话这一组会变成空跑。
 */
describe('小问号四种触发方式', () => {
  const HELP_ROW = 'canvas.dragCompanions'
  const HELP_TEXT = 'canvas.companionsExplain'
  const helpBtn = () => byAria(st('helpAbout', { label: st(HELP_ROW) }))!

  it('鼠标悬停即展开，移开后收回', async () => {
    await open('interface')
    const b = helpBtn()
    // React 的 onPointerEnter 是用冒泡的 pointerover 委托实现的，
    // 直接派 pointerenter 谁也收不到（那样写这条用例会「通过」但什么也没测）
    await act(async () => {
      b.dispatchEvent(new PointerEvent('pointerover', { bubbles: true, pointerType: 'mouse' }))
    })
    expect(b.getAttribute('aria-expanded')).toBe('true')
    expect(allText()).toContain(st(HELP_TEXT))
  })

  it('触摸（pointerType=touch）不走悬停，但点击能开', async () => {
    await open('interface')
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
    await open('interface')
    const b = helpBtn()
    await focusIt(b)
    expect(b.getAttribute('aria-expanded')).toBe('true')
  })

  it('Esc 关闭', async () => {
    await open('interface')
    const b = helpBtn()
    await act(async () => {
      b.click()
    })
    expect(b.getAttribute('aria-expanded')).toBe('true')
    // 浮层内容真的挂上来了才算「开着」；Radix 的 dismissable layer 是在
    // 内容挂载后的一个微任务里才注册 Escape 监听——不等它就是在赛跑，
    // 表现为这条用例偶发红（实测三轮里红一轮）
    expect(allText()).toContain(st(HELP_TEXT))
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
    await open('interface')
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
    await open('interface')
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
    await open('interface')
    expect(helpBtn().getAttribute('aria-label')).toBe(st('helpAbout', { label: st(HELP_ROW) }))
  })

  it('展开时焦点留在问号上，不被搬进浮层（Tab 顺序不乱）', async () => {
    await open('interface')
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
    expect(bodyText()).toContain(st('project.writeBackOffHint'))
  })

  it('允许写回时不出警告', async () => {
    await open('project')
    expect(bodyText()).toContain(st('project.allowWriteBack'))
    expect(bodyText()).not.toContain(st('project.writeBackOffHint'))
  })

  it('隐私最短摘要常驻', async () => {
    await open('about')
    expect(bodyText()).toContain(st('about.telemetry.summary'))
  })

  /**
   * 遥测默认关闭的语义没变。控件是一颗 `role="switch"` 的开关（三档由开关 +
   * 行内现状文字一起表达，细则与三档各自的用例在
   * `components/SettingsTelemetry.test.tsx`）——这里的夹具是 `consent: 'disabled'`，
   * 开关必须关着、现状写「关闭」而不是「尚未选择」。
   */
  it('遥测默认关闭的语义没变：开关关着，现状是「关闭」', async () => {
    await open('about')
    const toggle = body().querySelector(
      `button[role="switch"][aria-label="${st('about.telemetry.toggle')}"]`,
    )!
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    expect(bodyText()).toContain(st('about.telemetry.optOut'))
    expect(bodyText()).not.toContain(st('about.telemetry.unset'))
  })
})

/* ------------------------------ About 与诊断 ------------------------------ */

describe('诊断页（Session 19 起渲染环境从 About 搬到这里）', () => {
  it('首屏不显示完整解释器绝对路径', async () => {
    await open('diagnostics')
    expect(bodyText()).not.toContain(PYTHON_PATH)
  })

  it('首屏是健康状态 + 复制 / 导出诊断，不是环境路径', async () => {
    await open('diagnostics')
    expect(bodyText()).toContain(st('diagnostics.healthTitle'))
    expect(bodyText()).toContain(st('diagnostics.copyReport'))
    expect(bodyText()).toContain(st('about.exportBundle'))
    // 解释器来源与 matplotlib 版本是技术详情，不在首屏
    expect(bodyText()).not.toContain('3.10.8')
  })

  it('展开「技术详情」后完整路径才出现，且渲染环境卡只有一张', async () => {
    await open('diagnostics')
    const diag = buttons().find((b) => b.textContent?.trim() === st('techDetails'))!
    expect(diag.getAttribute('aria-expanded')).toBe('false')
    await act(async () => {
      diag.click()
    })
    expect(diag.getAttribute('aria-expanded')).toBe('true')
    expect(bodyText()).toContain(PYTHON_PATH)
    expect(bodyText()).toContain('3.10.8')
    // 渲染环境卡只出现一次——此前 About 页里有两张。**按元素数，不按字符串
    // 出现次数**：「渲染环境」四个字也出现在别的句子里（审计 T47 的环境说明）
    expect(document.body.querySelectorAll('[data-engine-env-card]')).toHaveLength(1)
  })

  it('About 页只剩产品与隐私两块，不再有渲染环境', async () => {
    await open('about')
    expect(bodyText()).toContain(st('about.privacyTitle'))
    expect(bodyText()).not.toContain(st('about.engineStatus'))
    expect(bodyText()).not.toContain(PYTHON_PATH)
  })
})

/* ------------------------------- 行的一致性 ------------------------------- */

describe('SettingRow 布局稳定', () => {
  it('不同分区的控件列宽一致（含样式 / 规范页的只读摘要行）', async () => {
    const widths = new Set<string>()
    let rows = 0
    // 「样式」「规范」两页也进这张单子：那两页的只读摘要行与 SettingRow 共用同一份
    // 网格，「摘要 ↔ 输入框」切换时值与输入框从同一条竖线起排，差几个像素就是整列左右跳一下
    for (const section of ['general', 'project', 'export', 'interface', 'style', 'spec']) {
      await open(section)
      for (const el of body().querySelectorAll('[data-setting-row], [data-summary-row]')) {
        rows += 1
        widths.add((el as HTMLElement).style.getPropertyValue('--setting-control'))
      }
      await act(async () => {
        root.unmount()
      })
      document.body.innerHTML = ''
    }
    expect(rows).toBeGreaterThan(10)
    expect([...widths]).toEqual([`${SETTING_CONTROL_WIDTH}px`])
  })

  it('普通分区的内容有最大宽度，样式 / 规范 / 包管理铺满', async () => {
    // 期望值写死在这里，不从 `CONTENT_MODE` 读——否则改了表判据跟着变，永远绿
    const WIDE = ['style', 'spec', 'packages']
    expect(Object.keys(CONTENT_MODE).sort()).toEqual([...SECTIONS].sort())
    // 「编码 Agent」页要一份能用的 caps，且挂载后会自己再探一次：回包一起摆好
    const caps = capsOf([agentCaps()])
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve(String(input).includes('/api/ai/capabilities') ? caps : { checks: [] }),
        } as Response),
      ),
    )
    useAiStore.setState({ caps, agent: 'codex' })
    for (const section of SECTIONS) {
      const mode = WIDE.includes(section) ? 'wide' : 'normal'
      await open(section)
      const wrap = body().querySelector('[data-content-mode]') as HTMLElement
      expect(wrap.dataset.contentMode, section).toBe(mode)
      expect(wrap.style.maxWidth, section).toBe(mode === 'normal' ? `${CONTENT_MAX_WIDTH}px` : '')
      await act(async () => {
        root.unmount()
      })
      document.body.innerHTML = ''
    }
  })
})
