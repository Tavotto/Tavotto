/**
 * 文档冲突条的三个出口（2026-10-07 审计 P0）。
 *
 * 此前「重新加载 / 覆盖 / 另存为」三颗同形同重的 ghost 钮，「覆盖」一下就把另一个窗口存的
 * 较新版本（或外部改过的文件）换掉，和安全的「重新加载」（先留恢复副本再读盘）一样轻。钉三件事：
 *   1. 「重新加载」是实心主按钮，「另存为」是次按钮，「覆盖」不是主按钮；
 *   2. 「覆盖」先弹危险档确认框，**取消 = 磁盘那份不动**（`overwriteDisk` 没被调）；
 *   3. 确认之后才覆盖；等回答期间冲突被别处裁决了就不覆盖。
 *
 * 判据的主语：覆盖有没有发生 = `overwriteDisk` 被调了几次（它是唯一写磁盘的那条），确认框在不在 =
 * `uiStore.confirm`（`ConfirmDialog` 由它驱动）。按钮认 `data-doc-conflict-action`，不认文案 / role。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/documentStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/documentStore')>()),
  overwriteDisk: vi.fn(async () => 'saved'),
  reloadFromDisk: vi.fn(async () => undefined),
}))

import { DocumentBanner } from '@/components/DocumentBanner'
import { overwriteDisk, reloadFromDisk, useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

const action = (name: 'reload' | 'overwrite' | 'save-as') =>
  container.querySelector<HTMLButtonElement>(`[data-doc-conflict-action="${name}"]`)!
const confirm = () => useUiStore.getState().confirm
const answer = async (ok: boolean) => {
  const req = confirm()!
  await act(async () => {
    useUiStore.getState().setConfirm(null)
    req.resolve(ok)
  })
}

const conflict = (kind: 'stale' | 'external') =>
  useDocumentStore.setState({
    saveState: 'conflict',
    saveIssue: { kind, docId: useDocumentStore.getState().documentId } as never,
  })

beforeEach(() => {
  vi.mocked(overwriteDisk).mockClear()
  vi.mocked(reloadFromDisk).mockClear()
  useUiStore.getState().setConfirm(null)
  conflict('stale')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<DocumentBanner />))
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  useUiStore.getState().setConfirm(null)
  useDocumentStore.setState({ saveState: 'saved', saveIssue: null })
})

describe('文档冲突条', () => {
  it('分轻重：重新加载是主按钮，另存为是次按钮，覆盖不是主按钮；冲突条是 urgent', () => {
    expect(container.querySelector('[data-doc-banner]')!.getAttribute('data-doc-banner')).toBe('urgent')
    expect(action('reload').className, '实心黑 = 主按钮').toContain('bg-ink')
    expect(action('save-as').className).not.toContain('bg-ink')
    expect(action('overwrite').className, '覆盖不许和安全出口同重').not.toContain('bg-ink')
  })

  it('重新加载直接走，不问', () => {
    act(() => action('reload').click())
    expect(reloadFromDisk).toHaveBeenCalledTimes(1)
    expect(confirm()).toBeNull()
  })

  it('覆盖先问（危险档）；取消 = 磁盘那份不动', async () => {
    await act(async () => action('overwrite').click())
    expect(confirm(), '弹了确认框').not.toBeNull()
    expect(confirm()!.danger).toBe(true)
    expect(confirm()!.body.key).toBe('docBanner.overwriteConfirmBodyStale')
    expect(overwriteDisk, '回答之前一个字都不写').not.toHaveBeenCalled()
    await answer(false)
    expect(overwriteDisk).not.toHaveBeenCalled()
  })

  it('确认之后才覆盖', async () => {
    await act(async () => action('overwrite').click())
    await answer(true)
    expect(overwriteDisk).toHaveBeenCalledTimes(1)
  })

  it('外部改动的冲突：确认框说的是外部那份', async () => {
    act(() => conflict('external'))
    await act(async () => action('overwrite').click())
    expect(confirm()!.body.key).toBe('docBanner.overwriteConfirmBodyExternal')
    await answer(false)
  })

  it('等回答期间冲突已被别处裁决：确认了也不覆盖', async () => {
    await act(async () => action('overwrite').click())
    act(() => useDocumentStore.setState({ saveState: 'saved', saveIssue: null }))
    await answer(true)
    expect(overwriteDisk).not.toHaveBeenCalled()
  })

  it('保存失败条也是 urgent', () => {
    act(() => useDocumentStore.setState({ saveState: 'save_error', saveIssue: null }))
    expect(container.querySelector('[data-doc-banner]')!.getAttribute('data-doc-banner')).toBe('urgent')
  })
})
