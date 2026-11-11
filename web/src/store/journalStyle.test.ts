/**
 * 「按期刊调整」新建样式那条路：等存库期间换了画布 / 文档，不许把样式绑到后来那张画布上（Codex #1851 P1）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setLocale } from '@/i18n'
import { emptyProject } from '@/types/document'

vi.mock('@/lib/journalUi', async (orig) => ({ ...(await orig<object>()), journalCatalogUi: () => true }))
vi.mock('@/lib/journalStyle', async (orig) => ({
  ...(await orig<object>()),
  journalStyleTargets: () => ({ journal: 'Fixture', minPt: 7, maxPt: null, latin: null, minLinePt: null }),
  journalStyleEdits: () => [{ kind: 'element', role: 'ticks', prop: 'fontsize', value: 7, from: 6 }],
}))

const { adjustStyleToJournal } = await import('./journalStyle')
const { useDocumentStore } = await import('./documentStore')
const { useProfileStore } = await import('./profileStore')
const styleBinding = await import('./styleBinding')
const { useUiStore } = await import('./uiStore')

beforeEach(async () => {
  await setLocale('zh-CN')
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_journal_style')
})
afterEach(() => vi.restoreAllMocks())

describe('adjustStyleToJournal（画布没跟随样式）', () => {
  it('存库期间换了文档：不绑、说一句', async () => {
    const bind = vi.spyOn(styleBinding, 'bindCanvasStyle').mockImplementation(() => {})
    let release!: (v: unknown) => void
    vi.spyOn(useProfileStore.getState(), 'create').mockImplementation(
      () => new Promise((r) => (release = r)) as never,
    )
    const pending = adjustStyleToJournal([])
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
    release({ id: 'u_new' })
    expect(await pending).toBe(false)
    expect(bind).not.toHaveBeenCalled()
    expect(useUiStore.getState().statusTone).toBe('error')
  })

  it('没换：照常绑上新建的那份', async () => {
    const bind = vi.spyOn(styleBinding, 'bindCanvasStyle').mockImplementation(() => {})
    vi.spyOn(useProfileStore.getState(), 'create').mockResolvedValue({ id: 'u_new' } as never)
    expect(await adjustStyleToJournal([])).toBe(true)
    expect(bind).toHaveBeenCalledWith('u_new')
  })
})
