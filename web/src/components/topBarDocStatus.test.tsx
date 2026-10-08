/**
 * 顶栏的文档状态芯片与文档名（2026-10-07 设计审计 §10.1）。钉：
 *   1. 落定只剩一枚图标：那句话还在（读屏 / 悬停），但不占字宽（整格 sr-only）；
 *   2. 出事的那几件（未恢复的编辑 / 上次的排版没打开 / 冲突 / 保存失败）是一颗锚点色胶囊，点开是说明 + 出口，
 *      出口调的是与横幅同一批函数——「未恢复的编辑」不再是贴在顶栏上的一整句加两颗按钮；
 *   3. 文档名双击 / F2 进入改名；项目文件落后是**空心环**（实心点留给「有更新」）。
 *
 * 主语：芯片认 `data-save-state` / `data-doc-issue` / `data-doc-status-pill`，说明块认
 * `data-doc-status-popover`，改名框认 `aria-label` 之外的 `data-document-rename`——全是 data 钩子。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/documentStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/documentStore')>()),
  recoverLocalCopy: vi.fn(() => true),
}))

import { emptyProject, type TextObject } from '@/types/document'
import { recoverLocalCopy, useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { DocumentMenu, SaveStateLabel } from './TopBar'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const text: TextObject = {
  id: 't1', type: 'text', text: 'a', sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
}

let root: Root
let host: HTMLDivElement

beforeEach(async () => {
  localStorage.clear()
  globalThis.fetch = (async (_u: unknown, init?: RequestInit) =>
    init?.method === 'PUT'
      ? new Response('{"ok":true,"saved_at":1,"revision":"r0"}')
      : new Response('{}', { status: 404 })) as typeof fetch
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_chip')
  await new Promise((r) => setTimeout(r, 10))
  useDocumentStore.getState().silent((d) => {
    d.objects.push(text)
  })
  useDocumentStore.setState({ saveState: 'saved', lastPersisted: Date.now(), docNotice: null })
  useProjectStore.setState({ lastDocumentIssue: null })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () =>
    root.render(
      <>
        <DocumentMenu />
        <SaveStateLabel />
      </>,
    ),
  )
})

afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
  useDocumentStore.setState({ docNotice: null, saveState: 'saved', saveIssue: null })
  useProjectStore.setState({ lastDocumentIssue: null })
  vi.mocked(recoverLocalCopy).mockClear()
})

const chip = () => host.querySelector<HTMLElement>('[data-save-state]')!
const pill = () => host.querySelector<HTMLButtonElement>('[data-doc-status-pill]')
const openPill = async () => {
  const p = pill()!
  await act(async () => {
    p.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
    p.click()
    await new Promise((r) => setTimeout(r, 0))
  })
  return document.querySelector<HTMLElement>('[data-doc-status-popover]')
}

describe('落定：只有图标', () => {
  it('那句话进读屏、不占字宽；图标在', () => {
    expect(chip().dataset.saveShown).toBe('settled')
    const textEl = chip().querySelector('[data-save-text]')!
    expect(textEl.textContent).toBe('已存在本机')
    expect(textEl.parentElement!.className, '整格只给读屏').toContain('sr-only')
    expect(chip().querySelector('svg'), '一枚图标').not.toBeNull()
    expect(pill()).toBeNull()
  })
})

describe('出事：锚点色胶囊 + 说明块', () => {
  it('未恢复的编辑：胶囊说「有未恢复的编辑」，点开有恢复 / 保留主版本，恢复调 recoverLocalCopy', async () => {
    await act(async () =>
      useDocumentStore.setState({
        docNotice: {
          kind: 'recovery',
          docId: 'd_chip',
          summary: { savedAt: Date.now(), objects: 3, canvases: 1, name: 'Fig' },
        },
      }),
    )
    expect(chip().dataset.docIssue).toBe('recovery')
    expect(pill()!.dataset.tone).toBe('info')
    expect(pill()!.textContent).toContain('有未恢复的编辑')
    const pop = await openPill()
    expect(pop).not.toBeNull()
    const recover = pop!.querySelector<HTMLButtonElement>('[data-recovery-action="recover"]')!
    expect(recover.dataset.variant).toBe('primary')
    expect(pop!.querySelector('[data-recovery-action="keep-main"]')).not.toBeNull()
    await act(async () => recover.click())
    expect(recoverLocalCopy).toHaveBeenCalledTimes(1)
  })

  it('上次的排版没打开：不再是横幅，是芯片里的一件事', async () => {
    await act(async () => useProjectStore.setState({ lastDocumentIssue: { id: 'd_x', name: 'Fig X' } }))
    expect(chip().dataset.docIssue).toBe('last_doc')
    const pop = await openPill()
    expect(pop!.textContent).toContain('Fig X')
  })

  it('版本过新排在上次的排版没打开之前（启动时两件同时在），关掉时同一份的重试一起收掉', async () => {
    await act(async () => {
      useProjectStore.setState({ lastDocumentIssue: { id: 'd_new', name: 'Fig New' } })
      useDocumentStore.setState({ docNotice: { kind: 'schema_too_new', docId: 'd_new', schema: 99 } })
    })
    expect(chip().dataset.docIssue).toBe('too_new')
    expect(pill()!.dataset.docStatusPill).toBe('too_new')
    const pop = await openPill()
    expect(pop!.dataset.docStatusPopover).toBe('too_new')
    expect(pop!.textContent).toContain('99')
    const dismiss = pop!.querySelector<HTMLButtonElement>('button')!
    await act(async () => dismiss.click())
    expect(useDocumentStore.getState().docNotice).toBeNull()
    expect(useProjectStore.getState().lastDocumentIssue, '同一份的「打开上次文档」只会再失败').toBeNull()
    expect(pill()).toBeNull()
  })

  it('保存失败：danger 胶囊，字就是保存状态那句，播报区有字', async () => {
    await act(async () => useDocumentStore.setState({ saveState: 'save_error' }))
    expect(pill()!.dataset.tone).toBe('danger')
    expect(chip().querySelector('[data-save-text]')!.textContent).toBe('保存失败')
    expect(chip().querySelector('[data-save-live]')!.textContent).toBe('保存失败')
  })

  it('冲突排在未恢复的编辑之前（同时在时芯片只说更急的那件）', async () => {
    await act(async () =>
      useDocumentStore.setState({
        saveState: 'conflict',
        saveIssue: { kind: 'stale', docId: 'd_chip' } as never,
        docNotice: {
          kind: 'recovery',
          docId: 'd_chip',
          summary: { savedAt: Date.now(), objects: 3, canvases: 1, name: 'Fig' },
        },
      }),
    )
    expect(chip().dataset.docIssue).toBe('conflict')
    expect(pill()!.dataset.tone).toBe('warn')
  })
})

describe('文档名', () => {
  const trigger = () => host.querySelector<HTMLButtonElement>('[data-document-menu]')!
  const renameBox = () => host.querySelector('input')

  it('F2 进入改名', async () => {
    await act(async () => {
      trigger().dispatchEvent(new KeyboardEvent('keydown', { key: 'F2', bubbles: true, cancelable: true }))
    })
    expect(renameBox()).not.toBeNull()
  })

  it('双击进入改名', async () => {
    await act(async () => {
      trigger().dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    })
    expect(renameBox()).not.toBeNull()
  })

  // Codex #833：Enter / Esc 收起改名框后焦点回到文档名按钮（框卸载后焦点掉到 body，键盘用户得从头 Tab）；
  // 点别处收起的不抢焦点
  const startRename = async () => {
    trigger().focus()
    await act(async () => {
      trigger().dispatchEvent(new KeyboardEvent('keydown', { key: 'F2', bubbles: true, cancelable: true }))
    })
    expect(document.activeElement).toBe(renameBox())
  }
  const key = (k: string) =>
    act(async () => {
      renameBox()!.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }))
    })

  it.each(['Enter', 'Escape'])('%s 收起改名：焦点回到文档名按钮', async (k) => {
    await startRename()
    await key(k)
    expect(renameBox()).toBeNull()
    expect(document.activeElement).toBe(trigger())
  })

  it('点别处收起改名：不把焦点抢回文档名按钮', async () => {
    const other = document.createElement('button')
    document.body.appendChild(other)
    await startRename()
    await act(async () => other.focus())
    expect(renameBox()).toBeNull()
    expect(document.activeElement).toBe(other)
  })

  it('项目文件落后是空心环，不是实心点', async () => {
    const { setCurrentProjectId } = await import('@/lib/session')
    setCurrentProjectId('pA')
    await act(async () =>
      useDocumentStore.setState({
        projectFile: { projectId: 'pA', file: 'tavottofile/A.json', dirty: true } as never,
      }),
    )
    const dot = host.querySelector<HTMLElement>('[data-project-file-dirty]')
    expect(dot, '落后时亮').not.toBeNull()
    expect(dot!.className).toContain('ring-1')
    expect(dot!.className).not.toMatch(/\bbg-/)
    setCurrentProjectId(null)
  })
})
