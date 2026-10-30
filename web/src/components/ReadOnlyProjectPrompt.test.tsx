/**
 * 没有写入权限的项目：打开时问一次要不要另存一份（判据的主语：后端给的 `project.writable` 与
 * `settings.readonly_save_as_declined`，界面只翻译不判断）——
 * ① 只读项目弹一次；可写项目、教程项目、已记住「继续只读」的项目都不弹；
 * ② 「另存为…」选文件夹 → 后端另存 → 打开副本；取消选择不是错误、弹窗留着；失败留在弹窗里说一句；
 * ③ 「继续只读打开」按项目路径记住，之后不再弹；
 * ④ Esc 只是这次先不决定：本次会话不再弹、也不写记忆；
 * ⑤ 弹窗里只有一句话 + 两颗按钮。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  saveProjectAs: vi.fn(),
  patchProjectSettings: vi.fn(),
}))
vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  isDesktop: vi.fn(() => true),
  pickDirectory: vi.fn(),
}))

import { ApiError, backendErrorText, EngineError, engineErrorMsg, patchProjectSettings, saveProjectAs, type ProjectStatus } from '@/lib/api'
import { isDesktop, pickDirectory } from '@/lib/desktop'
import { ReadOnlyProjectPrompt } from '@/components/ReadOnlyProjectPrompt'
import { resetReadOnlyPromptMemory } from '@/lib/readOnlyPromptMemory'
import { formatMessage, t } from '@/i18n'
import { PRODUCT_NAME } from '@/lib/brand'
import { useProjectStore } from '@/store/projectStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const saveMock = vi.mocked(saveProjectAs)
const patchMock = vi.mocked(patchProjectSettings)
const pickMock = vi.mocked(pickDirectory)
const desktopMock = vi.mocked(isDesktop)
const openMock = vi.fn()

const status = (over: Partial<ProjectStatus> = {}): ProjectStatus => ({
  open: true,
  id: 'p1',
  figures_dir: '/data/ro-project',
  name: 'ro-project',
  writable: false,
  settings: {},
  ...over,
})

const q = (sel: string) => document.querySelector<HTMLElement>(sel)
const flush = async () => {
  await act(async () => {
    await Promise.resolve()
  })
}
const click = async (sel: string) => {
  await act(async () => {
    q(sel)!.click()
  })
  await flush()
}

let root: Root
let host: HTMLDivElement

const setProject = (project: ProjectStatus | null, phase: 'open' | 'none' | 'loading' = 'open') =>
  act(() => {
    useProjectStore.setState({ phase, project, open: openMock as never })
  })

beforeEach(async () => {
  resetReadOnlyPromptMemory()
  vi.clearAllMocks()
  desktopMock.mockReturnValue(true)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<ReadOnlyProjectPrompt />)
  })
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  useProjectStore.setState({ phase: 'none', project: null })
})

describe('ReadOnlyProjectPrompt', () => {
  it('shows once for a read-only project: one sentence, two buttons', async () => {
    await setProject(status())
    await flush()
    const dialog = q('[data-dialog="readonly-project"]')!
    expect(dialog).toBeTruthy()
    expect(dialog.textContent).toContain(t('readOnlyPrompt.title', { ns: 'project', product: PRODUCT_NAME }))
    // 品牌名经插值进来，没有残留的占位符
    expect(dialog.textContent).toContain(PRODUCT_NAME)
    expect(dialog.textContent).not.toContain('{{')
    expect(q('[data-readonly-project-save]')!.textContent).toBe(t('readOnlyPrompt.save', { ns: 'project' }))
    expect(q('[data-readonly-project-continue]')!.textContent).toBe(
      t('readOnlyPrompt.continue', { ns: 'project' }),
    )
    // 项目状态刷新（同一个项目换了个对象）不再弹第二次
    await setProject(status({ scripts: 3 }))
    expect(document.querySelectorAll('[data-dialog="readonly-project"]').length).toBe(1)
  })

  it.each([
    ['writable project', status({ writable: true })],
    ['tutorial project', status({ tutorial: true })],
    ['already declined', status({ settings: { readonly_save_as_declined: true } })],
  ])('does not show for a %s', async (_name, project) => {
    await setProject(project)
    await flush()
    expect(q('[data-dialog="readonly-project"]')).toBeNull()
  })

  it('save as: picks a folder, saves a copy and opens it', async () => {
    pickMock.mockResolvedValue('/home/me/mine')
    saveMock.mockResolvedValue({ path: '/home/me/mine/ro-project', files: 3, bytes: 10, skipped: 0, skipped_paths: [] })
    openMock.mockResolvedValue(status({ figures_dir: '/home/me/mine/ro-project', writable: true }))
    await setProject(status())
    await click('[data-readonly-project-save]')
    expect(saveMock).toHaveBeenCalledWith('/home/me/mine')
    expect(openMock).toHaveBeenCalledWith('/home/me/mine/ro-project')
    expect(q('[data-dialog="readonly-project"]')).toBeNull()
  })

  it('copy with unreadable files is not opened until the user acknowledges it', async () => {
    pickMock.mockResolvedValue('/home/me/mine')
    saveMock.mockResolvedValue({
      path: '/home/me/mine/ro-project',
      files: 3,
      bytes: 10,
      skipped: 2,
      skipped_paths: ['data/a.bin', 'b.bin'],
    })
    await setProject(status())
    await click('[data-readonly-project-save]')
    expect(openMock).not.toHaveBeenCalled()
    const dialog = q('[data-dialog="readonly-project"]')!
    expect(dialog.textContent).toContain(t('readOnlyPrompt.partial', { ns: 'project', count: 2 }))
    expect(dialog.textContent).not.toContain('{{')
    expect(q('[data-readonly-project-skipped]')!.textContent).toContain('data/a.bin')
    expect(q('[data-readonly-project-save]')).toBeNull() // 一句话 + 一颗按钮
    expect(q('[data-readonly-project-continue]')).toBeNull()
    await click('[data-readonly-project-open-copy]')
    expect(openMock).toHaveBeenCalledWith('/home/me/mine/ro-project')
    expect(q('[data-dialog="readonly-project"]')).toBeNull()
  })

  it('dismissing the incomplete-copy notice does not switch projects', async () => {
    pickMock.mockResolvedValue('/home/me/mine')
    saveMock.mockResolvedValue({ path: '/home/me/mine/p', files: 1, bytes: 1, skipped: 1 })
    await setProject(status())
    await click('[data-readonly-project-save]')
    await act(async () => {
      q('[data-dialog="readonly-project"]')!.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
      )
    })
    await flush()
    expect(openMock).not.toHaveBeenCalled()
    expect(q('[data-dialog="readonly-project"]')).toBeNull()
  })

  it('cancelling the folder picker is not an error and keeps the prompt', async () => {
    pickMock.mockResolvedValue(null)
    await setProject(status())
    await click('[data-readonly-project-save]')
    expect(saveMock).not.toHaveBeenCalled()
    expect(openMock).not.toHaveBeenCalled()
    expect(q('[data-dialog="readonly-project"]')).toBeTruthy()
    expect(q('[data-readonly-project-error]')).toBeNull()
  })

  it('a failed copy stays in the prompt with one sentence and does not open anything', async () => {
    pickMock.mockResolvedValue('/home/me/mine')
    saveMock.mockRejectedValue(
      new ApiError('too big', 413, { code: 'project_copy_too_large', params: { limit_mb: 2048 } }),
    )
    await setProject(status())
    await click('[data-readonly-project-save]')
    expect(openMock).not.toHaveBeenCalled()
    expect(q('[data-readonly-project-error]')!.textContent).toContain('2048')
    expect(q('[data-dialog="readonly-project"]')).toBeTruthy()
  })

  it('keep read-only remembers the choice per project and never asks again', async () => {
    patchMock.mockResolvedValue({
      settings: { readonly_save_as_declined: true },
      export_dir: '/e',
      backup_dir: '/b',
    })
    await setProject(status())
    await click('[data-readonly-project-continue]')
    expect(patchMock).toHaveBeenCalledWith({ readonly_save_as_declined: true })
    expect(q('[data-dialog="readonly-project"]')).toBeNull()
    // 后端把记忆放进了项目状态：重新打开同一个项目不再问
    expect(useProjectStore.getState().project?.settings?.readonly_save_as_declined).toBe(true)
    resetReadOnlyPromptMemory() // 模拟下一次启动：内存里的「已问过」没了，只剩持久化的记忆
    await setProject(status({ settings: { readonly_save_as_declined: true } }))
    await flush()
    expect(q('[data-dialog="readonly-project"]')).toBeNull()
  })

  it('Esc defers: no memory written, not asked again this session', async () => {
    await setProject(status())
    await act(async () => {
      q('[data-dialog="readonly-project"]')!.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
      )
    })
    await flush()
    expect(q('[data-dialog="readonly-project"]')).toBeNull()
    expect(patchMock).not.toHaveBeenCalled()
    // 切走再切回同一个项目：本次会话不再问
    await setProject(null, 'none')
    await setProject(status({ scripts: 1 }))
    await flush()
    expect(q('[data-dialog="readonly-project"]')).toBeNull()
  })

  it('switching straight from read-only A to read-only B shows the prompt for B', async () => {
    await setProject(status())
    expect(q('[data-dialog="readonly-project"]')).toBeTruthy()
    await setProject(status({ id: 'p2', figures_dir: '/data/other-ro', name: 'other-ro' }))
    await flush()
    expect(q('[data-dialog="readonly-project"]')).toBeTruthy()
    // B 的弹窗是真的：继续只读写的是 B 的设置（patch 走当前项目），不会再弹第二次
    patchMock.mockResolvedValue({ settings: { readonly_save_as_declined: true }, export_dir: '', backup_dir: '' })
    await click('[data-readonly-project-continue]')
    expect(q('[data-dialog="readonly-project"]')).toBeNull()
  })

  it('a different read-only project asks on its own', async () => {
    await setProject(status())
    await click('[data-readonly-project-continue]').catch(() => {})
    await setProject(status({ id: 'p2', figures_dir: '/data/other-ro' }))
    await flush()
    expect(q('[data-dialog="readonly-project"]')).toBeTruthy()
  })
})

describe('output_folder_read_only 的渲染路径都带品牌名', () => {
  it('backendErrorText / engineErrorMsg 渲染出产品名，没有残留 {{product}}', () => {
    const viaBackend = backendErrorText(new ApiError('x', 500, { code: 'output_folder_read_only' }))
    const viaEngine = formatMessage(engineErrorMsg(new EngineError('x', '', 'output_folder_read_only')))
    for (const text of [viaBackend, viaEngine]) {
      expect(text).toContain(PRODUCT_NAME)
      expect(text).not.toContain('{{')
    }
  })
})
