/**
 * 没存的样式 / 规范草稿不许一声不响地丢掉（设计审计 2026-10-07 §9.1 / §10.2，P0）。
 *
 * 会整份换掉草稿的路：设置里切库里的另一份、切分区、关设置；论文样式对话框里切样式、
 * 关对话框。每条都钉三件事：草稿脏了 → 先弹确认（`askConfirm`，danger）；
 * 「继续编辑」→ 原地不动、改动还在；「放弃修改」→ 照常往下走。草稿干净时一声不问。
 *
 * 确认框是全局的（`ConfirmDialog` 挂在 App 上），这里不渲染它，直接读 / 答
 * `uiStore.confirm`——问没问、问的是什么、答了之后发生什么，才是这里的主语。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { t } from '@/i18n'
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
Element.prototype.scrollIntoView ??= function scrollIntoView() {}

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
  data: { pt_basis: 'page', element: { line: { linewidth: 1.25 } } },
})

let container: HTMLDivElement
let root: Root

const buttons = () => [...document.body.querySelectorAll('button')]
const confirmReq = () => useUiStore.getState().confirm
/** 替全局确认框作答（与 `ConfirmDialog.answer` 同一个动作） */
async function answer(ok: boolean) {
  const req = confirmReq()!
  await act(async () => {
    useUiStore.getState().setConfirm(null)
    req.resolve(ok)
  })
  await act(async () => {})
}
function typeInto(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}
const dlg = (key: string) => t(`draftGuard.${key}`, { ns: 'dialogs' })

