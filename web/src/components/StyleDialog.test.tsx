/**
 * 论文样式对话框（审计 T35）。盯三件事：
 *
 * 1. 从设置带来的样式**预选**；没带而草稿为空时选第一条已存样式——「空样式」与
 *    刚才点的那条是什么关系，不该让用户猜；
 * 2. 作用范围说的是「图」，不是「面板」这种泛称；作用对象与变化数先说总账；
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
  data: { element: { line: { linewidth: 1.25 } } },
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
    const fresh = [...document.body.querySelectorAll('button')].find((b) => b.textContent?.includes('新建样式'))!
    await act(async () => {
      fresh.click()
    })
    expect(nameInput()?.value).toBe('')
  })
})

describe('作用范围与影响', () => {
  it('范围说的是「图」；主按钮写明应用到哪；先说总账', async () => {
    await mount()
    await act(async () => {
      useUiStore.getState().setStylesOpen(true)
    })
    await act(async () => {})
    const group = document.body.querySelector('[role="radiogroup"]')!
    const labels = [...group.querySelectorAll('[role="radio"]')].map((b) => b.textContent?.trim())
    expect(labels).toEqual(['当前图', '选中的图', '同一脚本的图', '整个文档'])
    for (const gone of ['面板', '选区', '全文档']) expect(labels.join(' ')).not.toContain(gone)
    expect(text()).toContain('应用到当前图')
    expect(document.body.querySelector('[data-style-affect-summary]')?.textContent).toContain('没有会被改到的图')
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
