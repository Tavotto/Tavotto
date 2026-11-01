/**
 * 选期刊 = 一次提交（ADR 0155）：绑定与页宽在同一条历史里，⌘Z 一步全退回；确认走胶囊 toast。
 * 夹具期刊（`fx-*`），无真实数据。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/journal_vectors.json'
import { literal } from '@/i18n'
import { loadCatalog } from '@/lib/journals'
import { DEFAULT_PROFILE_ID } from '@/lib/profile'
import { bindingFor, builtinEntryById } from '@/lib/specBinding'
import { emptyProject } from '@/types/document'
import { clearJournal, selectJournal } from './actions'
import { startAutosave, useDocumentStore } from './documentStore'
import { useJournalCatalogStore } from './journalCatalogStore'
import { useUiStore } from './uiStore'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const s = () => useDocumentStore.getState()
let stop: (() => void) | null = null

beforeEach(async () => {
  localStorage.clear()
  useJournalCatalogStore.setState({ catalog: loadCatalog(vectors.catalog) })
  useUiStore.getState().setStatus(null)
  await s().switchDocument(emptyProject(), 'd_journal')
  s().commit(literal('页宽'), (d) => {
    d.page = { ...d.page, w: 120 }
  })
  stop = startAutosave()
  useDocumentStore.setState({ dirty: false })
})

afterEach(() => {
  stop?.()
  stop = null
  useJournalCatalogStore.setState({ catalog: loadCatalog() })
})

describe('选期刊', () => {
  it('绑定与页宽同一次提交：撤销一步，两样一起退回', () => {
    const depth = () => s().past.length
    const before = depth()
    selectJournal('fx-cols-inherit') // 栏位 57 / 121 / 184，当前 120 → 121

    expect(s().doc.profile?.id).toBe('journal-base-v1')
    expect(s().doc.profile?.journalRef?.id).toBe('fx-cols-inherit')
    expect(s().doc.page.w).toBe(121)
    expect(depth()).toBe(before + 1)
    expect(s().dirty).toBe(true)

    expect(s().undo()).not.toBeNull()
    expect(s().doc.page.w).toBe(120)
    expect(s().doc.profile).toBeUndefined()
    expect(depth()).toBe(before)
  })

  it('期刊没规定栏宽：只写绑定，页宽不动', () => {
    selectJournal('fx-delta')
    expect(s().doc.profile?.journalRef?.id).toBe('fx-delta')
    expect(s().doc.page.w).toBe(120)
  })

  it('确认是胶囊 toast（uiStore 状态），改了页宽时说出新宽度', () => {
    selectJournal('fx-cols-inherit')
    const st = useUiStore.getState()
    expect(st.status).toMatchObject({ key: 'journal.pickedWidth', ns: 'dialogs' })
    expect(st.status?.values).toMatchObject({ name: 'Fixture Columns Inherit', w: 121 })
    expect(st.statusTone).toBe('done')

    selectJournal('fx-delta')
    expect(useUiStore.getState().status).toMatchObject({ key: 'journal.picked' })
  })

  it('保存带着期刊覆盖与精简出处（不只存 id）', () => {
    selectJournal('fx-beta')
    const p = s().doc.profile!
    expect(p.journal && Object.keys(p.journal).length).toBeGreaterThan(0)
    expect(p.journalSources?.['widths_mm.double'].quote).toContain('FIXTURE')
    expect(p.snapshot).toBeTruthy()
  })
})

describe('不按期刊', () => {
  const lab = () => builtinEntryById(DEFAULT_PROFILE_ID)!

  it('退回选期刊之前的规范与页宽，一步撤销回到期刊', () => {
    s().commit(literal('绑课题组规范'), (d) => {
      d.profile = bindingFor(lab())
    })
    selectJournal('fx-cols-inherit') // 120 → 121
    expect(s().doc.profile?.journalRef?.id).toBe('fx-cols-inherit')
    const depth = s().past.length

    clearJournal()
    expect(s().doc.profile?.id).toBe(DEFAULT_PROFILE_ID)
    expect(s().doc.profile?.journalRef).toBeUndefined()
    expect(s().doc.profile?.journalSources).toBeUndefined()
    expect(s().doc.page.w).toBe(120)
    expect(s().past.length).toBe(depth + 1)
    expect(useUiStore.getState().status).toMatchObject({ key: 'journal.cleared' })

    expect(s().undo()).not.toBeNull()
    expect(s().doc.profile?.journalRef?.id).toBe('fx-cols-inherit')
    expect(s().doc.page.w).toBe(121)
  })

  it('从一本期刊换到另一本，退路仍是最初那份（不是上一本期刊）', () => {
    s().commit(literal('绑课题组规范'), (d) => {
      d.profile = bindingFor(lab())
    })
    selectJournal('fx-cols-inherit')
    selectJournal('fx-beta')
    clearJournal()
    expect(s().doc.profile?.id).toBe(DEFAULT_PROFILE_ID)
    expect(s().doc.page.w).toBe(120)
  })

  it('选期刊之前没有绑定：退回默认规范', () => {
    expect(s().doc.profile).toBeUndefined()
    selectJournal('fx-beta')
    clearJournal()
    expect(s().doc.profile?.id).toBe(DEFAULT_PROFILE_ID)
    expect(s().doc.page.w).toBe(120)
  })

  it('没有退路记录（旧数据）：退回默认规范、页宽不动', () => {
    selectJournal('fx-cols-inherit')
    s().commit(literal('抹掉记录'), (d) => {
      delete d.profile!.journalPrev
    })
    clearJournal()
    expect(s().doc.profile?.id).toBe(DEFAULT_PROFILE_ID)
    expect(s().doc.page.w).toBe(121)
  })

  it('没按期刊选过时什么都不做（不进历史）', () => {
    const depth = s().past.length
    clearJournal()
    expect(s().past.length).toBe(depth)
  })
})
