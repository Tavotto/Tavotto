/**
 * 「另存为」的外部修改检测（issue #222 §1）。
 *
 * 2026-09-06（审计 T04）起「另存为」与「打开」是同一个组件的两种形态，按
 * `uiStore.layoutIntent` 分：另存那屏只有名字和位置，打开那屏一个能写盘的
 * 控件都没有。下面每条用例都说清自己进的是哪一屏。
 *
 * 改造前这条路一个基线都不带：两个窗口对同名画布各存一次，后写的整份盖掉
 * 先写的，**而两边都收到 200**。判据在后端只有一份（`_revision_conflict`），
 * 这里守的是**前端真的把基线带过去了**，以及 409 之后的那条出口：
 *
 * 1. 本窗口没确认过这个名字 → 基线是 `absent`（不是"不带基线"）；
 * 2. 载入过 / 存成功过 → 基线是那一份的 hash，不再打扰用户；
 * 3. 409 `external_change` 不是错误，是一个岔口：显示磁盘上那份是什么 +
 *    一个「仍然覆盖」；
 * 4. 覆盖拿 **409 里回的 hash** 当基线，不是清空基线——清空等于用户按一次
 *    覆盖就把这个名字的外部修改检测永久关掉了（ADR 0024 §3c）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchLayout: vi.fn(),
  fetchLayoutNames: vi.fn(),
  saveLayout: vi.fn(),
}))

import { ApiError, REVISION_ABSENT, fetchLayout, fetchLayoutNames, saveLayout } from '@/lib/api'
import { LayoutDialog } from '@/components/LayoutDialog'
import { forgetLayoutRevisions } from '@/lib/layoutRevision'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockFetch = vi.mocked(fetchLayout)
const mockNames = vi.mocked(fetchLayoutNames)
const mockSave = vi.mocked(saveLayout)

const LAYOUT = {
  schema: 3,
  project: { id: 'p', name: 'Fig 1' },
  canvases: [
    { id: 'c1', name: 'Fig 1', page: { w: 100, h: 100 }, objects: [], guides: [] },
  ],
  activeCanvasId: 'c1',
  createdAt: 0,
  updatedAt: 1,
}

const conflictError = (revision: string) =>
  new ApiError('磁盘上的这份文档已被 Tavotto 之外的改动覆盖过', 409, {
    code: 'external_change',
    revision,
    summary: { schema: 3, canvases: 2, objects: 7, updatedAt: 5, mtime: 6, name: 'x', revision },
  })

let root: Root

async function open(names: string[] = ['Fig 1'], intent: 'save' | 'load' = 'save') {
  mockNames.mockResolvedValue(names)
  useUiStore.setState({ layoutOpen: true, layoutIntent: intent })
  const mountEl = document.createElement('div')
  document.body.appendChild(mountEl)
  root = createRoot(mountEl)
  await act(async () => {
    root.render(<LayoutDialog />)
  })
  await act(async () => {
    await Promise.resolve()
  })
}

const dialog = () => document.querySelector('[role="dialog"]')!
const buttonByText = (text: string) =>
  [...dialog().querySelectorAll('button')].find((b) => b.textContent?.includes(text))

const clickSave = async () => {
  await act(async () => {
    buttonByText('另存为')!.click()
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  forgetLayoutRevisions()
  useProjectStore.setState({ project: { open: true, id: 'p1', document_dir: '/figs/a/tavottofile' } })
  mockSave.mockResolvedValue({ ok: true, revision: 'rev-new' })
  mockFetch.mockResolvedValue({ doc: LAYOUT, revision: 'rev-disk' })
})

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  document.body.innerHTML = ''
  useUiStore.setState({ layoutOpen: false })
})

describe('另存为的基线', () => {
  it('本窗口没确认过这个名字时发 absent 哨兵，而不是不带基线', async () => {
    await open()
    await clickSave()
    expect(mockSave).toHaveBeenCalledTimes(1)
    // 第三个参数就是基线。不带基线（undefined）后端一律放行——那正是这条
    // issue 的现场：两个窗口都不带，双双 200，后写的整份盖掉先写的。
    expect(mockSave.mock.calls[0][2]).toBe(REVISION_ABSENT)
  })

  it('存成功之后再存，基线换成后端交回的那一份', async () => {
    await open()
    await clickSave()
    useUiStore.setState({ layoutOpen: true })
    await act(async () => {
      await Promise.resolve()
    })
    await clickSave()
    expect(mockSave.mock.calls[1][2]).toBe('rev-new')
  })

  it('载入过的名字用它读到的那一份当基线（不再多打扰用户一次）', async () => {
    // 「打开」和「另存为」现在是两屏（审计 T04）：先从打开那屏载入
    await open(['Fig 1'], 'load')
    await act(async () => {
      buttonByText('载入')!.click()
    })
    useUiStore.setState({ layoutOpen: true, layoutIntent: 'save' })
    await act(async () => {
      await Promise.resolve()
    })
    await clickSave()
    expect(mockSave.mock.calls[0][2]).toBe('rev-disk')
  })
})

describe('409 之后的出口', () => {
  it('冲突不显示成普通错误，而是给出磁盘上那份 + 一个「仍然覆盖」', async () => {
    mockSave.mockRejectedValueOnce(conflictError('rev-theirs'))
    await open()
    await clickSave()
    const text = dialog().textContent ?? ''
    expect(text).toContain('不是本窗口写的')
    expect(text).toContain('7') // 磁盘上那份的对象数
    expect(buttonByText('仍然覆盖')).toBeTruthy()
    expect(useUiStore.getState().layoutOpen).toBe(true) // 没关掉，用户还要裁决
  })

  it('覆盖拿 409 里回的 hash 当基线（不是清空基线）', async () => {
    mockSave.mockRejectedValueOnce(conflictError('rev-theirs'))
    await open()
    await clickSave()
    await act(async () => {
      buttonByText('仍然覆盖')!.click()
    })
    expect(mockSave).toHaveBeenCalledTimes(2)
    expect(mockSave.mock.calls[1][2]).toBe('rev-theirs')
  })

  it('别的失败仍然按普通错误显示（这条岔口只属于 external_change）', async () => {
    mockSave.mockRejectedValueOnce(new ApiError('磁盘满了', 500, { code: 'write_failed' }))
    await open()
    await clickSave()
    expect(buttonByText('仍然覆盖')).toBeUndefined()
    expect(dialog().textContent).toContain('写入磁盘失败')  // backendErrorText 按 code 翻的那一句
  })
})

/**
 * 另存与打开分成两屏（审计 T04）。
 *
 * 改造前一个弹窗上下两截：上面是保存表单、下面是可载入的文件列表，底部一颗
 * 会写盘的主按钮。从「载入」进来的用户正对着保存表单，而那颗按钮会把当前
 * 文档写到框里的名字下——两件后果相反的事共用一屏。
 */
