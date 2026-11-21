/**
 * 旧期刊 id 按别名换绑（`resolveJournalAlias` + `rebindJournalId` + `useJournalV2Sync` 里的订阅）：
 * 唯一匹配才换；匹配不到 / 匹配到多本 / id 本身在目录里一律不动。夹具期刊，无真实数据。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import vectors from '../../../tests/golden/journal_vectors.json'
import { setLocale } from '@/i18n'
import { useJournalV2Sync } from '@/hooks/useJournalV2Sync'
import { resolveJournalAlias } from '@/lib/journalPick'
import { loadCatalog, type JournalCatalog } from '@/lib/journals'
import { selectJournal, setPageSize } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { registerGesture } from '@/store/gestureCoordinator'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { useJournalV2Store } from '@/store/journalV2Store'
import { emptyProject, type FigureDocument } from '@/types/document'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

const OLD = 'fx-beta-old-id'

/** 夹具目录 + 给指定刊加别名（直接改对象：「匹配到多本」在合法目录里不会出现，加载校验会拒，所以绕过 loadCatalog） */
function catalogWith(aliases: Record<string, string[]>): JournalCatalog {
  const base = structuredClone(loadCatalog(vectors.catalog)) as JournalCatalog
  for (const [jid, extra] of Object.entries(aliases)) {
    base.journals[jid] = { ...base.journals[jid], aliases: [...(base.journals[jid].aliases ?? []), ...extra] }
  }
  return base
}

let host: HTMLDivElement
let root: Root
const ref = () => useDocumentStore.getState().doc.profile?.journalRef
const pastCount = () => useDocumentStore.getState().past.length

function Probe() {
  useJournalV2Sync()
  return null
}

/** 打开一个绑在 `id` 上的项目：先按 fx-beta 选刊（有规则快照），再把存着的 id 改成旧 id，模拟存盘后重开 */
async function openBoundTo(id: string): Promise<{ journal: unknown; label: string }> {
  selectJournal('fx-beta')
  const saved = structuredClone(useDocumentStore.getState().doc) as FigureDocument
  saved.profile!.journalRef!.id = id
  await useDocumentStore.getState().switchDocument(saved, 'd_alias_reopen')
  return { journal: structuredClone(saved.profile!.journal), label: saved.profile!.journalRef!.label }
}

const render = async () => {
  await act(async () => root.render(<Probe />))
  await act(async () => {
    await Promise.resolve()
  })
}

beforeEach(async () => {
  await setLocale('zh-CN')
  useJournalCatalogStore.setState({ catalog: loadCatalog(vectors.catalog) })
  useJournalV2Store.setState({ byJournal: {} })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_alias')
  vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline')) // v2 取不到：只看换绑
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  useJournalCatalogStore.setState({ catalog: loadCatalog() })
  vi.restoreAllMocks()
})

describe('resolveJournalAlias', () => {
  it('唯一匹配 → 新 id', () => {
    expect(resolveJournalAlias(OLD, catalogWith({ 'fx-beta': [OLD] }))).toBe('fx-beta')
  })
  it('匹配不到 → null', () => {
    expect(resolveJournalAlias(OLD, catalogWith({}))).toBeNull()
  })
  it('匹配到多本 → null', () => {
    expect(resolveJournalAlias(OLD, catalogWith({ 'fx-beta': [OLD], 'fx-alpha': [OLD] }))).toBeNull()
  })
  it('id 本身在目录里 → 不走别名（哪怕别的刊把它记成了别名）', () => {
    expect(resolveJournalAlias('fx-alpha', catalogWith({ 'fx-beta': ['fx-alpha'] }))).toBeNull()
  })
  it('只认逐字相等：大小写 / 全角 / 子串都不算', () => {
    const c = catalogWith({ 'fx-beta': [OLD] })
    expect(resolveJournalAlias(OLD.toUpperCase(), c)).toBeNull()
    expect(resolveJournalAlias(OLD.slice(0, -1), c)).toBeNull()
    expect(resolveJournalAlias(`${OLD} `, c)).toBeNull()
  })
})

describe('useJournalV2Sync：旧 id 换绑', () => {
  it('唯一匹配：静默换到新 id，规则快照与 label 原样，不进历史，不弹胶囊', async () => {
    const orig = await openBoundTo(OLD)
    useJournalCatalogStore.setState({ catalog: catalogWith({ 'fx-beta': [OLD] }) })
    await render()
    expect(ref()?.id).toBe('fx-beta')
    expect(ref()?.label).toBe(orig.label)
    expect(useDocumentStore.getState().doc.profile?.journal).toEqual(orig.journal)
    expect(pastCount()).toBe(0)
  })
  it('用户编辑后撤销：换绑不进撤销栈，重做历史不丢', async () => {
    await openBoundTo(OLD)
    useJournalCatalogStore.setState({ catalog: catalogWith({ 'fx-beta': [OLD] }) })
    await render()
    expect(ref()?.id).toBe('fx-beta')
    act(() => setPageSize(123, 99))
    expect(pastCount()).toBe(1)
    act(() => {
      useDocumentStore.getState().undo()
    })
    expect(useDocumentStore.getState().future.length).toBe(1)
    act(() => {
      useDocumentStore.getState().undo() // 栈里已经没有换绑这一步
    })
    expect(useDocumentStore.getState().future.length).toBe(1)
    expect(ref()?.id).toBe('fx-beta')
    act(() => {
      useDocumentStore.getState().redo()
    })
    expect(useDocumentStore.getState().doc.page.w).toBe(123)
  })
  it('目录晚到（启动后远程索引才换新）：到了再换', async () => {
    await openBoundTo(OLD)
    await render()
    expect(ref()?.id).toBe(OLD)
    await act(async () => useJournalCatalogStore.setState({ catalog: catalogWith({ 'fx-beta': [OLD] }) }))
    expect(ref()?.id).toBe('fx-beta')
  })
  it('匹配不到：不动、不写历史', async () => {
    await openBoundTo(OLD)
    await render()
    expect(ref()?.id).toBe(OLD)
    expect(pastCount()).toBe(0)
  })
  it('匹配到多本：不动、不写历史', async () => {
    await openBoundTo(OLD)
    useJournalCatalogStore.setState({ catalog: catalogWith({ 'fx-beta': [OLD], 'fx-alpha': [OLD] }) })
    await render()
    expect(ref()?.id).toBe(OLD)
    expect(pastCount()).toBe(0)
  })
  it('id 本身在目录里：不走别名', async () => {
    await openBoundTo('fx-alpha')
    useJournalCatalogStore.setState({ catalog: catalogWith({ 'fx-beta': ['fx-alpha'] }) })
    await render()
    expect(ref()?.id).toBe('fx-alpha')
    expect(pastCount()).toBe(0)
  })
  it('拖动 / 连续编辑中途不写，手势收尾再换', async () => {
    await openBoundTo(OLD)
    useJournalCatalogStore.setState({ catalog: catalogWith({ 'fx-beta': [OLD] }) })
    const unregister = registerGesture(() => {})
    await render()
    expect(ref()?.id).toBe(OLD)
    await act(async () => unregister())
    expect(ref()?.id).toBe('fx-beta')
  })
})
