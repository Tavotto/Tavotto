/**
 * 论文样式对话框（审计 T35）。盯三件事：
 *
 * 1. 从设置带来的样式**预选**；没带而草稿为空时选第一条已存样式——「空样式」与
 *    刚才点的那条是什么关系，不该让用户猜；
 * 2. 这里只编辑样式、不应用（ADR 0081：应用 = 画布绑定，在左栏样式面板）；
 * 3. 它压在设置之上：设置被盖住但没关，关掉样式就回到设置。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { SettingsDialog } from '@/components/SettingsDialog'
import { StyleDialog } from '@/components/StyleDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useProfileStore } from '@/store/profileStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const envelope = (over: Record<string, unknown>) => ({
  kind: 'style',
  schema_version: 1,
  revision: 1,
  name_key: '',
  version: '',
  created_at: 0,
  updated_at: 0,
  built_in: false,
  read_only: false,
  is_default: false,
  derived_from: '',
  warnings: [],
  data: {},
  ...over,
})

const BUILTIN = envelope({
  id: 'builtin-default-style',
  display_name: '默认样式',
  name_key: 'builtin.style.default',
  built_in: true,
  read_only: true,
  is_default: true,
  data: { element: { line: { linewidth: 0.5 } } },
})
const USER = envelope({
  id: 's1',
  display_name: '投稿用',
  derived_from: 'builtin-default-style',
  data: {
    element: {
      // 故意打乱角色顺序：界面上按对象类别归组，不按存盘顺序
      line: { linewidth: 1.25 },
      axis_label: { fontfamily: 'Times New Roman', fontsize: 9 },
      ticks: { direction: 'in' },
      title: { weight: 'bold' },
    },
  },
})

let container: HTMLDivElement
let root: Root
const text = () => document.body.textContent ?? ''
const nameInput = () =>
  document.body.querySelector<HTMLInputElement>('input[placeholder^="样式名称"]')

async function mount(withSettings = false) {
  await act(async () => {
    root.render(
      <TooltipProvider>
        {withSettings && <SettingsDialog />}
        <StyleDialog />
      </TooltipProvider>,
    )
  })
  await act(async () => {})
}

beforeEach(async () => {
  globalThis.fetch = vi.fn(
    async (input: RequestInfo | URL) =>
      new Response(
        JSON.stringify({ profiles: String(input).includes('/style') ? [BUILTIN, USER] : [] }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
  ) as typeof fetch
  useProfileStore.setState({ styles: [], loaded: false, error: null, conflict: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_style')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.objects = []
  })
  useUiStore.setState({ stylesOpen: false, stylesPresetId: null, settingsOpen: false, dialogStack: [] })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  useUiStore.setState({ stylesOpen: false, stylesPresetId: null, settingsOpen: false, dialogStack: [] })
  vi.restoreAllMocks()
})

describe('预选', () => {
  it('从设置带来的那一条直接选中', async () => {
    await mount()
    await act(async () => {
      useUiStore.getState().setStylesOpen(true, { presetId: 's1' })
    })
    await act(async () => {})
    expect(nameInput()?.value).toBe('投稿用')
  })

  it('没带预选、草稿是空的：选第一条已存样式，不摆一份空样式', async () => {
    await mount()
    await act(async () => {
      useUiStore.getState().setStylesOpen(true)
    })
    await act(async () => {})
    expect(nameInput()?.value).toBe('默认样式')
    expect(text()).not.toContain('空样式')
  })

  it('用户点了「新建样式」之后不再替他选回去', async () => {
    await mount()
    await act(async () => {
      useUiStore.getState().setStylesOpen(true)
    })
    await act(async () => {})
    // 「新建样式」自 2026-09-15 全面打磨 D23 起收进库行行尾的 ⋯ 菜单里（与设置 › 样式页同形）。
    // Radix 的 DropdownMenu 开在 pointerdown 上，jsdom 里 `.click()` 打不开它
    const more = [...document.body.querySelectorAll('button')].find(
      (b) => b.getAttribute('aria-label') === '更多操作',
    )!
    await act(async () => {
      more.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
      more.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, button: 0 }))
      more.click()
      await new Promise((r) => setTimeout(r, 0))
    })
    const fresh = [...document.querySelectorAll('[role=menuitem]')].find((b) =>
      b.textContent?.includes('新建样式'),
    ) as HTMLElement
    await act(async () => fresh.click())
    await act(async () => {})
    expect(nameInput()?.value).toBe('')
  })
})

describe('只编辑、不应用（ADR 0081：应用 = 画布绑定，只在左栏样式面板一处）', () => {
  it('没有第二套应用流程：没有范围选择、没有「应用到…」按钮；编辑与保存照旧', async () => {
    await mount()
    await act(async () => {
      useUiStore.getState().setStylesOpen(true, { presetId: 's1' })
    })
    await act(async () => {})
    const labels = [...document.body.querySelectorAll('button')].map((b) => b.textContent?.trim() ?? '')
    expect(labels).toEqual(expect.arrayContaining(['保存', '关闭']))
    expect(labels.filter((l) => l.startsWith('应用'))).toEqual([])
    expect(document.body.querySelector('[data-style-entries]')).not.toBeNull()
    expect(document.body.querySelector('[role="radiogroup"][aria-label="应用范围"]')).toBeNull()
  })
})

describe('字段按对象类别分组（审计 B25）', () => {
  it('组头按 文字 → 曲线与系列 → 坐标轴 排，条目落在各自的组里；每行的 × 说全了动作', async () => {
    await mount()
    await act(async () => {
      useUiStore.getState().setStylesOpen(true, { presetId: 's1' })
    })
    await act(async () => {})
    const entries = document.body.querySelector('[data-style-entries]')!
    const groups = [...entries.querySelectorAll('[data-style-group]')].map((g) =>
      g.getAttribute('data-style-group'),
    )
    expect(groups).toEqual(['text', 'series', 'axes', 'annotation'])
    const textGroup = entries.querySelector('[data-style-group="text"]')!
    expect(textGroup.textContent).toContain('轴标题')
    expect(textGroup.textContent).toContain('标题')
    expect(textGroup.textContent).not.toContain('曲线')
    expect(entries.querySelector('[data-style-group="axes"]')!.textContent).toContain('刻度')
    // × 的可达名点名移除的是哪一项，不是一个泛泛的「移除此项」
    const remove = [...entries.querySelectorAll('button[aria-label]')].find((b) =>
      b.getAttribute('aria-label')?.includes('从样式中移除'),
    )!
    expect(remove.getAttribute('aria-label')).toMatch(/从样式中移除「.+ · .+」/)
    // 移除后那一项没了，别的组还在
    await act(async () => (remove as HTMLButtonElement).click())
    expect(entries.querySelectorAll('[data-style-group]').length).toBeGreaterThanOrEqual(3)
  })
})

describe('压在设置之上', () => {
  it('设置被盖住但没关；关掉样式就回到设置', async () => {
    await mount(true)
    await act(async () => {
      useUiStore.getState().setSettingsOpen(true, 'style')
    })
    await act(async () => {
      useUiStore.getState().setStylesOpen(true, { presetId: 's1' })
    })
    await act(async () => {})
    const dialogs = [...document.body.querySelectorAll('[role="dialog"]')]
    expect(dialogs).toHaveLength(2)
    expect(dialogs[0].hasAttribute('data-covered'), '设置：藏起来但还在').toBe(true)
    expect(dialogs[0].classList.contains('invisible')).toBe(true)
    expect(dialogs[1].hasAttribute('data-covered')).toBe(false)
    expect(dialogs[1].classList.contains('invisible')).toBe(false)
    await act(async () => {
      useUiStore.getState().setStylesOpen(false)
    })
    await act(async () => {})
    expect(useUiStore.getState().settingsOpen).toBe(true)
    const left = [...document.body.querySelectorAll('[role="dialog"]')]
    expect(left).toHaveLength(1)
    expect(left[0].hasAttribute('data-covered')).toBe(false)
  })
})

describe('条目控件说的是真值、有本地化的可达名（2026-09-24 实测）', () => {
  it('0.75 的线宽显示 0.75（不是一位小数的 0.8），下拉的可达名是属性名的译文', async () => {
    const withFrame = envelope({
      id: 's2',
      display_name: '边框',
      data: {
        element: {
          axes: { spine_linewidth: 0.75 },
          axis_label: { fontfamily: 'Times New Roman' },
          title: { weight: 'bold' },
          ticks: { direction: 'in' },
        },
      },
    })
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ profiles: [withFrame] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    ) as typeof fetch
    await mount()
    await act(async () => {
      useUiStore.getState().setStylesOpen(true, { presetId: 's2' })
    })
    await act(async () => {})
    const frame = document.body.querySelector<HTMLInputElement>('input[aria-label="边框线宽"]')
    expect(frame?.value).toBe('0.75')
    const names = [...document.body.querySelectorAll('[data-style-entries] [role="combobox"]')].map((c) =>
      c.getAttribute('aria-label'),
    )
    expect(names).toEqual(expect.arrayContaining(['字体', '字重', '刻度朝向']))
    for (const raw of ['fontfamily', 'weight', 'direction']) expect(names).not.toContain(raw)
  })
})

describe('旧样式在样式对话框里第一次被存：升级成按页面 pt 记（与样式面板 / 设置同一条规则）', () => {
  it('存下的内容带 pt_basis:"page"，已有数字原样', async () => {
    await mount()
    await act(async () => {
      useUiStore.getState().setStylesOpen(true, { presetId: 's1' })
    })
    await act(async () => {})
    const saves: Record<string, unknown>[] = []
    useProfileStore.setState({
      save: async (_k, id, data) => {
        saves.push(data)
        return { ...(USER as never as object), id, data } as never
      },
      rename: async (_k, id) => ({ ...(USER as never as object), id }) as never,
    })
    await act(async () => {
      ;[...document.body.querySelectorAll('button')].find((b) => b.textContent?.trim() === '保存')!.click()
    })
    expect(saves[0]).toMatchObject({ pt_basis: 'page', element: (USER as unknown as { data: { element: unknown } }).data.element })
  })
})

describe('右栏的影响摘要（2026-10-07 设计审计 §10.2）', () => {
  it('xl 760；摘要卡数出这份样式管几项，以及这份文档里有几张画布跟随它（脱离的不算）', async () => {
    await mount()
    await act(async () => {
      useUiStore.getState().setStylesOpen(true, { presetId: 's1' })
    })
    await act(async () => {})
    const dialog = document.querySelector<HTMLElement>('[data-dialog="styles"]')!
    expect(dialog.style.width).toBe('760px')
    const card = () => document.querySelector('[data-style-impact]')!
    expect(card().closest('[data-card="subtle"]')).not.toBeNull()
    expect(card().querySelector('[data-style-following]')!.getAttribute('data-style-following')).toBe('0')
    await act(async () => {
      useDocumentStore.setState((s) => ({ doc: { ...s.doc, style: { id: 's1', snapshot: {} } } }))
    })
    expect(card().querySelector('[data-style-following]')!.getAttribute('data-style-following')).toBe('1')
    await act(async () => {
      useDocumentStore.setState((s) => ({ doc: { ...s.doc, style: { id: 's1', snapshot: {}, detached: true } } }))
    })
    expect(card().querySelector('[data-style-following]')!.getAttribute('data-style-following')).toBe('0')
  })
})

describe('就地「放弃修改？」开着时点了「保存」（Codex P2）', () => {
  it('问题作废：「放弃修改」不再在场，关闭直接走；重开不带着旧草稿显示未保存', async () => {
    await mount()
    await act(async () => {
      useUiStore.getState().setStylesOpen(true, { presetId: 's1' })
    })
    await act(async () => {})
    useProfileStore.setState({
      save: async (_k, id, data) => ({ ...(USER as never as object), id, data }) as never,
      rename: async (_k, id, name) => ({ ...(USER as never as object), id, display_name: name }) as never,
    })
    const button = (label: string) =>
      [...document.body.querySelectorAll('button')].find((b) => b.textContent?.trim() === label)
    // 改名 → 脏
    await act(async () => {
      const input = nameInput()!
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '投稿用 v2')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(document.querySelector('[data-style-dirty]')).not.toBeNull()
    // 关闭 → 就地问
    await act(async () => button('关闭')!.click())
    expect(document.querySelector('[data-style-discard]')).not.toBeNull()
    expect(useUiStore.getState().stylesOpen).toBe(true)
    // 不答那一问，点页脚「保存」
    await act(async () => button('保存')!.click())
    await act(async () => {})
    expect(document.querySelector('[data-style-dirty]')).toBeNull()
    expect(document.querySelector('[data-style-discard]'), '那一问作废').toBeNull()
    expect(document.querySelector('[data-style-discard-confirm]')).toBeNull()
    expect(nameInput()!.value).toBe('投稿用 v2')
    // 草稿干净：关闭直接关，不再问
    await act(async () => button('关闭')!.click())
    await act(async () => {})
    expect(useUiStore.getState().stylesOpen).toBe(false)
    // 不带预选重开：草稿仍是刚存的那份，且不显示未保存
    await act(async () => {
      useUiStore.getState().setStylesOpen(true)
    })
    await act(async () => {})
    expect(nameInput()!.value).toBe('投稿用 v2')
    expect(document.querySelector('[data-style-dirty]')).toBeNull()
  })
})
