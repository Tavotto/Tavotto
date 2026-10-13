/**
 * 顶栏芯片里的「上次的排版没能恢复」（`restore` issue，CONTRACT 2.4）。钉：
 *   1. 一句话 + 一个主按钮，其余出口折在「⋯」里（用户硬性要求：小白看不懂并列按钮）；
 *   2. 说明里不出现 owners / pj / revision / epoch 这类实现名；
 *   3. 主按钮按失败原因定：暂时读不到 → 重试；找不到 / 读不出 / 版本过新 → 打开已验证的另一份（没有就继续新建）；
 *      本机找到几份 → 选一份打开；示例没打开 → 继续新建；
 *   4. `saved → clean` 清不掉它（与保存进度正交）；元数据没更新是另一颗芯片（正文已存下），主按钮「再存一次」。
 *
 * 主语：芯片认 `data-save-state` / `data-doc-issue` / `data-doc-status-pill`，说明块认 `data-doc-status-popover`，
 * 按钮认 `data-restore-action`——全是 data 钩子，不认文案。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/documentRestore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/documentRestore')>()),
  retryRestore: vi.fn(async () => ({ kind: 'superseded' })),
  continueNew: vi.fn(async () => true),
  openAlternative: vi.fn(async () => ({ kind: 'superseded' })),
}))

import { emptyProject } from '@/types/document'
import { continueNew, openAlternative, retryRestore, type RestoreIssue } from '@/store/documentRestore'
import { useDocumentStore } from '@/store/documentStore'
import { SaveStateLabel } from './TopBar'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement

const issue = (over: Partial<RestoreIssue> = {}): RestoreIssue => ({
  pj: 'p_a', gen: 1, kind: 'unavailable', ref: { id: 'd_x', name: 'Fig X' }, alternatives: [], ...over,
})

beforeEach(async () => {
  localStorage.clear()
  // 写盘成功（上一个用例留下的有内容的 d_chip 会在换文档时被冲刷；写失败会异步把保存状态推成 save_error，抢在恢复问题前面）
  globalThis.fetch = (async (_u: unknown, init?: RequestInit) =>
    init?.method === 'PUT'
      ? new Response('{"ok":true,"saved_at":1,"revision":"r0"}')
      : new Response('{}', { status: 404 })) as typeof fetch
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_chip')
  await new Promise((r) => setTimeout(r, 10))
  useDocumentStore.getState().silent((d) => {
    d.objects.push({ id: 't1', type: 'text', text: 'a', sizePt: 9, bold: false, color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8 })
  })
  useDocumentStore.setState({ saveState: 'saved', lastPersisted: Date.now(), docNotice: null, restoreIssue: null, restoreMetaIssue: null })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<SaveStateLabel />))
})

afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
  useDocumentStore.setState({ restoreIssue: null, restoreMetaIssue: null, saveState: 'saved' })
  vi.clearAllMocks()
})

const chip = () => host.querySelector<HTMLElement>('[data-save-state]')
const pill = () => host.querySelector<HTMLButtonElement>('[data-doc-status-pill]')
const popover = () => document.querySelector<HTMLElement>('[data-doc-status-popover]')
async function openPill() {
  const p = pill()!
  await act(async () => {
    p.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
    p.click()
    await new Promise((r) => setTimeout(r, 0))
  })
  return popover()
}
const setIssue = (i: RestoreIssue | null) => act(async () => useDocumentStore.setState({ restoreIssue: i }))
const actions = (pop: HTMLElement) => [...pop.querySelectorAll<HTMLElement>('[data-restore-action]')]

describe('restore 芯片', () => {
  it('暂时读不到：一句话 + 一个主按钮「重试」；「继续新建」折在 ⋯ 里', async () => {
    await setIssue(issue({ kind: 'unavailable' }))
    expect(chip()!.dataset.docIssue).toBe('restore')
    expect(pill()!.dataset.tone).toBe('warn')
    const pop = (await openPill())!
    expect(pop.dataset.docStatusPopover).toBe('restore')
    // 可见的操作只有一个主按钮 + 「⋯」
    expect(actions(pop).map((a) => a.dataset.restoreAction)).toEqual(['retry'])
    expect(actions(pop)[0].dataset.variant).toBe('primary')
    expect(pop.querySelector('[data-restore-more]')).not.toBeNull()
    await act(async () => actions(pop)[0].click())
    expect(retryRestore).toHaveBeenCalledTimes(1)
  })

  it.each(['unavailable', 'session_unavailable', 'missing', 'invalid', 'too_new', 'choose', 'prepare_failed'] as const)(
    '说明里没有实现名（%s）',
    async (kind) => {
      await setIssue(
        issue({
          kind,
          alternatives: kind === 'choose' ? [{ id: 'd_1', name: 'A', sources: ['discovered'], ownership: 'this' }] : [],
        }),
      )
      const text = `${pill()!.textContent} ${(await openPill())?.textContent ?? ''}`
      expect(text).not.toMatch(/owners?|\bpj\b|revision|epoch|docId|schema/i)
    },
  )

  it('找不到 + 有已验证的另一份：主按钮「打开“它”」；点它走 openAlternative', async () => {
    await setIssue(
      issue({
        kind: 'missing',
        alternatives: [{ id: 'd_a', name: 'Older A', sources: ['backend_prev'], ownership: 'this' }],
      }),
    )
    const pop = (await openPill())!
    const [primary] = actions(pop)
    expect(primary.dataset.restoreAction).toBe('open-alt')
    expect(primary.textContent).toContain('Older A')
    expect(pop.textContent).toContain('Fig X') // 失败原因点名失败的那份，不是悄悄换成旧稿
    await act(async () => primary.click())
    expect(openAlternative).toHaveBeenCalledWith('d_a')
  })

  it('找不到 + 没有别的候选：主按钮是「继续新建」', async () => {
    await setIssue(issue({ kind: 'missing' }))
    const pop = (await openPill())!
    expect(actions(pop).map((a) => a.dataset.restoreAction)).toEqual(['continue-new'])
    await act(async () => actions(pop)[0].click())
    expect(continueNew).toHaveBeenCalledTimes(1)
  })

  it('示例没打开（prepare_failed）：出口只有继续新建', async () => {
    await setIssue(issue({ kind: 'prepare_failed', ref: null }))
    const pop = (await openPill())!
    expect(actions(pop).map((a) => a.dataset.restoreAction)).toEqual(['continue-new'])
  })

  it('saved → clean 清不掉未决的恢复问题；解决（issue 清掉）后芯片回到落定', async () => {
    await setIssue(issue())
    await act(async () => useDocumentStore.setState({ saveState: 'clean' }))
    await act(async () => useDocumentStore.setState({ saveState: 'saved' }))
    expect(chip()!.dataset.docIssue).toBe('restore')
    await setIssue(null)
    expect(chip()!.dataset.docIssue).toBeUndefined()
  })

  it('save_error / conflict 比恢复问题更急，排在前面；元数据没更新排在最后', async () => {
    await setIssue(issue())
    await act(async () => useDocumentStore.setState({ saveState: 'save_error' }))
    expect(chip()!.dataset.docIssue).toBe('save_error')
    await act(async () => useDocumentStore.setState({ saveState: 'saved' }))
    await setIssue(null)
    await act(async () =>
      useDocumentStore.setState({ restoreMetaIssue: { docId: useDocumentStore.getState().documentId, pj: 'p_a' } }),
    )
    expect(chip()!.dataset.docIssue).toBe('restore_meta')
    const pop = (await openPill())!
    expect(actions(pop).map((a) => a.dataset.restoreAction)).toEqual(['meta-retry'])
    // 别的文档的元数据问题不挂在这份头上
    await act(async () => useDocumentStore.setState({ restoreMetaIssue: { docId: 'd_other', pj: 'p_a' } }))
    expect(chip()!.dataset.docIssue).toBeUndefined()
  })

  const menuItems = async () => {
    const more = document.querySelector<HTMLElement>('[data-restore-more]')!
    await act(async () => {
      more.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
      more.click()
      await new Promise((r) => setTimeout(r, 0))
    })
    return [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')]
  }

  it.each(['unavailable', 'session_unavailable', 'index_degraded'] as const)(
    '%s：主按钮仍是重试，备选（含「打开本机副本」）折在 ⋯ 里，点它走 openAlternative',
    async (kind) => {
      await setIssue(
        issue({
          kind,
          alternatives: [
            { id: 'd_x', name: 'Fig X', sources: ['local_cache'], ownership: 'this', localCopy: true },
            { id: 'd_y', name: 'Fig Y', sources: ['global_current'], ownership: 'this' },
          ],
        }),
      )
      const pop = (await openPill())!
      expect(actions(pop).map((a) => a.dataset.restoreAction)).toEqual(['retry'])
      const items = await menuItems()
      expect(items.map((i) => i.dataset.restoreAction)).toEqual(['open-alt', 'open-alt', 'continue-new'])
      expect(items[0].textContent).toContain('本机副本')
      expect(items[1].textContent).not.toContain('本机副本')
      await act(async () => items[0].click())
      expect(openAlternative).toHaveBeenCalledWith('d_x')
    },
  )

  it('prepare_failed 带备选时也不丢：主按钮继续新建，备选折起', async () => {
    await setIssue(
      issue({
        kind: 'prepare_failed',
        ref: null,
        alternatives: [{ id: 'd_y', name: 'Fig Y', sources: ['discovered'], ownership: 'this' }],
      }),
    )
    const pop = (await openPill())!
    expect(actions(pop).map((a) => a.dataset.restoreAction)).toEqual(['continue-new'])
    expect((await menuItems()).map((i) => i.dataset.restoreAction)).toEqual(['open-alt'])
  })
})
