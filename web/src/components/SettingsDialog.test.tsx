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

import { setLocale, t } from '@/i18n'
import {
  resolveSection,
  SECTIONS,
  SettingsDialog,
  SHELL_HEIGHT,
  SHELL_WIDTH,
} from '@/components/SettingsDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { anchorsOf, SETTINGS_REGISTRY } from '@/components/settings/settingsRegistry'
import { agentCaps, capsOf } from '@/components/settings/testCaps'
import { useProjectStore } from '@/store/projectStore'
import { dialogCovered, useUiStore } from '@/store/uiStore'

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
    root.render(
      <TooltipProvider>
        <SettingsDialog />
      </TooltipProvider>,
    )
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
  useUiStore.setState({ settingsOpen: false, settingsSection: null, exportOpen: false, dialogStack: [] })
  document.body.innerHTML = ''
})

describe('分区与深链', () => {
  it('九个分区按固定顺序出现，默认落在「通用」', async () => {
    await open()
    expect(navButtons().map((b) => b.dataset.section)).toEqual(SECTIONS)
    expect(SECTIONS).toEqual([
      'general',
      'project',
      'style',
      'spec',
      'export',
      'ai',
      'packages',
      'diagnostics',
      'about',
    ])
    expect(current()?.dataset.section).toBe('general')
    for (const id of SECTIONS) expect(navButtons().map((b) => b.textContent)).toContain(st(`section.${id}`))
  })

  it('旧分区 id 深链到正确的新分区；不认识的回到通用', () => {
    expect(resolveSection('profiles')).toBe('spec')
    // 十一页并九页：被并掉的两页 id 仍认得，落在并入的那一页
    expect(resolveSection('interface')).toBe('general')
    expect(resolveSection('canvas')).toBe('general')
    expect(resolveSection('sidebars')).toBe('general')
    expect(resolveSection('shortcuts')).toBe('general')
    expect(resolveSection('update')).toBe('about')
    expect(resolveSection('ai')).toBe('ai')
    expect(resolveSection('nope')).toBeNull()
    expect(resolveSection(null)).toBeNull()
  })

  it('外部请求落在当前页也被消费：切走后同一菜单命令再来一次仍然生效（Codex #821 P2）', async () => {
    await open()
    await act(async () => navButtons().find((b) => b.dataset.section === 'about')!.click())
    // 已经在「关于」时菜单发「检查更新」：无事可做，但请求要消费掉
    await act(async () => useUiStore.getState().setSettingsOpen(true, 'about'))
    await act(async () => {})
    expect(current()?.dataset.section).toBe('about')
    expect(useUiStore.getState().settingsSection, '已在目标页的请求也要消费').toBeNull()
    await act(async () => navButtons().find((b) => b.dataset.section === 'general')!.click())
    expect(current()?.dataset.section).toBe('general')
    await act(async () => useUiStore.getState().setSettingsOpen(true, 'about'))
    await act(async () => {})
    expect(current()?.dataset.section).toBe('about')
  })

  it('导出面板的「编辑」深链落在「规范」页，不是样式页', async () => {
    await open('profiles')
    expect(current()?.dataset.section).toBe('spec')
  })

  it('「样式」与「规范」是两个分区，各自只有自己那类字段', async () => {
    await open('style')
    expect(current()?.dataset.section).toBe('style')
    await act(async () => navButtons().find((b) => b.dataset.section === 'spec')!.click())
    expect(current()?.dataset.section).toBe('spec')
  })
})