async function mount(node: React.ReactNode) {
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
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
  useProfileStore.setState({ styles: [], specs: [], loaded: false, error: null, conflict: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_draft_guard')
  useUiStore.setState({
    settingsOpen: false,
    settingsSection: null,
    stylesOpen: false,
    stylesPresetId: null,
    dialogStack: [],
    confirm: null,
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  useUiStore.setState({
    settingsOpen: false,
    settingsSection: null,
    stylesOpen: false,
    stylesPresetId: null,
    dialogStack: [],
    confirm: null,
  })
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

/* -------------------------------------------------------------------------- */
/*  设置 › 样式页                                                               */
/* -------------------------------------------------------------------------- */

const navItem = (id: string) =>
  document.querySelector<HTMLButtonElement>(`nav [data-section="${id}"]`)!
const currentSection = () =>
  document.querySelector('nav [aria-current="true"]')?.getAttribute('data-section')
const profileName = () => document.body.querySelector<HTMLInputElement>('#profile-name')

/** 打开设置 › 样式页，选中用户自建的那份，把名字改脏 */
async function openSettingsWithDirtyStyle() {
  useUiStore.setState({ settingsOpen: true, settingsSection: 'style' })
  await mount(<SettingsDialog />)
  await act(async () => {
    buttons().find((b) => b.getAttribute('role') === 'radio' && b.textContent?.includes('投稿用'))!.click()
  })
  await act(async () => {})
  expect(confirmReq(), '草稿干净时切换不问').toBeNull()
  await act(async () => typeInto(profileName()!, '投稿用 改'))
}

describe('设置 › 样式页：没存的草稿', () => {
  it('导航项上挂一个点；草稿干净时没有', async () => {
    useUiStore.setState({ settingsOpen: true, settingsSection: 'style' })
    await mount(<SettingsDialog />)
    expect(document.querySelector('[data-nav-dirty]')).toBeNull()
    await act(async () => {
      buttons().find((b) => b.getAttribute('role') === 'radio' && b.textContent?.includes('投稿用'))!.click()
    })
    await act(async () => typeInto(profileName()!, '投稿用 改'))
    const dots = [...document.querySelectorAll('[data-nav-dirty]')]
    expect(dots).toHaveLength(1)
    expect(dots[0].closest('[data-section]')!.getAttribute('data-section')).toBe('style')
    expect(dots[0].textContent).toBe(dlg('unsaved'))
  })

  it('切分区先问：继续编辑 → 留在原页、改动还在；放弃 → 切过去', async () => {
    await openSettingsWithDirtyStyle()
    await act(async () => navItem('spec').click())
    expect(confirmReq()).toMatchObject({ danger: true })
    expect(confirmReq()!.title).toMatchObject({ key: 'draftGuard.title' })
    await answer(false)
    expect(currentSection()).toBe('style')
    expect(profileName()!.value).toBe('投稿用 改')

    await act(async () => navItem('spec').click())
    await answer(true)
    expect(currentSection()).toBe('spec')
    expect(document.querySelector('[data-nav-dirty]'), '放弃之后点也没了').toBeNull()
  })

  it('外部请求换分区（桌面菜单「检查更新」）同样先问（Codex #821 P1）', async () => {
    await openSettingsWithDirtyStyle()
    await act(async () => useUiStore.getState().setSettingsOpen(true, 'about'))
    expect(confirmReq()).toMatchObject({ danger: true })
    await answer(false)
    expect(currentSection()).toBe('style')
    expect(profileName()!.value).toBe('投稿用 改')

    await act(async () => useUiStore.getState().setSettingsOpen(true, 'diagnostics'))
    await answer(true)
    expect(currentSection()).toBe('diagnostics')
  })

  it('外部请求被「继续编辑」取消后再发同一个分区，仍然生效（Codex #821 P2）', async () => {
    await openSettingsWithDirtyStyle()
    await act(async () => useUiStore.getState().setSettingsOpen(true, 'about'))
    await answer(false)
    expect(currentSection()).toBe('style')
    await act(async () => useUiStore.getState().setSettingsOpen(true, 'about'))
    expect(confirmReq(), '同一个请求再来一次要再问一次，而不是被吞').toMatchObject({ danger: true })
    await answer(true)
    expect(currentSection()).toBe('about')
  })

  it('方向键切分区同样先问', async () => {
    await openSettingsWithDirtyStyle()
    await act(async () => {
      navItem('style').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    })
    expect(confirmReq()).not.toBeNull()
    await answer(false)
    expect(currentSection()).toBe('style')
  })

  it('关设置先问：继续编辑 → 设置还开着；放弃 → 关掉', async () => {
    await openSettingsWithDirtyStyle()
    const close = document.querySelector<HTMLButtonElement>('[data-dialog-close]')!
    await act(async () => close.click())
    expect(confirmReq()).not.toBeNull()
    await answer(false)
    expect(useUiStore.getState().settingsOpen).toBe(true)
    expect(profileName()!.value).toBe('投稿用 改')

    await act(async () => document.querySelector<HTMLButtonElement>('[data-dialog-close]')!.click())
    await answer(true)
    expect(useUiStore.getState().settingsOpen).toBe(false)
  })

  it('选库里的另一份先问：继续编辑 → 仍是这一份；放弃 → 换过去', async () => {
    await openSettingsWithDirtyStyle()
    const builtin = () =>
      buttons().find((b) => b.getAttribute('role') === 'radio' && b.textContent?.includes('默认样式'))!
    await act(async () => builtin().click())
    expect(confirmReq()).not.toBeNull()
    await answer(false)
    expect(profileName()!.value).toBe('投稿用 改')

    await act(async () => builtin().click())
    await answer(true)
    // 内置那份只读：名字不再是输入框
    expect(profileName()).toBeNull()
    expect(builtin().getAttribute('aria-checked')).toBe('true')
  })

  it('草稿干净：切分区、关设置都不问', async () => {
    useUiStore.setState({ settingsOpen: true, settingsSection: 'style' })
    await mount(<SettingsDialog />)
    await act(async () => navItem('spec').click())
    await act(async () => {})
    expect(confirmReq()).toBeNull()
    expect(currentSection()).toBe('spec')
    await act(async () => document.querySelector<HTMLButtonElement>('[data-dialog-close]')!.click())
    await act(async () => {})
    expect(confirmReq()).toBeNull()
    expect(useUiStore.getState().settingsOpen).toBe(false)
  })
})

/* -------------------------------------------------------------------------- */
/*  论文样式对话框                                                               */
/* -------------------------------------------------------------------------- */

const styleName = () =>
  document.body.querySelector<HTMLInputElement>('input[placeholder^="样式名称"]')!
const saveButton = () => document.body.querySelector<HTMLButtonElement>('[data-style-save]')!

async function openStyleDialogDirty() {
  await mount(<StyleDialog />)
  await act(async () => {
    useUiStore.getState().setStylesOpen(true, { presetId: 's1' })
  })
  await act(async () => {})
  expect(styleName().value).toBe('投稿用')
  expect(saveButton().className, '没改时保存不是主按钮').not.toContain('bg-ink')
  await act(async () => typeInto(styleName(), '投稿用 改'))
}

describe('论文样式对话框：没存的草稿', () => {
  it('改了之后 footer 的「保存」升为主按钮', async () => {
    await openStyleDialogDirty()
    expect(saveButton().className).toContain('bg-ink')
    expect(saveButton().closest('[role="dialog"]')).not.toBeNull()
  })

  // 2026-10-07 设计审计 §10.2：对话框里不再叠一层确认框——就地问（页脚正上方的状态区里一条警示 +
  // 「继续编辑」「放弃修改」），问没问、答了之后发生什么，判据与原来那一问一致
  const discardNotice = () => document.body.querySelector('[data-style-discard]')
  async function answerInline(discard: boolean) {
    const btn = document.body.querySelector<HTMLButtonElement>(
      discard ? '[data-style-discard-confirm]' : '[data-style-discard-keep]',
    )!
    await act(async () => btn.click())
    await act(async () => {})
  }

  it('切样式先就地问：继续编辑 → 改动还在；放弃 → 换成那一份', async () => {
    await openStyleDialogDirty()
    const builtin = () =>
      buttons().find((b) => b.getAttribute('role') === 'radio' && b.textContent?.includes('默认样式'))!
    await act(async () => builtin().click())
    expect(confirmReq(), '不再叠一层确认框').toBeNull()
    expect(discardNotice()).not.toBeNull()
    expect(discardNotice()!.closest('[data-dialog-status]'), '问在页脚正上方、不随正文滚').not.toBeNull()
    expect(discardNotice()!.textContent).toContain(dlg('title'))
    await answerInline(false)
    expect(discardNotice()).toBeNull()
    expect(styleName().value).toBe('投稿用 改')

    await act(async () => builtin().click())
    await answerInline(true)
    expect(styleName().value).toBe('默认样式')
    expect(saveButton().className).not.toContain('bg-ink')
  })

  it('关对话框先就地问：继续编辑（或 Esc）→ 还开着；放弃 → 关掉，再打开不带着丢掉的改动', async () => {
    await openStyleDialogDirty()
    const close = () =>
      buttons().find((b) => b.textContent?.trim() === t('actions.close', { ns: 'common' }))!
    await act(async () => close().click())
    expect(confirmReq()).toBeNull()
    expect(discardNotice()).not.toBeNull()
    await answerInline(false)
    expect(useUiStore.getState().stylesOpen).toBe(true)
    expect(styleName().value).toBe('投稿用 改')

    // Esc 的安全答案：脏了也不直接关，先就地问；问着的时候再按 Esc = 继续编辑
    const content = document.body.querySelector<HTMLElement>('[data-dialog="styles"]')!
    const esc = () =>
      act(async () => {
        content.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
      })
    await esc()
    expect(useUiStore.getState().stylesOpen).toBe(true)
    expect(discardNotice()).not.toBeNull()
    await esc()
    expect(discardNotice()).toBeNull()
    expect(useUiStore.getState().stylesOpen).toBe(true)

    await act(async () => close().click())
    await answerInline(true)
    expect(useUiStore.getState().stylesOpen).toBe(false)

    await act(async () => useUiStore.getState().setStylesOpen(true))
    await act(async () => {})
    expect(styleName().value).toBe('投稿用')
  })

  it('草稿干净：切样式、关对话框都不问', async () => {
    await mount(<StyleDialog />)
    await act(async () => {
      useUiStore.getState().setStylesOpen(true, { presetId: 's1' })
    })
    await act(async () => {})
    await act(async () => {
      buttons().find((b) => b.getAttribute('role') === 'radio' && b.textContent?.includes('默认样式'))!.click()
    })
    await act(async () => {})
    expect(confirmReq()).toBeNull()
    expect(document.body.querySelector('[data-style-discard]')).toBeNull()
    expect(styleName().value).toBe('默认样式')
    await act(async () => {
      buttons().find((b) => b.textContent?.trim() === t('actions.close', { ns: 'common' }))!.click()
    })
    await act(async () => {})
    expect(confirmReq()).toBeNull()
    expect(useUiStore.getState().stylesOpen).toBe(false)
  })
})