describe('另存 / 打开是两屏', () => {
  it('另存这屏：只有名字和位置，没有可载入的列表', async () => {
    useProjectStore.setState({
      project: { open: true, id: 'p1', document_dir: '/figs/a/tavottofile' },
    })
    await open(['Fig 1', 'Fig 2'], 'save')
    const d = dialog()
    expect(d.querySelector('#layout-save-name')).not.toBeNull()
    // 位置说的是后端给的那个目录，不是界面自己拼的
    expect(d.textContent).toContain('/figs/a/tavottofile')
    expect(buttonByText('另存为')).toBeTruthy()
    // 一份都载入不了：那是另一屏的事
    expect(buttonByText('载入')).toBeUndefined()
    expect(d.textContent).not.toContain('Fig 2')
  })

  it('打开这屏：只有文档列表，一个能写盘的控件都没有', async () => {
    await open(['Fig 1', 'Fig 2'], 'load')
    const d = dialog()
    expect(buttonByText('载入')).toBeTruthy()
    expect(d.textContent).toContain('Fig 2')
    expect(buttonByText('另存为')).toBeUndefined()
    expect(d.querySelector('#layout-save-name')).toBeNull()
  })

  it('后端没给目录时不编一个出来', async () => {
    useProjectStore.setState({ project: { open: true, id: 'p1' } })
    await open(['Fig 1'], 'save')
    expect(dialog().textContent).not.toContain('保存到')
  })

  it('名字撞上已有文档时当场说出来（写盘之前）', async () => {
    await open(['Fig 1'], 'save')
    expect(dialog().textContent).toContain('已经有一份文档')
    expect(mockSave).not.toHaveBeenCalled()
  })

  it('名字没撞上就不吓唬用户', async () => {
    await open(['Something Else'], 'save')
    expect(dialog().textContent).not.toContain('已经有一份文档')
  })
})