describe('尺寸与滚动合同', () => {
  it('外框宽高由常量固定，切分区不变', async () => {
    await open()
    const before = { w: dialog().style.width, h: dialog().style.height }
    expect(before).toEqual({ w: `${SHELL_WIDTH}px`, h: SHELL_HEIGHT })
    for (const id of ['packages', 'diagnostics', 'about', 'spec']) {
      await act(async () => navButtons().find((b) => b.dataset.section === id)!.click())
      expect({ w: dialog().style.width, h: dialog().style.height }).toEqual(before)
    }
  })

  it('内容区独立滚动：切分区后滚回顶部，导航不滚', async () => {
    await open('project')
    const content = dialog().querySelector('[data-settings-content]') as HTMLElement
    // jsdom 只量常驻滚动轨道的合同；异步回包前后的像素位置由 e2e/perf-probe.spec.ts 验。
    expect(content.className).toContain('overflow-y-scroll')
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
    expect(current()?.dataset.section).toBe('project')
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

describe('深链的返回：主对话框栈（审计 T35）', () => {
  it('从导出面板进来的：导出面板**没关**、只是被盖住；关掉设置它自己回来', async () => {
    useUiStore.getState().setExportOpen(true)
    useUiStore.getState().setSettingsOpen(true, 'spec')
    const s = useUiStore.getState()
    expect(s.dialogStack).toEqual(['export', 'settings'])
    expect(s.exportOpen, '深链不许先关导出面板——那样用户填过的东西全丢').toBe(true)
    expect(dialogCovered(s.dialogStack, 'export')).toBe(true)
    expect(dialogCovered(s.dialogStack, 'settings')).toBe(false)
    useUiStore.getState().setSettingsOpen(false)
    const after = useUiStore.getState()
    expect(after.dialogStack).toEqual(['export'])
    expect(after.exportOpen).toBe(true)
    expect(dialogCovered(after.dialogStack, 'export')).toBe(false)
  })

  it('普通打开再关掉，不会冒出导出面板', async () => {
    useUiStore.getState().setSettingsOpen(true, 'spec')
    useUiStore.getState().setSettingsOpen(false)
    expect(useUiStore.getState().exportOpen).toBe(false)
    expect(useUiStore.getState().dialogStack).toEqual([])
  })

  it('设置上面再压一层论文样式：只有栈顶不被盖住，Esc 一层层退', async () => {
    useUiStore.getState().setExportOpen(true)
    useUiStore.getState().setSettingsOpen(true, 'style')
    useUiStore.getState().setStylesOpen(true, { presetId: 's1' })
    let s = useUiStore.getState()
    expect(s.dialogStack).toEqual(['export', 'settings', 'styles'])
    expect(s.stylesPresetId).toBe('s1')
    expect(dialogCovered(s.dialogStack, 'settings')).toBe(true)
    expect(dialogCovered(s.dialogStack, 'styles')).toBe(false)
    useUiStore.getState().setStylesOpen(false)
    s = useUiStore.getState()
    expect(s.dialogStack).toEqual(['export', 'settings'])
    expect(s.stylesPresetId, '预选是打开那一刻的意图，关掉就清').toBeNull()
    expect(s.settingsOpen).toBe(true)
    expect(dialogCovered(s.dialogStack, 'settings')).toBe(false)
  })

  it('直接 setState 打开（没入栈）的照常显示：判「被盖住」只看栈', async () => {
    useUiStore.setState({ settingsOpen: true })
    expect(dialogCovered(useUiStore.getState().dialogStack, 'settings')).toBe(false)
  })
})

describe('页头、导航与内容列（2026-10-07 设计审计 §9.1）', () => {
  it('每页一个页头：type-heading 的页名 + 一句说明；内容列 680 居中', async () => {
    await open('export')
    const header = document.querySelector('[data-settings-page-header]')!
    const h2 = header.querySelector('h2')!
    expect(h2.className).toContain('type-heading')
    expect(h2.textContent).toBe(st('section.export'))
    expect(header.querySelector('p')!.textContent).toBe(st('pageDesc.export'))
    const wrap = document.querySelector('[data-content-mode]') as HTMLElement
    expect(wrap.style.maxWidth).toBe('680px')
    expect(wrap.className).toMatch(/\bmx-auto\b/)
  })

  it('导航项 30px / 13px / 8 圆角，选中 600；组名 12 / 500 / ink-3', async () => {
    await open('project')
    const item = current()!
    expect(item.className).toMatch(/\bh-7\.5\b/)
    expect(item.className).toMatch(/\btext-base\b/)
    expect(item.className).toMatch(/\brounded-md\b/)
    expect(item.className).toMatch(/\bfont-semibold\b/)
    const other = navButtons().find((b) => b.dataset.section === 'general')!
    expect(other.className).not.toMatch(/\bfont-semibold\b/)
    const groupLabel = nav().querySelector('[data-nav-group="general"] > span')!
    expect(groupLabel.className).toMatch(/\btext-sm\b/)
    expect(groupLabel.className).toMatch(/\bfont-medium\b/)
    expect(groupLabel.className).toMatch(/\btext-ink-3\b/)
  })
})

describe('搜索（2026-10-07 设计审计 §9.1，settingsRegistry）', () => {
  const search = () => document.querySelector<HTMLInputElement>('[data-settings-search]')!
  async function type(value: string) {
    await act(async () => {
      const el = search()
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(el, value)
      el.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  const results = () =>
    [...document.querySelectorAll<HTMLElement>('[data-settings-result]')].map((r) => r.dataset.settingsResult)

  it('按行名过滤：只剩命中的分区与那一行，分组隐去', async () => {
    await open()
    expect(search().getAttribute('type')).toBe('search')
    await type(t('export.ppiLabel', { ns: 'dialogs' }))
    expect(results()).toContain('export.ppi')
    expect(nav().querySelectorAll('[data-nav-group]')).toHaveLength(0)
    expect(navButtons().filter((b) => b.dataset.section).map((b) => b.dataset.section)).toContain('export')
    expect(navButtons().some((b) => b.dataset.section === 'about')).toBe(false)
  })

  it('也认关键词（标签里没有的词）：dpi → 分辨率那一行', async () => {
    await open()
    await type('dpi')
    expect(results()).toContain('export.ppi')
  })

  it('分区名命中时只列分区本身，不把那一页的每一行都摊出来', async () => {
    await open()
    await type(st('section.packages'))
    expect(navButtons().some((b) => b.dataset.section === 'packages')).toBe(true)
    expect(results().filter((r) => r!.startsWith('packages.'))).toHaveLength(0)
  })

  it('点一个结果：切到那一页，那一行被标出来', async () => {
    await open('general')
    await type(st('project.allowWriteBack'))
    const hit = document.querySelector<HTMLButtonElement>('[data-settings-result="project.writeBack"]')!
    expect(hit).toBeTruthy()
    await act(async () => hit.click())
    await act(async () => {})
    expect(current()?.dataset.section).toBe('project')
    const row = document.querySelector('[data-settings-anchor="project.writeBack"]')!
    expect(row.hasAttribute('data-settings-hit')).toBe(true)
  })

  it('命中高亮是一下：过一会儿自己消掉，不永久挂在那一行（Codex #828 P2）', async () => {
    await open('general')
    await type(st('project.allowWriteBack'))
    await act(async () =>
      document.querySelector<HTMLButtonElement>('[data-settings-result="project.writeBack"]')!.click(),
    )
    await act(async () => {})
    const row = document.querySelector('[data-settings-anchor="project.writeBack"]')!
    expect(row.hasAttribute('data-settings-hit')).toBe(true)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1800))
    })
    expect(row.hasAttribute('data-settings-hit')).toBe(false)
  })

  it('窄到搜索框藏起来时不再按搜索词过滤：导航完整回来；宽回来接着搜（Codex #828 P2）', async () => {
    const listeners: (() => void)[] = []
    let wide = true
    vi.stubGlobal('matchMedia', (q: string) => ({
      get matches() {
        return q.includes('40rem') ? wide : false
      },
      media: q,
      addEventListener: (_: string, cb: () => void) => listeners.push(cb),
      removeEventListener: () => {},
    }))
    await open()
    await type('zzzz-nothing')
    expect(document.querySelector('[data-settings-no-results]')).toBeTruthy()
    wide = false
    await act(async () => listeners.forEach((cb) => cb()))
    expect(document.querySelector('[data-settings-no-results]')).toBeNull()
    expect(nav().querySelectorAll('[data-nav-group]')).toHaveLength(4)
    wide = true
    await act(async () => listeners.forEach((cb) => cb()))
    expect(document.querySelector('[data-settings-no-results]')).toBeTruthy()
  })

  it('没有命中时说一句；清空就回到完整导航', async () => {
    await open()
    await type('zzzz-nothing')
    expect(document.querySelector('[data-settings-no-results]')).toBeTruthy()
    await type('')
    expect(nav().querySelectorAll('[data-nav-group]')).toHaveLength(4)
  })

  it('换界面语言后按新语言重算命中，不沿用旧语言的结果（Codex #828 P2）', async () => {
    await open()
    // 「语言」在中文下命中语言那一行；换到英文后中文词不再命中
    await type('语言')
    expect(results()).toContain('general.language')
    await act(async () => {
      await setLocale('en-US')
    })
    try {
      expect(results()).not.toContain('general.language')
    } finally {
      await act(async () => {
        await setLocale('zh-CN')
      })
    }
  })

  it('打字只在本地过滤，一个请求都不发', async () => {
    await open()
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>
    const before = fetchMock.mock.calls.length
    await type('python')
    await type('pip')
    expect(fetchMock.mock.calls.length).toBe(before)
  })

  it('钻入页（改图助手 › 某 Agent）挂着时点同一分区的结果：先退回列表再落地高亮（Codex #828 P2）', async () => {
    const caps = capsOf([agentCaps()])
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve(String(input).includes('/api/ai/capabilities') ? caps : { checks: [] }),
        } as Response),
      ),
    )
    await open('ai')
    await act(async () => {})
    await act(async () => document.querySelector<HTMLButtonElement>('[data-agent-open]')!.click())
    expect(document.querySelector('[data-settings-anchor="ai.default"]'), '进了详情，列表那几行不在').toBeNull()
    const label = SETTINGS_REGISTRY.find((e) => e.id === 'ai.default')!.label()
    await type(label)
    await act(async () => document.querySelector<HTMLButtonElement>('[data-settings-result="ai.default"]')!.click())
    await act(async () => {})
    const row = document.querySelector('[data-settings-anchor="ai.default"]')
    expect(row, '退回了列表').toBeTruthy()
    expect(row!.hasAttribute('data-settings-hit')).toBe(true)
  })

  it('钻入页挂着时点同一分区的分区级结果（没有锚点）：同样退回列表（Codex #828 P2）', async () => {
    const caps = capsOf([agentCaps()])
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve(String(input).includes('/api/ai/capabilities') ? caps : { checks: [] }),
        } as Response),
      ),
    )
    await open('ai')
    await act(async () => {})
    await act(async () => document.querySelector<HTMLButtonElement>('[data-agent-open]')!.click())
    expect(document.querySelector('[data-agent-detail]'), '进了详情').toBeTruthy()
    await type(st('section.ai'))
    const sectionHit = document.querySelector<HTMLButtonElement>('[data-settings-results] [data-section="ai"]')
    expect(sectionHit, '分区名本身是一条结果').toBeTruthy()
    await act(async () => sectionHit!.click())
    await act(async () => {})
    expect(document.querySelector('[data-agent-detail]'), '离开了详情').toBeNull()
    expect(document.querySelector('[data-settings-anchor="ai.default"]'), '回到了列表').toBeTruthy()
  })

  it('钻入页挂着时方向键落回当前分区（只剩一项可走）：不当作「去这一页」、不退出详情', async () => {
    const caps = capsOf([agentCaps()])
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve(String(input).includes('/api/ai/capabilities') ? caps : { checks: [] }),
        } as Response),
      ),
    )
    await open('ai')
    await act(async () => {})
    await act(async () => document.querySelector<HTMLButtonElement>('[data-agent-open]')!.click())
    // 搜索只剩当前分区一项时，任何方向键都落回它自己
    await type(st('section.ai'))
    expect(document.querySelectorAll('[data-settings-results] [data-section]').length, '前提：只剩一项').toBe(1)
    const only = document.querySelector<HTMLButtonElement>('[data-settings-results] [data-section="ai"]')!
    await act(async () => {
      only.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    })
    await act(async () => {})
    expect(document.querySelector('[data-agent-detail]'), '方向键不是点击：详情还在').toBeTruthy()
  })

  /** 注册表里每一条都在它那一页上找得到锚点：登记了、页面却没挂 `data-settings-anchor` 时，点结果什么都不发生 */
  it('注册表里的每一条在页面上都有锚点', async () => {
    const bySection = new Map<string, string[]>()
    // 每一条认它的落点链：只在某些状态下才渲染的行退到一定在场的那一组上（anchorsOf）
    const fallback = (e: (typeof SETTINGS_REGISTRY)[number]) => anchorsOf(e).at(-1)!
    for (const e of SETTINGS_REGISTRY) bySection.set(e.section, [...(bySection.get(e.section) ?? []), fallback(e)])
    useProjectStore.setState({ project: { figures_dir: '/p/figs', scripts: 1, settings: {}, export_dir: '/p/exp', backup_dir: '/p/bak' } as never })
    // 「改图助手」页挂载后会自己探一次：给一份像样的回包（只有 checks 的通用桩会被当成没有 agents 的能力表）
    const caps = capsOf([agentCaps()])
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve(String(input).includes('/api/ai/capabilities') ? caps : { checks: [] }),
        } as Response),
      ),
    )
    for (const [section, ids] of bySection) {
      await open(section)
      for (const id of ids) {
        expect(document.querySelector(`[data-settings-anchor="${id}"]`), `${section}: ${id}`).toBeTruthy()
      }
      await act(async () => root.unmount())
      document.body.innerHTML = ''
      useUiStore.setState({ settingsOpen: false, settingsSection: null })
    }
  })
})
